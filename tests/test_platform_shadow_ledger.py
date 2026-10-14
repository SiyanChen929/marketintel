"""Synthetic fixtures only: independently hand-calculated lifecycle boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
import json

import pytest

from market_intel.platform.shadow_ledger import (
    ShadowAccountingError, ShadowLedger, inspect_shadow_artifact,
)

XYZ = "equity:US:XYZ"


def ts(day, hour=20, minute=0, second=0):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=timezone.utc)


def evidence(at, *, available=None):
    return {"source": "synthetic_fixture.hand_calculation", "content_sha256": sha256(str(at).encode()).hexdigest(),
            "observed_at": at.isoformat(), "available_at": (available or at).isoformat(), "finality": "confirmed"}


def quote(at, price="100", instrument=XYZ):
    return {"instrument_id": instrument, "currency": "USD", "price_basis": "unadjusted",
            "price": price, "evidence": evidence(at)}


def ledger(cash="1000"):
    return ShadowLedger(book_id="synthetic-boundary-fixture", opened_at=ts(14, 13), initial_cash=cash,
                        sample_type="synthetic_fixture")


def add(book, event_id, kind, at, payload):
    return book.append(event_id=event_id, kind=kind, effective_at=at, recorded_at=at, payload=payload)


def fill(book, event_id="buy", *, day=14, hour=14, side="BUY", quantity="4", price="100", fee="1", **overrides):
    at = ts(day, hour)
    p = {"fill_id": "source-" + event_id, "instrument_id": XYZ, "run_id": "fixture-run", "order_id": "order-" + event_id,
         "currency": "USD", "asset_class": "equity", "sample_type": "synthetic_fixture", "price_basis": "unadjusted",
         "side": side, "signal_at": (at - timedelta(seconds=2)).isoformat(),
         "order_at": (at - timedelta(seconds=1)).isoformat(), "quote": quote(at, price), "evidence": evidence(at),
         "quantity": quantity, "price": price, "fee": fee, "settlement_at": ts(day + 1, 13, 30).isoformat()}
    p.update(overrides)
    return add(book, event_id, "FILL", at, p)


def settle(book, fill_id, day, amount):
    return add(book, "settle-" + fill_id, "SETTLEMENT", ts(day, 13, 30),
               {"fill_event_id": fill_id, "amount": amount, "currency": "USD", "evidence": evidence(ts(day, 13, 30))})


def split_payload(at=ts(15, 13), **overrides):
    return {"action_id": "split-2-for-1", "instrument_id": XYZ, "currency": "USD", "new_shares": "2",
            "old_shares": "1", "evidence": evidence(at), **overrides}


def dividend_payload(at=ts(15, 12), **overrides):
    return {"action_id": "dividend-1", "instrument_id": XYZ, "currency": "USD", "evidence": evidence(at),
            "entitlement_rule": "pre_ex_position", "distribution_type": "normal_cash", "record_at": ts(15, 20).isoformat(),
            "pay_at": ts(16, 14).isoformat(), "entitled_quantity": "4", "gross_per_share": "2", "withholding": "2", **overrides}


def close_payload(book, day, *, price="102", action_ids=None, external=None):
    at = ts(day)
    held = {key for key, row in book.positions.items() if row["quantity"]}
    required = (held | book._interval_instruments
                | {row["instrument_id"] for row in book.pending_trades.values()}
                | {row["instrument_id"] for row in book.dividends.values() if not row["paid"] and row["net"]})
    return {"session": {"session_id": f"XNYS:2026-09-{day}", "opens_at": ts(day, 13, 30).isoformat(),
                        "closes_at": at.isoformat(), "previous_session_id": book.closes[-1]["session_id"] if book.closes else None,
                        "evidence": evidence(ts(14, 13))},
            "corporate_action_coverage": {"complete": True, "instrument_ids": sorted(required), "action_ids": action_ids or [],
                                          "unsupported_action_ids": [], "from_at": book.closes[-1]["asof"] if book.closes else ts(14, 13).isoformat(),
                                          "through_at": at.isoformat(), "evidence": evidence(at)},
            "quotes": {key: quote(at, price, key) for key in held},
            "external_reconciliation": external}


def close(book, day, **kwargs):
    return add(book, f"close-{day}", "CLOSE", ts(day), close_payload(book, day, **kwargs))


def full_fixture():
    b = ledger()
    fill(b)
    close(b, 14)
    add(b, "ex", "DIVIDEND_EX", ts(15, 12), dividend_payload())
    add(b, "split", "SPLIT", ts(15, 13), split_payload())
    settle(b, "buy", 15, "-400")
    fill(b, "sell-part", day=15, hour=15, side="SELL", quantity="3", price="52", fee="0.5")
    close(b, 15, price="51", action_ids=["dividend-1", "split-2-for-1"])
    settle(b, "sell-part", 16, "156")
    add(b, "pay", "DIVIDEND_PAY", ts(16, 14),
        {"action_id": "dividend-1", "amount": "6", "currency": "USD", "evidence": evidence(ts(16, 14))})
    fill(b, "sell-rest", day=16, hour=15, side="SELL", quantity="5", price="49", fee="0.5")
    close(b, 16)
    settle(b, "sell-rest", 17, "245")
    close(b, 17)
    return b


def test_four_day_manual_reconstruction_buys_sells_fees_dividend_and_split():
    """Values below are arithmetic constants, independently derived in the audit doc."""
    b = full_fixture()
    first, second, third, last = b.closes
    assert D(first["settled_cash"]) == D("999")
    assert D(first["economic_cash"]) == D("599")  # 1000 - 4*100 - 1
    assert D(first["available_cash"]) == D("599")
    assert D(first["equity"]) == D("1007")  # 599 + 4*102
    assert D(second["equity"]) == D("1015.5")  # 754.5 cash + 6 div receivable + 5*51
    assert D(second["realized_gross"]) == D("6")  # 3*(52 - 50)
    assert D(second["unrealized_gross"]) == D("5")
    assert D(second["dividend_receivable"]) == D("6")
    assert D(second["period_pnl"]) == D("8.5")
    assert D(third["equity"]) == D("1005")  # 760.5 - .5 + 245 outstanding settlement
    assert D(third["realized_gross"]) == D("1")  # earlier +6 then 5*(49 - 50)=-5
    assert D(third["period_pnl"]) == D("-10.5")
    assert D(last["settled_cash"]) == D("1005")
    assert D(last["cumulative_pnl"]) == D("5")  # gross gain1 + gross div8 - tax2 - fee2
    assert D(last["fees"]) == D("2")
    assert b.pending_trades == {}
    assert all(D(row["reconciliation_difference"]) == 0 for row in b.closes)
    assert all(row["external_reconciliation_status"] == "UNKNOWN_MISSING_STATEMENT" for row in b.closes)
    assert b.summary()["validation_status"] == "NOT VALIDATED"
    assert b.summary()["paper_overnight_enabled"] is False


def test_split_changes_shares_once_but_not_unsettled_dollars_or_accrued_dividend():
    b = ledger()
    fill(b)
    add(b, "ex", "DIVIDEND_EX", ts(15, 12), dividend_payload())
    add(b, "split", "SPLIT", ts(15, 13), split_payload())
    assert b.positions[XYZ] == {"quantity": D("8"), "cost": D("400")}
    assert b.pending_trades["buy"]["amount"] == D("-400")
    assert b.dividend_receivable == D("6")
    settle(b, "buy", 15, "-400")
    assert b.settled_cash == D("599")
    assert b.positions[XYZ]["quantity"] == D("8")


def test_unsettled_sale_proceeds_cannot_finance_another_purchase():
    b = ledger("402")
    fill(b)
    settle(b, "buy", 15, "-400")
    fill(b, "sell", day=15, hour=15, side="SELL", quantity="4", price="100", fee="0")
    assert b.economic_cash == D("401") and b.available_cash == D("1")
    before = b.artifact()
    with pytest.raises(ShadowAccountingError, match="unsettled proceeds"):
        fill(b, "reinvest", day=15, hour=16, quantity="1", price="100", fee="0")
    assert b.artifact() == before


def test_dividend_receivable_is_retained_after_full_sale():
    b = ledger()
    fill(b)
    add(b, "ex", "DIVIDEND_EX", ts(15, 12), dividend_payload())
    settle(b, "buy", 15, "-400")
    fill(b, "exit", day=15, hour=15, side="SELL", quantity="4", price="99", fee="0")
    assert b.positions[XYZ]["quantity"] == 0
    assert b.dividend_receivable == D("6")
    add(b, "pay", "DIVIDEND_PAY", ts(16, 14),
        {"action_id": "dividend-1", "amount": "6", "currency": "USD", "evidence": evidence(ts(16, 14))})
    assert b.dividend_receivable == 0 and b.dividend_income == D("8")


def test_idempotent_event_and_conflicting_retry_are_atomic():
    b = ledger()
    first = fill(b)
    assert fill(b) == first
    assert len(b.events) == 1
    with pytest.raises(ShadowAccountingError, match="conflicts"):
        fill(b, quantity="5")
    assert b.positions[XYZ]["quantity"] == D("4")
    with pytest.raises(ShadowAccountingError, match="already booked"):
        fill(b, "another-operation", hour=15, fill_id="source-buy")


@pytest.mark.parametrize("patch,match", [
    ({"price_basis": "adjusted"}, "unadjusted"),
    ({"currency": "EUR"}, "USD"),
    ({"quantity": "NaN"}, "quantity"),
    ({"fee": None}, "fee"),
    ({"fee": -1}, "fee"),
    ({"price": 1.5}, "decimal string"),
    ({"sample_type": "observed_paper"}, "sample_type"),
    ({"order_at": ts(14, 14, 0, 1).isoformat()}, "clock"),
    ({"signal_at": datetime(2026, 9, 14).isoformat()}, "timezone-aware"),
    ({"settlement_at": ts(14, 13).isoformat()}, "settlement"),
])
def test_malformed_fill_evidence_fails_closed(patch, match):
    b = ledger()
    with pytest.raises(ShadowAccountingError, match=match):
        fill(b, **patch)
    assert not b.events and not b.positions and b.settled_cash == D("1000")


def test_short_and_unknown_fee_cannot_create_a_book():
    with pytest.raises(ShadowAccountingError, match="shorts"):
        fill(ledger(), side="SELL")


@pytest.mark.parametrize("change,match", [
    (lambda p: p.pop("corporate_action_coverage"), "coverage is unknown"),
    (lambda p: p["corporate_action_coverage"].update(complete=False), "coverage is unknown"),
    (lambda p: p["corporate_action_coverage"].update(instrument_ids=[]), "held/traded"),
    (lambda p: p["corporate_action_coverage"].update(action_ids=["missing-dividend"]), "applied events"),
    (lambda p: p["corporate_action_coverage"].update(unsupported_action_ids=["merger"]), "unsupported"),
    (lambda p: p["corporate_action_coverage"].update(through_at=ts(14, 19).isoformat()), "interval"),
    (lambda p: p["corporate_action_coverage"].update(evidence=evidence(ts(14, 19))), "observed through"),
    (lambda p: p["quotes"].clear(), "fresh unadjusted"),
    (lambda p: p["session"].update(previous_session_id="missing-session"), "session chain"),
])
def test_incomplete_lifecycle_coverage_cannot_publish_a_close(change, match):
    b = ledger()
    fill(b)
    p = close_payload(b, 14)
    change(p)
    before = b.artifact()
    with pytest.raises(ShadowAccountingError, match=match):
        add(b, "bad-close", "CLOSE", ts(14), p)
    assert b.artifact() == before and not b.closes


@pytest.mark.parametrize("raw,match", [
    (quote(ts(14, 19, 54)), "stale"),
    (quote(ts(14, 20, 0, 1)), "unavailable"),
    ({**quote(ts(14)), "price_basis": "adjusted"}, "double-count"),
    ({**quote(ts(14)), "evidence": evidence(ts(14), available=ts(14, 20, 0, 1))}, "unavailable"),
    ({**quote(ts(14)), "instrument_id": "equity:US:OTHER"}, "mismatch"),
])
def test_stale_future_adjusted_or_wrong_security_marks_reject(raw, match):
    b = ledger()
    fill(b)
    p = close_payload(b, 14)
    p["quotes"][XYZ] = raw
    with pytest.raises(ShadowAccountingError, match=match):
        add(b, "bad-close", "CLOSE", ts(14), p)


def test_future_action_evidence_does_not_change_prior_equity():
    b = ledger()
    fill(b)
    p = dividend_payload(evidence=evidence(ts(15, 12), available=ts(15, 13)))
    with pytest.raises(ShadowAccountingError, match="unavailable"):
        add(b, "future-dividend", "DIVIDEND_EX", ts(15, 12), p)
    assert b.dividend_receivable == 0 and b.dividend_income == 0
    close(b, 14)
    with pytest.raises(ShadowAccountingError, match="sealed"):
        add(b, "backdated-dividend", "DIVIDEND_EX", ts(14, 19), dividend_payload(at=ts(14, 19)))


def test_split_fractional_cash_in_lieu_unknown_and_same_action_twice_fail():
    b = ledger()
    fill(b, quantity="3")
    with pytest.raises(ShadowAccountingError, match="cash-in-lieu"):
        add(b, "fractional", "SPLIT", ts(15, 13), split_payload(new_shares="1", old_shares="2"))
    assert b.positions[XYZ]["quantity"] == D("3")
    add(b, "split", "SPLIT", ts(15, 13), split_payload())
    with pytest.raises(ShadowAccountingError, match="already applied"):
        add(b, "split-again", "SPLIT", ts(15, 14), split_payload(at=ts(15, 14)))


def test_post_split_quotes_require_current_share_basis():
    b = ledger()
    fill(b)
    add(b, "split", "SPLIT", ts(15, 20), split_payload(at=ts(15, 20)))
    p = close_payload(b, 15, action_ids=["split-2-for-1"])
    p["quotes"][XYZ] = quote(ts(15, 19, 59, 59))
    with pytest.raises(ShadowAccountingError, match="share-basis"):
        add(b, "bad-split-close", "CLOSE", ts(15), p)


@pytest.mark.parametrize("patch,match", [
    ({"entitled_quantity": "3"}, "pre-ex position"),
    ({"withholding": "9"}, "exceeds"),
    ({"distribution_type": "special_cash"}, "normal-cash"),
    ({"record_at": ts(14).isoformat()}, "chronology"),
])
def test_dividend_entitlement_and_taxes_fail_closed(patch, match):
    b = ledger()
    fill(b)
    with pytest.raises(ShadowAccountingError, match=match):
        add(b, "ex", "DIVIDEND_EX", ts(15, 12), dividend_payload(**patch))
    assert b.dividend_receivable == 0


def test_payment_cannot_be_early_or_repeated_under_a_new_id():
    b = ledger()
    fill(b)
    add(b, "ex", "DIVIDEND_EX", ts(15, 12), dividend_payload())
    p = {"action_id": "dividend-1", "amount": "6", "currency": "USD", "evidence": evidence(ts(15, 13))}
    with pytest.raises(ShadowAccountingError, match="before pay_at"):
        add(b, "pay-early", "DIVIDEND_PAY", ts(15, 13), p)
    p["evidence"] = evidence(ts(16, 14))
    add(b, "pay", "DIVIDEND_PAY", ts(16, 14), p)
    with pytest.raises(ShadowAccountingError, match="already paid"):
        add(b, "pay-again", "DIVIDEND_PAY", ts(16, 14), p)


def test_exact_external_cash_and_quantity_statement_reconciliation():
    b = ledger()
    fill(b)
    statement = {"asof": ts(14).isoformat(), "settled_cash": "999", "economic_cash": "599",
                 "dividend_receivable": "0", "quantities": {XYZ: "4"}, "evidence": evidence(ts(14))}
    p = close_payload(b, 14, external=statement)
    p["external_reconciliation"]["settled_cash"] = "998"
    with pytest.raises(ShadowAccountingError, match="statement cash"):
        add(b, "close-bad", "CLOSE", ts(14), p)
    statement["settled_cash"] = "999"
    close(b, 14, external=statement)
    assert b.closes[-1]["external_reconciliation_status"] == "MATCHED_CASH_AND_QUANTITIES_ONLY"


def test_full_replay_hash_chain_expected_head_and_immutable_artifact(tmp_path):
    b = full_fixture()
    path = tmp_path / "fixture.json"
    digest = b.write_exclusive(path)
    assert b.write_exclusive(path) == digest
    report = inspect_shadow_artifact(path, expected_head=b.head)
    assert report["status"] == "REPLAY_VERIFIED"
    assert report["sample_type"] == "synthetic_fixture"
    assert report["independent_head_verified"] is True
    assert report["content_sha256"] == digest
    rebuilt = ShadowLedger.replay(json.loads(path.read_text()), expected_head=b.head)
    assert rebuilt.artifact() == b.artifact()
    with pytest.raises(ShadowAccountingError, match="immutable"):
        ledger().write_exclusive(path)
    data = b.artifact()
    data["events"][0]["payload"]["quantity"] = "5"
    with pytest.raises(ShadowAccountingError, match="altered"):
        ShadowLedger.replay(data)
    with pytest.raises(ShadowAccountingError, match="checkpoint"):
        ShadowLedger.replay(b.artifact(), expected_head="f" * 64)


def test_readonly_readiness_never_invents_a_ledger_and_fails_closed(tmp_path):
    path = tmp_path / "missing.json"
    assert inspect_shadow_artifact(path)["status"] == "UNAVAILABLE"
    assert not path.exists()
    path.write_text("not json")
    assert inspect_shadow_artifact(path)["status"] == "INVALID_ARTIFACT"


def test_events_after_close_do_not_claim_fresh_equity():
    b = ledger()
    fill(b)
    close(b, 14)
    assert b.summary()["equity_status"] == "CLOSED_AT_STATED_TIME"
    settle(b, "buy", 15, "-400")
    assert b.summary()["equity_status"] == "LATEST_CLOSE_STALE"


def test_duplicate_journal_row_and_tail_truncation_fail_expected_checkpoint():
    b = full_fixture()
    duplicate = b.artifact()
    duplicate["events"].append(deepcopy(duplicate["events"][-1]))
    with pytest.raises(ShadowAccountingError, match="sequence"):
        ShadowLedger.replay(duplicate)
    shortened = ledger()
    fill(shortened)
    # A locally self-consistent truncated journal still cannot satisfy an
    # independently retained full-journal checkpoint.
    with pytest.raises(ShadowAccountingError, match="checkpoint"):
        ShadowLedger.replay(shortened.artifact(), expected_head=b.head)


def test_action_same_boundary_cannot_capture_post_ex_purchase():
    b = ledger()
    fill(b)
    at = ts(14, 14)
    with pytest.raises(ShadowAccountingError, match="precede fills"):
        add(b, "ex-after-fill", "DIVIDEND_EX", at, dividend_payload(at=at))
    assert b.dividend_receivable == 0


def test_no_fee_double_count_when_observed_fill_price_contains_execution_cost():
    b = ledger()
    fill(b, price="101", fee="2")
    close(b, 14, price="100")
    # 1000 - 4*101 - 2 + 4*100 = 994. Spread/slippage is already in 101.
    assert D(b.closes[-1]["equity"]) == D("994")
    assert D(b.closes[-1]["fees"]) == D("2")


def test_unsettled_trade_and_dividend_double_payment_do_not_create_cash():
    b = ledger()
    fill(b)
    with pytest.raises(ShadowAccountingError, match="does not reconcile"):
        settle(b, "buy", 15, "400")
    assert b.settled_cash == D("999")
    settle(b, "buy", 15, "-400")
    with pytest.raises(ShadowAccountingError, match="already settled"):
        add(b, "settle-buy-twice", "SETTLEMENT", ts(15, 14),
            {"fill_event_id": "buy", "amount": "-400", "currency": "USD", "evidence": evidence(ts(15, 14))})
    assert b.settled_cash == D("599")


@pytest.mark.parametrize("key", ["equity", "market_value", "available_cash", "realized_gross", "unrealized_gross",
                               "fees", "dividend_income", "withholding_tax", "cumulative_pnl", "period_pnl", "period_return"])
def test_optional_statement_metrics_cannot_hide_a_contradiction(key):
    b = ledger()
    fill(b)
    statement = {"asof": ts(14).isoformat(), "settled_cash": "999", "economic_cash": "599",
                 "dividend_receivable": "0", "quantities": {XYZ: "4"}, "evidence": evidence(ts(14)), key: "999999"}
    with pytest.raises(ShadowAccountingError, match="statement " + key):
        close(b, 14, external=statement)
    assert not b.closes


def test_external_equity_match_is_distinguished_from_cash_and_quantity_only():
    b = ledger()
    fill(b)
    statement = {"asof": ts(14).isoformat(), "settled_cash": "999", "economic_cash": "599", "equity": "1007",
                 "dividend_receivable": "0", "quantities": {XYZ: "4"}, "evidence": evidence(ts(14))}
    close(b, 14, external=statement)
    assert b.closes[-1]["external_reconciliation_status"] == "MATCHED_DECLARED_STATEMENT"


def test_action_coverage_includes_unpaid_dividend_after_shares_and_trade_proceeds_exit():
    b = ledger()
    fill(b)
    add(b, "ex", "DIVIDEND_EX", ts(15, 12), dividend_payload(pay_at=ts(18, 14).isoformat()))
    settle(b, "buy", 15, "-400")
    fill(b, "exit", day=15, hour=15, side="SELL", quantity="4", price="99", fee="0")
    close(b, 15, action_ids=["dividend-1"])
    settle(b, "exit", 16, "396")
    close(b, 16)
    p = close_payload(b, 17)
    p["corporate_action_coverage"]["instrument_ids"] = []
    with pytest.raises(ShadowAccountingError, match="receivable instruments"):
        add(b, "close-without-receivable-coverage", "CLOSE", ts(17), p)
    assert b.dividend_receivable == D("6")
