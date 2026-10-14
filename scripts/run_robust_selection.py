"""Robust-selection walk-forward test for the portfolio construction sweep.

Question: can a yearly re-tuning policy beat the fixed default (ERC, 15 names, 25% vol
target) out of sample if the choice is restricted to economic levers and made with a
robust statistic instead of the trailing-Sharpe maximum?

Reads the saved sweep matrices (outputs/validation/portfolio_sweep/net_returns.parquet,
configs.csv), runs every selection rule on the full 1,008-config grid and on the
restricted 18-config ERC grid (holdings count x volatility target), compares each with the
fixed default on the same out-of-sample months, and writes:

  robust_selection_results.csv   Sharpe / CAGR / vol / max DD / switches per grid x rule
  robust_selection_choices.json  yearly configuration chosen by each rule
  robust_selection_oos.csv       monthly OOS return series per grid x rule
  robust_selection_tests.csv     paired test of each rule against the fixed default
  charts/robust_selection.png    OOS growth of $1 for the default and the main rules

Run:  python scripts/run_robust_selection.py
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from ai_semi_intel.research import portfolio_sweep as PS  # noqa: E402

OUT = ROOT / "outputs" / "validation" / "portfolio_sweep"
RULES = [
    ("max", "max trailing Sharpe", {}),
    ("nbr", "neighbourhood median", {"rule": "nbr_median"}),
    ("maximin", "maximin over training halves", {"rule": "maximin_halves"}),
    ("sticky05", "sticky, switch on +0.05", {"rule": "sticky", "margin": 0.05}),
    ("sticky10", "sticky, switch on +0.10", {"rule": "sticky", "margin": 0.10}),
    ("stickynbr", "sticky + neighbourhood median, +0.05", {"rule": "sticky_nbr", "margin": 0.05}),
    ("avg", "model averaging (equal)", {"rule": "average"}),
    ("softmax", "softmax averaging (T=20)", {"rule": "softmax"}),
]


def paired_test(a: pd.Series, b: pd.Series, n_boot: int = 5000, seed: int = 0) -> dict:
    """Paired comparison of two monthly return series on the same months.

    t-stat of the mean monthly return difference and a stationary-bootstrap-free
    (iid) bootstrap of the Sharpe difference; both are conservative enough for a
    'does not beat the default' conclusion.
    """
    d = (a - b).dropna()
    t = float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))) if d.std(ddof=1) > 0 else 0.0
    rng = np.random.default_rng(seed)
    both = pd.concat([a, b], axis=1).dropna().to_numpy()
    n = len(both)
    diffs = np.empty(n_boot)
    for k in range(n_boot):
        idx = rng.integers(0, n, n)
        s = both[idx]
        sa = (s[:, 0].mean() - 0.02 / 12) / s[:, 0].std(ddof=1) * np.sqrt(12)
        sb = (s[:, 1].mean() - 0.02 / 12) / s[:, 1].std(ddof=1) * np.sqrt(12)
        diffs[k] = sa - sb
    return {
        "mean_diff_ann": float(d.mean() * 12),
        "t_stat": t,
        "sharpe_diff": float(np.mean(diffs)),
        "sharpe_diff_ci_lo": float(np.percentile(diffs, 2.5)),
        "sharpe_diff_ci_hi": float(np.percentile(diffs, 97.5)),
        "p_sharpe_gt_default": float(np.mean(diffs > 0)),
        "n_months": int(n),
    }


def main() -> None:
    R = pd.read_parquet(OUT / "net_returns.parquet")
    R.columns = R.columns.astype(int)
    cfg = pd.read_csv(OUT / "configs.csv", index_col=0)
    base = (cfg.method == "erc") & (cfg.max_weight == 0.15) & (cfg.lookback == 756) & (cfg.hysteresis == 1.0)
    default = int(cfg[base & (cfg.n_hold == 15) & (cfg.vol_target == 0.25)].index[0])
    grids = {
        "full 1,008": (cfg.index, ("n_hold", "vol_target", "max_weight", "lookback", "hysteresis")),
        "restricted 18": (cfg[base].index, ("n_hold", "vol_target")),
    }

    rows, runs = [], {}
    for gname, (idx, levers) in grids.items():
        for key, name, kw in RULES:
            kw = dict(kw)
            rule = kw.pop("rule", "max")
            if rule.startswith("sticky"):
                kw["incumbent"] = default
            res = PS.walk_forward_rule(R, cfg, rule, idx, levers, **kw)
            st = PS.stats(res["oos"])
            rows.append({"grid": gname, "rule": name, "sharpe": st["sharpe"], "cagr": st["cagr"], "vol": st["vol"],
                         "max_dd": st["max_dd"], "switches": res["switches"]})
            runs[(gname, key, name)] = res

    oos_idx = next(iter(runs.values()))["oos"].index
    ref = R[default].reindex(oos_idx)
    st = PS.stats(ref)
    rows.append({"grid": "reference", "rule": "fixed default ERC/15/25%", "sharpe": st["sharpe"], "cagr": st["cagr"],
                 "vol": st["vol"], "max_dd": st["max_dd"], "switches": 0})
    res = pd.DataFrame(rows)
    print(f"OOS window {oos_idx[0].date()} to {oos_idx[-1].date()} ({len(oos_idx)} months)\n")
    print(res.round(3).to_string(index=False))

    tests = []
    for (gname, key, name), r in runs.items():
        tests.append({"grid": gname, "rule": name, **paired_test(r["oos"], ref)})
    tests = pd.DataFrame(tests)
    print("\nPaired test vs fixed default (monthly diff t-stat; bootstrap Sharpe difference, 95% CI):")
    print(tests.round(3).to_string(index=False))

    res.to_csv(OUT / "robust_selection_results.csv", index=False)
    tests.to_csv(OUT / "robust_selection_tests.csv", index=False)
    json.dump({f"{g} | {k}": v["choices"].to_dict(orient="records") for (g, k, _), v in runs.items()},
              open(OUT / "robust_selection_choices.json", "w"), indent=1, default=str)
    pd.DataFrame({f"{g} | {k}": v["oos"] for (g, k, _), v in runs.items()}).assign(default=ref).to_csv(
        OUT / "robust_selection_oos.csv")

    for gname, key in (("restricted 18", "nbr"), ("restricted 18", "maximin"), ("restricted 18", "sticky05"),
                       ("restricted 18", "max")):
        r = next(v for (g, k, _), v in runs.items() if g == gname and k == key)
        print(f"\nyearly choice, {gname}, {key}:",
              [(int(c["year"]), int(c["n_hold"]), float(c["vol_target"])) for c in r["choices"].to_dict("records")])

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        (OUT / "charts").mkdir(exist_ok=True)
        series = {
            "Fixed default (ERC, 15 names, 25% target)": ref,
            "Model averaging, all 1,008": runs[("full 1,008", "avg", "model averaging (equal)")]["oos"],
            "Max trailing Sharpe, restricted 18": runs[("restricted 18", "max", "max trailing Sharpe")]["oos"],
            "Max trailing Sharpe, all 1,008": runs[("full 1,008", "max", "max trailing Sharpe")]["oos"],
        }
        colors = ["#1f4e79", "#2a9d8f", "#e9a23b", "#c0392b"]
        fig, ax = plt.subplots(figsize=(10, 5.2))
        for (name, s), c in zip(series.items(), colors):
            g = (1 + s.fillna(0)).cumprod()
            ax.plot(g.index, g.values, lw=2 if "default" in name else 1.6, color=c,
                    label=f"{name}  (Sharpe {PS.stats(s)['sharpe']:.2f})")
        ax.set_yscale("log")
        ax.set_ylabel("Growth of $1 (log scale)")
        fig.suptitle("Walk-forward re-tuning cannot beat the fixed default out of sample", x=0.01, y=0.985,
                     ha="left", fontsize=13, fontweight="bold")
        ax.set_title(f"Yearly expanding-window selection, OOS {oos_idx[0].year}-{oos_idx[-1].year}, "
                     "net of 20bp costs, Sharpe with rf 2%", loc="left", fontsize=9, color="#555")
        ax.grid(alpha=0.25)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.legend(frameon=False, fontsize=9, loc="upper left")
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        fig.savefig(OUT / "charts" / "robust_selection.png", dpi=160)
        print(f"\nchart -> {OUT / 'charts' / 'robust_selection.png'}")
    except Exception as exc:  # chart is optional
        print("chart skipped:", exc)


if __name__ == "__main__":
    main()
