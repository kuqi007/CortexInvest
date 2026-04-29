import { NextResponse } from "next/server";
import Database from "better-sqlite3";
import { randomUUID } from "crypto";
import { openConfigDb, openTradingDb } from "../../lib/db";
import { buildAuditEventV2, insertConfigAuditOutbox, makeActor, recordDbChangeBestEffort } from "../../lib/audit";

import type { WatchEntry, MonitorConfig } from "../../types";
import { EM_UT } from "../../theme";

const EM_API = "https://push2.eastmoney.com/api/qt/ulist.np/get";

/** Read current price for a stock from price_snapshots DB. Returns null if unavailable. */
function readMarketPrice(code: string): number | null {
  const db = openTradingDb(true);
  try {
    const row = db
      .prepare("SELECT price FROM price_snapshots WHERE code = ? ORDER BY ts DESC LIMIT 1")
      .get(code) as { price: number } | undefined;
    return row?.price != null ? Number(row.price) : null;
  } catch {
    return null;
  } finally {
    db.close();
  }
}

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ── Config (monitor_config.json snapshot only) ──

type NormalizedMonitorConfig = MonitorConfig & {
  holdings: Record<string, WatchEntry>;
  watching: Record<string, WatchEntry>;
};

function splitWatchlist(
  watchlist: Record<string, WatchEntry>,
): { holdings: Record<string, WatchEntry>; watching: Record<string, WatchEntry> } {
  const holdings: Record<string, WatchEntry> = {};
  const watching: Record<string, WatchEntry> = {};
  for (const [code, entry] of Object.entries(watchlist || {})) {
    if (entry?.type === "holding") holdings[code] = entry;
    else watching[code] = entry;
  }
  return { holdings, watching };
}

function mergeSplitLists(
  holdings: Record<string, WatchEntry>,
  watching: Record<string, WatchEntry>,
): Record<string, WatchEntry> {
  const merged: Record<string, WatchEntry> = {};
  for (const [code, entry] of Object.entries(holdings || {})) {
    merged[code] = { ...entry, type: "holding" };
  }
  for (const [code, entry] of Object.entries(watching || {})) {
    // If both lists contain same symbol, holding wins.
    if (!merged[code]) merged[code] = { ...entry, type: "watching" };
  }
  return merged;
}

function normalizeConfig(raw: unknown): NormalizedMonitorConfig {
  const obj = (raw || {}) as {
    watchlist?: Record<string, WatchEntry>;
    holdings?: Record<string, WatchEntry>;
    watching?: Record<string, WatchEntry>;
    settings?: Record<string, number>;
  };
  const settings = obj.settings || {};

  let watchlist: Record<string, WatchEntry> = {};
  if (obj.watchlist && Object.keys(obj.watchlist).length > 0) {
    watchlist = obj.watchlist;
  } else {
    watchlist = mergeSplitLists(obj.holdings || {}, obj.watching || {});
  }
  const { holdings, watching } = splitWatchlist(watchlist);
  return { watchlist, holdings, watching, settings };
}

// ── Config DB helpers (monitor_* tables) ──

type MonitorDb = Database.Database;

type WatchRow = {
  symbol: string;
  name: string;
  alias: string | null;
  list_type: "holding" | "watching";
  cost: number | null;
  shares: number | null;
  lot: number | null;
  hidden: number;
  star: number;
  dip_buy: number;
  tags: string | null;
  watch_price: number | null;
  watch_price_date: string | null;
  pin_order: number;
};

function openMonitorDb(readonly = false): MonitorDb {
  return openConfigDb(readonly);
}

function ensureMonitorTables(db: MonitorDb) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS monitor_watchlist (
      symbol TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      list_type TEXT NOT NULL CHECK (list_type IN ('holding', 'watching')),
      cost REAL,
      shares INTEGER,
      lot INTEGER,
      hidden INTEGER NOT NULL DEFAULT 0,
      star INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_monitor_watchlist_type ON monitor_watchlist(list_type);
    CREATE INDEX IF NOT EXISTS idx_monitor_watchlist_updated ON monitor_watchlist(updated_at);
  `);
  // Safe column-add migrations (SQLite throws if column already exists)
  const existingCols = new Set(
    (db.pragma("table_info(monitor_watchlist)") as { name: string }[]).map((c) => c.name),
  );
  if (!existingCols.has("dip_buy")) {
    db.exec(`ALTER TABLE monitor_watchlist ADD COLUMN dip_buy INTEGER NOT NULL DEFAULT 0`);
  }
  if (!existingCols.has("alias")) {
    db.exec(`ALTER TABLE monitor_watchlist ADD COLUMN alias TEXT`);
  }
  if (!existingCols.has("tags")) {
    db.exec(`ALTER TABLE monitor_watchlist ADD COLUMN tags TEXT DEFAULT '[]'`);
  }
  if (!existingCols.has("watch_price")) {
    db.exec(`ALTER TABLE monitor_watchlist ADD COLUMN watch_price REAL`);
  }
  if (!existingCols.has("watch_price_date")) {
    db.exec(`ALTER TABLE monitor_watchlist ADD COLUMN watch_price_date TEXT`);
  }
  if (!existingCols.has("pin_order")) {
    db.exec(`ALTER TABLE monitor_watchlist ADD COLUMN pin_order INTEGER NOT NULL DEFAULT 0`);
  }
  db.exec(`
    CREATE TABLE IF NOT EXISTS monitor_settings (
      key TEXT PRIMARY KEY,
      value REAL NOT NULL,
      updated_at INTEGER NOT NULL
    );
  `);
  db.exec(`
    CREATE TABLE IF NOT EXISTS tag_meta (
      tag TEXT PRIMARY KEY,
      star INTEGER DEFAULT 0,
      watch INTEGER DEFAULT 1,
      baseline_value REAL DEFAULT 100,
      parent TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    );
  `);
  // Migration: add parent column to tag_meta if missing
  const tagMetaCols = new Set(
    (db.pragma("table_info(tag_meta)") as { name: string }[]).map((c) => c.name),
  );
  if (!tagMetaCols.has("parent")) {
    db.exec(`ALTER TABLE tag_meta ADD COLUMN parent TEXT`);
  }
  // 持仓变更记录表
  db.exec(`
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
  try { db.exec(`ALTER TABLE position_change_log ADD COLUMN type_from TEXT`); } catch {}
  try { db.exec(`ALTER TABLE position_change_log ADD COLUMN type_to TEXT`); } catch {}

  // alert_rules table
  db.exec(`
    CREATE TABLE IF NOT EXISTS alert_rules (
      symbol TEXT PRIMARY KEY,
      above REAL,
      below REAL,
      updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
    );
  `);
  db.exec(`
    CREATE TABLE IF NOT EXISTS config_audit_outbox (
      event_id TEXT PRIMARY KEY,
      schema_version INTEGER NOT NULL DEFAULT 1,
      correlation_id TEXT,
      ts TEXT NOT NULL,
      ts_ms INTEGER NOT NULL,
      source TEXT NOT NULL,
      action TEXT NOT NULL,
      entity TEXT NOT NULL,
      key TEXT NOT NULL,
      db TEXT NOT NULL DEFAULT 'config.db' CHECK (db = 'config.db'),
      payload_json TEXT NOT NULL,
      flushed_at TEXT,
      flushed_at_ms INTEGER,
      flush_id TEXT,
      flush_started_at_ms INTEGER,
      CHECK (length(trim(payload_json)) > 0)
    );
  `);
  const auditCols = new Set(
    (db.pragma("table_info(config_audit_outbox)") as { name: string }[]).map((c) => c.name),
  );
  if (!auditCols.has("flushed_at")) {
    db.exec(`ALTER TABLE config_audit_outbox ADD COLUMN flushed_at TEXT`);
  }
  if (!auditCols.has("flushed_at_ms")) {
    db.exec(`ALTER TABLE config_audit_outbox ADD COLUMN flushed_at_ms INTEGER`);
  }
  if (!auditCols.has("flush_id")) {
    db.exec(`ALTER TABLE config_audit_outbox ADD COLUMN flush_id TEXT`);
  }
  if (!auditCols.has("flush_started_at_ms")) {
    db.exec(`ALTER TABLE config_audit_outbox ADD COLUMN flush_started_at_ms INTEGER`);
  }
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_config_audit_outbox_pending
      ON config_audit_outbox(ts_ms, event_id)
      WHERE flushed_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_config_audit_outbox_correlation
      ON config_audit_outbox(correlation_id, ts_ms)
      WHERE correlation_id IS NOT NULL;
  `);
}

function readConfigFromDb(db: MonitorDb, ensureSchema = true): MonitorConfig {
  if (ensureSchema) ensureMonitorTables(db);
  const rows = db
    .prepare(
      `SELECT symbol, name, alias, list_type, cost, shares, lot, hidden, star, dip_buy,
              tags, watch_price, watch_price_date, pin_order
       FROM monitor_watchlist
       ORDER BY symbol`,
    )
    .all() as WatchRow[];

  const settingsRows = db
    .prepare("SELECT key, value FROM monitor_settings ORDER BY key")
    .all() as { key: string; value: number }[];

  const watchlist: Record<string, WatchEntry> = {};
  const holdings: Record<string, WatchEntry> = {};
  const watching: Record<string, WatchEntry> = {};
  for (const r of rows) {
    const entry: WatchEntry = { name: r.name };
    if (r.alias) entry.alias = r.alias;
    if (r.list_type === "holding") entry.type = "holding";
    if (r.cost != null) entry.cost = Number(r.cost);
    if (r.shares != null) entry.shares = Number(r.shares);
    if (r.lot != null) {
      (entry as WatchEntry & { lot?: number | null }).lot = Number(r.lot);
    }
    if (Boolean(r.hidden)) entry.hidden = true;
    if (Boolean(r.star)) entry.star = true;
    if (Boolean(r.dip_buy)) entry.dip_buy = true;
    // tags: stored as JSON string in DB, parse to array
    if (r.tags) {
      try {
        const parsed = JSON.parse(r.tags);
        if (Array.isArray(parsed) && parsed.length > 0) {
          (entry as WatchEntry & { tags?: string[] }).tags = parsed;
        }
      } catch { /* ignore malformed JSON */ }
    }
    if (r.watch_price != null) {
      (entry as WatchEntry & { watch_price?: number }).watch_price = Number(r.watch_price);
    }
    if (r.watch_price_date != null) {
      (entry as WatchEntry & { watch_price_date?: string }).watch_price_date = r.watch_price_date;
    }
    if (r.pin_order > 0) entry.pin_order = r.pin_order;
    watchlist[r.symbol] = entry;
    if (r.list_type === "holding") holdings[r.symbol] = entry;
    else watching[r.symbol] = entry;
  }

  const settings: Record<string, number> = {};
  for (const r of settingsRows) {
    settings[r.key] = Number(r.value);
  }

  return { watchlist, holdings, watching, settings };
}

function exportMonitorSnapshotFromDb(_db: MonitorDb): string | null {
  // JSON snapshot export removed — DB is the single source of truth
  return null;
}

function readWatchRow(db: MonitorDb, symbol: string): WatchRow | undefined {
  return db
    .prepare(
      `SELECT symbol, name, alias, list_type, cost, shares, lot, hidden, star, dip_buy,
              tags, watch_price, watch_price_date, pin_order
       FROM monitor_watchlist
       WHERE symbol = ?`,
    )
    .get(symbol) as WatchRow | undefined;
}

function parseTagsForAudit(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map((tag) => String(tag)) : [];
  } catch {
    return [];
  }
}

function watchRowToAudit(row: WatchRow | undefined): Record<string, unknown> | null {
  if (!row) return null;
  return {
    symbol: row.symbol,
    name: row.name,
    alias: row.alias,
    list_type: row.list_type,
    cost: row.cost,
    shares: row.shares,
    lot: row.lot,
    hidden: Boolean(row.hidden),
    star: Boolean(row.star),
    dip_buy: Boolean(row.dip_buy),
    tags: parseTagsForAudit(row.tags),
    watch_price: row.watch_price,
    watch_price_date: row.watch_price_date,
    pin_order: row.pin_order,
  };
}

function recordConfigAudit(
  db: MonitorDb,
  args: {
    action: string;
    entity: string;
    key: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
    metadata?: Record<string, unknown>;
  },
) {
  const nowMs = Date.now();
  insertConfigAuditOutbox(
    db,
    buildAuditEventV2({
      eventId: randomUUID(),
      tsMs: nowMs,
      correlationId: randomUUID(),
      source: "api_config",
      actor: makeActor({ type: "user", id: "local-ui" }),
      action: args.action,
      entity: args.entity,
      key: args.key,
      dbName: "config.db",
      before: args.before,
      after: args.after,
      metadata: args.metadata,
    }),
  );
}

function isConfigEmpty(config: MonitorConfig): boolean {
  return (
    Object.keys(config.watchlist || {}).length === 0 &&
    Object.keys(config.settings || {}).length === 0
  );
}

// ── Alerts (alert_rules table in config.db) ──

interface AlertEntry { above?: number; below?: number; }
interface AlertConfig { alerts: Record<string, AlertEntry>; }

function readAlertsFromDb(db: MonitorDb): AlertConfig {
  const rows = db
    .prepare("SELECT symbol, above, below FROM alert_rules")
    .all() as { symbol: string; above: number | null; below: number | null }[];

  const alerts: Record<string, AlertEntry> = {};
  for (const r of rows) {
    const entry: AlertEntry = {};
    if (r.above != null) entry.above = Number(r.above);
    if (r.below != null) entry.below = Number(r.below);
    if (Object.keys(entry).length > 0) {
      alerts[r.symbol] = entry;
    }
  }
  return { alerts };
}

function writeAlertToDb(
  db: MonitorDb,
  symbol: string,
  entry: AlertEntry | null,
) {
  const before = db.prepare("SELECT symbol, above, below, updated_at FROM alert_rules WHERE symbol = ?").get(symbol) as
    | Record<string, unknown>
    | undefined;
  if (!entry || (entry.above == null && entry.below == null)) {
    const result = db.prepare("DELETE FROM alert_rules WHERE symbol = ?").run(symbol);
    if (result.changes > 0) {
      recordDbChangeBestEffort(db, {
        dbName: "config.db",
        table: "alert_rules",
        action: "delete",
        key: symbol,
        source: "api_config",
        actor: makeActor({ type: "user", id: "local-user" }),
        before: before ?? null,
        after: null,
      });
    }
    return;
  }
  const nowTs = Math.floor(Date.now() / 1000);
  db.prepare(
    `INSERT INTO alert_rules(symbol, above, below, updated_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT(symbol) DO UPDATE SET
       above = excluded.above,
       below = excluded.below,
       updated_at = excluded.updated_at`
  ).run(
    symbol,
    entry.above != null ? Number(entry.above) : null,
    entry.below != null ? Number(entry.below) : null,
    nowTs,
  );
  const after = db.prepare("SELECT symbol, above, below, updated_at FROM alert_rules WHERE symbol = ?").get(symbol) as
    | Record<string, unknown>
    | undefined;
  recordDbChangeBestEffort(db, {
    dbName: "config.db",
    table: "alert_rules",
    action: before ? "update" : "create",
    key: symbol,
    source: "api_config",
    actor: makeActor({ type: "user", id: "local-user" }),
    before: before ?? null,
    after: after ?? null,
  });
}

function deleteAlertFromDb(db: MonitorDb, symbol: string) {
  const before = db.prepare("SELECT symbol, above, below, updated_at FROM alert_rules WHERE symbol = ?").get(symbol) as
    | Record<string, unknown>
    | undefined;
  const result = db.prepare("DELETE FROM alert_rules WHERE symbol = ?").run(symbol);
  if (result.changes > 0) {
    recordDbChangeBestEffort(db, {
      dbName: "config.db",
      table: "alert_rules",
      action: "delete",
      key: symbol,
      source: "api_config",
      actor: makeActor({ type: "user", id: "local-user" }),
      before: before ?? null,
      after: null,
    });
  }
}

// ── Helpers ──

function emMarket(code: string): string {
  if (code.toUpperCase().startsWith("HK")) return "116";
  return code.startsWith("6") ? "1" : "0";
}

function rawCode(code: string): string {
  return code.toUpperCase().startsWith("HK")
    ? code.toUpperCase().replace("HK", "")
    : code;
}

async function fetchStockName(code: string): Promise<string> {
  try {
    const secid = `${emMarket(code)}.${rawCode(code)}`;
    const url = `${EM_API}?fltt=2&secids=${secid}&fields=f12,f14&ut=${EM_UT}`;
    const resp = await fetch(url);
    const data = await resp.json();
    if (data?.data?.diff?.[0]?.f14) {
      return String(data.data.diff[0].f14);
    }
  } catch {
    // fallback
  }
  return code;
}

// ── GET /api/config — return config + alerts ──
export async function GET() {
  let db: MonitorDb | null = null;
  try {
    db = openMonitorDb(true);
    ensureMonitorTables(db);
    const config = readConfigFromDb(db, false);
    const alertCfg = readAlertsFromDb(db);
    return NextResponse.json({ ...config, alerts: alertCfg.alerts });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  } finally {
    db?.close();
  }
}

// ── POST /api/config — modify config and/or alerts ──
export async function POST(request: Request) {
  const db = openMonitorDb(false);
  try {
    ensureMonitorTables(db);
    const body = await request.json();
    const { action } = body;

    const withSnapshotWarning = (message: string, warn: string | null): string => {
      if (!warn) return message;
      return `${message} | WARN: snapshot export failed`;
    };

    switch (action) {
      case "add": {
        const { code, data } = body as {
          code: string;
          data?: Partial<WatchEntry> & { above?: number; below?: number };
        };
        if (!code) {
          return NextResponse.json({ success: false, message: "Missing code" }, { status: 400 });
        }

        let name = data?.name || "";
        if (!name) {
          name = await fetchStockName(code);
        }

        const isHolding = data?.type === "holding" || data?.cost != null || data?.shares != null;
        const listType: "holding" | "watching" = isHolding ? "holding" : "watching";
        const cost = data?.cost != null ? Number(data.cost) : null;
        const shares = data?.shares != null ? Number(data.shares) : null;
        const lotRaw = (data as WatchEntry & { lot?: number | null } | undefined)?.lot;
        const lot = lotRaw != null ? Number(lotRaw) : null;
        const hidden = data?.hidden ? 1 : 0;
        const star = data?.star ? 1 : 0;
        const nowTs = Math.floor(Date.now() / 1000);

        // tags + watch_price auto-recording
        const tags = Array.isArray(data?.tags) ? data.tags : [];
        const tagsJson = JSON.stringify(tags);
        const watchPrice = data?.watch_price != null ? Number(data.watch_price) : readMarketPrice(code);
        const watchPriceDate = watchPrice != null ? todayStr() : null;

        const oldRow = readWatchRow(db, code);
        db.transaction(() => {
          // ── 持仓变更记录（add 也可能触发 ON CONFLICT 更新）──
          if (oldRow) {
            const oldShares = oldRow.shares;
            const oldCost = oldRow.cost;
            const newShares = shares;
            const newCost = cost;
            const oldType = oldRow.list_type;
            const newType = listType;
            const sharesOrCostChanged = oldShares !== newShares || oldCost !== newCost;
            const typeChanged = oldType !== newType;
            if (sharesOrCostChanged || typeChanged) {
              const nowIso = new Date().toISOString();
              db.prepare(`
                INSERT OR IGNORE INTO position_change_log
                  (symbol, ts, source, shares_from, shares_to, cost_from, cost_to, type_from, type_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
              `).run(code, nowIso, "import",
                oldShares != null ? oldShares : null,
                newShares != null ? newShares : null,
                oldCost != null ? oldCost : null,
                newCost != null ? newCost : null,
                typeChanged ? oldType : null,
                typeChanged ? newType : null);
            }
          }

          db.prepare(
            `INSERT INTO monitor_watchlist(
              symbol, name, list_type, cost, shares, lot, hidden, star, dip_buy,
              tags, watch_price, watch_price_date, pin_order, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
              name = excluded.name,
              list_type = excluded.list_type,
              cost = excluded.cost,
              shares = excluded.shares,
              lot = excluded.lot,
              hidden = excluded.hidden,
              star = excluded.star,
              dip_buy = excluded.dip_buy,
              tags = excluded.tags,
              watch_price = excluded.watch_price,
              watch_price_date = excluded.watch_price_date,
              pin_order = excluded.pin_order,
              updated_at = excluded.updated_at,
              created_at = monitor_watchlist.created_at`
          ).run(code, name, listType, cost, shares, lot, hidden, star, 0, tagsJson, watchPrice, watchPriceDate, 0, nowTs, nowTs);

          // 告警写到 alert_rules
          if (data?.above != null || data?.below != null) {
            const alertEntry: AlertEntry = {};
            if (data.above != null) alertEntry.above = data.above;
            if (data.below != null) alertEntry.below = data.below;
            writeAlertToDb(db, code, alertEntry);
          }

          recordConfigAudit(db, {
            action: oldRow ? "upsert" : "add",
            entity: "monitor_watchlist",
            key: code,
            before: watchRowToAudit(oldRow),
            after: watchRowToAudit(readWatchRow(db, code)),
            metadata: {
              source_action: "add",
              alert_updated: data?.above != null || data?.below != null,
            },
          });
        })();

        const env = listType === "holding" ? "PROD" : "DEV";
        const extras: string[] = [];
        if (cost != null) extras.push(`cost:${cost.toFixed(2)}`);
        if (shares != null) extras.push(`shares:${shares}`);
        if (data?.above != null) extras.push(`above:${data.above}`);
        if (data?.below != null) extras.push(`below:${data.below}`);
        const extStr = extras.length > 0 ? ` | ${extras.join(" ")}` : "";
        const snapshotWarn = exportMonitorSnapshotFromDb(db);

        return NextResponse.json({
          success: true,
          message: withSnapshotWarning(`Added ${code} (${name}) → ${env}${extStr}`, snapshotWarn),
        });
      }

      case "update": {
        const { code, data } = body as {
          code: string;
          data?: Partial<WatchEntry> & { above?: number; below?: number };
        };
        const existing = code ? readWatchRow(db, code) : undefined;
        if (!code || !existing) {
          return NextResponse.json(
            { success: false, message: `${code || "?"} not in watchlist` },
            { status: 400 }
          );
        }

        let listType: "holding" | "watching" = existing.list_type === "holding" ? "holding" : "watching";
        let cost = existing.cost;
        let shares = existing.shares;
        let hidden = Boolean(existing.hidden);
        let star = Boolean(existing.star);
        let dipBuy = Boolean(existing.dip_buy);
        let lot = existing.lot;
        let tagsJson = existing.tags ?? "[]";
        let watchPrice = existing.watch_price;
        let watchPriceDate = existing.watch_price_date;
        let alias = existing.alias;

        if (data?.type !== undefined) {
          listType = data.type === "holding" ? "holding" : "watching";
        }
        if (data?.cost !== undefined) {
          cost = data.cost == null ? null : Number(data.cost);
        }
        if (data?.shares !== undefined) {
          shares = data.shares == null ? null : Number(data.shares);
        }
        if ((data as WatchEntry & { lot?: number | null } | undefined)?.lot !== undefined) {
          lot = (data as WatchEntry & { lot?: number | null }).lot ?? null;
        }
        if (data?.hidden !== undefined) hidden = Boolean(data.hidden);
        if (data?.star !== undefined) star = Boolean(data.star);
        if (data?.dip_buy !== undefined) dipBuy = Boolean(data.dip_buy);
        if ((data as WatchEntry & { pin_order?: number })?.pin_order !== undefined) {
          // handled separately in pin action
        }
        if (data?.alias !== undefined) alias = data.alias || null;
        if (Array.isArray(data?.tags)) {
          tagsJson = JSON.stringify(data.tags);
        }
        if (data?.watch_price !== undefined) {
          watchPrice = data.watch_price == null ? null : Number(data.watch_price);
          watchPriceDate = watchPrice != null ? todayStr() : null;
        }

        // 自动提升为 holding：仅当用户未显式设置 type 且新增了 cost/shares 时
        if (data?.type === undefined && (data?.cost != null || data?.shares != null)) {
          if ((cost != null || shares != null) && listType !== "holding") {
            listType = "holding";
          }
        }

        const nowTs = Math.floor(Date.now() / 1000);

        db.transaction(() => {
          // ── 持仓变更记录 ──
          const oldShares = existing.shares;
          const oldCost = existing.cost;
          const newShares = shares;
          const newCost = cost;
          const sharesChanged = oldShares !== newShares;
          const costChanged = oldCost !== newCost;
          const oldType = existing.list_type;
          const newType = listType;
          const typeChanged = oldType !== newType;
          if (sharesChanged || costChanged || typeChanged) {
            const nowIso = new Date().toISOString();
            db.prepare(`
              INSERT OR IGNORE INTO position_change_log
                (symbol, ts, source, shares_from, shares_to, cost_from, cost_to, type_from, type_to)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            `).run(code, nowIso, "manual",
              oldShares != null ? oldShares : null,
              newShares != null ? newShares : null,
              oldCost != null ? oldCost : null,
              newCost != null ? newCost : null,
              typeChanged ? oldType : null,
              typeChanged ? newType : null);
          }

          db.prepare(
            `UPDATE monitor_watchlist
             SET list_type = ?, cost = ?, shares = ?, lot = ?, hidden = ?, star = ?, dip_buy = ?,
                 alias = ?, tags = ?, watch_price = ?, watch_price_date = ?, pin_order = ?, updated_at = ?
             WHERE symbol = ?`
          ).run(listType, cost, shares, lot, hidden ? 1 : 0, star ? 1 : 0, dipBuy ? 1 : 0, alias, tagsJson, watchPrice, watchPriceDate, existing.pin_order, nowTs, code);

          // 告警写到 alert_rules
          if (data?.above !== undefined || data?.below !== undefined) {
            const currentAlert = db
              .prepare("SELECT above, below FROM alert_rules WHERE symbol = ?")
              .get(code) as { above: number | null; below: number | null } | undefined;
            const alertEntry: AlertEntry = {};
            if (currentAlert?.above != null) alertEntry.above = currentAlert.above;
            if (currentAlert?.below != null) alertEntry.below = currentAlert.below;

            if (data.above !== undefined) {
              if (data.above === null) delete alertEntry.above;
              else alertEntry.above = data.above;
            }
            if (data.below !== undefined) {
              if (data.below === null) delete alertEntry.below;
              else alertEntry.below = data.below;
            }
            writeAlertToDb(db, code, alertEntry);
          }

          recordConfigAudit(db, {
            action: "update",
            entity: "monitor_watchlist",
            key: code,
            before: watchRowToAudit(existing),
            after: watchRowToAudit(readWatchRow(db, code)),
            metadata: {
              alert_updated: data?.above !== undefined || data?.below !== undefined,
            },
          });
        })();
        const snapshotWarn = exportMonitorSnapshotFromDb(db);

        const changed: string[] = [];
        if (data?.type !== undefined) changed.push(`type:${data.type}`);
        if (data?.cost !== undefined) changed.push(`cost:${data.cost}`);
        if (data?.shares !== undefined) changed.push(`shares:${data.shares}`);
        if (data?.above !== undefined) changed.push(`above:${data.above}`);
        if (data?.below !== undefined) changed.push(`below:${data.below}`);
        if (data?.hidden !== undefined) changed.push(`hidden:${data.hidden}`);
        if (data?.star !== undefined) changed.push(`star:${data.star}`);
        if (data?.dip_buy !== undefined) changed.push(`dip_buy:${data.dip_buy}`);
        if ((data as WatchEntry & { lot?: number | null } | undefined)?.lot !== undefined) {
          changed.push(`lot:${(data as WatchEntry & { lot?: number | null }).lot}`);
        }
        if (Array.isArray(data?.tags)) changed.push(`tags:[${data.tags.join(",")}]`);
        if (data?.watch_price !== undefined) changed.push(`watch_price:${data.watch_price}`);

        return NextResponse.json({
          success: true,
          message: withSnapshotWarning(
            `Updated ${code} (${existing.name}) → ${changed.join(" ")}`,
            snapshotWarn
          ),
        });
      }

      case "pin": {
        const { code, value } = body as { code?: string; value?: boolean };
        if (!code) {
          return NextResponse.json({ success: false, message: "Missing code" }, { status: 400 });
        }
        const row = readWatchRow(db, code);
        if (!row) {
          return NextResponse.json({ success: false, message: `${code} not in watchlist` }, { status: 400 });
        }
        const nowTs = Math.floor(Date.now() / 1000);
        if (value) {
          const maxRow = db.prepare("SELECT MAX(pin_order) as m FROM monitor_watchlist").get() as { m: number };
          const nextOrder = (maxRow?.m ?? 0) + 1;
          db.transaction(() => {
            db.prepare("UPDATE monitor_watchlist SET pin_order = ?, updated_at = ? WHERE symbol = ?").run(nextOrder, nowTs, code);
            recordConfigAudit(db, {
              action: "pin",
              entity: "monitor_watchlist",
              key: code,
              before: watchRowToAudit(row),
              after: watchRowToAudit(readWatchRow(db, code)),
            });
          })();
          return NextResponse.json({ success: true, message: `Pinned ${code} (order=${nextOrder})` });
        } else {
          db.transaction(() => {
            db.prepare("UPDATE monitor_watchlist SET pin_order = 0, updated_at = ? WHERE symbol = ?").run(nowTs, code);
            recordConfigAudit(db, {
              action: "unpin",
              entity: "monitor_watchlist",
              key: code,
              before: watchRowToAudit(row),
              after: watchRowToAudit(readWatchRow(db, code)),
            });
          })();
          return NextResponse.json({ success: true, message: `Unpinned ${code}` });
        }
      }

      case "remove": {
        const { code, codes } = body as { code?: string; codes?: string[] };
        const toRemove = codes || (code ? [code] : []);
        if (toRemove.length === 0) {
          return NextResponse.json({ success: false, message: "Missing code(s)" }, { status: 400 });
        }

        const removedRows: WatchRow[] = [];
        const notFound: string[] = [];
        db.transaction(() => {
          for (const c of toRemove) {
            const row = readWatchRow(db, c);
            if (row) {
              removedRows.push(row);
              db.prepare("DELETE FROM monitor_watchlist WHERE symbol = ?").run(c);
              deleteAlertFromDb(db, c); // 同步清理告警
              recordConfigAudit(db, {
                action: "remove",
                entity: "monitor_watchlist",
                key: c,
                before: watchRowToAudit(row),
                after: null,
                metadata: { alert_deleted: true },
              });
            } else {
              notFound.push(c);
            }
          }
        })();

        const removed: string[] = removedRows.map((row) => `${row.symbol} (${row.name})`);

        const snapshotWarn = exportMonitorSnapshotFromDb(db);
        let msg = removed.length > 0 ? `Removed ${removed.join(", ")}` : "";
        if (notFound.length > 0) {
          msg += (msg ? "; " : "") + `Not found: ${notFound.join(", ")}`;
        }
        msg = withSnapshotWarning(msg, snapshotWarn);

        return NextResponse.json({ success: removed.length > 0, message: msg });
      }

      case "settings": {
        const { settings } = body as { settings: Record<string, number> };
        if (!settings) {
          return NextResponse.json({ success: false, message: "Missing settings" }, { status: 400 });
        }

        const ALLOWED: Record<string, [number, number]> = {
          poll_interval: [5, 300],
          big_move_pct: [0.5, 20],
          cooldown_minutes: [1, 120],
          l1_trigger_pct: [1, 20],
          l1_delta_pct: [1, 20],
          l1_cooldown_min: [0, 60],
          l2_trigger_pct: [1, 20],
          l2_delta_pct: [1, 20],
          l2_cooldown_min: [1, 120],
          l3_cooldown_min: [1, 120],
          available_balance_hkd: [0, 1e9],
          available_balance_rmb: [0, 1e9],
        };

        const rejected: string[] = [];
        const applied: string[] = [];
        const beforeSettings: Record<string, number | null> = {};
        const afterSettings: Record<string, number> = {};
        for (const [key, val] of Object.entries(settings)) {
          const range = ALLOWED[key];
          if (!range) { rejected.push(`${key} (unknown)`); continue; }
          const n = Number(val);
          if (isNaN(n) || n < range[0] || n > range[1]) {
            rejected.push(`${key}=${val} (must be ${range[0]}-${range[1]})`);
            continue;
          }
          applied.push(`${key}=${n}`);
          const current = db
            .prepare("SELECT value FROM monitor_settings WHERE key = ?")
            .get(key) as { value: number } | undefined;
          beforeSettings[key] = current ? Number(current.value) : null;
          afterSettings[key] = n;
        }
        if (Object.keys(afterSettings).length > 0) {
          db.transaction(() => {
            const nowTs = Math.floor(Date.now() / 1000);
            for (const [key, n] of Object.entries(afterSettings)) {
              db.prepare(
                `INSERT INTO monitor_settings(key, value, updated_at)
                 VALUES (?, ?, ?)
                 ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at`
              ).run(key, n, nowTs);
            }
            recordConfigAudit(db, {
              action: "update",
              entity: "monitor_settings",
              key: "settings",
              before: beforeSettings,
              after: afterSettings,
              metadata: { rejected },
            });
          })();
        }
        const snapshotWarn = exportMonitorSnapshotFromDb(db);

        const msg = applied.length > 0 ? `Updated: ${applied.join(" ")}` : "No changes";
        const warn = rejected.length > 0 ? ` | Rejected: ${rejected.join(", ")}` : "";
        return NextResponse.json({
          success: applied.length > 0,
          message: withSnapshotWarning(msg + warn, snapshotWarn),
        });
      }

      case "tag-add": {
        const { codes, tag } = body as { codes?: string[]; tag?: string };
        if (!tag || !Array.isArray(codes) || codes.length === 0) {
          return NextResponse.json(
            { success: false, message: "Missing codes (array) or tag (string)" },
            { status: 400 }
          );
        }
        const updated: string[] = [];
        db.transaction(() => {
          for (const c of codes) {
            const row = readWatchRow(db, c);
            if (!row) continue;
            let tags: string[] = [];
            try { tags = JSON.parse(row.tags ?? "[]"); } catch { /* ignore */ }
            if (!tags.includes(tag)) {
              tags.push(tag);
              const nowTs = Math.floor(Date.now() / 1000);
              db.prepare("UPDATE monitor_watchlist SET tags = ?, updated_at = ? WHERE symbol = ?")
                .run(JSON.stringify(tags), nowTs, c);
              updated.push(c);
              recordConfigAudit(db, {
                action: "tag_add",
                entity: "monitor_watchlist",
                key: c,
                before: watchRowToAudit(row),
                after: watchRowToAudit(readWatchRow(db, c)),
                metadata: { tag },
              });
            }
          }
          // Auto-create tag_meta row if tag is new
          db.prepare("INSERT OR IGNORE INTO tag_meta(tag) VALUES (?)").run(tag);
        })();
        const snapshotWarnTagAdd = exportMonitorSnapshotFromDb(db);
        return NextResponse.json({
          success: updated.length > 0,
          message: withSnapshotWarning(
            updated.length > 0
              ? `Added tag "${tag}" to ${updated.join(", ")}`
              : `Tag "${tag}" already present on all specified codes`,
            snapshotWarnTagAdd
          ),
        });
      }

      case "tag-remove": {
        const { codes, tag } = body as { codes?: string[]; tag?: string };
        if (!tag || !Array.isArray(codes) || codes.length === 0) {
          return NextResponse.json(
            { success: false, message: "Missing codes (array) or tag (string)" },
            { status: 400 }
          );
        }
        const removed: string[] = [];
        db.transaction(() => {
          for (const c of codes) {
            const row = readWatchRow(db, c);
            if (!row) continue;
            let tags: string[] = [];
            try { tags = JSON.parse(row.tags ?? "[]"); } catch { /* ignore */ }
            const idx = tags.indexOf(tag);
            if (idx >= 0) {
              tags.splice(idx, 1);
              const nowTs = Math.floor(Date.now() / 1000);
              db.prepare("UPDATE monitor_watchlist SET tags = ?, updated_at = ? WHERE symbol = ?")
                .run(JSON.stringify(tags), nowTs, c);
              removed.push(c);
              recordConfigAudit(db, {
                action: "tag_remove",
                entity: "monitor_watchlist",
                key: c,
                before: watchRowToAudit(row),
                after: watchRowToAudit(readWatchRow(db, c)),
                metadata: { tag },
              });
            }
          }
        })();
        const snapshotWarnTagRm = exportMonitorSnapshotFromDb(db);
        return NextResponse.json({
          success: removed.length > 0,
          message: withSnapshotWarning(
            removed.length > 0
              ? `Removed tag "${tag}" from ${removed.join(", ")}`
              : `Tag "${tag}" not found on any specified codes`,
            snapshotWarnTagRm
          ),
        });
      }

      case "reset-watch-price": {
        const { code } = body as { code?: string };
        if (!code) {
          return NextResponse.json({ success: false, message: "Missing code" }, { status: 400 });
        }
        const row = readWatchRow(db, code);
        if (!row) {
          return NextResponse.json(
            { success: false, message: `${code} not in watchlist` },
            { status: 400 }
          );
        }
        const price = readMarketPrice(code);
        if (price == null) {
          return NextResponse.json(
            { success: false, message: `No market price available for ${code}` },
            { status: 400 }
          );
        }
        const nowTs = Math.floor(Date.now() / 1000);
        db.transaction(() => {
          db.prepare(
            "UPDATE monitor_watchlist SET watch_price = ?, watch_price_date = ?, updated_at = ? WHERE symbol = ?"
          ).run(price, todayStr(), nowTs, code);
          recordConfigAudit(db, {
            action: "reset_watch_price",
            entity: "monitor_watchlist",
            key: code,
            before: watchRowToAudit(row),
            after: watchRowToAudit(readWatchRow(db, code)),
          });
        })();
        const snapshotWarnWp = exportMonitorSnapshotFromDb(db);
        return NextResponse.json({
          success: true,
          message: withSnapshotWarning(
            `Reset watch_price for ${code} (${row.name}) → ${price}`,
            snapshotWarnWp
          ),
        });
      }

      default:
        return NextResponse.json(
          { success: false, message: `Unknown action: ${action}` },
          { status: 400 }
        );
    }
  } catch (e) {
    return NextResponse.json({ success: false, message: String(e) }, { status: 500 });
  } finally {
    db.close();
  }
}
