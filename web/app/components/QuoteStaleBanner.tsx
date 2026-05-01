"use client";

import type { CSSProperties } from "react";
import { D } from "../theme";
import type { TradingStatus } from "../lib/trading-hours";
import { computeQuoteTrust } from "../lib/data-trust";

function bannerStyle(color: string): CSSProperties {
  return {
    border: `1px solid ${color}`,
    borderLeft: `3px solid ${color}`,
    background: color === D.orange ? "rgba(255, 184, 108, 0.06)" : "rgba(98, 114, 164, 0.06)",
    color: color === D.orange ? D.fg : D.comment,
    padding: "9px 16px",
    borderRadius: 6,
    marginBottom: 12,
    fontSize: 12,
    fontWeight: 500,
    display: "flex",
    alignItems: "center",
    gap: 8,
  };
}

export function QuoteStaleBanner({
  pollMs,
  ts,
  fetchError,
  tradingStatus,
}: {
  pollMs: number;
  ts: number;
  fetchError: string | null;
  tradingStatus: TradingStatus | null;
}) {
  if (fetchError || ts <= 0) return null;

  const ageMs = Date.now() - ts;
  if (ageMs <= pollMs * 6) return null;

  const quoteTrust = computeQuoteTrust({ ts, pollMs, fetchError, tradingStatus });

  if (tradingStatus?.markets?.cn) {
    return (
      <div style={bannerStyle(D.orange)}>
        <span style={{ color: D.orange, fontSize: 14 }}>⚠</span>
        <span>
          A股交易时段 · 行情快照已{" "}
          <span style={{ color: D.orange, fontWeight: 700 }}>{Math.round(ageMs / 60000)}</span>{" "}
          分钟未刷新 — 优先检查 poller / trading.db 写入
        </span>
      </div>
    );
  }

  if (tradingStatus?.markets?.hk) {
    return (
      <div style={bannerStyle(D.comment)}>
        <span>● A股已收盘，港股仍在交易 — 快照 age 偏大通常与 A 股侧停更有关；仍以 SQLite last 时间为准</span>
      </div>
    );
  }

  return (
    <div style={bannerStyle(D.comment)}>
      <span>
        ●{" "}
        {quoteTrust === "CLOSED_STALE"
          ? "休市 · 快照偏旧（过久未见 DB 写入），确认 poller 是否停用"
          : "休市 · CLOSED — last 行情时间见顶栏"}
      </span>
    </div>
  );
}
