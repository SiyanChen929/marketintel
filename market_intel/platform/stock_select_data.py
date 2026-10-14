"""Freeze the legacy Stock Select projection into exact catalog versions.

``outputs/latest`` is a mutable publication surface.  This module is the only
migration boundary allowed to read it for a platform run: every source is read
once, validated in memory, and only then registered in :class:`DatasetCatalog`.
Consumers receive exact version references and parse the frozen catalog bytes;
they never reopen the mutable projection.

Catalog finality describes the captured artifact, not research validity.  The
legacy strategy and every derived envelope remain ``NOT VALIDATED``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .core import (
    NOT_VALIDATED,
    DatasetRequest,
    DatasetVersionRef,
    FrozenMapping,
    canonical_hash,
    canonical_json,
)
from .data import DatasetCatalog


UTC = timezone.utc
REQUIRED_STOCK_SELECT_DATASETS = (
    "price_panel",
    "construction_weights",
    "landed_weights",
    "universe",
    "benchmark",
)
REQUIRED_DATASET_ROLES = REQUIRED_STOCK_SELECT_DATASETS
LANDED_WEIGHTS_SCHEMA_VERSION = "platform.stock-select.landed-weights.v1"
BENCHMARK_SCHEMA_VERSION = "platform.stock-select.benchmark.v1"
INPUT_MANIFEST_SCHEMA_VERSION = "platform.stock-select.input-manifest.v1"
FORMAL_BENCHMARK_PROVENANCE_SCHEMA_VERSION = (
    "platform.stock-select.benchmark-mandate-provenance.v1"
)
STOCK_SELECT_DATASET_SCHEMAS = {
    "price_panel": "stock-select.price-panel.v1",
    "construction_weights": "stock-select.construction-weights.v1",
    "landed_weights": LANDED_WEIGHTS_SCHEMA_VERSION,
    "universe": "stock-select.universe.v1",
    "benchmark": BENCHMARK_SCHEMA_VERSION,
}


class StockSelectDataError(ValueError):
    """The mutable legacy projection cannot be frozen without inventing data."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _aware_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StockSelectDataError(f"{field} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise StockSelectDataError(f"{field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StockSelectDataError(f"{field} must be a timezone-aware timestamp")
    return parsed.astimezone(UTC)


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StockSelectDataError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _full_sha256(value: Any, field: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise StockSelectDataError(f"{field} must be a full SHA-256")
    return digest


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise StockSelectDataError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise StockSelectDataError(f"{field} must be an ISO date") from exc


def _finite(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StockSelectDataError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise StockSelectDataError(f"{field} must be a finite number")
    if minimum is not None and result < minimum:
        raise StockSelectDataError(f"{field} must be at least {minimum}")
    return result


def _ticker(value: Any, field: str) -> str:
    ticker = str(value).strip().upper()
    if not ticker or ticker in {"NAN", "NONE"}:
        raise StockSelectDataError(f"{field} must be a non-empty ticker")
    if any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in ticker):
        raise StockSelectDataError(f"{field} contains unsupported characters")
    return ticker


def _instrument_ids(tickers: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"equity:US:{ticker}" for ticker in sorted(set(tickers)))


def _read_once(path: Path, field: str) -> bytes:
    """Read one physical file once and reject a concurrent replacement/write."""

    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            payload = handle.read()
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise StockSelectDataError(f"{field} is unreadable: {path}") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(payload) != after.st_size:
        raise StockSelectDataError(f"{field} changed while it was being read")
    if not payload:
        raise StockSelectDataError(f"{field} is empty")
    return payload


def _strict_json(payload: bytes, field: str) -> dict[str, Any]:
    def pairs(items):
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise StockSelectDataError(f"{field} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StockSelectDataError(f"{field} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise StockSelectDataError(f"{field} must contain one JSON object")
    return value


def _csv(payload: bytes, field: str) -> pd.DataFrame:
    try:
        text = bytes(payload).decode("utf-8-sig")
        rows = csv.reader(io.StringIO(text))
        header = next(rows)
    except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise StockSelectDataError(f"{field} is not a readable UTF-8 CSV") from exc
    normalized_header = [name.strip() for name in header]
    named = [name for name in normalized_header if name]
    if len(named) != len(set(named)):
        raise StockSelectDataError(f"{field} columns must be unique")
    try:
        return pd.read_csv(io.StringIO(text))
    except Exception as exc:
        raise StockSelectDataError(f"{field} is not a readable CSV") from exc


def parse_frozen_price_panel(payload: bytes, *, format: str = "parquet") -> pd.DataFrame:
    """Parse and canonicalise frozen price bytes without any filesystem access."""

    encoding = str(format).strip().lower()
    try:
        if encoding == "parquet":
            frame = pd.read_parquet(io.BytesIO(bytes(payload)))
        elif encoding == "csv":
            frame = _csv(bytes(payload), "price_panel")
            if frame.shape[1] < 2:
                raise StockSelectDataError(
                    "price_panel CSV must contain an index and at least one instrument"
                )
            frame = frame.set_index(frame.columns[0])
        else:
            raise StockSelectDataError("price_panel format must be 'parquet' or 'csv'")
    except StockSelectDataError:
        raise
    except Exception as exc:
        raise StockSelectDataError(f"price_panel is not valid {encoding}") from exc
    if frame.empty or frame.shape[1] == 0:
        raise StockSelectDataError("price_panel must contain observations and instruments")
    if not frame.columns.is_unique:
        raise StockSelectDataError("price_panel columns must be unique")
    columns = [_ticker(value, "price_panel column") for value in frame.columns]
    if len(set(columns)) != len(columns):
        raise StockSelectDataError("price_panel columns collide after ticker normalization")
    try:
        index = pd.to_datetime(frame.index, errors="raise", utc=True)
    except Exception as exc:
        raise StockSelectDataError("price_panel index must contain valid timestamps") from exc
    if index.hasnans or not index.is_unique:
        raise StockSelectDataError("price_panel index must be non-null and unique")
    # Risk calculations use session dates.  Canonical UTC-naive dates avoid
    # machine-local timezone drift while retaining the observed date labels.
    index = pd.DatetimeIndex(index).tz_convert(UTC).tz_localize(None)
    if not index.is_unique:
        raise StockSelectDataError("price_panel timestamps collide after UTC normalization")
    converted: dict[str, np.ndarray] = {}
    for original, ticker in zip(frame.columns, columns):
        try:
            converted[ticker] = pd.to_numeric(
                frame[original], errors="raise"
            ).to_numpy(dtype="float64")
        except Exception as exc:
            raise StockSelectDataError(f"price_panel[{ticker}] must be numeric") from exc
    numeric = pd.DataFrame(converted, index=index)
    values = numeric.to_numpy(dtype="float64", copy=False)
    if np.isinf(values).any():
        raise StockSelectDataError("price_panel cannot contain infinity")
    valid = ~np.isnan(values)
    if np.any(values[valid] <= 0):
        raise StockSelectDataError("price_panel non-missing prices must be positive")
    return numeric.astype("float64").sort_index().sort_index(axis=1)


def _ticker_indexed_csv(payload: bytes, field: str) -> pd.DataFrame:
    frame = _csv(payload, field)
    if frame.empty:
        raise StockSelectDataError(f"{field} must contain at least one row")
    if not frame.columns.is_unique:
        raise StockSelectDataError(f"{field} columns must be unique")
    ticker_column = "ticker" if "ticker" in frame.columns else str(frame.columns[0])
    if ticker_column != "ticker" and not ticker_column.lower().startswith("unnamed"):
        raise StockSelectDataError(f"{field} must contain a ticker column")
    tickers = [_ticker(value, f"{field}.ticker") for value in frame[ticker_column]]
    if len(set(tickers)) != len(tickers):
        raise StockSelectDataError(f"{field} tickers must be unique")
    frame = frame.drop(columns=[ticker_column]).copy()
    frame.index = pd.Index(tickers, name="ticker")
    return frame.sort_index()


def parse_frozen_construction_weights(payload: bytes) -> pd.DataFrame:
    """Return canonical ticker-by-construction weights from frozen CSV bytes."""

    frame = _ticker_indexed_csv(payload, "construction_weights")
    if frame.shape[1] == 0:
        raise StockSelectDataError("construction_weights has no construction columns")
    numeric = pd.DataFrame(index=frame.index)
    for column in frame.columns:
        name = str(column).strip()
        if not name:
            raise StockSelectDataError("construction_weights column names must be non-empty")
        try:
            series = pd.to_numeric(frame[column], errors="raise").astype("float64")
        except Exception as exc:
            raise StockSelectDataError(f"construction_weights[{name}] must be numeric") from exc
        if not np.isfinite(series.to_numpy()).all():
            raise StockSelectDataError(f"construction_weights[{name}] must be finite")
        if bool((series < 0).any()) or bool((series > 1 + 1e-12).any()):
            raise StockSelectDataError(f"construction_weights[{name}] must lie in [0, 1]")
        total = float(series.sum())
        if total <= 0 or total > 1 + 1e-8:
            raise StockSelectDataError(
                f"construction_weights[{name}] sum must lie in (0, 1]"
            )
        numeric[name] = series
    if not numeric.columns.is_unique:
        raise StockSelectDataError("construction_weights names must be unique")
    return numeric.sort_index().sort_index(axis=1)


def parse_frozen_universe(payload: bytes) -> pd.DataFrame:
    """Return a canonical frozen universe indexed by permanent ticker label."""

    return _ticker_indexed_csv(payload, "universe")


def _weight_mapping(value: Any, field: str, *, require_total_one: bool) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise StockSelectDataError(f"{field} must be a non-empty object")
    result: dict[str, float] = {}
    for raw_ticker, raw_weight in value.items():
        ticker = _ticker(raw_ticker, f"{field} ticker")
        if ticker in result:
            raise StockSelectDataError(f"{field} has duplicate normalized ticker {ticker}")
        weight = _finite(raw_weight, f"{field}[{ticker}]", minimum=0.0)
        if weight > 1 + 1e-12:
            raise StockSelectDataError(f"{field}[{ticker}] cannot exceed 1")
        result[ticker] = weight
    total = sum(result.values())
    if total <= 0:
        raise StockSelectDataError(f"{field} must have positive total weight")
    if require_total_one and not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError(f"{field} weights must sum to 1")
    return dict(sorted(result.items()))


def parse_frozen_landed_weights(payload: bytes) -> dict[str, Any]:
    """Validate the canonical executable-book envelope stored in the catalog."""

    value = _strict_json(bytes(payload), "landed_weights")
    if bytes(payload) != canonical_json(value).encode("utf-8"):
        raise StockSelectDataError("landed_weights must use canonical JSON encoding")
    expected_fields = {
        "schema_version",
        "run_id",
        "portfolio_id",
        "construction",
        "vol_target",
        "scale",
        "cash",
        "invested",
        "weights",
        "model_weights",
        "risk_free",
        "risk_free_source",
        "validation_status",
    }
    if set(value) != expected_fields:
        raise StockSelectDataError("landed_weights fields do not match the v1 schema")
    if value.get("schema_version") != LANDED_WEIGHTS_SCHEMA_VERSION:
        raise StockSelectDataError("landed_weights schema_version is unsupported")
    for field in ("run_id", "portfolio_id", "construction"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise StockSelectDataError(f"landed_weights.{field} is required")
    if value.get("validation_status") != NOT_VALIDATED:
        raise StockSelectDataError("landed_weights must remain NOT VALIDATED")
    weights = _weight_mapping(value.get("weights"), "landed_weights.weights", require_total_one=False)
    model = _weight_mapping(
        value.get("model_weights"), "landed_weights.model_weights", require_total_one=True
    )
    if set(weights) != set(model):
        raise StockSelectDataError("landed_weights executable and model tickers must match")
    cash = _finite(value.get("cash"), "landed_weights.cash", minimum=0.0)
    invested = _finite(value.get("invested"), "landed_weights.invested", minimum=0.0)
    scale = _finite(value.get("scale"), "landed_weights.scale", minimum=0.0)
    _finite(value.get("vol_target"), "landed_weights.vol_target", minimum=0.0)
    _finite(value.get("risk_free"), "landed_weights.risk_free", minimum=0.0)
    risk_free_source = value.get("risk_free_source")
    if not isinstance(risk_free_source, Mapping):
        raise StockSelectDataError("landed_weights.risk_free_source is required")
    if risk_free_source.get("artifact") != "summary.json" or risk_free_source.get("field") != "risk_free":
        raise StockSelectDataError("landed_weights.risk_free_source is unsupported")
    source_digest = str(risk_free_source.get("content_sha256") or "").lower()
    if len(source_digest) != 64 or any(ch not in "0123456789abcdef" for ch in source_digest):
        raise StockSelectDataError(
            "landed_weights.risk_free_source.content_sha256 must be a full SHA-256"
        )
    if cash > 1 + 1e-8 or invested > 1 + 1e-8 or scale > 1 + 1e-8:
        raise StockSelectDataError("landed_weights cash, invested and scale cannot exceed 1")
    if not math.isclose(sum(weights.values()), invested, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError("landed_weights weights do not sum to invested")
    if not math.isclose(cash + invested, 1.0, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError("landed_weights cash + invested must equal 1")
    if not math.isclose(scale, invested, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError("landed_weights scale must equal invested")
    for ticker in weights:
        if not math.isclose(weights[ticker], model[ticker] * scale, rel_tol=0.0, abs_tol=1e-8):
            raise StockSelectDataError(
                f"landed_weights executable weight does not reconcile for {ticker}"
            )
    return value


def parse_frozen_benchmark(payload: bytes) -> dict[str, Any]:
    """Validate the formal-mandate or explicitly non-decision-usable proxy."""

    value = _strict_json(bytes(payload), "benchmark")
    if bytes(payload) != canonical_json(value).encode("utf-8"):
        raise StockSelectDataError("benchmark must use canonical JSON encoding")
    if set(value) != {
        "schema_version",
        "kind",
        "decision_usable",
        "weights",
        "source",
        "validation_status",
    }:
        raise StockSelectDataError("benchmark fields do not match the v1 schema")
    if value.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise StockSelectDataError("benchmark schema_version is unsupported")
    kind = value.get("kind")
    if kind not in {"formal_mandate", "research_proxy"}:
        raise StockSelectDataError("benchmark.kind is unsupported")
    if value.get("decision_usable") is not (kind == "formal_mandate"):
        raise StockSelectDataError("benchmark decision_usable does not match its kind")
    if value.get("validation_status") != NOT_VALIDATED:
        raise StockSelectDataError("benchmark must remain NOT VALIDATED")
    if not isinstance(value.get("source"), Mapping):
        raise StockSelectDataError("benchmark.source is required")
    source = value["source"]
    source_fields = (
        {
            "type",
            "content_sha256",
            "original_weight_sum",
            "normalization",
            "mandate_id",
            "portfolio_id",
            "effective_at",
            "observed_at",
            "acquired_at",
            "finality",
            "provenance",
            "approval",
        }
        if kind == "formal_mandate"
        else {"type", "dataset_role", "content_sha256", "note"}
    )
    if set(source) != source_fields:
        raise StockSelectDataError("benchmark.source fields do not match benchmark.kind")
    source_digest = str(source.get("content_sha256") or "").lower()
    if len(source_digest) != 64 or any(ch not in "0123456789abcdef" for ch in source_digest):
        raise StockSelectDataError("benchmark.source.content_sha256 must be a full SHA-256")
    if kind == "formal_mandate":
        if source.get("type") != "formal_file" or source.get("normalization") != "divide by positive source sum":
            raise StockSelectDataError("formal benchmark source metadata is unsupported")
        original_sum = _finite(
            source.get("original_weight_sum"),
            "benchmark.source.original_weight_sum",
            minimum=0.0,
        )
        if original_sum <= 0:
            raise StockSelectDataError("formal benchmark source weight sum must be positive")
        for field in ("mandate_id", "portfolio_id"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise StockSelectDataError(f"formal benchmark source.{field} is required")
        effective_at = _aware_timestamp(
            source.get("effective_at"), "benchmark.source.effective_at"
        )
        observed_at = _aware_timestamp(
            source.get("observed_at"), "benchmark.source.observed_at"
        )
        acquired_at = _aware_timestamp(
            source.get("acquired_at"), "benchmark.source.acquired_at"
        )
        if effective_at > observed_at or observed_at > acquired_at:
            raise StockSelectDataError(
                "formal benchmark requires effective_at <= observed_at <= acquired_at"
            )
        if source.get("finality") not in {"confirmed", "final"}:
            raise StockSelectDataError(
                "formal benchmark source.finality must be confirmed or final"
            )
        provenance = source.get("provenance")
        if not isinstance(provenance, Mapping) or set(provenance) != {
            "source_system",
            "source_reference",
        }:
            raise StockSelectDataError(
                "formal benchmark source.provenance fields do not match the provenance envelope"
            )
        for field in ("source_system", "source_reference"):
            if not isinstance(provenance.get(field), str) or not provenance[field].strip():
                raise StockSelectDataError(
                    f"formal benchmark source.provenance.{field} is required"
                )
        approval = source.get("approval")
        if not isinstance(approval, Mapping) or set(approval) != {
            "approval_id",
            "status",
            "approved_by",
            "approved_at",
        }:
            raise StockSelectDataError(
                "formal benchmark source.approval fields do not match the approval envelope"
            )
        for field in ("approval_id", "approved_by"):
            if not isinstance(approval.get(field), str) or not approval[field].strip():
                raise StockSelectDataError(
                    f"formal benchmark source.approval.{field} is required"
                )
        if approval.get("status") != "approved":
            raise StockSelectDataError("formal benchmark mandate must be explicitly approved")
        approved_at = _aware_timestamp(
            approval.get("approved_at"), "benchmark.source.approval.approved_at"
        )
        if approved_at > observed_at:
            raise StockSelectDataError(
                "formal benchmark approval must exist by observed_at"
            )
    elif source.get("type") != "equal_weight_eligible_universe" or source.get("dataset_role") != "universe":
        raise StockSelectDataError("research proxy must identify the frozen universe source")
    _weight_mapping(value.get("weights"), "benchmark.weights", require_total_one=True)
    return value


def frozen_benchmark_metric_identity(
    benchmark: Mapping[str, Any],
    reference: DatasetVersionRef,
) -> dict[str, Any]:
    """Canonical TE identity derived from the verified benchmark bytes and ref."""

    if not isinstance(reference, DatasetVersionRef):
        raise StockSelectDataError("benchmark reference must be a DatasetVersionRef")
    kind = benchmark.get("kind")
    source = benchmark.get("source")
    if not isinstance(source, Mapping):
        raise StockSelectDataError("benchmark.source is required")
    source_digest = _full_sha256(
        source.get("content_sha256"), "benchmark.source.content_sha256"
    )
    if kind == "formal_mandate":
        mandate_id = str(source.get("mandate_id") or "").strip()
        effective_at = str(source.get("effective_at") or "").strip()
        if not mandate_id or not effective_at:
            raise StockSelectDataError(
                "formal benchmark metric identity requires mandate_id and effective_at"
            )
        metric_kind = "formal mandate"
        metric_source = f"formal_mandate:{mandate_id}:{effective_at}:{source_digest}"
        decision_usable = benchmark.get("decision_usable") is True
    elif kind == "research_proxy":
        source_type = str(source.get("type") or "").strip()
        if not source_type:
            raise StockSelectDataError("research benchmark metric identity requires source.type")
        metric_kind = "research proxy"
        metric_source = f"research_proxy:{source_type}:{source_digest}"
        decision_usable = False
    else:
        raise StockSelectDataError("benchmark.kind is unsupported")
    return {
        "benchmark_kind": metric_kind,
        "benchmark_source": metric_source,
        "decision_usable": decision_usable,
        "benchmark_dataset_id": reference.dataset_id,
        "benchmark_version_id": reference.version_id,
        "benchmark_content_sha256": reference.content_sha256,
    }


def validate_frozen_benchmark_contract(
    binding: Any,
    *,
    expected_portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Reconcile frozen benchmark payload, catalog request and dataset identity."""

    datasets = getattr(binding, "datasets", None)
    if not isinstance(datasets, Mapping) or "benchmark" not in datasets:
        raise StockSelectDataError("frozen benchmark dataset is required")
    version = datasets["benchmark"]
    reference = getattr(version, "reference", None)
    request = getattr(version, "request", None)
    if not isinstance(reference, DatasetVersionRef) or not isinstance(request, Mapping):
        raise StockSelectDataError("frozen benchmark is not a verified dataset version")
    benchmark = parse_frozen_benchmark(bytes(getattr(version, "payload", b"")))
    parameters = request.get("parameters")
    if not isinstance(parameters, Mapping):
        raise StockSelectDataError("frozen benchmark request.parameters is required")
    source = benchmark["source"]
    if parameters.get("decision_usable") is not benchmark["decision_usable"]:
        raise StockSelectDataError(
            "frozen benchmark request.parameters.decision_usable does not match its payload"
        )
    for field, expected in (
        ("kind", benchmark["kind"]),
        ("source_content_sha256", source["content_sha256"]),
    ):
        if parameters.get(field) != expected:
            raise StockSelectDataError(
                f"frozen benchmark request.parameters.{field} does not match its payload"
            )
    if benchmark["kind"] == "research_proxy":
        if parameters.get("decision_usable") is not False:
            raise StockSelectDataError("research proxy benchmark is never decision usable")
        universe = datasets.get("universe")
        universe_reference = getattr(universe, "reference", None)
        if not isinstance(universe_reference, DatasetVersionRef):
            raise StockSelectDataError(
                "research proxy benchmark requires a verified frozen universe"
            )
        if source.get("content_sha256") != universe_reference.content_sha256:
            raise StockSelectDataError(
                "research proxy benchmark source does not match the frozen universe"
            )
        universe_frame = parse_frozen_universe(
            bytes(getattr(universe, "payload", b""))
        )
        eligible = _eligible_tickers(universe_frame)
        benchmark_weights = _weight_mapping(
            benchmark.get("weights"), "benchmark.weights", require_total_one=True
        )
        expected_weight = 1.0 / len(eligible)
        if set(benchmark_weights) != set(eligible) or any(
            not math.isclose(weight, expected_weight, rel_tol=0.0, abs_tol=1e-12)
            for weight in benchmark_weights.values()
        ):
            raise StockSelectDataError(
                "research proxy weights do not match the frozen eligible universe"
            )
    else:
        if (
            expected_portfolio_id is not None
            and source.get("portfolio_id") != expected_portfolio_id
        ):
            raise StockSelectDataError(
                "formal benchmark mandate portfolio_id does not match the requested portfolio"
            )
        if _aware_timestamp(
            source.get("effective_at"), "benchmark.source.effective_at"
        ) != reference.effective_at:
            raise StockSelectDataError(
                "formal benchmark effective_at does not match its catalog version"
            )
        if _aware_timestamp(
            source.get("observed_at"), "benchmark.source.observed_at"
        ) != reference.observed_at:
            raise StockSelectDataError(
                "formal benchmark observed_at does not match its catalog version"
            )
        if source.get("finality") != reference.finality.value:
            raise StockSelectDataError(
                "formal benchmark finality does not match its catalog version"
            )
        source_acquired_at = _aware_timestamp(
            source.get("acquired_at"), "benchmark.source.acquired_at"
        )
        if source_acquired_at > reference.acquired_at:
            raise StockSelectDataError(
                "formal benchmark source acquisition is after catalog acquisition"
            )
        if source_acquired_at > _aware_datetime(
            getattr(binding, "as_of", None), "binding.as_of"
        ):
            raise StockSelectDataError(
                "formal benchmark source was acquired after the frozen vintage"
            )
        formal_parameters = {
            "mandate_id": source["mandate_id"],
            "mandate_effective_at": source["effective_at"],
            "mandate_observed_at": source["observed_at"],
            "mandate_acquired_at": source["acquired_at"],
            "mandate_finality": source["finality"],
            "mandate_approval_id": source["approval"]["approval_id"],
        }
        for field, expected in formal_parameters.items():
            if parameters.get(field) != expected:
                raise StockSelectDataError(
                    f"frozen benchmark request.parameters.{field} does not match its mandate"
                )
    return frozen_benchmark_metric_identity(benchmark, reference)


def _eligible_tickers(universe: pd.DataFrame) -> list[str]:
    if "eligible" not in universe.columns:
        raise StockSelectDataError(
            "universe.eligible is required to derive the research benchmark proxy"
        )
    series = universe["eligible"]
    eligible: list[str] = []
    for ticker, raw in series.items():
        if pd.isna(raw):
            flag = False
        elif isinstance(raw, (bool, np.bool_)):
            flag = bool(raw)
        elif isinstance(raw, (int, np.integer, float, np.floating)) and raw in (0, 1):
            flag = bool(raw)
        elif isinstance(raw, str) and raw.strip().lower() in {"true", "false"}:
            flag = raw.strip().lower() == "true"
        else:
            raise StockSelectDataError(f"universe.eligible is invalid for {ticker}")
        if flag:
            eligible.append(str(ticker))
    if not eligible:
        raise StockSelectDataError("universe has no eligible instruments for benchmark proxy")
    return sorted(eligible)


def _formal_benchmark(
    payload: bytes,
    source_sha256: str,
    provenance: Mapping[str, Any],
    *,
    portfolio_id: str,
    generated_at: datetime,
) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "mandate_id",
        "portfolio_id",
        "effective_at",
        "observed_at",
        "acquired_at",
        "finality",
        "weights_sha256",
        "provenance",
        "approval",
    }
    if set(provenance) != expected_fields:
        raise StockSelectDataError(
            "formal benchmark provenance fields do not match the mandate envelope"
        )
    if provenance.get("schema_version") != FORMAL_BENCHMARK_PROVENANCE_SCHEMA_VERSION:
        raise StockSelectDataError("formal benchmark provenance schema_version is unsupported")
    for field in ("mandate_id", "portfolio_id"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise StockSelectDataError(f"formal benchmark provenance.{field} is required")
    if provenance["portfolio_id"] != portfolio_id:
        raise StockSelectDataError(
            "formal benchmark provenance.portfolio_id does not match the frozen run"
        )
    expected_source_sha = _full_sha256(
        provenance.get("weights_sha256"), "formal benchmark provenance.weights_sha256"
    )
    if expected_source_sha != source_sha256:
        raise StockSelectDataError(
            "formal benchmark provenance.weights_sha256 does not match benchmark_weights.csv"
        )
    effective_at = _aware_timestamp(
        provenance.get("effective_at"), "formal benchmark provenance.effective_at"
    )
    observed_at = _aware_timestamp(
        provenance.get("observed_at"), "formal benchmark provenance.observed_at"
    )
    acquired_at = _aware_timestamp(
        provenance.get("acquired_at"), "formal benchmark provenance.acquired_at"
    )
    if effective_at > observed_at or observed_at > acquired_at:
        raise StockSelectDataError(
            "formal benchmark requires effective_at <= observed_at <= acquired_at"
        )
    if acquired_at > generated_at:
        raise StockSelectDataError(
            "formal benchmark mandate was acquired after the generated run"
        )
    finality = provenance.get("finality")
    if finality not in {"confirmed", "final"}:
        raise StockSelectDataError(
            "formal benchmark provenance.finality must be confirmed or final"
        )
    source_provenance = provenance.get("provenance")
    if not isinstance(source_provenance, Mapping) or set(source_provenance) != {
        "source_system",
        "source_reference",
    }:
        raise StockSelectDataError(
            "formal benchmark provenance.provenance is incomplete"
        )
    for field in ("source_system", "source_reference"):
        if not isinstance(source_provenance.get(field), str) or not source_provenance[field].strip():
            raise StockSelectDataError(
                f"formal benchmark provenance.provenance.{field} is required"
            )
    approval = provenance.get("approval")
    if not isinstance(approval, Mapping) or set(approval) != {
        "approval_id",
        "status",
        "approved_by",
        "approved_at",
    }:
        raise StockSelectDataError("formal benchmark approval envelope is incomplete")
    for field in ("approval_id", "approved_by"):
        if not isinstance(approval.get(field), str) or not approval[field].strip():
            raise StockSelectDataError(f"formal benchmark approval.{field} is required")
    if approval.get("status") != "approved":
        raise StockSelectDataError("formal benchmark mandate must be explicitly approved")
    approved_at = _aware_timestamp(
        approval.get("approved_at"), "formal benchmark approval.approved_at"
    )
    if approved_at > observed_at:
        raise StockSelectDataError("formal benchmark approval must exist by observed_at")

    frame = _csv(payload, "formal benchmark")
    if list(frame.columns) != ["ticker", "weight"]:
        raise StockSelectDataError("formal benchmark must contain exactly ticker,weight columns")
    tickers = [_ticker(value, "formal benchmark ticker") for value in frame["ticker"]]
    if len(set(tickers)) != len(tickers):
        raise StockSelectDataError("formal benchmark tickers must be unique")
    try:
        weights = pd.to_numeric(frame["weight"], errors="raise").astype("float64")
    except Exception as exc:
        raise StockSelectDataError("formal benchmark weights must be numeric") from exc
    if not np.isfinite(weights.to_numpy()).all() or bool((weights <= 0).any()):
        raise StockSelectDataError("formal benchmark weights must be finite and positive")
    original_sum = float(weights.sum())
    if original_sum <= 0:
        raise StockSelectDataError("formal benchmark total weight must be positive")
    normalized = {
        ticker: float(weight / original_sum)
        for ticker, weight in sorted(zip(tickers, weights.tolist()))
    }
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "kind": "formal_mandate",
        "decision_usable": True,
        "weights": normalized,
        "source": {
            "type": "formal_file",
            "content_sha256": source_sha256,
            "original_weight_sum": original_sum,
            "normalization": "divide by positive source sum",
            "mandate_id": provenance["mandate_id"],
            "portfolio_id": provenance["portfolio_id"],
            "effective_at": effective_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "acquired_at": acquired_at.isoformat(),
            "finality": finality,
            "provenance": dict(source_provenance),
            "approval": dict(approval),
        },
        "validation_status": NOT_VALIDATED,
    }


def _proxy_benchmark(universe: pd.DataFrame, universe_sha256: str) -> dict[str, Any]:
    eligible = _eligible_tickers(universe)
    weight = 1.0 / len(eligible)
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "kind": "research_proxy",
        "decision_usable": False,
        "weights": {ticker: weight for ticker in eligible},
        "source": {
            "type": "equal_weight_eligible_universe",
            "dataset_role": "universe",
            "content_sha256": universe_sha256,
            "note": "No formal mandate benchmark file was supplied; research use only.",
        },
        "validation_status": NOT_VALIDATED,
    }


def _legacy_manifest_hash(manifest: Mapping[str, Any]) -> str:
    entries = manifest.get("entries")
    if not isinstance(entries, Mapping):
        raise StockSelectDataError("data_manifest.entries must be an object")
    payload: dict[str, dict[str, Any]] = {}
    for key, raw in entries.items():
        if not isinstance(raw, Mapping):
            raise StockSelectDataError(f"data_manifest.entries[{key!r}] must be an object")
        payload[str(key)] = {name: value for name, value in raw.items() if name != "fetched_at"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _status_is_not_validated(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith(NOT_VALIDATED)


def _reconcile_projection(
    run: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    manifest: Mapping[str, Any],
    weights: pd.DataFrame,
) -> tuple[datetime, date, dict[str, Any]]:
    for field in ("run_id", "portfolio_id"):
        if not isinstance(run.get(field), str) or not run[field].strip():
            raise StockSelectDataError(f"run.{field} is required")
        if snapshot.get(field) != run[field]:
            raise StockSelectDataError(f"snapshot.{field} does not match run.{field}")
    generated = _aware_timestamp(run.get("generated_at"), "run.generated_at")
    if _aware_timestamp(snapshot.get("generated_at"), "snapshot.generated_at") != generated:
        raise StockSelectDataError("snapshot.generated_at does not match run.generated_at")
    if _aware_timestamp(manifest.get("generated_at"), "data_manifest.generated_at") != generated:
        raise StockSelectDataError("data_manifest.generated_at does not match run.generated_at")
    asof_session = _date(run.get("asof_session"), "run.asof_session")
    if _date(snapshot.get("asof_session"), "snapshot.asof_session") != asof_session:
        raise StockSelectDataError("snapshot.asof_session does not match run.asof_session")
    if not _status_is_not_validated(run.get("validation_status")):
        raise StockSelectDataError("run must remain NOT VALIDATED")
    if not _status_is_not_validated(snapshot.get("validation_status")):
        raise StockSelectDataError("snapshot must remain NOT VALIDATED")
    declared_manifest_hash = str(manifest.get("hash") or "").strip().lower()
    if declared_manifest_hash != _legacy_manifest_hash(manifest):
        raise StockSelectDataError("data_manifest legacy hash does not reconcile")
    if str(run.get("data_manifest_hash") or "").strip().lower() != declared_manifest_hash:
        raise StockSelectDataError("run.data_manifest_hash does not match data_manifest.hash")

    portfolio = run.get("portfolio")
    if not isinstance(portfolio, Mapping):
        raise StockSelectDataError("run.portfolio must be an object")
    construction = str(portfolio.get("construction") or "").strip()
    if not construction or construction not in weights.columns:
        raise StockSelectDataError("run portfolio construction is absent from construction_weights")
    if "recommended" not in weights.columns:
        raise StockSelectDataError("construction_weights must contain recommended executable weights")
    positions = portfolio.get("positions")
    if not isinstance(positions, list) or not positions:
        raise StockSelectDataError("run.portfolio.positions must be a non-empty list")
    executable: dict[str, float] = {}
    model: dict[str, float] = {}
    for number, raw in enumerate(positions):
        if not isinstance(raw, Mapping):
            raise StockSelectDataError(f"run.portfolio.positions[{number}] must be an object")
        ticker = _ticker(raw.get("ticker"), f"run.portfolio.positions[{number}].ticker")
        if ticker in executable:
            raise StockSelectDataError("run.portfolio.positions tickers must be unique")
        executable[ticker] = _finite(
            raw.get("target_executable_weight"),
            f"run.portfolio.positions[{number}].target_executable_weight",
            minimum=0.0,
        )
        model[ticker] = _finite(
            raw.get("target_model_weight"),
            f"run.portfolio.positions[{number}].target_model_weight",
            minimum=0.0,
        )
    if set(executable) != set(weights.index):
        raise StockSelectDataError("run positions do not match construction_weights tickers")
    for ticker in executable:
        if not math.isclose(
            executable[ticker], float(weights.loc[ticker, "recommended"]), rel_tol=0.0, abs_tol=1e-10
        ):
            raise StockSelectDataError(f"recommended executable weight mismatch for {ticker}")
        if not math.isclose(
            model[ticker], float(weights.loc[ticker, construction]), rel_tol=0.0, abs_tol=1e-10
        ):
            raise StockSelectDataError(f"model construction weight mismatch for {ticker}")
    cash = _finite(portfolio.get("cash"), "run.portfolio.cash", minimum=0.0)
    invested = _finite(portfolio.get("invested"), "run.portfolio.invested", minimum=0.0)
    scale = _finite(portfolio.get("scale"), "run.portfolio.scale", minimum=0.0)
    vol_target = _finite(portfolio.get("vol_target"), "run.portfolio.vol_target", minimum=0.0)
    if not math.isclose(sum(executable.values()), invested, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError("run executable positions do not sum to invested")
    if not math.isclose(sum(model.values()), 1.0, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError("run model positions do not sum to 1")
    if not math.isclose(cash + invested, 1.0, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError("run cash + invested does not equal 1")
    if not math.isclose(scale, invested, rel_tol=0.0, abs_tol=1e-8):
        raise StockSelectDataError("run scale does not equal invested")
    if portfolio.get("reconciles") is not True:
        raise StockSelectDataError("run portfolio does not declare reconciliation")

    snapshot_weights = snapshot.get("weights")
    snapshot_model = snapshot.get("weights_model")
    if _weight_mapping(snapshot_weights, "snapshot.weights", require_total_one=False) != dict(
        sorted(executable.items())
    ):
        raise StockSelectDataError("snapshot executable weights do not match run")
    if _weight_mapping(snapshot_model, "snapshot.weights_model", require_total_one=True) != dict(
        sorted(model.items())
    ):
        raise StockSelectDataError("snapshot model weights do not match run")
    for field, expected in (
        ("construction", construction),
        ("cash", cash),
        ("scale", scale),
        ("vol_target", vol_target),
    ):
        actual = snapshot.get(field)
        if isinstance(expected, float):
            actual = _finite(actual, f"snapshot.{field}")
            equal = math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-10)
        else:
            equal = actual == expected
        if not equal:
            raise StockSelectDataError(f"snapshot.{field} does not match run portfolio")
    return generated, asof_session, {
        "schema_version": LANDED_WEIGHTS_SCHEMA_VERSION,
        "run_id": run["run_id"],
        "portfolio_id": run["portfolio_id"],
        "construction": construction,
        "vol_target": vol_target,
        "scale": scale,
        "cash": cash,
        "invested": invested,
        "weights": dict(sorted(executable.items())),
        "model_weights": dict(sorted(model.items())),
        # Filled from the run-bound summary by the producer before encoding.
        "risk_free": None,
        "risk_free_source": None,
        "validation_status": NOT_VALIDATED,
    }


@dataclass(frozen=True, slots=True)
class StockSelectFrozenInputs:
    """Complete exact input set for one retrospectively frozen Stock Select run."""

    run: Mapping[str, Any]
    snapshot: Mapping[str, Any]
    source_manifest: Mapping[str, Any]
    dataset_versions: Mapping[str, DatasetVersionRef]
    as_of: datetime
    run_started_at: datetime
    input_manifest_hash: str
    source_manifest_sha256: str
    legacy_data_manifest_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run", FrozenMapping(self.run))
        object.__setattr__(self, "snapshot", FrozenMapping(self.snapshot))
        object.__setattr__(self, "source_manifest", FrozenMapping(self.source_manifest))
        versions = FrozenMapping(self.dataset_versions)
        if tuple(versions) != tuple(sorted(REQUIRED_STOCK_SELECT_DATASETS)) or set(versions) != set(
            REQUIRED_STOCK_SELECT_DATASETS
        ):
            raise StockSelectDataError("dataset_versions must contain exactly the five required roles")
        if any(not isinstance(reference, DatasetVersionRef) for reference in versions.values()):
            raise StockSelectDataError("dataset_versions values must be DatasetVersionRef objects")
        object.__setattr__(self, "dataset_versions", versions)
        expected_hash = canonical_hash(
            {role: versions[role] for role in sorted(REQUIRED_STOCK_SELECT_DATASETS)}
        )
        if self.input_manifest_hash != expected_hash:
            raise StockSelectDataError("input_manifest_hash does not match dataset_versions")
        object.__setattr__(self, "as_of", _aware_datetime(self.as_of, "as_of"))
        object.__setattr__(
            self,
            "run_started_at",
            _aware_datetime(self.run_started_at, "run_started_at"),
        )
        if len(self.source_manifest_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in self.source_manifest_sha256
        ):
            raise StockSelectDataError("source_manifest_sha256 must be a full SHA-256")
        if self.run_started_at < self.as_of:
            raise StockSelectDataError("run_started_at cannot precede as_of")
        if any(ref.acquired_at > self.run_started_at for ref in versions.values()):
            raise StockSelectDataError("run_started_at precedes a dataset acquisition")

    @property
    def context_dataset_versions(self) -> Mapping[str, DatasetVersionRef]:
        return self.dataset_versions

    @property
    def canonical_manifest_bytes(self) -> bytes:
        return canonical_json(
            {role: self.dataset_versions[role] for role in sorted(REQUIRED_STOCK_SELECT_DATASETS)}
        ).encode("utf-8")

    @property
    def input_manifest_bytes(self) -> bytes:
        return self.canonical_manifest_bytes

    @property
    def lineage_summary(self) -> dict[str, Any]:
        benchmark_ref = self.dataset_versions["benchmark"]
        return {
            "schema_version": INPUT_MANIFEST_SCHEMA_VERSION,
            "complete": True,
            "required_roles": list(REQUIRED_STOCK_SELECT_DATASETS),
            "dataset_version_count": len(self.dataset_versions),
            "dataset_versions": {
                role: {
                    "dataset_id": ref.dataset_id,
                    "version_id": ref.version_id,
                    "request_hash": ref.request_hash,
                    "content_sha256": ref.content_sha256,
                    "schema_version": ref.schema_version,
                    "effective_at": ref.effective_at.isoformat(),
                    "observed_at": ref.observed_at.isoformat(),
                    "acquired_at": ref.acquired_at.isoformat(),
                    "finality": ref.finality.value,
                }
                for role, ref in self.dataset_versions.items()
            },
            "input_manifest_hash": self.input_manifest_hash,
            "source_manifest_sha256": self.source_manifest_sha256,
            "legacy_data_manifest_hash": self.legacy_data_manifest_hash,
            "as_of": self.as_of.isoformat(),
            "run_started_at": self.run_started_at.isoformat(),
            "benchmark_version_id": benchmark_ref.version_id,
            "validation_status": NOT_VALIDATED,
        }


def validate_frozen_stock_select_provenance(
    binding: Any,
    *,
    expected_run_id: str | None = None,
    expected_as_of: datetime | None = None,
    expected_source_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Fail closed unless all five versions share one run, manifest and vintage."""

    if not getattr(binding, "complete", False):
        raise StockSelectDataError("frozen Stock Select binding is incomplete")
    required_roles = tuple(getattr(binding, "required_roles", ()))
    datasets = getattr(binding, "datasets", None)
    if not isinstance(datasets, Mapping):
        raise StockSelectDataError("frozen Stock Select binding datasets are required")
    if (
        len(required_roles) != len(REQUIRED_STOCK_SELECT_DATASETS)
        or set(required_roles) != set(REQUIRED_STOCK_SELECT_DATASETS)
        or set(datasets) != set(REQUIRED_STOCK_SELECT_DATASETS)
    ):
        raise StockSelectDataError(
            "frozen Stock Select binding must contain exactly the five required roles"
        )
    run_ids: set[str] = set()
    manifest_hashes: set[str] = set()
    vintages: set[datetime] = set()
    for role in REQUIRED_STOCK_SELECT_DATASETS:
        version = datasets[role]
        reference = getattr(version, "reference", None)
        request = getattr(version, "request", None)
        if not isinstance(reference, DatasetVersionRef) or not isinstance(request, Mapping):
            raise StockSelectDataError(f"frozen role {role} is not a verified dataset version")
        expected_dataset = f"stock_select:{role}"
        expected_schema = STOCK_SELECT_DATASET_SCHEMAS[role]
        if reference.dataset_id != expected_dataset or request.get("dataset") != expected_dataset:
            raise StockSelectDataError(f"frozen role {role} has the wrong dataset identity")
        if (
            reference.schema_version != expected_schema
            or request.get("schema_version") != expected_schema
        ):
            raise StockSelectDataError(f"frozen role {role} has the wrong schema_version")
        parameters = request.get("parameters")
        if not isinstance(parameters, Mapping):
            raise StockSelectDataError(f"frozen role {role} request.parameters is required")
        run_id = str(parameters.get("legacy_run_id") or "").strip()
        if not run_id:
            raise StockSelectDataError(
                f"frozen role {role} request.parameters.legacy_run_id is required"
            )
        run_ids.add(run_id)
        manifest_hashes.add(
            _full_sha256(
                parameters.get("source_manifest_sha256"),
                f"frozen role {role} request.parameters.source_manifest_sha256",
            )
        )
        vintages.add(
            _aware_timestamp(
                request.get("vintage_as_of"), f"frozen role {role} request.vintage_as_of"
            )
        )
    if len(run_ids) != 1:
        raise StockSelectDataError("frozen Stock Select roles span multiple legacy_run_id values")
    if len(manifest_hashes) != 1:
        raise StockSelectDataError(
            "frozen Stock Select roles span multiple source_manifest_sha256 values"
        )
    if len(vintages) != 1:
        raise StockSelectDataError("frozen Stock Select roles span multiple vintages")
    run_id = next(iter(run_ids))
    manifest_hash = next(iter(manifest_hashes))
    vintage_as_of = next(iter(vintages))
    binding_as_of = _aware_datetime(getattr(binding, "as_of", None), "binding.as_of")
    if vintage_as_of != binding_as_of:
        raise StockSelectDataError(
            "frozen Stock Select request vintage does not match the binding as_of"
        )
    if expected_run_id is not None and run_id != expected_run_id:
        raise StockSelectDataError(
            "frozen Stock Select legacy_run_id does not match the requested run"
        )
    if expected_as_of is not None and vintage_as_of != _aware_datetime(
        expected_as_of, "expected_as_of"
    ):
        raise StockSelectDataError(
            "frozen Stock Select vintage does not match the requested as_of"
        )
    if expected_source_manifest_sha256 is not None and manifest_hash != _full_sha256(
        expected_source_manifest_sha256, "expected_source_manifest_sha256"
    ):
        raise StockSelectDataError(
            "frozen Stock Select source manifest does not match the requested run"
        )
    return {
        "legacy_run_id": run_id,
        "source_manifest_sha256": manifest_hash,
        "vintage_as_of": vintage_as_of.isoformat(),
    }


@dataclass(frozen=True, slots=True)
class _PreparedDataset:
    role: str
    request: DatasetRequest
    payload: bytes
    effective_at: datetime
    observed_at: datetime
    finality: str


def freeze_stock_select_latest(
    latest_dir: str | Path,
    catalog: DatasetCatalog,
    *,
    acquired_at: datetime | None = None,
    benchmark_path: str | Path | None = None,
    benchmark_provenance_path: str | Path | None = None,
    benchmark_directory: str | Path | None = None,
) -> StockSelectFrozenInputs:
    """Read, validate and freeze one complete mutable Stock Select projection.

    ``acquired_at`` is the platform migration time.  It deliberately does not
    replace the price vendor's original acquisition timestamp, which remains in
    the price request's ``source_acquired_at`` parameter.
    """

    if not isinstance(catalog, DatasetCatalog):
        raise TypeError("catalog must be a DatasetCatalog")
    root = Path(latest_dir)
    migration_time = _aware_datetime(
        acquired_at or datetime.now(UTC), "acquired_at"
    )

    # All mutable bytes are captured before any catalog mutation.
    run_payload = _read_once(root / "run.json", "run.json")
    snapshot_payload = _read_once(root / "snapshot.json", "snapshot.json")
    manifest_payload = _read_once(root / "data_manifest.json", "data_manifest.json")
    construction_payload = _read_once(
        root / "portfolio_weights.csv", "portfolio_weights.csv"
    )
    universe_payload = _read_once(root / "universe_scores.csv", "universe_scores.csv")
    run = _strict_json(run_payload, "run.json")
    snapshot = _strict_json(snapshot_payload, "snapshot.json")
    manifest = _strict_json(manifest_payload, "data_manifest.json")
    construction = parse_frozen_construction_weights(construction_payload)
    universe = parse_frozen_universe(universe_payload)
    generated_at, asof_session, landed = _reconcile_projection(
        run, snapshot, manifest, construction
    )
    if migration_time < generated_at:
        raise StockSelectDataError("acquired_at cannot precede the generated legacy run")

    # risk_free belongs to the exact generated run but is only projected in
    # summary.json.  It must be frozen with the landed book because cash return
    # changes VaR/ES.  Absence blocks migration rather than silently using 0.
    summary_payload = _read_once(root / "summary.json", "summary.json")
    summary = _strict_json(summary_payload, "summary.json")
    for field in ("run_id", "portfolio_id", "generated_at"):
        expected = run.get(field)
        actual = summary.get(field)
        if field == "generated_at":
            if _aware_timestamp(actual, "summary.generated_at") != generated_at:
                raise StockSelectDataError("summary.generated_at does not match run.generated_at")
        elif actual != expected:
            raise StockSelectDataError(f"summary.{field} does not match run.{field}")
    landed["risk_free"] = _finite(summary.get("risk_free"), "summary.risk_free", minimum=0.0)
    landed["risk_free_source"] = {
        "artifact": "summary.json",
        "field": "risk_free",
        "content_sha256": _sha(summary_payload),
    }
    landed_payload = canonical_json(landed).encode("utf-8")
    parse_frozen_landed_weights(landed_payload)

    entries = manifest.get("entries")
    price_entry = entries.get("prices") if isinstance(entries, Mapping) else None
    if not isinstance(price_entry, Mapping):
        raise StockSelectDataError("data_manifest.entries.prices is required")
    price_path_raw = price_entry.get("path")
    if not isinstance(price_path_raw, str) or not price_path_raw.strip():
        raise StockSelectDataError("data_manifest.entries.prices.path is required")
    price_path = Path(price_path_raw)
    price_payload = _read_once(price_path, "price panel")
    price_sha = _sha(price_payload)
    legacy_price_sha = str(price_entry.get("sha256") or "").strip().lower()
    if not legacy_price_sha or len(legacy_price_sha) > 64 or any(
        char not in "0123456789abcdef" for char in legacy_price_sha
    ):
        raise StockSelectDataError("data_manifest price sha256 must be a hex digest or prefix")
    if not price_sha.startswith(legacy_price_sha):
        raise StockSelectDataError("price panel does not match the manifest sha256 prefix")
    suffix = price_path.suffix.lower()
    price_format = "parquet" if suffix in {".parquet", ".pq"} else "csv" if suffix == ".csv" else ""
    if not price_format:
        raise StockSelectDataError("price panel path must use parquet or csv")
    prices = parse_frozen_price_panel(price_payload, format=price_format)
    if int(price_entry.get("n_tickers", -1)) != prices.shape[1]:
        raise StockSelectDataError("price panel column count does not match manifest n_tickers")
    effective_date = _date(price_entry.get("effective_at"), "prices.effective_at")
    if prices.index[-1].date() != effective_date:
        raise StockSelectDataError("price panel last session does not match prices.effective_at")
    source_acquired_at = _aware_timestamp(
        price_entry.get("fetched_at"), "prices.fetched_at"
    )
    if source_acquired_at > generated_at:
        raise StockSelectDataError("price source was acquired after the generated run")
    price_finality = str(price_entry.get("finality") or "").strip().lower()
    if price_finality not in {"provisional", "confirmed", "final"}:
        raise StockSelectDataError("prices.finality is unsupported")

    held = list(landed["weights"])
    missing_prices = sorted(set(held) - set(prices.columns))
    missing_universe = sorted(set(held) - set(universe.index))
    if missing_prices:
        raise StockSelectDataError(
            "landed instruments absent from price_panel: " + ", ".join(missing_prices)
        )
    if missing_universe:
        raise StockSelectDataError(
            "landed instruments absent from universe: " + ", ".join(missing_universe)
        )

    # Archive ingestion must preserve the live projection's benchmark location;
    # an archive is deeper in the tree and has a different parent directory.
    benchmark_dir = Path(benchmark_directory) if benchmark_directory is not None else root.parent.parent / "data"
    inferred_benchmark_path = benchmark_dir / "benchmark_weights.csv"
    formal_path = Path(benchmark_path) if benchmark_path is not None else inferred_benchmark_path
    provenance_path = (
        Path(benchmark_provenance_path)
        if benchmark_provenance_path is not None
        else formal_path.with_name(f"{formal_path.stem}.provenance.json")
    )
    if formal_path.is_file():
        formal_payload = _read_once(formal_path, "formal benchmark")
        if not provenance_path.is_file():
            raise StockSelectDataError(
                "formal benchmark file requires an explicit mandate provenance envelope"
            )
        formal_provenance_payload = _read_once(
            provenance_path, "formal benchmark provenance"
        )
        formal_provenance = _strict_json(
            formal_provenance_payload, "formal benchmark provenance"
        )
        benchmark = _formal_benchmark(
            formal_payload,
            _sha(formal_payload),
            formal_provenance,
            portfolio_id=str(run["portfolio_id"]),
            generated_at=generated_at,
        )
    elif benchmark_path is not None or benchmark_provenance_path is not None:
        raise StockSelectDataError("explicit formal benchmark input is missing")
    elif provenance_path.is_file():
        raise StockSelectDataError(
            "formal benchmark provenance exists without benchmark_weights.csv"
        )
    else:
        benchmark = _proxy_benchmark(universe, _sha(universe_payload))
    benchmark_payload = canonical_json(benchmark).encode("utf-8")
    parse_frozen_benchmark(benchmark_payload)
    benchmark_tickers = set(benchmark["weights"])
    if benchmark["kind"] == "formal_mandate" and not benchmark_tickers <= set(prices.columns):
        missing = sorted(benchmark_tickers - set(prices.columns))
        raise StockSelectDataError(
            "formal benchmark instruments absent from price_panel: " + ", ".join(missing)
        )

    source_manifest_sha = _sha(manifest_payload)
    common_parameters = {
        "legacy_run_id": run["run_id"],
        "source_manifest_sha256": source_manifest_sha,
        "retrospective_catalog_migration": True,
        "validation_status": NOT_VALIDATED,
    }
    snapshot_effective = datetime.combine(asof_session, time.min, tzinfo=UTC)
    benchmark_source = benchmark["source"]
    if benchmark["kind"] == "formal_mandate":
        benchmark_effective = _aware_timestamp(
            benchmark_source["effective_at"], "benchmark.source.effective_at"
        )
        benchmark_observed = _aware_timestamp(
            benchmark_source["observed_at"], "benchmark.source.observed_at"
        )
        benchmark_finality = str(benchmark_source["finality"])
    else:
        benchmark_effective = snapshot_effective
        benchmark_observed = generated_at
        benchmark_finality = "confirmed"
    price_request = DatasetRequest(
        provider=str(price_entry.get("vendor") or "unverified provider"),
        entitlement="unverified",
        dataset="stock_select:price_panel",
        instrument_ids=_instrument_ids(list(prices.columns)),
        fields=(str(price_entry.get("field") or "adjusted_close"),),
        frequency=str(price_entry.get("frequency") or "daily"),
        adjustment=str(price_entry.get("adjustment") or "unverified"),
        start=prices.index[0].date(),
        end=prices.index[-1].date(),
        as_of=generated_at,
        schema_version="stock-select.price-panel.v1",
        parameters={
            **common_parameters,
            "format": price_format,
            "source_acquired_at": source_acquired_at.isoformat(),
            "source_sha256": price_sha,
            "legacy_source_sha256": legacy_price_sha,
            "source_status": price_entry.get("status"),
            "source_stale_reason": price_entry.get("stale_reason"),
            "currency": price_entry.get("currency"),
            "unit": price_entry.get("unit"),
        },
    )
    output_tickers = sorted(set(universe.index) | set(held))
    prepared = (
        _PreparedDataset(
            "price_panel",
            price_request,
            price_payload,
            datetime.combine(effective_date, time.min, tzinfo=UTC),
            source_acquired_at,
            price_finality,
        ),
        _PreparedDataset(
            "construction_weights",
            DatasetRequest(
                provider="stock_select_pipeline",
                entitlement="local_research_artifact",
                dataset="stock_select:construction_weights",
                instrument_ids=_instrument_ids(list(construction.index)),
                fields=tuple(map(str, construction.columns)),
                frequency="run_snapshot",
                adjustment="none",
                start=asof_session,
                end=asof_session,
                as_of=generated_at,
                schema_version="stock-select.construction-weights.v1",
                parameters={**common_parameters, "format": "csv"},
            ),
            construction_payload,
            snapshot_effective,
            generated_at,
            "confirmed",
        ),
        _PreparedDataset(
            "landed_weights",
            DatasetRequest(
                provider="stock_select_pipeline",
                entitlement="local_research_artifact",
                dataset="stock_select:landed_weights",
                instrument_ids=_instrument_ids(held),
                fields=(
                    "target_executable_weight",
                    "target_model_weight",
                    "cash",
                    "scale",
                    "risk_free",
                ),
                frequency="run_snapshot",
                adjustment="none",
                start=asof_session,
                end=asof_session,
                as_of=generated_at,
                schema_version=LANDED_WEIGHTS_SCHEMA_VERSION,
                parameters={
                    **common_parameters,
                    "format": "canonical_json",
                    "risk_free_source_sha256": _sha(summary_payload),
                },
            ),
            landed_payload,
            snapshot_effective,
            generated_at,
            "confirmed",
        ),
        _PreparedDataset(
            "universe",
            DatasetRequest(
                provider="stock_select_pipeline",
                entitlement="local_research_artifact",
                dataset="stock_select:universe",
                instrument_ids=_instrument_ids(output_tickers),
                fields=tuple(map(str, universe.columns)),
                frequency="run_snapshot",
                adjustment="none",
                start=asof_session,
                end=asof_session,
                as_of=generated_at,
                schema_version="stock-select.universe.v1",
                parameters={**common_parameters, "format": "csv"},
            ),
            universe_payload,
            snapshot_effective,
            generated_at,
            "confirmed",
        ),
        _PreparedDataset(
            "benchmark",
            DatasetRequest(
                provider=(
                    "analyst_mandate_file"
                    if benchmark["kind"] == "formal_mandate"
                    else "stock_select_pipeline"
                ),
                entitlement="local_research_artifact",
                dataset="stock_select:benchmark",
                instrument_ids=_instrument_ids(list(benchmark["weights"])),
                fields=("weight",),
                frequency="run_snapshot",
                adjustment="normalized_weights",
                start=asof_session,
                end=asof_session,
                as_of=generated_at,
                schema_version=BENCHMARK_SCHEMA_VERSION,
                parameters={
                    **common_parameters,
                    "format": "canonical_json",
                    "kind": benchmark["kind"],
                    "decision_usable": benchmark["decision_usable"],
                    "source_content_sha256": benchmark["source"]["content_sha256"],
                    **(
                        {
                            "mandate_id": benchmark_source["mandate_id"],
                            "mandate_effective_at": benchmark_source["effective_at"],
                            "mandate_observed_at": benchmark_source["observed_at"],
                            "mandate_acquired_at": benchmark_source["acquired_at"],
                            "mandate_finality": benchmark_source["finality"],
                            "mandate_approval_id": benchmark_source["approval"]["approval_id"],
                        }
                        if benchmark["kind"] == "formal_mandate"
                        else {}
                    ),
                },
            ),
            benchmark_payload,
            benchmark_effective,
            benchmark_observed,
            benchmark_finality,
        ),
    )

    refs: dict[str, DatasetVersionRef] = {}
    for item in prepared:
        version = catalog.register(
            item.request,
            item.payload,
            effective_at=item.effective_at,
            observed_at=item.observed_at,
            acquired_at=migration_time,
            finality=item.finality,
            expected_sha256=_sha(item.payload),
        )
        refs[item.role] = version.to_ref(item.request.dataset)
    run_started_at = max(generated_at, *(reference.acquired_at for reference in refs.values()))
    input_manifest_hash = canonical_hash(
        {role: refs[role] for role in sorted(REQUIRED_STOCK_SELECT_DATASETS)}
    )
    verified_binding = catalog.freeze_versions(
        refs,
        required_roles=REQUIRED_STOCK_SELECT_DATASETS,
        as_of=generated_at,
        acquired_by=run_started_at,
        require_confirmed=True,
    )
    validate_frozen_stock_select_provenance(
        verified_binding,
        expected_run_id=str(run["run_id"]),
        expected_as_of=generated_at,
        expected_source_manifest_sha256=source_manifest_sha,
    )
    validate_frozen_benchmark_contract(
        verified_binding,
        expected_portfolio_id=str(run["portfolio_id"]),
    )
    return StockSelectFrozenInputs(
        run=run,
        snapshot=snapshot,
        source_manifest=manifest,
        dataset_versions=refs,
        as_of=generated_at,
        run_started_at=run_started_at,
        input_manifest_hash=input_manifest_hash,
        source_manifest_sha256=source_manifest_sha,
        legacy_data_manifest_hash=str(manifest["hash"]),
    )


__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "FORMAL_BENCHMARK_PROVENANCE_SCHEMA_VERSION",
    "INPUT_MANIFEST_SCHEMA_VERSION",
    "LANDED_WEIGHTS_SCHEMA_VERSION",
    "REQUIRED_DATASET_ROLES",
    "REQUIRED_STOCK_SELECT_DATASETS",
    "StockSelectDataError",
    "StockSelectFrozenInputs",
    "STOCK_SELECT_DATASET_SCHEMAS",
    "freeze_stock_select_latest",
    "frozen_benchmark_metric_identity",
    "parse_frozen_benchmark",
    "parse_frozen_construction_weights",
    "parse_frozen_landed_weights",
    "parse_frozen_price_panel",
    "parse_frozen_universe",
    "validate_frozen_benchmark_contract",
    "validate_frozen_stock_select_provenance",
]
