import { NextResponse } from "next/server";
import { openTradingDb } from "../../lib/db";

export const dynamic = "force-dynamic";

/** Poller writes ~30s; warn when newest snapshot is older than this (seconds). */
const PRICE_STALE_SEC = 120;

type FailureRow = {
  id: string;
  job_type: string;
  status: string;
  error: string | null;
  finished_at_ms: number | null;
  correlation_id: string;
};

const FAILURE_ERROR_MAX = 200;
const FAILURE_CORRELATION_MAX = 64;

/** Limit error/correlation length in JSON (paths/stack traces in job_runs.error). */
export function redactFailureForApi(row: FailureRow): FailureRow {
  let err = row.error;
  if (err) {
    err = err.replace(/[\r\n]+/g, " ").trim();
    if (err.length > FAILURE_ERROR_MAX) {
      err = `${err.slice(0, FAILURE_ERROR_MAX)}…`;
    }
  }
  let cid = row.correlation_id;
  if (cid.length > FAILURE_CORRELATION_MAX) {
    cid = `${cid.slice(0, FAILURE_CORRELATION_MAX)}…`;
  }
  return { ...row, error: err, correlation_id: cid };
}

function maxPriceSnapshotTs(db: import("better-sqlite3").Database): number | null {
  try {
    const row = db.prepare("SELECT MAX(ts) AS m FROM price_snapshots").get() as { m: number | null };
    return row?.m ?? null;
  } catch {
    return null;
  }
}

function maxMarketTurnoverTs(db: import("better-sqlite3").Database): number | null {
  try {
    const row = db.prepare("SELECT MAX(ts) AS m FROM market_turnover").get() as { m: number | null };
    return row?.m ?? null;
  } catch {
    return null;
  }
}

/**
 * GET /api/health
 *
 * Read-only operational snapshot: market data freshness (from poller tables) and recent job failures.
 */
export async function GET() {
  const now = Date.now();
  const db = openTradingDb(true);
  try {
    const priceSnapshotsMs = maxPriceSnapshotTs(db);
    const marketTurnoverMs = maxMarketTurnoverTs(db);

    const agePriceSec =
      priceSnapshotsMs !== null ? Math.max(0, Math.floor((now - priceSnapshotsMs) / 1000)) : null;
    const ageMarketSec =
      marketTurnoverMs !== null ? Math.max(0, Math.floor((now - marketTurnoverMs) / 1000)) : null;

    let earningsPending = 0;
    try {
      const row = db
        .prepare(
          `SELECT COUNT(*) AS c FROM job_requests WHERE job_type = 'earnings_check' AND status = 'pending'`,
        )
        .get() as { c: number };
      earningsPending = row?.c ?? 0;
    } catch {
      earningsPending = 0;
    }

    let recentFailures: FailureRow[] = [];
    try {
      const raw = db
        .prepare(
          `SELECT id, job_type, status, error, finished_at_ms, correlation_id
           FROM job_runs
           WHERE status = 'failed' AND job_type = 'earnings_check'
           ORDER BY COALESCE(finished_at_ms, started_at_ms) DESC
           LIMIT 8`,
        )
        .all() as FailureRow[];
      recentFailures = raw.map(redactFailureForApi);
    } catch {
      recentFailures = [];
    }

    const warnings: string[] = [];
    if (agePriceSec !== null && agePriceSec > PRICE_STALE_SEC) {
      warnings.push("price_snapshots_stale");
    }
    if (ageMarketSec !== null && ageMarketSec > PRICE_STALE_SEC) {
      warnings.push("market_turnover_stale");
    }
    if (recentFailures.length > 0) {
      warnings.push("recent_job_failures");
    }

    let overall: "live" | "stale" | "unknown" = "unknown";
    if (agePriceSec !== null) {
      overall = agePriceSec > PRICE_STALE_SEC ? "stale" : "live";
    }

    return NextResponse.json({
      success: true,
      message: "ok",
      timestamp: new Date(now).toISOString(),
      freshness: {
        overall,
        /** Same as `overall`; documents that this is from price_snapshots age only, not jobs/turnover. */
        overallScope: "price_snapshots" as const,
        priceSnapshotsAgeSec: agePriceSec,
        marketTurnoverAgeSec: ageMarketSec,
      },
      data: {
        priceSnapshotsMs: priceSnapshotsMs,
        marketTurnoverMs: marketTurnoverMs,
        jobs: {
          earningsCheckPending: earningsPending,
          recentFailures,
        },
      },
      warnings,
    });
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : "health check failed";
    return NextResponse.json(
      {
        success: false,
        message: msg,
        timestamp: new Date(now).toISOString(),
        freshness: { overall: "unknown" as const, overallScope: "price_snapshots" as const },
        data: null,
        warnings: ["health_query_error"],
      },
      { status: 500 },
    );
  } finally {
    db.close();
  }
}
