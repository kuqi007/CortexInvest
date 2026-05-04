"use client";

import { useState, useEffect } from "react";
import { D } from "../theme";
import { AppTabs } from "../components/AppTabs";

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
  if (v > 0) return D.red;
  if (v < 0) return D.green;
  return D.fg; // 0% → white for contrast
}

function priceColor(v: number | null): string {
  return changeColor(v);
}

function changeAbs(changePct: number | null, price: number | null): string {
  if (changePct === null || changePct === undefined || Number.isNaN(changePct) || price === null || price === undefined || Number.isNaN(price)) return "—";
  const abs = Math.abs(price * changePct / 100);
  return `${changePct >= 0 ? "+" : "-"}${fmtNum(abs, 2)}`;
}

function fmtChangePct(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${fmtNum(v, 2)}%`;
}

function changeSign(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  if (v > 0) return "+";
  return "";
}

/* ── Sparkline helper ──────────────────────────────────── */

function Sparkline({ data, color, labels }: { data: number[]; color: string; labels?: string[] }) {
  if (!data || data.length < 2) return null;
  const w = 120;
  const h = 32;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const coords = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return { x, y, v };
  });
  const points = coords.map((c) => `${c.x},${c.y}`).join(" ");
  return (
    <svg width={w} height={h} style={{ opacity: 0.75 }} role="img" aria-label="近N日价格走势">
      <polyline
        fill="none"
        stroke={color}
        strokeWidth={2}
        points={points}
      />
      {/* Data points with tooltip */}
      {coords.map((c, i) => (
        <circle
          key={i}
          cx={c.x}
          cy={c.y}
          r={2}
          fill={color}
          opacity={0.6}
        >
          <title>{labels?.[i] ?? `第${i + 1}日: ${c.v}`}</title>
        </circle>
      ))}
      {/* End dot marker */}
      {coords.length > 0 && (
        <circle
          cx={coords[coords.length - 1].x}
          cy={coords[coords.length - 1].y}
          r={3}
          fill={color}
          stroke={D.bg}
          strokeWidth={1}
        />
      )}
    </svg>
  );
}

/* ── Card Component (Futu-style) ──────────────────────── */

function MacroCard({
  name,
  price,
  priceColor,
  changeAbs,
  changePct,
  sparklineData,
}: {
  name: string;
  price: string;
  priceColor: string;
  changeAbs: string;
  changePct: string;
  sparklineData?: number[];
}) {
  const [hovered, setHovered] = useState(false);
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      // placeholder: could expand to detail view
    }
  };
  return (
    <div
      tabIndex={0}
      role="button"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onKeyDown={handleKeyDown}
      style={{
        padding: "16px",
        borderRadius: 16,
        background: D.currentLine,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        fontFamily: "JetBrains Mono, monospace",
        cursor: "default",
        transform: hovered ? "translateY(-2px)" : "translateY(0)",
        boxShadow: hovered
          ? "0 8px 24px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.04)"
          : "0 2px 8px rgba(0,0,0,0.2)",
        transition: "transform 0.2s, box-shadow 0.2s",
        outline: "none",
      }}
    >
      {/* Name */}
      <span style={{ fontSize: 13, color: "#a0a0b0", fontWeight: 400 }}>
        {name}
      </span>

      {/* Price */}
      <span style={{ fontSize: 26, color: priceColor, fontWeight: 700, lineHeight: 1.2 }}>
        {price}
      </span>

      {/* Change row */}
      <span style={{ fontSize: 12, color: priceColor, fontWeight: 500 }}>
        {changeAbs} {changePct}
      </span>

      {/* Sparkline or placeholder */}
      <div style={{ height: 32, marginTop: 2 }}>
        {sparklineData && sparklineData.length >= 2 ? (
          <Sparkline data={sparklineData} color={priceColor} />
        ) : (
          <div style={{ width: "100%", height: "100%" }} />
        )}
      </div>
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
      ? { color: D.red }
      : undefined;

  const goldAlert =
    data?.gold_change_pct !== null &&
    data?.gold_change_pct !== undefined &&
    !Number.isNaN(data.gold_change_pct) &&
    Math.abs(data.gold_change_pct) > 2
      ? { color: D.orange }
      : undefined;

  const copperAlert =
    data?.copper_change_pct !== null &&
    data?.copper_change_pct !== undefined &&
    !Number.isNaN(data.copper_change_pct) &&
    Math.abs(data.copper_change_pct) > 3
      ? { color: D.red }
      : undefined;

  const vixAlert =
    data?.vix !== null &&
    data?.vix !== undefined &&
    !Number.isNaN(data.vix) &&
    data.vix > 25
      ? { color: D.red }
      : undefined;

  const ty10yAlert =
    data?.ty10y !== null &&
    data?.ty10y !== undefined &&
    !Number.isNaN(data.ty10y) &&
    data.ty10y > 4.5
      ? { color: D.orange }
      : undefined;

  const fxAlert =
    data?.usd_cnh_change_pct !== null &&
    data?.usd_cnh_change_pct !== undefined &&
    !Number.isNaN(data.usd_cnh_change_pct) &&
    data.usd_cnh_change_pct < -0.5
      ? { color: D.orange }
      : undefined;

  const tungstenAlert =
    data?.tungsten_change_pct !== null &&
    data?.tungsten_change_pct !== undefined &&
    !Number.isNaN(data.tungsten_change_pct) &&
    Math.abs(data.tungsten_change_pct) > 2
      ? { color: D.orange }
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
      <AppTabs active="macro" />

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

      {/* Cards grid — Futu style */}
      <div
        style={{
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: 24,
          flex: 1,
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
            {/* ── Section: 大宗商品 ───────────────────────── */}
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: D.fg, marginBottom: 12 }}>
                大宗商品
              </div>
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                gap: 12,
              }}>
                <MacroCard
                  name="COMEX黄金"
                  price={fmtNum(data?.gold_price ?? null, 2)}
                  priceColor={priceColor(data?.gold_change_pct ?? null)}
                  changeAbs={changeAbs(data?.gold_change_pct ?? null, data?.gold_price ?? null)}
                  changePct={fmtChangePct(data?.gold_change_pct ?? null)}
                  sparklineData={history.map(h => h.gold_price).filter(Boolean) as number[]}
                />
                <MacroCard
                  name="LME铜"
                  price={fmtNum(data?.copper_price ?? null, 2)}
                  priceColor={priceColor(data?.copper_change_pct ?? null)}
                  changeAbs={changeAbs(data?.copper_change_pct ?? null, data?.copper_price ?? null)}
                  changePct={fmtChangePct(data?.copper_change_pct ?? null)}
                  sparklineData={history.map(h => h.copper_price).filter(Boolean) as number[]}
                />
                <MacroCard
                  name="65%黑钨精矿"
                  price={fmtNum(data?.tungsten_price ?? null, 2)}
                  priceColor={priceColor(data?.tungsten_change_pct ?? null)}
                  changeAbs={changeAbs(data?.tungsten_change_pct ?? null, data?.tungsten_price ?? null)}
                  changePct={fmtChangePct(data?.tungsten_change_pct ?? null)}
                  sparklineData={history.map(h => h.tungsten_price).filter(Boolean) as number[]}
                />
              </div>
            </div>

            {/* ── Section: 风险指标 ───────────────────────── */}
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: D.fg, marginBottom: 12 }}>
                风险指标
              </div>
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                gap: 12,
              }}>
                <MacroCard
                  name="VIX恐慌指数"
                  price={fmtNum(data?.vix ?? null, 2)}
                  priceColor={D.fg}
                  changeAbs="—"
                  changePct="—"
                />
                <MacroCard
                  name="美债10Y收益率"
                  price={fmtNum(data?.ty10y ?? null, 3)}
                  priceColor={D.fg}
                  changeAbs="—"
                  changePct="—"
                />
              </div>
            </div>

            {/* ── Section: 资金流动 ───────────────────────── */}
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: D.fg, marginBottom: 12 }}>
                资金流动
              </div>
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                gap: 12,
              }}>
                <MacroCard
                  name="北向净流入"
                  price={fmtNum(data?.northbound_net ?? null, 2)}
                  priceColor={data?.northbound_net != null && data.northbound_net > 0 ? D.red : data?.northbound_net != null && data.northbound_net < 0 ? D.green : D.fg}
                  changeAbs="—"
                  changePct="—"
                />
                <MacroCard
                  name="离岸人民币"
                  price={fmtNum(data?.usd_cnh ?? null, 4)}
                  priceColor={priceColor(data?.usd_cnh_change_pct ?? null)}
                  changeAbs={changeAbs(data?.usd_cnh_change_pct ?? null, data?.usd_cnh ?? null)}
                  changePct={fmtChangePct(data?.usd_cnh_change_pct ?? null)}
                  sparklineData={history.map(h => h.usd_cnh).filter(Boolean) as number[]}
                />
              </div>
            </div>
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
