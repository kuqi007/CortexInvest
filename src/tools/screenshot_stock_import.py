#!/usr/bin/env python3
"""
Screenshot Stock Import Tool

Vision 流程（CLI）：`--dry-run` 生成冻结的 ImportPlan JSON；`--apply --plan` 经 Config API 落库。

遗留 OCR 方法仍保留：`extract_text_from_image`、`parse_stocks_from_text`、`import_stocks_to_db`（供脚本/测试调用）。

Usage:
    uv run python -m src.tools.screenshot_stock_import shot.png --dry-run
    uv run python -m src.tools.screenshot_stock_import --apply --plan import_plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

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
from src.tools.screenshot_import import DEFAULT_CONFIRM_THRESHOLD
from src.tools.screenshot_import.classifier import classify_fingerprint, extract_fingerprint
from src.tools.screenshot_import.config_api import ConfigApiClient, ConfigApiError
from src.tools.screenshot_import.debug_artifacts import tier_a_debug_payload, write_tier_a_bundle
from src.tools.screenshot_import.models import ApplyLogEntry, ImportPlan, VisionResponse
from src.tools.screenshot_import.path_safety import (
    PathSafetyError,
    atomic_write_json,
    validate_debug_output_dir,
    validate_existing_image_path,
    validate_existing_plan_json_path,
    validate_output_path,
)
from src.tools.screenshot_import.planner import (
    apply_log_request_payload_hash,
    build_import_plan,
    verify_import_plan_integrity,
)
from src.tools.screenshot_import.prompts import (
    build_upload_disclosure,
    build_vision_prompt,
)
from src.tools.screenshot_import.providers import VisionRequest, create_provider, resolve_provider_name
from src.tools.screenshot_import.validator import normalize_row
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


def _content_fingerprint_sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _image_mime_type(image_path: Path) -> str:
    if not HAS_PIL:
        return "image/png"
    try:
        with Image.open(image_path) as im:
            fmt = (im.format or "").upper()
    except OSError:
        return "image/png"
    mapping = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "GIF": "image/gif",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
        "TIFF": "image/tiff",
    }
    return mapping.get(fmt, "image/png")


def _collect_plan_codes(plan: ImportPlan) -> list[str]:
    codes = [a.code for a in plan.auto_apply] + [a.code for a in plan.needs_confirmation]
    for r in plan.rejected:
        c = r.get("code")
        if c is not None:
            codes.append(str(c))
    return codes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vision screenshot import (frozen plan) or apply a saved plan."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Recognize image and write ImportPlan JSON")
    mode.add_argument("--apply", action="store_true", help="Apply auto_apply from --plan JSON via config API")

    parser.add_argument(
        "image_path",
        nargs="?",
        type=Path,
        help="Screenshot path (required for --dry-run)",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "kimi", "glm", "minimax"],
        default="auto",
    )
    parser.add_argument("--yes", action="store_true", help="With uncertain auto classification, force exit 3 instead of calling vision (same as non-interactive stdin)")
    parser.add_argument("--plan", type=Path, help="ImportPlan JSON for --apply")
    parser.add_argument("--confirm-threshold", type=float, default=DEFAULT_CONFIRM_THRESHOLD)
    parser.add_argument("--type", dest="shot_type", choices=["auto", "holding", "watchlist"], default="auto")
    parser.add_argument(
        "--platform",
        choices=["auto", "ths", "eastmoney", "hk_panda", "other"],
        default="auto",
    )
    parser.add_argument("--output-json", type=Path, help="Write plan to this path (default: under .screenshot_import_runs/)")
    parser.add_argument("--debug-dir", type=Path, default=None)
    parser.add_argument("--debug-sensitive", action="store_true")
    parser.add_argument("--allow-path", action="append", default=[], metavar="PATH", help="Additional allowed root (repeatable)")

    args = parser.parse_args(argv)

    if args.apply:
        if not args.plan:
            print("错误: --apply 需要 --plan", file=sys.stderr)
            return 1
        extra_roots = [Path(p).expanduser() for p in (args.allow_path or [])]
        allowed_roots_apply = [PROJECT_ROOT, Path.home() / "Downloads", *extra_roots]
        try:
            plan_path = validate_existing_plan_json_path(args.plan, allowed_roots_apply)
        except PathSafetyError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2
        try:
            raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan = ImportPlan.model_validate(raw_plan)
        except (json.JSONDecodeError, ValueError, ValidationError) as e:
            print(f"错误: 无法加载计划文件: {e}", file=sys.stderr)
            return 2

        ok, integrity_msg = verify_import_plan_integrity(plan)
        if not ok:
            print(f"错误: {integrity_msg}", file=sys.stderr)
            return 5

        client = ConfigApiClient()
        successful_ids = {e.row_apply_id for e in plan.apply_log if e.response_ok}
        to_apply = [a for a in plan.auto_apply if a.row_apply_id not in successful_ids]
        for a in plan.auto_apply:
            if a.row_apply_id in successful_ids:
                print(f"  skip (already applied)  {a.code}")

        if not to_apply:
            if plan.auto_apply:
                print("  no pending auto_apply rows")
            return 0

        result = client.apply_actions(to_apply, import_run_id=plan.import_run_id)
        new_entries: list[ApplyLogEntry] = []
        for action, row in zip(to_apply, result.rows, strict=True):
            new_entries.append(
                ApplyLogEntry(
                    row_apply_id=action.row_apply_id,
                    code=action.code,
                    action=action.action,
                    request_payload_hash=apply_log_request_payload_hash(action.payload),
                    response_ok=row.ok,
                    ts=datetime.now(timezone.utc),
                    http_status=row.status_code,
                    message=row.message or None,
                )
            )
        updated_plan = plan.model_copy(update={"apply_log": [*plan.apply_log, *new_entries]})
        atomic_write_json(plan_path, updated_plan.model_dump(mode="json"))

        for row in result.applied:
            print(f"  applied  {row.code}  HTTP {row.status_code}")
        for row in result.failed:
            print(f"  failed   {row.code}  HTTP {row.status_code}  {row.message}")
        return 6 if result.failed else 0

    if not args.image_path:
        print("错误: --dry-run 需要截图路径", file=sys.stderr)
        return 2

    extra_roots = [Path(p).expanduser() for p in (args.allow_path or [])]
    allowed_roots = [PROJECT_ROOT, Path.home() / "Downloads", *extra_roots]

    debug_dir: Path | None = None
    if args.debug_dir is not None:
        try:
            debug_dir = validate_debug_output_dir(args.debug_dir, allowed_roots)
        except PathSafetyError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2

    try:
        image_path = validate_existing_image_path(args.image_path, allowed_roots)
    except PathSafetyError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    fingerprint = extract_fingerprint(image_path)
    forced_platform = args.platform
    forced_type = args.shot_type
    classification = classify_fingerprint(
        fingerprint,
        forced_platform=forced_platform,
        forced_type=forced_type,
    )

    confirm_thr = float(args.confirm_threshold)
    auto_class_low_confidence = (
        args.platform == "auto"
        and args.shot_type == "auto"
        and classification.confidence < confirm_thr
    )
    if auto_class_low_confidence and (not sys.stdin.isatty() or args.yes):
        print(
            json.dumps(
                {"needs_user_input": True, "classification": classification.model_dump(mode="json")},
                ensure_ascii=False,
            )
        )
        return 3

    try:
        resolved_name = resolve_provider_name(args.provider)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    print(build_upload_disclosure(resolved_name))

    try:
        provider = create_provider(args.provider)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    existing_codes: set[str] = set()
    existing_watchlist: dict[str, object] | None = None
    try:
        wl = ConfigApiClient().fetch_watchlist()
        if isinstance(wl, dict):
            existing_watchlist = wl
            existing_codes = {str(k) for k in wl.keys()}
    except ConfigApiError:
        pass
    except OSError:
        pass

    prompt = build_vision_prompt(classification)
    recognition_run_id = uuid.uuid4().hex
    mime_type = _image_mime_type(image_path)
    request = VisionRequest(
        image_path=str(image_path),
        mime_type=mime_type,
        prompt=prompt,
        json_schema=VisionResponse.model_json_schema(),
        timeout_s=60.0,
        run_id=recognition_run_id,
    )
    outcome = provider.complete(request)
    if not outcome.ok or outcome.response is None:
        msg = outcome.error.message if outcome.error else "vision request failed"
        print(f"错误: {msg}", file=sys.stderr)
        return 4

    norm_rows: list[Any] = []
    for i, stock in enumerate(outcome.response.stocks):
        try:
            norm_rows.append(normalize_row(stock, source_row_index=i))
        except (TypeError, ValueError) as e:
            print(f"警告: 跳过无法规范化的行 {i}: {e}", file=sys.stderr)

    content_fp = _content_fingerprint_sha256(image_path)
    manual_platform = args.platform != "auto" and classification.confidence < confirm_thr
    model_confidence = float(outcome.response.confidence)

    plan = build_import_plan(
        norm_rows,
        provider=outcome.provider,
        model=outcome.model,
        classification=classification,
        model_confidence=model_confidence,
        threshold=confirm_thr,
        content_fingerprint=content_fp,
        existing_codes=existing_codes,
        existing_watchlist=existing_watchlist,
        manual_platform_after_low_confidence=manual_platform,
    )

    if args.output_json is not None:
        out_path = validate_output_path(args.output_json, allowed_roots)
    else:
        default_path = (
            PROJECT_ROOT / ".screenshot_import_runs" / plan.import_run_id / "import_plan.json"
        )
        out_path = validate_output_path(default_path, allowed_roots)

    atomic_write_json(out_path, plan.model_dump(mode="json"))

    if debug_dir is not None:
        plan_summary_obj = tier_a_debug_payload(
            outcome.provider,
            _collect_plan_codes(plan),
            {
                "classifier": float(classification.confidence),
                "model": model_confidence,
            },
            {
                "auto_apply_count": len(plan.auto_apply),
                "needs_confirmation_count": len(plan.needs_confirmation),
                "rejected_count": len(plan.rejected),
            },
        )
        fp_redacted = {
            "width": fingerprint.width,
            "height": fingerprint.height,
            "layout": fingerprint.layout,
            "classification_confidence": float(classification.confidence),
        }
        prov_redacted = {
            "provider": outcome.provider,
            "model": outcome.model,
            "mime_type": mime_type,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "run_id": recognition_run_id,
        }
        write_tier_a_bundle(
            debug_dir,
            image_fingerprint_redacted=fp_redacted,
            provider_request_redacted=prov_redacted,
            import_plan_summary_redacted=plan_summary_obj,
        )
        if args.debug_sensitive:
            dbg_fp = asdict(fingerprint)
            (debug_dir / "fingerprint.json").write_text(
                json.dumps(dbg_fp, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            (debug_dir / "classification.json").write_text(
                json.dumps(classification.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (debug_dir / "provider_response.json").write_text(
                json.dumps(outcome.response.model_dump(mode="json"), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            validated_payload = [r.model_dump(mode="json") for r in norm_rows]
            (debug_dir / "validated_result.json").write_text(
                json.dumps(validated_payload, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            (debug_dir / "import_plan.json").write_text(
                json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str)
                + "\n",
                encoding="utf-8",
            )

    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
