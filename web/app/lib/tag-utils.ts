import type { CSSProperties } from "react";

// Dracula theme colors for tag chips
const TAG_COLORS = ["#8be9fd","#ff79c6","#50fa7b","#f1fa8c","#bd93f9","#ffb86c","#ff5555","#6272a4"];

export function tagColor(tag: string): string {
  let hash = 0;
  for (const ch of tag) hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

/** Visual hierarchy: risk > theme > descriptive/meta */
export type TagLayer = "risk" | "theme" | "meta";

const RISK_RE =
  /止损|止盈|风控|减仓|风险|警戒|高危|爆仓|退市|\*ST|ST\*|\bSL\b|\bSTOP\b/i;
const THEME_RE =
  /主线|题材|赛道|板块|周期|成长|价值|龙头|核心|红利|高股息|出海|重组|并购|国资|央企|国企|AI|新能源|半导体|消费|医药|军工|券商|地产|银行|有色|化工|通信|计算机|电子|汽车|电力|煤炭|钢铁|传媒|环保|机械|建筑|交运|农林|家电|纺服|轻工|公用|休闲|食品|饮料|中药|化学制药|生物|医疗器械|光伏|锂电|储能|机器人|算力|港股通|北交所|科创|创业板|沪深300|中证|ETF/i;

export function tagLayer(tag: string): TagLayer {
  const t = tag.trim();
  if (RISK_RE.test(t)) return "risk";
  if (THEME_RE.test(t)) return "theme";
  return "meta";
}

export function sortTagsForDisplay(tags: string[]): string[] {
  const rank: Record<TagLayer, number> = { risk: 0, theme: 1, meta: 2 };
  return [...tags].sort((a, b) => {
    const d = rank[tagLayer(a)] - rank[tagLayer(b)];
    if (d !== 0) return d;
    return a.localeCompare(b, "zh-CN");
  });
}

/** Row chip styles: risk pops, theme saturated, meta muted for scan speed */
export function layeredTagChipStyle(tag: string): CSSProperties {
  const layer = tagLayer(tag);
  const base = tagColor(tag);
  if (layer === "risk") {
    return {
      display: "inline-block",
      padding: "1px 6px",
      borderRadius: 3,
      fontSize: 10,
      marginRight: 3,
      color: "#282a36",
      background: base,
      whiteSpace: "nowrap",
      fontWeight: 800,
      border: "1px solid rgba(255, 85, 85, 0.95)",
      boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.2)",
    };
  }
  if (layer === "theme") {
    return {
      display: "inline-block",
      padding: "1px 6px",
      borderRadius: 3,
      fontSize: 10,
      marginRight: 3,
      color: "#282a36",
      background: base,
      whiteSpace: "nowrap",
      fontWeight: 600,
      border: "1px solid rgba(40, 42, 54, 0.35)",
      opacity: 0.96,
    };
  }
  return {
    display: "inline-block",
    padding: "1px 5px",
    borderRadius: 3,
    fontSize: 10,
    marginRight: 3,
    color: "#d8dae6",
    background: "#3d4054",
    whiteSpace: "nowrap",
    fontWeight: 500,
    opacity: 0.9,
    border: "1px solid #4d5168",
  };
}
