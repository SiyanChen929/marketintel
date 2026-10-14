"""Independent operational counterexamples; no pipeline or network requests."""
from datetime import datetime, timezone
import json
import os

import pytest

from market_intel.pipeline_activity import describe


NOW = datetime.fromisoformat("2026-09-23T08:00:00+00:00")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def setup_logs(tmp_path, state=None):
    web = tmp_path / "logs" / "pipeline_web.log"
    web.parent.mkdir(parents=True)
    web.write_text("OLD_MANUAL_LOG_0914")
    stamp = datetime.fromisoformat("2026-09-14T11:15:00+00:00").timestamp()
    os.utime(web, (stamp, stamp))
    auto = web.parent / "aisemi_daily.out"
    auto.write_text("DAILY_LOG_0922")
    stamp = datetime.fromisoformat("2026-09-22T11:18:37+00:00").timestamp()
    os.utime(auto, (stamp, stamp))
    default = {"session": "2026-09-22", "status": "TARGET_READY", "started_at": "2026-09-22T11:15:25+00:00",
               "finished_at": "2026-09-22T11:18:37+00:00", "run_id": "daily-0922", "construction_count": 13}
    default.update(state or {})
    write_json(tmp_path / "daily" / (default["session"] + ".json"), default)
    return web, auto


def test_recent_daily_attempt_survives_web_service_restart(tmp_path):
    web, _ = setup_logs(tmp_path)
    result = describe(tmp_path, {"running": False}, web, occupied=False, now=NOW)
    assert result["task_source"] == "daily"
    assert result["attempt_run_id"] == "daily-0922"
    assert result["log_tail"] == "DAILY_LOG_0922"
    assert result["task_status"] == "TARGET_READY" and result["returncode"] == 0
    assert result["daily_state"] is None
    assert result["last_daily_state"]["session"] == "2026-09-22"


def test_current_web_process_has_priority_over_prior_daily_log(tmp_path):
    web, _ = setup_logs(tmp_path)
    result = describe(tmp_path, {"running": True, "started_at": "2026-09-23T07:59:00+00:00"},
                      web, occupied=True, now=NOW)
    assert result["task_source"] == "web" and result["running"]
    assert result["attempt_run_id"] is None
    assert result["log_source"] == "web"


def test_persisted_running_web_job_without_live_lock_is_interrupted(tmp_path):
    web, _ = setup_logs(tmp_path)
    result = describe(tmp_path, {"running": False, "started_at": "2026-09-23T07:00:00+00:00",
                                "status": "RUNNING", "returncode": None}, web, occupied=False, now=NOW)
    assert not result["running"]
    assert result["task_status"] == "INTERRUPTED"


def test_blocked_daily_retains_real_nonzero_command_result(tmp_path):
    web, _ = setup_logs(tmp_path, {"status": "TARGET_BLOCKED"})
    result = describe(tmp_path, {}, web, occupied=False, now=NOW)
    assert result["task_status"] == "TARGET_BLOCKED"
    assert result["returncode"] == 1


@pytest.mark.parametrize("bad_source", [["launchd"], "launchd", True])
def test_malformed_daily_log_source_cannot_crash_status_or_select_other_file(tmp_path, bad_source):
    web, _ = setup_logs(tmp_path, {"log_source": bad_source})
    result = describe(tmp_path, {}, web, occupied=False, now=NOW)
    assert result["task_source"] == "daily"
    assert result["log_tail"] == ""
    assert result["log_error"]


def test_unknown_cli_lock_does_not_relabel_old_daily_attempt_as_current(tmp_path):
    web, _ = setup_logs(tmp_path)
    result = describe(tmp_path, {}, web, occupied=True, now=NOW)
    assert result["running"] and result["task_source"] == "external"
    assert result["attempt_run_id"] is None
    assert result["attempt_session"] is None
    assert result["started_at"] is None
    assert result["log_tail"] == ""  # Prior log may be exposed separately with an explicit old-attempt label.


def test_future_daily_start_cannot_hide_last_real_attempt(tmp_path):
    web, _ = setup_logs(tmp_path)
    write_json(tmp_path / "daily" / "2026-09-23.json", {
        "session": "2026-09-23", "status": "TARGET_READY", "started_at": "2026-09-23T11:15:00+00:00",
        "finished_at": "2026-09-23T11:20:00+00:00", "run_id": "future",
    })
    result = describe(tmp_path, {}, web, occupied=False, now=NOW)
    assert result["attempt_run_id"] == "daily-0922"
    assert result["last_daily_state"]["session"] == "2026-09-22"


def test_future_completion_is_not_reported_as_finished_success(tmp_path):
    web, _ = setup_logs(tmp_path, {"finished_at": "2026-09-24T11:18:37+00:00"})
    result = describe(tmp_path, {}, web, occupied=False, now=NOW)
    assert result["task_status"] != "TARGET_READY"
    assert result["returncode"] != 0


def test_invalid_session_name_cannot_become_latest_daily_state(tmp_path):
    web, _ = setup_logs(tmp_path)
    write_json(tmp_path / "daily" / "9999-99-99.json", {
        "session": "9999-99-99", "status": "TARGET_READY", "started_at": "2026-09-23T07:00:00+00:00",
        "finished_at": "2026-09-23T07:05:00+00:00", "run_id": "corrupt",
    })
    result = describe(tmp_path, {}, web, occupied=False, now=NOW)
    assert result["attempt_run_id"] == "daily-0922"
    assert result["last_daily_state"]["session"] == "2026-09-22"


def test_redaction_precedes_tail_slice(tmp_path):
    web, auto = setup_logs(tmp_path)
    auto.write_text("api_key=" + "private" * 1000 + "\n")
    result = describe(tmp_path, {}, web, occupied=False, now=NOW)
    assert "private" not in result["log_tail"]
    assert "[REDACTED]" in result["log_tail"]


@pytest.mark.parametrize("runner_name", ["BLRunner", "ConstructionRunner"])
def test_specialized_runner_status_does_not_adopt_primary_daily_activity(tmp_path, monkeypatch, runner_name):
    from market_intel import research
    from ai_semi_intel import publication
    setup_logs(tmp_path, {"status": "RUNNING", "finished_at": None})
    monkeypatch.setattr(research.config, "OUTPUTS", tmp_path)
    monkeypatch.setattr(research, "summary", lambda: {})

    def fail(_):
        raise AssertionError("Independent research must not probe primary pipeline ownership")

    monkeypatch.setattr(publication, "pipeline_running", fail)
    runner = getattr(research, runner_name)()
    runner.LOG = tmp_path / "logs" / (runner_name + ".log")
    result = runner.status()
    assert not result["running"]
    assert result["attempt_run_id"] is None
    assert result["log_tail"] == ""
    assert result["next_preparation_at"] is None
