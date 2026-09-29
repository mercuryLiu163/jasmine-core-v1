#!/usr/bin/env python3
"""P1-T10 real Codex conversation Gate entry and conservative preflight.

This initial entry only prepares and validates a concrete Gate run. It never
installs/trusts hooks, starts Core, or runs a synthetic conversation. Until
real `codex exec` and explicit-session `codex exec resume` traces have been
captured and checked, it records BLOCKED and exits 2. A failure in an actual
Gate assertion must be recorded as FAIL (exit 1), never BLOCKED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ID_SHAPE = re.compile(r"^[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")


class Blocked(Exception):
    pass


class Failed(Exception):
    pass


def _same(a: str | Path, b: str | Path) -> bool:
    return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()


def _private_file(path: Path, label: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise Blocked(f"{label} is absent or unreadable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Blocked(f"{label} must be an owned, non-symlink regular file with mode 0600")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Blocked(f"{label} is unreadable") from exc


def _binding(path: Path, gate_nonce: str) -> dict:
    try:
        value = json.loads(_private_file(path, "binding"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Blocked("binding is not valid JSON") from exc
    if not isinstance(value, dict):
        raise Blocked("binding must be a JSON object")
    for name, prefix in (("project_id", "prj"), ("task_id", "tsk"),
                         ("step_id", "stp"), ("host_id", "hst")):
        item = value.get(name)
        if not isinstance(item, str) or not ID_SHAPE.fullmatch(item) or not item.startswith(prefix + "_"):
            raise Blocked(f"binding.{name} is malformed")
    if value.get("run_nonce") != gate_nonce or not gate_nonce:
        raise Blocked("binding nonce does not match private Gate environment")
    if value.get("codex_session_id") is not None:
        raise Blocked("fresh Gate binding must be unclaimed before first real prompt")
    for name in ("core_url", "token_file", "human_token_file"):
        if not isinstance(value.get(name), str) or not value[name]:
            raise Blocked(f"binding.{name} is required")
    lease = Path(str(path) + ".lease")
    if lease.exists():
        raise Blocked("fresh Gate binding already has a session lease")
    return value


def _owned_endpoint(url: str, expected_pid: int) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise Blocked("Core URL must be loopback HTTP")
    port = parsed.port
    if port is None:
        raise Blocked("Core URL must name a port")
    if not shutil.which("lsof"):
        raise Blocked("lsof is needed to prove Core endpoint ownership")
    proc = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                          capture_output=True, text=True, timeout=5)
    owners = {int(line) for line in proc.stdout.splitlines() if line.isdigit()}
    if proc.returncode != 0 or owners != {expected_pid}:
        raise Blocked("Core endpoint is not exclusively owned by the expected PID")
    with socket.create_connection((parsed.hostname, port), timeout=1):
        pass
    return {"host": parsed.hostname, "port": port, "owner_pid": expected_pid}


def _token_identity(conn: sqlite3.Connection, token_file: Path, *, kind: str,
                    required_scopes: set[str]) -> dict:
    token = _private_file(token_file, f"{kind} token file").decode("utf-8").strip()
    if not token:
        raise Blocked(f"{kind} token file is empty")
    row = conn.execute("SELECT a.actor_id,a.kind,k.scopes,k.revoked_at FROM api_keys k "
                       "JOIN actors a ON a.actor_id=k.actor_id WHERE k.token_sha256=?",
                       (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if row is None or row[3] is not None or row[1] != kind:
        raise Blocked(f"{kind} token does not resolve to an active {kind} actor")
    scopes = set(json.loads(row[2]))
    if not required_scopes <= scopes:
        raise Blocked(f"{kind} token lacks required Gate scopes")
    return {"actor_id": row[0], "kind": row[1], "required_scopes": sorted(required_scopes)}


def _db_binding(db_path: Path, binding: dict) -> dict:
    if not db_path.is_file():
        raise Blocked("Gate Core database is absent")
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        try:
            version = conn.execute("SELECT value FROM core_meta WHERE key='schema_version'").fetchone()
            if version is None or int(version[0]) != 5:
                raise Blocked("Gate Core database is not schema version 5")
            row = conn.execute("SELECT t.project_id,s.task_id FROM tasks t JOIN steps s "
                               "ON s.task_id=t.task_id WHERE t.task_id=? AND s.step_id=?",
                               (binding["task_id"], binding["step_id"])).fetchone()
            if row is None or row != (binding["project_id"], binding["task_id"]):
                raise Blocked("binding Task/Step/Project do not resolve together")
            host = conn.execute("SELECT 1 FROM hosts WHERE host_id=?",
                                (binding["host_id"],)).fetchone()
            if host is None:
                raise Blocked("binding Host is absent from Core database")
            system = _token_identity(conn, Path(binding["token_file"]), kind="system",
                                     required_scopes={"evidence:write", "guard:check"})
            human = _token_identity(conn, Path(binding["human_token_file"]), kind="human",
                                    required_scopes={"events:write", "evidence:confirm"})
            seq = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            return {"schema_version": 5, "event_seq_before": int(seq),
                    "system_actor_id": system["actor_id"], "human_actor_id": human["actor_id"]}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise Blocked("Gate Core database could not be read") from exc


def _hook_config(path: Path, binding_path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise Blocked("project-local hook config is absent or symlinked")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blocked("project-local hook config is unreadable") from exc
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        raise Blocked("hook config lacks hooks object")
    seen: dict[str, int] = {}
    for name in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
        entries = hooks.get(name, [])
        if not isinstance(entries, list):
            raise Blocked(f"{name} hook config is malformed")
        matching = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for item in entry.get("hooks", []):
                if not isinstance(item, dict) or item.get("type") != "command":
                    continue
                command = item.get("command")
                if isinstance(command, str) and str(binding_path.resolve()) in command:
                    matching += 1
        seen[name] = matching
        if matching < 1:
            raise Blocked(f"no {name} command bound to this Gate binding")
    return seen


def _git() -> dict:
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True)
    return {"commit": commit, "dirty": bool(dirty.strip()),
            "dirty_paths": dirty.splitlines(), "python": platform.python_version()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="new directory for Gate record")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--hook-config", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--core-pid", type=int, required=True)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--user-reviewed-trust", action="store_true",
                        help="operator confirms project hook review; runtime still must prove behavior")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        parser.error("--out must be a new directory; old failure records are append-only")
    out.mkdir(parents=True, mode=0o700)
    record: dict = {"case_id": "P1-T10", "verdict": "BLOCKED", "phase": "preflight",
                    "provenance": _git(), "checks": []}
    try:
        if record["provenance"]["dirty"]:
            raise Blocked("checkout is dirty; freeze a candidate commit")
        if not shutil.which(args.codex_bin):
            raise Blocked("codex executable is unavailable")
        nonce = os.environ.get("JASMINE_CORE_GATE_NONCE", "")
        if not nonce or len(nonce) < 16:
            raise Blocked("private JASMINE_CORE_GATE_NONCE is missing or too short")
        project = Path(args.project_root).resolve(strict=True)
        binding_path = Path(args.binding).expanduser().absolute()
        binding = _binding(binding_path, nonce)
        record["checks"].append("binding and nonce match")
        endpoint = _owned_endpoint(binding["core_url"], args.core_pid)
        record["checks"].append({"endpoint": endpoint})
        record["checks"].append(_db_binding(Path(args.db), binding))
        hook_path = Path(args.hook_config).expanduser().absolute()
        if not hook_path.is_relative_to(project):
            raise Blocked("hook config is outside the target project")
        record["checks"].append({"bound_hooks": _hook_config(hook_path, binding_path)})
        if not args.user_reviewed_trust:
            raise Blocked("user has not yet reviewed and trusted project hooks")
        record["checks"].append("user reported project hook trust review complete")
        # Runtime phase will be added after the exact installer and native hook
        # payload contract is frozen. Never call this a PASS based on preflight.
        raise Blocked("real codex exec/resume and tool traces are not yet captured")
    except Blocked as exc:
        record["reason"] = str(exc)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record["reason"] = f"preflight could not establish a requirement: {type(exc).__name__}"
    path = out / f"p1-t10-{int(time.time())}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    print(json.dumps({"record": str(path), "verdict": record["verdict"],
                      "reason": record.get("reason")}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
