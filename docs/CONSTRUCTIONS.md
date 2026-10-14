# Portfolio construction comparison

`ai_semi_intel/portfolio/constructions.py` (13 methods), `construction_eval.py` (walk-forward + performance
matrix), `scripts/run_constructions.py`, the main `/portfolio` selector, comparison page `/constructions`,
Streamlit dashboard, and `tests/test_bl.py`.

Risk parity was a config default (`PortfolioConfig.primary`), never a decision. This module makes the choice
an evidence question: every method sees the **same universe, covariance, expected returns, constraints and
rebalance dates**, so the spread between equity curves is the construction and nothing else.

## The methods

| Method | Family | Reads E[r] | Note |
|---|---|:--:|---|
| Equal weight | risk-only | no | 1/N, the null hypothesis |
| Cap weight | risk-only | no | market-cap proportional within the selection |
| Inverse volatility | risk-only | no | risk parity's diagonal-only approximation |
| Risk parity (ERC) | risk-only | no | **the production default** |
| Segment risk parity | risk-only | no | equal risk budget per value-chain segment |
| Minimum variance | risk-only | no | lowest ex-ante variance |
| Max diversification | risk-only | no | Choueifaty: maximise (w'σ)/√(w'Σw) |
| Hierarchical risk parity | risk-only | no | López de Prado: correlation clustering, no matrix inversion |
| Minimum CVaR (95%) | risk-only | no | Rockafellar-Uryasev LP on the loss tail |
| Max Sharpe | return-aware | **yes** | tangency portfolio |
| Mean-variance / Black-Litterman | return-aware | **yes** | BL posterior + mean-variance utility |
| Kelly (log-optimal) | return-aware | **yes** | utility at δ = 1 |
| Resampled max Sharpe | return-aware | **yes** | Michaud: average over estimation error |

The family label is not a claim, it is **measured**: `constructions.reads_mu()` rebuilds each method on two
very different expected-return vectors with the covariance held fixed and reports the turnover between them.
Every risk-only method returns exactly `0.000`; the test suite asserts it.

## Walk-forward results

164 monthly rebalances, Feb 2013 → Sep 2026. Each month end: top 15 names by 12-1 momentum, 2-year
Ledoit-Wolf covariance, Black-Litterman posterior from point-in-time momentum views, 15% weight cap, net of
10 bps one-way costs.

| Rank | Method | CAGR | Vol | Sharpe | Max DD | Turnover/mo | Eff. N |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | Max diversification | 45.4% | 38.8% | **1.093** | -44.9% | 20.6% | 9.5 |
| 2 | Mean-variance utility | 45.8% | 39.4% | 1.087 | -47.7% | 24.6% | 7.7 |
| 3 | Kelly | 42.1% | 36.4% | 1.076 | -43.8% | 23.4% | 8.0 |
| 4 | Hierarchical risk parity | 40.4% | 36.2% | 1.033 | -43.6% | 19.4% | 12.3 |
| 5 | Max Sharpe | 46.2% | 41.9% | 1.030 | -48.7% | 25.1% | 7.2 |
| 6 | Cap weight | 40.9% | 37.4% | 1.013 | -44.8% | 16.7% | 11.3 |
| 7 | Minimum CVaR | 38.3% | 35.3% | 0.999 | -40.3% | 26.7% | 7.7 |
| **8** | **Risk parity (production)** | 39.7% | 37.1% | **0.988** | -45.2% | 16.7% | 13.8 |
| 9 | Minimum variance | 36.9% | 34.6% | 0.979 | -39.7% | 24.1% | 8.3 |
| 10 | Inverse volatility | 39.2% | 37.2% | 0.973 | -44.9% | 15.3% | 14.1 |
| 11 | Equal weight | 40.5% | 38.8% | 0.966 | -46.5% | 12.6% | 15.0 |
| 12 | Resampled max Sharpe | 38.0% | 37.7% | 0.929 | -44.6% | 27.3% | 12.7 |
| 13 | Segment risk parity | 37.8% | 37.8% | 0.920 | -46.9% | 22.5% | 13.1 |
| — | SOXX | 28.7% | 31.6% | 0.813 | -45.8% | — | — |

### What this does and does not support

**Does not support a method change on its own.** Against equal weight, the largest Newey-West t is 1.68
(max Sharpe); max diversification 1.59, mean-variance 1.32. **Nothing clears |t| ≥ 2**, and the whole
best-to-worst Sharpe spread is 0.17. Rank order here is a weak preference, not evidence.

**Does support dropping risk parity as an unexamined default.** It ranks 8th of 13, below equal weight on
CAGR and below seven methods on Sharpe, while its one real advantage - the highest effective breadth (13.8
names) and low turnover (16.7%) - is matched by inverse volatility at a fraction of the complexity.

**Updates the Black-Litterman verdict.** Mean-variance utility, BL's native construction, ranks 2nd. The
earlier "BL is not validated" conclusion was measured against risk parity, which cannot read expected returns
at all; on its own construction BL sits near the top of the table, though still short of significance.

**Survivorship applies to every arm.** The selection universe is today's constituents, so all 13 methods beat
SOXX by construction. Only the spread between methods is meaningful.

## The projection bug this surfaced

Analytic methods (cap weight, inverse volatility, HRP) produce raw weights that can breach the per-name cap.
Clip-then-renormalise does not fix it: dividing by the new sum pushes untouched weights back through the cap.
With one name holding 90% of the universe's market cap against a 15% limit, cap weighting returned **16.1%**.
`_norm()` now projects raw weights onto the common per-name and per-segment constraint set, and the tests
stress it against that pathological case.

The main Portfolio page stores a separate weight, risk-contribution, KPI, cumulative-return and drawdown
series for every method. Changing the selector redraws all of them; the canonical executable target remains
ERC with the volatility overlay until a separate production decision changes `PortfolioConfig.primary`.


## Decomposition

A single 13-year Sharpe hides everything a researcher needs to judge a method. The panel therefore splits every
method's record four ways.

**Calendar years** (heatmap + table). Max diversification's lead is not steady: it beat risk parity by 14pp in
2013, 38pp in 2020 and 63pp in 2023, but *lost* by 11pp in 2014 and 5pp in 2021. An edge concentrated in a few
years is a weaker claim than the headline Sharpe suggests.

**Market regimes**, labelled from the benchmark's own path with no lookahead (drawdown state from the running
high; volatility state from trailing 60-day realised vol against its expanding median).

| Regime | Share of time | Max diversification | Risk parity | Equal weight |
|---|---:|---:|---:|---:|
| SOXX near highs | 69% | 2.736 | 2.769 | 2.715 |
| SOXX correction | 19% | -0.12 | -0.348 | -0.365 |
| SOXX bear | 12% | -0.819 | -0.81 | -0.763 |
| high volatility | 75% | 0.956 | 0.863 | 0.821 |
| low volatility | 20% | 2.035 | 2.015 | 2.119 |

This is the most useful split: **risk parity is marginally the better book while the sector is near its highs**
(69% of the time), and loses only in corrections and high-volatility stretches. Max diversification's advantage
is concentrated in exactly the periods that hurt, which is a more defensible reason to prefer it than its
average Sharpe.

**Drawdown episodes** per method: start, trough, depth, length and whether it recovered. Risk parity's three
worst are -45.2% (Jan-Apr 2025, recovered in 104 days), -41.4% (Nov 2021-Oct 2022, 271 days) and an **ongoing**
-39.9% since June 2026.

**Return contribution** per holding and per segment: weight x realised return summed over every holding month,
with average weight, months held and hit rate. For max diversification the top five names are SMCI (7.2%), BE (5.0%), MOD (4.6%), APP (4.6%), AMD (3.9%), and the
top five together are only 25% of total contribution across 103 names
touched - a broad book rather than a few lucky picks.

**Consistency**, the average yearly rank across methods:

| Method | Avg rank | Top-3 years | Bottom-3 years |
|---|---:|---:|---:|
| Max Sharpe | 6.07 | 6 | 3 |
| Max diversification | 6.57 | 5 | 2 |
| Cap weight | 6.79 | 5 | 3 |
| Kelly (log-optimal) | 6.86 | 4 | 2 |
| Mean-variance / Black-Litterman | 7.0 | 6 | 4 |


## Period windows and selection

The panel re-cuts every record over standard horizons - 3M, 6M, YTD, 1Y, 3Y, 5Y and since inception - the way
charting software does. Metrics are recomputed from the daily wealth path for the chosen window (rebased to 100
at the window start), not sliced from precomputed totals, so CAGR, volatility, Sharpe, Sortino, max drawdown and
Calmar all refer to that window. Horizons longer than the available record are greyed out rather than silently
clamped.

This matters: over the full window max diversification leads on Sharpe (1.09), but **over the last year minimum
CVaR leads (2.66) and max diversification is mid-table**. Method ranking is horizon-dependent, which a single
since-inception number hides completely.

Selection controls sit above the curves: **Select all**, **Clear**, **Risk-only**, **Return-aware** and **Top 5
by Sharpe**, plus click-to-toggle on any legend entry. The window metrics table below the chart follows the
selection.

The same period bar is on the Portfolio replay chart (`/portfolio`), where it exposes the same effect: the
executable target book shows +129% since inception but **-2.1% over the last three months** (Sharpe -0.38).

## Reproducibility

Every run stamps an `inputs` fingerprint (price panel hash and last date, universe hash, total market cap).
The walk-forward is deterministic given fixed inputs - verified by running it twice - but the daily pipeline
rewrites `universe_scores.csv`, and market caps feed both the equilibrium prior and cap weighting, so two runs
of identical code on different days legitimately differ. The fingerprint makes that visible instead of leaving
it to look like solver noise.

## Navigation

Portfolio is now one section with three views: **Current book** (`/portfolio`), **Construction comparison**
(`/constructions`) and **Black-Litterman** (`/bl`), joined by a sub-tab bar.

## Commands

```bash
python scripts/run_constructions.py                       # all 13, ~3 min
python scripts/run_constructions.py --no-bl-views         # equilibrium prior only
python scripts/run_constructions.py --methods risk_parity mean_variance hrp
```

Outputs `outputs/latest/constructions_walkforward.json` and `constructions_stats.csv`. Panel `/constructions`
shows equity curves (click the legend to isolate), drawdown, rolling 1-year Sharpe, risk-return and
turnover-Sharpe scatters, the full performance matrix (Sortino, Calmar, CVaR95, worst month, β, IR, turnover,
cost drag, effective N) and the significance bar chart. API: `GET /api/research/constructions`,
`POST /api/research/constructions/run`.

Research tooling for discussion only; not investment advice.
