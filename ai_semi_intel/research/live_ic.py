"""Fixed-horizon score diagnostics from the daily archive; NOT VALIDATED.

Every pipeline run appends the full-universe score table to ``outputs/history/universe_panel.csv``.
This module joins each historical snapshot with realised forward returns and reports the rank
information coefficient (IC) of the composite and each pillar, plus top-15 vs universe returns.
These close-to-close labels do not reconstruct the daily executed portfolio.
Generation-time availability, next-open fills, costs and overlapping-label inference
require separate validation before any trading-performance claim.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from pathlib import Path

from ..config.settings import HISTORY, LATEST
from ..utils.io import write_json
from .. import runs
from .forward_registry import label_window
from ..trading_policy import calendar, FINALITY_BUFFER, aware

HORIZONS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "12m": 252}
SIGNALS = ["composite", "alpha_score", "score_scarcity", "score_risk", "score_risk_resilience", "score_market_resilience",
           "score_financial_resilience", "score_valuation_resilience", "score_fund_momentum", "score_price_momentum",
           "score_value", "score_quality", "upside", "eps_rev_90d", "growth_accel", "mom_12_1_volscaled"]
_DEFAULT_OUTPUT = object()


def forward_returns(px: pd.DataFrame, date: str, days: int, *, generated_at=None,
                    acquired_at=None, evaluated_at=None) -> pd.Series | None:
    """Legacy diagnostics require an actual generation clock and exact sessions.

    A date-only archived score has unknown within-day availability and cannot be
    assigned the preceding close. Missing price rows are never skipped forward.
    """
    if generated_at is None or acquired_at is None:
        return None
    if aware(acquired_at) > aware(evaluated_at or datetime.now(timezone.utc)):
        raise ValueError("price acquisition is later than the evaluation clock")
    if px.index.has_duplicates or not px.index.is_monotonic_increasing or px.columns.has_duplicates:
        raise ValueError("live IC prices require sorted unique sessions and tickers")
    anchor, end = label_window(generated_at, days)
    if calendar().session_close(end) + FINALITY_BUFFER > aware(acquired_at):
        return None
    if anchor not in px.index or end not in px.index:
        return None
    start, finish = px.loc[anchor], px.loc[end]
    valid = start.gt(0) & finish.gt(0) & np.isfinite(start) & np.isfinite(finish)
    return (finish / start - 1).where(valid)


def compute(px: pd.DataFrame, min_days: int = 21, *, acquired_at=None, evaluated_at=None,
            output_path=_DEFAULT_OUTPUT) -> dict:
    ppath = HISTORY / "universe_panel.csv"
    out = {"status": "insufficient history", "validation_status": "NOT VALIDATED",
           "performance_basis": "fixed-horizon close-to-close score diagnostic; not a daily strategy ledger",
           "daily_rebalanced_performance": {"status": "unavailable", "reason": "requires chronological executed positions, cash, costs and corporate actions"},
           "n_snapshots": 0, "snapshots_with_forward": {}, "ic": {}, "top15_vs_universe": {}}
    out["inference_status"] = "DESCRIPTIVE_ONLY"
    out["t_stat_reason"] = "overlapping horizons and too few independent dates; IID t statistics are suppressed"
    out["clock_unavailable_snapshots"] = []
    if not ppath.exists():
        return out
    panel = pd.read_csv(ppath)
    index = runs.load_index(HISTORY)
    panel = runs.latest_per_date(panel, index)        # legacy approved/latest views remain descriptive, not preregistered
    run_clocks = {r["run_id"]: r.get("generated_at") for r in index}
    # Callers should provide the original price acquisition time. Without it the
    # newest supplied close cannot be assumed final merely because the clock moved.
    if acquired_at is None:
        out["label_status"] = "price_acquisition_time_missing"
    dates = sorted(panel["date"].unique())
    out["n_snapshots"] = len(dates)
    ic_rows = []
    perf_rows = []
    for h, days in HORIZONS.items():
        n_ok = 0
        for d in dates:
            run_ids = panel.loc[panel["date"] == d, "run_id"].unique() if "run_id" in panel else []
            generated_at = run_clocks.get(run_ids[0]) if len(run_ids) == 1 else None
            if generated_at is None and d not in out["clock_unavailable_snapshots"]:
                out["clock_unavailable_snapshots"].append(d)
            fr = forward_returns(px, d, days, generated_at=generated_at, acquired_at=acquired_at, evaluated_at=evaluated_at)
            if fr is None:
                continue
            snap = panel[panel["date"] == d].set_index("ticker")
            snap = snap[snap["eligible"] == True] if "eligible" in snap else snap
            r = fr.reindex(snap.index).dropna()
            if len(r) < 20:
                continue
            n_ok += 1
            for sig in SIGNALS:
                if sig in snap.columns:
                    x = snap.loc[r.index, sig].astype(float)
                    m = x.notna()
                    if m.sum() >= 20:
                        rho, _ = spearmanr(x[m], r[m])
                        ic_rows.append({"date": d, "horizon": h, "signal": sig, "ic": rho, "n": int(m.sum())})
            top = snap.sort_values("composite", ascending=False).head(15).index
            bench = fr.get("SOXX", np.nan)
            perf_rows.append({"date": d, "horizon": h, "top15": float(r.reindex(top).mean()), "universe_ew": float(r.mean()), "soxx": bench})
        out["snapshots_with_forward"][h] = n_ok
    if ic_rows:
        ic = pd.DataFrame(ic_rows)
        summ = ic.groupby(["horizon", "signal"])["ic"].agg(["mean", "std", "count"]).reset_index()
        out["ic"] = {h: {r["signal"]: {"mean_ic": round(r["mean"], 4), "t_stat": None, "n_periods": int(r["count"]), "inference_status": "DESCRIPTIVE_ONLY"}
                         for _, r in summ[summ["horizon"] == h].iterrows()} for h in summ["horizon"].unique()}
        if output_path is not None:
            ic.to_csv(HISTORY / "live_ic_detail.csv", index=False)
        out["status"] = "diagnostic_only" if any(v >= 1 for v in out["snapshots_with_forward"].values()) else "insufficient history"
    if perf_rows:
        pf = pd.DataFrame(perf_rows)
        out["top15_vs_universe"] = {h: {"top15": round(g["top15"].mean(), 4), "universe_ew": round(g["universe_ew"].mean(), 4), "soxx": round(g["soxx"].mean(), 4), "n": int(len(g))}
                                    for h, g in pf.groupby("horizon")}
    out["computed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if output_path is not None:
        write_json(out, LATEST / "live_validation.json" if output_path is _DEFAULT_OUTPUT else Path(output_path))
    return out
