# Methodology

## 1. Universe
`ai_semi_intel/config/universe.py` defines ~116 US-listed companies across 11 value-chain segments
(EDA & IP → equipment → foundry/packaging → compute → memory → interconnect/optics → analog/power →
servers & DC infrastructure → cloud/neocloud → AI software → power for AI). Key foreign issuers with
US ADRs (TSM, ASML, ARM, NXPI, UMC, NBIS, CAMT, NVMI, IREN) are included and flagged (`is_adr`).

## 2. Data
| Source | What we take | Module |
|---|---|---|
| SEC EDGAR | XBRL company facts (revenue, gross profit, R&D, operating income, net income, OCF, capex, SBC, buybacks, equity, cash, debt, EPS), 10-K/10-Q/20-F annual & quarterly series, filing index (alerts, links) | `data/edgar.py` |
| Yahoo Finance | 4y adjusted prices, annual/quarterly statements, consensus revenue & EPS (FY0/FY+1), EPS trend (7/30/60/90d), growth estimates, price targets, earnings calendar, FX | `data/yahoo.py` |
| FRED | DGS10/DGS2/T10Y2Y, FEDFUNDS, CPI, core PCE, UNRATE, GDP, INDPRO, semiconductor IP (IPG3344S) & PPI (PCU33443344), VIX, HY OAS, USD, capital-goods orders | `data/fred.py` |
| PitchBook | Optional: platform exports in `data_cache/pitchbook/` or licensed API hook | `data/pitchbook.py` |

All calls are cached with age thresholds (`config/settings.py`).

## 3. Feature engineering (`analysis/fundamentals.py`)
* **NTM alignment**: FY0/FY+1 consensus blended by the months left in the fiscal year (`fy0_weight`), with
  a check that Yahoo's "0y" is the first unreported year.
* **Currency**: statements/consensus in the reporting currency are converted at spot FX; share counts are
  implied from USD market cap / price so ADR ratios never distort per-share values; EV = market cap + net debt.
* **Through-cycle margins**: 5-year average FCF and operating margins from EDGAR annual facts.
* Growth (NTM, FY+1, 3y CAGR, quarterly YoY, 2y forward CAGR), profitability (GM/OpM/FCF/ROIC/ROE),
  balance sheet (net debt/EBITDA), capital intensity (capex, R&D, SBC), Rule of 40, revisions, momentum
  (1/3/6/12m, vs 200dma, 6m relative to SOXX), risk (vol, drawdown, beta).

## 4. Valuation (`analysis/valuation.py`)
* **DCF (FCFF, 10-year explicit)**: year-1 revenue = NTM consensus; year-2 growth = 60% of FY+1 consensus
  growth (cap 30%), linear fade to 3% terminal by year 8. FCF margin converges over 5 years to a target =
  60% current + 40% five-year average (85/15 for companies whose 3-yr revenue CAGR > 25%, since sub-scale
  history is not a through-cycle guide). WACC: CAPM with Blume-adjusted beta (0.7–1.4), ERP 4.5%,
  rf = 10Y Treasury, after-tax cost of debt; bounded 7.5–11%.
* **Reverse DCF**: post-consensus growth that equates DCF value to price, margins held at current.
* **Regression multiple**: ln(EV/S NTM) ~ NTM growth + FCF margin + gross margin across the universe;
  the residual is the mispricing signal, the fitted multiple gives an implied price.
* **Street target**: consensus mean target.
* **Blend**: 50% DCF, 30% regression, 20% target; methods outside 0.1x–5x of price are dropped and the
  weights renormalised. `upside_dispersion` and `n_methods` feed the portfolio view confidence.

## 5. Scoring & selection (`analysis/scoring.py`)
The live model exposes five decision pillars. `alpha_score` remains a point-in-time researched diagnostic.
`composite` directly combines growth, momentum, value, scarcity and risk resilience. Winsorised (5/95) z-scores;
pillar = mean of signed z's.
* Fundamental momentum 50%: growth acceleration (latest-quarter YoY minus trailing-twelve-month growth), latest-quarter
  YoY growth, forward acceleration (NTM consensus growth minus trailing growth), 30- and 90-day EPS revisions.
* Price momentum 35%: volatility-scaled 12-1 month momentum, segment (industry) 6-month momentum, 6-month return
  relative to SOXX.
* Growth-adjusted value 15%: regression residual of ln(EV/S NTM) on growth, FCF margin and gross margin (half
  universe-wide, half segment-relative). Every value definition tested at or below zero IC; the tilt reflects the
  mandate and is the first weight the live archive should re-test.
* Growth 37.5%: fundamental momentum from growth acceleration, latest-quarter growth, forward acceleration and revisions.
* Momentum 26.25%: volatility-scaled 12-1 momentum, segment momentum and relative strength.
* Value 11.25%: growth- and margin-adjusted multiple residual.
* Scarcity 15%: evidence-logged strategic assessments covering bottleneck control,
  lock-in, roadmap, AI exposure and pricing power. The supplied report's Table 6 provides low-confidence initial
  priors for 15 names; missing or stale assessments are neutral.
* Risk resilience 10%: 35% market resilience (volatility, drawdown, beta), 35% financial
  resilience (leverage, FCF margin, ROIC, through-cycle operating margin, SBC), and 30% valuation resilience
  (EV/Sales, P/E, terminal-value dependence, fair-value dispersion). Higher is safer; missing fields shrink to neutral.
* `composite` is the weighted sum of those five pillars. `alpha_score` separately reports the original
  50/35/15 growth/momentum/value combination. Scarcity and risk must earn validation from archived
  point-in-time assessments and forward returns, so the decision score remains `RESEARCH_PRIOR_NOT_VALIDATED`.
Eligibility: market cap >= $2B, >= 60% feature coverage, computable valuation. Top 15 chosen greedily by composite with
at most 3 names per segment. Style tag: Accelerating growth / Price momentum / Undervalued.

## 6. Industry structure (`analysis/industry.py`)
Segment size, growth, margins, multiples, median upside, revenue HHI (concentration) and per-company
positioning (revenue share, margin premium, growth premium, R&D intensity, moat score, 2x2 position).

## 7. Portfolio (`portfolio/optimizer.py`)
3y daily returns on a common observed window (no pre-IPO or peer-return backfill), Ledoit-Wolf covariance. **Recommended
construction = equal risk contribution** (2-15% per name, <= 35% per segment) **scaled to a 25% ex-ante volatility
target, remainder in cash** - the construction with the best Sharpe and drawdown in the 2012-2026 portfolio lab.
Expected returns for reporting = CAPM (realised beta x ERP) + Grinold alpha (IC 0.03 x volatility x standardised
score); fair-value upside is displayed but not used as a return forecast. Max-Sharpe (Grinold alphas), minimum
variance and equal weight are reported as references; Black-Litterman with upside views remains available
(`bl["mu_bl_upside"]`). Efficient frontier, risk contributions, and daily-rebalanced hindsight illustration vs SPY/QQQ/SOXX for new runs. Archived monthly studies retain their original frequency. The illustration reuses today's book and is invalid as a strategy track record; see [the daily execution policy](DAILY_REBALANCE.md).

## 7a. Canonical run and evidence state (`runs.py`, 2026-09-03 remediation)
Every pipeline execution is a *run* with an immutable `run_id`, a manifest (`outputs/latest/run.json`: data cut-off,
config / code / data-manifest hashes, field-level data health, validation state, canonical weights) and its own
directory under `outputs/history/runs/<run_id>/`; runs are never overwritten, a same-session rerun *supersedes* the
earlier one. The canonical portfolio is the ERC construction scaled to the 25% vol target: `snapshot.json["weights"]`
are **executable target weights** (assets + cash = 100%); the fully-invested model weights and the max-Sharpe
alternative are kept alongside. There is no live position ledger, so every page says *target*, never *held*.
Turnover is measured against the *approved prior run* (last approved run of an earlier session), cash included.
The decision score is labelled `NOT VALIDATED · exploratory` everywhere until the live forward archive delivers
12 months of forward returns (gates: PIT 3m HAC t >= 2, walk-forward t >= 2, >= 12 snapshots with 12m returns).
Runs whose inputs are stale or show recent price gaps are `NOT ACTIONABLE`; runs without prices or a risk-free rate
are blocked before analysis (`run_blocked.json`).

## 7b. Hindsight illustration (formerly "illustrative backtest")
`optimizer.backtest` replays *today's* names and weights over the return window: selection and weight look-ahead by
construction, no costs, fills, delistings or corporate actions. It is labelled **INVALID AS TRACK RECORD** on every
surface and reports the standard Sharpe (daily excess return over the fed funds path, annualised) next to the old
(CAGR - rf) / vol ratio (`cagr_excess_over_vol`), benchmarks on the same dates including day one, and the trading days
lost to missing prices. A valid re-validation needs a point-in-time universe (delistings, IPO timing, renames), data
as available at each decision date, t+1 execution with costs, a cash and corporate-action ledger, a historical cash-rate
path, frozen config/data/code hashes and an untouched hold-out; that is a separate research project.

## 8. Monitoring (`dashboard/monitoring.py`)
Run-over-run entries/exits, rank and weight changes, turnover versus the approved prior run (cash included); alert
rules on price moves, revisions, valuation thresholds ("analyst review", never trade instructions), drawdowns,
earnings dates and new SEC filings. History tables under `outputs/history/` are append-only with a `run_id` column.

## 9. Validation (`ai_semi_intel/research/`)
* **Point-in-time features** (`pit_features.py`): every XBRL fact is used only after its `filed` date; first-filed
  values are kept per period; Q4 = FY - (Q1+Q2+Q3); TTM = last four consecutive quarters; market cap = split-adjusted
  close x cover-page share count re-based with the split history. ADRs excluded (currency / ADR ratio).
* **Trailing-only score** (`backtest.py`): reconstructs the alpha score only (no consensus, revisions, targets,
  or historical evidence snapshots). It does not validate the scarcity/risk decision overlay.
* **Tests** (`ic_tests.py`): rank IC by horizon with Newey-West t-stats, ICIR, feature-level IC, quintile portfolios,
  top-15 backtest with 20bp costs, permutation test, random-pillar-weight test, leave-one-out, regime split
  (semiconductor output YoY, SOXX 12m), alpha vs SPY/SOXX and vs Fama-French 5 + momentum.
* **Live validation** (`live_ic.py`): forward IC of the archived daily universe scores as returns realise.
* **Known biases**: survivorship (today's constituents only), no delisted prices, IPO entry after four filed quarters.

## 10. Strategic role: scarcity and risk (`analysis/strategic.py`, `analysis/risk.py`)
Deliberate, evidence-logged judgment enters the decision score in two pillars (see docs/SCARCITY_RISK_SCORE.md and
docs/STRATEGIC_RUBRIC.md): a scarcity pillar (LLM-researched bottleneck control, lock-in, roadmap, AI exposure,
pricing power; confidence-shrunk; Table 6 priors as fallback) and a risk-resilience pillar (market, financial,
valuation resilience from data plus strategic resilience from the LLM's competitive, geopolitical, execution and
concentration assessments). A risk gate and a 5% weight cap protect the portfolio from high-risk names.
Assessments refresh on events (filings, price shocks, revisions, `market_intel` alerts) and every 90 days, with
change control that requires new dated evidence. Real-time monitoring is provided by the `market_intel` package
(docs/MARKET_INTEL.md).
