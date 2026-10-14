"""Run and independently reconcile the platform's fixed exposure grid."""
from decimal import Decimal, localcontext
import hashlib
import json

from market_intel import config
from market_intel.platform.performance import PerformanceStore
from scripts.assess_platform_capital_expansion import decimal_reconstruction


def main():
    out = config.PROJECT_ROOT / "outputs/platform_exposure_20260904"
    out.mkdir(parents=True, exist_ok=True)
    previous = config.PROJECT_ROOT / "outputs/platform_review_20260904"
    store = PerformanceStore(config.DATA_DIR / "platform/performance.sqlite")
    ids = [json.loads((previous / f"{recipe}_import.json").read_text())[0]["artifact_id"] for recipe in ("vol", "commodity", "msci", "ps")]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (store.path, store.activity.path)}
    base = store.analyze({"method": "activity_risk_budget", "artifact_ids": ids, "currency_mode": "constant_fx", "allocation_cost_bps": 0})
    report = store.exposure_report({"base_request": base["request"], "base_analysis_id": base["analysis_id"]})
    assert store.exposure_report(report["request"]) == report
    artifacts = [store.get(i) for i in ids]
    profiles = [store.activity.get(i) for i in base["request"]["profile_ids"]]
    checks = []
    for scenario in report["position_scenarios"]:
        # Independent high-precision observation -> month linking; these
        # temporary series are never imported as strategy artifacts.
        transformed = []
        for artifact, profile in zip(artifacts, profiles):
            with localcontext() as context:
                context.prec = 60
                multiplier = Decimal(str(scenario["request"]["multipliers"][artifact["strategy_id"]]))
                levels = {row["month"]: Decimal(1) for row in scenario["rows"]}
                for row in profile["rows"]:
                    month = row["date"][:7]
                    if month in levels:
                        levels[month] *= 1 + multiplier * Decimal(str(row["return"]))
                transformed.append({**artifact, "rows": [{"month": m, "return": str(level - 1)} for m, level in levels.items()]})
        check = decimal_reconstruction(transformed, scenario, 0)
        checks.append({k: v for k, v in check.items() if k != "monthly_reconstruction"})
    capital_check = decimal_reconstruction(artifacts, report["capital_scenarios"]["scenarios"][-1], 0)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == before[p.name] for p in (store.path, store.activity.path))
    checks.append({k: v for k, v in capital_check.items() if k != "monthly_reconstruction"})
    (out / "base_policy.json").write_text(json.dumps(base, indent=2, ensure_ascii=False))
    (out / "exposure_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    evidence = {"validation_status": "NOT VALIDATED", "report_id": report["report_id"], "base_analysis_id": base["analysis_id"],
                "pinned_report_exact_replay": True, "state_hashes_unchanged": before, "decimal_reconstructions": checks,
                "source_scope": "Frozen daily/event NAV returns and source-declared clocks. Source trades, prospective edge, financing, margin and executable capacity are not certified.",
                "code_sha256": {str(p): hashlib.sha256((config.PROJECT_ROOT / p).read_bytes()).hexdigest() for p in (
                    "market_intel/platform/performance.py", "market_intel/platform/exposure.py", "market_intel/platform/capital_expansion.py",
                    "market_intel/platform/event_sensitivity.py", "scripts/verify_platform_exposure.py")}}
    (out / "verification.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
    print(json.dumps({"report_id": report["report_id"], "checks": checks,
                      "position_scenarios": [{"label": s["label"], "metrics": s["metrics"]} for s in report["position_scenarios"]]}, indent=2))


if __name__ == "__main__":
    main()
