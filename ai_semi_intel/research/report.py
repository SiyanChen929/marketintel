"""Validation report: charts, summary JSON and a Markdown write-up in outputs/validation/."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import PercentFormatter

from ..charts import style as S
from ..charts.style import apply_theme, title, source
from ..config.settings import OUTPUTS
from ..utils.io import write_json

VAL = OUTPUTS / "validation"
CH = VAL / "charts"
apply_theme()


def _save(fig, name):
    CH.mkdir(parents=True, exist_ok=True)
    p = CH / f"{name}.png"; fig.savefig(p); plt.close(fig); return p


def chart_ic_timeseries(ic: pd.Series, horizon: str, window: int = 12) -> Path:
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.bar(ic.index, ic.values, width=20, color=[S.BLUE if v >= 0 else S.RED for v in ic.values], alpha=0.55)
    ax.plot(ic.index, ic.rolling(window).mean(), color=S.TEXT, lw=2, label=f"{window}-month rolling mean")
    ax.axhline(0, color=S.GRAY_DARK, lw=0.8); ax.axhline(ic.mean(), color=S.ORANGE, lw=1.2, ls="--", label=f"mean IC {ic.mean():.3f}")
    ax.set_ylabel("Rank IC"); ax.legend(loc="upper left")
    title(ax, f"Composite score: rank IC vs {horizon} forward return, by month", "Spearman correlation between score and subsequent return across eligible names")
    source(fig, "Source: SEC EDGAR point-in-time reconstruction, Yahoo Finance prices; trailing-only score")
    return _save(fig, f"ic_timeseries_{horizon}")


def chart_ic_decay(ict: pd.DataFrame) -> Path:
    piv = ict.pivot(index="horizon", columns="signal", values="mean_ic").reindex(["1m", "3m", "6m", "12m"])
    fig, ax = plt.subplots(figsize=(10, 4.8))
    sigs = [c for c in ["composite", "score_fund_momentum", "score_price_momentum", "score_value", "score_growth", "score_quality", "score_momentum"] if c in piv.columns]
    x = np.arange(len(piv)); w = 0.8 / len(sigs)
    for i, s in enumerate(sigs):
        ax.bar(x + (i - len(sigs) / 2 + 0.5) * w, piv[s], width=w * 0.95, color=S.CAT[i], label=s.replace("score_", "").capitalize(), edgecolor=S.SURFACE)
    ax.set_xticks(x); ax.set_xticklabels(piv.index); ax.axhline(0, color=S.GRAY_DARK, lw=0.8)
    ax.set_ylabel("Mean rank IC"); ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.1))
    title(ax, "IC by horizon and pillar", "Which pillar predicts at which horizon")
    source(fig, "Source: point-in-time backtest")
    return _save(fig, "ic_decay")


def chart_quintiles(stats: pd.DataFrame, bt: pd.DataFrame) -> Path:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 4.8), gridspec_kw={"width_ratios": [1, 1.5]})
    qs = [f"q{k}" for k in range(1, 6)]
    vals = [stats.loc[q, "cagr"] for q in qs]
    a1.bar(qs, vals, color=[S.SEQ_BLUE[i] for i in (3, 5, 7, 9, 11)], edgecolor=S.SURFACE)
    for i, v in enumerate(vals):
        a1.text(i, v + 0.005, f"{v:.0%}", ha="center", fontsize=8.5, color=S.TEXT2)
    a1.yaxis.set_major_formatter(PercentFormatter(1.0)); a1.set_title("Annualised return by score quintile (Q5 = best)", fontsize=10.5)
    cum = (1 + bt[["q5", "q1", "ls_q5_q1", "universe_ew"]]).cumprod() * 100
    for c, col, lab in (("q5", S.BLUE, "Top quintile"), ("q1", S.RED, "Bottom quintile"), ("universe_ew", S.GRAY_DARK, "Universe EW"), ("ls_q5_q1", S.ORANGE, "Q5 - Q1 (long-short)")):
        a2.plot(cum.index, cum[c], color=col, lw=1.8, label=lab)
    a2.set_yscale("log"); a2.legend(loc="upper left", fontsize=8.5); a2.set_title("Growth of 100 (log)", fontsize=10.5)
    fig.suptitle("Quintile portfolios, monthly rebalanced, equal weight", x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.02, 1, 0.94)); source(fig, "Source: point-in-time backtest; 1-month forward returns")
    return _save(fig, "quintiles")


def chart_top15(bt: pd.DataFrame, stats: pd.DataFrame) -> Path:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11.5, 7), gridspec_kw={"height_ratios": [2.4, 1]}, sharex=True)
    cum = (1 + bt[["top15", "top15_net", "universe_ew", "SOXX", "SPY"]]).cumprod() * 100
    for c, col, lw, lab in (("top15", S.BLUE, 2.2, "Top 15 (gross)"), ("top15_net", S.SEQ_BLUE[4], 1.4, "Top 15 (20bp cost)"), ("universe_ew", S.GRAY_DARK, 1.6, "Universe EW"), ("SOXX", S.ORANGE, 1.5, "SOXX"), ("SPY", S.YELLOW, 1.5, "SPY")):
        st = stats.loc[c]
        a1.plot(cum.index, cum[c], color=col, lw=lw, label=f"{lab}: {st['cagr']:.1%} CAGR, {st['vol']:.0%} vol, Sharpe {st['sharpe']:.2f}, maxDD {st['max_dd']:.0%}")
    a1.set_yscale("log"); a1.legend(loc="upper left", fontsize=8.5); a1.set_ylabel("Growth of 100 (log)")
    active = (1 + bt["active_vs_universe"]).cumprod() * 100
    a2.plot(active.index, active, color=S.BLUE, lw=1.8); a2.axhline(100, color=S.GRAY_DARK, lw=0.8)
    a2.set_ylabel("Top 15 vs universe\n(cumulative active)")
    hr = stats.loc["top15", "hit_rate_vs_universe"]
    title(a1, "Top-15 selection (segment cap 3), monthly rebalanced: point-in-time, trailing-only score",
          f"Monthly hit rate vs equal-weight universe {hr:.0%}; universe = current constituents with filed data (survivorship caveat)")
    source(fig, "Source: SEC EDGAR PIT features, Yahoo Finance adjusted prices")
    return _save(fig, "top15_backtest")


def chart_permutation(perm: dict, rw: dict) -> Path:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.4))
    a1.hist(perm["distribution"], bins=40, color=S.GRAY, edgecolor=S.SURFACE)
    a1.axvline(perm["actual_mean_ic"], color=S.BLUE, lw=2, label=f"actual {perm['actual_mean_ic']:.3f} (p = {perm['p_value']:.3f})")
    a1.set_title("Permutation test: mean IC when scores are shuffled", fontsize=10.5); a1.legend(fontsize=8.5)
    a2.hist(rw["distribution"], bins=40, color=S.GRAY, edgecolor=S.SURFACE)
    a2.axvline(rw["actual_mean_ic"], color=S.BLUE, lw=2, label=f"40/30/20/10: {rw['actual_mean_ic']:.3f} (pctile {rw['percentile_of_actual']:.0%})")
    a2.axvline(rw["equal_weight_ic"], color=S.ORANGE, lw=1.5, ls="--", label=f"equal pillar weights: {rw['equal_weight_ic']:.3f}")
    a2.set_title("Random pillar-weight test: mean IC of random weightings", fontsize=10.5); a2.legend(fontsize=8.5)
    fig.suptitle("Is the score better than chance, and are the weights better than random?", x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.02, 1, 0.93)); source(fig, "Source: point-in-time backtest, 3-month horizon")
    return _save(fig, "permutation_random_weights")


def chart_feature_ic(fic: pd.DataFrame) -> Path:
    d = fic.sort_values("mean_ic")
    fig, ax = plt.subplots(figsize=(10, 6))
    cols = {"value": S.BLUE, "fund_momentum": S.ORANGE, "price_momentum": S.YELLOW, "growth": S.ORANGE, "quality": S.AQUA, "momentum": S.YELLOW}
    ax.barh(d["feature"], d["mean_ic"], color=[cols.get(p, S.GRAY) for p in d["pillar"]], edgecolor=S.SURFACE)
    for i, (v, t) in enumerate(zip(d["mean_ic"], d["t_hac"])):
        ax.text(v + (0.002 if v >= 0 else -0.002), i, f"{v:.3f} (t {t:.1f})", va="center", ha="left" if v >= 0 else "right", fontsize=8, color=S.TEXT2)
    ax.axvline(0, color=S.GRAY_DARK, lw=0.8)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=p.replace("_", " ").capitalize()) for p, c in cols.items() if p in set(d["pillar"])], loc="lower right")
    title(ax, "Mean rank IC of each raw feature (3-month horizon, signed as used)", "Newey-West t-statistics in brackets")
    source(fig, "Source: point-in-time backtest")
    return _save(fig, "feature_ic")


def chart_walk_forward(wf: dict) -> Path:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={"height_ratios": [1.4, 1]}, sharex=True)
    oos, fx = wf["oos_ic"], wf["fixed_ic"]
    a1.plot(oos.index, oos.rolling(12).mean(), color=S.BLUE, lw=2, label=f"walk-forward weights, 12m rolling IC (mean {oos.mean():.3f})")
    a1.plot(fx.index, fx.rolling(12).mean(), color=S.GRAY_DARK, lw=1.6, ls="--", label=f"fixed 50/35/15, 12m rolling IC (mean {fx.mean():.3f})")
    a1.axhline(0, color=S.GRAY_DARK, lw=0.8); a1.legend(loc="upper left", fontsize=9); a1.set_ylabel("Rank IC (3m)")
    w = wf["weights"]
    cols = {c: col for c, col in zip(w.columns, [S.ORANGE, S.YELLOW, S.BLUE, S.AQUA])}
    a2.stackplot(w.index, [w[c] for c in cols], labels=[c.replace("_", " ") for c in cols], colors=list(cols.values()), alpha=0.9)
    a2.set_ylim(0, 1); a2.yaxis.set_major_formatter(PercentFormatter(1.0)); a2.legend(loc="upper left", ncol=4, fontsize=8.5); a2.set_ylabel("Pillar weights")
    title(a1, "Out-of-sample pillar weights: trailing-IC weighting shrunk 50% to the prior", "Weights at t use only ICs realised before t; no look-ahead")
    source(fig, "Source: point-in-time backtest")
    return _save(fig, "walk_forward")


def write_report(summary: dict, ict: pd.DataFrame, fic: pd.DataFrame, stats: pd.DataFrame, regime: pd.DataFrame | None, loo: pd.DataFrame) -> Path:
    VAL.mkdir(parents=True, exist_ok=True)
    write_json(summary, VAL / "summary.json")
    L = []
    L.append(f"# Score validation report\n\nGenerated {summary['generated_at']}. Point-in-time, trailing-only variant of the composite; "
             f"{summary['panel']['n_tickers']} tickers, {summary['panel']['n_months']} month-ends from {summary['panel']['start']} to {summary['panel']['end']}; "
             f"{summary['panel']['n_rows']} ticker-months.\n")
    L.append("**Caveats.** Universe = today's constituents that have EDGAR history (survivorship bias: failed names are absent). "
             "ADRs excluded. Consensus pillars absent (lower bound on the live model). Costs 20bp per unit of turnover.\n")
    L.append("## Rank IC by horizon\n")
    L.append(ict.pivot(index="signal", columns="horizon", values="mean_ic").reindex(columns=["1m", "3m", "6m", "12m"]).round(4).to_markdown())
    L.append("\n\nHAC t-statistics:\n")
    L.append(ict.pivot(index="signal", columns="horizon", values="t_hac").reindex(columns=["1m", "3m", "6m", "12m"]).round(2).to_markdown())
    L.append("\n\n## Portfolio statistics (monthly rebalanced)\n")
    L.append(stats[["cagr", "vol", "sharpe", "max_dd", "total", "months"]].round(3).to_markdown())
    L.append("\n\n## Tests\n")
    p = summary["permutation"]; r = summary["random_weights"]; f = summary["factor_regression"]
    L.append(f"- Permutation test (3m): actual mean IC {p['actual_mean_ic']:.3f} vs null {p['null_mean']:.3f} ± {p['null_sd']:.3f}; p-value {p['p_value']:.3f}.")
    L.append(f"- Random pillar weights (3m): fixed 50/35/15 weights IC {r['actual_mean_ic']:.3f}, percentile {r['percentile_of_actual']:.0%} of {r['n_draw']} random weightings; equal weights {r['equal_weight_ic']:.3f}; single pillars {json.dumps({k: round(v, 3) for k, v in r['single_pillar_ic'].items()})}.")
    L.append(f"- Alpha of top-15 minus universe vs SOXX: {f['vs_soxx']['alpha_annual']:.1%} p.a. (t {f['vs_soxx']['alpha_t']:.2f}); top-15 vs SPY+SOXX: alpha {f['top15_vs_spy_soxx']['alpha_annual']:.1%} (t {f['top15_vs_spy_soxx']['alpha_t']:.2f}), betas {json.dumps({k: round(v, 2) for k, v in f['top15_vs_spy_soxx']['betas'].items()})}.")
    if summary.get("fama_french") and "alpha_annual" in summary["fama_french"]:
        ff = summary["fama_french"]
        L.append(f"- Fama-French 5 + momentum: alpha {ff['alpha_annual']:.1%} p.a. (t {ff['alpha_t']:.2f}), betas {json.dumps(ff['betas'])}, R² {ff['r2']:.2f}.")
    wf = summary.get("walk_forward")
    if wf:
        L.append(f"- Walk-forward weights (3m, OOS): IC {wf['oos_mean_ic']:.3f} (t {wf['oos_t_hac']:.2f}) vs fixed {wf['fixed_mean_ic_same_period']:.3f} over {wf['n_periods']} months from {wf['start']}; latest weights {json.dumps(wf['latest_weights'])}; mean weights {json.dumps(wf['mean_weights'])}.")
    if summary.get("ic_by_year"):
        L.append("\n### 3-month IC by calendar year\n"); L.append(pd.DataFrame(summary["ic_by_year"]).set_index("year").to_markdown())
    if regime is not None and not regime.empty:
        L.append("\n### IC by regime (3m)\n"); L.append(regime.round(3).to_markdown())
    if loo is not None and not loo.empty:
        L.append("\n### Leave-one-out (3m mean IC)\n"); L.append(loo.round(4).to_markdown())
    L.append("\n\n## Feature-level IC (3m)\n"); L.append(fic.round(4).to_markdown(index=False))
    L.append("\n\n## Interpretation\n" + summary["interpretation"])
    (VAL / "report.md").write_text("\n".join(L))
    return VAL / "report.md"
