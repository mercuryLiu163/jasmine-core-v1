"""Project-local P1 Codex hooks, gated by an operator-owned session lease.

The binding file and token files are trusted runtime inputs. Hook JSON and
model-provided tool arguments cannot select a Task or actor. A nonce inherited
from the operator's Codex process atomically claims the first real session.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .. import ids
from ..api.client import ApiError, CoreClient
from ..canonical import canonical_json
from .codex_user_prompt_submit import derive_event_id

SAFE_ACTIONS = frozenset({"pwd", "true", "touch", "rm", "cat", "ls"})
PATH_ACTIONS = frozenset({"touch", "rm", "cat", "ls"})
SHELL_OPERATORS = frozenset(";&|><`$\n\r")


def _private_file(path: Path) -> bytes:
    if not path.is_absolute():
        raise ValueError("runtime file must be an absolute regular path")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("runtime file directory must be owner-only")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("runtime file must be owned by this user and private")
        if metadata.st_size > 64 * 1024:
            raise ValueError("runtime file exceeds size cap")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(64 * 1024 + 1)
    finally:
        os.close(descriptor)


def _binding(path: Path, payload: dict[str, Any], event: str) -> dict[str, Any] | None:
    try:
        config = json.loads(_private_file(path))
        if not isinstance(config, dict) or set(config) - {
            "run_nonce", "project_id", "task_id", "step_id", "host_id", "core_url",
            "token_file", "human_token_file", "trace_file"
        }:
            raise ValueError("invalid binding fields")
        nonce = config.get("run_nonce")
        if not isinstance(nonce, str) or len(nonce) < 32 or os.environ.get("JASMINE_CORE_GATE_NONCE") != nonce:
            return None
        for key, prefix in (("project_id", "prj"), ("task_id", "tsk"),
                            ("step_id", "stp"), ("host_id", "hst")):
            if not ids.is_id(config.get(key), prefix):
                raise ValueError(f"invalid {key}")
        url = config.get("core_url")
        if not isinstance(url, str) or re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}", url) is None:
            raise ValueError("core_url must be loopback HTTP")
        session = payload.get("session_id")
        if not isinstance(session, str) or not session:
            raise ValueError("hook session_id missing")
        lease = path.with_name(path.name + ".lease")
        lock = path.with_name(path.name + ".lock")
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if lease.exists():
                claimed = json.loads(_private_file(lease))
                if claimed.get("run_nonce_sha256") != hashlib.sha256(nonce.encode()).hexdigest() or claimed.get("session_id") != session:
                    raise ValueError("bound session lease does not match this hook")
            elif event == "UserPromptSubmit":
                data = {"session_id": session,
                        "run_nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest()}
                fd = os.open(lease, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, (canonical_json(data) + "\n").encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:
                raise ValueError("bound session lease is missing")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        return config
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # A malformed trusted binding must not silently bind another session.
        raise


def _token(config: dict[str, Any], field: str) -> str:
    value = config.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} is missing")
    token = _private_file(Path(value)).decode("utf-8").strip()
    if not token:
        raise ValueError(f"{field} is empty")
    return token


def _trace(config: dict[str, Any] | None, payload: dict[str, Any], result: str,
           *, event_id: str | None = None) -> bool:
    if not config or not isinstance(config.get("trace_file"), str):
        return False
    path = Path(config["trace_file"])
    try:
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("trace path invalid")
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError("trace directory is not private")
        if path.exists():
            metadata = path.stat()
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise ValueError("trace file is not private")
        line = {"hook_event_name": payload.get("hook_event_name"),
                "session_id": payload.get("session_id"), "turn_id": payload.get("turn_id"),
                "tool_use_id": payload.get("tool_use_id"), "result": result,
                "event_id": event_id,
                "tool_input_sha256": hashlib.sha256(canonical_json(payload.get("tool_input")).encode()).hexdigest()
                if payload.get("tool_input") is not None else None}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (canonical_json(line) + "\n").encode())
        finally:
            os.close(fd)
        return True
    except (OSError, ValueError, TypeError):
        print("jasmine-p1-hook: private trace unavailable", file=sys.stderr)
        return False


def _deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason[:200]}}


def _simple_action(payload: dict[str, Any]) -> tuple[str, str | None]:
    if payload.get("tool_name") != "Bash":
        raise ValueError("unsupported tool path")
    arguments = payload.get("tool_input")
    command = arguments.get("command") if isinstance(arguments, dict) else None
    if not isinstance(command, str) or not command or any(char in SHELL_OPERATORS for char in command):
        raise ValueError("unresolved or compound shell command")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("unresolved shell quoting") from exc
    if not tokens or len(tokens) > 2:
        raise ValueError("unsupported command shape")
    action = Path(tokens[0]).name
    if action not in SAFE_ACTIONS or tokens[0] != action:
        raise ValueError("unsupported shell action")
    if action in PATH_ACTIONS:
        if len(tokens) != 2 or not tokens[1].startswith("/"):
            raise ValueError("unresolved path")
        path = tokens[1]
        if ("//" in path or "\\" in path or "\x00" in path or
                any(part in (".", "..") for part in path.split("/")) or
                str(PurePosixPath(path)) != path):
            raise ValueError("noncanonical path")
        parts = Path(path).parts
        for index in range(1, len(parts) + 1):
            component = Path(*parts[:index])
            if component.is_symlink():
                raise ValueError("symlink path component")
        if not Path(path).parent.is_dir():
            raise ValueError("path parent is unresolved")
    elif len(tokens) != 1:
        raise ValueError("unsupported command arguments")
    else:
        path = None
    return action, path


def handle(payload: dict[str, Any], binding_path: Path) -> dict[str, Any]:
    event = payload.get("hook_event_name")
    if event not in ("PreToolUse", "PostToolUse", "UserPromptSubmit"):
        return {}
    try:
        config = _binding(binding_path, payload, event)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        if os.environ.get("JASMINE_CORE_GATE_NONCE"):
            print("jasmine-p1-hook: bound lease or binding invalid", file=sys.stderr)
        return (_deny("Jasmine bound configuration is unavailable")
                if event == "PreToolUse" and os.environ.get("JASMINE_CORE_GATE_NONCE") else {})
    if config is None:
        return {}
    try:
        if event == "PreToolUse":
            action, path = _simple_action(payload)
            client = CoreClient(config["core_url"], _token(config, "token_file"))
            check = client.post("/v1/guard/check", {"project_id": config["project_id"],
                "task_id": config["task_id"], "tool": "Bash", "action": action,
                **({"path": path} if path else {})})
            decision = check.get("decision")
            _trace(config, payload, f"guard:{decision}")
            if decision in ("deny", "confirm"):
                return _deny("Jasmine Authority requires operator action")
            if decision not in ("allow", "verify"):
                return _deny("Jasmine Guard returned an unresolved decision")
            return {}
        if event == "PostToolUse":
            # The exact producer mapping is checked again by Core; a hook
            # cannot promote a generic zero-exit command to TEST by assertion.
            response = payload.get("tool_response")
            if not isinstance(response, dict):
                response = {"raw_response": response}
            client = CoreClient(config["core_url"], _token(config, "token_file"))
            result = client.post("/v1/tool-results", {"task_id": config["task_id"],
                "step_id": config["step_id"], "host_id": config["host_id"],
                "codex_session_id": payload["session_id"], "turn_id": payload["turn_id"],
                "tool_use_id": payload["tool_use_id"], "tool_name": payload["tool_name"],
                "tool_input": payload["tool_input"], "tool_response": response})
            client.post("/v1/workspaces/fingerprint", {"project_id": config["project_id"],
                "host_id": config["host_id"]})
            _trace(config, payload, "captured", event_id=result["result_event"]["event_id"])
            return {}
        prompt = payload.get("prompt")
        turn = payload.get("turn_id")
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(turn, str) or not turn:
            raise ValueError("real prompt or turn id missing")
        reader = CoreClient(config["core_url"], _token(config, "token_file"))
        try:
            task = reader.get(f"/v1/tasks/{config['task_id']}")["task"]
            step = reader.get(f"/v1/steps/{config['step_id']}")["step"]
            rules = reader.get(f"/v1/rules/active?project_id={config['project_id']}&task_id={config['task_id']}")["rules"]
            if (task["project_id"] != config["project_id"] or step["task_id"] != config["task_id"] or
                    len(rules) > 20):
                raise ValueError("bound state or Rule list cannot be represented safely")
            context = {"task": {"task_id": task["task_id"], "status": task["status"],
                                "revision": task["revision"]},
                       "step": {"step_id": step["step_id"], "status": step["status"],
                                "revision": step["revision"]},
                       "active_rules": [{"rule_id": rule["rule_id"], "version": rule["version"],
                                         "severity": rule["severity"],
                                         "enforcement": rule["enforcement"],
                                         "content": rule["content"]} for rule in rules]}
            context_text = "Jasmine P1 current state: " + canonical_json(context)
            if len(context_text.encode("utf-8")) > 8000:
                raise ValueError("bound Rule/State context exceeds the supported size")
        except (KeyError, TypeError, ApiError, OSError, ValueError):
            _trace(config, payload, "state-read-unavailable")
            return {"decision": "block", "reason": "Jasmine Rule/State context is unavailable or too large"}
        client = CoreClient(config["core_url"], _token(config, "human_token_file"))
        event_id = derive_event_id(config["host_id"], payload["session_id"], turn,
                                   "p1-bound:" + prompt)
        result = client.post("/v1/events", {"event_type": "user.prompt",
            "source_system": "codex-p1-bound", "source_event_id": canonical_json([payload["session_id"], turn]),
            "host_id": config["host_id"], "project_id": config["project_id"],
            "task_id": config["task_id"], "event_id": event_id,
            "payload": {"text": prompt, "step_id": config["step_id"],
                        "turn_id": turn, "source_session_id": payload["session_id"]}})
        _trace(config, payload, "captured", event_id=result["event"]["event_id"])
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": context_text}}
    except Exception as exc:  # A bound PreToolUse error must deny, not fail open.
        _trace(config, payload, f"error:{type(exc).__name__}")
        if event == "PreToolUse":
            return _deny("Jasmine Guard is unavailable or cannot resolve this tool call")
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (OSError, ValueError):
        payload = {}
    result = handle(payload, args.binding)
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
