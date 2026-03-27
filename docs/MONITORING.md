# 盯盘系统

## 组件概览

| 组件 | 进程 | 用途 |
|------|------|------|
| Poller | `market_data_poller.py` | 采集行情数据，写入 `market_data.json` |
| Notifier | `stock_notifier.py` | 告警计算 + 弹窗 |
| L2 Daemon | `l2_strategy_daemon.py` | 模拟交易信号（Futu OpenD） |

启动：`./start_monitor.sh`

## Poller (`market_data_poller.py`)

- **数据源**: 东方财富 API（优先）+ 新浪财经（降级）
- **轮询间隔**: 30s（可配置）
- **写入**: `market_data.json`（个股行情 + marketTurnover + hkdCnyRate）
- **降级**: 东方财富不可用时 fallback 到新浪，价格刷新但无量比/换手率

### 降级保护

- **量比/换手率继承**: 新浪不提供时从上一轮继承
- **汇率继承**: 获取失败时继承上次有效值
- **孤儿进程防护**: `start_monitor.sh` 使用进程组 kill + `pkill -f`

## Notifier (`stock_notifier.py`)

读取 poller 输出，从不直接获取数据。

**Data flow**:
```
market_data.json + monitor_config.json + alert_config.json
  → DeltaAlertEngine + TradePlanEngine
  → stealth_dispatch → terminal-notifier
  → sim_trading.db:alert_events
  → web
```

## 分级通知 L1-L4

由 `star` + `type` + `hidden` 推导级别：

| Level | 匹配规则 | trigger | delta | cooldown | 通知方式 |
|-------|---------|---------|-------|----------|---------|
| L1 ★ | `star=true` | 4% | 3% | 5min | 弹窗+声音 |
| L2 | `holding & !star & !hidden` | 6% | 5% | 15min | 弹窗静默 |
| L3 | `watching & !star & !hidden` | 仅threshold | 仅threshold | 30min | 仅web日志 |
| L4 | `hidden` 或不在watchlist | - | - | - | 不通知 |

### 通知规则

- **变化驱动，非状态驱动**: 使用 `DeltaAlertEngine`，记录上次通知价格，只在显著变化时再次通知
- **首次触发**: `|日涨跌幅| >= trigger_pct`
- **再次触发**: `|price - last_notified_price| / last_notified_price >= delta_pct`
- **合并通知**: 同一轮检测的所有告警合并为 1~2 条 macOS 通知
- **Stealth 模式**: 通知标题伪装为 CI 监控系统
- **每日重置**: 每天 8:00 清除所有 delta 追踪状态

## L2 Signal 降噪

### 关闭 8 个 tick/session 级信号

| 信号 | 关闭理由 |
|------|---------|
| `capital_flow_spike` | 5 分钟资金脉冲，日内噪音 |
| `large_order` | HK 大票常态，非方向信号 |
| `order_book_imbalance` | 毫秒级噪音 |
| `volume_price_divergence` | 30 分钟窗口无统计意义 |
| `tick_imbalance` | 信噪比低 |
| `volume_accel_alert` | 纯日内交易工具 |
| `large_order_reversal` | v1 实盘惨案，信噪比最差 |
| `closing_surge` | 与中长线无关 |

### 升级 7 个日线信号到 L2 弹窗

`ma_bullish_align`, `ma_bearish_align`, `adx_trend_start`, `volume_breakout`, `support_breakdown`, `macd_golden_cross`, `macd_death_cross`

每天最多触发 1 次（480min 冷却）。

## L2 Daemon (`l2_strategy_daemon.py`)

- 每 3s tick（交易时段）
- `DailyIndicatorTracker.score()` → 0-100 分，6 个子维度
- `get_atr(code)` → 缓存在 `_ind[code]["atr"]` 中的 14 期 ATR
- 每 30 分钟从 Futu OpenD 刷新日 K 线（120 天）

## DailyIndicatorTracker 评分系统

`score(code, main_net_inflow_pct)` → 0-100 分，无 kline 数据时返回 `{"total": 0, "action": "WAIT"}`。

## 每日摘要生成

**收盘后** (16:05-16:15): `generate_daily_summary()` 写入 `daily_summary.json`

**早间简报** (8:25-8:35): `generate_morning_briefing()` 写入 `morning_briefing.json`

## TradePlanEngine 交易计划

每 tick 检查所有 `status=active` 的计划，支持：
- 到价买卖
- 移动止损（回落卖出）
- 反弹买入
- 成交额过滤
- 连续天数

触发后标记 `triggered=true`，写入 `trade_plan_events` SQLite 表。

## Settings (monitor_config.json → settings)

| Key | Default | Description |
|-----|---------|-------------|
| `l1_trigger_pct` | 4 | L1 首次触发阈值% |
| `l1_delta_pct` | 3 | L1 再次触发阈值% |
| `l1_cooldown_min` | 5 | L1 冷却时间 |
| `l2_trigger_pct` | 6 | L2 首次触发阈值% |
| `l2_delta_pct` | 5 | L2 再次触发阈值% |
| `l2_cooldown_min` | 15 | L2 冷却时间 |
| `l3_cooldown_min` | 30 | L3 冷却时间 |
| `portfolio_delta_pct` | 2 | 组合 P&L 变化阈值% |
| `poll_interval` | 30 | Poller 轮询间隔（秒） |

## Config Write Safety

- **DB 写入**: `/api/config` 通过 `better-sqlite3` 同步写入
- **JSON 快照**: 写 DB 后自动导出 JSON，原子写入（tmp → rename）
- **Poller 写入**: `market_data.json` 也使用 tmp → rename 原子写入
