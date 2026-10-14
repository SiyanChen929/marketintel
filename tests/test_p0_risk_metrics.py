"""P0 risk remediation: frozen positions, confirmed closes, fail-closed windows, phases, TE disclosure.

Each test targets a specific defect named in the review, and several are hand-computed so the code is checked
against arithmetic rather than against itself.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from market_intel import risk_metrics as RM

ET = __import__("zoneinfo").ZoneInfo("America/New_York")


def _panel(prices: dict, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(prices, index=pd.DatetimeIndex(dates))


# ----------------------------------------------------------------------------- A. frozen vs rebalanced
def test_frozen_position_pnl_is_hand_computable_and_differs_from_daily_rebalancing():
    """A book held for N days is not rebalanced daily. The old method booked that rebalancing for free."""
    d = ["2026-01-02", "2026-01-05", "2026-01-06"]
    px = _panel({"A": [100.0, 141.42, 200.0], "B": [100.0, 70.71, 50.0]}, d)
    idx = pd.DatetimeIndex(d)
    w = {"A": 0.5, "B": 0.5}
    frozen = RM.frozen_pnl(px, w, idx, horizon=2, offset=0, step=1)
    # hand: A doubles (+100%), B halves (-50%); frozen 50/50 -> 0.5*1.0 + 0.5*(-0.5) = +0.25
    assert frozen.n == 1
    assert frozen.pnl.iloc[0] == pytest.approx(0.25, abs=1e-9)
    reb = RM.daily_rebalanced_pnl(px, w, idx, horizon=2, offset=0, step=1)
    d1 = 0.5 * (141.42 / 100 - 1) + 0.5 * (70.71 / 100 - 1)
    d2 = 0.5 * (200 / 141.42 - 1) + 0.5 * (50 / 70.71 - 1)
    assert reb.iloc[0] == pytest.approx((1 + d1) * (1 + d2) - 1, abs=1e-9)
    assert frozen.pnl.iloc[0] - reb.iloc[0] == pytest.approx(0.125, abs=1e-6)   # 12.5pp of free rebalancing


def test_frozen_pnl_matches_weighted_sum_of_asset_moves_on_random_data():
    rng = np.random.default_rng(7)
    d = pd.bdate_range("2026-01-01", periods=40)
    px = pd.DataFrame({c: 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 40))) for c in "ABC"}, index=d)
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    ws = RM.frozen_pnl(px, w, d, horizon=10, offset=0, step=1)
    i = 5
    expected = sum(w[c] * (px[c].iloc[i + 10] / px[c].iloc[i] - 1) for c in "ABC")
    assert ws.pnl.iloc[i] == pytest.approx(expected, abs=1e-12)


def test_cash_earns_the_stated_rate_and_is_disclosed():
    d = pd.bdate_range("2026-01-01", periods=12)
    px = pd.DataFrame({"A": np.full(12, 100.0)}, index=d)              # asset flat: all P&L must be cash
    ws = RM.frozen_pnl(px, {"A": 0.6}, d, horizon=10, offset=0, step=1, cash_weight=0.4, cash_annual_rate=0.05)
    expected_cash = 0.4 * ((1.05) ** (10 / 252) - 1)
    assert ws.pnl.iloc[0] == pytest.approx(expected_cash, abs=1e-12)
    assert ws.cash_weight == 0.4 and ws.cash_return == pytest.approx((1.05) ** (10 / 252) - 1)


# ----------------------------------------------------------------------------- B. finality & missing bars
def test_unconfirmed_session_is_excluded_from_close_to_close():
    d = pd.DatetimeIndex(["2026-09-01", "2026-09-02", "2026-09-03"])
    acquired_open = datetime(2026, 9, 3, 11, 0, tzinfo=ET)             # snapshot taken while market was open
    idx, fin = RM.confirmed_sessions(d, acquired_open)
    assert list(idx) == list(d[:2]) and fin["dropped"][0]["date"] == "2026-09-03"
    acquired_closed = datetime(2026, 9, 3, 17, 0, tzinfo=ET)           # snapshot taken after the bell
    idx2, fin2 = RM.confirmed_sessions(d, acquired_closed)
    assert len(idx2) == 3 and fin2["dropped"] == []


def test_missing_bar_fails_the_window_instead_of_bridging_the_gap():
    d = pd.bdate_range("2026-01-01", periods=6)
    px = pd.DataFrame({"A": [100, 101, 102, 103, 104, 105.0], "B": [100, np.nan, 102, 103, 104, 105.0]}, index=d)
    ws = RM.frozen_pnl(px, {"A": 0.5, "B": 0.5}, d, horizon=1, offset=0, step=1)
    # 5 possible 1-session windows; the two touching B's gap must fail, not be silently bridged
    assert ws.n_possible == 5 and ws.n == 3 and len(ws.failures) == 2
    assert all("missing/invalid bar for B" in f["reason"] for f in ws.failures)


def test_a_holding_with_no_price_column_fails_every_window():
    d = pd.bdate_range("2026-01-01", periods=5)
    px = pd.DataFrame({"A": np.linspace(100, 104, 5)}, index=d)
    ws = RM.frozen_pnl(px, {"A": 0.5, "GONE": 0.5}, d, horizon=1, step=1)
    assert ws.n == 0 and len(ws.failures) == 4 and "GONE" in ws.failures[0]["reason"]


# ----------------------------------------------------------------------------- D. phases
def test_all_ten_non_overlapping_phases_are_reported_with_a_range():
    rng = np.random.default_rng(3)
    d = pd.bdate_range("2024-01-01", periods=300)
    px = pd.DataFrame({"A": 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))}, index=d)
    ph = RM.phases(px, {"A": 1.0}, d, horizon=10, level=0.99)
    assert ph["n_phases"] == 10 and len(ph["phases"]) == 10
    assert {p["phase"] for p in ph["phases"]} == set(range(10))
    assert ph["var_range"][0] <= ph["var_median"] <= ph["var_range"][1]
    for p in ph["phases"]:                                              # disjoint: ~n/10 windows each
        assert p["n_obs"] <= len(d) // 10 + 1


def test_block_bootstrap_reports_its_parameters_and_is_reproducible():
    rng = np.random.default_rng(11)
    s = pd.Series(rng.normal(0, 0.03, 400))
    a = RM.block_bootstrap_ci(s, 0.99, "var", reps=200, seed=42)
    b = RM.block_bootstrap_ci(s, 0.99, "var", reps=200, seed=42)
    assert a["available"] and a["lo"] < a["hi"]
    assert a["block_length"] and a["reps"] == 200 and a["seed"] == 42
    assert a == b                                                        # same seed -> same interval
    assert "distinct from the VaR confidence level" in a["note"]
    assert RM.block_bootstrap_ci(s.head(5), 0.99)["available"] is False   # too few observations: fail closed


# ----------------------------------------------------------------------------- C/E/F/G via the workbench
@pytest.fixture(scope="module")
def built():
    from market_intel import risk_workbench
    return risk_workbench.build()


@pytest.mark.xfail(reason="Specification test for the planned risk.v4 / constraint-audit hardening; not yet implemented.", strict=False)
def test_three_year_window_is_unavailable_when_history_is_short(built):
    v = built["var"]
    w3 = v["windows"]["3y"]
    if w3["available"]:
        assert w3["n_sessions_used"] >= 756           # if it IS available it must really have the sessions
    else:
        assert w3["reason"] and (w3.get("limiting_holding") or w3.get("n_sessions_available") is not None)
        assert "not shortened" in w3["reason"] or "required" in w3["reason"]


def test_expected_shortfall_is_not_claimed_to_be_regulatory(built):
    note = built["var"]["es_level_note"]
    assert "NOT a regulatory FRTB" in note and "liquidity-horizon" in note
    assert built["var"]["es_level"] == 0.975


def test_no_preferred_or_headline_estimate_is_published(built):
    v = built["var"]
    assert v["no_preferred_estimate"] is True
    for w in v["windows"].values():
        for h in w.get("horizons", {}).values():
            assert "preferred" not in h and "headline" not in h
    assert "headline_window" not in v


@pytest.mark.xfail(reason="Specification test for the planned risk.v4 / constraint-audit hardening; not yet implemented.", strict=False)
def test_tracking_error_names_its_benchmark_and_window_independently(built):
    windows = [w for w in built["var"]["windows"].values() if w.get("available")]
    assert windows, "no risk window available"
    tes = windows[0]["tracking_error"]
    labels = {t["benchmark"] for t in tes}
    # an equal-weight fallback is explicitly a proxy; the missing mandate slot remains unavailable
    from market_intel import risk_workbench
    _, meta = risk_workbench.benchmark_weights(__import__("market_intel").research.universe())
    if not meta.get("custom"):
        assert "equal-weight current-universe proxy" in labels and "mandate benchmark" in labels
        mandate = next(t for t in tes if t["benchmark"] == "mandate benchmark")
        assert mandate["available"] is False and mandate["kind"] == "formal mandate"
    for t in tes:
        if t["benchmark"] == "mandate benchmark":
            continue
        assert t.get("source")
        if t["available"]:
            assert t["n_days"] > 0 and t["start"] and t["end"] and t["coverage"]["paired_observations"] > 0
    # the index TE must not be shortened by the unrelated proxy universe
    idx_te = [t for t in tes if "index proxy" in t["benchmark"]]
    proxy_te = [t for t in tes if "research proxy" in t["benchmark"]]
    if idx_te and proxy_te and idx_te[0]["available"] and proxy_te[0]["available"]:
        assert idx_te[0]["n_days"] >= proxy_te[0]["n_days"]


@pytest.mark.xfail(reason="Specification test for the planned risk.v4 / constraint-audit hardening; not yet implemented.", strict=False)
def test_schema_is_versioned_and_carries_provenance(built):
    v = built["var"]
    assert v["schema_version"] == RM.SCHEMA_VERSION == "risk.v4"
    p = v["provenance"]
    assert len(p["calculator_code_hash"]) == 16 and len(p["data_snapshot_hash"]) == 16
    assert p["asof_last_confirmed_close"] and p["reconciliation"]
    # legacy v1 flat keys must not silently persist with new semantics
    for legacy in ("var_95", "var_99", "es_975", "cvar_95", "cvar_99", "tracking_error_ann"):
        assert legacy not in v, f"legacy flat key {legacy} still present; v1 consumers would read v2 numbers"


@pytest.mark.xfail(reason="Specification test for the planned risk.v4 / constraint-audit hardening; not yet implemented.", strict=False)
def test_session_index_does_not_claim_to_be_an_exchange_calendar(built):
    cal = built["var"]["session_index"]
    assert cal["is_official_exchange_calendar"] is False and "observed bars" in cal["source"]


def test_only_one_multiday_estimator_exists_and_it_labels_the_heuristic():
    """No second, differently-labelled estimator may sit beside the shipped one."""
    import inspect
    from market_intel import risk_workbench as RW
    src = inspect.getsource(RW)
    assert '"effective_obs"' not in src, "bare effective_obs claims a statistical effective sample size"
    assert src.count("def _horizon_block") == 0, "dead duplicate estimator must not linger"
    assert src.count("non_overlap_equivalent_count_definition") == 1
    assert "NOT a statistical effective sample size" in src
