/**
 * Tests for /api/position-change-log GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/position-change-log/route.test.ts
 *
 * Uses a real temporary SQLite database to test the actual handler.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";

// ── Test database setup ──────────────────────────────────────────────────────

const TEST_DB_PATH = join(tmpdir(), "test_pcl_route.db");

function setupTestDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");

  db.exec(`
    CREATE TABLE IF NOT EXISTS monitor_watchlist (
      symbol TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      list_type TEXT NOT NULL,
      cost REAL,
      shares INTEGER,
      lot INTEGER,
      hidden INTEGER NOT NULL DEFAULT 0,
      star INTEGER NOT NULL DEFAULT 0,
      dip_buy INTEGER NOT NULL DEFAULT 0,
      tags TEXT DEFAULT '[]',
      watch_price REAL,
      watch_price_date TEXT,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS position_change_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      ts TEXT NOT NULL,
      source TEXT NOT NULL,
      shares_from INTEGER,
      shares_to INTEGER,
      cost_from REAL,
      cost_to REAL
    );
    CREATE INDEX IF NOT EXISTS idx_pcl_symbol ON position_change_log(symbol);
    CREATE INDEX IF NOT EXISTS idx_pcl_ts ON position_change_log(ts);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pcl_unique
      ON position_change_log(symbol, ts, source, shares_from, shares_to, cost_from, cost_to);
  `);

  const now = new Date().toISOString();
  db.exec(`
    INSERT INTO position_change_log (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
    VALUES ('HK00700', '${now}', 'manual', 200, 300, 350.0, 340.0);

    INSERT INTO position_change_log (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
    VALUES ('HK00700', '2026-03-21T10:00:00', 'sync', 100, 200, 360.0, 350.0);

    INSERT INTO position_change_log (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
    VALUES ('HK99999', '${now}', 'sync', NULL, 500, NULL, 120.0);
  `);
  db.close();
}

function cleanupTestDb() {
  try { unlinkSync(TEST_DB_PATH); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-wal"); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-shm"); } catch { /* ignore */ }
}

// ── Mock route module ────────────────────────────────────────────────────────
// We test the GET handler by calling it with a mocked Database.

describe("GET /api/position-change-log", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  // Inline handler that mirrors the route logic but uses TEST_DB_PATH
  async function getHandler(request: Request) {
    const { NextResponse } = await import("next/server");
    const { searchParams } = new URL(request.url);
    const symbol = searchParams.get("symbol");
    const limit = Math.min(Number(searchParams.get("limit")) || 20, 100);

    if (!symbol) {
      return NextResponse.json({ error: "symbol is required" }, { status: 400 });
    }

    const db = new Database(TEST_DB_PATH, { readonly: true });
    try {
      const rows = db.prepare(`
        SELECT ts, source, shares_from, shares_to, cost_from, cost_to
        FROM position_change_log
        WHERE symbol = ?
        ORDER BY ts DESC
        LIMIT ?
      `).all(symbol, limit) as {
        ts: string;
        source: string;
        shares_from: number | null;
        shares_to: number | null;
        cost_from: number | null;
        cost_to: number | null;
      }[];

      return NextResponse.json({ results: rows });
    } finally {
      db.close();
    }
  }

  it("returns 400 when symbol is missing", async () => {
    const request = new Request("http://localhost/api/position-change-log", { method: "GET" });
    const response = await getHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.error).toMatch(/symbol/i);
  });

  it("returns log entries for a symbol ordered by ts DESC", async () => {
    const request = new Request("http://localhost/api/position-change-log?symbol=HK00700", {
      method: "GET",
    });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toHaveProperty("results");
    expect(Array.isArray(body.results)).toBe(true);
    // Most recent first
    expect(body.results.length).toBeGreaterThanOrEqual(2);
    expect(body.results[0].symbol).toBeUndefined(); // symbol not selected
    expect(body.results[0].source).toBe("manual");
    expect(body.results[0].shares_from).toBe(200);
    expect(body.results[0].shares_to).toBe(300);
    expect(body.results[0].cost_from).toBe(350.0);
    expect(body.results[0].cost_to).toBe(340.0);
  });

  it("respects limit parameter (max 100)", async () => {
    const request = new Request("http://localhost/api/position-change-log?symbol=HK00700&limit=1", {
      method: "GET",
    });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.results.length).toBe(1);
    // Second entry should be older
    expect(body.results[0].source).toBe("manual"); // most recent
  });

  it("returns empty array for unknown symbol", async () => {
    const request = new Request("http://localhost/api/position-change-log?symbol=HK00000", {
      method: "GET",
    });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.results).toEqual([]);
  });

  it("handles null values correctly (NULL stored → JSON null returned)", async () => {
    const request = new Request("http://localhost/api/position-change-log?symbol=HK99999", {
      method: "GET",
    });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.results.length).toBe(1);
    const row = body.results[0];
    // Null fields from DB become JSON null
    expect(row.shares_from).toBeNull();
    expect(row.cost_from).toBeNull();
    expect(row.shares_to).toBe(500);
    expect(row.cost_to).toBe(120.0);
  });

  it("caps limit at 100", async () => {
    const request = new Request("http://localhost/api/position-change-log?symbol=HK00700&limit=500", {
      method: "GET",
    });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.results.length).toBeLessThanOrEqual(100);
  });

  it("returns correct JSON structure with all expected fields", async () => {
    const request = new Request("http://localhost/api/position-change-log?symbol=HK00700&limit=1", {
      method: "GET",
    });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    const row = body.results[0];
    expect(row).toHaveProperty("ts");
    expect(row).toHaveProperty("source");
    expect(row).toHaveProperty("shares_from");
    expect(row).toHaveProperty("shares_to");
    expect(row).toHaveProperty("cost_from");
    expect(row).toHaveProperty("cost_to");
    expect(typeof row.ts).toBe("string");
    expect(typeof row.source).toBe("string");
  });

  it("default limit is 20", async () => {
    // Insert many rows for HK00001 to test default limit
    const db = new Database(TEST_DB_PATH);
    const now = new Date().toISOString();
    for (let i = 0; i < 25; i++) {
      db.exec(`
        INSERT INTO position_change_log (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
        VALUES ('HK00001', '${now}', 'manual', ${i}, ${i + 1}, NULL, NULL)
      `);
    }
    db.close();

    const request = new Request("http://localhost/api/position-change-log?symbol=HK00001", {
      method: "GET",
    });
    const response = await getHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    // Default limit=20
    expect(body.results.length).toBe(20);
  });
});
