import { NextResponse } from "next/server";
import { openTradingDb } from "../../lib/db";

type MacroRow = {
  ts: number;
  date: string;
  northbound_net: number;
  northbound_total: number;
  gold_price: number;
  gold_change_pct: number;
  copper_price: number;
  copper_change_pct: number;
  vix: number;
  ty10y: number;
  usd_cnh: number;
  usd_cnh_change_pct: number;
  tungsten_price: number;
  tungsten_change_pct: number;
};

function tableExists(db: any, tableName: string): boolean {
  const row = db
    .prepare(
      "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
    )
    .get(tableName);
  return !!row;
}

/**
 * GET /api/macro
 *
 * 返回最新宏观数据 + 最近 24h 历史（约 48 条，每 30min 一条）
 */
export async function GET() {
  try {
    const db = openTradingDb(true);
    try {
      // 检查表是否存在
      if (!tableExists(db, "macro_indicators")) {
        return NextResponse.json(
          { data: null, error: "macro table not initialized" },
          { status: 200 },
        );
      }

      // 查询最新一条数据
      const latest = db
        .prepare(
          `SELECT ts, date, northbound_net, northbound_total, gold_price,
                  gold_change_pct, copper_price, copper_change_pct, vix,
                  ty10y, usd_cnh, usd_cnh_change_pct,
                  tungsten_price, tungsten_change_pct
           FROM macro_indicators
           ORDER BY ts DESC
           LIMIT 1`,
        )
        .get() as MacroRow | undefined;

      // 数据为空
      if (!latest) {
        return NextResponse.json({ data: null, history: [] });
      }

      // 查询最近 48 条历史（约 24h，每 30min 一条）
      const oneDayAgo = Date.now() - 24 * 60 * 60 * 1000;
      const history = db
        .prepare(
          `SELECT ts, date, northbound_net, northbound_total, gold_price,
                  gold_change_pct, copper_price, copper_change_pct, vix,
                  ty10y, usd_cnh, usd_cnh_change_pct,
                  tungsten_price, tungsten_change_pct
           FROM macro_indicators
           WHERE ts > ?
           ORDER BY ts DESC
           LIMIT 48`,
        )
        .all(oneDayAgo) as MacroRow[];

      const data = {
        ts: latest.ts,
        date: latest.date,
        northbound_net: latest.northbound_net,
        northbound_total: latest.northbound_total,
        gold_price: latest.gold_price,
        gold_change_pct: latest.gold_change_pct,
        copper_price: latest.copper_price,
        copper_change_pct: latest.copper_change_pct,
        vix: latest.vix,
        ty10y: latest.ty10y,
        usd_cnh: latest.usd_cnh,
        usd_cnh_change_pct: latest.usd_cnh_change_pct,
        tungsten_price: latest.tungsten_price,
        tungsten_change_pct: latest.tungsten_change_pct,
      };

      return NextResponse.json({ data, history });
    } finally {
      db.close();
    }
  } catch (e) {
    return NextResponse.json(
      { data: null, error: String(e) },
      { status: 500 },
    );
  }
}
