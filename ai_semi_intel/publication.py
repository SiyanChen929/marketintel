"""Publish complete immutable targets to the legacy latest-file interface.

Readers should pin the archive named by run.json. A publication marker keeps
execution blocked if mirroring is interrupted; calling publish again repairs it.
This is process-crash recovery, not a multi-file database transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile


def pipeline_running(outputs: Path) -> bool | None:
    """Read-only lock probe shared by scheduled, command-line and web runs."""
    path = Path(outputs) / ".pipeline.lock"
    if not path.exists():
        return False
    try:
        with path.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False
    except OSError:
        return None


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def publish(archive: Path, latest: Path) -> dict:
    archive, latest = Path(archive), Path(latest)
    from .runs import inspect_completed_archive
    manifest_bytes = (archive / "archive_manifest.json").read_bytes()
    verified = inspect_completed_archive(archive)
    if verified is None:
        raise ValueError("publication requires a verified complete archive manifest")
    if (archive / "archive_manifest.json").read_bytes() != manifest_bytes:
        raise ValueError("publication manifest changed during archive verification")
    manifest = json.loads(manifest_bytes)
    payloads = {}
    for name, expected_hash in manifest["files"].items():
        if not isinstance(name, str) or Path(name).name != name or name.startswith("."):
            raise ValueError("invalid publication artifact name")
        content = (archive / name).read_bytes()
        if sha256(content).hexdigest() != expected_hash:
            raise ValueError("publication artifact changed after archive verification")
        payloads[name] = content
    run = json.loads(payloads["run.json"])
    snapshot = json.loads(payloads["snapshot.json"])
    if not isinstance(run, dict) or not isinstance(snapshot, dict):
        raise ValueError("publication requires complete run and snapshot objects")
    for key in ("run_id", "generated_at", "asof_session"):
        if not run.get(key) or run[key] != snapshot.get(key):
            raise ValueError("publication identity mismatch: " + key)
    if archive.name != run["run_id"]:
        raise ValueError("publication archive identity mismatch")
    if run != verified:
        raise ValueError("publication run changed after archive verification")
    marker = latest / "run_publishing.json"
    atomic_bytes(marker, json.dumps({"run_id": run["run_id"], "started_at": datetime.now(timezone.utc).isoformat()}).encode())
    # File replacement is atomic. The public identity is switched last.
    for name, content in sorted(payloads.items()):
        if name != "run.json":
            atomic_bytes(latest / name, content)
    atomic_bytes(latest / "archive_manifest.json", manifest_bytes)
    atomic_bytes(latest / "run.json", payloads["run.json"])
    marker.unlink()
    return {"run_id": run["run_id"], "status": "published"}
