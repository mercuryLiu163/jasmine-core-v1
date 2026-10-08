#!/usr/bin/env python3
"""Jasmine P4 SessionStart hook: inject the staged Context Pack, write a receipt.

Installed by `scripts/p4-codex-hook-install.py` into
`<repo>/.codex/jasmine/jasmine-p4-session-start.py` and invoked by Codex as a
project-local command hook. It is stdlib-only and imports nothing from the repo,
so it works regardless of the session's cwd.

Codex writes one JSON object to stdin and the hook writes its injection to
stdout. The receipt records the exact bytes injected, the pack's digest, the
installed script's digest and the payload's session id; what it cannot prove is
*which process* ran the script, because it is an ordinary program and a
hand-written payload produces the same artifact. Whether Codex fired the hook is
an operator attestation recorded in the Gate manifest, not a property of the
receipt. A payload that is not a SessionStart for one of the four documented
sources is not this hook's business, so it exits 0 silently.

The injection is staged by the Gate:

    <repo>/.codex/jasmine/injection.json   {context_pack_id, task_id,
                                            rendered_path (relative to <repo>),
                                            rendered_sha256}
    <repo>/.codex/jasmine/rendered-*.txt   the exact rendered pack bytes

If the staged bytes do not hash to `rendered_sha256`, the hook injects nothing
and writes no receipt (exit 2): a mismatched pack must never reach an agent.

The receipt at `<repo>/.codex/jasmine/session-start-<session_id>.json` is
write-once. A re-trigger in the same session that offers the same injected bytes
prints the injection again and exits 0 without touching the receipt, so a Codex
`resume` can re-inject what the session already read. A re-trigger whose pack
bytes differ from the receipt's `injected_sha256` is refused (exit 2): the
receipt is what binds this session to the exact pack bytes that were injected,
and an overwritten receipt proves nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCES = ("startup", "resume", "clear", "compact")
INJECTION_PATH = Path(".codex") / "jasmine" / "injection.json"
RECEIPT_DIR = Path(".codex") / "jasmine"
INJECTED_VIA = "hookSpecificOutput.additionalContext"


def _refuse(message: str) -> int:
    """Exit 2 with a message on stderr and no stdout, receipt or injection."""
    sys.stderr.write("jasmine-p4-session-start: " + message + "\n")
    return 2


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parent_process_name() -> str | None:
    """The invoking process's name, as supporting evidence and nothing more.

    `os.getppid()` is what the OS reports, but a name proves nothing on its own:
    a person can run this script from a shell, and `ps` can be missing or refuse.
    So the field is recorded when it is available and never treated as proof of
    the producer. It is deliberately not a hard gate anywhere.
    """
    try:
        completed = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(os.getppid())],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    name = completed.stdout.strip()
    if completed.returncode != 0 or not name:
        return None
    return name.splitlines()[0].strip() or None


def _emit(stdout: bytes) -> None:
    sys.stdout.buffer.write(stdout + b"\n")
    sys.stdout.buffer.flush()


def _write_once(path: Path, body: bytes) -> None:
    """Create `path` with `body`, or leave identical bytes alone.

    `O_EXCL` rather than an existence check: the receipt is written once, and a
    concurrent invocation must lose the create race rather than overwrite it.
    """
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.read_bytes() != body:
            raise
        return
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def handle(payload: dict) -> int:
    if payload.get("hook_event_name") != "SessionStart":
        return 0
    source = payload.get("source")
    if source not in SOURCES:
        return 0
    cwd = payload.get("cwd")
    session_id = payload.get("session_id")
    if not isinstance(cwd, str) or not cwd:
        return 0
    if not isinstance(session_id, str) or not session_id:
        return 0
    if os.path.basename(session_id) != session_id or session_id in (".", ".."):
        return 0
    repo = Path(cwd)
    injection_path = repo / INJECTION_PATH
    if not injection_path.is_file():
        return 0
    try:
        injection = json.loads(injection_path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return _refuse(f"cannot read {injection_path}: {exc}")
    if not isinstance(injection, dict):
        return _refuse(f"{injection_path} is not a JSON object")
    context_pack_id = injection.get("context_pack_id")
    task_id = injection.get("task_id")
    rendered_path = injection.get("rendered_path")
    expected = injection.get("rendered_sha256")
    if not all(isinstance(value, str) and value
               for value in (context_pack_id, task_id, rendered_path, expected)):
        return _refuse(f"{injection_path} is missing context_pack_id, task_id, "
                       f"rendered_path or rendered_sha256")
    # The pointer must name a file inside the repository it claims. An absolute
    # path or a `..` traversal would let a staged `injection.json` point the hook
    # at any file on the machine, inject it and receipt it as this repository's
    # pack; resolving symlinks closes the same door.
    rendered_relative = Path(rendered_path)
    if rendered_relative.is_absolute() or ".." in rendered_relative.parts:
        return _refuse(f"{injection_path} names rendered_path {rendered_path!r}, which is "
                       f"not a relative path inside the repository")
    try:
        repo_root = repo.resolve(strict=True)
    except OSError as exc:
        return _refuse(f"cannot resolve repository {repo}: {exc}")
    rendered_file = repo / rendered_relative
    try:
        rendered_resolved = rendered_file.resolve(strict=True)
    except OSError as exc:
        return _refuse(f"cannot read {rendered_file}: {exc}")
    if not rendered_resolved.is_relative_to(repo_root):
        return _refuse(f"{rendered_file} resolves to {rendered_resolved}, outside the "
                       f"repository {repo_root}; refusing to inject a file the "
                       f"repository does not contain")
    try:
        rendered_bytes = rendered_resolved.read_bytes()
    except OSError as exc:
        return _refuse(f"cannot read {rendered_file}: {exc}")
    pack_sha256 = _sha256(rendered_bytes)
    if pack_sha256 != expected:
        return _refuse(f"{rendered_file} hashes to {pack_sha256}, injection.json "
                       f"declares {expected}; refusing to inject a mismatched pack")
    try:
        rendered = rendered_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _refuse(f"{rendered_file} is not UTF-8: {exc}")
    context_bytes = rendered.encode("utf-8")
    stdout = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                "additionalContext": rendered}},
        ensure_ascii=False).encode("utf-8")
    receipt = {
        "hook_event_name": "SessionStart",
        "source": source,
        "session_id": session_id,
        "transcript_path": payload.get("transcript_path"),
        "cwd": cwd,
        "model": payload.get("model"),
        "permission_mode": payload.get("permission_mode"),
        "context_pack_id": context_pack_id,
        "task_id": task_id,
        "rendered_path": rendered_path,
        "pack_sha256": pack_sha256,
        "injected_sha256": _sha256(context_bytes),
        "injected_bytes": len(context_bytes),
        "injected_via": INJECTED_VIA,
        "hook_script_sha256": _sha256(Path(__file__).read_bytes()),
        "parent_process_name": _parent_process_name(),
        "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    receipt_path = repo / RECEIPT_DIR / f"session-start-{session_id}.json"
    body = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
    receipt_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A session that is started, then resumed, fires SessionStart again over the
    # same staged pack. The second trigger must inject the pack again -- the
    # session needs it -- but must not rewrite the receipt: it is write-once,
    # and only the bytes the first trigger actually injected describe the pack
    # the session saw. Same pack: re-emit. Different pack: refuse.
    if receipt_path.exists():
        try:
            existing = json.loads(receipt_path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return _refuse(f"receipt {receipt_path} exists but is unreadable: {exc}")
        if (not isinstance(existing, dict)
                or existing.get("injected_sha256") != receipt["injected_sha256"]):
            return _refuse(
                f"receipt {receipt_path} already binds this session to another "
                f"injection; this session's receipt is written once, so the pack "
                f"under this session cannot change")
        _emit(stdout)
        return 0
    try:
        _write_once(receipt_path, body)
    except FileExistsError:
        try:
            raced = json.loads(receipt_path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            raced = None
        if (isinstance(raced, dict)
                and raced.get("injected_sha256") == receipt["injected_sha256"]):
            _emit(stdout)
            return 0
        return _refuse(f"receipt {receipt_path} already exists with different bytes; "
                       f"a receipt is written once and is never overwritten")
    except OSError as exc:
        return _refuse(f"cannot write receipt {receipt_path}: {exc}")
    _emit(stdout)
    return 0


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    return handle(payload)


if __name__ == "__main__":
    raise SystemExit(main())
