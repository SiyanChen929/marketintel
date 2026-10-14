from datetime import datetime, timezone
import fcntl
import json
import time

from fastapi.testclient import TestClient
import pytest

from ai_semi_intel import publication, runs
from market_intel.entities import Company, EntityIndex
from market_intel.platform.auto_sync import CurrentRunSynchronizer
from market_intel.platform.service import PlatformBlocked, PlatformInputError, PlatformService
from market_intel.server import create_app
from market_intel.store import Store


def publish_fixture(root, rid="fixture-001"):
    """Synthetic accounting fixture; real immutable archive and publication."""
    stage = root / "stage"
    stage.mkdir(exist_ok=True)
    generated = "2026-09-22T11:18:00+00:00"
    run = {
        "run_id": rid, "portfolio_id": "fixture-book", "asof_session": "2026-09-22",
        "generated_at": generated, "status": "pending", "code_hash": "fixture",
        "validation_status": "NOT VALIDATED", "actionable": False,
        "not_actionable_reasons": ["synthetic fixture"],
        "portfolio": {"construction": "risk_parity", "cash": .1, "invested": .9,
                      "reconciles": True,
                      "positions": [{"ticker": "AAA", "target_executable_weight": .9}]},
    }
    snapshot = {
        "run_id": rid, "portfolio_id": "fixture-book", "asof_session": "2026-09-22",
        "generated_at": generated, "date": "2026-09-22", "top": ["AAA"],
        "composite": {"AAA": 1.0}, "validation_status": "NOT VALIDATED",
    }
    for name, value in (("run.json", run), ("snapshot.json", snapshot)):
        (stage / name).write_text(json.dumps(value))
    archive = runs.register(run, stage, root / "history")
    publication.publish(archive, root / "latest")
    return archive


@pytest.fixture
def service(tmp_path):
    return PlatformService(
        latest_dir=tmp_path / "latest", state_dir=tmp_path / "platform",
        require_versioned_lineage=False,
        clock=lambda: datetime(2026, 9, 23, tzinfo=timezone.utc),
    )


def physical(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_new_published_run_syncs_without_restart_or_order_execution(tmp_path, service, monkeypatch):
    old = publish_fixture(tmp_path)
    old_bytes = physical(old)
    worker = CurrentRunSynchronizer(service)
    assert worker.tick()["synced_run_id"] == "fixture-001"
    old_record = service.repository.get_run("fixture-001")
    monkeypatch.setattr(service, "create_paper_session", lambda *_: pytest.fail("created a session"))
    monkeypatch.setattr(service, "step_paper", lambda *_: pytest.fail("executed paper orders"))
    publish_fixture(tmp_path, "fixture-002")
    state = worker.tick()
    assert state["status"] == "SYNCED"
    assert state["synced_run_id"] == "fixture-002"
    assert {r["run_id"] for r in service.repository.runs()} == {"fixture-001", "fixture-002"}
    assert service.repository.get_run("fixture-001") == old_record
    assert physical(old) == old_bytes
    assert not list(service.paper_dir.glob("*.jsonl"))


def test_unchanged_successful_identity_performs_no_database_writes(tmp_path, service):
    publish_fixture(tmp_path)
    worker = CurrentRunSynchronizer(service)
    worker.tick()
    before = physical(service.state_dir)
    for _ in range(3):
        assert worker.tick()["status"] == "SYNCED"
    assert worker.status()["sync_attempts"] == 1
    assert physical(service.state_dir) == before


def test_failed_sync_is_observable_redacted_and_retried(tmp_path, service, monkeypatch):
    publish_fixture(tmp_path)
    worker = CurrentRunSynchronizer(service)
    worker.tick()
    publish_fixture(tmp_path, "fixture-002")
    original = service.sync_current_run
    attempts = []

    def flaky(**kwargs):
        attempts.append(kwargs["source_dir"])
        if len(attempts) == 1:
            raise PlatformBlocked("fixture api_key=private-value unavailable")
        return original(**kwargs)

    monkeypatch.setattr(service, "sync_current_run", flaky)
    failed = worker.tick()
    assert failed["status"] == "FAILED"
    assert failed["synced_run_id"] == "fixture-001"
    assert failed["observed_run_id"] == "fixture-002"
    assert "private-value" not in failed["last_error"]
    assert failed["consecutive_failures"] == 1
    assert worker.tick()["synced_run_id"] == "fixture-002"
    assert len(attempts) == 2
    assert worker.status()["consecutive_failures"] == 0


def test_pipeline_and_incomplete_publication_are_waiting_gates(tmp_path, service):
    publish_fixture(tmp_path)
    worker = CurrentRunSynchronizer(service)
    with (tmp_path / ".pipeline.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.tick()["status"] == "WAITING"
        assert service.repository.runs() == []
    (tmp_path / "latest/run_publishing.json").write_text("malformed marker still blocks")
    assert worker.tick()["status"] == "WAITING"
    assert service.repository.runs() == []
    (tmp_path / "latest/run_publishing.json").unlink()
    assert worker.tick()["status"] == "SYNCED"


def test_background_freeze_holds_lock_against_pipeline_race(tmp_path, service, monkeypatch):
    publish_fixture(tmp_path)
    original = service.sync_current_run

    def inspect_lock(**kwargs):
        with (tmp_path / ".pipeline.lock").open("a+") as handle:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return original(**kwargs)

    monkeypatch.setattr(service, "sync_current_run", inspect_lock)
    assert CurrentRunSynchronizer(service).tick()["status"] == "SYNCED"


def test_tampered_archive_blocks_without_changing_existing_history(tmp_path, service):
    publish_fixture(tmp_path)
    worker = CurrentRunSynchronizer(service)
    worker.tick()
    archive = publish_fixture(tmp_path, "fixture-002")
    (archive / "snapshot.json").write_text("{}")
    before = physical(service.state_dir)
    assert worker.tick()["status"] == "FAILED"
    assert worker.status()["synced_run_id"] == "fixture-001"
    assert physical(service.state_dir) == before


def test_server_lifecycle_updates_without_get_and_stops_worker(tmp_path, service):
    publish_fixture(tmp_path)
    index = EntityIndex([Company(ticker="AAA", name="Fixture", segment="Fixture", aliases=[], weight=.9, cik=1)])
    app = create_app(store=Store(tmp_path / "events.sqlite"), index=index,
                     platform_service=service, platform_sync_interval=.02)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        worker = app.state.platform_sync
        assert worker.status()["synced_run_id"] == "fixture-001"
        publish_fixture(tmp_path, "fixture-002")
        deadline = time.monotonic() + 3
        while worker.status()["synced_run_id"] != "fixture-002" and time.monotonic() < deadline:
            time.sleep(.02)
        assert worker.status()["synced_run_id"] == "fixture-002"
        assert len(client.get("/api/v2/platform/runs").json()["items"]) == 2
    assert worker.status()["status"] == "STOPPED"
    assert worker.status()["worker_running"] is False


def test_versioned_ingestor_receives_archive_and_keeps_its_validation(tmp_path):
    archive = publish_fixture(tmp_path)
    seen = []

    def ingestor(**kwargs):
        seen.append(kwargs["latest_dir"])
        raise ValueError("fixture dataset checksum invalid")

    svc = PlatformService(latest_dir=tmp_path / "latest", state_dir=tmp_path / "platform",
                          dataset_ingestor=ingestor)
    state = CurrentRunSynchronizer(svc).tick()
    assert seen == [archive]
    assert state["status"] == "FAILED"
    assert "checksum invalid" in state["last_error"]
    assert svc.repository.runs() == []


def test_archive_override_does_not_bypass_injected_loaders(tmp_path):
    svc = PlatformService(latest_dir=tmp_path / "latest", state_dir=tmp_path / "platform",
                          snapshot_loader=lambda: {}, run_loader=lambda: {}, require_versioned_lineage=False)
    with pytest.raises(PlatformInputError, match="cannot override"):
        svc.sync_current_run(source_dir=tmp_path)
