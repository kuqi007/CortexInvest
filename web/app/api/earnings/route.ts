import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { openTradingDb } from "../../lib/db";
import { buildAuditEventV2, insertTradingAuditOutbox, makeActor } from "../../lib/audit";

// GET /api/earnings — 获取财报日历列表
export async function GET(request: NextRequest) {
  const db = openTradingDb(true);
  try {
    const searchParams = request.nextUrl.searchParams;
    const days = parseInt(searchParams.get("days") || "7", 10);

    const now = new Date();
    const cutoff = new Date(now.getTime() + days * 24 * 60 * 60 * 1000);
    const today = now.toISOString().slice(0, 10);
    const cutoffStr = cutoff.toISOString().slice(0, 10);

    const rows = db
      .prepare(
        `SELECT symbol, report_date, name, source, created_at, updated_at
         FROM earnings_calendar
         WHERE report_date >= ? AND report_date <= ?
         ORDER BY report_date, symbol`
      )
      .all(today, cutoffStr) as Array<{
      symbol: string;
      report_date: string;
      name: string | null;
      source: string | null;
      created_at: string;
      updated_at: string;
    }>;

    const upcoming = rows.map((r) => ({
      symbol: r.symbol,
      report_date: r.report_date,
      name: r.name,
      source: r.source,
      created_at: r.created_at,
      updated_at: r.updated_at,
    }));

    // Get last_updated from the most recent record
    const lastUpdatedRow = db
      .prepare("SELECT MAX(updated_at) as last_updated FROM earnings_calendar")
      .get() as { last_updated: string | null } | undefined;

    return NextResponse.json({
      success: true,
      count: upcoming.length,
      upcoming,
      last_updated: lastUpdatedRow?.last_updated || null,
    });
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  } finally {
    db.close();
  }
}

// POST /api/earnings — 手动触发一次检查（仅CLI触发，API不做实际操作）
export async function POST(request: NextRequest) {
  const db = openTradingDb();
  try {
    const body = await request.json();
    const { action } = body;

    if (action === "trigger_check") {
      const before = db
        .prepare("SELECT value FROM portfolio_config WHERE key = ?")
        .get("earnings_check_trigger") as { value: string } | undefined;
      const triggerAt = new Date().toISOString();
      // Store trigger flag in portfolio_config table
      db.transaction(() => {
        db.prepare(
          `INSERT INTO portfolio_config (key, value, updated_at)
           VALUES ('earnings_check_trigger', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`
        ).run(triggerAt);
        insertTradingAuditOutbox(
          db,
          buildAuditEventV2({
            eventId: randomUUID(),
            tsMs: Date.now(),
            correlationId: randomUUID(),
            source: "api_earnings",
            actor: makeActor({ type: "user", id: "local-ui" }),
            action: "trigger_check",
            entity: "portfolio_config",
            key: "earnings_check_trigger",
            dbName: "trading.db",
            before: before ? { value: before.value } : null,
            after: { value: triggerAt },
          }),
        );
      })();

      return NextResponse.json({ success: true, message: "检查已触发" });
    }

    return NextResponse.json(
      { success: false, error: "Unknown action" },
      { status: 400 }
    );
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  } finally {
    db.close();
  }
}
