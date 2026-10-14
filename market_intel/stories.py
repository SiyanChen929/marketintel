"""Story-level clustering of event copies (S6).

Many feeds carry the same story: wire copies, syndication, aggregator rewrites, follow-ups. Scoring every copy as a
new event multiplies the impact of one fact by the number of outlets. Here every event is attached to a *story*:

* candidates: stories whose first copy was published within ``WINDOW_H`` hours of the new event
* same story when the direct tickers overlap and either a salient bigram (e.g. "hugging face") is shared or the
  token Jaccard similarity is at least ``JACCARD_TICKER``; ticker-less items (macro) need the same event type and
  Jaccard at least ``JACCARD_MACRO``
* a *syndication family* groups outlets that republish each other (Dow Jones titles, aggregators, portals);
  ``independent_sources`` counts distinct families, aggregators and portals never count as independent confirmation
* status: retraction / correction / update copies mark the story, so the score can drop it or use the latest copy
"""
from __future__ import annotations

import re

WINDOW_H = 72
JACCARD_TICKER = 0.25
JACCARD_MACRO = 0.5
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "by", "its", "is", "are", "with", "from", "after", "amid", "says", "say", "said",
        "this", "that", "be", "may", "could", "would", "will", "into", "over", "up", "down", "vs", "how", "why", "what", "watch", "video", "report", "reports",
        "reportedly", "reported", "sources", "source", "news", "update", "updates", "live", "stock", "stocks", "shares", "share", "deal", "talks", "week",
        "today", "now", "new", "here", "s", "us", "it", "about", "than", "more", "most", "big", "top", "just", "still", "near", "nears", "close", "closes",
        "billion", "million", "bn", "mn", "b", "m", "per", "cent", "percent", "pct", "not", "no", "but", "or", "if", "so", "do", "does", "did", "has", "have",
        "had", "was", "were", "been", "being", "get", "gets", "got", "make", "makes", "made", "set", "sets", "see", "sees", "seen", "also", "again"}
GENERIC = STOP | {"acquire", "acquires", "acquisition", "acquiring", "buy", "buys", "buying", "sell", "sells", "selling", "merger", "merge", "agreement",
                  "agree", "agrees", "agreeing", "plan", "plans", "planning", "aims", "aim", "startup", "company", "companies", "ai", "chip", "chips",
                  "semiconductor", "semiconductors", "tech", "technology", "market", "markets", "investors", "analyst", "analysts", "earnings", "results",
                  "guidance", "quarter", "q1", "q2", "q3", "q4", "fiscal", "rises", "rise", "falls", "fall", "jumps", "jump", "gains", "gain", "drops", "drop",
                  "soars", "surges", "slides", "slips", "tumbles", "record", "high", "low", "price", "prices", "target", "rating", "upgrade", "downgrade"}
RETRACT_RE = re.compile(r"\b(retract(s|ed|ion)?|withdraw(s|n|al)? (the |its )?(report|story|claim))\b", re.I)
CORRECT_RE = re.compile(r"\b(correct(s|ed|ion)|corrects|clarif(y|ies|ication))\b", re.I)
UPDATE_RE = re.compile(r"\b(update[sd]?|latest|follow-?up|confirms?|confirmed|official)\b", re.I)

FAMILY_MAP = {
    "bloomberg": "bloomberg", "barron's": "dowjones", "barrons": "dowjones", "marketwatch": "dowjones", "wsj": "dowjones", "wall street journal": "dowjones",
    "dow jones": "dowjones", "reuters": "reuters", "cnbc": "cnbc", "new york times": "nyt", "nyt": "nyt", "financial times": "ft", "ft": "ft", "digitimes": "digitimes",
    "sec edgar": "sec", "federal reserve": "fed", "bls": "bls", "white house": "whitehouse", "business wire": "wire", "globenewswire": "wire", "pr newswire": "wire",
    "ad hoc news": "wire", "stock titan": "wire", "benzinga": "aggregator", "gurufocus": "aggregator", "marketbeat": "aggregator", "tipranks": "aggregator",
    "zacks": "aggregator", "zacks investment research": "aggregator", "investing": "aggregator", "citybiz": "aggregator", "finimize": "aggregator",
    "24/7 wall st.": "aggregator", "24/7 wall st": "aggregator", "motley fool": "aggregator", "the motley fool": "aggregator", "simply wall st": "aggregator",
    "simply wall st.": "aggregator", "investorplace": "aggregator", "insider monkey": "aggregator", "stocks down under": "aggregator", "tradingview": "aggregator",
    "stocktwits": "aggregator", "seeking alpha": "aggregator", "seeking alpha market currents": "aggregator", "marketscreener": "aggregator", "moomoo": "aggregator",
    "富途牛牛": "aggregator", "pluang": "aggregator", "finance.biggo": "aggregator", "thestreet": "aggregator", "sharewise": "aggregator", "barchart": "aggregator",
    "fxstreet": "aggregator", "finbold": "aggregator", "investopedia": "aggregator", "kiplinger": "aggregator", "nasdaq": "aggregator", "trefis": "aggregator",
    "the globe and mail": "globeandmail", "yahoo finance": "portal", "yahoo finance video": "portal", "msn": "portal", "aol": "portal", "google news": "portal",
}
NON_INDEPENDENT = {"aggregator", "portal"}


def family_of(source_name: str | None) -> str:
    s = (source_name or "").strip().lower()
    s = re.sub(r"\.(com|net|org|io)$", "", s)
    s = re.sub(r"\s+(top news|technology|earnings|investing|markets|business|market currents|top stories|financial|ai)$", "", s)
    if s in FAMILY_MAP:
        return FAMILY_MAP[s]
    for k, v in FAMILY_MAP.items():
        if s.startswith(k):
            return v
    return s or "unknown"


def tokens(text: str | None) -> list[str]:
    t = re.sub(r"[^a-z0-9一-鿿 ]+", " ", (text or "").lower())
    return [w for w in t.split() if w and w not in STOP]


def salient_bigrams(toks: list[str], exclude: set[str]) -> set[str]:
    out = set()
    for a, b in zip(toks, toks[1:]):
        if a in GENERIC or b in GENERIC or a in exclude or b in exclude or a.isdigit() or b.isdigit():
            continue
        out.add(f"{a} {b}")
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def signature(title: str, one_liner: str | None, tickers: list[str], aliases: dict[str, list[str]] | None = None) -> dict:
    """Comparable representation of one event copy."""
    toks = tokens(title) + tokens(one_liner)
    ex = {t.lower() for t in tickers}
    for t in tickers:
        for a in (aliases or {}).get(t, []):
            ex.update(tokens(a))
    return {"tokens": set(toks), "bigrams": salient_bigrams(tokens(title), ex) | salient_bigrams(tokens(one_liner), ex), "tickers": set(tickers)}


def same_story(new: dict, cand: dict, new_type: str | None, cand_type: str | None) -> tuple[bool, float]:
    """Decide whether two signatures describe the same story; returns (match, similarity)."""
    j = jaccard(new["tokens"], cand["tokens"])
    if new["tickers"] and cand["tickers"]:
        if not (new["tickers"] & cand["tickers"]):
            return False, j
        if new["bigrams"] & cand["bigrams"]:
            return True, max(j, JACCARD_TICKER)
        return j >= JACCARD_TICKER, j
    if not new["tickers"] and not cand["tickers"]:
        return (new_type == cand_type and (j >= JACCARD_MACRO or bool(new["bigrams"] & cand["bigrams"]) and j >= JACCARD_TICKER)), j
    return False, j


def copy_status(title: str | None) -> str:
    t = title or ""
    if RETRACT_RE.search(t):
        return "retracted"
    if CORRECT_RE.search(t):
        return "corrected"
    if UPDATE_RE.search(t):
        return "updated"
    return "active"


def story_status(statuses: list[str]) -> str:
    for s in ("retracted", "corrected", "updated"):
        if s in statuses:
            return s
    return "active"


def independent_sources(source_names: list[str]) -> tuple[int, dict[str, int]]:
    fam: dict[str, int] = {}
    for s in source_names:
        f = family_of(s)
        fam[f] = fam.get(f, 0) + 1
    n = len([f for f in fam if f not in NON_INDEPENDENT])
    return max(1, n), fam


def bonus(n_independent: int) -> float:
    """Independent confirmation adds at most 4% (2% per extra independent family, two families max)."""
    return 1.0 + 0.02 * min(max(n_independent - 1, 0), 2)
