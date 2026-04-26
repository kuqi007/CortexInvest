/**
 * Tests for /api/summary GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/summary/route.test.ts
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";
import { NextResponse } from "next/server";

const TEST_DB_PATH = join(tmpdir(), "test_summary.db");

function setupTestDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS daily_summaries (
      date TEXT PRIMARY KEY,
      market TEXT NOT NULL,
      stats_json TEXT NOT NULL,
      per_stock_json TEXT,
      generated_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS morning_briefings (
      date TEXT PRIMARY KEY,
      generated_at TEXT NOT NULL,
      content_json TEXT NOT NULL
    );
  `);
  db.close();
}

function cleanupTestDb() {
  try { unlinkSync(TEST_DB_PATH); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-wal"); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-shm"); } catch { /* ignore */ }
}

async function getHandler() {
  const db = new Database(TEST_DB_PATH, { readonly: true });
  try {
    const today = new Date().toISOString().slice(0, 10);

    const summaryRow = db
      .prepare("SELECT date, market, stats_json, per_stock_json, generated_at FROM daily_summaries WHERE date = ?")
      .get(today) as
      | { date: string; market: string; stats_json: string; per_stock_json: string | null; generated_at: number | null }
      | undefined;

    if (summaryRow) {
      const summary: Record<string, any> = {
        date: summaryRow.date,
        market: summaryRow.market,
        stats: JSON.parse(summaryRow.stats_json),
        perStock: summaryRow.per_stock_json ? JSON.parse(summaryRow.per_stock_json) : [],
        generatedAt: summaryRow.generated_at,
      };

      const morningRow = db
        .prepare("SELECT date, generated_at, content_json FROM morning_briefings WHERE date = ?")
        .get(today) as
        | { date: string; generated_at: string; content_json: string }
        | undefined;

      if (morningRow) {
        summary.morning = JSON.parse(morningRow.content_json);
      } else {
        summary.morning = null;
      }

      return NextResponse.json({ data: summary });
    }

    const morningRow = db
      .prepare("SELECT date, generated_at, content_json FROM morning_briefings WHERE date = ?")
      .get(today) as
      | { date: string; generated_at: string; content_json: string }
      | undefined;

    if (morningRow) {
      return NextResponse.json({
        data: { morning: JSON.parse(morningRow.content_json) },
      });
    }

    return NextResponse.json({ data: null });
  } catch (error: any) {
    return NextResponse.json({ data: null, error: error.message }, { status: 500 });
  } finally {
    db.close();
  }
}

describe("GET /api/summary", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("returns summary + morning briefing when daily_summary exists", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO daily_summaries (date, market, stats_json, per_stock_json, generated_at)
      VALUES ('${today}', 'A-share', '{"total_turnover": 8500, "up_count": 2500, "down_count": 1800}', '[{"symbol":"000001","change":2.5}]', 1714118400);
      INSERT INTO morning_briefings (date, generated_at, content_json)
      VALUES ('${today}', '2024-04-26T08:00:00Z', '{"title": "Morning Brief", "highlights": ["Fed pause"]}');
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data).toBeDefined();
    expect(body.data.date).toBe(today);
    expect(body.data.market).toBe("A-share");
    expect(body.data.stats).toEqual({ total_turnover: 8500, up_count: 2500, down_count: 1800 });
    expect(body.data.perStock).toEqual([{ symbol: "000001", change: 2.5 }]);
    expect(body.data.generatedAt).toBe(1714118400);
    expect(body.data.morning).toEqual({ title: "Morning Brief", highlights: ["Fed pause"] });
  });

  it("returns morning data only when only morning briefing exists", async () => {
    // Clear daily_summaries for today
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM daily_summaries WHERE date = '${today}';`);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data).toBeDefined();
    expect(body.data.morning).toEqual({ title: "Morning Brief", highlights: ["Fed pause"] });
    expect(body.data.report).toBeUndefined();
    expect(body.data.stats).toBeUndefined();
  });

  it("returns data: null when neither exists", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM daily_summaries WHERE date = '${today}';`);
    db.exec(`DELETE FROM morning_briefings WHERE date = '${today}';`);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data).toBeNull();
  });

  it("correctly parses stats_json and per_stock_json", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    const stats = { total_turnover: 12000, up_count: 3000, down_count: 1500, top_sectors: ["AI", "EV"] };
    const perStock = [
      { symbol: "000001", name: "平安银行", change: 3.2, volume: 50000 },
      { symbol: "HK00700", name: "腾讯", change: -1.5, volume: 20000 },
    ];
    db.exec(`
      INSERT INTO daily_summaries (date, market, stats_json, per_stock_json, generated_at)
      VALUES ('${today}', 'mixed', '${JSON.stringify(stats)}', '${JSON.stringify(perStock)}', 1714204800)
      ON CONFLICT(date) DO UPDATE SET
        market = excluded.market,
        stats_json = excluded.stats_json,
        per_stock_json = excluded.per_stock_json,
        generated_at = excluded.generated_at;
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.data.stats).toEqual(stats);
    expect(body.data.perStock).toEqual(perStock);
  });

  it("handles null per_stock_json gracefully", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO daily_summaries (date, market, stats_json, per_stock_json, generated_at)
      VALUES ('${today}', 'A-share', '{"turnover": 5000}', NULL, 1714204800)
      ON CONFLICT(date) DO UPDATE SET
        market = excluded.market,
        stats_json = excluded.stats_json,
        per_stock_json = excluded.per_stock_json,
        generated_at = excluded.generated_at;
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.data.perStock).toEqual([]);
  });

  it("handles empty per_stock_json array", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO daily_summaries (date, market, stats_json, per_stock_json, generated_at)
      VALUES ('${today}', 'A-share', '{"turnover": 5000}', '[]', 1714204800)
      ON CONFLICT(date) DO UPDATE SET
        market = excluded.market,
        stats_json = excluded.stats_json,
        per_stock_json = excluded.per_stock_json,
        generated_at = excluded.generated_at;
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.data.perStock).toEqual([]);
  });
});
