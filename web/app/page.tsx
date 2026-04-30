"use client";

import { Suspense, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { useAlerts } from "./hooks/useAlerts";
import { useLogEntries } from "./hooks/useCommand";
import { AppTabs } from "./components/AppTabs";
import { AppTitleBar } from "./components/AppTitleBar";
import { MarketSwitch, type MarketTab } from "./components/MarketSwitch";
import MarketSummaryBar from './components/MarketSummaryBar';
import { useMetrics } from "./providers/MetricsProvider";
import { useTradePlans } from "./hooks/useTradePlans";
import { StockDrawer } from "./components/StockDrawer";

import type { Service } from "./types";
import { tagColor } from "./lib/tag-utils";
import { useTradingStatus } from "./lib/trading-hours";

const DEFAULT_POLL_SEC = 30;

import { D } from "./theme";

function chgColor(v: number) {
  return v > 0 ? D.red : v < 0 ? D.green : D.comment;
}

function pad(s: string, n: number, right = false): string {
  return right ? s.padStart(n) : s.padEnd(n);
}

function fmtAmt(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e8) return sign + (abs / 1e8).toFixed(1) + "亿";
  if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0) + "万";
  return n.toFixed(0);
}

function fmtMoney(n: number): string {
  const sign = n >= 0 ? "+" : "";
  const abs = Math.abs(n);
  if (abs >= 1e4) return `${sign}${(n / 1e4).toFixed(1)}万`;
  return `${sign}${Math.round(n).toLocaleString("en-US")}`;
}

/* ── Main ── */
export default function Page() {
  return (
    <Suspense>
      <Home />
    </Suspense>
  );
}

function Home() {
  const { services, ts, tick, loading, settings, fetchError, alertEvents, marketTurnover, hkdCnyRate, refresh } = useMetrics();
  const { status: tradingStatus } = useTradingStatus();
  // tab state: URL ?tab=A|HK, default by time (before 15:00 → A, after → HK)
  const searchParams = useSearchParams();

  function getDefaultTab(): MarketTab {
    const param = searchParams.get("tab")?.toUpperCase();
    if (param === "A" || param === "HK") return param;
    return new Date().getHours() < 15 ? "A" : "HK";
  }

  const [activeTab, setActiveTab] = useState<MarketTab>(getDefaultTab);

  function switchTab(tab: MarketTab) {
    setActiveTab(tab);
    clearLogs();  // 清掉另一个市场的 alert 日志
    window.history.replaceState(null, "", `/?tab=${tab}`);
  }
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [prodStockOpen, setProdStockOpen] = useState(true);
  const [prodETFOpen, setProdETFOpen] = useState(true);
  const [hiddenOpen, setHiddenOpen] = useState(false);

  const { logs, addLogs, clearLogs } = useLogEntries();
  const { planMap, refresh: refreshPlans } = useTradePlans();
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [addCode, setAddCode] = useState("");
  const [addCost, setAddCost] = useState("");
  const [addShares, setAddShares] = useState("");

  async function handleAdd() {
    const code = addCode.trim();
    if (!code) return;
    if (!/^(HK\d{5,6}|\d{6})$/.test(code)) { alert("股票代码格式错误\n港股: HK00700  A股: 600519"); return; }
    const data: Record<string, unknown> = { type: "holding" };
    if (addCost) data.cost = Number(addCost);
    if (addShares) data.shares = Number(addShares);
    try {
      const resp = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "add", code, data }),
      });
      const result = await resp.json();
      if (result?.success) {
        setAddCode(""); setAddCost(""); setAddShares("");
        refresh();
      }
    } catch { /* ignore */ }
  }

  // Filter alerts by active market tab (HK symbols start with "HK", rest are A-share)
  // Portfolio-level alerts (empty symbol) show in both tabs
  const tabAlertEvents = alertEvents.filter(
    (e) => !e.symbol || (activeTab === "HK" ? e.symbol.startsWith("HK") : !e.symbol.startsWith("HK"))
  );
  useAlerts(tabAlertEvents, addLogs, activeTab);

  const pollMs = (settings.poll_interval ?? DEFAULT_POLL_SEC) * 1000;

  // 持仓排序状态，支持虚拟字段 mktVal / totalPnl / dayPnl
  type SortKey = keyof Service | "mktVal" | "totalPnl" | "dayPnl";
  type SortState = { key: SortKey | null; asc: boolean };
  const [holdSort, setHoldSort] = useState<SortState>({ key: null, asc: false });

  function toggleHoldSort(key: SortKey) {
    setHoldSort((prev) =>
      prev.key === key ? { key, asc: !prev.asc } : { key, asc: false }
    );
  }

  // 今日盈亏：当日买入的股票用 totalPnl 封顶（不可能今天赚的比总共赚的还多）
  function calcDayPnl(s: Service, fx: number): number {
    if (s.shares == null) return 0;
    const raw = s.chgAmt * s.shares * fx;
    if (s.cost == null || s.cost === 0) return raw;
    const total = (s.price - s.cost) * s.shares * fx;
    if (total >= 0 && raw > 0 && raw > total) return total;
    if (total <= 0 && raw < 0 && raw < total) return total;
    return raw;
  }

  function derivedVal(s: Service, key: SortKey): number {
    if (key === "mktVal") return s.shares != null ? s.price * s.shares : -Infinity;
    if (key === "totalPnl") return s.cost != null && s.shares != null ? (s.price - s.cost) * s.shares : -Infinity;
    if (key === "dayPnl") return s.shares != null ? calcDayPnl(s, 1) : -Infinity;
    return (s[key as keyof Service] as number) ?? -Infinity;
  }

  function applySortList(list: Service[], st: SortState): Service[] {
    const pinned = (s: Service) => s.pin_order ?? 0;
    if (!st.key) return list.slice().sort((a, b) => {
      const pp = pinned(b) - pinned(a);
      if (pp !== 0) return pp;
      return b.change - a.change;
    });
    return list.slice().sort((a, b) => {
      const pp = pinned(b) - pinned(a);
      if (pp !== 0) return pp;
      const av = derivedVal(a, st.key!);
      const bv = derivedVal(b, st.key!);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return st.asc ? cmp : -cmp;
    });
  }

  const isHK = (s: Service) => s.id.startsWith("HK");
  const isETF = (s: Service) => !isHK(s) && /^(51|15|58)\d{4}$/.test(s.id);
  const inTab = (s: Service) => activeTab === "HK" ? isHK(s) : !isHK(s);
  const tabServices = useMemo(() => services.filter((s) => inTab(s)), [services, activeTab]);
  const tabHoldingAll = useMemo(() => tabServices.filter((s) => s.type === "holding"), [tabServices]);
  const tagFiltered = useMemo(() => filterTag
    ? tabServices.filter((s) => s.tags?.includes(filterTag))
    : tabServices, [tabServices, filterTag]);
  const searchFiltered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tagFiltered;
    return tagFiltered.filter((s) =>
      s.id.toLowerCase().includes(q) || (s.alias || s.name).toLowerCase().includes(q)
    );
  }, [tagFiltered, search]);
  const pinnedList = useMemo(() => applySortList(searchFiltered.filter((s) => s.type === "holding" && !s.hidden && (s.star || (s.pin_order ?? 0) > 0)), holdSort), [searchFiltered, holdSort]);
  const pinnedIds = useMemo(() => new Set(pinnedList.map((s) => s.id)), [pinnedList]);
  const prodStock = useMemo(() => applySortList(searchFiltered.filter((s) => s.type === "holding" && !s.hidden && !isETF(s) && !pinnedIds.has(s.id)), holdSort), [searchFiltered, holdSort, pinnedIds]);
  const prodETF = useMemo(() => applySortList(searchFiltered.filter((s) => s.type === "holding" && !s.hidden && isETF(s) && !pinnedIds.has(s.id)), holdSort), [searchFiltered, holdSort, pinnedIds]);
  const hiddenList = useMemo(() => applySortList(searchFiltered.filter((s) => s.hidden && s.type === "holding"), holdSort), [searchFiltered, holdSort]);
  const hasHold = pinnedList.length > 0 || prodStock.length > 0 || prodETF.length > 0;
  const allTags = useMemo(
    () => [...new Set(services.flatMap((s) => s.tags ?? []))],
    [services]
  );

  const now = ts
    ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })
    : "--:--:--";
  const isStale = (ts > 0 && Date.now() - ts > pollMs * 3) || fetchError !== null;


  // ── 当前 Tab 统计 ──
  const tabUp = tabHoldingAll.filter((s) => s.change > 0).length;
  const tabDn = tabHoldingAll.filter((s) => s.change < 0).length;
  const tabAmt = tabHoldingAll.reduce((a, s) => a + s.amount, 0);
  const tabAvgChg = tabHoldingAll.length > 0
    ? tabHoldingAll.reduce((a, s) => a + s.change, 0) / tabHoldingAll.length : 0;
  const tabHoldCount = tabHoldingAll.filter((s) => !s.hidden).length;

  // ── 当前 Tab P&L ──
  const tabHoldings = tabServices.filter((s) => s.type === "holding" && s.pnl !== null && s.cost && s.shares);
  const tabPnl = tabHoldings.reduce((sum, s) => sum + (s.price - s.cost!) * s.shares!, 0);
  const tabTodayPnl = tabHoldings.reduce((sum, s) => sum + calcDayPnl(s, 1), 0);
  const tabPosition = tabHoldings.reduce((sum, s) => sum + s.price * s.shares!, 0);
  const tabCostBasis = tabHoldings.reduce((sum, s) => sum + s.cost! * s.shares!, 0);
  const tabReturnPct = tabCostBasis > 0 ? (tabPnl / tabCostBasis) * 100 : 0;
  const avail = activeTab === "HK" ? (settings.available_balance_hkd ?? 0) : (settings.available_balance_rmb ?? 0);
  const totalAssets = avail + tabPosition;

  /* ── sort header helpers (per-section) ── */
  const mkArrow = (st: SortState) => (k: SortKey) =>
    st.key === k ? (st.asc ? " ▲" : " ▼") : "";
  const mkHStyle = (st: SortState) => (
    w: string,
    k: SortKey | null,
    right = false,
  ): React.CSSProperties => ({
    width: w,
    textAlign: right ? "right" : "left",
    cursor: k ? "pointer" : "default",
    userSelect: "none",
    color: k && st.key === k ? D.yellow : D.pink,
  });

  /* ── Tag chip style ── */
  const tagChipStyle = (tag: string): React.CSSProperties => ({
    display: "inline-block",
    padding: "1px 6px",
    borderRadius: 3,
    fontSize: 10,
    marginRight: 3,
    cursor: "pointer",
    color: "#282a36",
    background: tagColor(tag),
    whiteSpace: "nowrap",
  });

  /* ── Holdings Row ── */
  function HoldRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const pnlPctStr = s.pnl !== null ? `${s.pnl >= 0 ? "+" : ""}${s.pnl.toFixed(1)}%` : "-";
    const mktVal = s.shares != null ? s.price * s.shares : null;
    const totalPnlRaw = s.cost != null && s.cost !== 0 && s.shares != null ? (s.price - s.cost) * s.shares : null;
    const dayPnl = s.shares != null ? calcDayPnl(s, 1) : null;
    return (
      <div
        style={{
          display: "flex",
          whiteSpace: "pre",
          padding: "1px 0",
          borderBottom: `1px solid #191a21`,
          cursor: "pointer",
        }}
        onClick={() => setDrawerSymbol(s.id)}
        title="点击查看详情"
      >
        {/* Plan/Star/Dip indicators — replaces type column */}
        <span style={{ width: "9ch", display: "inline-flex", gap: 2, alignItems: "center" }}>
          {s.star && (
            <span style={{
              border: `1px solid ${D.yellow}`, color: D.yellow,
              fontSize: 10, padding: "0 2px", lineHeight: "1.4",
              fontFamily: "JetBrains Mono, monospace",
            }}>★</span>
          )}
          {(planMap[s.id]?.length ?? 0) > 0 && (
            <span style={{
              border: `1px solid ${D.cyan}`, color: D.cyan,
              fontSize: 10, padding: "0 2px", lineHeight: "1.4",
              fontFamily: "JetBrains Mono, monospace",
            }}>条</span>
          )}
          {s.dip_buy && (
            <span style={{
              border: `1px solid ${D.green}`, color: D.green,
              fontSize: 10, padding: "0 2px", lineHeight: "1.4",
              fontFamily: "JetBrains Mono, monospace",
            }}>d</span>
          )}
        </span>
        <span style={{ color: D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
        <span style={{ color: D.fg, width: "10ch" }}>{pad((s.alias || s.name).slice(0, 6), 8)}</span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(s.price.toFixed(2), 9, true)}
        </span>
        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(s.cost != null ? s.cost.toFixed(2) : "-", 8, true)}
        </span>
        <span style={{ color: D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.shares != null ? String(s.shares) : "-", 6, true)}
        </span>
        <span style={{ color: s.pnl !== null ? chgColor(s.pnl) : D.comment, width: "10ch", textAlign: "right", fontWeight: 500 }}>
          {pad(pnlPctStr, 9, true)}
        </span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(mktVal != null ? fmtAmt(mktVal) : "-", 9, true)}
        </span>
        <span style={{ color: D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.position_pct != null ? `${(s.position_pct * 100).toFixed(1)}%` : "-", 7, true)}
        </span>
        <span style={{ color: totalPnlRaw !== null ? chgColor(totalPnlRaw) : D.comment, width: "10ch", textAlign: "right", fontWeight: 500 }}>
          {pad(totalPnlRaw !== null ? fmtMoney(totalPnlRaw) : "-", 9, true)}
        </span>
        <span style={{ color: dayPnl != null ? chgColor(dayPnl) : D.comment, width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(dayPnl != null ? fmtMoney(dayPnl) : "-", 8, true)}
        </span>
        <span style={{ color: s.volRatio >= 1.5 ? D.red : s.volRatio <= 0.5 ? D.comment : D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.volRatio > 0 ? s.volRatio.toFixed(2) : "-", 6, true)}
        </span>
        <span style={{ color: s.turnover >= 5 ? D.red : D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.turnover > 0 ? s.turnover.toFixed(2) : "-", 7, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(fmtAmt(s.amount), 8, true)}
        </span>
        {activeTab === "HK" && (<span style={{ color: s.mainNetInflow != null ? chgColor(s.mainNetInflow) : D.comment, width: "9ch", textAlign: "right" }}>
          {pad(s.mainNetInflow != null ? fmtAmt(s.mainNetInflow) : "-", 8, true)}
        </span>)}
        {activeTab === "HK" && (<span style={{ color: s.mainNetInflowPct != null ? chgColor(s.mainNetInflowPct) : D.comment, width: "7ch", textAlign: "right" }}>
          {s.mainNetInflowPct != null ? `${s.mainNetInflowPct >= 0 ? "+" : ""}${s.mainNetInflowPct.toFixed(1)}%` : pad("-", 6, true)}
        </span>)}
        <span style={{ width: "12ch", overflow: "hidden", whiteSpace: "nowrap", marginLeft: 8 }}>
          {(s.tags ?? []).map((t) => (
            <span key={t} style={tagChipStyle(t)} onClick={(e) => { e.stopPropagation(); setFilterTag(t); }}>{t}</span>
          ))}
        </span>
      </div>
    );
  }

  /* ── Watching Row (different column set) ── */
  function WatchRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const csign = s.chgAmt > 0 ? "+" : "";

    return (
      <div
        style={{
          display: "flex",
          whiteSpace: "pre",
          padding: "1px 0",
          borderBottom: `1px solid #191a21`,
        }}
      >
        <span style={{ color: s.star ? D.yellow : D.comment, width: "9ch" }}>{s.star ? "★" : " "} DEV</span>
        <span style={{ color: D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
        <span style={{ color: D.fg, width: "10ch" }}>{pad(s.name.slice(0, 6), 8)}</span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(s.price.toFixed(2), 9, true)}
        </span>
        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}
        </span>
        <span style={{ color: chgColor(s.chgAmt), width: "8ch", textAlign: "right" }}>
          {pad(`${csign}${s.chgAmt.toFixed(2)}`, 7, true)}
        </span>
        <span style={{ color: s.volRatio >= 1.5 ? D.red : s.volRatio <= 0.5 ? D.comment : D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.volRatio > 0 ? s.volRatio.toFixed(2) : "-", 6, true)}
        </span>
        <span style={{ color: s.turnover >= 5 ? D.red : D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.turnover > 0 ? s.turnover.toFixed(2) : "-", 7, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(fmtAmt(s.amount), 8, true)}
        </span>
        <span style={{ color: D.comment, width: "13ch", textAlign: "right" }}>
          {pad(`${s.low.toFixed(2)}-${s.high.toFixed(2)}`, 12, true)}
        </span>
        {activeTab === "HK" && (<span style={{ color: s.mainNetInflow != null ? chgColor(s.mainNetInflow) : D.comment, width: "9ch", textAlign: "right" }}>
          {pad(s.mainNetInflow != null ? fmtAmt(s.mainNetInflow) : "-", 8, true)}
        </span>)}
        {activeTab === "HK" && (<span style={{ color: s.mainNetInflowPct != null ? chgColor(s.mainNetInflowPct) : D.comment, width: "7ch", textAlign: "right" }}>
          {s.mainNetInflowPct != null ? `${s.mainNetInflowPct >= 0 ? "+" : ""}${s.mainNetInflowPct.toFixed(1)}%` : pad("-", 6, true)}
        </span>)}
        <span style={{ width: "12ch", overflow: "hidden", whiteSpace: "nowrap", marginLeft: 8 }}>
          {(s.tags ?? []).map((t) => (
            <span key={t} style={tagChipStyle(t)} onClick={() => setFilterTag(t)}>{t}</span>
          ))}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: D.bg,
      }}
    >
      <AppTitleBar title="monitor" />
      <AppTabs active="holdings" />

      {/* Terminal body */}
      <div
        style={{
          flex: 1,
          padding: "10px 16px",
          overflow: "auto",
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        <div style={{ height: 8 }} />
        <MarketSwitch activeTab={activeTab} onTabChange={switchTab} />

        {/* 大盘摘要区 */}
        <div style={{ marginBottom: 6 }}>
          <MarketSummaryBar marketTurnover={marketTurnover ?? {} as any} market={activeTab === 'A' ? 'A' : 'HK'} />
        </div>

        {loading && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.green }}>info</span> 正在加载数据...
            <span style={{ animation: "blink 1s step-end infinite" }}>...</span>
          </div>
        )}

        {!loading && (<>
        {/* tag filter bar */}
        {filterTag && (
          <div style={{ padding: "4px 8px", backgroundColor: "#44475a", color: D.fg, fontSize: 12, marginBottom: 4, display: "flex", alignItems: "center", gap: 8, borderRadius: 3 }}>
            <span>筛选: <span style={{ color: tagColor(filterTag), fontWeight: 500 }}>{filterTag}</span></span>
            <span style={{ cursor: "pointer", color: D.red, fontWeight: 500 }} onClick={() => setFilterTag(null)}>x</span>
          </div>
        )}

        {/* search + add */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
          <span style={{ color: D.comment }}>搜索:</span>
          <input
            placeholder="代码或名称..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              background: D.currentLine,
              border: `1px solid ${D.comment}`,
              color: D.fg,
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 12,
              padding: "2px 8px",
              outline: "none",
              borderRadius: 2,
              width: 140,
            }}
          />
          {search && (
            <span style={{ color: D.comment, fontSize: 11 }}>
              {searchFiltered.length}/{tabServices.length} 匹配
            </span>
          )}
          <div style={{ flex: 1 }} />
          <input
            placeholder="代码"
            value={addCode}
            onChange={(e) => setAddCode(e.target.value.toUpperCase())}
            style={{
              background: D.currentLine,
              border: `1px solid ${D.comment}`,
              color: D.fg,
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 12,
              padding: "2px 8px",
              outline: "none",
              borderRadius: 2,
              width: 90,
            }}
            onKeyDown={(e) => { if (e.key === "Enter") handleAdd(); }}
          />
          <input
            placeholder="成本"
            type="number"
            step="any"
            value={addCost}
            onChange={(e) => setAddCost(e.target.value)}
            style={{
              background: D.currentLine,
              border: `1px solid ${D.comment}`,
              color: D.fg,
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 12,
              padding: "2px 8px",
              outline: "none",
              borderRadius: 2,
              width: 70,
            }}
          />
          <input
            placeholder="股数"
            type="number"
            step={100}
            value={addShares}
            onChange={(e) => setAddShares(e.target.value)}
            style={{
              background: D.currentLine,
              border: `1px solid ${D.comment}`,
              color: D.fg,
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 12,
              padding: "2px 8px",
              outline: "none",
              borderRadius: 2,
              width: 70,
            }}
          />
          <button
            onClick={handleAdd}
            style={{
              background: D.purple,
              color: D.bg,
              border: "none",
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 12,
              fontWeight: 700,
              padding: "3px 12px",
              borderRadius: 3,
              cursor: "pointer",
            }}
          >
            添加持仓
          </button>
        </div>

        {/* watch header */}
        <div style={{ color: D.comment, marginBottom: 6 }}>
          <span>每 {pollMs / 1000} 秒轮询一次</span>
          <span style={{ float: "right", display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ color: tradingStatus?.markets?.cn ? D.green : D.comment, fontSize: 11, fontWeight: 600 }}>● A股 {tradingStatus?.markets?.cn ? "交易中" : "休市"}</span>
            <span style={{ color: tradingStatus?.markets?.hk ? D.green : D.comment, fontSize: 11, fontWeight: 600 }}>● 港股 {tradingStatus?.markets?.hk ? "交易中" : "休市"}</span>
            <span style={{ color: D.comment }}>|</span>
            {/* Freshness chip */}
            {isStale && Date.now() - ts > pollMs * 10 ? (
              // Very stale (>5 min) — show "已休市"
              <span style={{
                color: D.comment,
                fontSize: 11,
                fontWeight: 600,
                background: "rgba(98, 114, 164, 0.15)",
                border: `1px solid ${D.comment}`,
                padding: "1px 8px",
                borderRadius: 20,
              }}>已休市</span>
            ) : isStale ? (
              // Stale (2–5 min) — show minutes in orange
              <span style={{
                color: D.orange,
                fontSize: 11,
                fontWeight: 600,
                background: "rgba(255, 184, 108, 0.12)",
                border: `1px solid ${D.orange}`,
                padding: "1px 8px",
                borderRadius: 20,
              }}>数据 {Math.round((Date.now() - ts) / 60000)} 分钟前更新</span>
            ) : (
              // Fresh (<=2 min) — show seconds in green
              <span style={{
                color: D.green,
                fontSize: 11,
                fontWeight: 600,
                background: "rgba(80, 250, 123, 0.10)",
                border: `1px solid ${D.green}`,
                padding: "1px 8px",
                borderRadius: 20,
              }}>数据 {Math.round((Date.now() - ts) / 1000)} 秒前更新</span>
            )}
            {/* Alert status pill */}
            <span style={{
              fontSize: 11,
              fontWeight: 600,
              color: isStale ? D.red : D.green,
              background: isStale ? "rgba(255, 85, 85, 0.12)" : "rgba(80, 250, 123, 0.10)",
              border: `1px solid ${isStale ? D.red : D.green}`,
              padding: "1px 8px",
              borderRadius: 20,
            }}>告警 {isStale ? "过期" : "正常"}</span>
            <span style={{ color: D.comment }}>|</span>
            <span style={{ color: isStale ? D.red : D.comment }}>时间: {now}</span>
            <span style={{ color: D.comment }}> #{tick}</span>
          </span>
        </div>

        {/* stale warning banner — only show when data is stale for > 3 min and A股 is open */}
        {isStale && (Date.now() - ts > pollMs * 6) && (
          tradingStatus?.markets?.cn ? (
          <div style={{
            border: `1px solid ${D.orange}`,
            borderLeft: `3px solid ${D.orange}`,
            background: "rgba(255, 184, 108, 0.06)",
            color: D.fg,
            padding: "9px 16px",
            borderRadius: 6,
            marginBottom: 12,
            fontSize: 12,
            fontWeight: 500,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}>
            <span style={{ color: D.orange, fontSize: 14 }}>⚠</span>
            <span>
              行情已停止更新 <span style={{ color: D.orange, fontWeight: 700 }}>{Math.round((Date.now() - ts) / 60000)}</span> 分钟 — 请检查 poller 进程是否在运行
            </span>
          </div>
          ) : (
          <div style={{
            border: `1px solid ${D.comment}`,
            borderLeft: `3px solid ${D.comment}`,
            background: "rgba(98, 114, 164, 0.06)",
            color: D.comment,
            padding: "9px 16px",
            borderRadius: 6,
            marginBottom: 12,
            fontSize: 12,
            fontWeight: 500,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}>
            <span>● 已休市，行情暂时停止更新</span>
          </div>
          )
        )}

        {/* error banner */}
        {fetchError && (
          <div style={{ color: D.red, marginBottom: 6, fontWeight: 500 }}>
            [错误] 数据获取失败: {fetchError}
            {ts > 0 && (
              <span style={{ color: D.comment, fontWeight: 400 }}>
                {" "}— 显示过期数据 (上次更新: {new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })})
              </span>
            )}
          </div>
        )}

        {/* summary bar — current tab */}
        <div style={{ color: D.comment, marginBottom: 6 }}>
          <span style={{ color: D.fg }}>
            节点:<span style={{ color: D.purple }}>{tabHoldingAll.length}</span>
          </span>
          {"  "}
          持仓:<span style={{ color: D.orange }}>{tabHoldCount}</span>
          {tabHoldingAll.length > tabHoldCount && (
            <span style={{ color: D.comment, fontSize: 11 }}>(+{tabHoldingAll.length - tabHoldCount} 隐藏)</span>
          )}
          {"  "}
          涨:<span style={{ color: D.red }}>{tabUp}</span>
          {" "}跌:<span style={{ color: D.green }}>{tabDn}</span>
          {"  "}
          成交额:<span style={{ color: D.fg }}>{fmtAmt(tabAmt)}</span>
          {"  "}
          平均涨跌:
          <span style={{ color: chgColor(tabAvgChg) }}>
            {tabAvgChg >= 0 ? "+" : ""}{tabAvgChg.toFixed(2)}%
          </span>
        </div>
        {/* tab portfolio summary */}
        {tabHoldings.length > 0 && (
          <div style={{ color: D.comment, marginBottom: 6 }}>
            可用:<span style={{ color: D.fg }}>{fmtMoney(avail).replace("+", "")}</span>
            <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
            {"  "}
            总市值:<span style={{ color: D.fg }}>{fmtMoney(tabPosition).replace("+", "")}</span>
            <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
            {"  "}
            总资产:<span style={{ color: D.fg }}>{fmtMoney(totalAssets).replace("+", "")}</span>
            <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
            {"  "}
            盈亏:<span style={{ color: chgColor(tabPnl) }}>{fmtMoney(tabPnl)}</span>
            <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
            {"  "}
            收益率:<span style={{ color: chgColor(tabReturnPct) }}>{tabReturnPct >= 0 ? "+" : ""}{tabReturnPct.toFixed(1)}%</span>
            {"  "}
            今日:<span style={{ color: chgColor(tabTodayPnl) }}>{fmtMoney(tabTodayPnl)}</span>
            <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
          </div>
        )}

        {/* ══ Section renderer ══ */}
        {(() => {
          const ha = mkArrow(holdSort);
          const hs = mkHStyle(holdSort);
          const ht = toggleHoldSort;

          const holdHeader = (
            <div style={{ display: "flex", whiteSpace: "pre", color: D.pink, borderBottom: `1px solid ${D.currentLine}`, paddingBottom: 3, marginBottom: 2, fontWeight: 500 }}>
              <span style={{ width: "9ch" }}> 类型</span>
              <span style={hs("10ch", "id")} onClick={() => ht("id")}>代码{ha("id")}</span>
              <span style={{ width: "10ch" }}>名称</span>
              <span style={hs("10ch", "price", true)} onClick={() => ht("price")}>{pad("现价" + ha("price"), 9, true)}</span>
              <span style={hs("9ch", "change", true)} onClick={() => ht("change")}>{pad("涨跌幅" + ha("change"), 8, true)}</span>
              <span style={hs("9ch", "cost", true)} onClick={() => ht("cost")}>{pad("成本" + ha("cost"), 8, true)}</span>
              <span style={{ width: "7ch", textAlign: "right" }}>{pad("股数", 6, true)}</span>
              <span style={hs("10ch", "pnl", true)} onClick={() => ht("pnl")}>{pad("盈亏%" + ha("pnl"), 9, true)}</span>
              <span style={hs("10ch", "mktVal", true)} onClick={() => ht("mktVal")}>{pad("市值" + ha("mktVal"), 9, true)}</span>
              <span style={hs("8ch", "position_pct", true)} onClick={() => ht("position_pct")}>{pad("仓位" + ha("position_pct"), 7, true)}</span>
              <span style={hs("10ch", "totalPnl", true)} onClick={() => ht("totalPnl")}>{pad("盈亏额" + ha("totalPnl"), 9, true)}</span>
              <span style={hs("9ch", "dayPnl", true)} onClick={() => ht("dayPnl")}>{pad("今日" + ha("dayPnl"), 8, true)}</span>
              <span style={hs("7ch", "volRatio", true)} onClick={() => ht("volRatio")}>{pad("量比" + ha("volRatio"), 6, true)}</span>
              <span style={hs("8ch", "turnover", true)} onClick={() => ht("turnover")}>{pad("换手%" + ha("turnover"), 7, true)}</span>
              <span style={hs("9ch", "amount", true)} onClick={() => ht("amount")}>{pad("成交额" + ha("amount"), 8, true)}</span>
              {activeTab === "HK" && <span style={hs("9ch", "mainNetInflow" as SortKey, true)} onClick={() => ht("mainNetInflow" as SortKey)}>{pad("主力" + ha("mainNetInflow" as SortKey), 8, true)}</span>}
              {activeTab === "HK" && <span style={hs("7ch", "mainNetInflowPct" as SortKey, true)} onClick={() => ht("mainNetInflowPct" as SortKey)}>{pad("主力%" + ha("mainNetInflowPct" as SortKey), 6, true)}</span>}
              <span style={{ width: "12ch", color: D.pink, marginLeft: 8 }}>标签</span>
            </div>
          );

          const secTitle = (
            label: string,
            count: number,
            open: boolean,
            toggle: (v: (prev: boolean) => boolean) => void,
            opacity = 1,
          ) => (
            <div
              style={{ color: D.comment, padding: "4px 0 1px", cursor: "pointer", userSelect: "none", opacity }}
              onClick={() => toggle((v) => !v)}
            >
              <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span>
              {" "}# ── {label} ({count}) ──
            </div>
          );

          return (
            <>
              {/* ── pinned (starred) ── */}
              {pinnedList.length > 0 && (
                <>
                  <div style={{ color: D.comment, padding: "4px 0 1px" }}>
                    <span style={{ color: D.yellow }}>★</span>
                    {" "}# ── pinned ({pinnedList.length}) ──
                  </div>
                  {holdHeader}{pinnedList.map((s) => <HoldRow key={s.id} s={s} />)}
                </>
              )}

              {/* ── prod:stocks ── */}
              {prodStock.length > 0 && (
                <>
                  {secTitle("prod:stocks", prodStock.length, prodStockOpen, setProdStockOpen)}
                  {prodStockOpen && <>{holdHeader}{prodStock.map((s) => <HoldRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── prod:ETF ── */}
              {prodETF.length > 0 && (
                <>
                  {secTitle("prod:ETF", prodETF.length, prodETFOpen, setProdETFOpen)}
                  {prodETFOpen && <>{holdHeader}{prodETF.map((s) => <HoldRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── hidden ── */}
              {hiddenList.length > 0 && (
                <>
                  {secTitle("hidden", hiddenList.length, hiddenOpen, setHiddenOpen, 0.6)}
                  {hiddenOpen && (
                    <>
                      {holdHeader}{hiddenList.map((s) => <HoldRow key={s.id} s={s} />)}
                    </>
                  )}
                </>
              )}

              {prodStock.length === 0 && prodETF.length === 0 && hiddenList.length === 0 && (
                <div style={{ color: D.comment, padding: "8px 0" }}>
                  # 当前市场无持仓
                </div>
              )}
            </>
          );
        })()}

        {/* log tail */}
        <div style={{ height: 16 }} />
        <div style={{ color: D.comment, fontSize: 12 }}>
          [{now}] <span style={{ color: D.green }}>info</span> 行情收集: 已轮询{" "}
          {services.length} 个端点 ({(tick * 7 + 23) % 50 + 15}ms)
        </div>
        <div style={{ color: D.comment, fontSize: 12 }}>
          [{now}] <span style={{ color: D.green }}>info</span> 调度器: 下次轮询{" "}
          {pollMs / 1000}秒
        </div>

        </>)}

        {/* alert log tail */}
        {logs.length > 0 && (
          <div style={{ marginTop: 8 }}>
            {[...logs].reverse().slice(0, 8).reverse().map((log, i) => (
              <div key={i} style={{ fontSize: 12, color: D.comment }}>
                <span>[{log.time}] </span>
                <span style={{ color: log.level === "error" ? D.red : log.level === "warn" ? D.yellow : D.green }}>
                  {log.level.padEnd(5)}
                </span>
                <span> {log.source}: </span>
                <span style={{ color: log.level === "error" ? D.red : log.level === "warn" ? D.orange : D.comment }}>
                  {log.message}
                </span>
              </div>
            ))}
          </div>
        )}

      </div>

      <style>{`
        @keyframes blink {
          50% { opacity: 0; }
        }
      `}</style>

      <StockDrawer
        symbol={drawerSymbol}
        services={services}
        planMap={planMap}
        allTags={allTags}
        onClose={() => setDrawerSymbol(null)}
        onRefreshPlans={refreshPlans}
        onRefreshMetrics={refresh}
      />
    </div>
  );
}
