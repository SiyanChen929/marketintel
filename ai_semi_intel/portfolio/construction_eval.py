"""Point-in-time walk-forward across every construction method, with equity curves and a full performance matrix.

One universe, one covariance, one expected-return model, one rebalance schedule, one cost model - only the
construction differs. That is the only way the equity curves are comparable.

Honesty constraints carried over from bl_eval:
* Views must be point-in-time, so expected returns come from 12-1 price momentum (the fair-value and event views
  exist only as today's snapshot and cannot be replayed historically).
* The selection universe is today's constituents, so **every method is survivorship-biased alike**. Compare
  methods against each other; the level, and any margin over SOXX, is not evidence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.settings import CONFIG, PortfolioConfig
from . import bl, constructions as C
from . import optimizer

TRADING_DAYS = 252
SCHEMA_VERSION = "construction_eval.v2"   # v1 = fixed-weight return proxy; v2 = continuous shares+cash ledger
PRODUCTION_ARM = "production_replica"
PRODUCTION_LABEL = "Production replica (raw cov, vol-target cash)"


# ----------------------------------------------------------------------------- provenance / staleness
def code_hash() -> str:
    """SHA-1 over every module whose source can change a curve produced here."""
    import hashlib
    import pathlib
    here = pathlib.Path(__file__).parent
    parts = []
    for name in ("construction_eval.py", "constructions.py", "bl.py", "optimizer.py"):
        p = here / name
        parts.append(f"{name}:{hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p.exists() else 'MISSING'}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def input_fingerprint(long_px: pd.DataFrame, universe: pd.DataFrame) -> dict:
    """Identify the exact inputs a run used, over the **whole** history it consumed.

    Hashing only ``tail(250)`` left every earlier bar outside the fingerprint, so a correction to 2015 data
    produced a different curve under an identical fingerprint. Results are deterministic given fixed inputs,
    but the daily pipeline rewrites universe_scores.csv, so two runs of identical code on different days differ;
    stamping the inputs makes that visible instead of looking like noise.
    """
    import hashlib
    def h(df: pd.DataFrame) -> str:
        return hashlib.sha1(pd.util.hash_pandas_object(df.fillna(0.0), index=True).values.tobytes()).hexdigest()[:12]
    caps = universe["market_cap"] if "market_cap" in universe.columns else pd.Series(dtype=float)
    return {"price_rows": int(len(long_px)), "price_cols": int(long_px.shape[1]),
            "price_first_date": str(long_px.index[0])[:10], "price_last_date": str(long_px.index[-1])[:10],
            "price_hash_full_history": h(long_px), "price_hash_tail250": h(long_px.tail(250)),
            "universe_names": int(len(universe)),
            "universe_hash": h(universe[[c for c in ("market_cap", "segment") if c in universe.columns]]),
            "total_market_cap": None if caps.empty else float(caps.sum()),
            "code_hash": code_hash()}


def verify_artifact(payload: dict, long_px: pd.DataFrame, universe: pd.DataFrame) -> dict:
    """Is a stored walk-forward artifact still the truth for the current code and data?

    An artifact that predates the code that would produce it is stale, and serving it as current is how a
    fixed defect keeps being reported as fixed while the published curve still contains it.
    """
    want = input_fingerprint(long_px, universe)
    got = (payload or {}).get("inputs") or {}
    checks = {"code_hash": got.get("code_hash") == want["code_hash"],
              "price_hash": got.get("price_hash_full_history") == want["price_hash_full_history"],
              "universe_hash": got.get("universe_hash") == want["universe_hash"],
              "schema": (payload or {}).get("schema_version") == SCHEMA_VERSION}
    stale = [k for k, v in checks.items() if not v]
    return {"fresh": not stale, "stale_reasons": stale, "checks": checks,
            "expected": {k: want.get(k) for k in ("code_hash", "price_hash_full_history", "universe_hash")},
            "found": {k: got.get(k) for k in ("code_hash", "price_hash_full_history", "universe_hash")},
            "artifact_schema": (payload or {}).get("schema_version"), "current_schema": SCHEMA_VERSION,
            "verdict": ("current" if not stale else
                        "STALE - this artifact was produced by different code, data or schema and must not be "
                        "served as the current result")}


# ----------------------------------------------------------------------------- the ledger
def _dlabel(d) -> str:
    """A date label that works for real timestamps and for plain test labels alike."""
    try:
        return str(pd.Timestamp(d).date())
    except Exception:
        return str(d)


class Ledger:
    """One continuous shares + cash book, rebalanced only on rebalance dates.

    The defect this replaces applied fixed target weights to daily returns while charging turnover against a
    separately-drifted book: two different portfolios, one of them never actually held. Here there is a single
    state - shares per name plus cash - carried across every session. Weights drift with prices because shares
    do not move; NAV is a level, so the equity curve needs no compounding of a gappy return series; and the
    close(t) -> close(t+1) move belongs to whatever the book held at close(t), which is the *old* book.
    """

    def __init__(self, cost_bps: float, rf: float = 0.0):
        self.shares: dict[str, float] = {}
        self.cash = 1.0
        self.cost_bps = cost_bps
        self.rf = rf
        self.total_cost = 0.0
        self.accrued_days = 0.0
        self.cash_interest = 0.0
        self.trades: list[dict] = []

    def value(self, prices: pd.Series) -> float | None:
        """NAV from the *current* shares. None when a held name has no bar - never a carried-forward price."""
        tot = self.cash
        for t, n in self.shares.items():
            if n == 0:
                continue
            p = prices.get(t)
            if p is None or not np.isfinite(p) or p <= 0:
                return None
            tot += n * float(p)
        return float(tot)

    def accrue_cash(self, days: float) -> None:
        """Accrue the cash rate over ``days`` **calendar** days (ACT/365.25).

        Cash earns over weekends and holidays, so the interval is calendar time, not sessions. This must be
        called exactly once per session before the book is valued or traded: a book that reports a cash
        remainder earning the risk-free rate and never accrues it is misstating its own return.
        """
        if days <= 0 or not self.rf or not self.cash:
            return
        self.cash *= (1.0 + self.rf) ** (float(days) / 365.25)
        self.accrued_days += float(days)

    def rebalance(self, date, prices: pd.Series, target: pd.Series) -> dict:
        """Move to ``target`` weights at ``prices``. Cost is charged once per unit of traded notional."""
        nav = self.value(prices)
        if nav is None or nav <= 0:
            return {"date": _dlabel(date), "executed": False,
                    "reason": "a held name has no bar at this date; the book cannot be valued or traded"}
        want_shares = {t: (float(w) * nav) / float(prices[t]) for t, w in target.items()
                       if w > 0 and np.isfinite(prices.get(t, np.nan)) and prices.get(t, 0) > 0}
        names = set(self.shares) | set(want_shares)
        notional = sum(abs(want_shares.get(t, 0.0) - self.shares.get(t, 0.0)) * float(prices[t])
                       for t in names if np.isfinite(prices.get(t, np.nan)))
        cost = notional * self.cost_bps / 1e4
        self.total_cost += cost
        nav_after = nav - cost
        # re-solve shares on the post-cost NAV so the book is exactly funded
        want_shares = {t: (float(w) * nav_after) / float(prices[t]) for t, w in target.items()
                       if w > 0 and np.isfinite(prices.get(t, np.nan)) and prices.get(t, 0) > 0}
        invested = sum(n * float(prices[t]) for t, n in want_shares.items())
        self.shares = want_shares
        self.cash = nav_after - invested
        rec = {"date": _dlabel(date), "executed": True, "nav_before": nav,
               "traded_notional": notional, "traded_fraction_of_nav": notional / nav if nav else None,
               "cost": cost, "cost_bps_charged": self.cost_bps, "n_names": len(want_shares),
               "cash_after": self.cash, "nav_after": nav_after}
        self.trades.append(rec)
        return rec

    def liquidate(self, date, prices: pd.Series) -> dict:
        """Close the book. The cost of getting out is part of a terminal-liquidated return, not a footnote."""
        nav = self.value(prices)
        if nav is None:
            return {"executed": False, "reason": "a held name has no bar at the final date"}
        notional = sum(abs(n) * float(prices[t]) for t, n in self.shares.items() if n)
        cost = notional * self.cost_bps / 1e4
        self.total_cost += cost
        self.shares = {}
        self.cash = nav - cost
        return {"executed": True, "date": _dlabel(date), "traded_notional": notional,
                "cost": cost, "nav_before": nav, "nav_after": self.cash}

    def weights(self, prices: pd.Series) -> pd.Series:
        nav = self.value(prices)
        if not nav:
            return pd.Series(dtype=float)
        return pd.Series({t: n * float(prices[t]) / nav for t, n in self.shares.items() if n}, dtype=float)


def _hac_t(x: pd.Series, lags: int = 3) -> float:
    x = x.dropna().astype(float)
    n = len(x)
    if n < 8:
        return float("nan")
    d = x - x.mean()
    var = float((d ** 2).mean())
    for L in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - L / (lags + 1)) * float((d.iloc[L:].values * d.iloc[:-L].values).mean())
    return float(x.mean() / np.sqrt(var / n)) if var > 0 else float("nan")


#: The counterpart of optimizer.EX_ANTE_SHARPE_DISCLOSURE, for the historical walk-forward ratio.
WALK_FORWARD_SHARPE_DISCLOSURE = {
    "estimator": "historical realised illustration",
    "formula": "(CAGR - risk-free) / realised annualised volatility of the daily ledger returns",
    "horizon": "the whole walk-forward window, annualised",
    "portfolio": "a monthly-changing book: names are reselected and reweighted every month",
    "rebalancing": "monthly, at close(t+1) after a close(t) signal",
    "costs": "deducted - the ledger charges traded notional and the terminal liquidation",
    "comparability": "NOT comparable with the ex-ante Sharpe on /portfolio: different portfolio, window, "
                     "constraints, risk-free rate and estimator. This is an illustration of a mechanism over "
                     "history, not a forecast for the current book, and it does not validate the strategy.",
}


def performance(daily: pd.Series, monthly: pd.Series, rf: float, bench_m: pd.Series | None = None,
                turnover: float = np.nan, eff_n: float = np.nan, max_w: float = np.nan, cost: float = np.nan,
                nav: pd.Series | None = None) -> dict:
    """The performance matrix for one method: growth, risk, tail, and activity.

    Level statistics come from the NAV series when one is supplied, not from compounding ``daily``. A session
    on which a holding has no bar yields no return observation, so the return series has holes by design;
    compounding it would quietly omit those moves and produce a total return that does not match the book's
    own terminal NAV. The NAV path has no holes - it is a level - so growth and drawdown are read from it,
    while volatility and the ratios that need a distribution use the observed returns, with the number of
    unobservable sessions disclosed.
    """
    r = daily.dropna()
    if len(r) < 30:
        return {}
    if nav is not None and len(nav.dropna()) > 1:
        lv = nav.dropna()
        span_days = max((lv.index[-1] - lv.index[0]).days, 1)
        yrs = span_days / 365.25
        growth = float(lv.iloc[-1] / lv.iloc[0])
        c = lv / lv.iloc[0]
        level_basis = "NAV levels (complete; unaffected by sessions with no observable return)"
    else:
        yrs = len(r) / TRADING_DAYS
        c = (1 + r).cumprod()
        growth = float(c.iloc[-1])
        level_basis = "compounded daily returns"
    cagr = float(growth ** (1 / yrs) - 1) if yrs > 0 else np.nan
    vol = float(r.std() * np.sqrt(TRADING_DAYS))
    dd_series = c / c.cummax() - 1
    mdd = float(dd_series.min())
    down = float(r[r < 0].std() * np.sqrt(TRADING_DAYS)) if (r < 0).any() else np.nan
    m = monthly.dropna()
    out = {"cagr": cagr, "vol": vol, "sharpe": (cagr - rf) / vol if vol else np.nan,
           "sortino": (cagr - rf) / down if down and not np.isnan(down) else np.nan,
           "max_drawdown": mdd, "calmar": cagr / abs(mdd) if mdd else np.nan,
           "total_return": float(growth - 1.0),
           "level_basis": level_basis, "years": float(yrs),
           "worst_month": float(m.min()) if len(m) else np.nan, "best_month": float(m.max()) if len(m) else np.nan,
           "positive_months": float((m > 0).mean()) if len(m) else np.nan,
           "monthly_var95": float(m.quantile(0.05)) if len(m) else np.nan,
           "monthly_cvar95": float(m[m <= m.quantile(0.05)].mean()) if len(m) else np.nan,
           "skew": float(m.skew()) if len(m) > 3 else np.nan, "kurtosis": float(m.kurtosis()) if len(m) > 3 else np.nan,
           "avg_turnover": turnover, "avg_effective_n": eff_n, "avg_max_weight": max_w, "cost_drag_pa": cost / yrs if yrs else np.nan,
           "n_days": int(len(r)), "n_months": int(len(m))}
    if bench_m is not None and len(bench_m.dropna()) > 12:
        j = pd.concat([m, bench_m], axis=1, join="inner").dropna()
        if len(j) > 12:
            x, y = j.iloc[:, 1], j.iloc[:, 0]
            beta = float(np.cov(y, x)[0, 1] / np.var(x)) if np.var(x) > 0 else np.nan
            act = y - x
            out.update({"beta_vs_bench": beta, "alpha_pa": float((y.mean() - beta * x.mean()) * 12) if not np.isnan(beta) else np.nan,
                        "tracking_error": float(act.std() * np.sqrt(12)),
                        "information_ratio": float(act.mean() * 12 / (act.std() * np.sqrt(12))) if act.std() > 0 else np.nan,
                        "up_capture": float(y[x > 0].mean() / x[x > 0].mean()) if (x > 0).any() and x[x > 0].mean() else np.nan,
                        "down_capture": float(y[x < 0].mean() / x[x < 0].mean()) if (x < 0).any() and x[x < 0].mean() else np.nan})
    return out


def _feasible(ctx, sel: list) -> str | None:
    """Reject an impossible constraint set up front.

    An infeasible segment or weight cap is not a solver problem, it is an arithmetic one, and handing it to
    SLSQP anyway costs minutes per rebalance while it retries its way to the same answer. Say so immediately.
    """
    cfg = ctx.cfg
    if len(sel) * float(cfg.max_weight) < 1.0 - 1e-9:
        return (f"infeasible weight cap: {len(sel)} names at {cfg.max_weight:.0%} can hold at most "
                f"{len(sel) * cfg.max_weight:.0%} of the book")
    segs = pd.Series(ctx.segments).reindex(sel).fillna("unknown")
    cap = float(getattr(cfg, "max_segment_weight", 1.0) or 1.0)
    if cap < 1.0 and segs.nunique() * cap < 1.0 - 1e-9:
        return (f"infeasible segment cap: {segs.nunique()} segment(s) at {cap:.0%} can hold at most "
                f"{segs.nunique() * cap:.0%} of the book")
    return None


def _plan_weights(mth: str, ctx, sel: list, resample_draws: int):
    """Target weights for one method, or the error that stopped it. Never a substitute book."""
    bad = _feasible(ctx, sel)
    if bad:
        return None, bad
    try:
        w = (C.resampled_max_sharpe(ctx, draws=resample_draws) if mth == "resampled_max_sharpe" else C.build(mth, ctx))
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    w = pd.to_numeric(w, errors="coerce").reindex(sel).fillna(0.0)
    if not np.isfinite(w).all() or w.sum() <= 0:
        return None, "solver returned a non-finite or empty weight vector"
    return w / w.sum(), None


def walk_forward(long_px: pd.DataFrame, universe: pd.DataFrame, methods: list[str] | None = None,
                 rf: float = 0.03, start: str = "2013-01-01", cov_years: int = 2, top_n: int = 15,
                 min_names: int = 8, mom_scale: float = 0.06, view_conf: float = 0.5, erp: float = 0.045,
                 max_w: float = 0.15, min_w: float = 0.0, max_seg: float = 0.35, bench: str = "SOXX",
                 cost_bps: float = 10.0, use_bl_views: bool = True, resample_draws: int = 8,
                 require_pit: bool = True, vol_target: float | None = None,
                 production_replica: bool = True) -> dict:
    """Monthly-rebalanced walk-forward of every construction on identical inputs, run as a real book.

    **One continuous ledger.** Each method holds shares and cash, carried across every session. Weights drift
    because shares do not move. NAV is a level, so the equity curve is read off the ledger rather than
    compounded from a return series with holes in it. The previous implementation applied fixed target weights
    to daily returns while charging turnover against a separately-drifted book - two portfolios, one of them
    never held.

    **Execution timeline.**::

        close(t)      signal formed from prices up to and including t
        close(t+1)    order filled at the first close at which the signal could be traded
                      the t -> t+1 move is earned by the OLD book, which is still what the ledger holds

    No session is skipped: the fill date is part of the record, not a gap the benchmark quietly bridges.

    **Eligibility uses fill-time information only.** A name is held if it has a bar at the fill. Whether it
    will still price in three weeks is not knowable then and is not consulted.

    **Costs.** One charge per unit of traded notional: the initial allocation is one side, a rebalance is
    charged on what actually changes hands, and the terminal liquidation is included in the net terminal
    return rather than reported beside it.
    """
    from market_intel import pit_contract              # sibling package, absolute import
    methods = list(methods or C.REGISTRY)
    px = long_px.copy()
    px.index = pd.to_datetime(px.index)
    if getattr(px.index, "tz", None) is not None:
        px.index = px.index.tz_localize(None)
    tickers = [t for t in universe.index if t in px.columns]
    sessions = px.index[px.index >= pd.Timestamp(start)]
    gate = pit_contract.check(window=(str(sessions[0].date()), str(sessions[-1].date())) if len(sessions) else None,
                              tickers=tickers)
    pit_data = {k: pit_contract.load(k) for k in ("pit_universe", "historical_shares", "delisting")} \
        if gate.alpha_claim_available else {}
    if require_pit and not gate.alpha_claim_available:
        enforcement = {"require_pit": True, "gate_open": False, "branch_taken": "MECHANISM_DIAGNOSTIC_ONLY",
                       "withheld": ["cagr", "total_return", "sharpe", "sortino", "calmar", "alpha_pa",
                                    "information_ratio", "beta_vs_bench", "up_capture", "down_capture"],
                       "note": "require_pit=True and the point-in-time contract is not satisfied, so every "
                               "level- and alpha-dependent statistic is moved into stats[m]['withheld'] and is "
                               "absent from the published block. Relative mechanism comparisons remain."}
    else:
        enforcement = {"require_pit": bool(require_pit), "gate_open": bool(gate.alpha_claim_available),
                       "branch_taken": ("PIT_CONSUMED" if gate.alpha_claim_available else "UNGATED"),
                       "withheld": [], "pit_datasets_loaded": sorted(k for k, v in pit_data.items() if v is not None)}
    caps_now = universe["market_cap"].reindex(tickers)
    shares_proxy = (caps_now / px[tickers].iloc[-1]).replace([np.inf, -np.inf], np.nan)
    segments_all = universe["segment"].reindex(tickers) if "segment" in universe.columns else pd.Series("all", index=tickers)
    cfg = PortfolioConfig(min_weight=min_w, max_weight=max_w, max_segment_weight=max_seg,
                          lookback_years=cov_years, min_history_days=CONFIG.portfolio.min_history_days)
    month_ends = px.loc[px.index >= pd.Timestamp(start)].resample("ME").last().index
    arms = list(methods)
    if production_replica and vol_target:
        arms.append(PRODUCTION_ARM)
    plan: dict[pd.Timestamp, dict] = {}
    errors: dict[str, list] = {m: [] for m in arms}
    rows, skipped, data_events = [], 0, []
    for i, d in enumerate(month_ends[:-1]):
        sig_close = px.index[px.index <= d][-1]                       # close(t): signal date
        after = px.index[px.index > sig_close]
        if not len(after):
            skipped += 1
            continue
        fill_close = after[0]                                          # close(t+1): earliest tradable close
        hist = px.loc[px.index <= sig_close, tickers]
        window = hist.loc[hist.index >= sig_close - pd.DateOffset(years=cov_years)]
        r_win = window.pct_change(fill_method=None).iloc[1:]
        eligible = tickers
        if pit_data.get("pit_universe") is not None:
            pu = pit_data["pit_universe"]
            live = pu[(pu["effective_from"] <= sig_close) &
                      (pu["effective_to"].isna() | (pd.to_datetime(pu["effective_to"], errors="coerce") > sig_close))]
            eligible = [t for t in tickers if t in set(live["ticker"])]
        ok = [t for t in eligible if r_win[t].notna().sum() >= int(cov_years * TRADING_DAYS * 0.85)]
        if len(ok) < min_names:
            skipped += 1
            continue
        p_now = hist[ok].iloc[-1]
        try:
            p_1m = hist[ok].loc[hist.index <= sig_close - pd.DateOffset(months=1)].iloc[-1]
            p_12m = hist[ok].loc[hist.index <= sig_close - pd.DateOffset(months=12)].iloc[-1]
        except IndexError:
            skipped += 1
            continue
        mom = (p_1m / p_12m - 1).dropna()
        if len(mom) < min_names:
            skipped += 1
            continue
        cand = list(mom.sort_values(ascending=False).head(top_n).index)
        # eligibility is decided with FILL-TIME information only: does this name price at the fill?
        fill_px = pd.to_numeric(px.loc[fill_close, cand], errors="coerce")
        sel = [t for t in cand if np.isfinite(fill_px.get(t, np.nan)) and fill_px.get(t, 0) > 0]
        unpriced = [t for t in cand if t not in sel]
        if unpriced:
            data_events.append({"date": str(fill_close.date()), "not_priceable_at_fill": unpriced,
                                "treatment": "not bought - a name that does not price at the fill cannot be traded. "
                                             "No information after the fill is consulted."})
        if len(sel) < 2:
            skipped += 1
            continue
        Rw = r_win[sel].dropna(how="any")
        if len(Rw) < int(cov_years * TRADING_DAYS * 0.6):
            skipped += 1
            continue
        cov_raw = optimizer.shrunk_cov(Rw)
        if pit_data.get("historical_shares") is not None:
            hs = pit_data["historical_shares"]
            asof = hs[hs["date"] <= sig_close].sort_values("date").groupby("ticker")["shares"].last()
            cap_sel = (asof.reindex(sel) * p_now.reindex(sel)).replace([np.inf, -np.inf], np.nan)
        else:
            cap_sel = (shares_proxy.reindex(sel) * p_now.reindex(sel)).replace([np.inf, -np.inf], np.nan)
        cap_sel = cap_sel.fillna(cap_sel.median()) if cap_sel.notna().any() else pd.Series(1.0, index=sel)
        b = pd.Series(1.2, index=sel)
        if bench in px.columns:
            br = px[bench].loc[Rw.index].pct_change().fillna(0.0)
            if float(br.var()) > 0:
                b = pd.Series([float(np.cov(Rw[t], br)[0, 1] / br.var()) for t in sel], index=sel).clip(0.3, 3.0)
        pi, w_mkt, delta = bl.equilibrium_prior(cov_raw, cap_sel, None, b, erp)
        if use_bl_views:
            z = (mom.reindex(sel) - mom.reindex(sel).mean()) / (mom.reindex(sel).std() or 1.0)
            views = [bl.View(f"MOM:{t}", {t: 1.0}, float(np.clip(z[t] * mom_scale, -0.25, 0.35)), view_conf, "momentum") for t in sel]
            post = bl.posterior(cov_raw, pi, views, 0.05, rf)
            mu_use, cov_use = post["mu"], post["cov_post"]
        else:
            mu_use, cov_use = pi + rf, cov_raw
        ctx = C.Ctx(cov=cov_use, mu=mu_use, returns=Rw, caps=cap_sel, segments=segments_all.reindex(sel),
                    rf=rf, delta=delta, cfg=cfg)
        entry, row = {}, {"date": str(sig_close.date()), "fill_date": str(fill_close.date()),
                          "n_candidates": len(cand), "n_selected": len(sel), "delta": float(delta)}
        for mth in methods:
            w, err = _plan_weights(mth, ctx, sel, resample_draws)
            if err:
                errors[mth].append({"date": str(sig_close.date()), "error": err})
                continue                                    # fail closed: no substitute book under this name
            entry[mth] = w
            row[f"n_{mth}"] = int((w > 1e-9).sum())
        if PRODUCTION_ARM in arms:
            # production semantics, reproduced exactly: RAW covariance (no BL posterior) and cash retained
            ctx_prod = C.Ctx(cov=cov_raw, mu=None, returns=Rw, caps=cap_sel, segments=segments_all.reindex(sel),
                             rf=rf, delta=delta, cfg=cfg)
            w, err = _plan_weights(CONFIG.portfolio.primary, ctx_prod, sel, resample_draws)
            if err:
                errors[PRODUCTION_ARM].append({"date": str(sig_close.date()), "error": err})
            else:
                ann_vol = float(np.sqrt(w.values @ cov_raw.loc[sel, sel].to_numpy(float) @ w.values))
                scale = float(min(1.0, vol_target / ann_vol)) if ann_vol > 0 else 1.0
                entry[PRODUCTION_ARM] = w * scale           # remainder stays in cash, as production does
                row["production_scale"] = scale
        if entry:
            plan[fill_close] = entry
            rows.append(row)
    if not plan:
        return {"available": False, "reason": "not enough history for a walk-forward", "pit_gate": gate.to_dict(),
                "pit_enforcement": enforcement, "schema_version": SCHEMA_VERSION}
    # ------------------------------------------------------------------ run the ledgers over every session
    first_fill = min(plan)
    grid = sessions[sessions >= first_fill]
    ledgers = {m: Ledger(cost_bps, rf) for m in arms}
    navs = {m: {} for m in arms}
    pending = {m: None for m in arms}
    held = {m: [] for m in arms}
    deferrals, unvalued = [], {m: 0 for m in arms}
    prev_t = None
    for t in grid:
        prices = pd.to_numeric(px.loc[t], errors="coerce")
        tgt = plan.get(t)
        elapsed = (t - prev_t).days if prev_t is not None else 0
        for m in arms:
            L = ledgers[m]
            before = L.cash
            L.accrue_cash(elapsed)                 # exactly once per session, before valuing or trading
            L.cash_interest += L.cash - before
            if tgt is not None and m in tgt:
                pending[m] = tgt[m]
            if pending[m] is not None:
                rec = L.rebalance(t, prices, pending[m])
                if rec["executed"]:
                    held[m].append((t, L.weights(prices)))
                    pending[m] = None
                else:
                    deferrals.append({"method": m, "date": str(t.date()), "reason": rec["reason"]})
            nav = L.value(prices)
            if nav is None:
                unvalued[m] += 1
            navs[m][t] = nav
        prev_t = t
    final_t = grid[-1]
    final_prices = pd.to_numeric(px.loc[final_t], errors="coerce")
    liquidation = {m: ledgers[m].liquidate(final_t, final_prices) for m in arms}
    # ------------------------------------------------------------------ series, stats
    # The published level series is the CLOSED book. It starts at the funded 1.0 on the session BEFORE the
    # first trade, so the initial allocation cost is inside the return, and its last point is the NAV after
    # the terminal liquidation, so the exit cost is too. Reporting a curve that stops before liquidation while
    # total_cost already includes liquidation would state a return gross of a cost it claims to have deducted.
    _pre = sessions[sessions < first_fill]
    fund_t = _pre[-1] if len(_pre) else (first_fill - pd.Timedelta(days=1))
    nav_pre_liq, nav_s = {}, {}
    for m in arms:
        raw = pd.Series(navs[m], dtype=float).sort_index()
        nav_pre_liq[m] = raw.copy()
        closed = raw.copy()
        if liquidation[m].get("executed"):
            closed.iloc[-1] = float(liquidation[m]["nav_after"])
        nav_s[m] = pd.concat([pd.Series({fund_t: 1.0}, dtype=float), closed]).sort_index()
    failed = {m: errors[m] for m in arms if errors[m]}
    live = [m for m in arms if m not in failed and nav_s[m].notna().sum() > 60]
    series, monthly, curves = {}, {}, {}
    for m in live:
        s = nav_s[m]
        r = s.pct_change(fill_method=None).dropna()          # NAV levels: a gap drops an observation, never bridges
        series[m] = r
        curves[m] = s                                        # funded at 1.0, so the curve IS the closed-book NAV
        me = s.dropna().resample("ME").last()
        monthly[m] = me.pct_change().dropna()
    bench_nav = None
    if bench in px.columns:
        bp = pd.to_numeric(px[bench], errors="coerce").reindex(nav_s[arms[0]].index)
        bench_nav = bp / bp.dropna().iloc[0]                 # same index as the books, so curves are comparable
        series[bench] = bench_nav.pct_change(fill_method=None).dropna()
        curves[bench] = bench_nav
        monthly[bench] = bench_nav.dropna().resample("ME").last().pct_change().dropna()
    bench_m = monthly.get(bench)
    stats = {}
    for m in live:
        trades = [t for t in ledgers[m].trades if t.get("executed")]
        turn = float(np.mean([t["traded_fraction_of_nav"] / 2 for t in trades[1:]])) if len(trades) > 1 else np.nan
        eff = float(np.mean([1.0 / (w[w > 0] ** 2).sum() for _, w in held[m] if len(w)])) if held[m] else np.nan
        mx = float(np.mean([float(w.max()) for _, w in held[m] if len(w)])) if held[m] else np.nan
        st = performance(series[m], monthly[m], rf, bench_m, turn, eff, mx, ledgers[m].total_cost,
                         nav=nav_s[m])
        st["sharpe_disclosure"] = {**WALK_FORWARD_SHARPE_DISCLOSURE, "risk_free": float(rf),
                                   "objective": (C.REGISTRY[m]["label"] if m in C.REGISTRY else PRODUCTION_LABEL),
                                   "objective_reads_expected_return": bool(C.REGISTRY[m]["needs_mu"]) if m in C.REGISTRY else False,
                                   "sample": {"start": str(series[m].index[0].date()), "end": str(series[m].index[-1].date()),
                                              "n_days": int(len(series[m])), "n_months": int(len(monthly[m])),
                                              "n_rebalances": len(trades)},
                                   "constraints": {"max_weight": max_w, "min_weight": min_w, "max_segment_weight": max_seg},
                                   "portfolio_layer": ("vol-target scaled with cash" if m == PRODUCTION_ARM
                                                       else "fully invested, no cash")}
        st.update({"label": (C.REGISTRY[m]["label"] if m in C.REGISTRY else PRODUCTION_LABEL),
                   "family": (C.REGISTRY[m]["family"] if m in C.REGISTRY else "production replica"),
                   "needs_mu": (C.REGISTRY[m]["needs_mu"] if m in C.REGISTRY else False),
                   "total_cost": ledgers[m].total_cost, "n_rebalances_executed": len(trades),
                   "terminal_nav_pre_liquidation": float(nav_pre_liq[m].dropna().iloc[-1]),
                   "terminal_nav_net_of_liquidation": liquidation[m].get("nav_after"),
                   "terminal_return_net_of_liquidation": (liquidation[m]["nav_after"] - 1.0)
                                                          if liquidation[m].get("executed") else None,
                   "liquidation_cost": liquidation[m].get("cost"),
                   "cash_interest_earned": ledgers[m].cash_interest,
                   "cash_accrual_days": ledgers[m].accrued_days,
                   "sessions_unvalued": unvalued[m],
                   "accounting_basis": "CLOSED BOOK: funded at 1.0 on the session before the first trade and "
                                       "valued after the terminal liquidation, so total_return, CAGR, Sharpe "
                                       "and every derived statistic are net of entry, rebalance and exit costs"})
        if enforcement["withheld"]:
            st["withheld"] = {k: st.pop(k) for k in enforcement["withheld"] if k in st}
            st["claim_status"] = "MECHANISM DIAGNOSTIC ONLY"
        stats[m] = st
    if bench in monthly:
        stats[bench] = performance(series[bench], monthly[bench], rf, nav=bench_nav)
        stats[bench].update({"label": bench, "family": "benchmark", "needs_mu": False})
        if enforcement["withheld"]:
            stats[bench]["withheld"] = {k: stats[bench].pop(k) for k in enforcement["withheld"] if k in stats[bench]}
    base = "equal_weight" if "equal_weight" in monthly else (live[0] if live else None)
    versus = {}
    for m in monthly:
        if m == base or base is None:
            continue
        dd = (monthly[m] - monthly[base]).dropna()
        versus[m] = {"vs": base, "monthly_mean_bps": float(dd.mean() * 1e4), "t_hac": _hac_t(dd),
                     "win_rate": float((dd > 0).mean()), "n_months": int(len(dd)),
                     "claim": ("relative mechanism difference only" if enforcement["withheld"] else "excess return")}
    cum = pd.DataFrame(curves)
    ddf = cum / cum.cummax() - 1
    roll = cum.pct_change(fill_method=None).rolling(TRADING_DAYS).apply(
        lambda x: (x.mean() * TRADING_DAYS - rf) / (x.std() * np.sqrt(TRADING_DAYS))
        if x.std() > 0 else np.nan)
    held_for_decomp = {m: [(d, w, _period_returns(px, w, d, held[m], grid)) for d, w in held[m]] for m in live}
    decomp = decompose(series, monthly, held_for_decomp, segments_all, rf, series.get(bench), bench)
    return {"available": True, "schema_version": SCHEMA_VERSION,
            "methods": live, "benchmark": bench if bench_nav is not None else None,
            "window": (str(cum.index[0].date()), str(cum.index[-1].date())),
            "n_rebalances": len(rows), "skipped": skipped,
            "pit_gate": gate.to_dict(), "pit_enforcement": enforcement,
            "claim_status": ("MECHANISM DIAGNOSTIC ONLY - not a performance or alpha claim"
                             if not gate.alpha_claim_available else "performance claim admissible"),
            "failed_methods": {m: {"n_failures": len(v), "sample": v[:5],
                                   "treatment": "excluded entirely; equal weight is NEVER published under a "
                                                "failed method's name"} for m, v in failed.items()},
            "n_failed_methods": len(failed),
            "ledger": {"model": "one continuous shares + cash book per method, rebalanced only on fill dates",
                       "cost_model": f"{cost_bps} bps charged once per unit of traded notional; the initial "
                                     f"allocation is one side, a rebalance is charged on what changes hands",
                       "terminal_liquidation": "inside the published level series: the last NAV point is the "
                                               "closed book, so total_return and every derived statistic are net "
                                               "of the exit cost that total_cost reports",
                       "cash_accrual": f"idle cash accrues {rf:.2%} p.a. on ACT/365.25 calendar days, applied once "
                                       f"per session before valuation or trading",
                       "deferred_rebalances": deferrals[:20], "n_deferred": len(deferrals),
                       "sessions_unvalued": unvalued,
                       "unvalued_treatment": "a session on which a held name has no bar yields no NAV and no "
                                             "return; the price is never carried forward"},
            "liquidation": liquidation,
            "views": {"kind": "12-1 price momentum, point-in-time" if use_bl_views else "equilibrium prior only",
                      "note": "these are NOT the live fair-value or event-score views; those exist only as today's "
                              "snapshot and cannot be replayed, so nothing here tests them"},
            "production_semantics": {
                "production_construction": CONFIG.portfolio.primary,
                "production_covariance": "raw shrunk covariance; the production book reads no expected returns",
                "production_cash": "vol-target scaled, remainder in cash",
                "research_arm_differs_by": ["Black-Litterman posterior covariance is fed to every method",
                                            "every research arm is fully invested with no cash"],
                "replica_arm": (PRODUCTION_ARM if PRODUCTION_ARM in live else None),
                "production_rank": None,
                "production_rank_reason": "no production rank is published: the research arms do not reproduce "
                                          "production semantics, and the replica arm is not yet validated "
                                          "against the live book"},
            "execution": {"signal_at": "close(t)", "fill_at": "close(t+1)", "accrual_from": "continuous",
                          "note": "the t -> t+1 move is earned by the book actually held at close(t); no session "
                                  "is omitted from the ledger"},
            "stats": stats, "versus": versus, "baseline": base,
            "data_events": data_events[:50], "n_data_events": len(data_events),
            "cumulative": {"dates": [d.strftime("%Y-%m-%d") for d in cum.index],
                           **{k: [None if pd.isna(v) else round(float(v), 4) for v in cum[k]] for k in cum.columns}},
            "drawdown": {k: [None if pd.isna(v) else round(float(v), 4) for v in ddf[k]] for k in ddf.columns},
            "rolling_sharpe": {"dates": [d.strftime("%Y-%m-%d") for d in roll.index[::5]],
                               **{k: [None if pd.isna(v) else round(float(v), 3) for v in roll[k].iloc[::5]] for k in roll.columns}},
            "monthly": [{"date": d.strftime("%Y-%m"), **{m: (None if d not in monthly[m].index or pd.isna(monthly[m][d]) else round(float(monthly[m][d]), 4)) for m in monthly}}
                        for d in sorted({d for s in monthly.values() for d in s.index})],
            "rebalances": rows, "total_cost": {m: ledgers[m].total_cost for m in live},
            "decomposition": decomp,
            "inputs": input_fingerprint(long_px, universe),
            "generated_at": pd.Timestamp.utcnow().isoformat(timespec="seconds"),
            "params": {"cov_years": cov_years, "top_n": top_n, "max_w": max_w, "min_w": min_w, "max_seg": max_seg,
                       "cost_bps": cost_bps, "rf": rf, "start": start, "use_bl_views": use_bl_views,
                       "mom_scale": mom_scale, "resample_draws": resample_draws, "bench": bench,
                       "require_pit": require_pit, "vol_target": vol_target}}


def _period_returns(px: pd.DataFrame, w: pd.Series, d, recs: list, grid) -> pd.Series:
    """Per-name price return from this rebalance to the next, for contribution decomposition."""
    dates = [x[0] for x in recs]
    try:
        nxt = dates[dates.index(d) + 1]
    except (ValueError, IndexError):
        nxt = grid[-1]
    if nxt <= d:
        return pd.Series(0.0, index=w.index)
    a = pd.to_numeric(px.loc[d, list(w.index)], errors="coerce")
    b = pd.to_numeric(px.loc[nxt, list(w.index)], errors="coerce")
    return (b / a - 1.0).fillna(0.0)


# ----------------------------------------------------------------------------- decomposition
def drawdown_episodes(daily: pd.Series, top: int = 5, min_depth: float = 0.10) -> list[dict]:
    """The worst peak-to-trough episodes, with trough date, depth, and whether/when it recovered."""
    c = (1 + daily.dropna()).cumprod()
    peak = c.cummax()
    dd = c / peak - 1
    out, in_dd, start = [], False, None
    for d, v in dd.items():
        if not in_dd and v < -1e-9:
            in_dd, start = True, d
        elif in_dd and v >= -1e-9:
            seg = dd.loc[start:d]
            out.append({"start": str(start.date()), "trough": str(seg.idxmin().date()), "end": str(d.date()),
                        "depth": float(seg.min()), "length_days": int((d - start).days),
                        "recovery_days": int((d - seg.idxmin()).days), "recovered": True})
            in_dd = False
    if in_dd:
        seg = dd.loc[start:]
        out.append({"start": str(start.date()), "trough": str(seg.idxmin().date()), "end": None,
                    "depth": float(seg.min()), "length_days": int((dd.index[-1] - start).days),
                    "recovery_days": None, "recovered": False})
    return sorted([o for o in out if o["depth"] <= -min_depth], key=lambda x: x["depth"])[:top]


def market_regimes(bench_daily: pd.Series, bench: str) -> pd.Series:
    """Point-in-time regime label per day, from the benchmark's own path only (no lookahead).

    * drawdown state: within 10% of the running high / 10-20% below / more than 20% below
    * volatility state: trailing 60-day realised vol above or below its expanding median
    """
    c = (1 + bench_daily).cumprod()
    dd = c / c.cummax() - 1
    state = pd.Series(np.where(dd > -0.10, f"{bench} near highs", np.where(dd > -0.20, f"{bench} correction", f"{bench} bear")), index=bench_daily.index)
    rv = bench_daily.rolling(60).std() * np.sqrt(TRADING_DAYS)
    med = rv.expanding(120).median()
    vol = pd.Series(np.where(rv.isna() | med.isna(), "n/a", np.where(rv > med, "high volatility", "low volatility")), index=bench_daily.index)
    return pd.DataFrame({"drawdown_state": state, "vol_state": vol}).apply(lambda r: r, axis=0)


def decompose(series: dict[str, pd.Series], monthly: dict[str, pd.Series], held: dict[str, list],
              segments: pd.Series, rf: float, bench_daily: pd.Series | None, bench: str) -> dict:
    """Split every method's record by calendar year, by market regime, by drawdown episode, and by holding.

    A single 13-year number hides everything a researcher needs: whether an edge is one lucky year, whether it
    survives a drawdown, and which names actually produced it.
    """
    methods = list(series)
    # --- calendar years
    years = {}
    for m, s in series.items():
        y = s.groupby(s.index.year).apply(lambda x: float((1 + x).prod() - 1))
        years[m] = {int(k): round(float(v), 4) for k, v in y.items()}
    all_years = sorted({y for v in years.values() for y in v})
    # --- regimes
    regimes = {}
    if bench_daily is not None and len(bench_daily) > 200:
        lab = market_regimes(bench_daily, bench)
        for col in ("drawdown_state", "vol_state"):
            for state in [s for s in lab[col].unique() if s != "n/a"]:
                mask = lab[col] == state
                if int(mask.sum()) < 40:
                    continue
                entry = {"n_days": int(mask.sum()), "share_of_time": round(float(mask.mean()), 3)}
                for m, s in series.items():
                    r = s[mask.reindex(s.index).fillna(False)]
                    if len(r) < 20:
                        continue
                    yrs = len(r) / TRADING_DAYS
                    cagr = float((1 + r).prod() ** (1 / yrs) - 1) if yrs > 0 else np.nan
                    vol = float(r.std() * np.sqrt(TRADING_DAYS))
                    entry[m] = {"cagr": round(cagr, 4), "vol": round(vol, 4),
                                "sharpe": round((cagr - rf) / vol, 3) if vol else None,
                                "total": round(float((1 + r).prod() - 1), 4)}
                regimes[state] = entry
    # --- drawdown episodes
    episodes = {m: drawdown_episodes(s) for m, s in series.items()}
    # --- contribution by holding and by segment (weight x realised return over each holding month)
    contrib = {}
    for m, recs in held.items():
        if not recs:
            continue
        by_name: dict[str, dict] = {}
        for _, w, pr in recs:
            for t in w.index:
                c = float(w[t]) * float(pr.get(t, 0.0))
                e = by_name.setdefault(t, {"contribution": 0.0, "weight_sum": 0.0, "months": 0, "wins": 0})
                e["contribution"] += c
                e["weight_sum"] += float(w[t])
                e["months"] += 1 if w[t] > 1e-6 else 0
                e["wins"] += 1 if c > 0 else 0
        tot = sum(v["contribution"] for v in by_name.values()) or 1.0
        rows_n = []
        for t, v in by_name.items():
            if v["months"] == 0:
                continue
            rows_n.append({"ticker": t, "contribution": round(v["contribution"], 4), "share": round(v["contribution"] / tot, 4),
                           "avg_weight": round(v["weight_sum"] / max(len(recs), 1), 4), "months_held": v["months"],
                           "hit_rate": round(v["wins"] / v["months"], 3), "segment": str(segments.get(t, "unknown"))})
        rows_n.sort(key=lambda r: -r["contribution"])
        seg_agg: dict[str, dict] = {}
        for r in rows_n:
            e = seg_agg.setdefault(r["segment"], {"contribution": 0.0, "avg_weight": 0.0, "n_names": 0})
            e["contribution"] += r["contribution"]
            e["avg_weight"] += r["avg_weight"]
            e["n_names"] += 1
        contrib[m] = {"by_name": rows_n, "by_segment": [{"segment": k, **{kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}} for k, v in sorted(seg_agg.items(), key=lambda kv: -kv[1]["contribution"])],
                      "top5_share": round(sum(r["contribution"] for r in rows_n[:5]) / tot, 4),
                      "n_names_touched": len(rows_n)}
    # --- consistency of the winner: how often is each method top-3 by year?
    rank_by_year = {}
    for y in all_years:
        vals = [(m, years[m].get(y)) for m in methods if years[m].get(y) is not None]
        vals.sort(key=lambda kv: -kv[1])
        rank_by_year[int(y)] = {m: i + 1 for i, (m, _) in enumerate(vals)}
    consistency = {m: {"avg_rank": round(float(np.mean([rank_by_year[y][m] for y in rank_by_year if m in rank_by_year[y]])), 2),
                       "top3_years": int(sum(1 for y in rank_by_year if rank_by_year[y].get(m, 99) <= 3)),
                       "bottom3_years": int(sum(1 for y in rank_by_year if rank_by_year[y].get(m, 0) >= len(methods) - 2)),
                       "n_years": int(sum(1 for y in rank_by_year if m in rank_by_year[y]))} for m in methods}
    return {"calendar_years": {"years": [int(y) for y in all_years], "returns": years},
            "regimes": regimes, "drawdown_episodes": episodes, "contribution": contrib,
            "rank_by_year": rank_by_year, "consistency": consistency}
