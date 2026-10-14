"""Pure, bounded notional/cost sensitivity for frozen event-return profiles."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .activity import _validate, summarize
from .service import PlatformInputError

CALCULATOR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def evaluate_event_sensitivity(artifact, profile, start, end):
    """Scale the selected source's embedded net returns; never switch clocks.

    Additional round-trip costs are charged per unit of reported opening gross
    notional. The source's own 5/10/20 bps cost is already in its NAV returns.
    """
    if artifact.get("strategy_id") != "msci_rebalance" or profile.get("frequency") != "event":
        raise PlatformInputError("event sensitivity requires a frozen MSCI event profile")
    if not isinstance(profile.get("profile_id"), str):
        raise PlatformInputError("event sensitivity requires an immutable profile identifier")
    clean = {k: v for k, v in profile.items() if k != "profile_id"}
    _validate(clean, artifact)
    checked = summarize(profile, artifact, start, end)
    rows = [r for r in profile["rows"] if start <= r["date"][:7] <= end]
    if any(r["gross_open"] is None for r in rows):
        raise PlatformInputError("event sensitivity requires measured event-opening gross exposure")
    returns = np.asarray([r["return"] for r in rows], dtype=float)
    exposure = np.asarray([r["gross_open"] for r in rows], dtype=float)
    active = np.asarray([r["active"] for r in rows], dtype=bool)
    dates = [r["date"] for r in rows]
    active_returns = returns[active]
    gross_sum = float(exposure.sum())
    result = {"schema_version": "platform.event-sensitivity.v1", "artifact_id": artifact["artifact_id"],
              "profile_id": profile["profile_id"], "strategy_id": artifact["strategy_id"], "variant": artifact["variant"],
              "start": start, "end": end, "event_count": int(active.sum()), "recorded_observations": len(rows),
              "mean_net_bps": float(active_returns.mean() * 10000) if len(active_returns) else None,
              "std_net_bps": float(active_returns.std(ddof=1) * 10000) if len(active_returns) > 1 else None,
              "worst_net_bps": float(active_returns.min() * 10000) if len(active_returns) else None,
              "extra_cost_break_even_bps": float(10000 * returns.sum() / gross_sum) if gross_sum > 0 else None,
              "source_total_return": checked["calendar_total_return"], "source_costs": artifact["costs"],
              "source_clock": artifact["clock"], "capital_basis": profile["capital_basis"],
              "margin_basis": profile["margin_basis"],
              "method": "For each recorded event, r_scaled = L * (source_net_NAV_return - gross_open * extra_bps / 10000); then compound event returns. Source embedded costs are not charged twice. Extra cost break-even is arithmetic, not geometric or capacity break-even.",
              "funding_interpretation": "10% initial strategy capital × 5x event notional initially means 50% gross master notional on a 1x-source event, or 25% on a 0.5x-source event. It does not establish margin availability, borrow, financing, fill capacity or reusable cash.",
              "limitations": list(dict.fromkeys(checked["limitations"] + [
                  "Sensitivity is retrospective and assumes linear execution economics; increasing notional also scales losses and may worsen impact and borrow costs.",
                  "No entry/exit timing change is simulated. Event-end losses omit intraday adverse excursions; nominal bar duration is not capital-reservation duration.",
                  "Dates are the resampling unit. Descriptive small-sample bootstrap and leave-one-date-out checks do not correct post-hoc selection or prove independent effectiveness."])),
              "validation_status": "NOT VALIDATED", "calculator": {"code_sha256": CALCULATOR_HASH, "numpy_version": np.__version__}, "scenarios": [],
              "event_rows": [{"date": r["date"], "source_net_return": r["return"], "gross_open": r["gross_open"], "active": r["active"]} for r in rows]}
    # Deterministic descriptive base intervals only; the offline source audit
    # also reports 50,000-draw cost-grid and circular four-date block intervals.
    if len(active_returns) > 1:
        indices = np.random.default_rng(20260904).integers(0, len(active_returns), size=(20000, len(active_returns)))
        means = active_returns[indices].mean(axis=1) * 10000
        result["descriptive_event_mean_bootstrap"] = {"resampling_unit": "recorded active review date", "samples": 20000,
            "mean_net_bps_interval_90": np.quantile(means, [.05, .95]).tolist(),
            "mean_net_bps_interval_95": np.quantile(means, [.025, .975]).tolist(),
            "scope": "IID date percentile interval, conditional on the retrospective observed sample; not selection-adjusted or an independent holdout."}
    else:
        result["descriptive_event_mean_bootstrap"] = None
    for multiplier in (1, 2, 3, 5):
        for extra in (0, 5, 10, 20):
            r = multiplier * (returns - exposure * extra / 10000)
            if not np.isfinite(r).all() or (r <= -1).any():
                raise PlatformInputError("event notional scenario is insolvent; default accounting is required")
            path = np.r_[1.0, np.cumprod(1 + r)]
            conditional = r[active]
            loo = [float(np.prod(1 + np.delete(r, i)) - 1) for i in range(len(r))]
            result["scenarios"].append({"multiplier": multiplier, "extra_cost_bps": extra,
                "total_return": float(path[-1] - 1),
                "mean_event_bps": float(conditional.mean() * 10000) if len(conditional) else None,
                "std_event_bps": float(conditional.std(ddof=1) * 10000) if len(conditional) > 1 else None,
                "worst_event_bps": float(conditional.min() * 10000) if len(conditional) else None,
                "max_event_end_drawdown": float(np.min(path / np.maximum.accumulate(path) - 1)),
                "mean_gross_notional_multiple": float(multiplier * exposure.mean()) if len(exposure) else None,
                "max_gross_notional_multiple": float(multiplier * exposure.max()) if len(exposure) else None,
                "initial_10pct_sleeve_return_contribution_other_cash": float(.1 * (path[-1] - 1)),
                "leave_one_event_out": {"min_total_return": min(loo) if loo else None, "max_total_return": max(loo) if loo else None,
                    "positive_count": sum(v > 0 for v in loo), "replicates": len(loo)},
                "event_returns": [{"date": day, "return": float(value)} for day, value in zip(dates, r)]})
    return result
