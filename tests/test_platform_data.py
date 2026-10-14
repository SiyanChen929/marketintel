from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from market_intel.platform import DatasetRequest, Finality, Instrument
from market_intel.platform.data import DatasetCatalog, InstrumentMaster


UTC = timezone.utc
T0 = datetime(2026, 1, 2, 15, tzinfo=UTC)


def _physical_file(path):
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns, stat.st_size


def _sidecars(root):
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.name.endswith(("-wal", "-shm", "-journal"))
    )


def request(**kw):
    base = dict(
        provider="vendor", entitlement="research", dataset="daily_close",
        instrument_ids=("equity:US:MU", "equity:US:NVDA"), fields=("close", "volume"),
        frequency="1d", adjustment="split_dividend", start=date(2025, 1, 1), end=date(2025, 12, 31),
        as_of=T0, schema_version="1", parameters={"currency": "USD"},
    )
    base.update(kw)
    return DatasetRequest(**base)


def test_request_identity_is_canonical_and_pit_bound():
    a = request()
    b = request(instrument_ids=tuple(reversed(a.instrument_ids)), fields=tuple(reversed(a.fields)),
                parameters={"currency": "USD"})
    assert a.key == b.key
    assert a.canonical_dict()["instrument_ids"] == sorted(a.instrument_ids)
    with pytest.raises(ValueError, match="timezone-aware"):
        request(as_of=datetime(2026, 1, 2))
    with pytest.raises(ValueError, match="end"):
        request(start=date(2026, 1, 1), end=date(2025, 1, 1))


def test_catalog_deduplicates_bytes_and_enforces_observation_clock(tmp_path):
    cat = DatasetCatalog(tmp_path / "catalog.sqlite")
    req = request()
    v1 = cat.register(req, b"one", effective_at=T0 - timedelta(days=3), observed_at=T0,
                      acquired_at=T0 + timedelta(seconds=2), finality="provisional")
    assert cat.lookup(req, T0 + timedelta(seconds=3), require_confirmed=True) is None
    assert cat.lookup(req, T0 - timedelta(seconds=1), require_confirmed=False) is None
    assert cat.lookup(req, T0 + timedelta(seconds=1), require_confirmed=False) is None
    assert cat.lookup(req, T0 + timedelta(seconds=3), require_confirmed=False) == v1
    # Same identity is idempotent and does not duplicate the blob or version row.
    again = cat.register(req, b"one", effective_at=T0 - timedelta(days=3), observed_at=T0,
                         acquired_at=T0 + timedelta(seconds=9), finality="provisional")
    assert again.version_id == v1.version_id
    assert cat.summary() == {"requests": 1, "versions": 1, "unique_payloads": 1,
                             "by_finality": {"provisional": 1}}


def test_catalog_effective_at_is_version_identity_but_acquired_at_is_not(tmp_path):
    cat = DatasetCatalog(tmp_path / "catalog.sqlite")
    req = request()
    common = dict(observed_at=T0, finality="confirmed")
    first = cat.register(
        req,
        b"same bytes",
        effective_at=T0 - timedelta(days=2),
        acquired_at=T0 + timedelta(seconds=1),
        **common,
    )
    retry = cat.register(
        req,
        b"same bytes",
        effective_at=T0 - timedelta(days=2),
        acquired_at=T0 + timedelta(minutes=1),
        **common,
    )
    distinct_vintage = cat.register(
        req,
        b"same bytes",
        effective_at=T0 - timedelta(days=1),
        acquired_at=T0 + timedelta(minutes=2),
        **common,
    )

    assert retry.version_id == first.version_id
    assert retry.acquired_at == first.acquired_at
    assert distinct_vintage.version_id != first.version_id
    assert len(cat.versions(req)) == 2
    assert cat.summary()["unique_payloads"] == 1


def test_catalog_migrates_legacy_uniqueness_without_losing_rows(tmp_path):
    db = tmp_path / "legacy.sqlite"
    req = request()
    payload = b"legacy bytes"
    digest = hashlib.sha256(payload).hexdigest()
    blob = tmp_path / "legacy_blobs" / digest[:2] / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE dataset_requests(
              request_key TEXT PRIMARY KEY, canonical_json TEXT NOT NULL
            );
            CREATE TABLE dataset_versions(
              version_id TEXT PRIMARY KEY, request_key TEXT NOT NULL,
              content_sha256 TEXT NOT NULL, n_bytes INTEGER NOT NULL,
              effective_at TEXT NOT NULL, observed_at TEXT NOT NULL,
              acquired_at TEXT NOT NULL, finality TEXT NOT NULL,
              schema_version TEXT NOT NULL, blob_path TEXT NOT NULL,
              UNIQUE(request_key,content_sha256,observed_at,finality),
              FOREIGN KEY(request_key) REFERENCES dataset_requests(request_key)
            );
            CREATE INDEX ix_dataset_versions_pit
              ON dataset_versions(request_key,observed_at,acquired_at);
            """
        )
        conn.execute(
            "INSERT INTO dataset_requests(request_key,canonical_json) VALUES(?,?)",
            (req.key, json.dumps(req.canonical_dict(), sort_keys=True, separators=(",", ":"))),
        )
        conn.execute(
            """INSERT INTO dataset_versions VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "legacy-version",
                req.key,
                digest,
                len(payload),
                (T0 - timedelta(days=2)).isoformat(),
                T0.isoformat(),
                (T0 + timedelta(seconds=1)).isoformat(),
                "confirmed",
                req.schema_version,
                str(blob),
            ),
        )

    cat = DatasetCatalog(db)
    legacy_retry = cat.register(
        req,
        payload,
        effective_at=T0 - timedelta(days=2),
        observed_at=T0,
        acquired_at=T0 + timedelta(minutes=1),
        finality="confirmed",
    )
    distinct_vintage = cat.register(
        req,
        payload,
        effective_at=T0 - timedelta(days=1),
        observed_at=T0,
        acquired_at=T0 + timedelta(minutes=1),
        finality="confirmed",
    )
    assert legacy_retry.version_id == "legacy-version"
    assert legacy_retry.acquired_at == T0 + timedelta(seconds=1)
    assert distinct_vintage.version_id != "legacy-version"
    assert {version.version_id for version in cat.versions(req)} == {
        "legacy-version",
        distinct_vintage.version_id,
    }


def test_catalog_latest_confirmed_checksum_and_restart(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cat = DatasetCatalog(db)
    req = request()
    v = cat.register(req, b"confirmed bytes", effective_at=T0, observed_at=T0 + timedelta(hours=1),
                     acquired_at=T0 + timedelta(hours=1, seconds=1), finality="confirmed")
    assert v.finality is Finality.CONFIRMED
    assert v.to_ref(req.dataset).request_hash == req.key
    assert DatasetCatalog(db).lookup(req, T0 + timedelta(hours=2)).version_id == v.version_id
    path = tmp_path / "catalog_blobs" / v.content_sha256[:2] / v.content_sha256
    path.write_bytes(b"tampered")
    with pytest.raises(IOError, match="checksum"):
        DatasetCatalog(db).lookup(req, T0 + timedelta(hours=2))
    with pytest.raises(ValueError, match="checksum"):
        cat.register(req, b"x", effective_at=T0, observed_at=T0, acquired_at=T0,
                     finality="confirmed", expected_sha256="0" * 64)


def test_exact_catalog_version_recomputes_request_metadata_and_blob_identity(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cat = DatasetCatalog(db)
    req = request()
    payload = b"catalog-bound quote"
    version = cat.register(
        req,
        payload,
        effective_at=T0,
        observed_at=T0,
        acquired_at=T0 + timedelta(seconds=1),
        finality="confirmed",
    )
    resolved, request_record, resolved_payload = cat.get_version(version.version_id)
    assert resolved == version
    assert request_record == req.canonical_dict()
    assert resolved_payload == payload

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE dataset_versions SET acquired_at=? WHERE version_id=?",
            ((T0 + timedelta(days=1)).isoformat(), version.version_id),
        )
    with pytest.raises(IOError, match="metadata checksum"):
        cat.get_version(version.version_id)


def test_exact_catalog_version_rejects_tampered_request_record(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cat = DatasetCatalog(db)
    req = request()
    version = cat.register(
        req,
        b"quote",
        effective_at=T0,
        observed_at=T0,
        acquired_at=T0,
        finality="confirmed",
    )
    forged = dict(req.canonical_dict())
    forged["instrument_ids"] = ["equity:US:OTHER"]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE dataset_requests SET canonical_json=? WHERE request_key=?",
            (json.dumps(forged, sort_keys=True, separators=(",", ":")), req.key),
        )
    with pytest.raises(IOError, match="request metadata"):
        cat.get_version(version.version_id)


def test_instrument_master_resolves_symbol_validity_and_rejects_overlap(tmp_path):
    master = InstrumentMaster(tmp_path / "instruments.sqlite")
    old = Instrument("equity:US:OLD", "equity", "USD")
    new = Instrument("equity:US:NEW", "equity", "USD")
    master.register(old); master.register(new); master.register(old)  # idempotent
    cut = datetime(2025, 6, 1, tzinfo=UTC)
    master.map_symbol("vendor", "ABC", old.instrument_id, datetime(2020, 1, 1, tzinfo=UTC), cut)
    master.map_symbol("vendor", "ABC", new.instrument_id, cut)
    assert master.resolve("vendor", "abc", cut - timedelta(seconds=1)) == old.instrument_id
    assert master.resolve("vendor", "ABC", cut) == new.instrument_id
    with pytest.raises(ValueError, match="overlapping"):
        master.map_symbol("vendor", "ABC", old.instrument_id, cut - timedelta(days=2))
    with pytest.raises(KeyError, match="unknown"):
        master.map_symbol("vendor", "XYZ", "missing", cut)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "ESZ6-ALT"),
        ("exchange", "OTHER"),
        ("underlying_instrument_id", "index:US:OTHER"),
        ("expiry", date(2026, 12, 19)),
        ("contract_code", "ES_ALT"),
    ],
)
def test_instrument_master_persists_and_compares_full_future_identity(tmp_path, field, value):
    db = tmp_path / "instruments.sqlite"
    future = Instrument(
        instrument_id="future:US:ESZ6",
        asset_class="future",
        currency="USD",
        symbol="ESZ6",
        exchange="XCME",
        underlying_instrument_id="index:US:SPX",
        expiry=date(2026, 12, 18),
        contract_code="ES",
        multiplier=50.0,
        metadata={"settlement": "cash"},
    )
    InstrumentMaster(db).register(future)

    restarted = InstrumentMaster(db)
    assert restarted.register(future) == future
    with pytest.raises(ValueError, match="different definition"):
        restarted.register(replace(future, **{field: value}))


def test_instrument_master_additively_migrates_legacy_table(tmp_path):
    db = tmp_path / "legacy-instruments.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE instruments(
              instrument_id TEXT PRIMARY KEY, asset_class TEXT NOT NULL,
              currency TEXT NOT NULL, multiplier REAL NOT NULL, metadata TEXT NOT NULL
            );
            CREATE TABLE symbols(
              provider TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL,
              valid_from TEXT NOT NULL, valid_to TEXT,
              PRIMARY KEY(provider, symbol, valid_from),
              FOREIGN KEY(instrument_id) REFERENCES instruments(instrument_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO instruments VALUES(?,?,?,?,?)",
            ("equity:US:NVDA", "equity", "USD", 1.0, "{}"),
        )

    master = InstrumentMaster(db)
    legacy = Instrument("equity:US:NVDA", "equity", "USD")
    assert master.register(legacy) == legacy
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(instruments)")}
    assert {"symbol", "exchange", "underlying_instrument_id", "expiry", "contract_code"} <= columns
    with pytest.raises(ValueError, match="different definition"):
        master.register(replace(legacy, symbol="NVDA"))


def test_finality_and_timestamp_validation_fail_closed(tmp_path):
    cat = DatasetCatalog(tmp_path / "catalog.sqlite")
    req = request()
    with pytest.raises(ValueError, match="timezone-aware"):
        cat.register(req, b"x", effective_at=datetime(2026, 1, 1), observed_at=T0,
                     acquired_at=T0, finality="confirmed")
    with pytest.raises(ValueError, match="unknown finality"):
        cat.register(req, b"x", effective_at=T0, observed_at=T0,
                     acquired_at=T0, finality="final-ish")
    with pytest.raises(ValueError, match="after acquired"):
        cat.register(req, b"x", effective_at=T0, observed_at=T0 + timedelta(seconds=1),
                     acquired_at=T0, finality="confirmed")


def test_catalog_reads_are_physically_read_only(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cat = DatasetCatalog(db)
    req = request()
    version = cat.register(
        req,
        b"catalog payload",
        effective_at=T0,
        observed_at=T0,
        acquired_at=T0 + timedelta(seconds=1),
        finality="confirmed",
    )
    before = _physical_file(db)
    tree_before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    sidecars_before = _sidecars(tmp_path)

    assert cat.lookup(req, T0 + timedelta(seconds=2)) == version
    assert cat.request_record(req.key) == req.canonical_dict()
    assert cat.get_version(version.version_id)[0] == version
    assert cat.versions(req) == [version]
    assert cat.summary()["versions"] == 1

    assert _physical_file(db) == before
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == tree_before
    assert _sidecars(tmp_path) == sidecars_before == []


def test_catalog_read_does_not_create_or_migrate_storage(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cat = DatasetCatalog(db)
    db.unlink()
    with pytest.raises(FileNotFoundError, match="catalog is missing"):
        cat.summary()
    assert not db.exists()

    db.touch()
    before = _physical_file(db)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        cat.summary()
    assert _physical_file(db) == before
    assert _sidecars(tmp_path) == []
