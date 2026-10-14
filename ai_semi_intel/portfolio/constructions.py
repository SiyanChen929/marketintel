"""A library of portfolio construction methods behind one interface, so they can be compared on equal terms.

Every method maps the same inputs to weights: ``f(Ctx) -> pd.Series``. That makes the comparison honest - the
universe, covariance, expected returns, constraints and rebalance dates are identical across methods, so any
difference in the equity curve is the construction itself and nothing else.

Methods split into two families, and the distinction decides what Black-Litterman (or any alpha model) can reach:

* **Risk-only** - read the covariance and nothing else: equal weight, inverse vol, risk parity, min variance,
  max diversification, HRP, segment risk parity. Expected returns cannot influence them by construction.
* **Return-aware** - read expected returns: max Sharpe, mean-variance utility, Kelly, resampled max Sharpe,
  min CVaR with a return floor.

Constraints (fully invested, per-name box, per-segment cap) are applied consistently, and every method is run
through :func:`ai_semi_intel.portfolio.optimizer.check_feasible` so an impossible box fails loudly rather than
being silently renormalised.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.optimize import linprog, minimize
from scipy.spatial.distance import squareform

from ..config.settings import CONFIG, PortfolioConfig
from ..utils.io import log

TRADING_DAYS = 252


@dataclass
class Ctx:
    """Everything a construction may look at. Methods take only what they need."""
    cov: pd.DataFrame
    mu: pd.Series | None = None                 # expected returns (total, incl. rf)
    returns: pd.DataFrame | None = None         # daily returns, for HRP / CVaR
    caps: pd.Series | None = None               # market caps, for cap weighting
    max_weights: pd.Series | None = None        # optional per-name caps (strategic risk overlay)
    segments: pd.Series | None = None
    rf: float = 0.045
    delta: float = 1.0                          # risk aversion for utility methods
    cfg: PortfolioConfig = field(default_factory=lambda: CONFIG.portfolio)

    @property
    def tickers(self) -> list[str]:
        return list(self.cov.index)

    @property
    def vols(self) -> pd.Series:
        return pd.Series(np.sqrt(np.diag(self.cov.values)), index=self.tickers)

    def max_weight_series(self) -> pd.Series:
        hi = pd.Series(self.cfg.max_weight, index=self.tickers, dtype=float)
        if self.max_weights is not None:
            hi = self.max_weights.reindex(self.tickers).fillna(self.cfg.max_weight).astype(float)
        return hi.clip(lower=self.cfg.min_weight, upper=self.cfg.max_weight)

    def bounds(self) -> list[tuple[float, float]]:
        hi = self.max_weight_series()
        return [(self.cfg.min_weight, float(hi[t])) for t in self.tickers]

    def constraints(self) -> list[dict]:
        cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
        if self.segments is not None:
            segs = self.segments.reindex(self.tickers)
            for s in sorted(segs.dropna().unique()):
                mask = (segs == s).values.astype(float)
                cons.append({"type": "ineq", "fun": (lambda mk: (lambda w: self.cfg.max_segment_weight - w @ mk))(mask)})
        return cons

    def check(self) -> None:
        from .optimizer import check_feasible
        matrix = self.cov.to_numpy(dtype=float)
        if self.cov.index.has_duplicates or not self.cov.columns.equals(self.cov.index):
            raise ValueError("covariance axis identity must match unique tickers")
        if not np.isfinite(matrix).all() or not np.allclose(matrix, matrix.T, rtol=1e-8, atol=1e-10):
            raise ValueError("covariance must be finite and symmetric")
        tolerance = 1e-10 * max(1.0, float(np.linalg.norm(matrix, ord=2)))
        if np.linalg.eigvalsh(matrix).min() < -tolerance:
            raise ValueError("covariance must be positive semidefinite")
        if self.max_weights is not None:
            caps = self.max_weights.reindex(self.tickers).to_numpy(dtype=float)
            if not np.isfinite(caps).all() or (caps < self.cfg.min_weight).any():
                raise ValueError("infeasible per-name cap below minimum bound or missing cap")
        if self.segments is not None and self.segments.reindex(self.tickers).isna().any():
            raise ValueError("missing segment identity")
        segs = self.segments.reindex(self.tickers) if self.segments is not None else None
        names = sorted(segs.dropna().unique()) if segs is not None else []
        check_feasible(len(self.tickers), self.cfg.min_weight, self.max_weight_series().values,
                       self.cfg.max_segment_weight if names else None,
                       [int((segs == s).sum()) for s in names] if names else None)


def _norm(w: np.ndarray, ctx: Ctx) -> pd.Series:
    """Euclidean projection of raw weights onto all portfolio constraints.

    Clip-then-divide does not work: dividing by the new sum pushes untouched weights back through the cap,
    and a final renormalisation undoes any clipping that came before it.  A linear programme first finds a
    feasible point; SLSQP then finds the closest feasible weights to the raw construction.
    """
    ctx.check()                                            # raises if the box cannot hold a full book
    w = np.nan_to_num(np.asarray(w, dtype=float), nan=0.0)
    target = np.clip(w, 0.0, None)
    target = target / target.sum() if target.sum() else np.full(len(target), 1.0 / len(target))
    # Project analytic constructions (1/N, cap weight, inverse-vol, HRP) onto the same
    # box and segment constraints used by the numerical optimisers.
    feasible = linprog(np.zeros(len(target)), A_ub=_segment_matrix(ctx),
                       b_ub=_segment_limits(ctx), A_eq=np.ones((1, len(target))), b_eq=[1.0],
                       bounds=ctx.bounds(), method="highs")
    if not feasible.success:
        raise ValueError(f"infeasible portfolio constraints: {feasible.message}")
    projected = minimize(lambda x: float(np.sum((x - target) ** 2)), feasible.x, method="SLSQP",
                         bounds=ctx.bounds(), constraints=ctx.constraints(), options={"maxiter": 500, "ftol": 1e-12})
    out = projected.x if projected.success else feasible.x
    weights = pd.Series(out, index=ctx.tickers)
    if not projected.success:
        weights.attrs.update(construction_status="fallback", fallback_reason=str(projected.message))
    return weights


def _segment_matrix(ctx: Ctx) -> np.ndarray | None:
    if ctx.segments is None:
        return None
    segs = ctx.segments.reindex(ctx.tickers)
    names = sorted(segs.dropna().unique())
    return np.vstack([(segs == s).values.astype(float) for s in names]) if names else None


def _segment_limits(ctx: Ctx) -> np.ndarray | None:
    A = _segment_matrix(ctx)
    return np.full(A.shape[0], ctx.cfg.max_segment_weight) if A is not None else None


def _solve(obj, ctx: Ctx, x0: np.ndarray | None = None, tries: int = 2) -> pd.Series:
    ctx.check()
    n = len(ctx.tickers)
    start = _norm(np.full(n, 1.0 / n), ctx).values
    starts = [x0 if x0 is not None else start, start]
    if tries > 1:
        starts.append(_norm(np.random.default_rng(0).dirichlet(np.ones(n)), ctx).values)
    best = None
    for s0 in starts[:tries + 1]:
        r = minimize(obj, s0, method="SLSQP", bounds=ctx.bounds(), constraints=ctx.constraints(),
                     options={"maxiter": 500, "ftol": 1e-11})
        if r.success and (best is None or r.fun < best.fun):
            best = r
    if best is None:
        log.debug("construction solve did not converge; using the capped equal-weight start")
        weights = _norm(start, ctx)
        weights.attrs.update(construction_status="fallback", fallback_reason="construction solver did not converge")
        return weights
    return _norm(best.x, ctx)


# ----------------------------------------------------------------------------- risk-only methods
def equal_weight(ctx: Ctx) -> pd.Series:
    return _norm(np.full(len(ctx.tickers), 1.0 / len(ctx.tickers)), ctx)


def cap_weight(ctx: Ctx) -> pd.Series:
    if ctx.caps is None:
        return equal_weight(ctx)
    c = ctx.caps.reindex(ctx.tickers).astype(float)
    c = c.fillna(c.median() if c.notna().any() else 1.0).clip(lower=0)
    return _norm((c / c.sum()).values, ctx)


def inverse_vol(ctx: Ctx) -> pd.Series:
    iv = 1.0 / ctx.vols.replace(0, np.nan)
    return _norm((iv / iv.sum()).values, ctx)


def min_variance(ctx: Ctx) -> pd.Series:
    S = ctx.cov.values
    return _solve(lambda w: w @ S @ w, ctx)


def risk_parity(ctx: Ctx) -> pd.Series:
    """Equal risk contribution: each name contributes the same share of portfolio variance."""
    S, n = ctx.cov.values, len(ctx.tickers)

    def obj(w):
        v = np.sqrt(max(w @ S @ w, 1e-12))
        rc = w * (S @ w) / v
        return float(np.sum((rc - v / n) ** 2) * 1e4)
    return _solve(obj, ctx)


def segment_risk_parity(ctx: Ctx) -> pd.Series:
    """Equal risk budget per segment, then equal risk contribution inside each segment."""
    if ctx.segments is None:
        return risk_parity(ctx)
    segs = ctx.segments.reindex(ctx.tickers)
    names = sorted(segs.dropna().unique())
    if not names:
        return risk_parity(ctx)
    S = ctx.cov.values
    budget = np.array([1.0 / len(names) / max(int((segs == segs[t]).sum()), 1) for t in ctx.tickers])
    budget = budget / budget.sum()

    def obj(w):
        v = np.sqrt(max(w @ S @ w, 1e-12))
        rc = w * (S @ w) / v
        return float(np.sum((rc / v - budget) ** 2) * 1e4)
    return _solve(obj, ctx)


def max_diversification(ctx: Ctx) -> pd.Series:
    """Choueifaty: maximise (w'σ) / sqrt(w'Σw), the diversification ratio."""
    S, sig = ctx.cov.values, ctx.vols.values

    def obj(w):
        return -float((w @ sig) / np.sqrt(max(w @ S @ w, 1e-12)))
    return _solve(obj, ctx)


def hrp(ctx: Ctx) -> pd.Series:
    """Hierarchical Risk Parity (Lopez de Prado 2016): cluster by correlation distance, then recursive bisection.

    Needs no matrix inversion and no expected returns, which makes it robust when the covariance is noisy.
    Box constraints are applied afterwards by clipping and renormalising.
    """
    tick = ctx.tickers
    if len(tick) < 3:
        return inverse_vol(ctx)
    S = ctx.cov.values
    inv_sd = np.diag(1.0 / np.sqrt(np.clip(np.diag(S), 1e-12, None)))
    corr_m = np.clip(inv_sd @ S @ inv_sd, -1.0, 1.0)
    dist = np.sqrt(np.clip((1 - corr_m) / 2, 0, 1))
    np.fill_diagonal(dist, 0.0)
    link = linkage(squareform(dist, checks=False), method="single")
    order = [int(i) for i in to_tree(link, rd=False).pre_order()]
    w = pd.Series(1.0, index=range(len(tick)))
    clusters = [order]
    while clusters:
        nxt = []
        for c in clusters:
            if len(c) <= 1:
                continue
            half = len(c) // 2
            a, b = c[:half], c[half:]
            va = _cluster_var(S, a)
            vb = _cluster_var(S, b)
            alpha = 1 - va / (va + vb) if (va + vb) > 0 else 0.5
            for i in a:
                w[i] *= alpha
            for i in b:
                w[i] *= 1 - alpha
            nxt += [a, b]
        clusters = nxt
    out = np.array([w[i] for i in range(len(tick))])
    return _norm(out / out.sum(), ctx)


def _cluster_var(S: np.ndarray, idx: list[int]) -> float:
    sub = S[np.ix_(idx, idx)]
    iv = 1.0 / np.diag(sub)
    wv = iv / iv.sum()
    return float(wv @ sub @ wv)


# ----------------------------------------------------------------------------- return-aware methods
def max_sharpe(ctx: Ctx) -> pd.Series:
    if ctx.mu is None:
        return min_variance(ctx)
    m = (ctx.mu.reindex(ctx.tickers).astype(float) - ctx.rf).values
    S = ctx.cov.values

    def obj(w):
        return -float((w @ m) / np.sqrt(max(w @ S @ w, 1e-12)))
    return _solve(obj, ctx)


def mean_variance(ctx: Ctx) -> pd.Series:
    """argmax w'(mu-rf) - (delta/2) w'Sw - the utility form Black-Litterman is written for."""
    if ctx.mu is None:
        return min_variance(ctx)
    m = (ctx.mu.reindex(ctx.tickers).astype(float) - ctx.rf).values
    S = ctx.cov.values
    return _solve(lambda w: -float(w @ m - 0.5 * ctx.delta * w @ S @ w), ctx)


def kelly(ctx: Ctx) -> pd.Series:
    """Log-optimal growth: mean-variance utility with delta = 1 (full Kelly)."""
    if ctx.mu is None:
        return min_variance(ctx)
    m = (ctx.mu.reindex(ctx.tickers).astype(float) - ctx.rf).values
    S = ctx.cov.values
    return _solve(lambda w: -float(w @ m - 0.5 * w @ S @ w), ctx)


def resampled_max_sharpe(ctx: Ctx, draws: int = 12, seed: int = 0) -> pd.Series:
    """Michaud resampling: average the max-Sharpe weights over draws of (mu, Sigma) from their sampling error.

    Mean-variance optimisers are famously unstable in mu; averaging over the estimation error damps that.
    """
    if ctx.mu is None:
        return min_variance(ctx)
    rng = np.random.default_rng(seed)
    tick = ctx.tickers
    m = ctx.mu.reindex(tick).astype(float).values
    S = ctx.cov.values
    n_obs = len(ctx.returns) if ctx.returns is not None else TRADING_DAYS * 3
    se = np.sqrt(np.diag(S) / max(n_obs / TRADING_DAYS, 1e-6))
    acc = np.zeros(len(tick))
    ok = 0
    for _ in range(draws):
        mu_d = pd.Series(m + rng.normal(0, se * 0.25), index=tick)
        w = max_sharpe(Ctx(cov=ctx.cov, mu=mu_d, returns=ctx.returns, caps=ctx.caps, max_weights=ctx.max_weights, segments=ctx.segments,
                           rf=ctx.rf, delta=ctx.delta, cfg=ctx.cfg))
        acc += w.reindex(tick).fillna(0.0).values
        ok += 1
    return _norm(acc / max(ok, 1), ctx)


def min_cvar(ctx: Ctx, alpha: float = 0.95) -> pd.Series:
    """Rockafellar-Uryasev: minimise Conditional Value-at-Risk of the historical return scenarios (an LP).

    Optimises the loss tail rather than variance, which matters for the fat-tailed names in this universe.
    """
    if ctx.returns is None or len(ctx.returns) < 60:
        return min_variance(ctx)
    ctx.check()
    R = ctx.returns.reindex(columns=ctx.tickers).dropna(how="any").values
    T, n = R.shape
    # variables: [w (n), zeta (1), u (T)];  min zeta + 1/((1-a)T) sum u
    c = np.concatenate([np.zeros(n), [1.0], np.full(T, 1.0 / ((1 - alpha) * T))])
    # -R w - zeta - u <= 0
    A_ub = np.hstack([-R, -np.ones((T, 1)), -np.eye(T)])
    b_ub = np.zeros(T)
    if ctx.segments is not None:
        segs = ctx.segments.reindex(ctx.tickers)
        for s in sorted(segs.dropna().unique()):
            row = np.zeros(n + 1 + T)
            row[:n] = (segs == s).values.astype(float)
            A_ub = np.vstack([A_ub, row])
            b_ub = np.append(b_ub, ctx.cfg.max_segment_weight)
    A_eq = np.zeros((1, n + 1 + T))
    A_eq[0, :n] = 1.0
    bounds = ctx.bounds() + [(None, None)] + [(0, None)] * T
    try:
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=[1.0], bounds=bounds, method="highs")
    except Exception as e:                                   # pragma: no cover - solver edge
        log.debug("CVaR LP failed (%s); falling back to min variance", e)
        return min_variance(ctx)
    if not res.success:
        return min_variance(ctx)
    return _norm(res.x[:n], ctx)


# ----------------------------------------------------------------------------- registry
REGISTRY: dict[str, dict] = {
    "equal_weight":        {"fn": equal_weight,        "needs_mu": False, "label": "Equal weight",            "family": "risk-only",    "desc": "1/N. The benchmark every other method has to beat."},
    "cap_weight":          {"fn": cap_weight,          "needs_mu": False, "label": "Cap weight",              "family": "risk-only",    "desc": "Market-cap proportional within the selected names."},
    "inverse_vol":         {"fn": inverse_vol,         "needs_mu": False, "label": "Inverse volatility",      "family": "risk-only",    "desc": "w proportional to 1/sigma. Risk parity's diagonal-only approximation."},
    "risk_parity":         {"fn": risk_parity,         "needs_mu": False, "label": "Risk parity (ERC)",       "family": "risk-only",    "desc": "Equal risk contribution. The current production default."},
    "segment_risk_parity": {"fn": segment_risk_parity, "needs_mu": False, "label": "Segment risk parity",     "family": "risk-only",    "desc": "Equal risk budget per value-chain segment, ERC inside each."},
    "min_variance":        {"fn": min_variance,        "needs_mu": False, "label": "Minimum variance",        "family": "risk-only",    "desc": "Lowest ex-ante variance under the constraints."},
    "max_diversification": {"fn": max_diversification, "needs_mu": False, "label": "Max diversification",     "family": "risk-only",    "desc": "Choueifaty: maximise (w'sigma)/sqrt(w'Sw)."},
    "hrp":                 {"fn": hrp,                 "needs_mu": False, "label": "Hierarchical risk parity","family": "risk-only",    "desc": "Lopez de Prado: correlation clustering + recursive bisection, no matrix inversion."},
    "max_sharpe":          {"fn": max_sharpe,          "needs_mu": True,  "label": "Max Sharpe",              "family": "return-aware", "desc": "Tangency portfolio on the supplied expected returns."},
    "mean_variance":       {"fn": mean_variance,       "needs_mu": True,  "label": "Mean-variance / Black-Litterman", "family": "return-aware", "desc": "BL posterior returns and covariance in the main portfolio; mean-variance utility objective."},
    "kelly":               {"fn": kelly,               "needs_mu": True,  "label": "Kelly (log-optimal)",     "family": "return-aware", "desc": "Growth-optimal: mean-variance utility at delta = 1."},
    "resampled_max_sharpe":{"fn": resampled_max_sharpe,"needs_mu": True,  "label": "Resampled max Sharpe",    "family": "return-aware", "desc": "Michaud: average max-Sharpe weights over draws of estimation error."},
    "min_cvar":            {"fn": min_cvar,            "needs_mu": False, "label": "Minimum CVaR (95%)",      "family": "risk-only",    "desc": "Rockafellar-Uryasev LP on the loss tail rather than variance."},
}
RISK_ONLY = [k for k, v in REGISTRY.items() if not v["needs_mu"]]
RETURN_AWARE = [k for k, v in REGISTRY.items() if v["needs_mu"]]


def build(name: str, ctx: Ctx) -> pd.Series:
    if name not in REGISTRY:
        raise KeyError(f"unknown construction {name!r}; known: {', '.join(REGISTRY)}")
    return REGISTRY[name]["fn"](ctx)


def build_all(ctx: Ctx, names: list[str] | None = None) -> dict[str, pd.Series]:
    out = {}
    for k in (names or list(REGISTRY)):
        try:
            out[k] = build(k, ctx)
        except Exception as e:
            log.warning("construction %s failed: %s", k, e)
    return out


def reads_mu(name: str, ctx: Ctx, probe: float = 3.0) -> float:
    """Empirically: how far do this method's weights move when expected returns change a lot?

    Returns the one-way turnover between weights built on two very different mu vectors with the covariance
    held fixed. Exactly 0 means the method cannot see expected returns at all.
    """
    if ctx.mu is None:
        return float("nan")
    lo = Ctx(cov=ctx.cov, mu=pd.Series(ctx.rf + 0.01, index=ctx.tickers), returns=ctx.returns, caps=ctx.caps, max_weights=ctx.max_weights,
             segments=ctx.segments, rf=ctx.rf, delta=ctx.delta, cfg=ctx.cfg)
    hi = Ctx(cov=ctx.cov, mu=ctx.mu * probe, returns=ctx.returns, caps=ctx.caps, max_weights=ctx.max_weights, segments=ctx.segments,
             rf=ctx.rf, delta=ctx.delta, cfg=ctx.cfg)
    return float(0.5 * (build(name, lo) - build(name, hi)).abs().sum())
