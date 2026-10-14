import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.analysis import valuation, scoring, risk
from ai_semi_intel.portfolio import optimizer
from ai_semi_intel.config.settings import CONFIG


def _row(**kw):
    base = dict(price=100.0, shares=1e9, market_cap=1e11, total_debt=0.0, net_debt=-5e9, revenue_ntm=2e10,
                revenue_ttm=1.6e10, rev_growth_ntm=0.25, rev_growth_fy1=0.20, fcf_margin=0.30, op_margin=0.35,
                fcf_margin_5y=0.25, rev_cagr_3y=0.15, beta=1.2, segment="AI Compute Silicon")
    base.update(kw)
    return pd.Series(base)


def test_dcf_monotonic_in_growth():
    r = _row()
    lo = valuation.dcf_value(r, 0.045, g1_override=0.05, hold_margin=True)["dcf_fv"]
    hi = valuation.dcf_value(r, 0.045, g1_override=0.30, hold_margin=True)["dcf_fv"]
    assert hi > lo > 0


def test_reverse_dcf_recovers_price():
    r = _row()
    g = valuation.implied_growth(r, 0.045)
    fv = valuation.dcf_value(r, 0.045, g1_override=g, hold_margin=True)["dcf_fv"]
    assert abs(fv / r["price"] - 1) < 0.02


def test_wacc_bounds():
    cfg = CONFIG.valuation
    w = valuation.wacc_for(_row(beta=5.0), 0.045, cfg)
    assert cfg.wacc_floor <= w <= cfg.wacc_cap


def test_select_top_respects_segment_cap():
    n = 60
    segs = ["Memory & Storage", "AI Compute Silicon", "Semiconductor Equipment", "AI Software & Applications", "Power & Energy for AI", "Cloud, Hyperscale & Neocloud"]
    df = pd.DataFrame({"composite": np.linspace(2, -1, n), "segment": [segs[i % 6] for i in range(n)],
                       "eligible": True, "score_value": 0.0, "score_growth": 0.0}, index=[f"T{i}" for i in range(n)])
    top = scoring.select_top(df)
    assert len(top) == CONFIG.screen.top_n
    assert top["segment"].value_counts().max() <= CONFIG.screen.max_per_segment


def test_optimiser_constraints():
    rng = np.random.default_rng(0)
    tick = [f"A{i}" for i in range(15)]
    R = pd.DataFrame(rng.normal(0.0005, 0.02, (600, 15)), columns=tick)
    cov = optimizer.shrunk_cov(R)
    mu = pd.Series(rng.uniform(0.05, 0.25, 15), index=tick)
    segs = pd.Series(["S1"] * 5 + ["S2"] * 5 + ["S3"] * 5, index=tick)
    out = optimizer.optimise(mu, cov, 0.04, segs)
    w = out["max_sharpe"]["weights"]
    assert abs(w.sum() - 1) < 1e-6
    assert (w >= CONFIG.portfolio.min_weight - 1e-6).all() and (w <= CONFIG.portfolio.max_weight + 1e-6).all()
    assert w.groupby(segs).sum().max() <= CONFIG.portfolio.max_segment_weight + 1e-6


def test_risk_resilience_penalises_fragility():
    n = 20
    x = np.linspace(0, 1, n)
    d = pd.DataFrame({
        "vol_1y": 0.20 + x, "max_dd_1y": -0.10 - 0.60 * x, "beta": 0.7 + 2 * x,
        "net_debt_to_ebitda": -1 + 6 * x, "fcf_margin": 0.35 - 0.5 * x,
        "roic": 0.35 - 0.4 * x, "op_margin_5y": 0.30 - 0.4 * x, "sbc_pct": 0.01 + 0.25 * x,
        "ev_sales_ntm": 2 + 25 * x, "pe_ntm": 10 + 60 * x, "dcf_tv_share": 0.45 + 0.45 * x,
        "upside_dispersion": 0.05 + 0.8 * x,
    }, index=[f"T{i}" for i in range(n)])
    out = risk.score_risk(d)
    assert out.loc["T0", "score_risk_resilience"] > out.loc[f"T{n-1}", "score_risk_resilience"]
    assert out.loc["T0", "risk_flag_count"] == 0
    assert out.loc[f"T{n-1}", "risk_flag_count"] >= 5


def test_scarcity_is_confidence_shrunk_and_missing_is_neutral():
    rng = np.random.default_rng(4)
    n = 20
    fields = [f for feats in scoring.PILLARS.values() for f in feats]
    d = pd.DataFrame({f: rng.normal(size=n) for f in fields}, index=[f"T{i}" for i in range(n)])
    for f in ["gross_margin", "op_margin", "fcf_margin", "roic", "op_margin_5y"]:
        d[f] = 0.2
    d["net_debt_to_ebitda"] = 1.0; d["sbc_pct"] = 0.05
    d["vol_1y"] = 0.4; d["max_dd_1y"] = -0.25; d["beta"] = 1.2
    d["ev_sales_ntm"] = 5.0; d["pe_ntm"] = 25.0; d["dcf_tv_share"] = 0.7; d["upside_dispersion"] = 0.2
    d["market_cap"] = 5e9; d["price"] = 100.0; d["upside"] = 0.2
    d["segment"] = [f"S{i % 5}" for i in range(n)]
    d["scarcity_raw"] = np.nan; d["strategic_confidence"] = np.nan; d["strategic_stale"] = True
    d.loc["T0", ["scarcity_raw", "strategic_confidence", "strategic_stale"]] = [1.0, 0.5, False]
    out = scoring.score_universe(d)
    assert np.isclose(out.loc["T0", "score_scarcity"], 1.0)
    assert out.loc["T1", "score_scarcity"] == 0.0
    assert out.loc["T1", "scarcity_coverage"] == 0.0
    expected = (CONFIG.screen.w_growth * out.loc["T0", "score_growth"]
                + CONFIG.screen.w_momentum * out.loc["T0", "score_momentum"]
                + CONFIG.screen.w_value * out.loc["T0", "score_value"]
                + CONFIG.screen.w_scarcity * out.loc["T0", "score_scarcity"]
                + CONFIG.screen.w_risk * out.loc["T0", "score_risk"])
    assert np.isclose(out.loc["T0", "composite"], expected)


def test_return_builder_never_proxy_fills_missing_prices():
    dates = pd.bdate_range("2024-01-01", periods=260)
    px = pd.DataFrame({"A": 100 * np.cumprod(np.full(260, 1.001)),
                       "B": 100 * np.cumprod(np.full(260, 1.002))}, index=dates)
    px.loc[dates[100], "B"] = np.nan
    cfg = type(CONFIG.portfolio)(min_history_days=200, lookback_years=3)
    returns, info = optimizer.build_returns(px, ["A", "B"], pd.Series({"A": "S", "B": "S"}), cfg)
    assert dates[100] not in returns.index and dates[101] not in returns.index
    assert info["backfilled_days"] == {}
    assert info["method"] == "common_observed_window"


def test_backtest_is_labelled_hindsight_with_standard_sharpe_and_aligned_benchmarks():
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2025-01-01", periods=300)
    px = pd.DataFrame({t: 100 * np.cumprod(1 + rng.normal(0.0004, 0.02, 300)) for t in ("A", "B", "C", "SPY", "SOXX")}, index=dates)
    px.loc[dates[150], "B"] = np.nan                                  # one missing price inside the window
    cfg = type(CONFIG.portfolio)(min_history_days=100, lookback_years=3)
    R, info = optimizer.build_returns(px, ["A", "B", "C"], pd.Series({"A": "S", "B": "S", "C": "S"}), cfg)
    assert info["dropped_dates"] == [str(dates[150].date()), str(dates[151].date())] and info["missing_by_ticker"] == {"B": 2}
    w = pd.Series({"A": 0.2, "B": 0.2, "C": 0.1})                    # 50% cash
    bt = optimizer.backtest(R, w, px, ("SPY", "SOXX"), rf=0.04)
    cum, daily = bt["cumulative"], bt["daily"]
    assert "INVALID AS TRACK RECORD" in bt["meta"]["label"] and bt["meta"]["status"] == "hindsight_illustration"
    assert dates[150] not in cum.index and dates[151] not in cum.index
    d0 = R.index[0]
    assert np.isclose(daily.loc[d0, "SPY"], px["SPY"].pct_change().loc[d0])                     # day one included for benchmarks
    d_after = R.index[R.index.get_loc(dates[149]) + 1]
    assert d_after == dates[152] and np.isclose(daily.loc[d_after, "SPY"], px["SPY"].pct_change().loc[d_after])   # single-day return after the gap
    ex = daily["Portfolio"] - 0.04 / 252
    st = bt["stats"].loc["Portfolio"]
    assert np.isclose(st["sharpe"], ex.mean() / ex.std() * np.sqrt(252)) and np.isclose(st["cagr_excess_over_vol"], (st["cagr"] - 0.04) / st["vol"])
    assert "sharpe_definition" in bt["meta"] and "no commissions" in bt["meta"]["costs"] and "constant 4.00%" in bt["meta"]["cash_rate"]
    path = pd.Series(0.08, index=dates, name="FEDFUNDS")
    bt2 = optimizer.backtest(R, w, px, ("SPY",), rf=0.04, cash_rate=path)
    assert bt2["daily"]["Portfolio"].sum() > daily["Portfolio"].sum() and "FEDFUNDS" in bt2["meta"]["cash_rate"]


def test_fred_and_fx_fail_closed(monkeypatch, tmp_path):
    import json, os, time
    from ai_semi_intel import runs
    from ai_semi_intel.data import fred, yahoo
    from ai_semi_intel.analysis import fundamentals
    fred_dir = tmp_path / "fred"; fred_dir.mkdir()
    monkeypatch.setattr(fred, "FRED_DIR", fred_dir); monkeypatch.setattr(fred, "STATUS_PATH", fred_dir / "_status.json"); monkeypatch.setattr(fred, "load_fred_key", lambda: "k")
    p = fred_dir / "DGS10.csv"
    pd.Series([4.0, 4.1], index=pd.to_datetime(["2026-08-01", "2026-08-02"]), name="DGS10").to_csv(p)
    old = time.time() - 3 * 86400; os.utime(p, (old, old))                          # cache older than the max age -> a fetch is attempted

    def offline(*a, **k):
        raise ConnectionError("offline")
    monkeypatch.setattr(fred.requests, "get", offline)
    s = fred.fetch_series("DGS10")
    assert s.attrs["status"] == "stale" and "fetch failed" in s.attrs["stale_reason"] and len(s) == 2
    st = json.load(open(fred_dir / "_status.json")); assert st["DGS10"]["ok"] is False and st["DGS10"]["served_from_cache"] is True
    s2 = fred.fetch_series("DGS2")
    assert s2.empty and s2.attrs["status"] == "unavailable"
    with pytest.raises(fred.DataUnavailable):
        fred.risk_free_rate({"DGS10": s2})
    assert np.isclose(fred.risk_free_rate({"DGS10": s}), 0.041)
    m = runs.data_manifest(None, [], {"DGS10": s, "DGS2": s2}, data_cache=tmp_path)
    assert m["entries"]["fred:DGS10"]["status"] == "stale" and "fetch failed" in m["entries"]["fred:DGS10"]["stale_reason"]
    assert m["entries"]["fred:DGS2"]["status"] == "unavailable" and "fred:DGS2" in m["stale"]
    assert runs.blocking_reasons(m) and "prices" in runs.blocking_reasons(m)[0]      # no prices at all blocks; stale DGS10 does not
    m_ok = {"entries": {"prices": {"status": "stale"}, "fred:DGS10": {"status": "stale"}}}
    assert runs.blocking_reasons(m_ok) == []
    # FX: a failed fetch is None, never 1.0, and the name is marked missing
    monkeypatch.setattr(yahoo, "PRICE_DIR", tmp_path / "prices"); (tmp_path / "prices").mkdir(); monkeypatch.setattr(yahoo, "FX_STATUS", tmp_path / "prices" / "fx_status.json")

    class Boom:
        def __init__(self, *a, **k): pass
        def history(self, **k): raise RuntimeError("no fx")
    monkeypatch.setattr(yahoo.yf, "Ticker", Boom)
    assert yahoo.fetch_fx("TWD") is None and yahoo.fetch_fx("USD") == 1.0
    monkeypatch.setattr(fundamentals, "fetch_fx", lambda ccy: None)
    cv, missing = fundamentals.fx_conversion("TWD")
    assert missing and np.isnan(cv) and fundamentals.fx_conversion("USD") == (1.0, False)
    m2 = runs.data_manifest(None, [], {}, data_cache=tmp_path)
    assert m2["entries"]["fx"]["status"] == "unavailable" and "TWD" in m2["entries"]["fx"]["failed_pairs"]
    b = runs.write_blocked(["prices unavailable"], "data", latest=tmp_path)
    assert json.load(open(b))["reasons"] == ["prices unavailable"]
    runs.clear_blocked(latest=tmp_path); assert not b.exists()


def test_fx_missing_names_are_not_eligible():
    rng = np.random.default_rng(7)
    n = 20
    fields = [f for feats in scoring.PILLARS.values() for f in feats]
    d = pd.DataFrame({f: rng.normal(size=n) for f in fields}, index=[f"T{i}" for i in range(n)])
    for f in ["gross_margin", "op_margin", "fcf_margin", "roic", "op_margin_5y"]:
        d[f] = 0.2
    d["net_debt_to_ebitda"] = 1.0; d["sbc_pct"] = 0.05; d["vol_1y"] = 0.4; d["max_dd_1y"] = -0.25; d["beta"] = 1.2
    d["ev_sales_ntm"] = 5.0; d["pe_ntm"] = 25.0; d["dcf_tv_share"] = 0.7; d["upside_dispersion"] = 0.2
    d["market_cap"] = 5e9; d["price"] = 100.0; d["upside"] = 0.2; d["segment"] = [f"S{i % 5}" for i in range(n)]
    d["fx_missing"] = False; d.loc["T3", "fx_missing"] = True
    out = scoring.score_universe(d)
    assert not out.loc["T3", "eligible"] and out.loc["T4", "eligible"] and "fx_unavailable" in str(out.loc["T3", "risk_flags"])
