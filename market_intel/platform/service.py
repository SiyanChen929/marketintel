"""Application service for the versioned multi-strategy platform API.

The service is deliberately local and dependency-injected.  It can expose the
legacy Stock Select run through the new contracts and drive the append-only
paper ledger, but it has no network client and no live broker path.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .core import (
    NOT_VALIDATED,
    DatasetVersionRef,
    Finality,
    PositionIntent,
    RunContext,
    StrategyRegistry,
    build_stock_select_adapter,
    canonical_hash,
    canonical_json,
)
from .checkpoint import (
    GENESIS_CHECKPOINT_HASH,
    LedgerCheckpointError,
    LedgerCheckpointStore,
)
from .data import DatasetCatalog
from .paper import (
    CostModel,
    ExecutionMode,
    PaperBroker,
    PaperError,
    PaperMandate,
    PaperSessionBinding,
    Quote,
    QuoteLineage,
    QuoteRejected,
    Side,
)
from .quotes import (
    ConfirmedQuoteAdapter,
    ConfirmedQuoteError,
    QuoteVersionBinding,
)
from .repository import PlatformRepository
from .risk_adapter import REQUIRED_FROZEN_DATASETS
from .stock_select_data import (
    validate_frozen_benchmark_contract,
    validate_frozen_stock_select_provenance,
)


PAPER_STATUS = "NOT VALIDATED/PAPER"
PAPER_MUTATION_COMMITTED = "committed"
PAPER_MUTATION_CHECKPOINT_PENDING = "committed_checkpoint_pending"
RISK_REQUEST_SCHEMA_VERSION = "platform.risk.request.v1"
RISK_PAYLOAD_SCHEMA_VERSION = "platform.risk.payload.v1"
RISK_BINDING_SCHEMA_VERSION = "platform.risk.binding.v1"
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
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_CANONICAL_SESSION_FILE_RE = re.compile(r"^paper_[0-9a-f]{32}\.jsonl$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNIT_QUANTUM = Decimal("0.00000001")

# Adapter responses are deliberately narrower than the legacy risk workbench.
# The adapter must translate its native output into these canonical fields so a
# platform consumer cannot silently mix horizons, units, windows or portfolios.
_RISK_METRIC_SPECS: dict[str, dict[str, Any]] = {
    "var_99_1d": {
        "unit": "fraction_of_nav",
        "window": "3y",
        "window_sessions": 756,
        "horizon_days": 1,
        "confidence_level": 0.99,
        "minimum_observations": 755,
    },
    "var_99_10d": {
        "unit": "fraction_of_nav",
        "window": "3y",
        "window_sessions": 756,
        "horizon_days": 10,
        "confidence_level": 0.99,
        "minimum_observations": 746,
        "confidence_interval_required": True,
    },
    "es_97_5": {
        "unit": "fraction_of_nav",
        "window": "3y",
        "window_sessions": 756,
        "horizon_days": 10,
        "confidence_level": 0.975,
        "minimum_observations": 746,
    },
    "tracking_error": {
        "unit": "annualized_fraction",
        "window": "3y",
        "window_sessions": 756,
        "annualization_sessions": 252,
        "minimum_observations": 755,
        "benchmark_required": True,
    },
}


class PlatformServiceError(RuntimeError):
    """Base error suitable for conversion to an HTTP response."""


class PlatformInputError(PlatformServiceError):
    """The operator supplied an invalid request."""


class PlatformNotFound(PlatformServiceError):
    """A requested platform object does not exist."""


class PlatformBlocked(PlatformServiceError):
    """A fail-closed research or execution gate prevented mutation."""


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PlatformInputError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any, name: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except Exception as exc:
        raise PlatformInputError(f"{name} must be an ISO datetime") from exc
    return _aware(parsed, name)


def _plain(value: Any) -> Any:
    """Convert platform dataclasses/enums/frozen mappings into JSON values."""

    return json.loads(canonical_json(value))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise PlatformNotFound(f"missing platform input: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise PlatformBlocked(f"invalid platform input: {path.name}") from exc
    if not isinstance(value, dict):
        raise PlatformBlocked(f"platform input must be an object: {path.name}")
    return value


def _paper_operation(body: Mapping[str, Any], action: str) -> tuple[dict[str, Any], str, str]:
    """Remove server-owned idempotency fields and verify their request binding."""

    cleaned = dict(body)
    operation_id = cleaned.pop("_operation_id", None)
    supplied_hash = cleaned.pop("_request_sha256", None)
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise PlatformInputError(f"{action} requires an Idempotency-Key")
    operation_id = operation_id.strip()
    if len(operation_id) > 256:
        raise PlatformInputError("Idempotency-Key is too long")
    actual_hash = hashlib.sha256(
        json.dumps(cleaned, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    if supplied_hash != actual_hash:
        raise PlatformInputError("paper mutation request hash does not match its payload")
    return cleaned, operation_id, actual_hash


def _risk_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PlatformBlocked(f"risk adapter contract: {field} must be a finite JSON number")
    return float(value)


def _validate_risk_metric(name: str, raw: Any, spec: Mapping[str, Any]) -> None:
    if not isinstance(raw, Mapping) or not raw:
        raise PlatformBlocked(f"risk adapter contract: metric {name!r} must be a non-empty object")
    if "available" in raw and raw.get("available") is not True:
        raise PlatformBlocked(f"risk adapter contract: metric {name!r} is not available")
    value = _risk_number(raw.get("value"), f"metrics.{name}.value")
    if value < 0 or (spec.get("unit") == "fraction_of_nav" and value > 1):
        raise PlatformBlocked(
            f"risk adapter contract: metrics.{name}.value must be within [0, 1] of NAV"
            if spec.get("unit") == "fraction_of_nav"
            else f"risk adapter contract: metrics.{name}.value cannot be negative"
        )
    for field in ("unit", "window"):
        if raw.get(field) != spec[field]:
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.{field} must equal {spec[field]!r}"
            )
    window_sessions = raw.get("window_sessions")
    if (
        isinstance(window_sessions, bool)
        or not isinstance(window_sessions, int)
        or window_sessions != spec["window_sessions"]
    ):
        raise PlatformBlocked(
            f"risk adapter contract: metrics.{name}.window_sessions must equal "
            f"{spec['window_sessions']}"
        )
    observations = raw.get("observations")
    if (
        isinstance(observations, bool)
        or not isinstance(observations, int)
        or observations != int(spec["minimum_observations"])
    ):
        raise PlatformBlocked(
            f"risk adapter contract: metrics.{name}.observations must equal "
            f"{spec['minimum_observations']} for the fixed window"
        )
    if "horizon_days" in spec:
        horizon = raw.get("horizon_days")
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon != spec["horizon_days"]:
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.horizon_days must equal {spec['horizon_days']}"
            )
    if "confidence_level" in spec:
        actual = _risk_number(raw.get("confidence_level"), f"metrics.{name}.confidence_level")
        if not math.isclose(actual, float(spec["confidence_level"]), rel_tol=0.0, abs_tol=1e-12):
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.confidence_level must equal "
                f"{spec['confidence_level']}"
            )
    if "annualization_sessions" in spec:
        annualization = raw.get("annualization_sessions")
        if (
            isinstance(annualization, bool)
            or not isinstance(annualization, int)
            or annualization != spec["annualization_sessions"]
        ):
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.annualization_sessions must equal "
                f"{spec['annualization_sessions']}"
            )
    if spec.get("benchmark_required"):
        benchmark = raw.get("benchmark")
        if not isinstance(benchmark, str) or not benchmark.strip():
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.benchmark is required"
            )
        for field in ("benchmark_kind", "benchmark_source"):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                raise PlatformBlocked(
                    f"risk adapter contract: metrics.{name}.{field} is required"
                )
    if spec.get("confidence_interval_required"):
        interval = raw.get("confidence_interval")
        if not isinstance(interval, Mapping) or interval.get("available") is not True:
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.confidence_interval must be available"
            )
        if interval.get("unit") != spec["unit"]:
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.confidence_interval.unit must equal "
                f"{spec['unit']!r}"
            )
        level = _risk_number(
            interval.get("confidence_level"),
            f"metrics.{name}.confidence_interval.confidence_level",
        )
        if not 0 < level < 1:
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.confidence_interval.confidence_level "
                "must be between zero and one"
            )
        low = _risk_number(interval.get("lower"), f"metrics.{name}.confidence_interval.lower")
        high = _risk_number(interval.get("upper"), f"metrics.{name}.confidence_interval.upper")
        if (
            low < 0
            or high < low
            or not low <= value <= high
            or (spec.get("unit") == "fraction_of_nav" and high > 1)
        ):
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.confidence_interval bounds are invalid "
                "or do not contain the point estimate"
            )
        method = interval.get("method")
        if not isinstance(method, str) or not method.strip():
            raise PlatformBlocked(
                f"risk adapter contract: metrics.{name}.confidence_interval.method is required"
            )


@dataclass(frozen=True, slots=True)
class ExecutionQuote:
    """A quote plus the point-in-time evidence required for paper execution."""

    quote: Quote
    observed_at: datetime
    acquired_at: datetime
    finality: Finality | str
    source: str
    content_hash: str
    instrument_id: str
    dataset_version_id: str
    request_hash: str
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.quote, Quote):
            raise PlatformInputError("execution quote must contain a validated Quote")
        observed = _aware(self.observed_at, "observed_at")
        acquired = _aware(self.acquired_at, "acquired_at")
        if self.quote.timestamp != observed:
            raise PlatformInputError("quote timestamp must equal observed_at")
        if observed > acquired:
            raise PlatformInputError("observed_at cannot be after acquired_at")
        try:
            finality = self.finality if isinstance(self.finality, Finality) else Finality(str(self.finality))
        except ValueError as exc:
            raise PlatformInputError(f"unknown quote finality {self.finality!r}") from exc
        source = self.source.strip() if isinstance(self.source, str) else ""
        digest = self.content_hash.lower() if isinstance(self.content_hash, str) else ""
        currency = self.currency.strip().upper() if isinstance(self.currency, str) else ""
        instrument_id = self.instrument_id.strip() if isinstance(self.instrument_id, str) else ""
        version_id = (
            self.dataset_version_id.strip().lower()
            if isinstance(self.dataset_version_id, str)
            else ""
        )
        request_hash = (
            self.request_hash.strip().lower() if isinstance(self.request_hash, str) else ""
        )
        if not source:
            raise PlatformInputError("execution quote source is required")
        if not _SHA256_RE.fullmatch(digest):
            raise PlatformInputError("execution quote content_hash must be a full SHA-256")
        if len(currency) != 3 or not currency.isalpha():
            raise PlatformInputError("execution quote currency must be a three-letter code")
        if not instrument_id or ":" not in instrument_id:
            raise PlatformInputError("execution quote instrument_id is required")
        if not _SHA256_RE.fullmatch(version_id) or not _SHA256_RE.fullmatch(request_hash):
            raise PlatformInputError(
                "execution quote dataset_version_id and request_hash must be full SHA-256 digests"
            )
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "acquired_at", acquired)
        object.__setattr__(self, "finality", finality)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "dataset_version_id", version_id)
        object.__setattr__(self, "request_hash", request_hash)

    def artifact(self) -> dict[str, Any]:
        """Canonical quote bytes whose hash is registered in DatasetCatalog."""

        return {
            "schema_version": EXECUTION_QUOTE_SCHEMA_VERSION,
            "instrument_id": self.instrument_id,
            "symbol": self.quote.symbol,
            "observed_at": self.observed_at.isoformat(),
            "bid": str(self.quote.bid),
            "ask": str(self.quote.ask),
            "available_quantity": (
                None
                if self.quote.available_quantity is None
                else str(self.quote.available_quantity)
            ),
            "currency": self.currency,
            "source": self.source,
        }

    def lineage(self) -> dict[str, Any]:
        return {
            "symbol": self.quote.symbol,
            "observed_at": self.observed_at.isoformat(),
            "acquired_at": self.acquired_at.isoformat(),
            "finality": self.finality.value,
            "source": self.source,
            "content_hash": self.content_hash,
            "currency": self.currency,
            "instrument_id": self.instrument_id,
            "dataset_version_id": self.dataset_version_id,
            "request_hash": self.request_hash,
        }


QuoteLoader = Callable[[tuple[str, ...], datetime, datetime], Mapping[str, ExecutionQuote]]
# Receives a ``platform.risk.request.v1`` envelope and must return a
# ``platform.risk.payload.v1`` object bound to the request's frozen identity.
RiskLoader = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class _CurrentRun:
    snapshot: Mapping[str, Any]
    run: Mapping[str, Any]
    context: RunContext
    registered_context_hash: str
    adapter: Any


class PlatformService:
    """Boundary used by ``/api/v2/platform/*``.

    A default instance reads the two legacy JSON projections and persists only
    platform metadata plus virtual paper events.  Tests and future data adapters
    inject loaders, clocks and repositories directly.
    """

    planned_strategies = (
        ("stock_select", "Stock Select", ("equity",), "reference_adapter"),
        ("alpha_engine", "Alpha Engine", ("equity",), "adapter_not_installed"),
        ("stable_alpha", "StableAlpha", ("equity",), "adapter_not_installed"),
        ("vol_trading", "Vol Trading", ("equity", "option", "future"), "adapter_not_installed"),
        ("pairs_trading", "Pairs Trading", ("equity",), "adapter_not_installed"),
        ("commodity_arbitrage", "Commodity Arbitrage", ("future",), "adapter_not_installed"),
        ("msci_rebalance", "MSCI Rebalance", ("equity",), "adapter_not_installed"),
    )

    def __init__(
        self,
        *,
        latest_dir: str | Path | None = None,
        state_dir: str | Path | None = None,
        snapshot_loader: Callable[[], Mapping[str, Any]] | None = None,
        run_loader: Callable[[], Mapping[str, Any]] | None = None,
        quote_loader: QuoteLoader | None = None,
        risk_loader: RiskLoader | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        repository: PlatformRepository | None = None,
        catalog: DatasetCatalog | None = None,
        checkpoint_store: LedgerCheckpointStore | None = None,
        dataset_ingestor: Callable[..., Any] | None = None,
        require_versioned_lineage: bool = True,
    ) -> None:
        if latest_dir is None or state_dir is None:
            from .. import config

            latest_dir = config.LATEST if latest_dir is None else latest_dir
            state_dir = config.DATA_DIR / "platform" if state_dir is None else state_dir
        self.latest_dir = Path(latest_dir)
        self.state_dir = Path(state_dir)
        self.paper_dir = self.state_dir / "paper"
        self.paper_stage_dir = self.paper_dir / ".staging"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.paper_dir.mkdir(parents=True, exist_ok=True)
        self.paper_stage_dir.mkdir(parents=True, exist_ok=True)
        self._snapshot_loader = snapshot_loader or (lambda: _read_json(self.latest_dir / "snapshot.json"))
        self._run_loader = run_loader or (lambda: _read_json(self.latest_dir / "run.json"))
        self._legacy_loaders_injected = snapshot_loader is not None or run_loader is not None
        self._quote_loader = quote_loader
        self._risk_loader = risk_loader
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: f"paper_{uuid.uuid4().hex}")
        self.repository = repository or PlatformRepository(self.state_dir / "platform.sqlite")
        self.catalog = catalog or DatasetCatalog(self.state_dir / "catalog.sqlite")
        if checkpoint_store is not None and not isinstance(
            checkpoint_store, LedgerCheckpointStore
        ):
            raise PlatformInputError(
                "checkpoint_store does not implement the ledger checkpoint contract"
            )
        self.checkpoint_store = checkpoint_store
        self._dataset_ingestor = dataset_ingestor
        if not isinstance(require_versioned_lineage, bool):
            raise PlatformInputError("require_versioned_lineage must be a boolean")
        self.require_versioned_lineage = require_versioned_lineage
        self.registry = StrategyRegistry()
        self._lock = threading.RLock()
        from .performance import PerformanceStore
        self.performance = PerformanceStore(self.state_dir / "performance.sqlite")

    def _now(self) -> datetime:
        return _aware(self._clock(), "clock")

    def _candidate_from_payloads(
        self,
        snapshot_raw: Mapping[str, Any],
        run_raw: Mapping[str, Any],
        *,
        dataset_versions: Mapping[str, DatasetVersionRef] | None = None,
        run_started_at: datetime | None = None,
        dataset_manifest_hash: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], RunContext, Any, datetime]:
        """Validate already-read producer payloads and construct their context."""

        snapshot = dict(snapshot_raw)
        run = dict(run_raw)
        snapshot_generated_raw = snapshot.get("generated_at")
        run_generated_raw = run.get("generated_at")
        if snapshot_generated_raw is None or run_generated_raw is None:
            raise PlatformBlocked("snapshot and run must each have timezone-aware generated_at")
        snapshot_generated = _parse_datetime(snapshot_generated_raw, "snapshot.generated_at")
        generated_at = _parse_datetime(run_generated_raw, "run.generated_at")
        if snapshot_generated != generated_at:
            raise PlatformBlocked("snapshot and run generated_at identities do not match")
        run_id = str(run.get("run_id") or "").strip()
        portfolio_id = str(run.get("portfolio_id") or "").strip()
        if not run_id or not portfolio_id:
            raise PlatformBlocked("current run is missing run_id or portfolio_id")
        model_raw = str(run.get("code_hash") or run.get("config_hash") or "unknown")
        model_id = "legacy-" + re.sub(r"[^A-Za-z0-9._:/-]", "-", model_raw)[:64]
        versions = dict(dataset_versions or {})
        started = generated_at if run_started_at is None else _aware(
            run_started_at, "run_started_at"
        )
        input_artifacts = {
            "legacy_snapshot": canonical_hash(snapshot),
            "legacy_run": canonical_hash(run),
        }
        if dataset_manifest_hash is not None:
            digest = str(dataset_manifest_hash).strip().lower()
            if not _SHA256_RE.fullmatch(digest):
                raise PlatformBlocked("dataset manifest hash must be a full SHA-256")
            input_artifacts["dataset_manifest"] = digest
        context = RunContext(
            workspace_id="market_intel",
            strategy_id="stock_select",
            portfolio_id=portfolio_id,
            run_id=run_id,
            as_of=generated_at,
            run_started_at=started,
            dataset_versions=versions,
            model_version=model_id,
            mandate={
                "source": "legacy_projection",
                "actionable": run.get("actionable") is True,
                "paper_only": True,
            },
            seed=0,
            input_artifacts=input_artifacts,
        )
        adapter = build_stock_select_adapter(snapshot, run)
        # Validate cross-payload identity and target accounting before any
        # immutable run or artifact can enter the repository.
        adapter.generate_signals(context)
        adapter.construct_targets(context)
        return snapshot, run, context, adapter, generated_at

    def _current_candidate(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], RunContext, Any, datetime]:
        """Build an unversioned candidate from injected legacy projections."""

        return self._candidate_from_payloads(
            self._snapshot_loader(),
            self._run_loader(),
        )

    @staticmethod
    def _repository_context(context: RunContext, strategy_version: str) -> dict[str, Any]:
        persisted = _plain(context)
        persisted["as_of"] = context.as_of.isoformat()
        persisted["strategy_version"] = strategy_version
        return persisted

    @staticmethod
    def _context_from_repository(raw: Mapping[str, Any]) -> RunContext:
        """Rehydrate a verified repository record without consulting latest files."""

        versions_raw = raw.get("dataset_versions")
        if not isinstance(versions_raw, Mapping):
            raise PlatformBlocked("frozen RunContext dataset_versions is not an object")
        versions: dict[str, DatasetVersionRef] = {}
        try:
            for role, value in versions_raw.items():
                if not isinstance(value, Mapping):
                    raise TypeError(f"dataset version role {role!r} is not an object")
                versions[str(role)] = DatasetVersionRef(
                    dataset_id=value["dataset_id"],
                    version_id=value["version_id"],
                    content_hash=value.get("content_hash", value.get("content_sha256")),
                    observed_at=_parse_datetime(
                        value.get("observed_at"), f"dataset_versions.{role}.observed_at"
                    ),
                    acquired_at=_parse_datetime(
                        value.get("acquired_at"), f"dataset_versions.{role}.acquired_at"
                    ),
                    effective_at=_parse_datetime(
                        value.get("effective_at"), f"dataset_versions.{role}.effective_at"
                    ),
                    finality=value["finality"],
                    schema_version=value["schema_version"],
                    request_hash=value["request_hash"],
                )
            return RunContext(
                workspace_id=raw["workspace_id"],
                strategy_id=raw["strategy_id"],
                portfolio_id=raw["portfolio_id"],
                run_id=raw["run_id"],
                as_of=_parse_datetime(raw.get("as_of"), "RunContext.as_of"),
                run_started_at=_parse_datetime(
                    raw.get("run_started_at"), "RunContext.run_started_at"
                ),
                dataset_versions=versions,
                model_version=raw["model_version"],
                mandate=raw.get("mandate") or {},
                seed=raw["seed"],
                input_artifacts=raw.get("input_artifacts") or {},
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PlatformBlocked(f"frozen RunContext cannot be reconstructed: {exc}") from exc

    def sync_current_run(self, *, source_dir: str | Path | None = None) -> _CurrentRun:
        """Explicit producer ingestion, optionally from a verified run archive.

        The background publisher supplies an immutable archive while holding
        the pipeline lock. Viewer methods continue to perform no ingestion.
        """

        if source_dir is not None and self._legacy_loaders_injected:
            raise PlatformInputError("source_dir cannot override injected legacy loaders")
        source = self.latest_dir if source_dir is None else Path(source_dir)

        frozen = None
        should_freeze = self.require_versioned_lineage and (
            self._dataset_ingestor is not None or not self._legacy_loaders_injected
        )
        if should_freeze:
            ingestion_options = {}
            if self._dataset_ingestor is None:
                from .stock_select_data import freeze_stock_select_latest

                ingestor = freeze_stock_select_latest
                if source_dir is not None:
                    ingestion_options["benchmark_directory"] = self.latest_dir.parent.parent / "data"
            else:
                ingestor = self._dataset_ingestor
            try:
                frozen = ingestor(
                    latest_dir=source,
                    catalog=self.catalog,
                    acquired_at=self._now(),
                    **ingestion_options,
                )
            except (OSError, TypeError, ValueError, KeyError) as exc:
                raise PlatformBlocked(
                    f"Stock Select dataset freezing failed: {exc}"
                ) from exc
            snapshot, run, context, adapter, generated_at = self._candidate_from_payloads(
                frozen.snapshot,
                frozen.run,
                dataset_versions=frozen.dataset_versions,
                run_started_at=frozen.run_started_at,
                dataset_manifest_hash=frozen.input_manifest_hash,
            )
        elif source_dir is not None:
            snapshot, run, context, adapter, generated_at = self._candidate_from_payloads(
                _read_json(source / "snapshot.json"), _read_json(source / "run.json")
            )
        else:
            snapshot, run, context, adapter, generated_at = self._current_candidate()
        manifest = adapter.manifest()
        with self._lock:
            try:
                self.registry.get(manifest.strategy_id, manifest.version)
            except LookupError:
                self.registry.register(adapter)
            self.repository.register_strategy(_plain(manifest), at=generated_at)
            persisted_context = self._repository_context(context, manifest.version)
            artifact_payloads = {
                "legacy_snapshot": canonical_json(snapshot).encode("utf-8"),
                "legacy_run": canonical_json(run).encode("utf-8"),
            }
            if frozen is not None:
                artifact_payloads["dataset_manifest"] = canonical_json(
                    {
                        role: context.dataset_versions[role]
                        for role in sorted(context.dataset_versions)
                    }
                ).encode("utf-8")
                if hashlib.sha256(artifact_payloads["dataset_manifest"]).hexdigest() != str(
                    frozen.input_manifest_hash
                ).lower():
                    raise PlatformBlocked(
                        "Stock Select dataset manifest hash does not reconcile to frozen references"
                    )
            artifact_metadata = {
                name: {
                    "encoding": "canonical-json",
                    "role": (
                        "frozen_dataset_manifest"
                        if name == "dataset_manifest"
                        else "frozen_adapter_input"
                    ),
                }
                for name in artifact_payloads
            }
            registered = self.repository.register_run_bundle(
                persisted_context,
                generated_at=generated_at,
                artifacts=artifact_payloads,
                artifact_metadata=artifact_metadata,
                expected_sha256=dict(context.input_artifacts),
                required_artifacts=tuple(artifact_payloads),
                validation_status=NOT_VALIDATED,
            )
        return _CurrentRun(
            snapshot=snapshot,
            run=run,
            context=context,
            registered_context_hash=str(registered["context_hash"]),
            adapter=adapter,
        )

    def _current(self) -> _CurrentRun:
        """Resolve the current frozen run without creating or changing state."""

        snapshot, run, candidate, adapter, _ = self._current_candidate()
        manifest = adapter.manifest()
        try:
            registered = self.repository.get_run(candidate.run_id, require_complete=True)
            persisted_strategies = self.repository.strategies()
        except (KeyError, ValueError) as exc:
            raise PlatformBlocked(
                "current projection has not been synchronized as a verified frozen run"
            ) from exc
        context = self._context_from_repository(registered["context"])
        expected_context = self._repository_context(context, manifest.version)
        if registered["context"] != expected_context:
            raise PlatformBlocked("repository record does not match its reconstructed frozen RunContext")
        if str(registered.get("context_hash")) != canonical_hash(expected_context):
            raise PlatformBlocked("repository context hash does not match the frozen RunContext")
        for field in ("workspace_id", "strategy_id", "portfolio_id", "run_id", "model_version", "as_of"):
            if getattr(context, field) != getattr(candidate, field):
                raise PlatformBlocked(
                    f"current projection does not match frozen RunContext field {field}"
                )
        expected_inputs = {
            "legacy_snapshot": canonical_hash(snapshot),
            "legacy_run": canonical_hash(run),
        }
        for name, digest in expected_inputs.items():
            if context.input_artifacts.get(name) != digest:
                raise PlatformBlocked(
                    f"current projection does not match its frozen RunContext input artifact {name}"
                )
        roles = tuple(REQUIRED_FROZEN_DATASETS)
        if context.dataset_versions:
            if set(context.dataset_versions) != set(roles):
                raise PlatformBlocked(
                    "frozen RunContext must contain exactly the required Stock Select dataset roles"
                )
            manifest_payload = canonical_json(
                {
                    role: context.dataset_versions[role]
                    for role in sorted(context.dataset_versions)
                }
            ).encode("utf-8")
            if context.input_artifacts.get("dataset_manifest") != hashlib.sha256(
                manifest_payload
            ).hexdigest():
                raise PlatformBlocked(
                    "frozen dataset manifest does not match the RunContext references"
                )
            try:
                self.catalog.freeze_versions(
                    context.dataset_versions,
                    required_roles=roles,
                    as_of=context.as_of,
                    acquired_by=context.run_started_at,
                    require_confirmed=True,
                )
            except (OSError, TypeError, ValueError, KeyError) as exc:
                raise PlatformBlocked(
                    f"frozen RunContext dataset verification failed: {exc}"
                ) from exc
        expected_manifest = _plain(manifest)
        matching = [
            row
            for row in persisted_strategies
            if row.get("strategy_id") == manifest.strategy_id
            and row.get("version") == manifest.version
        ]
        if len(matching) != 1 or any(
            matching[0].get(key) != value for key, value in expected_manifest.items()
        ):
            raise PlatformBlocked("current strategy manifest does not match the frozen registry")
        return _CurrentRun(
            snapshot=snapshot,
            run=run,
            context=context,
            registered_context_hash=str(registered["context_hash"]),
            adapter=adapter,
        )

    def _current_or_none(self) -> tuple[_CurrentRun | None, list[str]]:
        try:
            return self._current(), []
        except (PlatformServiceError, ValueError, KeyError) as exc:
            return None, [str(exc)]

    @staticmethod
    def _lineage(bundle: _CurrentRun | None) -> dict[str, Any]:
        if bundle is None:
            return {
                "complete": False,
                "dataset_version_count": 0,
                "reason": "no frozen run context is available",
            }
        legacy_hash = bundle.run.get("data_manifest_hash")
        roles = tuple(sorted(bundle.context.dataset_versions))
        required = tuple(sorted(REQUIRED_FROZEN_DATASETS))
        complete = roles == required
        return {
            "complete": complete,
            "dataset_version_count": len(bundle.context.dataset_versions),
            "required_roles": list(required),
            "roles": list(roles),
            "versions": {
                role: bundle.context.dataset_versions[role].version_id
                for role in roles
            },
            "input_manifest_hash": bundle.context.input_artifacts.get("dataset_manifest"),
            "legacy_data_manifest_hash": legacy_hash,
            "reason": (
                "all required datasets are catalog-verified and content-addressed in the RunContext"
                if complete
                else "legacy run has not yet been migrated to all required content-addressed dataset versions"
            ),
        }

    def overview(self) -> dict[str, Any]:
        bundle, blockers = self._current_or_none()
        current = None
        if bundle is not None:
            run = bundle.run
            current = {
                "run_id": bundle.context.run_id,
                "strategy_id": bundle.context.strategy_id,
                "portfolio_id": bundle.context.portfolio_id,
                "as_of": bundle.context.as_of.isoformat(),
                "context_hash": bundle.registered_context_hash,
                "status": run.get("status"),
                "actionable": run.get("actionable") is True,
                "not_actionable_reasons": list(run.get("not_actionable_reasons") or []),
                "validation_status": NOT_VALIDATED,
            }
            if not current["actionable"]:
                blockers.extend(current["not_actionable_reasons"] or ["current run is NOT ACTIONABLE"])
            if self.require_versioned_lineage and not self._lineage(bundle)["complete"]:
                blockers.append("paper step requires content-addressed dataset versions in RunContext")
        paper_step_ready = bool(
            bundle is not None
            and bundle.run.get("actionable") is True
            and (not self.require_versioned_lineage or self._lineage(bundle)["complete"])
            and self._quote_loader is not None
        )
        paper = self.paper()
        return {
            "schema_version": "platform.v2",
            "status": PAPER_STATUS,
            "validation_status": NOT_VALIDATED,
            "execution_mode": "PAPER",
            "live_trading_supported": False,
            "broker_connections": 0,
            "current_run": current,
            "lineage": self._lineage(bundle),
            "registry": self.repository.summary(),
            "shared_data": self.catalog.summary(),
            "paper": {
                "session_count": paper["count"],
                "active_session_count": paper["active_count"],
                "virtual_broker_only": True,
            },
            "capabilities": {
                "strategy_plugins": True,
                "shared_dataset_catalog": True,
                "immutable_run_context": True,
                "paper_ledger": True,
                "risk_interface": True,
                "attribution_interface": True,
                "paper_step_ready": paper_step_ready,
                "pinned_confirmed_quote_versions_supported": True,
                "paper_step_requires_quote_versions": self._quote_loader is None,
                "live_orders": False,
            },
            "blockers": sorted(set(str(item) for item in blockers if str(item))),
        }

    def strategies(self) -> dict[str, Any]:
        bundle, blockers = self._current_or_none()
        try:
            persisted = self.repository.strategies()
        except (ValueError, KeyError) as exc:
            raise PlatformBlocked("strategy registry failed immutable verification") from exc
        installed = {
            str(item["strategy_id"]): {
                key: value
                for key, value in item.items()
                if key not in {"manifest_hash", "registered_at"}
            }
            for item in persisted
        }
        rows = []
        for strategy_id, name, asset_classes, implementation in self.planned_strategies:
            manifest = installed.get(strategy_id)
            rows.append(
                {
                    "strategy_id": strategy_id,
                    "name": name,
                    "asset_classes": list(asset_classes),
                    "implementation": implementation if manifest is None else "reference_adapter",
                    "installed": manifest is not None,
                    "manifest": manifest,
                    "validation_status": NOT_VALIDATED,
                    "paper_supported": strategy_id == "stock_select" and bundle is not None,
                }
            )
        return {
            "schema_version": "platform.strategies.v2",
            "status": PAPER_STATUS,
            "live_trading_supported": False,
            "items": rows,
            "blockers": blockers,
        }

    def runs(self, strategy_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        _, blockers = self._current_or_none()
        try:
            bounded = max(1, min(int(limit), 1000))
        except (TypeError, ValueError) as exc:
            raise PlatformInputError("limit must be an integer") from exc
        return {
            "schema_version": "platform.runs.v2",
            "status": PAPER_STATUS,
            "items": self.repository.runs(strategy_id=strategy_id, limit=bounded),
            "blockers": blockers,
        }

    def _session_path(self, session_id: str, *, require_exists: bool = True) -> Path:
        if not isinstance(session_id, str) or not _SESSION_RE.fullmatch(session_id):
            raise PlatformInputError("invalid paper session_id")
        path = self.paper_dir / f"{session_id}.jsonl"
        if require_exists and not path.is_file():
            raise PlatformNotFound(f"unknown paper session {session_id!r}")
        return path

    def _broker(self, session_id: str) -> PaperBroker:
        try:
            broker = PaperBroker.replay(self._session_path(session_id))
            if broker.session_id != session_id:
                raise PlatformBlocked("paper ledger session_id does not match its file identity")
            return broker
        except PaperError as exc:
            raise PlatformBlocked(f"paper ledger cannot be replayed: {exc}") from exc

    def _checkpoint_status(
        self, broker: PaperBroker, *, repair: bool = False
    ) -> dict[str, Any]:
        """Verify or advance the optional independent ledger witness.

        A lagging witness may be advanced only when its signed head is an exact
        prefix of the locally verified ledger.  An ahead or divergent witness
        is never overwritten.  This makes a failed post-commit publication
        safely repairable by an idempotent retry without accepting rollback.
        """

        head = broker.events[-1] if broker.events else None
        base = {
            "schema_version": "platform.paper.external-checkpoint-status.v1",
            "configured": self.checkpoint_store is not None,
            "synchronized": False,
            "independent_trust_domain_required": True,
            "reference_store_is_external_only_if_separately_administered": True,
            "store_type": (
                None
                if self.checkpoint_store is None
                else type(self.checkpoint_store).__name__
            ),
            "error_type": None,
            "ledger_sequence": None if head is None else head.sequence,
            "ledger_event_hash": None if head is None else head.event_hash,
        }
        if self.checkpoint_store is None:
            return {
                **base,
                "reason": (
                    "no independent signed ledger checkpoint store is configured; "
                    "the local .head remains in the same ledger trust domain"
                ),
            }
        if head is None:
            return {**base, "reason": "paper ledger has no head to checkpoint"}
        try:
            latest = self.checkpoint_store.latest(broker.session_id)
            if latest is None:
                if not repair:
                    return {**base, "reason": "external ledger checkpoint is missing"}
                if head.sequence != 1:
                    raise LedgerCheckpointError(
                        "external ledger checkpoint is missing for an established ledger; "
                        "refusing to reinitialize a non-genesis head"
                    )
                latest = self.checkpoint_store.publish(
                    session_id=broker.session_id,
                    sequence=head.sequence,
                    event_hash=head.event_hash,
                    timestamp=head.timestamp,
                    expected_previous_checkpoint_hash=GENESIS_CHECKPOINT_HASH,
                )
            elif latest.sequence > head.sequence:
                raise LedgerCheckpointError(
                    "external checkpoint is ahead of the local ledger"
                )
            elif latest.sequence == head.sequence:
                if latest.event_hash != head.event_hash:
                    raise LedgerCheckpointError(
                        "external checkpoint conflicts with the local ledger head"
                    )
            else:
                local_prefix = broker.events[latest.sequence - 1]
                if local_prefix.event_hash != latest.event_hash:
                    raise LedgerCheckpointError(
                        "external checkpoint is not a prefix of the local ledger"
                    )
                if repair:
                    latest = self.checkpoint_store.publish(
                        session_id=broker.session_id,
                        sequence=head.sequence,
                        event_hash=head.event_hash,
                        timestamp=head.timestamp,
                        expected_previous_checkpoint_hash=latest.checkpoint_hash,
                    )
            synchronized = (
                latest.sequence == head.sequence
                and latest.event_hash == head.event_hash
            )
            return {
                **base,
                "synchronized": synchronized,
                "sequence": latest.sequence,
                "event_hash": latest.event_hash,
                "checkpoint_hash": latest.checkpoint_hash,
                "timestamp": latest.timestamp.isoformat(),
                "key_id": latest.key_id,
                "reason": None if synchronized else "external ledger checkpoint is lagging",
            }
        except Exception as exc:
            # The checkpoint is a separately implemented trust-domain adapter.
            # Its SDK may use exception classes unknown to this package.  Never
            # let such an exception escape after the local ledger has committed:
            # callers must receive an explicit pending state, not a false
            # rejection or an ambiguous HTTP 500.  The same status makes the
            # pre-commit gate fail closed in ``_ensure_external_checkpoint``.
            return {
                **base,
                "error_type": type(exc).__name__,
                "reason": (
                    "external ledger checkpoint unavailable or invalid: "
                    f"{type(exc).__name__}: {exc}"
                ),
            }

    def _ensure_external_checkpoint(self, broker: PaperBroker) -> dict[str, Any]:
        status = self._checkpoint_status(broker, repair=True)
        if status["configured"] and not status["synchronized"]:
            raise PlatformBlocked(str(status["reason"]))
        return status

    def _committed_checkpoint_status(
        self, broker: PaperBroker
    ) -> tuple[dict[str, Any], str]:
        """Report a post-commit witness outcome without claiming rollback.

        The local hash-chained ledger is already authoritative when this is
        called.  A witness outage therefore changes the observable mutation
        state, but can never turn the committed operation into a rejection.
        """

        status = self._checkpoint_status(broker, repair=True)
        state = (
            PAPER_MUTATION_CHECKPOINT_PENDING
            if status["configured"] and not status["synchronized"]
            else PAPER_MUTATION_COMMITTED
        )
        return status, state

    def _paper_snapshot(
        self, broker: PaperBroker, *, asof: datetime | None = None
    ) -> dict[str, Any]:
        snapshot = broker.snapshot(asof=asof)
        snapshot["external_checkpoint"] = self._checkpoint_status(broker)
        return snapshot

    def _persisted_operation_result(
        self,
        broker: PaperBroker,
        *,
        operation_id: str,
        action: str,
        request_sha256: str,
    ) -> dict[str, Any] | None:
        """Return one durable receipt with the checkpoint's current state."""

        try:
            prior = broker.operation_result(
                operation_id,
                action=action,
                request_sha256=request_sha256,
            )
        except PaperError as exc:
            raise PlatformBlocked(str(exc)) from exc
        if prior is None:
            return None
        checkpoint, mutation_state = self._committed_checkpoint_status(broker)
        return {
            **prior,
            "mutation_state": mutation_state,
            "external_checkpoint": checkpoint,
        }

    def _recover_concurrent_operation(
        self,
        session_id: str,
        *,
        operation_id: str,
        action: str,
        request_sha256: str,
    ) -> dict[str, Any] | None:
        """Resolve a CAS race when another writer committed this operation."""

        current = self._broker(session_id)
        return self._persisted_operation_result(
            current,
            operation_id=operation_id,
            action=action,
            request_sha256=request_sha256,
        )

    def _paper_session_result(
        self, broker: PaperBroker, *, operation_id: str
    ) -> dict[str, Any]:
        checkpoint, mutation_state = self._committed_checkpoint_status(broker)
        opened_at = broker.events[0].timestamp
        return {
            "schema_version": "platform.paper.session.v2",
            "status": PAPER_STATUS,
            "live_trading_supported": False,
            "operation_id": operation_id,
            "mutation_state": mutation_state,
            "external_checkpoint": checkpoint,
            "session": self._paper_snapshot(broker, asof=opened_at),
        }

    def paper(self, session_id: str | None = None) -> dict[str, Any]:
        if session_id is not None:
            try:
                broker = PaperBroker.inspect(self._session_path(session_id))
                if broker.session_id != session_id:
                    raise PlatformBlocked(
                        "paper ledger session_id does not match its file identity"
                    )
            except PaperError as exc:
                raise PlatformBlocked(f"paper ledger cannot be inspected: {exc}") from exc
            return {
                "schema_version": "platform.paper.v2",
                "status": PAPER_STATUS,
                "live_trading_supported": False,
                "session": self._paper_snapshot(broker),
            }
        sessions, corrupt = [], []
        for path in sorted(self.paper_dir.iterdir()):
            if (
                not _CANONICAL_SESSION_FILE_RE.fullmatch(path.name)
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            try:
                broker = PaperBroker.inspect(path)
                if broker.session_id != path.stem:
                    corrupt.append(
                        {
                            "session_id": path.stem,
                            "error": "paper ledger session_id does not match its canonical file identity",
                        }
                    )
                    continue
                sessions.append(self._paper_snapshot(broker))
            except PaperError as exc:
                corrupt.append({"session_id": path.stem, "error": str(exc)})
        return {
            "schema_version": "platform.paper.v2",
            "status": PAPER_STATUS,
            "validation_status": NOT_VALIDATED,
            "live_trading_supported": False,
            "virtual_broker_only": True,
            "count": len(sessions),
            "active_count": sum(not item["kill_switch_active"] for item in sessions),
            "sessions": sessions,
            "corrupt_ledgers": corrupt,
        }

    def create_paper_session(self, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        body, operation_id, request_sha256 = _paper_operation(
            body or {}, "paper session creation"
        )
        try:
            bundle = self._current()
        except (PlatformServiceError, ValueError, KeyError) as exc:
            raise PlatformBlocked(f"paper session cannot bind a frozen book: {exc}") from exc
        for field in ("allow_short", "allow_margin"):
            if field in body and not isinstance(body[field], bool):
                raise PlatformInputError(f"{field} must be a boolean")
            if body.get(field) is True:
                raise PlatformInputError(
                    f"{field}=true is unavailable until borrow, financing, collateral and "
                    "corporate-action accounting are implemented"
                )
        costs_raw = body.get("costs") or {}
        if not isinstance(costs_raw, Mapping):
            raise PlatformInputError("costs must be an object")
        currency = body.get("currency", "USD")
        currency = currency.strip().upper() if isinstance(currency, str) else ""
        if currency != "USD":
            raise PlatformInputError(
                "paper sessions are USD-only until quote-currency and point-in-time FX accounting are implemented"
            )
        try:
            costs = CostModel(**dict(costs_raw))
            mandate = PaperMandate(
                starting_cash=body.get("starting_cash", "100000"),
                currency=currency,
                allow_short=bool(body.get("allow_short", False)),
                allow_margin=bool(body.get("allow_margin", False)),
                max_gross_leverage=body.get("max_gross_leverage", "1"),
                max_quote_age=timedelta(seconds=float(body.get("max_quote_age_seconds", 300))),
                costs=costs,
            )
            mode = ExecutionMode(str(body.get("mode", "PAPER")).upper())
        except (PaperError, ValueError, TypeError, OverflowError) as exc:
            raise PlatformInputError(str(exc)) from exc
        session_id = "paper_" + hashlib.sha256(operation_id.encode()).hexdigest()[:32]
        path = self._session_path(session_id, require_exists=False)
        if path.exists():
            try:
                existing = PaperBroker.replay(path)
            except PaperError as exc:
                raise PlatformBlocked(f"paper session retry cannot replay its ledger: {exc}") from exc
            if (
                existing.creation_operation_id != operation_id
                or existing.creation_request_sha256 != request_sha256
            ):
                raise PlatformBlocked("Idempotency-Key conflicts with another paper session request")
            return self._paper_session_result(existing, operation_id=operation_id)
        now = self._now()
        try:
            broker = PaperBroker.start_session(
                path,
                mode=mode,
                mandate=mandate,
                binding=PaperSessionBinding(
                    workspace_id=bundle.context.workspace_id,
                    strategy_id=bundle.context.strategy_id,
                    portfolio_id=bundle.context.portfolio_id,
                ),
                session_id=session_id,
                opened_at=now,
                creation_operation_id=operation_id,
                creation_request_sha256=request_sha256,
            )
        except PaperError as exc:
            # Another process may have created the deterministic session after
            # our existence check.  Replaying the same creation request is a
            # success; a different request remains a conflict.
            try:
                existing = PaperBroker.replay(path)
            except PaperError:
                existing = None
            if (
                existing is not None
                and existing.creation_operation_id == operation_id
                and existing.creation_request_sha256 == request_sha256
            ):
                return self._paper_session_result(existing, operation_id=operation_id)
            if (
                existing is not None
                and existing.creation_operation_id == operation_id
                and existing.creation_request_sha256 != request_sha256
            ):
                raise PlatformBlocked(
                    "Idempotency-Key conflicts with another paper session request"
                ) from exc
            raise PlatformInputError(str(exc)) from exc
        return self._paper_session_result(broker, operation_id=operation_id)

    @staticmethod
    def _ticker(intent: PositionIntent) -> str:
        raw = intent.metadata.get("ticker") if intent.metadata else None
        ticker = str(raw or intent.instrument_id.rsplit(":", 1)[-1]).strip().upper()
        if not ticker:
            raise PlatformBlocked(f"position intent {intent.instrument_id!r} has no executable symbol")
        return ticker

    def _execution_quotes(
        self,
        symbols: tuple[str, ...],
        after: datetime,
        available_at: datetime,
        instrument_ids: Mapping[str, str],
        *,
        pinned_versions: Mapping[str, Any] | None = None,
        max_quote_age: timedelta = timedelta(minutes=5),
    ) -> tuple[dict[str, ExecutionQuote], dict[str, Any]]:
        loader: Any = self._quote_loader
        if pinned_versions is not None:
            if not isinstance(pinned_versions, Mapping):
                raise PlatformInputError("quote_versions must be an object keyed by symbol")
            normalized = {
                str(symbol).strip().upper(): str(version_id).strip().lower()
                for symbol, version_id in pinned_versions.items()
            }
            if set(normalized) != set(symbols):
                missing = sorted(set(symbols) - set(normalized))
                extra = sorted(set(normalized) - set(symbols))
                raise PlatformBlocked(
                    "quote_versions must exactly cover the paper book; "
                    f"missing={missing}, extra={extra}"
                )
            bindings = []
            for symbol in symbols:
                try:
                    version, _, _ = self.catalog.get_version(normalized[symbol])
                except (KeyError, ValueError, OSError) as exc:
                    raise PlatformBlocked(
                        f"pinned quote version for {symbol} cannot be verified: {exc}"
                    ) from exc
                bindings.append(
                    QuoteVersionBinding(
                        symbol=symbol,
                        instrument_id=instrument_ids[symbol],
                        dataset_version_id=version.version_id,
                        content_hash=version.content_sha256,
                    )
                )
            try:
                loader = ConfirmedQuoteAdapter(
                    catalog=self.catalog,
                    bindings=bindings,
                    max_quote_age=max_quote_age,
                )
            except ConfirmedQuoteError as exc:
                raise PlatformBlocked(f"pinned confirmed quote adapter failed: {exc}") from exc
        if loader is None:
            raise PlatformBlocked("no confirmed quote adapter is configured; no prices were substituted")
        try:
            load_snapshot = getattr(loader, "load_snapshot", None)
            snapshot = (
                load_snapshot(symbols, after, available_at)
                if callable(load_snapshot)
                else None
            )
            raw = snapshot.quotes if snapshot is not None else loader(symbols, after, available_at)
        except PlatformServiceError:
            raise
        except Exception as exc:
            raise PlatformBlocked(f"confirmed quote adapter failed: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise PlatformBlocked("confirmed quote adapter returned an invalid payload")
        quotes: dict[str, ExecutionQuote] = {}
        for symbol in symbols:
            envelope = raw.get(symbol)
            if not isinstance(envelope, ExecutionQuote):
                raise PlatformBlocked(f"no lineage-bound confirmed quote for {symbol}")
            if envelope.quote.symbol != symbol:
                raise PlatformBlocked(f"quote symbol mismatch for {symbol}")
            expected_instrument = instrument_ids.get(symbol)
            if not expected_instrument or envelope.instrument_id != expected_instrument:
                raise PlatformBlocked(
                    f"quote instrument identity mismatch for {symbol}"
                )
            if envelope.finality not in {Finality.CONFIRMED, Finality.FINAL}:
                raise PlatformBlocked(f"quote for {symbol} is {envelope.finality.value}, not confirmed/final")
            if envelope.observed_at <= after:
                raise PlatformBlocked(f"quote for {symbol} is not after the signal/order boundary")
            if envelope.acquired_at > available_at:
                raise PlatformBlocked(f"quote for {symbol} was not acquired by the paper step time")
            self._verify_execution_quote(envelope, available_at=available_at)
            quotes[symbol] = envelope
        if snapshot is not None:
            snapshot_evidence = snapshot.to_dict()
        else:
            snapshot_body = {
                "schema_version": "platform.confirmed_quote.snapshot.v1",
                "after": after.isoformat(),
                "as_of": available_at.isoformat(),
                "quotes": [
                    {
                        "symbol": symbol,
                        "artifact": quotes[symbol].artifact(),
                        "lineage": quotes[symbol].lineage(),
                    }
                    for symbol in sorted(quotes)
                ],
            }
            snapshot_evidence = {
                **snapshot_body,
                "snapshot_hash": canonical_hash(snapshot_body),
                "adapter": type(loader).__name__,
            }
        return quotes, snapshot_evidence

    def _verify_execution_quote(
        self, envelope: ExecutionQuote, *, available_at: datetime
    ) -> None:
        """Resolve and validate the exact catalog artifact used for a fill/mark."""

        try:
            version, request, payload = self.catalog.get_version(
                envelope.dataset_version_id
            )
        except (KeyError, ValueError, OSError) as exc:
            raise PlatformBlocked(
                f"quote for {envelope.quote.symbol} is not backed by a verified DatasetCatalog version: {exc}"
            ) from exc
        if version.request_key != envelope.request_hash:
            raise PlatformBlocked(
                f"quote request identity mismatch for {envelope.quote.symbol}"
            )
        if version.content_sha256 != envelope.content_hash:
            raise PlatformBlocked(
                f"quote content identity mismatch for {envelope.quote.symbol}"
            )
        if (
            version.schema_version != EXECUTION_QUOTE_SCHEMA_VERSION
            or version.effective_at != envelope.observed_at
            or version.observed_at != envelope.observed_at
            or version.acquired_at != envelope.acquired_at
            or version.finality is not envelope.finality
        ):
            raise PlatformBlocked(
                f"quote catalog clocks/finality/schema mismatch for {envelope.quote.symbol}"
            )
        if version.acquired_at > available_at:
            raise PlatformBlocked(
                f"quote for {envelope.quote.symbol} was not cataloged by the paper step time"
            )
        expected_request = {
            "provider": envelope.source,
            "dataset": EXECUTION_QUOTE_DATASET,
            "instrument_ids": [envelope.instrument_id],
            "fields": list(EXECUTION_QUOTE_FIELDS),
            "frequency": "event",
            "adjustment": "raw",
            "date_range": [
                envelope.observed_at.date().isoformat(),
                envelope.observed_at.date().isoformat(),
            ],
            "vintage_as_of": envelope.acquired_at.isoformat(),
            "schema_version": EXECUTION_QUOTE_SCHEMA_VERSION,
            "parameters": {
                "currency": envelope.currency,
                "symbol": envelope.quote.symbol,
            },
        }
        for field, expected in expected_request.items():
            if request.get(field) != expected:
                raise PlatformBlocked(
                    f"quote catalog request.{field} mismatch for {envelope.quote.symbol}"
                )
        entitlement = request.get("entitlement")
        if not isinstance(entitlement, str) or not entitlement.strip():
            raise PlatformBlocked(
                f"quote catalog request.entitlement is missing for {envelope.quote.symbol}"
            )
        expected_payload = canonical_json(envelope.artifact()).encode("utf-8")
        if payload != expected_payload:
            raise PlatformBlocked(
                f"quote artifact payload mismatch for {envelope.quote.symbol}"
            )

    @staticmethod
    def _plan(
        broker: PaperBroker,
        intents: Sequence[PositionIntent],
        quotes: Mapping[str, ExecutionQuote],
    ) -> tuple[
        Decimal,
        list[tuple[str, str, str, Decimal, str, Side, Decimal]],
        list[dict[str, Any]],
    ]:
        positions = {symbol: position.quantity for symbol, position in broker.positions.items()}
        marked_value = sum(
            (quantity * quotes[symbol].quote.mid for symbol, quantity in positions.items()),
            Decimal("0"),
        )
        equity = broker.cash + marked_value
        if equity <= 0:
            raise PlatformBlocked("paper account equity is nonpositive")
        targets: dict[str, Decimal] = {}
        identities: dict[str, tuple[str, str, str, Decimal]] = {}
        for record in broker.orders.values():
            order = record.order
            identity = (
                order.instrument_id,
                order.asset_class,
                order.currency,
                order.multiplier,
            )
            prior = identities.get(order.symbol)
            if prior is not None and prior != identity:
                raise PlatformBlocked(
                    f"paper symbol {order.symbol} maps to multiple immutable instrument identities"
                )
            identities[order.symbol] = identity
        evidence: list[dict[str, Any]] = []
        for intent in intents:
            if intent.weight is None:
                raise PlatformBlocked("paper adapter currently requires weight-sized position intents")
            if intent.currency != broker.mandate.currency:
                raise PlatformBlocked(
                    "paper accounting has no point-in-time FX conversion; "
                    f"{intent.instrument_id} is {intent.currency} but the session is "
                    f"{broker.mandate.currency}"
                )
            if Decimal(str(intent.multiplier)) != Decimal("1"):
                raise PlatformBlocked(
                    "paper accounting does not yet support contract multipliers; "
                    f"{intent.instrument_id} was blocked before order creation"
                )
            ticker = PlatformService._ticker(intent)
            asset_class = intent.instrument_id.partition(":")[0].lower()
            if asset_class != "equity":
                raise PlatformBlocked(
                    "paper execution supports USD cash equities only; futures/options require expiry, "
                    "contract identity, settlement and variation-margin accounting"
                )
            identity = (
                intent.instrument_id,
                asset_class,
                intent.currency,
                Decimal(str(intent.multiplier)),
            )
            prior = identities.get(ticker)
            if prior is not None and prior != identity:
                raise PlatformBlocked(
                    f"paper symbol {ticker} maps to multiple immutable instrument identities"
                )
            identities[ticker] = identity
            if quotes[ticker].currency != broker.mandate.currency:
                raise PlatformBlocked(
                    "paper accounting has no point-in-time FX conversion; "
                    f"quote {ticker} is {quotes[ticker].currency} but the session is "
                    f"{broker.mandate.currency}"
                )
            target = (equity * Decimal(str(intent.weight)) / quotes[ticker].quote.mid).quantize(
                _UNIT_QUANTUM, rounding=ROUND_DOWN
            )
            targets[ticker] = targets.get(ticker, Decimal("0")) + target
            evidence.append(
                {
                    "instrument_id": intent.instrument_id,
                    "portfolio_id": intent.portfolio_id,
                    "sleeve_id": intent.sleeve_id,
                    "pair_id": intent.pair_id,
                    "leg_id": intent.leg_id,
                    "ticker": ticker,
                    "target_weight": str(intent.weight),
                    "multiplier": str(intent.multiplier),
                    "currency": intent.currency,
                    "sizing_equity": str(equity),
                    "target_quantity": str(target),
                    "quote": quotes[ticker].quote.to_dict(),
                    "quote_lineage": quotes[ticker].lineage(),
                }
            )
        effective = dict(positions)
        for record in broker.orders.values():
            if record.remaining_quantity <= 0 or record.status.value not in {
                "ACCEPTED", "PARTIALLY_FILLED"
            }:
                continue
            effective[record.order.symbol] = effective.get(
                record.order.symbol, Decimal("0")
            ) + record.order.side.sign * record.remaining_quantity
        plan: list[tuple[str, str, str, Decimal, str, Side, Decimal]] = []
        for symbol in sorted(set(effective) | set(targets)):
            delta = targets.get(symbol, Decimal("0")) - effective.get(symbol, Decimal("0"))
            if abs(delta) < _UNIT_QUANTUM:
                continue
            try:
                instrument_id, asset_class, currency, multiplier = identities[symbol]
            except KeyError as exc:
                raise PlatformBlocked(
                    f"paper position {symbol} has no immutable cash-equity instrument identity"
                ) from exc
            plan.append(
                (
                    instrument_id,
                    asset_class,
                    currency,
                    multiplier,
                    symbol,
                    Side.BUY if delta > 0 else Side.SELL,
                    abs(delta),
                )
            )
        return equity, plan, evidence

    @staticmethod
    def _execute_paper_cycle(
        broker: PaperBroker,
        intents: Sequence[PositionIntent],
        quotes: Mapping[str, ExecutionQuote],
        *,
        strategy_id: str,
        run_id: str,
        context_hash: str,
        signal_time: datetime,
        asof: datetime,
    ) -> tuple[
        Decimal,
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        """Advance only orders that existed before ``asof``, then submit new ones.

        A quote acquired before the operator's current step may size a new order,
        but can never fill that order.  The order remains open until a later step
        supplies a confirmed quote observed after its recorded order time.
        """

        pre_positions = {
            symbol: position.quantity
            for symbol, position in broker.positions.items()
            if position.quantity != 0
        }
        equity_before = broker.cash + sum(
            (quantity * quotes[symbol].quote.mid for symbol, quantity in pre_positions.items()),
            Decimal("0"),
        )
        open_records = [
            record
            for record in broker.orders.values()
            if record.remaining_quantity > 0
            and record.status.value in {"ACCEPTED", "PARTIALLY_FILLED"}
        ]
        fills: list[dict[str, Any]] = []
        for record in sorted(
            open_records,
            key=lambda item: (
                quotes[item.order.symbol].acquired_at,
                0 if item.order.side is Side.SELL else 1,
                item.order.order_time,
                item.order.order_id,
            ),
        ):
            envelope = quotes[record.order.symbol]
            try:
                fill = broker.fill_order(
                    record.order.order_id,
                    envelope.quote,
                    fill_time=envelope.acquired_at,
                    quote_lineage=QuoteLineage.from_dict(envelope.lineage()),
                )
            except QuoteRejected as exc:
                if "no available quantity" in str(exc):
                    continue
                raise
            fills.append(fill.to_dict())

        if any(position.quantity != 0 for position in broker.positions.values()):
            broker.mark_to_market(
                {symbol: envelope.quote for symbol, envelope in quotes.items()},
                asof=asof,
                quote_lineage={
                    symbol: QuoteLineage.from_dict(envelope.lineage())
                    for symbol, envelope in quotes.items()
                },
            )

        _, plan, sizing_evidence = PlatformService._plan(broker, intents, quotes)
        orders = []
        for instrument_id, asset_class, currency, multiplier, symbol, side, quantity in plan:
            order = broker.submit_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                signal_time=signal_time,
                order_time=asof,
                strategy_id=strategy_id,
                run_id=run_id,
                context_hash=context_hash,
                instrument_id=instrument_id,
                asset_class=asset_class,
                currency=currency,
                multiplier=multiplier,
            )
            orders.append(order.to_dict())
        return equity_before, orders, fills, sizing_evidence, broker.snapshot(asof=asof)

    @staticmethod
    def _commit_staged_paper(original: PaperBroker, staged: PaperBroker) -> PaperBroker:
        """CAS-commit a complete local generation under an interprocess lock."""

        expected = original.events[-1]
        original.store.replace_from(
            staged.store,
            expected_sequence=expected.sequence,
            expected_hash=expected.event_hash,
        )
        return PaperBroker.replay(original.store.path)

    @staticmethod
    def _cleanup_staged_paper(path: Path) -> None:
        """Remove one private generation and its store sidecars."""

        for suffix in (
            "",
            ".head",
            ".lock",
            ".txn",
            ".txn.old",
            ".head.txn.old",
            ".txn.new",
            ".head.txn.new",
            ".append",
            ".head.append",
            ".restore",
            ".head.restore",
        ):
            path.with_name(path.name + suffix).unlink(missing_ok=True)

    def step_paper(self, session_id: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        body, operation_id, request_sha256 = _paper_operation(body or {}, "paper step")
        with self._lock:
            broker = self._broker(session_id)
            prior = self._persisted_operation_result(
                broker,
                operation_id=operation_id,
                action="paper.step",
                request_sha256=request_sha256,
            )
            if prior is not None:
                return prior
            self._ensure_external_checkpoint(broker)
            try:
                bundle = self._current()
            except (PlatformServiceError, ValueError, KeyError) as exc:
                raise PlatformBlocked(f"paper step blocked: frozen run is unavailable: {exc}") from exc
            if body.get("run_id") and str(body["run_id"]) != bundle.context.run_id:
                raise PlatformBlocked("requested run_id is not the frozen current run")
            expected_binding = PaperSessionBinding(
                workspace_id=bundle.context.workspace_id,
                strategy_id=bundle.context.strategy_id,
                portfolio_id=bundle.context.portfolio_id,
            )
            if broker.binding != expected_binding:
                raise PlatformBlocked(
                    "paper session is not bound to the frozen strategy/portfolio; start a new session"
                )
            reasons = [str(item) for item in bundle.run.get("not_actionable_reasons") or []]
            if bundle.run.get("actionable") is not True:
                detail = "; ".join(reasons) or "current run is NOT ACTIONABLE"
                raise PlatformBlocked(f"paper step blocked: {detail}")
            if self.require_versioned_lineage and not bundle.context.dataset_versions:
                raise PlatformBlocked(
                    "paper step blocked: RunContext has no content-addressed dataset versions"
                )
            status = str(bundle.run.get("validation_status") or NOT_VALIDATED)
            if not status.startswith(NOT_VALIDATED):
                raise PlatformBlocked("paper adapter received an unsupported validation claim")
            now = self._now()
            signal_time = bundle.context.as_of
            if bundle.run.get("rebalance_policy") is not None:
                from ai_semi_intel.trading_policy import execution_gate
                from ai_semi_intel.utils.io import DataUnavailable
                try:
                    execution_gate(bundle.run, now)
                except (DataUnavailable, KeyError, ValueError) as exc:
                    raise PlatformBlocked(f"daily rebalance blocked: {exc}") from exc
            last_event = broker.events[-1].timestamp
            if signal_time >= now:
                raise PlatformBlocked("paper step time must be strictly after the frozen signal time")
            if last_event > now:
                raise PlatformBlocked("paper ledger head is later than the current step time")
            intents = bundle.adapter.construct_targets(bundle.context)
            target_symbols = {self._ticker(intent) for intent in intents}
            position_symbols = {
                symbol for symbol, position in broker.positions.items() if position.quantity != 0
            }
            open_records = [
                record
                for record in broker.orders.values()
                if record.remaining_quantity > 0
                and record.status.value in {"ACCEPTED", "PARTIALLY_FILLED"}
            ]
            foreign_orders = [
                record.order.order_id
                for record in open_records
                if record.order.run_id != bundle.context.run_id
                or record.order.context_hash != bundle.registered_context_hash
            ]
            if foreign_orders:
                raise PlatformBlocked(
                    "open paper orders belong to a different frozen run/context; start a new session"
                )
            open_symbols = {record.order.symbol for record in open_records}
            symbols = tuple(sorted(target_symbols | position_symbols | open_symbols))
            instrument_ids: dict[str, str] = {}
            for record in broker.orders.values():
                prior = instrument_ids.get(record.order.symbol)
                if prior is not None and prior != record.order.instrument_id:
                    raise PlatformBlocked(
                        f"paper symbol {record.order.symbol} maps to multiple instrument identities"
                    )
                instrument_ids[record.order.symbol] = record.order.instrument_id
            for intent in intents:
                symbol = self._ticker(intent)
                prior = instrument_ids.get(symbol)
                if prior is not None and prior != intent.instrument_id:
                    raise PlatformBlocked(
                        f"paper symbol {symbol} maps to multiple instrument identities"
                    )
                instrument_ids[symbol] = intent.instrument_id
            if not set(symbols).issubset(instrument_ids):
                missing = sorted(set(symbols) - set(instrument_ids))
                raise PlatformBlocked(
                    "paper positions lack immutable instrument identity: " + ", ".join(missing)
                )
            quote_boundary = (
                max(record.order.order_time for record in open_records)
                if open_records
                else signal_time
            )
            if bundle.run.get("rebalance_policy") is not None:
                quote_boundary = max(quote_boundary, _parse_datetime(
                    bundle.run["rebalance_policy"]["not_before"], "daily execution open"))
            quotes, quote_snapshot = self._execution_quotes(
                symbols,
                quote_boundary,
                now,
                instrument_ids,
                pinned_versions=body.get("quote_versions"),
                max_quote_age=broker.mandate.max_quote_age,
            )
            for symbol, envelope in quotes.items():
                if now - envelope.quote.timestamp > broker.mandate.max_quote_age:
                    raise PlatformBlocked(f"quote for {symbol} is stale under the paper mandate")
            for record in open_records:
                quote_time = quotes[record.order.symbol].quote.timestamp
                if quote_time.date() != record.order.order_time.date():
                    raise PlatformBlocked(
                        "paper execution is intraday-only until trading-calendar and "
                        "corporate-action feeds are connected; an order crossed a UTC session date"
                    )
            for symbol in position_symbols:
                mark_time = broker.mark_times.get(symbol)
                if mark_time is None or quotes[symbol].quote.timestamp.date() != mark_time.date():
                    raise PlatformBlocked(
                        "paper execution is intraday-only until dividend, split, symbol-change, "
                        "financing and trading-calendar accounting are connected"
                    )

            # Execute the whole operator step on a private ledger generation.
            # The local generation is committed atomically.  The independent
            # witness is advanced afterwards and may be explicitly pending.
            with tempfile.NamedTemporaryFile(
                prefix="paper-stage-", suffix=".ledger", dir=self.paper_stage_dir, delete=False
            ) as tmp:
                staged_path = Path(tmp.name)
            try:
                shutil.copyfile(broker.store.path, staged_path)
                staged_head = staged_path.with_name(staged_path.name + ".head")
                shutil.copyfile(broker.store.head_path, staged_head)
                staged = PaperBroker.replay(staged_path)
                equity_before, orders, fills, sizing_evidence, snapshot = self._execute_paper_cycle(
                    staged,
                    intents,
                    quotes,
                    strategy_id=bundle.context.strategy_id,
                    run_id=bundle.context.run_id,
                    context_hash=bundle.registered_context_hash,
                    signal_time=signal_time,
                    asof=now,
                )
                result = {
                    "schema_version": "platform.paper.step.v2",
                    "status": PAPER_STATUS,
                    "validation_status": NOT_VALIDATED,
                    "live_trading_supported": False,
                    "operation_id": operation_id,
                    "mutation_state": PAPER_MUTATION_COMMITTED,
                    "run_id": bundle.context.run_id,
                    "context_hash": bundle.registered_context_hash,
                    "signal_time": signal_time.isoformat(),
                    "order_time": now.isoformat(),
                    "asof": now.isoformat(),
                    "equity_before": str(equity_before),
                    "target_count": len(intents),
                    "orders": orders,
                    "fills": fills,
                    "sizing_evidence": sizing_evidence,
                    "phase": (
                        "fills_processed_and_orders_submitted"
                        if fills and orders
                        else "fills_processed"
                        if fills
                        else "orders_submitted"
                        if orders
                        else "no_change"
                    ),
                    "retroactive_fills_allowed": False,
                    "quote_snapshot": quote_snapshot,
                    "quote_snapshot_hash": quote_snapshot["snapshot_hash"],
                    "quote_lineage": [quotes[symbol].lineage() for symbol in sorted(quotes)],
                    "session": snapshot,
                }
                staged.record_operation(
                    operation_id,
                    action="paper.step",
                    request_sha256=request_sha256,
                    result=result,
                    recorded_at=now,
                )
                committed = self._commit_staged_paper(broker, staged)
                persisted_result = committed.operation_result(
                    operation_id,
                    action="paper.step",
                    request_sha256=request_sha256,
                )
                if persisted_result is None:
                    raise PlatformBlocked("paper step committed without its operation receipt")
                checkpoint, mutation_state = self._committed_checkpoint_status(committed)
                result = {
                    **persisted_result,
                    "mutation_state": mutation_state,
                    "external_checkpoint": checkpoint,
                }
            except PaperError as exc:
                recovered = self._recover_concurrent_operation(
                    session_id,
                    operation_id=operation_id,
                    action="paper.step",
                    request_sha256=request_sha256,
                )
                if recovered is not None:
                    result = recovered
                else:
                    raise PlatformBlocked(f"paper batch rejected before commit: {exc}") from exc
            except OSError as exc:
                recovered = self._recover_concurrent_operation(
                    session_id,
                    operation_id=operation_id,
                    action="paper.step",
                    request_sha256=request_sha256,
                )
                if recovered is not None:
                    result = recovered
                else:
                    raise PlatformBlocked(f"paper ledger batch could not be committed: {exc}") from exc
            finally:
                self._cleanup_staged_paper(staged_path)
            return result

    def kill_switch(self, session_id: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        body, operation_id, request_sha256 = _paper_operation(
            body or {}, "paper kill switch"
        )
        reason = str(body.get("reason") or "").strip()
        if not reason:
            raise PlatformInputError("kill-switch reason is required")
        with self._lock:
            broker = self._broker(session_id)
            prior = self._persisted_operation_result(
                broker,
                operation_id=operation_id,
                action="paper.kill_switch",
                request_sha256=request_sha256,
            )
            if prior is not None:
                return prior
            self._ensure_external_checkpoint(broker)
            now = max(self._now(), broker.events[-1].timestamp)
            with tempfile.NamedTemporaryFile(
                prefix="paper-kill-stage-", suffix=".ledger", dir=self.paper_stage_dir, delete=False
            ) as tmp:
                staged_path = Path(tmp.name)
            try:
                shutil.copyfile(broker.store.path, staged_path)
                staged_head = staged_path.with_name(staged_path.name + ".head")
                shutil.copyfile(broker.store.head_path, staged_head)
                staged = PaperBroker.replay(staged_path)
                staged.activate_kill_switch(reason, activated_at=now)
                result = {
                    "schema_version": "platform.paper.kill-switch.v2",
                    "status": PAPER_STATUS,
                    "live_trading_supported": False,
                    "operation_id": operation_id,
                    "mutation_state": PAPER_MUTATION_COMMITTED,
                    "session": staged.snapshot(asof=now),
                }
                staged.record_operation(
                    operation_id,
                    action="paper.kill_switch",
                    request_sha256=request_sha256,
                    result=result,
                    recorded_at=now,
                )
                committed = self._commit_staged_paper(broker, staged)
                persisted_result = committed.operation_result(
                    operation_id,
                    action="paper.kill_switch",
                    request_sha256=request_sha256,
                )
                if persisted_result is None:
                    raise PlatformBlocked("paper kill switch committed without its operation receipt")
                checkpoint, mutation_state = self._committed_checkpoint_status(committed)
                result = {
                    **persisted_result,
                    "mutation_state": mutation_state,
                    "external_checkpoint": checkpoint,
                }
            except PaperError as exc:
                recovered = self._recover_concurrent_operation(
                    session_id,
                    operation_id=operation_id,
                    action="paper.kill_switch",
                    request_sha256=request_sha256,
                )
                if recovered is not None:
                    result = recovered
                else:
                    raise PlatformBlocked(str(exc)) from exc
            except OSError as exc:
                recovered = self._recover_concurrent_operation(
                    session_id,
                    operation_id=operation_id,
                    action="paper.kill_switch",
                    request_sha256=request_sha256,
                )
                if recovered is not None:
                    result = recovered
                else:
                    raise PlatformBlocked(f"paper kill switch could not be committed: {exc}") from exc
            finally:
                self._cleanup_staged_paper(staged_path)
            return result

    @staticmethod
    def _primary_construction(bundle: _CurrentRun) -> str:
        portfolio = bundle.run.get("portfolio")
        name = portfolio.get("construction") if isinstance(portfolio, Mapping) else None
        if not isinstance(name, str) or not name.strip():
            raise PlatformBlocked("frozen run does not identify its primary construction")
        return name.strip()

    @staticmethod
    def _risk_binding(bundle: _CurrentRun) -> dict[str, Any]:
        manifest = bundle.adapter.manifest()
        return {
            "schema_version": RISK_BINDING_SCHEMA_VERSION,
            "run_id": bundle.context.run_id,
            "context_hash": bundle.registered_context_hash,
            "as_of": bundle.context.as_of.isoformat(),
            "run_started_at": bundle.context.run_started_at.isoformat(),
            "strategy_id": bundle.context.strategy_id,
            "strategy_version": manifest.version,
            "portfolio_id": bundle.context.portfolio_id,
        }

    def _risk_request(self, bundle: _CurrentRun) -> dict[str, Any]:
        frozen_lineage = None
        if set(bundle.context.dataset_versions) == set(REQUIRED_FROZEN_DATASETS):
            frozen_lineage = self.catalog.freeze_versions(
                bundle.context.dataset_versions,
                required_roles=REQUIRED_FROZEN_DATASETS,
                as_of=bundle.context.as_of,
                acquired_by=bundle.context.run_started_at,
                require_confirmed=True,
            ).lineage()
        return {
            "schema_version": RISK_REQUEST_SCHEMA_VERSION,
            "binding": self._risk_binding(bundle),
            "required_metrics": _plain(_RISK_METRIC_SPECS),
            "run": _plain(bundle.run),
            "dataset_context_hash": bundle.registered_context_hash,
            "dataset_versions": _plain(bundle.context.dataset_versions),
            "input_manifest_hash": bundle.context.input_artifacts.get("dataset_manifest"),
            "frozen_dataset_lineage": frozen_lineage,
        }

    def _validated_risk_payload(
        self, raw: Any, bundle: _CurrentRun
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping) or not raw:
            raise PlatformBlocked("risk adapter contract: response must be a non-empty object")
        try:
            payload = _plain(raw)
        except Exception as exc:
            raise PlatformBlocked("risk adapter contract: response must be canonical JSON") from exc
        if payload.get("schema_version") != RISK_PAYLOAD_SCHEMA_VERSION:
            raise PlatformBlocked(
                f"risk adapter contract: schema_version must equal {RISK_PAYLOAD_SCHEMA_VERSION!r}"
            )
        if not isinstance(payload.get("available"), bool):
            raise PlatformBlocked("risk adapter contract: available must be a boolean")
        binding = payload.get("binding")
        if not isinstance(binding, Mapping) or not binding:
            raise PlatformBlocked("risk adapter contract: versioned binding is required")
        expected = self._risk_binding(bundle)
        if binding.get("schema_version") != RISK_BINDING_SCHEMA_VERSION:
            raise PlatformBlocked(
                f"risk adapter contract: binding.schema_version must equal "
                f"{RISK_BINDING_SCHEMA_VERSION!r}"
            )
        for field in (
            "run_id",
            "context_hash",
            "strategy_id",
            "strategy_version",
            "portfolio_id",
        ):
            if binding.get(field) != expected[field]:
                raise PlatformBlocked(
                    f"risk adapter contract: binding.{field} is missing or does not match the frozen run"
                )
            if field in payload and payload[field] != binding[field]:
                raise PlatformBlocked(
                    f"risk adapter contract: top-level {field} conflicts with the versioned binding"
                )
        if "as_of" not in binding:
            raise PlatformBlocked("risk adapter contract: binding.as_of is required")
        try:
            bound_as_of = _parse_datetime(binding["as_of"], "risk binding as_of")
        except PlatformInputError as exc:
            raise PlatformBlocked(f"risk adapter contract: {exc}") from exc
        if bound_as_of != bundle.context.as_of:
            raise PlatformBlocked(
                "risk adapter contract: binding.as_of does not match the frozen run"
            )
        if "run_started_at" not in binding:
            raise PlatformBlocked("risk adapter contract: binding.run_started_at is required")
        try:
            bound_started = _parse_datetime(
                binding["run_started_at"], "risk binding run_started_at"
            )
        except PlatformInputError as exc:
            raise PlatformBlocked(f"risk adapter contract: {exc}") from exc
        if bound_started != bundle.context.run_started_at:
            raise PlatformBlocked(
                "risk adapter contract: binding.run_started_at does not match the frozen run"
            )
        if "as_of" in payload:
            try:
                top_as_of = _parse_datetime(payload["as_of"], "risk payload as_of")
            except PlatformInputError as exc:
                raise PlatformBlocked(f"risk adapter contract: {exc}") from exc
            if top_as_of != bound_as_of:
                raise PlatformBlocked(
                    "risk adapter contract: top-level as_of conflicts with the versioned binding"
                )

        construction = payload.get("construction")
        if not isinstance(construction, Mapping):
            raise PlatformBlocked("risk adapter contract: construction scope is required")
        expected_construction = self._primary_construction(bundle)
        expected_scope = {
            "name": expected_construction,
            "layer": "actual_run",
            "window": "3y",
            "window_sessions": 756,
        }
        for field, expected_value in expected_scope.items():
            if construction.get(field) != expected_value:
                raise PlatformBlocked(
                    f"risk adapter contract: construction.{field} must equal {expected_value!r}"
                )
        source_construction = construction.get("source_construction")
        if not isinstance(source_construction, str) or not source_construction.strip():
            raise PlatformBlocked(
                "risk adapter contract: construction.source_construction is required"
            )
        basis = construction.get("basis")
        if not isinstance(basis, Mapping):
            raise PlatformBlocked(
                "risk adapter contract: construction.basis must disclose algorithm and landed-weight identities"
            )
        if basis.get("algorithm_identity") != "run.portfolio.construction":
            raise PlatformBlocked(
                "risk adapter contract: construction.basis.algorithm_identity must equal "
                "'run.portfolio.construction'"
            )
        landed_source = basis.get("landed_weights_source")
        if not isinstance(landed_source, str) or not landed_source.strip():
            raise PlatformBlocked(
                "risk adapter contract: construction.basis.landed_weights_source is required"
            )

        lineage = payload.get("lineage")
        if not isinstance(lineage, Mapping):
            raise PlatformBlocked("risk adapter contract: calculation lineage is required")
        for field, expected_value in (
            ("run_id", expected["run_id"]),
            ("context_hash", expected["context_hash"]),
        ):
            if lineage.get(field) != expected_value:
                raise PlatformBlocked(
                    f"risk adapter contract: lineage.{field} does not match the frozen run"
                )
        for field in (
            "data_snapshot_hash",
            "portfolio_weights_hash",
            "calculator_hash",
            "calculation_sample_hash",
        ):
            digest = lineage.get(field)
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest.lower()):
                raise PlatformBlocked(
                    f"risk adapter contract: lineage.{field} must be a full SHA-256"
                )
        weight_identity = construction.get("weight_identity")
        if (
            not isinstance(weight_identity, str)
            or not _SHA256_RE.fullmatch(weight_identity.lower())
            or weight_identity.lower() != str(lineage["portfolio_weights_hash"]).lower()
        ):
            raise PlatformBlocked(
                "risk adapter contract: construction.weight_identity must equal the actual-run "
                "lineage.portfolio_weights_hash"
            )
        acquired_at = lineage.get("acquired_at")
        try:
            acquired = _parse_datetime(acquired_at, "risk lineage acquired_at")
        except PlatformInputError as exc:
            raise PlatformBlocked(f"risk adapter contract: {exc}") from exc
        if acquired > bundle.context.run_started_at:
            raise PlatformBlocked(
                "risk adapter contract: lineage.acquired_at is after the frozen run start"
            )
        for field in ("asof", "finality"):
            value = lineage.get(field)
            if not isinstance(value, str) or not value.strip():
                raise PlatformBlocked(
                    f"risk adapter contract: lineage.{field} is required"
                )

        if payload["available"] is False:
            unavailable = payload.get("unavailable")
            if not isinstance(unavailable, Mapping):
                raise PlatformBlocked(
                    "risk adapter contract: unavailable detail is required when available=false"
                )
            for field in ("code", "reason"):
                value = unavailable.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise PlatformBlocked(
                        f"risk adapter contract: unavailable.{field} is required"
                    )
            if unavailable.get("window") != "3y" or unavailable.get("window_sessions") != 756:
                raise PlatformBlocked(
                    "risk adapter contract: unavailable window must be the requested 3y/756 sessions"
                )
            limiting = unavailable.get("limiting_holdings")
            if not isinstance(limiting, list):
                raise PlatformBlocked(
                    "risk adapter contract: unavailable.limiting_holdings must be a list"
                )
            metrics = payload.get("metrics", {})
            if not isinstance(metrics, Mapping) or metrics:
                raise PlatformBlocked(
                    "risk adapter contract: unavailable payload cannot publish metric values"
                )
            return payload

        if lineage.get("complete") is not True:
            raise PlatformBlocked(
                "risk adapter contract: available=true requires lineage.complete=true"
            )
        if lineage.get("frozen_inputs_consumed") is not True:
            raise PlatformBlocked(
                "risk adapter contract: available=true requires verified frozen inputs to be consumed"
            )
        if set(bundle.context.dataset_versions) != set(REQUIRED_FROZEN_DATASETS):
            raise PlatformBlocked(
                "risk adapter contract: frozen run lacks one or more required risk dataset roles"
            )
        expected_manifest_hash = canonical_hash(
            {
                role: bundle.context.dataset_versions[role]
                for role in sorted(bundle.context.dataset_versions)
            }
        )
        if bundle.context.input_artifacts.get("dataset_manifest") != expected_manifest_hash:
            raise PlatformBlocked(
                "risk adapter contract: RunContext dataset manifest hash is inconsistent"
            )
        if lineage.get("input_manifest_hash") != expected_manifest_hash:
            raise PlatformBlocked(
                "risk adapter contract: lineage.input_manifest_hash does not match the frozen run"
            )
        try:
            verified_inputs = self.catalog.freeze_versions(
                bundle.context.dataset_versions,
                required_roles=REQUIRED_FROZEN_DATASETS,
                as_of=bundle.context.as_of,
                acquired_by=bundle.context.run_started_at,
                require_confirmed=True,
            )
            validate_frozen_stock_select_provenance(
                verified_inputs,
                expected_run_id=bundle.context.run_id,
                expected_as_of=bundle.context.as_of,
            )
            expected_benchmark = validate_frozen_benchmark_contract(
                verified_inputs,
                expected_portfolio_id=bundle.context.portfolio_id,
            )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            raise PlatformBlocked(
                f"risk adapter contract: frozen dataset verification failed: {exc}"
            ) from exc
        expected_frozen_lineage = verified_inputs.lineage()
        if lineage.get("frozen_datasets") != expected_frozen_lineage:
            raise PlatformBlocked(
                "risk adapter contract: frozen dataset lineage does not match the catalog"
            )
        for field in ("frozen_input_binding_hash", "frozen_run_reconciliation"):
            if lineage.get(field) != verified_inputs.binding_hash:
                raise PlatformBlocked(
                    f"risk adapter contract: lineage.{field} does not match the verified input binding"
                )

        metrics = payload.get("metrics")
        if not isinstance(metrics, Mapping) or not metrics:
            raise PlatformBlocked("risk adapter contract: metrics must be a non-empty object")
        for name, spec in _RISK_METRIC_SPECS.items():
            if name not in metrics:
                raise PlatformBlocked(
                    f"risk adapter contract: required metric {name!r} is missing"
                )
            _validate_risk_metric(name, metrics[name], spec)
        sample_hash = str(lineage["calculation_sample_hash"]).lower()
        sample_identities: set[tuple[str, str, str]] = set()
        for name in _RISK_METRIC_SPECS:
            metric = metrics[name]
            start = metric.get("start")
            end = metric.get("end")
            metric_hash = metric.get("calculation_sample_hash")
            if not isinstance(start, str) or not isinstance(end, str):
                raise PlatformBlocked(
                    f"risk adapter contract: metrics.{name} requires exact sample start/end"
                )
            try:
                start_date = datetime.fromisoformat(start).date()
                end_date = datetime.fromisoformat(end).date()
            except ValueError as exc:
                raise PlatformBlocked(
                    f"risk adapter contract: metrics.{name} sample start/end must be ISO dates"
                ) from exc
            if start_date > end_date:
                raise PlatformBlocked(
                    f"risk adapter contract: metrics.{name} sample start is after end"
                )
            if metric_hash != sample_hash:
                raise PlatformBlocked(
                    f"risk adapter contract: metrics.{name}.calculation_sample_hash does not match lineage"
                )
            sample_identities.add((start, end, metric_hash))
        if len(sample_identities) != 1:
            raise PlatformBlocked(
                "risk adapter contract: required metrics do not share one exact calculation sample"
            )
        _start, end, _digest = next(iter(sample_identities))
        if end != lineage.get("asof"):
            raise PlatformBlocked(
                "risk adapter contract: metric sample end does not match lineage.asof"
            )
        tracking_error = metrics["tracking_error"]
        if (
            expected_benchmark["benchmark_kind"] == "research proxy"
            and tracking_error.get("decision_usable") is not False
        ):
            raise PlatformBlocked(
                "risk adapter contract: research proxy tracking error is never decision usable"
            )
        for field in (
            "benchmark_kind",
            "benchmark_source",
            "decision_usable",
            "benchmark_dataset_id",
            "benchmark_version_id",
            "benchmark_content_sha256",
        ):
            if tracking_error.get(field) != expected_benchmark[field]:
                raise PlatformBlocked(
                    "risk adapter contract: metrics.tracking_error."
                    f"{field} does not match the frozen benchmark dataset"
                )
        return payload

    def risk(self) -> dict[str, Any]:
        bundle, blockers = self._current_or_none()
        attribution = None
        payload = None
        adapter_reason = "no versioned platform risk adapter is configured"
        if bundle is not None:
            attribution = _plain(bundle.adapter.export_attribution(bundle.context))
            if self._risk_loader is not None:
                try:
                    raw = self._risk_loader(self._risk_request(bundle))
                    payload = self._validated_risk_payload(raw, bundle)
                    if payload.get("available") is True:
                        adapter_reason = None
                    else:
                        adapter_reason = str((payload.get("unavailable") or {}).get("reason"))
                        blockers.append(adapter_reason)
                except Exception as exc:
                    adapter_reason = f"risk adapter unavailable: {exc}"
                    blockers.append(adapter_reason)
        else:
            adapter_reason = "no frozen run context is available"
        return {
            "schema_version": "platform.risk.v2",
            "status": PAPER_STATUS,
            "validation_status": NOT_VALIDATED,
            "live_trading_supported": False,
            "run_id": None if bundle is None else bundle.context.run_id,
            "risk": {
                "available": bool(payload is not None and payload.get("available") is True),
                "payload": payload,
                "construction_endpoint": "/api/research/risk/constructions",
                "required_metrics": [
                    "VaR 99% 1-day",
                    "VaR 99% 10-day",
                    "Expected Shortfall 97.5%",
                    "Tracking Error",
                ],
                "adapter_contract": {
                    "request_schema_version": RISK_REQUEST_SCHEMA_VERSION,
                    "payload_schema_version": RISK_PAYLOAD_SCHEMA_VERSION,
                    "binding_schema_version": RISK_BINDING_SCHEMA_VERSION,
                    "required_metric_keys": list(_RISK_METRIC_SPECS),
                },
                "reason": adapter_reason,
            },
            "attribution": attribution,
            "blockers": blockers,
        }


__all__ = [
    "ExecutionQuote",
    "PAPER_STATUS",
    "PAPER_MUTATION_CHECKPOINT_PENDING",
    "PAPER_MUTATION_COMMITTED",
    "RISK_BINDING_SCHEMA_VERSION",
    "RISK_PAYLOAD_SCHEMA_VERSION",
    "RISK_REQUEST_SCHEMA_VERSION",
    "PlatformBlocked",
    "PlatformInputError",
    "PlatformNotFound",
    "PlatformService",
    "PlatformServiceError",
]
