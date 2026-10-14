"""Fixed cash-to-sleeve capital scenarios over an existing lagged policy.

This compares funding decisions, not increased leverage within source strategies.
The scenario set is declared before evaluation and is never searched or ranked.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .allocation import _simulate
from .service import PlatformBlocked, PlatformInputError


CALCULATOR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_TARGETS = ("commodity_arbitrage", "msci_rebalance", "pairs_trading")
_SPECS = (
    ("baseline", "Existing risk budget", None),
    ("commodity_plus_10pp", "Commodity +10 pp from cash", "commodity_arbitrage"),
    ("msci_plus_10pp", "MSCI +10 pp from cash", "msci_rebalance"),
    ("ps_plus_10pp", "PS Trading +10 pp from cash", "pairs_trading"),
    ("eligible_cmp_cash_pro_rata", "Eligible Commodity / MSCI / PS absorb cash pro rata", "pro_rata"),
)


def _expanded_decisions(base_decisions, strategy_ids, target):
    result = []
    for d in base_decisions:
        original = [float(w) for w in d["weights"]]
        cash = float(d["cash_weight"])
        if (len(original) != len(strategy_ids) or len(d["estimates"]) != len(strategy_ids)
                or any(not math.isfinite(w) or w < 0 for w in original)
                or not math.isfinite(cash) or cash < -1e-12
                or abs(sum(original) + cash - 1) > 1e-10):
            raise PlatformInputError("base capital weights must be finite, nonnegative and fully reconciled")
        if d["training_end"] >= d["month"] + "-01":
            raise PlatformBlocked("capital expansion must use strictly prior policy decisions")
        eligible = []
        for i, (sid, estimate) in enumerate(zip(strategy_ids, d["estimates"])):
            if estimate["strategy_id"] != sid or not isinstance(estimate["eligible"], bool):
                raise PlatformInputError("base eligibility does not match the strategy order")
            eligible.append(estimate["eligible"] and original[i] > 0)
        weights, added = original.copy(), [0.] * len(original)
        if target == "pro_rata":
            indices = [i for i, sid in enumerate(strategy_ids) if sid in _TARGETS and eligible[i]]
            total = sum(original[i] for i in indices)
            if total > 0 and cash > 0:
                remaining = cash
                for k, i in enumerate(indices):
                    transfer = remaining if k == len(indices) - 1 else cash * original[i] / total
                    added[i] = transfer
                    remaining -= transfer
        elif target in _TARGETS:
            if target in strategy_ids:
                i = strategy_ids.index(target)
                if eligible[i]:
                    added[i] = min(max(cash, 0.), .10)
        elif target is not None:
            raise PlatformInputError("unknown fixed capital expansion scenario")
        weights = [w + x for w, x in zip(weights, added)]
        remaining_cash = max(0., cash - sum(added))
        if sum(weights) > 1 + 1e-10 or abs(sum(weights) + remaining_cash - 1) > 1e-10:
            raise PlatformBlocked("capital expansion exceeds funded NAV")
        overrides = [{"strategy_id": sid, "original_cap": d["estimates"][i]["cap"], "scenario_weight": weights[i]}
                     for i, sid in enumerate(strategy_ids)
                     if weights[i] > float(d["estimates"][i]["cap"]) + 1e-12]
        result.append({"month": d["month"], "training_start": d["training_start"], "training_end": d["training_end"],
                       "weights": weights, "cash_weight": remaining_cash, "original_weights": original,
                       "original_cash_weight": cash, "eligible_for_addition": eligible,
                       "added_capital_weights": added, "cap_overrides": overrides})
    return result


def evaluate_capital_expansion(artifacts, base_result):
    """Compare five predeclared funded-capital rules on the base policy's window."""
    if not artifacts or not base_result.get("decisions") or not base_result.get("rows"):
        raise PlatformInputError("capital scenarios require existing frozen artifacts and lagged policy decisions")
    ids = [a["artifact_id"] for a in artifacts]
    strategy_ids = [a["strategy_id"] for a in artifacts]
    if len(set(ids)) != len(ids) or len(set(strategy_ids)) != len(strategy_ids):
        raise PlatformInputError("capital scenarios require unique strategies")
    if ids != [a["artifact_id"] for a in base_result["selected"]]:
        raise PlatformInputError("capital scenario artifacts must match the verified base policy order")
    if any(a.get("source_verdict") == "INVALID" for a in artifacts):
        raise PlatformBlocked("INVALID source cannot participate in capital scenarios")
    months = [row["month"] for row in base_result["rows"]]
    if len(set(months)) != len(months) or months != sorted(months):
        raise PlatformInputError("base evaluation months must be sorted and unique")
    for previous, current in zip(months, months[1:]):
        py, pm = map(int, previous.split("-")); cy, cm = map(int, current.split("-"))
        if cy * 12 + cm != py * 12 + pm + 1:
            raise PlatformInputError("base evaluation must retain complete contiguous calendar months")
    expected_updates = [month for i, month in enumerate(months) if i == 0 or int(month[-2:]) in (1, 4, 7, 10)]
    if [d["month"] for d in base_result["decisions"]] != expected_updates:
        raise PlatformInputError("capital scenarios must preserve the initial and quarterly decision schedule")
    series = [{row["month"]: float(row["return"]) for row in a["rows"]} for a in artifacts]
    try:
        matrix = np.asarray([[values[month] for values in series] for month in months], dtype=float)
    except KeyError:
        raise PlatformBlocked("frozen strategy NAV does not cover the base evaluation window") from None
    if not np.isfinite(matrix).all() or (matrix <= -1).any():
        raise PlatformBlocked("capital scenarios require finite solvent source NAV returns")
    fee = float(base_result["request"]["allocation_cost_bps"])
    if not math.isfinite(fee) or not 0 <= fee <= 1000:
        raise PlatformInputError("invalid allocation cost")
    assumptions = [
        "Only unallocated platform cash moves into independently funded strategy NAV sleeves; total funded capital stays at or below 100%. No additional within-strategy leverage is simulated.",
        "Each addition uses the existing strictly prior 36-month decision's eligibility and original target weight; source strategy returns, variants, selection, holding rules and embedded costs remain unchanged.",
        "The +10 percentage-point cases transfer min(10 percentage points, unallocated cash) at each existing decision. The joint case transfers cash in proportion to original eligible Commodity, MSCI and PS weights; Vol Trading receives no addition.",
        "These scenarios explicitly override the prior 40% ordinary / 10% event capital caps where needed. This is a retrospective policy change, not a validated allocation recommendation or an optimized weight search.",
        "Original currency convention and transfer fee are preserved; cash earns 0%. Strategy idle cash is retained within its sleeve and is not recycled across strategies.",
        "Source NAV scaling is linear in funded capital. Capacity, market impact, financing, borrow, collateral, dated margin and simultaneous funding are not independently established.",
        "Monthly risk and drawdown can conceal intramonth losses. The comparison uses already inspected 2025–2026 research and does not create independent out-of-sample evidence.",
        "Cross-strategy gross and margin impact is unavailable: source exposure clocks and currencies differ and do not establish synchronized funded or collateral peaks.",
    ]
    scenarios = []
    for sid, label, target in _SPECS:
        if target in _TARGETS and target not in strategy_ids:
            continue
        decisions = _expanded_decisions(base_result["decisions"], strategy_ids, target)
        run = _simulate(months, matrix, decisions, fee)
        if sid == "baseline":
            max_error = max(abs(x["nav"] - y["nav"]) for x, y in zip(run["rows"], base_result["rows"]))
            if max_error > 1e-10:
                raise PlatformBlocked("capital scenario baseline does not reproduce the verified policy NAV")
        else:
            max_error = None
        added = [sum(d["added_capital_weights"]) for d in decisions]
        eligible_indices = [i for i, strategy_id in enumerate(strategy_ids)
                            if strategy_id in _TARGETS and (target == "pro_rata" or strategy_id == target)]
        any_eligible = any(d["eligible_for_addition"][i] for d in decisions for i in eligible_indices)
        reason = ("BASELINE" if target is None else "APPLIED" if any(x > 1e-12 for x in added)
                  else "NO_ELIGIBLE_BUDGET" if not any_eligible else "NO_UNALLOCATED_CASH")
        scenarios.append({"id": sid, "label": label, **run, "decisions": decisions,
                          "reason": reason, "capital_addition_applied": reason == "APPLIED",
                          "maximum_added_weight": max(added),
                          "decisions_with_capital_addition": sum(x > 1e-12 for x in added),
                          "latest_weights": decisions[-1]["weights"], "latest_cash_weight": decisions[-1]["cash_weight"],
                          "current_capital_weights": run["rows"][-1]["capital_weights"],
                          "original_caps_overridden": any(d["cap_overrides"] for d in decisions),
                          "funded_capital_le_one": all(sum(d["weights"]) <= 1 + 1e-10 for d in decisions),
                          "baseline_nav_reconstruction_max_error": max_error,
                          "risk_by_strategy": [{"strategy_id": key, "risk_share": risk} for key, risk in zip(strategy_ids, run["risk_contribution"])],
                          "assumptions": assumptions})
    baseline = scenarios[0]["metrics"]
    for scenario in scenarios:
        scenario["change_from_baseline"] = {key: scenario["metrics"][key] - baseline[key]
                                             if scenario["metrics"].get(key) is not None and baseline.get(key) is not None else None
                                             for key in ("total_return", "cagr", "annualized_volatility", "max_drawdown_monthly", "sharpe_zero_rf")}
    output = {"schema_version": "platform.capital-expansion.v1", "validation_status": "NOT VALIDATED",
              "status": "RETROSPECTIVE_CAPITAL_SCENARIOS", "start": months[0], "end": months[-1], "observations": len(months),
              "source_analysis_id": base_result.get("analysis_id"), "strategy_ids": strategy_ids,
              "artifact_ids": ids, "request": {"currency_mode": base_result["request"]["currency_mode"], "allocation_cost_bps": fee},
              "live_trading_supported": False, "optimization_performed": False,
              "scenario_order": "Predeclared baseline, single-strategy additions, then pro-rata cash transfer; not ranked by result.",
              "assumptions": assumptions, "scenarios": scenarios,
              "calculator": {"code_sha256": CALCULATOR_HASH}}
    output["analysis_id"] = "capital_expansion_" + hashlib.sha256(json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return output
