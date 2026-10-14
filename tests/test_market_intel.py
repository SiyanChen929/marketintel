"""Unit tests for the market_intel package (no network)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from market_intel import config, scoring
from market_intel.alerts import alerts_for_event
from market_intel.classify import rules
from market_intel.entities import ALIASES, Company, EntityIndex
from market_intel.sources import macro, sec
from market_intel.sources.base import RawItem, clean_html, to_utc
from market_intel.store import Store, event_id, title_key

SEG_COMPUTE, SEG_MEM, SEG_EQ, SEG_AN, SEG_SW, SEG_CLOUD = ("AI Compute Silicon", "Memory & Storage", "Semiconductor Equipment",
                                                            "Analog, Power & Mixed-Signal", "AI Software & Applications", "Cloud, Hyperscale & Neocloud")


def _c(t, name, seg, w=0.0, cik=None):
    return Company(ticker=t, name=name, segment=seg, aliases=list(ALIASES.get(t, [])), weight=w, cik=cik, next_earnings="2026-09-30")


@pytest.fixture(scope="module")
def index() -> EntityIndex:
    return EntityIndex([_c("NVDA", "NVIDIA Corporation", SEG_COMPUTE, cik=1045810), _c("MU", "Micron Technology, Inc.", SEG_MEM, 0.15, cik=723125),
                        _c("DELL", "Dell Technologies Inc.", "Servers & Data Center Infrastructure", 0.15, cik=1571996),
                        _c("CRDO", "Credo Technology Group", "Interconnect, Networking & Optics", 0.05), _c("AMAT", "Applied Materials", SEG_EQ),
                        _c("LRCX", "Lam Research", SEG_EQ), _c("ON", "ON Semiconductor", SEG_AN), _c("AI", "C3.ai, Inc.", SEG_SW),
                        _c("SMCI", "Super Micro Computer", "Servers & Data Center Infrastructure"), _c("ARM", "Arm Holdings plc", "EDA & Semiconductor IP"),
                        _c("META", "Meta Platforms, Inc.", SEG_CLOUD), _c("TSM", "Taiwan Semiconductor", "Foundry, IDM & Packaging", cik=1046179)])


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "t.sqlite")


def _item(title, summary="", source="cnbc_top", publisher="CNBC", stype="news", weight=0.8, hints=None, extra=None):
    return RawItem(uid=title, source=source, source_name=publisher, source_type=stype, title=title, summary=summary, url="https://x/" + str(abs(hash(title))),
                   tickers_hint=hints or [], extra=extra or {}, weight=weight)


# ----------------------------------------------------------------------------- entities
def test_alias_and_symbol_matching(index):
    m = index.match("Nvidia beats estimates; Micron (NASDAQ: MU) rises on HBM pricing; $DELL up")
    assert set(m) == {"NVDA", "MU", "DELL"}
    assert index.themes("HBM pricing and CoWoS capacity") == ["hbm_dram", "advanced_packaging"] or set(index.themes("HBM pricing and CoWoS capacity")) == {"hbm_dram", "advanced_packaging"}


def test_risky_symbols_need_context(index):
    assert index.match("ON the other hand, AI is now everywhere and ARM wrestling is fun") == {}
    assert set(index.match("ON Semiconductor cuts guidance; C3.ai plunges; Arm Holdings rallies")) == {"ON", "AI", "ARM"}
    assert "META" not in index.match("a meta-analysis of the results") and "META" in index.match("Meta Platforms lifts capex")


def test_readthrough_uses_theme_sign(index):
    rt = index.readthrough(["export_controls"], {"NVDA": 0.9})
    assert "NVDA" not in rt and rt["AMAT"] == (0.5, -1) and rt["LRCX"][1] == -1


# ----------------------------------------------------------------------------- rules
def test_rules_guidance_raise_on_holding(index):
    c = rules.classify(_item("Micron raises fiscal Q4 guidance on stronger HBM pricing; shares jump 8%", "The memory maker lifted its outlook."), index)
    assert c["event_type"] == "guidance" and c["direction"] == 2 and c["materiality"] > 0.7 and c["relevant"]
    assert list(c["tickers"]) == ["MU"] and c["action"] == "refresh model inputs" and "growth" in c["pillars"]
    imp = rules.impacts(c, index)
    assert imp[0][0] == "MU" and imp[0][1] == "direct" and imp[0][2] > 0.4


def test_rules_commentary_is_downweighted(index):
    c = rules.classify(_item("Is Nvidia Stock a No-Brainer Buy Right Now?", source="yahoo_rss", publisher="Motley Fool", weight=0.6), index)
    assert c["event_type"] == "commentary" and c["materiality"] <= 0.15 and not c["relevant"]


def test_rules_export_controls_readthrough_negative(index):
    c = rules.classify(_item("US to impose new export controls on chipmaking tools to China, sources say", publisher="Reuters"), index)
    assert c["event_type"] == "regulatory_export" and c["direction"] < 0 and not c["tickers"]
    imp = dict((t, i) for t, rel, i in rules.impacts(c, index))
    assert imp["AMAT"] < 0 and imp["LRCX"] < 0 and c["relevant"]


def test_rules_fed_rate_cut_is_positive(index):
    c = rules.classify(_item("Federal Reserve issues FOMC statement", "The Committee decided to lower the target range by 25 basis points", source="fed_press",
                             publisher="Federal Reserve", stype="macro", weight=1.0), index)
    assert c["event_type"] == "fed_policy" and c["direction"] == 1


def test_rules_analyst_downgrade(index):
    c = rules.classify(_item("Morgan Stanley downgrades Super Micro to underweight on margin pressure", publisher="Bloomberg"), index)
    assert c["event_type"] == "analyst" and c["direction"] <= -1 and list(c["tickers"]) == ["SMCI"]


def test_rules_price_move(index):
    c = rules.classify(_item("CRDO -20.1% intraday", source="price_monitor", publisher="Price monitor", stype="price", hints=["CRDO"], weight=1.0, extra={"pct": -0.201}), index)
    assert c["event_type"] == "price_move" and c["direction"] == -2 and c["materiality"] >= 0.9 and list(c["tickers"]) == ["CRDO"]


def test_rules_noise_and_readthrough_gating(index):
    c = rules.classify(_item("YieldMax AMD Option Income Strategy ETF declares $0.4868 dividend", source="gnw", publisher="GlobeNewswire", stype="press", weight=0.85), index)
    assert not c["relevant"] and c["event_type"] == "other" and c["materiality"] < 0.2
    c = rules.classify(_item("FTC files lawsuit alleging Meta deceived advertisers", publisher="Reuters"), index)
    assert c["event_type"] == "litigation_investigation" and list(c["tickers"]) == ["META"]
    c = rules.classify(_item("Why AI demand could keep chip stocks rising", source="yahoo_rss", publisher="Motley Fool", weight=0.6), index)
    assert not c["tickers_rt"] and not c["relevant"]
    c = rules.classify(_item("Russia and Ukraine trade drone strikes overnight", publisher="BBC Business"), index)
    assert c["event_type"] == "geopolitical" and not c["relevant"]
    c = rules.classify(_item("Hyperscalers lift 2027 capex plans again, Morgan Stanley estimates $600 billion of AI spending", "AI infrastructure spend keeps rising", publisher="Reuters"), index)
    assert c["relevant"] and c["tickers_rt"] and all(w <= 0.25 for w, _ in c["tickers_rt"].values())


# ----------------------------------------------------------------------------- sec / macro / base
def test_sec_filing_types():
    assert sec.filing_event_type("8-K", ["2.02", "9.01"]) == ("earnings", 0, 0.9)
    assert sec.filing_event_type("8-K", ["5.02", "9.01"])[0] == "management"
    assert sec.filing_event_type("424B5", [])[0] == "capital_raise"
    assert sec._items_from_summary("<b>Filed:</b> 2026-09-02 <br>Item 1.01: Entry<br>Item 9.01: Exhibits") == ["1.01", "9.01"]


def test_macro_importance_and_helpers():
    assert macro._importance("Consumer Price Index") == (3, "08:30")
    assert macro._importance("Kansas City Fed Policy Rate Skew") is None
    assert to_utc("Wed, 02 Sep 2026 12:47:28 GMT").hour == 12
    assert clean_html("<p>Hello &amp; <b>world</b></p>") == "Hello & world"


# ----------------------------------------------------------------------------- store
def test_store_dedup_search_and_impacts(store):
    now = datetime.now(timezone.utc)
    rows = [dict(uid="a", source="s", source_name="CNBC", source_type="news", title="Micron raises guidance on HBM pricing", published=now, tickers=["MU"], relevant=1, materiality=0.8),
            dict(uid="b", source="s2", source_name="Reuters", source_type="news", title="Micron Raises Guidance on HBM Pricing!", published=now, tickers=["MU"], relevant=1, materiality=0.8),
            dict(uid="c", source="s", source_name="SEC EDGAR", source_type="sec", title="Dell files 8-K: Results of Operations", published=now - timedelta(hours=5), tickers=["DELL"], relevant=1, materiality=0.9)]
    new = store.upsert_events(rows)
    assert len(new) == 2 and store.upsert_events(rows) == []
    store.set_impacts(new[0], [("MU", "direct", 0.6), ("NVDA", "readthrough", 0.1)])
    assert [e["id"] for e in store.events(ticker="MU")] == [new[0]]
    assert store.search("micron guidance")[0]["id"] == new[0]
    assert store.search("hbm")                                    # prefix / OR fallback
    assert store.search("zzzznotthere") == []
    assert len(store.event_tickers_since(now - timedelta(days=1))) == 2
    assert store.stats()["events_total"] == 2
    store.kv_set("k", {"x": 1}); assert store.kv_get("k") == {"x": 1}


def test_scores_overlay_and_pipeline_alerts(store, index, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LATEST", tmp_path)
    monkeypatch.setattr(config, "MI_OUT", tmp_path)
    now = datetime.now(timezone.utc)
    ev = [dict(uid="g", source="s", source_name="CNBC", source_type="news", title="Micron raises guidance", published=now - timedelta(hours=2), tickers=["MU"], relevant=1, materiality=0.85, direction=2, event_type="guidance", alert_level="critical", action="refresh model inputs", pillars=["growth"]),
          dict(uid="h", source="s", source_name="Reuters", source_type="news", title="Dell downgraded", published=now - timedelta(hours=30), tickers=["DELL"], relevant=1, materiality=0.4, direction=-1, event_type="analyst", alert_level="info", action="monitor", pillars=["momentum"])]
    ids = store.upsert_events(ev)
    store.set_impacts(ids[0], [("MU", "direct", 0.7), ("NVDA", "readthrough", 0.15)])
    store.set_impacts(ids[1], [("DELL", "direct", -0.2)])
    sc = scoring.compute_scores(store, index, now=now)
    by = {r["ticker"]: r for r in sc}
    assert by["MU"]["event_score_7d"] > 0.6 and by["MU"]["alert_level"] == "critical" and by["MU"]["action"] == "refresh model inputs"
    assert by["DELL"]["event_score_7d"] < 0 and abs(by["DELL"]["event_score_7d"]) < 0.2      # decayed (30h old, 36h half-life)
    assert by["NVDA"]["n_direct_7d"] == 0 and by["NVDA"]["n_events_7d"] == 1
    assert sc[0]["is_holding"]
    summary = scoring.write_overlay(sc, store, index, now=now)
    assert (tmp_path / "event_overlay.csv").exists() and summary["n_holdings_flagged"] == 1
    ov = scoring.load_overlay(36.0)
    assert ov is not None and ov.loc["MU", "event_score_7d"] > 0.6
    # the selection pipeline hook
    from ai_semi_intel.dashboard import monitoring
    top = pd.DataFrame(index=["MU", "DELL", "NVDA"])
    al = monitoring.event_alerts(top)
    assert any(a["ticker"] == "MU" and a["severity"] == "critical" for a in al)


def test_pipeline_event_tilt(monkeypatch, tmp_path):
    from ai_semi_intel.analysis import scoring as pscore
    from ai_semi_intel.config.settings import ScreenConfig
    rng = np.random.default_rng(0)
    n = 40
    segs = [SEG_MEM, SEG_COMPUTE, SEG_EQ, SEG_SW, SEG_AN, SEG_CLOUD]
    df = pd.DataFrame({f: rng.normal(size=n) for f in ["growth_accel", "rev_growth_q_yoy", "fwd_accel", "eps_rev_90d", "eps_rev_30d", "mom_12_1_volscaled",
                                                       "industry_mom", "rel_6m_vs_soxx", "mult_residual", "gross_margin", "op_margin", "fcf_margin", "roic",
                                                       "net_debt_to_ebitda", "sbc_pct"]}, index=[f"T{i}" for i in range(n)])
    df["segment"] = [segs[i % len(segs)] for i in range(n)]
    df["market_cap"], df["price"], df["ev_sales_ntm"], df["upside"] = 5e9, 100.0, 5.0, 0.2
    base = pscore.score_universe(df, ScreenConfig())
    overlay = pd.DataFrame({"ticker": df.index, "event_score_7d": 0.0, "asof": datetime.now(timezone.utc).isoformat()})
    overlay.loc[overlay["ticker"] == "T0", "event_score_7d"] = 1.0
    overlay.loc[overlay["ticker"] == "T1", "event_score_7d"] = -1.0
    overlay.to_csv(tmp_path / "event_overlay.csv", index=False)
    monkeypatch.setattr(config, "LATEST", tmp_path)
    tilted = pscore.score_universe(df, ScreenConfig(w_event=0.2))
    assert np.isclose(tilted.loc["T0", "composite"], base.loc["T0", "composite"] + 0.2)
    assert np.isclose(tilted.loc["T1", "composite"], base.loc["T1", "composite"] - 0.2)
    assert np.isclose(tilted.loc["T5", "composite"], base.loc["T5", "composite"])


# ----------------------------------------------------------------------------- alerts
def test_alert_levels(index):
    held_sec = {"materiality": 0.9, "direction": 0, "source_type": "sec", "event_type": "earnings", "relevant": 1, "extra": {"items": ["2.02", "9.01"]}}
    assert scoring.alert_level(held_sec, [("MU", "direct", 0.0)], index) == "critical"
    assert scoring.alert_level({"materiality": 0.9, "direction": 2, "source_type": "news", "event_type": "guidance", "relevant": 1}, [("NVDA", "direct", 0.8)], index) == "warning"
    assert scoring.alert_level({"materiality": 0.1, "direction": 0, "source_type": "news", "event_type": "commentary", "relevant": 1}, [("MU", "direct", 0.0)], index) is None
    assert scoring.alert_level({"materiality": 0.5, "direction": -1, "source_type": "news", "event_type": "regulatory_export", "relevant": 1}, [("MU", "readthrough", -0.25)], index) == "info"
    ev = {"id": "x", "title": "Micron raises guidance", "alert_level": "critical", "tickers": ["NVDA", "MU"], "direction": 2, "source_name": "CNBC", "one_liner": ""}
    a = alerts_for_event(ev, index)
    assert a[0]["ticker"] == "MU" and a[0]["level"] == "critical" and "▲▲" in a[0]["message"]


# ----------------------------------------------------------------------------- server
def test_server_endpoints(store, index, monkeypatch):
    from fastapi.testclient import TestClient
    from market_intel.server import create_app
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    now = datetime.now(timezone.utc)
    ids = store.upsert_events([dict(uid="q", source="s", source_name="CNBC", source_type="news", title="Micron raises guidance on HBM", published=now, tickers=["MU"], relevant=1, materiality=0.8, direction=2, alert_level="critical")])
    store.set_impacts(ids[0], [("MU", "direct", 0.6)])
    store.set_scores(scoring.compute_scores(store, index, now=now), now)
    store.save_calendar([{"date": config.now_et().date().isoformat(), "name": "Consumer Price Index", "importance": 3, "time_et": "08:30", "source": "FRED", "kind": "macro"}])
    client = TestClient(
        create_app(store=store, index=index),
        client=("127.0.0.1", 50000),
        headers={"host": "127.0.0.1"},
    )
    assert client.get("/").status_code == 200 and "Market Intel" in client.get("/").text
    st = client.get("/api/status").json()
    assert st["llm"]["available"] is False and "MU" in st["holdings"]
    ev = client.get("/api/events?filter=holdings").json()
    assert ev["count"] == 1 and ev["events"][0]["holding_tickers"] == ["MU"]
    assert client.get("/api/events?filter=alerts").json()["count"] == 1
    assert client.get("/api/events?q=micron").json()["count"] == 1
    board = client.get("/api/board").json()["rows"]
    assert board[0]["ticker"] == "MU" and board[0]["event_score_7d"] > 0
    assert client.get("/api/calendar").json()["rows"][0]["name"] == "Consumer Price Index"
    r = client.post("/api/ask", json={"question": "What happened to MU?"})
    assert r.status_code == 200
    head, body = r.text.split("\n---\n", 1)
    assert json.loads(head)["evidence"][0]["tickers"] == ["MU"] and "No LLM is configured" in body
    assert client.post(f"/api/events/{ids[0]}/read").json()["ok"] is True
    assert client.post("/api/poll").status_code == 400          # no poller attached


# ----------------------------------------------------------------------------- auth + research endpoints
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
    )  # localhost -> auth exempt


def test_auth_local_exempt_and_proxied_requires_login(app_client, tmp_path):
    from market_intel import auth
    assert app_client.get("/api/status").status_code == 200                                   # localhost, no proxy headers
    proxied = {"cf-connecting-ip": "203.0.113.5", "x-forwarded-proto": "https"}
    assert app_client.get("/api/status", headers=proxied).status_code == 401
    r = app_client.get("/portfolio", headers=proxied, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login")
    assert "access token" in app_client.get("/login", headers=proxied).text
    token = auth.get_token()
    assert (tmp_path / "token").exists() and len(token) > 20
    bad = app_client.post("/login", data={"password": "wrong", "next": "/portfolio"}, headers=proxied, follow_redirects=False)
    assert bad.status_code == 302 and "error" in bad.headers["location"]
    ok = app_client.post("/login", data={"password": token, "next": "/portfolio"}, headers=proxied, follow_redirects=False)
    assert ok.status_code == 302 and ok.headers["location"] == "/portfolio" and auth.COOKIE in ok.cookies
    assert app_client.get("/api/status", headers=proxied, cookies={auth.COOKIE: ok.cookies[auth.COOKIE]}).status_code == 200
    assert app_client.get("/api/status", headers={**proxied, "authorization": f"Bearer {token}"}).status_code == 200
    assert app_client.get("/healthz", headers=proxied).status_code == 200


def test_research_pages_and_apis(app_client):
    from market_intel import research
    for path, marker in (("/portfolio", "Optimizer lab"), ("/universe", "Screening funnel"), ("/ticker/MU", "Recent events"), ("/macro", "Snapshot"), ("/research", "Pipeline")):
        r = app_client.get(path)
        assert r.status_code == 200 and marker in r.text, path
    assert app_client.get("/static/base.js").status_code == 200 and app_client.get("/static/plotly.min.js").status_code == 200
    s = app_client.get("/api/research/summary").json()
    if not s.get("available"):
        pytest.skip("pipeline outputs not present")
    p = app_client.get("/api/research/portfolio").json()
    assert p["available"] and len(p["holdings"]) >= 5 and "risk_parity" in p["weights"] and p["backtest"]["dates"]
    assert {"cap_weight", "inverse_vol", "segment_risk_parity", "min_variance", "max_diversification", "hrp",
            "max_sharpe", "mean_variance", "kelly", "resampled_max_sharpe", "min_cvar"} <= set(p["registry"])
    u = app_client.get("/api/research/universe").json()
    assert len(u["rows"]) >= 50 and u["funnel"]
    t = app_client.get("/api/research/ticker/MU").json()
    assert t["in_universe"] and t["prices"]["dates"] and "events" in t
    assert app_client.get("/api/research/macro").json()["series"]
    assert app_client.get("/api/research/validation").status_code == 200
    assert app_client.get("/api/research/strategic").status_code == 200
    assert app_client.get("/api/pipeline/status").json()["running"] is False
    assert app_client.get("/api/tunnel").status_code == 200
    o = app_client.post("/api/research/optimize", json={"tickers": ["MU", "DELL", "NVDA", "AMD", "TSM"], "max_weight": 0.4, "vol_target": 0.3}).json()
    from ai_semi_intel.portfolio import optimizer
    assert set(o["methods"]) == set(optimizer.CONSTRUCTION_METHODS) and abs(sum(o["recommended"]["weights"].values()) + o["recommended"]["cash"] - 1) < 1e-3
    assert all(w <= 0.4 + 1e-6 for w in o["methods"]["max_sharpe"]["weights"].values())
    assert set(o["construction_backtest"]["cumulative"]) >= set(optimizer.CONSTRUCTION_METHODS)
    bad = app_client.post("/api/research/optimize", json={"tickers": ["ZZZZ"]})
    assert bad.status_code == 400


# ----------------------------------------------------------------------------- LLM providers (no network)
def test_llm_provider_detection_and_openai_request_shape(monkeypatch):
    from market_intel.classify import llm as L
    for v in ("MARKET_INTEL_NO_LLM", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "MARKET_INTEL_LLM_PROVIDER", "MARKET_INTEL_MODEL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(config, "_read_key_file", lambda names: None)
    assert config.llm_provider() is None and not config.llm_available()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test")
    assert config.llm_provider() == "openai" and config.llm_available()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert config.llm_provider() == "anthropic"                                   # Anthropic wins when both exist
    monkeypatch.setenv("MARKET_INTEL_LLM_PROVIDER", "openai")
    assert config.llm_provider() == "openai"
    assert L.detect_provider("sk-ant-api03-abc") == "anthropic" and L.detect_provider("sk-proj-abc") == "openai"
    m = L.LLM(config.Settings(model=""), provider="openai")
    assert m.available and m.model is None
    m.model = "gpt-5"
    kw = m._openai_kwargs("sys", "usr", L.BATCH_SCHEMA, "low", 123)
    assert kw["response_format"]["json_schema"]["strict"] is True and kw["reasoning_effort"] == "low" and kw["max_completion_tokens"] == 123
    assert kw["messages"][0] == {"role": "system", "content": "sys"} and "stream" not in kw
    m.model = "gpt-4.1"
    kw2 = m._openai_kwargs("s", "u", None, "high", 10, stream=True)
    assert "reasoning_effort" not in kw2 and kw2["stream"] is True and "response_format" not in kw2
    a = L.LLM(config.Settings(model=""), provider="anthropic")
    assert a.model == "claude-opus-5"


def test_persistent_model_setting(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.delenv("MARKET_INTEL_MODEL", raising=False)
    assert config.Settings().model == ""
    config.save_user_setting("model", "gpt-5.6-luna")
    assert config.Settings().model == "gpt-5.6-luna" and config.user_settings() == {"model": "gpt-5.6-luna"}
    monkeypatch.setenv("MARKET_INTEL_MODEL", "gpt-5.4")
    assert config.Settings().model == "gpt-5.4"                         # env wins
    config.save_user_setting("model", None)
    assert config.user_settings() == {}


# ----------------------------------------------------------------------------- canonical run (S1)
def test_holdings_are_executable_targets_and_run_info(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LATEST", tmp_path)
    snap = {"date": "2026-09-02", "run_id": "20260902T181335-test", "top": ["MU", "DELL"], "weights": {"MU": 0.03, "DELL": 0.04},
            "weights_model": {"MU": 0.06, "DELL": 0.08}, "cash": 0.93, "generated_at": "2026-09-02T18:13:35"}
    (tmp_path / "snapshot.json").write_text(json.dumps(snap))
    assert config.holdings() == {"MU": 0.03, "DELL": 0.04} and config.holdings_model() == {"MU": 0.06, "DELL": 0.08}
    r = config.run_info()
    assert r["basis"] == "legacy_snapshot" and r["run_id"] == "20260902T181335-test" and r["validation_status"].startswith("NOT VALIDATED")
    run = {"run_id": "20260902T181335-test", "asof_session": "2026-09-02", "generated_at": "2026-09-02T18:13:35", "validation_status": "NOT VALIDATED · exploratory",
           "actionable": True, "portfolio": {"cash": 0.93, "invested": 0.07, "construction": "risk_parity", "reconciles": True}}
    (tmp_path / "run.json").write_text(json.dumps(run))
    r = config.run_info()
    assert r["basis"] == "run_manifest" and abs(r["cash"] + r["invested"] - 1) < 1e-9 and r["wording"] == "target"
    from market_intel.entities import build_index
    monkeypatch.setattr(config, "universe_table", lambda: [{"ticker": "MU", "name": "Micron", "segment": "Memory & Storage", "sub_segment": "", "description": "",
                                                            "cik": None, "next_earnings": None, "market_cap": None}])
    monkeypatch.setattr(config, "extra_watchlist", lambda: {})
    ix = build_index()
    assert ix.companies["MU"].weight == 0.03 and ix.companies["MU"].model_weight == 0.06 and ix.holdings == ["MU"]
    desc = ix.describe_universe()
    assert "[TARGET 3.0% exec / 6.0% model]" in desc and "HELD" not in desc


def test_run_id_is_consistent_across_status_and_research_apis(app_client):
    st = app_client.get("/api/status").json()
    assert "run" in st and "validation_status" in st["run"] and st["run"]["validation_status"].startswith("NOT VALIDATED")
    s = app_client.get("/api/research/summary").json()
    if not s.get("available"):
        pytest.skip("pipeline outputs not present")
    p = app_client.get("/api/research/portfolio").json()
    u = app_client.get("/api/research/universe").json()
    t = app_client.get("/api/research/ticker/MU").json()
    m = app_client.get("/api/research/macro").json()
    ids = {st["run"]["run_id"], s["run"]["run_id"], p["run"]["run_id"], u["run"]["run_id"], t["run"]["run_id"], m["run"]["run_id"]}
    assert len(ids) == 1 and None not in ids
    assert p["reconciles"] is True
    inv = sum(h["weight"] for h in p["holdings"])
    assert abs(inv + p["recommended_cash"] - 1) < 1e-6 and all(h["weight"] <= h["weight_model"] + 1e-12 for h in p["holdings"])
    held = {r["ticker"]: r for r in u["rows"] if r["is_holding"]}
    assert set(held) == {h["ticker"] for h in p["holdings"]} and all(abs(held[h["ticker"]]["weight"] - h["weight"]) < 1e-9 for h in p["holdings"])
    if t.get("holding"):
        assert abs(t["holding"]["weight"] - held["MU"]["weight"]) < 1e-9


# ----------------------------------------------------------------------------- evidence state + analyst vocabulary (S3)
def test_brief_wording_filter_and_legacy_action_migration(store):
    from market_intel.brief import sanitise
    txt, n = sanitise("DELL: stay long / consider add on confirmation. NVDA: **Hold**; re-run model. MU: overweight into the print.")
    assert n >= 4 and "stay long" not in txt and "overweight" not in txt and "analyst review" in txt
    assert sanitise("Holdings of the target list are unchanged; monitor.")[1] == 0
    now = datetime.now(timezone.utc)
    ids = store.upsert_events([dict(uid="leg", source="s", source_name="CNBC", source_type="news", title="Micron raises guidance", published=now, tickers=["MU"], relevant=1,
                                    materiality=0.8, direction=2, event_type="guidance", action="re-run model")])
    store._migrate()
    assert store.get_event(ids[0])["action"] == "refresh model inputs"


def test_pages_carry_the_evidence_banner(app_client):
    for path in ("/", "/portfolio", "/universe", "/ticker/MU", "/macro", "/research"):
        html = app_client.get(path).text
        assert 'id="banner"' in html and "statusBanner(" in html, path
    js = app_client.get("/static/base.js").text
    assert "NOT VALIDATED" in js and "hindsight illustration" in js and "no live ledger" in js


# ----------------------------------------------------------------------------- model lineage (S5)
def test_lineage_columns_and_rules_stamp(store, index, monkeypatch):
    from market_intel.classify import llm as L, rules as R
    from market_intel.poller import Poller
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"classifier", "provider", "model", "prompt_version", "schema_version", "rules_version", "input_hash", "output_hash", "classified_at", "human_override", "story_id", "needs_review"} <= cols
    poller = Poller(config.Settings(notify=False), store=store, index=index, llm=L.LLM(config.Settings(model=""), index, provider=None), feeds={"feeds": [], "google_news": []})
    poller.process([_item("Micron raises fiscal Q4 guidance on stronger HBM pricing", "The memory maker lifted its outlook.", publisher="Reuters")])
    e = store.events(limit=1)[0]
    assert e["classifier"] == "rules" and e["rules_version"] == R.RULES_VERSION and len(e["input_hash"]) == 16 and e["classified_at"]
    assert L.PROMPT_VERSION.startswith("classify-v") and L.SCHEMA_VERSION.startswith("schema-")
    # a legacy row (llm=1, no provider) is attributed to the LLM layer by the migration, never to a named vendor
    ids = store.upsert_events([dict(uid="legacy", source="s", source_name="CNBC", source_type="news", title="Dell beats and raises", published=datetime.now(timezone.utc), llm=1, relevant=1)])
    store._migrate()
    e2 = store.get_event(ids[0])
    assert e2["classifier"] == "llm" and e2["provider"] is None


def test_human_override_is_recorded_as_lineage(app_client, store, index):
    now = datetime.now(timezone.utc)
    ids = store.upsert_events([dict(uid="ov", source="s", source_name="DigiTimes", source_type="news", title="TSMC doubles down on equipment purchase", published=now,
                                    tickers=["TSM"], relevant=1, materiality=0.47, direction=-1, event_type="geopolitical", confidence=0.65)])
    store.set_impacts(ids[0], [("TSM", "direct", -0.2)])
    r = app_client.post(f"/api/events/{ids[0]}/override", json={"direction": 2, "event_type": "capacity_capex", "materiality": 0.7, "note": "capex, not geopolitics", "by": "analyst"})
    assert r.status_code == 200 and r.json()["override"]["previous"] == {"direction": -1, "event_type": "geopolitical", "materiality": 0.47}
    e = store.get_event(ids[0])
    # the override records the SESSION actor, not the body's "by": a client cannot attribute its edit to someone else
    assert e["classifier"] == "human" and e["direction"] == 2 and e["event_type"] == "capacity_capex" and e["human_override"]["by"] == "local"
    assert [i for i in store.event_tickers_since(now - timedelta(hours=1)) if i["ticker"] == "TSM"][0]["impact"] > 0
    assert app_client.post(f"/api/events/{ids[0]}/override", json={"note": "no fields"}).status_code == 400


# ----------------------------------------------------------------------------- story clustering (S6)
def _copies(n):
    """Paraphrased copies of one story from different outlets (titles differ, so the exact-title dedupe does not catch them)."""
    outlets = ["Bloomberg.com", "Reuters", "CNBC Technology", "Barron's", "Benzinga", "GuruFocus", "MarketBeat", "Yahoo Finance", "TechCrunch AI", "Techzine Global",
               "marketscreener.com", "citybiz", "Finimize", "24/7 Wall St.", "The Globe and Mail", "Investing.com", "Seeking Alpha", "TradingView", "Stocktwits", "moomoo.com"]
    forms = ["Nvidia Nears ${b} Billion Hugging Face Deal This Week", "Nvidia said to be close to ${b}bn Hugging Face acquisition", "NVIDIA Reportedly in Talks to Acquire Hugging Face for ${b} Billion",
             "Nvidia plans to buy Hugging Face for about ${b} billion, sources say", "Hugging Face deal: Nvidia nears ${b} billion agreement", "Nvidia Nears ${b}B Deal For Hugging Face - NVIDIA (NASDAQ:NVDA)"]
    now = datetime.now(timezone.utc)
    return [dict(uid=f"hf{i}", source=f"src{i}", source_name=outlets[i % len(outlets)], source_type="news", title=forms[i % len(forms)].format(b=[14, 13, 12.9, 14.5, 13.2][i % 5]),
                 published=now - timedelta(hours=1, minutes=i), tickers=["NVDA"], relevant=1, materiality=0.8 if i % 2 else 0.7, direction=1, event_type="m_and_a",
                 confidence=0.8, llm=1) for i in range(n)]


def test_story_clustering_caps_duplicate_copies(store, index):
    now = datetime.now(timezone.utc)
    one = store.upsert_events(_copies(1))
    store.set_impacts(one[0], [("NVDA", "direct", 0.28)])
    unrelated = store.upsert_events([dict(uid="mu", source="x", source_name="Reuters", source_type="news", title="Micron raises fiscal Q4 guidance on HBM pricing",
                                          published=now - timedelta(hours=2), tickers=["MU"], relevant=1, materiality=0.8, direction=2, event_type="guidance")])
    store.set_impacts(unrelated[0], [("MU", "direct", 0.6)])
    s1 = {r["ticker"]: r for r in scoring.compute_scores(store, index, now=now)}
    rest = store.upsert_events(_copies(20)[1:])
    for i, eid in enumerate(rest):
        store.set_impacts(eid, [("NVDA", "direct", 0.28 if i % 2 else 0.25)])
    ids = one + rest
    sids = {store.get_event(i)["story_id"] for i in ids}
    assert len(sids) == 1 and None not in sids, "all 20 copies must share one story"
    assert store.get_event(unrelated[0])["story_id"] not in sids
    st = store.story(next(iter(sids)))
    assert st["n_events"] == 20 and st["n_sources"] == 20 and 1 <= st["n_independent"] <= 8 and st["status"] == "active"
    s20 = {r["ticker"]: r for r in scoring.compute_scores(store, index, now=now)}
    assert s20["NVDA"]["event_score_7d"] <= s1["NVDA"]["event_score_7d"] * 1.05 + 1e-9, (s1["NVDA"]["event_score_7d"], s20["NVDA"]["event_score_7d"])
    assert s20["NVDA"]["n_events_7d"] == 1 and s20["NVDA"]["n_copies_7d"] == 20 and s20["MU"]["event_score_7d"] == s1["MU"]["event_score_7d"]
    # a copy with no new facts from the same family adds exactly zero
    before = s20["NVDA"]["event_score_7d"]
    dup = store.upsert_events([dict(uid="hf-dup", source="dup", source_name="Bloomberg.com", source_type="news", title="Nvidia nears $14 billion Hugging Face takeover, Bloomberg says",
                                    published=now - timedelta(minutes=30), tickers=["NVDA"], relevant=1, materiality=0.8, direction=1, event_type="m_and_a", llm=1)])
    store.set_impacts(dup[0], [("NVDA", "direct", 0.28)])
    after = {r["ticker"]: r for r in scoring.compute_scores(store, index, now=now)}["NVDA"]["event_score_7d"]
    assert abs(after - before) < 1e-9
    # a retraction zeroes the story
    ret = store.upsert_events([dict(uid="hf-ret", source="r", source_name="Bloomberg.com", source_type="news", title="Bloomberg retracts report that Nvidia nears Hugging Face deal",
                                    published=now - timedelta(minutes=10), tickers=["NVDA"], relevant=1, materiality=0.8, direction=0, event_type="m_and_a", llm=1)])
    assert store.get_event(ret[0])["story_id"] in sids and store.story(next(iter(sids)))["status"] == "retracted"
    assert abs({r["ticker"]: r for r in scoring.compute_scores(store, index, now=now)}["NVDA"]["event_score_7d"]) < 1e-9


def test_story_recluster_after_llm_update(store, index):
    now = datetime.now(timezone.utc)
    a = store.upsert_events([dict(uid="a", source="s", source_name="Bloomberg.com", source_type="news", title="Nvidia Nears $14 Billion Hugging Face Deal This Week", published=now,
                                  tickers=["NVDA"], relevant=1, materiality=0.2, direction=0, event_type="other")])
    b = store.upsert_events([dict(uid="b", source="t", source_name="Reuters", source_type="news", title="Chipmaker close to Hugging Face purchase, people familiar say", published=now,
                                  tickers=[], relevant=1, materiality=0.2, direction=0, event_type="other")])
    assert store.get_event(a[0])["story_id"] != store.get_event(b[0])["story_id"]          # no ticker on the second copy yet
    store.update_event(b[0], tickers=["NVDA"], event_type="m_and_a", one_liner="Nvidia is close to acquiring Hugging Face for about $14 billion.", llm=1)
    assert store.get_event(a[0])["story_id"] == store.get_event(b[0])["story_id"]          # re-clustered once the LLM named the ticker
    assert store.story(store.get_event(a[0])["story_id"])["n_events"] == 2


# ----------------------------------------------------------------------------- classification constraints (S7)
TSMC_TITLE = "TSMC doubles down on equipment purchase as AI demand is outpacing semiconductor supply chain planning"
TSMC_BODY = ("TSMC says surging AI demand is stretching the global chip industry beyond its usual planning cycle, with fast-changing procurement needs, "
             "labor shortages, and infrastructure constraints affecting markets far beyond Taiwan. The company is expanding aggressively.")


def test_tsmc_headline_is_not_geopolitical_and_fanout_is_withheld(store, index, monkeypatch):
    from market_intel.classify import llm as L
    from market_intel.poller import Poller
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    c = rules.classify(_item(TSMC_TITLE, TSMC_BODY, source="digitimes", publisher="DigiTimes", weight=0.85), index)
    assert c["event_type"] == "capacity_capex" and c["direction"] >= 0 and list(c["tickers"]) == ["TSM"]
    assert c["readthrough"] and all({"theme", "edge", "kpi", "weight", "sign", "via"} <= set(d) for d in c["readthrough"].values())
    g = rules.classify(_item("TSMC halts shipments as PLA drills blockade the Taiwan Strait", publisher="Reuters"), index)
    assert g["event_type"] == "geopolitical" and g["direction"] < 0                      # a real geopolitical event still classifies
    poller = Poller(config.Settings(notify=False, readthrough_cap=2), store=store, index=index, llm=L.LLM(config.Settings(model=""), index, provider=None), feeds={"feeds": [], "google_news": []})
    poller.process([_item(TSMC_TITLE, TSMC_BODY, source="digitimes", publisher="DigiTimes", weight=0.85)])
    e = store.events(limit=1)[0]
    imps = store.impacts_for([e["id"]])[e["id"]]
    assert e["needs_review"] == 1 and [i["ticker"] for i in imps] == ["TSM"] and imps[0]["impact"] > 0
    edges = store.readthrough_for([e["id"]])[e["id"]]
    assert len(edges) > 2 and all(x["status"] == "needs_review" and x["causal_edge"] and x["affected_kpi"] for x in edges)
    sc = {r["ticker"]: r for r in scoring.compute_scores(store, index)}
    assert sc["NVDA"]["event_score_7d"] == 0.0 and sc["TSM"]["event_score_7d"] > 0 and sc["TSM"]["n_review_7d"] == 1


def test_retroactive_readthrough_cap(store):
    now = datetime.now(timezone.utc)
    ids = store.upsert_events([dict(uid="fan", source="s", source_name="CNBC", source_type="news", title="Canadian dollar slides as tariff talks stall", published=now,
                                    relevant=1, materiality=0.54, direction=-1, event_type="tariff_trade", classifier="rules")])
    store.set_impacts(ids[0], [(f"T{i}", "readthrough", -0.03) for i in range(15)])
    assert store.apply_readthrough_cap(12) == 1
    assert store.get_event(ids[0])["needs_review"] == 1 and store.impacts_for(ids)[ids[0]] == []
    assert store.apply_readthrough_cap(12) == 0


# ----------------------------------------------------------------------------- fail closed (S8)
def test_run_info_reports_a_blocked_run_and_banner_shows_it(monkeypatch, tmp_path, app_client):
    monkeypatch.setattr(config, "LATEST", tmp_path)
    (tmp_path / "run.json").write_text(json.dumps({"run_id": "r1", "asof_session": "2026-09-03", "generated_at": "2026-09-03T00:25:11-04:00", "actionable": True, "portfolio": {"cash": 0.5, "invested": 0.5}}))
    assert config.run_info()["blocked"] is None
    (tmp_path / "run_blocked.json").write_text(json.dumps({"at": "2026-09-04T07:15:00-04:00", "stage": "data", "reasons": ["prices unavailable: no data"]}))
    r = config.run_info()
    assert r["blocked"]["reasons"] == ["prices unavailable: no data"] and r["run_id"] == "r1"        # the last completed run stays on display, marked blocked
    js = app_client.get("/static/base.js").text
    assert "Run blocked" in js and "showing the last completed run" in js


# ----------------------------------------------------------------------------- connection hygiene
def test_store_releases_connections_of_finished_threads(store):
    import threading
    def work():
        store.kv_set("t", {"x": threading.get_ident()})      # opens this thread's connection
    threads = [threading.Thread(target=work) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.n_connections >= 1                              # registry still holds the dead threads' handles until reaped
    store.kv_get("t")                                            # any new connection (or reap()) closes them
    store.reap()
    assert store.n_connections <= 2                              # this thread (+ the fixture's) only
    store.close()
    assert store.n_connections <= 1
