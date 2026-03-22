---
name: mx_search
description: 基于东方财富妙想搜索获取金融资讯（新闻、研报、公告、政策），用于时效性信息或特定事件信息查询
user-invocable: true
---

# 妙想资讯搜索 (mx_search)

基于东方财富妙想搜索能力，智能筛选金融场景权威信源，获取新闻、研报、公告、政策等时效性信息。

## 适用场景

- 个股资讯（研报、机构观点、公司新闻）
- 板块/主题（行业动态、政策解读）
- 宏观/风险（汇率、美联储、A股影响）
- 事件驱动（业绩公告、并购重组、大宗交易）
- 综合解读（大盘异动原因、北向资金流向）

## API Key 配置

优先从环境变量 `MX_APIKEY` 读取，其次从 `~/.mx_api` 文件读取。

```bash
# 方式1: 环境变量
export MX_APIKEY="your_key_here"

# 方式2: 文件配置
echo "your_key_here" > ~/.mx_api
```

## 调用接口

```bash
curl -X POST 'https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search' \
  -H 'Content-Type: application/json' \
  -H 'apikey: <MX_APIKEY>' \
  -d '{"query": "<搜索内容>"}'
```

## 返回字段

| 字段 | 释义 |
|------|------|
| `title` | 资讯标题 |
| `secuList[].secuCode` | 关联证券代码 |
| `secuList[].secuName` | 关联证券名称 |
| `trunk` | 核心正文/结构化数据 |

## 问句示例

| 类型 | 示例 |
|------|------|
| 个股资讯 | "东阳光600673最新研报" |
| 板块动态 | "锂电池板块近期新闻" |
| 政策解读 | "新能源政策变化" |
| 事件 | "某公司业绩预告" |

## Python 调用封装

```python
import os, requests

def mx_search(query: str) -> dict:
    api_key = os.environ.get("MX_APIKEY")
    if not api_key:
        try:
            with open(os.path.expanduser("~/.mx_api")) as f:
                api_key = f.read().strip()
        except FileNotFoundError:
            with open(os.path.expanduser("~/.eastmoney_api")) as f:
                api_key = f.read().strip()
    if not api_key:
        raise ValueError("请配置 MX_APIKEY 环境变量")

    resp = requests.post(
        "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search",
        headers={"Content-Type": "application/json", "apikey": api_key},
        json={"query": query},
        timeout=20
    )
    resp.raise_for_status()
    return resp.json()
```
