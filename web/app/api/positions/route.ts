import { NextResponse } from "next/server";
import { openConfigDb } from "../../lib/db";

/**
 * GET /api/positions
 *
 * Returns current holdings (list_type = "holding") with cost/shares from DB.
 * Used by daily portfolio review to get position data.
 */
export async function GET() {
  try {
    const db = openConfigDb(true);
    try {
      const rows = db
        .prepare(
          `SELECT symbol, name, alias, cost, shares, hidden, star, dip_buy, tags,
                  watch_price, watch_price_date, pin_order
           FROM monitor_watchlist
           WHERE list_type = 'holding'
           ORDER BY pin_order ASC, symbol ASC`
        )
        .all() as {
          symbol: string;
          name: string;
          alias: string | null;
          cost: number | null;
          shares: number | null;
          hidden: number;
          star: number;
          dip_buy: number;
          tags: string | null;
          watch_price: number | null;
          watch_price_date: string | null;
          pin_order: number;
        }[];

      const holdings = rows.map((r) => {
        let parsedTags: string[] | undefined;
        if (r.tags) {
          try { parsedTags = JSON.parse(r.tags); } catch { /* ignore */ }
        }
        return {
          symbol: r.symbol,
          name: r.name,
          alias: r.alias ?? undefined,
          cost: r.cost,
          shares: r.shares,
          hidden: Boolean(r.hidden),
          star: Boolean(r.star),
          dip_buy: Boolean(r.dip_buy),
          tags: parsedTags,
          watch_price: r.watch_price,
          watch_price_date: r.watch_price_date,
          pin_order: r.pin_order,
        };
      });

      return NextResponse.json({ holdings });
    } finally {
      db.close();
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg, holdings: [] }, { status: 500 });
  }
}
