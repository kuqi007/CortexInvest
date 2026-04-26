/**
 * Tests for /api/earnings GET + POST endpoint.
 *
 * Run: cd web && npx vitest run app/api/earnings/route.test.ts
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";
import { NextResponse } from "next/server";

const TEST_DB_PATH = join(tmpdir(), "test_earnings.db");

function setupTestDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS earnings_calendar (
      symbol TEXT NOT NULL,
      report_date TEXT NOT NULL,
      name TEXT,
      source TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (symbol, report_date)
    );
    CREATE TABLE IF NOT EXISTS portfolio_config (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
  `);
  db.close();
}

function cleanupTestDb() {
  try { unlinkSync(TEST_DB_PATH); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-wal"); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-shm"); } catch { /* ignore */ }
}

async function getHandler(request: Request) {
  const db = new Database(TEST_DB_PATH, { readonly: true });
  try {
    const url = new URL(request.url);
    const days = parseInt(url.searchParams.get("days") || "7", 10);

    const now = new Date();
    const cutoff = new Date(now.getTime() + days * 24 * 60 * 60 * 1000);
    const today = now.toISOString().slice(0, 10);
    const cutoffStr = cutoff.toISOString().slice(0, 10);

    const rows = db
      .prepare(
        `SELECT symbol, report_date, name, source, created_at, updated_at
         FROM earnings_calendar
         WHERE report_date >= ? AND report_date <= ?
         ORDER BY report_date, symbol`
      )
      .all(today, cutoffStr) as Array<{
      symbol: string;
      report_date: string;
      name: string | null;
      source: string | null;
      created_at: string;
      updated_at: string;
    }>;

    const upcoming = rows.map((r) => ({
      symbol: r.symbol,
      report_date: r.report_date,
      name: r.name,
      source: r.source,
      created_at: r.created_at,
      updated_at: r.updated_at,
    }));

    const lastUpdatedRow = db
      .prepare("SELECT MAX(updated_at) as last_updated FROM earnings_calendar")
      .get() as { last_updated: string | null } | undefined;

    return NextResponse.json({
      success: true,
      count: upcoming.length,
      upcoming,
      last_updated: lastUpdatedRow?.last_updated || null,
    });
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  } finally {
    db.close();
  }
}

async function postHandler(request: Request) {
  const db = new Database(TEST_DB_PATH);
  try {
    const body = await request.json();
    const { action } = body;

    if (action === "trigger_check") {
      db.prepare(
        `INSERT INTO portfolio_config (key, value, updated_at)
         VALUES ('earnings_check_trigger', ?, datetime('now'))
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`
      ).run(new Date().toISOString());

      return NextResponse.json({ success: true, message: "检查已触发" });
    }

    return NextResponse.json(
      { success: false, error: "Unknown action" },
      { status: 400 }
    );
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  } finally {
    db.close();
  }
}

describe("GET /api/earnings", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("returns upcoming earnings for default 7 days", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    const d1 = new Date();
    d1.setDate(d1.getDate() + 2);
    const d2 = new Date();
    d2.setDate(d2.getDate() + 5);
    const d3 = new Date();
    d3.setDate(d3.getDate() + 10); // beyond default 7 days

    db.exec(`
      INSERT INTO earnings_calendar (symbol, report_date, name, source, created_at, updated_at)
      VALUES ('000001', '${d1.toISOString().slice(0, 10)}', '平安银行', 'sse', '${today}', '${today}');
      INSERT INTO earnings_calendar (symbol, report_date, name, source, created_at, updated_at)
      VALUES ('HK00700', '${d2.toISOString().slice(0, 10)}', '腾讯控股', 'hkex', '${today}', '${today}');
      INSERT INTO earnings_calendar (symbol, report_date, name, source, created_at, updated_at)
      VALUES ('000002', '${d3.toISOString().slice(0, 10)}', '万科A', 'sse', '${today}', '${today}');
    `);
    db.close();

    const request = new Request("http://localhost/api/earnings", { method: "GET" });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.count).toBe(2);
    expect(body.upcoming.map((e: any) => e.symbol)).toContain("000001");
    expect(body.upcoming.map((e: any) => e.symbol)).toContain("HK00700");
    expect(body.upcoming.map((e: any) => e.symbol)).not.toContain("000002");
  });

  it("respects days parameter", async () => {
    const request = new Request("http://localhost/api/earnings?days=3", { method: "GET" });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.count).toBe(1);
    expect(body.upcoming[0].symbol).toBe("000001");
  });

  it("returns last_updated", async () => {
    const request = new Request("http://localhost/api/earnings", { method: "GET" });
    const response = await getHandler(request);
    const body = await response.json();
    expect(body.last_updated).not.toBeNull();
    expect(typeof body.last_updated).toBe("string");
  });

  it("returns empty array when no earnings in range", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM earnings_calendar;`);
    db.close();

    const request = new Request("http://localhost/api/earnings", { method: "GET" });
    const response = await getHandler(request);
    const body = await response.json();
    expect(body.count).toBe(0);
    expect(body.upcoming).toEqual([]);
    expect(body.last_updated).toBeNull();
  });

  it("handles null name and source gracefully", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const d1 = new Date();
    d1.setDate(d1.getDate() + 1);
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO earnings_calendar (symbol, report_date, name, source, created_at, updated_at)
      VALUES ('999999', '${d1.toISOString().slice(0, 10)}', NULL, NULL, '${today}', '${today}');
    `);
    db.close();

    const request = new Request("http://localhost/api/earnings", { method: "GET" });
    const response = await getHandler(request);
    const body = await response.json();
    const entry = body.upcoming.find((e: any) => e.symbol === "999999");
    expect(entry).toBeDefined();
    expect(entry.name).toBeNull();
    expect(entry.source).toBeNull();
  });
});

describe("POST /api/earnings", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("trigger_check action succeeds", async () => {
    const request = new Request("http://localhost/api/earnings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "trigger_check" }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.message).toBe("检查已触发");

    // Verify DB write
    const db = new Database(TEST_DB_PATH, { readonly: true });
    const row = db.prepare("SELECT key, value FROM portfolio_config WHERE key = 'earnings_check_trigger'").get() as {
      key: string;
      value: string;
    } | undefined;
    db.close();
    expect(row).toBeDefined();
    expect(row!.key).toBe("earnings_check_trigger");
    expect(new Date(row!.value).getTime()).toBeGreaterThan(0);
  });

  it("unknown action returns 400", async () => {
    const request = new Request("http://localhost/api/earnings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "unknown_action" }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.error).toBe("Unknown action");
  });

  it("missing action body returns 400", async () => {
    const request = new Request("http://localhost/api/earnings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
  });
});
