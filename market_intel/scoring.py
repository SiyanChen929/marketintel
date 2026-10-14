"""Per-ticker event scores (decayed, signed), alert levels and the overlay consumed by the stock-selection pipeline."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config
from . import stories as ST
from .classify.rules import LEGACY_ACTIONS
from .entities import EntityIndex
from .store import Store

OVERLAY_COLS = ["ticker", "name", "segment", "is_holding", "weight", "model_weight", "event_score_7d", "event_score_1d", "n_events_7d", "n_copies_7d", "n_direct_7d", "n_review_7d",
                "max_impact_7d", "alert_level", "pillars", "action", "last_event_at", "last_event_type", "last_event_title", "last_event_url", "asof"]
CRITICAL_ITEMS = {"2.02", "2.05", "2.06", "4.02", "1.05", "1.03", "3.01", "5.01"}      # results, exit costs, impairment, non-reliance, cyber, bankruptcy, delisting, change of control
WARNING_ITEMS = {"1.01", "1.02", "5.02", "3.02", "2.03", "2.04"}                       # material agreements, officer changes, unregistered sales, debt


def decay(age_hours: float, half_life: float) -> float:
    return 0.5 ** (max(age_hours, 0.0) / half_life) if half_life > 0 else 1.0


def alert_level(event: dict, impacts: list[tuple[str, str, float]], index: EntityIndex, settings: config.Settings = config.SETTINGS) -> str | None:
    """critical / warning / info / None for one classified event, from its direct/read-through impacts."""
    m, d = float(event.get("materiality", 0)), int(event.get("direction", 0))
    direct = [t for t, rel, _ in impacts if rel == "direct"]
    held = [t for t in direct if index.companies[t].is_holding]
    stype = event.get("source_type")
    extra = event.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            extra = {}
    if event.get("event_type") == "commentary" or not event.get("relevant", True):
        return None
    if held:
        if stype == "sec" and CRITICAL_ITEMS & set(extra.get("items") or []):
            return "critical"
        if stype == "sec" and WARNING_ITEMS & set(extra.get("items") or []):
            return "warning"
        if stype == "price" and abs(float(extra.get("pct", 0))) >= settings.price_tiers[1]:
            return "critical"
        if m >= 0.75 and abs(d) == 2:
            return "critical"
        if m >= 0.55 and abs(d) >= 1:
            return "warning"
        if stype in ("sec", "price") or m >= 0.45:
            return "warning" if stype == "price" else "info"
        return "info"
    if direct:
        if m >= 0.8 and abs(d) == 2:
            return "warning"
        if stype == "price" and abs(float(extra.get("pct", 0))) >= settings.price_tiers[1]:
            return "warning"
        if m >= 0.5 or stype == "sec":
            return "info"
        return None
    if event.get("event_type") in ("macro_data", "fed_policy", "geopolitical", "regulatory_export", "tariff_trade", "policy"):
        if m >= 0.7 and abs(d) >= 1:
            return "warning"
        if m >= 0.45:
            return "info"
    rt_held = [(t, imp) for t, rel, imp in impacts if rel == "readthrough" and index.companies[t].is_holding]
    if rt_held and max(abs(i) for _, i in rt_held) >= 0.2:
        return "info"
    return None


def compute_scores(store: Store, index: EntityIndex, settings: config.Settings = config.SETTINGS, now: datetime | None = None) -> list[dict]:
    """Per-ticker decayed event scores. Copies of one story are collapsed first (S6): a story contributes its single most
    material copy (or the latest copy after a correction, nothing after a retraction), lifted by at most 4% for
    independent confirmation; ``n_events`` counts stories, ``n_copies`` counts rows."""
    now = now or datetime.now(timezone.utc)
    rows = store.event_tickers_since(now - timedelta(days=settings.lookback_days))
    snapshot = store.kv_get("price_snapshot", {}) or {}
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["ticker"], r.get("story_id") or r["event_id"]), []).append(r)
    per: dict[str, dict] = {}
    for (t, _key), copies in groups.items():
        status = copies[0].get("story_status") or "active"
        n_copies = len(copies)
        if status == "retracted":
            rep = None
        elif status == "corrected":
            rep = max(copies, key=lambda r: r["published"])
        else:
            rep = max(copies, key=lambda r: (abs(float(r["impact"])), r["published"]))
        first_pub = min(r["published"] for r in copies)
        pub = datetime.fromisoformat(first_pub)
        age = (now - pub).total_seconds() / 3600.0
        if age < -6:
            continue
        p = per.setdefault(t, {"score_7d": 0.0, "score_1d": 0.0, "events": set(), "copies": 0, "direct": set(), "max_abs": 0.0, "top": None, "last": None,
                               "last_direct": None, "pillars": set(), "actions": set(), "level": None, "review": 0})
        p["events"].add(_key); p["copies"] += n_copies
        if rep is None:
            continue
        lift = ST.bonus(int(copies[0].get("n_independent") or 1))
        impact = float(rep["impact"]) * lift
        w = impact * decay(age, settings.half_life_hours)
        p["score_7d"] += w
        if age <= 24:
            p["score_1d"] += impact
        if int(rep.get("needs_review") or 0):
            p["review"] += 1
        if rep["relation"] == "direct":
            p["direct"].add(_key)
            if rep.get("event_type") != "commentary" and float(rep.get("materiality") or 0) >= 0.3 and (p["last_direct"] is None or rep["published"] > p["last_direct"]["published"]):
                p["last_direct"] = rep
            for pl in rep.get("pillars") or []:
                p["pillars"].add(pl)
            if rep.get("action") and rep["action"] != "none":
                p["actions"].add(rep["action"])
            if age <= 48 and rep.get("alert_level"):
                if p["level"] is None or config.LEVEL_RANK.get(rep["alert_level"], 0) > config.LEVEL_RANK.get(p["level"], 0):
                    p["level"] = rep["alert_level"]
        if abs(w) > p["max_abs"]:
            p["max_abs"], p["top"] = abs(w), rep
        if rep.get("event_type") != "commentary" and (p["last"] is None or rep["published"] > p["last"]["published"]):
            p["last"] = rep
    out = []
    for t, c in index.companies.items():
        p = per.get(t)
        last = (p["last_direct"] or p["last"]) if p else None
        rank = {"refresh model inputs": 3, "analyst review": 2, "monitor": 1, "re-run model": 3, "thesis check": 2}
        action = max(p["actions"], key=lambda a: rank.get(a, 0)) if p and p["actions"] else "none"
        action = LEGACY_ACTIONS.get(action, action)
        px = snapshot.get(t) or {}
        out.append({"ticker": t, "name": c.name, "segment": c.segment, "is_holding": c.is_holding, "weight": round(c.weight, 4), "model_weight": round(getattr(c, "model_weight", 0.0), 4),
                    "event_score_7d": round(p["score_7d"], 4) if p else 0.0, "event_score_1d": round(p["score_1d"], 4) if p else 0.0,
                    "n_events_7d": len(p["events"]) if p else 0, "n_copies_7d": p["copies"] if p else 0, "n_direct_7d": len(p["direct"]) if p else 0,
                    "n_review_7d": p["review"] if p else 0,
                    "max_impact_7d": round(p["max_abs"], 4) if p else 0.0, "alert_level": p["level"] if p else None,
                    "pillars": "|".join(sorted(p["pillars"])) if p else "", "action": action,
                    "last_event_at": last["published"] if last else None, "last_event_type": last["event_type"] if last else None,
                    "last_event_title": (last.get("one_liner") or last["title"])[:160] if last else None, "last_event_url": last["url"] if last else None,
                    "top_event_id": p["top"]["event_id"] if p and p["top"] else None, "px_pct": px.get("pct"), "px_idio": px.get("idio"),
                    "next_earnings": c.next_earnings, "asof": now.isoformat(timespec="seconds")})
    out.sort(key=lambda r: (not r["is_holding"], -abs(r["event_score_7d"]), r["ticker"]))
    return out


def write_overlay(scores: list[dict], store: Store, index: EntityIndex, settings: config.Settings = config.SETTINGS, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    df = pd.DataFrame(scores)
    csv_path = config.LATEST / "event_overlay.csv"
    df[OVERLAY_COLS].to_csv(csv_path, index=False)
    store.set_scores(scores, now)
    top = store.events(since=now - timedelta(hours=36), relevant_only=True, min_materiality=0.5, order="materiality", limit=40)
    holdings = [s for s in scores if s["is_holding"]]
    run = config.run_info()
    summary = {"asof": now.isoformat(timespec="seconds"), "asof_et": now.astimezone(config.ET).strftime("%Y-%m-%d %H:%M ET"),
               "run": {k: run.get(k) for k in ("run_id", "asof_session", "generated_at", "validation_status", "actionable", "cash")},
               "n_holdings_flagged": sum(1 for s in holdings if s["alert_level"] in ("warning", "critical")),
               "holdings": [{k: s[k] for k in ("ticker", "weight", "model_weight", "event_score_7d", "event_score_1d", "n_events_7d", "alert_level", "action", "last_event_title")} for s in holdings],
               "movers": [{k: s[k] for k in ("ticker", "event_score_7d", "n_events_7d", "alert_level", "last_event_title")} for s in sorted(scores, key=lambda s: -abs(s["event_score_7d"]))[:15]],
               "top_events": [{k: e.get(k) for k in ("id", "published", "source_name", "event_type", "direction", "materiality", "tickers", "tickers_rt", "alert_level", "one_liner", "title", "url")} for e in top],
               "csv": str(csv_path)}
    (config.MI_OUT / "event_overlay.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def load_overlay(max_age_hours: float = 36.0, path=None) -> pd.DataFrame | None:
    """Used by the stock-selection pipeline: returns the overlay frame indexed by ticker, or None if missing/stale."""
    p = path or (config.LATEST / "event_overlay.csv")
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if df.empty or "asof" not in df.columns:
        return None
    try:
        asof = pd.Timestamp(df["asof"].iloc[0])
        age_h = (pd.Timestamp.now(tz="UTC") - asof.tz_convert("UTC")).total_seconds() / 3600
    except Exception:
        return None
    if age_h > max_age_hours:
        return None
    return df.set_index("ticker")
