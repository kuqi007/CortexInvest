# 系统架构

## 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Dashboard (Next.js)                  │
│              Holdings / Watching / Alerts / Sim / Sector / Manage │
└─────────────────────────┬───────────────────────────────────┘
                          │ /api/metrics, /api/config, etc.
┌─────────────────────────▼───────────────────────────────────┐
│         trading.db:price_snapshots + config.db              │
│                 (Poller 写入, Web 只读)                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Poller (market_data_poller.py)           │
│    东方财富 API → trading.db:price_snapshots (30s 轮询)     │
│    汇率 hkdCnyRate → trading.db:market_turnover            │
│    audit JSONL 记录 mutation 历史，旧 JSON 不参与 runtime   │
└─────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              Notifier (stock_notifier.py)                   │
│  trading.db:price_snapshots → DeltaAlertEngine → 弹窗/SQLite │
│  trading.db:price_snapshots → TradePlanEngine → 条件单触发   │
└─────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              L2 Daemon (l2_strategy_daemon.py)             │
│         Futu OpenD → L2 signals → signal_archiver → db     │
│         DailyIndicatorTracker.score() → RealtimeSimEngine     │
└─────────────────────────────────────────────────────────────┘
```

## 核心原则

### 1. Poller 是生产者，UI 是消费者

- Poller (`src/tools/market_data_poller.py`) 只写 `trading.db:price_snapshots`
- Web 通过 `/api/metrics` 读取，不直接获取市场数据
- 所有市场数据必须在 Python poller 中获取，Next.js API 路由绝不直接获取市场数据
- `src/data/audit/*.jsonl` 是 mutation audit log；旧 JSON 文件不作为实时数据源或热备

### 2. 告警计算单一数据源

- `stock_notifier.py` 的 `DeltaAlertEngine` 是唯一告警计算引擎
- 产出写入 `trading.db:alert_events` 表
- Web 前端 (`useAlerts`) 只读取展示，不做任何告警计算

### 3. 手续费单一计算源

- 交易成本只在 Python `SimulationEngine.calc_cost()` 中计算
- `position_manager.close_position` 写入 DB 的 `pnl` 字段已包含买卖双边手续费
- Web `/api/sim` 直接读 DB pnl，不重新计算

### 4. Monitor Config DB-first

- `/api/config` 所有写操作先写 SQLite `monitor_watchlist`/`monitor_settings` 表
- 同事务写入 `config_audit_outbox`，由 audit flush 输出 JSONL
- Python 端（Poller/Notifier）和 Web 端都读 DB
- 旧 `monitor_config.json` 只允许迁移/人工归档工具使用，不作为 runtime fallback

### 5. 板块轮动独立于 Poller

- `sector_index_engine.py` 是独立 cron，不嵌入 poller 循环
- 数据源降级链: akshare (EM push2) → 腾讯财经 kline → 新浪财经
- 指数定义从 `monitor_watchlist.tags` + `tag_meta` 聚合
- Web 层 `/api/sector` 只读 SQLite，不调用外部 API

## 数据流

```
market_data_poller ──→ trading.db:price_snapshots / market_turnover ──→ /api/metrics ──→ Web UI
                         ↑
config.db:monitor_watchlist / monitor_settings / alert_rules ──→ stock_notifier.py
                         ↑
                    /api/config (Web)
                         ↓
               config_audit_outbox / trading_audit_outbox ──→ src/data/audit/*.jsonl
```

## Config 数据职责分离

| 存储 | 写入方 | 内容 |
|------|--------|------|
| `trading.db:price_snapshots/market_turnover` | Poller (Python) | 个股行情 + 两市成交额 + 汇率 |
| `config.db:monitor_watchlist` | UI (/api/config) | 真实持仓/自选 |
| `config.db:monitor_settings/alert_rules/tag_meta` | UI (/api/config, /api/sector) | 设置、价格告警、标签元数据 |
| `trading.db:alert_events` | Notifier (Python) | 告警事件流 |
| `trading.db:trade_plans/trade_plan_events` | UI + TradePlanEngine | 条件单和触发事件 |
| `trading.db:sector_*` | sector_index_engine (Python) | 板块排名 + 自定义指数 |
| `config_audit_outbox` / `trading_audit_outbox` → `src/data/audit/*.jsonl` | API/Python mutators + audit_flush | mutation 历史和 crash/replay 依据 |

## Poller 降级保护

`fetch_realtime_with_fallback` 返回 `(stocks, is_sina_fallback)`：

- **量比/换手率继承**: 新浪不提供 `turnover`/`volRatio`，降级时从上一轮继承
- **汇率继承**: `hkdCnyRate` 获取失败时继承上次有效值
- **孤儿进程防护**: `start_monitor.sh` 使用进程组 kill + `pkill -f` 兜底
- **日志按日期轮转**: `logs/l2_daemon_YYYY-MM-DD.log`

## Stock Code 前缀

| 前缀 | 市场 | 示例 | 东方财富 market code |
|------|------|------|---------------------|
| `HK` | 港股 | `HK09988` | 116 |
| `KR` | 韩国 | `KR005930` | (不支持实时行情) |
| (无) | A 股 | `000792`, `688676` | 1 (沪) / 0 (深) |

Web metrics API 去掉前缀后调用东方财富。见 `emMarket()` 和 `rawCode()` in `web/app/api/metrics/route.ts`。

## HK P&L FX 转换

行级和汇总级都乘 `fxRate`（来自 poller 的 `hkdCnyRate`，fallback 0.92）。`HoldRow` 中 `rowFx = s.id.startsWith("HK") ? fxRate : 1` 应用于 `mktVal`、`totalPnlRaw`、`dayPnl`。百分比字段不转换。

## Git 版本控制

### 需要提交的（含持久状态）

| 文件 | 说明 |
|------|------|
| `src/data/config.db` | 真实持仓/自选/设置/告警规则 |
| `src/data/trading.db` | 行情、告警事件、交易计划、模拟交易、板块/日报缓存 |
| `src/data/audit/*.jsonl` + manifest | mutation audit log |
| `stocks/` | 股票研究资料、研报、mx-data 缓存 |

### 不需要提交的（临时/派生）

| 文件 | 说明 |
|------|------|
| `sim_trading.db-shm` / `sim_trading.db-wal` | SQLite WAL 临时文件 |
| `archive/` | 历史归档目录 |
