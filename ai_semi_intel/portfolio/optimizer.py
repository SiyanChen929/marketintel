"""Portfolio construction for the top-15 list.

Pipeline: common-window observed returns -> Ledoit-Wolf covariance -> 13 constrained
construction methods. Risk-only methods use covariance; most return-aware methods use
Grinold expected returns; ``mean_variance`` uses the Black-Litterman posterior. The
canonical executable target remains ERC scaled to the configured volatility target.
Every method has its own clearly labelled hindsight replay versus SPY / QQQ / SOXX.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from ..config.settings import CONFIG, PortfolioConfig
from ..utils.io import log
from . import bl as bl_model
from . import constructions as construction_lib

TRADING_DAYS = 252
CONSTRUCTION_METHODS = tuple(construction_lib.REGISTRY)
CONSTRUCTION_ALIASES = {"min_vol": "min_variance"}


# ----------------------------------------------------------------------- returns
def build_returns(px: pd.DataFrame, tickers: list[str], segments: pd.Series,
                  cfg: PortfolioConfig | None = None) -> tuple[pd.DataFrame, dict]:
    """Daily simple returns over a common observed window.

    No pre-IPO or missing price is replaced with peer performance. Names below the
    individual-history floor are excluded, and an insufficient common window fails closed.
    """
    cfg = cfg or CONFIG.portfolio
    end = px.index[-1]
    start = end - pd.DateOffset(years=cfg.lookback_years)
    p = px.loc[px.index >= start]
    r = p.pct_change(fill_method=None)
    r = r.iloc[1:]
    info = {"start": str(r.index[0].date()), "end": str(r.index[-1].date()), "backfilled_days": {},
            "method": "common_observed_window", "excluded_short_history": {}}
    keep = []
    for t in tickers:
        if t not in r.columns:
            log.warning("no prices for %s - excluded from portfolio", t)
            continue
        n = r[t].notna().sum()
        if n < cfg.min_history_days:
            log.warning("%s has only %d days of history - excluded", t, n)
            info["excluded_short_history"][t] = int(n)
            continue
        keep.append(t)
    R = r[keep].copy()
    before = len(R)
    first_common = max((r[t].first_valid_index() for t in keep), default=R.index[0])
    inside = R.loc[R.index >= first_common]
    gap_rows = inside[inside.isna().any(axis=1)]
    info["missing_by_ticker"] = {t: int(n) for t, n in gap_rows.isna().sum().items() if n > 0}
    info["dropped_dates"] = [str(d.date()) for d in gap_rows.index]          # trading days inside the common window lost to a missing price
    info["window_start_reason"] = f"first common observation ({first_common.date()}); shortest history: " + ", ".join(
        t for t in keep if r[t].first_valid_index() == first_common)
    R = R.dropna(how="any")
    info["dropped_missing_rows"] = int(before - len(R))
    info["common_observed_days"] = int(len(R))
    info["window_years"] = round(len(R) / TRADING_DAYS, 2)
    if len(R) < cfg.min_history_days:
        raise ValueError(f"only {len(R)} common observed return days; require {cfg.min_history_days}")
    info["start"], info["end"] = str(R.index[0].date()), str(R.index[-1].date())
    return R, info


def shrunk_cov(R: pd.DataFrame) -> pd.DataFrame:
    lw = LedoitWolf().fit(R.values)
    return pd.DataFrame(lw.covariance_ * TRADING_DAYS, index=R.columns, columns=R.columns)


# ----------------------------------------------------------- Black-Litterman views
def black_litterman(cov: pd.DataFrame, mkt_caps: pd.Series, rf: float, upside: pd.Series,
                    confidence: pd.Series, cfg: PortfolioConfig | None = None,
                    betas: pd.Series | None = None, erp: float = 0.05) -> dict:
    """Prior = CAPM (beta vs S&P 500 x equity risk premium); views = annualised
    fair-value upside with confidence from the number/agreement of valuation methods."""
    cfg = cfg or CONFIG.portfolio
    tick = list(cov.index)
    S = cov.values
    w_mkt = (mkt_caps.reindex(tick).fillna(mkt_caps.median())).values
    w_mkt = w_mkt / w_mkt.sum()
    if betas is not None:
        pi = betas.reindex(tick).fillna(1.2).clip(0.5, 2.5).values * erp     # CAPM excess returns
    else:
        pi = cfg.risk_aversion * S @ w_mkt                                    # reverse-optimisation fallback
    # views: annualised fair-value upside (excess of rf), clipped
    up = upside.reindex(tick).fillna(0.0).values
    q = np.sign(1 + up) * np.abs(1 + up) ** (1 / cfg.view_horizon_years) - 1 - rf
    q = np.clip(q, cfg.view_clip[0], cfg.view_clip[1])
    conf = confidence.reindex(tick).fillna(0.5).clip(0.2, 1.0).values
    tau = cfg.bl_tau
    P = np.eye(len(tick))
    omega = np.diag(np.diag(tau * S) / conf)                 # lower confidence -> larger view variance
    inv_tS = np.linalg.inv(tau * S)
    inv_om = np.linalg.inv(omega)
    M = np.linalg.inv(inv_tS + P.T @ inv_om @ P)
    mu_excess = M @ (inv_tS @ pi + P.T @ inv_om @ q)
    return {"mu": pd.Series(mu_excess + rf, index=tick), "prior": pd.Series(pi + rf, index=tick),
            "views": pd.Series(q + rf, index=tick), "w_mkt": pd.Series(w_mkt, index=tick),
            "confidence": pd.Series(conf, index=tick)}


# ------------------------------------------------------------------- optimisers
def _constraints(n: int, seg_matrix: np.ndarray | None, seg_cap: float):
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    if seg_matrix is not None:
        for row in seg_matrix:
            cons.append({"type": "ineq", "fun": (lambda w, r=row: seg_cap - r @ w)})
    return cons


def check_feasible(n: int, min_w: float, max_w: float | np.ndarray, seg_cap: float | None = None,
                   seg_sizes: list[int] | None = None) -> None:
    """Fail loudly when the weight box cannot hold a fully-invested portfolio.

    SLSQP does not report this usefully: the solve fails, the caller renormalises and returns weights that
    breach the very cap that was requested. With n names capped at max_w the budget needs n*max_w >= 1, and
    each segment cap must be reachable too.
    """
    caps = np.atleast_1d(np.asarray(max_w, dtype=float))
    total_max = float(caps.sum()) if caps.size == n else float(caps[0] * n)
    if total_max < 1.0 - 1e-9:
        raise ValueError(f"infeasible weight constraints: {n} names capped at {float(caps.max()):.0%} can hold at most "
                         f"{total_max:.0%} of the book; raise the maximum weight or add names")
    if min_w * n > 1.0 + 1e-9:
        raise ValueError(f"infeasible weight constraints: {n} names at a {min_w:.0%} minimum need {min_w * n:.0%} of the book")
    if seg_cap is not None and seg_sizes:
        if seg_cap * len(seg_sizes) < 1.0 - 1e-9:
            raise ValueError(f"infeasible segment constraints: {len(seg_sizes)} segments capped at {seg_cap:.0%} "
                             f"can hold at most {seg_cap * len(seg_sizes):.0%} of the book")


def _solve(obj, n, bounds, cons, x0=None):
    x0 = x0 if x0 is not None else np.full(n, 1.0 / n)
    best = None
    for start in (x0, np.random.default_rng(0).dirichlet(np.ones(n))):
        res = minimize(obj, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 1000, "ftol": 1e-12})
        if res.success and (best is None or res.fun < best.fun):
            best = res
    if best is None:
        res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=cons, options={"maxiter": 2000})
        best = res
    w = np.clip(best.x, 0, None)
    return w / w.sum()


#: Everything a reader needs to know before comparing this Sharpe with any other one.
EX_ANTE_SHARPE_DISCLOSURE = {
    "estimator": "ex-ante forecast",
    "formula": "(expected return - risk-free) / ex-ante volatility, both annualised",
    "expected_return_source": "model expected returns for today's target list",
    "volatility_source": "sqrt(w' S w) from the shrunk covariance of the common price window",
    "horizon": "single period, annualised - not a realised track record",
    "portfolio": "today's names at today's target weights, fully invested",
    "rebalancing": "none - this is a snapshot of one book",
    "costs": "not deducted (no trading takes place in a snapshot)",
    "comparability": "NOT comparable with a historical walk-forward Sharpe: different portfolio, window, "
                     "constraints, risk-free rate and estimator. An ex-ante ratio is a forecast about one "
                     "book; a walk-forward ratio is an illustration of a changing book over history. Neither "
                     "validates the other, and neither validates the strategy.",
}


def port_stats(w: np.ndarray, mu: np.ndarray, S: np.ndarray, rf: float, *, sample: dict | None = None,
               objective: str | None = None, constraints: dict | None = None) -> dict:
    """Ex-ante expected return, volatility and Sharpe for one weight vector.

    The Sharpe carries its own disclosure. Two numbers on two pages both called "Sharpe" - one an ex-ante
    forecast on today's fifteen names, one a 13-year walk-forward ratio on a monthly-changing book - are not
    the same statistic, and a reader comparing them without being told will draw a false conclusion.
    """
    ret = float(w @ mu)
    vol = float(np.sqrt(w @ S @ w))
    return {"exp_return": ret, "vol": vol, "sharpe": (ret - rf) / vol if vol else np.nan,
            "sharpe_disclosure": {**EX_ANTE_SHARPE_DISCLOSURE, "risk_free": float(rf),
                                  "objective": objective,
                                  "sample": sample or {},
                                  "constraints": constraints or {}}}


def optimise(mu: pd.Series, cov: pd.DataFrame, rf: float, segments: pd.Series,
             cfg: PortfolioConfig | None = None, max_w: pd.Series | None = None) -> dict:
    cfg = cfg or CONFIG.portfolio
    tick = list(cov.index)
    n = len(tick)
    m, S = mu.reindex(tick).values, cov.values
    caps = (max_w.reindex(tick).fillna(cfg.max_weight) if max_w is not None else pd.Series(cfg.max_weight, index=tick)).clip(cfg.min_weight, cfg.max_weight)
    bounds = [(cfg.min_weight, float(caps[t])) for t in tick]
    segs = segments.reindex(tick)
    seg_names = sorted(segs.dropna().unique())
    check_feasible(n, cfg.min_weight, caps.values, cfg.max_segment_weight if seg_names else None,
                   [int((segs == s).sum()) for s in seg_names] if seg_names else None)
    seg_matrix = np.array([[1.0 if segs[t] == s else 0.0 for t in tick] for s in seg_names]) if seg_names else None
    cons = _constraints(n, seg_matrix, cfg.max_segment_weight)

    def neg_sharpe(w):
        r, v = w @ m, np.sqrt(w @ S @ w)
        return -(r - rf) / v

    def variance(w):
        return w @ S @ w

    def rp_obj(w):
        v = np.sqrt(w @ S @ w)
        rc = w * (S @ w) / v
        return np.sum((rc - v / n) ** 2) * 1e4

    out = {}
    w_ms = _solve(neg_sharpe, n, bounds, cons)
    w_mv = _solve(variance, n, bounds, cons)
    w_rp = _solve(rp_obj, n, bounds, cons)
    w_eq = np.full(n, 1.0 / n)
    for name, w in (("max_sharpe", w_ms), ("min_vol", w_mv), ("risk_parity", w_rp), ("equal_weight", w_eq)):
        st = port_stats(w, m, S, rf)
        st["weights"] = pd.Series(w, index=tick)
        st["risk_contrib"] = risk_contributions(w, S, tick)
        out[name] = st
    out["frontier"] = efficient_frontier(m, S, rf, bounds, n)
    out["meta"] = {"n_assets": n, "rf": rf, "min_weight": cfg.min_weight, "max_weight": cfg.max_weight,
                   "max_segment_weight": cfg.max_segment_weight}
    return out


def risk_contributions(w: np.ndarray, S: np.ndarray, tick: list[str]) -> pd.Series:
    v = np.sqrt(w @ S @ w)
    rc = w * (S @ w) / v
    return pd.Series(rc / rc.sum(), index=tick)


def _combine_construction_backtests(backtests: dict[str, dict], benchmarks: tuple[str, ...]) -> dict:
    """One aligned payload for the construction selector.

    Every method contributes only its Portfolio series. Benchmarks come from the canonical replay once,
    because every construction uses the same dates and price panel.
    """
    methods = [m for m in ("recommended", *CONSTRUCTION_METHODS) if m in backtests]
    base = backtests["recommended"]
    cumulative = pd.DataFrame({m: backtests[m]["cumulative"]["Portfolio"] for m in methods})
    drawdown = pd.DataFrame({m: backtests[m]["drawdown"]["Portfolio"] for m in methods})
    stats = pd.DataFrame({m: backtests[m]["stats"].loc["Portfolio"] for m in methods}).T
    for bench in benchmarks:
        if bench in base["cumulative"]:
            cumulative[bench] = base["cumulative"][bench]
            drawdown[bench] = base["drawdown"][bench]
            stats.loc[bench] = base["stats"].loc[bench]
    return {"cumulative": cumulative, "drawdown": drawdown, "stats": stats,
            "window": base["window"], "meta": base["meta"], "methods": methods}


def efficient_frontier(m, S, rf, bounds, n, points: int = 25) -> pd.DataFrame:
    lo_bounds = [(0.0, 1.0)] * n           # unconstrained long-only frontier for the picture
    r_min = float(np.min(m)); r_max = float(np.max(m))
    rows = []
    for tr in np.linspace(r_min, r_max, points):
        cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
                {"type": "eq", "fun": (lambda w, tr=tr: w @ m - tr)}]
        res = minimize(lambda w: w @ S @ w, np.full(n, 1.0 / n), method="SLSQP", bounds=lo_bounds, constraints=cons,
                       options={"maxiter": 500})
        if res.success:
            v = float(np.sqrt(res.fun))
            rows.append({"target_return": tr, "vol": v, "sharpe": (tr - rf) / v if v else np.nan})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------ backtest
BACKTEST_LABEL = "HINDSIGHT ILLUSTRATION - INVALID AS TRACK RECORD"


def backtest(R: pd.DataFrame, weights: pd.Series, px: pd.DataFrame, benchmarks: tuple[str, ...],
             rf: float, rebalance: str | None = None, cash_rate: pd.Series | None = None) -> dict:
    """Replay of *today's* target weights over the return window. This is a hindsight illustration and is
    INVALID as a track record or as evidence of strategy efficacy: the names were selected and the weights
    (covariance, ERC, vol scale) estimated with information available today, then applied to the whole window.

    Simulation rules (stated literally, also returned in ``meta``):
    * equity sleeve: constant-mix, reset to the target weights on the first trading day of each ``rebalance``
      period; weights drift within the period
    * cash sleeve: the fixed fraction 1 - sum(weights); it is rebalanced every day and accrues ``cash_rate``
      (a daily rate path, e.g. FEDFUNDS / 252) or, failing that, the constant ``rf``
    * no commissions, spreads, slippage, market impact, fills, taxes or corporate-action ledger
    * dates: only days on which every name has an observed price (``R``); benchmarks use the same dates,
      day one included, so both sides drop the same gap days
    * Sharpe = mean daily excess return over the cash rate / std, annualised by sqrt(252); the previous
      (CAGR - rf) / vol ratio is reported separately as ``cagr_excess_over_vol``
    """
    rebalance = rebalance or CONFIG.portfolio.rebalance
    w = weights.reindex(R.columns).fillna(0.0).values
    idx = R.index
    periods = idx.to_period(rebalance)
    reb = pd.Series(periods, index=idx).ne(pd.Series(periods, index=idx).shift(1))
    invested = float(w.sum())                 # < 1 when the vol target leaves cash
    if cash_rate is not None and len(pd.Series(cash_rate).dropna()):
        cr = (pd.Series(cash_rate).astype(float).sort_index().reindex(idx, method="ffill").bfill() / TRADING_DAYS).fillna(rf / TRADING_DAYS)
        cash_basis = f"daily path from {getattr(cash_rate, 'name', 'rate series') or 'rate series'} ({float(cr.iloc[0]) * TRADING_DAYS:.2%} at start, {float(cr.iloc[-1]) * TRADING_DAYS:.2%} at end)"
    else:
        cr = pd.Series(rf / TRADING_DAYS, index=idx)
        cash_basis = f"constant {rf:.2%} (today's DGS10 applied to the whole window)"
    holdings = w.copy()
    port_r = []
    for d in idx:
        if reb[d]:
            holdings = w.copy()
        r = R.loc[d].values
        pr = float(holdings @ r) + (1 - invested) * float(cr[d])
        port_r.append(pr)
        holdings = holdings * (1 + r)
        holdings = holdings / holdings.sum() * invested if holdings.sum() else holdings
    port = pd.Series(port_r, index=idx, name="Portfolio")
    bcols = [c for c in benchmarks if c in px.columns]
    b = px[bcols].pct_change(fill_method=None).reindex(idx).fillna(0.0)      # same dates as the portfolio, day one included
    allr = pd.concat([port, b], axis=1)
    cum = (1 + allr).cumprod()

    def stats(s: pd.Series):
        yrs = len(s) / TRADING_DAYS
        cagr = float(s.add(1).prod() ** (1 / yrs) - 1)
        vol = float(s.std() * np.sqrt(TRADING_DAYS))
        ex = s - cr.reindex(s.index)
        sharpe = float(ex.mean() / ex.std() * np.sqrt(TRADING_DAYS)) if ex.std() else np.nan
        c = (1 + s).cumprod()
        dd = float((c / c.cummax() - 1).min())
        return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "cagr_excess_over_vol": (cagr - rf) / vol if vol else np.nan, "max_drawdown": dd,
                "total_return": float(c.iloc[-1] - 1)}
    table = pd.DataFrame({c: stats(allr[c]) for c in allr.columns}).T
    if "SPY" in allr:
        cov = np.cov(allr["Portfolio"], allr["SPY"])
        table.loc["Portfolio", "beta_vs_spy"] = cov[0, 1] / cov[1, 1]
    if "SOXX" in allr:
        te = float((allr["Portfolio"] - allr["SOXX"]).std() * np.sqrt(TRADING_DAYS))
        table.loc["Portfolio", "tracking_error_vs_soxx"] = te
    meta = {"label": BACKTEST_LABEL, "status": "hindsight_illustration",
            "selection": "today's target list replayed over the whole window (selection look-ahead)",
            "weights": "today's target weights, covariance and vol scale estimated on the full window (weight look-ahead)",
            "rebalance": f"equity sleeve reset to target weights at the start of every '{rebalance}' period; cash fraction {1 - invested:.1%} rebalanced daily",
            "cash_rate": cash_basis, "costs": "none: no commissions, spreads, slippage, impact, fills, taxes or corporate-action ledger",
            "universe": "current static universe; no delistings, IPO timing or renames",
            "dates": {"start": str(idx[0].date()), "end": str(idx[-1].date()), "n_days": int(len(idx)), "years": round(len(idx) / TRADING_DAYS, 2)},
            "benchmark_basis": "same dates as the portfolio, day one included; gap days dropped on both sides",
            "sharpe_definition": "mean daily excess return over the cash rate / std, annualised by sqrt(252)",
            "cagr_excess_over_vol_definition": "(CAGR - rf) / annualised vol; the pre-remediation 'Sharpe' figure, not a Sharpe ratio"}
    return {"daily": allr, "cumulative": cum, "stats": table, "drawdown": cum / cum.cummax() - 1, "window": (str(idx[0].date()), str(idx[-1].date())),
            "meta": meta, "cash_rate_daily": cr}


def realised_betas(R: pd.DataFrame, px: pd.DataFrame, bench: str = "SPY") -> pd.Series:
    b = px[bench].pct_change().reindex(R.index)
    out = {}
    for t in R.columns:
        m = pd.concat([R[t], b], axis=1).dropna()
        c = np.cov(m.iloc[:, 0], m.iloc[:, 1])
        out[t] = c[0, 1] / c[1, 1] if c[1, 1] else np.nan
    return pd.Series(out)


def grinold_alphas(top: pd.DataFrame, cov: pd.DataFrame, rf: float, betas: pd.Series, erp: float, ic: float) -> pd.Series:
    """Expected return = CAPM + Grinold alpha from the historically tested alpha score.

    Scarcity/risk overlays affect selection, but are not promoted to expected return before their
    live forward IC is validated.
    """
    tick = list(cov.index)
    signal = "alpha_score" if "alpha_score" in top else "composite"
    z = top[signal].reindex(tick).astype(float)
    z = (z - z.mean()) / (z.std() if z.std() else 1.0)
    sig = pd.Series(np.sqrt(np.diag(cov.values)), index=tick)
    capm = rf + betas.reindex(tick).fillna(1.2).clip(0.5, 2.5) * erp
    return capm + ic * sig * z


def run_portfolio(top: pd.DataFrame, px: pd.DataFrame, rf: float, cfg: PortfolioConfig | None = None,
                  erp: float = 0.05, cash_rate: pd.Series | None = None) -> dict:
    cfg = cfg or CONFIG.portfolio
    if not np.isfinite(cfg.vol_target) or cfg.vol_target <= 0:
        raise ValueError("volatility target must be finite and positive")
    tickers = list(top.index)
    segs = top["segment"]
    R, rinfo = build_returns(px, tickers, segs, cfg)
    cov = shrunk_cov(R)
    corr = R.corr()
    top_cov = top.reindex(cov.index)
    conf = (top_cov["n_methods"].fillna(1) / 3).clip(0.2, 1.0) * (1 - top_cov["upside_dispersion"].fillna(0.3).clip(0, 0.8) / 2)
    betas = realised_betas(R, px)
    legacy_bl = black_litterman(cov, top_cov["market_cap"], rf, top_cov["upside"], conf, cfg, betas=betas, erp=erp)
    mu = grinold_alphas(top_cov, cov, rf, betas, erp, cfg.grinold_ic)
    HIGH_RISK_WEIGHT_CAP = 0.05  # numeric policy retained from the private strategic layer
    max_w = pd.Series(cfg.max_weight, index=top_cov.index)
    if "risk_capped" in top_cov.columns:
        max_w[top_cov["risk_capped"].fillna(False).astype(bool)] = HIGH_RISK_WEIGHT_CAP

    # Black-Litterman is a return model plus a covariance update. It enters the selector through
    # mean-variance utility; the remaining return-aware methods keep the historically tested Grinold mu.
    pi, w_mkt, delta = bl_model.equilibrium_prior(cov, top_cov["market_cap"], None, betas, erp)
    views = bl_model.build_views(top_cov, rf, cfg, sources=("fair_value",))
    bl = bl_model.posterior(cov, pi, views, cfg.bl_tau, rf)
    bl.update({"betas": betas, "w_mkt": w_mkt, "delta": delta, "sources": ["fair_value"],
               "n_views": len(views), "confidence": legacy_bl["confidence"], "views_by_ticker": legacy_bl["views"]})

    common = dict(returns=R, caps=top_cov["market_cap"], max_weights=max_w,
                  segments=top_cov["segment"], rf=rf, cfg=cfg)
    grinold_ctx = construction_lib.Ctx(cov=cov, mu=mu, delta=cfg.risk_aversion, **common)
    bl_ctx = construction_lib.Ctx(cov=bl["cov_post"], mu=bl["mu"], delta=delta, **common)
    weights = {}
    for name in CONSTRUCTION_METHODS:
        weights[name] = construction_lib.build(name, bl_ctx if name == "mean_variance" else grinold_ctx)

    opt = {}
    for name, w in weights.items():
        mu_used, cov_used = (bl["mu"], bl["cov_post"]) if name == "mean_variance" else (mu, cov)
        st = port_stats(w.values, mu_used.reindex(w.index).values, cov_used.reindex(index=w.index, columns=w.index).values, rf)
        st.update({"weights": w, "risk_contrib": risk_contributions(w.values, cov_used.reindex(index=w.index, columns=w.index).values, list(w.index)),
                   "label": construction_lib.REGISTRY[name]["label"], "family": construction_lib.REGISTRY[name]["family"],
                   "return_model": "black_litterman" if name == "mean_variance" else ("grinold" if construction_lib.REGISTRY[name]["needs_mu"] else "risk_only")})
        opt[name] = st
    opt["min_vol"] = opt["min_variance"]                              # backwards-compatible API alias
    opt["frontier"] = efficient_frontier(mu.values, cov.values, rf,
                                           [(cfg.min_weight, float(max_w[t])) for t in cov.index], len(cov))
    opt["meta"] = {"n_assets": len(cov), "rf": rf, "min_weight": cfg.min_weight, "max_weight": cfg.max_weight,
                   "max_segment_weight": cfg.max_segment_weight,
                   "risk_capped": [t for t in top_cov.index if "risk_capped" in top_cov.columns and bool(top_cov.loc[t, "risk_capped"])],
                   "methods": list(CONSTRUCTION_METHODS), "bl_method": "mean_variance"}
    # primary = ERC with an ex-ante volatility target; cash holds the remainder
    primary = CONSTRUCTION_ALIASES.get(cfg.primary, cfg.primary)
    if primary not in opt:
        raise ValueError(f"unknown primary construction {cfg.primary!r}; choose one of {', '.join(CONSTRUCTION_METHODS)}")
    prim = opt[primary]
    scale = min(1.0, cfg.vol_target / prim["vol"]) if prim["vol"] > 0 else 1.0
    w_vt = prim["weights"] * scale
    opt["recommended"] = {"weights": w_vt, "cash": 1.0 - scale, "exp_return": prim["exp_return"] * scale + rf * (1 - scale),
                          "vol": prim["vol"] * scale, "sharpe": prim["sharpe"], "risk_contrib": prim["risk_contrib"], "base": primary,
                          "scale": scale, "label": f"Executable target ({construction_lib.REGISTRY[primary]['label']} x vol target)",
                          "family": prim["family"], "return_model": prim["return_model"]}
    bt = backtest(R, opt["recommended"]["weights"], px, cfg.benchmarks, rf, cfg.rebalance, cash_rate=cash_rate)
    bt["meta"]["dropped_dates"] = rinfo.get("dropped_dates", []); bt["meta"]["missing_by_ticker"] = rinfo.get("missing_by_ticker", {})
    bt["meta"]["window_start_reason"] = rinfo.get("window_start_reason")
    backtests = {"recommended": bt}
    for name in CONSTRUCTION_METHODS:
        backtests[name] = backtest(R, opt[name]["weights"], px, cfg.benchmarks, rf, cfg.rebalance, cash_rate=cash_rate)
        backtests[name]["meta"]["construction"] = name
    construction_backtest = _combine_construction_backtests(backtests, cfg.benchmarks)
    bt_eq = backtests["equal_weight"]
    asset_stats = pd.DataFrame({"exp_return_bl": bl["mu"], "exp_return_grinold": mu, "prior_return": bl["prior"],
                                "view_return": legacy_bl["views"], "beta_spy": betas,
                                "vol": pd.Series(np.sqrt(np.diag(cov.values)), index=cov.index),
                                "confidence": legacy_bl["confidence"]})
    return {"returns": R, "returns_info": rinfo, "cov": cov, "corr": corr, "bl": bl, "opt": opt,
            "backtest": bt, "backtest_equal": bt_eq, "backtests": backtests,
            "construction_backtest": construction_backtest, "asset_stats": asset_stats}
