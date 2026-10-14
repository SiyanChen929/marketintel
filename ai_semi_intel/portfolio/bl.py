"""Black-Litterman engine: equilibrium prior, multi-source views, posterior returns AND posterior covariance.

Why a separate module: ``optimizer.black_litterman`` builds a single absolute-view posterior that
``run_portfolio`` then discards (it optimises Grinold alphas instead). This module keeps BL as a
first-class construction with

* two priors - reverse-optimised market equilibrium (Idzorek/He-Litterman) and CAPM;
* typed views - absolute (per name) and relative (long/short baskets, e.g. segment tilts), each with
  its own confidence, so fair-value upside, live event scores and analyst targets can be mixed;
* the posterior covariance ``S + M`` (BL's parameter uncertainty), which the optimiser then uses;
* per-view attribution: how many basis points each view moved each name's posterior return.

Reference: He & Litterman (1999) closed form with Omega = diag(P tau S P') / confidence (Idzorek's
confidence scaling), solved with ``np.linalg.solve`` rather than explicit inverses.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config.settings import CONFIG, PortfolioConfig
from ..utils.io import log

TRADING_DAYS = 252


# ----------------------------------------------------------------------------- views
@dataclass
class View:
    """One view. ``weights`` maps ticker -> loading (absolute view: {t: 1.0}; relative: longs +1/n, shorts -1/n).

    ``q`` is the annualised EXCESS return (over the risk-free rate) the view asserts for that combination.
    ``confidence`` in (0, 1]: 1.0 = as certain as the prior's own scale, small = nearly ignored.
    """
    name: str
    weights: dict[str, float]
    q: float
    confidence: float = 0.5
    source: str = ""
    detail: str = ""

    def row(self, tickers: list[str]) -> np.ndarray:
        return np.array([float(self.weights.get(t, 0.0)) for t in tickers])


def _z(s: pd.Series) -> pd.Series:
    x = s.astype(float)
    sd = x.std()
    return (x - x.mean()) / sd if sd and sd > 0 else pd.Series(0.0, index=x.index)


def views_from_upside(top: pd.DataFrame, rf: float, cfg: PortfolioConfig | None = None) -> list[View]:
    """Absolute views from the blended fair-value upside, annualised over the view horizon.

    Confidence rises with the number of agreeing valuation methods and falls with their dispersion -
    the same construction the legacy BL call used, kept so results stay comparable.
    """
    cfg = cfg or CONFIG.portfolio
    out: list[View] = []
    for t, r in top.iterrows():
        up = r.get("upside")
        if up is None or pd.isna(up) or up <= -0.95:
            continue
        q = float(np.sign(1 + up) * np.abs(1 + up) ** (1 / cfg.view_horizon_years) - 1 - rf)
        q = float(np.clip(q, cfg.view_clip[0], cfg.view_clip[1]))
        n_meth = float(r.get("n_methods") or 1)
        disp = float(r.get("upside_dispersion") or 0.3)
        conf = float(np.clip((n_meth / 3.0) * (1 - np.clip(disp, 0, 0.8) / 2), 0.15, 1.0))
        out.append(View(f"FV:{t}", {t: 1.0}, q, conf, "fair_value", f"blended upside {up:+.0%} over {cfg.view_horizon_years:.0f}y, {int(n_meth)} methods, dispersion {disp:.2f}"))
    return out


def views_from_events(overlay: pd.DataFrame, tickers: list[str], scale: float = 0.04, cap: float = 0.12,
                      min_abs: float = 0.35, conf: float = 0.30) -> list[View]:
    """Absolute views from the live market-intelligence event score (market_intel overlay).

    The event score is a signed, decayed sum of classified event impacts. It has no validated forward IC
    yet, so it enters as a deliberately small, low-confidence tilt: q = scale x score (capped), and only
    for names whose |score| clears ``min_abs``. Raise ``scale``/``conf`` only after the live IC is positive.
    """
    if overlay is None or overlay.empty or "event_score_7d" not in overlay.columns:
        return []
    out: list[View] = []
    s = pd.to_numeric(overlay["event_score_7d"], errors="coerce")
    for t in tickers:
        if t not in s.index or pd.isna(s.get(t)):
            continue
        v = float(s[t])
        if abs(v) < min_abs:
            continue
        q = float(np.clip(v * scale, -cap, cap))
        lvl = overlay.loc[t].get("alert_level") if t in overlay.index else None
        n = overlay.loc[t].get("n_events_7d") if t in overlay.index else None
        out.append(View(f"EV:{t}", {t: 1.0}, q, conf, "event_score", f"7d event score {v:+.2f} over {int(n or 0)} events, level {lvl or '-'}"))
    return out


def views_from_targets(top: pd.DataFrame, rf: float, horizon_years: float = 1.0, clip: tuple = (-0.25, 0.35),
                       conf_scale: float = 0.06) -> list[View]:
    """Absolute views from the sell-side consensus target (1-year horizon), confidence from analyst count."""
    out: list[View] = []
    for t, r in top.iterrows():
        up = r.get("target_upside")
        if up is None or pd.isna(up):
            continue
        q = float(np.clip((1 + up) ** (1 / horizon_years) - 1 - rf, clip[0], clip[1]))
        n = float(r.get("n_analysts") or 0)
        if n < 3:
            continue
        conf = float(np.clip(n * conf_scale, 0.15, 0.8))
        out.append(View(f"TGT:{t}", {t: 1.0}, q, conf, "analyst_target", f"consensus target {up:+.0%} from {int(n)} analysts"))
    return out


def relative_segment_views(top: pd.DataFrame, score_col: str = "composite", rf: float = 0.0, spread: float = 0.05,
                           conf: float = 0.4, min_names: int = 1) -> list[View]:
    """Relative views: within the portfolio, the top-scoring half of a segment outperforms the bottom half.

    Relative views are BL's real strength - they express a ranking without asserting a level, so they tilt
    weights without inflating the whole portfolio's expected return.
    """
    if score_col not in top.columns or "segment" not in top.columns:
        return []
    out: list[View] = []
    for seg, grp in top.groupby("segment"):
        g = grp[score_col].dropna().sort_values(ascending=False)
        if len(g) < 2 * min_names:
            continue
        k = len(g) // 2
        longs, shorts = list(g.index[:k]), list(g.index[-k:])
        w = {t: 1.0 / len(longs) for t in longs}
        w.update({t: -1.0 / len(shorts) for t in shorts})
        out.append(View(f"REL:{seg[:22]}", w, spread, conf, "segment_ranking",
                        f"{'/'.join(longs)} over {'/'.join(shorts)} by {score_col}, {spread:.0%} p.a."))
    return out


# ----------------------------------------------------------------------------- priors
def calibrate_delta(cov: pd.DataFrame, w_mkt: pd.Series, betas: pd.Series | None, erp: float) -> float:
    """Risk-aversion implied by the basket, not a guess.

    An uncalibrated delta makes the prior absurd: with 40-50% vol AI/semi names, delta = 2.5 implies
    13-28% equilibrium excess returns and the prior then swamps every view. Instead solve
    ``delta = beta_basket * erp / var_basket`` so the cap-weighted basket's own equilibrium excess return
    equals its CAPM expectation (He-Litterman calibrate delta from the market Sharpe ratio the same way).
    """
    var = float(w_mkt.values @ cov.values @ w_mkt.values)
    if var <= 0:
        return CONFIG.portfolio.risk_aversion
    beta = float((betas.reindex(w_mkt.index).fillna(1.2).clip(0.5, 2.5) * w_mkt).sum()) if betas is not None else 1.0
    return float(np.clip(beta * erp / var, 0.05, 10.0))


def equilibrium_prior(cov: pd.DataFrame, mkt_caps: pd.Series, delta: float | None = None,
                      betas: pd.Series | None = None, erp: float = 0.045) -> tuple[pd.Series, pd.Series, float]:
    """Reverse optimisation: pi = delta * S * w_mkt, w_mkt = cap weights of the selected names.

    ``delta=None`` calibrates it from the basket (recommended); a number is used as given.
    """
    tick = list(cov.index)
    w = mkt_caps.reindex(tick).astype(float)
    w = w.fillna(w.median() if w.notna().any() else 1.0)
    w = w / w.sum()
    d = calibrate_delta(cov, w, betas, erp) if delta is None else float(delta)
    pi = pd.Series(d * cov.values @ w.values, index=tick)
    return pi, w, d


def capm_prior(betas: pd.Series, erp: float, tick: list[str]) -> pd.Series:
    return betas.reindex(tick).fillna(1.2).clip(0.5, 2.5).astype(float) * erp


# ----------------------------------------------------------------------------- posterior
def posterior(cov: pd.DataFrame, pi: pd.Series, views: list[View], tau: float = 0.05,
              rf: float = 0.0, omega_mode: str = "idzorek") -> dict:
    """He-Litterman posterior. Returns excess-return posterior, posterior covariance and diagnostics.

    Omega modes and what tau does (verified in the tests):
    * ``"idzorek"`` (default) - Omega = diag(P tau S P')/confidence. Omega scales with tau, so **tau cancels
      exactly** and confidence is the only dial. One fewer arbitrary knob, which is why it is the default.
    * ``"prop"`` - textbook He-Litterman Omega = diag(P tau S P'). Also tau-invariant, for the same reason.
    * ``"absolute"`` - Omega = diag(P S P')/confidence, independent of tau. Only here does tau genuinely trade
      prior confidence against view confidence, so the tau sensitivity analysis uses this mode.

    mu_bar = pi + tau S P' (P tau S P' + Omega)^-1 (q - P pi)
    M      = tau S - tau S P' (P tau S P' + Omega)^-1 P tau S      (posterior parameter uncertainty)
    Sigma_bar = S + M
    """
    tick = list(cov.index)
    S = cov.values.astype(float)
    pi_v = pi.reindex(tick).astype(float).values
    if not views:
        return {"mu_excess": pd.Series(pi_v, index=tick), "mu": pd.Series(pi_v + rf, index=tick), "prior": pd.Series(pi_v + rf, index=tick),
                "cov_post": cov.copy(), "views": [], "P": None, "q": None, "omega": None, "attribution": pd.DataFrame(index=tick),
                "view_table": pd.DataFrame(), "tilt": pd.Series(0.0, index=tick)}
    P = np.vstack([v.row(tick) for v in views])
    q = np.array([v.q for v in views], dtype=float)
    conf = np.array([max(min(v.confidence, 1.0), 1e-3) for v in views], dtype=float)
    tS = tau * S
    PtS = P @ tS                                   # k x n
    A = PtS @ P.T                                  # k x k  = P tau S P'
    diag_A = np.clip(np.diag(A), 1e-10, None)
    if omega_mode == "absolute":                   # Omega from view variance alone (no tau) -> tau is a real dial
        omega = np.diag(np.clip(np.diag(P @ S @ P.T), 1e-10, None) / conf)
    elif omega_mode == "prop":                     # He-Litterman: Omega = diag(P tau S P')
        omega = np.diag(diag_A)
    else:                                          # Idzorek confidence scaling (default)
        omega = np.diag(diag_A / conf)
    B = A + omega
    resid = q - P @ pi_v                           # view surprise vs the prior
    try:
        sol = np.linalg.solve(B, resid)            # (P tau S P' + Omega)^-1 (q - P pi)
        K = np.linalg.solve(B, PtS)                # (…)^-1 P tau S
    except np.linalg.LinAlgError:                  # pragma: no cover - defensive
        log.warning("BL: singular view system, falling back to lstsq")
        sol = np.linalg.lstsq(B, resid, rcond=None)[0]
        K = np.linalg.lstsq(B, PtS, rcond=None)[0]
    tilt = PtS.T @ sol                             # n-vector: posterior minus prior
    mu_excess = pi_v + tilt
    M = tS - PtS.T @ K
    M = (M + M.T) / 2.0
    cov_post = pd.DataFrame(S + M, index=tick, columns=tick)
    # per-view attribution: contribution of view j to each name's tilt
    contrib = PtS.T * sol[None, :]                 # n x k
    attribution = pd.DataFrame(contrib, index=tick, columns=[v.name for v in views])
    view_table = pd.DataFrame([{"view": v.name, "source": v.source, "q": v.q, "confidence": v.confidence,
                                "prior_view_return": float((P @ pi_v)[i]), "surprise": float(resid[i]),
                                "omega": float(omega[i, i]), "abs_impact_bps": float(np.abs(contrib[:, i]).sum() * 1e4),
                                "detail": v.detail, "n_legs": int((P[i] != 0).sum())} for i, v in enumerate(views)])
    return {"mu_excess": pd.Series(mu_excess, index=tick), "mu": pd.Series(mu_excess + rf, index=tick),
            "prior": pd.Series(pi_v + rf, index=tick), "cov_post": cov_post, "views": views, "P": P, "q": q,
            "omega": omega, "attribution": attribution, "view_table": view_table.sort_values("abs_impact_bps", ascending=False),
            "tilt": pd.Series(tilt, index=tick)}


# ----------------------------------------------------------------------------- end-to-end construction
VIEW_SOURCES = ("fair_value", "event_score", "analyst_target", "segment_ranking")

#: Where each view's number actually comes from. BL needs views; this records who produced them, so a reader
#: can audit a tilt back to the model or feed that generated it rather than taking "our view" on trust.
VIEW_PROVENANCE = {
    "fair_value": {
        "label": "Blended fair value", "field": "upside", "kind": "absolute",
        "origin": "ai_semi_intel valuation: DCF (50%) + peer-regression multiple (30%) + sell-side target (20%)",
        "inputs": "SEC EDGAR fundamentals, Yahoo consensus revenue/EPS, FRED risk-free rate, Damodaran-style ERP",
        "q": "upside annualised over the view horizon, clipped to the config's view_clip",
        "confidence": "rises with the number of agreeing valuation methods, falls with their dispersion",
        "caveat": "a model output, not a market observation; the DCF and the regression frequently disagree by more than 2x",
    },
    "event_score": {
        "label": "Live event score", "field": "event_score_7d", "kind": "absolute",
        "origin": "market_intel: SEC filings, news, press releases and intraday moves classified by rules + LLM",
        "inputs": "SEC EDGAR current feed, Yahoo/Google News, DigiTimes, Fed/BLS, 5-minute prices",
        "q": "0.04 x the 7-day decayed event score, capped at +/-12%, only when |score| >= 0.35",
        "confidence": "fixed at 0.30 - deliberately low",
        "caveat": "NO validated forward IC yet; sized to tilt, not to drive. Raise only after the live IC turns positive",
    },
    "analyst_target": {
        "label": "Sell-side consensus target", "field": "target_upside", "kind": "absolute",
        "origin": "Yahoo Finance consensus price target (mean of contributing analysts)",
        "inputs": "targetMeanPrice, numberOfAnalystOpinions",
        "q": "target upside over a 1-year horizon, clipped to (-25%, +35%)",
        "confidence": "scales with analyst count, capped at 0.8; names with fewer than 3 analysts are skipped",
        "caveat": "targets are known to be optimistic and slow to update; the largest raw tilt of the four sources",
    },
    "segment_ranking": {
        "label": "Within-segment ranking", "field": "composite", "kind": "relative",
        "origin": "the stock-selection composite score, ranked inside each value-chain segment",
        "inputs": "fundamental momentum, price momentum and the valuation residual (see docs/FACTOR_RESEARCH.md)",
        "q": "top half over bottom half of each segment, 5% p.a. spread",
        "confidence": "0.40",
        "caveat": "the only relative view: expresses a ranking without asserting a level, so it tilts without inflating expected return",
    },
}


def build_views(top: pd.DataFrame, rf: float, cfg: PortfolioConfig | None = None, overlay: pd.DataFrame | None = None,
                sources: tuple[str, ...] = ("fair_value", "event_score"), event_scale: float = 0.04,
                event_conf: float = 0.30, segment_spread: float = 0.05) -> list[View]:
    cfg = cfg or CONFIG.portfolio
    views: list[View] = []
    if "fair_value" in sources:
        views += views_from_upside(top, rf, cfg)
    if "analyst_target" in sources:
        views += views_from_targets(top, rf)
    if "event_score" in sources and overlay is not None:
        views += views_from_events(overlay, list(top.index), scale=event_scale, conf=event_conf)
    if "segment_ranking" in sources:
        views += relative_segment_views(top, rf=rf, spread=segment_spread)
    return views


def run_bl(top: pd.DataFrame, cov: pd.DataFrame, rf: float, cfg: PortfolioConfig | None = None,
           betas: pd.Series | None = None, erp: float = 0.045, overlay: pd.DataFrame | None = None,
           prior: str = "equilibrium", tau: float | None = None, sources: tuple[str, ...] = ("fair_value", "event_score"),
           use_posterior_cov: bool = True, max_w: pd.Series | None = None, delta: float | None = None,
           omega_mode: str = "idzorek", **view_kw) -> dict:
    """Full BL construction: prior -> views -> posterior -> constrained optimisation on the posterior."""
    from . import optimizer
    cfg = cfg or CONFIG.portfolio
    tau = cfg.bl_tau if tau is None else tau
    tick = list(cov.index)
    top = top.reindex(tick)
    delta_used = None
    if prior == "capm" and betas is not None:
        pi = capm_prior(betas, erp, tick)
        caps = top["market_cap"].reindex(tick)
        caps = caps.fillna(caps.median())
        w_mkt = caps / caps.sum()
    else:
        pi, w_mkt, delta_used = equilibrium_prior(cov, top["market_cap"], delta, betas, erp)
    views = build_views(top, rf, cfg, overlay, sources, **{k: v for k, v in view_kw.items() if k in ("event_scale", "event_conf", "segment_spread")})
    post = posterior(cov, pi, views, tau, rf, omega_mode)
    cov_used = post["cov_post"] if use_posterior_cov else cov
    opt = optimizer.optimise(post["mu"], cov_used, rf, top["segment"], cfg, max_w=max_w)
    post.update({"opt": opt, "w_mkt": w_mkt, "cov_used": cov_used, "tau": tau, "prior_kind": prior,
                 "sources": list(sources), "n_views": len(views), "delta": delta_used, "omega_mode": omega_mode,
                 "prior_basket_return": float((pi * w_mkt.reindex(pi.index).fillna(0)).sum()) + rf})
    return post


# ----------------------------------------------------------------------------- BL-optimal portfolio
def mean_variance_weights(mu: pd.Series, cov: pd.DataFrame, rf: float, delta: float, segments: pd.Series | None = None,
                          cfg: PortfolioConfig | None = None, max_w: pd.Series | None = None) -> pd.Series:
    """argmax_w  w'(mu - rf) - (delta/2) w'Sw  subject to the book's constraints (fully invested, per-name and
    per-segment caps).

    This is the construction Black-Litterman is written for: the unconstrained solution is
    w = (delta S)^-1 (mu - rf), which returns exactly the cap weights when there are no views. Risk parity,
    by contrast, never looks at mu, so BL views cannot reach it - see docs/BLACK_LITTERMAN.md.
    """
    from scipy.optimize import minimize

    from .optimizer import check_feasible
    cfg = cfg or CONFIG.portfolio
    tick = list(cov.index)
    m = (mu.reindex(tick).astype(float) - rf).values
    S = cov.values
    caps = (max_w.reindex(tick).fillna(cfg.max_weight) if max_w is not None else pd.Series(cfg.max_weight, index=tick)).clip(cfg.min_weight, cfg.max_weight)
    bounds = [(cfg.min_weight, float(caps[t])) for t in tick]
    segs_chk = segments.reindex(tick) if segments is not None else None
    seg_names_chk = sorted(segs_chk.dropna().unique()) if segs_chk is not None else []
    check_feasible(len(tick), cfg.min_weight, caps.values, cfg.max_segment_weight if seg_names_chk else None,
                   [int((segs_chk == s).sum()) for s in seg_names_chk] if seg_names_chk else None)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    if segments is not None:
        segs = segments.reindex(tick)
        for sname in sorted(segs.dropna().unique()):
            mask = (segs == sname).values.astype(float)
            cons.append({"type": "ineq", "fun": (lambda mk: (lambda w: cfg.max_segment_weight - w @ mk))(mask)})

    def neg_util(w):
        return -(w @ m - 0.5 * delta * w @ S @ w)
    x0 = np.clip(np.full(len(tick), 1.0 / len(tick)), [b[0] for b in bounds], [b[1] for b in bounds])
    x0 = x0 / x0.sum()
    best = None
    for start in (x0, np.random.default_rng(0).dirichlet(np.ones(len(tick)))):
        r = minimize(neg_util, start, method="SLSQP", bounds=bounds, constraints=cons, options={"maxiter": 600, "ftol": 1e-11})
        if r.success and (best is None or r.fun < best.fun):
            best = r
    if best is None:
        log.warning("BL mean-variance solve did not converge; returning the capped equal-weight start")
        return pd.Series(x0, index=tick)
    w = np.clip(best.x, [b[0] for b in bounds], [b[1] for b in bounds])
    return pd.Series(w / w.sum(), index=tick)
