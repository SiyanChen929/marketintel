"""Synthetic in-memory source fixtures, never production fills or vendor data."""
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json

import pytest

from market_intel.platform.core import DatasetRequest, canonical_hash
from market_intel.platform.data import DatasetVersion
from market_intel.platform.shadow_source_review import (
    BUNDLE_SCHEMA, SOURCE_SCHEMA, UNITS, ShadowSourceReviewError, review_shadow_sources,
)

XYZ = "equity:US:XYZ"
OPEN = "2026-09-14T13:00:00+00:00"
FILL = "2026-09-14T14:00:00+00:00"
CLOSE = "2026-09-14T20:00:00+00:00"
NEXT = "2026-09-15T20:00:00+00:00"


class Sources:
    def __init__(self):
        self.records, self.refs, self.calls = {}, {}, []

    def add(self, role, data, effective_at, *, observed_at=None, acquired_at=None, instruments=()):
        observed_at, acquired_at = observed_at or effective_at, acquired_at or observed_at or effective_at
        doc = {"schema_version": SOURCE_SCHEMA, "role": role, "book_id": "synthetic-source-fixture", "currency": "USD",
               "sample_type": "synthetic_fixture", "instrument_ids": list(instruments),
               "timestamp_precision": "second", "units": deepcopy(UNITS), "data": deepcopy(data)}
        scope = instruments or ["shadow-book:" + sha256(doc["book_id"].encode()).hexdigest()]
        request = DatasetRequest(provider="synthetic_fixture", entitlement="test_only", dataset="shadow_source." + role.lower(),
                   instrument_ids=tuple(scope), fields=("record",), frequency="event", adjustment="unadjusted",
                   start=date(2026, 9, 14), end=date(2026, 9, 30), as_of=datetime.fromisoformat(acquired_at),
                   schema_version=SOURCE_SCHEMA).canonical_dict()
        return self.pack(doc, request, effective_at, observed_at, acquired_at)

    def pack(self, doc, request, effective_at, observed_at, acquired_at):
        raw = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
        digest, request_hash = sha256(raw).hexdigest(), canonical_hash(request)
        version_id = canonical_hash({"request_key": request_hash, "content_sha256": digest, "effective_at": effective_at,
                                     "observed_at": observed_at, "finality": "confirmed"})
        version = DatasetVersion(version_id, request_hash, digest, len(raw), datetime.fromisoformat(effective_at),
                                 datetime.fromisoformat(observed_at), datetime.fromisoformat(acquired_at), "confirmed", SOURCE_SCHEMA, "unused")
        self.records[version_id] = (version, deepcopy(request), raw)
        self.refs[version_id] = {"version_id": version_id, "content_sha256": digest, "request_key": request_hash,
                                "role": doc["role"], "provider": request["provider"], "instrument_ids": doc["instrument_ids"],
                                "acquired_at": acquired_at}
        return version_id

    def change(self, bundle, identity, transform):
        """Change a document and honestly repin bytes; use a leaf or opening source."""
        version, request, raw = self.records[identity]
        doc = json.loads(raw)
        transform(doc)
        replacement = self.pack(doc, request, version.effective_at.isoformat(), version.observed_at.isoformat(), version.acquired_at.isoformat())
        bundle["sources"] = [deepcopy(self.refs[replacement]) if row["version_id"] == identity else row for row in bundle["sources"]]
        if bundle["opening_version_id"] == identity:
            bundle["opening_version_id"] = replacement
        bundle["event_version_ids"] = [replacement if key == identity else key for key in bundle["event_version_ids"]]
        return replacement

    def get_version(self, identity):
        self.calls.append(identity)
        return deepcopy(self.records[identity])

    def bundle(self, opening_id, events, through=CLOSE):
        return {"schema_version": BUNDLE_SCHEMA, "book_id": "synthetic-source-fixture", "currency": "USD",
                "sample_type": "synthetic_fixture", "interval": {"from_at": OPEN, "through_at": through},
                "opening_version_id": opening_id, "event_version_ids": events, "sources": deepcopy(list(self.refs.values())),
                "limits": {"max_quote_age_seconds": 300, "max_acquisition_lag_seconds": 300}}


def fixture(*, statement=True):
    s = Sources()
    opening = s.add("OPENING", {"opened_at": OPEN, "initial_cash": "100", "capital_basis": "cash_only_no_positions_or_receivables"}, OPEN)
    q = s.add("QUOTE", {"instrument_id": XYZ, "currency": "USD", "price_basis": "unadjusted", "price": "30",
                        "mark_convention": "provider_explicit_mark"}, FILL, instruments=[XYZ])
    fill = s.add("FILL", {"event_id": "buy", "effective_at": FILL, "recorded_at": FILL, "payload": {
        "instrument_id": XYZ, "run_id": "synthetic-run", "order_id": "order-buy", "fill_id": "fill-buy", "currency": "USD",
        "asset_class": "equity", "sample_type": "synthetic_fixture", "price_basis": "unadjusted", "side": "BUY",
        "signal_at": "2026-09-14T13:59:58+00:00", "order_at": "2026-09-14T13:59:59+00:00", "quote_version_id": q,
        "quantity": "2", "price": "30", "fee": "1", "settlement_at": "2026-09-15T13:30:00+00:00"}}, FILL, instruments=[XYZ])
    cq = s.add("QUOTE", {"instrument_id": XYZ, "currency": "USD", "price_basis": "unadjusted", "price": "31",
                         "mark_convention": "provider_explicit_mark"}, CLOSE, instruments=[XYZ])
    calendar = s.add("CALENDAR", {"session_id": "XNYS:2026-09-14", "previous_session_id": None,
                                 "opens_at": "2026-09-14T13:30:00+00:00", "closes_at": CLOSE,
                                 "exchange": "XNYS", "timezone": "America/New_York"}, CLOSE, observed_at=OPEN)
    coverage = s.add("ACTION_COVERAGE", {"from_at": OPEN, "through_at": CLOSE, "instrument_ids": [XYZ],
        "complete": True, "action_ids": [], "unsupported_action_ids": [],
        "coverage_convention": "provider_complete_for_instruments_and_interval"}, CLOSE, instruments=[XYZ])
    statement_id = s.add("STATEMENT", {"asof": CLOSE, "currency": "USD", "settled_cash": "99", "economic_cash": "39",
        "dividend_receivable": "0", "quantities": {XYZ: "2"}, "equity": "101", "market_value": "62"}, CLOSE, instruments=[XYZ]) if statement else None
    close = s.add("CLOSE", {"event_id": "close-14", "effective_at": CLOSE, "recorded_at": CLOSE,
                           "payload": {"session_version_id": calendar, "coverage_version_id": coverage,
                                       "quote_version_ids": [cq], "statement_version_id": statement_id}}, CLOSE, instruments=[XYZ])
    return s, s.bundle(opening, [fill, close])


def test_manual_cost_bearing_buy_close_returns_only_a_reviewable_plan():
    s, b = fixture()
    result = review_shadow_sources(b, s, NEXT)
    # Independent arithmetic: cash100 - fee1 - principal60 =39; shares2*31=62; equity101.
    close = result["accounting_summary"]["latest_close"]
    assert close["economic_cash"] == "39"
    assert close["market_value"] == "62"
    assert close["equity"] == "101"
    assert close["cumulative_pnl"] == "1"
    assert result["status"] == "REVIEWABLE_EVENT_PLAN"
    assert result["validation_status"] == "NOT VALIDATED"
    assert result["paper_overnight_enabled"] is False
    assert result["live_trading_supported"] is False
    assert result["evidence_states"]["source_authenticity"] == "UNVERIFIED_REQUIRES_TRUSTED_PROVIDER"
    assert result["evidence_states"]["coverage"] == "DECLARED_UNVERIFIED"
    assert result["evidence_states"]["external_statement_independence"] == "UNVERIFIED"
    assert result["evidence_states"]["external_reconciliation"] == "MATCHED_DECLARED_STATEMENT"
    assert any(x["json_pointer"] == "/data/payload/fee" for x in result["field_bindings"])
    assert set(s.calls) == set(s.records) and len(s.calls) == len(s.records)


def test_source_order_does_not_change_bundle_or_plan_but_event_order_matters():
    s, b = fixture()
    first = review_shadow_sources(b, s, NEXT)
    b["sources"].reverse()
    second = review_shadow_sources(b, s, NEXT)
    assert first == second
    b["event_version_ids"].reverse()
    with pytest.raises(ShadowSourceReviewError):
        review_shadow_sources(b, s, NEXT)


def test_reader_input_store_and_bundle_never_mutated_and_no_ledger_is_published(monkeypatch):
    s, b = fixture()
    original_b, original_store = deepcopy(b), deepcopy(s.records)
    def forbidden(*args, **kwargs):
        raise AssertionError("review must not instantiate catalog, publish files or access network")
    from market_intel.platform.data import DatasetCatalog
    from market_intel.platform.shadow_ledger import ShadowLedger
    import socket
    monkeypatch.setattr(DatasetCatalog, "__init__", forbidden)
    monkeypatch.setattr(ShadowLedger, "write_exclusive", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = review_shadow_sources(b, s, NEXT)
    assert b == original_b and s.records == original_store
    result["event_plan"][0]["payload"]["fee"] = "999"
    assert b == original_b and s.records == original_store


@pytest.mark.parametrize("change", [
    lambda d: d.update(extra="hidden economic field"),
    lambda d: d["data"].update(extra="ignored opening field"),
    lambda d: d.update(currency="EUR"),
    lambda d: d.update(sample_type="observed_paper"),
    lambda d: d["units"].update(price="USD/100 shares"),
    lambda d: d.update(timestamp_precision="day"),
    lambda d: d["data"].update(opened_at="2026-09-14"),
    lambda d: d["data"].update(initial_cash=100.0),
    lambda d: d["data"].update(initial_cash="NaN"),
    lambda d: d["data"].update(capital_basis="net_liquidation_value_including_positions"),
])
def test_unknown_fields_units_clocks_or_capital_basis_reject_even_with_matching_hashes(change):
    s, b = fixture()
    s.change(b, b["opening_version_id"], change)
    with pytest.raises(ShadowSourceReviewError):
        review_shadow_sources(b, s, NEXT)


@pytest.mark.parametrize("change", [
    lambda p: p.pop("fee"),
    lambda p: p.pop("settlement_at"),
    lambda p: p.update(fee=None),
    lambda p: p.update(quantity="-2"),
    lambda p: p.update(side="SELL"),
    lambda p: p.update(instrument_id="equity:US:OTHER"),
    lambda p: p.update(currency="EUR"),
    lambda p: p.update(fee_basis="per-share"),
    lambda p: p.update(price_basis="split_adjusted"),
    lambda p: p.update(signal_at=FILL),
])
def test_fill_fields_cannot_be_filled_by_defaults_or_reinterpreted(change):
    s, b = fixture()
    s.change(b, b["event_version_ids"][0], lambda doc: change(doc["data"]["payload"]))
    with pytest.raises(ShadowSourceReviewError):
        review_shadow_sources(b, s, NEXT)


def test_changed_original_bytes_reject_before_any_event_plan():
    s, b = fixture()
    identity = b["opening_version_id"]
    v, r, raw = s.records[identity]
    s.records[identity] = v, r, raw.replace(b'"100"', b'"900"')
    with pytest.raises(ShadowSourceReviewError) as exc:
        review_shadow_sources(b, s, NEXT)
    assert exc.value.code == "INTEGRITY_FAILURE"


def test_metadata_identity_or_acquisition_time_cannot_be_forged():
    s, b = fixture()
    identity = b["opening_version_id"]
    v, r, raw = s.records[identity]
    fields = v.to_dict()
    fields["effective_at"] = "2026-09-13T13:00:00+00:00"
    s.records[identity] = fields, r, raw
    with pytest.raises(ShadowSourceReviewError, match="identity"):
        review_shadow_sources(b, s, NEXT)
    fields = v.to_dict()
    fields["acquired_at"] = "2026-09-14T13:00:01+00:00"
    s.records[identity] = fields, r, raw
    with pytest.raises(ShadowSourceReviewError, match="acquisition time"):
        review_shadow_sources(b, s, NEXT)


def test_late_quote_acquisition_cannot_be_backdated_to_create_historical_fill():
    s, b = fixture()
    identity = next(k for k, row in s.refs.items() if row["role"] == "QUOTE")
    v, r, raw = s.records[identity]
    fields = v.to_dict()
    fields["acquired_at"] = "2026-09-14T14:00:01+00:00"
    s.records[identity] = fields, r, raw
    next(row for row in b["sources"] if row["version_id"] == identity)["acquired_at"] = fields["acquired_at"]
    with pytest.raises(ShadowSourceReviewError) as exc:
        review_shadow_sources(b, s, NEXT)
    assert exc.value.code == "UNAVAILABLE_AT_BOUNDARY"


def test_unrelated_right_hash_source_cannot_satisfy_quote_reference():
    s, b = fixture()
    opening = b["opening_version_id"]
    s.change(b, b["event_version_ids"][0], lambda d: d["data"]["payload"].update(quote_version_id=opening))
    with pytest.raises(ShadowSourceReviewError, match="wrong role"):
        review_shadow_sources(b, s, NEXT)


def test_missing_statement_stays_unknown_and_no_invented_reconciliation():
    s, b = fixture(statement=False)
    report = review_shadow_sources(b, s, NEXT)
    assert report["evidence_states"]["external_reconciliation"] == "UNKNOWN_MISSING_STATEMENT"


def test_duplicate_or_unreferenced_sources_fail_and_cannot_be_silently_dropped():
    s, b = fixture()
    b["sources"].append(deepcopy(b["sources"][0]))
    with pytest.raises(ShadowSourceReviewError, match="listed twice"):
        review_shadow_sources(b, s, NEXT)
    b["sources"].pop()
    extra = s.add("QUOTE", {"instrument_id": XYZ, "currency": "USD", "price_basis": "unadjusted", "price": "99",
                           "mark_convention": "provider_explicit_mark"}, CLOSE, instruments=[XYZ])
    b["sources"].append(s.refs[extra])
    with pytest.raises(ShadowSourceReviewError, match="unreferenced"):
        review_shadow_sources(b, s, NEXT)


def test_missing_pinned_source_and_duplicate_event_id_reject():
    s, b = fixture()
    b["sources"] = [row for row in b["sources"] if row["role"] != "QUOTE"]
    with pytest.raises(ShadowSourceReviewError, match="not pinned"):
        review_shadow_sources(b, s, NEXT)
    s, b = fixture()
    s.change(b, b["event_version_ids"][-1], lambda d: d["data"].update(event_id="buy"))
    with pytest.raises(ShadowSourceReviewError, match="duplicate source event ID"):
        review_shadow_sources(b, s, NEXT)


def test_unavailable_boundary_missing_events_and_unsupported_prior_book_reject():
    s, b = fixture()
    with pytest.raises(ShadowSourceReviewError):
        review_shadow_sources(b, s, OPEN)
    b["interval"]["through_at"] = NEXT
    with pytest.raises(ShadowSourceReviewError, match="reach"):
        review_shadow_sources(b, s, NEXT)
    b["prior_book"] = {"head": "self-signed"}
    with pytest.raises(ShadowSourceReviewError, match="unsupported fields"):
        review_shadow_sources(b, s, NEXT)


def extend_lifecycle(s, b):
    def event(role, event_id, at, payload):
        identity = s.add(role, {"event_id": event_id, "effective_at": at, "recorded_at": at, "payload": payload}, at, instruments=[XYZ])
        b["event_version_ids"].append(identity)
        return identity
    event("DIVIDEND_EX", "ex", "2026-09-15T12:00:00+00:00", {
        "instrument_id": XYZ, "currency": "USD", "action_id": "dividend", "entitlement_rule": "pre_ex_position",
        "distribution_type": "normal_cash", "record_at": NEXT, "pay_at": "2026-09-16T14:00:00+00:00",
        "entitled_quantity": "2", "gross_per_share": "2", "withholding": "1"})
    event("SPLIT", "split", "2026-09-15T13:00:00+00:00", {
        "instrument_id": XYZ, "currency": "USD", "action_id": "split", "new_shares": "2", "old_shares": "1"})
    event("SETTLEMENT", "buy-settle", "2026-09-15T13:30:00+00:00", {
        "instrument_id": XYZ, "currency": "USD", "fill_event_id": "buy", "amount": "-60"})
    sell_time = "2026-09-15T15:00:00+00:00"
    sell_quote = s.add("QUOTE", {"instrument_id": XYZ, "currency": "USD", "price_basis": "unadjusted", "price": "16",
                                "mark_convention": "provider_explicit_mark"}, sell_time, instruments=[XYZ])
    event("FILL", "sell", sell_time, {"instrument_id": XYZ, "run_id": "synthetic-run", "order_id": "order-sell",
        "fill_id": "fill-sell", "currency": "USD", "asset_class": "equity", "sample_type": "synthetic_fixture",
        "price_basis": "unadjusted", "side": "SELL", "signal_at": "2026-09-15T14:59:58+00:00",
        "order_at": "2026-09-15T14:59:59+00:00", "quote_version_id": sell_quote, "quantity": "4", "price": "16",
        "fee": "1", "settlement_at": "2026-09-16T13:30:00+00:00"})
    def finish(day, since, actions):
        at = f"2026-09-{day}T20:00:00+00:00"
        cal = s.add("CALENDAR", {"session_id": f"XNYS:2026-09-{day}", "previous_session_id": f"XNYS:2026-09-{day-1}",
                               "opens_at": f"2026-09-{day}T13:30:00+00:00", "closes_at": at,
                               "exchange": "XNYS", "timezone": "America/New_York"}, at, observed_at=OPEN)
        cov = s.add("ACTION_COVERAGE", {"from_at": since, "through_at": at, "instrument_ids": [XYZ], "complete": True,
            "action_ids": actions, "unsupported_action_ids": [], "coverage_convention": "provider_complete_for_instruments_and_interval"}, at, instruments=[XYZ])
        event("CLOSE", f"close-{day}", at, {"session_version_id": cal, "coverage_version_id": cov,
                                           "quote_version_ids": [], "statement_version_id": None})
    finish(15, CLOSE, ["dividend", "split"])
    event("SETTLEMENT", "sell-settle", "2026-09-16T13:30:00+00:00", {
        "instrument_id": XYZ, "currency": "USD", "fill_event_id": "sell", "amount": "64"})
    event("DIVIDEND_PAY", "pay", "2026-09-16T14:00:00+00:00", {
        "instrument_id": XYZ, "currency": "USD", "action_id": "dividend", "amount": "3"})
    finish(16, NEXT, [])
    b["sources"] = deepcopy(list(s.refs.values()))
    b["interval"]["through_at"] = "2026-09-16T20:00:00+00:00"
    return b


def test_cross_day_split_unsettled_principal_sale_dividend_right_and_tax_reconstruct_by_hand():
    s, b = fixture()
    extend_lifecycle(s, b)
    report = review_shadow_sources(b, s, b["interval"]["through_at"])
    close = report["accounting_summary"]["latest_close"]
    # 100 - 60 - 1 + 64 - 1 + (2 shares * $2 - $1 tax) =105; split adds no cash.
    assert close["equity"] == close["settled_cash"] == close["economic_cash"] == "105"
    assert close["cumulative_pnl"] == "5"
    assert close["realized_gross"] == "4"
    assert close["fees"] == "2" and close["dividend_income"] == "4" and close["withholding_tax"] == "1"
    assert close["dividend_receivable"] == "0" and close["positions"] == []
    assert report["accounting_summary"]["unsettled_trade_count"] == 0
    assert report["accounting_summary"]["close_count"] == 3
    assert report["evidence_states"]["source_authenticity"] == "UNVERIFIED_REQUIRES_TRUSTED_PROVIDER"


@pytest.mark.parametrize("field,value", [("equity", "1"), ("market_value", "1"), ("pnl_ignored", "1"), ("currency", "EUR")])
def test_source_bound_statement_cannot_ignore_supplied_wrong_or_unknown_metrics(field, value):
    s, b = fixture()
    identity = next(row["version_id"] for row in b["sources"] if row["role"] == "STATEMENT")
    replacement = s.change(b, identity, lambda doc: doc["data"].update({field: value}))
    s.change(b, b["event_version_ids"][-1], lambda doc: doc["data"]["payload"].update(statement_version_id=replacement))
    with pytest.raises(ShadowSourceReviewError):
        review_shadow_sources(b, s, NEXT)


@pytest.mark.parametrize("role,change", [
    ("ACTION_COVERAGE", lambda d: d.update(complete=False)),
    ("ACTION_COVERAGE", lambda d: d.update(unsupported_action_ids=["unknown-merger"])),
    ("ACTION_COVERAGE", lambda d: d.update(through_at="2026-09-14T19:59:59+00:00")),
    ("ACTION_COVERAGE", lambda d: d.update(action_ids=["future-dividend"])),
    ("CALENDAR", lambda d: d.update(previous_session_id="XNYS:2026-09-11")),
    ("CALENDAR", lambda d: d.update(timezone="America/Los_Angeles")),
])
def test_missing_coverage_unsupported_actions_or_calendar_scope_reject(role, change):
    s, b = fixture()
    identity = next(row["version_id"] for row in b["sources"] if row["role"] == role)
    replacement = s.change(b, identity, lambda doc: change(doc["data"]))
    field = "coverage_version_id" if role == "ACTION_COVERAGE" else "session_version_id"
    s.change(b, b["event_version_ids"][-1], lambda doc: doc["data"]["payload"].update({field: replacement}))
    with pytest.raises(ShadowSourceReviewError):
        review_shadow_sources(b, s, NEXT)


def test_cash_only_opening_is_reviewable_without_claiming_a_close():
    s = Sources()
    opening = s.add("OPENING", {"opened_at": OPEN, "initial_cash": "100", "capital_basis": "cash_only_no_positions_or_receivables"}, OPEN)
    b = s.bundle(opening, [], through=OPEN)
    report = review_shadow_sources(b, s, OPEN)
    assert report["event_plan"] == [] and report["evidence_states"]["coverage"] == "UNKNOWN_NO_CLOSE"


def test_duplicate_json_fields_rejected_after_true_hash_verification():
    s = Sources()
    identity = s.add("OPENING", {"opened_at": OPEN, "initial_cash": "100", "capital_basis": "cash_only_no_positions_or_receivables"}, OPEN)
    v, request, raw = s.records[identity]
    raw = raw.replace(b'"initial_cash":"100"', b'"initial_cash":"999","initial_cash":"100"')
    digest = sha256(raw).hexdigest()
    version = v.to_dict()
    version.update(content_sha256=digest, n_bytes=len(raw))
    new_id = canonical_hash({key: version[key] for key in ("request_key", "content_sha256", "effective_at", "observed_at", "finality")})
    version["version_id"] = new_id
    ref = deepcopy(s.refs[identity])
    ref.update(version_id=new_id, content_sha256=digest)
    s.records = {new_id: (version, request, raw)}
    s.refs = {new_id: ref}
    with pytest.raises(ShadowSourceReviewError, match="duplicate JSON"):
        review_shadow_sources(s.bundle(new_id, [], through=OPEN), s, OPEN)
