# 统一报告模板参考 (Step 5)

> 完整模板 → 从 valuation_result.json 渲染人读报告

## 文件生成顺序

1. `valuation_result.json` — 结构化结果(truth source)
2. `{股票名}_v{N}_{日期}.md` — 人读报告
3. `metadata.json` — 索引摘要(从valuation_result投影)
4. 更新 `catalog.json` 的 latest_version

## valuation_result.json 模板

```json
{
  "schema_version": "1.0",
  "stock_code": "{代码.市场}",
  "stock_name": "{股票名}",
  "analysis_date": "{YYYY-MM-DD}",
  "version": {N},
  "skill_version": "stock-analysis-unified v1.8",
  "classification": {
    "type": "{分类}",
    "sector": "{行业}",
    "market_cap_range": "{市值区间}"
  },
  "valuation": {
    "currency": "{HKD/CNY}",
    "method": "{估值方法名}",
    "conservative": { "price": XX, "pe": XX },
    "neutral":      { "price": XX, "pe": XX },
    "optimistic":   { "price": XX, "pe": XX },
    "assumptions": {}
  },
  "price_at_analysis": XX,
  "financials": {
    "currency_source": "{RMB原始/mx-data自动转HKD/已手动转换}",
    "cagr_actual_period": "{3yr/5yr}",
    "forward_earnings": {
      "fy1_net_profit": "{XX亿, 经调整/归母}",
      "fy1_source": "{mx-data预测table/mx-search研报/不足}",
      "fy2_net_profit": "{XX亿, 同上}",
      "fy2_source": "{同上}",
      "coverage": "{充分/不足/缺失}"
    },
    "profit_bridge": {
      "triggered": {true/false},
      "level": "{简化版/完整桥接/数据受限}",
      "items": ["A.公允价值变动", "B.资产减值", "..."]
    }
  },
  "defense_check": {},
  "core_thesis": "{一句话核心逻辑}",
  "key_risks": [],
  "classification": {
    "type": "{分类}",
    "sector": "{行业}",
    "market_cap_range": "{市值区间}",
    "data_gate": {
      "passed": {true/false},
      "original_method": "{原始估值方法}",
      "downgraded_method": "{降级后方法, null if passed}",
      "reason": "{降级原因, null if passed}",
      "missing_items": []
    }
  },
  "review": {
    "next_review_date": "{YYYY-MM-DD}",
    "interval_days": {按分类: 价值90/成长30/亏损14}
  },
  "shareholder_signal": {
    "non_institutional_ratio_pct": "number",
    "institutional_ratio_qoq_change_pp": "number",
    "shareholder_count_qoq_change_pct": "number",
    "top10_concentration_pct": "number",
    "controlling_shareholder_pct": "number",
    "risk_level": "HIGH | MEDIUM | LOW",
    "trigger_conditions": ["string"],
    "top10_holders": [{"name": "string", "type": "string", "ratio_pct": "number", "change": "string"}],
    "institutional_breakdown": {"fund_etf": "number", "private_fund": "number", "social_security": "number", "hkscc": "number", "other": "number"},
    "report_date": "string"
  },
  "technical_signal": {
    "data_range_days": "number",
    "data_end_date": "string",
    "signal_valid_until": "string",
    "board_type": "main_board | chinext | star",
    "indicators": {
      "rsi_14": {"value": "number", "oversold": "boolean", "overbought": "boolean"},
      "macd": {"dif": "number", "dea": "number", "histogram": "number", "crossover": "string", "divergence": "string"},
      "kdj": {"k": "number", "d": "number", "j": "number", "crossover": "string"},
      "ma": {"ma5": "number", "ma10": "number", "ma20": "number", "ma60": "number", "arrangement": "string"},
      "volume": {"ratio_vs_ma5": "number", "trend": "string"}
    },
    "synthesis_signal": "BULLISH | NEUTRAL | BEARISH",
    "signal_consistency_score": "string",
    "entry_levels": {
      "support_1": "number", "support_2": "number", "resistance_1": "number",
      "recommended_entry_range": "string", "technical_stop_loss": "number",
      "target_price": "number", "risk_reward_ratio": "number"
    },
    "conflicts_with_fundamentals": "boolean"
  },
  "fundamental_signal": {
    "direction": "BUY | HOLD | SELL",
    "conviction": "HIGH | MEDIUM | LOW",
    "rationale": "string"
  }
}
```

## metadata.json 模板

```json
{
  "schema_version": "1.0",
  "version": {N},
  "stock_code": "{代码.市场}",
  "stock_name": "{股票名}",
  "analysis_date": "{YYYY-MM-DD}",
  "classification": "{分类}",
  "valuation_neutral": XX,
  "currency": "{HKD/CNY}",
  "price_at_analysis": XX,
  "core_thesis": "{一句话核心逻辑}",
  "next_review": "{YYYY-MM-DD}",
  "review_interval_days": {N},
  "source": "valuation_result.json"
}
```

## 人读报告格式

### 头部
```markdown
# {股票名} ({代码}) 分析报告
> 分析日期：{YYYY-MM-DD} | 分析员：AI unified stock analysis skill v1.8

### 数据来源声明 (必须)
| 数据项 | 来源 | 本地文件 | 状态 |
|--------|------|----------|------|
| 基础行情 | mx-data | mx_data_*.xlsx | 已验证/缺失 |
| 财务报表 | mx-data | mx_data_*.xlsx | 已验证/缺失 |
| 收入构成 | mx-data | mx_data_*.xlsx | 已验证/缺失 |
| 行业数据 | mx-search | mx_search_*.txt | 已验证/缺失 |
| 机构数据 | mx-search | mx_search_*.txt | 已验证/缺失 |
| 前瞻预测 | mx-data预测table + mx-search | mx_data_*_raw.json + mx_search_*.txt | 已验证/缺失/不足 |
| 货币单位 | mx-data + 手动确认 | mx_data_*.xlsx | 已确认/待确认 |
```

### 一、股票分类
- **主要类型**: 价值/成长/周期/转型/亏损/过渡/混合待确认
- **置信度**: XX%
- **辅助特征**: 混合类型/边界提示
- **分类所用利润口径**: 报表归母/扣非归母/调整后经营利润
- **分类理由**: 关键指标说明

### 二、估值方法
- **选用方法**: 方法名
- **选择理由**: 为什么选这个方法
- **参考文档**: docs/method-*.md

### 三、估值结果

#### 利润口径桥接
| 口径 | 金额 | 是否用于估值主锚 | 说明 |
|------|------|------------------|------|
| 报表归母净利润 | XX | 是/否 | 股东真实口径 |
| 扣非归母净利润 | XX | 是/否 | 剔除非经常后 |
| 调整后经营利润 | XX | 是/否 | 仅用于趋势/辅助 |

**差异来源**: 列出归母/扣非/调整后三者差异原因

#### 估值结果表
| 情景 | 公允市值(亿) | 公允价值(元) | 安全买点(元) | 较当前 | 关键假设 |
|------|-------------|-------------|-------------|--------|----------|
| 保守 | XX | XX | XX | XX% | 假设1 |
| 中性 | XX | XX | XX | XX% | 假设2 |
| 乐观 | XX | XX | XX | XX% | 假设3 |

#### 防守检查摘要
**毛利率趋势**: 🟢/🟡/🔴 | **竞争力量化**: 强/中/弱 | **公司治理**: 🟢/🟡/🔴 | **股本稀释**: XX% | **ESG/政策**: 🟢/🟡/🔴
> 详细分析见报告末尾附录

#### 前瞻估值 (重大并表/成长+转型时必填)
| 情景 | 前瞻利润/EPS | 口径说明 | 估值方法 | 目标倍数 | 平台估值(亿) | 平台价值(元) |
|------|--------------|----------|----------|----------|--------------|--------------|
| 保守 | XX | {口径} | Forward PE | XXx | XX | XX |
| 中性 | XX | {口径} | Forward PE | XXx | XX | XX |
| 乐观 | XX | {口径} | Forward PE | XXx | XX | XX |

#### 敏感性分析
| 变量 | 基准值 | -10% | +10% | 估值影响 |
|------|--------|------|------|----------|
| PE倍数 | XXx | XX亿 | XX亿 | ±XX% |
| 利润增速 | XX% | XX亿 | XX亿 | ±XX% |

### 四、与机构对比
- **机构一致目标价**: XX元
- **我们的公允价值**: XX元 (中性)
- **我们的平台估值**: XX元 (如适用)
- **我们的安全买点**: XX元
- **差异口径**: 机构在比XX / 我们在比XX
- **差异原因**: 分析

### 五、投资建议
- **评级**: 买入/增持/持有/减持/卖出
- **建仓区间**: XX-XX元 (基于安全买点)
- **静态目标区间**: XX-XX元 (基于公允价值)
- **平台目标区间**: XX-XX元 (基于前瞻平台估值)
- **止损位**: XX元

### 3.5 短期技术信号参考

> ⚠️ 技术信号有效期：{data_end_date} 至 {signal_valid_until}（5个交易日）

#### 3.5.1 技术指标现状
| 指标 | 数值 | 信号 |
|------|------|------|
| RSI(14) | {rsi_value} | {超买/超卖/中性} |
| MACD | DIF={dif}, DEA={dea}, 柱={histogram} | {金叉/死叉/收敛} |
| KDJ | K={k}, D={d}, J={j} | {金叉/死叉/中性} |
| MA系统 | {ma5}>{ma10}>{ma20}>{ma60} | **{多头排列/空头排列/混乱}** |
| 成交量 | 量比{ratio_vs_ma5} | {放量/缩量/正常} |

**综合信号**: {synthesis_signal}（{signal_consistency_score}）

#### 3.5.2 入场参考价位
| 类型 | 价位 | 逻辑 |
|------|------|------|
| 第一支撑 | {support_1}元 | {MA20/布林下轨/近期低点} |
| 第二支撑 | {support_2}元 | {布林下轨/近期低点} |
| 建议入场区间 | {recommended_entry_range}元 | 回调至支撑位附近 |
| 技术止损位 | {technical_stop_loss}元 | 支撑-{atr_pct}%ATR |
| 目标位 | {target_price}元 | {MA60/前高}，R:R={risk_reward_ratio} |

#### 3.5.3 技术与基本面共振评估
| 维度 | 方向 | 说明 |
|------|------|------|
| 基本面方向 | {fundamental_signal.direction} | {fundamental_signal.rationale} |
| 技术面方向 | {synthesis_signal} | {AI 描述技术面状态} |
| **综合结论** | **{共振/分歧}，服从基本面** | {综合操作建议} |

> ⚠️ 技术止损位（{technical_stop_loss}元）不低于基本面止损位（{fundamental_stop_loss}元）。

### 六、核心逻辑
**看多逻辑**: 1. 2.
**风险提示**: 1. 2.

### 七、防守检查详细分析 (附录)

#### A. 毛利率趋势
| 年份 | 毛利率 | 同比变动 | 主要原因 |
|------|--------|----------|----------|
| 20XX | XX% | ±XXpp | 原因 |

**趋势判断**: 🟢/🟡/🔴 | **对估值影响**: 无/调整说明

#### B. 竞争力评分
| 维度 | 本公司 | 行业均值 | 评级 |
|------|--------|----------|------|
| 市场地位 | 描述 | 描述 | 强/中/弱 |
| 定价权 | | | |
| 技术壁垒 | | | |
| 客户粘性 | | | |
| 规模优势 | | | |
| 进入壁垒 | | | |
| 生态绑定 | | | |

**综合竞争力**: 强/中/弱 | **对估值倍数影响**: 无/折价XX%

#### C. 公司治理
| 检查项 | 状态 | 说明 |
|--------|------|------|
| 关联交易 | 🟢/🟡/🔴 | XX% |
| 管理层稳定性 | 🟢/🟡/🔴 | 变动 |
| 股权质押 | 🟢/🟡/🔴 | XX% |
| 审计意见 | 🟢/🟡/🔴 | 标准/非标 |
| 关键人风险 | 🟢/🟡/🔴 | 依赖程度 |
| 信息透明度 | 🟢/🟡/🔴 | 评价 |

**对估值影响**: 无/折价XX%

#### D. 稀释影响
| 项目 | 股数(万股) | 稀释比例 |
|------|-----------|----------|
| 当前总股本 | XX | - |
| 已增发/配售 | XX | +XX% |
| 可转债(全部转股) | XX | +XX% |
| 期权池 | XX | +XX% |
| **完全稀释股本** | **XX** | **+XX%** |

**调整后每股价值**: XX元 (原 XX元)

#### E. ESG/政策风险
**政策风险等级**: 🟢/🟡/🔴
**ESG评级**: XX
**对估值影响**: 无/折价XX%

#### F. 股东结构与筹码分析

**筹码集中度评估**
| 指标 | 数值 | 风险 |
|------|------|------|
| 非机构持股比例 | {non_institutional_ratio_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 机构持股环比 | {institutional_ratio_qoq_change_pp}pp | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 股东户数环比 | {shareholder_count_qoq_change_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 十大股东集中度 | {top10_concentration_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 控股股东持股 | {controlling_shareholder_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |

**综合风险等级**: {risk_level}

> 解读：{AI 根据各指标综合撰写解读文本}

**十大流通股东结构**
| 类型 | 持股合计 | 环比变化 |
|------|---------|---------|
| 控股股东 | {pct}% | {变化} |
| 证券投资基金/ETF | {pct}% | {变化} |
| 私募/券商 | {pct}% | {变化} |
| HKSCC（港股通） | {pct}% | {变化} |

**风险提示**（若无 HIGH 风险则省略此节）：
- 🔴 HIGH: {具体触发条件}
- ⚠️ MEDIUM: {具体触发条件}
