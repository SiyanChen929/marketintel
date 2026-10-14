"""Exchange-session daily target preparation. No broker calls or invented fills."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import uuid

from .config.settings import OUTPUTS
from .utils.io import DataUnavailable
from .utils.redaction import redact_sensitive_text
from . import trading_policy

HEARTBEAT_MAX_SECONDS = 900
RETRYABLE = {"FAILED", "TARGET_BLOCKED", "INTERRUPTED"}


def _log_event(event: str, state: dict):
    """Flush a bounded UTC event to the scheduler's actual stdout log."""
    value = {"event": event, "observed_at": datetime.now(timezone.utc).isoformat()}
    for field in ("session", "attempt_id", "status", "run_id", "construction_count", "error_type"):
        if field in state:
            value[field] = state[field]
    print(redact_sensitive_text(json.dumps(value, ensure_ascii=False)), flush=True)


def _log_source() -> dict:
    if os.environ.get("XPC_SERVICE_NAME") == "com.siyanchen.aisemi.daily":
        return {"kind": "launchd", "label": "com.siyanchen.aisemi.daily",
                "path": str(OUTPUTS / "logs" / "aisemi_daily.out")}
    return {"kind": "daily_cli", "stream": "stdout"}


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _archive_state(path: Path) -> str:
    """Preserve exact prior bytes before a recovery or explicit retry."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    archive = path.parent / "attempts" / path.stem / (digest + ".json")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        with archive.open("xb") as f:
            f.write(raw)
    elif archive.read_bytes() != raw:
        raise DataUnavailable("daily attempt archive integrity mismatch")
    return str(archive.relative_to(path.parent))


def _running(state_dir: Path) -> bool:
    """Probe an existing advisory lock without creating or changing files."""
    lock_path = state_dir / "prepare.lock"
    if not lock_path.exists():
        return False
    with lock_path.open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
    return False


def _archived_runs(history: Path, warnings: list[str]) -> list[dict]:
    from .runs import inspect_completed_archive
    try:
        index = _read(history / "runs" / "index.json") or []
        if not isinstance(index, list):
            raise ValueError("invalid index")
    except (OSError, ValueError):
        warnings.append("run history index is unreadable")
        return []
    result = []
    for item in index:
        rid = item.get("run_id") if isinstance(item, dict) else None
        if not isinstance(rid, str) or not rid or Path(rid).name != rid or rid in (".", ".."):
            warnings.append("run history index contains an invalid identity")
            continue
        try:
            folder = history / "runs" / rid
            run = (inspect_completed_archive(folder) if (folder / "archive_manifest.json").exists()
                   else _read(folder / "run.json"))
            if not isinstance(run, dict) or run.get("run_id") != rid:
                raise ValueError("archive identity mismatch")
            result.append(run)
        except (OSError, ValueError, DataUnavailable):
            warnings.append("run archive unavailable: " + rid)
    # A process can finish the directory commit and stop before its index write.
    # Only new archives with a complete, verified file manifest may be observed
    # here; arbitrary old folders never become registered evidence implicitly.
    indexed_ids = {item.get("run_id") for item in index if isinstance(item, dict)}
    for folder in sorted((history / "runs").glob("*")):
        if not folder.is_dir() or folder.name.startswith(".") or folder.name in indexed_ids:
            continue
        try:
            run = inspect_completed_archive(folder)
            if run is not None:
                result.append(run)
                warnings.append("completed archive awaits index recovery: " + folder.name)
        except (OSError, ValueError, DataUnavailable):
            warnings.append("unindexed archive integrity is unverified: " + folder.name)
    return result


def status(now=None, *, state_dir=None, history=None, latest=None):
    """Read-only operational evidence. Missing sessions are never backfilled."""
    now = trading_policy.aware(now or datetime.now(timezone.utc))
    state_dir = Path(state_dir) if state_dir is not None else OUTPUTS / "daily"
    history = Path(history) if history is not None else state_dir.parent / "history"
    latest = Path(latest) if latest is not None else state_dir.parent / "latest"
    window = trading_policy.preparation_window(now)
    warnings, states = [], {}
    for path in sorted(state_dir.glob("????-??-??.json")):
        try:
            datetime.strptime(path.stem, "%Y-%m-%d")
        except ValueError:
            warnings.append("daily state has invalid date: " + path.name)
            continue
        try:
            value = _read(path)
            if not isinstance(value, dict) or value.get("session") != path.stem:
                raise ValueError("daily state identity mismatch")
            states[path.stem] = value
        except (OSError, ValueError):
            warnings.append("daily state unreadable: " + path.name)
            states[path.stem] = {"session": path.stem, "status": "INVALID"}
    try:
        heartbeat = _read(state_dir / "heartbeat.json")
        if heartbeat is None:
            heartbeat_status = "missing"
        else:
            age = (now - trading_policy.aware(heartbeat["checked_at"])).total_seconds()
            heartbeat_status = "invalid" if age < 0 else "fresh" if age <= HEARTBEAT_MAX_SECONDS else "stale"
    except (OSError, KeyError, TypeError, ValueError, DataUnavailable):
        heartbeat, heartbeat_status = None, "invalid"
    try:
        latest_run = _read(latest / "run.json")
        if latest_run is not None and not isinstance(latest_run, dict):
            raise ValueError("invalid latest run")
    except (OSError, ValueError):
        latest_run = None
        warnings.append("latest run is unreadable")
    current = trading_policy.current_status(latest_run, now)
    archived = _archived_runs(history, warnings)
    coverage = min(states, default=None)
    runs_by_session = {}
    for run in archived:
        p = run.get("rebalance_policy") or {}
        if p.get("version") != trading_policy.CONFIG.portfolio.execution_policy_version:
            continue
        session = p.get("execution_session")
        if session:
            runs_by_session.setdefault(session, []).append(run)
    missed, failed, incomplete = [], [], []
    if coverage and coverage <= str(now.tz_convert(trading_policy.ET).date()):
        cal = trading_policy.calendar()
        end = now.tz_convert(trading_policy.ET).date()
        for session in cal.sessions_in_range(coverage, str(end)):
            day = str(session.date())
            if now < cal.session_open(session) - timedelta(minutes=15):
                continue
            state = states.get(day)
            completed = [r for r in runs_by_session.get(day, [])
                         if (r.get("rebalance_policy") or {}).get("ready") is True]
            if state is None and not completed:
                missed.append(day)
            elif state and state.get("status") in ("FAILED", "TARGET_BLOCKED"):
                failed.append(day)
            elif not completed:
                incomplete.append(day)
    try:
        running = _running(state_dir)
    except OSError:
        running = False
        warnings.append("scheduler lock status is unreadable")
    today_state = states.get(window["session"])
    if today_state and today_state.get("status") == "RUNNING" and not running:
        today_state = dict(today_state, observed_status="INTERRUPTED", recovery_required=True)
    if heartbeat_status != "fresh":
        warnings.append("scheduler heartbeat " + heartbeat_status + "; local host wake and scheduling are not confirmed")
    if missed:
        warnings.append("missing daily target sessions; no retrospective targets have been created")
    return {"schema": "stock_select.daily_status.v1", "checked_at": now.isoformat(),
            "preparation": window, "next_preparation": window["next_preparation_at"],
            "today_state": today_state, "last_heartbeat": heartbeat,
            "heartbeat_status": heartbeat_status, "missed_sessions": missed,
            "failed_sessions": failed, "incomplete_sessions": incomplete,
            "coverage_start": coverage, "latest_run_id": (latest_run or {}).get("run_id"),
            "current_target_status": current, "actionable_now": current["actionable_now"],
            "running": running, "warnings": warnings, "live_trading": False,
            "scheduler": {"kind": "local_launch_agent", "label": "com.siyanchen.aisemi.daily",
                          "poll_interval_seconds": 300, "requires_host_awake": True,
                          "missed_window_policy": "skip_and_report; never backfill"}}


def _apply_result(state: dict, run: dict, construction_count: int):
    from .portfolio.constructions import REGISTRY
    if type(run.get("actionable")) is not bool:
        raise DataUnavailable("daily result actionability must be an explicit boolean")
    if type(construction_count) is not int or construction_count != len(REGISTRY):
        raise DataUnavailable("daily result must contain every registered construction")
    p = run.get("rebalance_policy") or {}
    if p.get("execution_session") != state["session"]:
        raise DataUnavailable("preparation missed its execution session; no late or retrospective trade")
    if state.get("attempt_id") and run.get("daily_attempt_id") != state["attempt_id"]:
        raise DataUnavailable("daily result does not match its source attempt")
    clock = trading_policy.current_status(run, run.get("generated_at"))
    if clock["status"] == "INVALID_POLICY" or (run["actionable"] and clock["status"] == "BLOCKED"):
        raise DataUnavailable("daily result has an inconsistent execution clock")
    state.update(status="TARGET_READY" if run.get("actionable") is True else "TARGET_BLOCKED",
                 run_id=run["run_id"], rebalance_policy=p, construction_count=construction_count,
                 reasons=run.get("not_actionable_reasons", []), execution_status="NO_FILLS_RECORDED")


def _restore_publication(folder: Path, state_dir: Path, run: dict) -> str:
    from . import publication, runs
    latest = state_dir.parent / "latest"
    with (state_dir.parent / ".pipeline.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DataUnavailable("another pipeline is publishing; recovery must wait") from exc
        current = _read(latest / "run.json")
        if current is not None and not isinstance(current, dict):
            raise DataUnavailable("latest run identity is unreadable; publication requires review")
        if current and trading_policy.aware(current["generated_at"]) > trading_policy.aware(run["generated_at"]):
            return "preserved_newer_latest"
        marker = _read(latest / "run_publishing.json")
        if marker is not None and not isinstance(marker, dict):
            raise DataUnavailable("publication marker is invalid; review required")
        if marker and marker.get("run_id") != run["run_id"]:
            raise DataUnavailable("another target publication is incomplete; review it before recovery")
        changed = not current or current.get("run_id") != run["run_id"] or marker is not None
        if changed:
            publication.publish(folder, latest)
        blocked = _read(latest / "run_blocked.json")
        if blocked and trading_policy.aware(blocked["at"]) <= trading_policy.aware(run["generated_at"]):
            runs.clear_blocked(latest)
        return "restored" if changed else "already_published"


def _recover(path: Path, state: dict, history: Path, now) -> dict:
    """Recover only a registered run bearing this exact attempt identity."""
    warnings = []
    attempt = state.get("attempt_id")
    matches = [r for r in _archived_runs(history, warnings)
               if attempt and r.get("daily_attempt_id") == attempt
               and (r.get("rebalance_policy") or {}).get("execution_session") == state["session"]]
    indexed_attempts = [r for r in (_read(history / "runs" / "index.json") or [])
                        if isinstance(r, dict) and attempt and r.get("daily_attempt_id") == attempt]
    if not matches and indexed_attempts:
        recovered = dict(state, status="INTERRUPTED", recovered_at=now.isoformat(),
                         prior_state_archive=_archive_state(path), recovery="registered_archive_requires_review",
                         reason="This attempt has registered target evidence that failed integrity checks; research will not repeat.")
        _write(path, recovered)
        return recovered
    if not matches and state.get("status") != "RUNNING":
        return state
    prior_archive = _archive_state(path)
    recovered = dict(state, prior_state_archive=prior_archive, recovered_at=now.isoformat())
    if len(matches) == 1:
        from .portfolio import target_archive
        target_file = history / "runs" / matches[0]["run_id"] / "construction_targets.json"
        try:
            targets = _read(target_file) or {}
            snap = _read(target_file.parent / "snapshot.json")
            count = target_archive.validate(targets, matches[0], snap)
            _apply_result(recovered, matches[0], count)
            index = _read(history / "runs" / "index.json") or []
            if not any(item.get("run_id") == matches[0]["run_id"] for item in index):
                from .runs import recover_registration
                recover_registration(matches[0]["run_id"], history)
            recovered.update(recovery="matched immutable archive; no research repeated", finished_at=now.isoformat())
        except (OSError, KeyError, TypeError, ValueError, DataUnavailable):
            recovered.update(status="INTERRUPTED", registered_run_id=matches[0]["run_id"],
                             recovery="registered_archive_requires_review",
                             reason="Matching run has incomplete or inconsistent target evidence; explicit review required.")
        if recovered.get("status") in ("TARGET_READY", "TARGET_BLOCKED"):
            try:
                recovered["publication_recovery"] = _restore_publication(target_file.parent, path.parent, matches[0])
            except (OSError, KeyError, TypeError, ValueError, DataUnavailable):
                recovered.update(status="INTERRUPTED", recovery="publication_retry_required",
                                 reason="Completed target is preserved; publication recovery requires a retry or review.")
    else:
        recovered.update(status="INTERRUPTED", reason="Prior process ended without a uniquely matched completed archive; explicit retry required.")
    _write(path, recovered)
    return recovered


def run_once(now=None, *, state_dir=None, history=None, pipeline_runner=None, check=False,
             retry=False, retry_reason=None, record_poll=False, log_events=False):
    now = trading_policy.aware(now or datetime.now(timezone.utc))
    window = trading_policy.preparation_window(now)
    state_dir = Path(state_dir) if state_dir is not None else OUTPUTS / "daily"
    history = Path(history) if history is not None else state_dir.parent / "history"
    if check:
        return dict(window, status="CHECK")
    if record_poll:
        _write(state_dir / "heartbeat.json", {"checked_at": now.isoformat(), "pid": os.getpid(),
               "due": window["due"], "session": window["session"], "reason": window["reason"],
               "next_preparation_at": window["next_preparation_at"], "live_trading": False})
    if not window["due"]:
        if retry:
            raise DataUnavailable("retry is only permitted inside today's preparation window")
        return dict(window, status="WAITING")
    if retry and not (isinstance(retry_reason, str) and retry_reason.strip()):
        raise DataUnavailable("explicit retry requires an audit reason")
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / (window["session"] + ".json")
    with (state_dir / "prepare.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(window, status="ALREADY_RUNNING")
        prior = _read(path)
        if prior is not None and (not isinstance(prior, dict) or prior.get("session") != window["session"]):
            raise DataUnavailable("daily state is invalid; inspect it before retrying")
        if prior and (prior.get("status") == "RUNNING" or (retry and prior.get("status") in ("FAILED", "INTERRUPTED"))):
            prior = _recover(path, prior, history, now)
            if prior.get("status") in ("TARGET_READY", "TARGET_BLOCKED"):
                return dict(window, status="ALREADY_ATTEMPTED", prior=prior)
        if prior is not None and not retry:
            return dict(window, status="ALREADY_ATTEMPTED", prior=prior)
        if retry and (prior is None or prior.get("status") not in RETRYABLE):
            raise DataUnavailable("explicit retry requires a failed, blocked, or interrupted attempt")
        if retry and prior.get("recovery") == "registered_archive_requires_review":
            raise DataUnavailable("registered target archive requires review; research will not be repeated")
        if retry and prior.get("recovery") == "publication_retry_required":
            raise DataUnavailable("completed target publication is unresolved; research will not be repeated")
        archived = _archive_state(path) if prior else None
        state = dict(window, status="RUNNING", started_at=now.isoformat(), live_trading=False,
                     attempt_id=uuid.uuid4().hex, attempt_number=(prior or {}).get("attempt_number", 1 if prior else 0) + 1,
                     allow_llm_refresh=not retry, retry_reason=retry_reason.strip() if retry else None,
                     prior_state_archive=archived)
        if log_events:
            state["log_source"] = _log_source()
        _write(path, state)
        if log_events:
            _log_event("DAILY_ATTEMPT_STARTED", state)
        try:
            if pipeline_runner is None:
                from .pipeline import run
                pipeline_runner = run
            result = pipeline_runner(stages=("data", "analysis", "portfolio", "persist"), force=True,
                                     allow_llm_refresh=not retry, daily_attempt_id=state["attempt_id"])
            from .portfolio import target_archive
            snap = result.get("snapshot")
            if snap is None and result.get("run_dir"):
                snap = _read(Path(result["run_dir"]) / "snapshot.json")
            count = target_archive.validate(result.get("construction_targets"), result["run"], snap)
            _apply_result(state, result["run"], count)
        except Exception as exc:
            state.update(status="FAILED", error_type=type(exc).__name__,
                         reason="Daily target preparation failed; inspect the pipeline log before an explicit retry.")
            raise
        finally:
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
            _write(path, state)
            if log_events:
                _log_event("DAILY_ATTEMPT_FINISHED", state)
        return state


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="read-only exchange-clock check; no data or LLM calls")
    ap.add_argument("--status", action="store_true", help="read-only schedule, missing-session and target-clock report")
    ap.add_argument("--retry", action="store_true", help="explicitly retry a failed attempt; reuses stored LLM assessments")
    ap.add_argument("--reason", help="required audit reason for --retry")
    args = ap.parse_args(argv)
    if args.status:
        if args.retry:
            ap.error("--status cannot be combined with --retry")
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.retry and (args.check or not args.reason or not args.reason.strip()):
        ap.error("--retry requires --reason and cannot be combined with --check")
    try:
        result = run_once(check=args.check, retry=args.retry, retry_reason=args.reason,
                          record_poll=not args.check, log_events=not args.check)
    except Exception as exc:
        # Vendor exception messages can contain credential-bearing URLs. The
        # attempt state and pipeline stage log retain context without echoing
        # an uncontrolled exception body or traceback into launchd's log.
        _log_event("DAILY_COMMAND_FAILED", {"status": "FAILED", "error_type": type(exc).__name__})
        return 1
    if args.check or result["status"] in ("TARGET_READY", "TARGET_BLOCKED"):
        print(redact_sensitive_text(json.dumps(result, ensure_ascii=False, indent=2)), flush=True)
    failed = result["status"] == "TARGET_BLOCKED" or (
        result["status"] == "ALREADY_ATTEMPTED" and
        result.get("prior", {}).get("status") in ("FAILED", "TARGET_BLOCKED", "RUNNING", "INTERRUPTED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
