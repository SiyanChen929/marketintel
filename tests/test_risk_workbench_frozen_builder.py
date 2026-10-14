from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from market_intel import risk_workbench as RW
from market_intel.platform import DatasetRequest
from market_intel.platform.core import canonical_json
from market_intel.platform.data import DatasetCatalog
from market_intel.platform.risk_adapter import (
    REQUIRED_FROZEN_DATASETS,
    StockSelectRiskLoader,
)
from market_intel.platform.stock_select_data import (
    BENCHMARK_SCHEMA_VERSION,
    LANDED_WEIGHTS_SCHEMA_VERSION,
)


UTC = timezone.utc
SOURCE_ACQUIRED = datetime(2026, 9, 3, 21, 0, tzinfo=UTC)
CATALOG_ACQUIRED = datetime(2026, 9, 4, 15, 0, tzinfo=UTC)


def _frozen_binding(tmp_path):
    dates = pd.bdate_range(end="2026-09-02", periods=756)
    x = np.arange(len(dates), dtype=float)
    prices = pd.DataFrame(
        {
            "AAA": 100.0 * np.exp(np.cumsum(0.0004 + 0.012 * np.sin(x / 13.0))),
            "SOXX": 200.0 * np.exp(np.cumsum(0.0003 + 0.010 * np.cos(x / 17.0))),
        },
        index=dates,
    )
    prices.index.name = "date"
    price_payload = prices.to_csv().encode("utf-8")
    construction_payload = (
        "ticker,recommended,risk_parity\n"
        "AAA,0.5,1.0\n"
    ).encode("utf-8")
    universe_payload = (
        "ticker,eligible,segment,country\n"
        "AAA,True,Compute,United States\n"
    ).encode("utf-8")
    landed_payload = canonical_json(
        {
            "schema_version": LANDED_WEIGHTS_SCHEMA_VERSION,
            "run_id": "run-frozen-001",
            "portfolio_id": "portfolio-frozen-001",
            "construction": "risk_parity",
            "vol_target": 0.25,
            "scale": 0.5,
            "cash": 0.5,
            "invested": 0.5,
            "weights": {"AAA": 0.5},
            "model_weights": {"AAA": 1.0},
            "risk_free": 0.04,
            "risk_free_source": {
                "artifact": "summary.json",
                "field": "risk_free",
                "content_sha256": "a" * 64,
            },
            "validation_status": "NOT VALIDATED",
        }
    ).encode("utf-8")
    benchmark_payload = canonical_json(
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "kind": "research_proxy",
            "decision_usable": False,
            "weights": {"AAA": 1.0},
            "source": {
                "type": "equal_weight_eligible_universe",
                "dataset_role": "universe",
                "content_sha256": hashlib.sha256(universe_payload).hexdigest(),
                "note": "fixture proxy derived from the frozen eligible universe",
            },
            "validation_status": "NOT VALIDATED",
        }
    ).encode("utf-8")
    payloads = {
        "price_panel": price_payload,
        "construction_weights": construction_payload,
        "landed_weights": landed_payload,
        "universe": universe_payload,
        "benchmark": benchmark_payload,
    }
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    refs = {}
    schemas = {
        "price_panel": "stock-select.price-panel.v1",
        "construction_weights": "stock-select.construction-weights.v1",
        "landed_weights": LANDED_WEIGHTS_SCHEMA_VERSION,
        "universe": "stock-select.universe.v1",
        "benchmark": BENCHMARK_SCHEMA_VERSION,
    }
    for role in REQUIRED_FROZEN_DATASETS:
        parameters = {
            "role": role,
            "format": "csv",
            "legacy_run_id": "run-frozen-001",
            "source_manifest_sha256": "c" * 64,
        }
        if role == "price_panel":
            parameters.update(
                {
                    "source_acquired_at": SOURCE_ACQUIRED.isoformat(),
                }
            )
        if role == "benchmark":
            parameters.update(
                {
                    "format": "canonical_json",
                    "kind": "research_proxy",
                    "decision_usable": False,
                    "source_content_sha256": hashlib.sha256(
                        universe_payload
                    ).hexdigest(),
                }
            )
        request = DatasetRequest(
            provider="fixture",
            entitlement="research",
            dataset=f"stock_select:{role}",
            instrument_ids=("equity:US:AAA",),
            fields=(role,),
            frequency="run_snapshot",
            adjustment="none",
            start=date(2023, 1, 1),
            end=date(2026, 9, 2),
            as_of=SOURCE_ACQUIRED,
            schema_version=schemas[role],
            parameters=parameters,
        )
        version = catalog.register(
            request,
            payloads[role],
            effective_at=SOURCE_ACQUIRED - timedelta(days=1),
            observed_at=SOURCE_ACQUIRED,
            acquired_at=CATALOG_ACQUIRED,
            finality="confirmed",
        )
        refs[role] = version.to_ref(request.dataset)
    binding = catalog.freeze_versions(
        refs,
        required_roles=REQUIRED_FROZEN_DATASETS,
        as_of=SOURCE_ACQUIRED,
        acquired_by=CATALOG_ACQUIRED,
    )
    return catalog, refs, binding


def _risk_request(refs):
    return {
        "schema_version": "platform.risk.request.v1",
        "binding": {
            "schema_version": "platform.risk.binding.v1",
            "run_id": "run-frozen-001",
            "context_hash": "1" * 64,
            "as_of": SOURCE_ACQUIRED.isoformat(),
            "run_started_at": CATALOG_ACQUIRED.isoformat(),
            "strategy_id": "stock_select",
            "strategy_version": "0.1.0",
            "portfolio_id": "portfolio-frozen-001",
        },
        "run": {
            "run_id": "run-frozen-001",
            "portfolio_id": "portfolio-frozen-001",
            "portfolio": {"construction": "risk_parity"},
        },
        "dataset_context_hash": "1" * 64,
        "dataset_versions": refs,
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


def test_frozen_builder_consumes_all_five_versions_without_mutable_reads(
    tmp_path, monkeypatch
):
    catalog, refs, binding = _frozen_binding(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("mutable Stock Select input path was consulted")

    for target in (
        (RW.config, "run_info"),
        (RW.research, "price_panel"),
        (RW.research, "universe"),
        (RW.research, "summary"),
        (RW, "raw_run"),
        (RW, "benchmark_weights"),
        (RW, "enumerate_constructions"),
    ):
        monkeypatch.setattr(*target, forbidden)

    result = RW.build_frozen_construction(
        frozen_inputs=binding,
        layer="actual_run",
        window="3y",
        bootstrap_reps=0,
    )

    assert result is not None and result["available"] is True
    assert result["construction"] == "recommended"
    assert result["run"]["run_id"] == "run-frozen-001"
    assert result["acquisition"]["acquired_at"] == SOURCE_ACQUIRED.isoformat()
    assert result["acquisition"]["catalog_acquired_at"] == CATALOG_ACQUIRED.isoformat()
    assert result["frozen_input_binding_hash"] == binding.binding_hash
    assert result["input_dataset_versions"] == {
        role: refs[role].version_id for role in REQUIRED_FROZEN_DATASETS
    }
    receipt = result["frozen_input_receipt"]
    assert receipt["consumed_roles"] == list(REQUIRED_FROZEN_DATASETS)
    assert set(receipt["dataset_hashes"]) == set(REQUIRED_FROZEN_DATASETS)
    actual = result["risk"]["layers"]["actual_run"]
    assert actual["available"] is True
    assert actual["cash"]["annual_rate"] == 0.04
    assert actual["windows"]["3y"]["available"] is True
    lineage = result["platform_lineage"]
    assert len(lineage["data_snapshot_hash"]) == 64
    assert len(lineage["portfolio_weights_hash"]) == 64
    assert len(lineage["calculator_hash"]) == 64
    assert len(lineage["calculation_sample_hash"]) == 64
    assert lineage["data_snapshot_hash"].startswith(
        actual["provenance"]["data_snapshot_hash"]
    )
    assert lineage["portfolio_weights_hash"].startswith(
        actual["provenance"]["portfolio_weights_hash"]
    )


def test_real_frozen_builder_can_publish_adapter_metrics(tmp_path):
    catalog, refs, _binding = _frozen_binding(tmp_path)
    loader = StockSelectRiskLoader(
        build_frozen_construction=RW.build_frozen_construction,
        dataset_catalog=catalog,
        dataset_versions_loader=lambda request: {
            "context_hash": request["dataset_context_hash"],
            "dataset_versions": request["dataset_versions"],
        },
        official_bootstrap_reps=2000,
    )

    payload = loader(_risk_request(refs))

    assert payload["available"] is True
    assert payload["lineage"]["complete"] is True
    assert payload["lineage"]["frozen_inputs_consumed"] is True
    assert set(payload["metrics"]) == {
        "var_99_1d",
        "var_99_10d",
        "es_97_5",
        "tracking_error",
    }
    assert payload["metrics"]["var_99_1d"]["observations"] == 755
    assert payload["metrics"]["var_99_10d"]["observations"] == 746
    assert payload["metrics"]["tracking_error"]["observations"] == 755
    tracking_error = payload["metrics"]["tracking_error"]
    assert tracking_error["benchmark"] == "equal-weight current-universe proxy"
    assert tracking_error["benchmark_kind"] == "research proxy"
    assert tracking_error["decision_usable"] is False
    assert tracking_error["benchmark_version_id"] == refs["benchmark"].version_id
    assert (
        tracking_error["benchmark_content_sha256"]
        == refs["benchmark"].content_sha256
    )
    assert payload["validation_status"] == "NOT VALIDATED"


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("legacy_run_id", "run-other", "legacy_run_id"),
        ("source_manifest_sha256", "d" * 64, "source_manifest_sha256"),
        (
            "vintage_as_of",
            SOURCE_ACQUIRED - timedelta(minutes=1),
            "vintage",
        ),
    ],
)
def test_frozen_builder_rejects_mixed_role_lineage(
    tmp_path, field, value, match
):
    catalog, refs, _binding = _frozen_binding(tmp_path)
    old_version, old_request, payload = catalog.get_version(
        refs["benchmark"].version_id
    )
    parameters = dict(old_request["parameters"])
    if field != "vintage_as_of":
        parameters[field] = value
    request_as_of = value if field == "vintage_as_of" else SOURCE_ACQUIRED
    replacement_request = DatasetRequest(
        provider=old_request["provider"],
        entitlement=old_request["entitlement"],
        dataset=old_request["dataset"],
        instrument_ids=tuple(old_request["instrument_ids"]),
        fields=tuple(old_request["fields"]),
        frequency=old_request["frequency"],
        adjustment=old_request["adjustment"],
        start=date.fromisoformat(old_request["date_range"][0]),
        end=date.fromisoformat(old_request["date_range"][1]),
        as_of=request_as_of,
        schema_version=old_request["schema_version"],
        parameters=parameters,
    )
    replacement = catalog.register(
        replacement_request,
        payload,
        effective_at=old_version.effective_at,
        observed_at=old_version.observed_at,
        acquired_at=CATALOG_ACQUIRED,
        finality="confirmed",
    )
    mixed_refs = dict(refs)
    mixed_refs["benchmark"] = replacement.to_ref(replacement_request.dataset)
    mixed = catalog.freeze_versions(
        mixed_refs,
        required_roles=REQUIRED_FROZEN_DATASETS,
        as_of=SOURCE_ACQUIRED,
        acquired_by=CATALOG_ACQUIRED,
    )
    with pytest.raises(ValueError, match=match):
        RW.build_frozen_construction(
            frozen_inputs=mixed,
            layer="actual_run",
            window="3y",
            bootstrap_reps=0,
        )
