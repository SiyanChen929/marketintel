"""Nested Optuna/TPE arm versus the fixed ERC control.

The protocol is frozen to outputs/validation/optuna_tpe/protocol.json before a study is run.
Development outer folds are 2019-2023. A single configuration selected with pre-2024 data is
then frozen across the 2024-2026 terminal evaluation. That terminal period is untouched by this
TPE study, but it appeared in older project sweeps and is therefore not globally pristine.

Run:  python scripts/run_optuna_tpe.py --trials 80
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_semi_intel.research import optuna_tpe as OT  # noqa: E402
from ai_semi_intel.research import pit_features as PIT  # noqa: E402
from ai_semi_intel.research import portfolio_sweep as PS  # noqa: E402
from ai_semi_intel.utils.io import read_json, write_json  # noqa: E402

SWEEP = ROOT / "outputs" / "validation" / "portfolio_sweep"
OUT = ROOT / "outputs" / "validation" / "optuna_tpe"
warnings.filterwarnings("ignore", message="Values in x were outside bounds during a minimize step")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def trials_rows(label: str, cutoff: str, tuned: dict) -> list[dict]:
    rows = []
    for trial in tuned["study"].trials:
        rows.append({"study": label, "cutoff": cutoff, "number": int(trial.number),
                     "state": str(trial.state.name), "objective": trial.value,
                     "config": trial.user_attrs.get("config_id"),
                     "max_training_date": trial.user_attrs.get("max_training_date"),
                     **trial.params})
    return rows


def combine_monthly(tpe: dict, erc: dict) -> pd.DataFrame:
    fields = ["entry_date", "exit_date", "config", "gross", "turnover", "cost", "net", "invested", "exante_vol"]
    return tpe["monthly"][fields].add_prefix("tpe_").join(erc["monthly"][fields].add_prefix("erc_"), how="inner")


def manual_reconstruction(label: str, replay: dict, adj: pd.DataFrame, cost_bp: float) -> list[dict]:
    """Recompute first, worst and first-switch months directly from saved targets and raw prices."""
    m, W = replay["monthly"], replay["weights"]
    if m.empty:
        return []
    dates = [m.index[0], m["net"].idxmin()]
    switches = m.index[(m["config"] != m["config"].shift()) & (m.index != m.index[0])]
    if len(switches):
        dates.append(switches[0])
    out = []
    for d in list(dict.fromkeys(dates)):
        pos = m.index.get_loc(d)
        current = W.loc[d]
        previous = pd.Series(dtype=float) if pos == 0 else W.loc[m.index[pos - 1]]
        entry, exit_ = pd.Timestamp(m.loc[d, "entry_date"]), pd.Timestamp(m.loc[d, "exit_date"])
        returns = adj.loc[exit_, current.index] / adj.loc[entry, current.index] - 1
        gross = float((current * returns).sum())
        union = previous.index.union(current.index)
        turnover = float((current.reindex(union).fillna(0) - previous.reindex(union).fillna(0)).abs().sum() / 2)
        cost = float(2 * turnover * cost_bp / 1e4)
        net = gross - cost
        out.append({"arm": label, "date": d, "case": "first" if d == m.index[0] else "worst" if d == m["net"].idxmin() else "switch",
                    "entry_date": entry, "exit_date": exit_, "gross_manual": gross, "gross_engine": m.loc[d, "gross"],
                    "turnover_manual": turnover, "turnover_engine": m.loc[d, "turnover"],
                    "cost_manual": cost, "cost_engine": m.loc[d, "cost"], "net_manual": net, "net_engine": m.loc[d, "net"],
                    "max_abs_diff": float(max(abs(gross - m.loc[d, "gross"]), abs(turnover - m.loc[d, "turnover"]),
                                              abs(cost - m.loc[d, "cost"]), abs(net - m.loc[d, "net"])))})
    return out


def stats_frame(comparison: dict) -> pd.DataFrame:
    return pd.DataFrame({"optuna_tpe": comparison["tpe"], "fixed_erc": comparison["fixed_erc"]}).T


def audit_constraints(replay: dict, cfg: pd.DataFrame, scored: pd.DataFrame,
                      segment_weight_cap: float = 0.35, names_per_segment_cap: int = 3) -> dict:
    """Verify saved target weights against the exact config and segment limits."""
    W, monthly = replay["weights"], replay["monthly"]
    maximum_weight = maximum_segment_weight = 0.0
    maximum_names_per_segment = missing_segment_labels = violations = 0
    for date, row in W.iterrows():
        live = row[row > 1e-10]
        segment = scored[scored["date"] == date].set_index("ticker")["segment"].reindex(live.index)
        missing_segment_labels += int(segment.isna().sum())
        grouped_weight = live.groupby(segment).sum()
        grouped_count = live.groupby(segment).size()
        cid = int(monthly.loc[date, "config"])
        name_cap = float(cfg.loc[cid, "max_weight"])
        row_max = float(live.max()) if len(live) else 0.0
        segment_max = float(grouped_weight.max()) if len(grouped_weight) else 0.0
        count_max = int(grouped_count.max()) if len(grouped_count) else 0
        maximum_weight = max(maximum_weight, row_max)
        maximum_segment_weight = max(maximum_segment_weight, segment_max)
        maximum_names_per_segment = max(maximum_names_per_segment, count_max)
        violations += int(row_max > name_cap + 1e-6)
        violations += int(segment_max > segment_weight_cap + 1e-6)
        violations += int(count_max > names_per_segment_cap)
    return {"months": int(len(W)), "max_weight": maximum_weight,
            "max_segment_weight": maximum_segment_weight,
            "max_names_per_segment": maximum_names_per_segment,
            "min_invested": float(monthly["invested"].min()),
            "max_invested": float(monthly["invested"].max()),
            "missing_segment_labels": missing_segment_labels, "violations": violations}


def audit_trial_boundaries(trials: pd.DataFrame) -> dict:
    cutoff = pd.to_datetime(trials["cutoff"])
    maximum_training_date = pd.to_datetime(trials["max_training_date"])
    bad = maximum_training_date >= cutoff
    return {"trials": int(len(trials)), "studies": int(trials["study"].nunique()),
            "violations": int(bad.sum()),
            "latest_training_date": str(maximum_training_date.max().date())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Nested TPE portfolio construction versus fixed ERC")
    ap.add_argument("--trials", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--cost-bp", type=float, default=20.0)
    ap.add_argument("--force-protocol", action="store_true", help="replace an existing incompatible protocol")
    a = ap.parse_args(argv)
    if a.trials < 20:
        raise ValueError("at least 20 trials are required so TPE has startup and model-guided phases")

    paths = {"net_returns": SWEEP / "net_returns.parquet", "configs": SWEEP / "configs.csv",
             "sweep_manifest": SWEEP / "sweep_manifest.json", "scored": ROOT / "outputs" / "validation" / "scored.parquet",
             "prices": ROOT / "data_cache" / "prices" / "long_adj_close.parquet"}
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(f"missing inputs {missing}; run scripts/run_portfolio_sweep.py first")
    manifest = read_json(paths["sweep_manifest"], {})
    required = {"engine_version": 3, "execution_lag_bars": 1, "cost_bp": a.cost_bp, "segment_weight_cap": 0.35}
    if any(manifest.get(k) != v for k, v in required.items()):
        raise RuntimeError(f"stale sweep manifest {manifest}; expected {required}. Rerun scripts/run_portfolio_sweep.py")

    R = pd.read_parquet(paths["net_returns"]); R.columns = R.columns.astype(int)
    cfg = pd.read_csv(paths["configs"], index_col=0); cfg.index = cfg.index.astype(int)
    scored = pd.read_parquet(paths["scored"]); scored["date"] = pd.to_datetime(scored["date"])
    adj = PIT.load_prices()["adj_close"]
    fixed = PS.default_erc_config(cfg)
    dev_years = [2019, 2020, 2021, 2022, 2023]
    holdout_start, holdout_end = "2024-01-01", "2026-08-01"

    protocol = {
        "protocol_version": 1, "status": "FROZEN_BEFORE_OPTUNA", "sampler": "Optuna TPESampler",
        "optuna_version_required": ">=4.8,<5", "seed": a.seed, "n_trials_per_study": a.trials,
        "search_space": OT.parameter_levels(cfg), "objective": "mean annual net Sharpe across complete inner validation years",
        "inner_first_year": 2016, "min_inner_folds": 3, "development_outer_years": dev_years,
        "terminal_holdout": {"start": holdout_start, "end_exclusive": holdout_end,
                             "rule": "one pre-2024 TPE-selected config, frozen throughout"},
        "fixed_control": {"config": fixed, **OT.config_params(cfg, fixed)},
        "cost": {"bp_per_buy_or_sell_notional": a.cost_bp, "formula": "2 * one_way_turnover * cost_bp / 10000"},
        "constraints": {"max_names_per_segment": 3, "max_segment_weight": 0.35},
        "execution": "month-end close signal; first available daily close after signal; hold to first close after next signal",
        "input_sha256": {k: sha256(v) for k, v in paths.items()},
        "global_holdout_warning": "2024-2026 appeared in earlier project sweeps; it is untouched by this TPE study, not globally pristine",
        "prospective_untouched_oos": {"first_signal_date": "2026-09-30", "status": "UNTOUCHED_NO_RESULTS"},
        "predeclared_verdict_ceiling": "NOT VALIDATED: current-universe survivorship, absent delisting returns, and historical consensus/scarcity gaps remain",
    }
    payload = json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode()
    protocol["protocol_hash"] = hashlib.sha256(payload).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    old = read_json(OUT / "protocol.json")
    if old and old.get("protocol_hash") != protocol["protocol_hash"] and not a.force_protocol:
        raise RuntimeError("existing frozen protocol differs; inspect it or pass --force-protocol for a new experiment")
    write_json(protocol, OUT / "protocol.json")
    print(f"protocol frozen {protocol['protocol_hash'][:12]}; fixed ERC config {fixed}", flush=True)

    choices, trial_rows = {}, []
    for y in dev_years:
        cutoff = f"{y}-01-01"
        tuned = OT.tune_fold(R, cfg, cutoff, n_trials=a.trials, seed=a.seed + y,
                             inner_first_year=2016, min_inner_folds=3, baseline_config=fixed)
        choices[y] = tuned["best_config"]
        trial_rows += trials_rows(f"outer_{y}", cutoff, tuned)
        print(f"outer {y}: config {tuned['best_config']} inner score {tuned['best_score']:.3f} "
              f"from {tuned['unique_configs']} unique trials", flush=True)

    tpe_dev = PS.replay_policy(scored, adj, cfg, choices, "2019-01-01", "2024-01-01", cost_bp=a.cost_bp)
    erc_dev = PS.replay_policy(scored, adj, cfg, fixed, "2019-01-01", "2024-01-01", cost_bp=a.cost_bp)
    dev_cmp = OT.compare_replays(tpe_dev, erc_dev, seed=a.seed)

    final_tune = OT.tune_fold(R, cfg, holdout_start, n_trials=a.trials, seed=a.seed + 2024,
                              inner_first_year=2016, min_inner_folds=3, baseline_config=fixed)
    trial_rows += trials_rows("terminal_pre_2024", holdout_start, final_tune)
    terminal_config = final_tune["best_config"]
    tpe_final = PS.replay_policy(scored, adj, cfg, terminal_config, holdout_start, holdout_end, cost_bp=a.cost_bp)
    erc_final = PS.replay_policy(scored, adj, cfg, fixed, holdout_start, holdout_end, cost_bp=a.cost_bp)
    final_cmp = OT.compare_replays(tpe_final, erc_final, seed=a.seed + 1)

    choice_rows = []
    for y, cid in choices.items():
        choice_rows.append({"outer_year": y, "config": cid, **OT.config_params(cfg, cid)})
    pd.DataFrame(choice_rows).to_csv(OUT / "nested_fold_choices.csv", index=False)
    trials = pd.DataFrame(trial_rows)
    trials.to_csv(OUT / "trials.csv", index=False)
    combine_monthly(tpe_dev, erc_dev).to_csv(OUT / "development_oos_monthly.csv")
    combine_monthly(tpe_final, erc_final).to_csv(OUT / "terminal_oos_monthly.csv")
    tpe_dev["weights"].to_parquet(OUT / "development_tpe_weights.parquet")
    erc_dev["weights"].to_parquet(OUT / "development_erc_weights.parquet")
    tpe_final["weights"].to_parquet(OUT / "terminal_tpe_weights.parquet")
    erc_final["weights"].to_parquet(OUT / "terminal_erc_weights.parquet")
    stats_frame(dev_cmp).to_csv(OUT / "development_oos_stats.csv")
    stats_frame(final_cmp).to_csv(OUT / "terminal_oos_stats.csv")

    manual = pd.DataFrame(manual_reconstruction("development_tpe", tpe_dev, adj, a.cost_bp)
                          + manual_reconstruction("development_erc", erc_dev, adj, a.cost_bp)
                          + manual_reconstruction("terminal_tpe", tpe_final, adj, a.cost_bp)
                          + manual_reconstruction("terminal_erc", erc_final, adj, a.cost_bp))
    manual.to_csv(OUT / "manual_reconstruction.csv", index=False)
    if manual.empty or manual["max_abs_diff"].max() > 1e-12:
        raise AssertionError(f"manual reconstruction failed: {manual['max_abs_diff'].max() if len(manual) else 'empty'}")

    qa = {
        "return_matrix": {"months": int(len(R)), "configs": int(len(R.columns)),
                          "missing_returns": int(R.isna().sum().sum()),
                          "duplicate_dates": int(R.index.duplicated().sum())},
        "trial_boundaries": audit_trial_boundaries(trials),
        "constraints": {
            "development_tpe": audit_constraints(tpe_dev, cfg, scored),
            "development_fixed_erc": audit_constraints(erc_dev, cfg, scored),
            "terminal_tpe": audit_constraints(tpe_final, cfg, scored),
            "terminal_fixed_erc": audit_constraints(erc_final, cfg, scored),
        },
        "manual_accounting": {"sampled_months": int(len(manual)),
                              "max_abs_diff": float(manual["max_abs_diff"].max())},
    }
    qa_violations = (qa["return_matrix"]["missing_returns"] + qa["return_matrix"]["duplicate_dates"]
                     + qa["trial_boundaries"]["violations"]
                     + sum(x["violations"] + x["missing_segment_labels"]
                           for x in qa["constraints"].values()))
    qa["total_violations"] = int(qa_violations)
    write_json(qa, OUT / "qa.json")
    if qa_violations:
        raise AssertionError(f"research-arm QA failed with {qa_violations} violations; inspect {OUT / 'qa.json'}")

    summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "research_status": "NOT VALIDATED", "protocol_hash": protocol["protocol_hash"],
               "fixed_erc_config": fixed, "development": dev_cmp,
               "terminal": {**final_cmp, "tpe_config": terminal_config,
                            "tpe_params": OT.config_params(cfg, terminal_config),
                            "training_max_date": final_tune["training_max_date"],
                            "inner_years": final_tune["inner_years"], "inner_best_score": final_tune["best_score"]},
               "manual_reconstruction_max_abs_diff": float(manual["max_abs_diff"].max()), "qa": qa,
               "blocking_limitations": ["current-universe survivorship bias", "no delisting terminal returns",
                                         "historical consensus and scarcity inputs absent",
                                         "terminal period is not globally pristine because older sweeps used it"]}
    write_json(summary, OUT / "summary.json")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
        for ax, title, aa, bb in ((axes[0], "Nested development OOS", tpe_dev, erc_dev),
                                  (axes[1], "Arm-local terminal OOS", tpe_final, erc_final)):
            for name, replay, color in (("Optuna/TPE", aa, "#c65d21"), ("Fixed ERC", bb, "#1f4e79")):
                growth = (1 + replay["monthly"]["net"]).cumprod()
                ax.plot(growth.index, growth, label=name, color=color, lw=2)
            locator = mdates.AutoDateLocator(minticks=4, maxticks=7)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            ax.set_title(title); ax.set_yscale("log"); ax.grid(alpha=.25); ax.legend(frameon=False)
        fig.suptitle("Portfolio construction research arms — net of identical costs")
        fig.tight_layout(); fig.savefig(OUT / "comparison.png", dpi=160); plt.close(fig)
    except Exception as exc:
        print("chart skipped:", exc)

    audit = f"""# Optuna/TPE nested research arm audit

Generated: {summary['generated_at']}  
Protocol: `{protocol['protocol_hash']}`  
Verdict: **NOT VALIDATED**

## Frozen design

- Control: fixed ERC, 15 names, 756-day covariance, 15% cap, 25% volatility target.
- TPE: {a.trials} seeded trials per study over the existing 1,008 discrete configurations.
- Inner objective: mean annual net Sharpe across complete chronological validation years from 2016.
- Development outer folds: 2019-2023; outer returns never enter their fold's study.
- Terminal evaluation: one configuration selected using data through 2023-12-31 and frozen for 2024-01 through 2026-07.
- Execution: next available close after each month-end signal; costs: {a.cost_bp:.0f} bp on buys and sells; turnover uses the full holdings union.

## Results

| Window | Arm | Net Sharpe | CAGR | Vol | Max DD | Avg turnover | Total cost return |
|---|---|---:|---:|---:|---:|---:|---:|
| Development OOS | TPE | {dev_cmp['tpe']['net_sharpe']:.3f} | {dev_cmp['tpe']['net_cagr']:.1%} | {dev_cmp['tpe']['net_vol']:.1%} | {dev_cmp['tpe']['net_max_dd']:.1%} | {dev_cmp['tpe']['avg_turnover']:.1%} | {dev_cmp['tpe']['total_cost_return']:.1%} |
| Development OOS | Fixed ERC | {dev_cmp['fixed_erc']['net_sharpe']:.3f} | {dev_cmp['fixed_erc']['net_cagr']:.1%} | {dev_cmp['fixed_erc']['net_vol']:.1%} | {dev_cmp['fixed_erc']['net_max_dd']:.1%} | {dev_cmp['fixed_erc']['avg_turnover']:.1%} | {dev_cmp['fixed_erc']['total_cost_return']:.1%} |
| Terminal OOS | TPE | {final_cmp['tpe']['net_sharpe']:.3f} | {final_cmp['tpe']['net_cagr']:.1%} | {final_cmp['tpe']['net_vol']:.1%} | {final_cmp['tpe']['net_max_dd']:.1%} | {final_cmp['tpe']['avg_turnover']:.1%} | {final_cmp['tpe']['total_cost_return']:.1%} |
| Terminal OOS | Fixed ERC | {final_cmp['fixed_erc']['net_sharpe']:.3f} | {final_cmp['fixed_erc']['net_cagr']:.1%} | {final_cmp['fixed_erc']['net_vol']:.1%} | {final_cmp['fixed_erc']['net_max_dd']:.1%} | {final_cmp['fixed_erc']['avg_turnover']:.1%} | {final_cmp['fixed_erc']['total_cost_return']:.1%} |

Terminal TPE minus ERC: annualized mean return {final_cmp['paired']['mean_diff_annualized']:.1%}, Newey-West t {final_cmp['paired']['newey_west_t']:.2f}; Sharpe difference {final_cmp['paired']['sharpe_diff']:.3f}, 95% moving-block bootstrap CI [{final_cmp['paired']['ci_lo']:.3f}, {final_cmp['paired']['ci_hi']:.3f}].

Selected terminal config: `{terminal_config}` — `{json.dumps(OT.config_params(cfg, terminal_config), sort_keys=True)}`.

## Audit evidence and limits

- Fold boundary: every trial records `max_training_date`; it is strictly before the outer cutoff.
- Accounting: sampled first, losing and switch months independently reconstruct gross return, full-union turnover, cost and net return; maximum absolute difference `{manual['max_abs_diff'].max():.3g}`.
- Constraints: all saved targets pass their config's name cap, the 35% segment-weight cap and the three-names-per-segment cap; total QA violations `{qa_violations}`.
- Data keys: the regenerated matrix has {len(R)} unique monthly signal dates and {len(R.columns)} configurations with {int(R.isna().sum().sum())} missing returns.
- The terminal window is untouched by this TPE study, but older grid research already reported it. It is arm-local OOS, not a globally pristine final test.
- Current-universe survivorship, absent delisting returns, and missing historical consensus/scarcity remain major evidence gaps. They cap the verdict at `NOT VALIDATED` regardless of the performance comparison.
- A genuinely untouched prospective comparison is frozen from the 2026-09-30 signal date; it currently has no results.
"""
    (OUT / "audit.md").write_text(audit)

    print("\nDevelopment OOS")
    print(stats_frame(dev_cmp)[["net_sharpe", "net_cagr", "net_vol", "net_max_dd", "avg_turnover"]].round(3).to_string())
    print("\nTerminal OOS")
    print(stats_frame(final_cmp)[["net_sharpe", "net_cagr", "net_vol", "net_max_dd", "avg_turnover"]].round(3).to_string())
    print("\nTerminal paired", json.dumps(final_cmp["paired"], indent=2))
    print(f"selected TPE config {terminal_config}: {OT.config_params(cfg, terminal_config)}")
    print(f"written -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
