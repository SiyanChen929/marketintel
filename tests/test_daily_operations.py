"""Operational counterexamples: clock eligibility is not trading validation."""
from copy import deepcopy
import fcntl
import hashlib
import json

import pytest

from ai_semi_intel import daily, trading_policy as tp
from ai_semi_intel.utils.io import DataUnavailable


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def run(day="2026-09-14", prior="2026-09-11", attempt=None, actionable=True):
    generated = day + "T07:20:00-04:00"
    return {"run_id": "fixture-" + day, "generated_at": generated, "asof_session": day,
            "actionable": actionable, "daily_attempt_id": attempt,
            "rebalance_policy": tp.policy(generated, prior)}


def result(r):
    from ai_semi_intel.portfolio import target_archive
    from ai_semi_intel.portfolio.constructions import REGISTRY
    snap = dict(r, construction="risk_parity", weights={"A": .5}, cash=.5, vol_target=.25)
    targets = target_archive.build({m: {"weights": {"A": 1.}, "vol": .5} for m in REGISTRY}, snap, list(REGISTRY))
    return {"run": r, "snapshot": snap, "construction_targets": targets}


def archive(root, r):
    staged_result(root, r)


@pytest.mark.parametrize("when,state,allowed", [
    ("2026-09-14T09:29:59-04:00", "PENDING_OPEN", False),
    ("2026-09-14T09:30:00-04:00", "OPEN", True),
    ("2026-09-14T09:35:00-04:00", "EXPIRED", False),
    ("2026-09-22T06:00:00-04:00", "EXPIRED", False),
])
def test_current_eligibility_does_not_rewrite_historical_actionable(when, state, allowed):
    r = run()
    old = deepcopy(r)
    result = tp.current_status(r, when)
    assert result["status"] == state and result["actionable_now"] is allowed
    assert result["actionable_at_generation"] is True and r == old
    assert result["live_trading"] is False


def test_current_status_rejects_tampered_future_and_incomplete_policy():
    r = run()
    r["rebalance_policy"]["expires_at"] = "2026-09-23T13:35:00+00:00"
    assert tp.current_status(r, "2026-09-22T06:00:00-04:00")["status"] == "INVALID_POLICY"
    assert tp.current_status(run(), "2026-09-14T07:00:00-04:00")["status"] == "INVALID_POLICY"
    assert tp.current_status({"actionable": True}, "2026-09-14T09:31:00-04:00")["actionable_now"] is False
    assert tp.current_status(run(actionable=False), "2026-09-14T09:31:00-04:00")["status"] == "BLOCKED"


def test_read_only_status_reports_missing_sessions_without_making_history(tmp_path):
    r = run()
    archive(tmp_path, r)
    put(tmp_path / "latest" / "run.json", r)
    put(tmp_path / "daily" / "2026-09-14.json", {"session": "2026-09-14", "status": "TARGET_READY"})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    s = daily.status("2026-09-22T06:00:00-04:00", state_dir=tmp_path / "daily")
    assert s["missed_sessions"] == ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]
    assert s["current_target_status"]["status"] == "EXPIRED" and s["actionable_now"] is False
    assert s["today_state"] is None and s["heartbeat_status"] == "missing"
    assert s["next_preparation"] == "2026-09-22T07:15:00-04:00"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_manual_archive_covers_session_and_missing_archive_is_incomplete(tmp_path):
    put(tmp_path / "daily" / "2026-09-14.json", {"session": "2026-09-14", "status": "TARGET_READY"})
    archive(tmp_path, run("2026-09-15", "2026-09-14"))
    s = daily.status("2026-09-16T09:15:00-04:00", state_dir=tmp_path / "daily")
    assert s["incomplete_sessions"] == ["2026-09-14"]
    assert s["missed_sessions"] == ["2026-09-16"]


def test_local_timezone_does_not_change_deadline_and_idle_poll_never_calls_pipeline(tmp_path):
    def forbidden(**kwargs):
        raise AssertionError("idle poll must not call research")
    s = daily.run_once("2026-09-21T06:16:00-07:00", state_dir=tmp_path,
                       pipeline_runner=forbidden, record_poll=True)
    assert s["status"] == "WAITING"
    assert not (tmp_path / "2026-09-21.json").exists()
    heartbeat = json.loads((tmp_path / "heartbeat.json").read_text())
    assert heartbeat["due"] is False
    assert daily.status("2026-09-21T06:20:00-07:00", state_dir=tmp_path)["heartbeat_status"] == "fresh"
    assert daily.status("2026-09-21T06:40:00-07:00", state_dir=tmp_path)["heartbeat_status"] == "stale"


def test_read_only_check_never_creates_heartbeat(tmp_path):
    daily.run_once("2026-09-14T07:15:00-04:00", state_dir=tmp_path, check=True, record_poll=True)
    assert list(tmp_path.iterdir()) == []
    daily.status("2026-09-14T07:15:00-04:00", state_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_retry_preserves_failed_bytes_and_disables_new_llm_requests(tmp_path):
    path = tmp_path / "2026-09-14.json"
    failed = {"session": "2026-09-14", "status": "FAILED", "attempt_id": "first", "attempt_number": 1}
    put(path, failed)
    raw = path.read_bytes()
    calls = []
    def runner(**kwargs):
        calls.append(kwargs)
        return result(run(attempt=kwargs["daily_attempt_id"]))
    output = daily.run_once("2026-09-14T08:00:00-04:00", state_dir=tmp_path,
                            pipeline_runner=runner, retry=True, retry_reason="source recovered")
    assert output["status"] == "TARGET_READY" and output["attempt_number"] == 2
    assert calls[0]["allow_llm_refresh"] is False and calls[0]["daily_attempt_id"] != "first"
    assert output["retry_reason"] == "source recovered"
    assert (tmp_path / output["prior_state_archive"]).read_bytes() == raw
    assert hashlib.sha256(raw).hexdigest() in output["prior_state_archive"]
    daily.run_once("2026-09-14T08:01:00-04:00", state_dir=tmp_path, pipeline_runner=runner)
    assert len(calls) == 1


@pytest.mark.parametrize("prior_status", [None, "TARGET_READY", "INVALID"])
def test_retry_cannot_replace_success_or_unknown_state(tmp_path, prior_status):
    if prior_status:
        put(tmp_path / "2026-09-14.json", {"session": "2026-09-14", "status": prior_status})
    with pytest.raises(DataUnavailable, match="explicit retry requires"):
        daily.run_once("2026-09-14T08:00:00-04:00", state_dir=tmp_path, retry=True, retry_reason="review")


def test_retry_requires_reason_and_cannot_backfill(tmp_path):
    with pytest.raises(DataUnavailable, match="audit reason"):
        daily.run_once("2026-09-14T08:00:00-04:00", state_dir=tmp_path, retry=True)
    with pytest.raises(DataUnavailable, match="preparation window"):
        daily.run_once("2026-09-14T09:16:00-04:00", state_dir=tmp_path, retry=True, retry_reason="late")
    assert list(tmp_path.iterdir()) == []


def test_crash_recovery_matches_exact_registered_attempt_without_research(tmp_path):
    state_dir = tmp_path / "daily"
    path = state_dir / "2026-09-14.json"
    put(path, {"session": "2026-09-14", "status": "RUNNING", "attempt_id": "known"})
    raw = path.read_bytes()
    archive(tmp_path, run(attempt="known"))
    def forbidden(**kwargs):
        raise AssertionError("completed attempt must not rerun")
    result = daily.run_once("2026-09-14T08:00:00-04:00", state_dir=state_dir, pipeline_runner=forbidden)
    assert result["status"] == "ALREADY_ATTEMPTED" and result["prior"]["status"] == "TARGET_READY"
    assert result["prior"]["construction_count"] == 13
    assert (state_dir / result["prior"]["prior_state_archive"]).read_bytes() == raw


def test_crash_without_matching_attempt_is_interrupted_not_recharged(tmp_path):
    state_dir = tmp_path / "daily"
    put(state_dir / "2026-09-14.json", {"session": "2026-09-14", "status": "RUNNING", "attempt_id": "lost"})
    archive(tmp_path, run(attempt="unrelated-manual"))
    result = daily.run_once("2026-09-14T08:00:00-04:00", state_dir=state_dir,
                           pipeline_runner=lambda **kw: pytest.fail("must not retry"))
    assert result["prior"]["status"] == "INTERRUPTED"


def test_crash_cannot_promote_incomplete_construction_archive(tmp_path):
    state_dir = tmp_path / "daily"
    put(state_dir / "2026-09-14.json", {"session": "2026-09-14", "status": "RUNNING", "attempt_id": "known"})
    r = run(attempt="known")
    archive(tmp_path, r)
    put(tmp_path / "history" / "runs" / r["run_id"] / "construction_targets.json", {"construction_count": 13})
    result = daily.run_once("2026-09-14T08:00:00-04:00", state_dir=state_dir,
                           pipeline_runner=lambda **kw: pytest.fail("must not retry"))
    assert result["prior"]["status"] == "INTERRUPTED"


def test_wrong_pipeline_attempt_fails_without_saving_ready(tmp_path):
    with pytest.raises(DataUnavailable, match="source attempt"):
        daily.run_once("2026-09-14T08:00:00-04:00", state_dir=tmp_path,
                       pipeline_runner=lambda **kw: result(run(attempt="someone-else")))
    assert json.loads((tmp_path / "2026-09-14.json").read_text())["status"] == "FAILED"


def test_lock_prevents_recovery_of_a_running_worker(tmp_path):
    put(tmp_path / "2026-09-14.json", {"session": "2026-09-14", "status": "RUNNING"})
    with (tmp_path / "prepare.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = daily.run_once("2026-09-14T08:00:00-04:00", state_dir=tmp_path)
        assert result["status"] == "ALREADY_RUNNING"
        assert daily.status("2026-09-14T08:00:00-04:00", state_dir=tmp_path)["running"] is True
    assert json.loads((tmp_path / "2026-09-14.json").read_text())["status"] == "RUNNING"


def test_corrupt_operational_files_fail_closed_without_endpoint_crash(tmp_path):
    put(tmp_path / "daily" / "2026-09-14.json", {"session": "wrong", "status": "TARGET_READY"})
    put(tmp_path / "daily" / "9999-99-99.json", {})
    put(tmp_path / "daily" / "heartbeat.json", [])
    put(tmp_path / "latest" / "run.json", [])
    put(tmp_path / "history" / "runs" / "index.json", [{"run_id": "../../escape"}])
    s = daily.status("2026-09-14T10:00:00-04:00", state_dir=tmp_path / "daily")
    assert s["actionable_now"] is False and s["heartbeat_status"] == "invalid"
    assert len(s["warnings"]) >= 4


def staged_result(root, r):
    from ai_semi_intel import runs
    complete = result(r)
    source = root / "stage"
    for name, data in (("run.json", r), ("snapshot.json", complete["snapshot"]),
                       ("construction_targets.json", complete["construction_targets"])):
        put(source / name, data)
    complete["run_dir"] = runs.register(r, source, root / "history")
    return complete


def test_committed_directory_recovers_failed_index_without_research(tmp_path, monkeypatch):
    from ai_semi_intel import runs
    original = runs.save_index
    failures = []
    calls = []
    def fail_first(index, history):
        if not failures:
            failures.append(True)
            raise OSError("index commit interrupted")
        return original(index, history)
    monkeypatch.setattr(runs, "save_index", fail_first)
    def runner(**kwargs):
        calls.append(kwargs)
        return staged_result(tmp_path, run(attempt=kwargs["daily_attempt_id"]))
    with pytest.raises(OSError, match="index commit"):
        daily.run_once("2026-09-14T07:15:00-04:00", state_dir=tmp_path / "daily", pipeline_runner=runner)
    assert not runs.load_index(tmp_path / "history")
    recovered = daily.run_once("2026-09-14T07:25:00-04:00", state_dir=tmp_path / "daily",
        pipeline_runner=runner, retry=True, retry_reason="repair index commit")
    assert len(calls) == 1
    assert recovered["prior"]["status"] == "TARGET_READY"
    assert runs.load_index(tmp_path / "history")[0]["run_id"] == "fixture-2026-09-14"
    assert json.loads((tmp_path / "latest" / "run.json").read_text())["run_id"] == "fixture-2026-09-14"


def test_failed_publication_repairs_existing_target_without_research(tmp_path, monkeypatch):
    from ai_semi_intel import publication
    original = publication.atomic_bytes
    writes, calls = [], []
    def fail_mirror(path, raw):
        writes.append(path.name)
        if len(writes) == 2:
            raise OSError("mirror interrupted")
        return original(path, raw)
    monkeypatch.setattr(publication, "atomic_bytes", fail_mirror)
    def runner(**kwargs):
        calls.append(kwargs)
        complete = staged_result(tmp_path, run(attempt=kwargs["daily_attempt_id"]))
        publication.publish(complete["run_dir"], tmp_path / "latest")
        return complete
    with pytest.raises(OSError, match="mirror interrupted"):
        daily.run_once("2026-09-14T07:15:00-04:00", state_dir=tmp_path / "daily", pipeline_runner=runner)
    assert (tmp_path / "latest" / "run_publishing.json").exists()
    recovered = daily.run_once("2026-09-14T07:25:00-04:00", state_dir=tmp_path / "daily",
        pipeline_runner=runner, retry=True, retry_reason="repair mirror")
    assert len(calls) == 1 and recovered["prior"]["status"] == "TARGET_READY"
    assert recovered["prior"]["publication_recovery"] == "restored"
    assert not (tmp_path / "latest" / "run_publishing.json").exists()


def test_recovery_preserves_newer_latest_and_newer_blocked_marker(tmp_path):
    r = run(attempt="old-attempt")
    archive(tmp_path, r)
    newer = dict(run(attempt="new-attempt"), run_id="newer-run", generated_at="2026-09-14T08:30:00-04:00")
    newer["rebalance_policy"] = tp.policy(newer["generated_at"], "2026-09-11")
    put(tmp_path / "latest" / "run.json", newer)
    put(tmp_path / "latest" / "run_blocked.json", {"at": "2026-09-14T12:40:00Z", "reasons": ["newer failure"]})
    put(tmp_path / "daily" / "2026-09-14.json", {"session": "2026-09-14", "status": "FAILED", "attempt_id": "old-attempt"})
    recovered = daily.run_once("2026-09-14T08:50:00-04:00", state_dir=tmp_path / "daily", retry=True,
        retry_reason="recover prior attempt", pipeline_runner=lambda **kw: pytest.fail("must not repeat research"))
    assert recovered["prior"]["publication_recovery"] == "preserved_newer_latest"
    assert json.loads((tmp_path / "latest" / "run.json").read_text())["run_id"] == "newer-run"
    assert (tmp_path / "latest" / "run_blocked.json").exists()


def test_recovery_does_not_clear_later_failure_for_same_run(tmp_path):
    r = run(attempt="known")
    archive(tmp_path, r)
    put(tmp_path / "latest" / "run.json", r)
    put(tmp_path / "latest" / "run_blocked.json", {"at": "2026-09-14T12:40:00Z", "reasons": ["later failure"]})
    put(tmp_path / "daily" / "2026-09-14.json", {"session": "2026-09-14", "status": "RUNNING", "attempt_id": "known"})
    daily.run_once("2026-09-14T08:50:00-04:00", state_dir=tmp_path / "daily",
                   pipeline_runner=lambda **kw: pytest.fail("must not repeat research"))
    assert (tmp_path / "latest" / "run_blocked.json").exists()
