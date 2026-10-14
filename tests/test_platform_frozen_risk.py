from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from market_intel.platform import DatasetRequest, canonical_json
from market_intel.platform.data import DatasetCatalog, FrozenDatasetBinding
from market_intel.platform.risk_adapter import (
    REQUIRED_FROZEN_DATASETS,
    StockSelectRiskLoader,
)
from market_intel.platform.stock_select_data import (
    BENCHMARK_SCHEMA_VERSION,
    STOCK_SELECT_DATASET_SCHEMAS,
)


UTC = timezone.utc
AS_OF = datetime(2026, 9, 3, 14, tzinfo=UTC)
UNIVERSE_PAYLOAD = b"ticker,eligible\nNVDA,True\n"
UNIVERSE_SHA256 = hashlib.sha256(UNIVERSE_PAYLOAD).hexdigest()
BENCHMARK_SOURCE = (
    "research_proxy:equal_weight_eligible_universe:" + UNIVERSE_SHA256
)


def _dataset_request(role: str) -> DatasetRequest:
    return DatasetRequest(
        provider="fixture",
        entitlement="research",
        dataset=f"stock_select:{role}",
        instrument_ids=("equity:US:NVDA",),
        fields=(role,),
        frequency="snapshot",
        adjustment="none",
        start=date(2023, 8, 29),
        end=date(2026, 9, 2),
        as_of=AS_OF,
        schema_version=STOCK_SELECT_DATASET_SCHEMAS[role],
        parameters={
            "role": role,
            "legacy_run_id": "run-001",
            "source_manifest_sha256": "f" * 64,
            **(
                {
                    "format": "canonical_json",
                    "kind": "research_proxy",
                    "decision_usable": False,
                    "source_content_sha256": UNIVERSE_SHA256,
                }
                if role == "benchmark"
                else {}
            ),
        },
    )


def _catalog_inputs(tmp_path, *, provisional_role: str | None = None):
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    refs = {}
    payloads = {}
    versions = {}
    for role in REQUIRED_FROZEN_DATASETS:
        request = _dataset_request(role)
        if role == "universe":
            payload = UNIVERSE_PAYLOAD
        elif role == "benchmark":
            payload = canonical_json(
                {
                    "schema_version": BENCHMARK_SCHEMA_VERSION,
                    "kind": "research_proxy",
                    "decision_usable": False,
                    "weights": {"NVDA": 1.0},
                    "source": {
                        "type": "equal_weight_eligible_universe",
                        "dataset_role": "universe",
                        "content_sha256": UNIVERSE_SHA256,
                        "note": "fixture proxy derived from frozen universe",
                    },
                    "validation_status": "NOT VALIDATED",
                }
            ).encode()
        else:
            payload = f"{role}:immutable-v1".encode()
        version = catalog.register(
            request,
            payload,
            effective_at=AS_OF - timedelta(days=1),
            observed_at=AS_OF - timedelta(minutes=2),
            acquired_at=AS_OF - timedelta(minutes=1),
            finality="provisional" if role == provisional_role else "confirmed",
        )
        refs[role] = version.to_ref(request.dataset)
        payloads[role] = payload
        versions[role] = version
    return catalog, refs, payloads, versions


def _request():
    return {
        "schema_version": "platform.risk.request.v1",
        "binding": {
            "schema_version": "platform.risk.binding.v1",
            "run_id": "run-001",
            "context_hash": "1" * 64,
            "as_of": AS_OF.isoformat(),
            "run_started_at": AS_OF.isoformat(),
            "strategy_id": "stock_select",
            "strategy_version": "0.1.0",
            "portfolio_id": "portfolio-001",
        },
        "run": {
            "run_id": "run-001",
            "portfolio_id": "portfolio-001",
            "portfolio": {"construction": "risk_parity"},
        },
        "required_metrics": {
            "var_99_1d": {
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 1,
                "confidence_level": 0.99,
            },
            "var_99_10d": {
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 10,
                "confidence_level": 0.99,
            },
            "es_97_5": {
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 10,
                "confidence_level": 0.975,
            },
            "tracking_error": {"window": "3y", "window_sessions": 756},
        },
    }


def _risk_result():
    window = {
        "requested_sessions": 756,
        "available": True,
        "start": "2023-08-29",
        "end": "2026-09-02",
        "n_sessions_used": 756,
        "calculation_sample_hash": "d" * 16,
        "horizons": {
            "1": {"overlapping": {"var_99": 0.031, "es_975": 0.039, "n_obs": 755}},
            "10": {
                "overlapping": {"var_99": 0.087, "es_975": 0.091, "n_obs": 746},
                "estimate_uncertainty": {
                    "tier": "official",
                    "primary_method": "asset_shock_bootstrap",
                    "var_99": {
                        "available": True,
                        "ci": 0.90,
                        "lo": 0.072,
                        "hi": 0.103,
                        "method": "asset-shock block bootstrap",
                    },
                },
            },
        },
        "tracking_error": [
            {
                "benchmark": "equal-weight current-universe proxy",
                "kind": "research proxy",
                "source": BENCHMARK_SOURCE,
                "available": True,
                "tracking_error_ann": 0.142,
                "n_days": 755,
                "definition": "annualized sample standard deviation of active return",
            }
        ],
    }
    return {
        "available": True,
        "construction": "recommended",
        "layer": "actual_run",
        "window": "3y",
        "precision_tier": "official",
        "config_hash": "e" * 16,
        "run": {"run_id": "run-001", "portfolio_id": "portfolio-001"},
        "risk": {
            "layers": {
                "actual_run": {
                    "available": True,
                    "layer": {
                        "is_actual_run": True,
                        "run_id": "run-001",
                        "basis": "frozen landed weights",
                    },
                    "session_index": {
                        "finality": {
                            "confirmed_through": "2026-09-02",
                            "acquired_after_close": False,
                            "wall_clock_independent": True,
                            "rule": "frozen acquisition timestamp",
                        }
                    },
                    "provenance": {
                        "data_snapshot_hash": "a" * 16,
                        "portfolio_weights_hash": "b" * 16,
                        "calculator_code_hash": "c" * 16,
                        "asof_last_confirmed_close": "2026-09-02",
                        "acquired_at": (AS_OF - timedelta(minutes=1)).isoformat(),
                        "finality_basis": "confirmed catalog versions",
                        "reconciliation": None,
                    },
                    "windows": {"3y": window},
                }
            }
        },
    }


def _lineage(binding_hash: str | None = None):
    lineage = {
        "data_snapshot_hash": "a" * 64,
        "portfolio_weights_hash": "b" * 64,
        "calculator_hash": "c" * 64,
        "calculation_sample_hash": "d" * 64,
        "asof": "2026-09-02",
        "acquired_at": (AS_OF - timedelta(minutes=1)).isoformat(),
        "finality": "confirmed catalog versions",
        "holding_first_bars": {"NVDA": "1999-01-22"},
    }
    if binding_hash is not None:
        lineage["frozen_input_binding_hash"] = binding_hash
    return lineage


def _legacy_loader(**changes):
    options = {
        "build_construction": lambda _name, **_kwargs: _risk_result(),
        "enumerate_constructions": lambda: {
            "recommended": {"is_canonical_executable": True}
        },
        "lineage_loader": lambda *_args: _lineage(),
        "official_bootstrap_reps": 2000,
    }
    options.update(changes)
    return StockSelectRiskLoader(**options)


def test_catalog_verifies_exact_refs_and_returns_immutable_complete_binding(tmp_path):
    catalog, refs, payloads, _versions = _catalog_inputs(tmp_path)
    binding = catalog.freeze_versions(
        refs,
        required_roles=REQUIRED_FROZEN_DATASETS,
        as_of=AS_OF,
    )

    assert isinstance(binding, FrozenDatasetBinding)
    assert binding.complete is True
    assert len(binding.binding_hash) == 64
    assert {role: binding.payload(role) for role in REQUIRED_FROZEN_DATASETS} == payloads
    proof = binding.lineage()
    assert proof["complete"] is True
    assert proof["binding_hash"] == binding.binding_hash
    assert tuple(proof["required_roles"]) == REQUIRED_FROZEN_DATASETS
    assert {
        role: proof["dataset_versions"][role]["version_id"]
        for role in REQUIRED_FROZEN_DATASETS
    } == {role: refs[role].version_id for role in REQUIRED_FROZEN_DATASETS}
    for ref in refs.values():
        assert catalog.verify_ref(ref, as_of=AS_OF)[0].version_id == ref.version_id


def test_lookup_uses_latest_effective_vintage_deterministically(tmp_path):
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    request = _dataset_request("price_panel")
    common = {
        "observed_at": AS_OF - timedelta(minutes=2),
        "acquired_at": AS_OF - timedelta(minutes=1),
        "finality": "confirmed",
    }
    future = catalog.register(
        request,
        b"future",
        effective_at=AS_OF + timedelta(days=1),
        **common,
    )
    newest = catalog.register(
        request,
        b"newest-effective",
        effective_at=AS_OF - timedelta(hours=1),
        **common,
    )
    catalog.register(
        request,
        b"older-effective",
        effective_at=AS_OF - timedelta(days=2),
        **common,
    )

    selected = catalog.lookup(request, AS_OF)
    assert selected == newest
    assert selected != future


def test_verify_ref_rejects_forged_reference_and_nonfinal_input(tmp_path):
    catalog, refs, _payloads, _versions = _catalog_inputs(tmp_path)
    forged = replace(refs["price_panel"], content_hash="0" * 64)
    with pytest.raises(IOError, match="does not match"):
        catalog.verify_ref(forged, as_of=AS_OF)

    provisional_catalog, provisional_refs, _payloads, _versions = _catalog_inputs(
        tmp_path / "provisional", provisional_role="benchmark"
    )
    with pytest.raises(ValueError, match="confirmed or final"):
        provisional_catalog.freeze_versions(
            provisional_refs,
            required_roles=REQUIRED_FROZEN_DATASETS,
            as_of=AS_OF,
        )


def test_mutable_workbench_path_cannot_publish_available_metrics():
    payload = _legacy_loader()(_request())

    assert payload["available"] is False
    assert payload["metrics"] == {}
    assert payload["unavailable"]["code"] == "FROZEN_RISK_INPUTS_UNVERIFIED"
    assert payload["lineage"]["complete"] is False
    assert payload["lineage"]["frozen_inputs_consumed"] is False
    assert "no dataset catalog binding" in payload["unavailable"]["reason"]
    assert payload["validation_status"] == "NOT VALIDATED"


def test_only_catalog_verified_inputs_reaching_frozen_builder_can_be_available(tmp_path):
    catalog, refs, payloads, _versions = _catalog_inputs(tmp_path)
    seen = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("mutable risk dependency must not be used")

    def frozen_builder(*, frozen_inputs, layer, window, bootstrap_reps, force):
        seen.append(frozen_inputs.binding_hash)
        assert layer == "actual_run" and window == "3y"
        assert bootstrap_reps == 2000 and force is False
        assert frozen_inputs.payload("price_panel") == payloads["price_panel"]
        result = _risk_result()
        result["frozen_input_binding_hash"] = frozen_inputs.binding_hash
        result["input_dataset_versions"] = {
            role: frozen_inputs.datasets[role].reference.version_id
            for role in REQUIRED_FROZEN_DATASETS
        }
        result["platform_lineage"] = _lineage(frozen_inputs.binding_hash)
        return result

    loader = StockSelectRiskLoader(
        build_construction=forbidden,
        build_frozen_construction=frozen_builder,
        enumerate_constructions=forbidden,
        lineage_loader=forbidden,
        dataset_catalog=catalog,
        dataset_versions_loader=lambda _request: {
            "context_hash": "1" * 64,
            "dataset_versions": refs,
        },
        official_bootstrap_reps=2000,
    )
    payload = loader(_request())

    assert payload["available"] is True
    assert payload["validation_status"] == "NOT VALIDATED"
    assert payload["lineage"]["complete"] is True
    assert payload["lineage"]["frozen_inputs_consumed"] is True
    assert payload["lineage"]["frozen_datasets"]["complete"] is True
    assert payload["lineage"]["frozen_input_binding_hash"] == seen[0]
    assert payload["construction"]["basis"]["landed_weights_source"] == (
        "catalog-verified frozen dataset role 'landed_weights'"
    )
    assert set(payload["metrics"]) == {
        "var_99_1d",
        "var_99_10d",
        "es_97_5",
        "tracking_error",
    }


@pytest.mark.parametrize("missing_role", REQUIRED_FROZEN_DATASETS)
def test_every_required_risk_input_role_is_mandatory(tmp_path, missing_role):
    catalog, refs, _payloads, _versions = _catalog_inputs(tmp_path)
    incomplete = {role: ref for role, ref in refs.items() if role != missing_role}
    payload = _legacy_loader(
        build_frozen_construction=lambda **_kwargs: pytest.fail(
            "incomplete frozen inputs reached the frozen builder"
        ),
        dataset_catalog=catalog,
        dataset_versions_loader=lambda _request: {
            "context_hash": "1" * 64,
            "dataset_versions": incomplete,
        },
    )(_request())

    assert payload["available"] is False
    assert payload["metrics"] == {}
    assert payload["unavailable"]["code"] == "FROZEN_RISK_INPUTS_UNVERIFIED"
    assert missing_role in payload["unavailable"]["reason"]


def test_catalog_refs_from_another_run_context_cannot_make_risk_available(tmp_path):
    catalog, refs, _payloads, _versions = _catalog_inputs(tmp_path)
    payload = _legacy_loader(
        build_frozen_construction=lambda **_kwargs: pytest.fail(
            "wrong-context frozen inputs reached the frozen builder"
        ),
        dataset_catalog=catalog,
        dataset_versions_loader=lambda _request: {
            "context_hash": "2" * 64,
            "dataset_versions": refs,
        },
    )(_request())

    assert payload["available"] is False
    assert payload["metrics"] == {}
    assert "context_hash does not match" in payload["unavailable"]["reason"]


def test_confirmed_role_from_another_legacy_run_never_reaches_builder(tmp_path):
    catalog, refs, payloads, _versions = _catalog_inputs(tmp_path)
    base_request = _dataset_request("price_panel")
    mixed_parameters = dict(base_request.parameters)
    mixed_parameters["legacy_run_id"] = "run-other"
    mixed_request = replace(base_request, parameters=mixed_parameters)
    replacement = catalog.register(
        mixed_request,
        payloads["price_panel"],
        effective_at=AS_OF - timedelta(days=1),
        observed_at=AS_OF - timedelta(minutes=2),
        acquired_at=AS_OF - timedelta(minutes=1),
        finality="confirmed",
    )
    mixed_refs = dict(refs)
    mixed_refs["price_panel"] = replacement.to_ref(mixed_request.dataset)
    payload = _legacy_loader(
        build_frozen_construction=lambda **_kwargs: pytest.fail(
            "mixed-run frozen inputs reached the frozen builder"
        ),
        dataset_catalog=catalog,
        dataset_versions_loader=lambda _request: {
            "context_hash": "1" * 64,
            "dataset_versions": mixed_refs,
        },
    )(_request())

    assert payload["available"] is False
    assert payload["unavailable"]["code"] == "FROZEN_RISK_INPUTS_UNVERIFIED"
    assert "legacy_run_id" in payload["unavailable"]["reason"]


def test_corrupt_content_addressed_blob_cannot_make_risk_available(tmp_path):
    catalog, refs, _payloads, versions = _catalog_inputs(tmp_path)
    Path(versions["price_panel"].blob_path).write_bytes(b"tampered")
    payload = _legacy_loader(
        build_frozen_construction=lambda **_kwargs: pytest.fail(
            "corrupt frozen inputs reached the frozen builder"
        ),
        dataset_catalog=catalog,
        dataset_versions_loader=lambda _request: {
            "context_hash": "1" * 64,
            "dataset_versions": refs,
        },
    )(_request())

    assert payload["available"] is False
    assert payload["metrics"] == {}
    assert payload["lineage"]["complete"] is False
    assert "checksum mismatch" in payload["unavailable"]["reason"]
