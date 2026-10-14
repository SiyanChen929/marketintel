"""Independent, long-only USD lifecycle accounting; never an execution route.

All economic inputs are explicit, source-bound events. No quote, fill, action,
calendar, or zero-income assumption is inferred from a target portfolio. This
module does not change the existing PAPER intraday restriction. Decimal strings
are the interchange format. Artifacts contain the complete replayable journal.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

SCHEMA = "platform.shadow-lifecycle.v1"
ZERO = Decimal("0")
TOLERANCE = Decimal("0.00000001")
GENESIS = "0" * 64
_HASH = re.compile(r"^[0-9a-f]{64}$")


class ShadowAccountingError(ValueError):
    """Missing or inconsistent evidence prevents this event from being booked."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowAccountingError(f"{field} is required")
    return value.strip()


def _decimal(value: Any, field: str, *, positive=False, nonnegative=False) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ShadowAccountingError(f"{field} must use a decimal string or integer")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ShadowAccountingError(f"{field} must be finite") from exc
    if not number.is_finite() or (positive and number <= 0) or (nonnegative and number < 0):
        raise ShadowAccountingError(f"invalid {field}")
    return number


def _time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise ShadowAccountingError(f"invalid {field}") from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ShadowAccountingError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str:
    return _time(value, "timestamp").isoformat()


def _encode(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ShadowAccountingError("event must contain finite JSON values; use decimal strings") from exc


def _digest(value: Any) -> str:
    return sha256(_encode(value).encode()).hexdigest()


def _evidence(value: Any, *, known_by: datetime) -> dict:
    if not isinstance(value, Mapping):
        raise ShadowAccountingError("source evidence is required")
    row = dict(value)
    _text(row.get("source"), "evidence source")
    if not _HASH.fullmatch(str(row.get("content_sha256", ""))):
        raise ShadowAccountingError("evidence content_sha256 is required")
    observed = _time(row.get("observed_at"), "evidence observed_at")
    available = _time(row.get("available_at"), "evidence available_at")
    if observed > available or available > known_by:
        raise ShadowAccountingError("source evidence was unavailable at the accounting boundary")
    if row.get("finality") not in ("confirmed", "final"):
        raise ShadowAccountingError("confirmed/final evidence is required")
    return row


def _instrument(value: Any) -> str:
    instrument = _text(value, "instrument_id")
    if not re.fullmatch(r"equity:[A-Za-z0-9][A-Za-z0-9._:/-]*", instrument):
        raise ShadowAccountingError("only stable equity instrument IDs are supported")
    return instrument


class ShadowLedger:
    """A replayable research book, deliberately separate from PaperBroker.

    Fill prices already include spread/slippage; ``fee`` is an additional cash
    charge. Trade principal is unsettled until an explicit settlement event.
    Normal cash dividends accrue at the declared ex boundary and remain owned
    after the shares are sold. No shorts, leverage, FX, due bills or implicit
    corporate-action coverage are supported.
    """

    def __init__(self, *, book_id: str, opened_at: Any, initial_cash: str,
                 sample_type: str, max_quote_age_seconds: int = 300,
                 max_acquisition_lag_seconds: int = 300):
        if sample_type not in ("synthetic_fixture", "observed_paper"):
            raise ShadowAccountingError("sample_type must explicitly be synthetic_fixture or observed_paper")
        for label, value in (("max_quote_age_seconds", max_quote_age_seconds),
                             ("max_acquisition_lag_seconds", max_acquisition_lag_seconds)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ShadowAccountingError(f"invalid {label}")
        self.config = {"book_id": _text(book_id, "book_id"), "opened_at": _iso(opened_at),
                       "initial_cash": str(_decimal(initial_cash, "initial_cash", nonnegative=True)),
                       "sample_type": sample_type, "currency": "USD",
                       "max_quote_age_seconds": max_quote_age_seconds,
                       "max_acquisition_lag_seconds": max_acquisition_lag_seconds}
        self.events: list[dict] = []
        self._ids: dict[str, dict] = {}
        self.settled_cash = Decimal(self.config["initial_cash"])
        self.positions: dict[str, dict] = {}
        self.pending_trades: dict[str, dict] = {}
        self.dividends: dict[str, dict] = {}
        self.fees = ZERO
        self.dividend_income = ZERO
        self.withholding_tax = ZERO
        self.realized_gross = ZERO
        self.closes: list[dict] = []
        self._interval_instruments: set[str] = set()
        self._actions: list[dict] = []
        self._source_fills: set[str] = set()

    @property
    def head(self) -> str:
        return self.events[-1]["event_hash"] if self.events else _digest(self.config)

    @property
    def economic_cash(self) -> Decimal:
        return self.settled_cash + sum((t["amount"] for t in self.pending_trades.values()), ZERO)

    @property
    def available_cash(self) -> Decimal:
        # Unsettled sale proceeds and dividend receivables cannot finance buys.
        return self.settled_cash + sum((min(ZERO, t["amount"]) for t in self.pending_trades.values()), ZERO)

    @property
    def dividend_receivable(self) -> Decimal:
        return sum((d["net"] for d in self.dividends.values() if not d["paid"]), ZERO)

    def _economic_instruments(self) -> set[str]:
        return ({instrument for instrument, row in self.positions.items() if row["quantity"]}
                | {row["instrument_id"] for row in self.pending_trades.values()}
                | {row["instrument_id"] for row in self.dividends.values() if not row["paid"] and row["net"]})

    def append(self, *, event_id: str, kind: str, effective_at: Any, payload: Mapping,
               recorded_at: Any) -> dict:
        """Validate on a private copy, then atomically publish an in-memory event.

        An identical event ID is idempotent. A changed payload or timestamp with
        the same ID is an error. Backdated inserts require a separately named
        replay artifact and cannot rewrite this book.
        """
        effective, recorded = _time(effective_at, "effective_at"), _time(recorded_at, "recorded_at")
        if recorded < effective:
            raise ShadowAccountingError("recorded_at cannot precede effective_at")
        body = {"event_id": _text(event_id, "event_id"), "kind": _text(kind, "kind"),
                "effective_at": effective.isoformat(), "recorded_at": recorded.isoformat(),
                "payload": deepcopy(dict(payload))}
        _encode(body)
        existing = self._ids.get(body["event_id"])
        if existing:
            if any(existing[key] != value for key, value in body.items()):
                raise ShadowAccountingError("event ID conflicts with a previously recorded event")
            return deepcopy(existing)
        last = _time(self.events[-1]["effective_at"] if self.events else self.config["opened_at"], "last event")
        if effective < last or (self.closes and effective <= _time(self.closes[-1]["asof"], "last close")):
            raise ShadowAccountingError("event crosses a sealed or later accounting boundary")
        if self.events and recorded < _time(self.events[-1]["recorded_at"], "last recorded_at"):
            raise ShadowAccountingError("recorded_at must be monotonic")
        candidate = deepcopy(self)
        candidate._apply(body)
        row = {**body, "sequence": len(self.events) + 1, "previous_hash": self.head}
        row["event_hash"] = _digest(row)
        candidate.events.append(row)
        candidate._ids[row["event_id"]] = row
        self.__dict__.update(candidate.__dict__)
        return deepcopy(row)

    def _apply(self, event: dict) -> None:
        handler = getattr(self, "_on_" + event["kind"].lower(), None)
        if handler is None or event["kind"] not in {"FILL", "SETTLEMENT", "DIVIDEND_EX", "DIVIDEND_PAY", "SPLIT", "CLOSE"}:
            raise ShadowAccountingError("unsupported lifecycle event")
        handler(event["event_id"], _time(event["effective_at"], "effective_at"), event["payload"])

    def _event_evidence(self, value: Any, *, known_by: datetime) -> dict:
        return _evidence(value, known_by=known_by)

    def _quote_evidence(self, value: Any, *, known_by: datetime) -> dict:
        return _evidence(value, known_by=known_by)

    def _on_fill(self, event_id: str, at: datetime, p: dict) -> None:
        instrument = _instrument(p.get("instrument_id"))
        _text(p.get("run_id"), "run_id")
        _text(p.get("order_id"), "order_id")
        fill_id = _text(p.get("fill_id"), "fill_id")
        if fill_id in self._source_fills:
            raise ShadowAccountingError("source fill was already booked under another event ID")
        if p.get("currency") != "USD" or p.get("asset_class") != "equity":
            raise ShadowAccountingError("only USD cash equities are supported")
        if p.get("sample_type") != self.config["sample_type"]:
            raise ShadowAccountingError("fill sample_type does not match the book")
        if p.get("price_basis") != "unadjusted":
            raise ShadowAccountingError("fills require unadjusted trade prices")
        side = p.get("side")
        if side not in ("BUY", "SELL"):
            raise ShadowAccountingError("fill side must be BUY or SELL")
        signal, order = _time(p.get("signal_at"), "signal_at"), _time(p.get("order_at"), "order_at")
        quote = self._quote(p.get("quote"), at, instrument)
        quote_at = _time(quote["evidence"]["observed_at"], "quote observed_at")
        if not signal < order <= quote_at <= at:
            raise ShadowAccountingError("fill signal/order/quote clock is inconsistent")
        evidence = self._event_evidence(p.get("evidence"), known_by=at)
        if _time(evidence["observed_at"], "fill evidence observed_at") != at:
            raise ShadowAccountingError("fill evidence must identify its fill timestamp")
        quantity = _decimal(p.get("quantity"), "quantity", positive=True)
        price = _decimal(p.get("price"), "price", positive=True)
        fee = _decimal(p.get("fee"), "fee", nonnegative=True)
        settlement = _time(p.get("settlement_at"), "settlement_at")
        if settlement < at:
            raise ShadowAccountingError("settlement cannot precede fill")
        position = self.positions.setdefault(instrument, {"quantity": ZERO, "cost": ZERO})
        gross = quantity * price
        if side == "BUY":
            if gross + fee > self.available_cash:
                raise ShadowAccountingError("insufficient settled cash; unsettled proceeds cannot be reused")
            position["cost"] += gross
            position["quantity"] += quantity
            amount = -gross
        else:
            if quantity > position["quantity"]:
                raise ShadowAccountingError("shorts require borrow/locate and liability accounting; rejected")
            if fee > self.available_cash:
                raise ShadowAccountingError("insufficient settled cash for fee")
            disposed_cost = position["cost"] * quantity / position["quantity"]
            position["quantity"] -= quantity
            position["cost"] -= disposed_cost
            self.realized_gross += gross - disposed_cost
            amount = gross
        self.settled_cash -= fee
        self.fees += fee
        self.pending_trades[event_id] = {"instrument_id": instrument, "amount": amount,
                                         "settlement_at": settlement.isoformat()}
        self._source_fills.add(fill_id)
        self._interval_instruments.add(instrument)

    def _on_settlement(self, event_id: str, at: datetime, p: dict) -> None:
        self._event_evidence(p.get("evidence"), known_by=at)
        trade_id = _text(p.get("fill_event_id"), "fill_event_id")
        trade = self.pending_trades.get(trade_id)
        if trade is None:
            raise ShadowAccountingError("unknown or already settled fill")
        if at < _time(trade["settlement_at"], "settlement_at"):
            raise ShadowAccountingError("settlement arrived before the declared settlement boundary")
        amount = _decimal(p.get("amount"), "settlement amount")
        if p.get("currency") != "USD" or amount != trade["amount"]:
            raise ShadowAccountingError("settlement amount/currency does not reconcile to the fill")
        self.settled_cash += amount
        self._interval_instruments.add(trade["instrument_id"])
        del self.pending_trades[trade_id]
        if self.available_cash < ZERO:
            raise ShadowAccountingError("settlement breaches reserved cash")

    def _action(self, action_id: str, at: datetime, p: dict) -> str:
        instrument = _instrument(p.get("instrument_id"))
        self._event_evidence(p.get("evidence"), known_by=at)
        if p.get("currency") != "USD":
            raise ShadowAccountingError("corporate action currency must be USD")
        if any(a["action_id"] == action_id for a in self._actions):
            raise ShadowAccountingError("corporate action ID was already applied")
        if any(row["kind"] == "FILL" and row["payload"]["instrument_id"] == instrument
               and _time(row["effective_at"], "fill time") == at for row in self.events):
            raise ShadowAccountingError("corporate actions must precede fills at the same effective boundary")
        self._actions.append({"action_id": action_id, "instrument_id": instrument, "effective_at": at.isoformat()})
        self._interval_instruments.add(instrument)
        return instrument

    def _on_dividend_ex(self, event_id: str, at: datetime, p: dict) -> None:
        action = _text(p.get("action_id"), "action_id")
        instrument = self._action(action, at, p)
        if p.get("entitlement_rule") != "pre_ex_position" or p.get("distribution_type") != "normal_cash":
            raise ShadowAccountingError("only explicitly attested normal-cash pre-ex entitlement is supported")
        record, pay = _time(p.get("record_at"), "record_at"), _time(p.get("pay_at"), "pay_at")
        if not at <= record <= pay:
            raise ShadowAccountingError("special/due-bill dividend chronology is unsupported")
        quantity = self.positions.get(instrument, {}).get("quantity", ZERO)
        if _decimal(p.get("entitled_quantity"), "entitled_quantity", nonnegative=True) != quantity:
            raise ShadowAccountingError("dividend entitlement does not match the pre-ex position")
        per_share = _decimal(p.get("gross_per_share"), "gross_per_share", positive=True)
        withholding = _decimal(p.get("withholding"), "withholding", nonnegative=True)
        gross = quantity * per_share
        if withholding > gross:
            raise ShadowAccountingError("dividend withholding exceeds gross income")
        self.dividends[action] = {"instrument_id": instrument, "quantity": quantity,
                                  "gross": gross, "net": gross - withholding, "paid": False,
                                  "pay_at": pay.isoformat()}
        self.dividend_income += gross
        self.withholding_tax += withholding

    def _on_dividend_pay(self, event_id: str, at: datetime, p: dict) -> None:
        self._event_evidence(p.get("evidence"), known_by=at)
        action = _text(p.get("action_id"), "action_id")
        dividend = self.dividends.get(action)
        if dividend is None or dividend["paid"]:
            raise ShadowAccountingError("unknown or already paid dividend")
        if at < _time(dividend["pay_at"], "pay_at"):
            raise ShadowAccountingError("dividend cannot be paid before pay_at")
        amount = _decimal(p.get("amount"), "dividend payment", nonnegative=True)
        if p.get("currency") != "USD" or amount != dividend["net"]:
            raise ShadowAccountingError("dividend payment does not match net entitlement")
        self.settled_cash += amount
        dividend["paid"] = True
        self._interval_instruments.add(dividend["instrument_id"])

    def _on_split(self, event_id: str, at: datetime, p: dict) -> None:
        instrument = self._action(_text(p.get("action_id"), "action_id"), at, p)
        self._actions[-1]["type"] = "SPLIT"
        numerator = _decimal(p.get("new_shares"), "new_shares", positive=True)
        denominator = _decimal(p.get("old_shares"), "old_shares", positive=True)
        if numerator != numerator.to_integral_value() or denominator != denominator.to_integral_value():
            raise ShadowAccountingError("split ratio must use positive integral share counts")
        position = self.positions.get(instrument)
        if position is not None:
            new_quantity = position["quantity"] * numerator / denominator
            if new_quantity != new_quantity.to_integral_value():
                raise ShadowAccountingError("fractional split requires cash-in-lieu or custody evidence; rejected")
            position["quantity"] = new_quantity
        # Total cost and unsettled trade dollar principal are deliberately unchanged.

    def _quote(self, raw: Any, at: datetime, instrument: str) -> dict:
        if not isinstance(raw, Mapping):
            raise ShadowAccountingError("explicit quote evidence is required")
        quote = dict(raw)
        if quote.get("instrument_id") != instrument or quote.get("currency") != "USD":
            raise ShadowAccountingError("quote instrument/currency mismatch")
        if quote.get("price_basis") != "unadjusted":
            raise ShadowAccountingError("adjusted quotes would double-count corporate actions")
        evidence = self._quote_evidence(quote.get("evidence"), known_by=at)
        observed, available = _time(evidence["observed_at"], "quote observed_at"), _time(evidence["available_at"], "quote available_at")
        splits = [row for row in self._actions if row["instrument_id"] == instrument and row.get("type") == "SPLIT"]
        if splits and observed < _time(splits[-1]["effective_at"], "split boundary"):
            raise ShadowAccountingError("quote predates the share-basis split boundary")
        if at - observed > timedelta(seconds=self.config["max_quote_age_seconds"]):
            raise ShadowAccountingError("quote is stale")
        if available - observed > timedelta(seconds=self.config["max_acquisition_lag_seconds"]):
            raise ShadowAccountingError("quote is a delayed/backfilled acquisition")
        _decimal(quote.get("price"), "quote price", positive=True)
        return quote

    def _on_close(self, event_id: str, at: datetime, p: dict) -> None:
        session = p.get("session")
        if not isinstance(session, Mapping):
            raise ShadowAccountingError("explicit exchange session and calendar evidence are required")
        self._event_evidence(session.get("evidence"), known_by=at)
        session_id = _text(session.get("session_id"), "session_id")
        session_open = _time(session.get("opens_at"), "opens_at")
        if _time(session.get("closes_at"), "closes_at") != at or session_open >= at:
            raise ShadowAccountingError("valuation boundary must equal the declared exchange close")
        previous_session = self.closes[-1]["session_id"] if self.closes else None
        if session.get("previous_session_id") != previous_session:
            raise ShadowAccountingError("daily session chain is missing or inconsistent")
        if any(row["session_id"] == session_id for row in self.closes):
            raise ShadowAccountingError("session already closed")
        since = _time(self.closes[-1]["asof"] if self.closes else self.config["opened_at"], "coverage start")
        coverage = p.get("corporate_action_coverage")
        if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
            raise ShadowAccountingError("corporate-action coverage is unknown; absence is not a no-event declaration")
        coverage_evidence = self._event_evidence(coverage.get("evidence"), known_by=at)
        if _time(coverage_evidence["observed_at"], "coverage observed_at") != at:
            raise ShadowAccountingError("complete corporate-action coverage must be observed through the close")
        if _time(coverage.get("from_at"), "coverage from_at") != since or _time(coverage.get("through_at"), "coverage through_at") != at:
            raise ShadowAccountingError("corporate-action coverage interval is incomplete")
        held = {instrument for instrument, row in self.positions.items() if row["quantity"]}
        required = self._economic_instruments() | self._interval_instruments
        covered = coverage.get("instrument_ids")
        if not isinstance(covered, list) or len(set(covered)) != len(covered) or set(covered) != required:
            raise ShadowAccountingError("corporate-action coverage must match all held/traded and receivable instruments in the interval")
        declared = coverage.get("action_ids")
        expected = {row["action_id"] for row in self._actions if since <= _time(row["effective_at"], "action time") <= at
                    and (not self.closes or _time(row["effective_at"], "action time") > since)}
        if not isinstance(declared, list) or len(set(declared)) != len(declared) or set(declared) != expected:
            raise ShadowAccountingError("declared corporate actions do not reconcile to applied events")
        if coverage.get("unsupported_action_ids") != []:
            raise ShadowAccountingError("unsupported or unclassified corporate actions prevent close")
        quotes = p.get("quotes")
        if not isinstance(quotes, Mapping) or set(quotes) != held:
            raise ShadowAccountingError("a fresh unadjusted quote is required for each open position")
        positions, market_value, unrealized = [], ZERO, ZERO
        for instrument in sorted(held):
            quote = self._quote(quotes[instrument], at, instrument)
            price = _decimal(quote["price"], "mark price", positive=True)
            row = self.positions[instrument]
            value = row["quantity"] * price
            market_value += value
            unrealized += value - row["cost"]
            positions.append({"instrument_id": instrument, "quantity": str(row["quantity"]),
                              "cost": str(row["cost"]), "mark_price": str(price), "market_value": str(value)})
        equity = self.economic_cash + self.dividend_receivable + market_value
        pnl = self.realized_gross + unrealized + self.dividend_income - self.withholding_tax - self.fees
        difference = equity - Decimal(self.config["initial_cash"]) - pnl
        if abs(difference) > TOLERANCE:
            raise ShadowAccountingError("equity does not reconcile to cash, positions and P&L components")
        previous_equity = Decimal(self.closes[-1]["equity"]) if self.closes else Decimal(self.config["initial_cash"])
        external = p.get("external_reconciliation")
        external_status = "UNKNOWN_MISSING_STATEMENT"
        if external is not None:
            if not isinstance(external, Mapping):
                raise ShadowAccountingError("external statement must be an evidence mapping")
            statement_evidence = self._event_evidence(external.get("evidence"), known_by=at)
            if _time(statement_evidence["observed_at"], "statement observed_at") != at:
                raise ShadowAccountingError("external statement evidence must be observed through the close")
            if _time(external.get("asof"), "statement asof") != at:
                raise ShadowAccountingError("external statement must use the same close boundary")
            if (_decimal(external.get("settled_cash"), "statement settled_cash") != self.settled_cash
                    or _decimal(external.get("economic_cash"), "statement economic_cash") != self.economic_cash
                    or _decimal(external.get("dividend_receivable"), "statement dividend_receivable") != self.dividend_receivable):
                raise ShadowAccountingError("external statement cash/receivables do not reconcile")
            quantities = external.get("quantities")
            if not isinstance(quantities, Mapping) or set(quantities) != held or any(
                    _decimal(quantities[key], "statement quantity") != self.positions[key]["quantity"] for key in held):
                raise ShadowAccountingError("external statement positions do not reconcile")
            metrics = {"equity": equity, "market_value": market_value, "available_cash": self.available_cash,
                       "realized_gross": self.realized_gross, "unrealized_gross": unrealized,
                       "fees": self.fees, "dividend_income": self.dividend_income,
                       "withholding_tax": self.withholding_tax, "cumulative_pnl": pnl,
                       "period_pnl": equity - previous_equity}
            if previous_equity > 0:
                metrics["period_return"] = (equity - previous_equity) / previous_equity
            if external.get("currency", "USD") != "USD":
                raise ShadowAccountingError("external statement currency must be USD")
            for key, expected_value in metrics.items():
                if key in external and abs(_decimal(external[key], "statement " + key) - expected_value) > TOLERANCE:
                    raise ShadowAccountingError("external statement " + key + " does not reconcile")
            if "period_return" in external and previous_equity <= 0:
                raise ShadowAccountingError("external period return cannot be verified with a zero opening equity")
            external_status = "MATCHED_DECLARED_STATEMENT" if "equity" in external else "MATCHED_CASH_AND_QUANTITIES_ONLY"
        self.closes.append({"session_id": session_id, "asof": at.isoformat(), "equity": str(equity),
                            "settled_cash": str(self.settled_cash), "economic_cash": str(self.economic_cash),
                            "available_cash": str(self.available_cash), "dividend_receivable": str(self.dividend_receivable),
                            "market_value": str(market_value), "positions": positions,
                            "realized_gross": str(self.realized_gross), "unrealized_gross": str(unrealized),
                            "fees": str(self.fees), "dividend_income": str(self.dividend_income),
                            "withholding_tax": str(self.withholding_tax), "cumulative_pnl": str(pnl),
                            "period_pnl": str(equity - previous_equity),
                            "period_return": str((equity - previous_equity) / previous_equity) if previous_equity > 0 else None,
                            "reconciliation_difference": str(difference), "reconciliation_status": "INTERNALLY_RECONCILED",
                            "external_reconciliation_status": external_status})
        self._interval_instruments = self._economic_instruments()

    def summary(self) -> dict:
        asof = _time(self.events[-1]["effective_at"] if self.events else self.config["opened_at"], "summary asof")
        overdue_trades = sum(_time(row["settlement_at"], "settlement_at") < asof for row in self.pending_trades.values())
        overdue_dividends = sum(not row["paid"] and _time(row["pay_at"], "pay_at") < asof for row in self.dividends.values())
        return {"schema_version": SCHEMA, "book_id": self.config["book_id"],
                "sample_type": self.config["sample_type"], "currency": "USD",
                "execution_status": "SHADOW_ACCOUNTING_ONLY", "validation_status": "NOT VALIDATED",
                "actionability": "NOT ACTIONABLE", "live_trading_supported": False,
                "paper_overnight_enabled": False, "event_count": len(self.events),
                "close_count": len(self.closes), "head_hash": self.head,
                "settled_cash": str(self.settled_cash), "economic_cash": str(self.economic_cash),
                "available_cash": str(self.available_cash), "dividend_receivable": str(self.dividend_receivable),
                "unsettled_trade_count": len(self.pending_trades),
                "overdue_settlement_count": overdue_trades, "overdue_dividend_count": overdue_dividends,
                "lifecycle_status": "OVERDUE_EVIDENCE" if overdue_trades or overdue_dividends else "ACCOUNTING_ONLY",
                "latest_close": deepcopy(self.closes[-1]) if self.closes else None,
                "equity_status": ("CLOSED_AT_STATED_TIME" if self.events and self.events[-1]["kind"] == "CLOSE"
                                  else "LATEST_CLOSE_STALE" if self.closes else "UNKNOWN_NO_RECONCILED_CLOSE"),
                "source_verification": "DECLARED_PROVENANCE_REQUIRES_INDEPENDENT_ARTIFACT_VERIFICATION",
                "limitations": ["Source completeness attestations require independent verification.",
                                "No short, margin, FX, due-bill, cash-in-lieu or live-broker support.",
                                "Accounting fixtures and replay checks do not validate investment returns."]}

    def artifact(self) -> dict:
        return {"schema_version": SCHEMA, "config": deepcopy(self.config),
                "events": deepcopy(self.events), "head_hash": self.head, "summary": self.summary()}

    def write_exclusive(self, path: str | Path) -> str:
        """Freeze a new artifact without overwriting any prior version."""
        data = (_encode(self.artifact()) + "\n").encode()
        destination = Path(path)
        try:
            with destination.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if destination.read_bytes() != data:
                raise ShadowAccountingError("immutable shadow artifact already exists")
        return sha256(data).hexdigest()

    @classmethod
    def replay(cls, artifact: Mapping, *, expected_head: str | None = None) -> "ShadowLedger":
        if not isinstance(artifact, Mapping):
            raise ShadowAccountingError("shadow artifact must be a mapping")
        if artifact.get("schema_version") != SCHEMA:
            raise ShadowAccountingError("unknown shadow artifact schema")
        config = dict(artifact.get("config", {}))
        if config.pop("currency", None) != "USD":
            raise ShadowAccountingError("artifact currency must be USD")
        try:
            ledger = cls(**config)
        except TypeError as exc:
            raise ShadowAccountingError("invalid shadow artifact configuration") from exc
        events = artifact.get("events")
        if not isinstance(events, list):
            raise ShadowAccountingError("shadow events must be a complete ordered list")
        for row in events:
            if not isinstance(row, Mapping):
                raise ShadowAccountingError("shadow journal row must be a mapping")
            if row.get("sequence") != len(ledger.events) + 1:
                raise ShadowAccountingError("shadow journal sequence was duplicated or reordered")
            event = ledger.append(**{key: row[key] for key in ("event_id", "kind", "effective_at", "payload", "recorded_at")})
            if event != row:
                raise ShadowAccountingError("shadow journal chain or event payload was altered")
        if ledger.head != artifact.get("head_hash") or (expected_head is not None and ledger.head != expected_head):
            raise ShadowAccountingError("shadow journal head does not match its expected checkpoint")
        if ledger.summary() != artifact.get("summary"):
            raise ShadowAccountingError("stored shadow summary does not reconcile to the journal")
        return ledger


def inspect_shadow_artifact(path: str | Path, *, expected_head: str | None = None) -> dict:
    """Read-only integration surface; missing/invalid files never become ready."""
    path = Path(path)
    if not path.is_file():
        return {"schema_version": SCHEMA, "status": "UNAVAILABLE", "validation_status": "NOT VALIDATED",
                "paper_overnight_enabled": False, "reason": "No independent shadow lifecycle artifact is available."}
    try:
        raw = path.read_bytes()
        artifact = json.loads(raw)
        ledger = ShadowLedger.replay(artifact, expected_head=expected_head)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"schema_version": SCHEMA, "status": "INVALID_ARTIFACT", "validation_status": "NOT VALIDATED",
                "paper_overnight_enabled": False, "reason": str(exc)}
    return {**ledger.summary(), "status": "REPLAY_VERIFIED", "content_sha256": sha256(raw).hexdigest(),
            "independent_head_verified": expected_head is not None}
