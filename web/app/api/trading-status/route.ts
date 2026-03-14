import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * GET /api/trading-status
 *
 * Returns current market trading status based on real trading calendar from Futu API.
 * Response: { trading: boolean, markets: { cn: boolean, hk: boolean }, time: string }
 */
export async function GET() {
  try {
    const { spawn } = await import("child_process");

    const pythonCode = `
import sys
import os

# Suppress logging before importing anything
import logging
logging.disable(logging.CRITICAL)

# Disable Futu logging
if hasattr(os, 'environ'):
    os.environ['FUTU_NO_LOG'] = '1'

import json
from datetime import datetime
from src.tools.trading_calendar import is_trading_day

now = datetime.now()
cn = is_trading_day("CN")
hk = is_trading_day("HK")

# Check if within trading hours
mins = now.hour * 60 + now.minute
cn_trading_hours = (mins >= 9 * 60 + 15 and mins <= 11 * 60 + 30) or (mins >= 13 * 60 and mins <= 15 * 60)
hk_trading_hours = (mins >= 9 * 60 + 15 and mins <= 12 * 60) or (mins >= 13 * 60 and mins <= 16 * 60)

result = {
    "trading": (cn and cn_trading_hours) or (hk and hk_trading_hours),
    "markets": {
        "cn": cn and cn_trading_hours,
        "hk": hk and hk_trading_hours
    },
    "time": now.strftime("%Y-%m-%d %H:%M:%S")
}
print(json.dumps(result))
`;

    return new Promise<NextResponse>((resolve) => {
      const py = spawn(
        "poetry",
        ["run", "python", "-c", pythonCode],
        {
          cwd: process.cwd(),
          stdio: ["pipe", "pipe", "ignore"],
        }
      );

      let output = "";
      py.stdout.on("data", (data) => {
        output += data.toString();
      });

      py.on("close", (code) => {
        if (code === 0 && output) {
          try {
            const result = JSON.parse(output.trim());
            resolve(NextResponse.json({ success: true, data: result }));
          } catch {
            resolve(NextResponse.json({ success: false, error: "Parse error" }));
          }
        } else {
          resolve(NextResponse.json({ success: false, error: "Failed to get trading status" }));
        }
      });

      py.on("error", () => {
        resolve(NextResponse.json({ success: false, error: "Process error" }));
      });
    });
  } catch (e) {
    return NextResponse.json({ success: false, error: String(e) });
  }
}
