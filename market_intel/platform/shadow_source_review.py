"""Pure review of pinned source bytes into an uncommitted shadow event plan.

The v1 input is an explicit normalized source contract, not an adapter for an
arbitrary vendor response. Original provenance, timestamp truthfulness and
provider completeness remain external obligations. Checksums and a successful
replay cannot establish them. The injected reader must itself be read-only.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .core import canonical_hash
from .shadow_ledger import ShadowAccountingError, ShadowLedger

BUNDLE_SCHEMA = "platform.shadow-source-bundle.v1"
SOURCE_SCHEMA = "platform.shadow-source-record.v1"
REVIEW_SCHEMA = "platform.shadow-source-review.v1"
UNITS = {"cash": "USD", "price": "USD/share", "quantity": "share", "ratio": "dimensionless"}
EVENT_ROLES = frozenset({"FILL", "SETTLEMENT", "DIVIDEND_EX", "DIVIDEND_PAY", "SPLIT", "CLOSE"})
ROLES = EVENT_ROLES | {"OPENING", "QUOTE", "CALENDAR", "ACTION_COVERAGE", "STATEMENT"}
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")
_INSTRUMENT = re.compile(r"^equity:[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_MONEY = {"initial_cash", "price", "fee", "amount", "gross_per_share", "withholding",
          "settled_cash", "economic_cash", "dividend_receivable", "equity", "market_value",
          "available_cash", "realized_gross", "unrealized_gross", "fees", "dividend_income",
          "withholding_tax", "cumulative_pnl", "period_pnl", "period_return", "quantity",
          "entitled_quantity", "new_shares", "old_shares"}
_METRICS = {"equity", "market_value", "available_cash", "realized_gross", "unrealized_gross",
            "fees", "dividend_income", "withholding_tax", "cumulative_pnl", "period_pnl", "period_return"}
_PAYLOAD_KEYS = {
    "FILL": {"instrument_id", "run_id", "order_id", "fill_id", "currency", "asset_class",
             "sample_type", "price_basis", "side", "signal_at", "order_at", "quote_version_id",
             "quantity", "price", "fee", "settlement_at"},
    "SETTLEMENT": {"instrument_id", "fill_event_id", "amount", "currency"},
    "DIVIDEND_EX": {"action_id", "instrument_id", "currency", "entitlement_rule", "distribution_type",
                    "record_at", "pay_at", "entitled_quantity", "gross_per_share", "withholding"},
    "DIVIDEND_PAY": {"instrument_id", "action_id", "amount", "currency"},
    "SPLIT": {"action_id", "instrument_id", "currency", "new_shares", "old_shares"},
    "CLOSE": {"session_version_id", "coverage_version_id", "quote_version_ids", "statement_version_id"},
}


class ShadowSourceReviewError(ValueError):
    """Fail-closed source review rejection with a machine-readable category."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise ShadowSourceReviewError(code, message)


def _keys(value: Any, required: set[str], label: str, optional: set[str] | None = None) -> dict:
    if not isinstance(value, Mapping):
        _fail("INVALID_SCHEMA", f"{label} must be an object")
    keys = set(value)
    if required - keys:
        _fail("INCOMPLETE_EVIDENCE", f"{label} missing fields: {sorted(required - keys)}")
    extra = keys - required - (optional or set())
    if extra:
        _fail("INVALID_SCHEMA", f"{label} has unsupported fields: {sorted(map(str, extra))}")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        _fail("INVALID_SCHEMA", f"{label} must be nonempty exact text")
    return value


def _time(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        if not isinstance(value, str) or not _TIME.fullmatch(value):
            _fail("UNSUPPORTED_TIME_PRECISION", f"{label} requires an explicit offset and whole-second timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _fail("INVALID_SCHEMA", f"invalid {label}")
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        _fail("UNSUPPORTED_TIME_PRECISION", f"{label} requires timezone-aware whole seconds")
    return parsed.astimezone(timezone.utc)


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _fail("INVALID_SCHEMA", f"{label} must be a full lowercase SHA-256")
    return value


def _ids(value: Any, label: str, *, instruments=False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        _fail("INVALID_SCHEMA", f"{label} must be a list of exact IDs")
    if len(value) != len(set(value)):
        _fail("CONFLICTING_IDENTITY", f"{label} contains duplicate IDs")
    if instruments and any(not _INSTRUMENT.fullmatch(x) for x in value):
        _fail("UNSUPPORTED_INSTRUMENT", f"{label} requires stable equity IDs")
    return list(value)


def _scalar_fields(value: Mapping) -> None:
    for key, item in value.items():
        if key in _MONEY:
            if not isinstance(item, str):
                _fail("INVALID_UNITS", f"{key} must be a finite decimal string")
            try:
                if not Decimal(item).is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                _fail("INVALID_UNITS", f"{key} must be a finite decimal string")
        elif key.endswith("_at") or key == "asof":
            _time(item, key)
        elif key == "quantities":
            if not isinstance(item, Mapping):
                _fail("INVALID_SCHEMA", "quantities must be a security-to-decimal mapping")
            _ids(list(item), "quantities", instruments=True)
            for quantity in item.values():
                _scalar_fields({"quantity": quantity})


def _json(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("INVALID_SCHEMA", "duplicate JSON object field")
            result[key] = value
        return result
    def nonfinite(_):
        _fail("INVALID_SCHEMA", "non-finite JSON value")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("INVALID_SCHEMA", "source must contain strict UTF-8 JSON")
    if not isinstance(value, dict):
        _fail("INVALID_SCHEMA", "source must contain a JSON object")
    return value


def _leaves(value: Any, path: str = "") -> list[str]:
    if isinstance(value, Mapping) and value:
        return [p for key in sorted(value) for p in _leaves(value[key], path + "/" + str(key).replace("~", "~0").replace("/", "~1"))]
    if isinstance(value, list) and value:
        return [p for i, item in enumerate(value) for p in _leaves(item, path + "/" + str(i))]
    return [path]


def review_shadow_sources(bundle: Mapping, version_reader: Any, review_asof: Any) -> dict:
    """Verify supplied bytes and return a plan; never create storage or book events.

    ``version_reader`` is a callable or an existing object's ``get_version``;
    it returns ``(DatasetVersion-or-mapping, request-mapping, bytes)``. Reader
    side effects and authentic provider delivery are the caller's trust boundary.
    Rejections raise :class:`ShadowSourceReviewError`; no partial plan is returned.
    Only a cash-only opening followed by the complete ordered journal is accepted
    in v1. Continuations and transformations of vendor formats require an adapter.
    """
    now = _time(review_asof, "review_asof")
    b = deepcopy(_keys(bundle, {"schema_version", "book_id", "currency", "sample_type", "interval",
                                 "opening_version_id", "event_version_ids", "sources", "limits"}, "bundle"))
    if b["schema_version"] != BUNDLE_SCHEMA:
        _fail("INVALID_SCHEMA", "unknown bundle schema")
    _text(b["book_id"], "book_id")
    if b["currency"] != "USD" or b["sample_type"] not in {"observed_paper", "synthetic_fixture"}:
        _fail("UNSUPPORTED_SCOPE", "only explicit USD observed_paper or synthetic_fixture books are supported")
    interval = _keys(b["interval"], {"from_at", "through_at"}, "interval")
    start, end = (_time(interval[k], k) for k in ("from_at", "through_at"))
    if not start <= end <= now:
        _fail("UNAVAILABLE_AT_BOUNDARY", "review interval is reversed or in the future")
    limits = _keys(b["limits"], {"max_quote_age_seconds", "max_acquisition_lag_seconds"}, "limits")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in limits.values()):
        _fail("INVALID_SCHEMA", "quote limits must be nonnegative integer seconds")
    opening_id = _hash(b["opening_version_id"], "opening_version_id")
    event_ids = _ids(b["event_version_ids"], "event_version_ids")
    for identity in event_ids:
        _hash(identity, "event version")
    if not isinstance(b["sources"], list) or not b["sources"]:
        _fail("INCOMPLETE_EVIDENCE", "pinned sources are required")
    reader = getattr(version_reader, "get_version", version_reader)
    if not callable(reader):
        _fail("INVALID_SCHEMA", "version_reader must be an existing read-only callable")
    refs: dict[str, dict] = {}
    for ref in b["sources"]:
        ref = _keys(ref, {"version_id", "content_sha256", "request_key", "role", "provider",
                          "instrument_ids", "acquired_at"}, "source reference")
        identity = _hash(ref["version_id"], "version_id")
        if identity in refs:
            _fail("CONFLICTING_IDENTITY", "source version is listed twice")
        _hash(ref["content_sha256"], "content_sha256")
        _hash(ref["request_key"], "request_key")
        _text(ref["provider"], "provider")
        _ids(ref["instrument_ids"], "source instruments", instruments=True)
        _time(ref["acquired_at"], "pinned acquired_at")
        if ref["role"] not in ROLES:
            _fail("UNSUPPORTED_SCOPE", "unknown source role")
        refs[identity] = ref

    sources: dict[str, dict] = {}
    bindings: list[dict] = []
    for identity in sorted(refs):
        ref = refs[identity]
        try:
            version, request, raw = reader(identity)
        except (KeyError, OSError, ValueError, TypeError) as exc:
            raise ShadowSourceReviewError("SOURCE_UNAVAILABLE", f"cannot resolve pinned version {identity}") from exc
        version = version.to_dict() if hasattr(version, "to_dict") else version
        v = _keys(version, {"version_id", "request_key", "content_sha256", "n_bytes", "effective_at",
                            "observed_at", "acquired_at", "finality", "schema_version"},
                  "version metadata", {"blob_path"})
        if not isinstance(raw, bytes) or isinstance(v["n_bytes"], bool) or not isinstance(v["n_bytes"], int):
            _fail("INTEGRITY_FAILURE", "source reader must return exact bytes and byte count")
        if (v["version_id"] != identity or v["request_key"] != ref["request_key"]
                or v["content_sha256"] != ref["content_sha256"] or len(raw) != v["n_bytes"]
                or sha256(raw).hexdigest() != ref["content_sha256"]):
            _fail("INTEGRITY_FAILURE", "pinned source bytes or identity changed")
        r = _keys(request, {"provider", "entitlement", "dataset", "instrument_ids", "fields", "frequency",
                            "adjustment", "date_range", "vintage_as_of", "schema_version", "parameters"}, "request")
        if canonical_hash(r) != v["request_key"]:
            _fail("INTEGRITY_FAILURE", "request does not match its pinned hash")
        effective, observed, acquired = (_time(v[k], "version " + k) for k in ("effective_at", "observed_at", "acquired_at"))
        if v["finality"] not in {"confirmed", "final"} or not observed <= acquired <= now:
            _fail("UNAVAILABLE_AT_BOUNDARY", "source is provisional, future or has inconsistent acquisition time")
        if acquired != _time(ref["acquired_at"], "pinned acquired_at"):
            _fail("INTEGRITY_FAILURE", "source acquisition time differs from its pin")
        identity_fields = {"request_key": v["request_key"], "content_sha256": v["content_sha256"],
                           "effective_at": effective.isoformat(), "observed_at": observed.isoformat(), "finality": v["finality"]}
        if canonical_hash(identity_fields) != identity:
            _fail("INTEGRITY_FAILURE", "version metadata does not match its identity")
        if (v["schema_version"] != SOURCE_SCHEMA or r["schema_version"] != SOURCE_SCHEMA
                or r["provider"] != ref["provider"] or r["dataset"] != "shadow_source." + ref["role"].lower()
                or set(_ids(r["instrument_ids"], "request scope IDs")) != set(
                    ref["instrument_ids"] or ["shadow-book:" + sha256(b["book_id"].encode()).hexdigest()])
                or r["frequency"] != "event" or r["adjustment"] != "unadjusted" or r["fields"] != ["record"]
                or r["parameters"] != {}):
            _fail("BINDING_FAILURE", "request role, security, schema or normalization contract mismatch")
        if not acquired <= _time(r["vintage_as_of"], "request vintage_as_of") <= now:
            _fail("UNAVAILABLE_AT_BOUNDARY", "requested vintage must include acquisition and not exceed review time")
        _text(r["entitlement"], "request entitlement")
        dates = r["date_range"]
        try:
            if (not isinstance(dates, list) or len(dates) != 2
                    or any(not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates)
                    or not date.fromisoformat(dates[0]) <= effective.date() <= date.fromisoformat(dates[1])):
                _fail("BINDING_FAILURE", "v1 request date range must include the source effective UTC date")
        except ValueError as exc:
            if isinstance(exc, ShadowSourceReviewError):
                raise
            _fail("INVALID_SCHEMA", "invalid request date range")
        doc = _keys(_json(raw), {"schema_version", "role", "book_id", "currency", "sample_type", "instrument_ids",
                               "timestamp_precision", "units", "data"}, "source record")
        if (doc["schema_version"] != SOURCE_SCHEMA or doc["role"] != ref["role"] or doc["book_id"] != b["book_id"]
                or doc["currency"] != b["currency"] or doc["sample_type"] != b["sample_type"]
                or set(_ids(doc["instrument_ids"], "record instruments", instruments=True)) != set(ref["instrument_ids"])):
            _fail("BINDING_FAILURE", "source record belongs to another role, book, currency, sample type or security")
        if doc["units"] != UNITS:
            _fail("INVALID_UNITS", "only exact USD, USD/share, share and dimensionless units are supported")
        if doc["timestamp_precision"] != "second":
            _fail("UNSUPPORTED_TIME_PRECISION", "v1 requires whole-second source timestamps")
        sources[identity] = {"doc": doc, "version": v, "effective": effective, "observed": observed,
                             "acquired": acquired, "evidence": {"source": ref["provider"], "content_sha256": ref["content_sha256"],
                             "observed_at": observed.isoformat(), "available_at": acquired.isoformat(), "finality": v["finality"]}}
        bindings.extend({"version_id": identity, "json_pointer": path, "transformation_id": "exact_source_field.v1"}
                        for path in _leaves(doc))

    used: set[str] = set()
    def source(identity, role, boundary):
        _hash(identity, role + " version")
        if identity not in sources:
            _fail("INCOMPLETE_EVIDENCE", f"{role} reference is not pinned")
        s = sources[identity]
        if s["doc"]["role"] != role:
            _fail("BINDING_FAILURE", "source reference has the wrong role")
        if s["acquired"] > boundary:
            _fail("UNAVAILABLE_AT_BOUNDARY", role + " source was acquired after its accounting boundary")
        used.add(identity)
        return s

    def exact_instruments(s, ids):
        if set(s["doc"]["instrument_ids"]) != set(ids):
            _fail("BINDING_FAILURE", "source instrument scope does not match accounting fields")

    def auxiliary(identity, role, boundary):
        s = source(identity, role, boundary)
        requirements = {
            "QUOTE": {"instrument_id", "currency", "price_basis", "price", "mark_convention"},
            "CALENDAR": {"session_id", "previous_session_id", "opens_at", "closes_at", "exchange", "timezone"},
            "ACTION_COVERAGE": {"from_at", "through_at", "instrument_ids", "complete", "action_ids", "unsupported_action_ids", "coverage_convention"},
            "STATEMENT": {"asof", "currency", "settled_cash", "economic_cash", "dividend_receivable", "quantities"},
        }
        data = _keys(s["doc"]["data"], requirements[role], role, _METRICS if role == "STATEMENT" else None)
        _scalar_fields(data)
        if "currency" in data and data["currency"] != "USD":
            _fail("INVALID_UNITS", role + " currency must be USD")
        if role == "QUOTE":
            exact_instruments(s, [data["instrument_id"]])
            if data.pop("mark_convention") != "provider_explicit_mark" or s["effective"] != s["observed"]:
                _fail("BINDING_FAILURE", "quote requires an explicit contemporaneous provider mark")
        elif role == "CALENDAR":
            exact_instruments(s, [])
            if data["exchange"] not in {"XNYS", "XNAS"} or data["timezone"] != "America/New_York":
                _fail("UNSUPPORTED_SCOPE", "calendar exchange/timezone is unsupported")
            opens, closes = _time(data["opens_at"], "opens_at"), _time(data["closes_at"], "closes_at")
            local = ZoneInfo(data["timezone"])
            if (opens.astimezone(local).date() != closes.astimezone(local).date()
                    or data["session_id"] != data["exchange"] + ":" + closes.astimezone(local).date().isoformat()
                    or s["effective"] != closes):
                _fail("BINDING_FAILURE", "calendar identity/effective time differs from its close")
        elif role == "ACTION_COVERAGE":
            exact_instruments(s, _ids(data["instrument_ids"], "coverage instruments", instruments=True))
            _ids(data["action_ids"], "action_ids")
            _ids(data["unsupported_action_ids"], "unsupported_action_ids")
            if data.pop("coverage_convention") != "provider_complete_for_instruments_and_interval":
                _fail("UNSUPPORTED_SCOPE", "unknown corporate action coverage convention")
            if s["effective"] != _time(data["through_at"], "through_at"):
                _fail("BINDING_FAILURE", "coverage effective time differs from its interval")
        elif role == "STATEMENT":
            if s["effective"] != _time(data["asof"], "asof") or not set(data["quantities"]) <= set(s["doc"]["instrument_ids"]):
                _fail("BINDING_FAILURE", "statement identity/effective time mismatch")
        return {**deepcopy(data), "evidence": deepcopy(s["evidence"])}

    op = source(opening_id, "OPENING", start)
    opening = _keys(op["doc"]["data"], {"opened_at", "initial_cash", "capital_basis"}, "opening")
    _scalar_fields(opening)
    exact_instruments(op, [])
    if (opening["capital_basis"] != "cash_only_no_positions_or_receivables"
            or _time(opening["opened_at"], "opened_at") != start or op["effective"] != start or op["observed"] != start):
        _fail("BINDING_FAILURE", "opening must attest cash-only capital at the interval start")
    try:
        ledger = ShadowLedger(book_id=b["book_id"], opened_at=opening["opened_at"], initial_cash=opening["initial_cash"],
                              sample_type=b["sample_type"], **limits)
    except ShadowAccountingError as exc:
        raise ShadowSourceReviewError("ACCOUNTING_REPLAY_FAILURE", str(exc)) from exc
    plan, event_lineage, seen_events = [], [], set()
    for identity in event_ids:
        if identity not in sources or sources[identity]["doc"]["role"] not in EVENT_ROLES:
            _fail("BINDING_FAILURE", "event list contains a missing or non-event source")
        s = sources[identity]
        role = s["doc"]["role"]
        event = _keys(s["doc"]["data"], {"event_id", "effective_at", "recorded_at", "payload"}, "event")
        event_id = _text(event["event_id"], "event_id")
        if event_id in seen_events:
            _fail("CONFLICTING_IDENTITY", "duplicate source event ID")
        seen_events.add(event_id)
        at, recorded = _time(event["effective_at"], "effective_at"), _time(event["recorded_at"], "recorded_at")
        if not start <= at <= end or not at <= recorded <= now or s["effective"] != at:
            _fail("UNAVAILABLE_AT_BOUNDARY", "event time does not match its pinned source or review interval")
        if role in {"FILL", "SETTLEMENT", "DIVIDEND_PAY", "CLOSE"} and s["observed"] != at:
            _fail("BINDING_FAILURE", "observed cash/execution/close event requires contemporaneous source observation")
        source(identity, role, at)
        p = _keys(event["payload"], _PAYLOAD_KEYS[role], role + " payload")
        _scalar_fields(p)
        dependencies = []
        if role == "CLOSE":
            quote_ids = _ids(p["quote_version_ids"], "quote_version_ids")
            dependencies = [p["session_version_id"], p["coverage_version_id"], *quote_ids]
            if p["statement_version_id"] is not None:
                dependencies.append(p["statement_version_id"])
            session = auxiliary(p["session_version_id"], "CALENDAR", at)
            coverage = auxiliary(p["coverage_version_id"], "ACTION_COVERAGE", at)
            exact_instruments(s, coverage["instrument_ids"])
            quotes = {}
            for quote_id in quote_ids:
                quote = auxiliary(quote_id, "QUOTE", at)
                instrument = quote["instrument_id"]
                if instrument in quotes:
                    _fail("CONFLICTING_IDENTITY", "duplicate quote security")
                quotes[instrument] = quote
            statement = auxiliary(p["statement_version_id"], "STATEMENT", at) if p["statement_version_id"] is not None else None
            p = {"session": session, "corporate_action_coverage": coverage, "quotes": quotes, "external_reconciliation": statement}
        else:
            exact_instruments(s, [p["instrument_id"]])
            if p["currency"] != "USD":
                _fail("INVALID_UNITS", "event currency must be USD")
            if role == "FILL":
                dependencies.append(p["quote_version_id"])
                p["quote"] = auxiliary(p.pop("quote_version_id"), "QUOTE", at)
            if role == "SETTLEMENT":
                previous = ledger.pending_trades.get(p["fill_event_id"])
                if previous is None or previous["instrument_id"] != p.pop("instrument_id"):
                    _fail("BINDING_FAILURE", "settlement security differs from its original fill")
            if role == "DIVIDEND_PAY":
                previous = ledger.dividends.get(p["action_id"])
                if previous is None or previous["instrument_id"] != p.pop("instrument_id"):
                    _fail("BINDING_FAILURE", "dividend payment security differs from its entitlement")
            p["evidence"] = deepcopy(s["evidence"])
        planned = {"event_id": event_id, "kind": role, "effective_at": at.isoformat(),
                   "recorded_at": recorded.isoformat(), "payload": p}
        try:
            ledger.append(**planned)
        except (ShadowAccountingError, TypeError, KeyError) as exc:
            raise ShadowSourceReviewError("ACCOUNTING_REPLAY_FAILURE", str(exc)) from exc
        plan.append(planned)
        event_lineage.append({"event_id": event_id, "source_version_id": identity,
                              "auxiliary_version_ids": dependencies, "record_pointer": "/data",
                              "transformation_id": "shadow_source_to_ledger.v1"})
    if used != set(sources):
        _fail("BINDING_FAILURE", "bundle contains unreferenced sources; complete binding is required")
    if (plan and _time(plan[-1]["effective_at"], "last event") != end) or (not plan and start != end):
        _fail("INCOMPLETE_EVIDENCE", "event plan does not reach its declared interval boundary")
    # Source array ordering is irrelevant; event order is economically significant.
    canonical_bundle = {**b, "sources": [refs[k] for k in sorted(refs)]}
    summary = ledger.summary()
    return {"schema_version": REVIEW_SCHEMA, "status": "REVIEWABLE_EVENT_PLAN", "review_asof": now.isoformat(),
            "bundle_hash": canonical_hash(canonical_bundle), "event_plan_hash": canonical_hash(plan),
            "book_config": deepcopy(ledger.config), "event_plan": plan, "field_bindings": bindings,
            "event_lineage": event_lineage, "opening_source_version_id": opening_id,
            "source_versions": sorted(sources), "candidate_head_hash": ledger.head, "accounting_summary": summary,
            "validation_status": "NOT VALIDATED", "execution_status": "SHADOW_ACCOUNTING_ONLY",
            "actionability": "NOT ACTIONABLE", "paper_overnight_enabled": False, "live_trading_supported": False,
            "evidence_states": {"bytes_integrity": "PINNED_BYTES_AND_IDENTITY_MATCHED",
                "field_binding": "EXACT_NORMALIZED_SOURCE_FIELDS", "availability": "DECLARED_ACQUISITION_TIMES_CHECKED",
                "source_authenticity": "UNVERIFIED_REQUIRES_TRUSTED_PROVIDER",
                "coverage": "DECLARED_UNVERIFIED" if ledger.closes else "UNKNOWN_NO_CLOSE",
                "accounting_replay": "IN_MEMORY_REPLAY_RECONCILED",
                "external_reconciliation": summary["latest_close"]["external_reconciliation_status"] if ledger.closes else "UNKNOWN_NO_CLOSE",
                "external_statement_independence": "UNVERIFIED", "head_checkpoint": "UNCOMMITTED_PLAN_NO_INDEPENDENT_CHECKPOINT"},
            "limitations": ["No source origin, acquisition timestamp truthfulness or provider completeness has been authenticated.",
                "Exact field binding applies to the normalized source record; vendor-to-record transformations need independent evidence.",
                "The caller must supply a read-only version reader; this function performs no filesystem or network access.",
                "No ledger is written, no execution is authorized, and no investment return is validated."]}
