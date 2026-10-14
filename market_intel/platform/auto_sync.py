"""Background ingestion of fully published research runs, never order execution.

The HTTP service outlives daily pipeline processes. This producer watches the
published identity and freezes each new archive into the platform repository.
HTTP GET handlers only read its in-memory status and the existing repository.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import threading
from typing import Callable

from ai_semi_intel.runs import inspect_completed_archive
from ai_semi_intel.utils.redaction import redact_sensitive_text


class _Waiting(RuntimeError):
    pass


class CurrentRunSynchronizer:
    """One retrying producer per server; only ``tick`` may mutate the catalog.

    Failed identities are never cached as successful. On restart the existing
    catalog and repository retain their idempotent registration checks. This
    worker cannot create sessions, orders, fills, or a research pipeline run.
    """

    def __init__(self, service, *, interval: float = 15.0,
                 clock: Callable[[], datetime] | None = None):
        if isinstance(interval, bool) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("sync interval must be a positive finite number")
        self.service = service
        self.latest = Path(service.latest_dir)
        self.interval = float(interval)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._successful_identity: str | None = None
        self._state = {
            "status": "NOT_STARTED", "enabled": True,
            "interval_seconds": self.interval,
            "checked_at": None, "last_attempt_at": None, "last_success_at": None,
            "observed_run_id": None, "synced_run_id": None,
            "last_error": None, "error_type": None, "consecutive_failures": 0,
            "sync_attempts": 0, "successful_syncs": 0,
            "live_trading_supported": False,
        }

    def _update(self, **values):
        with self._state_lock:
            self._state.update(values)

    def status(self) -> dict:
        """Read-only snapshot suitable for a viewer API; does not touch disk."""
        with self._state_lock:
            return dict(self._state, worker_running=bool(self._thread and self._thread.is_alive()))

    @contextmanager
    def _pipeline_idle(self):
        # A shared lock closes the check/use race with the pipeline's exclusive
        # lock. Price paths in legacy manifests can otherwise change mid-freeze.
        lock_path = self.latest.parent / ".pipeline.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise _Waiting("pipeline is running; synchronization deferred") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _published(self) -> tuple[Path, dict, str]:
        if (self.latest / "run_publishing.json").exists():
            raise _Waiting("run publication is incomplete; synchronization deferred")
        run_bytes = (self.latest / "run.json").read_bytes()
        run = json.loads(run_bytes)
        rid = run.get("run_id") if isinstance(run, dict) else None
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", rid):
            raise ValueError("published run identity is invalid")
        self._update(observed_run_id=rid)
        archive = self.latest.parent / "history" / "runs" / rid
        verified = inspect_completed_archive(archive)
        if verified is None or verified != run:
            raise ValueError("published run requires a matching verified archive")
        snapshot = json.loads((archive / "snapshot.json").read_bytes())
        if not isinstance(snapshot, dict) or any(
            not run.get(key) or snapshot.get(key) != run[key]
            for key in ("run_id", "generated_at", "asof_session")
        ):
            raise ValueError("published snapshot identity does not match its run")
        identity = hashlib.sha256(
            run_bytes + b"\0" + (archive / "archive_manifest.json").read_bytes()
        ).hexdigest()
        return archive, run, identity

    def tick(self) -> dict:
        """Perform a single producer check; safe to retry after any failure."""
        if not self._tick_lock.acquire(blocking=False):
            return self.status()
        try:
            now = self._clock().astimezone(timezone.utc).isoformat()
            self._update(checked_at=now)
            try:
                with self._pipeline_idle():
                    archive, run, identity = self._published()
                    if identity == self._successful_identity:
                        self._update(status="SYNCED", last_error=None, error_type=None,
                                     consecutive_failures=0)
                        return self.status()
                    self._update(status="SYNCING", last_attempt_at=now,
                                 sync_attempts=self.status()["sync_attempts"] + 1)
                    result = self.service.sync_current_run(source_dir=archive)
                    if result.run.get("run_id") != run["run_id"]:
                        raise ValueError("platform synchronized a different run identity")
                    # Verify the publication still selects this run before
                    # advertising success (also catches writers ignoring locks).
                    _, after, after_identity = self._published()
                    if after != run or after_identity != identity:
                        raise ValueError("published run changed during synchronization")
                    self._successful_identity = identity
                    self._update(status="SYNCED", synced_run_id=run["run_id"],
                                 last_success_at=self._clock().astimezone(timezone.utc).isoformat(),
                                 last_error=None, error_type=None, consecutive_failures=0,
                                 successful_syncs=self.status()["successful_syncs"] + 1)
                    logging.getLogger(__name__).info("platform synchronized published run %s", run["run_id"])
            except _Waiting as exc:
                self._update(status="WAITING", last_error=str(exc), error_type=None)
            except Exception as exc:
                reason = redact_sensitive_text(str(exc))[:1000]
                self._update(status="FAILED", last_error=reason, error_type=type(exc).__name__,
                             consecutive_failures=self.status()["consecutive_failures"] + 1)
                logging.getLogger(__name__).warning("platform synchronization failed (%s): %s",
                                                    type(exc).__name__, reason)
            return self.status()
        finally:
            self._tick_lock.release()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.tick()  # Initial ingestion completes before the server accepts GETs.
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mi-platform-sync")
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(self.interval):
            self.tick()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        if not self._thread or not self._thread.is_alive():
            self._update(status="STOPPED")
