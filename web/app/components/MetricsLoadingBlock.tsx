import { D } from "../theme";

export function MetricsLoadingBlock({ blink = false }: { blink?: boolean }) {
  return (
    <div style={{ color: D.comment, padding: "16px 0" }}>
      <span style={{ color: D.green }}>info</span> 正在加载数据...
      {blink && <span style={{ animation: "blink 1s step-end infinite" }}>...</span>}
    </div>
  );
}
