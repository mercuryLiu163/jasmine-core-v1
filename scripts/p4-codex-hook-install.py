#!/usr/bin/env python3
"""Plan or install the project-local P4 SessionStart hook binding.

This tool writes `<repo>/.codex/hooks.json` and copies the hook script to
`<repo>/.codex/jasmine/jasmine-p4-session-start.py`; the hooks.json command is an
absolute-path invocation of that copy through a stable interpreter launcher.

It does not, and by construction cannot, write Codex trust state: it never touches
`~/.codex/config.toml` or any trust store, and it never uses
`--dangerously-bypass-hook-trust`. Trust is a human action performed in the
Codex `/hooks` browser, which is why `--plan` prints the exact steps.

  scripts/p4-codex-hook-install.py --plan --repo <repo>
  scripts/p4-codex-hook-install.py --apply --repo <repo> --python <venv>/bin/python
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK_SOURCE = ROOT / "scripts" / "jasmine-p4-session-start.py"
INSTALLED_NAME = "jasmine-p4-session-start.py"
HOOKS_PATH = Path(".codex") / "hooks.json"
JASMINE_DIR = Path(".codex") / "jasmine"
MATCHER = "startup|resume|clear|compact"
DESCRIPTION = "Jasmine P4 native SessionStart injection receipts"
STATUS_MESSAGE = "Jasmine P4 native SessionStart injection receipt"


def owned(hook: dict, script: Path, python: Path) -> bool:
    """Is this hook *ours*, and bound exactly the way this run would write it?

    A command that runs some other tool is not ours and is preserved. A command
    that references *this* hook script through some other binding (another
    interpreter or extra arguments) is ours but incompatible, and is refused
    rather than silently duplicated: two SessionStart hooks over one repository
    would inject the pack twice and write two receipts for one session.
    """
    command = hook.get("command")
    if hook.get("type") != "command" or not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    if str(script) not in tokens:
        if any(os.path.basename(token) == INSTALLED_NAME for token in tokens):
            raise ValueError(f"hooks.json already binds a P4 SessionStart hook at "
                             f"{tokens[-1]!r}; explicit operator migration required")
        return False
    if tokens != [str(python), str(script)]:
        raise ValueError("ambiguous/other binding command for this hook script; "
                         "explicit operator migration required")
    return True


def command_for(python: Path, script: Path) -> str:
    return " ".join(shlex.quote(str(part)) for part in (python, script))


def plan(repo: Path, python: Path):
    """Compute the exact hooks document; change nothing on disk."""
    repo = repo.resolve(strict=True)
    target = repo / HOOKS_PATH
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("regular project-local hooks path required")
    jasmine = repo / JASMINE_DIR
    if jasmine.is_symlink():
        raise ValueError("regular project-local jasmine directory required")
    # Preserve the venv launcher symlink: resolving it discards its site-packages.
    # The launcher's parent directory, not the launcher, has to be canonical.
    if not python.exists() or python.parent != python.parent.resolve(strict=True):
        raise ValueError("canonical venv launcher parent required")
    script = jasmine / INSTALLED_NAME
    old = target.read_bytes() if target.exists() else b""
    document = json.loads(old) if old else {"description": DESCRIPTION, "hooks": {}}
    if not isinstance(document, dict) or not isinstance(document.get("hooks"), dict):
        raise ValueError("invalid hooks document")
    entries = []
    for entry in document["hooks"].get("SessionStart", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            raise ValueError("invalid hook entry")
        remaining = [hook for hook in entry["hooks"]
                     if not owned(hook, script, python)]
        if remaining:
            entries.append({**entry, "hooks": remaining})
    entries.append({"matcher": MATCHER, "hooks": [{
        "type": "command", "command": command_for(python, script),
        "timeout": 30, "additionalContextLimit": 0,
        "statusMessage": STATUS_MESSAGE}]})
    document["hooks"]["SessionStart"] = entries
    new = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    return target, old, new, script


def install_script(script: Path) -> str:
    """Copy the hook source to its stable path; an update is reported, not hidden.

    The copy is what the hooks.json command runs, so updating it changes the
    trusted hook's hash: Codex re-marks the hook for review, and a receipt written
    by the previous script no longer matches the installed script's digest.
    """
    body = HOOK_SOURCE.read_bytes()
    if script.exists():
        if script.is_symlink() or not script.is_file():
            raise ValueError(f"{script} is not a regular file")
        if script.read_bytes() == body:
            return "unchanged"
        write = "updated"
    else:
        write = "created"
    script.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".jasmine-p4-hook-", dir=script.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, script)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return write


def install_hooks(target: Path, old: bytes, new: bytes) -> None:
    body = new
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if old == body:
        return
    if old:
        backup = target.with_name("hooks.json.backup-"
                                   + hashlib.sha256(old).hexdigest())
        if not backup.exists():
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(old)
    fd, temporary = tempfile.mkstemp(prefix=".hooks-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render(repo: Path, python: Path, target: Path, old: bytes, new: bytes,
           script: Path, result: str, script_write: str | None = None) -> str:
    command = command_for(python, script)
    lines = [
        f"result: {result}",
        f"repo: {repo}",
        f"hooks_target: {target}",
        f"hook_script: {script}",
        f"hook_command: {command}",
        f"old_sha256: {hashlib.sha256(old).hexdigest()}",
        f"new_sha256: {hashlib.sha256(new).hexdigest()}",
        f"hook_source_sha256: {hashlib.sha256(HOOK_SOURCE.read_bytes()).hexdigest()}",
        "trust_changed: false",
    ]
    if script_write is not None:
        lines.append(f"hook_script_write: {script_write}")
        if script_write == "updated":
            lines.append(
                "warning: the installed hook script changed; Codex will re-mark the hook "
                "for review, and receipts written by the previous script hash no longer "
                "match the installed script.")
    lines += [
        "",
        "human trust steps (a human operator performs these; this tool never does):",
        "  1. launch Codex in the repository:",
        f"       cd {shlex.quote(str(repo))} && codex",
        "  2. open the hook browser in the Codex TUI:",
        "       /hooks",
        f"  3. review the SessionStart entry for {repo} and trust it. Codex records",
        "     trust against the hook's current hash, so a new or changed hook is",
        "     marked for review and skipped until trusted.",
        "  4. project-local hooks load only when the project .codex/ layer is trusted.",
        "     Trust is a human action: this tool never writes trust state, never",
        "     touches ~/.codex/config.toml or any trust store, and never uses",
        "     --dangerously-bypass-hook-trust.",
        "",
        "--probe: whether `codex exec` fires SessionStart is undocumented and must be",
        "  probed after trust, not assumed. After trusting the hook, run `codex exec`",
        f"  in {repo} and look for a new receipt at:",
        f"    {script.parent}/session-start-<session_id>.json",
        "  If `codex exec` does not fire SessionStart, use an interactive `codex`",
        "  session instead. Do not bypass trust to make the probe pass.",
        "",
        ("would write" if result.startswith("would") else "wrote")
        + f" {target}:",
        new.decode("utf-8"),
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path("."),
                        help="the repository Codex runs in (default: current directory)")
    parser.add_argument("--python", type=Path,
                        default=Path("/opt/homebrew/opt/python@3.13/bin/python3.13"),
                        help="the stable interpreter launcher that runs the hook script; "
                             "the launcher symlink is preserved, its parent must be "
                             "canonical")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true",
                      help="print the exact hooks.json and trust steps; change nothing "
                           "(default)")
    mode.add_argument("--apply", action="store_true",
                      help="write hooks.json and copy the hook script, idempotently")
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    try:
        target, old, new, script = plan(repo, args.python)
    except (OSError, ValueError) as exc:
        print(json.dumps({"result": "refused", "repo": str(repo),
                          "trust_changed": False, "reason": str(exc)}))
        return 1
    script_write = None
    result = "would-write" if old != new else "would-be-unchanged"
    if args.apply:
        try:
            script_write = install_script(script)
            install_hooks(target, old, new)
        except (OSError, ValueError) as exc:
            print(json.dumps({"result": "refused", "repo": str(repo),
                              "trust_changed": False, "reason": str(exc)}))
            return 1
        result = "written" if old != new or script_write != "unchanged" else "unchanged"
    print(render(repo, args.python, target, old, new, script, result, script_write))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
