import copy
import sqlite3

import pytest

from market_intel.platform.activity import ActivityStore, summarize
from market_intel.platform.service import PlatformBlocked, PlatformInputError


def fixture():
    sources = [{"name": "frozen_ledger.csv", "sha256": "a" * 64}]
    artifact = {"artifact_id": "perf_test", "strategy_id": "strategy_a", "name": "Strategy A", "variant": "v1",
                "sources": sources, "limitations": ["Source research is retrospective"],
                "rows": [{"month": "2020-01", "return": -.01}]}
    rows = []
    for i, (opening, closing, gross, pnl) in enumerate([(100, 110, .5, 10), (110, 110, .5, 0), (110, 99, 0, -11)]):
        rows.append({"date": f"2020-01-{i+1:02}", "return": closing / opening - 1,
                     "active": True, "nav_open": opening, "nav_close": closing,
                     "net_pnl": pnl, "gross_open": 0 if i == 0 else .5, "gross_close": gross,
                     "margin_open": None, "margin_close": None, "event_id": None})
    profile = {"return_artifact_id": "perf_test", "strategy_id": "strategy_a", "frequency": "daily", "sources": sources,
               "capital_basis": "Source currency NAV", "margin_basis": "unavailable", "exposure_basis": "Daily post-fill holdings / closing NAV",
               "limitations": ["No executable fills"], "rows": rows}
    return artifact, profile


def test_store_identity_persists_and_summary_has_real_zero_pnl_activity(tmp_path):
    artifact, profile = fixture()
    store = ActivityStore(tmp_path / "activity.sqlite")
    assert store.latest("missing") is None
    one = store.put(profile, artifact)
    assert one == store.put(profile, artifact)
    reopened = ActivityStore(store.path)
    assert reopened.get(one["profile_id"]) == one == reopened.latest(artifact["artifact_id"])
    assert "created_at" not in one and one["validation_status"] == "NOT VALIDATED"
    s = summarize(one, artifact)
    assert s["observations"] == s["active_observations"] == 3
    assert s["active_return_mean"] == pytest.approx(0)
    assert s["active_return_std"] == pytest.approx(.1)
    assert s["active_return_worst"] == pytest.approx(-.1)
    assert s["calendar_total_return"] == pytest.approx(-.01)
    assert s["calendar_cagr"] is None
    assert s["margin_reported_mean"] is None
    assert s["gross_reported_mean"] == pytest.approx(1 / 3)
    assert "post-fill" in s["exposure_basis"]


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(return_artifact_id="wrong"),
    lambda p: p.update(strategy_id="wrong"),
    lambda p: p["sources"][0].update(sha256="b" * 64),
    lambda p: p["rows"][1].update(date="2020-01-01"),
    lambda p: p["rows"][0].update(active=1),
    lambda p: p["rows"][0].update(active=False),
    lambda p: p["rows"][0].update(return_=1, **{"return": float("nan")}),
    lambda p: p["rows"][0].update(gross_open=-.1),
    lambda p: p["rows"][0].update(nav_close=120),
    lambda p: p["rows"][1].update(nav_open=100),
    lambda p: p["rows"][2].update(net_pnl=1),
])
def test_invalid_accounting_and_parent_anchors_fail_closed(tmp_path, mutate):
    artifact, profile = fixture()
    profile = copy.deepcopy(profile)
    mutate(profile)
    store = ActivityStore(tmp_path / "activity.sqlite")
    with pytest.raises(PlatformInputError):
        store.put(profile, artifact)
    assert store.latest(artifact["artifact_id"]) is None


def test_daily_missing_month_rejected_while_explicit_event_idle_month_allowed(tmp_path):
    artifact, profile = fixture()
    artifact["rows"] += [{"month": "2020-02", "return": 0}]
    store = ActivityStore(tmp_path / "activity.sqlite")
    with pytest.raises(PlatformInputError, match="missing"):
        store.put(profile, artifact)
    profile["frequency"] = "event"
    for row in profile["rows"]:
        row["event_id"] = row["date"]
    saved = store.put(profile, artifact)
    summary = summarize(saved, artifact, "2020-02", "2020-02")
    assert summary["event_count"] == 0 and summary["observations"] == 0
    assert summary["active_fraction"] is None and summary["active_return_mean"] is None
    assert summary["calendar_total_return"] == 0
    artifact["rows"][1]["return"] = .1
    with pytest.raises(PlatformInputError, match="monthly"):
        store.put(profile, artifact)


def test_corrupted_payload_or_parent_index_cannot_be_read(tmp_path):
    artifact, profile = fixture()
    store = ActivityStore(tmp_path / "activity.sqlite")
    saved = store.put(profile, artifact)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE activity_profiles SET payload=replace(payload,'Source currency NAV','changed')")
    with pytest.raises(PlatformBlocked, match="checksum"):
        store.latest(artifact["artifact_id"])
    other = ActivityStore(tmp_path / "other.sqlite")
    saved = other.put(profile, artifact)
    with sqlite3.connect(other.path) as db:
        db.execute("UPDATE activity_profiles SET artifact_id='other'")
    with pytest.raises(PlatformBlocked, match="parent"):
        other.get(saved["profile_id"])


def test_summary_refuses_silent_window_shortening_and_parent_switch(tmp_path):
    artifact, profile = fixture()
    saved = ActivityStore(tmp_path / "activity.sqlite").put(profile, artifact)
    with pytest.raises(PlatformInputError, match="fully covered"):
        summarize(saved, artifact, "2019-12", "2020-01")
    other = dict(artifact, artifact_id="other")
    with pytest.raises(PlatformInputError, match="parent"):
        summarize(saved, other)
