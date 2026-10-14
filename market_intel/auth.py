"""Access control for internet exposure: a single shared secret, a login page and a signed session cookie.

* The secret lives in ``data_cache/market_intel/access_token`` (mode 600, created on first use; rotate with
  ``python -m market_intel token --reset``).
* Requests arriving directly from localhost (no proxy headers) are trusted; anything coming through the tunnel
  (or any proxy) must carry a valid session cookie or ``Authorization: Bearer <token>``.
* ``MARKET_INTEL_AUTH=off`` disables the check (development only).
"""
from __future__ import annotations

import hmac
import html
import os
import re
import secrets
import time
from collections import defaultdict

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from . import config

TOKEN_PATH = config.DATA_DIR / "access_token"
COOKIE = "mi_session"
SESSION_DAYS = 30
PUBLIC_PATHS = ("/login", "/healthz", "/favicon.ico", "/static/")
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "cf-ray")

#: Fixed login messages. The query string never reaches the page; only a code selects one of these, so a
#: crafted ?error=<script> cannot be reflected.
LOGIN_MESSAGES = {"invalid": "Invalid token.", "ratelimited": "Too many attempts. Try again later.",
                  "expired": "Session expired. Sign in again.", "": ""}
_SAFE_NEXT = re.compile(r"^/(?!/)[A-Za-z0-9._~\-/]*(?:\?[A-Za-z0-9._~\-/=&%+]*)?$")


def safe_next(value: str | None, default: str = "/") -> str:
    """Only same-origin absolute paths. Rejects '//evil.example' (protocol-relative), '/\\evil', schemes and CRLF.

    ``startswith("/")`` is not enough: the browser reads '//host' as a protocol-relative URL, so it is an open
    redirect, and the value was also interpolated into the page unescaped.
    """
    v = (value or "").strip()
    if not v or len(v) > 512 or "\\" in v or "\n" in v or "\r" in v or "\t" in v:
        return default
    return v if _SAFE_NEXT.match(v) else default
_attempts: dict[str, list[float]] = defaultdict(list)


def get_token(create: bool = True) -> str:
    env = os.environ.get("MARKET_INTEL_TOKEN", "").strip()
    if env:
        return env
    if TOKEN_PATH.exists():
        t = TOKEN_PATH.read_text().strip()
        if t:
            return t
    if not create:
        return ""
    t = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(t + "\n")
    TOKEN_PATH.chmod(0o600)
    return t


def reset_token() -> str:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    return get_token(True)


def auth_enabled() -> bool:
    return os.environ.get("MARKET_INTEL_AUTH", "on").lower() not in ("off", "0", "false")


def _signer() -> TimestampSigner:
    return TimestampSigner(get_token(), salt="mi-session")


def make_session() -> str:
    return _signer().sign(secrets.token_hex(8)).decode()


def session_ok(value: str | None) -> bool:
    if not value:
        return False
    try:
        _signer().unsign(value, max_age=SESSION_DAYS * 86400)
        return True
    except (BadSignature, SignatureExpired):
        return False


def is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost") and not any(h in request.headers for h in PROXY_HEADERS)


def client_ip(request: Request) -> str:
    for h in ("cf-connecting-ip", "x-real-ip"):
        if h in request.headers:
            return request.headers[h]
    if "x-forwarded-for" in request.headers:
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "?"


def too_many_attempts(ip: str, limit: int = 10, window: int = 600) -> bool:
    now = time.time()
    _attempts[ip] = [t for t in _attempts[ip] if now - t < window]
    return len(_attempts[ip]) >= limit


def record_attempt(ip: str) -> None:
    _attempts[ip].append(time.time())


def check_password(candidate: str) -> bool:
    return hmac.compare_digest((candidate or "").strip().encode(), get_token().encode())


def resolve_actor(request: Request):
    """Derive the acting identity from the request. Never reads the body - that is the whole point.

    Order: personal account token (header or cookie) -> loopback local operator -> anonymous viewer.
    """
    from . import identity
    tok = ""
    a = request.headers.get("authorization", "")
    if a.lower().startswith("bearer "):
        tok = a[7:].strip()
    tok = tok or request.headers.get("x-mi-token", "") or request.cookies.get("mi_token", "")
    if tok:
        who = identity.actor_for_token(tok)
        if who is not None:
            return who
    if is_local(request) and trust_local():
        return identity.LOCAL_ACTOR
    # a valid *shared* legacy credential proves access, not identity: it is deliberately read-only, so an
    # unattributable session can never produce a mutation the audit log cannot name
    hdr = request.headers.get("authorization", "")
    legacy = (hdr.lower().startswith("bearer ") and check_password(hdr[7:])) or session_ok(request.cookies.get(COOKIE))
    if legacy:
        return identity.LEGACY_SHARED
    return identity.ANONYMOUS


def trust_local() -> bool:
    """Should a loopback request without a token be treated as the local admin?

    Convenient for a single-user machine, wrong once the service is shared: any local process would act as
    admin, and the operator's own actions would be logged as "local" rather than as themselves. So the
    shortcut switches itself off as soon as named accounts exist. ``MARKET_INTEL_TRUST_LOCAL=on`` forces it
    back on (single-user convenience), ``=off`` disables it even with no accounts.
    """
    from . import identity
    v = os.environ.get("MARKET_INTEL_TRUST_LOCAL", "").strip().lower()
    if v in ("on", "1", "true", "yes"):
        return True
    if v in ("off", "0", "false", "no"):
        return False
    return not identity.any_accounts()


def actor_of(request: Request):
    from . import identity
    return getattr(request.state, "actor", None) or identity.ANONYMOUS


def is_authenticated(request: Request) -> bool:
    if not auth_enabled() or is_local(request):
        return True
    # a personal account token authenticates as its named actor (preferred)
    who = getattr(request.state, "actor", None)
    if who is not None and who.source == "token":
        return True
    if session_ok(request.cookies.get(COOKIE)):
        return True
    hdr = request.headers.get("authorization", "")
    if hdr.lower().startswith("bearer ") and check_password(hdr[7:]):
        return True                                   # legacy shared token, still accepted
    return False


def wants_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


async def auth_middleware(request: Request, call_next):
    request.state.actor = resolve_actor(request)
    path = request.url.path
    if any(path == p or path.startswith(p) for p in PUBLIC_PATHS) or is_authenticated(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "authentication required"}, status_code=401)
    from urllib.parse import quote
    nxt = safe_next(path + ("?" + request.url.query if request.url.query else ""))
    return RedirectResponse(f"/login?next={quote(nxt, safe='/?=&')}", status_code=302)
