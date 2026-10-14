"""Freeze activity profiles for existing, revealed platform research artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from market_intel import config
from market_intel.platform.activity import ActivityStore, summarize
from market_intel.platform.performance import PerformanceStore
from scripts.platform_continuous_activity import build_continuous_profile
from scripts.platform_event_activity import build_event_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=config.DATA_DIR / "platform")
    parser.add_argument("--artifact-id", action="append")
    parser.add_argument("--output", type=Path, default=Path("outputs/platform_measurement_20260904/activity_import.json"))
    args = parser.parse_args()
    performance = PerformanceStore(args.state_dir / "performance.sqlite")
    activity = ActivityStore(args.state_dir / "activity.sqlite")
    ids = args.artifact_id or [v["artifact_id"] for s in performance.catalog()["strategies"] for v in s["variants"]]
    recipes = {"vol_trading": ("vol", build_continuous_profile), "pairs_trading": ("ps", build_continuous_profile),
               "commodity_arbitrage": ("commodity", build_event_profile), "msci_rebalance": ("msci", build_event_profile)}
    receipts = []
    for artifact_id in ids:
        artifact = performance.get(artifact_id)
        try:
            if artifact["strategy_id"] not in recipes:
                raise ValueError("no reviewed activity adapter for this strategy")
            recipe, builder = recipes[artifact["strategy_id"]]
            profile = builder(recipe, artifact, args.state_dir / "performance_sources")
            stored = activity.put(profile, artifact)
            receipts.append({"status": "IMPORTED", **summarize(stored, artifact)})
            print(f"IMPORTED {artifact['name']} / {artifact['variant']} / {stored['profile_id']}")
        except (ValueError, KeyError) as exc:
            receipts.append({"status": "BLOCKED", "artifact_id": artifact_id, "strategy_id": artifact["strategy_id"],
                             "variant": artifact["variant"], "reason": str(exc), "validation_status": "NOT VALIDATED"})
            print(f"BLOCKED {artifact['name']} / {artifact['variant']}: {exc}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, allow_nan=False))
    if any(r["status"] != "IMPORTED" for r in receipts):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
