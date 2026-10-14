"""Black-Litterman engine, evaluation and panel page (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.config.settings import CONFIG, ScreenConfig
from ai_semi_intel.portfolio import bl

TICK = list("ABCDEF")


@pytest.fixture(scope="module")
def cov() -> pd.DataFrame:
    vol = np.array([0.40, 0.42, 0.38, 0.45, 0.50, 0.35])
    C = np.full((6, 6), 0.35)
    np.fill_diagonal(C, 1.0)
    return pd.DataFrame(np.outer(vol, vol) * C, index=TICK, columns=TICK)


@pytest.fixture(scope="module")
def caps() -> pd.Series:
    return pd.Series([1e11, 5e10, 2e10, 1e10, 8e9, 5e9], index=TICK, dtype=float)


@pytest.fixture(scope="module")
def betas() -> pd.Series:
    return pd.Series([1.9, 1.7, 1.5, 2.0, 2.2, 1.3], index=TICK)


def test_no_views_leaves_prior_and_covariance_untouched(cov, caps, betas):
    pi, w, d = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    p = bl.posterior(cov, pi, [], 0.05, 0.045)
    assert np.allclose(p["mu_excess"].values, pi.values)
    assert np.allclose(p["cov_post"].values, cov.values)
    assert p["tilt"].abs().max() == 0.0 and p["view_table"].empty


def test_delta_calibration_matches_capm_expectation(cov, caps, betas):
    """The whole point: an uncalibrated delta implies absurd equilibrium returns for 40-50% vol names."""
    pi_c, w, d = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    assert 0.05 < d < 3.0
    basket = float((pi_c * w).sum())
    capm = float((betas * w).sum() * 0.045)
    assert abs(basket - capm) < 1e-9                          # calibration is exact by construction
    pi_u, _, _ = bl.equilibrium_prior(cov, caps, 2.5, betas, 0.045)
    assert pi_u.max() > 0.20 and pi_c.max() < pi_u.max()      # the uncalibrated prior is the pathological one


def test_absolute_view_pulls_name_between_prior_and_view(cov, caps, betas):
    pi, _, _ = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    q = 0.20
    p = bl.posterior(cov, pi, [bl.View("v", {"A": 1.0}, q, 0.9, "t")], 0.05, 0.045)
    lo, hi = sorted([float(pi["A"]), q])
    assert lo <= float(p["mu_excess"]["A"]) <= hi              # posterior sits between prior and view
    assert p["tilt"].abs().idxmax() == "A"                     # the viewed name moves most
    assert (np.diag(p["cov_post"].values) >= np.diag(cov.values) - 1e-12).all()   # parameter uncertainty added


def test_view_confidence_is_monotone_and_attribution_sums_to_tilt(cov, caps, betas):
    pi, _, _ = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    mags = [float(abs(bl.posterior(cov, pi, [bl.View("v", {"A": 1.0}, 0.20, c, "t")], 0.05, 0.045)["tilt"]["A"])) for c in (0.05, 0.2, 0.5, 0.9)]
    assert all(b > a for a, b in zip(mags, mags[1:]))
    p = bl.posterior(cov, pi, [bl.View("v1", {"A": 1.0}, 0.20, 0.6, "t"), bl.View("v2", {"B": 1.0}, -0.05, 0.6, "t")], 0.05, 0.045)
    assert np.allclose(p["attribution"].sum(axis=1).values, p["tilt"].values)     # attribution decomposes the tilt
    assert set(p["view_table"]["view"]) == {"v1", "v2"}


def test_tau_only_bites_with_a_tau_independent_omega(cov, caps, betas):
    """Under Idzorek/He-Litterman Omega (both proportional to tau), tau cancels exactly - documented, not a bug."""
    pi, _, _ = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    v = [bl.View("v", {"A": 1.0}, 0.20, 0.5, "t")]
    for mode in ("idzorek", "prop"):
        t = {round(float(bl.posterior(cov, pi, v, x, 0.045, mode)["tilt"]["A"]), 9) for x in (0.01, 0.05, 0.25, 1.0)}
        assert len(t) == 1, mode
    t_abs = [float(bl.posterior(cov, pi, v, x, 0.045, "absolute")["tilt"]["A"]) for x in (0.01, 0.05, 0.25, 1.0)]
    assert len(set(np.round(t_abs, 9))) == 4 and all(b > a for a, b in zip(t_abs, t_abs[1:]))


def test_relative_view_respects_the_prior_it_disagrees_with(cov, caps, betas):
    """A long/short view moves legs in opposite directions relative to what the prior already implied."""
    pi, _, _ = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    legs = {"E": 0.5, "F": 0.5, "A": -0.5, "B": -0.5}
    spread_prior = float(pi["E"] + pi["F"] - pi["A"] - pi["B"]) / 2
    p = bl.posterior(cov, pi, [bl.View("rel", legs, spread_prior + 0.10, 0.6, "t")], 0.05, 0.045)
    t = p["tilt"]
    assert t["E"] > 0 and t["F"] > 0 and t["A"] < 0 and t["B"] < 0
    assert abs(float(t.sum())) < abs(float(t["E"])) * 2        # relative views barely move the overall level


def test_mean_variance_consumes_mu_while_risk_parity_ignores_it(cov, caps, betas):
    from ai_semi_intel.portfolio import optimizer
    pi, w_mkt, d = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    segs = pd.Series(["s1", "s1", "s2", "s2", "s3", "s3"], index=TICK)
    import dataclasses
    cfg = dataclasses.replace(CONFIG.portfolio, max_weight=0.35, max_segment_weight=0.6)   # 6 names need >= 1/6 each
    mu_lo = pd.Series(0.045 + 0.01, index=TICK)
    mu_hi = pd.Series(np.linspace(0.30, 0.05, 6), index=TICK)
    w_rp_lo = optimizer.optimise(mu_lo, cov, 0.045, segs, cfg)["risk_parity"]["weights"]
    w_rp_hi = optimizer.optimise(mu_hi, cov, 0.045, segs, cfg)["risk_parity"]["weights"]
    assert float((w_rp_lo - w_rp_hi).abs().sum()) < 1e-6      # risk parity never looks at mu
    w_mv_lo = bl.mean_variance_weights(mu_lo, cov, 0.045, d, segs, cfg)
    w_mv_hi = bl.mean_variance_weights(mu_hi, cov, 0.045, d, segs, cfg)
    assert float((w_mv_lo - w_mv_hi).abs().sum()) > 0.05      # mean-variance does
    assert abs(float(w_mv_hi.sum()) - 1.0) < 1e-6 and w_mv_hi.max() <= cfg.max_weight + 1e-6
    assert w_mv_hi.idxmax() == "A"                            # highest mu gets the largest weight


def test_view_builders_are_defensive(cov):
    top = pd.DataFrame({"upside": [0.5, np.nan, -0.99], "n_methods": [3, 3, 3], "upside_dispersion": [0.1, 0.1, 0.1],
                        "target_upside": [0.3, 0.2, 0.1], "n_analysts": [10, 1, 20], "composite": [1.0, 0.5, -0.5],
                        "segment": ["s1", "s1", "s2"], "market_cap": [1e10] * 3}, index=["A", "B", "C"])
    fv = bl.views_from_upside(top, 0.045)
    assert [v.weights for v in fv] == [{"A": 1.0}]            # NaN and <= -95% upside skipped
    tg = bl.views_from_targets(top, 0.045)
    assert {list(v.weights)[0] for v in tg} == {"A", "C"}     # fewer than 3 analysts skipped
    assert bl.views_from_events(pd.DataFrame(), ["A"]) == []
    ov = pd.DataFrame({"event_score_7d": [2.0, 0.1], "n_events_7d": [5, 1], "alert_level": ["warning", None]}, index=["A", "B"])
    ev = bl.views_from_events(ov, ["A", "B"])
    assert len(ev) == 1 and ev[0].source == "event_score" and abs(ev[0].q) <= 0.12     # low scores skipped, q capped


def test_bl_api_and_page(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    monkeypatch.delenv("MARKET_INTEL_TOKEN", raising=False)
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "token")
    idx = EntityIndex([Company(ticker="MU", name="Micron", segment="Memory & Storage", aliases=[], weight=0.1, cik=723125)])
    c = TestClient(create_app(store=Store(tmp_path / "t.sqlite"), index=idx), client=("127.0.0.1", 50000))
    r = c.get("/bl")
    assert r.status_code == 200
    for marker in ('id="nav"', 'id="banner"', 'id="hdr"', "statusBanner", "runChip", "Walk-forward"):
        assert marker in r.text, marker
    assert "['/bl','Black-Litterman']" in c.get("/static/base.js").text
    j = c.get("/api/research/bl").json()
    assert "available" in j and "run" in j
    assert c.get("/api/research/bl/status").json()["running"] is False
    assert c.post("/api/research/bl", json={"tickers": ["ZZZZ"]}).status_code == 400


def test_infeasible_weight_box_raises_instead_of_breaching_the_cap(cov, caps, betas):
    """5 names capped at 15% cannot be fully invested; SLSQP fails and the old code renormalised past the cap."""
    import dataclasses

    from ai_semi_intel.portfolio import optimizer
    pi, w, d = bl.equilibrium_prior(cov, caps, None, betas, 0.045)
    cfg = dataclasses.replace(CONFIG.portfolio, max_weight=0.15, max_segment_weight=0.35)
    segs = pd.Series(["s1", "s1", "s2", "s2", "s3", "s3"], index=TICK)
    with pytest.raises(ValueError, match="infeasible weight constraints"):
        bl.mean_variance_weights(pi + 0.045, cov, 0.045, d, segs, cfg)
    with pytest.raises(ValueError, match="infeasible weight constraints"):
        optimizer.optimise(pi + 0.045, cov, 0.045, segs, cfg)
    ok = dataclasses.replace(CONFIG.portfolio, max_weight=0.35, max_segment_weight=0.6)
    wmv = bl.mean_variance_weights(pi + 0.045, cov, 0.045, d, segs, ok)
    assert abs(float(wmv.sum()) - 1.0) < 1e-9 and wmv.max() <= 0.35 + 1e-9


# ----------------------------------------------------------------------------- construction library
@pytest.fixture(scope="module")
def ctx10():
    from ai_semi_intel.portfolio import constructions as C
    import dataclasses
    rng = np.random.default_rng(0)
    tk = list("ABCDEFGHIJ")
    vol = rng.uniform(0.3, 0.6, 10)
    Cm = np.full((10, 10), 0.4)
    np.fill_diagonal(Cm, 1.0)
    cov = pd.DataFrame(np.outer(vol, vol) * Cm, index=tk, columns=tk)
    R = pd.DataFrame(rng.multivariate_normal(np.zeros(10), cov.values / 252, 600), columns=tk)
    cfg = dataclasses.replace(CONFIG.portfolio, max_weight=0.30, max_segment_weight=0.60)
    return C.Ctx(cov=cov, mu=pd.Series(np.linspace(0.30, 0.06, 10), index=tk), returns=R,
                 caps=pd.Series(rng.uniform(1e9, 1e11, 10), index=tk),
                 segments=pd.Series(["s1"] * 3 + ["s2"] * 3 + ["s3"] * 4, index=tk), rf=0.045, delta=0.85, cfg=cfg)


def test_every_construction_returns_a_valid_portfolio(ctx10):
    from ai_semi_intel.portfolio import constructions as C
    ws = C.build_all(ctx10)
    assert set(ws) == set(C.REGISTRY), "a construction failed to build"
    for k, w in ws.items():
        assert abs(float(w.sum()) - 1.0) < 1e-6, k                       # fully invested
        assert w.min() >= ctx10.cfg.min_weight - 1e-9, k                 # box respected
        assert w.max() <= ctx10.cfg.max_weight + 1e-9, k
        assert list(w.index) == ctx10.tickers, k


def test_registry_families_match_measured_mu_dependence(ctx10):
    """The risk-only / return-aware split is not a label: it is measured on the weights."""
    from ai_semi_intel.portfolio import constructions as C
    for name, meta in C.REGISTRY.items():
        moved = C.reads_mu(name, ctx10)
        if meta["needs_mu"]:
            assert moved > 1e-6, f"{name} is declared return-aware but ignores mu"
        else:
            assert moved == 0.0, f"{name} is declared risk-only but moved {moved}"


def test_known_construction_properties(ctx10):
    from ai_semi_intel.portfolio import constructions as C
    ws = C.build_all(ctx10)
    assert ws["min_variance"].values @ ctx10.cov.values @ ws["min_variance"].values <= \
           ws["equal_weight"].values @ ctx10.cov.values @ ws["equal_weight"].values + 1e-9   # min var is minimal
    iv, vols = ws["inverse_vol"], ctx10.vols
    assert iv.idxmax() == vols.idxmin()                                   # inverse vol favours the calmest name
    assert abs(float(ws["equal_weight"].std())) < 1e-12                    # 1/N is flat
    mv = ws["mean_variance"]
    assert mv[ctx10.mu.idxmax()] > mv[ctx10.mu.idxmin()]                   # utility tilts toward high mu
    S = ctx10.cov.values
    for k in ("risk_parity",):                                             # ERC equalises risk contributions
        w = ws[k].values
        rc = w * (S @ w) / np.sqrt(w @ S @ w)
        assert float(rc.std() / rc.mean()) < 0.25, k


def test_segment_cap_is_respected_when_binding(ctx10):
    import dataclasses
    from ai_semi_intel.portfolio import constructions as C
    tight = dataclasses.replace(ctx10.cfg, max_segment_weight=0.40)
    ctx = C.Ctx(cov=ctx10.cov, mu=ctx10.mu, returns=ctx10.returns, caps=ctx10.caps, segments=ctx10.segments,
                rf=ctx10.rf, delta=ctx10.delta, cfg=tight)
    for k in ("mean_variance", "max_sharpe", "min_variance", "risk_parity"):
        w = C.build(k, ctx)
        assert float(w.groupby(ctx.segments).sum().max()) <= 0.40 + 1e-4, k


def test_every_construction_respects_per_name_and_segment_caps(ctx10):
    from ai_semi_intel.portfolio import constructions as C
    max_weights = pd.Series(ctx10.cfg.max_weight, index=ctx10.tickers)
    max_weights.iloc[0] = 0.05
    ctx = C.Ctx(cov=ctx10.cov, mu=ctx10.mu, returns=ctx10.returns, caps=ctx10.caps,
                max_weights=max_weights, segments=ctx10.segments, rf=ctx10.rf, delta=ctx10.delta, cfg=ctx10.cfg)
    for name, w in C.build_all(ctx).items():
        assert w.iloc[0] <= 0.05 + 1e-6, name
        assert float(w.groupby(ctx.segments).sum().max()) <= ctx.cfg.max_segment_weight + 1e-6, name


def test_constructions_api_and_page(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from market_intel import auth
    from market_intel.entities import Company, EntityIndex
    from market_intel.server import create_app
    from market_intel.store import Store
    monkeypatch.setenv("MARKET_INTEL_NO_LLM", "1")
    monkeypatch.delenv("MARKET_INTEL_TOKEN", raising=False)
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "token")
    idx = EntityIndex([Company(ticker="MU", name="Micron", segment="Memory & Storage", aliases=[], weight=0.1, cik=723125)])
    c = TestClient(create_app(store=Store(tmp_path / "t.sqlite"), index=idx), client=("127.0.0.1", 50000))
    r = c.get("/constructions")
    assert r.status_code == 200
    for marker in ('id="nav"', 'id="banner"', 'id="hdr"', "statusBanner", "runChip", "Performance matrix", "Equity curves"):
        assert marker in r.text, marker
    assert "['/constructions','构建方法比较']" in c.get("/static/base.js").text
    j = c.get("/api/research/constructions").json()
    assert "available" in j and "run" in j
    if j.get("available"):
        assert set(j["registry"]) and "walk_forward" in j
    assert c.get("/api/research/constructions/status").json()["running"] is False
