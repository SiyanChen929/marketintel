from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
from threading import Barrier, Event
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from market_intel.platform.core import (
    DatasetRequest,
    Finality,
    PositionIntent,
    canonical_hash,
    canonical_json,
)
from market_intel.platform.checkpoint import (
    FileLedgerCheckpointStore,
    HmacSha256Signer,
    LedgerCheckpointError,
)
from market_intel.platform.paper import PaperBroker, PaperError, PaperMandate, Quote
from market_intel.platform.risk_adapter import REQUIRED_FROZEN_DATASETS
from market_intel.platform.stock_select_data import (
    BENCHMARK_SCHEMA_VERSION,
    STOCK_SELECT_DATASET_SCHEMAS,
)
from market_intel.platform.service import (
    EXECUTION_QUOTE_DATASET,
    EXECUTION_QUOTE_FIELDS,
    EXECUTION_QUOTE_SCHEMA_VERSION,
    ExecutionQuote,
    PAPER_MUTATION_CHECKPOINT_PENDING,
    PAPER_MUTATION_COMMITTED,
    PlatformBlocked,
    PlatformInputError,
    PlatformService,
    RISK_BINDING_SCHEMA_VERSION,
    RISK_PAYLOAD_SCHEMA_VERSION,
    RISK_REQUEST_SCHEMA_VERSION,
)


UTC = timezone.utc
FIXTURE_UNIVERSE_PAYLOAD = b"ticker,eligible\nAAA,True\nBBB,True\n"
FIXTURE_UNIVERSE_SHA256 = hashlib.sha256(FIXTURE_UNIVERSE_PAYLOAD).hexdigest()
FIXTURE_BENCHMARK_SOURCE = (
    "research_proxy:equal_weight_eligible_universe:" + FIXTURE_UNIVERSE_SHA256
)
FORMAL_SOURCE_SHA256 = "a" * 64
FORMAL_EFFECTIVE_AT = "2026-09-03T00:00:00+00:00"
FORMAL_OBSERVED_AT = "2026-09-03T13:00:00+00:00"
FORMAL_ACQUIRED_AT = "2026-09-03T13:30:00+00:00"
FORMAL_BENCHMARK_SOURCE = (
    "formal_mandate:mandate-fixture:"
    + FORMAL_EFFECTIVE_AT
    + ":"
    + FORMAL_SOURCE_SHA256
)


def operation(key: str, body=None):
    payload = dict(body or {})
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return {**payload, "_operation_id": key, "_request_sha256": digest}


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def legacy_payloads(*, actionable: bool = True):
    generated = "2026-09-03T14:00:00+00:00"
    snapshot = {
        "run_id": "run-001",
        "portfolio_id": "portfolio-001",
        "date": "2026-09-03",
        "asof_session": "2026-09-03",
        "generated_at": generated,
        "top": ["AAA", "BBB"],
        "composite": {"AAA": 1.2, "BBB": 0.8},
        "validation_status": "NOT VALIDATED · fixture",
    }
    run = {
        "run_id": "run-001",
        "portfolio_id": "portfolio-001",
        "asof_session": "2026-09-03",
        "generated_at": generated,
        "status": "pending",
        "code_hash": "abc123",
        "data_manifest_hash": "legacy-hash",
        "validation_status": "NOT VALIDATED · fixture",
        "actionable": actionable,
        "not_actionable_reasons": [] if actionable else ["fixture price gap"],
        "portfolio": {
            "construction": "risk_parity",
            "cash": 0.1,
            "invested": 0.9,
            "reconciles": True,
            "positions": [
                {"ticker": "AAA", "target_executable_weight": 0.6},
                {"ticker": "BBB", "target_executable_weight": 0.3},
            ],
        },
    }
    return snapshot, run


def envelope(symbol: str, price: str, observed: datetime, *, finality=Finality.CONFIRMED):
    digest = hashlib.sha256(f"{symbol}:{price}:{observed.isoformat()}".encode()).hexdigest()
    return ExecutionQuote(
        quote=Quote(symbol, observed, Decimal(price), Decimal(price)),
        observed_at=observed,
        acquired_at=observed + timedelta(seconds=1),
        finality=finality,
        source="fixture.close",
        content_hash=digest,
        instrument_id=f"equity:US:{symbol}",
        dataset_version_id="0" * 64,
        request_hash="0" * 64,
    )


def catalog_envelope(svc: PlatformService, value: ExecutionQuote) -> ExecutionQuote:
    request = DatasetRequest(
        provider=value.source,
        entitlement="fixture-entitlement",
        dataset=EXECUTION_QUOTE_DATASET,
        instrument_ids=(value.instrument_id,),
        fields=EXECUTION_QUOTE_FIELDS,
        frequency="event",
        adjustment="raw",
        start=value.observed_at.date(),
        end=value.observed_at.date(),
        as_of=value.acquired_at,
        schema_version=EXECUTION_QUOTE_SCHEMA_VERSION,
        parameters={"currency": value.currency, "symbol": value.quote.symbol},
    )
    version = svc.catalog.register(
        request,
        canonical_json(value.artifact()).encode("utf-8"),
        effective_at=value.observed_at,
        observed_at=value.observed_at,
        acquired_at=value.acquired_at,
        finality=value.finality.value,
    )
    return replace(
        value,
        content_hash=version.content_sha256,
        dataset_version_id=version.version_id,
        request_hash=request.key,
    )


def catalog_quote_loader(svc: PlatformService, loader):
    def load(symbols, after, available_at):
        return {
            symbol: catalog_envelope(svc, value)
            for symbol, value in loader(symbols, after, available_at).items()
        }

    return load


def valid_risk_payload(request):
    frozen_lineage = request["frozen_dataset_lineage"]
    assert frozen_lineage is not None
    sample = {
        "start": "2023-09-05",
        "end": "2026-09-03",
        "calculation_sample_hash": "4" * 64,
    }
    benchmark_ref = request["dataset_versions"]["benchmark"]
    return {
        "schema_version": RISK_PAYLOAD_SCHEMA_VERSION,
        "available": True,
        "binding": dict(request["binding"]),
        "construction": {
            "name": "risk_parity",
            "layer": "actual_run",
            "window": "3y",
            "window_sessions": 756,
            "source_construction": "recommended",
            "basis": {
                "algorithm_identity": "run.portfolio.construction",
                "landed_weights_source": "fixture actual-run weights",
            },
            "weight_identity": "2" * 64,
        },
        "lineage": {
            "run_id": request["binding"]["run_id"],
            "context_hash": request["binding"]["context_hash"],
            "data_snapshot_hash": "1" * 64,
            "portfolio_weights_hash": "2" * 64,
            "calculator_hash": "3" * 64,
            "calculation_sample_hash": "4" * 64,
            "asof": "2026-09-03",
            "acquired_at": "2026-09-03T13:59:00+00:00",
            "finality": "confirmed close",
            "complete": True,
            "frozen_inputs_consumed": True,
            "input_manifest_hash": request["input_manifest_hash"],
            "frozen_datasets": frozen_lineage,
            "frozen_input_binding_hash": frozen_lineage["binding_hash"],
            "frozen_run_reconciliation": frozen_lineage["binding_hash"],
        },
        "metrics": {
            "var_99_1d": {
                "available": True,
                "value": 0.031,
                "unit": "fraction_of_nav",
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 1,
                "confidence_level": 0.99,
                "observations": 755,
                **sample,
            },
            "var_99_10d": {
                "available": True,
                "value": 0.087,
                "unit": "fraction_of_nav",
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 10,
                "confidence_level": 0.99,
                "observations": 746,
                **sample,
                "confidence_interval": {
                    "available": True,
                    "confidence_level": 0.95,
                    "lower": 0.072,
                    "upper": 0.103,
                    "unit": "fraction_of_nav",
                    "method": "asset-shock block bootstrap",
                },
            },
            "es_97_5": {
                "available": True,
                "value": 0.091,
                "unit": "fraction_of_nav",
                "window": "3y",
                "window_sessions": 756,
                "horizon_days": 10,
                "confidence_level": 0.975,
                "observations": 746,
                **sample,
            },
            "tracking_error": {
                "available": True,
                "value": 0.142,
                "unit": "annualized_fraction",
                "window": "3y",
                "window_sessions": 756,
                "annualization_sessions": 252,
                "observations": 755,
                "benchmark": "equal-weight current-universe proxy",
                "benchmark_kind": "research proxy",
                "benchmark_source": FIXTURE_BENCHMARK_SOURCE,
                "decision_usable": False,
                "benchmark_dataset_id": benchmark_ref["dataset_id"],
                "benchmark_version_id": benchmark_ref["version_id"],
                "benchmark_content_sha256": benchmark_ref.get(
                    "content_sha256", benchmark_ref["content_hash"]
                ),
                **sample,
            },
        },
    }


def unavailable_risk_payload(request):
    payload = valid_risk_payload(request)
    payload["available"] = False
    payload["metrics"] = {}
    payload["unavailable"] = {
        "code": "INSUFFICIENT_STRUCTURAL_HISTORY",
        "reason": "SNDK and ALAB do not cover the fixed 3y window",
        "limiting_holdings": [
            {
                "ticker": "SNDK",
                "first_bar": "2025-02-13",
                "required_start": "2023-08-29",
            }
        ],
        "window": "3y",
        "window_sessions": 756,
    }
    return payload


def valid_formal_risk_payload(request):
    payload = valid_risk_payload(request)
    benchmark_ref = request["dataset_versions"]["benchmark"]
    payload["metrics"]["tracking_error"].update(
        {
            "benchmark": "mandate benchmark",
            "benchmark_kind": "formal mandate",
            "benchmark_source": FORMAL_BENCHMARK_SOURCE,
            "decision_usable": True,
            "benchmark_dataset_id": benchmark_ref["dataset_id"],
            "benchmark_version_id": benchmark_ref["version_id"],
            "benchmark_content_sha256": benchmark_ref.get(
                "content_sha256", benchmark_ref["content_hash"]
            ),
        }
    )
    return payload


def service(
    tmp_path,
    clock: Clock,
    *,
    actionable=True,
    quote_loader=None,
    risk_loader=None,
    checkpoint_store=None,
    formal_benchmark=False,
    dataset_parameter_overrides=None,
):
    snapshot, run = legacy_payloads(actionable=actionable)

    def ingest_fixture_datasets(*, latest_dir, catalog, acquired_at):
        del latest_dir
        observed_at = datetime.fromisoformat(run["generated_at"])
        versions = {}
        for role in REQUIRED_FROZEN_DATASETS:
            dataset_id = f"stock_select:{role}"
            parameters = {
                "role": role,
                "legacy_run_id": run["run_id"],
                "source_manifest_sha256": "f" * 64,
            }
            if role == "benchmark" and formal_benchmark:
                parameters.update(
                    {
                        "format": "canonical_json",
                        "kind": "formal_mandate",
                        "decision_usable": True,
                        "source_content_sha256": FORMAL_SOURCE_SHA256,
                        "mandate_id": "mandate-fixture",
                        "mandate_effective_at": FORMAL_EFFECTIVE_AT,
                        "mandate_observed_at": FORMAL_OBSERVED_AT,
                        "mandate_acquired_at": FORMAL_ACQUIRED_AT,
                        "mandate_finality": "confirmed",
                        "mandate_approval_id": "approval-fixture",
                    }
                )
            elif role == "benchmark":
                parameters.update(
                    {
                        "format": "canonical_json",
                        "kind": "research_proxy",
                        "decision_usable": False,
                        "source_content_sha256": FIXTURE_UNIVERSE_SHA256,
                    }
                )
            if dataset_parameter_overrides and role in dataset_parameter_overrides:
                parameters.update(dataset_parameter_overrides[role])
            request = DatasetRequest(
                provider="fixture",
                entitlement="fixture-entitlement",
                dataset=dataset_id,
                instrument_ids=("equity:US:AAA", "equity:US:BBB"),
                fields=("fixture_payload",),
                frequency="run",
                adjustment="raw",
                start=observed_at.date(),
                end=observed_at.date(),
                as_of=observed_at,
                schema_version=STOCK_SELECT_DATASET_SCHEMAS[role],
                parameters=parameters,
            )
            if role == "universe":
                payload = FIXTURE_UNIVERSE_PAYLOAD
            elif role == "benchmark" and formal_benchmark:
                payload = canonical_json(
                    {
                        "schema_version": BENCHMARK_SCHEMA_VERSION,
                        "kind": "formal_mandate",
                        "decision_usable": True,
                        "weights": {"AAA": 0.6, "BBB": 0.4},
                        "source": {
                            "type": "formal_file",
                            "content_sha256": FORMAL_SOURCE_SHA256,
                            "original_weight_sum": 1.0,
                            "normalization": "divide by positive source sum",
                            "mandate_id": "mandate-fixture",
                            "portfolio_id": "portfolio-001",
                            "effective_at": FORMAL_EFFECTIVE_AT,
                            "observed_at": FORMAL_OBSERVED_AT,
                            "acquired_at": FORMAL_ACQUIRED_AT,
                            "finality": "confirmed",
                            "provenance": {
                                "source_system": "fixture mandate registry",
                                "source_reference": "mandates/fixture/v1",
                            },
                            "approval": {
                                "approval_id": "approval-fixture",
                                "status": "approved",
                                "approved_by": "fixture-investment-committee",
                                "approved_at": "2026-09-03T12:00:00+00:00",
                            },
                        },
                        "validation_status": "NOT VALIDATED",
                    }
                ).encode("utf-8")
            elif role == "benchmark":
                payload = canonical_json(
                    {
                        "schema_version": BENCHMARK_SCHEMA_VERSION,
                        "kind": "research_proxy",
                        "decision_usable": False,
                        "weights": {"AAA": 0.5, "BBB": 0.5},
                        "source": {
                            "type": "equal_weight_eligible_universe",
                            "dataset_role": "universe",
                            "content_sha256": FIXTURE_UNIVERSE_SHA256,
                            "note": "fixture proxy derived from frozen universe",
                        },
                        "validation_status": "NOT VALIDATED",
                    }
                ).encode("utf-8")
            else:
                payload = canonical_json(
                    {"role": role, "run_id": run["run_id"], "fixture": True}
                ).encode("utf-8")
            version_observed_at = (
                datetime.fromisoformat(FORMAL_OBSERVED_AT)
                if role == "benchmark" and formal_benchmark
                else observed_at
            )
            version_effective_at = (
                datetime.fromisoformat(FORMAL_EFFECTIVE_AT)
                if role == "benchmark" and formal_benchmark
                else observed_at
            )
            version = catalog.register(
                request,
                payload,
                effective_at=version_effective_at,
                observed_at=version_observed_at,
                acquired_at=acquired_at,
                finality=Finality.CONFIRMED.value,
            )
            versions[role] = version.to_ref(dataset_id)
        return SimpleNamespace(
            snapshot=snapshot,
            run=run,
            dataset_versions=versions,
            run_started_at=acquired_at,
            input_manifest_hash=canonical_hash(
                {role: versions[role] for role in sorted(versions)}
            ),
        )

    versioned_risk_fixture = risk_loader is not None
    svc = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=tmp_path / "state",
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        quote_loader=None,
        risk_loader=risk_loader,
        checkpoint_store=checkpoint_store,
        clock=clock,
        id_factory=lambda: "paper_fixture",
        dataset_ingestor=(ingest_fixture_datasets if versioned_risk_fixture else None),
        require_versioned_lineage=versioned_risk_fixture,
    )
    if quote_loader is not None:
        svc._quote_loader = catalog_quote_loader(svc, quote_loader)
    svc.sync_current_run()
    return svc


def checkpoint_store(tmp_path):
    return FileLedgerCheckpointStore(
        tmp_path / "separate-checkpoint-domain",
        HmacSha256Signer("fixture-witness", b"w" * 32),
    )


class FailOnceAfterGenesisCheckpoint:
    """One-shot witness outage used to exercise post-commit recovery."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.failed = False

    def latest(self, session_id):
        return self.delegate.latest(session_id)

    def verify_head(self, session_id, sequence, event_hash):
        return self.delegate.verify_head(session_id, sequence, event_hash)

    def publish(self, **kwargs):
        if kwargs["sequence"] > 1 and not self.failed:
            self.failed = True
            raise LedgerCheckpointError("injected witness outage")
        return self.delegate.publish(**kwargs)


class FailOnceWithCheckpointException:
    """Raise an arbitrary adapter/SDK exception after the genesis publish."""

    def __init__(self, delegate, error):
        self.delegate = delegate
        self.error = error
        self.failed = False

    def latest(self, session_id):
        return self.delegate.latest(session_id)

    def verify_head(self, session_id, sequence, event_hash):
        return self.delegate.verify_head(session_id, sequence, event_hash)

    def publish(self, **kwargs):
        if kwargs["sequence"] > 1 and not self.failed:
            self.failed = True
            raise self.error
        return self.delegate.publish(**kwargs)


class ToggleLatestCheckpointFailure:
    """Exercise an unknown adapter exception before any local mutation."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.fail_latest = False

    def latest(self, session_id):
        if self.fail_latest:
            raise RuntimeError("injected checkpoint SDK read failure")
        return self.delegate.latest(session_id)

    def verify_head(self, session_id, sequence, event_hash):
        return self.delegate.verify_head(session_id, sequence, event_hash)

    def publish(self, **kwargs):
        return self.delegate.publish(**kwargs)


def test_overview_registers_frozen_reference_run_and_declares_limits(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    svc = service(tmp_path, clock)
    overview = svc.overview()
    assert overview["status"] == "NOT VALIDATED/PAPER"
    assert overview["validation_status"] == "NOT VALIDATED"
    assert overview["live_trading_supported"] is False
    assert overview["broker_connections"] == 0
    assert overview["current_run"]["run_id"] == "run-001"
    assert len(overview["current_run"]["context_hash"]) == 64
    assert overview["lineage"]["complete"] is False
    assert overview["registry"] == {
        "strategies": 1,
        "runs": 1,
        "completed_runs": 1,
        "incomplete_runs": 0,
        "artifacts": 2,
    }
    assert {item["kind"] for item in svc.repository.get_run("run-001")["artifacts"]} == {
        "legacy_snapshot", "legacy_run"
    }
    registered_run = svc.runs()["items"][0]
    assert registered_run["strategy_version"] == "0.1.0"
    assert registered_run["context_hash"] == overview["current_run"]["context_hash"]
    strategies = svc.strategies()["items"]
    assert [row["strategy_id"] for row in strategies] == [
        "stock_select", "alpha_engine", "stable_alpha", "vol_trading", "pairs_trading",
        "commodity_arbitrage", "msci_rebalance",
    ]
    assert strategies[0]["installed"] is True
    assert all(row["validation_status"] == "NOT VALIDATED" for row in strategies)


def test_paper_exposes_unconfigured_independent_checkpoint_gap(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    svc = service(tmp_path, clock)
    sid = svc.create_paper_session(operation("create-no-witness"))["session"]["session_id"]
    status = svc.paper(sid)["session"]["external_checkpoint"]
    assert status["configured"] is False
    assert status["synchronized"] is False
    assert "same ledger trust domain" in status["reason"]


def test_signed_external_checkpoint_follows_each_committed_paper_generation(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def confirmed(symbols, after, available_at):
        observed = available_at - timedelta(seconds=1)
        return {symbol: envelope(symbol, "100", observed) for symbol in symbols}

    witness = checkpoint_store(tmp_path)
    svc = service(
        tmp_path,
        clock,
        quote_loader=confirmed,
        checkpoint_store=witness,
    )
    created = svc.create_paper_session(operation("create-with-witness"))
    sid = created["session"]["session_id"]
    assert created["external_checkpoint"]["synchronized"] is True
    assert created["external_checkpoint"]["sequence"] == 1

    clock.value += timedelta(minutes=1)
    stepped = svc.step_paper(sid, operation("step-with-witness"))
    state = svc.paper(sid)["session"]
    external = state["external_checkpoint"]
    assert stepped["external_checkpoint"] == external
    assert external["configured"] is True and external["synchronized"] is True
    assert external["sequence"] == state["ledger_head"]["sequence"]
    assert external["event_hash"] == state["ledger_head"]["event_hash"]
    witness.verify_head(sid, external["sequence"], external["event_hash"])


def test_committed_step_reports_checkpoint_pending_and_same_operation_repairs_it(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    durable_witness = checkpoint_store(tmp_path)
    flaky = FailOnceAfterGenesisCheckpoint(durable_witness)
    svc = service(
        tmp_path,
        clock,
        risk_loader=valid_risk_payload,
        checkpoint_store=flaky,
    )
    sid = svc.create_paper_session(
        operation("pending-create", {"starting_cash": "1000"})
    )["session"]["session_id"]
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)
    quotes = {
        symbol: catalog_envelope(svc, envelope(symbol, price, observed))
        for symbol, price in {"AAA": "100", "BBB": "50"}.items()
    }
    request = operation(
        "pending-step",
        {
            "quote_versions": {
                symbol: quote.dataset_version_id for symbol, quote in quotes.items()
            }
        },
    )
    clock.value = observed + timedelta(seconds=2)

    pending = svc.step_paper(sid, request)
    committed = svc.paper(sid)["session"]
    assert pending["mutation_state"] == PAPER_MUTATION_CHECKPOINT_PENDING
    assert pending["operation_receipt"]["sequence"] == committed["ledger_head"]["sequence"] == 4
    assert pending["external_checkpoint"]["synchronized"] is False
    assert "injected witness outage" in pending["external_checkpoint"]["reason"]
    assert len(committed["orders"]) == 2

    recovered = svc.step_paper(sid, request)
    final = svc.paper(sid)["session"]
    assert recovered["mutation_state"] == PAPER_MUTATION_COMMITTED
    assert final["event_count"] == 4
    assert len(final["orders"]) == 2
    assert final["external_checkpoint"]["synchronized"] is True
    durable_witness.verify_head(
        sid,
        final["ledger_head"]["sequence"],
        final["ledger_head"]["event_hash"],
    )


@pytest.mark.parametrize("error_type", (PaperError, RuntimeError))
def test_unknown_checkpoint_adapter_exception_after_commit_is_structured_pending(
    tmp_path, error_type
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def confirmed(symbols, after, available_at):
        observed = available_at - timedelta(seconds=1)
        return {symbol: envelope(symbol, "100", observed) for symbol in symbols}

    durable_witness = checkpoint_store(tmp_path)
    flaky = FailOnceWithCheckpointException(
        durable_witness,
        error_type("injected checkpoint adapter failure"),
    )
    svc = service(
        tmp_path,
        clock,
        quote_loader=confirmed,
        checkpoint_store=flaky,
    )
    sid = svc.create_paper_session(operation(f"create-{error_type.__name__}"))[
        "session"
    ]["session_id"]
    clock.value += timedelta(minutes=1)
    request = operation(f"step-{error_type.__name__}")

    pending = svc.step_paper(sid, request)
    state = svc.paper(sid)["session"]
    assert pending["mutation_state"] == PAPER_MUTATION_CHECKPOINT_PENDING
    assert pending["external_checkpoint"]["error_type"] == error_type.__name__
    assert error_type.__name__ in pending["external_checkpoint"]["reason"]
    assert state["event_count"] == 4
    assert state["operation_receipt_count"] == 1
    assert len(state["orders"]) == 2

    recovered = svc.step_paper(sid, request)
    final = svc.paper(sid)["session"]
    assert recovered["mutation_state"] == PAPER_MUTATION_COMMITTED
    assert final["event_count"] == 4
    assert final["operation_receipt_count"] == 1
    assert len(final["orders"]) == 2
    durable_witness.verify_head(
        sid,
        final["ledger_head"]["sequence"],
        final["ledger_head"]["event_hash"],
    )


def test_unknown_checkpoint_adapter_exception_before_commit_fails_closed(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    durable_witness = checkpoint_store(tmp_path)
    flaky = ToggleLatestCheckpointFailure(durable_witness)
    svc = service(tmp_path, clock, checkpoint_store=flaky)
    sid = svc.create_paper_session(operation("create-before-checkpoint-failure"))[
        "session"
    ]["session_id"]
    before = svc.paper(sid)["session"]["event_count"]
    flaky.fail_latest = True
    clock.value += timedelta(minutes=1)

    with pytest.raises(PlatformBlocked, match="checkpoint.*RuntimeError"):
        svc.step_paper(sid, operation("blocked-before-checkpoint"))

    assert PaperBroker.replay(svc.paper_dir / f"{sid}.jsonl").snapshot(
        asof=clock.value
    )["event_count"] == before


def test_missing_witness_for_established_ledger_never_reinitializes_genesis(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    witness = checkpoint_store(tmp_path)
    svc = service(
        tmp_path,
        clock,
        risk_loader=valid_risk_payload,
        checkpoint_store=witness,
    )
    sid = svc.create_paper_session(
        operation("missing-witness-create", {"starting_cash": "1000"})
    )["session"]["session_id"]
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)
    quotes = {
        symbol: catalog_envelope(svc, envelope(symbol, price, observed))
        for symbol, price in {"AAA": "100", "BBB": "50"}.items()
    }
    request = operation(
        "missing-witness-step",
        {
            "quote_versions": {
                symbol: quote.dataset_version_id for symbol, quote in quotes.items()
            }
        },
    )
    clock.value = observed + timedelta(seconds=2)
    committed = svc.step_paper(sid, request)
    assert committed["mutation_state"] == PAPER_MUTATION_COMMITTED
    checkpoint_path = (
        tmp_path
        / "separate-checkpoint-domain"
        / f"{sid}.checkpoints.jsonl"
    )
    assert len(checkpoint_path.read_text().splitlines()) == 2
    checkpoint_path.unlink()

    retry = svc.step_paper(sid, request)
    assert retry["mutation_state"] == PAPER_MUTATION_CHECKPOINT_PENDING
    assert "refusing to reinitialize a non-genesis head" in retry["external_checkpoint"]["reason"]
    assert not checkpoint_path.exists()
    before = svc.paper(sid)["session"]["event_count"]
    with pytest.raises(PlatformBlocked, match="refusing to reinitialize a non-genesis head"):
        svc.step_paper(sid, operation("new-step-after-witness-loss"))
    assert svc.paper(sid)["session"]["event_count"] == before
    assert not checkpoint_path.exists()


def test_pinned_catalog_quotes_drive_auditable_two_phase_paper_step(tmp_path):
    """Five frozen inputs and exact quote versions drive submit, fill and replay."""

    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    witness = checkpoint_store(tmp_path)
    svc = service(
        tmp_path,
        clock,
        risk_loader=valid_risk_payload,
        checkpoint_store=witness,
    )
    lineage = svc.overview()["lineage"]
    assert lineage["complete"] is True
    assert lineage["dataset_version_count"] == 5
    assert set(lineage["roles"]) == set(REQUIRED_FROZEN_DATASETS)
    sid = svc.create_paper_session(
        operation(
            "create-pinned-quotes",
            {"starting_cash": "1000", "mode": "SHADOW"},
        )
    )["session"]["session_id"]

    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)
    first_quotes = {
        symbol: catalog_envelope(svc, envelope(symbol, price, observed))
        for symbol, price in {"AAA": "100", "BBB": "50"}.items()
    }
    clock.value = observed + timedelta(seconds=2)
    submitted = svc.step_paper(
        sid,
        operation(
            "pinned-submit",
            {
                "quote_versions": {
                    symbol: quote.dataset_version_id
                    for symbol, quote in first_quotes.items()
                }
            },
        ),
    )
    assert len(submitted["orders"]) == 2 and submitted["fills"] == []
    assert len(submitted["quote_snapshot_hash"]) == 64
    assert submitted["quote_snapshot"]["snapshot_hash"] == submitted["quote_snapshot_hash"]
    assert submitted["external_checkpoint"]["synchronized"] is True

    later = datetime(2026, 9, 3, 14, 3, tzinfo=UTC)
    second_quotes = {
        symbol: catalog_envelope(svc, envelope(symbol, price, later))
        for symbol, price in {"AAA": "101", "BBB": "49"}.items()
    }
    clock.value = later + timedelta(seconds=2)
    filled = svc.step_paper(
        sid,
        operation(
            "pinned-fill",
            {
                "quote_versions": {
                    symbol: quote.dataset_version_id
                    for symbol, quote in second_quotes.items()
                }
            },
        ),
    )
    assert len(filled["fills"]) == 2
    assert filled["phase"] in {"fills_processed", "fills_processed_and_orders_submitted"}
    assert filled["session"]["reconciliation"]["ok"] is True
    assert filled["quote_snapshot_hash"] != submitted["quote_snapshot_hash"]
    state = svc.paper(sid)["session"]
    replayed = PaperBroker.replay(svc.paper_dir / f"{sid}.jsonl").snapshot(
        asof=clock.value
    )
    assert state["mode"] == "SHADOW"
    assert replayed["reconciliation"]["ok"] is True
    assert replayed["cash"] == state["cash"]
    assert state["external_checkpoint"]["synchronized"] is True
    assert state["external_checkpoint"]["sequence"] == state["ledger_head"]["sequence"]
    witness.verify_head(
        sid,
        state["ledger_head"]["sequence"],
        state["ledger_head"]["event_hash"],
    )


def test_all_service_reads_are_pure_after_explicit_sync(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    svc = service(tmp_path, clock, risk_loader=valid_risk_payload)
    sid = svc.create_paper_session(operation("create-read-purity"))["session"]["session_id"]
    ledger = svc.paper_dir / f"{sid}.jsonl"
    head = ledger.with_name(ledger.name + ".head")
    repository_before = svc.repository.summary()
    run_before = svc.repository.get_run("run-001")
    ledger_before = ledger.read_bytes()
    head_before = head.read_bytes()

    assert svc.overview()["current_run"]["run_id"] == "run-001"
    assert svc.strategies()["items"][0]["installed"] is True
    assert svc.runs()["items"][0]["run_id"] == "run-001"
    assert svc.risk()["risk"]["available"] is True
    assert svc.paper()["sessions"][0]["session_id"] == sid
    assert svc.paper(sid)["session"]["session_id"] == sid

    run_after = svc.repository.get_run("run-001")
    assert svc.repository.summary() == repository_before
    assert run_after == run_before
    assert run_after["event_count"] == run_before["event_count"]
    assert run_after["event_head_hash"] == run_before["event_head_hash"]
    assert ledger.read_bytes() == ledger_before
    assert head.read_bytes() == head_before


def test_cross_payload_identity_is_rejected_before_repository_pollution(tmp_path):
    snapshot, run = legacy_payloads()
    snapshot["run_id"] = "snapshot-other"
    svc = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=tmp_path / "state",
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        clock=lambda: datetime(2026, 9, 3, 14, 2, tzinfo=UTC),
        require_versioned_lineage=False,
    )
    overview = svc.overview()
    assert overview["current_run"] is None
    assert any("run_id does not match" in reason for reason in overview["blockers"])
    assert svc.repository.summary() == {
        "strategies": 0,
        "runs": 0,
        "completed_runs": 0,
        "incomplete_runs": 0,
        "artifacts": 0,
    }


def test_snapshot_and_run_generation_identity_must_match(tmp_path):
    snapshot, run = legacy_payloads()
    snapshot["generated_at"] = "2026-09-03T14:00:01+00:00"
    svc = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=tmp_path / "state",
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        require_versioned_lineage=False,
    )
    assert svc.overview()["current_run"] is None
    assert svc.repository.summary()["runs"] == 0


def test_same_legacy_run_id_cannot_silently_change_frozen_target_payload(tmp_path):
    snapshot, run = legacy_payloads(actionable=True)
    svc = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=tmp_path / "state",
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        clock=lambda: datetime(2026, 9, 3, 14, 2, tzinfo=UTC),
        require_versioned_lineage=False,
    )
    svc.sync_current_run()
    first = svc.overview()
    assert first["current_run"]["run_id"] == "run-001"
    original_hash = first["current_run"]["context_hash"]
    run["portfolio"]["positions"][0]["target_executable_weight"] = 0.55
    run["portfolio"]["positions"][1]["target_executable_weight"] = 0.35
    second = svc.overview()
    assert second["current_run"] is None
    assert any("does not match its frozen RunContext" in reason for reason in second["blockers"])
    assert svc.repository.get_run("run-001")["context_hash"] == original_hash


def test_current_production_projection_is_fail_closed(tmp_path):
    """A NOT ACTIONABLE projection blocks before quotes, independent of today's refreshed output."""
    from test_platform_stock_select_data import _fixture

    latest, _ = _fixture(tmp_path)
    run_path = latest / "run.json"
    run = json.loads(run_path.read_text())
    run.update(actionable=False, not_actionable_reasons=["recent price gaps: fixture"])
    run_path.write_text(json.dumps(run))
    snapshot_path = latest / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    snapshot.update(top=["A", "B"], composite={"A": 1.2, "B": 0.8})
    snapshot_path.write_text(json.dumps(snapshot))

    calls = []
    svc = PlatformService(
        latest_dir=latest,
        state_dir=tmp_path / "state",
        quote_loader=lambda *args: calls.append(args) or {},
        clock=lambda: datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        id_factory=lambda: "paper_production_gate",
    )
    svc.sync_current_run()
    created = svc.create_paper_session(operation("create-production-gate"))
    session_id = created["session"]["session_id"]
    with pytest.raises(PlatformBlocked, match="paper step blocked: recent price gaps"):
        svc.step_paper(session_id, operation("step-production-gate"))
    assert calls == []
    unchanged = svc.paper(session_id)["session"]
    assert unchanged["event_count"] == 1
    assert unchanged["orders"] == [] and unchanged["fills"] == []


def test_actionable_legacy_run_still_blocks_without_versioned_signal_lineage(tmp_path):
    calls = []
    snapshot, run = legacy_payloads(actionable=True)
    svc = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=tmp_path / "state",
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        quote_loader=lambda *args: calls.append(args) or {},
        clock=lambda: datetime(2026, 9, 3, 14, 2, tzinfo=UTC),
        id_factory=lambda: "paper_lineage_gate",
    )
    svc.sync_current_run()
    overview = svc.overview()
    assert overview["capabilities"]["paper_step_ready"] is False
    assert any("content-addressed dataset versions" in item for item in overview["blockers"])
    sid = svc.create_paper_session(operation("create-lineage-gate"))["session"]["session_id"]
    with pytest.raises(PlatformBlocked, match="no content-addressed dataset versions"):
        svc.step_paper(sid, operation("step-lineage-gate"))
    assert calls == []
    state = svc.paper(sid)["session"]
    assert state["event_count"] == 1
    assert state["orders"] == [] and state["fills"] == []


def test_paper_step_requires_lineage_bound_confirmed_quotes_before_mutation(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)

    def provisional(symbols, after, available_at):
        return {
            "AAA": envelope("AAA", "100", observed, finality=Finality.PROVISIONAL),
            "BBB": envelope("BBB", "50", observed, finality=Finality.CONFIRMED),
        }

    svc = service(tmp_path, clock, quote_loader=provisional)
    sid = svc.create_paper_session(operation("create-provisional"))["session"]["session_id"]
    clock.value = observed + timedelta(seconds=2)
    with pytest.raises(PlatformBlocked, match="AAA.*provisional"):
        svc.step_paper(sid, operation("step-provisional"))
    state = svc.paper(sid)["session"]
    assert state["event_count"] == 1
    assert state["reconciliation"]["ok"] is True


def test_quote_digest_string_without_catalog_artifact_is_rejected_before_mutation(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)
    svc = service(tmp_path, clock)
    svc._quote_loader = lambda symbols, *_: {
        symbol: envelope(symbol, "100", observed) for symbol in symbols
    }
    sid = svc.create_paper_session(operation("create-unregistered-quote"))["session"]["session_id"]
    ledger = svc.paper_dir / f"{sid}.jsonl"
    before = (ledger.read_bytes(), ledger.with_name(ledger.name + ".head").read_bytes())
    clock.value = observed + timedelta(seconds=2)

    with pytest.raises(PlatformBlocked, match="not backed by a verified DatasetCatalog version"):
        svc.step_paper(sid, operation("step-unregistered-quote"))

    assert svc.paper(sid)["session"]["event_count"] == 1
    assert (ledger.read_bytes(), ledger.with_name(ledger.name + ".head").read_bytes()) == before


def test_catalog_quote_blob_tamper_is_rejected_before_fill_or_mark(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)
    svc = service(tmp_path, clock)

    def tampered(symbols, *_):
        values = {
            symbol: catalog_envelope(svc, envelope(symbol, "100", observed))
            for symbol in symbols
        }
        victim = values[symbols[0]]
        version, _, _ = svc.catalog.get_version(victim.dataset_version_id)
        Path(version.blob_path).write_bytes(b"tampered quote bytes")
        return values

    svc._quote_loader = tampered
    sid = svc.create_paper_session(operation("create-tampered-quote"))["session"]["session_id"]
    clock.value = observed + timedelta(seconds=2)
    with pytest.raises(PlatformBlocked, match="checksum mismatch"):
        svc.step_paper(sid, operation("step-tampered-quote"))
    assert svc.paper(sid)["session"]["event_count"] == 1


def test_catalog_quote_request_identity_mismatch_is_rejected(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)
    svc = service(tmp_path, clock)

    def mismatched(symbols, *_):
        return {
            symbol: replace(
                catalog_envelope(svc, envelope(symbol, "100", observed)),
                request_hash="f" * 64,
            )
            for symbol in symbols
        }

    svc._quote_loader = mismatched
    sid = svc.create_paper_session(operation("create-mismatched-request"))["session"]["session_id"]
    clock.value = observed + timedelta(seconds=2)
    with pytest.raises(PlatformBlocked, match="request identity mismatch"):
        svc.step_paper(sid, operation("step-mismatched-request"))
    assert svc.paper(sid)["session"]["event_count"] == 1


def test_paper_step_executes_after_signal_and_reconciles_cash_positions_and_costs(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)
    later = datetime(2026, 9, 3, 14, 3, tzinfo=UTC)

    def confirmed(symbols, after, available_at):
        assert symbols == ("AAA", "BBB")
        assert available_at == clock.value
        quote_time = later if after >= observed else observed
        return {
            "AAA": envelope("AAA", "100", quote_time),
            "BBB": envelope("BBB", "50", quote_time),
        }

    svc = service(tmp_path, clock, quote_loader=confirmed)
    created = svc.create_paper_session(operation("create-execution", {"starting_cash": "1000"}))
    sid = created["session"]["session_id"]
    assert created["session"]["binding"] == {
        "workspace_id": "market_intel",
        "strategy_id": "stock_select",
        "portfolio_id": "portfolio-001",
    }
    clock.value = observed + timedelta(seconds=2)
    submitted = svc.step_paper(sid, operation("step-submit"))
    assert submitted["status"] == "NOT VALIDATED/PAPER"
    assert submitted["live_trading_supported"] is False
    assert submitted["retroactive_fills_allowed"] is False
    assert datetime.fromisoformat(submitted["signal_time"]) < datetime.fromisoformat(submitted["order_time"])
    assert len(submitted["orders"]) == 2 and submitted["fills"] == []
    assert {row["run_id"] for row in submitted["orders"]} == {"run-001"}
    assert {row["context_hash"] for row in submitted["orders"]} == {submitted["context_hash"]}
    assert {row["finality"] for row in submitted["quote_lineage"]} == {"confirmed"}
    assert all(len(row["dataset_version_id"]) == 64 for row in submitted["quote_lineage"])
    assert all(len(row["request_hash"]) == 64 for row in submitted["quote_lineage"])
    assert submitted["session"]["cash"] == "1000"
    assert submitted["session"]["positions"] == []

    # Only a later operator step can consume a quote observed after the orders.
    clock.value = later + timedelta(seconds=2)
    result = svc.step_paper(sid, operation("step-fill"))
    assert result["orders"] == [] and len(result["fills"]) == 2
    assert all(
        datetime.fromisoformat(row["order_time"])
        < datetime.fromisoformat(fill["quote_time"])
        for row, fill in zip(sorted(submitted["orders"], key=lambda x: x["symbol"]),
                             sorted(result["fills"], key=lambda x: x["symbol"]))
    )
    state = result["session"]
    assert state["cash"] == "100.00000000"
    assert state["equity"] == "1000.00000000"
    assert Decimal(state["costs"]["total"]) == 0
    assert state["reconciliation"]["ok"] is True
    assert {row["symbol"]: row["quantity"] for row in state["positions"]} == {
        "AAA": "6.00000000", "BBB": "6.00000000"
    }
    replayed = svc.paper(sid)["session"]
    assert replayed["cash"] == state["cash"]
    assert replayed["event_count"] == state["event_count"]


def test_operator_step_never_backfills_an_order_with_pre_click_quotes(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 15, 0, tzinfo=UTC))

    def confirmed(symbols, after, available_at):
        observed = available_at - timedelta(minutes=1)
        assert observed > after
        return {symbol: envelope(symbol, "100", observed) for symbol in symbols}

    svc = service(tmp_path, clock, quote_loader=confirmed)
    sid = svc.create_paper_session(operation("create-backfill", {"starting_cash": "1000"}))["session"]["session_id"]
    first = svc.step_paper(sid, operation("step-backfill-submit"))
    assert len(first["orders"]) == 2
    assert first["fills"] == []
    assert first["order_time"] == clock.value.isoformat()
    assert first["retroactive_fills_allowed"] is False

    clock.value += timedelta(minutes=2)
    second = svc.step_paper(sid, operation("step-backfill-fill"))
    assert second["fills"]
    orders = {row["symbol"]: row for row in first["orders"]}
    for fill in second["fills"]:
        assert datetime.fromisoformat(fill["quote_time"]) > datetime.fromisoformat(
            orders[fill["symbol"]]["order_time"]
        )


def test_paper_listing_never_exposes_an_uncommitted_staged_generation(
    tmp_path, monkeypatch
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def confirmed(symbols, after, available_at):
        observed = available_at - timedelta(seconds=1)
        return {symbol: envelope(symbol, "100", observed) for symbol in symbols}

    svc = service(tmp_path, clock, quote_loader=confirmed)
    sid = svc.create_paper_session(operation("create-private-stage"))["session"]["session_id"]
    entered, release = Event(), Event()
    original = PlatformService._execute_paper_cycle

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(PlatformService, "_execute_paper_cycle", staticmethod(paused))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(svc.step_paper, sid, operation("step-private-stage"))
        assert entered.wait(timeout=10)
        during = svc.paper()
        release.set()
        pending.result(timeout=10)

    assert during["count"] == 1
    assert [row["session_id"] for row in during["sessions"]] == [sid]
    assert list(svc.paper_stage_dir.iterdir()) == []


def test_paper_listing_filters_garbage_symlinks_and_forged_session_files(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    svc = service(tmp_path, clock)
    sid = svc.create_paper_session(operation("create-list-filter"))["session"]["session_id"]
    ledger = svc.paper_dir / f"{sid}.jsonl"

    (svc.paper_dir / "notes.jsonl").write_text("not a ledger")
    noncanonical = svc.paper_dir / "paper_not-a-digest.jsonl"
    shutil.copyfile(ledger, noncanonical)
    shutil.copyfile(ledger.with_name(ledger.name + ".head"), noncanonical.with_name(noncanonical.name + ".head"))

    symlink = svc.paper_dir / f"paper_{'e' * 32}.jsonl"
    symlink.symlink_to(ledger)

    forged = svc.paper_dir / f"paper_{'f' * 32}.jsonl"
    shutil.copyfile(ledger, forged)
    shutil.copyfile(ledger.with_name(ledger.name + ".head"), forged.with_name(forged.name + ".head"))
    forged.with_name(forged.name + ".lock").touch()

    listing = svc.paper()
    assert listing["count"] == 1
    assert [row["session_id"] for row in listing["sessions"]] == [sid]
    assert listing["corrupt_ledgers"] == [
        {
            "session_id": forged.stem,
            "error": "paper ledger session_id does not match its canonical file identity",
        }
    ]


def test_kill_switch_is_persistent_and_blocks_later_steps(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    svc = service(tmp_path, clock)
    sid = svc.create_paper_session(operation("create-kill"))["session"]["session_id"]
    killed = svc.kill_switch(
        sid, operation("kill-drill", {"reason": "operator drill"})
    )
    assert killed["session"]["kill_switch_active"] is True
    assert killed["session"]["kill_switch_reason"] == "operator drill"
    assert svc.paper(sid)["session"]["kill_switch_active"] is True


def test_unbounded_quote_age_is_rejected_as_input(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    svc = service(tmp_path, clock)
    with pytest.raises(PlatformInputError):
        svc.create_paper_session(
            operation("create-invalid-age", {"max_quote_age_seconds": "1e999"})
        )


def test_risk_endpoint_is_explicit_about_missing_versioned_adapter(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    payload = service(tmp_path, clock).risk()
    assert payload["status"] == "NOT VALIDATED/PAPER"
    assert payload["risk"]["available"] is False
    assert payload["risk"]["required_metrics"] == [
        "VaR 99% 1-day", "VaR 99% 10-day", "Expected Shortfall 97.5%", "Tracking Error"
    ]
    assert payload["attribution"]["attributions"] == []
    assert "continuous-ledger attribution" in payload["attribution"]["reason"]


def test_risk_adapter_receives_versioned_request_and_valid_payload_becomes_available(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    seen = []

    def loader(request):
        seen.append(request)
        return valid_risk_payload(request)

    payload = service(tmp_path, clock, risk_loader=loader).risk()
    assert payload["risk"]["available"] is True
    assert payload["risk"]["reason"] is None
    assert payload["risk"]["payload"]["schema_version"] == RISK_PAYLOAD_SCHEMA_VERSION
    assert len(seen) == 1
    request = seen[0]
    assert request["schema_version"] == RISK_REQUEST_SCHEMA_VERSION
    assert request["binding"]["schema_version"] == RISK_BINDING_SCHEMA_VERSION
    assert request["binding"]["run_id"] == "run-001"
    assert request["binding"]["strategy_id"] == "stock_select"
    assert request["binding"]["strategy_version"] == "0.1.0"
    assert request["binding"]["portfolio_id"] == "portfolio-001"
    assert len(request["binding"]["context_hash"]) == 64
    assert set(request["required_metrics"]) == {
        "var_99_1d", "var_99_10d", "es_97_5", "tracking_error"
    }
    assert payload["risk"]["adapter_contract"] == {
        "request_schema_version": RISK_REQUEST_SCHEMA_VERSION,
        "payload_schema_version": RISK_PAYLOAD_SCHEMA_VERSION,
        "binding_schema_version": RISK_BINDING_SCHEMA_VERSION,
        "required_metric_keys": [
            "var_99_1d", "var_99_10d", "es_97_5", "tracking_error"
        ],
    }


def test_risk_adapter_accepts_explicit_frozen_formal_mandate_identity(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    result = service(
        tmp_path,
        clock,
        risk_loader=valid_formal_risk_payload,
        formal_benchmark=True,
    ).risk()

    assert result["risk"]["available"] is True
    tracking_error = result["risk"]["payload"]["metrics"]["tracking_error"]
    assert tracking_error["benchmark_kind"] == "formal mandate"
    assert tracking_error["benchmark_source"] == FORMAL_BENCHMARK_SOURCE
    assert tracking_error["decision_usable"] is True


def test_service_rejects_mixed_frozen_role_lineage_even_if_adapter_claims_valid(
    tmp_path,
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    result = service(
        tmp_path,
        clock,
        risk_loader=valid_risk_payload,
        dataset_parameter_overrides={
            "price_panel": {"legacy_run_id": "run-other"}
        },
    ).risk()

    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert "legacy_run_id" in result["risk"]["reason"]


def test_risk_adapter_preserves_bound_structural_history_unavailability(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    result = service(
        tmp_path, clock, risk_loader=unavailable_risk_payload
    ).risk()
    assert result["risk"]["available"] is False
    payload = result["risk"]["payload"]
    assert payload["available"] is False
    assert payload["binding"]["run_id"] == "run-001"
    assert payload["construction"]["name"] == "risk_parity"
    assert payload["construction"]["source_construction"] == "recommended"
    assert payload["construction"]["weight_identity"] == payload["lineage"]["portfolio_weights_hash"]
    assert payload["unavailable"]["code"] == "INSUFFICIENT_STRUCTURAL_HISTORY"
    assert payload["unavailable"]["limiting_holdings"][0]["ticker"] == "SNDK"
    assert result["risk"]["reason"] == payload["unavailable"]["reason"]
    assert result["risk"]["reason"] in result["blockers"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda p: p["metrics"]["var_99_10d"]["confidence_interval"].update(
                {"lower": 0.10, "upper": 0.12}
            ),
            "do not contain the point estimate",
        ),
        (
            lambda p: p["metrics"]["var_99_1d"].update({"value": 9.0}),
            "within [0, 1]",
        ),
        (
            lambda p: p["metrics"]["tracking_error"].pop("benchmark_kind"),
            "benchmark_kind",
        ),
        (
            lambda p: p["lineage"].update({"data_snapshot_hash": "short"}),
            "data_snapshot_hash",
        ),
            (
                lambda p: p["lineage"].update(
                    {"acquired_at": "2026-09-03T14:01:01+00:00"}
                ),
                "after the frozen run start",
            ),
        (
            lambda p: p["construction"].update({"source_construction": ""}),
            "source_construction",
        ),
        (
            lambda p: p["construction"]["basis"].update(
                {"algorithm_identity": "portfolio_weights.csv[risk_parity]"}
            ),
            "algorithm_identity",
        ),
        (
            lambda p: p["construction"].update({"weight_identity": "5" * 64}),
            "weight_identity",
        ),
    ],
)
def test_risk_adapter_rejects_semantically_unbound_or_impossible_values(
    tmp_path, mutate, reason
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        mutate(payload)
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert reason in result["risk"]["reason"]


def test_risk_adapter_rejects_available_result_with_incomplete_frozen_lineage(
    tmp_path,
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        payload["lineage"]["complete"] = False
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert "available=true requires lineage.complete=true" in result["risk"]["reason"]


def test_risk_adapter_rejects_frozen_reference_that_disagrees_with_catalog(
    tmp_path,
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        payload["lineage"]["frozen_datasets"]["dataset_versions"]["benchmark"][
            "version_id"
        ] = "f" * 64
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert "frozen dataset lineage does not match the catalog" in result["risk"]["reason"]


def test_risk_adapter_rechecks_catalog_bytes_after_adapter_calculation(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    holder = {}

    def loader(request):
        ref = request["dataset_versions"]["price_panel"]
        version, _, _ = holder["service"].catalog.get_version(ref["version_id"])
        Path(version.blob_path).write_bytes(b"tampered after risk request binding")
        return valid_risk_payload(request)

    svc = service(tmp_path, clock, risk_loader=loader)
    holder["service"] = svc
    result = svc.risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert "frozen dataset verification failed" in result["risk"]["reason"]
    assert "checksum mismatch" in result["risk"]["reason"]


@pytest.mark.parametrize(
    ("defect", "reason"),
    [
        ("sample-end", "metric sample end does not match lineage.asof"),
        ("sample-hash", "calculation_sample_hash does not match lineage"),
    ],
)
def test_risk_adapter_rejects_metric_sample_time_or_hash_mismatch(
    tmp_path, defect, reason
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        if defect == "sample-end":
            for metric in payload["metrics"].values():
                metric["end"] = "2026-09-02"
        else:
            payload["metrics"]["es_97_5"]["calculation_sample_hash"] = "5" * 64
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert reason in result["risk"]["reason"]


def test_risk_adapter_rejects_proxy_tracking_error_marked_decision_usable(
    tmp_path,
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        payload["metrics"]["tracking_error"]["decision_usable"] = True
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert "research proxy tracking error is never decision usable" in result["risk"]["reason"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("benchmark_kind", "formal mandate"),
        ("benchmark_source", "formal_mandate:invented"),
        ("benchmark_version_id", "e" * 64),
        ("benchmark_content_sha256", "d" * 64),
    ],
)
def test_risk_adapter_rejects_tracking_error_benchmark_claim_drift(
    tmp_path, field, value
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        payload["metrics"]["tracking_error"][field] = value
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert f"tracking_error.{field}" in result["risk"]["reason"]
    assert "frozen benchmark dataset" in result["risk"]["reason"]


@pytest.mark.parametrize("raw", [{}, [], None], ids=["empty-object", "array", "null"])
def test_risk_adapter_rejects_empty_or_non_object_payload(tmp_path, raw):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    payload = service(tmp_path, clock, risk_loader=lambda _request: raw).risk()
    assert payload["risk"]["available"] is False
    assert payload["risk"]["payload"] is None
    assert "response must be a non-empty object" in payload["risk"]["reason"]
    assert payload["risk"]["reason"] in payload["blockers"]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("run_id", "different-run"),
        ("context_hash", "0" * 64),
        ("as_of", "2026-09-03T14:00:01+00:00"),
        ("strategy_id", "different-strategy"),
        ("strategy_version", "9.9.9"),
        ("portfolio_id", "different-portfolio"),
    ],
)
def test_risk_adapter_rejects_mismatched_frozen_identity(
    tmp_path, field, bad_value
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        payload["binding"][field] = bad_value
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert f"binding.{field}" in result["risk"]["reason"]


@pytest.mark.parametrize(
    "field",
    ["run_id", "context_hash", "as_of", "strategy_id", "strategy_version", "portfolio_id"],
)
def test_risk_adapter_rejects_missing_frozen_identity(tmp_path, field):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        del payload["binding"][field]
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert f"binding.{field}" in result["risk"]["reason"]


@pytest.mark.parametrize(
    ("target", "bad_value"),
    [
        ("payload_schema", "risk.v4"),
        ("payload_available", False),
        ("binding_missing", None),
        ("binding_schema", "platform.risk.binding.v0"),
        ("metrics_empty", None),
        ("missing_metric", "es_97_5"),
        ("metric_available", False),
        ("var_unit", "percent"),
        ("var_window", "1y"),
        ("var_window_sessions", 252),
        ("var_horizon", 1),
        ("var_observations", 745),
        ("var_interval", None),
        ("es_level", 0.95),
        ("te_unit", "fraction_of_nav"),
        ("te_annualization", 365),
        ("te_benchmark", ""),
    ],
)
def test_risk_adapter_rejects_wrong_schema_metric_coverage_units_or_windows(
    tmp_path, target, bad_value
):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def loader(request):
        payload = valid_risk_payload(request)
        if target == "payload_schema":
            payload["schema_version"] = bad_value
        elif target == "payload_available":
            payload["available"] = bad_value
        elif target == "binding_missing":
            del payload["binding"]
        elif target == "binding_schema":
            payload["binding"]["schema_version"] = bad_value
        elif target == "metrics_empty":
            payload["metrics"] = {}
        elif target == "missing_metric":
            del payload["metrics"][bad_value]
        elif target == "metric_available":
            payload["metrics"]["var_99_1d"]["available"] = bad_value
        elif target == "var_unit":
            payload["metrics"]["var_99_10d"]["unit"] = bad_value
        elif target == "var_window":
            payload["metrics"]["var_99_10d"]["window"] = bad_value
        elif target == "var_window_sessions":
            payload["metrics"]["var_99_10d"]["window_sessions"] = bad_value
        elif target == "var_horizon":
            payload["metrics"]["var_99_10d"]["horizon_days"] = bad_value
        elif target == "var_observations":
            payload["metrics"]["var_99_10d"]["observations"] = bad_value
        elif target == "var_interval":
            del payload["metrics"]["var_99_10d"]["confidence_interval"]
        elif target == "es_level":
            payload["metrics"]["es_97_5"]["confidence_level"] = bad_value
        elif target == "te_unit":
            payload["metrics"]["tracking_error"]["unit"] = bad_value
        elif target == "te_annualization":
            payload["metrics"]["tracking_error"]["annualization_sessions"] = bad_value
        elif target == "te_benchmark":
            payload["metrics"]["tracking_error"]["benchmark"] = bad_value
        return payload

    result = service(tmp_path, clock, risk_loader=loader).risk()
    assert result["risk"]["available"] is False
    assert result["risk"]["payload"] is None
    assert result["risk"]["reason"].startswith("risk adapter unavailable: risk adapter contract:")
    assert result["risk"]["reason"] in result["blockers"]


def test_paper_session_cannot_drift_into_another_portfolio(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    snapshot, run = legacy_payloads()
    observed = datetime(2026, 9, 3, 14, 2, tzinfo=UTC)

    def quotes(symbols, after, available_at):
        quote_time = available_at - timedelta(seconds=1)
        return {symbol: envelope(symbol, "100", quote_time) for symbol in symbols}

    svc = PlatformService(
        latest_dir=tmp_path / "unused",
        state_dir=tmp_path / "state",
        snapshot_loader=lambda: snapshot,
        run_loader=lambda: run,
        quote_loader=None,
        clock=clock,
        id_factory=lambda: "fixed_book",
        require_versioned_lineage=False,
    )
    svc._quote_loader = catalog_quote_loader(svc, quotes)
    svc.sync_current_run()
    sid = svc.create_paper_session(operation("create-fixed-book", {"starting_cash": "1000"}))["session"]["session_id"]
    clock.value = observed + timedelta(seconds=2)
    svc.step_paper(sid, operation("step-fixed-submit"))
    clock.value += timedelta(minutes=1)
    svc.step_paper(sid, operation("step-fixed-fill"))
    event_count = svc.paper(sid)["session"]["event_count"]

    snapshot.update({"run_id": "run-002", "portfolio_id": "portfolio-002"})
    run.update({"run_id": "run-002", "portfolio_id": "portfolio-002"})
    svc.sync_current_run()
    with pytest.raises(PlatformBlocked, match="not bound to the frozen strategy/portfolio"):
        svc.step_paper(sid, operation("step-wrong-book"))
    assert svc.paper(sid)["session"]["event_count"] == event_count


def test_non_unit_contract_multiplier_fails_before_order_creation(tmp_path):
    broker = PaperBroker.start_session(
        tmp_path / "future.jsonl",
        mandate=PaperMandate(starting_cash=Decimal("100000")),
        opened_at=datetime(2026, 9, 3, 14, 0, tzinfo=UTC),
    )
    intent = PositionIntent(
        instrument_id="future:CME:ESZ26",
        portfolio_id="future-book",
        direction="long",
        weight=0.5,
        multiplier=50,
    )
    quote = envelope(
        "ESZ26", "5000", datetime(2026, 9, 3, 14, 1, tzinfo=UTC)
    )
    with pytest.raises(PlatformBlocked, match="contract multipliers"):
        PlatformService._plan(broker, (intent,), {"ESZ26": quote})
    assert broker.orders == {}


def test_paper_session_rejects_non_usd_cash_until_fx_accounting_exists(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))
    svc = service(tmp_path, clock)
    with pytest.raises(PlatformInputError, match="USD-only.*FX accounting"):
        svc.create_paper_session(operation("create-eur", {"currency": "EUR"}))
    assert svc.paper()["count"] == 0


@pytest.mark.parametrize(
    ("intent_currency", "quote_currency", "expected"),
    [
        ("EUR", "USD", "equity:US:ESZ26 is EUR.*session is USD"),
        ("USD", "EUR", "quote ESZ26 is EUR.*session is USD"),
    ],
)
def test_paper_plan_rejects_unconverted_currency_exposure(
    tmp_path, intent_currency, quote_currency, expected
):
    broker = PaperBroker.start_session(
        tmp_path / f"currency-{intent_currency}-{quote_currency}.jsonl",
        mandate=PaperMandate(starting_cash=Decimal("100000"), currency="USD"),
        opened_at=datetime(2026, 9, 3, 14, 0, tzinfo=UTC),
    )
    intent = PositionIntent(
        instrument_id="equity:US:ESZ26",
        portfolio_id="future-book",
        direction="long",
        weight=0.5,
        currency=intent_currency,
    )
    base_quote = envelope(
        "ESZ26", "5000", datetime(2026, 9, 3, 14, 1, tzinfo=UTC)
    )
    quote = ExecutionQuote(
        quote=base_quote.quote,
        observed_at=base_quote.observed_at,
        acquired_at=base_quote.acquired_at,
        finality=base_quote.finality,
        source=base_quote.source,
        content_hash=base_quote.content_hash,
        instrument_id=base_quote.instrument_id,
        dataset_version_id=base_quote.dataset_version_id,
        request_hash=base_quote.request_hash,
        currency=quote_currency,
    )
    with pytest.raises(PlatformBlocked, match=expected):
        PlatformService._plan(broker, (intent,), {"ESZ26": quote})
    assert broker.orders == {}


def test_create_and_step_are_persistently_idempotent_by_operation_id(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def quotes(symbols, after, available_at):
        observed = available_at - timedelta(seconds=1)
        return {symbol: envelope(symbol, "100", observed) for symbol in symbols}

    svc = service(tmp_path, clock, quote_loader=quotes)
    create_request = operation("same-create", {"starting_cash": "1000"})
    created = svc.create_paper_session(create_request)
    assert svc.create_paper_session(create_request) == created
    sid = created["session"]["session_id"]
    assert svc.paper()["count"] == 1
    with pytest.raises(PlatformBlocked, match="Idempotency-Key conflicts"):
        svc.create_paper_session(operation("same-create", {"starting_cash": "2000"}))

    clock.value += timedelta(minutes=1)
    step_request = operation("same-step")
    first = svc.step_paper(sid, step_request)
    persisted_count = svc.paper(sid)["session"]["event_count"]
    assert first["sizing_evidence"]
    assert first["sizing_evidence"][0]["quote_lineage"]["source"] == "fixture.close"
    clock.value += timedelta(minutes=1)
    retry = svc.step_paper(sid, step_request)
    assert retry == first
    assert svc.paper(sid)["session"]["event_count"] == persisted_count

    with pytest.raises(PlatformBlocked, match="different action or payload"):
        svc.step_paper(sid, operation("same-step", {"run_id": "run-001"}))


def test_same_operation_cas_race_replays_one_persisted_receipt(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def quotes(symbols, after, available_at):
        observed = available_at - timedelta(seconds=1)
        return {symbol: envelope(symbol, "100", observed) for symbol in symbols}

    witness = checkpoint_store(tmp_path)
    first_service = service(
        tmp_path, clock, quote_loader=quotes, checkpoint_store=witness
    )
    second_service = service(
        tmp_path, clock, quote_loader=quotes, checkpoint_store=witness
    )
    sid = first_service.create_paper_session(operation("create-cas-race"))[
        "session"
    ]["session_id"]
    clock.value += timedelta(minutes=1)
    commit_barrier = Barrier(2)
    real_commit = PlatformService._commit_staged_paper

    def synchronized_commit(original, staged):
        commit_barrier.wait(timeout=5)
        return real_commit(original, staged)

    first_service._commit_staged_paper = synchronized_commit
    second_service._commit_staged_paper = synchronized_commit
    request = operation("same-concurrent-step")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda svc: svc.step_paper(sid, request),
                (first_service, second_service),
            )
        )

    assert {result["mutation_state"] for result in results} == {
        PAPER_MUTATION_COMMITTED
    }
    assert {result["operation_receipt"]["sequence"] for result in results} == {4}
    state = first_service.paper(sid)["session"]
    assert state["event_count"] == 4
    assert state["operation_receipt_count"] == 1
    assert len(state["orders"]) == 2
    assert state["external_checkpoint"]["synchronized"] is True


def test_mutating_service_calls_require_server_owned_operation_fields(tmp_path):
    svc = service(tmp_path, Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC)))
    with pytest.raises(PlatformInputError, match="Idempotency-Key"):
        svc.create_paper_session({})


def test_open_positions_fail_closed_across_dates_without_corporate_action_feed(tmp_path):
    clock = Clock(datetime(2026, 9, 3, 14, 1, tzinfo=UTC))

    def quotes(symbols, after, available_at):
        observed = available_at - timedelta(seconds=1)
        return {symbol: envelope(symbol, "100", observed) for symbol in symbols}

    svc = service(tmp_path, clock, quote_loader=quotes)
    sid = svc.create_paper_session(operation("create-intraday"))["session"]["session_id"]
    clock.value = datetime(2026, 9, 3, 15, 0, tzinfo=UTC)
    svc.step_paper(sid, operation("intraday-submit"))
    clock.value = datetime(2026, 9, 3, 15, 1, tzinfo=UTC)
    svc.step_paper(sid, operation("intraday-fill"))
    before = svc.paper(sid)["session"]["event_count"]

    clock.value = datetime(2026, 9, 4, 15, 0, tzinfo=UTC)
    with pytest.raises(PlatformBlocked, match="intraday-only"):
        svc.step_paper(sid, operation("overnight-block"))
    assert svc.paper(sid)["session"]["event_count"] == before
