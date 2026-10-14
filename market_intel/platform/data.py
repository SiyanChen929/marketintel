"""Versioned, point-in-time data catalog for the multi-strategy platform.

This module is deliberately provider-agnostic.  It records *what* was requested,
*when* a payload could have been observed, and the exact bytes behind a version.
Downloaders remain outside the catalog; they may reuse a catalog hit instead of
fetching the same data again.

The implementation is intentionally small and strict for the first migration
slice:

* dataset identities are canonical and independent of input ordering;
* payloads are content addressed and checksummed on every read;
* point-in-time lookup requires both ``observed_at`` and ``acquired_at``;
* provisional data cannot satisfy a confirmed-data request;
* vendor symbols are resolved through validity intervals, never today's ticker.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .core import (
    DatasetRequest,
    DatasetVersionRef,
    Finality,
    FrozenMapping,
    Instrument,
    canonical_hash,
    canonical_json,
)


UTC = timezone.utc
FINALITIES = frozenset(item.value for item in Finality)


def _aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _canon(obj: Any) -> str:
    return canonical_json(obj)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class DatasetVersion:
    version_id: str
    request_key: str
    content_sha256: str
    n_bytes: int
    effective_at: datetime
    observed_at: datetime
    acquired_at: datetime
    finality: Finality | str
    schema_version: str
    blob_path: str

    def __post_init__(self) -> None:
        for name in ("effective_at", "observed_at", "acquired_at"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))
        if self.observed_at > self.acquired_at:
            raise ValueError("observed_at cannot be after acquired_at")
        try:
            object.__setattr__(self, "finality", Finality(self.finality))
        except ValueError as exc:
            raise ValueError(f"unknown finality {self.finality!r}") from exc
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a full SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for name in ("effective_at", "observed_at", "acquired_at"):
            d[name] = getattr(self, name).isoformat()
        d["finality"] = self.finality.value
        return d

    def to_ref(self, dataset_id: str) -> DatasetVersionRef:
        """Return the canonical immutable reference embedded in a RunContext."""

        return DatasetVersionRef(
            dataset_id=dataset_id,
            version_id=self.version_id,
            content_hash=self.content_sha256,
            observed_at=self.observed_at,
            acquired_at=self.acquired_at,
            effective_at=self.effective_at,
            finality=self.finality,
            schema_version=self.schema_version,
            request_hash=self.request_key,
        )


@dataclass(frozen=True, slots=True)
class VerifiedDatasetVersion:
    """One catalog version whose metadata, request identity and bytes were verified.

    Instances returned by :meth:`DatasetCatalog.freeze_versions` retain the
    exact immutable bytes supplied to a downstream calculation.  The payload
    is intentionally excluded from ``lineage_record``; its full content hash
    is the portable identity recorded there.
    """

    role: str
    reference: DatasetVersionRef
    request: Mapping[str, Any]
    payload: bytes

    def __post_init__(self) -> None:
        role = str(self.role).strip()
        if not role:
            raise ValueError("dataset role must be non-empty")
        if not isinstance(self.reference, DatasetVersionRef):
            raise TypeError("reference must be a DatasetVersionRef")
        if not isinstance(self.request, Mapping):
            raise TypeError("request must be a mapping")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "request", FrozenMapping(self.request))
        object.__setattr__(self, "payload", bytes(self.payload))

    def lineage_record(self) -> dict[str, Any]:
        ref = self.reference
        return {
            "role": self.role,
            "dataset_id": ref.dataset_id,
            "version_id": ref.version_id,
            "request_hash": ref.request_hash,
            "content_sha256": ref.content_sha256,
            "n_bytes": len(self.payload),
            "schema_version": ref.schema_version,
            "effective_at": ref.effective_at.isoformat(),
            "observed_at": ref.observed_at.isoformat(),
            "acquired_at": ref.acquired_at.isoformat(),
            "finality": ref.finality.value,
        }


@dataclass(frozen=True, slots=True)
class FrozenDatasetBinding:
    """Complete, content-addressed input set for one calculation boundary."""

    datasets: Mapping[str, VerifiedDatasetVersion]
    required_roles: tuple[str, ...]
    as_of: datetime
    acquired_by: datetime

    schema_version = "platform.frozen-dataset-binding.v1"

    def __post_init__(self) -> None:
        as_of = _aware(self.as_of, "as_of")
        acquired_by = _aware(self.acquired_by, "acquired_by")
        if as_of > acquired_by:
            raise ValueError("as_of cannot be after acquired_by")
        required = tuple(str(role).strip() for role in self.required_roles)
        if not required or any(not role for role in required):
            raise ValueError("required_roles must contain non-empty roles")
        if len(set(required)) != len(required):
            raise ValueError("required_roles must be unique")
        datasets = FrozenMapping(self.datasets)
        missing = [role for role in required if role not in datasets]
        if missing:
            raise ValueError("missing required frozen dataset roles: " + ", ".join(missing))
        for role, version in datasets.items():
            if not isinstance(version, VerifiedDatasetVersion):
                raise TypeError("datasets values must be VerifiedDatasetVersion objects")
            if version.role != role:
                raise ValueError(f"dataset role {role!r} does not match its verified version")
        object.__setattr__(self, "datasets", datasets)
        object.__setattr__(self, "required_roles", required)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "acquired_by", acquired_by)

    @property
    def complete(self) -> bool:
        return all(role in self.datasets for role in self.required_roles)

    @property
    def binding_hash(self) -> str:
        return canonical_hash(
            {
                "schema_version": self.schema_version,
                "required_roles": self.required_roles,
                "as_of": self.as_of,
                "acquired_by": self.acquired_by,
                "datasets": {
                    role: self.datasets[role].lineage_record()
                    for role in self.required_roles
                },
            }
        )

    def payload(self, role: str) -> bytes:
        return self.datasets[role].payload

    def lineage(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "complete": self.complete,
            "binding_hash": self.binding_hash,
            "required_roles": list(self.required_roles),
            "as_of": self.as_of.isoformat(),
            "acquired_by": self.acquired_by.isoformat(),
            "dataset_versions": {
                role: self.datasets[role].lineage_record()
                for role in self.required_roles
            },
        }


class InstrumentMaster:
    """Permanent instruments plus PIT-valid vendor symbol aliases."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS instruments(
                  instrument_id TEXT PRIMARY KEY, asset_class TEXT NOT NULL,
                  currency TEXT NOT NULL, symbol TEXT, exchange TEXT,
                  underlying_instrument_id TEXT, expiry TEXT, contract_code TEXT,
                  multiplier REAL NOT NULL, metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS symbols(
                  provider TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL,
                  valid_from TEXT NOT NULL, valid_to TEXT,
                  PRIMARY KEY(provider, symbol, valid_from),
                  FOREIGN KEY(instrument_id) REFERENCES instruments(instrument_id)
                );
                CREATE INDEX IF NOT EXISTS ix_symbols_lookup
                  ON symbols(provider, symbol, valid_from, valid_to);
                """
            )
            # Older catalogs stored only the generic fields.  Add columns in
            # place so aliases and dependent tables remain intact.  Values that
            # were never persisted stay NULL and therefore fail closed if a
            # caller later tries to attach a richer definition to the same ID.
            existing_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(instruments)")
            }
            for name in (
                "symbol",
                "exchange",
                "underlying_instrument_id",
                "expiry",
                "contract_code",
            ):
                if name not in existing_columns:
                    conn.execute(f"ALTER TABLE instruments ADD COLUMN {name} TEXT")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=DELETE")
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _read_conn(self) -> Iterator[sqlite3.Connection]:
        """Open the initialized instrument master without creating files or sidecars."""

        if not self.db_path.is_file():
            raise FileNotFoundError(f"instrument master is missing: {self.db_path}")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            yield conn
        finally:
            conn.close()

    def register(self, instrument: Instrument) -> Instrument:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM instruments WHERE instrument_id=?", (instrument.instrument_id,)).fetchone()
            payload = _canon(dict(instrument.metadata))
            values = (
                instrument.asset_class.value,
                instrument.currency,
                instrument.symbol,
                instrument.exchange,
                instrument.underlying_instrument_id,
                instrument.expiry.isoformat() if instrument.expiry is not None else None,
                instrument.contract_code,
                float(instrument.multiplier),
                payload,
            )
            if row is not None:
                old = (
                    row["asset_class"],
                    row["currency"],
                    row["symbol"],
                    row["exchange"],
                    row["underlying_instrument_id"],
                    row["expiry"],
                    row["contract_code"],
                    float(row["multiplier"]),
                    row["metadata"],
                )
                if old != values:
                    raise ValueError(f"instrument_id {instrument.instrument_id!r} already has a different definition")
                return instrument
            conn.execute(
                """INSERT INTO instruments(
                     instrument_id,asset_class,currency,symbol,exchange,
                     underlying_instrument_id,expiry,contract_code,multiplier,metadata
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (instrument.instrument_id, *values),
            )
        return instrument

    def map_symbol(
        self,
        provider: str,
        symbol: str,
        instrument_id: str,
        valid_from: datetime,
        valid_to: datetime | None = None,
    ) -> None:
        provider, symbol = provider.strip(), symbol.strip().upper()
        start = _aware(valid_from, "valid_from")
        end = _aware(valid_to, "valid_to") if valid_to is not None else None
        if not provider or not symbol:
            raise ValueError("provider and symbol are required")
        if end is not None and end <= start:
            raise ValueError("valid_to must be after valid_from")
        with self._lock, self._conn() as conn:
            if conn.execute("SELECT 1 FROM instruments WHERE instrument_id=?", (instrument_id,)).fetchone() is None:
                raise KeyError(f"unknown instrument_id {instrument_id!r}")
            rows = conn.execute(
                "SELECT valid_from,valid_to,instrument_id FROM symbols WHERE provider=? AND symbol=?",
                (provider, symbol),
            ).fetchall()
            for row in rows:
                other_start = datetime.fromisoformat(row["valid_from"])
                other_end = datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None
                overlap = (end is None or other_start < end) and (other_end is None or start < other_end)
                exact = other_start == start and other_end == end and row["instrument_id"] == instrument_id
                if overlap and not exact:
                    raise ValueError(f"overlapping symbol validity for {provider}:{symbol}")
                if exact:
                    return
            conn.execute(
                "INSERT INTO symbols(provider,symbol,instrument_id,valid_from,valid_to) VALUES(?,?,?,?,?)",
                (provider, symbol, instrument_id, start.isoformat(), end.isoformat() if end else None),
            )

    def resolve(self, provider: str, symbol: str, at: datetime) -> str:
        point = _aware(at, "at").isoformat()
        with self._lock, self._read_conn() as conn:
            rows = conn.execute(
                """SELECT instrument_id FROM symbols
                   WHERE provider=? AND symbol=? AND valid_from<=?
                     AND (valid_to IS NULL OR valid_to>?)""",
                (provider.strip(), symbol.strip().upper(), point, point),
            ).fetchall()
        if len(rows) != 1:
            raise KeyError(f"{provider}:{symbol} has {len(rows)} mappings at {point}; expected exactly one")
        return str(rows[0]["instrument_id"])


class DatasetCatalog:
    """Persistent request/version registry with content-addressed local blobs."""

    def __init__(self, db_path: str | Path, blob_dir: str | Path | None = None):
        self.db_path = Path(db_path)
        self.blob_dir = Path(blob_dir) if blob_dir is not None else self.db_path.with_suffix("").with_name(self.db_path.stem + "_blobs")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dataset_requests(
                  request_key TEXT PRIMARY KEY, canonical_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dataset_versions(
                  version_id TEXT PRIMARY KEY, request_key TEXT NOT NULL,
                  content_sha256 TEXT NOT NULL, n_bytes INTEGER NOT NULL,
                  effective_at TEXT NOT NULL, observed_at TEXT NOT NULL,
                  acquired_at TEXT NOT NULL, finality TEXT NOT NULL,
                  schema_version TEXT NOT NULL, blob_path TEXT NOT NULL,
                  metadata_sha256 TEXT,
                  UNIQUE(request_key,content_sha256,effective_at,observed_at,finality),
                  FOREIGN KEY(request_key) REFERENCES dataset_requests(request_key)
                );
                CREATE INDEX IF NOT EXISTS ix_dataset_versions_pit
                  ON dataset_versions(request_key,observed_at,acquired_at);
                """
            )
            self._migrate_version_uniqueness(conn)
            self._ensure_metadata_hashes(conn)

    @staticmethod
    def _migrate_version_uniqueness(conn: sqlite3.Connection) -> None:
        """Replace the legacy, overly broad uniqueness contract in place.

        The legacy constraint omitted ``effective_at`` and consequently
        collapsed two economically distinct vintages when their bytes and
        observation clocks happened to match.  SQLite cannot drop a table
        constraint, so preserve every row while rebuilding the table inside
        the caller's transaction.
        """

        legacy_columns = ("request_key", "content_sha256", "observed_at", "finality")
        unique_indexes: set[tuple[str, ...]] = set()
        for index in conn.execute("PRAGMA index_list(dataset_versions)").fetchall():
            if int(index["unique"]):
                columns = tuple(
                    str(row["name"])
                    for row in conn.execute(f"PRAGMA index_info({index['name']})").fetchall()
                )
                unique_indexes.add(columns)
        if legacy_columns not in unique_indexes:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP INDEX IF EXISTS ix_dataset_versions_pit")
            conn.execute("ALTER TABLE dataset_versions RENAME TO dataset_versions_legacy")
            conn.execute(
                """CREATE TABLE dataset_versions(
                     version_id TEXT PRIMARY KEY, request_key TEXT NOT NULL,
                     content_sha256 TEXT NOT NULL, n_bytes INTEGER NOT NULL,
                     effective_at TEXT NOT NULL, observed_at TEXT NOT NULL,
                     acquired_at TEXT NOT NULL, finality TEXT NOT NULL,
                     schema_version TEXT NOT NULL, blob_path TEXT NOT NULL,
                     metadata_sha256 TEXT,
                     UNIQUE(request_key,content_sha256,effective_at,observed_at,finality),
                     FOREIGN KEY(request_key) REFERENCES dataset_requests(request_key)
                   )"""
            )
            conn.execute(
                """INSERT INTO dataset_versions(
                     version_id,request_key,content_sha256,n_bytes,effective_at,observed_at,
                     acquired_at,finality,schema_version,blob_path
                   )
                   SELECT version_id,request_key,content_sha256,n_bytes,effective_at,observed_at,
                          acquired_at,finality,schema_version,blob_path
                   FROM dataset_versions_legacy"""
            )
            conn.execute("DROP TABLE dataset_versions_legacy")
            conn.execute(
                """CREATE INDEX ix_dataset_versions_pit
                   ON dataset_versions(request_key,observed_at,acquired_at)"""
            )
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    @staticmethod
    def _metadata_hash(row: Mapping[str, Any]) -> str:
        return _sha(
            _canon(
                {
                    "version_id": row["version_id"],
                    "request_key": row["request_key"],
                    "content_sha256": row["content_sha256"],
                    "n_bytes": int(row["n_bytes"]),
                    "effective_at": row["effective_at"],
                    "observed_at": row["observed_at"],
                    "acquired_at": row["acquired_at"],
                    "finality": row["finality"],
                    "schema_version": row["schema_version"],
                    "blob_path": row["blob_path"],
                }
            ).encode()
        )

    @classmethod
    def _ensure_metadata_hashes(cls, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(dataset_versions)").fetchall()
        }
        if "metadata_sha256" not in columns:
            conn.execute("ALTER TABLE dataset_versions ADD COLUMN metadata_sha256 TEXT")
        rows = conn.execute(
            "SELECT * FROM dataset_versions WHERE metadata_sha256 IS NULL"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE dataset_versions SET metadata_sha256=? WHERE version_id=?",
                (cls._metadata_hash(row), row["version_id"]),
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            # Keep committed catalog state in the main file so read-only
            # consumers never need to create or update WAL sidecars.
            conn.execute("PRAGMA journal_mode=DELETE")
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _read_conn(self) -> Iterator[sqlite3.Connection]:
        """Open the initialized catalog without creating files or WAL sidecars."""

        if not self.db_path.is_file():
            raise FileNotFoundError(f"dataset catalog is missing: {self.db_path}")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> DatasetVersion:
        return DatasetVersion(
            version_id=row["version_id"], request_key=row["request_key"],
            content_sha256=row["content_sha256"], n_bytes=int(row["n_bytes"]),
            effective_at=datetime.fromisoformat(row["effective_at"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            acquired_at=datetime.fromisoformat(row["acquired_at"]),
            finality=row["finality"], schema_version=row["schema_version"],
            blob_path=row["blob_path"],
        )

    def register(
        self,
        request: DatasetRequest,
        payload: bytes,
        *,
        effective_at: datetime,
        observed_at: datetime,
        acquired_at: datetime,
        finality: str,
        expected_sha256: str | None = None,
    ) -> DatasetVersion:
        effective_at = _aware(effective_at, "effective_at")
        observed_at = _aware(observed_at, "observed_at")
        acquired_at = _aware(acquired_at, "acquired_at")
        if observed_at > acquired_at:
            raise ValueError("observed_at cannot be after acquired_at")
        try:
            finality_value = Finality(finality).value
        except ValueError as exc:
            raise ValueError(f"unknown finality {finality!r}") from exc
        digest = _sha(bytes(payload))
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("payload checksum does not match expected_sha256")
        # ``effective_at`` is part of a dataset vintage's economic identity.
        # ``acquired_at`` is deliberately excluded so an exact retry remains
        # idempotent even when the downloader's wall clock changes.
        version_id = _sha(_canon({
            "request_key": request.key,
            "content_sha256": digest,
            "effective_at": effective_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "finality": finality_value,
        }).encode())
        blob = self.blob_dir / digest[:2] / digest
        blob.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as conn:
            canon = _canon(request.canonical_dict())
            old = conn.execute("SELECT canonical_json FROM dataset_requests WHERE request_key=?", (request.key,)).fetchone()
            if old is not None and old["canonical_json"] != canon:
                raise RuntimeError("request hash collision")
            conn.execute("INSERT OR IGNORE INTO dataset_requests(request_key,canonical_json) VALUES(?,?)", (request.key, canon))
            # Query by the declared uniqueness contract as well as by the
            # derived ID.  This keeps exact retries idempotent after migrating
            # legacy rows whose historical version IDs predate effective_at.
            existing = conn.execute(
                """SELECT * FROM dataset_versions
                   WHERE request_key=? AND content_sha256=? AND effective_at=?
                     AND observed_at=? AND finality=?""",
                (
                    request.key,
                    digest,
                    effective_at.isoformat(),
                    observed_at.isoformat(),
                    finality_value,
                ),
            ).fetchone()
            if existing is not None:
                version = self._row(existing)
            else:
                if conn.execute(
                    "SELECT 1 FROM dataset_versions WHERE version_id=?", (version_id,)
                ).fetchone() is not None:
                    raise RuntimeError("dataset version hash collision")
                # Write before the metadata transaction commits.  An orphaned hash blob
                # is harmless; metadata pointing at absent bytes is not.
                if blob.exists() and _sha(blob.read_bytes()) != digest:
                    raise IOError(f"content-addressed blob {blob} is corrupt")
                if not blob.exists():
                    tmp = blob.with_name(blob.name + f".{os.getpid()}.tmp")
                    tmp.write_bytes(bytes(payload))
                    os.replace(tmp, blob)
                conn.execute(
                    """INSERT INTO dataset_versions(
                         version_id,request_key,content_sha256,n_bytes,effective_at,observed_at,
                         acquired_at,finality,schema_version,blob_path,metadata_sha256
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        version_id,
                        request.key,
                        digest,
                        len(payload),
                        effective_at.isoformat(),
                        observed_at.isoformat(),
                        acquired_at.isoformat(),
                        finality_value,
                        request.schema_version,
                        str(blob),
                        self._metadata_hash(
                            {
                                "version_id": version_id,
                                "request_key": request.key,
                                "content_sha256": digest,
                                "n_bytes": len(payload),
                                "effective_at": effective_at.isoformat(),
                                "observed_at": observed_at.isoformat(),
                                "acquired_at": acquired_at.isoformat(),
                                "finality": finality_value,
                                "schema_version": request.schema_version,
                                "blob_path": str(blob),
                            }
                        ),
                    ),
                )
                version = DatasetVersion(version_id, request.key, digest, len(payload), effective_at,
                                         observed_at, acquired_at, finality_value, request.schema_version, str(blob))
        return version

    def lookup(self, request: DatasetRequest, as_of: datetime, *, require_confirmed: bool = True) -> DatasetVersion | None:
        point = _aware(as_of, "as_of").isoformat()
        finality_clause = "AND finality IN ('confirmed','final')" if require_confirmed else ""
        with self._lock, self._read_conn() as conn:
            row = conn.execute(
                f"""SELECT * FROM dataset_versions
                    WHERE request_key=? AND effective_at<=? AND observed_at<=? AND acquired_at<=?
                    {finality_clause}
                    ORDER BY observed_at DESC, acquired_at DESC, effective_at DESC, version_id DESC
                    LIMIT 1""",
                (request.key, point, point, point),
            ).fetchone()
        if row is None:
            return None
        if row["metadata_sha256"] != self._metadata_hash(row):
            raise IOError("dataset version metadata checksum mismatch")
        version = self._row(row)
        stored_request = self.request_record(version.request_key)
        if stored_request != request.canonical_dict():
            raise IOError("dataset lookup request metadata does not match its request_key")
        if version.schema_version != stored_request.get("schema_version"):
            raise IOError("dataset version schema does not match its request")
        self.read(version)  # every hit proves the bytes still match the identity
        return version

    def read(self, version: DatasetVersion) -> bytes:
        path = Path(version.blob_path)
        expected_path = self.blob_dir / version.content_sha256[:2] / version.content_sha256
        if path.resolve(strict=False) != expected_path.resolve(strict=False):
            raise IOError("dataset blob path does not match its content-addressed identity")
        if not path.exists():
            raise IOError(f"dataset blob missing: {path}")
        payload = path.read_bytes()
        if len(payload) != version.n_bytes or _sha(payload) != version.content_sha256:
            raise IOError(f"dataset blob checksum mismatch: {path}")
        return payload

    def request_record(self, request_key: str) -> dict[str, Any]:
        """Return one canonical request after recomputing its stored identity."""

        key = str(request_key).strip().lower()
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("request_key must be a full SHA-256 digest")
        with self._lock, self._read_conn() as conn:
            row = conn.execute(
                "SELECT canonical_json FROM dataset_requests WHERE request_key=?", (key,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown dataset request {key!r}")
        raw = str(row["canonical_json"])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IOError("dataset request metadata is not valid JSON") from exc
        if not isinstance(payload, Mapping) or _canon(payload) != raw or _sha(raw.encode()) != key:
            raise IOError("dataset request metadata does not match its request_key")
        return dict(payload)

    def get_version(self, version_id: str) -> tuple[DatasetVersion, dict[str, Any], bytes]:
        """Resolve one exact catalog version and verify request, metadata and bytes."""

        identity = str(version_id).strip().lower()
        if len(identity) != 64 or any(char not in "0123456789abcdef" for char in identity):
            raise ValueError("version_id must be a full SHA-256 digest")
        with self._lock, self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM dataset_versions WHERE version_id=?", (identity,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown dataset version {identity!r}")
        if row["metadata_sha256"] != self._metadata_hash(row):
            raise IOError("dataset version metadata checksum mismatch")
        version = self._row(row)
        request = self.request_record(version.request_key)
        if version.schema_version != request.get("schema_version"):
            raise IOError("dataset version schema does not match its request")
        expected_id = _sha(
            _canon(
                {
                    "request_key": version.request_key,
                    "content_sha256": version.content_sha256,
                    "effective_at": version.effective_at.isoformat(),
                    "observed_at": version.observed_at.isoformat(),
                    "finality": version.finality.value,
                }
            ).encode()
        )
        if version.version_id != expected_id:
            raise IOError("dataset version metadata does not match its version_id")
        return version, request, self.read(version)

    def verify_ref(
        self,
        reference: DatasetVersionRef,
        *,
        as_of: datetime,
        acquired_by: datetime | None = None,
        require_confirmed: bool = True,
    ) -> tuple[DatasetVersion, dict[str, Any], bytes]:
        """Resolve and fully verify one exact immutable dataset reference."""

        if not isinstance(reference, DatasetVersionRef):
            raise TypeError("reference must be a DatasetVersionRef")
        point = _aware(as_of, "as_of")
        deadline = _aware(acquired_by, "acquired_by") if acquired_by is not None else point
        if point > deadline:
            raise ValueError("as_of cannot be after acquired_by")
        version, request, payload = self.get_version(reference.version_id)
        dataset_id = request.get("dataset")
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise IOError("registered dataset request has no dataset identity")
        expected = version.to_ref(dataset_id.strip())
        if reference != expected:
            raise IOError("dataset reference does not match its registered version")
        if version.effective_at > point:
            raise ValueError("dataset version is not effective at as_of")
        if version.observed_at > point:
            raise ValueError("dataset version was not observed by as_of")
        if version.acquired_at > deadline:
            raise ValueError("dataset version was not acquired by the calculation deadline")
        if require_confirmed and version.finality not in (Finality.CONFIRMED, Finality.FINAL):
            raise ValueError("dataset version is not confirmed or final")
        vintage_raw = request.get("vintage_as_of")
        if not isinstance(vintage_raw, str):
            raise IOError("registered dataset request has no vintage_as_of")
        try:
            vintage = datetime.fromisoformat(vintage_raw.replace("Z", "+00:00"))
            vintage = _aware(vintage, "dataset request vintage_as_of")
        except (TypeError, ValueError) as exc:
            raise IOError("registered dataset request has invalid vintage_as_of") from exc
        if vintage > point:
            raise ValueError("dataset version has a future request vintage")
        return version, request, payload

    def freeze_versions(
        self,
        dataset_versions: Mapping[str, DatasetVersionRef],
        *,
        required_roles: Iterable[str],
        as_of: datetime,
        acquired_by: datetime | None = None,
        require_confirmed: bool = True,
    ) -> FrozenDatasetBinding:
        """Verify and bind an exact set of catalog versions for a calculation.

        This is an exact-version operation, not a latest-version lookup.  Each
        supplied reference must reconcile to the registered request metadata,
        version metadata and content-addressed blob.  Only the declared roles
        enter the returned binding, so a caller cannot satisfy a required role
        with an unrelated extra dataset.
        """

        if not isinstance(dataset_versions, Mapping):
            raise TypeError("dataset_versions must be a mapping")
        point = _aware(as_of, "as_of")
        deadline = _aware(acquired_by, "acquired_by") if acquired_by is not None else point
        if point > deadline:
            raise ValueError("as_of cannot be after acquired_by")
        roles = tuple(str(role).strip() for role in required_roles)
        if not roles or any(not role for role in roles):
            raise ValueError("required_roles must contain non-empty roles")
        if len(set(roles)) != len(roles):
            raise ValueError("required_roles must be unique")
        missing = [role for role in roles if role not in dataset_versions]
        if missing:
            raise ValueError("missing required frozen dataset roles: " + ", ".join(missing))

        verified: dict[str, VerifiedDatasetVersion] = {}
        for role in roles:
            reference = dataset_versions[role]
            if not isinstance(reference, DatasetVersionRef):
                raise TypeError(f"dataset_versions[{role!r}] must be a DatasetVersionRef")
            try:
                version, request, payload = self.verify_ref(
                    reference,
                    as_of=point,
                    acquired_by=deadline,
                    require_confirmed=require_confirmed,
                )
            except (OSError, TypeError, ValueError, KeyError) as exc:
                raise type(exc)(f"frozen dataset role {role!r}: {exc}") from exc
            verified[role] = VerifiedDatasetVersion(role, reference, request, payload)

        return FrozenDatasetBinding(verified, roles, point, deadline)

    def versions(self, request: DatasetRequest | None = None) -> list[DatasetVersion]:
        sql, args = "SELECT * FROM dataset_versions", []
        if request is not None:
            sql += " WHERE request_key=?"
            args.append(request.key)
        sql += " ORDER BY observed_at DESC, acquired_at DESC"
        with self._lock, self._read_conn() as conn:
            rows = list(conn.execute(sql, args))
        versions: list[DatasetVersion] = []
        for row in rows:
            if row["metadata_sha256"] != self._metadata_hash(row):
                raise IOError("dataset version metadata checksum mismatch")
            version = self._row(row)
            stored_request = self.request_record(version.request_key)
            if version.schema_version != stored_request.get("schema_version"):
                raise IOError("dataset version schema does not match its request")
            self.read(version)
            versions.append(version)
        return versions

    def summary(self) -> dict[str, Any]:
        with self._lock, self._read_conn() as conn:
            n_req = int(conn.execute("SELECT COUNT(*) FROM dataset_requests").fetchone()[0])
            n_ver = int(conn.execute("SELECT COUNT(*) FROM dataset_versions").fetchone()[0])
            n_blob = int(conn.execute("SELECT COUNT(DISTINCT content_sha256) FROM dataset_versions").fetchone()[0])
            finals = {row[0]: int(row[1]) for row in conn.execute(
                "SELECT finality,COUNT(*) FROM dataset_versions GROUP BY finality"
            )}
        return {"requests": n_req, "versions": n_ver, "unique_payloads": n_blob, "by_finality": finals}
