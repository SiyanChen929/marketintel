"""All deck charts (static PNG). Each function returns the saved path."""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter, PercentFormatter

from ..config.settings import CHARTS
from ..config.universe import SEGMENT_ORDER
from . import style as S
from .style import apply_theme, pct, bn, title, source

apply_theme()


def _save(fig, name: str, out: Path = CHARTS) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def _pctfmt(ax, axis="x", d=0):
    f = PercentFormatter(1.0, decimals=d)
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(f)


def _labels(ax, xs, ys, texts, color=S.TEXT, size=8.5):
    try:
        from adjustText import adjust_text
        ts = [ax.text(x, y, t, fontsize=size, color=color, fontweight="bold") for x, y, t in zip(xs, ys, texts)]
        adjust_text(ts, ax=ax, arrowprops=dict(arrowstyle="-", color=S.GRAY, lw=0.6), expand=(1.2, 1.4))
    except Exception:
        for x, y, t in zip(xs, ys, texts):
            ax.annotate(t, (x, y), xytext=(4, 4), textcoords="offset points", fontsize=size, color=color, fontweight="bold")


# ------------------------------------------------------------------ 1 value chain map
def chart_value_chain(top: pd.DataFrame, seg: pd.DataFrame, asof: str) -> Path:
    fig, ax = plt.subplots(figsize=(15, 6.0))
    ax.set_xlim(0, 11); ax.set_ylim(0.2, 10); ax.axis("off")
    ramp = [S.SEQ_BLUE[i] for i in np.linspace(3, 11, 11).astype(int)]
    for i, s in enumerate(SEGMENT_ORDER):
        x = i + 0.05
        box = FancyBboxPatch((x, 6.3), 0.9, 2.6, boxstyle="round,pad=0.02,rounding_size=0.08",
                             fc=ramp[i], ec="none")
        ax.add_patch(box)
        words = textwrap.fill(s, 14)
        ax.text(x + 0.45, 7.6, words, ha="center", va="center", fontsize=8.4, color="white", fontweight="bold", linespacing=1.15)
        if i < 10:
            ax.annotate("", xy=(x + 0.98, 7.6), xytext=(x + 0.90, 7.6), arrowprops=dict(arrowstyle="-|>", color=S.GRAY_DARK, lw=1))
        picks = top[top["segment"] == s].sort_values("rank")
        y = 5.6
        for t, r in picks.iterrows():
            ax.add_patch(FancyBboxPatch((x + 0.08, y - 0.42), 0.74, 0.62, boxstyle="round,pad=0.01,rounding_size=0.06",
                                        fc="white", ec=ramp[i], lw=1.4))
            ax.text(x + 0.45, y - 0.02, f"{t}", ha="center", va="center", fontsize=9.5, fontweight="bold", color=S.TEXT)
            ax.text(x + 0.45, y - 0.3, f"#{int(r['rank'])} · {pct(r['upside'])} upside", ha="center", va="center", fontsize=6.8, color=S.TEXT2)
            y -= 0.85
        if picks.empty:
            ax.text(x + 0.45, 5.3, "no pick", ha="center", va="center", fontsize=8, color=S.TEXT3, style="italic")
        st = seg.loc[s] if s in seg.index else None
        if st is not None:
            ax.plot([x + 0.1, x + 0.8], [2.55, 2.55], color=S.GRID, lw=0.8)
            ax.text(x + 0.45, 2.25, f"{st['n']:.0f} names · {bn(st['market_cap_bn']*1e9)}", ha="center", fontsize=7.3, color=S.TEXT2)
            ax.text(x + 0.45, 1.85, f"NTM growth {pct(st['med_rev_growth_ntm'])}", ha="center", fontsize=7.3, color=S.TEXT2)
            ax.text(x + 0.45, 1.45, f"{st['med_ev_sales_ntm']:.1f}x EV/Sales", ha="center", fontsize=7.3, color=S.TEXT2)
            ax.text(x + 0.45, 1.0, textwrap.fill(str(st['structure']), 14), ha="center", va="top", fontsize=7.0, color=S.TEXT3, style="italic", linespacing=1.1)
    ax.text(0.05, 9.55, "End-to-end AI & semiconductor value chain: where the 15 picks sit", fontsize=13, fontweight="bold", color=S.TEXT)
    ax.text(0.05, 9.12, "Boxes: picks with rank and blended upside. Segment statistics: number of companies, aggregate market cap, median NTM revenue growth, median EV/Sales, revenue concentration (HHI)", fontsize=8.6, color=S.TEXT2)
    source(fig, asof=asof)
    return _save(fig, "value_chain_map")


# ------------------------------------------------------------------ 2 score ranking
def chart_score_ranking(top: pd.DataFrame, weights: dict, asof: str) -> Path:
    d = top.sort_values("composite")
    fig, ax = plt.subplots(figsize=(11, 6.5))
    pillars = [("score_value", "Value", S.BLUE), ("score_growth", "Fundamental momentum", S.ORANGE),
               ("score_momentum", "Price momentum", S.YELLOW), ("score_scarcity", "Scarcity", S.VIOLET),
               ("score_risk_resilience", "Risk resilience", S.GREEN)]
    wmap = {"score_value": weights["w_value"], "score_growth": weights["w_growth"],
            "score_momentum": weights["w_momentum"], "score_scarcity": weights["w_scarcity"],
            "score_risk_resilience": weights["w_risk"]}
    y = np.arange(len(d))
    pos_base = np.zeros(len(d)); neg_base = np.zeros(len(d))
    for col, lab, c in pillars:
        v = (d[col].fillna(0) * wmap[col]).values
        pos = np.where(v > 0, v, 0); neg = np.where(v < 0, v, 0)
        ax.barh(y, pos, left=pos_base, color=c, height=0.62, label=lab, edgecolor=S.SURFACE, linewidth=1.5)
        ax.barh(y, neg, left=neg_base, color=c, height=0.62, edgecolor=S.SURFACE, linewidth=1.5)
        pos_base += pos; neg_base += neg
    for i, (t, r) in enumerate(d.iterrows()):
        ax.text(pos_base[i] + 0.03, i, f"{r['composite']:.2f}", va="center", fontsize=9, color=S.TEXT, fontweight="bold")
    ax.set_yticks(y); ax.set_yticklabels([f"{t}  ·  {r['style']}" for t, r in d.iterrows()], fontsize=9.5)
    ax.axvline(0, color=S.GRAY_DARK, lw=0.8)
    ax.set_xlabel("Weighted z-score contribution")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=5)
    title(ax, "Top 15 decision score by pillar", f"Growth {weights['w_growth']:.1%} · momentum {weights['w_momentum']:.2%} · value {weights['w_value']:.2%} · scarcity {weights['w_scarcity']:.0%} · risk {weights['w_risk']:.0%}")
    source(fig, asof=asof)
    return _save(fig, "score_ranking")


# ------------------------------------------------------------- 3 valuation vs growth
def chart_valuation_vs_growth(univ: pd.DataFrame, top: pd.DataFrame, reg: dict, asof: str) -> Path:
    d = univ[univ["ev_sales_ntm"].between(0.2, 80) & univ["rev_growth_ntm"].between(-0.3, 2.5)]
    fig, ax = plt.subplots(figsize=(11.5, 6.6))
    rest = d.loc[~d.index.isin(top.index)]
    ax.scatter(rest["rev_growth_ntm"], rest["ev_sales_ntm"], s=np.sqrt(rest["market_cap"].fillna(1e9) / 1e9) * 4 + 12,
               color=S.GRAY, alpha=0.55, edgecolor=S.SURFACE, linewidth=0.8, label="Universe")
    pk = d.loc[d.index.isin(top.index)]
    ax.scatter(pk["rev_growth_ntm"], pk["ev_sales_ntm"], s=np.sqrt(pk["market_cap"].fillna(1e9) / 1e9) * 4 + 12,
               color=S.BLUE, edgecolor=S.SURFACE, linewidth=0.8, label="Top 15 picks", zorder=3)
    if reg.get("coef"):
        c = reg["coef"]; xs = np.linspace(-0.2, 1.5, 100)
        med_fcf, med_gm = d["fcf_margin"].median(), d["gross_margin"].median()
        ys = np.exp(c["intercept"] + c["growth"] * np.clip(xs, -0.3, 1.0) + c["fcf_margin"] * med_fcf + c["gross_margin"] * med_gm)
        ax.plot(xs, ys, color=S.GRAY_DARK, lw=1.4, ls="--", label=f"Fair multiple at median margins (R² {reg.get('r2', 0):.2f})")
    ax.set_yscale("log"); ax.set_ylim(0.3, 100)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}x"))
    ax.set_yticks([0.5, 1, 2, 5, 10, 20, 50])
    _pctfmt(ax, "x"); ax.set_xlim(-0.25, 2.55)
    _labels(ax, pk["rev_growth_ntm"], pk["ev_sales_ntm"], pk.index)
    ax.set_xlabel("Consensus NTM revenue growth"); ax.set_ylabel("EV / NTM sales (log)")
    ax.legend(loc="upper left")
    title(ax, "Growth-adjusted valuation: picks sit below the fair-multiple line or carry superior growth",
          "Bubble size = market cap. Dashed line: cross-sectional regression ln(EV/S) ~ growth + FCF margin + gross margin")
    source(fig, asof=asof)
    return _save(fig, "valuation_vs_growth")


# ------------------------------------------------------------------ 4 upside
def chart_upside(top: pd.DataFrame, asof: str) -> Path:
    d = top.sort_values("upside")
    fig, ax = plt.subplots(figsize=(11, 6.5))
    y = np.arange(len(d))
    cols = [S.BLUE if u >= 0 else S.RED for u in d["upside"]]
    ax.barh(y, d["upside"].clip(-1, 3), color=cols, height=0.55, edgecolor=S.SURFACE)
    for lab, col, mk, c in (("DCF", "dcf_upside", "o", S.ORANGE), ("Regression multiple", "mult_upside", "s", S.AQUA), ("Street target", "target_upside", "^", S.YELLOW)):
        v = d[col].clip(-1, 3)
        ax.scatter(v, y, marker=mk, s=42, color=c, edgecolor=S.SURFACE, linewidth=1, zorder=3, label=lab)
    for i, (t, r) in enumerate(d.iterrows()):
        xr = np.nanmax([r["upside"], r["dcf_upside"], r["mult_upside"], r["target_upside"], 0])
        xr = min(float(np.clip(xr, 0, 3)), 3.0)
        ax.text(xr + 0.05, i, f"{pct(r['upside'])}  (FV \\${r['fair_value']:,.0f} vs \\${r['price']:,.0f})", va="center", fontsize=8.3, color=S.TEXT)
    ax.set_yticks(y); ax.set_yticklabels(d.index, fontsize=9.5)
    ax.axvline(0, color=S.GRAY_DARK, lw=0.8)
    _pctfmt(ax, "x"); ax.set_xlim(-1.05, 4.2)
    ax.set_xlabel("Upside to fair value (bar = blended; markers = individual methods; clipped at +300%)")
    ax.legend(loc="lower right")
    title(ax, "Blended fair value upside: 50% DCF · 30% regression multiple · 20% consensus target")
    source(fig, asof=asof)
    return _save(fig, "upside_by_method")


# ------------------------------------------------------------------ 5 margins
def chart_margins(top: pd.DataFrame, asof: str) -> Path:
    d = top.sort_values("rank")
    fig, ax = plt.subplots(figsize=(12, 5.6))
    x = np.arange(len(d)); w = 0.26
    for i, (col, lab, c) in enumerate((("gross_margin", "Gross margin", S.BLUE), ("op_margin", "Operating margin", S.ORANGE), ("fcf_margin", "FCF margin (TTM)", S.AQUA))):
        v = d[col].clip(-1, 1)
        ax.bar(x + (i - 1) * w, v, width=w * 0.92, color=c, label=lab, edgecolor=S.SURFACE, linewidth=0.8)
        for xi, vi in zip(x + (i - 1) * w, v):
            if not np.isnan(vi):
                ax.text(xi, vi + (0.015 if vi >= 0 else -0.015), pct(vi), ha="center", va="bottom" if vi >= 0 else "top", fontsize=6.6, color=S.TEXT2)
    ax.set_xticks(x); ax.set_xticklabels(d.index, fontsize=9.5)
    ax.axhline(0, color=S.GRAY_DARK, lw=0.8); _pctfmt(ax, "y"); ax.set_ylim(min(-0.2, d["fcf_margin"].clip(-1, 1).min() - 0.1), 1.02)
    ax.legend(loc="upper right", ncol=3)
    title(ax, "Profitability profile of the picks", "Negative FCF margins reflect growth capex (e.g. neoclouds) - the DCF fades them to segment norms")
    source(fig, asof=asof)
    return _save(fig, "margin_profile")


# ------------------------------------------------------------------ 6 growth
def chart_growth(top: pd.DataFrame, asof: str) -> Path:
    d = top.sort_values("rev_growth_ntm", ascending=True)
    fig, ax = plt.subplots(figsize=(11, 6.3))
    y = np.arange(len(d))
    hist = d["rev_cagr_3y"].clip(-0.5, 3); ntm = d["rev_growth_ntm"].clip(-0.5, 3); fy1 = d["rev_growth_fy1"].clip(-0.5, 3)
    for yi, h, n in zip(y, hist, ntm):
        if not (np.isnan(h) or np.isnan(n)):
            ax.plot([h, n], [yi, yi], color=S.GRAY_LIGHT, lw=3, zorder=1)
    ax.scatter(hist, y, color=S.GRAY_DARK, s=60, zorder=2, label="3-yr revenue CAGR (historical)", edgecolor=S.SURFACE)
    ax.scatter(ntm, y, color=S.BLUE, s=70, zorder=3, label="NTM consensus revenue growth", edgecolor=S.SURFACE)
    ax.scatter(fy1, y, color=S.ORANGE, s=48, marker="D", zorder=3, label="FY+1 consensus revenue growth", edgecolor=S.SURFACE)
    for yi, n, e in zip(y, ntm, d["eps_growth_ntm"]):
        xr = np.nanmax([n, fy1.iloc[yi], hist.iloc[yi]])
        ax.text(xr + 0.06, yi, f"{pct(n)} rev · EPS {pct(e) if not np.isnan(e) else 'n/a'}", va="center", fontsize=8.3, color=S.TEXT2)
    ax.set_yticks(y); ax.set_yticklabels(d.index, fontsize=9.5)
    _pctfmt(ax, "x"); ax.set_xlim(-0.55, 3.5); ax.axvline(0, color=S.GRAY_DARK, lw=0.8)
    ax.legend(loc="lower right")
    title(ax, "Growth profile: historical CAGR vs forward consensus (clipped at 300%)")
    source(fig, asof=asof)
    return _save(fig, "growth_profile")


# ------------------------------------------------------------------ 7 quality map
def chart_quality(univ: pd.DataFrame, top: pd.DataFrame, asof: str) -> Path:
    d = univ.dropna(subset=["roic", "fcf_margin"]).copy()
    d["roic"] = d["roic"].clip(-0.5, 1.2); d["fcf_margin"] = d["fcf_margin"].clip(-1.0, 0.7)
    fig, ax = plt.subplots(figsize=(11.5, 6.4))
    rest = d.loc[~d.index.isin(top.index)]; pk = d.loc[d.index.isin(top.index)]
    ax.scatter(rest["roic"], rest["fcf_margin"], s=np.sqrt(rest["market_cap"].fillna(1e9) / 1e9) * 4 + 12, color=S.GRAY, alpha=0.55, edgecolor=S.SURFACE, label="Universe")
    ax.scatter(pk["roic"], pk["fcf_margin"], s=np.sqrt(pk["market_cap"].fillna(1e9) / 1e9) * 4 + 12, color=S.BLUE, edgecolor=S.SURFACE, zorder=3, label="Top 15 picks")
    ax.axhline(d["fcf_margin"].median(), color=S.GRAY_DARK, lw=0.8, ls=":"); ax.axvline(d["roic"].median(), color=S.GRAY_DARK, lw=0.8, ls=":")
    _pctfmt(ax, "x"); _pctfmt(ax, "y"); ax.set_xlim(-0.55, 1.25); ax.set_ylim(-1.05, 0.75)
    _labels(ax, pk["roic"], pk["fcf_margin"], pk.index)
    ax.set_xlabel("ROIC (TTM EBIT after tax / invested capital)"); ax.set_ylabel("FCF margin (TTM)")
    ax.legend(loc="lower right")
    title(ax, "Quality map: returns on capital vs cash conversion", "Dotted lines = universe medians; bubble size = market cap")
    source(fig, asof=asof)
    return _save(fig, "quality_map")


# ------------------------------------------------------------------ 8 revisions
def chart_revisions(top: pd.DataFrame, asof: str) -> Path:
    d = top.sort_values("eps_rev_90d")
    fig, ax = plt.subplots(figsize=(10.5, 6))
    y = np.arange(len(d))
    v90 = d["eps_rev_90d"].clip(-1, 3); v30 = d["eps_rev_30d"].clip(-1, 3)
    ax.barh(y + 0.18, v90, height=0.34, color=[S.DIV_POS if x >= 0 else S.DIV_NEG for x in v90], label="90-day EPS revision", edgecolor=S.SURFACE)
    ax.barh(y - 0.18, v30, height=0.34, color=[S.SEQ_BLUE[3] if x >= 0 else "#f0a3a2" for x in v30], label="30-day EPS revision", edgecolor=S.SURFACE)
    for i, v in enumerate(v90):
        if not np.isnan(v):
            ax.text(v + (0.02 if v >= 0 else -0.02), i + 0.18, pct(v), va="center", ha="left" if v >= 0 else "right", fontsize=8, color=S.TEXT2)
    ax.set_yticks(y); ax.set_yticklabels(d.index, fontsize=9.5); ax.axvline(0, color=S.GRAY_DARK, lw=0.8)
    _pctfmt(ax, "x"); ax.set_xlabel("Change in consensus EPS (avg of FY0 and FY+1)")
    ax.legend(loc="lower right")
    title(ax, "Estimate momentum: consensus EPS revisions", "Blue = upward revisions, red = downward; darker = 90-day, lighter = 30-day")
    source(fig, asof=asof)
    return _save(fig, "estimate_revisions")


# ---------------------------------------------------------- 9 relative performance
def chart_relative_performance(px: pd.DataFrame, top: pd.DataFrame, asof: str, bench: str = "SOXX") -> Path:
    tick = list(top.sort_values("rank").index)
    n = len(tick); cols = 5; rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(15, 2.6 * rows), sharex=False)
    p = px.tail(252)
    b = p[bench] / p[bench].dropna().iloc[0] * 100
    for ax, t in zip(axes.flat, tick):
        s = p[t].dropna(); s = s / s.iloc[0] * 100
        ax.plot(b.index, b, color=S.GRAY, lw=1.4, label=bench)
        ax.plot(s.index, s, color=S.BLUE, lw=1.8, label=t)
        ax.text(0.02, 0.9, f"{t}", transform=ax.transAxes, fontsize=10, fontweight="bold", color=S.TEXT)
        ax.text(0.98, 0.9, f"{s.iloc[-1]-100:+.0f}% vs {bench} {b.iloc[-1]-100:+.0f}%", transform=ax.transAxes, fontsize=8, ha="right", color=S.TEXT2)
        ax.tick_params(labelsize=7); ax.set_xticks([p.index[0], p.index[len(p)//2], p.index[-1]])
        ax.set_xticklabels([d.strftime("%b %y") for d in [p.index[0], p.index[len(p)//2], p.index[-1]]])
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    fig.suptitle(f"One-year indexed price (=100) for the picks (blue) vs {bench} (gray)", x=0.01, ha="left", fontsize=13, fontweight="bold", color=S.TEXT)
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    source(fig, asof=asof)
    return _save(fig, "relative_performance")


# ---------------------------------------------------------- 10 efficient frontier
def chart_efficient_frontier(res: dict, asof: str) -> Path:
    opt, ast = res["opt"], res["asset_stats"]
    fig, ax = plt.subplots(figsize=(11, 6.4))
    fr = opt["frontier"]
    if not fr.empty:
        ax.plot(fr["vol"], fr["target_return"], color=S.GRAY_DARK, lw=1.6, label="Efficient frontier (long-only)")
    ax.scatter(ast["vol"], ast["exp_return_bl"], color=S.GRAY, s=40, edgecolor=S.SURFACE, label="Individual stocks (expected return)")
    ax.set_xlim(0, ast["vol"].max() * 1.12); ax.set_ylim(min(0, ast["exp_return_bl"].min() - 0.05), ast["exp_return_bl"].max() + 0.12)
    _labels(ax, ast["vol"], ast["exp_return_bl"], ast.index, color=S.TEXT2, size=8)
    rf = opt["meta"]["rf"]
    for key, lab, c, mk in (("recommended", "Recommended: equal risk contribution, 25% vol target", S.BLUE, "*"), ("max_sharpe", "Max Sharpe (Grinold alphas)", S.ORANGE, "o"),
                            ("min_vol", "Minimum variance", S.AQUA, "s"), ("equal_weight", "Equal weight", S.YELLOW, "D")):
        o = opt[key]
        ax.scatter(o["vol"], o["exp_return"], color=c, s=260 if mk == "*" else 110, marker=mk, edgecolor=S.SURFACE, linewidth=1.2, zorder=5,
                   label=f"{lab}: {pct(o['exp_return'])} / {pct(o['vol'])} vol / Sharpe {o['sharpe']:.2f}")
    ms = opt["max_sharpe"]
    xs = np.linspace(0, max(ast["vol"].max(), ms["vol"]) * 1.05, 50)
    ax.plot(xs, rf + (ms["exp_return"] - rf) / ms["vol"] * xs, color=S.ORANGE, lw=1, ls=":", label=f"Capital market line (rf {pct(rf, 2)})")
    _pctfmt(ax, "x"); _pctfmt(ax, "y")
    ax.legend(loc="upper left", fontsize=8.3)
    ax.set_xlabel("Annualised volatility (Ledoit-Wolf, 3-yr daily)"); ax.set_ylabel("Expected return (CAPM + Grinold alpha from score)")
    title(ax, "Portfolio optimisation: efficient frontier and candidate portfolios",
          f"Constraints: long-only, {opt['meta']['min_weight']:.0%}-{opt['meta']['max_weight']:.0%} per name, max {opt['meta']['max_segment_weight']:.0%} per segment")
    source(fig, asof=asof)
    return _save(fig, "efficient_frontier")


# ---------------------------------------------------------- 11 weights
def chart_weights(res: dict, top: pd.DataFrame, asof: str) -> Path:
    w = res["opt"]["recommended"]["weights"].sort_values()
    grp = top["segment"].map(S.MACRO_GROUP).reindex(w.index)
    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    y = np.arange(len(w))
    ax.barh(y, w.values, color=[S.MACRO_COLORS[g] for g in grp], height=0.62, edgecolor=S.SURFACE)
    for i, (t, v) in enumerate(w.items()):
        ax.text(v + 0.003, i, f"{v:.1%}  ·  {top.loc[t, 'segment']}", va="center", fontsize=8.5, color=S.TEXT2)
    ax.set_yticks(y); ax.set_yticklabels(w.index, fontsize=9.5)
    _pctfmt(ax, "x"); ax.set_xlim(0, w.max() * 1.9)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=g) for g, c in S.MACRO_COLORS.items() if g in set(grp)], loc="lower right", title="Value-chain group")
    gw = w.groupby(grp).sum()
    cash = res["opt"]["recommended"].get("cash", 0.0)
    title(ax, "Recommended portfolio weights: equal risk contribution, 25% volatility target", " · ".join(f"{g} {v:.0%}" for g, v in gw.sort_values(ascending=False).items()) + f" · cash {cash:.0%}")
    source(fig, asof=asof)
    return _save(fig, "portfolio_weights")


# ---------------------------------------------------------- 12 correlation
def chart_correlation(res: dict, asof: str) -> Path:
    c = res["corr"]
    fig, ax = plt.subplots(figsize=(9.5, 8))
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("div", [S.DIV_NEG, S.DIV_MID, S.DIV_POS])
    im = ax.imshow(c.values, cmap=cmap, vmin=-1, vmax=1)
    ax.set_xticks(range(len(c))); ax.set_yticks(range(len(c)))
    ax.set_xticklabels(c.columns, rotation=45, ha="right", fontsize=9); ax.set_yticklabels(c.index, fontsize=9)
    ax.grid(False)
    for i in range(len(c)):
        for j in range(len(c)):
            v = c.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7, color="white" if abs(v) > 0.6 else S.TEXT)
    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02); cb.outline.set_visible(False)
    avg = (c.values[np.triu_indices(len(c), 1)]).mean()
    title(ax, "Pairwise correlation of daily returns (3 years)", f"Average pairwise correlation {avg:.2f}; blue = positive, red = negative")
    source(fig, asof=asof)
    return _save(fig, "correlation_heatmap")


# ---------------------------------------------------------- 13 risk contribution
def chart_risk_contrib(res: dict, asof: str) -> Path:
    o = res["opt"]["recommended"]
    d = pd.DataFrame({"Weight": o["weights"], "Risk contribution": o["risk_contrib"]}).sort_values("Risk contribution")
    fig, ax = plt.subplots(figsize=(10.5, 6))
    y = np.arange(len(d))
    ax.barh(y + 0.19, d["Weight"], height=0.36, color=S.BLUE, label="Portfolio weight", edgecolor=S.SURFACE)
    ax.barh(y - 0.19, d["Risk contribution"], height=0.36, color=S.ORANGE, label="Share of portfolio variance", edgecolor=S.SURFACE)
    for i, (t, r) in enumerate(d.iterrows()):
        ax.text(max(r["Weight"], r["Risk contribution"]) + 0.003, i, f"{r['Weight']:.1%} / {r['Risk contribution']:.1%}", va="center", fontsize=8, color=S.TEXT2)
    ax.set_yticks(y); ax.set_yticklabels(d.index, fontsize=9.5); _pctfmt(ax, "x"); ax.set_xlim(0, max(d.max()) * 1.35)
    ax.legend(loc="lower right")
    title(ax, "Weight vs risk contribution (recommended portfolio)", f"Ex-ante volatility {pct(o['vol'])} after vol targeting; equal risk contribution equalises the risk shares")
    source(fig, asof=asof)
    return _save(fig, "risk_contribution")


# ---------------------------------------------------------- 14 backtest
def chart_backtest(res: dict, asof: str) -> Path:
    bt = res["backtest"]; cum = bt["cumulative"] * 100; dd = bt["drawdown"]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11.5, 7.2), gridspec_kw={"height_ratios": [2.6, 1]}, sharex=True)
    series = [("Portfolio", S.BLUE, 2.4), ("SOXX", S.ORANGE, 1.5), ("QQQ", S.AQUA, 1.5), ("SPY", S.YELLOW, 1.5)]
    st = bt["stats"]
    for name, c, lw in series:
        if name in cum:
            ax1.plot(cum.index, cum[name], color=c, lw=lw, label=f"{name}: {st.loc[name,'total_return']:+.0%} total · {st.loc[name,'cagr']:.0%} CAGR · {st.loc[name,'vol']:.0%} vol · maxDD {st.loc[name,'max_drawdown']:.0%}")
            ax1.text(cum.index[-1], cum[name].iloc[-1], f" {name}", color=c, fontsize=8.5, va="center", fontweight="bold")
            ax2.plot(dd.index, dd[name], color=c, lw=lw if name == "Portfolio" else 1.1)
    ax1.set_yscale("log"); ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax1.set_ylabel("Growth of 100 (log scale)"); ax1.legend(loc="upper left", fontsize=8.3)
    ax2.fill_between(dd.index, dd["Portfolio"], 0, color=S.BLUE, alpha=0.15); _pctfmt(ax2, "y"); ax2.set_ylabel("Drawdown")
    title(ax1, "HINDSIGHT ILLUSTRATION (invalid as track record): today's target weights replayed vs SPY / QQQ / SOXX",
          f"{bt['window'][0]} to {bt['window'][1]}. Names and weights chosen with today's information, no costs or delistings - read as a risk profile only")
    source(fig, asof=asof)
    return _save(fig, "backtest")


# ---------------------------------------------------------- 15 macro
def chart_macro(panel: dict, asof: str) -> Path:
    spec = [("DGS10", "10-year Treasury yield (%)", "DGS2", "2-year"), ("FEDFUNDS", "Fed funds effective rate (%)", None, None),
            ("CPI_YOY", "Inflation YoY (%): CPI vs core PCE", "COREPCE_YOY", "Core PCE"),
            ("SEMI_IP_YOY", "Semiconductor industrial production YoY (%)", None, None),
            ("SEMI_PPI_YOY", "Semiconductor producer prices YoY (%)", None, None),
            ("CAPGOODS_YOY", "Core capital goods orders YoY (%)", "INDPRO_YOY", "Industrial production")]
    fig, axes = plt.subplots(2, 3, figsize=(15, 7))
    start = pd.Timestamp.today() - pd.DateOffset(years=5)
    for ax, (sid, lab, sid2, lab2) in zip(axes.flat, spec):
        s = panel.get(sid)
        if s is None or s.empty:
            ax.axis("off"); continue
        s = s[s.index >= start]
        ax.plot(s.index, s, color=S.BLUE, lw=1.8, label=lab.split(":")[0] if sid2 else None)
        ax.text(s.index[-1], s.iloc[-1], f" {s.iloc[-1]:.1f}", color=S.BLUE, fontsize=9, fontweight="bold", va="center")
        if sid2 and sid2 in panel:
            s2 = panel[sid2]; s2 = s2[s2.index >= start]
            ax.plot(s2.index, s2, color=S.ORANGE, lw=1.5, label=lab2)
            ax.text(s2.index[-1], s2.iloc[-1], f" {s2.iloc[-1]:.1f}", color=S.ORANGE, fontsize=9, va="center")
            ax.legend(loc="upper left", fontsize=8)
        ax.axhline(0, color=S.GRAY_DARK, lw=0.6) if "YoY" in lab else None
        ax.set_title(lab, fontsize=10.5)
        ax.tick_params(labelsize=8)
    fig.suptitle("Macro backdrop (FRED): rates, inflation and the semiconductor cycle", x=0.01, ha="left", fontsize=13, fontweight="bold", color=S.TEXT)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    source(fig, "Source: FRED (St. Louis Fed); last 5 years; analysis as of {asof}", asof=asof)
    return _save(fig, "macro_backdrop")


# ---------------------------------------------------------- 16 segment structure
def chart_segment_structure(seg: pd.DataFrame, asof: str) -> Path:
    d = seg.dropna(subset=["med_rev_growth_ntm", "med_ev_sales_ntm"])
    fig, ax = plt.subplots(figsize=(11.5, 6.4))
    ax.scatter(d["med_rev_growth_ntm"], d["med_ev_sales_ntm"], s=np.sqrt(d["market_cap_bn"]) * 9 + 30, color=S.BLUE, alpha=0.85, edgecolor=S.SURFACE, linewidth=1)
    ax.set_xlim(d["med_rev_growth_ntm"].min() - 0.08, d["med_rev_growth_ntm"].max() + 0.15); ax.set_ylim(0, d["med_ev_sales_ntm"].max() * 1.35)
    _labels(ax, d["med_rev_growth_ntm"], d["med_ev_sales_ntm"], [f"{i}\n{r['structure']} · upside {pct(r['med_upside'])}" for i, r in d.iterrows()], color=S.TEXT2, size=8)
    _pctfmt(ax, "x"); ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}x"))
    ax.set_xlabel("Segment median NTM revenue growth"); ax.set_ylabel("Segment median EV / NTM sales")
    title(ax, "Industry structure: growth, valuation and concentration by value-chain segment", "Bubble size = aggregate market cap; concentration from revenue HHI")
    source(fig, asof=asof)
    return _save(fig, "segment_structure")


# ---------------------------------------------------------- 17 funnel
def chart_funnel(funnel: list[dict], asof: str) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.2))
    labels = [f["step"] for f in funnel][::-1]; vals = [f["n"] for f in funnel][::-1]
    ramp = [S.SEQ_BLUE[i] for i in np.linspace(3, 11, len(vals)).astype(int)][::-1]
    y = np.arange(len(vals))
    ax.barh(y, vals, color=ramp, height=0.6, edgecolor=S.SURFACE)
    for i, v in enumerate(vals):
        ax.text(v + 1, i, f"{v}", va="center", fontsize=10, fontweight="bold", color=S.TEXT)
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=9.5); ax.set_xlim(0, max(vals) * 1.12)
    ax.set_xlabel("Companies")
    title(ax, "Screening funnel")
    source(fig, asof=asof)
    return _save(fig, "screening_funnel")


# ---------------------------------------------------------- 18 tearsheets
def chart_tearsheet(t: str, r: pd.Series, snap: dict, px: pd.DataFrame, asof: str) -> Path:
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(15, 3.6), gridspec_kw={"width_ratios": [1.6, 1, 1]})
    s = px[t].dropna().tail(504)
    a1.plot(s.index, s, color=S.BLUE, lw=1.6, label="Price")
    a1.plot(s.index, s.rolling(200).mean(), color=S.GRAY_DARK, lw=1, ls="--", label="200-day average")
    fv = r.get("fair_value", np.nan)
    if not np.isnan(fv) and fv <= s.max() * 1.6:
        a1.axhline(fv, color=S.ORANGE, lw=1.2, ls=":", label=f"Blended fair value \\${fv:,.0f}")
    elif not np.isnan(fv):
        a1.text(0.02, 0.06, f"Blended fair value \\${fv:,.0f} ({r.get('upside', 0):+.0%}) - above chart range", transform=a1.transAxes, fontsize=8, color=S.ORANGE)
    a1.legend(loc="upper left", fontsize=8); a1.set_title("2-year price", fontsize=10.5); a1.tick_params(labelsize=8)
    # revenue: annual actuals + consensus
    ia = snap.get("income_annual", {}) or {}
    ann = pd.Series({pd.Timestamp(k).year: (v.get("Total Revenue") or np.nan) for k, v in ia.items()}).sort_index().dropna()
    ann = ann * r.get("fx_to_usd", 1.0)
    xs, ys, cs = [], [], []
    for yr, v in ann.tail(4).items():
        xs.append(f"FY{str(yr)[-2:]}"); ys.append(v / 1e9); cs.append(S.BLUE)
    last_year = int(ann.index[-1]) if len(ann) else pd.Timestamp.today().year
    for k, (lab, col) in enumerate((("revenue_fy0", "E"), ("revenue_fy1", "E"))):
        v = r.get(lab)
        if v and not np.isnan(v):
            xs.append(f"FY{str(last_year + k + 1)[-2:]}E"); ys.append(v / 1e9); cs.append(S.SEQ_BLUE[3])
    a2.bar(xs, ys, color=cs, edgecolor=S.SURFACE)
    for x, v in zip(xs, ys):
        a2.text(x, v, f"{v:,.1f}", ha="center", va="bottom", fontsize=7.5, color=S.TEXT2)
    a2.set_title("Revenue, $bn (light = consensus)", fontsize=10.5); a2.tick_params(labelsize=8)
    # valuation snapshot bars: current vs fair multiples
    items = [("EV/S NTM", r.get("ev_sales_ntm"), r.get("mult_fair_ev_sales")), ("P/E NTM", r.get("pe_ntm"), None)]
    labs = ["EV/Sales NTM\n(actual vs fair)", "P/E NTM"]
    a3.bar([0], [items[0][1] or 0], color=S.BLUE, width=0.35, label="Actual"); a3.bar([0.4], [items[0][2] or 0], color=S.GRAY, width=0.35, label="Regression-fair")
    a3.bar([1.2], [items[1][1] or 0], color=S.BLUE, width=0.35)
    for x, v in ((0, items[0][1]), (0.4, items[0][2]), (1.2, items[1][1])):
        if v and not np.isnan(v):
            a3.text(x, v, f"{v:.1f}x", ha="center", va="bottom", fontsize=8, color=S.TEXT2)
    a3.set_xticks([0.2, 1.2]); a3.set_xticklabels(labs, fontsize=8); a3.legend(fontsize=8, loc="upper right"); a3.set_title("Valuation", fontsize=10.5); a3.tick_params(labelsize=8)
    fig.suptitle(f"{t} · {r.get('name')} · {r.get('segment')}", x=0.01, ha="left", fontsize=12, fontweight="bold", color=S.TEXT)
    fig.tight_layout(rect=(0, 0.03, 1, 0.93))
    source(fig, asof=asof)
    return _save(fig, f"tearsheet_{t}")


def build_all(ctx: dict) -> dict[str, Path]:
    """ctx keys: universe, top, seg, reg, res, panel, funnel, px, snaps, asof, weights"""
    asof = ctx["asof"]; top = ctx["top"]
    paths = {
        "value_chain": chart_value_chain(top, ctx["seg"], asof),
        "score_ranking": chart_score_ranking(top, ctx["weights"], asof),
        "valuation_vs_growth": chart_valuation_vs_growth(ctx["universe"], top, ctx["reg"], asof),
        "upside": chart_upside(top, asof),
        "margins": chart_margins(top, asof),
        "growth": chart_growth(top, asof),
        "quality": chart_quality(ctx["universe"], top, asof),
        "revisions": chart_revisions(top, asof),
        "relative_performance": chart_relative_performance(ctx["px"], top, asof),
        "efficient_frontier": chart_efficient_frontier(ctx["res"], asof),
        "weights": chart_weights(ctx["res"], top, asof),
        "correlation": chart_correlation(ctx["res"], asof),
        "risk_contrib": chart_risk_contrib(ctx["res"], asof),
        "backtest": chart_backtest(ctx["res"], asof),
        "macro": chart_macro(ctx["panel"], asof),
        "segment_structure": chart_segment_structure(ctx["seg"], asof),
        "funnel": chart_funnel(ctx["funnel"], asof),
    }
    for t, r in top.iterrows():
        paths[f"tearsheet_{t}"] = chart_tearsheet(t, r, ctx["snaps"].get(t, {}), ctx["px"], asof)
    return paths
