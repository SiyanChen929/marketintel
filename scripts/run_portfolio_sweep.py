"""Portfolio construction hyperparameter sweep + walk-forward tuning.  python scripts/run_portfolio_sweep.py"""
import sys, json, time, warnings, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from ai_semi_intel.research import portfolio_sweep as PS, pit_features as P
from ai_semi_intel.utils.io import write_json

OUT = Path("outputs/validation/portfolio_sweep"); OUT.mkdir(parents=True, exist_ok=True); t0 = time.time()
scored = pd.read_parquet("outputs/validation/scored.parquet"); scored["date"] = pd.to_datetime(scored["date"])
adj = P.load_prices()["adj_close"]
cost_bp = 20.0; execution_lag_bars = 1; segment_weight_cap = 0.35
R, cfg, G, T = PS.run_grid(scored, adj, cost_bp=cost_bp, execution_lag_bars=execution_lag_bars,
                           segment_weight_cap=segment_weight_cap)
R.to_parquet(OUT / "net_returns.parquet"); G.to_parquet(OUT / "gross_returns.parquet"); T.to_parquet(OUT / "turnover.parquet"); cfg.to_csv(OUT / "configs.csv")
def _hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
write_json({"engine_version": 3, "execution_lag_bars": execution_lag_bars, "cost_bp": cost_bp,
            "segment_weight_cap": segment_weight_cap,
            "signal_time": "month-end close", "fill_time": "first available daily close after signal",
            "turnover": "full holdings union; exits included", "n_configs": int(len(cfg)), "n_months": int(len(R)),
            "input_sha256": {"scored": _hash(Path("outputs/validation/scored.parquet")),
                              "adjusted_prices": _hash(Path("data_cache/prices/long_adj_close.parquet"))}},
           OUT / "sweep_manifest.json")
split = pd.Timestamp("2019-06-30")
ev = PS.evaluate_grid(R, cfg, split); ev["avg_turnover"] = T.mean().values
ev.to_csv(OUT / "grid_results.csv")
print(f"\ngrid: {len(ev)} configs, {len(R)} months, {time.time() - t0:.0f}s")
best = ev.sort_values("sharpe", ascending=False)
print("\n=== top 12 by full-sample Sharpe (in-sample) ==="); print(best.head(12)[["method", "max_weight", "lookback", "n_hold", "hysteresis", "vol_target", "cagr", "vol", "sharpe", "max_dd", "avg_turnover", "sharpe_h1", "sharpe_h2"]].round(3).to_string())
default = ev[(ev.method == "erc") & (ev.max_weight == 0.15) & (ev.lookback == 756) & (ev.n_hold == 15) & (ev.hysteresis == 1.0) & (ev.vol_target == 0.25)]
print("\n=== current default (ERC, 15 names, 756d, no hysteresis, 25% VT) ==="); print(default[["cagr", "vol", "sharpe", "max_dd", "avg_turnover", "sharpe_h1", "sharpe_h2"]].round(3).to_string())
me = PS.marginal_effects(ev); print("\n=== marginal effects (mean Sharpe by parameter level) ===")
for k, v in me.items(): print(k); print(v.to_string())
# ---- walk-forward tuning (honest)
wf = PS.walk_forward(R, cfg, first_test_year=2016)
oos = wf["oos"]; st_oos = PS.stats(oos)
d_series = R[default.index[0]].reindex(oos.index); st_def = PS.stats(d_series)
ew_idx = ev[(ev.method == "equal") & (ev.max_weight == 0.15) & (ev.lookback == 756) & (ev.n_hold == 15) & (ev.hysteresis == 1.0) & (ev.vol_target == 0.0)].index[0]
st_ew = PS.stats(R[ew_idx].reindex(oos.index))
best_is = R[best.index[0]].reindex(oos.index); st_bis = PS.stats(best_is)
print("\n=== walk-forward tuning, out of sample from %s ===" % oos.index[0].date())
print(pd.DataFrame({"tuned_each_year": st_oos, "fixed_default_ERC_25VT": st_def, "equal_weight_fully_invested": st_ew, "best_in_sample_config(cheating)": st_bis}).T.round(3).to_string())
print("\nchoices by year:"); print(wf["choices"][["year", "method", "max_weight", "lookback", "n_hold", "hysteresis", "vol_target", "train_sharpe"]].to_string(index=False))
# ---- cost sensitivity for the top-5 and default
rows = []
for c in list(best.head(5).index) + [default.index[0]]:
    for bp in (0, 10, 20, 40, 80):
        net = G[c] - bp / 1e4 * 2 * T[c]; s_ = PS.stats(net); rows.append({"config": int(c), **{k: cfg.loc[c, k] for k in ("method", "n_hold", "hysteresis", "vol_target")}, "cost_bp": bp, "sharpe": s_["sharpe"], "cagr": s_["cagr"]})
cs = pd.DataFrame(rows); cs.to_csv(OUT / "cost_sensitivity.csv", index=False)
print("\n=== cost sensitivity (Sharpe) ==="); print(cs.pivot_table(index=["config", "method", "n_hold", "hysteresis", "vol_target"], columns="cost_bp", values="sharpe").round(3).to_string())
# ---- neighbourhood stability of the top config
b = best.iloc[0]; neigh = ev[(ev.method == b.method) & (ev.vol_target == b.vol_target)]
print("\nneighbourhood of the best config (same method & vol target, other params vary): Sharpe mean %.3f, min %.3f, max %.3f, n %d" % (neigh.sharpe.mean(), neigh.sharpe.min(), neigh.sharpe.max(), len(neigh)))
summary = {"n_configs": int(len(ev)), "months": int(len(R)), "top10": best.head(10).round(4).to_dict(orient="records"),
           "default": default.round(4).to_dict(orient="records")[0], "marginal": {k: v.reset_index().to_dict(orient="records") for k, v in me.items()},
           "walk_forward": {"oos_start": str(oos.index[0].date()), "tuned": st_oos, "default": st_def, "equal_weight": st_ew, "best_in_sample": st_bis,
                            "choices": wf["choices"].to_dict(orient="records")}, "cost_sensitivity": cs.to_dict(orient="records"),
           "sharpe_distribution": {"mean": float(ev.sharpe.mean()), "sd": float(ev.sharpe.std()), "p10": float(ev.sharpe.quantile(.1)), "p90": float(ev.sharpe.quantile(.9))}}
write_json(summary, OUT / "summary.json"); oos.to_csv(OUT / "walk_forward_oos.csv"); wf["choices"].to_csv(OUT / "walk_forward_choices.csv", index=False)
print("\nsaved -> %s in %.0fs" % (OUT, time.time() - t0))
