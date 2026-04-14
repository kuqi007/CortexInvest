"use client";

import { Suspense, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { useAlerts } from "../hooks/useAlerts";
import { useLogEntries } from "../hooks/useCommand";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";
import { MarketSwitch, type MarketTab } from "../components/MarketSwitch";
import MarketSummaryBar from "../components/MarketSummaryBar";
import { useMetrics } from "../providers/MetricsProvider";
import { StockDrawer } from "../components/StockDrawer";
import { useTradePlans } from "../hooks/useTradePlans";
import { useTradingStatus } from "../lib/trading-hours";
import { tagColor } from "../lib/tag-utils";
import { D } from "../theme";

import type { Service } from "../types";

const DEFAULT_POLL_SEC = 30;

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

export default function Page() {
  return (
    <Suspense>
      <StarredPage />
    </Suspense>
  );
}

function StarredPage() {
  const { services, ts, tick, loading, settings, fetchError, alertEvents, marketTurnover, refresh } = useMetrics();
  const { status: tradingStatus } = useTradingStatus();
  const searchParams = useSearchParams();

  function getDefaultTab(): MarketTab {
    const param = searchParams.get("tab")?.toUpperCase();
    if (param === "A" || param === "HK") return param;
    return new Date().getHours() < 15 ? "A" : "HK";
  }
  const [activeTab, setActiveTab] = useState<MarketTab>(getDefaultTab);

  // Section open/close state
  const [pinnedOpen, setPinnedOpen] = useState(true);
  const [stockOpen, setStockOpen] = useState(true);
  const [etfOpen, setEtfOpen] = useState(true);
  const [hiddenOpen, setHiddenOpen] = useState(false);

  // Tag filter
  const [filterTag, setFilterTag] = useState<string | null>(null);

  // Logs and alerts
  const { logs, addLogs, clearLogs } = useLogEntries();

  // Sort state
  type SortKey = keyof Service | "mktVal" | "totalPnl" | "dayPnl";
  type SortState = { key: SortKey | null; asc: boolean };
  const [sortState, setSortState] = useState<SortState>({ key: null, asc: false });

  const { planMap, refresh: refreshPlans } = useTradePlans();
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);

  function switchTab(tab: MarketTab) {
    setActiveTab(tab);
    clearLogs();
    window.history.replaceState(null, "", `/starred?tab=${tab}`);
  }

  // Filter alerts by market
  const tabAlertEvents = alertEvents.filter(
    (e) => !e.symbol || (activeTab === "HK" ? e.symbol.startsWith("HK") : !e.symbol.startsWith("HK"))
  );
  useAlerts(tabAlertEvents, addLogs, activeTab);

  const pollMs = (settings.poll_interval ?? DEFAULT_POLL_SEC) * 1000;

  function toggleSort(key: SortKey) {
    setSortState((prev) =>
      prev.key === key ? { key, asc: !prev.asc } : { key, asc: false }
    );
  }

  // P&L calculation
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

  function applySort(list: Service[], st: SortState): Service[] {
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

  // Market helpers
  const isHK = (s: Service) => s.id.startsWith("HK");
  const isETF = (s: Service) => !isHK(s) && /^(51|15|58)\d{4}$/.test(s.id);
  const inTab = (s: Service) => activeTab === "HK" ? isHK(s) : !isHK(s);

  // L2 data only for HK
  const showL2 = activeTab === "HK";

  // Filter: starred + inTab + tag filter
  const tabStarred = useMemo(() => {
    return services.filter((s) => s.star && inTab(s));
  }, [services, activeTab]);

  const tagFiltered = useMemo(() => filterTag
    ? tabStarred.filter((s) => s.tags?.includes(filterTag))
    : tabStarred, [tabStarred, filterTag]);

  // Sections: pinned (holdings) -> stocks -> ETFs -> hidden
  const pinnedList = useMemo(() => applySort(tagFiltered.filter((s) => s.type === "holding" && !s.hidden), sortState), [tagFiltered, sortState]);
  const pinnedIds = useMemo(() => new Set(pinnedList.map((s) => s.id)), [pinnedList]);
  const stockList = useMemo(() => applySort(tagFiltered.filter((s) => s.type === "watching" && !s.hidden && !isETF(s)), sortState), [tagFiltered, sortState]);
  const etfList = useMemo(() => applySort(tagFiltered.filter((s) => s.type === "watching" && !s.hidden && isETF(s)), sortState), [tagFiltered, sortState]);
  const hiddenList = useMemo(() => applySort(tagFiltered.filter((s) => s.hidden), sortState), [tagFiltered, sortState]);

  const allTags = useMemo(
    () => [...new Set(services.flatMap((s) => s.tags ?? []))],
    [services]
  );

  const now = ts
    ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })
    : "--:--:--";
  const isStale = (ts > 0 && Date.now() - ts > pollMs * 3) || fetchError !== null;

  // Stats
  const starredCount = tagFiltered.length;
  const starredUp = tagFiltered.filter((s) => s.change > 0).length;
  const starredDn = tagFiltered.filter((s) => s.change < 0).length;

  // Holdings stats
  const holdings = tagFiltered.filter((s) => s.type === "holding" && s.pnl !== null && s.cost && s.shares);
  const totalPnl = holdings.reduce((sum, s) => sum + (s.price - s.cost!) * s.shares!, 0);
  const todayPnl = holdings.reduce((sum, s) => sum + calcDayPnl(s, 1), 0);
  const position = holdings.reduce((sum, s) => sum + s.price * s.shares!, 0);
  const costBasis = holdings.reduce((sum, s) => sum + s.cost! * s.shares!, 0);
  const returnPct = costBasis > 0 ? (totalPnl / costBasis) * 100 : 0;

  // Sort header helpers
  const mkArrow = (k: SortKey) => sortState.key === k ? (sortState.asc ? " ▲" : " ▼") : "";
  const mkHStyle = (w: string, k: SortKey | null, right = false): React.CSSProperties => ({
    width: w,
    textAlign: right ? "right" : "left",
    cursor: k ? "pointer" : "default",
    userSelect: "none",
    color: k && sortState.key === k ? D.yellow : D.pink,
  });

  // Tag chip style
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

  // Section title with collapse (中文与 holdings/watching 一致)
  const secTitle = (label: string, count: number, open: boolean, setOpen: (v: boolean | ((prev: boolean) => boolean)) => void, opacity = 1) => (
    <div
      style={{ color: D.comment, padding: "4px 0 1px", cursor: "pointer", userSelect: "none", opacity }}
      onClick={() => setOpen((v) => !v)}
    >
      <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span>
      {" "}# ── {label} ({count}) ──
    </div>
  );

  // Starred Row - 字段与 holdings 保持一致（中文表头）
  function StarredRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const pnlPctStr = s.pnl !== null ? `${s.pnl >= 0 ? "+" : ""}${s.pnl.toFixed(1)}%` : "-";
    const mktVal = s.shares != null ? s.price * s.shares : null;
    const totalPnlRaw = s.cost != null && s.cost !== 0 && s.shares != null ? (s.price - s.cost) * s.shares : null;
    const dayPnl = s.shares != null ? calcDayPnl(s, 1) : null;
    const isHolding = s.type === "holding";

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
        {/* 标记列: ★ + 计划 + 洗盘 */}
        <span style={{ width: "9ch", display: "inline-flex", gap: 2, alignItems: "center" }}>
          <span style={{
            border: `1px solid ${D.yellow}`, color: D.yellow,
            fontSize: 10, padding: "0 2px", lineHeight: "1.4",
            fontFamily: "JetBrains Mono, monospace",
          }}>★</span>
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
        {/* 代码 */}
        <span style={{ color: isHolding ? D.orange : D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
        {/* 名称 */}
        <span style={{ color: D.fg, width: "10ch" }}>{pad((s.alias || s.name).slice(0, 6), 8)}</span>
        {/* 现价 */}
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(s.price.toFixed(2), 9, true)}
        </span>
        {/* 涨跌幅 */}
        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}
        </span>
        {/* 成本 */}
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(s.cost != null ? s.cost.toFixed(2) : "-", 8, true)}
        </span>
        {/* 股数 */}
        <span style={{ color: D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.shares != null ? String(s.shares) : "-", 6, true)}
        </span>
        {/* 盈亏% */}
        <span style={{ color: s.pnl !== null ? chgColor(s.pnl) : D.comment, width: "10ch", textAlign: "right", fontWeight: 500 }}>
          {pad(pnlPctStr, 9, true)}
        </span>
        {/* 市值 */}
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(mktVal != null ? fmtAmt(mktVal) : "-", 9, true)}
        </span>
        {/* 盈亏额 */}
        <span style={{ color: totalPnlRaw !== null ? chgColor(totalPnlRaw) : D.comment, width: "10ch", textAlign: "right", fontWeight: 500 }}>
          {pad(totalPnlRaw !== null ? fmtMoney(totalPnlRaw) : "-", 9, true)}
        </span>
        {/* 今日 */}
        <span style={{ color: dayPnl != null ? chgColor(dayPnl) : D.comment, width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(dayPnl != null ? fmtMoney(dayPnl) : "-", 8, true)}
        </span>
        {/* 量比 */}
        <span style={{ color: s.volRatio >= 1.5 ? D.red : s.volRatio <= 0.5 ? D.comment : D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.volRatio > 0 ? s.volRatio.toFixed(2) : "-", 6, true)}
        </span>
        {/* 换手% */}
        <span style={{ color: s.turnover >= 5 ? D.red : D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.turnover > 0 ? s.turnover.toFixed(2) : "-", 7, true)}
        </span>
        {/* 成交额 */}
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(fmtAmt(s.amount), 8, true)}
        </span>
        {/* 主力 (仅港股) */}
        {showL2 && (
          <span style={{ color: s.mainNetInflow != null ? chgColor(s.mainNetInflow) : D.comment, width: "9ch", textAlign: "right" }}>
            {pad(s.mainNetInflow != null ? fmtAmt(s.mainNetInflow) : "-", 8, true)}
          </span>
        )}
        {/* 主力% (仅港股) */}
        {showL2 && (
          <span style={{ color: s.mainNetInflowPct != null ? chgColor(s.mainNetInflowPct) : D.comment, width: "7ch", textAlign: "right" }}>
            {s.mainNetInflowPct != null ? `${s.mainNetInflowPct >= 0 ? "+" : ""}${s.mainNetInflowPct.toFixed(1)}%` : pad("-", 6, true)}
          </span>
        )}
        {/* 标签 */}
        <span style={{ width: "12ch", overflow: "hidden", whiteSpace: "nowrap", marginLeft: 8 }}>
          {(s.tags ?? []).map((t) => (
            <span key={t} style={tagChipStyle(t)} onClick={(e) => { e.stopPropagation(); setFilterTag(t); }}>{t}</span>
          ))}
        </span>
      </div>
    );
  }

  // 表头与 holdings 保持一致（中文）
  const header = (
    <div style={{ display: "flex", whiteSpace: "pre", color: D.pink, borderBottom: `1px solid ${D.currentLine}`, paddingBottom: 3, marginBottom: 2, fontWeight: 500 }}>
      <span style={{ width: "9ch" }}> 标记</span>
      <span style={mkHStyle("10ch", "id")} onClick={() => toggleSort("id")}>代码{mkArrow("id")}</span>
      <span style={{ width: "10ch" }}>名称</span>
      <span style={mkHStyle("10ch", "price", true)} onClick={() => toggleSort("price")}>{pad("现价" + mkArrow("price"), 9, true)}</span>
      <span style={mkHStyle("9ch", "change", true)} onClick={() => toggleSort("change")}>{pad("涨跌幅" + mkArrow("change"), 8, true)}</span>
      <span style={mkHStyle("9ch", "cost", true)} onClick={() => toggleSort("cost")}>{pad("成本" + mkArrow("cost"), 8, true)}</span>
      <span style={{ width: "7ch", textAlign: "right" }}>{pad("股数", 6, true)}</span>
      <span style={mkHStyle("10ch", "pnl", true)} onClick={() => toggleSort("pnl")}>{pad("盈亏%" + mkArrow("pnl"), 9, true)}</span>
      <span style={mkHStyle("10ch", "mktVal", true)} onClick={() => toggleSort("mktVal")}>{pad("市值" + mkArrow("mktVal"), 9, true)}</span>
      <span style={mkHStyle("10ch", "totalPnl", true)} onClick={() => toggleSort("totalPnl")}>{pad("盈亏额" + mkArrow("totalPnl"), 9, true)}</span>
      <span style={mkHStyle("9ch", "dayPnl", true)} onClick={() => toggleSort("dayPnl")}>{pad("今日" + mkArrow("dayPnl"), 8, true)}</span>
      <span style={mkHStyle("7ch", "volRatio", true)} onClick={() => toggleSort("volRatio")}>{pad("量比" + mkArrow("volRatio"), 6, true)}</span>
      <span style={mkHStyle("8ch", "turnover", true)} onClick={() => toggleSort("turnover")}>{pad("换手%" + mkArrow("turnover"), 7, true)}</span>
      <span style={mkHStyle("9ch", "amount", true)} onClick={() => toggleSort("amount")}>{pad("成交额" + mkArrow("amount"), 8, true)}</span>
      {showL2 && <span style={mkHStyle("9ch", "mainNetInflow" as SortKey, true)} onClick={() => toggleSort("mainNetInflow" as SortKey)}>{pad("主力" + mkArrow("mainNetInflow" as SortKey), 8, true)}</span>}
      {showL2 && <span style={mkHStyle("7ch", "mainNetInflowPct" as SortKey, true)} onClick={() => toggleSort("mainNetInflowPct" as SortKey)}>{pad("主力%" + mkArrow("mainNetInflowPct" as SortKey), 6, true)}</span>}
      <span style={{ width: "12ch", color: D.pink, marginLeft: 8 }}>标签</span>
    </div>
  );

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: D.bg }}>
      <AppTitleBar title="starred" />
      <AppTabs active="starred" />

      <div style={{ flex: 1, padding: "10px 16px", overflow: "auto", fontSize: 13, lineHeight: 1.55 }}>
        <div style={{ height: 8 }} />

        {/* Market Switch */}
        <MarketSwitch activeTab={activeTab} onTabChange={switchTab} />
        
        {/* Market Summary Bar */}
        <div style={{ marginBottom: 6 }}>
          <MarketSummaryBar marketTurnover={marketTurnover ?? {} as any} market={activeTab === 'A' ? 'A' : 'HK'} />
        </div>

        {loading && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.green }}>info</span> 加载中...
          </div>
        )}

        {!loading && (
          <>
            {/* 标签筛选栏 */}
            {filterTag && (
              <div style={{ padding: "4px 8px", backgroundColor: "#44475a", color: D.fg, fontSize: 12, marginBottom: 4, display: "flex", alignItems: "center", gap: 8, borderRadius: 3 }}>
                <span>筛选: <span style={{ color: tagColor(filterTag), fontWeight: 500 }}>{filterTag}</span></span>
                <span style={{ cursor: "pointer", color: D.red, fontWeight: 500 }} onClick={() => setFilterTag(null)}>x</span>
              </div>
            )}

            {/* 状态行 */}
            <div style={{ color: D.comment, marginBottom: 6 }}>
              <span>Every {pollMs / 1000}.0s: svc-monitor --starred</span>
              <span style={{ float: "right" }}>
                {isStale && <span style={{ color: D.red, fontWeight: 500, marginRight: 8 }}>STALE</span>}
                {tradingStatus?.trading ? "交易中" : "休市"}
                {" | "}
                devbox: <span style={{ color: isStale ? D.red : D.comment }}>{now}</span> &nbsp; refresh #{tick}
              </span>
            </div>

            {fetchError && (
              <div style={{ color: D.red, marginBottom: 6, fontWeight: 500 }}>
                [ERROR] metrics fetch failed: {fetchError}
              </div>
            )}

            {/* 统计 */}
            <div style={{ color: D.comment, marginBottom: 6 }}>
              <span>Nodes: <span style={{ color: D.purple }}>{starredCount}</span></span>
              {"  "}
              上涨:<span style={{ color: D.red }}>{starredUp}</span>
              {"  "}
              下跌:<span style={{ color: D.green }}>{starredDn}</span>
              {holdings.length > 0 && (
                <>
                  {"  "}
                  持仓:<span style={{ color: D.fg }}>{holdings.length}</span>
                  {"  "}
                  仓位:<span style={{ color: D.fg }}>{fmtMoney(position).replace("+", "")}</span>
                  <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
                  {"  "}
                  收益:<span style={{ color: chgColor(totalPnl) }}>{fmtMoney(totalPnl)}</span>
                  {"  "}
                  收益率:<span style={{ color: chgColor(returnPct) }}>{returnPct >= 0 ? "+" : ""}{returnPct.toFixed(1)}%</span>
                  {"  "}
                  今日:<span style={{ color: chgColor(todayPnl) }}>{fmtMoney(todayPnl)}</span>
                </>
              )}
            </div>

            {starredCount === 0 ? (
              <div style={{ color: D.comment, padding: "16px 0" }}>
                # 暂无特别关注的股票，在 holdings 或 watching 页面点击 ☆ 添加
              </div>
            ) : (
              <>
                {/* Pinned Holdings */}
                {pinnedList.length > 0 && (
                  <>
                    {secTitle("pinned:holdings", pinnedList.length, pinnedOpen, setPinnedOpen)}
                    {pinnedOpen && <>{header}{pinnedList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}

                {/* Watchlist Stocks */}
                {stockList.length > 0 && (
                  <>
                    {secTitle("watchlist:stocks", stockList.length, stockOpen, setStockOpen)}
                    {stockOpen && <>{header}{stockList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}

                {/* Watchlist ETFs */}
                {etfList.length > 0 && (
                  <>
                    {secTitle("watchlist:ETFs", etfList.length, etfOpen, setEtfOpen)}
                    {etfOpen && <>{header}{etfList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}

                {/* Hidden */}
                {hiddenList.length > 0 && (
                  <>
                    {secTitle("hidden", hiddenList.length, hiddenOpen, setHiddenOpen, 0.6)}
                    {hiddenOpen && <>{header}{hiddenList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}
              </>
            )}

            {/* Alert logs */}
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
          </>
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
