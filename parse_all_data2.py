#!/usr/bin/env python3
"""Improved parser for mx_data raw.json files - dump all data structures."""
import json
import os

def parse_mx_data(filepath):
    """Parse mx_data raw.json file with deep structure exploration."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f, strict=False)
    
    try:
        dto = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    except (KeyError, TypeError) as e:
        return f"Error accessing dataTableDTOList: {e}"
    
    output = []
    for idx, table in enumerate(dto):
        output.append(f"\n=== Table {idx} ===")
        output.append(f"Keys: {list(table.keys())}")
        
        # Check for tableName
        if 'tableName' in table:
            output.append(f"TableName: {table['tableName']}")
        
        # Check for tableDesc
        if 'tableDesc' in table:
            output.append(f"TableDesc: {table['tableDesc']}")
        
        # Try to find rows and headers in various structures
        for key in table:
            val = table[key]
            if key in ('tableName', 'tableDesc'):
                continue
            if isinstance(val, str):
                output.append(f"  {key}: {val}")
            elif isinstance(val, list):
                output.append(f"  {key} (list, len={len(val)}):")
                for i, item in enumerate(val[:20]):
                    if isinstance(item, dict):
                        output.append(f"    [{i}] keys={list(item.keys())}")
                        # Print all key-value pairs
                        for k, v in item.items():
                            output.append(f"        {k}: {v}")
                    elif isinstance(item, list):
                        output.append(f"    [{i}] list: {item}")
                    else:
                        output.append(f"    [{i}] {item}")
                if len(val) > 20:
                    output.append(f"    ... ({len(val)} total)")
            elif isinstance(val, dict):
                output.append(f"  {key} (dict): {json.dumps(val, ensure_ascii=False)[:500]}")
            else:
                output.append(f"  {key}: {val}")
    
    return '\n'.join(output)

stocks = {
    '德赛西威': '/Users/zhul1/Documents/aiWorkspace/ai-investor/德赛西威_分析数据',
    '比亚迪股份': '/Users/zhul1/Documents/aiWorkspace/ai-investor/比亚迪股份_分析数据',
    '澜起科技': '/Users/zhul1/Documents/aiWorkspace/ai-investor/澜起科技_分析数据',
    '禾赛-W': '/Users/zhul1/Documents/aiWorkspace/ai-investor/禾赛-W_分析数据',
}

for stock_name, stock_dir in stocks.items():
    print(f"\n{'='*80}")
    print(f"  {stock_name}")
    print(f"{'='*80}")
    
    for f in sorted(os.listdir(stock_dir)):
        if f.endswith('_raw.json'):
            print(f"\n--- {f} ---")
            filepath = os.path.join(stock_dir, f)
            result = parse_mx_data(filepath)
            print(result)
