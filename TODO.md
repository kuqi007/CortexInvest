# TODO

## Frontend

- [ ] `/sim` 页面交易计划区域：order 有 `indicators` 字段时，在 label 后追加彩色 tag 显示指标条件
  - 示例: `[RSI>30]` `[RSI<70]` `[MACD收窄3日]` `[金叉]` `[死叉]` `[量比>1.5]`
  - 文件: `web/app/sim/page.tsx`
  - 依赖: 后端已完成（`TradePlanEngine` 支持 `indicators` 字段）

## Backend / Infrastructure

- [x] `l2_strategy_daemon.py` 业务健康 watchdog：防止进程存活但业务卡死
  - **已完成** (commit c00f450, 2026-04-19)
  - 实现内容:
    - `start_ai_investor_full.sh`: 外部 watchdog，5 分钟检查一次 signals.json mtime，连续 3 次超时则 kill-9 + 重启
    - `l2_strategy_engine.py`: 新增 `restore_state()` 用于 tracker 状态恢复，STALE_SEC/STALE_CONSECUTIVE 常量
    - `l2_strategy_daemon.py`: OpenD 连接失败时正确退出码
  - 相关文件: `src/tools/l2_strategy_daemon.py`, `src/tools/l2_strategy_engine.py`, `start_ai_investor_full.sh`
