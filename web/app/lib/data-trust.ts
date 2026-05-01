import type { TradingStatus } from "./trading-hours";

/** Unified quote freshness for skilled-operator readability */
export type QuoteTrust =
  | "OFFLINE"
  | "LIVE"
  | "DELAYED"
  | "STALE"
  | "CLOSED"
  | "CLOSED_STALE";

export function formatAgeZh(ageMs: number): string {
  if (!Number.isFinite(ageMs) || ageMs < 0) return "—";
  const sec = Math.round(ageMs / 1000);
  if (sec < 60) return `${sec}秒`;
  const min = Math.round(ageMs / 60000);
  if (min < 60) return `${min}分`;
  const h = Math.floor(ageMs / 3600000);
  const m = Math.round((ageMs % 3600000) / 60000);
  return `${h}h${m}m`;
}

export function computeQuoteTrust(args: {
  ts: number;
  pollMs: number;
  fetchError: string | null | undefined;
  tradingStatus: TradingStatus | null | undefined;
}): QuoteTrust {
  const { ts, pollMs, fetchError, tradingStatus } = args;
  if (fetchError || ts <= 0) return "OFFLINE";
  const age = Date.now() - ts;
  const cn = tradingStatus?.markets?.cn ?? false;
  const hk = tradingStatus?.markets?.hk ?? false;
  const trading = cn || hk;

  if (!trading) {
    if (age > pollMs * 30) return "CLOSED_STALE";
    return "CLOSED";
  }
  if (age > pollMs * 10) return "STALE";
  if (age > pollMs * 3) return "DELAYED";
  return "LIVE";
}

export function quoteTrustLabel(trust: QuoteTrust): { text: string; hint: string } {
  switch (trust) {
    case "OFFLINE":
      return {
        text: "离线",
        hint: "无法可靠拉取 /api/metrics；界面可能展示上次成功的 SQLite 快照",
      };
    case "LIVE":
      return { text: "实时", hint: "快照新鲜度与轮询窗口一致" };
    case "DELAYED":
      return { text: "延迟", hint: "略旧，尚在容忍区间；留意下一轮写入" };
    case "STALE":
      return { text: "滞后", hint: "交易时段内快照明显过期，优先检查 poller / OpenD" };
    case "CLOSED":
      return { text: "休市", hint: "两市休市，行情静止为常态" };
    case "CLOSED_STALE":
      return {
        text: "休市·快照偏旧",
        hint: "休市但仍过久未见 trading.db 写入，留意后台是否停用",
      };
    default:
      return { text: "—", hint: "" };
  }
}
