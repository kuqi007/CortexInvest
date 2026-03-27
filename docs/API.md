# Web API 路由

## 路由清单

| 路由 | 方法 | 说明 |
|------|------|------|
| `/api/metrics` | GET | 合并行情 + 配置 + 告警 + P&L |
| `/api/config` | GET/POST | 持仓配置管理 |
| `/api/sim` | GET | 模拟交易数据 |
| `/api/sector` | GET/POST | 板块指数 |
| `/api/trade-plans` | GET/POST | 交易计划 |
| `/api/alerts` | GET | 告警事件（独立轮询） |

## /api/metrics

返回合并后的市场数据：

```json
{
  "services": [...],
  "ts": 1234567890,
  "tick": "2024-01-01 12:00:00",
  "loading": false,
  "settings": {...},
  "hkdCnyRate": 0.92,
  "alertEvents": [...],
  "marketTurnover": {...}
}
```

## /api/config

| Action | 说明 |
|--------|------|
| `add` | 添加股票到 watchlist |
| `update` | 更新 cost/shares/name 等字段 |
| `rm` | 删除股票（同步清理 alert_config） |
| `hide`/`unhide` | 切换隐藏状态 |
| `star`/`unstar` | 切换星标 |
| `promote` | watching → holding（需 cost/shares） |
| `demote` | holding → watching（清除 cost/shares） |

所有操作 DB-first 写入 `monitor_watchlist` 表，然后导出 JSON 快照。

## /api/sector

| Action | 说明 |
|--------|------|
| `create` | 创建自定义指数（操作 tags + tag_meta） |
| `update` | 更新指数 |
| `delete` | 删除指数 |
| `watch`/`star` | 切换 watch/star 状态 |
| `config` | 更新告警规则 |

## /api/trade-plans

| Action | 说明 |
|--------|------|
| `GET` | 所有计划 + 持仓数据 + lot_size |
| `POST create/update/delete/toggle/reset` | 管理交易计划 |
