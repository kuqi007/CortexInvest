import { NextResponse } from "next/server";
import Database from "better-sqlite3";
import { join } from "path";

const SIM_DB_PATH = join(process.cwd(), "..", "src", "data", "sim_trading.db");

/**
 * GET /api/close-events
 *
 * Returns today's market close notification from alert_events.
 * Filtered by kind = 'market_close' for the current date.
 */
export async function GET() {
  try {
    const db = new Database(SIM_DB_PATH, { readonly: true });
    
    const today = new Date().toISOString().slice(0, 10);
    
    const rows = db
      .prepare(
        `SELECT ts, time, symbol, kind, level, message, display, change_pct 
         FROM alert_events 
         WHERE date = ? AND kind = 'market_close'
         ORDER BY ts DESC
         LIMIT 10`
      )
      .all(today);
    
    db.close();

    if (rows.length === 0) {
      return NextResponse.json({ data: [] });
    }

    const events = (rows as Array<{
      ts: number;
      time: string;
      symbol: string;
      kind: string;
      level: number;
      message: string;
      display: string;
      change_pct: number;
    }>).map(event => ({
      time: event.time,
      message: event.message,
      display: event.display || event.message,
    }));

    return NextResponse.json({ data: events });
  } catch (error) {
    console.error("Failed to read close events:", error);
    return NextResponse.json({ data: null }, { status: 500 });
  }
}
