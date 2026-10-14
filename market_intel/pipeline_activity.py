"""Read-only view of scheduled and manual pipeline activity."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from ai_semi_intel import trading_policy
from ai_semi_intel.daily import HEARTBEAT_MAX_SECONDS
from ai_semi_intel.utils.redaction import redact_sensitive_text


def _object(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _time(value):
    try:
        return trading_policy.aware(value).to_pydatetime() if value else None
    except (ValueError, TypeError, RuntimeError, OverflowError):
        return None


def describe(outputs: Path, web: dict, web_log: Path, *, occupied: bool | None,
             now: datetime, include_daily: bool = True) -> dict:
    """Keep the last actual attempt separate from today's scheduler heartbeat."""
    candidates, states = [], {}
    web_at = _time(web.get("started_at"))
    if web_at is None and web_log.is_file():
        web_at = datetime.fromtimestamp(web_log.stat().st_mtime, timezone.utc)
    if web_at is not None and web_at <= now:
        candidates.append((web_at, "web", web, web_log))
    clock = trading_policy.preparation_window(now)
    if include_daily:
        for path in sorted((outputs / "daily").glob("????-??-??.json")):
            try:
                datetime.strptime(path.stem, "%Y-%m-%d")
            except ValueError:
                continue
            state = _object(path)
            at = _time(state.get("started_at"))
            if state.get("session") != path.stem or at is None or at > now:
                continue
            finished = _time(state.get("finished_at"))
            if state.get("finished_at") and (finished is None or finished > now or finished < at):
                state = dict(state, status="INVALID", finished_at=None)
            states[path.stem] = state
            source = state.get("log_source") or {}
            # Never read an arbitrary path supplied by a saved state.
            daily_log = outputs / "logs" / "aisemi_daily.out"
            if not isinstance(source, dict) or (source and source.get("kind") != "launchd"):
                daily_log = None
            candidates.append((at, "daily", state, daily_log))
    if web.get("running"):
        chosen = (web_at or now, "web", web, web_log)
    else:
        chosen = max(candidates, key=lambda x: x[0]) if candidates else (now, None, {}, None)
    _, source, state, path = chosen
    active = bool(web.get("running") or (include_daily and occupied is True))
    task_status = state.get("status") or (
        "RUNNING" if web.get("running") else "FINISHED" if state.get("returncode") == 0 else
        "FAILED" if state.get("returncode") is not None else "IDLE")
    if active:
        if source != "web" or not web.get("running"):
            source = "daily" if source == "daily" and task_status == "RUNNING" else "external"
        task_status = "RUNNING"
    elif task_status == "RUNNING":
        task_status = "INTERRUPTED"
    last_attempt = {"source": chosen[1], "run_id": state.get("run_id"),
                    "started_at": state.get("started_at"), "finished_at": state.get("finished_at")}
    unknown_external = source == "external"
    if unknown_external:
        state = {}
        path = None
    tail, updated, log_error = "", None, None
    if path is not None:
        try:
            tail = redact_sensitive_text(path.read_text(errors="replace"))[-4000:]
            updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            log_error = "日志文件无法读取"
    elif source == "daily":
        log_error = "本次从命令行运行，未保存到自动任务日志"
    heartbeat = _object(outputs / "daily" / "heartbeat.json") if include_daily else {}
    checked = _time(heartbeat.get("checked_at"))
    age = (now - checked).total_seconds() if checked else None
    heartbeat_status = "missing" if age is None else "invalid" if age < 0 else "fresh" if age <= HEARTBEAT_MAX_SECONDS else "stale"
    rc = state.get("returncode")
    if source == "daily":
        rc = 0 if task_status == "TARGET_READY" else 1 if task_status in ("FAILED", "TARGET_BLOCKED") else None
    return {"running": active, "running_source": ("web" if source == "web" else "scheduled_or_cli") if active else None,
            "task_source": source, "task_status": task_status, "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"), "returncode": rc,
            "stages": ["data", "analysis", "portfolio", "persist"] if source == "daily" else state.get("stages", []),
            "attempt_run_id": state.get("run_id"), "attempt_session": state.get("session"),
            "last_attempt": last_attempt, "log_current_attempt": not unknown_external,
            "log_tail": tail, "log_source": "daily" if path and path.name == "aisemi_daily.out" else "web" if path else None,
            "log_updated_at": updated, "log_error": log_error,
            "log_label": ("当前外部任务日志尚未匹配" if unknown_external else
                          "自动日频任务日志" if path and path.name == "aisemi_daily.out" else "手动任务日志" if path else "无已保存日志"),
            "daily_clock": clock, "daily_state": states.get(clock["session"]),
            "last_daily_state": states[max(states)] if states else None,
            "next_preparation_at": clock["next_preparation_at"] if include_daily else None,
            "heartbeat_status": heartbeat_status, "heartbeat_checked_at": heartbeat.get("checked_at")}
