"""Immutable domain boundary for the multi-strategy research platform.

The platform core deliberately has no filesystem, database, network or clock dependency.  A
strategy receives a :class:`RunContext` and immutable payloads; it cannot infer "today" or find a
mutable ``outputs/latest`` directory through this module.  Persistence and market-data adapters
belong outside this boundary.

This is the phase-zero contract described by ``ADR-001-multi-strategy-platform.md``.  Nothing in
this module labels research output as validated or executable.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable


PLATFORM_API_VERSION = "1.0.0"
NOT_VALIDATED = "NOT VALIDATED"
VALIDATION_STATUS = NOT_VALIDATED

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PlatformContractError(ValueError):
    """A platform object or plugin violates the immutable contract."""


class DuplicatePluginError(PlatformContractError):
    """The same ``strategy_id`` and version is already registered."""


class PluginNotFoundError(LookupError):
    """No plugin exists for the requested exact identity."""


class AmbiguousPluginVersionError(LookupError):
    """An unversioned lookup matched more than one plugin version."""


class ReconciliationError(PlatformContractError):
    """Position intents do not reconcile to their declared portfolio totals."""


K = TypeVar("K", bound=str)
V = TypeVar("V")


class FrozenMapping(Mapping[K, V], Generic[K, V]):
    """A deterministic, hashable and recursively immutable string-keyed mapping."""

    __slots__ = ("_items", "_lookup", "_hash")

    def __init__(self, source: Mapping[K, V] | Iterable[tuple[K, V]] = ()) -> None:
        raw = dict(source)
        if any(not isinstance(key, str) or not key for key in raw):
            raise PlatformContractError("frozen mapping keys must be non-empty strings")
        items = tuple(sorted(((key, freeze(value)) for key, value in raw.items()), key=lambda x: x[0]))
        self._items = items
        self._lookup = dict(items)
        try:
            self._hash = hash(items)
        except TypeError as exc:  # pragma: no cover - freeze normally catches unsupported values
            raise PlatformContractError("frozen mapping contains an unhashable value") from exc

    def __getitem__(self, key: K) -> V:
        return self._lookup[key]

    def __iter__(self) -> Iterator[K]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        body = ", ".join(f"{key!r}: {value!r}" for key, value in self._items)
        return f"FrozenMapping({{{body}}})"


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PlatformContractError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _require_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PlatformContractError(f"{field_name} must be a non-empty stable identifier")
    return value


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError(f"{field_name} must be non-empty")
    return value.strip()


def _require_sha256(value: str, field_name: str) -> str:
    value = _require_text(value, field_name).lower()
    if not _SHA256_RE.fullmatch(value):
        raise PlatformContractError(f"{field_name} must be a full SHA-256 digest")
    return value


def _finite(value: float | int, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PlatformContractError(f"{field_name} must be finite")
    return float(value)


def _semver_key(version: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]]:
    match = _SEMVER_RE.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise PlatformContractError(f"invalid semantic version: {version!r}")
    prerelease = match.group(4)
    # Stable releases sort after their prereleases. Numeric prerelease identifiers sort before text.
    pre_key: tuple[tuple[int, int | str], ...] = ((2, ""),) if prerelease is None else tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".")
    )
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), pre_key


def freeze(value: Any) -> Any:
    """Recursively freeze JSON-like data and reject mutable/ambiguous values.

    Tuples remain tuples, lists become tuples, mappings become :class:`FrozenMapping`, and sets are
    deterministically ordered.  Non-finite floats and naive datetimes are rejected because they do
    not have a portable canonical representation.
    """

    if isinstance(value, FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        frozen = tuple(freeze(item) for item in value)
        return tuple(sorted(frozen, key=canonical_json))
    if isinstance(value, datetime):
        return _aware_utc(value, "datetime")
    if isinstance(value, float) and not math.isfinite(value):
        raise PlatformContractError("non-finite floats are not canonical")
    if value is None or isinstance(value, (str, int, float, bool, date, Enum)) or is_dataclass(value):
        return value
    raise PlatformContractError(f"unsupported immutable payload type: {type(value).__name__}")


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, datetime):
        dt = _aware_utc(value, "datetime")
        return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PlatformContractError("canonical JSON requires string mapping keys")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=lambda item: canonical_json(item))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PlatformContractError("non-finite floats are not canonical")
        return 0.0 if value == 0 else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise PlatformContractError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON for a platform object or immutable payload."""

    return json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_hash(value: Any) -> str:
    """Full SHA-256 over :func:`canonical_json`."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class AssetClass(str, Enum):
    EQUITY = "equity"
    FUTURE = "future"
    OPTION = "option"
    FX = "fx"
    CASH = "cash"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Finality(str, Enum):
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    FINAL = "final"
    AS_FILED = "as_filed"
    REVISED = "revised"


class ExecutionStatus(str, Enum):
    NOT_SIMULATED = "not_simulated"
    SIMULATED = "simulated"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class StrategyManifest:
    strategy_id: str
    name: str
    version: str
    description: str = ""
    asset_classes: tuple[AssetClass | str, ...] = ()
    api_version: str = PLATFORM_API_VERSION
    validation_status: str = NOT_VALIDATED

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _require_id(self.strategy_id, "strategy_id"))
        object.__setattr__(self, "name", _require_text(self.name, "name"))
        _semver_key(self.version)
        _semver_key(self.api_version)
        if self.api_version != PLATFORM_API_VERSION:
            raise PlatformContractError(
                f"plugin API {self.api_version} is incompatible with {PLATFORM_API_VERSION}"
            )
        if self.validation_status != NOT_VALIDATED:
            raise PlatformContractError("new platform plugins must remain NOT VALIDATED")
        try:
            classes = tuple(AssetClass(item) for item in self.asset_classes)
        except ValueError as exc:
            raise PlatformContractError(f"unsupported asset class: {exc}") from exc
        if len(set(classes)) != len(classes):
            raise PlatformContractError("asset_classes contains duplicates")
        object.__setattr__(self, "asset_classes", tuple(sorted(classes, key=lambda item: item.value)))

    @property
    def identity(self) -> str:
        return f"{self.strategy_id}@{self.version}"

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self)


DateBoundary = date | datetime | str


def _normalise_boundary(value: DateBoundary, field_name: str) -> DateBoundary:
    if isinstance(value, datetime):
        return _aware_utc(value, field_name)
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise PlatformContractError(f"{field_name} must be an ISO date") from exc
    if not isinstance(value, date):
        raise PlatformContractError(f"{field_name} must be a date or timezone-aware datetime")
    return value


def _boundary_order(value: DateBoundary) -> datetime:
    if isinstance(value, datetime):
        return _aware_utc(value, "date boundary")
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class DatasetRequest:
    """A vendor-neutral request whose hash is the download de-duplication key."""

    provider: str
    entitlement: str
    dataset: str
    instrument_ids: tuple[str, ...]
    fields: tuple[str, ...]
    frequency: str
    adjustment: str
    start: DateBoundary
    end: DateBoundary
    as_of: datetime
    schema_version: str
    parameters: Mapping[str, Any] = FrozenMapping()

    def __post_init__(self) -> None:
        for name in ("provider", "entitlement", "dataset", "frequency", "adjustment", "schema_version"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        instruments = tuple(_require_id(item, "instrument_id") for item in self.instrument_ids)
        fields_ = tuple(_require_text(item, "field") for item in self.fields)
        if not instruments or not fields_:
            raise PlatformContractError("dataset requests require instruments and fields")
        if len(set(instruments)) != len(instruments) or len(set(fields_)) != len(fields_):
            raise PlatformContractError("dataset requests cannot contain duplicate instruments or fields")
        object.__setattr__(self, "instrument_ids", tuple(sorted(instruments)))
        object.__setattr__(self, "fields", tuple(sorted(fields_)))
        start = _normalise_boundary(self.start, "start")
        end = _normalise_boundary(self.end, "end")
        if _boundary_order(start) > _boundary_order(end):
            raise PlatformContractError("dataset request start is after end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        object.__setattr__(self, "parameters", FrozenMapping(self.parameters))

    @property
    def date_range(self) -> tuple[DateBoundary, DateBoundary]:
        return self.start, self.end

    @property
    def vintage_as_of(self) -> datetime:
        """ADR wording retained as a read-only alias for ``as_of``."""

        return self.as_of

    def canonical_dict(self) -> dict[str, Any]:
        """Canonical download identity in the field names used by ADR-001."""

        return {
            "provider": self.provider,
            "entitlement": self.entitlement,
            "dataset": self.dataset,
            "instrument_ids": list(self.instrument_ids),
            "fields": list(self.fields),
            "frequency": self.frequency,
            "adjustment": self.adjustment,
            "date_range": [self.start.isoformat(), self.end.isoformat()],
            "vintage_as_of": self.as_of.isoformat(),
            "schema_version": self.schema_version,
            "parameters": _canonical(self.parameters),
        }

    @property
    def dedupe_key(self) -> str:
        return canonical_hash(self.canonical_dict())

    @property
    def key(self) -> str:
        return self.dedupe_key

    @property
    def identity_hash(self) -> str:
        return self.dedupe_key


@dataclass(frozen=True, slots=True)
class DatasetVersionRef:
    """Immutable reference placed in a run; payload storage is owned by the data plane."""

    dataset_id: str
    version_id: str
    content_hash: str
    observed_at: datetime
    acquired_at: datetime
    effective_at: datetime
    finality: Finality | str
    schema_version: str
    request_hash: str = ""

    def __post_init__(self) -> None:
        for name in ("dataset_id", "version_id"):
            object.__setattr__(self, name, _require_id(getattr(self, name), name))
        object.__setattr__(self, "content_hash", _require_sha256(self.content_hash, "content_hash"))
        object.__setattr__(self, "schema_version", _require_text(self.schema_version, "schema_version"))
        if self.request_hash:
            object.__setattr__(self, "request_hash", _require_sha256(self.request_hash, "request_hash"))
        object.__setattr__(self, "observed_at", _aware_utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "acquired_at", _aware_utc(self.acquired_at, "acquired_at"))
        object.__setattr__(self, "effective_at", _aware_utc(self.effective_at, "effective_at"))
        if self.observed_at > self.acquired_at:
            raise PlatformContractError("observed_at cannot be after acquired_at")
        try:
            object.__setattr__(self, "finality", Finality(self.finality))
        except ValueError as exc:
            raise PlatformContractError(f"invalid finality: {self.finality!r}") from exc

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self)

    @property
    def content_sha256(self) -> str:
        return self.content_hash

    @property
    def request_key(self) -> str:
        return self.request_hash


@dataclass(frozen=True, slots=True)
class RunContext:
    workspace_id: str
    strategy_id: str
    portfolio_id: str
    run_id: str
    as_of: datetime
    run_started_at: datetime
    dataset_versions: Mapping[str, DatasetVersionRef]
    model_version: str
    mandate: Mapping[str, Any]
    seed: int
    input_artifacts: Mapping[str, str] = FrozenMapping()

    def __post_init__(self) -> None:
        for name in ("workspace_id", "strategy_id", "portfolio_id", "run_id", "model_version"):
            object.__setattr__(self, name, _require_id(getattr(self, name), name))
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        object.__setattr__(
            self, "run_started_at", _aware_utc(self.run_started_at, "run_started_at")
        )
        if self.as_of > self.run_started_at:
            raise PlatformContractError("as_of cannot be after run_started_at")
        versions = FrozenMapping(self.dataset_versions)
        if any(not isinstance(ref, DatasetVersionRef) for ref in versions.values()):
            raise PlatformContractError("dataset_versions values must be DatasetVersionRef objects")
        future_versions = [
            name
            for name, ref in versions.items()
            if ref.observed_at > self.as_of or ref.acquired_at > self.run_started_at
        ]
        if future_versions:
            raise PlatformContractError(
                "dataset version was not observable at as_of or acquired by run_started_at: "
                + ", ".join(future_versions)
            )
        object.__setattr__(self, "dataset_versions", versions)
        object.__setattr__(self, "mandate", FrozenMapping(self.mandate))
        artifacts = {
            _require_id(name, "input artifact name"): _require_sha256(
                digest, f"input_artifacts[{name}]"
            )
            for name, digest in self.input_artifacts.items()
        }
        object.__setattr__(self, "input_artifacts", FrozenMapping(artifacts))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise PlatformContractError("seed must be a non-negative integer")

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self)

    @property
    def context_hash(self) -> str:
        return self.identity_hash


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    asset_class: AssetClass | str
    currency: str
    symbol: str | None = None
    exchange: str | None = None
    underlying_instrument_id: str | None = None
    expiry: date | None = None
    contract_code: str | None = None
    multiplier: float = 1.0
    metadata: Mapping[str, Any] = FrozenMapping()

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _require_id(self.instrument_id, "instrument_id"))
        try:
            kind = AssetClass(self.asset_class)
        except ValueError as exc:
            raise PlatformContractError(f"unsupported asset_class: {self.asset_class!r}") from exc
        object.__setattr__(self, "asset_class", kind)
        currency = _require_text(self.currency, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise PlatformContractError("currency must be a three-letter code")
        object.__setattr__(self, "currency", currency)
        for name in ("symbol", "exchange", "contract_code"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, name))
        if self.underlying_instrument_id is not None:
            object.__setattr__(
                self,
                "underlying_instrument_id",
                _require_id(self.underlying_instrument_id, "underlying_instrument_id"),
            )
        multiplier = _finite(self.multiplier, "multiplier")
        if multiplier <= 0:
            raise PlatformContractError("multiplier must be positive")
        object.__setattr__(self, "multiplier", multiplier)
        if kind is AssetClass.FUTURE:
            if self.expiry is None or self.exchange is None or self.contract_code is None:
                raise PlatformContractError("futures require expiry, exchange and contract_code")
        if self.expiry is not None and (
            not isinstance(self.expiry, date) or isinstance(self.expiry, datetime)
        ):
            raise PlatformContractError("expiry must be a date")
        object.__setattr__(self, "metadata", FrozenMapping(self.metadata))

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self)


@dataclass(frozen=True, slots=True)
class PositionIntent:
    instrument_id: str
    portfolio_id: str
    direction: Direction | str
    sleeve_id: str = "core"
    leg_id: str | None = None
    pair_id: str | None = None
    weight: float | None = None
    notional: float | None = None
    units: float | None = None
    multiplier: float = 1.0
    currency: str = "USD"
    delta: float | None = None
    margin: float | None = None
    metadata: Mapping[str, Any] = FrozenMapping()

    def __post_init__(self) -> None:
        for name in ("instrument_id", "portfolio_id", "sleeve_id"):
            object.__setattr__(self, name, _require_id(getattr(self, name), name))
        for name in ("leg_id", "pair_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_id(value, name))
        try:
            direction = Direction(self.direction)
        except ValueError as exc:
            raise PlatformContractError(f"invalid direction: {self.direction!r}") from exc
        object.__setattr__(self, "direction", direction)
        sizes: list[float] = []
        for name in ("weight", "notional", "units"):
            value = getattr(self, name)
            if value is not None:
                value = _finite(value, name)
                object.__setattr__(self, name, value)
                sizes.append(value)
        if not sizes:
            raise PlatformContractError("an intent requires weight, notional or units")
        nonzero = [value for value in sizes if abs(value) > 0]
        if direction is Direction.LONG and (not nonzero or any(value < 0 for value in nonzero)):
            raise PlatformContractError("long intent sizes must be positive")
        if direction is Direction.SHORT and (not nonzero or any(value > 0 for value in nonzero)):
            raise PlatformContractError("short intent sizes must be negative")
        if direction is Direction.FLAT and nonzero:
            raise PlatformContractError("flat intent sizes must be zero")
        multiplier = _finite(self.multiplier, "multiplier")
        if multiplier <= 0:
            raise PlatformContractError("multiplier must be positive")
        object.__setattr__(self, "multiplier", multiplier)
        currency = _require_text(self.currency, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise PlatformContractError("currency must be a three-letter code")
        object.__setattr__(self, "currency", currency)
        if self.delta is not None:
            object.__setattr__(self, "delta", _finite(self.delta, "delta"))
        if self.margin is not None:
            margin = _finite(self.margin, "margin")
            if margin < 0:
                raise PlatformContractError("margin cannot be negative")
            object.__setattr__(self, "margin", margin)
        object.__setattr__(self, "metadata", FrozenMapping(self.metadata))

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self)


@dataclass(frozen=True, slots=True)
class IntentReconciliation:
    portfolio_id: str
    count: int
    net_weight: float
    gross_weight: float
    net_notional: float
    gross_notional: float
    net_units: float
    gross_units: float
    cash_weight: float | None
    reconciles: bool
    reasons: tuple[str, ...]


def reconcile_intents(
    intents: Iterable[PositionIntent],
    *,
    expected_portfolio_id: str | None = None,
    cash_weight: float | None = None,
    expected_net_weight: float | None = None,
    expected_gross_weight: float | None = None,
    tolerance: float = 1e-9,
) -> IntentReconciliation:
    """Summarise signed intents and optionally enforce portfolio totals.

    No implicit absolute-value or long-only conversion occurs.  Pair legs therefore preserve their
    signed net and gross exposures.  Requested checks fail closed when any intent lacks the sizing
    field needed for that check.
    """

    rows = tuple(intents)
    if not rows:
        raise ReconciliationError("cannot reconcile an empty intent set")
    if not all(isinstance(row, PositionIntent) for row in rows):
        raise ReconciliationError("all rows must be PositionIntent objects")
    portfolios = {row.portfolio_id for row in rows}
    if len(portfolios) != 1:
        raise ReconciliationError("intents span multiple portfolios")
    portfolio_id = next(iter(portfolios))
    if expected_portfolio_id is not None and portfolio_id != expected_portfolio_id:
        raise ReconciliationError("intent portfolio_id does not match RunContext")
    keys = [(row.sleeve_id, row.pair_id, row.leg_id, row.instrument_id) for row in rows]
    if len(set(keys)) != len(keys):
        raise ReconciliationError("duplicate position intent identity")
    tolerance = _finite(tolerance, "tolerance")
    if tolerance <= 0:
        raise ReconciliationError("tolerance must be positive")

    weights = [row.weight for row in rows if row.weight is not None]
    notionals = [row.notional for row in rows if row.notional is not None]
    units = [row.units for row in rows if row.units is not None]
    net_weight, gross_weight = sum(weights), sum(abs(value) for value in weights)
    net_notional, gross_notional = sum(notionals), sum(abs(value) for value in notionals)
    net_units, gross_units = sum(units), sum(abs(value) for value in units)
    reasons: list[str] = []
    if cash_weight is not None:
        cash_weight = _finite(cash_weight, "cash_weight")
        if len(weights) != len(rows):
            reasons.append("cash reconciliation requires weight on every intent")
        elif abs(net_weight + cash_weight - 1.0) > tolerance:
            reasons.append("net weight plus cash does not equal 1")
    if expected_net_weight is not None:
        expected_net_weight = _finite(expected_net_weight, "expected_net_weight")
        if len(weights) != len(rows):
            reasons.append("net-weight check requires weight on every intent")
        elif abs(net_weight - expected_net_weight) > tolerance:
            reasons.append("net weight differs from expected_net_weight")
    if expected_gross_weight is not None:
        expected_gross_weight = _finite(expected_gross_weight, "expected_gross_weight")
        if len(weights) != len(rows):
            reasons.append("gross-weight check requires weight on every intent")
        elif abs(gross_weight - expected_gross_weight) > tolerance:
            reasons.append("gross weight differs from expected_gross_weight")
    return IntentReconciliation(
        portfolio_id=portfolio_id,
        count=len(rows),
        net_weight=net_weight,
        gross_weight=gross_weight,
        net_notional=net_notional,
        gross_notional=gross_notional,
        net_units=net_units,
        gross_units=gross_units,
        cash_weight=cash_weight,
        reconciles=not reasons,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class DataSnapshot:
    as_of: datetime
    dataset_versions: Mapping[str, DatasetVersionRef]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        versions = FrozenMapping(self.dataset_versions)
        if any(not isinstance(ref, DatasetVersionRef) for ref in versions.values()):
            raise PlatformContractError("dataset_versions values must be DatasetVersionRef objects")
        future_versions = [name for name, ref in versions.items() if ref.observed_at > self.as_of]
        if future_versions:
            raise PlatformContractError(
                "dataset version was not observable at snapshot as_of: " + ", ".join(future_versions)
            )
        object.__setattr__(self, "dataset_versions", versions)
        object.__setattr__(self, "payload", FrozenMapping(self.payload))

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self)


@dataclass(frozen=True, slots=True)
class Universe:
    as_of: datetime
    instruments: tuple[Instrument, ...]
    validation_status: str = NOT_VALIDATED

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        instruments = tuple(self.instruments)
        if not instruments or any(not isinstance(item, Instrument) for item in instruments):
            raise PlatformContractError("universe requires Instrument objects")
        ids = [item.instrument_id for item in instruments]
        if len(set(ids)) != len(ids):
            raise PlatformContractError("universe contains duplicate instrument IDs")
        object.__setattr__(self, "instruments", instruments)
        if self.validation_status != NOT_VALIDATED:
            raise PlatformContractError("reference universe must remain NOT VALIDATED")


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    strategy_id: str
    instrument_id: str
    as_of: datetime
    value: float
    horizon: str = "unspecified"
    confidence: float | None = None
    metadata: Mapping[str, Any] = FrozenMapping()

    def __post_init__(self) -> None:
        for name in ("signal_id", "strategy_id", "instrument_id"):
            object.__setattr__(self, name, _require_id(getattr(self, name), name))
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        object.__setattr__(self, "value", _finite(self.value, "value"))
        object.__setattr__(self, "horizon", _require_text(self.horizon, "horizon"))
        if self.confidence is not None:
            confidence = _finite(self.confidence, "confidence")
            if not 0 <= confidence <= 1:
                raise PlatformContractError("confidence must be between 0 and 1")
            object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "metadata", FrozenMapping(self.metadata))


@dataclass(frozen=True, slots=True)
class SignalFrame:
    strategy_id: str
    run_id: str
    as_of: datetime
    signals: tuple[Signal, ...]
    validation_status: str = NOT_VALIDATED

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _require_id(self.strategy_id, "strategy_id"))
        object.__setattr__(self, "run_id", _require_id(self.run_id, "run_id"))
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        values = tuple(self.signals)
        if any(not isinstance(item, Signal) for item in values):
            raise PlatformContractError("signals must contain Signal objects")
        if any(item.strategy_id != self.strategy_id or item.as_of != self.as_of for item in values):
            raise PlatformContractError("signal frame identity does not match its rows")
        object.__setattr__(self, "signals", values)
        if self.validation_status != NOT_VALIDATED:
            raise PlatformContractError("signals must remain NOT VALIDATED")


@dataclass(frozen=True, slots=True)
class Attribution:
    strategy_id: str
    portfolio_id: str
    run_id: str
    component: str
    contribution: float
    currency: str = "USD"
    metadata: Mapping[str, Any] = FrozenMapping()

    def __post_init__(self) -> None:
        for name in ("strategy_id", "portfolio_id", "run_id"):
            object.__setattr__(self, name, _require_id(getattr(self, name), name))
        object.__setattr__(self, "component", _require_text(self.component, "component"))
        object.__setattr__(self, "contribution", _finite(self.contribution, "contribution"))
        currency = _require_text(self.currency, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise PlatformContractError("currency must be a three-letter code")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "metadata", FrozenMapping(self.metadata))


@dataclass(frozen=True, slots=True)
class AttributionFrame:
    strategy_id: str
    portfolio_id: str
    run_id: str
    as_of: datetime
    attributions: tuple[Attribution, ...] = ()
    validation_status: str = NOT_VALIDATED
    reason: str = "no canonical attribution has been produced"

    def __post_init__(self) -> None:
        for name in ("strategy_id", "portfolio_id", "run_id"):
            object.__setattr__(self, name, _require_id(getattr(self, name), name))
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        values = tuple(self.attributions)
        if any(not isinstance(item, Attribution) for item in values):
            raise PlatformContractError("attributions must contain Attribution objects")
        if any(
            item.strategy_id != self.strategy_id
            or item.portfolio_id != self.portfolio_id
            or item.run_id != self.run_id
            for item in values
        ):
            raise PlatformContractError("attribution frame identity does not match its rows")
        object.__setattr__(self, "attributions", values)
        if self.validation_status != NOT_VALIDATED:
            raise PlatformContractError("attribution must remain NOT VALIDATED")
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    strategy_id: str
    portfolio_id: str
    run_id: str
    as_of: datetime
    status: ExecutionStatus | str
    requested: tuple[PositionIntent, ...]
    filled: tuple[PositionIntent, ...] = ()
    costs: float = 0.0
    validation_status: str = NOT_VALIDATED
    message: str = "research-only; no broker order was submitted"

    def __post_init__(self) -> None:
        for name in ("strategy_id", "portfolio_id", "run_id"):
            object.__setattr__(self, name, _require_id(getattr(self, name), name))
        object.__setattr__(self, "as_of", _aware_utc(self.as_of, "as_of"))
        try:
            status = ExecutionStatus(self.status)
        except ValueError as exc:
            raise PlatformContractError(f"invalid execution status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        requested, filled = tuple(self.requested), tuple(self.filled)
        if any(not isinstance(item, PositionIntent) for item in requested + filled):
            raise PlatformContractError("execution rows must be PositionIntent objects")
        if any(item.portfolio_id != self.portfolio_id for item in requested + filled):
            raise PlatformContractError("execution rows have the wrong portfolio_id")
        if status is ExecutionStatus.NOT_SIMULATED and filled:
            raise PlatformContractError("NOT_SIMULATED execution cannot contain fills")
        object.__setattr__(self, "requested", requested)
        object.__setattr__(self, "filled", filled)
        costs = _finite(self.costs, "costs")
        if costs < 0:
            raise PlatformContractError("costs cannot be negative")
        object.__setattr__(self, "costs", costs)
        if self.validation_status != NOT_VALIDATED:
            raise PlatformContractError("execution summaries must remain NOT VALIDATED")
        object.__setattr__(self, "message", _require_text(self.message, "message"))


# The name in ADR-001 remains valid while callers migrate to the more explicit Summary name.
ExecutionReport = ExecutionSummary


@runtime_checkable
class StrategyPlugin(Protocol):
    def manifest(self) -> StrategyManifest: ...

    def required_datasets(self) -> tuple[DatasetRequest, ...]: ...

    def build_universe(self, snapshot: DataSnapshot) -> Universe: ...

    def generate_signals(self, ctx: RunContext) -> SignalFrame: ...

    def construct_targets(self, ctx: RunContext) -> tuple[PositionIntent, ...]: ...

    def simulate_execution(self, ctx: RunContext) -> ExecutionSummary: ...

    def export_attribution(self, ctx: RunContext) -> AttributionFrame: ...


class StrategyRegistry:
    """Thread-safe registry keyed by the exact ``strategy_id`` and semantic version."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plugins: dict[tuple[str, str], tuple[StrategyManifest, StrategyPlugin]] = {}

    @staticmethod
    def _validate_plugin(plugin: StrategyPlugin) -> StrategyManifest:
        for method in (
            "manifest",
            "required_datasets",
            "build_universe",
            "generate_signals",
            "construct_targets",
            "simulate_execution",
            "export_attribution",
        ):
            if not callable(getattr(plugin, method, None)):
                raise PlatformContractError(f"plugin is missing callable {method}()")
        manifest = plugin.manifest()
        if not isinstance(manifest, StrategyManifest):
            raise PlatformContractError("manifest() must return StrategyManifest")
        _semver_key(manifest.version)
        requests = plugin.required_datasets()
        if not isinstance(requests, tuple) or any(not isinstance(item, DatasetRequest) for item in requests):
            raise PlatformContractError("required_datasets() must return a tuple of DatasetRequest objects")
        if len({item.dedupe_key for item in requests}) != len(requests):
            raise PlatformContractError("required_datasets() returned duplicate requests")
        return manifest

    def register(self, plugin: StrategyPlugin) -> StrategyManifest:
        manifest = self._validate_plugin(plugin)
        key = manifest.strategy_id, manifest.version
        with self._lock:
            if key in self._plugins:
                raise DuplicatePluginError(f"plugin {manifest.identity} is already registered")
            self._plugins[key] = manifest, plugin
        return manifest

    def get(self, strategy_id: str, version: str | None = None) -> StrategyPlugin:
        strategy_id = _require_id(strategy_id, "strategy_id")
        with self._lock:
            if version is not None:
                _semver_key(version)
                try:
                    return self._plugins[(strategy_id, version)][1]
                except KeyError as exc:
                    raise PluginNotFoundError(f"plugin {strategy_id}@{version} is not registered") from exc
            matches = [entry[1] for (sid, _), entry in self._plugins.items() if sid == strategy_id]
            if not matches:
                raise PluginNotFoundError(f"strategy {strategy_id} is not registered")
            if len(matches) > 1:
                raise AmbiguousPluginVersionError(
                    f"strategy {strategy_id} has multiple versions; request one explicitly"
                )
            return matches[0]

    resolve = get

    def latest(self, strategy_id: str) -> StrategyPlugin:
        strategy_id = _require_id(strategy_id, "strategy_id")
        with self._lock:
            matches = [
                (version, entry[1])
                for (sid, version), entry in self._plugins.items()
                if sid == strategy_id
            ]
            if not matches:
                raise PluginNotFoundError(f"strategy {strategy_id} is not registered")
            return max(matches, key=lambda item: _semver_key(item[0]))[1]

    def manifests(self) -> tuple[StrategyManifest, ...]:
        with self._lock:
            values = [entry[0] for entry in self._plugins.values()]
        return tuple(sorted(values, key=lambda item: (item.strategy_id, _semver_key(item.version))))

    def __len__(self) -> int:
        with self._lock:
            return len(self._plugins)


# Shorter alias for clients that use the ADR's generic "registry" wording.
PluginRegistry = StrategyRegistry


PayloadSource = Mapping[str, Any] | Callable[[RunContext], Mapping[str, Any]]
InstrumentSource = Mapping[str, Instrument] | Callable[[str], Instrument]


class StockSelectAdapter:
    """Reference adapter over injected, immutable legacy Stock Select payloads.

    It does not import ``ai_semi_intel``, open ``outputs/latest`` or read a clock.  Mapping inputs are
    deep-frozen at construction; callable inputs are invoked only with the explicit RunContext and
    their return value is frozen before use.
    """

    def __init__(
        self,
        snapshot: PayloadSource,
        run: PayloadSource,
        *,
        instruments: InstrumentSource | None = None,
        dataset_requests: Iterable[DatasetRequest] = (),
        version: str = "0.1.0",
    ) -> None:
        self._snapshot = self._freeze_source(snapshot, "snapshot")
        self._run = self._freeze_source(run, "run")
        if instruments is None:
            self._instruments: InstrumentSource | None = None
        elif isinstance(instruments, Mapping):
            values = FrozenMapping(instruments)
            if any(not isinstance(value, Instrument) for value in values.values()):
                raise PlatformContractError("instruments mapping values must be Instrument objects")
            self._instruments = values
        elif callable(instruments):
            self._instruments = instruments
        else:
            raise PlatformContractError("instruments must be a mapping or callable")
        requests = tuple(dataset_requests)
        if any(not isinstance(request, DatasetRequest) for request in requests):
            raise PlatformContractError("dataset_requests must contain DatasetRequest objects")
        if len({request.dedupe_key for request in requests}) != len(requests):
            raise PlatformContractError("duplicate dataset request")
        self._requests = tuple(sorted(requests, key=lambda request: request.dedupe_key))
        self._manifest = StrategyManifest(
            strategy_id="stock_select",
            name="Stock Select reference adapter",
            version=version,
            description="Read-only bridge for the current AI semiconductor stock-selection snapshot",
            asset_classes=(AssetClass.EQUITY,),
        )

    @staticmethod
    def _freeze_source(source: PayloadSource, name: str) -> PayloadSource:
        if isinstance(source, Mapping):
            return FrozenMapping(source)
        if callable(source):
            return source
        raise PlatformContractError(f"{name} must be an injected mapping or callable")

    @staticmethod
    def _load(source: PayloadSource, ctx: RunContext) -> FrozenMapping[str, Any]:
        payload = source(ctx) if callable(source) else source
        if not isinstance(payload, Mapping):
            raise PlatformContractError("injected payload source must return a mapping")
        return FrozenMapping(payload)

    @staticmethod
    def _asof_date(payload: Mapping[str, Any]) -> date:
        raw = payload.get("asof_session", payload.get("date"))
        if not isinstance(raw, str):
            raise PlatformContractError("legacy payload has no explicit as-of session")
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise PlatformContractError("legacy payload as-of session is invalid") from exc

    @staticmethod
    def _ticker(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PlatformContractError("ticker must be a non-empty string")
        ticker = value.strip().upper()
        _require_id(ticker, "ticker")
        return ticker

    def _validate_payload(
        self, payload: Mapping[str, Any], ctx: RunContext, *, artifact_name: str
    ) -> None:
        if ctx.strategy_id != self._manifest.strategy_id:
            raise PlatformContractError("RunContext strategy_id does not match Stock Select")
        if payload.get("run_id") != ctx.run_id:
            raise PlatformContractError("legacy payload run_id does not match RunContext")
        if payload.get("portfolio_id") != ctx.portfolio_id:
            raise PlatformContractError("legacy payload portfolio_id does not match RunContext")
        if self._asof_date(payload) != ctx.as_of.date():
            raise PlatformContractError("legacy payload as-of session does not match RunContext")
        status = str(payload.get("validation_status", NOT_VALIDATED))
        if not status.startswith(NOT_VALIDATED):
            raise PlatformContractError("legacy payload makes an unsupported validation claim")
        expected_hash = ctx.input_artifacts.get(artifact_name)
        if expected_hash is not None and canonical_hash(payload) != expected_hash:
            raise PlatformContractError(
                f"legacy {artifact_name} payload does not match the frozen RunContext hash"
            )

    def _instrument(self, ticker: str) -> Instrument:
        ticker = self._ticker(ticker)
        source = self._instruments
        if source is None:
            return Instrument(
                instrument_id=f"equity:US:{ticker}",
                asset_class=AssetClass.EQUITY,
                currency="USD",
                symbol=ticker,
                exchange="US",
                metadata={"reference_only": True},
            )
        try:
            instrument = source(ticker) if callable(source) else source[ticker]
        except KeyError as exc:
            raise PlatformContractError(f"no permanent instrument mapping for {ticker}") from exc
        if not isinstance(instrument, Instrument):
            raise PlatformContractError("instrument resolver must return Instrument")
        return instrument

    def manifest(self) -> StrategyManifest:
        return self._manifest

    def required_datasets(self) -> tuple[DatasetRequest, ...]:
        return self._requests

    def build_universe(self, snapshot: DataSnapshot) -> Universe:
        if not isinstance(snapshot, DataSnapshot):
            raise PlatformContractError("snapshot must be DataSnapshot")
        raw = snapshot.payload.get("top")
        if not isinstance(raw, tuple) or not raw:
            raise PlatformContractError("Stock Select snapshot has no frozen top list")
        return Universe(snapshot.as_of, tuple(self._instrument(self._ticker(ticker)) for ticker in raw))

    def generate_signals(self, ctx: RunContext) -> SignalFrame:
        snapshot = self._load(self._snapshot, ctx)
        self._validate_payload(snapshot, ctx, artifact_name="legacy_snapshot")
        top = snapshot.get("top")
        composite = snapshot.get("composite")
        if not isinstance(top, tuple) or not top or not isinstance(composite, Mapping):
            raise PlatformContractError("legacy snapshot lacks top/composite signal inputs")
        signals = []
        for ticker_raw in top:
            ticker = self._ticker(ticker_raw)
            if ticker not in composite:
                raise PlatformContractError(f"missing composite score for {ticker}")
            instrument = self._instrument(ticker)
            signals.append(
                Signal(
                    signal_id=f"{ctx.run_id}:composite:{ticker}",
                    strategy_id=ctx.strategy_id,
                    instrument_id=instrument.instrument_id,
                    as_of=ctx.as_of,
                    value=_finite(composite[ticker], f"composite[{ticker}]"),
                    horizon="model-defined",
                    metadata={"source": "legacy_snapshot", "ticker": ticker},
                )
            )
        return SignalFrame(ctx.strategy_id, ctx.run_id, ctx.as_of, tuple(signals))

    def construct_targets(self, ctx: RunContext) -> tuple[PositionIntent, ...]:
        run = self._load(self._run, ctx)
        self._validate_payload(run, ctx, artifact_name="legacy_run")
        portfolio = run.get("portfolio")
        if not isinstance(portfolio, Mapping):
            raise PlatformContractError("legacy run has no portfolio")
        positions = portfolio.get("positions")
        if not isinstance(positions, tuple) or not positions:
            raise PlatformContractError("legacy portfolio has no positions")
        intents: list[PositionIntent] = []
        for row in positions:
            if not isinstance(row, Mapping):
                raise PlatformContractError("legacy position must be a mapping")
            ticker = self._ticker(row.get("ticker"))
            weight = _finite(row.get("target_executable_weight"), f"weight[{ticker}]")
            if abs(weight) == 0:
                continue
            instrument = self._instrument(ticker)
            intents.append(
                PositionIntent(
                    instrument_id=instrument.instrument_id,
                    portfolio_id=ctx.portfolio_id,
                    sleeve_id=ctx.strategy_id,
                    direction=Direction.LONG if weight > 0 else Direction.SHORT,
                    weight=weight,
                    multiplier=instrument.multiplier,
                    currency=instrument.currency,
                    metadata={"ticker": ticker, "source": "legacy_run"},
                )
            )
        if not intents:
            raise PlatformContractError("legacy portfolio contains no non-zero targets")
        cash = _finite(portfolio.get("cash"), "portfolio.cash")
        check = reconcile_intents(
            intents, expected_portfolio_id=ctx.portfolio_id, cash_weight=cash, tolerance=1e-6
        )
        legacy_reconciles = portfolio.get("reconciles")
        if legacy_reconciles is not True or not check.reconciles:
            raise ReconciliationError("legacy target portfolio does not reconcile: " + "; ".join(check.reasons))
        return tuple(intents)

    def simulate_execution(self, ctx: RunContext) -> ExecutionSummary:
        intents = self.construct_targets(ctx)
        return ExecutionSummary(
            strategy_id=ctx.strategy_id,
            portfolio_id=ctx.portfolio_id,
            run_id=ctx.run_id,
            as_of=ctx.as_of,
            status=ExecutionStatus.NOT_SIMULATED,
            requested=intents,
            message="reference adapter emits targets only; no ledger, fills or broker orders",
        )

    def export_attribution(self, ctx: RunContext) -> AttributionFrame:
        run = self._load(self._run, ctx)
        self._validate_payload(run, ctx, artifact_name="legacy_run")
        return AttributionFrame(
            strategy_id=ctx.strategy_id,
            portfolio_id=ctx.portfolio_id,
            run_id=ctx.run_id,
            as_of=ctx.as_of,
            reason="legacy snapshot contains no canonical continuous-ledger attribution",
        )


def build_stock_select_adapter(
    snapshot: Mapping[str, Any],
    run: Mapping[str, Any],
    *,
    instruments: InstrumentSource | None = None,
    dataset_requests: Iterable[DatasetRequest] = (),
    version: str = "0.1.0",
) -> StockSelectAdapter:
    """Build a file-free Stock Select reference adapter from immutable payload copies."""

    return StockSelectAdapter(
        snapshot,
        run,
        instruments=instruments,
        dataset_requests=dataset_requests,
        version=version,
    )


__all__ = [
    "PLATFORM_API_VERSION",
    "NOT_VALIDATED",
    "VALIDATION_STATUS",
    "PlatformContractError",
    "DuplicatePluginError",
    "PluginNotFoundError",
    "AmbiguousPluginVersionError",
    "ReconciliationError",
    "FrozenMapping",
    "freeze",
    "canonical_json",
    "canonical_hash",
    "AssetClass",
    "Direction",
    "Finality",
    "ExecutionStatus",
    "StrategyManifest",
    "DatasetRequest",
    "DatasetVersionRef",
    "RunContext",
    "Instrument",
    "PositionIntent",
    "IntentReconciliation",
    "reconcile_intents",
    "DataSnapshot",
    "Universe",
    "Signal",
    "SignalFrame",
    "Attribution",
    "AttributionFrame",
    "ExecutionSummary",
    "ExecutionReport",
    "StrategyPlugin",
    "StrategyRegistry",
    "PluginRegistry",
    "StockSelectAdapter",
    "build_stock_select_adapter",
]
