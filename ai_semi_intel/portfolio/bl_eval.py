"""Evaluation of the Black-Litterman construction: ex-ante comparison, sensitivity, view ablation and a
point-in-time walk-forward test.

Three separate questions, kept separate because they carry very different evidential weight:

1. **Ex-ante comparison** (`compare_constructions`) - given today's covariance and views, how do BL weights
   differ from the production Grinold portfolio, the equilibrium prior and equal weight? Risk, concentration
   and turnover only; no return claim.
2. **Sensitivity / ablation** (`tau_sensitivity`, `confidence_sensitivity`, `view_ablation`) - how much of the
   BL tilt is driven by each view source and by the confidence dials. A construction whose weights swing
   wildly on an arbitrary knob is not trustworthy.
3. **Walk-forward** (`walk_forward`) - the only arm that says anything about returns. Views must be
   point-in-time, so it uses 12-1 price momentum (not the fair-value upside, which exists only as today's
   snapshot). It answers "does adding views to a calibrated equilibrium prior beat the prior itself?", with
   equal-weight, cap-weight and SOXX as references. The universe is today's constituents, so every arm is
   survivorship-biased alike: read the SPREAD between arms, never the level.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..config.settings import CONFIG, PortfolioConfig
from ..utils.io import log
from . import bl, optimizer

TRADING_DAYS = 252


# ----------------------------------------------------------------------------- helpers
def _stats(r: pd.Series, rf: float) -> dict:
    r = r.dropna()
    if len(r) < 2:
        return {}
    yrs = len(r) / TRADING_DAYS
    c = (1 + r).cumprod()
    cagr = float(c.iloc[-1] ** (1 / yrs) - 1)
    vol = float(r.std() * np.sqrt(TRADING_DAYS))
    dd = float((c / c.cummax() - 1).min())
    downside = float(r[r < 0].std() * np.sqrt(TRADING_DAYS)) if (r < 0).any() else np.nan
    return {"cagr": cagr, "vol": vol, "sharpe": (cagr - rf) / vol if vol else np.nan,
            "sortino": (cagr - rf) / downside if downside and not np.isnan(downside) else np.nan,
            "max_drawdown": dd, "total_return": float(c.iloc[-1] - 1), "calmar": cagr / abs(dd) if dd else np.nan,
            "n_days": int(len(r))}


def _hac_t(x: pd.Series, lags: int = 3) -> float:
    """Newey-West t-statistic for mean(x) = 0 (monthly series, so a few lags suffice)."""
    x = x.dropna().astype(float)
    n = len(x)
    if n < 8:
        return float("nan")
    d = x - x.mean()
    g0 = float((d ** 2).mean())
    var = g0
    for L in range(1, min(lags, n - 1) + 1):
        gL = float((d.iloc[L:].values * d.iloc[:-L].values).mean())
        var += 2 * (1 - L / (lags + 1)) * gL
    if var <= 0:
        return float("nan")
    return float(x.mean() / np.sqrt(var / n))


def _effective_n(w: pd.Series) -> float:
    x = w[w > 0]
    return float(1.0 / (x ** 2).sum()) if len(x) else 0.0


def weight_summary(w: pd.Series, mu: pd.Series, cov: pd.DataFrame, rf: float, segments: pd.Series | None = None) -> dict:
    w = w.reindex(cov.index).fillna(0.0)
    vol = float(np.sqrt(w.values @ cov.values @ w.values))
    ret = float(w.values @ mu.reindex(cov.index).fillna(0.0).values)
    out = {"exp_return": ret, "vol": vol, "sharpe": (ret - rf) / vol if vol else np.nan, "invested": float(w.sum()),
           "max_weight": float(w.max()), "effective_n": _effective_n(w), "n_positions": int((w > 1e-6).sum())}
    if segments is not None:
        out["max_segment_weight"] = float(w.groupby(segments.reindex(w.index)).sum().max())
    return out


# ----------------------------------------------------------------------------- 1. ex-ante comparison
def compare_constructions(top: pd.DataFrame, px: pd.DataFrame, rf: float, cfg: PortfolioConfig | None = None,
                          erp: float = 0.045, overlay: pd.DataFrame | None = None,
                          sources: tuple[str, ...] = ("fair_value", "event_score")) -> dict:
    """BL (equilibrium and CAPM priors) vs the production Grinold portfolio, prior-only and equal weight."""
    cfg = cfg or CONFIG.portfolio
    R, rinfo = optimizer.build_returns(px, list(top.index), top["segment"], cfg)
    cov = optimizer.shrunk_cov(R)
    tick = list(cov.index)
    tp = top.reindex(tick)
    betas = optimizer.realised_betas(R, px)
    segments = tp["segment"]

    # --- production construction (Grinold alphas on the sample covariance)
    mu_grin = optimizer.grinold_alphas(tp, cov, rf, betas, erp, cfg.grinold_ic)
    opt_grin = optimizer.optimise(mu_grin, cov, rf, segments, cfg)

    # --- BL, both priors
    res = {}
    for prior in ("equilibrium", "capm"):
        res[prior] = bl.run_bl(tp, cov, rf, cfg, betas=betas, erp=erp, overlay=overlay, prior=prior, sources=sources)

    # --- prior-only (no views) and equal weight, as references
    pi, w_mkt, delta = bl.equilibrium_prior(cov, tp["market_cap"], None, betas, erp)
    prior_only = bl.posterior(cov, pi, [], cfg.bl_tau, rf)
    opt_prior = optimizer.optimise(prior_only["mu"], cov, rf, segments, cfg)

    beq = res["equilibrium"]
    mu_ref = beq["mu"]                                        # score every construction on the same BL posterior
    w_mv_bl = bl.mean_variance_weights(beq["mu"], beq["cov_used"], rf, delta_cal := bl.calibrate_delta(cov, w_mkt_cal := bl.equilibrium_prior(cov, tp["market_cap"], None, betas, erp)[1], betas, erp), segments, cfg)
    w_mv_prior = bl.mean_variance_weights(pd.Series(bl.equilibrium_prior(cov, tp["market_cap"], None, betas, erp)[0] + rf), cov, rf, delta_cal, segments, cfg)
    cons = {
        "bl_meanvariance": {"weights": w_mv_bl, "mu": beq["mu"], "cov": beq["cov_used"]},
        "prior_meanvariance": {"weights": w_mv_prior, "mu": pd.Series(bl.equilibrium_prior(cov, tp["market_cap"], None, betas, erp)[0] + rf, index=tick), "cov": cov},
        "bl_equilibrium": {"weights": beq["opt"][cfg.primary]["weights"], "mu": beq["mu"], "cov": beq["cov_used"]},
        "bl_capm": {"weights": res["capm"]["opt"][cfg.primary]["weights"], "mu": res["capm"]["mu"], "cov": res["capm"]["cov_used"]},
        "bl_equilibrium_maxsharpe": {"weights": beq["opt"]["max_sharpe"]["weights"], "mu": beq["mu"], "cov": beq["cov_used"]},
        "prior_only": {"weights": opt_prior[cfg.primary]["weights"], "mu": prior_only["mu"], "cov": cov},
        "production_grinold": {"weights": opt_grin[cfg.primary]["weights"], "mu": mu_grin, "cov": cov},
        "production_maxsharpe": {"weights": opt_grin["max_sharpe"]["weights"], "mu": mu_grin, "cov": cov},
        "equal_weight": {"weights": pd.Series(1.0 / len(tick), index=tick), "mu": mu_ref, "cov": cov},
        "cap_weight": {"weights": w_mkt, "mu": mu_ref, "cov": cov},
    }
    table, weights = {}, {}
    base = cons["production_grinold"]["weights"]
    for name, c in cons.items():
        w = c["weights"].reindex(tick).fillna(0.0)
        row = weight_summary(w, c["mu"], c["cov"], rf, segments)
        row["own_exp_return"] = row.pop("exp_return")
        ref = weight_summary(w, mu_ref, beq["cov_used"], rf, segments)
        row["exp_return_bl"] = ref["exp_return"]
        row["sharpe_bl"] = ref["sharpe"]
        row["turnover_vs_production"] = float(0.5 * (w - base.reindex(tick).fillna(0.0)).abs().sum())
        table[name] = row
        weights[name] = w.round(5).to_dict()
    # Does the primary construction read expected returns at all? Isolate mu by holding the covariance fixed:
    # feed the same optimiser two very different mu vectors and see whether the weights move.
    w_lo = optimizer.optimise(pd.Series(rf + 0.01, index=tick), cov, rf, segments, cfg)[cfg.primary]["weights"]
    w_hi = optimizer.optimise(mu_grin * 3.0, cov, rf, segments, cfg)[cfg.primary]["weights"]
    mu_sensitivity = float(0.5 * (w_lo.reindex(tick).fillna(0) - w_hi.reindex(tick).fillna(0)).abs().sum())
    mu_agnostic = mu_sensitivity < 1e-3
    return {"mu_agnostic_primary": mu_agnostic, "primary_reads_mu": not mu_agnostic, "mu_sensitivity_turnover": mu_sensitivity,
            "tickers": tick, "returns_info": rinfo, "rf": rf, "erp": erp, "delta": delta,
            "prior_basket_return": beq["prior_basket_return"], "table": table, "weights": weights,
            "mu": {"prior": beq["prior"].round(5).to_dict(), "posterior": beq["mu"].round(5).to_dict(),
                   "tilt": beq["tilt"].round(5).to_dict(), "grinold": mu_grin.round(5).to_dict(),
                   "views_by_name": {v.weights and list(v.weights)[0] if len(v.weights) == 1 else v.name: v.q for v in beq["views"]}},
            "view_table": beq["view_table"].to_dict(orient="records"), "n_views": beq["n_views"],
            "attribution": beq["attribution"].round(5).to_dict(), "betas": betas.reindex(tick).round(3).to_dict(),
            "vols": pd.Series(np.sqrt(np.diag(cov.values)), index=tick).round(4).to_dict(),
            "cov_diag_inflation": float((np.diag(beq["cov_used"].values) / np.diag(cov.values)).mean() - 1),
            "sources": list(sources), "primary": cfg.primary}


# ----------------------------------------------------------------------------- 2. sensitivity & ablation
def tau_sensitivity(top: pd.DataFrame, cov: pd.DataFrame, rf: float, betas: pd.Series, cfg: PortfolioConfig,
                    erp: float, overlay: pd.DataFrame | None, sources: tuple[str, ...],
                    taus: tuple[float, ...] = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)) -> list[dict]:
    """tau sweep in ``omega_mode='absolute'`` - the only mode where tau is not algebraically cancelled."""
    out = []
    base = None
    delta = bl.calibrate_delta(cov, bl.equilibrium_prior(cov, top["market_cap"], None, betas, erp)[1], betas, erp)
    for t in taus:
        r = bl.run_bl(top, cov, rf, cfg, betas=betas, erp=erp, overlay=overlay, sources=sources, tau=t, omega_mode="absolute")
        w = bl.mean_variance_weights(r["mu"], r["cov_used"], rf, delta, top["segment"], cfg)
        base = w if base is None else base
        out.append({"tau": t, "mean_abs_tilt_bps": float(r["tilt"].abs().mean() * 1e4), "max_weight": float(w.max()),
                    "effective_n": _effective_n(w), "turnover_vs_lowest_tau": float(0.5 * (w - base).abs().sum()),
                    "exp_return": float(r["opt"][cfg.primary]["exp_return"]), "vol": float(r["opt"][cfg.primary]["vol"])})
    return out


def confidence_sensitivity(top: pd.DataFrame, cov: pd.DataFrame, rf: float, betas: pd.Series, cfg: PortfolioConfig,
                           erp: float, overlay: pd.DataFrame | None,
                           scales: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0)) -> list[dict]:
    """Scale every view's confidence up/down; weights from the mean-variance (mu-consuming) book."""
    out, base = [], None
    for s in scales:
        views = bl.build_views(top, rf, cfg, overlay, ("fair_value", "event_score"))
        for v in views:
            v.confidence = float(np.clip(v.confidence * s, 0.01, 1.0))
        pi, w_mkt, delta = bl.equilibrium_prior(cov, top["market_cap"], None, betas, erp)
        post = bl.posterior(cov, pi, views, cfg.bl_tau, rf)
        w = bl.mean_variance_weights(post["mu"], post["cov_post"], rf, bl.calibrate_delta(cov, w_mkt, betas, erp), top["segment"], cfg)
        base = w if base is None else base
        out.append({"confidence_scale": s, "mean_abs_tilt_bps": float(post["tilt"].abs().mean() * 1e4),
                    "max_weight": float(w.max()), "effective_n": _effective_n(w),
                    "turnover_vs_lowest": float(0.5 * (w - base).abs().sum())})
    return out


def view_ablation(top: pd.DataFrame, cov: pd.DataFrame, rf: float, betas: pd.Series, cfg: PortfolioConfig,
                  erp: float, overlay: pd.DataFrame | None) -> list[dict]:
    """Each view source alone and in combination, measured on the mean-variance (mu-consuming) book."""
    combos = [(), ("fair_value",), ("event_score",), ("analyst_target",), ("segment_ranking",),
              ("fair_value", "event_score"), ("fair_value", "event_score", "analyst_target"),
              ("fair_value", "event_score", "segment_ranking")]
    out, prior_w = [], None
    delta = bl.calibrate_delta(cov, bl.equilibrium_prior(cov, top["market_cap"], None, betas, erp)[1], betas, erp)
    for c in combos:
        r = bl.run_bl(top, cov, rf, cfg, betas=betas, erp=erp, overlay=overlay, sources=c)
        w = bl.mean_variance_weights(r["mu"], r["cov_used"], rf, delta, top["segment"], cfg)
        if not c:
            prior_w = w
        out.append({"sources": "+".join(c) or "prior_only", "n_views": r["n_views"],
                    "mean_abs_tilt_bps": float(r["tilt"].abs().mean() * 1e4), "max_tilt_bps": float(r["tilt"].abs().max() * 1e4),
                    "max_weight": float(w.max()), "effective_n": _effective_n(w),
                    "turnover_vs_prior_only": float(0.5 * (w - prior_w).abs().sum()) if prior_w is not None else 0.0,
                    "exp_return": float(w @ r["mu"].reindex(w.index)), "vol": float(np.sqrt(w.values @ r["cov_used"].reindex(index=w.index, columns=w.index).values @ w.values)),
                    "top3": ",".join(w.sort_values(ascending=False).head(3).index)})
    return out


# ----------------------------------------------------------------------------- 3. walk-forward (PIT views)
def _bounded_max_sharpe(mu: np.ndarray, S: np.ndarray, rf: float, lo: float, hi: float) -> np.ndarray:
    n = len(mu)
    x0 = np.full(n, 1.0 / n)

    def neg(w):
        v = np.sqrt(max(w @ S @ w, 1e-12))
        return -(w @ mu - rf) / v
    r = minimize(neg, x0, method="SLSQP", bounds=[(lo, hi)] * n,
                 constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}], options={"maxiter": 200, "ftol": 1e-8})
    w = r.x if r.success else x0
    w = np.clip(w, lo, hi)
    return w / w.sum()


def walk_forward(long_px: pd.DataFrame, universe: pd.DataFrame, rf: float = 0.03, start: str = "2013-01-01",
                 cov_years: int = 2, top_n: int = 15, min_names: int = 8, mom_scale: float = 0.06,
                 view_conf: float = 0.5, erp: float = 0.045, max_w: float = 0.15, min_w: float = 0.0,
                 bench: tuple[str, ...] = ("SOXX", "SPY"), cost_bps: float = 10.0) -> dict:
    """Monthly-rebalanced BL with point-in-time momentum views vs prior-only, equal-weight, cap-weight and benchmarks.

    At each month end t, using only data up to t: trailing ``cov_years`` covariance (Ledoit-Wolf), a calibrated
    equilibrium prior on price-based cap weights, and absolute views q = mom_scale x z(12-1 momentum). Names are
    the ``top_n`` by momentum among those with a full covariance window. Returns are net of ``cost_bps`` one-way
    turnover cost. Survivorship-biased in level (today's constituents); the arms share that bias, so compare arms.
    """
    px = long_px.copy()
    px.index = pd.to_datetime(px.index)
    if getattr(px.index, "tz", None) is not None:
        px.index = px.index.tz_localize(None)
    tickers = [t for t in universe.index if t in px.columns]
    caps = universe["market_cap"].reindex(tickers)
    shares = (caps / px[tickers].iloc[-1]).replace([np.inf, -np.inf], np.nan)     # cap proxy: shares x historical price
    rets = px[tickers].pct_change(fill_method=None)
    month_ends = px.loc[px.index >= pd.Timestamp(start)].resample("ME").last().index
    month_ends = [d for d in month_ends if d in px.index or (px.index <= d).any()]
    arms = ["bl_views", "prior_only", "equal_weight", "cap_weight"]
    daily: dict[str, list] = {a: [] for a in arms}
    dates: list[pd.Timestamp] = []
    prev_w: dict[str, pd.Series] = {a: pd.Series(dtype=float) for a in arms}
    rows, cost_paid = [], {a: 0.0 for a in arms}
    for i, d in enumerate(month_ends[:-1]):
        hist_end = px.index[px.index <= d][-1]
        nxt = month_ends[i + 1]
        hist = px.loc[px.index <= hist_end, tickers]
        window = hist.loc[hist.index >= hist_end - pd.DateOffset(years=cov_years)]
        r_win = window.pct_change(fill_method=None).iloc[1:]
        ok = [t for t in tickers if r_win[t].notna().sum() >= int(cov_years * TRADING_DAYS * 0.85)]
        if len(ok) < min_names:
            continue
        # PIT 12-1 momentum
        p_now = hist[ok].iloc[-1]
        try:
            p_1m = hist[ok].loc[hist.index <= hist_end - pd.DateOffset(months=1)].iloc[-1]
            p_12m = hist[ok].loc[hist.index <= hist_end - pd.DateOffset(months=12)].iloc[-1]
        except IndexError:
            continue
        mom = (p_1m / p_12m - 1).dropna()
        if len(mom) < min_names:
            continue
        sel = list(mom.sort_values(ascending=False).head(top_n).index)
        Rw = r_win[sel].dropna(how="any")
        if len(Rw) < int(cov_years * TRADING_DAYS * 0.6):
            continue
        cov = optimizer.shrunk_cov(Rw)
        cap_now = (shares.reindex(sel) * p_now.reindex(sel)).replace([np.inf, -np.inf], np.nan)
        if cap_now.isna().all():
            cap_now = pd.Series(1.0, index=sel)
        cap_now = cap_now.fillna(cap_now.median())
        bench_px = px["SPY"] if "SPY" in px.columns else None
        b = pd.Series(1.2, index=sel)
        if bench_px is not None:
            br = bench_px.loc[Rw.index].pct_change().fillna(0.0)
            var_b = float(br.var())
            if var_b > 0:
                b = pd.Series([float(np.cov(Rw[t], br)[0, 1] / var_b) for t in sel], index=sel).clip(0.3, 3.0)
        pi, w_mkt, delta = bl.equilibrium_prior(cov, cap_now, None, b, erp)
        z = (mom.reindex(sel) - mom.reindex(sel).mean()) / (mom.reindex(sel).std() or 1.0)
        views = [bl.View(f"MOM:{t}", {t: 1.0}, float(np.clip(z[t] * mom_scale, -0.25, 0.35)), view_conf, "momentum") for t in sel]
        post = bl.posterior(cov, pi, views, 0.05, rf)
        w_bl = pd.Series(_bounded_max_sharpe(post["mu"].reindex(sel).values, post["cov_post"].reindex(index=sel, columns=sel).values, rf, min_w, max_w), index=sel)
        w_pr = pd.Series(_bounded_max_sharpe((pi + rf).reindex(sel).values, cov.reindex(index=sel, columns=sel).values, rf, min_w, max_w), index=sel)
        w_now = {"bl_views": w_bl, "prior_only": w_pr, "equal_weight": pd.Series(1.0 / len(sel), index=sel), "cap_weight": w_mkt.reindex(sel).fillna(0) / w_mkt.reindex(sel).fillna(0).sum()}
        seg = px.index[(px.index > hist_end) & (px.index <= nxt)]
        if not len(seg):
            continue
        rr = rets.loc[seg, sel].fillna(0.0)
        turnover_now = {}
        for a in arms:
            w = w_now[a]
            to = float(0.5 * (w.reindex(sel).fillna(0) - prev_w[a].reindex(sel).fillna(0)).abs().sum()) if len(prev_w[a]) else 1.0
            turnover_now[a] = to
            c = to * 2 * cost_bps / 1e4
            cost_paid[a] += c
            path = (rr @ w.reindex(sel).fillna(0)).astype(float)
            if len(path):
                path.iloc[0] -= c                                     # charge rebalance cost on the first day
            daily[a].append(path)
            prev_w[a] = w
        dates.append(hist_end)
        rows.append({"date": str(hist_end.date()), "n_names": len(sel), "delta": delta,
                     "mean_abs_tilt_bps": float(post["tilt"].abs().mean() * 1e4),
                     "top_names": ",".join(sel[:5]), "bl_max_w": float(w_bl.max()), "bl_eff_n": _effective_n(w_bl),
                     "turnover_bl": turnover_now["bl_views"], "turnover_prior": turnover_now["prior_only"],
                     "bl_top_weight_name": str(w_bl.idxmax())})
    if not dates:
        return {"available": False, "reason": "not enough history for a walk-forward"}
    series = {a: pd.concat(daily[a]).sort_index() for a in arms}
    idx = series["bl_views"].index
    for bnm in bench:
        if bnm in px.columns:
            series[bnm] = px[bnm].reindex(idx).pct_change().fillna(0.0)
    stats = {a: _stats(s, rf) for a, s in series.items()}
    # is BL - prior_only different from zero?
    diff = (series["bl_views"] - series["prior_only"]).dropna()
    n = len(diff)
    t_naive = float(diff.mean() / (diff.std() / np.sqrt(n))) if n > 2 and diff.std() > 0 else np.nan
    m = pd.DataFrame({a: series[a] for a in arms}).resample("ME").apply(lambda x: (1 + x).prod() - 1)
    monthly_win = float((m["bl_views"] > m["prior_only"]).mean())
    # every arm compared against BL, on monthly returns with Newey-West inference
    versus = {}
    for a in [x for x in arms if x != "bl_views"] + [b for b in bench if b in series]:
        if a not in m.columns:
            m[a] = series[a].resample("ME").apply(lambda x: (1 + x).prod() - 1)
        d = (m["bl_views"] - m[a]).dropna()
        sa, sb = stats.get("bl_views", {}), stats.get(a, {})
        versus[a] = {"annualised_excess": float(sa.get("cagr", np.nan) - sb.get("cagr", np.nan)),
                     "sharpe_delta": float(sa.get("sharpe", np.nan) - sb.get("sharpe", np.nan)),
                     "vol_delta": float(sa.get("vol", np.nan) - sb.get("vol", np.nan)),
                     "monthly_mean_bps": float(d.mean() * 1e4), "t_hac": _hac_t(d), "t_naive": float(d.mean() / (d.std() / np.sqrt(len(d)))) if d.std() > 0 else np.nan,
                     "win_rate": float((d > 0).mean()), "n_months": int(len(d))}
    cum = pd.DataFrame({k: (1 + v).cumprod() for k, v in series.items()})
    return {"available": True, "arms": arms, "benchmarks": [b for b in bench if b in series],
            "window": (str(idx[0].date()), str(idx[-1].date())), "n_rebalances": len(dates), "stats": stats,
            "excess_vs_prior": {"annualised": float(stats["bl_views"]["cagr"] - stats["prior_only"]["cagr"]),
                                "sharpe_delta": float(stats["bl_views"]["sharpe"] - stats["prior_only"]["sharpe"]),
                                "t_naive": t_naive, "t_hac_monthly": _hac_t((m["bl_views"] - m["prior_only"]).dropna()),
                                "monthly_win_rate": monthly_win, "n_days": n},
            "versus": versus,
            "cumulative": {"dates": [d.strftime("%Y-%m-%d") for d in cum.index], **{k: cum[k].round(4).tolist() for k in cum.columns}},
            "monthly": [{"date": d.strftime("%Y-%m"), **{a: (None if pd.isna(row[a]) else round(float(row[a]), 4)) for a in arms}} for d, row in m.iterrows()],
            "rebalances": rows, "total_cost": cost_paid, "params": {"cov_years": cov_years, "top_n": top_n, "mom_scale": mom_scale,
                                                                    "view_conf": view_conf, "max_w": max_w, "cost_bps": cost_bps, "rf": rf, "start": start}}
