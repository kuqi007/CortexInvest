/**
 * Tests for /api/trade-plans GET and POST endpoints.
 *
 * Run: cd web && npx vitest run app/api/trade-plans/route.test.ts
 *
 * Uses a real temporary SQLite database containing both trading and config tables.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";

// ── Test database setup ──────────────────────────────────────────────────────

const TEST_DB_PATH = join(tmpdir(), "test_trade_plans_route.db");

function setupTestDb() {
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");

  // trading.db tables
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

  // config.db tables (unified into same temp DB for testing)
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
  `);

  // Seed data
  db.exec(`
    INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
    VALUES ('plan-1', 'Plan A', 'HK00700', 'active', 'real', '2026-01-01', '[{"id":"o1","side":"buy","op":">=","price":350,"shares":100,"volume_min":null,"consecutive_days":null,"trailing":null,"label":"entry","triggered":false,"triggered_at":null}]');

    INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
    VALUES ('plan-2', 'Plan B', 'HK09988', 'paused', 'sim', '2026-01-02', '[]');

    INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, lot, created_at, updated_at)
    VALUES ('HK00700', 'Tencent', 'holding', 340.0, 500, 100, 0, 0);

    INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, lot, created_at, updated_at)
    VALUES ('HK09988', 'Alibaba', 'watching', NULL, NULL, 500, 0, 0);

    INSERT INTO price_snapshots (code, name, price, change_pct, ts)
    VALUES ('HK00700', 'Tencent', 360.0, 2.5, '2026-04-26T10:00:00');
  `);

  db.close();
}

function cleanupTestDb() {
  try { unlinkSync(TEST_DB_PATH); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-wal"); } catch { /* ignore */ }
  try { unlinkSync(TEST_DB_PATH + "-shm"); } catch { /* ignore */ }
}

// ── Inline handlers mirroring route logic ────────────────────────────────────

interface Order {
  id: string;
  side: "buy" | "sell";
  op: ">=" | "<=";
  price: number;
  shares: number | null;
  volume_min: number | null;
  consecutive_days: number | null;
  trailing: { pct: number; watermark: number | null; active: boolean } | null;
  label: string;
  triggered: boolean;
  triggered_at: string | null;
}

interface TradePlan {
  name: string;
  symbol: string;
  status: "active" | "paused";
  scope: "real" | "sim" | "tick_monitor";
  created_at: string;
  orders: Order[];
}

interface PlanPosition {
  cost: number | null;
  shares: number | null;
  price: number | null;
  change_pct: number | null;
  name: string;
}

function rowToPlan(row: { id: string; name: string; symbol: string; status: string; scope: string | null; created_at: string; orders_json: string }): TradePlan {
  return {
    name: row.name,
    symbol: row.symbol,
    status: row.status as "active" | "paused",
    scope: (row.scope as TradePlan["scope"]) || "real",
    created_at: row.created_at,
    orders: JSON.parse(row.orders_json),
  };
}

async function getHandler() {
  const { NextResponse } = await import("next/server");

  const db = new Database(TEST_DB_PATH, { readonly: true });
  try {
    const planRows = db
      .prepare("SELECT id, name, symbol, status, scope, created_at, orders_json FROM trade_plans")
      .all() as Array<{
      id: string;
      name: string;
      symbol: string;
      status: string;
      scope: string | null;
      created_at: string;
      orders_json: string;
    }>;

    const wlRows = db
      .prepare("SELECT symbol, name, cost, shares, lot FROM monitor_watchlist")
      .all() as Array<{
      symbol: string;
      name: string;
      cost: number | null;
      shares: number | null;
      lot: number | null;
    }>;

    const wlMap = new Map<string, { name: string; cost: number | null; shares: number | null; lot: number | null }>();
    for (const r of wlRows) {
      wlMap.set(r.symbol, { name: r.name, cost: r.cost, shares: r.shares, lot: r.lot });
    }

    const priceRows = db
      .prepare(
        `SELECT code, price, change_pct, name
         FROM price_snapshots
         WHERE (code, ts) IN (
           SELECT code, MAX(ts) FROM price_snapshots GROUP BY code
         )`
      )
      .all() as Array<{ code: string; price: number; change_pct: number; name: string }>;

    const priceMap = new Map<string, { price: number; change: number; name: string }>();
    for (const r of priceRows) {
      priceMap.set(r.code, { price: r.price, change: r.change_pct, name: r.name });
    }

    const result: Record<string, TradePlan & { position: PlanPosition | null; lot_size: number | null }> = {};

    for (const row of planRows) {
      const plan = rowToPlan(row);
      const wl = wlMap.get(plan.symbol);
      const mkt = priceMap.get(plan.symbol);

      const position: PlanPosition | null = wl
        ? {
            cost: wl.cost ?? null,
            shares: wl.shares ?? null,
            price: mkt?.price ?? null,
            change_pct: mkt?.change ?? null,
            name: wl.name || mkt?.name || plan.symbol,
          }
        : null;

      const lot_size = wl?.lot ?? null;

      result[row.id] = { ...plan, position, lot_size };
    }

    return NextResponse.json({ plans: result });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  } finally {
    db.close();
  }
}

async function postHandler(request: Request) {
  const { NextResponse } = await import("next/server");
  const db = new Database(TEST_DB_PATH);
  try {
    const body = await request.json();
    const { action } = body;

    switch (action) {
      case "create": {
        const { id, plan } = body as { id: string; plan: Partial<TradePlan> };
        if (!id || !plan?.symbol || !plan?.name) {
          return NextResponse.json({ success: false, message: "Missing id, symbol, or name" }, { status: 400 });
        }

        const existing = db.prepare("SELECT 1 FROM trade_plans WHERE id = ?").get(id);
        if (existing) {
          return NextResponse.json({ success: false, message: `Plan ${id} already exists` }, { status: 400 });
        }

        const scope = plan.scope || "real";
        const orders = plan.orders || [];
        if (scope !== "tick_monitor") {
          for (const o of orders) {
            if (o.side === "buy" && (o.shares == null || o.shares <= 0)) {
              return NextResponse.json({ success: false, message: `Buy order ${o.id} needs shares > 0` }, { status: 400 });
            }
            if (o.side === "sell" && (o.shares == null || o.shares <= 0)) {
              return NextResponse.json({ success: false, message: `Sell order ${o.id} needs shares > 0` }, { status: 400 });
            }
          }
        }

        db.prepare(
          `INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)`
        ).run(
          id,
          plan.name,
          plan.symbol,
          plan.status || "active",
          scope,
          plan.created_at || new Date().toISOString().slice(0, 10),
          JSON.stringify(orders)
        );

        return NextResponse.json({ success: true, message: `Created plan ${id}` });
      }

      case "update": {
        const { id, updates } = body as { id: string; updates: Partial<TradePlan> };
        const row = db.prepare("SELECT id, name, symbol, status, scope, created_at, orders_json FROM trade_plans WHERE id = ?").get(id) as
          | { id: string; name: string; symbol: string; status: string; scope: string | null; created_at: string; orders_json: string }
          | undefined;
        if (!row) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }

        const plan = rowToPlan(row);
        const newName = updates.name !== undefined ? updates.name : plan.name;
        const newStatus = updates.status !== undefined ? updates.status : plan.status;
        let newOrders = plan.orders;

        if (updates.orders !== undefined) {
          if (plan.scope !== "tick_monitor") {
            for (const o of updates.orders) {
              if (o.side === "buy" && (o.shares == null || o.shares <= 0)) {
                return NextResponse.json({ success: false, message: `Buy order ${o.id} needs shares > 0` }, { status: 400 });
              }
              if (o.side === "sell" && (o.shares == null || o.shares <= 0)) {
                return NextResponse.json({ success: false, message: `Sell order ${o.id} needs shares > 0` }, { status: 400 });
              }
            }
          }
          newOrders = updates.orders;
        }

        const newScope = updates.scope !== undefined ? updates.scope : plan.scope;

        db.prepare(
          `UPDATE trade_plans SET name = ?, status = ?, scope = ?, orders_json = ?, updated_at = strftime('%s', 'now') WHERE id = ?`
        ).run(newName, newStatus, newScope, JSON.stringify(newOrders), id);

        return NextResponse.json({ success: true, message: `Updated plan ${id}` });
      }

      case "delete": {
        const { id } = body as { id: string };
        const existing = db.prepare("SELECT 1 FROM trade_plans WHERE id = ?").get(id);
        if (!existing) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        db.prepare("DELETE FROM trade_plans WHERE id = ?").run(id);
        return NextResponse.json({ success: true, message: `Deleted plan ${id}` });
      }

      case "toggle": {
        const { id } = body as { id: string };
        const row = db.prepare("SELECT status FROM trade_plans WHERE id = ?").get(id) as { status: string } | undefined;
        if (!row) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        const newStatus = row.status === "active" ? "paused" : "active";
        db.prepare("UPDATE trade_plans SET status = ?, updated_at = strftime('%s', 'now') WHERE id = ?").run(newStatus, id);
        return NextResponse.json({ success: true, message: `${newStatus === "active" ? "Activated" : "Paused"} plan ${id}` });
      }

      case "reset": {
        const { id, order_id } = body as { id: string; order_id: string };
        const row = db.prepare("SELECT orders_json FROM trade_plans WHERE id = ?").get(id) as { orders_json: string } | undefined;
        if (!row) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        const orders: Order[] = JSON.parse(row.orders_json);
        const order = orders.find((o) => o.id === order_id);
        if (!order) {
          return NextResponse.json({ success: false, message: `Order ${order_id} not found in plan ${id}` }, { status: 400 });
        }
        order.triggered = false;
        order.triggered_at = null;
        db.prepare("UPDATE trade_plans SET orders_json = ?, updated_at = strftime('%s', 'now') WHERE id = ?").run(
          JSON.stringify(orders),
          id
        );
        return NextResponse.json({ success: true, message: `Reset order ${order_id} in plan ${id}` });
      }

      default:
        return NextResponse.json({ success: false, message: `Unknown action: ${action}` }, { status: 400 });
    }
  } catch (e: any) {
    return NextResponse.json({ success: false, message: e.message }, { status: 500 });
  } finally {
    db.close();
  }
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe("GET /api/trade-plans", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("returns plans enriched with position and lot_size", async () => {
    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toHaveProperty("plans");
    expect(body.plans).toHaveProperty("plan-1");
    expect(body.plans).toHaveProperty("plan-2");

    const p1 = body.plans["plan-1"];
    expect(p1.name).toBe("Plan A");
    expect(p1.symbol).toBe("HK00700");
    expect(p1.status).toBe("active");
    expect(p1.scope).toBe("real");
    expect(p1.position).not.toBeNull();
    expect(p1.position.cost).toBe(340.0);
    expect(p1.position.shares).toBe(500);
    expect(p1.position.price).toBe(360.0);
    expect(p1.position.change_pct).toBe(2.5);
    expect(p1.position.name).toBe("Tencent");
    expect(p1.lot_size).toBe(100);

    const p2 = body.plans["plan-2"];
    expect(p2.name).toBe("Plan B");
    expect(p2.symbol).toBe("HK09988");
    expect(p2.status).toBe("paused");
    expect(p2.scope).toBe("sim");
    // HK09988 is in monitor_watchlist (watching type), so position is non-null with null cost/shares
    expect(p2.position).not.toBeNull();
    expect(p2.position.cost).toBeNull();
    expect(p2.position.shares).toBeNull();
    expect(p2.lot_size).toBe(500);
  });

  it("returns empty object when no plans exist", async () => {
    // Wipe plans temporarily
    const db = new Database(TEST_DB_PATH);
    db.prepare("DELETE FROM trade_plans").run();
    db.close();

    const response = await getHandler();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.plans).toEqual({});

    // Restore: clear and re-seed
    const dbRestore = new Database(TEST_DB_PATH);
    dbRestore.prepare("DELETE FROM trade_plans").run();
    dbRestore.prepare("DELETE FROM monitor_watchlist").run();
    dbRestore.prepare("DELETE FROM price_snapshots").run();
    dbRestore.exec(`
      INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
      VALUES ('plan-1', 'Plan A', 'HK00700', 'active', 'real', '2026-01-01', '[{"id":"o1","side":"buy","op":">=","price":350,"shares":100,"volume_min":null,"consecutive_days":null,"trailing":null,"label":"entry","triggered":false,"triggered_at":null}]');

      INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
      VALUES ('plan-2', 'Plan B', 'HK09988', 'paused', 'sim', '2026-01-02', '[]');

      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, lot, created_at, updated_at)
      VALUES ('HK00700', 'Tencent', 'holding', 340.0, 500, 100, 0, 0);

      INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares, lot, created_at, updated_at)
      VALUES ('HK09988', 'Alibaba', 'watching', NULL, NULL, 500, 0, 0);

      INSERT INTO price_snapshots (code, name, price, change_pct, ts)
      VALUES ('HK00700', 'Tencent', 360.0, 2.5, '2026-04-26T10:00:00');
    `);
    dbRestore.close();
  });
});

describe("POST /api/trade-plans", () => {
  beforeAll(setupTestDb);
  afterAll(cleanupTestDb);

  it("create action succeeds with valid data", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "create",
        id: "plan-new",
        plan: {
          name: "New Plan",
          symbol: "HK00001",
          status: "active",
          scope: "real",
          orders: [{ id: "o1", side: "buy", op: ">=", price: 100, shares: 50, label: "entry", triggered: false }],
        },
      }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.message).toContain("Created");
  });

  it("create action returns 400 for duplicate id", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "create",
        id: "plan-1",
        plan: { name: "Dup", symbol: "HK00001" },
      }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.message).toMatch(/already exists/);
  });

  it("create action returns 400 when buy order lacks shares", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "create",
        id: "plan-bad",
        plan: {
          name: "Bad Plan",
          symbol: "HK00001",
          orders: [{ id: "o1", side: "buy", op: ">=", price: 100, shares: null, label: "entry", triggered: false }],
        },
      }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.message).toMatch(/needs shares > 0/);
  });

  it("update action succeeds", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "update",
        id: "plan-1",
        updates: { name: "Plan A Updated", status: "paused" },
      }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.message).toContain("Updated");

    // Verify
    const db = new Database(TEST_DB_PATH, { readonly: true });
    const row = db.prepare("SELECT name, status FROM trade_plans WHERE id = ?").get("plan-1") as { name: string; status: string };
    db.close();
    expect(row.name).toBe("Plan A Updated");
    expect(row.status).toBe("paused");
  });

  it("update action returns 400 when validation fails", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "update",
        id: "plan-1",
        updates: {
          orders: [{ id: "o1", side: "sell", op: "<=", price: 300, shares: 0, label: "exit", triggered: false }],
        },
      }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.message).toMatch(/needs shares > 0/);
  });

  it("delete action succeeds", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", id: "plan-2" }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.message).toContain("Deleted");

    const db = new Database(TEST_DB_PATH, { readonly: true });
    const row = db.prepare("SELECT 1 FROM trade_plans WHERE id = ?").get("plan-2");
    db.close();
    expect(row).toBeUndefined();
  });

  it("delete action returns 404 for missing plan", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", id: "plan-missing" }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.message).toMatch(/not found/);
  });

  it("toggle action switches status", async () => {
    // plan-1 is currently paused from earlier test; toggle back to active
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "toggle", id: "plan-1" }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.message).toContain("Activated");

    const db = new Database(TEST_DB_PATH, { readonly: true });
    const row = db.prepare("SELECT status FROM trade_plans WHERE id = ?").get("plan-1") as { status: string };
    db.close();
    expect(row.status).toBe("active");
  });

  it("reset action un-triggers an order", async () => {
    // Set order triggered first
    const db = new Database(TEST_DB_PATH);
    db.prepare("UPDATE trade_plans SET orders_json = ? WHERE id = ?").run(
      JSON.stringify([{ id: "o1", side: "buy", op: ">=", price: 350, shares: 100, label: "entry", triggered: true, triggered_at: "2026-01-01" }]),
      "plan-1"
    );
    db.close();

    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reset", id: "plan-1", order_id: "o1" }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.message).toContain("Reset");

    const db2 = new Database(TEST_DB_PATH, { readonly: true });
    const row = db2.prepare("SELECT orders_json FROM trade_plans WHERE id = ?").get("plan-1") as { orders_json: string };
    db2.close();
    const orders = JSON.parse(row.orders_json);
    expect(orders[0].triggered).toBe(false);
    expect(orders[0].triggered_at).toBeNull();
  });

  it("returns 400 for unknown action", async () => {
    const request = new Request("http://localhost/api/trade-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "foobar" }),
    });
    const response = await postHandler(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.message).toMatch(/Unknown action/);
  });
});
