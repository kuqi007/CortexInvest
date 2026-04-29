# Config API 用法

所有自选/持仓配置变更必须通过 `POST /api/config`，禁止直接修改 JSON 或 SQLite。

成功写入 `config.db` 的变更会进入 `config_audit_outbox`，由 `audit_flush` 异步刷写到 `src/data/audit/config_events.jsonl`（append-only 审计，不是配置回读源）。详见 [CLAUDE.md](../CLAUDE.md) 与 [json-to-db-audit 设计](superpowers/specs/2026-04-28-json-to-db-audit-log-design.md)。

## 端点

`POST http://localhost:3120/api/config`

## action: add / update

添加或更新一只股票：

```bash
curl -X POST http://localhost:3120/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "action": "add",
    "code": "002436",
    "data": {
      "name": "兴森科技",
      "type": "holding",
      "cost": 23.687,
      "shares": 8700,
      "star": true
    }
  }'
```

## action: batch

批量更新多只股票：

```bash
curl -X POST http://localhost:3120/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "action": "batch",
    "updates": [
      {"code": "002436", "data": {"type": "holding", "cost": 23.69, "shares": 8700}},
      {"code": "000338", "data": {"type": "holding", "cost": 26.45, "shares": 2000}}
    ]
  }'
```

## action: remove

移除股票：

```bash
curl -X POST http://localhost:3120/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "action": "remove",
    "codes": ["000078"]
  }'
```

## Python 调用

```python
import requests

requests.post("http://localhost:3120/api/config", json={
    "action": "add",
    "code": "002436",
    "data": {
        "name": "兴森科技",
        "type": "holding",
        "cost": 23.687,
        "shares": 8700,
        "star": True
    }
})
```

## data 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 股票名称 |
| `type` | string | `holding` / `watching` |
| `cost` | float | 成本价（holding 必填） |
| `shares` | int | 持仓数量（holding 必填） |
| `star` | bool | 是否星标 |
| `hidden` | bool | 是否隐藏 |
| `threshold_high` | float | 高阈值 |
| `threshold_low` | float | 低阈值 |
