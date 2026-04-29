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
});
