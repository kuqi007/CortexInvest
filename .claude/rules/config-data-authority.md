# Config Data Authority — ABSOLUTE PRIORITY

## The Rule

**所有持仓/自选的增删改必须走 `POST /api/config`，禁止直接编辑 JSON 或 DB。**

## Data Flow (Single Source of Truth)

```
POST /api/config
  ↓
config.db: monitor_watchlist / monitor_settings / alert_rules  ← 唯一运行态权威源
  ↓
config_audit_outbox → src/data/audit/config_events.jsonl
  ↓
Python 工具 + Web API → 只读 DB，禁止 runtime JSON fallback
```

## Why This Matters

- 直接改旧 JSON → runtime 不读取，不会生效
- 直接改 DB → 绕过 audit outbox，恢复链断裂
- 直接改两边 → 时序不确定，无法可靠追踪历史
- **走 API 保证业务变更和 audit outbox 同事务写入**

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

- ❌ 手动编辑 `src/data/monitor_config.json` — runtime 不读取，只能作为迁移/归档输入
- ❌ 直接写 `config.db` 的 `monitor_watchlist` 表 — 绕过 audit outbox
- ❌ 同时改 JSON 和 DB — 时序竞争，数据不一致

## Exceptions

- `monitor_config_db_migrator.py` — 一次性迁移/人工导出旧 `monitor_config.json` 快照
- `src/data/audit/*.jsonl` — audit flush 输出，只追加，不作为配置输入
- 紧急修复 — 如果 API 不可用，手动修复 DB 后必须补录 audit 或保留清晰操作记录
