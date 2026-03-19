import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

const SUMMARY_PATH = join(process.cwd(), "..", "src", "data", "daily_summary.json");
const MORNING_PATH = join(process.cwd(), "..", "src", "data", "morning_briefing.json");

/**
 * GET /api/summary
 *
 * Returns unified daily summary:
 * - If daily_summary.json has a report (post-market): returns summary with morning briefing as background
 * - Otherwise (pre-market): returns morning briefing data only
 *
 * Frontend uses presence of "report" field to decide which card to show.
 */
export async function GET() {
  try {
    const raw = readFileSync(SUMMARY_PATH, "utf-8");
    const summary = JSON.parse(raw);
    // Attach morning briefing as background (used in LLM prompt, also available for UI)
    try {
      const morningRaw = readFileSync(MORNING_PATH, "utf-8");
      const morning = JSON.parse(morningRaw);
      summary.morning = morning;
    } catch {
      // Morning briefing may not exist yet
      summary.morning = null;
    }
    return NextResponse.json({ data: summary });
  } catch {
    // No daily_summary yet — return morning briefing if available
    try {
      const morningRaw = readFileSync(MORNING_PATH, "utf-8");
      const morning = JSON.parse(morningRaw);
      return NextResponse.json({ data: { morning } });
    } catch {
      return NextResponse.json({ data: null });
    }
  }
}
