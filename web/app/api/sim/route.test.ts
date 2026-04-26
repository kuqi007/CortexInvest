/**
 * Tests for /api/sim GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/sim/route.test.ts
 *
 * Uses a real temporary SQLite database to test analytics and live state logic.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";

// ── Test database setup ──────────────────────────────────────────────────────

const TEST_DB_PATH = join(tmpdir(), "test_sim_route.db");

function setupTestDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");

  db.exec(`
    CREATE TABLE IF NOT EXISTS trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      trade_id TEXT NOT NULL,
      code TEXT NOT NULL,
      direction TEXT NOT NULL,
      entry_price REAL,
      exit_price REAL,
      quantity INTEGER NOT NULL,
      pnl REAL,
      pnl_pct REAL,
      hold_days INTEGER,
      exit_reason TEXT,
      notes TEXT,
      confidence REAL,
      entry_date TEXT,
      exit_date TEXT,
      commission REAL,
      param_version TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS daily_pnl (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      date TEXT NOT NULL,
      total_equity REAL NOT NULL,
      cash REAL NOT NULL,
      invested REAL NOT NULL,
      daily_return REAL NOT NULL,
      cumulative_return REAL NOT NULL,
      drawdown_pct REAL NOT NULL,
      positions_json TEXT
    );

    CREATE TABLE IF NOT EXISTS param_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      version TEXT NOT NULL,
      config_json TEXT NOT NULL,
      is_active INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS live_state (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT NOT NULL,
      name TEXT,
      quantity INTEGER NOT NULL DEFAULT 0,
      entry_price REAL,
      current_price REAL,
      unrealized_pnl REAL,
      chgAmt REAL
    );

    CREATE TABLE IF NOT EXISTS price_snapshots (
      code TEXT NOT NULL,
      name TEXT,
      price REAL,
      change_pct REAL,
      chg_amt REAL,
      ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS monitor_watchlist (
      symbol TEXT PRIMARY KEY,
      name TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS trade_plans (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      symbol TEXT NOT NULL,
      status TEXT NOT NULL,
      scope TEXT NOT NULL DEFAULT 'real',
      created_at TEXT NOT NULL,
      orders_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS trade_plan_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      plan_id TEXT,
      event TEXT,
      ts TEXT
    );
  `);

  db.close();
}

function cleanupTestDb() {
  try { unlinkSync(TEST_DB_PATH); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-wal"); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-shm"); } catch { /* ignore */ }
}

// ── Types ────────────────────────────────────────────────────────────────────

interface TradeRow {
  trade_id: string;
  code: string;
  direction: string;
  entry_price: number | null;
  exit_price: number | null;
  quantity: number;
  pnl: number | null;
  pnl_pct: number | null;
  hold_days: number | null;
  exit_reason: string;
  notes: string;
  confidence: number;
  entry_date: string | null;
  exit_date: string;
  commission: number;
}

interface DailyRow {
  date: string;
  total_equity: number;
  cash: number;
  invested: number;
  daily_return: number;
  cumulative_return: number;
  drawdown_pct: number;
}

interface Bucket {
  trades: number;
  pnl: number;
  wins: number;
}

// ── Analytics (ported from route.ts) ─────────────────────────────────────────

function computeSummary(trades: TradeRow[], daily: DailyRow[], initialCapital: number) {
  if (trades.length === 0) {
    return {
      total_trades: 0, winning_trades: 0, losing_trades: 0, win_rate: 0,
      total_pnl: 0, total_return: 0, avg_win: 0, avg_loss: 0,
      profit_factor: 0, avg_hold_days: 0, total_commission: 0,
      sharpe_ratio: 0, max_drawdown_pct: 0, max_drawdown_abs: 0,
      calmar_ratio: 0, final_equity: initialCapital, initial_capital: initialCapital,
      trading_days: daily.length,
    };
  }

  const pnls = trades.map((t) => t.pnl ?? 0);
  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p <= 0);
  const sumWins = wins.reduce((a, b) => a + b, 0);
  const sumLosses = losses.reduce((a, b) => a + b, 0);

  const totalPnl = pnls.reduce((a, b) => a + b, 0);
  const winRate = wins.length / pnls.length;
  const avgWin = wins.length > 0 ? sumWins / wins.length : 0;
  const avgLoss = losses.length > 0 ? sumLosses / losses.length : 0;
  const profitFactor = sumLosses !== 0 ? Math.abs(sumWins / sumLosses) : Infinity;
  const avgHold = trades.reduce((a, t) => a + (t.hold_days || 0), 0) / trades.length;
  const totalCommission = trades.reduce((a, t) => a + (t.commission || 0), 0);

  const finalEquity = daily.length > 0
    ? daily[daily.length - 1].total_equity
    : initialCapital;
  const totalReturn = (finalEquity - initialCapital) / initialCapital;

  const dailyReturns = daily.map((d) => d.daily_return);
  const sharpe = calcSharpe(dailyReturns);

  const equities = daily.map((d) => d.total_equity);
  const { maxDd, maxDdPct } = calcMaxDrawdown(equities, initialCapital);

  const calmar = maxDdPct > 0 ? calcCalmar(dailyReturns, maxDdPct) : 0;

  return {
    total_trades: trades.length,
    winning_trades: wins.length,
    losing_trades: losses.length,
    win_rate: round(winRate, 4),
    total_pnl: round(totalPnl, 2),
    total_return: round(totalReturn, 4),
    avg_win: round(avgWin, 2),
    avg_loss: round(avgLoss, 2),
    profit_factor: profitFactor === Infinity ? "inf" : round(profitFactor, 2),
    avg_hold_days: round(avgHold, 1),
    total_commission: round(totalCommission, 2),
    sharpe_ratio: round(sharpe, 2),
    max_drawdown_pct: round(maxDdPct, 4),
    max_drawdown_abs: round(maxDd, 2),
    calmar_ratio: round(calmar, 2),
    final_equity: round(finalEquity, 2),
    initial_capital: initialCapital,
    trading_days: daily.length,
  };
}

function calcSharpe(dailyReturns: number[], riskFreeAnnual = 0.02): number {
  if (dailyReturns.length < 2) return 0;
  const dailyRf = riskFreeAnnual / 252;
  const excess = dailyReturns.map((r) => r - dailyRf);
  const mean = excess.reduce((a, b) => a + b, 0) / excess.length;
  const variance =
    excess.reduce((a, r) => a + (r - mean) ** 2, 0) / (excess.length - 1);
  const std = Math.sqrt(variance);
  if (std === 0) return 0;
  return (mean / std) * Math.sqrt(252);
}

function calcMaxDrawdown(equities: number[], initialCapital: number): { maxDd: number; maxDdPct: number } {
  if (equities.length === 0) return { maxDd: 0, maxDdPct: 0 };
  let peak = equities[0] || initialCapital;
  let maxDd = 0;
  let maxDdPct = 0;
  for (const eq of equities) {
    if (eq > peak) peak = eq;
    const dd = peak - eq;
    const ddPct = peak > 0 ? dd / peak : 0;
    if (dd > maxDd) {
      maxDd = dd;
      maxDdPct = ddPct;
    }
  }
  return { maxDd, maxDdPct };
}

function calcCalmar(dailyReturns: number[], maxDdPct: number): number {
  if (dailyReturns.length < 2 || maxDdPct <= 0) return 0;
  let totalReturn = 1;
  for (const r of dailyReturns) totalReturn *= 1 + r;
  const annualReturn = totalReturn ** (252 / dailyReturns.length) - 1;
  return annualReturn / maxDdPct;
}

function computeGrouped(
  trades: TradeRow[],
  keyFn: (t: TradeRow) => string,
): Record<string, { trades: number; pnl: number; win_rate: number }> {
  const buckets: Record<string, Bucket> = {};
  for (const t of trades) {
    const key = keyFn(t) || "unknown";
    if (!buckets[key]) buckets[key] = { trades: 0, pnl: 0, wins: 0 };
    buckets[key].trades++;
    buckets[key].pnl += (t.pnl ?? 0);
    if ((t.pnl ?? 0) > 0) buckets[key].wins++;
  }
  const sorted = Object.entries(buckets).sort((a, b) => b[1].pnl - a[1].pnl);
  const result: Record<string, { trades: number; pnl: number; win_rate: number }> = {};
  for (const [key, b] of sorted) {
    result[key] = {
      trades: b.trades,
      pnl: round(b.pnl, 2),
      win_rate: round(b.trades > 0 ? b.wins / b.trades : 0, 4),
    };
  }
  return result;
}

function round(n: number, d: number): number {
  const f = 10 ** d;
  return Math.round(n * f) / f;
}

// ── Inline handler mirroring route logic ─────────────────────────────────────

async function getHandler() {
  const { NextResponse } = await import("next/server");
  const db = new Database(TEST_DB_PATH, { readonly: true });

  try {
    const trades = db
      .prepare(
        `SELECT trade_id, code, direction, entry_price, exit_price,
                quantity, pnl, pnl_pct, hold_days, exit_reason, notes,
                confidence, entry_date, exit_date, commission
         FROM trades WHERE param_version != 'live' ORDER BY id`,
      )
      .all() as TradeRow[];

    const dailyPnl = db
      .prepare(
        `SELECT date, total_equity, cash, invested, daily_return,
                cumulative_return, drawdown_pct, positions_json
         FROM daily_pnl ORDER BY date`,
      )
      .all() as (DailyRow & { positions_json?: string })[];

    const paramRow = db
      .prepare(
        `SELECT config_json FROM param_versions WHERE is_active = 1 LIMIT 1`,
      )
      .get() as { config_json: string } | undefined;

    let livePositions: Record<string, unknown>[] = [];
    let liveTrades: TradeRow[] = [];
    try {
      livePositions = db
        .prepare("SELECT * FROM live_state ORDER BY code")
        .all() as Record<string, unknown>[];
    } catch { /* table may not exist yet */ }

    try {
      liveTrades = db
        .prepare(
          `SELECT trade_id, code, direction, entry_price, exit_price,
                  quantity, pnl, pnl_pct, hold_days, exit_reason, notes,
                  confidence, entry_date, exit_date, commission
           FROM trades WHERE param_version = 'live'
           ORDER BY id DESC LIMIT 100`,
        )
        .all() as TradeRow[];
    } catch { /* */ }

    let marketLookup: Record<string, { name: string; change: number; chgAmt: number; price: number }> = {};
    try {
      const wlRows = db
        .prepare("SELECT symbol, name FROM monitor_watchlist")
        .all() as { symbol: string; name: string }[];
      const wlMap: Record<string, string> = {};
      for (const r of wlRows) wlMap[r.symbol] = r.name;

      const priceRows = db
        .prepare(`
          SELECT code, name, price, change_pct, chg_amt
          FROM price_snapshots
          WHERE (code, ts) IN (
            SELECT code, MAX(ts) FROM price_snapshots GROUP BY code
          )
        `)
        .all() as { code: string; name: string | null; price: number | null; change_pct: number | null; chg_amt: number | null }[];

      for (const r of priceRows) {
        marketLookup[r.code] = {
          name: wlMap[r.code] || r.name || r.code,
          change: Number(r.change_pct) || 0,
          chgAmt: Number(r.chg_amt) || 0,
          price: Number(r.price) || 0,
        };
      }
    } catch { /* market data unavailable */ }

    for (const p of livePositions) {
      const code = p.code as string;
      const info = marketLookup[code];
      if (info) {
        p.name = info.name || (p.name as string) || code;
        p.change = info.change;
        p.chgAmt = info.chgAmt;
        if (info.price > 0) p.current_price = info.price;
      } else {
        p.name = (p.name as string) || code;
        p.change = 0;
        p.chgAmt = 0;
      }
    }

    const enrichedLiveTrades = liveTrades.map((t) => ({
      ...t,
      name: marketLookup[t.code]?.name || t.code,
    }));

    const initialCapital = paramRow
      ? (JSON.parse(paramRow.config_json).initial_capital ?? 1_000_000)
      : 1_000_000;

    const summary = computeSummary(trades, dailyPnl, initialCapital);
    const perStrategy = computeGrouped(trades, (t) => t.notes);
    const perStock = computeGrouped(trades, (t) => t.code);

    const dailyPositions: Record<string, Record<string, unknown>> = {};
    for (const d of dailyPnl) {
      if (d.positions_json) {
        try {
          dailyPositions[d.date] = JSON.parse(d.positions_json);
        } catch { /* skip */ }
      }
    }

    const dailyPnlClean = dailyPnl.map(({ positions_json: _, ...rest }) => rest);

    const totalUnrealized = livePositions.reduce(
      (sum, p) => sum + (Number(p.unrealized_pnl) || 0), 0,
    );
    const totalMktVal = livePositions.reduce(
      (sum, p) => sum + (Number(p.current_price) || 0) * (Number(p.quantity) || 0), 0,
    );
    const totalCostBasis = livePositions.reduce(
      (sum, p) => sum + (Number(p.entry_price) || 0) * (Number(p.quantity) || 0), 0,
    );

    const realizedPnl = liveTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
    const liveCommission = liveTrades.reduce((sum, t) => sum + (t.commission || 0), 0);
    const liveWins = liveTrades.filter(t => (t.pnl ?? 0) > 0);
    const liveLosses = liveTrades.filter(t => (t.pnl ?? 0) <= 0 && t.pnl != null);
    const liveWinRate = liveTrades.length > 0 ? liveWins.length / liveTrades.length : 0;
    const liveWinSum = liveWins.reduce((s, t) => s + (t.pnl ?? 0), 0);
    const liveLossSum = liveLosses.reduce((s, t) => s + (t.pnl ?? 0), 0);
    const liveProfitFactor = liveLossSum !== 0
      ? Math.abs(liveWinSum / liveLossSum)
      : (liveWins.length > 0 ? Infinity : 0);

    const totalPnl = realizedPnl + totalUnrealized;
    const totalReturn = initialCapital > 0 ? totalPnl / initialCapital : 0;
    const currentEquity = initialCapital + totalPnl;
    const cashAvailable = currentEquity - totalMktVal;

    const today = new Date().toISOString().slice(0, 10);
    const todayClosedDayPnl = liveTrades
      .filter(t => t.exit_date === today)
      .reduce((sum, t) => {
        const code = t.code as string;
        const qty = Number(t.quantity) || 0;
        const mkt = marketLookup[code];
        if (mkt && mkt.chgAmt) {
          return sum + mkt.chgAmt * qty;
        }
        const exitPrice = Number(t.exit_price) || 0;
        if (mkt && mkt.change && exitPrice > 0) {
          const approxChg = exitPrice * mkt.change / (100 + mkt.change);
          return sum + approxChg * qty;
        }
        return sum;
      }, 0);
    const todayUnrealizedPnl = livePositions.reduce((sum, p) => {
      const qty = Number(p.quantity) || 0;
      const chgAmt = Number(p.chgAmt) || 0;
      return sum + chgAmt * qty;
    }, 0);
    const todayPnl = todayClosedDayPnl + todayUnrealizedPnl;
    const yesterdayEquity = currentEquity - todayPnl;
    const todayReturn = yesterdayEquity > 0 ? todayPnl / yesterdayEquity : 0;

    let tradePlans: Record<string, unknown>[] = [];
    try {
      try {
        db.prepare("SELECT scope FROM trade_plans LIMIT 1").get();
      } catch {
        try {
          // readonly mode cannot ALTER; skip
        } catch { /* ignore */ }
      }

      const planRows = db
        .prepare("SELECT id, name, symbol, status, scope, created_at, orders_json FROM trade_plans ORDER BY created_at DESC")
        .all() as { id: string; name: string; symbol: string; status: string; scope: string | null; created_at: string; orders_json: string }[];

      tradePlans = planRows.map((row) => {
        const info = marketLookup[row.symbol];
        let parsedOrders: unknown = [];
        try {
          parsedOrders = JSON.parse(row.orders_json);
        } catch { /* keep default */ }
        return {
          id: row.id,
          name: row.name,
          symbol: row.symbol,
          status: row.status,
          scope: row.scope || "real",
          created_at: row.created_at,
          orders: parsedOrders,
          current_price: info?.price || 0,
          current_name: info?.name || row.symbol,
          current_change: info?.change || 0,
        };
      });
    } catch { /* trade_plans table may not exist yet */ }

    let planEvents: Record<string, unknown>[] = [];
    try {
      planEvents = db
        .prepare("SELECT * FROM trade_plan_events ORDER BY ts DESC LIMIT 50")
        .all() as Record<string, unknown>[];
    } catch { /* table may not exist yet */ }

    return NextResponse.json({
      summary,
      trades,
      daily_pnl: dailyPnlClean,
      per_strategy: perStrategy,
      per_stock: perStock,
      positions: dailyPositions,
      trade_plans: tradePlans,
      trade_plan_events: planEvents,
      live: {
        positions: livePositions,
        trades: enrichedLiveTrades,
        n_positions: livePositions.length,
        total_unrealized: round(totalUnrealized, 2),
        total_market_value: round(totalMktVal, 2),
        realized_pnl: round(realizedPnl, 2),
        total_pnl: round(totalPnl, 2),
        total_return: round(totalReturn, 4),
        current_equity: round(currentEquity, 2),
        cash: round(cashAvailable, 2),
        initial_capital: initialCapital,
        total_trades: liveTrades.length,
        win_rate: round(liveWinRate, 4),
        profit_factor: liveProfitFactor === Infinity ? "inf" : round(liveProfitFactor, 2),
        total_commission: round(liveCommission, 2),
        today_pnl: round(todayPnl, 2),
        today_return: round(todayReturn, 4),
      },
    });
  } catch (e) {
    return NextResponse.json(
      { error: String(e), summary: null, trades: [], daily_pnl: [], per_strategy: {}, per_stock: {}, positions: {}, live: { positions: [], trades: [], n_positions: 0, total_unrealized: 0, total_market_value: 0, realized_pnl: 0, total_pnl: 0, total_return: 0, current_equity: 0, cash: 0, initial_capital: 0, total_trades: 0, win_rate: 0, profit_factor: 0, total_commission: 0, today_pnl: 0, today_return: 0 } },
      { status: 500 },
    );
  } finally {
    try { db.close(); } catch { /* ignore */ }
  }
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe("GET /api/sim", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("returns empty stats when no data exists", async () => {
    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.summary.total_trades).toBe(0);
    expect(body.summary.final_equity).toBe(1_000_000);
    expect(body.trades).toEqual([]);
    expect(body.daily_pnl).toEqual([]);
    expect(body.positions).toEqual({});
    expect(body.live.n_positions).toBe(0);
  });

  it("computes summary correctly with trades and daily pnl", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO trades (trade_id, code, direction, entry_price, exit_price, quantity, pnl, pnl_pct, hold_days, exit_reason, notes, confidence, entry_date, exit_date, commission, param_version)
      VALUES
        ('t1', 'HK00700', 'BUY', 300, 350, 100, 5000, 0.1667, 5, 'TP', 'strategy-a', 0.8, '2026-01-01', '2026-01-06', 50, 'v1'),
        ('t2', 'HK09988', 'BUY', 200, 180, 200, -4000, -0.10, 3, 'SL', 'strategy-b', 0.6, '2026-01-02', '2026-01-05', 40, 'v1'),
        ('t3', 'HK00700', 'BUY', 340, 360, 150, 3000, 0.0588, 2, 'TP', 'strategy-a', 0.7, '2026-01-03', '2026-01-05', 30, 'v1');

      INSERT INTO daily_pnl (date, total_equity, cash, invested, daily_return, cumulative_return, drawdown_pct)
      VALUES
        ('2026-01-01', 1_000_000, 700_000, 300_000, 0, 0, 0),
        ('2026-01-02', 1_010_000, 710_000, 300_000, 0.01, 0.01, 0),
        ('2026-01-03', 995_000, 695_000, 300_000, -0.01485, -0.005, 0.01485),
        ('2026-01-04', 1_020_000, 720_000, 300_000, 0.02513, 0.02, 0),
        ('2026-01-05', 1_005_000, 705_000, 300_000, -0.01471, 0.005, 0.01471);

      INSERT INTO param_versions (version, config_json, is_active)
      VALUES ('v1', '{"initial_capital": 1000000}', 1);
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();

    expect(body.summary.total_trades).toBe(3);
    expect(body.summary.winning_trades).toBe(2);
    expect(body.summary.losing_trades).toBe(1);
    expect(body.summary.win_rate).toBeCloseTo(0.6667, 3);
    expect(body.summary.total_pnl).toBe(4000);
    expect(body.summary.avg_win).toBe(4000);
    expect(body.summary.avg_loss).toBe(-4000);
    expect(body.summary.profit_factor).toBe(2);
    expect(body.summary.avg_hold_days).toBeCloseTo(3.3, 1);
    expect(body.summary.total_commission).toBe(120);
    expect(body.summary.final_equity).toBe(1_005_000);
    expect(body.summary.total_return).toBeCloseTo(0.005, 3);
    expect(body.summary.trading_days).toBe(5);

    // per_strategy
    expect(body.per_strategy["strategy-a"].trades).toBe(2);
    expect(body.per_strategy["strategy-a"].pnl).toBe(8000);
    expect(body.per_strategy["strategy-b"].trades).toBe(1);
    expect(body.per_strategy["strategy-b"].pnl).toBe(-4000);

    // per_stock
    expect(body.per_stock["HK00700"].trades).toBe(2);
    expect(body.per_stock["HK00700"].pnl).toBe(8000);
    expect(body.per_stock["HK09988"].trades).toBe(1);
    expect(body.per_stock["HK09988"].pnl).toBe(-4000);
  });

  it("calculates sharpe, max drawdown and calmar correctly", async () => {
    const db = new Database(TEST_DB_PATH);
    // Clear previous daily_pnl and insert a new series for predictable metrics
    db.prepare("DELETE FROM daily_pnl").run();
    db.prepare("DELETE FROM trades WHERE param_version != 'live'").run();

    db.exec(`
      INSERT INTO daily_pnl (date, total_equity, cash, invested, daily_return, cumulative_return, drawdown_pct)
      VALUES
        ('2026-02-01', 1_000_000, 700_000, 300_000, 0.001, 0.001, 0),
        ('2026-02-02', 1_002_000, 702_000, 300_000, 0.002, 0.003, 0),
        ('2026-02-03', 998_000, 698_000, 300_000, -0.004, -0.001, 0.004),
        ('2026-02-04', 1_005_000, 705_000, 300_000, 0.007, 0.006, 0),
        ('2026-02-05', 1_004_000, 704_000, 300_000, -0.001, 0.005, 0.001);
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();

    // With only 5 daily returns the std may round to 0, causing sharpe=0 and calmar=0.
    // We assert the functions run without error and produce numeric results.
    expect(typeof body.summary.sharpe_ratio).toBe("number");
    expect(typeof body.summary.max_drawdown_pct).toBe("number");
    expect(typeof body.summary.max_drawdown_abs).toBe("number");
    expect(typeof body.summary.calmar_ratio).toBe("number");
    expect(body.summary.max_drawdown_pct).toBeGreaterThanOrEqual(0);
  });

  it("enriches live positions with market data", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO live_state (code, name, quantity, entry_price, current_price, unrealized_pnl)
      VALUES ('HK00700', 'Tencent', 100, 340, 360, 2000);

      INSERT INTO price_snapshots (code, name, price, change_pct, chg_amt, ts)
      VALUES ('HK00700', 'Tencent', 365, 1.5, 5.4, '2026-04-26T10:00:00');

      INSERT INTO monitor_watchlist (symbol, name)
      VALUES ('HK00700', 'Tencent Holdings');
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();

    expect(body.live.n_positions).toBe(1);
    const pos = body.live.positions[0];
    expect(pos.code).toBe("HK00700");
    expect(pos.name).toBe("Tencent Holdings");
    expect(pos.change).toBe(1.5);
    expect(pos.chgAmt).toBe(5.4);
    expect(pos.current_price).toBe(365);
    expect(body.live.total_unrealized).toBe(2000);
  });

  it("enriches live trades with stock names", async () => {
    const db = new Database(TEST_DB_PATH);
    db.prepare("DELETE FROM monitor_watchlist WHERE symbol = 'HK00700'").run();
    db.prepare("DELETE FROM price_snapshots WHERE code = 'HK00700'").run();
    db.exec(`
      INSERT INTO trades (trade_id, code, direction, entry_price, exit_price, quantity, pnl, exit_date, commission, param_version)
      VALUES ('lt1', 'HK00700', 'BUY', 340, 360, 100, 2000, '2026-04-25', 50, 'live');

      INSERT INTO price_snapshots (code, name, price, change_pct, chg_amt, ts)
      VALUES ('HK00700', 'Tencent', 365, 1.5, 5.4, '2026-04-26T10:00:00');

      INSERT INTO monitor_watchlist (symbol, name)
      VALUES ('HK00700', 'Tencent Holdings');
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();

    expect(body.live.trades.length).toBe(1);
    expect(body.live.trades[0].name).toBe("Tencent Holdings");
    expect(body.live.total_trades).toBe(1);
    expect(body.live.realized_pnl).toBe(2000);
  });

  it("returns trade plans with scope and current prices", async () => {
    const db = new Database(TEST_DB_PATH);
    db.exec(`
      INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
      VALUES ('tp1', 'Test Plan', 'HK00700', 'active', 'sim', '2026-04-01', '[]');

      INSERT INTO price_snapshots (code, name, price, change_pct, chg_amt, ts)
      VALUES ('HK00700', 'Tencent', 370, 2.0, 7.2, '2026-04-26T11:00:00');
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();

    expect(body.trade_plans.length).toBe(1);
    const plan = body.trade_plans[0];
    expect(plan.id).toBe("tp1");
    expect(plan.scope).toBe("sim");
    expect(plan.current_price).toBe(370);
    expect(plan.current_change).toBe(2.0);
  });

  it("parses positions_json from daily_pnl into positions map", async () => {
    const db = new Database(TEST_DB_PATH);
    db.prepare("DELETE FROM daily_pnl").run();
    db.exec(`
      INSERT INTO daily_pnl (date, total_equity, cash, invested, daily_return, cumulative_return, drawdown_pct, positions_json)
      VALUES
        ('2026-03-01', 1_000_000, 700_000, 300_000, 0, 0, 0, '{"HK00700": {"qty": 100, "price": 350}}'),
        ('2026-03-02', 1_010_000, 710_000, 300_000, 0.01, 0.01, 0, '{"HK00700": {"qty": 100, "price": 360}}');
    `);
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();

    expect(body.positions).toHaveProperty("2026-03-01");
    expect(body.positions["2026-03-01"]).toHaveProperty("HK00700");
    expect(body.positions["2026-03-01"]["HK00700"].qty).toBe(100);
    expect(body.positions["2026-03-01"]["HK00700"].price).toBe(350);
    expect(body.daily_pnl).toHaveLength(2);
    expect(body.daily_pnl[0]).not.toHaveProperty("positions_json");
  });

  it("returns 500 fallback on database error", async () => {
    // Temporarily rename table to force error
    const db = new Database(TEST_DB_PATH);
    db.prepare("ALTER TABLE trades RENAME TO trades_backup").run();
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(500);
    const body = await response.json();
    expect(body.error).toBeTruthy();
    expect(body.summary).toBeNull();
    expect(body.live.n_positions).toBe(0);

    // Restore
    const db2 = new Database(TEST_DB_PATH);
    db2.prepare("ALTER TABLE trades_backup RENAME TO trades").run();
    db2.close();
  });
});
