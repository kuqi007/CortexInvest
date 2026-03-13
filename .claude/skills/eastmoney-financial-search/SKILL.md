---
name: eastmoney-financial-search
description: 搜索东方财富金融资讯（新闻、研报、公告、政策），基于权威信源获取时效性信息
argument-hint: [查询内容]
---

# 东方财富资讯搜索 Skill

基于东方财富妙想搜索能力，检索金融资讯、新闻、研报、公告、政策等时效性信息。

## 适用场景

- 查询个股最新资讯、研报、机构观点
- 查询板块/主题近期新闻、政策解读
- 查询宏观事件影响分析
- 查询特定事件、交易规则等非常识信息

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
curl -X POST "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search" \
  -H "Content-Type: application/json" \
  -H "apikey: $EASTMONEY_APIKEY" \
  -d '{"query": "<查询内容>"}'
```

## 查询示例

| 类型 | 示例 |
|-----|------|
| 个股资讯 | "立讯精密最新研报" |
| 个股观点 | "贵州茅台机构观点" |
| 板块新闻 | "商业航天板块近期新闻" |
| 政策解读 | "新能源政策解读" |
| 宏观分析 | "美联储加息对A股影响" |
| 大盘解读 | "今日大盘异动原因" |

## 返回数据解析

| 字段 | 含义 |
|-----|------|
| `title` | 资讯标题 |
| `secuList[].secuCode` | 关联证券代码 |
| `secuList[].secuName` | 关联证券名称 |
| `trunk` | 资讯正文/核心内容 |

## 实现代码

```python
import os
import requests
import json

def search_eastmoney_news(query: str) -> dict:
    """Search East Money financial news."""
    api_key = os.environ.get("EASTMONEY_APIKEY")
    if not api_key:
        return {"error": "请设置环境变量 EASTMONEY_APIKEY"}

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }
    payload = {"query": query}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def parse_news_result(data: dict) -> str:
    """Parse news search result to readable format."""
    if "error" in data:
        return f"搜索失败: {data['error']}"

    news_list = data.get("data", {}).get("list", [])
    if not news_list:
        return "未找到相关资讯"

    results = []
    for item in news_list:
        title = item.get("title", "")
        secu_list = item.get("secuList", [])
        trunk = item.get("trunk", "")[:500]  # 截断太长

        securities = ", ".join(
            f"{s.get('secuName', '')}({s.get('secuCode', '')})"
            for s in secu_list[:3]
        )

        results.append(f"**{title}**")
        if securities:
            results.append(f"关联: {securities}")
        if trunk:
            results.append(f"摘要: {trunk}...")
        results.append("")

    return "\n".join(results)
```

## 使用示例

```python
# 搜索个股资讯
result = search_eastmoney_news("立讯精密最新研报")
print(parse_news_result(result))

# 搜索政策解读
result = search_eastmoney_news("新能源政策解读")
print(parse_news_result(result))
```
