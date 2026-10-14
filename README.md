# MarketIntel
### From an equity-research hypothesis to an inspectable portfolio decision

MarketIntel connects AI/semiconductor value-chain research, point-in-time signal tests, constrained portfolio construction, risk reporting, and a governed event-intelligence interface. I developed the project around a mentor-provided portfolio framework and extended its research and review tools after the mentored period.

**Author/project lead:** Siyan (Diego) Chen, UC Santa Barbara. **Status:** research prototype; **NOT VALIDATED**.

## Research in one minute

The central question is whether an economically plausible stock score survives a test using information available at each historical date. The documented baseline composite had negative point-in-time rank correlation in the studied universe. The follow-up study evaluated 36 candidate features with fixed selection rules, split-period checks, and nested evaluation. Growth acceleration and momentum motivated further investigation; the evidence retained universe and data-vintage limits.

The project then examines how signals become holdings. It compares 13 construction methods, including equal weight, minimum variance, equal risk contribution, hierarchical risk parity, and mean-variance optimization. Risk and implementation checks cover constraints, covariance estimation, turnover, volatility targets, and the weights that actually reach the reporting layer.

The event system records rule-based classification, optional LLM enrichment, source lineage, review state, and usage budgets. The default event weight is zero. Target weights and simulated ledgers are research artifacts; this system has no live broker path.

## What to inspect

| Component | Inspectable implementation | Research purpose |
|---|---|---|
| Point-in-time fundamentals | [Feature adapter](ai_semi_intel/research/pit_features.py) | Align SEC facts to filing availability; preserve missingness |
| Factor evaluation | [Factor lab](ai_semi_intel/research/factor_lab.py) | Compare candidate definitions and chronological selection |
| Portfolio construction | [Construction library](ai_semi_intel/portfolio/constructions.py) | Translate scores and risk estimates into constrained holdings |
| Investor views | [Black–Litterman](ai_semi_intel/portfolio/bl.py) | Make view/prior assumptions explicit and test downstream use |

[Research overview](docs/research-overview.md) · [Contribution and provenance](docs/provenance.md)

## Run an offline example

```bash
python -m pip install -r requirements.txt
python scripts/synthetic_portfolio_demo.py
python -m pytest -q tests
```

The demo uses a seeded synthetic covariance matrix and compares portfolio rules. It needs no API keys and does not reproduce historical returns.

The public snapshot contains selected **numerical research modules** and synthetic examples. The full private application also includes an event-review interface; its operational backend and account state are excluded from this admissions-facing distribution. Historical studies require separately supplied price and SEC-fundamental inputs. `FRED_API_KEY` and `SEC_USER_AGENT` are configured through the environment when needed.


## Contribution

I directed the research specifications, selected questions and comparisons, reviewed results, and made project decisions. AI coding assistants wrote substantial portions of the implementation. The original portfolio framework was provided by my mentor; this repository presents my student research project and makes no claim to be an institutional UBS product.

The most useful outcome was learning to trace a plausible research story through dated data, explicit evaluation rules, constrained holdings, and failure states. Historical diagnostics remain exploratory and conditional on the studied sample.
