"""Local API + web app (FastAPI). ``serve()`` also runs the poller in a background thread.

Pages: / (events), /portfolio, /universe, /ticker/{T}, /macro, /research. All pages share /static/base.css|js and a
locally served Plotly bundle. Access from outside localhost requires the access token (see auth.py)."""
from __future__ import annotations

import json
import hashlib
import ipaddress
import logging
import os
import re
import threading
from urllib.parse import quote, urlsplit
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from . import ask as ask_mod
from . import identity
from . import auth, config, research, tunnel
from .classify.rules import MACRO_TYPES
from .entities import EntityIndex, build_index
from .store import Store

STATIC_DIR = config.PKG_DIR / "static"
STATIC = STATIC_DIR / "dashboard.html"
PAGES = {"/portfolio": "portfolio.html", "/universe": "universe.html", "/macro": "macro.html", "/research": "research.html", "/risk": "risk.html", "/cases": "cases.html", "/bl": "bl.html", "/constructions": "constructions.html", "/platform": "platform.html", "/rebalances": "rebalances.html"}
GZIP_MIN_BYTES = 1000
CACHE_MAX_AGE = {"min.js": 86400, "default": 600}       # W9: vendored bundles a day, own assets ten minutes (ETag revalidation stays)
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~:/-]{0,127}$")


def _authority_host(value: str) -> str | None:
    """Extract one canonical hostname from a Host authority or absolute URL."""

    raw = (value or "").strip()
    if not raw or any(char.isspace() or ord(char) < 32 for char in raw):
        return None
    try:
        parsed = urlsplit(raw if "://" in raw else "//" + raw)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed.hostname.lower().rstrip(".")


def _platform_allowed_hosts(settings: config.Settings) -> set[str]:
    """Loopback plus explicitly configured bind/public hosts.

    Wildcard bind addresses are intentionally not trusted as HTTP Host values.
    Public tunnel files are written by this application or its setup script and
    therefore act as the explicit allowlist for browser access.
    """

    hosts = {"localhost", "127.0.0.1", "::1"}
    candidates = [str(getattr(settings, "host", "") or "")]
    candidates.extend(os.environ.get("MARKET_INTEL_ALLOWED_HOSTS", "").split(","))
    for path in (tunnel.URL_FILE, tunnel.LAST_URL_FILE):
        try:
            candidates.append(path.read_text().strip())
        except OSError:
            pass
    for candidate in candidates:
        host = _authority_host(candidate)
        if host and host not in {"0.0.0.0", "::", "*"}:
            hosts.add(host)
    return hosts


class CachedStaticFiles(StaticFiles):
    """StaticFiles with an explicit Cache-Control policy on top of Starlette's ETag / Last-Modified handling."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        name = str(full_path).lower()
        max_age = CACHE_MAX_AGE["min.js"] if name.endswith(".min.js") else CACHE_MAX_AGE["default"]
        resp.headers["Cache-Control"] = f"public, max-age={max_age}"
        return resp


def _age(iso: str, now: datetime) -> str:
    try:
        d = now - datetime.fromisoformat(iso)
    except Exception:
        return ""
    s = int(d.total_seconds())
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _decorate(e: dict, index: EntityIndex, now: datetime, stories: dict | None = None, impacts: dict | None = None, edges: dict | None = None) -> dict:
    e = dict(e)
    e["impacts"] = (impacts or {}).get(e["id"], [])[:20]
    e["readthrough"] = (edges or {}).get(e["id"], [])[:20]
    e["published_et"] = ask_mod._fmt_et(e["published"])
    e["age"] = _age(e["published"], now)
    e["holding_tickers"] = [t for t in (e.get("tickers") or []) if t in index.companies and index.companies[t].is_holding]
    st = (stories or {}).get(e.get("story_id"))
    e["story"] = {k: st.get(k) for k in ("n_events", "n_sources", "n_independent", "status", "first_published")} if st else None
    return e


def _fd_limit() -> int | None:
    try:
        import resource
        soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        return int(soft)
    except Exception:                                   # pragma: no cover - platform
        return None


def _open_fds_count() -> int | None:
    import os
    for d in ("/dev/fd", "/proc/self/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    return None


def _feed_names(poller=None) -> dict[str, str]:
    """feed id -> display name (feeds.yaml); Google News queries become 'Google News: <query id>'."""
    try:
        import yaml
        cfg = poller.feeds if poller is not None and getattr(poller, "feeds", None) else yaml.safe_load((config.PKG_DIR / "feeds.yaml").read_text())
    except Exception:
        return {}
    out = {f["id"]: f.get("name", f["id"]) for f in cfg.get("feeds", [])}
    for g in cfg.get("google_news", []):
        out[f"gnews_{g['id']}"] = f"Google News: {g['id'].replace('_', ' ')}"
        for i in range(6):
            out[f"gnews_{g['id']}_{i}"] = f"Google News: {g['id'].replace('_', ' ')} ({i + 1})"
    out["sec_current"] = "SEC EDGAR current filings"; out["price_monitor"] = "Price monitor"; out["yahoo_rss"] = "Yahoo Finance headlines"
    return out


def _versioned(html: str) -> str:
    """Load local UI catalogs before page scripts and invalidate changed assets."""
    catalogs = ("i18n.js", "i18n-common.js", "i18n-home.js", "i18n-portfolio.js", "i18n-platform.js")
    if '/static/i18n.js' not in html:
        scripts = "\n".join(f'<script src="/static/{name}"></script>' for name in catalogs)
        # Also covers login, whose UI intentionally has no base.js dependency.
        html = html.replace('<script src="/static/base.js">', scripts + '\n<script src="/static/base.js">') if '/static/base.js' in html else html.replace('</head>', scripts + '\n</head>')
    for f in (*catalogs, "base.js", "base.css", "platform-performance.js", "platform-performance.css", "rebalances.js", "rebalances.css", "research-readiness.js", "pipeline-status.js"):
        fp = STATIC_DIR / f
        if fp.exists():
            html = html.replace(f'"/static/{f}"', f'"/static/{f}?v={int(fp.stat().st_mtime)}"')
    return html


def _page(name: str) -> HTMLResponse:
    p = STATIC_DIR / name
    if not p.exists():
        raise HTTPException(404, f"page {name} missing")
    return HTMLResponse(_versioned(p.read_text()))


def _default_platform_service():
    """Build the production platform boundary around one verified data catalog.

    The service and Stock Select risk adapter must share the exact catalog
    instance so the adapter verifies the same immutable versions frozen during
    current-run synchronization.  Only the frozen builder can publish an
    available risk result; the legacy mutable workbench path remains unable to
    satisfy that contract.
    """

    from . import risk_workbench
    from .platform.checkpoint import checkpoint_store_from_environment
    from .platform.data import DatasetCatalog
    from .platform.risk_adapter import StockSelectRiskLoader
    from .platform.service import PlatformService

    catalog = DatasetCatalog(config.DATA_DIR / "platform" / "catalog.sqlite")
    risk_loader = StockSelectRiskLoader(
        build_frozen_construction=risk_workbench.build_frozen_construction,
        dataset_catalog=catalog,
    )
    return PlatformService(
        catalog=catalog,
        risk_loader=risk_loader,
        checkpoint_store=checkpoint_store_from_environment(),
    )


def create_app(settings: config.Settings = config.SETTINGS, store: Store | None = None, index: EntityIndex | None = None, poller=None, llm=None,
               platform_service=None, platform_sync_interval: float = 15.0) -> FastAPI:
    app = FastAPI(title="Market Intelligence AI", version="0.2", docs_url=None, redoc_url=None)
    st = {"settings": settings, "store": store or (poller.store if poller else Store()), "index": index or (poller.index if poller else build_index()),
          "poller": poller, "llm": llm or (poller.llm if poller else None), "thread": None,
          "platform_service": platform_service}
    platform_mutation_lock = threading.RLock()
    app.state.platform_sync = None
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES)      # added first = innermost: sees the real content-length, so small bodies stay uncompressed
    app.middleware("http")(auth.auth_middleware)

    @app.middleware("http")
    async def pin_research_run(request: Request, call_next):
        with config.pinned_run():
            return await call_next(request)

    @app.on_event("startup")
    def initialize_platform_service() -> None:
        """Start a producer that follows publications throughout server life."""
        from .platform.auto_sync import CurrentRunSynchronizer

        service = st["platform_service"]
        if service is None:
            service = st["platform_service"] = _default_platform_service()
        # Injected in-memory loaders remain controlled by their test/operator.
        # Production services, including the one supplied by serve(), watch
        # published archives independently of viewer requests.
        if (callable(getattr(service, "sync_current_run", None))
                and hasattr(service, "latest_dir")
                and not getattr(service, "_legacy_loaders_injected", False)):
            worker = CurrentRunSynchronizer(service, interval=platform_sync_interval)
            app.state.platform_sync = worker
            worker.start()

    @app.on_event("shutdown")
    def stop_platform_synchronizer() -> None:
        if app.state.platform_sync is not None:
            app.state.platform_sync.stop()

    @app.middleware("http")
    async def platform_host_middleware(request: Request, call_next):
        path = request.url.path
        host_protected = (
            path in {"/api/llm/usage", "/api/status"}
            or path == "/api/v2/platform"
            or path.startswith("/api/v2/platform/")
        )
        if host_protected:
            host = _authority_host(request.headers.get("host", ""))
            try:
                loopback = bool(host and ipaddress.ip_address(host).is_loopback)
            except ValueError:
                loopback = False
            if not host or (not loopback and host not in _platform_allowed_hosts(settings)):
                return JSONResponse({"error": "invalid Host header"}, status_code=400)
        return await call_next(request)

    app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")
    if research.CHARTS.exists():
        app.mount("/charts", CachedStaticFiles(directory=str(research.CHARTS)), name="charts")
    if (research.VALID / "charts").exists():
        app.mount("/validation-charts", CachedStaticFiles(directory=str(research.VALID / "charts")), name="validation-charts")

    def actor(request: Request):
        return auth.actor_of(request)

    def require(request: Request, capability: str = "write"):
        """Server-side authorisation. `capability` is write | approve | operate."""
        who = actor(request)
        ok = {"write": who.can_write, "approve": who.can_approve, "operate": who.can_operate}.get(capability, False)
        if not ok:
            raise HTTPException(403, f"{who.username} ({who.role}) is not permitted to {capability}")
        return who

    def S() -> Store:
        return st["store"]

    def IX() -> EntityIndex:
        return st["poller"].index if st["poller"] is not None else st["index"]

    def llm_usage_payload() -> dict:
        llm = st["llm"]
        fn = getattr(llm, "usage_payload", None) if llm is not None else None
        if callable(fn):
            return fn()
        from .llm_usage import unavailable_payload
        return unavailable_payload("llm_not_configured", blocked=False)

    def P():
        """Return the service initialized before request handling."""
        if st["platform_service"] is None:
            raise HTTPException(
                503,
                "platform service is not initialized; startup synchronization did not run",
            )
        return st["platform_service"]

    def platform_call(method, *args, **kwargs):
        from .platform.paper import PaperError
        from .platform.service import PlatformBlocked, PlatformInputError, PlatformNotFound
        try:
            return method(*args, **kwargs)
        except PlatformNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except PlatformInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (PlatformBlocked, PaperError) as exc:
            raise HTTPException(409, str(exc)) from exc

    def platform_origin(value: str, *, allow_path: bool) -> tuple[str, str, int] | None:
        """Normalize a browser origin, rejecting ambiguous or credential-bearing URLs."""

        try:
            parsed = urlsplit(value)
            port = parsed.port
        except (TypeError, ValueError):
            return None
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        if (
            scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (not allow_path and (parsed.path not in {"", "/"} or parsed.query))
        ):
            return None
        return scheme, host, port if port is not None else (443 if scheme == "https" else 80)

    def require_platform_same_origin(request: Request) -> None:
        """Fail closed on every browser provenance signal that is present.

        Headerless loopback/API clients remain supported. Browsers normally send
        at least one of Origin, Referer, or Sec-Fetch-Site on these POSTs; every
        signal supplied must independently identify this request's own origin.
        """

        target = platform_origin(str(request.base_url), allow_path=False)
        if target is None:
            raise HTTPException(403, "platform mutation target origin is invalid")
        origin = request.headers.get("origin")
        if "origin" in request.headers and platform_origin(origin or "", allow_path=False) != target:
            raise HTTPException(403, "cross-origin platform mutation rejected")
        referer = request.headers.get("referer")
        if "referer" in request.headers and platform_origin(referer or "", allow_path=True) != target:
            raise HTTPException(403, "cross-origin platform mutation rejected")
        fetch_site = request.headers.get("sec-fetch-site")
        if "sec-fetch-site" in request.headers and (fetch_site or "").strip().lower() != "same-origin":
            raise HTTPException(403, "cross-origin platform mutation rejected")

    async def platform_json_body(request: Request, *, max_bytes: int | None = None) -> dict:
        """Validate browser provenance and parse one JSON-object mutation body."""

        require_platform_same_origin(request)
        if max_bytes is None:
            raw = await request.body()
        else:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, "preview input is too large")
                chunks.append(chunk)
            raw = b"".join(chunks)
        if not raw:
            return {}
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise HTTPException(415, "platform mutation body must use application/json")
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, "request body must be valid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "request body must be a JSON object")
        return body

    def platform_mutation(
        request: Request,
        who,
        action: str,
        object_id: str,
        body: dict,
        method,
        *args,
    ):
        """Audit intent before a separate paper-ledger mutation, then its outcome.

        The paper JSONL and the business audit DB cannot share one transaction.
        A durable intent therefore gates the mutation.  The paper hash chain is
        authoritative for the result; failure of the secondary completion audit
        is returned as an explicit warning rather than a false rollback claim.
        Likewise, a post-commit independent-checkpoint outage is HTTP 202 with
        a dedicated audit outcome, never a rejected mutation.
        """

        idempotency_key = (request.headers.get("idempotency-key") or "").strip()
        if not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise HTTPException(
                400,
                "Idempotency-Key is required and must be 1-128 URL-safe characters",
            )
        # These fields are service-owned even if a hostile client sends names
        # that collide. They also do not affect the semantic request hash.
        public_body = {
            key: value
            for key, value in body.items()
            if key not in {"_operation_id", "_request_sha256"}
        }
        request_hash = hashlib.sha256(
            json.dumps(public_body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        actor_key = f"{who.source}:{who.id}"
        operation_scope = (actor_key, action, object_id, idempotency_key)
        operation_id = "op_" + hashlib.sha256(
            "\0".join(operation_scope).encode()
        ).hexdigest()
        service_body = {
            **public_body,
            "_operation_id": operation_id,
            "_request_sha256": request_hash,
        }
        audit_meta = {
            "operation_id": operation_id,
            "request_sha256": request_hash,
        }
        audit_object_type = "performance_artifact" if action == "platform.performance.import" else "paper_session"
        with platform_mutation_lock:
            # Do not cache completed mutation responses in process memory.
            # Every retry must re-enter the service so the durable operation
            # receipt remains the idempotency authority, the external witness
            # is checked at its current head, and a failed completion audit can
            # be repaired.  The lock is only an in-process single-flight gate;
            # ledger CAS and receipts cover other processes and restarts.
            identity.audit(
                who,
                action + ".requested",
                audit_object_type,
                object_id,
                meta=audit_meta,
            )
            try:
                result = platform_call(method, *args, service_body)
            except HTTPException as exc:
                try:
                    identity.audit(
                        who,
                        action + ".rejected",
                        audit_object_type,
                        object_id,
                        after={"http_status": exc.status_code, "detail": str(exc.detail)},
                        meta=audit_meta,
                    )
                except Exception:
                    pass
                raise
            actual_id = str((result.get("session") or {}).get("session_id") or result.get("artifact_id") or object_id)
            outcome = {
                "status": result.get("status"),
                "mutation_state": result.get("mutation_state"),
                "run_id": result.get("run_id"),
                "orders": len(result.get("orders") or []),
                "fills": len(result.get("fills") or []),
                "kill_switch_active": (result.get("session") or {}).get("kill_switch_active"),
                "live_trading_supported": False,
            }
            if audit_object_type == "performance_artifact":
                outcome["artifact_id"] = result.get("artifact_id")
            checkpoint_pending = (
                result.get("mutation_state") == "committed_checkpoint_pending"
            )
            completion_action = (
                action + ".committed_checkpoint_pending"
                if checkpoint_pending
                else action
            )
            try:
                identity.audit(
                    who,
                    completion_action,
                    audit_object_type,
                    actual_id,
                    after=outcome,
                    meta=audit_meta,
                )
                result["audit"] = {
                    "status": (
                        "COMMITTED_CHECKPOINT_PENDING"
                        if checkpoint_pending
                        else "COMPLETE"
                    ),
                    "intent_recorded": True,
                    "completion_recorded": True,
                }
            except Exception as exc:
                result["audit"] = {
                    "status": "INTENT_ONLY",
                    "intent_recorded": True,
                    "warning": f"{audit_object_type} mutation persisted; completion audit append failed",
                    "error_type": type(exc).__name__,
                }
            if checkpoint_pending:
                return JSONResponse(content=result, status_code=202)
            return result

    # ---- pages / auth ---------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def home():
        return _versioned(STATIC.read_text())

    for path, fname in PAGES.items():
        app.add_api_route(path, (lambda f: (lambda: _page(f)))(fname), methods=["GET"], response_class=HTMLResponse)

    @app.get("/ticker/{ticker}", response_class=HTMLResponse)
    def ticker_page(ticker: str):
        return _page("ticker.html")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(next: str = "/", error: str = ""):
        # neither parameter is reflected: `next` is validated to a same-origin path and escaped, `error` only
        # selects a fixed message by code
        import html as _html
        page = (STATIC_DIR / "login.html").read_text().replace("__NEXT__", _html.escape(auth.safe_next(next), quote=True))
        msg = auth.LOGIN_MESSAGES.get(error, "")
        return _versioned(page.replace("__ERROR__", f'<div class="err" role="alert">{_html.escape(msg)}</div>' if msg else ""))

    @app.post("/login")
    def login(request: Request, password: str = Form(""), next: str = Form("/")):
        ip = auth.client_ip(request)
        nxt = auth.safe_next(next)
        if auth.too_many_attempts(ip):
            return RedirectResponse("/login?error=ratelimited", status_code=302)
        if not auth.check_password(password):
            auth.record_attempt(ip)
            return RedirectResponse(f"/login?error=invalid&next={quote(nxt, safe='/?=&')}", status_code=302)
        resp = RedirectResponse(nxt, status_code=302)
        resp.set_cookie(auth.COOKIE, auth.make_session(), max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=auth.wants_https(request))
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=302)
        resp.delete_cookie(auth.COOKIE)
        return resp

    # ---- status / events / board (event monitor) -------------------------------------------------
    @app.get("/api/llm/usage")
    def llm_usage():
        """Read-only persistent usage, explicit-cost and budget state."""
        return llm_usage_payload()

    @app.get("/api/status")
    def status():
        now = datetime.now(timezone.utc)
        llm = st["llm"]
        usage = llm_usage_payload()
        feeds = {}
        names = _feed_names(st["poller"])
        for fid, fs in S().all_feed_states().items():
            feeds[fid] = {"name": names.get(fid, fid), "status": fs.get("last_status"), "items": fs.get("n_items"), "error": fs.get("error"), "age": _age(fs["last_fetch"], now) if fs.get("last_fetch") else None}
        return {"now_et": now.astimezone(config.ET).strftime("%Y-%m-%d %H:%M:%S ET"), "market_hours": config.is_market_hours(settings=settings),
                "llm": {"available": bool(llm and llm.available), "provider": getattr(llm, "provider", None), "model": getattr(llm, "model", None), "calls": getattr(llm, "calls", 0),
                        "tokens_in": getattr(llm, "tokens_in", 0), "tokens_out": getattr(llm, "tokens_out", 0),
                        "cache_tokens": getattr(llm, "cache_tokens", 0), "disabled_reason": getattr(llm, "disabled_reason", None), "usage": usage},
                "last_cycle": S().kv_get("last_cycle", {}), "polling": bool(st["poller"] and st["poller"].running), "holdings": IX().holdings,
                "process": {"db_connections": S().n_connections, "open_fds": _open_fds_count(), "fd_limit": _fd_limit()},
                "db": S().stats(targets=IX().holdings), "feeds": feeds, "price_snapshot_at": S().kv_get("price_snapshot_at"), "brief_date": S().kv_get("brief_date"),
                "research_asof": research.summary().get("asof"), "run": config.run_info(), "public_url": tunnel.public_url(), "auth": auth.auth_enabled(),
                "settings": {"poll_interval_market": settings.poll_interval_market, "poll_interval_off": settings.poll_interval_off, "model": settings.model,
                             "notify_min_level": settings.notify_min_level, "half_life_hours": settings.half_life_hours}}

    @app.get("/api/events")
    def events(filter: str = "all", ticker: str | None = None, q: str | None = None, hours: int = 48, limit: int = 150, all_items: bool = False, collapse: bool = False,
               offset: int = 0, since: str | None = None):
        """since: ISO timestamp for incremental refresh (only events published after it); offset: pagination."""
        now = datetime.now(timezone.utc)
        ix = IX()
        if q:
            rows = S().search(q, limit=limit)
        else:
            start = now - timedelta(hours=hours)
            if since:
                try:
                    start = max(start, datetime.fromisoformat(since.replace("Z", "+00:00")))
                except ValueError:
                    pass
            kw: dict = {"since": start, "limit": limit, "offset": max(0, int(offset)), "relevant_only": not all_items, "collapse": collapse}
            if ticker:
                kw["ticker"] = ticker.upper()
            if filter == "holdings":
                kw["holdings"] = ix.holdings
            elif filter == "alerts":
                kw["levels"] = ("warning", "critical")
            elif filter == "sec":
                kw["source_types"] = ("sec",)
            elif filter == "macro":
                kw["types"] = tuple(MACRO_TYPES)
            elif filter == "price":
                kw["source_types"] = ("price",)
            elif filter == "press":
                kw["source_types"] = ("press",)
            elif filter == "material":
                kw["min_materiality"] = 0.5
                kw["order"] = "materiality"
            rows = S().events(**kw)
        stories = S().stories_for(e.get("story_id") for e in rows)
        ids = [e["id"] for e in rows]
        imps, edges = S().impacts_for(ids), S().readthrough_for(ids)
        return {"count": len(rows), "collapsed": collapse, "offset": int(offset), "limit": int(limit), "as_of": now.isoformat(timespec="seconds"),
                "events": [_decorate(e, ix, now, stories, imps, edges) for e in rows]}

    # ---- P1 W1/W2: action feed, bulk read, acknowledgement, review cases ---------------------------------
    @app.get("/api/actions")
    def actions_feed(hours: int = 48, since: str | None = None):
        from . import actions as actions_mod
        p = st["poller"]
        return actions_mod.build(S(), IX(), settings, hours=hours, since=since, llm=st["llm"], polling=bool(p and p.running) if p is not None else None)

    @app.post("/api/events/read")
    async def mark_read_bulk(request: Request):
        require(request, "write")
        body = await request.json()
        ids = body.get("ids") if isinstance(body, dict) else body
        if not isinstance(ids, list) or not ids:
            raise HTTPException(400, "ids required")
        n = S().mark_read_many([str(i) for i in ids][:500], 1 if (body.get("read", True) if isinstance(body, dict) else True) else 0)
        return {"ok": True, "n": n}

    @app.get("/api/events/{event_id}")
    def event(event_id: str):
        e = S().get_event(event_id)
        if not e:
            raise HTTPException(404, "unknown event")
        return _decorate(e, IX(), datetime.now(timezone.utc), S().stories_for([e.get("story_id")]), S().impacts_for([event_id]), S().readthrough_for([event_id]))

    @app.post("/api/events/{event_id}/read")
    def mark_read(event_id: str, request: Request):
        require(request, "write")
        S().mark_read(event_id)
        return {"ok": True}

    @app.post("/api/events/{event_id}/ack")
    async def ack_event(event_id: str, request: Request):
        """Mark read and acknowledge the review case for the story x target-list name (created if the event warrants one)."""
        from . import cases as cases_mod
        e = S().get_event(event_id)
        if not e:
            raise HTTPException(404, "unknown event")
        who = require(request, "write")
        body = await request.json() if int(request.headers.get("content-length") or 0) > 0 else {}
        by = who.username                          # server-derived; a client-supplied "by" is ignored
        S().mark_read(event_id)
        ix = IX()
        targets = set(ix.holdings)
        tickers = [str(body["ticker"]).upper()] if body.get("ticker") else [t for t in (e.get("tickers") or []) if t in targets]
        out = []
        if e.get("alert_level") in ("warning", "critical") or body.get("ticker"):
            for t in tickers:
                c = cases_mod.ensure_case(S(), e, t, e.get("alert_level") or "warning", settings)
                out.append(cases_mod.acknowledge(S(), c["id"], by=by))
        return {"ok": True, "read": True, "cases": research.clean(out), "case": research.clean(out[0]) if out else None}

    @app.get("/api/cases")
    def cases_list(status: str | None = None, ticker: str | None = None, limit: int = 100):
        from . import cases as cases_mod
        try:
            rows = cases_mod.list_cases(S(), status=status, ticker=ticker, limit=max(1, min(int(limit), 500)))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return research.clean({"count": len(rows), "cases": rows, "summary": cases_mod.summary(S()), "statuses": list(cases_mod.STATUSES), "run": config.run_info()})

    @app.post("/api/cases")
    async def cases_create(request: Request):
        from . import cases as cases_mod
        who = require(request, "write")
        body = await request.json()
        ticker = str(body.get("ticker") or "").upper().strip()
        title = str(body.get("title") or "").strip()
        if not ticker or not title:
            raise HTTPException(400, "ticker and title required")
        level = body.get("level") if body.get("level") in cases_mod.LEVELS else "warning"
        now = datetime.now(timezone.utc)
        hours = float(body.get("sla_hours") or cases_mod.sla_hours(level, settings))
        row = {"story_id": body.get("story_id") or f"manual_{now:%Y%m%dT%H%M%S}_{ticker}", "event_id": body.get("event_id"), "ticker": ticker, "title": title[:200], "level": level,
               "owner": who.username, "due": body.get("due") or (now + timedelta(hours=hours)).isoformat(timespec="seconds"),
               "sla_hours": hours, "status": "open", "evidence": [str(x) for x in (body.get("evidence") or []) if x] + ([str(body["event_id"])] if body.get("event_id") else []),
               "created_by": who.username,
               "notes": [{"at": now.isoformat(timespec="seconds"), "by": who.username, "text": str(body["note"])[:2000]}] if body.get("note") else []}
        with S().transaction() as _cx:
            c = S().insert_case(row)
            identity.audit(who, "case.create", "case", str(c.get("id")),
                           after={k: row.get(k) for k in ("ticker", "title", "level", "owner", "status", "created_by")}, conn=_cx)
        return research.clean({"ok": True, "case": cases_mod.decorate(c), "actor": who.to_dict()})

    @app.get("/api/cases/{case_id}")
    def cases_get(case_id: int):
        from . import cases as cases_mod
        c = cases_mod.get_case(S(), case_id)
        if not c:
            raise HTTPException(404, "unknown case")
        evs = S().events(ids=c.get("evidence") or [], limit=100) if c.get("evidence") else []
        return research.clean({"case": c, "evidence": [{k: e.get(k) for k in ("id", "title", "one_liner", "published", "source_name", "event_type", "alert_level", "url", "read")} for e in evs]})

    @app.post("/api/cases/{case_id}")
    async def cases_update(case_id: int, request: Request):
        from . import cases as cases_mod
        who = require(request, "write")
        body = await request.json()
        # Identity is never taken from the body: `by`, `signoff_by` and `ack_by` are ignored if sent.
        # `owner` is different - it is a work ASSIGNMENT, not a claim about who is acting - so it stays
        # settable by an authorised caller and the change is audited.
        ignored = [k for k in ("by", "signoff_by", "ack_by") if k in body]
        ignored += [k for k in ("created_by",) if k in body]
        allowed = {k: body[k] for k in ("owner", "due", "status", "decision", "no_trade_reason", "execution_ref", "note", "level", "title", "evidence") if k in body}
        wants_signoff = bool(body.get("signoff_by")) or str(body.get("status", "")).lower() in ("decided", "closed")
        before = cases_mod.get_case(S(), case_id)
        if before is None:
            raise HTTPException(404, "unknown case")
        locked = str(before.get("status", "")).lower() in ("decided", "closed")
        if locked and not who.can_approve:
            raise HTTPException(403, f"case {case_id} is {before.get('status')}; an approver must reopen it before it can be changed")
        if wants_signoff:
            maker = before.get("created_by")            # immutable author, not the mutable owner field
            try:
                identity.check_maker_checker(who, maker)
            except identity.ApprovalError as e:
                raise HTTPException(403, str(e))
            allowed["signoff_by"] = who.username
        if not allowed:
            raise HTTPException(400, "nothing to update (owner, due, status, decision, no_trade_reason, execution_ref, note, level, title, evidence)")
        try:
            c = cases_mod.update_case(S(), case_id, by=who.username, **allowed)
        except KeyError:
            raise HTTPException(404, "unknown case")
        except ValueError as e:
            raise HTTPException(400, str(e))
        with S().transaction() as _cx:
            identity.audit(who, "case.update", "case", str(case_id),
                           before={k: before.get(k) for k in ("status", "owner", "decision", "signoff_by", "level", "created_by")},
                           after={k: c.get(k) for k in ("status", "owner", "decision", "signoff_by", "level", "created_by")},
                           meta={"ignored_client_identity": ignored, "locked_before": locked} if (ignored or locked) else None, conn=_cx)
        return research.clean({"ok": True, "case": c, "actor": who.to_dict(), "ignored_fields": ignored})

    @app.post("/api/cases/{case_id}/ack")
    async def cases_ack(case_id: int, request: Request):
        from . import cases as cases_mod
        who = require(request, "write")
        try:
            c = cases_mod.acknowledge(S(), case_id, by=who.username)
        except KeyError:
            raise HTTPException(404, "unknown case")
        identity.audit(who, "case.ack", "case", str(case_id), after={"status": c.get("status")})
        return research.clean({"ok": True, "case": c, "actor": who.to_dict()})

    @app.post("/api/events/{event_id}/override")
    async def override(event_id: str, request: Request):
        _ov_actor = require(request, "write")
        """Analyst override of a classification (recorded as lineage: classifier='human', human_override JSON)."""
        from .classify import rules as rules_mod
        from . import scoring as scoring_mod
        e = S().get_event(event_id)
        if not e:
            raise HTTPException(404, "unknown event")
        body = await request.json()
        fields = {}
        if "direction" in body:
            fields["direction"] = int(max(-2, min(2, int(body["direction"]))))
        if "materiality" in body:
            fields["materiality"] = float(max(0.0, min(1.0, float(body["materiality"]))))
        if "relevant" in body:
            fields["relevant"] = int(bool(body["relevant"]))
        if body.get("event_type") in rules_mod.EVENT_TYPES:
            fields["event_type"] = body["event_type"]
        if body.get("action") in rules_mod.ACTIONS:
            fields["action"] = body["action"]
        if not fields:
            raise HTTPException(400, "nothing to override (direction, materiality, relevant, event_type, action)")
        override_rec = {**fields, "note": str(body.get("note") or "")[:400], "by": _ov_actor.username,
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "previous": {k: e.get(k) for k in fields}}
        merged = {**e, **fields}
        cls = {"direction": merged["direction"], "materiality": merged["materiality"], "confidence": merged.get("confidence", 0.5),
               "tickers": {t: 0.9 for t in (merged.get("tickers") or [])}, "tickers_rt": {t: (0.5, 0) for t in (merged.get("tickers_rt") or [])}}
        imp = rules_mod.impacts(cls, IX()) if merged.get("relevant") else []
        level = scoring_mod.alert_level(merged, imp, IX(), settings) if merged.get("relevant") else None
        S().update_event(event_id, **fields, alert_level=level, classifier="human", human_override=override_rec, needs_review=0)
        S().set_impacts(event_id, imp)
        return {"ok": True, "override": override_rec, "alert_level": level, "impacts": imp}

    @app.get("/api/board")
    def board():
        rows = S().scores()
        snap = S().kv_get("price_snapshot", {}) or {}
        for r in rows:
            px = snap.get(r["ticker"]) or {}
            r["px_pct"], r["px_idio"] = px.get("pct"), px.get("idio")
        rows.sort(key=lambda r: (not r["is_holding"], -abs(r["event_score_7d"]), r["ticker"]))
        return {"asof": rows[0]["asof"] if rows else None, "rows": rows}

    @app.get("/api/calendar")
    def calendar(days: int = 7):
        today = config.now_et().date()
        rows = S().calendar((today - timedelta(days=1)).isoformat(), (today + timedelta(days=days)).isoformat())
        return {"today": today.isoformat(), "rows": rows}

    @app.get("/api/alerts")
    def alerts(hours: int = 24, limit: int = 100):
        rows = S().alerts(since=datetime.now(timezone.utc) - timedelta(hours=hours), limit=limit)
        for r in rows:
            r["age"] = _age(r["created"], datetime.now(timezone.utc))
        return {"alerts": rows}

    @app.get("/api/brief")
    def brief():
        return S().latest_brief() or {"text": "", "date": None}

    @app.post("/api/brief")
    def brief_build(request: Request):
        _who = require(request, "write")
        identity.audit(_who, "brief.rebuild", "brief")
        from . import brief as brief_mod
        return {"text": brief_mod.build(S(), IX(), settings, st["llm"], notify=False)}

    @app.post("/api/ask")
    async def ask(request: Request):
        require(request, "write")
        body = await request.json()
        q = (body.get("question") or "").strip()
        if not q:
            raise HTTPException(400, "question required")
        ix = IX()
        evs = ask_mod.retrieve(S(), ix, q)
        header = json.dumps({"evidence": [{k: e.get(k) for k in ("id", "title", "url", "source_name", "published", "tickers", "event_type", "one_liner")} for e in evs]}, default=str)
        gen = ask_mod.answer(q, S(), ix, st["llm"], stream=True)

        def stream():
            yield header + "\n---\n"
            for chunk in gen:
                yield chunk
        return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")

    @app.post("/api/poll")
    def poll(request: Request):
        _who = require(request, "operate")
        identity.audit(_who, "poller.run", "poller")
        p = st["poller"]
        if p is None:
            raise HTTPException(400, "poller not attached")
        if p.running or (st["thread"] and st["thread"].is_alive()):
            return {"started": False, "reason": "already running"}
        t = threading.Thread(target=p.cycle, daemon=True, name="mi-poll-once")
        t.start()
        st["thread"] = t
        return {"started": True}

    @app.get("/api/search")
    def search(q: str, limit: int = 50):
        now = datetime.now(timezone.utc)
        return {"events": [_decorate(e, IX(), now) for e in S().search(q, limit=limit)]}

    # ---- research: stock selection, portfolio, universe, macro, validation ---------------------------
    @app.get("/api/research/summary")
    def research_summary():
        s = research.summary()
        return research.clean({"available": research.available(), "asof": s.get("asof"), "generated_at": s.get("generated_at"), "risk_free": s.get("risk_free"), "run": config.run_info(),
                               "portfolio": s.get("portfolio", {}), "recommended_cash": s.get("recommended_cash"), "funnel": s.get("funnel", []), "universe_size": s.get("universe_size"),
                               "changes": s.get("changes", {}), "alerts": s.get("alerts", []), "data_sources": s.get("data_sources", []), "config": s.get("config", {}), "files": research.files_payload()})

    @app.get("/api/research/portfolio")
    def research_portfolio():
        return research.portfolio_payload()

    @app.post("/api/research/optimize")
    async def research_optimize(request: Request):
        _who = require(request, "write")
        identity.audit(_who, "optimizer.run", "optimizer")
        body = await request.json()
        out = research.optimize(body or {})
        if "error" in out:
            return JSONResponse(out, status_code=400)
        return out

    @app.get("/api/research/universe")
    def research_universe():
        return research.universe_payload()

    @app.get("/api/research/ticker/{ticker}")
    def research_ticker(ticker: str):
        return research.ticker_payload(ticker, S())

    @app.get("/api/research/macro")
    def research_macro():
        return research.macro_payload()

    @app.get("/api/research/history")
    def research_history():
        return research.history_payload()

    @app.get("/api/research/rebalances")
    def research_rebalances(construction: str = "active", basis: str = "target"):
        from .rebalance_history import payload
        try:
            paper = P().paper()
        except Exception:
            paper = None
        try:
            result = payload(research.HISTORY, paper, construction=construction, basis=basis)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/research/readiness")
    def research_readiness():
        from .readiness import payload
        return JSONResponse(research.clean(payload(config.OUTPUTS)), headers={"Cache-Control": "no-store"})

    @app.get("/api/research/validation")
    def research_validation():
        return research.validation_payload()

    @app.get("/api/research/strategic")
    def research_strategic():
        return research.strategic_payload()

    @app.get("/api/research/bl")
    def research_bl():
        return research.bl_payload()

    @app.post("/api/research/bl")
    async def research_bl_run(request: Request):
        _who = require(request, "write")
        identity.audit(_who, "bl.adhoc", "bl")
        body = await request.json() if int(request.headers.get("content-length") or 0) > 0 else {}
        out = research.bl_run(body or {})
        if "error" in out:
            return JSONResponse(out, status_code=400)
        return out

    @app.post("/api/research/bl/run")
    async def research_bl_refresh(request: Request):
        _who = require(request, "operate")
        identity.audit(_who, "bl.run", "bl")
        body = await request.json() if int(request.headers.get("content-length") or 0) > 0 else {}
        args = ["--no-walk-forward"] if body.get("skip_walk_forward") else []
        return research.BL_RUNNER.start(args)

    @app.get("/api/research/bl/status")
    def research_bl_status():
        return research.BL_RUNNER.status()

    @app.get("/api/research/constructions")
    def research_constructions():
        return research.constructions_payload()

    @app.post("/api/research/constructions/run")
    async def research_constructions_run(request: Request):
        _who = require(request, "operate")
        identity.audit(_who, "constructions.run", "constructions")
        body = await request.json() if int(request.headers.get("content-length") or 0) > 0 else {}
        args = []
        if body.get("no_bl_views"):
            args.append("--no-bl-views")
        if body.get("methods"):
            args += ["--methods", *[str(m) for m in body["methods"]]]
        return research.CONSTRUCTION_RUNNER.start(args)

    @app.get("/api/research/constructions/status")
    def research_constructions_status():
        return research.CONSTRUCTION_RUNNER.status()

    @app.get("/api/whoami")
    def whoami(request: Request):
        who = actor(request)
        return {"actor": who.to_dict(), "accounts_configured": identity.any_accounts(),
                "note": "the actor is derived from the session; client-supplied identity fields are ignored"}

    @app.get("/api/audit")
    def audit_log(object_type: str | None = None, object_id: str | None = None, actor_name: str | None = None, limit: int = 100):
        return {"events": identity.audit_log(object_type, object_id, actor_name, max(1, min(int(limit), 500))),
                "chain": identity.verify_chain()}

    @app.get("/api/research/files")
    def research_files():
        return research.files_payload()

    @app.get("/api/research/risk")
    def research_risk():
        from . import risk_workbench
        return risk_workbench.build()

    @app.get("/api/research/risk/constructions")
    def research_risk_constructions(reps: int | None = None, force: bool = False):
        from . import risk_workbench
        # reps=0 is the deterministic tier: exact point estimates, no confidence intervals. It exists so the
        # matrix can render immediately instead of blocking a page for a minute on resampling.
        n = (risk_workbench.OFFICIAL_BOOTSTRAP_REPS if reps is None
             else (0 if int(reps) <= 0 else max(50, min(int(reps), 5000))))
        return research.clean(risk_workbench.build_constructions(bootstrap_reps=n, force=bool(force)))

    @app.get("/api/research/risk/constructions/{name}")
    def research_risk_construction(name: str, reps: int | None = None, layer: str = "model", window: str = "1y",
                                   force: bool = False):
        from . import risk_workbench
        if layer not in risk_workbench.LAYERS:
            raise HTTPException(422, f"unknown layer {layer!r}; known: {', '.join(risk_workbench.LAYERS)}")
        if window not in risk_workbench.VAR_WINDOWS:
            raise HTTPException(422, f"unknown window {window!r}; known: {', '.join(risk_workbench.VAR_WINDOWS)}")
        n = (risk_workbench.OFFICIAL_BOOTSTRAP_REPS if reps is None
             else (0 if int(reps) <= 0 else max(50, min(int(reps), 5000))))
        out = risk_workbench.build_construction(name, layer=layer, window=window,
                                                bootstrap_reps=n, force=bool(force))
        if out is None:
            known = risk_workbench.enumerate_constructions()
            raise HTTPException(404, f"unknown construction {name!r}; known: {', '.join(known)}")
        return research.clean(out)

    # ---- extensible strategy platform / virtual paper ledger ----------------------------------
    @app.post("/api/v2/platform/rebalance/preview")
    async def platform_rebalance_preview(request: Request):
        from .platform.rebalance_preview import build_preview
        body = await platform_json_body(request, max_bytes=262144)
        if set(body) - {"target", "account", "quotes", "policy"}:
            raise HTTPException(422, "preview accepts target, account, quotes and policy only")
        result = build_preview(**{key: body.get(key) for key in ("target", "account", "quotes", "policy")},
                               now=datetime.now(timezone.utc))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/v2/platform/performance")
    def platform_performance(request: Request):
        result = platform_call(P().performance.catalog)
        who = actor(request)
        result["access"] = {"can_import": who.can_operate, "role": who.role}
        return result

    @app.get("/api/v2/platform/performance/artifacts/{artifact_id}")
    def platform_performance_artifact(artifact_id: str):
        return platform_call(P().performance.get, artifact_id)

    @app.get("/api/v2/platform/performance/export")
    def platform_performance_export(payload: str, analysis_id: str):
        if len(payload) > 20_000:
            raise HTTPException(413, "export configuration is too large")
        try:
            body = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "invalid export configuration") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "export configuration must be an object")
        result = platform_call(P().performance.analyze, body)
        if result["analysis_id"] != analysis_id:
            raise HTTPException(409, "analysis changed; recalculate before exporting")
        filename = f"joint-performance-{result['start']}-{result['end']}.json"
        return JSONResponse(result, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})

    @app.post("/api/v2/platform/performance/analyze")
    async def platform_performance_analyze(request: Request):
        # Calculation only: this never persists a run or advances a paper book.
        body = await platform_json_body(request)
        return platform_call(P().performance.analyze, body)

    @app.post("/api/v2/platform/performance/activity")
    async def platform_performance_activity(request: Request):
        body = await platform_json_body(request)
        return platform_call(P().performance.activity_report, body)

    @app.post("/api/v2/platform/performance/exposure")
    async def platform_performance_exposure(request: Request):
        body = await platform_json_body(request)
        return platform_call(P().performance.exposure_report, body)

    @app.get("/api/v2/platform/performance/exposure/export")
    def platform_exposure_export(payload: str, report_id: str):
        if len(payload) > 20_000:
            raise HTTPException(413, "export configuration is too large")
        try:
            body = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "invalid export configuration") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "export configuration must be an object")
        result = platform_call(P().performance.exposure_report, body)
        if result["report_id"] != report_id:
            raise HTTPException(409, "exposure comparison changed; recalculate before exporting")
        filename = f"exposure-comparison-{result['start']}-{result['end']}.json"
        return JSONResponse(result, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})

    @app.post("/api/v2/platform/performance/import")
    async def platform_performance_import(request: Request):
        who = require(request, "operate")
        body = await platform_json_body(request)
        if len(json.dumps(body)) > 2_000_000:
            raise HTTPException(413, "performance import exceeds 2 MB")
        return platform_mutation(request, who, "platform.performance.import", "performance-artifact", body, P().performance.import_artifact)

    @app.get("/api/v2/platform/overview")
    def platform_overview():
        return platform_call(P().overview)

    @app.get("/api/v2/platform/strategies")
    def platform_strategies():
        return platform_call(P().strategies)

    @app.get("/api/v2/platform/runs")
    def platform_runs(strategy_id: str | None = None, limit: int = 100):
        return platform_call(P().runs, strategy_id=strategy_id, limit=limit)

    @app.get("/api/v2/platform/sync-status")
    def platform_sync_status():
        worker = getattr(app.state, "platform_sync", None)
        result = worker.status() if worker is not None else {"status": "NOT_STARTED"}
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/v2/platform/paper")
    def platform_paper(session_id: str | None = None):
        return platform_call(P().paper, session_id=session_id)

    @app.get("/api/v2/platform/risk")
    def platform_risk():
        return platform_call(P().risk)

    @app.post("/api/v2/platform/paper/sessions")
    async def platform_paper_session(request: Request):
        who = require(request, "operate")
        body = await platform_json_body(request)
        return platform_mutation(
            request,
            who,
            "platform.paper.session.create",
            "new-paper-session",
            body,
            P().create_paper_session,
        )

    @app.post("/api/v2/platform/paper/{session_id}/step")
    async def platform_paper_step(session_id: str, request: Request):
        who = require(request, "operate")
        body = await platform_json_body(request)
        return platform_mutation(
            request,
            who,
            "platform.paper.step",
            session_id,
            body,
            P().step_paper,
            session_id,
        )

    @app.post("/api/v2/platform/paper/{session_id}/kill-switch")
    async def platform_paper_kill_switch(session_id: str, request: Request):
        who = require(request, "operate")
        body = await platform_json_body(request)
        return platform_mutation(
            request,
            who,
            "platform.paper.kill_switch",
            session_id,
            body,
            P().kill_switch,
            session_id,
        )

    @app.get("/api/research/dossier/{ticker}")
    def research_dossier(ticker: str):
        from . import dossiers
        return dossiers.build(ticker, S())

    @app.get("/files/deck")
    def deck():
        f = research.files_payload().get("deck")
        if not f:
            raise HTTPException(404, "no deck")
        return FileResponse(str(research.DECK / f), filename=f, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

    @app.get("/files/static-dashboard", response_class=HTMLResponse)
    def static_dashboard():
        p = config.OUTPUTS / "dashboard" / "index.html"
        if not p.exists():
            raise HTTPException(404, "no static dashboard")
        return HTMLResponse(p.read_text())

    @app.post("/api/pipeline/run")
    async def pipeline_run(request: Request):
        _who = require(request, "operate")
        identity.audit(_who, "pipeline.run", "pipeline")
        body = await request.json() if int(request.headers.get("content-length") or 0) > 0 else {}
        return research.RUNNER.start(body.get("stages") or [])

    @app.get("/api/pipeline/status")
    def pipeline_status():
        result = research.RUNNER.status()
        worker = getattr(app.state, "platform_sync", None)
        result["platform_sync"] = worker.status() if worker is not None else {"status": "NOT_STARTED"}
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/tunnel")
    def tunnel_status():
        return tunnel.status()

    return app


def _raise_fd_limit(target: int = 4096) -> None:
    """launchd agents start with a 256 open-file soft limit; the poller's thread pool plus the API easily exceed it."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except Exception:                                   # pragma: no cover - platform
        pass


def serve(settings: config.Settings = config.SETTINGS, poll: bool = True, log_level: str = "info") -> None:
    import uvicorn
    from .poller import Poller, setup_logging
    setup_logging()
    _raise_fd_limit()
    auth.get_token()                                    # make sure the access token exists before we go online
    poller = Poller(settings) if poll else None
    app = create_app(settings, poller=poller)
    stop = threading.Event()
    threading.Thread(target=tunnel.watchdog_loop, args=(stop,), daemon=True, name="mi-tunnel-watchdog").start()
    if poller:
        threading.Thread(target=poller.loop, args=(stop,), daemon=True, name="mi-poller").start()
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level=log_level, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    finally:
        stop.set()
