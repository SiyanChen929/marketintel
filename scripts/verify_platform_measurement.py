"""Reproduce the selected activity report and independently check allocation NAV."""
from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import statistics

from market_intel import config
from market_intel.platform.performance import PerformanceStore


def main():
    getcontext().prec = 40
    out = config.PROJECT_ROOT / "outputs/platform_measurement_20260904"
    out.mkdir(parents=True, exist_ok=True)
    previous = config.PROJECT_ROOT / "outputs/platform_review_20260904"
    store = PerformanceStore(config.DATA_DIR / "platform/performance.sqlite")
    ids = [json.loads((previous / f"{recipe}_import.json").read_text())[0]["artifact_id"]
           for recipe in ("vol", "commodity", "msci", "ps")]
    body = {"method": "activity_risk_budget", "artifact_ids": ids, "currency_mode": "constant_fx", "allocation_cost_bps": 0}
    report = store.activity_report({"artifact_ids": ids})
    result = store.analyze(body)
    assert store.analyze(result["request"]) == result
    profiles = [store.activity.get(i) for i in result["request"]["profile_ids"]]
    policy = result["policy"]
    decision_error = 0.
    for d in result["decisions"]:
        risks = []
        for p in profiles:
            rows = [r for r in p["rows"] if d["training_start"] <= r["date"] <= d["training_end"]]
            active = [r["return"] for r in rows if r["active"]]
            minimum = policy["minimum_independent_events"] if p["frequency"] == "event" else policy["minimum_active_daily_observations"]
            if len(active) < minimum:
                risks.append(None)
                continue
            calendar = statistics.stdev([r["return"] for r in rows]) if p["frequency"] == "daily" else 0.
            risks.append(max(calendar, statistics.stdev(active), max(0., -min(active)) / 3., .003))
        eligible = sum(r is not None for r in risks)
        denominator = sum(1 / r for r in risks if r is not None)
        for i, (risk, p) in enumerate(zip(risks, profiles)):
            expected = min((eligible / len(ids)) / risk / denominator, .10 if p["frequency"] == "event" else .40) if risk is not None else 0.
            decision_error = max(decision_error, abs(expected - d["weights"][i]))
        assert decision_error < 1e-12
    # Decimal capital ledger; policy decisions are inputs, source monthly NAVs
    # supply P&L. This independently checks transfers, fee timing and aggregation.
    errors = []
    for fee in (0, 5):
        scenario = store.analyze({**body, "allocation_cost_bps": fee})
        schedules = {d["month"]: d for d in scenario["decisions"]}
        source = [{r["month"]: Decimal(str(r["return"])) for r in store.get(i)["rows"]} for i in ids]
        capital = None
        maximum = 0.
        for row in scenario["rows"]:
            if row["month"] in schedules:
                d = schedules[row["month"]]
                weights = [Decimal(str(w)) for w in d["weights"]]
                weights.append(Decimal(1) - sum(weights))
                nav = sum(capital) if capital is not None else Decimal(1)
                cost = sum(abs(weights[i] * nav - capital[i]) for i in range(len(ids))) * Decimal(fee) / 10000 if capital else Decimal(0)
                capital = [w * (nav - cost) for w in weights]
            for i, series in enumerate(source):
                capital[i] *= 1 + series[row["month"]]
            maximum = max(maximum, abs(float(sum(capital)) - row["nav"]))
        assert maximum < 1e-12
        errors.append({"allocation_cost_bps": fee, "max_nav_error": maximum})
        if fee:
            (out / "risk_budget_5bps.json").write_text(json.dumps(scenario, indent=2, ensure_ascii=False))
    (out / "activity_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (out / "risk_budget.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    evidence = {"validation_status": "NOT VALIDATED", "analysis_id": result["analysis_id"], "policy_sha256": result["policy_sha256"],
                "calculator": result["calculator"], "profile_ids": result["request"]["profile_ids"],
                "decision_max_weight_error": decision_error, "independent_decimal_accounting": errors,
                "pinned_request_exact_reproduction": True,
                "scope": "Frozen return/activity lineage, lagged decisions and funded-capital aggregation only; no new strategy fitting or independent OOS claim.",
                "code_sha256": {str(p): hashlib.sha256((config.PROJECT_ROOT / p).read_bytes()).hexdigest() for p in (
                    "market_intel/platform/performance.py", "market_intel/platform/allocation.py", "market_intel/platform/activity.py",
                    "scripts/platform_event_activity.py", "scripts/platform_continuous_activity.py", "scripts/verify_platform_measurement.py")}}
    (out / "verification.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
    print(json.dumps({"verification": evidence, "metrics": result["metrics"], "latest_weights": result["latest_weights"],
                      "cash": result["latest_cash_weight"]}, indent=2))


if __name__ == "__main__":
    main()
