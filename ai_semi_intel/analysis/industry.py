"""Industry structure & competitive positioning derived from the feature table.

Segment economics (size, growth, margins, valuation), concentration (HHI on
revenue), and per-company positioning (revenue share, margin rank, R&D intensity,
pricing-power proxy) that feed the deck narrative and dashboard.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.universe import SEGMENT_ORDER


def segment_table(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby("segment")
    def hhi(s):
        s = s.dropna(); s = s[s > 0]
        if s.empty:
            return np.nan
        sh = s / s.sum()
        return float((sh ** 2).sum() * 10000)
    t = pd.DataFrame({
        "n": g.size(),
        "market_cap_bn": g["market_cap"].sum() / 1e9,
        "revenue_ttm_bn": g["revenue_ttm"].sum() / 1e9,
        "med_rev_growth_ntm": g["rev_growth_ntm"].median(),
        "med_gross_margin": g["gross_margin"].median(),
        "med_op_margin": g["op_margin"].median(),
        "med_fcf_margin": g["fcf_margin"].median(),
        "med_ev_sales_ntm": g["ev_sales_ntm"].median(),
        "med_pe_ntm": g["pe_ntm"].median(),
        "med_upside": g["upside"].median(),
        "med_composite": g["composite"].median() if "composite" in d else np.nan,
        "med_eps_rev_90d": g["eps_rev_90d"].median(),
        "hhi_revenue": g["revenue_ttm"].apply(hhi),
        "top3_share": g["market_cap"].apply(lambda s: float(s.nlargest(3).sum() / s.sum()) if s.sum() else np.nan),
        "ret_6m_median": g["ret_6m"].median(),
    })
    t["structure"] = pd.cut(t["hhi_revenue"], [0, 1500, 2500, 10001], labels=["Fragmented", "Moderately concentrated", "Concentrated"])
    return t.reindex(SEGMENT_ORDER)


def positioning(d: pd.DataFrame) -> pd.DataFrame:
    """Per-company competitive-position metrics relative to its segment."""
    p = pd.DataFrame(index=d.index)
    seg_rev = d.groupby("segment")["revenue_ttm"].transform("sum")
    p["rev_share_in_segment"] = d["revenue_ttm"] / seg_rev
    p["gm_rank_in_segment"] = d.groupby("segment")["gross_margin"].rank(ascending=False)
    p["growth_rank_in_segment"] = d.groupby("segment")["rev_growth_ntm"].rank(ascending=False)
    p["gm_vs_segment"] = d["gross_margin"] - d.groupby("segment")["gross_margin"].transform("median")
    p["growth_vs_segment"] = d["rev_growth_ntm"] - d.groupby("segment")["rev_growth_ntm"].transform("median")
    p["ev_sales_vs_segment"] = d["ev_sales_ntm"] / d.groupby("segment")["ev_sales_ntm"].transform("median") - 1
    p["rnd_intensity"] = d["rnd_pct"]
    p["pricing_power_proxy"] = d["gm_change_3y"]
    p["operating_leverage"] = d["opm_change_3y"]
    # moat score: margin premium + scale + R&D, all within segment
    z = lambda s: (s - s.mean()) / s.std() if s.std() else s * 0
    p["moat_score"] = (d.groupby("segment")["gross_margin"].transform(z).fillna(0)
                       + np.log1p(p["rev_share_in_segment"].fillna(0) * 100) / 2
                       + d.groupby("segment")["roic"].transform(z).fillna(0)).clip(-3, 4)
    # 2x2 positioning
    p["position"] = np.select(
        [(p["growth_vs_segment"] >= 0) & (p["gm_vs_segment"] >= 0), (p["growth_vs_segment"] >= 0) & (p["gm_vs_segment"] < 0),
         (p["growth_vs_segment"] < 0) & (p["gm_vs_segment"] >= 0)],
        ["Leader (growth + margin)", "Challenger (growth, thin margin)", "Franchise (margin, slower)"], default="Laggard")
    return p
