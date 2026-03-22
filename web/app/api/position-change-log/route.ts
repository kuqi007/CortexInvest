import { NextResponse } from "next/server";
import { join } from "path";
import Database from "better-sqlite3";

const SIM_DB_PATH = join(process.cwd(), "..", "src", "data", "sim_trading.db");

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol");
  const limit = Math.min(Number(searchParams.get("limit")) || 20, 100);

  if (!symbol) {
    return NextResponse.json({ error: "symbol is required" }, { status: 400 });
  }

  const db = new Database(SIM_DB_PATH, { readonly: true });
  try {
    const rows = db.prepare(`
      SELECT ts, source, shares_from, shares_to, cost_from, cost_to
      FROM position_change_log
      WHERE symbol = ?
      ORDER BY ts DESC
      LIMIT ?
    `).all(symbol, limit) as {
      ts: string;
      source: string;
      shares_from: number | null;
      shares_to: number | null;
      cost_from: number | null;
      cost_to: number | null;
    }[];

    return NextResponse.json({ results: rows });
  } finally {
    db.close();
  }
}
