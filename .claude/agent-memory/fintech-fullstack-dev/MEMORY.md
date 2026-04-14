# Agent Memory - Fintech Fullstack Dev

## Architecture Summary
- **Data pipeline**: Python poller (`src/tools/market_data_poller.py`) -> JSON file (`src/data/market_data.json`) -> Next.js API (`web/app/api/metrics/route.ts`) -> React frontend
- **Config**: `src/data/monitor_config.json` -- read/written by both Python and Next.js API
- **Market data source**: 东方财富 push2 API with token `fa5fd1943c7b386f172d6893dbfba10b`
- **Stock codes**: A-shares = 6-digit, HK = `HK` + 5-digit. `emMarket()` maps: 6xxx->1(SH), else->0(SZ), HK->116

## Known Issues (as of 2026-02-10)
- Stats bar up/down colors are swapped (Western convention instead of Chinese)
- Mixed currency P&L summation (RMB + HKD without FX conversion)
- No data staleness indicator when poller dies
- Dracula palette `D` object copy-pasted 4x (page.tsx, CommandPrompt.tsx, manage/page.tsx, globals.css)
- `Row` component defined inside `Home()` -- re-created every render
- Silent error swallowing in `fetchData` catch block
- No trading hours awareness in web frontend (Python CLI has `is_trading_hours()`)
- Stock code regex allows bare 5-digit codes which are invalid A-share codes

## API Quirks
- 东方财富 fields: f2=price, f3=pct, f4=change, f5=vol, f6=amount, f7=amp, f8=turnover, f10=vol_ratio, f12=code, f14=name, f15=high, f16=low, f17=open, f18=prevClose
- Poller uses atomic file write (write .tmp then rename) for safe concurrent reads
- `readFileSync` used in Next.js API routes (blocking but acceptable for single-user)

## Trading Hours Reference
- A-shares: 9:30-11:30, 13:00-15:00 (Beijing)
- HK: 9:30-12:00, 13:00-16:00 (HK time = Beijing time)
- HK pre-market auction: 9:00-9:30

## File Locations
- Monitor page: `web/app/page.tsx`
- Manage page: `web/app/manage/page.tsx`
- Metrics API: `web/app/api/metrics/route.ts` (reads from JSON file)
- Config API: `web/app/api/config/route.ts` (CRUD watchlist)
- Command hook: `web/app/hooks/useCommand.ts`
- Alert hook: `web/app/hooks/useAlerts.ts`
- Command parser: `web/app/utils/commandParser.ts`
- Python poller: `src/tools/market_data_poller.py`
- Python stock monitor (CLI): `src/tools/stock_monitor.py`
- Watchlist config: `src/data/monitor_config.json`

## 阳光电源 (300274) 分析要点 (2026-04-13)
- **业务结构**: 储能系统(41.81%, +49%) | 光伏逆变器(34.91%, +6.9%) | 新能源开发(18.57%, -21%)
- **利润桥接**: 报表归母134.61亿 | 扣非128.29亿 | 还原激励基金后147.75亿
- **关键风险**: Q4毛利率骤降12.91pct（22.95%），储能成本传导压力
- **AIDC进度**: 2026产品落地，2027批量交付；目标10-15亿收入；与英伟达SST方案契合
- **机构评级**: 中信目标价185元（买入），长江/开源预测2026E净利155亿
- **估值结论**: 当前价134元 ≈ 前瞻平台估值(155亿×18x)，安全边际不足，建议持有
- **SOTP公允价值**: ~77.6元（当前股价134元已大幅超出静态估值）
