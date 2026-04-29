import type Database from "better-sqlite3";

type AuditDbName = "config.db" | "trading.db";
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

export function insertConfigAuditOutbox(db: Database.Database, event: AuditEventV2) {
  insertOutbox(db, "config_audit_outbox", "config.db", event);
}

export function insertTradingAuditOutbox(db: Database.Database, event: AuditEventV2) {
  insertOutbox(db, "trading_audit_outbox", "trading.db", event);
}
