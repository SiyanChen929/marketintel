"""Research layer: exposes the daily stock-selection outputs (ai_semi_intel) to the web app, runs custom portfolio
optimisations on the cached price panel, assembles per-ticker detail and can trigger a pipeline run."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import config

LATEST, HISTORY = config.LATEST, config.OUTPUTS / "history"
CHARTS, VALID, DECK = config.OUTPUTS / "charts", config.OUTPUTS / "validation", config.OUTPUTS / "deck"
PRICES = config.PROJECT_ROOT / "data_cache" / "prices" / "prices_universe.parquet"
BENCHMARKS = ("SPY", "QQQ", "SOXX")
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


# ----------------------------------------------------------------------------- cached file loaders
def _cached(path: Path, loader):
    path = config.run_file(path)
    key = str(path)
    try:
        m = path.stat().st_mtime
    except FileNotFoundError:
        return None
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == m:
            return hit[1]
    val = loader(path)
    with _lock:
        _cache[key] = (m, val)
    return val


def _csv(path: Path, index_col=0, **kw) -> pd.DataFrame | None:
    return _cached(path, lambda p: pd.read_csv(p, index_col=index_col, **kw))


def _json(path: Path):
    return _cached(path, lambda p: json.loads(p.read_text()))


def clean(o: Any) -> Any:
    """Recursively convert pandas/numpy values into JSON-safe python (NaN -> None)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return [clean(r) for r in o.reset_index().to_dict(orient="records")]
    if isinstance(o, pd.Series):
        return clean(o.to_dict())
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) or np.isinf(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if o is pd.NaT:
        return None
    return o


def available() -> bool:
    return (LATEST / "summary.json").exists()


def summary() -> dict:
    return _json(LATEST / "summary.json") or {}


def universe() -> pd.DataFrame:
    df = _csv(LATEST / "universe_scores.csv")
    return df if df is not None else pd.DataFrame()


def top() -> pd.DataFrame:
    df = _csv(LATEST / "top15.csv")
    return df if df is not None else pd.DataFrame()


def price_panel() -> pd.DataFrame:
    df = _cached(PRICES, lambda p: pd.read_parquet(p))
    if df is None:
        df = _csv(LATEST / "prices_top15.csv", parse_dates=True)
    return df if df is not None else pd.DataFrame()


def overlay() -> pd.DataFrame:
    df = _csv(LATEST / "event_overlay.csv", index_col=None)
    return df.set_index("ticker") if df is not None and "ticker" in df.columns else pd.DataFrame()


HOLDING_COLS = ["name", "segment", "sub_segment", "style", "rank", "price", "fair_value", "upside", "dcf_upside", "mult_upside", "target_upside", "composite",
                "alpha_score", "score_growth", "score_momentum", "score_value", "score_quality", "score_scarcity", "score_risk",
                "scarcity_coverage", "risk_coverage", "market_cap", "ev_sales_ntm",
                "pe_ntm", "rev_growth_ntm", "rev_growth_q_yoy", "gross_margin", "fcf_margin", "eps_rev_30d", "eps_rev_90d", "ret_1m", "ret_3m", "vs_200dma",
                "next_earnings", "risk_flags", "strategic_pillar", "beta"]


def _with_events(df: pd.DataFrame) -> pd.DataFrame:
    ov = overlay()
    if not ov.empty:
        df = df.join(ov[["event_score_7d", "n_events_7d", "alert_level", "action"]].rename(columns={"alert_level": "event_level", "action": "event_action"}), how="left")
    return df


# ----------------------------------------------------------------------------- payloads
def portfolio_payload() -> dict:
    if not available():
        return {"available": False}
    s = summary()
    w = _csv(LATEST / "portfolio_weights.csv")
    kpi = _csv(LATEST / "kpi_table.csv")
    tp = top()
    rc = _csv(LATEST / "risk_contributions.csv")
    ps = _csv(LATEST / "portfolio_stats.csv")
    fr = _csv(LATEST / "frontier.csv", index_col=None)
    ast = _csv(LATEST / "asset_stats.csv")
    corr = _csv(LATEST / "correlation.csv")
    cum = _csv(LATEST / "backtest_cumulative.csv", parse_dates=True)
    dd = _csv(LATEST / "backtest_drawdown.csv", parse_dates=True)
    bts = _csv(LATEST / "backtest_stats.csv")
    ccum = _csv(LATEST / "construction_backtest_cumulative.csv", parse_dates=True)
    cdd = _csv(LATEST / "construction_backtest_drawdown.csv", parse_dates=True)
    cstats = _csv(LATEST / "construction_backtest_stats.csv")
    hold = tp.copy()
    run = config.run_info()
    cash = float(s.get("recommended_cash") or 0.0)
    base = s.get("config", {}).get("portfolio", {}).get("primary", "risk_parity")
    # canonical: executable target weights (recommended construction scaled to the vol target); model = fully invested construction
    if w is not None and "recommended" in w.columns:
        hold["weight"] = w["recommended"].reindex(hold.index)
    elif kpi is not None and "weight" in kpi.columns:
        hold["weight"] = kpi["weight"].reindex(hold.index)
    if w is not None and base in w.columns:
        hold["weight_model"] = w[base].reindex(hold.index)
    else:
        hold["weight_model"] = hold["weight"] / (1.0 - cash) if cash < 1 else hold["weight"]
    hold["weight_rec"] = hold["weight"]                                   # backward-compatible alias
    if w is not None and "max_sharpe" in w.columns:
        hold["weight_max_sharpe"] = w["max_sharpe"].reindex(hold.index)
    hold = _with_events(hold)
    cols = ["weight", "weight_model", "weight_rec"] + [c for c in ("weight_max_sharpe",) if c in hold.columns] + [c for c in HOLDING_COLS if c in hold.columns] + [c for c in ("event_score_7d", "n_events_7d", "event_level", "event_action") if c in hold.columns]
    from ai_semi_intel.portfolio import constructions as C
    registry = {k: {kk: v[kk] for kk in ("label", "family", "desc", "needs_mu")} for k, v in C.REGISTRY.items()}
    registry["recommended"] = {"label": "Executable target (ERC x volatility target)", "family": "canonical", "desc": "Production ERC weights scaled to the ex-ante volatility target; remainder held in cash.", "needs_mu": False}
    construction_backtest = None
    if ccum is not None:
        construction_backtest = {"dates": [d.strftime("%Y-%m-%d") for d in ccum.index],
                                 "cumulative": {c: ccum[c].tolist() for c in ccum.columns},
                                 "drawdown": {c: cdd[c].tolist() for c in cdd.columns} if cdd is not None else {},
                                 "stats": cstats.to_dict(orient="index") if cstats is not None else {}}
    return clean({"available": True, "asof": s.get("asof"), "generated_at": s.get("generated_at"), "risk_free": s.get("risk_free"), "run": run,
                  "validation": run.get("validation") or s.get("validation") or {}, "reconciles": abs(float(hold["weight"].fillna(0).sum()) + cash - 1.0) < 1e-6,
                  "stats": ps.to_dict(orient="index") if ps is not None else s.get("portfolio", {}), "recommended_cash": s.get("recommended_cash"), "backtest_meta": s.get("backtest_meta", {}), "returns_info": {k: v for k, v in s.get("returns_info", {}).items() if k != "backfilled_days"},
                  "config": s.get("config", {}).get("portfolio", {}), "weights": {m: w[m].to_dict() for m in w.columns} if w is not None else {},
                  "risk_contrib": {m: rc[m].to_dict() for m in rc.columns} if rc is not None else {}, "frontier": fr.to_dict(orient="records") if fr is not None else [],
                  "asset_stats": ast.to_dict(orient="index") if ast is not None else {},
                  "corr": {"tickers": list(corr.index), "matrix": corr.values.tolist()} if corr is not None else None,
                  "backtest": {"dates": [d.strftime("%Y-%m-%d") for d in cum.index], "cumulative": {c: cum[c].tolist() for c in cum.columns},
                               "drawdown": {c: dd[c].tolist() for c in dd.columns} if dd is not None else {}, "stats": bts.to_dict(orient="index") if bts is not None else {}} if cum is not None else None,
                  "construction_backtest": construction_backtest, "registry": registry,
                  "holdings": hold[cols].reset_index().rename(columns={"index": "ticker"}).to_dict(orient="records"), "changes": s.get("changes", {}), "alerts": s.get("alerts", [])})


UNIVERSE_COLS = ["name", "segment", "sub_segment", "style", "rank_all", "rank_eligible", "eligible", "composite", "alpha_score", "score_growth", "score_momentum",
                 "score_value", "score_quality", "score_scarcity", "score_risk", "scarcity_coverage", "risk_coverage",
                 "upside", "dcf_upside", "mult_upside", "target_upside", "price", "market_cap",
                 "ev_sales_ntm", "pe_ntm", "rev_growth_ntm", "rev_growth_q_yoy", "gross_margin", "op_margin", "fcf_margin", "roic", "net_debt_to_ebitda", "eps_rev_30d",
                 "eps_rev_90d", "ret_1m", "ret_3m", "ret_12m", "vs_200dma", "next_earnings", "risk_flags", "risk_capped", "strategic_score", "strategic_pillar", "position", "is_adr"]


def universe_payload() -> dict:
    if not available():
        return {"available": False}
    u = _with_events(universe().copy())
    hold, model = config.holdings(), config.holdings_model()
    u["is_holding"] = u.index.isin(list(hold))
    u["weight"] = [hold.get(t, 0.0) for t in u.index]
    u["weight_model"] = [model.get(t, 0.0) for t in u.index]
    cols = [c for c in UNIVERSE_COLS if c in u.columns] + ["is_holding", "weight", "weight_model"] + [c for c in ("event_score_7d", "n_events_7d", "event_level") if c in u.columns]
    seg = _csv(LATEST / "segment_table.csv")
    s = summary()
    return clean({"available": True, "asof": s.get("asof"), "run": config.run_info(), "rows": u[cols].reset_index().rename(columns={"index": "ticker"}).to_dict(orient="records"),
                  "funnel": s.get("funnel", []), "segments": seg.reset_index().to_dict(orient="records") if seg is not None else [],
                  "weights": s.get("config", {}).get("screen", {})})


def ticker_payload(t: str, store=None) -> dict:
    t = t.upper()
    u = universe()
    row = clean(u.loc[t].to_dict()) if t in u.index else {}
    hold, model = config.holdings(), config.holdings_model()
    out = {"ticker": t, "row": row, "in_universe": t in u.index, "run": config.run_info(),
           "holding": {"weight": hold.get(t), "model_weight": model.get(t), "rank": row.get("rank_eligible")} if t in hold else None}
    px = price_panel()
    if not px.empty and t in px.columns:
        p = px[[c for c in (t, "SOXX", "SPY") if c in px.columns]].dropna(subset=[t]).tail(504)
        base = p.iloc[0]
        rel = (p[t] / base[t]) / (p["SOXX"] / base["SOXX"]) * 100 if "SOXX" in p.columns else None
        out["prices"] = {"dates": [d.strftime("%Y-%m-%d") for d in p.index], "close": p[t].round(3).tolist(),
                         "soxx_index": (p["SOXX"] / base["SOXX"] * 100).round(2).tolist() if "SOXX" in p.columns else None,
                         "stock_index": (p[t] / base[t] * 100).round(2).tolist(), "rel_soxx": rel.round(2).tolist() if rel is not None else None}
    tear = CHARTS / f"tearsheet_{t}.png"
    out["tearsheet"] = f"/charts/tearsheet_{t}.png" if tear.exists() else None
    st = _csv(LATEST / "strategic_table.csv")
    if st is not None and t in st.index:
        out["strategic"] = clean(st.loc[t].to_dict())
    ov = overlay()
    out["event_score"] = clean(ov.loc[t].to_dict()) if t in ov.index else None
    if store is not None:
        since = datetime.now(timezone.utc) - timedelta(days=30)
        evs = store.events(ticker=t, since=since, relevant_only=True, limit=40)
        out["events"] = [{k: e.get(k) for k in ("id", "published", "source_name", "event_type", "direction", "materiality", "alert_level", "one_liner", "title", "url", "tickers")} for e in evs]
    runs = _csv(HISTORY / "runs.csv", index_col=None)
    if runs is not None:
        r = runs[runs["ticker"] == t]
        out["history"] = clean(r.to_dict(orient="records"))
    return clean(out)


def macro_payload() -> dict:
    snap = _csv(LATEST / "macro_snapshot.csv")
    panel = _json(LATEST / "macro_panel.json") or {}
    labels = {}
    try:
        from ai_semi_intel.config.settings import FRED_SERIES  # type: ignore
        labels = dict(FRED_SERIES)
    except Exception:
        pass
    labels.update({"CPI_YOY": "CPI YoY (%)", "COREPCE_YOY": "Core PCE YoY (%)", "SEMI_IP_YOY": "Semiconductor IP YoY (%)", "SEMI_PPI_YOY": "Semiconductor PPI YoY (%)",
                   "INDPRO_YOY": "Industrial production YoY (%)", "CAPGOODS_YOY": "Core capital goods orders YoY (%)", "GDP_YOY": "Real GDP YoY (%)"})
    series = {}
    for k, s in panel.items():
        items = sorted(s.items())
        series[k] = {"dates": [d for d, _ in items], "values": [v for _, v in items], "label": labels.get(k, k)}
    manifest = _json(LATEST / "data_manifest.json") or {}
    return clean({"available": bool(panel), "run": config.run_info(), "snapshot": snap.reset_index().to_dict(orient="records") if snap is not None else [], "series": series, "labels": labels,
                  "manifest": {k: {kk: v.get(kk) for kk in ("status", "effective_at", "fetched_at", "stale_reason", "unit", "vendor", "frequency", "finality")} for k, v in (manifest.get("entries") or {}).items()},
                  "manifest_generated_at": manifest.get("generated_at")})


def history_payload() -> dict:
    runs = _csv(HISTORY / "runs.csv", index_col=None)
    ph = _csv(HISTORY / "portfolio_history.csv", index_col=None)
    index = _json(HISTORY / "runs" / "index.json") or []
    try:
        from ai_semi_intel.runs import latest_per_date  # type: ignore
        if runs is not None:
            runs = latest_per_date(runs, index)
        if ph is not None:
            ph = latest_per_date(ph, index)
    except Exception:
        pass
    return clean({"runs": runs.to_dict(orient="records") if runs is not None else [], "portfolio": ph.to_dict(orient="records") if ph is not None else [],
                  "index": index, "run": config.run_info()})


def validation_payload() -> dict:
    s = _json(VALID / "summary.json") or {}
    ic = _csv(VALID / "ic_table.csv", index_col=None)
    fic = _csv(VALID / "feature_ic.csv", index_col=None)
    charts = sorted(p.name for p in (VALID / "charts").glob("*.png")) if (VALID / "charts").exists() else []
    keep = {k: s.get(k) for k in ("generated_at", "panel", "backtest_stats", "permutation", "random_weights", "walk_forward", "interpretation", "regime", "leave_one_out", "avg_monthly_turnover_top15", "ic_by_year") if k in s}
    report = (VALID / "report.md").read_text()[:20000] if (VALID / "report.md").exists() else ""
    return clean({"available": bool(s) or ic is not None, "summary": keep, "ic_table": ic.to_dict(orient="records") if ic is not None else [],
                  "feature_ic": fic.to_dict(orient="records") if fic is not None else [], "charts": [f"/validation-charts/{c}" for c in charts],
                  "live": _json(LATEST / "live_validation.json") or {}, "report": report})


def strategic_payload() -> dict:
    st = _csv(LATEST / "strategic_table.csv")
    if st is None:
        return {"available": False, "rows": []}
    cols = [c for c in ("name", "segment", "strategic_score", "scarcity_strength", "risk_score", "llm_risk", "financial_risk", "strategic_pillar", "news_direction",
                        "strategic_confidence", "strategic_asof", "strategic_stale", "risk_capped", "risk_gated_out", "risk_flags", "strategic_thesis", "kill_criteria", "watch_items", "composite", "rank_all") if c in st.columns]
    refresh = _json(LATEST / "strategic_refresh.json") or {}
    return clean({"available": True, "rows": st[cols].reset_index().rename(columns={"index": "ticker"}).to_dict(orient="records"), "refresh": refresh})


def files_payload() -> dict:
    deck = sorted(DECK.glob("*.pptx")) if DECK.exists() else []
    deck = [p for p in deck if not p.name.startswith("~$")]
    charts = sorted(p.name for p in CHARTS.glob("*.png")) if CHARTS.exists() else []
    return {"deck": deck[0].name if deck else None, "deck_size_mb": round(deck[0].stat().st_size / 1e6, 1) if deck else None,
            "static_dashboard": (config.OUTPUTS / "dashboard" / "index.html").exists(), "charts": [f"/charts/{c}" for c in charts]}


# ----------------------------------------------------------------------------- custom optimisation
OPT_FIELDS = ("lookback_years", "min_weight", "max_weight", "max_segment_weight", "vol_target", "rebalance", "primary", "grinold_ic", "min_history_days")
_opt_cache: dict[str, tuple[float, dict]] = {}


def optimize(params: dict) -> dict:
    """Run the pipeline's optimiser (Ledoit-Wolf, Black-Litterman prior, Grinold alphas, constrained solves, backtest)
    on an arbitrary subset of the universe with optional constraint overrides."""
    from ai_semi_intel.config.settings import CONFIG  # type: ignore
    from ai_semi_intel.portfolio import constructions as C, optimizer  # type: ignore
    t0 = time.time()
    u, px = universe(), price_panel()
    if u.empty or px.empty:
        return {"error": "pipeline outputs or price cache not available"}
    req = [str(t).upper().strip() for t in params.get("tickers", []) if str(t).strip()]
    if not req:
        req = list(config.holdings())
    missing = [t for t in req if t not in u.index or t not in px.columns]
    tickers = [t for t in req if t in u.index and t in px.columns]
    if len(tickers) < 2:
        return {"error": "need at least two universe tickers with price history", "missing": missing}
    overrides = {}
    for k in OPT_FIELDS:
        if params.get(k) not in (None, ""):
            v = params[k]
            overrides[k] = str(v) if k in ("rebalance", "primary") else (int(v) if k in ("lookback_years", "min_history_days") else float(v))
    key = hashlib.sha1(json.dumps({"t": tickers, "o": overrides}, sort_keys=True).encode()).hexdigest()
    hit = _opt_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    cfg = dataclasses.replace(CONFIG.portfolio, **overrides)
    tp = u.loc[tickers].copy()
    for c, d in (("upside", 0.0), ("n_methods", 1), ("upside_dispersion", 0.3), ("market_cap", float(u["market_cap"].median()))):
        if c not in tp.columns:
            tp[c] = d
        tp[c] = tp[c].fillna(d)
    rf = float(summary().get("risk_free") or 0.045)
    erp = float(CONFIG.valuation.equity_risk_premium)
    ff = (_json(LATEST / "macro_panel.json") or {}).get("FEDFUNDS") or {}
    cash_rate = pd.Series({pd.Timestamp(k): float(v) / 100.0 for k, v in ff.items()}, name="FEDFUNDS").sort_index() if ff else None
    try:
        res = optimizer.run_portfolio(tp, px, rf, cfg, erp=erp, cash_rate=cash_rate)
    except Exception as e:
        return {"error": f"optimisation failed: {e}", "missing": missing}
    opt = res["opt"]
    methods = {}
    for m in optimizer.CONSTRUCTION_METHODS:
        o = opt[m]
        methods[m] = {"exp_return": o["exp_return"], "vol": o["vol"], "sharpe": o["sharpe"], "weights": o["weights"].round(4).to_dict(),
                      "risk_contrib": o["risk_contrib"].round(4).to_dict(), "label": o["label"], "family": o["family"], "return_model": o["return_model"]}
    rec = opt["recommended"]
    bt, bte = res["backtest"], res.get("backtest_equal")
    out = clean({"tickers": list(res["returns"].columns), "missing": missing, "excluded": res["returns_info"].get("excluded_short_history", {}),
                 "config": dataclasses.asdict(cfg), "rf": rf, "methods": methods,
                 "registry": {k: {kk: v[kk] for kk in ("label", "family", "desc", "needs_mu")} for k, v in C.REGISTRY.items()},
                 "recommended": {"weights": rec["weights"].round(4).to_dict(), "cash": rec["cash"], "exp_return": rec["exp_return"], "vol": rec["vol"], "sharpe": rec["sharpe"], "base": rec["base"], "scale": rec["scale"]},
                 "frontier": opt["frontier"].to_dict(orient="records"), "asset_stats": res["asset_stats"].round(4).to_dict(orient="index"),
                 "corr": {"tickers": list(res["corr"].index), "matrix": res["corr"].round(3).values.tolist()},
                 "backtest": {"dates": [d.strftime("%Y-%m-%d") for d in bt["cumulative"].index], "cumulative": {c: bt["cumulative"][c].round(4).tolist() for c in bt["cumulative"].columns},
                              "drawdown": {c: bt["drawdown"][c].round(4).tolist() for c in bt["drawdown"].columns}, "stats": bt["stats"].to_dict(orient="index"), "window": bt["window"], "meta": bt.get("meta", {}),
                              "equal_weight_stats": bte["stats"].to_dict(orient="index") if bte else None},
                 "construction_backtest": {"dates": [d.strftime("%Y-%m-%d") for d in res["construction_backtest"]["cumulative"].index],
                                           "cumulative": {c: res["construction_backtest"]["cumulative"][c].round(4).tolist() for c in res["construction_backtest"]["cumulative"].columns},
                                           "drawdown": {c: res["construction_backtest"]["drawdown"][c].round(4).tolist() for c in res["construction_backtest"]["drawdown"].columns},
                                           "stats": res["construction_backtest"]["stats"].to_dict(orient="index")},
                 "returns_info": {k: v for k, v in res["returns_info"].items() if k != "backfilled_days"}, "took_s": round(time.time() - t0, 1)})
    _opt_cache[key] = (time.time(), out)
    return out


# ----------------------------------------------------------------------------- Black-Litterman
def bl_payload() -> dict:
    """Latest BL construction, sensitivity and walk-forward evaluation (scripts/run_bl.py writes these)."""
    cmp_ = _json(LATEST / "bl_comparison.json")
    if cmp_ is None:
        return {"available": False, "run": config.run_info(), "reason": "no BL run yet - POST /api/research/bl/run or: python scripts/run_bl.py"}
    from ai_semi_intel.portfolio import bl as _bl
    return clean({"available": True, "run": config.run_info(), "provenance": _bl.VIEW_PROVENANCE,
                  "summary": _json(LATEST / "bl_summary.json") or {}, "comparison": cmp_,
                  "sensitivity": _json(LATEST / "bl_sensitivity.json") or {}, "walk_forward": _json(LATEST / "bl_walkforward.json") or {"available": False}})


def bl_run(params: dict) -> dict:
    """Run the BL construction on an arbitrary ticker set / view mix, without touching the stored files."""
    import numpy as np
    from ai_semi_intel.config.settings import CONFIG
    from ai_semi_intel.portfolio import bl as blmod
    from ai_semi_intel.portfolio import bl_eval, optimizer
    u, px = universe(), price_panel()
    if u.empty or px.empty:
        return {"error": "pipeline outputs or price cache not available"}
    req = [str(t).upper().strip() for t in params.get("tickers", []) if str(t).strip()] or list(config.holdings())
    tickers = [t for t in req if t in u.index and t in px.columns]
    missing = [t for t in req if t not in tickers]
    if len(tickers) < 3:
        return {"error": "need at least three universe tickers with price history", "missing": missing}
    sources = tuple(x for x in (params.get("sources") or ["fair_value", "event_score"]) if x in blmod.VIEW_SOURCES)
    rf = float(summary().get("risk_free") or 0.045)
    erp = float(CONFIG.valuation.equity_risk_premium)
    top = u.loc[tickers]
    ov = overlay()
    try:
        out = bl_eval.compare_constructions(top, px, rf, CONFIG.portfolio, erp, ov if not ov.empty else None, sources)
        R, _ = optimizer.build_returns(px, tickers, top["segment"], CONFIG.portfolio)
        cov = optimizer.shrunk_cov(R)
        betas = optimizer.realised_betas(R, px)
        tp = top.reindex(cov.index)
        out["ablation"] = bl_eval.view_ablation(tp, cov, rf, betas, CONFIG.portfolio, erp, ov if not ov.empty else None)
    except Exception as e:
        return {"error": f"BL run failed: {e}", "missing": missing}
    out["missing"] = missing
    return clean(out)


def constructions_payload() -> dict:
    """Walk-forward comparison of every construction method (scripts/run_constructions.py writes this)."""
    wf = _json(LATEST / "constructions_walkforward.json")
    if wf is None:
        return {"available": False, "run": config.run_info(), "reason": "no run yet - POST /api/research/constructions/run or: python scripts/run_constructions.py"}
    from ai_semi_intel.portfolio import constructions as C
    from ai_semi_intel.portfolio import construction_eval as CE
    # A stored curve is only the truth while the code, data and schema that produced it are still current.
    # Serving a stale artifact is how a fixed defect keeps being reported as fixed while the published curve
    # still contains it, so freshness is verified here rather than assumed.
    freshness = {"checked": False, "reason": "price panel or universe unavailable"}
    try:
        import pandas as _pd
        _px = _pd.read_parquet(config.PROJECT_ROOT / "data_cache" / "prices" / "long_adj_close.parquet")
        _uni = universe()
        if _uni is not None and not _uni.empty:
            freshness = {"checked": True, **CE.verify_artifact(wf, _px, _uni)}
    except Exception as e:                                                   # pragma: no cover - IO failure
        freshness = {"checked": False, "reason": f"freshness not verifiable: {e}"}
    # An IO failure is not evidence that the stored result is current.  Keep the
    # bytes inspectable in that case, but fail closed on decision use until the
    # artifact can be checked against the current code and inputs.
    verified_fresh = bool(freshness.get("checked") and freshness.get("fresh") is True)
    stale = bool(freshness.get("checked") and not freshness.get("fresh"))
    unverified = not bool(freshness.get("checked"))
    if stale:
        artifact_scope = "HISTORICAL_STALE_COMPARISON"
        reason = ("this walk-forward artifact is STALE (" + ", ".join(freshness.get("stale_reasons", []))
                  + ") and is shown only as a historical comparison. Re-run: "
                  "python scripts/run_constructions.py")
    elif unverified:
        artifact_scope = "UNVERIFIED_HISTORICAL_COMPARISON"
        reason = (str(freshness.get("reason") or "artifact freshness could not be verified")
                  + "; the stored result is shown only for inspection and is not decision-usable")
    else:
        artifact_scope = "CURRENT"
        reason = None
    pit_gate = wf.get("pit_gate") or {}
    pit_claim_available = pit_gate.get("alpha_claim_available") is True
    decision_usable = bool(verified_fresh and pit_claim_available)
    if decision_usable:
        decision_reason = None
    elif not verified_fresh:
        decision_reason = reason
    else:
        decision_reason = (str(pit_gate.get("headline") or wf.get("claim_status") or
                               "the point-in-time alpha-claim gate is absent or closed")
                           + "; the current artifact is a construction-mechanism diagnostic only")
    return clean({"available": True, "run": config.run_info(),
                  # Keep a stale artifact inspectable as a historical comparison.  It is
                  # explicitly scoped below and must never be presented as the current
                  # run.  Withholding the bytes made every comparison chart disappear and
                  # prevented users from diagnosing why a regeneration was required.
                  "walk_forward": wf,
                  "artifact_freshness": freshness,
                  "stale": bool(stale),
                  "freshness_verified": not unverified,
                  "artifact_scope": artifact_scope,
                  # Currentness and research validity are separate gates.  A
                  # freshly generated mechanism diagnostic remains CURRENT,
                  # while the missing PIT contract keeps decision use closed.
                  "decision_usable": decision_usable,
                  "decision_reason": decision_reason,
                  "reason": reason,
                  "registry": {k: {kk: v[kk] for kk in ("label", "family", "desc", "needs_mu")} for k, v in C.REGISTRY.items()},
                  "production_primary": __import__("ai_semi_intel.config.settings", fromlist=["CONFIG"]).CONFIG.portfolio.primary})


# ----------------------------------------------------------------------------- pipeline runner
class PipelineRunner:
    LOG = config.LOGS / "pipeline_web.log"
    INCLUDE_DAILY = True

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.returncode: int | None = None
        self.stages: list[str] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, stages: list[str] | None = None) -> dict:
        with self._lock:
            if self.running:
                return {"started": False, "reason": "already running"}
            from ai_semi_intel.publication import pipeline_running
            occupied = pipeline_running(config.OUTPUTS)
            if occupied is not False:
                return {"started": False, "reason": "scheduled or command-line pipeline is running" if occupied else "pipeline lock status is unreadable"}
            self.stages = stages or []
            cmd = [sys.executable, "-m", "ai_semi_intel.pipeline"] + (["--stages", *self.stages] if self.stages else [])
            self.LOG.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.LOG, "w")
            self.proc = subprocess.Popen(cmd, cwd=str(config.PROJECT_ROOT), stdout=f, stderr=subprocess.STDOUT)
            self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.finished_at, self.returncode = None, None
            self._save_state()
            threading.Thread(target=self._wait, daemon=True).start()
            return {"started": True, "cmd": " ".join(cmd)}

    def _wait(self):
        rc = self.proc.wait()
        self.returncode, self.finished_at = rc, datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save_state()

    def _save_state(self):
        from ai_semi_intel.publication import atomic_bytes
        value = {"started_at": self.started_at, "finished_at": self.finished_at,
                 "returncode": self.returncode, "stages": self.stages,
                 "status": "RUNNING" if self.returncode is None else "FINISHED" if self.returncode == 0 else "FAILED"}
        try:
            atomic_bytes(self.LOG.with_suffix(self.LOG.suffix + ".json"), json.dumps(value).encode())
        except OSError:
            pass  # The process still owns its log; metadata failure must not launch it again.

    def status(self) -> dict:
        from .pipeline_activity import describe
        from ai_semi_intel.publication import pipeline_running
        from ai_semi_intel.config.settings import CONFIG
        web = config._json_file(self.LOG.with_suffix(self.LOG.suffix + ".json"))
        if self.started_at is not None:
            web = {"started_at": self.started_at, "finished_at": self.finished_at,
                   "returncode": self.returncode, "stages": self.stages}
        web = dict(web, running=self.running)
        occupied = pipeline_running(config.OUTPUTS) if self.INCLUDE_DAILY else False
        activity = describe(config.OUTPUTS, web, self.LOG, occupied=occupied,
                            now=datetime.now(timezone.utc), include_daily=self.INCLUDE_DAILY)
        output = summary()
        return {**activity,
                "lock_status": "unknown" if occupied is None else "busy" if occupied else "idle",
                "asof": output.get("asof"), "generated_at": output.get("generated_at"), "run_id": output.get("run_id"),
                "active_rebalance_policy": CONFIG.portfolio.execution_policy_version,
                "rebalance_frequency": CONFIG.portfolio.rebalance,
                "execution_status": "TARGET_PREPARATION_ONLY; overnight paper accounting and live execution are not enabled"}


RUNNER = PipelineRunner()


class BLRunner(PipelineRunner):
    """Runs scripts/run_bl.py (including the slow walk-forward) in the background."""
    LOG = config.LOGS / "bl_run.log"
    INCLUDE_DAILY = False

    def start(self, stages: list[str] | None = None) -> dict:
        with self._lock:
            if self.running:
                return {"started": False, "reason": "already running"}
            cmd = [sys.executable, str(config.PROJECT_ROOT / "scripts" / "run_bl.py")] + (stages or [])
            self.LOG.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.LOG, "w")
            self.proc = subprocess.Popen(cmd, cwd=str(config.PROJECT_ROOT), stdout=f, stderr=subprocess.STDOUT)
            self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.finished_at, self.returncode, self.stages = None, None, stages or []
            threading.Thread(target=self._wait, daemon=True).start()
            return {"started": True, "cmd": " ".join(cmd)}


BL_RUNNER = BLRunner()


class ConstructionRunner(BLRunner):
    """Runs scripts/run_constructions.py (the 13-method walk-forward) in the background."""
    LOG = config.LOGS / "constructions_run.log"

    def start(self, stages: list[str] | None = None) -> dict:
        with self._lock:
            if self.running:
                return {"started": False, "reason": "already running"}
            cmd = [sys.executable, str(config.PROJECT_ROOT / "scripts" / "run_constructions.py")] + (stages or [])
            self.LOG.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.LOG, "w")
            self.proc = subprocess.Popen(cmd, cwd=str(config.PROJECT_ROOT), stdout=f, stderr=subprocess.STDOUT)
            self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.finished_at, self.returncode, self.stages = None, None, stages or []
            threading.Thread(target=self._wait, daemon=True).start()
            return {"started": True, "cmd": " ".join(cmd)}


CONSTRUCTION_RUNNER = ConstructionRunner()
