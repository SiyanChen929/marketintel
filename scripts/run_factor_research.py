"""Re-run the factor construction and portfolio construction study (docs/FACTOR_RESEARCH.md).
Usage: python scripts/run_factor_research.py   (requires outputs/validation/pit_panel.parquet from run_validation.py)"""
import sys, warnings, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
import pandas as pd
from ai_semi_intel.research import factor_lab as F, backtest as B, pit_features as P, ic_tests as T, portfolio_lab as PL
OUT = Path("outputs/validation/factor_research"); OUT.mkdir(parents=True, exist_ok=True); t0 = time.time()
panel = pd.read_parquet("outputs/validation/pit_panel.parquet"); panel["date"] = pd.to_datetime(panel["date"])
fr = pd.read_parquet("outputs/validation/forward_returns.parquet"); fr["date"] = pd.to_datetime(fr["date"])
d = F.derive(panel); d.to_parquet(OUT / "panel_derived.parquet")
z = F.evaluate(d, fr); z.to_csv(OUT / "factor_zoo.csv", index=False)
print(z[["feature", "family", "ic_3m", "t_3m", "ic_h1", "ic_h2", "ic_ex_winners", "selected_strict", "selected_lenient"]].round(3).to_string(index=False))
nested = {rule: {k: v for k, v in F.nested_oos(d, fr, rule).items() if k not in ("first_half_table", "scored")} for rule in ("strict", "lenient")}
print("nested:", json.dumps(nested, default=float))
sc = F.build_composite(d, [("growth_accel", 1, "growth"), ("rev_q_yoy", 1, "growth"), ("mom_12_1_volscaled", 1, "momentum"), ("industry_mom", 1, "momentum")])
ic3 = F.composite_ic(sc, fr, "3m"); print("fund+price momentum composite: IC3m %.3f t %.2f" % (ic3.mean(), T._hac_t(ic3, 2)))
pl = PL.run(sc, P.load_prices()["adj_close"]); print(pl["stats"].round(3).to_string())
json.dump({"nested": nested, "portfolio_lab": pl["stats"].round(4).to_dict(orient="index")}, open(OUT / "summary_rerun.json", "w"), default=float, indent=1)
print("done %.0fs" % (time.time() - t0))
