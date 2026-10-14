# Strategic-role rubric (qualitative pillar)

Purpose: capture what filed numbers cannot see - scarcity, lock-in, roadmap position, pricing power,
competitive and geopolitical threats - as a scored, evidence-logged input to the composite. Scores are
0.0-1.0 per dimension with anchored definitions. **Every score must cite evidence** (source, date, URL or
filing reference, one-line claim). Evidence newer than the last assessment is required for a score to change.

| # | Dimension | Weight | 0.0 (weak) | 0.5 (average) | 1.0 (strong) |
|---|---|---|---|---|---|
| 1 | `scarcity` - control of a scarce capability in the AI build-out | 0.25 | commodity supplier, many substitutes | one of 3-5 credible suppliers | sole or dominant source of a bottleneck (EUV, CoWoS, HBM leadership, CUDA-class ecosystem) |
| 2 | `lock_in` - switching costs, design-in cycles, ecosystem | 0.15 | easily swapped, spot purchasing | multi-quarter qualification | multi-year design-ins, software/IP ecosystem, standards control |
| 3 | `roadmap` - technology leadership and product cadence vs peers | 0.15 | trailing, losing share | at parity | clear generation lead, share gaining |
| 4 | `ai_exposure` - share of revenue and growth tied to AI/data-centre demand | 0.15 | < 10% of revenue, indirect | 25-50% | > 50% of revenue, direct, with visibility (backlog, LTAs) |
| 5 | `pricing_power` - evidence of pricing/margin durability | 0.10 | price-taker, margin compression | stable pricing | contract price increases, long-term agreements, prepayments |
| 6 | `competitive_threat` (inverted: 1.0 = low threat) - custom silicon substitution, China competition, new entrants | 0.10 | active share loss / commoditisation under way | manageable | no credible near-term substitute |
| 7 | `geo_regulatory` (inverted: 1.0 = low exposure) - export controls, Taiwan/China concentration, tariffs, antitrust | 0.05 | revenue materially at risk from current rules | moderate | minimal |
| 8 | `execution_governance` - guidance credibility, accounting/auditor history, capital allocation | 0.05 | recent restatement, auditor change, missed guidance repeatedly | normal | consistently conservative guidance, clean history |

`strategic_score` = weighted sum (0-1). Separately:
* `news_direction` (-1.0 to +1.0): direction of **new information in the last 90 days** (contracts, capacity, pricing,
  regulatory actions, guidance changes). This is the fast-moving part and decays to 0 after 90 days.
* `confidence` (0-1): quality and recency of evidence.
* `thesis` (<= 60 words), `kill_criteria` (2-4 observable conditions that would invalidate the role),
  `watch_items` (2-4 things to monitor), `evidence` (3-8 items: `{date, source, url_or_ref, claim, dimension, direction}`).

Anchoring rules: score the company **relative to the AI/semiconductor value chain**, not to the whole market;
do not reward size or past stock performance; use filings (10-K Item 1/1A, 10-Q, 8-K), earnings calls,
company announcements and reputable press; mark inferences without a source as `confidence` < 0.5.
Change control: a re-assessment must list the new evidence that justifies any score change; absent new evidence
the previous scores stand. Full re-assessment quarterly; event-driven re-assessment on triggers
(new 10-K/10-Q/8-K, |5-day move| > 10%, EPS revision > 10%, earnings inside 7 days, age > 90 days).

The report priors in `data/strategic_reference_priors.csv` are secondary-source starting values with 0.50
confidence. They are replaced by company-level evidence assessments when available, expire after 90 days,
and are never silently promoted to verified facts.

## Two required sub-scores: scarcity strength and risk

The pillar is reported and used as two explicit parts, both 0-1:

* **Scarcity strength** = 0.40 scarcity + 0.20 lock_in + 0.15 roadmap + 0.15 ai_exposure + 0.10 pricing_power.
  "How irreplaceable is this company in the AI build-out, and how much of it is priced by contracts rather than hope?"
* **Risk score** (higher = riskier) = 0.70 x LLM-assessed risk + 0.30 x data-derived financial risk, where
  LLM-assessed risk = 1 - [0.35 (competitive_threat) + 0.25 (geo_regulatory) + 0.20 (execution_governance) + 0.20 (customer_concentration)]
  (all four inverted, 1 = low risk; `customer_concentration` is a ninth optional dimension: 1 = diversified,
  0 = one customer > 50% of revenue), and financial risk is computed from filings/prices: net debt/EBITDA,
  negative free cash flow, interest cover proxy, 1-year drawdown and volatility.

**Pillar score used in the composite** = 0.75 x scarcity strength + 0.25 x (1 - risk score).

**Risk gate**: a name with risk score >= 0.75 is excluded from the top-15 unless its scarcity strength is >= 0.80
(a genuine bottleneck with high risk stays in the list but its portfolio weight is capped at 5%).
