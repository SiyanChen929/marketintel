"""Integration of frozen monthly NAV, pinned activity evidence and policy exports."""
from __future__ import annotations

import copy
import json

import pytest

from market_intel.platform.performance import PerformanceStore
from market_intel.platform.service import PlatformBlocked, PlatformInputError
from test_platform_allocation import fixture, set_return
from test_platform_api import FakePlatformService, client
from test_platform_performance import payload


def attach(store, sid="strategy_a", *, event=False, active=True):
    draft, observations = fixture(sid, event=event, inactive=not active)
    if not event:
        set_return(draft, observations, "2023-02", .1)
    body = payload(sid)
    body["rows"] = draft["rows"]
    artifact = store.get(store.import_artifact(body)["artifact_id"])
    nav, rows = 1., []
    for row in observations["rows"]:
        closing = nav * (1 + row["return"])
        rows.append({**row, "nav_open": nav, "nav_close": closing, "net_pnl": closing - nav,
                     "gross_open": 1. if row["active"] else 0., "gross_close": 1. if row["active"] else 0.,
                     "margin_open": None, "margin_close": None,
                     "event_id": row["date"] if event else None})
        nav = closing
    profile = {"return_artifact_id": artifact["artifact_id"], "strategy_id": sid,
               "frequency": observations["frequency"], "sources": artifact["sources"],
               "capital_basis": "Source net NAV return", "margin_basis": "No confirmed margin data",
               "limitations": ["Research proxy, no executable funding evidence"], "rows": rows}
    return artifact, store.activity.put(profile, artifact)


def request(*artifacts, **kwargs):
    return {"method": "activity_risk_budget", "artifact_ids": [a["artifact_id"] for a in artifacts], **kwargs}


def revised_profile(store, artifact, profile):
    revised = copy.deepcopy(profile)
    revised.pop("profile_id")
    revised["limitations"].append("Revised activity classification; retained as a new immutable profile")
    for row in revised["rows"]:
        # The same source NAV can remain flat while a classifier finds no positions.
        # Remove prior activity only, keeping the nonzero evaluation-month P&L active.
        if row["date"] < "2023-02-01":
            row["active"] = False
            row["gross_open"] = row["gross_close"] = 0.
    return store.activity.put(revised, artifact)


def test_policy_pins_profile_and_old_result_survives_a_new_latest_profile(tmp_path):
    store = PerformanceStore(tmp_path / "performance.sqlite")
    artifact, first_profile = attach(store)
    first = store.analyze(request(artifact))
    assert first["request"]["profile_ids"] == [first_profile["profile_id"]]
    assert first["activity_profiles"][0]["profile_id"] == first_profile["profile_id"]
    second_profile = revised_profile(store, artifact, first_profile)
    assert store.activity.latest(artifact["artifact_id"])["profile_id"] == second_profile["profile_id"]
    assert store.analyze(first["request"]) == first
    second = store.analyze(request(artifact))
    assert second["analysis_id"] != first["analysis_id"]
    assert first["selected"][0]["weight"] == .4
    assert second["selected"][0]["weight"] == 0.


def test_wrong_parent_pin_and_malformed_pins_are_rejected(tmp_path):
    store = PerformanceStore(tmp_path / "performance.sqlite")
    a, pa = attach(store, "strategy_a")
    b, pb = attach(store, "strategy_b")
    with pytest.raises(PlatformBlocked, match="does not match"):
        store.analyze(request(a, profile_ids=[pb["profile_id"]]))
    with pytest.raises(PlatformInputError, match="profile_ids"):
        store.analyze(request(a, profile_ids=[]))
    with pytest.raises(PlatformInputError, match="at most one variant"):
        store.analyze(request(a, a))
    assert store.analyze(request(a, b, profile_ids=[pa["profile_id"], pb["profile_id"]]))["observations"] == 6


def test_missing_activity_blocks_automatic_sizing_but_preserves_manual_nav(tmp_path):
    store = PerformanceStore(tmp_path / "performance.sqlite")
    body = payload("missing_activity", (.1, -.1, .05))
    artifact = store.get(store.import_artifact(body)["artifact_id"])
    manual = {"sleeves": [{"artifact_id": artifact["artifact_id"], "weight": .5}]}
    result = store.analyze(manual)
    assert [r["nav"] for r in result["rows"]] == pytest.approx([1.05, .995, 1.01975])
    assert store.analyze({**manual, "method": "manual"}) == result
    with pytest.raises(PlatformBlocked, match="activity ledger required"):
        store.analyze(request(artifact))
    summary = store.activity_report({"artifact_ids": [artifact["artifact_id"]]})
    assert summary["profiles"][0]["status"] == "ACTIVITY_UNAVAILABLE"
    assert "active_fraction" not in summary["profiles"][0]


def test_activity_report_uses_requested_same_window_and_keeps_event_counts_separate(tmp_path):
    store = PerformanceStore(tmp_path / "performance.sqlite")
    a, _ = attach(store, "strategy_daily")
    b, _ = attach(store, "strategy_event", event=True)
    report = store.activity_report({"artifact_ids": [a["artifact_id"], b["artifact_id"]], "start": "2023-02", "end": "2023-07"})
    assert report["start"] == "2023-02"
    assert report["end"] == "2023-07"
    daily, event = report["profiles"]
    assert daily["active_fraction"] == 1
    assert daily["observations"] == 12
    assert event["event_count"] == 2
    assert event["active_fraction"] is None
    assert "not annualized" in event["active_return_unit"]
    with pytest.raises(PlatformBlocked, match="outside complete common"):
        store.activity_report({"artifact_ids": [a["artifact_id"]], "start": "2019-01"})


def test_read_only_comparison_and_activity_report_do_not_write_stores(tmp_path):
    store = PerformanceStore(tmp_path / "performance.sqlite")
    a, _ = attach(store)
    before = {path.name: path.read_bytes() for path in (store.path, store.activity.path)}
    store.catalog()
    first = store.analyze(request(a))
    store.analyze(first["request"])
    store.activity_report({"artifact_ids": [a["artifact_id"]]})
    after = {path.name: path.read_bytes() for path in (store.path, store.activity.path)}
    assert before == after


def test_pinned_download_reproduces_old_policy_while_unpinned_export_detects_change(tmp_path):
    service = FakePlatformService()
    service.performance = PerformanceStore(tmp_path / "performance.sqlite")
    c = client(tmp_path, service)
    artifact, profile = attach(service.performance)
    response = c.post("/api/v2/platform/performance/analyze", json=request(artifact))
    assert response.status_code == 200, response.text
    first = response.json()
    revised_profile(service.performance, artifact, profile)
    params = {"payload": json.dumps(first["request"]), "analysis_id": first["analysis_id"]}
    downloaded = c.get("/api/v2/platform/performance/export", params=params)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.json() == first
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["cache-control"] == "no-store"
    params["payload"] = json.dumps(request(artifact))
    assert c.get("/api/v2/platform/performance/export", params=params).status_code == 409


def test_viewer_policy_and_activity_are_read_only_and_same_origin(tmp_path):
    from market_intel import identity
    service = FakePlatformService()
    service.performance = PerformanceStore(tmp_path / "performance.sqlite")
    c = client(tmp_path, service, remote=True)
    artifact, _ = attach(service.performance)
    _, token = identity.create_account("measurement-viewer", "Viewer", "viewer")
    headers = {"Authorization": f"Bearer {token}"}
    before = {p.name: p.read_bytes() for p in (service.performance.path, service.performance.activity.path)}
    assert c.post("/api/v2/platform/performance/analyze", json=request(artifact), headers=headers).status_code == 200
    assert c.post("/api/v2/platform/performance/activity", json={"artifact_ids": [artifact["artifact_id"]]}, headers=headers).status_code == 200
    assert c.post("/api/v2/platform/performance/activity", json={"artifact_ids": [artifact["artifact_id"]]},
                  headers={**headers, "Origin": "https://unrelated.invalid"}).status_code == 403
    assert c.post("/api/v2/platform/performance/analyze", json=request(artifact),
                  headers={**headers, "Origin": "https://unrelated.invalid"}).status_code == 403
    assert before == {p.name: p.read_bytes() for p in (service.performance.path, service.performance.activity.path)}


def test_policy_rejects_manual_start_override_and_unknown_method(tmp_path):
    store = PerformanceStore(tmp_path / "performance.sqlite")
    artifact, _ = attach(store)
    with pytest.raises(PlatformInputError, match="evaluation start is determined"):
        store.analyze(request(artifact, start="2023-02"))
    with pytest.raises(PlatformInputError, match="method must be"):
        store.analyze({"method": "best_sharpe"})
