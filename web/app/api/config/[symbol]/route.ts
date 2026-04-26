import { NextResponse } from "next/server";
import Database from "better-sqlite3";
import { openConfigDb } from "../../../lib/db";

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

function readWatchRow(db: Database.Database, symbol: string): WatchRow | undefined {
  return db
    .prepare(
      `SELECT symbol, name, alias, list_type, cost, shares, lot, hidden, star, dip_buy,
              tags, watch_price, watch_price_date, pin_order
       FROM monitor_watchlist
       WHERE symbol = ?`,
    )
    .get(symbol) as WatchRow | undefined;
}

function rowToEntry(r: WatchRow) {
  const entry: Record<string, unknown> = { name: r.name };
  if (r.alias) entry.alias = r.alias;
  if (r.list_type === "holding") entry.type = "holding";
  if (r.cost != null) entry.cost = Number(r.cost);
  if (r.shares != null) entry.shares = Number(r.shares);
  if (r.lot != null) entry.lot = Number(r.lot);
  if (Boolean(r.hidden)) entry.hidden = true;
  if (Boolean(r.star)) entry.star = true;
  if (Boolean(r.dip_buy)) entry.dip_buy = true;
  if (r.tags) {
    try {
      const parsed = JSON.parse(r.tags);
      if (Array.isArray(parsed) && parsed.length > 0) entry.tags = parsed;
    } catch { /* ignore */ }
  }
  if (r.watch_price != null) entry.watch_price = Number(r.watch_price);
  if (r.watch_price_date) entry.watch_price_date = r.watch_price_date;
  if (r.pin_order > 0) entry.pin_order = r.pin_order;
  return entry;
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  if (!symbol) {
    return NextResponse.json({ found: false }, { status: 400 });
  }

  let db: Database.Database | null = null;
  try {
    db = openConfigDb(true);
    const row = readWatchRow(db, symbol);
    if (!row) {
      return NextResponse.json({ found: false });
    }
    const entry = rowToEntry(row);

    // Also check alerts from alert_rules table
    let alerts: { above?: number; below?: number } | undefined;
    try {
      const alertRow = db
        .prepare("SELECT above, below FROM alert_rules WHERE symbol = ?")
        .get(symbol) as { above: number | null; below: number | null } | undefined;
      if (alertRow) {
        alerts = {};
        if (alertRow.above != null) alerts.above = Number(alertRow.above);
        if (alertRow.below != null) alerts.below = Number(alertRow.below);
      }
    } catch { /* no alerts */ }

    return NextResponse.json({ found: true, ...entry, alerts });
  } catch (e) {
    return NextResponse.json({ found: false, error: String(e) }, { status: 500 });
  } finally {
    db?.close();
  }
}
