# Black-Litterman construction and evaluation

`ai_semi_intel/portfolio/bl.py` (engine), `bl_eval.py` (evaluation), `scripts/run_bl.py` (runner),
`/bl` on the monitor panel, `/api/research/bl*` (API), `tests/test_bl.py`.

## Why this exists separately

`optimizer.black_litterman()` already built a BL posterior, but `run_portfolio` overwrote it
(`bl["mu_bl_upside"] = bl["mu"]; bl["mu"] = mu`) with Grinold alphas and optimised those instead. The
`exp_return_bl` column on the portfolio page was therefore the *Grinold* number, not a BL one. This module
keeps BL as a first-class construction, evaluates it honestly, and reports what it can and cannot support.

## The finding that matters most

**The production construction cannot use Black-Litterman at all.** `PortfolioConfig.primary` is
`risk_parity`, which allocates purely from the covariance matrix and never reads expected returns. Measured
directly (covariance held fixed, expected returns tripled) the risk-parity weights move by **exactly 0.0**.
The views move expected returns by ~430-1000 bps and change the risk-parity book by ~0.03%, all of which
comes from the posterior covariance, not the views.

So adopting BL is a **construction decision, not a parameter change**: `primary` must become a
mu-consuming optimiser (`bl.mean_variance_weights`, the natural BL choice, or `max_sharpe`). Doing that
costs **37% turnover** and cuts effective breadth from 14.0 names to 8.5.

## Engine

```
π  = δ · Σ · w_mkt                                    equilibrium prior (reverse optimisation)
μ̄  = π + τΣPᵗ(PτΣPᵗ + Ω)⁻¹(q − Pπ)                    He-Litterman posterior returns
Σ̄  = Σ + [τΣ − τΣPᵗ(PτΣPᵗ + Ω)⁻¹PτΣ]                  posterior covariance (parameter uncertainty)
```

Solved with `np.linalg.solve` (no explicit inverses). Covariance is Ledoit-Wolf shrunk; the optimiser
consumes the **posterior** covariance Σ̄, which inflates variances by ~2%.

**δ is calibrated, not guessed.** `delta = β_basket · ERP / var_basket` makes the cap-weighted basket's
equilibrium excess return equal its CAPM expectation. Left at the config's `risk_aversion = 2.5`, the prior
implies **13-28% excess returns** for these 40-50% vol names and swamps every view; calibration gives
δ ≈ 0.45 and a 13.6% basket return (verified exact in `test_delta_calibration_matches_capm_expectation`).

**τ is a no-op under the usual Ω.** With Ω = diag(PτΣPᵗ)/confidence (Idzorek) or diag(PτΣPᵗ)
(He-Litterman), Ω scales with τ so τ **cancels exactly** — confidence is the only dial. The τ sensitivity
analysis therefore switches to `omega_mode="absolute"` (Ω = diag(PΣPᵗ)/confidence), the only specification
where τ genuinely trades prior against view confidence. Both facts are asserted in the tests.

## Views

| Source | Shape | q | Confidence | Notes |
|---|---|---|---|---|
| `fair_value` | absolute | blended fair-value upside, annualised over the view horizon, clipped to (-20%, +40%) | method count and dispersion | the largest mover: 29% turnover alone |
| `event_score` | absolute | 0.04 × 7-day event score, capped ±12%, only when \|score\| ≥ 0.35 | 0.30 (fixed, low) | from the live market-intelligence overlay; **no validated forward IC**, so deliberately small: 7% turnover |
| `analyst_target` | absolute | consensus target over 1 year | analyst count | biggest raw tilt (1898 bps) and turnover (43%) |
| `segment_ranking` | **relative** | top vs bottom half of each segment by composite, 5% p.a. spread | 0.40 | BL's real strength: expresses a ranking without asserting a level |

Each view reports its prior-implied return, its surprise (`q − Pπ`) and its total absolute posterior impact
in basis points; `attribution` decomposes each name's tilt per view and sums exactly to the tilt.


## Where the views come from

Black-Litterman does not generate opinions. It blends the **market's** implied returns (the prior, reverse
engineered from market caps) with **views supplied by us**. "Market view" in the BL sense means our view,
expressed against the market's. Four sources feed it, and the `/bl` page lists this table so any tilt can be
audited back to the number that caused it.

| Source | Type | Field | Where the number comes from | Caveat |
|---|---|---|---|---|
| Blended fair value | absolute | `upside` | `ai_semi_intel` valuation: DCF 50% + peer-regression multiple 30% + sell-side target 20%, off EDGAR fundamentals, Yahoo consensus and the FRED risk-free rate | a model output, not an observation; DCF and regression often disagree by more than 2x (MU: DCF 822, regression 2917, street 1513, blended 1589) |
| Live event score | absolute | `event_score_7d` | `market_intel`: filings, news and intraday moves classified by rules + LLM | **no validated forward IC**; sized to tilt, not to drive (confidence fixed at 0.30) |
| Sell-side consensus target | absolute | `target_upside` | Yahoo consensus target, confidence scaling with analyst count | known to be optimistic and slow; the largest raw tilt of the four |
| Within-segment ranking | relative | `composite` | the stock-selection composite, ranked inside each value-chain segment | the only relative view: asserts a ranking, not a level |

In the **walk-forward** none of these are usable, because they exist only as today's snapshot. That test
substitutes 12-1 price momentum, which is genuinely point in time. So the historical evidence speaks to the BL
*mechanism*, not to these four view sources; those can only be judged once the live forward IC accumulates.

## Evaluation

Three arms, deliberately separated by evidential weight.

**1. Ex-ante comparison** — 10 constructions scored on the same posterior. Risk and concentration only, no
return claim (scoring BL weights on BL's own μ is circular).

**2. Sensitivity and ablation** — measured on the mean-variance book, since risk parity would report zeros
forever. τ from 0.01 → 1.0 moves the book 0% → 25%; each view source's contribution is listed above.

**3. Walk-forward (the only arm that speaks to returns)** — 164 monthly rebalances, Feb 2013 → Sep 2026.
Views must be point-in-time, so it uses **12-1 price momentum** (the fair-value views exist only as today's
snapshot and cannot be tested historically). Net of 10 bps one-way costs.

| Arm | CAGR | Vol | Sharpe | Max DD |
|---|---:|---:|---:|---:|
| BL + views | 44.4% | 41.3% | **1.00** | -48.2% |
| Prior only (no views) | 39.0% | 36.5% | 0.99 | -45.3% |
| Equal weight | 40.5% | 38.8% | 0.97 | -46.5% |
| Cap weight | 37.5% | 38.1% | 0.90 | -52.5% |
| SOXX | 28.7% | 31.6% | 0.81 | -45.8% |

BL views vs prior only: **+5.3% p.a., but +4.9pp more volatility → Sharpe +0.013, Newey-West t 1.58,
monthly win rate 51%.**

### Verdict

**The view layer is not validated.** The return gain over the equilibrium prior is compensation for extra
risk, not evidence of skill: risk-adjusted, the improvement is ~0.01 of Sharpe with a coin-flip win rate and
a t-statistic well inside the noise band. The large margin over SOXX (+15.7%, t 2.33) comes from the
**survivorship-biased universe** (today's AI/semi constituents), not from BL — every arm shares that bias,
which is why only the spread between arms is meaningful.

Recommended use: keep BL as a **diagnostic and a small tilt**, not the production construction, until the
live forward IC of the event and fair-value signals turns positive. Switching `primary` to mean-variance
would buy an unproven 0.013 of Sharpe for 37% turnover.

## Bug found and fixed along the way

`optimizer._solve` and the BL mean-variance solve both **renormalised a failed SLSQP solve**, silently
returning weights that breached the requested cap. With 5 names capped at 15%, a fully-invested portfolio is
arithmetically impossible (5 × 15% = 75%), yet the optimizer lab returned weights anyway. Both now call
`optimizer.check_feasible()` and fail loudly; the lab surfaces it as HTTP 400 with the arithmetic spelled
out. Covered by `test_infeasible_weight_box_raises_instead_of_breaching_the_cap`.

## Commands

```bash
python scripts/run_bl.py                          # construction + sensitivity + walk-forward
python scripts/run_bl.py --no-walk-forward        # fast (~20 s)
python scripts/run_bl.py --tickers MU DELL NVDA --sources fair_value event_score analyst_target
```

Outputs: `outputs/latest/bl_comparison.json|csv`, `bl_sensitivity.json`, `bl_walkforward.json`,
`bl_summary.json`. Panel: `/bl` (verdict, walk-forward, prior→posterior, weights, ablation, τ sweep, views
in force, method). API: `GET/POST /api/research/bl`, `POST /api/research/bl/run`, `GET /api/research/bl/status`.

Research tooling for discussion only; not investment advice.
