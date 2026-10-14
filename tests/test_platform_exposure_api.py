"""Exposure report preserves a pinned baseline and remains a read-only action."""
import json

import pytest

from market_intel.platform.performance import PerformanceStore
from market_intel.platform.service import PlatformBlocked, PlatformInputError
from test_platform_api import FakePlatformService, client
from test_platform_measurement_integration import attach, request


def setup_store(tmp_path):
    store = PerformanceStore(tmp_path / "performance.sqlite")
    commodity, _ = attach(store, "commodity_arbitrage")
    msci, _ = attach(store, "msci_rebalance", event=True)
    base = store.analyze(request(commodity, msci))
    body = {"base_request": base["request"], "base_analysis_id": base["analysis_id"]}
    return store, base, body


def test_pinned_grid_reproduces_baseline_and_does_not_write(tmp_path):
    store, base, body = setup_store(tmp_path)
    before = {p: p.read_bytes() for p in (store.path, store.activity.path)}
    result = store.exposure_report(body)
    assert store.exposure_report(result["request"]) == result
    assert result["position_scenarios"][0]["metrics"]["total_return"] == pytest.approx(base["metrics"]["total_return"])
    assert result["capital_scenarios"]["scenarios"][0]["metrics"] == base["metrics"]
    assert result["request"]["base_request"]["profile_ids"] == base["request"]["profile_ids"]
    assert result["msci_event"]["evaluation"]["event_count"] == 2
    assert result["funding_validated"] is False
    assert result["validation_status"] == "NOT VALIDATED"
    assert all(p.read_bytes() == data for p, data in before.items())


def test_stale_base_and_manual_requests_fail_closed(tmp_path):
    store, _, body = setup_store(tmp_path)
    with pytest.raises(PlatformBlocked, match="base analysis changed"):
        store.exposure_report({**body, "base_analysis_id": "stale"})
    with pytest.raises(PlatformInputError, match="completed activity"):
        store.exposure_report({"base_request": {"method": "manual"}})


def test_viewer_can_download_exact_comparison_but_cross_origin_is_rejected(tmp_path):
    from market_intel import identity
    store, _, body = setup_store(tmp_path)
    service = FakePlatformService()
    service.performance = store
    c = client(tmp_path, service, remote=True)
    _, token = identity.create_account("exposure-viewer", "Exposure viewer", "viewer")
    headers = {"Authorization": f"Bearer {token}"}
    reply = c.post("/api/v2/platform/performance/exposure", json=body, headers=headers)
    assert reply.status_code == 200, reply.text
    result = reply.json()
    params = {"payload": json.dumps(result["request"]), "report_id": result["report_id"]}
    download = c.get("/api/v2/platform/performance/exposure/export", params=params, headers=headers)
    assert download.status_code == 200, download.text
    assert download.json() == result
    assert "attachment" in download.headers["content-disposition"]
    assert c.get("/api/v2/platform/performance/exposure/export", params={**params, "report_id": "stale"}, headers=headers).status_code == 409
    assert c.post("/api/v2/platform/performance/exposure", json=body, headers={**headers, "Origin": "https://elsewhere.invalid"}).status_code == 403
