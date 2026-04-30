import { NextResponse } from "next/server";
import { openTradingDb } from "../../lib/db";

/**
 * GET /api/summary
 *
 * Returns unified daily summary:
 * - If daily_summaries has a report for today (post-market): returns summary with morning briefing as background
 * - Otherwise (pre-market): returns morning briefing data only
 *
 * Frontend uses presence of "report" field to decide which card to show.
 */
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
      const summary: Record<string, any> = {
        date: summaryRow.date,
        market: summaryRow.market,
        stats: JSON.parse(summaryRow.stats_json),
        perStock: summaryRow.per_stock_json ? JSON.parse(summaryRow.per_stock_json) : [],
        report: summaryRow.report_md || "",
        generatedAt: summaryRow.generated_at,
      };

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
