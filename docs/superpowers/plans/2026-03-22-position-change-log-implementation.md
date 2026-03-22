# 持仓变更记录 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 记录每只股票的 shares/cost 变化，在 StockDrawer 底部展示最近变更历史

**Architecture:**
- SQLite 新表 `position_change_log` 存储变更记录
- 主动比对模式：写入 `monitor_watchlist` 前检查 shares/cost 是否有变，有变才写日志
- Web API 路由 `/api/position-change-log` 提供查询接口
- StockDrawer 底部新增变更历史 section，默认展示 3 条，可展开至 20 条

**Tech Stack:** TypeScript (Next.js API + React), Python (db.py, futu_position_sync.py), SQLite

---

## File Map

```
src/sim_trading/db.py                    # 新增 position_change_log 建表 SQL
src/sim_trading/futu_position_sync.py     # sync 时主动比对 shares/cost 并记录
web/app/api/position-change-log/route.ts # 新增 GET API 路由
web/app/components/StockDrawer.tsx        # 底部新增变更历史 section
```

---

## Task 1: Add `position_change_log` table to db.py

**Files:**
- Modify: `src/sim_trading/db.py` (在 `SCHEMA` 字符串的 `daily_kline` 表定义之后插入新表)

- [ ] **Step 1: 在 SCHEMA 中插入新表定义**

在 `src/sim_trading/db.py` 的 SCHEMA 字符串中，找到 `-- 日K线缓存` 注释块之后、`"""` 结束之前，插入：

```python
--- 持仓变更记录
CREATE TABLE IF NOT EXISTS position_change_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  shares_from INTEGER,
  shares_to INTEGER,
  cost_from REAL,
  cost_to REAL
);
CREATE INDEX IF NOT EXISTS idx_pcl_symbol ON position_change_log(symbol);
CREATE INDEX IF NOT EXISTS idx_pcl_ts ON position_change_log(ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pcl_unique
  ON position_change_log(symbol, ts, source, shares_from, shares_to, cost_from, cost_to);
```

- [ ] **Step 2: 验证建表 SQL 语法正确**

在项目根目录运行：
```bash
poetry run python -c "
import sqlite3
conn = sqlite3.connect(':memory:')
schema = open('src/sim_trading/db.py').read()
# 提取 SCHEMA 中的建表语句
import re
m = re.search(r'SCHEMA = \"\"\"(.*?)\"\"\"', schema, re.DOTALL)
print('SCHEMA found:', bool(m))
"
```
Expected: 输出 `SCHEMA found: True`

- [ ] **Step 3: Commit**
```bash
git add src/sim_trading/db.py && git commit -m "feat: add position_change_log table to SCHEMA"
```

---

## Task 2: Create `position_change_log` GET API route

**Files:**
- Create: `web/app/api/position-change-log/route.ts`

**参考已有 API 风格**（见 `web/app/api/config/route.ts`）：
- 使用 `better-sqlite3` 连接 `SIM_DB_PATH`
- 返回 `{ results: [...] }` 格式
- 参数校验用 `URLSearchParams`

- [ ] **Step 1: 创建 API route 文件**

`web/app/api/position-change-log/route.ts`:

```typescript
import { NextResponse } from "next/server";
import Database from "better-sqlite3";

const SIM_DB_PATH = join(process.cwd(), "..", "..", "src", "data", "sim_trading.db");

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol");
  const limit = Math.min(Number(searchParams.get("limit")) || 20, 100);

  if (!symbol) {
    return NextResponse.json({ error: "symbol is required" }, { status: 400 });
  }

  const db = new Database(SIM_DB_PATH, { readonly: true });
  try {
    const rows = db.prepare(`
      SELECT ts, source, shares_from, shares_to, cost_from, cost_to
      FROM position_change_log
      WHERE symbol = ?
      ORDER BY ts DESC
      LIMIT ?
    `).all(symbol, limit) as {
      ts: string;
      source: string;
      shares_from: number | null;
      shares_to: number | null;
      cost_from: number | null;
      cost_to: number | null;
    }[];

    return NextResponse.json({ results: rows });
  } finally {
    db.close();
  }
}
```

- [ ] **Step 2: 添加缺失的 `join` import**

```typescript
import { join } from "path";
```

- [ ] **Step 3: 测试 API（手动验证）**

启动 web dev server 后测试：
```bash
curl "http://localhost:3120/api/position-change-log?symbol=HK09988&limit=5"
```
Expected: `{"results":[]}`（表存在但无数据，不报错）

- [ ] **Step 4: Commit**
```bash
git add web/app/api/position-change-log/route.ts && git commit -m "feat: add GET /api/position-change-log route"
```

---

## Task 3: Integrate change recording into `/api/config` POST update

**Files:**
- Modify: `web/app/api/config/route.ts:726-731` (在 `UPDATE monitor_watchlist` 之前插入比对逻辑)

**修改位置**：在 `case "update":` 的 DB 分支中，`db.prepare(UPDATE ...)` 之前。

- [ ] **Step 1: 在 UPDATE 之前插入比对和写日志逻辑**

在 `const nowTs = Math.floor(Date.now() / 1000);` 之后、`db.prepare(UPDATE monitor_watchlist` 之前，插入：

```typescript
        // ── 持仓变更记录 ──
        const oldShares = existing.shares;
        const oldCost = existing.cost;
        const newShares = shares;
        const newCost = cost;
        const sharesChanged = oldShares !== newShares;
        const costChanged = oldCost !== newCost;
        if (sharesChanged || costChanged) {
          const nowIso = new Date().toISOString();
          db.prepare(`
            INSERT OR IGNORE INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
          `).run(code, nowIso, "manual",
            oldShares != null ? oldShares : null,
            newShares != null ? newShares : null,
            oldCost != null ? oldCost : null,
            newCost != null ? newCost : null);
        }
```

**关键点**：
- 用 `db.prepare(...).run(...)` 而不是 `exec`，因为 `db` 处于事务中
- `INSERT OR IGNORE` + 唯一索引防止重复写入
- `oldShares !== newShares` 和 `oldCost !== newCost` 使用严格不等比对（处理 null 情况）

- [ ] **Step 2: 验证 TypeScript 不报错**

```bash
cd web && npx tsc --noEmit 2>&1 | head -30
```
Expected: 无新增错误

- [ ] **Step 3: Commit**
```bash
git add web/app/api/config/route.ts && git commit -m "feat: record position changes on shares/cost update in /api/config"
```

---

## Task 4: Integrate change recording into `futu_position_sync.py`

**Files:**
- Modify: `src/sim_trading/futu_position_sync.py`

**参考 futu_position_sync.py 中的 `sync_live_state` 函数**（约 line 100-180），在 `UPDATE live_state` 之后插入比对逻辑。

- [ ] **Step 1: 找到 sync 写入 monitor_watchlist 的位置**

在 `futu_position_sync.py` 中找到写入 `monitor_watchlist` 的 `INSERT OR REPLACE` 或 `UPDATE` 语句，在该语句之后插入：

```python
# ── 持仓变更记录 ──
old = _get_watch_row(conn, code)  # 读取旧值
if old:
    old_shares = old.get("shares")
    old_cost = old.get("cost")
    if (old_shares != shares or old_cost != cost) and (shares is not None or cost is not None):
        now_iso = datetime.now().isoformat(timespec="seconds")
        conn.execute("""
            INSERT OR IGNORE INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (code, now_iso, "sync",
               old_shares, shares if shares is not None else None,
               old_cost, cost if cost is not None else None))
```

需要添加辅助函数 `_get_watch_row`：

```python
def _get_watch_row(conn, code):
    """从 monitor_watchlist 读取单条记录，返回 dict 或 None。"""
    try:
        row = conn.execute(
            "SELECT shares, cost FROM monitor_watchlist WHERE symbol = ?",
            (code,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
```

- [ ] **Step 2: 添加 datetime import**

文件顶部如有 `from datetime import datetime` 则跳过；如无则添加。

- [ ] **Step 3: 验证 Python 语法**

```bash
poetry run python -m py_compile src/sim_trading/futu_position_sync.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 4: Commit**
```bash
git add src/sim_trading/futu_position_sync.py && git commit -m "feat: record position changes on futu sync"
```

---

## Task 5: Add ChangeHistory section to StockDrawer

**Files:**
- Modify: `web/app/components/StockDrawer.tsx` (在 `</div>` 闭合标签之前、`{/* Toast */}` 之后)

**参考 StockDrawer 现有 section 风格**（如"交易计划" section），使用内联 style（与现有代码一致），Dracula 主题色。

- [ ] **Step 1: 在 StockDrawer 中添加 ChangeHistory section**

在 `StockDrawer.tsx` 的 JSX 中找到「交易计划」section 末尾（`</div>` 包裹的最后一行），在其后、Drawer 内容区 `</div>` 之前插入：

```tsx
          {/* Section 5: 变更历史 */}
          <ChangeHistorySection symbol={symbol!} />
```

- [ ] **Step 2: 创建 ChangeHistorySection 组件**

在 `StockDrawer.tsx` 文件末尾（`export function StockDrawer` 之后）、`export { EditableCell, Toast }` 之前，插入：

```tsx
/* ── ChangeHistorySection ── */
function ChangeHistorySection({ symbol }: { symbol: string }) {
  const [expanded, setExpanded] = useState(false);
  const [logs, setLogs] = useState<ChangeLogEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const limit = expanded ? 20 : 3;

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    fetch(`/api/position-change-log?symbol=${encodeURIComponent(symbol)}&limit=${limit}`)
      .then((r) => r.json())
      .then((d) => setLogs(d.results ?? []))
      .finally(() => setLoading(false));
  }, [symbol, limit]);

  const sourceColor: Record<string, string> = {
    manual: D.cyan,
    import: D.purple,
    sync: D.yellow,
    type_change: D.orange,
  };
  const sourceLabel: Record<string, string> = {
    manual: "手动",
    import: "导入",
    sync: "同步",
    type_change: "类型转换",
  };

  function fmtVal(v: number | null | undefined): string {
    if (v == null) return "-";
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }

  return (
    <div style={{ borderTop: `1px solid ${D.currentLine}`, marginTop: 8, paddingTop: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ color: D.comment, fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>
          变更历史 {logs.length > 0 && `(${logs.length})`}
        </div>
        {logs.length > 3 && !expanded && (
          <button
            onClick={() => setExpanded(true)}
            style={{
              fontSize: 11, background: "none",
              border: `1px solid ${D.cyan}`, color: D.cyan,
              padding: "2px 10px", cursor: "pointer", borderRadius: 2,
              fontFamily: "JetBrains Mono, monospace",
            }}
          >展开更多</button>
        )}
      </div>

      {loading && <div style={{ color: D.comment, fontSize: 11 }}>加载中...</div>}

      {!loading && logs.length === 0 && (
        <div style={{ color: D.comment, fontSize: 11 }}>暂无变更记录</div>
      )}

      {logs.map((log) => {
        const color = sourceColor[log.source] ?? D.comment;
        const label = sourceLabel[log.source] ?? log.source;
        const dateStr = log.ts ? new Date(log.ts).toLocaleString("zh-CN", {
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
        }).replace(/\//g, "-") : "-";
        const sharesStr = `${fmtVal(log.shares_from)} → ${fmtVal(log.shares_to)}股`;
        const costStr = `${fmtVal(log.cost_from)} → ${fmtVal(log.cost_to)}`;

        return (
          <div key={log.ts + log.source} style={{
            display: "flex", gap: 8, fontSize: 12, padding: "4px 0",
            borderBottom: `1px solid ${D.currentLine}`,
          }}>
            <span style={{ color: D.comment, whiteSpace: "nowrap", minWidth: 110 }}>
              {dateStr}
            </span>
            <span style={{
              display: "inline-block",
              padding: "1px 6px",
              borderRadius: 3,
              fontSize: 10,
              fontWeight: 700,
              background: color,
              color: D.bg,
            }}>
              {label}
            </span>
            <span style={{ flex: 1, textAlign: "right", color: D.fg }}>
              {sharesStr}
            </span>
            <span style={{ minWidth: 130, textAlign: "right", color: D.fg }}>
              {costStr}
            </span>
          </div>
        );
      })}
    </div>
  );
}

type ChangeLogEntry = {
  ts: string;
  source: string;
  shares_from: number | null;
  shares_to: number | null;
  cost_from: number | null;
  cost_to: number | null;
};
```

**关键点**：
- 使用 `useState/useEffect` 在组件挂载时获取数据（与现有 StockDrawer 异步获取数据的方式一致）
- 不使用 `useMetrics`（变更记录不通过 MetricsProvider）
- 样式与现有 section 风格保持一致（Dracula 主题色、JetBrains Mono）
- `fmtVal` 处理 null → `-` 的显示

- [ ] **Step 3: 验证 TypeScript 不报错**

```bash
cd web && npx tsc --noEmit 2>&1 | grep -i "StockDrawer\|ChangeHistory" | head -20
```
Expected: 无相关错误

- [ ] **Step 4: 手动验证 UI**

启动 `npm run dev` 后：
1. 打开 http://localhost:3120
2. 点击任意持仓股票展开 StockDrawer
3. 滚动到底部，确认看到「变更历史」section（可能为空或显示记录）
4. 如有记录，确认来源标签颜色正确

- [ ] **Step 5: Commit**
```bash
git add web/app/components/StockDrawer.tsx && git commit -m "feat: add ChangeHistory section to StockDrawer"
```

---

## Execution Order

执行顺序：Task 1 → Task 2 → Task 3 → Task 4 → Task 5

（Task 1 建立数据层，Task 2 提供 API，Task 3/4 接入记录入口，Task 5 展示 UI）
