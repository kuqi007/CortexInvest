# AI 股票监控系统

Python 3.13 + uv, Next.js 15, SQLite。行情轮询/告警/模拟交易/Web Dashboard。

## 规则

- **本地数据优先**: 查行情/持仓/告警 → 读 `src/data/market_data.json` + SQLite，不爬外部
- **Poller 唯一生产者**: Web/API 只读，不写行情数据
- **告警单一数据源**: `stock_notifier.py` DeltaAlertEngine 是唯一计算引擎
- **Config DB-first 双写**: API 写 DB → 导 JSON，Python 读 JSON

## 风控硬线（不得放松）

`max_single_stock_pct=25%, max_total_invested_pct=80%, position_pct=25%, max_new_positions_per_day=1, min_notional=30000, min_hold_minutes=30`

## 命令

```
./start_ai_investor_full.sh                                    # 启动
uv run pytest src/sim_trading/test_sim_trading.py -m smoke -q  # 冒烟 14 tests <1s
uv run pytest src/sim_trading/test_sim_trading.py -v           # 全量 138 tests
cd web && npm run dev                                          # Web :3120
```

## 告警 L1-L4

L1(star,4%,3%,弹窗+声) → L2(holding,6%,5%,弹窗) → L3(watching,threshold,仅web) → L4(hidden,不通知)

## 关键文件

`src/tools/market_data_poller.py` `src/tools/stock_notifier.py` `src/tools/l2_strategy_daemon.py` `src/sim_trading/broker.py` `src/sim_trading/position_manager.py` `start_ai_investor_full.sh`
