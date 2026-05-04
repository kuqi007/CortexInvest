"use client";

import { useState, useEffect } from "react";
import { D } from "../theme";
import { AppTitleBar } from "../components/AppTitleBar";

/* ── Types ─────────────────────────────────────────────── */

type MacroData = {
  ts: number;
  date: string;
  northbound_net: number | null;
  northbound_total: number | null;
  gold_price: number | null;
  gold_change_pct: number | null;
  copper_price: number | null;
  copper_change_pct: number | null;
  vix: number | null;
  ty10y: number | null;
  usd_cnh: number | null;
  usd_cnh_change_pct: number | null;
  tungsten_price: number | null;
  tungsten_change_pct: number | null;
};

type MacroResponse = {
  data: MacroData | null;
  history: MacroData[];
  error?: string | null;
};

/* ── Helpers ───────────────────────────────────────────── */

function fmtNum(n: number | null, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtInt(n: number | null): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function changeColor(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return D.comment;
  if (v > 0) return D.red;   // 涨 = red
  if (v < 0) return D.green; // 跌 = green
  return D.comment;
}

function changeSign(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  if (v > 0) return "+";
  return "";
}

/* ── Card Component ────────────────────────────────────── */

function MacroCard({
  label,
  value,
  unit,
  change,
  alertBorder,
  alertBg,
}: {
  label: string;
  value: string;
  unit: string;
  change: number | null;
  alertBorder?: string;
  alertBg?: string;
}) {
  const borderColor = alertBorder || "transparent";
  const bgColor = alertBg || D.bg;

  return (
    <div
      style={{
        width: 200,
        minWidth: 200,
        padding: "16px 14px",
        borderRadius: 8,
        background: bgColor,
        border: `2px solid ${borderColor}`,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        fontFamily: "JetBrains Mono, monospace",
      }}
    >
      <span style={{ fontSize: 11, color: D.comment, fontWeight: 500 }}>
        {label}
      </span>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontSize: 24, color: D.fg, fontWeight: 700 }}>
          {value}
        </span>
        {unit && unit !== "—" && (
          <span style={{ fontSize: 11, color: D.comment }}>{unit}</span>
        )}
      </div>
      {change !== null && change !== undefined && !Number.isNaN(change) ? (
        <span style={{ fontSize: 13, color: changeColor(change), fontWeight: 600 }}>
          {changeSign(change)}{fmtNum(change, 2)}%
        </span>
      ) : (
        <span style={{ fontSize: 13, color: D.comment }}>—</span>
      )}
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────── */

export default function MacroPage() {
  const [data, setData] = useState<MacroData | null>(null);
  const [history, setHistory] = useState<MacroData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [lastFetchTs, setLastFetchTs] = useState<number>(0);

  const fetchMacro = () => {
    fetch("/api/macro")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((body: MacroResponse) => {
        setData(body.data);
        setHistory(body.history || []);
        setError(body.error || null);
        setLoading(false);
        setTick((t) => t + 1);
        setLastFetchTs(Date.now());
      })
      .catch((e: Error) => {
        setError(e.message);
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchMacro();
    const id = setInterval(fetchMacro, 60_000); // auto-refresh every 60s
    return () => clearInterval(id);
  }, []);

  const isStale =
    !loading &&
    !error &&
    data &&
    data.ts > 0 &&
    Date.now() - data.ts > 600_000; // 10min stale threshold

  /* ── Alert threshold logic ─────────────────────────── */

  const northboundAlert =
    data?.northbound_net !== null &&
    data?.northbound_net !== undefined &&
    !Number.isNaN(data.northbound_net) &&
    data.northbound_net < -50
      ? { border: D.red, bg: "rgba(255, 85, 85, 0.06)" }
      : undefined;

  const goldAlert =
    data?.gold_change_pct !== null &&
    data?.gold_change_pct !== undefined &&
    !Number.isNaN(data.gold_change_pct) &&
    Math.abs(data.gold_change_pct) > 2
      ? { border: D.orange, bg: "rgba(255, 184, 108, 0.06)" }
      : undefined;

  const copperAlert =
    data?.copper_change_pct !== null &&
    data?.copper_change_pct !== undefined &&
    !Number.isNaN(data.copper_change_pct) &&
    Math.abs(data.copper_change_pct) > 3
      ? { border: D.red, bg: "rgba(255, 85, 85, 0.06)" }
      : undefined;

  const vixAlert =
    data?.vix !== null &&
    data?.vix !== undefined &&
    !Number.isNaN(data.vix) &&
    data.vix > 25
      ? { border: D.red, bg: "rgba(255, 85, 85, 0.06)" }
      : undefined;

  const ty10yAlert =
    data?.ty10y !== null &&
    data?.ty10y !== undefined &&
    !Number.isNaN(data.ty10y) &&
    data.ty10y > 4.5
      ? { border: D.orange, bg: "rgba(255, 184, 108, 0.06)" }
      : undefined;

  const fxAlert =
    data?.usd_cnh_change_pct !== null &&
    data?.usd_cnh_change_pct !== undefined &&
    !Number.isNaN(data.usd_cnh_change_pct) &&
    data.usd_cnh_change_pct < -0.5
      ? { border: D.orange, bg: "rgba(255, 184, 108, 0.06)" }
      : undefined;

  const tungstenAlert =
    data?.tungsten_change_pct !== null &&
    data?.tungsten_change_pct !== undefined &&
    !Number.isNaN(data.tungsten_change_pct) &&
    Math.abs(data.tungsten_change_pct) > 2
      ? { border: D.orange, bg: "rgba(255, 184, 108, 0.06)" }
      : undefined;

  /* ── Derived values ────────────────────────────────── */

  const lastClock =
    data && data.ts > 0
      ? new Date(data.ts).toLocaleTimeString("zh-CN", { hour12: false })
      : "--:--:--";

  const isUninitialized =
    !loading &&
    !error &&
    data &&
    data.northbound_net === null &&
    data.gold_price === null &&
    data.copper_price === null &&
    data.vix === null &&
    data.ty10y === null &&
    data.usd_cnh === null &&
    data.tungsten_price === null;

  /* ── Render ────────────────────────────────────────── */

  return (
    <div
      style={{
        background: D.bg,
        color: D.fg,
        minHeight: "100vh",
        fontFamily: "JetBrains Mono, monospace",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <AppTitleBar title="macro — 宏观指标" />

      {/* Status bar */}
      <div
        style={{
          padding: "10px 16px",
          display: "flex",
          alignItems: "center",
          gap: 12,
          fontSize: 11,
          color: D.comment,
          borderBottom: `1px solid ${D.currentLine}`,
        }}
      >
        <span style={{ color: D.cyan, fontWeight: 700 }}>macro</span>
        <span style={{ color: D.comment }}>|</span>
        {loading ? (
          <span>Loading...</span>
        ) : error ? (
          <span style={{ color: D.red, fontWeight: 600 }}>
            [ERROR] {error}
          </span>
        ) : isStale ? (
          <>
            <span style={{ color: D.orange, fontWeight: 600 }}>stale {Math.round((Date.now() - (data?.ts ?? 0)) / 60_000)}min</span>
            <span style={{ color: D.comment }}>|</span>
            <span>last {lastClock}</span>
          </>
        ) : (
          <>
            <span>last {lastClock}</span>
            <span style={{ color: D.comment }}>|</span>
            <span>已刷新 {tick} 次</span>
          </>
        )}
      </div>

      {/* Cards grid */}
      <div
        style={{
          padding: "16px",
          display: "flex",
          flexWrap: "wrap",
          gap: 12,
          flex: 1,
          alignContent: "flex-start",
        }}
      >
        {loading ? (
          <div style={{ color: D.comment, fontSize: 13, padding: "20px 0" }}>
            Loading...
          </div>
        ) : error ? (
          <div style={{ color: D.red, fontSize: 13, padding: "20px 0", fontWeight: 600 }}>
            [ERROR] {error}
          </div>
        ) : isUninitialized ? (
          <div style={{ color: D.comment, fontSize: 13, padding: "20px 0" }}>
            宏观数据未初始化 · 等待 poller 首次更新
          </div>
        ) : (
          <>
            {/* 1. 北向资金 */}
            <MacroCard
              label="北向净流入"
              value={fmtNum(data?.northbound_net ?? null, 2)}
              unit="亿元"
              change={null}
              alertBorder={northboundAlert?.border}
              alertBg={northboundAlert?.bg}
            />

            {/* 2. 黄金 */}
            <MacroCard
              label="COMEX黄金"
              value={fmtNum(data?.gold_price ?? null, 2)}
              unit="USD"
              change={data?.gold_change_pct ?? null}
              alertBorder={goldAlert?.border}
              alertBg={goldAlert?.bg}
            />

            {/* 3. 铜 */}
            <MacroCard
              label="LME铜"
              value={fmtNum(data?.copper_price ?? null, 2)}
              unit="USD"
              change={data?.copper_change_pct ?? null}
              alertBorder={copperAlert?.border}
              alertBg={copperAlert?.bg}
            />

            {/* 4. VIX */}
            <MacroCard
              label="VIX恐慌指数"
              value={fmtNum(data?.vix ?? null, 2)}
              unit=""
              change={null}
              alertBorder={vixAlert?.border}
              alertBg={vixAlert?.bg}
            />

            {/* 5. 汇率 */}
            <MacroCard
              label="离岸人民币"
              value={fmtNum(data?.usd_cnh ?? null, 4)}
              unit=""
              change={data?.usd_cnh_change_pct ?? null}
              alertBorder={fxAlert?.border}
              alertBg={fxAlert?.bg}
            />

            {/* 6. 钨价 */}
            <MacroCard
              label="65%黑钨精矿"
              value={fmtNum(data?.tungsten_price ?? null, 2)}
              unit="万元/标吨"
              change={data?.tungsten_change_pct ?? null}
              alertBorder={tungstenAlert?.border}
              alertBg={tungstenAlert?.bg}
            />

            {/* ty10y 副指标 — 显示在北向卡片旁边但不单独成卡 */}
            <MacroCard
              label="美债10Y"
              value={fmtNum(data?.ty10y ?? null, 3)}
              unit=""
              change={null}
              alertBorder={ty10yAlert?.border}
              alertBg={ty10yAlert?.bg}
            />
          </>
        )}
      </div>

      {/* History placeholder */}
      {!loading && !error && (
        <div
          style={{
            padding: "16px",
            borderTop: `1px solid ${D.currentLine}`,
            color: D.comment,
            fontSize: 12,
          }}
        >
          <div style={{ marginBottom: 8, fontWeight: 600, color: D.fg }}>
            历史趋势
          </div>
          <div
            style={{
              padding: "40px 0",
              textAlign: "center",
              color: D.comment,
              fontSize: 13,
              border: `1px dashed ${D.currentLine}`,
              borderRadius: 8,
            }}
          >
            历史趋势功能开发中
          </div>
          {history.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 11, color: D.comment }}>
              历史记录: {history.length} 条
            </div>
          )}
        </div>
      )}

      {/* Bottom refresh hint */}
      <div
        style={{
          padding: "8px 16px",
          borderTop: `1px solid ${D.currentLine}`,
          fontSize: 11,
          color: D.comment,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>
          API: /api/macro · 数据来源: backend poller
        </span>
        <span>refresh {lastClock}</span>
      </div>
    </div>
  );
}
