"""Project-level hook registration (ADR 0017 section 16).

Agent-neutral. Each dialect supplies `INSTALL` (config file relative to the project,
native event names, timeouts); this module merges the hook entries into that one
file and removes only entries it owns. It writes nothing outside
`<project-dir>/<INSTALL["config"]>` and refuses any path that would land in a
user-global configuration directory (each dialect's `INSTALL["global_dir"]`).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import dialects
from .binding import BindingError, load_binding


class InstallError(Exception):
    """Refusal with a message safe to print."""


def _home_dirs() -> list[Path]:
    home = Path(os.path.expanduser("~"))
    return [home]


def target_path(dialect: Any, project_dir: Path) -> Path:
    project = Path(project_dir)
    if not project.is_dir():
        raise InstallError("--project-dir must be an existing directory")
    project = project.resolve()
    for home in _home_dirs():
        home = home.resolve()
        if project == home:
            raise InstallError("refusing to use the home directory as a project: that would write user-global config")
    target = project / dialect.INSTALL["config"]
    for ancestor in (target, target.parent):
        if ancestor.is_symlink():
            raise InstallError(f"{ancestor} is a symlink; refusing")
    resolved = target.parent.resolve() / target.name
    # every dialect names its own user-global config directory (relative to home); refuse all of them
    forbidden_names = {module.INSTALL.get("global_dir") for module in
                       (dialects.load(name) for name in dialects.names())} - {None}
    for home in _home_dirs():
        for name in forbidden_names:
            forbidden = home.resolve() / name
            if forbidden == resolved.parent or forbidden in resolved.parents:
                raise InstallError("refusing to write inside a user-global agent configuration directory")
    return target


def command_for(agent: str, binding: Path, executable: str | None = None) -> str:
    """`executable` is a command prefix (shell words), e.g. `/venv/bin/python -m jasmine_core.cli`."""
    prefix = shlex.split(executable) if executable else [shutil.which("jasmine-core") or "jasmine-core"]
    return " ".join(shlex.quote(part) for part in (*prefix, "hook", "--agent", agent, "--binding", str(binding)))


def owns(command: Any, agent: str, binding: Path | None = None) -> bool:
    """True iff `command` is a hook command written by this installer for `agent`."""
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        if token == "hook" and tokens[index + 1:index + 3] == ["--agent", agent] and index > 0:
            before = tokens[index - 1]
            if not (os.path.basename(before) == "jasmine-core" or before == "jasmine_core.cli"):
                return False
            rest = tokens[index + 3:]
            if len(rest) != 2 or rest[0] != "--binding":
                return False
            return binding is None or rest[1] == str(binding)
    return False


def _read(target: Path, dialect: Any) -> dict[str, Any]:
    if not target.exists():
        return json.loads(json.dumps(dialect.INSTALL["new_document"]))
    try:
        document = json.loads(target.read_bytes() or b"{}")
    except ValueError as exc:
        raise InstallError(f"{target} is not valid JSON; refusing to overwrite it") from exc
    if not isinstance(document, dict):
        raise InstallError(f"{target} must contain a JSON object")
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("the 'hooks' key exists but is not an object; refusing")
    return document


def _groups(document: dict[str, Any], event: str) -> list[Any]:
    groups = document["hooks"].setdefault(event, [])
    if not isinstance(groups, list):
        raise InstallError(f"hooks.{event} exists but is not a list; refusing")
    return groups


def _strip_owned(groups: list[Any], agent: str, binding: Path | None) -> tuple[list[Any], int]:
    """Remove owned handlers; keep every other group/handler untouched."""
    kept, removed = [], 0
    for group in groups:
        handlers = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(handlers, list):
            kept.append(group)
            continue
        remaining = [h for h in handlers if not (isinstance(h, dict) and owns(h.get("command"), agent, binding))]
        removed += len(handlers) - len(remaining)
        if remaining:
            kept.append({**group, "hooks": remaining} if len(remaining) != len(handlers) else group)
    return kept, removed


def _write(target: Path, document: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".jasmine-hosthook-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _dialect(agent: str) -> Any:
    try:
        return dialects.load(agent)
    except KeyError as exc:
        raise InstallError(f"unknown agent {agent!r}; available: {', '.join(dialects.names())}") from exc


def install(agent: str, project_dir: Path, binding_file: Path, executable: str | None = None) -> dict[str, Any]:
    dialect = _dialect(agent)
    target = target_path(dialect, project_dir)
    binding = Path(binding_file)
    if not binding.is_absolute():
        binding = Path(os.path.abspath(binding))
    try:
        loaded = load_binding(binding)
    except BindingError as exc:
        raise InstallError(f"binding file unusable: {exc}") from exc
    document = _read(target, dialect)
    command = command_for(agent, binding, executable)
    spec = dialect.INSTALL
    for event in spec["events"]:
        groups, _ = _strip_owned(_groups(document, event), agent, None)
        timeout = spec["timeout_seconds"].get(event, spec["timeout_seconds"]["default"])
        handler = {"type": "command", "command": command, "timeout": timeout}
        if spec.get("status_message"):
            handler["statusMessage"] = spec["status_message"]
        groups.append({"hooks": [handler]})
        document["hooks"][event] = groups
    _write(target, document)
    notes = []
    if spec.get("needs_user_trust"):
        notes.append("the agent requires the user to review and trust new hooks before they run")
    if loaded.on_unavailable == "allow":
        notes.append("binding sets on_unavailable=allow: tool calls run unchecked when Core is unreachable")
    return {"result": "installed", "agent": agent, "config": str(target), "events": list(spec["events"]),
            "binding": str(binding), "notes": notes}


def uninstall(agent: str, project_dir: Path, binding_file: Path | None = None) -> dict[str, Any]:
    dialect = _dialect(agent)
    target = target_path(dialect, project_dir)
    if not target.exists():
        return {"result": "not_installed", "agent": agent, "config": str(target), "removed": 0}
    document = _read(target, dialect)
    binding = Path(os.path.abspath(binding_file)) if binding_file else None
    removed = 0
    for event in list(document["hooks"]):
        groups = document["hooks"][event]
        if not isinstance(groups, list):
            continue
        kept, count = _strip_owned(groups, agent, binding)
        removed += count
        if kept:
            document["hooks"][event] = kept
        elif count:
            del document["hooks"][event]
    if removed:
        _write(target, document)
    return {"result": "uninstalled" if removed else "not_installed", "agent": agent, "config": str(target),
            "removed": removed}


def status(agent: str, project_dir: Path) -> dict[str, Any]:
    dialect = _dialect(agent)
    target = target_path(dialect, project_dir)
    spec = dialect.INSTALL
    report: dict[str, Any] = {"agent": agent, "config": str(target), "exists": target.exists(),
                              "events": {}, "bindings": [], "other_entries": 0}
    if not target.exists():
        report["events"] = {event: False for event in spec["events"]}
        report["installed"] = False
        return report
    document = _read(target, dialect)
    bindings: set[str] = set()
    for event in spec["events"]:
        found = False
        for group in document["hooks"].get(event, []) if isinstance(document["hooks"].get(event), list) else []:
            for handler in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if isinstance(handler, dict) and owns(handler.get("command"), agent):
                    found = True
                    bindings.add(shlex.split(handler["command"])[-1])
        report["events"][event] = found
    for event, groups in document["hooks"].items():
        for group in groups if isinstance(groups, list) else []:
            for handler in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if isinstance(handler, dict) and not owns(handler.get("command"), agent):
                    report["other_entries"] += 1
    report["bindings"] = sorted(bindings)
    report["installed"] = all(report["events"].values())
    report["partial"] = any(report["events"].values()) and not report["installed"]
    problems = []
    for path in report["bindings"]:
        try:
            load_binding(Path(path))
        except BindingError as exc:
            problems.append(f"{path}: {exc}")
    report["binding_problems"] = problems
    report["native_effect"] = "not verified: status reads the config file only"
    return report
