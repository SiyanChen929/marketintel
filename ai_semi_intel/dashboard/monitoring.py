"""Daily monitoring: run-over-run changes and KPI alerts for the investor dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

ALERT_RULES = {
    "price_move_1d": 0.05,       # |1-day move| above this
    "eps_rev_30d": 0.05,         # |30-day EPS revision| above this
    "upside_floor": 0.10,        # thesis check: blended upside below this
    "upside_ceiling": 0.60,      # unusually high upside -> verify inputs
    "earnings_days": 14,         # earnings inside this window
    "weight_change_pp": 0.03,    # weight change vs previous run
    "drawdown_1y": -0.30,        # 1-year drawdown deeper than this
}


def compare_runs(cur: dict, prev: dict | None) -> dict:
    """Run-over-run changes on the canonical executable weights.

    cur/prev: {'date', 'run_id', 'top': [..], 'weights': {t: w}, 'cash': c, 'composite', 'upside', 'rank'}.
    Turnover = 0.5 x sum |delta weight| over assets *and* cash, so both sides reconcile to 100%.
    """
    out = {"prev_date": None, "prev_run_id": None, "entries": [], "exits": [], "rank_changes": {}, "weight_changes": {}, "turnover": 0.0, "cash_change": 0.0}
    if not prev:
        return out
    out["prev_date"], out["prev_run_id"] = prev.get("date"), prev.get("run_id")
    cur_set, prev_set = set(cur["top"]), set(prev.get("top", []))
    out["entries"] = sorted(cur_set - prev_set)
    out["exits"] = sorted(prev_set - cur_set)
    for t in cur_set & prev_set:
        out["rank_changes"][t] = int(prev["rank"].get(t, 0)) - int(cur["rank"].get(t, 0))
    allt = cur_set | prev_set
    wc = {t: float(cur["weights"].get(t, 0.0)) - float(prev.get("weights", {}).get(t, 0.0)) for t in allt}
    out["weight_changes"] = {t: v for t, v in sorted(wc.items(), key=lambda kv: -abs(kv[1]))}
    out["cash_change"] = float(cur.get("cash", 0.0)) - float(prev.get("cash", 0.0))
    out["turnover"] = float(0.5 * (sum(abs(v) for v in wc.values()) + abs(out["cash_change"])))
    return out


def build_alerts(top: pd.DataFrame, weights: pd.Series, changes: dict, px: pd.DataFrame, asof: datetime) -> list[dict]:
    alerts: list[dict] = []
    def add(sev, t, kind, msg):
        alerts.append({"severity": sev, "ticker": t, "type": kind, "message": msg})
    for t in changes.get("entries", []):
        add("info", t, "entry", f"{t} entered the top 15 (was outside on {changes.get('prev_date')})")
    for t in changes.get("exits", []):
        add("warning", t, "exit", f"{t} dropped out of the top 15")
    for t, dw in changes.get("weight_changes", {}).items():
        if abs(dw) >= ALERT_RULES["weight_change_pp"] and t in top.index:
            add("info", t, "rebalance", f"{t} executable target weight moved {dw:+.1%} vs the approved prior run")
    for t, r in top.iterrows():
        if t in px.columns:
            s = px[t].dropna()
            if len(s) > 2:
                d1 = s.iloc[-1] / s.iloc[-2] - 1
                if abs(d1) >= ALERT_RULES["price_move_1d"]:
                    add("warning" if abs(d1) < 0.10 else "critical", t, "price", f"{t} moved {d1:+.1%} on the last session")
                s1 = s.tail(252); dd = s1.iloc[-1] / s1.max() - 1
                if dd <= ALERT_RULES["drawdown_1y"]:
                    add("warning", t, "drawdown", f"{t} is {dd:.0%} below its 1-year high")
        rev = r.get("eps_rev_30d")
        if rev is not None and not np.isnan(rev) and abs(rev) >= ALERT_RULES["eps_rev_30d"]:
            add("info" if rev > 0 else "warning", t, "revision", f"{t} consensus EPS revised {rev:+.1%} in 30 days")
        up = r.get("upside")
        if up is not None and not np.isnan(up):
            if up < ALERT_RULES["upside_floor"]:
                add("warning", t, "valuation", f"{t} blended upside only {up:+.0%}: analyst review of the valuation inputs")
            elif up > ALERT_RULES["upside_ceiling"]:
                add("info", t, "valuation", f"{t} blended upside {up:+.0%}: verify inputs (consensus/FX) - analyst review")
        ne = r.get("next_earnings")
        if ne:
            try:
                d = pd.Timestamp(ne).tz_localize(None)
                days = (d - pd.Timestamp(asof)).days
                if 0 <= days <= ALERT_RULES["earnings_days"]:
                    add("info", t, "earnings", f"{t} reports in {days} days ({d.date()})")
            except Exception:
                pass
        if int(r.get("filings_30d") or 0) > 0 and r.get("latest_10q_date"):
            try:
                if (pd.Timestamp(asof) - pd.Timestamp(r["latest_10q_date"])).days <= 7:
                    add("info", t, "filing", f"{t} filed a 10-Q on {r['latest_10q_date']} - refresh model inputs")
            except Exception:
                pass
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda a: (order[a["severity"]], a["ticker"]))


def kpi_table(top: pd.DataFrame, weights: pd.Series, px: pd.DataFrame, weights_model: pd.Series | None = None) -> pd.DataFrame:
    """Per-stock KPI row for the dashboard. ``weights`` are the executable target weights; ``weights_model`` the fully-invested construction."""
    rows = []
    for t, r in top.iterrows():
        s = px[t].dropna() if t in px.columns else pd.Series(dtype=float)
        d1 = s.iloc[-1] / s.iloc[-2] - 1 if len(s) > 2 else np.nan
        w1 = s.iloc[-1] / s.iloc[-6] - 1 if len(s) > 6 else np.nan
        rows.append({"ticker": t, "name": r["name"], "segment": r["segment"], "style": r["style"], "rank": int(r["rank"]),
                     "weight": float(weights.get(t, 0.0)), "weight_model": float(weights_model.get(t, 0.0)) if weights_model is not None else np.nan, "price": r["price"], "ret_1d": d1, "ret_1w": w1, "ret_1m": r.get("ret_1m"),
                     "fair_value": r["fair_value"], "upside": r["upside"], "composite": r["composite"],
                     "score_growth": r.get("score_growth"), "score_momentum": r.get("score_momentum"),
                     "score_value": r.get("score_value"), "score_scarcity": r.get("score_scarcity"),
                     "score_risk": r.get("score_risk"), "alpha_score": r.get("alpha_score"),
                     "score_risk_resilience": r.get("score_risk_resilience"), "risk_flags": r.get("risk_flags"),
                     "ev_sales_ntm": r["ev_sales_ntm"], "pe_ntm": r["pe_ntm"], "rev_growth_ntm": r["rev_growth_ntm"],
                     "fcf_margin": r["fcf_margin"], "eps_rev_30d": r["eps_rev_30d"], "eps_rev_90d": r["eps_rev_90d"],
                     "vs_200dma": r.get("vs_200dma"), "next_earnings": r.get("next_earnings"), "filings_30d": r.get("filings_30d"),
                     "latest_10k_url": r.get("latest_10k_url"), "market_cap": r["market_cap"],
                     "scarcity_strength": r.get("scarcity_strength"), "risk_score": r.get("risk_score"), "strategic_pillar": r.get("strategic_pillar")})
    return pd.DataFrame(rows).set_index("ticker")


def event_alerts(top: pd.DataFrame, max_age_hours: float = 36.0) -> list[dict]:
    """Alerts from the real-time market-intelligence overlay (market_intel package), when it is fresh."""
    try:
        from market_intel.scoring import load_overlay
        ov = load_overlay(max_age_hours)
    except Exception:
        return []
    if ov is None:
        return []
    out = []
    for t in top.index:
        if t not in ov.index:
            continue
        r = ov.loc[t]
        lvl = r.get("alert_level")
        score = float(r.get("event_score_7d") or 0.0)
        if isinstance(lvl, str) and lvl in ("warning", "critical"):
            out.append({"severity": lvl, "ticker": t, "type": "event",
                        "message": f"{t} event {lvl}: {str(r.get('last_event_title'))[:140]} (7d event score {score:+.2f}; action: {r.get('action')})"})
        elif score <= -0.5:
            out.append({"severity": "warning", "ticker": t, "type": "event",
                        "message": f"{t} negative news flow: 7d event score {score:+.2f} over {int(r.get('n_events_7d') or 0)} events"})
    return out
