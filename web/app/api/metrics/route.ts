import { NextResponse } from "next/server";
import { openConfigDb, openTradingDb } from "../../lib/db";

const EMPTY = { services: [], ts: 0, settings: {} };

function dataRuntimeHintFromEnv(): string | undefined {
  const dir = process.env.AI_INVESTOR_DATA_DIR ?? "";
  if (!dir) return undefined;
  const n = dir.replace(/\\/g, "/");
  if (n.includes(".e2e-runs") || n.includes(".e2e-data")) return "E2E";
  return undefined;
}

type DbWatchRow = {
  symbol: string;
  name: string;
  alias: string | null;
  list_type: "holding" | "watching";
  cost: number | null;
  shares: number | null;
  hidden: number;
  star: number;
  dip_buy: number;
  tags: string | null;
  watch_price: number | null;
  watch_price_date: string | null;
  pin_order: number;
};

type PriceSnapshot = {
  ts: number;
  code: string;
  name: string;
  price: number;
  volume: number;
  amount: number;
  change_pct: number;
  chg_amt: number;
  amp: number;
  turnover: number;
  vol_ratio: number;
  high: number;
  low: number;
  open: number;
  prev_close: number;
  amo1: number;
  amo2: number;
  main_net_inflow: number | null;
  main_net_inflow_pct: number | null;
};

type MarketTurnover = {
  ts: number;
  sh: number;
  sz: number;
  total: number;
  sh_index: number;
  sz_index: number;
  sh_pct: number;
  sz_pct: number;
  verdict: string;
  chi_next: number;
  chi_next_pct: number;
  kc50: number;
  kc50_pct: number;
  hk_index: number;
  hk_index_pct: number;
  hk_tech: number;
  hk_tech_pct: number;
  hk_turnover: number;
  amo1: number;
  amo2: number;
};

function readMonitorConfigFromDb(): {
  watchlist: Record<string, Record<string, unknown>>;
  settings: Record<string, number>;
  empty: boolean;
} {
  const db = openConfigDb(true);
  try {
    const watchRows = db
      .prepare(
        `SELECT symbol, name, alias, list_type, cost, shares, hidden, star, dip_buy, tags, watch_price, watch_price_date, pin_order
         FROM monitor_watchlist`,
      )
      .all() as DbWatchRow[];
    const settingsRows = db
      .prepare("SELECT key, value FROM monitor_settings")
      .all() as { key: string; value: number }[];

    const watchlist: Record<string, Record<string, unknown>> = {};
    for (const r of watchRows) {
      let parsedTags: string[] | undefined;
      if (r.tags) {
        try { parsedTags = JSON.parse(r.tags); } catch { /* ignore malformed */ }
      }
      watchlist[r.symbol] = {
        name: r.name,
        alias: r.alias ?? undefined,
        type: r.list_type,
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
    }

    const settings: Record<string, number> = {};
    for (const r of settingsRows) {
      settings[r.key] = Number(r.value);
    }

    return {
      watchlist,
      settings,
      empty: watchRows.length === 0 && settingsRows.length === 0,
    };
  } finally {
    db.close();
  }
}

function readLatestPriceSnapshots(): { snapshots: PriceSnapshot[]; ts: number } {
  const db = openTradingDb(true);
  try {
    const rows = db
      .prepare(
        `SELECT ts, code, name, price, volume, amount, change_pct, chg_amt, amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2, main_net_inflow, main_net_inflow_pct
         FROM price_snapshots
         WHERE (code, ts) IN (
           SELECT code, MAX(ts) FROM price_snapshots GROUP BY code
         )`,
      )
      .all() as PriceSnapshot[];

    let ts = 0;
    for (const r of rows) {
      if (r.ts > ts) ts = r.ts;
    }
    return { snapshots: rows, ts };
  } finally {
    db.close();
  }
}

function readLatestMarketTurnover(): MarketTurnover | null {
  const db = openTradingDb(true);
  try {
    const row = db
      .prepare(
        `SELECT ts, sh, sz, total, sh_index, sz_index, sh_pct, sz_pct, verdict,
                chi_next, chi_next_pct, kc50, kc50_pct, hk_index, hk_index_pct,
                hk_tech, hk_tech_pct, hk_turnover, amo1, amo2
         FROM market_turnover
         ORDER BY ts DESC
         LIMIT 1`,
      )
      .get() as MarketTurnover | undefined;
    return row ?? null;
  } finally {
    db.close();
  }
}

function readAlertRules(): Record<string, { above: number | null; below: number | null }> {
  const db = openConfigDb(true);
  try {
    const rows = db
      .prepare("SELECT symbol, above, below FROM alert_rules")
      .all() as { symbol: string; above: number | null; below: number | null }[];

    const alerts: Record<string, { above: number | null; below: number | null }> = {};
    for (const r of rows) {
      alerts[r.symbol] = { above: r.above ?? null, below: r.below ?? null };
    }
    return alerts;
  } finally {
    db.close();
  }
}

/**
 * GET /api/metrics
 *
 * 三源合并（全部来自 SQLite DB）:
 * - price_snapshots (trading.db): 纯行情数据
 * - monitor_watchlist (config.db): 持仓配置 (type/cost/shares/hidden)
 * - alert_rules (config.db): 告警规则 (above/below)
 */
export async function GET() {
  try {
    // 1. 读 watchlist + settings
    const { watchlist, settings } = readMonitorConfigFromDb();

    // 2. 读最新行情快照
    const { snapshots, ts } = readLatestPriceSnapshots();

    // 3. 读 alert rules
    const alerts = readAlertRules();

    // 4. 读 market turnover
    const turnover = readLatestMarketTurnover();

    // 5. 构建 services 数组
    const services: Record<string, unknown>[] = [];
    const existingIds = new Set<string>();

    for (const s of snapshots) {
      const id = s.code;
      const entry = watchlist[id];
      if (!entry) continue; // 不在 watchlist 里的股票不返回
      existingIds.add(id);
      const alert = alerts[id];

      const price = Number(s.price) || 0;
      const cost = entry.cost != null ? Number(entry.cost) : null;
      const shares = entry.shares != null ? Number(entry.shares) : null;
      const type = (entry.type as string) || "watching";
      const isHolding = type === "holding";

      let pnl: number | null = null;
      if (isHolding && cost != null && cost !== 0 && price > 0) {
        pnl = Math.round(((price - cost) / Math.abs(cost)) * 10000) / 100;
      }

      services.push({
        id,
        name: (entry.name as string) || s.name || id,
        price,
        change: s.change_pct ?? 0,
        chgAmt: s.chg_amt ?? 0,
        vol: s.volume ?? 0,
        amount: s.amount ?? 0,
        amp: s.amp ?? 0,
        turnover: s.turnover ?? 0,
        volRatio: s.vol_ratio ?? 0,
        high: s.high ?? 0,
        low: s.low ?? 0,
        open: s.open ?? 0,
        prevClose: s.prev_close ?? 0,
        amo1: s.amo1 ?? 0,
        amo2: s.amo2 ?? 0,
        mainNetInflow: s.main_net_inflow ?? null,
        mainNetInflowPct: s.main_net_inflow_pct ?? null,
        type,
        cost,
        shares,
        pnl,
        above: alert?.above ?? null,
        below: alert?.below ?? null,
        hidden: Boolean(entry.hidden),
        star: Boolean(entry.star),
        ...(entry.dip_buy ? { dip_buy: true } : {}),
        ...(entry.alias ? { alias: entry.alias } : {}),
        ...(entry.tags ? { tags: entry.tags } : {}),
        ...(entry.watch_price != null ? { watch_price: Number(entry.watch_price) } : {}),
        ...(entry.watch_price_date ? { watch_price_date: entry.watch_price_date } : {}),
        ...((entry as Record<string, unknown>).pin_order != null ? { pin_order: (entry as Record<string, unknown>).pin_order } : {}),
      });
    }

    // 补充 watchlist 中有但 price_snapshots 里没有的条目（新加持仓/自选立即显示）
    for (const [id, entry] of Object.entries(watchlist)) {
      if (existingIds.has(id)) continue;
      const alert = alerts[id];
      const type = (entry.type as string) || "watching";
      const cost = entry.cost != null ? Number(entry.cost) : null;
      const shares = entry.shares != null ? Number(entry.shares) : null;

      services.push({
        id,
        name: (entry.name as string) || id,
        price: 0,
        change: 0,
        chgAmt: 0,
        vol: 0,
        amount: 0,
        amp: 0,
        turnover: 0,
        volRatio: 0,
        high: 0,
        low: 0,
        open: 0,
        prevClose: 0,
        amo1: 0,
        amo2: 0,
        mainNetInflow: null,
        mainNetInflowPct: null,
        type,
        cost,
        shares,
        pnl: null,
        above: alert?.above ?? null,
        below: alert?.below ?? null,
        hidden: Boolean(entry.hidden),
        star: Boolean(entry.star),
        ...(entry.dip_buy ? { dip_buy: true } : {}),
        ...(entry.alias ? { alias: entry.alias } : {}),
        ...(entry.tags ? { tags: entry.tags } : {}),
        ...(entry.watch_price != null ? { watch_price: Number(entry.watch_price) } : {}),
        ...(entry.watch_price_date ? { watch_price_date: entry.watch_price_date } : {}),
        ...((entry as Record<string, unknown>).pin_order != null ? { pin_order: (entry as Record<string, unknown>).pin_order } : {}),
      });
    }

    // 6. 构建 marketTurnover
    const marketTurnover = turnover
      ? {
          sh: turnover.sh ?? 0,
          sz: turnover.sz ?? 0,
          total: turnover.total ?? 0,
          shIndex: turnover.sh_index ?? 0,
          szIndex: turnover.sz_index ?? 0,
          shPct: turnover.sh_pct ?? 0,
          szPct: turnover.sz_pct ?? 0,
          verdict: turnover.verdict ?? "",
          chiNext: turnover.chi_next ?? 0,
          chiNextPct: turnover.chi_next_pct ?? 0,
          kc50: turnover.kc50 ?? 0,
          kc50Pct: turnover.kc50_pct ?? 0,
          hkIndex: turnover.hk_index ?? 0,
          hkIndexPct: turnover.hk_index_pct ?? 0,
          hkTech: turnover.hk_tech ?? 0,
          hkTechPct: turnover.hk_tech_pct ?? 0,
          hkTurnover: turnover.hk_turnover ?? 0,
          amo1: turnover.amo1 ?? 0,
          amo2: turnover.amo2 ?? 0,
        }
      : undefined;

    // 7. 读 alert events + indicator_cache
    let alertEvents: unknown[] = [];
    let indicatorMap: Record<string, unknown> = {};
    try {
      const now = new Date();
      const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

      const db = openTradingDb(true);
      try {
        alertEvents = db
          .prepare(
            "SELECT ts, time, symbol, kind, level, message, display, change_pct " +
            "FROM alert_events WHERE date = ? ORDER BY ts",
          )
          .all(today);

        try {
          const indRows = db.prepare(
            "SELECT symbol, data_json FROM indicator_cache WHERE date = ?"
          ).all(today) as { symbol: string; data_json: string }[];
          for (const r of indRows) {
            try { indicatorMap[r.symbol] = JSON.parse(r.data_json); } catch { /* skip */ }
          }
        } catch { /* indicator_cache table may not exist yet */ }
      } finally {
        db.close();
      }
    } catch { /* alert_events read failure is non-fatal */ }

    // 8. 附加 indicators 到 services
    if (Object.keys(indicatorMap).length > 0) {
      for (const svc of services) {
        const ind = indicatorMap[svc.id as string];
        if (ind) svc.indicators = ind;
      }
    }

    // ── 按市场计算持仓市值、总资产、仓位比例 ──
    const availHkd = Number(settings.available_balance_hkd) || 0;
    const availRmb = Number(settings.available_balance_rmb) || 0;

    let posValHkd = 0;
    let posValRmb = 0;
    for (const svc of services as Array<Record<string, any>>) {
      if (svc.type === "holding" && svc.shares != null && svc.price > 0) {
        const mktVal = svc.price * svc.shares;
        if (svc.id.startsWith("HK")) posValHkd += mktVal;
        else posValRmb += mktVal;
      }
    }

    const totalAssetsHkd = availHkd + posValHkd;
    const totalAssetsRmb = availRmb + posValRmb;

    for (const svc of services as Array<Record<string, any>>) {
      if (svc.type !== "holding" || svc.shares == null || svc.price <= 0) {
        (svc as Record<string, unknown>).position_pct = null;
        continue;
      }
      const mktVal = svc.price * svc.shares;
      const total = svc.id.startsWith("HK") ? totalAssetsHkd : totalAssetsRmb;
      (svc as Record<string, unknown>).position_pct = total > 0
        ? Math.round((mktVal / total) * 10000) / 10000
        : 0;
    }

    const response: Record<string, unknown> = {
      services,
      ts,
      settings,
      alertEvents,
      portfolio: {
        hkd: {
          available_balance: availHkd,
          position_value: posValHkd,
          total_assets: totalAssetsHkd,
        },
        rmb: {
          available_balance: availRmb,
          position_value: posValRmb,
          total_assets: totalAssetsRmb,
        },
      },
    };

    if (marketTurnover) {
      response.marketTurnover = marketTurnover;
    }

    const hint = dataRuntimeHintFromEnv();
    if (hint) {
      response.dataRuntimeHint = hint;
    }

    return NextResponse.json(response);
  } catch (e) {
    return NextResponse.json({ ...EMPTY, error: String(e) });
  }
}
