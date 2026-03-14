---
name: eastmoney_financial_search
description: 基于东方财富妙想搜索能力，根据用户问句搜索相关金融资讯（新闻、公告、研报、政策等），返回可读的文本内容。
user_invocable: true
---

# 东方财富资讯搜索 (eastmoney_financial_search)

## Overview

本 Skill 基于东方财富妙想搜索能力，用于获取涉及时效性信息或特定事件信息的任务，包括：
- 新闻、公告、研报、政策
- 交易规则、具体事件
- 影响分析、舆情监测
- 需要检索外部数据的非常识信息

避免 AI 在搜索金融场景信息时，参考到非权威及过时的信息。

## When to Use

- 用户询问股票、板块的最新新闻或公告时
- 查询某公司最近的研报、机构观点时
- 了解政策变化、行业动态时
- 分析大盘异动、资金流向等解读性内容时
- **Not for**: 实时行情数据查询（使用 eastmoney_financial_data）

## API 调用方式

### 1. API Key 配置

已在 `~/.zshrc` 中配置：
```bash
export EASTMONEY_APIKEY="mkt_tB9bJABwaGpnQl-dQ2EHaVjFaskuuemec5xpkIOz380"
```

### 2. 调用示例

```bash
# 查询个股资讯
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search' \
--header 'Content-Type: application/json' \
--header 'apikey: mkt_tB9bJABwaGpnQl-dQ2EHaVjFaskuuemec5xpkIOz380' \
--data '{"query":"立讯精密的资讯"}'

# 查询板块新闻
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search' \
--header 'Content-Type: application/json' \
--header 'apikey: mkt_tB9bJABwaGpnQl-dQ2EHaVjFaskuuemec5xpkIOz380' \
--data '{"query":"商业航天板块近期新闻"}'

# 查询政策解读
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search' \
--header 'Content-Type: application/json' \
--header 'apikey: mkt_tB9bJABwaGpnQl-dQ2EHaVjFaskuuemec5xpkIOz380' \
--data '{"query":"新能源政策解读"}'
```

## 常用查询模板

| 类型 | 查询示例 |
|------|---------|
| 个股资讯 | "格力电器最新研报"、"贵州茅台机构观点"、"立讯精密资讯" |
| 板块/主题 | "商业航天板块近期新闻"、"新能源汽车政策"、"AI算力板块动态" |
| 宏观/风险 | "美联储加息对A股影响"、"人民币汇率走势分析"、"A股具备自然对冲优势的公司" |
| 综合解读 | "今日大盘异动原因"、"北向资金流向解读"、"券商最新看市观点" |
| 财报业绩 | "宁德时代年报解读"、"银行股一季度业绩" |

## 返回结果格式

### 核心字段

| 字段路径 | 类型 | 释义 |
|---------|------|------|
| `title` | 字符串 | 信息标题，高度概括核心内容 |
| `secuList` | 数组 | 关联证券列表 |
| `secuList[].secuCode` | 字符串 | 证券代码（如 002475） |
| `secuList[].secuName` | 字符串 | 证券名称（如立讯精密） |
| `secuList[].secuType` | 字符串 | 证券类型（股票/债券） |
| `trunk` | 字符串 | 信息核心正文，承载具体业务数据 |

### 返回示例

```json
{
  "data": [
    {
      "title": "立讯精密(002475): 2024年一季度营收同比增长",
      "secuList": [
        {
          "secuCode": "002475",
          "secuName": "立讯精密",
          "secuType": "股票"
        }
      ],
      "trunk": "立讯精密发布2024年一季度报告，营收同比增长..."
    }
  ]
}
```

## Python 调用封装

```python
import os
import json
import requests

def search_eastmoney_news(query: str) -> dict:
    """搜索东方财富金融资讯"""
    api_key = os.environ.get("EASTMONEY_APIKEY")
    if not api_key:
        api_file = os.path.expanduser("~/.eastmoney_api")
        if os.path.exists(api_file):
            api_key = open(api_file).read().strip()

    if not api_key:
        raise ValueError("请配置 EASTMONEY_APIKEY 环境变量")

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }
    data = {"query": query}

    response = requests.post(url, json=data, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# 使用示例
result = search_eastmoney_news("立讯精密的资讯")
print(json.dumps(result, ensure_ascii=False, indent=2))
```

## 使用建议

1. **查询精确**: 尽量使用具体的公司名称、板块名称或事件描述
2. **结合 Financial Data**: 资讯搜索与行情数据结合使用，获得更全面的分析
3. **时效性**: 资讯具有时效性，重要决策前建议最新日期的资讯
4. **多源验证**: 重大信息建议结合官方公告和多个资讯来源交叉验证
