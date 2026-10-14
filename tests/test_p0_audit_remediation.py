"""Counterexample tests for the independent audit findings (P0 risk items 1-8).

Each test states the defect it forbids and fails on the pre-fix behaviour.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from market_intel import risk_metrics as RM
from market_intel import risk_workbench as RW

ET = ZoneInfo("America/New_York")


def _panel(n=400, seed=7, names=("AAA", "BBB", "CCC"), start="2024-01-02"):
    """A synthetic confirmed panel plus the SOXX reference listing."""
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    data = {t: 100 * np.cumprod(1 + rng.normal(0.0004, 0.02, n)) for t in names}
    data["SOXX"] = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    return pd.DataFrame(data, index=idx)


# --------------------------------------------------------------- 1. finality from acquisition, not the clock
def test_finality_is_a_property_of_the_snapshot_not_of_the_reader_clock():
    """The same frozen snapshot must not gain a session because the afternoon wore on."""
    idx = pd.DatetimeIndex(pd.bdate_range("2026-08-24", periods=9))      # ends 2026-09-03
    acquired = datetime(2026, 9, 3, 13, 27, 7, tzinfo=ET)                # run.json: fetched before the close
    pre, meta_pre = RM.confirmed_sessions(idx, acquired)
    # re-reading the identical snapshot hours later, after 16:20 ET, must be byte-identical
    post, meta_post = RM.confirmed_sessions(idx, acquired)
    assert list(pre) == list(post)
    assert meta_pre["confirmed_through"] == meta_post["confirmed_through"] == "2026-09-02"
    assert str(pre[-1].date()) == "2026-09-02", "a bar that was partial when fetched is never final later"
    assert [d["date"] for d in meta_pre["dropped"]] == ["2026-09-03"]
    assert meta_pre["basis"] == "acquisition" and meta_pre["wall_clock_independent"] is True
    # and a snapshot genuinely taken after the close does keep that session
    after, meta_after = RM.confirmed_sessions(idx, datetime(2026, 9, 3, 17, 30, tzinfo=ET))
    assert str(after[-1].date()) == "2026-09-03" and meta_after["acquired_after_close"] is True


def test_confirmed_sessions_fails_closed_without_acquisition_provenance():
    idx = pd.DatetimeIndex(pd.bdate_range("2026-08-24", periods=9))
    out, meta = RM.confirmed_sessions(idx, None)
    assert len(out) == 0 and meta["available"] is False
    assert "Wall-clock time is NOT an acceptable substitute" in meta["reason"]


def test_live_run_snapshot_excludes_its_own_partial_bar():
    """Regression against the reproduced defect: 390 sessions / asof 9/3 must not appear."""
    from market_intel import research
    acq, meta = RW.price_acquisition()
    if not meta.get("available"):
        pytest.skip("run declares no price acquisition timestamp")
    px = research.price_panel()
    grid, cal = RM.session_index(px, RW.TE_INDEX, acq)
    fin = cal["finality"]
    assert fin["basis"] == "acquisition"
    assert str(grid[-1].date()) <= fin["confirmed_through"]
    if meta.get("declared_price_date") == pd.Timestamp(acq).tz_convert("America/New_York").date().isoformat() and not fin["acquired_after_close"]:
        assert str(grid[-1].date()) != meta["declared_price_date"], \
            "the panel's last bar was fetched intraday and must not be a confirmed close"


# --------------------------------------------------------------- 2. the session grid is never compressed
def test_four_session_gap_is_two_failed_windows_not_one_fabricated_move():
    """The auditor's reproducer: B has no bar on the second session.

    Filtering incomplete rows before building windows gives 2 'one-day' observations and 0 failures, and books
    day0 -> day2 as a single session. On the intact grid it is 1 valid observation and 2 failed windows.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]))
    px = pd.DataFrame({"A": [100.0, 101.0, 102.0, 103.0],
                       "B": [50.0, np.nan, 52.0, 53.0]}, index=idx)
    w = {"A": 0.5, "B": 0.5}
    full = RM.frozen_pnl(px, w, idx, horizon=1, step=1)
    assert full.n == 1, "only the last pair has a bar for every holding"
    assert len(full.failures) == 2
    assert {f["start"] for f in full.failures} == {"2026-01-05", "2026-01-06"}
    # the pre-fix path, shown failing its own claim
    usable = idx[px.notna().all(axis=1).values]
    compressed = RM.frozen_pnl(px, w, usable, horizon=1, step=1)
    assert compressed.n == 2 and len(compressed.failures) == 0
    assert compressed.starts[0].date().isoformat() == "2026-01-05"
    assert compressed.ends[0].date().isoformat() == "2026-01-07", \
        "the discarded path calls a two-session move a one-day return"


def test_grid_returns_never_bridges_a_gap():
    idx = pd.DatetimeIndex(pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]))
    px = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1],
                       "B": [50.0, np.nan, 52.0, 53.0]}, index=idx)
    r, sub, cov = RM.grid_returns(px, ["A", "B"], idx)
    assert cov["n_dropped_returns"] == 2 and cov["n_valid_returns"] == 1
    assert list(r.index.strftime("%Y-%m-%d")) == ["2026-01-08"]
    # the surviving A return is a genuine one-session move, not a compounded two-session one
    assert r["A"].iloc[0] == pytest.approx(133.1 / 121.0 - 1, rel=1e-9)
    bad = px.dropna().pct_change().dropna()
    assert bad["A"].iloc[0] == pytest.approx(121.0 / 100.0 - 1, rel=1e-9), "pre-fix path compounds two sessions"


def test_var_block_reports_failed_windows_instead_of_shrinking_the_grid():
    px = _panel(n=400)
    px.iloc[300, px.columns.get_loc("BBB")] = np.nan          # one real gap inside the 1y window
    acq = datetime(2026, 1, 5, 17, 0, tzinfo=ET)
    blk, _ = var_block_for(px, {"AAA": 0.5, "BBB": 0.5}, acq)
    w1 = blk["windows"]["1y"]
    assert w1["available"] is True
    o1 = w1["horizons"]["1"]["overlapping"]
    assert o1["n_failed_windows"] >= 2, "a missing bar must fail its two adjacent one-day windows"
    assert o1["n_obs"] + o1["n_failed_windows"] == o1["n_possible_windows"]
    assert w1["n_sessions_used"] == 252, "the span is cut from the intact grid, not from surviving rows"
    assert w1["grid_coverage"]["n_dropped_returns"] >= 1
    assert w1["horizons"]["10"]["overlapping"]["n_failed_windows"] >= 10
    assert w1["horizons"]["10"]["estimate_uncertainty"]["var_99"]["available"] is False


def var_block_for(px, hold, acq, **kw):
    return RW.var_block(px, hold, {"SOXX": 1.0}, windows={"1y": 252}, bench_meta={"custom": False},
                        acquired_at=acq, bootstrap_reps=60,
                        acq_meta={"acquired_at": acq.isoformat(), "source": "test"}, **kw)


# --------------------------------------------------------------- 3. cache key covers every real input
def test_cache_key_moves_when_any_real_input_moves():
    px = _panel(n=300)
    acq = datetime(2026, 1, 5, 17, 0, tzinfo=ET)
    run = {"run_id": "R1", "portfolio": {"positions": [], "cash": 0.5}}
    cons = {"m": {"weights": {"AAA": 0.6, "BBB": 0.4}}}
    bench = {"SOXX": 1.0}
    base = RW._cache_key(run, px, cons, bench, 0.03, 0.25, acq, 2000, {"1y": 252})
    # a same-date price CORRECTION must invalidate: date, shape and column count are all unchanged
    px2 = px.copy()
    px2.iloc[-1, 0] = float(px2.iloc[-1, 0]) * 1.01
    assert RW._cache_key(run, px2, cons, bench, 0.03, 0.25, acq, 2000, {"1y": 252}) != base
    variants = {
        "weights": (run, px, {"m": {"weights": {"AAA": 0.61, "BBB": 0.39}}}, bench, 0.03, 0.25, acq, 2000),
        "benchmark": (run, px, cons, {"SOXX": 0.5, "AAA": 0.5}, 0.03, 0.25, acq, 2000),
        "rf": (run, px, cons, bench, 0.04, 0.25, acq, 2000),
        "vol_target": (run, px, cons, bench, 0.03, 0.20, acq, 2000),
        "acquired_at": (run, px, cons, bench, 0.03, 0.25, acq + timedelta(hours=1), 2000),
        "reps": (run, px, cons, bench, 0.03, 0.25, acq, 500),
    }
    for label, args in variants.items():
        assert RW._cache_key(*args, {"1y": 252}) != base, f"cache key ignores {label}"


def test_cache_key_is_stable_when_nothing_changes():
    px = _panel(n=300)
    acq = datetime(2026, 1, 5, 17, 0, tzinfo=ET)
    run = {"run_id": "R1", "portfolio": {"positions": [], "cash": 0.5}}
    cons = {"m": {"weights": {"AAA": 0.6, "BBB": 0.4}}}
    a = RW._cache_key(run, px, cons, {"SOXX": 1.0}, 0.03, 0.25, acq, 2000, {"1y": 252})
    b = RW._cache_key(run, px.copy(), dict(cons), {"SOXX": 1.0}, 0.03, 0.25, acq, 2000, {"1y": 252})
    assert a == b


# --------------------------------------------------------------- 4. landed run vs recalculated scenario
def test_actual_run_layer_is_the_landed_book_not_a_recomputation():
    lw, meta = RW.landed_book()
    if not meta.get("available"):
        pytest.skip("no landed run manifest")
    man = RW.raw_run()
    assert meta["scale"] == man["portfolio"]["scale"], "the landed scale is read, never re-derived"
    assert lw == {p["ticker"]: p["target_executable_weight"] for p in man["portfolio"]["positions"]
                  if p["target_executable_weight"] > 0}


def test_only_the_run_column_may_claim_an_actual_run_layer():
    from market_intel import research
    px = research.price_panel()
    acq, _ = RW.price_acquisition()
    cons = RW.enumerate_constructions()
    if not cons:
        pytest.skip("no constructions in this run")
    man = RW.raw_run()
    for name, spec in cons.items():
        lay = RW._layers(px, name, spec, 0.25, acq, man)
        assert set(lay) == set(RW.LAYERS)
        if spec.get("is_canonical_executable"):
            assert lay["actual_run"]["available"] is True
            assert lay["actual_run"]["is_actual_run"] is True
            assert lay["actual_run"]["scale"] == man["portfolio"]["scale"]
        else:
            assert lay["actual_run"]["available"] is False
            assert "is not it" in lay["actual_run"]["reason"]
        sc = lay["vol_target_scenario"]
        if sc.get("available"):
            assert sc["is_hypothetical"] is True and sc["is_actual_run"] is False
            assert "NOT the book any run executed" in sc["basis"]


def test_no_layer_is_named_executable():
    """'executable' names a book that was executed; a recalculation may not borrow the word."""
    assert "executable" not in RW.LAYERS
    assert "vol_target_scenario" in RW.LAYERS and "actual_run" in RW.LAYERS


# --------------------------------------------------------------- 5. tracking error per requested window
def test_tracking_error_is_computed_per_window_not_once_over_all_history():
    px = _panel(n=900)
    acq = datetime(2027, 1, 5, 17, 0, tzinfo=ET)
    blk, _ = RW.var_block(px, {"AAA": 0.5, "BBB": 0.5}, {"AAA": 0.5, "CCC": 0.5},
                          windows={"1y": 252, "3y": 756}, bench_meta={"custom": False},
                          acquired_at=acq, bootstrap_reps=60,
                          acq_meta={"acquired_at": acq.isoformat(), "source": "test"})
    te1 = blk["windows"]["1y"]["tracking_error"]
    te3 = blk["windows"]["3y"]["tracking_error"]
    got1 = {t["benchmark"]: t for t in te1}
    got3 = {t["benchmark"]: t for t in te3}
    key = "equal-weight current-universe proxy"
    assert got1[key]["n_days"] < got3[key]["n_days"], "each window must use its own span"
    assert got1[key]["tracking_error_ann"] != got3[key]["tracking_error_ann"]
    assert got1[key]["n_days"] <= 252 and got3[key]["n_days"] <= 756


def test_absent_mandate_benchmark_is_reported_not_available():
    px = _panel(n=400)
    acq = datetime(2026, 1, 5, 17, 0, tzinfo=ET)
    blk, _ = var_block_for(px, {"AAA": 0.5, "BBB": 0.5}, acq)
    te = blk["windows"]["1y"]["tracking_error"]
    mandate = [t for t in te if t.get("kind") == "formal mandate"]
    assert mandate and mandate[0]["status"] == "NOT AVAILABLE"
    assert mandate[0]["available"] is False
    for t in te:
        if t.get("available"):
            assert t["kind"] == "research proxy", "no proxy may be presented as the mandate benchmark"


# --------------------------------------------------------------- 6. bootstrap dependence at the 10d horizon
def test_pnl_block_bootstrap_refuses_a_block_shorter_than_the_horizon():
    rng = np.random.default_rng(3)
    daily = pd.Series(rng.normal(0, 0.02, 300))
    overlapping = (1 + daily).rolling(10).apply(np.prod, raw=True).dropna() - 1
    auto = RM.block_bootstrap_ci(overlapping, 0.99, "var", reps=80, min_block=10)
    assert auto["block_length"] >= 10, "dependence spans the whole 10-session horizon"
    assert auto["min_block_required"] == 10


def test_primary_interval_resamples_asset_shocks_and_discloses_sensitivity():
    px = _panel(n=400)
    acq = datetime(2026, 1, 5, 17, 0, tzinfo=ET)
    blk, _ = var_block_for(px, {"AAA": 0.5, "BBB": 0.5}, acq)
    u = blk["windows"]["1y"]["horizons"]["10"]["estimate_uncertainty"]
    assert u["primary_method"] == "asset_shock_bootstrap"
    assert u["var_99"]["available"] is True
    assert "rebuilt on each" in u["var_99"]["method"] or "rebuild" in u["var_99"]["method"]
    sens = u["pnl_block_bootstrap_sensitivity"]
    assert set(sens) == {"block_10", "block_20"}
    assert all(v["block_length"] >= 10 for v in sens.values() if v.get("available"))
    assert u["autocorrelation_lag1_overlapping"] is not None


# --------------------------------------------------------------- 7. hashes
def test_calculator_hash_covers_the_orchestrator_too():
    man = RM.calculator_manifest()
    assert set(man["modules"]) == {"risk_metrics.py", "risk_workbench.py"}
    assert all(v != "MISSING" for v in man["modules"].values())
    assert man["combined"] == RM.calculator_hash()


def test_shared_snapshot_hash_is_not_built_from_construction_specific_dates():
    px = _panel(n=300)
    grid = px.index
    shared = RM.data_snapshot_hash(px, grid)
    # a construction that can only price a subset must not change the SHARED snapshot identity
    assert RM.data_snapshot_hash(px, grid, columns=None) == shared
    sub_sample = RM.calculation_sample_hash(px, ["AAA", "BBB"], grid[-100:])
    other_sample = RM.calculation_sample_hash(px, ["AAA", "CCC"], grid[-100:])
    assert sub_sample != other_sample, "calculation samples differ per construction"
    assert sub_sample != shared and other_sample != shared


def test_every_layer_of_every_construction_shares_one_data_snapshot_hash():
    from market_intel import research
    out = RW.build_constructions(bootstrap_reps=60)
    if not out.get("available"):
        pytest.skip(out.get("reason"))
    hashes = set()
    for lay in RW.LAYERS:
        for r in out[f"comparison_{lay}"]:
            if r.get("data_snapshot_hash"):
                hashes.add(r["data_snapshot_hash"])
    assert len(hashes) == 1, f"the shared snapshot must be one value, got {hashes}"
