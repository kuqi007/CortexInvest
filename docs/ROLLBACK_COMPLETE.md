# Turso 迁移回滚完成报告

## 执行时间
2026-04-07

## 回滚内容

### ✅ 已完成清理

1. **Turso 服务停止**
   - Web 服务已停止并重新启动
   - Turso 环境变量已清理

2. **代码回滚**
   - ✅ `web/app/lib/db.ts` - 恢复为本地 SQLite 路径
   - ✅ `web/app/api/metrics/route.ts` - 移除 Turso 读取逻辑，修复重复 `db.close()` bug
   - ✅ `web/app/lib/turso.ts` - 已删除
   - ✅ `src/sim_trading/db_turso.py` - 已删除
   - ✅ `scripts/sync_to_turso*.py` - 已删除
   - ✅ `scripts/check_turso_data.py` - 已删除

3. **配置文件清理**
   - ✅ `.env.turso` - 已删除
   - ✅ `web/.env.local` - 已删除
   - ✅ `start_with_turso.sh` - 已删除
   - ✅ `TSURO_MIGRATION.md` - 已删除
   - ✅ 临时脚本 - 已删除

4. **OneDrive 冲突解决**
   - ✅ 本地数据库路径：`~/.ai-investor-data-local/sim_trading.db`
   - ✅ Cron 定时同步：每2分钟从 OneDrive 复制生产机数据
   - ✅ 解决方案文档：`ONEDRIVE_SYNC_SOLUTION.md`

## 当前架构

```
┌─────────────────┐
│  Web 前端       │
│  (Next.js)      │
└────────┬────────┘
         │
         │ 读取
         ▼
┌─────────────────┐
│ 本地 SQLite     │  ◀── 主数据源
│ ~/.ai-investor  │
└────────┬────────┘
         │
         │ Cron 每2分钟复制
         ▼
┌─────────────────┐
│ OneDrive        │
│ (生产机数据)    │
└─────────────────┘
```

## 验证结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Web 服务启动 | ✅ | http://localhost:3120 |
| 数据库连接 | ✅ | 本地 SQLite 正常 |
| Services 数量 | ✅ | 240 只股票 |
| Alerts (04-03) | ✅ | 82 条 |
| Cron 同步 | ✅ | 每2分钟自动同步 |

## 注意事项

1. **当前日期问题**：今天是 2026-04-07，但数据库只有 2026-04-03 的数据
   - 这是因为生产机（写入端）的数据是历史数据
   - 实际使用时会显示最新日期的数据

2. **定时同步**：
   - 已配置 Cron 任务，每2分钟从 OneDrive 同步
   - 如需立即同步，运行：`cp ~/OneDrive\ -\ Autodesk/ai-investor-data/sim_trading-ADSKX21K67LYQL.db ~/.ai-investor-data-local/sim_trading.db`

3. **如需恢复 Turso**：
   - 参考 git 历史记录 `git log --all --oneline | grep -i turso`
   - 或重新执行迁移步骤

## 总结

✅ **回滚成功** - 系统已恢复到纯本地 SQLite 架构
✅ **OneDrive 冲突已解决** - 使用本地副本 + 定时同步
✅ **无 Turso 依赖** - 移除所有云端数据库相关代码和配置
