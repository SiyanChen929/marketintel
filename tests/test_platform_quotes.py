from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from market_intel.platform.core import DatasetRequest, canonical_json
from market_intel.platform.data import DatasetCatalog
from market_intel.platform.quotes import (
    ConfirmedQuoteAdapter,
    ConfirmedQuoteError,
    EXECUTION_QUOTE_DATASET,
    EXECUTION_QUOTE_FIELDS,
    EXECUTION_QUOTE_SCHEMA_VERSION,
    QUOTE_SNAPSHOT_SCHEMA_VERSION,
    QuoteVersionBinding,
)
from market_intel.platform.service import ExecutionQuote, PlatformService


UTC = timezone.utc
OBSERVED = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
ACQUIRED = OBSERVED + timedelta(seconds=1)
AFTER = OBSERVED - timedelta(minutes=1)
AS_OF = OBSERVED + timedelta(minutes=2)


def _catalog(tmp_path) -> DatasetCatalog:
    return DatasetCatalog(tmp_path / "catalog.sqlite")


def _register(
    catalog: DatasetCatalog,
    *,
    symbol: str = "AAA",
    instrument_id: str | None = None,
    bid: str = "99.90",
    ask: str = "100.10",
    observed_at: datetime = OBSERVED,
    acquired_at: datetime = ACQUIRED,
    effective_at: datetime | None = None,
    finality: str = "confirmed",
    artifact_changes: dict | None = None,
    parameters: dict | None = None,
):
    symbol = symbol.upper()
    instrument_id = instrument_id or f"equity:US:{symbol}"
    artifact = {
        "schema_version": EXECUTION_QUOTE_SCHEMA_VERSION,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "bid": bid,
        "ask": ask,
        "available_quantity": None,
        "currency": "USD",
        "source": "fixture.confirmed",
    }
    artifact.update(artifact_changes or {})
    request = DatasetRequest(
        provider="fixture.confirmed",
        entitlement="fixture-seat",
        dataset=EXECUTION_QUOTE_DATASET,
        instrument_ids=(instrument_id,),
        fields=EXECUTION_QUOTE_FIELDS,
        frequency="event",
        adjustment="raw",
        start=observed_at.date(),
        end=observed_at.date(),
        as_of=acquired_at,
        schema_version=EXECUTION_QUOTE_SCHEMA_VERSION,
        parameters=parameters or {"currency": "USD", "symbol": symbol},
    )
    version = catalog.register(
        request,
        canonical_json(artifact).encode("utf-8"),
        effective_at=effective_at or observed_at,
        observed_at=observed_at,
        acquired_at=acquired_at,
        finality=finality,
    )
    binding = QuoteVersionBinding(
        symbol=symbol,
        instrument_id=instrument_id,
        dataset_version_id=version.version_id,
        content_hash=version.content_sha256,
    )
    return binding, version


def test_adapter_matches_paper_loader_and_snapshot_hash_is_deterministic(tmp_path):
    catalog = _catalog(tmp_path)
    aaa, _ = _register(catalog, symbol="AAA")
    bbb, _ = _register(
        catalog,
        symbol="BBB",
        bid="49.95",
        ask="50.05",
        finality="final",
    )
    adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(bbb, aaa))

    first = adapter.load_snapshot(("BBB", "AAA"), AFTER, AS_OF)
    eastern = timezone(timedelta(hours=-4))
    second = adapter.load_snapshot(
        ("AAA", "BBB"), AFTER.astimezone(eastern), AS_OF.astimezone(eastern)
    )

    assert first.schema_version == QUOTE_SNAPSHOT_SCHEMA_VERSION
    assert len(first.snapshot_hash) == 64
    assert first.snapshot_hash == second.snapshot_hash == first.identity_hash
    assert tuple(first.quotes) == ("AAA", "BBB")
    assert all(isinstance(value, ExecutionQuote) for value in first.quotes.values())
    assert first.quotes["AAA"].dataset_version_id == aaa.dataset_version_id
    assert first.to_dict()["snapshot_hash"] == first.snapshot_hash
    assert [row["symbol"] for row in first.to_dict()["quotes"]] == ["AAA", "BBB"]
    assert adapter(("AAA", "BBB"), AFTER, AS_OF) == first.quotes
    service = PlatformService(
        latest_dir=tmp_path / "unused-latest",
        state_dir=tmp_path / "platform-state",
        catalog=catalog,
        quote_loader=adapter,
    )
    for envelope in first.quotes.values():
        service._verify_execution_quote(envelope, available_at=AS_OF)
    with pytest.raises(TypeError):
        first.quotes["CCC"] = first.quotes["AAA"]


def test_adapter_requires_timezone_aware_boundaries_and_artifact_clock(tmp_path):
    catalog = _catalog(tmp_path)
    binding, _ = _register(catalog)
    adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(binding,))

    with pytest.raises(ConfirmedQuoteError, match="after must be timezone-aware"):
        adapter(("AAA",), AFTER.replace(tzinfo=None), AS_OF)
    with pytest.raises(ConfirmedQuoteError, match="as_of must be timezone-aware"):
        adapter(("AAA",), AFTER, AS_OF.replace(tzinfo=None))

    naive_binding, _ = _register(
        catalog,
        symbol="NAIVE",
        artifact_changes={"observed_at": "2026-09-04T14:00:00"},
    )
    naive = ConfirmedQuoteAdapter(catalog=catalog, bindings=(naive_binding,))
    with pytest.raises(ConfirmedQuoteError, match="artifact observed_at must be timezone-aware"):
        naive(("NAIVE",), AFTER, AS_OF)


@pytest.mark.parametrize(
    ("bid", "ask", "message"),
    [
        ("0", "1", "bid must be positive"),
        ("NaN", "1", "bid must be finite"),
        ("1", "Infinity", "ask must be finite"),
    ],
)
def test_nonpositive_or_nonfinite_prices_fail_closed(tmp_path, bid, ask, message):
    catalog = _catalog(tmp_path)
    binding, _ = _register(catalog, bid=bid, ask=ask)
    adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(binding,))
    with pytest.raises(ConfirmedQuoteError, match=message):
        adapter(("AAA",), AFTER, AS_OF)


def test_provisional_quote_fails_closed_but_final_is_allowed(tmp_path):
    catalog = _catalog(tmp_path)
    provisional, _ = _register(catalog, symbol="PROV", finality="provisional")
    rejected = ConfirmedQuoteAdapter(catalog=catalog, bindings=(provisional,))
    with pytest.raises(ConfirmedQuoteError, match="provisional, not confirmed/final"):
        rejected(("PROV",), AFTER, AS_OF)

    final, _ = _register(catalog, symbol="FINAL", finality="final")
    accepted = ConfirmedQuoteAdapter(catalog=catalog, bindings=(final,))
    assert accepted(("FINAL",), AFTER, AS_OF)["FINAL"].finality.value == "final"


def test_stale_future_preboundary_and_backfill_quotes_fail_closed(tmp_path):
    catalog = _catalog(tmp_path)
    ordinary, _ = _register(catalog, symbol="STALE")
    adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(ordinary,))
    with pytest.raises(ConfirmedQuoteError, match="stale"):
        adapter(("STALE",), AFTER, OBSERVED + timedelta(minutes=6))
    with pytest.raises(ConfirmedQuoteError, match="not after the signal/order boundary"):
        adapter(("STALE",), OBSERVED, AS_OF)

    future_observed = AS_OF + timedelta(seconds=1)
    future, _ = _register(
        catalog,
        symbol="FUTURE",
        observed_at=future_observed,
        acquired_at=future_observed + timedelta(seconds=1),
    )
    future_adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(future,))
    with pytest.raises(ConfirmedQuoteError, match="from the future"):
        future_adapter(("FUTURE",), AFTER, AS_OF)

    delayed, _ = _register(
        catalog,
        symbol="BACKFILL",
        acquired_at=OBSERVED + timedelta(minutes=2),
    )
    delayed_adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(delayed,))
    with pytest.raises(ConfirmedQuoteError, match="delayed/backfill acquisition"):
        delayed_adapter(
            ("BACKFILL",), AFTER, OBSERVED + timedelta(minutes=2, seconds=1)
        )


def test_effective_time_and_request_backfill_markers_are_forbidden(tmp_path):
    catalog = _catalog(tmp_path)
    effective_backfill, _ = _register(
        catalog,
        symbol="EFFECTIVE",
        effective_at=OBSERVED - timedelta(days=1),
    )
    adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(effective_backfill,))
    with pytest.raises(ConfirmedQuoteError, match="backfilled effective timestamp"):
        adapter(("EFFECTIVE",), AFTER, AS_OF)

    marked_backfill, _ = _register(
        catalog,
        symbol="MARKED",
        parameters={"currency": "USD", "symbol": "MARKED", "backfill": True},
    )
    marked_adapter = ConfirmedQuoteAdapter(catalog=catalog, bindings=(marked_backfill,))
    with pytest.raises(ConfirmedQuoteError, match="backfill quote request is forbidden"):
        marked_adapter(("MARKED",), AFTER, AS_OF)


def test_catalog_version_and_content_hash_are_verified(tmp_path):
    catalog = _catalog(tmp_path)
    binding, version = _register(catalog)
    wrong_hash = replace(
        binding,
        content_hash=("f" if binding.content_hash[0] != "f" else "e")
        + binding.content_hash[1:],
    )
    with pytest.raises(ConfirmedQuoteError, match="content hash mismatch"):
        ConfirmedQuoteAdapter(catalog=catalog, bindings=(wrong_hash,))(
            ("AAA",), AFTER, AS_OF
        )

    Path(version.blob_path).write_bytes(b"tampered")
    with pytest.raises(ConfirmedQuoteError, match="could not be verified.*checksum mismatch"):
        ConfirmedQuoteAdapter(catalog=catalog, bindings=(binding,))(
            ("AAA",), AFTER, AS_OF
        )


def test_symbol_and_instrument_bindings_are_unique_and_exact(tmp_path):
    catalog = _catalog(tmp_path)
    aaa, _ = _register(catalog, symbol="AAA")
    alias, _ = _register(
        catalog,
        symbol="ALIAS",
        instrument_id=aaa.instrument_id,
    )
    with pytest.raises(ConfirmedQuoteError, match="bound to both"):
        ConfirmedQuoteAdapter(catalog=catalog, bindings=(aaa, alias))
    with pytest.raises(ConfirmedQuoteError, match="duplicate quote binding"):
        ConfirmedQuoteAdapter(catalog=catalog, bindings=(aaa, aaa))
    with pytest.raises(ConfirmedQuoteError, match="no pinned catalog quote"):
        ConfirmedQuoteAdapter(catalog=catalog, bindings=(aaa,))(
            ("MISSING",), AFTER, AS_OF
        )
    with pytest.raises(ConfirmedQuoteError, match="dataset_version_id must be a full SHA-256"):
        QuoteVersionBinding("AAA", aaa.instrument_id, "latest", aaa.content_hash)

    mismatched, _ = _register(
        catalog,
        symbol="MISMATCH",
        artifact_changes={"instrument_id": "equity:US:OTHER"},
    )
    with pytest.raises(ConfirmedQuoteError, match="symbol/instrument identity mismatch"):
        ConfirmedQuoteAdapter(catalog=catalog, bindings=(mismatched,))(
            ("MISMATCH",), AFTER, AS_OF
        )
