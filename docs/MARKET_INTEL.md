# Market Intelligence AI (`market_intel`)

A program-level, always-on event monitor for the AI / semiconductor value-chain stock-selection process, and a
desktop information assistant. It watches filings, news, macro releases and intraday prices, classifies every
item into an investable-event taxonomy, scores the impact per ticker (direct and value-chain read-through),
writes an overlay the daily selection pipeline can consume, and pushes desktop alerts. A local dashboard and a
question-answering interface sit on top of the same event store.

```
sources (RSS/Atom, SEC EDGAR, Yahoo, Google News, FRED, prices)
   -> normalise (RawItem)  -> rule classifier (type, direction, materiality, tickers, themes)
   -> SQLite store (dedup, FTS5)  -> LLM enrichment (Anthropic or OpenAI; structured JSON; optional; provider/model/prompt version recorded per event)
   -> per-ticker impacts -> decayed 7-day event scores -> outputs/latest/event_overlay.csv
   -> alerts (critical / warning / info) -> macOS notifications, dashboard, menu bar
   -> pre-market brief (07:00 ET) ; Q&A over the store (CLI + dashboard)
```

## Commands
| Command | What it does |
|---|---|
| `python -m market_intel serve` | Poller loop + API + dashboard at http://127.0.0.1:8787 (`--no-poll`, `--no-notify`, `--port`) |
| `python -m market_intel poll [--json] [--no-notify]` | One polling cycle (fetch, classify, score, alert) |
| `python -m market_intel ask "..."` | Question answering over the event store (streams) |
| `python -m market_intel brief` | Build the pre-market brief now (`outputs/latest/market_brief.md`) |
| `python -m market_intel overlay` | Recompute ticker scores and rewrite the overlay |
| `python -m market_intel search "..."` / `calendar` / `status` | Full-text search, macro+earnings calendar, health |
| `python -m market_intel menubar` | macOS menu-bar companion (alert count, latest alerts, shortcuts) |
| `python -m market_intel set-key` | Store the Anthropic key (hidden prompt, file mode 600) and validate it with a test call |
| `./scripts/install_market_intel.sh` | launchd login agents: the service (restarts on failure) and the menu-bar companion (`--no-menubar` to skip) |

## Data sources (all free; see `market_intel/feeds.yaml`)
| Source | Content | Cadence |
|---|---|---|
| SEC EDGAR current-filings feed | 8-K (with item codes), 10-Q, 10-K, SC 13D, 424B5 for the 116 universe CIKs | every cycle |
| SEC EDGAR full-text search | backfill of the same forms per CIK (batched) | every 6 h |
| Yahoo Finance | per-ticker headlines (holdings every cycle, universe rotating), search API with `relatedTickers` | every cycle / 3rd cycle |
| Google News RSS | holdings by name, semis, AI infrastructure, policy, power, optics queries | every 2nd cycle |
| CNBC, MarketWatch, WSJ, NYT, BBC, Yahoo, Investing.com, Seeking Alpha currents | market news | every 1-2 cycles |
| DigiTimes, SemiAnalysis, TechCrunch AI, The Verge | supply chain / AI | every 1-3 cycles |
| GlobeNewswire earnings, PR Newswire financial | press releases | every 1-2 cycles |
| Federal Reserve press releases, BLS latest numbers, White House actions | macro / policy | every 1-3 cycles |
| FRED release calendar + FOMC dates + universe earnings dates | calendar | daily |
| Yahoo Finance 5-minute bars | abnormal intraday moves vs previous close and vs SOXX (4/8/15% tiers, >= 2.5 sigma) | every cycle, 04:00-20:00 ET |

Polling runs every 5 minutes 06:00-20:00 ET on weekdays and every 15 minutes otherwise.

launchd note: both agents launch `python3` directly with a `WorkingDirectory`. A `/bin/zsh script.sh` wrapper spawned by launchd
cannot open files inside the privacy-protected `~/Desktop` folder (exit 127, "can't open input file"), while python launched
the same way works; the `scripts/*.sh` files remain for manual runs. Feeds use
ETag/Last-Modified conditional requests; the SEC User-Agent carries the analyst's contact email.

## Event model
Each stored event carries: `event_type` (earnings, guidance, preannouncement, m_and_a, capital_raise,
buyback_dividend, contract_customer, product, capacity_capex, supply_chain_pricing, management,
regulatory_export, tariff_trade, litigation_investigation, analyst, insider_holder, index_change, macro_data,
fed_policy, geopolitical, policy, price_move, filing, commentary, market_news, other), `direction` (-2..+2),
`materiality` (0-1), `confidence`, `horizon`, `tickers` (direct), `tickers_rt` (value-chain read-through),
`themes`, `pillars` (growth / momentum / value / quality / risk / macro = which part of the selection model
the event touches), `action` (none / monitor / thesis check / re-run model) and a one-line analyst summary.

**Rule engine** (`classify/rules.py`): pattern lexicons per event type, a sentiment lexicon for direction,
source-credibility weights (SEC and price events 1.0, tier-1 media 0.85+, listicle publishers <= 0.4),
commentary detection, 8-K item codes -> type (2.02 results, 1.01 material agreement, 5.02 officer change,
2.06 impairment, 4.02 non-reliance, ...). Entities come from curated aliases (`entities.py`); symbols that are
also words (ON, AI, ARM, NOW, ...) only match as `$ARM` / `(NASDAQ: ARM)`.

**LLM enrichment** (`classify/llm.py`; Anthropic `claude-opus-5` or the pinned OpenAI model): relevant, non-commentary items above the materiality floor are sent
in batches of 20 to `claude-opus-5` with a structured JSON schema (`output_config.format`), a cached system
prompt that contains the whole universe with current holdings, effort `low`, adaptive thinking, and server-side
refusal fallbacks. Its judgement overrides the rule engine (tickers, direction, materiality, action, one-liner).
Without a key the system runs on rules only; Q&A returns the retrieved evidence without synthesis.

### LLM usage and budget controls

`market_intel/llm_usage.py` persists request metadata and provider-reported usage by provider, model and operation;
prompts and responses are not stored. `GET /api/llm/usage` is a read-only rolling-window view, and the same payload
appears under `/api/status → llm.usage`. Its five controls are:

| Variable | Meaning |
|---|---|
| `MARKET_INTEL_LLM_USAGE_DB` | SQLite ledger path; default `data_cache/market_intel/llm_usage.sqlite` |
| `MARKET_INTEL_LLM_PRICING_JSON` | Explicit provider/model per-million-token rate card; no built-in or inferred price |
| `MARKET_INTEL_LLM_TOKEN_BUDGET` | Optional rolling token threshold; later calls stop once recorded use reaches it |
| `MARKET_INTEL_LLM_COST_BUDGET_USD` | Optional rolling known-cost threshold in USD |
| `MARKET_INTEL_LLM_BUDGET_WINDOW_SECONDS` | Rolling UTC request-time window; default `86400` |

For OpenAI, cached input is a subset of input, so total tokens are `input + output`; cached input is separated only
for pricing. For Anthropic, cache-read and cache-write tokens are separate from uncached input, so total tokens are
`input + cache_read + cache_write + output`. Cost is available only when the response reports usage and an explicit
rate exists for every non-zero component used by that provider/model call. Each priced record stores the normalized
rate-card hash; later rate changes do not rewrite historical cost. Missing pricing, incomplete usage, pending/failed
reservations, an unreadable ledger, or an exhausted configured budget blocks later LLM calls when that uncertainty
prevents budget enforcement. The gate does not predict the current response size, so one completed call can cross a
configured threshold; the next call is then blocked. The rules classifier remains usable and never spends LLM budget.

## Scoring and alerts
* impact(event, ticker) = direction/2 x materiality x confidence (x 0.6 x theme weight for read-through; read-through only for
  material event-like items, and segment-wide themes are halved when the article names no company)
* ticker event score (7d) = sum of impacts decayed with a 36-hour half-life; also a 1-day sum, counts, the
  strongest event and the pillars touched.
* Alert level per event: **critical** = held name with an 8-K carrying a material item, an intraday move >= 8%,
  or materiality >= 0.75 with a clearly directional read; **warning** = held name with materiality >= 0.55 and a
  direction, a universe name with materiality >= 0.8 and |direction| = 2, or a directional tier-1 macro/policy
  item; **info** = everything else that is relevant. Warning and critical alerts are pushed as macOS
  notifications (max 4 per cycle, grouped beyond that); all levels appear on the dashboard and the menu bar.

## Integration with the stock-selection pipeline
* `outputs/latest/event_overlay.csv` (one row per universe ticker: `event_score_7d`, `event_score_1d`,
  `n_events_7d`, `alert_level`, `pillars`, `action`, last event) and `outputs/market_intel/event_overlay.json`
  (holdings summary, movers, top events) are rewritten every cycle.
* `ai_semi_intel.dashboard.monitoring.event_alerts` merges warning/critical overlay flags (and strongly negative
  news flow) into the daily pipeline's alert list, so they appear on the static dashboard and in `summary.json`.
* `ScreenConfig.w_event` (default 0.0) adds a bounded event tilt to the composite:
  `composite += w_event * clip(event_score_7d, -2, 2)` (so w_event = 0.15 moves a name by at most 0.3). It is off by default because the point-in-time validation showed
  the composite's skill is concentrated in growth acceleration and momentum; enable it only after checking the
  walk-forward results with the live archive. The overlay is ignored when older than 36 hours.


## Web app: research pages

### Interface language

Every MarketIntel screen, including sign-in, has an **English / 中文 / 日本語** selector.
The selection is saved as `mi-language` in this browser or the desktop WebKit shell;
it survives refresh and navigation. English is the default and the fallback for
a catalog entry missing a locale; unmapped text retains its source wording. Storage
failures also default to English on a new page. Switching uses
cached interface data and does not run research or call a translation API.

Translations live in `market_intel/static/i18n-{common,home,portfolio,platform}.js`,
loaded by the server before the shared UI. Each authored phrase has English,
Simplified Chinese and Japanese values. `t('Label {count}', {count})` handles dynamic
UI labels; new original news, evidence or analyst text must use `translate="no"`.
Table data cells and chart data arrays retain their original values. Source reports,
news titles/summaries, questions, analyst notes, identifiers, formulas and downloaded
research artifacts retain their original language. Unknown source descriptions and
backend diagnostic messages remain verbatim rather than receiving guessed translations.

Launch without polling or notifications:
`python -m market_intel serve --no-poll --no-notify`, then open
`http://127.0.0.1:8787/` (or refresh the existing MarketIntel desktop app). If a server
was started before this change, restart it to load the localization asset registration.

Offline localization checks: `python -m pytest -q tests/test_market_intel_i18n.py`
and `node --test tests/market_intel_i18n_frontend.test.cjs`. Browser rendering checks
are available with `MARKET_INTEL_I18N_BROWSER=1` where headless browser launch is allowed.

### Research screens

The same server also serves the stock-selection research system (reads `outputs/latest`, `outputs/history`, `outputs/validation`,
`data_cache/prices`). All charts are Plotly, served locally (`/static/plotly.min.js`), no CDN.

| Page | Content |
|---|---|
| `/` | Live event feed, impact board, calendar, brief, Q&A (the event monitor) |
| `/portfolio` | KPI row, target weights per construction, risk contributions, efficient frontier, correlations, in-sample backtest with drawdown, holdings table (with live event score/level), **Optimizer lab**: re-run the pipeline optimiser (Ledoit-Wolf, Black-Litterman prior + Grinold alphas, constrained solves, vol target, backtest) on any subset of the universe with your own constraints |
| `/risk` | Risk workbench on the canonical run's executable target weights: reconciliation, active weights vs benchmark, segment / country / currency / ADR / Taiwan-China / AI-capex exposures, gross-net, historical VaR/CVaR, beta stress, event concentration, liquidity (explicit gaps), current vs target (no ledger), pre-trade compliance |
| `/platform` | Multi-strategy research-platform control plane: adapter registry, frozen run/context lineage, shared dataset catalog, local PAPER/SHADOW ledger, risk/attribution interface and explicit blockers. It has no live broker path. |
| `/cases` | Case management for escalations: owner, due / SLA, status, acknowledgement, evidence, decision, no-trade reason, execution reference, sign-off, notes; opened automatically for warning / critical events on target-list names |
| `/universe` | Screening funnel, composite ranking, valuation-vs-growth bubble chart (holdings emphasised), sortable/filterable universe table with event columns, segment table |
| `/ticker/{T}` | Price indexed with SOXX and event markers, valuation/scores, fundamentals & consensus, recent events, strategic thesis, tearsheet |
| `/macro` | FRED KPI tiles, small-multiple charts, snapshot table, macro + earnings calendar |
| `/research` | Run the pipeline (full / re-score) with live log, point-in-time validation (IC table, feature IC, charts), live forward IC, run history, strategic-role table, deck download, static daily dashboard, chart gallery |

APIs: `/api/research/{summary,portfolio,universe,ticker/{T},macro,history,validation,strategic,files}`,
`POST /api/research/optimize` (`{"tickers": [...], "max_weight": 0.2, "vol_target": 0.25, "lookback_years": 3, ...}`),
`POST /api/pipeline/run` (`{"stages": []}`), `GET /api/pipeline/status`, `GET /api/tunnel`, `/files/deck`, `/files/static-dashboard`.

## Multi-strategy platform (MVP)

The first migration slice uses a strangler boundary. Existing `outputs/latest/*` readers continue to work;
Stock Select is exposed through an injected reference adapter and the five future engines remain clearly
labelled `adapter_not_installed`. The reusable contracts live in `market_intel/platform/`:

- one versioned `StrategyPlugin` registry and immutable `RunContext`;
- one permanent-instrument and content-addressed dataset catalog, keyed by exact vendor request and PIT
  observation time;
- one signed-sizing `PositionIntent` that can represent equity, pair legs and dated futures without coercing them to
  a long-only weight;
- one hash-chained local PAPER/SHADOW ledger for orders, fills, costs, positions, cash, marks and kill switch.

All platform output is `NOT VALIDATED/PAPER`. Startup freezes Stock Select into exactly five content-addressed
dataset roles: `price_panel`, `construction_weights`, `landed_weights`, `universe` and `benchmark`. Every paper order
persists the source `run_id` and full frozen context hash. A paper step requires an actionable run and a
`quote_versions` object that exactly covers the paper book. Each referenced quote must be a catalog-verified,
confirmed/final version observed after the signal and acquired before the step. Failure at any gate returns 409
without consulting a later stage or appending an order. Execution is deliberately limited to long, cash-funded,
unit-multiplier USD equities in `PAPER` or `SHADOW`: shorting, margin, derivatives/options, futures, non-USD/FX
accounting, `LIVE`, broker connections and real orders are rejected.

Those five roles form one indivisible lineage set: their catalog requests must share the same legacy run ID, full
source-manifest SHA-256 and vintage timestamp. `data/benchmark_weights.csv` is formal only when accompanied by
`data/benchmark_weights.provenance.json`, whose weight hash, mandate and portfolio IDs, effective/observed/acquired
times, finality and approval all validate. A bare CSV fails closed and cannot make Tracking Error decision-usable.

The platform risk request is bound to the frozen run and its declared algorithm (`risk_parity` in the current run),
while `source_construction=recommended` identifies the executable column whose verbatim landed weights feed the
`actual_run` layer. The adapter requires exactly 756 sessions for the three-year window and publishes 99% 1-day and
10-day historical VaR, 97.5% 10-day historical Expected Shortfall, a block-bootstrap confidence interval for 10-day
VaR and annualised Tracking Error only when the full sample and lineage pass. The current book returns a structured
unavailable payload because ALAB and SNDK lack structural history back to the required start. No shorter window,
holding deletion or substitute price is used. Tracking Error uses a formal mandate benchmark when supplied;
otherwise the platform metric uses the frozen equal-weight eligible-universe benchmark and is locked to
`decision_usable=false`. Separate SOXX diagnostics may remain visible in the legacy workbench but cannot replace the
frozen platform benchmark identity.

An optional HMAC-signed external ledger checkpoint can be enabled only by supplying all three environment values:

```bash
MARKET_INTEL_PAPER_CHECKPOINT_DIR=/separately-administered/path
MARKET_INTEL_PAPER_CHECKPOINT_KEY_ID=paper-witness-v1
MARKET_INTEL_PAPER_CHECKPOINT_HMAC_KEY_B64='<base64 key containing at least 32 bytes>'
```

Creation, paper steps and kill-switch events verify or advance this append-only checkpoint using compare-and-swap.
An invalid, divergent or unavailable witness blocks a new mutation before ledger changes. If the local ledger has
already committed and witness publication then fails, the API returns HTTP 202 with
`mutation_state=committed_checkpoint_pending` and audits that exact outcome; it never reports a rejection or implies
rollback. Retrying the same idempotency key may advance a genuinely lagging witness and returns the persisted result
without duplicate orders or fills. A missing witness may initialize from genesis only for ledger sequence 1; once a
ledger is established, witness loss/reset fails closed instead of re-genesis from a later local head. The ledger's
local `.head` remains in the same trust domain and is not an independent witness; the configured directory and
signing key must be separately administered for the checkpoint to provide rollback evidence.

The production `serve()` entry point explicitly calls `sync_current_run()` once before the HTTP listener starts. This is the producer-side
ingestion boundary. All `GET /api/v2/platform/*` handlers resolve already frozen state and never synchronize or
mutate the registry, catalog, paper ledger or usage ledger.

| Endpoint | Content |
|---|---|
| `GET /api/v2/platform/overview` | Current frozen run, blockers, lineage/catalog/registry counts and capability flags |
| `GET /api/v2/platform/strategies` | Stock Select reference adapter plus five uninstalled adapter declarations |
| `GET /api/v2/platform/runs` | Immutable, strategy-version-pinned run records and append-only state events |
| `GET /api/v2/platform/paper` | Virtual sessions or one replayed paper ledger |
| `GET /api/v2/platform/risk` | Frozen-run-bound 3Y/756-session risk contract for the actual landed book; structured unavailable detail includes limiting holdings and lineage |
| `POST /api/v2/platform/paper/sessions` | Create a PAPER/SHADOW session; requires `operate` and is audited |
| `POST /api/v2/platform/paper/{id}/step` | Reconcile one frozen target set through lineage-bound virtual quotes; requires `operate` and is audited |
| `POST /api/v2/platform/paper/{id}/kill-switch` | Persistently block later virtual orders; requires a reason, `operate`, and audit |

Architecture and migration limits: `docs/adr/ADR-001-multi-strategy-platform.md`.

## Access control (P0-3)

Identity is a server-side fact, never a field in the request body.

```bash
python -m market_intel account add cs --role admin --name "C Shen"   # prints a one-time personal token
python -m market_intel account list
python -m market_intel account disable <user>                        # revocation
python -m market_intel audit                                         # log + hash-chain verification
```

Roles: `viewer` (read), `analyst` (write), `pm` / `risk` (write **and** approve), `quantops` (write, operate),
`admin` (all). Maker-checker: an analyst cannot sign off at all, and no one can sign off their own work.
Every mutation appends one hash-chained `AuditEvent`; `verify_chain()` names the exact row if anything is
edited or deleted.

**The loopback shortcut switches itself off as soon as named accounts exist.** Before that, a request from
127.0.0.1 without a token is the local admin (single-user convenience). After that, it is an anonymous
*viewer*: it can read, but writes return 403 until you present your own token. `MARKET_INTEL_TRUST_LOCAL=on`
forces the old behaviour back; `=off` disables it even with no accounts. This matters because otherwise your
own actions would be logged as "local" rather than as you.

Tunnel traffic never inherits local rights: `is_local()` requires a loopback peer **and** the absence of any
proxy header (`cf-connecting-ip`, `x-forwarded-for`, `x-real-ip`, `cf-ray`), so anything arriving through
cloudflared must authenticate. Verified by test and by live probe.

## Internet access
Two layers: **authentication** (always on for non-local requests) and a **Cloudflare quick tunnel** (HTTPS, no port
forwarding, no account).

* Token: `python -m market_intel token` prints the access token (stored in `data_cache/market_intel/access_token`, mode 600;
  `--reset` rotates it). Remote visitors get the `/login` page; the session cookie lasts 30 days. Scripts can send
  `Authorization: Bearer <token>`. Requests from localhost without proxy headers are trusted; everything arriving through
  the tunnel (or any proxy) must authenticate. Login attempts are rate-limited (10 per 10 minutes per IP).
  `MARKET_INTEL_AUTH=off` disables the check (development only).
* Tunnel: `brew install cloudflared`, then `./scripts/install_market_intel.sh --tunnel` installs `com.siyanchen.marketintel.tunnel`
  (launchd, restarts automatically, HTTP/2 transport). `python -m market_intel url` prints the current public URL (also shown
  under **System & data health → Phone access**). A URL is exposed only while the connector has an active edge connection and
  its HTTPS hostname resolves in DNS. Quick-tunnel hostnames change whenever cloudflared restarts (reboot); for a
  **stable hostname** create a named tunnel with your own domain: `cloudflared tunnel login`, `cloudflared tunnel create market-intel`,
  `cloudflared tunnel route dns market-intel intel.yourdomain.com`, then replace the `--url` arguments in the plist with
  `tunnel run market-intel` (config in `~/.cloudflared/config.yml` pointing at `http://127.0.0.1:8787`).
* Phones and other computers: open the public URL, enter the token once (30-day cookie). Quick-tunnel hostnames change on every
  reconnect; after a confirmed expired-tunnel error the service restarts only this public connector, then notifies the Mac desktop
  of the new URL and writes it to `outputs/latest/public_url.txt`.
  For a permanent address run `./scripts/setup_named_tunnel.sh intel.yourdomain.com` once (needs a Cloudflare account with a
  domain; the script logs in through your browser, creates the tunnel, routes DNS and switches the launchd agent).
* The Mac must be awake for the site to be reachable (launchd agents run in your login session): System Settings > Energy >
  prevent automatic sleeping, or `caffeinate -s` while it should stay up.
* Stop exposure at any time: `launchctl unload ~/Library/LaunchAgents/com.siyanchen.marketintel.tunnel.plist`.
* The dashboard is research tooling; keep the token private, rotate it if a device is lost, and prefer Cloudflare Access
  (named tunnel + identity provider) if more people need access.

## Credentials
* LLM provider: **Anthropic** (`ANTHROPIC_API_KEY` or `Anthropic API Key.txt`, model `claude-opus-5`) or **OpenAI** (`OPENAI_API_KEY` or
  `OpenAI API Key.txt`; the best model available to the account is picked: gpt-5.x > gpt-5 > gpt-5-mini > gpt-4.1 > gpt-4o).
  `python -m market_intel set-key` detects the key type from its prefix, stores it (mode 600) and validates it with a test call; the
  launchd service reads the file, not your shell environment. Anthropic wins when both keys exist (`MARKET_INTEL_LLM_PROVIDER=openai`
  to force); `python -m market_intel set-model gpt-5.6-luna` pins a model (validated, stored in `data_cache/market_intel/settings.json`, read by the
  service and the CLI; `--auto` reverts); `MARKET_INTEL_MODEL` overrides both.
* Both SDK clients request gzip-only responses (`Accept-Encoding: gzip`): on this Anaconda build the `httpx2` Brotli/zstd decoders fail
  with `process() takes no keyword arguments`, which the SDKs surface as a generic connection error after an HTTP 200.
* FRED key is read from `FRED API Key.txt` (shared with the pipeline). SEC needs no key.
* `MARKET_INTEL_NO_LLM=1` forces rules-only mode; `MARKET_INTEL_PORT` changes the port. LLM metering and budget
  controls are `MARKET_INTEL_LLM_USAGE_DB`, `MARKET_INTEL_LLM_PRICING_JSON`,
  `MARKET_INTEL_LLM_TOKEN_BUDGET`, `MARKET_INTEL_LLM_COST_BUDGET_USD` and
  `MARKET_INTEL_LLM_BUDGET_WINDOW_SECONDS`; see **LLM usage and budget controls** above.

## Tuning
`market_intel/config.py` (`Settings`): poll intervals, LLM batch size / max items per cycle / effort, half-life,
notification level and cap, price-move floor and sigma, brief hour, retention. `feeds.yaml`: feeds, weights and
cadence; add Google News queries there. `market_intel/watchlist.yaml` (optional) adds extra tickers:
```yaml
tickers:
  SKHY: {name: SK hynix, aliases: [SK hynix, SK Hynix], segment: Memory & Storage}
```
`entities.py` holds aliases and the value-chain theme map (`THEMES`) used for read-through.

## Files
```
market_intel/
  config.py  entities.py  store.py  scoring.py  alerts.py  poller.py  ask.py  brief.py  server.py  menubar.py  __main__.py
  feeds.yaml  static/dashboard.html
  sources/   base.py rss.py sec.py yahoo_news.py google_news.py macro.py prices.py
  classify/  rules.py llm.py
data_cache/market_intel/market_intel.sqlite      event store (WAL, FTS5)
outputs/latest/event_overlay.csv, market_brief.md ; outputs/market_intel/event_overlay.json ; outputs/logs/market_intel.log
tests/test_market_intel.py
```
Research tool for discussion purposes only; not investment advice.

## P1 APIs
All JSON payloads are NaN-free (`research.clean`) and quote the canonical run (`run`: run_id, validation_status, cash, data_health).
Vocabulary: positions are **target-list names** with executable target weights (never "held"); actions are
`analyst review` / `monitor` / `refresh model inputs`; no trade instructions anywhere.

| Endpoint | Content |
|---|---|
| `GET /api/llm/usage` | Read-only persistent usage grouped by provider/model/operation, token definition, explicit-cost availability, rolling-window budget state and recent calls; never initializes or migrates the ledger on read |
| `GET /api/actions?hours=48&since=<iso>` | Action feed (`actions.build`): `counts` {relevant_24h, events_24h, escalations_open, escalations_unread, cases_open, cases_overdue}; `action_required` = warning/critical relevant events of the last `hours` whose direct tickers include a target-list name, one per story, unread first then critical > warning then newest (id, story_id, level, ticker, weight, model_weight, event_type, title, one_liner, published, age, action, read, needs_review, case_id, case_status, n_copies); `portfolio_impact` = every target-list name sorted by \|event_score_7d\| (weight, model_weight, event_score_7d/1d, n_events_7d, n_copies_7d, alert_level, action, last_event_title/at, px_pct, next_earnings); `catalysts` = calendar rows today..+5d with `is_target`/`weight` for target-list earnings, sorted by date then importance; `new_evidence` = relevant non-commentary stories published after `since` (default 24h ago), `count` total, top-10 `items` by materiality; `system_health` = last_cycle (cycle, at_et, duration_s, errors), feeds with errors, llm availability, run data_health (status, stale, price_gaps), polling flag and poll intervals |
| `POST /api/events/read` `{"ids": [...]}` | Bulk mark read (`read: false` in the body to unread); `POST /api/events/{id}/read` is kept |
| `POST /api/events/{id}/ack` `{"by": "...", "ticker": "..."}` | Mark read and ensure + acknowledge the review case for the story x target-list name (`case`, `cases`) |
| `GET /api/cases?status=&ticker=&limit=` | Review cases (`cases.py`, table `cases`): status `open` / `acknowledged` / `in_review` / `decided` / `closed`, `active` (= open+acknowledged+in_review), comma lists, empty = all; each case carries owner, due (created + SLA: `Settings.case_sla_hours` = {critical: 4, warning: 24}), sla_hours, evidence (event ids), decision, no_trade_reason, execution_ref, signoff_by/at, notes [{at, by, text}], plus derived is_open, overdue, hours_to_due, age_hours; `summary` {open, overdue, unacknowledged, by_status} |
| `POST /api/cases` | Manual case: `{ticker, title, level?, owner?, due?, sla_hours?, story_id?, event_id?, evidence?, note?, by?}` |
| `GET /api/cases/{id}` | One case with its evidence events |
| `POST /api/cases/{id}` | Update owner / due / status / decision / no_trade_reason / execution_ref / signoff_by / level / title / note / by (any status may close; acknowledging stamps ack_at/ack_by; a sign-off stamps signoff_at) |
| `POST /api/cases/{id}/ack` | Acknowledge (open -> acknowledged) |
| `GET /api/research/risk` | Risk workbench (`risk_workbench.build`): `benchmark` (equal-weight eligible universe, or `data/benchmark_weights.csv` when present - stated in `name`/`custom`), `weights` (executable, model, cash, invested, reconciles, scale), `active` per name (weight, model, benchmark, active, active_model) + `active_summary`, `exposures` by segment / country / currency, `adr_share`, `taiwan_china` (Taiwan, China, Hong Kong or TSM/UMC), `ai_capex_dependency` (segments containing Compute, Memory, Equipment, Foundry, Interconnect, Servers, Power), `gross_net` (long-only: gross = net = invested, leverage 1.0), `liquidity` (20-day ADV when `long_volume.parquet` exists; days_to_liquidate / cost_impact always null - no NAV or ledger; explicit note), `var` (historical 1-day VaR/CVaR 95/99 from the last 252 common-window daily returns, executable weights, cash returns 0, losses positive), `betas` (realised vs SOXX/SPY over the window), `stress` (SOXX -20%/-35%, SPY -10% via betas; NVDA -25% direct weight only; USD +5% = ADR/non-USD names -5%), `event_concentration` (target names reporting within 14 days, weight sum), `current_vs_target` (current_live_weight null, "no ledger"), `compliance` (max_weight, min_weight, max_segment_weight on model weights, high-risk cap, invested + cash == 1, long-only, executable = model x scale) |
| `GET /api/research/dossier/{ticker}` | Dossier (`dossiers.build`): `identity`, `horizon` 12m, `benchmark` SOXX, `price`, `target_list` (weight / model_weight), `pt_bridge` (DCF / EV-Sales regression / target mean with blend weights from `summary.config.valuation`, blended fair value + recomputed check, upside per method), `consensus` (`current` revenue/eps FY0/FY1, growth, revisions 30d/90d, n_analysts, rec_mean; `prior` from the earliest `outputs/history/runs/*/consensus_snapshot.json`; `revisions` since then; `actual`/`surprise` null - "not available from free data"), `scores` (composite + pillars), `fundamentals`, `strategic` (score, scarcity, risk, thesis, kill_criteria, watch_items, asof, stale), `events` (last 10 relevant), `scenarios` from `data/dossiers/<TICKER>.json` (`{"base": {"pt", "prob", "thesis"}, "bull": ..., "bear": ..., "author", "asof"}`) else null with `scenarios_note`, `expected_return` (blended upside), `downside` (bear PT, else worst model method), `kill_criteria`, `watch_kpis`, run/validation status |
| `GET /api/status` | `db` reports `cases_open` and `escalations_unread` (unread warning/critical on target names, 48h); `llm.usage` embeds the same persistent rolling usage/budget payload |

Serving: responses over 1000 bytes are gzip-compressed (`GZipMiddleware`); `/static/*.min.js` is served with
`Cache-Control: public, max-age=86400`, other static files with `max-age=600`, ETag revalidation unchanged.
Brief titles are session-aware in ET (`brief.session_label`): before 09:30 *Pre-market brief*, 09:30-16:00 *Intraday brief*,
16:00-20:00 *Post-close brief*, after 20:00 *Next-open brief*. The poller opens a review case for every fresh warning/critical
event on a target-list name (one per story x name; the event is appended as evidence on repeats).
Files: `market_intel/actions.py cases.py risk_workbench.py dossiers.py`, `tests/test_p1_backend.py`.

### Portfolio and risk-analysis entry point (2026-09-14)

The Portfolio section now groups the overview, risk analysis, rebalance history, construction comparison and Black-Litterman. The old `/risk` address still works; in the top navigation it sits under Portfolio. Links between the overview and the risk page carry the selected construction: the primary book maps to the published cash-inclusive target, standalone models map to fully invested equity weights. The hypothetical volatility scenario remains explicitly labelled as hypothetical, and its overview and history links use that method's model basis. Cross-page selection only controls what is viewed; it never changes the primary-book configuration, archives, risk calculations or trade execution.
