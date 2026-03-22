# 持仓变更记录 — 设计文档

## 背景

用户希望通过记录持仓变化（股数、成本价）来追溯自己的操作行为。现有的 `monitor_watchlist` 表每次更新都是覆盖式写入，没有历史追溯能力。

## 需求

- 记录每只股票的 `shares` 和 `cost` 变化
- 区分变化来源：manual / import / sync / type_change
- 在 StockDrawer 个股详情页展示最近变更历史
- 保留 90 天

---

## 数据层

### SQLite 表: `position_change_log`

```sql
CREATE TABLE IF NOT EXISTS position_change_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol       TEXT NOT NULL,
  ts           TEXT NOT NULL,        -- ISO 8601, e.g. "2026-03-22T10:30:00"
  source       TEXT NOT NULL,        -- manual | import | sync | type_change
  shares_from  INTEGER,
  shares_to    INTEGER,
  cost_from    REAL,
  cost_to      REAL
);

CREATE INDEX IF NOT EXISTS idx_pcl_symbol ON position_change_log(symbol);
CREATE INDEX IF NOT EXISTS idx_pcl_ts     ON position_change_log(ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pcl_unique
  ON position_change_log(symbol, ts, source, shares_from, shares_to, cost_from, cost_to);
```

**null 处理**: `shares_from` / `shares_to` / `cost_from` / `cost_to` 允许 NULL，代表"无值"。展示时统一显示 `-`。

---

## 记录逻辑

### 核心原则

**主动比对**: 在所有写入 `monitor_watchlist` 的入口，写入前主动比对 `shares` 和 `cost` 的旧值，有变化才写入日志。无变化不写入。

### 入口点

| 入口 | source | 说明 |
|---|---|---|
| Web `/api/config` POST `update` (cost/shares) | `manual` | EditableCell 保存、promote/demote 等 |
| 批量导入脚本 | `import` | 解析 CSV/截图导入时逐条比对 |
| Futu 同步 | `sync` | `futu_position_sync.py` 每次 sync 比对持仓变化 |
| promote/demote（预留） | `type_change` | holding↔watching 类型转换（当前不改变 cost/shares，但保留此分类）|

### 比对算法

```python
def record_change(symbol, new_shares, new_cost, source):
    old = get_current(symbol)       # 从 monitor_watchlist 读取当前值
    if old.shares == new_shares and old.cost == new_cost:
        return                      # 无变化，不记录
    write_log(symbol, source, old.shares, new_shares, old.cost, new_cost)
    write_monitor_watchlist(symbol, new_shares, new_cost)  # 原有写入逻辑不变
```

---

## API

### GET /api/position-change-log

**Query 参数**:
- `symbol` (required): 股票代码，如 `HK09988`
- `limit` (optional): 默认 20，最大 100

**响应**:
```json
{
  "results": [
    {
      "ts":          "2026-03-22T10:30:00",
      "source":      "manual",
      "shares_from": 1000,
      "shares_to":   800,
      "cost_from":   32.3,
      "cost_to":     34.0
    }
  ]
}
```

**来源映射** (source → 展示标签 + 颜色):
| source | 标签 | 颜色 |
|---|---|---|
| `manual` | 手动 | 青色 cyan |
| `import` | 导入 | 紫色 purple |
| `sync` | 同步 | 黄色 yellow |
| `type_change` | 类型转换 | 橙色 orange |

---

## 清理策略

每天 08:00（与 alert_events 清理时机一致）执行：

```sql
DELETE FROM position_change_log WHERE ts < datetime('now', '-90 days');
```

---

## UI — StockDrawer 底部变更历史

### 位置

在 StockDrawer「交易计划」section 下方，作为最后一个 section。

### 默认展示

- 标题: `变更历史` + 记录总数（如 `变更历史 (5)`）
- 默认展示最近 **3 条**，按时间倒序
- 每行格式:

```
时间          来源    股数变化             成本变化
2026-03-22 10:30  手动      1000 → 800股     32.30 → 34.00
2026-03-20 14:15  同步      1200 → 1000股    - → -
```

- 成本和股数都是 `-` 时，该列显示 `-`
- 当 shares_from / shares_to 为 NULL 时，显示 `-`

### 展开更多

- 「展开更多」按钮：显示最近 20 条
- 总数超过 20 时显示: `共 N 条，还剩 M 条未显示`
- 无记录时显示: `暂无变更记录`

### 样式

- 变更历史 section 与上方交易计划 section 用 `border-top` 分隔
- 来源标签用小色块 + 文字，颜色对应表
- 时间左对齐，变化明细右对齐

---

## 实现清单

| 文件 | 改动 |
|---|---|
| `src/sim_trading/db.py` | 新增 `position_change_log` 建表 SQL |
| `web/app/api/config/route.ts` | POST `update` 时主动比对 shares/cost，有变则写入日志 |
| `src/tools/futu_position_sync.py` | sync 后主动比对 shares/cost，有变则写入日志 |
| `web/app/api/position-change-log/route.ts` | 新增 API 路由 (GET) |
| `web/app/components/StockDrawer.tsx` | 底部新增变更历史 section |
| 批量导入脚本 | 导入时逐条比对写入日志 |

---

## 依赖

- 现有 `monitor_watchlist` 表结构不变
- 现有 `/api/config` 写入逻辑不变（只新增比对和写日志）
- 日志写入与业务写入在同一个事务中，确保一致性
