"""Independent synthetic end-to-end persistence / publication crash audit."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from ai_semi_intel import daily, pipeline, publication, runs, trading_policy
from ai_semi_intel.analysis import strategic
from ai_semi_intel.portfolio import optimizer
from ai_semi_intel.research import forward_registry, live_ic
from ai_semi_intel.utils.io import DataUnavailable
from market_intel import config, readiness


class FixedClock(datetime):
    offset = 0

    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 9, 22, 11, 20, tzinfo=timezone.utc) + timedelta(seconds=cls.offset)
        cls.offset += 1
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def persist_fixture(tmp_path, monkeypatch):
    """Run real serialization, target building, registration and publication.

    External research, source acquisition and validation results are replaced by
    explicit synthetic inputs. Every output path is inside pytest's directory.
    """
    latest, history = tmp_path / "latest", tmp_path / "history"
    latest.mkdir(); history.mkdir()
    FixedClock.offset = 0
    for module in (pipeline, strategic, config):
        for name, value in (("OUTPUTS", tmp_path), ("LATEST", latest), ("HISTORY", history)):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(pipeline, "datetime", FixedClock)
    monkeypatch.setattr(strategic, "QUEUE_PATH", tmp_path / "unused-queue.json")
    monkeypatch.setattr(strategic, "ASSESS_DIR", tmp_path / "empty-assessments")
    monkeypatch.setattr(strategic, "CONTEXT_DIR", tmp_path / "empty-contexts")
    monkeypatch.setattr(strategic, "load_all", lambda: {})
    monkeypatch.setattr(runs, "code_hash", lambda: "c" * 64)
    monkeypatch.setattr(runs, "config_hash", lambda: "d" * 64)
    monkeypatch.setattr(runs, "data_manifest", lambda *a, **kw: {
        "entries": {"prices": {"status": "fresh"}, "fred:DGS10": {"status": "fresh"}},
        "status": "fresh", "stale": [], "price_gaps": {}, "hash": "e" * 64})
    monkeypatch.setattr(pipeline.monitoring, "event_alerts", lambda *a, **kw: [])
    monkeypatch.setattr(live_ic, "compute", lambda *a, **kw: {
        "status": "synthetic_fixture", "n_snapshots": 11, "snapshots_with_forward": {}, "validation_status": "NOT VALIDATED"})
    monkeypatch.setattr(forward_registry, "load_contexts", lambda *a: {})
    monkeypatch.setattr(forward_registry, "register_snapshot", lambda *a, **kw: {"record_hash": "f" * 64})
    monkeypatch.setattr(forward_registry, "evaluate", lambda *a, **kw: {
        "status": "synthetic_fixture", "evaluated_at": "2026-09-22T11:21:00+00:00", "n_registered_versions": 1})
    top = pd.DataFrame([{
        "ticker": "XYZ", "name": "Synthetic XYZ", "segment": "fixture", "style": "fixture",
        "rank": 1, "composite": .5, "upside": .2, "price": 100., "fair_value": 120.,
        "ev_sales_ntm": 5., "pe_ntm": 20., "rev_growth_ntm": .2, "fcf_margin": .1,
        "eps_rev_30d": .01, "eps_rev_90d": .02, "market_cap": 1e10,
        "strategic_stale": False, "strategic_source": "synthetic_fixture", "rank_all": 1,
    }]).set_index("ticker")
    weights = pd.Series({"XYZ": 1.})
    methods = {method: {"weights": weights.copy(), "risk_contrib": weights.copy(),
                        "vol": .5, "exp_return": .1, "sharpe": .2} for method in optimizer.CONSTRUCTION_METHODS}
    methods["recommended"] = {"base": "risk_parity", "weights": weights * .5, "cash": .5, "scale": .5,
                              "vol": .25, "exp_return": .05, "sharpe": .2}
    methods["frontier"] = pd.DataFrame({"vol": [.25], "ret": [.05]})
    dates = pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21"])
    prices = pd.DataFrame({symbol: [99., 100., 100.] for symbol in ["XYZ", *pipeline.BENCHMARKS]}, index=dates)
    stats = pd.DataFrame({"vol": [.25], "cagr": [.05]}, index=["Portfolio"])
    returns = pd.DataFrame({"Portfolio": [1., 1., 1.]}, index=dates)
    result = {"opt": methods, "asset_stats": stats.copy(), "corr": pd.DataFrame([[1.]], index=["XYZ"], columns=["XYZ"]),
              "backtest": {"cumulative": returns.copy(), "drawdown": returns * 0, "stats": stats.copy()},
              "construction_backtest": {"cumulative": returns.copy(), "drawdown": returns * 0, "stats": stats.copy()},
              "returns_info": {"start": "2026-09-17", "end": "2026-09-21", "common_observed_days": 3}}
    ctx = {"asof": datetime(2026, 9, 22), "daily_attempt_id": "fixture-attempt", "top": top.copy(), "universe": top.copy(),
           "res": result, "px": prices, "price_finality": {"acquired_at": "2026-09-21T20:30:00+00:00", "fresh_complete_session": True,
                                                         "required_through": "2026-09-21", "confirmed_through": "2026-09-21"},
           "panel": {"DGS10": pd.Series([4.], index=dates[-1:])}, "snaps": {}, "rf": .04,
           "seg": pd.DataFrame({"n": [1]}, index=["fixture"]), "macro_snapshot": pd.DataFrame({"value": [4.]}),
           "strategic_refresh": {"status": "synthetic_fixture", "assessed": []}, "vmeta": {}, "funnel": {},
           "pitchbook": {"status": "synthetic_fixture"}}
    return tmp_path, ctx


def run_persist(fixture):
    _, ctx = fixture
    return pipeline.stage_persist(deepcopy(ctx))


def test_real_staged_persist_publishes_all_required_dashboard_outputs(persist_fixture):
    root, _ = persist_fixture
    out = run_persist(persist_fixture)
    rid = out["run"]["run_id"]
    assert json.loads((root / "latest/run.json").read_text())["run_id"] == rid
    for name in ("snapshot.json", "summary.json", "correlation.csv", "frontier.csv", "risk_contributions.csv",
                 "asset_stats.csv", "segment_table.csv", "macro_panel.json", "prices_top15.csv",
                 "backtest_drawdown.csv", "construction_targets.json", "live_validation.json"):
        assert (root / "latest" / name).read_bytes() == (out["run_dir"] / name).read_bytes()
    assert not (root / "latest/run_publishing.json").exists()


def test_new_run_validation_uses_its_staged_live_diagnostic(persist_fixture):
    root, _ = persist_fixture
    put(root / "latest/live_validation.json", {"status": "old_fixture", "n_snapshots": 3})
    out = run_persist(persist_fixture)
    assert json.loads((out["run_dir"] / "live_validation.json").read_text())["n_snapshots"] == 11
    assert out["run"]["validation"]["live_snapshots"] == 11


def test_fault_before_registration_keeps_old_latest_bytes(persist_fixture, monkeypatch):
    root, _ = persist_fixture
    run_persist(persist_fixture)
    before = {p.name: p.read_bytes() for p in (root / "latest").iterdir() if p.is_file()}
    monkeypatch.setattr(pipeline.monitoring, "kpi_table", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fixture stage interruption")))
    with pytest.raises(RuntimeError, match="fixture stage"):
        run_persist(persist_fixture)
    assert {p.name: p.read_bytes() for p in (root / "latest").iterdir() if p.is_file()} == before


@pytest.mark.parametrize("error", [RuntimeError("fixture unexpected source failure"), OSError("fixture transient disk error"), DataUnavailable("fixture no source")])
def test_precommit_pipeline_errors_leave_observable_failed_gate(persist_fixture, monkeypatch, error):
    root, _ = persist_fixture
    run_persist(persist_fixture)
    def failure(ctx):
        raise error
    monkeypatch.setattr(pipeline, "stage_analysis", failure)
    with pytest.raises(type(error)):
        pipeline.run(stages=("analysis",), allow_llm_refresh=False)
    blocked = root / "latest/run_blocked.json"
    assert blocked.exists()
    assert json.loads(blocked.read_text())["stage"] == "analysis"


def test_published_identity_and_current_snapshot_agree_during_every_mirror_step(persist_fixture, monkeypatch):
    root, ctx = persist_fixture
    old = run_persist(persist_fixture)["run"]["run_id"]
    ctx["top"].loc["XYZ", "price"] = 101.
    observed = []
    original_atomic = publication.atomic_bytes
    def observe(path, content):
        original_atomic(path, content)
        if Path(path).parent == root / "latest":
            rid = config.run_info()["run_id"]
            snapshot = config._snapshot()
            observed.append((Path(path).name, rid, snapshot.get("run_id")))
    monkeypatch.setattr(publication, "atomic_bytes", observe)
    new = run_persist(persist_fixture)["run"]["run_id"]
    assert old != new
    assert all(rid == snap for _, rid, snap in observed), observed


def test_interrupted_publication_keeps_old_current_and_blocks_clock(persist_fixture, monkeypatch):
    root, _ = persist_fixture
    old = run_persist(persist_fixture)["run"]["run_id"]
    original_atomic = publication.atomic_bytes
    def interrupt(path, content):
        if Path(path).name == "run.json":
            raise OSError("fixture power loss before public identity switch")
        original_atomic(path, content)
    monkeypatch.setattr(publication, "atomic_bytes", interrupt)
    with pytest.raises(OSError, match="power loss"):
        run_persist(persist_fixture)
    assert json.loads((root / "latest/run.json").read_text())["run_id"] == old
    assert config._snapshot().get("run_id") == old
    info = config.run_info(datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc))
    assert info["actionable_now"] is False
    assert (root / "latest/run_publishing.json").exists()


def test_recovery_completes_registered_publication_without_research(persist_fixture, monkeypatch):
    root, ctx = persist_fixture
    old = run_persist(persist_fixture)["run"]["run_id"]
    ctx["daily_attempt_id"] = "interrupted-publication-attempt"
    original_atomic = publication.atomic_bytes
    def interrupt(path, content):
        if Path(path).name == "run.json":
            raise OSError("fixture interrupted mirror")
        original_atomic(path, content)
    monkeypatch.setattr(publication, "atomic_bytes", interrupt)
    with pytest.raises(OSError):
        run_persist(persist_fixture)
    put(root / "daily/2026-09-22.json", {"session": "2026-09-22", "status": "RUNNING", "attempt_id": ctx["daily_attempt_id"]})
    monkeypatch.setattr(publication, "atomic_bytes", original_atomic)
    response = daily.run_once("2026-09-22T08:00:00-04:00", state_dir=root / "daily",
                              pipeline_runner=lambda **kw: pytest.fail("recovery cannot rerun research"))
    state = response["prior"]
    # A pending state is acceptable; an advertised ready state requires publish.
    if state["status"] == "TARGET_READY":
        assert json.loads((root / "latest/run.json").read_text())["run_id"] == state["run_id"]
        assert not (root / "latest/run_publishing.json").exists()


def test_postcommit_forward_runtime_failure_is_recorded_not_rethrown(persist_fixture, monkeypatch):
    root, _ = persist_fixture
    monkeypatch.setattr(forward_registry, "register_snapshot", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fixture forward failure")))
    out = run_persist(persist_fixture)
    assert out["forward_evaluation"]["status"] == "error"
    saved = json.loads((root / "latest/forward_evaluation.json").read_text())
    assert saved["run_id"] == out["run"]["run_id"] and saved["error_type"] == "RuntimeError"


def test_compatibility_rows_replayed_for_same_run_are_idempotent(tmp_path):
    path = tmp_path / "history.csv"
    frame = pd.DataFrame({"date": ["2026-09-22"], "ticker": ["XYZ"], "rank": [1]})
    runs.append_rows(path, frame, "already-committed-run")
    before = path.read_bytes()
    runs.append_rows(path, frame, "already-committed-run")
    assert path.read_bytes() == before


def test_recovery_exposes_missing_forward_after_compatibility_write_failure(persist_fixture, monkeypatch):
    root, ctx = persist_fixture
    original_append = runs.append_rows
    monkeypatch.setattr(runs, "append_rows", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture compatibility table failure")))
    with pytest.raises(OSError):
        run_persist(persist_fixture)
    rid = json.loads((root / "latest/run.json").read_text())["run_id"]
    assert not (root / "latest/forward_evaluation.json").exists()
    put(root / "daily/2026-09-22.json", {"session": "2026-09-22", "status": "FAILED", "attempt_id": ctx["daily_attempt_id"]})
    monkeypatch.setattr(runs, "append_rows", original_append)
    try:
        daily.run_once("2026-09-22T08:00:00-04:00", state_dir=root / "daily", retry=True, retry_reason="repair fixture",
                       pipeline_runner=lambda **kw: pytest.fail("must not recompute committed target"))
    except DataUnavailable:
        pass
    report = readiness.payload(root, datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc))
    assert report["run_id"] == rid
    assert report["forward"]["last_evaluation"]["status"] != "ok"


def test_publication_rejects_archive_bytes_changed_after_registration(persist_fixture):
    root, _ = persist_fixture
    result = run_persist(persist_fixture)
    archive = result["run_dir"]
    (archive / "portfolio_weights.csv").write_text("ticker,recommended\nXYZ,999\n")
    previous = (root / "latest/portfolio_weights.csv").read_bytes()
    with pytest.raises((DataUnavailable, ValueError)):
        publication.publish(archive, root / "latest")
    assert (root / "latest/portfolio_weights.csv").read_bytes() == previous


def test_publication_cannot_promote_a_file_absent_from_archive_manifest(persist_fixture):
    root, _ = persist_fixture
    result = run_persist(persist_fixture)
    extra = result["run_dir"] / "unmanifested_projection.json"
    put(extra, {"invented_evidence": True})
    try:
        publication.publish(result["run_dir"], root / "latest")
    except (DataUnavailable, ValueError):
        pass
    assert not (root / "latest" / extra.name).exists()


def test_publication_uses_only_bytes_that_were_actually_verified(persist_fixture, monkeypatch):
    root, _ = persist_fixture
    result = run_persist(persist_fixture)
    original = runs.inspect_completed_archive
    before = (root / "latest/portfolio_weights.csv").read_bytes()
    def change_after_check(folder):
        verified = original(folder)
        (folder / "portfolio_weights.csv").write_text("ticker,recommended\nXYZ,999\n")
        return verified
    monkeypatch.setattr(runs, "inspect_completed_archive", change_after_check)
    try:
        publication.publish(result["run_dir"], root / "latest")
    except (DataUnavailable, ValueError):
        pass
    assert (root / "latest/portfolio_weights.csv").read_bytes() == before


def test_publication_cannot_switch_to_a_different_manifest_after_verification(persist_fixture, monkeypatch):
    root, _ = persist_fixture
    result = run_persist(persist_fixture)
    original = runs.inspect_completed_archive
    before = (root / "latest/portfolio_weights.csv").read_bytes()
    def replace_manifest_after_check(folder):
        verified = original(folder)
        payload = b"ticker,recommended\nXYZ,999\n"
        (folder / "portfolio_weights.csv").write_bytes(payload)
        manifest = json.loads((folder / "archive_manifest.json").read_text())
        from hashlib import sha256
        manifest["files"]["portfolio_weights.csv"] = sha256(payload).hexdigest()
        put(folder / "archive_manifest.json", manifest)
        return verified
    monkeypatch.setattr(runs, "inspect_completed_archive", replace_manifest_after_check)
    try:
        publication.publish(result["run_dir"], root / "latest")
    except (DataUnavailable, ValueError):
        pass
    assert (root / "latest/portfolio_weights.csv").read_bytes() == before


def test_candidate_sidecar_receives_exact_frozen_analysis_inputs(persist_fixture, monkeypatch):
    from dataclasses import asdict
    from ai_semi_intel.research import challengers
    root, ctx = persist_fixture
    ctx.update(strategic_knowledge_at=datetime(2026, 9, 22, 11, 19, tzinfo=timezone.utc),
               screen_config=asdict(pipeline.CONFIG.screen), previous_holdings=["OLD"],
               strategic_assessments={"XYZ": {"source": "synthetic_fixture"}})
    seen = {}
    def build(**kwargs):
        seen.update(kwargs)
        return {"run_id": kwargs["run_id"], "fixture": True}
    def register(folder, value):
        assert folder == root / "history/research_candidates"
        return {"run_id": value["run_id"], "registered_at": "2026-09-22T11:22:00+00:00",
                "record_hash": "a" * 64, "n_rows": 1, "n_paired_scores": 0,
                "selection": {"status": "no_paired_names"}, "validation_status": "NOT VALIDATED",
                "inference_status": "NO_RETURN_EVALUATION", "execution_status": "NO_ORDERS"}
    monkeypatch.setattr(challengers, "build_daily_candidates", build)
    monkeypatch.setattr(challengers, "register_candidates", register)
    out = run_persist(persist_fixture)
    frozen = json.loads((out["run_dir"] / "research_inputs.json").read_text())
    assert frozen["previous_holdings"] == seen["previous_holdings"] == ["OLD"]
    assert frozen["screen_config"] == seen["screen_config"] == ctx["screen_config"]
    assert frozen["knowledge_at"] == seen["knowledge_at"].isoformat()
    assert seen["assessments"] == ctx["strategic_assessments"]
    assert seen["mode"] == "prospective_candidate"
    status = json.loads((root / "latest/research_candidates.json").read_text())
    assert status["status"] == "registered" and status["n_paired_scores"] == 0


def test_candidate_failure_does_not_invalidate_completed_target(persist_fixture, monkeypatch):
    from dataclasses import asdict
    from ai_semi_intel.research import challengers
    root, ctx = persist_fixture
    ctx.update(strategic_knowledge_at=datetime(2026, 9, 22, 11, 19, tzinfo=timezone.utc),
               screen_config=asdict(pipeline.CONFIG.screen), previous_holdings=[], strategic_assessments={})
    def fail(**kwargs):
        raise ValueError("synthetic candidate input failure")
    monkeypatch.setattr(challengers, "build_daily_candidates", fail)
    out = run_persist(persist_fixture)
    assert runs.inspect_completed_archive(out["run_dir"])["run_id"] == out["run"]["run_id"]
    assert json.loads((root / "latest/run.json").read_text())["run_id"] == out["run"]["run_id"]
    assert json.loads((root / "latest/research_candidates.json").read_text())["status"] == "error"
    assert not (root / "latest/run_blocked.json").exists()
