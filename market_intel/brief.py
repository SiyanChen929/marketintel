"""Pre-market brief: portfolio events, value-chain themes, macro/earnings calendar. The configured LLM (Anthropic or OpenAI) writes it when available; the rule-based fallback otherwise."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import alerts as alerts_mod
from . import config
from .ask import evidence_block
from .classify.llm import BRIEF_SYSTEM, LLM
from .entities import EntityIndex
from .store import Store


def _collect(store: Store, index: EntityIndex, now: datetime, hours: int = 26) -> dict:
    since = now - timedelta(hours=hours)
    hold = index.holdings
    port = store.events(since=since, holdings=hold, relevant_only=True, order="materiality", limit=25) if hold else []
    port_ids = {e["id"] for e in port}
    chain = [e for e in store.events(since=since, relevant_only=True, min_materiality=0.45, order="materiality", limit=60) if e["id"] not in port_ids][:20]
    macro = [e for e in store.events(since=since, types=("macro_data", "fed_policy", "geopolitical", "policy", "tariff_trade", "regulatory_export"),
                                     relevant_only=True, order="materiality", limit=15)]
    today = now.astimezone(config.ET).date()
    cal = store.calendar(today.isoformat(), (today + timedelta(days=2)).isoformat())
    week = store.calendar(today.isoformat(), (today + timedelta(days=7)).isoformat())
    scores = {s["ticker"]: s for s in store.scores()}
    return {"portfolio": port, "chain": chain, "macro": macro, "calendar": cal, "week_earnings": [c for c in week if c["kind"] == "earnings"], "scores": scores}


def session_label(now_et: datetime) -> str:
    """Title of the brief by ET session: before 09:30 pre-market, 09:30-16:00 intraday, 16:00-20:00 post-close, after 20:00 next-open."""
    minutes = now_et.hour * 60 + now_et.minute
    if minutes < 9 * 60 + 30:
        return "Pre-market brief"
    if minutes < 16 * 60:
        return "Intraday brief"
    if minutes < 20 * 60:
        return "Post-close brief"
    return "Next-open brief"


def _title(now: datetime) -> str:
    et = now.astimezone(config.ET)
    return f"# {session_label(et)} - {et:%A %Y-%m-%d %H:%M ET}"


def _run_line() -> str:
    r = config.run_info()
    cash = f"{r['cash']:.1%}" if isinstance(r.get("cash"), (int, float)) else "n/a"
    act = "" if r.get("actionable") in (None, True) else " · NOT ACTIONABLE: " + "; ".join(r.get("not_actionable_reasons") or [])
    return (f"_Canonical run {r.get('run_id') or 'none'} (session {r.get('asof_session') or '-'}) · executable target weights, cash {cash} · "
            f"{r.get('validation_status')} · no live position ledger{act}_")


TRADE_WORDS = re.compile(r"(?:\b(?:stay long|consider (?:adding|add|trimming|trim|buying|selling)|add on (?:confirmation|weakness|dips?)|buy the dip|accumulate|go long|"
                         r"overweight|underweight|trim(?:ming)? (?:the )?position|initiate a (?:long|short)|short (?:the|it))\b|\*\*hold[.;:]?\*\*|\bhold[;:] )", re.I)


def sanitise(text: str) -> tuple[str, int]:
    """Replace trade instructions with analyst vocabulary; returns (text, number of replacements)."""
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        return "analyst review"
    return TRADE_WORDS.sub(rep, text or ""), n


def _fallback_text(d: dict, index: EntityIndex, now: datetime) -> str:
    def line(e: dict) -> str:
        tk = ",".join(e.get("tickers") or []) or ",".join((e.get("tickers_rt") or [])[:3])
        arrow = {2: "▲▲", 1: "▲", 0: "•", -1: "▼", -2: "▼▼"}.get(int(e.get("direction") or 0), "•")
        return f"- **{tk}** {arrow} {e.get('one_liner') or e['title']} ({e.get('source_name')}, {e.get('event_type')}, action: {e.get('action')})"
    parts = [_title(now), _run_line(), "", "## Target list"]
    parts += [line(e) for e in d["portfolio"][:12]] or ["- No material events on held names in the last 24 hours."]
    parts += ["", "## Value chain"] + ([line(e) for e in d["chain"][:10]] or ["- Quiet."])
    parts += ["", "## Macro & calendar"]
    parts += [f"- {c['date']} {c['time_et'] or ''} {c['name']}" for c in d["calendar"][:10]] or ["- No scheduled releases."]
    parts += [line(e) for e in d["macro"][:6]]
    parts += ["", "## Earnings this week (universe)"] + ([f"- {c['date']} {c['name']}" for c in d["week_earnings"][:15]] or ["- None."])
    flagged = [s for s in d["scores"].values() if s.get("is_holding") and s.get("alert_level") in ("warning", "critical")]
    parts += ["", "## Watch today"] + ([f"- {s['ticker']}: {s['alert_level']} - {s.get('last_event_title')}" for s in flagged] or ["- No flagged holdings."])
    return "\n".join(parts)


def build(store: Store, index: EntityIndex, settings: config.Settings = config.SETTINGS, llm: LLM | None = None, now: datetime | None = None,
          notify: bool = True) -> str:
    now = now or datetime.now(timezone.utc)
    d = _collect(store, index, now)
    text, mode, n_viol = "", "rules", 0
    if llm is not None and llm.available:
        ev = d["portfolio"] + d["chain"] + d["macro"]
        seen, ev2 = set(), []
        for e in ev:
            if e["id"] not in seen:
                seen.add(e["id"]); ev2.append(e)
        cal = "\n".join(f"{c['date']} {c['time_et'] or ''} {c['name']} (importance {c['importance']})" for c in d["calendar"][:12] + d["week_earnings"][:12])
        held = "\n".join(f"{s['ticker']} target {s['weight']:.1%} (model {s.get('model_weight', 0.0):.1%}) 7d score {s['event_score_7d']:+.2f} level {s['alert_level'] or '-'} action {s['action']}"
                         for s in d["scores"].values() if s.get("is_holding"))
        run = config.run_info()
        user = (f"Date: {now.astimezone(config.ET):%A %Y-%m-%d}\nCanonical run: {run.get('run_id')} ({run.get('validation_status')}); weights are executable targets with "
                f"cash {run.get('cash') if run.get('cash') is not None else 'n/a'}; there is no live position ledger.\nTarget list (top-15): {', '.join(index.holdings)}\n\n"
                f"Target-list scores:\n{held}\n\n"
                f"Evidence:\n{evidence_block(ev2[:45])}\n\nCalendar:\n{cal}")
        text = llm.complete(BRIEF_SYSTEM, user, effort="high", max_tokens=3000, operation="brief")
        text, n_viol = sanitise(text)
        if text:
            mode = llm.provider or "llm"
            text = f"{_title(now)}\n{_run_line()}\n\n" + text
    if not text:
        text = _fallback_text(d, index, now)
    label = session_label(now.astimezone(config.ET))
    date = now.astimezone(config.ET).date().isoformat()
    (config.LATEST / "market_brief.md").write_text(text)
    run = config.run_info()
    store.save_brief(date, text, {"mode": mode, "session": label, "model": getattr(llm, "model", None) if llm is not None else None, "run_id": run.get("run_id"), "wording_violations": n_viol,
                                  "n_portfolio": len(d["portfolio"]), "n_chain": len(d["chain"]), "n_macro": len(d["macro"])})
    if notify and settings.notify:
        alerts_mod.notify_macos(f"Market Intel · {label}", f"{len(d['portfolio'])} target-list events, {len(d['chain'])} value-chain, {len(d['calendar'])} calendar items", sound=False)
    return text
