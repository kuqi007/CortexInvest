"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { D } from "../theme";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";
import { useMetrics } from "../providers/MetricsProvider";
import { useTradingStatus } from "../lib/trading-hours";
import type { AlertEvent } from "../types";

interface DailySummary {
  date: string;
  generatedAt: number;
  market: string;
  stats?: {
    totalSignals: number;
    totalAlerts: number;
    l1Count: number;
    bullish: number;
    bearish: number;
    stockCount: number;
    upCount: number;
    downCount: number;
  };
  perStock?: {
    code: string;
    name: string;
    change: number;
    signalCount: number;
    direction: string;
    keySignals: string[];
    sentiment?: number | null;  // 新闻情感分，-1~1，由 Kimi json_schema 分析
  }[];
  report?: string;
  morning?: {
    generated_at: string;
    us_markets: Record<string, { name: string; change_pct: number }>;
    asia_markets: Record<string, { name: string; change_pct: number }>;
    global_news: { title: string; summary: string; source: string; time: string; url: string }[];
  } | null;
}

const LEVEL_COLORS: Record<number, string> = {
  1: D.yellow,
  2: D.orange,
  3: D.comment,
};

/** Rows from GET /api/ai-investment-events (subset used by alerts page). */
type AiInvestmentEventApiRow = {
  id: string;
  event_date: string;
  symbol: string | null;
  name: string | null;
  source: string;
  event_type: string;
  severity: string;
  delivery_scope: string;
  verdict: string;
  title: string;
  summary: string;
  metrics_json?: string | null;
  recommendation_json?: string | null;
  notify_status: string;
  created_at: string;
  /** Pre-parsed metrics_json to avoid repeated JSON.parse on every render */
  _parsedMetrics?: {
    pre_earnings?: { verdict?: string; score?: number };
  } | null;
};

const KIND_LABELS: Record<string, { label: string; color: string }> = {
  big_move: { label: "大幅异动", color: D.orange },
  threshold: { label: "触价告警", color: D.red },
  portfolio: { label: "组合变动", color: D.purple },
  l2_strategy: { label: "L2信号", color: D.cyan },
  STALE: { label: "数据监控", color: "#ff6b6b" },
  DRIFT: { label: "涨跌追踪", color: "#8be9fd" },
  MAINLINE: { label: "主线行情", color: "#ff5555" },
  trade_plan: { label: "交易计划", color: D.purple },
};

// ── Alert 解析器类型 ──────────────────────────────────────────────────────────

type ParsedAlert = {
  stockName: string;   // "海天味业"
  stockCode: string;   // "03288"
  signal: string;      // "均线多排" / "高开低走" 等
  signalColor: string;
  price: string;       // "33.84"
  detail: string;
};

type AlertParser = (e: AlertEvent, d: string, sym: string, shortCode: string, services?: { id: string; name?: string }[]) => ParsedAlert | null;

// ── 各 kind 的解析函数 ────────────────────────────────────────────────────────
// 新增形态只需：① 写一个函数，② 在 KIND_PARSERS 里加一行，完成。

function parseL2Strategy(e: AlertEvent, d: string, _sym: string, shortCode: string): ParsedAlert | null {
  const priceSplit = d.split(/\s*\|\s*现价/);
  const mainPart = priceSplit[0] || "";
  const priceTail = priceSplit[1] || "";
  const price = priceTail.match(/^([\d.]+)/)?.[1] || "";
  const tail = priceTail.replace(/^[\d.]+\s*/, "").replace(/^日[涨跌][+-]?[\d.]+%\s*/, "").trim();
  const signalKeywords = "MACD|RSI|均线|布林|ADX|趋势|放量|缩量|突破|破位|吞没|看涨|看跌|星|盘口|委比|量价|相对|散户|机构|主力|主买|主卖|大单|动量|资金|尾盘|多头|空头";
  const m = mainPart.match(new RegExp(`^(?:HK|KR)?\\d+\\s+(.+?)\\s+((?:${signalKeywords})[^:：]*)(?:[:：]\\s*(.*))?$`));
  if (!m) return null;
  return {
    stockName: m[1].trim(),
    stockCode: shortCode,
    signal: m[2].trim(),
    signalColor: getSignalColor(m[2].trim()),
    price,
    detail: [m[3]?.trim(), tail].filter(Boolean).join(" "),
  };
}

function parseBigMove(_e: AlertEvent, d: string, _sym: string, shortCode: string): ParsedAlert | null {
  // display 格式: "600673 东阳光 ↓-4.0% → 34.24" 或 "HK07709 ＸＬ二南方海力士 ↓-2.4% → 34.56"
  const m = d.match(/^(?:HK|KR)?\d+\s+(.+?)\s+([↑↓][+-]?[\d.]+%)/);
  if (!m) return null;
  const pctStr = m[2]; // e.g. "↓-4.0%"
  const isUp = pctStr.startsWith("↑");
  return {
    stockName: m[1].trim(),
    stockCode: shortCode,
    signal: isUp ? "大涨" : "大跌",
    signalColor: isUp ? D.red : D.green,
    price: d.match(/→\s*([\d.]+)/)?.[1] || "",
    detail: pctStr,
  };
}

function parseThreshold(_e: AlertEvent, d: string, _sym: string, shortCode: string): ParsedAlert | null {
  const m = d.match(/^(?:HK|KR)?\d+\s+(.+?)\s+触价告警\s*(.*)/);
  if (!m) return null;
  return {
    stockName: m[1].trim(),
    stockCode: shortCode,
    signal: "触价",
    signalColor: D.red,
    price: m[2]?.replace(/[! ]/g, "") || "",
    detail: "触及阈值",
  };
}

function parseGapFade(_e: AlertEvent, d: string, _sym: string, shortCode: string): ParsedAlert | null {
  // "{code} {name} 高开低走: 高开+X.X% 回落-Y.Y%[ 缺口完全回吐] | 现价Z.ZZ"
  const m = d.match(/^(?:HK|KR)?\d+\s+(.+?)\s+高开低走:\s*高开([+\d.]+%)\s+回落([-\d.]+%)([^|]*)\|\s*现价([\d.]+)/);
  if (!m) return null;
  return {
    stockName: m[1].trim(),
    stockCode: shortCode,
    signal: m[4].includes("缺口完全回吐") ? "高开低走 缺口回吐" : "高开低走",
    signalColor: "#ff6b6b",
    price: m[5],
    detail: `高开${m[2]} 回落${m[3]}`,
  };
}

function parseGapRecover(_e: AlertEvent, d: string, _sym: string, shortCode: string): ParsedAlert | null {
  // "{code} {name} 低开高走: 低开-X.X% 反弹+Y.Y%[ 缺口完全收复] | 现价Z.ZZ"
  const m = d.match(/^(?:HK|KR)?\d+\s+(.+?)\s+低开高走:\s*低开([-\d.]+%)\s+反弹([+\d.]+%)([^|]*)\|\s*现价([\d.]+)/);
  if (!m) return null;
  return {
    stockName: m[1].trim(),
    stockCode: shortCode,
    signal: m[4].includes("缺口完全收复") ? "低开高走 缺口收复" : "低开高走",
    signalColor: "#50fa7b",
    price: m[5],
    detail: `低开${m[2]} 反弹${m[3]}`,
  };
}

function parseDrift(e: AlertEvent, d: string, sym: string, shortCode: string): ParsedAlert | null {
  const isIndex = sym.startsWith("tag:");
  const direction = e.change_pct > 0;
  const price = d.match(/(?:现价|当前)([\d.]+)/)?.[1] || "";
  const name = d.match(/^📊\s*(.+?)(?:\([\dA-Z]+\)|\s+距)/)?.[1]?.trim() || "";
  return {
    stockName: isIndex ? sym.replace("tag:", "") + "指数" : name,
    stockCode: isIndex ? "" : shortCode,
    signal: direction ? `距关注涨${Math.abs(e.change_pct).toFixed(1)}%` : `距关注跌${Math.abs(e.change_pct).toFixed(1)}%`,
    signalColor: direction ? "#50fa7b" : "#ff5555",
    price,
    detail: d.replace(/^📊\s*/, ""),
  };
}

function parseMainline(_e: AlertEvent, d: string, sym: string, _shortCode: string): ParsedAlert | null {
  const isApproaching = d.includes("接近主线");
  return {
    stockName: sym.replace("tag:", "") + "指数",
    stockCode: "",
    signal: isApproaching ? "接近主线" : "主线确认",
    signalColor: isApproaching ? "#ffb86c" : "#ff5555",
    price: "",
    detail: d.replace(/^[🔥⚡]\s*/, ""),
  };
}

function parseStale(_e: AlertEvent, d: string, _sym: string, _shortCode: string): ParsedAlert | null {
  const isRecovery = d.includes("恢复") || d.startsWith("[OK]");
  return {
    stockName: isRecovery ? "恢复" : "系统",
    stockCode: "",
    signal: isRecovery ? "数据恢复" : "数据异常",
    signalColor: isRecovery ? D.green : "#ff6b6b",
    price: "",
    detail: d.replace(/^\[(OK|WARN)\]\s*/, ""),
  };
}

function parsePortfolio(_e: AlertEvent, d: string, _sym: string, _shortCode: string): ParsedAlert | null {
  return { stockName: "组合", stockCode: "", signal: "组合P&L", signalColor: D.purple, price: "", detail: d };
}

function parseAiInvestment(
  _e: AlertEvent,
  d: string,
  sym: string,
  shortCode: string,
  services?: { id: string; name?: string }[],
): ParsedAlert | null {
  const svc = services?.find((s) => s.id === sym);
  const stockName =
    svc?.name?.trim() ||
    (sym.replace(/^(?:HK|KR)/i, "").trim() || "—");
  return {
    stockName,
    stockCode: shortCode,
    signal: "AI财报",
    signalColor: D.yellow,
    price: "",
    detail: d,
  };
}

function parseTradePlan(e: AlertEvent, d: string, _sym: string, shortCode: string, services?: { id: string; name?: string }[]): ParsedAlert | null {
  // 两种格式:
  // 1. "📋 澜起科技持有策略 | 卖出 100股: 止盈1——200卖100股(1/3) | 卖出 100 股 @ 200.00" (plan name only)
  // 2. "📋 五一视界 回踩分批建仓 | ... | 买入 200 股 @ 55.00" (has stock name in plan name)
  // 3. "HK06651 五一视界 跌幅 11.6%" (旧格式 fallback)
  let planName = "", label = "", priceStr = "";
  if (d.includes("📋")) {
    const parts = d.replace(/^📋\s*/, "").split(/\s*\|\s*/);
    planName = parts[0] || "";
    label = parts[1] || "";
    const action = parts[2] || "";
    const priceMatch = action.match(/@\s*([\d.]+)/);
    priceStr = priceMatch ? priceMatch[1] : "";
  } else {
    const priceMatch = d.match(/现价([\d.]+)/);
    priceStr = priceMatch ? priceMatch[1] : "";
    const parts = d.split(/\s+/);
    planName = parts.length > 1 ? parts.slice(1).join(" ").split(/\s/)[0] : "";
  }
  // 从 plan name 中提取股票名称（去掉"持有策略"/"建仓仓"等后缀）
  let stockName = planName.split(/\s+/)[0] || "";
  const sym = e.symbol || "";
  // 去掉"持有策略"、"建仓策略"等后缀，看剩下的部分是否需要查 services
  const nameWithoutSuffix = stockName
    .replace(/持有策略$/, "")
    .replace(/建仓策略$/, "")
    .replace(/左侧试探$/, "")
    .replace(/右侧确认$/, "");
  // 如果名称以 HK/KR 开头或纯数字（股票代码），则查 services 获取真实名称
  const needsLookup = !nameWithoutSuffix ||
    /^(?:HK|KR|\d{5,})$/.test(nameWithoutSuffix) ||
    /^\d{6}$/.test(nameWithoutSuffix); // A 股如 600xxx
  if (needsLookup) {
    const svc = services?.find((s) => s.id === sym);
    if (svc?.name) stockName = svc.name;
    else if (nameWithoutSuffix && !/^(?:HK|KR|\d{5,})$/.test(nameWithoutSuffix) && nameWithoutSuffix !== stockName)
      stockName = nameWithoutSuffix;
  } else {
    stockName = nameWithoutSuffix;
  }
  return {
    stockName,
    stockCode: shortCode,
    signal: label || "交易计划",
    signalColor: D.purple,
    price: priceStr,
    detail: d,
  };
}

// ── 注册表：新增形态只改这里 ─────────────────────────────────────────────────
const KIND_PARSERS: Record<string, AlertParser> = {
  l2_strategy: parseL2Strategy,
  big_move:    parseBigMove,
  threshold:   parseThreshold,
  gap_fade:    parseGapFade,
  gap_recover: parseGapRecover,
  DRIFT:       parseDrift,
  MAINLINE:    parseMainline,
  STALE:       parseStale,
  portfolio:   parsePortfolio,
  ai_investment: parseAiInvestment,
  trade_plan:  parseTradePlan,
};

// ── 统一入口 ──────────────────────────────────────────────────────────────────
function parseAlert(e: AlertEvent, services?: { id: string; name?: string }[]): ParsedAlert {
  const d = e.display || "";
  const sym = e.symbol || "";
  const shortCode = sym.startsWith("tag:") ? sym.replace("tag:", "") : sym.replace(/^(?:HK|KR)/, "");

  const parser = KIND_PARSERS[e.kind];
  if (parser) {
    const result = parser(e, d, sym, shortCode, services);
    if (result) return result;
  }

  // Fallback：未注册 kind 或解析失败
  return { stockName: "", stockCode: shortCode, signal: e.kind, signalColor: D.comment, price: "", detail: d };
}

/** Color for signal names */
function getSignalColor(signal: string): string {
  // Bearish signals → red/orange
  if (/空头|死叉|顶背离|破位|大跌|主卖|空排/.test(signal)) return "#ff6b6b";
  // Bullish signals → green
  if (/多头|金叉|底背离|多排|大涨|突破回踩/.test(signal)) return D.green;
  // Divergence/reversal → yellow (warning)
  if (/背离|翻转|失衡|超买|超卖/.test(signal)) return D.yellow;
  // Neutral/info
  if (/盘口|相对|趋势|RSI|布林|吞没/.test(signal)) return D.cyan;
  return D.orange;
}

/** Strip **bold** markers from heading text (headings are already styled bold) */
function stripBold(s: string): string {
  return s.replace(/\*\*([^*]+)\*\*/g, "$1");
}

/** Render markdown report in terminal style */
function TerminalMarkdown({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        const trimmed = line.trimStart();
        // H3 (must check before H2 — "###" starts with "##")
        if (trimmed.startsWith("### ")) {
          return (
            <div key={i} style={{ color: D.cyan, fontWeight: 600, marginTop: 6, marginBottom: 2 }}>
              {stripBold(trimmed.slice(4))}
            </div>
          );
        }
        // H2
        if (trimmed.startsWith("## ")) {
          return (
            <div key={i} style={{ color: D.purple, fontWeight: 700, marginTop: 8, marginBottom: 2 }}>
              {stripBold(trimmed.slice(3))}
            </div>
          );
        }
        // H1
        if (trimmed.startsWith("# ")) {
          return (
            <div key={i} style={{ color: D.yellow, fontWeight: 700, marginTop: i > 0 ? 8 : 0, marginBottom: 4 }}>
              {stripBold(trimmed.slice(2))}
            </div>
          );
        }
        // Bullet
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          const content = trimmed.slice(2);
          return (
            <div key={i} style={{ color: D.fg, paddingLeft: 16 }}>
              <span style={{ color: D.comment }}>  - </span>
              <MarkdownInline text={content} />
            </div>
          );
        }
        // Numbered list
        const numMatch = trimmed.match(/^(\d+)\.\s(.+)/);
        if (numMatch) {
          return (
            <div key={i} style={{ color: D.fg, paddingLeft: 16 }}>
              <span style={{ color: D.comment }}>  {numMatch[1]}. </span>
              <MarkdownInline text={numMatch[2]} />
            </div>
          );
        }
        // Empty line
        if (!trimmed) {
          return <div key={i} style={{ height: 6 }} />;
        }
        // Regular text
        return (
          <div key={i} style={{ color: D.fg }}>
            <MarkdownInline text={trimmed} />
          </div>
        );
      })}
    </>
  );
}

/** Render inline markdown: **bold** */
function MarkdownInline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <span key={i} style={{ color: D.orange, fontWeight: 600 }}>
              {part.slice(2, -2)}
            </span>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function TabBar({
  l1Count,
  l2Count,
  l3Count,
  showL3,
  onToggleL3,
  visibleCount,
  viewMode,
  onToggleView,
  groupCount,
  services,
  trading,
}: {
  l1Count: number;
  l2Count: number;
  l3Count: number;
  showL3: boolean;
  onToggleL3: () => void;
  visibleCount: number;
  viewMode: "grouped" | "detail";
  onToggleView: () => void;
  groupCount: number;
  services?: { id: string }[];
  trading?: boolean;
}) {
  return (
    <AppTabs
      active="alerts"
      rightSlot={(
        <span style={{ color: D.comment, fontSize: 11, marginLeft: 12, marginRight: 16, display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: D.fg }}>{new Date().toISOString().slice(0, 10)}</span>
          <span style={{ color: D.comment }}>|</span>
          <span style={{ color: trading ? D.green : D.comment }}>
            {trading ? "交易中" : "休市"}
          </span>
          <span style={{ color: D.comment }}>|</span>
          <span style={{ color: D.yellow }}>L1:{l1Count}</span>
          <span style={{ color: D.orange }}>L2:{l2Count}</span>
          <span
            onClick={onToggleL3}
            style={{
              color: D.comment,
              cursor: "pointer",
              textDecoration: "underline",
              textDecorationStyle: "dotted" as const,
            }}
            title={showL3 ? "Hide L3 signals" : "Show L3 signals"}
          >
            L3:{l3Count} {showL3 ? "(shown)" : "(hidden)"}
          </span>
          <span style={{ color: D.comment }}>|</span>
          <span
            onClick={onToggleView}
            style={{ cursor: "pointer", textDecoration: "underline", textDecorationStyle: "dotted" as const, color: D.cyan }}
            title={viewMode === "grouped" ? "Switch to detail view" : "Switch to grouped view"}
          >
            {viewMode === "grouped" ? `${groupCount} stocks` : `${visibleCount} events`}
          </span>
          <span style={{ color: D.comment }}>| 30s</span>
        </span>
      )}
    />
  );
}

// ── Grouped view types ───────────────────────────────────────────────────────

type SignalChip = { signal: string; color: string; count: number; price: string };
type TimelineEvent = { time: string; level: number; signal: string; signalColor: string; price: string; changePct: number; detail: string };

// Priority order for signal display — most important first
const SIGNAL_PRIORITY = [
  "移动止损", "硬止损", "止损",
  "分批建仓", "建仓", "加仓",
  "均线多排", "均线空排", "MACD金叉", "MACD死叉",
  "突破买入", "反弹买入",
  "触价卖出", "触价买入",
  "主线", "空头信号", "多头信号",
  "分批止盈", "止盈", "清仓",
];
function signalPriority(s: string): number {
  const idx = SIGNAL_PRIORITY.findIndex((p) => s.startsWith(p));
  return idx === -1 ? 99 : idx;
}

type StockGroup = {
  symbol: string;
  stockCode: string;
  stockName: string;
  price: string;
  changePct: number;
  maxLevel: number;
  signals: SignalChip[];
  timeline: TimelineEvent[];
  totalCount: number;
  latestTime: string;
};

// Normalize stock code to consistent key — strips SH/SZ/HK/BJ prefixes and leading zeros
function normalizeKey(code: string): string {
  return code.replace(/^(SH|SZ|HK|BJ)/i, "").replace(/^0+/, "").toUpperCase();
}

function buildGroups(events: AlertEvent[], services?: { id: string; name?: string }[]): StockGroup[] {
  const map = new Map<string, StockGroup>();
  // Process in chronological order so latest overwrites
  for (const e of events) {
    const parsed = parseAlert(e, services);
    // Normalize both symbol sources to the same key — handles HK00700 vs 00700 inconsistency
    const symKey = normalizeKey(e.symbol || parsed.stockCode || "unknown");
    let group = map.get(symKey);
    if (!group) {
      group = {
        symbol: symKey, // normalized key — consistent across all events for same stock
        stockCode: parsed.stockCode,
        stockName: parsed.stockName,
        price: parsed.price,
        changePct: e.change_pct || 0,
        maxLevel: e.level ?? 2,
        signals: [],
        timeline: [],
        totalCount: 0,
        latestTime: e.time || "",
      };
      map.set(symKey, group);
    }
    // Update with latest data
    if (parsed.price) group.price = parsed.price;
    if (e.change_pct) group.changePct = e.change_pct;
    if (parsed.stockName) group.stockName = parsed.stockName;
    if ((e.level ?? 2) < group.maxLevel) group.maxLevel = e.level ?? 2;
    if (e.time) group.latestTime = e.time;
    group.totalCount++;

    // Merge signal chips (for summary row) — store price of most recent occurrence
    const existing = group.signals.find((s) => s.signal === parsed.signal);
    if (existing) {
      existing.count++;
      if (parsed.price) existing.price = parsed.price;
    } else {
      group.signals.push({ signal: parsed.signal, color: parsed.signalColor, count: 1, price: parsed.price || "" });
    }

    // Push to timeline (for expanded view)
    group.timeline.push({
      time: e.time || "",
      level: e.level ?? 2,
      signal: parsed.signal,
      signalColor: parsed.signalColor,
      price: parsed.price,
      changePct: e.change_pct || 0,
      detail: parsed.detail,
    });
  }
  // Sort: latest alert time desc (most recent activity first)
  return Array.from(map.values()).sort((a, b) => b.latestTime.localeCompare(a.latestTime));
}

// Flat stock row — all events visible at once, no expand needed
function FlatStockRow({ group }: { group: StockGroup }) {
  const isHighPriority = group.maxLevel <= 1;
  // Sort signals by priority — most important first
  const sortedSignals = [...group.signals].sort((a, b) => signalPriority(a.signal) - signalPriority(b.signal));

  // Check if a signal is "critical" (stop loss, plan trigger)
  const isCritical = (s: string) =>
    s.includes("止损") || s.includes("触价") || s.includes("清仓") || s.includes("止盈");

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        padding: "5px 6px",
        borderBottom: "1px solid #191a21",
        background: isHighPriority ? "#44475a" : "transparent",
        borderLeft: isHighPriority ? `3px solid ${D.yellow}` : "3px solid transparent",
        gap: 4,
        fontSize: 12,
        minHeight: 36,
      }}
    >
      {/* Level badge */}
      <span style={{
        color: LEVEL_COLORS[group.maxLevel] || D.comment,
        flexShrink: 0,
        width: 22,
        fontWeight: 700,
        fontSize: 11,
        textAlign: "center",
        background: `${LEVEL_COLORS[group.maxLevel] || D.comment}22`,
        borderRadius: 3,
        padding: "1px 0",
      }}>
        L{group.maxLevel}
      </span>

      {/* Stock code */}
      <span style={{ color: D.cyan, flexShrink: 0, width: 72, fontWeight: 600, fontSize: 12 }}>
        {group.stockCode}
      </span>

      {/* Stock name */}
      <span style={{ color: D.fg, flexShrink: 0, width: 80, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {group.stockName}
      </span>

      {/* Current price */}
      <span style={{ color: D.fg, flexShrink: 0, width: 56, textAlign: "right", fontSize: 12 }}>
        {group.price || "—"}
      </span>

      {/* Current change% */}
      {group.changePct != null ? (
        <span style={{ color: group.changePct > 0 ? D.red : D.green, flexShrink: 0, width: 48, textAlign: "right", fontWeight: 600, fontSize: 12 }}>
          {group.changePct >= 0 ? "+" : ""}{group.changePct.toFixed(1)}%
        </span>
      ) : (
        <span style={{ flexShrink: 0, width: 48 }} />
      )}

      {/* Separator */}
      <span style={{ color: D.comment, flexShrink: 0, fontSize: 10 }}>|</span>

      {/* All event chips — sorted by priority */}
      <span style={{
        flex: 1,
        display: "flex",
        gap: 4,
        overflow: "hidden",
        alignItems: "center",
        flexWrap: "wrap",
        padding: "2px 0",
      }}>
        {sortedSignals.map((chip, i) => (
          <span
            key={i}
            title={`${chip.signal} × ${chip.count}${chip.price ? ` @ ${chip.price}` : ""} (最后 ${group.timeline.filter(t => t.signal === chip.signal).pop()?.time || ""})`}
            style={{
              background: isCritical(chip.signal) ? `${chip.color}44` : `${chip.color}18`,
              color: chip.color,
              border: `1px solid ${chip.color}66`,
              borderRadius: 4,
              padding: "2px 6px",
              fontSize: 11,
              fontWeight: isCritical(chip.signal) ? 700 : 600,
              flexShrink: 0,
              whiteSpace: "nowrap",
              letterSpacing: 0.2,
            }}
          >
            {chip.signal}
            {chip.count > 1 && <span style={{ fontSize: 10, marginLeft: 2, opacity: 0.8 }}>×{chip.count}</span>}
            {chip.price && <span style={{ fontSize: 10, marginLeft: 3, opacity: 0.85 }}>@{chip.price}</span>}
          </span>
        ))}
      </span>

      {/* Total event count */}
      <span style={{ color: D.comment, flexShrink: 0, fontSize: 10, width: 36, textAlign: "right" }}>
        {group.totalCount}条
      </span>

      {/* Latest event time */}
      <span style={{ color: D.comment, flexShrink: 0, fontSize: 10, width: 64, textAlign: "right" }}>
        {group.latestTime}
      </span>
    </div>
  );
}

export default function AlertsPage() {
  const { alertEvents: events, loading, fetchError, services, tick } = useMetrics();
  const { status: tradingStatus } = useTradingStatus();
  const [showL3, setShowL3] = useState(false);
  const [viewMode, setViewMode] = useState<"grouped" | "detail">("grouped");
  const [earningsAiRows, setEarningsAiRows] = useState<AiInvestmentEventApiRow[]>([]);
  const [earningsAiErr, setEarningsAiErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Retry on mount and whenever the metrics tick refreshes (covers error recovery)
    fetch("/api/ai-investment-events?limit=40")
      .then((r) => {
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        return r.json() as Promise<{ data?: AiInvestmentEventApiRow[] }>;
      })
      .then((body) => {
        if (cancelled) {
          return;
        }
        const rows = (body.data ?? []).filter((x) =>
          (x.event_type ?? "").startsWith("earnings")
        );
        // Pre-parse metrics_json to avoid repeated JSON.parse on every render
        const parsedRows = rows.map((row) => {
          if (!row.metrics_json) return { ...row, _parsedMetrics: null };
          try {
            const m = JSON.parse(row.metrics_json) as {
              pre_earnings?: { verdict?: string; score?: number };
            };
            return { ...row, _parsedMetrics: m };
          } catch {
            return { ...row, _parsedMetrics: null };
          }
        });
        setEarningsAiRows(parsedRows);
        setEarningsAiErr(null);
      })
      .catch(() => {
        if (!cancelled) {
          setEarningsAiErr("ai-investment-events unavailable");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  // Level counts (memoized to avoid re-filtering on every render)
  const { l1Count, l2Count, l3Count } = useMemo(() => {
    let l1 = 0, l2 = 0, l3 = 0;
    for (const e of events) {
      const lvl = e.level ?? 2;
      if (lvl === 1) l1++;
      else if (lvl === 2) l2++;
      else if (lvl === 3) l3++;
    }
    return { l1Count: l1, l2Count: l2, l3Count: l3 };
  }, [events]);

  // 高价值信号 kinds — 始终显示（不受 L3 过滤影响）
  const HIGH_VALUE_KINDS = new Set([
    "trade_plan",
    "MAINLINE",
    "big_move",
    "threshold",
    "gap_fade",
    "gap_recover",
    "ai_investment",
  ]);
  // 按时间倒序（最新在前），默认隐藏 L3 + 低价值 kinds
  const filtered = useMemo(() => {
    return events.filter((e) => {
      // L1 始终显示
      if ((e.level ?? 2) <= 1) return true;
      // 高价值 kinds 始终显示
      if (HIGH_VALUE_KINDS.has(e.kind)) return true;
      // L2 + L3 需要 showL3
      return showL3;
    });
  }, [events, showL3]);
  const sorted = useMemo(() => [...filtered].reverse(), [filtered]);
  const groups = useMemo(() => buildGroups(filtered, services), [filtered, services]);

  const st = null;

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: D.bg,
        fontFamily: '"JetBrains Mono", Menlo, Monaco, "Courier New", monospace',
        color: D.fg,
        fontSize: 13,
      }}
    >
      <AppTitleBar title="alerts — event log" />

      <TabBar
        l1Count={l1Count}
        l2Count={l2Count}
        l3Count={l3Count}
        showL3={showL3}
        onToggleL3={() => setShowL3(!showL3)}
        visibleCount={sorted.length}
        viewMode={viewMode}
        onToggleView={() => setViewMode((v) => v === "grouped" ? "detail" : "grouped")}
        groupCount={groups.length}
        services={services}
        trading={tradingStatus?.trading}
      />

      {/* event list */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "10px 16px",
          lineHeight: 1.6,
        }}
      >
        {/* ── Statistics cards (always visible) ── */}
        <div
          style={{
            display: "flex",
            gap: 10,
            marginBottom: 12,
            flexWrap: "wrap",
          }}
        >
          {[
            { label: "L1 告警", count: l1Count, color: D.yellow, desc: "弹窗+声音" },
            { label: "L2 告警", count: l2Count, color: D.orange, desc: "弹窗" },
            { label: "L3 信号", count: l3Count, color: D.comment, desc: "Web仅显示" },
            { label: "监控标的", count: services?.length ?? 0, color: D.cyan, desc: "总股票数" },
          ].map((card) => (
            <div
              key={card.label}
              style={{
                background: `${card.color}11`,
                border: `1px solid ${card.color}33`,
                borderRadius: 6,
                padding: "8px 14px",
                minWidth: 100,
                display: "flex",
                alignItems: "baseline",
                gap: 8,
              }}
            >
              <span
                style={{
                  color: card.color,
                  fontWeight: 700,
                  fontSize: 18,
                  lineHeight: 1,
                }}
              >
                {card.count}
              </span>
              <div style={{ display: "flex", flexDirection: "column" }}>
                <span style={{ color: D.fg, fontSize: 11, fontWeight: 600 }}>
                  {card.label}
                </span>
                <span style={{ color: D.comment, fontSize: 10 }}>
                  {card.desc}
                </span>
              </div>
            </div>
          ))}
        </div>

        {fetchError && (
          <div style={{ color: D.red, marginBottom: 8, fontWeight: 500 }}>
            [ERROR] alert events fetch failed: {fetchError}
          </div>
        )}

        {(earningsAiRows.length > 0 || earningsAiErr) && (
          <div
            style={{
              marginBottom: 12,
              padding: "8px 10px",
              border: `1px solid ${D.comment}44`,
              borderRadius: 4,
              background: "#1e1f29",
            }}
          >
            <div style={{ color: D.yellow, fontWeight: 700, marginBottom: 6, fontSize: 12 }}>
              财报 AI 事件流
              <span style={{ color: D.comment, fontWeight: 400, marginLeft: 8 }}>
                ai_investment_events（持仓/star）
              </span>
            </div>
            {earningsAiErr && (
              <div style={{ color: D.comment, fontSize: 11 }}>{earningsAiErr}</div>
            )}
            {earningsAiRows.map((row, idx) => (
              <div
                key={row.id}
                style={{
                  fontSize: 11,
                  color: D.fg,
                  padding: "4px 0",
                  borderTop: idx === 0 ? "none" : `1px solid ${D.comment}22`,
                }}
              >
                <span style={{ color: D.cyan }}>{row.symbol ?? "—"}</span>
                <span style={{ color: D.comment, margin: "0 6px" }}>{row.event_type}</span>
                <span style={{ color: D.comment }}>{row.notify_status}</span>
                <span style={{ color: D.comment, marginLeft: 8 }}>{row.created_at?.slice(0, 16)}</span>
                <div style={{ color: D.fg, marginTop: 2 }}>{row.title}</div>
                <div style={{ color: D.comment, marginTop: 2 }}>{row.summary}</div>
                {(() => {
                  const pe = row._parsedMetrics?.pre_earnings;
                  if (pe?.verdict !== undefined && pe.score !== undefined) {
                    return (
                      <div style={{ color: D.orange, marginTop: 4, fontSize: 10 }}>
                        预判 {pe.verdict} · 评分 {pe.score}
                      </div>
                    );
                  }
                  return null;
                })()}
              </div>
            ))}
          </div>
        )}

        {loading && (
          <div style={{ color: D.comment }}>Loading...</div>
        )}

        {!loading && !fetchError && sorted.length === 0 && (
          <>
            <div style={{ padding: "32px 0", textAlign: "center" }}>
              <div style={{ color: D.fg, fontSize: 16, marginBottom: 8, display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
                <span>今日暂无新告警</span>
              </div>
              <div style={{ color: D.comment, fontSize: 12, marginBottom: 12 }}>
                上次重置: 08:00 · 每日自动清零
              </div>
              <div
                style={{
                  display: "inline-flex",
                  gap: 12,
                  background: `${D.bg}`,
                  border: `1px solid ${D.comment}33`,
                  borderRadius: 6,
                  padding: "8px 14px",
                  fontSize: 11,
                }}
              >
                <span style={{ color: D.yellow }}>L1(弹窗+声音)</span>
                <span style={{ color: D.comment }}>|</span>
                <span style={{ color: D.orange }}>L2(弹窗)</span>
                <span style={{ color: D.comment }}>|</span>
                <span style={{ color: D.comment }}>L3(Web仅显示)</span>
              </div>
            </div>

            {/* 7-day trend placeholder */}
            <div
              style={{
                marginTop: 16,
                padding: "16px 20px",
                border: `1px dashed ${D.comment}33`,
                borderRadius: 6,
                background: `${D.comment}08`,
                color: D.comment,
                fontSize: 12,
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 14, marginBottom: 6, color: D.fg }}>
                最近7天告警趋势
              </div>
              <div>历史数据回顾功能开发中</div>
            </div>
          </>
        )}

        {viewMode === "grouped" ? (
          <>
            {/* Column header — matches FlatStockRow layout */}
            <div style={{ display: "flex", alignItems: "center", padding: "2px 6px", borderBottom: `1px solid ${D.comment}44`, gap: 4, fontSize: 10, color: D.comment }}>
              <span style={{ flexShrink: 0, width: 22, textAlign: "center" }}>级别</span>
              <span style={{ flexShrink: 0, width: 72 }}>代码</span>
              <span style={{ flexShrink: 0, width: 80 }}>名称</span>
              <span style={{ flexShrink: 0, width: 56, textAlign: "right" }}>现价</span>
              <span style={{ flexShrink: 0, width: 48, textAlign: "right" }}>涨跌</span>
              <span style={{ flex: 1, flexShrink: 0 }}>信号 (重要程度排序)</span>
              <span style={{ flexShrink: 0, width: 36, textAlign: "right" }}>条</span>
              <span style={{ flexShrink: 0, width: 64, textAlign: "right" }}>最后时间</span>
            </div>
            {groups.map((g) => (
              <FlatStockRow key={g.symbol || g.stockCode} group={g} />
            ))}
          </>
        ) : (
          <>
            {/* Detail view — same flat stock rows but sorted by event time */}
            <div style={{ display: "flex", alignItems: "center", padding: "2px 6px", borderBottom: `1px solid ${D.comment}44`, gap: 4, fontSize: 10, color: D.comment }}>
              <span style={{ flexShrink: 0, width: 22, textAlign: "center" }}>级别</span>
              <span style={{ flexShrink: 0, width: 72 }}>代码</span>
              <span style={{ flexShrink: 0, width: 80 }}>名称</span>
              <span style={{ flexShrink: 0, width: 56, textAlign: "right" }}>现价</span>
              <span style={{ flexShrink: 0, width: 48, textAlign: "right" }}>涨跌</span>
              <span style={{ flex: 1, flexShrink: 0 }}>信号 (时间排序)</span>
              <span style={{ flexShrink: 0, width: 36, textAlign: "right" }}>条</span>
              <span style={{ flexShrink: 0, width: 64, textAlign: "right" }}>最后时间</span>
            </div>
            {/* Detail view also uses flat stock rows — all events visible per stock */}
            {groups
              .slice()
              .sort((a, b) => b.latestTime.localeCompare(a.latestTime))
              .map((g) => (
                <FlatStockRow key={`detail-${g.symbol || g.stockCode}`} group={g} />
              ))}
          </>
        )}

      </div>
    </div>
  );
}
