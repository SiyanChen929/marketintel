"""Synthetic constraint attacks; no market data or economic validation."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ai_semi_intel.config.settings import PortfolioConfig
from ai_semi_intel.portfolio import constructions as C, optimizer as O


def context(**config):
    tickers = [f"T{i:02}" for i in range(15)]
    rng = np.random.default_rng(822)
    returns = pd.DataFrame(rng.normal(0, .02, (90, 15)), columns=tickers,
                           index=pd.bdate_range("2026-01-02", periods=90))
    covariance = pd.DataFrame(np.eye(15) * .04 + np.ones((15, 15)) * .01, index=tickers, columns=tickers)
    caps = pd.Series(.15, index=tickers)
    caps.iloc[0] = .05
    cfg = PortfolioConfig(min_history_days=60, **config)
    return C.Ctx(cov=covariance, mu=pd.Series(np.linspace(.6, .08, 15), index=tickers), returns=returns,
                 caps=pd.Series(np.linspace(1e12, 1e9, 15), index=tickers), max_weights=caps,
                 segments=pd.Series([f"sector-{i // 3}" for i in range(15)], index=tickers), cfg=cfg)


@pytest.mark.parametrize("method", list(C.REGISTRY))
def test_all_13_methods_preserve_five_percent_special_cap_and_sector_limits(method):
    ctx = context()
    weights = C.build(method, ctx)
    assert np.isfinite(weights).all() and weights.sum() == pytest.approx(1., abs=1e-8)
    assert weights.iloc[0] <= .05 + 1e-8
    assert (weights >= ctx.cfg.min_weight - 1e-8).all()
    assert (weights <= ctx.max_weights + 1e-8).all()
    assert (weights.groupby(ctx.segments).sum() <= ctx.cfg.max_segment_weight + 1e-8).all()


@pytest.mark.parametrize("method", list(C.REGISTRY))
def test_fully_invested_special_cap_infeasibility_blocks_every_method(method):
    ctx = context()
    ctx.max_weights[:] = .05  # 15 * 5% can fund only 75% of the requested fully invested book.
    with pytest.raises(ValueError, match="infeasible"):
        C.build(method, ctx)


def test_sector_capacity_infeasibility_found_by_lp_even_when_simple_count_check_passes():
    ctx = context(min_weight=.06, max_segment_weight=.7)
    ctx.max_weights[:] = .15
    ctx.segments[:] = ["large-sector"] * 12 + ["small-sector"] * 3
    # Two sectors can nominally hold 140%; 12 mandatory minimums in one sector
    # already need 72%, so their requested 70% cap cannot be satisfied.
    with pytest.raises(ValueError, match="infeasible"):
        C.build("equal_weight", ctx)


def test_special_maximum_below_configured_minimum_must_block_not_relax_the_hard_cap():
    ctx = context(min_weight=.06)
    with pytest.raises(ValueError, match="(infeasible|cap|bound)"):
        C.build("equal_weight", ctx)


def test_indefinite_covariance_must_not_produce_an_executable_risk_target():
    ctx = context()
    ctx.cov[:] = -.01
    np.fill_diagonal(ctx.cov.values, .04)
    assert np.linalg.eigvalsh(ctx.cov.values).min() < 0
    with pytest.raises(ValueError, match="(covariance|semidefinite|variance)"):
        C.build("risk_parity", ctx)


def test_missing_sector_identity_cannot_silently_bypass_sector_caps():
    ctx = context()
    ctx.segments.iloc[0] = np.nan
    with pytest.raises(ValueError, match="(segment|sector|identity)"):
        C.build("max_sharpe", ctx)


def test_solver_failure_cannot_be_published_without_a_fallback_status(monkeypatch):
    ctx = context()
    def fail(fun, x0, **kwargs):
        return SimpleNamespace(success=False, x=np.array(x0), fun=float(fun(np.array(x0))), message="synthetic solver failure")
    monkeypatch.setattr(C, "minimize", fail)
    weights = C.build("max_sharpe", ctx)
    # Returning a feasible fallback can be a policy, but its method identity
    # and failure state must survive into the published construction artifact.
    assert weights.attrs.get("construction_status") == "fallback"
    assert weights.attrs.get("fallback_reason")


def portfolio_inputs():
    ctx = context()
    rng = np.random.default_rng(811)
    all_names = [*ctx.tickers, "SPY", "QQQ", "SOXX"]
    prices = pd.DataFrame(100 * np.cumprod(1 + rng.normal(.0002, .02, (91, len(all_names))), axis=0),
                          columns=all_names, index=pd.bdate_range("2026-01-02", periods=91))
    top = pd.DataFrame({"segment": ctx.segments, "n_methods": 3, "upside_dispersion": .2,
                        "market_cap": ctx.caps, "upside": .2, "alpha_score": np.linspace(-1, 1, 15),
                        "risk_capped": [True] + [False] * 14}, index=ctx.tickers)
    return top, prices


def test_valid_vol_target_scaling_preserves_all_upper_caps(monkeypatch):
    top, prices = portfolio_inputs()
    monkeypatch.setattr(O, "efficient_frontier", lambda *args, **kwargs: pd.DataFrame())
    result = O.run_portfolio(top, prices, .04, PortfolioConfig(min_history_days=60, vol_target=.01))
    target = result["opt"]["recommended"]
    assert 0 < target["scale"] <= 1
    assert target["weights"].iloc[0] <= .05 + 1e-8
    assert (target["weights"] >= 0).all() and target["cash"] >= 0
    assert target["weights"].sum() + target["cash"] == pytest.approx(1.)
    assert (target["weights"].groupby(top["segment"]).sum() <= .35 + 1e-8).all()


@pytest.mark.parametrize("vol_target", [-.1, float("nan")])
def test_invalid_vol_target_blocks_before_weights_can_be_scaled(monkeypatch, vol_target):
    top, prices = portfolio_inputs()
    monkeypatch.setattr(O, "efficient_frontier", lambda *args, **kwargs: pd.DataFrame())
    with pytest.raises(ValueError, match="(vol|target|finite|positive)"):
        O.run_portfolio(top, prices, .04, PortfolioConfig(min_history_days=60, vol_target=vol_target))
