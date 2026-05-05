"""
股票关联分析引擎 — 自动聚合关联资产数据供 AI 分析使用
"""

import json
import logging
import os
import sqlite3
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ── DB 路径统一（复用 db.py 逻辑，消除硬编码不一致）───────────────────
try:
    from ..sim_trading.db import CONFIG_DB_PATH
except ImportError:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    _symlink_data_dir = _PROJECT_ROOT / "src" / "data"
    _DATA_DIR = _symlink_data_dir if _symlink_data_dir.is_symlink() else _PROJECT_ROOT / "data"
    CONFIG_DB_PATH = _DATA_DIR / "config.db"

# ── 配置 ─────────────────────────────────────────────────────

MACRO_API = "http://localhost:3120/api/macro"
CACHE_TTL = {
    "macro": 300,
    "stock": 60,
    "index": 120,
}

THRESHOLD_PRESETS = {
    "commodity": 3.0,
    "stock": 5.0,
    "index": 1.5,
    "crypto": 8.0,
}


# ── None 保护格式化辅助 ───────────────────────────────────────

def _fmt_pct(val: Optional[float], digits: int = 2) -> str:
    if val is None:
        return "N/A"
    return f"{val:+.{digits}f}%"


def _fmt_price(val: Optional[float], digits: int = 2) -> str:
    if val is None:
        return "N/A"
    return f"{val:.{digits}f}"


# ── 数据模型 ─────────────────────────────────────────────────

@dataclass
class Relationship:
    symbol: str
    related_type: str
    related_code: str
    related_name: str
    data_source: str
    field_path: str
    influence: str
    weight: float
    threshold_pct: Optional[float]
    volatility_preset: str


@dataclass
class RelatedData:
    relationship: Relationship
    current_value: Optional[float]
    change_pct: Optional[float]
    is_triggered: bool
    raw_data: Dict[str, Any]


# ── 缓存（支持连接复用）──────────────────────────────────────

class _Cache:
    """SQLite 缓存表，支持注入外部共享连接以降低 connect/close 次数"""

    def __init__(self, db_path: str, shared_conn: Optional[sqlite3.Connection] = None):
        self.db_path = db_path
        self._shared_conn = shared_conn
        self._local_conn: Optional[sqlite3.Connection] = None
        self._ensure_table()

    def _is_shared(self) -> bool:
        return self._shared_conn is not None

    def _get_conn(self) -> sqlite3.Connection:
        if self._shared_conn is not None:
            return self._shared_conn
        if self._local_conn is None:
            self._local_conn = sqlite3.connect(self.db_path)
        return self._local_conn

    def close(self) -> None:
        if self._local_conn is not None:
            self._local_conn.close()
            self._local_conn = None

    def _ensure_table(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)
        if not self._is_shared():
            conn.commit()

    def get(self, key: str) -> Optional[Dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM _cache WHERE key=? AND expires_at>?",
            (key, int(time.time()))
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def set(self, key: str, value: Dict, ttl: int):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO _cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), int(time.time()) + ttl)
        )
        if not self._is_shared():
            conn.commit()


# ── 数据源适配器 ─────────────────────────────────────────────

class _MacroApiSource:
    def __init__(self, cache: _Cache):
        self.cache = cache

    def fetch(self, fields: List[str]) -> Dict[str, Any]:
        cached = self.cache.get("macro_api")
        if cached:
            raw = cached
        else:
            raw = {}
            try:
                with urllib.request.urlopen(MACRO_API, timeout=3) as r:
                    raw = json.loads(r.read())
                self.cache.set("macro_api", raw, CACHE_TTL["macro"])
            except Exception as e:
                logger.debug(f"Macro API fetch failed: {e}")
        data = raw.get("data", {})
        result: Dict[str, Any] = {"raw": raw}
        if len(fields) >= 1:
            result["current"] = data.get(fields[0])
        if len(fields) >= 2:
            result["change_pct"] = data.get(fields[1])
        return result


class _AkshareSource:
    def __init__(self, cache: _Cache):
        self.cache = cache

    def fetch(self, code: str) -> Dict[str, Any]:
        cache_key = f"akshare:{code}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        try:
            import akshare as ak
            if code.endswith(".SH") or code.endswith(".SZ"):
                df = ak.stock_zh_a_spot_em()
                row = df[df["代码"] == code.replace(".SH", "").replace(".SZ", "")]
                if not row.empty:
                    result = {
                        "current": float(row["最新价"].values[0]),
                        "change_pct": float(row["涨跌幅"].values[0]),
                    }
                    self.cache.set(cache_key, result, CACHE_TTL["stock"])
                    return result
        except Exception as e:
            logger.debug(f"akshare fetch failed for {code}: {e}")

        return {"error": f"akshare fetch failed for {code}"}


class _YFinanceSource:
    def __init__(self, cache: _Cache):
        self.cache = cache

    def fetch(self, code: str) -> Dict[str, Any]:
        cache_key = f"yfinance:{code}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        try:
            import yfinance as yf
            ticker = yf.Ticker(code)
            # period="5d" 保证跨越周末/假日时仍有至少2条有效交易日数据
            hist = ticker.history(period="5d")
            if len(hist) >= 2:
                today = float(hist["Close"].iloc[-1])
                yesterday = float(hist["Close"].iloc[-2])
                change_pct = (today - yesterday) / yesterday * 100
                result = {"current": today, "change_pct": change_pct}
                self.cache.set(cache_key, result, CACHE_TTL["stock"])
                return result
            elif len(hist) == 1:
                today = float(hist["Close"].iloc[-1])
                result = {"current": today, "change_pct": None}
                self.cache.set(cache_key, result, CACHE_TTL["stock"])
                return result
        except Exception as e:
            logger.debug(f"yfinance fetch failed for {code}: {e}")

        return {"error": f"yfinance fetch failed for {code}"}


# ── 引擎核心（支持上下文管理器复用连接）────────────────────────

class RelationshipEngine:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(CONFIG_DB_PATH)
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self.cache: Optional[_Cache] = None
        self._macro: Optional[_MacroApiSource] = None
        self._akshare: Optional[_AkshareSource] = None
        self._yfinance: Optional[_YFinanceSource] = None

    def _ensure_initialized(self):
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self.cache = _Cache(self.db_path, shared_conn=self._conn)
        self._macro = _MacroApiSource(self.cache)
        self._akshare = _AkshareSource(self.cache)
        self._yfinance = _YFinanceSource(self.cache)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None
        if self.cache is not None:
            self.cache.close()
            self.cache = None
        self._macro = None
        self._akshare = None
        self._yfinance = None

    def __enter__(self):
        self._ensure_initialized()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def get_relationships(self, symbol: str) -> List[Relationship]:
        self._ensure_initialized()
        assert self._conn is not None

        # Normalize symbol: HK03858 -> 03858.HK, SH600519 -> 600519.SH, etc.
        normalized = symbol
        for prefix in ["HK", "SH", "SZ", "BJ", "US"]:
            if symbol.startswith(prefix) and "." not in symbol:
                normalized = symbol[len(prefix):] + "." + prefix
                break

        candidates = [normalized]
        # Also try raw symbol and common suffixes for backward compat
        if normalized != symbol:
            candidates.append(symbol)
        for suffix in [".SH", ".SZ", ".BJ", ".HK", ".US"]:
            if not normalized.endswith(suffix) and normalized + suffix not in candidates:
                candidates.append(normalized + suffix)

        rows = []
        for candidate in candidates:
            rows = self._conn.execute(
                """SELECT symbol, related_type, related_code, related_name,
                          data_source, field_path, influence, weight,
                          threshold_pct, volatility_preset
                   FROM stock_relationships
                   WHERE symbol=? AND is_active=1
                   ORDER BY weight DESC""",
                (candidate,),
            ).fetchall()
            if rows:
                break

        return [
            Relationship(
                symbol=r["symbol"],
                related_type=r["related_type"],
                related_code=r["related_code"],
                related_name=r["related_name"] or r["related_code"],
                data_source=r["data_source"],
                field_path=r["field_path"] or "",
                influence=r["influence"] or "positive",
                weight=r["weight"] or 1.0,
                threshold_pct=r["threshold_pct"],
                volatility_preset=r["volatility_preset"] or "commodity",
            )
            for r in rows
        ]

    def fetch_related_data(self, rel: Relationship) -> RelatedData:
        self._ensure_initialized()
        raw: Dict[str, Any] = {}
        current: Optional[float] = None
        change_pct: Optional[float] = None

        try:
            if rel.data_source == "macro_api":
                assert self._macro is not None
                fields = [f.strip() for f in rel.field_path.split(",") if f.strip()]
                raw = self._macro.fetch(fields)
                current = raw.get("current")
                change_pct = raw.get("change_pct")

            elif rel.data_source == "akshare":
                assert self._akshare is not None
                raw = self._akshare.fetch(rel.related_code)
                current = raw.get("current")
                change_pct = raw.get("change_pct")

            elif rel.data_source == "yfinance":
                assert self._yfinance is not None
                raw = self._yfinance.fetch(rel.related_code)
                current = raw.get("current")
                change_pct = raw.get("change_pct")

        except Exception as e:
            raw = {"error": str(e)}

        threshold = rel.threshold_pct
        if threshold is None:
            threshold = THRESHOLD_PRESETS.get(rel.volatility_preset, 2.0)

        triggered = False
        if change_pct is not None and threshold is not None:
            triggered = abs(change_pct) >= threshold

        return RelatedData(
            relationship=rel,
            current_value=current,
            change_pct=change_pct,
            is_triggered=triggered,
            raw_data=raw,
        )

    def analyze_symbol(self, symbol: str) -> Dict[str, Any]:
        relationships = self.get_relationships(symbol)
        if not relationships:
            return {"symbol": symbol, "relationships": [], "triggered_count": 0, "summary": ""}

        related_datas: List[RelatedData] = []
        for rel in relationships:
            rd = self.fetch_related_data(rel)
            related_datas.append(rd)

        triggered = [rd for rd in related_datas if rd.is_triggered]
        summary = self._generate_summary(symbol, related_datas)

        return {
            "symbol": symbol,
            "relationships": [
                {
                    "type": rd.relationship.related_type,
                    "name": rd.relationship.related_name,
                    "code": rd.relationship.related_code,
                    "current": rd.current_value,
                    "change_pct": rd.change_pct,
                    "triggered": rd.is_triggered,
                    "influence": rd.relationship.influence,
                    "weight": rd.relationship.weight,
                    "threshold": THRESHOLD_PRESETS.get(
                        rd.relationship.volatility_preset, 2.0
                    ) if rd.relationship.threshold_pct is None else rd.relationship.threshold_pct,
                    "error": rd.raw_data.get("error"),  # 透传底层错误
                }
                for rd in related_datas
            ],
            "triggered_count": len(triggered),
            "summary": summary,
        }

    def _generate_summary(self, symbol: str, datas: List[RelatedData]) -> str:
        if not datas:
            return ""
        lines = [f"## 关联资产分析 ({symbol})"]
        for rd in datas:
            rel = rd.relationship
            val_str = _fmt_price(rd.current_value)
            chg_str = _fmt_pct(rd.change_pct)
            flag = "🔥" if rd.is_triggered else ""
            direction = "📈" if rel.influence == "positive" else "📉" if rel.influence == "negative" else "➡️"
            lines.append(
                f"- {direction} **{rel.related_name}** ({rel.related_code}): {val_str} ({chg_str}) {flag}"
            )
            if rd.is_triggered:
                thr = rel.threshold_pct or THRESHOLD_PRESETS.get(rel.volatility_preset, 2.0)
                lines.append(f"  ⚠️ 变动超过 {thr}% 阈值，建议重点关注")
        return "\n".join(lines)


# ── CLI 入口 ─────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python relationship_engine.py <symbol>")
        sys.exit(1)
    symbol = sys.argv[1]
    with RelationshipEngine() as engine:
        result = engine.analyze_symbol(symbol)
    print(json.dumps(result, ensure_ascii=False, indent=2))
