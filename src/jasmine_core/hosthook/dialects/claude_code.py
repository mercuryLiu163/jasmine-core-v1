"""Claude Code dialect (ADR 0017 section 9; details in docs/hosthook-claude-code-dialect.md).

Constructed from the Claude Code hooks documentation at
https://code.claude.com/docs/en/hooks, read on 2026-10-12 through a summarising fetch
tool (not raw HTML). No native Claude Code session has been run against this module:
every native field name and output shape below is what the (summarised) documentation
states or what is conventional for that page, not something observed. Fields that
could not be confirmed are listed in the dialect document.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...canonical import canonical_json
from . import Outcome, Translated, _common as common, delivery_id

NAME = "claude-code"
ADAPTER_VERSION = "1"

CAPABILITIES: dict[str, Any] = {
    "agent": "claude-code",
    "dialect_version": "1",
    "events": ["session.start", "prompt.submit", "tool.before", "tool.after",
               "context.compact", "turn.stop", "session.end"],
    "can_block_tool": True,
    "can_ask": True,
    "can_inject_context": True,
    "inject_events": ["session.start", "prompt.submit"],
    "has_turn_id": True,
    "has_call_id": True,
    "documentation": {
        "url": "https://code.claude.com/docs/en/hooks",
        "read_on": "2026-10-12",
        "status": ("constructed from docs (summarised fetch); no native verification; turn id is the documented "
                   "prompt_id when present, else a dialect substitute (docs/hosthook-claude-code-dialect.md)"),
    },
}

_EVENTS = {
    "SessionStart": "session.start",
    "SessionEnd": "session.end",
    "UserPromptSubmit": "prompt.submit",
    "PreToolUse": "tool.before",
    "PostToolUse": "tool.after",
    "PreCompact": "context.compact",
    "Stop": "turn.stop",
}
REASON_LIMIT = 500

#: native tool -> (category, tool_input key holding the file operand, Guard action)
_FILE_TOOLS = {
    "Read": ("file.read", "file_path", "read"),
    "Write": ("file.write", "file_path", "write"),
    "Edit": ("file.edit", "file_path", "edit"),
    "MultiEdit": ("file.edit", "file_path", "edit"),
    "NotebookEdit": ("file.edit", "notebook_path", "edit"),
    "Glob": ("file.read", "path", "read"),
    "Grep": ("file.read", "path", "read"),
}
_WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})


_sha, _text = common.sha, common.text


def _category(tool_name: str) -> str:
    if tool_name == "Bash":
        return "shell"
    if tool_name in _FILE_TOOLS:
        return _FILE_TOOLS[tool_name][0]
    if tool_name in _WEB_TOOLS:
        return "web"
    if tool_name.startswith("mcp__"):
        return "mcp"
    return "other"


_exit_code = common.exit_code


def _is_error(response: Any) -> bool | None:
    if isinstance(response, dict):
        for key in ("is_error", "isError"):
            if type(response.get(key)) is bool:
                return response[key]
        if response.get("interrupted") is True:
            return True
    return None


def passthrough(native: dict[str, Any]) -> tuple[str, int]:
    return "", 0


def translate_in(native: dict[str, Any], now: datetime) -> Translated | None:
    name = native.get("hook_event_name")
    event = _EVENTS.get(name)
    session = _text(native.get("session_id"))
    if event is None or session is None:
        return None
    cwd = _text(native.get("cwd"))
    # Claude Code has no turn id in the shape other agents use. The documented `prompt_id`
    # (a UUID for the user prompt being processed, absent before the first input) is used as
    # the turn id when present. Without it, only prompt.submit gets a content-derived
    # substitute; other events carry no turn id. See the dialect document for stability.
    prompt_id = _text(native.get("prompt_id"))
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    # discriminator that separates repeated hook runs when nothing native does:
    # exact when the native prompt_id exists, otherwise the invocation second.
    when = prompt_id or stamp
    common = dict(session_id=session, cwd=cwd, native_event=name)
    if event == "session.start":
        source = _text(native.get("source"))
        return Translated(event=event, turn_id=None, call_id=None, payload={"source": source} if source else {},
                          delivery_id=delivery_id(NAME, event, session, source or "", stamp), **common)
    if event == "session.end":
        reason = _text(native.get("reason"))
        return Translated(event=event, turn_id=prompt_id, call_id=None, payload={"reason": reason} if reason else {},
                          delivery_id=delivery_id(NAME, event, session, reason or "", stamp), **common)
    if event == "prompt.submit":
        prompt = native.get("prompt")
        if not isinstance(prompt, str):
            return None
        turn = prompt_id or "cc-" + _sha(session + "\0" + prompt)[:16]
        return Translated(event=event, turn_id=turn, call_id=None, payload={"text": prompt},
                          delivery_id=delivery_id(NAME, event, session, when, _sha(prompt)), **common)
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
        elif tool in _FILE_TOOLS:
            operand = tool_input.get(_FILE_TOOLS[tool][1])
            if isinstance(operand, str) and operand:
                files, action = [operand], _FILE_TOOLS[tool][2]
        return Translated(event=event, turn_id=prompt_id, call_id=call, payload=payload, files=files,
                          file_action=action, delivery_id=delivery_id(NAME, event, session, call), **common)
    if event == "context.compact":
        trigger = native.get("trigger") if native.get("trigger") in ("auto", "manual") else None
        payload = {"phase": "before"}
        if trigger:
            payload["trigger"] = trigger
        # Only PreCompact exists; compaction is followed by SessionStart(source=compact), which is
        # where context can be injected (handled as session.start).
        return Translated(event=event, turn_id=prompt_id, call_id=None, payload=payload,
                          delivery_id=delivery_id(NAME, event, session, "before", trigger or "", when), **common)
    # Stop
    last = native.get("last_assistant_message")
    payload = {"last_message_sha256": _sha(last)} if isinstance(last, str) and last else {}
    return Translated(event=event, turn_id=prompt_id, call_id=None, payload=payload,
                      delivery_id=delivery_id(NAME, event, session, when, payload.get("last_message_sha256", ""),
                                              str(bool(native.get("stop_hook_active")))), **common)


_pre_tool = common.permission_decision
_inject = common.additional_context


def translate_out(tr: Translated, outcome: Outcome) -> tuple[str, int]:
    if tr.event == "tool.before":
        if outcome.status == "ok" and outcome.response is not None:
            decision = outcome.response.get("decision")
            reason = outcome.response.get("reason") or "Jasmine Authority"
            if decision in ("deny", "ask"):
                # Claude Code documents "ask" natively: the user is prompted. It is not downgraded.
                return _pre_tool(decision, reason), 0
            # "allow" is deliberately NOT emitted as permissionDecision "allow": that would
            # skip Claude Code's own permission prompt. No output leaves its normal flow.
            return "", 0
        if outcome.status == "no_binding":
            return "", 0
        if outcome.on_unavailable == "deny":
            return _pre_tool("deny", "Jasmine Core could not check this tool call (on_unavailable=deny)"), 0
        return "", 0
    if outcome.status == "ok" and outcome.response is not None:
        inject = outcome.response.get("inject_context")
        if inject and tr.event in ("session.start", "prompt.submit"):
            hook = "SessionStart" if tr.event == "session.start" else "UserPromptSubmit"
            return _inject(hook, inject["text"]), 0
    return "", 0


#: Project-level registration data used by `jasmine-core hooks install` (hosthook/install.py).
#: Matchers are omitted (all tools/sources). Timeouts are in seconds (documented).
INSTALL: dict[str, Any] = {
    "global_dir": ".claude",
    "config": ".claude/settings.json",
    "events": ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact", "Stop", "SessionEnd"],
    "timeout_seconds": {"PreToolUse": 10, "default": 30},
    "status_message": "Jasmine Host Hook",
    "needs_user_trust": False,
    "new_document": {"hooks": {}},
}
