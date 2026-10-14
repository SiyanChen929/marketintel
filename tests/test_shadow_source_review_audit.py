"""Independent real-catalog integration and explicit v1 late-arrival limits.

All payloads are synthetic. Actual DatasetCatalog is exercised in tmp_path;
passing the review never authenticates vendor data or observed fills.
"""
from copy import deepcopy
from datetime import date, datetime
import json

import pytest

from market_intel.platform.core import DatasetRequest
from market_intel.platform.data import DatasetCatalog
from market_intel.platform.shadow_source_review import ShadowSourceReviewError, review_shadow_sources
from test_platform_shadow_source_review import Sources, fixture, extend_lifecycle, OPEN, FILL, NEXT


def persisted_sources(tmp_path, sources):
    catalog = DatasetCatalog(tmp_path / "catalog.sqlite")
    for identity, (version, request, raw) in sources.records.items():
        req = DatasetRequest(provider=request["provider"], entitlement=request["entitlement"],
            dataset=request["dataset"], instrument_ids=tuple(request["instrument_ids"]), fields=tuple(request["fields"]),
            frequency=request["frequency"], adjustment=request["adjustment"],
            start=date.fromisoformat(request["date_range"][0]), end=date.fromisoformat(request["date_range"][1]),
            as_of=datetime.fromisoformat(request["vintage_as_of"]), schema_version=request["schema_version"],
            parameters=request["parameters"])
        actual = catalog.register(req, raw, effective_at=version.effective_at, observed_at=version.observed_at,
                                  acquired_at=version.acquired_at, finality=version.finality.value)
        assert actual.version_id == identity
    return catalog


def replace_source(sources, bundle, identity, *, mutate_document=None, acquired_at=None, vintage_asof=None):
    version, request, raw = sources.records[identity]
    document = json.loads(raw)
    if mutate_document:
        mutate_document(document)
    request = deepcopy(request)
    if vintage_asof:
        request["vintage_as_of"] = vintage_asof
    replacement = sources.pack(document, request, version.effective_at.isoformat(), version.observed_at.isoformat(),
                               acquired_at or version.acquired_at.isoformat())
    # Rebind any direct source-version reference in other normalized records.
    bundle["sources"] = [deepcopy(sources.refs[replacement]) if row["version_id"] == identity else row for row in bundle["sources"]]
    bundle["event_version_ids"] = [replacement if value == identity else value for value in bundle["event_version_ids"]]
    if bundle["opening_version_id"] == identity:
        bundle["opening_version_id"] = replacement
    if replacement != identity:
        del sources.records[identity]
        del sources.refs[identity]
    return replacement


def test_real_catalog_contract_accepts_complete_synthetic_multi_day_replay_without_mutation(tmp_path):
    sources, bundle = fixture()
    extend_lifecycle(sources, bundle)
    catalog = persisted_sources(tmp_path, sources)
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = review_shadow_sources(bundle, catalog, bundle["interval"]["through_at"])
    close = result["accounting_summary"]["latest_close"]
    # $100 - $60 principal - $1 buy fee + $64 proceeds - $1 sell fee
    # + ($4 gross dividend - $1 withholding) = $105; split creates no wealth.
    assert close["equity"] == close["settled_cash"] == close["economic_cash"] == "105"
    assert close["dividend_receivable"] == "0"
    assert result["accounting_summary"]["unsettled_trade_count"] == 0
    assert result["evidence_states"]["source_authenticity"] == "UNVERIFIED_REQUIRES_TRUSTED_PROVIDER"
    assert result["validation_status"] == "NOT VALIDATED"
    after = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after


def test_v1_rejects_valid_catalog_broker_fill_confirmation_received_one_second_later(tmp_path):
    sources, bundle = fixture()
    identity = bundle["event_version_ids"][0]
    acquired = "2026-09-14T14:00:01+00:00"
    replacement = replace_source(sources, bundle, identity, acquired_at=acquired, vintage_asof=acquired,
        mutate_document=lambda doc: doc["data"].update(recorded_at="2026-09-14T14:00:02+00:00"))
    catalog = persisted_sources(tmp_path, sources)
    version, _, _ = catalog.get_version(replacement)
    assert version.effective_at.isoformat() == FILL
    assert version.acquired_at.isoformat() == acquired
    # This is an explicit v1 unsupported-late-arrival case, not an invalid
    # economic fill. A v2 reconciliation must preserve all three real clocks.
    with pytest.raises(ShadowSourceReviewError) as rejection:
        review_shadow_sources(bundle, catalog, NEXT)
    assert rejection.value.code == "UNAVAILABLE_AT_BOUNDARY"
    assert "acquired after its accounting boundary" in str(rejection.value)


def test_v1_vintage_rule_rejects_ordinary_request_then_download_lag_even_for_early_calendar(tmp_path):
    sources, bundle = fixture()
    identity = next(row["version_id"] for row in bundle["sources"] if row["role"] == "CALENDAR")
    acquired = "2026-09-14T13:00:01+00:00"
    replacement = replace_source(sources, bundle, identity, acquired_at=acquired)
    # Rebind the close's calendar reference while preserving its valid fields.
    close_id = bundle["event_version_ids"][-1]
    replace_source(sources, bundle, close_id,
                   mutate_document=lambda doc: doc["data"]["payload"].update(session_version_id=replacement))
    catalog = persisted_sources(tmp_path, sources)
    version, request, _ = catalog.get_version(replacement)
    assert request["vintage_as_of"] == OPEN
    assert version.acquired_at.isoformat() == acquired
    with pytest.raises(ShadowSourceReviewError) as rejection:
        review_shadow_sources(bundle, catalog, NEXT)
    assert rejection.value.code == "UNAVAILABLE_AT_BOUNDARY"
    assert "requested vintage must include acquisition" in str(rejection.value)
