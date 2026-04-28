#!/usr/bin/env python3
"""
model_router.py — opencode slim preset 自动切换器

用法：
    python3 scripts/model_router.py status              # 查看各模型可用性状态
    python3 scripts/model_router.py quota               # 查看 5小时/每周剩余额度
    python3 scripts/model_router.py switch <preset>     # 切换 preset (minimax/glm)
    python3 scripts/model_router.py auto                # 自动探测并切换到最优 preset

功能：
- 轻量级 API probe（1 token 请求）检测模型是否可用
- 查询 Kimi/GLM 的 5小时/每周额度，以及 MiniMax 的 5小时额度
- 解析额度耗尽错误码（429/403 + 特定消息）
- 自动修改 ~/.config/opencode/oh-my-opencode-slim.jsonc
"""

import json
import sys
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / ".config/opencode/oh-my-opencode-slim.jsonc"
OPENCODE_CONFIG = Path.home() / ".config/opencode/opencode.json"
GLM_QUOTA_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
KIMI_QUOTA_URL = "https://api.kimi.com/coding/v1/usages"
MINIMAX_QUOTA_URL = "https://api.minimaxi.com/v1/token_plan/remains"
GLM_BALANCED_MIN_REMAINING_PCT = 50
KIMI_SMART_MIN_REMAINING_PCT = 20
PRESET_ALIASES = {
    "minimax": "economy",
    "glm": "balanced",
    "kimi": "smart",
}
PRESET_STATUS_PRIORITY = ["balanced", "economy", "smart"]
FALLBACK_PRESET_PRIORITY = ["economy", "smart"]
PRESET_PROVIDER = {
    "economy": "minimax",
    "balanced": "glm",
    "smart": "kimi",
}
UNSUPPORTED_TOP_LEVEL_KEYS = {"council"}

# Provider 探测配置
# 使用 opencode 实际调用的端点（coding/anthropic 兼容端点受周额度限制）
PROVIDERS = {
    "kimi": {
        "url": "https://api.kimi.com/coding/v1/chat/completions",
        "model": "kimi-k2.6",
        "headers": {
            "User-Agent": "claude-code/0.1",
            "X-Client-Name": "claude-code",
        },
    },
    "glm": {
        # GLM Coding Plan 使用 anthropic 兼容端点，受周额度限制
        "url": "https://open.bigmodel.cn/api/anthropic/v1/messages",
        "model": "glm-5",
        "headers": {
            "anthropic-version": "2023-06-01",
        },
        "anthropic": True,  # 使用 anthropic 协议
    },
    "minimax": {
        "url": "https://api.minimaxi.com/v1/chat/completions",
        "model": "MiniMax-M2.7-highspeed",
        "headers": {},
    },
}


def load_api_keys():
    """从 ~/.config/opencode/opencode.json 读取各 provider API key"""
    if not OPENCODE_CONFIG.exists():
        print(f"❌ 找不到 opencode 配置: {OPENCODE_CONFIG}")
        sys.exit(1)
    try:
        data = json.loads(OPENCODE_CONFIG.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ 读取 opencode 配置失败: {OPENCODE_CONFIG} — {e}")
        sys.exit(1)
    keys = {}
    for provider in ["kimi", "glm", "minimax"]:
        cfg = data.get("provider", {}).get(provider, {})
        keys[provider] = cfg.get("options", {}).get("apiKey", "")
    return keys


def _to_number(value):
    """把 API 返回的数字字符串转成 int/float，无法转换时返回 None。"""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value)
            return int(parsed) if parsed.is_integer() else parsed
        except ValueError:
            return None
    return None


def _request_json(url, api_key, *, bearer=False, headers=None):
    """执行 GET JSON 请求；调用者只拿状态码和 body，避免打印密钥。"""
    auth = f"Bearer {api_key}" if bearer else api_key
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if hasattr(e, "read") else ""
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"error": body[:300]}
        return e.code, parsed
    except Exception as e:
        return "ERR", {"error": str(e)}


def _quota_row(label, *, used=None, remaining=None, total=None, reset=None, note=None, unit=None):
    return {
        "label": label,
        "used": _to_number(used),
        "remaining": _to_number(remaining),
        "total": _to_number(total),
        "reset": reset,
        "note": note,
        "unit": unit,
    }


def _remaining_from_percentage(percentage):
    used = _to_number(percentage)
    return None if used is None else max(0, 100 - used)


def _fmt_reset(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        except ValueError:
            return value
    return str(value)


def _fmt_duration_ms(value):
    ms = _to_number(value)
    if ms is None:
        return None
    seconds = max(0, int(ms / 1000))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def parse_glm_quota(data):
    """解析 GLM quota API，返回统一 quota 行。"""
    if not isinstance(data, dict) or not data.get("success"):
        return []

    limits = data.get("data", {}).get("limits", [])
    rows = []
    for item in limits:
        if item.get("type") != "TOKENS_LIMIT":
            continue
        unit = item.get("unit")
        number = item.get("number")
        if unit == 3 and number == 5:
            label = "GLM 5小时额度"
        elif unit == 6 and number == 1:
            label = "GLM 每周额度"
        else:
            label = f"GLM TOKENS_LIMIT unit={unit} number={number}"
        used = _to_number(item.get("percentage"))
        rows.append(_quota_row(
            label,
            used=used,
            remaining=_remaining_from_percentage(used),
            reset=_fmt_reset(item.get("nextResetTime")),
            unit="%",
        ))

    rows.sort(key=lambda row: 0 if "5小时" in row["label"] else 1)

    for item in limits:
        if item.get("type") == "TIME_LIMIT":
            rows.append(_quota_row(
                "GLM MCP/TIME_LIMIT",
                used=item.get("currentValue"),
                remaining=item.get("remaining"),
                total=item.get("usage"),
                reset=_fmt_reset(item.get("nextResetTime")),
            ))
    return rows


def _parse_quota_detail(label, detail, *, unit=None):
    total = _to_number(detail.get("limit"))
    used = _to_number(detail.get("used"))
    remaining = _to_number(detail.get("remaining"))
    if used is None and total is not None and remaining is not None:
        used = total - remaining
    return _quota_row(
        label,
        used=used,
        remaining=remaining,
        total=total,
        reset=_fmt_reset(detail.get("resetTime") or detail.get("reset_time") or detail.get("resetAt") or detail.get("reset_at")),
        unit=unit,
    )


def parse_kimi_quota(data):
    """解析 Kimi Code usages API，返回 weekly + 5小时窗口。"""
    if not isinstance(data, dict):
        return []

    rows = []
    usage = data.get("usage")
    if isinstance(usage, dict):
        rows.append(_parse_quota_detail("Kimi 每周额度", usage, unit="%"))

    usages = data.get("usages")
    if isinstance(usages, list):
        for usage_item in usages:
            if not isinstance(usage_item, dict):
                continue
            detail = usage_item.get("detail")
            if isinstance(detail, dict):
                label = "Kimi 每周额度" if usage_item.get("scope") == "FEATURE_CODING" else "Kimi 总额度"
                rows.append(_parse_quota_detail(label, detail, unit="%"))
            for limit in usage_item.get("limits") or []:
                if isinstance(limit, dict):
                    rows.extend(_parse_kimi_limits([limit]))

    rows.extend(_parse_kimi_limits(data.get("limits")))
    return rows


def _parse_kimi_limits(limits):
    rows = []
    if not isinstance(limits, list):
        return rows
    for item in limits:
        if not isinstance(item, dict):
            continue
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else item
        window = item.get("window") if isinstance(item.get("window"), dict) else {}
        label = "Kimi 5小时额度" if window.get("duration") == 300 else "Kimi 窗口额度"
        rows.append(_parse_quota_detail(label, detail, unit="%"))
    return rows


def parse_minimax_quota(data):
    """解析 MiniMax Token Plan remains；usage_count 字段实际是剩余次数。"""
    if not isinstance(data, dict):
        return []
    base = data.get("base_resp", {})
    status_code = _to_number(base.get("status_code")) if isinstance(base, dict) else None
    if isinstance(base, dict) and status_code not in (None, 0):
        return []

    rows = []
    remains = data.get("model_remains")
    if not isinstance(remains, list):
        return rows

    for item in remains:
        if not isinstance(item, dict):
            continue
        model = item.get("model_name") or item.get("model") or "MiniMax"
        interval_total = _to_number(item.get("current_interval_total_count"))
        interval_remaining = _to_number(item.get("current_interval_usage_count"))
        reset_note = _fmt_duration_ms(item.get("remains_time"))

        if interval_total is not None or interval_remaining is not None:
            used = interval_total - interval_remaining if interval_total is not None and interval_remaining is not None else None
            rows.append(_quota_row(
                f"{model} 5小时额度",
                used=used,
                remaining=interval_remaining,
                total=interval_total,
                note=f"窗口剩余 {reset_note}" if reset_note else None,
            ))

    return rows


def is_minimax_coding_quota_row(label):
    """只显示日常 coding 相关的 MiniMax 文本/VLM/search 额度。"""
    return label.startswith(("MiniMax-M", "coding-plan-vlm", "coding-plan-search"))


def _parse_glm_error(body: str) -> tuple:
    """解析 GLM 错误响应，提取错误码和重置时间"""
    try:
        data = json.loads(body)
        err = data.get("error", {})
        code = err.get("code", "")
        msg = err.get("message", "")
        if code == "1310" or "每周/每月使用上限" in msg:
            # 提取重置时间，如 "您的限额将在 2026-04-28 10:05:58 重置"
            m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', msg)
            reset_at = m.group(1) if m else "unknown"
            return "quota_exhausted", f"周/月额度耗尽 [code=1310] — 重置时间: {reset_at}"
        if code == "1304" or "今日调用次数限额" in msg:
            return "quota_exhausted", f"日额度耗尽 [code=1304] — {msg[:100]}"
        if code == "1308" or "使用上限" in msg:
            return "quota_exhausted", f"额度耗尽 [code=1308] — {msg[:100]}"
        return None, None
    except Exception:
        return None, None


def probe_provider(name, config, api_key):
    """发送极简请求探测 provider 可用性"""
    if config.get("anthropic"):
        payload = json.dumps({
            "model": config["model"],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }).encode("utf-8")
    else:
        payload = json.dumps({
            "model": config["model"],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **config["headers"],
    }

    req = urllib.request.Request(
        config["url"],
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            if "quota" in body.lower() or "limit" in body.lower() or "余额" in body:
                return "warning", f"响应包含额度警告: {body[:200]}"
            return "ok", f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if hasattr(e, "read") else ""
        status = e.code

        # GLM 特殊错误解析
        if name == "glm":
            parsed_status, parsed_detail = _parse_glm_error(body)
            if parsed_status:
                return parsed_status, parsed_detail

        # 通用额度耗尽检测
        if status == 429:
            if any(k in body for k in ["quota", "limit", "余额", "上限", "exhausted", "已用完", "使用上限", "额度"]):
                return "quota_exhausted", f"HTTP 429 — 额度耗尽: {body[:300]}"
            return "rate_limited", f"HTTP 429 — 频率限制: {body[:300]}"

        if status == 403:
            if any(k in body for k in ["access_terminated", "terminated", "欠费", "到期", "expired"]):
                return "quota_exhausted", f"HTTP 403 — 访问终止/欠费: {body[:300]}"
            return "forbidden", f"HTTP 403 — 禁止访问: {body[:300]}"

        if status == 401:
            return "auth_failed", f"HTTP 401 — 鉴权失败 (key 可能失效): {body[:300]}"

        return "error", f"HTTP {status}: {body[:300]}"

    except Exception as e:
        return "error", f"请求异常: {e}"


def read_current_preset():
    """读取当前 preset"""
    if not CONFIG_PATH.exists():
        return None
    text = CONFIG_PATH.read_text()
    m = re.search(r'"preset"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _read_json_string(text, start):
    """读取 JSON/JSONC 字符串 token，返回 (content, end_index)。"""
    if start >= len(text) or text[start] != '"':
        return None, start
    chars = []
    i = start + 1
    escaped = False
    while i < len(text):
        ch = text[i]
        if escaped:
            chars.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            return "".join(chars), i + 1
        else:
            chars.append(ch)
        i += 1
    return None, start


def _skip_ws(text, index):
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _value_end(text, start):
    """找到 JSON/JSONC value 的结束位置。覆盖对象、数组、字符串和简单值。"""
    start = _skip_ws(text, start)
    if start >= len(text):
        return start
    if text[start] == '"':
        _content, end = _read_json_string(text, start)
        return end
    if text[start] not in "{[":
        i = start
        while i < len(text) and text[i] not in ",}\n":
            i += 1
        return i

    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def strip_top_level_key(text, key):
    """从 JSONC 文本中移除一个顶层 key，保留注释和其余格式。"""
    object_depth = 0
    array_depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            content, end = _read_json_string(text, i)
            if object_depth == 1 and array_depth == 0:
                colon = _skip_ws(text, end)
                if content == key and colon < len(text) and text[colon] == ":":
                    value_end = _value_end(text, colon + 1)
                    remove_start = i
                    remove_end = _skip_ws(text, value_end)
                    if remove_end < len(text) and text[remove_end] == ",":
                        remove_end += 1
                    else:
                        prev = i - 1
                        while prev >= 0 and text[prev].isspace():
                            prev -= 1
                        if prev >= 0 and text[prev] == ",":
                            remove_start = prev
                    return text[:remove_start].rstrip() + text[remove_end:]
            i = end
            continue
        if ch == "{":
            object_depth += 1
        elif ch == "}":
            object_depth -= 1
        elif ch == "[":
            array_depth += 1
        elif ch == "]":
            array_depth -= 1
        i += 1
    return text


def sanitize_config_text(text):
    """移除当前 opencode agent 列表不支持的顶层配置块。"""
    for key in UNSUPPORTED_TOP_LEVEL_KEYS:
        text = strip_top_level_key(text, key)
    return text


def resolve_preset_name(name):
    """支持 provider 名作为 preset 别名，兼容旧用法。"""
    return PRESET_ALIASES.get(name, name)


def switch_preset(preset_name):
    """修改 oh-my-opencode-slim.jsonc 的 preset 字段"""
    preset_name = resolve_preset_name(preset_name)
    if not CONFIG_PATH.exists():
        print(f"❌ 找不到配置文件: {CONFIG_PATH}")
        sys.exit(1)

    text = CONFIG_PATH.read_text()
    old_preset = read_current_preset()

    # 简单替换 preset 字段（保留 jsonc 注释）
    preset_text = re.sub(r'("preset"\s*:\s*)"[^"]+"', rf'\1"{preset_name}"', text)
    new_text = sanitize_config_text(preset_text)
    preset_changed = preset_text != text
    sanitized = new_text != preset_text

    if new_text == text and old_preset == preset_name:
        print(f"ℹ️  当前已经是 '{preset_name}' preset，无需切换")
        return

    # 备份
    backup = CONFIG_PATH.with_suffix(".jsonc.bak")
    backup.write_text(text)

    CONFIG_PATH.write_text(new_text)
    if preset_changed:
        print(f"✅ preset 已从 '{old_preset}' 切换到 '{preset_name}'")
    if sanitized:
        print("✅ 配置已清理：移除当前环境不支持的顶层 council 配置")
    print(f"📝 备份保存于: {backup}")
    print("⚠️  请重启 opencode 或重新加载配置使其生效")


GLM_GENERIC_CONFIG = {
    "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "model": "glm-5",
    "headers": {},
}


def probe_glm(api_key):
    """GLM 双端点探测：coding(anthropic) + 通用(paas/v4)"""
    coding_status, coding_detail = probe_provider("glm", PROVIDERS["glm"], api_key)
    generic_status, generic_detail = probe_provider("glm-generic", GLM_GENERIC_CONFIG, api_key)

    if coding_status == "ok":
        return "ok", f"coding + 通用端点均可用 | {coding_detail}"

    # coding 端点不可用，检查通用端点
    if generic_status == "ok":
        if coding_status == "quota_exhausted":
            # 提取重置时间
            reset = ""
            m = re.search(r'重置时间:\s*(\S+)', coding_detail)
            if m:
                reset = f" (重置: {m.group(1)})"
            return "warning", f"coding 额度耗尽{reset}，但通用端点可用 | fallback 到通用端点"
        return "warning", f"coding 异常 [{coding_status}]，但通用端点可用"

    # 两端点均不可用
    return coding_status, f"coding: {coding_detail} | 通用: {generic_detail}"


def cmd_status():
    """显示各 provider 状态"""
    keys = load_api_keys()
    current = read_current_preset()

    print(f"📋 当前 preset: {current or 'unknown'}")
    print("-" * 60)

    results = {}
    for name, cfg in PROVIDERS.items():
        key = keys.get(name, "")
        if not key:
            status, detail = "no_key", "未配置 API key"
        elif name == "glm":
            status, detail = probe_glm(key)
        else:
            status, detail = probe_provider(name, cfg, key)
        results[name] = (status, detail)

        icon = {
            "ok": "🟢",
            "warning": "🟡",
            "quota_exhausted": "🔴",
            "rate_limited": "🟠",
            "forbidden": "🔴",
            "auth_failed": "🔴",
            "error": "⚫",
            "no_key": "⚪",
        }.get(status, "❓")

        print(f"{icon} {name:10s}  [{status}]  {detail}")

    # 推荐 preset: minimax 优先（便宜量大），glm 次之，kimi 留给难任务
    # 注意：glm 的 warning 状态（coding 耗尽但通用可用）也算可用。
    # 新 preset: balanced=GLM 主力, economy=MiniMax 主力, smart=Kimi oracle。
    rec = next((
        preset for preset in PRESET_STATUS_PRIORITY
        if results.get(PRESET_PROVIDER[preset], ("", ""))[0] in ("ok", "warning")
    ), None)

    print("-" * 60)
    if rec:
        print(f"💡 推荐 preset: {rec}")
        if rec != current:
            print(f"   执行: python3 scripts/model_router.py switch {rec}")
    else:
        print("🔴 所有 provider 均不可用，请检查 API key 和网络")


def _print_quota_rows(provider, status, rows, error=None):
    print(f"\n{provider}")
    if status != 200:
        print(f"  查询失败: HTTP {status} — {str(error)[:200]}")
        return
    if not rows:
        print("  未拿到可解析的额度信息")
        return
    for row in rows:
        print(f"  {format_quota_row(row)}")


def format_quota_row(row):
    """格式化单条 quota 行；百分比额度不显示 /100。"""
    parts = []
    if row.get("unit") == "%":
        if row.get("remaining") is not None:
            parts.append(f"剩余 {row['remaining']}%")
        if row.get("used") is not None:
            parts.append(f"已用 {row['used']}%")
    elif row.get("remaining") is not None and row.get("total") is not None:
        parts.append(f"剩余 {row['remaining']}/{row['total']}")
        if row.get("used") is not None:
            parts.append(f"已用 {row['used']}")
    else:
        if row.get("remaining") is not None:
            parts.append(f"剩余 {row['remaining']}")
        if row.get("used") is not None:
            parts.append(f"已用 {row['used']}")
    if row.get("reset"):
        parts.append(f"重置 {row['reset']}")
    if row.get("note"):
        parts.append(row["note"])
    return f"{row['label']}: " + "，".join(parts)


def fetch_quota(provider, api_key):
    """查询单个 provider quota，返回 (status, rows, error)。"""
    if not api_key:
        return "no_key", [], "未配置 API key"

    if provider == "kimi":
        status, data = _request_json(
            KIMI_QUOTA_URL,
            api_key,
            bearer=True,
            headers={
                "User-Agent": "claude-code/0.1",
                "X-Client-Name": "claude-code",
            },
        )
        return status, parse_kimi_quota(data), data
    if provider == "glm":
        status, data = _request_json(GLM_QUOTA_URL, api_key)
        return status, parse_glm_quota(data), data
    if provider == "minimax":
        status, data = _request_json(MINIMAX_QUOTA_URL, api_key, bearer=True)
        rows = parse_minimax_quota(data)
        # 日常 coding 只需要文本模型和两个 coding-plan 工具额度。
        rows = [row for row in rows if is_minimax_coding_quota_row(row["label"])]
        return status, rows, data
    return "error", [], f"unknown provider: {provider}"


def fetch_all_quota(keys):
    """查询全部 provider quota。"""
    return {
        provider: fetch_quota(provider, keys.get(provider, ""))
        for provider in ["kimi", "glm", "minimax"]
    }


def _has_percent_quota(rows, label_prefix, min_remaining):
    matches = [
        row for row in rows
        if row.get("label", "").startswith(label_prefix) and row.get("unit") == "%"
    ]
    return bool(matches) and all((row.get("remaining") or 0) >= min_remaining for row in matches)


def _has_count_quota(rows, label_prefix):
    matches = [
        row for row in rows
        if row.get("label", "").startswith(label_prefix) and row.get("total") is not None
    ]
    return any((row.get("remaining") or 0) > 0 for row in matches)


def recommend_preset_from_quota(rows_by_provider):
    """根据剩余额度推荐 preset：balanced 优先，GLM 不足时降级 economy。"""
    glm_rows = rows_by_provider.get("glm", [])
    minimax_rows = rows_by_provider.get("minimax", [])
    kimi_rows = rows_by_provider.get("kimi", [])

    if (
        _has_percent_quota(glm_rows, "GLM 5小时额度", GLM_BALANCED_MIN_REMAINING_PCT)
        and _has_percent_quota(glm_rows, "GLM 每周额度", GLM_BALANCED_MIN_REMAINING_PCT)
    ):
        return "balanced"
    if _has_count_quota(minimax_rows, "MiniMax-M"):
        return "economy"
    if (
        _has_percent_quota(kimi_rows, "Kimi 5小时额度", KIMI_SMART_MIN_REMAINING_PCT)
        and _has_percent_quota(kimi_rows, "Kimi 每周额度", KIMI_SMART_MIN_REMAINING_PCT)
    ):
        return "smart"
    return None


def cmd_quota():
    """查询 Kimi/GLM 5小时/每周额度，以及 MiniMax 5小时额度。"""
    keys = load_api_keys()
    print("📊 模型剩余额度")

    quota = fetch_all_quota(keys)
    _print_quota_rows("Kimi Code", *quota["kimi"])
    _print_quota_rows("GLM Coding Plan", *quota["glm"])
    _print_quota_rows("MiniMax Token Plan", *quota["minimax"])

    rows_by_provider = {provider: rows for provider, (_status, rows, _error) in quota.items()}
    rec = recommend_preset_from_quota(rows_by_provider)
    if rec:
        print(f"\n💡 推荐 preset: {rec}")


def cmd_auto():
    """查询额度后自动选择 preset 并切换。"""
    keys = load_api_keys()
    quota = fetch_all_quota(keys)
    rows_by_provider = {provider: rows for provider, (_status, rows, _error) in quota.items()}
    chosen = recommend_preset_from_quota(rows_by_provider)

    if chosen:
        print(f"🤖 根据额度自动选择 preset: {chosen}")
        switch_preset(chosen)
        return

    print("⚠️  未拿到足够 quota 信息，fallback 到可用性探测")
    results = {}
    for name, cfg in PROVIDERS.items():
        key = keys.get(name, "")
        if not key:
            results[name] = ("no_key", "")
            continue
        results[name] = probe_glm(key) if name == "glm" else probe_provider(name, cfg, key)

    chosen = None
    for preset in FALLBACK_PRESET_PRIORITY:
        provider = PRESET_PROVIDER[preset]
        if results.get(provider, ("", ""))[0] in ("ok", "warning"):
            chosen = preset
            break

    if chosen:
        print(f"🤖 根据可用性自动选择 preset: {chosen}")
        switch_preset(chosen)
    else:
        print("🔴 无可用 provider，无法自动切换")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        cmd_status()
        return

    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "quota":
        cmd_quota()
    elif cmd == "switch" and len(sys.argv) >= 3:
        switch_preset(sys.argv[2])
    elif cmd == "auto":
        cmd_auto()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
