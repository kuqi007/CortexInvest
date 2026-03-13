---
name: eastmoney-financial-data
description: 查询东方财富金融数据（行情、财务、关系），基于权威数据库提供实时和历史数据
argument-hint: [查询内容]
---

# 东方财富金融数据 Skill

通过东方财富权威 API 查询金融数据，支持行情、财务、关系与经营三类数据。

## 适用场景

- 查询股票实时行情、资金流向、估值
- 查询上市公司财务指标、高管信息、股东结构
- 查询板块、行业的行情数据
- 查询基金、债券数据

## 使用前提

1. 获取 API Key：在东方财富 Skills 页面申请
2. 设置环境变量：`EASTMONEY_APIKEY`
3. 检查本地 API 是否可用，若存在可直接使用
4. **⚠️ 每个 Skill 每天限调用 50 次，省着点用**

**设置环境变量**：
```bash
export EASTMONEY_APIKEY="你的APIKey"
```

> 建议将上述命令加到 `~/.zshrc` 或 `~/.bashrc` 永久生效。

## API 调用方式

```bash
curl -X POST "https://mkapi2.dfcfs.com/finskillshub/api/claw/query" \
  -H "Content-Type: application/json" \
  -H "apikey: $EASTMONEY_APIKEY" \
  -d '{"toolQuery": "<查询内容>"}'
```

## 查询示例

| 查询类型 | 示例 |
|---------|------|
| 股票行情 | "贵州茅台最新价" |
| 资金流向 | "宁德时代主力资金流向" |
| 财务指标 | "比亚迪2023年净利润" |
| 板块行情 | "新能源汽车板块今日行情" |
| 基金数据 | "易方达蓝筹精选基金净值" |

## 返回数据解析

核心路径 `data.dataTableDTOList[]`：

| 字段 | 含义 |
|-----|------|
| `code` | 证券代码（如 600519.SH） |
| `entityName` | 证券名称 |
| `table` | 表格数据（键=指标编码，值=数值） |
| `nameMap` | 列名映射（编码→中文） |
| `field.returnName` | 指标名称 |
| `entityTagDTO.marketChar` | 市场标识（.SH/.SZ） |

## 数据限制

- 单次查询避免大时间范围（如3年日K），防止返回数据过多导致上下文爆炸
- 复杂查询建议拆分为多次调用

## 数据为空

若返回空结果，提示用户到东方财富妙想 AI 直接查询验证。

## 实现代码

```python
import os
import requests
import json

def query_eastmoney(query: str) -> dict:
    """Query East Money financial data API."""
    api_key = os.environ.get("EASTMONEY_APIKEY")
    if not api_key:
        return {"error": "请设置环境变量 EASTMONEY_APIKEY"}

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }
    payload = {"toolQuery": query}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def parse_result(data: dict) -> str:
    """Parse API result to readable format."""
    if "error" in data:
        return f"查询失败: {data['error']}"

    table_list = data.get("data", {}).get("dataTableDTOList", [])
    if not table_list:
        return "未找到相关数据，请到东方财富妙想AI查询"

    results = []
    for item in table_list:
        name = item.get("entityName", "")
        code = item.get("code", "")
        table = item.get("table", {})
        name_map = item.get("nameMap", {})

        rows = []
        for indicator, values in table.items():
            col_name = name_map.get(indicator, indicator)
            val_str = ", ".join(str(v) for v in values) if values else "N/A"
            rows.append(f"  {col_name}: {val_str}")

        results.append(f"{name} ({code}):\n" + "\n".join(rows))

    return "\n\n".join(results)
```

## 使用示例

```python
# 查询股票行情
result = query_eastmoney("贵州茅台最新价")
print(parse_result(result))

# 查询财务数据
result = query_eastmoney("海康威视2023年营业收入")
print(parse_result(result))
```
