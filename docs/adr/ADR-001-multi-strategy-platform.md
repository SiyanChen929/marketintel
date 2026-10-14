# ADR-001 — Multi-strategy research platform: plugin boundary, data plane, migration

**Status:** accepted; phase 0 plus the Stock Select reference-adapter and paper-ledger slice are implemented.
**Date:** 2026-09-03 · **Accepted:** 2026-09-04 · **Supersedes:** none · **Related:** `docs/MARKET_INTEL.md`

---

## Context

Six engines exist as independent repositories: **Stock Select** (this project), **Alpha Engine**,
**StableAlpha**, **Pairs Trading**, **Commodity Arbitrage**, **MSCI Rebalance**. The review asks whether they
can share one platform. They can — but not by merging code.

What is reusable here today: the immutable `run_id` with config/code/data hashes and append-only run history;
the 13-method construction registry; the risk workbench; LLM provider and prompt lineage; the event/story/case
store.

What blocks reuse: the pipeline is welded to one universe (AI/semiconductor), three vendors (Yahoo/EDGAR/FRED),
a five-pillar score and one optimiser; `PORTFOLIO_ID` is a constant; `outputs/latest` holds exactly one book;
the web layer reads a fixed `prices_universe.parquet`; risk assumes long-only, no derivatives, no position
ledger; and events are keyed by ticker with no `strategy_id` / `portfolio_id` / `instrument_id` /
`dataset_version`.

## Decision

**Three layers, and strategies stay in their own packages.**

```
Shared Data Plane      InstrumentMaster · DatasetCatalog · raw/normalized/features/artifacts
        │                    (PIT, finality, lineage, content-addressed, vendor-agnostic)
Platform Core          Workspace · Strategy · Portfolio/Mandate · Experiment/Run · DatasetVersion
        │              ModelVersion · Artifact · Risk · Attribution · Evidence/Claim · AuditEvent
StrategyPlugin         each engine implements the interface; platform never imports engine internals
```

**Rejected:** copying the six engines into `ai_semi_intel`, or widening its pipeline with flags. Both make one
package own six research agendas, and every strategy's release then blocks on the others.

**Chosen:** each engine keeps its repository, CLI and internal algorithms. It gains a thin **adapter** that
implements `StrategyPlugin`. Phase 1 adapters may shell out to the existing CLI and translate CSV/JSON — no
algorithm is rewritten to onboard.

---

## The plugin interface

```python
class StrategyPlugin(Protocol):
    def manifest(self) -> StrategyManifest: ...
    def required_datasets(self) -> list[DatasetRequest]: ...
    def build_universe(self, snapshot: DataSnapshot) -> Universe: ...
    def generate_signals(self, ctx: RunContext) -> SignalFrame: ...
    def construct_targets(self, ctx: RunContext) -> list[PositionIntent]: ...
    def simulate_execution(self, ctx: RunContext) -> ExecutionReport: ...
    def export_attribution(self, ctx: RunContext) -> AttributionFrame: ...
```

`RunContext` is **frozen**: `workspace_id`, `strategy_id`, `portfolio_id`, `run_id`, `as_of`,
`dataset_versions`, `model_version`, `mandate`, `seed`. A plugin cannot read "today" — it reads its context.
That is what makes a Decision restorable, and it is enforced by handing the plugin a read-only snapshot
handle rather than a path.

### PositionIntent — the universal output

One record type every strategy emits, expressive enough for all six. This is a research interchange contract;
it does not imply that the current paper executor can execute every represented instrument:

| Field | Why it exists |
|---|---|
| `instrument_id` | permanent, not a ticker: survives renames, delistings, contract rolls |
| `portfolio_id`, `sleeve_id`, `leg_id` | `leg_id` carries pair legs; `sleeve_id` carries cross-strategy allocation |
| `direction`, `weight` / `notional` / `units` | long-short needs signed; futures need units |
| `multiplier`, `currency` | commodity contracts and non-USD |
| `delta`, `margin` | options Greeks (MSCI arb) and futures margin |

Portfolio optimisation then has **two levels**: within-strategy construction (the plugin's job) and
cross-strategy sleeve allocation (the platform's job). Today's 13 constructions serve the first level only.

---

## Per-strategy adapter difficulty

| Strategy | New primitives required | Difficulty |
|---|---|---|
| **Stock Select** | none — reference implementation | ★ |
| **Alpha Engine** | PIT universe membership, long-short, borrow cost, neutralisation | ★★ |
| **StableAlpha** | HS300 PIT membership, A-share T+1, price limits, ST, suspensions | ★★★ |
| **Pairs** | `Pair` / `Leg` / `PairVintage`, hedge ratio, two-leg cost and accounting | ★★★ |
| **Commodity** | dated contracts, roll policy, settlement, multiplier, margin, exchange calendars | ★★★★ |
| **MSCI Rebalance** | `ReviewEvent`, announcement vs effective time, the 15:48:59 information boundary, minute/NBBO/NOII/auction data, options Greeks | ★★★★★ |

Sequencing follows this column, not business priority: each strategy introduces primitives the next one
reuses. MSCI is last because its information-boundary and microstructure needs would otherwise force the
data plane to be designed twice.

---

## Data plane

`InstrumentMaster` issues permanent `instrument_id`s and maps vendor symbols with validity intervals.
`DatasetCatalog` versions every pull.

**Download de-duplication key** (as specified in the review): `provider`, `entitlement`, `dataset`,
`instrument_ids`, `fields`, `frequency`, `adjustment`, `date_range`, `vintage/as_of`, `schema_version`. A
coverage index fetches only missing intervals; payloads are de-duplicated by content hash. No vendor is
privileged — Yahoo/EDGAR/FRED become three providers among others.

**PIT and finality are first-class**: every row carries `effective_at` (when it was true) and `observed_at`
(when we could have known it), plus a finality flag for restatements. This is what the MSCI 15:48:59 boundary
and StableAlpha's membership history need, and it is cheaper to build in now than to retrofit.

---

## Market Intelligence

The event store maps to permanent `instrument_id` **first**; each strategy then subscribes and derives its
own impact. One NVDA event, six different readings, no duplication.

**Hard boundary:** the LLM reads a frozen evidence bundle and emits structured `Claim` / `Scenario` objects.
It is never in the path that computes canonical P&L or risk. That keeps every number reproducible and
auditable, and it is why the event overlay stays at `w_event = 0` until its forward IC is measured.

LLM provider usage is stored in a separate SQLite ledger by provider, model, operation and UTC request time.
Budgets are rolling-window controls. Cost exists only for calls with provider-reported usage and an operator-supplied
rate for every used token component; no rate is embedded or inferred. Uncertain metering or exhausted configured
budgets disables the LLM path fail-closed, while the deterministic rules path remains available.

---

## Migration and compatibility

Strangler-fig, not big-bang. Each step ships behind a flag and is reversible.

| Step | Ships | Rollback |
|---|---|---|
| 0 | interfaces + `InstrumentMaster` + `DatasetCatalog` schemas | leave additive tables unused |
| 1 | Stock Select reference adapter on additive `/api/v2/platform/*`; legacy pages remain default | remove the additive route/injection |
| 2 | explicit startup ingestion freezes the current projection as a Run plus required artifacts | skip `sync_current_run()`; legacy output is untouched |
| 3 | legacy web readers move through the registry; `outputs/latest` becomes a projection | not implemented |
| 4+ | one strategy per batch, in the difficulty order above | not implemented |

### Implemented slice (2026-09-04)

- `market_intel/platform/core.py`: frozen strategy/run/data contracts, signed-sizing multi-asset
  `PositionIntent`, versioned plugin registry, and the injected Stock Select adapter.
- `market_intel/platform/data.py`: permanent instrument aliases, exact-request de-duplication,
  content-addressed blobs, checksums, observation-time lookup and finality gates.
- `market_intel/platform/stock_select_data.py`: freezes the mutable Stock Select projection into exactly five
  catalog roles (`price_panel`, `construction_weights`, `landed_weights`, `universe`, `benchmark`) and binds their
  version IDs into one canonical input-manifest hash. All five request records must agree on the legacy run ID,
  full source-manifest SHA-256 and vintage. A formal benchmark additionally requires a hash-bound mandate provenance
  sidecar with portfolio identity, PIT timestamps, confirmed/final status and explicit approval; a weights CSV alone
  fails closed.
- `market_intel/platform/repository.py`: version-pinned strategy/run registry, immutable run context,
  append-only run events and hashed artifacts.
- `market_intel/platform/paper.py`: local PAPER/SHADOW-only orders, fills, costs, positions, cash,
  mark-to-market, replay, reconciliation and kill switch. Every order is bound to a `run_id` and
  full context hash. The implemented executor supports only long, cash-funded, unit-multiplier USD equities.
  It rejects shorting, margin, derivatives/options, futures, non-USD/FX accounting, LIVE execution and broker
  adapters.
- `market_intel/platform/quotes.py`: resolves an exact per-symbol map of catalog quote versions and accepts only
  content-matching confirmed/final quotes observed after the signal and available before the paper step.
- `market_intel/platform/checkpoint.py`: optional HMAC-signed, append-only external ledger-head checkpoints with
  compare-and-swap and read-back verification. The file reference implementation is independent only when its
  path and signing key are administered outside the local ledger trust domain. Post-commit publication failure is
  an explicit `committed_checkpoint_pending` state (HTTP 202 and a matching audit event), repaired only by an
  idempotent retry against an intact lagging witness. A missing witness for sequence greater than 1 cannot be
  initialized as a new genesis.
- `market_intel/platform/risk_adapter.py`: a frozen-run-bound Stock Select adapter for the exact 3Y/756-session
  actual landed book. The run declares the `risk_parity` algorithm, while `recommended` is the executable weight
  column used by the `actual_run` layer. VaR 99% (1-day and 10-day), 10-day ES 97.5% and Tracking Error publish
  only when the complete sample, official interval and lineage pass. ALAB and SNDK currently make 3Y unavailable;
  the adapter does not shorten the window or drop them. TE kind, source, decision usability and benchmark version/hash
  must reconcile to the frozen benchmark bytes and catalog request. Without a formal mandate, TE is a non-decision-
  usable research proxy.
- `market_intel/llm_usage.py`: persistent token use, explicit-cost lineage and rolling token/cost budget gates;
  the five environment controls are documented in `docs/MARKET_INTEL.md`.
- `/api/v2/platform/*`: read interfaces plus operate-authorised, audited paper mutations.

`serve()` invokes `sync_current_run()` once before starting HTTP. Platform GETs only resolve that frozen state;
they never ingest mutable `outputs/latest` projections or mutate platform/usage persistence.

This slice is still `NOT VALIDATED`. The checked-in Stock Select run is also `NOT ACTIONABLE`; its five required
input roles are now frozen and catalog-verified, but paper stepping remains closed before a quote source is
consulted. An actionable run must additionally pin one confirmed quote version for every symbol in its paper book.
The other five strategy rows are capability declarations only; their adapters are not installed.

**Compatibility guarantees during migration:** `outputs/latest/*` keeps its current shape until every reader has
moved; the synchronized run retains the producer's `run_id`; current API paths keep working, with additive platform
routes under `/api/v2/`. Relevant regression suites must stay green at every step, and each adapter gets its own
tests before installation.

## Consequences

**Accepted cost:** an adapter layer and a second optimisation level are real complexity, and cross-repository
versioning becomes a coordination problem. **Bought:** each engine keeps its release cadence; a Decision
becomes restorable across all six; one Market Intelligence feed serves every strategy; and the risk and
attribution stack is written once.

The platform reuses the server's identity/RBAC and append-only audit controls: paper mutations require `operate`,
an `Idempotency-Key`, same-origin JSON requests and an audit intent. This does not establish organisation-wide
governance or production execution approval; the remaining control gaps stay tracked as open items.

---

## Remaining decisions

1. **Risk policy.** Keep 1-day and 10-day estimates side by side until a limit/reporting owner selects a
   canonical measure. Availability must remain construction-specific and fail closed.
2. **Cross-strategy allocation owner.** Decide whether sleeve allocation is a platform mandate or a PM-owned
   decision before the second strategy is installed.
3. **A-share and futures entitlements.** StableAlpha and Commodity adapters stay disabled until licensed PIT
   membership, dated-contract, settlement and exchange-calendar sources are identified and sample-verified.
4. **Execution evidence.** Paper accounting tests do not validate fill realism, market impact, corporate
   actions, borrow, margin or multi-process crash recovery. These remain explicit release gates.
