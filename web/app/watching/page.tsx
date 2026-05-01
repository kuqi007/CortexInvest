"use client";

import { Suspense, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { useAlerts } from "../hooks/useAlerts";
import { useLogEntries } from "../hooks/useCommand";
import { useTradePlans } from "../hooks/useTradePlans";
import type { Service } from "../types";
import { useTradingStatus } from "../lib/trading-hours";
import { D } from "../theme";
import { StockDrawer } from "../components/StockDrawer";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";
import { MarketSwitch, type MarketTab } from "../components/MarketSwitch";
import MarketSummaryBar from "../components/MarketSummaryBar";
import { DataTrustBar } from "../components/DataTrustBar";
import { QuoteStaleBanner } from "../components/QuoteStaleBanner";
import { StockTagChips } from "../components/StockTagChips";
import { useMetrics } from "../providers/MetricsProvider";
import { CollapsibleSectionTitle } from "../components/CollapsibleSectionTitle";
import { FetchErrorBanner } from "../components/FetchErrorBanner";
import { MetricsLoadingBlock } from "../components/MetricsLoadingBlock";
import { StockSearchToolbar } from "../components/StockSearchToolbar";
import {
  StockTableHeader,
  l2Columns,
  tagColumn,
  watchingColumns,
  type StockTableColumn,
} from "../components/StockTableHeader";
import { TagFilterBanner } from "../components/TagFilterBanner";
import {
  stockToolbarButtonStyle,
  stockToolbarFieldStyle,
} from "../components/toolbarStyles";
import { buildPollHint, chgColor, fmtAmt, pad } from "../lib/display-utils";

const DEFAULT_POLL_SEC = 30;

export default function WatchingPage() {
  return (
    <Suspense>
      <WatchingContent />
    </Suspense>
  );
}

function WatchingContent() {
  const { services, ts, tick, loading, settings, fetchError, alertEvents, refresh, marketTurnover, dataRuntimeHint } = useMetrics();
  const { status: tradingStatus, loading: tradingStatusLoading } = useTradingStatus();
  const searchParams = useSearchParams();

  function getDefaultTab(): MarketTab {
    const param = searchParams.get("tab")?.toUpperCase();
    if (param === "A" || param === "HK") return param;
    return new Date().getHours() < 15 ? "A" : "HK";
  }
  const [activeTab, setActiveTab] = useState<MarketTab>(getDefaultTab);
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [watchStockOpen, setWatchStockOpen] = useState(true);
  const [watchETFOpen, setWatchETFOpen] = useState(true);
  const [hiddenOpen, setHiddenOpen] = useState(false);
  type SortKey = keyof Service;
  type SortState = { key: SortKey | null; asc: boolean };
  const [watchSort, setWatchSort] = useState<SortState>({ key: null, asc: false });
  const { logs, addLogs, clearLogs } = useLogEntries();
  const { planMap, refresh: refreshPlans } = useTradePlans();
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [addCode, setAddCode] = useState("");

  async function handleAdd() {
    const code = addCode.trim();
    if (!code) return;
    if (!/^(HK\d{5,6}|\d{6})$/.test(code)) { alert("股票代码格式错误\n港股: HK00700  A股: 600519"); return; }
    try {
      const resp = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "add", code, data: { type: "watching" } }),
      });
      const result = await resp.json();
      if (result?.success) {
        setAddCode("");
        refresh();
      }
    } catch { /* ignore */ }
  }

  const allTags = useMemo(
    () => [...new Set(services.flatMap((s) => s.tags ?? []))],
    [services]
  );

  const pollMs = (settings.poll_interval ?? DEFAULT_POLL_SEC) * 1000;

  function switchTab(tab: MarketTab) {
    setActiveTab(tab);
    clearLogs();
    window.history.replaceState(null, "", `/watching?tab=${tab}`);
  }

  const tabAlertEvents = alertEvents.filter(
    (e) => !e.symbol || (activeTab === "HK" ? e.symbol.startsWith("HK") : !e.symbol.startsWith("HK"))
  );
  useAlerts(tabAlertEvents, addLogs, activeTab);

  const showL2 = activeTab === "HK";
  const isHK = (s: Service) => s.id.startsWith("HK");
  const isETF = (s: Service) => !isHK(s) && /^(51|15|58)\d{4}$/.test(s.id);
  const inTab = (s: Service) => activeTab === "HK" ? isHK(s) : !isHK(s);
  function toggleWatchSort(key: SortKey) {
    setWatchSort((prev) =>
      prev.key === key ? { key, asc: !prev.asc } : { key, asc: false }
    );
  }
  function derivedVal(s: Service, key: SortKey): number {
    return (s[key] as number | null | undefined) ?? -Infinity;
  }
  function applySortList(list: Service[]): Service[] {
    const pinned = (s: Service) => s.pin_order ?? 0;
    if (!watchSort.key) return list.slice().sort((a, b) => {
      const pp = pinned(b) - pinned(a);
      if (pp !== 0) return pp;
      return b.change - a.change;
    });
    return list.slice().sort((a, b) => {
      const pp = pinned(b) - pinned(a);
      if (pp !== 0) return pp;
      const av = derivedVal(a, watchSort.key!);
      const bv = derivedVal(b, watchSort.key!);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return watchSort.asc ? cmp : -cmp;
    });
  }
  const tabServices = useMemo(() => services.filter((s) => inTab(s) && s.type !== "holding"), [services, activeTab]);
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
  const pinnedList = useMemo(() => applySortList(searchFiltered.filter((s) => !s.hidden && (s.star || (s.pin_order ?? 0) > 0))), [searchFiltered, watchSort]);
  const pinnedIds = useMemo(() => new Set(pinnedList.map((s) => s.id)), [pinnedList]);
  const watchStock = useMemo(() => applySortList(searchFiltered.filter((s) => !s.hidden && !isETF(s) && !pinnedIds.has(s.id))), [searchFiltered, watchSort, pinnedIds]);
  const watchETF = useMemo(() => applySortList(searchFiltered.filter((s) => !s.hidden && isETF(s) && !pinnedIds.has(s.id))), [searchFiltered, watchSort, pinnedIds]);
  const hiddenList = useMemo(() => applySortList(searchFiltered.filter((s) => s.hidden)), [searchFiltered, watchSort]);

  function WatchRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const csign = s.chgAmt > 0 ? "+" : "";
    return (
      <div
        style={{ display: "flex", whiteSpace: "pre", padding: "1px 0", borderBottom: `1px solid #191a21`, cursor: "pointer" }}
        onClick={() => setDrawerSymbol(s.id)}
        title="点击查看详情"
      >
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
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>{pad(s.price.toFixed(2), 9, true)}</span>
        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>{pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}</span>
        <span style={{ color: chgColor(s.chgAmt), width: "8ch", textAlign: "right" }}>{pad(`${csign}${s.chgAmt.toFixed(2)}`, 7, true)}</span>
        <span style={{ color: s.volRatio >= 1.5 ? D.red : s.volRatio <= 0.5 ? D.comment : D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.volRatio > 0 ? s.volRatio.toFixed(2) : "-", 6, true)}
        </span>
        <span style={{ color: s.turnover >= 5 ? D.red : D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.turnover > 0 ? s.turnover.toFixed(2) : "-", 7, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>{pad(fmtAmt(s.amount), 8, true)}</span>
        {showL2 && (
          <span style={{ color: s.mainNetInflow != null ? chgColor(s.mainNetInflow) : D.comment, width: "9ch", textAlign: "right" }}>
            {pad(s.mainNetInflow != null ? fmtAmt(s.mainNetInflow) : "-", 8, true)}
          </span>
        )}
        {showL2 && (
          <span style={{ color: s.mainNetInflowPct != null ? chgColor(s.mainNetInflowPct) : D.comment, width: "7ch", textAlign: "right" }}>
            {s.mainNetInflowPct != null ? `${s.mainNetInflowPct >= 0 ? "+" : ""}${s.mainNetInflowPct.toFixed(1)}%` : pad("-", 6, true)}
          </span>
        )}
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

  const header = (
    <StockTableHeader
      columns={[
        ...(watchingColumns as StockTableColumn<SortKey>[]),
        ...(showL2 ? l2Columns<SortKey>() : []),
        tagColumn<SortKey>(),
      ]}
      sortKey={watchSort.key}
      sortAsc={watchSort.asc}
      onSort={toggleWatchSort}
    />
  );

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: D.bg }}>
      <AppTitleBar title="watching" />
      <AppTabs active="watching" />
      <div style={{ flex: 1, padding: "10px 16px", overflow: "auto", fontSize: 13, lineHeight: 1.55 }}>
        <div style={{ height: 8 }} />
        <MarketSwitch activeTab={activeTab} onTabChange={switchTab} />
        {/* 大盘摘要区 */}
        <div style={{ marginBottom: 6 }}>
          <MarketSummaryBar marketTurnover={marketTurnover ?? {} as any} market={activeTab === 'A' ? 'A' : 'HK'} />
        </div>

        {loading && (
          <MetricsLoadingBlock />
        )}

        {!loading && (
          <>
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
              <button
                onClick={handleAdd}
                style={stockToolbarButtonStyle}
              >
                添加自选
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
              pollHint={<span>{buildPollHint(pollMs, "watching")}</span>}
            />
            <QuoteStaleBanner
              pollMs={pollMs}
              ts={ts}
              fetchError={fetchError}
              tradingStatus={tradingStatus}
            />
            <FetchErrorBanner fetchError={fetchError} ts={ts} />

            <div style={{ color: D.comment, marginBottom: 6 }}>
              节点: <span style={{ color: D.purple }}>{tabServices.length}</span>{"  "}
              可见:<span style={{ color: D.fg }}>{watchStock.length + watchETF.length}</span>{"  "}
              隐藏:<span style={{ color: D.comment }}>{hiddenList.length}</span>
            </div>

            {pinnedList.length > 0 && (
              <>
                <CollapsibleSectionTitle
                  label="置顶"
                  count={pinnedList.length}
                  variant="star"
                />
                {header}{pinnedList.map((s) => <WatchRow key={s.id} s={s} />)}
              </>
            )}

            {watchStock.length > 0 && (
              <>
                <CollapsibleSectionTitle
                  label="自选:股票"
                  count={watchStock.length}
                  open={watchStockOpen}
                  onToggle={() => setWatchStockOpen((v) => !v)}
                />
                {watchStockOpen && <>{header}{watchStock.map((s) => <WatchRow key={s.id} s={s} />)}</>}
              </>
            )}

            {watchETF.length > 0 && (
              <>
                <CollapsibleSectionTitle
                  label="自选:ETF"
                  count={watchETF.length}
                  open={watchETFOpen}
                  onToggle={() => setWatchETFOpen((v) => !v)}
                />
                {watchETFOpen && <>{header}{watchETF.map((s) => <WatchRow key={s.id} s={s} />)}</>}
              </>
            )}

            {hiddenList.length > 0 && (
              <>
                <CollapsibleSectionTitle
                  label="隐藏"
                  count={hiddenList.length}
                  open={hiddenOpen}
                  onToggle={() => setHiddenOpen((v) => !v)}
                  opacity={0.6}
                />
                {hiddenOpen && <>{header}{hiddenList.map((s) => <WatchRow key={s.id} s={s} />)}</>}
              </>
            )}

            {watchStock.length === 0 && watchETF.length === 0 && hiddenList.length === 0 && (
              <div style={{ color: D.comment, padding: "8px 0" }}>
                # 当前市场无自选
              </div>
            )}

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
