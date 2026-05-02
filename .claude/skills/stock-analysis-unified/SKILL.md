---
name: stock-analysis-unified
description: Use when a user requests stock valuation, fundamental analysis, buy point calculation, or investment research on A-share or HK listed companies. Triggered by stock name or code input, valuation questions, target price inquiries, or requests to classify a stock as value/growth/cyclical/transformation/loss-making.
---

# 股票统一分析系统 (Unified Stock Analysis)

> **设计理念**: 一个入口，智能分类，自动匹配最优估值方法

---

## 使用方式

```text
# Skill 调用示例（不是 shell 命令）
/stock-analysis-unified 某价值股
/stock-analysis-unified 某转型股
/stock-analysis-unified 某成长股
```

---

## 工作流程

```
用户输入股票名
    ↓
Step 1: 数据收集 (mx-data + mx-search)
    ↓
Step 1.5: 数据收集验证门 (HARD GATE — 核心数据校验)
    ↓
Step 2: 智能分类 (多维度评分)
    ↓
Step 2.5: 估值前防守检查 (A-E五项必做)
    ├─ A. 毛利率趋势
    ├─ B. 竞争量化分析
    ├─ C. 公司治理
    ├─ D. 股本稀释检查
    ├─ E. ESG/政策风险
    └─ F. 股东结构与筹码分析
    ↓
Step 3.5: 短期技术信号（入场时机参考）
Step 3: 匹配估值方法 (主方法 + 条件触发双轨)
    ├─ 价值股 → PE/PB/股息率
    ├─ 成长股 → PEG/Forward PE/DCF
    ├─ 周期股 → 正常化PE/PB
    ├─ 转型股 → SOTP
    │   └─ 若重大并表 / pro forma → 追加 Forward PE/PEG/EV
    └─ 亏损股 → PS/终局估值
    ↓
Step 4: 交叉验证
    ├─ 方法内验证
    ├─ 与机构对比
    └─ 与历史估值对比
    ↓
Step 5: 输出统一格式报告 → 保存为 MD 文件
```

---

## Step 1: 数据收集 (两阶段执行)

### 阶段架构

```
本 skill 分两阶段执行:
  Phase 1 (主会话): 数据收集 — 串行调用 mx-data/mx-search，所有数据落盘到本地文件
  Phase 2 (可 agent): 报告生成 — 只读取本地文件，不再调用任何外部 API
```

### Phase 1: 数据收集 (主会话执行，串行)

先确定输出目录，再收集数据：

```
项目根目录 = <项目根目录>
全局目录 = 项目根目录/stocks/catalog.json

确定股票代码:
    1. 【市场确认 - HARD STOP】收到股票名后，**先用 mx-search 轻量级确认市场**（不是 mx-data）：
       - 查询句: `"{股票名}" 股票代码 A股 港股`
       - 检查返回的 `secuList[]`，根据后缀数量做判断:
         - **只有 1 个候选 + 后缀为 .SZ/.SH** → 纯 A 股，直接用
         - **只有 1 个候选 + 后缀为 .HK** → 纯港股，直接用
         - **返回多个候选（如同名ST股等）** → 用公司全称/主体类型/成立时间等辅助信息自行判断，AI 决定，不问用户
         - **返回 A+H 两地** → 按下方规则自动选择:
           - **默认选 A 股**（市值更大、流动性更好）
           - 若 A 股不在 mx-data 系统覆盖范围（查询返回空或同名混淆）→ 切换为港股
       - ⚠️ **禁止在未完成此步的情况下直接开始 mx-data 模糊查询**
       - ⚠️ 此步只调用 mx-search（轻量），不计 Phase 1 API 额度消耗
    2. 用确认后的股票代码（如 06651.HK），在 catalog.json 的 `stocks` 字典中查找对应 entry，定位 dir_name
    3. 若 catalog 中已有该代码 → latest_version + 1；否则新建 v1
    4. 确定版本号和日期

输出目录结构:
    stocks/{代码.市场}_{股票名}/v{N}_{YYYY-MM-DD}/
    ├── {股票名}_v{N}_{YYYY-MM-DD}.md    ← 分析报告
    ├── valuation_result.json              ← 结构化分析结果（truth source）
    ├── metadata.json                      ← 索引摘要（自动从 valuation_result 生成）
    └── data/                              ← 原始数据（mx-data/mx-search 产物）

市场后缀: .HK 港股 | .SH 沪市 | .SZ 深市
目录名示例: 00700.HK_腾讯控股/、600309.SH_万华化学/

硬规则:
    - 所有 mx-data/mx-search 产物必须保存到 data/ 子目录
    - 分析报告、valuation_result、metadata 保存在版本目录根层
    - 不得将同一次分析的文件散落到其他目录
    - mx-data 输出目录参数必须指定为版本目录下的 data/
    - mx-search 输出目录参数必须指定为版本目录下的 data/

串行协议 (防止 429 限流):
    - mx-data 和 mx-search 调用必须串行，严禁并行
    - 每次调用之间间隔至少 3 秒
    - 同一时间只能有 1 个 mx-data 或 mx-search 请求在执行中
    - 若出现 429 错误: 等待 10 秒后重试同一查询，最多重试 2 次
    - 若重试仍失败: 跳过该数据项，在报告中标注 "数据收集受限"
```

### Phase 2: 报告生成 (只读本地文件)

```
进入 Phase 2 的前提: Step 1.5 验证门已通过
Phase 2 执行规则:
    - 只读取版本目录 data/ 下已有的 mx_* 文件
    - 禁止在 Phase 2 调用 mx-data 或 mx-search
    - 所有估值数字必须能追溯到具体的 mx_* 文件
```

通过 mx-data 和 mx-search 收集：

| # | 数据项 | 用途 | 来源 |
|---|--------|------|------|
| 1 | 基础行情 | 当前价格、市值、PB | mx-data |
| 2 | 财务报表 | 利润、收入、ROE | mx-data |
| 3 | 收入构成 | 分部数据（转型股必需） | mx-data |
| 4 | 3年财务 | 趋势判断（**必须含毛利率、净利率[计算]、经营性现金流**） | mx-data |
| 5 | 利润质量桥接 | 归母/扣非差异来源、一次性项目 | mx-data + 财报/研报 |
| 6 | 行业数据 | 可比公司、周期位置、**竞争格局（前3名收入/增速/市占率，从 mx_search txt 文本提取）**、**可比公司当前PS倍数（转型股/亏损股必需）** | mx-search |
| 7 | 机构数据 | 目标价、评级交叉验证 | mx-search |
| 8 | 毛利率趋势 | 竞争力与成本结构变化 | mx-data |
| 9 | 竞争格局 | 市占率、定价权、护城河 | mx-search |
| 10 | 股本变动 | 近12月配股/增发/可转债转股/稀释比例 | mx-data + 财报/研报 |
| 11 | 现金流质量 | 经营性现金流 vs 净利润趋势 | mx-data |
| 12 | **前瞻盈利预测** | **FY1/FY2经调整净利润（含方向变化：上调/下调至±XX亿）** | mx-data预测table + mx-search |
| 13 | **货币单位确认** | **报表原始币种 vs mx-data标准化口径（港股必需，防止单位混淆）** | mx-data |
| **F1** | **机构持股比例合计** | 近3年报 机构持股比例合计 | mx-data |
| **F2** | **股东户数** | 股东户数 总股本 流通股本 | mx-data |
| **F3** | **十大流通股东明细** | 十大流通股东 | mx-data |
| **T1** | **每日 OHLCV** | 近{N}日 每日开盘价收盘价最高价最低价成交量 | mx-data |
| **T2** | **主力资金流向**（可选） | 近{N}日 主力资金流向 | mx-data |

**前置数据补充说明**:

> 股东数据（F1-F3）和技术数据（T1-T2）在 Step 1 原有 14 项完成后，作为补充步骤收集。

**股东数据（F1-F3）收集说明**:
```
A股 (.SH/.SZ)：使用季度数据（最近4期）
港股 (.HK)：使用半年度数据（最近2期）

保存路径：mx_data_*机构持股比例*.json → stocks/{CODE}_{NAME}/v{N}_{DATE}/data/
         mx_data_*股东户数*.json → 同上
         mx_data_*十大流通股东*.json → 同上
```

**技术数据（T1-T2）收集说明**:
```
数据范围确定：
  - 主板股：近35日（确保≥30交易日）
  - 创业板/科创板：近25日
  - 识别方式：检查 entityTagDTO.className
    · 含"创业板" → 25日
    · 含"科创板" → 25日
    · 其他 → 35日

保存路径：mx_data_*近{N}日每日*.json → stocks/{CODE}_{NAME}/v{N}_{DATE}/data/
```

**前瞻数据双源优先级链 (v1.8 新增)**:
```
优先级 1: mx-data 年报文件预测 table（结构化 col_id 100000000004890，口径统一为预测归母净利润）
优先级 2: mx-search 研报文本正则提取（覆盖经调整/Non-IFRS等非标口径，含负数和方向变化）
优先级 3: 标注"前瞻数据覆盖度不足"
差异处理: 两者都有时，以 mx-data 为锚，mx-search 做交叉验证
覆盖度检查: 至少2家独立券商的FY1预测，或1家头部券商+mx-data预测table数据点
不满足 → 标注"前瞻数据覆盖度不足，估值方法降级"
```

### 估值方法→数据需求逆向映射 (v1.8 新增)

> 目的: Step 1 数据收集表是"正向"需求列表，但估值方法(Step 3)需要的数据项
> 可能超出了基础表。本映射表让 Step 1 收集前能预判"如果最终分类为X，
> 还缺什么数据"，从而一次收齐。

| 估值方法 | 必需数据项 (Step 1 必收) | 补充数据项 (分类确认后补收) |
|----------|------------------------|--------------------------|
| 方法1 PE/PB/股息率 | 行情(PE/PB/市值) + 3年财务(利润/ROE) + 分红率 | 历史PE/PB中枢(至少3年) + 同行可比PE |
| 方法2 PEG/Forward PE | 行情 + 3年财务 + **前瞻EPS(FY1/FY2)** + 利润质量桥接 | DCF所需: 自由现金流/WACC假设 + 行业增速 |
| 方法3 正常化PE/PB | 行情 + **至少1个完整周期(3-5年)的利润数据** + 行业周期位置 | 正常化利润计算 + 资源储量(矿企) |
| 方法4 SOTP | 行情 + **收入构成(分部收入/利润/增速)** + **可比公司PS/PE** | 各分部可比倍数(至少2个同行/分部) + 协同价值估算 |
| 方法5 PS/终局 | 行情 + **收入增速(近3年+前瞻)** + **可比公司PS倍数** | 终局假设(渗透率/市占率/利润率回归) + 现金消耗率 |
| 方法6 困境股 | 行情 + PB + **资产质量(有息负债/现金/受限资产)** | 清算价值估算 + 债务到期结构 |

**使用规则**:
```
1. Step 1 收集基础数据时，参考此表预判可能的分类方向
2. 若初步判断可能为"转型股"(转型分>0.4) → 提前收集收入构成和可比PS
3. 若初步判断可能为"亏损股"(扣非<0) → 提前收集收入增速和同行PS
4. 若初步判断可能为"周期股"(行业属周期) → 提前收集至少5年利润数据
5. Step 2 分类完成后 → 对照此表检查"补充数据项"是否已收集
6. 缺失 → 回退到 Step 1 补收（允许 Phase 2 启动前的补充数据收集）
```

**门控规则**: 如果分类确认后的"必需数据项"缺失 → 不得进入 Step 3，必须补收。

### 利润质量桥接（必须做）

在开始分类和估值前，必须先建立三层利润口径：

```
1. 报表归母净利润
2. 扣非归母净利润
3. 调整后经营利润（如有必要）
```

**分级触发规则 (v1.8 新增)**:
```
利润质量桥接不是全量3年必做，而是按差异程度分级：
- 3年差异均<20% → 简化版（只做当期）
- 任一年差异>20% → 完整桥接该年份（逐项拆解A-F）
- 数据不足以做完整桥接 → 单期+标注"非经常性损益明细受限于披露"
- 硬规则: 报告中必须说明每项是"精确值"还是"估计值"
```

如果 `归母净利润` 与 `扣非净利润` 差异明显（经验阈值：绝对差异 > 20%，或方向相反）：

```
必须追查差异来源，并明确归类为以下哪一种：

A. 公允价值变动 / 金融资产价格波动
B. 资产减值 / 信用减值 / 存货跌价
C. 资产处置 / 出售股权 / 卖楼卖资产
D. 投资收益（权益法、处置收益、理财等）
E. 股权激励 / 一次性费用
F. 其他一次性或低频事项
```

**硬规则**:
- 不得直接拿“调整后利润”替代股东口径利润
- 不得因为报表难看就默认全部加回
- 也不得因为报表好看就默认都是主营利润
- 必须说明：哪些项目代表**真实股东损益**，哪些项目只是**主营经营趋势观察项**
- 若利润高增长主要来自 `资产处置 / 出售股权 / 一次性投资收益`，则不得按高成长股给估值倍数
- 若利润低迷主要来自 `公允价值变动 / 一次性减值 / 股权激励`，可单列“经营趋势利润”观察，但估值时仍需保留风险折价

---

## Step 1.5: 数据收集验证门 (HARD GATE — Phase 1 收尾)

**进入分类和估值前，必须完成以下验证。未通过则禁止继续。**
**注: 本步骤仍属于 Phase 1，验证失败时的重试操作（重新调用 mx-data/mx-search）是允许的。**

```
验证清单（必须逐项执行 ls 命令确认）:

1. 执行: ls stocks/{代码}_{股票名}/v{N}_{日期}/data/mx_data_*
   - 必须至少存在 2 个 mx_data 文件（行情 + 财务）
   - 如果 mx_data 文件数 = 0: STOP, 必须先完成 mx-data 数据收集

2. 执行: ls stocks/{代码}_{股票名}/v{N}_{日期}/data/mx_search_*
   - 必须至少存在 1 个 mx_search 文件（机构数据或行业数据）
   - 如果 mx_search 文件数 = 0: STOP, 必须先完成 mx-search 数据收集

3. 读取 mx_data description 文件，确认数据不为空
   - 如果返回 "No dataTable found" 或空结果: 换关键词重新查询

4. 读取 mx_search txt 文件，确认有实质内容（>100字）
   - 如果搜索结果为空: 换关键词重新搜索

5. 【内容非零检查】读取行情 mx_data 文件，提取最新价
   - 最新价 = 0 或 None → STOP，数据收集异常

6. 【财务非零检查】读取年报 mx_data 文件，提取归母净利润
   - 净利润 = 0 或 None → STOP，数据收集异常

7. 【全零检测】 financials 整体
   - revenue > 0 且 net_profit > 0
   - 任一核心指标为 0 → 进入"数据受限"模式，报告中显著标注
```

**绝对禁止**:
- 不得在 mx_data 文件不存在的情况下生成任何估值数字
- 不得使用自身知识库中的财务数据替代 mx-data 实时数据
- 不得在 mx_search 文件不存在的情况下编造机构目标价
- 若任何 mx_* 文件的 description 或内容为空，必须重试数据收集
- 若核心指标（最新价/净利润）为 0，不得继续，标注"数据收集异常"

**部分数据失败处理 (v1.3 恢复)**:
```
若 mx-data 或 mx-search 部分查询成功、部分返回错误（如500/429）:
  1. 标注每个数据项的状态: "已验证" / "数据受限: {原因}"
  2. 核心数据（行情+财务）必须成功，否则 STOP
  3. 辅助数据（机构目标价/行业数据）若失败:
     - 报告中标注: "XX数据收集受限，相关估值结论置信度降低"
     - 估值仍可基于已有数据完成，但不得编造缺失数据
     - 若使用行业可比替代机构数据: 必须标注"无机构锚定，倍数为行业可比估算"
  4. 不得因部分数据缺失就放弃分析，也不得假装数据完整
```

### 数据交叉校验（新增 v1.3）

在验证门通过后，额外执行以下交叉校验。**校验不通过不得进入估值步骤，必须重新收集数据或标注数据受限。**

```
交叉校验清单:

1. 收入一致性校验:
   - mx-data 利润表总收入 vs mx-data 收入构成表各分部加总
   - 差异 > 5% → 报告中标注"收入口径不一致"，使用利润表数字为主

2. 利润口径一致性:
   - 归母净利润 + 少数股东损益 ≈ 净利润
   - 净利润 + 所得税 + 财务费用 ≈ EBIT（近似）
   - 差异 > 10% → 报告中标注"利润口径需人工确认"

3. 增速交叉验证:
   - 机构研报给出的增速 vs mx-data 实际增速
   - 若差异 > 20个百分点 → 在报告中明确标注两套数据，不得只取好看的那个
   - AI云/新业务等分部增速：必须能用(本期收入-上期收入)/上期收入 还原验证
   - 【硬规则·增速年份归属】所有引用的增速数据必须明确标注年份归属：
     · 实际YoY：标注具体财年（如"FY2024A 实际YoY +28%"）
     · 预测YoY：标注预测年份与来源（如"FY2025E 预测YoY +35%，来源XX券商"）
     · 不得混用实际增速与预测增速；不得用预测增速冒充实际增速来支撑分类判断
     · 若分类依赖预测增速，报告中必须额外说明"分类依赖前瞻预测，存在兑现风险"

4. 估值倍数合理性:
   - 自算 PE vs 行业可比 PE vs 机构引用 PE
   - 三者中任意两个差异 > 30% → 必须解释差异来源（口径不同/年份不同/是否扣非）

5. 股本一致性:
   - 当前总股本 vs 历史股本趋势
   - 若近12个月股本变动 > 5% → 触发稀释检查（见 Step 2.5D）

### 叙事矛盾检查 (v1.8 新增，Step 1.5 交叉校验第6项)

```
6. 叙事与前瞻预测矛盾检查:
   - 提取分析中关于利润方向的叙事（如"扭亏为盈"/"盈利改善"/"利润承压"）
   - 提取机构前瞻预测的利润方向（FY1经调整净利润的正负）
   - 口径对齐规则（核心）:
     · 只比较相同口径（经调整 vs 经调整，归母 vs 归母）
     · 不同口径方向不一致 → 标注"口径不可比"，不算矛盾
     · 同口径方向一致 → 正常
     · 同口径方向矛盾 → 标"HIGH"级别矛盾
   - 常见矛盾场景:
     · 分析说"经调整扭亏为盈(FY2025)"，但机构预计"FY2026E经调整净利润-1.92亿(再度亏损)"
       → 必须在报告中显著标注"⚠️ 扭亏可能非趋势性拐点，FY2026E预计再度亏损"
     · 分析说"利润改善"，但机构FY1预测大幅低于当前 → 标注并调整评级
   - 港股中小票特殊处理:
     · 机构覆盖<2家 → 标注"前瞻数据覆盖度不足，叙事矛盾检查置信度低"
```
- **硬性 STOP（校验不通过不得进入 Step 2）**: 核心数据（行情+财务）查询失败，或收入/利润数据交叉校验误差超标
- **软性标注（可继续但需标注"数据受限"）**: 辅助数据（机构目标价/行业数据）收集失败，或非核心校验项误差超标
- 核心数据的定义: mx-data 返回"行情"或"财务报表"为空/错误
- 判断顺序: 先判断是否为核心数据失败 → 若是则 STOP → 若否则允许软性标注后继续
```

---

## Step 1.6: 字段提取规范 (Phase 2 执行)

> 进入此步骤的前提: Step 1.5 验证门已通过
> 此步骤属于 Phase 2，只读取本地文件，不调用外部 API
> 完整提取函数和 col_id 映射见 `./docs/mx-data-field-mapping.md`

### 提取优先级

| 指标类型 | 提取来源 | 说明 |
|---------|---------|------|
| 行情数据（股价/PE/PB/市值） | mx_data 行情文件，取最新一期 | |
| 财务数据（利润/收入/毛利率/ROE） | mx_data 年报文件，取最新一期 | |
| 前瞻预测（EPS/增速） | mx_data 年报文件的预测 table | col_id 见文档 |
| 机构目标价/评级 | mx_search txt 文件 | 见 ./docs/mx-search-institutional-schema.md |

### 提取到 valuation_result 的映射

| valuation_result 字段 | 提取来源 | 方法 |
|---------------------|---------|------|
| price_at_analysis | mx_data 行情文件 | col `325898` 收盘价 |
| pe_at_analysis | mx_data 行情文件 | col `328773` 市盈率PE(TTM) |
| pb_at_analysis | mx_data 行情文件 | col `328664` 市净率PB |
| market_cap | mx_data 行情文件 | col `326809` 总市值 |
| financials.revenue_latest | mx_data 年报文件 | col `100000000000415` 最新一期 |
| financials.net_profit_latest | mx_data 年报文件 | col `100000000003705` 最新一期 |
| financials.adjusted_net_profit | mx_data 年报文件 | col `100000000003520` 最新一期 |
| financials.gross_margin | mx_data 年报文件 | col `100000000002972` 最新一期 |
| financials.roe | mx_data 年报文件 | col `100000000003466` 最新一期 |
| assumptions.eps | mx_data 年报文件 | **计算**: 归母净利润 × 1e8 / 总股本 × 1e4 |
| assumptions.forward_eps | mx_data 年报预测table | col `100000000004890` / 总股本 |
| institutional.consensus_target_price | mx_search txt | 正则提取（见文档） |
| institutional.rating | mx_search txt | rating 字段直接取 |

**H股字段**: 部分字段（H股table）使用不同 col_id，具体见 `./docs/mx-data-field-mapping.md`

### CAGR 计算降级规则

```
原始要求: 近五年 CAGR
实际可用: 近三年 CAGR（mx-data 系统限制最多返回 3 年年报）

处理规则:
- 5yr CAGR 不可计算 → 自动降级为 3yr CAGR
- 在 financials 中标注: "cagr_actual_period": "3yr"
- 报告中标注: "CAGR 降级为近三年，因系统返回数据限制"
- 若不足 3 年年报: 标注"数据受限，CAGR 仅供参考"
```

### 多 entity（A+H）数据规范化

```
问题: mx_data 可能返回 A+H 两套数据（如 002475.SZ + H5162.HK）
处理规则:
  1. 优先 .SZ / .SH（A 股主体）
  2. 纯港股（.HK only）直接使用
  3. 若 H 股数据用于交叉验证，在报告中标注

识别方式: 检查 dataTableDTOList 中 entityTagDTO.marketChar 字段
```

### 提取步骤（操作清单）

```
1. 加载 mx_data 行情文件 → 提取最新价/PE/PB/市值
2. 加载 mx_data 年报文件 → 提取最新一期利润/收入/毛利率/ROE
3. 从年报文件的预测 table → 提取前瞻利润/增速
4. 从 mx_search txt 文件 → 提取机构目标价/评级（见机构数据提取文档）
5. 按 entity 选择规则过滤（多 entity 时优先 A 股）
6. 计算派生指标: EPS = 净利润/总股本, CAGR = (期末/期初)^(1/年数)-1
7. 将所有提取结果填入 valuation_result.json
```

---

## Step 2: 股票分类 (核心)

### 多维度评分系统

对每个维度计算得分 (0-1)，然后综合判断：

```
价值维度:    稳定性(0.4) + 分红率(0.3) + 低波动(0.3)
成长维度:    收入增速(0.3) + 利润增速(0.3) + 利润率趋势(0.2) + 现金流质量(0.2)
周期维度:    行业属性(0.5) + 利润波动(0.5)
转型维度:    新业务占比(0.4) + 新旧业务增速差(0.4) + 结构变化(0.2)
亏损维度:    亏损程度(0.5) + 现金消耗(0.3) + 收入增速(0.2)
```

**分类口径硬规则**:
```
1. 分类阶段的"利润增速"默认优先看 扣非归母净利润增速
2. 若扣非也明显失真（经验阈值: 与报表归母差异>20%，或方向相反）:
   - 可用"调整后经营利润"仅辅助判断主营趋势
   - 但不得直接据此给高成长估值倍数
3. 若高增长主要来自 资产处置 / 出售股权 / 一次性投资收益 / 公允价值抬升:
   - 不得直接分类为"高成长股"
   - 至少降级为"普通增长/混合型/待人工确认"
```

**利润率趋势评分规则 (成长维度)**:
```
利润率趋势得分 = clamp((当前毛利率 - 3年前毛利率) / 3年前毛利率, 0, 1)
  - 毛利率持续改善 → 趋势分高
  - 毛利率持平 → 趋势分中(0.5)
  - 毛利率连续下滑 → 趋势分低

硬规则:
  - "连续下滑"定义: 最近3个完整财年毛利率依次递减，或最近4个报告期中≥3期同比下滑
  - 必须使用同比(YoY)，不得使用环比(QoQ)
  - 例外: 若毛利率下降主因是高增长新业务占比提升（新业务毛利虽低但正在改善），不触发降级
  - 若收入增速>30% 但毛利率连续下滑 → 不得归类为"高质量成长股"
```

**现金流质量评分规则 (成长维度)**:
```
现金流质量得分:
  - 经营性现金流/净利润 > 1.0 且趋势改善 → 1.0
  - 经营性现金流/净利润 0.5-1.0 → 0.5
  - 经营性现金流为负（利润为正时） → 0.2
  - 两者均为负，但现金流亏损小于净利润亏损 → 0.4
```

**利润增速特殊处理 (扭亏为盈/亏损/由盈转亏)**:
```
若基期（上一年）扣非归母净利润为负，且当期已转正:
  - 利润增速得分直接取 1.0（最高分），不得因"增速百分比无意义"而给低分
  - 不得计算理论增速百分比（如从-10亿到+5亿 = 150%）后套入常规增速分级
  - 必须在报告中标注: "扭亏为盈，利润增速得分取满分"
  - 进一步验证: 检查转正来源是否为主营改善（毛利率提升/费用率下降/收入规模效应）
    - 若主营改善 → 可支持成长股分类
    - 若主要来自一次性项目 → 按利润质量桥接规则处理，不得直接给满分

若基期为负且当期仍为负（亏损收窄）:
  - 利润增速得分 = clamp(亏损收窄幅度 / 100, 0, 0.7)
  - 不取满分，因为尚未真正盈利
  - 例如: 从-10亿收窄到-5亿 → 收窄50% → 得分 0.5

若基期为正且当期转负（由盈转亏）:
  - 利润增速得分 = 0.1（最低档）
  - 不得因前几年利润为正就给中间分数
  - 分类时需额外评估: 是一次性亏损还是趋势性恶化
  - 若一次性因素导致（如大幅计提减值/一次性投入）:
    - 可在报告中说明"经营趋势仍可观察"，但分类得分不变
  - 若经营性亏损（收入增速不达预期/毛利率持续下滑/费用失控）:
    - 不得归类为"成长股"
    - 建议归入"转型早期"或"困境股"
```

4. 若报表利润偏弱主要来自 公允价值下跌 / 一次性减值 / 股权激励:
   - 可保留"成长/转型候选"判断
   - 但报告中必须提示: 股东口径利润仍承压，估值要保留风险折价

5. 最终必须明确:
   - 哪个利润口径用于分类
   - 哪个利润口径用于估值主锚
   - 哪个利润口径仅用于观察经营趋势

### 分类决策规则

| 分类 | 判定条件 | 估值方法 |
|------|----------|----------|
| **价值股** | 价值分最高，且扣非口径增速<15%，ROE>10% | 方法1: PE/PB/股息率 |
| **成长股** | 成长分最高，且扣非/经营趋势增速>30%，并通过利润质量桥接 | 方法2: PEG/Forward PE/DCF |
| **周期股** | 周期分最高，且行业为周期行业 | 方法3: 正常化PE/PB |
| **转型股** | 转型分>0.6，且新旧业务均明显 | 方法4: SOTP分部估值 |
| **亏损股** | 扣非归母净利润<0，且收入增速>50% | 方法5: PS/终局估值 |
| **困境股 / 低增速亏损股** | 扣非归母净利润<0，且收入增速<=50% | 资产价值/PB/等待拐点 |

**【转型股 vs 亏损股 边界讨论硬规则】**:
```
若公司扣非归母净利润<0，但同时满足转型股条件（转型分>0.6，新旧业务均明显）:
  → 不得直接归入"转型股"而跳过亏损股方法
  → 必须在报告中逐项回答以下问题:
    1. 当期亏损是新业务投入导致的战略性亏损，还是主业衰退导致的经营性亏损？
    2. 亏损的趋势是收窄还是扩大？收窄速度是否可量化？
    3. 若按亏损股方法（PS/终局估值）估值，结果与SOTP估值差异多大？
    4. 为何选择转型股SOTP方法而非亏损股PS方法？（必须给出至少2条具体理由）
  → 若无法清晰回答以上4个问题，必须同时输出两套估值结果供对比
  → 若公司整体仍处于"烧钱扩张"阶段（经营性现金流持续为负），SOTP中的执行风险折扣不得低于30%
```

**金融股分类特殊规则**:
```
银行/保险/券商等金融股：
- 默认归入"价值股"（方法1），但须检查经济周期敏感性
- 若 NIM/不良率/ROE 呈明显周期波动 → 识别为"价值+周期"混合
- 混合时使用: 正常化PE 60% + PB 30% + 股息率 10%
- 估值时必须额外关注: 资本充足率、拨备覆盖率、净息差趋势
- 这些金融特有指标不是"可选附加"，而是方法1对金融股的必填检查项
```

**边界缓冲处理**:
- 扣非/经营趋势增速28-32%区间 → 提示"接近成长股阈值，需人工确认是按15-30%过渡区处理，还是按成长股处理"
- 转型分0.55-0.65 + 新业务18-22% → 提示"转型早期，谨慎使用SOTP；若重大并表已明显改变盈利结构，可按转型股双轨估值处理"

**15%端点规则**:
```
若 扣非/经营趋势利润增速恰好约等于15%（如 14.5%-15.5%）:
    - 不默认直接归入价值股
    - 先进入"15-30%过渡区"规则处理
    - 只有在ROE、分红、稳定性都明显更像成熟价值股时，才可按方法1为主
```

**重大并表 / pro forma 规则**:
```
如果 过去12个月内完成重大收购、并表或资产重组，且新增资产满足任一条件:
    - 新增资产收入/EBITDA占合并口径 > 30%
    - 新增资产估值占总估值 > 30%
    - 次年利润预测较当前报表口径抬升 > 50%

则:
    1. 分类结果必须额外标记 "成长+转型候选"
    2. 估值必须双轨输出:
       - 静态公允价值: SOTP / 分部估值
       - 前瞻平台估值: Forward PE / PEG / EV
    3. 与机构目标价对比时，默认先对比"公允价值"或"前瞻平台估值"
       不得拿"安全买点"直接对比机构目标价
```

**增速15-30%区间处理** (价值与成长之间的过渡区):
```
如果 扣非/经营趋势利润增速在15-30%区间:
    先检查利润质量桥接，确认增长是否来自主营
    如果 ROE > 12% 且 分红率 > 30%:
        → "高质量成长股，偏价值属性"
        → 使用PEG估值 (基准PE 15-25x)
    否则如果 利润稳定性高(5年波动<20%):
        → "稳健增长股"
        → PE Band为主 (12-18x)，PEG为辅验证
    否则:
        → "普通增长股"
        → PEG估值 (1.0-1.2x)
```

**无法分类兜底**:
```
如果 所有维度得分 < 0.3:
    返回 "数据不足，无法可靠分类"
    建议: "请检查财务数据完整性，或在本 skill 内按报告模板进行人工分析"
```

### 混合类型处理

如果前两名得分差距 < 0.15，识别为混合类型：

| 混合类型 | 权重分配 | 理由 |
|----------|----------|------|
| 周期+转型 | 周期40% + SOTP 60% | 周期影响短期盈利，转型决定长期价值 |
| 成长+转型 | SOTP 50% + Forward PE/PEG 50% | 并表前后口径差异大时，必须同时保留资产价值与前瞻盈利视角 |
| 价值+周期 | 正常化PE 70% + PB 30% | 周期位置决定入场时机，价值属性决定长期持有 |
| 周期+高分红 | 正常化PE 40% + 股息率 30% + PB 20% + 资源价值(NAV) 10% | 分红提供价值锚，但需验证分红可持续性（分红/正常化利润<80%为安全线） |
| 成长+周期 | 周期位置优先 | 高波动环境，先判断周期位置再论成长 |

**成长+转型 hybrid 权重条件化 (v1.8 新增)**:
```
上表"成长+转型 → SOTP 50% + Forward 50%"是默认值。
当 SOTP 与 Forward PS 差异较大时，应根据转型成熟度调整:

if SOTP vs Forward 差异 < 30%:
    权重 = 50/50（两者接近，简单平均即可）
elif SOTP vs Forward 差异 30-60%:
    if 转型分 > 0.65 且 新业务收入增速 > 100%:
        权重 = 40/60  # 更信Forward（转型验证度高）
    else:
        权重 = 60/40  # 更信SOTP（保守，转型未验证）
elif SOTP vs Forward 差异 > 60%:
    不得简单加权，必须双轨并列输出:
    1. SOTP公允价值区间
    2. Forward PS估值区间
    3. 安全买点区间
    并解释差异来源

注意: 周期+转型、价值+周期、周期+高分红、成长+周期的权重保持硬编码，
条件化仅适用于"成长+转型"和"亏损+转型"两种缺乏历史经验的混合类型。
```

**三重混合处理**: 输出"复杂类型，建议人工介入分步分析"

**成长+周期处理硬规则**:
```
若识别为 成长+周期:
    1. 先判断周期位置，再决定是否允许使用成长股倍数
    2. 若处于周期顶部或利润显著高于正常化水平:
       - 禁用高PEG / 高Forward PE 作为主锚
       - 默认使用正常化PE / PB
    3. 若处于周期底部或复苏早期，且成长逻辑来自真实市占率提升/产品升级:
       - 可将 PEG / Forward PE 作为辅助验证
       - 但主结论仍以周期位置校准后的利润口径为准
    4. 报告中必须说明:
       - 当前使用的是报表利润、正常化利润，还是 next-year 利润
       - 成长逻辑是否可能只是周期回升而非结构性成长
```

**双轨输出硬规则**:
```
若 SOTP公允价值 与 前瞻平台估值 差异 > 2x:
    - 不得简单取平均后输出单一目标价
    - 必须并列展示:
      1. 静态公允价值区间
      2. 前瞻平台估值区间
      3. 安全买点区间
    - 必须解释差异来自 分部价值 / 并表协同 / 利润口径 / 估值倍数 的哪几项
```

### 分类后数据充分性门控 (v1.8 新增)

> 目的: 分类完成后、估值执行前，检查已收集数据是否支撑所选方法。
> 这是 Step 2 → Step 2.5 之间的 HARD GATE。

```
门控清单（分类完成后逐项检查）:

1. 【基础数据完备性】
   - ✅ 行情数据: 最新价、PE、PB、市值均已提取且非零
   - ✅ 财务数据: 最近3年利润表（收入/归母/扣非/毛利率）均已提取
   - ❌ 任一缺失 → STOP，补收后重新门控

2. 【方法特定数据完备性】（对照"逆向映射表"的"必需数据项"列）
   - 若分类为"转型股" → 检查: 收入构成(分部数据) + 可比公司PS/PE 是否已收集
   - 若分类为"亏损股" → 检查: 收入增速(3年+前瞻) + 可比PS 是否已收集
   - 若分类为"成长股" → 检查: 前瞻EPS(FY1/FY2) + 利润质量桥接 是否已完成
   - 若分类为"周期股" → 检查: 至少3-5年利润数据 + 行业周期位置 是否已有
   - 若分类为"价值股" → 检查: 分红率 + 历史PE/PB中枢 是否已有
   - ❌ 方法特定数据缺失 → 标注"数据受限"，估值方法降级:
     · 转型股缺分部数据 → 降级为方法5(PS)
     · 亏损股缺可比PS → 降级为方法6(PB/资产价值)
     · 成长股缺前瞻EPS → 降级为方法1(PE)，标注"前瞻数据不足，PEG不可用"
     · 周期股缺多年数据 → 降级为方法1(当前PE/PB)
     · 价值股缺分红率 → PE/PB仍可用，标注"股息率数据缺失"

3. 【前瞻数据覆盖度】
   - ✅ 至少2家独立券商FY1预测，或1家头部券商+mx-data预测table数据点
   - ⚠️ 仅1家非头部预测 → 标注"前瞻数据覆盖度不足"
   - ❌ 完全无前瞻预测 → 分类为"价值/困境"时不影响；分类为"成长/转型"时必须降级

4. 【货币一致性】
   - ✅ 所有估值用数据（利润/收入/市值/股价）使用统一货币
   - ⚠️ 混币种 → 按mx-data-field-mapping.md的detect_currency()规则转换并标注currency_source
   - ❌ 无法确认货币 → 标注"货币口径不确定，估值仅供参考"
```

**降级记录**: 每次门控触发降级，必须在 valuation_result.json 的 `classification.data_gate` 字段中记录:
```json
{
  "data_gate": {
    "passed": false,
    "original_method": "方法4: SOTP",
    "downgraded_method": "方法5: PS",
    "reason": "收入构成(分部数据)缺失",
    "missing_items": ["分部收入/利润/增速", "可比公司PS/PE"]
  }
}
```

> **→ 继续 Step 2.5 防守检查（A-E五项均为必做），方可进入 Step 3。**

---

## Step 2.5: 估值前防守检查 (新增 v1.3)

**⚠️ 硬性必填声明：A/B/C/D/E 五项防守检查均为必做项，不得跳过、不得省略、不得以"数据不足"为由略过。每项检查必须在报告"防守检查详细分析"章节中逐项输出，缺失任何一项等同于估值未完成。**

**在进入 Step 3 估值之前，必须完成以下五项防守检查。任何一项亮红灯，必须在报告中显著标注，并调整估值结论。**

详细执行规则见 ./docs/defense-check-reference.md，包含：
- **A. 毛利率趋势分析** — 趋势判断/分部检查/同行对比，红灯折价10-20%
- **B. 竞争量化分析** — 7维度评分表，竞争折价15-25%，行业适配规则
- **C. 公司治理评估** — 6项检查清单，治理折价10-15%，与执行风险折扣叠加关系
- **D. 股本稀释检查** — 完全稀释股数计算，披露分级规则，稀释质量评估
- **E. ESG/政策风险评估** — 政策逆风/ESG评级/转型资本开支，高风险折价10-20%
- **F. 股东结构与筹码分析** — 非机构持股比例/机构持股环比/股东户数环比/十大股东集中度/控股股东持股

---

## Step 2.5-F: 股东结构与筹码分析（新增 v1.9）

> 评估筹码分散度和主力动向。所有股票都执行此检查。

**数据来源**（已由 Phase 1 收集）：
- F1: mx_data_*机构持股比例*.json
- F2: mx_data_*股东户数*.json
- F3: mx_data_*十大流通股东*.json

**Python 解析函数**（位于 `src/analysis/`）：
```python
from src.analysis import parse_institutional_ratio, classify_top10_holders
from src.analysis.shareholder_analyzer import assess_shareholder_risk
```

**执行步骤**：
1. **解析机构持股比例** — 调用 `parse_institutional_ratio()`，提取最新一期和上一期数据
2. **计算非机构持股比例** — `non_institutional_ratio_pct = 100 - latest_ratio_pct`
3. **计算环比变化** — 机构持股环比、股东户数环比
4. **分类十大股东** — 调用 `classify_top10_holders()`，识别控股股东/基金/HKSCC 等类型
5. **评估风险等级** — 调用 `assess_shareholder_risk()`，使用市场差异化阈值
6. **写入 valuation_result.json** — shareholder_signal 字段
7. **输出报告章节** — `## 2.5-F 股东结构与筹码分析`

**HKSCC 特殊处理**：
- 港股十大股东中"香港中央结算有限公司"为代名人，记录但单独标记
- 不参与"有效机构持股"计算
- 报告中注明"港股 HKSCC 持股为代表持有人，实际投资者结构未知"

**阈值差异化**：
| 市场 | 非机构持股 HIGH | 非机构持股 MEDIUM |
|------|----------------|-----------------|
| A股  | > 35%          | 25%~35%         |
| 港股 | > 50%          | 35%~50%         |

**错误处理**：
- 机构持股数据缺失 → 标注"数据受限，无法计算股东风险"
- 仅有单一报告期 → 标注"数据不足"，环比变化记为 None
- 股东户数不可用 → 跳过，不影响整体风险评级

**⚠️ 硬性规则**：Step 2.5-F 必须完成，才能进入 Step 3。即使数据受限，也必须标注后继续。

---

## Step 3: 估值方法执行

根据分类结果，执行对应估值方法。详细方法说明见参考文档：

| 方法 | 类型 | 参考文档 |
|------|------|---------|
| 方法1 | 价值股估值 | ./docs/method-value.md |
| 方法2 | 成长股估值 | ./docs/method-growth.md |
| 方法3 | 周期股估值 | ./docs/method-cyclical.md |
| 方法4 | 转型股SOTP | ./docs/method-transformation.md |
| 方法5 | 亏损股PS/终局 | ./docs/method-lossmaking.md |
| 方法6 | 困境股估值 | ./docs/method-distressed.md |

---

## Step 3.5: 短期技术信号（新增 v1.9 — 入场时机参考）

> 基于 20-30 个交易日的技术指标，提供短期入场价位建议。**技术信号服从基本面**——不改变基本面方向，只辅助判断入场时机。

**数据来源**（已由 Phase 1 收集）：
- T1: mx_data_*近{N}日每日*.json
- 数据范围：主板 35日（确保≥30交易日），创业板/科创板 25日

**⚠️ 数据充分性要求**：
| 指标 | 最小数据量 | 数据不足时的行为 |
|------|-----------|----------------|
| RSI(14) | ≥ 15 条 | 返回 None，视为中性(0分)，报告中标注 |
| MACD(12,26,9) | ≥ 35 条 | DIF=0 且 DEA=0 → 强制归零(0分)，同时标注"⚠️ MACD 数据不足(<35条)" |
| KDJ(9,3,3) | ≥ 10 条 | 返回默认值 {k:50,d:50,j:50}，视为中性(0分) |
| MA(5/10/20/60) | 分别 ≥ 5/10/20/60 条 | 不足的均线记为 None；MA=None 视为中性(0分)，支撑/阻力计算时过滤 |
| ATR(14) | ≥ 15 条 | 返回 None，无法计算止损/目标位，报告中标注 |
| 布林带(20) | ≥ 20 条 | 返回 None，支撑位计算时跳过布林下轨 |
| 成交量分析 | ≥ 5 条 | 正常计算 |

**⚠️ T1 收集验证门**：调用 `parse_kline_ohlcv()` 后，检查返回行数：
- 若 < 30 条：报告中显著标注"⚠️ K线数据仅 N 条（<30），技术信号精度下降，仅供参考"
- 若 < 15 条：整个 Step 3.5 跳过，在报告中标注"技术数据不足"

**Python 解析函数**（位于 `src/analysis/`）：
```python
from src.analysis import parse_kline_ohlcv
from src.analysis.technical_indicators import (
    calc_rsi, calc_macd, calc_kdj, calc_ma,
    calc_atr, calc_bollinger_bands, calc_volume_ratio
)
```

**执行步骤**：

1. **解析 K 线数据** — 调用 `parse_kline_ohlcv()`，提取 OHLCV 列表
2. **数据充分性检查**：
   - 若返回行数 < 15 条：整个 Step 3.5 跳过，报告中标注"技术数据不足"
   - 若返回行数 < 30 条：标注"⚠️ K线数据仅 N 条（<30），技术信号精度下降"
3. **计算技术指标**（调用 `src/analysis/technical_indicators.py`）：
   - `calc_rsi(closes, 14)` — RSI(14)
   - `calc_macd(closes)` — MACD(12,26,9)；**若 DIF=0 且 DEA=0，标记"⚠️ MACD 数据不足"，不参与计分或强制归零**
   - `calc_kdj(highs, lows, closes)` — KDJ(9,3,3)
   - `calc_ma(closes)` — MA(5/10/20/60)；MA60=None 时在阻力位计算中排除
   - `calc_volume_ratio(volumes)` — 成交量分析
   - `calc_atr(highs, lows, closes, 14)` — ATR（用于止损）；ATR=None 时无法计算入场价位，报告中标注
   - `calc_bollinger_bands(closes, 20)` — 布林带（用于支撑位）；BB=None 时排除布林下轨
4. **综合信号评分**（5 指标计分）：
   ```
   指标: [RSI方向, MACD方向, KDJ方向, MA排列, 成交量方向]
   得分: 看多=+1, 看空=-1, 中性=0
   ⚠️ MACD 特殊规则: DIF≈0 且 DEA≈0（数据不足）→ 强制归零(0分)，同时标注"⚠️ MACD 数据不足"
   总分 = sum(各指标得分)
   if 总分 >= 3: synthesis_signal = "BULLISH"
   elif 总分 <= -3: synthesis_signal = "BEARISH"
   else: synthesis_signal = "NEUTRAL"
   ```
   - MACD 强制归零而非排除，保证总分基准始终为 5，保持与原设计的一致性
   - 标注"⚠️ MACD 数据不足"仅用于报告展示，不影响计分逻辑
5. **计算入场价位**（N = K线数据条数）：
   ```
   近期低点 = min(收盘价[-(N-1):-(N-5)])  # 最近5日内最低（不含今日）
   近期高点 = max(收盘价[-(N-1):-(N-5)])  # 最近5日内最高（不含今日）
   支撑位 = min([v for v in [MA20, 近期低点, 布林下轨] if v is not None])
   阻力位 = max([v for v in [MA60, 近期高点] if v is not None])  # MA60=None时排除
   技术止损 = 支撑位 - 2% × ATR  (ATR=None时标注"止损位无法计算")
   目标位 = 阻力位 + 2 × ATR    (ATR=None时标注"目标位无法计算")
   ```
6. **冲突检测**：若 `technical_signal.synthesis_signal == "BEARISH"` 且基本面方向为 BUY，则 `conflicts_with_fundamentals = True`
7. **写入 valuation_result.json** — technical_signal + fundamental_signal 字段（含 `data_sufficient: bool` 和 `macd_data_insufficient: bool`）
8. **输出报告章节** — `## 3.5 短期技术信号参考`

**信号有效期**：所有技术信号有效期为 5 个交易日（数据截止日 + 5）。

**禁止**：技术信号不得抬高基本面止损位，不得覆盖基本面卖出信号。

**⚠️ 硬性规则**：技术面服从基本面——Step 3.5 只提供入场时机参考，不改变 Step 3 的估值方向。

---

## Step 4: 交叉验证

### 方法内验证
- PE vs PB 差异是否在±30%内
- DCF假设是否合理（增长率、折现率）
- 归母/扣非/调整后经营利润三者是否已分层说明
- 高增长利润是否来自主营，还是来自资产处置/投资收益/公允价值波动

### 与机构对比
- 必须先列清三种口径: 公允价值 / 前瞻平台估值 / 安全买点
- 对比机构一致目标价时，默认仅使用"公允价值"或"前瞻平台估值"
- 安全买点仅用于建仓区间，不作为机构目标价对比口径
- 若公允价值与前瞻平台估值同时存在:
  - 先判断机构研报主口径是分部/NAV还是 next-year PE/PEG/EV
  - 能判断时，headline 默认跟随机构主口径
  - 不能判断时，默认先展示公允价值，再补展示平台估值
  - 不得为了贴近机构目标价临时挑选更高的一侧做 headline
- 如果差异>30%，分析原因（假设差异、方法差异）

### 与历史估值对比
- 当前PE/PB处于历史什么分位
- 是否显著高于/低于历史中枢

---

## Step 5: 输出报告

**报告保存路径**: `stocks/{代码.市场}_{股票名}/v{N}_{YYYY-MM-DD}/{股票名}_v{N}_{YYYY-MM-DD}.md`

**完成后必须生成的文件**:

| 文件 | 说明 | 生成顺序 |
|------|------|----------|
| `valuation_result.json` | 结构化分析结果（truth source） | 第一个生成 |
| `{股票名}_v{N}_{日期}.md` | 人读分析报告 | 从 valuation_result 渲染 |
| `metadata.json` | 索引摘要 | 从 valuation_result 投影 |

**最后更新 catalog.json**: 在 `项目根目录/stocks/catalog.json` 中更新对应股票的 `latest_version`。

详细报告模板和文件格式说明见 ./docs/report-template-reference.md。

---

## 估值方法快速参考

| 股票类型 | 核心指标 | 估值锚定 | 典型倍数 |
|----------|----------|----------|----------|
| 价值股 | ROE、股息率 | 历史PE/PB中枢 | PE 8-15x, PB 1-2x |
| 成长股 | 扣非/经营趋势增速 | PEG分档 + Forward交叉验证 | 先看详细规则，不直接套单一区间 |
| 周期股 | 周期位置 | 正常化利润 | PE 10-15x, PB 0.8-1.5x |
| 转型股 | 分部利润 + 前瞻利润 | SOTP + Forward交叉验证 | 分部不同 |
| 亏损股 | 收入增速 | PS | PS 5-50x |

**口径提醒**:
- 全公司价值股的 `PE 8-15x` 是历史中枢口径，适用于整家公司成熟稳定估值
- SOTP 里的成熟分部 `PE 15-25x` 是分部可比口径，适用于拆分估值，不得直接拿来替代整家公司价值股中枢
- 成长股快速参考只作索引，具体倍数以 `方法2` 里的 PEG 分档、Forward PE 优先级和利润质量约束为准

---

## 重要提醒

### 估值不是精确科学
- 所有估值都是"大概正确"
- 关键是逻辑自洽，不是数字精确
- 安全边际比精确估值更重要

### 动态调整
- 季度财报后重新评估
- 行业重大变化后重新评估
- 催化剂兑现/落空后重新评估

### 分类纠错

当自动分类明显错误时，用户可手动指定类型：

```text
# 在指令中直接说明希望采用的估值类型
/stock-analysis-unified 股票名，按价值股处理
/stock-analysis-unified 股票名，按成长股处理
/stock-analysis-unified 股票名，按周期股处理
/stock-analysis-unified 股票名，按转型股处理
/stock-analysis-unified 股票名，按亏损股处理
```

**常见分类错误信号**:
- 增速30%的公司被分为价值股 → 检查利润稳定性
- 明显周期股被分为成长股 → 检查利润波动率
- 转型早期被识别为转型成功 → 检查新业务占比

### 风险控制
- 单股仓位不超过10%
- 设置止损，严格执行
- 不追逐过高估值

---

## 边界情况提示

| 情况 | 提示信息 | 建议操作 |
|------|----------|----------|
| 增速15-18% | "处于15-30%过渡区，建议人工确认" | 查看ROE、分红率和利润稳定性，决定偏价值还是偏成长 |
| 新业务18-22% | "转型早期，谨慎使用SOTP" | 降低执行风险折扣至25-30% |
| 周期顶部信号 | "当前PE处于历史低位，可能是周期顶部" | 优先使用正常化PE，降低仓位 |
| 亏损但增速<50% | "亏损股但增速不足，不符合终局估值条件" | 使用PB估值，等待盈利拐点 |

---

## 参考文档

| 文件 | 内容 |
|------|------|
| ./docs/method-value.md | 价值股估值详解（PE/PB/股息率三档） |
| ./docs/method-growth.md | 成长股估值详解（PEG/Forward PE/DCF/扭亏为盈规则） |
| ./docs/method-cyclical.md | 周期股估值详解（周期位置判断/正常化PE） |
| ./docs/method-transformation.md | SOTP估值详解（分部估值/执行风险折扣表） |
| ./docs/method-lossmaking.md | 亏损股估值详解（PS倍数/终局估值） |
| ./docs/method-distressed.md | 困境股估值详解（PB主锚/正常化PE辅助） |
| ./docs/defense-check-reference.md | Step 2.5 防守检查A-E详解 |
| ./docs/report-template-reference.md | Step 5 报告模板/valuation_result/metadata 格式 |
| ./docs/mx-data-field-mapping.md | **mx-data 字段映射表（含 col_id/提取函数）** |
| ./docs/mx-search-institutional-schema.md | **mx-search 机构数据提取规范** |

---


## mx-data Raw JSON 解析参考

批量分析时需要直接解析 mx-data `_raw.json` 文件。其结构如下：

```
data.data.searchDataResultDTO.dataTableDTOList[0]
├── nameMap: dict  {列ID → 指标名}   例: {"100000000003703": "净利润"}
├── table:   dict  {列ID → [值列表]}  ⚠️ 是 dict 不是 list！
│   例: {"100000000003703": ["2.911亿元", "3.749亿元", "-3.391亿元"]}
├── headNameMap: dict (通常为空)
├── rowInfo: list (通常为空)
└── colInfo: list (通常为空)
```

**解析步骤**:
```python
import json
data = json.load(open("mx_data_xxx_raw.json"), strict=False)
dt = data['data']['data']['searchDataResultDTO']['dataTableDTOList'][0]
table = dt['table']       # dict: col_id -> [val_per_row]
name_map = dt['nameMap']  # dict: col_id -> metric_name
for col_id, metric_name in name_map.items():
    values = table.get(col_id, [])
    print(f"{metric_name}: {values}")
```

**⚠️ 常见陷阱**:
1. `table` 是 `dict` 不是 `list`，`table[0]` 会抛 `KeyError`
2. `nameMap` 中可能有重复的指标名（不同列ID，不同口径）
3. 值是带单位的字符串（如"2.911亿元"、"19.89%"），解析数字时需去除单位
4. 行（年份/季度）标签不在 table 中，需从查询语句或 description.txt 推断

---

## Step 6: 最终校验（Step 5 输出后必做）

**⚠️ 强制门控：Step 5 输出后必须执行此校验，未通过不得结束任务。**

### 文件完整性校验

每次分析完成后，**在关闭 session 前**，逐项验证以下内容：

```
1. 三个核心文件存在性检查（ls 命令）：
   stocks/{代码}_{股票名}/v{N}_{日期}/
   ├── {股票名}_v{N}_{YYYY-MM-DD}.md         ✅ 必须存在
   ├── valuation_result.json                   ✅ 必须存在
   ├── metadata.json                          ✅ 必须存在
   └── data/                                  ✅ 必须存在
       └── mx_*                               ✅ 所有 mx_* 文件必须在此目录下

2. JSON 有效性检查（python3 -c "import json; json.load(open('path'))"）：
   - valuation_result.json 必须是合法 JSON
   - metadata.json 必须是合法 JSON
   - 任何解析错误 → 必须修复后才能结束

3. mx 文件散落检查（find 命令）：
   find stocks/{代码}_{股票名}/v{N}_{日期}/ -name "mx_*" -not -path "*/data/*"
   - 若有结果 → 文件散落在 data/ 之外，必须移动到 data/ 后才能结束
```

### Python 一键校验脚本

```python
import json, os, glob

code = "000338.SZ"          # 替换为实际代码
name = "潍柴动力"           # 替换为实际名称
ver = 2
date = "2026-04-20"         # 替换为实际日期

vpath = f"stocks/{code}_{name}/v{ver}_{date}"

# 前置检查：版本目录必须存在
if not os.path.isdir(vpath):
    print(f"🚨 版本目录不存在: {vpath}")
    raise SystemExit(1)

ok = True

# 1. 三个核心文件 + data/ 目录
for f in [f"{name}_v{ver}_{date}.md", "valuation_result.json", "metadata.json"]:
    if not os.path.exists(f"{vpath}/{f}"):
        print(f"❌ 缺失: {f}")
        ok = False
    else:
        print(f"✅ {f}")

if not os.path.isdir(f"{vpath}/data"):
    print(f"❌ 缺失: data/ 目录")
    ok = False
else:
    print(f"✅ data/ 目录存在")

# 2. JSON 有效性
for f in ["valuation_result.json", "metadata.json"]:
    fp = f"{vpath}/{f}"
    if not os.path.exists(fp):
        continue  # 已在上一步报告
    try:
        with open(fp) as fh:
            json.load(fh)
        print(f"✅ {f} JSON合法")
    except json.JSONDecodeError as e:
        print(f"❌ {f} JSON无效: {e}")
        ok = False

# 3. mx 文件散落检查（检查 mx_* 文件是否全在 data/ 下）
misplaced = []
mx_in_data = 0
for root, dirs, files in os.walk(vpath):
    for f in files:
        if f.startswith("mx_"):
            rel = os.path.relpath(os.path.join(root, f), vpath)
            parts = rel.split(os.sep)
            # 正确位置: data/mx_* 或 data/子目录/mx_*
            if len(parts) >= 2 and parts[0] == "data":
                mx_in_data += 1
            else:
                misplaced.append(rel)

if misplaced:
    for f in misplaced:
        print(f"❌ 散落: {f}")
    ok = False
else:
    print(f"✅ mx文件: {mx_in_data} 个，全部在 data/ 下")

if ok:
    print(f"\n✅ 最终校验通过: {vpath}")
else:
    print(f"\n❌ 最终校验失败，请修复后再结束")
```

### 常见失败模式与修复

| 失败模式 | 原因 | 修复方法 |
|---------|------|---------|
| `valuation_result.json` 缺失 | Phase 2 未执行或脚本中断 | 从 md 报告提取数据补写，或重新执行 Phase 2 |
| JSON 解析错误 | 引号/逗号/括号不配对 | 检查文件尾部，补全缺失字符 |
| mx 文件散落在版本根目录 | mx-data/mx-search 输出目录参数错误 | `mv {file} {vpath}/data/` |
| mx 文件散落在 stocks/ 根目录 | 市场确认步骤输出到错误位置 | 同上，移到对应股票版本的 data/ |
| metadata.json 缺失 | Step 5 未执行 | 从 valuation_result.json 投影生成 |

### 完成后操作

校验通过后，更新 `catalog.json`（**注意使用项目根目录的绝对路径**）：

```python
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent
catalog_path = project_root / "stocks" / "catalog.json"

# 以下变量替换为实际值
code = "000338.SZ"
name = "潍柴动力"
ver = 2
date = "2026-04-20"
dir_name = f"{code}_{name}"

with open(catalog_path) as f:
    cat = json.load(f)

# 更新 last_updated
cat["last_updated"] = date

# 更新或新建股票条目
if code not in cat["stocks"]:
    cat["stocks"][code] = {}
cat["stocks"][code].update({
    "name": name,
    "latest_version": ver,
    "dir_name": dir_name
})

with open(catalog_path, "w") as f:
    json.dump(cat, f, ensure_ascii=False, indent=2)

print(f"✅ catalog.json 已更新: {code} v{ver}")
```

---

*Version: Unified v1.9*
*Core: One entry, smart classification, right method for right stock*
*Changelog v1.9: 新增股东结构分析(Step 2.5-F)和短期技术信号(Step 3.5)。(F1-F3)股东数据收集: 机构持股比例/股东户数/十大流通股东；(T1-T2)技术数据收集: 每日OHLCV K线/主力资金流向。(Step 2.5-F)调用parse_institutional_ratio()+assess_shareholder_risk()，A股/港股差异化阈值，非机构持股/HKSCC处理。(Step 3.5)调用calc_rsi()+calc_macd()+calc_kdj()+calc_ma()+calc_atr()+calc_bollinger_bands()+calc_volume_ratio()，5指标计分综合信号，入场价位计算，技术止损 ATR×2%，技术服从基本面硬规则。src/analysis/新增mx_parser.py/technical_indicators.py/shareholder_analyzer.py，34个单元测试全通过。
*Changelog v1.8: 基于明略科技(02718.HK)分析报告审查发现的11项缺陷系统性修复。结构改进: (S1) Step 1数据收集表新增#12前瞻盈利预测+#13货币单位确认+双源优先级链；(S2) 新增"估值方法→数据需求逆向映射"表（Step 3反推Step 1必须收集什么）；(S3) 新增"分类后数据充分性门控"(Step 2→2.5之间HARD GATE，含降级规则和data_gate JSON记录)。缺陷修复: (M1) 前瞻盈利预测双源(mx-data预测table+mx-search研报)及覆盖度检查；(M2) mx-search正则扩展覆盖负数/方向变化/经调整口径/forward_earnings提取函数；(M3) Step 1.5叙事矛盾检查(口径对齐规则)；(M4) mx-data detect_currency()函数+currency_source标注规则；(M6) Step 2.5可比公司PS锚定表+PS可比校准；(M7) 成长+转型hybrid权重条件化(SOTP vs Forward差异驱动)；(M8) 利润质量桥接分级触发(差异>20%年份才完整桥接)。report-template-reference.md: financials扩展(currency_source/forward_earnings/profit_bridge)、新增classification.data_gate字段、前瞻预测+货币单位数据来源行。docs更新: mx-search-institutional-schema.md正则扩展、mx-data-field-mapping.md货币检测、method-transformation.md PS锚定表、method-lossmaking.md PS可比校准*
*Changelog v1.7: 新增Step 6最终校验门（含文件存在性/JSON有效性/mx文件散落检查/Python一键校验脚本/修复指南/完成后更新catalog.json）；Changelog v1.6:* 修复Phase 1→2桥接缺失（Step 1.5增强验证门含内容非零检查+全零检测；新增Step 1.6字段提取规范含col_id映射表+提取函数+操作清单）；新增docs/mx-data-field-mapping.md（含已验证col_id/提取函数/多entity处理/已知限制）；新增docs/mx-search-institutional-schema.md（含rating字段/文本正则/权威度规则）；defense-check-reference.md新增A-E数据来源附录；method-growth.md新增输入溯源表；修正"近五年→近三年"表述；修正"净利率"标注需计算*
*Changelog v1.5: v1.4骨架 + v1.3精肉合并（成长四维权重、利润率趋势评分细则、现金流质量评分、利润增速特殊处理(扭亏/亏损/由盈转亏)、扭亏为盈估值规则、恢复性增长PEG修正、竞争折价规则、稀释披露+质量评估、部分数据失败处理、方法6困境股、数据收集表细化）；附录拆分（Step2.5/6种方法/报告模板/示例解析出为独立参考文档），SKILL.md减少~600行，ghost ref全部修复；自包含迁移（所有参考文档移入 .claude/skills/stock-analysis-unified/docs/）；目录管理v2.2*
*Changelog v1.4: 目录管理v2.2集成（代码做主键、版本化目录、valuation_result.json为truth source、catalog.json全局索引、metadata.json自动生成）*
*Changelog v1.3: 毛利率趋势分析 + 竞争量化框架 + 公司治理评估 + 股本稀释检查 + 数据交叉校验 + 折扣率决策规则 + ESG/政策风险评估 + 周期顶部强制校验 + 周期+高分红混合类型 + 煤炭/矿业/AI行业适配 + 资源型央企竞争适配 + PS倍数扩展*
