"""
Futu L2 数据增强器 — 可选依赖，失败不影响主流程

为 market_data_poller 提供 Tier-1 L2 衍生信号:
  - mainNetInflow    主力净流入（超大单+大单）
  - mainNetInflowPct 主力净流入占成交额比例%
  - retailNetInflow  散户净流入（小单）
  - bidAskRatio      委比
  - avgPrice         当日均价

设计原则:
  - Lazy connect: 首次调用时才连接 OpenD，不阻塞 poller 启动
  - Graceful degradation: 任何 Futu 调用失败 → 返回空 dict，poller 照常运行
  - Auto reconnect: 连接断开后下次调用自动重连
  - 不消耗订阅配额: capital_flow + snapshot 都是 one-shot 查询
"""

import socket
import time
from typing import Optional

from src.utils.futu_codes import to_futu_code, from_futu_code
from src.utils.logging_config import setup_logger

logger = setup_logger("futu_enricher")

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111

# 连接失败后的冷却时间（秒），避免每轮 poll 都尝试连接
RECONNECT_COOLDOWN = 60


# ── Enricher ─────────────────────────────────────────────


class FutuL2Enricher:
    """Futu L2 可选增强器

    用法:
        enricher = FutuL2Enricher()
        # 在 poll_once 中:
        l2_data, hk_index = enricher.enrich(services)
        # l2_data = {"HK00700": {"mainNetInflow": 6.35e8, ...}, ...}
        # hk_index = {"hkIndex": 20000.0, "hkIndexPct": 1.5, "hkTurnover": 120000000000, ...}
        # 如果 Futu 不可用，l2_data = {}, hk_index = {}
    """

    INDEX_CODES = ["HK800000", "HKHSTECH"]

    def __init__(self, host: str = OPEND_HOST, port: int = OPEND_PORT):
        self._host = host
        self._port = port
        self._ctx = None
        self._last_fail_time: float = 0
        self._hk_index_data: dict = {}  # 存储港股指数数据

    def _is_port_open(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((self._host, self._port)) == 0
        sock.close()
        return result

    def _ensure_connected(self) -> bool:
        """懒连接 + 冷却期控制"""
        if self._ctx is not None:
            return True

        # 冷却期内不重试
        if time.time() - self._last_fail_time < RECONNECT_COOLDOWN:
            return False

        if not self._is_port_open():
            self._last_fail_time = time.time()
            logger.debug("OpenD 未运行，跳过 L2 增强")
            return False

        try:
            from futu import OpenQuoteContext
            self._ctx = OpenQuoteContext(host=self._host, port=self._port)
            logger.info("Futu OpenD 连接成功，L2 增强已启用")
            return True
        except Exception as e:
            self._last_fail_time = time.time()
            logger.warning(f"Futu 连接失败: {e}")
            return False

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    def enrich(self, services: list[dict]) -> tuple[dict[str, dict], dict]:
        """为 services 列表中的每只股票获取 L2 衍生信号

        Returns:
            (l2_extra, hk_index_data) — 失败时返回 ({}, {})
            l2_extra: {stock_code: {field: value, ...}, ...}
            hk_index_data: {"hkIndex": float, "hkIndexPct": float, "hkTurnover": float,
                            "hkTech": float, "hkTechPct": float}
        """
        self._hk_index_data = {}  # 重置指数数据
        if not self._ensure_connected():
            return {}, {}

        try:
            return self._do_enrich(services)
        except Exception as e:
            logger.warning(f"L2 增强失败: {e}")
            # 连接可能已断开，清理以便下次重连
            self.close()
            self._last_fail_time = time.time()
            return {}, {}

    def _do_enrich(self, services: list[dict]) -> tuple[dict[str, dict], dict]:
        from futu import RET_OK

        result = {}
        self._hk_index_data = {}

        # 按股票分组获取数据
        codes = [s["id"] for s in services if s.get("id")]
        if not codes:
            return {}, {}

        # ── 1. Snapshot (HK=完整行情+L2, A股=仅L2衍生) ──
        snapshot_data = self._fetch_snapshot_extra(codes)

        # ── 2. Capital flow (mainNetInflow, retailNetInflow) ──
        # 从 l2_strategy_daemon 的 session 产出读取，避免与 daemon 竞争
        # Futu get_capital_flow 的 30次/30s 限频（daemon 每 3s poll 已占满配额）
        capital_data = self._read_capital_from_l2_signals()

        # ── 3. 组装结果 ──
        amount_map = {s["id"]: s.get("amount", 0) for s in services}

        for code in codes:
            entry = {}

            # snapshot 字段 — HK 已含 price/change 等行情字段，直接 merge
            snap = snapshot_data.get(code)
            if snap:
                entry.update(snap)

            # capital flow 字段
            cap = capital_data.get(code)
            if cap:
                entry["mainNetInflow"] = cap.get("mainNetInflow")
                entry["retailNetInflow"] = cap.get("retailNetInflow")

                # mainNetInflowPct = mainNetInflow / 当日成交额 * 100
                # 优先用 snapshot 的 amount（HK），fallback 到 services 的 amount
                amount = entry.get("amount") or amount_map.get(code, 0)
                main_inflow = cap.get("mainNetInflow", 0)
                if amount and main_inflow is not None:
                    entry["mainNetInflowPct"] = round(main_inflow / amount * 100, 2)

            # 只保留有实际数据的条目
            entry = {k: v for k, v in entry.items() if v is not None}
            if entry:
                result[code] = entry

        return result, self._hk_index_data

    def _fetch_snapshot_extra(self, codes: list[str]) -> dict[str, dict]:
        """从 snapshot 提取行情数据

        港股: 完整行情 (price/change/vol/amount/...) + L2 衍生字段，
              直接覆盖新浪/东方财富数据，Futu HK L2 是最可靠数据源。
        A股:  仅 L2 衍生字段 (bidAskRatio, avgPrice, volumeRatio, turnoverRate)。
        指数:  恒生指数(HK.800000)/恒生科技指数(HK.HSTECH)，写入 self._hk_index_data。
        """
        from futu import RET_OK

        futu_codes = [to_futu_code(c) for c in codes]

        # 指数代码单独处理（不走标准 stock 处理逻辑）
        index_futu_codes = [to_futu_code(c) for c in self.INDEX_CODES]
        index_futu_set = set(index_futu_codes)

        # 港股和 A 股分批（A 股可能没权限）
        hk = [c for c in futu_codes if c.startswith("HK.") and c not in index_futu_set]
        a_share = [c for c in futu_codes if not c.startswith("HK.")]

        result = {}

        # ── 港股: 完整行情 + L2 字段 ──
        if hk:
            try:
                ret, data = self._ctx.get_market_snapshot(hk)
                if ret == RET_OK:
                    # get_market_snapshot 可能返回 DataFrame 或 list[dict]
                    rows = data.itertuples() if hasattr(data, "iterrows") else data
                    for row in rows:
                        row_dict = dict(row._asdict()) if hasattr(row, "_asdict") else row
                        code = from_futu_code(str(row_dict.get("code", "")))
                        entry = {}
                        # 核心行情字段 — 覆盖新浪/东方财富
                        price = row_dict.get("last_price")
                        prev = row_dict.get("prev_close_price")
                        if price and price > 0:
                            entry["price"] = round(float(price), 3)
                        if prev and prev > 0:
                            entry["prevClose"] = round(float(prev), 3)
                            if price and price > 0:
                                entry["change"] = round((price - prev) / prev * 100, 2)
                                entry["chgAmt"] = round(float(price - prev), 3)
                        for ft_key, svc_key in [
                            ("open_price", "open"), ("high_price", "high"),
                            ("low_price", "low"), ("volume", "vol"),
                            ("turnover", "amount"), ("amplitude", "amp"),
                        ]:
                            val = row_dict.get(ft_key)
                            if val and val > 0:
                                entry[svc_key] = round(float(val), 3) if isinstance(val, float) else int(val)
                        # L2 衍生字段
                        bid_ask = row_dict.get("bid_ask_ratio")
                        avg = row_dict.get("avg_price")
                        vol_ratio = row_dict.get("volume_ratio")
                        turnover_rate = row_dict.get("turnover_rate")
                        if bid_ask and bid_ask != 0:
                            entry["bidAskRatio"] = round(float(bid_ask), 3)
                        if avg and avg != 0:
                            entry["avgPrice"] = round(float(avg), 3)
                        if vol_ratio and vol_ratio > 0:
                            entry["volRatio"] = round(float(vol_ratio), 2)
                        if turnover_rate and turnover_rate > 0:
                            entry["turnover"] = round(float(turnover_rate), 2)
                        if entry:
                            result[code] = entry
            except Exception as e:
                logger.debug(f"HK snapshot 失败: {e}")

        # ── 港股指数: 恒生指数 + 恒生科技指数 ──
        # get_market_snapshot 不支持指数代码，改用 get_stock_quote
        if index_futu_codes:
            try:
                ret, data = self._ctx.get_stock_quote(index_futu_codes)
                if ret == RET_OK:
                    if isinstance(data, list):
                        for row_dict in data:
                            self._process_index_row(row_dict)
                    else:
                        for row in data.itertuples():
                            self._process_index_row(row._asdict())
                    if self._hk_index_data:
                        logger.info(f"港股指数获取成功: {self._hk_index_data}")
                else:
                    logger.debug(f"HK index quote ret={ret}, data={data}")
            except Exception as e:
                logger.debug(f"HK index quote 失败: {e}")

        # ── A股: 仅 L2 衍生字段 ──
        if a_share:
            try:
                ret, data = self._ctx.get_market_snapshot(a_share)
                if ret == RET_OK:
                    if isinstance(data, list):
                        rows_iter = data
                    else:
                        rows_iter = data.itertuples()
                    for row in rows_iter:
                        row_dict = dict(row._asdict()) if hasattr(row, "_asdict") else row
                        code = from_futu_code(str(row_dict.get("code", "")))
                        entry = {}
                        bid_ask = row_dict.get("bid_ask_ratio")
                        avg = row_dict.get("avg_price")
                        vol_ratio = row_dict.get("volume_ratio")
                        turnover_rate = row_dict.get("turnover_rate")
                        if bid_ask and bid_ask != 0:
                            entry["bidAskRatio"] = round(float(bid_ask), 3)
                        if avg and avg != 0:
                            entry["avgPrice"] = round(float(avg), 3)
                        if vol_ratio and vol_ratio > 0:
                            entry["volRatio"] = round(float(vol_ratio), 2)
                        if turnover_rate and turnover_rate > 0:
                            entry["turnover"] = round(float(turnover_rate), 2)
                        if entry:
                            result[code] = entry
            except Exception as e:
                logger.debug(f"A股 snapshot 失败: {e}")

        return result

    def _process_index_row(self, row: dict):
        """处理港股指数行，更新 self._hk_index_data"""
        from_futu = from_futu_code(str(row.get("code", "")))
        last_price = row.get("last_price") or 0
        change_ratio = row.get("change_ratio") or 0
        turnover_val = row.get("turnover") or 0
        if from_futu == "HK800000":
            self._hk_index_data = {
                "hkIndex": round(float(last_price), 2) if last_price else 0,
                "hkIndexPct": round(float(change_ratio), 2) if change_ratio else 0,
                "hkTurnover": round(float(turnover_val), 2) if turnover_val else 0,
            }
        elif from_futu == "HKHSTECH":
            self._hk_index_data.update({
                "hkTech": round(float(last_price), 2) if last_price else 0,
                "hkTechPct": round(float(change_ratio), 2) if change_ratio else 0,
            })

    def _read_capital_from_l2_signals(self) -> dict[str, dict]:
        """从 l2_strategy_daemon 的 session 产出读取主力资金数据

        l2_strategy_signals.json 的 session[code].capital_flow 包含:
          main_net_inflow, main_net_inflow_pct, direction_score
        daemon 每 3s 更新一次，数据比 poller 自己调 API 更实时。
        """
        import json
        from pathlib import Path

        L2_SIGNALS_PATH = Path(__file__).parent.parent / "data" / "l2_strategy_signals.json"
        result = {}
        try:
            data = json.loads(L2_SIGNALS_PATH.read_text(encoding="utf-8"))
            session = data.get("session", {})
            for code, info in session.items():
                cf = info.get("capital_flow")
                if cf and cf.get("main_net_inflow") is not None:
                    result[code] = {
                        "mainNetInflow": cf["main_net_inflow"],
                        "retailNetInflow": 0,  # session 不拆分散户，用 0 占位
                    }
        except Exception as e:
            logger.debug(f"读取 l2_strategy_signals.json 失败: {e}")
        return result
