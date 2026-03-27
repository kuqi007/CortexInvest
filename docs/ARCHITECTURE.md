# 系统架构

## 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Dashboard (Next.js)                  │
│              Holdings / Watching / Alerts / Sim / Sector / Manage │
└─────────────────────────┬───────────────────────────────────┘
                          │ /api/metrics, /api/config, etc.
┌─────────────────────────▼───────────────────────────────────┐
│              market_data.json + sim_trading.db               │
│                 (Poller 写入, Web 只读)                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Poller (market_data_poller.py)             │
│         东方财富 API → market_data.json (30s 轮询)           │
│         汇率 hkdCnyRate → market_data.json                  │
└─────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              Notifier (stock_notifier.py)                   │
│         market_data.json → DeltaAlertEngine → 弹窗/SQLite   │
│         market_data.json → TradePlanEngine → 条件单触发      │
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

- Poller (`src/tools/market_data_poller.py`) 只写 `market_data.json`
- Web 通过 `/api/metrics` 读取，不直接获取市场数据
- 所有市场数据必须在 Python poller 中获取，Next.js API 路由绝不直接获取市场数据

### 2. 告警计算单一数据源

- `stock_notifier.py` 的 `DeltaAlertEngine` 是唯一告警计算引擎
- 产出写入 `sim_trading.db:alert_events` 表
- Web 前端 (`useAlerts`) 只读取展示，不做任何告警计算

### 3. 手续费单一计算源

- 交易成本只在 Python `SimulationEngine.calc_cost()` 中计算
- `position_manager.close_position` 写入 DB 的 `pnl` 字段已包含买卖双边手续费
- Web `/api/sim` 直接读 DB pnl，不重新计算

### 4. Monitor Config DB-first 双写

- `/api/config` 所有写操作先写 SQLite `monitor_watchlist`/`monitor_settings` 表
- 然后导出快照到 `monitor_config.json`
- Python 端（Poller/Notifier）仍读 JSON
- `CONFIG_SOURCE=json` 环境变量可强制 Web 端也读 JSON

### 5. 板块轮动独立于 Poller

- `sector_index_engine.py` 是独立 cron，不嵌入 poller 循环
- 数据源降级链: akshare (EM push2) → 腾讯财经 kline → 新浪财经
- 指数定义从 `monitor_watchlist.tags` + `tag_meta` 聚合
- Web 层 `/api/sector` 只读 SQLite，不调用外部 API

## 数据流

```
market_data.json ─────────────────────────────────────────────────→ /api/metrics ──→ Web UI
     ↑                                                              ↑
monitor_config.json ← → monitor_watchlist (DB)      alert_events (DB) ← DeltaAlertEngine
     ↑                            ↑                                  ↑
/api/config (Web)        /api/config (Web)              stock_notifier.py
```

## Config 数据职责分离

| 存储 | 写入方 | 内容 |
|------|--------|------|
| `market_data.json` | Poller (Python) | 个股行情 + 两市成交额 (marketTurnover) + 汇率 |
| `sim_trading.db` → `monitor_watchlist` | UI (/api/config) | 持仓配置 (主存储，DB-first) |
| `monitor_config.json` | UI (/api/config) 双写 | 持仓配置 JSON 快照 |
| `alert_config.json` | UI (/api/config) | 告警规则 (above/below) |
| `sim_trading.db` → `alert_events` | Notifier (Python) | 告警事件流 |
| `trade_plans.json` | UI + TradePlanEngine | 条件单 |
| `sim_trading.db` → `sector_*` | sector_index_engine (Python) | 板块排名 + 自定义指数 |

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
| `monitor_config.json` | 持仓配置 |
| `alert_config.json` | 告警规则 |
| `trade_plans.json` | 条件单 |
| `sim_trading.db` | SQLite 数据库 |
| `market_data.json` | 最新行情快照 |
| `l2_strategy_signals.json` | L2 信号 + session 上下文 |
| `signal_rules.json` | 信号规则配置 |
| `l2_strategy_config.json` | L2 策略参数 |
| `sector_config.json` | 板块告警规则 |
| `morning_briefing.json` | 早间市场简报 |
| `daily_summary.json` | 每日信号日报 |

### 不需要提交的（临时/派生）

| 文件 | 说明 |
|------|------|
| `sim_trading.db-shm` / `sim_trading.db-wal` | SQLite WAL 临时文件 |
| `archive/` | 历史归档目录 |
