from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from src.tools.screenshot_import.models import PlannedAction

DEFAULT_API_URL = "http://127.0.0.1:3120/api/config"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})

# POST /api/config expects top-level `code` and nested `data` for add/update.
_EXCLUDED_FROM_DATA = frozenset({"code", "action", "audit_context"})


def _payload_to_api_data(payload: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in payload.items() if k not in _EXCLUDED_FROM_DATA}


class ConfigApiError(RuntimeError):
    """Raised when the config API client cannot complete a request safely."""


@dataclass(frozen=True)
class AppliedRow:
    row_apply_id: str
    code: str
    status_code: int
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class ApplyResult:
    rows: list[AppliedRow]

    @property
    def applied(self) -> list[AppliedRow]:
        return [r for r in self.rows if r.ok]

    @property
    def failed(self) -> list[AppliedRow]:
        return [r for r in self.rows if not r.ok]


def _require_loopback_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    host = parsed.hostname
    if host is None:
        raise ConfigApiError("invalid config API URL")
    if host.lower() not in _LOOPBACK_HOSTS:
        raise ConfigApiError("remote config API URLs are disabled by default")


class ConfigApiClient:
    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        session: requests.Session | None = None,
        timeout_s: float = 10,
    ) -> None:
        _require_loopback_url(base_url)
        self.base_url = base_url
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout_s

    def fetch_watchlist(self) -> dict[str, object]:
        response = self._session.get(self.base_url, timeout=self._timeout)
        if response.status_code >= 400:
            raise ConfigApiError(
                f"GET {self.base_url} failed: HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        raw = payload.get("watchlist")
        return raw if isinstance(raw, dict) else {}

    def apply_actions(
        self, actions: list[PlannedAction], import_run_id: str
    ) -> ApplyResult:
        ordered = sorted(actions, key=lambda a: a.plan_sequence)
        rows_out: list[AppliedRow] = []

        for action in ordered:
            try:
                current = self.fetch_watchlist()
            except ConfigApiError as exc:
                rows_out.append(
                    AppliedRow(
                        row_apply_id=action.row_apply_id,
                        code=action.code,
                        status_code=0,
                        ok=False,
                        message=str(exc),
                    )
                )
                continue

            exists = action.code in current
            if action.action == "add" or not exists:
                api_action = "add"
            else:
                api_action = "update"

            body: dict[str, object] = {
                "action": api_action,
                "code": action.code,
                "data": _payload_to_api_data(action.payload),
                "audit_context": {
                    "source": "screenshot_stock_import",
                    "import_run_id": import_run_id,
                    "row_apply_id": action.row_apply_id,
                },
            }
            post_resp = self._session.post(
                self.base_url,
                json=body,
                timeout=self._timeout,
                headers={},
            )
            try:
                resp_payload = post_resp.json()
            except ValueError:
                resp_payload = {}

            ok = _is_apply_success(post_resp.status_code, resp_payload)
            msg = ""
            if isinstance(resp_payload, dict):
                raw_msg = resp_payload.get("message")
                if isinstance(raw_msg, str):
                    msg = raw_msg
            if not ok and not msg:
                if post_resp.status_code < 200 or post_resp.status_code >= 300:
                    msg = f"HTTP {post_resp.status_code}"
                else:
                    msg = "apply failed"

            rows_out.append(
                AppliedRow(
                    row_apply_id=action.row_apply_id,
                    code=action.code,
                    status_code=post_resp.status_code,
                    ok=ok,
                    message=msg,
                )
            )

        return ApplyResult(rows=rows_out)


def _is_apply_success(status_code: int, payload: object) -> bool:
    if status_code < 200 or status_code >= 300:
        return False
    if isinstance(payload, dict) and payload.get("success") is False:
        return False
    return True
