"""Regression: the Scarcity and Risk pillars must not disappear from /universe again.

They are 15% and 10% of the composite. A scoring view that omits them shows a decision score whose two
smallest-but-real pillars are invisible, and there is no way to see why a name ranks where it does.
"""
from __future__ import annotations

import pytest

from test_p1_ui import _scripts, _strip_js


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    tmp = tmp_path_factory.mktemp("p0uni")
    mp = pytest.MonkeyPatch()
    mp.setenv("MARKET_INTEL_NO_LLM", "1")
    mp.delenv("MARKET_INTEL_TOKEN", raising=False)
    mp.setattr(auth, "TOKEN_PATH", tmp / "token")
    index = EntityIndex([Company(ticker="MU", name="Micron", segment="Memory", aliases=[], weight=0.1, cik=723125)])
    c = TestClient(create_app(store=Store(tmp / "t.sqlite"), index=index), client=("127.0.0.1", 50000))
    yield c
    mp.undo()


@pytest.fixture(scope="module")
def uni_html(app_client) -> str:
    r = app_client.get("/universe")
    assert r.status_code == 200
    return r.text


# ----------------------------------------------------------------------------- API
def test_api_universe_carries_both_pillars_and_their_coverage(app_client):
    r = app_client.get("/api/research/universe")
    if r.status_code != 200 or not r.json().get("available"):
        pytest.skip("no pipeline output")
    rows = r.json()["rows"]
    assert rows
    for field in ("score_scarcity", "score_risk", "scarcity_coverage", "risk_coverage"):
        present = [x for x in rows if field in x]
        assert len(present) == len(rows), f"{field} missing from the universe payload"
    assert any(x.get("score_scarcity") not in (None, 0) for x in rows), "scarcity is all null/zero"
    assert any(x.get("score_risk") not in (None, 0) for x in rows), "risk is all null/zero"


def test_composite_still_includes_the_configured_pillar_weights():
    """If the pillars are shown, the composite they feed must still be the one being shown."""
    from ai_semi_intel.config.settings import CONFIG
    s = CONFIG.screen
    assert s.w_scarcity > 0 and s.w_risk > 0
    total = s.w_growth + s.w_momentum + s.w_value + s.w_scarcity + s.w_risk
    assert abs(total - 1.0) < 1e-9, f"pillar weights sum to {total}"
    import inspect
    from ai_semi_intel.analysis import scoring
    src = inspect.getsource(scoring)
    assert "cfg.w_scarcity * d[\"score_scarcity\"]" in src
    assert "cfg.w_risk * d[\"score_risk\"]" in src


def test_risk_pillar_sign_is_resilience_higher_is_safer():
    """The pillar enters the composite with a POSITIVE weight, so a high score must mean safer."""
    import inspect
    from ai_semi_intel.analysis import scoring
    src = inspect.getsource(scoring)
    assert 'd["score_risk"] = d["score_risk_resilience"]' in src, \
        "score_risk is the resilience score; if that changes the UI label must change with it"


# ----------------------------------------------------------------------------- DOM
def test_universe_table_renders_both_pillar_columns(uni_html):
    h = uni_html
    assert 'data-k="score_scarcity"' in h, "Scarcity column header is missing from /universe"
    assert 'data-k="score_risk"' in h, "Risk column header is missing from /universe"
    assert ">Scarcity<" in h
    assert "Risk (resilience)" in h, "the Risk column must say it is a resilience score"
    # sortable: every pillar header carries a sort key, like its neighbours
    for k in ("score_growth", "score_momentum", "score_value", "score_quality", "score_scarcity", "score_risk"):
        assert f'data-k="{k}"' in h


def test_universe_row_template_emits_both_pillar_cells(uni_html):
    body = "".join(_scripts(uni_html))
    assert "r.score_scarcity" in body, "row template does not render scarcity"
    assert "r.score_risk" in body, "row template does not render risk"
    assert "pillarCell(r.score_scarcity" in body and "pillarCell(r.score_risk" in body


def test_missing_pillar_coverage_shows_explicit_unavailable_not_zero(uni_html):
    """A filled 0.0 with no coverage must render an unavailable state, never a trusted number."""
    body = "".join(_scripts(uni_html))
    assert "function pillarCell" in body
    assert "N/A" in body or "unavailable" in body.lower()
    assert "Number(cov)<=0" in body, "zero coverage must be detected"
    assert "is a fill, not a measurement" in body


def test_risk_column_label_states_the_direction(uni_html):
    assert "higher means SAFER" in uni_html or "higher = safer" in uni_html.lower()


def test_ticker_dossier_keeps_both_pillars():
    from market_intel.dossiers import PILLARS
    assert PILLARS["scarcity"] == "score_scarcity"
    assert PILLARS["risk"] == "score_risk"
