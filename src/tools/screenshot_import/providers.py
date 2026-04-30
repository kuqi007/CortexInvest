from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import ValidationError

from src.tools.screenshot_import.models import VisionResponse


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


def _parse_vision_payload(raw: str | dict[str, Any], provider: str, model: str) -> VisionResult:
    try:
        if isinstance(raw, str):
            data = json.loads(raw)
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
            ),
        )
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

        rf = _response_format_for_schema(request.json_schema)
        try:
            resp = self._client.with_options(timeout=request.timeout_s).chat.completions.create(
                model=self._model,
                messages=messages,
                response_format=rf,
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
        if os.environ.get("KIMI_API_KEY"):
            return "kimi"
        if os.environ.get("GLM_API_KEY"):
            return "glm"
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
        return OpenAICompatibleVisionProvider("kimi", key, base, model)

    if p == "glm":
        key = os.environ.get("GLM_API_KEY", "")
        if not key:
            raise ValueError("GLM_API_KEY is required for glm provider")
        base = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        model = os.environ.get("GLM_VISION_MODEL") or os.environ.get(
            "GLM_MODEL", "glm-4v-flash"
        )
        return OpenAICompatibleVisionProvider("glm", key, base, model)

    if p == "minimax":
        key = os.environ.get("MINIMAX_API_KEY", "")
        if not key:
            raise ValueError("MINIMAX_API_KEY is required for minimax provider")
        base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
        model = os.environ.get("MINIMAX_VISION_MODEL") or os.environ.get(
            "MINIMAX_MODEL", "MiniMax-Text-01"
        )
        return OpenAICompatibleVisionProvider("minimax", key, base, model)

    raise ValueError(f"unsupported vision provider: {p!r}")
