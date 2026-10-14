"""Deterministic, local-only paper/shadow execution and accounting.

This module deliberately has no broker adapter and no network path.  It models
virtual fills, persists an append-only event ledger, and always reports
``NOT VALIDATED`` / ``NOT ACTIONABLE``.  Spread and slippage are embedded in the
fill price; commission is the only additional cash deduction.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
import os
import re
import shutil
import fcntl
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping


ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
ACCOUNTING_TOLERANCE = Decimal("0.00000001")
_GENESIS_HASH = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperError(RuntimeError):
    """Base class for fail-closed paper-engine errors."""


class ValidationError(PaperError):
    pass


class OrderRejected(PaperError):
    pass


class DuplicateOrderError(OrderRejected):
    pass


class QuoteRejected(OrderRejected):
    pass


class InsufficientCash(OrderRejected):
    pass


class MandateViolation(OrderRejected):
    pass


class KillSwitchActive(OrderRejected):
    pass


class CorporateActionUnsupported(PaperError):
    pass


class LedgerIntegrityError(PaperError):
    pass


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    SHADOW = "SHADOW"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> Decimal:
        return ONE if self is Side.BUY else -ONE


class OrderStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class CashEventKind(str, Enum):
    OPENING_BALANCE = "OPENING_BALANCE"
    TRADE_AND_COMMISSION = "TRADE_AND_COMMISSION"


class LedgerEventType(str, Enum):
    SESSION_STARTED = "SESSION_STARTED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    FILL = "FILL"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    MARK_TO_MARKET = "MARK_TO_MARKET"
    KILL_SWITCH = "KILL_SWITCH"
    CORPORATE_ACTION_BLOCKED = "CORPORATE_ACTION_BLOCKED"
    OPERATION_RECEIPT = "OPERATION_RECEIPT"


def _decimal(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValidationError(f"{name} must be finite")
    if positive and result <= ZERO:
        raise ValidationError(f"{name} must be positive")
    if nonnegative and result < ZERO:
        raise ValidationError(f"{name} must be nonnegative")
    return result


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _dt_text(value: datetime) -> str:
    return _aware(value, "timestamp").isoformat()


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _aware(parsed, "stored timestamp")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _identifier(prefix: str, payload: Any) -> str:
    return f"{prefix}_{sha256(_canonical(payload).encode('utf-8')).hexdigest()[:32]}"


@dataclass(frozen=True)
class CostModel:
    """Explicit virtual-fill costs, all in the quote currency.

    ``spread_bps`` is a minimum full bid/ask spread.  When an observed quote is
    wider, its spread wins. ``slippage_bps`` is applied beyond the selected
    touch.  This avoids double-counting the observed and configured spread.
    """

    commission_fixed: Decimal = ZERO
    commission_per_unit: Decimal = ZERO
    commission_bps: Decimal = ZERO
    spread_bps: Decimal = ZERO
    slippage_bps: Decimal = ZERO

    def __post_init__(self) -> None:
        for name in (
            "commission_fixed",
            "commission_per_unit",
            "commission_bps",
            "spread_bps",
            "slippage_bps",
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, nonnegative=True))

    def to_dict(self) -> dict[str, str]:
        return {name: _decimal_text(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CostModel":
        return cls(**{name: value.get(name, "0") for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class PaperMandate:
    starting_cash: Decimal = Decimal("100000")
    currency: str = "USD"
    allow_short: bool = False
    allow_margin: bool = False
    max_gross_leverage: Decimal = ONE
    max_quote_age: timedelta = timedelta(minutes=5)
    costs: CostModel = field(default_factory=CostModel)

    def __post_init__(self) -> None:
        object.__setattr__(self, "starting_cash", _decimal(self.starting_cash, "starting_cash", nonnegative=True))
        object.__setattr__(self, "max_gross_leverage", _decimal(self.max_gross_leverage, "max_gross_leverage", positive=True))
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValidationError("currency is required")
        currency = self.currency.strip().upper()
        if currency != "USD":
            raise ValidationError(
                "paper broker supports USD cash equities only; point-in-time FX accounting is unavailable"
            )
        object.__setattr__(self, "currency", currency)
        for name in ("allow_short", "allow_margin"):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"{name} must be a boolean")
        if self.allow_short:
            raise ValidationError(
                "short paper execution is unavailable until borrow/locate, financing, collateral, "
                "dividend-liability and segregated-proceeds accounting are implemented"
            )
        if self.allow_margin:
            raise ValidationError(
                "margin paper execution is unavailable until financing and collateral accounting are implemented"
            )
        if not isinstance(self.max_quote_age, timedelta) or self.max_quote_age < timedelta(0):
            raise ValidationError("max_quote_age must be a nonnegative timedelta")
        if not isinstance(self.costs, CostModel):
            raise ValidationError("costs must be a CostModel")

    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_cash": _decimal_text(self.starting_cash),
            "currency": self.currency,
            "allow_short": self.allow_short,
            "allow_margin": self.allow_margin,
            "max_gross_leverage": _decimal_text(self.max_gross_leverage),
            "max_quote_age_seconds": _decimal_text(Decimal(str(self.max_quote_age.total_seconds()))),
            "costs": self.costs.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PaperMandate":
        return cls(
            starting_cash=value["starting_cash"],
            currency=value["currency"],
            allow_short=value["allow_short"],
            allow_margin=value["allow_margin"],
            max_gross_leverage=value["max_gross_leverage"],
            max_quote_age=timedelta(seconds=float(value["max_quote_age_seconds"])),
            costs=CostModel.from_dict(value["costs"]),
        )


@dataclass(frozen=True)
class PaperSessionBinding:
    """Fixed book identity; runs may advance only inside this book."""

    workspace_id: str
    strategy_id: str
    portfolio_id: str

    def __post_init__(self) -> None:
        for name in ("workspace_id", "strategy_id", "portfolio_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"paper binding {name} is required")
            object.__setattr__(self, name, value.strip())

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "strategy_id": self.strategy_id,
            "portfolio_id": self.portfolio_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PaperSessionBinding":
        return cls(
            workspace_id=value["workspace_id"],
            strategy_id=value["strategy_id"],
            portfolio_id=value["portfolio_id"],
        )


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    available_quantity: Decimal | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol:
            raise ValidationError("quote symbol is required")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timestamp", _aware(self.timestamp, "quote timestamp"))
        object.__setattr__(self, "bid", _decimal(self.bid, "bid", positive=True))
        object.__setattr__(self, "ask", _decimal(self.ask, "ask", positive=True))
        if self.ask < self.bid:
            raise ValidationError("ask must be greater than or equal to bid")
        if self.available_quantity is not None:
            object.__setattr__(
                self,
                "available_quantity",
                _decimal(self.available_quantity, "available_quantity", nonnegative=True),
            )

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": _dt_text(self.timestamp),
            "bid": _decimal_text(self.bid),
            "ask": _decimal_text(self.ask),
            "available_quantity": None if self.available_quantity is None else _decimal_text(self.available_quantity),
        }


@dataclass(frozen=True)
class QuoteLineage:
    """Point-in-time evidence stored with every virtual fill and mark."""

    symbol: str
    observed_at: datetime
    acquired_at: datetime
    finality: str
    source: str
    content_hash: str
    instrument_id: str
    dataset_version_id: str
    request_hash: str
    currency: str = "USD"

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        source = self.source.strip() if isinstance(self.source, str) else ""
        finality = self.finality.strip().lower() if isinstance(self.finality, str) else ""
        digest = self.content_hash.strip().lower() if isinstance(self.content_hash, str) else ""
        instrument_id = self.instrument_id.strip() if isinstance(self.instrument_id, str) else ""
        version_id = (
            self.dataset_version_id.strip().lower()
            if isinstance(self.dataset_version_id, str)
            else ""
        )
        request_hash = (
            self.request_hash.strip().lower() if isinstance(self.request_hash, str) else ""
        )
        currency = self.currency.strip().upper() if isinstance(self.currency, str) else ""
        observed = _aware(self.observed_at, "quote lineage observed_at")
        acquired = _aware(self.acquired_at, "quote lineage acquired_at")
        if not symbol or not source:
            raise ValidationError("quote lineage symbol and source are required")
        if finality not in {"confirmed", "final"}:
            raise QuoteRejected("paper fills and marks require confirmed/final quote lineage")
        if not _SHA256_RE.fullmatch(digest):
            raise ValidationError("quote lineage content_hash must be a full SHA-256 digest")
        if not instrument_id or ":" not in instrument_id:
            raise ValidationError("quote lineage instrument_id is required")
        if not _SHA256_RE.fullmatch(version_id) or not _SHA256_RE.fullmatch(request_hash):
            raise ValidationError(
                "quote lineage dataset_version_id and request_hash must be full SHA-256 digests"
            )
        if len(currency) != 3 or not currency.isalpha():
            raise ValidationError("quote lineage currency must be a three-letter code")
        if observed > acquired:
            raise ValidationError("quote lineage observed_at cannot be after acquired_at")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "finality", finality)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "dataset_version_id", version_id)
        object.__setattr__(self, "request_hash", request_hash)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "acquired_at", acquired)

    def validate_quote(self, quote: Quote, *, available_at: datetime) -> None:
        available = _aware(available_at, "quote lineage available_at")
        if self.symbol != quote.symbol or self.observed_at != quote.timestamp:
            raise QuoteRejected("quote does not match its point-in-time lineage")
        if self.acquired_at > available:
            raise QuoteRejected("quote was not acquired by the recorded execution time")

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "observed_at": _dt_text(self.observed_at),
            "acquired_at": _dt_text(self.acquired_at),
            "finality": self.finality,
            "source": self.source,
            "content_hash": self.content_hash,
            "instrument_id": self.instrument_id,
            "dataset_version_id": self.dataset_version_id,
            "request_hash": self.request_hash,
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QuoteLineage":
        return cls(
            symbol=value["symbol"],
            observed_at=_dt(value["observed_at"]),
            acquired_at=_dt(value["acquired_at"]),
            finality=value["finality"],
            source=value["source"],
            content_hash=value["content_hash"],
            instrument_id=value["instrument_id"],
            dataset_version_id=value["dataset_version_id"],
            request_hash=value["request_hash"],
            currency=value["currency"],
        )


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol:
            raise ValidationError("bar symbol is required")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timestamp", _aware(self.timestamp, "bar timestamp"))
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=True))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.high < self.low:
            raise ValidationError("bar OHLC values are inconsistent")
        if self.volume is not None:
            object.__setattr__(self, "volume", _decimal(self.volume, "volume", nonnegative=True))

    def to_quote(self, spread_bps: Decimal = ZERO, available_quantity: Decimal | None = None) -> Quote:
        spread = _decimal(spread_bps, "spread_bps", nonnegative=True)
        half = self.close * spread / (Decimal("2") * BPS)
        return Quote(
            symbol=self.symbol,
            timestamp=self.timestamp,
            bid=self.close - half,
            ask=self.close + half,
            available_quantity=available_quantity,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": _dt_text(self.timestamp),
            "open": _decimal_text(self.open),
            "high": _decimal_text(self.high),
            "low": _decimal_text(self.low),
            "close": _decimal_text(self.close),
            "volume": None if self.volume is None else _decimal_text(self.volume),
        }


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side | str
    quantity: Decimal
    signal_time: datetime
    order_time: datetime
    run_id: str
    context_hash: str
    strategy_id: str = "default"
    client_order_id: str = ""
    order_id: str = ""
    mode: ExecutionMode | str = ExecutionMode.PAPER
    instrument_id: str = ""
    asset_class: str = ""
    currency: str = ""
    multiplier: Decimal = ZERO
    expiry: date | None = None
    contract_code: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        strategy = self.strategy_id.strip() if isinstance(self.strategy_id, str) else ""
        run_id = self.run_id.strip() if isinstance(self.run_id, str) else ""
        context_hash = self.context_hash.lower() if isinstance(self.context_hash, str) else ""
        if not symbol or not strategy or not run_id:
            raise ValidationError("order symbol, strategy_id and run_id are required")
        instrument_id = self.instrument_id.strip() if isinstance(self.instrument_id, str) else ""
        asset_class = self.asset_class.strip().lower() if isinstance(self.asset_class, str) else ""
        currency = self.currency.strip().upper() if isinstance(self.currency, str) else ""
        if not instrument_id or not asset_class or not currency:
            raise ValidationError(
                "order instrument_id, asset_class and currency are required; bare-symbol execution is disabled"
            )
        multiplier = _decimal(self.multiplier, "multiplier", positive=True)
        contract_code = (
            self.contract_code.strip()
            if isinstance(self.contract_code, str) and self.contract_code.strip()
            else None
        )
        if asset_class != "equity" or not instrument_id.lower().startswith("equity:"):
            raise ValidationError(
                "paper broker supports USD cash equities only; futures/options require expiry, contract "
                "identity, multiplier, settlement and variation-margin accounting"
            )
        if currency != "USD":
            raise ValidationError(
                "paper broker supports USD cash equities only; point-in-time FX accounting is unavailable"
            )
        if multiplier != ONE:
            raise ValidationError(
                "paper broker supports unit-multiplier cash equities only; contract multiplier accounting is unavailable"
            )
        if self.expiry is not None or contract_code is not None:
            raise ValidationError(
                "expiry/contract identity is invalid for the supported cash-equity execution path"
            )
        if not _SHA256_RE.fullmatch(context_hash):
            raise ValidationError("context_hash must be a full SHA-256 digest")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "strategy_id", strategy)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "context_hash", context_hash)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "multiplier", multiplier)
        object.__setattr__(self, "contract_code", contract_code)
        try:
            object.__setattr__(self, "side", self.side if isinstance(self.side, Side) else Side(str(self.side).upper()))
            object.__setattr__(self, "mode", self.mode if isinstance(self.mode, ExecutionMode) else ExecutionMode(str(self.mode).upper()))
        except ValueError as exc:
            raise ValidationError("only BUY/SELL in PAPER/SHADOW mode is supported") from exc
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        object.__setattr__(self, "signal_time", _aware(self.signal_time, "signal_time"))
        object.__setattr__(self, "order_time", _aware(self.order_time, "order_time"))
        if self.signal_time >= self.order_time:
            raise ValidationError("signal_time must be strictly before order_time")
        if self.client_order_id and not self.client_order_id.strip():
            raise ValidationError("client_order_id cannot be whitespace")

    def canonical_intent(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "run_id": self.run_id,
            "context_hash": self.context_hash,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": _decimal_text(self.quantity),
            "instrument_id": self.instrument_id,
            "asset_class": self.asset_class,
            "currency": self.currency,
            "multiplier": _decimal_text(self.multiplier),
            "expiry": None if self.expiry is None else self.expiry.isoformat(),
            "contract_code": self.contract_code,
            "signal_time": _dt_text(self.signal_time),
            "order_time": _dt_text(self.order_time),
            "mode": self.mode.value,
        }

    def with_ids(self) -> "Order":
        client_id = self.client_order_id or _identifier("coid", self.canonical_intent())
        expected_order_id = _identifier("ord", {"mode": self.mode.value, "client_order_id": client_id})
        if self.order_id and self.order_id != expected_order_id:
            raise ValidationError("order_id must be the deterministic id for client_order_id")
        return replace(self, client_order_id=client_id, order_id=expected_order_id)

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_intent(), "client_order_id": self.client_order_id, "order_id": self.order_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Order":
        return cls(
            symbol=value["symbol"], side=value["side"], quantity=value["quantity"],
            signal_time=_dt(value["signal_time"]), order_time=_dt(value["order_time"]),
            run_id=value["run_id"], context_hash=value["context_hash"],
            strategy_id=value["strategy_id"], client_order_id=value["client_order_id"],
            order_id=value["order_id"], mode=value["mode"],
            instrument_id=value["instrument_id"], asset_class=value["asset_class"],
            currency=value["currency"], multiplier=value["multiplier"],
            expiry=(None if value.get("expiry") is None else date.fromisoformat(value["expiry"])),
            contract_code=value.get("contract_code"),
        )


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    client_order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    reference_price: Decimal
    gross_notional: Decimal
    commission: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    total_cost: Decimal
    fill_time: datetime
    quote_time: datetime
    quote_id: str
    quote_available_quantity: Decimal | None
    quote_lineage: QuoteLineage
    mode: ExecutionMode

    def __post_init__(self) -> None:
        for name in ("fill_id", "order_id", "client_order_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"{name} is required")
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol:
            raise ValidationError("fill symbol is required")
        object.__setattr__(self, "symbol", symbol)
        try:
            object.__setattr__(self, "side", self.side if isinstance(self.side, Side) else Side(str(self.side).upper()))
            object.__setattr__(self, "mode", self.mode if isinstance(self.mode, ExecutionMode) else ExecutionMode(str(self.mode).upper()))
        except ValueError as exc:
            raise ValidationError("fill side/mode is invalid") from exc
        for name in ("quantity", "price", "reference_price", "gross_notional"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=True))
        for name in ("commission", "spread_cost", "slippage_cost", "total_cost"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, nonnegative=True))
        object.__setattr__(self, "fill_time", _aware(self.fill_time, "fill_time"))
        object.__setattr__(self, "quote_time", _aware(self.quote_time, "quote_time"))
        if self.quote_time > self.fill_time:
            raise ValidationError("fill quote_time cannot be after fill_time")
        if not isinstance(self.quote_id, str) or not self.quote_id.strip():
            raise ValidationError("quote_id is required")
        if self.quote_available_quantity is not None:
            available = _decimal(
                self.quote_available_quantity, "quote_available_quantity", nonnegative=True
            )
            if self.quantity > available:
                raise ValidationError("fill quantity exceeds quote available quantity")
            object.__setattr__(self, "quote_available_quantity", available)
        if not isinstance(self.quote_lineage, QuoteLineage):
            raise ValidationError("fill quote_lineage is required")
        if (
            self.quote_lineage.symbol != self.symbol
            or self.quote_lineage.observed_at != self.quote_time
            or self.quote_lineage.acquired_at > self.fill_time
        ):
            raise ValidationError("fill does not reconcile to quote lineage")
        if abs(self.gross_notional - self.quantity * self.price) > ACCOUNTING_TOLERANCE:
            raise ValidationError("fill gross_notional does not equal quantity times price")
        if abs(self.total_cost - (self.commission + self.spread_cost + self.slippage_cost)) > ACCOUNTING_TOLERANCE:
            raise ValidationError("fill total_cost does not reconcile")
        execution_cost = abs(self.price - self.reference_price) * self.quantity
        if abs(execution_cost - (self.spread_cost + self.slippage_cost)) > ACCOUNTING_TOLERANCE:
            raise ValidationError("fill execution price does not reconcile to spread and slippage")
        if self.side is Side.BUY and self.price < self.reference_price:
            raise ValidationError("buy fill price cannot be below its reference price")
        if self.side is Side.SELL and self.price > self.reference_price:
            raise ValidationError("sell fill price cannot be above its reference price")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id, "order_id": self.order_id,
            "client_order_id": self.client_order_id, "symbol": self.symbol,
            "side": self.side.value, "quantity": _decimal_text(self.quantity),
            "price": _decimal_text(self.price), "reference_price": _decimal_text(self.reference_price),
            "gross_notional": _decimal_text(self.gross_notional),
            "commission": _decimal_text(self.commission), "spread_cost": _decimal_text(self.spread_cost),
            "slippage_cost": _decimal_text(self.slippage_cost), "total_cost": _decimal_text(self.total_cost),
            "fill_time": _dt_text(self.fill_time), "quote_time": _dt_text(self.quote_time),
            "quote_id": self.quote_id,
            "quote_available_quantity": (
                None
                if self.quote_available_quantity is None
                else _decimal_text(self.quote_available_quantity)
            ),
            "quote_lineage": self.quote_lineage.to_dict(),
            "mode": self.mode.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Fill":
        decimal_names = ("quantity", "price", "reference_price", "gross_notional", "commission", "spread_cost", "slippage_cost", "total_cost")
        values = {name: Decimal(value[name]) for name in decimal_names}
        return cls(
            fill_id=value["fill_id"], order_id=value["order_id"], client_order_id=value["client_order_id"],
            symbol=value["symbol"], side=Side(value["side"]), fill_time=_dt(value["fill_time"]),
            quote_time=_dt(value["quote_time"]), quote_id=value["quote_id"],
            quote_available_quantity=(
                None
                if value.get("quote_available_quantity") is None
                else Decimal(value["quote_available_quantity"])
            ),
            quote_lineage=QuoteLineage.from_dict(value["quote_lineage"]),
            mode=ExecutionMode(value["mode"]), **values,
        )


@dataclass(frozen=True)
class CashEvent:
    cash_event_id: str
    kind: CashEventKind
    timestamp: datetime
    amount: Decimal
    currency: str
    related_id: str
    balance_after: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.cash_event_id, str) or not self.cash_event_id.strip():
            raise ValidationError("cash_event_id is required")
        try:
            object.__setattr__(self, "kind", self.kind if isinstance(self.kind, CashEventKind) else CashEventKind(self.kind))
        except ValueError as exc:
            raise ValidationError("cash event kind is invalid") from exc
        object.__setattr__(self, "timestamp", _aware(self.timestamp, "cash event timestamp"))
        object.__setattr__(self, "amount", _decimal(self.amount, "cash event amount"))
        object.__setattr__(self, "balance_after", _decimal(self.balance_after, "cash event balance_after"))
        currency = self.currency.strip().upper() if isinstance(self.currency, str) else ""
        if not currency or not isinstance(self.related_id, str) or not self.related_id.strip():
            raise ValidationError("cash event currency and related_id are required")
        object.__setattr__(self, "currency", currency)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash_event_id": self.cash_event_id, "kind": self.kind.value,
            "timestamp": _dt_text(self.timestamp), "amount": _decimal_text(self.amount),
            "currency": self.currency, "related_id": self.related_id,
            "balance_after": _decimal_text(self.balance_after),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CashEvent":
        return cls(
            cash_event_id=value["cash_event_id"], kind=CashEventKind(value["kind"]),
            timestamp=_dt(value["timestamp"]), amount=Decimal(value["amount"]),
            currency=value["currency"], related_id=value["related_id"],
            balance_after=Decimal(value["balance_after"]),
        )


@dataclass
class Position:
    symbol: str
    quantity: Decimal = ZERO
    average_cost: Decimal = ZERO
    realized_pnl: Decimal = ZERO

    def to_dict(self, mark: Decimal | None = None) -> dict[str, Any]:
        unrealized = None
        market_value = None
        if mark is not None:
            market_value = self.quantity * mark
            if self.quantity >= ZERO:
                unrealized = self.quantity * (mark - self.average_cost)
            else:
                unrealized = (-self.quantity) * (self.average_cost - mark)
        return {
            "symbol": self.symbol, "quantity": _decimal_text(self.quantity),
            "average_cost": _decimal_text(self.average_cost), "realized_pnl": _decimal_text(self.realized_pnl),
            "mark": None if mark is None else _decimal_text(mark),
            "market_value": None if market_value is None else _decimal_text(market_value),
            "unrealized_pnl": None if unrealized is None else _decimal_text(unrealized),
        }


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    event_id: str
    event_type: LedgerEventType
    timestamp: datetime
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str

    def hash_body(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence, "event_id": self.event_id, "event_type": self.event_type.value,
            "timestamp": _dt_text(self.timestamp), "payload": self.payload, "previous_hash": self.previous_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.hash_body(), "event_hash": self.event_hash}

    @classmethod
    def create(
        cls, sequence: int, event_id: str, event_type: LedgerEventType,
        timestamp: datetime, payload: dict[str, Any], previous_hash: str,
    ) -> "LedgerEvent":
        partial = cls(sequence, event_id, event_type, _aware(timestamp, "event timestamp"), payload, previous_hash, "")
        return replace(partial, event_hash=sha256(_canonical(partial.hash_body()).encode("utf-8")).hexdigest())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LedgerEvent":
        event = cls(
            sequence=int(value["sequence"]), event_id=value["event_id"],
            event_type=LedgerEventType(value["event_type"]), timestamp=_dt(value["timestamp"]),
            payload=dict(value["payload"]), previous_hash=value["previous_hash"], event_hash=value["event_hash"],
        )
        expected = sha256(_canonical(event.hash_body()).encode("utf-8")).hexdigest()
        if event.event_hash != expected:
            raise LedgerIntegrityError(f"event hash mismatch at sequence {event.sequence}")
        return event


class JsonlPaperStore:
    """Append-only, hash-checked JSONL storage at an explicitly injected path."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.head_path = self.path.with_name(self.path.name + ".head")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.txn_path = self.path.with_name(self.path.name + ".txn")
        self.txn_old_path = self.path.with_name(self.path.name + ".txn.old")
        self.txn_old_head = self.path.with_name(self.path.name + ".head.txn.old")
        self.txn_new_path = self.path.with_name(self.path.name + ".txn.new")
        self.txn_new_head = self.path.with_name(self.path.name + ".head.txn.new")
        if self.path.exists() and self.path.is_dir():
            raise ValidationError("paper store path must be a file, not a directory")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @contextmanager
    def _file_lock(self):
        with self.lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _read_only_file_lock(self):
        """Share-lock an existing canonical store without creating sidecars."""

        if self.lock_path.is_symlink() or not self.lock_path.is_file():
            raise LedgerIntegrityError("paper ledger lock checkpoint is missing or unsafe")
        with self.lock_path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _copy_durable(source: Path, target: Path) -> None:
        with source.open("rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())

    @staticmethod
    def _write_durable(path: Path, payload: str) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_events_unlocked(self) -> list[LedgerEvent]:
        if not self.path.exists():
            if self.head_path.exists():
                raise LedgerIntegrityError("ledger head exists without its event file")
            return []
        events: list[LedgerEvent] = []
        previous_hash = _GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                try:
                    event = LedgerEvent.from_dict(json.loads(raw))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    raise LedgerIntegrityError(f"invalid ledger event on line {line_number}") from exc
                if event.sequence != len(events) + 1:
                    raise LedgerIntegrityError(f"non-contiguous sequence on line {line_number}")
                if event.previous_hash != previous_hash:
                    raise LedgerIntegrityError(f"broken hash chain on line {line_number}")
                events.append(event)
                previous_hash = event.event_hash
        if events:
            try:
                checkpoint = json.loads(self.head_path.read_text(encoding="utf-8"))
                head_sequence = int(checkpoint["sequence"])
                head_hash = str(checkpoint["event_hash"])
            except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise LedgerIntegrityError("ledger external head checkpoint is missing or invalid") from exc
            if head_sequence != len(events) or head_hash != events[-1].event_hash:
                raise LedgerIntegrityError("ledger tail does not match its external head checkpoint")
        elif self.head_path.exists():
            raise LedgerIntegrityError("empty ledger cannot have an external head checkpoint")
        return events

    @staticmethod
    def _matches(events: list[LedgerEvent], sequence: int, event_hash: str) -> bool:
        if sequence == 0:
            return not events and event_hash == _GENESIS_HASH
        return bool(events) and len(events) == sequence and events[-1].event_hash == event_hash

    def _cleanup_transaction_unlocked(self) -> None:
        for path in (
            self.txn_path,
            self.txn_old_path,
            self.txn_old_head,
            self.txn_new_path,
            self.txn_new_head,
        ):
            path.unlink(missing_ok=True)

    def _recover_transaction_unlocked(self) -> None:
        if not self.txn_path.exists():
            return
        try:
            journal = json.loads(self.txn_path.read_text(encoding="utf-8"))
            old_sequence = int(journal["old_sequence"])
            old_hash = str(journal["old_hash"])
            new_sequence = int(journal["new_sequence"])
            new_hash = str(journal["new_hash"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise LedgerIntegrityError("paper ledger recovery journal is invalid") from exc

        try:
            current = self._read_events_unlocked()
        except LedgerIntegrityError:
            current = []
        if self._matches(current, new_sequence, new_hash):
            self._cleanup_transaction_unlocked()
            return
        if old_sequence == 0:
            self.path.unlink(missing_ok=True)
            self.head_path.unlink(missing_ok=True)
            self._cleanup_transaction_unlocked()
            return
        if not self.txn_old_path.exists() or not self.txn_old_head.exists():
            raise LedgerIntegrityError("paper ledger transaction cannot be recovered")
        restore_ledger = self.path.with_name(self.path.name + ".restore")
        restore_head = self.head_path.with_name(self.head_path.name + ".restore")
        self._copy_durable(self.txn_old_path, restore_ledger)
        self._copy_durable(self.txn_old_head, restore_head)
        os.replace(restore_ledger, self.path)
        os.replace(restore_head, self.head_path)
        restored = self._read_events_unlocked()
        if not self._matches(restored, old_sequence, old_hash):
            raise LedgerIntegrityError("paper ledger rollback did not restore the expected head")
        self._cleanup_transaction_unlocked()

    def read_events(self, *, recover: bool = True) -> list[LedgerEvent]:
        if recover:
            with self._lock, self._file_lock():
                self._recover_transaction_unlocked()
                return self._read_events_unlocked()
        with self._lock, self._read_only_file_lock():
            if self.txn_path.exists():
                raise LedgerIntegrityError(
                    "paper ledger requires recovery; read-only inspection cannot mutate state"
                )
            return self._read_events_unlocked()

    def _commit_generation_unlocked(
        self,
        source_path: Path,
        source_head: Path,
        current: list[LedgerEvent],
        proposed: list[LedgerEvent],
    ) -> None:
        if current:
            self._copy_durable(self.path, self.txn_old_path)
            self._copy_durable(self.head_path, self.txn_old_head)
        else:
            self.txn_old_path.unlink(missing_ok=True)
            self.txn_old_head.unlink(missing_ok=True)
        self._copy_durable(source_path, self.txn_new_path)
        self._copy_durable(source_head, self.txn_new_head)
        journal = _canonical(
                {
                    "old_sequence": len(current),
                    "old_hash": current[-1].event_hash if current else _GENESIS_HASH,
                "new_sequence": len(proposed),
                "new_hash": proposed[-1].event_hash,
            }
        ) + "\n"
        self._write_durable(self.txn_path, journal)
        try:
            os.replace(self.txn_new_path, self.path)
            os.replace(self.txn_new_head, self.head_path)
            committed = self._read_events_unlocked()
            if not self._matches(committed, len(proposed), proposed[-1].event_hash):
                raise LedgerIntegrityError("committed paper generation failed verification")
        except Exception:
            self._recover_transaction_unlocked()
            raise
        self._cleanup_transaction_unlocked()

    def append(self, event: LedgerEvent) -> None:
        with self._lock, self._file_lock():
            self._recover_transaction_unlocked()
            existing = self._read_events_unlocked()
            expected_sequence = len(existing) + 1
            expected_previous = existing[-1].event_hash if existing else _GENESIS_HASH
            if event.sequence != expected_sequence or event.previous_hash != expected_previous:
                raise LedgerIntegrityError("append does not continue the current ledger")
            source_path = self.path.with_name(self.path.name + ".append")
            source_head = self.head_path.with_name(self.head_path.name + ".append")
            try:
                if existing:
                    self._copy_durable(self.path, source_path)
                with source_path.open("a" if existing else "w", encoding="utf-8") as handle:
                    handle.write(_canonical(event.to_dict()) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                self._write_durable(
                    source_head,
                    _canonical({"sequence": event.sequence, "event_hash": event.event_hash}) + "\n",
                )
                self._commit_generation_unlocked(
                    source_path, source_head, existing, [*existing, event]
                )
            finally:
                source_path.unlink(missing_ok=True)
                source_head.unlink(missing_ok=True)

    def replace_from(
        self,
        staged: "JsonlPaperStore",
        *,
        expected_sequence: int,
        expected_hash: str,
    ) -> None:
        """CAS-replace a complete staged generation with crash recovery."""

        staged_events = staged.read_events()
        if not staged_events:
            raise LedgerIntegrityError("staged paper ledger is empty")
        with self._lock, self._file_lock():
            self._recover_transaction_unlocked()
            current = self._read_events_unlocked()
            if not self._matches(current, expected_sequence, expected_hash):
                raise LedgerIntegrityError("paper ledger changed concurrently; staged batch was not committed")
            if len(staged_events) < len(current) or any(
                left.event_hash != right.event_hash
                for left, right in zip(current, staged_events)
            ):
                raise LedgerIntegrityError("staged ledger does not extend the committed ledger")

            self._commit_generation_unlocked(
                staged.path, staged.head_path, current, staged_events
            )


@dataclass
class _OrderRecord:
    order: Order
    filled_quantity: Decimal = ZERO
    status: OrderStatus = OrderStatus.ACCEPTED

    @property
    def remaining_quantity(self) -> Decimal:
        return self.order.quantity - self.filled_quantity

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.order.to_dict(), "status": self.status.value,
            "filled_quantity": _decimal_text(self.filled_quantity),
            "remaining_quantity": _decimal_text(self.remaining_quantity),
        }


class PaperBroker:
    """High-level local paper/shadow broker with deterministic recovery."""

    validation_status = "NOT VALIDATED"
    actionability = "NOT ACTIONABLE"

    def __init__(
        self,
        store_path: str | Path,
        *,
        mode: ExecutionMode | str | None = None,
        mandate: PaperMandate | None = None,
        binding: PaperSessionBinding | None = None,
        session_id: str | None = None,
        opened_at: datetime | None = None,
        creation_operation_id: str | None = None,
        creation_request_sha256: str | None = None,
        _require_existing: bool = False,
        _recover_store: bool = True,
    ):
        self.store = JsonlPaperStore(store_path)
        self._lock = RLock()
        events = self.store.read_events(recover=_recover_store)
        if events:
            first = events[0]
            if first.event_type is not LedgerEventType.SESSION_STARTED:
                raise LedgerIntegrityError("first event must start a paper session")
            persisted_mode = ExecutionMode(first.payload["mode"])
            persisted_mandate = PaperMandate.from_dict(first.payload["mandate"])
            persisted_binding = (
                None
                if first.payload.get("binding") is None
                else PaperSessionBinding.from_dict(first.payload["binding"])
            )
            persisted_session = first.payload["session_id"]
            persisted_creation_operation = first.payload.get("creation_operation_id")
            persisted_creation_hash = first.payload.get("creation_request_sha256")
            if mode is not None and ExecutionMode(str(mode).upper() if not isinstance(mode, ExecutionMode) else mode) is not persisted_mode:
                raise ValidationError("requested mode differs from persisted session")
            if mandate is not None and mandate.to_dict() != persisted_mandate.to_dict():
                raise ValidationError("requested mandate differs from persisted session")
            if binding is not None and binding != persisted_binding:
                raise ValidationError("requested binding differs from persisted session")
            if session_id is not None and session_id != persisted_session:
                raise ValidationError("requested session_id differs from persisted session")
            if (
                creation_operation_id is not None
                and creation_operation_id != persisted_creation_operation
            ):
                raise ValidationError("creation operation differs from persisted session")
            if (
                creation_request_sha256 is not None
                and creation_request_sha256 != persisted_creation_hash
            ):
                raise ValidationError("creation request differs from persisted session")
            self.mode, self.mandate, self.binding, self.session_id = (
                persisted_mode,
                persisted_mandate,
                persisted_binding,
                persisted_session,
            )
            self.creation_operation_id = persisted_creation_operation
            self.creation_request_sha256 = persisted_creation_hash
        else:
            if _require_existing:
                raise LedgerIntegrityError("paper ledger is empty or its initial transaction was not committed")
            try:
                self.mode = ExecutionMode.PAPER if mode is None else (mode if isinstance(mode, ExecutionMode) else ExecutionMode(str(mode).upper()))
            except ValueError as exc:
                raise ValidationError("execution mode must be PAPER or SHADOW; LIVE is impossible") from exc
            self.mandate = mandate or PaperMandate()
            if binding is not None and not isinstance(binding, PaperSessionBinding):
                raise ValidationError("binding must be a PaperSessionBinding")
            self.binding = binding
            if (creation_operation_id is None) != (creation_request_sha256 is None):
                raise ValidationError("creation operation id and request hash must be supplied together")
            if creation_operation_id is not None:
                if not isinstance(creation_operation_id, str) or not creation_operation_id.strip():
                    raise ValidationError("creation_operation_id is required")
                if not _SHA256_RE.fullmatch(str(creation_request_sha256).lower()):
                    raise ValidationError("creation_request_sha256 must be a full SHA-256 digest")
                creation_operation_id = creation_operation_id.strip()
                creation_request_sha256 = str(creation_request_sha256).lower()
            self.creation_operation_id = creation_operation_id
            self.creation_request_sha256 = creation_request_sha256
            self.session_id = session_id or _identifier("paper", {
                "path": str(self.store.path.resolve()), "mode": self.mode.value,
                "opened_at": _dt_text(opened_at or datetime.now(timezone.utc)),
            })

        self.events: list[LedgerEvent] = []
        self.orders: dict[str, _OrderRecord] = {}
        self._client_orders: dict[str, str] = {}
        self.fills: list[Fill] = []
        self.cash_events: list[CashEvent] = []
        self.positions: dict[str, Position] = {}
        self.marks: dict[str, Decimal] = {}
        self.mark_times: dict[str, datetime] = {}
        self.cash = ZERO
        self.total_commission = ZERO
        self.total_spread_cost = ZERO
        self.total_slippage_cost = ZERO
        self.kill_switch_active = False
        self.kill_switch_reason: str | None = None
        self.operation_receipts: dict[str, dict[str, Any]] = {}
        self._event_ids: dict[str, LedgerEvent] = {}

        if events:
            for event in events:
                self._apply(event)
        else:
            event_time = opened_at or datetime.now(timezone.utc)
            _aware(event_time, "opened_at")
            cash_event = CashEvent(
                cash_event_id=_identifier("cash", {"session_id": self.session_id, "kind": "opening"}),
                kind=CashEventKind.OPENING_BALANCE, timestamp=event_time,
                amount=self.mandate.starting_cash, currency=self.mandate.currency,
                related_id=self.session_id, balance_after=self.mandate.starting_cash,
            )
            self._append(
                LedgerEventType.SESSION_STARTED, event_time,
                {"session_id": self.session_id, "mode": self.mode.value,
                 "validation_status": self.validation_status, "actionability": self.actionability,
                 "mandate": self.mandate.to_dict(),
                 "binding": None if self.binding is None else self.binding.to_dict(),
                 "creation_operation_id": self.creation_operation_id,
                 "creation_request_sha256": self.creation_request_sha256,
                 "cash_event": cash_event.to_dict()},
                idempotency_key=self.session_id,
            )

    @classmethod
    def start_session(cls, store_path: str | Path, **kwargs: Any) -> "PaperBroker":
        return cls(store_path, **kwargs)

    @classmethod
    def replay(cls, store_path: str | Path) -> "PaperBroker":
        return cls(
            store_path,
            mode=None,
            mandate=None,
            binding=None,
            session_id=None,
            _require_existing=True,
        )

    @classmethod
    def inspect(cls, store_path: str | Path) -> "PaperBroker":
        """Replay an already consistent ledger without recovery or file creation."""

        return cls(
            store_path,
            mode=None,
            mandate=None,
            binding=None,
            session_id=None,
            _require_existing=True,
            _recover_store=False,
        )

    def _append(
        self, event_type: LedgerEventType, timestamp: datetime, payload: dict[str, Any],
        *, idempotency_key: str,
    ) -> LedgerEvent:
        event_id = _identifier("evt", {"session_id": self.session_id, "type": event_type.value, "key": idempotency_key})
        if event_id in self._event_ids:
            existing = self._event_ids[event_id]
            if existing.event_type is not event_type or existing.payload != payload:
                raise LedgerIntegrityError("idempotency key reused for a different event")
            return existing
        if self.events and timestamp < self.events[-1].timestamp:
            raise ValidationError("ledger events must be appended in nondecreasing timestamp order")
        previous_hash = self.events[-1].event_hash if self.events else _GENESIS_HASH
        event = LedgerEvent.create(len(self.events) + 1, event_id, event_type, timestamp, payload, previous_hash)
        self.store.append(event)
        self._apply(event)
        return event

    def _apply(self, event: LedgerEvent) -> None:
        if event.event_id in self._event_ids:
            raise LedgerIntegrityError(f"duplicate event id {event.event_id}")
        if event.sequence != len(self.events) + 1:
            raise LedgerIntegrityError("events must replay in sequence")
        if self.events and event.timestamp < self.events[-1].timestamp:
            raise LedgerIntegrityError("ledger event timestamps move backwards")
        if event.event_type is LedgerEventType.SESSION_STARTED:
            cash_event = CashEvent.from_dict(event.payload["cash_event"])
            expected_cash_id = _identifier(
                "cash", {"session_id": self.session_id, "kind": "opening"}
            )
            if (
                cash_event.cash_event_id != expected_cash_id
                or cash_event.kind is not CashEventKind.OPENING_BALANCE
                or cash_event.timestamp != event.timestamp
                or cash_event.amount != self.mandate.starting_cash
                or cash_event.balance_after != self.mandate.starting_cash
                or cash_event.currency != self.mandate.currency
                or cash_event.related_id != self.session_id
            ):
                raise LedgerIntegrityError("opening cash event does not match the paper session")
            self.cash = cash_event.balance_after
            self.cash_events.append(cash_event)
        elif event.event_type is LedgerEventType.ORDER_ACCEPTED:
            order = Order.from_dict(event.payload["order"])
            if order.mode is not self.mode:
                raise LedgerIntegrityError("order mode differs from session mode")
            if order.order_time != event.timestamp:
                raise LedgerIntegrityError("order event time differs from order_time")
            if self.binding is not None and order.strategy_id != self.binding.strategy_id:
                raise LedgerIntegrityError("order strategy differs from the fixed session binding")
            identity = (order.instrument_id, order.asset_class, order.currency, order.multiplier)
            if any(
                record.order.symbol == order.symbol
                and (
                    record.order.instrument_id,
                    record.order.asset_class,
                    record.order.currency,
                    record.order.multiplier,
                )
                != identity
                for record in self.orders.values()
            ):
                raise LedgerIntegrityError(
                    "paper symbol maps to multiple immutable instrument identities"
                )
            if order.order_id in self.orders or order.client_order_id in self._client_orders:
                raise LedgerIntegrityError("order identity is duplicated in the ledger")
            self.orders[order.order_id] = _OrderRecord(order)
            self._client_orders[order.client_order_id] = order.order_id
        elif event.event_type is LedgerEventType.FILL:
            fill = Fill.from_dict(event.payload["fill"])
            cash_event = CashEvent.from_dict(event.payload["cash_event"])
            record = self.orders.get(fill.order_id)
            if record is None or fill.quantity > record.remaining_quantity:
                raise LedgerIntegrityError("fill does not match an open order")
            order = record.order
            expected_commission = (
                self.mandate.costs.commission_fixed
                + self.mandate.costs.commission_per_unit * fill.quantity
                + fill.gross_notional * self.mandate.costs.commission_bps / BPS
            )
            expected_cash_delta = (
                -(fill.gross_notional + fill.commission)
                if fill.side is Side.BUY
                else fill.gross_notional - fill.commission
            )
            expected_cash_id = _identifier("cash", {"fill_id": fill.fill_id})
            if (
                fill.client_order_id != order.client_order_id
                or fill.symbol != order.symbol
                or fill.side is not order.side
                or fill.mode is not self.mode
                or fill.fill_time != event.timestamp
                or fill.fill_time < order.order_time
                or fill.quote_lineage.instrument_id != order.instrument_id
                or fill.quote_lineage.currency != order.currency
                or abs(fill.commission - expected_commission) > ACCOUNTING_TOLERANCE
                or cash_event.cash_event_id != expected_cash_id
                or cash_event.kind is not CashEventKind.TRADE_AND_COMMISSION
                or cash_event.timestamp != fill.fill_time
                or cash_event.currency != self.mandate.currency
                or cash_event.related_id != fill.fill_id
                or abs(cash_event.amount - expected_cash_delta) > ACCOUNTING_TOLERANCE
                or abs(cash_event.balance_after - (self.cash + expected_cash_delta))
                > ACCOUNTING_TOLERANCE
            ):
                raise LedgerIntegrityError("fill, order and cash event do not reconcile")
            if any(existing.fill_id == fill.fill_id for existing in self.fills):
                raise LedgerIntegrityError("fill identity is duplicated in the ledger")
            consumed = sum(
                (existing.quantity for existing in self.fills if existing.quote_id == fill.quote_id),
                ZERO,
            )
            if (
                fill.quote_available_quantity is not None
                and consumed + fill.quantity > fill.quote_available_quantity
            ):
                raise LedgerIntegrityError("fills exceed the quote available quantity")
            self._apply_position(fill)
            record.filled_quantity += fill.quantity
            record.status = OrderStatus.FILLED if record.remaining_quantity == ZERO else OrderStatus.PARTIALLY_FILLED
            self.cash += cash_event.amount
            if abs(self.cash - cash_event.balance_after) > ACCOUNTING_TOLERANCE:
                raise LedgerIntegrityError("cash event does not reconcile")
            self.cash_events.append(cash_event)
            self.fills.append(fill)
            self.total_commission += fill.commission
            self.total_spread_cost += fill.spread_cost
            self.total_slippage_cost += fill.slippage_cost
            self.marks[fill.symbol] = fill.reference_price
            self.mark_times[fill.symbol] = fill.quote_time
        elif event.event_type is LedgerEventType.ORDER_CANCELLED:
            record = self.orders.get(event.payload["order_id"])
            if record is None or record.status is OrderStatus.FILLED:
                raise LedgerIntegrityError("cancel event does not match an open order")
            record.status = OrderStatus.CANCELLED
        elif event.event_type is LedgerEventType.MARK_TO_MARKET:
            for item in event.payload["marks"]:
                symbol = item["symbol"]
                quote_time = _dt(item["quote_time"])
                lineage = QuoteLineage.from_dict(item["quote_lineage"])
                price = _decimal(item["price"], "mark price", positive=True)
                identities = {
                    (record.order.instrument_id, record.order.currency)
                    for record in self.orders.values()
                    if record.order.symbol == symbol
                }
                if (
                    lineage.symbol != symbol
                    or lineage.observed_at != quote_time
                    or lineage.acquired_at > event.timestamp
                    or quote_time > event.timestamp
                    or identities != {(lineage.instrument_id, lineage.currency)}
                ):
                    raise LedgerIntegrityError("mark does not match its quote lineage")
                self.marks[symbol] = price
                self.mark_times[symbol] = quote_time
        elif event.event_type in (LedgerEventType.KILL_SWITCH, LedgerEventType.CORPORATE_ACTION_BLOCKED):
            self.kill_switch_active = True
            self.kill_switch_reason = event.payload["reason"]
        elif event.event_type is LedgerEventType.OPERATION_RECEIPT:
            operation_id = event.payload.get("operation_id")
            action = event.payload.get("action")
            request_hash = event.payload.get("request_sha256")
            result = event.payload.get("result")
            if (
                not isinstance(operation_id, str)
                or not operation_id.strip()
                or not isinstance(action, str)
                or not action.strip()
                or not isinstance(request_hash, str)
                or not _SHA256_RE.fullmatch(request_hash)
                or not isinstance(result, Mapping)
            ):
                raise LedgerIntegrityError("operation receipt is malformed")
            canonical_result = json.loads(_canonical(dict(result)))
            existing = self.operation_receipts.get(operation_id)
            receipt = {
                "operation_id": operation_id,
                "action": action,
                "request_sha256": request_hash,
                "result": canonical_result,
                "receipt_sequence": event.sequence,
                "receipt_event_hash": event.event_hash,
                "receipt_timestamp": _dt_text(event.timestamp),
                "receipt_ordinal": len(self.operation_receipts) + 1,
            }
            if existing is not None and existing != receipt:
                raise LedgerIntegrityError("operation id was reused for a different paper mutation")
            self.operation_receipts[operation_id] = receipt
        self.events.append(event)
        self._event_ids[event.event_id] = event

    def operation_result(
        self, operation_id: str, *, action: str, request_sha256: str
    ) -> dict[str, Any] | None:
        receipt = self.operation_receipts.get(operation_id)
        if receipt is None:
            return None
        if receipt["action"] != action or receipt["request_sha256"] != request_sha256:
            raise LedgerIntegrityError("operation id was reused with a different action or payload")
        result = json.loads(_canonical(receipt["result"]))
        session = result.get("session")
        if isinstance(session, dict):
            session["event_count"] = receipt["receipt_sequence"]
            session["operation_receipt_count"] = receipt["receipt_ordinal"]
            session["ledger_head"] = {
                "sequence": receipt["receipt_sequence"],
                "event_hash": receipt["receipt_event_hash"],
                "timestamp": receipt["receipt_timestamp"],
                "checkpoint_path": self.store.head_path.name,
            }
        result["operation_receipt"] = {
            "sequence": receipt["receipt_sequence"],
            "event_hash": receipt["receipt_event_hash"],
            "timestamp": receipt["receipt_timestamp"],
        }
        return result

    def record_operation(
        self,
        operation_id: str,
        *,
        action: str,
        request_sha256: str,
        result: Mapping[str, Any],
        recorded_at: datetime,
    ) -> dict[str, Any]:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValidationError("operation_id is required")
        if not isinstance(action, str) or not action.strip():
            raise ValidationError("operation action is required")
        digest = str(request_sha256).lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValidationError("request_sha256 must be a full SHA-256 digest")
        try:
            canonical_result = json.loads(_canonical(dict(result)))
        except (TypeError, ValueError) as exc:
            raise ValidationError("operation result must be canonical JSON") from exc
        existing_receipt = self.operation_receipts.get(operation_id)
        if existing_receipt is not None:
            if (
                existing_receipt["action"] != action
                or existing_receipt["request_sha256"] != digest
            ):
                raise LedgerIntegrityError(
                    "operation id was reused with a different action or payload"
                )
            if existing_receipt["result"] != canonical_result:
                raise LedgerIntegrityError("operation result conflicts with its persisted receipt")
            return self.operation_result(
                operation_id, action=action, request_sha256=digest
            )
        payload = {
            "operation_id": operation_id.strip(),
            "action": action.strip(),
            "request_sha256": digest,
            "result": canonical_result,
        }
        self._append(
            LedgerEventType.OPERATION_RECEIPT,
            recorded_at,
            payload,
            idempotency_key=f"operation:{operation_id}",
        )
        return canonical_result

    def _apply_position(self, fill: Fill) -> None:
        position = self.positions.setdefault(fill.symbol, Position(fill.symbol))
        old_quantity = position.quantity
        signed = fill.side.sign * fill.quantity
        new_quantity = old_quantity + signed
        if old_quantity == ZERO or old_quantity * signed > ZERO:
            total_units = abs(old_quantity) + fill.quantity
            position.average_cost = (
                abs(old_quantity) * position.average_cost + fill.quantity * fill.price
            ) / total_units
        else:
            closed = min(abs(old_quantity), fill.quantity)
            if old_quantity > ZERO:
                position.realized_pnl += closed * (fill.price - position.average_cost)
            else:
                position.realized_pnl += closed * (position.average_cost - fill.price)
            if new_quantity == ZERO:
                position.average_cost = ZERO
            elif old_quantity * new_quantity < ZERO:
                position.average_cost = fill.price
        position.quantity = new_quantity

    def create_order(
        self, *, symbol: str, side: Side | str, quantity: Decimal,
        signal_time: datetime, order_time: datetime, run_id: str, context_hash: str,
        strategy_id: str = "default",
        client_order_id: str = "",
        instrument_id: str = "", asset_class: str = "", currency: str = "",
        multiplier: Decimal = ZERO, expiry: date | None = None,
        contract_code: str | None = None,
    ) -> Order:
        return Order(
            symbol=symbol, side=side, quantity=quantity, signal_time=signal_time,
            order_time=order_time, run_id=run_id, context_hash=context_hash,
            strategy_id=strategy_id,
            client_order_id=client_order_id, mode=self.mode,
            instrument_id=instrument_id, asset_class=asset_class, currency=currency,
            multiplier=multiplier, expiry=expiry, contract_code=contract_code,
        ).with_ids()

    def submit_order(self, order: Order | None = None, **kwargs: Any) -> Order:
        with self._lock:
            if self.kill_switch_active:
                raise KillSwitchActive(self.kill_switch_reason or "kill switch is active")
            candidate = order.with_ids() if order is not None else self.create_order(**kwargs)
            if candidate.mode is not self.mode:
                raise OrderRejected("order mode differs from the paper session")
            if self.binding is not None and candidate.strategy_id != self.binding.strategy_id:
                raise OrderRejected("order strategy differs from the fixed paper session binding")
            candidate_identity = (
                candidate.instrument_id,
                candidate.asset_class,
                candidate.currency,
                candidate.multiplier,
            )
            if any(
                record.order.symbol == candidate.symbol
                and (
                    record.order.instrument_id,
                    record.order.asset_class,
                    record.order.currency,
                    record.order.multiplier,
                )
                != candidate_identity
                for record in self.orders.values()
            ):
                raise OrderRejected(
                    "paper symbol is already bound to another immutable instrument identity"
                )
            existing_id = self._client_orders.get(candidate.client_order_id)
            if existing_id is not None:
                existing = self.orders[existing_id].order
                if existing.to_dict() != candidate.to_dict():
                    raise DuplicateOrderError("client_order_id conflicts with a different order")
                return existing
            if candidate.order_id in self.orders:
                existing = self.orders[candidate.order_id].order
                if existing.to_dict() != candidate.to_dict():
                    raise DuplicateOrderError("order_id conflicts with a different order")
                return existing
            self._append(
                LedgerEventType.ORDER_ACCEPTED, candidate.order_time,
                {"order": candidate.to_dict()}, idempotency_key=candidate.order_id,
            )
            return candidate

    def _validate_quote(self, order: Order, quote: Quote | None, fill_time: datetime) -> Quote:
        if quote is None:
            raise QuoteRejected("a quote is required; missing prices are never substituted")
        if not isinstance(quote, Quote):
            raise QuoteRejected("quote must be a validated Quote")
        _aware(fill_time, "fill_time")
        if order.signal_time >= order.order_time or order.order_time > fill_time:
            raise OrderRejected("required timeline is signal_time < order_time <= fill_time")
        if quote.symbol != order.symbol:
            raise QuoteRejected("quote symbol does not match order symbol")
        if quote.timestamp < order.order_time:
            raise QuoteRejected("quote predates the order")
        if quote.timestamp > fill_time:
            raise QuoteRejected("future quote cannot be used for a fill")
        if fill_time - quote.timestamp > self.mandate.max_quote_age:
            raise QuoteRejected("quote is stale")
        return quote

    def _price_and_costs(self, side: Side, quantity: Decimal, quote: Quote) -> tuple[Decimal, ...]:
        mid = quote.mid
        observed_half_spread = (quote.ask - quote.bid) / Decimal("2")
        configured_half_spread = mid * self.mandate.costs.spread_bps / (Decimal("2") * BPS)
        half_spread = max(observed_half_spread, configured_half_spread)
        slippage_per_unit = mid * self.mandate.costs.slippage_bps / BPS
        price = mid + side.sign * (half_spread + slippage_per_unit)
        if price <= ZERO:
            raise QuoteRejected("modeled execution price is nonpositive")
        notional = quantity * price
        commission = (
            self.mandate.costs.commission_fixed
            + self.mandate.costs.commission_per_unit * quantity
            + notional * self.mandate.costs.commission_bps / BPS
        )
        spread_cost = half_spread * quantity
        slippage_cost = slippage_per_unit * quantity
        return price, mid, notional, commission, spread_cost, slippage_cost

    def _pretrade_check(
        self, order: Order, quantity: Decimal, price: Decimal, reference_price: Decimal,
        commission: Decimal, fill_time: datetime,
    ) -> Decimal:
        current = self.positions.get(order.symbol, Position(order.symbol)).quantity
        new_quantity = current + order.side.sign * quantity
        if not self.mandate.allow_short and new_quantity < ZERO:
            raise MandateViolation("short positions are disabled by the mandate")
        cash_delta = -(quantity * price + commission) if order.side is Side.BUY else quantity * price - commission
        new_cash = self.cash + cash_delta
        if not self.mandate.allow_margin and new_cash < -ACCOUNTING_TOLERANCE:
            raise InsufficientCash("paper account has insufficient cash")
        projected_marks = dict(self.marks)
        projected_marks[order.symbol] = reference_price
        projected_quantities = {symbol: position.quantity for symbol, position in self.positions.items()}
        projected_quantities[order.symbol] = new_quantity
        missing = [symbol for symbol, qty in projected_quantities.items() if qty != ZERO and symbol not in projected_marks]
        if missing:
            raise MandateViolation(f"cannot calculate leverage without marks for {', '.join(sorted(missing))}")
        stale = []
        for symbol, qty in projected_quantities.items():
            if qty == ZERO or symbol == order.symbol:
                continue
            mark_time = self.mark_times.get(symbol)
            if (
                mark_time is None
                or mark_time > fill_time
                or fill_time - mark_time > self.mandate.max_quote_age
            ):
                stale.append(symbol)
        if stale:
            raise MandateViolation(
                "cannot calculate leverage with stale marks for " + ", ".join(sorted(stale))
            )
        net_market_value = sum((qty * projected_marks[symbol] for symbol, qty in projected_quantities.items()), ZERO)
        gross_exposure = sum((abs(qty * projected_marks[symbol]) for symbol, qty in projected_quantities.items()), ZERO)
        equity = new_cash + net_market_value
        if equity <= ZERO:
            raise MandateViolation("projected equity is nonpositive")
        if gross_exposure / equity > self.mandate.max_gross_leverage + ACCOUNTING_TOLERANCE:
            raise MandateViolation("projected gross leverage exceeds the mandate")
        return cash_delta

    def fill_order(
        self, order_id: str, quote: Quote | None, *, fill_time: datetime | None = None,
        max_quantity: Decimal | None = None,
        quote_lineage: QuoteLineage | Mapping[str, Any] | None = None,
    ) -> Fill:
        with self._lock:
            if self.kill_switch_active:
                raise KillSwitchActive(self.kill_switch_reason or "kill switch is active")
            record = self.orders.get(order_id)
            if record is None:
                raise OrderRejected("unknown order_id")
            if record.status is OrderStatus.CANCELLED:
                raise OrderRejected("cancelled order cannot fill")
            effective_fill_time = fill_time or (quote.timestamp if quote is not None else None)
            if effective_fill_time is None:
                raise QuoteRejected("a quote is required; fill_time and quote are both missing")
            valid_quote = self._validate_quote(record.order, quote, effective_fill_time)
            if quote_lineage is None:
                raise QuoteRejected("confirmed/final quote lineage is required for every fill")
            lineage = (
                quote_lineage
                if isinstance(quote_lineage, QuoteLineage)
                else QuoteLineage.from_dict(quote_lineage)
            )
            lineage.validate_quote(valid_quote, available_at=effective_fill_time)
            if (
                lineage.instrument_id != record.order.instrument_id
                or lineage.currency != record.order.currency
            ):
                raise QuoteRejected("quote lineage instrument/currency does not match the order")
            quote_id = _identifier(
                "quote", {"quote": valid_quote.to_dict(), "lineage": lineage.to_dict()}
            )
            requested = record.remaining_quantity
            if requested <= ZERO:
                raise OrderRejected("order is already filled")
            if max_quantity is not None:
                requested = min(requested, _decimal(max_quantity, "max_quantity", positive=True))
            if valid_quote.available_quantity is not None:
                consumed = sum(
                    (fill.quantity for fill in self.fills if fill.quote_id == quote_id), ZERO
                )
                remaining_liquidity = valid_quote.available_quantity - consumed
                requested = min(requested, remaining_liquidity)
            if requested <= ZERO:
                raise QuoteRejected("quote has no available quantity")
            fill_key = {
                "order_id": order_id, "fill_time": _dt_text(effective_fill_time),
                "quote": valid_quote.to_dict(), "quantity": _decimal_text(requested),
            }
            fill_id = _identifier("fill", fill_key)
            for existing in self.fills:
                if existing.fill_id == fill_id:
                    return existing
            price, mid, notional, commission, spread_cost, slippage_cost = self._price_and_costs(
                record.order.side, requested, valid_quote,
            )
            cash_delta = self._pretrade_check(
                record.order, requested, price, mid, commission, effective_fill_time
            )
            fill = Fill(
                fill_id=fill_id, order_id=order_id, client_order_id=record.order.client_order_id,
                symbol=record.order.symbol, side=record.order.side, quantity=requested,
                price=price, reference_price=mid, gross_notional=notional,
                commission=commission, spread_cost=spread_cost, slippage_cost=slippage_cost,
                total_cost=commission + spread_cost + slippage_cost,
                fill_time=effective_fill_time, quote_time=valid_quote.timestamp,
                quote_id=quote_id,
                quote_available_quantity=valid_quote.available_quantity,
                quote_lineage=lineage,
                mode=self.mode,
            )
            cash_event = CashEvent(
                cash_event_id=_identifier("cash", {"fill_id": fill_id}),
                kind=CashEventKind.TRADE_AND_COMMISSION, timestamp=effective_fill_time,
                amount=cash_delta, currency=self.mandate.currency, related_id=fill_id,
                balance_after=self.cash + cash_delta,
            )
            self._append(
                LedgerEventType.FILL, effective_fill_time,
                {"fill": fill.to_dict(), "cash_event": cash_event.to_dict()},
                idempotency_key=fill_id,
            )
            return fill

    def process_quote(
        self,
        quote: Quote,
        *,
        quote_lineage: QuoteLineage | Mapping[str, Any],
        fill_time: datetime | None = None,
    ) -> list[Fill]:
        """Fill open orders for a symbol in deterministic FIFO order.

        A quote's available quantity is shared across orders in this call.
        """
        effective_fill_time = fill_time or quote.timestamp
        available = quote.available_quantity
        results: list[Fill] = []
        records = sorted(
            (r for r in self.orders.values() if r.order.symbol == quote.symbol and r.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}),
            key=lambda r: (r.order.order_time, r.order.order_id),
        )
        for record in records:
            if available is not None and available <= ZERO:
                break
            fill = self.fill_order(
                record.order.order_id,
                quote,
                fill_time=effective_fill_time,
                max_quantity=available,
                quote_lineage=quote_lineage,
            )
            results.append(fill)
            if available is not None:
                available -= fill.quantity
        return results

    def process_bar(
        self, bar: Bar, *, fill_time: datetime | None = None,
        available_quantity: Decimal | None = None,
        quote_lineage: QuoteLineage | Mapping[str, Any],
    ) -> list[Fill]:
        quote = bar.to_quote(self.mandate.costs.spread_bps, available_quantity)
        return self.process_quote(
            quote, quote_lineage=quote_lineage, fill_time=fill_time or bar.timestamp
        )

    def cancel_order(self, order_id: str, *, cancelled_at: datetime, reason: str) -> None:
        _aware(cancelled_at, "cancelled_at")
        record = self.orders.get(order_id)
        if record is None or record.status is OrderStatus.FILLED:
            raise OrderRejected("only an open order can be cancelled")
        self._append(
            LedgerEventType.ORDER_CANCELLED, cancelled_at,
            {"order_id": order_id, "reason": reason.strip() or "operator cancellation"},
            idempotency_key=f"{order_id}:{_dt_text(cancelled_at)}",
        )

    def mark_to_market(
        self,
        quotes: Mapping[str, Quote] | Iterable[Quote],
        *,
        asof: datetime,
        quote_lineage: Mapping[str, QuoteLineage | Mapping[str, Any]],
    ) -> dict[str, Any]:
        asof = _aware(asof, "asof")
        quote_map = dict(quotes) if isinstance(quotes, Mapping) else {quote.symbol: quote for quote in quotes}
        mark_rows: list[dict[str, str]] = []
        for symbol, position in sorted(self.positions.items()):
            if position.quantity == ZERO:
                continue
            quote = quote_map.get(symbol)
            if quote is None:
                raise QuoteRejected(f"missing mark for open position {symbol}")
            if quote.symbol != symbol or quote.timestamp > asof or asof - quote.timestamp > self.mandate.max_quote_age:
                raise QuoteRejected(f"invalid or stale mark for {symbol}")
            raw_lineage = quote_lineage.get(symbol)
            if raw_lineage is None:
                raise QuoteRejected(f"confirmed/final quote lineage is required for mark {symbol}")
            lineage = (
                raw_lineage
                if isinstance(raw_lineage, QuoteLineage)
                else QuoteLineage.from_dict(raw_lineage)
            )
            lineage.validate_quote(quote, available_at=asof)
            identities = {
                (record.order.instrument_id, record.order.currency)
                for record in self.orders.values()
                if record.order.symbol == symbol
            }
            if identities != {(lineage.instrument_id, lineage.currency)}:
                raise QuoteRejected(
                    f"quote lineage instrument/currency does not match position {symbol}"
                )
            mark_rows.append(
                {
                    "symbol": symbol,
                    "price": _decimal_text(quote.mid),
                    "quote_time": _dt_text(quote.timestamp),
                    "quote_lineage": lineage.to_dict(),
                }
            )
        key = _identifier("marks", {"asof": _dt_text(asof), "marks": mark_rows})
        self._append(LedgerEventType.MARK_TO_MARKET, asof, {"marks": mark_rows}, idempotency_key=key)
        return self.snapshot(asof=asof)

    def activate_kill_switch(self, reason: str, *, activated_at: datetime) -> None:
        _aware(activated_at, "activated_at")
        text = reason.strip() if isinstance(reason, str) else ""
        if not text:
            raise ValidationError("kill-switch reason is required")
        self._append(
            LedgerEventType.KILL_SWITCH, activated_at, {"reason": text},
            idempotency_key=f"kill:{_dt_text(activated_at)}:{text}",
        )

    def on_corporate_action(
        self, *, symbol: str, action_type: str, effective_at: datetime,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Fail closed until split/dividend/symbol-change accounting is implemented."""
        _aware(effective_at, "effective_at")
        reason = f"unsupported corporate action {action_type} for {symbol.upper()}"
        self._append(
            LedgerEventType.CORPORATE_ACTION_BLOCKED, effective_at,
            {"reason": reason, "symbol": symbol.upper(), "action_type": action_type,
             "details": dict(details or {})},
            idempotency_key=f"corp:{symbol.upper()}:{action_type}:{_dt_text(effective_at)}",
        )
        raise CorporateActionUnsupported(reason)

    def reconcile(self) -> dict[str, Any]:
        expected_cash = self.mandate.starting_cash
        expected_quantities: dict[str, Decimal] = {}
        for fill in self.fills:
            expected_cash += -(fill.gross_notional + fill.commission) if fill.side is Side.BUY else fill.gross_notional - fill.commission
            expected_quantities[fill.symbol] = expected_quantities.get(fill.symbol, ZERO) + fill.side.sign * fill.quantity
        cash_difference = self.cash - expected_cash
        position_differences = {
            symbol: self.positions.get(symbol, Position(symbol)).quantity - quantity
            for symbol, quantity in expected_quantities.items()
            if abs(self.positions.get(symbol, Position(symbol)).quantity - quantity) > ACCOUNTING_TOLERANCE
        }
        ok = abs(cash_difference) <= ACCOUNTING_TOLERANCE and not position_differences
        return {
            "ok": ok, "tolerance": _decimal_text(ACCOUNTING_TOLERANCE),
            "cash_expected": _decimal_text(expected_cash), "cash_actual": _decimal_text(self.cash),
            "cash_difference": _decimal_text(cash_difference),
            "position_differences": {key: _decimal_text(value) for key, value in position_differences.items()},
        }

    def status(self) -> dict[str, Any]:
        ledger_head = self.events[-1] if self.events else None
        return {
            "session_id": self.session_id, "mode": self.mode.value,
            "status": "NOT VALIDATED/PAPER", "execution_status": "PAPER",
            "validation_status": self.validation_status, "actionability": self.actionability,
            "virtual_broker_only": True, "live_trading_supported": False,
            "kill_switch_active": self.kill_switch_active,
            "binding": None if self.binding is None else self.binding.to_dict(),
            "operation_receipt_count": len(self.operation_receipts),
            "kill_switch_reason": self.kill_switch_reason,
            "event_count": len(self.events),
            "ledger_head": None if ledger_head is None else {
                "sequence": ledger_head.sequence,
                "event_hash": ledger_head.event_hash,
                "timestamp": _dt_text(ledger_head.timestamp),
                "checkpoint_path": self.store.head_path.name,
            },
            "open_order_count": sum(
                record.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
                for record in self.orders.values()
            ),
        }

    def snapshot(self, *, asof: datetime | None = None) -> dict[str, Any]:
        if asof is not None:
            asof = _aware(asof, "asof")
            if self.events and asof < self.events[-1].timestamp:
                raise ValidationError(
                    "historical snapshot asof predates the ledger head; use a truncated replay"
                )
        effective_asof = asof or (self.events[-1].timestamp if self.events else None)
        market_value = ZERO
        gross_exposure = ZERO
        position_rows = []
        missing_marks = []
        stale_marks = []
        for symbol, position in sorted(self.positions.items()):
            if position.quantity == ZERO:
                continue
            mark = self.marks.get(symbol)
            if mark is None:
                missing_marks.append(symbol)
            else:
                mark_time = self.mark_times.get(symbol)
                if (
                    effective_asof is not None
                    and (
                        mark_time is None
                        or mark_time > effective_asof
                        or effective_asof - mark_time > self.mandate.max_quote_age
                    )
                ):
                    stale_marks.append(symbol)
                else:
                    market_value += position.quantity * mark
                    gross_exposure += abs(position.quantity * mark)
            row = position.to_dict(mark)
            row["mark_time"] = (
                None if self.mark_times.get(symbol) is None else _dt_text(self.mark_times[symbol])
            )
            row["mark_status"] = (
                "missing" if symbol in missing_marks else "stale" if symbol in stale_marks else "current"
            )
            position_rows.append(row)
        equity = None if missing_marks or stale_marks else self.cash + market_value
        leverage = None if equity is None or equity <= ZERO else gross_exposure / equity
        reconciliation = self.reconcile()
        return {
            **self.status(), "asof": None if asof is None else _dt_text(asof),
            "currency": self.mandate.currency, "cash": _decimal_text(self.cash),
            "market_value": None if equity is None else _decimal_text(market_value),
            "equity": None if equity is None else _decimal_text(equity),
            "gross_exposure": None if equity is None else _decimal_text(gross_exposure),
            "gross_leverage": None if leverage is None else _decimal_text(leverage),
            "missing_marks": missing_marks, "stale_marks": stale_marks,
            "positions": position_rows,
            "orders": [self.orders[key].to_dict() for key in sorted(self.orders)],
            "fills": [fill.to_dict() for fill in self.fills],
            "costs": {
                "commission": _decimal_text(self.total_commission),
                "spread": _decimal_text(self.total_spread_cost),
                "slippage": _decimal_text(self.total_slippage_cost),
                "total": _decimal_text(self.total_commission + self.total_spread_cost + self.total_slippage_cost),
            },
            "reconciliation": reconciliation,
            "wording": "Virtual PAPER/SHADOW simulation only; NOT VALIDATED; NOT ACTIONABLE; no live broker path.",
        }

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()


# The platform layer may refer to either name; both are strictly virtual.
PaperEngine = PaperBroker


__all__ = [
    "ACCOUNTING_TOLERANCE", "Bar", "CashEvent", "CashEventKind", "CorporateActionUnsupported",
    "CostModel", "DuplicateOrderError", "ExecutionMode", "Fill", "InsufficientCash",
    "JsonlPaperStore", "KillSwitchActive", "LedgerEvent", "LedgerIntegrityError",
    "MandateViolation", "Order", "OrderRejected", "OrderStatus", "PaperBroker",
    "PaperEngine", "PaperError", "PaperMandate", "PaperSessionBinding", "Position", "Quote", "QuoteLineage", "QuoteRejected",
    "Side", "ValidationError",
]
