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
import { NextRequest } from "next/server";

const TEST_DB_PATH = join(tmpdir(), "test_earnings.db");
let getEarnings: typeof import("./route").GET;
let postEarnings: typeof import("./route").POST;
let parseDaysAhead: typeof import("./route").parseDaysAhead;

function setupTestDb() {
  cleanupTestDb();
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

function cleanupTestDb() {
  try { unlinkSync(TEST_DB_PATH); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-wal"); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-shm"); } catch { /* ignore */ }
}

beforeAll(async () => {
  process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE = "1";
  process.env.AI_INVESTOR_TRADING_DB_PATH = TEST_DB_PATH;
  setupTestDb();
  const route = await import("./route");
  getEarnings = route.GET;
  postEarnings = route.POST;
  parseDaysAhead = route.parseDaysAhead;
});

afterAll(() => {
  delete process.env.AI_INVESTOR_TRADING_DB_PATH;
  delete process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE;
  cleanupTestDb();
});

describe("parseDaysAhead", () => {
  it("defaults null, empty, and non-numeric values to 7", () => {
    expect(parseDaysAhead(null)).toBe(7);
    expect(parseDaysAhead("")).toBe(7);
    expect(parseDaysAhead("abc")).toBe(7);
    expect(parseDaysAhead("12.5")).toBe(12);
  });

  it("clamps to 1..366", () => {
    expect(parseDaysAhead("0")).toBe(1);
    expect(parseDaysAhead("-99")).toBe(1);
    expect(parseDaysAhead("99999")).toBe(366);
    expect(parseDaysAhead("366")).toBe(366);
    expect(parseDaysAhead("1")).toBe(1);
  });
});

describe("GET /api/earnings", () => {
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

    const request = new NextRequest("http://localhost/api/earnings", { method: "GET" });
    const response = await getEarnings(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.count).toBe(2);
    expect(body.upcoming.map((e: any) => e.symbol)).toContain("000001");
    expect(body.upcoming.map((e: any) => e.symbol)).toContain("HK00700");
    expect(body.upcoming.map((e: any) => e.symbol)).not.toContain("000002");
  });

  it("respects days parameter", async () => {
    const request = new NextRequest("http://localhost/api/earnings?days=3", { method: "GET" });
    const response = await getEarnings(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.count).toBe(1);
    expect(body.upcoming[0].symbol).toBe("000001");
  });

  it("clamps huge days so query window stays bounded", async () => {
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const at200 = new Date(today);
    at200.setDate(at200.getDate() + 200);
    const at200Str = at200.toISOString().slice(0, 10);

    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM earnings_calendar;`);
    db.exec(`
      INSERT INTO earnings_calendar (symbol, report_date, name, source, created_at, updated_at)
      VALUES ('FAR200', '${at200Str}', 'x', 's', '${todayStr}', '${todayStr}');
    `);
    db.close();

    const short = new NextRequest("http://localhost/api/earnings?days=1", { method: "GET" });
    expect((await (await getEarnings(short)).json()).count).toBe(0);

    const capped = new NextRequest("http://localhost/api/earnings?days=99999", { method: "GET" });
    const body = await (await getEarnings(capped)).json();
    expect(body.count).toBe(1);
    expect(body.upcoming[0].symbol).toBe("FAR200");
  });

  it("returns last_updated", async () => {
    const request = new NextRequest("http://localhost/api/earnings", { method: "GET" });
    const response = await getEarnings(request);
    const body = await response.json();
    expect(body.last_updated).not.toBeNull();
    expect(typeof body.last_updated).toBe("string");
  });

  it("returns empty array when no earnings in range", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM earnings_calendar;`);
    db.close();

    const request = new NextRequest("http://localhost/api/earnings", { method: "GET" });
    const response = await getEarnings(request);
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

    const request = new NextRequest("http://localhost/api/earnings", { method: "GET" });
    const response = await getEarnings(request);
    const body = await response.json();
    const entry = body.upcoming.find((e: any) => e.symbol === "999999");
    expect(entry).toBeDefined();
    expect(entry.name).toBeNull();
    expect(entry.source).toBeNull();
  });
});

describe("POST /api/earnings", () => {
  it("trigger_check creates a pending earnings_check job request", async () => {
    const request = new Request("http://localhost/api/earnings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "trigger_check" }),
    });
    const response = await postEarnings(request as any);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.message).toBe("检查已触发");
    expect(body.request_id).toBeTruthy();

    // Verify DB write
    const db = new Database(TEST_DB_PATH, { readonly: true });
    const row = db.prepare(
      "SELECT id, job_type, requested_by, status, correlation_id, created_at_ms FROM job_requests WHERE id = ?"
    ).get(body.request_id) as {
      id: string;
      job_type: string;
      requested_by: string;
      status: string;
      correlation_id: string;
      created_at_ms: number;
    } | undefined;
    db.close();
    expect(row).toBeDefined();
    expect(row!.job_type).toBe("earnings_check");
    expect(row!.requested_by).toBe("api");
    expect(row!.status).toBe("pending");
    expect(row!.correlation_id).toBeTruthy();
    expect(row!.created_at_ms).toBeGreaterThan(0);
  });

  it("trigger_check is idempotent for the same idempotency key", async () => {
    const key = "earnings-check-idempotent-test";
    const makeRequest = () =>
      new Request("http://localhost/api/earnings", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify({ action: "trigger_check" }),
      });

    const first = await postEarnings(makeRequest() as any);
    const second = await postEarnings(makeRequest() as any);
    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    const firstBody = await first.json();
    const secondBody = await second.json();
    expect(secondBody.request_id).toBe(firstBody.request_id);

    const db = new Database(TEST_DB_PATH, { readonly: true });
    const row = db.prepare(
      "SELECT COUNT(*) AS count FROM job_requests WHERE job_type = 'earnings_check' AND correlation_id = ?"
    ).get(key) as { count: number };
    db.close();
    expect(row.count).toBe(1);
  });

  it("unknown action returns 400", async () => {
    const request = new Request("http://localhost/api/earnings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "unknown_action" }),
    });
    const response = await postEarnings(request as any);
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
    const response = await postEarnings(request as any);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
  });
});
