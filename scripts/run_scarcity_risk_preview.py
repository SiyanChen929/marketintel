#!/usr/bin/env python3
"""Re-score the latest saved universe without fetching new market data."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_semi_intel.analysis import scoring, strategic
from ai_semi_intel.config.settings import LATEST, OUTPUTS
from ai_semi_intel.utils.io import write_json


def main() -> None:
    source = LATEST / "universe_scores.csv"
    if not source.exists():
        raise FileNotFoundError(f"Run the daily pipeline first; missing {source}")
    summary_path = LATEST / "summary.json"
    run_summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    asof = pd.Timestamp(run_summary.get("asof") or datetime.today().date()).to_pydatetime()
    raw = pd.read_csv(source, index_col=0)

    # Permit repeated previews against outputs created by either score-system version.
    derived_prefixes = ("score_scarcity", "score_risk_", "score_market_", "score_financial_", "score_valuation_",
                        "coverage_market_", "coverage_financial_", "coverage_valuation_")
    derived_exact = {"alpha_score", "scarcity_coverage", "risk_coverage", "risk_flags", "risk_flag_count",
                     "decision_score_status", "decision_coverage", "rank_alpha"}
    raw = raw.drop(columns=[c for c in raw if c in derived_exact or c.startswith(derived_prefixes)], errors="ignore")
    strategic_cols = [c for c in raw if c.startswith("strategic_") or c.startswith("strat_") or c in {"scarcity_raw", "news_direction"}]
    raw = raw.drop(columns=strategic_cols, errors="ignore")
    raw = raw.join(strategic.strategic_features(list(raw.index), asof))
    scored = scoring.score_universe(raw)
    decision = scoring.select_top(scored)

    alpha_view = scored.copy()
    alpha_view["composite"] = alpha_view["alpha_score"]
    alpha = scoring.select_top(alpha_view)

    out_dir = OUTPUTS / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "scarcity_risk_score_preview.csv"
    keep = ["name", "segment", "eligible", "score_value", "score_growth", "score_momentum",
            "score_scarcity", "score_risk", "alpha_score", "scarcity_coverage",
            "score_market_resilience", "score_financial_resilience", "score_valuation_resilience",
            "score_risk_resilience", "risk_coverage", "risk_flags", "composite", "rank_all", "rank_alpha",
            "strategic_asof", "strategic_source", "decision_score_status"]
    scored[[c for c in keep if c in scored]].sort_values("composite", ascending=False).to_csv(out_csv)

    out_json = out_dir / "scarcity_risk_score_summary.json"
    write_json({
        "asof": str(pd.Timestamp(asof).date()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "NOT VALIDATED",
        "scope": "snapshot preview only; alpha score has historical evidence, combined decision score does not",
        "weights": {"growth": 0.375, "momentum": 0.2625, "value": 0.1125,
                    "scarcity": 0.15, "risk_resilience": 0.10},
        "universe_size": int(len(scored)),
        "scarcity_names_with_nonzero_confidence": int((scored["scarcity_coverage"] > 0).sum()),
        "median_risk_coverage": float(scored["risk_coverage"].median()),
        "top15_decision": list(decision.index),
        "top15_alpha": list(alpha.index),
        "decision_entries_vs_alpha": sorted(set(decision.index) - set(alpha.index)),
        "decision_exits_vs_alpha": sorted(set(alpha.index) - set(decision.index)),
        "detail_csv": str(out_csv),
    }, out_json)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_json}")


if __name__ == "__main__":
    main()
