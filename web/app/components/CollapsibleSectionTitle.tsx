import { D } from "../theme";

type SectionTitleVariant = "chevron" | "star";

export function CollapsibleSectionTitle({
  label,
  count,
  open,
  onToggle,
  opacity = 1,
  variant = "chevron",
}: {
  label: string;
  count: number;
  open?: boolean;
  onToggle?: () => void;
  opacity?: number;
  variant?: SectionTitleVariant;
}) {
  const collapsible = variant === "chevron" && open !== undefined && onToggle;

  return (
    <div
      style={{
        color: D.comment,
        padding: "4px 0 1px",
        cursor: collapsible ? "pointer" : "default",
        userSelect: "none",
        opacity,
      }}
      onClick={collapsible ? onToggle : undefined}
    >
      {variant === "star" ? (
        <span style={{ color: D.yellow }}>★</span>
      ) : (
        <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span>
      )}
      {" "}# ── {label} ({count}) ──
    </div>
  );
}
