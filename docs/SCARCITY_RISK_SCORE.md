# Scarcity and risk valuation layer

## Objective

The decision score now exposes five direct pillars:

`decision score = 37.5% growth + 26.25% momentum + 11.25% value + 15% scarcity + 10% risk resilience`

These first three weights preserve the researched alpha score's relative 50/35/15 mix while assigning it 75%
of the decision budget. `alpha_score` remains visible as a diagnostic. Scarcity and risk are conservative
research priors, not validated alpha factors.

## Scarcity

The evidence rubric scores bottleneck control, switching costs, roadmap leadership, AI revenue exposure and
pricing power. A 0-1 assessment becomes a bounded z-like score `(raw - 0.5) / 0.25`, then is multiplied by
evidence confidence. Missing and >90-day-old assessments contribute zero. The initial 15 report priors come
from *Market Intelligence - AI Stocks Research Paper*, Table 6 (page 10), dated 31 July 2026. Their confidence
is capped at 0.50 because the paper says the supplied deck fields were not independently reconciled.

Detailed company assessments in `data_cache/strategic/assessments/` supersede these priors and retain source,
date, claim, dimension, change history, kill criteria and watch items.

## Risk resilience

Higher is safer. Every raw input is winsorised cross-sectionally and converted to a signed z-score. Missing
inputs are neutral and shrink coverage.

| Sub-score | Weight | Inputs |
|---|---:|---|
| Market resilience | 35% | lower 1-year volatility, shallower 1-year drawdown, lower beta |
| Financial resilience | 35% | lower net debt/EBITDA and SBC; higher FCF margin, ROIC and through-cycle operating margin |
| Valuation resilience | 30% | lower positive EV/Sales and P/E; lower DCF terminal-value share and fair-value-method dispersion |

Separate flags expose high volatility (>60%), drawdown (<-35%), beta (>1.75), leverage (>3x), valuation-method
dispersion (>50%), and valuation crowding (EV/Sales >15x or P/E >45x).

## Validation boundary

The 2012-2026 point-in-time backtest does not contain historical strategic assessments, consensus snapshots,
or reconstructed report priors. It validates `alpha_score` only. The daily archive now stores every new score,
coverage field, source date and risk flag. Forward IC, rank turnover and performance must be evaluated after a
sufficient live history exists. Until then the combined score is always labelled `RESEARCH_PRIOR_NOT_VALIDATED`.

## LLM deep-research layer (evidence-logged) and how it feeds both pillars

`ai_semi_intel/analysis/strategic.py` + `docs/STRATEGIC_RUBRIC.md`. Each company carries a versioned assessment
(`data_cache/strategic/assessments/<TICKER>.json`) on eight anchored 0-1 dimensions plus an optional ninth
(customer concentration), with 3-8 cited evidence items, a thesis, kill criteria and watch items. Assessments come
from (a) interactive LLM research sessions run by the analyst (`assessor: claude-session-research`) and (b) the daily
pipeline's Claude API assessor (research turn with server-side web search/fetch, then a structured parse into the
schema) once `ANTHROPIC_API_KEY` or an `ant auth login` profile is present. Without credentials the pipeline runs
in dry-run mode on stored assessments.

* **Scarcity pillar input** = scarcity strength = 0.40 scarcity + 0.20 lock-in + 0.15 roadmap + 0.15 AI exposure
  + 0.10 pricing power (`scarcity_raw`), then confidence-shrunk as above. Evidence assessments supersede the
  Table 6 priors automatically.
* **Risk-resilience pillar** gains a fourth sub-score, *strategic resilience* (25%; market 26%, financial 26%,
  valuation 23%): 1 minus the LLM risk = 0.35 competitive threat + 0.25 geopolitical/regulatory + 0.20 execution/
  governance + 0.20 customer concentration (all inverted dimensions). Missing when no assessment exists.
* **Risk gate**: combined risk = 0.7 x LLM risk + 0.3 x data-derived financial risk (leverage, negative FCF,
  drawdown, volatility). Risk >= 0.75 excludes a name from the top-15 unless scarcity strength >= 0.80, in which
  case it stays and its portfolio weight is capped at 5%. Gate decisions are alerted (`risk_gate`, `risk_cap`).
* **Refresh triggers** (`strategic.detect_triggers`): new 10-K/10-Q/8-K, |5-day move| >= 10%, 30-day EPS revision
  >= 10%, earnings within 7 days, assessment older than 90 days, and `market_intel` overlay events (|7-day event
  score| >= 3 or a warning/critical alert). Up to 12 re-assessments per daily run; change control accepts a new
  assessment only if it cites evidence dated after the previous one (or the 90-day scheduled refresh).
  `news_direction` (-1..+1 for the last 90 days) decays linearly to zero.
* **Archive**: `outputs/history/<date>/strategic_snapshot.json` and the universe panel store every score, source
  and confidence for forward validation of the pillar.
