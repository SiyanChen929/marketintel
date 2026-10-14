"""Factor research on the point-in-time panel.

Discipline against data mining:
  * the candidate list, signs and rationales are fixed here before evaluation;
  * every candidate is evaluated identically (rank IC at 1/3/6/12 months, Newey-West t);
  * robustness = same sign in both halves of the sample, after excluding the ten biggest
    winners (survivorship proxy), and after size-neutralisation;
  * selection rules are stated in advance (see SELECTION);
  * the final composite is confirmed with a *nested* test: features selected on the first
    half only, evaluated on the second half only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .backtest import HORIZONS, _wz, add_mult_residual, run_backtest, select_top
from .ic_tests import _hac_t

# name, family, sign (+1 higher is better), rationale
CANDIDATES = [
    ("ev_sales", "value", -1, "Raw EV/TTM sales"),
    ("earnings_yield", "value", 1, "TTM net income / market cap"),
    ("fcf_yield", "value", 1, "TTM FCF / market cap"),
    ("ev_sales_to_growth", "value", -1, "EV/S per point of trailing growth"),
    ("mult_residual", "value", -1, "EV/S residual after growth & margins (cross-sectional regression)"),
    ("ev_sales_own_hist", "value", -1, "ln EV/S minus own trailing 36-month mean (time-series value)"),
    ("garp", "value", 1, "rank(cheap vs fundamentals) x rank(growth acceleration)"),
    ("rev_growth_ttm", "growth", 1, "TTM revenue growth"),
    ("rev_q_yoy", "growth", 1, "Latest quarter revenue growth YoY"),
    ("growth_accel", "growth", 1, "Quarter YoY growth minus TTM growth"),
    ("rev_cagr_3y", "growth", 1, "3-year revenue CAGR"),
    ("opi_growth", "growth", 1, "TTM operating income growth"),
    ("ni_growth", "growth", 1, "TTM net income growth"),
    ("opm_change_1y", "growth", 1, "Operating margin change YoY (margin momentum)"),
    ("gm_change_1y", "growth", 1, "Gross margin change YoY"),
    ("gross_margin", "quality", 1, "Gross margin level"),
    ("op_margin", "quality", 1, "Operating margin level"),
    ("fcf_margin", "quality", 1, "FCF margin level"),
    ("roic", "quality", 1, "After-tax operating return on capital"),
    ("net_debt_to_opinc", "quality", -1, "Leverage"),
    ("sbc_pct", "quality", -1, "Stock compensation / revenue"),
    ("rule_of_40", "quality", 1, "Growth + FCF margin"),
    ("opm_change_3y", "quality", 1, "3-year operating margin change"),
    ("accruals", "quality", -1, "(NI - OCF) / assets, Sloan accruals"),
    ("asset_growth", "quality", -1, "Total asset growth (investment factor)"),
    ("capex_pct", "quality", -1, "Capex / revenue (capital intensity)"),
    ("mom_12_1", "momentum", 1, "12-1 month price momentum"),
    ("mom_6_1", "momentum", 1, "6-1 month price momentum"),
    ("rel_6m_vs_soxx", "momentum", 1, "6-month return relative to SOXX"),
    ("vs_200dma", "momentum", 1, "Price vs 200-day average"),
    ("high_52w_prox", "momentum", 1, "Price / 52-week high"),
    ("mom_12_1_volscaled", "momentum", 1, "12-1 momentum scaled by volatility"),
    ("ret_1m", "momentum", -1, "1-month reversal"),
    ("industry_mom", "momentum", 1, "Segment average 6-month return"),
    ("log_mcap", "size", -1, "Size (small beats large)"),
    ("vol_1y", "risk", -1, "Low volatility"),
]
SELECTION = {"strict": {"t_min": 2.0, "both_halves": True, "ex_winners": True},
             "lenient": {"t_min": 1.5, "both_halves": True, "ex_winners": False}}
SPLIT_DATE = pd.Timestamp("2019-06-30")
MIN_MCAP = 1e9


def derive(panel: pd.DataFrame) -> pd.DataFrame:
    """Add derived candidates that need the cross-section or the ticker's own history."""
    d = panel.sort_values(["ticker", "date"]).copy()
    parts = []
    for dt, cs in d.groupby("date"):
        cs = add_mult_residual(cs.set_index("ticker"))
        seg_mom = cs.groupby("segment")["ret_6m"].transform("mean")
        cs["industry_mom"] = seg_mom
        r_cheap = (-cs["mult_residual"]).rank(pct=True); r_acc = cs["growth_accel"].rank(pct=True)
        cs["garp"] = r_cheap * r_acc
        parts.append(cs.reset_index())
    d = pd.concat(parts, ignore_index=True).sort_values(["ticker", "date"])
    lev = np.log(d["ev_sales"].where(d["ev_sales"] > 0))
    d["_lev"] = lev
    d["ev_sales_own_hist"] = d["_lev"] - d.groupby("ticker")["_lev"].transform(lambda s: s.shift(1).rolling(36, min_periods=12).mean())
    d["log_mcap"] = np.log(d["market_cap"].where(d["market_cap"] > 0))
    d["eligible"] = (d["market_cap"].fillna(0) >= MIN_MCAP) & d["ev_sales"].notna()
    return d.drop(columns=["_lev"])


def _ic_series(d: pd.DataFrame, fr: pd.DataFrame, col: str, horizon: str, sign: int = 1, neutralise: str | None = None,
               exclude: set[str] | None = None) -> pd.Series:
    frh = fr[fr["horizon"] == horizon].set_index("date")
    out = {}
    for dt, cs in d.groupby("date"):
        if dt not in frh.index:
            continue
        cs = cs[cs["eligible"]].set_index("ticker")
        if exclude:
            cs = cs[~cs.index.isin(exclude)]
        r = pd.to_numeric(frh.loc[dt].reindex(cs.index), errors="coerce")
        x = cs[col].astype(float) * sign
        if neutralise and neutralise in cs.columns:
            z = cs[neutralise].astype(float)
            m0 = x.notna() & z.notna()
            if m0.sum() >= 20:
                xr, zr = x[m0].rank(), z[m0].rank()
                b = np.cov(xr, zr)[0, 1] / zr.var()
                x = x.copy(); x[m0] = xr - b * zr
        m = r.notna() & x.notna()
        if m.sum() >= 20:
            out[dt] = spearmanr(x[m], r[m])[0]
    return pd.Series(out).sort_index()


def top_winners(d: pd.DataFrame, fr: pd.DataFrame, n: int = 10) -> set[str]:
    """Tickers with the largest cumulative 1-month forward returns over the sample (survivorship proxy)."""
    fr1 = fr[fr["horizon"] == "1m"].set_index("date")
    cols = [c for c in fr1.columns if c not in ("horizon",) and c in set(d["ticker"])]
    cum = (1 + fr1[cols].apply(pd.to_numeric, errors="coerce").fillna(0)).prod()
    return set(cum.sort_values(ascending=False).head(n).index)


def evaluate(d: pd.DataFrame, fr: pd.DataFrame) -> pd.DataFrame:
    winners = top_winners(d, fr)
    rows = []
    for name, fam, sign, why in CANDIDATES:
        if name not in d.columns:
            continue
        row = {"feature": name, "family": fam, "sign": sign, "rationale": why, "coverage": float(d.loc[d["eligible"], name].notna().mean())}
        for h, k in HORIZONS.items():
            ic = _ic_series(d, fr, name, h, sign)
            row[f"ic_{h}"] = ic.mean(); row[f"t_{h}"] = _hac_t(ic, k - 1)
            if h == "3m":
                row["pct_pos_3m"] = float((ic > 0).mean()); row["n_3m"] = int(len(ic))
                row["ic_h1"] = ic[ic.index <= SPLIT_DATE].mean(); row["ic_h2"] = ic[ic.index > SPLIT_DATE].mean()
                row["t_h1"] = _hac_t(ic[ic.index <= SPLIT_DATE], 2); row["t_h2"] = _hac_t(ic[ic.index > SPLIT_DATE], 2)
                icx = _ic_series(d, fr, name, h, sign, exclude=winners); row["ic_ex_winners"] = icx.mean(); row["t_ex_winners"] = _hac_t(icx, 2)
                icn = _ic_series(d, fr, name, h, sign, neutralise="log_mcap"); row["ic_size_neutral"] = icn.mean()
        rows.append(row)
    z = pd.DataFrame(rows)
    for rule, cfg in SELECTION.items():
        ok = (z["ic_3m"] > 0) & (z["t_3m"] >= cfg["t_min"])
        if cfg["both_halves"]:
            ok &= (z["ic_h1"] > 0) & (z["ic_h2"] > 0)
        if cfg["ex_winners"]:
            ok &= z["ic_ex_winners"] > 0
        z[f"selected_{rule}"] = ok
    return z.sort_values("t_3m", ascending=False)


def select_features(z: pd.DataFrame, rule: str = "strict", stats_prefix: str = "") -> list[tuple[str, int, str]]:
    col = f"selected_{rule}"
    sel = z[z[col]]
    return [(r["feature"], int(r["sign"]), r["family"]) for _, r in sel.iterrows() if r["family"] not in ("size",)]


def build_composite(d: pd.DataFrame, features: list[tuple[str, int, str]], pillar_weights: dict | None = None,
                    within: str = "ew") -> pd.DataFrame:
    """Composite = mean of signed winsorised z-scores of the selected features (equal weight), or
    family pillars (equal-weight features within family) combined with `pillar_weights`."""
    parts = []
    for dt, cs in d.groupby("date"):
        cs = cs.set_index("ticker")
        zs = {f: _wz(cs[f]) * s for f, s, _ in features if f in cs.columns}
        Z = pd.DataFrame(zs)
        out = pd.DataFrame(index=cs.index)
        fams = {}
        for f, s, fam in features:
            fams.setdefault(fam, []).append(f)
        for fam, fs in fams.items():
            out[f"score_{fam}"] = Z[[f for f in fs if f in Z.columns]].mean(axis=1).clip(-3, 3)
        if pillar_weights:
            out["composite"] = sum(w * out[f"score_{fam}"].fillna(0) for fam, w in pillar_weights.items() if f"score_{fam}" in out.columns)
        else:
            out["composite"] = Z.mean(axis=1).clip(-3, 3)
        out["coverage"] = Z.notna().mean(axis=1)
        out["eligible"] = cs["eligible"] & (out["coverage"] >= 0.5)
        out["segment"] = cs["segment"]; out["market_cap"] = cs["market_cap"]; out["date"] = dt
        parts.append(out.reset_index())
    return pd.concat(parts, ignore_index=True)


def composite_ic(scored: pd.DataFrame, fr: pd.DataFrame, horizon: str = "3m", start=None, end=None) -> pd.Series:
    s = scored.copy()
    if start is not None:
        s = s[s["date"] > start]
    if end is not None:
        s = s[s["date"] <= end]
    return _ic_series(s, fr, "composite", horizon)


def walk_forward_family_weights(scored: pd.DataFrame, fr: pd.DataFrame, families: list[str], horizon: str = "3m",
                                min_train: int = 36, shrink: float = 0.5) -> dict:
    """Out-of-sample family weights = shrink * equal + (1-shrink) * trailing positive IC share."""
    k = HORIZONS[horizon]
    frh = fr[fr["horizon"] == horizon].set_index("date")
    pic, groups = {}, {}
    for dt, cs in scored.groupby("date"):
        if dt not in frh.index:
            continue
        cs = cs[cs["eligible"]].set_index("ticker")
        r = pd.to_numeric(frh.loc[dt].reindex(cs.index), errors="coerce"); m = r.notna()
        if m.sum() < 20:
            continue
        X = cs.loc[m, [f"score_{f}" for f in families]].fillna(0); groups[dt] = (X, r[m])
        pic[dt] = {f: spearmanr(X[f"score_{f}"], r[m])[0] for f in families}
    pic = pd.DataFrame(pic).T.sort_index()
    eq = np.full(len(families), 1 / len(families))
    oos, wpath = {}, {}
    for dt in pic.index:
        known = pic[pic.index <= pd.Timestamp(dt) - pd.DateOffset(months=k)]
        if len(known) < min_train:
            continue
        est = known.tail(60).mean().clip(lower=0).values
        est = est / est.sum() if est.sum() > 0 else eq
        w = shrink * eq + (1 - shrink) * est
        X, r = groups[dt]
        oos[dt] = spearmanr(X.values @ w, r)[0]; wpath[dt] = dict(zip(families, w))
    return {"oos_ic": pd.Series(oos).sort_index(), "weights": pd.DataFrame(wpath).T.sort_index(), "pillar_ic": pic}


def nested_oos(d: pd.DataFrame, fr: pd.DataFrame, rule: str = "strict") -> dict:
    """Select features on the first half only; evaluate the equal-weight composite on the second half only."""
    d1 = d[d["date"] <= SPLIT_DATE]
    fr1 = fr[fr["date"] <= SPLIT_DATE - pd.DateOffset(months=3)]        # forward returns fully inside the first half
    z1 = evaluate(d1, fr1)
    # within-half evaluation has no split; apply the t-threshold and ex-winners criteria only
    cfg = SELECTION[rule]
    ok = (z1["ic_3m"] > 0) & (z1["t_3m"] >= cfg["t_min"])
    if cfg["ex_winners"]:
        ok &= z1["ic_ex_winners"] > 0
    feats = [(r["feature"], int(r["sign"]), r["family"]) for _, r in z1[ok].iterrows() if r["family"] != "size"]
    if not feats:
        return {"features": [], "oos_ic": np.nan}
    sc = build_composite(d, feats)
    ic2 = composite_ic(sc, fr, "3m", start=SPLIT_DATE)
    ic2_12 = composite_ic(sc, fr, "12m", start=SPLIT_DATE)
    return {"features": [f for f, _, _ in feats], "n_features": len(feats), "oos_ic_3m": float(ic2.mean()), "oos_t_3m": _hac_t(ic2, 2),
            "oos_pct_pos": float((ic2 > 0).mean()), "oos_ic_12m": float(ic2_12.mean()), "oos_t_12m": _hac_t(ic2_12, 11), "n_oos": int(len(ic2)),
            "first_half_table": z1[["feature", "family", "ic_3m", "t_3m", "ic_ex_winners"]].sort_values("t_3m", ascending=False), "scored": sc}
