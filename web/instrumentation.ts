const GLOBAL_KEY = "__ai_investor_audit_flush_interval__";
let lastFlushTs = 0;
const MIN_FLUSH_INTERVAL_MS = 5_000;

function triggerAuditFlush(): void {
  const now = Date.now();
  if (now - lastFlushTs < MIN_FLUSH_INTERVAL_MS) return;
  lastFlushTs = now;

  const cp = eval("require")("child_process");
  const projectRoot = eval("require")("path").resolve(process.cwd(), "..");

  const env: NodeJS.ProcessEnv = { ...process.env };
  if (process.env.AI_INVESTOR_DATA_DIR) {
    env.AI_INVESTOR_DATA_DIR = process.env.AI_INVESTOR_DATA_DIR;
  }

  const proc = cp.spawn("uv", ["run", "python", "src/tools/audit_flush.py"], {
    cwd: projectRoot,
    env,
    detached: true,
    stdio: ["ignore", "ignore", "pipe"],
  });

  let stderr = "";
  proc.stderr?.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

  const killTimer = setTimeout(() => {
    try { proc.kill("SIGTERM"); } catch {}
    setTimeout(() => { try { proc.kill("SIGKILL"); } catch {} }, 5_000).unref();
  }, 30_000);
  killTimer.unref();

  proc.on("exit", (code: number | null) => {
    clearTimeout(killTimer);
    if (code !== 0 && code !== null) {
      console.warn(`[auditFlush] exit ${code}:`, stderr.slice(0, 300));
    }
  });
  proc.on("error", () => { clearTimeout(killTimer); });
  proc.unref();
}

export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const existing = (globalThis as Record<string, unknown>)[GLOBAL_KEY];
  if (existing) return;

  triggerAuditFlush();
  const handle = setInterval(triggerAuditFlush, 60_000);
  (globalThis as Record<string, unknown>)[GLOBAL_KEY] = handle;
}
