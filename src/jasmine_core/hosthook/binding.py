"""Binding files for the hook runtime (ADR 0017 section 10, ADR 0019).

A binding names the Core, the ownership (project/task/step/host) and the
private key files. It never contains a token. Ownership comes only from here:
neither the model nor tool arguments can select a Task.

Two forms exist. A *full* binding (`<project>/.jasmine/host-binding.json`) holds everything and
is what project-level hook registration uses. With a *device* binding (one per machine, named
by the globally registered hook command) the project file shrinks to ownership data only
(`project_id`, `task_id`, `step_id`, `inject_context`); the Core address, host id, key files
and failure policy come from the device file and a project file may not set them.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import ids

BINDING_RELATIVE = Path(".jasmine") / "host-binding.json"
ENV_BINDING = "JASMINE_HOST_BINDING"
LOOPBACK_URL = re.compile(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):[0-9]{1,5}\Z")
ALLOWED = {"version", "core_url", "host_id", "project_id", "task_id", "step_id", "api_key_file",
           "human_api_key_file", "on_unavailable", "spool_dir", "timeout_seconds", "inject_context"}
#: What a project file may say when a device binding applies (everything else is device-owned).
PROJECT_FIELDS = {"version", "project_id", "task_id", "step_id", "inject_context"}
DEVICE_FIELDS = {"version", "core_url", "host_id", "api_key_file", "human_api_key_file", "on_unavailable",
                 "spool_dir", "timeout_seconds", "inject_context"}
MAX_FILE_BYTES = 64 * 1024


class BindingError(Exception):
    """The binding or a key file is unusable. Message is safe to print (no secrets)."""


@dataclass(frozen=True)
class Binding:
    path: Path
    core_url: str
    host_id: str
    project_id: str | None
    task_id: str | None
    step_id: str | None
    api_key_file: Path
    human_api_key_file: Path | None
    on_unavailable: str
    spool_dir: Path
    timeout_seconds: float | None
    inject_context: bool = True
    #: Set when the device binding supplied the device-owned fields (ADR 0019).
    device_path: Path | None = None

    def key_file_for(self, event: str) -> Path:
        if event == "prompt.submit" and self.human_api_key_file is not None:
            return self.human_api_key_file
        return self.api_key_file


def find_binding(explicit: str | os.PathLike | None, environ: dict[str, str], cwd: str | None) -> Path | None:
    """`--binding`, else JASMINE_HOST_BINDING, else upward search from cwd."""
    chosen = explicit or environ.get(ENV_BINDING)
    if chosen:
        path = Path(chosen)
        return path if path.is_file() else None
    if cwd and os.path.isabs(cwd):
        current = Path(cwd)
        for directory in (current, *current.parents):
            candidate = directory / BINDING_RELATIVE
            if candidate.is_file():
                return candidate
    return None


def _private_bytes(path: Path, what: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise BindingError(f"{what} is not readable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BindingError(f"{what} must be a regular file owned by this user with mode 0600")
        if info.st_size > MAX_FILE_BYTES:
            raise BindingError(f"{what} is too large")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(MAX_FILE_BYTES + 1)
    finally:
        os.close(descriptor)


def read_key(path: Path) -> str:
    token = _private_bytes(path, "API key file").decode("utf-8", "replace").strip()
    if not token:
        raise BindingError("API key file is empty")
    return token


def _abs_file(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not os.path.isabs(value):
        raise BindingError(f"{name} must be an absolute path")
    return Path(value)


def _read_json(path: Path, what: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise BindingError(f"{what} is too large")
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise BindingError(f"{what} is unreadable or not JSON") from exc
    if not isinstance(data, dict):
        raise BindingError(f"{what} must be a JSON object")
    return data


def load_binding(path: Path, device: Path | None = None) -> Binding:
    """Load a full binding, or a project file merged onto the device binding `device`."""
    data = _read_json(path, "binding file")
    spool_base = path.parent
    if device is not None:
        base = _read_json(device, "device binding file")
        if set(base) - DEVICE_FIELDS:
            raise BindingError("device binding file has unknown fields")
        extra = sorted(set(data) - PROJECT_FIELDS)
        if extra:
            raise BindingError("project binding may not set " + ", ".join(extra) + " when a device binding is used")
        merged = dict(base)
        for key in ("project_id", "task_id", "step_id"):
            if data.get(key) is not None:
                merged[key] = data[key]
        if data.get("inject_context") is False:  # a project may withhold the pack, never force it on
            merged["inject_context"] = False
        data, spool_base = merged, device.parent
    elif set(data) - ALLOWED:
        raise BindingError("binding file has unknown fields")
    return _build(data, path, spool_base, device)


def load_device(path: Path) -> Binding:
    """The device binding on its own (replay, installer checks): no project ownership."""
    data = _read_json(path, "device binding file")
    if set(data) - DEVICE_FIELDS:
        raise BindingError("device binding file has unknown fields")
    return _build(data, path, path.parent, path)


def _build(data: dict[str, Any], path: Path, spool_base: Path, device: Path | None) -> Binding:
    if data.get("version") != 1:
        raise BindingError("binding version must be 1")
    url = data.get("core_url")
    if not isinstance(url, str) or not LOOPBACK_URL.match(url):
        raise BindingError("core_url must be loopback http://HOST:PORT")
    for name, prefix in (("host_id", "hst"), ("project_id", "prj"), ("task_id", "tsk"), ("step_id", "stp")):
        value = data.get(name)
        if value is None and name != "host_id":
            continue
        if not isinstance(value, str) or not ids.is_id(value, prefix):
            raise BindingError(f"{name} must be a {prefix}_ id")
    policy = data.get("on_unavailable", "deny")
    if policy not in ("allow", "deny"):
        raise BindingError("on_unavailable must be allow or deny")
    timeout = data.get("timeout_seconds")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10):
        raise BindingError("timeout_seconds must be in (0, 10]")
    inject = data.get("inject_context", True)
    if type(inject) is not bool:
        raise BindingError("inject_context must be a boolean")
    spool = data.get("spool_dir")
    spool_dir = _abs_file(spool, "spool_dir") if spool else spool_base / "spool"
    human = data.get("human_api_key_file")
    return Binding(path=path, core_url=url, host_id=data["host_id"], project_id=data.get("project_id"),
                   task_id=data.get("task_id"), step_id=data.get("step_id"),
                   api_key_file=_abs_file(data.get("api_key_file"), "api_key_file"),
                   human_api_key_file=_abs_file(human, "human_api_key_file") if human else None,
                   on_unavailable=policy, spool_dir=spool_dir,
                   timeout_seconds=float(timeout) if timeout is not None else None, inject_context=inject,
                   device_path=device)
