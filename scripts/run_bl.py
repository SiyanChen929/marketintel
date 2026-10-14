"""Black-Litterman construction + evaluation. Writes outputs/latest/bl_*.json|csv and prints a summary.

    python scripts/run_bl.py                    # holdings, fair-value + event views, full evaluation
    python scripts/run_bl.py --no-walk-forward  # skip the (slow) point-in-time backtest
    python scripts/run_bl.py --tickers MU DELL NVDA --sources fair_value event_score analyst_target
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_semi_intel.config.settings import CONFIG, LATEST
from ai_semi_intel.portfolio import bl, bl_eval, optimizer
from ai_semi_intel.utils.io import setup_logging, write_json

OUT = LATEST


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Black-Litterman construction and evaluation")
    ap.add_argument("--tickers", nargs="*", help="default: current holdings from snapshot.json")
    ap.add_argument("--sources", nargs="*", default=["fair_value", "event_score"],
                    choices=list(bl.VIEW_SOURCES))
    ap.add_argument("--no-walk-forward", action="store_true")
    ap.add_argument("--wf-start", default="2013-01-01")
    ap.add_argument("--wf-top-n", type=int, default=15)
    a = ap.parse_args(argv)
    setup_logging()

    uni = pd.read_csv(LATEST / "universe_scores.csv", index_col=0)
    summary = json.loads((LATEST / "summary.json").read_text())
    rf = float(summary.get("risk_free") or 0.045)
    erp = float(CONFIG.valuation.equity_risk_premium)
    px = pd.read_parquet("data_cache/prices/prices_universe.parquet")
    try:
        snap = json.loads((LATEST / "snapshot.json").read_text())
        holdings = list(snap.get("top", []))
    except FileNotFoundError:
        holdings = []
    tickers = [t.upper() for t in (a.tickers or holdings) if t.upper() in uni.index]
    if len(tickers) < 3:
        print("need at least 3 tickers present in universe_scores.csv"); return 1
    top = uni.loc[tickers]
    ov = None
    p = LATEST / "event_overlay.csv"
    if p.exists():
        o = pd.read_csv(p)
        if "ticker" in o.columns:
            ov = o.set_index("ticker")

    print(f"=== Black-Litterman on {len(tickers)} names: {', '.join(tickers)}")
    print(f"    views: {', '.join(a.sources)} | rf {rf:.2%} | erp {erp:.2%}")
    cmp_ = bl_eval.compare_constructions(top, px, rf, CONFIG.portfolio, erp, ov, tuple(a.sources))
    write_json(cmp_, OUT / "bl_comparison.json")
    tbl = pd.DataFrame(cmp_["table"]).T
    print(f"\n--- calibrated delta {cmp_['delta']:.3f}; prior basket return {cmp_['prior_basket_return']:.2%}; "
          f"posterior variance inflation {cmp_['cov_diag_inflation']:+.1%}; {cmp_['n_views']} views")
    print("\n--- ex-ante comparison (all scored on the BL posterior) ---")
    print(tbl[["exp_return_bl", "vol", "sharpe_bl", "max_weight", "effective_n", "max_segment_weight", "turnover_vs_production"]].round(4).to_string())
    tbl.to_csv(OUT / "bl_comparison.csv")

    R, _ = optimizer.build_returns(px, tickers, top["segment"], CONFIG.portfolio)
    cov = optimizer.shrunk_cov(R)
    betas = optimizer.realised_betas(R, px)
    tp = top.reindex(cov.index)
    sens = {"tau": bl_eval.tau_sensitivity(tp, cov, rf, betas, CONFIG.portfolio, erp, ov, tuple(a.sources)),
            "confidence": bl_eval.confidence_sensitivity(tp, cov, rf, betas, CONFIG.portfolio, erp, ov),
            "ablation": bl_eval.view_ablation(tp, cov, rf, betas, CONFIG.portfolio, erp, ov)}
    write_json(sens, OUT / "bl_sensitivity.json")
    print("\n--- view ablation (turnover vs prior-only = how far each source moves the book) ---")
    print(pd.DataFrame(sens["ablation"])[["sources", "n_views", "mean_abs_tilt_bps", "max_weight", "effective_n", "turnover_vs_prior_only"]].round(3).to_string(index=False))
    print("\n--- tau sensitivity (omega_mode=absolute) ---")
    print(pd.DataFrame(sens["tau"]).round(4).to_string(index=False))

    print("\n--- top views by impact ---")
    vt = pd.DataFrame(cmp_["view_table"])
    if not vt.empty:
        print(vt[["view", "source", "q", "confidence", "surprise", "abs_impact_bps"]].head(12).round(4).to_string(index=False))

    wf = {"available": False}
    if not a.no_walk_forward:
        print("\n--- walk-forward (point-in-time momentum views; may take a minute) ---")
        long_px = pd.read_parquet("data_cache/prices/long_adj_close.parquet")
        wf = bl_eval.walk_forward(long_px, uni, rf=0.03, start=a.wf_start, top_n=a.wf_top_n)
        if wf.get("available"):
            print(f"window {wf['window'][0]} -> {wf['window'][1]}, {wf['n_rebalances']} monthly rebalances")
            print(pd.DataFrame(wf["stats"]).T[["cagr", "vol", "sharpe", "max_drawdown", "total_return"]].round(4).to_string())
            ex = wf["excess_vs_prior"]
            print(f"BL views minus prior-only: {ex['annualised']:+.2%} p.a., naive t {ex['t_naive']:.2f}, monthly win rate {ex['monthly_win_rate']:.0%}")
        else:
            print("walk-forward unavailable:", wf.get("reason"))
    write_json(wf, OUT / "bl_walkforward.json")
    write_json({"asof": summary.get("asof"), "tickers": tickers, "sources": a.sources, "rf": rf, "erp": erp,
                "delta": cmp_["delta"], "n_views": cmp_["n_views"], "primary": CONFIG.portfolio.primary,
                "walk_forward": {k: wf.get(k) for k in ("available", "window", "n_rebalances", "stats", "excess_vs_prior", "params")}},
               OUT / "bl_summary.json")
    print(f"\nwritten: {OUT}/bl_comparison.json|csv, bl_sensitivity.json, bl_walkforward.json, bl_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
