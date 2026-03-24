#!/usr/bin/env python3
"""批量导入 2026-03-22 持仓截图数据。

东方财富证券 (ETF watching, 无成本)
国泰海通证券 (A股ETF/watching, 无成本)
熊猫证券 (港股 holding, 有成本)
"""

import json
import time
import requests

BASE = "http://localhost:3120/api"

HEADERS = {
    "Content-Type": "application/json",
    # 本地认证 header（review-local-env.md）
    "x-ads-token-data": '{"access_token":{"userid":"B8CJF562MZ96SVDT"},"client_id":"gAbnwKAccrMgJ2SAVD7tBODLxii8ijVt"}',
    "x-ads-gateway-secret": "b4e09b5cc70f469860d8a6aa7f4553c5",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def api_add(code: str, data: dict) -> dict:
    """调用 /api/config { action: "add" }"""
    payload = {"action": "add", "code": code, "data": data}
    r = SESSION.post(f"{BASE}/config", json=payload, timeout=10)
    return r.json()

def api_update(code: str, data: dict) -> dict:
    """调用 /api/config { action: "update" }"""
    payload = {"action": "update", "code": code, "data": data}
    r = SESSION.post(f"{BASE}/config", json=payload, timeout=10)
    return r.json()

# ──────────────────────────────────────────────────────────────
# 东方财富证券 — 11只ETF (watching, 无成本价)
# ──────────────────────────────────────────────────────────────
eastmoney_etfs = [
    ("159919", "易方达沪深300ETF", 3000),
    ("510300", "华夏沪深300ETF", 101000),
    ("159915", "易方达创业板ETF", 1000),
    ("588000", "华夏科创50ETF", 900),
    ("159992", "创新药产业ETF", 2200),
    ("159805", "医疗器械ETF", 1000),
    ("588050", "华夏科创板50ETF", 2700),
    ("512480", "半导体ETF", 1000),
    ("512760", "芯片ETF", 1000),
    ("513500", "纳指ETF", 500),
    ("513100", "国泰纳指ETF", 1000),
    ("159941", "纳指ETF", 1000),
]

# ──────────────────────────────────────────────────────────────
# 国泰海通证券 — 24只A股ETF (watching, 无成本价)
# ──────────────────────────────────────────────────────────────
haitong_etfs = [
    ("510210", "券商ETF", 1000),
    ("512000", "券商ETF", 7000),
    ("159931", "港股通ETF", 1000),
    ("513600", "恒生科技ETF", 500),
    ("159941", "纳指ETF", 1900),
    ("159915", "易方达创业板ETF", 10000),
    ("512690", "酒ETF", 5100),
    ("512010", "医药ETF", 10100),
    ("515050", "5G ETF", 10100),
    ("512170", "医疗ETF", 3100),
    ("512760", "芯片ETF", 3100),
    ("516950", "基建ETF", 10100),
    ("515980", "人工智能ETF", 11000),
    ("588000", "华夏科创50ETF", 7100),
    ("515220", "军工ETF", 8100),
    ("515700", "新能源ETF", 5100),
    ("512480", "半导体ETF", 8100),
    ("159992", "创新药产业ETF", 8100),
    ("510900", "H股ETF", 8100),
    ("513500", "纳指ETF", 5100),
    ("513100", "国泰纳指ETF", 5100),
    ("159805", "医疗器械ETF", 4100),
    ("512800", "银行ETF", 9100),
]

# ──────────────────────────────────────────────────────────────
# 熊猫证券 — 8只港股 (holding, 有成本价)
# ──────────────────────────────────────────────────────────────
panda_stocks = [
    ("HK00700", "腾讯控股",    200, 155.0,   100),  # 成本155, 200股
    ("HK03690", "美团-W",     1200, 72.0,   100),  # 成本72, 1200股
    ("HK01810", "小米集团-W",  400, 14.42,  200),  # 成本14.42, 400股
    ("HK02618", "京东健康",   3500, 28.0,  500),  # 成本28, 3500股
    ("HK09988", "阿里巴巴-W",  500, 85.0,  100),  # 成本85, 500股
    ("HK06160", "百济神州",    100, 93.0,   100),  # 成本93, 100股
    ("HK02589", "极智嘉",      2400, 20.68, 100),  # 成本20.68, 2400股
    ("HK02899", "京东物流",    700, 11.28,  200),  # 成本11.28, 700股
]

def import_entries(entries: list, source: str):
    """批量导入，1秒间隔避免限速。"""
    total = len(entries)
    for i, entry in enumerate(entries, 1):
        code = entry["code"]
        data = {k: v for k, v in entry.items() if k not in ("code", "type")}
        result = api_add(code, data)
        msg = result.get("message", "")
        success = result.get("success", False)
        tag = "✅" if success else "❌"
        print(f"[{source}] [{i}/{total}] {tag} {code}: {msg}")
        time.sleep(1)

# ──────────────────────────────────────────────────────────────
# 执行导入
# ──────────────────────────────────────────────────────────────
print("=== 东方财富证券 ETF (watching) ===")
import_entries(
    [{"code": code, "name": name, "shares": shares, "type": "watching"}
     for code, name, shares in eastmoney_etfs],
    "eastmoney"
)

print("\n=== 国泰海通证券 A股ETF (watching) ===")
import_entries(
    [{"code": code, "name": name, "shares": shares, "type": "watching"}
     for code, name, shares in haitong_etfs],
    "haitong"
)

print("\n=== 熊猫证券 港股 (holding) ===")
import_entries(
    [{"code": code, "name": name, "shares": shares, "cost": cost, "lot": lot, "type": "holding"}
     for code, name, shares, cost, lot in panda_stocks],
    "panda"
)

print("\n=== 完成 ===")
