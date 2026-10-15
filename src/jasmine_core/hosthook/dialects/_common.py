"""Helpers shared by dialects (ADR 0017 section 9, ADR 0018).

Only agent-neutral mechanics live here: hashing, defensive field readers, and the
two output envelopes that Claude Code, Codex and Grok Build document identically
(`hookSpecificOutput.permissionDecision` and `hookSpecificOutput.additionalContext`).
Native names, tool tables and event maps stay in each dialect.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

REASON_LIMIT = 500


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def pick(native: Mapping[str, Any], *keys: str) -> Any:
    """First present key (dialects whose agent spells fields in two cases pass both)."""
    for key in keys:
        if key in native and native[key] is not None:
            return native[key]
    return None


def exit_code(response: Any) -> int | None:
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "returncode"):
            value = response.get(key)
            if type(value) is int:
                return value
    return None


def is_error(response: Any) -> bool | None:
    if isinstance(response, dict):
        for key in ("is_error", "isError"):
            if type(response.get(key)) is bool:
                return response[key]
    return None


def permission_decision(decision: str, reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision,
        "permissionDecisionReason": reason[:REASON_LIMIT]}}, ensure_ascii=False)


def additional_context(hook_event: str, body: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": hook_event, "additionalContext": body}},
                      ensure_ascii=False)
