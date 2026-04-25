import { NextRequest, NextResponse } from "next/server";
import { writeFile, readFile, mkdir } from "fs/promises";
import { existsSync } from "fs";
import path from "path";

const DATA_DIR = path.join(process.cwd(), "..", "src", "data");
const CACHE_FILE = path.join(DATA_DIR, "earnings_calendar_cache.json");
const HISTORY_FILE = path.join(DATA_DIR, "earnings_history.json");

// GET /api/earnings — 获取财报日历列表
export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const days = parseInt(searchParams.get("days") || "7", 10);

  try {
    // 读取缓存的财报日历
    let cache: any = {};
    if (existsSync(CACHE_FILE)) {
      const content = await readFile(CACHE_FILE, "utf-8");
      cache = JSON.parse(content);
    }

    const now = new Date();
    const cutoff = new Date(now.getTime() + days * 24 * 60 * 60 * 1000);
    const today = now.toISOString().slice(0, 10);
    const cutoffStr = cutoff.toISOString().slice(0, 10);

    const upcoming = (cache.upcoming || []).filter((e: any) => {
      const rd = (e.report_date || e.公告时间 || "")[:10];
      return rd >= today && rd <= cutoffStr;
    });

    return NextResponse.json({
      success: true,
      count: upcoming.length,
      upcoming,
      last_updated: cache.last_updated,
    });
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}

// POST /api/earnings — 手动触发一次检查（仅CLI触发，API不做实际操作）
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { action } = body;

    if (action === "trigger_check") {
      // 通知父进程执行检查（通过写标记文件）
      const flagFile = path.join(DATA_DIR, ".earnings_check_trigger");
      await writeFile(flagFile, new Date().toISOString());
      return NextResponse.json({ success: true, message: "检查已触发" });
    }

    return NextResponse.json(
      { success: false, error: "Unknown action" },
      { status: 400 }
    );
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}
