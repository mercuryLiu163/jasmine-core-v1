"""Transport-independent request and response values.

The HTTP server is a thin adapter over these: a route resolves to a handler that
takes a `Request` and returns a `Response`, with no knowledge of sockets. That
keeps the contract in ADR 0003 testable without a socket and lets a different
server be substituted later without touching the contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .. import errors

MAX_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    request_id: str = ""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def param(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        if not values:
            return default
        return values[0]

    def json_body(self) -> dict[str, Any]:
        if not self.body:
            return {}
        if len(self.body) > MAX_BODY_BYTES:
            raise errors.PayloadTooLarge(f"request body exceeds {MAX_BODY_BYTES} bytes")
        content_type = (self.header("content-type") or "").split(";")[0].strip().lower()
        if content_type and content_type != "application/json":
            raise errors.UnsupportedMediaType(
                f"expected application/json, got {content_type}", content_type=content_type
            )
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise errors.InvalidRequest(f"body is not valid UTF-8: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise errors.InvalidRequest(f"body is not valid JSON: {exc.msg} at position {exc.pos}") from exc
        if not isinstance(parsed, dict):
            raise errors.InvalidRequest("body must be a JSON object")
        return parsed


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any] | list[Any]
    headers: dict[str, str] = field(default_factory=dict)

    def json_bytes(self) -> bytes:
        # ensure_ascii=False: original text is stored and returned as written,
        # not escaped into \uXXXX sequences the caller did not send.
        return json.dumps(self.body, ensure_ascii=False, allow_nan=False).encode("utf-8")


def split_target(target: str) -> tuple[str, dict[str, list[str]]]:
    parts = urlsplit(target)
    return parts.path, parse_qs(parts.query, keep_blank_values=True)
