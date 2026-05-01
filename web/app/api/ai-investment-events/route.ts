import { NextResponse } from "next/server";
import { openTradingDb } from "../../lib/db";

const LIMIT_DEFAULT = 50;
const LIMIT_MAX = 200;

function parseLimit(raw: string | null): number | NextResponse {
  if (raw === null || raw === "") {
    return LIMIT_DEFAULT;
  }
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n) || n < 1 || n > LIMIT_MAX) {
    return NextResponse.json({ error: "limit must be an integer 1-200" }, { status: 400 });
  }
  return n;
}

/** Allow safe subset for SQL filter; reject odd input with sentinel. */
function sanitizeEventType(raw: string | null): string | null | "__invalid__" {
  if (raw === null || raw === "") {
    return null;
  }
  if (raw.length > 64 || !/^[a-zA-Z0-9_-]+$/.test(raw)) {
    return "__invalid__";
  }
  return raw;
}

/**
 * GET /api/ai-investment-events
 *
 * Read-only list of normalized AI events (Phase 2+). Query: limit (default 50, max 200),
 * optional event_type (alphanumeric + _ -).
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const lim = parseLimit(url.searchParams.get("limit"));
  if (lim instanceof NextResponse) {
    return lim;
  }

  const et = sanitizeEventType(url.searchParams.get("event_type"));
  if (et === "__invalid__") {
    return NextResponse.json({ error: "invalid event_type" }, { status: 400 });
  }

  const db = openTradingDb(true);
  try {
    const cols = `id, event_date, symbol, name, source, event_type, severity, delivery_scope,
                  verdict, confidence, dedupe_key, title, summary, reasons_json, metrics_json,
                  notify_status, created_at, updated_at`;

    const rows = et
      ? db
          .prepare(
            `SELECT ${cols}
             FROM ai_investment_events
             WHERE event_type = ?
             ORDER BY created_at DESC
             LIMIT ?`
          )
          .all(et, lim)
      : db
          .prepare(
            `SELECT ${cols}
             FROM ai_investment_events
             ORDER BY created_at DESC
             LIMIT ?`
          )
          .all(lim);

    return NextResponse.json({ data: rows });
  } catch (error) {
    console.error("ai-investment-events:", error);
    return NextResponse.json({ error: "read_failed" }, { status: 500 });
  } finally {
    db.close();
  }
}
