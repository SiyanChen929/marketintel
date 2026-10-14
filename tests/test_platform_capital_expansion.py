import copy

import numpy as np
import pytest

from market_intel.platform.allocation import _simulate
from market_intel.platform.capital_expansion import _expanded_decisions, evaluate_capital_expansion
from market_intel.platform.service import PlatformBlocked
from scripts.assess_platform_capital_expansion import decimal_reconstruction


IDS = ["vol_trading", "commodity_arbitrage", "msci_rebalance", "pairs_trading"]


def decision(month="2025-02", weights=None, eligible=None):
    weights = weights or [.1, .35, .1, .2]
    eligible = eligible or [True] * 4
    return {"month": month, "training_start": "2022-02-01", "training_end": "2025-01-31",
            "weights": weights, "cash_weight": 1 - sum(weights),
            "estimates": [{"strategy_id": sid, "eligible": ok, "cap": .1 if sid == "msci_rebalance" else .4}
                          for sid, ok in zip(IDS, eligible)]}


def fixture(fee=5):
    months = ["2025-02", "2025-03", "2025-04", "2025-05"]
    matrix = np.array([[.04, .01, 0, -.01], [-.03, -.02, .004, .02], [.02, .01, -.002, .01], [.01, 0, 0, -.04]])
    decisions = [decision(), decision("2025-04", [.08, .37, .1, .22])]
    artifacts = [{"strategy_id": sid, "artifact_id": "perf_" + sid,
                  "rows": [{"month": month, "return": float(matrix[j, i])} for j, month in enumerate(months)]}
                 for i, sid in enumerate(IDS)]
    base = {**_simulate(months, matrix, decisions, fee), "decisions": decisions,
            "selected": [{"artifact_id": a["artifact_id"]} for a in artifacts],
            "request": {"allocation_cost_bps": fee, "currency_mode": "constant_fx"}, "analysis_id": "base_fixture"}
    return artifacts, base


def test_cash_limited_addition_uses_old_eligibility_and_never_touches_vol():
    d = decision(weights=[.1, .35, .1, .42])
    changed = _expanded_decisions([d], IDS, "commodity_arbitrage")[0]
    assert changed["added_capital_weights"] == pytest.approx([0, .03, 0, 0])
    assert sum(changed["weights"]) == pytest.approx(1)
    assert changed["cash_weight"] == pytest.approx(0)
    blocked = decision(weights=[.1, .35, 0, .2], eligible=[True, True, False, True])
    unchanged = _expanded_decisions([blocked], IDS, "msci_rebalance")[0]
    assert unchanged["weights"] == blocked["weights"]
    assert unchanged["cash_weight"] == blocked["cash_weight"]


def test_pro_rata_cash_excludes_ineligible_strategy_and_records_cap_changes():
    d = decision(weights=[.1, .35, 0, .2], eligible=[True, True, False, True])
    changed = _expanded_decisions([d], IDS, "pro_rata")[0]
    assert changed["weights"][0] == d["weights"][0]
    assert changed["weights"][2] == 0
    assert changed["weights"][1] / changed["weights"][3] == pytest.approx(.35 / .2)
    assert sum(changed["weights"]) == pytest.approx(1)
    assert any(x["strategy_id"] == "commodity_arbitrage" for x in changed["cap_overrides"])


def test_full_cash_scenario_independently_reconciles_fees_and_funded_accounts():
    artifacts, base = fixture()
    frozen = copy.deepcopy(base)
    out = evaluate_capital_expansion(artifacts, base)
    assert base == frozen
    assert len(out["scenarios"]) == 5
    assert out["scenarios"][0]["baseline_nav_reconstruction_max_error"] == 0
    all_cash = out["scenarios"][-1]
    assert all_cash["allocation_cost"] > 0
    audit = decimal_reconstruction(artifacts, all_cash, 5)
    assert audit["passed"]
    assert all(sum(d["weights"]) <= 1 + 1e-12 and d["cash_weight"] >= 0 for s in out["scenarios"] for d in s["decisions"])
    assert all(d["training_end"] < d["month"] + "-01" for s in out["scenarios"] for d in s["decisions"])


def test_unverified_baseline_or_non_prior_decision_fails_closed():
    artifacts, base = fixture()
    base["rows"][-1]["nav"] += .01
    with pytest.raises(PlatformBlocked, match="does not reproduce"):
        evaluate_capital_expansion(artifacts, base)
    late = decision()
    late["training_end"] = "2025-02-01"
    with pytest.raises(PlatformBlocked, match="strictly prior"):
        _expanded_decisions([late], IDS, "pro_rata")


def test_absent_strategy_is_omitted_and_no_eligible_budget_is_explicit():
    artifacts, base = fixture()
    keep = [0, 2, 3]  # USD-only selection has no Commodity sleeve.
    artifacts = [artifacts[i] for i in keep]
    for d in base["decisions"]:
        d["weights"] = [.1, 0., 0.]
        d["cash_weight"] = .9
        d["estimates"] = [d["estimates"][i] for i in keep]
        for estimate in d["estimates"][1:]:
            estimate["eligible"] = False
    months = [r["month"] for r in base["rows"]]
    matrix = np.asarray([[a["rows"][i]["return"] for a in artifacts] for i in range(len(months))])
    base.update(_simulate(months, matrix, base["decisions"], 5))
    base["selected"] = [{"artifact_id": a["artifact_id"]} for a in artifacts]
    out = evaluate_capital_expansion(artifacts, base)
    assert "commodity_plus_10pp" not in [s["id"] for s in out["scenarios"]]
    for scenario in out["scenarios"][1:]:
        assert scenario["reason"] == "NO_ELIGIBLE_BUDGET"
        assert not scenario["capital_addition_applied"]
        assert scenario["maximum_added_weight"] == 0
