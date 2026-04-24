import { NextResponse } from "next/server";
import { openTradingDb } from "../../lib/db";

/**
 * GET /api/close-events
 *
 * Returns today's market close notification from alert_events.
 * Falls back to the most recent trading day's market_close event
 * if today's is not yet available (e.g. daemon hasn't run).
 */
export async function GET() {
  const db = openTradingDb(true);
  try {
    const today = new Date().toISOString().slice(0, 10);

    // 1. Try today's market_close event first
    let rows = db
      .prepare(
        `SELECT date, ts, time, symbol, kind, level, message, display, change_pct
         FROM alert_events
         WHERE date = ? AND kind = 'market_close'
         ORDER BY ts DESC
         LIMIT 1`
      )
      .all(today);

    // 2. Fallback: most recent market_close event before today
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
        display: event.message,  // market_close: message contains the actual briefing
      },
    });
  } catch (error) {
    console.error("Failed to read close events:", error);
    return NextResponse.json({ data: null }, { status: 500 });
  } finally {
    db.close();
  }
}
