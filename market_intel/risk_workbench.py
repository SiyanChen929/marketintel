"""Risk workbench (P1 W10): exposures, active weights, historical VaR / CVaR, beta stress, liquidity, event concentration and
pre-trade compliance for the canonical run's executable target weights.

Everything reconciles to the run: executable weights (recommended construction scaled to the vol target) + cash = 1;
model weights are the fully-invested primary construction the constraints apply to. Long-only, no derivatives, no ledger:
gross = net = invested, leverage 1.0, current live weights unknown."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, research

BENCHMARK_FILE = config.PROJECT_ROOT / "data" / "benchmark_weights.csv"
VOLUME_FILE = config.PROJECT_ROOT / "data_cache" / "prices" / "long_volume.parquet"
TAIWAN_CHINA_COUNTRIES = {"Taiwan", "China", "Hong Kong"}
TAIWAN_CHINA_TICKERS = {"TSM", "UMC"}
AI_CAPEX_SEGMENT_KEYS = ("Compute", "Memory", "Equipment", "Foundry", "Interconnect", "Servers", "Power")
STRESS_SCENARIOS = (("SOXX -20%", "SOXX", -0.20), ("SOXX -35%", "SOXX", -0.35), ("SPY -10%", "SPY", -0.10), ("NVDA -25%", "NVDA", -0.25), ("USD +5%", "USD", 0.05))
VAR_WINDOW = 252
VAR_WINDOWS = {"1y": 252, "3y": 756}      # 99% VaR on one year is ~2-3 tail observations; read the 3-year figure
HEADLINE_WINDOW = "3y"
ES_LEVEL = 0.975                          # confidence level; see es_level_note - not a regulatory FRTB calc
HORIZONS = (1, 10)                        # 1-day and 10-day (Basel regulatory) holding periods
TE_INDEX = "SOXX"                         # index proxy for tracking error, alongside the mandate benchmark
MIN_VAR_DAYS = 60
EVENT_DAYS = 14
EPS = 1e-6
OFFICIAL_BOOTSTRAP_REPS = 2000          # the only precision that may be quoted alongside the main figures


def _val(x, default=None):
    """Scalar from a frame cell: NaN / NaT -> default, numpy scalars -> python."""
    if x is None:
        return default
    try:
        if pd.isna(x):
            return default
    except (TypeError, ValueError):
        pass
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return float(x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    return x


def _r(x, n=6):
    return None if x is None else round(float(x), n)


def _attr(uni: pd.DataFrame, t: str, col: str, default=None):
    if uni is None or uni.empty or t not in uni.index or col not in uni.columns:
        return default
    return _val(uni.loc[t, col], default)


# ----------------------------------------------------------------------------- benchmark
def benchmark_weights(uni: pd.DataFrame, path: Path | None = None) -> tuple[dict[str, float], dict]:
    """Analyst benchmark from data/benchmark_weights.csv (ticker, weight) when present; else equal-weight eligible universe."""
    path = path or BENCHMARK_FILE
    if path.exists():
        try:
            df = pd.read_csv(path)
            if {"ticker", "weight"} <= set(df.columns):
                w = df.assign(ticker=df["ticker"].astype(str).str.upper().str.strip()).groupby("ticker")["weight"].sum().astype(float)
                w = w[w > 0]
                if len(w):
                    w = w / w.sum()
                    return w.round(8).to_dict(), {"name": f"analyst benchmark ({path.name})", "source": str(path), "n": int(len(w)), "custom": True,
                                                   "note": f"weights read from {path.name} (ticker, weight) and normalised to sum to 1"}
        except Exception as e:                                                          # malformed file: fall back, say so
            return _equal_weight(uni, f"{path.name} unreadable ({e}); ")
    return _equal_weight(uni, "")


def _equal_weight(uni: pd.DataFrame, prefix: str) -> tuple[dict[str, float], dict]:
    if uni is None or uni.empty:
        return {}, {"name": "equal-weight eligible universe", "source": "default", "n": 0, "custom": False, "note": prefix + "universe not available"}
    if "eligible" in uni.columns:
        elig = uni["eligible"].fillna(False).astype(bool)
        names = [str(t) for t in uni.index[elig.values]]
    else:
        names = []
    if not names:
        names = [str(t) for t in uni.index]
    return {t: 1.0 / len(names) for t in names}, {"name": "equal-weight eligible universe", "source": "default", "n": len(names), "custom": False,
                                                    "note": prefix + "no data/benchmark_weights.csv; equal weight over the run's eligible universe (universe_scores.csv eligible=True)"}


# ----------------------------------------------------------------------------- exposures
def _buckets(hold: dict, model: dict, bench: dict, key) -> list[dict]:
    names = set(hold) | set(bench)
    keys = {key(t) for t in names}
    rows = []
    for k in sorted(keys, key=lambda s: str(s)):
        members = [t for t in names if key(t) == k]
        w = sum(hold.get(t, 0.0) for t in members)
        m = sum(model.get(t, 0.0) for t in members)
        b = sum(bench.get(t, 0.0) for t in members)
        rows.append({"name": k, "weight": _r(w), "model": _r(m), "benchmark": _r(b), "active": _r(w - b), "active_model": _r(m - b),
                     "n_target": sum(1 for t in members if hold.get(t, 0.0) > 0), "n_benchmark": sum(1 for t in members if bench.get(t, 0.0) > 0),
                     "tickers": sorted(t for t in members if hold.get(t, 0.0) > 0)})
    rows.sort(key=lambda r: -(r["weight"] or 0.0))
    return rows


def _flag_bucket(hold: dict, model: dict, bench: dict, pred, label: str) -> dict:
    members = sorted(t for t in set(hold) | set(bench) if pred(t))
    return {"name": label, "weight": _r(sum(hold.get(t, 0.0) for t in members)), "model": _r(sum(model.get(t, 0.0) for t in members)),
            "benchmark": _r(sum(bench.get(t, 0.0) for t in members)), "tickers": [t for t in members if hold.get(t, 0.0) > 0],
            "active": _r(sum(hold.get(t, 0.0) for t in members) - sum(bench.get(t, 0.0) for t in members))}


def exposures(hold: dict, model: dict, bench: dict, uni: pd.DataFrame) -> dict:
    seg = lambda t: _attr(uni, t, "segment", "unknown") or "unknown"
    country = lambda t: _attr(uni, t, "country", "unknown") or "unknown"
    ccy = lambda t: (_attr(uni, t, "financial_currency", "unknown") or "unknown")
    is_adr = lambda t: bool(_attr(uni, t, "is_adr", False))
    tw = lambda t: t in TAIWAN_CHINA_TICKERS or country(t) in TAIWAN_CHINA_COUNTRIES
    capex = lambda t: any(k.lower() in str(seg(t)).lower() for k in AI_CAPEX_SEGMENT_KEYS)
    return {"segment": _buckets(hold, model, bench, seg), "country": _buckets(hold, model, bench, country), "currency": _buckets(hold, model, bench, ccy),
            "adr_share": _flag_bucket(hold, model, bench, is_adr, "ADR"),
            "taiwan_china": {**_flag_bucket(hold, model, bench, tw, "Taiwan-China"), "definition": "country in Taiwan / China / Hong Kong, or ticker in TSM, UMC"},
            "ai_capex_dependency": {**_flag_bucket(hold, model, bench, capex, "AI-capex dependency"),
                                    "definition": "segment name contains " + ", ".join(AI_CAPEX_SEGMENT_KEYS)}}


# ----------------------------------------------------------------------------- VaR / betas / stress
def window_returns(px: pd.DataFrame, names: list[str], window: int = VAR_WINDOW) -> pd.DataFrame:
    """Last ``window`` daily simple returns over the common observed window of ``names`` (no fills)."""
    cols = [t for t in names if t in px.columns]
    if not cols or px.empty:
        return pd.DataFrame()
    p = px[cols].apply(pd.to_numeric, errors="coerce").dropna(how="any")
    r = p.pct_change(fill_method=None).dropna(how="any")
    return r.tail(window)


def multi_day_returns(pr: pd.Series, horizon: int, overlapping: bool = True) -> pd.Series:
    """Compound ``pr`` into ``horizon``-day returns.

    Overlapping windows keep the sample large but are serially dependent by construction (each observation
    shares 9 of its 10 days with its neighbour), so the effective sample - and therefore the confidence in a
    99% quantile - is far smaller than the observation count suggests. Non-overlapping windows are
    statistically clean but leave very few observations. Both are reported; neither is silently preferred.
    """
    if horizon <= 1:
        return pr
    if overlapping:
        return ((1 + pr).rolling(horizon).apply(np.prod, raw=True) - 1).dropna()
    n = len(pr) - len(pr) % horizon
    if n < horizon:
        return pd.Series(dtype=float)
    blocks = np.asarray(pr.iloc[:n]).reshape(-1, horizon)
    return pd.Series((1 + blocks).prod(axis=1) - 1)


# ----------------------------------------------------------------------------- acquisition provenance
def raw_run(path: Path | None = None) -> dict:
    """The **full** run manifest from disk.

    ``config.run_info()`` is a UI projection: it drops ``data_cutoff`` (which carries the acquisition
    provenance finality depends on) and ``portfolio.positions`` (which carries the weights the run actually
    landed). Anything that needs either must read the manifest itself.
    """
    import json
    p = path or config.run_file(config.LATEST / "run.json")
    try:
        return json.loads(p.read_text())
    except Exception:                                                        # pragma: no cover - absent/malformed
        return {}


def price_acquisition(run: dict | None = None) -> tuple[datetime | None, dict]:
    """When the price panel was fetched. This, not the wall clock, decides which closes are final.

    Reads ``run.json -> data_cutoff.fetched_at.prices``. Absent provenance is reported as such and finality
    fails closed downstream; it is never replaced by ``datetime.now()``.
    """
    run = run if run is not None else raw_run()
    dc = run.get("data_cutoff") or (raw_run().get("data_cutoff") or {})
    raw = (dc.get("fetched_at") or {}).get("prices")
    if not raw:
        return None, {"available": False, "source": "run.json data_cutoff.fetched_at.prices", "raw": None,
                      "reason": "the run declares no price acquisition timestamp; close finality is unknowable"}
    try:
        ts = pd.Timestamp(raw).to_pydatetime()
    except Exception as e:                                                   # pragma: no cover - malformed run.json
        return None, {"available": False, "source": "run.json data_cutoff.fetched_at.prices", "raw": str(raw),
                      "reason": f"unparseable acquisition timestamp ({e})"}
    return ts, {"available": True, "source": "run.json data_cutoff.fetched_at.prices", "raw": str(raw),
                "acquired_at": ts.isoformat(), "declared_price_date": dc.get("prices"),
                "note": "finality is derived from this instant; re-reading the same snapshot later cannot change it"}


def _span(grid: pd.DatetimeIndex, need: int, px: pd.DataFrame, names: list[str],
          require_complete: bool = False) -> tuple[pd.DatetimeIndex | None, dict]:
    """Return the last ``need`` reference sessions without ever compressing their grid.

    Structural history coverage is mandatory for every window: each instrument must already exist at the
    requested span's first session. For a 3Y claim, ``require_complete`` additionally requires a finite,
    positive bar on all 756 sessions. Shorter windows may retain isolated gaps, but downstream observations are
    built on this intact grid and any path touching a gap is rejected. No gap can extend or compress a span.
    """
    names = [str(t) for t in names]
    if len(grid) < need:
        return None, {"available": False, "n_sessions_available": int(len(grid)),
                      "n_reference_sessions": int(len(grid)),
                      "reason": f"the reference session grid holds only {len(grid)} confirmed sessions; {need} are required"}
    missing_columns = sorted(t for t in names if t not in px.columns)
    if missing_columns:
        return None, {"available": False, "n_sessions_available": 0,
                      "n_reference_sessions": int(len(grid)), "missing_columns": missing_columns,
                      "limiting_holding": missing_columns[0],
                      "reason": f"required price column(s) are absent for {', '.join(missing_columns[:8])}; "
                                f"the {need}-session window is unavailable and no weight is dropped"}
    win = grid[-need:]
    sub = px[names].apply(pd.to_numeric, errors="coerce").reindex(grid) if names else pd.DataFrame(index=grid)
    valid = sub.notna() & np.isfinite(sub) & (sub > 0)
    common = valid.all(axis=1) if names else pd.Series(False, index=grid)
    trailing = 0
    for ok in reversed(common.tolist()):
        if not ok:
            break
        trailing += 1
    exact = valid.reindex(win)
    bad_by_name = {t: [str(pd.Timestamp(d).date()) for d in exact.index[~exact[t].values]]
                   for t in names if not bool(exact[t].all())}
    firsts = {t: sub[t].where(valid[t]).first_valid_index() for t in names}
    late = {t: firsts[t] for t in names if firsts[t] is None or firsts[t] > win[0]}
    if late:
        limiting = max(late, key=lambda t: (late[t] is None, late[t] or pd.Timestamp.min))
        return None, {"available": False, "n_sessions_available": int(common.sum()),
                      "n_trailing_uninterrupted_sessions": int(trailing),
                      "n_reference_sessions": int(len(grid)),
                      "span_start": str(win[0].date()), "span_end": str(win[-1].date()),
                      "limiting_holding": limiting,
                      "limiting_first_bar": (str(firsts[limiting].date()) if firsts[limiting] is not None else None),
                      "n_holdings_without_full_span": len(late),
                      "holdings_without_full_span": sorted(late)[:8],
                      "reason": f"{len(late)} holding(s) do not have structural history from the requested "
                                f"span start {win[0].date()}; {limiting} first prices "
                                f"{firsts[limiting].date() if firsts[limiting] is not None else 'never'}. The "
                                f"window is not shortened and no earlier/later sample is substituted - it is unavailable."}
    if require_complete and bad_by_name:
        limiting = min(bad_by_name, key=lambda t: int(valid[t].iloc[-need:].sum()))
        return None, {"available": False, "n_sessions_available": int(common.reindex(win).sum()),
                      "n_trailing_uninterrupted_sessions": int(trailing),
                      "n_reference_sessions": int(len(grid)),
                      "span_start": str(win[0].date()), "span_end": str(win[-1].date()),
                      "limiting_holding": limiting,
                      "limiting_first_bar": (str(firsts[limiting].date()) if firsts[limiting] is not None else None),
                      "n_holdings_without_full_span": len(bad_by_name),
                      "holdings_without_full_span": sorted(bad_by_name)[:8],
                      "invalid_dates_sample": {t: ds[:8] for t, ds in list(sorted(bad_by_name.items()))[:8]},
                      "reason": f"the exact {need}-session span has missing/invalid bars for "
                                f"{', '.join(sorted(bad_by_name)[:8])}; only {trailing} trailing common sessions "
                                f"are uninterrupted. The window is not shortened, gaps are not dropped, and "
                                f"separated observations are not bridged - it is unavailable."}
    return win, {"available": True, "span_start": str(win[0].date()), "span_end": str(win[-1].date()),
                 "n_sessions_used": int(len(win)),
                 "n_sessions_available": int(common.reindex(win).sum()),
                 "n_trailing_uninterrupted_sessions": int(trailing), "n_reference_sessions": int(len(grid)),
                 "n_common_priced_sessions": int(common.reindex(win).sum()),
                 "data_complete": not bool(bad_by_name),
                 "invalid_dates_sample": {t: ds[:8] for t, ds in list(sorted(bad_by_name.items()))[:8]},
                 "basis": "the last N confirmed reference sessions on an intact grid; every return/P&L path "
                          "touching a missing bar fails after window construction, never through grid compression"}


def _te_block(px, hold: dict, bench: dict, label: str, source: str, grid: pd.DatetimeIndex,
              need: int, cash_w: float, rf: float, kind: str = "research proxy") -> dict:
    """Tracking error against one complete benchmark on its own strict requested sample.

    SOXX and the mandate benchmark do not share a pre-filtered intersection. Each independently proves that
    every portfolio and benchmark instrument has the full requested span. No missing constituent is discarded
    and benchmark weights are never silently renormalised after a coverage failure.
    """
    from . import risk_metrics as RM
    hold_names = [t for t, w in hold.items() if w > 0]
    bench_names = [t for t, w in bench.items() if w > 0]
    missing_hold = sorted(t for t in hold_names if t not in px.columns)
    missing_bench = sorted(t for t in bench_names if t not in px.columns)
    base = {"benchmark": label, "kind": kind, "source": source, "requested_sessions": int(need),
            "sample_independent": True,
            "sample_policy": "benchmark-specific intact grid; only genuinely adjacent paired observations enter; "
                             "no constituent drops, gap compression or post-coverage weight renormalisation"}
    if not hold_names or not bench_names:
        return {**base, "available": False,
                "reason": "no positive-weight holdings" if not hold_names else "no positive-weight benchmark constituents"}
    if missing_hold or missing_bench:
        role = (f"portfolio holdings {', '.join(missing_hold[:8])}" if missing_hold else
                f"benchmark constituents {', '.join(missing_bench[:8])}")
        return {**base, "available": False, "missing_portfolio_members": missing_hold,
                "missing_benchmark_members": missing_bench,
                "reason": f"missing price columns for {role}; no member was dropped and no weights were renormalised"}
    bsum = float(sum(float(bench[t]) for t in bench_names))
    if not np.isfinite(bsum) or abs(bsum - 1.0) > EPS:
        return {**base, "available": False, "declared_weight_sum": _r(bsum),
                "reason": f"benchmark weights sum to {bsum:.8f}, not 1; weights are not silently renormalised"}
    required = list(dict.fromkeys(hold_names + bench_names))
    win, span = _span(grid, need, px, required, require_complete=False)
    if win is None:
        return {"benchmark": label, "kind": kind, "source": source, "available": False,
                **base, **{k: v for k, v in span.items() if k != "available"},
                "reason": f"benchmark-specific sample unavailable: {span.get('reason', 'incomplete span')}"}
    bw = {t: float(bench[t]) for t in bench_names}
    ph = RM.frozen_pnl(px, hold, win, horizon=1, step=1, cash_weight=cash_w, cash_annual_rate=rf)
    bh = RM.frozen_pnl(px, bw, win, horizon=1, step=1)
    j = pd.concat([ph.pnl.rename("p"), bh.pnl.rename("b")], axis=1, join="inner")
    if len(j) < MIN_VAR_DAYS:
        return {**base, "available": False,
                "reason": f"only {len(j)} paired one-session observations inside this benchmark's strict window "
                          f"(need {MIN_VAR_DAYS})",
                "n_days": int(len(j)), "n_failed_portfolio": len(ph.failures), "n_failed_benchmark": len(bh.failures)}
    active = j["p"] - j["b"]
    return {**base, "available": True,
            "tracking_error_ann": _r(float(active.std(ddof=1) * np.sqrt(252))),
            "mean_active_ann": _r(float(active.mean() * 252)),
            "start": str(j.index[0].date()), "end": str(j.index[-1].date()), "n_days": int(len(j)),
            "coverage": {"holdings_priced": len(hold_names), "holdings_total": int(sum(1 for w in hold.values() if w > 0)),
                         "benchmark_priced": len(bench_names), "benchmark_total": int(sum(1 for w in bench.values() if w > 0)),
                         "sessions_in_window": int(len(win)), "paired_observations": int(len(j)),
                         "portfolio_failed_sessions": len(ph.failures), "benchmark_failed_sessions": len(bh.failures)},
            "definition": "annualised sample stdev (ddof=1) of one-session (portfolio - benchmark) return, frozen "
                          "weights, computed on this benchmark's independent intact grid; observations touching a "
                          "missing bar are rejected after adjacency is defined and are never bridged"}


def _te_set(px, hold: dict, bench: dict, bench_meta: dict, grid: pd.DatetimeIndex, need: int,
            cash_w: float, rf: float) -> list[dict]:
    """Every tracking-error line for one window: the formal mandate slot, then the research proxies."""
    out = []
    formal = bool((bench_meta or {}).get("custom"))
    if not formal:
        out.append({"benchmark": "mandate benchmark", "kind": "formal mandate", "available": False,
                    "status": "NOT AVAILABLE",
                    "reason": "no formal mandate benchmark is defined for this book (data/benchmark_weights.csv "
                              "is absent), so the mandate's active risk cannot be computed",
                    "forbidden_use": "tracking error against a research proxy must not be presented, or read, as "
                                     "tracking error against a mandate benchmark"})
    if bench:
        out.append(_te_block(px, hold, bench,
                             "mandate benchmark" if formal else "equal-weight current-universe proxy",
                             (bench_meta or {}).get("source") or (bench_meta or {}).get("note") or "unspecified",
                             grid, need, cash_w, rf, kind=("formal mandate" if formal else "research proxy")))
    out.append(_te_block(px, hold, {TE_INDEX: 1.0}, f"{TE_INDEX} index proxy", f"{TE_INDEX} ETF close series",
                         grid, need, cash_w, rf, kind="research proxy"))
    return out


def var_block(px: pd.DataFrame, hold: dict, bench: dict | None = None, windows: dict | None = None,
              bench_meta: dict | None = None, cash_weight: float = 0.0, rf: float = 0.0,
              acquired_at: datetime | None = None, bootstrap_reps: int = 2000,
              acq_meta: dict | None = None) -> tuple[dict, pd.DataFrame]:
    """Frozen-position historical VaR / ES / tracking error. See market_intel/risk_metrics.py for the method.

    No preferred or headline estimate is published: the risk policy that would choose between overlapping,
    non-overlapping and sqrt-time has not been agreed, so all are reported side by side with their sample sizes.
    """
    from . import risk_metrics as RM

    windows = windows or VAR_WINDOWS
    bench, bench_meta = (bench or {}), (bench_meta or {})
    names = [t for t in hold if hold[t] > 0]
    priced = [t for t in names if t in px.columns]
    missing = [t for t in names if t not in px.columns]
    grid, cal = RM.session_index(px, TE_INDEX, acquired_at, source=(acq_meta or {}).get("source", "unspecified"))
    base = {"schema_version": RM.SCHEMA_VERSION,
            "method": "historical scenario P&L on FROZEN positions: each holding's cumulative price change over the "
                      "window, combined once with the current weights; positions are not rebalanced inside the window",
            "positions_frozen": True, "sign": "losses as positive fractions of NAV",
            "es_level": ES_LEVEL,
            "es_level_note": "97.5% is the confidence level used as one input to FRTB; this is a plain historical "
                             "ES and is NOT a regulatory FRTB calculation (no liquidity-horizon scaling, no stress "
                             "calibration, no desk-level model approval)",
            "var_levels": [0.95, 0.99], "horizons_days": list(HORIZONS),
            "cash": {"weight": _r(cash_weight), "annual_rate": _r(rf), "treatment": "cash earns the run's risk-free rate over the horizon"},
            "names_priced": priced, "missing": missing, "weight_priced": _r(sum(hold[t] for t in priced)),
            "session_index": cal, "acquisition": acq_meta, "no_preferred_estimate": True}
    if not grid.size or not priced:
        reason = (cal.get("finality", {}).get("reason") or "no confirmed sessions") if not grid.size else "no priced holdings"
        return {**base, "available": False, "reason": reason, "windows": {}, "tracking_error": []}, pd.DataFrame()
    out_windows = {}
    for label, need in windows.items():
        win, span = _span(grid, need, px, names, require_complete=(need >= VAR_WINDOWS["3y"]))
        if win is None:
            out_windows[label] = {"requested_sessions": need, "available": False, "horizons": {},
                                  "tracking_error": [{"benchmark": "all", "available": False,
                                                      "reason": "the window itself is unavailable"}], **span}
            continue
        hz = {}
        # daily asset returns on the FULL window grid - adjacency is guaranteed, invalid rows dropped after
        day_ret, _px_grid, ret_cov = RM.grid_returns(px, priced, win)
        for h in HORIZONS:
            ov = RM.frozen_pnl(px, hold, win, h, offset=0, step=1, cash_weight=cash_weight, cash_annual_rate=rf)
            entry = {"horizon_days": h, "overlapping": None, "non_overlapping_phases": None, "sqrt_time_reference": None}
            v95, es95, t95 = RM.var_es(ov.pnl, 0.95)
            v99, es99, t99 = RM.var_es(ov.pnl, 0.99)
            _v, es975, t975 = RM.var_es(ov.pnl, ES_LEVEL)
            entry["overlapping"] = {"n_obs": ov.n, "n_possible_windows": ov.n_possible, "n_failed_windows": len(ov.failures),
                                    "non_overlap_equivalent_count": (int(ov.n / h) if h > 1 else ov.n),
                                    "non_overlap_equivalent_count_definition":
                                        ("floor(n_windows / horizon). A crude count heuristic for how many disjoint "
                                         "windows the same span would hold - NOT a statistical effective sample size "
                                         "(no autocorrelation estimate enters it)." if h > 1 else "independent daily observations"),
                                    "var_95": _r(v95), "var_99": _r(v99), "es_95": _r(es95), "es_975": _r(es975), "es_99": _r(es99),
                                    "tail_obs_99": t99, "tail_obs_975": t975,
                                    "dependence": ("each window shares %d of %d sessions with its neighbour" % (h - 1, h)) if h > 1 else "independent daily observations",
                                    "failures_sample": ov.failures[:3]}
            if ov.n:
                entry["overlapping"]["worst_day"] = {"date": str(pd.Timestamp(ov.pnl.idxmin()).date()), "return": _r(float(ov.pnl.min()))}
                entry["overlapping"]["best_day"] = {"date": str(pd.Timestamp(ov.pnl.idxmax()).date()), "return": _r(float(ov.pnl.max()))}
            if h > 1:
                ph = RM.phases(px, hold, win, h, 0.99, cash_weight, rf)
                ph975 = RM.phases(px, hold, win, h, ES_LEVEL, cash_weight, rf)
                per_phase = []
                for pv, pe in zip(ph["phases"], ph975["phases"]):
                    per_phase.append({"phase": pv["phase"], "n_windows": pv["n_obs"], "var_99": _r(pv["var"]),
                                      "es_975": _r(pe["es"]), "tail_obs_99": pv["tail_obs"], "n_failed": pv["n_failed"]})
                nw = [x["n_windows"] for x in per_phase]
                entry["non_overlapping_phases"] = {
                    "n_phases": h, "phases": per_phase,
                    "n_windows_per_phase_range": [min(nw), max(nw)] if nw else None,
                    "n_windows_total_across_alternative_phases": int(sum(nw)),
                    "var_99_range": ph["var_range"], "var_99_median": _r(ph["var_median"]),
                    "es_975_range": ph975["es_range"], "es_975_median": _r(ph975["es_median"]),
                    "note": "each phase is an alternative set of internally disjoint windows and has its OWN "
                            "n_windows (shown per phase), roughly n_sessions/horizon. Different phases overlap "
                            "one another, are never pooled, and their summed count is not an independent sample."}
                one = RM.frozen_pnl(px, hold, win, 1, step=1, cash_weight=cash_weight, cash_annual_rate=rf)
                v1, e1, _ = RM.var_es(one.pnl, 0.99)
                _v1, e975_1, _ = RM.var_es(one.pnl, ES_LEVEL)
                entry["sqrt_time_reference"] = {"var_99": _r(v1 * np.sqrt(h) if v1 is not None else None),
                                                "es_975": _r(e975_1 * np.sqrt(h) if e975_1 is not None else None),
                                                "assumption": f"1-day figure x sqrt({h}); assumes i.i.d. returns (no autocorrelation, no volatility "
                                                              "clustering, stable correlations). Reference point only."}
            # --- estimate uncertainty. For h > 1 the overlapping P&L series is not a valid bootstrap unit.
            if bootstrap_reps <= 0:
                # Deterministic tier: VaR/ES/TE are exact given the data and need no resampling. Intervals are
                # NOT computed - they are reported absent, never as a cheap interval wearing an official label.
                entry["estimate_uncertainty"] = {
                    "primary_method": None, "tier": "deterministic",
                    "var_99": {"available": False, "reason": "confidence intervals not computed in the "
                                                             "deterministic tier"},
                    "es_975": {"available": False, "reason": "confidence intervals not computed in the "
                                                             "deterministic tier"},
                    "note": "the point estimates on this row are exact for this sample; only their sampling "
                            "intervals are absent. Request the official tier to compute them."}
                hz[str(h)] = entry
                continue
            cash_ret = (1.0 + rf) ** (h / RM.TRADING_DAYS) - 1.0 if rf else 0.0
            primary = RM.asset_shock_bootstrap_ci(day_ret, hold, h, 0.99, "var", reps=bootstrap_reps,
                                                  cash_weight=cash_weight, cash_return=cash_ret)
            primary_es = RM.asset_shock_bootstrap_ci(day_ret, hold, h, ES_LEVEL, "es", reps=bootstrap_reps,
                                                     cash_weight=cash_weight, cash_return=cash_ret)
            sens = {}
            for b in sorted({h, 2 * h} if h > 1 else {1}):
                sens[f"block_{b}"] = RM.block_bootstrap_ci(ov.pnl, 0.99, "var", block=b, reps=bootstrap_reps, min_block=h)
            entry["estimate_uncertainty"] = {
                "primary_method": "asset_shock_bootstrap",
                "tier": "official" if bootstrap_reps >= OFFICIAL_BOOTSTRAP_REPS else "preview",
                "var_99": primary, "es_975": primary_es,
                "pnl_block_bootstrap_sensitivity": sens,
                "autocorrelation_lag1_overlapping": _r(float(ov.pnl.autocorr(1))) if ov.n > 3 else None,
                "why_not_pnl_bootstrap": (
                    f"the overlapping {h}-day P&L series has dependence spanning {h} sessions by construction, so a "
                    f"block shorter than {h} splices together windows that overlapped in the original data. The "
                    f"primary interval resamples DAILY ASSET shocks and rebuilds the {h}-day frozen P&L on each "
                    f"replicate; the P&L-level bootstrap is retained only as a block-{h}/block-{2 * h} sensitivity."
                    if h > 1 else "at a one-day horizon the P&L observations do not overlap"),
                "warning": "this is an interval around the ESTIMATE from resampling; it is not the VaR confidence level"}
            hz[str(h)] = entry
        out_windows[label] = {"requested_sessions": need, "available": True,
                              "start": str(win[0].date()), "end": str(win[-1].date()),
                              "n_sessions_used": int(len(win)), "grid_coverage": ret_cov, **{k: v for k, v in span.items() if k != "available"},
                              "horizons": hz,
                              "tracking_error": _te_set(px, hold, bench, bench_meta, grid, need, cash_weight, rf),
                              "calculation_sample_hash": RM.calculation_sample_hash(px, priced, win)}
    full_grid_ret, _sub, full_cov = RM.grid_returns(px, priced, grid)
    diag = {"reference_grid": {"start": str(grid[0].date()), "end": str(grid[-1].date()), "n_sessions": int(len(grid)),
                               "note": "the confirmed reference session grid; window spans are cut from THIS index"},
            "return_coverage": full_cov}
    prov = {"calculator_code_hash": RM.calculator_hash(),
            "calculator_modules": RM.calculator_manifest()["modules"],
            "data_snapshot_hash": RM.data_snapshot_hash(px, grid),
            "data_snapshot_basis": "the SHARED market panel over the full confirmed reference grid; identical for "
                                   "every construction priced off the same acquisition, by construction",
            "portfolio_weights_hash": RM.weights_hash(hold),
            "asof_last_confirmed_close": str(grid[-1].date()),
            "finality_basis": "acquisition timestamp of the price snapshot, not the reader's clock",
            "acquired_at": (acq_meta or {}).get("acquired_at"),
            "confirmed_through": cal.get("finality", {}).get("confirmed_through"),
            "run_asof_prices": None,          # filled by build(); differs when the panel holds an unconfirmed bar
            "reconciliation": None}
    return {**base, "available": True, "windows": out_windows,
            "tracking_error": [{"note": "tracking error is reported per window under windows[<label>].tracking_error; "
                                        "a single all-history number is not the tracking error of any requested window"}],
            "observed_window_diagnostic": diag, "provenance": prov}, full_grid_ret


def config_hash(cfg_like: dict) -> str:
    """Hash of the risk configuration that produced a figure (windows, horizons, ES level, bootstrap params)."""
    import hashlib
    import json as _j
    return hashlib.sha256(_j.dumps(cfg_like, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _alias_map() -> dict:
    try:
        from ai_semi_intel.portfolio.optimizer import CONSTRUCTION_ALIASES
        return dict(CONSTRUCTION_ALIASES)
    except Exception:
        return {}


def enumerate_constructions_from_frame(
    weights: pd.DataFrame,
    run: dict,
    *,
    source: str = "construction_weights",
) -> dict:
    """Build construction identities from an already-loaded weight matrix.

    This is the data-pure half of :func:`enumerate_constructions`.  Callers at
    a frozen calculation boundary can therefore pass the catalog-verified
    frame without giving this module a path from which it could silently read
    a newer run.
    """
    out: dict[str, dict] = {}
    if weights is None or weights.empty:
        return out
    w = weights.copy(deep=True)
    canonical = str(run.get("construction") or "")
    run_cash = run.get("cash")
    run_cash = float(run_cash) if isinstance(run_cash, (int, float)) else None
    for col in w.columns:
        s_ = pd.to_numeric(w[col], errors="coerce").fillna(0.0)
        s_ = s_[s_ > 1e-9]
        if s_.empty:
            continue
        invested = float(s_.sum())
        # a book summing to <1 is vol-target scaled and holds the remainder in cash; one summing to 1 is fully invested
        cash_w = max(0.0, 1.0 - invested)
        out[col] = {"weights": {t: float(v) for t, v in s_.items()},
                    "weights_hash": None,          # filled below once risk_metrics is imported
                    "n_names": int(len(s_)), "invested": _r(invested), "cash_weight": _r(cash_w),
                    "vol_target_scaled": bool(invested < 0.999),
                    "scale": _r(invested) if invested < 0.999 else 1.0,
                    "is_canonical_executable": bool(col == "recommended"),
                    "is_run_primary": bool(col == canonical),
                    "source": f"{source}[{col}]",
                    "cash_basis": ("scaled to the run's volatility target; remainder in cash"
                                   if invested < 0.999 else "fully invested, no cash"),
                    "matches_run_cash": (None if run_cash is None else bool(abs(cash_w - run_cash) < 1e-3))}
    # mark aliases so two names for one algorithm are never read as two independent methods
    from . import risk_metrics as _RM
    aliases = _alias_map()
    by_hash: dict[str, str] = {}
    for name, spec in out.items():
        h = _RM.weights_hash(spec["weights"])
        spec["weights_hash"] = h
        declared = aliases.get(name)
        same = by_hash.get(h)
        spec["alias_of"] = declared or (same if same and same != name else None)
        spec["is_alias"] = bool(spec["alias_of"])
        spec["alias_basis"] = ("declared in optimizer.CONSTRUCTION_ALIASES" if declared else
                               ("identical target weights to " + same if same else None))
        by_hash.setdefault(h, name)
    return out


def enumerate_constructions(weights_path: Path | None = None, run: dict | None = None) -> dict:
    """Every construction present in the run, with its own weights, cash and scale.

    Risk must be reported for each of them, not only the canonical book: they hold different weights (and the
    recommended book holds cash against a volatility target), so their loss distributions differ. Constructions
    are read from the pipeline's own ``portfolio_weights.csv`` so this cannot drift from what the run published.
    """
    run = run if run is not None else config.run_info()
    path = weights_path or config.run_file(config.LATEST / "portfolio_weights.csv")
    if not path.exists():
        return {}
    try:
        weights = pd.read_csv(path, index_col=0)
    except Exception as e:                                                     # pragma: no cover - malformed file
        import logging
        logging.getLogger("market_intel").warning("portfolio_weights.csv unreadable: %s", e)
        return {}
    return enumerate_constructions_from_frame(weights, run, source=path.name)


def landed_book(run: dict | None = None) -> tuple[dict, dict]:
    """The weights the run **actually landed**, read from run.json - not recomputed.

    The executable book is a published artefact of the pipeline: it has one scale, one cash figure and one set
    of weights, and those are what a reader would trade. Re-deriving a scale here from a covariance estimated
    on today's panel produces a *different* number (0.4989 pre-close, 0.4995 live, against the manifest's
    0.499922893) and calling it "executable" misrepresents a recalculation as the run. This returns the landed
    vector; recalculated books are labelled scenarios elsewhere.
    """
    run = run if run is not None else raw_run()
    pf = run.get("portfolio") or (raw_run().get("portfolio") or {})
    pos = pf.get("positions") or []
    w = {str(p.get("ticker")): float(p.get("target_executable_weight") or 0.0) for p in pos
         if p.get("ticker") and (p.get("target_executable_weight") or 0.0) > 0}
    model = {str(p.get("ticker")): float(p.get("target_model_weight") or 0.0) for p in pos
             if p.get("ticker") and (p.get("target_model_weight") or 0.0) > 0}
    if not w:
        return {}, {"available": False, "reason": "run.json declares no executable positions"}
    cash = pf.get("cash")
    scale = pf.get("scale")
    source = str(run.get("_landed_weights_source") or
                 "run.json portfolio.positions[].target_executable_weight")
    return w, {"available": True, "source": source,
               "cash_weight": _r(float(cash)) if isinstance(cash, (int, float)) else _r(1.0 - sum(w.values())),
               "scale": float(scale) if isinstance(scale, (int, float)) else None,
               "invested": _r(sum(w.values())), "n_names": len(w), "model_weights": model,
               "construction": pf.get("construction"), "vol_target": pf.get("vol_target"),
               "run_id": run.get("run_id"), "asof_session": run.get("asof_session")}


def _layers(px: pd.DataFrame, name: str, spec: dict, vol_target: float | None,
            acquired_at: datetime | None, run: dict | None = None) -> dict:
    """Every reporting layer for one construction. Three distinct objects, never conflated:

    ``model``
        the fully-invested target weights.
    ``vol_target_scenario``
        a **hypothetical** book: the model weights rescaled here, now, by this construction's own ex-ante
        volatility to the common volatility target, remainder in cash. It is a recalculation for like-for-like
        comparison across constructions. It is NOT what any run executed, and is never labelled executable.
    ``actual_run``
        the weights the pipeline actually landed, read from run.json. Available only for the construction the
        run published as its executable book; unavailable, with a reason, for every other label.

    A fully-invested book is never relabelled executable, and a recalculated scale is never presented as the
    run's scale.
    """
    from . import risk_metrics as RM
    raw = spec["weights"]
    tot = sum(raw.values())
    model = {t: v / tot for t, v in raw.items()} if tot > 0 else dict(raw)
    out = {"model": {"weights": model, "cash_weight": 0.0, "invested": 1.0, "scale": 1.0,
                     "display_name": "model (fully invested)",
                     "basis": "fully-invested target weights, no cash"}}
    # ---- hypothetical common-vol-target scenario
    grid, _cal = RM.session_index(px, TE_INDEX, acquired_at)
    names = [t for t in model if t in px.columns]
    reason = None
    ann_vol = None
    day_ret = pd.DataFrame()
    win = grid[-VAR_WINDOWS["1y"]:] if len(grid) >= VAR_WINDOWS["1y"] else grid
    if names and len(win):
        day_ret, _sub, _cov = RM.grid_returns(px, names, win)
    if vol_target is None or not (vol_target > 0):
        reason = "the run declares no volatility target, so no construction-specific scale exists"
    elif len(day_ret) < MIN_VAR_DAYS:
        reason = (f"only {len(day_ret)} valid daily returns on the reference grid for every name; "
                  f"need {MIN_VAR_DAYS} to estimate ex-ante volatility")
    else:
        try:
            from ai_semi_intel.portfolio.optimizer import shrunk_cov
            cov = shrunk_cov(day_ret)       # already annualised (multiplies by TRADING_DAYS internally)
        except Exception:
            cov = day_ret.cov() * 252.0     # raw daily covariance does need annualising
        ann_vol = RM.ex_ante_vol(model, cov)
        if not ann_vol or not np.isfinite(ann_vol) or ann_vol <= 0:
            reason = "ex-ante volatility not computable from the available window"
    if reason:
        out["vol_target_scenario"] = {"available": False, "reason": reason,
                                      "display_name": "hypothetical common-vol-target scenario",
                                      "note": "a fully-invested book is NOT reported as a vol-target book; this layer fails closed"}
    else:
        scale = float(min(1.0, vol_target / ann_vol))          # no leverage: cash only
        out["vol_target_scenario"] = {
            "available": True, "weights": {t: v * scale for t, v in model.items()},
            "display_name": "hypothetical common-vol-target scenario",
            "is_hypothetical": True, "is_actual_run": False,
            "cash_weight": _r(1.0 - scale), "invested": _r(scale), "scale": _r(scale),
            "ex_ante_vol_ann": _r(ann_vol), "vol_target": _r(vol_target),
            "basis": f"RECALCULATED here: model weights x (vol_target {vol_target:.2%} / this construction's own "
                     f"ex-ante vol {ann_vol:.2%}), capped at 1.0 (no leverage); remainder in cash. This is a "
                     f"scenario for like-for-like comparison, NOT the book any run executed.",
            "vol_window": {"start": str(day_ret.index[0].date()), "end": str(day_ret.index[-1].date()),
                           "n_daily_returns": int(len(day_ret))}}
    # ---- the actual landed run book
    lw, lmeta = landed_book(run)
    if not spec.get("is_canonical_executable"):
        out["actual_run"] = {"available": False,
                             "display_name": "actual landed run book",
                             "reason": f"the run landed one executable book; '{name}' is not it. Only the "
                                       f"construction published as the run's executable column has actually "
                                       f"executed weights, so no other label may claim a run executable layer."}
    elif not lmeta.get("available"):
        out["actual_run"] = {"available": False, "display_name": "actual landed run book", "reason": lmeta.get("reason")}
    else:
        recomputed = out.get("vol_target_scenario") or {}
        rescale = recomputed.get("scale") if recomputed.get("available") else None
        out["actual_run"] = {
            "available": True, "weights": lw, "display_name": "actual landed run book",
            "is_hypothetical": False, "is_actual_run": True,
            "cash_weight": lmeta["cash_weight"], "invested": lmeta["invested"], "scale": lmeta["scale"],
            "vol_target": lmeta.get("vol_target"), "run_id": lmeta.get("run_id"),
            "basis": f"the weights run {lmeta.get('run_id')} actually landed, read verbatim from {lmeta['source']} "
                     f"(scale {lmeta['scale']}); nothing here is recomputed",
            "recomputation_check": {
                "recomputed_scale": rescale,
                "landed_scale": lmeta["scale"],
                "delta": _r((rescale - lmeta["scale"]) if (rescale is not None and lmeta["scale"] is not None) else None),
                "note": "a recomputation on today's panel does not reproduce the landed scale exactly, which is "
                        "why the landed book is preserved rather than re-derived"}}
    return out


def per_construction_risk(px: pd.DataFrame, constructions: dict, bench: dict, bench_meta: dict, rf: float,
                          windows: dict | None = None, bootstrap_reps: int = OFFICIAL_BOOTSTRAP_REPS,
                          acquired_at: datetime | None = None, vol_target: float | None = None,
                          acq_meta: dict | None = None, run: dict | None = None) -> dict:
    """Run the full risk battery independently for every construction, in every layer.

    Each construction/layer gets its own window availability, its own fail-closed 3Y decision, its own
    per-window tracking error and its own calculation-sample hash. Nothing is inherited from another book.
    """
    windows = windows or VAR_WINDOWS
    official = bootstrap_reps >= OFFICIAL_BOOTSTRAP_REPS
    tier = "official" if official else ("deterministic" if bootstrap_reps <= 0 else "preview")
    cfg_fingerprint = {"windows": windows, "horizons": list(HORIZONS), "es_level": ES_LEVEL,
                       "var_levels": [0.95, 0.99], "bootstrap_reps": bootstrap_reps, "te_index": TE_INDEX,
                       "min_var_days": MIN_VAR_DAYS, "vol_target": vol_target,
                       "precision_tier": tier}
    chash = config_hash(cfg_fingerprint)
    out = {}
    for name, spec in constructions.items():
        layers = _layers(px, name, spec, vol_target, acquired_at, run)
        blk = {"construction": {"name": name, **{k: v for k, v in spec.items() if k != "weights"}},
               "config_hash": chash, "config": cfg_fingerprint, "layers": {}}
        for layer, lspec in layers.items():
            if not lspec.get("available", True):
                blk["layers"][layer] = {"available": False, "reason": lspec["reason"], "note": lspec.get("note"),
                                        "display_name": lspec.get("display_name"), "basis": lspec.get("basis")}
                continue
            lb, _ = var_block(px, lspec["weights"], bench, windows=windows, bench_meta=bench_meta,
                              cash_weight=float(lspec["cash_weight"]), rf=rf, acquired_at=acquired_at,
                              bootstrap_reps=bootstrap_reps, acq_meta=acq_meta)
            lb["layer"] = {"name": layer, **{k: v for k, v in lspec.items() if k != "weights"}}
            blk["layers"][layer] = lb
        out[name] = blk
    return {"constructions": out, "config_hash": chash, "config": cfg_fingerprint,
            "layers": list(LAYERS),
            "precision_tier": tier,
            "precision_note": (
                "point estimates are exact for this sample; confidence intervals were NOT computed in this tier"
                if tier == "deterministic" else
                f"block-bootstrap intervals computed with {bootstrap_reps} reps"
                + ("" if official else f"; PREVIEW ONLY - the official tier is {OFFICIAL_BOOTSTRAP_REPS} reps "
                                       "and preview intervals must not be quoted alongside official figures")),
            "layers_note": "three layers per construction: 'model' (fully invested), 'vol_target_scenario' (a "
                           "HYPOTHETICAL recalculation to a common vol target) and 'actual_run' (the weights the "
                           "run actually landed, available only for the run's own executable column). Compare "
                           "like with like, and never read a scenario as an executed book.",
            "note": "every construction is computed independently: its own window availability, its own "
                    "per-window tracking error and hashes. No figure is inherited from another construction.",
            "no_preferred_route": True}


LAYERS = ("model", "vol_target_scenario", "actual_run")


def construction_comparison(per: dict, layer: str = "model") -> list[dict]:
    """One flat row per construction **for a single layer**, so comparisons are like-for-like."""
    rows = []
    for name, blk in (per.get("constructions") or {}).items():
        c = blk.get("construction", {})
        lb = (blk.get("layers") or {}).get(layer) or {}
        row = {"construction": name, "layer": layer, "n_names": c.get("n_names"),
               "alias_of": c.get("alias_of"), "is_alias": c.get("is_alias", False), "alias_basis": c.get("alias_basis"),
               "weights_hash": c.get("weights_hash"),
               "is_canonical_executable": c.get("is_canonical_executable"), "is_run_primary": c.get("is_run_primary"),
               "layer_display_name": lb.get("display_name") or (lb.get("layer") or {}).get("display_name"),
               "layer_available": bool(lb.get("available", False))}
        if not lb.get("available"):
            row["layer_reason"] = lb.get("reason")
            rows.append(row)
            continue
        li = lb.get("layer", {})
        row.update({"cash_weight": li.get("cash_weight"), "invested": li.get("invested"), "scale": li.get("scale"),
                    "ex_ante_vol_ann": li.get("ex_ante_vol_ann"), "vol_target": li.get("vol_target"),
                    "is_hypothetical": li.get("is_hypothetical"), "is_actual_run": li.get("is_actual_run"),
                    "layer_basis": li.get("basis")})
        for wk in lb.get("windows", {}):
            w = lb["windows"][wk]
            row[f"{wk}_available"] = w.get("available", False)
            if not w.get("available"):
                row[f"{wk}_reason"] = w.get("reason")
                row[f"{wk}_limiting_holding"] = w.get("limiting_holding")
                continue
            row[f"{wk}_start"], row[f"{wk}_end"], row[f"{wk}_n_sessions"] = w.get("start"), w.get("end"), w.get("n_sessions_used")
            h1 = (w.get("horizons") or {}).get("1", {})
            o1 = h1.get("overlapping") or {}
            u1 = (h1.get("estimate_uncertainty") or {}).get("var_99") or {}
            h10 = (w.get("horizons") or {}).get("10", {})
            o10 = h10.get("overlapping") or {}
            ph = h10.get("non_overlapping_phases") or {}
            st = h10.get("sqrt_time_reference") or {}
            u10 = (h10.get("estimate_uncertainty") or {}).get("var_99") or {}
            row.update({f"{wk}_var99_1d": o1.get("var_99"), f"{wk}_es975_1d": o1.get("es_975"), f"{wk}_n_obs_1d": o1.get("n_obs"),
                        f"{wk}_n_failed_1d": o1.get("n_failed_windows"),
                        f"{wk}_var99_1d_ci_lo": u1.get("lo"), f"{wk}_var99_1d_ci_hi": u1.get("hi"),
                        f"{wk}_var99_10d_overlap": o10.get("var_99"), f"{wk}_es975_10d_overlap": o10.get("es_975"),
                        f"{wk}_n_obs_10d_overlap": o10.get("n_obs"), f"{wk}_n_failed_10d": o10.get("n_failed_windows"),
                        f"{wk}_10d_nonoverlap_windows_per_phase": ph.get("n_windows_per_phase_range"),
                        f"{wk}_var99_10d_phase_lo": (ph.get("var_99_range") or [None, None])[0],
                        f"{wk}_var99_10d_phase_hi": (ph.get("var_99_range") or [None, None])[1],
                        f"{wk}_var99_10d_phase_median": ph.get("var_99_median"),
                        f"{wk}_es975_10d_phase_median": ph.get("es_975_median"),
                        f"{wk}_var99_10d_sqrt_ref": st.get("var_99"),
                        f"{wk}_var99_ci_lo": u10.get("lo"), f"{wk}_var99_ci_hi": u10.get("hi"),
                        f"{wk}_ci_reps": u10.get("reps"), f"{wk}_ci_block": u10.get("block_length"),
                        f"{wk}_ci_confidence": u10.get("ci"),
                        f"{wk}_ci_tier": (h10.get("estimate_uncertainty") or {}).get("tier"),
                        f"{wk}_ci_reason": (None if u10.get("available") else u10.get("reason")),
                        f"{wk}_ci_method": (h10.get("estimate_uncertainty") or {}).get("primary_method"),
                        f"{wk}_calc_sample_hash": w.get("calculation_sample_hash")})
            for te in w.get("tracking_error", []) or []:
                b = str(te.get("benchmark", ""))
                key = ("te_mandate" if te.get("kind") == "formal mandate" or b == "mandate benchmark"
                       else ("te_index" if "index proxy" in b else "te_proxy"))
                row[f"{wk}_{key}"] = te.get("tracking_error_ann") if te.get("available") else None
                row[f"{wk}_{key}_status"] = "available" if te.get("available") else (te.get("status") or "unavailable")
                row[f"{wk}_{key}_n_days"] = te.get("n_days")
                row[f"{wk}_{key}_kind"] = te.get("kind")
        prov = lb.get("provenance") or {}
        row.update({"calculator_hash": prov.get("calculator_code_hash"), "data_snapshot_hash": prov.get("data_snapshot_hash"),
                    "portfolio_weights_hash": prov.get("portfolio_weights_hash"), "config_hash": blk.get("config_hash"),
                    "asof": prov.get("asof_last_confirmed_close"), "acquired_at": prov.get("acquired_at"),
                    "finality_basis": prov.get("finality_basis")})
        rows.append(row)
    # two labels can coincide *within a layer* even when their published weights differ - the canonical
    # executable book is its primary construction rescaled, so normalising to fully invested makes them
    # identical. Surface that rather than letting a reader treat them as two independent results.
    by_hash: dict[str, str] = {}
    for r in rows:
        h = r.get("portfolio_weights_hash")
        if not h:
            r.setdefault("layer_equivalent_to", None)
            r.setdefault("layer_equivalent_basis", None)
            continue
        first = by_hash.setdefault(h, r["construction"])
        if first != r["construction"]:
            r["layer_equivalent_to"] = first
            r["layer_equivalent_basis"] = (f"identical {layer}-layer weights to {first}"
                                           + (" (declared alias)" if r.get("is_alias") else
                                              "; the published weights differ only by the scale this layer removes"))
        else:
            r["layer_equivalent_to"] = None
            r["layer_equivalent_basis"] = None
    return rows


def independent_portfolios(rows: list[dict]) -> dict:
    """How many of the reported labels are economically distinct books, and which collapse together.

    Fifteen columns are published because the run publishes fifteen; they are not fifteen independent
    portfolios. This states the count explicitly so a reader never infers independence from row count.
    """
    classes: dict[str, list[str]] = {}
    unavailable: list[str] = []
    for r in rows:
        h = r.get("portfolio_weights_hash")
        if not h or not r.get("layer_available"):
            unavailable.append(r["construction"])          # a label with no book is not a book
            continue
        classes.setdefault(h, []).append(r["construction"])
    groups = [sorted(v) for v in classes.values()]
    dupes = [g for g in groups if len(g) > 1]
    return {"n_labels": len(rows), "n_available_labels": len(rows) - len(unavailable),
            "n_independent_portfolios": len(groups),
            "n_unavailable_labels": len(unavailable), "unavailable_labels": sorted(unavailable),
            "equivalence_classes": sorted(groups, key=lambda g: (-len(g), g[0])),
            "collapsing": dupes,
            "note": (f"{len(rows)} labels are reported because the run publishes {len(rows)} columns. "
                     f"{len(rows) - len(unavailable)} of them have a book in this layer, and those resolve to "
                     f"{len(groups)} economically distinct weight vector(s). "
                     + (f"{len(unavailable)} label(s) have no book in this layer and are counted as neither. "
                        if unavailable else "")
                     + "Labels inside one class are the same portfolio under different names, not independent "
                       "algorithms.")}


_PER_CACHE: dict = {}
_ONE_CACHE: dict[str, dict] = {}
#: Single-flight guard. The sweep takes ~a minute cold; without this, two page loads (or a page plus a curl)
#: each start their own full computation, so both take twice as long and neither can ever finish first.
_BUILD_LOCK = __import__("threading").Lock()
_ONE_BUILD_LOCK = __import__("threading").Lock()


def _cache_key(run: dict, px: pd.DataFrame, cons: dict, bench: dict, rf: float, vol_target: float | None,
               acquired_at: datetime | None, bootstrap_reps: int, windows: dict) -> str:
    """Content key over **every input that can change a number**.

    The previous key was (run_id, last index label, number of constructions, reps). None of those move when a
    price is corrected in place, when weights change without changing the column count, when the benchmark,
    risk-free rate or vol target change, or when the calculator itself changes - so a stale answer survived all
    of them. This hashes the actual values.
    """
    from . import risk_metrics as RM
    import json as _j
    grid, _ = RM.session_index(px, TE_INDEX, acquired_at)
    payload = {
        "run_id": run.get("run_id"),
        "data_snapshot_hash": RM.data_snapshot_hash(px, grid),     # price VALUES, not just the last date
        "n_grid_sessions": int(len(grid)),
        "weights": {k: RM.weights_hash(v["weights"]) for k, v in sorted(cons.items())},
        "landed_book": RM.weights_hash(landed_book(run)[0] or {}),
        "benchmark": RM.weights_hash(bench or {}),
        "rf": round(float(rf or 0.0), 10),
        "vol_target": (round(float(vol_target), 10) if vol_target is not None else None),
        "acquired_at": (acquired_at.isoformat() if acquired_at is not None else None),
        "calculator_hash": RM.calculator_hash(),
        "config_hash": config_hash({"windows": windows, "horizons": list(HORIZONS), "es_level": ES_LEVEL,
                                    "bootstrap_reps": bootstrap_reps, "te_index": TE_INDEX,
                                    "min_var_days": MIN_VAR_DAYS}),
        "bootstrap_reps": bootstrap_reps,
    }
    return RM._sha(_j.dumps(payload, sort_keys=True, default=str).encode())[:24]


def build_constructions(now=None, bootstrap_reps: int = OFFICIAL_BOOTSTRAP_REPS, force: bool = False) -> dict:
    """Per-construction risk sweep across every layer, cached on a content key over all real inputs."""
    import time
    run = config.run_info()
    manifest = raw_run()
    uni = research.universe()
    px = research.price_panel()
    bench, bench_meta = benchmark_weights(uni)
    rf = run.get("risk_free")
    rf = float(rf) if isinstance(rf, (int, float)) else float(research.summary().get("risk_free") or 0.0)
    acquired_at, acq_meta = price_acquisition(manifest)
    cons = enumerate_constructions(run=run)
    if not cons:
        return {"available": False, "reason": "no portfolio_weights.csv in this run", "run": run, "constructions": {}}
    if acquired_at is None:
        return {"available": False, "reason": acq_meta.get("reason"), "acquisition": acq_meta, "run": run,
                "constructions": {},
                "note": "close finality cannot be established without acquisition provenance; this fails closed "
                        "rather than falling back to the wall clock"}
    vt = (manifest.get("portfolio") or {}).get("vol_target", run.get("vol_target"))
    vt = float(vt) if isinstance(vt, (int, float)) and vt > 0 else None
    key = _cache_key({**run, **manifest}, px, cons, bench, rf, vt, acquired_at, bootstrap_reps, VAR_WINDOWS)
    if not force and _PER_CACHE.get("k") == key and "v" in _PER_CACHE:
        return {**_PER_CACHE["v"], "cache": {"hit": True, "key": key}}
    with _BUILD_LOCK:
        # another request may have finished this exact build while we waited for the lock
        if not force and _PER_CACHE.get("k") == key and "v" in _PER_CACHE:
            return {**_PER_CACHE["v"], "cache": {"hit": True, "key": key, "shared_inflight_build": True}}
        return _build_constructions_locked(run, manifest, px, cons, bench, bench_meta, rf, vt,
                                           acquired_at, acq_meta, bootstrap_reps, key, time)


def _build_selected_construction(
    name: str,
    *,
    layer: str,
    window: str,
    bootstrap_reps: int,
    run: dict,
    manifest: dict,
    px: pd.DataFrame,
    constructions: dict,
    bench: dict,
    bench_meta: dict,
    rf: float,
    acquired_at: datetime,
    acq_meta: dict,
    vol_target: float | None,
    cache: dict | None = None,
) -> dict | None:
    """Calculate one selected book from explicit, already-resolved inputs."""
    if layer not in LAYERS or window not in VAR_WINDOWS:
        raise ValueError(f"unknown layer/window: {layer}/{window}")
    if name not in constructions:
        return None
    requested_windows = {window: VAR_WINDOWS[window]}
    tier = ("official" if bootstrap_reps >= OFFICIAL_BOOTSTRAP_REPS
            else ("deterministic" if bootstrap_reps <= 0 else "preview"))
    cfg_fingerprint = {"windows": requested_windows, "horizons": list(HORIZONS), "es_level": ES_LEVEL,
                       "var_levels": [0.95, 0.99], "bootstrap_reps": bootstrap_reps,
                       "te_index": TE_INDEX, "min_var_days": MIN_VAR_DAYS, "vol_target": vol_target,
                       "precision_tier": tier, "layer": layer}
    chash = config_hash(cfg_fingerprint)
    construction = constructions[name]
    lspec = _layers(px, name, construction, vol_target, acquired_at, manifest)[layer]
    blk = {"construction": {"name": name, **{k: v for k, v in construction.items() if k != "weights"}},
           "config_hash": chash, "config": cfg_fingerprint, "layers": {}}
    if not lspec.get("available", True):
        blk["layers"][layer] = {"available": False, "reason": lspec["reason"], "note": lspec.get("note"),
                                "display_name": lspec.get("display_name"), "basis": lspec.get("basis")}
    else:
        lb, _ = var_block(px, lspec["weights"], bench, windows=requested_windows, bench_meta=bench_meta,
                          cash_weight=float(lspec["cash_weight"]), rf=rf, acquired_at=acquired_at,
                          bootstrap_reps=bootstrap_reps, acq_meta=acq_meta)
        lb["layer"] = {"name": layer, **{k: v for k, v in lspec.items() if k != "weights"}}
        blk["layers"][layer] = lb
    row = construction_comparison({"constructions": {name: blk}}, layer)[0]
    return {"available": True, "construction": name, "layer": layer, "window": window,
            "risk": blk, "comparison": row, "config": cfg_fingerprint, "config_hash": chash,
            "precision_tier": tier, "acquisition": acq_meta, "run": run,
            "benchmark": {**bench_meta, "is_formal_mandate": bool(bench_meta.get("custom")),
                          "label": ("mandate benchmark" if bench_meta.get("custom")
                                    else "equal-weight research proxy")},
            "cache": cache or {"hit": False, "basis": "explicit resolved inputs"}}


def build_construction(name: str, layer: str = "model", window: str = "1y",
                       bootstrap_reps: int = OFFICIAL_BOOTSTRAP_REPS, force: bool = False) -> dict | None:
    """Risk for one selected book, layer and window.

    The matrix endpoint intentionally computes deterministic point estimates for every construction. Sampling
    uncertainty is expensive, so the UI requests this endpoint only for the row the user selects. A request
    here must never trigger the full construction x layer x window sweep.
    """
    if layer not in LAYERS or window not in VAR_WINDOWS:
        raise ValueError(f"unknown layer/window: {layer}/{window}")
    run = config.run_info()
    manifest = raw_run()
    cons = enumerate_constructions(run=run)
    if name not in cons:
        return None
    uni = research.universe()
    px = research.price_panel()
    bench, bench_meta = benchmark_weights(uni)
    rf = run.get("risk_free")
    rf = float(rf) if isinstance(rf, (int, float)) else float(research.summary().get("risk_free") or 0.0)
    acquired_at, acq_meta = price_acquisition(manifest)
    if acquired_at is None:
        return {"available": False, "construction": name, "layer": layer, "window": window,
                "reason": acq_meta.get("reason"), "acquisition": acq_meta, "run": run}
    vt = (manifest.get("portfolio") or {}).get("vol_target", run.get("vol_target"))
    vt = float(vt) if isinstance(vt, (int, float)) and vt > 0 else None
    requested_windows = {window: VAR_WINDOWS[window]}
    base_key = _cache_key({**run, **manifest}, px, {name: cons[name]}, bench, rf, vt, acquired_at,
                          bootstrap_reps, requested_windows)
    key = f"{base_key}:{layer}:{window}"
    if not force and key in _ONE_CACHE:
        return {**_ONE_CACHE[key], "cache": {"hit": True, "key": key}}
    with _ONE_BUILD_LOCK:
        if not force and key in _ONE_CACHE:
            return {**_ONE_CACHE[key], "cache": {"hit": True, "key": key, "shared_inflight_build": True}}
        out = _build_selected_construction(
            name,
            layer=layer,
            window=window,
            bootstrap_reps=bootstrap_reps,
            run=run,
            manifest=manifest,
            px=px,
            constructions=cons,
            bench=bench,
            bench_meta=bench_meta,
            rf=rf,
            acquired_at=acquired_at,
            acq_meta=acq_meta,
            vol_target=vt,
            cache={"hit": False, "key": key},
        )
        assert out is not None
        if len(_ONE_CACHE) >= 64:
            _ONE_CACHE.pop(next(iter(_ONE_CACHE)))
        _ONE_CACHE[key] = out
        return out


def _full_frame_hash(frame: pd.DataFrame, sessions, columns: list[str] | None = None) -> str:
    """Full SHA-256 counterpart of the display-oriented 16-character risk hash."""
    import hashlib

    cols = sorted(columns) if columns else sorted(frame.columns)
    selected = frame.loc[
        frame.index.isin(sessions), [c for c in cols if c in frame.columns]
    ].astype("float64")
    raw = pd.util.hash_pandas_object(selected.fillna(-1.0), index=True).values.tobytes()
    return hashlib.sha256(raw).hexdigest()


def _frozen_platform_lineage(
    px: pd.DataFrame,
    weights: dict[str, float],
    *,
    window: str,
    acquired_at: datetime,
    acquisition: dict,
    frozen_inputs,
) -> dict:
    """Portable full-digest receipt for one frozen-input calculation."""
    import hashlib
    import json

    from . import risk_metrics as RM

    grid, calendar = RM.session_index(
        px,
        TE_INDEX,
        acquired_at,
        source=str(acquisition.get("source") or "frozen price_panel dataset"),
    )
    requested = grid[-VAR_WINDOWS[window]:] if len(grid) else grid
    weight_payload = json.dumps(
        {str(k): round(float(v), 10) for k, v in sorted(weights.items())},
        sort_keys=True,
    ).encode()
    calculator_dir = Path(RM.__file__).parent
    calculator_parts = []
    for module in sorted(RM.CALCULATOR_MODULES):
        path = calculator_dir / module
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
        calculator_parts.append(f"{module}:{digest}")
    first_bars = {}
    for ticker in sorted(weights):
        series = (pd.to_numeric(px[ticker], errors="coerce")
                  if ticker in px.columns else pd.Series(dtype=float))
        valid = series[(series > 0) & np.isfinite(series)].dropna()
        first_bars[ticker] = None if valid.empty else str(valid.index[0].date())
    versions = {
        role: frozen_inputs.datasets[role].reference.version_id
        for role in frozen_inputs.required_roles
    }
    version_hashes = {
        role: frozen_inputs.datasets[role].reference.content_sha256
        for role in frozen_inputs.required_roles
    }
    finality = (calendar.get("finality") or {}) if isinstance(calendar, dict) else {}
    return {
        "data_snapshot_hash": _full_frame_hash(px, grid),
        "portfolio_weights_hash": hashlib.sha256(weight_payload).hexdigest(),
        "calculator_hash": hashlib.sha256("|".join(calculator_parts).encode()).hexdigest(),
        "calculation_sample_hash": _full_frame_hash(px, requested, sorted(weights)),
        "holding_first_bars": first_bars,
        "asof": str(grid[-1].date()) if len(grid) else None,
        "acquired_at": acquired_at.isoformat(),
        "finality": (
            "confirmed source snapshot; finality derived from frozen source acquisition time "
            f"({finality.get('basis') or 'acquisition'})"
        ),
        "frozen_input_binding_hash": frozen_inputs.binding_hash,
        "input_dataset_versions": versions,
        "input_dataset_hashes": version_hashes,
    }


def build_frozen_construction(
    name: str | None = None,
    *,
    frozen_inputs=None,
    frozen_binding=None,
    layer: str = "actual_run",
    window: str = "3y",
    bootstrap_reps: int = OFFICIAL_BOOTSTRAP_REPS,
    force: bool = False,
) -> dict | None:
    """Risk for one book using only catalog-verified immutable input bytes.

    ``StockSelectRiskLoader`` passes ``frozen_inputs``. ``frozen_binding`` is
    accepted as a descriptive alias for direct callers.  No config/latest,
    research cache, network source, or wall clock is consulted.  The regular
    risk estimators are reused unchanged after the five payloads are parsed.
    """
    from collections.abc import Mapping

    from .platform.risk_adapter import REQUIRED_FROZEN_DATASETS
    from .platform.stock_select_data import (
        parse_frozen_benchmark,
        parse_frozen_construction_weights,
        parse_frozen_landed_weights,
        parse_frozen_price_panel,
        parse_frozen_universe,
        validate_frozen_benchmark_contract,
        validate_frozen_stock_select_provenance,
    )

    del force  # frozen bindings are immutable; there is no mutable cache to bypass
    if frozen_inputs is not None and frozen_binding is not None:
        raise ValueError("pass one of frozen_inputs or frozen_binding, not both")
    binding = frozen_inputs if frozen_inputs is not None else frozen_binding
    if binding is None:
        raise ValueError("a complete frozen dataset binding is required")
    if not getattr(binding, "complete", False):
        raise ValueError("the frozen dataset binding is incomplete")
    roles = tuple(getattr(binding, "required_roles", ()))
    if len(roles) != len(REQUIRED_FROZEN_DATASETS) or set(roles) != set(REQUIRED_FROZEN_DATASETS):
        raise ValueError(
            "frozen dataset roles must equal " + ", ".join(REQUIRED_FROZEN_DATASETS)
        )
    if layer not in LAYERS or window not in VAR_WINDOWS:
        raise ValueError(f"unknown layer/window: {layer}/{window}")

    shared_provenance = validate_frozen_stock_select_provenance(
        binding,
        expected_as_of=binding.as_of,
    )

    price_version = binding.datasets["price_panel"]
    parameters = price_version.request.get("parameters")
    parameters = parameters if isinstance(parameters, Mapping) else {}
    price_format = str(parameters.get("format") or "parquet").strip().lower()
    acquired_raw = parameters.get("source_acquired_at")
    if not isinstance(acquired_raw, str) or not acquired_raw.strip():
        raise ValueError("price_panel request.parameters.source_acquired_at is required")
    try:
        acquired_at = pd.Timestamp(acquired_raw).to_pydatetime()
    except Exception as exc:
        raise ValueError("price_panel source_acquired_at is not a datetime") from exc
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ValueError("price_panel source_acquired_at must be timezone-aware")

    px = parse_frozen_price_panel(binding.payload("price_panel"), format=price_format)
    construction_frame = parse_frozen_construction_weights(
        binding.payload("construction_weights")
    )
    universe = parse_frozen_universe(binding.payload("universe"))
    landed = parse_frozen_landed_weights(binding.payload("landed_weights"))
    benchmark = parse_frozen_benchmark(binding.payload("benchmark"))
    source_sha = str(parameters.get("source_sha256") or "").strip().lower()
    if source_sha and source_sha != price_version.reference.content_sha256:
        raise ValueError("price_panel source_sha256 does not match its frozen content hash")

    risk_free = landed.get("risk_free")
    if isinstance(risk_free, bool) or not isinstance(risk_free, (int, float)):
        raise ValueError("landed_weights.risk_free is required for frozen cash P&L")
    rf = float(risk_free)
    if not np.isfinite(rf) or rf <= -1.0:
        raise ValueError("landed_weights.risk_free must be finite and greater than -1")
    run_id = str(landed.get("run_id") or "").strip()
    portfolio_id = str(landed.get("portfolio_id") or "").strip()
    run_construction = str(landed.get("construction") or "").strip()
    if not run_id or not portfolio_id or not run_construction:
        raise ValueError("landed_weights must identify run_id, portfolio_id, and construction")
    if run_id != shared_provenance["legacy_run_id"]:
        raise ValueError("landed_weights.run_id does not match frozen role legacy_run_id")

    weights = {str(k): float(v) for k, v in dict(landed.get("weights") or {}).items()}
    model_weights = {
        str(k): float(v) for k, v in dict(landed.get("model_weights") or {}).items()
    }
    missing_universe = sorted((set(weights) | set(model_weights)) - set(universe.index))
    if missing_universe:
        raise ValueError(
            "landed holdings are absent from the frozen universe: "
            + ", ".join(missing_universe)
        )
    missing_construction_universe = sorted(set(construction_frame.index) - set(universe.index))
    if missing_construction_universe:
        raise ValueError(
            "construction weight rows are absent from the frozen universe: "
            + ", ".join(missing_construction_universe[:8])
        )
    run_view = {
        "run_id": run_id,
        "portfolio_id": portfolio_id,
        "construction": run_construction,
        "cash": float(landed["cash"]),
        "vol_target": landed.get("vol_target"),
        "risk_free": rf,
        "asof_session": str(px.index[-1].date()) if len(px.index) else None,
        "data_manifest_hash": parameters.get("source_manifest_sha256"),
        "validation_status": landed.get("validation_status") or "NOT VALIDATED",
    }
    manifest = {
        **run_view,
        "_landed_weights_source": "catalog-verified frozen landed_weights dataset",
        "data_cutoff": {
            "prices": run_view["asof_session"],
            "fetched_at": {"prices": acquired_at.isoformat()},
        },
        "portfolio": {
            "construction": run_construction,
            "vol_target": landed.get("vol_target"),
            "scale": landed.get("scale"),
            "cash": landed.get("cash"),
            "positions": [
                {
                    "ticker": ticker,
                    "target_executable_weight": weights.get(ticker, 0.0),
                    "target_model_weight": model_weights.get(ticker, 0.0),
                }
                for ticker in sorted(set(weights) | set(model_weights))
            ],
        },
    }
    constructions = enumerate_constructions_from_frame(
        construction_frame,
        run_view,
        source="catalog-verified frozen construction_weights dataset",
    )
    canonical = [
        construction_name
        for construction_name, spec in constructions.items()
        if spec.get("is_canonical_executable") is True
    ]
    if name is None:
        if len(canonical) != 1:
            raise ValueError(
                f"expected exactly one canonical executable construction, found {len(canonical)}"
            )
        name = canonical[0]
    if name not in constructions:
        return None
    if name in canonical and model_weights:
        raw_model = constructions[name]["weights"]
        total = float(sum(raw_model.values()))
        normalized = {ticker: value / total for ticker, value in raw_model.items()}
        tickers = set(normalized) | set(model_weights)
        mismatch = [
            ticker
            for ticker in sorted(tickers)
            if not np.isclose(
                normalized.get(ticker, 0.0),
                model_weights.get(ticker, 0.0),
                rtol=0.0,
                atol=1e-7,
            )
        ]
        if mismatch:
            raise ValueError(
                "frozen construction_weights and landed model_weights disagree for "
                + ", ".join(mismatch[:8])
            )

    bench_weights = {
        str(k): float(v) for k, v in dict(benchmark.get("weights") or {}).items()
    }
    benchmark_identity = validate_frozen_benchmark_contract(
        binding,
        expected_portfolio_id=portfolio_id,
    )
    source = benchmark.get("source")
    if benchmark.get("kind") == "research_proxy":
        if not isinstance(source, Mapping) or source.get("dataset_role") not in (None, "universe"):
            raise ValueError("research proxy benchmark must identify the frozen universe role")
        declared_universe_hash = str(source.get("content_sha256") or "").lower()
        actual_universe_hash = binding.datasets["universe"].reference.content_sha256
        if declared_universe_hash != actual_universe_hash:
            raise ValueError("research proxy benchmark does not derive from the frozen universe version")
        if "eligible" not in universe.columns:
            raise ValueError("frozen universe.eligible is required for the research proxy")
        eligible = []
        for ticker, raw in universe["eligible"].items():
            if isinstance(raw, (bool, np.bool_)):
                include = bool(raw)
            elif isinstance(raw, (int, float, np.integer, np.floating)) and raw in (0, 1):
                include = bool(raw)
            elif isinstance(raw, str) and raw.strip().lower() in {"true", "false"}:
                include = raw.strip().lower() == "true"
            elif pd.isna(raw):
                include = False
            else:
                raise ValueError(f"frozen universe.eligible is invalid for {ticker}")
            if include:
                eligible.append(str(ticker))
        if not eligible:
            raise ValueError("frozen universe has no eligible names for the research proxy")
        expected_weight = 1.0 / len(eligible)
        if set(bench_weights) != set(eligible) or any(
            not np.isclose(weight, expected_weight, rtol=0.0, atol=1e-12)
            for weight in bench_weights.values()
        ):
            raise ValueError("research proxy weights do not match the frozen eligible universe")
    formal = benchmark.get("kind") == "formal_mandate"
    bench_meta = {
        "name": "frozen mandate benchmark" if formal else "frozen equal-weight research proxy",
        "source": benchmark_identity["benchmark_source"],
        "n": len(bench_weights),
        "custom": formal,
        "kind": benchmark.get("kind"),
        "decision_usable": benchmark_identity["decision_usable"],
        "note": "weights consumed from the catalog-verified frozen benchmark dataset",
    }
    vol_target = landed.get("vol_target")
    vol_target = (
        float(vol_target)
        if isinstance(vol_target, (int, float)) and not isinstance(vol_target, bool)
        and np.isfinite(float(vol_target)) and float(vol_target) > 0
        else None
    )
    acquisition = {
        "available": True,
        "source": "price_panel request.parameters.source_acquired_at",
        "raw": acquired_raw,
        "acquired_at": acquired_at.isoformat(),
        "catalog_acquired_at": price_version.reference.acquired_at.isoformat(),
        "note": "risk finality uses the original source acquisition instant, not the later catalog migration time",
    }
    result = _build_selected_construction(
        name,
        layer=layer,
        window=window,
        bootstrap_reps=bootstrap_reps,
        run=run_view,
        manifest=manifest,
        px=px,
        constructions=constructions,
        bench=bench_weights,
        bench_meta=bench_meta,
        rf=rf,
        acquired_at=acquired_at,
        acq_meta=acquisition,
        vol_target=vol_target,
        cache={
            "hit": False,
            "key": binding.binding_hash,
            "basis": "exact catalog-verified frozen dataset binding",
        },
    )
    if result is None:
        return None
    raw_selected = constructions[name]["weights"]
    selected_total = float(sum(raw_selected.values()))
    selected_model = {
        ticker: value / selected_total for ticker, value in raw_selected.items()
    }
    if layer == "actual_run":
        lineage_weights = weights
    elif layer == "model":
        lineage_weights = selected_model
    else:
        selected_layer = result["risk"]["layers"].get(layer) or {}
        selected_layer_meta = selected_layer.get("layer") or {}
        selected_scale = selected_layer_meta.get("scale")
        lineage_weights = (
            {
                ticker: value * float(selected_scale)
                for ticker, value in selected_model.items()
            }
            if isinstance(selected_scale, (int, float)) and not isinstance(selected_scale, bool)
            else selected_model
        )
    lineage = _frozen_platform_lineage(
        px,
        lineage_weights,
        window=window,
        acquired_at=acquired_at,
        acquisition=acquisition,
        frozen_inputs=binding,
    )
    version_ids = {
        role: binding.datasets[role].reference.version_id
        for role in REQUIRED_FROZEN_DATASETS
    }
    result.update({
        "frozen_input_binding_hash": binding.binding_hash,
        "frozen_benchmark_identity": benchmark_identity,
        "input_dataset_versions": version_ids,
        "platform_lineage": lineage,
        "frozen_input_receipt": {
            "schema_version": "platform.stock-select.risk-consumption.v1",
            "binding_hash": binding.binding_hash,
            "dataset_versions": version_ids,
            "dataset_hashes": lineage["input_dataset_hashes"],
            "consumed_roles": list(REQUIRED_FROZEN_DATASETS),
            "construction": name,
            "layer": layer,
            "window": window,
            "source_acquired_at": acquired_at.isoformat(),
        },
    })
    return result


def _build_constructions_locked(run, manifest, px, cons, bench, bench_meta, rf, vt, acquired_at, acq_meta,
                                bootstrap_reps, key, time) -> dict:
    t0 = time.time()
    per = per_construction_risk(px, cons, bench, bench_meta, rf, bootstrap_reps=bootstrap_reps,
                                acquired_at=acquired_at, vol_target=vt, acq_meta=acq_meta, run=manifest)
    comps = {f"comparison_{lay}": construction_comparison(per, lay) for lay in LAYERS}
    out = {"available": True, "run": run, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "acquisition": acq_meta,
           "benchmark": {**bench_meta, "is_formal_mandate": bool(bench_meta.get("custom")),
                         "label": ("mandate benchmark" if bench_meta.get("custom") else "equal-weight research proxy"),
                         "mandate_benchmark_status": ("available" if bench_meta.get("custom") else "NOT AVAILABLE"),
                         "hash": config_hash({"weights": bench})},
           "n_constructions": len(cons), "took_s": round(time.time() - t0, 1),
           "vol_target": vt, "layers": list(LAYERS),
           **comps,
           "comparison": comps["comparison_model"],           # default view: like-for-like model weights
           "independence": {lay: independent_portfolios(comps[f"comparison_{lay}"]) for lay in LAYERS},
           "cache": {"hit": False, "key": key,
                     "basis": "content hash of price values, every construction's weights, the landed book, the "
                              "benchmark, rf, vol target, acquisition instant, calculator source and config"},
           **per}
    _PER_CACHE.update({"k": key, "v": out})
    return out


def realised_betas(r: pd.DataFrame, px: pd.DataFrame, factors=("SOXX", "SPY")) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict] = {t: {} for t in r.columns}
    if r.empty:
        return out
    for f in factors:
        if f not in px.columns:
            for t in r.columns:
                out[t][f.lower()] = None
            continue
        b = pd.to_numeric(px[f], errors="coerce").pct_change(fill_method=None).reindex(r.index)
        for t in r.columns:
            m = pd.concat([r[t], b], axis=1).dropna()
            if len(m) < MIN_VAR_DAYS:
                out[t][f.lower()] = None
                continue
            c = np.cov(m.iloc[:, 0], m.iloc[:, 1])
            out[t][f.lower()] = _r(c[0, 1] / c[1, 1]) if c[1, 1] > 0 else None
    return out


def stress(hold: dict, betas: dict, uni: pd.DataFrame, scenarios=STRESS_SCENARIOS) -> list[dict]:
    rows = []
    invested = sum(hold.values())
    for name, factor, shock in scenarios:
        if factor in ("SOXX", "SPY"):
            key = factor.lower()
            contrib = {t: hold[t] * betas[t][key] * shock for t in hold if t in betas and betas[t].get(key) is not None}
            covered = sum(hold[t] for t in contrib)
            rows.append({"name": name, "factor": factor, "shock": shock, "pnl": _r(sum(contrib.values())), "method": f"realised beta vs {factor} over the VaR window x weight x shock",
                         "coverage": _r(covered / invested) if invested > 0 else None, "uncovered": sorted(t for t in hold if t not in contrib),
                         "portfolio_beta": _r(sum(hold[t] * betas[t][key] for t in contrib)) if contrib else None,
                         "contributors": [{"ticker": t, "pnl": _r(v), "beta": betas[t][key]} for t, v in sorted(contrib.items(), key=lambda kv: kv[1])[:8]]})
        elif factor == "USD":
            names = sorted(t for t in hold if bool(_attr(uni, t, "is_adr", False)) or (_attr(uni, t, "financial_currency", "USD") or "USD") != "USD")
            rows.append({"name": name, "factor": factor, "shock": shock, "pnl": _r(-abs(shock) * sum(hold[t] for t in names)),
                         "method": "ADR and non-USD reporting names -5% (direct weight); USD names unchanged", "coverage": 1.0, "uncovered": [],
                         "contributors": [{"ticker": t, "pnl": _r(-abs(shock) * hold[t])} for t in names]})
        else:
            w = hold.get(factor, 0.0)
            rows.append({"name": name, "factor": factor, "shock": shock, "pnl": _r(w * shock), "method": "direct weight only (no read-through to the rest of the list)",
                         "coverage": 1.0, "uncovered": [], "contributors": [{"ticker": factor, "pnl": _r(w * shock)}] if w else []})
    return rows


# ----------------------------------------------------------------------------- liquidity / events / compliance
def _volume_panel(path: Path | None) -> pd.DataFrame | None:
    path = path or VOLUME_FILE
    if not path.exists():
        return None
    try:
        return research._cached(path, lambda p: pd.read_parquet(p))
    except Exception:
        return None


def liquidity(hold: dict, uni: pd.DataFrame, px: pd.DataFrame, volume_path: Path | None = None) -> dict:
    vol = _volume_panel(volume_path)
    positions = []
    for t, w in sorted(hold.items(), key=lambda kv: -kv[1]):
        adv_sh = adv_usd = None
        if vol is not None and t in vol.columns and not px.empty and t in px.columns:
            v = pd.to_numeric(vol[t], errors="coerce").dropna().tail(20)
            if len(v) >= 5:
                p = pd.to_numeric(px[t], errors="coerce").reindex(v.index).ffill()
                adv_sh = _r(float(v.mean()), 0)
                adv_usd = _r(float((v * p).dropna().mean()), 0) if p.notna().any() else None
        positions.append({"ticker": t, "weight": _r(w), "market_cap": _attr(uni, t, "market_cap"), "adv_20d_shares": adv_sh, "adv_20d_usd": adv_usd,
                          "position_usd": None, "days_to_liquidate": None, "cost_impact_bps": None})
    if vol is None:
        note = ("the price cache has no volume: ADV, days-to-liquidate and cost impact are not computable; there is also no NAV or live ledger "
                "to size positions in dollars")
    else:
        note = ("20-day ADV from data_cache/prices/long_volume.parquet x adjusted close; days-to-liquidate and cost impact need a position size in dollars "
                "(NAV / live ledger), which does not exist, so they are null")
    return {"computable": {"adv": vol is not None, "days_to_liquidate": False, "cost_impact": False}, "nav_usd": None, "adv_source": str(volume_path or VOLUME_FILE) if vol is not None else None,
            "days_to_liquidate": None, "cost_impact_bps": None, "positions": positions, "note": note}


def event_concentration(hold: dict, uni: pd.DataFrame, today, days: int = EVENT_DAYS, next_earnings: dict | None = None) -> dict:
    rows = []
    for t, w in hold.items():
        ne = (next_earnings or {}).get(t) or _attr(uni, t, "next_earnings")
        if not ne:
            continue
        try:
            d = pd.Timestamp(ne).date()
        except (ValueError, TypeError):
            continue
        ahead = (d - today).days
        if 0 <= ahead <= days:
            rows.append({"ticker": t, "weight": _r(w), "next_earnings": str(d), "days_ahead": ahead})
    rows.sort(key=lambda r: (r["days_ahead"], -r["weight"]))
    return {"days": days, "names": rows, "n": len(rows), "weight_sum": _r(sum(r["weight"] for r in rows)),
            "weight_share_of_invested": _r(sum(r["weight"] for r in rows) / sum(hold.values())) if hold and sum(hold.values()) > 0 else None,
            "no_date": sorted(t for t in hold if not ((next_earnings or {}).get(t) or _attr(uni, t, "next_earnings")))}


def compliance(hold: dict, model: dict, cash: float, uni: pd.DataFrame, cfg=None, risk_cap: float | None = None) -> dict:
    checks = []
    invested = sum(hold.values())
    if cfg is None:
        checks.append({"name": "portfolio config", "ok": None, "limit": None, "value": None, "detail": "ai_semi_intel.config.settings.CONFIG.portfolio not importable"})
    elif not model:
        checks.append({"name": "model weights", "ok": None, "limit": None, "value": None, "detail": "no model weights in the run snapshot"})
    else:
        tmax = max(model, key=model.get)
        checks.append({"name": "max_weight (model)", "limit": float(cfg.max_weight), "value": _r(model[tmax]), "ok": bool(model[tmax] <= cfg.max_weight + EPS), "detail": f"largest model weight {tmax}"})
        tmin = min(model, key=model.get)
        checks.append({"name": "min_weight (model)", "limit": float(cfg.min_weight), "value": _r(model[tmin]), "ok": bool(model[tmin] >= cfg.min_weight - EPS), "detail": f"smallest model weight {tmin}"})
        segw: dict[str, float] = {}
        for t, w in model.items():
            s = _attr(uni, t, "segment", "unknown") or "unknown"
            segw[s] = segw.get(s, 0.0) + w
        smax = max(segw, key=segw.get) if segw else None
        checks.append({"name": "max_segment_weight (model)", "limit": float(cfg.max_segment_weight), "value": _r(segw[smax]) if smax else None,
                       "ok": bool(smax is not None and segw[smax] <= cfg.max_segment_weight + EPS), "detail": f"largest segment {smax}", "segments": {k: _r(v) for k, v in sorted(segw.items(), key=lambda kv: -kv[1])}})
        if risk_cap is not None and not uni.empty and "risk_capped" in uni.columns:
            capped = [t for t in model if bool(_attr(uni, t, "risk_capped", False))]
            if capped:
                worst = max(capped, key=lambda t: model[t])
                checks.append({"name": "high-risk weight cap (model)", "limit": float(risk_cap), "value": _r(model[worst]), "ok": bool(model[worst] <= risk_cap + EPS),
                               "detail": "risk-capped names: " + ", ".join(capped)})
    checks.append({"name": "invested + cash == 1", "limit": 1.0, "value": _r(invested + cash), "ok": bool(abs(invested + cash - 1.0) < EPS), "detail": f"invested {invested:.4f} + cash {cash:.4f}"})
    checks.append({"name": "long-only", "limit": 0.0, "value": _r(min(hold.values())) if hold else None, "ok": bool(all(w >= -EPS for w in hold.values())), "detail": "no negative executable weight"})
    if model and hold:
        scale = [hold[t] / model[t] for t in hold if model.get(t, 0) > 0]
        spread = (max(scale) - min(scale)) if scale else 0.0
        checks.append({"name": "executable = model x scale", "limit": None, "value": _r(sum(scale) / len(scale)) if scale else None, "ok": bool(scale and spread < 1e-4),
                       "detail": "one vol-target scale applied to every name" if scale else "no overlap between executable and model weights"})
    known = [c["ok"] for c in checks if c["ok"] is not None]
    return {"ok": bool(all(known)) if known else None, "n_checks": len(checks), "n_failed": sum(1 for c in checks if c["ok"] is False), "checks": checks,
            "basis": "constraints apply to the fully-invested model weights of the primary construction; executable weights = model x vol-target scale, remainder cash",
            "config": {k: getattr(cfg, k) for k in ("min_weight", "max_weight", "max_segment_weight", "vol_target", "primary") if cfg is not None and hasattr(cfg, k)}}


def _portfolio_cfg(cfg=None):
    if cfg is not None:
        return cfg
    try:
        from ai_semi_intel.config.settings import CONFIG  # type: ignore
        return CONFIG.portfolio
    except Exception:
        return None


def _risk_cap():
    try:
        from ai_semi_intel.analysis.strategic import HIGH_RISK_WEIGHT_CAP  # type: ignore
        return float(HIGH_RISK_WEIGHT_CAP)
    except Exception:
        return None


# ----------------------------------------------------------------------------- payload
def build(now: datetime | None = None, cfg=None, benchmark_path: Path | None = None, volume_path: Path | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    run = config.run_info()
    hold = {t: float(w) for t, w in config.holdings().items()}
    model = {t: float(w) for t, w in config.holdings_model().items()}
    uni = research.universe()
    px = research.price_panel()
    if not px.empty and not isinstance(px.index, pd.DatetimeIndex):
        try:
            px = px.set_index(pd.to_datetime(px.index))
        except Exception:
            pass
    invested = sum(hold.values())
    cash = run.get("cash")
    cash = float(cash) if isinstance(cash, (int, float)) else round(1.0 - invested, 10)
    bench, bench_meta = benchmark_weights(uni, benchmark_path)
    names = sorted(set(hold) | set(bench))
    active = [{"ticker": t, "weight": _r(hold.get(t, 0.0)), "model": _r(model.get(t, 0.0)), "benchmark": _r(bench.get(t, 0.0)), "active": _r(hold.get(t, 0.0) - bench.get(t, 0.0)),
               "active_model": _r(model.get(t, 0.0) - bench.get(t, 0.0)), "in_target": t in hold, "in_benchmark": t in bench, "segment": _attr(uni, t, "segment"),
               "country": _attr(uni, t, "country"), "is_adr": bool(_attr(uni, t, "is_adr", False))} for t in names]
    active.sort(key=lambda r: (-abs(r["active"] or 0.0), r["ticker"]))
    rf_run = run.get("risk_free")
    rf_run = float(rf_run) if isinstance(rf_run, (int, float)) else float(research.summary().get("risk_free") or 0.0)
    acquired_at, acq_meta = price_acquisition(raw_run())
    var, r = var_block(px, hold, bench, bench_meta=bench_meta, cash_weight=float(cash or 0.0), rf=rf_run,
                       acquired_at=acquired_at, acq_meta=acq_meta)
    if var.get("provenance"):                 # make the as-of gap between run and risk explicit, never implicit
        run_asof = str(px.index[-1].date()) if not px.empty else None
        risk_asof = var["provenance"]["asof_last_confirmed_close"]
        var["provenance"]["run_asof_prices"] = run_asof
        var["provenance"]["reconciliation"] = (
            "identical to the run's last price bar" if run_asof == risk_asof else
            f"the run's panel ends {run_asof}, but that bar was still partial when the snapshot was acquired "
            f"({acq_meta.get('acquired_at')}); risk uses the last confirmed close {risk_asof}")
    betas = realised_betas(r, px)
    today = config.now_et(now).date()
    cfg = _portfolio_cfg(cfg)
    payload = {
        "run": run, "generated_at": now.isoformat(timespec="seconds"), "asof_prices": str(px.index[-1].date()) if not px.empty else None,
        "benchmark": {**bench_meta, "weights": bench},
        "weights": {"executable": {t: _r(w) for t, w in hold.items()}, "model": {t: _r(w) for t, w in model.items()}, "cash": _r(cash), "invested": _r(invested),
                    "n_names": len(hold), "reconciles": bool(abs(invested + cash - 1.0) < EPS), "scale": _r(run.get("scale") or (invested / sum(model.values()) if model and sum(model.values()) > 0 else None)),
                    "construction": run.get("construction"), "vol_target": run.get("vol_target"), "basis": "executable = recommended construction x vol-target scale; model = fully-invested primary construction"},
        "active": active,
        "active_summary": {"active_share_exec": _r(0.5 * (sum(abs(hold.get(t, 0.0) - bench.get(t, 0.0)) for t in names) + cash)),
                           "active_share_model": _r(0.5 * sum(abs(model.get(t, 0.0) - bench.get(t, 0.0)) for t in names)),
                           "n_overweight": sum(1 for t in names if hold.get(t, 0.0) - bench.get(t, 0.0) > EPS), "n_underweight": sum(1 for t in names if hold.get(t, 0.0) - bench.get(t, 0.0) < -EPS),
                           "n_benchmark_not_in_target": sum(1 for t in bench if t not in hold), "n_target_not_in_benchmark": sum(1 for t in hold if t not in bench),
                           "note": "active = executable - benchmark (cash counts as active vs a fully-invested benchmark); active_model = model - benchmark"},
        "exposures": exposures(hold, model, bench, uni),
        "gross_net": {"long": _r(invested), "short": 0.0, "gross": _r(invested), "net": _r(invested), "cash": _r(cash), "leverage": 1.0,
                      "note": "long-only, unlevered: gross = net = invested; cash is the vol-target remainder"},
        "liquidity": liquidity(hold, uni, px, volume_path),
        "var": var, "betas": betas, "stress": stress(hold, betas, uni),
        "event_concentration": event_concentration(hold, uni, today, EVENT_DAYS),
        "current_vs_target": {"rows": [{"ticker": t, "target_executable_weight": _r(hold[t]), "target_model_weight": _r(model.get(t, 0.0)), "current_live_weight": None, "delta": None}
                                       for t in sorted(hold, key=lambda x: -hold[x])], "note": "no ledger: current live weights are unknown; deltas cannot be computed"},
        "compliance": compliance(hold, model, cash, uni, cfg, _risk_cap()),
        "wording": "target-list names with executable target weights; no live position ledger; research tool, not investment advice",
    }
    return research.clean(payload)
