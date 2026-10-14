"""Ticker dossiers (P1 W11): identity, price-target bridge, consensus with the earliest archived snapshot as prior, scores,
strategic thesis with kill criteria and watch items, recent events and analyst-authored scenarios.

Scenarios come from data/dossiers/<TICKER>.json ({"base": {"pt", "prob", "thesis"}, "bull": {...}, "bear": {...}, "author", "asof"}).
Model output is not a scenario: without an authored file the block is null and the downside is the worst of the model methods."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, research

DOSSIER_DIR = config.PROJECT_ROOT / "data" / "dossiers"
HORIZON, BENCHMARK = "12m", "SOXX"
SCENARIO_NOTE = "analyst-authored scenarios required; model output is not a scenario"
ACTUAL_NOTE = "not available from free data"
CONSENSUS_FIELDS = ("revenue_fy0", "revenue_fy1", "eps_fy0", "eps_fy1", "rev_growth_fy1", "eps_growth_fy1", "eps_rev_30d", "eps_rev_90d", "n_analysts", "rec_mean", "target_mean")
PILLARS = {"growth": "score_growth", "momentum": "score_momentum", "value": "score_value", "quality": "score_quality", "scarcity": "score_scarcity", "risk": "score_risk"}
EVENT_KEYS = ("id", "story_id", "published", "source_name", "event_type", "direction", "materiality", "alert_level", "action", "one_liner", "title", "url", "tickers", "read")


def _f(x):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return float(x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    return x


def _split(s) -> list[str]:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return []
    return [p.strip() for p in str(s).split("|") if p.strip()]


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ----------------------------------------------------------------------------- consensus prior
def _flatten_consensus(c: dict) -> dict:
    """Yahoo-shaped snapshot (revenue_estimate / earnings_estimate / price_targets ...) or an already-flat row -> the dossier fields."""
    if "revenue_estimate" in c or "earnings_estimate" in c:
        rev, eps, pt = c.get("revenue_estimate") or {}, c.get("earnings_estimate") or {}, c.get("price_targets") or {}
        trend = c.get("eps_trend") or {}

        def g(d, k, f="avg"):
            return _f((d.get(k) or {}).get(f))

        fy1 = trend.get("+1y") or {}
        cur = _f(fy1.get("current"))
        rev30, rev90 = None, None
        if cur:
            if _f(fy1.get("30daysAgo")):
                rev30 = cur / _f(fy1.get("30daysAgo")) - 1
            if _f(fy1.get("90daysAgo")):
                rev90 = cur / _f(fy1.get("90daysAgo")) - 1
        return {"revenue_fy0": g(rev, "0y"), "revenue_fy1": g(rev, "+1y"), "eps_fy0": g(eps, "0y"), "eps_fy1": g(eps, "+1y"), "rev_growth_fy1": g(rev, "+1y", "growth"),
                "eps_growth_fy1": g(eps, "+1y", "growth"), "eps_rev_30d": rev30, "eps_rev_90d": rev90, "n_analysts": _f(c.get("n_analysts")), "rec_mean": _f(c.get("recommendation_mean")),
                "target_mean": _f(pt.get("mean")), "price": _f(c.get("price")), "next_earnings": c.get("next_earnings")}
    return {k: _f(c.get(k)) for k in (*CONSENSUS_FIELDS, "price", "next_earnings")}


def prior_consensus(ticker: str, history: Path | None = None) -> dict | None:
    """Consensus of the earliest archived run that carries a consensus_snapshot.json, so revisions since then are visible."""
    runs = (history or research.HISTORY) / "runs"
    if not runs.exists():
        return None
    cands = []
    for d in runs.iterdir():
        p = d / "consensus_snapshot.json"
        if not (d.is_dir() and p.exists()):
            continue
        r = _read_json(d / "run.json") or {}
        cands.append((str(r.get("asof_session") or d.name[:8]), str(r.get("generated_at") or d.name), d.name, p))
    if not cands:
        return None
    asof, gen, run_id, p = sorted(cands)[0]
    snap = _read_json(p) or {}
    c = snap.get(ticker) if isinstance(snap, dict) else None
    if not isinstance(c, dict):
        return {"asof": asof, "run_id": run_id, "available": False, "note": f"{ticker} not in the earliest consensus snapshot ({run_id})"}
    return {"asof": asof, "run_id": run_id, "available": True, "source": str(p.relative_to(config.PROJECT_ROOT)) if p.is_relative_to(config.PROJECT_ROOT) else str(p), **_flatten_consensus(c)}


def _revisions(current: dict, prior: dict | None) -> dict | None:
    if not prior or not prior.get("available"):
        return None
    out = {}
    for k in ("revenue_fy0", "revenue_fy1", "eps_fy0", "eps_fy1", "target_mean"):
        a, b = current.get(k), prior.get(k)
        out[k] = (a / b - 1) if (a is not None and b not in (None, 0)) else None
    for k in ("n_analysts", "rec_mean"):
        a, b = current.get(k), prior.get(k)
        out[k] = (a - b) if (a is not None and b is not None) else None
    return {"since": prior.get("asof"), "pct_change": {k: v for k, v in out.items() if k not in ("n_analysts", "rec_mean")}, "delta": {k: out[k] for k in ("n_analysts", "rec_mean")}}


# ----------------------------------------------------------------------------- scenarios
def scenarios(ticker: str, price: float | None, directory: Path | None = None) -> dict | None:
    p = (directory or DOSSIER_DIR) / f"{ticker.upper()}.json"
    if not p.exists():
        return None
    s = _read_json(p)
    if not isinstance(s, dict):
        return None
    out = {"author": s.get("author"), "asof": s.get("asof"), "source": p.name, "horizon": s.get("horizon") or HORIZON}
    probs, exp = [], 0.0
    for k in ("base", "bull", "bear"):
        sc = s.get(k) or {}
        pt, prob = _f(sc.get("pt")), _f(sc.get("prob"))
        up = (pt / price - 1) if (pt and price) else None
        out[k] = {"pt": pt, "prob": prob, "thesis": sc.get("thesis"), "upside": up}
        if prob is not None and up is not None:
            probs.append(prob); exp += prob * up
    out["prob_sum"] = round(sum(probs), 4) if probs else None
    out["prob_weighted_upside"] = exp if probs and abs(sum(probs) - 1) < 0.02 else None
    out["note"] = None if probs and abs(sum(probs) - 1) < 0.02 else "scenario probabilities missing or do not sum to 1"
    return out


# ----------------------------------------------------------------------------- payload
def build(ticker: str, store=None, now: datetime | None = None, history: Path | None = None, scenario_dir: Path | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    t = ticker.upper().strip()
    uni = research.universe()
    row = uni.loc[t] if (not uni.empty and t in uni.index) else None

    def g(col, default=None):
        if row is None or col not in row.index:
            return default
        v = _f(row[col])
        return default if v is None else v

    s = research.summary()
    val = (s.get("config") or {}).get("valuation") or {}
    price = g("price")
    weights = {"dcf": _f(val.get("w_dcf")), "multiple": _f(val.get("w_multiple")), "target": _f(val.get("w_target"))}
    methods = {"dcf": {"fair_value": g("dcf_fv"), "upside": g("dcf_upside"), "weight": weights["dcf"], "label": "DCF"},
               "multiple": {"fair_value": g("mult_fv"), "upside": g("mult_upside"), "weight": weights["multiple"], "label": "EV/Sales regression"},
               "target": {"fair_value": g("target_mean"), "upside": g("target_upside"), "weight": weights["target"], "label": "sell-side target mean"}}
    avail = {k: m for k, m in methods.items() if m["fair_value"] is not None and m["weight"]}
    wsum = sum(m["weight"] for m in avail.values())
    recomputed = (sum(m["fair_value"] * m["weight"] for m in avail.values()) / wsum) if wsum else None
    blended = {"fair_value": g("fair_value"), "upside": g("upside"), "n_methods": g("n_methods"), "dispersion": g("upside_dispersion"), "recomputed_from_methods": recomputed,
               "matches_run": (abs(recomputed - g("fair_value")) / g("fair_value") < 0.02) if (recomputed and g("fair_value")) else None}
    current = {k: g(k) for k in CONSENSUS_FIELDS}
    prior = prior_consensus(t, history)
    hold, model = config.holdings(), config.holdings_model()
    st = research._csv(research.LATEST / "strategic_table.csv")
    strategic = None
    if st is not None and t in st.index:
        r = st.loc[t]
        gs = lambda c: _f(r[c]) if c in r.index else None
        strategic = {"strategic_score": gs("strategic_score"), "scarcity_strength": gs("scarcity_strength"), "risk_score": gs("risk_score"), "llm_risk": gs("llm_risk"),
                     "financial_risk": gs("financial_risk"), "pillar": gs("strategic_pillar"), "confidence": gs("strategic_confidence"), "thesis": gs("strategic_thesis"),
                     "kill_criteria": _split(gs("kill_criteria")), "watch_items": _split(gs("watch_items")), "risk_flags": _split(gs("risk_flags")),
                     "risk_capped": bool(gs("risk_capped") or False), "risk_gated_out": bool(gs("risk_gated_out") or False),
                     "strategic_asof": gs("strategic_asof"), "stale": bool(gs("strategic_stale") or False), "source": gs("strategic_source")}
    events = []
    if store is not None:
        for e in store.events(ticker=t, relevant_only=True, limit=10):
            d = {k: e.get(k) for k in EVENT_KEYS}
            d["published_et"] = _fmt_et(e.get("published"))
            events.append(d)
    sc = scenarios(t, price, scenario_dir)
    ups = [m["upside"] for m in methods.values() if m["upside"] is not None]
    if sc and sc.get("bear", {}).get("upside") is not None:
        downside, downside_basis = sc["bear"]["upside"], "bear scenario price target"
    else:
        downside, downside_basis = (min(ups) if ups else None), "worst of the model methods (DCF / multiple / target); no authored bear case"
    run = config.run_info()
    payload = {
        "ticker": t, "in_universe": row is not None, "run": run, "validation_status": run.get("validation_status"), "generated_at": now.isoformat(timespec="seconds"),
        "identity": {"name": g("name"), "segment": g("segment"), "sub_segment": g("sub_segment"), "country": g("country"), "is_adr": bool(g("is_adr", False)),
                     "financial_currency": g("financial_currency"), "market_cap": g("market_cap"), "cik": g("cik"), "description": g("description")},
        "horizon": HORIZON, "benchmark": BENCHMARK, "price": price, "price_asof": s.get("asof"),
        "target_list": {"in_target_list": t in hold, "weight": hold.get(t), "model_weight": model.get(t), "rank_eligible": g("rank_eligible"), "position": g("position")},
        "pt_bridge": {"price": price, "methods": methods, "blended": blended, "blend_weights": weights,
                      "basis": "blend weights from summary.config.valuation (w_dcf / w_multiple / w_target), renormalised over the methods available for the name"},
        "consensus": {"current": current, "prior": prior, "revisions": _revisions(current, prior), "actual": None, "surprise": None, "note": ACTUAL_NOTE,
                      "next_earnings": g("next_earnings"), "fy_end": g("fy_end")},
        "scores": {"composite": g("composite"), "alpha_score": g("alpha_score"), "rank_all": g("rank_all"), "rank_eligible": g("rank_eligible"), "eligible": bool(g("eligible", False)),
                   "pillars": {k: g(c) for k, c in PILLARS.items()}, "style": g("style"), "decision_score_status": g("decision_score_status")},
        "fundamentals": {k: g(k) for k in ("rev_growth_ntm", "rev_growth_q_yoy", "gross_margin", "op_margin", "fcf_margin", "roic", "net_debt_to_ebitda", "ev_sales_ntm", "pe_ntm", "beta",
                                            "ret_1m", "ret_3m", "ret_12m", "vs_200dma", "rel_6m_vs_soxx")},
        "strategic": strategic, "events": events, "n_events": len(events),
        "scenarios": sc if sc else None, "scenarios_note": None if sc else SCENARIO_NOTE,
        "expected_return": {"value": g("upside"), "basis": "blended fair-value upside (model)", "horizon": HORIZON},
        "downside": {"value": downside, "basis": downside_basis},
        "kill_criteria": strategic["kill_criteria"] if strategic else [], "watch_kpis": strategic["watch_items"] if strategic else [],
        "wording": "target-list names with executable target weights; no live position ledger; research tool, not investment advice",
    }
    return research.clean(payload)


def _fmt_et(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).astimezone(config.ET).strftime("%Y-%m-%d %H:%M ET")
    except ValueError:
        return iso
