"""Persistent, price-explicit metering and budget gates for LLM requests.

The ledger stores usage metadata only; prompts and model responses never enter it.
Rates are deliberately absent from source code.  A cost is available only when
the operator supplied every rate needed by that particular response.

Runtime configuration is explicit: ``MARKET_INTEL_LLM_USAGE_DB``,
``MARKET_INTEL_LLM_PRICING_JSON``, ``MARKET_INTEL_LLM_TOKEN_BUDGET``,
``MARKET_INTEL_LLM_COST_BUDGET_USD`` and
``MARKET_INTEL_LLM_BUDGET_WINDOW_SECONDS``. Pricing JSON maps provider -> model
-> per-million-token rate fields; ``*`` is accepted only as an explicit model
wildcard.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from . import config


SCHEMA_VERSION = "llm_usage.v1"
DB_SCHEMA_VERSION = 1
DEFAULT_WINDOW_SECONDS = 86_400
_RATE_ALIASES = {
    "input_per_million_usd": ("input_per_million_usd", "input_usd_per_million"),
    "output_per_million_usd": ("output_per_million_usd", "output_usd_per_million"),
    "cache_read_per_million_usd": ("cache_read_per_million_usd", "cache_read_usd_per_million"),
    "cache_write_per_million_usd": ("cache_write_per_million_usd", "cache_write_usd_per_million"),
}
_REQUIRED_COLUMNS = {
    "id", "occurred_at", "provider", "model", "operation", "status", "usage_reported", "calls",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cache_tokens", "total_tokens",
    "cost_status", "cost_reason", "cost_usd", "pricing_sha256", "pricing_json", "provider_request_id", "error_type",
}
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    usage_reported INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 1,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost_status TEXT NOT NULL DEFAULT 'unavailable',
    cost_reason TEXT,
    cost_usd TEXT,
    pricing_sha256 TEXT,
    pricing_json TEXT,
    provider_request_id TEXT,
    error_type TEXT
)
"""


class LLMUsageError(RuntimeError):
    """The usage ledger could not safely meter an LLM request."""


class LLMBudgetExceeded(LLMUsageError):
    """A configured budget, or an uncertainty that prevents enforcing it, blocked a call."""


def _nonnegative_decimal(value, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{label} must be a non-negative decimal")
    return parsed


def _optional_budget_decimal(value, label: str) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    return _nonnegative_decimal(value, label)


def _optional_budget_int(value, label: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer")
    try:
        parsed = int(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if parsed < 0 or str(value).strip() not in {str(parsed), f"+{parsed}"}:
        raise ValueError(f"{label} must be a non-negative integer")
    return parsed


def _strict_count(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LLMUsageError(f"{label} must be a non-negative integer")
    return value


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _normalise_pricing(raw: dict | None) -> dict[str, dict[str, dict[str, Decimal]]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("MARKET_INTEL_LLM_PRICING_JSON must be a JSON object")
    expanded: dict[str, dict] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError("pricing provider names must be strings")
        if "/" in key and isinstance(value, dict) and any(alias in value for aliases in _RATE_ALIASES.values() for alias in aliases):
            provider, model = key.split("/", 1)
            expanded.setdefault(provider, {})[model] = value
        else:
            expanded[key] = value
    out: dict[str, dict[str, dict[str, Decimal]]] = {}
    for provider, models in expanded.items():
        if not isinstance(models, dict):
            raise ValueError(f"pricing for provider {provider!r} must map models to rates")
        for model, rates in models.items():
            if not isinstance(model, str) or not model or not isinstance(rates, dict):
                raise ValueError(f"pricing for provider {provider!r} has an invalid model entry")
            canonical: dict[str, Decimal] = {}
            for canonical_name, aliases in _RATE_ALIASES.items():
                found = [rates[name] for name in aliases if name in rates]
                if len(found) > 1 and len({str(v) for v in found}) > 1:
                    raise ValueError(f"conflicting aliases for {provider}/{model}/{canonical_name}")
                if found:
                    canonical[canonical_name] = _nonnegative_decimal(found[0], f"{provider}/{model}/{canonical_name}")
            unknown = set(rates) - {name for aliases in _RATE_ALIASES.values() for name in aliases}
            if unknown:
                raise ValueError(f"unknown pricing fields for {provider}/{model}: {', '.join(sorted(unknown))}")
            if not canonical:
                raise ValueError(f"pricing for {provider}/{model} contains no rates")
            out.setdefault(provider.strip().lower(), {})[model.strip()] = canonical
    return out


def unavailable_payload(reason: str, *, blocked: bool, totals: dict | None = None) -> dict:
    """Stable read-model shape when no ledger snapshot can be produced."""
    totals = totals or {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "total_tokens": 0}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "persistence": {"status": "unavailable", "backend": "sqlite", "reason": reason},
        "token_definition": "OpenAI cached input is a subset of input; Anthropic cache read/write is added to uncached input",
        "totals": totals,
        "cost": {"status": "unavailable", "currency": "USD", "usd": None, "known_usd": None,
                 "unavailable_calls": totals.get("calls", 0), "reason": reason, "current_pricing_match": None},
        "budget": {
            "scope": "rolling_window",
            "window_seconds": None,
            "window_start": None,
            "window_end": None,
            "blocked": bool(blocked),
            "reason": reason if blocked else None,
            "reasons": [reason] if blocked else [],
            "token": {"configured": None, "limit": None, "used": None, "remaining": None, "status": "unavailable"},
            "cost": {"configured": None, "currency": "USD", "limit_usd": None, "known_used_usd": None,
                     "remaining_usd": None, "status": "unavailable"},
        },
        "by_provider_model_operation": [],
        "recent": [],
    }


class LLMUsageLedger:
    """SQLite-backed usage records plus a rolling-window budget gate."""

    def __init__(
        self,
        path: str | Path,
        *,
        pricing: dict | None = None,
        token_budget: int | str | None = None,
        cost_budget_usd: Decimal | str | float | None = None,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ):
        self.path = Path(path)
        self.pricing = _normalise_pricing(pricing)
        self.token_budget = _optional_budget_int(token_budget, "token budget")
        self.cost_budget_usd = _optional_budget_decimal(cost_budget_usd, "cost budget")
        self.window_seconds = int(window_seconds)
        if self.window_seconds <= 0:
            raise ValueError("budget window must be a positive number of seconds")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._initialize_storage()

    @classmethod
    def from_env(cls, path: str | Path | None = None) -> "LLMUsageLedger":
        pricing_text = os.environ.get("MARKET_INTEL_LLM_PRICING_JSON", "").strip()
        try:
            pricing = json.loads(pricing_text) if pricing_text else None
        except json.JSONDecodeError as exc:
            raise ValueError("MARKET_INTEL_LLM_PRICING_JSON must contain valid JSON") from exc
        raw_window = os.environ.get("MARKET_INTEL_LLM_BUDGET_WINDOW_SECONDS", str(DEFAULT_WINDOW_SECONDS)).strip()
        try:
            window = int(raw_window)
        except ValueError as exc:
            raise ValueError("MARKET_INTEL_LLM_BUDGET_WINDOW_SECONDS must be a positive integer") from exc
        ledger_path = Path(path or os.environ.get("MARKET_INTEL_LLM_USAGE_DB") or (config.DATA_DIR / "llm_usage.sqlite"))
        return cls(
            ledger_path,
            pricing=pricing,
            token_budget=os.environ.get("MARKET_INTEL_LLM_TOKEN_BUDGET"),
            cost_budget_usd=os.environ.get("MARKET_INTEL_LLM_COST_BUDGET_USD"),
            window_seconds=window,
        )

    @property
    def has_budget(self) -> bool:
        return self.token_budget is not None or self.cost_budget_usd is not None

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise LLMUsageError("usage clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    def _initialize_storage(self) -> None:
        """Create/adopt the ledger once, during construction rather than reads."""
        conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=FULL")
            self._ensure_schema(conn)
            conn.execute("PRAGMA journal_mode=WAL")
        except LLMUsageError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise LLMUsageError(f"LLM usage ledger initialization failed: {type(exc).__name__}") from exc
        finally:
            if conn is not None:
                conn.close()

    def _connect_write(self) -> sqlite3.Connection:
        conn = None
        try:
            if not self.path.is_file():
                raise LLMUsageError("LLM usage ledger unavailable: database missing")
            conn = sqlite3.connect(self.path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=FULL")
            self._validate_schema(conn)
            return conn
        except LLMUsageError:
            if conn is not None:
                conn.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if conn is not None:
                conn.close()
            raise LLMUsageError(f"LLM usage ledger unavailable: {type(exc).__name__}") from exc

    def _connect_read(self) -> sqlite3.Connection:
        """Open an existing ledger read-only; never create or migrate it."""
        conn = None
        try:
            if not self.path.is_file():
                raise LLMUsageError("LLM usage ledger unavailable: database missing")
            uri = self.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            self._validate_schema(conn)
            return conn
        except LLMUsageError:
            if conn is not None:
                conn.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if conn is not None:
                conn.close()
            raise LLMUsageError(f"LLM usage ledger unavailable: {type(exc).__name__}") from exc

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """Initialize or adopt only a structurally complete v0 ledger.

        There was no released pre-v1 partial layout to infer safely. An unknown
        or incomplete table is therefore rejected instead of being relabeled as
        v1 and silently under-counting a budget.
        """
        try:
            conn.execute("BEGIN IMMEDIATE")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > DB_SCHEMA_VERSION:
                raise LLMUsageError(f"LLM usage ledger schema {version} is newer than supported {DB_SCHEMA_VERSION}")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_usage'").fetchone() is not None
            if not exists:
                if version != 0:
                    raise LLMUsageError(f"LLM usage ledger schema {version} is inconsistent: table missing")
                conn.execute(_CREATE_TABLE_SQL)
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(llm_usage)")}
            missing = sorted(_REQUIRED_COLUMNS - columns)
            if missing:
                raise LLMUsageError("LLM usage ledger schema is incomplete: missing " + ", ".join(missing))
            if version not in (0, DB_SCHEMA_VERSION):
                raise LLMUsageError(f"unsupported LLM usage ledger schema {version}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_time ON llm_usage(occurred_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_dimensions ON llm_usage(provider, model, operation, occurred_at)")
            if version == 0:
                conn.execute(f"PRAGMA user_version={DB_SCHEMA_VERSION}")
            conn.commit()
        except LLMUsageError:
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise LLMUsageError(f"LLM usage schema initialization failed: {type(exc).__name__}") from exc

    @staticmethod
    def _validate_schema(conn: sqlite3.Connection) -> None:
        """Read-only validation used by all post-startup connections."""
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version != DB_SCHEMA_VERSION:
                raise LLMUsageError(f"unsupported LLM usage ledger schema {version}")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_usage'").fetchone() is not None
            if not exists:
                raise LLMUsageError("LLM usage ledger schema is inconsistent: table missing")
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(llm_usage)")}
            missing = sorted(_REQUIRED_COLUMNS - columns)
            if missing:
                raise LLMUsageError("LLM usage ledger schema is incomplete: missing " + ", ".join(missing))
        except LLMUsageError:
            raise
        except sqlite3.Error as exc:
            raise LLMUsageError(f"LLM usage schema validation failed: {type(exc).__name__}") from exc

    def _price_entry(self, provider: str, model: str) -> tuple[dict[str, Decimal] | None, str | None]:
        models = self.pricing.get((provider or "").lower(), {})
        if model in models:
            return models[model], model
        if "*" in models:
            return models["*"], "*"
        return None, None

    def _cost(
        self,
        provider: str,
        model: str,
        *,
        usage_reported: bool,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> dict:
        rates, matched_model = self._price_entry(provider, model)
        if not usage_reported:
            return {"status": "unavailable", "reason": "usage_not_reported", "usd": None, "pricing_sha256": None, "pricing_json": None}
        if rates is None:
            return {"status": "unavailable", "reason": "pricing_not_configured", "usd": None, "pricing_sha256": None, "pricing_json": None}
        if provider.lower() == "openai":
            if cache_read_tokens > input_tokens:
                return {"status": "unavailable", "reason": "cache_tokens_exceed_input_tokens", "usd": None, "pricing_sha256": None, "pricing_json": None}
            components = (
                (input_tokens - cache_read_tokens, "input_per_million_usd"),
                (cache_read_tokens, "cache_read_per_million_usd"),
                (cache_write_tokens, "cache_write_per_million_usd"),
                (output_tokens, "output_per_million_usd"),
            )
        else:
            components = (
                (input_tokens, "input_per_million_usd"),
                (cache_read_tokens, "cache_read_per_million_usd"),
                (cache_write_tokens, "cache_write_per_million_usd"),
                (output_tokens, "output_per_million_usd"),
            )
        missing = [name for count, name in components if count and name not in rates]
        if missing:
            return {"status": "unavailable", "reason": "missing_rate:" + ",".join(sorted(set(missing))), "usd": None,
                    "pricing_sha256": None, "pricing_json": None}
        usd = sum((Decimal(count) * rates.get(name, Decimal(0)) for count, name in components), Decimal(0)) / Decimal(1_000_000)
        bound = {"provider": provider.lower(), "model": matched_model, **{key: _decimal_text(value) for key, value in sorted(rates.items())}}
        pricing_json = json.dumps(bound, sort_keys=True, separators=(",", ":"))
        return {
            "status": "available",
            "reason": None,
            "usd": usd,
            "pricing_sha256": hashlib.sha256(pricing_json.encode()).hexdigest(),
            "pricing_json": pricing_json,
        }

    def _window_rows(self, conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
        start = now - timedelta(seconds=self.window_seconds)
        return list(conn.execute("SELECT * FROM llm_usage WHERE occurred_at >= ? ORDER BY id", (start.isoformat(),)))

    def _budget_state(self, conn: sqlite3.Connection, provider: str | None, model: str | None, now: datetime) -> dict:
        rows = self._window_rows(conn, now)
        token_used = sum(int(row["total_tokens"]) for row in rows)
        cost_known = sum((Decimal(row["cost_usd"]) for row in rows if row["cost_status"] == "available" and row["cost_usd"] is not None), Decimal(0))
        unknown_usage = sum(1 for row in rows if row["status"] != "complete" or not row["usage_reported"])
        unknown_cost = sum(1 for row in rows if row["status"] == "complete" and row["cost_status"] != "available")
        reasons: list[str] = []
        price, _ = self._price_entry(provider or "", model or "")
        pricing_missing = self.cost_budget_usd is not None and bool(provider and model) and price is None
        required_rates = ({"input_per_million_usd", "output_per_million_usd", "cache_read_per_million_usd"}
                          if (provider or "").lower() == "openai"
                          else set(_RATE_ALIASES))
        missing_budget_rates = sorted(required_rates - set(price or {}))
        pricing_incomplete = (self.cost_budget_usd is not None and bool(provider and model)
                              and price is not None and bool(missing_budget_rates))
        pricing_pending = self.cost_budget_usd is not None and bool(provider) and not model
        if self.token_budget is not None:
            if unknown_usage:
                reasons.append("token usage unavailable for one or more calls in the budget window")
            elif token_used >= self.token_budget:
                reasons.append("token budget exhausted")
        if self.cost_budget_usd is not None:
            # OpenAI may resolve the concrete model lazily.  The actual
            # reservation always supplies a model and therefore enforces this
            # check before a token-bearing request is sent.
            if pricing_missing:
                reasons.append("pricing unavailable for configured cost budget")
            elif pricing_incomplete:
                reasons.append("pricing incomplete for configured cost budget: " + ", ".join(missing_budget_rates))
            elif unknown_usage:
                reasons.append("cost usage unavailable for one or more calls in the budget window")
            elif unknown_cost:
                reasons.append("cost unavailable for one or more calls in the budget window")
            elif cost_known >= self.cost_budget_usd:
                reasons.append("cost budget exhausted")
        start = now - timedelta(seconds=self.window_seconds)
        token_remaining = None if self.token_budget is None else max(0, self.token_budget - token_used)
        cost_remaining = (None if self.cost_budget_usd is None or unknown_usage or unknown_cost or pricing_missing
                          or pricing_incomplete or pricing_pending else max(Decimal(0), self.cost_budget_usd - cost_known))
        return {
            "scope": "rolling_window",
            "window_seconds": self.window_seconds,
            "window_start": start.isoformat(),
            "window_end": now.isoformat(),
            "blocked": bool(reasons),
            "reason": reasons[0] if reasons else None,
            "reasons": reasons,
            "token": {
                "configured": self.token_budget is not None,
                "limit": self.token_budget,
                "used": token_used,
                "remaining": token_remaining,
                "status": "unavailable" if self.token_budget is not None and unknown_usage else "available",
            },
            "cost": {
                "configured": self.cost_budget_usd is not None,
                "currency": "USD",
                "limit_usd": _decimal_text(self.cost_budget_usd) if self.cost_budget_usd is not None else None,
                "known_used_usd": _decimal_text(cost_known),
                "remaining_usd": _decimal_text(cost_remaining) if cost_remaining is not None else None,
                "status": ("unavailable" if self.cost_budget_usd is not None
                           and (unknown_usage or unknown_cost or pricing_missing or pricing_incomplete or pricing_pending)
                           else "available"),
            },
        }

    def block_reason(self, provider: str | None, model: str | None) -> str | None:
        try:
            with self._lock:
                conn = self._connect_read()
                try:
                    return self._budget_state(conn, provider, model, self._now())["reason"]
                finally:
                    conn.close()
        except LLMUsageError as exc:
            return str(exc)

    def begin_call(self, provider: str, model: str, operation: str) -> int:
        provider = str(provider or "").strip().lower()
        model = str(model or "").strip()
        operation = str(operation or "unspecified").strip()[:80] or "unspecified"
        if provider not in {"anthropic", "openai"} or not model:
            raise LLMUsageError("provider and model are required for usage metering")
        now = self._now()
        with self._lock:
            conn = self._connect_write()
            try:
                conn.execute("BEGIN IMMEDIATE")
                state = self._budget_state(conn, provider, model, now)
                if state["blocked"]:
                    conn.rollback()
                    raise LLMBudgetExceeded(state["reason"] or "LLM budget blocked")
                cur = conn.execute(
                    "INSERT INTO llm_usage(occurred_at,provider,model,operation,status,cost_reason) VALUES(?,?,?,?,?,?)",
                    (now.isoformat(), provider, model, operation, "pending", "pending"),
                )
                conn.commit()
                return int(cur.lastrowid)
            except (LLMBudgetExceeded, LLMUsageError):
                raise
            except sqlite3.Error as exc:
                conn.rollback()
                raise LLMUsageError(f"LLM usage reservation failed: {type(exc).__name__}") from exc
            finally:
                conn.close()

    def complete_call(
        self,
        call_id: int,
        *,
        provider: str,
        model: str,
        usage_reported: bool,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        total_tokens: int,
        provider_request_id: str | None = None,
    ) -> dict:
        provider = str(provider or "").strip().lower()
        model = str(model or "").strip()
        if not isinstance(usage_reported, bool):
            raise LLMUsageError("usage_reported must be boolean")
        input_tokens = _strict_count(input_tokens, "input_tokens")
        output_tokens = _strict_count(output_tokens, "output_tokens")
        cache_read_tokens = _strict_count(cache_read_tokens, "cache_read_tokens")
        cache_write_tokens = _strict_count(cache_write_tokens, "cache_write_tokens")
        total_tokens = _strict_count(total_tokens, "total_tokens")
        expected_total = input_tokens + output_tokens if provider == "openai" else input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
        if total_tokens != expected_total:
            raise LLMUsageError(f"total_tokens mismatch for {provider or 'unknown'}: expected {expected_total}, got {total_tokens}")
        if isinstance(call_id, bool) or not isinstance(call_id, int) or call_id <= 0:
            raise LLMUsageError("call_id must be a positive integer")
        with self._lock:
            conn = self._connect_write()
            try:
                conn.execute("BEGIN IMMEDIATE")
                reservation = conn.execute("SELECT provider,model,status FROM llm_usage WHERE id=?", (call_id,)).fetchone()
                if reservation is None or reservation["status"] != "pending":
                    raise LLMUsageError("usage reservation is missing or already finalized")
                if reservation["provider"] != provider or reservation["model"] != model:
                    raise LLMUsageError(
                        f"usage reservation identity mismatch: expected {reservation['provider']}/{reservation['model']}"
                    )
                cost = self._cost(
                    provider,
                    model,
                    usage_reported=usage_reported,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                )
                cur = conn.execute(
                    """UPDATE llm_usage SET status='complete',usage_reported=?,input_tokens=?,output_tokens=?,
                       cache_read_tokens=?,cache_write_tokens=?,cache_tokens=?,total_tokens=?,cost_status=?,cost_reason=?,
                       cost_usd=?,pricing_sha256=?,pricing_json=?,provider_request_id=? WHERE id=? AND status='pending'""",
                    (
                        int(usage_reported), input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                        cache_read_tokens + cache_write_tokens, total_tokens, cost["status"], cost["reason"],
                        _decimal_text(cost["usd"]) if cost["usd"] is not None else None, cost["pricing_sha256"], cost["pricing_json"],
                        str(provider_request_id)[:160] if provider_request_id else None, call_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise LLMUsageError("usage reservation is missing or already finalized")
                conn.commit()
            except LLMUsageError:
                conn.rollback()
                raise
            except sqlite3.Error as exc:
                conn.rollback()
                raise LLMUsageError(f"LLM usage persistence failed: {type(exc).__name__}") from exc
            finally:
                conn.close()
        if self.cost_budget_usd is not None and cost["status"] != "available":
            raise LLMUsageError("cost budget cannot be enforced because response cost is unavailable")
        return {"cost_status": cost["status"], "cost_reason": cost["reason"], "cost_usd": _decimal_text(cost["usd"]) if cost["usd"] is not None else None}

    def fail_call(self, call_id: int, error: Exception) -> None:
        with self._lock:
            conn = self._connect_write()
            try:
                conn.execute(
                    "UPDATE llm_usage SET status='failed',cost_reason='request_failed_usage_unknown',error_type=? WHERE id=? AND status='pending'",
                    (type(error).__name__[:120], int(call_id)),
                )
                conn.commit()
            except sqlite3.Error as exc:
                raise LLMUsageError(f"LLM usage failure record could not be persisted: {type(exc).__name__}") from exc
            finally:
                conn.close()

    @staticmethod
    def _row_payload(row: sqlite3.Row) -> dict:
        return {
            "occurred_at": row["occurred_at"],
            "provider": row["provider"],
            "model": row["model"],
            "operation": row["operation"],
            "status": row["status"],
            "usage_reported": bool(row["usage_reported"]),
            "calls": int(row["calls"]),
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "cache_tokens": int(row["cache_tokens"]),
            "cache_read_tokens": int(row["cache_read_tokens"]),
            "cache_write_tokens": int(row["cache_write_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "cost": {
                "status": row["cost_status"],
                "currency": "USD",
                "usd": row["cost_usd"],
                "reason": row["cost_reason"],
                "pricing_sha256": row["pricing_sha256"],
            },
        }

    def payload(self, provider: str | None = None, model: str | None = None, recent_limit: int = 25) -> dict:
        now = self._now()
        with self._lock:
            conn = self._connect_read()
            try:
                rows = self._window_rows(conn, now)
                budget = self._budget_state(conn, provider, model, now)
                recent = list(conn.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT ?", (max(0, min(int(recent_limit), 100)),)))
            finally:
                conn.close()
        groups: dict[tuple[str, str, str], dict] = {}
        for row in rows:
            key = (row["provider"], row["model"], row["operation"])
            group = groups.setdefault(key, {"provider": key[0], "model": key[1], "operation": key[2], "calls": 0, "input_tokens": 0,
                                             "output_tokens": 0, "cache_tokens": 0, "total_tokens": 0, "known_cost_usd": Decimal(0), "unavailable_cost_calls": 0})
            group["calls"] += int(row["calls"])
            for field in ("input_tokens", "output_tokens", "cache_tokens", "total_tokens"):
                group[field] += int(row[field])
            if row["cost_status"] == "available" and row["cost_usd"] is not None:
                group["known_cost_usd"] += Decimal(row["cost_usd"])
            else:
                group["unavailable_cost_calls"] += int(row["calls"])
        grouped = []
        for group in sorted(groups.values(), key=lambda item: (item["provider"], item["model"], item["operation"])):
            group["known_cost_usd"] = _decimal_text(group["known_cost_usd"])
            group["cost_status"] = "available" if not group["unavailable_cost_calls"] else "unavailable"
            grouped.append(group)
        calls = sum(int(row["calls"]) for row in rows)
        known_cost = sum((Decimal(row["cost_usd"]) for row in rows if row["cost_status"] == "available" and row["cost_usd"] is not None), Decimal(0))
        unavailable_cost_calls = sum(int(row["calls"]) for row in rows if row["cost_status"] != "available")
        price, matched = self._price_entry(provider or "", model or "")
        cost_available = (calls > 0 and unavailable_cost_calls == 0) or (calls == 0 and price is not None)
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now.isoformat(),
            "persistence": {"status": "available", "backend": "sqlite"},
            "token_definition": "OpenAI cached input is a subset of input; Anthropic cache read/write is added to uncached input",
            "totals": {
                "calls": calls,
                "input_tokens": sum(int(row["input_tokens"]) for row in rows),
                "output_tokens": sum(int(row["output_tokens"]) for row in rows),
                "cache_tokens": sum(int(row["cache_tokens"]) for row in rows),
                "total_tokens": sum(int(row["total_tokens"]) for row in rows),
            },
            "cost": {
                "status": "available" if cost_available else "unavailable",
                "currency": "USD",
                "usd": _decimal_text(known_cost) if cost_available else None,
                "known_usd": _decimal_text(known_cost),
                "unavailable_calls": unavailable_cost_calls,
                "reason": None if cost_available else ("pricing_not_configured" if calls == 0 and price is None else "one_or_more_call_costs_unavailable"),
                "current_pricing_match": matched,
            },
            "budget": budget,
            "by_provider_model_operation": grouped,
            "recent": [self._row_payload(row) for row in recent],
        }
