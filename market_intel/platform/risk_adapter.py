"""Versioned Stock Select risk adapter for the platform boundary.

The legacy risk workbench still supplies diagnostics, but its mutable reads can
never publish an available platform result.  Availability requires the frozen
builder path: every required input is resolved from an exact catalog version,
verified byte-for-byte, tied to the requested RunContext hash, and passed as one
immutable binding.  The adapter never shortens the window or substitutes
another construction, layer, horizon, or benchmark.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from .core import DatasetVersionRef, canonical_hash
from .data import DatasetCatalog, FrozenDatasetBinding
from .stock_select_data import (
    validate_frozen_benchmark_contract,
    validate_frozen_stock_select_provenance,
)


REQUEST_SCHEMA_VERSION = "platform.risk.request.v1"
PAYLOAD_SCHEMA_VERSION = "platform.risk.payload.v1"
WINDOW = "3y"
WINDOW_SESSIONS = 756
LAYER = "actual_run"
REQUIRED_FROZEN_DATASETS = (
    "price_panel",
    "construction_weights",
    "landed_weights",
    "universe",
    "benchmark",
)


def _dataset_manifest_hash(
    versions: Mapping[str, DatasetVersionRef],
) -> str:
    """Canonical identity of the exact role -> dataset-version manifest."""

    return canonical_hash({role: versions[role] for role in sorted(versions)})


def _dataset_ref(value: Any, role: str) -> DatasetVersionRef:
    """Accept the in-process object or its canonical JSON representation."""

    if isinstance(value, DatasetVersionRef):
        return value
    raw = _object(value, f"dataset_versions.{role}")
    try:
        return DatasetVersionRef(
            dataset_id=raw["dataset_id"],
            version_id=raw["version_id"],
            content_hash=raw.get("content_hash", raw.get("content_sha256")),
            observed_at=_timestamp(raw.get("observed_at"), f"dataset_versions.{role}.observed_at"),
            acquired_at=_timestamp(raw.get("acquired_at"), f"dataset_versions.{role}.acquired_at"),
            effective_at=_timestamp(raw.get("effective_at"), f"dataset_versions.{role}.effective_at"),
            finality=raw["finality"],
            schema_version=raw["schema_version"],
            request_hash=raw["request_hash"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StockSelectRiskAdapterError(
            f"dataset_versions.{role} is not a complete DatasetVersionRef: {exc}"
        ) from exc


class StockSelectRiskAdapterError(RuntimeError):
    """The platform request and the workbench result cannot be reconciled."""


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StockSelectRiskAdapterError(f"{field} must be an object")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StockSelectRiskAdapterError(f"{field} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise StockSelectRiskAdapterError(f"{field} must be a finite number")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StockSelectRiskAdapterError(f"{field} must be a timezone-aware datetime")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise StockSelectRiskAdapterError(
            f"{field} must be a timezone-aware datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StockSelectRiskAdapterError(f"{field} must be a timezone-aware datetime")
    return parsed


def _full_digest(value: Any, field: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise StockSelectRiskAdapterError(f"{field} must be a full SHA-256 digest")
    return digest


def _request_identity(request: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise StockSelectRiskAdapterError(
            f"schema_version must equal {REQUEST_SCHEMA_VERSION!r}"
        )
    binding = _object(request.get("binding"), "binding")
    run = _object(request.get("run"), "run")
    for field in ("run_id", "context_hash", "strategy_id", "strategy_version", "portfolio_id"):
        if not isinstance(binding.get(field), str) or not str(binding[field]).strip():
            raise StockSelectRiskAdapterError(f"binding.{field} is required")
    _full_digest(binding["context_hash"], "binding.context_hash")
    _timestamp(binding.get("as_of"), "binding.as_of")
    if binding["strategy_id"] != "stock_select":
        raise StockSelectRiskAdapterError("binding.strategy_id must equal 'stock_select'")
    if run.get("run_id") != binding["run_id"]:
        raise StockSelectRiskAdapterError("run.run_id does not match binding.run_id")
    if run.get("portfolio_id") != binding["portfolio_id"]:
        raise StockSelectRiskAdapterError("run.portfolio_id does not match binding.portfolio_id")

    required = _object(request.get("required_metrics"), "required_metrics")
    expected = {
        "var_99_1d": (1, 0.99),
        "var_99_10d": (10, 0.99),
        "es_97_5": (10, 0.975),
        "tracking_error": (None, None),
    }
    if set(required) != set(expected):
        raise StockSelectRiskAdapterError(
            "required_metrics must contain exactly var_99_1d, var_99_10d, es_97_5, tracking_error"
        )
    for name, (horizon, confidence) in expected.items():
        spec = _object(required[name], f"required_metrics.{name}")
        if spec.get("window") != WINDOW or spec.get("window_sessions") != WINDOW_SESSIONS:
            raise StockSelectRiskAdapterError(
                f"required_metrics.{name} must request the fixed 3y/756-session window"
            )
        if horizon is not None and spec.get("horizon_days") != horizon:
            raise StockSelectRiskAdapterError(
                f"required_metrics.{name}.horizon_days must equal {horizon}"
            )
        if confidence is not None and not math.isclose(
            _number(spec.get("confidence_level"), f"required_metrics.{name}.confidence_level"),
            confidence,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise StockSelectRiskAdapterError(
                f"required_metrics.{name}.confidence_level must equal {confidence}"
            )
    return binding, run


def _actual_run_name(constructions: Mapping[str, Any]) -> str:
    names = [
        str(name)
        for name, raw in constructions.items()
        if isinstance(raw, Mapping) and raw.get("is_canonical_executable") is True
    ]
    if len(names) != 1:
        raise StockSelectRiskAdapterError(
            f"expected exactly one canonical executable construction, found {len(names)}"
        )
    return names[0]


def _full_workbench_lineage(
    result: Mapping[str, Any],
    source_construction: str,
    _layer: Mapping[str, Any],
    _window: Mapping[str, Any],
) -> dict[str, Any]:
    """Reproduce the workbench hashes without its display-only 16-char truncation.

    The platform contract requires full SHA-256 identities.  These digests use
    the same canonical inputs as ``risk_metrics``: the confirmed shared panel,
    the actual landed weights, the requested calculation sample, and every
    source module able to change a reported number.
    """

    import pandas as pd

    from .. import research, risk_metrics, risk_workbench

    px = research.price_panel()
    acquired_at, acquisition = risk_workbench.price_acquisition()
    if acquired_at is None:
        raise StockSelectRiskAdapterError(
            f"price acquisition provenance unavailable: {acquisition.get('reason')}"
        )
    grid, calendar = risk_metrics.session_index(
        px,
        risk_workbench.TE_INDEX,
        acquired_at,
        source=str(acquisition.get("source") or "run price acquisition"),
    )
    if len(grid) < WINDOW_SESSIONS:
        raise StockSelectRiskAdapterError(
            f"confirmed reference grid has only {len(grid)} sessions; need {WINDOW_SESSIONS}"
        )
    requested_grid = grid[-WINDOW_SESSIONS:]
    landed_weights, landed = risk_workbench.landed_book()
    if not landed.get("available") or not landed_weights:
        raise StockSelectRiskAdapterError(
            f"actual landed weights unavailable: {landed.get('reason')}"
        )
    result_run = _object(result.get("run") or {}, "result.run")
    if landed.get("run_id") != result_run.get("run_id"):
        raise StockSelectRiskAdapterError(
            "landed weights run_id does not match the risk workbench result"
        )

    def frame_hash(frame, sessions, columns=None):
        cols = sorted(columns) if columns else sorted(frame.columns)
        sub = frame.loc[
            frame.index.isin(sessions), [c for c in cols if c in frame.columns]
        ].astype("float64")
        raw = pd.util.hash_pandas_object(sub.fillna(-1.0), index=True).values.tobytes()
        return hashlib.sha256(raw).hexdigest()

    weight_payload = json.dumps(
        {str(k): round(float(v), 10) for k, v in sorted(landed_weights.items())},
        sort_keys=True,
    ).encode()
    calculator_parts = []
    calculator_dir = risk_metrics.pathlib.Path(risk_metrics.__file__).parent
    for name in sorted(risk_metrics.CALCULATOR_MODULES):
        path = calculator_dir / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
        calculator_parts.append(f"{name}:{digest}")
    first_bars = {}
    for ticker in landed_weights:
        if ticker not in px.columns:
            first_bars[ticker] = None
            continue
        valid = pd.to_numeric(px[ticker], errors="coerce")
        valid = valid[valid > 0].dropna()
        first_bars[ticker] = (
            None if valid.empty else str(valid.index[0].date())
        )
    return {
        "data_snapshot_hash": frame_hash(px, grid),
        "portfolio_weights_hash": hashlib.sha256(weight_payload).hexdigest(),
        "calculator_hash": hashlib.sha256("|".join(calculator_parts).encode()).hexdigest(),
        "calculation_sample_hash": frame_hash(
            px, requested_grid, sorted(landed_weights)
        ),
        "source_construction": source_construction,
        "holding_first_bars": first_bars,
        "asof": str(grid[-1].date()),
        "acquired_at": str(acquisition.get("acquired_at") or acquired_at.isoformat()),
        "finality": str(
            "confirmed close; finality derived from snapshot acquisition time "
            f"({((calendar.get('finality') or {}).get('basis')) or 'acquisition'})"
        ),
    }


def _lineage(
    binding: Mapping[str, Any],
    result: Mapping[str, Any],
    layer: Mapping[str, Any] | None,
    window: Mapping[str, Any] | None,
    construction: str,
    full: Mapping[str, Any],
    frozen_binding: FrozenDatasetBinding | None = None,
    frozen_inputs_consumed: bool = False,
    frozen_reason: str | None = None,
) -> dict[str, Any]:
    layer = layer or {}
    window = window or {}
    provenance = layer.get("provenance") if isinstance(layer.get("provenance"), Mapping) else {}
    session = layer.get("session_index") if isinstance(layer.get("session_index"), Mapping) else {}
    finality = session.get("finality") if isinstance(session.get("finality"), Mapping) else {}
    for short_field, full_field in (
        ("data_snapshot_hash", "data_snapshot_hash"),
        ("portfolio_weights_hash", "portfolio_weights_hash"),
        ("calculator_code_hash", "calculator_hash"),
    ):
        short = provenance.get(short_field)
        digest = full.get(full_field)
        if short and (not isinstance(digest, str) or not digest.startswith(str(short))):
            raise StockSelectRiskAdapterError(
                f"full {full_field} does not reconcile to workbench {short_field}"
            )
    short_sample = window.get("calculation_sample_hash")
    if short_sample and not str(full.get("calculation_sample_hash") or "").startswith(
        str(short_sample)
    ):
        raise StockSelectRiskAdapterError(
            "full calculation_sample_hash does not reconcile to the workbench sample"
        )
    for field in (
        "data_snapshot_hash",
        "portfolio_weights_hash",
        "calculator_hash",
        "calculation_sample_hash",
    ):
        _full_digest(full.get(field), f"full calculation lineage {field}")
    frozen_lineage = (
        frozen_binding.lineage()
        if frozen_binding is not None
        else {
            "schema_version": FrozenDatasetBinding.schema_version,
            "complete": False,
            "required_roles": list(REQUIRED_FROZEN_DATASETS),
            "dataset_versions": {},
            "reason": frozen_reason
            or "no verified content-addressed risk input binding was supplied",
        }
    )
    response = {
        "run_id": binding["run_id"],
        "context_hash": binding["context_hash"],
        "strategy_id": binding["strategy_id"],
        "strategy_version": binding["strategy_version"],
        "portfolio_id": binding["portfolio_id"],
        "construction": construction,
        "layer": LAYER,
        "data_snapshot_hash": full.get("data_snapshot_hash"),
        "portfolio_weights_hash": full.get("portfolio_weights_hash"),
        "calculator_hash": full.get("calculator_hash"),
        "config_hash": result.get("config_hash"),
        "calculation_sample_hash": full.get("calculation_sample_hash"),
        "asof": full.get("asof") or provenance.get("asof_last_confirmed_close"),
        "acquired_at": full.get("acquired_at") or provenance.get("acquired_at"),
        "finality": full.get("finality") or provenance.get("finality_basis"),
        "finality_detail": {
            "confirmed_through": finality.get("confirmed_through")
            or provenance.get("confirmed_through"),
            "acquired_after_close": finality.get("acquired_after_close"),
            "wall_clock_independent": finality.get("wall_clock_independent"),
            "rule": finality.get("rule"),
        },
        "frozen_run_reconciliation": (
            frozen_binding.binding_hash
            if frozen_binding is not None and frozen_inputs_consumed
            else provenance.get("reconciliation")
        ),
        "frozen_input_binding_hash": (
            frozen_binding.binding_hash if frozen_binding is not None else None
        ),
        "input_manifest_hash": (
            _dataset_manifest_hash(
                {
                    role: frozen_binding.datasets[role].reference
                    for role in frozen_binding.required_roles
                }
            )
            if frozen_binding is not None
            else None
        ),
        "frozen_inputs_consumed": bool(frozen_inputs_consumed),
        "frozen_datasets": frozen_lineage,
        "legacy_data_manifest_hash": (_object(result.get("run") or {}, "result.run")).get(
            "data_manifest_hash"
        ),
    }
    required = tuple(
        response.get(field)
        for field in (
            "data_snapshot_hash",
            "portfolio_weights_hash",
            "calculator_hash",
            "calculation_sample_hash",
            "asof",
            "acquired_at",
            "finality",
        )
    )
    response["complete"] = bool(
        all(required)
        and frozen_binding is not None
        and frozen_binding.complete
        and frozen_inputs_consumed
        and response["frozen_run_reconciliation"] == frozen_binding.binding_hash
    )
    if not response["complete"]:
        response["reason"] = (
            frozen_reason
            or "risk inputs were not consumed from a complete, catalog-verified, "
            "content-addressed frozen dataset binding"
        )
    return response


def _scope(
    primary_construction: str,
    source_construction: str,
    lineage: Mapping[str, Any],
    layer: Mapping[str, Any] | None,
) -> dict[str, Any]:
    layer_meta = (layer or {}).get("layer")
    layer_meta = layer_meta if isinstance(layer_meta, Mapping) else {}
    return {
        "name": primary_construction,
        "layer": LAYER,
        "window": WINDOW,
        "window_sessions": WINDOW_SESSIONS,
        "source_construction": source_construction,
        "basis": {
            "algorithm_identity": "run.portfolio.construction",
            "landed_weights_source": (
                "catalog-verified frozen dataset role 'landed_weights'"
                if lineage.get("frozen_inputs_consumed") is True
                else (
                    "risk workbench canonical executable column "
                    f"{source_construction!r}, actual_run layer"
                )
            ),
            "detail": layer_meta.get("basis"),
        },
        "weight_identity": lineage.get("portfolio_weights_hash"),
    }


def _unavailable(
    binding: Mapping[str, Any],
    result: Mapping[str, Any],
    primary_construction: str,
    source_construction: str,
    layer: Mapping[str, Any] | None,
    window: Mapping[str, Any] | None,
    full_lineage: Mapping[str, Any],
    *,
    code: str,
    reason: str,
    frozen_binding: FrozenDatasetBinding | None = None,
    frozen_inputs_consumed: bool = False,
    frozen_reason: str | None = None,
) -> dict[str, Any]:
    window = window or {}
    missing = list(window.get("holdings_without_full_span") or [])
    limiting = window.get("limiting_holding")
    limiting_first = window.get("limiting_first_bar")
    first_bars = full_lineage.get("holding_first_bars")
    first_bars = first_bars if isinstance(first_bars, Mapping) else {}
    details = [
        {
            "ticker": str(ticker),
            "first_bar": first_bars.get(ticker)
            or (limiting_first if ticker == limiting else None),
            "required_start": window.get("span_start"),
            "reason": (
                f"{ticker} does not have structural price history from the requested "
                f"span start {window.get('span_start')}"
            ),
        }
        for ticker in missing
    ]
    lineage = _lineage(
        binding,
        result,
        layer,
        window,
        primary_construction,
        full_lineage,
        frozen_binding,
        frozen_inputs_consumed,
        frozen_reason,
    )
    return {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "available": False,
        "binding": dict(binding),
        "construction": _scope(
            primary_construction, source_construction, lineage, layer
        ),
        "lineage": lineage,
        "metrics": {},
        "unavailable": {
            "code": code,
            "reason": reason,
            "window": WINDOW,
            "window_sessions": WINDOW_SESSIONS,
            "layer": LAYER,
            "span_start": window.get("span_start"),
            "span_end": window.get("span_end"),
            "available_sessions": window.get("n_sessions_available"),
            "trailing_uninterrupted_sessions": window.get(
                "n_trailing_uninterrupted_sessions"
            ),
            "limiting_holding": limiting,
            "limiting_holdings": details,
            "no_shorter_window_or_substitute": True,
        },
        "validation_status": "NOT VALIDATED",
    }


def _tracking_error(
    window: Mapping[str, Any],
    expected_benchmark: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    rows = [row for row in window.get("tracking_error") or [] if isinstance(row, Mapping)]
    available = [row for row in rows if row.get("available") is True]
    if not available:
        return None
    if expected_benchmark is not None:
        expected_kind = expected_benchmark.get("benchmark_kind")
        expected_source = expected_benchmark.get("benchmark_source")
        for row in available:
            if row.get("kind") == expected_kind and row.get("source") == expected_source:
                return row
        return None
    # A formal mandate benchmark wins when supplied.  Until then SOXX is an
    # explicitly labelled research proxy; it is never described as mandate TE.
    for row in available:
        if row.get("kind") == "formal mandate":
            return row
    for row in available:
        if "index proxy" in str(row.get("benchmark") or ""):
            return row
    return available[0]


class StockSelectRiskLoader:
    """Callable dependency injected into :class:`PlatformService`."""

    def __init__(
        self,
        *,
        build_construction: Callable[..., Mapping[str, Any] | None] | None = None,
        build_frozen_construction: Callable[..., Mapping[str, Any] | None] | None = None,
        enumerate_constructions: Callable[[], Mapping[str, Any]] | None = None,
        lineage_loader: Callable[
            [Mapping[str, Any], str, Mapping[str, Any], Mapping[str, Any]],
            Mapping[str, Any],
        ]
        | None = None,
        dataset_catalog: DatasetCatalog | None = None,
        dataset_versions_loader: Callable[
            [Mapping[str, Any]], Mapping[str, Any]
        ]
        | None = None,
        official_bootstrap_reps: int | None = None,
    ) -> None:
        self._builder = build_construction
        self._frozen_builder = build_frozen_construction
        self._enumerator = enumerate_constructions
        self._lineage_loader = lineage_loader
        self._dataset_catalog = dataset_catalog
        self._dataset_versions_loader = dataset_versions_loader
        self._official_reps = official_bootstrap_reps

    def _dependencies(self):
        if self._builder is not None and self._enumerator is not None:
            return (
                self._builder,
                self._enumerator,
                self._lineage_loader or _full_workbench_lineage,
                int(self._official_reps or 2000),
            )
        from .. import risk_workbench

        return (
            self._builder or risk_workbench.build_construction,
            self._enumerator or risk_workbench.enumerate_constructions,
            self._lineage_loader or _full_workbench_lineage,
            int(self._official_reps or risk_workbench.OFFICIAL_BOOTSTRAP_REPS),
        )

    def _frozen_inputs(
        self,
        request: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> tuple[FrozenDatasetBinding | None, str | None]:
        if self._dataset_catalog is None:
            return None, "the Stock Select risk adapter has no dataset catalog binding"
        try:
            raw = (
                self._dataset_versions_loader(request)
                if self._dataset_versions_loader is not None
                else {
                    "context_hash": request.get("dataset_context_hash"),
                    "dataset_versions": request.get("dataset_versions"),
                }
            )
            if not isinstance(raw, Mapping):
                raise TypeError("the frozen context loader must return an object")
            context_hash = _full_digest(
                raw.get("context_hash"), "frozen dataset context_hash"
            )
            if context_hash != str(binding["context_hash"]).lower():
                raise ValueError(
                    "frozen dataset context_hash does not match the risk binding"
                )
            versions = raw.get("dataset_versions")
            if not isinstance(versions, Mapping):
                raise TypeError("the frozen context has no dataset_versions mapping")
            refs = {
                str(role): _dataset_ref(value, str(role))
                for role, value in versions.items()
            }
            supplied_manifest_hash = request.get("input_manifest_hash")
            if supplied_manifest_hash is not None:
                supplied_manifest_hash = _full_digest(
                    supplied_manifest_hash, "input_manifest_hash"
                )
                if supplied_manifest_hash != _dataset_manifest_hash(refs):
                    raise ValueError(
                        "input_manifest_hash does not match the frozen dataset references"
                    )
            cutoff = _timestamp(binding.get("as_of"), "binding.as_of")
            acquired_by = (
                _timestamp(binding.get("run_started_at"), "binding.run_started_at")
                if binding.get("run_started_at") is not None
                else cutoff
            )
            frozen = self._dataset_catalog.freeze_versions(
                refs,
                required_roles=REQUIRED_FROZEN_DATASETS,
                as_of=cutoff,
                acquired_by=acquired_by,
                require_confirmed=True,
            )
            validate_frozen_stock_select_provenance(
                frozen,
                expected_run_id=str(binding["run_id"]),
                expected_as_of=cutoff,
            )
            validate_frozen_benchmark_contract(
                frozen,
                expected_portfolio_id=str(binding["portfolio_id"]),
            )
            return frozen, None
        except (OSError, TypeError, ValueError, KeyError) as exc:
            return None, f"frozen risk input verification failed: {exc}"

    def __call__(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request = _object(request, "request")
        binding, requested_run = _request_identity(request)
        builder, enumerator, lineage_loader, reps = self._dependencies()
        requested_portfolio = _object(requested_run.get("portfolio"), "run.portfolio")
        primary_construction = requested_portfolio.get("construction")
        if not isinstance(primary_construction, str) or not primary_construction.strip():
            raise StockSelectRiskAdapterError("run.portfolio.construction is required")
        primary_construction = primary_construction.strip()
        frozen_inputs, frozen_reason = self._frozen_inputs(request, binding)
        frozen_inputs_consumed = False
        if frozen_inputs is not None and self._frozen_builder is not None:
            result_raw = self._frozen_builder(
                frozen_inputs=frozen_inputs,
                layer=LAYER,
                window=WINDOW,
                bootstrap_reps=reps,
                force=False,
            )
            result = _object(result_raw, "frozen risk result")
            source_construction = result.get("construction")
            if not isinstance(source_construction, str) or not source_construction.strip():
                raise StockSelectRiskAdapterError(
                    "frozen risk result must identify its source construction"
                )
            source_construction = source_construction.strip()
            if result.get("frozen_input_binding_hash") != frozen_inputs.binding_hash:
                raise StockSelectRiskAdapterError(
                    "frozen risk result does not attest the supplied input binding"
                )
            expected_versions = {
                role: frozen_inputs.datasets[role].reference.version_id
                for role in REQUIRED_FROZEN_DATASETS
            }
            declared_versions = result.get("input_dataset_versions")
            if not isinstance(declared_versions, Mapping) or dict(declared_versions) != expected_versions:
                raise StockSelectRiskAdapterError(
                    "frozen risk result dataset versions do not match the supplied input binding"
                )
            frozen_inputs_consumed = True
        else:
            if frozen_inputs is not None:
                frozen_reason = (
                    "verified frozen datasets are present, but no frozen-input risk builder is configured"
                )
            source_construction = _actual_run_name(
                _object(enumerator(), "constructions")
            )
            result_raw = builder(
                source_construction,
                layer=LAYER,
                window=WINDOW,
                bootstrap_reps=reps,
                force=False,
            )
            result = _object(result_raw, "risk workbench result")
        result_run = _object(result.get("run") or {}, "result.run")
        if result_run.get("run_id") != binding["run_id"]:
            raise StockSelectRiskAdapterError(
                "risk workbench run_id does not match the frozen platform binding"
            )
        if result_run.get("portfolio_id") not in (None, binding["portfolio_id"]):
            raise StockSelectRiskAdapterError(
                "risk workbench portfolio_id does not match the frozen platform binding"
            )
        if result.get("construction") != source_construction or result.get("layer") != LAYER:
            raise StockSelectRiskAdapterError(
                "risk workbench returned a different construction or layer"
            )
        if result.get("window") != WINDOW:
            raise StockSelectRiskAdapterError("risk workbench returned a different window")
        if result.get("precision_tier") != "official":
            raise StockSelectRiskAdapterError("risk workbench did not use the official precision tier")

        risk = result.get("risk") if isinstance(result.get("risk"), Mapping) else {}
        layers = risk.get("layers") if isinstance(risk.get("layers"), Mapping) else {}
        layer = layers.get(LAYER) if isinstance(layers.get(LAYER), Mapping) else {}
        windows = layer.get("windows") if isinstance(layer.get("windows"), Mapping) else {}
        window = windows.get(WINDOW) if isinstance(windows.get(WINDOW), Mapping) else {}
        if frozen_inputs_consumed:
            full_lineage = _object(
                result.get("platform_lineage"), "frozen result platform_lineage"
            )
            if full_lineage.get("frozen_input_binding_hash") != frozen_inputs.binding_hash:
                raise StockSelectRiskAdapterError(
                    "frozen calculation lineage does not reconcile to its input binding"
                )
            expected_benchmark = validate_frozen_benchmark_contract(
                frozen_inputs,
                expected_portfolio_id=str(binding["portfolio_id"]),
            )
        else:
            full_lineage = _object(
                lineage_loader(result, source_construction, layer, window),
                "full calculation lineage",
            )
            expected_benchmark = None
        frozen_evidence = {
            "frozen_binding": frozen_inputs,
            "frozen_inputs_consumed": frozen_inputs_consumed,
            "frozen_reason": frozen_reason,
        }
        if result.get("available") is not True:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="RISK_WORKBENCH_UNAVAILABLE",
                reason=str(result.get("reason") or "risk workbench marked the result unavailable"),
                **frozen_evidence,
            )
        if layer.get("available") is not True:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="ACTUAL_RUN_LAYER_UNAVAILABLE",
                reason=str(layer.get("reason") or "actual landed run layer is unavailable"),
                **frozen_evidence,
            )
        layer_identity = layer.get("layer")
        if not isinstance(layer_identity, Mapping) or layer_identity.get("is_actual_run") is not True:
            raise StockSelectRiskAdapterError(
                "risk workbench actual_run layer is not identified as the actual landed run book"
            )
        if layer_identity.get("run_id") not in (None, binding["run_id"]):
            raise StockSelectRiskAdapterError(
                "risk workbench actual_run layer run_id does not match the frozen platform binding"
            )
        if window.get("available") is not True:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="INSUFFICIENT_STRUCTURAL_HISTORY",
                reason=str(window.get("reason") or "three-year window is unavailable"),
                **frozen_evidence,
            )

        horizons = _object(window.get("horizons"), "result 3y horizons")
        h1 = _object(horizons.get("1"), "result 3y horizon 1")
        h10 = _object(horizons.get("10"), "result 3y horizon 10")
        o1 = _object(h1.get("overlapping"), "result 3y horizon 1 overlapping")
        o10 = _object(h10.get("overlapping"), "result 3y horizon 10 overlapping")
        uncertainty = _object(
            h10.get("estimate_uncertainty"), "result 3y horizon 10 estimate_uncertainty"
        )
        ci = _object(uncertainty.get("var_99"), "result 3y VaR99 10d interval")
        te = _tracking_error(window, expected_benchmark)

        if te is None:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="TRACKING_ERROR_UNAVAILABLE",
                reason="no benchmark has enough intact observations for three-year tracking error",
                **frozen_evidence,
            )

        required_counts = {
            "var_99_1d": (o1.get("n_obs"), 755),
            "var_99_10d": (o10.get("n_obs"), 746),
            "es_97_5": (o10.get("n_obs"), 746),
            "tracking_error": (None if te is None else te.get("n_days"), 755),
        }
        insufficient = [
            f"{name}: {actual!r} != {minimum}"
            for name, (actual, minimum) in required_counts.items()
            if isinstance(actual, bool) or not isinstance(actual, int) or actual != minimum
        ]
        if insufficient:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="INSUFFICIENT_OBSERVATIONS",
                reason="canonical 3y observation requirements are not met: " + "; ".join(insufficient),
                **frozen_evidence,
            )
        if uncertainty.get("tier") != "official" or ci.get("available") is not True:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="OFFICIAL_CONFIDENCE_INTERVAL_UNAVAILABLE",
                reason=str(ci.get("reason") or "official VaR99 10-day interval is unavailable"),
                **frozen_evidence,
            )
        var_1d = _number(o1.get("var_99"), "var_99_1d")
        var_10d = _number(o10.get("var_99"), "var_99_10d")
        es_10d = _number(o10.get("es_975"), "es_97_5")
        te_value = _number(te.get("tracking_error_ann"), "tracking_error")
        ci_low = _number(ci.get("lo"), "var_99_10d confidence interval lower")
        ci_high = _number(ci.get("hi"), "var_99_10d confidence interval upper")
        if min(var_1d, var_10d, es_10d, te_value, ci_low) < 0 or ci_high < ci_low:
            raise StockSelectRiskAdapterError("risk workbench returned invalid loss or interval values")
        if not ci_low <= var_10d <= ci_high:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="CONFIDENCE_INTERVAL_EXCLUDES_ESTIMATE",
                reason=(
                    f"official VaR99 10-day estimate {var_10d} falls outside its reported "
                    f"interval [{ci_low}, {ci_high}]"
                ),
                **frozen_evidence,
            )

        sample = {
            "start": window.get("start"),
            "end": window.get("end"),
            "calculation_sample_hash": full_lineage.get("calculation_sample_hash"),
        }
        metric_base = {"window": WINDOW, "window_sessions": WINDOW_SESSIONS, **sample}
        metrics = {
            "var_99_1d": {
                "available": True,
                "value": var_1d,
                "unit": "fraction_of_nav",
                **metric_base,
                "horizon_days": 1,
                "confidence_level": 0.99,
                "observations": int(o1["n_obs"]),
                "estimator": "historical frozen-position P&L",
            },
            "var_99_10d": {
                "available": True,
                "value": var_10d,
                "unit": "fraction_of_nav",
                **metric_base,
                "horizon_days": 10,
                "confidence_level": 0.99,
                "observations": int(o10["n_obs"]),
                "estimator": "overlapping historical frozen-position P&L",
                "confidence_interval": {
                    "available": True,
                    "confidence_level": _number(ci.get("ci"), "interval confidence_level"),
                    "lower": ci_low,
                    "upper": ci_high,
                    "unit": "fraction_of_nav",
                    "method": str(ci.get("method") or uncertainty.get("primary_method")),
                    "block_length": ci.get("block_length"),
                    "reps": ci.get("reps"),
                    "seed": ci.get("seed"),
                    "tier": uncertainty.get("tier"),
                    "note": ci.get("note"),
                },
            },
            "es_97_5": {
                "available": True,
                "value": es_10d,
                "unit": "fraction_of_nav",
                **metric_base,
                "horizon_days": 10,
                "confidence_level": 0.975,
                "observations": int(o10["n_obs"]),
                "estimator": "overlapping historical frozen-position expected shortfall",
            },
            "tracking_error": {
                "available": True,
                "value": te_value,
                "unit": "annualized_fraction",
                **metric_base,
                "annualization_sessions": 252,
                "observations": int(te["n_days"]),
                "benchmark": str(te.get("benchmark") or ""),
                "benchmark_kind": (
                    te.get("kind")
                    if expected_benchmark is None
                    else expected_benchmark["benchmark_kind"]
                ),
                "benchmark_source": (
                    te.get("source")
                    if expected_benchmark is None
                    else expected_benchmark["benchmark_source"]
                ),
                "decision_usable": (
                    te.get("kind") == "formal mandate"
                    if expected_benchmark is None
                    else expected_benchmark["decision_usable"]
                ),
                **(
                    {}
                    if expected_benchmark is None
                    else {
                        "benchmark_dataset_id": expected_benchmark[
                            "benchmark_dataset_id"
                        ],
                        "benchmark_version_id": expected_benchmark[
                            "benchmark_version_id"
                        ],
                        "benchmark_content_sha256": expected_benchmark[
                            "benchmark_content_sha256"
                        ],
                    }
                ),
                "estimator": te.get("definition"),
            },
        }
        lineage = _lineage(
            binding,
            result,
            layer,
            window,
            primary_construction,
            full_lineage,
            frozen_inputs,
            frozen_inputs_consumed,
            frozen_reason,
        )
        if not lineage["complete"]:
            return _unavailable(
                binding,
                result,
                primary_construction,
                source_construction,
                layer,
                window,
                full_lineage,
                code="FROZEN_RISK_INPUTS_UNVERIFIED",
                reason=str(lineage.get("reason") or "frozen risk input lineage is incomplete"),
                **frozen_evidence,
            )
        return {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "available": True,
            "binding": dict(binding),
            "construction": _scope(
                primary_construction, source_construction, lineage, layer
            ),
            "lineage": lineage,
            "metrics": metrics,
            "validation_status": "NOT VALIDATED",
        }


stock_select_risk_loader = StockSelectRiskLoader()


__all__ = [
    "StockSelectRiskAdapterError",
    "StockSelectRiskLoader",
    "stock_select_risk_loader",
]
