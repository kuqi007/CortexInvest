# Notification System Rules

## 市场交易时段

### A股

| 时段 | 时间 | 说明 |
|------|------|------|
| 开盘集合竞价 | 09:15 - 09:25 | 9:15-9:20 可撤单，9:20-9:25 不可撤单 |
| 开盘撮合 | 09:25 | 产生开盘价 |
| 连续竞价（上午） | 09:30 - 11:30 | |
| 午休 | 11:30 - 13:00 | |
| 连续竞价（下午） | 13:00 - 14:57 | |
| 收盘集合竞价 | 14:57 - 15:00 | 不可撤单，产生收盘价 |

### 港股

| 时段 | 时间 | 说明 |
|------|------|------|
| 开市前时段（竞价） | 09:00 - 09:30 | 9:00-9:15 输入期，9:15-9:20 对盘前期，9:20-9:22 随机对盘期，9:22-9:30 暂停期 |
| 连续交易（上午） | 09:30 - 12:00 | |
| 午休 | 12:00 - 13:00 | |
| 连续交易（下午） | 13:00 - 16:00 | |
| 收市竞价时段 | 16:00 - 16:10 | 16:00-16:01 参考价定价期，16:01-16:06 输入期，16:06-16:08 随机收市期，16:08-16:10 暂停期 |

### 代码时间窗口映射

| 模块 | A股 | 港股 | 用途 |
|------|-----|------|------|
| `is_any_market_open` | 09:15-11:30, 13:00-15:00 | 09:00-12:00, 13:00-16:10 | Notifier 主循环开关 |
| `_is_market_active` | 09:30-11:30, 13:00-15:00 | 09:30-12:00, 13:00-16:10 | Price freeze 检测 |
| 告警计算 (`_get_ashare_indicators`) | 09:25-15:20 | N/A（A股专用） | 日线指标计算 |
| L2 `_is_continuous_trading` | 09:30-11:30, 13:00-15:00 | 09:30-12:00, 13:00-16:00 | 逐笔采集（排除竞价） |

**关键区分**: 竞价时段的撮合成交是聚合撮合，不是逐笔连续成交。L2 逐笔采集只在连续交易时段运行，不覆盖开市前/收市竞价。但 Notifier 告警需要覆盖竞价时段，因为竞价价格波动（如跳空高/低开）需要触发告警。

## macOS 依赖

L1/L2 告警弹窗需要安装 `terminal-notifier`:

```bash
brew install terminal-notifier
```

## Tiered Notification (L1-L4)

Level is derived from `star` + `type` + `hidden` on each watchlist entry:

| Level | Match | trigger_pct | delta_pct | cooldown | Dispatch |
|-------|-------|-------------|-----------|----------|----------|
| L1 | `star=true` | 4% | 3% | 5min | macOS popup + sound |
| L2 | `type=holding & !star & !hidden` | 6% | 5% | 15min | macOS popup silent |
| L3 | `type=watching & !star & !hidden` | threshold only | threshold only | 30min | web log only |
| L4 | `hidden=true` or not in watchlist | - | - | - | no notification |

Resolution priority: `hidden → star → type`.

## Policy Table (code-level)

Defined in `stock_notifier.py` as `NOTIFY_POLICIES` dict. Each level is a strategy:

```python
{level: {"trigger_pct", "delta_pct", "cooldown_min", "big_move", "threshold", "dispatch"}}
```

User can override per-level via `monitor_config.json → settings`:
- `l1_trigger_pct`, `l1_delta_pct`, `l1_cooldown_min`
- `l2_trigger_pct`, `l2_delta_pct`, `l2_cooldown_min`
- `l3_cooldown_min`

Merge: `{**DEFAULT_POLICIES[level], **user_overrides}`

## Single Computation Source

- **Notifier** (`DeltaAlertEngine`) is the ONLY alert computation engine
- Writes results to `sim_trading.db:alert_events` table (dual format: `message` for terminal, `display` for web)
- Web `useAlerts` hook ONLY reads and displays, never computes
- Terminal notifications and web logs are always in sync

## Dispatch Rules

- L1 alerts → `stealth_dispatch(sound="default")` — always audible
- L2 alerts → `stealth_dispatch(sound="")` — silent popup
- L3 alerts → `write_alert_events()` only — no macOS notification
- L4 → skipped entirely (but still counted in portfolio P&L)

## Delta-Driven (Not State-Driven)

- First trigger: `|daily change%| >= trigger_pct`
- Re-trigger: `cooldown_min` 门控优先 — 冷却期内不检查 delta；冷却期后 `|price - last_notified_price| / last_notified_price >= delta_pct`
- Threshold (above/below): also protected by delta — first breach notifies, then needs delta change
- Price unchanged → no repeat notification

## L2 Signal Dedup

- `check_l2_signals()` 去重 key: `(code, strategy)` — 不用 `(code, display)`，display 含动态数值（涨跌幅、净额）每 tick 变化会导致去重失效
- `_load_seen_today_from_db()`: 从 `signals` 表读取 `ts <= watermark` 的已消费信号，防止 notifier 重启后吞掉未消费信号
- L2 daemon 重启恢复: `CooldownManager.restore_from_db()` + `DailyIndicatorTracker.restore_from_db()` 从 signals 表恢复内存去重状态

## DRIFT Tier 规则

- 跨越高 tier 时自动填充所有低位 tier（防止回落时触发 spurious alerts）
- Retrace 告警有 5 分钟 per-key cooldown（防止阈值附近振荡）
- Retrace level 跟随股票 star/type 配置，不再硬编码 level=2

## Daily Reset

- Triggers at **08:00** (not midnight) — avoids clearing HK after-hours events
- Clears: `_notified` dict, `_last_portfolio_pnl`
- Runs 30-day cleanup: `DELETE FROM alert_events WHERE date < (today - 30d)`
- Web reads only today's events via `WHERE date = ?` — auto-scoped per day

## Stealth Mode

- Notification titles rotate: "CI Pipeline Alert", "Deploy Monitor", "SRE Notification", "Build Status"
- Content uses stock name + change%, no monetary values
- Max 2 lines per notification + "+N more" summary
- Click opens `http://localhost:3120/alerts`

## Star Feature

- `star: boolean` field on watchlist entry
- Set via manage page ★/☆ toggle or `svc star/unstar <code>`
- Star works for both holdings and watchlist stocks
- Star stocks sort to top in dashboard sections
- Star stocks show ★ prefix in yellow
