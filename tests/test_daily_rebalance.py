"""Daily-clock counterexamples; synthetic fixtures do not validate investment returns."""
from datetime import datetime
import json

import pandas as pd
import pytest

from ai_semi_intel import daily, trading_policy as tp
from ai_semi_intel.config.settings import CONFIG
from ai_semi_intel.portfolio import optimizer
from ai_semi_intel.utils.io import DataUnavailable


def stamp(s):
    return datetime.fromisoformat(s)


def test_daily_default_does_not_silently_use_monthly_rebalance():
    assert CONFIG.portfolio.rebalance == "D"
    idx = pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"])
    px = pd.DataFrame({"A": [100., 110., 121.], "B": [100., 100., 100.]}, index=idx)
    R = px.pct_change().iloc[1:]
    w = pd.Series({"A": .5, "B": .5})
    d = optimizer.backtest(R, w, px, (), 0.)
    m = optimizer.backtest(R, w, px, (), 0., rebalance="M")
    # With daily resets, each day earns 5%. A monthly book drifts to 52.38% A.
    assert d["daily"]["Portfolio"].iloc[-1] == pytest.approx(.05)
    assert m["daily"]["Portfolio"].iloc[-1] == pytest.approx(.1 * 55 / 105)
    assert "INVALID AS TRACK RECORD" in d["meta"]["label"]


@pytest.mark.parametrize("when", ["2026-09-07T07:15:00-04:00", "2026-09-12T07:15:00-04:00",
                                  "2026-11-26T07:15:00-05:00"])
def test_exchange_holidays_do_not_run(when):
    assert tp.preparation_window(when)["due"] is False


@pytest.mark.parametrize("when,due", [("2026-09-14T07:14:59-04:00", False),
                                      ("2026-09-14T07:15:00-04:00", True),
                                      ("2026-09-14T09:14:59-04:00", True),
                                      ("2026-09-14T09:15:00-04:00", False),
                                      ("2026-09-14T13:00:00-04:00", False)])
def test_preparation_has_a_bounded_et_window(when, due):
    assert tp.preparation_window(when)["due"] is due


def test_next_open_skips_weekend_and_labor_day():
    p = tp.policy("2026-09-04T16:25:00-04:00", "2026-09-04")
    assert p["ready"] and p["execution_session"] == "2026-09-08"
    assert p["not_before"] == "2026-09-08T13:30:00+00:00"


def test_dst_changes_utc_open_but_not_local_execution_time():
    a = tp.policy("2026-10-30T07:15:00-04:00", "2026-10-29")
    b = tp.policy("2026-11-02T07:15:00-05:00", "2026-10-30")
    assert a["not_before"].endswith("13:30:00+00:00")
    assert b["not_before"].endswith("14:30:00+00:00")


def test_partial_snapshot_never_becomes_final_when_clock_advances():
    px = pd.DataFrame({"A": [100., 105.]}, index=pd.to_datetime(["2026-09-10", "2026-09-11"]))
    a, ma = tp.confirmed_prices(px, "2026-09-11T15:39:00-04:00", "2026-09-11T15:40:00-04:00")
    b, mb = tp.confirmed_prices(px, "2026-09-11T15:39:00-04:00", "2026-09-14T07:15:00-04:00")
    pd.testing.assert_frame_equal(a, b)
    assert mb["confirmed_through"] == "2026-09-10"
    assert mb["required_through"] == "2026-09-11" and not mb["fresh_complete_session"]
    assert len(px) == 2


def test_early_close_uses_exchange_session_and_buffer():
    px = pd.DataFrame({"A": [100., 105.]}, index=pd.to_datetime(["2026-11-25", "2026-11-27"]))
    before, _ = tp.confirmed_prices(px, "2026-11-27T13:19:00-05:00", "2026-11-27T14:00:00-05:00")
    after, m = tp.confirmed_prices(px, "2026-11-27T13:20:00-05:00", "2026-11-27T14:00:00-05:00")
    assert len(before) == 1 and len(after) == 2 and m["fresh_complete_session"]


def test_missing_previous_session_cannot_form_ready_target():
    assert not tp.policy("2026-09-14T07:15:00-04:00", "2026-09-10")["ready"]


def run_fixture():
    generated = "2026-09-14T07:20:00-04:00"
    return {"run_id": "new-daily", "generated_at": generated, "asof_session": "2026-09-14", "actionable": True,
            "rebalance_policy": tp.policy(generated, "2026-09-11")}


@pytest.mark.parametrize("when", ["2026-09-14T09:29:59-04:00", "2026-09-14T09:35:00-04:00",
                                  "2026-09-15T09:31:00-04:00"])
def test_early_late_and_next_day_execution_are_rejected(when):
    with pytest.raises(DataUnavailable, match="outside"):
        tp.execution_gate(run_fixture(), when)


def test_opening_execution_and_clock_tampering():
    r = run_fixture()
    tp.execution_gate(r, "2026-09-14T09:30:01-04:00")
    r["rebalance_policy"]["expires_at"] = "2026-09-14T20:00:00+00:00"
    with pytest.raises(DataUnavailable, match="does not match"):
        tp.execution_gate(r, "2026-09-14T10:00:00-04:00")


def test_missing_policy_fails_closed():
    with pytest.raises(DataUnavailable, match="no current"):
        tp.execution_gate({}, "2026-09-14T09:31:00-04:00")


def test_scheduler_one_attempt_per_session_and_no_fabricated_fills(tmp_path):
    calls = []
    def execute(**kwargs):
        from ai_semi_intel.portfolio import target_archive
        from ai_semi_intel.portfolio.constructions import REGISTRY
        calls.append(kwargs)
        run = dict(run_fixture(), daily_attempt_id=kwargs["daily_attempt_id"])
        snap = dict(run, construction="risk_parity", weights={"A": .5}, cash=.5, vol_target=.25)
        targets = target_archive.build({m: {"weights": {"A": 1.}, "vol": .5} for m in REGISTRY}, snap, list(REGISTRY))
        return {"run": run, "snapshot": snap, "construction_targets": targets}
    when = stamp("2026-09-14T07:15:00-04:00")
    first = daily.run_once(when, state_dir=tmp_path, pipeline_runner=execute)
    second = daily.run_once(when, state_dir=tmp_path, pipeline_runner=execute)
    assert len(calls) == 1 and first["status"] == "TARGET_READY"
    assert first["execution_status"] == "NO_FILLS_RECORDED"
    assert second["status"] == "ALREADY_ATTEMPTED"
    assert "persist" in calls[0]["stages"]


def test_failed_attempt_does_not_repeat_paid_research(tmp_path):
    def fail(**kwargs):
        raise RuntimeError("simulated source failure")
    when = stamp("2026-09-14T07:15:00-04:00")
    with pytest.raises(RuntimeError):
        daily.run_once(when, state_dir=tmp_path, pipeline_runner=fail)
    result = daily.run_once(when, state_dir=tmp_path, pipeline_runner=fail)
    assert result["status"] == "ALREADY_ATTEMPTED"
    saved = json.loads((tmp_path / "2026-09-14.json").read_text())
    assert saved["status"] == "FAILED" and saved["finished_at"]


def test_check_and_late_wake_do_not_run_or_mutate(tmp_path):
    def fail(**kwargs):
        raise AssertionError("must not run")
    for when, check in [("2026-09-14T07:15:00-04:00", True), ("2026-09-14T10:00:00-04:00", False)]:
        daily.run_once(stamp(when), state_dir=tmp_path, pipeline_runner=fail, check=check)
    assert list(tmp_path.iterdir()) == []


def test_data_stage_filters_before_any_features_and_reseals_manifest(tmp_path, monkeypatch):
    import os
    from ai_semi_intel import pipeline
    from datetime import timezone
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = stamp("2026-09-11T17:00:00-04:00")
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(pipeline, "datetime", FixedClock)
    px = pd.DataFrame({"A": [100., 105.]}, index=pd.to_datetime(["2026-09-10", "2026-09-11"]))
    path = tmp_path / "prices_universe.parquet"
    px.to_parquet(path)
    acquired = stamp("2026-09-11T15:00:00-04:00").timestamp()
    os.utime(path, (acquired, acquired))
    monkeypatch.setattr(pipeline.yahoo, "PRICE_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "tickers", lambda: ["A"])
    monkeypatch.setattr(pipeline.yahoo, "fetch_prices", lambda *a, **k: px)
    monkeypatch.setattr(pipeline.yahoo, "fetch_all_fundamentals", lambda *a, **k: {})
    monkeypatch.setattr(pipeline.edgar, "fetch_all_edgar", lambda *a, **k: {})
    monkeypatch.setattr(pipeline.fred, "macro_panel", lambda: {})
    monkeypatch.setattr(pipeline.fred, "risk_free_rate", lambda *a: .04)
    monkeypatch.setattr(pipeline.fred, "macro_snapshot", lambda *a: {})
    monkeypatch.setattr(pipeline.pitchbook, "pitchbook_context", lambda *a: {"status": "fixture"})
    monkeypatch.setattr(pipeline.runs, "data_manifest", lambda *a, **k: {
        "entries": {"prices": {"status": "fresh"}, "fred:DGS10": {"status": "fresh"}},
        "status": "fresh", "stale": [], "price_gaps": {}, "hash": "old"})
    result = pipeline.stage_data({})
    assert str(result["px"].index[-1].date()) == "2026-09-10"
    assert result["manifest"]["entries"]["prices"]["status"] == "stale"
    assert result["manifest"]["hash"] != "old"
    assert result["price_finality"]["required_through"] == "2026-09-11"


def test_manual_and_automatic_pipeline_share_output_lock(tmp_path, monkeypatch):
    import fcntl
    from ai_semi_intel import pipeline
    monkeypatch.setattr(pipeline, "OUTPUTS", tmp_path)
    with (tmp_path / ".pipeline.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(DataUnavailable, match="another pipeline"):
            pipeline.run(stages=())


def test_run_manifest_carries_daily_clock_and_blocks_stale_target():
    from ai_semi_intel import runs
    p = tp.policy("2026-09-14T07:20:00-04:00", "2026-09-10")
    snap = {"run_id": "fixture", "date": "2026-09-14", "generated_at": "2026-09-14T07:20:00-04:00",
            "top": ["A"], "rank": {"A": 1}, "composite": {"A": 1.}, "rebalance_policy": p}
    opt = {"recommended": {"base": "risk_parity", "weights": {"A": .5}, "cash": .5},
           "risk_parity": {"weights": {"A": 1.}}, "max_sharpe": {"weights": {"A": 1.}}}
    run = runs.build_run(snap, opt, {"status": "fresh", "entries": {}}, {}, {}, config_h="c", code_h="c")
    assert run["rebalance_policy"] == p and not run["actionable"]
    assert any("daily execution policy" in r for r in run["not_actionable_reasons"])


def test_afternoon_check_reports_next_preparation_after_weekend():
    p = tp.preparation_window("2026-09-11T16:00:00-04:00")
    assert p["next_preparation_at"] == "2026-09-14T07:15:00-04:00"


def test_failed_state_keeps_nonzero_status_without_repeating_research(monkeypatch):
    monkeypatch.setattr(daily, "run_once", lambda **kw: {"status": "ALREADY_ATTEMPTED", "prior": {"status": "FAILED"}})
    assert daily.main([]) == 1


def test_pipeline_status_exposes_active_policy_without_promoting_old_run(tmp_path, monkeypatch):
    from market_intel import research
    monkeypatch.setattr(research.config, "OUTPUTS", tmp_path)
    monkeypatch.setattr(research, "summary", lambda: {"asof": "2026-09-03", "generated_at": "old"})
    monkeypatch.setattr(research.PipelineRunner, "LOG", tmp_path / "no.log")
    s = research.PipelineRunner().status()
    assert s["rebalance_frequency"] == "D" and s["asof"] == "2026-09-03"
    assert s["daily_state"] is None and "TARGET_PREPARATION_ONLY" in s["execution_status"]


def test_saved_manifest_finality_keeps_existing_gaps_and_binds_hash():
    from ai_semi_intel import runs
    from market_intel.platform.stock_select_data import _legacy_manifest_hash
    manifest = {"entries": {"prices": {"status": "stale", "stale_reason": "missing A"}},
                "status": "stale", "stale": ["prices"], "price_gaps": {"A": ["2026-09-11"]}, "hash": "old"}
    result = runs.bind_price_finality(manifest, {"fresh_complete_session": True, "required_through": "2026-09-11"})
    assert result["entries"]["prices"]["stale_reason"] == "missing A"
    assert result["price_gaps"] == {"A": ["2026-09-11"]}
    h = result["hash"]
    assert h == _legacy_manifest_hash(result)
    assert result["entries"]["prices"]["session_finality"] == result["price_finality"]
    result = runs.bind_price_finality(result, {"fresh_complete_session": False, "required_through": "2026-09-14"})
    assert result["hash"] != h and result["status"] == "stale"
    assert result["hash"] == _legacy_manifest_hash(result)


def test_fixed_horizon_diagnostics_cannot_claim_daily_strategy_returns(tmp_path, monkeypatch):
    from ai_semi_intel.research import live_ic
    monkeypatch.setattr(live_ic, "HISTORY", tmp_path)
    out = live_ic.compute(pd.DataFrame())
    assert out["validation_status"] == "NOT VALIDATED"
    assert out["daily_rebalanced_performance"]["status"] == "unavailable"
    assert "fixed-horizon" in out["performance_basis"]


def test_legacy_run_id_shared_across_dates_does_not_duplicate_daily_scores():
    from ai_semi_intel import runs
    df = pd.DataFrame({"date": ["2026-09-02", "2026-09-03", "2026-09-03"],
                       "run_id": ["legacy", "legacy", "fresh"], "ticker": ["A", "A", "A"]})
    chosen = runs.latest_per_date(df, [{"run_id": "fresh", "status": "approved"}])
    assert list(chosen["run_id"]) == ["legacy", "fresh"]
    assert not chosen.duplicated(["date", "ticker"]).any()
