import { NextResponse } from "next/server";
import { openTradingDb } from "../../lib/db";

const LIMIT_DEFAULT = 50;
const LIMIT_MAX = 200;

/** Matches trading.db CHECK on ai_investment_events.notify_status */
const NOTIFY_STATUSES = new Set(["pending", "sent", "suppressed", "web_only", "failed"]);

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

function sanitizeNotifyStatus(raw: string | null): string | null | "__invalid__" {
  if (raw === null || raw === "") {
    return null;
  }
  if (!NOTIFY_STATUSES.has(raw)) {
    return "__invalid__";
  }
  return raw;
}

/** Stock codes only — no SQL special chars. */
function sanitizeSymbol(raw: string | null): string | null | "__invalid__" {
  if (raw === null || raw === "") {
    return null;
  }
  if (raw.length > 24 || !/^[A-Za-z0-9.]+$/.test(raw)) {
    return "__invalid__";
  }
  return raw;
}

/**
 * GET /api/ai-investment-events
 *
 * Read-only list of normalized AI events (Phase 2+). Query:
 * - limit (default 50, max 200)
 * - event_type (alphanumeric + _ -)
 * - notify_status (pending | sent | suppressed | web_only | failed)
 * - symbol (alphanumeric + dot, e.g. HK00700, 600519)
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

  const ns = sanitizeNotifyStatus(url.searchParams.get("notify_status"));
  if (ns === "__invalid__") {
    return NextResponse.json({ error: "invalid notify_status" }, { status: 400 });
  }

  const sym = sanitizeSymbol(url.searchParams.get("symbol"));
  if (sym === "__invalid__") {
    return NextResponse.json({ error: "invalid symbol" }, { status: 400 });
  }

  const db = openTradingDb(true);
  try {
    const cols = `id, event_date, symbol, name, source, event_type, severity, delivery_scope,
                  verdict, confidence, dedupe_key, title, summary, reasons_json, metrics_json,
                  recommendation_json, notify_status, created_at, updated_at`;

    const clauses: string[] = [];
    const params: unknown[] = [];
    if (et) {
      clauses.push("event_type = ?");
      params.push(et);
    }
    if (ns) {
      clauses.push("notify_status = ?");
      params.push(ns);
    }
    if (sym) {
      clauses.push("symbol = ?");
      params.push(sym);
    }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

    const stmt = db.prepare(
      `SELECT ${cols}
       FROM ai_investment_events
       ${where}
       ORDER BY created_at DESC
       LIMIT ?`
    );
    const rows = stmt.all(...params, lim);

    return NextResponse.json({ data: rows });
  } catch (error) {
    console.error("ai-investment-events:", error);
    return NextResponse.json({ error: "read_failed" }, { status: 500 });
  } finally {
    db.close();
  }
}
