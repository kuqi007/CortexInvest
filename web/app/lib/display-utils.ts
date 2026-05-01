import { D } from "../theme";

export type DashboardPageKey = "holdings" | "starred" | "watching";

export function chgColor(v: number): string {
  return v > 0 ? D.red : v < 0 ? D.green : D.comment;
}

export function pad(s: string, n: number, right = false): string {
  return right ? s.padStart(n) : s.padEnd(n);
}

export function fmtAmt(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e8) return sign + (abs / 1e8).toFixed(1) + "亿";
  if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0) + "万";
  return n.toFixed(0);
}

export function fmtMoney(
  n: number,
  options: { sign?: "always" | "negativeOnly" } = {},
): string {
  const signMode = options.sign ?? "always";
  const sign = n >= 0 && signMode === "always" ? "+" : "";
  const abs = Math.abs(n);
  if (abs >= 1e4) return `${sign}${(n / 1e4).toFixed(1)}万`;
  return `${sign}${Math.round(n).toLocaleString("en-US")}`;
}

export function buildPollHint(pollMs: number, pageKey: DashboardPageKey): string {
  const seconds = pollMs / 1000;
  return `每 ${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} 秒拉取 /api/metrics · ${pageKey}`;
}
