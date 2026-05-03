"use client";

import type { CSSProperties, ReactNode } from "react";
import { D } from "../theme";
import type { TradingStatus } from "../lib/trading-hours";
import {
  computeQuoteTrust,
  formatAgeZh,
  quoteTrustLabel,
} from "../lib/data-trust";

function getTimeUntilNextOpen(): string {
  const now = new Date();
  const currentHour = now.getHours();
  const currentMinute = now.getMinutes();

  let target = new Date(now);
  if (currentHour < 9 || (currentHour === 9 && currentMinute < 30)) {
    target.setHours(9, 30, 0, 0);
  } else {
    target.setDate(target.getDate() + 1);
    target.setHours(9, 30, 0, 0);
  }

  const diffMs = target.getTime() - now.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffMinutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));

  return `${diffHours}h${diffMinutes.toString().padStart(2, "0")}m`;
}

function pillStyle(fg: string, bg: string, border: string): CSSProperties {
  return {
    fontSize: 11,
    fontWeight: 700,
    color: fg,
    background: bg,
    border: `1px solid ${border}`,
    padding: "1px 8px",
    borderRadius: 20,
    whiteSpace: "nowrap",
  };
}

export function DataTrustBar({
  pollMs,
  ts,
  tick,
  fetchError,
  tradingStatus,
  tradingLoading,
  dataRuntimeHint,
  pollHint,
}: {
  pollMs: number;
  ts: number;
  tick: number;
  fetchError: string | null;
  tradingStatus: TradingStatus | null;
  tradingLoading?: boolean;
  dataRuntimeHint?: string | null;
  pollHint?: ReactNode;
}) {
  const trust = computeQuoteTrust({
    ts,
    pollMs,
    fetchError,
    tradingStatus,
  });
  const meta = quoteTrustLabel(trust);
  const ageMs = ts > 0 ? Date.now() - ts : 0;
  const lastClock =
    ts > 0
      ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })
      : "--:--:--";

  const snapshotPill =
    trust === "LIVE"
      ? pillStyle(D.green, "rgba(80, 250, 123, 0.10)", D.green)
      : trust === "DELAYED"
        ? pillStyle(D.orange, "rgba(255, 184, 108, 0.12)", D.orange)
        : trust === "CLOSED" || trust === "CLOSED_STALE"
          ? pillStyle(D.comment, "rgba(98, 114, 164, 0.12)", D.comment)
          : trust === "OFFLINE"
            ? pillStyle(D.red, "rgba(255, 85, 85, 0.12)", D.red)
            : pillStyle(D.red, "rgba(255, 85, 85, 0.12)", D.red);

  const alertTrust =
    trust === "OFFLINE"
      ? { text: "不可用", fg: D.red, bg: "rgba(255, 85, 85, 0.12)", border: D.red }
      : trust === "STALE"
        ? { text: "可能滞后", fg: D.orange, bg: "rgba(255, 184, 108, 0.12)", border: D.orange }
        : trust === "DELAYED"
          ? { text: "略滞后", fg: D.orange, bg: "rgba(255, 184, 108, 0.10)", border: D.orange }
          : trust === "CLOSED" || trust === "CLOSED_STALE"
            ? {
                text: "休市静止",
                fg: D.comment,
                bg: "rgba(98, 114, 164, 0.08)",
                border: D.comment,
              }
            : { text: "正常", fg: D.green, bg: "rgba(80, 250, 123, 0.10)", border: D.green };

  return (
    <div
      style={{
        color: D.comment,
        marginBottom: 6,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "6px 10px",
        rowGap: 6,
      }}
    >
      {pollHint}
      <div style={{ flex: 1, minWidth: 120 }} />
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 8,
          justifyContent: "flex-end",
          fontSize: 11,
        }}
      >
        <span
          style={{
            color: tradingStatus?.markets?.cn ? D.green : D.comment,
            fontWeight: 700,
          }}
        >
          ● A股{" "}
          {tradingLoading ? "…" : tradingStatus?.markets?.cn ? "交易中" : "休市"}
        </span>
        <span
          style={{
            color: tradingStatus?.markets?.hk ? D.green : D.comment,
            fontWeight: 700,
          }}
        >
          ● 港股{" "}
          {tradingLoading ? "…" : tradingStatus?.markets?.hk ? "交易中" : "休市"}
        </span>
        <span style={{ color: D.comment }}>|</span>
        <span title={meta.hint} style={snapshotPill}>
          快照 {meta.text}
        </span>
        <span
          title="告警列表与快照同源（ SQLite ）；滞后表示界面上的告警事件可能未反映最新一轮写入"
          style={pillStyle(alertTrust.fg, alertTrust.bg, alertTrust.border)}
        >
          告警视图 {alertTrust.text}
        </span>
        <span style={{ color: D.comment }}>|</span>
        <span style={{ color: D.fg, fontFamily: "JetBrains Mono, monospace" }}>
          last {lastClock}
        </span>
        {trust === "CLOSED" || trust === "CLOSED_STALE" ? (
          <span style={{ color: D.comment }}>
            收盘快照 · 距下次开盘还有 {getTimeUntilNextOpen()}
          </span>
        ) : (
          <>
            <span style={{ color: ts > 0 ? D.comment : D.red }}>
              age {ts > 0 ? formatAgeZh(ageMs) : "—"}
            </span>
            <span style={{ color: D.comment }}>数据源</span>
            <span style={{ color: D.comment }}>刷新间隔 {Math.round(pollMs / 1000)}s</span>
            <span style={{ color: D.comment }} title="metrics fetch counter (E2E waits for refresh #1+)">
              已刷新 {tick} 次
            </span>
          </>
        )}
        {dataRuntimeHint ? (
          <span
            title="服务端 AI_INVESTOR_DATA_DIR 指向隔离目录"
            style={{
              color: D.purple,
              fontWeight: 700,
              border: `1px solid ${D.purple}`,
              borderRadius: 4,
              padding: "0 6px",
              fontSize: 10,
            }}
          >
            {dataRuntimeHint}
          </span>
        ) : null}
      </div>
    </div>
  );
}
