"""Dialect registry (ADR 0017 section 9).

A dialect is a module in this package that translates one agent's native hook
JSON to neutral events and the neutral response back to that agent's native
stdout/exit code. Dialects are the only place agent names and native field
names may appear. A dialect module provides:

    NAME: str                       registry key, also `--agent <NAME>`
    ADAPTER_VERSION: str
    CAPABILITIES: dict              also shipped as <NAME>.capabilities.json
    translate_in(native: dict, now: datetime) -> Translated | None
    translate_out(tr: Translated, outcome: Outcome) -> (stdout: str, exit_code: int)
    passthrough(native: dict) -> (stdout, exit_code)    used when nothing can be done
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ...canonical import canonical_json, sha256_hex


@dataclass(frozen=True)
class Translated:
    """A neutral event as produced by a dialect, before the runtime adds ownership."""

    event: str
    delivery_id: str
    session_id: str
    turn_id: str | None
    call_id: str | None
    payload: dict[str, Any]
    cwd: str | None = None
    agent_version: str | None = None
    #: File operands for file tools (raw, possibly relative) and the Guard action
    #: for them; the runtime resolves them (resolve.py).
    files: list[str] | None = None
    file_action: str | None = None
    native_event: str | None = None


@dataclass(frozen=True)
class Outcome:
    """What happened when the runtime talked to Core."""

    #: ok | unavailable | rejected | no_binding | skipped
    status: str
    response: dict[str, Any] | None = None
    #: allow | deny, from the binding (default deny, fail-closed), for a tool.before that was not checked.
    on_unavailable: str = "deny"
    note: str | None = None


def capabilities_sha256(capabilities: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(capabilities))


def delivery_id(agent: str, kind: str, *parts: str) -> str:
    """`<agent>:<kind>:<40 hex>`; parts are joined with NUL so they cannot alias."""
    import hashlib

    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:40]
    return f"{agent}:{kind}:{digest}"


def names() -> list[str]:
    """Dialects are discovered by module, so the registry itself names no agent."""
    import pkgutil

    return sorted(info.name for info in pkgutil.iter_modules(__path__) if not info.name.startswith("_"))


def load(name: str):
    if name not in names():
        raise KeyError(name)
    module = importlib.import_module(f"{__name__}.{name}")
    if getattr(module, "NAME", None) != name:
        raise KeyError(name)
    return module
