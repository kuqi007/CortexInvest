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
      const requestPayloadJson = JSON.stringify({ action: "trigger_check" });
      const idempotencyKey =
        request.headers.get("Idempotency-Key") ||
        request.headers.get("X-Idempotency-Key");
      const correlationId = idempotencyKey || randomUUID();
      const requestId = randomUUID();
      const createdAtMs = Date.now();
      const jobRequest = {
        id: requestId,
        job_type: "earnings_check",
        requested_by: "api",
        request_payload_json: requestPayloadJson,
        status: "pending",
        correlation_id: correlationId,
        created_at_ms: createdAtMs,
        claimed_at_ms: null,
        completed_at_ms: null,
      };
      let inserted = false;
      db.transaction(() => {
        const result = db.prepare(
          `INSERT OR IGNORE INTO job_requests (
             id, job_type, requested_by, request_payload_json, status,
             correlation_id, created_at_ms, claimed_at_ms, completed_at_ms
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
        ).run(
          jobRequest.id,
          jobRequest.job_type,
          jobRequest.requested_by,
          jobRequest.request_payload_json,
          jobRequest.status,
          jobRequest.correlation_id,
          jobRequest.created_at_ms,
          jobRequest.claimed_at_ms,
          jobRequest.completed_at_ms,
        );
        inserted = result.changes === 1;
        if (!inserted) return;

        insertTradingAuditOutbox(
          db,
          buildAuditEventV2({
            eventId: randomUUID(),
            tsMs: createdAtMs,
            correlationId,
            source: "api_earnings",
            actor: makeActor({ type: "user", id: "local-ui" }),
            action: "trigger_check",
            entity: "job_requests",
            key: requestId,
            dbName: "trading.db",
            before: null,
            after: jobRequest,
          }),
        );
      })();
      const row = db
        .prepare(
          "SELECT id, correlation_id FROM job_requests WHERE job_type = ? AND correlation_id = ?"
        )
        .get("earnings_check", correlationId) as
        | { id: string; correlation_id: string }
        | undefined;

      if (!row) {
        throw new Error("Failed to create earnings_check job request");
      }

      return NextResponse.json({
        success: true,
        message: "检查已触发",
        request_id: row.id,
        correlation_id: row.correlation_id,
        existing: !inserted,
      });
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
