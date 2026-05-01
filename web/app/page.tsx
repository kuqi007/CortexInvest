"use client";

import { Suspense, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { useAlerts } from "./hooks/useAlerts";
import { useLogEntries } from "./hooks/useCommand";
import { AppTabs } from "./components/AppTabs";
import { AppTitleBar } from "./components/AppTitleBar";
import { MarketSwitch, type MarketTab } from "./components/MarketSwitch";
import MarketSummaryBar from './components/MarketSummaryBar';
import { DataTrustBar } from "./components/DataTrustBar";
import { QuoteStaleBanner } from "./components/QuoteStaleBanner";
import { StockTagChips } from "./components/StockTagChips";
import { CollapsibleSectionTitle } from "./components/CollapsibleSectionTitle";
import { FetchErrorBanner } from "./components/FetchErrorBanner";
import { MetricsLoadingBlock } from "./components/MetricsLoadingBlock";
import { StockSearchToolbar } from "./components/StockSearchToolbar";
import {
  StockTableHeader,
  l2Columns,
  portfolioColumns,
  tagColumn,
  type StockTableColumn,
} from "./components/StockTableHeader";
import { TagFilterBanner } from "./components/TagFilterBanner";
import {
  stockToolbarButtonStyle,
  stockToolbarFieldStyle,
} from "./components/toolbarStyles";
import { useMetrics } from "./providers/MetricsProvider";
import { useTradePlans } from "./hooks/useTradePlans";
import { StockDrawer } from "./components/StockDrawer";

import type { Service } from "./types";
import { useTradingStatus } from "./lib/trading-hours";
import { buildPollHint, chgColor, fmtAmt, fmtMoney, pad } from "./lib/display-utils";

const DEFAULT_POLL_SEC = 30;

import { D } from "./theme";

/* ── Main ── */
export default function Page() {
  return (
    <Suspense>
      <Home />
    </Suspense>
  );
}

function Home() {
  const { services, ts, tick, loading, settings, fetchError, alertEvents, marketTurnover, hkdCnyRate, refresh, dataRuntimeHint } = useMetrics();
  const { status: tradingStatus, loading: tradingStatusLoading } = useTradingStatus();
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
          <StockTagChips
            tags={s.tags}
            maxVisible={3}
            onTagClick={(t, e) => { e.stopPropagation(); setFilterTag(t); }}
          />
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
        <span style={{ color: s.star ? D.yellow : D.comment, width: "9ch" }}>{s.star ? "★" : " "}</span>
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
          <StockTagChips
            tags={s.tags}
            maxVisible={3}
            onTagClick={(t, e) => { e.stopPropagation(); setFilterTag(t); }}
          />
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
          <MetricsLoadingBlock blink />
        )}

        {!loading && (<>
        {/* tag filter bar */}
        <TagFilterBanner tag={filterTag} onClear={() => setFilterTag(null)} />

        {/* search + add */}
        <StockSearchToolbar
          search={search}
          onSearchChange={setSearch}
          matchCount={searchFiltered.length}
          totalCount={tabServices.length}
        >
          <input
            placeholder="代码"
            value={addCode}
            onChange={(e) => setAddCode(e.target.value.toUpperCase())}
            style={stockToolbarFieldStyle(90)}
            onKeyDown={(e) => { if (e.key === "Enter") handleAdd(); }}
          />
          <input
            placeholder="成本"
            type="number"
            step="any"
            value={addCost}
            onChange={(e) => setAddCost(e.target.value)}
            style={stockToolbarFieldStyle(70)}
          />
          <input
            placeholder="股数"
            type="number"
            step={100}
            value={addShares}
            onChange={(e) => setAddShares(e.target.value)}
            style={stockToolbarFieldStyle(70)}
          />
          <button
            onClick={handleAdd}
            style={stockToolbarButtonStyle}
          >
            添加持仓
          </button>
        </StockSearchToolbar>

        <DataTrustBar
          pollMs={pollMs}
          ts={ts}
          tick={tick}
          fetchError={fetchError}
          tradingStatus={tradingStatus}
          tradingLoading={tradingStatusLoading}
          dataRuntimeHint={dataRuntimeHint}
          pollHint={<span>{buildPollHint(pollMs, "holdings")}</span>}
        />

        <QuoteStaleBanner
          pollMs={pollMs}
          ts={ts}
          fetchError={fetchError}
          tradingStatus={tradingStatus}
        />

        <FetchErrorBanner fetchError={fetchError} ts={ts} />

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
            可用:<span style={{ color: D.fg }}>{fmtMoney(avail, { sign: "negativeOnly" })}</span>
            <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
            {"  "}
            总市值:<span style={{ color: D.fg }}>{fmtMoney(tabPosition, { sign: "negativeOnly" })}</span>
            <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
            {"  "}
            总资产:<span style={{ color: D.fg }}>{fmtMoney(totalAssets, { sign: "negativeOnly" })}</span>
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
          const holdHeader = (
            <StockTableHeader
              columns={[
                ...(portfolioColumns as StockTableColumn<SortKey>[]),
                ...(activeTab === "HK" ? l2Columns<SortKey>() : []),
                tagColumn<SortKey>(),
              ]}
              sortKey={holdSort.key}
              sortAsc={holdSort.asc}
              onSort={toggleHoldSort}
            />
          );

          return (
            <>
              {/* ── 置顶 ── */}
              {pinnedList.length > 0 && (
                <>
                  <CollapsibleSectionTitle
                    label="置顶"
                    count={pinnedList.length}
                    variant="star"
                  />
                  {holdHeader}{pinnedList.map((s) => <HoldRow key={s.id} s={s} />)}
                </>
              )}

              {/* ── 持仓:股票 ── */}
              {prodStock.length > 0 && (
                <>
                  <CollapsibleSectionTitle
                    label="持仓:股票"
                    count={prodStock.length}
                    open={prodStockOpen}
                    onToggle={() => setProdStockOpen((v) => !v)}
                  />
                  {prodStockOpen && <>{holdHeader}{prodStock.map((s) => <HoldRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── 持仓:ETF ── */}
              {prodETF.length > 0 && (
                <>
                  <CollapsibleSectionTitle
                    label="持仓:ETF"
                    count={prodETF.length}
                    open={prodETFOpen}
                    onToggle={() => setProdETFOpen((v) => !v)}
                  />
                  {prodETFOpen && <>{holdHeader}{prodETF.map((s) => <HoldRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── 隐藏 ── */}
              {hiddenList.length > 0 && (
                <>
                  <CollapsibleSectionTitle
                    label="隐藏"
                    count={hiddenList.length}
                    open={hiddenOpen}
                    onToggle={() => setHiddenOpen((v) => !v)}
                    opacity={0.6}
                  />
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
