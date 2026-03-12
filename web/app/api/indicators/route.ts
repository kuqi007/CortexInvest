import { NextRequest, NextResponse } from "next/server";
import { execSync } from "child_process";
import path from "path";

const ROOT = path.resolve(process.cwd(), "..");

/**
 * GET /api/indicators?symbol=603929&live_price=128.5
 *
 * Computes daily technical indicators for an A-share stock via Python.
 * Optional live_price: appends a live bar for intraday indicator estimation.
 * Returns RSI, MACD, MA, volume ratio, signal flags.
 */
export async function GET(req: NextRequest) {
  const symbol = req.nextUrl.searchParams.get("symbol");
  if (!symbol) {
    return NextResponse.json({ error: "symbol required" }, { status: 400 });
  }

  // Skip unsupported markets
  if (symbol.startsWith("KR")) {
    return NextResponse.json({ error: "KR not supported" }, { status: 400 });
  }

  const livePrice = req.nextUrl.searchParams.get("live_price") || "";

  try {
    const livePriceArg = livePrice && Number(livePrice) > 0 ? Number(livePrice) : 0;
    const script = `
import json, sys
sys.path.insert(0, '${ROOT}')
from src.tools.indicator_alert_engine import fetch_kline_akshare, compute_indicators
kline = fetch_kline_akshare('${symbol}', days=60)
if kline is None:
    print(json.dumps({"error": "kline fetch failed"}))
else:
    ind = compute_indicators(kline, live_price=${livePriceArg} or None)
    if ind is None:
        print(json.dumps({"error": "insufficient data"}))
    else:
        print(json.dumps(ind))
`;
    const result = execSync(`poetry run python -c "${script.replace(/"/g, '\\"')}"`, {
      cwd: ROOT,
      timeout: 20000,
      encoding: "utf-8",
    }).trim();

    const data = JSON.parse(result);
    if (data.error) {
      return NextResponse.json(data, { status: 500 });
    }
    return NextResponse.json(data);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg.slice(0, 200) }, { status: 500 });
  }
}
