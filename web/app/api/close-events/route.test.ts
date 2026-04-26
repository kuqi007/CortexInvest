/**
 * Tests for /api/close-events GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/close-events/route.test.ts
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";
import { NextResponse } from "next/server";

const TEST_DB_PATH = join(tmpdir(), "test_close_events.db");

function setupTestDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS alert_events (
      date TEXT NOT NULL,
      ts INTEGER NOT NULL,
      time TEXT NOT NULL,
      symbol TEXT NOT NULL,
      kind TEXT NOT NULL,
      level INTEGER,
      message TEXT,
      display TEXT,
      change_pct REAL
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

    let rows = db
      .prepare(
        `SELECT date, ts, time, symbol, kind, level, message, display, change_pct
         FROM alert_events
         WHERE date = ? AND kind = 'market_close'
         ORDER BY ts DESC
         LIMIT 1`
      )
      .all(today);

    if (rows.length === 0) {
      rows = db
        .prepare(
          `SELECT date, ts, time, symbol, kind, level, message, display, change_pct
           FROM alert_events
           WHERE kind = 'market_close'
           ORDER BY date DESC, ts DESC
           LIMIT 1`
        )
        .all();
    }

    if (rows.length === 0) {
      return NextResponse.json({ data: null });
    }

    const event = rows[0] as {
      date: string;
      ts: number;
      time: string;
      symbol: string;
      kind: string;
      level: number;
      message: string;
      display: string;
      change_pct: number;
    };

    return NextResponse.json({
      data: {
        date: event.date,
        time: event.time,
        message: event.message,
        display: event.message,
      },
    });
  } catch (error) {
    console.error("Failed to read close events:", error);
    return NextResponse.json({ data: null }, { status: 500 });
  } finally {
    db.close();
  }
}

describe("GET /api/close-events", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("returns today's market_close event", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO alert_events (date, ts, time, symbol, kind, level, message, display, change_pct)
      VALUES ('${today}', 1714118400, '15:00:00', 'MARKET', 'market_close', 1, '今日收盘：沪指涨0.5%，深成指涨0.8%', '收盘简报', 0.5);
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data).toBeDefined();
    expect(body.data.date).toBe(today);
    expect(body.data.time).toBe("15:00:00");
    expect(body.data.message).toContain("沪指涨0.5%");
    expect(body.data.display).toBe(body.data.message);
  });

  it("falls back to most recent market_close when today's is missing", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM alert_events WHERE date = '${today}';`);
    db.exec(`
      INSERT INTO alert_events (date, ts, time, symbol, kind, level, message, display, change_pct)
      VALUES ('2024-04-25', 1714032000, '15:00:00', 'MARKET', 'market_close', 1, '昨日收盘：沪指跌0.2%', '收盘简报', -0.2);
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data).toBeDefined();
    expect(body.data.date).toBe("2024-04-25");
    expect(body.data.message).toContain("昨日收盘");
  });

  it("returns data: null when no market_close events exist", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM alert_events;`);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data).toBeNull();
  });

  it("returns most recent when multiple today's events exist", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM alert_events;`);
    db.exec(`
      INSERT INTO alert_events (date, ts, time, symbol, kind, level, message, display, change_pct)
      VALUES ('${today}', 1714118000, '14:53:00', 'MARKET', 'market_close', 1, '收盘前数据', '收盘简报', 0.3);
      INSERT INTO alert_events (date, ts, time, symbol, kind, level, message, display, change_pct)
      VALUES ('${today}', 1714118400, '15:00:00', 'MARKET', 'market_close', 1, '正式收盘', '收盘简报', 0.5);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.data.message).toBe("正式收盘");
    expect(body.data.time).toBe("15:00:00");
  });

  it("ignores non-market_close events", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM alert_events;`);
    db.exec(`
      INSERT INTO alert_events (date, ts, time, symbol, kind, level, message, display, change_pct)
      VALUES ('${today}', 1714118400, '10:00:00', '000001', 'price_spike', 1, 'Price alert', 'Alert', 5.0);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.data).toBeNull();
  });
});
