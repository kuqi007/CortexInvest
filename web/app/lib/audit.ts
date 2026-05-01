import type Database from "better-sqlite3";
import { randomUUID } from "crypto";

type AuditDbName = "config.db" | "trading.db";
const EXCLUDED_AUDIT_TABLES: Record<AuditDbName, Set<string>> = {
  "config.db": new Set(),
  "trading.db": new Set([
    "price_snapshots",
    "market_turnover",
    "market_amo_history",
    "signals",
    "session_snapshots",
    "tick_monitor_events",
    "tick_monitor_state",
    "poller_leader_lease",
    "daily_kline",
    "earnings_calendar",
    "earnings_history",
    "indicator_cache",
    "sentiment_cache",
    "sector_rotation",
    "sector_daily",
    "sector_alerts",
    "stock_daily",
    "stock_data_fetch_snapshots",
    "trading_calendar_cache",
    "morning_briefings",
    "daily_l2_digest",
    "daily_summaries",
    "job_requests",
    "job_runs",
  ]),
};
const SENSITIVE_FIELD_FRAGMENTS = [
  "api_key",
  "apikey",
  "authorization",
  "auth_token",
  "password",
  "private_key",
  "secret",
  "token",
];
const SENSITIVE_VALUE_PATTERNS = [
  /\bBearer\s+[A-Za-z0-9._~+/=-]{12,}/i,
  /\bsk-[A-Za-z0-9_-]{16,}/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/,
];

export type AuditActor = {
  type: string;
  id: string;
  host?: string;
};

export type AuditEventV2 = {
  event_id: string;
  schema_version: 2;
  ts: string;
  ts_ms: number;
  correlation_id: string | null;
  source: string;
  actor: AuditActor;
  action: string;
  entity: string;
  key: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
  db: AuditDbName;
};

export function makeActor(actor: AuditActor): AuditActor {
  return actor.host
    ? { type: actor.type, id: actor.id, host: actor.host }
    : { type: actor.type, id: actor.id };
}

function tsFromMs(tsMs: number): string {
  const secondsMs = Math.floor(tsMs / 1000) * 1000;
  return new Date(secondsMs).toISOString().replace(".000Z", "Z");
}

function rejectSensitiveFields(value: unknown, path: string) {
  if (Array.isArray(value)) {
    value.forEach((child, idx) => rejectSensitiveFields(child, `${path}[${idx}]`));
    return;
  }
  if (typeof value === "string") {
    if (SENSITIVE_VALUE_PATTERNS.some((pattern) => pattern.test(value))) {
      throw new Error(`sensitive audit value is not allowed: ${path}`);
    }
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const lowered = key.toLowerCase();
    if (SENSITIVE_FIELD_FRAGMENTS.some((fragment) => lowered.includes(fragment))) {
      throw new Error(`sensitive audit field is not allowed: ${path}.${key}`);
    }
    rejectSensitiveFields(child, `${path}.${key}`);
  }
}

export function buildAuditEventV2(args: {
  eventId: string;
  tsMs: number;
  source: string;
  actor: AuditActor;
  action: string;
  entity: string;
  key: string;
  dbName: AuditDbName;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
  correlationId?: string | null;
}): AuditEventV2 {
  rejectSensitiveFields(args.actor, "actor");
  rejectSensitiveFields(args.before, "before");
  rejectSensitiveFields(args.after, "after");
  rejectSensitiveFields(args.metadata, "metadata");

  return {
    event_id: args.eventId,
    schema_version: 2,
    ts: tsFromMs(args.tsMs),
    ts_ms: args.tsMs,
    correlation_id: args.correlationId ?? null,
    source: args.source,
    actor: args.actor,
    action: args.action,
    entity: args.entity,
    key: args.key,
    before: args.before,
    after: args.after,
    metadata: args.metadata ?? {},
    db: args.dbName,
  };
}

function insertOutbox(
  db: Database.Database,
  table: "config_audit_outbox" | "trading_audit_outbox",
  expectedDb: AuditDbName,
  event: AuditEventV2,
) {
  if (event.db !== expectedDb) {
    throw new Error(`Expected audit event for ${expectedDb}`);
  }
  ensureOutbox(db, table, expectedDb);
  db.prepare(
    `INSERT INTO ${table} (
      event_id, schema_version, correlation_id, ts, ts_ms, source,
      action, entity, key, db, payload_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).run(
    event.event_id,
    event.schema_version,
    event.correlation_id,
    event.ts,
    event.ts_ms,
    event.source,
    event.action,
    event.entity,
    event.key,
    event.db,
    JSON.stringify(event),
  );
}

function ensureOutbox(
  db: Database.Database,
  table: "config_audit_outbox" | "trading_audit_outbox",
  expectedDb: AuditDbName,
) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS ${table} (
      event_id TEXT PRIMARY KEY,
      schema_version INTEGER NOT NULL DEFAULT 1,
      correlation_id TEXT,
      ts TEXT NOT NULL,
      ts_ms INTEGER NOT NULL,
      source TEXT NOT NULL,
      action TEXT NOT NULL,
      entity TEXT NOT NULL,
      key TEXT NOT NULL,
      db TEXT NOT NULL DEFAULT '${expectedDb}' CHECK (db = '${expectedDb}'),
      payload_json TEXT NOT NULL,
      flushed_at TEXT,
      flushed_at_ms INTEGER,
      flush_id TEXT,
      flush_started_at_ms INTEGER,
      CHECK (length(trim(payload_json)) > 0)
    );
  `);
  const existingCols = new Set(
    (db.pragma(`table_info(${table})`) as { name: string }[]).map((c) => c.name),
  );
  if (!existingCols.has("flushed_at")) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN flushed_at TEXT`);
  }
  if (!existingCols.has("flushed_at_ms")) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN flushed_at_ms INTEGER`);
  }
  if (!existingCols.has("flush_id")) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN flush_id TEXT`);
  }
  if (!existingCols.has("flush_started_at_ms")) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN flush_started_at_ms INTEGER`);
  }
  const prefix = table === "config_audit_outbox" ? "config" : "trading";
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_${prefix}_audit_outbox_pending
      ON ${table}(ts_ms, event_id)
      WHERE flushed_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_${prefix}_audit_outbox_correlation
      ON ${table}(correlation_id, ts_ms)
      WHERE correlation_id IS NOT NULL;
  `);
}

export function insertConfigAuditOutbox(db: Database.Database, event: AuditEventV2) {
  insertOutbox(db, "config_audit_outbox", "config.db", event);
}

export function insertTradingAuditOutbox(db: Database.Database, event: AuditEventV2) {
  insertOutbox(db, "trading_audit_outbox", "trading.db", event);
}

export function auditTableEnabled(dbName: AuditDbName, table: string) {
  return !EXCLUDED_AUDIT_TABLES[dbName].has(table);
}

export function recordDbChangeBestEffort(
  db: Database.Database,
  args: {
    dbName: AuditDbName;
    table: string;
    action: string;
    key: string;
    source: string;
    actor: AuditActor;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
    metadata?: Record<string, unknown>;
    correlationId?: string | null;
  },
) {
  if (!auditTableEnabled(args.dbName, args.table)) return false;
  try {
    const event = buildAuditEventV2({
      eventId: randomUUID(),
      tsMs: Date.now(),
      source: args.source,
      actor: args.actor,
      action: args.action,
      entity: args.table,
      key: args.key,
      dbName: args.dbName,
      before: args.before,
      after: args.after,
      metadata: args.metadata,
      correlationId: args.correlationId,
    });
    if (args.dbName === "config.db") {
      insertConfigAuditOutbox(db, event);
    } else {
      insertTradingAuditOutbox(db, event);
    }
    return true;
  } catch (error) {
    console.warn("best-effort audit failed", error);
    return false;
  }
}
