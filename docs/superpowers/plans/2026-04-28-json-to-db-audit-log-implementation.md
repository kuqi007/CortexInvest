# JSON to DB Audit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate runtime JSON reads/writes under `src/data` to SQLite-backed runtime state, leaving JSON only as audit/event history or explicit archive/restore material.

**Architecture:** `config.db` remains the authority for user-maintained config/watchlist state, while `trading.db` owns operational runtime data. Writes record an audit envelope into a same-DB outbox in the same transaction, then a flush tool serializes events to JSONL with hash-chain and manifest metadata. Rollout is controlled by explicit gates and batch evidence; JSON fallback is never reintroduced as a production recovery path.

**Tech Stack:** Python 3.13 + SQLite (`sqlite3`), Next.js 15 + `better-sqlite3`, uv/pytest, npm test, shell/pre-commit checks.

**Source Spec:** `docs/superpowers/specs/2026-04-28-json-to-db-audit-log-design.md`

---

## Scope Split

This is an umbrella plan for seven reviewable PRs. Each PR must remain deployable with rollout gates off, and each Batch evidence must be completed in order: Batch 0 -> Batch 1 -> Batch 2 -> Batch 3 -> Batch 4. Do not turn a gate on just because its PR merged.


| PR                                   | Batch              | Gate                                                      |
| ------------------------------------ | ------------------ | --------------------------------------------------------- |
| PR 1: Schema + connection helpers    | Batch 0 foundation | none yet                                                  |
| PR 2: Audit outbox + flush + CI deny | Batch 0 completion | `JSON_DB_AUDIT_ENABLED`                                   |
| PR 3: Config DB-only + poller lease  | Batch 1            | `JSON_DB_ONLY_CONFIG`                                     |
| PR 4: Runtime JSON readers           | Batch 2            | `JSON_DB_ONLY_RUNTIME`                                    |
| PR 5: Archive exporter               | Batch 3            | no gate; scheduled export disabled until Batch 2 evidence |
| PR 6: Restore CLI                    | Batch 4            | `AUDIT_RESTORE_APPLY_ENABLED`                             |
| PR 7: Cleanup docs/tests/evidence    | Cleanup            | none                                                      |


## File Structure

### Shared Python

- Modify: `src/sim_trading/db.py`
  - Owns centralized DDL for `config.db` and `trading.db`.
  - Adds connection helper behavior required by the spec: `PRAGMA foreign_keys=ON`, `busy_timeout`, journal mode, test overrides.
  - Adds audit outbox, restore state, runtime target tables, `poller_leader_lease`, and indexes.
- Create: `src/utils/audit_log.py`
  - Builds canonical audit envelopes.
  - Validates schema fields, `ts`/`ts_ms`, DB target, action/entity/key.
  - Provides Python outbox insertion helpers that must run inside the caller transaction.
- Create: `src/utils/audit_hash.py`
  - Implements canonical JSON dump and hash-chain generation/verification.
  - Owns golden-vector tests for Unicode, floats, large integer strings, and Chinese fields.
- Create: `src/utils/path_safety.py`
  - Implements repo-root-relative path validation, symlink rejection, canonical realpath/NFC checks, and fd-bound restore materialization.
- Create: `src/utils/rollout_gates.py`
  - Reads rollout gates from environment.
  - Enforces gate order and production fail-fast rules.
- Create: `src/tools/audit_flush.py`
  - Flushes `config_audit_outbox` and `trading_audit_outbox` to JSONL.
  - Uses DB-adjacent file locks, sidecar files, fsync, tail verification, manifest generation, and DB marking.
- Create: `src/tools/db_snapshot_exporter.py`
  - Exports daily DB snapshots into `src/data/archive/YYYY-MM-DD/`.
  - Disabled for scheduled production export until Batch 2 evidence and `JSON_DB_ONLY_RUNTIME=true`.
- Create: `src/tools/audit_restore.py`
  - Dry-runs and applies JSONL/archive restores.
  - Requires stdin/prompt token in production and fails closed with active writers.

### Python Runtime Migration

- Modify: `src/utils/config_reader.py`
  - Remove production JSON fallback.
  - Read monitor config from `config.db` only after `JSON_DB_ONLY_CONFIG`.
- Modify: `src/tools/market_data_poller.py`
  - Read watchlist from DB.
  - Write market snapshots to `trading.db`.
  - Own `poller_leader_lease` heartbeat.
- Modify: `src/tools/monitor_lock.py`
  - Stop using `market_data.json` mtime as remote poller activity.
  - Read `poller_leader_lease`.
- Modify: `src/tools/stock_monitor.py`, `src/tools/stock_notifier.py`
  - Stop runtime JSON fallback for market/config reads.
  - Preserve `DeltaAlertEngine` authority.
- Modify: `src/tools/tick_monitor.py`
  - Move `tick_monitor_state.json` runtime state to DB.
- Modify: `src/tools/l2_strategy_engine.py`, `src/tools/l2_strategy_daemon.py`
  - Move L2 config/signals runtime reads/writes to DB tables.
- Modify: `src/tools/trading_calendar.py`, `src/tools/news_crawler.py`, `src/tools/daily_summary_generator.py`
  - Move runtime cache/summary writes to DB; archive exporter handles JSON snapshots.
- Modify: `src/tools/screenshot_stock_import.py`, `src/tools/monitor_config_db_migrator.py`
  - Convert one-time imports to DB + outbox with dry-run and idempotency ledger.

### Next.js

- Modify: `web/app/lib/db.ts`
  - Enable `foreign_keys=ON`.
  - Centralize busy timeout/journal behavior.
  - Keep server-only database usage.
- Create: `web/app/lib/audit.ts`
  - Server-only helper for audit envelope validation and outbox writes from API routes.
- Modify: `web/app/api/config/route.ts`, `web/app/api/config/[symbol]/route.ts`
  - Write config DB changes and outbox event in one transaction.
- Modify: `web/app/api/trade-plans/route.ts`
  - Move `trade_plans.json` runtime writes to `trading.db` + outbox.
- Modify: `web/app/api/metrics/route.ts`, `web/app/api/summary/route.ts`, `web/app/api/sector/route.ts`
  - Read runtime data from DB/archive-appropriate sources only.

### Tests and Tooling

- Modify: `.gitignore`
  - Ignore `src/data/audit/*.jsonl`, `src/data/archive/**/*.json`, `src/data/backups/**/`*, sidecars, manifests as needed.
- Create: `scripts/check_no_runtime_json_readers.py`
  - Fails when runtime code reads old root JSON files outside fixtures/archive/audit/restore tools.
- Create: `scripts/check_sensitive_paths.py`
  - Fails on staged or committed audit/archive/backup paths after canonical normalization.
- Create: `src/utils/test_audit_log.py`, `src/utils/test_audit_hash.py`, `src/utils/test_path_safety.py`, `src/utils/test_rollout_gates.py`
  - Unit tests for envelope/hash/path/gates.
- Modify/Add: `src/sim_trading/test_market_data_poller.py`, `src/tools/test_tick_monitor.py`, `src/tools/test_stock_monitor.py`, `web/app/api/trade-plans/route.test.ts`, `web/app/api/config/route.test.ts`
  - Migration and route coverage.

---

## PR 1: Schema + Connection Pragmas

**Files:**

- Modify: `src/sim_trading/db.py`
- Create: `src/utils/rollout_gates.py`
- Create: `src/utils/test_rollout_gates.py`
- Add tests in existing DB test area or create `src/sim_trading/test_db_schema.py`

### Task 1.1: Add DB helper invariants

- [x] **Step 1: Write failing tests for connection pragmas**

Create `src/sim_trading/test_db_schema.py` with tests that open temp config/trading DBs through existing test override hooks and assert:

```python
def test_config_connection_enables_foreign_keys_and_busy_timeout():
    conn = db.get_config_connection()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 15000


def test_trading_connection_enables_foreign_keys_and_busy_timeout():
    conn = db.get_connection()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 15000
```

Run: `uv run pytest src/sim_trading/test_db_schema.py -q`

Expected: FAIL until the connection helpers set `foreign_keys`.

- [x] **Step 2: Update connection helpers**

In `src/sim_trading/db.py`, ensure every connection helper executes:

```python
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA busy_timeout=15000")
```

Keep `config.db` on DELETE journal and `trading.db` on WAL as documented in this repo; do not change unrelated trading logic.

- [x] **Step 3: Verify tests**

Run: `uv run pytest src/sim_trading/test_db_schema.py -q`

Expected: PASS.

### Task 1.2: Add centralized DDL

- [x] **Step 1: Add tests for required tables and indexes**

Extend `src/sim_trading/test_db_schema.py`:

```python
REQUIRED_CONFIG_TABLES = {
    "monitor_watchlist",
    "monitor_settings",
    "tag_meta",
    "config_audit_outbox",
    "config_restore_sessions",
    "config_restore_applied_events",
    "poller_leader_lease",
    "l2_strategy_config",
    "signal_rules",
}

REQUIRED_TRADING_TABLES = {
    "trading_audit_outbox",
    "trading_restore_sessions",
    "trading_restore_applied_events",
    "trade_plans",
    "tick_monitor_state",
    "trading_calendar_cache",
    "sentiment_cache",
    "daily_summaries",
    "morning_briefings",
}


def table_names(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_init_creates_config_tables():
    db.init_config_db()
    assert REQUIRED_CONFIG_TABLES <= table_names(db.get_config_connection())


def test_fresh_init_creates_trading_tables():
    db.init_db()
    assert REQUIRED_TRADING_TABLES <= table_names(db.get_connection())
```

Run: `uv run pytest src/sim_trading/test_db_schema.py -q`

Expected: FAIL until DDL is added.

- [x] **Step 2: Add DDL to `CONFIG_SCHEMA` and `TRADING_SCHEMA`**

Add audit outbox columns exactly aligned with the spec:

```sql
event_id TEXT PRIMARY KEY,
schema_version INTEGER NOT NULL DEFAULT 1,
correlation_id TEXT,
ts TEXT NOT NULL,
ts_ms INTEGER NOT NULL,
source TEXT NOT NULL,
action TEXT NOT NULL,
entity TEXT NOT NULL,
key TEXT NOT NULL,
db TEXT NOT NULL CHECK (...),
payload_json TEXT NOT NULL,
flushed_at TEXT,
flushed_at_ms INTEGER,
flush_id TEXT,
flush_started_at_ms INTEGER
```

Add partial pending/correlation indexes. Add restore sessions/applied-events, keeping applied-events rows permanent. Add `poller_leader_lease` with `_ms` timestamps and generation. Add runtime target tables using conservative JSON TEXT columns only where the spec allows DB-internal JSON.

- [x] **Step 3: Verify fresh init**

Run: `uv run pytest src/sim_trading/test_db_schema.py -q`

Expected: PASS.

### Task 1.3: Add rollout gate parser

- [x] **Step 1: Create `src/utils/rollout_gates.py`**

Implement:

```python
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class RolloutGates:
    audit_enabled: bool
    config_db_only: bool
    runtime_db_only: bool
    restore_apply_enabled: bool
    allow_json_mixed_mode: bool


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def read_rollout_gates() -> RolloutGates:
    return RolloutGates(
        audit_enabled=_enabled("JSON_DB_AUDIT_ENABLED"),
        config_db_only=_enabled("JSON_DB_ONLY_CONFIG"),
        runtime_db_only=_enabled("JSON_DB_ONLY_RUNTIME"),
        restore_apply_enabled=_enabled("AUDIT_RESTORE_APPLY_ENABLED"),
        allow_json_mixed_mode=_enabled("ALLOW_JSON_MIXED_MODE"),
    )


def validate_gate_order(gates: RolloutGates, *, production: bool) -> None:
    if gates.config_db_only and not gates.audit_enabled:
        raise RuntimeError("JSON_DB_ONLY_CONFIG requires JSON_DB_AUDIT_ENABLED")
    if gates.runtime_db_only and not gates.config_db_only:
        raise RuntimeError("JSON_DB_ONLY_RUNTIME requires JSON_DB_ONLY_CONFIG")
    if gates.restore_apply_enabled and not gates.audit_enabled:
        raise RuntimeError("AUDIT_RESTORE_APPLY_ENABLED requires JSON_DB_AUDIT_ENABLED")
    if production and gates.allow_json_mixed_mode:
        raise RuntimeError("ALLOW_JSON_MIXED_MODE is forbidden in production")
```

- [x] **Step 2: Test gate ordering**

Create tests in `src/utils/test_rollout_gates.py` for each invalid combination.

Run: `uv run pytest src/utils/test_rollout_gates.py -q`

Expected: PASS.

---

## PR 2: Audit Envelope, Outbox Flush, Manifest, and CI Deny

**Files:**

- Create: `src/utils/audit_log.py`
- Create: `src/utils/audit_hash.py`
- Create: `src/utils/path_safety.py`
- Create: `src/tools/audit_flush.py`
- Create: `scripts/check_sensitive_paths.py`
- Modify: `.gitignore`
- Create tests under `src/utils/`

### Task 2.1: Implement audit envelope

- [x] **Step 1: Write envelope tests**

Create `src/utils/test_audit_log.py` covering:

```python
def test_envelope_requires_matching_ts_fields():
    with pytest.raises(ValueError):
        build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
            ts_ms=1777376520000,
            ts="2026-04-28T00:00:00Z",
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="config.db",
            before=None,
            after={"shares": 800},
        )
```

- [x] **Step 2: Implement `build_audit_event()`**

Return a dict with `event_id`, `schema_version`, `ts`, `ts_ms`, `correlation_id`, `source`, `action`, `entity`, `key`, `before`, `after`, `db`. Validate `db_name in {"config.db", "trading.db"}` and derive `ts` from `ts_ms` unless the caller passes an exact match.

- [x] **Step 3: Add transaction-local outbox insert helpers**

Provide `insert_config_outbox(conn, event)` and `insert_trading_outbox(conn, event)`. They must not open a new connection. They insert both flattened columns and `payload_json`.

### Task 2.2: Implement hash and manifest utilities

- [x] **Step 1: Add golden-vector tests**

Create `src/utils/test_audit_hash.py` with a fixture containing Chinese fields, NFC/NFD strings, large integer strings, and floats. Assert canonical dump bytes and hash values are stable.

- [x] **Step 2: Implement canonical dump and hash**

Use Python `json.dumps(event_without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.

- [x] **Step 3: Implement manifest writer**

Add a function that writes `<jsonl>.manifest.json` via temp file + fsync + atomic replace with fields:

```json
{
  "schema_version": 1,
  "file_name": "config_events.jsonl",
  "file_sha256": "sha256:...",
  "first_event_id": "...",
  "last_event_id": "...",
  "first_ts_ms": 0,
  "last_ts_ms": 0,
  "row_count": 0,
  "generated_at_ms": 0,
  "generator_version": "audit_flush:1"
}
```

### Task 2.3: Implement `audit_flush`

- [x] **Step 1: Write flush recovery tests**

Use a temp DB and simulate append-before-DB-commit recovery, corrupt JSONL fail-closed behavior, and normal config flush. Assert one JSONL line per event and no duplicate append.

- [x] **Step 2: Implement flush flow**

In `src/tools/audit_flush.py`:

1. Acquire `<db>.flush.lock`.
2. `BEGIN IMMEDIATE`.
3. Select `flushed_at IS NULL`.
4. Mark `flush_id` and `flush_started_at_ms`.
5. Write sidecar JSONL with `prev_hash/hash`.
6. fsync sidecar, append/merge to target JSONL, fsync target and directory.
7. Generate manifest and verify file SHA.
8. Mark rows with `flushed_at` and `flushed_at_ms`.

### Task 2.4: Add path deny checks

- [x] **Step 1: Update `.gitignore`**

Add:

```gitignore
src/data/audit/*.jsonl
src/data/audit/*.manifest.json
src/data/archive/**/*.json
src/data/backups/**/*
```

- [x] **Step 2: Implement `scripts/check_sensitive_paths.py`**

It must reject canonical paths under `src/data/audit/`, `src/data/archive/`, or `src/data/backups/`, including forced-add paths.

- [x] **Step 3: Add a shell test or documented command**

Run:

```bash
python scripts/check_sensitive_paths.py --paths src/data/audit/config_events.jsonl
```

Expected: non-zero exit.

---

## PR 3: Config DB-Only and Poller Lease

**Files:**

- Modify: `src/utils/config_reader.py`
- Modify: `src/tools/market_data_poller.py`
- Modify: `src/tools/monitor_lock.py`
- Modify: `src/tools/stock_monitor.py`
- Modify: `src/tools/stock_notifier.py`
- Modify: `start_ai_investor_full.sh`
- Modify docs listed in the spec.

### Task 3.1: Remove config JSON fallback

- **Step 1: Add tests deleting config JSON**

Extend existing config/poller tests so `monitor_config.json` is absent and `read_monitor_config()` still returns DB rows.

- **Step 2: Change `read_monitor_config()`**

Read from `config.db` only. If DB is unavailable, raise an explicit error. Do not read `monitor_config.json`.

- **Step 3: Verify**

Run: `uv run pytest src/sim_trading/test_market_data_poller.py -q`

Expected: PASS.

### Task 3.2: Replace market-data mtime lock with DB lease

- **Step 1: Add lease tests**

Test that lease acquisition increments `generation`, respects `lease_until_ms`, and does not trust PID.

- **Step 2: Update poller heartbeat**

In `src/tools/market_data_poller.py`, renew `poller_leader_lease` every poller cycle with `BEGIN IMMEDIATE`.

- **Step 3: Update `monitor_lock.py`**

Read `poller_leader_lease` instead of `market_data.json` mtime.

### Task 3.3: Batch 1 evidence

- **Step 1: Run reader inventory**

Run:

```bash
rg "monitor_config\\.json|market_data\\.json" src web start_ai_investor_full.sh
```

Expected: no runtime reads outside migration/test/docs.

- **Step 2: Delete/rename old JSON locally and smoke**

Run:

```bash
uv run pytest src/sim_trading/test_market_data_poller.py -q
uv run pytest src/sim_trading/test_sim_trading.py -m smoke -q
```

Expected: PASS.

---

## PR 4: Runtime JSON Readers

**Files:**

- Modify: `src/tools/tick_monitor.py`
- Modify: `src/tools/l2_strategy_engine.py`
- Modify: `src/tools/l2_strategy_daemon.py`
- Modify: `src/tools/trading_calendar.py`
- Modify: `src/tools/news_crawler.py`
- Modify: `src/tools/daily_summary_generator.py`
- Modify: `web/app/api/trade-plans/route.ts`
- Modify related route tests.

### Task 4.1: Move trade plans to `trading.db`

- **Step 1: Extend `web/app/api/trade-plans/route.test.ts`**

Assert create/update/delete/reset writes `trading.db` rows and creates outbox events, with `trade_plans.json` absent.

- **Step 2: Update route implementation**

Use `web/app/lib/db.ts` and `web/app/lib/audit.ts` inside one transaction. Do not write `trade_plans.json`.

- **Step 3: Verify**

Run: `cd web && npm test -- trade-plans`

Expected: PASS.

### Task 4.2: Move daemon/cache state to DB

- **Step 1: Add tests for each migrated runtime JSON**

Cover `tick_monitor_state`, L2 config/signals, calendar/news cache, and daily summaries with old JSON missing.

- **Step 2: Implement DB repositories**

Keep repositories local and small; use existing `db.py` helpers. JSON TEXT is allowed only for DB-internal flexible payloads, not root runtime fallback.

- **Step 3: Run inventory**

Run:

```bash
rg "trade_plans\\.json|tick_monitor_state\\.json|l2_strategy|signal_rules|daily_summary|calendar_cache|sentiment_cache" src web
```

Expected: only DB migration/import/archive/test/doc references remain.

---

## PR 5: Archive Exporter

**Files:**

- Create: `src/tools/db_snapshot_exporter.py`
- Create tests under `src/tools/test_db_snapshot_exporter.py`

### Task 5.1: Implement disabled-by-default exporter

- **Step 1: Write dry-run tests**

Test that production scheduled export refuses to run unless Batch 2 evidence is present and `JSON_DB_ONLY_RUNTIME=true`.

- **Step 2: Implement exporter**

Export daily files:

- `price_snapshots_YYYY-MM-DD.json`
- `market_turnover_YYYY-MM-DD.json`
- `l2_signals_YYYY-MM-DD.json`
- `session_snapshots_YYYY-MM-DD.json`
- `daily_summary_YYYY-MM-DD.json`

Use temp file + fsync + atomic replace.

- **Step 3: Implement dry-run restore validation**

Validate UPSERT keys without mutating DB.

---

## PR 6: Restore CLI

**Files:**

- Create: `src/tools/audit_restore.py`
- Add restore tests under `src/tools/test_audit_restore.py`

### Task 6.1: Implement restore dry-run

- **Step 1: Test path rejection**

Cover `..`, symlink, wrong extension, wrong `--kind`, oversized line, unknown fields, and hash-chain break.

- **Step 2: Implement dry-run**

Use `path_safety.materialize_restore_bytes()`, compute source SHA-256, row fingerprint, and emit a one-time token. Do not print payload details by default.

### Task 6.2: Implement restore apply

- **Step 1: Test token and active-writer fail-closed**

Production apply must refuse argv token unless explicitly in local/test mode, require stdin/prompt token, refuse active writer, and refuse fingerprint drift.

- **Step 2: Implement apply sequence**

Follow spec order: maintenance checks, flush/exclusive lock, backup, integrity check, session marker, replay, applied-events ledger, verification, completed marker, restore audit event.

- **Step 3: Test abort/resume**

Cover stale started sessions, `--abort-session`, `--resume-session`, and mismatch requiring manual DB backup restore.

---

## PR 7: Cleanup, Docs, and Evidence

**Files:**

- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/CONFIG_API.md`
- Modify: `docs/MONITORING.md`
- Modify: `docs/notification-system.md`
- Create evidence docs under `docs/superpowers/evidence/`

### Task 7.1: Clean stale JSON authority claims

- **Step 1: Run docs inventory**

Run:

```bash
rg "JSON|monitor_config\\.json|market_data\\.json|trade_plans\\.json|fallback|快照|热备" CLAUDE.md AGENTS.md docs .claude
```

- **Step 2: Update docs**

Replace runtime JSON authority statements with DB authority + audit/archive statements. Keep `stocks/` research JSON explicitly out of scope.

### Task 7.2: Generate final evidence

- **Step 1: Create evidence template**

Use:

```markdown
# JSON DB Migration Batch N Evidence

- Branch:
- Commit:
- Gate:
- Reader inventory:
- Schema check:
- Tests:
- Skipped checks:
- Redaction check:
- Rollback plan:
```

- **Step 2: Run redaction check before sharing**

Evidence must not contain audit payload, full DB backup path, restore token, real cost/shares, SQL bind values, or restore payload.

---

## Self-Review Checklist

- Spec coverage: This plan maps rollout gates, DDL/outbox, flush/manifest, config DB-only, runtime JSON readers, archive exporter, restore CLI, evidence, rollback, and docs cleanup to PRs.
- Placeholder scan: No "TBD" or "implement later" placeholders are used. Each PR has concrete files, commands, and acceptance checks.
- Type/name consistency: Gate names, DB names, table names, and tool names match the spec.
- Risk note: This is an umbrella plan. Before implementing PR 1, create a narrower PR 1 execution checklist if the implementer wants line-by-line patches.

## Execution Options

Plan implementation should start with PR 1. Do not implement PR 2+ until PR 1 tests pass and the schema evidence is produced.

Two execution options:

1. **Subagent-Driven (recommended):** dispatch a fresh implementation subagent per PR, review between PRs, then run focused tests.
2. **Inline Execution:** implement PR 1 in this session, checkpoint after schema tests, then continue one PR at a time.

