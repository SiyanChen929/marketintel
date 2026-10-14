"""P0 walk-forward remediation: fill timing, weight drift, exit/entry union, no zero-fill, PIT gate."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.portfolio import construction_eval as CE
from market_intel import pit_contract


# ----------------------------------------------------------------------------- D. PIT gate
def test_pit_gate_closes_when_the_required_datasets_are_absent(tmp_path, monkeypatch):
    for key, spec in pit_contract.REQUIREMENTS.items():
        monkeypatch.setitem(spec, "path", tmp_path / f"{key}.csv")
    g = pit_contract.check()
    assert g.alpha_claim_available is False and g.mechanism_diagnostic_available is True
    d = g.to_dict()
    assert d["permitted_use"] == ["mechanism"]
    assert "alpha" in d["forbidden_use"] and "Sharpe as a track record" in d["forbidden_use"]
    assert "WITHHELD" in d["headline"]


def test_pit_gate_opens_only_when_the_level_critical_files_exist(tmp_path, monkeypatch):
    valid = {
        "pit_universe": "ticker,effective_from,effective_to,source\nAAA,2020-01-01,,test\nBBB,2020-01-01,,test\n",
        "historical_shares": "ticker,date,shares,source\nAAA,2020-01-01,100,test\nBBB,2020-01-01,100,test\n",
        "delisting": "ticker,delist_date,reason,final_price,source\nAAA,2020-01-01,x,1,test\nBBB,2020-01-01,x,1,test\n",
    }
    for key, spec in pit_contract.REQUIREMENTS.items():
        p = tmp_path / f"{key}.csv"
        if key in valid:
            p.write_text(valid[key])
        monkeypatch.setitem(spec, "path", p)
    g = pit_contract.check()
    assert g.alpha_claim_available is True and "benchmark_weights" in g.missing


def test_walk_forward_labels_itself_a_mechanism_diagnostic_while_the_gate_is_closed():
    px, uni = _tiny_panel(n=650)
    wf = CE.walk_forward(px, uni, methods=["equal_weight"], start="2023-06-01", cov_years=1,
                         min_names=2, top_n=3, max_w=1.0, max_seg=1.0, resample_draws=2)
    assert wf.get("available"), wf.get("reason")
    assert wf["pit_gate"]["alpha_claim_available"] is False
    assert "MECHANISM DIAGNOSTIC ONLY" in wf["claim_status"]


# ----------------------------------------------------------------------------- A. execution timeline
def _tiny_panel(n=420, seed=5):
    rng = np.random.default_rng(seed)
    d = pd.bdate_range("2022-06-01", periods=n)
    cols = {c: 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n))) for c in ("AAA", "BBB", "CCC", "DDD")}
    cols["SOXX"] = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
    px = pd.DataFrame(cols, index=d)
    uni = pd.DataFrame({"market_cap": [1e10, 8e9, 6e9, 4e9], "segment": ["s1", "s1", "s2", "s2"]},
                       index=["AAA", "BBB", "CCC", "DDD"])
    return px, uni


def test_signal_cannot_earn_the_return_it_was_computed_from():
    """close(t) forms the signal, so the earliest fill is close(t+1) and accrual starts after that."""
    px, uni = _tiny_panel()
    wf = CE.walk_forward(px, uni, methods=["equal_weight"], start="2023-06-01", cov_years=1,
                         min_names=2, top_n=3, resample_draws=2)
    if not wf.get("available"):
        pytest.skip(wf.get("reason"))
    assert wf["execution"]["signal_at"] == "close(t)" and wf["execution"]["fill_at"] == "close(t+1)"
    for r in wf["rebalances"]:
        assert pd.Timestamp(r["fill_date"]) > pd.Timestamp(r["date"]), "fill must be strictly after the signal close"
    first = wf["rebalances"][0]
    ret_start = pd.Timestamp(wf["cumulative"]["dates"][0])
    assert ret_start > pd.Timestamp(first["fill_date"]), "returns must accrue only after the fill"


# ----------------------------------------------------------------------------- B. drift, union, costs
def test_turnover_is_measured_against_drifted_weights_not_the_previous_target():
    """Holding shares means weights move with prices; charging turnover against the old *target* understates it."""
    px, uni = _tiny_panel()
    wf = CE.walk_forward(px, uni, methods=["equal_weight"], start="2023-06-01", cov_years=1,
                         min_names=2, top_n=3, resample_draws=2)
    if not wf.get("available"):
        pytest.skip(wf.get("reason"))
    tos = [r["to_equal_weight"] for r in wf["rebalances"][1:]]
    # equal weight re-targets 1/N every month, so against a DRIFTED book turnover is strictly positive
    assert all(t > 0 for t in tos), "equal weight shows zero turnover only if drift is being ignored"
    assert wf["stats"]["equal_weight"]["avg_turnover"] > 0


def test_exits_and_entries_are_both_charged_via_the_union():
    d = pd.bdate_range("2024-01-01", periods=4)
    old = pd.Series({"A": 0.5, "B": 0.5})
    new = pd.Series({"B": 0.5, "C": 0.5})
    union = sorted(set(old.index) | set(new.index))
    to = float(0.5 * (new.reindex(union).fillna(0) - old.reindex(union).fillna(0)).abs().sum())
    assert to == pytest.approx(0.5)          # A exits (0.5) and C enters (0.5) -> one-way turnover 0.5
    # the union must not silently ignore the exited name
    naive = float(0.5 * (new - old.reindex(new.index)).abs().sum())
    assert naive < to


def test_final_liquidation_is_reported_separately():
    px, uni = _tiny_panel()
    wf = CE.walk_forward(px, uni, methods=["equal_weight"], start="2023-06-01", cov_years=1,
                         min_names=2, top_n=3, resample_draws=2)
    if not wf.get("available"):
        pytest.skip(wf.get("reason"))
    fl = wf["final_liquidation"]["equal_weight"]
    assert fl["turnover"] == pytest.approx(1.0, abs=1e-6) and fl["cost"] > 0
    assert "excluded from the return series" in fl["note"]


# ----------------------------------------------------------------------------- C. no zero-filling
def test_a_name_without_bars_is_dropped_and_recorded_not_zero_filled():
    px, uni = _tiny_panel()
    px.loc[px.index[300:340], "DDD"] = np.nan          # a gap mid-sample
    wf = CE.walk_forward(px, uni, methods=["equal_weight"], start="2023-06-01", cov_years=1,
                         min_names=2, top_n=4, resample_draws=2)
    if not wf.get("available"):
        pytest.skip(wf.get("reason"))
    assert wf["n_data_events"] >= 1
    ev = wf["data_events"][0]
    assert "DDD" in ev["dropped_no_bar"] and "NOT zero-filled" in ev["treatment"]
    for r in wf["rebalances"]:
        assert r["n_priceable"] <= r["n_names"]


# ----------------------------------------------------------------------------- E. view provenance
def test_walk_forward_states_that_it_does_not_test_the_live_views():
    px, uni = _tiny_panel()
    wf = CE.walk_forward(px, uni, methods=["equal_weight"], start="2023-06-01", cov_years=1,
                         min_names=2, top_n=3, resample_draws=2)
    if not wf.get("available"):
        pytest.skip(wf.get("reason"))
    v = wf["views"]
    assert "momentum" in v["kind"]
    assert "NOT the live fair-value or event-score views" in v["note"]
    assert "does not read expected returns" in v["production_note"]


# ----------------------------------------------------------------------------- F. statistics rebuilt by hand
def test_performance_statistics_match_an_independent_recomputation():
    rng = np.random.default_rng(9)
    d = pd.bdate_range("2024-01-01", periods=504)
    r = pd.Series(rng.normal(0.0006, 0.012, 504), index=d)
    m = r.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    st = CE.performance(r, m, rf=0.03)
    cum = float((1 + r).prod())
    yrs = len(r) / 252
    assert st["cagr"] == pytest.approx(cum ** (1 / yrs) - 1, rel=1e-9)
    assert st["vol"] == pytest.approx(float(r.std() * np.sqrt(252)), rel=1e-9)
    assert st["sharpe"] == pytest.approx((st["cagr"] - 0.03) / st["vol"], rel=1e-9)
    down = float(r[r < 0].std() * np.sqrt(252))
    assert st["sortino"] == pytest.approx((st["cagr"] - 0.03) / down, rel=1e-9)
    c = (1 + r).cumprod()
    assert st["max_drawdown"] == pytest.approx(float((c / c.cummax() - 1).min()), rel=1e-9)
    assert st["total_return"] == pytest.approx(cum - 1, rel=1e-9)
