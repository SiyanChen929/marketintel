"""Fixed, lagged risk-allocation research policy for independently funded sleeves.

The policy uses past observations only. It does not estimate expected returns,
choose a winning strategy variant, release idle collateral, or rerun source trades.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
from datetime import date
from pathlib import Path

import numpy as np

from .service import PlatformBlocked, PlatformInputError


POLICY = {
    "id": "lagged_active_risk_capped_v1",
    "training_months": 36,
    "minimum_evaluation_months": 3,
    "update_months": [1, 4, 7, 10],
    "first_evaluation_month_updates": True,
    "minimum_active_daily_observations": 20,
    "minimum_independent_events": 12,
    "risk_floor": 0.003,
    "worst_loss_divisor": 3.0,
    "ordinary_cap": 0.40,
    "event_cap": 0.10,
    "risk_definition": "max(calendar-day sample sigma when available, active-day sample sigma, worst active loss / 3, floor)",
    "risk_unit": "return on allocated NAV per observed at-risk trading day; not annualized",
    "allocation": "reserve 1/N for each ineligible sleeve; normalize eligible inverse-risk scores to eligible_count/N once; clip caps; retain excess cash",
    "rebalance": "first evaluation month and then calendar-quarter starts; only strictly prior complete months inform weights",
    "capital": "independent funded NAV sleeves, no leverage or shared collateral; cash return 0%",
    "fee": "bps times absolute non-cash allocation changes at pre-fee NAV; fund targets from net-of-fee NAV; first funding excluded",
    "parameter_status": "fixed research policy choices, not statistical confidence levels or optimized parameters",
}


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_encode(value).encode()).hexdigest()


CALCULATOR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlatformInputError(f"{label} must be finite")
    return float(value)


def _month_number(month):
    from .performance import month_number
    return month_number(month)


def _month(number):
    year, offset = divmod(number, 12)
    return f"{year:04d}-{offset + 1:02d}"


def _last_day(month):
    year, number = map(int, month.split("-"))
    return f"{month}-{calendar.monthrange(year, number)[1]:02d}"


def _validate_inputs(artifacts, profiles, currency_mode, allocation_cost_bps, end):
    if (not isinstance(artifacts, (list, tuple)) or not isinstance(profiles, (list, tuple))
            or not 1 <= len(artifacts) <= 30 or len(artifacts) != len(profiles)):
        raise PlatformInputError("provide 1–30 artifacts with matching activity profiles")
    if currency_mode not in ("native", "constant_fx"):
        raise PlatformInputError("currency_mode must be native or constant_fx")
    fee = _number(allocation_cost_bps, "allocation_cost_bps")
    if not 0 <= fee <= 1000:
        raise PlatformInputError("allocation_cost_bps must be 0–1000")
    if len({a.get("strategy_id") for a in artifacts}) != len(artifacts):
        raise PlatformInputError("select at most one variant per strategy")
    if len({a.get("sample_type") for a in artifacts}) > 1:
        raise PlatformBlocked("historical research, paper and live observations cannot be combined")
    if any(a.get("source_verdict") == "INVALID" for a in artifacts):
        raise PlatformBlocked("INVALID source cannot participate in allocation research")
    currencies = sorted({a["currency"] for a in artifacts})
    if currency_mode == "native" and len(currencies) > 1:
        raise PlatformBlocked("mixed currencies require dated FX or the explicit constant-FX research scenario")
    monthly, clean_profiles = [], []
    for artifact, profile in zip(artifacts, profiles):
        observations = artifact.get("rows", [])
        if not observations:
            raise PlatformInputError("monthly artifact has no observations")
        series, previous = {}, None
        for row in observations:
            number = _month_number(row.get("month"))
            if previous is not None and number != previous + 1:
                raise PlatformBlocked("monthly history must be sorted, unique and contiguous")
            value = _number(row.get("return"), "monthly return")
            if value <= -1:
                raise PlatformBlocked("insolvent source NAV cannot enter this policy")
            series[row["month"]] = value
            previous = number
        monthly.append(series)
        if not isinstance(profile, dict) or profile.get("frequency") not in ("daily", "event"):
            raise PlatformInputError("activity profile frequency must be daily or event")
        if profile.get("artifact_id") and profile["artifact_id"] != artifact.get("artifact_id"):
            raise PlatformBlocked("activity profile does not match its monthly artifact")
        cleaned, previous_date = [], None
        for row in profile.get("rows", []):
            try:
                day = date.fromisoformat(row["date"])
            except (KeyError, TypeError, ValueError):
                raise PlatformInputError("activity dates must be YYYY-MM-DD") from None
            if day.isoformat() != row["date"] or previous_date is not None and day <= previous_date:
                raise PlatformBlocked("activity dates must be sorted and unique; aggregate an event's legs on its date")
            value = _number(row.get("return"), "activity return")
            if value <= -1:
                raise PlatformBlocked("insolvent activity NAV cannot enter this policy")
            if not isinstance(row.get("active"), bool):
                raise PlatformInputError("activity rows need explicit boolean active status")
            cleaned.append({"date": day.isoformat(), "return": value, "active": row["active"]})
            previous_date = day
        clean_profiles.append({"frequency": profile["frequency"], "rows": cleaned})
    common_start = max(next(iter(series)) for series in monthly)
    common_end = min(next(reversed(series)) for series in monthly)
    if end is not None:
        _month_number(end)
        if end > common_end:
            raise PlatformBlocked(f"requested end exceeds complete common history ({common_end})")
        common_end = end
    if common_end >= date.today().strftime("%Y-%m"):
        raise PlatformBlocked("unfinished or future months cannot enter the policy")
    count = _month_number(common_end) - _month_number(common_start) + 1
    if count < POLICY["training_months"] + POLICY["minimum_evaluation_months"]:
        raise PlatformBlocked("36 complete common training months and at least 3 subsequent evaluation months are required")
    all_months = [_month(_month_number(common_start) + i) for i in range(count)]
    # Check both clocks against one frozen NAV. Missing event months can mean the
    # explicitly declared idle-cash scenario; absent daily months cannot.
    for series, profile in zip(monthly, clean_profiles):
        grouped = {}
        for row in profile["rows"]:
            month = row["date"][:7]
            if common_start <= month <= common_end:
                grouped.setdefault(month, []).append(row["return"])
        for month in all_months:
            if month not in series:
                raise PlatformBlocked("common calendar has missing monthly observations")
            observations = grouped.get(month, [])
            if not observations and profile["frequency"] == "daily":
                raise PlatformBlocked(f"daily activity profile has no observations in {month}")
            linked = float(np.prod(1 + np.asarray(observations, dtype=float)) - 1)
            if not math.isfinite(linked) or not math.isclose(linked, series[month], rel_tol=1e-8, abs_tol=1e-9):
                raise PlatformBlocked(f"activity returns do not reconcile with monthly NAV in {month}")
    return monthly, clean_profiles, all_months, fee, currencies


def _decision(month, artifacts, profiles):
    start = _month(_month_number(month) - POLICY["training_months"])
    stop = _last_day(_month(_month_number(month) - 1))
    estimates = []
    for artifact, profile in zip(artifacts, profiles):
        train = [r for r in profile["rows"] if start + "-01" <= r["date"] <= stop]
        active = [r["return"] for r in train if r["active"]]
        values = [r["return"] for r in train]
        is_event = profile["frequency"] == "event"
        minimum = POLICY["minimum_independent_events"] if is_event else POLICY["minimum_active_daily_observations"]
        calendar_sigma = float(np.std(values, ddof=1)) if not is_event and len(values) >= 2 else None
        active_sigma = float(np.std(active, ddof=1)) if len(active) >= 2 else None
        loss_threshold = max(0., -min(active)) / POLICY["worst_loss_divisor"] if active else None
        if any(component is not None and not math.isfinite(component)
               for component in (calendar_sigma, active_sigma, loss_threshold)):
            raise PlatformBlocked("risk estimation exceeded supported finite numeric bounds")
        eligible = len(active) >= minimum
        risk = max(calendar_sigma or 0., active_sigma or 0., loss_threshold or 0., POLICY["risk_floor"]) if eligible else None
        estimates.append({"artifact_id": artifact.get("artifact_id"), "strategy_id": artifact["strategy_id"],
                          "frequency": profile["frequency"], "n_observations": len(train), "n_active": len(active),
                          "minimum_active_observations": minimum, "last_observation": train[-1]["date"] if train else None,
                          "risk_components": {"calendar_sigma": calendar_sigma, "active_sigma": active_sigma,
                                              "worst_active_loss_div_3": loss_threshold, "floor": POLICY["risk_floor"]},
                          "risk_unit": risk, "eligible": eligible,
                          "cap": POLICY["event_cap"] if is_event else POLICY["ordinary_cap"],
                          "reason": "ELIGIBLE" if eligible else "INSUFFICIENT_INDEPENDENT_EVENTS" if is_event else "INSUFFICIENT_ACTIVE_DAYS"})
    count = sum(e["eligible"] for e in estimates)
    available_budget = count / len(estimates)
    inverse_sum = sum(1 / e["risk_unit"] for e in estimates if e["eligible"])
    for estimate in estimates:
        raw = available_budget / (estimate["risk_unit"] * inverse_sum) if estimate["eligible"] else 0.
        estimate["uncapped_weight"] = raw
        estimate["weight"] = min(raw, estimate["cap"])
        estimate["capped"] = raw > estimate["cap"] + 1e-12
    weights = [e["weight"] for e in estimates]
    return {"month": month, "training_start": start + "-01", "training_end": stop,
            "estimates": estimates, "weights": weights, "cash_weight": max(0., 1 - sum(weights)),
            "unestimated_budget_cash": 1 - available_budget,
            "cap_excess_cash": max(0., available_budget - sum(weights))}


def _simulate(months, matrix, decisions, fee_bps):
    from .performance import metrics
    n = matrix.shape[1]
    capital, accumulated, rows, contributions = np.zeros(n + 1), np.zeros(n), [], []
    paths = np.ones(n)
    nav, cost_total, peak = 1., 0., 1.
    schedule = {d["month"]: np.array(d["weights"] + [d["cash_weight"]], dtype=float) for d in decisions}
    target = None
    for i, month in enumerate(months):
        opening_nav, cost, turnover = nav, 0., 0.
        if month in schedule:
            target = schedule[month]
            if i:
                turnover = float(np.abs(target[:-1] * nav - capital[:-1]).sum())
                cost = turnover * fee_bps / 10000
            nav -= cost
            if nav <= 0:
                raise PlatformBlocked("allocation fees exhaust available capital")
            capital = target * nav
        opening_weights = (capital / nav).tolist()
        pnl = capital[:-1] * matrix[i]
        capital[:-1] += pnl
        paths *= 1 + matrix[i]
        accumulated += pnl
        cost_total += cost
        nav = float(capital.sum())
        if not math.isfinite(nav) or not 1e-12 <= nav <= 1e12:
            raise PlatformBlocked("combined NAV is outside the supported finite range")
        peak = max(peak, nav)
        contributions.append(np.r_[pnl / opening_nav, -cost / opening_nav])
        rows.append({"month": month, "nav": nav, "return": nav / opening_nav - 1,
                     "drawdown": nav / peak - 1, "rebalanced": month in schedule,
                     "target_weights": target[:-1].tolist(), "opening_weights": opening_weights,
                     "capital_weights": (capital / nav).tolist(), "pnl_contribution": accumulated.tolist(),
                     "sleeve_nav": paths.tolist(),
                     "allocation_turnover": turnover / opening_nav, "allocation_cost": cost,
                     "cumulative_allocation_cost": cost_total})
    returns = [r["return"] for r in rows]
    variance = float(np.var(returns, ddof=1))
    risk = [None] * (n + 1)
    if variance > 1e-20:
        cr = np.asarray(contributions)
        risk = [float(np.cov(cr[:, j], returns, ddof=1)[0, 1] / variance) for j in range(n + 1)]
    return {"start": months[0], "end": months[-1], "observations": len(months), "rows": rows,
            "metrics": metrics(returns), "pnl_contribution": accumulated.tolist(), "risk_contribution": risk[:-1],
            "allocation_cost": cost_total, "allocation_cost_risk_contribution": risk[-1],
            "accounting_error": float(abs(nav - (1 + accumulated.sum() - cost_total)))}


def evaluate_policy(artifacts, profiles, *, currency_mode="constant_fx", allocation_cost_bps=0, end=None):
    """Evaluate one fixed policy and its same-window baselines; no store mutation."""
    from .performance import metrics
    monthly, cleaned, all_months, fee, currencies = _validate_inputs(artifacts, profiles, currency_mode, allocation_cost_bps, end)
    months = all_months[POLICY["training_months"]:]
    updates = [month for i, month in enumerate(months) if i == 0 or int(month[-2:]) in POLICY["update_months"]]
    decisions = [_decision(month, artifacts, cleaned) for month in updates]
    matrix = np.asarray([[series[month] for series in monthly] for month in months])
    scenario = _simulate(months, matrix, decisions, fee)
    equal = [1 / len(artifacts)] * len(artifacts)
    equal_decisions = [{"month": month, "weights": equal, "cash_weight": 0.} for month in updates]
    baselines = {"equal_initial": _simulate(months, matrix, equal_decisions[:1], fee),
                 "equal_quarterly": _simulate(months, matrix, equal_decisions, fee)}
    selected = [{"artifact_id": a.get("artifact_id"), "strategy_id": a["strategy_id"], "name": a.get("name", a["strategy_id"]),
                 "variant": a.get("variant"), "run_id": a.get("run_id"), "currency": a["currency"],
                 "profile_sha256": _hash(p), "sources": a.get("sources", []),
                 "metrics": metrics(matrix[:, i]), "pnl_contribution": scenario["pnl_contribution"][i],
                 "risk_contribution": scenario["risk_contribution"][i], "weight": decisions[0]["weights"][i],
                 "latest_weight": decisions[-1]["weights"][i], "limitations": a.get("limitations", []),
                 "full_start": a["rows"][0]["month"], "full_end": a["rows"][-1]["month"]}
                for i, (a, p) in enumerate(zip(artifacts, profiles))]
    limitations = [
        "Retrospective walk-forward scenario on already inspected research; this is not new independent OOS evidence or an allocation recommendation.",
        "Risk floor, sample thresholds and caps are fixed policy choices. Worst loss / 3 is a heuristic stress threshold, not a tail confidence bound.",
        "Risk observations are returns on allocated NAV per at-risk trading day. Event observations are not a full daily calendar and are never annualized as 252 events.",
        "An unestimated sleeve receives zero automatic capital; its equal-share budget stays in cash. Caps are ceilings, not promised allocations; clipping is not renormalized.",
        "Independent funded sleeves retain idle cash. Margin proxies do not prove available cash; cross-margin, simultaneous funding and collateral reuse are unsupported.",
        "Monthly NAV aggregation can conceal intramonth losses. Daily completeness, event-calendar completeness, source clock/fills and linear rescaling capacity remain source-dependent.",
        "Source costs remain embedded; cash and risk-free return are 0%. Transfer fees apply at quarter-start reallocations except initial funding.",
        "Equal-allocation baselines deliberately retain the old capital rule and do not satisfy this policy's event cap or sample gate.",
    ]
    if currency_mode == "constant_fx":
        limitations.insert(0, "FIXED FX SCENARIO: currency moves, conversion and hedging are excluded; this is not a base-currency investment track record.")
    correlation = [[None if np.std(matrix[:, i]) < 1e-12 or np.std(matrix[:, j]) < 1e-12
                    else float(np.corrcoef(matrix[:, i], matrix[:, j])[0, 1])
                    for j in range(len(artifacts))] for i in range(len(artifacts))]
    result = {"schema_version": "platform.allocation-policy.v1", "status": "RETROSPECTIVE_WALK_FORWARD",
              "validation_status": "NOT VALIDATED", "scope": "fixed lagged capital allocation over frozen research NAVs; source strategy mechanics unchanged",
              **scenario, "decisions": decisions, "selected": selected, "correlation": correlation,
              "cash_weight": decisions[0]["cash_weight"],
              "latest_weights": decisions[-1]["weights"], "latest_cash_weight": decisions[-1]["cash_weight"],
              "current_capital_weights": scenario["rows"][-1]["capital_weights"],
              "warmup_start": all_months[0], "warmup_end": all_months[POLICY["training_months"] - 1],
              "baselines": baselines, "policy": json.loads(_encode(POLICY)), "policy_sha256": _hash(POLICY),
              "calculator": {"code_sha256": CALCULATOR_HASH, "numpy_version": np.__version__,
                             "method": "lagged risk-unit budgeting; quarterly capped independent capital sleeves"},
              "request": {"method": "activity_risk_budget", "artifact_ids": [a.get("artifact_id") for a in artifacts], "currency_mode": currency_mode,
                          "allocation_cost_bps": fee, "end": months[-1]},
              "currency_label": currencies[0] if currency_mode == "native" else "Fixed FX · normalized capital",
              "limitations": limitations, "assumptions": limitations, "live_trading_supported": False}
    result["analysis_id"] = "allocation_" + _hash(result)
    return result
