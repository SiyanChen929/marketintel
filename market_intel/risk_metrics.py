"""Frozen-position historical risk: VaR, Expected Shortfall and tracking error.

Design rules this module exists to enforce:

1. **Frozen positions, not free daily rebalancing.** A multi-day loss built by compounding the *portfolio*
   daily return silently rebalances to target every day. A book held for ten days does not do that. Each
   window is priced at the asset level: every holding's cumulative price change from window start to window
   end, combined once with the frozen weights.
2. **Finality comes from ACQUISITION, not from the wall clock.** See :func:`confirmed_sessions`.
3. **The session grid is never compressed.** See :func:`grid_returns` and :func:`frozen_pnl`. Removing
   incomplete rows *before* building windows turns a gap into a fabricated one-day move.
4. **No fabricated history.** A window longer than the shortest listing is unavailable, not truncated.

Nothing here is a regulatory calculation: no liquidity-horizon scaling, no stress calibration, no desk model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import pathlib
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from . import config

TRADING_DAYS = 252
SCHEMA_VERSION = "risk.v4"          # v4 = uninterrupted samples + benchmark-complete, independent TE windows
CLOSE_ET = time(16, 0)
FINALITY_BUFFER_MIN = 20            # a close is treated as confirmed this long after the bell
#: modules whose source can change a number produced here; all of them enter calculator_hash()
CALCULATOR_MODULES = ("risk_metrics.py", "risk_workbench.py")


# ----------------------------------------------------------------------------- sessions & finality
def confirmed_sessions(index: pd.DatetimeIndex, acquired_at: datetime | None,
                       source: str = "unspecified") -> tuple[pd.DatetimeIndex, dict]:
    """Confirmed closes of a price snapshot, decided by **when the snapshot was acquired**.

    A bar dated D is a confirmed close only if the data was fetched at or after D's close + buffer. Deciding
    this from ``now`` instead makes a frozen file rewrite its own history as the afternoon goes on: the very
    same bytes report 389 sessions at 13:30 ET and 390 at 17:30 ET, because a partial 16:00-less bar becomes
    "confirmed" the moment the reader's clock passes the bell. Finality is a property of the data, not of the
    reader, so it is derived from ``acquired_at`` (run.json ``data_cutoff.fetched_at.prices``) and is stable
    forever for a given snapshot.

    ``acquired_at`` is required. Without acquisition provenance the finality of the last bar is unknowable and
    this fails closed rather than guessing from the clock.
    """
    idx = pd.DatetimeIndex(sorted(pd.DatetimeIndex(index).unique()))
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    if acquired_at is None:
        return pd.DatetimeIndex([]), {
            "n_confirmed": 0, "dropped": [], "available": False,
            "basis": "acquisition", "acquired_at": None, "source": source,
            "reason": "no acquisition timestamp for this price snapshot; finality of the last bar is unknowable. "
                      "Wall-clock time is NOT an acceptable substitute - it would make the same frozen snapshot "
                      "report different history before and after 16:20 ET.",
            "rule": f"a bar dated D is confirmed iff the snapshot was acquired at or after D {CLOSE_ET:%H:%M} ET "
                    f"+ {FINALITY_BUFFER_MIN}min"}
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        return pd.DatetimeIndex([]), {
            "n_confirmed": 0, "dropped": [], "available": False,
            "basis": "acquisition", "acquired_at": acquired_at.isoformat(), "source": source,
            "reason": "the acquisition timestamp has no timezone, so it cannot prove whether the New York "
                      "close was complete; finality fails closed",
            "rule": f"a bar dated D is confirmed iff a timezone-aware acquisition timestamp is at or after "
                    f"D {CLOSE_ET:%H:%M} ET + {FINALITY_BUFFER_MIN}min"}
    acq_et = config.now_et(acquired_at)
    cutoff_time = (datetime.combine(acq_et.date(), CLOSE_ET) + timedelta(minutes=FINALITY_BUFFER_MIN)).time()
    acq_date = acq_et.date()
    acquired_after_close = acq_et.time() >= cutoff_time
    #: last date whose close had certainly printed when this snapshot was taken
    confirmed_through = acq_date if acquired_after_close else (acq_date - timedelta(days=1))
    dropped, keep = [], []
    for d in idx:
        dd = d.date()
        if dd > acq_date:
            dropped.append({"date": str(dd), "reason": "bar dated after the snapshot was acquired"})
        elif dd > confirmed_through:
            dropped.append({"date": str(dd),
                            "reason": f"snapshot acquired {acq_et:%Y-%m-%d %H:%M} ET, before this session's "
                                      f"{CLOSE_ET:%H:%M}+{FINALITY_BUFFER_MIN}min confirmation; the bar was partial "
                                      f"when fetched and does not become final because the clock moved on"})
        else:
            keep.append(d)
    return pd.DatetimeIndex(keep), {
        "n_confirmed": len(keep), "dropped": dropped, "available": len(keep) > 0,
        "basis": "acquisition", "acquired_at": acq_et.isoformat(timespec="seconds"), "source": source,
        "confirmed_through": str(confirmed_through), "acquired_after_close": bool(acquired_after_close),
        "rule": f"a bar dated D is confirmed iff the snapshot was acquired at or after D {CLOSE_ET:%H:%M} ET "
                f"+ {FINALITY_BUFFER_MIN}min",
        "wall_clock_independent": True,
        "note": "this result is a function of the snapshot only; re-reading the same file later cannot change it"}


def session_index(px: pd.DataFrame, reference: str, acquired_at: datetime | None,
                  source: str = "unspecified") -> tuple[pd.DatetimeIndex, dict]:
    """Consecutive-session reference grid taken from a continuously-listed listing's own bars.

    No exchange-calendar package is installed, so the sessions the exchange actually held are inferred from a
    reference ETF. That is an *observed* session index, not an official calendar, and is labelled as such.

    This grid is the **reference frame for every window**: it defines what "one session apart" means. It is
    deliberately NOT filtered to rows where every holding has a bar - doing that would make two rows that are
    twelve sessions apart look adjacent.
    """
    if reference not in px.columns:
        return pd.DatetimeIndex([]), {"available": False, "reason": f"reference listing {reference} not in the price panel",
                                      "source": None}
    s = pd.to_numeric(px[reference], errors="coerce").dropna()
    idx, fin = confirmed_sessions(s.index, acquired_at, source=source)
    return idx, {"available": len(idx) > 0, "source": f"observed bars of {reference}", "reference": reference,
                 "is_official_exchange_calendar": False, "n_sessions": len(idx),
                 "start": str(idx[0].date()) if len(idx) else None, "end": str(idx[-1].date()) if len(idx) else None,
                 "role": "reference session grid; window spans and adjacency are defined on THIS index, "
                         "never on the subset of rows where every holding happens to price",
                 "finality": fin}


# ----------------------------------------------------------------------------- uncompressed grid returns
def grid_returns(px: pd.DataFrame, names: list[str], sessions: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Daily returns computed **on the full session grid**, with invalid returns dropped afterwards.

    The defect this replaces::

        sub.loc[usable].pct_change()        # WRONG

    ``usable`` has the incomplete rows already removed, so ``pct_change`` divides row *k* by row *k-1* of the
    *filtered* frame. If a name had no bar on the intervening session, a two-session move is booked as a
    one-day return - a fabricated observation that inflates nothing visibly and corrupts every downstream
    quantile. Here the panel is reindexed onto the whole grid first, so ``pct_change`` always compares
    genuinely adjacent sessions, and only then are rows containing an invalid return removed.

    Returns ``(valid_returns, prices_on_grid, coverage)``.
    """
    cols = [t for t in names if t in px.columns]
    if not cols or not len(sessions):
        return pd.DataFrame(), pd.DataFrame(), {"n_grid": int(len(sessions)), "n_valid_returns": 0,
                                                "n_dropped_returns": 0, "reason": "no priced names or empty grid"}
    sub = px[cols].apply(pd.to_numeric, errors="coerce").reindex(sessions)
    sub = sub.where(sub > 0)                                   # non-positive price is not a price
    r = sub.pct_change(fill_method=None)                       # adjacency guaranteed by the grid
    r = r.iloc[1:]                                             # the first grid row has no predecessor
    valid = r.notna().all(axis=1)
    dropped = [str(d.date()) for d in r.index[~valid.values]]
    cov = {"n_grid": int(len(sessions)), "n_return_slots": int(len(r)), "n_valid_returns": int(valid.sum()),
           "n_dropped_returns": int((~valid).sum()), "dropped_return_dates": dropped[:12],
           "basis": "pct_change taken on the full reference grid, THEN invalid rows dropped; a gap never "
                    "becomes a one-day move"}
    out = r[valid.values]
    # Downstream resampling must be able to distinguish a genuinely uninterrupted shock matrix from one made
    # by deleting invalid dates. Pandas preserves attrs through the simple column selection used by the risk
    # workbench, so the bootstrap can fail closed if a caller bypasses the strict-span gate.
    out.attrs["reference_return_slots"] = int(len(r))
    out.attrs["invalid_return_slots"] = int((~valid).sum())
    out.attrs["uninterrupted"] = bool(valid.all())
    return out, sub, cov


# ----------------------------------------------------------------------------- frozen-position P&L
@dataclass
class WindowSet:
    """Historical horizon-day P&L observations for a frozen book, plus what had to be discarded."""
    pnl: pd.Series
    starts: list
    ends: list
    horizon: int
    n_possible: int
    failures: list = field(default_factory=list)
    cash_weight: float = 0.0
    cash_return: float = 0.0

    @property
    def n(self) -> int:
        return int(len(self.pnl))


def frozen_pnl(px: pd.DataFrame, weights: dict, sessions: pd.DatetimeIndex, horizon: int,
               offset: int = 0, step: int | None = None, cash_weight: float = 0.0,
               cash_annual_rate: float = 0.0) -> WindowSet:
    """Historical scenario P&L of a **frozen** book over ``horizon``-session windows.

    For window [t0, t1]:  pnl = sum_i w_i * (P_i(t1)/P_i(t0) - 1) + w_cash * cash_return(horizon)

    ``sessions`` MUST be the full reference grid, not a validity-filtered subset: window length is counted in
    grid positions, so ``sessions[i]`` to ``sessions[i+horizon]`` is a genuine ``horizon``-session move only
    when the grid is intact. A window whose full path is not priced becomes a recorded *failure*, never a
    shortened or endpoint-only window. ``step=1`` gives overlapping windows; ``step=horizon`` with ``offset``
    in 0..horizon-1 gives one non-overlapping phase.
    """
    step = horizon if step is None else step
    names = [t for t, w in weights.items() if w and w > 0]
    have = [t for t in names if t in px.columns]
    missing_cols = [t for t in names if t not in px.columns]
    prices = px[have].apply(pd.to_numeric, errors="coerce").reindex(sessions) if have else pd.DataFrame(index=sessions)
    values = prices.to_numpy(dtype=float)
    valid = np.isfinite(values) & (values > 0)
    # prefix_bad[k] counts invalid bars in rows [0, k). It makes a full-path check O(number of names)
    # instead of repeatedly slicing a DataFrame for every overlapping window and phase.
    prefix_bad = np.vstack([np.zeros((1, len(have)), dtype=int), (~valid).cumsum(axis=0)])
    weight_vec = np.array([float(weights[t]) for t in have], dtype=float)
    cash_ret = (1.0 + cash_annual_rate) ** (horizon / TRADING_DAYS) - 1.0 if cash_annual_rate else 0.0
    pnls, starts, ends, failures = [], [], [], []
    n_possible = 0
    # a horizon-session move needs grid points at i and i+horizon, so the last usable start is len-horizon-1
    for i in range(offset, max(len(sessions) - horizon, 0), step):
        t0, t1 = sessions[i], sessions[i + horizon]
        n_possible += 1
        if missing_cols:
            failures.append({"start": str(t0.date()), "end": str(t1.date()), "reason": f"no price column for {', '.join(missing_cols)}"})
            continue
        # A horizon is defined on the intact session grid. Requiring the complete path prevents a missing
        # intermediate bar from being hidden inside what is labelled an uninterrupted N-session observation.
        # This is deliberately stricter than endpoint-only arithmetic: with incomplete path evidence the
        # scenario is unavailable rather than silently treated as fully observed.
        invalid_counts = prefix_bad[i + horizon + 1] - prefix_bad[i]
        bad = [t for j, t in enumerate(have) if invalid_counts[j] > 0]
        if bad:
            row_bad = (~valid[i:i + horizon + 1]).any(axis=1)
            bad_dates = [str(pd.Timestamp(d).date()) for d in sessions[i:i + horizon + 1][row_bad]]
            failures.append({"start": str(t0.date()), "end": str(t1.date()),
                             "reason": f"missing/invalid bar for {', '.join(bad[:6])} inside the full "
                                       f"{horizon}-session path",
                             "invalid_dates": bad_dates[:8]})
            continue
        asset_move = (values[i + horizon] / values[i]) - 1.0           # cumulative, frozen over the window
        pnls.append(float(asset_move @ weight_vec + cash_weight * cash_ret))
        starts.append(t0)
        ends.append(t1)
    return WindowSet(pd.Series(pnls, index=pd.DatetimeIndex(ends) if ends else None), starts, ends, horizon,
                     n_possible, failures, cash_weight, cash_ret)


def daily_rebalanced_pnl(px: pd.DataFrame, weights: dict, sessions: pd.DatetimeIndex, horizon: int,
                         offset: int = 0, step: int | None = None) -> pd.Series:
    """The *incorrect* comparison series: compound the portfolio's daily return, i.e. rebalance every day.

    Kept only so the tests can show the two differ, and by how much.
    """
    step = horizon if step is None else step
    names = [t for t in weights if weights[t] > 0 and t in px.columns]
    p = px[names].apply(pd.to_numeric, errors="coerce").reindex(sessions)
    r = p.pct_change(fill_method=None)
    w = pd.Series({t: float(weights[t]) for t in names})
    port = (r * w).sum(axis=1, min_count=len(names))
    out, ends = [], []
    for i in range(offset, max(len(sessions) - horizon + 1, 0), step):
        seg = port.iloc[i + 1: i + horizon + 1]
        if seg.isna().any() or len(seg) < horizon:
            continue
        out.append(float((1 + seg).prod() - 1))
        ends.append(sessions[i + horizon])
    return pd.Series(out, index=pd.DatetimeIndex(ends) if ends else None)


# ----------------------------------------------------------------------------- estimators
def var_es(pnl: pd.Series, level: float) -> tuple[float | None, float | None, int]:
    """(VaR, ES, tail count) at ``level`` confidence, as positive loss fractions of NAV."""
    s = pnl.dropna()
    if len(s) < 2:
        return None, None, 0
    q = float(s.quantile(1.0 - level))
    tail = s[s <= q]
    return -q, (-float(tail.mean()) if len(tail) else None), int(len(tail))


def block_bootstrap_ci(pnl: pd.Series, level: float, statistic: str = "var", block: int | None = None,
                       reps: int = 2000, seed: int = 20260903, ci: float = 0.90,
                       min_block: int = 1) -> dict:
    """Interval around a VaR/ES point estimate, resampling contiguous blocks of the P&L series.

    ``min_block`` matters when ``pnl`` holds *overlapping* horizon-day windows. Neighbouring observations then
    share ``horizon - 1`` sessions, so dependence spans the whole horizon; the usual n**(1/3) rule gives ~6 for
    a 242-window year and splices together windows that overlapped in the original data, understating the
    interval. Callers must pass ``min_block >= horizon``.
    """
    s = pnl.dropna().to_numpy()
    n = len(s)
    if n < 20:
        return {"available": False, "reason": f"only {n} observations; need at least 20"}
    auto = max(2, round(n ** (1 / 3)))
    block = int(block or max(auto, min_block))
    if block >= n:
        return {"available": False, "reason": f"block length {block} is not shorter than the sample ({n})"}
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    stats = np.empty(reps)
    for r in range(reps):
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        sample = np.concatenate([s[i:i + block] for i in starts])[:n]
        q = np.quantile(sample, 1.0 - level)
        if statistic == "es":
            tail = sample[sample <= q]
            stats[r] = -tail.mean() if tail.size else np.nan
        else:
            stats[r] = -q
    lo, hi = np.nanpercentile(stats, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return {"available": True, "statistic": statistic, "level": level, "ci": ci, "lo": float(lo), "hi": float(hi),
            "point": float(np.nanmedian(stats)), "block_length": block, "auto_block_rule": auto,
            "min_block_required": min_block, "reps": reps, "seed": seed,
            "method": "moving-block bootstrap on the P&L series (contiguous blocks, sampled with replacement)",
            "note": "interval around the ESTIMATE; distinct from the VaR confidence level"}


def asset_shock_bootstrap_ci(returns: pd.DataFrame, weights: dict, horizon: int, level: float,
                             statistic: str = "var", block: int | None = None, reps: int = 2000,
                             seed: int = 20260903, ci: float = 0.90, cash_weight: float = 0.0,
                             cash_return: float = 0.0) -> dict:
    """Preferred interval: resample the underlying **daily asset shocks**, then rebuild frozen P&L.

    Bootstrapping an overlapping horizon-day P&L series directly resamples objects that already overlap each
    other, so no block length fully repairs the dependence. Resampling the daily asset return matrix instead
    and re-deriving horizon-day frozen P&L from the synthetic path reproduces the estimator on each replicate,
    including the overlap structure, cross-sectional correlation and the frozen-weight combination.

    Blocks are contiguous runs of an uninterrupted daily return matrix, which preserves local dependence and
    volatility clustering. If invalid session slots were deleted to make that matrix, the interval is
    unavailable: resampling would otherwise make observations separated by a gap adjacent. The block length
    remains disclosed; it need not exceed the horizon because horizon-day overlap is rebuilt, not resampled.
    """
    if bool(returns.isna().any(axis=None)):
        return {"available": False,
                "reason": "the daily asset-shock matrix contains missing values; rows are not silently dropped "
                          "before block resampling"}
    if returns.attrs.get("uninterrupted") is False or int(returns.attrs.get("invalid_return_slots", 0)) > 0:
        return {"available": False,
                "reason": "the daily asset-shock matrix was formed after one or more invalid session slots; "
                          "block resampling would make separated observations adjacent"}
    R = returns
    names = [t for t in weights if weights[t] > 0 and t in R.columns]
    n = len(R)
    if n < max(40, horizon * 4) or not names:
        return {"available": False, "reason": f"only {n} valid daily rows for {len(names)} names; "
                                              f"need at least {max(40, horizon * 4)}"}
    w = np.array([float(weights[t]) for t in names])
    M = R[names].to_numpy(float)
    block = int(block or max(2, round(n ** (1 / 3))))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    stats = np.empty(reps)
    for rep in range(reps):
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        path = np.concatenate([M[i:i + block] for i in starts])[:n]          # synthetic daily asset shocks
        # n shocks describe n+1 price points: the starting state plus one after each shock. Without the
        # leading 1.0 the first window is silently lost and the replicate holds n-h windows instead of
        # n-h+1, which biases the sample the estimator is supposed to reproduce.
        gross = np.vstack([np.ones((1, path.shape[1])), np.cumprod(1.0 + path, axis=0)])
        cum = gross[horizon:] / gross[:-horizon] - 1.0                       # n - h + 1 frozen windows
        pnl = cum @ w + cash_weight * cash_return
        q = np.quantile(pnl, 1.0 - level)
        if statistic == "es":
            tail = pnl[pnl <= q]
            stats[rep] = -tail.mean() if tail.size else np.nan
        else:
            stats[rep] = -q
    lo, hi = np.nanpercentile(stats, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return {"available": True, "statistic": statistic, "level": level, "ci": ci, "lo": float(lo), "hi": float(hi),
            "point": float(np.nanmedian(stats)), "block_length": block, "reps": reps, "seed": seed,
            "horizon_days": horizon, "n_daily_rows": n, "n_names": len(names),
            "windows_per_replicate": int(n - horizon + 1),
            "method": "block bootstrap of DAILY ASSET shocks, with frozen horizon-day P&L rebuilt on each "
                      "replicate (the estimator is reproduced, not the overlapping output series)",
            "why_preferred": "resampling the overlapping horizon-day P&L directly resamples observations that "
                             "already share horizon-1 sessions with each other; no block length repairs that",
            "note": "interval around the ESTIMATE; distinct from the VaR confidence level"}


def phases(px: pd.DataFrame, weights: dict, sessions: pd.DatetimeIndex, horizon: int, level: float,
           cash_weight: float = 0.0, cash_annual_rate: float = 0.0) -> dict:
    """Every non-overlapping phase (offset 0..horizon-1), so the reader sees the spread, not one lucky cut."""
    rows = []
    for off in range(horizon):
        ws = frozen_pnl(px, weights, sessions, horizon, offset=off, step=horizon,
                        cash_weight=cash_weight, cash_annual_rate=cash_annual_rate)
        v, e, tail = var_es(ws.pnl, level)
        rows.append({"phase": off, "n_obs": ws.n, "var": v, "es": e, "tail_obs": tail,
                     "n_failed": len(ws.failures)})
    vals = [r["var"] for r in rows if r["var"] is not None]
    es_vals = [r["es"] for r in rows if r["es"] is not None]
    return {"phases": rows, "n_phases": horizon,
            "var_range": [min(vals), max(vals)] if vals else None,
            "es_range": [min(es_vals), max(es_vals)] if es_vals else None,
            "var_median": float(np.median(vals)) if vals else None,
            "es_median": float(np.median(es_vals)) if es_vals else None}


# ----------------------------------------------------------------------------- provenance
def _sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def calculator_hash() -> str:
    """SHA-256 over **every** module whose source can change a number reported here.

    Hashing only this file left the orchestration - window selection, layer construction, tracking error,
    bootstrap wiring - outside the fingerprint, so a figure could change while its calculator hash stayed put.
    """
    here = pathlib.Path(__file__).parent
    parts = []
    for name in sorted(CALCULATOR_MODULES):
        p = here / name
        parts.append(f"{name}:{_sha(p.read_bytes()) if p.exists() else 'MISSING'}")
    return _sha("|".join(parts).encode())[:16]


def calculator_manifest() -> dict:
    """Per-module hashes behind :func:`calculator_hash`, so a change can be localised."""
    here = pathlib.Path(__file__).parent
    return {"combined": calculator_hash(),
            "modules": {n: (_sha((here / n).read_bytes())[:16] if (here / n).exists() else "MISSING")
                        for n in sorted(CALCULATOR_MODULES)}}


def data_snapshot_hash(px: pd.DataFrame, sessions, columns: list[str] | None = None) -> str:
    """Content hash of the **shared market-data snapshot**: the full confirmed panel.

    This identifies the *input data*, so every construction priced off one snapshot must get one value. It is
    deliberately computed over the whole confirmed session grid and all panel columns - building it from a
    construction's own usable dates would make the shared snapshot appear to differ per construction, which is
    exactly the identity confusion it exists to prevent. What distinguishes constructions is
    :func:`weights_hash`; what distinguishes their samples is :func:`calculation_sample_hash`.
    """
    if sessions is None or not len(sessions):
        return "empty"
    cols = sorted(columns) if columns else sorted(px.columns)
    sub = px.loc[px.index.isin(sessions), [c for c in cols if c in px.columns]].astype("float64")
    return _sha(pd.util.hash_pandas_object(sub.fillna(-1.0), index=True).values.tobytes())[:16]


def calculation_sample_hash(px: pd.DataFrame, names: list[str], sessions) -> str:
    """Content hash of the exact rows and columns ONE construction/window actually consumed.

    Distinct from :func:`data_snapshot_hash`: the snapshot is shared, the calculation sample is not.
    """
    if sessions is None or not len(sessions) or not names:
        return "empty"
    cols = sorted(t for t in names if t in px.columns)
    sub = px.loc[px.index.isin(sessions), cols].astype("float64")
    return _sha(pd.util.hash_pandas_object(sub.fillna(-1.0), index=True).values.tobytes())[:16]


def weights_hash(weights: dict) -> str:
    """Content hash of one construction's target weights - what actually differs between constructions."""
    import json as _j
    payload = _j.dumps({str(k): round(float(v), 10) for k, v in sorted(weights.items())}, sort_keys=True)
    return _sha(payload.encode())[:16]


def ex_ante_vol(weights: dict, cov: pd.DataFrame) -> float | None:
    """Annualised ex-ante volatility of a fully-invested book, sqrt(w' S w). None if not computable."""
    names = [t for t in weights if t in cov.index and weights[t] > 0]
    if len(names) < 1:
        return None
    w = np.array([float(weights[t]) for t in names])
    tot = w.sum()
    if tot <= 0:
        return None
    w = w / tot
    S = cov.loc[names, names].to_numpy(float)
    v = float(w @ S @ w)
    return float(np.sqrt(v)) if v > 0 else None
