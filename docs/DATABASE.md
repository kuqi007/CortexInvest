# SQLite 数据库

数据库：`src/data/sim_trading.db`

## 表清单

| 表 | 说明 | 保留期 |
|---|------|--------|
| `signals` | L2 信号归档 | 180 天 |
| `price_snapshots` | 30s 价格快照 (HK) | 180 天 |
| `session_snapshots` | 5 分钟 session 上下文 | 180 天 |
| `trades` | 已平仓交易 | 永久 |
| `daily_pnl` | 每日权益快照 | 永久 |
| `live_state` | 实时持仓 | - |
| `daily_kline` | 日 K 线缓存 | - |
| `param_versions` | 参数版本配置 | - |
| `alert_events` | 告警事件 | 30 天 |
| `trade_plan_events` | 交易计划触发事件 | 30 天 |
| `sector_rotation` | 板块每日排名 | 90 天 |
| `sector_daily` | 自定义指数日线 | 180 天 |
| `sector_alerts` | 主线告警 | 30 天 |
| `daily_l2_digest` | 日线微观结构聚合 | - |
| `monitor_watchlist` | 持仓配置主存储 | - |
| `monitor_settings` | 监控设置 | - |
| `tag_meta` | 标签元数据 | - |
| `futu_orders` | Futu 订单审计 | 永久 |

## 表结构

### `monitor_watchlist`

```sql
symbol TEXT PRIMARY KEY,   -- e.g. "HK09988", "000792"
name TEXT,
list_type TEXT,            -- "holding" or "watching"
cost REAL,
shares INTEGER,
lot INTEGER,
hidden INTEGER,            -- 0/1
star INTEGER,              -- 0/1
tags TEXT,                -- comma-separated
watch_price REAL,
watch_price_date TEXT,
created_at TEXT,
updated_at TEXT
```

### `live_state`

```sql
code TEXT PRIMARY KEY,
entry_price REAL,
shares INTEGER,
SL REAL,
TP REAL,
entry_time INTEGER,       -- Unix ms
daily_score REAL,
unrealized_pnl REAL,
atr_at_entry REAL,
param_version TEXT
```

### `trades`

```sql
id INTEGER PRIMARY KEY,
code TEXT,
entry_price REAL,
exit_price REAL,
shares INTEGER,
pnl REAL,                  -- 已含手续费
exit_reason TEXT,
param_version TEXT,
entry_time INTEGER,
exit_time INTEGER
```

### `alert_events`

```sql
ts INTEGER,                -- Unix ms
date TEXT,                 -- YYYY-MM-DD
symbol TEXT,
kind TEXT,
level INTEGER,
message TEXT,              -- stealth 简短格式
display TEXT,              -- 中文详细格式
change_pct REAL,
PRIMARY KEY (ts, symbol, message)
)
```

### `tag_meta`

```sql
tag TEXT PRIMARY KEY,
star INTEGER,              -- 0/1
watch INTEGER,             -- 0/1
baseline_value REAL,
created_at TEXT
```

## 索引

```sql
CREATE INDEX idx_alert_date ON alert_events(date);
CREATE INDEX idx_trades_code ON trades(code);
CREATE INDEX idx_live_state_code ON live_state(code);
```
