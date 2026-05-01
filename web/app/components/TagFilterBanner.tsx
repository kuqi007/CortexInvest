import { tagColor } from "../lib/tag-utils";
import { D } from "../theme";

export function TagFilterBanner({
  tag,
  onClear,
}: {
  tag: string | null;
  onClear: () => void;
}) {
  if (!tag) return null;

  return (
    <div
      style={{
        padding: "4px 8px",
        backgroundColor: "#44475a",
        color: D.fg,
        fontSize: 12,
        marginBottom: 4,
        display: "flex",
        alignItems: "center",
        gap: 8,
        borderRadius: 3,
      }}
    >
      <span>
        筛选: <span style={{ color: tagColor(tag), fontWeight: 500 }}>{tag}</span>
      </span>
      <button
        type="button"
        aria-label="清除筛选"
        style={{
          cursor: "pointer",
          color: D.red,
          fontWeight: 500,
          background: "transparent",
          border: "none",
          padding: 0,
          font: "inherit",
        }}
        onClick={onClear}
      >
        x
      </button>
    </div>
  );
}
