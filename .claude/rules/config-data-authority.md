# Config Data Authority — ABSOLUTE PRIORITY

## The Rule

**所有持仓/自选的增删改必须走 `POST /api/config`，禁止直接编辑 JSON 或 DB。**

## Data Flow (Single Source of Truth)

```
config.db: monitor_watchlist  ← 唯一权威源
  ↓
POST /api/config  → 写 DB + 导出 JSON 快照
  ↓
Python 工具 (poller/notifier/L2)  → load_watchlist_from_db() 读 DB，JSON fallback
  ↓
Web 前端 GET /api/metrics  → 读 JSON（由 DB 导出的快照）
```

## Why This Matters

- 直接改 JSON → Web API 的 `writeConfigSnapshot()` 会从 DB 导出 JSON，**覆盖你的手动修改**
- 直接改 DB → JSON 不同步，Web 前端看到旧数据
- 直接改两边 → 时序不确定，可能互相覆盖
- **走 API 保证 DB 和 JSON 原子性双写**

## Correct: Add/Update Holdings

```bash
# 加持仓
curl -X POST localhost:3120/api/config \
  -H 'Content-Type: application/json' \
  -d '{"action":"add","code":"HK03696","name":"英矽智能","type":"holding","cost":73.2,"shares":500,"lot":500,"star":true}'

# 改持仓（更新 cost/shares）
curl -X POST localhost:3120/api/config \
  -H 'Content-Type: application/json' \
  -d '{"action":"update","code":"HK02476","cost":330,"shares":100}'
```

## Wrong: Never Do This

- ❌ 手动编辑 `src/data/monitor_config.json` — 会被 DB 导出覆盖
- ❌ 直接写 `config.db` 的 `monitor_watchlist` 表 — JSON 不同步
- ❌ 同时改 JSON 和 DB — 时序竞争，数据不一致

## Exceptions

- `trade_plans.json` — 目前不走 API，可以直接编辑（JSON 是唯一源）
- `market_data.json` — 只由 poller 写入，其他进程只读
- 紧急修复 — 如果 API 不可用，手动修复后必须确认 DB 和 JSON 一致
