import copy

import pytest

from market_intel.platform.activity import ActivityStore
from market_intel.platform.event_sensitivity import evaluate_event_sensitivity
from market_intel.platform.service import PlatformInputError


def fixture(tmp_path):
    sources = [{"name": "source.csv", "sha256": "a" * 64}]
    artifact = {"artifact_id": "perf_test", "strategy_id": "msci_rebalance", "name": "MSCI", "variant": "10 bps",
                "costs": "10 bps already embedded", "clock": "15:49 open to 15:50 bar close", "sources": sources,
                "limitations": ["Retrospective"], "rows": [{"month": "2020-01", "return": .01}, {"month": "2020-02", "return": 0}, {"month": "2020-03", "return": -.02}]}
    rows = [{"date": "2020-01-31", "return": .01, "nav_open": 1, "nav_close": 1.01,
             "net_pnl": .01, "gross_open": 1, "gross_close": 0, "margin_open": None, "margin_close": None,
             "event_id": "2020-01-31", "active": True},
            {"date": "2020-03-31", "return": -.02, "nav_open": 1.01, "nav_close": .9898,
             "net_pnl": -.0202, "gross_open": .5, "gross_close": 0, "margin_open": None, "margin_close": None,
             "event_id": "2020-03-31", "active": True}]
    profile = {"return_artifact_id": artifact["artifact_id"], "strategy_id": "msci_rebalance", "frequency": "event",
               "sources": sources, "capital_basis": "Normalized NAV", "margin_basis": "unavailable", "limitations": ["Proxy fills"], "rows": rows}
    return artifact, ActivityStore(tmp_path / "activity.sqlite").put(profile, artifact)


def test_event_scale_uses_embedded_net_cost_and_exposure_cost_once(tmp_path):
    artifact, profile = fixture(tmp_path)
    result = evaluate_event_sensitivity(artifact, profile, "2020-01", "2020-03")
    base = result["scenarios"][0]
    assert base["total_return"] == pytest.approx(1.01 * .98 - 1)
    x = next(s for s in result["scenarios"] if s["multiplier"] == 2 and s["extra_cost_bps"] == 10)
    # First event 2*(.01-.001*1)=.018; second 2*(-.02-.001*.5)=-.041.
    assert [r["return"] for r in x["event_returns"]] == pytest.approx([.018, -.041])
    assert x["total_return"] == pytest.approx(1.018 * .959 - 1)
    assert result["extra_cost_break_even_bps"] == pytest.approx(-.01 / 1.5 * 10000)
    assert len(result["scenarios"]) == 16
    assert x["initial_10pct_sleeve_return_contribution_other_cash"] == pytest.approx(.1 * x["total_return"])
    assert "cagr" not in x
    assert result == evaluate_event_sensitivity(artifact, profile, "2020-01", "2020-03")


def test_idle_event_month_and_parent_or_unknown_exposure_guard(tmp_path):
    artifact, profile = fixture(tmp_path)
    idle = evaluate_event_sensitivity(artifact, profile, "2020-02", "2020-02")
    assert idle["event_count"] == 0 and idle["mean_net_bps"] is None
    assert all(s["total_return"] == 0 for s in idle["scenarios"])
    wrong = copy.deepcopy(profile)
    wrong["return_artifact_id"] = "other"
    with pytest.raises(PlatformInputError, match="parent"):
        evaluate_event_sensitivity(artifact, wrong, "2020-01", "2020-03")
    wrong = copy.deepcopy(profile)
    wrong["rows"][0]["gross_open"] = None
    with pytest.raises(PlatformInputError, match="opening gross"):
        evaluate_event_sensitivity(artifact, wrong, "2020-01", "2020-03")


def test_baseline_variant_already_embedded_cost_is_not_replaced(tmp_path):
    artifact, profile = fixture(tmp_path)
    artifact["variant"] = "20 bps"  # Source name changes never authorize subtracting another 10/20 bps.
    result = evaluate_event_sensitivity(artifact, profile, "2020-01", "2020-03")
    assert result["scenarios"][0]["total_return"] == pytest.approx(1.01 * .98 - 1)


def test_scaled_insolvency_is_not_compounded_through_negative_nav(tmp_path):
    artifact, profile = fixture(tmp_path)
    artifact["rows"] = [{"month": "2020-01", "return": -.25}]
    profile["rows"] = [dict(profile["rows"][0], **{"return": -.25, "nav_open": 1, "nav_close": .75, "net_pnl": -.25})]
    with pytest.raises(PlatformInputError, match="insolvent"):
        evaluate_event_sensitivity(artifact, profile, "2020-01", "2020-01")
