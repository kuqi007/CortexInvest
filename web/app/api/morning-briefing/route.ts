import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

const MORNING_PATH = join(process.cwd(), "..", "src", "data", "morning_briefing.json");

export async function GET() {
  try {
    const raw = readFileSync(MORNING_PATH, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ generated_at: null, us_markets: {}, asia_markets: {}, global_news: [] });
  }
}
