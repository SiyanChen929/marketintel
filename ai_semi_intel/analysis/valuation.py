"""Valuation: 10-year fading-growth DCF, cross-sectional multiple regression,
analyst targets, and a blended fair value with upside.

Every assumption is read from ``CONFIG.valuation`` and the risk-free rate from FRED,
so the daily job re-prices the universe consistently when rates move.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.settings import CONFIG, ValuationConfig
from ..utils.io import log


# ------------------------------------------------------------------------------ DCF
def wacc_for(row: pd.Series, rf: float, cfg: ValuationConfig) -> float:
    beta = row.get("beta")
    beta = 1.2 if beta is None or np.isnan(beta) else 0.67 * float(beta) + 0.33      # Blume adjustment
    beta = float(np.clip(beta, cfg.beta_floor, cfg.beta_cap))
    ke = rf + beta * cfg.equity_risk_premium
    kd = (rf + 0.015) * (1 - cfg.tax_rate)
    e = row.get("market_cap") or 0.0
    d = row.get("total_debt") or 0.0
    d = 0.0 if np.isnan(d) else d
    if not e or np.isnan(e):
        return float(np.clip(ke, cfg.wacc_floor, cfg.wacc_cap))
    w = ke * e / (e + d) + kd * d / (e + d)
    return float(np.clip(w, cfg.wacc_floor, cfg.wacc_cap))


def _growth_path(g1: float, g_term: float, years: int) -> np.ndarray:
    """Linear fade from g1 in year 1 to g_term in the final explicit year."""
    return np.linspace(g1, g_term, years)


def _margin_path(m_now: float, m_target: float, years: int, converge: int = 5) -> np.ndarray:
    path = np.full(years, m_target, dtype=float)
    k = min(converge, years)
    path[:k] = np.linspace(m_now, m_target, k + 1)[1:]
    return path


def dcf_value(row: pd.Series, rf: float, cfg: ValuationConfig | None = None,
              segment_target_margin: float | None = None, g1_override: float | None = None,
              hold_margin: bool = False) -> dict:
    """Free-cash-flow-to-firm DCF. Returns dict with fair value/share, EV, WACC and inputs."""
    cfg = cfg or CONFIG.valuation
    out = {"dcf_fv": np.nan, "dcf_upside": np.nan, "wacc": np.nan, "dcf_g1": np.nan,
           "dcf_margin_now": np.nan, "dcf_margin_target": np.nan, "dcf_ev": np.nan, "dcf_tv_share": np.nan}
    rev = row.get("revenue_ntm")
    rev_ttm = row.get("revenue_ttm")
    g_ntm = row.get("rev_growth_ntm")
    if (rev is None or np.isnan(rev)) and rev_ttm and not np.isnan(rev_ttm):
        g_seed = g_ntm if g_ntm is not None and not np.isnan(g_ntm) else (row.get("rev_growth_q_yoy") or 0.05)
        rev = rev_ttm * (1 + float(np.clip(g_seed, -0.3, cfg.max_growth_input)))
    shares, price = row.get("shares"), row.get("price")
    if not rev or np.isnan(rev) or not shares or np.isnan(shares) or not price or np.isnan(price):
        return out
    # Growth beyond the consensus horizon: half the FY+1 consensus rate, capped, then a
    # linear fade to terminal growth by year `converge` (explicit horizon = fade_years).
    g_seed = row.get("rev_growth_fy1")
    if g_seed is None or np.isnan(g_seed):
        g_seed = g_ntm if g_ntm is not None and not np.isnan(g_ntm) else row.get("rev_cagr_3y")
    if g_seed is None or np.isnan(g_seed):
        g_seed = 0.06
    if g1_override is not None:
        g1 = float(g1_override)                                   # reverse-DCF search: not capped
    else:
        g1 = float(np.clip(cfg.post_consensus_growth_share * g_seed, -0.15, cfg.max_growth_input))
    years = cfg.fade_years
    converge = min(cfg.projection_years, years)
    growth = np.full(years, cfg.terminal_growth)
    growth[:converge] = np.linspace(g1, cfg.terminal_growth, converge)
    # Margins: start at current FCF margin; converge to a through-cycle target = average of the
    # current margin and the 5-year historical FCF margin (mean reversion for cyclicals).
    m_now = row.get("fcf_margin")
    opm = row.get("op_margin")
    nopat_m = opm * (1 - cfg.tax_rate) if opm is not None and not np.isnan(opm) else np.nan
    if m_now is None or np.isnan(m_now):
        m_now = nopat_m if not np.isnan(nopat_m) else 0.0
    m_now = float(np.clip(m_now, -0.60, 0.60))
    hist = row.get("fcf_margin_5y")
    hist = np.nan if hist is None else hist
    # History matters for cyclicals; for companies that have structurally scaled (3-yr revenue
    # CAGR > 25%) the sub-scale years are not a through-cycle guide, so history gets less weight.
    cagr3 = row.get("rev_cagr_3y")
    scaling = cagr3 is not None and not np.isnan(cagr3) and cagr3 > 0.25
    wc = 0.85 if scaling else cfg.margin_weight_current
    if hold_margin:
        m_target = m_now
    elif not np.isnan(hist):
        m_target = wc * m_now + (1 - wc) * float(np.clip(hist, -0.3, 0.5))
    elif not np.isnan(nopat_m):
        m_target = wc * m_now + (1 - wc) * nopat_m
    else:
        m_target = m_now
    if m_target < 0.03 and not hold_margin:    # structurally loss-making today: revert only part-way to segment norm
        seg = segment_target_margin if segment_target_margin is not None and not np.isnan(segment_target_margin) else 0.10
        m_target = max(m_target, 0.5 * min(seg, 0.20))
    m_target = float(np.clip(m_target, -0.05, 0.45))
    margins = _margin_path(m_now, m_target, years, converge=5)
    wacc = wacc_for(row, rf, cfg)
    revs, fcfs = [], []
    r = rev
    for i in range(years):
        if i > 0:
            r = r * (1 + growth[i])
        revs.append(r)
        fcfs.append(r * margins[i])
    disc = np.array([(1 + wacc) ** -(i + 1) for i in range(years)])
    pv_fcf = float(np.sum(np.array(fcfs) * disc))
    tv = fcfs[-1] * (1 + cfg.terminal_growth) / (wacc - cfg.terminal_growth)
    pv_tv = float(tv * disc[-1])
    ev = pv_fcf + pv_tv
    net_debt = row.get("net_debt") or 0.0
    net_debt = 0.0 if np.isnan(net_debt) else net_debt
    equity = ev - net_debt
    fv = equity / shares
    out.update(dcf_fv=float(fv), dcf_upside=float(fv / price - 1), wacc=wacc, dcf_g1=g1,
               dcf_margin_now=m_now, dcf_margin_target=m_target, dcf_ev=float(ev),
               dcf_tv_share=float(pv_tv / ev) if ev else np.nan)
    return out


def implied_growth(row: pd.Series, rf: float, cfg: ValuationConfig | None = None,
                   segment_target_margin: float | None = None) -> float:
    """Reverse DCF: the post-consensus growth (same fade, margins held at today's level)
    that justifies today's price."""
    cfg = cfg or CONFIG.valuation
    price = row.get("price")
    if not price or np.isnan(price):
        return np.nan
    lo, hi = -0.15, 0.80
    def f(g):
        return dcf_value(row, rf, cfg, segment_target_margin, g1_override=g, hold_margin=True)["dcf_fv"] - price
    try:
        flo, fhi = f(lo), f(hi)
        if np.isnan(flo) or np.isnan(fhi) or flo * fhi > 0:
            return np.nan if np.isnan(flo) else (lo if flo > 0 else hi)
        for _ in range(40):
            mid = (lo + hi) / 2
            fm = f(mid)
            if fm > 0:
                hi = mid
            else:
                lo = mid
        return float((lo + hi) / 2)
    except Exception:
        return np.nan


# ----------------------------------------------------------------- multiple regression
def multiple_regression(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Cross-sectional fair EV/Sales(NTM) = f(growth, FCF margin, gross margin).

    ln(EV/S) = a + b1*growth + b2*fcf_margin + b3*gross_margin. Residual < 0 means the
    stock trades below what its fundamentals justify vs peers.
    """
    d = df.copy()
    y = np.log(d["ev_sales_ntm"].where((d["ev_sales_ntm"] > 0.2) & (d["ev_sales_ntm"] < 80)))
    X = pd.DataFrame({
        "growth": d["rev_growth_ntm"].clip(-0.3, 1.0),
        "fcf_margin": d["fcf_margin"].clip(-0.5, 0.6),
        "gross_margin": d["gross_margin"].clip(0.0, 0.95),
    })
    mask = y.notna() & X.notna().all(axis=1)
    stats = {"n": int(mask.sum()), "r2": np.nan, "coef": {}}
    d["mult_fair_ev_sales"] = np.nan; d["mult_residual"] = np.nan; d["mult_fv"] = np.nan; d["mult_upside"] = np.nan
    if mask.sum() < 15:
        log.warning("multiple regression: too few observations (%d)", mask.sum())
        return d, stats
    A = np.column_stack([np.ones(mask.sum()), X[mask].values])
    coef, *_ = np.linalg.lstsq(A, y[mask].values, rcond=None)
    pred_all = np.column_stack([np.ones(len(X)), X.fillna(X[mask].median()).values]) @ coef
    resid = y - pred_all
    ss_res = float(np.nansum((y[mask] - pred_all[mask.values]) ** 2))
    ss_tot = float(np.nansum((y[mask] - y[mask].mean()) ** 2))
    stats["r2"] = 1 - ss_res / ss_tot if ss_tot else np.nan
    stats["coef"] = dict(zip(["intercept", "growth", "fcf_margin", "gross_margin"], map(float, coef)))
    d["mult_fair_ev_sales"] = np.exp(pred_all)
    d["mult_residual"] = resid
    have = X.notna().all(axis=1) & d["revenue_ntm"].notna() & d["shares"].notna()
    fair_ev = d["mult_fair_ev_sales"] * d["revenue_ntm"]
    nd = d["net_debt"].fillna(0.0)
    d.loc[have, "mult_fv"] = ((fair_ev - nd) / d["shares"])[have]
    d["mult_upside"] = d["mult_fv"] / d["price"] - 1
    return d, stats


# ------------------------------------------------------------------------ blend
def run_valuation(df: pd.DataFrame, rf: float, cfg: ValuationConfig | None = None) -> tuple[pd.DataFrame, dict]:
    cfg = cfg or CONFIG.valuation
    d = df.copy()
    seg_margin = d.groupby("segment")["fcf_margin"].median()
    dcf_rows = {}
    for t, row in d.iterrows():
        dcf_rows[t] = dcf_value(row, rf, cfg, seg_margin.get(row["segment"]))
        dcf_rows[t]["implied_growth"] = implied_growth(row, rf, cfg, seg_margin.get(row["segment"]))
    d = d.join(pd.DataFrame(dcf_rows).T.astype(float))
    d, reg = multiple_regression(d)
    # blended fair value: renormalise weights over the methods available
    w = pd.DataFrame({"dcf_fv": cfg.w_dcf, "mult_fv": cfg.w_multiple, "target_mean": cfg.w_target}, index=d.index)
    fvs = d[["dcf_fv", "mult_fv", "target_mean"]].copy()
    # sanity: drop absurd method outputs (>5x or <0.1x price) rather than let them dominate
    for c in fvs.columns:
        ratio = fvs[c] / d["price"]
        fvs.loc[(ratio > 5) | (ratio < 0.1), c] = np.nan
    avail = fvs.notna()
    w = w.where(avail, 0.0)
    wsum = w.sum(axis=1)
    d["fair_value"] = (fvs.fillna(0.0) * w).sum(axis=1) / wsum.replace(0, np.nan)
    d["upside"] = d["fair_value"] / d["price"] - 1
    ups = pd.DataFrame({"dcf": d["dcf_upside"], "mult": d["mult_upside"], "tgt": d["target_upside"]})
    d["upside_dispersion"] = ups.std(axis=1)
    d["n_methods"] = avail.sum(axis=1)
    meta = {"risk_free": rf, "regression": reg, "erp": cfg.equity_risk_premium,
            "terminal_growth": cfg.terminal_growth, "weights": {"dcf": cfg.w_dcf, "multiple": cfg.w_multiple, "target": cfg.w_target}}
    return d, meta
