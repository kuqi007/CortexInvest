import type { CSSProperties } from "react";

import { pad } from "../lib/display-utils";
import { D } from "../theme";

export type StockTableColumn<K extends string> = {
  key?: K;
  label: string;
  width: string;
  alignRight?: boolean;
  sortable?: boolean;
  padWidth?: number;
  marginLeft?: number;
};

export const portfolioColumns: StockTableColumn<string>[] = [
  { label: "标记", width: "9ch" },
  { key: "id", label: "代码", width: "10ch", sortable: true },
  { label: "名称", width: "10ch" },
  { key: "price", label: "现价", width: "10ch", alignRight: true, sortable: true, padWidth: 9 },
  { key: "change", label: "涨跌幅", width: "9ch", alignRight: true, sortable: true, padWidth: 8 },
  { key: "cost", label: "成本", width: "9ch", alignRight: true, sortable: true, padWidth: 8 },
  { label: "股数", width: "7ch", alignRight: true, padWidth: 6 },
  { key: "pnl", label: "盈亏%", width: "10ch", alignRight: true, sortable: true, padWidth: 9 },
  { key: "mktVal", label: "市值", width: "10ch", alignRight: true, sortable: true, padWidth: 9 },
  { key: "position_pct", label: "仓位", width: "8ch", alignRight: true, sortable: true, padWidth: 7 },
  { key: "totalPnl", label: "盈亏额", width: "10ch", alignRight: true, sortable: true, padWidth: 9 },
  { key: "dayPnl", label: "今日", width: "9ch", alignRight: true, sortable: true, padWidth: 8 },
  { key: "volRatio", label: "量比", width: "7ch", alignRight: true, sortable: true, padWidth: 6 },
  { key: "turnover", label: "换手%", width: "8ch", alignRight: true, sortable: true, padWidth: 7 },
  { key: "amount", label: "成交额", width: "9ch", alignRight: true, sortable: true, padWidth: 8 },
];

export const watchingColumns: StockTableColumn<string>[] = [
  { label: "标记", width: "9ch" },
  { key: "id", label: "代码", width: "10ch" },
  { label: "名称", width: "10ch" },
  { key: "price", label: "现价", width: "10ch", alignRight: true, sortable: true, padWidth: 9 },
  { key: "change", label: "涨跌幅", width: "9ch", alignRight: true, sortable: true, padWidth: 8 },
  { key: "chgAmt", label: "涨跌", width: "8ch", alignRight: true, sortable: true, padWidth: 7 },
  { key: "volRatio", label: "量比", width: "7ch", alignRight: true, sortable: true, padWidth: 6 },
  { key: "turnover", label: "换手%", width: "8ch", alignRight: true, sortable: true, padWidth: 7 },
  { key: "amount", label: "成交额", width: "9ch", alignRight: true, sortable: true, padWidth: 8 },
];

export function l2Columns<K extends string>(): StockTableColumn<K>[] {
  return [
    { key: "mainNetInflow" as K, label: "主力", width: "9ch", alignRight: true, sortable: true, padWidth: 8 },
    { key: "mainNetInflowPct" as K, label: "主力%", width: "7ch", alignRight: true, sortable: true, padWidth: 6 },
  ];
}

export function tagColumn<K extends string>(): StockTableColumn<K> {
  return { label: "标签", width: "12ch", marginLeft: 8 };
}

export function StockTableHeader<K extends string>({
  columns,
  sortKey,
  sortAsc,
  onSort,
}: {
  columns: StockTableColumn<K>[];
  sortKey: K | null;
  sortAsc: boolean;
  onSort: (key: K) => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        whiteSpace: "pre",
        color: D.pink,
        borderBottom: `1px solid ${D.currentLine}`,
        paddingBottom: 3,
        marginBottom: 2,
        fontWeight: 500,
      }}
    >
      {columns.map((column, index) => {
        const key = column.key;
        const active = Boolean(key && sortKey === key);
        const arrow = active ? (sortAsc ? " ▲" : " ▼") : "";
        const text = column.padWidth
          ? pad(column.label + arrow, column.padWidth, column.alignRight)
          : column.label + arrow;
        const style: CSSProperties = {
          width: column.width,
          textAlign: column.alignRight ? "right" : "left",
          cursor: key && column.sortable ? "pointer" : "default",
          userSelect: "none",
          color: active ? D.yellow : D.pink,
          marginLeft: column.marginLeft,
        };

        return (
          <span
            key={`${column.label}-${index}`}
            style={style}
            data-sort-key={key}
            onClick={key && column.sortable ? () => onSort(key) : undefined}
          >
            {index === 0 ? ` ${text}` : text}
          </span>
        );
      })}
    </div>
  );
}
