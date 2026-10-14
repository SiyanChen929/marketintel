"""Independent adversarial audit; synthetic events, no production ledger artifacts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from market_intel.platform.shadow_ledger import ShadowAccountingError, ShadowLedger, inspect_shadow_artifact

SECURITY = "equity:US:AUDIT"


def at(day, hour=20, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def ev(when):
    # This is deliberately just a declared digest: not a verified source artifact.
    return {"source": "synthetic independent attack fixture", "content_sha256": "a" * 64,
            "observed_at": when.isoformat(), "available_at": when.isoformat(), "finality": "confirmed"}


def quote(when, price):
    return {"instrument_id": SECURITY, "currency": "USD", "price_basis": "unadjusted", "price": str(price), "evidence": ev(when)}


def new_book():
    return ShadowLedger(book_id="independent-audit", opened_at=at(21, 12), initial_cash="100", sample_type="synthetic_fixture")


def append(book, identity, kind, when, payload):
    return book.append(event_id=identity, kind=kind, effective_at=when, recorded_at=when, payload=payload)


def trade(book, identity, when, side, shares, price, fee="0"):
    return append(book, identity, "FILL", when, {
        "instrument_id": SECURITY, "currency": "USD", "asset_class": "equity", "sample_type": "synthetic_fixture",
        "run_id": "frozen-research", "order_id": "order-" + identity, "fill_id": "fill-" + identity,
        "price_basis": "unadjusted", "side": side, "quantity": str(shares), "price": str(price), "fee": fee,
        "signal_at": (when - timedelta(seconds=2)).isoformat(), "order_at": (when - timedelta(seconds=1)).isoformat(),
        "quote": quote(when, price), "evidence": ev(when), "settlement_at": at(when.day + 1, 12).isoformat()})


def settlement(book, identity, when, amount):
    return append(book, "settle-" + identity, "SETTLEMENT", when,
                  {"fill_event_id": identity, "currency": "USD", "amount": str(amount), "evidence": ev(when)})


def closing(book, day, price, *, action_ids=None, external=None):
    when = at(day)
    held = [k for k, r in book.positions.items() if r["quantity"]]
    return append(book, "close-" + str(day), "CLOSE", when, {
        "session": {"session_id": f"XNYS:2026-09-{day}", "opens_at": at(day, 13, 30).isoformat(), "closes_at": when.isoformat(),
                    "previous_session_id": book.closes[-1]["session_id"] if book.closes else None, "evidence": ev(at(21, 12))},
        "corporate_action_coverage": {"complete": True, "instrument_ids": sorted(set(held) | book._interval_instruments),
                                      "action_ids": action_ids or [], "unsupported_action_ids": [],
                                      "from_at": book.closes[-1]["asof"] if book.closes else at(21, 12).isoformat(),
                                      "through_at": when.isoformat(), "evidence": ev(when)},
        "quotes": {s: quote(when, price) for s in held}, "external_reconciliation": external})


def intermediate_book():
    b = new_book(); trade(b, "purchase", at(21, 14), "BUY", 2, 30, "1"); closing(b, 21, 30)
    append(b, "ex-date", "DIVIDEND_EX", at(22, 10), {
        "instrument_id": SECURITY, "currency": "USD", "action_id": "cash-distribution", "evidence": ev(at(22, 10)),
        "entitlement_rule": "pre_ex_position", "distribution_type": "normal_cash", "entitled_quantity": "2",
        "gross_per_share": "3", "withholding": "1", "record_at": at(22).isoformat(), "pay_at": at(23, 13).isoformat()})
    append(b, "split", "SPLIT", at(22, 11), {
        "instrument_id": SECURITY, "currency": "USD", "action_id": "split-2", "new_shares": "2", "old_shares": "1", "evidence": ev(at(22, 11))})
    settlement(b, "purchase", at(22, 12), -60)
    trade(b, "partial-sale", at(22, 14), "SELL", 1, 17, "2")
    return b


def test_independent_dividend_split_sale_reconstruction_and_no_receivable_buying_power():
    b = intermediate_book()
    # 100 - 60 - 1 - 2 = 37 settled; sale 17 pending; dividend 6-1=5 pending.
    assert b.settled_cash == b.available_cash == Decimal("37")
    assert b.economic_cash == Decimal("54") and b.dividend_receivable == Decimal("5")
    assert b.positions[SECURITY] == {"quantity": Decimal("3"), "cost": Decimal("45")}
    before = b.artifact()
    with pytest.raises(ShadowAccountingError, match="insufficient settled cash"):
        trade(b, "cash-reuse-attempt", at(22, 15), "BUY", 1, 40)
    assert b.artifact() == before
    closing(b, 22, 16, action_ids=["cash-distribution", "split-2"])
    # Equity = 54 + 5 + 3*16 = 107; gross realized2 + unrealized3 + dividend6 - tax1 - fees3 = 7.
    assert b.closes[-1]["equity"] == "107"
    assert b.closes[-1]["cumulative_pnl"] == "7"
    settlement(b, "partial-sale", at(23, 12), 17)
    append(b, "distribution-paid", "DIVIDEND_PAY", at(23, 13),
           {"action_id": "cash-distribution", "currency": "USD", "amount": "5", "evidence": ev(at(23, 13))})
    trade(b, "final-sale", at(23, 14), "SELL", 3, 16, "1")
    closing(b, 23, 16)
    assert b.closes[-1]["equity"] == "106" and b.closes[-1]["available_cash"] == "58"
    settlement(b, "final-sale", at(24, 12), 48); closing(b, 24, 16)
    assert b.closes[-1]["equity"] == b.closes[-1]["settled_cash"] == "106"


def test_declared_source_hashes_and_no_action_attestation_never_establish_source_verification(tmp_path):
    b = new_book(); trade(b, "purchase", at(21, 14), "BUY", 2, 30, "1"); closing(b, 21, 30)
    path = tmp_path / "declared.json"; b.write_exclusive(path)
    out = inspect_shadow_artifact(path, expected_head=b.head)
    assert out["status"] == "REPLAY_VERIFIED"
    assert out["source_verification"] == "DECLARED_PROVENANCE_REQUIRES_INDEPENDENT_ARTIFACT_VERIFICATION"
    assert out["validation_status"] == "NOT VALIDATED" and out["paper_overnight_enabled"] is False
    assert out["sample_type"] == "synthetic_fixture"


def test_self_consistent_tail_truncation_requires_independent_checkpoint(tmp_path):
    b = new_book(); trade(b, "purchase", at(21, 14), "BUY", 2, 30, "1"); closing(b, 21, 30)
    shorter = b.artifact()
    settlement(b, "purchase", at(22, 12), -60); closing(b, 22, 1)
    full_head = b.head
    # Attacker retained a perfectly valid earlier prefix, including its matching summary.
    path = tmp_path / "prefix.json"; path.write_text(json.dumps(shorter))
    unchecked = inspect_shadow_artifact(path)
    assert unchecked["status"] == "REPLAY_VERIFIED" and unchecked["independent_head_verified"] is False
    anchored = inspect_shadow_artifact(path, expected_head=full_head)
    assert anchored["status"] == "INVALID_ARTIFACT"


def test_backdated_after_close_event_is_atomic_even_if_recorded_later():
    b = new_book(); trade(b, "purchase", at(21, 14), "BUY", 2, 30, "1"); closing(b, 21, 30)
    before = b.artifact()
    with pytest.raises(ShadowAccountingError, match="sealed"):
        b.append(event_id="late-split", kind="SPLIT", effective_at=at(21, 19), recorded_at=at(22, 12), payload={
            "instrument_id": SECURITY, "currency": "USD", "action_id": "split", "new_shares": "10", "old_shares": "1", "evidence": ev(at(21, 19))})
    assert b.artifact() == before


def test_external_statement_declared_equity_mismatch_cannot_be_called_matched():
    b = new_book(); trade(b, "purchase", at(21, 14), "BUY", 2, 30, "1")
    statement = {"asof": at(21).isoformat(), "settled_cash": "99", "economic_cash": "39", "dividend_receivable": "0",
                 "quantities": {SECURITY: "2"}, "equity": "1", "evidence": ev(at(21))}
    with pytest.raises(ShadowAccountingError, match="equity|statement"):
        closing(b, 21, 30, external=statement)
    assert not b.closes
