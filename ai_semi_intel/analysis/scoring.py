"""Alpha, scarcity/risk decision scoring, and top-N selection.

Pillar design follows the point-in-time factor research (docs/FACTOR_RESEARCH.md, Sep 2026):
  Fundamental momentum (50%) - growth acceleration and latest-quarter growth were the only fundamental
      signals with a positive, stable IC 2012-2026; the consensus analogues (forward acceleration, EPS
      revisions) share the economics and will be validated from the daily archive.
  Price momentum (35%)       - volatility-scaled 12-1 momentum, segment momentum, relative strength.
  Valuation (15%)            - growth- and margin-adjusted multiple only (regression residual). Every value
      measure tested at or below zero IC; this small tilt reflects the "undervalued" mandate, not evidence.
  Quality is no longer an alpha pillar (IC ~ 0); related fields enter the risk-resilience overlay.
The live decision score has five direct pillars: growth 37.5%, momentum 26.25%, value 11.25%,
evidence-weighted scarcity 15%, and risk resilience 10%. Scarcity and risk are research priors and are
archived separately for forward validation.
Value z-scores are half universe-wide, half segment-relative.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.settings import CONFIG, ScreenConfig
from ..config.universe import SEGMENT_ORDER
from .risk import score_risk

PILLARS: dict[str, dict[str, int]] = {
    "fund_momentum": {"growth_accel": 1, "rev_growth_q_yoy": 1, "fwd_accel": 1, "eps_rev_90d": 1, "eps_rev_30d": 1},
    "price_momentum": {"mom_12_1_volscaled": 1, "industry_mom": 1, "rel_6m_vs_soxx": 1},
    "value": {"mult_residual": -1},
}
# legacy names kept for the dashboard/deck: value / growth / quality / momentum
PILLAR_ALIAS = {"score_growth": "score_fund_momentum", "score_momentum": "score_price_momentum"}
ALL_FEATURES = [f for p in PILLARS.values() for f in p]


def _winsor_z(s: pd.Series, pct: float) -> pd.Series:
    x = s.astype(float)
    if x.notna().sum() < 5:
        return pd.Series(np.nan, index=s.index)
    lo, hi = x.quantile(pct), x.quantile(1 - pct)
    x = x.clip(lo, hi)
    sd = x.std()
    return (x - x.mean()) / sd if sd and sd > 0 else pd.Series(0.0, index=s.index)


def _pillar(df: pd.DataFrame, feats: dict[str, int], pct: float, segment_relative: bool) -> tuple[pd.Series, pd.Series]:
    zs = []
    for f, sign in feats.items():
        if f not in df.columns:
            continue
        z = _winsor_z(df[f], pct) * sign
        if segment_relative:
            zseg = df.groupby("segment")[f].transform(lambda s: _winsor_z(s, pct) if s.notna().sum() >= 4 else pd.Series(np.nan, index=s.index)) * sign
            z = pd.concat([z, zseg], axis=1).mean(axis=1)
        zs.append(z.rename(f))
    Z = pd.concat(zs, axis=1)
    coverage = Z.notna().mean(axis=1)
    score = Z.mean(axis=1).clip(-3, 3)
    return score, coverage


def _event_overlay(max_age_hours: float):
    """Fresh market_intel overlay indexed by ticker, or None (missing, stale or package unavailable)."""
    try:
        from market_intel.scoring import load_overlay
        return load_overlay(max_age_hours)
    except Exception:
        return None


def score_universe(df: pd.DataFrame, cfg: ScreenConfig | None = None) -> pd.DataFrame:
    cfg = cfg or CONFIG.screen
    if not np.isclose(cfg.alpha_w_growth + cfg.alpha_w_momentum + cfg.alpha_w_value, 1.0):
        raise ValueError("alpha pillar weights must sum to 1")
    if not np.isclose(cfg.w_growth + cfg.w_momentum + cfg.w_value + cfg.w_scarcity + cfg.w_risk, 1.0):
        raise ValueError("decision-score weights must sum to 1")
    d = df.copy()
    cov = {}
    for name, feats in PILLARS.items():
        d[f"score_{name}"], cov[name] = _pillar(d, feats, cfg.winsor_pct, segment_relative=(name == "value"))
    d["feature_coverage"] = pd.DataFrame(cov).mean(axis=1)
    d["alpha_score"] = (cfg.alpha_w_growth * d["score_fund_momentum"].fillna(0)
                        + cfg.alpha_w_momentum * d["score_price_momentum"].fillna(0)
                        + cfg.alpha_w_value * d["score_value"].fillna(0))
    # Anchored 0-1 scarcity scores are converted to a bounded z-like scale and
    # shrunk by evidence confidence. Stale or missing inputs are neutral.
    scarcity_input = d["scarcity_raw"] if "scarcity_raw" in d else (d["strategic_score"] if "strategic_score" in d else pd.Series(np.nan, index=d.index))
    scarcity = pd.to_numeric(scarcity_input, errors="coerce")
    confidence = pd.to_numeric(d.get("strategic_confidence", pd.Series(np.nan, index=d.index)), errors="coerce").clip(0, 1)
    stale = d.get("strategic_stale", pd.Series(True, index=d.index)).fillna(True).astype(bool)
    effective_confidence = confidence.fillna(0.0).where(~stale, 0.0)
    d["scarcity_coverage"] = scarcity.notna().astype(float) * effective_confidence
    d["score_scarcity"] = (((scarcity - 0.5) / 0.25).clip(-2, 2) * effective_confidence).fillna(0.0)
    d = d.join(score_risk(d, cfg.winsor_pct))
    d["score_risk"] = d["score_risk_resilience"]
    d["composite"] = (cfg.w_growth * d["score_fund_momentum"].fillna(0)
                      + cfg.w_momentum * d["score_price_momentum"].fillna(0)
                      + cfg.w_value * d["score_value"].fillna(0)
                      + cfg.w_scarcity * d["score_scarcity"]
                      + cfg.w_risk * d["score_risk"])
    d["decision_score_status"] = "RESEARCH_PRIOR_NOT_VALIDATED"
    d["decision_coverage"] = (cfg.w_growth * cov["fund_momentum"].clip(0, 1)
                              + cfg.w_momentum * cov["price_momentum"].clip(0, 1)
                              + cfg.w_value * cov["value"].clip(0, 1)
                              + cfg.w_scarcity * d["scarcity_coverage"].clip(0, 1)
                              + cfg.w_risk * d["risk_coverage"].clip(0, 1))
    # optional real-time event tilt from market_intel (outputs/latest/event_overlay.csv); off unless w_event > 0
    d["score_event"] = 0.0
    if getattr(cfg, "w_event", 0.0) > 0:
        ov = _event_overlay(getattr(cfg, "event_overlay_max_age_hours", 36.0))
        if ov is not None and "event_score_7d" in ov.columns:
            ev = pd.to_numeric(ov["event_score_7d"], errors="coerce").reindex(d.index).fillna(0.0)
            d["score_event"] = ev.clip(-2, 2)               # bounded raw score: sparse overlays must not be z-scored away
            d["composite"] = d["composite"] + cfg.w_event * d["score_event"]
    # quality is reported (risk flags), not scored
    qz = pd.concat([_winsor_z(d[f], cfg.winsor_pct) * sgn for f, sgn in {"gross_margin": 1, "op_margin": 1, "fcf_margin": 1, "roic": 1, "net_debt_to_ebitda": -1, "sbc_pct": -1}.items() if f in d.columns], axis=1)
    d["score_quality"] = qz.mean(axis=1).clip(-3, 3)
    d["score_growth"] = d["score_fund_momentum"]; d["score_momentum"] = d["score_price_momentum"]
    # eligibility
    d["eligible"] = ((d["market_cap"] >= cfg.min_market_cap) & (d["feature_coverage"] >= cfg.min_feature_coverage)
                     & d["price"].notna() & d["ev_sales_ntm"].notna() & d["upside"].notna())
    # fail closed on FX: a non-USD reporter whose FX could not be fetched is not scoreable in USD terms
    if "fx_missing" in d.columns:
        miss = d["fx_missing"].fillna(False).astype(bool)
        d["eligible"] &= ~miss
        d["risk_flags"] = d["risk_flags"].where(~miss, d["risk_flags"].fillna("").astype(str).str.strip("|").radd("fx_unavailable|").str.strip("|"))
    # strategic risk gate (docs/STRATEGIC_RUBRIC.md): high LLM+financial risk excluded unless a genuine bottleneck (then weight-capped)
    if "risk_eligible" in d.columns:
        ok = d["risk_eligible"].fillna(True).astype(bool)
        d["risk_gated_out"] = d["eligible"] & ~ok
        d["eligible"] &= ok
    d["rank_all"] = d["composite"].rank(ascending=False, method="first").astype(int)
    d["rank_alpha"] = d["alpha_score"].rank(ascending=False, method="first").astype(int)
    d["rank_eligible"] = d.loc[d["eligible"], "composite"].rank(ascending=False, method="first")
    # style tag
    dv, dg, dm = d["score_value"].fillna(0), d["score_fund_momentum"].fillna(0), d["score_price_momentum"].fillna(0)
    d["style"] = np.where((dv > 0.5) & (dg < 0.5), "Undervalued", np.where(dg >= dm, "Accelerating growth", "Price momentum"))
    return d


def select_top(d: pd.DataFrame, cfg: ScreenConfig | None = None, prev: list[str] | None = None) -> pd.DataFrame:
    """Greedy pick by composite with a per-segment cap for value-chain diversification.
    With cfg.hysteresis > 1, names in `prev` (current holdings) are retained while their eligible rank
    stays within top_n * hysteresis, which cuts churn between daily runs (docs/FACTOR_RESEARCH.md s.8)."""
    cfg = cfg or CONFIG.screen
    cand = d[d["eligible"]].sort_values("composite", ascending=False)
    picks, seg_count = [], {}
    k = getattr(cfg, "hysteresis", 1.0)
    if prev and k > 1.0:
        rank = {t: i + 1 for i, t in enumerate(cand.index)}
        for t in prev:
            if t in rank and rank[t] <= cfg.top_n * k and seg_count.get(cand.loc[t, "segment"], 0) < cfg.max_per_segment:
                picks.append(t); seg_count[cand.loc[t, "segment"]] = seg_count.get(cand.loc[t, "segment"], 0) + 1
                if len(picks) >= cfg.top_n:
                    break
    for t, row in cand.iterrows():
        if len(picks) >= cfg.top_n:
            break
        if t in picks:
            continue
        seg = row["segment"]
        if seg_count.get(seg, 0) >= cfg.max_per_segment:
            continue
        picks.append(t)
        seg_count[seg] = seg_count.get(seg, 0) + 1
        if len(picks) >= cfg.top_n:
            break
    top = d.loc[picks].copy()
    top["rank"] = range(1, len(top) + 1)
    top["segment_order"] = top["segment"].map(SEGMENT_ORDER.index)
    return top


def selection_funnel(d: pd.DataFrame, cfg: ScreenConfig | None = None) -> list[dict]:
    cfg = cfg or CONFIG.screen
    steps = [("AI & semiconductor value-chain universe", len(d))]
    m = d["market_cap"] >= cfg.min_market_cap
    steps.append((f"Market cap >= ${cfg.min_market_cap/1e9:.0f}B", int(m.sum())))
    m &= d["feature_coverage"] >= cfg.min_feature_coverage
    steps.append((f"Data coverage >= {cfg.min_feature_coverage:.0%}", int(m.sum())))
    m &= d["upside"].notna() & d["ev_sales_ntm"].notna()
    steps.append(("Valuation computable (consensus + DCF)", int(m.sum())))
    m &= d["composite"] > 0
    steps.append(("Composite score above universe mean", int(m.sum())))
    steps.append((f"Top {cfg.top_n} (max {cfg.max_per_segment}/segment)", cfg.top_n))
    return [{"step": s, "n": n} for s, n in steps]
