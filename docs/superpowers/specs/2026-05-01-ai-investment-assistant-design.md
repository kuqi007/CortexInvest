# AI Investment Assistant Implementation Design

## Goal

Build the existing stock monitoring system into a reliable AI investment assistant. The system should watch markets, track fundamentals and earnings, generate post-close strategy, maintain executable trade plans, detect main-wave and trend-reversal opportunities, and keep an auditable record of recommendations and outcomes.

The design preserves existing authority rules:

- `config.db` remains the source of truth for real holdings, watchlist, monitor settings, and alert rules.
- `trading.db` stores operational data, derived AI events, jobs, recommendations, and summaries.
- `market_data_poller.py` is the only producer of market data.
- `stock_notifier.py` is the only owner of user-facing `alert_events` and external notifications.
- AI engines emit structured observations; they do not directly send Feishu/macOS notifications or mutate config tables.

## Current Gaps

- `earnings_calendar_daemon.py` is not managed by the main startup script.
- The current earnings trigger path is split between API DB flags and daemon trigger files.
- `daily_summaries` and `morning_briefings` schema contracts were inconsistent with the generator and Web API.
- Daily summary did not persist the generated Markdown report in the DB.
- New AI features need a common event stream, job tracking, health visibility, and safe recommendation workflow before adding more signals.

## Phase 0: Schema, Jobs, and Operating Foundation

This phase must be implemented before new AI engines.

### Schema Foundation

Add versioned schema tracking:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at_ms INTEGER NOT NULL,
  description TEXT NOT NULL
);
```

Reconcile runtime summary tables:

```sql
CREATE TABLE IF NOT EXISTS daily_summaries (
  date TEXT PRIMARY KEY,
  market TEXT NOT NULL DEFAULT '',
  stats_json TEXT NOT NULL DEFAULT '{}',
  per_stock_json TEXT,
  report_md TEXT,
  generated_at INTEGER
);

CREATE TABLE IF NOT EXISTS morning_briefings (
  date TEXT PRIMARY KEY,
  generated_at TEXT NOT NULL,
  content_json TEXT NOT NULL
);
```

Migrations from legacy `summary_json/briefing_json` tables must preserve existing rows and be idempotent.

### Job Tracking

Replace trigger files and ad hoc flags with DB-backed jobs:

```sql
CREATE TABLE IF NOT EXISTS job_requests (
  id TEXT PRIMARY KEY,
  job_type TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  request_payload_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  correlation_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  claimed_at_ms INTEGER,
  completed_at_ms INTEGER,
  UNIQUE(job_type, correlation_id)
);

CREATE TABLE IF NOT EXISTS job_runs (
  id TEXT PRIMARY KEY,
  request_id TEXT,
  job_type TEXT NOT NULL,
  runner TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at_ms INTEGER NOT NULL,
  finished_at_ms INTEGER,
  heartbeat_at_ms INTEGER,
  input_json TEXT,
  output_json TEXT,
  error TEXT,
  correlation_id TEXT NOT NULL,
  FOREIGN KEY(request_id) REFERENCES job_requests(id)
);
```

`POST /api/earnings {"action":"trigger_check"}` should create a `job_requests` row. `earnings_calendar_daemon.py` should claim pending requests and write `job_runs`.

### AI Event Stream

Add a normalized AI event index:

```sql
CREATE TABLE IF NOT EXISTS ai_investment_events (
  id TEXT PRIMARY KEY,
  event_date TEXT NOT NULL,
  symbol TEXT,
  name TEXT,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  delivery_scope TEXT NOT NULL DEFAULT 'web_only',
  verdict TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  dedupe_key TEXT NOT NULL,
  source_record_id TEXT,
  source_run_id TEXT,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  metrics_json TEXT,
  recommendation_json TEXT,
  notify_status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(dedupe_key)
);
```

Engine-specific tables may keep detailed evidence and state. `ai_investment_events` is the normalized review/notification index. `alert_events` remains the final notification ledger.

## Phase 1: Automation Reliability

- Add earnings daemon management to `start_ai_investor_full.sh`.
- Make earnings manual trigger use only `job_requests/job_runs`.
- Add `/api/health` for service status, freshness, and recent job failures.
- Add daily summary fallback if the post-close window was missed.
- Ensure `/api/summary` returns persisted `report_md` as `report`.

## Phase 2: Earnings Defense

The earnings workflow should:

- scan holdings and star stocks for upcoming reports
- generate T-5, T-3, T-1, and T-day risk events
- compare reported data with prior periods and forecasts
- emit `ai_investment_events` for notifier and daily review
- create daily action recommendations for high-risk holdings

Engines should set `severity` and `delivery_scope`; `stock_notifier.py` handles actual Feishu/macOS dispatch.

## Phase 3: Main-Wave Detection

Add a main-wave score using:

- price trend
- volume expansion
- moving average alignment
- relative strength vs sector/index
- L2/capital-flow confirmation
- risk penalties

Main-wave alerts require market-regime gates:

- `risk_on`: normal thresholds
- `neutral`: Web/daily first, stronger confirmation for Feishu
- `risk_off` or `panic`: suppress buy/add alerts unless score is extreme and sector relative strength is top tier

Only high-quality classifications such as `breakout_confirmed` can escalate beyond Web/daily.

## Phase 4: 15% Trend Reversal

For holdings and star stocks, detect:

- rebound of at least 15% from a meaningful bottom
- drawdown of at least 15% from a meaningful top

Classify events as:

- `reversal_confirmed`
- `rebound_unconfirmed`
- `bear_market_bounce`
- `exhaustion_rebound`
- `breakdown_confirmed`
- `pullback_unconfirmed`
- `trend_reset`

Suppress Feishu escalation when source data is stale, the move is a one-day spike, price is near resistance without volume, recent bearish earnings/news exists, or a recent same-symbol signal failed.

## Phase 5: Daily Investment Review

Add a daily review orchestrator that reads:

- alerts
- trade plans
- earnings events
- main-wave scores
- trend-reversal events
- portfolio risk snapshots
- watchlist/holding config
- news sentiment

The output should include market review, holding review, star stock review, anomaly shortlist, earnings risks, main-wave candidates, trend-reversal events, tomorrow action plan, and recommended trade-plan/alert updates.

## Recommendation Workflow

Recommendations should use a state machine:

- `draft`
- `pending_review`
- `approved`
- `applying`
- `applied`
- `apply_failed`
- `rejected`
- `expired`

`apply` must go through existing APIs and audit paths:

- trade-plan mutations through `/api/trade-plans`
- watchlist/config/alert-rule mutations through `POST /api/config`
- no direct Web writes to config-owned tables
- no broker order placement

Actionable recommendations must include action, price zone or trigger, size, stop-loss, invalidation, upside/downside, risk/reward, max loss, time horizon, and why action is needed now. Missing fields become `needs_review` or `watch`.

## Decision Ledger and Portfolio Risk

Add a decision ledger for AI advice accountability:

- recommended action
- user decision
- execution/application status
- thesis and invalidation
- linked source events
- 1/5/20-day outcomes
- max favorable/adverse excursion
- lesson learned

Daily portfolio risk should compute total market value, deployable capital, single-stock concentration, sector/theme concentration, total capital at risk, missing stop symbols, and risk budget usage.

## Web UX

Add a Today Action Center with:

- Must Decide Today
- Ready To Apply
- Watch Only
- Data Issues

Cards should show symbol, action, priority, confidence, current price, freshness, rationale, source event IDs, and proposed payload preview. `Apply` appears only after approval and fresh risk checks.

All new AI APIs should use a standard response envelope with `success`, `message`, `data`, `timestamp`, `freshness`, and `warnings`.

## Feishu Contract

AI Feishu messages should be rendered from typed payloads containing message type, event ID, dedupe key, symbol, priority, verdict, confidence, price, freshness, conclusion, evidence, action suggestion, invalidation, stop-loss, and dashboard URL.

Messages must say `建议审批`, `待确认`, or `观察`; they must never imply a real trade has executed.

## Testing and Validation

Before enabling Feishu for new signal types:

- replay 6-12 months of data
- measure precision, forward returns, max adverse excursion, false positives by regime, and alert frequency
- keep signals Web-only until at least 20 samples are reviewed
- store `signal_version`/`model_version` and threshold snapshots

## Recommended First PR Sequence

1. Schema foundation:
  - versioned migration runner
  - `schema_migrations`
  - `job_requests`
  - `job_runs`
  - `ai_investment_events`
  - `daily_summaries.report_md`
  - migration/init tests
2. Earnings trigger contract:
  - `/api/earnings` inserts `job_requests`
  - daemon claims jobs and writes `job_runs`
3. Health and startup observability:
  - `/api/health`
  - earnings daemon in start/status/stop
4. Daily summary contract:
  - persist and expose `report_md`
  - fallback summary window
5. Notifier AI-event skeleton:
  - consume pending AI events
  - convert eligible events to `alert_events`

Main-wave, trend-reversal, recommendation apply APIs, Today Action Center, and new Feishu signal escalation are intentionally out of scope for the first PR.