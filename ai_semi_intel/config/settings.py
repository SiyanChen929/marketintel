"""Central configuration: paths, API keys, model parameters.

Everything tunable lives here so the daily pipeline, the deck builder and the
dashboard all read the same assumptions.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_CACHE = PROJECT_ROOT / "data_cache"
# AI_SEMI_OUTPUTS redirects every product (latest/history/charts/deck/dashboard) - used for sandbox reruns
OUTPUTS = Path(os.environ.get("AI_SEMI_OUTPUTS") or (PROJECT_ROOT / "outputs")).resolve()
LATEST = OUTPUTS / "latest"
HISTORY = OUTPUTS / "history"
CHARTS = OUTPUTS / "charts"
DECK = OUTPUTS / "deck"
DASHBOARD = OUTPUTS / "dashboard"

for _p in (DATA_CACHE, LATEST, HISTORY, CHARTS, DECK, DASHBOARD):
    _p.mkdir(parents=True, exist_ok=True)


def load_fred_key() -> str:
    """FRED key: env var first, then the key file in the project root."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        return key
    raise RuntimeError("Set FRED_API_KEY in your environment.")


# SEC requires a descriptive User-Agent with contact info.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Student Research siyan_chen@ucsb.edu")


@dataclass
class ScreenConfig:
    """Stock-selection parameters."""
    top_n: int = 15
    hysteresis: float = 1.0             # keep a current holding while rank <= top_n * hysteresis (1.0 = off; sweep: 1.5 cuts churn)
    max_per_segment: int = 3            # ensures end-to-end value-chain coverage
    min_market_cap: float = 2e9         # USD, liquidity floor
    min_feature_coverage: float = 0.60  # share of scoring features that must be non-null
    # five-pillar decision score (must sum to 1). Growth/momentum/value retain the
    # same relative 50/35/15 mix as the researched alpha score, scaled to 75%.
    w_growth: float = 0.375
    w_momentum: float = 0.2625
    w_value: float = 0.1125
    w_scarcity: float = 0.15
    w_risk: float = 0.10
    # separately reported alpha diagnostic; these weights must sum to 1.
    alpha_w_growth: float = 0.50
    alpha_w_momentum: float = 0.35
    alpha_w_value: float = 0.15
    # real-time event overlay (market_intel): 0 = off; composite += w_event * clip(event_score_7d, -2, 2)
    w_event: float = 0.0
    event_overlay_max_age_hours: float = 36.0
    w_quality: float = 0.0
    winsor_pct: float = 0.05


@dataclass
class ValuationConfig:
    """DCF & multiple-regression assumptions."""
    equity_risk_premium: float = 0.045  # Damodaran-style implied ERP
    terminal_growth: float = 0.030
    tax_rate: float = 0.15
    projection_years: int = 8           # growth fades to terminal by this year
    fade_years: int = 10                # explicit forecast horizon
    wacc_floor: float = 0.075
    wacc_cap: float = 0.110
    beta_floor: float = 0.7
    beta_cap: float = 1.4               # applied after Blume adjustment (0.67*raw + 0.33)
    post_consensus_growth_share: float = 0.60   # year-2 growth = this x FY+1 consensus growth
    max_growth_input: float = 0.30      # cap on post-consensus growth fed to DCF
    margin_weight_current: float = 0.60 # target FCF margin = w*current + (1-w)*5-yr average
    # blended fair value weights
    w_dcf: float = 0.50
    w_multiple: float = 0.30
    w_target: float = 0.20


@dataclass
class PortfolioConfig:
    """Optimizer constraints."""
    lookback_years: int = 3
    min_history_days: int = 200
    min_weight: float = 0.02
    max_weight: float = 0.15
    max_segment_weight: float = 0.35
    primary: str = "risk_parity"        # recommended construction: equal risk contribution (portfolio lab, Sep 2026)
    vol_target: float = 0.25            # ex-ante volatility target; remainder held in cash
    grinold_ic: float = 0.03            # alpha_i = IC x sigma_i x score_z for the max-Sharpe alternative
    risk_aversion: float = 2.5          # delta for Black-Litterman equilibrium returns
    bl_tau: float = 0.05
    view_horizon_years: float = 2.0     # fair-value upside realised over this horizon
    view_clip: tuple = (-0.20, 0.40)    # clip of annualised view returns
    benchmarks: tuple = ("SPY", "QQQ", "SOXX")
    rebalance: str = "D"                # active daily policy; archived monthly studies retain their original protocol
    execution_policy_version: str = "daily_next_open_v1"
    execution_window_minutes: int = 5   # first regular-session quotes; never assume an opening-auction fill


@dataclass
class Config:
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    valuation: ValuationConfig = field(default_factory=ValuationConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    price_history_years: int = 4
    strategic_max_refresh_per_run: int = 12   # LLM re-assessments per daily run (cost control)
    yahoo_cache_hours: float = 12.0
    edgar_cache_days: int = 3
    fred_cache_hours: float = 12.0


CONFIG = Config()

# FRED macro panel used for the macro backdrop and the discount rate.
FRED_SERIES = {
    "DGS10": "10Y Treasury Yield (%)",
    "DGS2": "2Y Treasury Yield (%)",
    "T10Y2Y": "10Y-2Y Spread (%)",
    "FEDFUNDS": "Fed Funds Effective Rate (%)",
    "CPIAUCSL": "CPI (Index)",
    "PCEPILFE": "Core PCE Price Index",
    "UNRATE": "Unemployment Rate (%)",
    "GDPC1": "Real GDP (Bn 2017$)",
    "INDPRO": "Industrial Production Index",
    "IPG3344S": "IP: Semiconductors & Electronic Components",
    "PCU33443344": "PPI: Semiconductors & Electronic Components",
    "VIXCLS": "VIX",
    "BAMLH0A0HYM2": "HY OAS Spread (%)",
    "DTWEXBGS": "Trade-Weighted USD Index",
    "AMTMNO": "Manufacturers New Orders ($M)",
    "NEWORDER": "Core Capital Goods New Orders ($M)",
}
