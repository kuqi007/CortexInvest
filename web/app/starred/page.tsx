"use client";

import { Suspense, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { useAlerts } from "../hooks/useAlerts";
import { useLogEntries } from "../hooks/useCommand";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";
import { MarketSwitch, type MarketTab } from "../components/MarketSwitch";
import MarketSummaryBar from "../components/MarketSummaryBar";
import { DataTrustBar } from "../components/DataTrustBar";
import { QuoteStaleBanner } from "../components/QuoteStaleBanner";
import { StockTagChips } from "../components/StockTagChips";
import { CollapsibleSectionTitle } from "../components/CollapsibleSectionTitle";
import { FetchErrorBanner } from "../components/FetchErrorBanner";
import { MetricsLoadingBlock } from "../components/MetricsLoadingBlock";
import { StockSearchToolbar } from "../components/StockSearchToolbar";
import {
  StockTableHeader,
  l2Columns,
  portfolioColumns,
  tagColumn,
  type StockTableColumn,
} from "../components/StockTableHeader";
import { TagFilterBanner } from "../components/TagFilterBanner";
import {
  stockToolbarButtonStyle,
  stockToolbarFieldStyle,
  stockToolbarSelectStyle,
} from "../components/toolbarStyles";
import { useMetrics } from "../providers/MetricsProvider";
import { StockDrawer } from "../components/StockDrawer";
import { useTradePlans } from "../hooks/useTradePlans";
import { useTradingStatus } from "../lib/trading-hours";
import { buildPollHint, chgColor, fmtAmt, fmtMoney, pad } from "../lib/display-utils";
import { D } from "../theme";

import type { Service } from "../types";

const DEFAULT_POLL_SEC = 30;

export default function Page() {
  return (
    <Suspense>
      <StarredPage />
    </Suspense>
  );
}

function StarredPage() {
  const { services, ts, tick, loading, settings, fetchError, alertEvents, marketTurnover, refresh, dataRuntimeHint } = useMetrics();
  const { status: tradingStatus, loading: tradingStatusLoading } = useTradingStatus();
  const searchParams = useSearchParams();

  function getDefaultTab(): MarketTab {
    const param = searchParams.get("tab")?.toUpperCase();
    if (param === "A" || param === "HK") return param;
    return new Date().getHours() < 15 ? "A" : "HK";
  }
  const [activeTab, setActiveTab] = useState<MarketTab>(getDefaultTab);

  // Section open/close state
  const [pinnedOpen, setPinnedOpen] = useState(true);
  const [holdOpen, setHoldOpen] = useState(true);
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
  const [search, setSearch] = useState("");
  const [addCode, setAddCode] = useState("");
  const [addType, setAddType] = useState<"watching" | "holding">("watching");
  const [addCost, setAddCost] = useState("");
  const [addShares, setAddShares] = useState("");

  async function handleAdd() {
    const code = addCode.trim();
    if (!code) return;
    if (!/^(HK\d{5,6}|\d{6})$/.test(code)) { alert("股票代码格式错误\n港股: HK00700  A股: 600519"); return; }
    const data: Record<string, unknown> = { star: true, type: addType };
    if (addType === "holding") {
      if (addCost) data.cost = Number(addCost);
      if (addShares) data.shares = Number(addShares);
    }
    try {
      const resp = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "add", code, data }),
      });
      const result = await resp.json();
      if (result?.success) {
        setAddCode(""); setAddCost(""); setAddShares(""); setAddType("watching");
        refresh();
      }
    } catch (e) {
      console.error("添加失败:", e);
      alert("添加失败，请检查网络或代码格式");
    }
  }

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
  const searchFiltered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tagFiltered;
    return tagFiltered.filter((s) =>
      s.id.toLowerCase().includes(q) || (s.alias || s.name).toLowerCase().includes(q)
    );
  }, [tagFiltered, search]);

  // Sections: 置顶(pin_order>0) -> holdings -> stocks -> ETFs -> hidden
  const pinnedList = useMemo(() => applySort(searchFiltered.filter((s) => !s.hidden && (s.pin_order ?? 0) > 0), sortState), [searchFiltered, sortState]);
  const pinnedIds = useMemo(() => new Set(pinnedList.map((s) => s.id)), [pinnedList]);
  const holdList = useMemo(() => applySort(searchFiltered.filter((s) => s.type === "holding" && !s.hidden && !pinnedIds.has(s.id)), sortState), [searchFiltered, sortState, pinnedIds]);
  const stockList = useMemo(() => applySort(searchFiltered.filter((s) => s.type === "watching" && !s.hidden && !isETF(s) && !pinnedIds.has(s.id)), sortState), [searchFiltered, sortState, pinnedIds]);
  const etfList = useMemo(() => applySort(searchFiltered.filter((s) => s.type === "watching" && !s.hidden && isETF(s) && !pinnedIds.has(s.id)), sortState), [searchFiltered, sortState, pinnedIds]);
  const hiddenList = useMemo(() => applySort(searchFiltered.filter((s) => s.hidden), sortState), [searchFiltered, sortState]);

  const allTags = useMemo(
    () => [...new Set(services.flatMap((s) => s.tags ?? []))],
    [services]
  );

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
        {/* 仓位 */}
        <span style={{ color: D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.position_pct != null ? `${(s.position_pct * 100).toFixed(1)}%` : "-", 7, true)}
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
          <StockTagChips
            tags={s.tags}
            maxVisible={3}
            onTagClick={(t, e) => { e.stopPropagation(); setFilterTag(t); }}
          />
        </span>
      </div>
    );
  }

  // 表头与 holdings 保持一致（中文）
  const header = (
    <StockTableHeader
      columns={[
        ...(portfolioColumns as StockTableColumn<SortKey>[]),
        ...(showL2 ? l2Columns<SortKey>() : []),
        tagColumn<SortKey>(),
      ]}
      sortKey={sortState.key}
      sortAsc={sortState.asc}
      onSort={toggleSort}
    />
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
        {marketTurnover && (
          <div style={{ marginBottom: 6 }}>
            <MarketSummaryBar marketTurnover={marketTurnover} market={activeTab === 'A' ? 'A' : 'HK'} />
          </div>
        )}

        {loading && (
          <MetricsLoadingBlock />
        )}

        {!loading && (
          <>
            {/* 标签筛选栏 */}
            <TagFilterBanner tag={filterTag} onClear={() => setFilterTag(null)} />

            {/* search + add */}
            <StockSearchToolbar
              search={search}
              onSearchChange={setSearch}
              matchCount={searchFiltered.length}
              totalCount={tabStarred.length}
            >
              <input
                placeholder="代码"
                value={addCode}
                onChange={(e) => setAddCode(e.target.value.toUpperCase())}
                style={stockToolbarFieldStyle(90)}
                onKeyDown={(e) => { if (e.key === "Enter") handleAdd(); }}
              />
              <select
                value={addType}
                onChange={(e) => setAddType(e.target.value as "watching" | "holding")}
                style={stockToolbarSelectStyle}
              >
                <option value="watching">自选</option>
                <option value="holding">持仓</option>
              </select>
              <input
                placeholder="成本"
                type="number"
                step="any"
                value={addCost}
                onChange={(e) => setAddCost(e.target.value)}
                disabled={addType !== "holding"}
                style={stockToolbarFieldStyle(70, addType !== "holding")}
              />
              <input
                placeholder="股数"
                type="number"
                step={100}
                value={addShares}
                onChange={(e) => setAddShares(e.target.value)}
                disabled={addType !== "holding"}
                style={stockToolbarFieldStyle(70, addType !== "holding")}
              />
              <button
                onClick={handleAdd}
                style={stockToolbarButtonStyle}
              >
                添加关注
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
              pollHint={<span>{buildPollHint(pollMs, "starred")}</span>}
            />

            <QuoteStaleBanner
              pollMs={pollMs}
              ts={ts}
              fetchError={fetchError}
              tradingStatus={tradingStatus}
            />

            <FetchErrorBanner fetchError={fetchError} ts={ts} />

            {/* 统计 */}
            <div style={{ color: D.comment, marginBottom: 6 }}>
              <span>节点:<span style={{ color: D.purple }}>{starredCount}</span></span>
              {"  "}
              涨:<span style={{ color: D.red }}>{starredUp}</span>
              {"  "}
              跌:<span style={{ color: D.green }}>{starredDn}</span>
              {holdings.length > 0 && (
                <>
                  {"  "}
                  持仓:<span style={{ color: D.fg }}>{holdings.length}</span>
                  {"  "}
                  总市值:<span style={{ color: D.fg }}>{fmtMoney(position, { sign: "negativeOnly" })}</span>
                  <span style={{ color: D.comment }}>{activeTab === "HK" ? "HK$" : "¥"}</span>
                  {"  "}
                  盈亏:<span style={{ color: chgColor(totalPnl) }}>{fmtMoney(totalPnl)}</span>
                  {"  "}
                  收益率:<span style={{ color: chgColor(returnPct) }}>{returnPct >= 0 ? "+" : ""}{returnPct.toFixed(1)}%</span>
                  {"  "}
                  今日:<span style={{ color: chgColor(todayPnl) }}>{fmtMoney(todayPnl)}</span>
                </>
              )}
            </div>

            {starredCount === 0 ? (
              <div style={{ color: D.comment, padding: "16px 0" }}>
                # 暂无特别关注的股票，在持仓或自选页面点击 ☆ 添加
              </div>
            ) : (
              <>
                {/* 置顶 */}
                {pinnedList.length > 0 && (
                  <>
                    <CollapsibleSectionTitle
                      label="置顶"
                      count={pinnedList.length}
                      open={pinnedOpen}
                      onToggle={() => setPinnedOpen((v) => !v)}
                    />
                    {pinnedOpen && <>{header}{pinnedList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}

                {/* Holdings */}
                {holdList.length > 0 && (
                  <>
                    <CollapsibleSectionTitle
                      label="持仓"
                      count={holdList.length}
                      open={holdOpen}
                      onToggle={() => setHoldOpen((v) => !v)}
                    />
                    {holdOpen && <>{header}{holdList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}

                {/* Watchlist Stocks */}
                {stockList.length > 0 && (
                  <>
                    <CollapsibleSectionTitle
                      label="自选:股票"
                      count={stockList.length}
                      open={stockOpen}
                      onToggle={() => setStockOpen((v) => !v)}
                    />
                    {stockOpen && <>{header}{stockList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}

                {/* Watchlist ETFs */}
                {etfList.length > 0 && (
                  <>
                    <CollapsibleSectionTitle
                      label="自选:ETF"
                      count={etfList.length}
                      open={etfOpen}
                      onToggle={() => setEtfOpen((v) => !v)}
                    />
                    {etfOpen && <>{header}{etfList.map((s) => <StarredRow key={s.id} s={s} />)}</>}
                  </>
                )}

                {/* Hidden */}
                {hiddenList.length > 0 && (
                  <>
                    <CollapsibleSectionTitle
                      label="隐藏"
                      count={hiddenList.length}
                      open={hiddenOpen}
                      onToggle={() => setHiddenOpen((v) => !v)}
                      opacity={0.6}
                    />
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
