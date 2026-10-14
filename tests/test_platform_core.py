"""Phase-zero contract tests for the multi-strategy platform core."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone

import pytest

from market_intel.platform.core import (
    NOT_VALIDATED,
    AmbiguousPluginVersionError,
    AssetClass,
    DataSnapshot,
    DatasetRequest,
    DatasetVersionRef,
    Direction,
    DuplicatePluginError,
    ExecutionStatus,
    Finality,
    Instrument,
    PlatformContractError,
    PositionIntent,
    ReconciliationError,
    RunContext,
    StockSelectAdapter,
    StrategyManifest,
    StrategyPlugin,
    StrategyRegistry,
    build_stock_select_adapter,
    canonical_hash,
    reconcile_intents,
)


AS_OF = datetime(2026, 9, 3, 20, tzinfo=timezone.utc)


def _version() -> DatasetVersionRef:
    return DatasetVersionRef(
        dataset_id="prices",
        version_id="prices-20260903",
        content_hash="a" * 64,
        request_hash="b" * 64,
        effective_at=datetime(2026, 9, 2, 20, tzinfo=timezone.utc),
        observed_at=datetime(2026, 9, 3, 13, tzinfo=timezone.utc),
        acquired_at=datetime(2026, 9, 3, 14, tzinfo=timezone.utc),
        finality=Finality.FINAL,
        schema_version="risk.v3",
    )


def _context(**changes) -> RunContext:
    args = {
        "workspace_id": "research",
        "strategy_id": "stock_select",
        "portfolio_id": "ai_semi_top15",
        "run_id": "run-20260903",
        "as_of": AS_OF,
        "run_started_at": AS_OF,
        "dataset_versions": {"prices": _version()},
        "model_version": "score-5p-v1",
        "mandate": {"gross_limit": 1.0, "sectors": ["semis", "infrastructure"]},
        "seed": 7,
    }
    args.update(changes)
    return RunContext(**args)


def _legacy_payloads():
    snapshot = {
        "run_id": "run-20260903",
        "portfolio_id": "ai_semi_top15",
        "date": "2026-09-03",
        "validation_status": "NOT VALIDATED · exploratory",
        "top": ["NVDA", "AMD"],
        "composite": {"NVDA": 1.25, "AMD": 0.8},
    }
    run = {
        "run_id": "run-20260903",
        "portfolio_id": "ai_semi_top15",
        "asof_session": "2026-09-03",
        "validation_status": "NOT VALIDATED · exploratory",
        "portfolio": {
            "cash": 0.1,
            "reconciles": True,
            "positions": [
                {"ticker": "NVDA", "target_executable_weight": 0.55},
                {"ticker": "AMD", "target_executable_weight": 0.35},
            ],
        },
    }
    return snapshot, run


def test_run_context_is_deeply_immutable_and_requires_explicit_aware_time():
    mandate = {"limits": {"gross": 1.0}, "names": ["NVDA"]}
    ctx = _context(mandate=mandate)
    mandate["limits"]["gross"] = 9.0
    mandate["names"].append("AMD")

    assert ctx.mandate["limits"]["gross"] == 1.0
    assert ctx.mandate["names"] == ("NVDA",)
    with pytest.raises(TypeError):
        ctx.mandate["new"] = True
    with pytest.raises(FrozenInstanceError):
        ctx.seed = 9
    with pytest.raises(PlatformContractError, match="timezone-aware"):
        _context(as_of=datetime(2026, 9, 3, 16))
    with pytest.raises(PlatformContractError, match="DatasetVersionRef"):
        _context(dataset_versions={"prices": "latest"})


def test_canonical_hash_is_order_and_timezone_independent_but_content_sensitive():
    request_a = DatasetRequest(
        provider="vendor",
        entitlement="research-seat",
        dataset="daily-close",
        instrument_ids=("equity:US:NVDA", "equity:US:AMD"),
        fields=("volume", "close"),
        frequency="1d",
        adjustment="split-dividend",
        start=date(2023, 9, 3),
        end=date(2026, 9, 3),
        as_of=AS_OF,
        schema_version="prices.v1",
        parameters={"currency": "USD", "vendor_flags": ["A", "B"]},
    )
    request_b = DatasetRequest(
        provider="vendor",
        entitlement="research-seat",
        dataset="daily-close",
        instrument_ids=("equity:US:AMD", "equity:US:NVDA"),
        fields=("close", "volume"),
        frequency="1d",
        adjustment="split-dividend",
        start=date(2023, 9, 3),
        end=date(2026, 9, 3),
        as_of=AS_OF.astimezone(timezone(timedelta(hours=-4))),
        schema_version="prices.v1",
        parameters={"vendor_flags": ["A", "B"], "currency": "USD"},
    )
    assert request_a == request_b
    assert request_a.dedupe_key == request_b.dedupe_key
    assert request_a.key == request_a.dedupe_key
    assert request_a.vintage_as_of == request_a.as_of
    assert request_a.canonical_dict()["date_range"] == ["2023-09-03", "2026-09-03"]
    assert len(request_a.dedupe_key) == 64
    with pytest.raises(TypeError):
        request_a.parameters["currency"] = "EUR"

    ctx_a = _context(mandate={"b": 2, "a": {"y": 4, "x": 3}})
    ctx_b = _context(
        as_of=AS_OF.astimezone(timezone(timedelta(hours=-4))),
        mandate={"a": {"x": 3, "y": 4}, "b": 2},
    )
    assert ctx_a.identity_hash == ctx_b.identity_hash
    assert ctx_a.identity_hash != _context(seed=8, mandate={"a": {"x": 3, "y": 4}, "b": 2}).identity_hash
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_instruments_and_signed_pair_futures_intents_are_expressive_and_strict():
    future = Instrument(
        instrument_id="future:CME:CLZ26",
        asset_class=AssetClass.FUTURE,
        currency="usd",
        symbol="CLZ26",
        exchange="CME",
        expiry=date(2026, 11, 20),
        contract_code="CLZ26",
        multiplier=1000,
    )
    assert future.currency == "USD" and future.multiplier == 1000

    long = PositionIntent(
        instrument_id="equity:US:NVDA",
        portfolio_id="pairs-book",
        sleeve_id="pairs",
        pair_id="nvda-amd",
        leg_id="leg-a",
        direction="long",
        weight=0.6,
    )
    short = PositionIntent(
        instrument_id="equity:US:AMD",
        portfolio_id="pairs-book",
        sleeve_id="pairs",
        pair_id="nvda-amd",
        leg_id="leg-b",
        direction=Direction.SHORT,
        weight=-0.4,
    )
    result = reconcile_intents((long, short), expected_net_weight=0.2, expected_gross_weight=1.0)
    assert result.reconciles
    assert result.net_weight == pytest.approx(0.2)
    assert result.gross_weight == pytest.approx(1.0)

    with pytest.raises(PlatformContractError, match="short intent"):
        PositionIntent("equity:US:AMD", "pairs-book", Direction.SHORT, weight=0.4)
    leg_only = PositionIntent(
        "equity:US:AMD", "pairs-book", Direction.SHORT, leg_id="b", weight=-0.4
    )
    assert leg_only.leg_id == "b" and leg_only.pair_id is None
    with pytest.raises(PlatformContractError, match="futures require"):
        Instrument("future:CME:CL", AssetClass.FUTURE, "USD", multiplier=1000)


def test_cash_and_duplicate_intent_reconciliation_fail_closed():
    rows = (
        PositionIntent("equity:US:A", "book", "long", weight=0.7),
        PositionIntent("equity:US:B", "book", "long", weight=0.2),
    )
    assert reconcile_intents(rows, cash_weight=0.1).reconciles
    bad = reconcile_intents(rows, cash_weight=0.2)
    assert not bad.reconciles and "does not equal 1" in bad.reasons[0]
    with pytest.raises(ReconciliationError, match="duplicate"):
        reconcile_intents((rows[0], rows[0]))


def test_registry_rejects_duplicates_and_ambiguous_unversioned_resolution():
    snapshot, run = _legacy_payloads()
    one = build_stock_select_adapter(snapshot, run, version="1.0.0")
    two = build_stock_select_adapter(snapshot, run, version="1.1.0")
    registry = StrategyRegistry()
    assert registry.register(one).identity == "stock_select@1.0.0"
    with pytest.raises(DuplicatePluginError):
        registry.register(one)
    registry.register(two)
    assert registry.get("stock_select", "1.0.0") is one
    assert registry.latest("stock_select") is two
    with pytest.raises(AmbiguousPluginVersionError):
        registry.get("stock_select")
    assert tuple(m.version for m in registry.manifests()) == ("1.0.0", "1.1.0")
    with pytest.raises(PlatformContractError, match="semantic version"):
        StockSelectAdapter(snapshot, run, version="latest")


def test_registry_registration_is_atomic_under_concurrency():
    snapshot, run = _legacy_payloads()
    plugin = build_stock_select_adapter(snapshot, run)
    registry = StrategyRegistry()

    def attempt(_):
        try:
            registry.register(plugin)
            return "registered"
        except DuplicatePluginError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(24)))
    assert outcomes.count("registered") == 1
    assert outcomes.count("duplicate") == 23
    assert len(registry) == 1


def test_stock_select_adapter_is_file_free_immutable_and_research_only():
    snapshot, run = _legacy_payloads()
    adapter = build_stock_select_adapter(snapshot, run)
    snapshot["top"].append("FAKE")
    snapshot["composite"]["NVDA"] = 999
    run["portfolio"]["positions"][0]["target_executable_weight"] = 0.99
    ctx = _context()

    assert isinstance(adapter, StrategyPlugin)
    assert adapter.manifest().validation_status == NOT_VALIDATED
    assert adapter.required_datasets() == ()
    frame = adapter.generate_signals(ctx)
    assert [signal.value for signal in frame.signals] == [1.25, 0.8]
    assert [signal.instrument_id for signal in frame.signals] == [
        "equity:US:NVDA",
        "equity:US:AMD",
    ]
    targets = adapter.construct_targets(ctx)
    assert [target.weight for target in targets] == [0.55, 0.35]
    assert reconcile_intents(targets, cash_weight=0.1).reconciles
    execution = adapter.simulate_execution(ctx)
    assert execution.status is ExecutionStatus.NOT_SIMULATED
    assert execution.filled == () and execution.validation_status == NOT_VALIDATED
    attribution = adapter.export_attribution(ctx)
    assert attribution.attributions == () and attribution.validation_status == NOT_VALIDATED

    frozen_snapshot = DataSnapshot(ctx.as_of, ctx.dataset_versions, _legacy_payloads()[0])
    universe = adapter.build_universe(frozen_snapshot)
    assert [item.symbol for item in universe.instruments] == ["NVDA", "AMD"]
    assert universe.validation_status == NOT_VALIDATED


def test_stock_select_adapter_injected_loaders_receive_context_and_validate_identity():
    snapshot, run = _legacy_payloads()
    seen = []

    def snapshot_loader(ctx):
        seen.append(("snapshot", ctx.identity_hash))
        return snapshot

    def run_loader(ctx):
        seen.append(("run", ctx.identity_hash))
        return run

    adapter = StockSelectAdapter(snapshot_loader, run_loader)
    ctx = _context()
    adapter.generate_signals(ctx)
    adapter.construct_targets(ctx)
    assert seen == [("snapshot", ctx.identity_hash), ("run", ctx.identity_hash)]

    with pytest.raises(PlatformContractError, match="run_id"):
        adapter.construct_targets(_context(run_id="different-run"))
    bad_run = _legacy_payloads()[1]
    bad_run["portfolio"]["cash"] = 0.5
    with pytest.raises(ReconciliationError):
        StockSelectAdapter(snapshot, bad_run).construct_targets(ctx)


def test_stock_select_adapter_payloads_are_bound_to_frozen_context_hashes():
    snapshot, run = _legacy_payloads()
    ctx = _context(
        input_artifacts={
            "legacy_snapshot": canonical_hash(snapshot),
            "legacy_run": canonical_hash(run),
        }
    )
    adapter = StockSelectAdapter(snapshot, run)
    assert [row.weight for row in adapter.construct_targets(ctx)] == [0.55, 0.35]

    changed_run = _legacy_payloads()[1]
    changed_run["portfolio"]["positions"][0]["target_executable_weight"] = 0.65
    with pytest.raises(PlatformContractError, match="frozen RunContext hash"):
        StockSelectAdapter(snapshot, changed_run).construct_targets(ctx)


def test_manifest_and_dataset_version_fail_closed_on_unsupported_claims():
    with pytest.raises(PlatformContractError, match="NOT VALIDATED"):
        StrategyManifest("s", "Strategy", "1.0.0", validation_status="VALIDATED")
    announced_future_change = DatasetVersionRef(
        dataset_id="msci-review",
        version_id="v1",
        content_hash="a" * 64,
        effective_at=AS_OF + timedelta(days=7),
        observed_at=AS_OF - timedelta(seconds=1),
        acquired_at=AS_OF,
        finality="final",
        schema_version="v1",
    )
    assert announced_future_change.observed_at < announced_future_change.effective_at
    future_observation = DatasetVersionRef(
        dataset_id="prices",
        version_id="future",
        content_hash="f" * 64,
        effective_at=AS_OF,
        observed_at=AS_OF + timedelta(seconds=1),
        acquired_at=AS_OF + timedelta(seconds=1),
        finality="final",
        schema_version="v1",
    )
    with pytest.raises(PlatformContractError, match="not observable"):
        _context(dataset_versions={"prices": future_observation})
    historical_ref = DatasetVersionRef(
        dataset_id="pit-prices",
        version_id="historical-v1",
        content_hash="c" * 64,
        effective_at=AS_OF - timedelta(days=365),
        observed_at=AS_OF - timedelta(days=1),
        acquired_at=AS_OF + timedelta(days=10),
        finality="final",
        schema_version="v1",
    )
    historical = _context(
        dataset_versions={"prices": historical_ref},
        run_started_at=AS_OF + timedelta(days=11),
    )
    assert historical.dataset_versions["prices"].acquired_at > historical.as_of
    with pytest.raises(PlatformContractError, match="acquired by run_started_at"):
        _context(
            dataset_versions={"prices": historical_ref},
            run_started_at=AS_OF + timedelta(days=9),
        )
    with pytest.raises(PlatformContractError, match="duplicate"):
        DatasetRequest(
            provider="v",
            entitlement="e",
            dataset="d",
            instrument_ids=("x", "x"),
            fields=("close",),
            frequency="1d",
            adjustment="raw",
            start=date(2026, 1, 1),
            end=date(2026, 1, 2),
            as_of=AS_OF,
            schema_version="v1",
        )
    with pytest.raises(PlatformContractError, match="after end"):
        DatasetRequest(
            provider="v",
            entitlement="e",
            dataset="d",
            instrument_ids=("x",),
            fields=("close",),
            frequency="1d",
            adjustment="raw",
            start="2026-01-03",
            end="2026-01-02",
            as_of=AS_OF,
            schema_version="v1",
        )
    with pytest.raises(PlatformContractError, match="full SHA-256"):
        DatasetVersionRef(
            dataset_id="prices",
            version_id="v1",
            content_hash="short",
            effective_at=AS_OF,
            observed_at=AS_OF,
            acquired_at=AS_OF,
            finality="final",
            schema_version="v1",
        )
