"""A dependency-free HTTP client for the Core API.

Used by the capture entry, the validation runner and the tests. `urllib` is
standard library, so the capture hook has nothing to install on a machine that
only ever needs to post one event.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8787"
TIMEOUT_SECONDS = 5.0


class ApiError(Exception):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        code = ""
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            code = str(payload["error"].get("code", ""))
        super().__init__(f"HTTP {status} {code}".strip())

    @property
    def code(self) -> str:
        if isinstance(self.payload, dict) and isinstance(self.payload.get("error"), dict):
            return str(self.payload["error"].get("code", ""))
        return ""


class CoreClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: str | None = None,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def set_token(self, token: str) -> None:
        self._token = token

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw[:500]}
            raise ApiError(exc.code, payload) from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body)
