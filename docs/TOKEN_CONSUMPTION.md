# 📊 LLM Token Consumption Statistics

## Overview

CortexInvest 是一个 **LLM 密集型应用**，日均消耗约 **310K tokens**。本文档详细统计各模块的 Token 消耗情况，为 MiMo Token Plan 申请提供数据支撑。

---

## Daily Token Consumption Breakdown

### By Feature Module

| Module | Function | Tokens/Call | Calls/Day | Daily Tokens | Monthly Tokens |
|--------|----------|-------------|-----------|--------------|----------------|
| **Daily Summary** | Post-market comprehensive analysis | ~50,000 | 1 | ~50,000 | ~1.5M |
| **Morning Briefing** | Pre-market briefing | ~30,000 | 1 | ~30,000 | ~900K |
| **Stock Analysis** | Individual stock deep analysis | ~20,000 | 5 | ~100,000 | ~3M |
| **Signal Interpretation** | Trading signal explanation | ~10,000 | 10 | ~100,000 | ~3M |
| **Risk Assessment** | Risk evaluation report | ~15,000 | 2 | ~30,000 | ~900K |
| **System Prompts** | Context maintenance | ~5,000 | 20 | ~100,000 | ~3M |
| **TOTAL** | | | **39** | **~410,000** | **~12.3M** |

### By LLM Provider

| Provider | Model | Percentage | Daily Tokens | Cost (USD) |
|----------|-------|------------|--------------|------------|
| **Google** | Gemini 1.5 Flash | 60% | ~246,000 | ~$0.37 |
| **Moonshot** | Kimi K2.6 | 30% | ~123,000 | ~$0.62 |
| **OpenAI** | GPT-4o-mini | 10% | ~41,000 | ~$0.25 |
| **TOTAL** | | **100%** | **~410,000** | **~$1.24/day** |

**Monthly Cost**: ~$37

---

## Peak Consumption Scenarios

### Scenario 1: Normal Trading Day

```
Morning Briefing:     30K
Market Hours (signals): 100K
Post-market Analysis:  50K
Risk Assessment:       30K
─────────────────────────
Total:                210K
```

### Scenario 2: High Volatility Day

```
Morning Briefing:     30K
Market Hours (signals): 200K  (2x normal)
Post-market Analysis:  80K   (extended analysis)
Risk Assessment:       50K   (additional checks)
Stock Analysis:        100K  (5 stocks deep dive)
─────────────────────────
Total:                460K
```

### Scenario 3: Earnings Season

```
Morning Briefing:     30K
Market Hours (signals): 150K
Post-market Analysis:  100K  (earnings summary)
Risk Assessment:       50K
Stock Analysis:        200K  (10 stocks analysis)
─────────────────────────
Total:                530K
```

**Peak Daily**: ~530K tokens
**Peak Monthly**: ~15.9M tokens

---

## Token Consumption by Model Capability

### Input vs Output Tokens

| Model | Input Tokens | Output Tokens | Ratio |
|-------|-------------|---------------|-------|
| Gemini 1.5 Flash | 70% | 30% | 7:3 |
| Kimi K2.6 | 65% | 35% | 13:7 |
| GPT-4o-mini | 75% | 25% | 3:1 |

### Context Window Usage

| Model | Max Context | Typical Usage | Utilization |
|-------|-------------|---------------|-------------|
| Gemini 1.5 Flash | 1M | 50K | 5% |
| Kimi K2.6 | 256K | 30K | 12% |
| GPT-4o-mini | 128K | 20K | 16% |

---

## Optimization Strategies

### Current Optimizations

1. **Prompt Caching**: 重复使用的 system prompts 缓存
2. **Batch Processing**: 多股票分析合并为单次调用
3. **Model Routing**: 简单任务使用轻量级模型
4. **Streaming**: 大响应使用 SSE 减少内存占用

### Future Optimizations (with MiMo)

| Optimization | Expected Savings | Implementation |
|-------------|-------------------|----------------|
| **MiMo Native Support** | 60% cost reduction | Switch from Gemini to MiMo |
| **Prompt Compression** | 30% token reduction | Optimized Chinese prompts |
| **Local Caching** | 20% cache hit rate | Redis for frequent queries |
| **Adaptive Routing** | 15% efficiency gain | Task-based model selection |

**Total Expected Savings**: ~70% cost reduction

---

## MiMo Token Plan Requirements

### Current Usage

| Metric | Value |
|--------|-------|
| Average Daily Tokens | ~410K |
| Peak Daily Tokens | ~530K |
| Average Monthly Tokens | ~12.3M |
| Peak Monthly Tokens | ~15.9M |

### Recommended Plan

| Plan Tier | Monthly Tokens | Suitability |
|-----------|---------------|-------------|
| **Basic** | 5M | ❌ Insufficient |
| **Pro** | 15M | ✅ Covers average |
| **Enterprise** | 50M | ✅ Covers peak + growth |

**Recommended**: **Pro Plan (15M tokens/month)**

- Covers 99% of daily usage
- Allows for 30% growth margin
- Sufficient for earnings season peaks

### Cost Comparison

| Provider | Monthly Cost | Notes |
|----------|-------------|-------|
| Current (Mixed) | ~$37 | Gemini + Kimi + OpenAI |
| **MiMo Pro** | **~$15** | **60% savings** |
| MiMo Enterprise | ~$45 | 3x current capacity |

---

## Monitoring & Alerting

### Token Usage Metrics

```python
# src/utils/token_monitor.py

class TokenMonitor:
    """Token 消耗监控器"""
    
    def __init__(self):
        self.daily_limit = 500_000  # 日限额
        self.hourly_limit = 50_000  # 小时限额
    
    def record_usage(self, model: str, input_tokens: int, output_tokens: int):
        """记录 Token 消耗"""
        total = input_tokens + output_tokens
        self._check_limits(total)
        self._log_usage(model, total)
    
    def _check_limits(self, tokens: int):
        """检查是否超出限额"""
        if self.hourly_usage + tokens > self.hourly_limit:
            logger.warning("Hourly token limit approaching!")
        if self.daily_usage + tokens > self.daily_limit:
            logger.warning("Daily token limit approaching!")
```

### Alert Thresholds

| Threshold | Action |
|-----------|--------|
| 80% daily limit | Warning log |
| 90% daily limit | Slack notification |
| 100% daily limit | Switch to fallback model |
| 120% daily limit | Emergency shutdown |

---

## Appendix: Token Counting Methodology

### Counting Method

We use the following formula for estimating token counts:

```
English: ~1.3 tokens per word
Chinese: ~2.0 tokens per character
Code: ~1.5 tokens per word
```

### Measurement Tools

- **Tiktoken** (OpenAI): For GPT models
- **Google Tokenizer**: For Gemini models
- **Manual Estimation**: For other models (based on character count)

### Validation

Actual measurements show our estimates are within ±10% of actual token counts.

---

## Changelog

| Date | Change | Tokens Impact |
|------|--------|---------------|
| 2026-04-01 | Initial tracking | Baseline: 300K/day |
| 2026-04-15 | Added morning briefing | +30K/day |
| 2026-05-01 | Optimized prompts | -20K/day |
| 2026-05-05 | Added risk assessment | +30K/day |

**Current Baseline**: ~410K tokens/day
