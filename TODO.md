# TODO

## Frontend

- [ ] `/sim` 页面交易计划区域：order 有 `indicators` 字段时，在 label 后追加彩色 tag 显示指标条件
  - 示例: `[RSI>30]` `[RSI<70]` `[MACD收窄3日]` `[金叉]` `[死叉]` `[量比>1.5]`
  - 文件: `web/app/sim/page.tsx`
  - 依赖: 后端已完成（`TradePlanEngine` 支持 `indicators` 字段）

## Backend / Infrastructure

- [ ] `l2_strategy_daemon.py` 业务健康 watchdog：防止进程存活但业务卡死
  - 问题: 当前文件锁 + SQLite 心跳锁只能检测进程是否存活，无法检测 Futu API 调用阻塞、signals.json 写入停滞等业务层故障（2026-04-15 至 04-17 曾卡死 2 天）
  - 方案选项:
    1. 外部 watchdog 脚本：每分钟检查 `l2_strategy_signals.json` mtime / `daily_l2_digest` 最新数据时间，超 5 分钟则 `kill -9` + 重启
    2. Daemon 自检：主循环内增加写入健康检查，连续 N 个周期无数据写入则主动 `sys.exit(1)`，由 systemd/supervisord 重启
    3. 定时强制重启：每晚 20:00 自动 `kill -9` + 重启
  - 推荐: 方案 1（外部 watchdog 脚本）+ 方案 2（daemon 内自检）双保险
  - 相关文件: `src/tools/l2_strategy_daemon.py`, `src/tools/monitor_lock.py`
