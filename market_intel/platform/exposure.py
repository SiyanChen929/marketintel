"""Theoretical exposure multipliers on a fixed, lagged capital-allocation policy.

Each source daily/event net NAV return is scaled before monthly compounding.
This is a sensitivity calculation, not a trade, financing or margin simulator.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path

import numpy as np

from .allocation import POLICY, _hash, _number, _simulate, _validate_inputs
from .service import PlatformBlocked, PlatformInputError


CALCULATOR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SCALABLE_STRATEGIES = frozenset({"commodity_arbitrage", "msci_rebalance", "pairs_trading"})


def _multipliers(artifacts, requested):
    if requested is None:
        requested = {}
    if not isinstance(requested, dict):
        raise PlatformInputError("multipliers must map selected strategy identifiers to numbers")
    selected = {a["strategy_id"] for a in artifacts}
    if any(key not in selected for key in requested):
        raise PlatformInputError("multiplier contains an unknown or unselected strategy")
    result = []
    for artifact in artifacts:
        value = _number(requested.get(artifact["strategy_id"], 1), "exposure multiplier")
        if not 1 <= value <= 5:
            raise PlatformInputError("exposure multipliers must be between 1 and 5")
        if value != 1 and artifact["strategy_id"] not in SCALABLE_STRATEGIES:
            raise PlatformBlocked(f"exposure scaling is not enabled for {artifact['strategy_id']}")
        result.append(value)
    return result


def _check_base(artifacts, profiles, base_result):
    if not isinstance(base_result, dict) or base_result.get("schema_version") != "platform.allocation-policy.v1":
        raise PlatformInputError("exposure sensitivity requires a frozen lagged allocation-policy result")
    unsigned = {key: value for key, value in base_result.items() if key != "analysis_id"}
    if base_result.get("analysis_id") != "allocation_" + _hash(unsigned):
        raise PlatformBlocked("base allocation result checksum mismatch")
    if base_result.get("policy_sha256") != _hash(POLICY) or base_result.get("policy") != POLICY:
        raise PlatformBlocked("base allocation uses a different fixed policy")
    selected = base_result.get("selected", [])
    if len(selected) != len(artifacts) or len(profiles) != len(artifacts):
        raise PlatformBlocked("exposure sources do not match the base allocation")
    for artifact, profile, sleeve in zip(artifacts, profiles, selected):
        if (artifact.get("artifact_id") != sleeve.get("artifact_id")
                or artifact.get("strategy_id") != sleeve.get("strategy_id")
                or sleeve.get("profile_sha256") != _hash(profile)):
            raise PlatformBlocked("exposure source or activity profile differs from the pinned base allocation")
    request = base_result["request"]
    if request.get("artifact_ids") != [a.get("artifact_id") for a in artifacts]:
        raise PlatformBlocked("exposure artifact order differs from the base allocation")
    if "profile_ids" in request and request["profile_ids"] != [p.get("profile_id") for p in profiles]:
        raise PlatformBlocked("exposure profile pins differ from the base allocation")
    monthly, _, all_months, fee, _ = _validate_inputs(
        artifacts, profiles, request.get("currency_mode", "constant_fx"), request.get("allocation_cost_bps", 0), base_result["end"])
    months = all_months[POLICY["training_months"]:]
    if months != [row["month"] for row in base_result["rows"]] or months[0] != base_result["start"]:
        raise PlatformBlocked("exposure window differs from the fixed policy evaluation window")
    original = np.asarray([[series[month] for series in monthly] for month in months])
    replay = _simulate(months, original, base_result["decisions"], fee)
    if any(not math.isclose(a["nav"], b["nav"], rel_tol=1e-11, abs_tol=1e-12)
           for a, b in zip(replay["rows"], base_result["rows"])):
        raise PlatformBlocked("base allocation does not reconcile with its source NAV and fixed decisions")
    return months, fee


def _diagnostics(artifact, profile, multiplier, scenario, index):
    monthly_rows = {row["month"]: row for row in scenario["rows"]}
    prior_nav = 1.
    month_capital = {}
    for month, row in monthly_rows.items():
        month_capital[month] = (prior_nav - row["allocation_cost"]) * row["opening_weights"][index]
        prior_nav = row["nav"]
    observations, month, capital = [], None, None
    isolated_nav, isolated_peak, isolated_drawdown = 1., 1., 0.
    for source in profile["rows"]:
        current_month = source["date"][:7]
        if current_month not in monthly_rows:
            continue
        if current_month != month:
            month, capital = current_month, month_capital[current_month]
        opening = capital
        scaled_return = multiplier * source["return"]
        capital *= 1 + scaled_return
        isolated_nav *= 1 + scaled_return
        isolated_peak = max(isolated_peak, isolated_nav)
        isolated_drawdown = min(isolated_drawdown, isolated_nav / isolated_peak - 1)
        observation = {"date": source["date"], "active": source["active"], "source_return": source["return"],
                       "scaled_return": scaled_return, "sleeve_capital_open_per_initial_joint_capital": opening,
                       "sleeve_capital_close_per_initial_joint_capital": capital}
        for kind in ("gross", "margin"):
            for side, amount in (("open", opening), ("close", capital)):
                value = source.get(f"{kind}_{side}")
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                          or not math.isfinite(value) or value < 0):
                    raise PlatformInputError(f"source {kind}_{side} ratio must be nonnegative and finite")
                observation[f"{kind}_{side}_per_initial_joint_capital"] = value * multiplier * amount if value is not None else None
        observations.append(observation)
    chosen = []
    if observations:
        chosen = [observations[0], observations[-1], min(observations, key=lambda row: row["scaled_return"])]
    maxima = {}
    for kind in ("gross", "margin"):
        for side in ("open", "close"):
            key = f"{kind}_{side}_per_initial_joint_capital"
            available = [row for row in observations if row[key] is not None]
            # Do not present an incomplete observed subset as a full-window peak.
            maxima[key] = max(row[key] for row in available) if available and len(available) == len(observations) else None
            if available:
                chosen.append(max(available, key=lambda row: row[key]))
    samples = {row["date"]: row for row in chosen}
    return {"artifact_id": artifact["artifact_id"], "strategy_id": artifact["strategy_id"], "name": artifact.get("name"),
            "frequency": profile["frequency"], "multiplier": multiplier, "observations": len(observations),
            "active_observations": sum(row["active"] for row in observations), "observed_maxima": maxima,
            "isolated_sleeve_drawdown_at_source_observations": isolated_drawdown if observations else None,
            "sample_observations": [samples[key] for key in sorted(samples)],
            "exposure_basis": profile.get("exposure_basis", profile.get("capital_basis")),
            "margin_basis": profile.get("margin_basis"),
            "reference_capital": "initial joint capital = 1; theoretical NAV-rescaled target snapshots at each sleeve's own source observation clock",
            "snapshot_interpretation": "gross/margin source ratios x L x the scenario sleeve NAV at the stated open/close mark; maxima refer to these theoretical target snapshots only",
            "close_snapshot_interpretation": "Closing ratios are rescaled to the scenario's closing NAV. This is a theoretical NAV-rescaled target, not a valuation of executed source shares/contracts multiplied by L. It can imply re-targeting after the observation return; that trading and its costs are not simulated.",
            "executed_position_marks": None,
            "synchronized_portfolio_peak": None,
            "limitations": ["Source exposure/margin ratios are multiplied linearly and reapplied to scenario NAV; actual positions, collateral and financing need not scale this way.",
                            "Observed maxima are maxima of theoretical target snapshots, separate per sleeve and per clock; they are neither executed position marks nor simultaneously funded portfolio peaks.",
                            "Conditional/source-observation drawdown can miss losses between source marks."]}


def evaluate_exposure(artifacts, profiles, base_result, *, multipliers=None, extra_cost_bps=0):
    """Scale source observations while freezing the base policy's capital targets."""
    from .performance import metrics
    cost = _number(extra_cost_bps, "extra_cost_bps")
    if cost != 0:
        raise PlatformBlocked("additional costs require dated traded-notional, borrow and financing evidence; NAV-based bps are unsupported")
    months, fee = _check_base(artifacts, profiles, base_result)
    values = _multipliers(artifacts, multipliers)
    matrix = np.zeros((len(months), len(artifacts)))
    month_index = {month: i for i, month in enumerate(months)}
    for j, (profile, multiplier) in enumerate(zip(profiles, values)):
        levels = np.ones(len(months))
        for row in profile["rows"]:
            month = row["date"][:7]
            if month not in month_index:
                continue
            scaled = row["return"] * multiplier
            if scaled <= -1:
                raise PlatformBlocked(f"scaled source loss reaches or exceeds 100% on {row['date']}; liquidation/funding accounting is required")
            levels[month_index[month]] *= 1 + scaled
        if not np.isfinite(levels).all() or (levels < 1e-12).any() or (levels > 1e12).any():
            raise PlatformBlocked("scaled monthly sleeve NAV is outside supported finite bounds")
        matrix[:, j] = levels - 1
    scenario = _simulate(months, matrix, base_result["decisions"], fee)
    selected = []
    for i, (source, multiplier) in enumerate(zip(base_result["selected"], values)):
        selected.append({**copy.deepcopy(source), "exposure_multiplier": multiplier,
                         "baseline_metrics": copy.deepcopy(source["metrics"]), "metrics": metrics(matrix[:, i]),
                         "pnl_contribution": scenario["pnl_contribution"][i],
                         "pnl_contribution_delta": scenario["pnl_contribution"][i] - source["pnl_contribution"],
                         "risk_contribution": scenario["risk_contribution"][i],
                         "return_basis": "net return on independently funded sleeve NAV; source daily/event returns scaled before monthly compounding"})
    metric_delta = {key: value - base_result["metrics"][key]
                    if value is not None and base_result["metrics"].get(key) is not None else None
                    for key, value in scenario["metrics"].items()}
    limitations = [
        "THEORETICAL EXPOSURE SCENARIO: source daily/event net NAV returns are multiplied, then compounded. This does not simulate increased orders or validate leverage.",
        "Base 36-month lagged-policy capital targets are frozen. Risk estimates are not refit to cancel the requested multiplier, and reserved cash is not released.",
        "Embedded source P&L and costs are assumed to scale linearly. Incremental funding, borrow, market impact and nonlinear margin costs are unknown and excluded.",
        "Cash earns 0%; loan financing is excluded rather than proven unnecessary. Additional cost bps cannot be charged to NAV without a justified traded-notional/funding basis.",
        "Exposure and margin diagnostics use each sleeve's own source clock and initial joint capital as denominator. They cannot establish simultaneous peak buying power.",
        "Exposure/margin snapshots are theoretical targets rescaled to scenario opening/closing NAV. Closing snapshots do not value fixed source shares/contracts multiplied by L; re-targeting trades and costs are not simulated.",
        "Only source observation returns change; event entry/exit times, holding windows, strategy versions and source instruments remain fixed.",
        "A scaled observation loss of 100% or more is blocked. Other liquidation, cash-call and liquidity failures are not simulated.",
        "Results use an already inspected historical window; parameters are fixed sensitivity cases, not optimized or new independent OOS evidence.",
    ] + list(base_result.get("assumptions", []))
    correlation = [[None if np.std(matrix[:, i]) < 1e-12 or np.std(matrix[:, j]) < 1e-12
                    else float(np.corrcoef(matrix[:, i], matrix[:, j])[0, 1])
                    for j in range(len(artifacts))] for i in range(len(artifacts))]
    result = {"schema_version": "platform.exposure-sensitivity.v1", "status": "THEORETICAL_EXPOSURE_SCENARIO",
              "validation_status": "NOT VALIDATED", "funding_validated": False, "financing_excluded": True,
              "incremental_costs_status": "UNKNOWN_AND_EXCLUDED", "live_trading_supported": False,
              "scope": "source-observation exposure sensitivity with fixed lagged capital allocations; no funding or execution simulation",
              **scenario, "selected": selected, "correlation": correlation,
              "cash_weight": base_result["cash_weight"], "latest_weights": copy.deepcopy(base_result["latest_weights"]),
              "latest_cash_weight": base_result["latest_cash_weight"],
              "current_capital_weights": scenario["rows"][-1]["capital_weights"],
              "decisions": copy.deepcopy(base_result["decisions"]), "policy": copy.deepcopy(base_result["policy"]),
              "policy_sha256": base_result["policy_sha256"], "warmup_start": base_result["warmup_start"],
              "warmup_end": base_result["warmup_end"], "currency_label": base_result["currency_label"],
              "baseline": {key: copy.deepcopy(base_result[key]) for key in
                           ("analysis_id", "start", "end", "observations", "metrics", "rows", "selected", "allocation_cost")},
              "metrics_delta": metric_delta,
              "daily_diagnostics": [_diagnostics(a, p, multiplier, scenario, i)
                                    for i, (a, p, multiplier) in enumerate(zip(artifacts, profiles, values))],
              "calculator": {"code_sha256": CALCULATOR_HASH, "base_calculator": copy.deepcopy(base_result["calculator"]),
                             "numpy_version": np.__version__, "method": "L times each source daily/event net return, then monthly compounding; unchanged capital decisions"},
              "request": {"method": "exposure_sensitivity", "base_analysis_id": base_result["analysis_id"],
                          "artifact_ids": [a["artifact_id"] for a in artifacts],
                          "profile_ids": [p.get("profile_id") for p in profiles],
                          "currency_mode": base_result["request"]["currency_mode"],
                          "end": months[-1], "allocation_cost_bps": fee, "extra_cost_bps": 0,
                          "multipliers": {a["strategy_id"]: multiplier for a, multiplier in zip(artifacts, values)}},
              "assumptions": limitations, "limitations": limitations}
    result["analysis_id"] = "exposure_" + _hash(result)
    return result
