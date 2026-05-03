import { spawn } from "child_process";
import { dirname, resolve } from "path";

let lastFlushTs = 0;
const MIN_FLUSH_INTERVAL_MS = 5_000;

function resolveDataDir(): string | undefined {
  if (process.env.AI_INVESTOR_DATA_DIR) {
    return process.env.AI_INVESTOR_DATA_DIR;
  }
  const configDbPath = process.env.AI_INVESTOR_CONFIG_DB_PATH;
  if (configDbPath) {
    return dirname(configDbPath);
  }
  return undefined;
}

function resolveProjectRoot(): string {
  return resolve(process.cwd(), "..");
}

export function triggerAuditFlush(): void {
  const now = Date.now();
  if (now - lastFlushTs < MIN_FLUSH_INTERVAL_MS) {
    return;
  }
  lastFlushTs = now;

  const dataDir = resolveDataDir();
  const projectRoot = resolveProjectRoot();

  const env: NodeJS.ProcessEnv = { ...process.env };
  if (dataDir) {
    env.AI_INVESTOR_DATA_DIR = dataDir;
  }

  const proc = spawn("uv", ["run", "python", "src/tools/audit_flush.py"], {
    cwd: projectRoot,
    env,
    detached: true,
    stdio: ["ignore", "ignore", "pipe"],
  });

  let stderr = "";
  proc.stderr?.on("data", (chunk: Buffer) => {
    stderr += chunk.toString();
  });

  const KILL_TIMEOUT_MS = 30_000;
  const killTimer = setTimeout(() => {
    console.warn("[auditFlush] kill timeout exceeded, sending SIGTERM");
    try {
      proc.kill("SIGTERM");
    } catch {}
    setTimeout(() => {
      try {
        proc.kill("SIGKILL");
      } catch {}
    }, 5_000).unref();
  }, KILL_TIMEOUT_MS);
  killTimer.unref();

  proc.on("exit", (code) => {
    clearTimeout(killTimer);
    if (code !== 0 && code !== null) {
      console.warn(
        `[auditFlush] exit code ${code}, stderr:`,
        stderr.slice(0, 500),
      );
    }
  });

  proc.on("error", (err) => {
    clearTimeout(killTimer);
    console.warn("[auditFlush] spawn failed:", err.message);
  });

  proc.unref();
}
