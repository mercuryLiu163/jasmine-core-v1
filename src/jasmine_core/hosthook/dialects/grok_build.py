"""Grok Build dialect (ADR 0017 section 9, ADR 0018; details in docs/hosthook-grok-build-dialect.md).

Constructed from the hooks guide shipped with grok 1.0.41 (`~/.grok/docs/user-guide/10-hooks.md`,
read in full on 2026-10-13) and the open-source tool taxonomy (`xai-org/grok-build`,
`xai-grok-tools/src/tool_taxonomy.rs`). No native Grok Build conversation has been run against
this module (the machine was not signed in): every native field name below is what those
sources state, and tool input keys that they do not state are matched defensively
(`_PATH_KEYS`). Nothing here is observed behaviour until docs/hosthook-h4-results.md says so.

Differences from the other dialects that the protocol absorbs through capabilities:

* Grok never reads stdout of `SessionStart` or of an allowing `UserPromptSubmit` hook, so
  context cannot be injected there. It can be injected only as `additionalContext` of a
  tool hook, which the model reads after that tool call. The dialect therefore declares
  `inject_mode: "deferred"`: the runtime keeps the pack Core returned for `session.start`,
  `prompt.submit` and a post-compact `context.compact`, and hands it to the next
  `tool.after` of the same session (runtime.py).
* All failures of a hook are fail-open in Grok; only an explicit deny blocks. The runtime
  already emits an explicit deny when Core is unreachable (`on_unavailable=deny`).
* Hook payload keys are camelCase (`sessionId`, `toolName`, `toolInput`, `promptId`); the
  PascalCase event name is under `hook_event_name`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ...canonical import canonical_json
from . import Outcome, Translated, _common as common, delivery_id

NAME = "grok-build"
ADAPTER_VERSION = "1"

CAPABILITIES: dict[str, Any] = {
    "agent": "grok-build",
    "dialect_version": "1",
    "events": ["session.start", "prompt.submit", "tool.before", "tool.after",
               "context.compact", "turn.stop", "session.end"],
    "can_block_tool": True,
    "can_ask": True,
    "can_inject_context": True,
    "inject_mode": "deferred",
    "inject_events": ["tool.after"],
    "has_turn_id": True,
    "has_call_id": True,
    "documentation": {
        "url": "https://docs.x.ai/build/features/hooks",
        "local": "grok 1.0.41 user guide 10-hooks.md",
        "read_on": "2026-10-13",
        "status": ("constructed from docs; no native verification; tool input key names other than "
                   "`command` are unconfirmed"),
    },
}

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

#: Wire names of Grok's shell tools across its toolsets (tool_taxonomy.rs, hook docs).
_SHELL = frozenset({"run_terminal_command", "run_terminal_cmd", "bash", "Bash"})
#: native tool -> (category, Guard action)
_FILE_TOOLS = {
    "read_file": ("file.read", "read"), "read": ("file.read", "read"),
    "grep": ("file.read", "read"), "list_dir": ("file.read", "read"), "glob": ("file.read", "read"),
    "write": ("file.write", "write"), "write_file": ("file.write", "write"),
    "search_replace": ("file.edit", "edit"), "edit": ("file.edit", "edit"),
    "hashline_edit": ("file.edit", "edit"), "multi_edit": ("file.edit", "edit"),
    "apply_patch": ("file.edit", "edit"),
}
_WEB = frozenset({"web_search", "web_fetch"})
#: Input keys that may hold the file operand. The canonical vocabulary names `path`; the
#: others are tolerated because raw tool arguments are not documented in the hooks guide.
_PATH_KEYS = ("path", "file_path", "target_file", "filePath", "file")
_PATCH_KEYS = ("patch", "input", "command", "diff")


def _category(tool: str) -> str:
    if tool in _SHELL:
        return "shell"
    if tool in _FILE_TOOLS:
        return _FILE_TOOLS[tool][0]
    if tool in _WEB:
        return "web"
    if "__" in tool:  # MCP calls appear as `server__tool` (hooks guide, matcher section)
        return "mcp"
    return "other"


def _operands(tool: str, tool_input: dict[str, Any]) -> list[str]:
    if tool == "apply_patch":
        for key in _PATCH_KEYS:
            patch = tool_input.get(key)
            if isinstance(patch, str):
                found = [a or b for a, b in re.findall(
                    r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", patch, re.M)]
                if found:
                    return found
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return [value]
    return []


def passthrough(native: dict[str, Any]) -> tuple[str, int]:
    return "", 0


def translate_in(native: dict[str, Any], now: datetime) -> Translated | None:
    name = native.get("hook_event_name")
    event = _EVENTS.get(name)
    session = common.text(common.pick(native, "sessionId", "session_id"))
    if event is None or session is None:
        return None
    subagent = common.text(common.pick(native, "subagentType", "subagent_type"))
    cwd = common.text(native.get("cwd"))
    prompt_id = common.text(common.pick(native, "promptId", "prompt_id"))
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    when = prompt_id or stamp
    common_kw = dict(session_id=session, cwd=cwd, native_event=name)
    if event == "session.start":
        source = common.text(native.get("source"))
        return Translated(event=event, turn_id=None, call_id=None, payload={"source": source} if source else {},
                          delivery_id=delivery_id(NAME, event, session, source or "", stamp), **common_kw)
    if event == "session.end":
        if subagent:  # a child session's teardown is not this session's end
            return None
        reason = common.text(native.get("reason"))
        return Translated(event=event, turn_id=None, call_id=None, payload={"reason": reason} if reason else {},
                          delivery_id=delivery_id(NAME, event, session, reason or "", stamp), **common_kw)
    if event == "prompt.submit":
        prompt = native.get("prompt")
        if not isinstance(prompt, str) or subagent:
            return None
        turn = prompt_id or "gb-" + common.sha(session + "\0" + prompt)[:16]
        return Translated(event=event, turn_id=turn, call_id=None, payload={"text": prompt},
                          delivery_id=delivery_id(NAME, event, session, when, common.sha(prompt)), **common_kw)
    if event in ("tool.before", "tool.after"):
        tool = common.text(common.pick(native, "toolName", "tool_name"))
        tool_input = common.pick(native, "toolInput", "tool_input")
        if tool is None:
            return None
        if not isinstance(tool_input, dict):
            tool_input = {"value": tool_input}
        call = common.text(common.pick(native, "toolUseId", "tool_use_id")) or common.sha(
            tool + canonical_json(tool_input))
        payload: dict[str, Any] = {"tool": _category(tool), "native_tool": tool, "input": tool_input}
        files = action = None
        if event == "tool.after":
            response = common.pick(native, "toolResult", "tool_response")
            payload["response"] = response if response is not None else {}
            code, error = common.exit_code(response), common.is_error(response)
            if code is not None:
                payload["exit_code"] = code
            if error is not None:
                payload["is_error"] = error
        elif tool in _FILE_TOOLS:
            found = _operands(tool, tool_input)
            if found:
                files, action = found, _FILE_TOOLS[tool][1]
        return Translated(event=event, turn_id=prompt_id, call_id=call, payload=payload, files=files,
                          file_action=action, delivery_id=delivery_id(NAME, event, session, call), **common_kw)
    if event == "context.compact":
        phase = "before" if name == "PreCompact" else "after"
        trigger = native.get("trigger") if native.get("trigger") in ("auto", "manual") else None
        payload = {"phase": phase}
        if trigger:
            payload["trigger"] = trigger
        return Translated(event=event, turn_id=prompt_id, call_id=None, payload=payload,
                          delivery_id=delivery_id(NAME, event, session, phase, trigger or "", when), **common_kw)
    # Stop. Grok also fires one at session teardown (reason channel_closed/shutdown) and one per
    # subagent turn; neither is a turn end of this session.
    reason = common.text(native.get("reason"))
    if subagent or (reason is not None and reason != "end_turn"):
        return None
    last = common.pick(native, "lastAssistantMessage", "last_assistant_message")
    payload = {"last_message_sha256": common.sha(last)} if isinstance(last, str) and last else {}
    return Translated(event=event, turn_id=prompt_id, call_id=None, payload=payload,
                      delivery_id=delivery_id(NAME, event, session, when, payload.get("last_message_sha256", ""),
                                              str(bool(common.pick(native, "stopHookActive", "stop_hook_active")))),
                      **common_kw)


def translate_out(tr: Translated, outcome: Outcome) -> tuple[str, int]:
    if tr.event == "tool.before":
        if outcome.status == "ok" and outcome.response is not None:
            decision = outcome.response.get("decision")
            reason = outcome.response.get("reason") or "Jasmine Authority"
            if decision in ("deny", "ask"):
                return common.permission_decision(decision, reason), 0
            # "allow" is not emitted: Grok documents it as "not blocked" only, and an explicit
            # allow adds nothing over silence.
            return "", 0
        if outcome.status == "no_binding":
            return "", 0
        if outcome.on_unavailable == "deny":
            return common.permission_decision("deny", "Jasmine Core could not check this tool call "
                                              "(on_unavailable=deny)"), 0
        return "", 0
    if tr.event == "tool.after" and outcome.deferred_context:
        return common.additional_context("PostToolUse", outcome.deferred_context), 0
    return "", 0


#: Project-level registration data used by `jasmine-core hooks install` (hosthook/install.py).
#: Grok loads every `<project>/.grok/hooks/*.json`, so Jasmine owns one file of its own.
#: Project hooks run only after folder trust (`/hooks-trust` or `--trust`). Observe-event
#: timeouts default to 5 s in Grok, so every handler states its own.
INSTALL: dict[str, Any] = {
    "global_dir": ".grok",
    "config": ".grok/hooks/jasmine-core.json",
    "events": ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact",
               "PostCompact", "Stop", "SessionEnd"],
    "timeout_seconds": {"PreToolUse": 10, "SessionEnd": 1, "default": 30},
    "status_message": None,
    "needs_user_trust": True,
    "new_document": {"hooks": {}},
}
