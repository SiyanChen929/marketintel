"""Persistent registry for strategies, immutable runs and their artifacts.

The legacy application publishes one mutable ``outputs/latest`` projection.  The
platform registry is written alongside that projection and gives every strategy a
stable identity.  A run row is immutable; progress is represented by append-only
events so a completed result cannot be silently rewritten in place.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


UTC = timezone.utc
RUN_STATES = frozenset({"created", "running", "blocked", "failed", "completed"})
TERMINAL_RUN_STATES = frozenset({"failed", "completed"})
RUN_TRANSITIONS = {
    "created": frozenset({"running", "blocked", "failed", "completed"}),
    "running": frozenset({"running", "blocked", "failed", "completed"}),
    "blocked": frozenset({"running", "blocked", "failed", "completed"}),
    "failed": frozenset(),
    "completed": frozenset(),
}


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode()).hexdigest()


def _aware_iso(value: str | datetime, name: str) -> str:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except Exception as exc:
        raise ValueError(f"{name} must be an ISO datetime") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return dt.astimezone(UTC).isoformat()


class PlatformRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = self.path.parent / "artifacts"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategies(
                  strategy_id TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL,
                  asset_classes TEXT NOT NULL, clocks TEXT NOT NULL, manifest TEXT NOT NULL,
                  manifest_hash TEXT NOT NULL, registered_at TEXT NOT NULL,
                  PRIMARY KEY(strategy_id,version)
                );
                CREATE TABLE IF NOT EXISTS runs(
                  run_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
                  strategy_version TEXT NOT NULL,
                  portfolio_id TEXT NOT NULL, as_of TEXT NOT NULL, generated_at TEXT NOT NULL,
                  context TEXT NOT NULL, context_hash TEXT NOT NULL,
                  validation_status TEXT NOT NULL,
                  freeze_complete INTEGER NOT NULL DEFAULT 0,
                  required_artifacts TEXT NOT NULL DEFAULT '[]',
                  artifact_manifest_hash TEXT,
                  event_head_hash TEXT,
                  event_count INTEGER NOT NULL DEFAULT 0,
                  FOREIGN KEY(strategy_id,strategy_version) REFERENCES strategies(strategy_id,version)
                );
                CREATE TABLE IF NOT EXISTS run_events(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, at TEXT NOT NULL,
                  state TEXT NOT NULL, detail TEXT NOT NULL,
                  run_sequence INTEGER NOT NULL, previous_hash TEXT NOT NULL,
                  event_hash TEXT NOT NULL UNIQUE,
                  FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS ix_run_events_run ON run_events(run_id,seq);
                CREATE TABLE IF NOT EXISTS artifacts(
                  artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                  uri TEXT NOT NULL, content_sha256 TEXT NOT NULL, n_bytes INTEGER NOT NULL,
                  metadata TEXT NOT NULL, metadata_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                  UNIQUE(run_id,kind,uri,content_sha256),
                  FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                """
            )
            self._migrate_schema(conn)

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _event_genesis(cls, run_id: str, context_hash: str) -> str:
        return _hash({"chain": "run-events-v1", "run_id": run_id, "context_hash": context_hash})

    @staticmethod
    def _event_digest(
        run_id: str,
        run_sequence: int,
        at: str,
        state: str,
        detail: Mapping[str, Any],
        previous_hash: str,
    ) -> str:
        return _hash(
            {
                "run_id": run_id,
                "run_sequence": run_sequence,
                "at": at,
                "state": state,
                "detail": dict(detail),
                "previous_hash": previous_hash,
            }
        )

    @classmethod
    def _migrate_schema(cls, conn: sqlite3.Connection) -> None:
        """Add integrity columns without treating legacy runs as published.

        Legacy event hashes are checked under their original scheme before they
        are upgraded to the per-run chain.  Existing runs remain incomplete;
        only ``register_run_bundle`` may publish their required input manifest.
        """

        run_columns = cls._columns(conn, "runs")
        added_head = "event_head_hash" not in run_columns
        for name, ddl in (
            ("freeze_complete", "INTEGER NOT NULL DEFAULT 0"),
            ("required_artifacts", "TEXT NOT NULL DEFAULT '[]'"),
            ("artifact_manifest_hash", "TEXT"),
            ("event_head_hash", "TEXT"),
            ("event_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in run_columns:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {ddl}")

        event_columns = cls._columns(conn, "run_events")
        added_event_chain = "run_sequence" not in event_columns and "previous_hash" not in event_columns
        if "run_sequence" not in event_columns:
            conn.execute("ALTER TABLE run_events ADD COLUMN run_sequence INTEGER")
        if "previous_hash" not in event_columns:
            conn.execute("ALTER TABLE run_events ADD COLUMN previous_hash TEXT")

        artifact_columns = cls._columns(conn, "artifacts")
        added_metadata_hash = "metadata_hash" not in artifact_columns
        if added_metadata_hash:
            conn.execute("ALTER TABLE artifacts ADD COLUMN metadata_hash TEXT")

        if added_metadata_hash:
            for artifact in conn.execute("SELECT artifact_id,metadata FROM artifacts"):
                try:
                    metadata = json.loads(artifact["metadata"])
                except Exception as exc:
                    raise ValueError(
                        f"legacy artifact {artifact['artifact_id']!r} has invalid metadata JSON"
                    ) from exc
                conn.execute(
                    "UPDATE artifacts SET metadata_hash=? WHERE artifact_id=?",
                    (_hash(metadata), artifact["artifact_id"]),
                )

        for run in conn.execute("SELECT run_id,context,context_hash FROM runs ORDER BY run_id"):
            run_id, context_hash = str(run["run_id"]), str(run["context_hash"])
            try:
                context = json.loads(run["context"])
            except Exception as exc:
                raise ValueError(f"run {run_id!r} has invalid context JSON") from exc
            if _hash(context) != context_hash:
                raise ValueError(f"run {run_id!r} failed context hash verification during migration")
            events = conn.execute(
                """SELECT seq,at,state,detail,event_hash,run_sequence,previous_hash
                   FROM run_events WHERE run_id=? ORDER BY seq""",
                (run_id,),
            ).fetchall()
            if added_event_chain:
                previous = cls._event_genesis(run_id, context_hash)
                for run_sequence, event in enumerate(events, 1):
                    try:
                        detail = json.loads(event["detail"])
                    except Exception as exc:
                        raise ValueError(f"legacy event {event['seq']} has invalid detail JSON") from exc
                    legacy = _hash(
                        {
                            "run_id": run_id,
                            "at": event["at"],
                            "state": event["state"],
                            "detail": detail,
                        }
                    )
                    if legacy != event["event_hash"]:
                        raise ValueError(f"legacy event {event['seq']} failed hash verification")
                    digest = cls._event_digest(
                        run_id, run_sequence, event["at"], event["state"], detail, previous
                    )
                    conn.execute(
                        """UPDATE run_events
                           SET run_sequence=?,previous_hash=?,event_hash=? WHERE seq=?""",
                        (run_sequence, previous, digest, event["seq"]),
                    )
                    previous = digest
                events = conn.execute(
                    """SELECT seq,at,state,detail,event_hash,run_sequence,previous_hash
                       FROM run_events WHERE run_id=? ORDER BY seq""",
                    (run_id,),
                ).fetchall()
            elif any(event["run_sequence"] is None or event["previous_hash"] is None for event in events):
                raise ValueError(f"run {run_id!r} has a partially migrated event chain")

            if added_head:
                head = str(events[-1]["event_hash"]) if events else cls._event_genesis(run_id, context_hash)
                conn.execute(
                    "UPDATE runs SET event_head_hash=?,event_count=? WHERE run_id=?",
                    (head, len(events), run_id),
                )

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_run_events_run_sequence "
            "ON run_events(run_id,run_sequence)"
        )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            # Rollback-journal mode lets strict read-only viewers open the
            # committed registry without creating/updating WAL shared-memory
            # sidecars.  BEGIN IMMEDIATE transaction semantics stay unchanged.
            conn.execute("PRAGMA journal_mode=DELETE")
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _read_conn(self) -> Iterator[sqlite3.Connection]:
        """Open the initialized registry without creating files or WAL sidecars."""

        if not self.path.is_file():
            raise FileNotFoundError(f"platform registry is missing: {self.path}")
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _strategy_dict(row: sqlite3.Row) -> dict[str, Any]:
        identity = f"{row['strategy_id']}@{row['version']}"
        try:
            manifest = json.loads(row["manifest"])
        except Exception as exc:
            raise ValueError(f"strategy {identity!r} has invalid manifest JSON") from exc
        if not isinstance(manifest, dict) or _hash(manifest) != row["manifest_hash"]:
            raise ValueError(f"strategy {identity!r} failed manifest hash verification")
        if (
            manifest.get("strategy_id") != row["strategy_id"]
            or manifest.get("name") != row["name"]
            or manifest.get("version") != row["version"]
            or _canon(manifest.get("asset_classes") or []) != row["asset_classes"]
            or _canon(manifest.get("clocks") or []) != row["clocks"]
        ):
            raise ValueError(f"strategy {identity!r} manifest disagrees with indexed fields")
        registered_at = _aware_iso(row["registered_at"], "persisted strategy registered_at")
        return {**manifest, "manifest_hash": row["manifest_hash"], "registered_at": registered_at}

    def register_strategy(self, manifest: Mapping[str, Any], *, at: datetime) -> dict[str, Any]:
        raw = dict(manifest)
        sid = str(raw.get("strategy_id") or "").strip()
        name, version = str(raw.get("name") or "").strip(), str(raw.get("version") or "").strip()
        if not sid or not name or not version:
            raise ValueError("strategy_id, name and version are required")
        stamp = _aware_iso(at, "at")
        canonical, digest = _canon(raw), _hash(raw)
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id=? AND version=?", (sid, version)
            ).fetchone()
            if row is not None:
                self._strategy_dict(row)
                if row["manifest_hash"] != digest:
                    raise ValueError(f"strategy {sid!r}@{version!r} already has a different manifest")
            else:
                conn.execute(
                    """INSERT INTO strategies(strategy_id,name,version,asset_classes,clocks,manifest,manifest_hash,registered_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (sid, name, version, _canon(raw.get("asset_classes") or []),
                     _canon(raw.get("clocks") or []), canonical, digest, stamp),
                )
        return {**raw, "manifest_hash": digest, "registered_at": stamp}

    @staticmethod
    def _normalise_kind(kind: str) -> str:
        value = str(kind).strip()
        if not value:
            raise ValueError("artifact kind is required")
        return value

    def _prepare_run_context(
        self, conn: sqlite3.Connection, context: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str, str, sqlite3.Row | None]:
        raw = dict(context)
        required = ("run_id", "workspace_id", "strategy_id", "portfolio_id", "as_of")
        missing = [k for k in required if not str(raw.get(k) or "").strip()]
        if missing:
            raise ValueError("missing run fields: " + ", ".join(missing))
        raw["as_of"] = _aware_iso(raw["as_of"], "as_of")
        existing_run = conn.execute("SELECT * FROM runs WHERE run_id=?", (raw["run_id"],)).fetchone()
        candidates = conn.execute(
            "SELECT * FROM strategies WHERE strategy_id=? ORDER BY version",
            (raw["strategy_id"],),
        ).fetchall()
        if not candidates:
            raise KeyError(f"unknown strategy_id {raw['strategy_id']!r}")
        for candidate in candidates:
            self._strategy_dict(candidate)
        requested_version = str(raw.get("strategy_version") or "").strip()
        if (
            not requested_version
            and existing_run is not None
            and existing_run["strategy_id"] == raw["strategy_id"]
        ):
            requested_version = str(existing_run["strategy_version"])
        if requested_version:
            if requested_version not in {str(row["version"]) for row in candidates}:
                raise KeyError(f"unknown strategy {raw['strategy_id']!r}@{requested_version!r}")
        elif len(candidates) == 1:
            requested_version = str(candidates[0]["version"])
        else:
            raise ValueError(
                f"strategy_version is required because {raw['strategy_id']!r} has multiple registered versions"
            )
        raw["strategy_version"] = requested_version
        canonical, digest = _canon(raw), _hash(raw)
        return raw, canonical, digest, existing_run

    def _insert_or_validate_run(
        self,
        conn: sqlite3.Connection,
        context: Mapping[str, Any],
        generated: str,
        validation_status: str,
    ) -> sqlite3.Row:
        raw, canonical, digest, row = self._prepare_run_context(conn, context)
        if row is not None:
            try:
                stored_context = json.loads(row["context"])
            except Exception as exc:
                raise ValueError(f"run {raw['run_id']!r} has invalid context JSON") from exc
            if _hash(stored_context) != row["context_hash"]:
                raise ValueError(f"run {raw['run_id']!r} failed context hash verification")
            if row["context_hash"] != digest:
                raise ValueError(f"run_id {raw['run_id']!r} already has a different immutable context")
            if row["generated_at"] != generated or row["validation_status"] != validation_status:
                raise ValueError(f"run_id {raw['run_id']!r} already has different immutable run metadata")
            return row

        genesis = self._event_genesis(str(raw["run_id"]), digest)
        conn.execute(
            """INSERT INTO runs(
                   run_id,workspace_id,strategy_id,strategy_version,portfolio_id,as_of,generated_at,
                   context,context_hash,validation_status,freeze_complete,required_artifacts,
                   artifact_manifest_hash,event_head_hash,event_count
               ) VALUES(?,?,?,?,?,?,?,?,?,?,0,'[]',NULL,?,0)""",
            (
                raw["run_id"], raw["workspace_id"], raw["strategy_id"], raw["strategy_version"],
                raw["portfolio_id"], raw["as_of"], generated, canonical, digest, validation_status,
                genesis,
            ),
        )
        self._append_event(
            conn,
            str(raw["run_id"]),
            generated,
            "created",
            {"validation_status": validation_status},
        )
        return conn.execute("SELECT * FROM runs WHERE run_id=?", (raw["run_id"],)).fetchone()

    def register_run(
        self,
        context: Mapping[str, Any],
        *,
        generated_at: datetime | str,
        validation_status: str = "NOT VALIDATED",
    ) -> dict[str, Any]:
        """Register an incomplete run.

        This lower-level method remains available for staged producers.  An
        incomplete run is excluded from normal reads until
        :meth:`register_run_bundle` atomically publishes its required inputs.
        """

        generated = _aware_iso(generated_at, "generated_at")
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._insert_or_validate_run(conn, context, generated, validation_status)
            run_id = str(row["run_id"])
        return self._read_run(run_id, allow_incomplete=True)

    def _validated_events(self, conn: sqlite3.Connection, run: sqlite3.Row) -> list[dict[str, Any]]:
        run_id = str(run["run_id"])
        rows = conn.execute(
            """SELECT seq,run_sequence,at,state,detail,previous_hash,event_hash
               FROM run_events WHERE run_id=? ORDER BY run_sequence,seq""",
            (run_id,),
        ).fetchall()
        expected_previous = self._event_genesis(run_id, str(run["context_hash"]))
        previous_at: datetime | None = None
        previous_state: str | None = None
        events: list[dict[str, Any]] = []
        for expected_sequence, event in enumerate(rows, 1):
            sequence = event["run_sequence"]
            if sequence is None or int(sequence) != expected_sequence:
                raise ValueError(f"run {run_id!r} has a non-contiguous event sequence")
            if event["previous_hash"] != expected_previous:
                raise ValueError(f"run {run_id!r} failed event previous-hash verification")
            state = str(event["state"])
            if state not in RUN_STATES:
                raise ValueError(f"run {run_id!r} has unknown persisted state {state!r}")
            try:
                detail = json.loads(event["detail"])
            except Exception as exc:
                raise ValueError(f"run {run_id!r} has invalid event detail JSON") from exc
            if not isinstance(detail, dict):
                raise ValueError(f"run {run_id!r} event detail must be an object")
            stamp = _aware_iso(event["at"], "persisted event at")
            current_at = datetime.fromisoformat(stamp)
            if previous_at is not None and current_at <= previous_at:
                raise ValueError(f"run {run_id!r} event timestamps are not strictly increasing")
            if previous_state is None:
                if state != "created":
                    raise ValueError(f"run {run_id!r} event chain must begin with created")
            elif state not in RUN_TRANSITIONS[previous_state]:
                raise ValueError(
                    f"run {run_id!r} has invalid transition from {previous_state} to {state}"
                )
            digest = self._event_digest(
                run_id, expected_sequence, event["at"], state, detail, expected_previous
            )
            if digest != event["event_hash"]:
                raise ValueError(f"run {run_id!r} failed event hash verification")
            events.append(
                {
                    "seq": int(event["seq"]),
                    "run_sequence": expected_sequence,
                    "at": event["at"],
                    "state": state,
                    "detail": detail,
                    "previous_hash": expected_previous,
                    "event_hash": digest,
                }
            )
            expected_previous, previous_at, previous_state = digest, current_at, state

        if not events:
            raise ValueError(f"run {run_id!r} has no creation event")
        if int(run["event_count"]) != len(events) or run["event_head_hash"] != expected_previous:
            raise ValueError(f"run {run_id!r} failed event head/count verification")
        return events

    def _append_event(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        at: str,
        state: str,
        detail: Mapping[str, Any] | None,
    ) -> None:
        run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise KeyError(f"unknown run_id {run_id!r}")
        if int(run["event_count"]):
            events = self._validated_events(conn, run)
        else:
            stored_events = int(
                conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
            )
            expected_genesis = self._event_genesis(run_id, str(run["context_hash"]))
            if stored_events or run["event_head_hash"] != expected_genesis:
                raise ValueError(f"run {run_id!r} failed empty event head/count verification")
            events = []
        payload_detail = dict(detail or {})
        if events:
            last = events[-1]
            if last["at"] == at and last["state"] == state and last["detail"] == payload_detail:
                return
            if last["state"] in TERMINAL_RUN_STATES:
                raise ValueError(f"terminal run {run_id!r} cannot accept another event")
            if datetime.fromisoformat(_aware_iso(at, "at")) <= datetime.fromisoformat(
                _aware_iso(last["at"], "last event at")
            ):
                raise ValueError(f"run {run_id!r} event timestamps must be strictly increasing")
            if state not in RUN_TRANSITIONS[last["state"]]:
                raise ValueError(
                    f"run {run_id!r} cannot transition from {last['state']} to {state}"
                )
        elif state != "created":
            raise ValueError(f"run {run_id!r} event chain must begin with created")

        run_sequence = len(events) + 1
        previous_hash = str(run["event_head_hash"])
        digest = self._event_digest(run_id, run_sequence, at, state, payload_detail, previous_hash)
        conn.execute(
            """INSERT INTO run_events(
                   run_id,at,state,detail,run_sequence,previous_hash,event_hash
               ) VALUES(?,?,?,?,?,?,?)""",
            (run_id, at, state, _canon(payload_detail), run_sequence, previous_hash, digest),
        )
        updated = conn.execute(
            """UPDATE runs SET event_head_hash=?,event_count=?
               WHERE run_id=? AND event_head_hash=? AND event_count=?""",
            (digest, run_sequence, run_id, previous_hash, run_sequence - 1),
        )
        if updated.rowcount != 1:
            raise ValueError(f"run {run_id!r} event head changed during append")

    def append_run_event(self, run_id: str, state: str, *, at: datetime,
                         detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if state not in RUN_STATES:
            raise ValueError(f"unknown run state {state!r}")
        stamp = _aware_iso(at, "at")
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._append_event(conn, run_id, stamp, state, detail)
        return self._read_run(run_id, allow_incomplete=True)

    def add_artifact(self, run_id: str, kind: str, path: str | Path, *, created_at: datetime,
                     metadata: Mapping[str, Any] | None = None,
                     expected_sha256: str | None = None) -> dict[str, Any]:
        p = Path(path).resolve()
        if not p.is_file():
            raise FileNotFoundError(p)
        payload = p.read_bytes()
        details = {**dict(metadata or {}), "source_uri": str(p)}
        return self.add_artifact_bytes(
            run_id,
            kind,
            payload,
            created_at=created_at,
            metadata=details,
            expected_sha256=expected_sha256,
        )

    def add_artifact_bytes(
        self,
        run_id: str,
        kind: str,
        payload: bytes,
        *,
        created_at: datetime,
        metadata: Mapping[str, Any] | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Copy artifact bytes into an immutable content-addressed run store."""

        stamp = _aware_iso(created_at, "created_at")
        kind = self._normalise_kind(kind)
        prepared = self._prepare_artifact(
            run_id, kind, payload, stamp, metadata=metadata, expected_sha256=expected_sha256
        )
        with self._lock, self._conn() as conn:
            if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise KeyError(f"unknown run_id {run_id!r}")
        self._store_blob(payload, prepared["content_sha256"])

        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run_id {run_id!r}")
            self._validate_context_row(run)
            self._validated_events(conn, run)
            required = self._required_artifact_names(run)
            same_kind = conn.execute(
                "SELECT * FROM artifacts WHERE run_id=? AND kind=? ORDER BY artifact_id",
                (run_id, kind),
            ).fetchall()
            if bool(run["freeze_complete"]) and kind in required:
                if len(same_kind) == 1 and self._artifact_matches(same_kind[0], prepared):
                    return self._artifact_dict(same_kind[0], verify_blob=True)
                raise ValueError(f"required artifact {kind!r} is frozen for completed run {run_id!r}")
            for existing in same_kind:
                if existing["uri"] == prepared["uri"] and existing["content_sha256"] == prepared["content_sha256"]:
                    if not self._artifact_matches(existing, prepared):
                        raise ValueError("artifact retry has different immutable metadata")
                    return self._artifact_dict(existing, verify_blob=True)
            conn.execute(
                """INSERT INTO artifacts(
                       artifact_id,run_id,kind,uri,content_sha256,n_bytes,metadata,metadata_hash,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    prepared["artifact_id"], run_id, kind, prepared["uri"],
                    prepared["content_sha256"], prepared["n_bytes"], prepared["metadata_json"],
                    prepared["metadata_hash"], stamp,
                ),
            )
        return {
            key: value
            for key, value in prepared.items()
            if key not in {"metadata_json"}
        }

    def _prepare_artifact(
        self,
        run_id: str,
        kind: str,
        payload: bytes,
        stamp: str,
        *,
        metadata: Mapping[str, Any] | None,
        expected_sha256: str | None,
    ) -> dict[str, Any]:
        if not isinstance(payload, bytes):
            raise TypeError("artifact payload must be bytes")
        details = dict(metadata or {})
        metadata_json, metadata_hash = _canon(details), _hash(details)
        digest = hashlib.sha256(payload).hexdigest()
        if expected_sha256 is not None and digest != str(expected_sha256).lower():
            raise ValueError("artifact checksum does not match expected_sha256")
        blob = self.artifact_dir / digest[:2] / digest
        aid = _hash(
            {
                "run_id": run_id,
                "kind": kind,
                "sha256": digest,
                "metadata_hash": metadata_hash,
            }
        )
        return {
            "artifact_id": aid,
            "run_id": run_id,
            "kind": kind,
            "uri": str(blob),
            "content_sha256": digest,
            "n_bytes": len(payload),
            "metadata": details,
            "metadata_json": metadata_json,
            "metadata_hash": metadata_hash,
            "created_at": stamp,
        }

    def _store_blob(self, payload: bytes, digest: str) -> Path:
        blob = self.artifact_dir / digest[:2] / digest
        blob.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if blob.exists():
                existing = blob.read_bytes()
                if len(existing) != len(payload) or hashlib.sha256(existing).hexdigest() != digest:
                    raise ValueError("content-addressed artifact blob is corrupt")
            else:
                temporary = blob.with_name(f".{blob.name}.{os.getpid()}.tmp")
                try:
                    with temporary.open("wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, blob)
                    directory_fd = os.open(str(blob.parent), os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    temporary.unlink(missing_ok=True)
        return blob

    @staticmethod
    def _artifact_matches(row: sqlite3.Row, prepared: Mapping[str, Any]) -> bool:
        return all(
            row[field] == prepared[field]
            for field in ("kind", "uri", "content_sha256", "n_bytes", "metadata_hash", "created_at")
        ) and row["metadata"] == prepared["metadata_json"]

    def _artifact_dict(self, row: sqlite3.Row, *, verify_blob: bool) -> dict[str, Any]:
        artifact_id = str(row["artifact_id"])
        try:
            metadata = json.loads(row["metadata"])
        except Exception as exc:
            raise ValueError(f"artifact {artifact_id!r} has invalid metadata JSON") from exc
        if not isinstance(metadata, dict) or _hash(metadata) != row["metadata_hash"]:
            raise ValueError(f"artifact {artifact_id!r} failed metadata hash verification")
        digest = str(row["content_sha256"])
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"artifact {artifact_id!r} has an invalid content digest")
        expected_path = (self.artifact_dir / digest[:2] / digest).resolve()
        stored_path = Path(row["uri"]).resolve()
        if stored_path != expected_path:
            raise ValueError(f"artifact {artifact_id!r} does not use its content-addressed path")
        expected_id = _hash(
            {
                "run_id": str(row["run_id"]),
                "kind": str(row["kind"]),
                "sha256": digest,
                "metadata_hash": str(row["metadata_hash"]),
            }
        )
        if artifact_id != expected_id:
            raise ValueError(f"artifact {artifact_id!r} failed identity verification")
        _aware_iso(row["created_at"], "persisted artifact created_at")
        if verify_blob:
            if not stored_path.is_file():
                raise ValueError(f"artifact {artifact_id!r} content blob is missing")
            payload = stored_path.read_bytes()
            if len(payload) != int(row["n_bytes"]) or hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError(f"artifact {artifact_id!r} failed checksum verification")
        return {
            "artifact_id": artifact_id,
            "run_id": str(row["run_id"]),
            "kind": str(row["kind"]),
            "uri": str(row["uri"]),
            "content_sha256": digest,
            "n_bytes": int(row["n_bytes"]),
            "metadata": metadata,
            "metadata_hash": str(row["metadata_hash"]),
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _artifact_manifest(artifacts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            (
                {
                    "kind": str(item["kind"]),
                    "content_sha256": str(item["content_sha256"]),
                    "n_bytes": int(item["n_bytes"]),
                    "metadata_hash": str(item["metadata_hash"]),
                }
                for item in artifacts
            ),
            key=lambda item: item["kind"],
        )

    def register_run_bundle(
        self,
        context: Mapping[str, Any],
        *,
        generated_at: datetime | str,
        artifacts: Mapping[str, bytes],
        artifact_metadata: Mapping[str, Mapping[str, Any]] | None = None,
        expected_sha256: Mapping[str, str] | None = None,
        required_artifacts: Iterable[str] | None = None,
        validation_status: str = "NOT VALIDATED",
    ) -> dict[str, Any]:
        """Atomically publish a run and its complete required-input manifest.

        Blobs enter the content-addressed store first.  The run row, creation
        event, artifact rows, manifest and completion bit then commit in one
        SQLite transaction.  A failed publish may leave unreachable CAS bytes,
        but never a visible complete run.
        """

        generated = _aware_iso(generated_at, "generated_at")
        metadata_by_kind = dict(artifact_metadata or {})
        expected_by_kind = {str(key).strip(): str(value).lower() for key, value in dict(expected_sha256 or {}).items()}
        normalised_payloads: dict[str, bytes] = {}
        for raw_kind, payload in dict(artifacts).items():
            kind = self._normalise_kind(raw_kind)
            if kind in normalised_payloads:
                raise ValueError(f"duplicate artifact kind after normalisation: {kind!r}")
            normalised_payloads[kind] = payload
        if not normalised_payloads:
            raise ValueError("a run bundle requires at least one artifact")
        unknown_metadata = set(metadata_by_kind) - set(normalised_payloads)
        unknown_expected = set(expected_by_kind) - set(normalised_payloads)
        if unknown_metadata or unknown_expected:
            raise ValueError("artifact metadata/checksums contain unknown kinds")

        if required_artifacts is None:
            required = sorted(normalised_payloads)
        else:
            required = [self._normalise_kind(kind) for kind in required_artifacts]
            if len(required) != len(set(required)):
                raise ValueError("required artifact kinds must be unique")
            required = sorted(required)
        if not required or set(required) != set(normalised_payloads):
            raise ValueError("the bundle must contain exactly its required artifacts")

        raw_context = dict(context)
        declared_inputs = raw_context.get("input_artifacts")
        if declared_inputs:
            if not isinstance(declared_inputs, Mapping):
                raise ValueError("context input_artifacts must be a mapping")
            declared = {self._normalise_kind(key): str(value).lower() for key, value in declared_inputs.items()}
            if set(declared) != set(required):
                raise ValueError("required artifacts must exactly match context input_artifacts")
            for kind, digest in declared.items():
                supplied = expected_by_kind.get(kind)
                if supplied is not None and supplied != digest:
                    raise ValueError(f"expected checksum for {kind!r} conflicts with run context")
                expected_by_kind[kind] = digest

        prepared: dict[str, dict[str, Any]] = {}
        for kind, payload in normalised_payloads.items():
            prepared[kind] = self._prepare_artifact(
                str(raw_context.get("run_id") or ""),
                kind,
                payload,
                generated,
                metadata=metadata_by_kind.get(kind),
                expected_sha256=expected_by_kind.get(kind),
            )
        for kind, payload in normalised_payloads.items():
            self._store_blob(payload, prepared[kind]["content_sha256"])

        expected_manifest = self._artifact_manifest(prepared[kind] for kind in required)
        expected_manifest_hash = _hash(expected_manifest)
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._insert_or_validate_run(conn, context, generated, validation_status)
            run_id = str(run["run_id"])
            self._validated_events(conn, run)
            if bool(run["freeze_complete"]):
                if (
                    self._required_artifact_names(run) != required
                    or run["artifact_manifest_hash"] != expected_manifest_hash
                ):
                    raise ValueError(f"completed run {run_id!r} already has a different artifact bundle")
            else:
                for kind in normalised_payloads:
                    item = prepared[kind]
                    rows = conn.execute(
                        "SELECT * FROM artifacts WHERE run_id=? AND kind=? ORDER BY artifact_id",
                        (run_id, kind),
                    ).fetchall()
                    if len(rows) > 1:
                        raise ValueError(f"run {run_id!r} has duplicate artifact kind {kind!r}")
                    if rows:
                        if not self._artifact_matches(rows[0], item):
                            raise ValueError(f"run {run_id!r} has a conflicting artifact {kind!r}")
                        self._artifact_dict(rows[0], verify_blob=True)
                    else:
                        conn.execute(
                            """INSERT INTO artifacts(
                                   artifact_id,run_id,kind,uri,content_sha256,n_bytes,
                                   metadata,metadata_hash,created_at
                               ) VALUES(?,?,?,?,?,?,?,?,?)""",
                            (
                                item["artifact_id"], run_id, kind, item["uri"],
                                item["content_sha256"], item["n_bytes"], item["metadata_json"],
                                item["metadata_hash"], item["created_at"],
                            ),
                        )
                conn.execute(
                    """UPDATE runs SET freeze_complete=1,required_artifacts=?,artifact_manifest_hash=?
                       WHERE run_id=? AND freeze_complete=0""",
                    (_canon(required), expected_manifest_hash, run_id),
                )
        return self.get_run(str(raw_context["run_id"]), require_complete=True)

    def verify_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._lock, self._read_conn() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        p = Path(row["uri"])
        payload = p.read_bytes() if p.is_file() else None
        actual = hashlib.sha256(payload).hexdigest() if payload is not None else None
        try:
            metadata = json.loads(row["metadata"])
            metadata_ok = isinstance(metadata, dict) and _hash(metadata) == row["metadata_hash"]
        except Exception:
            metadata_ok = False
        expected_path = (self.artifact_dir / str(row["content_sha256"])[:2] / str(row["content_sha256"])).resolve()
        path_ok = p.resolve() == expected_path
        size_ok = payload is not None and len(payload) == int(row["n_bytes"])
        return {
            "artifact_id": artifact_id,
            "ok": actual == row["content_sha256"] and metadata_ok and path_ok and size_ok,
            "expected_sha256": row["content_sha256"],
            "actual_sha256": actual,
            "missing": not p.is_file(),
            "metadata_ok": metadata_ok,
            "path_ok": path_ok,
            "size_ok": size_ok,
        }

    @staticmethod
    def _required_artifact_names(run: sqlite3.Row) -> list[str]:
        try:
            required = json.loads(run["required_artifacts"])
        except Exception as exc:
            raise ValueError(f"run {run['run_id']!r} has invalid required-artifact JSON") from exc
        if (
            not isinstance(required, list)
            or any(not isinstance(kind, str) or not kind for kind in required)
            or required != sorted(set(required))
        ):
            raise ValueError(f"run {run['run_id']!r} has an invalid required-artifact manifest")
        return required

    @staticmethod
    def _validate_context_row(row: sqlite3.Row) -> dict[str, Any]:
        run_id = str(row["run_id"])
        try:
            context = json.loads(row["context"])
        except Exception as exc:
            raise ValueError(f"run {run_id!r} has invalid context JSON") from exc
        if not isinstance(context, dict) or _hash(context) != row["context_hash"]:
            raise ValueError(f"run {run_id!r} failed context hash verification")
        expected_fields = {
            "run_id": row["run_id"],
            "workspace_id": row["workspace_id"],
            "strategy_id": row["strategy_id"],
            "strategy_version": row["strategy_version"],
            "portfolio_id": row["portfolio_id"],
            "as_of": row["as_of"],
        }
        if any(context.get(field) != value for field, value in expected_fields.items()):
            raise ValueError(f"run {run_id!r} context disagrees with indexed identity fields")
        _aware_iso(row["generated_at"], "persisted generated_at")
        return context

    def _read_run(
        self,
        run_id: str,
        *,
        allow_incomplete: bool,
        verify_artifacts: bool = True,
    ) -> dict[str, Any]:
        with self._lock, self._read_conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            context = self._validate_context_row(row)
            events = self._validated_events(conn, row)
            if events[0]["at"] != row["generated_at"]:
                raise ValueError(
                    f"run {run_id!r} generated_at does not match its creation event"
                )
            if events[0]["detail"].get("validation_status") != row["validation_status"]:
                raise ValueError(
                    f"run {run_id!r} validation status does not match its creation event"
                )
            artifact_rows = conn.execute(
                "SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at,artifact_id", (run_id,)
            ).fetchall()
            artifacts = [
                self._artifact_dict(artifact, verify_blob=verify_artifacts)
                for artifact in artifact_rows
            ]
            complete_value = int(row["freeze_complete"])
            if complete_value not in (0, 1):
                raise ValueError(f"run {run_id!r} has invalid completion state")
            complete = bool(complete_value)
            required = self._required_artifact_names(row)
            if complete:
                if not required:
                    raise ValueError(f"completed run {run_id!r} has no required artifacts")
                required_rows = [item for item in artifacts if item["kind"] in set(required)]
                counts = {kind: sum(item["kind"] == kind for item in required_rows) for kind in required}
                if any(count != 1 for count in counts.values()):
                    raise ValueError(f"completed run {run_id!r} is missing or duplicates a required artifact")
                if any(item["created_at"] != row["generated_at"] for item in required_rows):
                    raise ValueError(
                        f"completed run {run_id!r} required-artifact time does not match generated_at"
                    )
                manifest_hash = _hash(self._artifact_manifest(required_rows))
                if manifest_hash != row["artifact_manifest_hash"]:
                    raise ValueError(f"run {run_id!r} failed artifact manifest verification")
            elif row["artifact_manifest_hash"] is not None or required:
                raise ValueError(f"incomplete run {run_id!r} has a published artifact manifest")
            if not complete and not allow_incomplete:
                raise ValueError(f"run {run_id!r} is incomplete and cannot be read for execution")
        return {
            "run_id": row["run_id"],
            "workspace_id": row["workspace_id"],
            "strategy_id": row["strategy_id"],
            "strategy_version": row["strategy_version"],
            "portfolio_id": row["portfolio_id"],
            "as_of": row["as_of"],
            "generated_at": row["generated_at"],
            "context": context,
            "context_hash": row["context_hash"],
            "validation_status": row["validation_status"],
            "state": events[-1]["state"],
            "events": events,
            "artifacts": artifacts,
            "complete": complete,
            "required_artifacts": required,
            "artifact_manifest_hash": row["artifact_manifest_hash"],
            "event_head_hash": row["event_head_hash"],
            "event_count": int(row["event_count"]),
        }

    def get_run(
        self,
        run_id: str,
        *,
        require_complete: bool = True,
        verify_artifacts: bool = True,
    ) -> dict[str, Any]:
        return self._read_run(
            run_id,
            allow_incomplete=not require_complete,
            verify_artifacts=verify_artifacts,
        )

    def strategies(self) -> list[dict[str, Any]]:
        with self._lock, self._read_conn() as conn:
            rows = conn.execute("SELECT * FROM strategies ORDER BY strategy_id,version").fetchall()
        return [self._strategy_dict(row) for row in rows]

    def runs(
        self,
        strategy_id: str | None = None,
        limit: int = 100,
        *,
        include_incomplete: bool = False,
    ) -> list[dict[str, Any]]:
        clauses, args = ([] if include_incomplete else ["freeze_complete=1"]), []
        if strategy_id:
            clauses.append("strategy_id=?")
            args.append(strategy_id)
        sql = "SELECT run_id FROM runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY generated_at DESC LIMIT ?"; args.append(max(1, min(int(limit), 1000)))
        with self._lock, self._read_conn() as conn:
            ids = [r["run_id"] for r in conn.execute(sql, args)]
        return [self.get_run(rid, require_complete=not include_incomplete) for rid in ids]

    def summary(self) -> dict[str, Any]:
        with self._lock, self._read_conn() as conn:
            completed = int(
                conn.execute("SELECT COUNT(*) FROM runs WHERE freeze_complete=1").fetchone()[0]
            )
            incomplete = int(
                conn.execute("SELECT COUNT(*) FROM runs WHERE freeze_complete=0").fetchone()[0]
            )
            return {
                "strategies": int(conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]),
                # The legacy ``runs`` field is the externally usable/published
                # count.  Staged rows are reported separately and never inflate it.
                "runs": completed,
                "completed_runs": completed,
                "incomplete_runs": incomplete,
                "artifacts": int(conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]),
            }
