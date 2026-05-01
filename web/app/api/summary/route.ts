import { NextResponse } from "next/server";
import { openTradingDb } from "../../lib/db";

/**
 * GET /api/summary
 *
 * Returns unified daily summary:
 * - If daily_summaries has a row for today (post-market): returns summary with morning briefing as background
 * - Else if an older row has non-empty report_md: returns that report as a fallback (`summaryFallback: true`)
 * - Otherwise (pre-market): returns morning briefing data only
 *
 * Frontend uses presence of "report" field to decide which card to show.
 */
function parseSummaryRow(row: {
  date: string;
  market: string;
  stats_json: string;
  per_stock_json: string | null;
  report_md: string | null;
  generated_at: number | null;
}): Record<string, unknown> {
  return {
    date: row.date,
    market: row.market,
    stats: JSON.parse(row.stats_json),
    perStock: row.per_stock_json ? JSON.parse(row.per_stock_json) : [],
    report: row.report_md || "",
    generatedAt: row.generated_at,
  };
}

export async function GET() {
  const db = openTradingDb(true);
  try {
    const today = new Date().toISOString().slice(0, 10);

    // Try daily summary first
    const summaryRow = db
      .prepare("SELECT date, market, stats_json, per_stock_json, report_md, generated_at FROM daily_summaries WHERE date = ?")
      .get(today) as
      | { date: string; market: string; stats_json: string; per_stock_json: string | null; report_md: string | null; generated_at: number | null }
      | undefined;

    if (summaryRow) {
      const summary = parseSummaryRow(summaryRow) as Record<string, unknown>;

      // Attach morning briefing as background
      const morningRow = db
        .prepare("SELECT date, generated_at, content_json FROM morning_briefings WHERE date = ?")
        .get(today) as
        | { date: string; generated_at: string; content_json: string }
        | undefined;

      if (morningRow) {
        summary.morning = JSON.parse(morningRow.content_json);
      } else {
        summary.morning = null;
      }

      return NextResponse.json({ data: summary });
    }

    // Missed today's generator window — surface the latest prior Markdown report if present
    const fallbackRow = db
      .prepare(
        `SELECT date, market, stats_json, per_stock_json, report_md, generated_at
         FROM daily_summaries
         WHERE date < ?
           AND report_md IS NOT NULL
           AND TRIM(report_md) != ''
         ORDER BY date DESC
         LIMIT 1`,
      )
      .get(today) as
      | { date: string; market: string; stats_json: string; per_stock_json: string | null; report_md: string | null; generated_at: number | null }
      | undefined;

    if (fallbackRow) {
      const summary = {
        ...parseSummaryRow(fallbackRow),
        summaryFallback: true,
      } as Record<string, unknown>;

      const morningRow = db
        .prepare("SELECT date, generated_at, content_json FROM morning_briefings WHERE date = ?")
        .get(today) as
        | { date: string; generated_at: string; content_json: string }
        | undefined;

      if (morningRow) {
        summary.morning = JSON.parse(morningRow.content_json);
      } else {
        summary.morning = null;
      }

      return NextResponse.json({ data: summary });
    }

    // No daily_summary yet — return morning briefing if available
    const morningRow = db
      .prepare("SELECT date, generated_at, content_json FROM morning_briefings WHERE date = ?")
      .get(today) as
      | { date: string; generated_at: string; content_json: string }
      | undefined;

    if (morningRow) {
      return NextResponse.json({
        data: { morning: JSON.parse(morningRow.content_json) },
      });
    }

    return NextResponse.json({ data: null });
  } catch (error: any) {
    return NextResponse.json({ data: null, error: error.message }, { status: 500 });
  } finally {
    db.close();
  }
}
