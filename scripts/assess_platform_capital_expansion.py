"""Run the fixed, fully funded capital comparisons on a pinned platform result.

Usage: python -m scripts.assess_platform_capital_expansion
Reads content-checked SQLite artifacts and frozen source bytes in read-only mode.
No source simulation, optimization, new strategy fit or profile import is run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3

from market_intel.platform.capital_expansion import evaluate_capital_expansion


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_artifact(state_dir, artifact_id):
    db_path = Path(state_dir) / "performance.sqlite"
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM performance_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    if row is None or "perf_" + _sha(row[0].encode()) != artifact_id:
        raise ValueError("missing or checksum-mismatched frozen performance artifact")
    artifact = json.loads(row[0])
    artifact["artifact_id"] = artifact_id
    for source in artifact["sources"]:
        frozen = Path(state_dir) / "performance_sources" / source["sha256"]
        if _sha(frozen.read_bytes()) != source["sha256"]:
            raise ValueError("frozen performance source bytes do not match artifact")
    return artifact


def decimal_reconstruction(artifacts, scenario, fee_bps):
    """Independent cash-and-account recurrence; does not call production code."""
    with localcontext() as ctx:
        ctx.prec = 60
        D = lambda value: Decimal(str(value))
        by_month = [{r["month"]: D(r["return"]) for r in a["rows"]} for a in artifacts]
        schedule = {d["month"]: [D(w) for w in d["weights"]] + [D(d["cash_weight"])] for d in scenario["decisions"]}
        holdings = [Decimal(0)] * (len(artifacts) + 1)
        nav, total_fees = Decimal(1), Decimal(0)
        evidence, max_error = [], Decimal(0)
        for i, observed in enumerate(scenario["rows"]):
            month, opening = observed["month"], nav
            fee, gross_transfer = Decimal(0), Decimal(0)
            if month in schedule:
                weights = schedule[month]
                if i:
                    gross_transfer = sum(abs(weights[j] * opening - holdings[j]) for j in range(len(artifacts)))
                    fee = gross_transfer * D(fee_bps) / Decimal(10000)
                holdings = [w * (opening - fee) for w in weights]
            funded_opening = holdings.copy()
            pnl = [holdings[j] * by_month[j][month] for j in range(len(artifacts))]
            holdings = [holdings[j] + pnl[j] for j in range(len(artifacts))] + [holdings[-1]]
            nav, total_fees = sum(holdings), total_fees + fee
            error = abs(nav - D(observed["nav"]))
            max_error = max(max_error, error)
            evidence.append({"month": month, "opening_nav": str(opening), "funded_strategy_capital": [str(x) for x in funded_opening[:-1]],
                             "unallocated_cash": str(funded_opening[-1]), "strategy_pnl": [str(x) for x in pnl],
                             "transfer_notional": str(gross_transfer), "allocation_fee": str(fee), "closing_nav": str(nav),
                             "production_nav": str(observed["nav"]), "absolute_nav_difference": str(error)})
        if max_error > Decimal("1e-12") or abs(total_fees - D(scenario["allocation_cost"])) > Decimal("1e-12"):
            raise ArithmeticError("independent Decimal recurrence does not reconcile")
        return {"scenario_id": scenario["id"], "precision_digits": 60, "tolerance": "1e-12", "passed": True,
                "max_nav_difference": str(max_error), "final_nav": str(nav), "allocation_fee_total": str(total_fees),
                "method": "Independently fund target accounts net of fees, grow each frozen monthly NAV return, retain separate unallocated cash, then sum account balances.",
                "monthly_reconstruction": evidence}


def main():
    from market_intel import config
    from market_intel.platform.allocation import CALCULATOR_HASH as BASE_CODE_HASH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=config.DATA_DIR / "platform")
    parser.add_argument("--base", type=Path, default=Path("outputs/platform_measurement_20260904/risk_budget.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/platform_exposure_20260904/capital_expansion.json"))
    args = parser.parse_args()
    raw = args.base.read_bytes()
    base = json.loads(raw)
    payload = {k: v for k, v in base.items() if k != "analysis_id"}
    expected = "allocation_" + _sha(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())
    if expected != base["analysis_id"]:
        raise ValueError("base allocation result checksum mismatch")
    if base["calculator"]["code_sha256"] != BASE_CODE_HASH:
        raise ValueError("base allocation calculator has changed; use a newly verified baseline")
    if base["start"] != "2025-02" or base["end"] != "2026-07":
        raise ValueError("this evidence run is pinned to February 2025 through July 2026")
    artifacts = [_read_artifact(args.state_dir, s["artifact_id"]) for s in base["selected"]]
    output = evaluate_capital_expansion(artifacts, base)
    independent = decimal_reconstruction(artifacts, output["scenarios"][-1], output["request"]["allocation_cost_bps"])
    output["independent_reconstruction"] = independent
    output["evidence_created_at"] = datetime.now(timezone.utc).isoformat()
    output["source_result"] = {"name": str(args.base.resolve()), "sha256": _sha(raw), "analysis_id": base["analysis_id"]}
    output["source_artifacts"] = [{"artifact_id": a["artifact_id"], "strategy_id": a["strategy_id"], "variant": a["variant"], "sources": a["sources"]} for a in artifacts]
    output["script_sha256"] = _sha(Path(__file__).read_bytes())
    # Keep the pure result ID as its lineage; hash the enriched evidence separately.
    output["evidence_sha256"] = _sha(json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output.resolve()), "sha256": _sha(args.output.read_bytes()),
                      "scenarios": [{"id": x["id"], "metrics": x["metrics"], "risk_contribution": x["risk_contribution"],
                                     "latest_weights": x["latest_weights"], "latest_cash_weight": x["latest_cash_weight"]} for x in output["scenarios"]],
                      "decimal_max_nav_difference": independent["max_nav_difference"]}, indent=2))


if __name__ == "__main__":
    main()
