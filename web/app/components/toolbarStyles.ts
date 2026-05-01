import type { CSSProperties } from "react";

import { D } from "../theme";

export const stockToolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginBottom: 6,
  flexWrap: "wrap",
};

export const stockToolbarLabelStyle: CSSProperties = {
  color: D.comment,
};

export const stockToolbarMatchStyle: CSSProperties = {
  color: D.comment,
  fontSize: 11,
};

export function stockToolbarFieldStyle(width: number, disabled = false): CSSProperties {
  return {
    background: D.currentLine,
    border: `1px solid ${D.comment}`,
    color: D.fg,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 12,
    padding: "2px 8px",
    outline: "none",
    borderRadius: 2,
    width,
    opacity: disabled ? 0.5 : 1,
  };
}

export const stockToolbarSelectStyle: CSSProperties = {
  ...stockToolbarFieldStyle(86),
};

export const stockToolbarButtonStyle: CSSProperties = {
  background: D.purple,
  color: D.bg,
  border: "none",
  fontFamily: "JetBrains Mono, monospace",
  fontSize: 12,
  fontWeight: 700,
  padding: "3px 12px",
  borderRadius: 3,
  cursor: "pointer",
};
