/**
 * Tests for /api/metrics GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/metrics/route.test.ts
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";
import { NextResponse } from "next/server";

const TEST_CONFIG_DB = join(tmpdir(), "test_metrics_config.db");
const TEST_TRADING_DB = join(tmpdir(), "test_metrics_trading.db");

function setupTestDbs() {
  // Config DB
  const configDb = new Database(TEST_CONFIG_DB);
  configDb.pragma("journal_mode = DELETE");
  configDb.exec(`
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
    CREATE TABLE IF NOT EXISTS monitor_settings (
      key TEXT PRIMARY KEY,
      value REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS alert_rules (
      symbol TEXT PRIMARY KEY,
      above REAL,
      below REAL
    );
  `);

  // Trading DB
  const tradingDb = new Database(TEST_TRADING_DB);
  tradingDb.pragma("journal_mode = WAL");
  tradingDb.exec(`
    CREATE TABLE IF NOT EXISTS price_snapshots (
      ts INTEGER NOT NULL,
      code TEXT NOT NULL,
      name TEXT,
      price REAL,
      volume REAL,
      amount REAL,
      change_pct REAL,
      chg_amt REAL,
      amp REAL,
      turnover REAL,
      vol_ratio REAL,
      high REAL,
      low REAL,
      open REAL,
      prev_close REAL,
      amo1 REAL,
      amo2 REAL
    );
    CREATE TABLE IF NOT EXISTS market_turnover (
      ts INTEGER PRIMARY KEY,
      sh REAL,
      sz REAL,
      total REAL,
      sh_index REAL,
      sz_index REAL,
      sh_pct REAL,
      sz_pct REAL,
      verdict TEXT,
      chi_next REAL,
      chi_next_pct REAL,
      kc50 REAL,
      kc50_pct REAL,
      hk_index REAL,
      hk_index_pct REAL,
      hk_tech REAL,
      hk_tech_pct REAL,
      hk_turnover REAL,
      amo1 REAL,
      amo2 REAL
    );
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
    CREATE TABLE IF NOT EXISTS indicator_cache (
      symbol TEXT NOT NULL,
      date TEXT NOT NULL,
      data_json TEXT NOT NULL,
      PRIMARY KEY (symbol, date)
    );
  `);

  configDb.close();
  tradingDb.close();
}

function cleanupTestDbs() {
  for (const path of [TEST_CONFIG_DB, TEST_TRADING_DB]) {
    try { unlinkSync(path); } catch { /* ignore */ }
    try { unlinkSync(path + "-wal"); } catch { /* ignore */ }
    try { unlinkSync(path + "-shm"); } catch { /* ignore */ }
  }
}

// Inline handler mirroring route.ts logic with temp DB paths
async function getHandler() {
  const EMPTY = { services: [], ts: 0, settings: {} };

  type DbWatchRow = {
    symbol: string;
    name: string;
    alias: string | null;
    list_type: "holding" | "watching";
    cost: number | null;
    shares: number | null;
    hidden: number;
    star: number;
    dip_buy: number;
    tags: string | null;
    watch_price: number | null;
    watch_price_date: string | null;
    pin_order: number;
  };

  type PriceSnapshot = {
    ts: number;
    code: string;
    name: string;
    price: number;
    volume: number;
    amount: number;
    change_pct: number;
    chg_amt: number;
    amp: number;
    turnover: number;
    vol_ratio: number;
    high: number;
    low: number;
    open: number;
    prev_close: number;
    amo1: number;
    amo2: number;
  };

  type MarketTurnover = {
    ts: number;
    sh: number;
    sz: number;
    total: number;
    sh_index: number;
    sz_index: number;
    sh_pct: number;
    sz_pct: number;
    verdict: string;
    chi_next: number;
    chi_next_pct: number;
    kc50: number;
    kc50_pct: number;
    hk_index: number;
    hk_index_pct: number;
    hk_tech: number;
    hk_tech_pct: number;
    hk_turnover: number;
    amo1: number;
    amo2: number;
  };

  function readMonitorConfigFromDb() {
    const db = new Database(TEST_CONFIG_DB, { readonly: true });
    try {
      const watchRows = db
        .prepare(
          `SELECT symbol, name, alias, list_type, cost, shares, hidden, star, dip_buy, tags, watch_price, watch_price_date, pin_order
           FROM monitor_watchlist`,
        )
        .all() as DbWatchRow[];
      const settingsRows = db
        .prepare("SELECT key, value FROM monitor_settings")
        .all() as { key: string; value: number }[];

      const watchlist: Record<string, Record<string, unknown>> = {};
      for (const r of watchRows) {
        let parsedTags: string[] | undefined;
        if (r.tags) {
          try { parsedTags = JSON.parse(r.tags); } catch { /* ignore */ }
        }
        watchlist[r.symbol] = {
          name: r.name,
          alias: r.alias ?? undefined,
          type: r.list_type,
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
      }

      const settings: Record<string, number> = {};
      for (const r of settingsRows) {
        settings[r.key] = Number(r.value);
      }

      return { watchlist, settings, empty: watchRows.length === 0 && settingsRows.length === 0 };
    } finally {
      db.close();
    }
  }

  function readLatestPriceSnapshots(): { snapshots: PriceSnapshot[]; ts: number } {
    const db = new Database(TEST_TRADING_DB, { readonly: true });
    try {
      const rows = db
        .prepare(
          `SELECT ts, code, name, price, volume, amount, change_pct, chg_amt, amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2
           FROM price_snapshots
           WHERE (code, ts) IN (
             SELECT code, MAX(ts) FROM price_snapshots GROUP BY code
           )`,
        )
        .all() as PriceSnapshot[];

      let ts = 0;
      for (const r of rows) {
        if (r.ts > ts) ts = r.ts;
      }
      return { snapshots: rows, ts };
    } finally {
      db.close();
    }
  }

  function readLatestMarketTurnover(): MarketTurnover | null {
    const db = new Database(TEST_TRADING_DB, { readonly: true });
    try {
      const row = db
        .prepare(
          `SELECT ts, sh, sz, total, sh_index, sz_index, sh_pct, sz_pct, verdict,
                  chi_next, chi_next_pct, kc50, kc50_pct, hk_index, hk_index_pct,
                  hk_tech, hk_tech_pct, hk_turnover, amo1, amo2
           FROM market_turnover
           ORDER BY ts DESC
           LIMIT 1`,
        )
        .get() as MarketTurnover | undefined;
      return row ?? null;
    } finally {
      db.close();
    }
  }

  function readAlertRules(): Record<string, { above: number | null; below: number | null }> {
    const db = new Database(TEST_CONFIG_DB, { readonly: true });
    try {
      const rows = db
        .prepare("SELECT symbol, above, below FROM alert_rules")
        .all() as { symbol: string; above: number | null; below: number | null }[];

      const alerts: Record<string, { above: number | null; below: number | null }> = {};
      for (const r of rows) {
        alerts[r.symbol] = { above: r.above ?? null, below: r.below ?? null };
      }
      return alerts;
    } finally {
      db.close();
    }
  }

  try {
    const { watchlist, settings } = readMonitorConfigFromDb();
    const { snapshots, ts } = readLatestPriceSnapshots();
    const alerts = readAlertRules();
    const turnover = readLatestMarketTurnover();

    const services: Record<string, unknown>[] = [];
    const existingIds = new Set<string>();

    for (const s of snapshots) {
      const id = s.code;
      const entry = watchlist[id];
      if (!entry) continue;
      existingIds.add(id);
      const alert = alerts[id];

      const price = Number(s.price) || 0;
      const cost = entry.cost != null ? Number(entry.cost) : null;
      const shares = entry.shares != null ? Number(entry.shares) : null;
      const type = (entry.type as string) || "watching";
      const isHolding = type === "holding";

      let pnl: number | null = null;
      if (isHolding && cost != null && cost !== 0 && price > 0) {
        pnl = Math.round(((price - cost) / Math.abs(cost)) * 10000) / 100;
      }

      services.push({
        id,
        name: (entry.name as string) || s.name || id,
        price,
        change: s.change_pct ?? 0,
        chgAmt: s.chg_amt ?? 0,
        vol: s.volume ?? 0,
        amount: s.amount ?? 0,
        amp: s.amp ?? 0,
        turnover: s.turnover ?? 0,
        volRatio: s.vol_ratio ?? 0,
        high: s.high ?? 0,
        low: s.low ?? 0,
        open: s.open ?? 0,
        prevClose: s.prev_close ?? 0,
        amo1: s.amo1 ?? 0,
        amo2: s.amo2 ?? 0,
        type,
        cost,
        shares,
        pnl,
        above: alert?.above ?? null,
        below: alert?.below ?? null,
        hidden: Boolean(entry.hidden),
        star: Boolean(entry.star),
        ...(entry.dip_buy ? { dip_buy: true } : {}),
        ...(entry.alias ? { alias: entry.alias } : {}),
        ...(entry.tags ? { tags: entry.tags } : {}),
        ...(entry.watch_price != null ? { watch_price: Number(entry.watch_price) } : {}),
        ...(entry.watch_price_date ? { watch_price_date: entry.watch_price_date } : {}),
        ...((entry as Record<string, unknown>).pin_order != null ? { pin_order: (entry as Record<string, unknown>).pin_order } : {}),
      });
    }

    for (const [id, entry] of Object.entries(watchlist)) {
      if (existingIds.has(id)) continue;
      const alert = alerts[id];
      const type = (entry.type as string) || "watching";
      const cost = entry.cost != null ? Number(entry.cost) : null;
      const shares = entry.shares != null ? Number(entry.shares) : null;

      services.push({
        id,
        name: (entry.name as string) || id,
        price: 0,
        change: 0,
        chgAmt: 0,
        vol: 0,
        amount: 0,
        amp: 0,
        turnover: 0,
        volRatio: 0,
        high: 0,
        low: 0,
        open: 0,
        prevClose: 0,
        amo1: 0,
        amo2: 0,
        type,
        cost,
        shares,
        pnl: null,
        above: alert?.above ?? null,
        below: alert?.below ?? null,
        hidden: Boolean(entry.hidden),
        star: Boolean(entry.star),
        ...(entry.dip_buy ? { dip_buy: true } : {}),
        ...(entry.alias ? { alias: entry.alias } : {}),
        ...(entry.tags ? { tags: entry.tags } : {}),
        ...(entry.watch_price != null ? { watch_price: Number(entry.watch_price) } : {}),
        ...(entry.watch_price_date ? { watch_price_date: entry.watch_price_date } : {}),
        ...((entry as Record<string, unknown>).pin_order != null ? { pin_order: (entry as Record<string, unknown>).pin_order } : {}),
      });
    }

    const marketTurnover = turnover
      ? {
          sh: turnover.sh ?? 0,
          sz: turnover.sz ?? 0,
          total: turnover.total ?? 0,
          shIndex: turnover.sh_index ?? 0,
          szIndex: turnover.sz_index ?? 0,
          shPct: turnover.sh_pct ?? 0,
          szPct: turnover.sz_pct ?? 0,
          verdict: turnover.verdict ?? "",
          chiNext: turnover.chi_next ?? 0,
          chiNextPct: turnover.chi_next_pct ?? 0,
          kc50: turnover.kc50 ?? 0,
          kc50Pct: turnover.kc50_pct ?? 0,
          hkIndex: turnover.hk_index ?? 0,
          hkIndexPct: turnover.hk_index_pct ?? 0,
          hkTech: turnover.hk_tech ?? 0,
          hkTechPct: turnover.hk_tech_pct ?? 0,
          hkTurnover: turnover.hk_turnover ?? 0,
          amo1: turnover.amo1 ?? 0,
          amo2: turnover.amo2 ?? 0,
        }
      : undefined;

    let alertEvents: unknown[] = [];
    let indicatorMap: Record<string, unknown> = {};
    try {
      const now = new Date();
      const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

      const db = new Database(TEST_TRADING_DB, { readonly: true });
      try {
        alertEvents = db
          .prepare(
            "SELECT ts, time, symbol, kind, level, message, display, change_pct " +
            "FROM alert_events WHERE date = ? ORDER BY ts",
          )
          .all(today);

        try {
          const indRows = db.prepare(
            "SELECT symbol, data_json FROM indicator_cache WHERE date = ?"
          ).all(today) as { symbol: string; data_json: string }[];
          for (const r of indRows) {
            try { indicatorMap[r.symbol] = JSON.parse(r.data_json); } catch { /* skip */ }
          }
        } catch { /* indicator_cache table may not exist yet */ }
      } finally {
        db.close();
      }
    } catch { /* alert_events read failure is non-fatal */ }

    if (Object.keys(indicatorMap).length > 0) {
      for (const svc of services) {
        const ind = indicatorMap[svc.id as string];
        if (ind) svc.indicators = ind;
      }
    }

    const availHkd = Number(settings.available_balance_hkd) || 0;
    const availRmb = Number(settings.available_balance_rmb) || 0;

    let posValHkd = 0;
    let posValRmb = 0;
    for (const svc of services as Array<Record<string, any>>) {
      if (svc.type === "holding" && svc.shares != null && svc.price > 0) {
        const mktVal = svc.price * svc.shares;
        if (svc.id.startsWith("HK")) posValHkd += mktVal;
        else posValRmb += mktVal;
      }
    }

    const totalAssetsHkd = availHkd + posValHkd;
    const totalAssetsRmb = availRmb + posValRmb;

    for (const svc of services as Array<Record<string, any>>) {
      if (svc.type !== "holding" || svc.shares == null || svc.price <= 0) {
        (svc as Record<string, unknown>).position_pct = null;
        continue;
      }
      const mktVal = svc.price * svc.shares;
      const total = svc.id.startsWith("HK") ? totalAssetsHkd : totalAssetsRmb;
      (svc as Record<string, unknown>).position_pct = total > 0
        ? Math.round((mktVal / total) * 10000) / 10000
        : 0;
    }

    const response: Record<string, unknown> = {
      services,
      ts,
      settings,
      alertEvents,
      portfolio: {
        hkd: {
          available_balance: availHkd,
          position_value: posValHkd,
          total_assets: totalAssetsHkd,
        },
        rmb: {
          available_balance: availRmb,
          position_value: posValRmb,
          total_assets: totalAssetsRmb,
        },
      },
    };

    if (marketTurnover) {
      response.marketTurnover = marketTurnover;
    }

    return NextResponse.json(response);
  } catch (e) {
    return NextResponse.json({ ...EMPTY, error: String(e) });
  }
}

describe("GET /api/metrics", () => {
  beforeAll(setupTestDbs);
  afterAll(cleanupTestDbs);

  it("returns empty services when watchlist is empty", async () => {
    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.services).toEqual([]);
    expect(body.ts).toBe(0);
    expect(body.settings).toEqual({});
    expect(body.portfolio).toEqual({
      hkd: { available_balance: 0, position_value: 0, total_assets: 0 },
      rmb: { available_balance: 0, position_value: 0, total_assets: 0 },
    });
  });

  it("correctly merges price_snapshots and watchlist data", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, pin_order)
      VALUES ('000001', '平安银行', 'holding', 10.5, 1000, 0, 1, 0);
    `);
    configDb.close();

    const tradingDb = new Database(TEST_TRADING_DB);
    tradingDb.exec(`
      INSERT INTO price_snapshots (ts, code, name, price, volume, amount, change_pct, chg_amt, amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2)
      VALUES (1714118400, '000001', '平安银行', 11.2, 50000, 560000, 6.67, 0.7, 2.5, 5.6, 1.2, 11.5, 10.8, 10.5, 10.5, 100, 200);
    `);
    tradingDb.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.services).toHaveLength(1);
    const svc = body.services[0];
    expect(svc.id).toBe("000001");
    expect(svc.name).toBe("平安银行");
    expect(svc.price).toBe(11.2);
    expect(svc.change).toBe(6.67);
    expect(svc.type).toBe("holding");
    expect(svc.cost).toBe(10.5);
    expect(svc.shares).toBe(1000);
    expect(svc.star).toBe(true);
    expect(svc.hidden).toBe(false);
  });

  it("fills price=0 for watchlist entries missing from price_snapshots", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, pin_order)
      VALUES ('000002', '万科A', 'watching', NULL, NULL, 0, 0, 0);
    `);
    configDb.close();

    const response = await getHandler();
    const body = await response.json();
    const svc = body.services.find((s: any) => s.id === "000002");
    expect(svc).toBeDefined();
    expect(svc.price).toBe(0);
    expect(svc.change).toBe(0);
    expect(svc.type).toBe("watching");
    expect(svc.cost).toBeNull();
    expect(svc.shares).toBeNull();
    expect(svc.pnl).toBeNull();
  });

  it("correctly calculates pnl for holding type", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, pin_order)
      VALUES ('HK00700', '腾讯控股', 'holding', 350.0, 500, 0, 0, 0);
    `);
    configDb.close();

    const tradingDb = new Database(TEST_TRADING_DB);
    tradingDb.exec(`
      INSERT INTO price_snapshots (ts, code, name, price, volume, amount, change_pct, chg_amt, amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2)
      VALUES (1714118400, 'HK00700', '腾讯控股', 385.0, 10000, 3850000, 10.0, 35.0, 3.0, 38.5, 1.5, 390.0, 380.0, 350.0, 350.0, 50, 100);
    `);
    tradingDb.close();

    const response = await getHandler();
    const body = await response.json();
    const svc = body.services.find((s: any) => s.id === "HK00700");
    expect(svc.pnl).toBe(10); // ((385 - 350) / 350) * 100 = 10%
  });

  it("correctly reads alert_rules above/below", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO alert_rules (symbol, above, below)
      VALUES ('000001', 12.0, 9.5);
    `);
    configDb.close();

    const response = await getHandler();
    const body = await response.json();
    const svc = body.services.find((s: any) => s.id === "000001");
    expect(svc.above).toBe(12.0);
    expect(svc.below).toBe(9.5);
  });

  it("correctly reads settings", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO monitor_settings (key, value) VALUES ('available_balance_hkd', 50000);
      INSERT INTO monitor_settings (key, value) VALUES ('available_balance_rmb', 100000);
    `);
    configDb.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.settings.available_balance_hkd).toBe(50000);
    expect(body.settings.available_balance_rmb).toBe(100000);
  });

  it("correctly calculates portfolio available_balance, position_value, total_assets", async () => {
    // Uses data from previous tests: HK00700 holding 500 shares @ 385, 000001 holding 1000 shares @ 11.2
    const response = await getHandler();
    const body = await response.json();
    // HK00700: 385 * 500 = 192500 HKD
    // 000001: 11.2 * 1000 = 11200 RMB
    expect(body.portfolio.hkd.available_balance).toBe(50000);
    expect(body.portfolio.hkd.position_value).toBe(192500);
    expect(body.portfolio.hkd.total_assets).toBe(242500);
    expect(body.portfolio.rmb.available_balance).toBe(100000);
    expect(body.portfolio.rmb.position_value).toBe(11200);
    expect(body.portfolio.rmb.total_assets).toBe(111200);
  });

  it("correctly calculates position_pct", async () => {
    const response = await getHandler();
    const body = await response.json();
    const hkSvc = body.services.find((s: any) => s.id === "HK00700");
    const rmbSvc = body.services.find((s: any) => s.id === "000001");
    // HK00700: 192500 / 242500 = ~0.7938
    expect(hkSvc.position_pct).toBeCloseTo(192500 / 242500, 4);
    // 000001: 11200 / 111200 = ~0.1007
    expect(rmbSvc.position_pct).toBeCloseTo(11200 / 111200, 4);
  });

  it("position_pct is null for non-holding or missing price/shares", async () => {
    const response = await getHandler();
    const body = await response.json();
    const watchingSvc = body.services.find((s: any) => s.id === "000002");
    expect(watchingSvc.position_pct).toBeNull();
  });

  it("correctly attaches indicator_cache data to services", async () => {
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const tradingDb = new Database(TEST_TRADING_DB);
    tradingDb.exec(`
      INSERT INTO indicator_cache (symbol, date, data_json)
      VALUES ('000001', '${today}', '{"rsi": 65, "macd": "bullish"}');
    `);
    tradingDb.close();

    const response = await getHandler();
    const body = await response.json();
    const svc = body.services.find((s: any) => s.id === "000001");
    expect(svc.indicators).toEqual({ rsi: 65, macd: "bullish" });
  });

  it("correctly reads alert_events", async () => {
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const tradingDb = new Database(TEST_TRADING_DB);
    tradingDb.exec(`
      INSERT INTO alert_events (date, ts, time, symbol, kind, level, message, display, change_pct)
      VALUES ('${today}', 1714118400, '10:00:00', '000001', 'price_spike', 1, 'Price spike alert', 'Price spike', 5.0);
    `);
    tradingDb.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.alertEvents).toHaveLength(1);
    expect(body.alertEvents[0].symbol).toBe("000001");
    expect(body.alertEvents[0].kind).toBe("price_spike");
  });

  it("correctly returns marketTurnover", async () => {
    const tradingDb = new Database(TEST_TRADING_DB);
    tradingDb.exec(`
      INSERT INTO market_turnover (ts, sh, sz, total, sh_index, sz_index, sh_pct, sz_pct, verdict, chi_next, chi_next_pct, kc50, kc50_pct, hk_index, hk_index_pct, hk_tech, hk_tech_pct, hk_turnover, amo1, amo2)
      VALUES (1714118400, 2500, 3200, 5700, 3050.5, 10200.3, 0.5, 0.8, 'balanced', 2100.1, 1.2, 1050.0, 0.3, 18000.0, 0.4, 4500.0, 0.6, 1200.0, 100, 200);
    `);
    tradingDb.close();

    const response = await getHandler();
    const body = await response.json();
    expect(body.marketTurnover).toBeDefined();
    expect(body.marketTurnover.sh).toBe(2500);
    expect(body.marketTurnover.sz).toBe(3200);
    expect(body.marketTurnover.total).toBe(5700);
    expect(body.marketTurnover.shIndex).toBe(3050.5);
    expect(body.marketTurnover.verdict).toBe("balanced");
    expect(body.marketTurnover.hkIndex).toBe(18000);
    expect(body.marketTurnover.hkIndexPct).toBe(0.4);
  });

  it("handles null alert_rules gracefully", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO alert_rules (symbol, above, below)
      VALUES ('000002', NULL, NULL);
    `);
    configDb.close();

    const response = await getHandler();
    const body = await response.json();
    const svc = body.services.find((s: any) => s.id === "000002");
    expect(svc.above).toBeNull();
    expect(svc.below).toBeNull();
  });

  it("handles tags JSON parsing", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, tags, pin_order)
      VALUES ('000003', '测试股', 'watching', NULL, NULL, 0, 0, '["tech", "growth"]', 0);
    `);
    configDb.close();

    const response = await getHandler();
    const body = await response.json();
    const svc = body.services.find((s: any) => s.id === "000003");
    expect(svc.tags).toEqual(["tech", "growth"]);
  });

  it("handles dip_buy boolean", async () => {
    const configDb = new Database(TEST_CONFIG_DB);
    configDb.exec(`
      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, hidden, star, dip_buy, pin_order)
      VALUES ('000004', '定投股', 'holding', 5.0, 2000, 0, 0, 1, 0);
    `);
    configDb.close();

    const tradingDb = new Database(TEST_TRADING_DB);
    tradingDb.exec(`
      INSERT INTO price_snapshots (ts, code, name, price, volume, amount, change_pct, chg_amt, amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2)
      VALUES (1714118400, '000004', '定投股', 5.5, 1000, 5500, 10, 0.5, 1, 0.55, 1, 5.6, 5.4, 5, 5, 10, 20);
    `);
    tradingDb.close();

    const response = await getHandler();
    const body = await response.json();
    const svc = body.services.find((s: any) => s.id === "000004");
    expect(svc.dip_buy).toBe(true);
  });
});
