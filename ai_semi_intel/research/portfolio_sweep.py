"""Hyperparameter sweep and walk-forward tuning for portfolio construction.

Grid (all combinations):
  method      : equal, inv_vol, min_var, erc, max_sharpe_grinold (IC 0.02 / 0.03 / 0.05)
  max_weight  : per-name cap
  lookback    : covariance window (trading days)
  n_hold      : number of holdings (segment cap 3 names)
  hysteresis  : keep a held name while its rank <= n_hold * k (k = 1 -> no hysteresis)
  vol_target  : ex-ante volatility target (None = fully invested); cash earns 0

Everything is point-in-time: at each month-end the selection uses the PIT score, the covariance uses
only prior returns, and execution is at the next available daily close. The position is held until the
first available close after the next signal date. Costs are charged on every buy and sell.
Objective: annualised Sharpe of net monthly returns (rf = 2%).

Walk-forward tuning: for each calendar year Y >= first_test_year, the configuration with the best
trailing Sharpe over all months before Y is applied during Y. The concatenated out-of-sample series is
the honest estimate of what tuning delivers.
"""
from __future__ import annotations

import itertools
import time

import numpy as np
import pandas as pd
from scipy.optimize import linprog, minimize
from sklearn.covariance import LedoitWolf

TD = 252
RF = 0.02
GRID = {"max_weight": [0.10, 0.15], "lookback": [504, 756], "n_hold": [10, 15, 20], "hysteresis": [1.0, 1.5],
        "vol_target": [None, 0.15, 0.20, 0.25, 0.30, 0.35]}
METHODS = ["equal", "inv_vol", "min_var", "erc", "ms_ic02", "ms_ic03", "ms_ic05"]


def _lw(R: pd.DataFrame) -> np.ndarray:
    return LedoitWolf().fit(R.values).covariance_ * TD


def _solve(obj, n, lo, hi, x0=None, segments: np.ndarray | None = None,
           segment_weight_cap: float | None = None):
    # If too few names survive the PIT/price filters to invest 100% without breaking the name cap,
    # retain the residual as zero-return cash instead of silently relaxing the cap.
    budget = min(1.0, n * hi)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - budget}]
    group_rows = []
    if segments is not None and segment_weight_cap is not None:
        segments = np.asarray(segments)
        if len(np.unique(segments)) * segment_weight_cap < budget - 1e-9:
            raise ValueError("infeasible segment weight cap")
        for segment in np.unique(segments):
            idx = np.flatnonzero(segments == segment)
            row = np.zeros(n); row[idx] = 1.0; group_rows.append(row)
            cons.append({"type": "ineq", "fun": lambda w, ii=idx: segment_weight_cap - w[ii].sum()})
    # SLSQP is materially more reliable when it starts inside every name and group bound.  Find a
    # feasible point with a zero-objective LP, then use the caller's preferred start only if feasible.
    lp = linprog(np.zeros(n), A_ub=np.asarray(group_rows) if group_rows else None,
                 b_ub=np.full(len(group_rows), segment_weight_cap) if group_rows else None,
                 A_eq=np.ones((1, n)), b_eq=np.array([budget]), bounds=[(lo, hi)] * n,
                 method="highs")
    if not lp.success:
        raise ValueError(f"infeasible portfolio constraints: {lp.message}")
    equal = np.full(n, budget / n)
    center = minimize(lambda w: np.sum((w - equal) ** 2), lp.x, method="SLSQP",
                      bounds=[(lo, hi)] * n, constraints=cons,
                      options={"maxiter": 300, "ftol": 1e-10})
    if not center.success or not np.isfinite(center.x).all():
        raise RuntimeError(f"failed to construct a central feasible portfolio: {center.message}")
    proposed = equal if x0 is None else np.asarray(x0, dtype=float)
    proposed_ok = (np.isclose(proposed.sum(), budget, atol=1e-8)
                   and proposed.min() >= lo - 1e-8 and proposed.max() <= hi + 1e-8
                   and all(row @ proposed <= segment_weight_cap + 1e-8 for row in group_rows))
    x0 = proposed if proposed_ok else center.x
    starts = [x0]
    if not np.allclose(x0, lp.x):
        starts.append(lp.x)
    candidates = [minimize(obj, start, method="SLSQP", bounds=[(lo, hi)] * n,
                           constraints=cons, options={"maxiter": 500, "ftol": 1e-9})
                  for start in starts]
    successful = [candidate for candidate in candidates
                  if candidate.success and np.isfinite(candidate.x).all()]
    if not successful:
        detail = "; ".join(str(candidate.message) for candidate in candidates)
        raise RuntimeError(f"portfolio optimizer failed from all feasible starts: {detail}")
    res = min(successful, key=lambda candidate: float(candidate.fun))
    w = np.asarray(res.x, dtype=float)
    if not np.isclose(w.sum(), budget, atol=1e-6):
        raise RuntimeError("portfolio optimizer violated the invested-budget constraint")
    if w.min() < lo - 1e-6 or w.max() > hi + 1e-6:
        raise RuntimeError("portfolio optimizer violated name bounds")
    if segments is not None and segment_weight_cap is not None:
        if max(w[segments == s].sum() for s in np.unique(segments)) > segment_weight_cap + 1e-6:
            raise RuntimeError("portfolio optimizer violated segment cap")
    return w


def _weights(method: str, S: np.ndarray, z: np.ndarray, lo: float, hi: float,
             segments: np.ndarray | None = None, segment_weight_cap: float | None = None) -> np.ndarray:
    n = S.shape[0]
    if method == "equal":
        raw = np.full(n, 1 / n)
        return _solve(lambda w: np.sum((w - raw) ** 2), n, lo, hi, raw, segments, segment_weight_cap)
    sig = np.sqrt(np.diag(S))
    if method == "inv_vol":
        raw = 1 / sig; raw = raw / raw.sum()
        return _solve(lambda w: np.sum((w - raw) ** 2), n, lo, hi, raw, segments, segment_weight_cap)
    if method == "min_var":
        return _solve(lambda w: w @ S @ w, n, lo, hi, segments=segments, segment_weight_cap=segment_weight_cap)
    if method == "erc":
        def obj(w):
            v = np.sqrt(w @ S @ w); rc = w * (S @ w) / v
            return np.sum((rc - v / n) ** 2) * 1e4
        return _solve(obj, n, lo, hi, segments=segments, segment_weight_cap=segment_weight_cap)
    if method.startswith("ms_ic"):
        ic = int(method[-2:]) / 100
        mu = ic * sig * z
        return _solve(lambda w: -(w @ mu) / np.sqrt(w @ S @ w), n, lo, hi,
                      segments=segments, segment_weight_cap=segment_weight_cap)
    raise ValueError(method)


def full_union_turnover(previous: pd.Series, current: pd.Series) -> float:
    """One-way turnover on the complete holdings union.

    Multiplying this value by two and by a per-traded-notional cost charges both purchases and sales.
    An initial 80% investment therefore has turnover 40% and incurs 80% * cost_bp.
    """
    previous = pd.Series(dtype=float) if previous is None else previous.astype(float)
    current = pd.Series(dtype=float) if current is None else current.astype(float)
    names = previous.index.union(current.index)
    return float((current.reindex(names).fillna(0.0) - previous.reindex(names).fillna(0.0)).abs().sum() / 2)


def execution_dates(index: pd.DatetimeIndex, signal: pd.Timestamp, next_signal: pd.Timestamp,
                    lag_bars: int = 1) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return tradable entry/exit closes strictly after the two signal dates."""
    if lag_bars < 1:
        raise ValueError("lag_bars must be >= 1: a close-based signal cannot fill at the same close")
    idx = pd.DatetimeIndex(index).sort_values().unique()
    p0 = int(idx.searchsorted(pd.Timestamp(signal), side="right")) + lag_bars - 1
    p1 = int(idx.searchsorted(pd.Timestamp(next_signal), side="right")) + lag_bars - 1
    if p0 >= len(idx) or p1 >= len(idx):
        raise IndexError("no complete post-signal execution window")
    return pd.Timestamp(idx[p0]), pd.Timestamp(idx[p1])


def select(cs: pd.DataFrame, n_hold: int, seg_cap: int, prev: list[str], k: float) -> list[str]:
    """Top-n by composite with a segment cap and a hysteresis band on previously held names."""
    cand = cs[cs["eligible"]].sort_values("composite", ascending=False)
    rank = {t: i + 1 for i, t in enumerate(cand.index)}
    keep = [t for t in prev if t in rank and rank[t] <= n_hold * k]
    picks, cnt = [], {}
    for t in keep:
        seg = cand.loc[t, "segment"]
        if cnt.get(seg, 0) < seg_cap:
            picks.append(t); cnt[seg] = cnt.get(seg, 0) + 1
    for t, r in cand.iterrows():
        if len(picks) >= n_hold:
            break
        if t in picks or cnt.get(r["segment"], 0) >= seg_cap:
            continue
        picks.append(t); cnt[r["segment"]] = cnt.get(r["segment"], 0) + 1
    return picks[:n_hold]


def run_grid(scored: pd.DataFrame, adj: pd.DataFrame, grid: dict = GRID, methods=METHODS, seg_cap: int = 3,
             cost_bp: float = 20.0, min_days: int = 200, verbose: bool = True,
             execution_lag_bars: int = 1, segment_weight_cap: float = 0.35) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (net returns, config table, gross returns, one-way turnover), all months x configs."""
    dates = sorted(pd.to_datetime(scored["date"].unique()))
    rets = adj.pct_change(fill_method=None)
    configs, series, turnover = [], {}, {}
    t0 = time.time()
    contexts = list(itertools.product(grid["lookback"], grid["n_hold"], grid["hysteresis"]))
    for ci, (lookback, n_hold, k) in enumerate(contexts):
        prev_names: list[str] = []
        prev_w: dict[tuple, pd.Series] = {}
        rows = {}
        for i, d in enumerate(dates[:-1]):
            d = pd.Timestamp(d); d_next = pd.Timestamp(dates[i + 1])
            try:
                entry_date, exit_date = execution_dates(adj.index, d, d_next, execution_lag_bars)
            except IndexError:
                continue
            cs = scored[scored["date"] == d].set_index("ticker")
            picks = [p for p in select(cs, n_hold, seg_cap, prev_names, k) if p in adj.columns]
            hist = rets.loc[(rets.index <= d), picks].tail(lookback)
            ok = [p for p in picks if hist[p].notna().sum() >= min_days]
            if len(ok) < max(6, n_hold // 2):
                prev_names = picks; continue
            H = hist[ok].copy(); H = H.apply(lambda c: c.fillna(H.mean(axis=1))).dropna()
            S = _lw(H)
            z = cs.loc[ok, "composite"].values; z = (z - z.mean()) / (z.std() if z.std() else 1)
            entry = adj.loc[entry_date, ok]
            if entry.isna().any():
                # Availability is known at entry. Rebuild risk and weights after excluding unavailable names.
                ok = [p for p in ok if pd.notna(entry[p])]
                if len(ok) < max(6, n_hold // 2):
                    prev_names = picks; continue
                H = hist[ok].copy(); H = H.apply(lambda c: c.fillna(H.mean(axis=1))).dropna()
                S = _lw(H)
                z = cs.loc[ok, "composite"].values; z = (z - z.mean()) / (z.std() if z.std() else 1)
                entry = adj.loc[entry_date, ok]
            exit_ = adj.loc[exit_date, ok]
            if exit_.isna().any():
                missing = list(exit_.index[exit_.isna()])
                raise ValueError(f"missing exit price at {exit_date.date()} for {missing}; refusing a future-data drop")
            nxt = (exit_ / entry - 1).values
            prev_names = ok
            segments = cs.loc[ok, "segment"].astype(str).values
            for method in methods:
                for mw in grid["max_weight"]:
                    lo = min(0.02, mw); hi = float(mw)
                    try:
                        solved = _weights(method, S, z, lo, hi, segments, segment_weight_cap)
                    except (RuntimeError, ValueError) as exc:
                        counts = pd.Series(segments).value_counts().to_dict()
                        raise RuntimeError(
                            f"weight solve failed date={d.date()} method={method} max_weight={mw} "
                            f"n={len(ok)} segment_counts={counts}: {exc}"
                        ) from exc
                    w = pd.Series(solved, index=ok)
                    exante = float(np.sqrt(w.values @ S @ w.values))
                    for vt in grid["vol_target"]:
                        scale = 1.0 if vt is None else min(1.0, vt / exante) if exante > 0 else 1.0
                        wv = w * scale
                        key = (method, mw, lookback, n_hold, k, vt)
                        pw = prev_w.get(key)
                        to = full_union_turnover(pw, wv)
                        gross = float(wv.values @ nxt)
                        rows.setdefault(key, {})[d] = (gross, to)
                        prev_w[key] = wv
        for key, ser in rows.items():
            series[key] = pd.Series({d: g for d, (g, _) in ser.items()})
            turnover[key] = pd.Series({d: t_ for d, (_, t_) in ser.items()})
        if verbose:
            print(f"  context {ci + 1}/{len(contexts)} lookback={lookback} n={n_hold} k={k} done in {time.time() - t0:.0f}s", flush=True)
    G = pd.DataFrame(series).sort_index(); T = pd.DataFrame(turnover).sort_index()[G.columns]
    cfg = pd.DataFrame([dict(method=m, max_weight=mw, lookback=lb, n_hold=nh, hysteresis=k, vol_target=0.0 if (vt is None or pd.isna(vt)) else float(vt))
                        for (m, mw, lb, nh, k, vt) in G.columns])
    cfg.index = range(len(cfg)); G.columns = cfg.index; T.columns = cfg.index
    R = G - cost_bp / 1e4 * 2 * T                      # net of round-trip costs on one-way turnover
    return R, cfg, G, T


def default_erc_config(cfg: pd.DataFrame) -> int:
    """Resolve the predeclared production control from a sweep configuration table."""
    m = ((cfg["method"] == "erc") & np.isclose(cfg["max_weight"], 0.15)
         & (cfg["lookback"].astype(int) == 756) & (cfg["n_hold"].astype(int) == 15)
         & np.isclose(cfg["hysteresis"], 1.0) & np.isclose(cfg["vol_target"], 0.25))
    ids = list(cfg.index[m])
    if len(ids) != 1:
        raise ValueError(f"expected one fixed ERC control, found {len(ids)}")
    return int(ids[0])


def replay_policy(scored: pd.DataFrame, adj: pd.DataFrame, cfg: pd.DataFrame,
                  policy: int | dict[int, int], start: str | pd.Timestamp, end: str | pd.Timestamp,
                  seg_cap: int = 3, cost_bp: float = 20.0, min_days: int = 200,
                  execution_lag_bars: int = 1, segment_weight_cap: float = 0.35) -> dict:
    """Replay a fixed config or year->config policy with actual cross-config turnover.

    This path is independent of the precomputed return matrix used by the tuner. It carries the prior
    holdings across outer folds, counts exits on the full holdings union, and records target weights so
    a sampled month can be reconstructed from prices without calling this function.
    """
    dates = [pd.Timestamp(x) for x in sorted(pd.to_datetime(scored["date"].unique()))]
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    rets = adj.pct_change(fill_method=None)
    prev_names: list[str] = []
    prev_w = pd.Series(dtype=float)
    rows: list[dict] = []
    weights: dict[pd.Timestamp, pd.Series] = {}
    for i, d in enumerate(dates[:-1]):
        if d < start or d >= end:
            continue
        cid = int(policy if isinstance(policy, (int, np.integer)) else policy[d.year])
        c = cfg.loc[cid]
        d_next = dates[i + 1]
        try:
            entry_date, exit_date = execution_dates(adj.index, d, d_next, execution_lag_bars)
        except IndexError:
            continue
        cs = scored[scored["date"] == d].set_index("ticker")
        n_hold, lookback, k = int(c["n_hold"]), int(c["lookback"]), float(c["hysteresis"])
        picks = [p for p in select(cs, n_hold, seg_cap, prev_names, k) if p in adj.columns]
        hist = rets.loc[rets.index <= d, picks].tail(lookback)
        ok = [p for p in picks if hist[p].notna().sum() >= min_days and pd.notna(adj.loc[entry_date, p])]
        if len(ok) < max(6, n_hold // 2):
            prev_names = picks
            continue
        H = hist[ok].copy(); H = H.apply(lambda x: x.fillna(H.mean(axis=1))).dropna()
        S = _lw(H)
        z = cs.loc[ok, "composite"].values; z = (z - z.mean()) / (z.std() if z.std() else 1)
        hi = float(c["max_weight"]); lo = min(0.02, hi)
        segments = cs.loc[ok, "segment"].astype(str).values
        w = pd.Series(_weights(str(c["method"]), S, z, lo, hi, segments, segment_weight_cap), index=ok)
        exante = float(np.sqrt(w.values @ S @ w.values))
        vt = float(c["vol_target"])
        scale = 1.0 if np.isclose(vt, 0.0) else min(1.0, vt / exante) if exante > 0 else 1.0
        target = w * scale
        exit_ = adj.loc[exit_date, ok]
        if exit_.isna().any():
            missing = list(exit_.index[exit_.isna()])
            raise ValueError(f"missing exit price at {exit_date.date()} for {missing}; refusing a future-data drop")
        asset_return = exit_ / adj.loc[entry_date, ok] - 1
        gross = float((target * asset_return).sum())
        turnover = full_union_turnover(prev_w, target)
        cost = float(2 * turnover * cost_bp / 1e4)
        rows.append({"date": d, "entry_date": entry_date, "exit_date": exit_date, "config": cid,
                     "gross": gross, "turnover": turnover, "cost": cost, "net": gross - cost,
                     "n_hold": len(ok), "invested": float(target.sum()), "exante_vol": exante})
        weights[d] = target
        prev_w, prev_names = target, ok
    monthly = pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()
    weight_frame = pd.DataFrame(weights).T.sort_index().fillna(0.0) if weights else pd.DataFrame()
    return {"monthly": monthly, "weights": weight_frame}


def stats(r: pd.Series, rf: float = RF) -> dict:
    r = r.dropna()
    if len(r) < 12:
        return {"cagr": np.nan, "vol": np.nan, "sharpe": np.nan, "max_dd": np.nan, "months": len(r)}
    cum = (1 + r).cumprod(); yrs = len(r) / 12
    cagr = cum.iloc[-1] ** (1 / yrs) - 1; vol = r.std() * np.sqrt(12)
    return {"cagr": cagr, "vol": vol, "sharpe": (cagr - rf) / vol if vol else np.nan, "max_dd": float((cum / cum.cummax() - 1).min()),
            "calmar": cagr / abs(float((cum / cum.cummax() - 1).min())) if float((cum / cum.cummax() - 1).min()) else np.nan, "months": int(len(r))}


def evaluate_grid(R: pd.DataFrame, cfg: pd.DataFrame, split: pd.Timestamp | None = None) -> pd.DataFrame:
    out = cfg.copy()
    st = pd.DataFrame({c: stats(R[c]) for c in R.columns}).T
    out = out.join(st)
    if split is not None:
        out["sharpe_h1"] = [stats(R.loc[R.index <= split, c])["sharpe"] for c in R.columns]
        out["sharpe_h2"] = [stats(R.loc[R.index > split, c])["sharpe"] for c in R.columns]
    return out


def walk_forward(R: pd.DataFrame, cfg: pd.DataFrame, first_test_year: int = 2016, min_train_months: int = 36,
                 objective: str = "sharpe") -> dict:
    """Each year, apply the config with the best trailing net Sharpe (all prior months)."""
    years = sorted({d.year for d in R.index if d.year >= first_test_year})
    oos, choices = [], []
    for y in years:
        train = R[R.index < pd.Timestamp(f"{y}-01-01")]
        if len(train) < min_train_months:
            continue
        sh = {c: stats(train[c])[objective] for c in R.columns}
        best = max(sh, key=lambda c: (sh[c] if not np.isnan(sh[c]) else -9))
        test = R[(R.index >= pd.Timestamp(f"{y}-01-01")) & (R.index < pd.Timestamp(f"{y + 1}-01-01"))][best]
        oos.append(test); choices.append({"year": y, "config": int(best), **cfg.loc[best].to_dict(), "train_sharpe": sh[best], "test_months": int(len(test))})
    oos = pd.concat(oos).sort_index()
    return {"oos": oos, "choices": pd.DataFrame(choices)}


def marginal_effects(ev: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for p in ("method", "max_weight", "lookback", "n_hold", "hysteresis", "vol_target"):
        g = ev.groupby(p)["sharpe"].agg(["mean", "median", "max", "count"]).round(3)
        out[p] = g
    return out


# ----------------------------------------------------------------- robust selection rules
def _sharpe(r: pd.Series, rf: float = RF) -> float:
    r = r.dropna()
    if len(r) < 12:
        return np.nan
    cagr = (1 + r).prod() ** (12 / len(r)) - 1; vol = r.std() * np.sqrt(12)
    return (cagr - rf) / vol if vol else np.nan


def _neighbours(cfg: pd.DataFrame, i: int, levers: tuple[str, ...]) -> list[int]:
    """Configs identical to i except one lever moved by one level (inclusive of i)."""
    row = cfg.loc[i]
    out = [i]
    for p in levers:
        levels = sorted(cfg[p].unique()); pos = levels.index(row[p])
        for q in (pos - 1, pos + 1):
            if 0 <= q < len(levels):
                m = np.ones(len(cfg), dtype=bool)
                for c in cfg.columns:
                    m &= (cfg[c] == (levels[q] if c == p else row[c])).values
                out += list(cfg.index[m])
    return sorted(set(out))


def walk_forward_rule(R: pd.DataFrame, cfg: pd.DataFrame, rule: str = "max", subset: pd.Index | None = None,
                      levers: tuple[str, ...] = ("n_hold", "vol_target"), first_test_year: int = 2016,
                      min_train_months: int = 36, margin: float = 0.0, incumbent: int | None = None, temperature: float = 20.0) -> dict:
    """Yearly walk-forward with a selection rule:
       max            - best trailing Sharpe
       nbr_median     - best neighbourhood median of trailing Sharpe (one lever moved by one level)
       maximin_halves - best min(Sharpe over the two halves of the training window)
       sticky         - keep the incumbent unless the best candidate beats it by `margin`
       average        - hold all candidates equally (model averaging = averaged weights)
       softmax        - Sharpe-weighted average of candidates, exp(temperature x trailing Sharpe)"""
    idx = list(subset) if subset is not None else list(R.columns)
    sub = cfg.loc[idx]
    years = sorted({d.year for d in R.index if d.year >= first_test_year})
    oos, choices = [], []
    cur = incumbent
    nbr_cache = {i: [j for j in _neighbours(sub, i, levers) if j in idx] for i in idx} if rule in ("nbr_median", "sticky_nbr") else {}
    for y in years:
        train = R[R.index < pd.Timestamp(f"{y}-01-01")][idx]
        if len(train) < min_train_months:
            continue
        test = R[(R.index >= pd.Timestamp(f"{y}-01-01")) & (R.index < pd.Timestamp(f"{y + 1}-01-01"))][idx]
        sh = pd.Series({i: _sharpe(train[i]) for i in idx})
        if rule == "average":
            w = pd.Series(1 / len(idx), index=idx)
        elif rule == "softmax":
            e = np.exp(temperature * (sh - sh.max()).fillna(-9)); w = e / e.sum()
        else:
            if rule in ("max", "sticky"):
                score = sh
            elif rule in ("nbr_median", "sticky_nbr"):
                score = pd.Series({i: float(np.nanmedian(sh[nbr_cache[i]])) for i in idx})
            elif rule == "maximin_halves":
                h = len(train) // 2
                score = pd.Series({i: min(_sharpe(train[i].iloc[:h]), _sharpe(train[i].iloc[h:])) for i in idx})
            else:
                raise ValueError(rule)
            best = int(score.idxmax())
            if rule.startswith("sticky") and cur is not None and cur in idx:
                if score[best] - score[cur] < margin:
                    best = cur
            cur = best
            w = pd.Series(0.0, index=idx); w[best] = 1.0
        oos.append((test * w).sum(axis=1))
        choices.append({"year": y, "n_active": int((w > 0.01).sum()), "top": int(w.idxmax()), **{k: cfg.loc[int(w.idxmax()), k] for k in ("method", "n_hold", "vol_target", "hysteresis")}})
    series = pd.concat(oos).sort_index()
    ch = pd.DataFrame(choices)
    return {"oos": series, "choices": ch, "switches": int((ch["top"] != ch["top"].shift()).sum() - 1) if len(ch) else 0}
