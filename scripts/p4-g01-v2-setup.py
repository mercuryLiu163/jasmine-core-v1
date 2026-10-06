#!/usr/bin/env python3
"""Prepare the pre-registered G01 v2 pair: two independent worktrees at the frozen ref.

The v2 specification requires each session to run in its own worktree so the
baseline's final diff exists before the lesson session can overwrite it. This
script creates both from the frozen fixture ref, installs the SessionStart hook
in each, verifies the operator config's frozen digests against the real files
and the recorded Codex version/model against the machine, and writes the input
manifest. It never writes Codex trust: the operator grants that per worktree
(`/hooks`), exactly as for the single-repo runs.

Exit codes: 0 prepared, 1 the frozen inputs do not match, 2 the script cannot be
used or the worktrees already exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_REL = "docs/p4-g01-v2-spec.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_codex() -> tuple[str, str]:
    codex = shutil.which("codex")
    if codex is None:
        raise SystemExit("codex is not on PATH; the pair cannot run without it")
    completed = subprocess.run([codex, "--version"], capture_output=True, text=True)
    version = completed.stdout.strip() or completed.stderr.strip()
    model = ""
    config = Path.home() / ".codex" / "config.toml"
    if config.is_file():
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        model = str(data.get("model") or "")
    return version, model


def prepare(repo: Path, ref: str, base: Path, work: Path, config_path: Path,
            prompt: Path, python_launcher: Path | None = None) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("g01_version") != "v2":
        raise SystemExit("the operator config does not select g01_version=v2")
    spec_sha = sha256(REPO_ROOT / SPEC_REL)
    if config.get("g01_v2_spec_sha256") != spec_sha:
        raise SystemExit(f"the config freezes spec {str(config.get('g01_v2_spec_sha256'))[:12]}, "
                         f"the file at {SPEC_REL} hashes to {spec_sha[:12]}")
    prompt_sha = sha256(prompt)
    if config.get("g01_v2_prompt_sha256") != prompt_sha:
        raise SystemExit(f"the config freezes prompt {str(config.get('g01_v2_prompt_sha256'))[:12]}, "
                         f"{prompt} hashes to {prompt_sha[:12]}")
    if config.get("g01_v2_fixture_ref") != ref:
        raise SystemExit(f"the config freezes fixture {config.get('g01_v2_fixture_ref')!r}, "
                         f"the argument names {ref!r}")
    actual_version, actual_model = collect_codex()
    recorded_version = str(config.get("g01_v2_codex_version") or "")
    recorded_model = str(config.get("g01_v2_codex_model") or "")
    if not recorded_version or recorded_version not in actual_version:
        raise SystemExit(f"the config records Codex {recorded_version!r}, this machine "
                         f"reports {actual_version!r}")
    if actual_model and recorded_model != actual_model:
        raise SystemExit(f"the config records model {recorded_model!r}, this machine's "
                         f"Codex config names {actual_model!r}")
    if not recorded_model:
        raise SystemExit("the config records no g01_v2_codex_model")
    worktrees = {}
    for label in ("baseline", "lesson"):
        path = base / f"g01-v2-{label}"
        if path.exists():
            raise SystemExit(f"{path} already exists; a pre-registered pair is not prepared twice")
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach",
                        str(path), ref], check=True, capture_output=True)
        # The hook launcher must be a canonical path: the installer preserves the
        # venv launcher symlink, and /var -> /private/var makes a TMPDIR-shaped
        # path fail its check. The run's interpreter is passed explicitly.
        launcher = python_launcher or Path(sys.executable)
        command = [sys.executable, str(REPO_ROOT / "scripts" / "p4-codex-hook-install.py"),
                   "--repo", str(path), "--python", str(launcher), "--apply"]
        installed = subprocess.run(command, capture_output=True, text=True)
        if installed.returncode != 0:
            raise SystemExit(f"hook install failed for {path}: "
                             f"{installed.stderr.strip() or installed.stdout.strip()}")
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        if head != ref:
            raise SystemExit(f"{path} is at {head}, not the frozen {ref}")
        worktrees[label] = {"path": str(path), "hook_sha256": sha256(
            path / ".codex" / "jasmine" / "jasmine-p4-session-start.py")}
    inputs = {
        "spec_sha256": spec_sha,
        "prompt_sha256": prompt_sha,
        "prompt_path": str(prompt),
        "fixture_repo": str(repo),
        "fixture_ref": ref,
        "codex_version": recorded_version,
        "codex_model": recorded_model,
        "worktrees": worktrees,
        "trust": "operator must run /hooks in each worktree and trust the project hook; "
                 "the native SessionStart receipt is the proof it fired",
    }
    target = work / "g01-v2-inputs.json"
    if target.exists():
        raise SystemExit(f"{target} already exists; refusing to overwrite frozen inputs")
    target.write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8")
    return inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--prompt", required=True, type=Path)
    args = parser.parse_args()
    inputs = prepare(args.repo.resolve(), args.ref, args.base, args.work,
                     args.config, args.prompt.resolve())
    print(json.dumps(inputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
