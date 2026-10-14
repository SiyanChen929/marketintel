from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from market_intel.platform import NOT_VALIDATED, canonical_hash, canonical_json
from market_intel.platform.data import DatasetCatalog
from market_intel.platform.stock_select_data import (
    FORMAL_BENCHMARK_PROVENANCE_SCHEMA_VERSION,
    REQUIRED_STOCK_SELECT_DATASETS,
    STOCK_SELECT_DATASET_SCHEMAS,
    StockSelectDataError,
    freeze_stock_select_latest,
    parse_frozen_benchmark,
    parse_frozen_construction_weights,
    parse_frozen_landed_weights,
    parse_frozen_price_panel,
    parse_frozen_universe,
)


UTC = timezone.utc
GENERATED = datetime(2026, 1, 5, 16, tzinfo=UTC)
FETCHED = GENERATED - timedelta(minutes=1)
ACQUIRED = GENERATED + timedelta(minutes=1)


def _legacy_hash(entries):
    body = {
        key: {field: value for field, value in entry.items() if field != "fetched_at"}
        for key, entry in entries.items()
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _fixture(
    tmp_path,
    *,
    price_mutator=None,
    formal_benchmark=False,
    formal_provenance=True,
):
    project = tmp_path / "project"
    latest = project / "outputs" / "latest"
    latest.mkdir(parents=True)
    price = pd.DataFrame(
        {"B": [20.0, 21.0, 22.0], "A": [10.0, 11.0, 12.0], "C": [5.0, None, 6.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"]),
    )
    price.index.name = "Date"
    if price_mutator is not None:
        price = price_mutator(price)
    price_path = project / "cache" / "prices.parquet"
    price_path.parent.mkdir(parents=True)
    price.to_parquet(price_path)
    price_sha = hashlib.sha256(price_path.read_bytes()).hexdigest()
    entries = {
        "prices": {
            "vendor": "fixture_vendor",
            "field": "adjusted close",
            "path": str(price_path),
            "sha256": price_sha[:16],
            "effective_at": "2026-01-05",
            "released_at": "2026-01-05",
            "fetched_at": FETCHED.isoformat(),
            "currency": "USD",
            "unit": "price",
            "adjustment": "split & dividend adjusted",
            "finality": "final",
            "n_tickers": 3,
            "status": "fresh",
            "stale_reason": None,
        }
    }
    legacy = _legacy_hash(entries)
    manifest = {
        "generated_at": GENERATED.isoformat(),
        "entries": entries,
        "price_gaps": {},
        "status": "fresh",
        "stale": [],
        "hash": legacy,
    }
    positions = [
        {
            "ticker": "A",
            "target_model_weight": 0.6,
            "target_executable_weight": 0.3,
        },
        {
            "ticker": "B",
            "target_model_weight": 0.4,
            "target_executable_weight": 0.2,
        },
    ]
    run = {
        "run_id": "run-001",
        "portfolio_id": "portfolio-001",
        "asof_session": "2026-01-05",
        "generated_at": GENERATED.isoformat(),
        "data_manifest_hash": legacy,
        "validation_status": "NOT VALIDATED - fixture",
        "portfolio": {
            "construction": "risk_parity",
            "vol_target": 0.25,
            "scale": 0.5,
            "cash": 0.5,
            "invested": 0.5,
            "reconciles": True,
            "positions": positions,
        },
    }
    snapshot = {
        "run_id": "run-001",
        "portfolio_id": "portfolio-001",
        "asof_session": "2026-01-05",
        "generated_at": GENERATED.isoformat(),
        "construction": "risk_parity",
        "vol_target": 0.25,
        "scale": 0.5,
        "cash": 0.5,
        "weights": {"A": 0.3, "B": 0.2},
        "weights_model": {"A": 0.6, "B": 0.4},
        "validation_status": NOT_VALIDATED,
    }
    summary = {
        "run_id": "run-001",
        "portfolio_id": "portfolio-001",
        "generated_at": GENERATED.isoformat(),
        "risk_free": 0.04,
    }
    _json(latest / "run.json", run)
    _json(latest / "snapshot.json", snapshot)
    _json(latest / "data_manifest.json", manifest)
    _json(latest / "summary.json", summary)
    pd.DataFrame(
        {
            "recommended": {"A": 0.3, "B": 0.2},
            "risk_parity": {"A": 0.6, "B": 0.4},
            "equal_weight": {"A": 0.5, "B": 0.5},
        }
    ).to_csv(latest / "portfolio_weights.csv")
    pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "eligible": [True, True, False],
            "scarcity_strength": [0.7, 0.6, 0.2],
            "risk_score": [0.8, 0.5, 0.1],
        }
    ).to_csv(latest / "universe_scores.csv", index=False)
    if formal_benchmark:
        benchmark_path = project / "data" / "benchmark_weights.csv"
        benchmark_path.parent.mkdir()
        benchmark_payload = b"ticker,weight\nA,3\nB,1\n"
        benchmark_path.write_bytes(benchmark_payload)
        if formal_provenance:
            _json(
                benchmark_path.with_name("benchmark_weights.provenance.json"),
                {
                    "schema_version": FORMAL_BENCHMARK_PROVENANCE_SCHEMA_VERSION,
                    "mandate_id": "mandate-001",
                    "portfolio_id": "portfolio-001",
                    "effective_at": "2026-01-05T00:00:00+00:00",
                    "observed_at": "2026-01-05T15:00:00+00:00",
                    "acquired_at": "2026-01-05T15:30:00+00:00",
                    "finality": "confirmed",
                    "weights_sha256": hashlib.sha256(benchmark_payload).hexdigest(),
                    "provenance": {
                        "source_system": "fixture mandate registry",
                        "source_reference": "mandates/mandate-001/v1",
                    },
                    "approval": {
                        "approval_id": "approval-001",
                        "status": "approved",
                        "approved_by": "fixture-investment-committee",
                        "approved_at": "2026-01-05T14:00:00+00:00",
                    },
                },
            )
    return latest, price_path


def test_freezes_exact_five_roles_and_retries_with_stable_clock(tmp_path):
    latest, _price_path = _fixture(tmp_path)
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    frozen = freeze_stock_select_latest(latest, catalog, acquired_at=ACQUIRED)

    assert set(frozen.dataset_versions) == set(REQUIRED_STOCK_SELECT_DATASETS)
    assert frozen.context_dataset_versions == frozen.dataset_versions
    assert frozen.as_of == GENERATED
    assert frozen.run_started_at == ACQUIRED
    assert frozen.input_manifest_hash == canonical_hash(
        {role: frozen.dataset_versions[role] for role in sorted(frozen.dataset_versions)}
    )
    assert hashlib.sha256(frozen.canonical_manifest_bytes).hexdigest() == frozen.input_manifest_hash
    assert frozen.lineage_summary["complete"] is True
    assert frozen.lineage_summary["dataset_version_count"] == 5
    assert catalog.summary() == {
        "requests": 5,
        "versions": 5,
        "unique_payloads": 5,
        "by_finality": {"confirmed": 4, "final": 1},
    }

    run_ids = set()
    manifest_hashes = set()
    vintages = set()
    for role, reference in frozen.dataset_versions.items():
        version, request, _payload = catalog.get_version(reference.version_id)
        assert request["dataset"] == f"stock_select:{role}"
        assert request["schema_version"] == STOCK_SELECT_DATASET_SCHEMAS[role]
        assert version.schema_version == STOCK_SELECT_DATASET_SCHEMAS[role]
        run_ids.add(request["parameters"]["legacy_run_id"])
        manifest_hashes.add(request["parameters"]["source_manifest_sha256"])
        vintages.add(request["vintage_as_of"])
    assert run_ids == {"run-001"}
    assert len(manifest_hashes) == 1
    assert len(next(iter(manifest_hashes))) == 64
    assert vintages == {GENERATED.isoformat()}

    price_version, price_request, price_payload = catalog.get_version(
        frozen.dataset_versions["price_panel"].version_id
    )
    assert price_request["entitlement"] == "unverified"
    assert price_request["parameters"]["source_acquired_at"] == FETCHED.isoformat()
    assert price_request["parameters"]["format"] == "parquet"
    assert price_version.observed_at == FETCHED
    panel = parse_frozen_price_panel(price_payload, format="parquet")
    assert list(panel.columns) == ["A", "B", "C"]
    assert panel.index.is_monotonic_increasing

    for role, parser in {
        "construction_weights": parse_frozen_construction_weights,
        "landed_weights": parse_frozen_landed_weights,
        "universe": parse_frozen_universe,
        "benchmark": parse_frozen_benchmark,
    }.items():
        _version, _request, payload = catalog.get_version(
            frozen.dataset_versions[role].version_id
        )
        assert parser(payload) is not None
    _v, _r, landed_payload = catalog.get_version(
        frozen.dataset_versions["landed_weights"].version_id
    )
    landed = parse_frozen_landed_weights(landed_payload)
    assert landed["risk_free"] == 0.04
    assert len(landed["risk_free_source"]["content_sha256"]) == 64
    _v, _r, benchmark_payload = catalog.get_version(
        frozen.dataset_versions["benchmark"].version_id
    )
    benchmark = parse_frozen_benchmark(benchmark_payload)
    assert benchmark["kind"] == "research_proxy"
    assert benchmark["decision_usable"] is False
    assert benchmark["weights"] == {"A": 0.5, "B": 0.5}

    retry = freeze_stock_select_latest(
        latest, catalog, acquired_at=ACQUIRED + timedelta(days=1)
    )
    assert retry.dataset_versions == frozen.dataset_versions
    assert retry.run_started_at == frozen.run_started_at
    assert retry.input_manifest_hash == frozen.input_manifest_hash
    assert catalog.summary()["versions"] == 5


def test_formal_benchmark_is_explicit_and_normalized(tmp_path):
    latest, _ = _fixture(tmp_path, formal_benchmark=True)
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    frozen = freeze_stock_select_latest(latest, catalog, acquired_at=ACQUIRED)
    _version, request, payload = catalog.get_version(
        frozen.dataset_versions["benchmark"].version_id
    )
    benchmark = parse_frozen_benchmark(payload)
    assert benchmark["kind"] == "formal_mandate"
    assert benchmark["decision_usable"] is True
    assert benchmark["weights"] == {"A": 0.75, "B": 0.25}
    assert request["parameters"]["kind"] == "formal_mandate"
    assert len(request["parameters"]["source_content_sha256"]) == 64
    assert request["parameters"]["mandate_id"] == "mandate-001"
    assert request["parameters"]["mandate_approval_id"] == "approval-001"
    assert benchmark["source"]["portfolio_id"] == "portfolio-001"
    assert benchmark["source"]["provenance"]["source_system"] == "fixture mandate registry"
    assert benchmark["source"]["approval"]["status"] == "approved"


def test_plain_benchmark_csv_never_becomes_formal_mandate(tmp_path):
    latest, _ = _fixture(
        tmp_path,
        formal_benchmark=True,
        formal_provenance=False,
    )
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    with pytest.raises(StockSelectDataError, match="provenance envelope"):
        freeze_stock_select_latest(latest, catalog, acquired_at=ACQUIRED)
    assert catalog.summary()["versions"] == 0


def test_archive_ingestion_preserves_the_project_formal_benchmark(tmp_path):
    import shutil
    from market_intel.platform.service import PlatformService

    latest, _ = _fixture(tmp_path, formal_benchmark=True)
    snapshot = json.loads((latest / "snapshot.json").read_text())
    snapshot.update(top=["A", "B"], composite={"A": .6, "B": .4})
    _json(latest / "snapshot.json", snapshot)
    archived = latest.parent / "history/runs/run-001"
    shutil.copytree(latest, archived)
    service = PlatformService(latest_dir=latest, state_dir=tmp_path / "platform",
                              clock=lambda: ACQUIRED)
    frozen = service.sync_current_run(source_dir=archived)
    _, _, payload = service.catalog.get_version(frozen.context.dataset_versions["benchmark"].version_id)
    benchmark = parse_frozen_benchmark(payload)
    assert benchmark["kind"] == "formal_mandate"
    assert benchmark["weights"] == {"A": .75, "B": .25}


def test_archive_ingestion_does_not_hide_missing_formal_provenance(tmp_path):
    import shutil
    from market_intel.platform.service import PlatformBlocked, PlatformService

    latest, _ = _fixture(tmp_path, formal_benchmark=True, formal_provenance=False)
    archived = latest.parent / "history/runs/run-001"
    shutil.copytree(latest, archived)
    service = PlatformService(latest_dir=latest, state_dir=tmp_path / "platform",
                              clock=lambda: ACQUIRED)
    with pytest.raises(PlatformBlocked, match="provenance envelope"):
        service.sync_current_run(source_dir=archived)
    assert service.repository.runs() == []


@pytest.mark.parametrize(
    "defect",
    [
        "weights_sha256",
        "portfolio_id",
        "approval",
        "finality",
        "future_acquisition",
    ],
)
def test_invalid_formal_mandate_envelope_fails_before_registration(
    tmp_path, defect
):
    latest, _ = _fixture(tmp_path, formal_benchmark=True)
    provenance_path = (
        latest.parent.parent / "data" / "benchmark_weights.provenance.json"
    )
    provenance = json.loads(provenance_path.read_text())
    if defect == "weights_sha256":
        provenance["weights_sha256"] = "0" * 64
    elif defect == "portfolio_id":
        provenance["portfolio_id"] = "portfolio-other"
    elif defect == "approval":
        provenance["approval"]["status"] = "pending"
    elif defect == "finality":
        provenance["finality"] = "provisional"
    else:
        provenance["acquired_at"] = (GENERATED + timedelta(seconds=1)).isoformat()
    _json(provenance_path, provenance)
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")

    with pytest.raises(StockSelectDataError):
        freeze_stock_select_latest(latest, catalog, acquired_at=ACQUIRED)
    assert catalog.summary()["versions"] == 0


@pytest.mark.parametrize("failure", ["price_hash", "summary_identity", "manifest_identity"])
def test_invalid_projection_fails_before_any_catalog_registration(tmp_path, failure):
    latest, _ = _fixture(tmp_path)
    if failure == "price_hash":
        manifest = json.loads((latest / "data_manifest.json").read_text())
        manifest["entries"]["prices"]["sha256"] = "0" * 16
        manifest["hash"] = _legacy_hash(manifest["entries"])
        run = json.loads((latest / "run.json").read_text())
        run["data_manifest_hash"] = manifest["hash"]
        _json(latest / "data_manifest.json", manifest)
        _json(latest / "run.json", run)
    elif failure == "summary_identity":
        summary = json.loads((latest / "summary.json").read_text())
        summary["run_id"] = "another-run"
        _json(latest / "summary.json", summary)
    else:
        run = json.loads((latest / "run.json").read_text())
        run["data_manifest_hash"] = "0" * 16
        _json(latest / "run.json", run)
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    with pytest.raises(StockSelectDataError):
        freeze_stock_select_latest(latest, catalog, acquired_at=ACQUIRED)
    assert catalog.summary()["versions"] == 0


def test_price_parser_rejects_duplicate_sessions_zero_and_infinity():
    def csv_bytes(frame):
        return frame.to_csv().encode()

    duplicate = pd.DataFrame(
        {"A": [1.0, 2.0]}, index=pd.to_datetime(["2026-01-01", "2026-01-01"])
    )
    with pytest.raises(StockSelectDataError, match="unique"):
        parse_frozen_price_panel(csv_bytes(duplicate), format="csv")
    zero = pd.DataFrame({"A": [1.0, 0.0]}, index=pd.date_range("2026-01-01", periods=2))
    with pytest.raises(StockSelectDataError, match="positive"):
        parse_frozen_price_panel(csv_bytes(zero), format="csv")
    infinite = pd.DataFrame(
        {"A": [1.0, float("inf")]}, index=pd.date_range("2026-01-01", periods=2)
    )
    with pytest.raises(StockSelectDataError, match="infinity"):
        parse_frozen_price_panel(csv_bytes(infinite), format="csv")


def test_frozen_parsers_use_only_catalog_bytes_after_projection_changes(tmp_path):
    latest, price_path = _fixture(tmp_path)
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    frozen = freeze_stock_select_latest(latest, catalog, acquired_at=ACQUIRED)
    stored = {
        role: catalog.get_version(reference.version_id)[2]
        for role, reference in frozen.dataset_versions.items()
    }
    price_path.write_bytes(b"destroyed mutable source")
    (latest / "portfolio_weights.csv").write_text("destroyed", encoding="utf-8")
    (latest / "universe_scores.csv").write_text("destroyed", encoding="utf-8")

    assert parse_frozen_price_panel(stored["price_panel"], format="parquet").shape == (3, 3)
    assert parse_frozen_construction_weights(stored["construction_weights"]).shape == (2, 3)
    assert parse_frozen_universe(stored["universe"]).shape == (3, 3)
    assert parse_frozen_landed_weights(stored["landed_weights"])["run_id"] == "run-001"
    assert parse_frozen_benchmark(stored["benchmark"])["kind"] == "research_proxy"


def test_canonical_envelopes_reject_noncanonical_or_relabelled_validity(tmp_path):
    latest, _ = _fixture(tmp_path)
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    frozen = freeze_stock_select_latest(latest, catalog, acquired_at=ACQUIRED)
    _version, _request, benchmark_payload = catalog.get_version(
        frozen.dataset_versions["benchmark"].version_id
    )
    benchmark = json.loads(benchmark_payload)
    benchmark["decision_usable"] = True
    with pytest.raises(StockSelectDataError, match="decision_usable"):
        parse_frozen_benchmark(canonical_json(benchmark).encode())
    with pytest.raises(StockSelectDataError, match="canonical"):
        parse_frozen_benchmark(json.dumps(json.loads(benchmark_payload), indent=2).encode())
    legacy_formal = {
        "schema_version": "platform.stock-select.benchmark.v1",
        "kind": "formal_mandate",
        "decision_usable": True,
        "weights": {"A": 1.0},
        "source": {
            "type": "formal_file",
            "content_sha256": "a" * 64,
            "original_weight_sum": 1.0,
            "normalization": "divide by positive source sum",
        },
        "validation_status": NOT_VALIDATED,
    }
    with pytest.raises(StockSelectDataError, match="source fields"):
        parse_frozen_benchmark(canonical_json(legacy_formal).encode())
