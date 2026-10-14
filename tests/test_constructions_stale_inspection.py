"""Regression coverage for inspectable stale construction comparisons.

The walk-forward artifact is expensive to regenerate.  A code/data/schema
mismatch must revoke decision use, but it must not erase the stored comparison
that tells the researcher what changed or leave the chart area blank.
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "market_intel" / "static" / "constructions.html"


def _legacy_walk_forward() -> dict:
    """Small v1-shaped artifact with every comparison series the UI promises."""
    common = {
        "cagr": 0.12,
        "vol": 0.20,
        "sharpe": 0.45,
        "sortino": 0.61,
        "max_drawdown": -0.18,
        "calmar": 0.67,
        "monthly_cvar95": -0.08,
        "worst_month": -0.07,
        "positive_months": 0.60,
        "beta_vs_bench": 0.9,
        "information_ratio": 0.2,
        "avg_turnover": 0.1,
        "cost_drag_pa": 0.002,
        "avg_effective_n": 8.0,
    }
    return {
        "available": True,
        "schema_version": "construction_eval.v1",
        "methods": ["equal_weight", "risk_parity"],
        "benchmark": "SOXX",
        "n_rebalances": 2,
        "window": ["2025-01-02", "2025-01-06"],
        "params": {
            "top_n": 15,
            "cov_years": 2,
            "use_bl_views": True,
            "max_w": 0.15,
            "cost_bps": 10,
            "rf": 0.03,
        },
        # v1 carried only the short price hash.  The absent full-history/code
        # hashes and old schema are exactly why this fixture is stale.
        "inputs": {
            "price_hash": "old-price-hash",
            "universe_hash": "old-universe-hash",
            "price_last_date": "2025-01-06",
        },
        "stats": {
            "equal_weight": {
                **common,
                "label": "Equal weight",
                "family": "risk-only",
                "needs_mu": False,
            },
            # v2's PIT gate can move level statistics into ``withheld``.  The
            # comparison page deliberately merges them back under a diagnostic
            # warning, so this also protects that display contract.
            "risk_parity": {
                "label": "Risk parity (ERC)",
                "family": "risk-only",
                "needs_mu": False,
                "avg_turnover": 0.09,
                "avg_effective_n": 9.0,
                "withheld": {**common, "sharpe": 0.55, "cagr": 0.13},
            },
            "SOXX": {
                **common,
                "label": "SOXX",
                "family": "benchmark",
                "needs_mu": False,
                "sharpe": 0.30,
            },
        },
        "versus": {
            "equal_weight": {"t_hac": 0.0},
            "risk_parity": {"t_hac": 1.2},
            "SOXX": {"t_hac": -0.4},
        },
        "cumulative": {
            "dates": ["2025-01-02", "2025-01-03", "2025-01-06"],
            "equal_weight": [1.0, 1.01, 1.02],
            "risk_parity": [1.0, 1.02, 1.03],
            "SOXX": [1.0, 1.005, 1.01],
        },
        "rolling_sharpe": {
            "dates": ["2025-01-02", "2025-01-03", "2025-01-06"],
            "equal_weight": [None, 0.4, 0.45],
            "risk_parity": [None, 0.5, 0.55],
            "SOXX": [None, 0.2, 0.3],
        },
        "decomposition": None,
        "pit_gate": {
            "alpha_claim_available": False,
            "headline": "Point-in-time evidence is incomplete",
            "missing": ["historical constituents"],
            "permitted_use": ["construction comparison"],
            "forbidden_use": ["alpha claim"],
        },
        "claim_status": "MECHANISM DIAGNOSTIC ONLY",
        "execution": {},
        "n_data_events": 0,
    }


def _patch_inputs(monkeypatch: pytest.MonkeyPatch, wf: dict) -> None:
    from market_intel import research

    idx = pd.bdate_range("2025-01-02", periods=3)
    px = pd.DataFrame({"AAA": np.array([100.0, 101.0, 102.0])}, index=idx)
    uni = pd.DataFrame({"market_cap": [1e10], "segment": ["compute"]}, index=["AAA"])
    monkeypatch.setattr(research, "_json", lambda _path: wf)
    monkeypatch.setattr(research, "universe", lambda: uni)
    monkeypatch.setattr(pd, "read_parquet", lambda _path: px)
    monkeypatch.setattr(research.config, "run_info", lambda: {"run_id": "fixture-run"})


def test_legacy_artifact_is_returned_but_explicitly_stale(monkeypatch):
    from market_intel import research

    wf = _legacy_walk_forward()
    _patch_inputs(monkeypatch, wf)
    payload = research.constructions_payload()

    assert payload["available"] is True
    assert payload["walk_forward"]["cumulative"] == wf["cumulative"]
    assert payload["walk_forward"]["rolling_sharpe"] == wf["rolling_sharpe"]
    assert payload["walk_forward"]["versus"] == wf["versus"]
    assert payload["stale"] is True
    assert payload["freshness_verified"] is True
    assert payload["artifact_scope"] == "HISTORICAL_STALE_COMPARISON"
    assert payload["decision_usable"] is False
    assert "schema" in payload["artifact_freshness"]["stale_reasons"]
    assert "STALE" in payload["reason"] and "shown only as a historical comparison" in payload["reason"]


def test_unverifiable_artifact_stays_visible_and_fails_closed(monkeypatch):
    from market_intel import research

    wf = _legacy_walk_forward()
    _patch_inputs(monkeypatch, wf)

    def cannot_read(_path):
        raise OSError("price store unavailable")

    monkeypatch.setattr(pd, "read_parquet", cannot_read)
    payload = research.constructions_payload()

    assert payload["walk_forward"] == wf
    assert payload["stale"] is False
    assert payload["freshness_verified"] is False
    assert payload["artifact_scope"] == "UNVERIFIED_HISTORICAL_COMPARISON"
    assert payload["decision_usable"] is False
    assert "price store unavailable" in payload["reason"]


@pytest.mark.parametrize("pit_open, expected_usable", [(False, False), (True, True)])
def test_currentness_and_pit_decision_gate_are_independent(monkeypatch, pit_open, expected_usable):
    from ai_semi_intel.portfolio import construction_eval as CE
    from market_intel import research

    wf = _legacy_walk_forward()
    wf["schema_version"] = CE.SCHEMA_VERSION
    wf["pit_gate"]["alpha_claim_available"] = pit_open
    _patch_inputs(monkeypatch, wf)
    monkeypatch.setattr(
        CE,
        "verify_artifact",
        lambda *_args: {"fresh": True, "stale_reasons": [], "checks": {}, "verdict": "current"},
    )

    payload = research.constructions_payload()

    assert payload["artifact_scope"] == "CURRENT"
    assert payload["freshness_verified"] is True
    assert payload["stale"] is False
    assert payload["decision_usable"] is expected_usable
    assert payload["reason"] is None, "artifact freshness has no error when the artifact is current"
    if pit_open:
        assert payload["decision_reason"] is None
    else:
        assert "mechanism diagnostic only" in payload["decision_reason"]


def _inline_script() -> str:
    html = PAGE.read_text()
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    return next(s for s in scripts if "async function load()" in s)


@pytest.mark.parametrize(
    "scope, stale, state_reason, historical",
    [
        ("HISTORICAL_STALE_COMPARISON", True, "this walk-forward artifact is STALE (schema)", True),
        ("CURRENT", False, None, False),
    ],
)
def test_non_decision_usable_payload_still_draws_all_core_comparison_views(
    tmp_path, scope, stale, state_reason, historical
):
    """Execute the real page script against a stale v1 payload in a tiny DOM.

    This catches the original regression directly: the stale warning must be
    visible at the same time as equity, drawdown, rolling Sharpe and significance
    plots.  A static source-marker test would not catch an early return or a
    runtime exception that blanks all four.
    """
    node = shutil.which("node")
    if not node:
        node = os.environ.get("MARKET_INTEL_NODE") or None
    if not node:
        pytest.skip("node is not on PATH")

    payload = {
        "available": True,
        "run": {"run_id": "fixture-run"},
        "walk_forward": _legacy_walk_forward(),
        "stale": stale,
        "freshness_verified": True,
        "artifact_scope": scope,
        "decision_usable": False,
        "decision_reason": "Point-in-time evidence is incomplete; current mechanism diagnostic only",
        "reason": state_reason,
        "production_primary": "risk_parity",
        "registry": {
            "equal_weight": {"label": "Equal weight", "family": "risk-only", "desc": "1/N", "needs_mu": False},
            "risk_parity": {"label": "Risk parity (ERC)", "family": "risk-only", "desc": "ERC", "needs_mu": False},
        },
    }
    page_script = _inline_script()
    harness = rf"""
const PAYLOAD={json.dumps(payload)};
const elements={{}};
function el(s){{
  if(!elements[s]) elements[s]={{innerHTML:'',textContent:'',onclick:null,options:[],
    value:s==='#dd-method'||s==='#c-method'?'risk_parity':'',dataset:{{}},
    classList:{{toggle(){{}}}},setAttribute(){{}},addEventListener(){{}}}};
  return elements[s];
}}
function $(s){{return el(s)}}
function $$(s){{return []}}
const plotIds=['p-eq','p-dd','p-roll','p-scatter','p-turn','p-t','p-years','p-consist','p-contrib','p-seg'];
const document={{
  getElementById(id){{return el('#'+id)}},
  querySelectorAll(s){{return s==='.plot'?plotIds.map(id=>el('#'+id)):[]}},
  addEventListener(){{}}
}};
function renderNav(){{}} function themeInit(){{}}
function pal(){{return {{gray:'#888',series:['#1','#2','#3','#4','#5','#6','#7','#8'],muted:'#777',pos:'#090',surface:'#fff',neg:'#900',mid:'#aaa',ink2:'#222',axis:'#333'}}}}
function esc(v){{return String(v==null?'':v)}}
// The served page loads i18n before its inline renderer; this focused harness
// uses the same English fallback while retaining the original evidence values.
function t(source,vars={{}}){{return String(source).replace(/\{{(\w+)\}}/g,(m,k)=>Object.prototype.hasOwnProperty.call(vars,k)?String(vars[k]??''):m)}}
const fmt={{pct(v){{return v==null?'':String(v)}},num(v){{return v==null?'':String(v)}}}};
function statusBanner(){{return '<div>run status</div>'}} function runChip(){{return '<span>run</span>'}}
function renderPeriodBar(){{}} function periodStartIndex(){{return 0}} function periodLabel(){{return 'all'}}
const plots={{}},tables={{}};
function plot(id,data){{plots[id]=(data||[]).length;el('#'+id).innerHTML='plot:'+plots[id]}}
function windowMetrics(dates,values){{
  if(!values||!values.length)return {{}};
  return {{rebased:values.map(v=>v/values[0]),drawdown:values.map(()=>0),total_return:0.02,cagr:0.12,
    vol:0.2,sharpe:0.45,sortino:0.6,max_drawdown:-0.1,calmar:1.2}};
}}
function sharpeCell(r){{return String(r.sharpe)}} function cls(){{return ''}}
function sortable(id,rows,render){{tables[id]=rows.length;el('#'+id).innerHTML=rows.map(render).join('')}}
async function j(url){{return url.includes('/status')?{{running:false}}:PAYLOAD}}
async function riskMatrix(){{return {{}}}}
{page_script}
setTimeout(()=>{{
  const blanked=Object.values(elements).some(e=>String(e.innerHTML).includes('no current walk-forward artifact'));
  console.log(JSON.stringify({{plots,tables,blanked,banner:el('#banner').innerHTML,hdr:el('#hdr').innerHTML,
    lead:el('#lead').innerHTML,flags:el('#flags').innerHTML,windowTable:el('#win tbody').innerHTML}}));
}},20);
"""
    script_path = tmp_path / "stale_constructions_harness.js"
    script_path.write_text(harness)
    proc = subprocess.run([node, str(script_path)], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    for plot_id in ("p-eq", "p-dd", "p-roll", "p-t"):
        assert result["plots"].get(plot_id, 0) > 0, f"stale artifact blanked {plot_id}"
    assert result["windowTable"].count("<tr") == 3
    assert result["tables"]["perf"] == 3
    assert result["blanked"] is False
    if historical:
        assert "HISTORICAL STALE COMPARISON" in result["banner"]
        assert "NOT DECISION-USABLE" in result["banner"]
        assert "Viewing the last stored historical artifact" in result["lead"]
        assert "Historical artifact" in result["flags"]
    else:
        assert "HISTORICAL" not in result["banner"]
        assert "Viewing the last stored historical artifact" not in result["lead"]
        assert "Historical artifact" not in result["flags"]
        assert "current diagnostic · not decision-usable" in result["hdr"]
        assert "MECHANISM DIAGNOSTIC ONLY" in result["flags"]


def test_only_a_missing_artifact_uses_the_blank_comparison_state():
    body = _inline_script()
    missing = body[body.index("if(!D.walk_forward){"):body.index("const W=D.walk_forward")]
    assert "no current walk-forward artifact" in missing
    assert "D.stale" not in missing and "decision_usable" not in missing
    assert "historical=D.artifact_scope?D.artifact_scope!=='CURRENT':D.stale===true" in body
    assert "historical=D.decision_usable" not in body
    for plot_id in ("p-eq", "p-dd", "p-roll", "p-t"):
        assert f"plot('{plot_id}'" in body
