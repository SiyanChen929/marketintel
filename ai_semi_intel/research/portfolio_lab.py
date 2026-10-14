"""Portfolio-construction research on the monthly top-15 selection.

For each month-end: take the top-15 (segment cap 3) from a scored panel, estimate risk from
the trailing 3 years of daily returns known at that date (Ledoit-Wolf), build candidate
portfolios, hold for one month, and measure realised performance. Everything is out of
sample by construction (weights use only past returns and point-in-time scores).

Candidates: equal weight; inverse volatility; minimum variance; equal risk contribution;
maximum Sharpe with Grinold alphas (alpha_i = IC * sigma_i * z_i, IC fixed at 0.03);
plus a volatility-target overlay (scale to 25% ex-ante vol, remainder in cash).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from .backtest import select_top

TD = 252


def _lw_cov(R: pd.DataFrame) -> np.ndarray:
    return LedoitWolf().fit(R.values).covariance_ * TD


def _solve(obj, n, lo, hi, cons=None, x0=None):
    cons = cons or [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    x0 = np.full(n, 1 / n) if x0 is None else x0
    res = minimize(obj, x0, method="SLSQP", bounds=[(lo, hi)] * n, constraints=cons, options={"maxiter": 500, "ftol": 1e-10})
    w = np.clip(res.x, 0, None)
    return w / w.sum() if w.sum() else np.full(n, 1 / n)


def weights_for(method: str, S: np.ndarray, z: np.ndarray, lo=0.02, hi=0.15, ic=0.03, rf=0.0) -> np.ndarray:
    n = S.shape[0]
    if method == "equal":
        return np.full(n, 1 / n)
    sig = np.sqrt(np.diag(S))
    if method == "inv_vol":
        w = 1 / sig; return w / w.sum()
    if method == "min_var":
        return _solve(lambda w: w @ S @ w, n, lo, hi)
    if method == "erc":
        def obj(w):
            v = np.sqrt(w @ S @ w); rc = w * (S @ w) / v
            return np.sum((rc - v / n) ** 2) * 1e4
        return _solve(obj, n, lo, hi)
    if method == "max_sharpe_grinold":
        mu = ic * sig * z                       # Grinold: alpha = IC x volatility x score
        return _solve(lambda w: -(w @ mu - rf) / np.sqrt(w @ S @ w), n, lo, hi)
    raise ValueError(method)


def run(scored: pd.DataFrame, adj: pd.DataFrame, methods=("equal", "inv_vol", "min_var", "erc", "max_sharpe_grinold"),
        vol_target: float | None = 0.25, lookback_days: int = 756, min_days: int = 200, cost_bp: float = 20.0,
        segment_cap: int = 3) -> dict:
    dates = sorted(scored["date"].unique())
    rets = adj.pct_change()
    rows = []
    prev_w = {m: pd.Series(dtype=float) for m in methods}
    prev_w.update({f"{m}_vt": pd.Series(dtype=float) for m in methods})
    for i, d in enumerate(dates[:-1]):
        d = pd.Timestamp(d); d_next = pd.Timestamp(dates[i + 1])
        cs = scored[scored["date"] == d].set_index("ticker")
        picks = select_top(cs, 15, segment_cap)
        picks = [p for p in picks if p in adj.columns]
        if len(picks) < 8:
            continue
        hist = rets.loc[(rets.index <= d) & (rets.index > d - pd.Timedelta(days=int(lookback_days * 1.5))), picks].tail(lookback_days)
        ok = [p for p in picks if hist[p].notna().sum() >= min_days]
        if len(ok) < 8:
            continue
        # proxy-fill short histories with the cross-sectional mean of the others
        H = hist[ok].copy()
        H = H.apply(lambda c: c.fillna(H.mean(axis=1)))
        S = _lw_cov(H.dropna())
        z = cs.loc[ok, "composite"].values; z = (z - z.mean()) / (z.std() if z.std() else 1)
        nxt = adj.loc[d_next, ok] / adj.loc[d, ok] - 1
        row = {"date": d, "n": len(ok)}
        for m in methods:
            w = pd.Series(weights_for(m, S, z), index=ok)
            gross = float((w * nxt).sum())
            to = float((w - prev_w[m].reindex(ok).fillna(0)).abs().sum()) / 2 if len(prev_w[m]) else 1.0
            row[m] = gross - cost_bp / 1e4 * 2 * to; row[f"{m}_turnover"] = to; prev_w[m] = w
            row[f"{m}_exante_vol"] = float(np.sqrt(w.values @ S @ w.values))
            if vol_target:
                scale = min(1.0, vol_target / row[f"{m}_exante_vol"]) if row[f"{m}_exante_vol"] > 0 else 1.0
                wv = w * scale
                to_v = float((wv - prev_w[f"{m}_vt"].reindex(ok).fillna(0)).abs().sum()) / 2 if len(prev_w[f"{m}_vt"]) else scale
                row[f"{m}_vt"] = float((wv * nxt).sum()) - cost_bp / 1e4 * 2 * to_v; prev_w[f"{m}_vt"] = wv
                row[f"{m}_vt_scale"] = scale
        rows.append(row)
    bt = pd.DataFrame(rows).set_index("date")
    return {"monthly": bt, "stats": stats(bt, methods, vol_target is not None)}


def stats(bt: pd.DataFrame, methods, with_vt: bool, rf: float = 0.02) -> pd.DataFrame:
    out = {}
    cols = list(methods) + ([f"{m}_vt" for m in methods] if with_vt else [])
    for c in cols:
        s = bt[c].dropna(); yrs = len(s) / 12; cum = (1 + s).cumprod()
        cagr = cum.iloc[-1] ** (1 / yrs) - 1; vol = s.std() * np.sqrt(12)
        dd = (cum / cum.cummax() - 1).min()
        base = c.replace("_vt", "")
        out[c] = {"cagr": cagr, "vol": vol, "sharpe": (cagr - rf) / vol if vol else np.nan, "max_dd": dd, "calmar": cagr / abs(dd) if dd else np.nan,
                  "avg_turnover": bt[f"{base}_turnover"].mean(), "avg_exante_vol": bt[f"{base}_exante_vol"].mean(),
                  "avg_scale": bt[f"{base}_vt_scale"].mean() if c.endswith("_vt") else 1.0, "months": int(len(s))}
    return pd.DataFrame(out).T
