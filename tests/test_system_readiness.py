"""Current clock, immutable archive identity, and missing-evidence regressions."""
from datetime import datetime, timezone
import hashlib
import json

import pytest

from ai_semi_intel import trading_policy
from ai_semi_intel.portfolio.constructions import REGISTRY
from ai_semi_intel.portfolio.target_archive import SCHEMA
from market_intel import config, readiness


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def frozen_run(root):
    ident = {"run_id": "20260922T072000-test", "generated_at": "2026-09-22T07:20:00-04:00", "asof_session": "2026-09-22"}
    run = {**ident, "actionable": True, "rebalance_policy": trading_policy.policy(ident["generated_at"], "2026-09-21"),
           "portfolio": {"construction": "risk_parity", "cash": .5}}
    snap = {**ident, "construction": "risk_parity", "weights": {"NVDA": .25, "AMD": .25}, "cash": .5, "rebalance_policy": run["rebalance_policy"]}
    records = {m: {"construction": m, "model_weights": {"NVDA": .5, "AMD": .5},
                   "target_weights": {"NVDA": .25, "AMD": .25}, "cash": .5, "scale": .5, "model_vol": .5,
                   "vol_target": .25, "sizing_policy": "vol_target_no_leverage_v1"} for m in REGISTRY}
    targets = {**ident, "schema_version": SCHEMA, "constructions": records, "construction_count": len(records),
               "active_construction": "risk_parity", "complete": True, "rebalance_policy": run["rebalance_policy"]}
    folder = root / "history" / "runs" / ident["run_id"]
    for name, value in [("run.json", run), ("snapshot.json", snap), ("construction_targets.json", targets)]:
        save(folder / name, value)
        save(root / "latest" / name, value)
    return run, folder, targets


def test_expired_run_preserves_generation_verdict_and_original_bytes(tmp_path, monkeypatch):
    run, folder, _ = frozen_run(tmp_path)
    monkeypatch.setattr(config, "LATEST", tmp_path / "latest")
    path = tmp_path / "latest" / "run.json"
    before = path.read_bytes()
    value = config.run_info(datetime(2026, 9, 22, 14, tzinfo=timezone.utc))
    assert value["actionable"] is True
    assert value["actionable_at_generation"] is True
    assert value["actionable_now"] is False
    assert value["current_target_status"]["status"] == "EXPIRED"
    assert path.read_bytes() == before


def test_failed_attempt_clock_is_compared_as_an_instant_not_iso_text(tmp_path, monkeypatch):
    frozen_run(tmp_path)
    monkeypatch.setattr(config, "LATEST", tmp_path / "latest")
    # 10:00 UTC is 06:00 ET, earlier than the successfully generated run.
    save(config.LATEST / "run_blocked.json", {"at": "2026-09-22T10:00:00+00:00", "reasons": ["earlier failed attempt"]})
    now = datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc)
    assert config.run_info(now)["actionable_now"] is True
    save(config.LATEST / "run_blocked.json", {"at": "2026-09-22T12:00:00+00:00", "reasons": ["newer failed attempt"]})
    current = config.run_info(now)
    assert current["actionable_now"] is False
    assert current["current_target_status"]["status"] == "BLOCKED"


@pytest.mark.parametrize("mutation", ["identity", "cash", "missing_method", "invalid_active", "invalid_scale"])
def test_inconsistent_construction_archive_cannot_pass(tmp_path, mutation):
    _, folder, targets = frozen_run(tmp_path)
    assert readiness.construction_status(folder)["status"] == "complete"
    if mutation == "identity":
        targets["run_id"] = "different-run"
    elif mutation == "cash":
        targets["constructions"]["hrp"]["cash"] = .6
    elif mutation == "missing_method":
        del targets["constructions"]["hrp"]
    elif mutation == "invalid_scale":
        targets["constructions"]["hrp"]["scale"] = .3
    else:
        targets["constructions"]["risk_parity"] = []
    save(folder / "construction_targets.json", targets)
    assert readiness.construction_status(folder)["status"] == "inconsistent"


def test_readiness_pins_one_run_and_does_not_write_or_assume_old_refresh_count(tmp_path):
    run, folder, _ = frozen_run(tmp_path)
    # A writer may be replacing latest targets; the readiness view stays with its frozen run.
    save(tmp_path / "latest" / "construction_targets.json", {"run_id": "later-incomplete-writer"})
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    result = readiness.payload(tmp_path, datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc))
    assert result["pinned_archive"] is True
    assert result["constructions"]["status"] == "complete"
    assert result["run_id"] == run["run_id"]
    assert result["strategic"]["assessed_count"] is None
    assert result["strategic"]["refresh_evidence"] == "unavailable"
    assert result["status"] == "NOT_DEPLOYABLE" and result["live_trading"] is False
    assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_no_run_cannot_become_actionable_and_coverage_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LATEST", tmp_path / "latest")
    assert config.run_info()["actionable_now"] is False
    result = readiness.payload(tmp_path)
    coverage = next(c for c in result["checks"] if c["id"] == "daily_coverage")
    assert coverage["status"] == "unknown"
    assert result["operation"]["current_target_status"]["status"] == "MISSING"


def test_run_readers_keep_frozen_identity_through_publication(tmp_path, monkeypatch):
    run, folder, _ = frozen_run(tmp_path)
    monkeypatch.setattr(config, "LATEST", tmp_path / "latest")
    save(folder / "universe_scores.csv", {})
    now = datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc)
    with config.pinned_run():
        save(config.LATEST / "run_publishing.json", {"run_id": "new-run"})
        save(config.LATEST / "snapshot.json", {"run_id": "new-run", "top": ["BAD"], "weights": {"BAD": 1}})
        assert config._snapshot()["run_id"] == run["run_id"]
        assert config.run_info(now)["actionable_now"] is False
        save(config.LATEST / "run.json", dict(run, run_id="new-run"))
        assert config.run_info(now)["run_id"] == run["run_id"]
        assert config.run_file(config.LATEST / "universe_scores.csv").parent == folder


def test_forward_evaluation_requires_matching_registration_and_past_clock():
    now = datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc)
    summary = {"latest_run_id": "r1", "latest_record_hash": "abc"}
    valid = {"status": "diagnostic_only", "run_id": "r1", "registration_record_hash": "abc",
             "evaluated_at": "2026-09-22T13:30:00+00:00", "n_registered_versions": 1}
    assert readiness.evaluation_status(valid, "r1", summary, now)["n_registered_versions"] == 1
    for key, value in [("run_id", "r2"), ("registration_record_hash", "wrong"),
                       ("evaluated_at", "2026-09-22T13:32:00+00:00")]:
        assert readiness.evaluation_status(dict(valid, **{key: value}), "r1", summary, now).get("n_registered_versions") is None


def test_web_runner_observes_external_pipeline_lock_without_spawning(tmp_path, monkeypatch):
    import fcntl
    from market_intel import research
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    monkeypatch.setattr(research, "summary", lambda: {})
    monkeypatch.setattr(research.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn overlapping pipeline"))
    runner = research.PipelineRunner()
    runner.LOG = tmp_path / "missing-log"
    with (tmp_path / ".pipeline.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert runner.status()["running"] is True
        assert runner.status()["running_source"] == "scheduled_or_cli"
        assert runner.start()["started"] is False
    assert runner.status()["running"] is False


def test_shadow_test_outputs_never_count_as_real_ledger(tmp_path):
    save(tmp_path / "reviews" / "shadow_accounting" / "ledger.json", {"status": "synthetic_fixture"})
    state = readiness.shadow_status(tmp_path)
    assert state["status"] == "UNAVAILABLE" and state["engine_status"] == "implemented"
    assert state["performance_validated"] is False and state["deployment_status"] == "BLOCKED"


def test_unreadable_failure_marker_cannot_silently_release_old_target(tmp_path, monkeypatch):
    frozen_run(tmp_path)
    monkeypatch.setattr(config, "LATEST", tmp_path / "latest")
    (config.LATEST / "run_blocked.json").write_text('{"at":')
    report = config.run_info(datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc))
    assert report["actionable_now"] is False
    assert report["current_target_status"]["status"] == "BLOCKED"
