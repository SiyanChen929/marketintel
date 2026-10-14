"""P1 W1/W2/D front-end slices: action-first home page, cases page, design-system unification (static checks on the served
pages + one TestClient workflow against a tmp store; no network, no browser)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_p1_ui import _scripts, _strip_js

STATIC = Path(__file__).resolve().parents[1] / "market_intel" / "static"
PANEL_ORDER = ("Action required", "Portfolio impact", "Upcoming catalysts", "New evidence", "System", "Live feed")
DRAWER_FIELDS = ("d-owner", "d-due", "d-status", "d-decision", "d-notrade", "d-exec", "d-signoff", "d-note", "d-addnote", "d-ack", "d-evidence", "d-chain", "d-save")


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    from market_intel.store import Store
    return Store(tmp_path_factory.mktemp("p1home") / "t.sqlite")


@pytest.fixture(scope="module")
def client(store, tmp_path_factory):
    """Offline TestClient at a loopback address (auth-exempt), mirroring tests/test_market_intel.py::app_client."""
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    tmp = tmp_path_factory.mktemp("p1home-auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("MARKET_INTEL_NO_LLM", "1")
    mp.delenv("MARKET_INTEL_TOKEN", raising=False)
    mp.setattr(auth, "TOKEN_PATH", tmp / "token")
    index = EntityIndex([Company(ticker="MU", name="Micron Technology, Inc.", segment="Memory & Storage", aliases=[], weight=0.15, model_weight=0.30, cik=723125, next_earnings="2026-09-30"),
                         Company(ticker="NVDA", name="NVIDIA Corporation", segment="AI Compute Silicon", aliases=[], cik=1045810)])
    c = TestClient(create_app(store=store, index=index), client=("127.0.0.1", 50000))
    yield c
    mp.undo()


@pytest.fixture(scope="module")
def home(client) -> str:
    r = client.get("/")
    assert r.status_code == 200
    return r.text


@pytest.fixture(scope="module")
def cases_page(client) -> str:
    r = client.get("/cases")
    assert r.status_code == 200
    return r.text


@pytest.fixture(scope="module")
def base_js(client) -> str:
    return client.get("/static/base.js").text


@pytest.fixture(scope="module")
def base_css(client) -> str:
    return client.get("/static/base.css").text


def _markup(html: str) -> str:
    """Static markup only: inline scripts and styles removed."""
    return re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.S)


# ----------------------------------------------------------------------------- W1 action-first home
def test_home_panels_in_required_order(home):
    idx = [home.index(s) for s in PANEL_ORDER]
    assert idx == sorted(idx) and len(set(idx)) == len(PANEL_ORDER), list(zip(PANEL_ORDER, idx))
    for pid in ("actions-panel", "impact-panel", "cat-panel", "evidence-panel", "health-panel", "ask-panel", "cal-panel", "brief-panel", "src-panel", "feed-panel"):
        assert f'id="{pid}"' in home, pid
    assert home.index('id="feed-panel"') > home.index('id="src-panel"')       # live feed last
    assert 'id="banner"' in home and "statusBanner(" in home and "runChip(" in home and 'id="hdr"' in home
    assert "relevant events (24h)" in home and "alerts ·" in home and "cases" in home


def test_home_action_feed_wiring(home):
    assert "fetchJSON(" in home and "/api/actions" in home and "fetchJSON(`/api/actions" in home
    assert "Mark all read" in home and "/api/events/read" in home and "/ack`" in home and "/read`" in home
    assert 'id="ar-level"' in home and 'id="ar-ticker"' in home and 'id="ar-unread" checked' in home
    assert "Acknowledge" in home and "Open case" in home and "/cases?id=" in home and "/cases?ticker=" in home
    assert "mi-last-visit" in home and 'id="ne-seen"' in home and "since" in home
    assert "/api/poll" in home and "s.polling" in home and "setTimeout(refreshAll" not in home       # real busy state, no fixed timer
    assert "visiblePoll(" in home and "offset" in home and "Load more" in home and "LIMIT=50" in home
    assert "qs()" in home and "q.get('ticker')" in home and "setQS(" in home and "function setTicker(" in home
    assert 'data-f="focus"' in home and "filter:state.filter==='focus'?'holdings'" in home and "passesFocus" in home
    assert "<details" in home and "36h half-life" in home                                                  # formula text moved into a drawer
    assert 'href="#ev-$1"' in home and 'id="ev-${i+1}"' in home                                            # clickable citations
    assert "st.n_events" in home and 'class="contrib"' in home and "LLM-classified" in home                 # story badge, contribution line, lineage tooltip kept


def test_home_design_system_and_vocabulary(home, base_js):
    src = (STATIC / "dashboard.html").read_text()
    assert ":root{" not in src and "prefers-color-scheme" not in src and "--bg:#" not in src
    assert "renderNav('/')" in src and "themeInit()" in src
    for helper in ("setPanel(", "fetchJSON(", "announce(", "busy(", "asOfChip(", "tableTools(", "cardRow(", "sortable(", "Badge(", "Kpi(", "Toast.", "postJSON(", "mdLite("):
        assert helper in src, helper
    assert re.search(r'<button type="button" class="chip btn" id="btn-theme"[^>]*aria-pressed=', src)
    assert '<span class="chip btn"' not in src and "onclick=" not in src
    assert "Claude-classified" not in src and "(FTS)" not in src and 'placeholder="search events…"' in src
    markup = _markup(src)
    assert "gnews_" not in markup and "holdings" not in markup.replace('data-f="holdings"', "")
    assert not re.search(r"\bheld\b", src, re.I)
    assert "Target list" in src and "target " in src


def test_base_js_nav_and_shared_components(base_js, base_css):
    assert "['/risk','风险分析']" in base_js and "['/cases','Cases']" in base_js
    assert "function Badge(" in base_js and "function Kpi(" in base_js and "const Toast=" in base_js and "async function postJSON(" in base_js
    for name in ("runChip", "statusBanner", "fetchJSON", "setPanel", "announce", "busy", "asOfChip", "ensurePlotly", "plot", "takeaway", "chartTable", "sortable", "rowLink", "cardsFromRows", "tableTools", "visiblePoll", "qs", "setQS", "mdLite"):
        assert re.search(rf"(function|const) {name}\b", base_js), name
    assert "dialog.drawer" in base_css and ".cst" in base_css and ".chain" in base_css and ".tk span.target" in base_css


def test_home_cellphone_layout_and_secure_access(home, base_css):
    login = (STATIC / "login.html").read_text()
    assert 'viewport-fit=cover' in home and 'id="main-content"' in home and 'class="skip-link"' in home
    assert 'class="mobile-jumps"' in home and 'href="#feed-panel"' in home and 'href="#health-panel"' in home
    assert 'id="ar-more"' in home and "arShown:5" in home and "PHONE_MQ.matches?it.slice" in home
    assert 'id="impact-sort"' in home and 'id="impact-more"' in home and "updateImpactLimit" in home
    assert 'id="phone-access"' in home and 'id="phone-copy"' in home and "s.public_url" in home
    assert "Secure tunnel offline" in home and "sign-in required" in home and "access_token" not in home
    assert '[hidden]{display:none!important}' in base_css
    assert "input[type=search]" in base_css and "input[type=datetime-local]" in base_css
    assert "font-size:16px" in base_css and ".chip.btn,.chip.btn.sm{min-width:44px;min-height:44px}" in base_css
    assert "safe-area-inset" in base_css and "height:100dvh" in base_css and "prefers-reduced-motion:reduce" in base_css
    assert "viewport-fit=cover" in login and "min-height:100dvh" in login and ".box input" in login and "min-height:44px" in login


def test_tunnel_uses_http2_fallback():
    root = STATIC.parents[1]
    for name in ("com.siyanchen.marketintel.tunnel.plist", "com.siyanchen.marketintel.tunnel.named.plist"):
        src = (root / "scripts" / name).read_text()
        assert "<string>--protocol</string><string>http2</string>" in src, name


# ----------------------------------------------------------------------------- W2 cases page
def test_cases_page_markup(cases_page):
    assert "renderNav('/cases')" in cases_page and "themeInit()" in cases_page
    assert 'id="banner"' in cases_page and "statusBanner(" in cases_page and "runChip(" in cases_page and 'id="hdr"' in cases_page
    sel = re.search(r'<select id="f-status">(.*?)</select>', cases_page, re.S).group(1)
    for s in ("active", "open", "acknowledged", "in_review", "decided", "closed", "all"):
        assert f'value="{s}"' in sel, s
    assert 'id="f-ticker"' in cases_page and '<table id="cases">' in cases_page and "cardRow(" in cases_page and "tableTools('cases'" in cases_page
    for col in ("Created", "Ticker", "Level", "Title", "Owner", "Due", "SLA h", "Status", "Ack"):
        assert f">{col}</th>" in cases_page, col
    assert '<dialog class="drawer" id="drawer"' in cases_page
    for f in DRAWER_FIELDS:
        assert f'id="{f}"' in cases_page, f
    assert "/api/cases/'+" in cases_page and "'/api/cases/'+cur.case.id+'/ack'" in cases_page and "postJSON('/api/cases',body)" in cases_page
    assert "/?ticker=" in cases_page and "'/research'" in cases_page and "'/portfolio'" in cases_page and "'/risk'" in cases_page
    assert "overdue" in cases_page and 'id="new-form"' in cases_page and 'id="n-ticker"' in cases_page and 'id="n-title"' in cases_page and 'id="n-level"' in cases_page
    assert not re.search(r"\bheld\b", cases_page, re.I) and "no trade instructions" in cases_page


def test_case_flow_ack_list_update_get(client, store):
    now = datetime.now(timezone.utc)
    (eid,) = store.upsert_events([dict(uid="A", source="s", source_name="Reuters", source_type="news", title="Micron raises fiscal Q4 guidance on stronger HBM pricing", published=now - timedelta(hours=1),
                                       tickers=["MU"], relevant=1, materiality=0.85, direction=2, event_type="guidance", alert_level="critical", action="analyst review", url="https://x/a")])
    store.set_impacts(eid, [("MU", "direct", 0.5)])
    ar = client.get("/api/actions?hours=48").json()["action_required"]
    assert [x["id"] for x in ar] == [eid] and ar[0]["read"] == 0 and ar[0]["case_id"] is None
    r = client.post(f"/api/events/{eid}/ack", json={"by": "cs"})
    assert r.status_code == 200 and r.json()["read"] is True and r.json()["case"]["status"] == "acknowledged" and r.json()["case"]["ticker"] == "MU"
    cid = r.json()["case"]["id"]
    lst = client.get("/api/cases?status=active").json()
    assert [c["id"] for c in lst["cases"]] == [cid] and lst["cases"][0]["level"] == "critical" and lst["cases"][0]["ack_by"] == "local" and lst["summary"]["open"] == 1
    assert client.get("/api/cases?status=active&ticker=MU").json()["count"] == 1 and client.get("/api/cases?status=closed").json()["count"] == 0
    r = client.post(f"/api/cases/{cid}", json={"decision": "refresh model inputs", "status": "decided", "owner": "cs", "note": "HBM contract print confirmed", "by": "cs"})
    assert r.status_code == 200 and r.json()["case"]["decision"] == "refresh model inputs" and r.json()["case"]["status"] == "decided"
    g = client.get(f"/api/cases/{cid}").json()
    # identity is server-derived: the client sent by="cs", the server records the session actor instead
    assert g["case"]["notes"][-1]["text"] == "HBM contract print confirmed"
    assert g["case"]["notes"][-1]["by"] == "local"        # author is the session actor, not the body's "cs"
    assert g["case"]["owner"] == "cs"                     # owner is an assignment and stays client-settable
    assert [e["id"] for e in g["evidence"]] == [eid] and g["evidence"][0]["read"] == 1
    ar = client.get("/api/actions?hours=48").json()
    assert ar["action_required"][0]["case_id"] == cid and ar["action_required"][0]["case_status"] == "decided" and ar["action_required"][0]["read"] == 1
    assert ar["counts"]["escalations_unread"] == 0 and ar["counts"]["cases_open"] == 0
    assert client.post("/api/events/read", json={"ids": [eid], "read": False}).json()["n"] == 1
    assert client.get("/api/actions?hours=48").json()["counts"]["escalations_unread"] == 1
    assert client.post("/api/events/read", json={"ids": [eid]}).json()["ok"] is True


# ----------------------------------------------------------------------------- syntax sanity
def test_inline_scripts_balanced(home, cases_page):
    for name, html in (("dashboard.html", home), ("cases.html", cases_page)):
        scripts = _scripts(html)
        assert scripts, name
        for i, s in enumerate(scripts):
            code = _strip_js(s)
            for o, c in ("{}", "()", "[]"):
                assert code.count(o) == code.count(c), (name, i, o, code.count(o), code.count(c))
            assert code.count("`") % 2 == 0, (name, i)
