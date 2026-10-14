"""Request validation for the Host Hook Protocol (ADR 0017 section 3).

Pure functions and values; no database access. Shared by the Core handler and
the hook runtime (which builds requests and validates them before sending).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import clock, errors, ids
from ..authority import TOOL_CATEGORIES  # noqa: F401 - single definition (ADR 0018)

PROTOCOL_VERSION = "1"
SOURCE_SYSTEM = "host-hook-v1"

EVENTS = ("session.start", "prompt.submit", "tool.before", "tool.after",
          "context.compact", "turn.stop", "session.end")
TRUST_LEVELS = ("NATIVE_HOOK", "PROXY_OBSERVED", "AGENT_REPORTED")
DECISIONS = ("allow", "deny", "ask", "none")

DELIVERY_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_PATHS = 8

TOP_FIELDS = {"protocol_version", "event", "delivery_id", "agent", "adapter", "host_id",
              "binding", "native", "trust", "occurred_at", "payload"}
TOP_REQUIRED = TOP_FIELDS - {"occurred_at"}


def _bad(message: str, field_name: str) -> errors.InvalidRequest:
    return errors.InvalidRequest(message, field=field_name)


def _obj(value: Any, name: str, allowed: set[str], required: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _bad(f"{name} must be an object", name)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise errors.InvalidRequest(f"unknown fields in {name}: {', '.join(unknown)}", field=name, fields=unknown)
    missing = sorted(required - set(value))
    if missing:
        raise errors.InvalidRequest(f"{name} is missing: {', '.join(missing)}", field=name, fields=missing)
    return value


def _text(value: Any, name: str, *, low: int = 1, high: int = 256, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not low <= len(value) <= high:
        raise _bad(f"{name} must be text of {low}..{high} characters", name)
    return value


def _id(value: Any, prefix: str, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not ids.is_id(value, prefix):
        raise _bad(f"{name} must be a {prefix}_ id", name)
    return value


def _abs_path(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value or len(value) > 4096:
        raise _bad(f"{name} must be an absolute path", name)
    return value


@dataclass(frozen=True)
class HostHookRequest:
    event: str
    delivery_id: str
    agent: dict[str, Any]
    adapter: dict[str, Any]
    host_id: str
    project_id: str | None
    task_id: str | None
    step_id: str | None
    native: dict[str, Any]
    trust: str
    occurred_at: datetime | None
    payload: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def parse_request(body: Any) -> HostHookRequest:
    if not isinstance(body, dict):
        raise errors.InvalidRequest("body must be a JSON object")
    version = body.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise errors.HostHookProtocolUnsupported(
            f"protocol_version must be {PROTOCOL_VERSION!r}", supported=[PROTOCOL_VERSION],
            received=version if isinstance(version, str) else None)
    _obj(body, "request", TOP_FIELDS, TOP_REQUIRED)
    event = body["event"]
    if event not in EVENTS:
        raise errors.InvalidRequest(f"event must be one of {', '.join(EVENTS)}", field="event")
    delivery_id = body["delivery_id"]
    if not isinstance(delivery_id, str) or not DELIVERY_ID.match(delivery_id):
        raise _bad("delivery_id must be 1..200 characters of [A-Za-z0-9_.:-]", "delivery_id")
    agent = _obj(body["agent"], "agent", {"name", "version"}, {"name"})
    _text(agent["name"], "agent.name", high=64)
    _text(agent.get("version"), "agent.version", high=64, optional=True)
    adapter = _obj(body["adapter"], "adapter", {"name", "version", "capabilities_sha256"},
                   {"name", "version", "capabilities_sha256"})
    _text(adapter["name"], "adapter.name", high=64)
    _text(adapter["version"], "adapter.version", high=64)
    if not isinstance(adapter["capabilities_sha256"], str) or not SHA256.match(adapter["capabilities_sha256"]):
        raise _bad("adapter.capabilities_sha256 must be lowercase SHA-256 hex", "adapter.capabilities_sha256")
    host_id = _id(body["host_id"], "hst", "host_id")
    binding = _obj(body["binding"], "binding", {"project_id", "task_id", "step_id"})
    project_id = _id(binding.get("project_id"), "prj", "binding.project_id", optional=True)
    task_id = _id(binding.get("task_id"), "tsk", "binding.task_id", optional=True)
    step_id = _id(binding.get("step_id"), "stp", "binding.step_id", optional=True)
    if step_id and not task_id:
        raise _bad("binding.step_id requires binding.task_id", "binding.step_id")
    native = _obj(body["native"], "native", {"session_id", "turn_id", "call_id"}, {"session_id"})
    _text(native["session_id"], "native.session_id")
    _text(native.get("turn_id"), "native.turn_id", optional=True)
    _text(native.get("call_id"), "native.call_id", optional=True)
    trust = body["trust"]
    if trust not in TRUST_LEVELS:
        raise errors.InvalidRequest(f"trust must be one of {', '.join(TRUST_LEVELS)}", field="trust")
    occurred_at = None
    if body.get("occurred_at") is not None:
        try:
            occurred_at = clock.parse_rfc3339(body["occurred_at"])
        except (ValueError, TypeError) as exc:
            raise errors.InvalidRequest(f"occurred_at must be RFC 3339: {exc}", field="occurred_at") from exc
    payload = _validate_payload(event, body["payload"])
    return HostHookRequest(event=event, delivery_id=delivery_id, agent=agent, adapter=adapter,
                           host_id=host_id, project_id=project_id, task_id=task_id, step_id=step_id,
                           native=native, trust=trust, occurred_at=occurred_at, payload=payload, raw=body)


def _tool_fields(payload: dict[str, Any], *, after: bool) -> None:
    category = payload.get("tool")
    if category not in TOOL_CATEGORIES:
        raise errors.InvalidRequest(f"payload.tool must be one of {', '.join(TOOL_CATEGORIES)}", field="payload.tool")
    _text(payload.get("native_tool"), "payload.native_tool", high=256)
    if not isinstance(payload.get("input"), dict):
        raise _bad("payload.input must be an object", "payload.input")
    if after:
        if "response" not in payload or payload["response"] is None:
            raise _bad("payload.response is required", "payload.response")
        code = payload.get("exit_code")
        if code is not None and type(code) is not int:
            raise _bad("payload.exit_code must be an integer", "payload.exit_code")
        flag = payload.get("is_error")
        if flag is not None and type(flag) is not bool:
            raise _bad("payload.is_error must be a boolean", "payload.is_error")
    else:
        _text(payload.get("action"), "payload.action", high=256, optional=True)
        if payload.get("path") is not None:
            _abs_path(payload["path"], "payload.path")
        paths = payload.get("paths")
        if paths is not None:
            if not isinstance(paths, list) or len(paths) > MAX_PATHS:
                raise _bad(f"payload.paths must be a list of at most {MAX_PATHS} paths", "payload.paths")
            for item in paths:
                _abs_path(item, "payload.paths")


def _validate_payload(event: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _bad("payload must be an object", "payload")
    if event == "session.start":
        _obj(payload, "payload", {"source"})
        _text(payload.get("source"), "payload.source", high=64, optional=True)
    elif event == "prompt.submit":
        _obj(payload, "payload", {"text"}, {"text"})
        if not isinstance(payload["text"], str):
            raise _bad("payload.text must be a string", "payload.text")
    elif event == "tool.before":
        _obj(payload, "payload", {"tool", "native_tool", "input", "action", "path", "paths"},
             {"tool", "native_tool", "input"})
        _tool_fields(payload, after=False)
    elif event == "tool.after":
        _obj(payload, "payload", {"tool", "native_tool", "input", "response", "exit_code", "is_error"},
             {"tool", "native_tool", "input", "response"})
        _tool_fields(payload, after=True)
    elif event == "context.compact":
        _obj(payload, "payload", {"phase", "trigger", "deliver_context"}, {"phase"})
        if payload["phase"] not in ("before", "after"):
            raise _bad("payload.phase must be before or after", "payload.phase")
        if payload.get("trigger") not in (None, "auto", "manual"):
            raise _bad("payload.trigger must be auto or manual", "payload.trigger")
        if "deliver_context" in payload and type(payload["deliver_context"]) is not bool:
            raise _bad("payload.deliver_context must be a boolean", "payload.deliver_context")
    elif event == "turn.stop":
        _obj(payload, "payload", {"last_message_sha256"})
        digest = payload.get("last_message_sha256")
        if digest is not None and (not isinstance(digest, str) or not SHA256.match(digest)):
            raise _bad("payload.last_message_sha256 must be SHA-256 hex", "payload.last_message_sha256")
    else:  # session.end
        _obj(payload, "payload", {"reason"})
        _text(payload.get("reason"), "payload.reason", high=64, optional=True)
    return payload


def empty_response(req: HostHookRequest) -> dict[str, Any]:
    return {"protocol_version": PROTOCOL_VERSION, "event": req.event, "delivery_id": req.delivery_id,
            "decision": "none", "reason": None, "inject_context": None,
            "recorded": {"event_ids": [], "evidence_ids": [], "checkpoint_id": None},
            "degraded": [], "trust": req.trust}
