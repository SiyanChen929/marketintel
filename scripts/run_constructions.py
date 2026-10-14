"""Walk-forward comparison of every portfolio construction method on identical inputs.

    python scripts/run_constructions.py                    # all methods, 2013-today
    python scripts/run_constructions.py --no-bl-views      # equilibrium prior only (no views)
    python scripts/run_constructions.py --methods risk_parity mean_variance hrp
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_semi_intel.config.settings import LATEST
from ai_semi_intel.portfolio import constructions as C
from ai_semi_intel.portfolio import construction_eval as CE
from ai_semi_intel.utils.io import setup_logging, write_json

COLS = ["cagr", "vol", "sharpe", "sortino", "max_drawdown", "calmar", "avg_turnover", "avg_effective_n", "information_ratio"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare portfolio construction methods, point in time")
    ap.add_argument("--methods", nargs="*", choices=list(C.REGISTRY))
    ap.add_argument("--start", default="2013-01-01")
    ap.add_argument("--top-n", type=int, default=15)
    ap.add_argument("--max-w", type=float, default=0.15)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--no-bl-views", action="store_true", help="use the equilibrium prior only")
    a = ap.parse_args(argv)
    setup_logging()
    uni = pd.read_csv(LATEST / "universe_scores.csv", index_col=0)
    long_px = pd.read_parquet("data_cache/prices/long_adj_close.parquet")
    print(f"=== walk-forward from {a.start}: {len(a.methods or C.REGISTRY)} constructions, top {a.top_n} by momentum, "
          f"max weight {a.max_w:.0%}, {a.cost_bps:.0f} bps costs, views {'off' if a.no_bl_views else 'on'}")
    from ai_semi_intel.config.settings import CONFIG
    wf = CE.walk_forward(long_px, uni, methods=a.methods, start=a.start, top_n=a.top_n, max_w=a.max_w,
                         cost_bps=a.cost_bps, use_bl_views=not a.no_bl_views,
                         vol_target=CONFIG.portfolio.vol_target)
    if not wf.get("available"):
        print("unavailable:", wf.get("reason"))
        return 1
    write_json(wf, LATEST / "constructions_walkforward.json")
    gate = wf.get("pit_gate") or {}
    if not gate.get("alpha_claim_available", False):
        print("\n*** " + wf.get("claim_status", "MECHANISM DIAGNOSTIC ONLY") + " ***")
        print("    " + gate.get("headline", ""))
        print("    missing: " + ", ".join(gate.get("missing", [])))
        print("    the table below compares CONSTRUCTIONS; its level is not a return claim\n")
    # level statistics are withheld while the point-in-time contract is unmet; surface them from the
    # diagnostic block so the console still shows a table, clearly labelled as a diagnostic
    flat = {m: {**{k: v for k, v in st.items() if k != "withheld"}, **(st.get("withheld") or {})}
            for m, st in wf["stats"].items()}
    t = pd.DataFrame(flat).T
    t = t.reindex(columns=[c for c in COLS if c in t.columns] + ["family"])
    if "sharpe" in t.columns:
        t = t.sort_values("sharpe", ascending=False)
    if wf.get("failed_methods"):
        print("\nFAILED (excluded, never replaced by equal weight): "
              + ", ".join(f"{m} x{v['n_failures']}" for m, v in wf["failed_methods"].items()))
    print(f"\nwindow {wf['window'][0]} -> {wf['window'][1]}, {wf['n_rebalances']} rebalances\n")
    print(t.round(3).to_string())
    t.to_csv(LATEST / "constructions_stats.csv")
    v = pd.DataFrame(wf["versus"]).T
    cols = [c for c in ("monthly_mean_bps", "t_hac", "win_rate", "n_months", "claim") if c in v.columns]
    if len(v) and cols:
        v = v.sort_values(cols[0], ascending=False)
        print(f"\n--- vs {wf['baseline']} (monthly, Newey-West t) ---")
        print(v[cols].round(3).to_string())
    print(f"\nwritten: {LATEST}/constructions_walkforward.json, constructions_stats.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
