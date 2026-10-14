"""Synthetic retrospective accounting; no broker, source fetch or ledger write."""
from copy import deepcopy
from datetime import timedelta

import pytest

from market_intel.platform.shadow_ledger import ShadowLedger, ShadowAccountingError
from market_intel.platform.shadow_revision import build_shadow_revision, ShadowRevisionError
from market_intel.platform.core import canonical_hash
from test_platform_shadow_ledger import ledger, full_fixture, evidence, ts, fill, settle


def bodies(book):
    return [{key: deepcopy(row[key]) for key in ("event_id", "kind", "effective_at", "recorded_at", "payload")} for row in book.events]


def revision(parent, events, *, review=None, **kw):
    head = parent.get("candidate_head_hash", parent.get("head_hash"))
    return build_shadow_revision(parent_artifact=parent, expected_parent_head=head, expected_parent_artifact_hash=canonical_hash(parent),
        revision_id="revision-fixture", reason="Synthetic late facts for accounting review",
        review_asof=review or ts(18, 21), source_events=events, opening_evidence=evidence(ts(14, 13)), **kw)


def delayed_fixture():
    source = bodies(full_fixture())
    for row in source:
        at = ts(14, 14) if row["event_id"] == "buy" else None
        if at:
            row["payload"]["evidence"]["available_at"] = (at + timedelta(seconds=1)).isoformat()
            row["recorded_at"] = (at + timedelta(seconds=2)).isoformat()
        if row["kind"] == "CLOSE":
            from datetime import datetime
            at = datetime.fromisoformat(row["effective_at"])
            row["payload"]["corporate_action_coverage"]["evidence"]["available_at"] = (at + timedelta(seconds=1)).isoformat()
            for quote in row["payload"]["quotes"].values():
                quote["evidence"]["available_at"] = (at + timedelta(seconds=1)).isoformat()
            row["recorded_at"] = (at + timedelta(seconds=2)).isoformat()
    return source


def test_late_fill_and_close_evidence_reconstruct_without_backdating_sources_or_parent():
    parent = ledger().artifact()
    source = delayed_fixture()
    before_parent, before_source = deepcopy(parent), deepcopy(source)
    result = revision(parent, source)
    assert result["reconstructed_book"]["summary"]["latest_close"]["equity"] == "1005.0"
    assert result["reconstructed_book"]["summary"]["latest_close"]["settled_cash"] == "1005.0"
    assert result["source_events"] == before_source
    assert parent == before_parent and source == before_source
    assert result["source_events"][0]["effective_at"] == ts(14, 14).isoformat()
    assert result["source_events"][0]["payload"]["evidence"]["available_at"] == ts(14, 14, second=1).isoformat()
    assert result["reconstructed_book"]["events"][0]["recorded_at"] == ts(18, 21).isoformat()
    assert result["knowledge_at"] == ts(18, 21).isoformat()
    assert result["historical_decision_replay"] is False and result["oos_eligible"] is False
    assert result["paper_overnight_enabled"] is False
    assert result["source_authenticity"] == "UNVERIFIED_DECLARED_PROVENANCE"
    with pytest.raises(ShadowAccountingError, match="unavailable"):
        ledger().append(**source[0])


def test_delayed_statement_requires_explicit_new_close_version_and_preserves_old_close():
    first = revision(ledger().artifact(), delayed_fixture())
    old = deepcopy(first)
    rows = deepcopy(first["source_events"])
    last = rows[-1]
    old_id = last["event_id"]
    last["event_id"] = "corrected-close-17"
    last["recorded_at"] = ts(19, 20).isoformat()
    last["payload"]["external_reconciliation"] = {
        "asof": ts(17).isoformat(), "currency": "USD", "settled_cash": "1005", "economic_cash": "1005",
        "dividend_receivable": "0", "quantities": {}, "equity": "1005",
        "evidence": evidence(ts(17), available=ts(19, 19))}
    result = build_shadow_revision(parent_artifact=first, expected_parent_head=first["candidate_head_hash"], expected_parent_artifact_hash=canonical_hash(first),
        revision_id="revision-fixture-2", reason="A late statement became available", review_asof=ts(19, 21),
        source_events=rows, opening_evidence=first["opening_evidence"],
        replacements={old_id: {"new_event_id": last["event_id"], "reason": "Attach actual late statement"}})
    assert first == old
    assert result["parent_head_hash"] == first["candidate_head_hash"]
    assert result["reconstructed_book"]["summary"]["latest_close"]["external_reconciliation_status"] == "MATCHED_DECLARED_STATEMENT"
    assert {e["event_id"] for e in result["source_event_history"]} >= {old_id, last["event_id"]}
    assert next(e for e in result["source_event_history"] if e["event_id"] == old_id) == first["source_events"][-1]
    assert result["source_events"][-1]["payload"]["external_reconciliation"]["evidence"]["available_at"] == ts(19, 19).isoformat()


@pytest.mark.parametrize("attack", ["edit", "drop", "head", "future_record", "future_available", "unknown_fields", "duplicate"])
def test_revision_rejects_silent_changes_unknown_schema_and_broken_clocks(attack):
    book = full_fixture()
    parent, source = book.artifact(), bodies(book)
    extra = {}
    if attack == "edit":
        source[0]["payload"]["fee"] = "5"
    elif attack == "drop":
        source.pop()
    elif attack == "head":
        parent["head_hash"] = "0" * 64
    elif attack == "future_record":
        source[0]["recorded_at"] = ts(20).isoformat()
    elif attack == "future_available":
        source[0]["payload"]["evidence"]["available_at"] = ts(20).isoformat()
    elif attack == "unknown_fields":
        source[0]["payload"]["assume_borrow"] = True
    elif attack == "duplicate":
        source.append(deepcopy(source[0]))
    with pytest.raises(ValueError):
        revision(parent, source, **extra)


def test_delayed_fill_does_not_make_a_future_execution_quote_available_in_the_past():
    source = delayed_fixture()
    source[0]["payload"]["quote"]["evidence"]["available_at"] = ts(14, 14, second=1).isoformat()
    with pytest.raises(ShadowAccountingError, match="unavailable"):
        revision(ledger().artifact(), source)


def test_close_revision_cannot_use_quote_observed_after_its_valuation_time():
    source = delayed_fixture()
    quote = next(iter(source[1]["payload"]["quotes"].values()))
    quote["evidence"]["observed_at"] = ts(14, 20, second=1).isoformat()
    with pytest.raises(ShadowAccountingError, match="observed after"):
        revision(ledger().artifact(), source)


def test_late_sale_settlement_cannot_fund_a_purchase_before_its_receipt():
    book = ledger(cash="100")
    fill(book, quantity="1", price="99", fee="0")
    settle(book, "buy", 15, "-99")
    fill(book, "sell", day=15, hour=14, side="SELL", quantity="1", price="99", fee="0")
    settle(book, "sell", 16, "99")
    fill(book, "second-buy", day=16, hour=15, quantity="1", price="99", fee="0")
    source = bodies(book)
    settlement = next(e for e in source if e["event_id"] == "settle-sell")
    settlement["payload"]["evidence"]["available_at"] = ts(16, 16).isoformat()
    settlement["recorded_at"] = ts(16, 16).isoformat()
    parent = ledger(cash="100").artifact()
    with pytest.raises(ShadowRevisionError, match="unknown at its order"):
        revision(parent, source)
    # The same actual settlement can finance an order only after it became known.
    second = source[-1]
    for field in ("effective_at", "recorded_at"):
        second[field] = ts(16, 17).isoformat()
    p = second["payload"]
    p["signal_at"], p["order_at"] = ts(16, 16, 59, 58).isoformat(), ts(16, 16, 59, 59).isoformat()
    p["evidence"] = evidence(ts(16, 17))
    p["quote"]["evidence"] = evidence(ts(16, 17))
    result = revision(parent, source)
    assert result["known_cash_checks"][-1]["known_available_cash"] == "100"


def test_review_has_no_write_path_and_cannot_enable_paper(monkeypatch):
    source = delayed_fixture()
    parent = ledger().artifact()
    def forbidden(*args, **kwargs):
        pytest.fail("revision must not perform filesystem or network operations")
    import socket
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(ShadowLedger, "write_exclusive", forbidden)
    result = revision(parent, source)
    assert result["status"] == "UNCOMMITTED_RETROSPECTIVE_REVISION"
    assert result["validation_status"] == "NOT VALIDATED"


def test_parent_original_receipt_clock_is_protected_by_whole_artifact_pin():
    first = revision(ledger().artifact(), delayed_fixture())
    pinned = canonical_hash(first)
    first["source_events"][0]["recorded_at"] = ts(14, 14, second=1).isoformat()
    first["revision_hash"] = canonical_hash({k: v for k, v in first.items() if k != "revision_hash"})
    with pytest.raises(ShadowRevisionError, match="caller-pinned content hash"):
        build_shadow_revision(parent_artifact=first, expected_parent_head=first["candidate_head_hash"],
            expected_parent_artifact_hash=pinned, revision_id="new-revision", reason="test", review_asof=ts(19),
            source_events=first["source_events"], opening_evidence=first["opening_evidence"])


def test_self_resigned_parent_source_book_disagreement_is_rejected():
    first = revision(ledger().artifact(), delayed_fixture())
    first["source_events"][0]["payload"]["fee"] = "2"
    first["source_event_history"][0]["payload"]["fee"] = "2"
    first["revision_hash"] = canonical_hash({k: v for k, v in first.items() if k != "revision_hash"})
    with pytest.raises(ShadowRevisionError, match="reconstruction"):
        build_shadow_revision(parent_artifact=first, expected_parent_head=first["candidate_head_hash"],
            expected_parent_artifact_hash=canonical_hash(first), revision_id="new-revision", reason="test", review_asof=ts(19),
            source_events=first["source_events"], opening_evidence=first["opening_evidence"])
