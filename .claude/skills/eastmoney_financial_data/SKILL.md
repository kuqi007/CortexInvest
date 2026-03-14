---
name: eastmoney_financial_data
description: 基于东方财富权威数据库查询金融数据（行情、财务、关系与经营数据），支持自然语言查询，返回JSON格式结果。
user_invocable: true
---

# 东方财富金融数据 (eastmoney_financial_data)

## Overview

本 Skill 基于**东方财富权威数据库**及**最新行情底层数据**构建，支持通过**自然语言**查询以下三类数据：

1. **行情类数据**: 股票、行业、板块、指数、基金、债券的实时行情、主力资金流向、估值等数据
2. **财务类数据**: 上市公司与非上市公司的基本信息、财务指标、高管信息、主营业务、股东结构、融资情况等数据
3. **关系与经营类数据**: 股票、非上市公司、股东及高管之间的关联关系数据，以及企业经营相关数据

采用此 Skill 可避免模型基于自身过时知识回答金融相关数据问题，可为大模型提供权威及时的金融数据。

## When to Use

- 用户询问股票、板块、指数、基金、债券的实时行情或历史数据时
- 查询上市公司财务指标（营收、利润、PE、PB等）时
- 查询股东结构、高管信息、主营业务等公司基本信息时
- 查询行业板块轮动、资金流向、估值数据时
- **Not for**: 需要实时交易下单、策略回测等复杂量化分析（使用专门的量化工具）

## API 调用方式

### 1. API Key 配置

东方财富 Skills API Key 需要存到环境变量，命名为 `EASTMONEY_APIKEY`。

优先检查顺序：
1. 环境变量 `EASTMONEY_APIKEY`
2. 本地配置文件 `~/.eastmoney_api`（如果存在）

```bash
# 设置环境变量（临时）
export EASTMONEY_APIKEY="your_api_key_here"

# 或写入配置文件
echo "your_api_key_here" > ~/.eastmoney_api
```

### 2. 调用示例

```bash
# 查询单只股票最新价
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/query' \
--header 'Content-Type: application/json' \
--header 'apikey: your_api_key_here' \
--data '{"toolQuery": "东方财富最新价"}'

# 查询股票财务数据
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/query' \
--header 'Content-Type: application/json' \
--header 'apikey: your_api_key_here' \
--data '{"toolQuery": "东方财富2024年营收净利润"}'

# 查询板块资金流向
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/query' \
--header 'Content-Type: application/json' \
--header 'apikey: your_api_key_here' \
--data '{"toolQuery": "今日主力资金流入"}'
```

## 数据限制说明

- **谨慎查询大数据范围**: 如某只股票 3 年的每日最新价，可能会导致返回内容过多，引起模型上下文爆炸问题
- **建议**: 查询时尽量指定具体时间范围，避免全量历史数据查询

## 结果返回格式

### 核心数据结构

返回 JSON 格式，主要路径：

| 字段路径 | 类型 | 核心释义 |
|---------|------|---------|
| `data.questionId` | 字符串 | 查数请求唯一标识 ID |
| `data.dataTableDTOList` | 数组 | **核心** 标准化证券指标数据列表 |
| `data.condition` | 对象 | 本次查询条件（关键词、时间范围等） |
| `data.entityTagDTOList` | 数组 | 关联证券主体汇总信息 |

### 证券指标数据 (dataTableDTOList[])

每个元素对应 **1 个证券 + 1 个指标** 的完整数据：

| 字段路径 | 类型 | 核心释义 |
|---------|------|---------|
| `code` | 字符串 | 证券完整代码（含市场标识，如 300059.SZ） |
| `entityName` | 字符串 | 证券全称（如东方财富 (300059.SZ)） |
| `table` | 对象 | **核心** 标准化表格数据 |
| `nameMap` | 对象 | 列名映射（指标编码→中文名） |
| `field` | 对象 | 指标元信息（编码、名称、时间粒度等） |
| `entityTagDTO` | 对象 | 证券主体属性（类型、市场、简称等） |

### 表格数据 (table)

```
table: {
  "指标编码": [数值数组],  // 如最新价数组
  "headName": "时间列名"
}
```

### nameMap 映射示例

```json
{
  "f2": "最新价",
  "f3": "涨跌幅",
  "f4": "涨跌额",
  "f12": "股票代码"
}
```

## 常用查询示例

### 行情类

| 查询场景 | toolQuery 示例 |
|---------|---------------|
| 股票最新价 | "东方财富最新价" |
| 股票行情 | "招商银行今日行情" |
| 板块涨跌 | "A股板块今日涨幅排名" |
| 指数行情 | "上证指数最新点位" |
| 基金净值 | "易方达蓝筹精选净值" |
| 资金流向 | "今日主力资金流入流出" |

### 财务类

| 查询场景 | toolQuery 示例 |
|---------|---------------|
| 营收利润 | "贵州茅台2024年营收净利润" |
| 财务指标 | "海康威视PE PB ROE" |
| 高管信息 | "比亚迪高管名单" |
| 主营业务 | "宁德时代主营业务" |
| 股东结构 | "中国平安前十大股东" |

### 关系与经营

| 查询场景 | toolQuery 示例 |
|---------|---------------|
| 关联公司 | "马化腾关联公司" |
| 上下游 | "光伏产业链公司" |
| 行业对比 | "新能源汽车行业对比" |

## 数据为空处理

当 API 返回数据为空时：
- 提示用户到东方财富妙想 AI 查询
- 建议用户使用更精确的查询关键词

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| API Key 无效 | 提示用户配置正确的 EASTMONEY_APIKEY |
| 网络超时 | 重试一次，仍失败则提示用户稍后重试 |
| 查询范围过大 | 提示用户缩小时间范围或减少查询条件 |
| API 返回空 | 提示用户到东方财富妙想 AI 查询 |

## Python 调用封装（可选）

如需在 Python 代码中调用，可封装为函数：

```python
import os
import json
import requests

def query_eastmoney(query: str) -> dict:
    """查询东方财富金融数据"""
    api_key = os.environ.get("EASTMONEY_APIKEY")
    if not api_key:
        # 尝试读取本地文件
        api_file = os.path.expanduser("~/.eastmoney_api")
        if os.path.exists(api_file):
            api_key = open(api_file).read().strip()

    if not api_key:
        raise ValueError("请配置 EASTMONEY_APIKEY 环境变量")

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }
    data = {"toolQuery": query}

    response = requests.post(url, json=data, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# 使用示例
result = query_eastmoney("东方财富最新价")
print(json.dumps(result, ensure_ascii=False, indent=2))
```

## 快速查询模板

当用户提问时，可直接使用以下模板构建查询：

```
# 股票行情
"{股票名称}最新价"
"{股票代码}今日行情"

# 财务数据
"{公司名称}{年份}{指标}"
"{公司名称}PE PB ROE"

# 板块资金
"今日{行业}主力资金"
"A股{板块}涨幅排名"

# 对比分析
"{公司A}对比{公司B}"
"{行业}行业财务对比"
```
