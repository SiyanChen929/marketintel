"""One-off migration: register the existing outputs as the approved baseline run without rerunning the pipeline.

* rewrites outputs/latest/snapshot.json to the canonical form (executable target weights + cash, model and max-Sharpe
  alternatives kept alongside; the original file is archived as snapshot_original_max_sharpe.json)
* writes run.json / data_manifest.json, adds run_id columns to the history tables (no row is deleted)
* registers the 13:04 same-day run (snapshot_prev.json, previous four-factor model) as *superseded* and the
  18:13 five-pillar run as the *approved* baseline in outputs/history/runs/index.json
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_semi_intel import runs                                              # noqa: E402
from ai_semi_intel.config.settings import CONFIG, DATA_CACHE, FRED_SERIES, HISTORY, LATEST  # noqa: E402
from ai_semi_intel.utils.io import read_json, write_json                    # noqa: E402


def main() -> int:
    summ = read_json(LATEST / "summary.json"); snap = read_json(LATEST / "snapshot.json"); prev = read_json(LATEST / "snapshot_prev.json")
    if not summ or not snap:
        print("no outputs to migrate"); return 1
    if snap.get("run_id") and "cash" in snap:
        print("already migrated:", snap["run_id"]); return 0
    w = pd.read_csv(LATEST / "portfolio_weights.csv", index_col=0)
    asof = snap["date"]
    gen_dt = datetime.fromisoformat(summ["generated_at"]).astimezone()
    base_id = f"{asof.replace('-', '')}T{gen_dt:%H%M%S}-baseline"
    base = summ["config"]["portfolio"]["primary"]; cash = float(summ["recommended_cash"])
    rdir = runs.runs_dir(HISTORY) / base_id; rdir.mkdir(parents=True, exist_ok=True)
    write_json(snap, rdir / "snapshot_original_max_sharpe.json")
    # ---- the superseded same-day run (13:04, previous model) --------------------------------------------
    prev_id = None
    index = runs.load_index(HISTORY)
    if prev and prev.get("date") == asof and prev.get("top") != snap.get("top"):
        pm = datetime.fromtimestamp((LATEST / "snapshot_prev.json").stat().st_mtime).astimezone()
        prev_id = f"{asof.replace('-', '')}T{pm:%H%M%S}-superseded"
        pdir = runs.runs_dir(HISTORY) / prev_id; pdir.mkdir(parents=True, exist_ok=True)
        prev_snap = {**prev, "asof_session": asof, "run_id": prev_id, "portfolio_id": runs.PORTFOLIO_ID, "generated_at": pm.isoformat(timespec="seconds"),
                     "weights_max_sharpe": prev["weights"], "legacy_weights_basis": "max_sharpe_fully_invested",
                     "note": "previous four-factor model; only max-Sharpe weights were stored (legacy snapshot_prev.json)"}
        write_json(prev_snap, pdir / "snapshot.json")
        write_json({"run_id": prev_id, "asof_session": asof, "generated_at": prev_snap["generated_at"], "status": "superseded", "superseded_by": base_id,
                    "note": prev_snap["note"], "validation_status": runs.VALIDATION_STATUS}, pdir / "run.json")
        index.append({"run_id": prev_id, "asof_session": asof, "generated_at": prev_snap["generated_at"], "status": "superseded", "superseded_by": base_id,
                      "supersedes": None, "approved_prior_run_id": None, "note": prev_snap["note"]})
        runs.save_index(index, HISTORY)
    # ---- canonical snapshot + run manifest ---------------------------------------------------------------
    new_snap = {**snap, "asof_session": asof, "run_id": base_id, "portfolio_id": runs.PORTFOLIO_ID, "generated_at": gen_dt.isoformat(timespec="seconds"),
                "supersedes": prev_id, "construction": base, "vol_target": float(CONFIG.portfolio.vol_target), "scale": 1.0 - cash, "cash": cash,
                "weights": w["recommended"].to_dict(), "weights_model": w[base].to_dict(), "weights_max_sharpe": w["max_sharpe"].to_dict(),
                "validation_status": runs.VALIDATION_STATUS}
    opt = {"recommended": {"weights": w["recommended"], "cash": cash, "base": base, "scale": 1.0 - cash}, base: {"weights": w[base]},
           "max_sharpe": {"weights": w["max_sharpe"]}, "min_vol": {"weights": w["min_vol"]}, "equal_weight": {"weights": w["equal_weight"]}}
    px = pd.read_parquet(DATA_CACHE / "prices" / "prices_universe.parquet")
    panel = {}
    for sid in FRED_SERIES:
        fp = DATA_CACHE / "fred" / f"{sid}.csv"
        if fp.exists():
            panel[sid] = pd.read_csv(fp, index_col=0, parse_dates=True).iloc[:, 0]
    manifest = runs.data_manifest(px, snap["top"], panel)
    validation = runs.validation_summary()
    changes = {"prev_date": None, "prev_run_id": None, "basis": "first_run", "entries": [], "exits": [], "rank_changes": {}, "weight_changes": {}, "turnover": None,
               "cash_change": None, "supersedes": prev_id}
    if prev_id:
        cur_set, prev_set = set(snap["top"]), set(prev["top"])
        changes["rerun_diff"] = {"entries": sorted(cur_set - prev_set), "exits": sorted(prev_set - cur_set), "weight_changes": None, "cash_change": None,
                                 "note": "vs the 13:04 run under the previous four-factor model; weights are not comparable (legacy run stored max-Sharpe weights only)"}
        changes["legacy_turnover_same_day"] = summ.get("changes", {}).get("turnover")
    run = runs.build_run(new_snap, opt, manifest, validation, changes, data_cutoff={"prices": str(px.index[-1].date()), "session": asof,
                         "fetched_at": {k: v.get("fetched_at") for k, v in manifest["entries"].items()}}, returns_info=summ.get("returns_info"))
    run["note"] = "baseline registered by scripts/migrate_runs.py from the 2026-09-02 18:13 outputs (five-pillar model); approved as the initial baseline"
    write_json(manifest, LATEST / "data_manifest.json")
    write_json(run, LATEST / "run.json")
    summ.update(run_id=base_id, portfolio_id=runs.PORTFOLIO_ID, supersedes=prev_id, validation_status=runs.VALIDATION_STATUS, validation=validation,
                actionable=run["actionable"], not_actionable_reasons=run["not_actionable_reasons"], data_health=run["data_health"], changes=changes,
                config_hash=run["config_hash"], code_hash=run["code_hash"], data_manifest_hash=run["data_manifest_hash"])
    write_json(summ, LATEST / "summary.json")
    write_json(new_snap, LATEST / "snapshot.json")
    # kpi_table: canonical executable weight + model weight
    kpi = pd.read_csv(LATEST / "kpi_table.csv", index_col=0)
    kpi["weight_max_sharpe"] = kpi["weight"]; kpi["weight_model"] = w[base].reindex(kpi.index); kpi["weight"] = w["recommended"].reindex(kpi.index)
    kpi.to_csv(LATEST / "kpi_table.csv")
    # history tables: run_id column, weights relabelled, nothing deleted
    rp = HISTORY / "runs.csv"
    if rp.exists():
        df = pd.read_csv(rp)
        if "run_id" not in df.columns:
            df.insert(0, "run_id", base_id)
            df = df.rename(columns={"weight": "weight_max_sharpe"})
            df["weight"] = df["ticker"].map(w["recommended"]); df["weight_model"] = df["ticker"].map(w[base])
            df.to_csv(rp, index=False)
    ph = HISTORY / "portfolio_history.csv"
    if ph.exists():
        df = pd.read_csv(ph)
        if "run_id" not in df.columns:
            df.insert(0, "run_id", base_id); df["cash"] = cash; df["turnover_basis"] = "same_day_rerun_legacy_not_comparable"; df["prev_run_id"] = prev_id
            df.to_csv(ph, index=False)
    up = HISTORY / "universe_panel.csv"
    if up.exists():
        df = pd.read_csv(up)
        if "run_id" not in df.columns:
            df.insert(0, "run_id", base_id); df.to_csv(up, index=False)
    # register the baseline run directory (copies latest/*), then approve it
    hdir = HISTORY / asof
    for f in ("run.json",):
        shutil.copy(LATEST / f, hdir / f)
    runs.register(run, LATEST, HISTORY, extra_files={"consensus_snapshot.json": hdir / "consensus_snapshot.json", "strategic_snapshot.json": hdir / "strategic_snapshot.json",
                                                    "data_manifest.json": LATEST / "data_manifest.json", "live_validation.json": LATEST / "live_validation.json",
                                                    "backtest_cumulative.csv": LATEST / "backtest_cumulative.csv"})
    runs.approve(base_id, HISTORY, note=run["note"])
    for r in runs.load_index(HISTORY):
        print(f"{r['run_id']:34s} {r['asof_session']} {r['status']:10s} supersedes={r.get('supersedes')} superseded_by={r.get('superseded_by')}")
    print("actionable:", run["actionable"], run["not_actionable_reasons"]); print("reconciles:", run["portfolio"]["reconciles"], "cash", cash)
    return 0


if __name__ == "__main__" and len(sys.argv) == 1:
    sys.exit(main())


# ----------------------------------------------------------------------------- historical per-date folders
def register_history(date: str) -> str | None:
    """Register outputs/history/<date>/ (a pre-migration 'latest of the day' folder) as an immutable run of that session."""
    hdir = HISTORY / date
    summ = read_json(hdir / "summary.json"); snap = read_json(hdir / "snapshot.json")
    if not summ or not snap or not (hdir / "portfolio_weights.csv").exists():
        print("nothing to register for", date); return None
    index = runs.load_index(HISTORY)
    gen_dt = datetime.fromisoformat(summ["generated_at"]).astimezone()
    run_id = f"{date.replace('-', '')}T{gen_dt:%H%M%S}-history"
    if any(r["run_id"] == run_id for r in index):
        print("already registered", run_id); return run_id
    w = pd.read_csv(hdir / "portfolio_weights.csv", index_col=0)
    base = summ["config"]["portfolio"]["primary"]; cash = float(summ["recommended_cash"])
    new_snap = {**snap, "asof_session": date, "run_id": run_id, "portfolio_id": runs.PORTFOLIO_ID, "generated_at": gen_dt.isoformat(timespec="seconds"), "supersedes": None,
                "construction": base, "vol_target": float(CONFIG.portfolio.vol_target), "scale": 1.0 - cash, "cash": cash, "weights": w["recommended"].to_dict(),
                "weights_model": w[base].to_dict(), "weights_max_sharpe": w["max_sharpe"].to_dict(), "validation_status": runs.VALIDATION_STATUS,
                "note": "registered from the per-date history folder; the same-day 13:04 run (previous four-factor model) was not archived and is not comparable"}
    opt = {"recommended": {"weights": w["recommended"], "cash": cash, "base": base, "scale": 1.0 - cash}, base: {"weights": w[base]},
           "max_sharpe": {"weights": w["max_sharpe"]}, "min_vol": {"weights": w["min_vol"]}, "equal_weight": {"weights": w["equal_weight"]}}
    manifest = {"status": "unknown", "entries": {}, "price_gaps": {}, "stale": [], "hash": None, "note": "no data manifest was recorded for pre-migration runs"}
    run = runs.build_run(new_snap, opt, manifest, runs.validation_summary(), {"prev_run_id": None, "basis": "not_recorded", "turnover": summ.get("changes", {}).get("turnover")},
                         config_h=None, code_h="pre-migration", returns_info=summ.get("returns_info"))
    run["note"] = new_snap["note"]; run["actionable"] = None; run["not_actionable_reasons"] = ["data health not recorded for pre-migration runs"]
    rdir = runs.runs_dir(HISTORY) / run_id; rdir.mkdir(parents=True, exist_ok=True)
    for f in hdir.glob("*"):
        shutil.copy(f, rdir / f.name)
    write_json(snap, rdir / "snapshot_original_max_sharpe.json")
    write_json(new_snap, rdir / "snapshot.json"); write_json(run, rdir / "run.json")
    index.append({"run_id": run_id, "asof_session": date, "generated_at": run["generated_at"], "status": "approved", "supersedes": None, "approved_prior_run_id": None,
                  "actionable": None, "turnover": None, "code_hash": "pre-migration", "config_hash": None, "data_manifest_hash": None, "note": new_snap["note"]})
    runs.save_index(index, HISTORY)
    print("registered", run_id, "approved")
    return run_id


def recompute_current_changes() -> None:
    """Re-derive the current run's turnover against the approved prior run once history has been registered."""
    from ai_semi_intel.dashboard import monitoring
    cur = read_json(LATEST / "snapshot.json"); run = read_json(LATEST / "run.json"); summ = read_json(LATEST / "summary.json")
    index = runs.load_index(HISTORY)
    prior = runs.approved_prior(index, cur["asof_session"])
    if not prior:
        print("no approved prior"); return
    prev = runs.load_run_snapshot(prior["run_id"], HISTORY)
    ch = monitoring.compare_runs(cur, prev); ch.update(basis="approved_prior_run", prev_run_id=prior["run_id"])
    run["approved_prior_run_id"] = prior["run_id"]; run["changes"] = {k: ch.get(k) for k in ("prev_run_id", "basis", "turnover", "entries", "exits", "supersedes", "rerun_turnover")}
    summ["changes"] = ch
    write_json(run, LATEST / "run.json"); write_json(summ, LATEST / "summary.json")
    rdir = runs.runs_dir(HISTORY) / run["run_id"]
    write_json(run, rdir / "run.json"); write_json(summ, rdir / "summary.json")
    for r in index:
        if r["run_id"] == run["run_id"]:
            r["approved_prior_run_id"], r["turnover"] = prior["run_id"], ch["turnover"]
    runs.save_index(index, HISTORY)
    ph = HISTORY / "portfolio_history.csv"
    if ph.exists():
        df = pd.read_csv(ph)
        m = df["run_id"] == run["run_id"]
        df.loc[m, ["turnover", "turnover_basis", "prev_run_id"]] = [ch["turnover"], "approved_prior_run", prior["run_id"]]
        df.to_csv(ph, index=False)
    print(f"current run {run['run_id']}: turnover {ch['turnover']:.2%} vs approved prior {prior['run_id']} ({len(ch['entries'])} in / {len(ch['exits'])} out)")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--history":
    for d in sys.argv[2:]:
        register_history(d)
    recompute_current_changes()
    for r in runs.load_index(HISTORY):
        print(f"{r['run_id']:34s} {r['asof_session']} {r['status']:10s} prior={r.get('approved_prior_run_id')} turnover={r.get('turnover')}")
