"""Point-in-time validation of the scoring system.  python scripts/run_validation.py [--rebuild]"""
import sys, warnings, json, time
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from ai_semi_intel.utils.io import setup_logging, log
from ai_semi_intel.research import pit_features as P, backtest as B, ic_tests as T, report as R
from ai_semi_intel.data.fred import fetch_series

setup_logging()
VAL = R.VAL; VAL.mkdir(parents=True, exist_ok=True)
t0 = time.time()
ppath = VAL / "pit_panel.parquet"
if ppath.exists() and "--rebuild" not in sys.argv:
    panel = pd.read_parquet(ppath); log.info("loaded cached panel %s", panel.shape)
else:
    panel = P.build_panel(start="2012-01-31"); panel.to_parquet(ppath); log.info("panel built %s in %.0fs", panel.shape, time.time() - t0)
panel["date"] = pd.to_datetime(panel["date"])
scored = B.score_panel(panel); scored.to_parquet(VAL / "scored.parquet")
dates = sorted(scored["date"].unique())
adj = P.load_prices()["adj_close"]
fr = B.forward_returns(dates, adj, sorted(scored["ticker"].unique()))
fr.to_parquet(VAL / "forward_returns.parquet")
log.info("scored %d rows, %d dates; eligible/date median %.0f", len(scored), len(dates), scored.groupby("date")["eligible"].sum().median())
# ---- backtest
bt = B.run_backtest(scored, fr); btm, stats = bt["monthly"], bt["stats"]
btm.to_csv(VAL / "backtest_monthly.csv"); stats.to_csv(VAL / "backtest_stats.csv")
# ---- IC tests
ict = T.ic_table(scored, fr); ict.to_csv(VAL / "ic_table.csv", index=False)
fic = T.feature_ic(scored, panel, fr, "3m"); fic.to_csv(VAL / "feature_ic.csv", index=False)
ic3 = T.ic_time_series(scored, fr, "composite", "3m"); ic1 = T.ic_time_series(scored, fr, "composite", "1m")
perm = T.permutation_test(scored, fr, "3m", n_perm=300)
rw = T.random_weights_test(scored, fr, "3m", n_draw=300)
top_names = scored[scored["eligible"]].groupby("ticker")["composite"].mean().sort_values(ascending=False).head(6).index.tolist()
loo = T.leave_one_out(scored, fr, "3m", tickers=top_names + ["MU", "NVDA", "SNDK"])
loo = loo.drop_duplicates("dropped")
# regimes: semiconductor industrial production YoY sign, and SOXX trailing 12m sign
ip = fetch_series("IPG3344S", start="2010-01-01"); ip_yoy = ip.pct_change(12)
reg_ip = pd.Series({d: ("semi output expanding" if ip_yoy[ip_yoy.index <= d].iloc[-1] > 0 else "semi output contracting") for d in ic3.index})
soxx = adj["SOXX"].reindex(dates); soxx12 = soxx.pct_change(12)
reg_mkt = pd.Series({d: ("SOXX up 12m" if soxx12.get(d, np.nan) > 0 else "SOXX down 12m") for d in ic3.index})
regime = pd.concat([T.regime_split(ic3, reg_ip), T.regime_split(ic3, reg_mkt)])
factor = T.factor_regression(btm)
ff = T.fama_french(btm)
# walk-forward (out-of-sample) pillar weights
wf = B.walk_forward_weights(scored, fr, "3m")
wf_summary = {"oos_mean_ic": float(wf["oos_ic"].mean()), "oos_t_hac": T._hac_t(wf["oos_ic"], 2), "fixed_mean_ic_same_period": float(wf["fixed_ic"].mean()),
              "n_periods": int(len(wf["oos_ic"])), "start": str(pd.Timestamp(wf["oos_ic"].index[0]).date()) if len(wf["oos_ic"]) else None,
              "latest_weights": {k: round(float(v), 3) for k, v in wf["weights"].iloc[-1].items()} if len(wf["weights"]) else {},
              "mean_weights": {k: round(float(v), 3) for k, v in wf["weights"].mean().items()} if len(wf["weights"]) else {}}
wf["weights"].to_csv(VAL / "walk_forward_weights.csv"); wf["pillar_ic"].to_csv(VAL / "pillar_ic_monthly.csv")
R.chart_walk_forward(wf)
ic_by_year = pd.DataFrame({sig: T.ic_time_series(scored, fr, sig, "3m").pipe(lambda s_: s_.groupby(s_.index.year).mean())
                           for sig in B.PILLAR_SIGNALS}).round(3)
ic_by_year.to_csv(VAL / "ic_by_year.csv")
# ---- charts
R.chart_ic_timeseries(ic3, "3m"); R.chart_ic_timeseries(ic1, "1m"); R.chart_ic_decay(ict); R.chart_quintiles(stats, btm)
R.chart_top15(btm, stats); R.chart_permutation(perm, rw); R.chart_feature_ic(fic)
# ---- summary
c3 = ict[(ict.signal == "composite") & (ict.horizon == "3m")].iloc[0]; c12 = ict[(ict.signal == "composite") & (ict.horizon == "12m")].iloc[0]
best_pillar = ict[ict.horizon == "3m"].sort_values("mean_ic", ascending=False).iloc[0]
vic = ict[(ict.signal == "score_value") & (ict.horizon == "3m")].iloc[0]
interp = (f"Walk-forward pillar weights (estimated only from ICs known at the time, shrunk 50% to the fixed 50/35/15 prior) give an out-of-sample 3-month IC of "
          f"{wf_summary['oos_mean_ic']:.3f} (t {wf_summary['oos_t_hac']:.2f}) versus {wf_summary['fixed_mean_ic_same_period']:.3f} for fixed weights over the same {wf_summary['n_periods']} months; "
          f"latest weights {json.dumps(wf_summary['latest_weights'])}. The value pillar alone has IC {vic['mean_ic']:.3f} (t {vic['t_hac']:.2f}): trailing cheapness has been negatively predictive in this universe. "
          f"The trailing-only composite has a mean 3-month rank IC of {c3['mean_ic']:.3f} (HAC t {c3['t_hac']:.2f}, positive in {c3['pct_positive']:.0%} of months) "
          f"and {c12['mean_ic']:.3f} at 12 months (t {c12['t_hac']:.2f}). The strongest pillar at 3 months is {best_pillar['signal'].replace('score_', '')} "
          f"(IC {best_pillar['mean_ic']:.3f}). The permutation p-value is {perm['p_value']:.3f}; the fixed 50/35/15 weights sit at the "
          f"{rw['percentile_of_actual']:.0%} percentile of random weightings. Top-15 CAGR {stats.loc['top15','cagr']:.1%} vs universe {stats.loc['universe_ew','cagr']:.1%} "
          f"and SOXX {stats.loc['SOXX','cagr']:.1%}; Q5-Q1 spread {stats.loc['ls_q5_q1','cagr']:.1%} p.a.; alpha vs SPY+SOXX {factor['top15_vs_spy_soxx']['alpha_annual']:.1%} (t {factor['top15_vs_spy_soxx']['alpha_t']:.2f}). "
          "Treat the level of returns as biased upward by survivorship; the IC and quintile *ordering* are the informative results.")
summary = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "panel": {"n_tickers": int(panel["ticker"].nunique()), "n_months": len(dates), "start": str(pd.Timestamp(dates[0]).date()), "end": str(pd.Timestamp(dates[-1]).date()), "n_rows": int(len(panel)),
                     "median_eligible_per_month": float(scored.groupby("date")["eligible"].sum().median())},
           "ic": ict.to_dict(orient="records"), "backtest_stats": stats.round(4).to_dict(orient="index"),
           "permutation": {k: v for k, v in perm.items() if k != "distribution"}, "random_weights": {k: v for k, v in rw.items() if k != "distribution"},
           "leave_one_out": loo.to_dict(orient="records"), "regime": regime.reset_index().rename(columns={"index": "regime"}).to_dict(orient="records"),
           "factor_regression": factor, "fama_french": ff, "feature_ic": fic.to_dict(orient="records"), "interpretation": interp,
           "walk_forward": wf_summary, "ic_by_year": ic_by_year.reset_index().rename(columns={"date": "year", "index": "year"}).to_dict(orient="records"),
           "avg_monthly_turnover_top15": float(btm["turnover"].mean())}
R.write_report(summary, ict, fic, stats, regime, loo)
log.info("done in %.0fs -> %s", time.time() - t0, VAL)
print(interp)
