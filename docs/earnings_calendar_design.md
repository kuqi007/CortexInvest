# 财报日历功能设计方案

## 1. 概述

实现一个财报日历功能，每天定时检查即将发布的财报，在财报发布前发飞书提醒，发布后搜索并解析财报，给出利好/利空预测和深度解析。

## 2. 已有组件分析

### 2.1 现有组件
- `src/tools/earnings_calendar.py` — 财报日历核心逻辑
- `src/tools/earnings_calendar_daemon.py` — 守护进程版本
- `src/tools/stock_monitor.py` — 飞书发送 (`feishu_send`)
- `src/tools/trading_calendar.py` — 交易日判断

### 2.2 核心功能
1. **日历获取**: 使用 akshare 从巨潮资讯网获取财报披露日期
2. **预判分析**: 基于财务指标（ROE、净利润增长率、营收增长等）扫描利好/利空条件
3. **LLM 补充**: 使用 OpenAI Compatible API 生成专业分析
4. **飞书通知**: 通过飞书卡片推送预警和分析结果
5. **缓存管理**: 避免重复推送

## 3. 功能模块

### 3.1 数据获取
- 使用 `akshare.stock_zh_a_disclosure_report_cninfo` 获取财报发布日历
- 支持全市场查询或指定股票列表查询
- 过滤年报、半年报、季报等关键词

### 3.2 预判分析（利好/利空）
评分机制（满分 ±10）:
- 净利润增长 >20%: +2.0
- 营收增长 >20%: +1.5
- ROE >15%: +1.5
- 每股经营现金流 >0.5元: +1.0
- 资产负债率 >70%: -2.0
- 净利润下降 <-20%: -2.5
- PE < 15: +0.5
- PE > 50: -1.0
- 机构评级买入: +1.0
- 机构评级卖出: -1.5

综合判定:
- score >= 3.0 → bullish (利好)
- score <= -2.0 → bearish (利空)
- 其他 → neutral (中性)

### 3.3 财报复盘（发布后）
- 获取历史财报数据
- 使用 LLM 生成深度分析
- 关键字判断: 超预期/增长/利好 → bullish; 低于预期/下滑/亏损 → bearish

### 3.4 飞书卡片格式
**预判预警**:
```
📈/📉/📊 财报预警 | {股票名}({代码}) | {期间}
📅 预计发布: {日期}
🎯 预判: 利好/利空/中性 (评分: {score})
• {条件1}
• {条件2}
...
**LLM预判:** (可选)
```

**财报复盘**:
```
📈/📉/📊 财报复盘 | {股票名}({代码}) | {期间}
📋 发布日期: {日期}
🎯 解读: 利好/利空/中性
{llm_summary}
```

## 4. Cron 定时任务设计

### 4.1 定时计划
- **交易时段 (08:00-15:00)**: 每 30 分钟检查一次
- **非交易时段**: 每 2 小时检查一次

### 4.2 推荐 Cron 表达式
```bash
# 每天 08:00-15:00 每30分钟运行一次
*/30 8-14 * * * cd /Users/zhul1/Documents/aiWorkspace/ai-investor && uv run python src/tools/earnings_calendar.py --alert

# 或者使用后台 daemon 方式
```

### 4.3 实现方式
两种方式可选:
1. **Daemon 模式**: `uv run python src/tools/earnings_calendar_daemon.py` — 常驻内存，每30分钟检查
2. **Cron CLI 模式**: 每30分钟调用一次 CLI

推荐使用 **Daemon 模式**，因为:
- 避免重复启动开销
- 内置单例锁
- 自动根据交易时段调整检查频率

## 5. 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| ALERT_BEFORE_DAYS | 3 | 提前N天预警 |
| ALERT_AFTER_HOURS | 4 | 发布后N小时分析 |
| CHECK_INTERVAL_SEC | 1800 | 交易时段检查间隔(30分钟) |
| LONG_CHECK_INTERVAL_SEC | 7200 | 非交易时段检查间隔(2小时) |

## 6. 数据流

```
Cron Trigger
    ↓
earnings_calendar_daemon.py (单例锁)
    ↓
EarningsCalendar.check_and_alert()
    ↓
┌─ refresh_calendar() → akshare API → 缓存
│
├─ 预判预警 (days_until <= 3)
│   ├─ get_stock_financial_metrics()
│   ├─ analyze_pre_earnings_bullish_bearish()
│   ├─ generate_llm_pre_earnings_analysis()
│   └─ send_feishu_alert() → 飞书卡片
│
└─ 财报复盘 (days_until <= 0)
    ├─ fetch_recent_earnings()
    ├─ generate_llm_post_earnings_analysis()
    └─ send_feishu_alert() → 飞书卡片
```

## 7. 文件结构

```
src/tools/
├── earnings_calendar.py      # 核心逻辑 (已有)
├── earnings_calendar_daemon.py  # 守护进程 (已有)
└── ...

data/
├── earnings_calendar_cache.json  # 财报日历缓存
├── earnings_history.json         # 分析历史记录
└── .earnings_calendar.lock      # 单例锁
```

## 8. 使用方式

### 8.1 启动 Daemon
```bash
cd ~/Documents/aiWorkspace/ai-investor
uv run python src/tools/earnings_calendar_daemon.py &
```

### 8.2 Cron 配置 (launchd 或 crontab)
```bash
# via launchd (推荐 macOS)
# 保存为 ~/Library/LaunchAgents/com.hermes.earnings-calendar.plist

# via crontab
*/30 9-14 * * 1-5 cd /Users/zhul1/Documents/aiWorkspace/ai-investor && uv run python src/tools/earnings_calendar.py --alert >> logs/earnings.log 2>&1
```

### 8.3 手动测试
```bash
# 查看即将发布的财报
uv run python src/tools/earnings_calendar.py --refresh --days 7

# 执行预警检查
uv run python src/tools/earnings_calendar.py --alert
```

## 9. 依赖

- `akshare` — 财报数据获取
- `requests` — HTTP 请求
- `src.tools.stock_monitor.feishu_send` — 飞书通知
- `src.tools.openrouter_config.get_chat_completion` — LLM 分析
- `src.utils.llm_clients` — LLM 客户端

## 10. 注意事项

1. **单例锁**: 使用 `fcntl.flock` 确保只有一个实例运行
2. **限流**: akshare API 调用间隔 0.1 秒避免被限
3. **缓存**: 使用 JSON 文件缓存避免重复请求
4. **去重**: 通过 `earnings_history.json` 标记已发送的预警
5. **自选股优先**: 优先检查 watchlist 中的股票