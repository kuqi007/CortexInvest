import { NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { openTradingDb, openConfigDb } from "../../lib/db";
import { buildAuditEventV2, insertTradingAuditOutbox, makeActor } from "../../lib/audit";

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

type PlanRow = {
  id: string;
  name: string;
  symbol: string;
  status: string;
  scope: string | null;
  created_at: string;
  orders_json: string;
};

/* ── DB helpers ── */

const VALID_STATUSES = new Set(["active", "paused"]);
const VALID_SCOPES = new Set(["real", "sim", "tick_monitor"]);

function safeParseOrders(raw: string): Order[] {
  try {
    const parsed = JSON.parse(raw || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function normalizeStatus(status: unknown): TradePlan["status"] {
  return typeof status === "string" && VALID_STATUSES.has(status)
    ? (status as TradePlan["status"])
    : "active";
}

function normalizeScope(scope: unknown): TradePlan["scope"] {
  return typeof scope === "string" && VALID_SCOPES.has(scope)
    ? (scope as TradePlan["scope"])
    : "real";
}

function rowToPlan(row: PlanRow): TradePlan {
  return {
    name: row.name,
    symbol: row.symbol,
    status: normalizeStatus(row.status),
    scope: normalizeScope(row.scope),
    created_at: row.created_at,
    orders: safeParseOrders(row.orders_json),
  };
}

function rowToAudit(row: PlanRow | undefined): Record<string, unknown> | null {
  if (!row) return null;
  return { id: row.id, ...rowToPlan(row) };
}

function ensureScopeColumn(db: ReturnType<typeof openTradingDb>) {
  try {
    db.prepare("SELECT scope FROM trade_plans LIMIT 1").get();
  } catch {
    try {
      db.prepare("ALTER TABLE trade_plans ADD COLUMN scope TEXT NOT NULL DEFAULT 'real'").run();
    } catch { /* ignore */ }
  }
  db.exec(`
    CREATE TABLE IF NOT EXISTS trading_audit_outbox (
      event_id TEXT PRIMARY KEY,
      schema_version INTEGER NOT NULL DEFAULT 1,
      correlation_id TEXT,
      ts TEXT NOT NULL,
      ts_ms INTEGER NOT NULL,
      source TEXT NOT NULL,
      action TEXT NOT NULL,
      entity TEXT NOT NULL,
      key TEXT NOT NULL,
      db TEXT NOT NULL DEFAULT 'trading.db' CHECK (db = 'trading.db'),
      payload_json TEXT NOT NULL,
      flushed_at TEXT,
      flushed_at_ms INTEGER,
      flush_id TEXT,
      flush_started_at_ms INTEGER,
      CHECK (length(trim(payload_json)) > 0)
    );
  `);
  const auditCols = new Set(
    (db.pragma("table_info(trading_audit_outbox)") as { name: string }[]).map((c) => c.name),
  );
  if (!auditCols.has("flushed_at")) {
    db.exec(`ALTER TABLE trading_audit_outbox ADD COLUMN flushed_at TEXT`);
  }
  if (!auditCols.has("flushed_at_ms")) {
    db.exec(`ALTER TABLE trading_audit_outbox ADD COLUMN flushed_at_ms INTEGER`);
  }
  if (!auditCols.has("flush_id")) {
    db.exec(`ALTER TABLE trading_audit_outbox ADD COLUMN flush_id TEXT`);
  }
  if (!auditCols.has("flush_started_at_ms")) {
    db.exec(`ALTER TABLE trading_audit_outbox ADD COLUMN flush_started_at_ms INTEGER`);
  }
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_trading_audit_outbox_pending
      ON trading_audit_outbox(ts_ms, event_id)
      WHERE flushed_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_trading_audit_outbox_correlation
      ON trading_audit_outbox(correlation_id, ts_ms)
      WHERE correlation_id IS NOT NULL;
  `);
}

function readPlanRow(db: ReturnType<typeof openTradingDb>, id: string): PlanRow | undefined {
  return db
    .prepare("SELECT id, name, symbol, status, scope, created_at, orders_json FROM trade_plans WHERE id = ?")
    .get(id) as PlanRow | undefined;
}

function recordTradePlanAudit(
  db: ReturnType<typeof openTradingDb>,
  args: {
    action: string;
    key: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
    metadata?: Record<string, unknown>;
  },
) {
  const nowMs = Date.now();
  insertTradingAuditOutbox(
    db,
    buildAuditEventV2({
      eventId: randomUUID(),
      tsMs: nowMs,
      correlationId: randomUUID(),
      source: "api_trade_plans",
      actor: makeActor({ type: "user", id: "local-ui" }),
      action: args.action,
      entity: "trade_plan",
      key: args.key,
      dbName: "trading.db",
      before: args.before,
      after: args.after,
      metadata: args.metadata,
    }),
  );
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
    ensureScopeColumn(db);
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
        const scope = normalizeScope(plan.scope);
        const status = normalizeStatus(plan.status);
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

        db.transaction(() => {
          db.prepare(
            `INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
             VALUES (?, ?, ?, ?, ?, ?, ?)`
          ).run(
            id,
            plan.name,
            plan.symbol,
            status,
            scope,
            plan.created_at || new Date().toISOString().slice(0, 10),
            JSON.stringify(orders)
          );
          recordTradePlanAudit(db, {
            action: "create",
            key: id,
            before: null,
            after: rowToAudit(readPlanRow(db, id)),
          });
        })();

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
        const newStatus = updates.status !== undefined ? normalizeStatus(updates.status) : plan.status;
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

        const newScope = updates.scope !== undefined ? normalizeScope(updates.scope) : plan.scope;

        db.transaction(() => {
          db.prepare(
            `UPDATE trade_plans SET name = ?, status = ?, scope = ?, orders_json = ?, updated_at = strftime('%s', 'now') WHERE id = ?`
          ).run(newName, newStatus, newScope, JSON.stringify(newOrders), id);
          recordTradePlanAudit(db, {
            action: "update",
            key: id,
            before: rowToAudit(row),
            after: rowToAudit(readPlanRow(db, id)),
          });
        })();

        return NextResponse.json({ success: true, message: `Updated plan ${id}` });
      }

      /* ── delete ── */
      case "delete": {
        const { id } = body as { id: string };
        const existing = readPlanRow(db, id);
        if (!existing) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        db.transaction(() => {
          db.prepare("DELETE FROM trade_plans WHERE id = ?").run(id);
          recordTradePlanAudit(db, {
            action: "delete",
            key: id,
            before: rowToAudit(existing),
            after: null,
          });
        })();
        return NextResponse.json({ success: true, message: `Deleted plan ${id}` });
      }

      /* ── toggle (active/paused) ── */
      case "toggle": {
        const { id } = body as { id: string };
        const row = readPlanRow(db, id);
        if (!row) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        const newStatus = row.status === "active" ? "paused" : "active";
        db.transaction(() => {
          db.prepare("UPDATE trade_plans SET status = ?, updated_at = strftime('%s', 'now') WHERE id = ?").run(newStatus, id);
          recordTradePlanAudit(db, {
            action: "toggle",
            key: id,
            before: rowToAudit(row),
            after: rowToAudit(readPlanRow(db, id)),
          });
        })();
        return NextResponse.json({ success: true, message: `${newStatus === "active" ? "Activated" : "Paused"} plan ${id}` });
      }

      /* ── reset (un-trigger an order) ── */
      case "reset": {
        const { id, order_id } = body as { id: string; order_id: string };
        const row = readPlanRow(db, id);
        if (!row) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        const orders: Order[] = safeParseOrders(row.orders_json);
        const order = orders.find((o) => o.id === order_id);
        if (!order) {
          return NextResponse.json({ success: false, message: `Order ${order_id} not found in plan ${id}` }, { status: 400 });
        }
        order.triggered = false;
        order.triggered_at = null;
        db.transaction(() => {
          db.prepare("UPDATE trade_plans SET orders_json = ?, updated_at = strftime('%s', 'now') WHERE id = ?").run(
            JSON.stringify(orders),
            id
          );
          recordTradePlanAudit(db, {
            action: "reset_order",
            key: id,
            before: rowToAudit(row),
            after: rowToAudit(readPlanRow(db, id)),
            metadata: { order_id },
          });
        })();
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
