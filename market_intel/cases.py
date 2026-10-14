"""Review cases (P1 W2): one case per story x target-list name for warning/critical escalations.

A case is the analyst workflow record behind an escalation: who owns it, when it is due (created + SLA per alert level),
what evidence it collects (event ids of the story), the decision reached and who signed it off. Cases never carry
trade instructions; the decision vocabulary is analyst review / monitor / refresh model inputs / no change.
Status lifecycle: open -> acknowledged -> in_review -> decided -> closed (any status may be closed directly)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import config
from .store import Store

STATUSES = ("open", "acknowledged", "in_review", "decided", "closed")
OPEN_STATUSES = Store.OPEN_CASE_STATUSES
LEVELS = ("info", "warning", "critical")
EDITABLE = ("owner", "due", "status", "decision", "no_trade_reason", "execution_ref", "signoff_by", "level", "title")
DEFAULT_SLA = {"critical": 4, "warning": 24}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def sla_hours(level: str | None, settings: config.Settings = config.SETTINGS) -> float:
    sla = getattr(settings, "case_sla_hours", None) or DEFAULT_SLA
    return float(sla.get(level or "warning", sla.get("warning", DEFAULT_SLA["warning"])))


def decorate(case: dict, now: datetime | None = None) -> dict:
    """Add derived fields: overdue flag, hours to due (negative when overdue), age in hours, open flag."""
    now = now or _now()
    c = dict(case)
    due, created = _parse(c.get("due")), _parse(c.get("created"))
    c["is_open"] = c.get("status") in OPEN_STATUSES
    c["hours_to_due"] = round((due - now).total_seconds() / 3600.0, 2) if due else None
    c["overdue"] = bool(due and now > due and c["is_open"])
    c["age_hours"] = round((now - created).total_seconds() / 3600.0, 2) if created else None
    c["n_evidence"] = len(c.get("evidence") or [])
    return c


def ensure_case(store: Store, event: dict, ticker: str, level: str | None, settings: config.Settings = config.SETTINGS, now: datetime | None = None) -> dict:
    """Idempotent by (story_id, ticker): create the case for a warning/critical event on a target-list name, or attach the
    event to the existing case (evidence). A higher level escalates the case and tightens the due date while it is open."""
    now = now or _now()
    story_id = event.get("story_id") or ("st_" + str(event["id"]))
    ticker = str(ticker).upper()
    level = level or event.get("alert_level") or "warning"
    if level not in LEVELS:
        level = "warning"
    existing = store.find_case(story_id, ticker)
    if existing:
        fields: dict[str, Any] = {}
        ev = list(existing.get("evidence") or [])
        if event["id"] not in ev:
            ev.append(event["id"])
            fields["evidence"] = ev
        if config.LEVEL_RANK.get(level, 0) > config.LEVEL_RANK.get(existing.get("level") or "", 0):
            fields["level"] = level
            if existing.get("status") in OPEN_STATUSES:
                created = _parse(existing.get("created")) or now
                hours = sla_hours(level, settings)
                fields["due"], fields["sla_hours"] = _iso(created + timedelta(hours=hours)), hours
        return store.update_case_row(existing["id"], **fields) if fields else existing
    hours = sla_hours(level, settings)
    title = (event.get("one_liner") or event.get("title") or "")[:200]
    return store.insert_case({"created": _iso(now), "updated": _iso(now), "story_id": story_id, "event_id": event["id"], "ticker": ticker, "title": title, "level": level,
                              "due": _iso(now + timedelta(hours=hours)), "sla_hours": hours, "status": "open", "evidence": [event["id"]], "notes": []})


def ensure_for_event(store: Store, event: dict, targets: Iterable[str], settings: config.Settings = config.SETTINGS, now: datetime | None = None) -> list[dict]:
    """Cases for every target-list name among the event's direct tickers (warning/critical events only)."""
    level = event.get("alert_level")
    if level not in ("warning", "critical"):
        return []
    ts = set(targets)
    tickers = event.get("tickers") or []
    tickers = list(tickers.keys()) if isinstance(tickers, dict) else list(tickers)
    return [ensure_case(store, event, t, level, settings, now) for t in tickers if t in ts]


def update_case(store: Store, id_: int, **fields: Any) -> dict:
    """Analyst update. Accepts owner, due, status, decision, no_trade_reason, execution_ref, signoff_by, level, title, note, by.
    Any status may move to closed; acknowledging stamps ack_at/ack_by; a sign-off stamps signoff_at; a note is appended as {at, by, text}."""
    case = store.get_case(id_)
    if not case:
        raise KeyError(f"unknown case {id_}")
    now = _now()
    by = str(fields.pop("by", None) or "analyst")[:80]
    note = fields.pop("note", None)
    upd: dict[str, Any] = {}
    for k in EDITABLE:
        if k in fields and fields[k] is not None:
            upd[k] = fields[k]
    if "status" in upd:
        s = str(upd["status"]).strip().lower().replace(" ", "_").replace("-", "_")
        if s not in STATUSES:
            raise ValueError(f"unknown status '{upd['status']}' (expected one of {', '.join(STATUSES)})")
        upd["status"] = s
        if s == "acknowledged" and not case.get("ack_at"):
            upd["ack_at"], upd["ack_by"] = _iso(now), by
    if "level" in upd and upd["level"] not in LEVELS:
        raise ValueError(f"unknown level '{upd['level']}'")
    if "due" in upd:
        d = _parse(str(upd["due"]))
        if d is None:
            raise ValueError("due must be an ISO-8601 timestamp")
        upd["due"] = _iso(d)
    for k in ("owner", "decision", "no_trade_reason", "execution_ref", "signoff_by", "title"):
        if k in upd:
            upd[k] = str(upd[k])[:400]
    if upd.get("signoff_by"):
        upd["signoff_at"] = _iso(now)
    if fields.get("evidence"):
        ev = list(case.get("evidence") or [])
        for e in fields["evidence"]:
            if e and e not in ev:
                ev.append(str(e))
        upd["evidence"] = ev
    if note:
        notes = list(case.get("notes") or [])
        notes.append({"at": _iso(now), "by": by, "text": str(note)[:2000]})
        upd["notes"] = notes
    if not upd:
        return decorate(case, now)
    return decorate(store.update_case_row(id_, **upd), now)


def acknowledge(store: Store, id_: int, by: str = "analyst") -> dict:
    case = store.get_case(id_)
    if not case:
        raise KeyError(f"unknown case {id_}")
    if case.get("status") == "open":
        return update_case(store, id_, status="acknowledged", by=by)
    if not case.get("ack_at"):
        return decorate(store.update_case_row(id_, ack_at=_iso(_now()), ack_by=str(by)[:80]))
    return decorate(case)


def get_case(store: Store, id_: int) -> dict | None:
    c = store.get_case(id_)
    return decorate(c) if c else None


def list_cases(store: Store, status: str | Iterable[str] | None = None, ticker: str | None = None, limit: int = 100) -> list[dict]:
    if isinstance(status, str):                       # "" / "all" -> every case; "active" -> open + acknowledged + in_review; "a,b" -> either
        s = status.strip().lower()
        if s in ("", "all"):
            status = None
        elif s == "active":
            status = OPEN_STATUSES
        else:
            status = [x.strip() for x in s.split(",") if x.strip()]
            unknown = [x for x in status if x not in STATUSES]
            if unknown:
                raise ValueError(f"unknown status '{unknown[0]}' (expected one of {', '.join(STATUSES)}, active, all)")
    now = _now()
    return [decorate(c, now) for c in store.list_cases(status=status, ticker=ticker, limit=limit)]


def overdue(store: Store, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    return [decorate(c, now) for c in store.list_cases(status=OPEN_STATUSES, limit=500, overdue_before=now)]


def summary(store: Store, now: datetime | None = None) -> dict:
    now = now or _now()
    open_cases = store.list_cases(status=OPEN_STATUSES, limit=1000)
    od = [c for c in open_cases if (d := _parse(c.get("due"))) and now > d]
    by_status = {s: 0 for s in STATUSES}
    for c in store.list_cases(limit=5000):
        by_status[c.get("status") or "open"] = by_status.get(c.get("status") or "open", 0) + 1
    return {"open": len(open_cases), "overdue": len(od), "unacknowledged": sum(1 for c in open_cases if c.get("status") == "open"), "by_status": by_status}
