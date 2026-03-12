"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { D } from "../theme";
import { tagColor } from "../lib/tag-utils";

/* ── TagEditor: combobox style ──────────────────────────────
   - Single input: search existing OR create new
   - Click tag chip to toggle instantly (no confirm button)
   - Enter on empty match → create new tag
   - Auto-save on close (click outside / Escape)
   ────────────────────────────────────────────────────────── */
function TagEditor({
  code,
  currentTags,
  allTags,
  onSave,
  onClose,
  zIndex = 100,
}: {
  code: string;
  currentTags: string[];
  allTags: string[];
  onSave: (code: string, tags: string[]) => void;
  onClose: () => void;
  zIndex?: number;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(currentTags));
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Save + close
  const close = useCallback(() => {
    onSave(code, Array.from(selected));
    onClose();
  }, [code, selected, onSave, onClose]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [close]);

  function toggle(tag: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag); else next.add(tag);
      return next;
    });
  }

  const q = query.trim();
  const filtered = allTags.filter((t) => t.toLowerCase().includes(query.toLowerCase()));
  const exactMatch = allTags.some((t) => t.toLowerCase() === q.toLowerCase());
  const canCreate = q.length > 0 && !exactMatch;

  function handleEnter() {
    if (filtered.length === 1 && !canCreate) {
      // single match → toggle it
      toggle(filtered[0]);
      setQuery("");
    } else if (canCreate) {
      // create new tag
      setSelected((prev) => new Set(prev).add(q));
      setQuery("");
    }
  }

  // Sort: selected first, then alphabetical
  const sorted = [
    ...filtered.filter((t) => selected.has(t)),
    ...filtered.filter((t) => !selected.has(t)),
  ];

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        top: "calc(100% + 4px)",
        left: 0,
        zIndex,
        background: "#1e1f29",
        border: `1px solid ${D.purple}`,
        borderRadius: 6,
        padding: "8px 8px 6px",
        minWidth: 200,
        maxWidth: 280,
        boxShadow: "0 4px 16px rgba(0,0,0,0.5)",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 11,
      }}
    >
      {/* ── Combobox input ── */}
      <input
        ref={inputRef}
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") handleEnter(); }}
        placeholder="search or create tag..."
        style={{
          background: D.currentLine,
          border: `1px solid ${D.comment}`,
          color: D.fg,
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 11,
          padding: "4px 8px",
          outline: "none",
          borderRadius: 3,
          width: "100%",
          boxSizing: "border-box",
          marginBottom: 6,
        }}
      />

      {/* ── Tag list ── */}
      <div style={{ maxHeight: 200, overflowY: "auto", display: "flex", flexWrap: "wrap", gap: 4 }}>
        {sorted.map((t) => {
          const on = selected.has(t);
          return (
            <span
              key={t}
              onClick={() => { toggle(t); inputRef.current?.focus(); }}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
                background: tagColor(t),
                color: "#282a36",
                padding: "2px 7px",
                borderRadius: 3,
                fontSize: 10,
                fontWeight: 700,
                cursor: "pointer",
                opacity: on ? 1 : 0.35,
                outline: on ? `2px solid ${D.green}` : "none",
                outlineOffset: 1,
                userSelect: "none",
              }}
            >
              {on && <span style={{ fontSize: 9, lineHeight: 1 }}>✓</span>}
              {t}
            </span>
          );
        })}

        {/* Create new tag hint */}
        {canCreate && (
          <span
            onClick={() => { setSelected((p) => new Set(p).add(q)); setQuery(""); inputRef.current?.focus(); }}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              background: "transparent",
              border: `1px dashed ${D.green}`,
              color: D.green,
              padding: "2px 7px",
              borderRadius: 3,
              fontSize: 10,
              fontWeight: 700,
              cursor: "pointer",
              userSelect: "none",
            }}
          >
            + 创建 &ldquo;{q}&rdquo;
          </span>
        )}

        {sorted.length === 0 && !canCreate && (
          <span style={{ color: D.comment, fontSize: 10, padding: "2px 0" }}>no tags yet</span>
        )}
      </div>

      {/* ── Footer hint ── */}
      <div style={{ marginTop: 6, color: D.comment, fontSize: 10, borderTop: `1px solid ${D.currentLine}`, paddingTop: 5 }}>
        点击 tag 选/取消 · Enter 确认 · Esc 保存关闭
      </div>
    </div>
  );
}

/* ── TagArea: chips with hover-to-delete + combobox editor ── */
export function TagArea({
  code,
  tags,
  allTags,
  onSaveTags,
  zIndex,
  wrap,
}: {
  code: string;
  tags: string[];
  allTags: string[];
  onSaveTags: (code: string, tags: string[]) => void;
  zIndex?: number;
  wrap?: boolean;
}) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [hoveredTag, setHoveredTag] = useState<string | null>(null);

  return (
    <span
      style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 3, cursor: "pointer", flexWrap: wrap ? "wrap" : undefined }}
      onClick={() => setEditorOpen((v) => !v)}
      title="Click to edit tags"
    >
      {tags.length > 0
        ? tags.map((t) => (
            <span
              key={t}
              onMouseEnter={() => setHoveredTag(t)}
              onMouseLeave={() => setHoveredTag(null)}
              style={{
                display: "inline-flex",
                alignItems: "center",
                background: tagColor(t),
                color: "#282a36",
                padding: hoveredTag === t ? "0 2px 0 5px" : "0 5px",
                borderRadius: 3,
                fontSize: 10,
                fontWeight: 700,
                whiteSpace: "nowrap",
                cursor: "default",
              }}
            >
              {t}
              {hoveredTag === t && (
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    onSaveTags(code, tags.filter((x) => x !== t));
                  }}
                  style={{ marginLeft: 3, cursor: "pointer", fontWeight: 900, fontSize: 11, lineHeight: 1, color: "#282a36", opacity: 0.8 }}
                >×</span>
              )}
            </span>
          ))
        : null
      }
      {/* Always-visible + button */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 16,
          height: 16,
          borderRadius: 3,
          border: `1px dashed ${D.comment}`,
          color: D.comment,
          fontSize: 12,
          lineHeight: 1,
          cursor: "pointer",
          flexShrink: 0,
          opacity: editorOpen ? 1 : 0.6,
        }}
        title="Add tag"
      >+</span>
      {editorOpen && (
        <TagEditor
          code={code}
          currentTags={tags}
          allTags={allTags}
          onSave={onSaveTags}
          onClose={() => setEditorOpen(false)}
          zIndex={zIndex}
        />
      )}
    </span>
  );
}
