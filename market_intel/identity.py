"""Named actors, roles and a tamper-evident audit log (P0-3).

The service previously authenticated a *shared* token and took the actor's name from the request body, so any
caller could claim to be anyone and could sign off their own work. This module makes identity a server-side
fact:

* each person holds their own token, which maps to a named account with a role;
* the actor is derived from the session and can never be supplied by the client;
* privileged transitions require a *different* actor with an approving role (maker-checker);
* every mutation appends one immutable, hash-chained AuditEvent.

Enterprise OIDC/SSO is the eventual target. Nothing here blocks it: `Actor` is the seam an OIDC callback would
populate instead of a local token, and the role names are chosen to match the review's model.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import config

# role -> what it may do. Deliberately coarse; object-level ACLs come later.
ROLES = ("viewer", "analyst", "pm", "risk", "quantops", "admin")
CAN_WRITE = {"analyst", "pm", "risk", "quantops", "admin"}
CAN_APPROVE = {"pm", "risk", "admin"}            # maker-checker: only these may sign off someone else's work
CAN_OPERATE = {"quantops", "admin"}              # run the pipeline, poll, rebuild artefacts
ACCOUNTS_PATH = config.DATA_DIR / "accounts.sqlite"
#: The audit chain lives in the BUSINESS database so a state change and its audit event share one transaction.
#: Splitting them across files was what allowed an unaudited mutation to commit.
AUDIT_PATH = config.DB_PATH
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL,
  token_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor_id TEXT, actor TEXT NOT NULL, role TEXT NOT NULL,
  action TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT, before TEXT, after TEXT, meta TEXT,
  prev_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_object ON audit_events(object_type, object_id);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_events(actor, at);
CREATE TABLE IF NOT EXISTS audit_checkpoint (id INTEGER PRIMARY KEY CHECK (id = 1), head TEXT NOT NULL, n_events INTEGER NOT NULL, at TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Actor:
    """Who is acting. Always derived server-side - never from the request body."""
    id: str
    username: str
    display_name: str
    role: str
    source: str = "token"          # token | local | service | oidc (future)

    @property
    def can_write(self) -> bool:
        return self.role in CAN_WRITE

    @property
    def can_approve(self) -> bool:
        return self.role in CAN_APPROVE

    @property
    def can_operate(self) -> bool:
        return self.role in CAN_OPERATE

    def to_dict(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role,
                "source": self.source, "can_write": self.can_write, "can_approve": self.can_approve, "can_operate": self.can_operate}


#: The identity used when the request comes from the loopback interface with no account token. It keeps the
#: single-user local workflow working, but it is a *named* actor with a role, and it is audited like any other.
LOCAL_ACTOR = Actor(id="local", username="local", display_name="Local operator", role="admin", source="local")
ANONYMOUS = Actor(id="anon", username="anonymous", display_name="Anonymous", role="viewer", source="none")
#: Authenticated by the shared token but not attributable to a person: read-only by construction.
LEGACY_SHARED = Actor(id="legacy", username="shared-token", display_name="Shared token (unattributed)",
                      role="viewer", source="legacy_shared")


def _conn() -> sqlite3.Connection:
    """Accounts database."""
    c = sqlite3.connect(str(ACCOUNTS_PATH), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def _audit_conn() -> sqlite3.Connection:
    """Audit database - the same file the business tables live in, so writes can be atomic with them."""
    c = sqlite3.connect(str(AUDIT_PATH), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    ensure_audit_tables(c)
    c.commit()
    return c


def _hash_token(token: str) -> str:
    return hashlib.sha256(("mi-account:" + token).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- accounts
def create_account(username: str, display_name: str, role: str, token: str | None = None,
                   actor: "Actor | None" = None) -> tuple[Actor, str]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
    token = token or secrets.token_urlsafe(24)
    aid = hashlib.sha1(username.encode()).hexdigest()[:12]
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO accounts(id, username, display_name, role, token_hash, active, created_at, last_seen) "
                  "VALUES (?,?,?,?,?,1,?,NULL)", (aid, username, display_name or username, role, _hash_token(token), _now()))
    who = Actor(id=aid, username=username, display_name=display_name or username, role=role)
    audit(actor or LOCAL_ACTOR, "account.create", "account", username,
          after={"username": username, "role": role, "display_name": display_name or username})
    return who, token


def list_accounts() -> list[dict]:
    with _lock, _conn() as c:
        return [{k: r[k] for k in ("id", "username", "display_name", "role", "active", "created_at", "last_seen")}
                for r in c.execute("SELECT * FROM accounts ORDER BY username")]


def deactivate_account(username: str, actor: "Actor | None" = None) -> bool:
    with _lock, _conn() as c:
        ok = c.execute("UPDATE accounts SET active=0 WHERE username=?", (username,)).rowcount > 0
    if ok:
        audit(actor or LOCAL_ACTOR, "account.disable", "account", username, before={"active": 1}, after={"active": 0})
    return ok


def actor_for_token(token: str | None) -> Actor | None:
    """Constant-time lookup of a personal token. Returns None for the shared legacy token."""
    if not token:
        return None
    th = _hash_token(token)
    with _lock, _conn() as c:
        for r in c.execute("SELECT * FROM accounts WHERE active=1"):
            if hmac.compare_digest(th, r["token_hash"]):
                c.execute("UPDATE accounts SET last_seen=? WHERE id=?", (_now(), r["id"]))
                return Actor(id=r["id"], username=r["username"], display_name=r["display_name"], role=r["role"])
    return None


def any_accounts() -> bool:
    with _lock, _conn() as c:
        return c.execute("SELECT COUNT(*) FROM accounts WHERE active=1").fetchone()[0] > 0


# ----------------------------------------------------------------------------- audit
def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str) if obj is not None else ""


AUDIT_SCHEMA = SCHEMA.split("CREATE TABLE IF NOT EXISTS audit_events", 1)[1]
AUDIT_DDL = "CREATE TABLE IF NOT EXISTS audit_events" + AUDIT_SCHEMA


def ensure_audit_tables(conn) -> None:
    """Create the audit tables inside an arbitrary connection, so a caller can write them transactionally.

    Uses individual ``execute`` calls: ``executescript`` issues an implicit COMMIT in Python's sqlite3, which
    would silently end the caller's transaction and defeat the atomicity this exists to provide.
    """
    for stmt in [x.strip() for x in AUDIT_DDL.split(";") if x.strip()]:
        conn.execute(stmt)


def audit(actor: Actor, action: str, object_type: str, object_id: str | None = None,
          before: Any = None, after: Any = None, meta: dict | None = None, conn=None) -> dict:
    """Append one immutable audit event, chained to its predecessor.

    Each row stores sha256(prev_hash | at | actor | action | object | before | after | meta). Editing or
    deleting any earlier row breaks every subsequent hash, so tampering is detectable by ``verify_chain``.
    """
    if conn is not None:                      # write inside the caller's transaction: no separate commit
        return _audit_on(conn, actor, action, object_type, object_id, before, after, meta)
    with _lock, _audit_conn() as c:
        prev = c.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = prev["hash"] if prev else "genesis"
        at = _now()
        payload = "|".join([prev_hash, at, actor.username, actor.role, action, object_type, str(object_id or ""),
                            _canon(before), _canon(after), _canon(meta)])
        h = hashlib.sha256(payload.encode()).hexdigest()
        c.execute("INSERT INTO audit_events(at, actor_id, actor, role, action, object_type, object_id, before, after, meta, prev_hash, hash) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (at, actor.id, actor.username, actor.role, action, object_type, str(object_id or ""),
                   _canon(before) or None, _canon(after) or None, _canon(meta) or None, prev_hash, h))
        n = c.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        # checkpoint head+count so that deleting the TAIL (which leaves the remaining chain self-consistent)
        # is still detected; without it, truncation is invisible
        c.execute("INSERT INTO audit_checkpoint(id, head, n_events, at) VALUES (1,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET head=excluded.head, n_events=excluded.n_events, at=excluded.at", (h, n, at))
        return {"at": at, "actor": actor.username, "action": action, "object_type": object_type, "object_id": object_id, "hash": h}


def _audit_on(c, actor: Actor, action: str, object_type: str, object_id, before, after, meta) -> dict:
    """The append+checkpoint body, against a connection the caller owns (and will commit or roll back)."""
    ensure_audit_tables(c)
    prev = c.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = (prev[0] if not isinstance(prev, dict) else prev["hash"]) if prev else "genesis"
    at = _now()
    payload = "|".join([prev_hash, at, actor.username, actor.role, action, object_type, str(object_id or ""),
                        _canon(before), _canon(after), _canon(meta)])
    h = hashlib.sha256(payload.encode()).hexdigest()
    c.execute("INSERT INTO audit_events(at, actor_id, actor, role, action, object_type, object_id, before, after, meta, prev_hash, hash) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (at, actor.id, actor.username, actor.role, action, object_type, str(object_id or ""),
               _canon(before) or None, _canon(after) or None, _canon(meta) or None, prev_hash, h))
    n = c.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    c.execute("INSERT INTO audit_checkpoint(id, head, n_events, at) VALUES (1,?,?,?) "
              "ON CONFLICT(id) DO UPDATE SET head=excluded.head, n_events=excluded.n_events, at=excluded.at", (h, n, at))
    return {"at": at, "actor": actor.username, "action": action, "object_type": object_type, "object_id": object_id, "hash": h}


def audit_log(object_type: str | None = None, object_id: str | None = None, actor: str | None = None, limit: int = 200) -> list[dict]:
    q, args = "SELECT * FROM audit_events", []
    where = []
    if object_type:
        where.append("object_type=?"); args.append(object_type)
    if object_id is not None:
        where.append("object_id=?"); args.append(str(object_id))
    if actor:
        where.append("actor=?"); args.append(actor)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY seq DESC LIMIT ?"
    args.append(int(limit))
    with _lock, _audit_conn() as c:
        out = []
        for r in c.execute(q, args):
            d = {k: r[k] for k in r.keys()}
            for k in ("before", "after", "meta"):
                if d.get(k):
                    try:
                        d[k] = json.loads(d[k])
                    except json.JSONDecodeError:
                        pass
            out.append(d)
        return out


def verify_chain() -> dict:
    """Recompute every hash. Returns the first break, if any - this is what makes the log tamper-evident."""
    with _lock, _audit_conn() as c:
        rows = list(c.execute("SELECT * FROM audit_events ORDER BY seq"))
        cp = c.execute("SELECT head, n_events, at FROM audit_checkpoint WHERE id=1").fetchone()
    prev_hash = "genesis"
    for r in rows:
        payload = "|".join([prev_hash, r["at"], r["actor"], r["role"], r["action"], r["object_type"], r["object_id"] or "",
                            r["before"] or "", r["after"] or "", r["meta"] or ""])
        h = hashlib.sha256(payload.encode()).hexdigest()
        if h != r["hash"] or r["prev_hash"] != prev_hash:
            return {"ok": False, "n_events": len(rows), "broken_at_seq": r["seq"], "expected": h, "stored": r["hash"]}
        prev_hash = r["hash"]
    if cp is not None:
        if cp["n_events"] != len(rows) or cp["head"] != prev_hash:
            return {"ok": False, "n_events": len(rows), "head": prev_hash, "reason": "tail truncation or replacement",
                    "checkpoint": {"head": cp["head"], "n_events": cp["n_events"], "at": cp["at"]},
                    "detail": f"checkpoint expects {cp['n_events']} events ending {cp['head'][:12]}, found {len(rows)} ending {prev_hash[:12]}"}
    return {"ok": True, "n_events": len(rows), "head": prev_hash,
            "checkpoint_present": cp is not None,
            "threat_model": "detects in-place edits, row deletion and tail truncation by any writer that does not also "
                            "forge the checkpoint. It does NOT make the table immutable: an attacker with write access "
                            "to this database can rewrite the chain AND the checkpoint together. Off-box replication "
                            "of the head is required for tamper-evidence against that attacker."}


# ----------------------------------------------------------------------------- maker-checker
class ApprovalError(PermissionError):
    """Raised when an actor tries to approve their own work, or lacks the approving role."""


def check_maker_checker(actor: Actor, maker: str | None, action: str = "sign off") -> None:
    if not actor.can_approve:
        raise ApprovalError(f"{actor.username} ({actor.role}) may not {action}; requires one of {', '.join(sorted(CAN_APPROVE))}")
    if maker and maker.strip().lower() == actor.username.strip().lower():
        raise ApprovalError(f"{actor.username} raised this item and may not {action} their own work; a second approver is required")
