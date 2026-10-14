"""P1 front-end slices for the risk workbench page (/risk) and the per-name dossier section on /ticker/{T}.

Static checks on the served pages (no network, no browser) plus key checks on the live payloads when pipeline output exists.
Mirrors tests/test_p1_ui.py: offline TestClient at a loopback address, string assertions, brace balance per inline <script>."""
from __future__ import annotations

import re

import pytest

from test_p1_ui import _scripts, _strip_js

RISK_HEADINGS = ["Weights", "Active", "Exposures", "Gross", "VaR", "Stress", "Betas", "Event concentration", "Liquidity", "Current vs target", "Compliance"]
RISK_KEYS = ["run", "generated_at", "asof_prices", "benchmark", "weights", "active", "active_summary", "exposures", "gross_net", "liquidity", "var", "betas", "stress",
             "event_concentration", "current_vs_target", "compliance", "wording"]
DOSSIER_KEYS = ["ticker", "run", "validation_status", "identity", "horizon", "benchmark", "price", "target_list", "pt_bridge", "consensus", "scores", "fundamentals",
                "strategic", "events", "scenarios", "scenarios_note", "expected_return", "downside", "kill_criteria", "watch_kpis", "wording"]


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    """Offline TestClient at a loopback address (auth-exempt), mirroring tests/test_p1_ui.py::client."""
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    tmp = tmp_path_factory.mktemp("p1uirisk")
    mp = pytest.MonkeyPatch()
    mp.setenv("MARKET_INTEL_NO_LLM", "1")
    mp.delenv("MARKET_INTEL_TOKEN", raising=False)
    mp.setattr(auth, "TOKEN_PATH", tmp / "token")
    index = EntityIndex([Company(ticker="MU", name="Micron Technology, Inc.", segment="Memory & Storage", aliases=[], weight=0.1, cik=723125, next_earnings="2026-09-30")])
    c = TestClient(create_app(store=Store(tmp / "t.sqlite"), index=index), client=("127.0.0.1", 50000))
    yield c
    mp.undo()


@pytest.fixture(scope="module")
def risk_html(app_client) -> str:
    r = app_client.get("/risk")
    assert r.status_code == 200
    return r.text


@pytest.fixture(scope="module")
def ticker_html(app_client) -> str:
    r = app_client.get("/ticker/MU")
    assert r.status_code == 200
    return r.text


def _pipeline_available(app_client) -> bool:
    r = app_client.get("/api/research/summary")
    return r.status_code == 200 and bool(r.json().get("available"))


# ----------------------------------------------------------------------------- /risk: wiring, headings, vocabulary
def test_risk_page_wiring(risk_html):
    h = risk_html
    assert "renderNav('/risk')" in h and "themeInit()" in h
    assert "statusBanner(" in h and "runChip(" in h and "asOfChip(" in h
    assert 'id="nav"' in h and 'id="banner"' in h and 'id="hdr"' in h
    assert re.search(r'<button type="button" class="chip btn" id="btn-theme"[^>]*aria-pressed=', h)
    assert "/api/research/risk" in h and "fetchJSON(" in h
    assert "setPanel(" in h and "'loading'" in h and "'fresh'" in h and "'error'" in h and "'empty'" in h and "announce(" in h
    plots = re.findall(r'id="([^"]+)" class="plot', h)
    assert plots and "plot(" in h and "chartNote(" in h
    for pid in plots:
        assert f"chartNote('{pid}'" in h, pid                                          # every chart has a takeaway + data table
    assert "sortable(" in h and "tableTools(" in h and "cardRow(" in h and "rowLink(" in h and "cardsFromRows" not in h or "cardRow(" in h
    assert "qs().get('dim')" in h and "setQS({dim:" in h                                # W6 URL state for the exposure dimension
    handlers = re.findall(r"addEventListener\('themechange',(.*?)\);", h)
    assert handlers
    for hd in handlers:
        assert "load(" not in hd and "fetch" not in hd, hd
    assert "onclick=" not in h and '<span class="chip btn"' not in h                   # W4
    assert 'src="/static/plotly.min.js"' not in h and "plotly-cartesian" not in h     # W9 lazy Plotly via base.js
    assert "location.href='/ticker/${" not in h


def test_var_panel_spans_both_desktop_columns(risk_html):
    """#var-panel is followed by a full-width panel, so a half-width VaR block left the right column empty."""
    var_tag = re.search(r'<section[^>]*id="var-panel"[^>]*>', risk_html)
    assert var_tag, "the VaR panel is missing"
    classes = re.search(r'class="([^"]*)"', var_tag.group(0))
    assert classes and "wide" in classes.group(1).split(), var_tag.group(0)
    # the panel after it is full width; a half-width VaR panel is what produced the blank right column
    order = re.findall(r'<section[^>]*id="([\w-]+)"[^>]*class="([^"]*)"|<section[^>]*class="([^"]*)"[^>]*id="([\w-]+)"',
                       risk_html)
    ids = [(m[0] or m[3], m[1] or m[2]) for m in order]
    names = [i for i, _ in ids]
    assert "var-panel" in names and "stress-panel" in names
    assert names[names.index("var-panel") + 1] == "stress-panel"
    assert "wide" in dict(ids)["stress-panel"].split()


def test_risk_page_panels_and_vocabulary(risk_html):
    h = risk_html
    h2s = re.findall(r"<h2>(.*?)</h2>", h, re.S)
    assert len(h2s) >= 11
    for heading in RISK_HEADINGS:
        assert any(heading.lower() in x.lower() for x in h2s), heading
    assert "no live position ledger" in h and "benchmark = " in h                      # permanent scope note
    assert "executable target" in h.lower() and "model" in h.lower()
    assert "no ledger" in h and "n/a (no ledger)" in h and "current_live_weight" in h   # current vs target: no ledger, no trade column values
    assert "not computable — no volume/NAV" in h                                       # liquidity: explicit cells + note
    assert "reconciles" in h and "✓" in h and "✗" in h                                 # targets + cash = 100% flag, compliance marks
    assert "var_95" in h and "var_99" in h and "es_975" in h and "worst_day" in h and "best_day" in h
    assert "tracking_error_ann" in h and "var-windows" in h        # window switcher and tracking error are surfaced
    assert "var-h10" in h and "non_overlapping" in h and "sqrt_time" in h   # 10-day horizon: all three routes shown
    assert "event_concentration" in h and "portfolio_beta" in h and "compliance" in h
    for word in ("held", "holdings", "buy", "sell", "trim"):
        assert not re.search(rf"\b{word}\b", h, re.I), word


# ----------------------------------------------------------------------------- /ticker/{T}: dossier section
def test_ticker_dossier_section(ticker_html):
    h = ticker_html
    assert 'id="dossier"' in h and "/api/research/dossier/" in h and "fetchJSON(" in h and "setPanel('#dossier'" in h
    assert "Relative to" in h and "kill criteria" in h and "data/dossiers" in h and "data/dossiers/README.md" in h
    assert 'id="pt-tbl"' in h and 'id="cons-tbl"' in h and 'id="scen"' in h and 'id="dos-strat"' in h and 'id="dos-answer"' in h
    assert "Blended fair value" in h and "Actual / surprise" in h and "Probability-weighted" in h and "scenarios_note" in h and "prob_weighted_upside" in h
    assert "Kill criteria" in h and "Watch KPIs" in h and "stale" in h
    assert h.index('id="kpis"') < h.index('id="dossier"') < h.index('id="px-panel"')  # placed after the KPI row
    assert "loadDossier();" in h                                                        # refresh reloads the dossier too
    # everything that existed stays: price chart with SOXX, valuation, fundamentals, events, strategic panel, target/model chip, pillar label handling
    assert 'id="p-px"' in h and "soxx_index" in h and "chartNote('p-px'" in h
    for pid in ("val-panel", "fund-panel", "ev-panel", "strat-panel", "tear-panel"):
        assert f'id="{pid}"' in h, pid
    assert "const pillar=x=>typeof x==='string'?esc(x):x==null?'':fmt.num(x,2);" in h
    assert "pillar {pillar}" in h and "pillar:typeof s.strategic_pillar==='string'?s.strategic_pillar:fmt.num(s.strategic_pillar,2)" in h
    assert "target {weight} · model {model}" in h and "not in target list" in h
    assert "weight:fmt.pct(D.holding.weight)" in h and "model:fmt.pct(D.holding.model_weight)" in h
    assert not re.search(r"\bheld\b", h, re.I)
    handlers = re.findall(r"addEventListener\('themechange',(.*?)\);", h)
    assert handlers and all("fetch" not in hd and "load(" not in hd for hd in handlers)


# ----------------------------------------------------------------------------- syntax sanity
def test_inline_scripts_have_balanced_brackets(risk_html, ticker_html):
    for name, html in (("risk.html", risk_html), ("ticker.html", ticker_html)):
        scripts = _scripts(html)
        assert scripts, name
        for i, src in enumerate(scripts):
            code = _strip_js(src)
            for o, c in ("{}", "()", "[]"):
                assert code.count(o) == code.count(c), (name, i, o, code.count(o), code.count(c))
            assert code.count("`") % 2 == 0, (name, i)


# ----------------------------------------------------------------------------- live payloads carry the keys the pages read
def test_risk_api_payload_has_page_keys(app_client):
    if not _pipeline_available(app_client):
        pytest.skip("no pipeline output: /api/research/summary not available")
    r = app_client.get("/api/research/risk")
    assert r.status_code == 200
    d = r.json()
    for k in RISK_KEYS:
        assert k in d, k
    w = d["weights"]
    for k in ("executable", "model", "cash", "invested", "n_names", "reconciles", "scale"):
        assert k in w, k
    assert w["reconciles"] is True and abs(w["invested"] + w["cash"] - 1.0) < 1e-4 and w["n_names"] == len(w["executable"])
    assert set(w["executable"]) == set(w["model"])
    assert d["benchmark"].get("name") and isinstance(d["benchmark"].get("weights"), dict)
    assert d["active"] and {"ticker", "weight", "model", "benchmark", "active", "active_model", "in_target"} <= set(d["active"][0])
    assert {"active_share_exec", "active_share_model", "n_overweight", "n_underweight"} <= set(d["active_summary"])
    for k in ("segment", "country", "currency", "adr_share", "taiwan_china", "ai_capex_dependency"):
        assert k in d["exposures"], k
    assert d["exposures"]["segment"] and {"name", "weight", "model", "benchmark", "active"} <= set(d["exposures"]["segment"][0])
    for k in ("long", "short", "gross", "net", "cash", "leverage", "note"):
        assert k in d["gross_net"], k
    # risk.v4 contract: acquisition finality, uninterrupted common samples, frozen-position accounting
    v = d["var"]
    for k in ("schema_version", "positions_frozen", "no_preferred_estimate", "es_level", "es_level_note",
              "horizons_days", "cash", "session_index", "windows", "tracking_error", "method"):
        assert k in v, k
    assert v["schema_version"] == "risk.v4" and v["positions_frozen"] is True
    for legacy in ("var_95", "cvar_95", "var_99", "cvar_99", "headline_window", "window"):
        assert legacy not in v, f"v1 key {legacy} would be read with v2 semantics"
    if v.get("available"):
        assert v["provenance"]["calculator_code_hash"] and v["provenance"]["data_snapshot_hash"]
        for wk, win in v["windows"].items():
            if not win["available"]:
                assert win["reason"], wk                     # unavailable windows must say why
                continue
            o = win["horizons"]["1"]["overlapping"]
            assert o["var_95"] <= o["var_99"] and o["es_95"] <= o["es_975"] <= o["es_99"], wk
    assert isinstance(d["betas"], dict)
    assert isinstance(d["stress"], list) and d["stress"] and all({"name", "shock", "pnl", "method", "contributors"} <= set(s) for s in d["stress"])
    assert {"days", "names", "n", "weight_sum", "no_date"} <= set(d["event_concentration"])
    assert {"positions", "note", "computable", "nav_usd"} <= set(d["liquidity"]) and d["liquidity"]["nav_usd"] is None
    rows = d["current_vs_target"]["rows"]
    assert rows and all(r["current_live_weight"] is None and r["delta"] is None for r in rows) and "no ledger" in d["current_vs_target"]["note"]
    assert {r["ticker"] for r in rows} == set(w["executable"])
    checks = d["compliance"]["checks"]
    assert checks and all({"name", "limit", "value", "ok", "detail"} <= set(c) for c in checks) and "basis" in d["compliance"]
    assert "no live position ledger" in d["wording"]


def test_dossier_api_payload_has_page_keys(app_client):
    if not _pipeline_available(app_client):
        pytest.skip("no pipeline output: /api/research/summary not available")
    r = app_client.get("/api/research/dossier/MU")
    assert r.status_code == 200
    d = r.json()
    for k in DOSSIER_KEYS:
        assert k in d, k
    assert d["ticker"] == "MU" and d["horizon"] and d["benchmark"]
    pb = d["pt_bridge"]
    assert {"methods", "blended", "blend_weights"} <= set(pb)
    for m in pb["methods"].values():
        assert {"fair_value", "upside", "weight", "label"} <= set(m)
    cs = d["consensus"]
    assert {"current", "prior", "revisions", "actual", "surprise", "note", "next_earnings"} <= set(cs)
    assert (d["scenarios"] is None) == (d["scenarios_note"] is not None)
    assert {"value", "basis", "horizon"} <= set(d["expected_return"]) and {"value", "basis"} <= set(d["downside"])
    assert isinstance(d["kill_criteria"], list) and isinstance(d["watch_kpis"], list)
    if d["strategic"]:
        assert {"strategic_score", "scarcity_strength", "risk_score", "thesis", "kill_criteria", "watch_items", "strategic_asof", "stale"} <= set(d["strategic"])
    assert "no live position ledger" in d["wording"]
