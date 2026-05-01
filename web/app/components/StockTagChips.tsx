"use client";

import type { CSSProperties, MouseEvent } from "react";
import { layeredTagChipStyle, sortTagsForDisplay } from "../lib/tag-utils";

export function StockTagChips({
  tags,
  maxVisible = 3,
  onTagClick,
  containerStyle,
}: {
  tags?: string[];
  maxVisible?: number;
  onTagClick?: (tag: string, e: MouseEvent<HTMLSpanElement>) => void;
  containerStyle?: CSSProperties;
}) {
  const sorted = sortTagsForDisplay(tags ?? []);
  const shown = sorted.slice(0, maxVisible);
  const rest = sorted.length - shown.length;

  return (
    <span
      title={sorted.join(" · ")}
      style={{
        display: "inline-flex",
        alignItems: "center",
        flexWrap: "nowrap",
        gap: 2,
        ...containerStyle,
      }}
    >
      {shown.map((t) => (
        <span
          key={t}
          style={{
            ...layeredTagChipStyle(t),
            cursor: onTagClick ? "pointer" : "default",
          }}
          onClick={onTagClick ? (e) => onTagClick(t, e) : undefined}
        >
          {t}
        </span>
      ))}
      {rest > 0 ? (
        <span
          style={{
            fontSize: 10,
            color: "#6272a4",
            fontWeight: 700,
            fontFamily: "JetBrains Mono, monospace",
            marginLeft: 1,
          }}
        >
          +{rest}
        </span>
      ) : null}
    </span>
  );
}
