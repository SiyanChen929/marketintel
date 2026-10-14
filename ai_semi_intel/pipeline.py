"""End-to-end daily pipeline.

    python -m ai_semi_intel.pipeline            # full run (data -> deck -> dashboard)
    python -m ai_semi_intel.pipeline --stages data analysis portfolio persist charts
    python -m ai_semi_intel.pipeline --force    # ignore caches
"""
from __future__ import annotations

import argparse
import fcntl
import shutil
import sys
import tempfile
import warnings
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config.settings import CONFIG, LATEST, HISTORY, CHARTS, DECK, DASHBOARD, OUTPUTS
from .config.universe import tickers, universe_frame, BENCHMARKS
from .utils.io import DataUnavailable, setup_logging, log, write_json, read_json
from .data import yahoo, edgar, fred, pitchbook
from .analysis import fundamentals, valuation, scoring, industry, strategic
from .portfolio import optimizer, target_archive
from . import runs
from . import trading_policy, publication
from .dashboard import monitoring

warnings.filterwarnings("ignore")
ALL_STAGES = ("data", "analysis", "portfolio", "persist", "charts", "deck", "dashboard")


def stage_data(ctx: dict, force: bool = False) -> dict:
    tk = tickers()
    ctx["px"] = yahoo.fetch_prices(tk + list(BENCHMARKS), tag="universe", force=force)
    acquired = datetime.fromtimestamp((yahoo.PRICE_DIR / "prices_universe.parquet").stat().st_mtime, timezone.utc)
    ctx["px"], ctx["price_finality"] = trading_policy.confirmed_prices(ctx["px"], acquired, datetime.now(timezone.utc))
    ctx["snaps"] = yahoo.fetch_all_fundamentals(tk, workers=4, force=force)
    ctx["edgar"] = edgar.fetch_all_edgar(tk)
    ctx["panel"] = fred.macro_panel()
    # fail closed: a run without prices or a risk-free rate stops here and leaves a BLOCKED marker instead of stale numbers
    ctx["manifest"] = runs.bind_price_finality(runs.data_manifest(ctx["px"], tk, ctx["panel"]), ctx["price_finality"])
    blocked = runs.blocking_reasons(ctx["manifest"])
    if not blocked:
        try:
            ctx["rf"] = fred.risk_free_rate(ctx["panel"])
        except DataUnavailable as e:
            blocked = [str(e)]
    if blocked:
        runs.write_blocked(blocked, "data", LATEST)
        log.error("run blocked: %s", "; ".join(blocked))
        raise DataUnavailable("; ".join(blocked))
    if ctx["manifest"]["status"] != "fresh":
        log.warning("data health %s: %s", ctx["manifest"]["status"], ", ".join(ctx["manifest"]["stale"]))
    ctx["macro_snapshot"] = fred.macro_snapshot(ctx["panel"])
    ctx["pitchbook"] = pitchbook.pitchbook_context(tk)
    log.info("data: prices to %s, rf %.2f%%, %s", ctx["px"].index[-1].date(), ctx["rf"] * 100, ctx["pitchbook"]["status"][:60])
    return ctx


def stage_analysis(ctx: dict) -> dict:
    screen_config = deepcopy(CONFIG.screen)
    ctx["screen_config"] = asdict(screen_config)
    feat = fundamentals.build_features(ctx["snaps"], ctx["edgar"], ctx["px"], ctx["asof"])
    val, vmeta = valuation.run_valuation(feat, ctx["rf"])
    ctx["strategic_refresh"] = strategic.refresh(val, ctx["px"], ctx["asof"],
        max_per_run=getattr(CONFIG, "strategic_max_refresh_per_run", 12),
        allow_llm_refresh=ctx.get("allow_llm_refresh", True))
    ctx["strategic_assessments"] = deepcopy(strategic.load_all())
    ctx["strategic_knowledge_at"] = datetime.now(timezone.utc)
    try:
        from .research.forward_registry import load_contexts
        ctx["strategic_contexts"] = deepcopy(load_contexts(strategic.CONTEXT_DIR))
    except (ValueError, OSError, TypeError) as exc:
        ctx["forward_input_error"] = type(exc).__name__
        ctx["strategic_contexts"] = {}
    val = val.join(strategic.strategic_features(val, ctx["asof"],
        knowledge_at=ctx["strategic_knowledge_at"], assessments=ctx["strategic_assessments"]))
    log.info("strategic: %d/%d evidence assessments, %d priors, %d stale, %d queued (%s)", int((val["strategic_source"] == "evidence_assessment").sum()), len(val),
             int((val["strategic_source"] == "reference_prior").sum()), int(val["strategic_stale"].fillna(True).sum()), ctx["strategic_refresh"]["queued"],
             "dry-run: set ANTHROPIC_API_KEY for automatic LLM re-assessment" if ctx["strategic_refresh"]["dry_run"] else "Claude API")
    sc = scoring.score_universe(val, screen_config)
    prev_snap = read_json(LATEST / "snapshot.json") or {}
    ctx["previous_holdings"] = list(prev_snap.get("top") or [])
    top = scoring.select_top(sc, screen_config, prev=ctx["previous_holdings"])
    sc = sc.join(industry.positioning(sc))
    top = top.join(industry.positioning(sc).reindex(top.index))
    ctx.update(universe=sc, top=top, vmeta=vmeta, funnel=scoring.selection_funnel(sc, screen_config),
               seg=industry.segment_table(sc))
    log.info("analysis: %d scored, top15 = %s", len(sc), ", ".join(top.index))
    return ctx


def stage_portfolio(ctx: dict) -> dict:
    ff = ctx["panel"].get("FEDFUNDS")                                   # cash accrues along the fed funds path, not today's 10Y yield
    cash_rate = (ff.astype(float) / 100.0).rename("FEDFUNDS") if ff is not None and len(ff) else None
    ctx["res"] = optimizer.run_portfolio(ctx["top"], ctx["px"], ctx["rf"], erp=CONFIG.valuation.equity_risk_premium, cash_rate=cash_rate)
    o = ctx["res"]["opt"]["recommended"]
    log.info("portfolio: recommended (%s, vol-target) ret %.1f%% vol %.1f%% sharpe %.2f cash %.0f%%", o.get("base", "?"), o["exp_return"] * 100, o["vol"] * 100, o["sharpe"], o.get("cash", 0) * 100)
    return ctx


def _snapshot_dict(ctx: dict, run_id: str, generated_at: str, supersedes: str | None = None) -> dict:
    """Canonical snapshot of a run: executable target weights (assets + cash = 100%) plus the model alternatives."""
    top, opt = ctx["top"], ctx["res"]["opt"]
    rec = opt["recommended"]; base = rec.get("base", "risk_parity")
    asof = ctx["asof"].strftime("%Y-%m-%d")
    return {"date": asof, "asof_session": asof, "run_id": run_id, "portfolio_id": runs.PORTFOLIO_ID, "generated_at": generated_at, "supersedes": supersedes,
            "rebalance_policy": trading_policy.policy(generated_at, str(ctx["px"].index[-1].date())),
            "price_finality": ctx.get("price_finality"), "daily_attempt_id": ctx.get("daily_attempt_id"),
            "construction": base, "vol_target": float(CONFIG.portfolio.vol_target), "scale": float(rec.get("scale", 1.0)), "cash": float(rec.get("cash", 0.0)),
            "weights": rec["weights"].to_dict(),                       # canonical: executable target weights
            "weights_model": opt[base]["weights"].to_dict(),           # fully-invested construction before the vol target
            "weights_max_sharpe": opt["max_sharpe"]["weights"].to_dict(),
            "top": list(top.index), "composite": top["composite"].to_dict(), "upside": top["upside"].to_dict(), "rank": top["rank"].to_dict(),
            "price": top["price"].to_dict(), "validation_status": runs.VALIDATION_STATUS}


def stage_persist(ctx: dict) -> dict:
    """Build one complete version without exposing partial latest files."""
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pipeline-stage-", dir=OUTPUTS) as temp:
        return _stage_persist(ctx, Path(temp))


def _stage_persist(ctx: dict, out: Path) -> dict:
    asof = ctx["asof"].strftime("%Y-%m-%d")
    generated_at = datetime.now(trading_policy.ET)
    run_id = runs.new_run_id(ctx["asof"], generated_at)
    index = runs.load_index(HISTORY)
    superseded = runs.latest_same_session(index, asof)
    cur = _snapshot_dict(ctx, run_id, generated_at.isoformat(timespec="seconds"), superseded["run_id"] if superseded else None)
    # All methods share one observed input snapshot, but retain independent target histories.
    ctx["construction_targets"] = target_archive.build(ctx["res"]["opt"], cur, optimizer.CONSTRUCTION_METHODS)
    # turnover is measured against the approved prior run (last approved run of an earlier session), never a same-day rerun
    prior = runs.approved_prior(index, asof)
    prior_snap = runs.load_run_snapshot(prior["run_id"], HISTORY) if prior else None
    target_archive.bind_previous(ctx["construction_targets"], HISTORY, prior["run_id"] if prior else None)
    write_json(ctx["construction_targets"], out / "construction_targets.json")
    basis = "approved_prior_run" if prior_snap else "first_run"
    if prior_snap is None:
        legacy = read_json(LATEST / "snapshot.json")
        if legacy and legacy.get("date") != asof and "cash" in legacy:      # canonical snapshot from before the run index existed
            prior_snap, basis = legacy, "legacy_snapshot"
    ctx["changes"] = monitoring.compare_runs(cur, prior_snap)
    ctx["changes"].update(basis=basis, prev_run_id=(prior or {}).get("run_id") or (prior_snap or {}).get("run_id"))
    if superseded:
        rd = monitoring.compare_runs(cur, runs.load_run_snapshot(superseded["run_id"], HISTORY) or {})
        ctx["changes"].update(supersedes=superseded["run_id"], rerun_turnover=rd["turnover"],
                              rerun_diff={k: rd[k] for k in ("entries", "exits", "weight_changes", "cash_change")})
    w = ctx["res"]["opt"]["recommended"]["weights"]                    # executable target weights everywhere downstream
    w_model = ctx["res"]["opt"][ctx["res"]["opt"]["recommended"].get("base", "risk_parity")]["weights"]
    ctx["alerts"] = monitoring.build_alerts(ctx["top"], w, ctx["changes"], ctx["px"], ctx["asof"])
    ctx["alerts"] += monitoring.event_alerts(ctx["top"])        # real-time event overlay (market_intel), if fresh
    # strategic pillar outputs, gate/cap/stale alerts and the daily assessment snapshot
    uni_ = ctx["universe"]
    scols = [c for c in ["name", "segment", "strategic_score", "scarcity_raw", "scarcity_strength", "risk_score", "llm_risk", "financial_risk", "strategic_pillar",
                         "score_scarcity", "score_risk", "score_strategic_resilience", "news_direction", "strategic_confidence", "strategic_asof", "strategic_stale",
                         "strategic_source", "strategic_availability", "strategic_rejection_reason", "strategic_version",
                         "risk_eligible", "risk_capped", "risk_gated_out", "risk_flags", "strategic_thesis", "kill_criteria", "watch_items",
                         "composite", "rank_all"] if c in uni_.columns]
    uni_[scols].to_csv(out / "strategic_table.csv")
    write_json(ctx.get("strategic_refresh", {}), out / "strategic_refresh.json")
    if strategic.QUEUE_PATH.exists():
        shutil.copy(strategic.QUEUE_PATH, out / "strategic_queue.json")
    strategic_snapshot_path = out / "strategic_snapshot.json"
    write_json(ctx.get("strategic_assessments", {}), strategic_snapshot_path)
    research_inputs = {"run_id": run_id,
        "knowledge_at": ctx["strategic_knowledge_at"].isoformat() if ctx.get("strategic_knowledge_at") else None,
        "screen_config": ctx.get("screen_config"), "previous_holdings": ctx.get("previous_holdings"),
        "input_status": "frozen_at_analysis" if all(key in ctx for key in
            ("strategic_knowledge_at", "screen_config", "previous_holdings")) else "unavailable",
        "validation_status": runs.VALIDATION_STATUS}
    write_json(research_inputs, out / "research_inputs.json")
    if "risk_gated_out" in uni_.columns:
        for t, r in uni_[uni_["risk_gated_out"].fillna(False).astype(bool)].iterrows():
            if int(r.get("rank_all", 999)) <= 25:
                ctx["alerts"].append({"severity": "warning", "ticker": t, "type": "risk_gate", "message": f"{t} excluded by the strategic risk gate (risk {r['risk_score']:.2f}, scarcity {r['scarcity_strength']:.2f}) despite rank {int(r['rank_all'])}"})
    for t, r in ctx["top"].iterrows():
        if bool(r.get("strategic_stale", True)) or r.get("strategic_source") != "evidence_assessment":
            ctx["alerts"].append({"severity": "info", "ticker": t, "type": "strategic_stale", "message": f"{t}: no fresh evidence-logged strategic assessment (source {r.get('strategic_source')}); refresh queued"})
        if bool(r.get("risk_capped", False)):
            ctx["alerts"].append({"severity": "warning", "ticker": t, "type": "risk_cap", "message": f"{t} high strategic risk ({r['risk_score']:.2f}) kept as bottleneck; weight capped at 5%"})
    ctx["kpi"] = monitoring.kpi_table(ctx["top"], w, ctx["px"], weights_model=w_model)
    # ---- write latest ---------------------------------------------------------------------
    uni = ctx["universe"].drop(columns=["summary"], errors="ignore")
    uni.to_csv(out / "universe_scores.csv")
    ctx["top"].to_csv(out / "top15.csv")
    ctx["kpi"].to_csv(out / "kpi_table.csv")
    ctx["seg"].to_csv(out / "segment_table.csv")
    ctx["macro_snapshot"].to_csv(out / "macro_snapshot.csv")
    res = ctx["res"]
    model_methods = [m for m in optimizer.CONSTRUCTION_METHODS if m in res["opt"]]
    output_methods = ["recommended", *model_methods]
    weights_out = pd.DataFrame({k: res["opt"][k]["weights"] for k in output_methods})
    weights_out["min_vol"] = weights_out["min_variance"]                 # legacy consumers
    weights_out.to_csv(out / "portfolio_weights.csv")
    res["asset_stats"].to_csv(out / "asset_stats.csv")
    res["corr"].to_csv(out / "correlation.csv")
    res["backtest"]["cumulative"].to_csv(out / "backtest_cumulative.csv")
    res["backtest"]["drawdown"].to_csv(out / "backtest_drawdown.csv")
    res["backtest"]["stats"].to_csv(out / "backtest_stats.csv")
    res["construction_backtest"]["cumulative"].to_csv(out / "construction_backtest_cumulative.csv")
    res["construction_backtest"]["drawdown"].to_csv(out / "construction_backtest_drawdown.csv")
    res["construction_backtest"]["stats"].to_csv(out / "construction_backtest_stats.csv")
    res["opt"]["frontier"].to_csv(out / "frontier.csv", index=False)
    stats_out = pd.DataFrame({k: {"exp_return": res["opt"][k]["exp_return"], "vol": res["opt"][k]["vol"], "sharpe": res["opt"][k]["sharpe"]}
                              for k in output_methods}).T
    stats_out.loc["min_vol"] = stats_out.loc["min_variance"]             # legacy consumers
    stats_out.to_csv(out / "portfolio_stats.csv")
    rc_out = pd.DataFrame({k: res["opt"][k]["risk_contrib"] for k in model_methods})
    rc_out["min_vol"] = rc_out["min_variance"]
    rc_out.to_csv(out / "risk_contributions.csv")
    macro_recent = {k: {str(i.date()): float(v) for i, v in s[s.index >= pd.Timestamp.today() - pd.DateOffset(years=5)].items()} for k, s in ctx["panel"].items()}
    write_json(macro_recent, out / "macro_panel.json")
    ctx["px"].tail(756)[list(ctx["top"].index) + list(BENCHMARKS)].to_csv(out / "prices_top15.csv")
    # ---- append-only history tables (run_id column; rows are never deleted) ---------------------
    panel_cols = ["composite", "alpha_score", "score_growth", "score_momentum", "score_value", "score_scarcity", "score_risk", "score_risk_resilience", "score_market_resilience", "score_financial_resilience", "score_valuation_resilience", "scarcity_coverage", "risk_coverage", "decision_coverage", "decision_score_status", "strategic_asof", "strategic_source", "risk_flags", "score_fund_momentum", "score_price_momentum", "score_quality", "upside", "dcf_upside", "mult_upside", "growth_accel", "fwd_accel", "mom_12_1_volscaled", "industry_mom", "mult_residual", "scarcity_strength", "risk_score", "llm_risk", "financial_risk", "strategic_pillar", "news_direction",
                  "target_upside", "implied_growth", "price", "market_cap", "segment", "eligible", "rank_all", "ev_sales_ntm", "pe_ntm",
                  "rev_growth_ntm", "eps_rev_90d", "eps_rev_30d", "fcf_margin", "gross_margin", "roic", "rel_6m_vs_soxx", "vs_200dma"]
    prow = uni[[c for c in panel_cols if c in uni.columns]].copy(); prow.insert(0, "date", asof); prow.index.name = "ticker"
    rows = [{"date": asof, "ticker": t, "rank": int(r["rank"]), "composite": r["composite"], "upside": r["upside"], "price": r["price"],
             "weight": float(w.get(t, 0.0)), "weight_model": float(w_model.get(t, 0.0)), "weight_max_sharpe": float(res["opt"]["max_sharpe"]["weights"].get(t, 0.0)),
             "fair_value": r["fair_value"]} for t, r in ctx["top"].iterrows()]
    ph = pd.DataFrame([{"date": asof, **{k: res["opt"]["recommended"][k] for k in ("exp_return", "vol", "sharpe")}, "cash": res["opt"]["recommended"].get("cash", 0.0),
                        "turnover": ctx["changes"]["turnover"], "turnover_basis": ctx["changes"]["basis"], "prev_run_id": ctx["changes"].get("prev_run_id"), "rf": ctx["rf"]}])
    try:
        from .research import live_ic
        ctx["live_validation"] = live_ic.compute(ctx["px"], acquired_at=ctx["price_finality"]["acquired_at"], output_path=None)
        write_json(ctx["live_validation"], out / "live_validation.json")
    except Exception as e:                        # pragma: no cover
        log.warning("live validation skipped: %s", e)
        ctx["live_validation"] = {"status": "error", "error_type": type(e).__name__,
                                  "computed_at": generated_at.isoformat(), "validation_status": runs.VALIDATION_STATUS,
                                  "daily_rebalanced_performance": {"status": "unavailable", "reason": "diagnostic computation failed; no executed strategy ledger"}}
        write_json(ctx["live_validation"], out / "live_validation.json")
    # ---- run manifest: data provenance, evidence state, canonical weights, hashes --------------------
    manifest = runs.bind_price_finality(runs.data_manifest(ctx["px"], list(ctx["top"].index), ctx["panel"], now=generated_at), ctx["price_finality"])
    validation = runs.validation_summary(OUTPUTS, live=ctx["live_validation"])
    fetched = {k: v.get("fetched_at") for k, v in manifest["entries"].items()}
    run = runs.build_run(cur, res["opt"], manifest, validation, ctx["changes"],
                         data_cutoff={"prices": str(ctx["px"].index[-1].date()), "fetched_at": fetched, "session": asof}, returns_info=res["returns_info"])
    write_json(manifest, out / "data_manifest.json")
    write_json(run, out / "run.json")
    summary = {"asof": asof, "run_id": run_id, "portfolio_id": runs.PORTFOLIO_ID, "generated_at": generated_at.isoformat(timespec="seconds"), "supersedes": cur["supersedes"],
               "validation_status": runs.VALIDATION_STATUS, "validation": validation, "actionable": run["actionable"], "not_actionable_reasons": run["not_actionable_reasons"],
               "data_health": run["data_health"], "risk_free": ctx["rf"],
               "valuation": ctx["vmeta"], "funnel": ctx["funnel"], "config": {"screen": asdict(CONFIG.screen), "valuation": asdict(CONFIG.valuation), "portfolio": asdict(CONFIG.portfolio)},
               "config_hash": run["config_hash"], "code_hash": run["code_hash"], "data_manifest_hash": run["data_manifest_hash"],
               "pitchbook": {k: v for k, v in ctx["pitchbook"].items() if k in ("available", "status", "exports", "api_configured")},
               "returns_info": res["returns_info"], "portfolio": {k: {kk: res["opt"][k][kk] for kk in ("exp_return", "vol", "sharpe")} for k in output_methods}, "recommended_cash": res["opt"]["recommended"].get("cash", 0.0),
               "backtest_stats": res["backtest"]["stats"].to_dict(orient="index"), "backtest_meta": res["backtest"].get("meta", {}), "changes": ctx["changes"], "alerts": ctx["alerts"],
               "universe_size": int(len(ctx["universe"])), "data_sources": ["SEC EDGAR XBRL companyfacts + submissions", "Yahoo Finance (yfinance)", "FRED", ctx["pitchbook"]["status"]]}
    write_json(summary, out / "summary.json")
    prev_latest = read_json(LATEST / "snapshot.json")
    if (LATEST / "snapshot.json").exists() and (prev_latest is None or prev_latest.get("date") != asof):
        shutil.copy(LATEST / "snapshot.json", out / "snapshot_prev.json")          # kept for older consumers; not used for turnover
    write_json(cur, out / "snapshot.json")
    # raw consensus snapshot: the only way to build point-in-time estimate history from free data
    cons = {}
    for t, snap in ctx["snaps"].items():
        if not snap.get("ok"):
            continue
        info = snap.get("info", {}) or {}
        cons[t] = {"price": info.get("currentPrice"), "market_cap": info.get("marketCap"), "shares": info.get("sharesOutstanding"),
                   "revenue_estimate": snap.get("revenue_estimate"), "earnings_estimate": snap.get("earnings_estimate"),
                   "eps_trend": snap.get("eps_trend"), "growth_estimates": snap.get("growth_estimates"),
                   "price_targets": snap.get("price_targets"), "recommendation_mean": info.get("recommendationMean"),
                   "n_analysts": info.get("numberOfAnalystOpinions"), "next_earnings": snap.get("next_earnings")}
    write_json(cons, out / "consensus_snapshot.json")
    ctx["run_dir"] = runs.register(run, out, HISTORY, extra_files={"consensus_snapshot.json": out / "consensus_snapshot.json", "strategic_snapshot.json": strategic_snapshot_path,
                                                                     "research_inputs.json": out / "research_inputs.json",
                                                                     "snapshot_prev.json": out / "snapshot_prev.json",
                                                                     "data_manifest.json": out / "data_manifest.json", "live_validation.json": out / "live_validation.json",
                                                                     "backtest_cumulative.csv": out / "backtest_cumulative.csv"})
    ctx["run"] = run
    publication.publish(ctx["run_dir"], LATEST)
    runs.clear_blocked(LATEST)
    # Compatibility tables follow the immutable commit. Recovery can use the
    # committed attempt if any subsequent diagnostic step fails.
    runs.append_rows(HISTORY / "universe_panel.csv", prow.reset_index(), run_id)
    runs.append_rows(HISTORY / "runs.csv", pd.DataFrame(rows), run_id)
    runs.append_rows(HISTORY / "portfolio_history.csv", ph, run_id)
    # ---- history: per-date folder (latest of the day, kept for older consumers) + immutable run directory ----
    hdir = HISTORY / asof; hdir.mkdir(parents=True, exist_ok=True)
    for f in ("top15.csv", "portfolio_weights.csv", "portfolio_stats.csv", "risk_contributions.csv",
              "construction_backtest_cumulative.csv", "construction_backtest_drawdown.csv", "construction_backtest_stats.csv",
              "summary.json", "kpi_table.csv", "snapshot.json", "universe_scores.csv", "run.json", "construction_targets.json", "strategic_snapshot.json"):
        shutil.copy(out / f, hdir / f)
    shutil.copy(out / "consensus_snapshot.json", hdir / "consensus_snapshot.json")
    # Registration time is the actual write time, even when an older assessment
    # is carried forward. Never synthesize prior decisions from today's inputs.
    try:
        from .research import forward_registry
        if ctx.get("forward_input_error"):
            raise ValueError("the analysis-stage context snapshot was unreadable")
        ctx["forward_registration"] = forward_registry.register_snapshot(
            HISTORY / "forward_scores", run_id=run_id, decision_at=generated_at,
            universe=ctx["universe"], assessments=ctx.get("strategic_assessments", {}),
            contexts=ctx.get("strategic_contexts", {}), input_manifest=manifest)
        ctx["forward_evaluation"] = forward_registry.evaluate(
            HISTORY / "forward_scores", ctx["px"], acquired_at=ctx["price_finality"]["acquired_at"])
        ctx["forward_evaluation"].update(run_id=run_id,
            registration_record_hash=ctx["forward_registration"]["record_hash"])
        write_json(ctx["forward_evaluation"], LATEST / "forward_evaluation.json")
    except Exception as exc:
        # The target is already frozen. Keep its identity and expose the evidence
        # failure without rerunning scoring or pretending registration succeeded.
        ctx["forward_evaluation"] = {"status": "error", "run_id": run_id,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "error_type": type(exc).__name__, "validation_status": runs.VALIDATION_STATUS,
            "reason": "Forward evidence could not be registered or evaluated; inspect the research log."}
        try:
            write_json(ctx["forward_evaluation"], LATEST / "forward_evaluation.json")
        except OSError:
            log.warning("forward evidence failure could not be written for %s", run_id)
        log.warning("forward evidence failed for %s: %s", run_id, type(exc).__name__)
    # Candidate comparisons share the frozen production inputs. Missing source
    # reviews stay explicit; this sidecar never changes portfolio targets.
    try:
        from .research import challengers
        if research_inputs["input_status"] != "frozen_at_analysis":
            raise ValueError("research inputs were not frozen at analysis")
        candidate = challengers.build_daily_candidates(run_id=run_id, universe=ctx["universe"],
            assessments=ctx["strategic_assessments"], knowledge_at=ctx["strategic_knowledge_at"],
            screen_config=ctx["screen_config"], previous_holdings=ctx["previous_holdings"],
            input_manifest=manifest, mode="prospective_candidate")
        record = challengers.register_candidates(HISTORY / "research_candidates", candidate)
        ctx["research_candidates"] = {key: record[key] for key in
            ("run_id", "registered_at", "record_hash", "n_rows", "n_paired_scores", "selection",
             "validation_status", "inference_status", "execution_status")}
        ctx["research_candidates"]["status"] = "registered"
    except Exception as exc:
        ctx["research_candidates"] = {"run_id": run_id, "status": "error", "error_type": type(exc).__name__,
            "evaluated_at": datetime.now(timezone.utc).isoformat(), "validation_status": runs.VALIDATION_STATUS}
        log.warning("research candidates unavailable for %s: %s", run_id, type(exc).__name__)
    try:
        write_json(ctx["research_candidates"], LATEST / "research_candidates.json")
    except OSError:
        log.warning("research candidate status could not be written for %s", run_id)
    vpath = OUTPUTS / "validation" / "summary.json"
    ctx["validation"] = read_json(vpath) if vpath.exists() else None
    log.info("persist: run %s (%s%s), %d alerts, %d entries, %d exits, turnover %s vs %s", run_id, "actionable" if run["actionable"] else "NOT ACTIONABLE",
             "" if run["actionable"] else ": " + "; ".join(run["not_actionable_reasons"]), len(ctx["alerts"]), len(ctx["changes"]["entries"]), len(ctx["changes"]["exits"]),
             f"{ctx['changes']['turnover']:.1%}" if ctx["changes"].get("prev_run_id") else "n/a (first run)", ctx["changes"].get("prev_run_id") or "-")
    return ctx


def stage_charts(ctx: dict) -> dict:
    from .charts import build_charts
    ctx["charts"] = build_charts.build_all({
        "universe": ctx["universe"], "top": ctx["top"], "seg": ctx["seg"], "reg": ctx["vmeta"]["regression"], "res": ctx["res"],
        "panel": ctx["panel"], "funnel": ctx["funnel"], "px": ctx["px"], "snaps": ctx["snaps"], "asof": ctx["asof"].strftime("%Y-%m-%d"),
        "weights": asdict(CONFIG.screen)})
    log.info("charts: %d files in %s", len(ctx["charts"]), CHARTS)
    return ctx


def stage_deck(ctx: dict) -> dict:
    from .deck import build_deck
    ctx["deck_path"] = build_deck.build(ctx)
    log.info("deck: %s", ctx["deck_path"])
    return ctx


def stage_dashboard(ctx: dict) -> dict:
    from .dashboard import static_report
    ctx["dashboard_path"] = static_report.build(ctx)
    log.info("dashboard: %s", ctx["dashboard_path"])
    return ctx


def run(stages=ALL_STAGES, force: bool = False, asof: datetime | None = None, *,
        allow_llm_refresh: bool = True, daily_attempt_id: str | None = None) -> dict:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    with (OUTPUTS / ".pipeline.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DataUnavailable("another pipeline owns this output directory") from exc
        return _run(stages, force, asof, allow_llm_refresh=allow_llm_refresh, daily_attempt_id=daily_attempt_id)


def _run(stages=ALL_STAGES, force: bool = False, asof: datetime | None = None, *,
         allow_llm_refresh: bool = True, daily_attempt_id: str | None = None) -> dict:
    setup_logging()
    ctx = {"asof": asof or datetime.now(trading_policy.ET).replace(tzinfo=None),
           "allow_llm_refresh": allow_llm_refresh, "daily_attempt_id": daily_attempt_id}
    t0 = datetime.now()
    for st in ALL_STAGES:
        if st not in stages:
            continue
        log.info("=== stage %s ===", st)
        try:
            ctx = {"data": lambda c: stage_data(c, force), "analysis": stage_analysis, "portfolio": stage_portfolio,
                   "persist": stage_persist, "charts": stage_charts, "deck": stage_deck, "dashboard": stage_dashboard}[st](ctx)
        except DataUnavailable as exc:
            runs.write_blocked([str(exc)], st, LATEST)
            raise
        except Exception as exc:
            # Surface unexpected pre-commit failures without persisting request
            # URLs or credentials that may be embedded in exception messages.
            runs.write_blocked([f"{st} failed ({type(exc).__name__}); inspect the local pipeline log"], st, LATEST)
            raise
    log.info("pipeline done in %.0fs", (datetime.now() - t0).total_seconds())
    return ctx


def main(argv=None):
    ap = argparse.ArgumentParser(description="AI & semiconductor value-chain intelligence pipeline")
    ap.add_argument("--stages", nargs="*", default=list(ALL_STAGES), choices=ALL_STAGES)
    ap.add_argument("--force", action="store_true", help="ignore data caches")
    ap.add_argument("--no-llm-refresh", action="store_true", help="reuse stored assessments without paid model calls")
    a = ap.parse_args(argv)
    stages = a.stages
    # later stages need earlier ones in memory: always run the prerequisites
    needed = set(stages)
    for s in stages:
        needed.update(ALL_STAGES[:ALL_STAGES.index(s)][:4])
    run([s for s in ALL_STAGES if s in needed], force=a.force, allow_llm_refresh=not a.no_llm_refresh)


if __name__ == "__main__":
    main()
