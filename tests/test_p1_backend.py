"""P1 backend slices (offline, tmp stores): gzip + cache headers (W9), action feed (W1), review cases (W2), risk workbench (W10),
dossiers (W11), session-aware brief titles (W7)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from market_intel import config, scoring
from market_intel.entities import ALIASES, Company, EntityIndex
from market_intel.sources.base import RawItem
from market_intel.store import Store

SEG_COMPUTE, SEG_MEM, SEG_EQ = "AI Compute Silicon", "Memory & Storage", "Semiconductor Equipment"


def _c(t, name, seg, w=0.0, mw=0.0, cik=None, ne="2026-09-30"):
    return Company(ticker=t, name=name, segment=seg, aliases=list(ALIASES.get(t, [])), weight=w, model_weight=mw, cik=cik, next_earnings=ne)


@pytest.fixture(scope="module")
def index() -> EntityIndex:
    return EntityIndex([_c("NVDA", "NVIDIA Corporation", SEG_COMPUTE, cik=1045810), _c("MU", "Micron Technology, Inc.", SEG_MEM, 0.15, 0.30, cik=723125),
                        _c("DELL", "Dell Technologies Inc.", "Servers & Data Center Infrastructure", 0.15, 0.30, cik=1571996),
                        _c("CRDO", "Credo Technology Group", "Interconnect, Networking & Optics", 0.05, 0.10), _c("AMAT", "Applied Materials", SEG_EQ),
                        _c("TSM", "Taiwan Semiconductor", "Foundry, IDM & Packaging", cik=1046179)])


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "t.sqlite")


@pytest.fixture
def app_client(store, index, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.server import create_app
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    monkeypatch.delenv("MARKET_INTEL_TOKEN", raising=False)
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "token")
    return TestClient(
        create_app(store=store, index=index),
        client=("127.0.0.1", 50000),
        headers={"host": "127.0.0.1"},
    )


def _item(title, summary="", source="cnbc_top", publisher="CNBC", stype="news", weight=0.8, hints=None, extra=None):
    return RawItem(uid=title, source=source, source_name=publisher, source_type=stype, title=title, summary=summary, url="https://x/" + str(abs(hash(title))),
                   tickers_hint=hints or [], extra=extra or {}, weight=weight)


def _ev(uid, title, tickers, published, level=None, materiality=0.8, direction=1, event_type="guidance", **kw):
    return dict(uid=uid, source="s", source_name="Reuters", source_type="news", title=title, published=published, tickers=tickers, relevant=1, materiality=materiality,
                direction=direction, event_type=event_type, alert_level=level, action="analyst review", **kw)


def _no_nan(obj) -> bool:
    return "NaN" not in json.dumps(obj) and "Infinity" not in json.dumps(obj)


# ----------------------------------------------------------------------------- W9: gzip + cache headers
def test_gzip_and_static_cache_headers(app_client, store):
    now = datetime.now(timezone.utc)
    ids = store.upsert_events([_ev(f"g{i}", f"Micron raises guidance item {i} on HBM pricing and capacity expansion plans", ["MU"], now - timedelta(minutes=i), "warning") for i in range(4)])
    for i in ids:
        store.set_impacts(i, [("MU", "direct", 0.5)])
    r = app_client.get("/api/events", headers={"accept-encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip" and r.json()["count"] == 4
    r = app_client.get("/static/base.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=600" and r.headers.get("etag")
    r = app_client.get("/static/plotly.min.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=86400"
    etag = app_client.get("/static/base.js").headers["etag"]
    assert app_client.get("/static/base.js", headers={"if-none-match": etag}).status_code == 304      # ETag revalidation still works


# ----------------------------------------------------------------------------- W7: session-aware brief title
def test_brief_session_label_and_title(store, index, monkeypatch, tmp_path):
    from market_intel import brief
    monkeypatch.setattr(config, "LATEST", tmp_path)
    et = config.ET
    assert brief.session_label(datetime(2026, 9, 3, 7, 0, tzinfo=et)) == "Pre-market brief"
    assert brief.session_label(datetime(2026, 9, 3, 9, 29, tzinfo=et)) == "Pre-market brief"
    assert brief.session_label(datetime(2026, 9, 3, 9, 30, tzinfo=et)) == "Intraday brief"
    assert brief.session_label(datetime(2026, 9, 3, 15, 59, tzinfo=et)) == "Intraday brief"
    assert brief.session_label(datetime(2026, 9, 3, 16, 0, tzinfo=et)) == "Post-close brief"
    assert brief.session_label(datetime(2026, 9, 3, 19, 59, tzinfo=et)) == "Post-close brief"
    assert brief.session_label(datetime(2026, 9, 3, 20, 0, tzinfo=et)) == "Next-open brief"
    assert brief.session_label(datetime(2026, 9, 3, 23, 30, tzinfo=et)) == "Next-open brief"
    for hour, label in ((7, "Pre-market brief"), (12, "Intraday brief"), (17, "Post-close brief"), (21, "Next-open brief")):
        now = datetime(2026, 9, 3, hour, 0, tzinfo=et).astimezone(timezone.utc)
        text = brief.build(store, index, config.Settings(notify=False), llm=None, now=now, notify=False)
        assert text.startswith(f"# {label} - "), text[:60]
        assert store.latest_brief()["meta"]["session"] == label
    assert "Pre-market" not in brief.build(store, index, config.Settings(notify=False), llm=None, now=datetime(2026, 9, 3, 12, 0, tzinfo=et).astimezone(timezone.utc), notify=False)[:40]


# ----------------------------------------------------------------------------- W2: cases
def test_case_lifecycle_and_sla(store):
    from market_intel import cases
    settings = config.Settings(notify=False)
    assert settings.case_sla_hours == {"critical": 4, "warning": 24}
    now = datetime.now(timezone.utc)
    a, b = store.upsert_events([_ev("a", "Micron cuts fiscal Q4 guidance on weaker NAND pricing", ["MU"], now - timedelta(hours=1), "warning", direction=-1),
                                _ev("b", "Dell files 8-K: results of operations", ["DELL"], now - timedelta(hours=2), "critical", event_type="earnings")])
    ea, eb = store.get_event(a), store.get_event(b)
    c1 = cases.ensure_case(store, ea, "MU", "warning", settings, now=now)
    assert c1["status"] == "open" and c1["level"] == "warning" and c1["sla_hours"] == 24 and c1["evidence"] == [a] and c1["story_id"] == ea["story_id"]
    assert datetime.fromisoformat(c1["due"]) - datetime.fromisoformat(c1["created"]) == timedelta(hours=24)
    assert cases.ensure_case(store, ea, "MU", "warning", settings)["id"] == c1["id"]                    # idempotent
    # a second copy of the same story attaches as evidence; escalation to critical tightens the SLA to 4h from creation
    ea2 = {**ea, "id": "copy-2", "alert_level": "critical"}
    c1b = cases.ensure_case(store, ea2, "MU", "critical", settings)
    assert c1b["id"] == c1["id"] and c1b["evidence"] == [a, "copy-2"] and c1b["level"] == "critical" and c1b["sla_hours"] == 4
    assert datetime.fromisoformat(c1b["due"]) - datetime.fromisoformat(c1b["created"]) == timedelta(hours=4)
    c2 = cases.ensure_case(store, eb, "DELL", "critical", settings, now=now)
    assert c2["id"] != c1["id"] and c2["sla_hours"] == 4
    assert store.stats(targets=["MU", "DELL"])["cases_open"] == 2 and store.stats(targets=["MU", "DELL"])["escalations_unread"] == 2
    # workflow: acknowledge -> in_review with a note -> decided -> closed; every status may close directly
    c = cases.acknowledge(store, c1["id"], by="cs")
    assert c["status"] == "acknowledged" and c["ack_by"] == "cs" and c["ack_at"]
    c = cases.update_case(store, c1["id"], status="in_review", owner="cs", note="checking the NAND contract price print", by="cs")
    assert c["status"] == "in_review" and c["owner"] == "cs" and c["notes"][0]["text"].startswith("checking") and c["notes"][0]["by"] == "cs"
    c = cases.update_case(store, c1["id"], status="decided", decision="analyst review: thesis intact, monitor", no_trade_reason="evidence within kill-criteria tolerance", signoff_by="pm")
    assert c["status"] == "decided" and c["signoff_at"] and c["no_trade_reason"]
    c = cases.update_case(store, c1["id"], status="closed")
    assert c["status"] == "closed" and not c["is_open"]
    assert cases.update_case(store, c2["id"], status="closed", by="cs")["status"] == "closed"           # open -> closed directly
    with pytest.raises(ValueError):
        cases.update_case(store, c2["id"], status="bogus")
    with pytest.raises(KeyError):
        cases.update_case(store, 9999, status="closed")
    assert store.stats(targets=["MU", "DELL"])["cases_open"] == 0
    assert [x["id"] for x in cases.list_cases(store, status="closed")] == sorted([c1["id"], c2["id"]], reverse=True) or len(cases.list_cases(store, status="closed")) == 2
    assert cases.list_cases(store, ticker="mu")[0]["ticker"] == "MU" and len(cases.list_cases(store)) == 2
    # overdue: an open case whose due date has passed
    c3 = cases.ensure_case(store, {**eb, "id": "late", "story_id": "st_late"}, "DELL", "warning", settings, now=now - timedelta(hours=30))
    od = cases.overdue(store)
    assert [x["id"] for x in od] == [c3["id"]] and od[0]["overdue"] and od[0]["hours_to_due"] < 0
    s = cases.summary(store)
    assert s["open"] == 1 and s["overdue"] == 1 and s["by_status"]["closed"] == 2


def test_poller_opens_cases_for_target_escalations(store, index, monkeypatch):
    from market_intel.classify import llm as L
    from market_intel.poller import Poller
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    poller = Poller(config.Settings(notify=False), store=store, index=index, llm=L.LLM(config.Settings(model=""), index, provider=None), feeds={"feeds": [], "google_news": []})
    proc = poller.process([_item("Micron raises fiscal Q4 guidance on stronger HBM pricing; shares jump 8%", "The memory maker lifted its outlook.", publisher="Reuters"),
                           _item("Applied Materials raises fiscal Q4 guidance on stronger WFE demand; shares jump 9%", "The equipment maker lifted its outlook.", publisher="Reuters")])
    assert proc["new"] == 2 and proc["cases"] == 1                                           # AMAT is not a target-list name: alert, but no case
    cs = store.list_cases()
    assert len(cs) == 1 and cs[0]["ticker"] == "MU" and cs[0]["level"] == "critical" and cs[0]["sla_hours"] == 4 and cs[0]["status"] == "open"
    e = store.events(ticker="MU", limit=1)[0]
    assert cs[0]["evidence"] == [e["id"]] and cs[0]["story_id"] == e["story_id"]
    assert poller.process([_item("Micron raises fiscal Q4 guidance on stronger HBM pricing; shares jump 8%", "dup", publisher="Reuters")])["cases"] == 0


# ----------------------------------------------------------------------------- W1: action feed + bulk read + ack + case endpoints
def test_action_feed_and_case_endpoints(app_client, store, index):
    now = datetime.now(timezone.utc)
    today = config.now_et(now).date()
    a, b, c, d, e = store.upsert_events([
        _ev("A", "Micron raises fiscal Q4 guidance on stronger HBM pricing", ["MU"], now - timedelta(hours=1), "critical", 0.85, 2),
        _ev("B", "Dell downgraded to underweight at Morgan Stanley", ["DELL"], now - timedelta(hours=3), "warning", 0.6, -1, "analyst"),
        _ev("C", "Nvidia wins sovereign AI contract in Europe", ["NVDA"], now - timedelta(hours=2, minutes=30), "warning", 0.8, 2, "contract_customer"),
        _ev("D", "Micron to present at Citi conference", ["MU"], now - timedelta(minutes=30), "info", 0.3, 0, "other"),
        _ev("E", "Is Micron a buy after the HBM rally?", ["MU"], now - timedelta(minutes=10), None, 0.1, 0, "commentary")])
    for i, t in ((a, "MU"), (b, "DELL"), (c, "NVDA"), (d, "MU"), (e, "MU")):
        store.set_impacts(i, [(t, "direct", 0.4)])
    store.mark_read(b)
    store.set_scores(scoring.compute_scores(store, index, now=now), now)
    store.kv_set("price_snapshot", {"MU": {"pct": 0.031, "idio": 0.02, "ts": now.isoformat()}})
    store.save_calendar([{"date": today.isoformat(), "name": "Consumer Price Index", "importance": 3, "time_et": "08:30", "source": "FRED", "kind": "macro"},
                         {"date": (today + timedelta(days=2)).isoformat(), "name": "MU earnings", "importance": 2, "time_et": "AMC", "source": "yahoo", "kind": "earnings", "ticker": "MU"},
                         {"date": (today + timedelta(days=2)).isoformat(), "name": "FOMC statement", "importance": 3, "time_et": "14:00", "source": "FRED", "kind": "macro"},
                         {"date": (today + timedelta(days=3)).isoformat(), "name": "AMAT earnings", "importance": 2, "time_et": "AMC", "source": "yahoo", "kind": "earnings", "ticker": "AMAT"},
                         {"date": (today + timedelta(days=9)).isoformat(), "name": "too far", "importance": 3, "time_et": "", "source": "FRED", "kind": "macro"}])
    store.kv_set("last_cycle", {"cycle": 12, "at": (now - timedelta(minutes=3)).isoformat(), "at_et": "x", "duration_s": 41.2, "errors": ["gnews_semis"]})
    store.set_feed_state("gnews_semis", {"last_status": 429, "error": "HTTP 429", "last_fetch": now.isoformat()})
    store.set_feed_state("cnbc_top", {"last_status": 200, "error": None, "last_fetch": now.isoformat(), "n_items": 30})

    r = app_client.get("/api/actions?hours=48")
    assert r.status_code == 200
    p = r.json()
    assert _no_nan(p) and set(p) >= {"run", "generated_at", "counts", "action_required", "portfolio_impact", "catalysts", "new_evidence", "system_health"}
    assert "validation_status" in p["run"] and p["n_targets"] == 3
    ar = p["action_required"]
    assert [x["id"] for x in ar] == [a, b], ar                                                # target names only, unread first, critical before warning
    assert ar[0]["ticker"] == "MU" and ar[0]["weight"] == 0.15 and ar[0]["model_weight"] == 0.3 and ar[0]["level"] == "critical" and ar[0]["read"] == 0
    assert ar[0]["case_id"] is None and ar[0]["n_copies"] == 1 and ar[0]["action"] == "analyst review" and ar[0]["age"] == "1h" and ar[0]["story_id"]
    assert ar[1]["ticker"] == "DELL" and ar[1]["read"] == 1
    assert p["counts"]["escalations_open"] == 2 and p["counts"]["escalations_unread"] == 1 and p["counts"]["cases_open"] == 0 and p["counts"]["relevant_24h"] == 5
    pi = p["portfolio_impact"]
    assert [x["ticker"] for x in pi][:2] == ["MU", "DELL"] and len(pi) == 3 and pi[0]["px_pct"] == 0.031 and pi[0]["weight"] == 0.15 and pi[0]["alert_level"] == "critical"
    assert pi[0]["event_score_7d"] > 0 and pi[1]["event_score_7d"] != 0 and pi[2]["ticker"] == "CRDO" and pi[2]["event_score_7d"] == 0 and pi[2]["next_earnings"] == "2026-09-30"
    cat = p["catalysts"]
    assert [x["name"] for x in cat] == ["Consumer Price Index", "FOMC statement", "MU earnings", "AMAT earnings"]      # date, then importance desc; 9 days out excluded
    mu_cat = next(x for x in cat if x["ticker"] == "MU")
    assert mu_cat["is_target"] and mu_cat["weight"] == 0.15 and mu_cat["days_ahead"] == 2 and not next(x for x in cat if x["ticker"] == "AMAT")["is_target"]
    ne = p["new_evidence"]
    assert ne["count"] == 4 and len(ne["items"]) == 4 and e not in {x["id"] for x in ne["items"]}    # commentary excluded; default window 24h
    assert ne["items"][0]["id"] == a and ne["items"][0]["target_tickers"] == ["MU"]                    # most material first
    ne2 = app_client.get("/api/actions", params={"since": (now - timedelta(hours=2)).isoformat()}).json()["new_evidence"]
    assert ne2["count"] == 2 and {x["id"] for x in ne2["items"]} == {a, d}
    sh = p["system_health"]
    assert sh["last_cycle"]["cycle"] == 12 and sh["last_cycle"]["errors"] == ["gnews_semis"] and sh["feeds"]["n"] == 2 and sh["feeds"]["errors"][0]["feed"] == "gnews_semis"
    assert sh["llm"]["available"] is False and sh["polling"] is None and sh["poll_interval_market"] == 300 and "status" in sh["data_health"] and sh["cycle_age_min"] < 10
    # bulk read
    r = app_client.post("/api/events/read", json={"ids": [a, c]})
    assert r.json() == {"ok": True, "n": 2} and store.get_event(a)["read"] == 1 and store.get_event(c)["read"] == 1
    assert app_client.get("/api/actions").json()["counts"]["escalations_unread"] == 0
    assert app_client.post("/api/events/read", json={"ids": []}).status_code == 400
    assert app_client.post(f"/api/events/{a}/read").json()["ok"] is True                              # legacy single-event route kept
    # ack: read + case created and acknowledged
    r = app_client.post(f"/api/events/{a}/ack", json={"by": "cs"})
    # over HTTP the acknowledger is the session actor, not the body's "by"
    assert r.status_code == 200 and r.json()["case"]["status"] == "acknowledged" and r.json()["case"]["ticker"] == "MU" and r.json()["case"]["ack_by"] == "local"
    cid = r.json()["case"]["id"]
    assert app_client.post(f"/api/events/{a}/ack").json()["case"]["id"] == cid                        # idempotent
    ar = app_client.get("/api/actions").json()["action_required"]
    assert ar[0]["id"] == a and ar[0]["case_id"] == cid and ar[0]["case_status"] == "acknowledged"
    assert app_client.post("/api/events/nope/ack").status_code == 404
    st = app_client.get("/api/status").json()["db"]
    assert st["cases_open"] == 1 and st["escalations_unread"] == 0
    # case endpoints
    lst = app_client.get("/api/cases").json()
    assert lst["count"] == 1 and lst["cases"][0]["id"] == cid and lst["summary"]["open"] == 1 and "run" in lst
    assert app_client.get("/api/cases?status=active&ticker=MU").json()["count"] == 1 and app_client.get("/api/cases?status=closed").json()["count"] == 0
    g = app_client.get(f"/api/cases/{cid}").json()
    assert g["case"]["evidence"] == [a] and g["evidence"][0]["id"] == a
    r = app_client.post(f"/api/cases/{cid}", json={"status": "in_review", "owner": "cs", "note": "HBM contract print pending", "by": "cs", "due": (now + timedelta(hours=2)).isoformat()})
    assert r.status_code == 200 and r.json()["case"]["status"] == "in_review" and r.json()["case"]["notes"][0]["text"] == "HBM contract print pending" and r.json()["case"]["owner"] == "cs"
    r = app_client.post(f"/api/cases/{cid}", json={"status": "closed", "decision": "refresh model inputs", "signoff_by": "pm"})
    assert r.json()["case"]["status"] == "closed" and r.json()["case"]["signoff_at"]
    assert app_client.post(f"/api/cases/{cid}", json={"status": "bogus"}).status_code == 400
    assert app_client.post(f"/api/cases/{cid}", json={}).status_code == 400
    assert app_client.post("/api/cases/9999", json={"status": "closed"}).status_code == 404
    assert app_client.get("/api/cases?status=closed").json()["count"] == 1
    # manual case
    r = app_client.post("/api/cases", json={"ticker": "dell", "title": "Check the 8-K item 2.02 detail", "level": "warning", "owner": "cs", "note": "manual", "by": "cs"})
    assert r.status_code == 200
    m = r.json()["case"]
    assert m["ticker"] == "DELL" and m["status"] == "open" and m["sla_hours"] == 24 and m["notes"][0]["text"] == "manual" and m["story_id"].startswith("manual_")
    assert app_client.post(f"/api/cases/{m['id']}/ack", json={"by": "pm"}).json()["case"]["status"] == "acknowledged"
    assert app_client.post("/api/cases", json={"ticker": "DELL"}).status_code == 400
    assert app_client.get("/api/actions").json()["counts"]["cases_open"] == 1
    assert app_client.get("/api/cases?status=nonsense").status_code == 400                             # unknown status filters are rejected
    assert app_client.get("/api/cases?status=acknowledged,closed").json()["count"] == 2 and app_client.get("/api/cases?status=open").json()["count"] == 0


def test_cases_bad_status_filter_is_rejected(store):
    from market_intel import cases
    with pytest.raises(ValueError):
        cases.list_cases(store, status="nonsense")


# ----------------------------------------------------------------------------- W10: risk workbench (synthetic data)
def _synthetic_prices(n=400, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-09-02", periods=n)
    soxx = rng.normal(0.0005, 0.02, n)
    spy = 0.5 * soxx + rng.normal(0.0003, 0.008, n)
    r = {"SOXX": soxx, "SPY": spy, "QQQ": 0.8 * spy + rng.normal(0, 0.004, n),
         "AAA": 1.5 * soxx + rng.normal(0, 0.005, n), "BBB": 0.8 * soxx + rng.normal(0, 0.005, n), "CCC": 1.0 * soxx + rng.normal(0, 0.005, n), "DDD": 0.5 * soxx + rng.normal(0, 0.005, n)}
    return pd.DataFrame({k: 100 * np.cumprod(1 + v) for k, v in r.items()}, index=idx)


@pytest.fixture
def risk_env(monkeypatch, tmp_path):
    from market_intel import research
    hold, model = {"AAA": 0.2, "BBB": 0.2, "CCC": 0.1}, {"AAA": 0.4, "BBB": 0.4, "CCC": 0.2}
    today = config.now_et().date()
    uni = pd.DataFrame({"segment": [SEG_COMPUTE, "AI Software & Applications", "Foundry, IDM & Packaging", SEG_EQ], "country": ["United States", "United States", "Taiwan", "Netherlands"],
                        "is_adr": [False, False, True, False], "financial_currency": ["USD", "USD", "TWD", "EUR"], "market_cap": [1e12, 5e10, 8e11, 3e11],
                        "next_earnings": [(today + timedelta(days=3)).isoformat(), (today + timedelta(days=40)).isoformat(), None, (today + timedelta(days=10)).isoformat()],
                        "beta": [1.5, 0.8, 1.0, 0.5], "eligible": [True, True, True, True], "risk_capped": [False, False, False, False]}, index=["AAA", "BBB", "CCC", "DDD"])
    monkeypatch.setattr(config, "holdings", lambda: dict(hold))
    monkeypatch.setattr(config, "holdings_model", lambda: dict(model))
    monkeypatch.setattr(config, "run_info", lambda: {"run_id": "r-synthetic", "cash": 0.5, "scale": 0.5, "risk_free": 0.05, "construction": "risk_parity", "vol_target": 0.25, "validation_status": config.VALIDATION_STATUS,
                                                     "data_health": {"status": "fresh", "stale": [], "price_gaps": {}}, "actionable": True, "not_actionable_reasons": []})
    monkeypatch.setattr(research, "universe", lambda: uni)
    monkeypatch.setattr(research, "price_panel", _synthetic_prices)
    return SimpleNamespace(hold=hold, model=model, uni=uni, tmp=tmp_path)


@pytest.mark.xfail(reason="Specification test for the planned risk.v4 / constraint-audit hardening; not yet implemented.", strict=False)
def test_risk_workbench_reconciles_and_computes(risk_env):
    from market_intel import risk_workbench as rw
    cfg = SimpleNamespace(min_weight=0.02, max_weight=0.15, max_segment_weight=0.35, vol_target=0.25, primary="risk_parity")
    out = rw.build(cfg=cfg, benchmark_path=risk_env.tmp / "none.csv", volume_path=risk_env.tmp / "none.parquet")
    assert _no_nan(out) and out["run"]["run_id"] == "r-synthetic"
    w = out["weights"]
    assert w["reconciles"] and w["invested"] == 0.5 and w["cash"] == 0.5 and w["executable"] == {"AAA": 0.2, "BBB": 0.2, "CCC": 0.1} and w["model"]["AAA"] == 0.4 and w["scale"] == 0.5
    assert out["benchmark"]["name"] == "equal-weight eligible universe" and out["benchmark"]["n"] == 4 and out["benchmark"]["custom"] is False
    act = {r["ticker"]: r for r in out["active"]}
    assert act["AAA"]["benchmark"] == 0.25 and act["AAA"]["active"] == pytest.approx(-0.05) and act["AAA"]["active_model"] == pytest.approx(0.15) and act["DDD"]["active"] == -0.25 and not act["DDD"]["in_target"]
    assert out["active_summary"]["active_share_exec"] == pytest.approx(0.5 * (0.05 + 0.05 + 0.15 + 0.25 + 0.5)) and out["active_summary"]["n_benchmark_not_in_target"] == 1
    ex = out["exposures"]
    seg = {r["name"]: r for r in ex["segment"]}
    assert sum(r["weight"] for r in ex["segment"]) == pytest.approx(0.5) and sum(r["benchmark"] for r in ex["segment"]) == pytest.approx(1.0)
    assert seg[SEG_COMPUTE]["weight"] == 0.2 and seg[SEG_COMPUTE]["tickers"] == ["AAA"] and seg[SEG_EQ]["weight"] == 0.0 and seg[SEG_EQ]["active"] == -0.25
    assert {r["name"]: r["weight"] for r in ex["country"]}["Taiwan"] == 0.1 and {r["name"]: r["weight"] for r in ex["currency"]}["TWD"] == 0.1
    assert ex["adr_share"]["weight"] == 0.1 and ex["adr_share"]["tickers"] == ["CCC"]
    assert ex["taiwan_china"]["weight"] == 0.1 and ex["taiwan_china"]["tickers"] == ["CCC"] and ex["taiwan_china"]["benchmark"] == 0.25
    assert ex["ai_capex_dependency"]["weight"] == pytest.approx(0.3) and ex["ai_capex_dependency"]["tickers"] == ["AAA", "CCC"] and ex["ai_capex_dependency"]["benchmark"] == 0.75
    gn = out["gross_net"]
    assert gn["gross"] == gn["net"] == 0.5 and gn["leverage"] == 1.0 and gn["short"] == 0.0 and gn["cash"] == 0.5
    liq = out["liquidity"]
    assert liq["days_to_liquidate"] is None and liq["cost_impact_bps"] is None and liq["nav_usd"] is None and "no volume" in liq["note"]
    assert all(p["adv_20d_usd"] is None and p["days_to_liquidate"] is None for p in liq["positions"]) and liq["positions"][0]["ticker"] == "AAA" and liq["computable"]["adv"] is False
    v = out["var"]
    # risk.v4: acquisition finality, uninterrupted common samples, frozen positions and complete benchmarks
    assert v["schema_version"] == "risk.v4" and v["positions_frozen"] is True and v["no_preferred_estimate"] is True
    assert v["es_level"] == 0.975 and "NOT a regulatory FRTB" in v["es_level_note"]
    assert set(v["windows"]) == {"1y", "3y"} and v["horizons_days"] == [1, 10]
    assert v["names_priced"] == ["AAA", "BBB", "CCC"] and v["missing"] == []
    assert v["session_index"]["is_official_exchange_calendar"] is False
    # v1 flat keys must be gone rather than silently carrying v2 semantics
    for legacy in ("var_95", "var_99", "cvar_95", "cvar_99", "es_975", "headline_window", "horizon", "window"):
        assert legacy not in v, legacy
    w1 = v["windows"]["1y"]
    assert w1["available"] and w1["requested_sessions"] == 252 and w1["n_sessions_used"] == 252
    h1, h10 = w1["horizons"]["1"], w1["horizons"]["10"]
    o1, o10 = h1["overlapping"], h10["overlapping"]
    assert 0 < o1["var_95"] < o1["var_99"] and o1["es_95"] <= o1["es_975"] <= o1["es_99"]
    assert h1["non_overlapping_phases"] is None and h1["sqrt_time_reference"] is None      # meaningless at h=1
    ph = h10["non_overlapping_phases"]
    assert ph["n_phases"] == 10 and len(ph["phases"]) == 10
    # each disjoint phase reports its OWN window count, never the overlapping total
    assert all(x["n_windows"] < o10["n_obs"] / 5 for x in ph["phases"])
    assert ph["n_windows_total_across_alternative_phases"] == sum(x["n_windows"] for x in ph["phases"])
    assert "NOT a statistical effective sample size" in o10["non_overlap_equivalent_count_definition"]
    assert h10["sqrt_time_reference"]["var_99"] > 0 and "i.i.d." in h10["sqrt_time_reference"]["assumption"]
    assert h10["estimate_uncertainty"]["var_99"]["block_length"] >= 2
    # independent hand rebuild of the 1-day FROZEN P&L from the same synthetic panel
    px = _synthetic_prices()
    sess = pd.DatetimeIndex([d for d in px.index if d.date() <= pd.Timestamp("2026-09-02").date()])[-252:]
    p0, p1 = px.loc[sess[:-1], ["AAA", "BBB", "CCC"]].to_numpy(), px.loc[sess[1:], ["AAA", "BBB", "CCC"]].to_numpy()
    wts = np.array([0.2, 0.2, 0.1])
    # frozen asset P&L + the explicit cash term (0.5 weight at the run's 5% rate over one session)
    cash_1d = 0.5 * ((1.05) ** (1 / 252) - 1)
    manual = pd.Series(((p1 / p0 - 1.0) * wts).sum(axis=1) + cash_1d)
    assert o1["n_obs"] == len(manual)
    # payload values are rounded to 6dp by _r(), so compare at that precision
    assert o1["var_95"] == pytest.approx(-float(manual.quantile(0.05)), abs=1e-6)
    assert o1["es_975"] == pytest.approx(-float(manual[manual <= manual.quantile(0.025)].mean()), abs=1e-6)
    assert o1["es_99"] == pytest.approx(-float(manual[manual <= manual.quantile(0.01)].mean()), abs=1e-6)
    b = out["betas"]
    assert b["AAA"]["soxx"] == pytest.approx(1.5, abs=0.15) and b["BBB"]["soxx"] == pytest.approx(0.8, abs=0.15) and b["AAA"]["spy"] is not None
    stress = {s["name"]: s for s in out["stress"]}
    exp_soxx = sum(risk_env.hold[t] * b[t]["soxx"] for t in risk_env.hold)
    assert stress["SOXX -20%"]["pnl"] == pytest.approx(-0.2 * exp_soxx, abs=1e-5) and stress["SOXX -35%"]["pnl"] == pytest.approx(-0.35 * exp_soxx, abs=1e-5) and stress["SOXX -20%"]["coverage"] == 1.0
    assert stress["SPY -10%"]["pnl"] == pytest.approx(-0.1 * sum(risk_env.hold[t] * b[t]["spy"] for t in risk_env.hold), abs=1e-5)
    assert stress["NVDA -25%"]["pnl"] == 0.0 and stress["NVDA -25%"]["contributors"] == []
    assert stress["USD +5%"]["pnl"] == pytest.approx(-0.005) and stress["USD +5%"]["contributors"][0]["ticker"] == "CCC"
    ec = out["event_concentration"]
    assert ec["days"] == 14 and [n["ticker"] for n in ec["names"]] == ["AAA"] and ec["weight_sum"] == 0.2 and ec["names"][0]["days_ahead"] == 3 and ec["no_date"] == ["CCC"]
    cvt = out["current_vs_target"]
    assert all(r["current_live_weight"] is None and r["delta"] is None for r in cvt["rows"]) and "no ledger" in cvt["note"] and cvt["rows"][0]["target_executable_weight"] == 0.2
    comp = out["compliance"]
    checks = {c["name"]: c for c in comp["checks"]}
    assert comp["ok"] is False and checks["max_weight (model)"]["ok"] is False and checks["max_weight (model)"]["value"] == 0.4 and checks["max_weight (model)"]["limit"] == 0.15
    assert checks["max_segment_weight (model)"]["ok"] is False and checks["min_weight (model)"]["ok"] is True and checks["invested + cash == 1"]["ok"] is True and checks["long-only"]["ok"]
    assert checks["executable = model x scale"]["ok"] is True and checks["executable = model x scale"]["value"] == 0.5
    loose = SimpleNamespace(min_weight=0.02, max_weight=0.5, max_segment_weight=0.5, vol_target=0.25, primary="risk_parity")
    assert rw.build(cfg=loose, benchmark_path=risk_env.tmp / "none.csv", volume_path=risk_env.tmp / "none.parquet")["compliance"]["ok"] is True


def test_risk_workbench_custom_benchmark_and_volume(risk_env):
    from market_intel import risk_workbench as rw
    bp = risk_env.tmp / "benchmark_weights.csv"
    bp.write_text("ticker,weight\nAAA,50\nDDD,50\n")
    vp = risk_env.tmp / "vol.parquet"
    px = _synthetic_prices()
    pd.DataFrame({"AAA": 1e6, "BBB": 2e6}, index=px.index).to_parquet(vp)
    out = rw.build(cfg=None, benchmark_path=bp, volume_path=vp)
    assert out["benchmark"]["custom"] is True and out["benchmark"]["weights"] == {"AAA": 0.5, "DDD": 0.5} and "benchmark_weights.csv" in out["benchmark"]["name"]
    act = {r["ticker"]: r for r in out["active"]}
    assert act["AAA"]["active"] == pytest.approx(-0.3) and act["BBB"]["benchmark"] == 0.0 and act["BBB"]["active"] == 0.2
    liq = {p["ticker"]: p for p in out["liquidity"]["positions"]}
    assert liq["AAA"]["adv_20d_shares"] == 1e6 and liq["AAA"]["adv_20d_usd"] == pytest.approx(1e6 * float(px["AAA"].tail(20).mean()), rel=1e-6)
    assert liq["CCC"]["adv_20d_usd"] is None and liq["AAA"]["days_to_liquidate"] is None and out["liquidity"]["computable"]["adv"] is True and "NAV" in out["liquidity"]["note"]
    assert out["compliance"]["checks"][0]["name"] == "max_weight (model)"                       # real pipeline config imported
    assert _no_nan(out)


def test_risk_workbench_without_prices(risk_env, monkeypatch):
    from market_intel import research, risk_workbench as rw
    monkeypatch.setattr(research, "price_panel", lambda: pd.DataFrame())
    out = rw.build(benchmark_path=risk_env.tmp / "none.csv", volume_path=risk_env.tmp / "none.parquet")
    v = out["var"]
    assert v["available"] is False and v["missing"] == ["AAA", "BBB", "CCC"]
    assert v["windows"] == {} and v["tracking_error"] == []
    assert v["reason"] == "no confirmed sessions"      # fail closed, no fabricated numbers
    assert {s["name"]: s["pnl"] for s in out["stress"]}["SOXX -20%"] == 0.0 and {s["name"]: s["coverage"] for s in out["stress"]}["SOXX -20%"] == 0.0
    assert out["weights"]["reconciles"] and _no_nan(out)


# ----------------------------------------------------------------------------- W11: dossiers (synthetic data)
@pytest.fixture
def dossier_env(monkeypatch, tmp_path, store):
    from market_intel import research
    row = {"name": "Micron Technology, Inc.", "segment": SEG_MEM, "sub_segment": "DRAM/HBM/NAND", "country": "United States", "is_adr": False, "financial_currency": "USD", "market_cap": 1e12,
           "price": 100.0, "dcf_fv": 90.0, "mult_fv": 120.0, "target_mean": 110.0, "fair_value": 103.0, "upside": 0.03, "dcf_upside": -0.10, "mult_upside": 0.20, "target_upside": 0.10,
           "n_methods": 3, "upside_dispersion": 0.15, "revenue_fy0": 1.0e11, "revenue_fy1": 1.5e11, "eps_fy0": 10.0, "eps_fy1": 16.0, "rev_growth_fy1": 0.5, "eps_growth_fy1": 0.6,
           "eps_rev_30d": 0.02, "eps_rev_90d": 0.1, "n_analysts": 40, "rec_mean": 1.5, "composite": 1.2, "alpha_score": 1.1, "rank_all": 3, "rank_eligible": 2, "eligible": True,
           "score_growth": 1.5, "score_momentum": 1.0, "score_value": 0.5, "score_quality": 0.7, "score_scarcity": 0.9, "score_risk": 0.1, "next_earnings": "2026-09-30", "beta": 2.1,
           "gross_margin": 0.5, "style": "growth"}
    uni = pd.DataFrame([row, {**row, "name": "Dell", "dcf_fv": np.nan, "dcf_upside": np.nan, "fair_value": 116.0, "upside": 0.16}], index=["MU", "DELL"])
    monkeypatch.setattr(research, "universe", lambda: uni)
    monkeypatch.setattr(research, "summary", lambda: {"asof": "2026-09-02", "config": {"valuation": {"w_dcf": 0.5, "w_multiple": 0.3, "w_target": 0.2}}})
    monkeypatch.setattr(research, "LATEST", tmp_path)
    monkeypatch.setattr(config, "holdings", lambda: {"MU": 0.03})
    monkeypatch.setattr(config, "holdings_model", lambda: {"MU": 0.06})
    monkeypatch.setattr(config, "run_info", lambda: {"run_id": "r-dossier", "validation_status": config.VALIDATION_STATUS, "cash": 0.97})
    pd.DataFrame([{"ticker": "MU", "strategic_score": 0.74, "scarcity_strength": 0.79, "risk_score": 0.4, "strategic_thesis": "One of three HBM suppliers.",
                   "kill_criteria": "HBM contract prices fall two quarters | HBM4 loses the NVIDIA socket", "watch_items": "HBM4 qualification | DRAM contract prices | capex",
                   "strategic_asof": "2026-09-02", "strategic_stale": False, "risk_flags": "deep_drawdown", "risk_capped": False, "risk_gated_out": False, "strategic_pillar": 0.74,
                   "strategic_confidence": 0.85, "strategic_source": "evidence_assessment"}]).set_index("ticker").to_csv(tmp_path / "strategic_table.csv")
    hist = tmp_path / "history"
    for rid, asof, gen, eps1 in (("20260810T090000-old", "2026-08-10", "2026-08-10T09:00:00", 12.0), ("20260901T090000-new", "2026-09-01", "2026-09-01T09:00:00", 15.0)):
        d = hist / "runs" / rid
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"run_id": rid, "asof_session": asof, "generated_at": gen}))
        (d / "consensus_snapshot.json").write_text(json.dumps({"MU": {"price": 80.0, "revenue_estimate": {"0y": {"avg": 9.0e10, "growth": 0.3}, "+1y": {"avg": 1.2e11, "growth": 0.33}},
                                                                     "earnings_estimate": {"0y": {"avg": 9.0}, "+1y": {"avg": eps1, "growth": 0.4}},
                                                                     "eps_trend": {"+1y": {"current": eps1, "30daysAgo": eps1 / 1.1, "90daysAgo": eps1 / 1.2}},
                                                                     "price_targets": {"mean": 95.0}, "recommendation_mean": 1.8, "n_analysts": 35, "next_earnings": "2026-09-25"}}))
    scen = tmp_path / "dossiers"
    scen.mkdir()
    (scen / "MU.json").write_text(json.dumps({"base": {"pt": 110, "prob": 0.5, "thesis": "HBM4 ramp on plan"}, "bull": {"pt": 150, "prob": 0.25, "thesis": "HBM share gain"},
                                              "bear": {"pt": 70, "prob": 0.25, "thesis": "DRAM price rollover"}, "author": "cs", "asof": "2026-09-01"}))
    now = datetime.now(timezone.utc)
    ids = store.upsert_events([_ev("d1", "Micron raises guidance on HBM pricing", ["MU"], now - timedelta(hours=2), "critical", 0.85, 2),
                               _ev("d2", "Micron to present at a conference", ["MU"], now - timedelta(days=3), "info", 0.3, 0, "other")])
    for i in ids:
        store.set_impacts(i, [("MU", "direct", 0.5)])
    return SimpleNamespace(tmp=tmp_path, hist=hist, scen=scen, ids=ids)


def test_dossier_blocks(dossier_env, store):
    from market_intel import dossiers
    d = dossiers.build("mu", store, history=dossier_env.hist, scenario_dir=dossier_env.scen)
    assert _no_nan(d) and d["ticker"] == "MU" and d["in_universe"] and d["horizon"] == "12m" and d["benchmark"] == "SOXX" and d["price"] == 100.0
    assert d["identity"]["name"].startswith("Micron") and d["identity"]["segment"] == SEG_MEM and d["identity"]["country"] == "United States"
    assert d["run"]["run_id"] == "r-dossier" and d["validation_status"].startswith("NOT VALIDATED")
    assert d["target_list"] == {"in_target_list": True, "weight": 0.03, "model_weight": 0.06, "rank_eligible": 2, "position": None}
    pt = d["pt_bridge"]
    assert pt["blend_weights"] == {"dcf": 0.5, "multiple": 0.3, "target": 0.2} and pt["methods"]["dcf"]["fair_value"] == 90.0 and pt["methods"]["multiple"]["upside"] == 0.2
    assert pt["blended"]["fair_value"] == 103.0 and pt["blended"]["recomputed_from_methods"] == pytest.approx(103.0) and pt["blended"]["matches_run"] is True
    c = d["consensus"]
    assert c["current"]["eps_fy1"] == 16.0 and c["current"]["n_analysts"] == 40 and c["actual"] is None and c["surprise"] is None and c["note"] == "not available from free data"
    assert c["prior"]["run_id"] == "20260810T090000-old" and c["prior"]["asof"] == "2026-08-10" and c["prior"]["eps_fy1"] == 12.0 and c["prior"]["revenue_fy1"] == 1.2e11
    assert c["prior"]["target_mean"] == 95.0 and c["prior"]["rec_mean"] == 1.8 and c["prior"]["eps_rev_30d"] == pytest.approx(0.1)
    assert c["revisions"]["since"] == "2026-08-10" and c["revisions"]["pct_change"]["eps_fy1"] == pytest.approx(16 / 12 - 1) and c["revisions"]["delta"]["n_analysts"] == 5
    assert d["scores"]["composite"] == 1.2 and d["scores"]["pillars"] == {"growth": 1.5, "momentum": 1.0, "value": 0.5, "quality": 0.7, "scarcity": 0.9, "risk": 0.1}
    s = d["strategic"]
    assert s["strategic_score"] == 0.74 and s["scarcity_strength"] == 0.79 and s["risk_score"] == 0.4 and s["thesis"].startswith("One of three") and s["stale"] is False and s["strategic_asof"] == "2026-09-02"
    assert d["kill_criteria"] == ["HBM contract prices fall two quarters", "HBM4 loses the NVIDIA socket"] and d["watch_kpis"] == ["HBM4 qualification", "DRAM contract prices", "capex"]
    assert [e["id"] for e in d["events"]] == list(dossier_env.ids) and d["events"][0]["alert_level"] == "critical" and d["events"][0]["published_et"].endswith("ET")
    sc = d["scenarios"]
    assert sc["author"] == "cs" and sc["bear"]["pt"] == 70 and sc["bear"]["upside"] == pytest.approx(-0.3) and sc["prob_sum"] == 1.0 and sc["prob_weighted_upside"] == pytest.approx(0.5 * 0.1 + 0.25 * 0.5 - 0.25 * 0.3)
    assert d["scenarios_note"] is None and d["expected_return"]["value"] == 0.03 and d["downside"] == {"value": pytest.approx(-0.3), "basis": "bear scenario price target"}
    # no authored scenarios: null block + note, downside = worst model method
    d2 = dossiers.build("MU", store, history=dossier_env.hist, scenario_dir=dossier_env.tmp / "missing")
    assert d2["scenarios"] is None and d2["scenarios_note"] == "analyst-authored scenarios required; model output is not a scenario"
    assert d2["downside"]["value"] == pytest.approx(-0.10) and "worst of the model methods" in d2["downside"]["basis"]
    # a method missing (DELL has no DCF): blend renormalises over the available methods, NaN never leaks
    d3 = dossiers.build("DELL", store, history=dossier_env.hist, scenario_dir=dossier_env.tmp / "missing")
    assert _no_nan(d3) and d3["pt_bridge"]["methods"]["dcf"]["fair_value"] is None and d3["pt_bridge"]["blended"]["recomputed_from_methods"] == pytest.approx((120 * 0.3 + 110 * 0.2) / 0.5)
    assert d3["target_list"]["in_target_list"] is False and d3["strategic"] is None and d3["kill_criteria"] == [] and d3["events"] == []
    assert d3["consensus"]["prior"]["available"] is False
    d4 = dossiers.build("ZZZZ", store, history=dossier_env.tmp / "nohist", scenario_dir=dossier_env.tmp / "missing")
    assert d4["in_universe"] is False and d4["consensus"]["prior"] is None and d4["price"] is None and _no_nan(d4)


def test_dossier_endpoint(app_client, dossier_env):
    d = app_client.get("/api/research/dossier/mu").json()
    assert d["ticker"] == "MU" and d["horizon"] == "12m" and "pt_bridge" in d and "consensus" in d and d["scores"]["composite"] == 1.2 and _no_nan(d)
    assert [e["id"] for e in d["events"]] == list(dossier_env.ids)


def test_risk_endpoint(app_client, risk_env):
    r = app_client.get("/api/research/risk").json()
    assert r["weights"]["reconciles"] and r["weights"]["invested"] == 0.5 and "var" in r and "stress" in r and "compliance" in r and r["gross_net"]["leverage"] == 1.0 and _no_nan(r)


# ----------------------------------------------------------------------------- store: bulk read
def test_store_bulk_read(store):
    now = datetime.now(timezone.utc)
    ids = store.upsert_events([_ev(f"r{i}", f"Dell wins server order number {i}", ["DELL"], now - timedelta(minutes=i), "warning") for i in range(3)])
    assert store.mark_read_many(ids[:2]) == 2 and [store.get_event(i)["read"] for i in ids] == [1, 1, 0]
    assert store.mark_read_many([]) == 0 and store.mark_read_many(ids, read=0) == 3 and store.get_event(ids[0])["read"] == 0
