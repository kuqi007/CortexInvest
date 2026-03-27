# 模拟交易系统

## 概述

v2→v3 策略：日线评分驱动型交易（替代 v1 的 L2 秒级信号驱动）。

**v3 策略核心**:
- **开仓**: 日线综合评分 >= 60 分 + ADX >= 25（趋势过滤）+ RR >= 2.0（盈亏比过滤）
- **止损/止盈**: SL = max(price - 2×ATR, price×0.90), TP = price + 3×ATR
- **Trailing stop**: 价格创新高后 SL = highest_price - 3×ATR（只升不降）
- **平仓**: 止损/止盈实时检查 + 15:30 收盘评估（评分 < 46 → 平仓）
- **入场窗口**: 10:00-10:30，每天最多 1 只新开仓
- **日内例外**: 极强 L2 信号（confidence >= 0.85 且 score >= 60）可在窗口外入场
- **T3 纠偏**: 加手续费过滤 + min_hold 30 分钟检查；盈利时只收窄止损不卖出
- **冷却**: 同一股票平仓后 6 日历天内不再入场
- **最小交易额**: notional < 30,000 HKD 的交易不执行

**v2→v3 改进背景**: v2 在 walk-forward 回测中 131 笔交易手续费 45K，Optuna 优化后加 ADX/RR/trailing 过滤降至 33 笔，OOS +280K，avg Sharpe 2.84。

## 数据流

```
实时: L2 signals → signal_archiver → sim_trading.db
      DailyIndicatorTracker.score() → RealtimeSimEngine.tick() → ADX/RR filter → trailing stop → live_state / trades
回放: sim_trading.db → replay_runner → trades/daily_pnl
回测: kline_fetcher (腾讯财经) → daily_kline → scoring_backtester (Optuna) → 最优参数 → signal_rules.json
Web:  sim_trading.db → /api/sim → /sim 页面
```

## 模块

| 模块 | 职责 |
|------|------|
| `signal_archiver.py` | 实时归档 L2 信号 + 30s 价格快照到 SQLite |
| `realtime_engine.py` | **v3 实时引擎**: 日线评分入场 + ADX/RR 过滤 + trailing stop + cooldown_days |
| `broker.py` | **AbstractBroker 策略模式**: VirtualBroker / FutuBroker，支持 trailing_atr 透传 |
| `signal_mapper.py` | 4 层信号规则引擎 (Tier1→Tier2→Tier3→Tier4) |
| `position_manager.py` | 虚拟持仓管理 (lot-size 对齐, SL/TP/trailing-stop/max-hold 退出, atr_at_entry) |
| `scoring_backtester.py` | **v3 回测引擎**: Optuna walk-forward 优化 (5 窗口, 300 trials) |
| `kline_fetcher.py` | 腾讯财经日 K 线获取器，写入 daily_kline 表 |
| `simulation_engine.py` | HK 交易成本 + 流动性滑点 |
| `trade_analyzer.py` | 绩效分析: 胜率/Sharpe/最大回撤/Calmar/归因 |
| `replay_runner.py` | 历史回放入口 |
| `futu_trade_adapter.py` | Futu 模拟盘交易适配器 |
| `futu_position_sync.py` | Futu 持仓同步 |

## 评分维度 (`signal_rules.json → scoring_weights`)

| 维度 | 权重 | 计算逻辑 |
|------|------|---------|
| MACD | 29 | 金叉=20, hist扩张=18, 死叉=0, DIF/DEA零轴上方+2, ADX<20 时-5 |
| RSI | 8 | 50-65=15, 45-50=12, >80=3(极度超买), ADX<20 时-5 |
| MA排列 | 10 | 多头排列+站上MA20=20, 空头排列=0 |
| 主力资金 | 22 | 净流入>10%=20, 无数据时固定=8 |
| 量价配合 | 21 | 放量上涨=15, 缩量上涨=6, 放量下跌=2 |
| 支撑位 | 12 | 距支撑<2%=10, <5%=8, >10%=3 |

## 信号规则 (`src/data/signal_rules.json`)

- `tiers.1-4`: 14 种独立 + 2 种增强 + 6 种纠偏 + 11 种日志
- `daily_score`: v3 核心配置
- `scoring_weights`: 6 维度权重（Optuna 优化）
- `risk_control`: min_confidence=0.60, max_single_stock=25%, max_total_invested=80%
- `cost_model`: HK 市场费率 (佣金 0.03% min 3 HKD, 印花税 0.13%, 交易费 0.00565%, 结算费 0.002%)
- `lot_sizes`: 每只 HK 股的每手股数

## Daemon 架构

```
L2StrategyEngine (poll_once → L2 signals)
  ├── _daily_indicators: DailyIndicatorTracker (日K指标 + 评分)
  └── 传递引用 → RealtimeSimEngine
       ├── _broker: FutuBroker 或 VirtualBroker
       ├── tick() 每 3s 调用（交易时段）
       ├── _evaluate_entries() → 10:00-10:30 评分选股 + ADX/RR/cooldown_days 过滤
       ├── check_exits() → trailing_atr=3.0 动态抬升 SL
       ├── _process_signal_v2() → T3 过滤 + 日内例外
       ├── _evaluate_exits() → 15:30 收盘评估
       └── _persist_state() → live_state 表
```

## AbstractBroker 架构 (`broker.py`)

- **策略模式**: `RealtimeSimEngine` 通过 `AbstractBroker` 接口执行下单
- **VirtualBroker**: 纯委托 PositionManager，回放/测试用
- **FutuBroker**: Futu-first + 影子 PM
  - BUY: Futu 下单 → 等成交 → 写影子 PM
  - SELL: Futu 下单 → 等成交 → 关影子 PM
  - 风控退出: **无论 Futu 成败，always 关影子 PM**
- `_wait_for_fill(order_id)`: 轮询 `get_today_orders()`，0.5s 间隔，30s 超时

## Futu 模拟盘交易

- **连接**: 需要 Futu OpenD 运行（端口 11111）
- **下单**: `adapter.buy()` / `adapter.sell()`
- **HK tick size 取整**: `_round_to_hk_tick(price)` 自动对齐港交所 tick size
- **持仓同步**: daemon 每 tick 调用 `FutuPositionSync.sync_live_state()`
- **限制**: 盘后市价单在下个交易日开盘成交；15 orders/30s 限频

## 每日收盘评估 (`_evaluate_exits`)

15:30 收盘时评估所有持仓：
- score < 46 → 平仓
- 检查止损/止盈条件

## 运行命令

```bash
# 回放
uv run python -m src.sim_trading.replay_runner
uv run python -m src.sim_trading.replay_runner --version v2_test

# 回测
uv run python -m src.sim_trading.kline_fetcher
uv run python -m src.sim_trading.kline_fetcher --codes HK09988,HK00700
uv run python -m src.sim_trading.scoring_backtester

# 重启 daemon
pkill -f l2_strategy_daemon
nohup uv run python src/tools/l2_strategy_daemon.py >> logs/l2_daemon_out.log 2>&1 &
tail -f logs/l2_daemon.log | grep rt_sim
```

## 测试

```bash
uv run pytest src/sim_trading/test_sim_trading.py -v  # 113 tests
```
