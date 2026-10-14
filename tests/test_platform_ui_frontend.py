"""Static front-end checks for the platform console page (market_intel/static/platform.html).

Offline: no browser, no network, and no PlatformService is ever constructed (a fake is injected, so nothing
writes production state under data/platform).  The page is read from disk and served through TestClient at
/static/platform.html; the canonical route /platform is a backend change and is deliberately not asserted here.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_p1_ui import _scripts, _strip_js

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "market_intel" / "static"
PAGE = STATIC / "platform.html"

# The eight versioned platform endpoints the backend owner froze, plus the read-only LLM usage ledger.
# Nothing else may be called from the page.
API_ENDPOINTS = {
    "/api/v2/platform/overview",
    "/api/v2/platform/strategies",
    "/api/v2/platform/runs",
    "/api/v2/platform/paper",
    "/api/v2/platform/risk",
    "/api/v2/platform/paper/sessions",
    "/api/v2/platform/paper/{session_id}/step",
    "/api/v2/platform/paper/{session_id}/kill-switch",
    "/api/llm/usage",
}
STATUS_PHRASES = ("NOT VALIDATED", "PAPER ONLY", "NO LIVE BROKER")
FORBIDDEN = ("live trading", "positions held", "actionable now")
CSS_BEGIN = "===== BEGIN platform console"
CSS_END = "===== END platform console block ====="


class _FakePlatformService:
    """Never used by these tests, but injected so create_app cannot fall back to the real service."""

    def overview(self):
        return {"status": "NOT VALIDATED/PAPER", "live_trading_supported": False}

    def strategies(self):
        return {"items": []}

    def runs(self, strategy_id=None, limit=100):
        return {"items": []}

    def paper(self, session_id=None):
        return {"count": 0, "active_count": 0, "sessions": [], "corrupt_ledgers": []}

    def risk(self):
        return {"risk": {"available": False}}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store

    tmp = tmp_path_factory.mktemp("pfui")
    mp = pytest.MonkeyPatch()
    mp.setenv("MARKET_INTEL_NO_LLM", "1")
    mp.delenv("MARKET_INTEL_TOKEN", raising=False)
    mp.setattr(auth, "TOKEN_PATH", tmp / "token")
    index = EntityIndex([Company(ticker="MU", name="Micron", segment="Memory", aliases=[], weight=0.1, cik=723125)])
    c = TestClient(
        create_app(store=Store(tmp / "t.sqlite"), index=index, platform_service=_FakePlatformService()),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )
    yield c
    mp.undo()


@pytest.fixture(scope="module")
def page() -> str:
    assert PAGE.is_file(), PAGE
    return PAGE.read_text()


@pytest.fixture(scope="module")
def script(page) -> str:
    bodies = _scripts(page)
    assert len(bodies) == 1, f"expected exactly one inline script, found {len(bodies)}"
    return bodies[0]


@pytest.fixture(scope="module")
def markup(page) -> str:
    """The page with inline <script> bodies removed."""
    return re.sub(r"<script(?![^>]*\bsrc=)[^>]*>.*?</script>", "<script></script>", page, flags=re.S)


@pytest.fixture(scope="module")
def base_js() -> str:
    return (STATIC / "base.js").read_text()


@pytest.fixture(scope="module")
def css_block() -> str:
    css = (STATIC / "base.css").read_text()
    assert CSS_BEGIN in css and CSS_END in css, "the appended platform CSS block markers are missing"
    start = css.index(CSS_BEGIN)
    end = css.index(CSS_END)
    assert start < end
    # everything after the BEGIN comment up to the END comment is the block this page owns
    return css[css.index("*/", start) + 2:end]


# ----------------------------------------------------------------------------- serving + skeleton
def test_page_is_served_as_html(client, page):
    r = client.get("/static/platform.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text == page


def test_shared_page_skeleton(page):
    assert '<link rel="stylesheet" href="/static/base.css">' in page
    assert '<script src="/static/base.js"></script>' in page
    assert re.search(r'<header class="top"><h1>Market Intel</h1><span class="sub">', page)
    assert '<nav class="tabs" id="nav"></nav>' in page and '<div class="chips hdr" id="hdr"></div>' in page
    assert re.search(r'<button type="button" class="chip btn" id="btn-theme"[^>]*aria-pressed=', page)
    assert page.count("<header") == 1 and page.count("<nav") == 1
    assert '<main class="g2"' in page and '<div id="banner"' in page
    assert "renderNav('/platform')" in page and "themeInit()" in page
    assert "<h2>" in page and 'class="small"' in page


def test_page_uses_shared_helpers_and_no_plotly(page):
    for helper in ("setPanel(", "fetchJSON(", "postJSON(", "announce(", "busy(", "asOfChip(", "Kpi(", "Toast.", "visiblePoll("):
        assert helper in page, helper
    assert "plotly" not in page.lower() and "ensurePlotly" not in page and "Plotly.react" not in page


def test_page_stays_small(page):
    # raised as the contract tightened: idempotency bookkeeping, ledger/binding detail, step sizing evidence,
    # canonical risk metrics, the risk unavailable detail and the LLM budget card. Still one file, no framework.
    assert len(page.encode()) < 80 * 1024, len(page.encode())


# ----------------------------------------------------------------------------- honest status vocabulary
def test_status_strip_phrases_present(page):
    for phrase in STATUS_PHRASES:
        assert phrase in page, phrase
    assert 'class="pf-strip"' in page and 'id="strip-detail"' in page
    assert "NOT VALIDATED/PAPER" in page or "OV.status" in page


def test_forbidden_positive_claims_absent(page):
    low = page.lower()
    for bad in FORBIDDEN:
        assert bad not in low, bad
    assert not re.search(r"\bheld\b", low)
    assert "virtual positions" in low and "paper" in low


def test_strategy_registry_labels_are_explicit(page):
    assert "'Adapter not installed'" in page or '"Adapter not installed"' in page
    assert "Adapter not installed" in page and "Reference adapter" in page
    assert "reference_adapter" in page and "adapter_not_installed" in page
    assert "Installed" in page and "Planned" in page


# ----------------------------------------------------------------------------- API surface
def test_only_the_frozen_platform_endpoints_are_called(script):
    literals = set(re.findall(r"""['"`](/api/[^'"`\s]*)""", script))
    assert literals, "no /api/ literals found in the inline script"
    assert literals <= API_ENDPOINTS, sorted(literals - API_ENDPOINTS)
    # the two templated POST paths are assembled from the paper endpoint, not hard-coded
    assert "'/step'" in script and "'/kill-switch'" in script
    assert "encodeURIComponent" in script


def test_platform_links_to_risk_and_rebalance_history(markup, script):
    hrefs = set(re.findall(r'href="([^"]+)"', markup))
    assert hrefs <= {"/static/base.css", "/static/platform-performance.css", "/login?next=/platform", "#main-content", "/rebalances"}, sorted(hrefs)
    assert '/rebalances' in hrefs
    assert '<a href="/risk">' in script, "the risk tiles must link back to the existing Risk page"


# ----------------------------------------------------------------------------- accessibility / no inline handlers
def test_no_inline_event_handlers(page, markup):
    assert "onclick=" not in page
    assert not re.search(r"<[^>]*\son[a-z]{2,}\s*=", markup), "inline event-handler attribute in the markup"
    assert '<span class="chip btn"' not in page
    assert "addEventListener" in page


def test_every_button_declares_a_type(page):
    tags = re.findall(r"<button\b[^>]*>", page)
    assert tags
    for tag in tags:
        assert re.search(r'\btype="(button|submit)"', tag), tag


def test_controls_are_real_elements_with_alert_and_live_regions(page):
    assert 'role="alert"' in page and 'id="pf-err"' in page
    assert 'id="btn-step"' in page and 'id="btn-kill"' in page and 'id="btn-start"' in page
    assert '<form class="form pf-start" id="start-form"' in page
    for field in ("f-cash", "f-ccy", "f-lev", "f-age", "f-short", "f-margin"):
        assert f'id="{field}"' in page, field
    assert 'value="100000"' in page and 'value="USD"' in page
    assert 'type="checkbox" id="f-short"' in page and 'type="checkbox" id="f-margin"' in page
    assert "announce(" in page and "aria-pressed" in page and "aria-expanded" in page


def test_short_and_margin_are_shown_refused_and_never_submitted_true(page, script):
    """The API rejects allow_short/allow_margin = true, so the console must not offer them as choices."""
    for field in ("f-short", "f-margin"):
        tag = re.search(rf'<input[^>]*id="{field}"[^>]*>', page)
        assert tag, field
        assert " disabled" in tag.group(0), f"{field} must be a disabled control, not an operable checkbox"
        assert 'aria-describedby="borrow-why"' in tag.group(0), field
    why = re.search(r'id="borrow-why"[^>]*>(.*?)</p>', page, re.S)
    assert why, "the refusal reason must be on the page"
    reason = why.group(1)
    for word in ("borrow", "financing", "collateral", "corporate-action"):
        assert word in reason, word
    assert "allow_short:false,allow_margin:false" in script
    assert ".checked" not in script, "no checkbox state may reach the request body"
    assert "pf-lock" in page


def test_kill_switch_dialog_requires_a_reason_and_a_second_confirm(page):
    dlg = re.search(r"<dialog[^>]*id=\"kill-dlg\".*?</dialog>", page, re.S)
    assert dlg, "kill-switch dialog missing"
    body = dlg.group(0)
    ta = re.search(r"<textarea[^>]*id=\"kill-reason\"[^>]*>", body)
    assert ta and " required" in ta.group(0), "the kill-switch reason textarea must be required"
    assert 'id="kill-confirm"' in body and "Activate kill switch" in body
    assert 'id="kill-cancel"' in body and "Cancel" in body
    assert "showModal()" in page and ".close()" in page
    assert "killFrom" in page and "focus()" in page          # focus enters the dialog and returns to the trigger


def test_hash_widgets_offer_copy_and_show_full(page):
    assert "function hashHTML(" in page and "data-copy=" in page and "data-toggle=" in page
    assert "navigator.clipboard" in page and "function fallbackCopy(" in page and "execCommand('copy')" in page
    assert "Show full" in page and 'aria-expanded="false"' in page and "pf-hashfull" in page
    assert "Copied the full value to the clipboard" in page and "Copy failed" in page


def test_backend_refusals_are_shown_verbatim(page):
    assert "function onMutErr(" in page and "showErr(p.detail" in page
    assert "Your role is not permitted to operate paper sessions — '+p.detail" in page
    assert "Toast.bad(" in page
    assert "RO=true" in page and "p.code===403" in page


def _fn(script: str, start: str, end: str) -> str:
    a, b = script.index(start), script.index(end)
    assert a < b, (start, end)
    return script[a:b]


def test_every_platform_mutation_carries_an_idempotency_key(script):
    assert "function operationKey(" in script and "crypto.randomUUID" in script
    assert "'Idempotency-Key':e.key" in script, "the header must carry the stored key, not a freshly minted one"
    assert "platformPost(API.ps,body,'paper-session-create')" in script
    assert "platformPost(stepURL(SEL),body,'paper-session-step')" in script
    assert "platformPost(killURL(SEL),{reason:reason},'paper-session-kill')" in script
    # the backend regex is ^[A-Za-z0-9][A-Za-z0-9._~:/-]{0,127}$
    key = _fn(script, "function freshKey(", "function operationKey(")
    assert "A-Za-z0-9._~:/-" in key and ".slice(0,128)" in key
    assert re.search(r"replace\(/\^\[\^A-Za-z0-9\]\+/", key), "the key must start with an alphanumeric"


def test_a_failed_mutation_reuses_its_key_and_only_success_retires_it(script):
    """A lost or refused response must retry the SAME operation, never advance a second step."""
    assert "const OPKEY={}" in script
    assert "function canonicalBody(" in script and "function keySlot(" in script
    slot = _fn(script, "function keySlot(", "// server contract")
    assert "canonicalBody(body)" in slot, "the key is keyed by URL plus canonical body"
    op = _fn(script, "function operationKey(", "function retireKey(")
    assert "let e=OPKEY[slot]" in op and "if(!e)" in op, "an existing slot must reuse its key"
    assert "e.attempts++" in op
    # freshKey is minted exactly once, inside operationKey, and only when the slot is empty
    assert script.count("freshKey(") == 2, "freshKey must be defined once and called once"
    post = _fn(script, "async function platformPost(", "// Paint the status region")
    assert post.index("await postJSON(") < post.index("retireKey(url,body)"), "the key may only be retired after the call"
    failure = post[post.index("catch(err)"):post.index("retireKey(url,body)")]
    assert "throw err" in failure and "retireKey" not in failure, "a failure must keep the key"
    assert post.count("retireKey(url,body)") == 1
    assert "only a fully committed mutation retires its key" in post
    assert 'id="idem-note"' in script or "$('#idem-note')" in script


def test_only_the_exact_committed_state_retires_the_key(script):
    """Fail closed: pending, missing and unknown states all keep the key."""
    assert "const COMMITTED_STATE='committed'" in script
    assert "function isCommitted(r){return !!r&&r.mutation_state===COMMITTED_STATE;}" in script
    post = _fn(script, "async function platformPost(", "// Paint the status region")
    # a single guard returns for everything that is not exactly committed, before the only retire call
    assert post.index("if(!isCommitted(r))") < post.index("retireKey(url,body)")
    guard = post[post.index("if(!isCommitted(r))"):post.index("retireKey(url,body)")]
    assert "return r" in guard and "retireKey" not in guard, "a non-committed state must keep the key"
    assert "unrecognised mutation_state" in guard
    assert "not treated as a completed action" in guard
    assert "Fail closed" in post
    # isCommitted is an exact equality on the literal, never a truthiness or prefix test
    assert "r.mutation_state===COMMITTED_STATE" in script
    assert "startsWith" not in post and "indexOf(COMMITTED_STATE)" not in post


def test_an_unknown_or_missing_mutation_state_is_not_reported_as_success(script, page):
    notice = _fn(script, "function mutationOutcome(", "const RUNS_Q=")
    assert "return 'unknown'" in notice
    assert "Unresolved: unrecognised mutation state" in notice
    assert "is <b>not</b> reported as completed" in notice
    assert "not reported as rejected either" in notice
    assert "The Idempotency-Key is kept" in notice
    assert "cannot create a second " in notice
    # a missing state is rendered as an explicit 'none' rather than blank or undefined
    assert "r.mutation_state!=null&&r.mutation_state!==''?String(r.mutation_state):'none'" in notice
    # the three callers only celebrate on the committed state
    for handler, end, label in (("async function startSession(", "async function stepSession(", "paper session"),
                                ("async function stepSession(", "function openKill(", "paper step"),
                                ("async function confirmKill(", "// ---------- wiring", "kill switch")):
        body = _fn(script, handler, end)
        assert f"mutationOutcome(" in body and f"'{label}'" in body, handler
        assert "oc===COMMITTED_STATE" in body, handler
        assert "oc===PENDING_STATE" in body, handler
        assert "else Toast.bad('Unresolved: the API returned an unrecognised mutation state')" in body, handler
        # the success toast is inside the committed branch, never the default path
        assert body.index("oc===COMMITTED_STATE") < body.index("Toast.ok"), handler


def test_a_checkpoint_pending_mutation_keeps_its_key_and_is_not_shown_as_rejected(script, page):
    """HTTP 202 / committed_checkpoint_pending: the ledger is committed, only the witness lags."""
    assert "const PENDING_STATE='committed_checkpoint_pending'" in script
    assert "function isPending(r){return !!r&&r.mutation_state===PENDING_STATE;}" in script
    post = _fn(script, "async function platformPost(", "// Paint the status region")
    assert "isPending(r)?" in post and post.index("isPending(r)?") < post.index("retireKey(url,body)")
    assert "the same key is kept to repair the witness" in post
    notice = _fn(script, "function mutationOutcome(", "const RUNS_Q=")
    # committed, not rejected and not rolled back
    assert "already committed to the local PAPER ledger" in notice
    assert "It was not rejected and nothing was rolled back" in notice
    assert "repairs the witness only" in notice
    assert "cannot place another order or record another fill" in notice
    assert "mutation_state" in notice and "HTTP 202" in notice
    assert "ck.reason" in notice, "the API reason is passed through verbatim"
    # the notice is a status region, never the red refusal alert
    tag = re.search(r'<div[^>]*id="pf-pending"[^>]*>', page)
    assert tag and 'role="status"' in tag.group(0), "pending is a status region, not an alert"
    assert 'class="pf-alert2"' in tag.group(0), "pending must not use the red refusal styling"
    assert "$('#pf-pending')" in script
    assert "showErr(" not in notice
    # all three mutation paths report it
    for handler, end in (("async function startSession(", "async function stepSession("),
                         ("async function stepSession(", "function openKill("),
                         ("async function confirmKill(", "// ---------- wiring")):
        body = _fn(script, handler, end)
        assert "witness checkpoint pending" in body, handler


def test_panels_use_async_states_with_retry(page):
    for panel in ("#ans-panel", "#paper-panel", "#run-panel", "#risk-panel", "#strat-panel", "#runs-panel", "#llm-panel"):
        assert f"beginLoad('{panel}'" in page, panel
        assert f"setPanel('{panel}','error'" in page, panel
    assert "'empty'" in page


def test_a_background_refresh_does_not_flash_a_skeleton_over_existing_content(script):
    assert "function beginLoad(" in script
    guard = _fn(script, "function beginLoad(", "async function loadOverview(")
    assert "if(!have)setPanel(sel,'loading')" in guard
    # every loader goes through the guard rather than blanking its panel on each poll
    assert "setPanel('#ans-panel','loading')" not in script
    for panel, state in (("#ans-panel", "OV"), ("#strat-panel", "STR"), ("#runs-panel", "RNS"),
                         ("#risk-panel", "RSK"), ("#paper-panel", "PAP"), ("#llm-panel", "USG")):
        assert f"beginLoad('{panel}',{state})" in script, panel


def test_required_risk_metrics_render_as_four_tiles(page):
    for label in ("VaR 99% 1-day", "VaR 99% 10-day", "Expected Shortfall 97.5%", "Tracking Error"):
        assert label in page, label
    for key in ("var_99_1d", "var_99_10d", "es_97_5", "tracking_error"):
        assert key in page, key
    assert "unavailable" in page and "pf-tiles" in page and "construction_endpoint" in page


def test_risk_metrics_are_read_from_the_canonical_metrics_map(script):
    """The payload is a canonical map; guessing at aliases or scanning the spec object is gone."""
    assert "payload.metrics&&payload.metrics[key]" in script
    assert "ALIAS" not in script and "metricValue(" not in script
    assert "required_metric_keys" in script, "keys the API declares but this page does not know still get a tile"
    keys = re.search(r"const METRICS=\[(.*?)\];", script)
    assert keys
    assert [k for k, _ in re.findall(r"\['([^']+)','([^']+)'\]", keys.group(1))] == [
        "var_99_1d", "var_99_10d", "es_97_5", "tracking_error"
    ]


def test_fractions_are_formatted_as_percentages_with_unit_window_and_sample(script):
    """0.05 must never be printed as 0.05: it is a fraction of NAV and says so."""
    assert "function pctOf(" in script and "fmt.pct(" in script
    assert "pctOf(m.value)" in script, "the metric value goes through the percentage formatter"
    assert "tx(m.value)" not in script and "esc(String(m.value" not in script
    row = _fn(script, "function metricRow(", "function metricTile(")
    for field in ("window", "window_sessions", "observations", "horizon_days", "confidence_level",
                  "benchmark", "annualization_sessions", "confidence_interval"):
        assert field in row, field
    assert "pctOf(m.confidence_level,1)" in row and "pctOf(ci.lower)" in row and "pctOf(ci.upper)" in row
    assert "fraction_of_nav" in script and "annualized_fraction" in script
    tile = _fn(script, "function metricTile(", "function renderRisk(")
    assert "unit <code>" in tile, "the raw unit name stays visible next to the percentage"
    assert "unavailable" in tile and "rk.reason" in tile


def test_paper_currency_is_locked_to_usd_with_a_stated_reason(page, script):
    tag = re.search(r'<input[^>]*id="f-ccy"[^>]*>', page)
    assert tag, "the currency field is missing"
    assert " readonly" in tag.group(0) and 'aria-readonly="true"' in tag.group(0)
    assert 'value="USD"' in tag.group(0)
    assert 'aria-describedby="ccy-why"' in tag.group(0)
    why = re.search(r'id="ccy-why"[^>]*>(.*?)</p>', page, re.S)
    assert why, "the USD-only reason must be on the page"
    for word in ("FX", "point-in-time", "refuses"):
        assert word in why.group(1), word
    assert "currency:'USD'" in script, "the request body must always carry USD"
    assert "$('#f-ccy').value" not in script, "no typed currency may reach the request body"


def test_llm_panel_reads_only_the_usage_ledger_and_names_what_it_measures(page, script):
    assert 'id="llm-panel"' in page and 'id="llm-body"' in page
    assert "LLM usage &amp; budget" in page
    assert "lu:'/api/llm/usage'" in script
    body = _fn(script, "function renderUsage(", "// ---------- paper ledger")
    # this is Market Intel's own API spend, not a chat-session token count
    assert "not</b> the token count of an interactive chat session" in body
    assert "provider invoice" in body
    for field in ("schema_version", "generated_at", "persistence", "totals", "token_definition",
                  "by_provider_model_operation", "recent"):
        assert field in body, field


def test_token_budget_card_shows_used_limit_percentage_bar_and_window(script):
    body = _fn(script, "function renderUsage(", "// ---------- paper ledger")
    assert "t.configured===true" in body, "the card must branch on whether a budget is configured"
    assert "n0(t.used)+' / '+n0(t.limit)" in body
    assert "progressBar(frac" in body and "fmt.pct(frac,1)" in body
    assert "n0(t.remaining)" in body
    assert "winTxt(b.window_seconds)" in body and "b.window_start" in body and "b.window_end" in body
    # not configured is stated honestly, and persisted usage is still shown
    assert "t.configured===false" in body, "'not configured' and 'unknown' must be separate states"
    assert "Budget not configured" in body
    assert "Usage is still metered and persisted" in body
    # an unreadable ledger reports configured=null: never claim a budget is absent in that case
    assert "Budget state unknown" in body
    assert "the usage ledger could not be read" in body
    # blocked is loud, and never promises that waiting will clear it: a block can come from pricing,
    # schema or usage being unavailable, not only from an exhausted rolling window
    assert "b.blocked===true" in body and "pf-goalb" in body and "LLM budget blocked" in body
    assert "until the reported reasons are resolved" in body
    assert "until the window rolls forward" not in body
    bar = _fn(script, "function progressBar(", "function renderUsage(")
    assert 'role="progressbar"' in bar and "aria-valuenow" in bar and "aria-valuetext" in bar
    assert "aria-valuemin" in bar and "aria-valuemax" in bar


def test_llm_cost_is_shown_only_when_the_ledger_says_available(script):
    body = _fn(script, "function renderUsage(", "// ---------- paper ledger")
    spend = _fn(script, "function knownSpend(", "function renderUsage(")
    assert "c.status!=='available'" in spend and "return null" in spend, "no dollars unless the ledger says available"
    assert "unavailable" in body and "c.reason" in body and "unavailable_calls" in body
    assert "No price is inferred here" in body
    # no arithmetic on prices anywhere in the panel
    for forbidden in ("*rate", "per_million", "/1000", "*1e6", "price*"):
        assert forbidden not in body, forbidden
    assert "bc.configured===true" in body and "Cost budget not configured" in body
    # null means the ledger could not be read: that is unknown, not absent
    assert "bc.configured===false" in body, "'not configured' and 'unknown' must be separate cost states"
    assert "Cost budget state unknown" in body
    assert "neither a limit nor a spend can be reported" in body
    # every spend figure in the budget block needs BOTH an available ledger cost and an available budget status
    assert "const spendOk=ks!=null&&bc.status==='available'" in body
    assert "spendOk?usd(bc.currency,bc.known_used_usd)" in body
    assert "spendOk&&bc.remaining_usd!=null" in body
    # the configured limit is an operator setting, so it stays visible and is labelled as such
    assert "configured limit, not a measured spend" in body


def test_recorded_known_cost_is_shown_even_without_a_configured_cost_budget(script):
    """A configured budget is not a precondition for reporting what the ledger already priced."""
    spend = _fn(script, "function knownSpend(", "function renderUsage(")
    order = re.search(r"const src=\[(.*?)\];", spend, re.S)
    assert order, "the field preference order must be explicit"
    assert [f for f in re.findall(r"'([a-z.of_]+)'", order.group(1))] == [
        "cost.usd", "cost.known_usd", "budget.cost.known_used_usd"
    ]
    body = _fn(script, "function renderUsage(", "// ---------- paper ledger")
    assert "recorded known cost" in body
    assert "ledger field " in body, "the figure names the ledger field it came from"
    # the honest framing survives next to the number
    assert "It is not a provider invoice" in body
    assert "no price on this page is inferred or estimated" in body
    # and the budget card still says the limit is absent
    assert "no spend limit is enforced" in body
    assert "The recorded known cost above is still what the ledger priced" in body


def test_llm_breakdown_and_recent_calls_are_folded_away(script):
    body = _fn(script, "function renderUsage(", "// ---------- paper ledger")
    assert '<details class="pf-det">' in body and "<summary>" in body
    assert body.index("Totals in the window") < body.index("<details")
    assert "dataTable(" in body, "tables become stacked cards on a phone through the shared renderer"


ROLE_CONTRACT = ("price_panel", "construction_weights", "landed_weights", "universe", "benchmark")


def test_every_required_dataset_role_is_listed_and_a_missing_one_is_flagged(script):
    grid = _fn(script, "function roleGrid(", "// ---------- frozen run")
    # the five roles come from the API's required_roles, never from a hard-coded list on the page
    assert "required.map(" in grid and "versions?versions[r]" in grid
    assert "MISSING" in grid and "pf-rolebad" in grid and "pf-miss" in grid
    assert "hashHTML(" in grid, "a bound version id must be copyable and expandable"
    assert "raw.version_id" in grid, "a lineage record exposes its version_id"
    run = _fn(script, "function renderRun(", "// ---------- strategy registry")
    assert "li.required_roles" in run and "li.versions" in run
    assert "roleGrid(req,vers)" in run
    assert "input_manifest_hash" in run
    # a legacy manifest hash is not lineage and must be labelled as such
    assert "legacy_data_manifest_hash" in run
    assert "not a content-addressed lineage" in run
    assert "never counts toward completeness" in run


def test_the_five_contract_roles_are_not_reimplemented_on_the_page(script):
    """The role list is the backend's; the page must not carry its own copy that could drift."""
    literals = {m.group(2) for m in re.finditer(r"""(['"])(.*?)\1""", script)}
    for role in ROLE_CONTRACT:
        assert role not in literals, f"{role} is a hard-coded literal instead of read from required_roles"


def test_paper_step_gate_names_the_confirmed_quote_version_requirement(script):
    gate = _fn(script, "function gateNote(", "// ---------- loading")
    assert "confirmed, content-addressed quote version" in gate
    assert "caps.paper_step_requires_quote_versions===true" in gate
    assert "caps.pinned_confirmed_quote_versions_supported===true" in gate
    assert "paper_step_ready" in gate
    # a missing pin is a refusal to price, never a generic data outage
    assert "refused by the backend" in gate
    assert "a refusal to price, not missing data on this page" in gate
    sync = _fn(script, "function syncButtons(", "function gateNote(")
    assert "caps.paper_step_ready===true" in sync
    assert "requires confirmed, content-addressed quote versions" in sync


def test_an_unsynchronized_witness_is_not_described_as_merely_behind(script):
    """It may be missing, ahead, conflicting or invalid: only the API knows which."""
    blk = _fn(script, "function checkpointBlock(", "// ---------- LLM usage")
    assert "behind the local head" not in blk
    assert "not synchronized with the local head" in blk
    assert "missing, ahead, conflicting or invalid" in blk
    assert "this page does not guess" in blk
    assert "x.reason" in blk, "the API reason is rendered verbatim"


def test_the_first_screen_separates_api_state_from_documented_engine_limits(page, script):
    head = re.search(r'id="ans-panel">\s*<div class="head">.*?</div>', page, re.S)
    assert head, "the answers panel head is missing"
    assert "every answer below is read from the platform API" not in page, "that claim is false for the engine row"
    assert "from API" in head.group(0) and "engine limit" in head.group(0)
    answers = _fn(script, "function renderAnswers(", "function renderRun(")
    # exactly one row is tagged as a documented constraint, and it is the engine-limits row
    assert answers.count(",'doc']") == 1
    doc_row = answers[answers.index("What can the paper engine do?"):]
    assert ",'doc']" in doc_row[:doc_row.index("]);") + 3]
    assert "pf-src" in answers and "pf-srcdoc" in answers
    assert "r[2]==='doc'?'engine limit':'from API'" in answers


def test_complete_lineage_does_not_claim_a_missing_reason(script):
    answers = _fn(script, "function renderAnswers(", "function renderRun(")
    assert "no reason given" not in answers
    assert "all required content-addressed roles are bound and verified" in answers
    assert "li.complete===true" in answers
    # a reason is appended when the API supplies one, and the incomplete branch keeps its own wording
    assert "li.reason?'. '+esc(String(li.reason))" in answers
    assert "the API gave no reason" in answers
    assert "Roles bound: " in answers


def test_external_checkpoint_separates_absent_lagging_and_synchronized(script):
    blk = _fn(script, "function checkpointBlock(", "// ---------- LLM usage")
    assert "x.configured===true" in blk and "x.synchronized===true" in blk
    for state in ("not configured", "configured, not synchronized", "synchronized"):
        assert state in blk, state
    for field in ("store_type", "sequence", "event_hash", "checkpoint_hash", "key_id",
                  "ledger_sequence", "ledger_event_hash", "independent_trust_domain_required"):
        assert field in blk, field
    assert "x.reason" in blk
    # the local .head is a self-checksum in the ledger's own trust domain, not a witness
    assert "same trust domain" in blk and "not a witness" in blk
    assert "separately administered" in blk
    assert "Nothing outside this process has attested to this ledger" in blk
    # shown for a session and for a step result
    detail = _fn(script, "function sessionDetail(", "// a fraction rendered as a percentage")
    assert "checkpointBlock(s.external_checkpoint)" in detail
    step = _fn(script, "function renderStep(", "// ---------- button state")
    assert "checkpointBlock(r.external_checkpoint)" in step


def test_risk_unavailable_shows_frozen_inputs_and_never_implies_a_shortened_window(script):
    ctx = _fn(script, "function riskContext(", "function renderRisk(")
    assert "li.frozen_inputs_consumed" in ctx and "frozen_input_binding_hash" in ctx
    assert "li.frozen_datasets" in ctx and "roleGrid(fz.required_roles" in ctx
    assert "fz.binding_hash" in ctx and "fz.complete" in ctx
    assert "was <b>not</b> shortened" in ctx and "no substitute series was used" in ctx
    assert "unavailable rather than blank" in ctx
    # tiles say unavailable with a reason; none is ever left empty
    tile = _fn(script, "function metricTile(", "function renderRisk(")
    assert "unavailable" in tile and "rk.reason" in tile


def test_risk_shows_binding_construction_and_lineage_even_when_unavailable(script):
    ctx = _fn(script, "function riskContext(", "function renderRisk(")
    # algorithm scope and landed weights are separate facts
    assert "algorithm scope" in ctx and "landed weights" in ctx
    assert "cs.name" in ctx and "cs.source_construction" in ctx
    assert "bs.algorithm_identity" in ctx and "bs.landed_weights_source" in ctx
    assert "are separate facts and are not interchangeable" in ctx
    for field in ("data_snapshot_hash", "portfolio_weights_hash", "calculator_hash",
                  "calculation_sample_hash", "finality", "acquired_at"):
        assert field in ctx, field
    # the unavailable detail
    assert "un.code" in ctx and "un.reason" in ctx
    assert "un.window" in ctx and "un.window_sessions" in ctx
    assert "un.span_start" in ctx and "un.available_sessions" in ctx
    assert "un.trailing_uninterrupted_sessions" in ctx and "un.limiting_holding" in ctx
    assert "limiting_holdings" in ctx and "Limiting holdings" in ctx
    assert "no_shorter_window_or_substitute" in ctx and "no fallback sample was used" in ctx
    # it renders for every payload, not only the available one
    render = _fn(script, "function renderRisk(", "// ---------- LLM usage")
    assert "riskContext(rk.payload)" in render
    assert "rk.available===true&&bd" not in render


def test_tracking_error_reports_its_benchmark_kind_and_source(script):
    row = _fn(script, "function metricRow(", "function metricTile(")
    assert "m.benchmark_kind" in row and "m.benchmark_source" in row
    assert "benchmark kind" in row and "benchmark source" in row
    assert "m.estimator" in row


def test_session_detail_shows_binding_ledger_head_receipts_and_stale_marks(script):
    detail = _fn(script, "function sessionDetail(", "// a fraction rendered as a percentage")
    for field in ("binding", "workspace_id", "strategy_id", "portfolio_id"):
        assert field in detail, field
    assert "ledger_head" in detail
    for field in ("sequence", "event_hash", "checkpoint_path", "event_count", "open_order_count"):
        assert field in detail, field
    assert "operation_receipt_count" in detail and "operation receipts" in detail
    assert "stale_marks" in detail and "missing_marks" in detail
    assert "no substitute price is used" in detail
    assert "mark_status" in detail and "mark_time" in detail
    assert "runs may only advance inside this book" in detail


def test_first_screen_states_the_intraday_only_engine_limit(page, script):
    note = re.search(r'id="engine-note".*?</p>', page, re.S)
    assert note, "the paper controls must carry the engine-limit note"
    body = note.group(0)
    assert "intraday only" in body and "UTC session date" in body and "fails closed" in body
    answers = _fn(script, "function renderAnswers(", "function renderRun(")
    assert "What can the paper engine do?" in answers
    assert "intraday only" in answers and "fails closed" in answers
    assert "documented engine limits" in answers, "the limit must not be dressed up as an API reading"


def test_step_result_leads_with_plain_words_and_folds_the_evidence_away(script):
    step = _fn(script, "function renderStep(", "// ---------- button state")
    assert "const PHASE=" in script and "PHASE[r.phase]" in step
    assert "r.retroactive_fills_allowed===false" in step
    assert "operation_id" in step and "r.operation_receipt" in step
    assert "<details class=\"pf-det\">" in step and "<summary>" in step
    assert step.index("Nothing left the simulation") < step.index("<details")
    for field in ("sizing_evidence", "target_weight", "sizing_equity", "target_quantity",
                  "quote_lineage", "content_hash", "instrument_id"):
        assert field in step, field
    assert "pctOf(e.target_weight)" in step, "the target weight is also shown as a percentage"
    phases = re.search(r"const PHASE=\{(.*?)\};", script, re.S)
    assert phases
    for name in ("fills_processed_and_orders_submitted", "fills_processed", "orders_submitted", "no_change"):
        assert name in phases.group(1), name


def test_mobile_switches_tables_to_stacked_cards(page, css_block):
    assert "pf-stack" in page and "pf-tbl" in page and "function dataTable(" in page
    assert "max-width:576px" in css_block
    tail = css_block[css_block.index("max-width:576px"):]
    assert "body.pf .pf-tbl{display:none}" in tail and "body.pf .pf-stack{display:grid}" in tail


# ----------------------------------------------------------------------------- inline script health
def _bracket_depths(src: str) -> dict[str, int]:
    pairs = {")": "(", "}": "{", "]": "["}
    stack: list[str] = []
    for ch in src:
        if ch in "({[":
            stack.append(ch)
        elif ch in pairs:
            assert stack and stack[-1] == pairs[ch], f"unbalanced {ch!r} in the inline script"
            stack.pop()
    return {"depth": len(stack)}


def test_inline_script_brackets_balance(script):
    stripped = _strip_js(script)
    assert _bracket_depths(stripped)["depth"] == 0, "inline script brackets do not balance"


JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
# parse without executing: `new Function(src)` compiles the body and throws on any syntax error,
# including ones bracket counting cannot see (a duplicate const, a stray reserved word).
_JSC_CHECK = 'var s = readFile(arguments[0]); try { new Function(s); print("OK"); } catch (e) { print("SYNTAX " + e); }'


def _syntax_check(src: str, tmp_path) -> tuple[bool, str]:
    """Return (parsed, detail). Prefers node --check, falls back to the JavaScriptCore shell on macOS."""
    f = tmp_path / "platform_inline.js"
    f.write_text(src)
    node = shutil.which("node")
    if node:
        proc = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        return proc.returncode == 0, proc.stderr
    if Path(JSC).exists():
        checker = tmp_path / "check.js"
        checker.write_text(_JSC_CHECK)
        proc = subprocess.run([JSC, str(checker), "--", str(f)], capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        return "OK" in out and "SYNTAX" not in out, out
    return True, "no javascript engine available"


def test_inline_script_parses_in_a_real_javascript_engine(script, tmp_path):
    if not shutil.which("node") and not Path(JSC).exists():
        pytest.skip("no javascript engine on this machine")
    ok, detail = _syntax_check(script, tmp_path)
    assert ok, detail


def test_the_syntax_checker_actually_rejects_a_broken_script(script, tmp_path):
    """Guard the guard: bracket counting misses a duplicate const, so the engine check must catch it."""
    if not shutil.which("node") and not Path(JSC).exists():
        pytest.skip("no javascript engine on this machine")
    ok, detail = _syntax_check(script + "\nconst OPKEY={};\n", tmp_path)
    assert not ok, "the syntax checker passed a script that redeclares a const"


# ----------------------------------------------------------------------------- shared assets
def test_base_js_nav_gains_the_platform_entry(base_js):
    assert "['/cases','Cases'],['/platform','Platform']" in base_js
    m = re.search(r"const PAGES=\[(.*?)\];", base_js)
    assert m
    entries = re.findall(r"\['([^']*)','([^']*)'\]", m.group(1))
    assert len(entries) == 7, entries
    assert ("/risk", "Risk") not in entries  # Risk is a Portfolio subview.
    assert ("/platform", "Platform") in entries
    assert entries[-1] == ("/platform", "Platform")


def test_appended_css_block_is_fully_namespaced(css_block):
    body = re.sub(r"/\*.*?\*/", "", css_block, flags=re.S)
    body = re.sub(r"@media[^{]*\{", "", body)
    selectors = [s.strip() for group in re.findall(r"([^{}]+)\{", body) for s in group.split(",")]
    selectors = [s for s in selectors if s]
    assert selectors, "no selectors found in the appended block"
    for sel in selectors:
        assert sel.startswith(".pf-") or sel.startswith("body.pf"), sel
    assert ":root" not in css_block and "prefers-color-scheme" not in css_block


def test_page_defines_no_theme_tokens_of_its_own(page):
    assert ":root{" not in page and "--bg:#" not in page and "prefers-color-scheme" not in page
    assert "var(--" not in page, "page-level colour work belongs in the namespaced base.css block"
