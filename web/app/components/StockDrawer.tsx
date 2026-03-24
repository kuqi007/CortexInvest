"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { D } from "../theme";
import { Service } from "../types";
import { PlanMap, TradePlan, PlanOrder } from "../hooks/useTradePlans";
import { EditableCell } from "./EditableCell";
import { Toast } from "./Toast";
import { TagArea } from "./TagArea";

/* ── Props ── */
export interface StockDrawerProps {
  symbol: string | null;
  services: Service[];
  planMap: PlanMap;
  allTags: string[];
  onClose: () => void;
  onRefreshPlans: () => void;
  onRefreshMetrics?: () => void;
}

/* ── Order type options ── */
const ORDER_TYPE_OPTIONS = [
  { label: "到价卖出", side: "sell" as const, op: ">=" as const, trailing: false },
  { label: "到价买入", side: "buy" as const, op: ">=" as const, trailing: false },
  { label: "回落卖出", side: "sell" as const, op: ">=" as const, trailing: true },
  { label: "反弹买入", side: "buy" as const, op: "<=" as const, trailing: true },
];

function orderTypeLabel(o: PlanOrder): string {
  if (o.trailing) {
    return o.side === "sell" ? "回落卖出" : "反弹买入";
  }
  return o.side === "sell" ? "到价卖出" : "到价买入";
}

/* ── PlanCard ── */
function PlanCard({
  plan,
  onChanged,
  indicators,
}: {
  plan: TradePlan;
  onChanged: () => void;
  indicators?: Record<string, unknown> | null;
}) {
  const planId = plan.id;
  const pos = plan.position;
  const lotSize = plan.lot_size || 1;
  void lotSize;
  const posShares = pos?.shares ?? 0;
  const posCost = pos?.cost ?? 0;
  const posPrice = pos?.price ?? 0;
  const pnlPct = posCost > 0 && posPrice > 0 ? ((posPrice - posCost) / posCost * 100) : null;
  const pnlAmt = posCost > 0 && posPrice > 0 && posShares > 0 ? (posPrice - posCost) * posShares : null;

  const [showAddOrder, setShowAddOrder] = useState(false);

  const cardBorder = plan.status === "active" ? D.green : D.comment;

  async function planPost(body: Record<string, unknown>) {
    try {
      await fetch("/api/trade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      onChanged();
    } catch {
      // silent
    }
  }

  async function handleToggle() {
    await planPost({ action: "toggle", id: planId });
  }

  async function handleDelete() {
    if (!confirm(`Delete plan ${planId}?`)) return;
    await planPost({ action: "delete", id: planId });
  }

  async function handleUpdateOrder(orderId: string, field: string, val: string) {
    const updatedOrders = plan.orders.map((o) => {
      if (o.id !== orderId) return o;
      const updated = { ...o };
      if (field === "price") updated.price = Number(val) || 0;
      if (field === "shares") updated.shares = Number(val) || 0;
      return updated;
    });
    await planPost({ action: "update", id: planId, updates: { orders: updatedOrders } });
  }

  return (
    <div style={{
      border: `1px solid ${cardBorder}`,
      borderRadius: 4,
      padding: "8px 12px",
      marginBottom: 8,
      background: D.bg,
    }}>
      {/* title row */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{
          display: "inline-block",
          padding: "1px 6px",
          borderRadius: 3,
          fontSize: 10,
          fontWeight: 700,
          background: plan.status === "active" ? D.green : D.comment,
          color: D.bg,
          cursor: "pointer",
          userSelect: "none",
        }} onClick={handleToggle} title="Toggle active/paused">
          {plan.status === "active" ? "运行中" : "已暂停"}
        </span>
        <span style={{ color: D.fg }}>{plan.name}</span>
        {pos && posShares > 0 && (
          <span style={{ color: D.comment, fontSize: 11 }}>
            {posShares}股 @{posCost > 0 ? posCost.toFixed(2) : "-"}
            {posPrice > 0 && <>{" "}now:{posPrice.toFixed(2)}</>}
            {pnlPct != null && (
              <span style={{ color: pnlPct >= 0 ? D.red : D.green, marginLeft: 4 }}>
                {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
              </span>
            )}
            {pnlAmt != null && (
              <span style={{ color: pnlAmt >= 0 ? D.red : D.green, marginLeft: 4 }}>
                {pnlAmt >= 0 ? "+" : ""}{pnlAmt.toFixed(0)}
              </span>
            )}
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <button
            onClick={() => setShowAddOrder((v) => !v)}
            style={{
              background: "transparent",
              border: `1px solid ${D.cyan}`,
              color: D.cyan,
              cursor: "pointer",
              fontSize: 11,
              fontFamily: "JetBrains Mono, monospace",
              padding: "1px 6px",
              borderRadius: 2,
            }}
            title="Add condition order"
          >+条件</button>
          <button
            onClick={handleDelete}
            style={{
              background: "transparent",
              border: "none",
              color: D.red,
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 700,
              fontFamily: "JetBrains Mono, monospace",
            }}
            title="Delete plan"
          >x</button>
        </span>
      </div>

      {/* orders */}
      {plan.orders.length > 0 && (
        <div style={{ fontSize: 12, marginTop: 4 }}>
          <div style={{ color: D.comment, fontSize: 11, marginBottom: 2 }}>-- 条件单 --</div>
          {plan.orders.map((o) => {
            const isSell = o.side === "sell";
            const isInd = !!o.indicators;

            // Compute indicator status for display
            let indStatus: { met: boolean; cur: string; dist: string | null } | null = null;
            if (isInd && indicators) {
              const ic = o.indicators!;
              const rsi = indicators.rsi as number | undefined;
              const dif = indicators.dif as number | undefined;
              const dea = indicators.dea as number | undefined;
              const volRatio = indicators.vol_ratio as number | undefined;
              const goldenCross = indicators.macd_golden_cross as boolean | undefined;
              const bullDiv = indicators.macd_bull_divergence as boolean | undefined;
              const ma5TurnUp = indicators.ma5_turn_up as boolean | undefined;

              if (ic.rsi_below != null && rsi != null) {
                const th = ic.rsi_below as number;
                indStatus = { met: rsi < th, cur: `RSI=${rsi.toFixed(0)}`, dist: rsi < th ? null : `差${(rsi - th).toFixed(0)}` };
              } else if (ic.rsi_above != null && rsi != null) {
                const th = ic.rsi_above as number;
                indStatus = { met: rsi > th, cur: `RSI=${rsi.toFixed(0)}`, dist: rsi > th ? null : `差${(th - rsi).toFixed(0)}` };
              } else if (ic.macd_golden_cross) {
                indStatus = { met: !!goldenCross, cur: dif != null && dea != null ? `DIF${dif > dea ? ">" : "<"}DEA` : "—", dist: goldenCross ? null : (dif != null && dea != null ? `差${(dea - dif).toFixed(2)}` : null) };
              } else if (ic.macd_bull_divergence) {
                indStatus = { met: !!bullDiv, cur: bullDiv ? "出现" : "未出现", dist: null };
              } else if (ic.ma5_turn_up) {
                indStatus = { met: !!ma5TurnUp, cur: ma5TurnUp ? "拐头↑" : "下行中", dist: null };
              } else if (ic.vol_ratio_above != null && volRatio != null) {
                const th = ic.vol_ratio_above as number;
                indStatus = { met: volRatio >= th, cur: `${volRatio.toFixed(1)}x`, dist: volRatio >= th ? null : `差${(th - volRatio).toFixed(1)}x` };
              } else if (ic.confluence_min != null) {
                const min = ic.confluence_min as number;
                let cnt = 0;
                if (rsi != null && rsi < 35) cnt++;
                if (goldenCross) cnt++;
                if (bullDiv) cnt++;
                if (ma5TurnUp) cnt++;
                if (volRatio != null && volRatio >= 1.5) cnt++;
                indStatus = { met: cnt >= min, cur: `${cnt}/${min}信号`, dist: cnt >= min ? null : `差${min - cnt}个` };
              }
            }

            return (
              <div key={o.id} style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "2px 0",
                opacity: o.triggered ? 0.5 : 1,
                borderLeft: `2px solid ${isInd ? D.cyan : isSell ? D.red : D.green}`,
                paddingLeft: 8,
                marginBottom: 2,
              }}>
                {isInd ? (
                  <>
                    <span style={{
                      fontSize: 10, color: D.bg, background: D.cyan,
                      padding: "0 4px", borderRadius: 2, fontWeight: 700,
                    }}>指标</span>
                    <span style={{ color: indStatus?.met ? D.green : D.comment, fontWeight: indStatus?.met ? 700 : 400, fontSize: 11 }}>
                      {indStatus?.met ? "●" : "○"}
                    </span>
                    <span style={{ color: D.fg, fontSize: 11 }}>{o.label}</span>
                    {indStatus && (
                      <span style={{
                        fontFamily: "JetBrains Mono, monospace", fontSize: 10, marginLeft: "auto",
                        color: indStatus.met ? D.green : D.comment,
                      }}>
                        {indStatus.cur}{indStatus.dist ? ` (${indStatus.dist})` : indStatus.met ? " ✓" : ""}
                      </span>
                    )}
                    {!indicators && !o.triggered && (
                      <span style={{ color: D.comment, fontSize: 10, marginLeft: "auto" }}>15:05检测</span>
                    )}
                  </>
                ) : (
                  <>
                    <span style={{
                      fontSize: 10, color: D.bg,
                      background: isSell ? D.red : D.green,
                      padding: "0 4px", borderRadius: 2, fontWeight: 700,
                    }}>
                      {orderTypeLabel(o)}
                    </span>
                    <span style={{ color: D.comment }}>{o.op}</span>
                    <EditableCell
                      value={o.price}
                      onSave={(v) => handleUpdateOrder(o.id, "price", v)}
                      width="60px"
                      isNumber
                      color={isSell ? D.red : D.green}
                    />
                    {o.shares != null && o.shares > 0 && (
                      <EditableCell
                        value={o.shares}
                        onSave={(v) => handleUpdateOrder(o.id, "shares", v)}
                        width="56px"
                        isNumber
                        color={isSell ? D.orange : D.green}
                      />
                    )}
                    <span style={{ color: isSell ? D.orange : D.green, fontSize: 11 }}>股</span>
                    {o.trailing && (
                      <span style={{ color: D.purple, fontSize: 10 }}>回落{o.trailing.pct}%</span>
                    )}
                    <span style={{ color: D.comment, fontSize: 11 }}>{o.label}</span>
                  </>
                )}
                {o.triggered && (
                  <span style={{ color: D.yellow, fontSize: 10 }}>已触发 {o.triggered_at?.slice(0, 10) || ""}</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* inline add order form */}
      {showAddOrder && (
        <NewOrderForm
          planId={planId}
          plan={plan}
          onCreated={() => { setShowAddOrder(false); onChanged(); }}
          onCancel={() => setShowAddOrder(false)}
        />
      )}
    </div>
  );
}

/* ── NewOrderForm (inline, appends an order to an existing plan) ── */
function NewOrderForm({
  planId,
  plan,
  onCreated,
  onCancel,
}: {
  planId: string;
  plan: TradePlan;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [typeIdx, setTypeIdx] = useState(0);
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [trailPct, setTrailPct] = useState("");
  const [label, setLabel] = useState("");

  const opt = ORDER_TYPE_OPTIONS[typeIdx];

  const inputS: React.CSSProperties = {
    background: D.currentLine,
    border: `1px solid ${D.comment}`,
    color: D.fg,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 11,
    padding: "3px 6px",
    outline: "none",
    borderRadius: 2,
  };

  const selectS: React.CSSProperties = {
    ...inputS,
    appearance: "auto" as const,
  };

  async function handleSubmit() {
    const qty = Number(quantity) || 0;
    const newOrder: Record<string, unknown> = {
      id: `o${plan.orders.length + 1}_${Date.now().toString(36)}`,
      side: opt.side,
      op: opt.op,
      price: Number(price) || 0,
      shares: qty || null,
      volume_min: null,
      consecutive_days: null,
      trailing: opt.trailing && trailPct ? { pct: Number(trailPct), watermark: null, active: false } : null,
      label: label || "",
      triggered: false,
      triggered_at: null,
    };
    const updatedOrders = [...plan.orders, newOrder];
    try {
      await fetch("/api/trade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", id: planId, updates: { orders: updatedOrders } }),
      });
      onCreated();
    } catch {
      // silent
    }
  }

  return (
    <div style={{
      marginTop: 8,
      padding: "8px",
      background: D.currentLine,
      borderRadius: 3,
      display: "flex",
      flexWrap: "wrap",
      gap: 6,
      alignItems: "center",
      fontSize: 11,
    }}>
      <select
        style={{ ...selectS, width: 90 }}
        value={typeIdx}
        onChange={(e) => setTypeIdx(Number(e.target.value))}
      >
        {ORDER_TYPE_OPTIONS.map((t, ti) => (
          <option key={ti} value={ti}>{t.label}</option>
        ))}
      </select>
      <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
        价格:
        <input
          style={{ ...inputS, width: 60 }}
          type="number"
          step="any"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
        />
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
        股数:
        <input
          style={{ ...inputS, width: 55 }}
          type="number"
          step="100"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          placeholder="1000"
        />
      </label>
      {opt.trailing && (
        <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.purple }}>
          回落%:
          <input
            style={{ ...inputS, width: 45 }}
            type="number"
            step="0.1"
            value={trailPct}
            onChange={(e) => setTrailPct(e.target.value)}
          />
        </label>
      )}
      <input
        style={{ ...inputS, width: 90 }}
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        placeholder="标签"
      />
      <button
        onClick={handleSubmit}
        style={{
          background: D.green,
          border: "none",
          color: D.bg,
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 700,
          padding: "3px 10px",
          borderRadius: 2,
          fontFamily: "JetBrains Mono, monospace",
        }}
      >确定</button>
      <button
        onClick={onCancel}
        style={{
          background: "transparent",
          border: `1px solid ${D.comment}`,
          color: D.comment,
          cursor: "pointer",
          fontSize: 11,
          padding: "3px 8px",
          borderRadius: 2,
          fontFamily: "JetBrains Mono, monospace",
        }}
      >取消</button>
    </div>
  );
}

/* ── NewPlanForm (inline, symbol pre-filled) ── */
function NewPlanForm({
  defaultSymbol,
  onCreated,
  onCancel,
}: {
  defaultSymbol: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [planName, setPlanName] = useState("");

  interface OrderDraft {
    typeIdx: number;
    price: string;
    quantity: string;
    trailPct: string;
    label: string;
  }
  const [orders, setOrders] = useState<OrderDraft[]>([]);

  function addOrderRow() {
    setOrders((prev) => [...prev, { typeIdx: 0, price: "", quantity: "", trailPct: "", label: "" }]);
  }

  function updateOrder(idx: number, field: keyof OrderDraft, val: string | number) {
    setOrders((prev) => prev.map((o, i) => i === idx ? { ...o, [field]: String(val) } : o));
  }

  function removeOrder(idx: number) {
    setOrders((prev) => prev.filter((_, i) => i !== idx));
  }

  const inputS: React.CSSProperties = {
    background: D.currentLine,
    border: `1px solid ${D.comment}`,
    color: D.fg,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 11,
    padding: "3px 6px",
    outline: "none",
    borderRadius: 2,
  };

  const selectS: React.CSSProperties = {
    ...inputS,
    appearance: "auto" as const,
  };

  async function handleSubmit() {
    if (!planName) return;
    const id = `${defaultSymbol}_${Date.now().toString(36)}`;

    const builtOrders = orders.map((o, i) => {
      const opt = ORDER_TYPE_OPTIONS[o.typeIdx];
      const qty = Number(o.quantity) || 0;
      return {
        id: `o${i + 1}`,
        side: opt.side,
        op: opt.op,
        price: Number(o.price) || 0,
        shares: qty || null,
        volume_min: null,
        consecutive_days: null,
        trailing: opt.trailing && o.trailPct ? { pct: Number(o.trailPct), watermark: null, active: false } : null,
        label: o.label || "",
        triggered: false,
        triggered_at: null,
      };
    });

    const plan = {
      name: planName,
      symbol: defaultSymbol,
      status: "active",
      scope: "real",
      created_at: new Date().toISOString().slice(0, 10),
      orders: builtOrders,
    };

    try {
      await fetch("/api/trade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "create", id, plan }),
      });
      onCreated();
    } catch {
      // silent
    }
  }

  return (
    <div style={{
      border: `1px solid ${D.purple}`,
      borderRadius: 4,
      padding: "10px 12px",
      marginBottom: 8,
      background: D.bg,
      fontSize: 12,
    }}>
      <div style={{ color: D.purple, fontWeight: 700, marginBottom: 8, fontSize: 12 }}>新建交易计划</div>

      {/* symbol (read-only) + plan name */}
      <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
        <span style={{ color: D.comment, fontSize: 11 }}>股票:</span>
        <span style={{ color: D.cyan, fontWeight: 700, fontSize: 12 }}>{defaultSymbol}</span>
        <label style={{ display: "flex", alignItems: "center", gap: 4, color: D.comment, marginLeft: 8 }}>
          计划名:
          <input
            style={{ ...inputS, width: 140 }}
            value={planName}
            onChange={(e) => setPlanName(e.target.value)}
            placeholder="分批建仓"
            autoFocus
          />
        </label>
      </div>

      {/* orders */}
      {orders.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          <div style={{ color: D.comment, fontSize: 11, marginBottom: 4 }}>-- 条件单 --</div>
          {orders.map((o, idx) => {
            const opt = ORDER_TYPE_OPTIONS[o.typeIdx];
            return (
              <div key={idx} style={{ display: "flex", gap: 5, marginBottom: 5, alignItems: "center", flexWrap: "wrap" }}>
                <select
                  style={{ ...selectS, width: 90 }}
                  value={o.typeIdx}
                  onChange={(e) => updateOrder(idx, "typeIdx", Number(e.target.value))}
                >
                  {ORDER_TYPE_OPTIONS.map((t, ti) => (
                    <option key={ti} value={ti}>{t.label}</option>
                  ))}
                </select>
                <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
                  价格:
                  <input
                    style={{ ...inputS, width: 60 }}
                    type="number"
                    step="any"
                    value={o.price}
                    onChange={(e) => updateOrder(idx, "price", e.target.value)}
                  />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
                  股数:
                  <input
                    style={{ ...inputS, width: 55 }}
                    type="number"
                    step="100"
                    value={o.quantity}
                    onChange={(e) => updateOrder(idx, "quantity", e.target.value)}
                    placeholder="1000"
                  />
                </label>
                {opt.trailing && (
                  <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.purple }}>
                    回落%:
                    <input
                      style={{ ...inputS, width: 45 }}
                      type="number"
                      step="0.1"
                      value={o.trailPct}
                      onChange={(e) => updateOrder(idx, "trailPct", e.target.value)}
                    />
                  </label>
                )}
                <input
                  style={{ ...inputS, width: 90 }}
                  value={o.label}
                  onChange={(e) => updateOrder(idx, "label", e.target.value)}
                  placeholder="标签"
                />
                <button
                  onClick={() => removeOrder(idx)}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: D.red,
                    cursor: "pointer",
                    fontSize: 13,
                    fontWeight: 700,
                    fontFamily: "JetBrains Mono, monospace",
                  }}
                >x</button>
              </div>
            );
          })}
        </div>
      )}

      <button
        onClick={addOrderRow}
        style={{
          background: "transparent",
          border: `1px dashed ${D.comment}`,
          color: D.comment,
          cursor: "pointer",
          fontSize: 11,
          padding: "2px 10px",
          borderRadius: 3,
          fontFamily: "JetBrains Mono, monospace",
          marginBottom: 10,
        }}
      >+ 添加条件</button>

      {/* actions */}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button
          onClick={onCancel}
          style={{
            background: "transparent",
            border: `1px solid ${D.comment}`,
            color: D.comment,
            cursor: "pointer",
            fontSize: 11,
            padding: "3px 12px",
            borderRadius: 3,
            fontFamily: "JetBrains Mono, monospace",
          }}
        >取消</button>
        <button
          onClick={handleSubmit}
          disabled={!planName}
          style={{
            background: planName ? D.purple : D.comment,
            border: "none",
            color: D.bg,
            cursor: planName ? "pointer" : "not-allowed",
            fontSize: 11,
            fontWeight: 700,
            padding: "3px 12px",
            borderRadius: 3,
            fontFamily: "JetBrains Mono, monospace",
          }}
        >创建</button>
      </div>
    </div>
  );
}

/* ── StockDrawer ── */
export function StockDrawer({
  symbol,
  services,
  planMap,
  allTags,
  onClose,
  onRefreshPlans,
  onRefreshMetrics,
}: StockDrawerProps) {
  // Keep a stable ref to onClose so the ESC handler doesn't need it as a dep
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // ESC key handler — dep only on symbol (open/close state)
  useEffect(() => {
    if (!symbol) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCloseRef.current();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [symbol]);

  // Body scroll lock while drawer is open
  useEffect(() => {
    if (!symbol) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [symbol]);

  const [toast, setToast] = useState<{ msg: string; type: "ok" | "err" } | null>(null);
  const [showNewPlanForm, setShowNewPlanForm] = useState(false);

  // Optimistic overrides: take precedence over stale service data from 30s poll
  const [starOverride, setStarOverride] = useState<boolean | null>(null);
  const [dipOverride, setDipOverride] = useState<boolean | null>(null);
  const [aliasOverride, setAliasOverride] = useState<string | null | undefined>(undefined);

  // Technical indicators from MetricsProvider (via indicator_cache)
  const svcMatch = services.find((s) => s.id === symbol);
  const indicators = svcMatch?.indicators ?? null;

  // Reset overrides when drawer opens a different symbol
  useEffect(() => {
    setStarOverride(null);
    setDipOverride(null);
    setAliasOverride(undefined);
  }, [symbol]);

  const saveConfig = useCallback(async (field: string, value: unknown) => {
    // Optimistic update for alias
    if (field === "alias") setAliasOverride(value as string | null);
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", code: symbol, data: { [field]: value } }),
      });
      setToast(res.ok ? { msg: "已保存", type: "ok" } : { msg: "保存失败", type: "err" });
      if (res.ok) onRefreshMetrics?.();
      else if (field === "alias") setAliasOverride(undefined); // revert on error
    } catch {
      setToast({ msg: "保存失败", type: "err" });
      if (field === "alias") setAliasOverride(undefined);
    }
    setTimeout(() => setToast(null), 2000);
  }, [symbol, onRefreshMetrics]);

  const saveData = useCallback(async (data: Record<string, unknown>) => {
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", code: symbol, data }),
      });
      setToast(res.ok ? { msg: "已保存", type: "ok" } : { msg: "保存失败", type: "err" });
      if (res.ok) onRefreshMetrics?.();
    } catch {
      setToast({ msg: "保存失败", type: "err" });
    }
    setTimeout(() => setToast(null), 2000);
  }, [symbol, onRefreshMetrics]);

  const saveTags = useCallback(async (code: string, tags: string[]) => {
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", code, data: { tags } }),
      });
      setToast(res.ok ? { msg: "标签已保存", type: "ok" } : { msg: "保存失败", type: "err" });
      if (res.ok) { onRefreshPlans(); onRefreshMetrics?.(); }
    } catch {
      setToast({ msg: "保存失败", type: "err" });
    }
    setTimeout(() => setToast(null), 2000);
  }, [onRefreshPlans, onRefreshMetrics]);

  if (!symbol) return null;

  const service = services.find((s) => s.id === symbol);
  const symbolPlans = planMap[symbol] ?? [];

  // Optimistic values: override takes precedence over stale poll data
  const isStar = starOverride !== null ? starOverride : (service?.star ?? false);
  const isDip = dipOverride !== null ? dipOverride : (service?.dip_buy ?? false);
  const aliasVal = aliasOverride !== undefined ? aliasOverride : (service?.alias ?? null);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.5)",
          zIndex: 200,
        }}
      />

      {/* Drawer panel */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(440px, 100vw)",
          background: D.bg,
          borderLeft: "1px solid " + D.currentLine,
          zIndex: 201,
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 13,
        }}
      >
        {/* Header */}
        <div
          style={{
            position: "sticky",
            top: 0,
            background: D.bg,
            borderBottom: "1px solid " + D.currentLine,
            padding: "10px 16px",
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "space-between",
            zIndex: 1,
          }}
        >
          <span style={{ color: D.cyan, fontWeight: "bold" }}>
            {symbol}&nbsp;&nbsp;{service?.name}
          </span>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{
              background: "none",
              border: "none",
              color: D.comment,
              cursor: "pointer",
              fontSize: 18,
              lineHeight: 1,
              padding: "0 4px",
            }}
          >
            ×
          </button>
        </div>

        {/* Content area */}
        <div style={{ padding: "12px 16px" }}>

          {/* Section 1: 基本信息 */}
          <section style={{ marginBottom: 20 }}>
            <div style={{ color: D.comment, fontSize: 11, marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>基本信息</div>
            <div style={{ display: "flex", gap: 24 }}>
              <span>成本&nbsp;
                <EditableCell
                  value={service?.cost ?? null}
                  onSave={(v) => saveConfig("cost", parseFloat(v))}
                  width="80px"
                  isNumber
                  placeholder="-"
                />
              </span>
              <span>股数&nbsp;
                <EditableCell
                  value={service?.shares ?? null}
                  onSave={(v) => saveConfig("shares", parseInt(v))}
                  width="80px"
                  isNumber
                  placeholder="-"
                />
              </span>
              <span style={{ color: D.comment }}>别名&nbsp;
                <EditableCell
                  value={aliasVal}
                  onSave={(v) => saveConfig("alias", v.trim() || null)}
                  width="90px"
                  placeholder="-"
                />
              </span>
            </div>
            {/* star + dip toggles + delete */}
            <div style={{ display: "flex", gap: 16, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
              {/* star */}
              <button
                onClick={() => { setStarOverride(!isStar); saveData({ star: !isStar }); }}
                title={isStar ? "取消 L1 优先级" : "标记为 L1 优先级"}
                style={{
                  background: isStar ? D.yellow : "transparent",
                  border: `1px solid ${isStar ? D.yellow : D.currentLine}`,
                  color: isStar ? D.bg : D.comment,
                  cursor: "pointer",
                  fontSize: 13,
                  fontWeight: 700,
                  padding: "2px 10px",
                  borderRadius: 3,
                  fontFamily: "JetBrains Mono, monospace",
                  userSelect: "none",
                }}
              >
                ★ {isStar ? "已星标" : "星标"}
              </button>
              {/* dip_buy */}
              <span
                onClick={() => { setDipOverride(!isDip); saveData({ dip_buy: !isDip }); }}
                title={isDip ? "关闭回调监控" : "开启回调买入监控"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  cursor: "pointer",
                  userSelect: "none",
                  fontFamily: "JetBrains Mono, monospace",
                  fontSize: 12,
                  color: isDip ? D.cyan : D.comment,
                }}
              >
                <span style={{
                  display: "inline-block",
                  width: 28, height: 14,
                  borderRadius: 7,
                  background: isDip ? D.cyan : D.currentLine,
                  position: "relative",
                  transition: "background 0.2s",
                }}>
                  <span style={{
                    display: "inline-block",
                    width: 10, height: 10,
                    borderRadius: "50%",
                    background: isDip ? D.bg : D.comment,
                    position: "absolute",
                    top: 2,
                    left: isDip ? 16 : 2,
                    transition: "left 0.2s",
                  }} />
                </span>
                dip 回调监控
              </span>
              {/* demote: holding → watching */}
              {service?.type === "holding" && (
                <button
                  onClick={async () => {
                    if (!symbol) return;
                    if (!window.confirm(`将 ${symbol}（${service?.name ?? ""}）从持仓降为自选？`)) return;
                    try {
                      const res = await fetch("/api/config", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ action: "update", code: symbol, data: { type: "watching" } }),
                      });
                      const json = await res.json();
                      if (res.ok && json.success) {
                        onRefreshMetrics?.();
                        onClose();
                      } else {
                        setToast({ msg: json.message || "操作失败", type: "err" });
                        setTimeout(() => setToast(null), 2500);
                      }
                    } catch {
                      setToast({ msg: "操作失败", type: "err" });
                      setTimeout(() => setToast(null), 2500);
                    }
                  }}
                  title="从持仓降为自选"
                  style={{
                    background: "transparent",
                    border: `1px solid ${D.orange}`,
                    color: D.orange,
                    cursor: "pointer",
                    fontSize: 11,
                    padding: "2px 10px",
                    borderRadius: 3,
                    fontFamily: "JetBrains Mono, monospace",
                    userSelect: "none",
                    opacity: 0.7,
                  }}
                >
                  转自选
                </button>
              )}
              {/* hide / unhide — only for holdings */}
              {service?.type === "holding" && (
                <button
                  onClick={() => saveData({ hidden: !service?.hidden })}
                  title={service?.hidden ? "取消隐藏" : "隐藏（不通知）"}
                  style={{
                    background: service?.hidden ? "rgba(155, 77, 207, 0.3)" : "transparent",
                    border: `1px solid ${D.purple}`,
                    color: service?.hidden ? D.purple : D.purple,
                    cursor: "pointer",
                    fontSize: 11,
                    padding: "2px 10px",
                    borderRadius: 3,
                    fontFamily: "JetBrains Mono, monospace",
                    userSelect: "none",
                    opacity: 0.9,
                  }}
                >
                  {service?.hidden ? "取消隐藏" : "隐藏"}
                </button>
              )}
              {/* delete */}
              <button
                onClick={async () => {
                  if (!symbol) return;
                  const label = service?.type === "holding" ? "持仓" : "自选";
                  if (!window.confirm(`确认删除 ${label} ${symbol}（${service?.name ?? ""}）？此操作不可撤销。`)) return;
                  try {
                    const res = await fetch("/api/config", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ action: "remove", code: symbol }),
                    });
                    const json = await res.json();
                    if (res.ok && json.success) {
                      onRefreshMetrics?.();
                      onClose();
                    } else {
                      setToast({ msg: json.message || "删除失败", type: "err" });
                      setTimeout(() => setToast(null), 2500);
                    }
                  } catch {
                    setToast({ msg: "删除失败", type: "err" });
                    setTimeout(() => setToast(null), 2500);
                  }
                }}
                title="从监控列表中删除"
                style={{
                  marginLeft: "auto",
                  background: "transparent",
                  border: `1px solid ${D.red}`,
                  color: D.red,
                  cursor: "pointer",
                  fontSize: 11,
                  padding: "2px 10px",
                  borderRadius: 3,
                  fontFamily: "JetBrains Mono, monospace",
                  userSelect: "none",
                  opacity: 0.7,
                }}
              >
                删除
              </button>
            </div>
          </section>

          {/* Divider */}
          <div style={{ borderTop: "1px solid " + D.currentLine, marginBottom: 16, paddingTop: 16 }} />

          {/* Section 2: 标签 */}
          <section style={{ marginBottom: 20 }}>
            <div style={{ color: D.comment, fontSize: 11, marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>标签</div>
            {symbol && (
              <TagArea
                code={symbol}
                tags={service?.tags || []}
                allTags={allTags}
                onSaveTags={saveTags}
                zIndex={300}
                wrap
              />
            )}
          </section>

          {/* Section: 技术指标 */}
          {symbol && !symbol.startsWith("KR") && (
            <div style={{ borderTop: `1px solid ${D.currentLine}`, marginTop: 8, paddingTop: 16 }}>
              <div style={{ color: D.comment, fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
                技术指标 (日线)
              </div>
              {!indicators && <div style={{ color: D.comment, fontSize: 11 }}>暂无数据</div>}
              {indicators && (() => {
                const rsi = indicators.rsi as number;
                const macdHist = indicators.macd_hist as number;
                const ma5 = indicators.ma5 as number;
                const ma10 = indicators.ma10 as number;
                const ma20 = indicators.ma20 as number;
                const close = indicators.close as number;
                const volRatio = indicators.vol_ratio as number;
                const goldenCross = indicators.macd_golden_cross as boolean;
                const deathCross = indicators.macd_death_cross as boolean;
                const bullDiv = indicators.macd_bull_divergence as boolean;
                const ma5TurnUp = indicators.ma5_turn_up as boolean;

                // RSI color
                const rsiColor = rsi < 30 ? D.green : rsi > 70 ? D.red : D.fg;
                // MA alignment
                const maAlign = ma5 > ma10 && ma10 > ma20 ? "多头排列" : ma5 < ma10 && ma10 < ma20 ? "空头排列" : "交叉";
                const maColor = maAlign === "多头排列" ? D.red : maAlign === "空头排列" ? D.green : D.yellow;

                // Active signals
                const signals: { label: string; color: string }[] = [];
                if (rsi < 30) signals.push({ label: "RSI超卖", color: D.green });
                if (rsi > 70) signals.push({ label: "RSI超买", color: D.red });
                if (goldenCross) signals.push({ label: "MACD金叉", color: D.green });
                if (deathCross) signals.push({ label: "MACD死叉", color: D.red });
                if (bullDiv) signals.push({ label: "MACD底背离", color: D.cyan });
                if (volRatio >= 2.0) signals.push({ label: `放量${volRatio.toFixed(1)}x`, color: D.yellow });
                if (ma5TurnUp) signals.push({ label: "MA5拐头↑", color: D.green });

                const rowStyle: React.CSSProperties = { display: "flex", gap: 12, fontSize: 12, padding: "2px 0" };
                const labelStyle: React.CSSProperties = { color: D.comment, width: 60, flexShrink: 0, textAlign: "right" as const };
                const valStyle: React.CSSProperties = { fontFamily: "JetBrains Mono, monospace" };

                return (
                  <div style={{ background: D.bg, border: `1px solid ${D.currentLine}`, borderRadius: 4, padding: "8px 12px" }}>
                    <div style={rowStyle}>
                      <span style={labelStyle}>RSI</span>
                      <span style={{ ...valStyle, color: rsiColor, fontWeight: 700 }}>{rsi.toFixed(0)}</span>
                      <span style={{ color: D.comment, fontSize: 10 }}>
                        {rsi < 30 ? "超卖" : rsi < 40 ? "偏弱" : rsi > 70 ? "超买" : rsi > 60 ? "偏强" : "中性"}
                      </span>
                    </div>
                    <div style={rowStyle}>
                      <span style={labelStyle}>MACD</span>
                      <span style={{ ...valStyle, color: macdHist >= 0 ? D.red : D.green }}>{macdHist.toFixed(2)}</span>
                      <span style={{ color: D.comment, fontSize: 10 }}>
                        DIF={(indicators.dif as number).toFixed(2)} DEA={(indicators.dea as number).toFixed(2)}
                      </span>
                    </div>
                    <div style={rowStyle}>
                      <span style={labelStyle}>均线</span>
                      <span style={{ ...valStyle, color: maColor, fontSize: 11 }}>{maAlign}</span>
                      <span style={{ color: D.comment, fontSize: 10 }}>
                        MA5={ma5.toFixed(0)} MA10={ma10.toFixed(0)} MA20={ma20.toFixed(0)}
                      </span>
                    </div>
                    <div style={rowStyle}>
                      <span style={labelStyle}>量比</span>
                      <span style={{ ...valStyle, color: volRatio >= 2 ? D.yellow : D.fg }}>{volRatio.toFixed(1)}x</span>
                      <span style={{ color: D.comment, fontSize: 10 }}>vs MA20</span>
                    </div>
                    <div style={rowStyle}>
                      <span style={labelStyle}>价格</span>
                      <span style={valStyle}>{close.toFixed(2)}</span>
                      <span style={{ color: D.comment, fontSize: 10 }}>
                        {close < ma5 ? `< MA5(${ma5.toFixed(0)})` : close > ma20 ? `> MA20(${ma20.toFixed(0)})` : `MA5~MA20之间`}
                      </span>
                    </div>
                    {signals.length > 0 && (
                      <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {signals.map((s) => (
                          <span key={s.label} style={{
                            fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
                            background: s.color, color: D.bg,
                          }}>{s.label}</span>
                        ))}
                      </div>
                    )}
                    {signals.length === 0 && (
                      <div style={{ marginTop: 4, color: D.comment, fontSize: 10 }}>无活跃信号 — 等待止跌确认</div>
                    )}

                  </div>
                );
              })()}
            </div>
          )}

          {/* Section 4: 交易计划 */}
          <div style={{ borderTop: `1px solid ${D.currentLine}`, marginTop: 8, paddingTop: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div style={{ color: D.comment, fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>交易计划</div>
              <button
                onClick={() => setShowNewPlanForm(true)}
                style={{
                  fontSize: 11, background: "none",
                  border: `1px solid ${D.cyan}`, color: D.cyan,
                  padding: "2px 10px", cursor: "pointer", borderRadius: 2,
                  fontFamily: "JetBrains Mono, monospace",
                }}
              >+ 新建</button>
            </div>
            {showNewPlanForm && (
              <NewPlanForm
                defaultSymbol={symbol!}
                onCreated={() => { setShowNewPlanForm(false); onRefreshPlans(); }}
                onCancel={() => setShowNewPlanForm(false)}
              />
            )}
            {symbolPlans.length === 0 && !showNewPlanForm && (
              <div style={{ color: D.comment, fontSize: 11 }}>暂无计划，点击「+ 新建」创建</div>
            )}
            {symbolPlans.map((plan) => (
              <PlanCard key={plan.id} plan={plan} onChanged={onRefreshPlans} indicators={indicators} />
            ))}
          </div>

          {/* Section 5: 变更历史 */}
          <ChangeHistorySection symbol={symbol!} />

        </div>
      </div>

      {/* Toast */}
      {toast && <Toast message={toast.msg} type={toast.type} />}
    </>
  );
}

/* ── ChangeHistorySection ── */
function ChangeHistorySection({ symbol }: { symbol: string }) {
  const [expanded, setExpanded] = useState(false);
  const [logs, setLogs] = useState<ChangeLogEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const limit = expanded ? 20 : 3;

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    fetch(`/api/position-change-log?symbol=${encodeURIComponent(symbol)}&limit=${limit}`)
      .then((r) => r.json())
      .then((d) => setLogs(d.results ?? []))
      .finally(() => setLoading(false));
  }, [symbol, limit]);

  const sourceColor: Record<string, string> = {
    manual: D.cyan,
    import: D.purple,
    sync: D.yellow,
    type_change: D.orange,
  };
  const sourceLabel: Record<string, string> = {
    manual: "手动",
    import: "导入",
    sync: "同步",
    type_change: "类型转换",
  };

  function fmtVal(v: number | null | undefined): string {
    if (v == null) return "-";
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }

  return (
    <div style={{ borderTop: `1px solid ${D.currentLine}`, marginTop: 8, paddingTop: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ color: D.comment, fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>
          变更历史 {logs.length > 0 && `(${logs.length})`}
        </div>
        {logs.length > 3 && !expanded && (
          <button
            onClick={() => setExpanded(true)}
            style={{
              fontSize: 11, background: "none",
              border: `1px solid ${D.cyan}`, color: D.cyan,
              padding: "2px 10px", cursor: "pointer", borderRadius: 2,
              fontFamily: "JetBrains Mono, monospace",
            }}
          >展开更多</button>
        )}
      </div>

      {loading && <div style={{ color: D.comment, fontSize: 11 }}>加载中...</div>}

      {!loading && logs.length === 0 && (
        <div style={{ color: D.comment, fontSize: 11 }}>暂无变更记录</div>
      )}

      {logs.map((log) => {
        const color = sourceColor[log.source] ?? D.comment;
        const label = sourceLabel[log.source] ?? log.source;
        const dateStr = log.ts ? new Date(log.ts).toLocaleString("zh-CN", {
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
        }).replace(/\//g, "-") : "-";
        const sharesStr = `${fmtVal(log.shares_from)} → ${fmtVal(log.shares_to)}股`;
        const costStr = `${fmtVal(log.cost_from)} → ${fmtVal(log.cost_to)}`;

        return (
          <div key={log.ts + log.source} style={{
            display: "flex", gap: 8, fontSize: 12, padding: "4px 0",
            borderBottom: `1px solid ${D.currentLine}`,
          }}>
            <span style={{ color: D.comment, whiteSpace: "nowrap", minWidth: 110 }}>
              {dateStr}
            </span>
            <span style={{
              display: "inline-block",
              padding: "1px 6px",
              borderRadius: 3,
              fontSize: 10,
              fontWeight: 700,
              background: color,
              color: D.bg,
            }}>
              {label}
            </span>
            <span style={{ flex: 1, textAlign: "right", color: D.fg }}>
              {sharesStr}
            </span>
            <span style={{ minWidth: 130, textAlign: "right", color: D.fg }}>
              {costStr}
            </span>
          </div>
        );
      })}
    </div>
  );
}

type ChangeLogEntry = {
  ts: string;
  source: string;
  shares_from: number | null;
  shares_to: number | null;
  cost_from: number | null;
  cost_to: number | null;
};

// Re-export shared helpers so existing imports from this file keep working
export { EditableCell, Toast };
