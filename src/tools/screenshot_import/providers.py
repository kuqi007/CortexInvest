from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import ValidationError

from src.tools.screenshot_import.models import VisionResponse

TRUSTED_PROVIDER_HOSTS = {
    "kimi": frozenset({"api.moonshot.cn"}),
    "glm": frozenset({"open.bigmodel.cn"}),
    "minimax": frozenset({"api.minimax.chat"}),
}


@dataclass(frozen=True)
class VisionRequest:
    image_path: str
    mime_type: str
    prompt: str
    json_schema: dict[str, Any]
    timeout_s: float = 120.0
    run_id: str = ""


@dataclass(frozen=True)
class ProviderError:
    kind: str
    message: str
    retryable: bool
    raw_redacted: str = ""
    raw_response: str = ""


@dataclass
class VisionResult:
    ok: bool
    provider: str
    model: str
    response: VisionResponse | None = None
    error: ProviderError | None = None


@runtime_checkable
class VisionProvider(Protocol):
    """OpenAI-compatible vision completion."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete(self, request: VisionRequest) -> VisionResult: ...


def _extract_json_text(raw: str) -> str:
    text = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fence:
        return fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _raw_excerpt(raw: str, limit: int = 4000) -> str:
    text = raw.strip()
    return text[:limit]


def _is_header_safe_secret(value: str) -> bool:
    return bool(value) and not any(ch in value for ch in "\r\n")


def _validate_provider_config(provider: str, key: str, base_url: str) -> tuple[str, str]:
    if not _is_header_safe_secret(key):
        raise ValueError(f"unsafe {provider} API key")
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    trusted_hosts = TRUSTED_PROVIDER_HOSTS.get(provider, frozenset())
    if parsed.scheme != "https" or parsed.username or parsed.password or host not in trusted_hosts:
        raise ValueError(f"untrusted {provider} base URL: {base_url!r}")
    return key, base_url


def _repair_json_text(text: str) -> str:
    """Repair common LLM JSON glitches without inventing semantic content."""
    repaired = text.strip()
    # Missing comma between array/object entries, e.g. "} {".
    repaired = re.sub(r"([}\]])\s+([{[])", r"\1,\2", repaired)
    # Missing comma between object properties across lines.
    repaired = re.sub(
        r'([}\]"0-9]|true|false|null)\s*\n\s*("[-A-Za-z0-9_]+"[ \t]*:)',
        r"\1,\n\2",
        repaired,
    )
    # Trailing commas are common in markdown-ish JSON snippets.
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def _repair_truncated_stocks_array(text: str) -> str:
    stocks_key = text.find('"stocks"')
    if stocks_key < 0:
        return text
    array_start = text.find("[", stocks_key)
    if array_start < 0:
        return text
    in_string = False
    escaped = False
    object_depth = 0
    last_complete_object_end: int | None = None
    for idx in range(array_start + 1, len(text)):
        ch = text[idx]
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_string:
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            object_depth += 1
        elif ch == "}" and object_depth > 0:
            object_depth -= 1
            if object_depth == 0:
                last_complete_object_end = idx + 1
    if last_complete_object_end is None:
        return text
    return text[:last_complete_object_end].rstrip().rstrip(",") + "\n      ]\n    }"


def _loads_provider_json(raw: str) -> dict[str, Any]:
    text = _extract_json_text(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_json_text(text)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            data = json.loads(_repair_truncated_stocks_array(repaired))
    if not isinstance(data, dict):
        msg = "provider JSON root must be an object"
        raise json.JSONDecodeError(msg, text, 0)
    return data


def _repair_provider_payload(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("schema_version", 1)
    data.setdefault("confidence", 0.85)
    screenshot_type = data.get("screenshot_type")
    default_is_holding: bool | None = None
    if screenshot_type == "holding":
        default_is_holding = True
    elif screenshot_type == "watchlist":
        default_is_holding = False
    stocks = data.get("stocks")
    if default_is_holding is not None and isinstance(stocks, list):
        flattened_stocks: list[Any] = []
        for item in stocks:
            if isinstance(item, list):
                flattened_stocks.extend(item)
            else:
                flattened_stocks.append(item)
        repaired_stocks: list[Any] = []
        top_warnings = data.get("warnings")
        if not isinstance(top_warnings, list):
            top_warnings = []
        for item in flattened_stocks:
            if not isinstance(item, dict):
                continue
            item.pop("pnl", None)
            item.pop("pnl_pct", None)
            item.pop("confidence", None)
            row_warning = item.pop("warning", None)
            if isinstance(row_warning, str) and row_warning.strip():
                top_warnings.append(row_warning.strip())
            row_warnings = item.pop("warnings", None)
            if isinstance(row_warnings, list):
                top_warnings.extend(str(w) for w in row_warnings[:3])
            elif isinstance(row_warnings, str) and row_warnings.strip():
                top_warnings.append(row_warnings.strip())
            if "is_holding" not in item:
                item["is_holding"] = default_is_holding
            fc = item.get("field_confidence")
            if isinstance(fc, int | float):
                value = float(fc)
                item["field_confidence"] = {
                    "code": value,
                    "name": value,
                    "cost": value if item.get("is_holding") is True else None,
                    "shares": value if item.get("is_holding") is True else None,
                }
            elif not isinstance(fc, dict):
                value = 0.7
                item["field_confidence"] = {
                    "code": value if item.get("code") is not None else None,
                    "name": value if item.get("name") else None,
                    "cost": value if item.get("is_holding") is True else None,
                    "shares": value if item.get("is_holding") is True else None,
                }
            for field_name in (
                "cost",
                "shares",
                "current_price",
                "market_value",
                "available_shares",
                "daily_pnl",
                "daily_pnl_pct",
            ):
                if field_name in item:
                    item[field_name] = _coerce_optional_number(item[field_name])
            _maybe_swap_cost_and_current_price(item, data.get("platform"))
            _lower_confidence_for_suspicious_cost(item)
            _ignore_provider_code_for_eastmoney(item, data.get("platform"))
            if item.get("is_holding") is True and (
                item.get("cost") is None or item.get("shares") is None
            ):
                name = item.get("name") or item.get("code") or "unknown"
                top_warnings.append(f"skipped invalid holding row: {name}")
                continue
            if item.get("is_holding") is True and (
                _is_non_positive_number(item.get("cost"))
                or _is_non_positive_number(item.get("shares"))
            ):
                name = item.get("name") or item.get("code") or "unknown"
                top_warnings.append(f"skipped non-positive holding row: {name}")
                continue
            repaired_stocks.append(item)
        data["stocks"] = repaired_stocks
        data["warnings"] = [str(w) for w in top_warnings[:20]]
    return data


def _is_non_positive_number(value: Any) -> bool:
    return isinstance(value, int | float) and value <= 0


def _coerce_optional_number(value: Any) -> Any:
    if value is None or isinstance(value, int | float):
        return value
    if not isinstance(value, str):
        return value
    text = (
        value.strip()
        .replace(",", "")
        .replace("，", "")
        .replace("HKD", "")
        .replace("人民币", "")
        .replace("%", "")
        .strip()
    )
    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10000.0
        text = text[:-1].strip()
    if not text:
        return value
    try:
        return float(text) * multiplier
    except ValueError:
        return value


def _maybe_swap_cost_and_current_price(item: dict[str, Any], platform: str | None) -> None:
    if platform != "ths":
        return
    if item.get("is_holding") is not True:
        return
    cost = item.get("cost")
    current_price = item.get("current_price")
    market_value = item.get("market_value")
    shares = item.get("shares")
    if not all(isinstance(v, int | float) for v in (cost, current_price, market_value, shares)):
        return
    if shares <= 0:
        return
    implied_price = market_value / shares
    cost_delta = abs(float(cost) - implied_price)
    price_delta = abs(float(current_price) - implied_price)
    if cost_delta < price_delta and price_delta > 0.01:
        item["cost"], item["current_price"] = current_price, cost


def _lower_confidence_for_suspicious_cost(item: dict[str, Any]) -> None:
    if item.get("is_holding") is not True:
        return
    cost = item.get("cost")
    shares = item.get("shares")
    fc = item.get("field_confidence")
    if not isinstance(cost, int | float) or not isinstance(shares, int | float):
        return
    if cost <= 5000 or shares <= 1 or not isinstance(fc, dict):
        return
    current_cost_confidence = fc.get("cost")
    if isinstance(current_cost_confidence, int | float):
        fc["cost"] = min(float(current_cost_confidence), 0.5)
    else:
        fc["cost"] = 0.5


def _ignore_provider_code_for_eastmoney(item: dict[str, Any], platform: str | None) -> None:
    if platform != "eastmoney" or not str(item.get("name") or "").strip():
        return
    item["code"] = None
    fc = item.get("field_confidence")
    if isinstance(fc, dict):
        fc["code"] = None


def _parse_vision_payload(raw: str | dict[str, Any], provider: str, model: str) -> VisionResult:
    raw_response = raw if isinstance(raw, str) else ""
    try:
        if isinstance(raw, str):
            data = _loads_provider_json(raw)
        else:
            data = raw
    except json.JSONDecodeError as e:
        return VisionResult(
            ok=False,
            provider=provider,
            model=model,
            error=ProviderError(
                kind="schema_parse",
                message=str(e),
                retryable=False,
                raw_redacted=_raw_excerpt(raw_response),
                raw_response=raw_response,
            ),
        )
    if isinstance(data, dict):
        data = _repair_provider_payload(data)
    try:
        vr = VisionResponse.model_validate(data)
    except ValidationError as e:
        return VisionResult(
            ok=False,
            provider=provider,
            model=model,
            error=ProviderError(
                kind="schema_parse",
                message=str(e),
                retryable=False,
                raw_redacted=_raw_excerpt(raw_response),
                raw_response=raw_response,
            ),
        )
    return VisionResult(ok=True, provider=provider, model=model, response=vr)


class StubVisionProvider:
    """Deterministic provider: returns canned JSON validated as VisionResponse."""

    def __init__(self, name: str, model: str, response: str | dict[str, Any]) -> None:
        self._name = name
        self._model = model
        self._response = response

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: VisionRequest) -> VisionResult:
        _ = request
        return _parse_vision_payload(self._response, self._name, self._model)


def _response_format_for_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "vision_import_response",
            "strict": True,
            "schema": schema,
        },
    }


def _response_format_for_provider(provider: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    if provider == "minimax":
        # Minimax OpenAI-compatible endpoint rejects both JSON Schema and json_object
        # response formats. Rely on prompt-only JSON and validate locally.
        return None
    return _response_format_for_schema(schema)


class OpenAICompatibleVisionProvider:
    """Calls OpenAI-compatible chat completions with base64 data URL and structured JSON."""

    def __init__(self, name: str, api_key: str, base_url: str, model: str) -> None:
        self._name = name
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: VisionRequest) -> VisionResult:
        path = Path(request.image_path)
        try:
            raw = path.read_bytes()
        except OSError as e:
            return VisionResult(
                ok=False,
                provider=self._name,
                model=self._model,
                error=ProviderError(
                    kind="transport",
                    message=f"read image failed: {e}",
                    retryable=False,
                ),
            )

        b64 = base64.standard_b64encode(raw).decode("ascii")
        data_url = f"data:{request.mime_type};base64,{b64}"

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": request.prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

        rf = _response_format_for_provider(self._name, request.json_schema)
        create_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if rf is not None:
            create_kwargs["response_format"] = rf
        if self._name == "kimi":
            create_kwargs["max_tokens"] = 4096
        try:
            resp = self._client.with_options(timeout=request.timeout_s).chat.completions.create(
                **create_kwargs,
            )
        except APIConnectionError as e:
            return VisionResult(
                ok=False,
                provider=self._name,
                model=self._model,
                error=ProviderError(
                    kind="transport",
                    message=str(e),
                    retryable=True,
                ),
            )
        except APIStatusError as e:
            code = getattr(e, "status_code", None)
            retryable = code in (408, 409, 429, 500, 502, 503, 504)
            return VisionResult(
                ok=False,
                provider=self._name,
                model=self._model,
                error=ProviderError(
                    kind="api",
                    message=str(e),
                    retryable=retryable,
                    raw_redacted="",
                ),
            )
        except Exception as e:
            return VisionResult(
                ok=False,
                provider=self._name,
                model=self._model,
                error=ProviderError(
                    kind="transport",
                    message=str(e),
                    retryable=False,
                ),
            )

        try:
            choice = resp.choices[0]
            msg = choice.message
            content = msg.content
        except (IndexError, AttributeError) as e:
            return VisionResult(
                ok=False,
                provider=self._name,
                model=self._model,
                error=ProviderError(
                    kind="schema_parse",
                    message=f"malformed completion: {e}",
                    retryable=False,
                ),
            )

        if not content:
            return VisionResult(
                ok=False,
                provider=self._name,
                model=self._model,
                error=ProviderError(
                    kind="schema_parse",
                    message="empty model content",
                    retryable=False,
                ),
            )

        return _parse_vision_payload(content, self._name, self._model)


def resolve_provider_name(provider: str) -> str:
    """Resolve ``auto`` to kimi / glm / minimax from env; pass-through for explicit names."""
    p = (provider or "").strip().lower()
    if p == "auto":
        if os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPU_API_KEY"):
            return "glm"
        if os.environ.get("KIMI_API_KEY"):
            return "kimi"
        if os.environ.get("MINIMAX_API_KEY"):
            return "minimax"
        raise ValueError(
            "no API key configured for --provider auto "
            "(set one of KIMI_API_KEY, GLM_API_KEY, MINIMAX_API_KEY)"
        )
    if p in ("kimi", "glm", "minimax"):
        return p
    raise ValueError(f"unsupported vision provider: {provider!r}")


def create_provider(provider: str) -> OpenAICompatibleVisionProvider:
    p = resolve_provider_name(provider)
    if p == "kimi":
        key = os.environ.get("KIMI_API_KEY", "")
        if not key:
            raise ValueError("KIMI_API_KEY is required for kimi provider")
        base = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
        model = os.environ.get("KIMI_VISION_MODEL") or os.environ.get(
            "KIMI_MODEL", "moonshot-v1-8k-vision-preview"
        )
        key, base = _validate_provider_config("kimi", key, base)
        return OpenAICompatibleVisionProvider("kimi", key, base, model)

    if p == "glm":
        key = os.environ.get("GLM_API_KEY", "") or os.environ.get("ZHIPU_API_KEY", "")
        if not key:
            raise ValueError("GLM_API_KEY or ZHIPU_API_KEY is required for glm provider")
        base = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        model = os.environ.get("GLM_VISION_MODEL") or os.environ.get(
            "GLM_MODEL", "glm-4v-flash"
        )
        key, base = _validate_provider_config("glm", key, base)
        return OpenAICompatibleVisionProvider("glm", key, base, model)

    if p == "minimax":
        key = os.environ.get("MINIMAX_API_KEY", "")
        if not key:
            raise ValueError("MINIMAX_API_KEY is required for minimax provider")
        base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
        model = os.environ.get("MINIMAX_VISION_MODEL") or os.environ.get(
            "MINIMAX_MODEL", "MiniMax-Text-01"
        )
        key, base = _validate_provider_config("minimax", key, base)
        return OpenAICompatibleVisionProvider("minimax", key, base, model)

    raise ValueError(f"unsupported vision provider: {p!r}")
