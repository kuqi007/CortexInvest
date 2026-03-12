"use client";

import { useState, useRef, useEffect } from "react";
import { D } from "../theme";
import { tagColor } from "../lib/tag-utils";

/* ── TagEditor dropdown ── */
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
  const [newTag, setNewTag] = useState("");
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [onClose]);

  function toggle(tag: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  function addNew() {
    const t = newTag.trim();
    if (!t) return;
    setSelected((prev) => new Set(prev).add(t));
    setNewTag("");
  }

  const filtered = allTags.filter((t) => t.toLowerCase().includes(search.toLowerCase()));

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        top: "100%",
        left: 0,
        zIndex,
        background: D.bg,
        border: `1px solid ${D.purple}`,
        borderRadius: 4,
        padding: "8px 10px",
        minWidth: 180,
        maxHeight: 260,
        overflow: "auto",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 11,
      }}
    >
      {allTags.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          <input
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="search tags..."
            style={{
              background: D.currentLine,
              border: `1px solid ${D.comment}`,
              color: D.fg,
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 11,
              padding: "2px 6px",
              outline: "none",
              borderRadius: 2,
              width: "100%",
              boxSizing: "border-box",
              marginBottom: 6,
            }}
          />
          {filtered.map((t) => (
            <label
              key={t}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0", cursor: "pointer", color: D.fg }}
            >
              <input
                type="checkbox"
                checked={selected.has(t)}
                onChange={() => toggle(t)}
                style={{ accentColor: D.purple }}
              />
              <span style={{ background: tagColor(t), color: "#282a36", padding: "0 6px", borderRadius: 3, fontSize: 10, fontWeight: 700 }}>
                {t}
              </span>
            </label>
          ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
        <input
          value={newTag}
          onChange={(e) => setNewTag(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") addNew(); }}
          placeholder="new tag..."
          style={{
            background: D.currentLine,
            border: `1px solid ${D.comment}`,
            color: D.fg,
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 11,
            padding: "2px 6px",
            outline: "none",
            borderRadius: 2,
            flex: 1,
            minWidth: 0,
          }}
        />
        <button
          onClick={addNew}
          style={{
            background: "transparent",
            border: `1px solid ${D.green}`,
            color: D.green,
            cursor: "pointer",
            fontSize: 11,
            fontWeight: 700,
            padding: "0 6px",
            borderRadius: 2,
            fontFamily: "JetBrains Mono, monospace",
          }}
        >+</button>
      </div>
      <button
        onClick={() => { onSave(code, Array.from(selected)); onClose(); }}
        style={{
          background: D.purple,
          border: "none",
          color: D.bg,
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 700,
          padding: "3px 12px",
          borderRadius: 3,
          fontFamily: "JetBrains Mono, monospace",
          width: "100%",
        }}
      >确定</button>
    </div>
  );
}

/* ── TagArea: chips with hover-to-delete + editor dropdown ── */
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
        : <span style={{ color: D.comment, fontSize: 10 }}>+tag</span>
      }
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
