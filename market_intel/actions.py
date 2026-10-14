"""Action feed (P1 W1): what needs the analyst's attention now, in one payload.

Sections: escalations that require review (warning/critical events on target-list names, one per story), the target list
ordered by event impact, the next five days of catalysts, new evidence since the analyst last looked, and system health.
Vocabulary: positions are target-list names with executable target weights; actions are analyst review / monitor /
refresh model inputs. No trade instructions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config, research
from .entities import EntityIndex
from .store import Store

EVIDENCE_EXCLUDED_TYPES = ("commentary",)
CATALYST_DAYS = 5
MAX_HOURS = 24 * 14


def _age(iso: str | None, now: datetime) -> str | None:
    if not iso:
        return None
    try:
        d = now - datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None
    s = int(d.total_seconds())
    if s < 0:
        return "now"
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def parse_since(since, default: datetime) -> datetime:
    """ISO timestamp (naive = UTC, trailing Z accepted) or datetime; anything else falls back to ``default``."""
    if isinstance(since, datetime):
        return since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    if isinstance(since, str) and since.strip():
        try:
            d = datetime.fromisoformat(since.strip().replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return default
    return default


def _direct_tickers(e: dict) -> list[str]:
    tk = e.get("tickers") or []
    return list(tk.keys()) if isinstance(tk, dict) else [str(t) for t in tk]


def action_required(store: Store, index: EntityIndex, targets: dict, now: datetime, hours: int) -> list[dict]:
    if not targets:
        return []
    rows = store.events(since=now - timedelta(hours=hours), levels=("warning", "critical"), relevant_only=True, collapse=True, holdings=list(targets), limit=300)
    rows = [e for e in rows if any(t in targets for t in _direct_tickers(e))]
    stories = store.stories_for(e.get("story_id") for e in rows)
    pairs = []
    for e in rows:
        tk = [t for t in _direct_tickers(e) if t in targets]
        e["_ticker"] = tk[0]
        pairs.append((e.get("story_id") or ("st_" + e["id"]), tk[0]))
    case_map = store.cases_for(pairs)
    items = []
    for e in rows:
        t = e["_ticker"]
        sid = e.get("story_id") or ("st_" + e["id"])
        st = stories.get(e.get("story_id")) or {}
        case = case_map.get((sid, t))
        items.append({"id": e["id"], "story_id": e.get("story_id"), "level": e.get("alert_level"), "ticker": t, "weight": targets[t].weight, "model_weight": getattr(targets[t], "model_weight", 0.0),
                      "target_tickers": [x for x in _direct_tickers(e) if x in targets], "event_type": e.get("event_type"), "title": e.get("title"), "one_liner": e.get("one_liner") or "",
                      "direction": e.get("direction"), "materiality": e.get("materiality"), "source_name": e.get("source_name"), "url": e.get("url"), "published": e.get("published"),
                      "age": _age(e.get("published"), now), "action": e.get("action") or "none", "read": int(e.get("read") or 0), "needs_review": int(e.get("needs_review") or 0),
                      "case_id": case["id"] if case else None, "case_status": case["status"] if case else None, "case_due": case.get("due") if case else None,
                      "n_copies": int(st.get("n_events") or 1), "n_independent": st.get("n_independent"), "story_status": st.get("status")})
    items.sort(key=lambda i: i["published"] or "", reverse=True)
    items.sort(key=lambda i: (i["read"], -config.LEVEL_RANK.get(i["level"] or "", 0)))
    return items


def portfolio_impact(store: Store, targets: dict) -> list[dict]:
    snap = store.kv_get("price_snapshot", {}) or {}
    scores = {r["ticker"]: r for r in store.scores()}
    keys = ("event_score_7d", "event_score_1d", "n_events_7d", "n_copies_7d", "n_direct_7d", "n_review_7d", "alert_level", "action", "last_event_title", "last_event_at",
            "last_event_type", "last_event_url", "top_event_id", "pillars", "asof")
    out = []
    for t, c in targets.items():
        r = scores.get(t) or {}
        px = snap.get(t) or {}
        row = {"ticker": t, "name": c.name, "segment": c.segment, "weight": round(float(c.weight), 6), "model_weight": round(float(getattr(c, "model_weight", 0.0)), 6)}
        for k in keys:
            row[k] = r.get(k, 0.0 if k.startswith(("event_score",)) else 0 if k.startswith("n_") else None)
        row["action"] = row["action"] or "none"
        row["px_pct"] = px.get("pct", r.get("px_pct"))
        row["px_idio"] = px.get("idio", r.get("px_idio"))
        row["next_earnings"] = c.next_earnings or r.get("next_earnings")
        out.append(row)
    out.sort(key=lambda r: (-abs(float(r.get("event_score_7d") or 0.0)), -config.LEVEL_RANK.get(r.get("alert_level") or "", 0), r["ticker"]))
    return out


def catalysts(store: Store, targets: dict, now: datetime, days: int = CATALYST_DAYS) -> list[dict]:
    today = config.now_et(now).date()
    rows = store.calendar(today.isoformat(), (today + timedelta(days=days)).isoformat())
    out = []
    for r in rows:
        r = dict(r)
        t = (r.get("ticker") or "").upper() or None
        r["ticker"] = t
        r["is_target"] = bool(r.get("kind") == "earnings" and t in targets)
        r["weight"] = round(float(targets[t].weight), 6) if r["is_target"] else None
        r["days_ahead"] = (datetime.fromisoformat(r["date"]).date() - today).days if r.get("date") else None
        out.append(r)
    out.sort(key=lambda r: (r.get("date") or "", -int(r.get("importance") or 0), not r["is_target"], r.get("time_et") or ""))
    return out


def new_evidence(store: Store, targets: dict, now: datetime, since: datetime, top: int = 10) -> dict:
    rows = store.events(since=since, relevant_only=True, collapse=True, order="materiality", limit=1000)
    rows = [e for e in rows if e.get("event_type") not in EVIDENCE_EXCLUDED_TYPES]
    stories = store.stories_for(e.get("story_id") for e in rows[:top])
    items = []
    for e in rows[:top]:
        st = stories.get(e.get("story_id")) or {}
        items.append({"id": e["id"], "story_id": e.get("story_id"), "title": e.get("title"), "one_liner": e.get("one_liner") or "", "event_type": e.get("event_type"),
                      "direction": e.get("direction"), "materiality": e.get("materiality"), "alert_level": e.get("alert_level"), "tickers": _direct_tickers(e),
                      "target_tickers": [t for t in _direct_tickers(e) if t in targets], "tickers_rt": list(e.get("tickers_rt") or [])[:8], "source_name": e.get("source_name"),
                      "url": e.get("url"), "published": e.get("published"), "age": _age(e.get("published"), now), "read": int(e.get("read") or 0), "action": e.get("action") or "none",
                      "n_copies": int(st.get("n_events") or 1)})
    return {"since": since.isoformat(timespec="seconds"), "count": len(rows), "items": items,
            "n_on_targets": sum(1 for e in rows if any(t in targets for t in _direct_tickers(e)))}


def system_health(store: Store, settings: config.Settings, run: dict, now: datetime, llm=None, polling: bool | None = None) -> dict:
    last = store.kv_get("last_cycle", {}) or {}
    feeds = store.all_feed_states()
    errors = [{"feed": fid, "error": fs.get("error"), "status": fs.get("last_status"), "last_fetch": fs.get("last_fetch"), "age": _age(fs.get("last_fetch"), now)}
              for fid, fs in sorted(feeds.items()) if fs.get("error")]
    dh = run.get("data_health") or {}
    cycle_at = last.get("at")
    cycle_age_min = None
    if cycle_at:
        try:
            cycle_age_min = round((now - datetime.fromisoformat(cycle_at).astimezone(timezone.utc)).total_seconds() / 60.0, 1)
        except ValueError:
            cycle_age_min = None
    interval = settings.poll_interval_market if config.is_market_hours(now, settings) else settings.poll_interval_off
    stale_cycle = bool(cycle_age_min is not None and cycle_age_min * 60 > 3 * interval)
    return {"last_cycle": {k: last.get(k) for k in ("cycle", "at", "at_et", "duration_s", "errors", "fetched", "new", "new_relevant", "llm_classified", "alerts", "notified")},
            "cycle_age_min": cycle_age_min, "cycle_stale": stale_cycle,
            "feeds": {"n": len(feeds), "n_errors": len(errors), "errors": errors[:25]},
            "llm": {"available": bool(llm is not None and getattr(llm, "available", False)), "provider": getattr(llm, "provider", None), "model": getattr(llm, "model", None),
                    "disabled_reason": getattr(llm, "disabled_reason", None), "calls": getattr(llm, "calls", 0)},
            "data_health": {"status": dh.get("status"), "stale": list(dh.get("stale") or []), "price_gaps": dict(dh.get("price_gaps") or {})},
            "run_blocked": run.get("blocked"), "actionable": run.get("actionable"), "not_actionable_reasons": run.get("not_actionable_reasons") or [],
            "polling": polling, "poll_interval_market": settings.poll_interval_market, "poll_interval_off": settings.poll_interval_off, "poll_interval_now": interval,
            "market_hours": config.is_market_hours(now, settings), "brief_date": store.kv_get("brief_date"), "price_snapshot_at": store.kv_get("price_snapshot_at"),
            "overlay_asof": (store.scores() or [{}])[0].get("asof") if store.scores() else None}


def build(store: Store, index: EntityIndex, settings: config.Settings = config.SETTINGS, hours: int = 48, since=None, llm=None, polling: bool | None = None,
          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    hours = max(1, min(int(hours or 48), MAX_HOURS))
    since_dt = parse_since(since, now - timedelta(hours=24))
    targets = {t: c for t, c in index.companies.items() if c.is_holding}
    run = config.run_info()
    ar = action_required(store, index, targets, now, hours)
    stats = store.stats(targets=list(targets))
    payload = {"run": run, "generated_at": now.isoformat(timespec="seconds"), "hours": hours, "n_targets": len(targets),
               "counts": {"relevant_24h": stats.get("relevant_24h", 0), "events_24h": stats.get("events_24h", 0), "escalations_open": len(ar),
                          "escalations_unread": sum(1 for i in ar if not i["read"]), "escalations_unread_48h": stats.get("escalations_unread", 0),
                          "cases_open": stats.get("cases_open", 0), "cases_overdue": sum(1 for c in store.list_cases(status=store.OPEN_CASE_STATUSES, limit=500, overdue_before=now))},
               "action_required": ar, "portfolio_impact": portfolio_impact(store, targets), "catalysts": catalysts(store, targets, now),
               "new_evidence": new_evidence(store, targets, now, since_dt), "system_health": system_health(store, settings, run, now, llm, polling),
               "wording": "target-list names with executable target weights; no live position ledger; actions are analyst review / monitor / refresh model inputs"}
    return research.clean(payload)
