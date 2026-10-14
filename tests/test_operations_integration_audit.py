"""Independent operational boundary regressions; all inputs are synthetic.

These tests state the safe contract. They deliberately exposed failures during
the September 22 audit; production code changes belong to the owning agent.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from ai_semi_intel import daily, runs, trading_policy as tp
from ai_semi_intel.portfolio.constructions import REGISTRY
from ai_semi_intel.portfolio import target_archive
from ai_semi_intel.utils.io import DataUnavailable
from market_intel import config, readiness

NOW = datetime(2026, 9, 22, 13, 31, tzinfo=timezone.utc)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def run(attempt="audit-attempt", run_id="audit-run"):
    generated = "2026-09-22T07:20:00-04:00"
    return {"run_id": run_id, "generated_at": generated, "asof_session": "2026-09-22",
            "actionable": True, "daily_attempt_id": attempt,
            "rebalance_policy": tp.policy(generated, "2026-09-21"),
            "portfolio": {"construction": "risk_parity", "cash": .5}}


def artifacts(root, value):
    ident = {key: value[key] for key in ("run_id", "generated_at", "asof_session")}
    snap = {**ident, "construction": "risk_parity", "weights": {"XYZ": .5}, "cash": .5, "vol_target": .25,
            "rebalance_policy": deepcopy(value["rebalance_policy"])}
    targets = target_archive.build({name: {"weights": {"XYZ": 1.}, "vol": .5} for name in REGISTRY}, snap, list(REGISTRY))
    folder = root / "history" / "runs" / value["run_id"]
    for name, data in (("run.json", value), ("snapshot.json", snap), ("construction_targets.json", targets)):
        put(folder / name, data)
        put(root / "latest" / name, data)
    put(root / "history" / "runs" / "index.json", [{"run_id": value["run_id"], "asof_session": value["asof_session"],
                                                    "generated_at": value["generated_at"], "status": "pending"}])
    return folder, targets


def test_recovery_rejects_unknown_method_and_invalid_accounting(tmp_path):
    r = run()
    folder, _ = artifacts(tmp_path, r)
    put(folder / "construction_targets.json", {"run_id": r["run_id"], "complete": True,
         "construction_count": 1, "constructions": {"bogus": {"cash": -1}}})
    put(tmp_path / "daily" / "2026-09-22.json", {"session": "2026-09-22", "status": "RUNNING", "attempt_id": "audit-attempt"})
    result = daily.run_once("2026-09-22T08:00:00-04:00", state_dir=tmp_path / "daily",
                            pipeline_runner=lambda **kw: pytest.fail("recovery must not rerun research"))
    assert result["prior"]["status"] == "INTERRUPTED"


def test_recovery_rejects_mixed_snapshot_identity(tmp_path):
    r = run()
    folder, _ = artifacts(tmp_path, r)
    snap = json.loads((folder / "snapshot.json").read_text())
    put(folder / "snapshot.json", dict(snap, run_id="a-different-completed-run"))
    put(tmp_path / "daily" / "2026-09-22.json", {"session": "2026-09-22", "status": "RUNNING", "attempt_id": "audit-attempt"})
    result = daily.run_once("2026-09-22T08:00:00-04:00", state_dir=tmp_path / "daily",
                            pipeline_runner=lambda **kw: pytest.fail("recovery must not rerun research"))
    assert result["prior"]["status"] == "INTERRUPTED"


def test_nonboolean_actionability_cannot_become_target_ready():
    state = {"session": "2026-09-22", "attempt_id": "audit-attempt"}
    try:
        daily._apply_result(state, dict(run(), actionable="false"), 0)
    except DataUnavailable:
        return
    assert state["status"] != "TARGET_READY"


def test_stale_price_policy_cannot_become_ready_via_saved_true_flag():
    state = {"session": "2026-09-22", "attempt_id": "audit-attempt"}
    r = run()
    r["rebalance_policy"] = tp.policy(r["generated_at"], "2026-09-18")
    try:
        daily._apply_result(state, r, len(REGISTRY))
    except DataUnavailable:
        return
    assert state["status"] != "TARGET_READY"


def test_readiness_respects_newer_failed_attempt_when_pinning_archive(tmp_path, monkeypatch):
    artifacts(tmp_path, run())
    monkeypatch.setattr(config, "LATEST", tmp_path / "latest")
    put(config.LATEST / "run_blocked.json", {"at": "2026-09-22T12:00:00+00:00", "reasons": ["later failure"]})
    assert config.run_info(NOW)["actionable_now"] is False
    report = readiness.payload(tmp_path, NOW)
    assert report["operation"]["actionable_now"] is False
    assert report["operation"]["current_target_status"]["status"] == "BLOCKED"


@pytest.mark.parametrize("malformed", [["not-a-run-object"], "not-a-run-object", 12])
def test_run_info_malformed_json_shape_is_unavailable_not_endpoint_crash(tmp_path, monkeypatch, malformed):
    monkeypatch.setattr(config, "LATEST", tmp_path)
    put(tmp_path / "run.json", malformed)
    result = config.run_info(NOW)
    assert result["actionable_now"] is False


def test_readiness_does_not_attach_different_run_forward_evaluation(tmp_path):
    artifacts(tmp_path, run())
    put(tmp_path / "latest" / "forward_evaluation.json", {"status": "ok", "run_id": "unrelated-later-run",
         "evaluated_at": "2026-09-22T13:30:00+00:00", "n_registered_versions": 999})
    report = readiness.payload(tmp_path, NOW)
    last = report["forward"]["last_evaluation"]
    assert last.get("run_id") in (None, report["run_id"])
    assert last.get("n_registered_versions") != 999


def test_run_registration_rejects_source_identity_mismatch(tmp_path):
    current = run()
    latest, history = tmp_path / "latest", tmp_path / "history"
    put(latest / "run.json", dict(current, run_id="previous-run"))
    try:
        runs.register(current, latest, history)
    except (DataUnavailable, ValueError):
        return
    pytest.fail("registration indexed a run whose archived run.json belongs to another identity")


def test_failed_archive_copy_cannot_publish_partial_run_directory(tmp_path, monkeypatch):
    current = run()
    latest, history = tmp_path / "latest", tmp_path / "history"
    put(latest / "run.json", current)
    put(latest / "snapshot.json", dict(current))
    original_copy = runs.shutil.copy
    copies = 0
    def crash_copy(source, destination):
        nonlocal copies
        copies += 1
        if copies == 2:
            raise OSError("synthetic interruption before archive completion")
        return original_copy(source, destination)
    monkeypatch.setattr(runs.shutil, "copy", crash_copy)
    with pytest.raises((OSError, DataUnavailable, ValueError)):
        runs.register(current, latest, history)
    assert not (history / "runs" / current["run_id"]).exists()
    assert not runs.load_index(history)


def test_post_commit_diagnostic_failure_does_not_repeat_completed_research(tmp_path):
    calls = []
    def crash_after_commit(**kwargs):
        calls.append(kwargs)
        committed = run(kwargs["daily_attempt_id"], "already-committed")
        artifacts(tmp_path, committed)
        raise RuntimeError("synthetic forward diagnostic failure after immutable target registration")
    try:
        daily.run_once("2026-09-22T08:00:00-04:00", state_dir=tmp_path / "daily", pipeline_runner=crash_after_commit)
    except RuntimeError:
        pass
    # An explicit retry may repair/report post-processing; it must first notice
    # the exact registered attempt and avoid another scoring/target invocation.
    try:
        daily.run_once("2026-09-22T08:01:00-04:00", state_dir=tmp_path / "daily", pipeline_runner=crash_after_commit,
                       retry=True, retry_reason="diagnostic stage recovered")
    except DataUnavailable:
        pass
    except RuntimeError:
        pass
    assert len(calls) == 1


def test_retry_disables_actual_client_initialization_not_only_report_flag(monkeypatch, tmp_path):
    from ai_semi_intel.analysis import strategic
    monkeypatch.setattr(strategic, "_client", lambda: pytest.fail("retry must not initialize a paid client"))
    monkeypatch.setattr(strategic, "detect_triggers", lambda *a, **kw: {"XYZ": ["fixture-refresh"]})
    # Inspect the actual implementation's early exit with all other side effects
    # redirected to a temporary cache; no network or real assessment is used.
    for name in ("CONTEXT_DIR", "ASSESS_DIR", "HISTORY"):
        if hasattr(strategic, name):
            monkeypatch.setattr(strategic, name, tmp_path / name)
    if hasattr(strategic, "QUEUE_PATH"):
        monkeypatch.setattr(strategic, "QUEUE_PATH", tmp_path / "queue.json")
    result = strategic.refresh(pd.DataFrame(index=["XYZ"]), pd.DataFrame(), datetime(2026, 9, 22), allow_llm_refresh=False)
    assert result["allow_llm_refresh"] is False
