"""The risk matrix must be visible on /constructions and /risk, and must auto-load.

A risk view that lives only behind a button on another page is a risk view nobody reads. These tests fail if
either page loses the matrix, if it stops auto-loading, if a required column disappears, or if an unavailable
window is allowed to render as blank or zero.
"""
from __future__ import annotations

import pytest

from test_p1_ui import _scripts


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    tmp = tmp_path_factory.mktemp("p0rmx")
    mp = pytest.MonkeyPatch()
    mp.setenv("MARKET_INTEL_NO_LLM", "1")
    mp.delenv("MARKET_INTEL_TOKEN", raising=False)
    mp.setattr(auth, "TOKEN_PATH", tmp / "token")
    index = EntityIndex([Company(ticker="MU", name="Micron", segment="Memory", aliases=[], weight=0.1, cik=723125)])
    c = TestClient(create_app(store=Store(tmp / "t.sqlite"), index=index), client=("127.0.0.1", 50000))
    yield c
    mp.undo()


@pytest.fixture(scope="module")
def base_js(app_client) -> str:
    r = app_client.get("/static/base.js")
    assert r.status_code == 200
    return r.text


# ----------------------------------------------------------------------------- the shared component
def test_component_renders_every_required_column(base_js):
    for col in ("VaR99 1D", "VaR99 10D", "ES97.5 1D", "ES97.5 10D"):
        assert col in base_js, f"the risk matrix is missing the {col} column"
    assert "TE (" in base_js, "tracking error column missing"
    assert "Obs 1D/10D" in base_js, "sample-size column missing"
    assert ">As-of<" in base_js, "as-of column missing"
    assert ">Status<" in base_js and ">Flags<" in base_js


def test_controls_are_explicitly_labelled(base_js):
    assert "rmx-lab" in base_js
    for lab in ("'Window'", "'Layer'", "'Benchmark'"):
        assert lab in base_js, f"control label {lab} missing"
    assert "RISK_WINDOWS=[['1y','1Y'],['3y','3Y']]" in base_js.replace(" ", "")
    for layer in ("model", "vol_target_scenario", "actual_run"):
        assert f"'{layer}'" in base_js
    for b in ("te_mandate", "te_proxy", "te_index"):
        assert f"'{b}'" in base_js


def test_an_unavailable_window_renders_unavailable_never_blank_or_zero(base_js):
    assert "unavailable</span></td>" in base_js, "a cell for an unsupported window must say so"
    assert "rmxCell(v,unavailable,reason)" in base_js.replace(" ", "") or "function rmxCell" in base_js
    # the limiting holding is named on the row
    assert "limiting_holding" in base_js
    assert "_reason" in base_js


def test_alias_and_equivalence_flags_are_preserved(base_js):
    assert "alias of " in base_js
    assert "layer_equivalent_to" in base_js and "layer_equivalent_basis" in base_js
    assert "hypothetical" in base_js, "a recalculated layer must be marked hypothetical"


def test_formal_mandate_absence_is_disclosed(base_js):
    assert "no formal mandate benchmark is defined" in base_js
    assert "a research proxy is not a substitute" in base_js


def test_loading_and_error_states_exist(base_js):
    assert "Loading risk by construction" in base_js
    assert "Could not load risk by construction" in base_js
    assert 'role="alert"' in base_js


def test_component_is_mobile_usable(app_client):
    css = app_client.get("/static/base.css").text
    assert "@media(max-width:700px){.rmx-ctl" in css.replace(" ", ""), "controls must stack on a narrow screen"
    assert ".rmx-ctl" in css


# ----------------------------------------------------------------------------- the two pages
def test_constructions_page_has_its_own_risk_matrix_that_auto_loads(app_client):
    h = app_client.get("/constructions").text
    assert 'id="riskmx-panel"' in h, "/constructions has no Risk matrix panel"
    assert 'id="riskmx"' in h
    assert "<h2>Risk matrix</h2>" in h
    body = "".join(_scripts(h))
    assert "riskMatrix(document.getElementById('riskmx')" in body, "the matrix must auto-load, not wait for a click"
    assert "btn" not in body.split("riskMatrix(")[1][:120], "no button gates the load"


def test_constructions_risk_matrix_is_separate_from_the_performance_matrix(app_client):
    h = app_client.get("/constructions").text
    assert h.index('id="riskmx-panel"') != h.index("Performance matrix")
    assert "<h2>Performance matrix</h2>" in h and "<h2>Risk matrix</h2>" in h


def test_risk_page_auto_loads_the_same_component(app_client):
    h = app_client.get("/risk").text
    assert 'id="riskmx"' in h
    body = "".join(_scripts(h))
    assert "riskMatrix(document.getElementById('riskmx')" in body
    assert "loadPC" not in body, "the manual load/refresh path is gone"
    assert "Load / refresh" not in h


def test_risk_page_detail_follows_the_selection(app_client):
    body = "".join(_scripts(app_client.get("/risk").text))
    assert "onpick:" in body and "onchange:" in body
    assert "PCSEL.layer" in body and "PCSEL.win" in body and "PCSEL.bench" in body
    assert "ES 97.5% · 10D" in body, "the detail must carry 10-day Expected Shortfall"
    assert "VaR 99% · 10D" in body and "VaR 99% · 1D" in body


def test_current_book_tracking_error_uses_the_selected_window(app_client):
    body = "".join(_scripts(app_client.get("/risk").text))
    compact = "".join(body.split())
    assert "(win.tracking_error||[]).filter" in compact
    assert "PCSEL.bench" in compact
    assert "(v.tracking_error||[]).map" not in compact


def test_current_book_ci_displays_the_backend_reason(app_client):
    body = "".join(_scripts(app_client.get("/risk").text))
    assert "u.reason||'not computed for this sample'" in body
    assert "'too few obs'" not in body


def test_risk_page_states_finality_from_acquisition(app_client):
    body = "".join(_scripts(app_client.get("/risk").text))
    assert "'Finality'" in body
    assert "confirmed through this close" in body
    assert "BEFORE the bell, so that session is not a confirmed close" in body


def test_risk_page_names_the_three_layers_correctly(app_client):
    h = app_client.get("/risk").text
    assert "common-vol-target scenario" in h.lower()
    assert "actual landed run book" in h.lower()
    assert "not a book any run executed" in h


# ----------------------------------------------------------------------------- API contract behind the UI
def test_api_serves_every_layer_with_the_fields_the_matrix_needs(app_client):
    r = app_client.get("/api/research/risk/constructions?reps=60")
    if r.status_code != 200 or not r.json().get("available"):
        pytest.skip("risk sweep unavailable")
    d = r.json()
    assert d["layers"] == ["model", "vol_target_scenario", "actual_run"]
    for lay in d["layers"]:
        rows = d[f"comparison_{lay}"]
        assert len(rows) == d["n_constructions"], f"{lay} must report every construction label"
        for row in rows:
            assert "layer_available" in row
            if not row["layer_available"]:
                assert row.get("layer_reason")
                continue
            for wk in ("1y", "3y"):
                assert f"{wk}_available" in row
                if row[f"{wk}_available"]:
                    for f in ("var99_1d", "var99_10d_overlap", "es975_1d", "es975_10d_overlap"):
                        assert f"{wk}_{f}" in row, f"{lay}/{wk} missing {f}"
                    assert f"{wk}_te_proxy_status" in row
                else:
                    assert row.get(f"{wk}_reason"), f"{lay}/{wk} unavailable without a reason"


def test_api_discloses_independent_portfolio_count(app_client):
    r = app_client.get("/api/research/risk/constructions?reps=60")
    if r.status_code != 200 or not r.json().get("available"):
        pytest.skip("risk sweep unavailable")
    d = r.json()
    ind = d["independence"]["model"]
    assert ind["n_labels"] == d["n_constructions"]
    assert ind["n_independent_portfolios"] <= ind["n_labels"]
    assert "resolve to" in ind["note"]


# ----------------------------------------------------------------------------- Sharpe comparability
def test_sharpe_never_appears_without_its_disclosure(base_js):
    assert "function sharpeCell" in base_js and "function sharpeTip" in base_js
    for term in ("estimator:", "formula:", "risk-free:", "horizon:", "sample:", "portfolio:", "objective:"):
        assert term in base_js, f"Sharpe disclosure is missing {term}"
    assert "risk-only: ignores expected return" in base_js


def test_constructions_page_uses_the_disclosing_sharpe_cell(app_client):
    h = app_client.get("/constructions").text
    body = "".join(_scripts(h))
    assert "sharpeCell(r)" in body
    assert "fmt.num(r.sharpe" not in body, "a bare Sharpe must not be printed"
    assert "not comparable with the ex-ante Sharpe" in h


def test_the_two_sharpe_definitions_are_declared_in_code():
    from ai_semi_intel.portfolio.optimizer import EX_ANTE_SHARPE_DISCLOSURE as EX
    from ai_semi_intel.portfolio.construction_eval import WALK_FORWARD_SHARPE_DISCLOSURE as WF
    assert EX["estimator"] == "ex-ante forecast"
    assert WF["estimator"] == "historical realised illustration"
    assert EX["formula"] != WF["formula"]
    for d in (EX, WF):
        assert "NOT comparable" in d["comparability"]
        assert "validate" in d["comparability"]


# ----------------------------------------------------------------------------- two-stage load + CI column
def test_matrix_renders_deterministic_figures_before_the_expensive_interval(base_js):
    """A matrix that blocks for a minute on resampling is a matrix nobody sees."""
    matrix = base_js[base_js.index("async function riskMatrix("):base_js.index("/* Sharpe with its disclosure")]
    selected = base_js[base_js.index("async function rmxFetchCI("):base_js.index("function riskMatrixControls(")]

    assert "fetch('/api/research/risk/constructions?reps=0')" in matrix, \
        "the only automatic all-construction fetch must use the deterministic tier"
    assert "reps=2000" not in matrix, \
        "riskMatrix must not automatically launch the expensive all-construction interval sweep"
    assert "${encodeURIComponent(name)}?reps=2000" in selected, \
        "the official interval must be requested from the selected-construction endpoint"
    assert "_RMXci" in selected and "state:'pending'" in selected
    assert "_RMXofficial" not in base_js, \
        "the removed all-construction official sweep must not return"


def test_ci_column_shows_confidence_reps_and_tier(base_js):
    assert "VaR99 10D CI" in base_js, "the 10-day VaR99 estimate-CI column is missing"
    assert "_ci_confidence" in base_js and "_ci_reps" in base_js and "_ci_block" in base_js
    assert "_ci_tier" in base_js
    assert "interval around the ESTIMATE (not the VaR confidence level)" in base_js
    assert "computing…" in base_js and "not computed" in base_js


def test_preview_interval_is_never_labelled_official(base_js):
    """The tier badge is read from the payload, not assumed."""
    assert "ciTier==='official'?'pass':'na'" in base_js.replace(" ", "")
    assert "deterministic tier" in base_js


def test_completed_but_unavailable_ci_has_no_dead_compute_button(base_js):
    content = base_js[base_js.index("function rmxCIContent("):base_js.index("function rmxPaintCI(")]
    fetcher = base_js[base_js.index("async function rmxFetchCI("):base_js.index("function riskMatrixControls(")]
    assert "st&&st.state==='unavailable'" in content
    assert "state:'unavailable'" in fetcher
    assert "done.state==='done'||done.state==='unavailable'" in "".join(fetcher.split())


def test_ci_request_state_is_latest_owner_wins(base_js):
    """An older abort/reply must never clear or overwrite a newer request for the same cell."""
    selected = base_js[base_js.index("async function rmxFetchCI("):base_js.index("function riskMatrixControls(")]
    compact = "".join(selected.split())
    assert "if(_RMXreq.get(key)!==ctrl)return;" in compact
    assert "_RMXreq.set(key,ctrl)" in compact
    assert "rmxNotify(id,r,view,curTag,onci)" in compact, \
        "pending, completion and abort cleanup must use the same matrix/detail notifier"


def test_benchmark_switch_cannot_strand_a_ci_request(base_js):
    """Changing the TE benchmark does not change a VaR CI and must not invalidate its repaint."""
    matrix = base_js[base_js.index("async function riskMatrix("):base_js.index("/* Sharpe with its disclosure")]
    compact = "".join(matrix.split())
    assert "_RMXgen" not in matrix, "a global generation gate strands CI replies after benchmark-only changes"
    assert "if(sel.layer!==prevLayer||sel.win!==prevWin)" in compact
    assert "constcurTag=()=>`${sel.layer}|${sel.win}`" in compact


def test_selected_ci_fails_closed_on_lineage_mismatch(base_js):
    """Never append an interval from a refreshed snapshot to older point estimates."""
    for field in ("data_snapshot_hash", "portfolio_weights_hash", "calculator_hash", "asof", "acquired_at"):
        assert field in base_js
    assert "_calc_sample_hash" in base_js
    assert "snapshot changed — reload" in base_js
    assert "rmxLineageMismatch" in base_js


def test_risk_detail_subscribes_to_every_ci_state(app_client):
    h = app_client.get("/risk").text
    body = "".join(_scripts(h))
    assert "rmxCIState(n,layer,wk)" in body
    assert "onci:" in body and "renderPCDetail()" in body
    assert "ci&&ci.state==='pending'" in body
    assert "ci&&ci.state==='stale'" in body


def test_unavailable_labels_are_not_counted_as_independent_portfolios(app_client):
    r = app_client.get("/api/research/risk/constructions?reps=0")
    if r.status_code != 200 or not r.json().get("available"):
        pytest.skip("risk sweep unavailable")
    ind = r.json()["independence"]["actual_run"]
    assert ind["n_unavailable_labels"] > 0, "only the run's own column has a landed book"
    assert ind["n_available_labels"] == ind["n_labels"] - ind["n_unavailable_labels"]
    assert ind["n_independent_portfolios"] == ind["n_available_labels"]
    assert ind["n_independent_portfolios"] < ind["n_labels"], \
        "labels with no book in this layer must not be counted as independent portfolios"
    assert "have no book in this layer" in ind["note"]


def test_deterministic_tier_reports_no_interval_rather_than_a_cheap_one(app_client):
    r = app_client.get("/api/research/risk/constructions?reps=0")
    if r.status_code != 200 or not r.json().get("available"):
        pytest.skip("risk sweep unavailable")
    d = r.json()
    assert d["precision_tier"] == "deterministic"
    assert "NOT computed" in d["precision_note"]
    for row in d["comparison_model"]:
        if row.get("1y_available"):
            assert row["1y_var99_1d"] is not None, "point estimates must still be exact"
            assert row["1y_var99_ci_lo"] is None, "no interval may be published in the deterministic tier"
            assert row["1y_ci_tier"] == "deterministic"
