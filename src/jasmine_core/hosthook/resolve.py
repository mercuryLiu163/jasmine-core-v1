"""Adapter-side resolution of a tool call into Guard's tool/action/path (ADR 0017 section 8).

ADR 0005 puts this duty on the caller: an unresolved tool must not be treated as a
safe approval, so anything this module cannot resolve deterministically yields no
`action`, which makes Guard answer `confirm`. It is a heuristic, not a shell parser.
"""

from __future__ import annotations

import os
import posixpath
import shlex
from typing import Any

MAX_PATHS = 8
_SHELL_OPERATORS = frozenset(";&|><`$(){}\n\r")


def _canonical(path: str) -> str | None:
    if "\x00" in path or "//" in path:
        return None
    norm = posixpath.normpath(path)
    return norm if norm.startswith("/") else None


def _real(path: str) -> str:
    """Resolve symlinks in the existing parent chain; the leaf need not exist."""
    parent, leaf = posixpath.split(path)
    try:
        resolved_parent = os.path.realpath(parent)
    except OSError:
        return path
    return posixpath.join(resolved_parent, leaf) if leaf else resolved_parent


def _with_real(paths: list[str]) -> list[str]:
    out: list[str] = []
    for path in paths:
        for candidate in (path, _real(path)):
            if candidate not in out:
                out.append(candidate)
    return out


def resolve_shell(command: Any, cwd: str | None) -> dict[str, Any]:
    """Return `{"action": str, "paths": [...]}` or `{}` when unresolved."""
    if not isinstance(command, str) or not command.strip() or any(c in _SHELL_OPERATORS for c in command):
        return {}
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return {}
    if not tokens or "=" in tokens[0] and not tokens[0].startswith("/"):
        return {}
    action = posixpath.basename(tokens[0])
    if not action:
        return {}
    base = cwd if isinstance(cwd, str) and cwd.startswith("/") else None
    found: list[str] = []
    for word in tokens[1:]:
        if word.startswith("-") or any(c in word for c in "*?[~") or "=" in word.split("/")[0] and not word.startswith("/"):
            continue
        if word.startswith("/"):
            canon = _canonical(word)
            if canon is None:
                return {"action": action}
            found.append(canon)
        elif base is not None and word:
            canon = _canonical(posixpath.join(base, word))
            if canon is not None:
                found.append(canon)
    if len(found) > MAX_PATHS // 2:
        return {"action": action}
    result: dict[str, Any] = {"action": action}
    if found:
        result["paths"] = _with_real(found)[: MAX_PATHS]
    return result


def resolve_files(paths: list[str], cwd: str | None, action: str) -> dict[str, Any]:
    """Resolve explicit file operands (file tools) the same way."""
    base = cwd if isinstance(cwd, str) and cwd.startswith("/") else None
    found: list[str] = []
    for item in paths:
        if not isinstance(item, str) or not item:
            return {}
        joined = item if item.startswith("/") else (posixpath.join(base, item) if base else None)
        canon = _canonical(joined) if joined else None
        if canon is None:
            return {}
        found.append(canon)
    if not found or len(found) > MAX_PATHS // 2:
        return {}
    return {"action": action, "paths": _with_real(found)[:MAX_PATHS]}
