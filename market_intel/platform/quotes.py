"""Catalog-backed confirmed quotes for deterministic PAPER steps.

The adapter in this module has no clock, filesystem discovery, broker, or
network client.  A caller pins exact :class:`DatasetCatalog` versions and
passes the PAPER step's signal boundary and availability time explicitly.
Every selected quote is re-read from the content-addressed catalog and must
pass the same artifact/request contract consumed by ``PlatformService``.

``ConfirmedQuoteAdapter`` is callable with the existing ``QuoteLoader`` shape::

    adapter(symbols, after, available_at) -> Mapping[str, ExecutionQuote]

Use :meth:`ConfirmedQuoteAdapter.load_snapshot` when the deterministic
snapshot identity is also required for an audit record.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from .core import Finality, FrozenMapping, canonical_hash, canonical_json
from .data import DatasetCatalog
from .paper import Quote

if TYPE_CHECKING:  # Avoid a service -> quotes -> service import cycle when wired.
    from .service import ExecutionQuote


UTC = timezone.utc
EXECUTION_QUOTE_SCHEMA_VERSION = "platform.execution.quote.v1"
EXECUTION_QUOTE_DATASET = "execution_quote"
EXECUTION_QUOTE_FIELDS = (
    "ask",
    "available_quantity",
    "bid",
    "currency",
    "instrument_id",
    "observed_at",
    "source",
    "symbol",
)
QUOTE_SNAPSHOT_SCHEMA_VERSION = "platform.confirmed_quote.snapshot.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FIELDS = frozenset(
    {
        "schema_version",
        "instrument_id",
        "symbol",
        "observed_at",
        "bid",
        "ask",
        "available_quantity",
        "currency",
        "source",
    }
)


class ConfirmedQuoteError(RuntimeError):
    """A quote cannot satisfy the fail-closed PAPER data contract."""


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ConfirmedQuoteError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_aware(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ConfirmedQuoteError(f"{name} must be a timezone-aware ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ConfirmedQuoteError(f"{name} must be a timezone-aware ISO datetime") from exc
    return _aware(parsed, name)


def _sha256(value: str, name: str) -> str:
    digest = value.strip().lower() if isinstance(value, str) else ""
    if not _SHA256_RE.fullmatch(digest):
        raise ConfirmedQuoteError(f"{name} must be a full SHA-256 digest")
    return digest


def _symbol(value: str, name: str = "symbol") -> str:
    result = value.strip().upper() if isinstance(value, str) else ""
    if not result:
        raise ConfirmedQuoteError(f"{name} is required")
    return result


def _instrument_id(value: str) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not result or ":" not in result:
        raise ConfirmedQuoteError("instrument_id must be a permanent instrument identity")
    return result


def _seconds(value: float | int, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfirmedQuoteError(f"{name} must be finite and nonnegative")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ConfirmedQuoteError(f"{name} must be finite and nonnegative")
    return result


def _duration(value: timedelta, name: str) -> float:
    if not isinstance(value, timedelta):
        raise ConfirmedQuoteError(f"{name} must be a nonnegative timedelta")
    return _seconds(value.total_seconds(), name)


@dataclass(frozen=True, slots=True)
class QuoteVersionBinding:
    """Pin one normalized symbol/instrument pair to one catalog artifact."""

    symbol: str
    instrument_id: str
    dataset_version_id: str
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "instrument_id", _instrument_id(self.instrument_id))
        object.__setattr__(
            self,
            "dataset_version_id",
            _sha256(self.dataset_version_id, "dataset_version_id"),
        )
        object.__setattr__(self, "content_hash", _sha256(self.content_hash, "content_hash"))


@dataclass(frozen=True, slots=True)
class ConfirmedQuoteSnapshot:
    """Immutable, order-independent set of verified execution quotes."""

    as_of: datetime
    after: datetime
    quotes: Mapping[str, Any]
    max_quote_age_seconds: float
    max_acquisition_lag_seconds: float
    snapshot_hash: str = field(init=False)

    schema_version: ClassVar[str] = QUOTE_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        as_of = _aware(self.as_of, "as_of")
        after = _aware(self.after, "after")
        max_age = _seconds(self.max_quote_age_seconds, "max_quote_age_seconds")
        max_lag = _seconds(
            self.max_acquisition_lag_seconds, "max_acquisition_lag_seconds"
        )
        if after >= as_of:
            raise ConfirmedQuoteError("after must be strictly before as_of")
        if not isinstance(self.quotes, Mapping) or not self.quotes:
            raise ConfirmedQuoteError("confirmed quote snapshot cannot be empty")

        normalized: dict[str, Any] = {}
        instrument_symbols: dict[str, str] = {}
        for raw_symbol, envelope in self.quotes.items():
            symbol = _symbol(raw_symbol, "quote mapping symbol")
            if symbol in normalized:
                raise ConfirmedQuoteError(f"duplicate quote symbol {symbol}")
            quote = getattr(envelope, "quote", None)
            if not isinstance(quote, Quote) or quote.symbol != symbol:
                raise ConfirmedQuoteError(f"quote payload does not match symbol {symbol}")
            try:
                finality = Finality(getattr(envelope, "finality", None))
            except (TypeError, ValueError) as exc:
                raise ConfirmedQuoteError(f"quote for {symbol} has unknown finality") from exc
            if finality not in {Finality.CONFIRMED, Finality.FINAL}:
                raise ConfirmedQuoteError(
                    f"quote for {symbol} is {finality.value}, not confirmed/final"
                )

            observed = _aware(getattr(envelope, "observed_at", None), "observed_at")
            acquired = _aware(getattr(envelope, "acquired_at", None), "acquired_at")
            if quote.timestamp != observed:
                raise ConfirmedQuoteError(f"quote timestamp mismatch for {symbol}")
            if observed > acquired:
                raise ConfirmedQuoteError(f"observed_at is after acquired_at for {symbol}")
            if observed <= after:
                raise ConfirmedQuoteError(
                    f"quote for {symbol} is not after the signal/order boundary"
                )
            if observed > as_of or acquired > as_of:
                raise ConfirmedQuoteError(f"quote for {symbol} is from the future")
            if (as_of - observed).total_seconds() > max_age:
                raise ConfirmedQuoteError(f"quote for {symbol} is stale")
            if (acquired - observed).total_seconds() > max_lag:
                raise ConfirmedQuoteError(
                    f"quote for {symbol} is a delayed/backfill acquisition"
                )

            instrument_id = _instrument_id(getattr(envelope, "instrument_id", ""))
            prior_symbol = instrument_symbols.get(instrument_id)
            if prior_symbol is not None:
                raise ConfirmedQuoteError(
                    f"instrument {instrument_id} is bound to both {prior_symbol} and {symbol}"
                )
            instrument_symbols[instrument_id] = symbol
            _sha256(getattr(envelope, "dataset_version_id", ""), "dataset_version_id")
            _sha256(getattr(envelope, "content_hash", ""), "content_hash")
            _sha256(getattr(envelope, "request_hash", ""), "request_hash")
            normalized[symbol] = envelope

        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "after", after)
        object.__setattr__(self, "max_quote_age_seconds", max_age)
        object.__setattr__(self, "max_acquisition_lag_seconds", max_lag)
        object.__setattr__(self, "quotes", FrozenMapping(normalized))
        object.__setattr__(self, "snapshot_hash", canonical_hash(self.canonical_dict()))

    def canonical_dict(self) -> dict[str, Any]:
        rows = []
        for symbol in self.quotes:
            envelope = self.quotes[symbol]
            rows.append(
                {
                    "symbol": symbol,
                    "artifact": envelope.artifact(),
                    "lineage": envelope.lineage(),
                }
            )
        return {
            "schema_version": self.schema_version,
            "as_of": self.as_of,
            "after": self.after,
            "max_quote_age_seconds": self.max_quote_age_seconds,
            "max_acquisition_lag_seconds": self.max_acquisition_lag_seconds,
            "quotes": rows,
        }

    @property
    def identity_hash(self) -> str:
        return self.snapshot_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            **json.loads(canonical_json(self.canonical_dict())),
            "snapshot_hash": self.snapshot_hash,
        }


class ConfirmedQuoteAdapter:
    """Resolve exact local catalog versions into PAPER-compatible quotes.

    Bindings are immutable and one-to-one.  To advance to newer observations,
    construct a new adapter from a newly frozen set of catalog version IDs.
    """

    def __init__(
        self,
        *,
        catalog: DatasetCatalog,
        bindings: Iterable[QuoteVersionBinding],
        max_quote_age: timedelta = timedelta(minutes=5),
        max_acquisition_lag: timedelta = timedelta(minutes=1),
    ) -> None:
        if not isinstance(catalog, DatasetCatalog):
            raise ConfirmedQuoteError("catalog must be a DatasetCatalog")
        max_age = _duration(max_quote_age, "max_quote_age")
        max_lag = _duration(max_acquisition_lag, "max_acquisition_lag")
        rows = tuple(bindings)
        if not rows or any(not isinstance(row, QuoteVersionBinding) for row in rows):
            raise ConfirmedQuoteError("bindings must contain QuoteVersionBinding objects")

        by_symbol: dict[str, QuoteVersionBinding] = {}
        instrument_symbols: dict[str, str] = {}
        version_symbols: dict[str, str] = {}
        for row in rows:
            if row.symbol in by_symbol:
                raise ConfirmedQuoteError(f"duplicate quote binding for {row.symbol}")
            prior_symbol = instrument_symbols.get(row.instrument_id)
            if prior_symbol is not None:
                raise ConfirmedQuoteError(
                    f"instrument {row.instrument_id} is bound to both {prior_symbol} and {row.symbol}"
                )
            prior_version_symbol = version_symbols.get(row.dataset_version_id)
            if prior_version_symbol is not None:
                raise ConfirmedQuoteError(
                    "one dataset version cannot satisfy multiple symbol bindings"
                )
            by_symbol[row.symbol] = row
            instrument_symbols[row.instrument_id] = row.symbol
            version_symbols[row.dataset_version_id] = row.symbol

        self._catalog = catalog
        self._bindings = FrozenMapping(by_symbol)
        self._max_quote_age_seconds = max_age
        self._max_acquisition_lag_seconds = max_lag

    @property
    def bindings(self) -> Mapping[str, QuoteVersionBinding]:
        return self._bindings

    def __call__(
        self,
        symbols: tuple[str, ...],
        after: datetime,
        available_at: datetime,
    ) -> Mapping[str, "ExecutionQuote"]:
        """Satisfy ``PlatformService.quote_loader`` without implicit I/O."""

        return self.load_snapshot(symbols, after, available_at).quotes

    def load(
        self,
        symbols: Iterable[str],
        after: datetime,
        available_at: datetime,
    ) -> Mapping[str, "ExecutionQuote"]:
        return self.load_snapshot(symbols, after, available_at).quotes

    def load_snapshot(
        self,
        symbols: Iterable[str],
        after: datetime,
        as_of: datetime,
    ) -> ConfirmedQuoteSnapshot:
        after_utc = _aware(after, "after")
        as_of_utc = _aware(as_of, "as_of")
        if isinstance(symbols, (str, bytes)):
            raise ConfirmedQuoteError("symbols must be an iterable of unique symbols")
        requested: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = _symbol(raw, "requested symbol")
            if symbol in seen:
                raise ConfirmedQuoteError(f"duplicate requested symbol {symbol}")
            seen.add(symbol)
            requested.append(symbol)
        if not requested:
            raise ConfirmedQuoteError("at least one quote symbol is required")

        envelopes: dict[str, ExecutionQuote] = {}
        for symbol in sorted(requested):
            binding = self._bindings.get(symbol)
            if binding is None:
                raise ConfirmedQuoteError(f"no pinned catalog quote for {symbol}")
            envelopes[symbol] = self._load_one(binding)
        return ConfirmedQuoteSnapshot(
            as_of=as_of_utc,
            after=after_utc,
            quotes=envelopes,
            max_quote_age_seconds=self._max_quote_age_seconds,
            max_acquisition_lag_seconds=self._max_acquisition_lag_seconds,
        )

    def _load_one(self, binding: QuoteVersionBinding) -> "ExecutionQuote":
        try:
            version, request, payload = self._catalog.get_version(
                binding.dataset_version_id
            )
        except (KeyError, ValueError, OSError) as exc:
            raise ConfirmedQuoteError(
                f"dataset version for {binding.symbol} could not be verified: {exc}"
            ) from exc

        if version.version_id != binding.dataset_version_id:
            raise ConfirmedQuoteError(
                f"dataset_version_id mismatch for {binding.symbol}"
            )
        if version.content_sha256 != binding.content_hash:
            raise ConfirmedQuoteError(f"content hash mismatch for {binding.symbol}")
        if version.finality not in {Finality.CONFIRMED, Finality.FINAL}:
            raise ConfirmedQuoteError(
                f"quote for {binding.symbol} is {version.finality.value}, not confirmed/final"
            )
        if version.schema_version != EXECUTION_QUOTE_SCHEMA_VERSION:
            raise ConfirmedQuoteError(f"quote schema mismatch for {binding.symbol}")
        observed = _aware(version.observed_at, "observed_at")
        acquired = _aware(version.acquired_at, "acquired_at")
        effective = _aware(version.effective_at, "effective_at")
        if observed > acquired:
            raise ConfirmedQuoteError(
                f"observed_at is after acquired_at for {binding.symbol}"
            )
        if effective != observed:
            raise ConfirmedQuoteError(
                f"quote for {binding.symbol} has a backfilled effective timestamp"
            )

        artifact = self._decode_artifact(payload, binding.symbol)
        artifact_observed = _parse_aware(
            artifact.get("observed_at"), "artifact observed_at"
        )
        if artifact_observed != observed:
            raise ConfirmedQuoteError(
                f"artifact observed_at mismatch for {binding.symbol}"
            )
        try:
            quote = Quote(
                symbol=artifact.get("symbol"),
                timestamp=artifact_observed,
                bid=artifact.get("bid"),
                ask=artifact.get("ask"),
                available_quantity=artifact.get("available_quantity"),
            )
        except Exception as exc:
            raise ConfirmedQuoteError(
                f"quote price/quantity contract failed for {binding.symbol}: {exc}"
            ) from exc

        artifact_instrument = _instrument_id(artifact.get("instrument_id"))
        if quote.symbol != binding.symbol or artifact_instrument != binding.instrument_id:
            raise ConfirmedQuoteError(
                f"quote symbol/instrument identity mismatch for {binding.symbol}"
            )
        source = artifact.get("source")
        if not isinstance(source, str) or not source.strip() or source != source.strip():
            raise ConfirmedQuoteError(f"quote source is invalid for {binding.symbol}")
        currency = artifact.get("currency")
        if (
            not isinstance(currency, str)
            or currency != currency.strip().upper()
            or len(currency) != 3
            or not currency.isalpha()
        ):
            raise ConfirmedQuoteError(f"quote currency is invalid for {binding.symbol}")

        self._verify_request(
            request,
            binding=binding,
            source=source,
            currency=currency,
            observed=observed,
            acquired=acquired,
        )

        # Delayed import lets service.py import this adapter later without a cycle.
        from .service import ExecutionQuote

        try:
            envelope = ExecutionQuote(
                quote=quote,
                observed_at=observed,
                acquired_at=acquired,
                finality=version.finality,
                source=source,
                content_hash=version.content_sha256,
                instrument_id=binding.instrument_id,
                dataset_version_id=version.version_id,
                request_hash=version.request_key,
                currency=currency,
            )
        except Exception as exc:
            raise ConfirmedQuoteError(
                f"execution quote contract failed for {binding.symbol}: {exc}"
            ) from exc
        if canonical_json(envelope.artifact()).encode("utf-8") != bytes(payload):
            raise ConfirmedQuoteError(
                f"quote artifact payload mismatch for {binding.symbol}"
            )
        return envelope

    @staticmethod
    def _decode_artifact(payload: bytes, symbol: str) -> dict[str, Any]:
        try:
            raw = bytes(payload)
            artifact = json.loads(raw.decode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ConfirmedQuoteError(
                f"quote artifact for {symbol} is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(artifact, dict):
            raise ConfirmedQuoteError(f"quote artifact for {symbol} must be an object")
        if frozenset(artifact) != _ARTIFACT_FIELDS:
            raise ConfirmedQuoteError(f"quote artifact fields mismatch for {symbol}")
        if canonical_json(artifact).encode("utf-8") != raw:
            raise ConfirmedQuoteError(f"quote artifact for {symbol} is not canonical")
        if artifact.get("schema_version") != EXECUTION_QUOTE_SCHEMA_VERSION:
            raise ConfirmedQuoteError(f"quote artifact schema mismatch for {symbol}")
        return artifact

    @staticmethod
    def _verify_request(
        request: Mapping[str, Any],
        *,
        binding: QuoteVersionBinding,
        source: str,
        currency: str,
        observed: datetime,
        acquired: datetime,
    ) -> None:
        if not isinstance(request, Mapping):
            raise ConfirmedQuoteError(
                f"quote request metadata is invalid for {binding.symbol}"
            )
        entitlement = request.get("entitlement")
        if not isinstance(entitlement, str) or not entitlement.strip():
            raise ConfirmedQuoteError(
                f"quote request entitlement is missing for {binding.symbol}"
            )
        vintage = _parse_aware(request.get("vintage_as_of"), "request vintage_as_of")
        expected = {
            "provider": source,
            "entitlement": entitlement,
            "dataset": EXECUTION_QUOTE_DATASET,
            "instrument_ids": [binding.instrument_id],
            "fields": list(EXECUTION_QUOTE_FIELDS),
            "frequency": "event",
            "adjustment": "raw",
            "date_range": [observed.date().isoformat(), observed.date().isoformat()],
            "vintage_as_of": acquired.isoformat(),
            "schema_version": EXECUTION_QUOTE_SCHEMA_VERSION,
            "parameters": {"currency": currency, "symbol": binding.symbol},
        }
        if vintage != acquired:
            raise ConfirmedQuoteError(
                f"quote request as-of/acquired_at mismatch for {binding.symbol}"
            )
        if dict(request) != expected:
            parameters = request.get("parameters")
            if isinstance(parameters, Mapping) and any(
                str(key).lower() in {"backfill", "historical", "replay"}
                for key in parameters
            ):
                raise ConfirmedQuoteError(
                    f"backfill quote request is forbidden for {binding.symbol}"
                )
            raise ConfirmedQuoteError(
                f"quote catalog request contract mismatch for {binding.symbol}"
            )


__all__ = [
    "ConfirmedQuoteAdapter",
    "ConfirmedQuoteError",
    "ConfirmedQuoteSnapshot",
    "EXECUTION_QUOTE_DATASET",
    "EXECUTION_QUOTE_FIELDS",
    "EXECUTION_QUOTE_SCHEMA_VERSION",
    "QUOTE_SNAPSHOT_SCHEMA_VERSION",
    "QuoteVersionBinding",
]
