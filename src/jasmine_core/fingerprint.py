"""Server-side workspace fingerprints with explicit UNKNOWN for incomplete scans.

The workspace root is operator configuration, not a caller-supplied path. Git
workspaces hash tracked and non-ignored untracked files. Plain directories hash
their regular files. A limit, unreadable file, or symlink makes the snapshot
partial; two partial snapshots can never compare SAME.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from . import clock, errors
from .canonical import body_hash

MAX_FILES = 20_000
MAX_BYTES = 1024 * 1024 * 1024
EXCLUDED_DIRS = frozenset({".git", ".codex", ".jasmine", "evidence", "artifacts", "_archive"})
EXCLUDED_NAMES = frozenset({"auth.json", "capture-token", "core.db"})


def _relevant(relative: str) -> bool:
    parts = Path(relative).parts
    name = parts[-1] if parts else ""
    return (bool(parts) and not any(part in EXCLUDED_DIRS for part in parts[:-1])
            and name not in EXCLUDED_NAMES and not name.endswith((".sqlite", ".db", "-wal", "-shm")))


def _git(root: Path, *args: str) -> bytes | None:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=15, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def configured_root() -> Path:
    raw = os.environ.get("JASMINE_CORE_WORKSPACE_ROOT")
    if not raw:
        raise errors.FingerprintUnavailable("operator has not configured JASMINE_CORE_WORKSPACE_ROOT")
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise errors.FingerprintUnavailable("configured workspace root is unavailable") from exc
    if not resolved.is_dir():
        raise errors.FingerprintUnavailable("configured workspace root is not a directory")
    return resolved


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def capture(root: Path | None = None) -> dict[str, Any]:
    root = configured_root() if root is None else root.resolve(strict=True)
    if not root.is_dir():
        raise errors.FingerprintUnavailable("workspace root is not a directory")
    top = _git(root, "rev-parse", "--show-toplevel")
    is_git = top is not None and Path(os.fsdecode(top.strip())).resolve() == root
    git_head = os.fsdecode((_git(root, "rev-parse", "HEAD") or b"").strip()) if is_git else None
    candidates: list[str] = []
    partial_reasons: list[str] = []
    if is_git:
        listed = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if listed is None:
            partial_reasons.append("git listing failed")
        else:
            candidates = sorted(set(os.fsdecode(item) for item in listed.split(b"\0")
                                    if item and _relevant(os.fsdecode(item))))
    else:
        try:
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
                for name in sorted(files):
                    relative = (Path(directory) / name).relative_to(root).as_posix()
                    if _relevant(relative):
                        candidates.append(relative)
                for name in dirs:
                    if (Path(directory) / name).is_symlink():
                        partial_reasons.append("symlink directory")
        except OSError:
            partial_reasons.append("directory listing failed")
    if len(candidates) > MAX_FILES:
        partial_reasons.append("file count limit")
        candidates = candidates[:MAX_FILES]
    hashes: dict[str, str] = {}
    total_bytes = 0
    for relative in candidates:
        candidate = root / relative
        try:
            mode = candidate.lstat().st_mode
            if not stat.S_ISREG(mode):
                partial_reasons.append("non-regular file")
                continue
            digest, length = _hash_file(candidate)
            total_bytes += length
            if total_bytes > MAX_BYTES:
                partial_reasons.append("byte limit")
                break
            hashes[relative] = digest
        except (OSError, ValueError):
            partial_reasons.append("unreadable file")
    if is_git:
        changed = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
        if changed is None:
            partial_reasons.append("git status failed")
            changed_paths: list[str] = []
        else:
            # This is display metadata only; completeness is decided by the
            # full file hashes above, not by status or git_head alone.
            changed_paths = sorted({os.fsdecode(entry[3:]) for entry in changed.split(b"\0")
                                    if len(entry) >= 4})
    else:
        changed_paths = []
    return {"root": str(root), "git_head": git_head or None, "is_git": is_git,
            "dirty": bool(changed_paths) if is_git else None,
            "changed_paths": changed_paths, "selected_hashes": hashes,
            "manifest_sha256": body_hash(hashes), "complete": not partial_reasons,
            "partial_reasons": sorted(set(partial_reasons)), "file_count": len(hashes),
            "captured_at": clock.now_rfc3339()}


def compare(left: dict[str, Any], right: dict[str, Any]) -> str:
    if not left.get("complete") or not right.get("complete"):
        return "UNKNOWN"
    if left.get("root") != right.get("root") or left.get("is_git") != right.get("is_git"):
        return "MISMATCH"
    return ("SAME" if left.get("git_head") == right.get("git_head") and
            left.get("manifest_sha256") == right.get("manifest_sha256") else "MISMATCH")
