"""Interactive Streamlit KPI dashboard.   Run:  streamlit run ai_semi_intel/dashboard/app.py"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
LATEST, HISTORY, CHARTS = ROOT / "outputs" / "latest", ROOT / "outputs" / "history", ROOT / "outputs" / "charts"
st.set_page_config(page_title="AI & Semi Value Chain Monitor", layout="wide", page_icon="📈")


@st.cache_data(ttl=300)
def load():
    d = {"summary": json.load(open(LATEST / "summary.json")),
         "top": pd.read_csv(LATEST / "top15.csv", index_col=0), "kpi": pd.read_csv(LATEST / "kpi_table.csv", index_col=0),
         "uni": pd.read_csv(LATEST / "universe_scores.csv", index_col=0), "w": pd.read_csv(LATEST / "portfolio_weights.csv", index_col=0),
         "pstats": pd.read_csv(LATEST / "portfolio_stats.csv", index_col=0), "astats": pd.read_csv(LATEST / "asset_stats.csv", index_col=0),
         "corr": pd.read_csv(LATEST / "correlation.csv", index_col=0), "bt": pd.read_csv(LATEST / "backtest_cumulative.csv", index_col=0, parse_dates=True),
         "construction_bt": pd.read_csv(LATEST / "construction_backtest_cumulative.csv", index_col=0, parse_dates=True),
         "construction_dd": pd.read_csv(LATEST / "construction_backtest_drawdown.csv", index_col=0, parse_dates=True),
         "construction_btstats": pd.read_csv(LATEST / "construction_backtest_stats.csv", index_col=0),
         "btstats": pd.read_csv(LATEST / "backtest_stats.csv", index_col=0), "seg": pd.read_csv(LATEST / "segment_table.csv", index_col=0),
         "macro": pd.read_csv(LATEST / "macro_snapshot.csv", index_col=0), "frontier": pd.read_csv(LATEST / "frontier.csv"),
         "px": pd.read_csv(LATEST / "prices_top15.csv", index_col=0, parse_dates=True)}
    d["runs"] = pd.read_csv(HISTORY / "runs.csv") if (HISTORY / "runs.csv").exists() else None
    d["phist"] = pd.read_csv(HISTORY / "portfolio_history.csv") if (HISTORY / "portfolio_history.csv").exists() else None
    return d


if not (LATEST / "summary.json").exists():
    st.error("No pipeline output yet. Run:  python -m ai_semi_intel.pipeline")
    st.stop()
D = load(); S = D["summary"]
st.sidebar.title("Controls")
st.sidebar.caption(f"Data as of {S['asof']} · generated {S['generated_at']}")
if st.sidebar.button("Re-run pipeline now (5-10 min)"):
    with st.spinner("Running pipeline..."):
        r = subprocess.run([sys.executable, "-m", "ai_semi_intel.pipeline", "--stages", "data", "analysis", "portfolio", "persist", "charts", "dashboard"], cwd=ROOT, capture_output=True, text=True)
    st.sidebar.code(r.stdout[-2000:] + r.stderr[-1500:]); st.cache_data.clear(); st.rerun()
METHOD_LABELS = {"recommended": "Executable target · ERC × vol target", "cap_weight": "Cap weight", "inverse_vol": "Inverse volatility",
                 "risk_parity": "ERC / risk parity", "segment_risk_parity": "Segment risk parity", "min_variance": "Minimum variance",
                 "max_diversification": "Maximum diversification", "hrp": "HRP", "max_sharpe": "Max Sharpe",
                 "mean_variance": "Mean-variance / Black-Litterman", "kelly": "Kelly", "resampled_max_sharpe": "Resampled max Sharpe",
                 "min_cvar": "Minimum CVaR", "equal_weight": "Equal weight"}
METHOD_ORDER = list(METHOD_LABELS)
available_methods = [m for m in METHOD_ORDER if m in D["w"].columns and m in D["pstats"].index]
port = st.sidebar.selectbox("Portfolio construction", available_methods, format_func=lambda m: METHOD_LABELS[m])
st.sidebar.markdown("**Sources**: " + " · ".join(S["data_sources"][:3]))
st.sidebar.caption(S["pitchbook"]["status"])

st.title("AI & Semiconductor Value Chain — Daily KPI Monitor")
ms = S["portfolio"].get(port, D["pstats"].loc[port].to_dict()); top = D["top"]; kpi = D["kpi"]
c = st.columns(6)
c[0].metric("Expected return", f"{ms['exp_return']:.1%}"); c[1].metric("Volatility", f"{ms['vol']:.1%}"); c[2].metric("Sharpe", f"{ms['sharpe']:.2f}")
c[3].metric("Median upside (top 15)", f"{top['upside'].median():+.0%}"); c[4].metric("Alerts", len(S["alerts"]), f"{sum(a['severity']=='critical' for a in S['alerts'])} critical")
c[5].metric("Turnover vs prev run", f"{S['changes'].get('turnover', 0):.0%}", f"{len(S['changes'].get('entries', []))} in / {len(S['changes'].get('exits', []))} out")

tabs = st.tabs(["Alerts", "Top 15", "Portfolio", "Universe", "Macro", "History", "Charts", "Model health"])
with tabs[0]:
    for a in S["alerts"]:
        icon = {"critical": "🔴", "warning": "🟠", "info": "🔵"}[a["severity"]]
        st.write(f"{icon} **{a['ticker']}** · {a['type']} — {a['message']}")
    ch = S["changes"]
    if ch.get("prev_date"):
        st.info(f"vs {ch['prev_date']}: entries {ch['entries'] or 'none'} · exits {ch['exits'] or 'none'} · turnover {ch['turnover']:.0%}")
with tabs[1]:
    k = kpi.sort_values("rank").copy(); k["weight"] = D["w"][port].reindex(k.index)
    fmt = {"weight": "{:.1%}", "price": "${:,.0f}", "ret_1d": "{:+.1%}", "ret_1w": "{:+.1%}", "ret_1m": "{:+.1%}", "fair_value": "${:,.0f}", "upside": "{:+.0%}", "composite": "{:.2f}", "alpha_score": "{:.2f}", "score_growth": "{:.2f}", "score_momentum": "{:.2f}", "score_value": "{:.2f}", "score_scarcity": "{:.2f}", "score_risk": "{:.2f}", "score_risk_resilience": "{:.2f}",
           "ev_sales_ntm": "{:.1f}x", "pe_ntm": "{:.1f}x", "rev_growth_ntm": "{:.0%}", "fcf_margin": "{:.0%}", "eps_rev_30d": "{:+.1%}", "eps_rev_90d": "{:+.1%}", "vs_200dma": "{:+.0%}", "market_cap": "{:,.0f}"}
    st.dataframe(k.drop(columns=["latest_10k_url"]).style.format(fmt, na_rep=""), use_container_width=True, height=600)
    t = st.selectbox("Stock detail", list(k.index))
    r = top.loc[t]
    st.markdown(f"**{r['name']}** — {r['segment']} / {r['sub_segment']} · {r['description']}")
    cc = st.columns(4)
    cc[0].metric("Blended fair value", f"${r['fair_value']:,.0f}", f"{r['upside']:+.0%}"); cc[1].metric("DCF / regression / target", f"{r['dcf_upside']:+.0%} / {r['mult_upside']:+.0%} / {r['target_upside']:+.0%}")
    cc[2].metric("Implied growth (reverse DCF)", f"{r['implied_growth']:.0%}"); cc[3].metric("WACC", f"{r['wacc']:.1%}")
    if (CHARTS / f"tearsheet_{t}.png").exists():
        st.image(str(CHARTS / f"tearsheet_{t}.png"), use_column_width=True)
    if isinstance(r.get("latest_10k_url"), str):
        st.markdown(f"[Latest 10-K / 20-F on EDGAR]({r['latest_10k_url']})")
with tabs[2]:
    w = D["w"][port].sort_values()
    a, b = st.columns(2)
    a.plotly_chart(px.bar(w, orientation="h", title=f"{port} weights", labels={"value": "weight", "index": ""}).update_layout(showlegend=False, yaxis_title="", xaxis_tickformat=".0%"), use_container_width=True)
    b.dataframe(D["pstats"].style.format({"exp_return": "{:.1%}", "vol": "{:.1%}", "sharpe": "{:.2f}"}), use_container_width=True)
    b.dataframe(D["astats"].style.format("{:.2f}"), use_container_width=True)
    fr = D["frontier"]
    fig = go.Figure(); fig.add_trace(go.Scatter(x=fr["vol"], y=fr["target_return"], mode="lines", name="Frontier"))
    fig.add_trace(go.Scatter(x=D["astats"]["vol"], y=D["astats"]["exp_return_bl"], mode="markers+text", text=D["astats"].index, textposition="top center", name="Stocks"))
    for k2, r2 in D["pstats"].iterrows():
        fig.add_trace(go.Scatter(x=[r2["vol"]], y=[r2["exp_return"]], mode="markers", marker=dict(size=14), name=k2))
    fig.update_layout(title="Efficient frontier", xaxis_tickformat=".0%", yaxis_tickformat=".0%")
    st.plotly_chart(fig, use_container_width=True)
    benchmarks = [x for x in ("SPY", "QQQ", "SOXX") if x in D["construction_bt"].columns]
    bt = D["construction_bt"][[port, *benchmarks]] * 100
    bt = bt.rename(columns={port: METHOD_LABELS[port]})
    st.plotly_chart(px.line(bt, title="Hindsight illustration — today's selected weights replayed; invalid as track record", log_y=True).update_layout(yaxis_title="growth of 100", xaxis_title=""), use_container_width=True)
    btstats = D["construction_btstats"].loc[[x for x in (port, *benchmarks) if x in D["construction_btstats"].index]].rename(index={port: METHOD_LABELS[port]})
    st.dataframe(btstats.style.format("{:.2f}"), use_container_width=True)
    st.plotly_chart(px.imshow(D["corr"], color_continuous_scale=[[0, "#e34948"], [0.5, "#f0efec"], [1, "#2a78d6"]], zmin=-1, zmax=1, title="Correlations"), use_container_width=True)
with tabs[3]:
    u = D["uni"].sort_values("composite", ascending=False)
    segs = st.multiselect("Segments", sorted(u["segment"].unique()), default=sorted(u["segment"].unique()))
    u = u[u["segment"].isin(segs)]
    st.plotly_chart(px.scatter(u, x="rev_growth_ntm", y="ev_sales_ntm", size="market_cap", color="segment", hover_name=u.index, log_y=True, title="EV/NTM sales vs NTM growth").update_layout(xaxis_tickformat=".0%"), use_container_width=True)
    cols = ["name", "segment", "style", "composite", "score_value", "score_growth", "score_momentum", "score_scarcity", "score_risk", "alpha_score", "risk_flags", "score_quality", "market_cap", "upside", "ev_sales_ntm", "pe_ntm", "rev_growth_ntm", "gross_margin", "fcf_margin", "roic", "eps_rev_90d", "eligible"]
    st.dataframe(u[cols].style.format({"composite": "{:.2f}", "alpha_score": "{:.2f}", "score_scarcity": "{:.2f}", "score_risk": "{:.2f}", "score_value": "{:.2f}", "score_growth": "{:.2f}", "score_quality": "{:.2f}", "score_momentum": "{:.2f}", "market_cap": "{:,.0f}", "upside": "{:+.0%}", "ev_sales_ntm": "{:.1f}x", "pe_ntm": "{:.1f}x", "rev_growth_ntm": "{:.0%}", "gross_margin": "{:.0%}", "fcf_margin": "{:.0%}", "roic": "{:.0%}", "eps_rev_90d": "{:+.1%}"}, na_rep=""), use_container_width=True, height=600)
    st.dataframe(D["seg"].style.format("{:.2f}", na_rep=""), use_container_width=True)
with tabs[4]:
    st.dataframe(D["macro"].style.format({"value": "{:.2f}", "chg_3m": "{:+.2f}", "chg_12m": "{:+.2f}"}), use_container_width=True)
    if (CHARTS / "macro_backdrop.png").exists():
        st.image(str(CHARTS / "macro_backdrop.png"), use_column_width=True)
with tabs[5]:
    if D["runs"] is not None and D["runs"]["date"].nunique() > 1:
        st.plotly_chart(px.area(D["runs"], x="date", y="weight", color="ticker", title="Target weights over runs").update_layout(yaxis_tickformat=".0%"), use_container_width=True)
        st.plotly_chart(px.line(D["runs"], x="date", y="rank", color="ticker", title="Rank over runs").update_yaxes(autorange="reversed"), use_container_width=True)
        st.dataframe(D["phist"], use_container_width=True)
    else:
        st.info("History charts appear after the second daily run.")
with tabs[6]:
    for p in sorted(CHARTS.glob("*.png")):
        if not p.name.startswith("tearsheet_"):
            st.image(str(p), caption=p.stem, use_column_width=True)

with tabs[7]:
    vpath = ROOT / "outputs" / "validation" / "summary.json"
    if vpath.exists():
        V = json.load(open(vpath))
        st.markdown(V["interpretation"])
        ic = pd.DataFrame(V["ic"])
        st.dataframe(ic.pivot(index="signal", columns="horizon", values="mean_ic")[["1m", "3m", "6m", "12m"]].style.format("{:.3f}"), use_container_width=True)
        st.dataframe(pd.DataFrame(V["backtest_stats"]).T.style.format("{:.3f}"), use_container_width=True)
        for name in ("ic_timeseries_3m", "ic_decay", "quintiles", "top15_backtest", "permutation_random_weights", "feature_ic"):
            pth = ROOT / "outputs" / "validation" / "charts" / f"{name}.png"
            if pth.exists():
                st.image(str(pth), use_column_width=True)
        st.markdown((ROOT / "outputs" / "validation" / "report.md").read_text())
    else:
        st.info("Run python scripts/run_validation.py to produce the point-in-time validation.")
    lpath = LATEST / "live_validation.json"
    if lpath.exists():
        st.subheader("Live forward IC (daily archive)"); st.json(json.load(open(lpath)))
