#!/usr/bin/env python3
"""
Screenshot Stock Import Tool

从截图中识别股票信息并导入系统。
支持持仓截图（更新成本和股数）和自选截图（添加股票到自选列表）

Usage:
    uv run python -m src.tools.screenshot_stock_import /path/to/screenshot.png
    uv run python -m src.tools.screenshot_stock_import /path/to/screenshot.png --type holding
    uv run python -m src.tools.screenshot_stock_import /path/to/screenshot.png --type watchlist
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# 尝试导入 PIL 和 pytesseract 进行 OCR
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sim_trading.db import get_config_connection, init_config_db
from src.utils.audit_log import insert_config_outbox
from src.utils.audit_system import build_audit_event_v2, make_actor


def _parse_tags_for_audit(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        tags = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(tags, list):
        return []
    return [str(tag) for tag in tags]

def extract_text_from_image(image_path: Path) -> str:
    """从图片中提取文本"""
    if not HAS_PIL:
        raise ImportError("需要安装 Pillow: uv pip install Pillow")
    if not HAS_TESSERACT:
        raise ImportError("需要安装 pytesseract: uv pip install pytesseract")
    
    image = Image.open(image_path)
    text = pytesseract.image_to_string(image, lang='chi_sim+eng')
    return text


def parse_stocks_from_text(text: str, import_type: str | None = None) -> list[dict[str, Any]]:
    """
    从文本中解析股票信息
    
    Returns:
        List of dict with keys: code, name, cost, shares, is_holding
    """
    stocks = []
    lines = text.split('\n')
    
    # 股票代码模式
    patterns = {
        'hk': r'(?:HK)?0\d{4,5}',  # 港股: HK09903 或 09903
        'a_share': r'\d{6}',       # A股: 6位数字
        'us': r'[A-Z]{1,5}',       # 美股: 字母代码
    }
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        
        stock_info = {'line': line, 'line_num': i}
        
        # 尝试匹配港股代码
        hk_match = re.search(r'(?:HK)?(0\d{4})', line)
        if hk_match:
            code = hk_match.group(1)
            stock_info['code'] = f'HK{code}'
        else:
            # 尝试匹配A股代码
            a_match = re.search(r'(\d{6})', line)
            if a_match:
                code = a_match.group(1)
                # 根据代码前缀判断是沪市还是深市
                if code.startswith(('600', '601', '603', '688', '689')):
                    stock_info['code'] = code
                elif code.startswith(('000', '001', '002', '003', '300')):
                    stock_info['code'] = code
                else:
                    continue
            else:
                continue
        
        # 尝试提取名称（通常是代码前的中文）
        # 查看前一行作为名称
        if i > 0:
            prev_line = lines[i-1].strip()
            if prev_line and not re.match(r'[\d\.]', prev_line) and len(prev_line) < 20:
                stock_info['name'] = prev_line
        
        # 尝试提取价格和股数（持仓截图）
        # 格式通常是: 价格 数量/持仓
        price_match = re.search(r'(\d+\.\d{2,3})', line)
        if price_match:
            price_str = price_match.group(1)
            # 检查是否是合理的价格（有多个数字时取适当范围的）
            price = float(price_str)
            if 1 <= price <= 10000:  # 合理价格范围
                stock_info['cost'] = price
        
        # 尝试提取股数
        shares_match = re.search(r'(\d{3,6})', line)
        if shares_match:
            shares_str = shares_match.group(1)
            shares = int(shares_str)
            if 100 <= shares <= 1000000:  # 合理股数范围
                stock_info['shares'] = shares
        
        # 判断是否是持仓
        if 'cost' in stock_info and 'shares' in stock_info:
            stock_info['is_holding'] = True
        else:
            stock_info['is_holding'] = False
        
        stocks.append(stock_info)
    
    return stocks


def import_stocks_to_db(stocks: list[dict[str, Any]], force_type: str | None = None) -> tuple[int, int]:
    """
    将股票导入数据库
    
    Args:
        stocks: 股票列表
        force_type: 强制类型 'holding' 或 'watching'
    
    Returns:
        (added_count, updated_count)
    """
    init_config_db()
    conn = get_config_connection()
    now_ts = int(time.time())
    
    added = 0
    updated = 0
    
    codes = [str(stock.get("code")) for stock in stocks if stock.get("code")]

    def snapshot() -> list[dict[str, Any]]:
        if not codes:
            return []
        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(
            f"""
            SELECT symbol, name, list_type, cost, shares, hidden, star, dip_buy,
                   alias, lot, tags, watch_price, watch_price_date, pin_order,
                   created_at, updated_at
            FROM monitor_watchlist
            WHERE symbol IN ({placeholders})
            ORDER BY symbol
            """,
            tuple(codes),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = _parse_tags_for_audit(item.get("tags"))
            result.append(item)
        return result

    try:
        conn.execute("BEGIN")
        before = snapshot()
        for stock in stocks:
            code = stock.get('code')
            name = stock.get('name', code)
            
            if not code:
                continue
            
            # 确定类型
            is_holding = stock.get('is_holding', False)
            if force_type == 'holding':
                is_holding = True
            elif force_type == 'watchlist':
                is_holding = False
            
            list_type = 'holding' if is_holding else 'watching'
            
            # 检查是否已存在
            cursor = conn.execute(
                'SELECT symbol FROM monitor_watchlist WHERE symbol = ?',
                (code,)
            )
            existing = cursor.fetchone()
            
            if existing:
                # 更新现有记录
                if is_holding and 'cost' in stock:
                    shares = stock.get('shares')
                    if shares is None:
                        raise ValueError(f'{code} holding import missing shares')
                    conn.execute(
                        '''UPDATE monitor_watchlist 
                           SET name = ?, list_type = 'holding', cost = ?, shares = ?, 
                               updated_at = ?, star = 1
                           WHERE symbol = ?''',
                        (name, stock.get('cost'), shares, now_ts, code)
                    )
                    print(f'  🔄 {code} ({name}) -> 更新持仓: 成本 {stock.get("cost")}, 股数 {shares}')
                else:
                    conn.execute(
                        '''UPDATE monitor_watchlist 
                           SET name = ?, star = 1, updated_at = ?
                           WHERE symbol = ?''',
                        (name, now_ts, code)
                    )
                    print(f'  ⭐ {code} ({name}) -> 更新为特别关注')
                updated += 1
            else:
                # 插入新记录
                if is_holding and 'cost' in stock:
                    shares = stock.get('shares')
                    if shares is None:
                        raise ValueError(f'{code} holding import missing shares')
                    conn.execute(
                        '''INSERT INTO monitor_watchlist 
                           (symbol, name, list_type, cost, shares, hidden, star, dip_buy, tags, created_at, updated_at)
                           VALUES (?, ?, 'holding', ?, ?, 0, 1, 0, '[]', ?, ?)''',
                        (code, name, stock.get('cost'), shares, now_ts, now_ts)
                    )
                    print(f'  ✅ {code} ({name}) -> 添加持仓: 成本 {stock.get("cost")}, 股数 {shares}')
                else:
                    conn.execute(
                        '''INSERT INTO monitor_watchlist 
                           (symbol, name, list_type, hidden, star, dip_buy, tags, created_at, updated_at)
                           VALUES (?, ?, 'watching', 0, 1, 0, '[]', ?, ?)''',
                        (code, name, now_ts, now_ts)
                    )
                    print(f'  ✅ {code} ({name}) -> 添加特别关注')
                added += 1

        if added or updated:
            after = snapshot()
            event = build_audit_event_v2(
                event_id=uuid.uuid4().hex,
                ts_ms=int(time.time() * 1000),
                source="screenshot_stock_import",
                actor=make_actor(actor_type="system", actor_id="screenshot_stock_import"),
                action="import",
                entity="monitor_watchlist",
                key=",".join(codes),
                db_name="config.db",
                before={"watchlist": before},
                after={"watchlist": after},
                metadata={
                    "force_type": force_type,
                    "added": added,
                    "updated": updated,
                    "input_count": len(stocks),
                },
            )
            insert_config_outbox(conn, event)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    
    return added, updated

def main():
    parser = argparse.ArgumentParser(description='从截图导入股票信息')
    parser.add_argument('image_path', type=Path, help='截图文件路径')
    parser.add_argument('--type', choices=['holding', 'watchlist'], 
                        help='强制指定类型: holding=持仓, watchlist=自选')
    
    args = parser.parse_args()
    
    if not args.image_path.exists():
        print(f'错误: 文件不存在 {args.image_path}')
        sys.exit(1)
    
    print(f'正在处理截图: {args.image_path}')
    print()
    
    try:
        # 提取文本
        text = extract_text_from_image(args.image_path)
        print('OCR 识别文本:')
        print('-' * 50)
        print(text[:500] + '...' if len(text) > 500 else text)
        print('-' * 50)
        print()
        
        # 解析股票
        stocks = parse_stocks_from_text(text, args.type)
        
        if not stocks:
            print('未识别到股票信息')
            sys.exit(1)
        
        print(f'识别到 {len(stocks)} 只股票:')
        for s in stocks:
            print(f'  {s.get("code", "?")} - {s.get("name", "Unknown")} ' 
                  f'(成本:{s.get("cost", "-")}, 股数:{s.get("shares", "-")})')
        print()
        
        # 导入数据库
        added, updated = import_stocks_to_db(stocks, args.type)
        
        print()
        print(f'共处理 {len(stocks)} 只股票')
        print(f'  新增: {added}')
        print(f'  更新: {updated}')
        print('配置已写入 config.db；JSON 快照由专用导出流程生成。')
        
    except Exception as e:
        print(f'错误: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
