# Optuna/TPE portfolio-construction research arm

## Decision

Keep fixed ERC as the control. The new TPE arm modestly improved development OOS Sharpe, then
underperformed ERC on the arm-local terminal period after identical costs and constraints.

Research verdict: **NOT VALIDATED**.

## Frozen experiment

- Fixed control: ERC, 15 holdings, 756-day covariance, 15% name cap, 25% volatility target.
- Search: 80 seeded Optuna/TPE trials in each of six studies over 1,008 discrete configurations.
- Tuned dimensions: method, max weight, covariance lookback, holding count, hysteresis, vol target.
- Inner objective: mean annual net Sharpe over complete chronological validation years.
- Development outer folds: 2019-2023.
- Terminal evaluation: one configuration selected using observations through 2023-12-29 and frozen
  for 2024-01 through 2026-07.
- Execution: month-end close signal, first available close after the signal, then hold to the first
  close after the next signal.
- Costs: 20 bp per bought or sold notional. Turnover uses the full old/new holdings union.
- Constraints: 15% or 10% configuration-level name cap, at most three names per segment, and at most
  35% portfolio weight per segment. Residual capital stays in zero-return cash when a cap or the
  volatility target prevents full investment.

Protocol hash: `c1fd53d0c3e106387cfdebbbf6c70a05bbedac6bfa374ed56b8a5ef5059c9d57`.

## Results

| Window | Arm | Net Sharpe | CAGR | Volatility | Max drawdown | Average turnover |
|---|---|---:|---:|---:|---:|---:|
| Development OOS, 60 months | Optuna/TPE | 0.908 | 33.7% | 35.0% | -32.7% | 32.6% |
| Development OOS, 60 months | Fixed ERC | 0.867 | 24.2% | 25.6% | -24.4% | 24.3% |
| Terminal, 31 months | Optuna/TPE | 1.352 | 72.4% | 52.1% | -27.2% | 30.8% |
| Terminal, 31 months | Fixed ERC | 1.879 | 67.4% | 34.8% | -15.2% | 17.9% |

The terminal TPE portfolio earned a higher raw CAGR by taking substantially more risk. Its Sharpe was
0.527 below ERC. The paired moving-block bootstrap 95% interval for the Sharpe difference was
`[-1.324, 0.118]`; the paired annualized mean-return difference was +10.2 percentage points with a
Newey-West t statistic of 0.92.

The terminal TPE choice was config 582: `ms_ic05`, 15% max weight, 756-day lookback, 10 holdings,
no hysteresis, and no volatility target.

## Audit evidence

- Return matrix: 175 unique months by 1,008 configurations; no missing returns.
- Leakage check: zero cutoff violations across 480 trials and six studies.
- Constraint check: zero name, segment-count, segment-weight, or missing-label violations.
- Independent accounting reconstruction: nine first/loss/switch months; maximum absolute difference
  `5.55e-17` across gross return, turnover, cost, and net return.
- Full test suite: 120 passed.

Detailed artifacts are in `outputs/validation/optuna_tpe/`, including `protocol.json`, `qa.json`, all
trial records, monthly returns, saved weights, independent reconstructions, and the comparison chart.

## Evidence boundary

The 2024-2026 period was not used by this TPE study, but prior project grid research had already
examined it. It is arm-local OOS, not a globally pristine holdout. A prospective untouched comparison
is frozen from the 2026-09-30 signal and currently has no observations.

Current-universe survivorship, missing delisting terminal returns, and absent historical consensus and
scarcity inputs remain production-blocking. These gaps cap the verdict at `NOT VALIDATED`.

## Reproduce

```bash
python scripts/run_portfolio_sweep.py
python scripts/run_optuna_tpe.py --trials 80
```
