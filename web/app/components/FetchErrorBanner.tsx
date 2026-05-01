import { D } from "../theme";

export function FetchErrorBanner({
  fetchError,
  ts,
}: {
  fetchError: string | null | undefined;
  ts: number;
}) {
  if (!fetchError) return null;

  return (
    <div style={{ color: D.red, marginBottom: 6, fontWeight: 500 }}>
      [错误] 数据获取失败: {fetchError}
      {ts > 0 && (
        <span style={{ color: D.comment, fontWeight: 400 }}>
          {" "}— 显示过期数据 (上次更新:{" "}
          {new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })})
        </span>
      )}
    </div>
  );
}
