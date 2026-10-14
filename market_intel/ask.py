"""Question answering over the event store: retrieve evidence (tickers, full-text, recency), then let the configured LLM (Anthropic or OpenAI) synthesise."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterator

from . import config
from .classify.llm import ASK_SYSTEM, LLM
from .entities import EntityIndex
from .store import Store

ARROW = {2: "++", 1: "+", 0: "0", -1: "-", -2: "--"}
STOPWORDS = {"what", "which", "who", "when", "where", "why", "how", "did", "does", "do", "is", "are", "was", "were", "be", "been", "the", "a", "an", "and", "or",
             "of", "to", "in", "on", "for", "with", "about", "this", "that", "these", "those", "it", "its", "my", "our", "we", "i", "you", "should", "could", "would",
             "will", "can", "any", "there", "here", "today", "yesterday", "week", "month", "year", "recent", "recently", "latest", "news", "happened", "happen", "moved",
             "move", "moving", "stock", "stocks", "share", "shares", "position", "positions", "portfolio", "holdings", "holding", "thesis", "change", "changes", "at", "from",
             "by", "as", "if", "so", "up", "down", "into", "over", "under", "vs", "versus", "than", "then", "now", "still", "also", "just", "please", "tell", "me", "give", "explain"}


def _fmt_et(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(config.ET).strftime("%Y-%m-%d %H:%M ET")
    except Exception:
        return iso


def question_tickers(question: str, index: EntityIndex) -> list[str]:
    found = set(index.match(question))
    for sym in re.findall(r"\b[A-Z]{2,5}\b", question):          # the analyst typing a symbol is unambiguous
        if sym in index.companies:
            found.add(sym)
    if re.search(r"\b(portfolio|holdings?|our (names|positions|book)|top ?15)\b", question, re.I):
        found.update(index.holdings)
    return sorted(found)


def retrieve(store: Store, index: EntityIndex, question: str, k: int = 30, days: int = 14) -> list[dict]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    tickers = question_tickers(question, index)
    by_ticker: list[dict] = []
    for t in tickers[:20]:
        by_ticker += store.events(ticker=t, since=since, relevant_only=True, order="materiality", limit=12)
    terms = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9.&'-]+", question) if w.lower() not in STOPWORDS and w.upper() not in index.companies]
    by_text = store.search(" ".join(terms), limit=25) if terms else []
    fallback = store.events(since=now - timedelta(days=2), relevant_only=True, min_materiality=0.5, order="materiality", limit=20) if len(by_ticker) + len(by_text) < 8 else []
    seen, out = set(), []
    for group in (by_ticker, by_text, fallback):           # ticker-matched evidence first, then text matches, then recent material events
        group = sorted(group, key=lambda e: e["published"], reverse=True)
        for e in group:
            if e["id"] in seen or (e.get("event_type") == "commentary" and group is not by_ticker):
                continue
            seen.add(e["id"])
            out.append(e)
    return out[:k]


def evidence_block(events: list[dict]) -> str:
    lines = []
    for i, e in enumerate(events, 1):
        tk = ",".join(e.get("tickers") or []) or "-"
        rt = ",".join((e.get("tickers_rt") or [])[:5])
        rt = f" rt:{rt}" if rt else ""
        body = e.get("one_liner") or e.get("title")
        lines.append(f"[{i}] {_fmt_et(e['published'])} | {e.get('source_name')} | {tk}{rt} | {e.get('event_type')} dir {ARROW.get(int(e.get('direction') or 0), '0')} "
                     f"mat {float(e.get('materiality') or 0):.2f} | {body} | {e.get('url') or ''}")
    return "\n".join(lines)


def context(store: Store, index: EntityIndex, question: str, events: list[dict]) -> str:
    tickers = question_tickers(question, index)
    scores = {s["ticker"]: s for s in store.scores()}
    score_lines = []
    for t in (tickers or index.holdings)[:20]:
        s = scores.get(t)
        if s:
            score_lines.append(f"{t} ({'target %.1f%% exec' % (s['weight'] * 100) if s['is_holding'] else 'universe'}): 7d event score {s['event_score_7d']:+.2f}, "
                               f"{s['n_events_7d']} events, level {s['alert_level'] or '-'}, action {s['action']}, next earnings {s.get('next_earnings') or 'n/a'}")
    today = config.now_et().date()
    cal = store.calendar(today.isoformat(), (today + timedelta(days=7)).isoformat())
    cal_lines = [f"{c['date']} {c['time_et'] or ''} {c['name']} (importance {c['importance']})" for c in cal[:25]]
    run = config.run_info()
    return (f"Question: {question}\n\nToday: {config.now_et():%A %Y-%m-%d %H:%M ET}\nTarget list (top-15, canonical run {run.get('run_id')}, {run.get('validation_status')}; "
            f"weights are executable targets, there is no live position ledger): {', '.join(index.holdings)}\n\n"
            f"Evidence (most recent first):\n{evidence_block(events) or '(no matching events in the store)'}\n\n"
            f"Event scores:\n" + ("\n".join(score_lines) or "(none)") + "\n\nCalendar (next 7 days):\n" + ("\n".join(cal_lines) or "(none)"))


def answer(question: str, store: Store, index: EntityIndex, llm: LLM | None = None, stream: bool = False) -> str | Iterator[str]:
    events = retrieve(store, index, question)
    user = context(store, index, question, events)
    if llm is not None and llm.available:
        if stream:
            result = llm.stream(ASK_SYSTEM, user, effort="high", max_tokens=4000, operation="ask")
            if llm.available:
                return result
        else:
            result = llm.complete(ASK_SYSTEM, user, effort="high", max_tokens=4000, operation="ask")
            if result:
                return result
    reason = "LLM usage is blocked by its metering or budget control" if llm is not None and getattr(llm, "_metering_error", None) else "No LLM is configured"
    text = (reason + " (configure credentials with: python -m market_intel set-key), so here is the retrieved evidence without synthesis.\n\n"
            + user.split("Evidence (most recent first):\n", 1)[-1])
    return iter([text]) if stream else text
