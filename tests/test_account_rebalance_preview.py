"""Counterexamples for a read-only preview; synthetic arithmetic is not a track record."""
from copy import deepcopy
from decimal import Decimal as D, localcontext
import json
import random

import pytest

from ai_semi_intel.trading_policy import policy as target_policy
from market_intel.platform.rebalance_preview import ALLOCATION, build_preview

A, B = "equity:US:A", "equity:US:B"
NOW = "2026-09-22T13:31:00+00:00"


@pytest.fixture
def inputs():
    source = {"source": "explicit synthetic fixture", "content_sha256": "a" * 64, "sample_type": "synthetic_fixture"}
    target = {**source, "run_id": "synthetic-22", "construction": "risk_parity", "generated_at": "2026-09-22T12:00:00+00:00",
              "actionable_at_generation": True, "weights": {A: ".4", B: ".4"}, "cash": ".2",
              "rebalance_policy": target_policy("2026-09-22T12:00:00+00:00", "2026-09-21"),
              "prior_target": {**source, "run_id": "synthetic-21", "construction": "risk_parity",
                               "generated_at": "2026-09-21T12:00:00+00:00", "weights": {A: ".4", B: ".4"}, "cash": ".2"}}
    account = {**source, "account_id": "synthetic-100k", "asof": "2026-09-22T13:30:00+00:00", "acquired_at": "2026-09-22T13:30:01+00:00",
               "currency": "USD", "cash_total": "20000", "settled_cash": "20000", "available_cash": "20000",
               "unsettled_receivables": "0", "unsettled_payables": "0", "reserved_cash": "0", "positions": {A: "400", B: "800"}, "open_orders": []}
    quotes = {key: {**source, "instrument_id": key, "currency": "USD", "price_basis": "unadjusted", "finality": "confirmed",
                    "observed_at": "2026-09-22T13:30:30+00:00", "acquired_at": "2026-09-22T13:30:31+00:00", "bid": bid, "ask": ask}
              for key, bid, ask in ((A, "109.95", "110.05"), (B, "44.975", "45.025"))}
    sizing = {**source, "policy_id": "explicit-whole-share-cost-fixture-v1", "available_at": "2026-09-22T12:00:00+00:00",
              "max_account_age_seconds": 120, "max_quote_age_seconds": 60, "quantity_steps": {A: "1", B: "1"},
              "buy_allocation": ALLOCATION, "sell_fee_treatment": "deduct_from_proceeds",
              "costs": {"commission_fixed": "1", "commission_per_unit": "0", "commission_bps": "0", "spread_bps": "10", "slippage_bps": "5"}}
    return {"target": target, "account": account, "quotes": quotes, "policy": sizing}


def run(values, **kwargs):
    return build_preview(**values, now=kwargs.get("now", NOW))


def reason(result):
    assert result["status"] == "UNAVAILABLE" and result["rows"] == []
    return result["blocked_reasons"][0]["code"]


def test_same_target_zero_change_still_rebalances_drift_and_costs_reconcile(inputs):
    out = run(inputs)
    assert out["status"] == "AVAILABLE"
    rows = {row["instrument_id"]: row for row in out["rows"]}
    assert out["target_adjustment_ratio"] == "0"
    assert (rows[A]["pretrade_weight"], rows[B]["pretrade_weight"]) == ("0.44", "0.36")
    assert (rows[A]["planned_quantity_change"], rows[B]["planned_quantity_change"]) == ("-37", "88")
    assert out["estimated_costs"]["total"] == "10.25"
    assert out["estimated_economic_cash_after"] == "20099.75"
    assert out["estimated_after_equity"] == "99989.75"
    assert out["available_cash_after"] == "16034.82"
    assert out["estimated_unsettled_sell_proceeds"] == "4064.93"
    assert out["sample_type"] == "synthetic_fixture" and out["source_verification"] == "DECLARED_INPUTS"
    assert out["execution_status"] == "PREVIEW_ONLY" and out["orders_created"] is False
    assert out["account_age_seconds"] == "60" and out["quote_age_seconds"][A] == "30"


def test_same_inputs_deterministic_no_mutation_or_file_network_access(inputs, monkeypatch):
    before = deepcopy(inputs)
    expected = run(inputs)  # Populate installed calendar package's read-only cache.
    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not access file or network interfaces")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("pathlib.Path.open", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    with localcontext() as ctx:
        ctx.prec = 7
        assert run(inputs) == expected
    assert inputs == before
    assert json.loads(json.dumps(expected)) == expected


@pytest.mark.parametrize("field", ["target", "account", "quotes", "policy"])
def test_missing_inputs_never_create_a_default_account(inputs, field):
    inputs[field] = None
    assert reason(run(inputs)) == "MISSING_EVIDENCE"


@pytest.mark.parametrize("key", ["commission_fixed", "commission_per_unit", "commission_bps", "spread_bps", "slippage_bps"])
def test_all_cost_components_must_be_explicit_even_if_zero(inputs, key):
    del inputs["policy"]["costs"][key]
    assert reason(run(inputs)) == "INVALID_INPUT"


def test_cash_only_target_and_empty_positions_are_explicitly_supported(inputs):
    inputs["target"].update(weights={}, cash="1", prior_target=None)
    inputs["account"].update(positions={}, cash_total="100000", settled_cash="100000", available_cash="100000")
    inputs["quotes"] = {}
    out = run(inputs)
    assert out["status"] == "AVAILABLE" and out["rows"] == []
    assert out["estimated_after_equity"] == "100000" and out["estimated_costs"]["total"] == "0"


def test_no_prior_is_unknown_not_a_zero_position_baseline(inputs):
    del inputs["target"]["prior_target"]
    out = run(inputs)
    assert out["status"] == "AVAILABLE" and out["target_deltas"] is None
    assert out["target_adjustment_ratio"] is None
    assert all(row["target_weight_change"] is None for row in out["rows"])


def test_switch_uses_actual_account_and_reports_prior_construction(inputs):
    inputs["target"].update(construction="hrp", weights={A: ".3", B: ".5"})
    out = run(inputs)
    assert out["target_comparison_basis"] == "construction_switch"
    assert out["target_adjustment_ratio"] == "0.1"
    row = out["rows"][0]
    assert row["target_weight_change"] == "-0.1" and row["account_weight_gap"] == "-0.14"
    assert row["desired_quantity_change"] == "-128"


def test_selling_cannot_fund_buying_in_same_preview(inputs):
    inputs["account"].update(cash_total="0", settled_cash="0", available_cash="0", positions={A: "1000"})
    inputs["target"].update(weights={A: "0", B: "1"}, cash="0")
    out = run(inputs)
    assert out["status"] == "AVAILABLE"
    assert out["rows"][0]["planned_quantity_change"] == "-1000"
    assert out["rows"][1]["planned_quantity_change"] == "0"
    assert D(out["estimated_unsettled_sell_proceeds"]) > 100000
    assert out["available_cash_after"] == "0" and out["estimated_buy_cash_outflow"] == "0"
    assert out["constraints"][0]["code"] == "BUY_BUDGET_REDUCED"


@pytest.mark.parametrize("changes", [
    {"settled_cash": "10000"},
    {"settled_cash": "0", "unsettled_receivables": "20000", "available_cash": "20000"},
    {"cash_total": "19000", "unsettled_payables": "1000", "available_cash": "20000"},
    {"reserved_cash": "1"},
])
def test_inconsistent_or_unsettled_buying_power_is_rejected(inputs, changes):
    inputs["account"].update(changes)
    assert reason(run(inputs)) == "CASH_NOT_RECONCILED"


def test_receivable_in_equity_is_not_available_cash(inputs):
    inputs["account"].update(cash_total="20000", settled_cash="0", available_cash="0", unsettled_receivables="20000")
    out = run(inputs)
    assert out["pretrade_equity"] == "100000"
    assert out["estimated_buy_cash_outflow"] == "0"


def test_full_investment_is_downsized_to_cover_costs_and_integer_shares(inputs):
    inputs["target"].update(weights={A: "1"}, cash="0")
    inputs["account"].update(positions={}, cash_total="100000", settled_cash="100000", available_cash="100000")
    inputs["quotes"][A].update(bid="99.99", ask="100.01")
    out = run(inputs)
    row = out["rows"][0]
    assert row["desired_quantity_change"] == "1000" and row["planned_quantity_change"] == "998"
    assert out["estimated_buy_cash_outflow"] == "99900.8"
    assert out["available_cash_after"] == "99.2"


@pytest.mark.parametrize("mutate, expected", [
    (lambda v: v["account"].update(asof="2026-09-21T13:30:00+00:00"), "STALE_ACCOUNT"),
    (lambda v: v["account"].update(acquired_at="2026-09-22T13:32:00+00:00"), "FUTURE_EVIDENCE"),
    (lambda v: v["quotes"][A].update(observed_at="2026-09-22T13:29:00+00:00"), "INVALID_QUOTE_CLOCK"),
    (lambda v: v["quotes"][A].update(acquired_at="2026-09-22T13:32:00+00:00"), "INVALID_QUOTE_CLOCK"),
    (lambda v: v["policy"].update(max_quote_age_seconds=10), "STALE_QUOTE"),
    (lambda v: v["account"].update(open_orders=[{"id": "unresolved"}]), "OPEN_ORDERS_UNRESOLVED"),
    (lambda v: v["account"].pop("open_orders"), "OPEN_ORDERS_UNRESOLVED"),
    (lambda v: v["quotes"][A].update(price_basis="adjusted"), "INVALID_INPUT"),
    (lambda v: v["policy"]["quantity_steps"].pop(A), "INVALID_INPUT"),
    (lambda v: v["account"]["positions"].update({A: "400.5"}), "UNSUPPORTED_UNIT_REMAINDER"),
    (lambda v: v["policy"].pop("max_account_age_seconds"), "INVALID_INPUT"),
    (lambda v: v["policy"]["costs"].update(slippage_bps="NaN"), "INVALID_INPUT"),
    (lambda v: v["policy"]["quantity_steps"].update({A: "1e-9999999"}), "INVALID_INPUT"),
])
def test_clock_unit_and_identity_rejections(inputs, mutate, expected):
    mutate(inputs)
    assert reason(run(inputs)) == expected


def test_expired_target_is_unavailable_even_with_fresh_quotes(inputs):
    assert reason(run(inputs, now="2026-09-22T13:35:00+00:00")) == "TARGET_NOT_CURRENT"


def test_new_hash_binds_every_input_and_explicit_clock(inputs):
    baseline = run(inputs)["inputs_hash"]
    inputs["policy"]["costs"]["commission_fixed"] = "2"
    assert run(inputs)["inputs_hash"] != baseline
    assert run(inputs, now="2026-09-22T13:31:01+00:00")["inputs_hash"] != run(inputs)["inputs_hash"]


@pytest.mark.parametrize("cash,step,fee", [("0", "1", "1"), (".01", "1", "1"), ("100", "25", "1"),
                                          ("100000", "1", "100"), ("13.37", ".25", ".005"), ("100000", ".00000001", "0")])
def test_proportional_buy_budget_always_reconciles_without_resale_credit(inputs, cash, step, fee):
    inputs["account"].update(positions={}, cash_total="100000", settled_cash=cash, available_cash=cash,
                             unsettled_receivables=str(D("100000") - D(cash)))
    inputs["target"].update(weights={A: ".5", B: ".5"}, cash="0")
    inputs["policy"]["quantity_steps"] = {A: step, B: step}
    inputs["policy"]["costs"]["commission_fixed"] = fee
    out = run(inputs)
    assert out["status"] == "AVAILABLE"
    assert D(out["estimated_buy_cash_outflow"]) <= D(cash)
    assert D(out["available_cash_after"]) == D(cash) - D(out["estimated_buy_cash_outflow"])
    for row in out["rows"]:
        assert D(row["planned_quantity_change"]) % D(step) == 0
    assert abs(D(out["pretrade_equity"]) - D(out["estimated_after_equity"]) - D(out["estimated_costs"]["total"])) <= D("1e-8")


def test_tiny_sale_cannot_create_a_fee_cash_deficit(inputs):
    inputs["account"].update(positions={A: "1"}, cash_total="0", settled_cash="0", available_cash="0")
    inputs["target"].update(weights={}, cash="1")
    inputs["policy"]["costs"]["commission_fixed"] = "1000"
    out = run(inputs)
    assert out["status"] == "AVAILABLE" and out["estimated_costs"]["total"] == "0"
    assert out["rows"][0]["planned_quantity_change"] == "0"
    assert out["constraints"][0]["code"] == "SELL_COST_EXCEEDS_PROCEEDS"


def test_seeded_accounts_reconcile_independently_across_cash_fees_and_units(inputs):
    rng = random.Random(73209)
    for _ in range(1000):
        values = deepcopy(inputs)
        step = D(rng.choice(["1", ".25", "25", ".0001"]))
        quantity_a, quantity_b = D(rng.randint(0, 500)) * step, D(rng.randint(0, 500)) * step
        settled, receivables = D(rng.randrange(0, 10000001)) / 100, D(rng.randrange(0, 10000001)) / 100
        available = settled * D(rng.randrange(0, 101)) / 100
        weight_a = D(rng.randrange(0, 101)) / 100
        weight_b = (D(1) - weight_a) * D(rng.randrange(0, 101)) / 100
        values["target"].update(weights={A: str(weight_a), B: str(weight_b)}, cash=str(1 - weight_a - weight_b))
        values["account"].update(positions={A: str(quantity_a), B: str(quantity_b)}, cash_total=str(settled + receivables),
                                 settled_cash=str(settled), available_cash=str(available), unsettled_receivables=str(receivables))
        values["policy"]["quantity_steps"] = {A: str(step), B: str(step)}
        values["policy"]["costs"].update(commission_fixed=str(D(rng.randrange(0, 2000)) / 100),
                                         commission_per_unit=str(D(rng.randrange(0, 100)) / 1000), commission_bps=str(rng.randrange(0, 100)))
        out = run(values)
        assert out["status"] == "AVAILABLE"
        bought = sum(D(r["estimated_notional"]) + D(r["estimated_commission"]) for r in out["rows"] if D(r["planned_quantity_change"]) > 0)
        sold = sum(D(r["estimated_notional"]) - D(r["estimated_commission"]) for r in out["rows"] if D(r["planned_quantity_change"]) < 0)
        assert bought <= available and D(out["available_cash_after"]) >= 0
        assert abs(D(out["available_cash_after"]) - (available - bought)) < D("1e-15")
        assert abs(D(out["estimated_economic_cash_after"]) - (settled + receivables + sold - bought)) < D("1e-12")
        assert all(D(r["estimated_after_quantity"]) >= 0 and D(r["planned_quantity_change"]) % step == 0 for r in out["rows"])
