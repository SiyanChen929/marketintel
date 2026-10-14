"""P1 front-end slices (static checks on the served pages, no network, no browser).

W4 accessibility, W6 URL/theme state, W3 async states, W5 responsive, W9 lazy Plotly, W8 research verdict page."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "market_intel" / "static"
PAGES = {"/portfolio": "portfolio.html", "/universe": "universe.html", "/ticker/MU": "ticker.html", "/macro": "macro.html", "/research": "research.html", "/login": "login.html"}
OWNED = ["portfolio.html", "universe.html", "ticker.html", "macro.html", "research.html", "login.html"]
SCRIPTED = ["portfolio.html", "universe.html", "ticker.html", "macro.html", "research.html"]
TABLE_PAGES = ["portfolio.html", "universe.html", "research.html"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Offline TestClient at a loopback address (auth-exempt), mirroring tests/test_market_intel.py::app_client."""
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    tmp = tmp_path_factory.mktemp("p1ui")
    mp = pytest.MonkeyPatch()
    mp.setenv("MARKET_INTEL_NO_LLM", "1")
    mp.delenv("MARKET_INTEL_TOKEN", raising=False)
    mp.setattr(auth, "TOKEN_PATH", tmp / "token")
    index = EntityIndex([Company(ticker="MU", name="Micron Technology, Inc.", segment="Memory & Storage", aliases=[], weight=0.1, cik=723125, next_earnings="2026-09-30")])
    c = TestClient(create_app(store=Store(tmp / "t.sqlite"), index=index), client=("127.0.0.1", 50000))
    yield c
    mp.undo()


@pytest.fixture(scope="module")
def pages(client) -> dict[str, str]:
    out = {}
    for path, fname in PAGES.items():
        r = client.get(path)
        assert r.status_code == 200, path
        out[fname] = r.text
    return out


@pytest.fixture(scope="module")
def base_js(client) -> str:
    r = client.get("/static/base.js")
    assert r.status_code == 200
    return r.text


@pytest.fixture(scope="module")
def base_css(client) -> str:
    r = client.get("/static/base.css")
    assert r.status_code == 200
    return r.text


# ----------------------------------------------------------------------------- helpers
def _scripts(html: str) -> list[str]:
    """Inline <script> bodies (external src= tags excluded)."""
    return [m.group(1) for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)]


def _strip_js(src: str) -> str:
    """Blank out comments, string/template/regex literal contents so bracket counting sees only code.
    Template literals keep their ${ } placeholders (scanned recursively), so nested templates stay balanced."""
    out: list[str] = []
    n = len(src)

    def prev_sig() -> str:
        for el in reversed(out):
            s = el.rstrip()
            if s:
                return s[-1]
        return ""

    def scan_template(i: int) -> int:                      # src[i] == '`'
        out.append("`")
        i += 1
        while i < n:
            c = src[i]
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if c == "`":
                out.append("`")
                return i + 1
            if c == "$" and src[i + 1:i + 2] == "{":
                out.append("${")
                i = scan_code(i + 2, "}")
                continue
            out.append(" ")
            i += 1
        return i

    def scan_code(i: int, stop: str | None) -> int:
        depth = 0
        while i < n:
            c = src[i]
            nxt = src[i + 1:i + 2]
            if c == "/" and nxt == "/":
                j = src.find("\n", i)
                j = n if j < 0 else j
                out.append(" " * (j - i))
                i = j
                continue
            if c == "/" and nxt == "*":
                j = src.find("*/", i + 2)
                j = n if j < 0 else j + 2
                out.append(" " * (j - i))
                i = j
                continue
            if c in "\"'":
                j = i + 1
                while j < n and src[j] != c:
                    if src[j] == "\\":
                        j += 1
                    j += 1
                out.append(c + " " * (j - i - 1) + c)
                i = j + 1
                continue
            if c == "`":
                i = scan_template(i)
                continue
            if c == "/":
                p = prev_sig()
                tail = "".join(out[-12:])
                if p == "" or p in "(,=:[!&|?{};+-*%<>~^" or re.search(r"\b(return|typeof|case|in|of)\s*$", tail):
                    j = i + 1
                    in_cls = False
                    while j < n:
                        ch = src[j]
                        if ch == "\\":
                            j += 2
                            continue
                        if in_cls:
                            if ch == "]":
                                in_cls = False
                        elif ch == "[":
                            in_cls = True
                        elif ch == "/" or ch == "\n":
                            break
                        j += 1
                    out.append("/" + " " * (j - i - 1) + "/")
                    i = j + 1
                    continue
            if c == "}" and stop == "}" and depth == 0:
                out.append("}")
                return i + 1
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            out.append(c)
            i += 1
        return i

    scan_code(0, None)
    return "".join(out)


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ----------------------------------------------------------------------------- W4 accessibility
def test_no_span_chip_buttons_and_theme_toggle_is_a_button(pages):
    for fname in OWNED:
        html = pages[fname]
        assert '<span class="chip btn"' not in html, fname
        assert "onclick=" not in html, fname
    for fname in SCRIPTED:
        html = pages[fname]
        assert re.search(r'<button type="button" class="chip btn" id="btn-theme"[^>]*aria-pressed=', html), fname
        assert 'id="nav"' in html and 'id="banner"' in html and 'id="hdr"' in html, fname


def test_base_js_accessibility_helpers(base_js):
    assert 'aria-current="page"' in base_js
    assert "aria-sort" in base_js and "e.key==='Enter'||e.key===' '" in base_js
    assert "aria-busy" in base_js and "aria-live" in base_js and "role','status'" in base_js
    assert "aria-pressed" in base_js and "aria-label" in base_js
    assert "function rowLink(" in base_js and '<a class="rl" href="/ticker/' in base_js
    assert "function chartTable(" in base_js and "function takeaway(" in base_js and "'takeaway'" in base_js and "createElement(kind==='tk'?'p'" in base_js
    assert "function cardsFromRows(" in base_js and "function cardRow(" in base_js and "function tableTools(" in base_js
    assert "function runChip(" in base_js and "function statusBanner(" in base_js


def test_rows_navigate_via_inner_anchor_not_onclick(pages, base_js):
    for fname in TABLE_PAGES:
        html = pages[fname]
        assert "rowLink(" in html or 'role="link"' in html or '<a class="rl"' in html, fname
        assert "location.href='/ticker/${" not in html, fname
    assert 'role="link"' in base_js or '<a class="rl"' in base_js


def test_muted_contrast_and_minimum_font_sizes(base_css):
    light = re.search(r":root\{[^}]*--bg:(#[0-9a-f]{6});[^}]*--muted:(#[0-9a-f]{6})", base_css)
    dark = re.search(r':root\[data-theme="dark"\]\{[^}]*--bg:(#[0-9a-f]{6});[^}]*--muted:(#[0-9a-f]{6})', base_css)
    assert light and dark
    assert light.group(1) == "#f9f9f7"
    assert _contrast(light.group(2), "#f9f9f7") >= 4.5 and _contrast(light.group(2), "#fcfcfb") >= 4.5
    assert _contrast(dark.group(2), dark.group(1)) >= 4.5 and _contrast(dark.group(2), "#1a1a19") >= 4.5
    sizes = [float(s) for s in re.findall(r"font-size:(\d+(?:\.\d+)?)px", base_css)]
    assert sizes and min(sizes) >= 11, min(sizes)
    assert re.search(r"\btable\{[^}]*font-size:12\.5px", base_css) and re.search(r"\bth\{[^}]*font-size:12px", base_css)
    assert ":focus-visible{outline:2px solid var(--focus)" in base_css and "--focus:" in base_css
    assert re.search(r"\.chip\.btn\{[^}]*min-height:36px", base_css) and "button.primary{" in base_css and "min-height:40px" in base_css
    assert 'th[aria-sort="ascending"]::after' in base_css and 'th[aria-sort="descending"]::after' in base_css


def test_charts_get_takeaway_and_data_table(pages):
    for fname in ["portfolio.html", "universe.html", "ticker.html", "macro.html", "research.html"]:
        html = pages[fname]
        plots = re.findall(r'id="([^"]+)" class="plot', html)
        assert "chartNote(" in html or "chartTable(" in html, fname
        for pid in plots:
            assert f"chartNote('{pid}'" in html or f"chartNote(id" in html or f"chartTable('{pid}'" in html, (fname, pid)


# ----------------------------------------------------------------------------- W6 URL + theme state
def test_url_state_sync(pages, base_js):
    assert "history.replaceState" in base_js and "function setQS(" in base_js and "function qs(" in base_js
    p = pages["portfolio.html"]
    assert "qs().get('method')" in p and "setQS({method:" in p
    u = pages["universe.html"]
    assert "function applyQS(" in u and "setQS({seg:" in u and "q.get('target')" in u and "q.get('q')" in u


def test_portfolio_exposes_all_constructions_and_redraws_dependent_outputs(pages):
    p = pages["portfolio.html"]
    expected = {"cap_weight", "inverse_vol", "risk_parity", "segment_risk_parity", "min_variance",
                "max_diversification", "hrp", "max_sharpe", "mean_variance", "kelly",
                "resampled_max_sharpe", "min_cvar", "equal_weight"}
    assert expected <= set(re.findall(r'<option value="([^"]+)"', p))
    assert "Mean-variance / Black-Litterman" in p
    assert "$('#method').addEventListener('change'" in p
    assert "if(P&&P.available)redraw();" in p
    assert "P.construction_backtest" in p and "renderKpis();drawWeights();drawRC();drawFrontier();drawCorr();drawBT()" in p


def test_theme_change_redraws_without_refetch(pages):
    for fname in SCRIPTED:
        html = pages[fname]
        handlers = re.findall(r"addEventListener\('themechange',(.*?)\);", html)
        assert handlers, fname
        for h in handlers:
            assert "load(" not in h and "fetch" not in h, (fname, h)


# ----------------------------------------------------------------------------- W3 async states
def test_async_states_and_abortable_fetch(pages, base_js):
    assert "AbortController" in base_js and "async function fetchJSON(" in base_js and "stale:true" in base_js
    assert "function setPanel(" in base_js and "'loading'" in base_js and "Retry" in base_js and "className='skel'" in base_js
    assert "function asOfChip(" in base_js and "refreshed" in base_js
    for fname in SCRIPTED:
        html = pages[fname]
        assert "setPanel(" in html and "'error'" in html and "fetchJSON(" in html and "asOfChip(" in html, fname
        assert "'empty'" in html, fname
    assert "Promise.allSettled" in pages["research.html"] and "allSettled" in pages["macro.html"]
    r = pages["research.html"]
    assert "/api/pipeline/status" in r and "visiblePoll(" in r and "aria-busy" in r and "setRunBusy(" in r
    assert "busy(btn,true" in pages["portfolio.html"]


def test_polling_pauses_when_hidden(base_js):
    assert "function visiblePoll(" in base_js and "visibilitychange" in base_js and "document.hidden" in base_js


# ----------------------------------------------------------------------------- W5 responsive
def test_responsive_css_cards_and_column_chooser(base_css, base_js, pages):
    assert "@media (max-width:760px)" in base_css and "@media (max-width:576px)" in base_css
    assert ".tblwrap th:first-child,.tblwrap td:first-child{position:sticky;left:0" in base_css
    assert ".tblwrap.cardable{display:none}.cards{display:grid}" in base_css
    assert ".colchooser" in base_css and '[data-density="comfortable"]' in base_css
    assert "localStorage.getItem(key)" in base_js and "try{hidden=JSON.parse" in base_js
    for fname, table in (("portfolio.html", "hold"), ("universe.html", "uni"), ("research.html", "strat")):
        assert f"tableTools('{table}'" in pages[fname] and "cardRow(" in pages[fname], fname


# ----------------------------------------------------------------------------- W9 lazy Plotly
def test_plotly_is_loaded_lazily(pages, base_js, client):
    for fname in OWNED:
        assert 'src="/static/plotly.min.js"' not in pages[fname], fname
        assert "plotly-cartesian" not in pages[fname], fname
    assert "function ensurePlotly(" in base_js and "/static/plotly-cartesian.min.js" in base_js and "/static/plotly.min.js" in base_js
    assert "ensurePlotly().then(" in base_js
    assert client.get("/static/plotly.min.js").status_code == 200
    partial = STATIC / "plotly-cartesian.min.js"
    if partial.exists():
        head = partial.read_bytes()[:64].decode("utf-8", "replace")
        assert partial.stat().st_size > 500_000 and (head.startswith("/**") or head.startswith("!function") or head.startswith("/*!"))


# ----------------------------------------------------------------------------- W8 research verdict page
def test_research_verdict_page(pages):
    r = pages["research.html"]
    for gate in ("pit_t_3m_ge_2", "wf_t_3m_ge_2", "live_forward_12m_ge_12"):
        assert gate in r
    assert "validation_status" in r and "permutation" in r and "Missing evidence" in r
    assert "JSON.stringify(V.live" not in r and "<dialog" in r and "mdLite(" in r and "chartTitle(" in r
    assert "fmt.num(r.mean_ic,3)" in r and "fmt.num(r.t_hac,2)" in r and "fmt.num(r.strategic_confidence,2)" in r
    assert "typeof x==='string'?esc(x)" in r and "<details>" in r
    assert "-month IC time series" in r
    assert 'id="run-full"' in r and 'id="run-fast"' in r


# ----------------------------------------------------------------------------- preserved content + vocabulary
def test_preserved_sections_and_vocabulary(pages):
    p = pages["portfolio.html"]
    assert 'id="bt-rules"' in p and "function sharpeStd(" in p and "Hindsight illustration" in p and "Invalid as track record" in p
    assert "Target w" in p and "Model w" in p
    for fname in ("portfolio.html", "universe.html", "ticker.html", "research.html"):
        assert not re.search(r"\bheld\b", pages[fname], re.I), fname
    m = pages["macro.html"]
    assert "obs {date}{fetched}" in m and "date:s.dates[s.dates.length-1]" in m
    assert "fetched" in m and "unit" in m


# ----------------------------------------------------------------------------- syntax sanity
def test_inline_scripts_have_balanced_brackets(pages, base_js):
    blocks = {"base.js": base_js}
    for fname in SCRIPTED:
        scripts = _scripts(pages[fname])
        assert scripts, fname
        for i, s in enumerate(scripts):
            blocks[f"{fname}#{i}"] = s
    for name, src in blocks.items():
        code = _strip_js(src)
        for o, c in ("{}", "()", "[]"):
            assert code.count(o) == code.count(c), (name, o, code.count(o), code.count(c))
        assert code.count("`") % 2 == 0, name


def test_strip_js_handles_regex_strings_and_nested_templates():
    src = "const a=`x ${y?`in ${z}`:'q'} {`;const r=/[{(]/g;const s=\"}\";const d=(1+2)/3;return x;"
    code = _strip_js(src)
    assert code.count("{") == code.count("}") and code.count("(") == code.count(")")
