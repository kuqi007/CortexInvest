import os
import requests
import json

api_key = os.environ.get("EASTMONEY_APIKEY", "")
if not api_key:
    try:
        api_key = open(os.path.expanduser("~/.eastmoney_api")).read().strip()
    except:
        print(
            "错误: 无法获取 API Key，请先配置 EASTMONEY_APIKEY 环境变量或 ~/.eastmoney_api 文件"
        )
        exit(1)

url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
news_url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search"
headers = {"Content-Type": "application/json", "apikey": api_key}

queries = [
    ("market", "东阳光最新价 涨跌幅 成交额 换手率 总市值"),
    ("financials_3y", "东阳光近三年年报净利润 营业收入 毛利率 净利率 ROE 每股净资产"),
    ("cashflow", "东阳光经营活动现金流量净额 自由现金流 近三年"),
    ("investment_income", "东阳光投资收益 公允价值变动收益 近三年"),
    ("capital_flow", "东阳光近5日主力资金流向 超大单 大单"),
    ("kline_60d", "东阳光近60日日K线 收盘价 成交量"),
    ("valuation", "东阳光市盈率TTM 每股收益EPS 每股净资产BPS"),
    ("revenue_breakdown", "东阳光主营业务收入构成 分产品 分地区"),
    ("earnings_guidance", "东阳光业绩预告"),
    ("shares", "东阳光总股本 流通股"),
    ("cash_reserves", "东阳光货币资金 现金及等价物 短期理财"),
    ("leverage", "东阳光资产负债率 有息负债 利息覆盖倍数"),
    ("peers", "东阳光同行业可比公司 市盈率 市净率"),
]

results = {}

print("=" * 60)
print("东阳光(600673) Phase 1 数据收集")
print("=" * 60)
print()

# 查询金融数据
for key, query in queries:
    print(f"\n[{key}] 查询: {query}")
    try:
        resp = requests.post(
            url, json={"toolQuery": query}, headers=headers, timeout=30
        )
        resp.raise_for_status()
        results[key] = resp.json()

        # 检查是否有数据
        if "data" in results[key] and "dataTableDTOList" in results[key]["data"]:
            count = len(results[key]["data"]["dataTableDTOList"])
            print(f"  ✓ 成功 | 返回 {count} 条数据")
        else:
            print(f"  ✓ 成功 | 返回数据")
    except Exception as e:
        results[key] = {"error": str(e)}
        print(f"  ✗ 失败 | {e}")

# 查询新闻资讯
news_queries = [
    ("news_2025", "东阳光最新消息 研报 2025年"),
    ("business_progress", "东阳光业务进展 产品 竞争"),
]

print("\n" + "=" * 60)
print("新闻资讯查询")
print("=" * 60)

for key, query in news_queries:
    print(f"\n[{key}] 查询: {query}")
    try:
        resp = requests.post(
            news_url, json={"query": query}, headers=headers, timeout=30
        )
        resp.raise_for_status()
        results[key] = resp.json()
        print(f"  ✓ 成功 | 返回数据")
    except Exception as e:
        results[key] = {"error": str(e)}
        print(f"  ✗ 失败 | {e}")

# 保存结果
output_path = "/Users/zhul1/Documents/dev/openWorkspace/ai-investor/reports/600673_phase1_data.json"
os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("\n" + "=" * 60)
print(f"数据已保存至: {output_path}")
print("=" * 60)
print("\n数据收集完成")
