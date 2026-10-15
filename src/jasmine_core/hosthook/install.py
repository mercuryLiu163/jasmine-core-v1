"""Hook registration (ADR 0017 section 16, ADR 0019).

Agent-neutral. Each dialect supplies `INSTALL` (config file, native event names, timeouts);
this module merges the hook entries into that one file and removes only entries it owns.

Two scopes. *Project* writes `<project-dir>/<INSTALL["config"]>` and refuses any path inside a
user-global agent directory (each dialect's `INSTALL["global_dir"]`). *User* writes the one
user-level file the dialect names (`INSTALL["user"]`, honouring the agent's own config-dir
environment variable so tests can redirect it), only with an explicit confirmation, and first
copies an existing file to a timestamped backup. Both scopes keep every entry they do not own.
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
from .binding import BindingError, load_binding, load_device


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


BINDING_FLAGS = ("--binding", "--device-binding")


def command_for(agent: str, binding: Path, executable: str | None = None, flag: str = "--binding") -> str:
    """`executable` is a command prefix (shell words), e.g. `/venv/bin/python -m jasmine_core.cli`."""
    prefix = shlex.split(executable) if executable else [shutil.which("jasmine-core") or "jasmine-core"]
    return " ".join(shlex.quote(part) for part in (*prefix, "hook", "--agent", agent, flag, str(binding)))


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
            if len(rest) != 2 or rest[0] not in BINDING_FLAGS:
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
    return _install_at(dialect, agent, target, command_for(agent, binding, executable), binding, loaded.on_unavailable)


def _install_at(dialect: Any, agent: str, target: Path, command: str, binding: Path, on_unavailable: str,
                backup: bool = False) -> dict[str, Any]:
    spec = dialect.INSTALL
    original = target.read_bytes() if target.exists() else None
    document = _read(target, dialect)
    for event in spec["events"]:
        groups, _ = _strip_owned(_groups(document, event), agent, None)
        timeout = spec["timeout_seconds"].get(event, spec["timeout_seconds"]["default"])
        handler = {"type": "command", "command": command, "timeout": timeout}
        if spec.get("status_message"):
            handler["statusMessage"] = spec["status_message"]
        groups.append({"hooks": [handler]})
        document["hooks"][event] = groups
    backup_path = _backup(target, original) if backup else None
    _write(target, document)
    notes = []
    if spec.get("needs_user_trust"):
        notes.append("the agent requires the user to review and trust new hooks before they run")
    if on_unavailable == "allow":
        notes.append("binding sets on_unavailable=allow: tool calls run unchecked when Core is unreachable")
    result = {"result": "installed", "agent": agent, "config": str(target), "events": list(spec["events"]),
              "binding": str(binding), "notes": notes}
    if backup_path is not None:
        result["backup"] = str(backup_path)
    return result


def _backup(target: Path, original: bytes | None) -> Path | None:
    """Copy the file as it is now, once per install that will change it; None when there is nothing to save."""
    if original is None:
        return None
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = target.with_name(f"{target.name}.jasmine-backup-{stamp}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(original)
    return path


def uninstall(agent: str, project_dir: Path, binding_file: Path | None = None) -> dict[str, Any]:
    dialect = _dialect(agent)
    return _uninstall_at(dialect, agent, target_path(dialect, project_dir), binding_file)


def _uninstall_at(dialect: Any, agent: str, target: Path, binding_file: Path | None,
                  backup: bool = False) -> dict[str, Any]:
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
    result: dict[str, Any] = {"result": "uninstalled" if removed else "not_installed", "agent": agent,
                              "config": str(target), "removed": removed}
    if removed:
        if backup:
            saved = _backup(target, target.read_bytes())
            if saved is not None:
                result["backup"] = str(saved)
        # User scope (`backup` is its marker) edits the user's real files: leave them as they were found.
        if backup and not document["hooks"] and document == dialect.INSTALL["new_document"]:
            # Nothing but the skeleton we would have created is left: this file was ours alone.
            target.unlink()
            result["file_removed"] = True
        else:
            if backup and not document["hooks"] and dialect.INSTALL.get("hooks_key_optional"):
                del document["hooks"]  # `_read` added the empty key; the agent does not need it
            _write(target, document)
    return result


def status(agent: str, project_dir: Path) -> dict[str, Any]:
    dialect = _dialect(agent)
    return _status_at(dialect, agent, target_path(dialect, project_dir))


def _status_at(dialect: Any, agent: str, target: Path) -> dict[str, Any]:
    spec = dialect.INSTALL
    report: dict[str, Any] = {"agent": agent, "config": str(target), "exists": target.exists(),
                              "events": {}, "bindings": [], "other_entries": 0}
    if not target.exists():
        report["events"] = {event: False for event in spec["events"]}
        report["installed"] = False
        return report
    document = _read(target, dialect)
    bindings: set[str] = set()
    flag_of: dict[str, str] = {}
    for event in spec["events"]:
        found = False
        for group in document["hooks"].get(event, []) if isinstance(document["hooks"].get(event), list) else []:
            for handler in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if isinstance(handler, dict) and owns(handler.get("command"), agent):
                    found = True
                    words = shlex.split(handler["command"])
                    bindings.add(words[-1])
                    flag_of[words[-1]] = words[-2]
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
            load_device(Path(path)) if flag_of.get(path) == "--device-binding" else load_binding(Path(path))
        except BindingError as exc:
            problems.append(f"{path}: {exc}")
    report["binding_problems"] = problems
    report["native_effect"] = "not verified: status reads the config file only"
    return report


# -- user scope (ADR 0019) -------------------------------------------------------------


def user_target(dialect: Any, environ: dict[str, str] | None = None) -> Path:
    """The single user-level file this dialect's agent reads hooks from."""
    spec = dialect.INSTALL.get("user")
    if not spec:
        raise InstallError("this agent has no user-level hook registration")
    environ = os.environ if environ is None else environ
    chosen = environ.get(spec["env"])
    base = Path(chosen) if chosen else Path(os.path.expanduser("~")) / spec["default_dir"]
    if not base.is_absolute():
        raise InstallError(f"{spec['env']} must be an absolute path")
    target = base / spec["config"]
    for ancestor in (target, target.parent, base):
        if ancestor.is_symlink():
            raise InstallError(f"{ancestor} is a symlink; refusing")
    return target


def _device_binding(path: Path) -> Path:
    device = Path(path)
    if not device.is_absolute():
        device = Path(os.path.abspath(device))
    try:
        load_device(device)
    except BindingError as exc:
        raise InstallError(f"device binding file unusable: {exc}") from exc
    return device


def install_user(agent: str, device_binding: Path, executable: str | None = None, *, confirmed: bool = False,
                 environ: dict[str, str] | None = None) -> dict[str, Any]:
    if not confirmed:
        raise InstallError("user-level registration changes your own agent configuration for every project on this "
                           "machine; pass --confirm-user-scope to proceed")
    dialect = _dialect(agent)
    target = user_target(dialect, environ)
    device = _device_binding(device_binding)
    loaded = load_device(device)
    result = _install_at(dialect, agent, target, command_for(agent, device, executable, "--device-binding"),
                         device, loaded.on_unavailable, backup=True)
    result["scope"] = "user"
    # An agent that also loads another agent's user hook file starts that agent's command too.
    for reader in dialects.names():
        for source in dialects.load(reader).INSTALL.get("reads_hooks_of", []):
            if agent in (reader, source) and _registered(reader, environ) and _registered(source, environ):
                result["notes"].append(f"{reader} also loads the {source} user hook file, so the {source} command "
                                       f"starts there too; it does not understand {reader} payloads and records nothing")
    return result


def _registered(agent: str, environ: dict[str, str] | None) -> bool:
    try:
        module = dialects.load(agent)
        return any(_status_at(module, agent, user_target(module, environ))["events"].values())
    except (InstallError, KeyError):
        return False


def uninstall_user(agent: str, device_binding: Path | None = None, *, environ: dict[str, str] | None = None) -> dict[str, Any]:
    dialect = _dialect(agent)
    target = user_target(dialect, environ)
    if not target.exists():
        return {"result": "not_installed", "agent": agent, "config": str(target), "removed": 0, "scope": "user"}
    result = _uninstall_at(dialect, agent, target, Path(os.path.abspath(device_binding)) if device_binding else None,
                           backup=True)
    result["scope"] = "user"
    return result


def status_user(agent: str, *, environ: dict[str, str] | None = None) -> dict[str, Any]:
    dialect = _dialect(agent)
    report = _status_at(dialect, agent, user_target(dialect, environ))
    report["scope"] = "user"
    return report
