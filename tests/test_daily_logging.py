"""Scheduler diagnostics identify real clocks and never echo vendor secrets."""
from datetime import datetime, timezone
import json

import pytest

from ai_semi_intel import daily


def test_actual_attempt_logs_real_utc_clock_and_safe_failure(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.siyanchen.aisemi.daily")
    monkeypatch.setattr(daily, "OUTPUTS", tmp_path.parent)
    secret = "private-vendor-secret"

    def fail(**kwargs):
        raise RuntimeError("https://vendor.invalid?api_key=" + secret)

    before = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError):
        daily.run_once("2026-09-14T07:15:00-04:00", state_dir=tmp_path,
                       pipeline_runner=fail, log_events=True)
    after = datetime.now(timezone.utc)
    lines = capsys.readouterr().out.splitlines()
    assert secret not in "\n".join(lines)
    events = [json.loads(line) for line in lines]
    assert [v["event"] for v in events] == ["DAILY_ATTEMPT_STARTED", "DAILY_ATTEMPT_FINISHED"]
    assert [v["status"] for v in events] == ["RUNNING", "FAILED"]
    assert events[1]["error_type"] == "RuntimeError"
    assert events[0]["attempt_id"] == events[1]["attempt_id"]
    assert all(before <= datetime.fromisoformat(v["observed_at"]) <= after for v in events)
    state = json.loads((tmp_path / "2026-09-14.json").read_text())
    assert state["log_source"] == {
        "kind": "launchd", "label": "com.siyanchen.aisemi.daily",
        "path": str(tmp_path.parent / "logs" / "aisemi_daily.out"),
    }
    assert secret not in json.dumps(state)
    # Repeated scheduling does not emit a new start or repeat failed research.
    result = daily.run_once("2026-09-14T07:20:00-04:00", state_dir=tmp_path,
                            pipeline_runner=fail, log_events=True)
    assert result["status"] == "ALREADY_ATTEMPTED"
    assert capsys.readouterr().out == ""


def test_cli_exception_is_bounded_and_keeps_failure_exit_code(monkeypatch, capsys):
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("Authorization: Bearer private-vendor-secret")

    monkeypatch.setattr(daily, "run_once", fail)
    assert daily.main([]) == 1
    captured = capsys.readouterr()
    assert "private-vendor-secret" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    event = json.loads(captured.out)
    assert event["event"] == "DAILY_COMMAND_FAILED"
    assert event["error_type"] == "RuntimeError"
    assert calls == [{"check": False, "retry": False, "retry_reason": None,
                      "record_poll": True, "log_events": True}]


def test_cli_completed_state_redacts_retry_reason(monkeypatch, capsys):
    monkeypatch.setattr(daily, "run_once", lambda **kwargs: {
        "status": "TARGET_READY", "retry_reason": "api_key=private-vendor-secret"
    })
    assert daily.main([]) == 0
    output = capsys.readouterr().out
    assert "private-vendor-secret" not in output
    assert "[REDACTED]" in output


def test_manual_daily_stdout_is_not_labeled_as_scheduler_file(monkeypatch):
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    assert daily._log_source() == {"kind": "daily_cli", "stream": "stdout"}


def test_idle_poll_stays_quiet_and_check_does_not_write(tmp_path, capsys):
    daily.run_once("2026-09-23T00:52:00-07:00", state_dir=tmp_path,
                   log_events=True, check=True)
    daily.run_once("2026-09-23T00:52:00-07:00", state_dir=tmp_path,
                   log_events=True)
    assert capsys.readouterr().out == ""
    assert list(tmp_path.iterdir()) == []
