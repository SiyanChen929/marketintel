import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from market_intel.platform.repository import PlatformRepository


UTC = timezone.utc
NOW = datetime(2026, 9, 4, 14, tzinfo=UTC)


def manifest():
    return {"strategy_id": "stock_select", "name": "Stock Select", "version": "1",
            "asset_classes": ["equity"], "clocks": ["daily_close"]}


def context():
    return {"run_id": "run-1", "workspace_id": "default", "strategy_id": "stock_select",
            "portfolio_id": "paper", "as_of": NOW.isoformat(), "dataset_versions": {"prices": "d1"},
            "model_version": "m1", "seed": 7}


def _physical_file(path):
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns, stat.st_size


def _sidecars(root):
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.name.endswith(("-wal", "-shm", "-journal"))
    )


def test_strategy_and_run_are_immutable_and_persistent(tmp_path):
    path = tmp_path / "platform.sqlite"
    repo = PlatformRepository(path)
    repo.register_strategy(manifest(), at=NOW)
    r = repo.register_run(context(), generated_at=NOW, validation_status="NOT VALIDATED")
    assert r["state"] == "created" and len(r["context_hash"]) == 64
    assert PlatformRepository(path).get_run("run-1", require_complete=False)["context"] == r["context"]
    assert r["strategy_version"] == "1"
    repo.register_strategy(manifest(), at=NOW)  # exact retries are idempotent
    repo.register_run(context(), generated_at=NOW)
    with pytest.raises(ValueError, match="different manifest"):
        repo.register_strategy({**manifest(), "name": "Changed"}, at=NOW)
    repo.register_strategy({**manifest(), "version": "2"}, at=NOW)
    assert [m["version"] for m in repo.strategies()] == ["1", "2"]
    with pytest.raises(ValueError, match="different immutable context"):
        repo.register_run({**context(), "seed": 9}, generated_at=NOW)


def test_run_must_pin_plugin_version_when_registry_is_ambiguous(tmp_path):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    repo.register_strategy(manifest(), at=NOW)
    repo.register_strategy({**manifest(), "version": "2"}, at=NOW)
    with pytest.raises(ValueError, match="strategy_version is required"):
        repo.register_run({**context(), "run_id": "ambiguous"}, generated_at=NOW)
    run = repo.register_run(
        {**context(), "run_id": "v2-run", "strategy_version": "2"}, generated_at=NOW
    )
    assert run["strategy_version"] == "2" and run["context"]["strategy_version"] == "2"


def test_strategy_read_recomputes_manifest_hash(tmp_path):
    db = tmp_path / "platform.sqlite"
    repo = PlatformRepository(db)
    repo.register_strategy(manifest(), at=NOW)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE strategies SET manifest=? WHERE strategy_id='stock_select' AND version='1'",
            ('{"name":"Forged LIVE","strategy_id":"stock_select","version":"1"}',),
        )
    with pytest.raises(ValueError, match="manifest hash"):
        repo.strategies()
    with pytest.raises(ValueError, match="manifest hash"):
        repo.register_run(context(), generated_at=NOW)


def test_run_state_is_append_only_and_terminal(tmp_path):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    repo.register_strategy(manifest(), at=NOW)
    repo.register_run(context(), generated_at=NOW)
    repo.append_run_event("run-1", "running", at=NOW + timedelta(seconds=1))
    done = repo.append_run_event("run-1", "completed", at=NOW + timedelta(seconds=2), detail={"rows": 15})
    assert [e["state"] for e in done["events"]] == ["created", "running", "completed"]
    assert done["events"][-1]["detail"] == {"rows": 15}
    with pytest.raises(ValueError, match="terminal"):
        repo.append_run_event("run-1", "running", at=NOW + timedelta(seconds=3))


def test_artifact_is_hashed_and_tampering_is_detected(tmp_path):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    repo.register_strategy(manifest(), at=NOW)
    repo.register_run(context(), generated_at=NOW)
    p = tmp_path / "targets.json"; p.write_text('{"MU":0.2}')
    artifact = repo.add_artifact("run-1", "targets", p, created_at=NOW)
    assert repo.verify_artifact(artifact["artifact_id"])["ok"] is True
    stored = artifact["uri"]
    assert stored != str(p.resolve())
    assert artifact["metadata"]["source_uri"] == str(p.resolve())
    p.write_text("tampered")
    # The registered artifact is a restorable content-addressed copy, so a
    # mutable source projection can change without rewriting the frozen run.
    assert repo.verify_artifact(artifact["artifact_id"])["ok"] is True
    from pathlib import Path
    Path(stored).write_text("tampered")
    assert repo.verify_artifact(artifact["artifact_id"])["ok"] is False
    with pytest.raises(ValueError, match="checksum"):
        repo.add_artifact("run-1", "targets", p, created_at=NOW, expected_sha256="0" * 64)


def test_unknown_ids_naive_times_and_invalid_states_fail_closed(tmp_path):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.register_strategy(manifest(), at=datetime(2026, 1, 1))
    repo.register_strategy(manifest(), at=NOW)
    with pytest.raises(KeyError):
        repo.register_run({**context(), "strategy_id": "missing"}, generated_at=NOW)
    repo.register_run(context(), generated_at=NOW)
    with pytest.raises(ValueError, match="unknown run state"):
        repo.append_run_event("run-1", "live", at=NOW)
    assert repo.summary() == {
        "strategies": 1,
        "runs": 0,
        "completed_runs": 0,
        "incomplete_runs": 1,
        "artifacts": 0,
    }


def test_run_bundle_is_complete_atomic_and_idempotent(tmp_path):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    repo.register_strategy(manifest(), at=NOW)
    payloads = {"snapshot": b'{"a":1}', "source_run": b'{"b":2}'}
    digests = {kind: hashlib.sha256(payload).hexdigest() for kind, payload in payloads.items()}
    frozen_context = {**context(), "input_artifacts": digests}
    metadata = {
        kind: {"encoding": "canonical-json", "role": "frozen_adapter_input"}
        for kind in payloads
    }

    run = repo.register_run_bundle(
        frozen_context,
        generated_at=NOW,
        artifacts=payloads,
        artifact_metadata=metadata,
        expected_sha256=digests,
    )
    assert run["complete"] is True
    assert run["required_artifacts"] == ["snapshot", "source_run"]
    assert len(run["artifact_manifest_hash"]) == 64
    assert len(run["artifacts"]) == 2
    assert repo.runs()[0]["run_id"] == "run-1"

    retried = repo.register_run_bundle(
        frozen_context,
        generated_at=NOW,
        artifacts=payloads,
        artifact_metadata=metadata,
        expected_sha256=digests,
    )
    assert retried["artifact_manifest_hash"] == run["artifact_manifest_hash"]
    assert len(retried["events"]) == 1
    assert repo.summary() == {
        "strategies": 1,
        "runs": 1,
        "completed_runs": 1,
        "incomplete_runs": 0,
        "artifacts": 2,
    }


def test_bundle_blob_failure_publishes_no_run(tmp_path, monkeypatch):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    repo.register_strategy(manifest(), at=NOW)
    payloads = {"first": b"one", "second": b"two"}
    digests = {kind: hashlib.sha256(payload).hexdigest() for kind, payload in payloads.items()}
    frozen_context = {**context(), "input_artifacts": digests}
    original_store = repo._store_blob
    calls = 0

    def fail_second(payload, digest):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected storage failure")
        return original_store(payload, digest)

    monkeypatch.setattr(repo, "_store_blob", fail_second)
    with pytest.raises(OSError, match="injected"):
        repo.register_run_bundle(frozen_context, generated_at=NOW, artifacts=payloads)
    assert repo.summary() == {
        "strategies": 1,
        "runs": 0,
        "completed_runs": 0,
        "incomplete_runs": 0,
        "artifacts": 0,
    }
    assert repo.runs() == []
    with pytest.raises(KeyError):
        repo.get_run("run-1", require_complete=False)


def test_bundle_database_failure_rolls_back_run_event_and_first_artifact(tmp_path):
    db = tmp_path / "platform.sqlite"
    repo = PlatformRepository(db)
    repo.register_strategy(manifest(), at=NOW)
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TRIGGER inject_second_artifact_failure
            BEFORE INSERT ON artifacts
            WHEN NEW.kind = 'second'
            BEGIN
              SELECT RAISE(ABORT, 'injected second artifact failure');
            END;
            """
        )
    payloads = {"first": b"one", "second": b"two"}
    digests = {kind: hashlib.sha256(payload).hexdigest() for kind, payload in payloads.items()}
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repo.register_run_bundle(
            {**context(), "input_artifacts": digests}, generated_at=NOW, artifacts=payloads
        )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert repo.runs() == []


def test_incomplete_runs_are_filtered_and_execution_read_fails_closed(tmp_path):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    repo.register_strategy(manifest(), at=NOW)
    staged = repo.register_run(context(), generated_at=NOW)
    assert staged["complete"] is False
    assert repo.runs() == []
    assert repo.runs(include_incomplete=True)[0]["run_id"] == "run-1"
    with pytest.raises(ValueError, match="incomplete"):
        repo.get_run("run-1", require_complete=True)


def test_event_chain_rejects_backdating_and_freezes_terminal_state(tmp_path):
    repo = PlatformRepository(tmp_path / "platform.sqlite")
    repo.register_strategy(manifest(), at=NOW)
    repo.register_run(context(), generated_at=NOW)
    repo.append_run_event("run-1", "running", at=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="strictly increasing"):
        repo.append_run_event("run-1", "blocked", at=NOW - timedelta(seconds=1))
    done_at = NOW + timedelta(seconds=2)
    done = repo.append_run_event("run-1", "completed", at=done_at, detail={"rows": 15})
    exact_retry = repo.append_run_event("run-1", "completed", at=done_at, detail={"rows": 15})
    assert len(exact_retry["events"]) == len(done["events"]) == 3
    with pytest.raises(ValueError, match="terminal"):
        repo.append_run_event(
            "run-1", "completed", at=NOW + timedelta(seconds=3), detail={"rows": 999}
        )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE run_events SET detail='{}' WHERE run_id='run-1' AND run_sequence=2", "event hash"),
        ("DELETE FROM run_events WHERE run_id='run-1' AND run_sequence=2", "head/count"),
        ("UPDATE runs SET context='{}' WHERE run_id='run-1'", "context hash"),
        (
            "UPDATE runs SET validation_status='VALIDATED / LIVE' WHERE run_id='run-1'",
            "validation status",
        ),
        (
            "UPDATE runs SET generated_at='2020-01-01T00:00:00+00:00' WHERE run_id='run-1'",
            "generated_at",
        ),
    ],
)
def test_run_read_recomputes_context_chain_and_checkpoint(tmp_path, statement, message):
    db = tmp_path / "platform.sqlite"
    repo = PlatformRepository(db)
    repo.register_strategy(manifest(), at=NOW)
    repo.register_run(context(), generated_at=NOW)
    repo.append_run_event("run-1", "running", at=NOW + timedelta(seconds=1), detail={"step": 1})
    with sqlite3.connect(db) as conn:
        conn.execute(statement)
    with pytest.raises(ValueError, match=message):
        repo.get_run("run-1")


def test_complete_read_verifies_artifact_metadata_and_blob(tmp_path):
    db = tmp_path / "platform.sqlite"
    repo = PlatformRepository(db)
    repo.register_strategy(manifest(), at=NOW)
    payload = b"frozen-input"
    digest = hashlib.sha256(payload).hexdigest()
    run = repo.register_run_bundle(
        {**context(), "input_artifacts": {"input": digest}},
        generated_at=NOW,
        artifacts={"input": payload},
        artifact_metadata={"input": {"role": "frozen_adapter_input"}},
    )
    artifact = run["artifacts"][0]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE artifacts SET metadata='{}' WHERE artifact_id=?", (artifact["artifact_id"],))
    with pytest.raises(ValueError, match="metadata hash"):
        repo.get_run("run-1", require_complete=True)

    # Restore the row, then prove the read gate also checks the CAS bytes.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE artifacts SET metadata=? WHERE artifact_id=?",
            ('{"role":"frozen_adapter_input"}', artifact["artifact_id"]),
        )
    Path(artifact["uri"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        repo.get_run("run-1", require_complete=True)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE artifacts SET artifact_id='forged'", "identity"),
        (
            "UPDATE artifacts SET created_at='2020-01-01T00:00:00+00:00'",
            "required-artifact time",
        ),
    ],
)
def test_complete_read_binds_artifact_identity_and_time(tmp_path, statement, message):
    db = tmp_path / "platform.sqlite"
    repo = PlatformRepository(db)
    repo.register_strategy(manifest(), at=NOW)
    payload = b"frozen-input"
    digest = hashlib.sha256(payload).hexdigest()
    repo.register_run_bundle(
        {**context(), "input_artifacts": {"input": digest}},
        generated_at=NOW,
        artifacts={"input": payload},
    )
    with sqlite3.connect(db) as conn:
        conn.execute(statement)
    with pytest.raises(ValueError, match=message):
        repo.get_run("run-1")


def test_repository_reads_are_physically_read_only(tmp_path):
    db = tmp_path / "platform.sqlite"
    repo = PlatformRepository(db)
    repo.register_strategy(manifest(), at=NOW)
    payload = b"frozen-input"
    digest = hashlib.sha256(payload).hexdigest()
    run = repo.register_run_bundle(
        {**context(), "input_artifacts": {"input": digest}},
        generated_at=NOW,
        artifacts={"input": payload},
    )
    artifact_id = run["artifacts"][0]["artifact_id"]
    before = _physical_file(db)
    tree_before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    sidecars_before = _sidecars(tmp_path)

    assert repo.strategies()[0]["strategy_id"] == "stock_select"
    assert repo.runs()[0]["run_id"] == "run-1"
    assert repo.get_run("run-1")["event_head_hash"] == run["event_head_hash"]
    assert repo.verify_artifact(artifact_id)["ok"] is True
    assert repo.summary()["completed_runs"] == 1

    assert _physical_file(db) == before
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == tree_before
    assert _sidecars(tmp_path) == sidecars_before == []


def test_repository_read_does_not_create_or_migrate_storage(tmp_path):
    db = tmp_path / "platform.sqlite"
    repo = PlatformRepository(db)
    db.unlink()
    with pytest.raises(FileNotFoundError, match="registry is missing"):
        repo.summary()
    assert not db.exists()

    db.touch()
    before = _physical_file(db)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        repo.summary()
    assert _physical_file(db) == before
    assert _sidecars(tmp_path) == []
