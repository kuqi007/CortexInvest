---
name: stock-fundamental-analysis
description: Use when performing comprehensive stock fundamental analysis, evaluating a stock's investment value, or generating a multi-perspective research report. Triggers when user asks to analyze a stock, assess valuation, or run due diligence on a listed company.
---

> **Placeholder convention:** Replace `{股票名}` with the stock name, `{code}` with stock code, `{当前年份}` with current year in all queries and output paths.

# Stock Fundamental Analysis (Multi-Agent)

## Overview

Use 6 agents to perform comprehensive stock fundamental analysis: 5 analysis agents (financials, business, valuation, capital flow, risk) run in parallel + 1 quality reviewer (you). Data sourced from 妙想 skills (mx-data, mx-search).

## Workflow

```
Phase 1: Data Gathering (mx-data + mx-search)
         ↓ (all data collected first)
┌────────────────────────────────────────────────────┐
│  Agent 1     Agent 2     Agent 3   Agent 4  Agent 5 │
│ Financials  Business  Valuation  Capital   Risk     │
│                (run in parallel, single message)    │
└────────────────────────────────────────────────────┘
         ↓ (all complete)
Phase 3: Agent 6 = You (Quality Audit + Final Report)
```

## Phase 1: Data Gathering

Query real-time data via 妙想 skills **before** launching agents. Collect **all** data first, then pass relevant subsets to each agent as context. **Agents must NOT make additional API calls** — they work only from provided context.

**IMPORTANT:** Set `MX_APIKEY` env var. Run commands with `source ~/.zshrc && python3 ...`.

### Required Queries (run in parallel)

| Query | Skill | Purpose | Used By |
|-------|-------|---------|---------|
| `{股票名}最新价 涨跌幅 成交额 换手率 总市值` | mx-data | Market data | All |
| `{股票名}近三年年报净利润 营业收入 毛利率 净利率 ROE 每股净资产` | mx-data | 3-year financials | A1, A3 |
| `{股票名}经营活动现金流量净额 自由现金流 近三年` | mx-data | Cash flow quality | A1 |
| `{股票名}投资收益 公允价值变动收益 近三年` | mx-data | Profit source decomposition | A1 |
| `{股票名}近5日主力资金流向 超大单 大单` | mx-data | 5-day capital flow | A4 |
| `{股票名}近60日日K线 收盘价 成交量` | mx-data | Price history for technicals | A4 |
| `{股票名}市盈率TTM 每股收益EPS 每股净资产BPS` | mx-data | Valuation ratios + EPS | A3 |
| `{股票名}主营业务收入构成 分产品 分地区` | mx-data | Revenue breakdown | A2 |
| `{股票名}业绩预告` | mx-data | Earnings guidance | A1, A3 |
| `{股票名}总股本 流通股` | mx-data | Share structure for EPS calc | A3 |
| `{股票名}货币资金 现金及等价物 短期理财` | mx-data | Cash reserves for ex-cash PE | A3 |
| `{股票名}资产负债率 有息负债 利息覆盖倍数` | mx-data | Leverage & debt structure | A1, A5 |
| `{股票名}同行业可比公司 市盈率 市净率` | mx-data | Industry peer comparison | A3 |
| `{股票名}最新消息 研报 {当前年份}年` | mx-search | News & research | A2, A5 |
| `{股票名}业务进展 产品 竞争` | mx-search | Business developments | A2 |

## Phase 2: Launch 5 Analysis Agents in Parallel

**Send all 5 Agent tool calls in a single message** — Claude Code automatically runs them in parallel. Do NOT use `run_in_background` (that parameter does not apply to Agent tool). Wait for all 5 to complete before proceeding to Phase 3.

Provide each agent with **only the relevant subset** of Phase 1 data (see "Used By" column above).

### Agent 1: Financial Analyst

**Input:** Market data, 3-year financials, cash flow data, investment income data, earnings guidance, leverage data
**Output limit:** 800 words max — prioritize numbers and conclusions over narrative
**Analysis:**
1. Profitability trend (gross margin, net margin, ROE 3-year trend)
2. **Profit source decomposition (REQUIRED if investment income data available):**
   - Use Phase 1 data for 投资收益/公允价值变动收益 (do NOT query again)
   - Calculate: operating profit (营业利润 - 投资收益 - 公允价值变动) vs investment income (投资收益 + 公允价值变动)
   - Flag if investment income / net profit > 30% — earnings quality concern
   - Flag if investment income / net profit > 50% — earnings structure risk (profit NOT driven by operations)
   - Explicitly state: "利润增长主要由[经营性利润/投资收益]驱动"
   - If data unavailable: note "[数据不可用：投资收益数据缺失]" and skip this step
3. Earnings quality: CFO/NI ratio (经营现金流/净利润) — ratio > 1 is healthy, < 0.6 is concern
4. Growth assessment (YoY, QoQ, Q4 revenue inflection point)
5. ROE/ROA vs industry peers
6. Full-year profit forecast (use guidance midpoint; if no guidance, use 3-year average growth)
7. Dividend capacity (cash reserves, debt ratio from leverage data)

**Output:** Score X/10, financial metrics table, 3+ core findings, conclusion on earnings quality

### Agent 2: Business Model Analyst

**Input:** Revenue breakdown, product lines, news on business developments
**Output limit:** 800 words max
**Analysis:**
1. Revenue structure (concentration, geographic distribution)
2. Business model pros/cons (analyze synergies or competition between each revenue stream)
3. Competitive moats (regulatory, channel, brand, technology, data)
4. Key product pipeline and catalysts
5. New business progress (AI, internet healthcare, etc.)
6. Investment/portfolio contribution and sustainability

**Output:** Score X/10, business structure table with margins and risks, moat assessment, conclusion

### Agent 3: Valuation Analyst

**Input:** Price, market cap, PE TTM, BPS, EPS, financials, guidance, total shares, cash reserves, industry peer PE/PB
**Output limit:** 800 words max
**Analysis (must complete ALL 4 methods, score based on cross-validation):**

1. **PE valuation:**
   - Forward EPS = guidance net profit midpoint / total shares
   - Use industry peer average PE as anchor: apply {peer_avg × 0.8, peer_avg × 1.0, peer_avg × 1.2} → conservative/neutral/optimistic targets
   - If no peer data available, fallback to {15, 20, 25} (A-share market median reference)
   - Also calculate ex-cash PE = (market_cap - cash_reserves) / net_profit if cash > 30% of market cap

2. **PB valuation:**
   - BPS × {2.0, 3.0, 4.0} → conservative/neutral/optimistic targets
   - Compare current PB to industry range, note deviation percentage

3. **PEG valuation:**
   - Short-term PEG = PE_TTM / next_year_growth_rate (PEG < 1 = undervalued)
   - Long-term PEG = PE_TTM / 3-year_CAGR (reference only, not primary judge)
   - **Do NOT score down solely based on PEG** — PB/PE undervaluation can coexist with high PEG

4. **Peer comparison:** PE/PB vs comparable companies from Phase 1 data, calculate average deviation

**Scoring rule:** Final score must weigh PE + PB heavily (they reflect absolute cheapness). PEG is supplementary — a stock with PE 50% below industry and PB 60% below industry should score >= 7 even if PEG is elevated. Low PB is the strongest undervaluation signal.

**Output:** Score X/10, valuation comparison table (all 4 methods), target price table (3 scenarios per method), undervaluation conclusion with cross-method confidence level

### Agent 4: Capital Flow & Technical Analyst

**Input:** 5-day capital flow data, 60-day daily K-line (close price + volume), market cap
**Output limit:** 600 words max
**Analysis:**
1. Main force fund flow trend and intent (5-day aggregate)
2. Super-large vs large order divergence (institution vs retail)
3. Volume-price analysis (divergence signals, volume shrink on limit-up)
4. Limit-up quality assessment (if applicable)
5. Support/resistance levels: based on recent 60 trading days, identify 2 valid support and resistance levels
6. Short-term trend judgment: based on MA5/MA10/MA20 alignment and volume confirmation over past 20 days

**Output:** Score X/10, 5-day fund flow table with interpretation, technical levels (explicit price values), trend conclusion

### Agent 5: Risk Analyst

**Input:** All data (price action, financials, business model, capital flow, news, leverage data)
**Output limit:** 800 words max
**Analysis (4 risk categories — select applicable sub-items based on stock type):**
1. **Business risk:** Demand cyclicality, market concentration, revenue decline
2. **Policy risk:**
   - A股 (code prefix 6/0/3): 行业监管（反垄断、合规）、价格管控、行业补贴退出
   - 港股/出口型 (code prefix 0/2/3 HK): 关税、汇率、地缘政治
3. **Market risk:** Concept speculation, short-term overbought, limit-up reversal
4. **Financial risk:** Investment income volatility, leverage/debt structure (from leverage data), goodwill, asset quality

**Scoring guide for risk (IMPORTANT: higher score = MORE risk, opposite of other agents):**

| Score | Meaning | Description |
|-------|---------|-------------|
| 1-3 | Low risk | Solid fundamentals, stable earnings, no major catalysts for decline |
| 4-5 | Medium risk | Some concerns but manageable, typical market volatility |
| 6-7 | Medium-high risk | Multiple risks overlapping, concept speculation evident |
| 8-10 | High risk | Severe risk concentration, possible sharp correction |

**Output:** Risk score X/10 (越高风险越大), risk matrix table (severity + probability + priority), TOP 5 risks with triggers, prevention suggestions with actionable price levels, risk conclusion

## Phase 3: Quality Review & Final Report (Agent 6)

**You act as Agent 6** after all 5 agents complete. Do NOT launch a 6th subagent for this.

### Review Checklist

| Check | Criteria |
|-------|----------|
| Data accuracy | All agents use same Phase 1 source data, no fabricated numbers |
| Logic consistency | Conclusions across agents don't contradict (different time horizons OK) |
| Completeness | All core dimensions covered, risks comprehensive |
| Objectivity | Risk warnings sufficient, no excessive optimism or pessimism |
| EPS verification | Forward EPS = (guidance profit) / (total shares) — check arithmetic |
| PEG denominator | Confirm growth_rate source: next_year_guidance or 3-year CAGR |
| Score calibration | Valuation score reflects PE+PB undervaluation as primary signal, not penalized by PEG alone |
| Risk score direction | Confirm risk score is inverted (8/10 = high risk), composite uses (11 - risk) |
| Missing data | Note any steps skipped due to unavailable data; do not penalize agents for skipped steps |

**Common review issues to watch:**
- Valuation agent over-weighting PEG while ignoring strong PB undervaluation → adjust score upward
- Risk agent scoring too optimistic on concept stocks with >50% short-term gains → flag
- Financial agent ignoring non-recurring investment income → flag as earnings quality concern
- Financial agent NOT decomposing profit sources when investment income > 30% of net profit → flag as critical omission (unless data was unavailable)
- Agent 3 using hardcoded PE {20,25,30} without referencing peer data → flag and recalculate

**If contradictions found:** Note them in the final report. Different time-horizon views (short-term bearish + long-term bullish) are acceptable and should be presented as nuance.

### Final Report Structure

Write to file: `reports/{code}-fundamental-{YYYYMMDD}.md` (create `reports/` dir if needed)

```markdown
# {股票名}（{code}）基本面评估报告

## 一、盈利能力评估【Agent 1 | X/10】
[Key metrics table + 3 core findings + conclusion]

## 二、业务模式评估【Agent 2 | X/10】
[Business structure table + moat analysis + conclusion]

## 三、估值分析【Agent 3 | X/10】
[Valuation comparison + target prices (3 scenarios) + conclusion]

## 四、资金面评估【Agent 4 | X/10】
[Capital flow table + technical levels + trend conclusion]

## 五、风险评估【Agent 5 | X/10】（越高风险越大）
[Risk matrix + TOP 5 risks + prevention suggestions]

## 六、综合评分汇总
| 维度 | 评分 | 核心判断 |
|------|------|----------|
| 盈利能力 | X/10 | ... |
| 业务模式 | X/10 | ... |
| 估值水平 | X/10 | ... |
| 资金面 | X/10 | ... |
| 风险控制 | X/10 | ... |
| **综合评分** | **X/10** | (盈利 + 业务 + 估值 + 资金面 + (11-风险)) / 5 |

## 七、最终结论
- 估值判断：低估/合理/高估
- 目标价建议：短期（1-3月）/ 中期（6-12月）/ 长期（1-3年）
- 操作建议：买入/增持/持有/减持/卖出

## 八、投资逻辑
[3-5 key investment thesis points]

## 九、核心风险
[Top risks]

> 本报告仅供参考，不构成投资建议。股市有风险，投资需谨慎。
> 报告生成时间：{date}
> 数据来源：东方财富妙想API
> 分析Agent数量：6个（5个分析师 + 1个审核员）
```

## Important Rules

- **All data must come from Phase 1 妙想 API queries** — agents must never query API again or fabricate numbers
- **Launch all 5 analysis agents in a single message** — Claude Code will run them in parallel automatically. Do NOT use `run_in_background` parameter with Agent tool
- **Agent 6 is YOU** (the orchestrator), not a subagent. Compile the final report yourself
- **Risk score is inverted**: higher number = more risk (unlike other agents where higher = better)
- **Always include disclaimer**: report is for reference only, not investment advice
- **Target prices must include 3 scenarios** from at least 2 different valuation methods (PE + PB minimum), using industry peer PE as anchor not hardcoded multiples
- **Ex-cash adjustment**: When company has large cash reserves (>30% of market cap), calculate and report ex-cash PE as supplementary metric
- **Score consistency check**: Before final report, verify valuation score aligns with PE/PB deviation level, not just PEG
- **Composite score formula**: `综合评分 = (盈利 + 业务 + 估值 + 资金面 + (11 - 风险)) / 5`  
  Risk score is inverted (higher = riskier). Use (11 - risk) to convert to positive contribution. Show calculation in report.
- **Missing data handling**: If a query returns empty, skip that step. Note as "[数据不可用：{field}数据缺失]" in the report. Agent 6 does not penalize agents for skipped steps caused by missing data. Never fabricate numbers.
- **Output length**: Each agent output should be ≤ 800 words (Agent 4: ≤ 600 words). Prioritize tables and numbers over narrative prose.
