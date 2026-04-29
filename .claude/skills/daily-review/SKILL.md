---
name: daily-review
description: 收盘复盘：汇总今日板块表现、持仓盈亏、告警事件
---

# 收盘复盘

## ⚠️ 重要：数据来源区分

| 数据源 | 类型 | 复盘时是否使用 |
|--------|------|---------------|
| `config.db:monitor_watchlist` | 用户手动录入的持仓/自选 | ✅ **主要来源** |
| `trading.db:price_snapshots` | 实时行情 | ✅ 用于计算浮盈浮亏 |
| `trading.db:alert_events` | 真实行情告警（DeltaAlertEngine） | ✅ 复盘参考 |
| `trading.db:trades` | **纯模拟交易**（FutOpenD撮合） | ❌ **复盘不看** |
| `trading.db:live_state` | **模拟持仓** | ❌ **复盘不看** |
| `.claude/notes/` | AI 历史分析 | ✅ 参考历史决策 |

**规则：复盘只看真实账户，不查 trading.db 的模拟成交记录。**

## 步骤

1. **读取今日告警事件**
   从 `trading.db:alert_events` 表筛选今日数据（注意：这是真实行情告警，不是模拟交易数据）

2. **读取持仓状态**
   从 `trading.db:price_snapshots` 读取当前行情，与 `config.db:monitor_watchlist` 持仓（list_type='holding'）对比计算盈亏

3. **板块分析**
   使用 `mx-data` 或 `eastmoney-financial-data` 工具查询：
   - 今日行业板块涨幅前5
   - 今日概念板块涨幅前5
   - 主力资金净流入前5板块

4. **生成复盘报告**
   输出格式：

   ```
   ## YYYY-MM-DD 收盘复盘

   ### 大盘概况
   上证/深证/创业板 涨跌幅，涨跌家数

   ### 板块热点
   TOP3 板块 + 驱动逻辑

   ### 持仓盈亏
   逐只列出持仓成本/现价/盈亏%（来源：config.db + trading.db:price_snapshots）

   ### 告警汇总
   今日触发的告警数量和级别分布

   ### 明日关注
   需要关注的股票/板块/事件
   ```

5. **真实账户成交（如有）**
   如果用户提供了真实账户成交信息，记录到 `.claude/notes/{CODE}.md` 或手动更新 config.db

## 使用

用户说 "复盘"、"今日复盘"、"收盘复盘" 时触发。
