"""Codex dialect (ADR 0017 section 9; details in docs/hosthook-codex-dialect.md).

Constructed from the Codex hooks documentation as read on 2026-10-12
(https://developers.openai.com/codex/hooks, which redirected to
https://learn.chatgpt.com/docs/hooks). No native Codex session has been run
against this module: every native field name and output shape below is what the
documentation states, not something observed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from ...canonical import canonical_json
from . import Outcome, Translated, delivery_id

NAME = "codex"
ADAPTER_VERSION = "1"

CAPABILITIES: dict[str, Any] = {
    "agent": "codex",
    "dialect_version": "1",
    "events": ["session.start", "prompt.submit", "tool.before", "tool.after",
               "context.compact", "turn.stop", "session.end"],
    "can_block_tool": True,
    "can_ask": False,
    "can_inject_context": True,
    "inject_events": ["session.start", "prompt.submit"],
    "has_turn_id": True,
    "has_call_id": True,
    "documentation": {
        "url": "https://developers.openai.com/codex/hooks",
        "resolved_url": "https://learn.chatgpt.com/docs/hooks",
        "read_on": "2026-10-12",
        "status": "constructed from docs; no native verification",
    },
}

#: Native event name -> neutral event.
_EVENTS = {
    "SessionStart": "session.start",
    "SessionEnd": "session.end",
    "UserPromptSubmit": "prompt.submit",
    "PreToolUse": "tool.before",
    "PostToolUse": "tool.after",
    "PreCompact": "context.compact",
    "PostCompact": "context.compact",
    "Stop": "turn.stop",
}
#: Native events whose exit-0 output must be JSON (documented for Stop and
#: SubagentStop and Interrupt); a bare "{}" is the neutral no-op.
_JSON_ONLY = frozenset({"Stop", "SubagentStop", "Interrupt"})
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.M)
REASON_LIMIT = 500


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _category(tool_name: str) -> str:
    if tool_name == "Bash":
        return "shell"
    if tool_name == "apply_patch":
        return "file.edit"
    if tool_name.startswith("mcp__"):
        return "mcp"
    return "other"


def _exit_code(response: Any) -> int | None:
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "returncode"):
            value = response.get(key)
            if type(value) is int:
                return value
    return None


def _is_error(response: Any) -> bool | None:
    if isinstance(response, dict):
        for key in ("is_error", "isError"):
            if type(response.get(key)) is bool:
                return response[key]
    return None


def passthrough(native: dict[str, Any]) -> tuple[str, int]:
    return ("{}" if native.get("hook_event_name") in _JSON_ONLY else ""), 0


def translate_in(native: dict[str, Any], now: datetime) -> Translated | None:
    name = native.get("hook_event_name")
    event = _EVENTS.get(name)
    session = _text(native.get("session_id"))
    if event is None or session is None:
        return None
    turn = _text(native.get("turn_id"))
    cwd = _text(native.get("cwd"))
    common = dict(session_id=session, turn_id=turn, cwd=cwd, native_event=name)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    if event == "session.start":
        source = _text(native.get("source"))
        return Translated(event=event, delivery_id=delivery_id(NAME, event, session, source or "", stamp),
                          call_id=None, payload={"source": source} if source else {}, **common)
    if event == "session.end":
        reason = _text(native.get("reason"))
        return Translated(event=event, delivery_id=delivery_id(NAME, event, session, reason or "", stamp),
                          call_id=None, payload={"reason": reason} if reason else {}, **common)
    if event == "prompt.submit":
        prompt = native.get("prompt")
        if not isinstance(prompt, str):
            return None
        return Translated(event=event, call_id=None, payload={"text": prompt},
                          delivery_id=delivery_id(NAME, event, session, turn or "", _sha(prompt)), **common)
    if event in ("tool.before", "tool.after"):
        tool = _text(native.get("tool_name"))
        tool_input = native.get("tool_input")
        if tool is None:
            return None
        if not isinstance(tool_input, dict):
            tool_input = {"value": tool_input}
        call = _text(native.get("tool_use_id")) or _sha(tool + canonical_json(tool_input))
        payload: dict[str, Any] = {"tool": _category(tool), "native_tool": tool, "input": tool_input}
        files = action = None
        if event == "tool.after":
            response = native.get("tool_response")
            payload["response"] = response if response is not None else {}
            code, error = _exit_code(response), _is_error(response)
            if code is not None:
                payload["exit_code"] = code
            if error is not None:
                payload["is_error"] = error
        elif tool == "apply_patch":
            patch = tool_input.get("command")
            found = [a or b for a, b in _PATCH_FILE.findall(patch)] if isinstance(patch, str) else []
            if found:
                files, action = found, "edit"
        return Translated(event=event, call_id=call, payload=payload, files=files, file_action=action,
                          delivery_id=delivery_id(NAME, event, session, turn or "", call), **common)
    if event == "context.compact":
        phase = "before" if name == "PreCompact" else "after"
        trigger = native.get("trigger") if native.get("trigger") in ("auto", "manual") else None
        payload = {"phase": phase}
        if trigger:
            payload["trigger"] = trigger
        if phase == "after":
            # Codex documents no context-injection output for PostCompact.
            payload["deliver_context"] = False
        return Translated(event=event, call_id=None, payload=payload,
                          delivery_id=delivery_id(NAME, event, session, turn or "", phase, trigger or ""), **common)
    # Stop
    last = native.get("last_assistant_message")
    payload = {"last_message_sha256": _sha(last)} if isinstance(last, str) and last else {}
    return Translated(event=event, call_id=None, payload=payload,
                      delivery_id=delivery_id(NAME, event, session, turn or "", str(bool(native.get("stop_hook_active")))),
                      **common)


def _deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason[:REASON_LIMIT]}}, ensure_ascii=False)


def _inject(hook_event: str, text: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": hook_event, "additionalContext": text}},
                      ensure_ascii=False)


def translate_out(tr: Translated, outcome: Outcome) -> tuple[str, int]:
    native_event = tr.native_event or ""
    empty = "{}" if native_event in _JSON_ONLY else ""
    if tr.event == "tool.before":
        if outcome.status == "ok" and outcome.response is not None:
            decision = outcome.response.get("decision")
            reason = outcome.response.get("reason") or "Jasmine Authority"
            if decision == "deny":
                return _deny(reason), 0
            if decision == "ask":
                # Codex documents no "ask" for PreToolUse: degrade to deny, say so.
                return _deny("confirmation required (this agent cannot ask): " + reason), 0
            return "", 0
        if outcome.status == "no_binding":
            return "", 0
        if outcome.on_unavailable == "deny":
            return _deny("Jasmine Core could not check this tool call (on_unavailable=deny)"), 0
        return "", 0
    if outcome.status == "ok" and outcome.response is not None:
        inject = outcome.response.get("inject_context")
        if inject and tr.event in ("session.start", "prompt.submit"):
            hook = "SessionStart" if tr.event == "session.start" else "UserPromptSubmit"
            return _inject(hook, inject["text"]), 0
    return empty, 0


#: Project-level registration data used by `jasmine-core hooks install` (hosthook/install.py).
#: `config` is relative to the project directory. Matchers are omitted (all tools/sources).
#: Hook timeouts are in seconds. Codex requires the user to review/trust new hooks (`/hooks`).
INSTALL: dict[str, Any] = {
    "global_dir": ".codex",
    "config": ".codex/hooks.json",
    "events": ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact",
               "PostCompact", "Stop", "SessionEnd"],
    "timeout_seconds": {"PreToolUse": 10, "SessionEnd": 3, "default": 30},
    "status_message": "Jasmine Host Hook",
    "needs_user_trust": True,
    "new_document": {"description": "Jasmine Host Hook Protocol hooks (project level)", "hooks": {}},
}
