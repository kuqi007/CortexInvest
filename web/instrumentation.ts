import { triggerAuditFlush } from "./app/lib/auditFlush";

const GLOBAL_KEY = "__ai_investor_audit_flush_interval__";

export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  // Prevent duplicate intervals in this process (HMR, multiple calls)
  const existing = (globalThis as Record<string, unknown>)[GLOBAL_KEY];
  if (existing) return;

  triggerAuditFlush();

  const intervalMs = 60_000;
  const handle = setInterval(() => {
    triggerAuditFlush();
  }, intervalMs);

  (globalThis as Record<string, unknown>)[GLOBAL_KEY] = handle;
}
