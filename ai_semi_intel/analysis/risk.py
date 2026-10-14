"""Cross-sectional risk-resilience scoring.

The output is oriented so higher is safer.  It complements the return-oriented
alpha score; it is not presented as an alpha factor.  Each sub-score is shrunk
toward neutral when inputs are missing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


RISK_PILLARS: dict[str, tuple[float, dict[str, int]]] = {
    "market_resilience": (
        0.26,
        {"vol_1y": -1, "max_dd_1y": 1, "beta": -1},
    ),
    "financial_resilience": (
        0.26,
        {
            "net_debt_to_ebitda": -1,
            "fcf_margin": 1,
            "roic": 1,
            "op_margin_5y": 1,
            "sbc_pct": -1,
        },
    ),
    "valuation_resilience": (
        0.23,
        {
            "_log_ev_sales_ntm": -1,
            "_log_pe_ntm": -1,
            "dcf_tv_share": -1,
            "upside_dispersion": -1,
        },
    ),
    # LLM-assessed, evidence-logged risk (competitive threat, geopolitics/regulation, execution, customer
    # concentration), inverted so higher = safer; missing when no assessment exists (neutral, lowers coverage)
    "strategic_resilience": (
        0.25,
        {"llm_resilience": 1},
    ),
}


def _winsor_z(s: pd.Series, pct: float) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() < 5:
        return pd.Series(np.nan, index=s.index, dtype=float)
    x = x.clip(x.quantile(pct), x.quantile(1 - pct))
    sd = x.std()
    return (x - x.mean()) / sd if sd and sd > 0 else pd.Series(0.0, index=s.index)


def _score_pillar(d: pd.DataFrame, features: dict[str, int], pct: float) -> tuple[pd.Series, pd.Series]:
    zscores = []
    for feature, sign in features.items():
        if feature in d:
            zscores.append((_winsor_z(d[feature], pct) * sign).rename(feature))
    if not zscores:
        empty = pd.Series(0.0, index=d.index)
        return empty, empty
    z = pd.concat(zscores, axis=1)
    coverage = z.notna().sum(axis=1) / len(features)
    # Missing observations are neutral and reduce confidence rather than improve a score.
    score = z.mean(axis=1).fillna(0.0).clip(-3, 3) * coverage
    return score, coverage


def _risk_flags(d: pd.DataFrame) -> pd.Series:
    flags = pd.Series("", index=d.index, dtype=object)

    def col(name: str) -> pd.Series:
        return pd.to_numeric(d[name], errors="coerce") if name in d else pd.Series(np.nan, index=d.index)

    def add(mask: pd.Series, label: str) -> None:
        nonlocal flags
        mask = mask.fillna(False)
        flags.loc[mask] = flags.loc[mask].map(lambda x: f"{x}|{label}" if x else label)

    add(col("vol_1y") > 0.60, "high_volatility")
    add(col("max_dd_1y") < -0.35, "deep_drawdown")
    add(col("beta") > 1.75, "high_beta")
    add(col("net_debt_to_ebitda") > 3.0, "high_leverage")
    add(col("upside_dispersion") > 0.50, "valuation_dispersion")
    evs = col("ev_sales_ntm")
    pe = col("pe_ntm")
    add((evs > 15) | (pe > 45), "valuation_crowding")
    return flags


def score_risk(d: pd.DataFrame, winsor_pct: float = 0.05) -> pd.DataFrame:
    """Return market, financial, valuation and combined resilience scores."""
    x = d.copy()
    evs = pd.to_numeric(x["ev_sales_ntm"], errors="coerce") if "ev_sales_ntm" in x else pd.Series(np.nan, index=x.index)
    pe = pd.to_numeric(x["pe_ntm"], errors="coerce") if "pe_ntm" in x else pd.Series(np.nan, index=x.index)
    x["_log_ev_sales_ntm"] = np.log(evs.where(evs > 0))
    x["_log_pe_ntm"] = np.log(pe.where(pe > 0))

    out = pd.DataFrame(index=x.index)
    weighted = pd.Series(0.0, index=x.index)
    weighted_coverage = pd.Series(0.0, index=x.index)
    for name, (weight, features) in RISK_PILLARS.items():
        score, coverage = _score_pillar(x, features, winsor_pct)
        out[f"score_{name}"] = score
        out[f"coverage_{name}"] = coverage
        weighted += weight * score
        weighted_coverage += weight * coverage
    out["risk_coverage"] = weighted_coverage.clip(0, 1)
    out["score_risk_resilience"] = weighted.clip(-2, 2)
    out["risk_flags"] = _risk_flags(x)
    out["risk_flag_count"] = out["risk_flags"].map(lambda value: 0 if not value else len(value.split("|")))
    return out
