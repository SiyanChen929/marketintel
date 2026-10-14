"""Trailing-only scoring of the point-in-time panel and monthly portfolio backtest.

The score mirrors the live composite (value 40 / growth 30 / quality 20 / momentum 10) but
uses only fields that can be reconstructed without look-ahead. Consensus growth, estimate
revisions and Street targets are absent, so this is a *lower bound* on the live model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.settings import CONFIG
from ..config.universe import SEGMENT_ORDER

PILLARS_PIT: dict[str, dict[str, int]] = {
    # trailing analogues of the live pillars (consensus fields are not reconstructable)
    "fund_momentum": {"growth_accel": 1, "rev_q_yoy": 1},
    "price_momentum": {"mom_12_1_volscaled": 1, "industry_mom": 1, "rel_6m_vs_soxx": 1},
    "value": {"mult_residual": -1},
}
DEFAULT_WEIGHTS = {"fund_momentum": 0.50, "price_momentum": 0.35, "value": 0.15}
PILLAR_SIGNALS = ["composite"] + [f"score_{k}" for k in DEFAULT_WEIGHTS]
HORIZONS = {"1m": 1, "3m": 3, "6m": 6, "12m": 12}


def _wz(s: pd.Series, pct: float = 0.05) -> pd.Series:
    x = s.astype(float)
    if x.notna().sum() < 5:
        return pd.Series(np.nan, index=s.index)
    x = x.clip(x.quantile(pct), x.quantile(1 - pct))
    sd = x.std()
    return (x - x.mean()) / sd if sd and sd > 0 else pd.Series(0.0, index=s.index)


def score_cross_section(cs: pd.DataFrame, pillars: dict = PILLARS_PIT) -> pd.DataFrame:
    out = pd.DataFrame(index=cs.index)
    for name, feats in pillars.items():
        zs = []
        for f, sign in feats.items():
            if f not in cs.columns:
                continue
            z = _wz(cs[f]) * sign
            if name == "value" and "segment" in cs.columns:
                zseg = cs.groupby("segment")[f].transform(lambda s: _wz(s) if s.notna().sum() >= 4 else pd.Series(np.nan, index=s.index)) * sign
                z = pd.concat([z, zseg], axis=1).mean(axis=1)
            zs.append(z)
        Z = pd.concat(zs, axis=1) if zs else pd.DataFrame(index=cs.index)
        out[f"score_{name}"] = Z.mean(axis=1).clip(-3, 3) if len(Z.columns) else np.nan
        out[f"cov_{name}"] = Z.notna().mean(axis=1) if len(Z.columns) else 0.0
    out["coverage"] = out[[c for c in out.columns if c.startswith("cov_")]].mean(axis=1)
    return out


def composite(scores: pd.DataFrame, w: dict = DEFAULT_WEIGHTS) -> pd.Series:
    return sum(w[k] * scores[f"score_{k}"].fillna(0) for k in w)


def add_mult_residual(cs: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional ln(EV/S) ~ trailing growth + FCF margin + gross margin; residual = rich (+) / cheap (-)."""
    cs = cs.copy(); cs["mult_residual"] = np.nan
    y = np.log(cs["ev_sales"].where((cs["ev_sales"] > 0.2) & (cs["ev_sales"] < 80)))
    X = pd.DataFrame({"g": cs["rev_growth_ttm"].clip(-0.5, 1.5), "f": cs["fcf_margin"].clip(-0.5, 0.6), "gm": cs["gross_margin"].clip(0, 0.95)})
    m = y.notna() & X.notna().all(axis=1)
    if m.sum() >= 15:
        A = np.column_stack([np.ones(m.sum()), X[m].values])
        coef, *_ = np.linalg.lstsq(A, y[m].values, rcond=None)
        cs.loc[m, "mult_residual"] = y[m].values - A @ coef
    return cs


def score_panel(panel: pd.DataFrame, w: dict = DEFAULT_WEIGHTS, min_mcap: float = 1e9, min_cov: float = 0.6) -> pd.DataFrame:
    parts = []
    for d, cs in panel.groupby("date"):
        cs = add_mult_residual(cs.set_index("ticker"))
        cs["industry_mom"] = cs.groupby("segment")["ret_6m"].transform("mean")
        sc = score_cross_section(cs)
        sc["composite"] = composite(sc, w)
        sc["date"] = d; sc["segment"] = cs["segment"]; sc["market_cap"] = cs.get("market_cap")
        sc["eligible"] = (cs["market_cap"].fillna(0) >= min_mcap) & (sc["coverage"] >= min_cov) & cs["ev_sales"].notna()
        parts.append(sc.reset_index())
    return pd.concat(parts, ignore_index=True)


def forward_returns(dates: list, adj: pd.DataFrame, tickers: list[str], bench=("SOXX", "SPY", "QQQ")) -> pd.DataFrame:
    """Forward total returns from each month-end date over 1/3/6/12 month-ends."""
    cols = [t for t in tickers if t in adj.columns] + [b for b in bench if b in adj.columns]
    me = adj.reindex(dates)[cols]
    rows = []
    for i, d in enumerate(dates):
        for h, k in HORIZONS.items():
            if i + k < len(dates):
                r = me.iloc[i + k] / me.iloc[i] - 1
                rows.append(r.rename(h).to_frame().T.assign(date=d, horizon=h))
    fr = pd.concat(rows, ignore_index=True)
    return fr


def select_top(cs: pd.DataFrame, n: int = 15, max_per_seg: int = 3) -> list[str]:
    cand = cs[cs["eligible"]].sort_values("composite", ascending=False)
    picks, cnt = [], {}
    for t, r in cand.iterrows():
        if cnt.get(r["segment"], 0) >= max_per_seg:
            continue
        picks.append(t); cnt[r["segment"]] = cnt.get(r["segment"], 0) + 1
        if len(picks) >= n:
            break
    return picks


def run_backtest(scored: pd.DataFrame, fr: pd.DataFrame, cost_bp: float = 20.0) -> dict:
    """Monthly EW top-15 (segment cap), quintiles, universe EW, benchmarks; 1-month forward returns."""
    fr1 = fr[fr["horizon"] == "1m"].set_index("date")
    dates = sorted(scored["date"].unique())
    rows, prev = [], set()
    for d in dates:
        if d not in fr1.index:
            continue
        cs = scored[scored["date"] == d].set_index("ticker")
        r = fr1.loc[d]
        elig = cs[cs["eligible"]]
        if len(elig) < 20:
            continue
        picks = select_top(cs)
        turnover = 1 - len(set(picks) & prev) / max(len(picks), 1) if prev else 1.0
        prev = set(picks)
        rp = pd.to_numeric(r.reindex(picks), errors="coerce").dropna()
        q = pd.qcut(elig["composite"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        qret = {f"q{k}": float(pd.to_numeric(r.reindex(q[q == k].index), errors="coerce").mean()) for k in range(1, 6)}
        rows.append({"date": d, "top15": float(rp.mean()), "top15_net": float(rp.mean()) - cost_bp / 1e4 * turnover * 2,
                     "universe_ew": float(pd.to_numeric(r.reindex(elig.index), errors="coerce").mean()), "turnover": turnover,
                     "n_elig": int(len(elig)), "SOXX": float(r.get("SOXX", np.nan)), "SPY": float(r.get("SPY", np.nan)), "QQQ": float(r.get("QQQ", np.nan)), **qret})
    bt = pd.DataFrame(rows).set_index("date")
    bt["ls_q5_q1"] = bt["q5"] - bt["q1"]
    bt["active_vs_universe"] = bt["top15"] - bt["universe_ew"]
    return {"monthly": bt, "stats": _stats(bt)}


def _stats(bt: pd.DataFrame, rf_annual: float = 0.02) -> pd.DataFrame:
    out = {}
    for c in ("top15", "top15_net", "universe_ew", "SOXX", "SPY", "QQQ", "ls_q5_q1", "q1", "q2", "q3", "q4", "q5"):
        s = bt[c].dropna()
        if s.empty:
            continue
        yrs = len(s) / 12
        cum = (1 + s).cumprod()
        cagr = cum.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
        vol = s.std() * np.sqrt(12)
        out[c] = {"cagr": cagr, "vol": vol, "sharpe": (cagr - rf_annual) / vol if vol else np.nan,
                  "max_dd": float((cum / cum.cummax() - 1).min()), "total": float(cum.iloc[-1] - 1), "months": int(len(s)),
                  "hit_rate_vs_universe": float((bt["top15"] > bt["universe_ew"]).mean()) if c == "top15" else np.nan}
    return pd.DataFrame(out).T


def walk_forward_weights(scored: pd.DataFrame, fr: pd.DataFrame, horizon: str = "3m", min_train: int = 36,
                         shrink: float = 0.5, prior: dict = DEFAULT_WEIGHTS) -> dict:
    """Out-of-sample pillar weights: at each date, weights = shrink * prior + (1 - shrink) * IC-proportional
    weights estimated on the trailing window (negative trailing IC -> zero weight). Returns the OOS composite
    IC series and the weight path. No information after t is used at t."""
    from scipy.stats import spearmanr
    pillars = list(prior)
    frh = fr[fr["horizon"] == horizon].set_index("date")
    k = HORIZONS[horizon]
    dates = sorted(scored["date"].unique())
    # pillar IC per date (uses returns realised only after date + k months)
    pic = {}
    groups = {}
    for d in dates:
        if d not in frh.index:
            continue
        cs = scored[(scored["date"] == d) & (scored["eligible"])].set_index("ticker")
        r = pd.to_numeric(frh.loc[d].reindex(cs.index), errors="coerce")
        m = r.notna()
        if m.sum() < 20:
            continue
        groups[d] = (cs.loc[m, [f"score_{p}" for p in pillars]].fillna(0), r[m])
        pic[d] = {p: spearmanr(groups[d][0][f"score_{p}"], groups[d][1])[0] for p in pillars}
    pic = pd.DataFrame(pic).T.sort_index()
    oos_ic, wpath = {}, {}
    pw = np.array([prior[p] for p in pillars])
    for i, d in enumerate(pic.index):
        # training ICs are known only k months after their date
        known = pic[pic.index <= pd.Timestamp(d) - pd.DateOffset(months=k)]
        if len(known) < min_train:
            continue
        est = known.tail(60).mean().clip(lower=0).values
        est = est / est.sum() if est.sum() > 0 else pw
        w = shrink * pw + (1 - shrink) * est
        X, r = groups[d]
        oos_ic[d] = spearmanr(X.values @ w, r)[0]
        wpath[d] = dict(zip(pillars, w))
    oos = pd.Series(oos_ic).sort_index()
    fixed = pd.Series({d: spearmanr(groups[d][0].values @ pw, groups[d][1])[0] for d in oos.index}).sort_index()
    return {"oos_ic": oos, "fixed_ic": fixed, "weights": pd.DataFrame(wpath).T.sort_index(), "pillar_ic": pic}
