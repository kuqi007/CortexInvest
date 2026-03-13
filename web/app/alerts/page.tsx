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
  stats: {
    totalSignals: number;
    totalAlerts: number;
    l1Count: number;
    bullish: number;
    bearish: number;
    stockCount: number;
    upCount: number;
    downCount: number;
  };
  perStock: {
    code: string;
    name: string;
    change: number;
    signalCount: number;
    direction: string;
    keySignals: string[];
  }[];
  report: string;
}

const LEVEL_COLORS: Record<number, string> = {
  1: D.yellow,
  2: D.orange,
  3: D.comment,
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

type AlertParser = (e: AlertEvent, d: string, sym: string, shortCode: string) => ParsedAlert | null;

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
  const m = d.match(/^(?:HK|KR)?\d+\s+(.+?)\s+(涨幅|跌幅)\s+([\d.]+)%(?:\s+现价([\d.]+))?/);
  if (!m) return null;
  return {
    stockName: m[1].trim(),
    stockCode: shortCode,
    signal: m[2] === "涨幅" ? "大涨" : "大跌",
    signalColor: m[2] === "涨幅" ? D.red : D.green,
    price: m[4] || "",
    detail: `${m[2]}${m[3]}%`,
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

function parseTradePlan(e: AlertEvent, d: string, _sym: string, shortCode: string): ParsedAlert | null {
  // 两种格式:
  // 1. "📋 五一视界 回踩分批建仓 | 主力成本区+MA20 | 买入 200 股 @ 55.00" (新格式)
  // 2. "HK06651 五一视界 跌幅 11.6%" (旧格式 fallback)
  let planName = "", label = "", priceStr = "";
  if (d.includes("📋")) {
    // 新格式
    const parts = d.replace(/^📋\s*/, "").split(/\s*\|\s*/);
    planName = parts[0] || "";
    label = parts[1] || "";
    const action = parts[2] || "";
    const priceMatch = action.match(/@\s*([\d.]+)/);
    priceStr = priceMatch ? priceMatch[1] : "";
  } else {
    // 旧格式 fallback: "HK06651 五一视界 跌幅 11.6%"
    const priceMatch = d.match(/现价([\d.]+)/);
    priceStr = priceMatch ? priceMatch[1] : "";
    // 提取股票名 (跳过 code)
    const parts = d.split(/\s+/);
    planName = parts.length > 1 ? parts.slice(1).join(" ").split(/\s/)[0] : "";
  }
  return {
    stockName: planName.split(/\s+/)[0] || "",
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
  trade_plan:  parseTradePlan,
};

// ── 统一入口 ──────────────────────────────────────────────────────────────────
function parseAlert(e: AlertEvent): ParsedAlert {
  const d = e.display || "";
  const sym = e.symbol || "";
  const shortCode = sym.startsWith("tag:") ? sym.replace("tag:", "") : sym.replace(/^(?:HK|KR)/, "");

  const parser = KIND_PARSERS[e.kind];
  if (parser) {
    const result = parser(e, d, sym, shortCode);
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

type SignalChip = { signal: string; color: string; count: number };
type TimelineEvent = { time: string; level: number; signal: string; signalColor: string; price: string; changePct: number; detail: string };

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

function buildGroups(events: AlertEvent[]): StockGroup[] {
  const map = new Map<string, StockGroup>();
  // Process in chronological order so latest overwrites
  for (const e of events) {
    const parsed = parseAlert(e);
    const key = e.symbol || parsed.stockCode || "unknown";
    let group = map.get(key);
    if (!group) {
      group = {
        symbol: e.symbol || "",
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
      map.set(key, group);
    }
    // Update with latest data
    if (parsed.price) group.price = parsed.price;
    if (e.change_pct) group.changePct = e.change_pct;
    if (parsed.stockName) group.stockName = parsed.stockName;
    if ((e.level ?? 2) < group.maxLevel) group.maxLevel = e.level ?? 2;
    if (e.time) group.latestTime = e.time;
    group.totalCount++;

    // Merge signal chips (for summary row)
    const existing = group.signals.find((s) => s.signal === parsed.signal);
    if (existing) {
      existing.count++;
    } else {
      group.signals.push({ signal: parsed.signal, color: parsed.signalColor, count: 1 });
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

function GroupedRow({ group, expanded, onToggle }: { group: StockGroup; expanded: boolean; onToggle: () => void }) {
  const isHighPriority = group.maxLevel <= 1;
  return (
    <div>
      <div
        onClick={onToggle}
        style={{
          display: "flex",
          alignItems: "center",
          padding: "5px 6px",
          borderBottom: "1px solid #191a21",
          background: isHighPriority ? "#44475a" : "transparent",
          borderLeft: isHighPriority ? `3px solid ${D.yellow}` : "3px solid transparent",
          cursor: "pointer",
          gap: 4,
        }}
      >
        {/* Expand arrow */}
        <span style={{ color: D.comment, width: 16, flexShrink: 0, fontSize: 10 }}>
          {expanded ? "\u25be" : "\u25b8"}
        </span>
        {/* Level badge */}
        <span style={{ color: LEVEL_COLORS[group.maxLevel] || D.comment, flexShrink: 0, width: 24, fontWeight: 700, fontSize: 11 }}>
          L{group.maxLevel}
        </span>
        {/* Stock code */}
        <span style={{ color: D.cyan, flexShrink: 0, width: 64, fontWeight: 600 }}>
          {group.stockCode}
        </span>
        {/* Stock name */}
        <span style={{ color: D.fg, flexShrink: 0, width: 90, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {group.stockName}
        </span>
        {/* Price + change% */}
        {group.price && (
          <span style={{ flexShrink: 0, width: 110, display: "flex", gap: 4, fontSize: 11 }}>
            <span style={{ color: D.fg }}>{group.price}</span>
            {group.changePct !== 0 && (
              <span style={{ color: group.changePct > 0 ? D.red : D.green, fontWeight: 600 }}>
                {group.changePct > 0 ? "+" : ""}{group.changePct.toFixed(1)}%
              </span>
            )}
          </span>
        )}
        {/* Signal: 只显示最新信号，去掉聚合噪音 */}
        <span style={{ marginLeft: 8, flex: 1, display: "flex", gap: 4, overflow: "hidden" }}>
          {group.signals[0] && (
            <span
              key={group.signals[0].signal}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
                background: group.signals[0].color + "22",
                color: group.signals[0].color,
                padding: "1px 6px",
                borderRadius: 3,
                fontSize: 10,
                fontWeight: 600,
                whiteSpace: "nowrap",
                border: `1px solid ${group.signals[0].color}44`,
              }}
            >
              {group.signals[0].signal}
            </span>
          )}
          {group.signals.length > 1 && (
            <span style={{ color: D.comment, fontSize: 10, flexShrink: 0 }}>
              +{group.signals.length - 1}
            </span>
          )}
        </span>
        {/* Total count */}
        <span style={{ color: D.comment, flexShrink: 0, fontSize: 10, marginLeft: 4 }}>
          {group.totalCount}条
        </span>
        {/* Latest time */}
        <span style={{ color: D.comment, flexShrink: 0, width: 56, textAlign: "right", fontSize: 11 }}>
          {group.latestTime}
        </span>
      </div>
      {/* Expanded timeline */}
      {expanded && (
        <div style={{ background: "#1a1b26", borderLeft: "3px solid #44475a" }}>
          {[...group.timeline].reverse().map((ev, i) => (
            <div
              key={`${ev.time}-${i}`}
              style={{
                display: "flex",
                alignItems: "baseline",
                padding: "2px 6px 2px 24px",
                borderBottom: "1px solid #15161e",
                fontSize: 12,
                gap: 4,
              }}
            >
              <span style={{ color: D.comment, fontSize: 9, flexShrink: 0, width: 10 }}>&#9679;</span>
              <span style={{ color: D.comment, width: 56, flexShrink: 0 }}>{ev.time}</span>
              <span style={{ color: LEVEL_COLORS[ev.level] || D.comment, width: 22, flexShrink: 0, fontSize: 10, fontWeight: 600 }}>
                L{ev.level}
              </span>
              <span style={{ color: ev.signalColor, width: 96, flexShrink: 0, fontWeight: 600 }}>{ev.signal}</span>
              {ev.price && <span style={{ color: D.fg, width: 56, flexShrink: 0, textAlign: "right" }}>{ev.price}</span>}
              {ev.changePct ? <span style={{ color: ev.changePct > 0 ? D.red : D.green, width: 52, flexShrink: 0, textAlign: "right" }}>{ev.changePct >= 0 ? "+" : ""}{ev.changePct.toFixed(1)}%</span> : null}
              <span style={{ color: D.comment, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {ev.detail}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function AlertsPage() {
  const { alertEvents: events, loading, fetchError, services } = useMetrics();
  const { status: tradingStatus } = useTradingStatus();
  const [summary, setSummary] = useState<DailySummary | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(true);
  const [showL3, setShowL3] = useState(false);
  const [viewMode, setViewMode] = useState<"grouped" | "detail">("grouped");
  const [expandedStocks, setExpandedStocks] = useState<Set<string>>(new Set());

  const fetchSummary = useCallback(async () => {
    try {
      const res = await fetch("/api/summary", { cache: "no-store" });
      const sData = await res.json();
      const s = sData.data;
      const today = new Date().toISOString().slice(0, 10);
      setSummary(s && s.date === today ? s : null);
    } catch {
      // summary fetch failure is non-critical
    }
  }, []);

  useEffect(() => {
    fetchSummary();
    const timer = setInterval(fetchSummary, 30_000);
    return () => clearInterval(timer);
  }, [fetchSummary]);

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
  const HIGH_VALUE_KINDS = new Set(["trade_plan", "MAINLINE", "big_move", "threshold", "gap_fade", "gap_recover"]);
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
  const groups = useMemo(() => buildGroups(filtered), [filtered]);

  const toggleStock = useCallback((key: string) => {
    setExpandedStocks((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }, []);

  const st = summary?.stats;

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
        {/* ── Daily Summary Card ── */}
        {summary && (
          <div
            style={{
              border: `1px solid ${D.purple}44`,
              borderRadius: 4,
              marginBottom: 12,
              background: "#21222c",
            }}
          >
            {/* Header row — always visible */}
            <div
              onClick={() => setSummaryOpen(!summaryOpen)}
              style={{
                padding: "6px 12px",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 8,
                userSelect: "none",
                borderBottom: summaryOpen ? `1px solid ${D.purple}33` : "none",
              }}
            >
              <span style={{ color: D.purple, fontSize: 11, width: "2ch" }}>
                {summaryOpen ? "\u25be" : "\u25b8"}
              </span>
              <span style={{ color: D.purple, fontWeight: 700 }}>
                # ── 信号日报 {summary.date}
              </span>
              {st && (
                <span style={{ color: D.comment, fontSize: 11 }}>
                  ({st.totalSignals} signals)
                </span>
              )}
              {/* Stats chips */}
              {st && (
                <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
                  L1:{st.l1Count}
                  {" | "}
                  <span style={{ color: D.green }}>bull:{st.bullish}</span>
                  {" "}
                  <span style={{ color: D.red }}>bear:{st.bearish}</span>
                  {" | "}
                  <span style={{ color: D.green }}>up:{st.upCount}</span>
                  {" "}
                  <span style={{ color: D.red }}>down:{st.downCount}</span>
                </span>
              )}
            </div>

            {/* Collapsible report content */}
            {summaryOpen && (
              <div style={{ padding: "8px 12px 12px", lineHeight: 1.7 }}>
                <TerminalMarkdown text={summary.report} />
              </div>
            )}
          </div>
        )}

        {fetchError && (
          <div style={{ color: D.red, marginBottom: 8, fontWeight: 500 }}>
            [ERROR] alert events fetch failed: {fetchError}
          </div>
        )}

        {loading && (
          <div style={{ color: D.comment }}>Loading...</div>
        )}

        {!loading && !fetchError && sorted.length === 0 && !summary && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            # No alert events today. Events reset daily at 08:00.
          </div>
        )}

        {viewMode === "grouped" ? (
          groups.map((g) => (
            <GroupedRow
              key={g.symbol || g.stockCode}
              group={g}
              expanded={expandedStocks.has(g.symbol || g.stockCode)}
              onToggle={() => toggleStock(g.symbol || g.stockCode)}
            />
          ))
        ) : (
          sorted.map((e, i) => {
            const parsed = parseAlert(e);
            const chgColor = e.change_pct > 0 ? D.red : e.change_pct < 0 ? D.green : D.comment;
            const isHighPriority = (e.level ?? 2) <= 1;

            return (
              <div
                key={`${e.ts}-${i}`}
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  padding: "3px 6px",
                  borderBottom: "1px solid #191a21",
                  background: isHighPriority ? "#44475a" : "transparent",
                  borderLeft: isHighPriority ? `3px solid ${D.yellow}` : "3px solid transparent",
                }}
              >
                {/* Time */}
                <span style={{ color: D.comment, flexShrink: 0, width: 72 }}>{e.time}</span>
                {/* Level */}
                <span style={{ color: LEVEL_COLORS[e.level ?? 2] || D.comment, flexShrink: 0, width: 28, fontWeight: isHighPriority ? 700 : 500 }}>
                  {`L${e.level ?? 2}`}
                </span>
                {/* Signal name (actual strategy) */}
                <span style={{ color: parsed.signalColor, flexShrink: 0, width: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: isHighPriority ? 700 : 500 }}>
                  {parsed.signal}
                </span>
                {/* Stock code */}
                <span style={{ color: D.cyan, flexShrink: 0, width: 72, overflow: "hidden", whiteSpace: "nowrap" }}>
                  {parsed.stockCode}
                </span>
                {/* Price */}
                <span style={{ color: D.fg, flexShrink: 0, width: 60, textAlign: "right" }}>
                  {parsed.price}
                </span>
                {/* Change% */}
                <span style={{ color: chgColor, flexShrink: 0, width: 52, textAlign: "right" }}>
                  {e.change_pct ? `${e.change_pct >= 0 ? "+" : ""}${e.change_pct.toFixed(1)}%` : ""}
                </span>
                {/* Name + Detail */}
                <span style={{ color: isHighPriority ? D.yellow : D.comment, marginLeft: 10, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {parsed.stockName ? <span style={{ color: D.fg }}>{parsed.stockName} </span> : null}{parsed.detail}
                </span>
              </div>
            );
          })
        )}

      </div>
    </div>
  );
}
