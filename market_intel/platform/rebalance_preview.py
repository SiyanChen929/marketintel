"""Read-only account sizing from declared, frozen inputs; never an order route.

No account, quote, trade unit, cost, or zero-income assumption is supplied by
default. Input provenance is declared evidence, not independent authentication.
The calculation cannot mutate an account, submit orders, fetch data, or save a
ledger. Decimal strings are the interchange format; all arithmetic is local.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
from hashlib import sha256
import json
import re
from typing import Any

from ai_semi_intel.trading_policy import current_status

SCHEMA = "platform.account_rebalance_preview.v1"
ALLOCATION = "proportional_cost_budget_floor_v1"
ZERO, ONE, BPS = Decimal(0), Decimal(1), Decimal(10000)
TOLERANCE = Decimal("0.00000001")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_INSTRUMENT = re.compile(r"^equity:[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_COST_FIELDS = ("commission_fixed", "commission_per_unit", "commission_bps", "spread_bps", "slippage_bps")


class _Unavailable(ValueError):
    def __init__(self, code: str, field: str, message: str):
        self.reason = {"code": code, "field": field, "message": message}


def _need(ok: bool, field: str, message: str, code="INVALID_INPUT") -> None:
    if not ok:
        raise _Unavailable(code, field, message)


def _mapping(value: Any, field: str) -> dict:
    _need(isinstance(value, dict), field, "An explicit object is required.", "MISSING_EVIDENCE")
    return value


def _text(value: Any, field: str) -> str:
    _need(isinstance(value, str) and bool(value.strip()), field, "A nonempty identifier is required.")
    return value


def _number(value: Any, field: str, *, positive=False) -> Decimal:
    _need(type(value) in (str, int) and len(str(value)) <= 120, field, "Use a finite decimal string or integer.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise _Unavailable("INVALID_INPUT", field, "Invalid decimal value.") from None
    _need(result.is_finite() and result.as_tuple().exponent >= -18 and ZERO <= result <= Decimal("1e18") and (not positive or result > 0),
          field, "Value must be finite, nonnegative, bounded, and positive where required.")
    return result


def _time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        _need(isinstance(parsed, datetime) and parsed.tzinfo is not None and parsed.utcoffset() is not None,
              field, "A timezone-aware timestamp is required.")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise _Unavailable("INVALID_CLOCK", field, "A valid timezone-aware timestamp is required.") from None


def _evidence(value: dict, field: str) -> str:
    _text(value.get("source"), field + ".source")
    _need(isinstance(value.get("content_sha256"), str) and bool(_HASH.fullmatch(value["content_sha256"])),
          field + ".content_sha256", "A declared full SHA-256 source reference is required.", "MISSING_EVIDENCE")
    sample = value.get("sample_type")
    _need(sample in ("observed_declared", "synthetic_fixture"), field + ".sample_type",
          "Inputs must explicitly identify observed_declared or synthetic_fixture.")
    return sample


def _weights(value: dict, field: str) -> tuple[dict[str, Decimal], Decimal]:
    weights = _mapping(value.get("weights"), field + ".weights")
    result = {}
    for key, raw in weights.items():
        _need(isinstance(key, str) and bool(_INSTRUMENT.fullmatch(key)), field + ".weights", "Permanent equity instrument IDs are required.")
        result[key] = _number(raw, field + ".weights." + key)
        _need(result[key] <= ONE, field + ".weights." + key, "Only long-only, unlevered targets are supported.")
    cash = _number(value.get("cash"), field + ".cash")
    _need(cash <= ONE and abs(sum(result.values(), cash) - ONE) <= TOLERANCE,
          field, "Target security and cash weights must reconcile to one.")
    return result, cash


def _s(value: Decimal) -> str:
    if not value:
        return "0"
    raw = format(value, "f")
    return raw.rstrip("0").rstrip(".") if "." in raw else raw


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _calculate(target, account, quotes, policy, now, out):
    target, account, quotes, policy = [_mapping(x, label) for x, label in
                                      ((target, "target"), (account, "account"), (quotes, "quotes"), (policy, "policy"))]
    samples = [_evidence(x, label) for x, label in ((target, "target"), (account, "account"), (policy, "policy"))]
    generated = _time(target.get("generated_at"), "target.generated_at")
    _text(target.get("run_id"), "target.run_id")
    _text(target.get("construction"), "target.construction")
    _need(type(target.get("actionable_at_generation")) is bool, "target.actionable_at_generation", "An explicit generation verdict is required.")
    target_clock = current_status({"generated_at": generated.isoformat(), "actionable": target["actionable_at_generation"],
                                   "rebalance_policy": target.get("rebalance_policy")}, now)
    out["target_status"] = target_clock
    _need(target_clock["status"] == "OPEN", "target.rebalance_policy", "Target is outside its valid daily window or its saved input gates are blocked.", "TARGET_NOT_CURRENT")
    weights, cash_weight = _weights(target, "target")
    previous, previous_cash = None, None
    prior = target.get("prior_target")
    if prior is not None:
        prior = _mapping(prior, "target.prior_target")
        samples.append(_evidence(prior, "target.prior_target"))
        _text(prior.get("run_id"), "target.prior_target.run_id")
        _text(prior.get("construction"), "target.prior_target.construction")
        _need(prior["run_id"] != target["run_id"] and _time(prior.get("generated_at"), "target.prior_target.generated_at") < generated,
              "target.prior_target", "Prior target must be a different, earlier frozen run.")
        previous, previous_cash = _weights(prior, "target.prior_target")
    _text(account.get("account_id"), "account.account_id")
    _need(account.get("currency") == "USD", "account.currency", "Only USD cash equity accounts are supported.")
    _need(account.get("open_orders") == [], "account.open_orders", "An explicit empty open-order inventory is required; unresolved orders are unsupported.", "OPEN_ORDERS_UNRESOLVED")
    _text(policy.get("policy_id"), "policy.policy_id")
    _need(_time(policy.get("available_at"), "policy.available_at") <= now, "policy.available_at", "Cost and unit policy was unavailable at the preview time.", "FUTURE_EVIDENCE")
    _need(policy.get("buy_allocation") == ALLOCATION and policy.get("sell_fee_treatment") == "deduct_from_proceeds",
          "policy", "Explicit supported buy allocation and sell-fee treatment are required.")
    ages = {}
    for name in ("max_account_age_seconds", "max_quote_age_seconds"):
        value = policy.get(name)
        _need(type(value) is int and 0 <= value <= 86400, "policy." + name, "Explicit integer freshness limit between zero and 86400 seconds is required.")
        ages[name] = value
    account_asof, acquired = _time(account.get("asof"), "account.asof"), _time(account.get("acquired_at"), "account.acquired_at")
    age = Decimal(str((now - account_asof).total_seconds()))
    out["account_age_seconds"] = _s(age)
    _need(account_asof <= acquired <= now, "account", "Account evidence is future-dated or has inconsistent acquisition time.", "FUTURE_EVIDENCE")
    _need(age <= ages["max_account_age_seconds"], "account.asof", "Observed positions/cash are stale under the declared policy.", "STALE_ACCOUNT")
    balances = {key: _number(account.get(key), "account." + key) for key in
                ("cash_total", "settled_cash", "available_cash", "unsettled_receivables", "unsettled_payables", "reserved_cash")}
    _need(abs(balances["cash_total"] - balances["settled_cash"] - balances["unsettled_receivables"] + balances["unsettled_payables"]) <= TOLERANCE,
          "account.cash_total", "Economic cash must equal settled cash plus receivables minus payables.", "CASH_NOT_RECONCILED")
    cash_limit = balances["settled_cash"] - balances["unsettled_payables"] - balances["reserved_cash"]
    _need(ZERO <= balances["available_cash"] <= cash_limit, "account.available_cash",
          "Available cash cannot include receivables or exceed settled cash net of payables and reservations.", "CASH_NOT_RECONCILED")
    quantities = {}
    for key, value in _mapping(account.get("positions"), "account.positions").items():
        _need(isinstance(key, str) and bool(_INSTRUMENT.fullmatch(key)), "account.positions", "Permanent equity instrument IDs are required.")
        quantities[key] = _number(value, "account.positions." + key)
    instruments = sorted(set(quantities) | set(weights))
    steps, costs = _mapping(policy.get("quantity_steps"), "policy.quantity_steps"), _mapping(policy.get("costs"), "policy.costs")
    costs = {k: _number(costs.get(k), "policy.costs." + k) for k in _COST_FIELDS}
    _need(all(costs[k] <= BPS for k in ("commission_bps", "spread_bps", "slippage_bps")), "policy.costs", "Cost rates above 10000 basis points are unsupported.")
    prices = {}
    out["quote_age_seconds"] = {}
    opening = _time(target_clock["not_before"], "target.not_before")
    for key in instruments:
        quote = _mapping(quotes.get(key), "quotes." + key)
        samples.append(_evidence(quote, "quotes." + key))
        _need(quote.get("instrument_id") == key and quote.get("currency") == "USD" and quote.get("price_basis") == "unadjusted" and quote.get("finality") == "confirmed",
              "quotes." + key, "Instrument-matched, confirmed, unadjusted USD quotes are required.")
        observed, acquired = _time(quote.get("observed_at"), "quote.observed_at"), _time(quote.get("acquired_at"), "quote.acquired_at")
        quote_age = Decimal(str((now - observed).total_seconds()))
        out["quote_age_seconds"][key] = _s(quote_age)
        _need(generated < observed and opening <= observed <= acquired <= now, "quotes." + key, "Quote must be available after the signal and at/after the execution open.", "INVALID_QUOTE_CLOCK")
        _need(quote_age <= ages["max_quote_age_seconds"], "quotes." + key, "Quote is stale under the declared policy.", "STALE_QUOTE")
        bid, ask = _number(quote.get("bid"), "quote.bid", positive=True), _number(quote.get("ask"), "quote.ask", positive=True)
        _need(bid <= ask, "quotes." + key, "Bid must not exceed ask.")
        mid = (bid + ask) / 2
        spread = max((ask - bid) / 2, mid * costs["spread_bps"] / (2 * BPS))
        slip = mid * costs["slippage_bps"] / BPS
        _need(mid - spread - slip > ZERO, "quotes." + key, "Modeled sell price must remain positive.")
        step = _number(steps.get(key), "policy.quantity_steps." + key, positive=True)
        quantity = quantities.get(key, ZERO)
        _need(quantity == _floor(quantity, step), "account.positions." + key, "Holding contains a unit remainder unsupported by the declared trading increment.", "UNSUPPORTED_UNIT_REMAINDER")
        prices[key] = {"mid": mid, "spread": spread, "slip": slip, "step": step}
    out["sample_type"] = "synthetic_fixture" if out["sample_type"] == "synthetic_fixture" or "synthetic_fixture" in samples else "observed_declared"
    equity = balances["cash_total"] + sum((quantities.get(k, ZERO) * p["mid"] for k, p in prices.items()), ZERO)
    _need(equity > ZERO, "account", "Economic equity must be positive.")
    rows = []
    desired_buy_cost = ZERO
    for key in instruments:
        p, current = prices[key], quantities.get(key, ZERO)
        target_quantity = _floor(equity * weights.get(key, ZERO) / p["mid"], p["step"])
        required = target_quantity - current
        side = "BUY" if required > 0 else "SELL" if required < 0 else "NONE"
        price = p["mid"] + (p["spread"] + p["slip"]) * (1 if required > 0 else -1) if required else p["mid"]
        variable = price * (ONE + costs["commission_bps"] / BPS) + costs["commission_per_unit"]
        budget_cost = abs(required) * variable + costs["commission_fixed"] if side == "BUY" else ZERO
        desired_buy_cost += budget_cost
        rows.append({"instrument_id": key, "p": p, "current": current, "target": target_quantity, "required": required,
                     "side": side, "price": price, "variable": variable, "budget_cost": budget_cost})
    buy_outflow, sell_proceeds, total_fee, spread_cost, slip_cost, planned_value = (ZERO,) * 6
    constraints = []
    final_rows = []
    ratio = min(ONE, balances["available_cash"] / desired_buy_cost) if desired_buy_cost else ONE
    if ratio < ONE:
        constraints.append({"code": "BUY_BUDGET_REDUCED", "message": "Only existing available cash funds buys; sell proceeds remain unsettled."})
    for row in rows:
        p, key = row["p"], row["instrument_id"]
        quantity = abs(row["required"])
        if row["side"] == "BUY" and ratio < ONE:
            allocated = row["budget_cost"] * ratio
            quantity = min(quantity, _floor(max(ZERO, allocated - costs["commission_fixed"]) / row["variable"], p["step"]))
        fee = costs["commission_fixed"] + quantity * costs["commission_per_unit"] + quantity * row["price"] * costs["commission_bps"] / BPS if quantity else ZERO
        if row["side"] == "SELL" and quantity * row["price"] < fee:
            constraints.append({"code": "SELL_COST_EXCEEDS_PROCEEDS", "instrument_id": key, "message": "Sell sizing omitted because its proceeds cannot cover its declared fee."})
            quantity, fee = ZERO, ZERO
        signed = quantity * (1 if row["side"] == "BUY" else -1) if row["side"] != "NONE" else ZERO
        after_quantity = row["current"] + signed
        notional = quantity * row["price"]
        if row["side"] == "BUY":
            buy_outflow += notional + fee
        elif row["side"] == "SELL":
            sell_proceeds += notional - fee
        total_fee += fee
        spread_cost += quantity * p["spread"]
        slip_cost += quantity * p["slip"]
        planned_value += after_quantity * p["mid"]
        final_rows.append({"instrument_id": key, "target_weight": _s(weights.get(key, ZERO)),
                           "prior_target_weight": _s(previous.get(key, ZERO)) if previous is not None else None,
                           "target_weight_change": _s(weights.get(key, ZERO) - previous.get(key, ZERO)) if previous is not None else None,
                           "pretrade_weight": _s(row["current"] * p["mid"] / equity),
                           "account_weight_gap": _s(weights.get(key, ZERO) - row["current"] * p["mid"] / equity),
                           "current_quantity": _s(row["current"]), "quantity_step": _s(p["step"]),
                           "unit_rounded_target_quantity": _s(row["target"]), "desired_quantity_change": _s(row["required"]),
                           "planned_quantity_change": _s(signed), "unfilled_quantity_gap": _s(row["required"] - signed),
                           "estimated_after_quantity": _s(after_quantity), "reference_mid": _s(p["mid"]),
                           "estimated_execution_price": _s(row["price"]) if quantity else None,
                           "estimated_notional": _s(notional), "estimated_commission": _s(fee),
                           "estimated_spread_cost": _s(quantity * p["spread"]), "estimated_slippage_cost": _s(quantity * p["slip"])})
    _need(buy_outflow <= balances["available_cash"], "calculation", "Rounding exceeded available buy cash; preview withheld.", "CASH_CONSTRAINT_FAILURE")
    after_cash = balances["cash_total"] + sell_proceeds - buy_outflow
    after_equity = after_cash + planned_value
    _need(after_equity > ZERO, "calculation", "Modeled costs exhaust account equity.", "NONPOSITIVE_ESTIMATED_EQUITY")
    total_cost = total_fee + spread_cost + slip_cost
    _need(abs(equity - after_equity - total_cost) <= TOLERANCE, "calculation", "Estimated cash, positions and execution costs do not reconcile.", "RECONCILIATION_FAILURE")
    for row in final_rows:
        after_weight = Decimal(row["estimated_after_quantity"]) * Decimal(row["reference_mid"]) / after_equity
        row["estimated_after_weight"] = _s(after_weight)
        row["weight_deviation"] = _s(after_weight - Decimal(row["target_weight"]))
    comparison_ids = sorted(set(weights) | set(previous or {}))
    out.update(status="AVAILABLE", constraints=constraints, rows=final_rows, account_id=account["account_id"],
               run_id=target["run_id"], construction=target["construction"], policy_id=policy["policy_id"],
               pretrade_equity=_s(equity), pretrade_cash_weight=_s(balances["cash_total"] / equity),
               target_cash_weight=_s(cash_weight), target_cash_change=_s(cash_weight - previous_cash) if previous_cash is not None else None,
               target_comparison_basis="same_construction" if prior and prior["construction"] == target["construction"] else "construction_switch" if prior else "prior_unavailable",
               target_deltas=[{"instrument_id": k, "delta": _s(weights.get(k, ZERO) - previous.get(k, ZERO))} for k in comparison_ids] if previous is not None else None,
               target_adjustment_ratio=_s((sum((abs(weights.get(k, ZERO) - previous.get(k, ZERO)) for k in comparison_ids), ZERO) + abs(cash_weight - previous_cash)) / 2) if previous is not None else None,
               desired_buy_cash=_s(desired_buy_cost), estimated_buy_cash_outflow=_s(buy_outflow),
               available_cash_before=_s(balances["available_cash"]), available_cash_after=_s(balances["available_cash"] - buy_outflow),
               estimated_unsettled_sell_proceeds=_s(sell_proceeds), estimated_economic_cash_after=_s(after_cash),
               estimated_after_equity=_s(after_equity), estimated_after_cash_weight=_s(after_cash / after_equity),
               estimated_cash_weight_deviation=_s(after_cash / after_equity - cash_weight),
               estimated_costs={"commission": _s(total_fee), "spread": _s(spread_cost), "slippage": _s(slip_cost), "total": _s(total_cost)},
               account_clock={"asof": account_asof.isoformat(), "acquired_at": _time(account["acquired_at"], "account.acquired_at").isoformat()},
               freshness_policy=ages)


def build_preview(target: dict | None, account: dict | None, quotes: dict | None,
                  policy: dict | None, *, now: datetime | str) -> dict:
    """Return an immutable-by-convention JSON preview; missing evidence is unavailable.

    This v1 supports long-only USD, explicitly empty open orders, and tradeable
    quantity increments. Sell proceeds never fund same-preview buys. The chosen
    allocation can leave residual cash and does not claim an optimal portfolio.
    All sample and source labels remain declarations. See ACCOUNT_REBALANCE_PREVIEW.md.
    """
    out = {"schema_version": SCHEMA, "status": "UNAVAILABLE", "execution_status": "PREVIEW_ONLY",
           "validation_status": "NOT VALIDATED", "actionability": "NOT ACTIONABLE", "source_verification": "DECLARED_INPUTS",
           "live_trading_supported": False, "orders_created": False, "sample_type": "unknown",
           "blocked_reasons": [], "rows": [], "constraints": [], "inputs_hash": None}
    declared = [value for value in (target, account, policy) if isinstance(value, dict)]
    if isinstance(target, dict) and isinstance(target.get("prior_target"), dict):
        declared.append(target["prior_target"])
    if isinstance(quotes, dict):
        declared.extend(value for value in quotes.values() if isinstance(value, dict))
    if any(value.get("sample_type") == "synthetic_fixture" for value in declared):
        out["sample_type"] = "synthetic_fixture"
    try:
        clock = _time(now, "now")
        out["asof"] = clock.isoformat()
        try:
            encoded = json.dumps({"target": target, "account": account, "quotes": quotes, "policy": policy, "now": out["asof"]},
                                 sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise _Unavailable("INVALID_INPUT", "inputs", "Inputs must be finite JSON values; use decimal strings.") from None
        out["inputs_hash"] = sha256(encoded).hexdigest()
        with localcontext() as context:
            context.prec = 80
            _calculate(target, account, quotes, policy, clock, out)
    except _Unavailable as exc:
        out["blocked_reasons"].append(exc.reason)
    return out
