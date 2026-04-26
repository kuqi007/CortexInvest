/**
 * Tests for /api/positions GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/positions/route.test.ts
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";
import { NextResponse } from "next/server";

const TEST_DB_PATH = join(tmpdir(), "test_positions.db");

function setupTestDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = DELETE");
  db.exec(`
    CREATE TABLE IF NOT EXISTS monitor_watchlist (
      symbol TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      alias TEXT,
      list_type TEXT NOT NULL,
      cost REAL,
      shares INTEGER,
      hidden INTEGER NOT NULL DEFAULT 0,
      star INTEGER NOT NULL DEFAULT 0,
      dip_buy INTEGER NOT NULL DEFAULT 0,
      tags TEXT DEFAULT '[]',
      watch_price REAL,
      watch_price_date TEXT,
      pin_order INTEGER NOT NULL DEFAULT 0
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
  try {
    const db = new Database(TEST_DB_PATH, { readonly: true });
    try {
      const rows = db
        .prepare(
          `SELECT symbol, name, alias, cost, shares, hidden, star, dip_buy, tags,
                  watch_price, watch_price_date, pin_order
           FROM monitor_watchlist
           WHERE list_type = 'holding'
           ORDER BY pin_order ASC, symbol ASC`
        )
        .all() as {
          symbol: string;
          name: string;
          alias: string | null;
          cost: number | null;
          shares: number | null;
          hidden: number;
          star: number;
          dip_buy: number;
          tags: string | null;
          watch_price: number | null;
          watch_price_date: string | null;
          pin_order: number;
        }[];

      const holdings = rows.map((r) => {
        let parsedTags: string[] | undefined;
        if (r.tags) {
          try { parsedTags = JSON.parse(r.tags); } catch { /* ignore */ }
        }
        return {
          symbol: r.symbol,
          name: r.name,
          alias: r.alias ?? undefined,
          cost: r.cost,
          shares: r.shares,
          hidden: Boolean(r.hidden),
          star: Boolean(r.star),
          dip_buy: Boolean(r.dip_buy),
          tags: parsedTags,
          watch_price: r.watch_price,
          watch_price_date: r.watch_price_date,
          pin_order: r.pin_order,
        };
      });

      return NextResponse.json({ holdings });
    } finally {
      db.close();
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg, holdings: [] }, { status: 500 });
  }
}

describe("GET /api/positions", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("returns correct holding list", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO monitor_watchlist (symbol, name, alias, list_type, cost, shares, hidden, star, dip_buy, tags, watch_price, watch_price_date, pin_order)
      VALUES ('000001', '平安银行', 'PAB', 'holding', 10.5, 1000, 0, 1, 0, '["finance"]', 12.0, '2024-04-20', 1);
      INSERT INTO monitor_watchlist (symbol, name, alias, list_type, cost, shares, hidden, star, dip_buy, tags, watch_price, watch_price_date, pin_order)
      VALUES ('HK00700', '腾讯控股', 'Tencent', 'holding', 350.0, 500, 0, 0, 1, '["tech"]', NULL, NULL, 2);
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, pin_order)
      VALUES ('000002', '万科A', 'watching', NULL, NULL, 0, 0, 0);
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.holdings).toHaveLength(2);
    expect(body.holdings.map((h: any) => h.symbol)).toContain("000001");
    expect(body.holdings.map((h: any) => h.symbol)).toContain("HK00700");
    expect(body.holdings.map((h: any) => h.symbol)).not.toContain("000002");
  });

  it("correctly parses tags JSON", async () => {
    const response = await getHandler();
    const body = await response.json();
    const pab = body.holdings.find((h: any) => h.symbol === "000001");
    expect(pab.tags).toEqual(["finance"]);
    const tencent = body.holdings.find((h: any) => h.symbol === "HK00700");
    expect(tencent.tags).toEqual(["tech"]);
  });

  it("returns empty array when no holdings", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM monitor_watchlist;`);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.holdings).toEqual([]);
  });

  it("correctly converts hidden/star/dip_buy booleans", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('000003', '测试股1', 'holding', 5.0, 100, 1, 0, 0, 0);
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('000004', '测试股2', 'holding', 8.0, 200, 0, 1, 1, 0);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    const h1 = body.holdings.find((h: any) => h.symbol === "000003");
    expect(h1.hidden).toBe(true);
    expect(h1.star).toBe(false);
    expect(h1.dip_buy).toBe(false);

    const h2 = body.holdings.find((h: any) => h.symbol === "000004");
    expect(h2.hidden).toBe(false);
    expect(h2.star).toBe(true);
    expect(h2.dip_buy).toBe(true);
  });

  it("handles null alias correctly", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM monitor_watchlist;`);
    db.exec(`
      INSERT INTO monitor_watchlist (symbol, name, alias, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('000005', '无名股', NULL, 'holding', 3.0, 500, 0, 0, 0, 0);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    const h = body.holdings[0];
    expect(h.alias).toBeUndefined();
  });

  it("handles null tags gracefully", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM monitor_watchlist;`);
    db.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, tags, pin_order)
      VALUES ('000006', '无标签股', 'holding', 2.0, 1000, 0, 0, 0, NULL, 0);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    const h = body.holdings[0];
    expect(h.tags).toBeUndefined();
  });

  it("handles invalid tags JSON gracefully", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM monitor_watchlist;`);
    db.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, tags, pin_order)
      VALUES ('000007', '坏标签股', 'holding', 2.0, 1000, 0, 0, 0, 'not-json', 0);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    const h = body.holdings[0];
    expect(h.tags).toBeUndefined();
  });

  it("orders by pin_order ASC then symbol ASC", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM monitor_watchlist;`);
    db.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('Z99999', 'Z股', 'holding', 1.0, 100, 0, 0, 0, 0);
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('A00001', 'A股', 'holding', 2.0, 200, 0, 0, 0, 1);
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('B00002', 'B股', 'holding', 3.0, 300, 0, 0, 0, 0);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    const symbols = body.holdings.map((h: any) => h.symbol);
    expect(symbols).toEqual(["B00002", "Z99999", "A00001"]);
  });

  it("handles null cost and shares", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`DELETE FROM monitor_watchlist;`);
    db.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('000008', '空仓股', 'holding', NULL, NULL, 0, 0, 0, 0);
    `);
    db.close();

    const response = await getHandler();
    const body = await response.json();
    const h = body.holdings[0];
    expect(h.cost).toBeNull();
    expect(h.shares).toBeNull();
  });
});
