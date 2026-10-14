"""Explicit, uncommitted retrospective revisions with two distinct clocks.

Economic facts can arrive late. Reconstruct them into a new version known only
at review_asof, preserve original receipts and every superseded record, and
never mutate or backdate a previously published book. This is not a historical
decision replay, a source authenticator, or an execution route.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Mapping

from .core import canonical_hash
from .shadow_ledger import SCHEMA as STRICT_SCHEMA, ShadowAccountingError, ShadowLedger, _evidence, _time
from .shadow_source_review import _keys, _scalar_fields, _time as _source_time, _METRICS

SCHEMA = "platform.shadow-retrospective-revision.v2"
BOOK_SCHEMA = "platform.shadow-reconstructed-book.v2"
_BODY = {"event_id", "kind", "effective_at", "recorded_at", "payload"}
_EVIDENCE = {"source", "content_sha256", "observed_at", "available_at", "finality"}


class ShadowRevisionError(ValueError):
    pass


class _ReconstructionLedger(ShadowLedger):
    """Internal reconstruction engine; never used by the strict v1 route."""

    def _apply(self, event):
        self._reconstruction_recorded_at = _time(event["recorded_at"], "recorded_at")
        self._reconstruction_kind = event["kind"]
        super()._apply(event)

    def _event_evidence(self, value, *, known_by):
        return _evidence(value, known_by=self._reconstruction_recorded_at)

    def _quote_evidence(self, value, *, known_by):
        boundary = self._reconstruction_recorded_at if self._reconstruction_kind == "CLOSE" else known_by
        evidence = _evidence(value, known_by=boundary)
        if _time(evidence["observed_at"], "quote observed_at") > known_by:
            raise ShadowAccountingError("valuation cannot use a quote observed after its economic boundary")
        return evidence

    def summary(self):
        return {**super().summary(), "schema_version": BOOK_SCHEMA,
                "knowledge_policy": "RETROSPECTIVE_RECONSTRUCTION_ONLY", "historical_decision_replay": False}

    def artifact(self):
        return {**super().artifact(), "schema_version": BOOK_SCHEMA}


def _checked_evidence(value, recorded):
    evidence = _keys(value, _EVIDENCE, "source evidence")
    for field in ("observed_at", "available_at"):
        _source_time(evidence[field], field)
    _evidence(evidence, known_by=recorded)


def _quote(value, recorded):
    q = _keys(value, {"instrument_id", "currency", "price_basis", "price", "evidence"}, "quote")
    _scalar_fields(q)
    _checked_evidence(q["evidence"], recorded)


def _validate_event(value, review_at):
    event = deepcopy(_keys(value, _BODY, "source event"))
    if not isinstance(event["event_id"], str) or not event["event_id"].strip():
        raise ShadowRevisionError("event_id must be explicit")
    at = _source_time(event["effective_at"], "effective_at")
    recorded = _source_time(event["recorded_at"], "source recorded_at")
    if not at <= recorded <= review_at:
        raise ShadowRevisionError("source recorded_at must follow economic time and precede review")
    expected = {
        "FILL": {"instrument_id", "run_id", "order_id", "fill_id", "currency", "asset_class", "sample_type",
                 "price_basis", "side", "signal_at", "order_at", "quote", "quantity", "price", "fee", "settlement_at", "evidence"},
        "SETTLEMENT": {"fill_event_id", "amount", "currency", "evidence"},
        "DIVIDEND_EX": {"action_id", "instrument_id", "currency", "entitlement_rule", "distribution_type",
                        "record_at", "pay_at", "entitled_quantity", "gross_per_share", "withholding", "evidence"},
        "DIVIDEND_PAY": {"action_id", "amount", "currency", "evidence"},
        "SPLIT": {"action_id", "instrument_id", "currency", "new_shares", "old_shares", "evidence"},
        "CLOSE": {"session", "corporate_action_coverage", "quotes", "external_reconciliation"},
    }
    if event["kind"] not in expected:
        raise ShadowRevisionError("unknown lifecycle event")
    p = _keys(event["payload"], expected[event["kind"]], "event payload")
    _scalar_fields(p)
    if event["kind"] != "CLOSE":
        _checked_evidence(p["evidence"], recorded)
        if event["kind"] == "FILL":
            _quote(p["quote"], recorded)
    else:
        session = _keys(p["session"], {"session_id", "previous_session_id", "opens_at", "closes_at", "evidence"},
                        "session", {"exchange", "timezone"})
        _scalar_fields(session)
        _checked_evidence(session["evidence"], recorded)
        coverage = _keys(p["corporate_action_coverage"], {"from_at", "through_at", "instrument_ids", "complete",
                                                        "action_ids", "unsupported_action_ids", "evidence"}, "coverage")
        _scalar_fields(coverage)
        _checked_evidence(coverage["evidence"], recorded)
        if not isinstance(p["quotes"], Mapping):
            raise ShadowRevisionError("quotes must be a security mapping")
        for quote in p["quotes"].values():
            _quote(quote, recorded)
        if p["external_reconciliation"] is not None:
            statement = _keys(p["external_reconciliation"], {"asof", "settled_cash", "economic_cash", "dividend_receivable", "quantities", "evidence"},
                              "statement", _METRICS | {"currency"})
            _scalar_fields(statement)
            _checked_evidence(statement["evidence"], recorded)
    return event


def _replay_book(artifact):
    if artifact.get("schema_version") == STRICT_SCHEMA:
        return ShadowLedger.replay(artifact)
    if artifact.get("schema_version") != BOOK_SCHEMA:
        raise ShadowRevisionError("unknown parent book schema")
    internal = deepcopy(artifact)
    internal["schema_version"] = STRICT_SCHEMA
    return _ReconstructionLedger.replay(internal)


def _known_cash(config, opening_evidence, events, current):
    """Conservative ex-post funding check; never use an unknown cash credit.

    Observed buy debits reserve cash from their declared order time. That is a
    conservative deduction, not a claim that the later fill price was predicted.
    Only cash credits both effective and actually recorded by the order boundary
    can finance it. Even a late-reported debit remains reserved.
    """
    order = _time(current["payload"]["order_at"], "order_at")
    if (_time(config["opened_at"], "opened_at") > order
            or _time(opening_evidence["available_at"], "opening known_at") > order):
        raise ShadowRevisionError("opening capital was not known by the order boundary")
    earlier = [e for e in events if e["event_id"] != current["event_id"] and e["kind"] == "FILL"
               and _time(e["payload"]["order_at"], "order_at") <= order]
    known_settlements = {e["payload"]["fill_event_id"]: e for e in events if e["kind"] == "SETTLEMENT"
                         and _time(e["effective_at"], "settlement effective_at") <= order
                         and _time(e["recorded_at"], "settlement recorded_at") <= order}
    cash = Decimal(config["initial_cash"])
    for event in known_settlements.values():
        cash += Decimal(event["payload"]["amount"])
    for event in events:
        if (event["kind"] == "DIVIDEND_PAY" and _time(event["effective_at"], "pay effective_at") <= order
                and _time(event["recorded_at"], "pay recorded_at") <= order):
            cash += Decimal(event["payload"]["amount"])
    for event in earlier:
        p = event["payload"]
        cash -= Decimal(p["fee"])
        if p["side"] == "BUY" and event["event_id"] not in known_settlements:
            cash -= Decimal(p["quantity"]) * Decimal(p["price"])
    p = current["payload"]
    required = Decimal(p["fee"]) + (Decimal(p["quantity"]) * Decimal(p["price"]) if p["side"] == "BUY" else Decimal(0))
    if cash < required:
        raise ShadowRevisionError("order relies on cash that was unsettled or unknown at its order boundary")
    return {"event_id": current["event_id"], "order_at": order.isoformat(), "known_available_cash": str(cash),
            "observed_required_cash": str(required), "status": "CONSERVATIVE_EX_POST_FUNDING_CHECK"}


def build_shadow_revision(*, parent_artifact: Mapping, expected_parent_head: str, expected_parent_artifact_hash: str, revision_id: str,
                          reason: str, review_asof: Any, source_events: list[dict], opening_evidence: Mapping,
                          replacements: Mapping | None = None) -> dict:
    """Build an independent historical revision without publishing or changing parent.

    ``source_events`` is the full active journal in economic order. Its original
    recorded/available clocks remain untouched. Replacement IDs explicitly name
    which parent fact is superseded; every old fact remains in source_event_history.
    The returned candidate book records its reconstruction at review_asof.
    Source authenticity is not established here; no provider reader is invoked.
    """
    if not isinstance(parent_artifact, Mapping) or not isinstance(source_events, list):
        raise ShadowRevisionError("parent artifact and complete source event list are required")
    parent = deepcopy(dict(parent_artifact))
    reviewed = _source_time(review_asof, "review_asof")
    if any(not isinstance(v, str) or not v.strip() for v in (revision_id, reason, expected_parent_head, expected_parent_artifact_hash)):
        raise ShadowRevisionError("revision ID, reason and caller-pinned parent head/artifact hash are required")
    if canonical_hash(parent) != expected_parent_artifact_hash:
        raise ShadowRevisionError("parent artifact does not match its caller-pinned content hash")
    if parent.get("schema_version") == SCHEMA:
        if parent.get("revision_hash") != canonical_hash({k: v for k, v in parent.items() if k != "revision_hash"}):
            raise ShadowRevisionError("parent revision integrity mismatch")
        if revision_id == parent["revision_id"] or reviewed < _time(parent["review_asof"], "parent review_asof"):
            raise ShadowRevisionError("revision must have a new identity and cannot move knowledge time backwards")
        book = _replay_book(parent["reconstructed_book"])
        parent_time = _source_time(parent["review_asof"], "parent review_asof")
        previous = [_validate_event(e, parent_time) for e in parent["source_events"]]
        history = [_validate_event(e, parent_time) for e in parent["source_event_history"]]
        parent_history = {e["event_id"]: e for e in history}
        if len(parent_history) != len(history) or any(parent_history.get(e["event_id"]) != e for e in previous):
            raise ShadowRevisionError("parent source history does not bind every active event")
        _checked_evidence(parent["opening_evidence"], parent_time)
        expected_book = _ReconstructionLedger(**{k: v for k, v in book.config.items() if k != "currency"})
        for event in previous:
            expected_book.append(**{**deepcopy(event), "recorded_at": parent_time.isoformat()})
        if (expected_book.artifact() != parent["reconstructed_book"] or expected_book.head != parent["candidate_head_hash"]
                or parent.get("oos_eligible") is not False or parent.get("historical_decision_replay") is not False
                or parent.get("status") != "UNCOMMITTED_RETROSPECTIVE_REVISION"):
            raise ShadowRevisionError("parent reconstruction does not match its source facts or retrospective scope")
        expected_funding = [_known_cash(book.config, parent["opening_evidence"], previous, e) for e in previous if e["kind"] == "FILL"]
        if expected_funding != parent["known_cash_checks"]:
            raise ShadowRevisionError("parent known-cash checks do not reconstruct")
        if opening_evidence != parent["opening_evidence"]:
            raise ShadowRevisionError("opening evidence cannot change silently across revisions")
    else:
        book = _replay_book(parent)
        previous = [{key: deepcopy(row[key]) for key in _BODY} for row in book.events]
        history = deepcopy(previous)
    if book.head != expected_parent_head:
        raise ShadowRevisionError("parent does not match the caller-pinned head")
    if book.events and reviewed < _time(book.events[-1]["recorded_at"], "last parent record"):
        raise ShadowRevisionError("review precedes the parent publication boundary")
    opening = deepcopy(_keys(opening_evidence, _EVIDENCE, "opening evidence"))
    _checked_evidence(opening, reviewed)
    if _time(opening["observed_at"], "opening observed_at") != _time(book.config["opened_at"], "opened_at"):
        raise ShadowRevisionError("opening evidence must identify the original opening balance time")
    events = [_validate_event(row, reviewed) for row in source_events]
    by_id = {row["event_id"]: row for row in events}
    if len(by_id) != len(events):
        raise ShadowRevisionError("duplicate active source event ID")
    parent_by_id = {row["event_id"]: row for row in previous}
    history_by_id = {row["event_id"]: row for row in history}
    replace = dict(replacements or {})
    new_replacement_ids = set()
    for old_id, declaration in replace.items():
        declaration = _keys(declaration, {"new_event_id", "reason"}, "replacement declaration")
        new_id = declaration["new_event_id"]
        if (old_id not in parent_by_id or old_id in by_id or new_id not in by_id or new_id in history_by_id
                or new_id in new_replacement_ids or not isinstance(declaration["reason"], str) or not declaration["reason"].strip()
                or parent_by_id[old_id]["kind"] != by_id[new_id]["kind"]):
            raise ShadowRevisionError("replacement must explicitly supersede one existing event with a new ID of the same kind")
        new_replacement_ids.add(new_id)
    for event_id, old in parent_by_id.items():
        if event_id not in replace and by_id.get(event_id) != old:
            raise ShadowRevisionError("previous fact was removed or edited without explicit supersession")
    for row in events:
        old = history_by_id.get(row["event_id"])
        if old is not None and row != old:
            raise ShadowRevisionError("source event identity conflicts with a retained historical record")
        if old is None:
            history.append(deepcopy(row))
    config = {key: value for key, value in book.config.items() if key != "currency"}
    rebuilt = _ReconstructionLedger(**config)
    for row in events:
        # The new journal is recorded now. The original source receipt remains
        # untouched in source_events/history and is validated independently.
        rebuilt.append(**{**deepcopy(row), "recorded_at": reviewed.isoformat()})
    funding = [_known_cash(book.config, opening, events, row) for row in events if row["kind"] == "FILL"]
    result = {"schema_version": SCHEMA, "revision_id": revision_id, "reason": reason,
              "parent_artifact_hash": canonical_hash(parent), "parent_head_hash": book.head,
              "parent_revision_id": parent.get("revision_id"), "review_asof": reviewed.isoformat(),
              "economic_through": events[-1]["effective_at"] if events else book.config["opened_at"],
              "opening_evidence": opening, "source_events": events, "source_event_history": history,
              "replacements": deepcopy(replace), "known_cash_checks": funding,
              "late_event_ids": [e["event_id"] for e in events if _time(e["recorded_at"], "recorded_at") > _time(e["effective_at"], "effective_at")],
              "reconstructed_book": rebuilt.artifact(), "candidate_head_hash": rebuilt.head,
              "knowledge_at": reviewed.isoformat(), "status": "UNCOMMITTED_RETROSPECTIVE_REVISION",
              "sample_type": "RETROSPECTIVE_ACCOUNTING_REVISION", "validation_status": "NOT VALIDATED",
              "execution_status": "SHADOW_ACCOUNTING_ONLY", "actionability": "NOT ACTIONABLE",
              "historical_decision_replay": False, "oos_eligible": False,
              "paper_overnight_enabled": False, "live_trading_supported": False,
              "source_authenticity": "UNVERIFIED_DECLARED_PROVENANCE", "parent_checkpoint": "CALLER_PINNED_NOT_INDEPENDENTLY_AUTHENTICATED",
              "limitations": ["Sources and their asserted receipt clocks need independent provider verification.",
                              "Late facts revise historical accounting only in this new version; no earlier decision can use them.",
                              "Cash checks conservatively reserve observed debits and do not simulate executable orders.",
                              "No storage, live routing, source normalization or actual account publication is performed."]}
    result["revision_hash"] = canonical_hash(result)
    return result
