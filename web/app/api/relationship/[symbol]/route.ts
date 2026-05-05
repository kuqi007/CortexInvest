import { NextRequest, NextResponse } from "next/server";
import { spawn } from "child_process";
import path from "path";

const ROOT = path.resolve(process.cwd(), "..");
const CONFIG_DB = path.join(ROOT, "src", "data", "config.db");

/**
 * 异步执行 Python 分析，spawn 不经过 shell，彻底消除注入风险
 */
interface RelationshipData {
  symbol: string;
  relationships: Array<{
    type: string;
    name: string;
    code: string;
    current: number | null;
    change_pct: number | null;
    triggered: boolean;
    influence: string;
    weight: number;
    threshold: number;
    error?: string | null;
  }>;
  triggered_count: number;
  summary: string;
  error?: string;
}

function analyzeSymbolAsync(symbol: string): Promise<{
  data?: RelationshipData;
  error?: string;
  stderr?: string;
}> {
  return new Promise((resolve) => {
    const pythonScript = `
import json, sys, os
sys.path.insert(0, os.environ['AI_ROOT'])
os.chdir(os.environ['AI_ROOT'])
from src.tools.relationship_engine import RelationshipEngine
engine = RelationshipEngine(os.environ['AI_CONFIG_DB'])
result = engine.analyze_symbol(os.environ['AI_SYMBOL'])
print(json.dumps(result, ensure_ascii=False))
`;

    const child = spawn("python3", ["-c", pythonScript], {
      cwd: ROOT,
      env: {
        ...process.env,
        AI_ROOT: ROOT,
        AI_CONFIG_DB: CONFIG_DB,
        AI_SYMBOL: symbol,
      },
    });

    let stdout = "";
    let stderr = "";

    child.stdout.setEncoding("utf-8");
    child.stderr.setEncoding("utf-8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });

    const timeoutId = setTimeout(() => {
      child.kill("SIGTERM");
      const forceKillId = setTimeout(() => child.kill("SIGKILL"), 5000);
      child.on("exit", () => clearTimeout(forceKillId));
      resolve({
        error: "Python execution timeout (15s)",
        stderr: stderr.trim(),
      });
    }, 15000);

    child.on("error", (err) => {
      clearTimeout(timeoutId);
      resolve({
        error: `Failed to spawn Python: ${err.message}`,
        stderr: stderr.trim(),
      });
    });

    child.on("close", (code) => {
      clearTimeout(timeoutId);
      if (code !== 0) {
        resolve({
          error: `Python exited with code ${code}`,
          stderr: stderr.trim(),
        });
        return;
      }
      try {
        const data = JSON.parse(stdout.trim());
        resolve({ data });
      } catch (e) {
        resolve({
          error: `Failed to parse Python output: ${e instanceof Error ? e.message : String(e)}`,
          stderr: stderr.trim(),
        });
      }
    });
  });
}

/**
 * GET /api/relationship/:symbol
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> }
) {
  const { symbol } = await params;
  if (!symbol || symbol.length > 20) {
    return NextResponse.json({ error: "invalid symbol" }, { status: 400 });
  }

  const safeSymbol = symbol.replace(/[^a-zA-Z0-9._-]/g, "");
  if (safeSymbol !== symbol) {
    return NextResponse.json(
      { error: "symbol contains invalid chars" },
      { status: 400 }
    );
  }

  const { data, error, stderr } = await analyzeSymbolAsync(safeSymbol);

  if (error) {
    return NextResponse.json(
      { error, detail: stderr },
      { status: 500 }
    );
  }

  if (data?.error) {
    return NextResponse.json(data, { status: 500 });
  }

  return NextResponse.json(data, {
    headers: {
      "Cache-Control": "public, s-maxage=300, stale-while-revalidate=60",
    },
  });
}
