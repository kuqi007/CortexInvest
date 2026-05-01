import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let tempDir: string;
let configDbPath: string;
let tradingDbPath: string;

function openDb(path: string) {
  return new Database(path);
}

function setupTradingDb() {
  const db = openDb(tradingDbPath);
  db.exec(`
    CREATE TABLE IF NOT EXISTS trade_plans (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      symbol TEXT NOT NULL,
      status TEXT NOT NULL,
      scope TEXT NOT NULL DEFAULT 'real',
      created_at TEXT NOT NULL,
      orders_json TEXT NOT NULL,
      updated_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS price_snapshots (
      code TEXT NOT NULL,
      name TEXT,
      price REAL,
      change_pct REAL,
      chg_amt REAL,
      ts TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS portfolio_config (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT DEFAULT (datetime('now'))
    );
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
  `);
  db.close();
}

function setupConfigDb() {
  const db = openDb(configDbPath);
  db.exec(`
    CREATE TABLE IF NOT EXISTS tag_meta (
      tag TEXT PRIMARY KEY,
      star INTEGER DEFAULT 0,
      watch INTEGER DEFAULT 1,
      baseline_value REAL DEFAULT 100,
      parent TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS monitor_watchlist (
      symbol TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      alias TEXT,
      list_type TEXT NOT NULL DEFAULT 'watching',
      cost REAL,
      shares INTEGER,
      lot INTEGER,
      hidden INTEGER NOT NULL DEFAULT 0,
      star INTEGER NOT NULL DEFAULT 0,
      dip_buy INTEGER NOT NULL DEFAULT 0,
      tags TEXT DEFAULT '[]',
      watch_price REAL,
      watch_price_date TEXT,
      pin_order INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL DEFAULT 0,
      updated_at INTEGER
    );
  `);
  db.close();
}

function readAuditPayload(path: string, table: string) {
  const db = openDb(path);
  const row = db
    .prepare(`SELECT payload_json FROM ${table} ORDER BY ts_ms DESC LIMIT 1`)
    .get() as { payload_json: string } | undefined;
  db.close();
  return row ? JSON.parse(row.payload_json) : null;
}

beforeEach(() => {
  tempDir = mkdtempSync(join(tmpdir(), "ai-investor-audit-routes-"));
  configDbPath = join(tempDir, "config.db");
  tradingDbPath = join(tempDir, "trading.db");
  process.env.AI_INVESTOR_CONFIG_DB_PATH = configDbPath;
  process.env.AI_INVESTOR_TRADING_DB_PATH = tradingDbPath;
  process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE = "1";
  setupTradingDb();
  setupConfigDb();
  vi.resetModules();
});

afterEach(() => {
  delete process.env.AI_INVESTOR_CONFIG_DB_PATH;
  delete process.env.AI_INVESTOR_TRADING_DB_PATH;
  delete process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE;
  vi.resetModules();
  rmSync(tempDir, { recursive: true, force: true });
});

describe("audit route integration", () => {
  it("/api/config add writes monitor row and config audit in one request", async () => {
    const { POST } = await import("./config/route");
    const response = await POST(
      new Request("http://localhost/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "add",
          code: "HK00700",
          data: { name: "Tencent", type: "holding", shares: 100, cost: 320 },
        }),
      }),
    );

    expect(response.status).toBe(200);
    const db = openDb(configDbPath);
    const watchRow = db
      .prepare("SELECT symbol, shares, cost FROM monitor_watchlist WHERE symbol = ?")
      .get("HK00700") as { symbol: string; shares: number; cost: number };
    const auditCount = db
      .prepare("SELECT COUNT(*) AS count FROM config_audit_outbox")
      .get() as { count: number };
    db.close();

    expect(watchRow).toEqual({ symbol: "HK00700", shares: 100, cost: 320 });
    expect(auditCount.count).toBe(1);
    const payload = readAuditPayload(configDbPath, "config_audit_outbox");
    expect(payload).toMatchObject({
      schema_version: 2,
      source: "api_config",
      action: "add",
      entity: "monitor_watchlist",
      key: "HK00700",
      db: "config.db",
    });
    expect(payload.after.shares).toBe(100);
  });

  it("/api/config handles invalid JSON without masking the parse failure", async () => {
    const { POST } = await import("./config/route");
    const response = await POST(
      new Request("http://localhost/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{",
      }),
    );

    expect(response.status).toBe(500);
    const body = await response.json();
    expect(String(body.message)).toMatch(/JSON|Unexpected|parse/i);
  });

  it("/api/trade-plans create writes plan and trading audit in one request", async () => {
    const { POST } = await import("./trade-plans/route");
    const response = await POST(
      new Request("http://localhost/api/trade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "create",
          id: "plan-audit",
          plan: {
            name: "Audit Plan",
            symbol: "HK00700",
            status: "active",
            scope: "real",
            created_at: "2026-04-29",
            orders: [
              {
                id: "entry",
                side: "buy",
                op: ">=",
                price: 300,
                shares: 100,
                volume_min: null,
                consecutive_days: null,
                trailing: null,
                label: "entry",
                triggered: false,
                triggered_at: null,
              },
            ],
          },
        }),
      }),
    );

    expect(response.status).toBe(200);
    const db = openDb(tradingDbPath);
    const planRow = db
      .prepare("SELECT id, symbol FROM trade_plans WHERE id = ?")
      .get("plan-audit") as { id: string; symbol: string };
    const auditCount = db
      .prepare("SELECT COUNT(*) AS count FROM trading_audit_outbox")
      .get() as { count: number };
    db.close();

    expect(planRow).toEqual({ id: "plan-audit", symbol: "HK00700" });
    expect(auditCount.count).toBe(1);
    const payload = readAuditPayload(tradingDbPath, "trading_audit_outbox");
    expect(payload).toMatchObject({
      schema_version: 2,
      source: "api_trade_plans",
      action: "create",
      entity: "trade_plan",
      key: "plan-audit",
      db: "trading.db",
    });
    expect(payload.after.symbol).toBe("HK00700");
  });

  it("/api/sector create-tag writes tag_meta and config audit", async () => {
    const { POST } = await import("./sector/route");
    const response = await POST(
      new Request("http://localhost/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "create-tag", tag: "AI", parent: "Tech" }),
      }) as any,
    );

    expect(response.status).toBe(200);
    const db = openDb(configDbPath);
    const tagRow = db
      .prepare("SELECT tag, parent FROM tag_meta WHERE tag = ?")
      .get("AI") as { tag: string; parent: string };
    const auditRow = db
      .prepare("SELECT payload_json FROM config_audit_outbox ORDER BY ts_ms DESC LIMIT 1")
      .get() as { payload_json: string };
    db.close();

    expect(tagRow).toEqual({ tag: "AI", parent: "Tech" });
    const payload = JSON.parse(auditRow.payload_json);
    expect(payload).toMatchObject({
      schema_version: 2,
      source: "api_sector",
      action: "create",
      entity: "tag_meta",
      key: "AI",
      db: "config.db",
    });
  });

  it("/api/config tag-add audits auto-created tag_meta", async () => {
    const db = openDb(configDbPath);
    db.prepare(
      "INSERT INTO monitor_watchlist (symbol, name, list_type, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
    ).run("HK06651", "五一视界", "holding", "[]", 0, 0);
    db.close();

    const { POST } = await import("./config/route");
    const response = await POST(
      new Request("http://localhost/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "tag-add",
          codes: ["HK06651"],
          tag: "AR/VR",
        }),
      }),
    );

    expect(response.status).toBe(200);
    const checkDb = openDb(configDbPath);
    const rows = checkDb
      .prepare("SELECT payload_json FROM config_audit_outbox ORDER BY ts_ms, event_id")
      .all() as { payload_json: string }[];
    checkDb.close();

    const payloads = rows.map((row) => JSON.parse(row.payload_json));
    expect(payloads).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: "api_config",
          action: "tag_add",
          entity: "monitor_watchlist",
          key: "HK06651",
        }),
        expect.objectContaining({
          source: "api_config",
          action: "create",
          entity: "tag_meta",
          key: "AR/VR",
          after: expect.objectContaining({ tag: "AR/VR" }),
        }),
      ]),
    );
  });

  it("/api/sector config writes portfolio_config and trading audit", async () => {
    const { POST } = await import("./sector/route");
    const response = await POST(
      new Request("http://localhost/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "config",
          alertRules: { AI: { cumulative_gain_pct: 8 } },
        }),
      }) as any,
    );

    expect(response.status).toBe(200);
    const db = openDb(tradingDbPath);
    const configRow = db
      .prepare("SELECT value FROM portfolio_config WHERE key = ?")
      .get("sector_config.alert_rules") as { value: string };
    const auditRow = db
      .prepare("SELECT payload_json FROM trading_audit_outbox ORDER BY ts_ms DESC LIMIT 1")
      .get() as { payload_json: string };
    db.close();

    expect(JSON.parse(configRow.value)).toEqual({ AI: { cumulative_gain_pct: 8 } });
    const payload = JSON.parse(auditRow.payload_json);
    expect(payload).toMatchObject({
      schema_version: 2,
      source: "api_sector",
      action: "update",
      entity: "portfolio_config",
      key: "sector_config",
      db: "trading.db",
    });
  });

  it("/api/sector rejects empty config without audit noise", async () => {
    const { POST } = await import("./sector/route");
    const response = await POST(
      new Request("http://localhost/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "config" }),
      }) as any,
    );

    expect(response.status).toBe(400);
    const db = openDb(tradingDbPath);
    const auditCount = db
      .prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'trading_audit_outbox'")
      .get() as { count: number };
    db.close();
    expect(auditCount.count).toBe(0);
  });

  it("/api/sector legacy delete returns 404 for unknown tag without audit", async () => {
    const { POST } = await import("./sector/route");
    const response = await POST(
      new Request("http://localhost/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete", id: "missing-tag" }),
      }) as any,
    );

    expect(response.status).toBe(404);
    const db = openDb(configDbPath);
    const auditCount = db
      .prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'config_audit_outbox'")
      .get() as { count: number };
    db.close();
    expect(auditCount.count).toBe(0);
  });

  it("/api/earnings trigger_check writes job request and trading audit", async () => {
    const { POST } = await import("./earnings/route");
    const response = await POST(
      new Request("http://localhost/api/earnings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "trigger_check" }),
      }) as any,
    );

    expect(response.status).toBe(200);
    const body = await response.json();
    const db = openDb(tradingDbPath);
    const requestRow = db
      .prepare("SELECT id, job_type, requested_by, status FROM job_requests WHERE id = ?")
      .get(body.request_id) as { id: string; job_type: string; requested_by: string; status: string };
    const auditRow = db
      .prepare("SELECT payload_json FROM trading_audit_outbox ORDER BY ts_ms DESC LIMIT 1")
      .get() as { payload_json: string };
    db.close();

    expect(requestRow).toMatchObject({
      id: body.request_id,
      job_type: "earnings_check",
      requested_by: "api",
      status: "pending",
    });
    const payload = JSON.parse(auditRow.payload_json);
    expect(payload).toMatchObject({
      schema_version: 2,
      source: "api_earnings",
      action: "trigger_check",
      entity: "job_requests",
      key: body.request_id,
      db: "trading.db",
    });
    expect(payload.after.job_type).toBe("earnings_check");
  });
});
