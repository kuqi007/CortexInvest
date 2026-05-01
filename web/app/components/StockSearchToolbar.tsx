import type { ReactNode } from "react";

import {
  stockToolbarFieldStyle,
  stockToolbarLabelStyle,
  stockToolbarMatchStyle,
  stockToolbarStyle,
} from "./toolbarStyles";

export function StockSearchToolbar({
  search,
  onSearchChange,
  matchCount,
  totalCount,
  children,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  matchCount: number;
  totalCount: number;
  children: ReactNode;
}) {
  return (
    <div style={stockToolbarStyle}>
      <span style={stockToolbarLabelStyle}>搜索:</span>
      <input
        placeholder="代码或名称..."
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        style={stockToolbarFieldStyle(140)}
      />
      {search && (
        <span style={stockToolbarMatchStyle}>
          {matchCount}/{totalCount} 匹配
        </span>
      )}
      <div style={{ flex: 1 }} />
      {children}
    </div>
  );
}
