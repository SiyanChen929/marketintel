from __future__ import annotations

import copy

import numpy as np
import pytest

from market_intel.platform.allocation import POLICY, _simulate, evaluate_policy
from market_intel.platform.service import PlatformBlocked, PlatformInputError


def fixture(sid="a", *, count=42, event=False, inactive=False, currency="USD"):
    """Start February, so first scoring month tests an irregular first quarter."""
    months = [f"{2020 + (i + 1) // 12:04d}-{(i + 1) % 12 + 1:02d}" for i in range(count)]
    rows = []
    for i, month in enumerate(months):
        if event:
            if i % 3 == 0:
                rows.append({"date": month + "-15", "return": 0., "active": not inactive})
        else:
            rows.extend([{"date": month + "-10", "return": 0., "active": not inactive},
                         {"date": month + "-20", "return": 0., "active": not inactive}])
    artifact = {"artifact_id": f"perf_{sid}", "strategy_id": sid, "name": sid, "variant": "frozen",
                "run_id": "run1", "currency": currency, "sample_type": "historical_research",
                "source_verdict": "NOT VALIDATED", "limitations": ["Source execution proxy"], "sources": [],
                "rows": [{"month": month, "return": 0.} for month in months]}
    return artifact, {"frequency": "event" if event else "daily", "rows": rows}


def set_return(artifact, profile, month, value):
    matching = [row for row in profile["rows"] if row["date"].startswith(month)]
    matching[0]["return"] = value
    next(row for row in artifact["rows"] if row["month"] == month)["return"] = value


def evaluate(*pairs, **kwargs):
    return evaluate_policy([pair[0] for pair in pairs], [pair[1] for pair in pairs], **kwargs)


def test_first_month_then_calendar_quarters_strictly_prior_training():
    a = fixture()
    result = evaluate(a)
    assert result["warmup_start"] == "2020-02"
    assert result["warmup_end"] == "2023-01"
    assert result["start"] == "2023-02"
    assert [d["month"] for d in result["decisions"]] == ["2023-02", "2023-04", "2023-07"]
    first = result["decisions"][0]
    assert first["training_start"] == "2020-02-01"
    assert first["training_end"] == "2023-01-31"
    assert first["estimates"][0]["last_observation"] == "2023-01-20"
    assert first["estimates"][0]["n_active"] == 72
    assert result["policy"]["parameter_status"].startswith("fixed research policy")


def test_future_returns_cannot_change_already_decided_weights_or_prior_nav():
    a, b = fixture("a"), fixture("b")
    original = evaluate(a, b)
    changed = copy.deepcopy(a)
    set_return(*changed, "2023-05", -.5)
    later = evaluate(changed, b)
    assert later["decisions"][:2] == original["decisions"][:2]
    assert later["rows"][:3] == original["rows"][:3]
    assert later["decisions"][2]["weights"] != original["decisions"][2]["weights"]


def test_scoring_month_return_never_informs_its_own_target():
    a, b = fixture("a"), fixture("b")
    before = evaluate(a, b)
    set_return(*a, "2023-02", -.6)
    after = evaluate(a, b)
    assert after["decisions"][0] == before["decisions"][0]
    assert after["rows"][0]["nav"] < before["rows"][0]["nav"]
    assert after["decisions"][1]["weights"][0] < before["decisions"][1]["weights"][0]


def test_training_observations_outside_36_month_window_do_not_affect_decision():
    a, b = fixture("a"), fixture("b")
    original = evaluate(a, b)
    set_return(*a, "2020-02", -.8)
    changed = evaluate(a, b)
    assert changed["decisions"][0]["weights"] != original["decisions"][0]["weights"]
    assert changed["decisions"][1] == original["decisions"][1]


def test_event_cap_and_cash_clipping_never_renormalize_to_full_investment():
    result = evaluate(fixture("a"), fixture("b"), fixture("c"), fixture("event", event=True))
    first = result["decisions"][0]
    assert first["weights"] == pytest.approx([.25, .25, .25, .1])
    assert first["cash_weight"] == pytest.approx(.15)
    assert first["cap_excess_cash"] == pytest.approx(.15)
    event = first["estimates"][-1]
    assert event["n_active"] == 12
    assert event["risk_components"]["calendar_sigma"] is None
    assert event["risk_unit"] == .003
    assert event["capped"]
    assert result["selected"][-1]["weight"] == .1
    assert result["cash_weight"] == pytest.approx(.15)


def test_insufficient_event_budget_stays_cash_instead_of_granting_event_cap():
    event = fixture("event", event=True)
    event[1]["rows"][0]["active"] = False
    result = evaluate(fixture("daily"), event)
    first = result["decisions"][0]
    assert first["weights"] == pytest.approx([.4, 0])
    assert first["cash_weight"] == pytest.approx(.6)
    assert first["unestimated_budget_cash"] == .5
    assert first["cap_excess_cash"] == pytest.approx(.1)
    estimate = first["estimates"][-1]
    assert estimate["risk_unit"] is None
    assert estimate["reason"] == "INSUFFICIENT_INDEPENDENT_EVENTS"


def test_continuous_active_sample_gate_and_all_cash_portfolio():
    daily = fixture(inactive=True)
    for row in daily[1]["rows"][:19]:
        row["active"] = True
    result = evaluate(daily)
    assert result["decisions"][0]["estimates"][0]["n_active"] == 19
    assert result["decisions"][0]["weights"] == [0]
    assert result["cash_weight"] == 1
    assert result["metrics"]["total_return"] == 0
    assert result["metrics"]["sharpe_zero_rf"] is None


def test_conditional_activity_sigma_and_loss_guard_not_diluted_by_idle_days():
    a = fixture()
    for row in a[1]["rows"]:
        row["active"] = row["date"].endswith("-10")
    for i, month in enumerate([row["month"] for row in a[0]["rows"][:36]]):
        set_return(*a, month, -.03 if i % 2 else .03)
    result = evaluate(a)
    estimate = result["decisions"][0]["estimates"][0]
    assert estimate["risk_components"]["active_sigma"] > estimate["risk_components"]["calendar_sigma"]
    assert estimate["risk_unit"] == pytest.approx(np.std([.03, -.03] * 18, ddof=1))
    assert estimate["risk_components"]["worst_active_loss_div_3"] == pytest.approx(.01)


def test_baselines_have_identical_evaluation_window_and_preserve_their_rules():
    a = fixture()
    set_return(*a, "2023-02", .1)
    result = evaluate(a, fixture("event", event=True), allocation_cost_bps=5)
    for baseline in result["baselines"].values():
        assert baseline["start"] == result["start"]
        assert baseline["end"] == result["end"]
        assert baseline["observations"] == result["observations"]
        assert [row["month"] for row in baseline["rows"]] == [row["month"] for row in result["rows"]]
    assert result["baselines"]["equal_initial"]["rows"][0]["opening_weights"] == [.5, .5, 0]
    assert sum(row["rebalanced"] for row in result["baselines"]["equal_initial"]["rows"]) == 1
    assert sum(row["rebalanced"] for row in result["baselines"]["equal_quarterly"]["rows"]) == len(result["decisions"])


def test_quarter_start_fee_and_post_fee_funding_reconcile_by_hand():
    months = ["2023-02", "2023-03", "2023-04", "2023-05"]
    decisions = [{"month": month, "weights": [.4, .4], "cash_weight": .2} for month in ("2023-02", "2023-04")]
    result = _simulate(months, np.array([[.1, 0], [0, 0], [0, .1], [0, 0]]), decisions, 100)
    # Before April: .44/.40/.20; target .416/.416/.208. Noncash turnover .04.
    # Fee .0004; post-fee capital .41584/.41584/.20792; second sleeve gains .041584.
    assert [r["nav"] for r in result["rows"]] == pytest.approx([1.04, 1.04, 1.081184, 1.081184])
    assert [r["allocation_cost"] for r in result["rows"]] == pytest.approx([0, 0, .0004, 0])
    assert result["pnl_contribution"] == pytest.approx([.04, .041584])
    assert result["accounting_error"] < 1e-14
    assert sum(result["risk_contribution"]) + result["allocation_cost_risk_contribution"] == pytest.approx(1)
    assert result["rows"][-1]["sleeve_nav"] == pytest.approx([1.1, 1.1])


def test_insufficient_history_and_missing_daily_month_fail_closed():
    with pytest.raises(PlatformBlocked, match="36 complete common"):
        evaluate(fixture(count=38))
    a = fixture()
    a[1]["rows"] = [row for row in a[1]["rows"] if not row["date"].startswith("2021-01")]
    with pytest.raises(PlatformBlocked, match="no observations"):
        evaluate(a)


def test_monthly_and_activity_returns_must_describe_same_frozen_nav():
    a = fixture()
    a[1]["rows"][0]["return"] = .1
    with pytest.raises(PlatformBlocked, match="do not reconcile"):
        evaluate(a)
    event = fixture(event=True)
    event[0]["rows"][1]["return"] = .01
    with pytest.raises(PlatformBlocked, match="do not reconcile"):
        evaluate(event)


@pytest.mark.parametrize("change", [
    lambda pair: pair[1]["rows"].insert(1, copy.deepcopy(pair[1]["rows"][0])),
    lambda pair: pair[1]["rows"].reverse(),
])
def test_duplicate_or_out_of_order_dates_cannot_inflate_sample_size(change):
    a = fixture()
    change(a)
    with pytest.raises(PlatformBlocked, match="sorted and unique"):
        evaluate(a)


@pytest.mark.parametrize("change", [
    lambda pair: pair[1]["rows"][0].update({"return": float("nan")}),
    lambda pair: pair[1]["rows"][0].update(active=1),
    lambda pair: pair[1].update(frequency="minute"),
])
def test_malformed_activity_inputs_fail_closed(change):
    a = fixture()
    change(a)
    with pytest.raises(PlatformInputError):
        evaluate(a)


def test_currency_source_status_and_requested_end_are_enforced():
    a, b = fixture("a"), fixture("b", currency="CNY")
    with pytest.raises(PlatformBlocked, match="mixed currencies"):
        evaluate(a, b, currency_mode="native")
    assert evaluate(a, b)["currency_label"].startswith("Fixed FX")
    with pytest.raises(PlatformBlocked, match="exceeds complete common"):
        evaluate(a, end="2024-01")
    b[0]["source_verdict"] = "INVALID"
    with pytest.raises(PlatformBlocked, match="INVALID"):
        evaluate(a, b)


def test_result_is_reproducible_and_does_not_mutate_inputs_or_policy():
    a = fixture()
    before = copy.deepcopy(a)
    first, second = evaluate(a), evaluate(a)
    assert first == second
    assert a == before
    assert first["validation_status"] == "NOT VALIDATED"
    assert first["request"]["method"] == "activity_risk_budget"
    first["policy"]["risk_floor"] = 999
    assert POLICY["risk_floor"] == .003
