from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal as D
from hashlib import sha256
import json

import pytest

import market_intel.platform.paper as paper_module

from market_intel.platform.paper import (
    CorporateActionUnsupported,
    CostModel,
    DuplicateOrderError,
    ExecutionMode,
    InsufficientCash,
    KillSwitchActive,
    LedgerIntegrityError,
    MandateViolation,
    Order,
    OrderRejected,
    OrderStatus,
    PaperBroker,
    PaperMandate,
    PaperSessionBinding,
    Quote,
    QuoteLineage,
    QuoteRejected,
    Side,
    ValidationError,
)


T0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
RUN_ID = "run-paper-fixture"
CONTEXT_HASH = "a" * 64


def _broker(tmp_path, *, costs=None, **mandate_kwargs):
    mandate = PaperMandate(
        starting_cash=D("1000"), costs=costs or CostModel(), **mandate_kwargs,
    )
    return PaperBroker(tmp_path / "paper.jsonl", mandate=mandate, opened_at=T0)


def _submit(broker, side, quantity, offset, *, client_order_id=""):
    return broker.submit_order(
        symbol="XYZ", side=side, quantity=D(quantity), strategy_id="manual-fixture",
        run_id=RUN_ID, context_hash=CONTEXT_HASH,
        instrument_id="equity:US:XYZ", asset_class="equity", currency="USD",
        multiplier="1",
        signal_time=T0 + timedelta(seconds=offset),
        order_time=T0 + timedelta(seconds=offset + 1),
        client_order_id=client_order_id,
    )


def _quote(offset, bid="9.90", ask="10.10", available=None):
    return Quote(
        "XYZ", T0 + timedelta(seconds=offset), D(bid), D(ask),
        None if available is None else D(available),
    )


def _lineage(quote, *, acquired_at=None):
    acquired = acquired_at or quote.timestamp
    return QuoteLineage(
        symbol=quote.symbol,
        observed_at=quote.timestamp,
        acquired_at=acquired,
        finality="confirmed",
        source="fixture.quote",
        content_hash=sha256(str(quote.to_dict()).encode()).hexdigest(),
        instrument_id=f"equity:US:{quote.symbol}",
        dataset_version_id="b" * 64,
        request_hash="c" * 64,
        currency="USD",
    )


def _fill(broker, order_id, quote, **kwargs):
    return broker.fill_order(
        order_id, quote, quote_lineage=_lineage(quote), **kwargs
    )


def test_manual_buy_sell_reconstruction_includes_all_costs(tmp_path):
    """Independent fixture: unchanged mid, so the net loss must equal costs."""
    costs = CostModel(
        commission_fixed=D("0.50"), commission_per_unit=D("0.05"),
        spread_bps=D("0"), slippage_bps=D("10"),
    )
    broker = _broker(tmp_path, costs=costs)

    buy = _submit(broker, Side.BUY, "4", 1)
    buy_fill = _fill(broker, buy.order_id, _quote(3))
    sell = _submit(broker, Side.SELL, "4", 4)
    sell_fill = _fill(broker, sell.order_id, _quote(6))

    # mid=10; observed half-spread=.10; slippage=.01; commission=.50+.05*4=.70
    assert buy_fill.price == D("10.11")
    assert sell_fill.price == D("9.89")
    assert buy_fill.commission == sell_fill.commission == D("0.70")
    assert buy_fill.spread_cost == sell_fill.spread_cost == D("0.40")
    assert buy_fill.slippage_cost == sell_fill.slippage_cost == D("0.04")
    assert broker.cash == D("997.72")  # 1000 - 4*10.11 - .70 + 4*9.89 - .70
    assert broker.positions["XYZ"].quantity == D("0")
    assert broker.positions["XYZ"].realized_pnl == D("-0.88")
    assert broker.snapshot()["costs"]["total"] == "2.28"
    assert broker.reconcile()["ok"] is True


def test_partial_fills_close_one_order_without_creating_cash(tmp_path):
    broker = _broker(tmp_path)
    order = _submit(broker, "BUY", "5", 1)

    first = _fill(broker, order.order_id, _quote(3, bid="10", ask="10", available="2"))
    assert first.quantity == D("2")
    assert broker.orders[order.order_id].status is OrderStatus.PARTIALLY_FILLED
    assert broker.orders[order.order_id].remaining_quantity == D("3")

    second = _fill(broker, order.order_id, _quote(4, bid="11", ask="11", available="9"))
    assert second.quantity == D("3")
    assert broker.orders[order.order_id].status is OrderStatus.FILLED
    assert broker.cash == D("947")
    assert broker.positions["XYZ"].quantity == D("5")
    assert broker.positions["XYZ"].average_cost == D("10.6")
    assert broker.reconcile()["ok"] is True


def test_deterministic_order_id_and_duplicate_submission_are_idempotent(tmp_path):
    broker = _broker(tmp_path)
    first = _submit(broker, "BUY", "1", 1)
    event_count = len(broker.events)
    duplicate = _submit(broker, "BUY", "1", 1)

    assert duplicate == first
    assert first.client_order_id.startswith("coid_")
    assert first.order_id.startswith("ord_")
    assert first.run_id == RUN_ID and first.context_hash == CONTEXT_HASH
    assert len(broker.events) == event_count

    conflicting = Order(
        symbol="XYZ", side="BUY", quantity=D("2"), strategy_id="manual-fixture",
        run_id=RUN_ID, context_hash=CONTEXT_HASH,
        instrument_id="equity:US:XYZ", asset_class="equity", currency="USD",
        multiplier="1",
        signal_time=T0 + timedelta(seconds=1), order_time=T0 + timedelta(seconds=2),
        client_order_id=first.client_order_id,
    )
    with pytest.raises(DuplicateOrderError):
        broker.submit_order(conflicting)

    other_context = Order(
        symbol="XYZ", side="BUY", quantity=D("1"), strategy_id="manual-fixture",
        run_id=RUN_ID, context_hash="b" * 64,
        instrument_id="equity:US:XYZ", asset_class="equity", currency="USD",
        multiplier="1",
        signal_time=T0 + timedelta(seconds=1), order_time=T0 + timedelta(seconds=2),
        client_order_id=first.client_order_id,
    )
    with pytest.raises(DuplicateOrderError):
        broker.submit_order(other_context)
    replayed = PaperBroker.replay(broker.store.path)
    persisted = replayed.snapshot()["orders"][0]
    assert persisted["run_id"] == RUN_ID and persisted["context_hash"] == CONTEXT_HASH


def test_stale_missing_future_and_preorder_quotes_fail_closed(tmp_path):
    broker = _broker(tmp_path, max_quote_age=timedelta(seconds=2))
    order = _submit(broker, "BUY", "1", 1)

    with pytest.raises(QuoteRejected, match="required"):
        broker.fill_order(order.order_id, None)
    with pytest.raises(QuoteRejected, match="predates"):
        _fill(broker, order.order_id, _quote(1), fill_time=T0 + timedelta(seconds=2))
    with pytest.raises(QuoteRejected, match="stale"):
        _fill(broker, order.order_id, _quote(3), fill_time=T0 + timedelta(seconds=6))
    with pytest.raises(QuoteRejected, match="future"):
        _fill(broker, order.order_id, _quote(5), fill_time=T0 + timedelta(seconds=4))
    assert broker.cash == D("1000")
    assert not broker.fills


def test_timezone_and_event_timeline_are_enforced(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(ValidationError, match="timezone-aware"):
        broker.submit_order(
            symbol="XYZ", side="BUY", quantity="1",
            run_id=RUN_ID, context_hash=CONTEXT_HASH,
            instrument_id="equity:US:XYZ", asset_class="equity", currency="USD",
            multiplier="1",
            signal_time=datetime(2026, 1, 2, 9, 30),
            order_time=datetime(2026, 1, 2, 9, 31),
        )
    with pytest.raises(ValidationError, match="strictly before"):
        broker.submit_order(
            symbol="XYZ", side="BUY", quantity="1", signal_time=T0, order_time=T0,
            run_id=RUN_ID, context_hash=CONTEXT_HASH,
            instrument_id="equity:US:XYZ", asset_class="equity", currency="USD",
            multiplier="1",
        )


def test_cash_short_and_leverage_mandates_reject_before_fill(tmp_path):
    cash_broker = _broker(tmp_path / "cash")
    too_large = _submit(cash_broker, "BUY", "101", 1)
    with pytest.raises(InsufficientCash):
        _fill(cash_broker, too_large.order_id, _quote(3, bid="10", ask="10"))

    short_broker = _broker(tmp_path / "short")
    short = _submit(short_broker, "SELL", "1", 1)
    with pytest.raises(MandateViolation, match="short"):
        _fill(short_broker, short.order_id, _quote(3, bid="10", ask="10"))

    leveraged = _broker(
        tmp_path / "leverage", max_gross_leverage=D("0.5"),
    )
    order = _submit(leveraged, "BUY", "60", 1)
    with pytest.raises(MandateViolation, match="leverage"):
        _fill(leveraged, order.order_id, _quote(3, bid="10", ask="10"))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"allow_short": True}, "borrow/locate"),
        ({"allow_margin": True}, "financing and collateral"),
        ({"currency": "EUR"}, "USD cash equities"),
    ],
)
def test_mandate_fails_closed_on_unsupported_accounting_modes(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        PaperMandate(**kwargs)


def test_persisted_mandate_boolean_flags_are_not_truthiness_coerced():
    payload = PaperMandate().to_dict()
    payload["allow_short"] = 0
    with pytest.raises(ValidationError, match="allow_short must be a boolean"):
        PaperMandate.from_dict(payload)


def test_one_symbol_cannot_alias_two_instruments_inside_a_session(tmp_path):
    broker = _broker(tmp_path)
    _submit(broker, "BUY", "1", 1)
    with pytest.raises(OrderRejected, match="another immutable instrument identity"):
        broker.submit_order(
            symbol="XYZ",
            side="BUY",
            quantity="1",
            strategy_id="manual-fixture",
            run_id=RUN_ID,
            context_hash=CONTEXT_HASH,
            instrument_id="equity:US:OTHER",
            asset_class="equity",
            currency="USD",
            multiplier="1",
            signal_time=T0 + timedelta(seconds=3),
            order_time=T0 + timedelta(seconds=4),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"instrument_id": "", "asset_class": "", "currency": ""}, "bare-symbol"),
        (
            {
                "instrument_id": "future:CME:ESZ6",
                "asset_class": "future",
                "currency": "USD",
                "multiplier": "50",
            },
            "futures/options require expiry",
        ),
        (
            {
                "instrument_id": "equity:US:XYZ",
                "asset_class": "equity",
                "currency": "USD",
                "multiplier": "50",
            },
            "unit-multiplier",
        ),
        (
            {
                "instrument_id": "equity:US:XYZ",
                "asset_class": "equity",
                "currency": "EUR",
                "multiplier": "1",
            },
            "USD cash equities",
        ),
        (
            {
                "instrument_id": "equity:US:XYZ",
                "asset_class": "equity",
                "currency": "USD",
                "multiplier": "1",
                "expiry": T0.date(),
                "contract_code": "XYZ6",
            },
            "expiry/contract identity",
        ),
    ],
)
def test_order_requires_explicit_supported_cash_equity_identity(tmp_path, overrides, message):
    broker = _broker(tmp_path)
    kwargs = {
        "symbol": "XYZ",
        "side": "BUY",
        "quantity": "1",
        "strategy_id": "manual-fixture",
        "run_id": RUN_ID,
        "context_hash": CONTEXT_HASH,
        "signal_time": T0 + timedelta(seconds=1),
        "order_time": T0 + timedelta(seconds=2),
        **overrides,
    }
    with pytest.raises(ValidationError, match=message):
        broker.submit_order(**kwargs)
    assert len(broker.events) == 1


def test_mark_to_market_and_replay_are_bit_for_bit_deterministic(tmp_path):
    path = tmp_path / "paper.jsonl"
    broker = PaperBroker(
        path, mode=ExecutionMode.SHADOW,
        mandate=PaperMandate(starting_cash=D("1000")), opened_at=T0,
        session_id="shadow-replay-fixture",
    )
    order = _submit(broker, "BUY", "10", 1)
    _fill(broker, order.order_id, _quote(3, bid="10", ask="10"))
    mark = _quote(4, bid="12", ask="12")
    broker.mark_to_market(
        {"XYZ": mark}, asof=T0 + timedelta(seconds=4),
        quote_lineage={"XYZ": _lineage(mark)},
    )
    before = broker.snapshot(asof=T0 + timedelta(seconds=4))

    replayed = PaperBroker.replay(path)
    after = replayed.snapshot(asof=T0 + timedelta(seconds=4))
    assert after == before
    assert after["mode"] == "SHADOW"
    assert after["equity"] == "1020"
    assert after["validation_status"] == "NOT VALIDATED"
    assert after["actionability"] == "NOT ACTIONABLE"
    assert after["live_trading_supported"] is False


def test_kill_switch_persists_and_blocks_orders_and_fills(tmp_path):
    path = tmp_path / "paper.jsonl"
    broker = PaperBroker(path, mandate=PaperMandate(starting_cash=D("1000")), opened_at=T0)
    order = _submit(broker, "BUY", "1", 1)
    broker.activate_kill_switch("data lineage mismatch", activated_at=T0 + timedelta(seconds=3))

    with pytest.raises(KillSwitchActive):
        _fill(broker, order.order_id, _quote(4))
    with pytest.raises(KillSwitchActive):
        _submit(broker, "BUY", "1", 5)
    replayed = PaperBroker.replay(path)
    assert replayed.kill_switch_active is True
    assert replayed.kill_switch_reason == "data lineage mismatch"


def test_corporate_action_hook_fails_closed_and_engages_kill_switch(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(CorporateActionUnsupported):
        broker.on_corporate_action(
            symbol="XYZ", action_type="SPLIT", effective_at=T0 + timedelta(seconds=1),
            details={"ratio": "2:1"},
        )
    assert broker.kill_switch_active is True
    assert "SPLIT" in broker.kill_switch_reason


def test_store_detects_tampering_instead_of_replaying_it(tmp_path):
    path = tmp_path / "paper.jsonl"
    broker = PaperBroker(path, mandate=PaperMandate(starting_cash=D("1000")), opened_at=T0)
    _submit(broker, "BUY", "1", 1)
    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[1])
    event["payload"]["order"]["quantity"] = "999"
    lines[1] = json.dumps(event)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="hash mismatch"):
        PaperBroker.replay(path)


def test_status_and_snapshot_are_json_safe_and_never_actionable(tmp_path):
    broker = _broker(tmp_path)
    payload = broker.to_dict()
    encoded = json.dumps(payload)
    assert "NOT VALIDATED" in encoded
    assert "NOT ACTIONABLE" in encoded
    assert payload["virtual_broker_only"] is True
    assert payload["mode"] == "PAPER"
    assert payload["status"] == "NOT VALIDATED/PAPER"
    assert payload["execution_status"] == "PAPER"


def test_live_mode_has_no_construction_path(tmp_path):
    with pytest.raises(ValidationError, match="PAPER or SHADOW"):
        PaperBroker(tmp_path / "live.jsonl", mode="LIVE", opened_at=T0)


def test_same_instant_with_different_offsets_is_one_idempotent_order(tmp_path):
    broker = _broker(tmp_path)
    utc_order = _submit(broker, "BUY", "1", 1)
    eastern = timezone(timedelta(hours=-5))
    duplicate = broker.submit_order(
        symbol="XYZ",
        side="BUY",
        quantity="1",
        strategy_id="manual-fixture",
        run_id=RUN_ID,
        context_hash=CONTEXT_HASH,
        instrument_id="equity:US:XYZ",
        asset_class="equity",
        currency="USD",
        multiplier="1",
        signal_time=(T0 + timedelta(seconds=1)).astimezone(eastern),
        order_time=(T0 + timedelta(seconds=2)).astimezone(eastern),
    )
    assert duplicate.order_id == utc_order.order_id
    assert len(broker.snapshot()["orders"]) == 1


def test_quote_liquidity_cannot_be_reused_across_fill_calls(tmp_path):
    broker = _broker(tmp_path)
    order = _submit(broker, "BUY", "2", 1)
    quote = _quote(3, bid="10", ask="10", available="1")
    first = _fill(broker, order.order_id, quote)
    assert first.quantity == D("1")
    with pytest.raises(QuoteRejected, match="no available"):
        _fill(broker, order.order_id, quote)
    assert broker.positions["XYZ"].quantity == D("1")


def test_fill_and_mark_require_and_persist_confirmed_quote_lineage(tmp_path):
    broker = _broker(tmp_path)
    order = _submit(broker, "BUY", "1", 1)
    quote = _quote(3, bid="10", ask="10")
    with pytest.raises(QuoteRejected, match="lineage"):
        broker.fill_order(order.order_id, quote)
    fill = _fill(broker, order.order_id, quote)
    assert fill.quote_lineage.source == "fixture.quote"
    replayed = PaperBroker.replay(broker.store.path)
    assert replayed.fills[0].quote_lineage.to_dict() == fill.quote_lineage.to_dict()


def test_fill_rejects_quote_lineage_bound_to_another_instrument(tmp_path):
    broker = _broker(tmp_path)
    order = _submit(broker, "BUY", "1", 1)
    quote = _quote(3, bid="10", ask="10")
    with pytest.raises(QuoteRejected, match="instrument/currency"):
        broker.fill_order(
            order.order_id,
            quote,
            quote_lineage=replace(
                _lineage(quote), instrument_id="equity:US:OTHER"
            ),
        )
    assert broker.snapshot()["event_count"] == 2


def test_external_head_detects_valid_hash_chain_tail_truncation(tmp_path):
    broker = _broker(tmp_path)
    order = _submit(broker, "BUY", "1", 1)
    _fill(broker, order.order_id, _quote(3, bid="10", ask="10"))
    lines = broker.store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    broker.store.path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(LedgerIntegrityError, match="external head"):
        PaperBroker.replay(broker.store.path)


def test_snapshot_cannot_read_future_marks_and_discloses_mark_time(tmp_path):
    broker = _broker(tmp_path)
    order = _submit(broker, "BUY", "1", 1)
    _fill(broker, order.order_id, _quote(3, bid="10", ask="10"))
    with pytest.raises(ValidationError, match="predates the ledger head"):
        broker.snapshot(asof=T0 + timedelta(seconds=2))
    row = broker.snapshot(asof=T0 + timedelta(seconds=3))["positions"][0]
    assert row["mark_time"] == (T0 + timedelta(seconds=3)).isoformat()
    assert row["mark_status"] == "current"


def test_stale_existing_marks_cannot_bypass_pretrade_leverage_check(tmp_path):
    broker = _broker(
        tmp_path,
        max_quote_age=timedelta(seconds=2),
        max_gross_leverage=D("2"),
    )
    first = _submit(broker, "BUY", "50", 1)
    _fill(broker, first.order_id, _quote(3, bid="10", ask="10"))
    second = broker.submit_order(
        symbol="ABC",
        side="BUY",
        quantity="1",
        strategy_id="manual-fixture",
        run_id=RUN_ID,
        context_hash=CONTEXT_HASH,
        instrument_id="equity:US:ABC",
        asset_class="equity",
        currency="USD",
        multiplier="1",
        signal_time=T0 + timedelta(seconds=98),
        order_time=T0 + timedelta(seconds=99),
    )
    quote = Quote("ABC", T0 + timedelta(seconds=100), D("10"), D("10"))
    with pytest.raises(MandateViolation, match="stale marks for XYZ"):
        broker.fill_order(
            second.order_id,
            quote,
            quote_lineage=_lineage(quote),
        )


def test_replay_never_creates_a_replacement_session_from_an_empty_store(tmp_path):
    path = tmp_path / "missing.jsonl"
    with pytest.raises(LedgerIntegrityError, match="empty"):
        PaperBroker.replay(path)
    assert not path.exists()


def test_initial_generation_crash_rolls_back_and_replay_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "initial-crash.jsonl"
    real_replace = paper_module.os.replace

    def crash_after_ledger_replace(source, target):
        if str(source).endswith(".txn.new") and target == path:
            real_replace(source, target)
            raise SystemExit("simulated process death")
        return real_replace(source, target)

    monkeypatch.setattr(paper_module.os, "replace", crash_after_ledger_replace)
    with pytest.raises(SystemExit, match="simulated"):
        PaperBroker.start_session(
            path,
            mandate=PaperMandate(starting_cash=D("1234")),
            session_id="requested_session",
            opened_at=T0,
        )
    monkeypatch.setattr(paper_module.os, "replace", real_replace)

    with pytest.raises(LedgerIntegrityError, match="empty"):
        PaperBroker.replay(path)
    assert not path.exists()
    assert not path.with_name(path.name + ".head").exists()


def test_later_generation_crash_recovers_previous_complete_head(tmp_path, monkeypatch):
    broker = _broker(tmp_path)
    real_replace = paper_module.os.replace

    def crash_after_ledger_replace(source, target):
        if str(source).endswith(".txn.new") and target == broker.store.path:
            real_replace(source, target)
            raise SystemExit("simulated process death")
        return real_replace(source, target)

    monkeypatch.setattr(paper_module.os, "replace", crash_after_ledger_replace)
    with pytest.raises(SystemExit, match="simulated"):
        _submit(broker, "BUY", "1", 1)
    monkeypatch.setattr(paper_module.os, "replace", real_replace)

    recovered = PaperBroker.replay(broker.store.path)
    assert len(recovered.events) == 1
    assert recovered.orders == {}


def test_stale_broker_cannot_overwrite_a_concurrent_generation(tmp_path):
    broker = _broker(tmp_path)
    stale = PaperBroker.replay(broker.store.path)
    accepted = _submit(broker, "BUY", "1", 1)

    with pytest.raises(LedgerIntegrityError, match="does not continue"):
        stale.activate_kill_switch("stale writer", activated_at=T0 + timedelta(seconds=3))

    replayed = PaperBroker.replay(broker.store.path)
    assert set(replayed.orders) == {accepted.order_id}
    assert replayed.kill_switch_active is False


def test_semantically_forged_fill_is_rejected_even_with_rehashed_head(tmp_path):
    broker = _broker(tmp_path)
    order = _submit(broker, "BUY", "1", 1)
    _fill(broker, order.order_id, _quote(3, bid="10", ask="10"))
    lines = [json.loads(line) for line in broker.store.path.read_text().splitlines()]
    forged = lines[-1]
    forged["payload"]["fill"]["gross_notional"] = "1000"
    forged["payload"]["cash_event"]["amount"] = "-1000"
    forged["payload"]["cash_event"]["balance_after"] = "0"
    body = {key: value for key, value in forged.items() if key != "event_hash"}
    forged["event_hash"] = sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    broker.store.path.write_text(
        "\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in lines)
        + "\n"
    )
    broker.store.head_path.write_text(
        json.dumps({"sequence": forged["sequence"], "event_hash": forged["event_hash"]}) + "\n"
    )

    with pytest.raises((LedgerIntegrityError, ValidationError), match="gross_notional"):
        PaperBroker.replay(broker.store.path)


def test_fixed_session_binding_rejects_another_strategy_before_append(tmp_path):
    binding = PaperSessionBinding("workspace", "stock_select", "portfolio-a")
    broker = PaperBroker.start_session(
        tmp_path / "bound.jsonl",
        mandate=PaperMandate(starting_cash=D("1000")),
        binding=binding,
        opened_at=T0,
    )
    before = len(broker.events)
    with pytest.raises(OrderRejected, match="fixed paper session binding"):
        broker.submit_order(
            symbol="XYZ",
            side="BUY",
            quantity="1",
            strategy_id="pairs_trading",
            run_id=RUN_ID,
            context_hash=CONTEXT_HASH,
            instrument_id="equity:US:XYZ",
            asset_class="equity",
            currency="USD",
            multiplier="1",
            signal_time=T0 + timedelta(seconds=1),
            order_time=T0 + timedelta(seconds=2),
        )
    assert len(PaperBroker.replay(broker.store.path).events) == before
