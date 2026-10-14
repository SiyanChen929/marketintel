"""Numerical regressions for the risk.v4 fail-closed sample contract."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from market_intel import risk_metrics as RM
from market_intel import risk_workbench as RW


ET = ZoneInfo("America/New_York")


def _prices(n: int = 800) -> pd.DataFrame:
    d = pd.bdate_range("2023-01-02", periods=n)
    x = np.arange(n, dtype=float)
    # Deterministic, non-collinear paths make the hand-computed TE assertions stable.
    return pd.DataFrame({
        "PORT": 100.0 * np.exp(0.0005 * x + 0.006 * np.sin(x / 7.0)),
        "MAND": 100.0 * np.exp(0.0003 * x + 0.004 * np.cos(x / 9.0)),
        "SOXX": 100.0 * np.exp(0.0004 * x + 0.005 * np.sin(x / 11.0)),
    }, index=d)


def test_ten_session_frozen_holding_uses_asset_level_endpoint_moves():
    d = pd.bdate_range("2026-01-02", periods=11)
    # The two assets diverge and reconverge enough that free daily rebalancing produces a different result.
    a = np.array([100, 120, 90, 130, 85, 140, 80, 150, 75, 160, 200.0])
    b = np.array([100, 80, 110, 70, 115, 60, 120, 50, 125, 40, 50.0])
    px = pd.DataFrame({"A": a, "B": b}, index=d)
    w = {"A": 0.6, "B": 0.4}

    frozen = RM.frozen_pnl(px, w, d, horizon=10, step=1)
    expected = 0.6 * (200.0 / 100.0 - 1.0) + 0.4 * (50.0 / 100.0 - 1.0)
    assert frozen.n == 1
    assert frozen.pnl.iloc[0] == pytest.approx(expected, abs=1e-12)
    assert RM.daily_rebalanced_pnl(px, w, d, horizon=10, step=1).iloc[0] != pytest.approx(expected)


def test_missing_intermediate_bar_invalidates_full_ten_session_path():
    d = pd.bdate_range("2026-01-02", periods=11)
    px = pd.DataFrame({"A": np.linspace(100, 110, 11), "B": np.linspace(50, 60, 11)}, index=d)
    px.loc[d[5], "B"] = np.nan
    ws = RM.frozen_pnl(px, {"A": 0.5, "B": 0.5}, d, horizon=10, step=1)
    assert ws.n_possible == 1 and ws.n == 0 and len(ws.failures) == 1
    assert str(d[5].date()) in ws.failures[0]["invalid_dates"]
    assert "full 10-session path" in ws.failures[0]["reason"]


def test_three_year_label_requires_756_uninterrupted_common_sessions():
    px = _prices(800)
    px.loc[px.index[-300], "PORT"] = np.nan
    acquired = datetime.combine(px.index[-1].date(), datetime.min.time(), tzinfo=ET).replace(hour=17)
    block, _ = RW.var_block(
        px, {"PORT": 1.0}, {"SOXX": 1.0}, windows={"3y": 756},
        bench_meta={"custom": False, "source": "test"}, acquired_at=acquired,
        bootstrap_reps=0, acq_meta={"source": "test", "acquired_at": acquired.isoformat()},
    )
    w3 = block["windows"]["3y"]
    assert w3["available"] is False
    assert w3["requested_sessions"] == 756
    assert w3["n_sessions_available"] < 756
    assert "not shortened" in w3["reason"] and "PORT" in w3["reason"]


def test_intraday_or_timezone_unknown_acquisition_cannot_confirm_the_last_bar():
    d = pd.DatetimeIndex(["2026-09-01", "2026-09-02", "2026-09-03"])
    intraday = datetime(2026, 9, 3, 13, 27, tzinfo=ET)
    confirmed, meta = RM.confirmed_sessions(d, intraday, source="fixture")
    assert list(confirmed) == list(d[:2])
    assert meta["confirmed_through"] == "2026-09-02"

    unknown, unknown_meta = RM.confirmed_sessions(d, datetime(2026, 9, 3, 17, 0), source="fixture")
    assert unknown.empty and unknown_meta["available"] is False
    assert "no timezone" in unknown_meta["reason"]


def test_each_nonoverlap_phase_is_an_alternative_disjoint_partition_with_hand_pnl():
    d = pd.bdate_range("2026-01-02", periods=36)
    px = pd.DataFrame({"A": 100.0 * (1.01 ** np.arange(36))}, index=d)
    ph = RM.phases(px, {"A": 1.0}, d, horizon=10, level=0.99)
    expected = 1.01 ** 10 - 1.0
    assert len(ph["phases"]) == 10
    for row in ph["phases"]:
        direct = RM.frozen_pnl(px, {"A": 1.0}, d, 10, offset=row["phase"], step=10)
        assert direct.n == row["n_obs"]
        assert np.allclose(direct.pnl.to_numpy(), expected, atol=1e-12)
    # The phases overlap one another, so their counts must never be marketed as one pooled disjoint sample.
    assert "n_windows_total_disjoint" not in RW.var_block(
        pd.DataFrame({"A": px["A"], "SOXX": px["A"]}, index=d), {"A": 1.0}, {"SOXX": 1.0},
        windows={"short": 30}, bench_meta={"custom": False, "source": "test"},
        acquired_at=datetime(2026, 3, 1, 17, 0, tzinfo=ET), bootstrap_reps=0,
    )[0]["windows"]["short"]["horizons"]["10"]["non_overlapping_phases"]


def test_te_is_hand_computable_and_each_benchmark_fails_independently():
    px = _prices(300)
    acquired = datetime.combine(px.index[-1].date(), datetime.min.time(), tzinfo=ET).replace(hour=17)
    block, _ = RW.var_block(
        px, {"PORT": 1.0}, {"MAND": 1.0}, windows={"1y": 252},
        bench_meta={"custom": True, "source": "mandate.csv"}, acquired_at=acquired,
        bootstrap_reps=0, acq_meta={"source": "fixture", "acquired_at": acquired.isoformat()},
    )
    rows = {r["benchmark"]: r for r in block["windows"]["1y"]["tracking_error"]}
    win = px.index[-252:]
    rp = px.loc[win, "PORT"].pct_change(fill_method=None).dropna()
    rm = px.loc[win, "MAND"].pct_change(fill_method=None).dropna()
    rs = px.loc[win, "SOXX"].pct_change(fill_method=None).dropna()
    assert rows["mandate benchmark"]["tracking_error_ann"] == pytest.approx(
        float((rp - rm).std(ddof=1) * np.sqrt(252)), abs=1e-6
    )
    assert rows["SOXX index proxy"]["tracking_error_ann"] == pytest.approx(
        float((rp - rs).std(ddof=1) * np.sqrt(252)), abs=1e-6
    )
    assert all(r["sample_independent"] for r in rows.values())

    # Removing one mandate constituent cannot change or suppress the independent SOXX result.
    broken, _ = RW.var_block(
        px, {"PORT": 1.0}, {"MAND": 0.5, "ABSENT": 0.5}, windows={"1y": 252},
        bench_meta={"custom": True, "source": "mandate.csv"}, acquired_at=acquired,
        bootstrap_reps=0, acq_meta={"source": "fixture", "acquired_at": acquired.isoformat()},
    )
    broken_rows = {r["benchmark"]: r for r in broken["windows"]["1y"]["tracking_error"]}
    assert broken_rows["mandate benchmark"]["available"] is False
    assert broken_rows["mandate benchmark"]["missing_benchmark_members"] == ["ABSENT"]
    assert "no member was dropped" in broken_rows["mandate benchmark"]["reason"]
    assert broken_rows["SOXX index proxy"]["available"] is True
    assert broken_rows["SOXX index proxy"]["tracking_error_ann"] == rows["SOXX index proxy"]["tracking_error_ann"]


def test_asset_bootstrap_refuses_a_matrix_made_by_deleting_gap_dates():
    d = pd.bdate_range("2026-01-02", periods=80)
    px = pd.DataFrame({"A": 100.0 * 1.001 ** np.arange(80), "B": 100.0 * 0.999 ** np.arange(80)}, index=d)
    px.loc[d[30], "B"] = np.nan
    returns, _, coverage = RM.grid_returns(px, ["A", "B"], d)
    assert coverage["n_dropped_returns"] == 2
    ci = RM.asset_shock_bootstrap_ci(returns, {"A": 0.5, "B": 0.5}, 10, 0.99, reps=20)
    assert ci["available"] is False
    assert "separated observations adjacent" in ci["reason"]
