#!/usr/bin/env python3
"""Extract key financial metrics from raw.json for all 4 stocks."""
import json
import os

def extract_tables(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f, strict=False)
    dto = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    results = []
    for table in dto:
        t = table.get('table', {})
        nm = table.get('nameMap', {})
        raw = table.get('rawTable', {})
        title = table.get('inputTitle', table.get('title', table.get('frontendTitle', '')))
        code = table.get('code', '')
        results.append({'table': t, 'nameMap': nm, 'rawTable': raw, 'title': title, 'code': code})
    return results

base = '/Users/zhul1/Documents/aiWorkspace/ai-investor'

# 1. 德赛西威
print("=== 德赛西威 ===")
for fn in ['mx_data_查询德赛西威最新股价_市值_市盈率_市净率_每股收益_raw.json',
           'mx_data_查询德赛西威近三年营业收入_净利润_ROE_毛利率_raw.json',
           'mx_data_查询德赛西威每股净资产_每股未分配利润_资产负债率_raw.json']:
    fp = os.path.join(base, '德赛西威_分析数据', fn)
    tables = extract_tables(fp)
    for t in tables:
        print(f"\n  [{t['code']}] {t['title']}")
        tbl = t['table']
        nm = t['nameMap']
        # Print as key-value
        for k, v in tbl.items():
            name = nm.get(k, k)
            if isinstance(v, list):
                print(f"    {name} ({k}): {v}")
            else:
                print(f"    {name} ({k}): {v}")

print("\n\n=== 比亚迪股份 ===")
for fn in ['mx_data_查询比亚迪股份最新股价_市值_市盈率_市净率_每股收益_raw.json',
           'mx_data_查询比亚迪股份近三年营业收入_净利润_ROE_毛利率_raw.json',
           'mx_data_查询比亚迪股份每股净资产_每股未分配利润_资产负债率_raw.json']:
    fp = os.path.join(base, '比亚迪股份_分析数据', fn)
    tables = extract_tables(fp)
    for t in tables:
        print(f"\n  [{t['code']}] {t['title']}")
        tbl = t['table']
        nm = t['nameMap']
        for k, v in tbl.items():
            name = nm.get(k, k)
            if isinstance(v, list):
                print(f"    {name} ({k}): {v}")
            else:
                print(f"    {name} ({k}): {v}")

print("\n\n=== 澜起科技 ===")
for fn in ['mx_data_查询澜起科技最新股价_市值_市盈率_市净率_每股收益_raw.json',
           'mx_data_查询澜起科技近三年营业收入_净利润_ROE_毛利率_raw.json',
           'mx_data_查询澜起科技每股净资产_每股未分配利润_资产负债率_raw.json']:
    fp = os.path.join(base, '澜起科技_分析数据', fn)
    tables = extract_tables(fp)
    for t in tables:
        print(f"\n  [{t['code']}] {t['title']}")
        tbl = t['table']
        nm = t['nameMap']
        for k, v in tbl.items():
            name = nm.get(k, k)
            if isinstance(v, list):
                print(f"    {name} ({k}): {v}")
            else:
                print(f"    {name} ({k}): {v}")

print("\n\n=== 禾赛-W ===")
for fn in ['mx_data_禾赛科技最新价_总市值_市净率_市盈率_raw.json',
           'mx_data_禾赛科技近三年营业收入_毛利率_净利润_扣非净利润_净利率_ROE_raw.json',
           'mx_data_查询禾赛科技每股净资产、每股未分配利润、资产负债率_raw.json']:
    fp = os.path.join(base, '禾赛-W_分析数据', fn)
    tables = extract_tables(fp)
    for t in tables:
        print(f"\n  [{t['code']}] {t['title']}")
        tbl = t['table']
        nm = t['nameMap']
        for k, v in tbl.items():
            name = nm.get(k, k)
            if isinstance(v, list):
                print(f"    {name} ({k}): {v}")
            else:
                print(f"    {name} ({k}): {v}")
