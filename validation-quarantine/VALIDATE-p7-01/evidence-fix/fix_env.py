#!/usr/bin/env python3
"""FIX-REVALIDATION environment record for the f0aea88 validation.

Writes env-fix-environment.txt next to this script. Read-only over the real
run tree; never prints a token value.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import jasmine_core  # noqa: E402
from jasmine_core import canonical, db  # noqa: E402
from jasmine_core.migrations import check_version, migrate  # noqa: E402

HERE = Path(__file__).resolve().parent
WT = HERE.parents[2]
REAL = Path("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs")
SHA = "f0aea886186a6c1e4704eceb2552c74845f333b2"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=WT, capture_output=True,
                          text=True, check=True).stdout.strip()


def utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    lines: list[str] = []
    lines.append(f"# FIX-REVALIDATION environment record (VALIDATE-p7-01, candidate {SHA[:12]})")
    lines.append(f"candidate_sha: {git('rev-parse', 'HEAD')}")
    lines.append(f"branch: {git('rev-parse', '--abbrev-ref', 'HEAD')}")
    lines.append(f"worktree: {WT}")
    lines.append("captured_utc: "
                 + dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    status = [ln for ln in git("status", "--porcelain").splitlines()
              if not ln.startswith("?? validation-quarantine/")]
    lines.append(f"tracked_tree_status: {len(status)} modified tracked files"
                 + (f" -> {status}" if status else ""))
    lines.append(f"python: {sys.version.split()[0]} at {sys.executable}")
    lines.append(f"platform: {platform.platform()}")
    lines.append(f"real_root: {REAL} (read-only)")
    lines.append("modes_exercised: single_user and acl")
    lines.append("scope_sets_used (token values never recorded): "
                 "validation:read; validation:read+validation:unattributed; "
                 "admin+objects:read+objects:write (project creation only); "
                 "objects:read (deliberately scope-less key)")
    lines.append(f"core_module: {jasmine_core.__file__}")
    lines.append(f"core_version_attr: {jasmine_core.__version__}")
    lines.append(f"REDACT_TEXT_VERSION: {canonical.REDACT_TEXT_VERSION}")
    with tempfile.TemporaryDirectory(prefix="v7-env-") as tmp:
        conn = db.connect(Path(tmp) / "e.db")
        migrate(conn)
        lines.append("schema_version_after_migrate_check_version: "
                     f"{check_version(conn)} (check_version(); PRAGMA user_version is not "
                     "the schema counter)")
        conn.close()
    contract = json.loads((WT / "web" / "contracts" / "p7-validation-read.v1.json")
                          .read_text())
    lines.append(f"contract: {contract['contract']} source.ref: {contract['source']['ref']}")
    lines.append(f"contract_pin_is_ancestor_of_candidate: "
                 f"{subprocess.run(['git', 'merge-base', '--is-ancestor', contract['source']['ref'], SHA], cwd=WT).returncode == 0}")

    files = sorted(REAL.rglob("run-*.json"))
    lines.append(f"real_run_shaped_files: {len(files)}")
    for path in files:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        cases = data.get("cases", [])
        fail = sum(1 for c in cases if c.get("verdict") == "FAIL")
        fc = sum(1 for c in cases if c.get("failure_class") not in (None, ""))
        fo = sum(1 for c in cases if c.get("failure_output") not in (None, ""))
        lines.append(
            f"  {path.relative_to(REAL)} sha256={hashlib.sha256(raw).hexdigest()}"
            f" bytes={len(raw)} mtime_utc={utc(path.stat().st_mtime)}"
            f" stage={data.get('stage')} verdict={data.get('verdict')}"
            f" cases={len(cases)} fail_cases={fail} failure_class={fc}"
            f" failure_output={fo} synthetic={data.get('synthetic', False)}")
    lines.append("fixture_roots: per-run TemporaryDirectory(prefix='validate-p7-01-') "
                 "(validator-built); one short AF_UNIX root under /tmp (v7f-*)")
    text = "\n".join(lines) + "\n"
    (HERE / "env-fix-environment.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
