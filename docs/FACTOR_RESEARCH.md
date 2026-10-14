# Factor construction and portfolio optimisation research

*Point-in-time study, 2 September 2026. Code: `ai_semi_intel/research/factor_lab.py`, `portfolio_lab.py`; outputs in
`outputs/validation/factor_research/`.*

## 1. Question
The documented composite (value 40 / growth 30 / quality 20 / momentum 10) had a **negative** point-in-time rank IC
(3m −0.02, 12m −0.08, t −3.1) over 2012–2026. Which factor definitions do predict returns inside the AI/semiconductor
value chain, how should they be combined, and how should the resulting list be turned into a portfolio?

## 2. Method
* **Panel.** 105 US filers (ADRs excluded), 176 month-ends Jan 2012–Aug 2026, 13,228 ticker-months. Every SEC XBRL
  fact is used only after its filing date; first-filed values; Q4 = FY − 9M; TTM aggregates; market cap = split-rebased
  cover-page shares × price. Consensus fields are not reconstructable and are absent.
* **Pre-registration.** 36 candidate features across value, growth, quality, momentum, size and risk, each with a fixed
  sign and rationale (`factor_lab.CANDIDATES`), evaluated identically: rank IC at 1/3/6/12 months, Newey-West t,
  first/second-half ICs (split June 2019), IC after excluding the ten largest winners (survivorship proxy), and IC after
  size neutralisation.
* **Selection rules fixed in advance.** Strict: 3m IC > 0 with t ≥ 2, positive in both halves, positive ex-winners.
  Lenient: t ≥ 1.5 and positive in both halves.
* **Nested test.** Features selected on the first half only; the equal-weight composite evaluated on the second half only.
* **Portfolio lab.** Monthly top-15 (segment cap 3) from each composite; risk from the trailing 3-year Ledoit-Wolf
  covariance known at each date; equal weight, inverse-vol, minimum variance, equal risk contribution (ERC), max-Sharpe
  with Grinold alphas (α = IC·σ·z, IC = 0.03); each with and without a 25% ex-ante volatility target; 20bp costs.

## 3. Factor zoo results (3-month IC, HAC t)
| Feature | Family | IC 3m | t | IC H1 | IC H2 | IC ex-winners | Verdict |
|---|---|---|---|---|---|---|---|
| growth_accel (quarter YoY − TTM growth) | growth | +0.033 | 2.33 | +0.040 | +0.025 | +0.016 | **strict** |
| rev_q_yoy (latest-quarter growth) | growth | +0.031 | 1.72 | +0.039 | +0.023 | +0.013 | lenient |
| mom_12_1_volscaled | momentum | +0.035 | 1.62 | +0.035 | +0.035 | +0.017 | lenient (12m IC +0.060, t 2.2) |
| mom_12_1 | momentum | +0.027 | 1.31 | +0.029 | +0.025 | +0.007 | |
| industry_mom (segment 6m return) | momentum | +0.024 | 1.07 | +0.019 | +0.029 | +0.009 | best 1m IC (+0.024) |
| high_52w_prox, vs_200dma, rev_cagr_3y | | +0.017 to +0.019 | < 1 | | | | |
| accruals (−) | quality | +0.010 | 0.55 | +0.034 | −0.015 | | unstable |
| gross/operating/FCF margin, ROIC, Rule of 40 | quality | ≈ 0 | | | | | no information |
| mult_residual (growth-adjusted EV/S) | value | −0.020 | −1.10 | −0.020 | −0.020 | +0.004 | |
| ev_sales_own_hist (own-history value) | value | −0.020 | −1.10 | | | | |
| garp (cheap × accelerating) | value | +0.002 | 0.14 | | | | |
| fcf_yield, earnings_yield, ev_sales_to_growth | value | −0.026 to −0.034 | −1.1 to −1.6 | | | | negative |
| log_mcap (small − large) | size | −0.023 | −0.98 | | | | large won |
| vol_1y (low vol) | risk | −0.014 | −0.52 | | | | high vol won |

Full table: `factor_zoo.csv`; chart: `charts/factor_zoo.png`, `charts/split_half.png`.

**Reading.** In this universe and period, no definition of value predicts returns: raw multiples, growth- and
margin-adjusted multiples, a company's own multiple history and a GARP interaction all sit at or below zero.
Quality levels carry no information. The signal is in *change*: growth acceleration (the quarter's growth versus its
trailing trend) and volatility-scaled price momentum, with segment momentum adding at the one-month horizon. With
36 candidates the expected maximum |t| under the null is about 2.3, so even the best feature is only marginally
significant on its own; the nested test is the honest check.

## 4. Composites
| Composite | IC 1m | IC 3m (t) | IC 6m | IC 12m | IC H1 / H2 | Top-15 CAGR | Sharpe | Max DD | Q5−Q1 p.a. | Hit rate |
|---|---|---|---|---|---|---|---|---|---|---|
| Documented 40/30/20/10 | −0.006 | −0.022 (−1.3) | −0.041 | −0.083 | −0.045 / +0.004 | 28.9% | 0.99 | −39% | −1.9% | 50% |
| growth_accel only (strict) | +0.018 | +0.033 (2.4) | +0.041 | +0.024 | +0.040 / +0.025 | 33.5% | 1.12 | −31% | +7.1% | 61% |
| Lenient EW (3 features) | +0.021 | +0.035 (2.1) | +0.047 | +0.030 | +0.037 / +0.034 | 36.0% | 1.13 | −36% | +15.1% | 61% |
| Momentum only | +0.027 | +0.033 (1.5) | +0.040 | +0.037 | +0.025 / +0.042 | 35.3% | 1.11 | −33% | +11.2% | 54% |
| **Fundamental + price momentum** | **+0.027** | **+0.039 (2.3)** | **+0.049** | +0.029 | +0.035 / +0.043 | **36.0%** | 1.10 | −31% | **+17.5%** | 61% |

Universe equal-weight CAGR 26.1%, SOXX 26.9%. **Nested out-of-sample:** features selected on 2012–Jun 2019 alone
(strict rule → growth_accel) give a second-half IC of +0.025 (3m, t 1.3) and +0.059 (12m, t 2.1) over 83 months.
Walk-forward family weights (growth vs momentum, shrunk 50% to equal) give an OOS IC of +0.031 (t 1.5) with mean
weights 59/41. Chart: `charts/composite_comparison.png`.

## 5. Portfolio construction (fundamental + price momentum top-15, 2012–2026, 20bp costs)
| Method | CAGR | Vol | Sharpe | Max DD | Calmar | Turnover |
|---|---|---|---|---|---|---|
| Equal weight | 35.3% | 31.0% | 1.08 | −31.5% | 1.12 | 14% |
| Inverse volatility | 34.5% | 28.9% | 1.13 | −29.0% | 1.19 | 15% |
| Minimum variance (2–15%) | 30.8% | 25.8% | 1.12 | −24.6% | 1.26 | 19% |
| Equal risk contribution | 34.8% | 29.1% | 1.13 | −29.1% | 1.20 | 16% |
| Max Sharpe, Grinold alphas | 39.4% | 33.5% | 1.12 | −30.1% | 1.31 | 25% |
| Equal weight, 25% vol target | 28.6% | 22.7% | 1.17 | −23.0% | 1.24 | |
| Inverse vol, 25% vol target | 29.0% | 22.2% | 1.22 | −20.9% | 1.39 | |
| Minimum variance, 25% vol target | 27.9% | 21.5% | 1.20 | −19.7% | 1.41 | |
| **ERC, 25% vol target** | 29.4% | 22.4% | **1.22** | −21.4% | 1.37 | |
| Max Sharpe (Grinold), 25% vol target | 30.1% | 23.5% | 1.19 | −28.0% | 1.07 | |

Construction cannot manufacture alpha (Sharpe 1.08–1.13 fully invested across methods) but the volatility target
reliably improves Sharpe and cuts the maximum drawdown by roughly ten points. Chart: `charts/portfolio_lab.png`.

## 6. Decisions implemented in the live model
1. **Score** = 50% fundamental momentum (growth acceleration, latest-quarter growth, and their consensus analogues:
   forward acceleration, 30/90-day EPS revisions) + 35% price momentum (vol-scaled 12-1, segment momentum, 6-month
   relative strength) + 15% growth-adjusted valuation (regression residual). Quality is reported as risk flags, not scored.
   The 15% value tilt reflects the "undervalued" mandate and has no evidential support; it is the first weight the
   live archive should re-test.
2. **Portfolio** = equal risk contribution on the Ledoit-Wolf covariance, 2–15% per name, ≤35% per segment, scaled to
   a 25% ex-ante volatility target with the remainder in cash. Expected returns for reporting = CAPM + Grinold alpha
   from the score; fair-value upside is shown as analyst information, not used as a return forecast.
3. **Validation** re-run with the new pillar definitions (`scripts/run_validation.py`); the PIT mirror uses the
   trailing analogues only, so it remains a lower bound on the live model.
4. **Five-pillar decision score (added 2 September 2026).** The live score is 37.5% growth, 26.25% momentum,
   11.25% value, 15% evidence-weighted scarcity, and 10% risk resilience. The first three weights preserve the
   alpha score's 50/35/15 relative mix. No historical point-in-time scarcity assessments exist,
   so the published 2012-2026 results validate only `alpha_score`. The combined decision score is
   `RESEARCH_PRIOR_NOT_VALIDATED` until the daily archive supports forward IC and turnover analysis.

## 7. Caveats
Survivorship (today's constituents; the excluded-winners test is a proxy, not a cure); no delisted prices; consensus
features untested until the daily archive accumulates 6–12 months; ICs of 0.03–0.04 are small and regime-dependent
(2016 and 2018 were negative years for every composite); multiple-testing risk across 36 candidates. The strategy
should be rejected if the live forward IC from the archive is not positive after twelve months.

## 8. Portfolio hyperparameter sweep and walk-forward tuning (2 September 2026)

Code: `ai_semi_intel/research/portfolio_sweep.py`, `scripts/run_portfolio_sweep.py`; outputs in
`outputs/validation/portfolio_sweep/` (grid_results.csv, walk_forward_*.csv, cost_sensitivity.csv, charts/).

**Grid.** 1,008 configurations = 7 weighting methods (equal, inverse-vol, minimum variance, equal risk contribution,
max-Sharpe on Grinold alphas at IC 0.02/0.03/0.05) x 2 weight caps (10%, 15%) x 2 covariance lookbacks (504, 756 days)
x 3 holding counts (10, 15, 20; segment cap 3) x 2 hysteresis bands (none; keep a holding while rank <= 1.5N) x 6
volatility targets (none, 15-35%). Point-in-time selection on the alpha score, trailing Ledoit-Wolf covariance,
monthly holding, 20bp per unit of one-way turnover, Sharpe with rf 2%.

**In-sample ranking.** Best: minimum variance, 10 holdings, 30% target, 756-day covariance, hysteresis 1.5 -
Sharpe 1.33, CAGR 34.9%, max drawdown -25%. Current default (ERC, 15 holdings, 25% target): Sharpe 1.20, CAGR 29.6%,
drawdown -22.5%. The whole grid spans Sharpe 0.99-1.33 (mean 1.13, sd 0.06): construction moves Sharpe by tenths.

| Parameter | Mean Sharpe by level (full / H1 / H2) | Reading |
|---|---|---|
| Holdings | 10: 1.20 / 1.15 / 1.31 · 15: 1.13 / 1.11 / 1.22 · 20: 1.07 / 1.10 / 1.12 | consistent in both halves; the one robust effect |
| Vol target | none 1.11 · 15% 1.09 · 20% 1.14 · 25% 1.16 · 30% 1.15 · 35% 1.15 | Sharpe gain modest and mixed by half; drawdown falls monotonically with the target (-32% none -> -18% at 15%) |
| Method | equal 1.09 · ERC 1.12 · inv-vol 1.13 · min-var 1.12 · max-Sharpe 1.15 | within noise; max-Sharpe leads H1, min-var/inv-vol lead H2 |
| Hysteresis 1.5 | 1.12 vs 1.15 without | costs ~0.03 Sharpe at 20bp, cuts turnover 14% -> 12%, wins at 40-80bp |
| Weight cap, lookback | 1.13 vs 1.13; 1.13 vs 1.14 | irrelevant |

The three Grinold IC levels give identical results because the max-Sharpe objective (no rf term) is scale-invariant in
the alphas; the IC only matters relative to a risk-free rate.

**Walk-forward tuning (the honest test).** From 2016, each year applies the configuration with the best trailing
net Sharpe over all prior months. Out of sample: tuned-each-year Sharpe **1.20** (CAGR 44.7%, drawdown -32%),
fixed default **1.33** (35.8%, -22%), equal weight 1.19, and the in-sample best applied with look-ahead 1.64.
The tuner switched between max-Sharpe and other variants, mostly without a volatility target, and chased noise;
annual re-tuning was worse than never tuning. Configuration Sharpe persists only moderately across halves
(Spearman 0.51); the top-50 configurations of the first half averaged 1.28 in the second half, no better than
the default's 1.29.

**Cost sensitivity.** The top configurations fall from 1.37 (0bp) to 1.18-1.22 (80bp); hysteresis variants hold up
best at high costs. **Neighbourhood stability.** Configurations sharing the in-sample winner's method and target
average 1.14 (range 0.99-1.33), so the winner's edge is not robust to its other settings.

**Decision.** Keep ERC with a 25% volatility target as the production default: it is inside the top band on every
robust dimension, has the lowest drawdown of the strong configurations, and beat the tuned policy out of sample.
Two options are exposed as parameters rather than adopted: `hysteresis` (recommended 1.5 for the daily-monitored live
list, where churn is costlier than in a monthly backtest) and `top_n` = 10 (higher Sharpe in both halves, but more
name-specific risk and a mandate change; the effect may partly reflect survivorship in the universe).

### 8.1 Robust selection rules (follow-up, 2 September 2026)

Code: `portfolio_sweep.walk_forward_rule`, `scripts/run_robust_selection.py`; outputs `robust_selection_results.csv`,
`robust_selection_tests.csv`, `robust_selection_choices.json`, `robust_selection_oos.csv`, `charts/robust_selection.png`.

The naive tuner above loses to the fixed default because it picks the maximum of 1,008 noisy trailing Sharpes. Two
remedies were tested on the same yearly expanding-window protocol (OOS 2016-01 to 2026-07, 127 months): restrict the
choice to economic levers (the 18 ERC configurations that vary only holdings count and volatility target), and replace
the maximum with a robust statistic. Rules: max trailing Sharpe; median trailing Sharpe of the configuration's
one-step neighbourhood; maximin over the two halves of the training window; sticky (keep the incumbent, starting from
the default, unless a challenger leads by 0.05 or 0.10 Sharpe); sticky on neighbourhood medians; equal model averaging
of all configurations; softmax averaging (temperature 20). Each is compared with the fixed default on the same months
by a paired test (mean monthly difference; iid bootstrap of the Sharpe difference, 5,000 draws).

| Rule | Full grid Sharpe / vol / DD / switches | Restricted grid Sharpe / vol / DD / switches |
|---|---|---|
| Max trailing Sharpe | 1.20 / 36% / -32% / 9 | 1.32 / 28% / -31% / 6 |
| Neighbourhood median | 1.13 / 30% / -31% / 8 | 1.26 / 30% / -30% / 5 |
| Maximin over halves | 1.18 / 31% / -31% / 10 | 1.30 / 31% / -31% / 6 |
| Sticky, +0.05 margin | 1.33 / 37% / -32% / 2 | 1.30 / 27% / -31% / 2 |
| Sticky, +0.10 margin | 1.33 / 37% / -32% / 2 | 1.33 / 25% / -22% / 0 (= default) |
| Sticky on neighbourhood median | 1.32 / 30% / -23% / 2 | 1.33 / 29% / -27% / 0 (30% target) |
| Model averaging (equal) | **1.35** / 25% / -22% / - | 1.33 / 25% / -24% / - |
| Softmax averaging | 1.30 / 29% / -26% / 9 | 1.33 / 28% / -28% / 6 |
| Fixed default (ERC, 15, 25%) | 1.33 / 25% / -22% / 0 | same |

Switches are counted after the first-year choice. Findings:

- **Restricting the levers removes the winner's curse but adds nothing.** On the 18-configuration grid the plain
  max rule recovers from 1.20 to 1.32 Sharpe, statistically indistinguishable from the default (bootstrap Sharpe
  difference -0.01, 95% CI -0.12 to +0.11). Its choices alternate between 10 and 15 names and between no target and
  30-35%, and each switch costs a little.
- **Robust statistics do not rescue the full grid.** Neighbourhood median (1.13) and maximin (1.18) still follow the
  max-Sharpe-on-Grinold-alphas family with no volatility target that dominated 2012-2016, then trail it.
- **Sticky rules converge to the default or to its neighbour.** With a 0.10 margin the restricted rule never leaves
  the default; the neighbourhood-median variant moves once, to the 30% target, and stays there (Sharpe 1.33, higher
  volatility and drawdown). On the full grid the sticky rules lock onto max-Sharpe with 10 names and no target from
  2017: the same Sharpe as the default at 37% volatility and a -32% drawdown, i.e. the default with leverage.
- **Model averaging is the only rule with a positive point estimate,** +0.016 Sharpe over the default (95% CI -0.08
  to +0.12; probability of beating the default 0.62). The average of 1,008 weight vectors is itself a diversified,
  volatility-targeted portfolio with the default's risk profile, so the gain is the ensemble's variance reduction,
  not information about which configuration is better. It is not significant and not worth the operational cost
  (1,008 optimisations per rebalance).
- Positive paired t-statistics on mean return (up to 2.9 for the sticky rules) come from rules that selected
  configurations without a volatility target; they earn more only by running more risk, which is why the
  comparison is made on Sharpe.

**Decision unchanged.** The fixed default sits at the ceiling of what yearly re-tuning can deliver on this sample:
no rule beats it with any confidence, and every rule that switches does worse or takes more risk for the same
Sharpe. Production keeps ERC, 15 names, 25% volatility target; construction hyperparameters are not re-tuned on
trailing performance. Re-run `scripts/run_robust_selection.py` after the sweep is refreshed if a future sample
shows configuration persistence materially above the 0.51 Spearman measured here.
