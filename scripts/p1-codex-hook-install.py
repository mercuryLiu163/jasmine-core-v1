#!/usr/bin/env python3
"""Install project-local P1 hooks without changing P0 or Codex trust state."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MARKER = "jasmine-p1-hook.sh"
EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse")


def private(path: Path) -> bytes:
    if not path.is_absolute() or path != path.resolve(strict=True):
        raise ValueError(f"private runtime file unavailable: {path}")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError(f"private runtime directory must be owner-only: {path.parent}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        mode = os.fstat(descriptor)
        if not stat.S_ISREG(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
            raise ValueError(f"private runtime file must be owner-only: {path}")
        if mode.st_size > 64 * 1024:
            raise ValueError(f"private runtime file is too large: {path}")
        return os.read(descriptor, 64 * 1024 + 1)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--hooks-json", type=Path)
    parser.add_argument("--python", default="/opt/homebrew/bin/python3.13")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve(strict=True)
    target_dir = root / ".codex"
    target = args.hooks_json or target_dir / "hooks.json"
    wrapper = root / "scripts" / MARKER
    if target_dir.is_symlink() or target.is_symlink() or target.parent.resolve() != target_dir.resolve():
        raise ValueError("hooks target must be a regular project-local .codex file")
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise ValueError("P1 hook wrapper is missing or not executable")
    config = json.loads(private(args.binding))
    if not isinstance(config, dict) or not isinstance(config.get("run_nonce"), str) or len(config["run_nonce"]) < 32:
        raise ValueError("binding requires a private run_nonce")
    for field in ("token_file", "human_token_file"):
        private(Path(config.get(field, "")))
    check = subprocess.run([args.python, "-c", "import sys; assert sys.version_info >= (3,11)"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if check.returncode:
        raise ValueError("--python must be a working Python 3.11+ interpreter")
    command = " ".join(shlex.quote(part) for part in (
        str(wrapper), "--python", str(Path(args.python).resolve()), "--binding", str(args.binding)))
    new_entries = {
        event: {**({"matcher": "^Bash$"} if event != "UserPromptSubmit" else {}),
                "hooks": [{"type": "command", "command": command,
                            "timeout": 12, "statusMessage": f"Jasmine P1 {event}"}]}
        for event in EVENTS
    }
    if target.exists():
        if not target.is_file():
            raise ValueError("hooks target must be a regular file")
        old = target.read_bytes()
        document = json.loads(old)
        if not isinstance(document, dict) or not isinstance(document.get("hooks"), dict):
            raise ValueError("hooks JSON must contain a hooks object")
    else:
        old = None
        document = {"description": "Jasmine Core project hooks", "hooks": {}}
    for event, entry in new_entries.items():
        existing = document["hooks"].get(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"{event} hook list is invalid")
        mine = [item for item in existing if MARKER in json.dumps(item)]
        if mine and (len(mine) != 1 or mine[0] != entry) and not args.force:
            raise ValueError(f"changed P1 {event} entry requires --force")
        kept = []
        for item in existing:
            if not isinstance(item, dict) or not isinstance(item.get("hooks"), list):
                raise ValueError(f"{event} contains an invalid hook entry")
            remaining = [hook for hook in item["hooks"] if MARKER not in str(hook.get("command", ""))]
            if remaining:
                kept.append({**item, "hooks": remaining})
        document["hooks"][event] = kept + [entry]
    new = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    outcome = "unchanged" if old == new else ("would-write" if args.dry_run else "written")
    if not args.dry_run and old != new:
        target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if old is not None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = target.with_name(f"{target.name}.bak.{stamp}")
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(old)
                stream.flush()
                os.fsync(stream.fileno())
        fd, temporary = tempfile.mkstemp(prefix=".hooks.", dir=target_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(new)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print(json.dumps({"result": outcome, "target": str(target), "events": list(EVENTS),
                      "binding": str(args.binding), "trust_changed": False}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"p1-hook-install: {exc}", file=sys.stderr)
        raise SystemExit(1)
