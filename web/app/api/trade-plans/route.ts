import { NextResponse } from "next/server";
import { openTradingDb, openConfigDb } from "../../lib/db";

/* ── Types ── */

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

/* ── DB helpers ── */

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

function ensureScopeColumn(db: ReturnType<typeof openTradingDb>) {
  try {
    db.prepare("SELECT scope FROM trade_plans LIMIT 1").get();
  } catch {
    try {
      db.prepare("ALTER TABLE trade_plans ADD COLUMN scope TEXT NOT NULL DEFAULT 'real'").run();
    } catch { /* ignore */ }
  }
}

/* ── GET ── */

export async function GET() {
  const tdb = openTradingDb();
  const cdb = openConfigDb(true);
  try {
    ensureScopeColumn(tdb);

    // Read all trade plans
    const planRows = tdb
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

    // Read watchlist for position data
    const wlRows = cdb
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

    // Read latest price snapshots per code
    const priceRows = tdb
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
    tdb.close();
    cdb.close();
  }
}

/* ── POST ── */

export async function POST(request: Request) {
  const db = openTradingDb();
  try {
    const body = await request.json();
    const { action } = body;

    switch (action) {
      /* ── create ── */
      case "create": {
        const { id, plan } = body as { id: string; plan: Partial<TradePlan> };
        if (!id || !plan?.symbol || !plan?.name) {
          return NextResponse.json({ success: false, message: "Missing id, symbol, or name" }, { status: 400 });
        }

        const existing = db.prepare("SELECT 1 FROM trade_plans WHERE id = ?").get(id);
        if (existing) {
          return NextResponse.json({ success: false, message: `Plan ${id} already exists` }, { status: 400 });
        }

        // validate orders (shares required only for real/sim trade plans)
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

      /* ── update ── */
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

        // orders replacement
        if (updates.orders !== undefined) {
          // validate (shares required only for real/sim trade plans)
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

      /* ── delete ── */
      case "delete": {
        const { id } = body as { id: string };
        const existing = db.prepare("SELECT 1 FROM trade_plans WHERE id = ?").get(id);
        if (!existing) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        db.prepare("DELETE FROM trade_plans WHERE id = ?").run(id);
        return NextResponse.json({ success: true, message: `Deleted plan ${id}` });
      }

      /* ── toggle (active/paused) ── */
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

      /* ── reset (un-trigger an order) ── */
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
