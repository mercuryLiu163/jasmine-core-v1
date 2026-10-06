"""Pure extraction of the G01 native tool stream from `codex exec --json` output.

This module is deliberately free of any Core or Gate dependency so that both the
operator-side session assembler and the Gate can parse the *same* bytes the same
way. The Gate re-extracts the sealed transcript with this module and refuses a
session whose recorded `tool_events` do not match it, because a metric computed
from a hand-written action list is a metric the session wrote about itself.
"""
from __future__ import annotations

import json
from typing import Any

#: Item types in the `codex exec --json` stream that are tool actions. Everything
#: else (`agent_message`, `reasoning`) is not an action and never enters the
#: metric; any other completed item is an *unknown* action, which the Gate
#: refuses rather than counting nothing: a future Codex build that records new
#: tool work must block the run, not silently undercount it.
ACTION_ITEM_TYPES = ("command_execution", "file_change", "mcp_tool_call")
NON_ACTION_ITEM_TYPES = ("agent_message", "reasoning")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def extract_actions(text: str) -> list[dict[str, Any]]:
    """The ordered tool actions, one entry per native `item.completed` event.

    Granularity is the native event: a batched call appears as several
    `item.completed` events and therefore counts as several actions; a compound
    shell command is a single event and counts once. No de-duplication, no
    merging, no dropping of events this build happens not to recognise.
    """
    actions: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "command_execution":
            command = item.get("command")
            if not isinstance(command, str):
                command = ""
            actions.append({
                "kind": "shell",
                "tool_name": "shell",
                "command": command,
                "output": str(item.get("aggregated_output") or ""),
                "exit_code": int(item.get("exit_code") or 0),
            })
        elif item_type == "file_change":
            changes = []
            for change in item.get("changes") or []:
                if not isinstance(change, dict):
                    continue
                changes.append({"path": change.get("path"), "kind": change.get("kind")})
            actions.append({
                "kind": "file_change",
                "tool_name": "file_change",
                "changes": changes,
            })
        elif item_type == "mcp_tool_call":
            tool = item.get("tool") or item.get("name") or "mcp"
            actions.append({
                "kind": "mcp",
                "tool_name": f"mcp:{tool}",
                "payload": item,
            })
        elif item_type not in NON_ACTION_ITEM_TYPES:
            actions.append({
                "kind": "unknown",
                "tool_name": f"unknown:{item_type}",
                "payload": item,
            })
    return actions


def action_payload(action: dict[str, Any]) -> tuple[str, str]:
    """(command, output) for one action, canonical and bound by the Evidence row.

    The pair is what the Gate hashes to find the action's Core Evidence, so it
    must be a pure function of the extracted bytes:
      * shell       -> (verbatim command, verbatim aggregated output)
      * file_change -> ("file_change:<path>:<kind>[;...]", canonical change JSON
                        including the harness-computed before/after digests)
      * mcp         -> ("mcp:<tool>", canonical payload JSON)
    """
    kind = action.get("kind")
    if kind == "shell":
        return str(action.get("command") or ""), str(action.get("output") or "")
    if kind == "file_change":
        changes = action.get("changes") or []
        parts = []
        for change in changes:
            parts.append(f"{change.get('path')}:{change.get('kind')}")
        command = "file_change:" + ";".join(parts)
        body = []
        for change in changes:
            body.append({
                "path": change.get("path"),
                "kind": change.get("kind"),
                "before_sha256": change.get("before_sha256"),
                "after_sha256": change.get("after_sha256"),
                "diff": change.get("diff"),
            })
        return command, _canonical(body)
    if kind == "mcp":
        return str(action.get("tool_name") or "mcp"), _canonical(action.get("payload"))
    if kind == "unknown":
        return str(action.get("tool_name") or "unknown"), _canonical(action.get("payload"))
    raise ValueError(f"unknown action kind {kind!r}")
