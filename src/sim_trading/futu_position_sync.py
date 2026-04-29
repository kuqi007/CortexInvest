"""Futu 持仓同步 — Futu positions → SQLite live_state/trades/daily_pnl。

职责:
  1. sync_live_state: Futu get_positions() → live_state 表（替代影子 PM 的 _persist_state）
  2. detect_and_save_closed: 对比前后快照，检测平仓 → trades 表
  3. save_daily_pnl: get_funds() → daily_pnl 表
  4. save_order / update_order_status: futu_orders 审计表
"""

import json
import logging
import time
import uuid
from datetime import datetime

from .db import get_connection, get_config_connection, init_db
from .futu_trade_adapter import FutuTradeAdapter, FutuPosition, OrderUpdate
from src.utils.audit_system import make_actor
from src.utils.audit_writer import record_db_change_best_effort

logger = logging.getLogger("l2_daemon.futu_sync")

PARAM_VERSION = "live"


def _futu_actor() -> dict[str, str]:
    return make_actor(actor_type="system", actor_id="futu_position_sync")


def _get_watch_row(conn, code: str) -> dict | None:
    """从 monitor_watchlist 读取单条记录，返回 dict 或 None。"""
    try:
        row = conn.execute(
            "SELECT shares, cost, list_type FROM monitor_watchlist WHERE symbol = ?",
            (code,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


class FutuPositionSync:
    """Futu 持仓 → SQLite 同步器。"""

    def __init__(self, adapter: FutuTradeAdapter):
        self._adapter = adapter
        self._prev_positions: dict[str, FutuPosition] = {}
        init_db()

    def sync_live_state(self, daily_scores: dict[str, int] | None = None) -> None:
        """从 Futu get_positions() 读取，写入 live_state 表。

        Args:
            daily_scores: {code: score_int} 可选，用于填充 daily_score 列。
        """
        positions = self._adapter.get_positions()
        if not positions and not self._prev_positions:
            return

        scores = daily_scores or {}
        now_ts = int(time.time() * 1000)
        conn = get_connection()
        config_conn = get_config_connection()
        try:
            before_rows = conn.execute("SELECT * FROM live_state").fetchall()
            before_by_code = {
                row["code"]: {
                    "code": row["code"],
                    "name": row["name"],
                    "entry_price": row["entry_price"],
                    "quantity": row["quantity"],
                    "entry_time": row["entry_time"],
                    "entry_date": row["entry_date"],
                    "stop_loss": row["stop_loss"],
                    "take_profit": row["take_profit"],
                    "max_hold_days": row["max_hold_days"],
                    "entry_strategy": row["entry_strategy"],
                    "confidence": row["confidence"],
                    "trigger_signals": row["trigger_signals"],
                    "buy_cost_per_share": row["buy_cost_per_share"],
                    "atr_at_entry": row["atr_at_entry"],
                }
                for row in before_rows
            }
            after_by_code: dict[str, dict] = {}
            # 清理不再持有的
            current_codes = set(positions.keys())
            if current_codes:
                placeholders = ",".join("?" for _ in current_codes)
                conn.execute(
                    f"DELETE FROM live_state WHERE code NOT IN ({placeholders})",
                    list(current_codes),
                )
            else:
                conn.execute("DELETE FROM live_state")

            for code, fp in positions.items():
                unrealized = fp.unrealized_pnl
                pnl_pct = (
                    (fp.market_val / (fp.avg_price * fp.quantity) - 1)
                    if fp.avg_price > 0 and fp.quantity > 0
                    else 0
                )

                # Preserve existing risk params set by dip-buy or RT engine.
                # NOTE: use "existing" check (not entry_time truthiness) to avoid
                # entry_time=0 being permanently stuck in the else branch.
                existing = conn.execute(
                    "SELECT entry_time, entry_date, stop_loss, take_profit, "
                    "max_hold_days, entry_strategy, confidence, trigger_signals, "
                    "buy_cost_per_share, atr_at_entry FROM live_state WHERE code = ?",
                    (code,),
                ).fetchone()
                if existing and existing["entry_time"] and existing["entry_time"] > 0:
                    # Existing position with valid entry_time — preserve all fields
                    entry_time = existing["entry_time"]
                    entry_date = existing["entry_date"] or ""
                    sl = existing["stop_loss"] or 0
                    tp = existing["take_profit"]
                    max_hold = existing["max_hold_days"] or 10
                    strategy = existing["entry_strategy"] or "futu_sim"
                    confidence = existing["confidence"] or 0.0
                    signals = existing["trigger_signals"] or "[]"
                    bps = existing["buy_cost_per_share"] or 0
                    atr_entry = existing["atr_at_entry"] or 0
                else:
                    # New position or entry_time was 0 (corrupted by prior bug):
                    # assign now_ts + default SL to ensure risk protection.
                    now_date = datetime.now().strftime("%Y-%m-%d")
                    default_sl = round(fp.avg_price * 0.90, 4)
                    entry_time = now_ts
                    entry_date = now_date
                    sl = default_sl
                    tp = None
                    max_hold = 10
                    strategy = "futu_sim"
                    confidence = 0.0
                    signals = "[]"
                    bps = 0
                    atr_entry = 0
                    logger.info(
                        f"sync_live_state: new Futu position {code} "
                        f"entry_time={now_ts}, SL={default_sl:.2f}"
                    )

                conn.execute(
                    """INSERT OR REPLACE INTO live_state
                       (code, name, entry_price, quantity, current_price, entry_time, entry_date,
                        stop_loss, take_profit, max_hold_days, entry_strategy, confidence,
                        trigger_signals, unrealized_pnl, pnl_pct, daily_score,
                        buy_cost_per_share, atr_at_entry, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        code,
                        fp.name,
                        fp.avg_price,
                        fp.quantity,
                        round(fp.market_val / fp.quantity, 4) if fp.quantity > 0 else 0,
                        entry_time,
                        entry_date,
                        sl,
                        tp,
                        max_hold,
                        strategy,
                        confidence,
                        signals,
                        round(unrealized, 2),
                        round(pnl_pct, 6),
                        scores.get(code, 0),
                        bps,
                        atr_entry,
                        now_ts,
                    ),
                )
                after_by_code[code] = {
                    "code": code,
                    "name": fp.name,
                    "entry_price": fp.avg_price,
                    "quantity": fp.quantity,
                    "entry_time": entry_time,
                    "entry_date": entry_date,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "max_hold_days": max_hold,
                    "entry_strategy": strategy,
                    "confidence": confidence,
                    "trigger_signals": signals,
                    "buy_cost_per_share": bps,
                    "atr_at_entry": atr_entry,
                }

                # ── 持仓变更记录（按标的，须与当前 fp 同次循环内写入）──
                old_row = _get_watch_row(config_conn, code)
                if old_row:
                    old_shares = old_row.get("shares")
                    old_cost = old_row.get("cost")
                    old_type = old_row.get("list_type")
                    new_shares = fp.quantity
                    new_cost = fp.avg_price if fp.avg_price > 0 else None
                    new_type = "holding" if fp.quantity > 0 else old_type
                    shares_or_cost_changed = (
                        old_shares != new_shares or old_cost != new_cost
                    )
                    type_changed = old_type != new_type
                    if shares_or_cost_changed or type_changed:
                        now_iso = datetime.now().isoformat(timespec="seconds")
                        try:
                            cursor = config_conn.execute(
                                """
                                INSERT OR IGNORE INTO position_change_log
                                  (symbol, ts, source, shares_from, shares_to, cost_from, cost_to, type_from, type_to)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                                (
                                    code,
                                    now_iso,
                                    "sync",
                                    old_shares,
                                    new_shares if new_shares > 0 else None,
                                    old_cost,
                                    new_cost,
                                    old_type if type_changed else None,
                                    new_type if type_changed else None,
                                ),
                            )
                            if cursor.rowcount:
                                record_db_change_best_effort(
                                    config_conn,
                                    db_name="config.db",
                                    table="position_change_log",
                                    action="create",
                                    key=f"{code}:{now_iso}:sync",
                                    source="futu_position_sync",
                                    actor=_futu_actor(),
                                    before=None,
                                    after={
                                        "symbol": code,
                                        "ts": now_iso,
                                        "source": "sync",
                                        "shares_from": old_shares,
                                        "shares_to": new_shares if new_shares > 0 else None,
                                        "cost_from": old_cost,
                                        "cost_to": new_cost,
                                        "type_from": old_type if type_changed else None,
                                        "type_to": new_type if type_changed else None,
                                    },
                                )
                            config_conn.commit()
                        except Exception as pcl_err:
                            logger.warning(
                                f"position_change_log write failed: {pcl_err}"
                            )

            actor = _futu_actor()
            for code, before in before_by_code.items():
                if code not in after_by_code:
                    record_db_change_best_effort(
                        conn,
                        db_name="trading.db",
                        table="live_state",
                        action="delete",
                        key=code,
                        source="futu_position_sync",
                        actor=actor,
                        before=before,
                        after=None,
                    )
            for code, after in after_by_code.items():
                before = before_by_code.get(code)
                if before == after:
                    continue
                record_db_change_best_effort(
                    conn,
                    db_name="trading.db",
                    table="live_state",
                    action="create" if before is None else "update",
                    key=code,
                    source="futu_position_sync",
                    actor=actor,
                    before=before,
                    after=after,
                )

            conn.commit()
        except Exception as e:
            logger.error(f"sync_live_state failed: {e}")
        finally:
            conn.close()
            if config_conn is not conn:
                config_conn.close()

        # 更新前一快照（用于 detect_and_save_closed）
        self._prev_positions = positions

    def detect_and_save_closed(self) -> list[dict]:
        """对比前后快照，检测平仓 → 写入 trades 表。

        需在 sync_live_state() 之前调用（用旧 prev vs 新 current）。
        """
        current = self._adapter.get_positions()
        closed_trades = []

        for code, prev_pos in self._prev_positions.items():
            if code not in current:
                # 完全平仓
                trade = self._create_trade_record(prev_pos, 0, "futu_close")
                if trade:
                    closed_trades.append(trade)
            else:
                cur_pos = current[code]
                if cur_pos.quantity < prev_pos.quantity:
                    # 部分平仓
                    trade = self._create_trade_record(
                        prev_pos,
                        prev_pos.quantity - cur_pos.quantity,
                        "futu_partial_close",
                    )
                    if trade:
                        closed_trades.append(trade)

        # 保存到 DB
        for trade in closed_trades:
            self._save_trade(trade)

        return closed_trades

    def _create_trade_record(
        self, prev_pos: FutuPosition, close_qty: int, reason: str
    ) -> dict | None:
        """从前后持仓变化构建 trade record。"""
        qty = close_qty if close_qty > 0 else prev_pos.quantity
        if qty <= 0:
            return None

        # 退出价 = 当前市值 / 当前数量（近似）
        # 更精确的方式需要查询 Futu order 的成交均价
        exit_price = prev_pos.avg_price  # fallback to avg_price

        now_ts = int(time.time() * 1000)
        today = datetime.now().strftime("%Y-%m-%d")

        pnl = prev_pos.unrealized_pnl if close_qty == 0 else 0
        pnl_pct = (
            pnl / (prev_pos.avg_price * qty)
            if prev_pos.avg_price > 0 and qty > 0
            else 0
        )

        # Read entry_time/entry_date from live_state (authoritative source)
        entry_time = 0
        entry_date = ""
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT entry_time, entry_date FROM live_state WHERE code = ?",
                (prev_pos.code,),
            ).fetchone()
            conn.close()
            if row and row["entry_time"] and row["entry_time"] > 0:
                entry_time = row["entry_time"]
                entry_date = row["entry_date"] or ""
        except Exception as e:
            logger.debug(f"_create_trade_record: failed to read entry_time: {e}")

        return {
            "trade_id": str(uuid.uuid4())[:8],
            "code": prev_pos.code,
            "action": "SELL",
            "direction": "close_long",
            "entry_price": prev_pos.avg_price,
            "exit_price": exit_price,
            "quantity": qty,
            "entry_time": entry_time,
            "exit_time": now_ts,
            "entry_date": entry_date,
            "exit_date": today,
            "hold_days": 0,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 6),
            "commission": 0,  # Futu 模拟盘自动扣除
            "total_cost": 0,
            "confidence": 0,
            "trigger_signals": [],
            "exit_reason": reason,
            "notes": "futu_sim",
        }

    def _save_trade(self, trade: dict):
        """Save trade to trades table."""
        conn = get_connection()
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO trades
                   (trade_id, param_version, code, action, direction,
                    entry_price, exit_price, quantity, entry_time, exit_time,
                    entry_date, exit_date, hold_days, pnl, pnl_pct,
                    commission, total_cost, confidence, trigger_signals,
                    exit_reason, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade["trade_id"],
                    PARAM_VERSION,
                    trade["code"],
                    trade["action"],
                    trade["direction"],
                    trade["entry_price"],
                    trade["exit_price"],
                    trade["quantity"],
                    trade["entry_time"],
                    trade["exit_time"],
                    trade["entry_date"],
                    trade["exit_date"],
                    trade.get("hold_days", 0),
                    trade["pnl"],
                    trade["pnl_pct"],
                    trade["commission"],
                    trade.get("total_cost", 0),
                    trade["confidence"],
                    json.dumps(trade.get("trigger_signals", [])),
                    trade["exit_reason"],
                    trade.get("notes", ""),
                ),
            )
            if cursor.rowcount:
                record_db_change_best_effort(
                    conn,
                    db_name="trading.db",
                    table="trades",
                    action="create",
                    key=trade["trade_id"],
                    source="futu_position_sync",
                    actor=_futu_actor(),
                    before=None,
                    after={**trade, "param_version": PARAM_VERSION},
                    hash_text_fields=True,
                )
            conn.commit()
        except Exception as e:
            logger.warning(f"Save trade error: {e}")
        finally:
            conn.close()

    def save_daily_pnl(self, date: str | None = None) -> None:
        """从 get_funds() 读取权益 → 写入 daily_pnl 表。"""
        funds = self._adapter.get_funds()
        if funds.total_assets <= 0:
            return

        today = date or datetime.now().strftime("%Y-%m-%d")
        positions = self._adapter.get_positions()
        positions_json = json.dumps(
            {
                code: {
                    "entry_price": fp.avg_price,
                    "quantity": fp.quantity,
                    "market_val": fp.market_val,
                }
                for code, fp in positions.items()
            }
        )

        conn = get_connection()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO daily_pnl
                   (date, param_version, total_equity, cash, invested,
                    daily_return, cumulative_return, drawdown_pct, positions_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    today,
                    PARAM_VERSION,
                    funds.total_assets,
                    funds.cash,
                    funds.market_val,
                    0,
                    0,
                    0,  # 需要与前日对比计算，暂填 0
                    positions_json,
                ),
            )
            record_db_change_best_effort(
                conn,
                db_name="trading.db",
                table="daily_pnl",
                action="upsert",
                key=f"{PARAM_VERSION}:{today}",
                source="futu_position_sync",
                actor=_futu_actor(),
                before=None,
                after={
                    "date": today,
                    "param_version": PARAM_VERSION,
                    "total_equity": funds.total_assets,
                    "cash": funds.cash,
                    "invested": funds.market_val,
                    "daily_return": 0,
                    "cumulative_return": 0,
                    "drawdown_pct": 0,
                    "positions": json.loads(positions_json),
                },
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Save daily_pnl error: {e}")
        finally:
            conn.close()

    # ── Order audit ────────────────────────────────────────

    def save_order(
        self,
        order_id: str,
        code: str,
        side: str,
        price: float,
        qty: int,
        reason: str = "",
        acc_id: int | None = None,
    ) -> None:
        """记录订单到 futu_orders 表。"""
        now_ts = int(time.time() * 1000)
        conn = get_connection()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO futu_orders
                   (order_id, code, side, price, quantity, status,
                    futu_acc_id, exit_reason, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)""",
                (order_id, code, side, price, qty, acc_id, reason, now_ts, now_ts),
            )
            record_db_change_best_effort(
                conn,
                db_name="trading.db",
                table="futu_orders",
                action="upsert",
                key=order_id,
                source="futu_position_sync",
                actor=_futu_actor(),
                before=None,
                after={
                    "order_id": order_id,
                    "code": code,
                    "side": side,
                    "price": price,
                    "quantity": qty,
                    "status": "PENDING",
                    "futu_acc_id": acc_id,
                    "exit_reason": reason,
                    "created_at": now_ts,
                    "updated_at": now_ts,
                },
                hash_text_fields=True,
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Save order error: {e}")
        finally:
            conn.close()

    def update_order_status(self, updates: list[OrderUpdate]) -> None:
        """批量更新 futu_orders 状态。"""
        if not updates:
            return

        now_ts = int(time.time() * 1000)
        conn = get_connection()
        try:
            for u in updates:
                before_row = conn.execute(
                    "SELECT * FROM futu_orders WHERE order_id = ?",
                    (u.order_id,),
                ).fetchone()
                conn.execute(
                    """UPDATE futu_orders
                       SET status=?, filled_qty=?, avg_fill_price=?, updated_at=?
                       WHERE order_id=?""",
                    (u.status, u.filled_qty, u.avg_fill_price, now_ts, u.order_id),
                )
                after_row = conn.execute(
                    "SELECT * FROM futu_orders WHERE order_id = ?",
                    (u.order_id,),
                ).fetchone()
                if after_row:
                    record_db_change_best_effort(
                        conn,
                        db_name="trading.db",
                        table="futu_orders",
                        action="update",
                        key=u.order_id,
                        source="futu_position_sync",
                        actor=_futu_actor(),
                        before=dict(before_row) if before_row else None,
                        after=dict(after_row),
                        hash_text_fields=True,
                    )
            conn.commit()
        except Exception as e:
            logger.warning(f"Update order status error: {e}")
        finally:
            conn.close()
