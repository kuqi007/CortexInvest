/**
 * Tests for GET /api/ai-investment-events.
 *
 * Run: cd web && npx vitest run app/api/ai-investment-events/route.test.ts
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";

const TEST_DB_PATH = join(tmpdir(), "test_ai_investment_events_route.db");

function cleanupTestDb() {
  try {
    unlinkSync(TEST_DB_PATH);
  } catch {
    /* ignore */
  }
  try {
    unlinkSync(`${TEST_DB_PATH}-wal`);
  } catch {
    /* ignore */
  }
  try {
    unlinkSync(`${TEST_DB_PATH}-shm`);
  } catch {
    /* ignore */
  }
}

function seedDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");
  db.exec(`
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
  `);
  db.prepare(
    `INSERT INTO ai_investment_events (
      id, event_date, symbol, name, source, event_type, severity, delivery_scope,
      verdict, confidence, dedupe_key, title, summary, reasons_json, notify_status,
      created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(
    "e1",
    "2026-05-01",
    "000001",
    "Ping An",
    "unit",
    "earnings_countdown",
    "normal",
    "web_only",
    "observe",
    0.5,
    "dk1",
    "t",
    "s",
    "[]",
    "pending",
    "2026-05-01T12:00:01",
    "2026-05-01T12:00:01"
  );
  db.prepare(
    `INSERT INTO ai_investment_events (
      id, event_date, symbol, name, source, event_type, severity, delivery_scope,
      verdict, confidence, dedupe_key, title, summary, reasons_json, notify_status,
      created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(
    "e2",
    "2026-05-01",
    "000002",
    "Other",
    "unit",
    "other_type",
    "low",
    "web_only",
    "observe",
    0.4,
    "dk2",
    "t2",
    "s2",
    "[]",
    "web_only",
    "2026-05-01T12:00:02",
    "2026-05-01T12:00:02"
  );
  db.close();
}

describe("GET /api/ai-investment-events", () => {
  let GET: (request: Request) => Promise<Response>;

  beforeAll(async () => {
    cleanupTestDb();
    seedDb();
    process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE = "1";
    process.env.AI_INVESTOR_TRADING_DB_PATH = TEST_DB_PATH;
    const mod = await import("./route");
    GET = mod.GET;
  });

  afterAll(() => {
    cleanupTestDb();
    delete process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE;
    delete process.env.AI_INVESTOR_TRADING_DB_PATH;
  });

  it("returns rows ordered by created_at desc", async () => {
    const res = await GET(new Request("http://local/api/ai-investment-events?limit=10"));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { data: { id: string }[] };
    expect(body.data).toHaveLength(2);
    expect(body.data[0].id).toBe("e2");
    expect(body.data[1].id).toBe("e1");
  });

  it("filters by event_type", async () => {
    const res = await GET(
      new Request("http://local/api/ai-investment-events?event_type=earnings_countdown&limit=5")
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { data: { id: string }[] };
    expect(body.data).toHaveLength(1);
    expect(body.data[0].id).toBe("e1");
  });

  it("rejects bad limit", async () => {
    const res = await GET(new Request("http://local/api/ai-investment-events?limit=999"));
    expect(res.status).toBe(400);
  });

  it("rejects bad event_type", async () => {
    const res = await GET(new Request("http://local/api/ai-investment-events?event_type=a;b"));
    expect(res.status).toBe(400);
  });

  it("filters by notify_status", async () => {
    const res = await GET(
      new Request("http://local/api/ai-investment-events?notify_status=pending&limit=10")
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { data: { id: string }[] };
    expect(body.data).toHaveLength(1);
    expect(body.data[0].id).toBe("e1");
  });

  it("filters by symbol", async () => {
    const res = await GET(
      new Request("http://local/api/ai-investment-events?symbol=000002&limit=10")
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { data: { id: string }[] };
    expect(body.data).toHaveLength(1);
    expect(body.data[0].id).toBe("e2");
  });

  it("combines event_type and notify_status", async () => {
    const res = await GET(
      new Request(
        "http://local/api/ai-investment-events?event_type=earnings_countdown&notify_status=pending&limit=5"
      )
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { data: { id: string }[] };
    expect(body.data).toHaveLength(1);
    expect(body.data[0].id).toBe("e1");
  });

  it("rejects bad notify_status", async () => {
    const res = await GET(new Request("http://local/api/ai-investment-events?notify_status=nope"));
    expect(res.status).toBe(400);
  });

  it("rejects bad symbol", async () => {
    const res = await GET(new Request("http://local/api/ai-investment-events?symbol=x';--"));
    expect(res.status).toBe(400);
  });
});
