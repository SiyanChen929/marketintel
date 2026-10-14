from __future__ import annotations

import copy

import pytest

from market_intel.platform.allocation import evaluate_policy
from market_intel.platform.exposure import evaluate_exposure
from market_intel.platform.service import PlatformBlocked, PlatformInputError
from test_platform_allocation import fixture, set_return


def sources(*pairs):
    artifacts, profiles = [p[0] for p in pairs], [p[1] for p in pairs]
    return artifacts, profiles, evaluate_policy(artifacts, profiles)


def test_unit_multipliers_reproduce_original_nav_fees_risk_and_fixed_decisions():
    a, b = fixture("commodity_arbitrage"), fixture("vol_trading")
    set_return(*a, "2023-02", .05)
    set_return(*b, "2023-04", -.03)
    artifacts, profiles, _ = sources(a, b)
    base = evaluate_policy(artifacts, profiles, allocation_cost_bps=10)
    result = evaluate_exposure(artifacts, profiles, base)
    assert [row["nav"] for row in result["rows"]] == pytest.approx([row["nav"] for row in base["rows"]], abs=1e-14)
    assert result["allocation_cost"] == pytest.approx(base["allocation_cost"], abs=1e-14)
    assert result["risk_contribution"] == pytest.approx(base["risk_contribution"], abs=1e-12)
    assert result["decisions"] == base["decisions"]
    assert result["latest_cash_weight"] == base["latest_cash_weight"]
    assert result["baseline"]["analysis_id"] == base["analysis_id"]


def test_daily_scaling_then_compounding_differs_from_scaling_monthly_return():
    a = fixture("commodity_arbitrage")
    february = [row for row in a[1]["rows"] if row["date"].startswith("2023-02")]
    february[0]["return"], february[1]["return"] = .1, -.1
    next(row for row in a[0]["rows"] if row["month"] == "2023-02")["return"] = -.01
    result = evaluate_exposure(*sources(a), multipliers={"commodity_arbitrage": 2})
    # (1+.2)(1-.2)-1 = -.04; funded sleeve .4 loses .016, cash stays .6.
    assert result["selected"][0]["metrics"]["total_return"] == pytest.approx(-.04)
    assert result["rows"][0]["nav"] == pytest.approx(.984)
    assert result["rows"][0]["nav"] != pytest.approx(.992)  # Incorrect 2 * monthly NAV return.
    assert result["rows"][0]["sleeve_nav"] == pytest.approx([.96])
    assert result["selected"][0]["pnl_contribution"] == pytest.approx(-.016)
    assert "funded sleeve NAV" in result["selected"][0]["return_basis"]
    assert result["accounting_error"] < 1e-14


def test_event_idle_months_remain_zero_without_inventing_daily_observations():
    event = fixture("msci_rebalance", event=True)
    set_return(*event, "2023-02", .01)
    result = evaluate_exposure(*sources(event), multipliers={"msci_rebalance": 3})
    assert result["rows"][0]["return"] == pytest.approx(.003)
    assert result["rows"][1]["return"] == 0
    assert result["rows"][2]["return"] == 0
    assert result["daily_diagnostics"][0]["observations"] == 2
    assert result["daily_diagnostics"][0]["active_observations"] == 2
    assert result["cash_weight"] == .9


def test_inactive_zero_returns_cannot_gain_returns_or_release_cash():
    a = fixture("pairs_trading", inactive=True)
    original = sources(a)
    result = evaluate_exposure(*original, multipliers={"pairs_trading": 5})
    assert result["metrics"]["total_return"] == 0
    assert result["cash_weight"] == 1
    assert result["latest_weights"] == [0]
    assert result["daily_diagnostics"][0]["active_observations"] == 0


def test_multiplier_loss_at_or_below_minus_one_blocks_default_accounting():
    a = fixture("commodity_arbitrage")
    set_return(*a, "2023-02", -.5)
    with pytest.raises(PlatformBlocked, match="100%"):
        evaluate_exposure(*sources(a), multipliers={"commodity_arbitrage": 2})


def test_multiplier_is_only_applied_in_evaluation_window_not_to_prior_training():
    a = fixture("commodity_arbitrage")
    set_return(*a, "2020-02", -.5)
    result = evaluate_exposure(*sources(a), multipliers={"commodity_arbitrage": 5})
    assert result["start"] == "2023-02"
    assert result["metrics"]["total_return"] == 0
    assert result["decisions"][0]["training_start"] == "2020-02-01"


def test_source_profile_changes_or_reordered_artifacts_cannot_reuse_base():
    artifacts, profiles, base = sources(fixture("commodity_arbitrage"), fixture("pairs_trading"))
    changed = copy.deepcopy(profiles)
    changed[0]["rows"][0]["active"] = False
    with pytest.raises(PlatformBlocked, match="pinned base"):
        evaluate_exposure(artifacts, changed, base)
    with pytest.raises(PlatformBlocked, match="pinned base"):
        evaluate_exposure(list(reversed(artifacts)), list(reversed(profiles)), base)


def test_tampered_base_decisions_are_rejected_before_scaling():
    artifacts, profiles, base = sources(fixture("pairs_trading"))
    base["decisions"][0]["weights"] = [.8]
    with pytest.raises(PlatformBlocked, match="checksum"):
        evaluate_exposure(artifacts, profiles, base)


@pytest.mark.parametrize("multipliers", [
    {"commodity_arbitrage": .5}, {"commodity_arbitrage": 5.1},
    {"commodity_arbitrage": True}, {"commodity_arbitrage": float("nan")},
    {"unselected_strategy": 1}, {"msci_rebalance": 2}, [2],
])
def test_unsupported_or_unused_multiplier_inputs_fail_closed(multipliers):
    with pytest.raises(PlatformInputError):
        evaluate_exposure(*sources(fixture("commodity_arbitrage")), multipliers=multipliers)


def test_vol_multiplier_and_generic_nav_costs_are_not_silently_enabled():
    src = sources(fixture("vol_trading"))
    with pytest.raises(PlatformBlocked, match="not enabled"):
        evaluate_exposure(*src, multipliers={"vol_trading": 2})
    with pytest.raises(PlatformBlocked, match="traded-notional"):
        evaluate_exposure(*src, extra_cost_bps=5)
    with pytest.raises(PlatformInputError, match="finite"):
        evaluate_exposure(*src, extra_cost_bps=float("inf"))


def test_observed_notional_uses_funded_capital_and_never_claims_synchronized_peak():
    a = fixture("commodity_arbitrage")
    for row in a[1]["rows"]:
        row.update(gross_open=.5, gross_close=.5, margin_open=.1, margin_close=.1)
    set_return(*a, "2023-02", .1)
    result = evaluate_exposure(*sources(a), multipliers={"commodity_arbitrage": 2})
    diagnostics = result["daily_diagnostics"][0]
    first = diagnostics["sample_observations"][0]
    assert first["gross_open_per_initial_joint_capital"] == pytest.approx(.4 * .5 * 2)
    assert first["gross_close_per_initial_joint_capital"] == pytest.approx(.4 * 1.2 * .5 * 2)
    assert first["margin_open_per_initial_joint_capital"] == pytest.approx(.4 * .1 * 2)
    assert diagnostics["synchronized_portfolio_peak"] is None
    assert result["financing_excluded"] is True
    assert result["funding_validated"] is False
    assert result["incremental_costs_status"] == "UNKNOWN_AND_EXCLUDED"
    assert result["status"] == "THEORETICAL_EXPOSURE_SCENARIO"


def test_partial_margin_evidence_remains_unavailable_and_inputs_are_immutable():
    a = fixture("pairs_trading")
    a[1]["rows"][-1]["margin_close"] = .1
    artifacts, profiles, base = sources(a)
    before = copy.deepcopy((artifacts, profiles, base))
    first = evaluate_exposure(artifacts, profiles, base, multipliers={"pairs_trading": 2})
    assert first["daily_diagnostics"][0]["observed_maxima"]["margin_close_per_initial_joint_capital"] is None
    assert (artifacts, profiles, base) == before
    assert first == evaluate_exposure(artifacts, profiles, base, multipliers={"pairs_trading": 2})


def test_same_window_and_risk_budget_are_preserved_for_all_scaled_sleeves():
    a, b = fixture("commodity_arbitrage"), fixture("pairs_trading")
    set_return(*a, "2023-02", .05)
    set_return(*b, "2023-05", -.03)
    artifacts, profiles, base = sources(a, b)
    result = evaluate_exposure(artifacts, profiles, base, multipliers={"commodity_arbitrage": 2, "pairs_trading": 3})
    assert (result["start"], result["end"], result["observations"]) == (base["start"], base["end"], base["observations"])
    assert result["decisions"] == base["decisions"]
    assert result["latest_weights"] == base["latest_weights"]
    assert result["metrics_delta"]["total_return"] == pytest.approx(result["metrics"]["total_return"] - base["metrics"]["total_return"])
    assert sum(result["risk_contribution"]) + result["allocation_cost_risk_contribution"] == pytest.approx(1)
    assert result["policy_sha256"] == base["policy_sha256"]
