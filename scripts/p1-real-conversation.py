#!/usr/bin/env python3
"""P1-T10: prepare a private fixture, then verify a real Codex conversation.

``--prepare`` creates a scratch Core database, Task, Step, Rules, and private
binding. It starts and stops its own Core only to bootstrap through HTTP. It
does not install hooks, alter trust, or launch Codex. After an operator installs
and reviews the project-local hooks, ``--run`` starts an owned Core process and
checks real exec/resume turns, hook traces, State, Evidence, and file effects.
Fixture API calls and mocked subprocesses never count as a T10 PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jasmine_core import SCHEMA_VERSION, auth, db, ids, registry  # noqa: E402
from jasmine_core.api.client import ApiError, CoreClient  # noqa: E402
from jasmine_core.canonical import canonical_json  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402

ID_SHAPE = re.compile(r"^[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")
PROMPT_EXECUTED = ("Read the Jasmine hook context. Report the Step as "
                   "`STEP status=<value> revision=<number>` using the exact values you see. Do not call any tool.")
PROMPT_VERIFIED = ("Read the Jasmine hook context and report the exact Step status and revision. "
                   "Use `STEP status=<value> revision=<number>`. Then use Bash to run exactly "
                   "`{stale_command}`. This changes the scratch workspace. "
                   "Do not use any other tool.")
PROMPT_STEP_CONFIRM = ("I confirm the current Step at the revision shown in the Jasmine hook context. "
                       "Report `STEP status=<value> revision=<number>`; do not call any tool.")
PROMPT_TASK_CONFIRM = ("I confirm the current Task at the revision shown in the Jasmine hook context. "
                       "Report `TASK status=<value> revision=<number>`; do not call any tool.")


class Blocked(Exception):
    """A required external runtime or trust condition has not been established."""


class Failed(Exception):
    """A real Gate assertion failed after the runtime was available."""


def _write_private(path: Path, data: str | dict[str, Any]) -> None:
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(raw)


def _private_file(path: Path, label: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise Blocked(f"{label} is absent or unreadable") from exc
    parent = path.parent.stat()
    if (path != path.resolve() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
        raise Blocked(f"{label} must be an owned regular file with mode 0600")
    return path.read_bytes()


def _git() -> dict[str, Any]:
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True)
    return {"commit": commit, "dirty": bool(dirty.strip()), "dirty_paths": dirty.splitlines(),
            "python": platform.python_version()}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_owners(port: int) -> set[int]:
    if not shutil.which("lsof"):
        raise Blocked("lsof is needed to prove Core endpoint ownership")
    result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                            capture_output=True, text=True, timeout=5)
    if result.returncode not in (0, 1):
        raise Blocked("lsof could not inspect Core port")
    return {int(line) for line in result.stdout.splitlines() if line.isdecimal()}


def _owned_endpoint(url: str, pid: int) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path not in ("", "/"):
        raise Blocked("Core URL must be exact loopback HTTP")
    if parsed.port is None or _port_owners(parsed.port) != {pid}:
        raise Blocked("Core endpoint is not exclusively owned by the expected PID")
    with socket.create_connection(("127.0.0.1", parsed.port), timeout=1):
        pass
    return {"port": parsed.port, "owner_pid": pid}


def _start_core(db_path: Path, workspace: Path, port: int, out: Path) -> subprocess.Popen[bytes]:
    if _port_owners(port):
        raise Blocked("selected Core port is already occupied")
    log = (out / "api.log").open("ab")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
               JASMINE_CORE_WORKSPACE_ROOT=str(workspace))
    proc = subprocess.Popen([sys.executable, "-m", "jasmine_core.cli", "serve", "--db",
                             str(db_path), "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    log.close()
    try:
        for _ in range(80):
            if proc.poll() is not None:
                raise Blocked(f"owned Core exited before readiness; see {out/'api.log'}")
            try:
                _owned_endpoint(f"http://127.0.0.1:{port}", proc.pid)
                if CoreClient(f"http://127.0.0.1:{port}").get("/v1/health").get("status") == "ok":
                    return proc
            except (Blocked, OSError, ApiError):
                pass
            time.sleep(0.1)
        raise Blocked(f"owned Core did not become healthy; see {out/'api.log'}")
    except BaseException:
        _stop_core(proc, port)
        raise


def _stop_core(proc: subprocess.Popen[bytes] | None, port: int) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    for _ in range(30):
        if not _port_owners(port):
            return
        time.sleep(0.1)
    raise Failed("owned Core port remained open after shutdown")


def _api(client: CoreClient, method: str, path: str, body: dict | None = None) -> dict:
    try:
        return client.request(method, path, body)
    except ApiError as exc:
        raise Failed(f"{method} {path} returned HTTP {exc.status} {exc.code}") from exc


def _binding(path: Path, nonce: str) -> dict[str, Any]:
    try:
        value = json.loads(_private_file(path, "binding"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Blocked("binding is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("run_nonce") != nonce or len(nonce) < 32:
        raise Blocked("binding nonce does not match private Gate environment")
    if set(value) - {"run_nonce", "project_id", "task_id", "step_id", "host_id", "core_url",
                      "token_file", "human_token_file", "trace_file", "producer_config"}:
        raise Blocked("binding has unknown fields")
    for name, prefix in (("project_id", "prj"), ("task_id", "tsk"), ("step_id", "stp"), ("host_id", "hst")):
        item = value.get(name)
        if not isinstance(item, str) or not ID_SHAPE.fullmatch(item) or not item.startswith(prefix + "_"):
            raise Blocked(f"binding.{name} is malformed")
    for name in ("core_url", "token_file", "human_token_file", "trace_file"):
        if not isinstance(value.get(name), str) or not value[name]:
            raise Blocked(f"binding.{name} is required")
    if Path(str(path) + ".lease").exists():
        raise Blocked("fresh Gate binding already has a session lease")
    return value


def _token_identity(conn: sqlite3.Connection, path: Path, *, kind: str,
                    scopes_required: set[str]) -> dict[str, Any]:
    token = _private_file(path, f"{kind} token").decode().strip()
    row = conn.execute("SELECT a.actor_id,a.kind,k.scopes,k.revoked_at FROM api_keys k "
                       "JOIN actors a ON a.actor_id=k.actor_id WHERE k.token_sha256=?",
                       (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if row is None or row[1] != kind or row[3] is not None:
        raise Blocked(f"{kind} token is not active for the expected actor kind")
    if not scopes_required <= set(json.loads(row[2])):
        raise Blocked(f"{kind} token lacks required Gate scopes")
    return {"actor_id": row[0], "kind": kind}


def _db_binding(path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        raise Blocked("Gate Core database is absent")
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            version = conn.execute("SELECT value FROM core_meta WHERE key='schema_version'").fetchone()
            if version is None or int(version[0]) != SCHEMA_VERSION:
                raise Blocked("Gate Core schema does not match this code")
            row = conn.execute("SELECT t.project_id,s.task_id FROM tasks t JOIN steps s "
                               "ON s.task_id=t.task_id WHERE t.task_id=? AND s.step_id=?",
                               (binding["task_id"], binding["step_id"])).fetchone()
            if row != (binding["project_id"], binding["task_id"]):
                raise Blocked("binding Task/Step/Project do not resolve together")
            if conn.execute("SELECT 1 FROM hosts WHERE host_id=?", (binding["host_id"],)).fetchone() is None:
                raise Blocked("binding Host is absent")
            system = _token_identity(conn, Path(binding["token_file"]), kind="system",
                scopes_required={"guard:check", "evidence:write", "fingerprint:scan",
                                 "objects:read", "state:read", "authority:read"})
            human = _token_identity(conn, Path(binding["human_token_file"]), kind="human",
                scopes_required={"events:write", "evidence:confirm", "state:read", "authority:read"})
            return {"schema_version": SCHEMA_VERSION,
                    "event_seq_before": int(conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]),
                    "system_actor_id": system["actor_id"], "human_actor_id": human["actor_id"]}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise Blocked("Gate Core database could not be read") from exc


def _hook_config(path: Path, root: Path, binding: Path) -> dict[str, str]:
    expected = root / ".codex" / "hooks.json"
    if path != expected or not path.is_file() or path.is_symlink():
        raise Blocked("Codex hook config must be exact project .codex/hooks.json")
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Blocked("project hook config is unreadable") from exc
    hooks = config.get("hooks") if isinstance(config, dict) else None
    if not isinstance(hooks, dict):
        raise Blocked("project hook config has no hooks object")
    wrapper = root / "scripts" / "jasmine-p1-hook.sh"
    found: dict[str, str] = {}
    for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
        matches: list[str] = []
        for entry in hooks.get(event, []):
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                continue
            if event != "UserPromptSubmit" and entry.get("matcher") != "^Bash$":
                continue
            for hook in entry["hooks"]:
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    continue
                try:
                    words = shlex.split(hook.get("command", ""))
                except ValueError:
                    continue
                if (len(words) == 5 and Path(words[0]).resolve() == wrapper.resolve() and
                    words[1] == "--python" and Path(words[2]).resolve() == Path(sys.executable).resolve() and
                    words[3] == "--binding" and Path(words[4]).resolve() == binding.resolve()):
                    matches.append(hook["command"])
        if len(matches) != 1:
            raise Blocked(f"expected exactly one real {event} P1 wrapper entry")
        found[event] = hashlib.sha256(matches[0].encode()).hexdigest()
    return found


def _record(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _prepare(args: argparse.Namespace, report: dict[str, Any]) -> None:
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        raise Blocked("--prepare requires a new output directory; prior records are append-only")
    out.mkdir(mode=0o700, parents=True)
    os.chmod(out, 0o700)
    project_root = Path(args.project_root).expanduser().resolve(strict=True)
    workspace = out / "workspace"
    workspace.mkdir(mode=0o700)
    (workspace / "source.txt").write_text("original\n", encoding="utf-8")
    for command in (["git", "init", "-q", str(workspace)],
                    ["git", "-C", str(workspace), "add", "source.txt"],
                    ["git", "-C", str(workspace), "-c", "user.name=P1 Gate",
                     "-c", "user.email=p1-gate@local.invalid", "commit", "-qm", "initial fixture"]):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise Blocked("scratch Git workspace could not be initialized")
    db_path = out / "core.db"
    host, human, system, agent = [ids.new_id("hst" if index == 0 else "act") for index in range(4)]
    conn = db.connect(db_path)
    try:
        migrate(conn)
        with db.transaction(conn):
            reg = registry.Registry(conn)
            reg.upsert_host(host)
            for actor, kind in ((human, "human"), (system, "system"), (agent, "agent")):
                reg.upsert_actor(actor, kind=kind, home_host_id=host)
        issuer = auth.Auth(conn)
        tokens = {
            "human": issuer.issue_key(actor_id=human, label="p1-gate-human", scopes=[
                "objects:read", "objects:write", "events:read", "events:write",
                "authority:read", "authority:propose", "authority:manage",
                "state:read", "state:write", "state:accept", "evidence:read",
                "evidence:confirm", "fingerprint:read"])["token"],
            "system": issuer.issue_key(actor_id=system, label="p1-gate-system", scopes=[
                "guard:check", "evidence:write", "evidence:read", "fingerprint:scan",
                "fingerprint:read", "objects:read", "state:read", "authority:read"])["token"],
            "agent": issuer.issue_key(actor_id=agent, label="p1-gate-agent", scopes=[
                "state:read", "state:write", "events:write", "authority:propose"])["token"],
        }
    finally:
        conn.close()
    for role, token in tokens.items():
        _write_private(out / f"{role}.token", token + "\n")
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    core: subprocess.Popen[bytes] | None = None
    try:
        core = _start_core(db_path, workspace, port, out)
        h = CoreClient(url, tokens["human"])
        project = _api(h, "POST", "/v1/projects", {"name": "p1-t10-real-gate", "host_id": host})["object"]["project_id"]
        task_criteria = {"requirements": [{"key": "human-task", "kind": "USER_CONFIRMATION",
                                            "required_result": "INFO"}]}
        allowed = workspace / "allowed.txt"
        denied = workspace / "denied.txt"
        stale = workspace / "stale.txt"
        command = f"touch {shlex.quote(str(allowed))}"
        step_criteria = {"requirements": [{"key": "real-tool", "kind": "COMMAND_RESULT",
            "required_result": "PASS", "tool_name": "Bash",
            "command_sha256": hashlib.sha256(command.encode()).hexdigest()}]}
        task = _api(h, "POST", "/v1/tasks", {"title": "P1 real Gate", "host_id": host,
            "project_id": project, "acceptance_criteria": task_criteria})["object"]["task_id"]
        step = _api(h, "POST", f"/v1/tasks/{task}/steps", {"title": "Run actual safe Bash tool",
            "host_id": host, "expected_revision": 1, "acceptance_criteria": step_criteria})["step"]["step_id"]
        proposing_agent = CoreClient(url, tokens["agent"])
        origin = _api(proposing_agent, "POST", "/v1/events", {"event_type": "assistant.message",
            "source_system": "p1-gate-fixture-only", "host_id": host, "project_id": project,
            "task_id": task, "payload": {"text": "Synthetic Rule proposal fixture; no human quote"}})["event"]["event_id"]
        scope = {"kind": "task", "project_id": project, "task_id": task}
        rules = []
        for key, enforcement, path, req in (
            ("gate-denied", "DENY", denied, None),
            ("gate-verify", "VERIFY", allowed, step_criteria),
        ):
            proposal = {"rule_key": key, "kind": "RULE", "severity": "HARD",
                "enforcement": enforcement, "content": f"P1 Gate {key}",
                "matcher": {"tool": "Bash", "action": "touch", "path_prefix": str(path)},
                "scope": scope, "origin_event_id": origin, "host_id": host}
            if req is not None:
                proposal["verification_requirements"] = req
            rule = _api(proposing_agent, "POST", "/v1/rules/proposals", proposal)["rule"]
            active = _api(h, "POST", f"/v1/rules/{rule['rule_id']}/approve",
                          {"host_id": host, "expected_revision": 1})["rule"]
            rules.append({"rule_id": active["rule_id"], "version": active["version"],
                          "enforcement": enforcement})
    finally:
        _stop_core(core, port)
    nonce = secrets.token_urlsafe(36)
    binding = out / "binding.json"
    _write_private(binding, {"run_nonce": nonce, "project_id": project, "task_id": task,
        "step_id": step, "host_id": host, "core_url": url,
        "token_file": str(out / "system.token"), "human_token_file": str(out / "human.token"),
        "trace_file": str(out / "hook-trace.jsonl")})
    manifest = {"prepared_commit": report["provenance"]["commit"], "project_root": str(project_root),
        "workspace": str(workspace), "db": str(db_path), "binding": str(binding),
        "hook_config": str(project_root / ".codex" / "hooks.json"), "port": port,
        "project_id": project, "task_id": task, "step_id": step,
        "host_id": host, "human_actor_id": human, "system_actor_id": system,
        "denied": str(denied), "allowed": str(allowed), "stale": str(stale),
        "denied_command": f"touch {shlex.quote(str(denied))}",
        "allowed_command": command,
        "stale_command": f"touch {shlex.quote(str(stale))}", "rules": rules}
    _write_private(out / "manifest.json", manifest)
    installer = [sys.executable, str(project_root / "scripts" / "p1-codex-hook-install.py"),
                 "--binding", str(binding), "--project-root", str(project_root),
                 "--hooks-json", manifest["hook_config"], "--python", sys.executable]
    report.update(phase="prepared", verdict="BLOCKED", manifest=str(out / "manifest.json"),
                  reason="operator must review/install project hooks and project trust before --run",
                  installer_dry_run=installer + ["--dry-run"], installer_install=installer,
                  run_command=[sys.executable, str(Path(__file__).resolve()), "--run", "--out",
                               str(out), "--user-reviewed-trust"],
                  nonce_instruction="Set JASMINE_CORE_GATE_NONCE privately to binding.json run_nonce; never paste it into a Codex prompt.")


def _read_json_private(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_private_file(path, label))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Blocked(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise Blocked(f"{label} must be an object")
    return value


def _traces(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = _private_file(path, "hook trace")
    try:
        values = [json.loads(line) for line in raw.splitlines() if line]
    except ValueError as exc:
        raise Failed("hook trace contains malformed JSON") from exc
    if any(not isinstance(value, dict) for value in values):
        raise Failed("hook trace entry is not an object")
    return values


def _session(stdout: str) -> str:
    sessions = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("type") == "thread.started" and isinstance(value.get("thread_id"), str):
            sessions.append(value["thread_id"])
    if len(sessions) != 1:
        raise Failed(f"Codex JSONL exposed {len(sessions)} new session IDs")
    return sessions[0]


def _messages(stdout: str) -> str:
    parts = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        item = value.get("item") if isinstance(value, dict) else None
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _tool_items(stdout: str) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        item = value.get("item") if isinstance(value, dict) else None
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind not in {"command_execution", "tool_call", "function_call", "mcp_tool_call", "file_change"}:
            continue
        key = str(item.get("id", len(calls)))
        calls[key] = {"id": key, "type": kind, "command": item.get("command")}
    return list(calls.values())


def _real_turn(args: argparse.Namespace, out: Path, manifest: dict[str, Any],
               name: str, prompt: str, session: str | None, nonce: str,
               report: dict[str, Any], *, expected_state: tuple[str, str, int] | None = None,
               allowed_commands: tuple[str, ...] = ()) -> tuple[str, dict[str, Any]]:
    if session is None:
        command = [args.codex_bin, "exec", "--json", "--skip-git-repo-check",
                   "--sandbox", "workspace-write", "--add-dir", manifest["workspace"], prompt]
    else:
        command = [args.codex_bin, "exec", "resume", "--json", "--skip-git-repo-check", session, prompt]
    before = len(_traces(out / "hook-trace.jsonl"))
    env = dict(os.environ, JASMINE_CORE_GATE_NONCE=nonce)
    stdout_path, stderr_path = out / f"{name}-codex.jsonl", out / f"{name}-stderr.txt"
    timed_out = False
    try:
        completed = subprocess.run(command, cwd=manifest["project_root"], env=env,
                                   capture_output=True, text=True, timeout=args.codex_timeout)
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        exit_code = None
    _write_private(stdout_path, stdout)
    _write_private(stderr_path, stderr)
    report.setdefault("turns", []).append({"name": name, "command": command[:-1] + ["<prompt>"],
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "stdout": str(stdout_path), "stderr": str(stderr_path), "exit": exit_code,
        "timed_out": timed_out})
    recent = _traces(out / "hook-trace.jsonl")[before:]
    report["turns"][-1]["hook_traces"] = recent
    captured_prompts = [entry for entry in recent if entry.get("hook_event_name") == "UserPromptSubmit"
                        and entry.get("result") == "captured"]
    if not captured_prompts:
        if recent:
            raise Failed(f"{name} real hook ran but did not capture the UserPromptSubmit")
        raise Blocked(f"{name} has no real hook invocation; hook load or trust is unproven")
    tool_items = _tool_items(stdout)
    report["turns"][-1]["codex_tool_items"] = tool_items
    if any(item["type"] != "command_execution" or item["command"] not in allowed_commands
           for item in tool_items):
        raise Failed(f"{name} Codex JSONL contains an unexpected tool call")
    allowed_inputs = {hashlib.sha256(canonical_json({"command": command}).encode()).hexdigest()
                      for command in allowed_commands}
    if any(entry.get("hook_event_name") in ("PreToolUse", "PostToolUse") and
           entry.get("tool_input_sha256") not in allowed_inputs for entry in recent):
        raise Failed(f"{name} Hook trace contains an unexpected Bash command")
    if timed_out:
        raise Failed(f"{name} timed out after a real hook invocation")
    if exit_code:
        raise Failed(f"{name} codex exec exited {exit_code} after a real hook invocation")
    actual = _session(stdout)
    if session is not None and actual != session:
        raise Failed(f"{name} resume changed explicit session")
    try:
        lease = _read_json_private(Path(manifest["binding"] + ".lease"), "session lease")
    except Blocked as exc:
        raise Failed("real hook ran without a valid bound session lease") from exc
    if lease.get("session_id") != actual or lease.get("run_nonce_sha256") != hashlib.sha256(nonce.encode()).hexdigest():
        raise Failed("real hook lease does not bind this Codex session and nonce")
    prompts = [entry for entry in recent if entry.get("hook_event_name") == "UserPromptSubmit" and
               entry.get("session_id") == actual and entry.get("result") == "captured" and
               isinstance(entry.get("event_id"), str)]
    if len(prompts) != 1:
        raise Failed(f"{name} has {len(prompts)} captured same-session UserPromptSubmit traces")
    human = CoreClient(manifest["core_url"], _private_file(out / "human.token", "human token").decode().strip())
    origin = _api(human, "GET", f"/v1/events/{prompts[0]['event_id']}")["event"]
    if (origin.get("event_type") != "user.prompt" or origin.get("source_system") != "codex-p1-bound" or
        origin.get("actor_kind") != "human" or origin.get("actor_id") != manifest["human_actor_id"] or
        origin.get("project_id") != manifest["project_id"] or origin.get("task_id") != manifest["task_id"] or
        origin.get("payload", {}).get("text") != prompt or origin.get("payload", {}).get("source_session_id") != actual or
        origin.get("payload", {}).get("turn_id") != prompts[0].get("turn_id")):
        raise Failed(f"{name} Event does not match this real Codex prompt")
    answer = _messages(stdout)
    if expected_state:
        entity, status, revision = expected_state
        pattern = rf"\b{re.escape(entity)} status={re.escape(status)} revision={revision}\b"
        if re.search(pattern, answer) is None:
            raise Failed(f"{name} Agent did not report exact current {entity} status and revision")
    report["turns"][-1].update(session_id=actual, origin_event_id=origin["event_id"],
                                hook_traces=recent, agent_message=answer[-1500:])
    return actual, origin


def _state(client: CoreClient, task: str, step: str) -> tuple[dict, dict]:
    return (_api(client, "GET", f"/v1/tasks/{task}")["task"],
            _api(client, "GET", f"/v1/steps/{step}")["step"])


def _transition(client: CoreClient, manifest: dict, status: str) -> dict:
    _, step = _state(client, manifest["task_id"], manifest["step_id"])
    return _api(client, "POST", f"/v1/steps/{manifest['step_id']}/transition",
                {"status": status, "host_id": manifest["host_id"],
                 "expected_revision": step["revision"],
                 **({"reason": "real workspace changed"} if step["status"] == "STALE" else {})})


def _evidence(client: CoreClient, evidence_ids: list[str], manifest: dict) -> list[dict]:
    values = [_api(client, "GET", f"/v1/evidence/{evidence_id}")["evidence"]
              for evidence_id in evidence_ids]
    if not values or any(item["task_id"] != manifest["task_id"] for item in values):
        raise Failed("State validation lacks same-task immutable Evidence references")
    return values


def _captured_tool_event(client: CoreClient, manifest: dict[str, Any],
                         turn: dict[str, Any], command: str,
                         allowed_decisions: set[str]) -> dict[str, Any]:
    expected_hash = hashlib.sha256(canonical_json({"command": command}).encode()).hexdigest()
    prompt_traces = [entry for entry in turn["hook_traces"] if
                     entry.get("hook_event_name") == "UserPromptSubmit" and
                     entry.get("result") == "captured" and
                     entry.get("session_id") == turn["session_id"]]
    if len(prompt_traces) != 1:
        raise Failed("tool turn has no unique same-session UserPromptSubmit trace")
    turn_id = prompt_traces[0].get("turn_id")
    pre = [entry for entry in turn["hook_traces"] if
           entry.get("hook_event_name") == "PreToolUse" and
           entry.get("result") in allowed_decisions and
           entry.get("tool_input_sha256") == expected_hash and
           entry.get("session_id") == turn["session_id"] and
           entry.get("turn_id") == turn_id and entry.get("tool_use_id")]
    if len(pre) != 1:
        raise Failed("allowed tool lacks one matching real PreToolUse decision")
    post = [entry for entry in turn["hook_traces"] if
            entry.get("hook_event_name") == "PostToolUse" and entry.get("result") == "captured" and
            entry.get("tool_input_sha256") == expected_hash and
            all(entry.get(key) == pre[0].get(key) for key in
                ("session_id", "turn_id", "tool_use_id")) and isinstance(entry.get("event_id"), str)]
    if len(post) != 1:
        raise Failed("allowed tool lacks one matching real PostToolUse capture")
    event = _api(client, "GET", f"/v1/events/{post[0]['event_id']}")["event"]
    payload = event.get("payload", {})
    if (event.get("event_type") != "tool.result" or event.get("actor_kind") != "system" or
        event.get("actor_id") != manifest["system_actor_id"] or
        event.get("task_id") != manifest["task_id"] or event.get("project_id") != manifest["project_id"] or
        payload.get("codex_session_id") != turn["session_id"] or payload.get("turn_id") != turn_id or
        payload.get("tool_use_id") != pre[0]["tool_use_id"] or payload.get("tool_name") != "Bash" or
        not isinstance(payload.get("call_event_id"), str)):
        raise Failed("stored tool.result does not match the real bound Bash call")
    call = _api(client, "GET", f"/v1/events/{payload['call_event_id']}")["event"]
    call_payload = call.get("payload", {})
    if (call.get("event_type") != "tool.call" or call.get("actor_kind") != "system" or
        call.get("actor_id") != event["actor_id"] or call.get("task_id") != manifest["task_id"] or
        call.get("project_id") != manifest["project_id"] or
        call_payload.get("codex_session_id") != turn["session_id"] or
        call_payload.get("turn_id") != turn_id or
        call_payload.get("tool_use_id") != pre[0]["tool_use_id"] or
        call_payload.get("tool_name") != "Bash" or
        call_payload.get("tool_input", {}).get("command") != command or
        hashlib.sha256(canonical_json(call_payload.get("tool_input")).encode()).hexdigest() != expected_hash):
        raise Failed("linked tool.call does not match the real Bash command and session")
    return event


def _hook_probe(manifest: dict[str, Any], session: str, nonce: str,
                *, bound: bool) -> dict[str, Any]:
    """Direct integration probe after Core stop; it is not real Codex Evidence."""
    payload = {"hook_event_name": "PreToolUse", "session_id": session if bound else "unbound-other-session",
               "turn_id": "gate-offline-probe", "tool_use_id": "gate-offline-probe",
               "tool_name": "Bash", "tool_input": {"command": "true"}}
    env = dict(os.environ)
    if bound:
        env["JASMINE_CORE_GATE_NONCE"] = nonce
    else:
        env.pop("JASMINE_CORE_GATE_NONCE", None)
    command = [str(Path(manifest["project_root"]) / "scripts/jasmine-p1-hook.sh"),
               "--python", sys.executable, "--binding", manifest["binding"]]
    result = subprocess.run(command, input=canonical_json(payload), capture_output=True,
                            text=True, cwd=manifest["project_root"], env=env, timeout=20)
    if result.returncode:
        raise Failed("direct offline hook probe failed to execute")
    try:
        output = json.loads(result.stdout)
    except ValueError as exc:
        raise Failed("direct offline hook probe returned malformed JSON") from exc
    if bound and output.get("hookSpecificOutput", {}).get("permissionDecision") != "deny":
        raise Failed("bound hook did not fail closed when owned Core was stopped")
    if not bound and output != {}:
        raise Failed("unbound session did not remain no-op")
    return {"bound": bound, "result": "deny" if bound else "noop"}


def _run(args: argparse.Namespace, report: dict[str, Any]) -> None:
    out = Path(args.out).expanduser().resolve()
    if not out.is_dir():
        if not out.exists():
            out.mkdir(mode=0o700, parents=True)
            os.chmod(out, 0o700)
        raise Blocked("no prepared scratch Gate directory; run --prepare first")
    manifest = _read_json_private(out / "manifest.json", "manifest")
    if _git()["dirty"] or _git()["commit"] != manifest["prepared_commit"]:
        raise Blocked("code candidate changed or is dirty since preparation")
    if not args.user_reviewed_trust:
        raise Blocked("project hooks and trust require operator review")
    if not shutil.which(args.codex_bin):
        raise Blocked("codex executable is unavailable")
    nonce = os.environ.get("JASMINE_CORE_GATE_NONCE", "")
    binding_path = Path(manifest["binding"])
    binding = _binding(binding_path, nonce)
    if binding["core_url"] != f"http://127.0.0.1:{manifest['port']}":
        raise Blocked("binding URL differs from prepared owned Core port")
    report["preflight"] = _db_binding(Path(manifest["db"]), binding)
    project_root = Path(manifest["project_root"])
    hook_path = Path(manifest["hook_config"])
    report["hook_command_hashes"] = _hook_config(hook_path, project_root, binding_path)
    for name in ("allowed", "denied", "stale"):
        if Path(manifest[name]).exists():
            raise Blocked(f"scratch {name} side effect already exists")
    core: subprocess.Popen[bytes] | None = None
    try:
        core = _start_core(Path(manifest["db"]), Path(manifest["workspace"]), manifest["port"], out)
        report["owned_core"] = _owned_endpoint(binding["core_url"], core.pid)
        human = CoreClient(binding["core_url"], _private_file(out / "human.token", "human token").decode().strip())
        agent = CoreClient(binding["core_url"], _private_file(out / "agent.token", "agent token").decode().strip())
        system = CoreClient(binding["core_url"], _private_file(out / "system.token", "system token").decode().strip())
        guard = _api(system, "POST", "/v1/guard/check", {
            "project_id": manifest["project_id"], "task_id": manifest["task_id"],
            "tool": "Bash", "action": "touch", "path": manifest["denied"]})
        if (guard.get("decision") != "deny" or not any(
            rule.get("rule_id") == manifest["rules"][0]["rule_id"] and rule.get("version") == 1
            for rule in guard.get("matched", []))):
            raise Failed("prepared HARD Rule does not deny the actual Bash wire action")
        report["guard_preflight"] = {"decision": guard["decision"], "matched": guard["matched"]}
        _transition(agent, manifest, "IN_PROGRESS")
        prompt1 = (f"P1 real Gate: use Bash to run exactly `{manifest['denied_command']}` and observe the native denial. "
                   f"Then independently use Bash to run exactly `{manifest['allowed_command']}`. "
                   "Continue after the denial. Report both outcomes and do not use other tools.")
        session, _ = _real_turn(args, out, manifest, "01-tools", prompt1, None, nonce, report,
                                allowed_commands=(manifest["denied_command"], manifest["allowed_command"]))
        traces = report["turns"][-1]["hook_traces"]
        if Path(manifest["denied"]).exists() or not Path(manifest["allowed"]).exists():
            raise Failed("native DENY had a side effect or allowed Bash tool did not run")
        denied_input_sha = hashlib.sha256(canonical_json(
            {"command": manifest["denied_command"]}).encode()).hexdigest()
        denied_traces = [t for t in traces if t.get("result") == "guard:deny" and
                         t.get("tool_input_sha256") == denied_input_sha and
                         t.get("session_id") == session and t.get("turn_id") and t.get("tool_use_id")]
        if len(denied_traces) != 1:
            raise Failed("real DENY trace is not one prepared denied Bash call")
        denied_trace = denied_traces[0]
        if any(t.get("hook_event_name") == "PostToolUse" and
               all(t.get(key) == denied_trace.get(key) for key in
                   ("session_id", "turn_id", "tool_use_id")) for t in traces):
            raise Failed("denied Bash call reached PostToolUse; native denial was not enforced")
        first_tool_event = _captured_tool_event(human, manifest, report["turns"][-1],
                                                 manifest["allowed_command"], {"guard:verify"})
        response = first_tool_event["payload"].get("tool_response")
        exit_code = response.get("exit_code", response.get("exitCode")) if isinstance(response, dict) else None
        if type(exit_code) is not int or exit_code != 0:
            raise Failed("native PostToolUse result has no trustworthy zero integer exit code")
        _transition(agent, manifest, "EXECUTED")
        _, step = _state(human, manifest["task_id"], manifest["step_id"])
        session, _ = _real_turn(args, out, manifest, "02-executed", PROMPT_EXECUTED,
                                session, nonce, report,
                                expected_state=("STEP", step["status"], step["revision"]))
        verified = _transition(agent, manifest, "VERIFIED")
        refs = _evidence(human, verified["evidence_ids"], manifest)
        if not any(item.get("source_event_id") == first_tool_event["event_id"] for item in refs):
            raise Failed("VERIFIED did not cite actual PostToolUse Evidence")
        if {"rule_id": manifest["rules"][1]["rule_id"], "version": 1} not in verified["rule_versions"]:
            raise Failed("VERIFIED did not consume the active VERIFY Rule version")
        _, step = _state(human, manifest["task_id"], manifest["step_id"])
        session, _ = _real_turn(args, out, manifest, "03-stale", PROMPT_VERIFIED.format(stale_command=manifest["stale_command"]),
                                session, nonce, report,
                                expected_state=("STEP", step["status"], step["revision"]),
                                allowed_commands=(manifest["stale_command"],))
        _captured_tool_event(human, manifest, report["turns"][-1], manifest["stale_command"],
                             {"guard:allow"})
        task, step = _state(human, manifest["task_id"], manifest["step_id"])
        if not Path(manifest["stale"]).exists() or step["status"] != "STALE":
            raise Failed("real workspace mutation did not persist VERIFIED to STALE")
        _transition(agent, manifest, "IN_PROGRESS")
        prompt4 = f"Use Bash to run exactly `{manifest['allowed_command']}` again. Report its outcome."
        session, _ = _real_turn(args, out, manifest, "04-rerun", prompt4, session, nonce, report,
                                allowed_commands=(manifest["allowed_command"],))
        rerun_event = _captured_tool_event(human, manifest, report["turns"][-1],
                                            manifest["allowed_command"], {"guard:verify"})
        _transition(agent, manifest, "EXECUTED")
        verified_again = _transition(agent, manifest, "VERIFIED")
        rerun_refs = _evidence(human, verified_again["evidence_ids"], manifest)
        if not any(item.get("source_event_id") == rerun_event["event_id"] for item in rerun_refs):
            raise Failed("second VERIFIED did not cite the actual rerun PostToolUse")
        _, step = _state(human, manifest["task_id"], manifest["step_id"])
        session, step_origin = _real_turn(args, out, manifest, "05-step-confirm", PROMPT_STEP_CONFIRM,
                                          session, nonce, report,
                                          expected_state=("STEP", step["status"], step["revision"]))
        task, step = _state(human, manifest["task_id"], manifest["step_id"])
        step_confirmation = _api(human, "POST", "/v1/evidence/confirm", {
            "task_id": manifest["task_id"], "step_id": manifest["step_id"],
            "host_id": manifest["host_id"], "origin_event_id": step_origin["event_id"],
            "expected_revision": step["revision"]})
        accepted_step = _transition(human, manifest, "ACCEPTED")
        if step_confirmation["evidence"]["evidence_id"] not in accepted_step["evidence_ids"]:
            raise Failed("Step ACCEPTED did not cite the genuine current-revision confirmation")
        task, _ = _state(human, manifest["task_id"], manifest["step_id"])
        session, task_origin = _real_turn(args, out, manifest, "06-task-confirm", PROMPT_TASK_CONFIRM,
                                          session, nonce, report,
                                          expected_state=("TASK", task["status"], task["revision"]))
        task, step = _state(human, manifest["task_id"], manifest["step_id"])
        task_confirmation = _api(human, "POST", "/v1/evidence/confirm", {
            "task_id": manifest["task_id"], "host_id": manifest["host_id"],
            "origin_event_id": task_origin["event_id"], "expected_revision": task["revision"]})
        accepted_task = _api(human, "POST", f"/v1/tasks/{manifest['task_id']}/accept",
                             {"host_id": manifest["host_id"], "expected_revision": task["revision"]})
        if task_confirmation["evidence"]["evidence_id"] not in accepted_task["evidence_ids"]:
            raise Failed("Task ACCEPTED did not cite the genuine later Task confirmation")
        history = _api(human, "GET", f"/v1/steps/{manifest['step_id']}/history")["events"]
        for state_result in (verified, verified_again, accepted_step):
            if not any(event["event_id"] == state_result["event"]["event_id"] and
                       event["payload"].get("evidence_ids") == state_result["evidence_ids"]
                       for event in history):
                raise Failed("Step history lacks immutable Evidence IDs")
        task_history = _api(human, "GET", f"/v1/tasks/{manifest['task_id']}/history")["events"]
        if not any(event["event_id"] == accepted_task["event"]["event_id"] and
                   event["payload"].get("evidence_ids") == accepted_task["evidence_ids"]
                   for event in task_history):
            raise Failed("Task history lacks immutable Evidence IDs")
        final_task, final_step = _state(human, manifest["task_id"], manifest["step_id"])
        if final_task["status"] != "ACCEPTED" or final_step["status"] != "ACCEPTED":
            raise Failed("Task or Step did not reach ACCEPTED")
        first_pid = core.pid
        _stop_core(core, manifest["port"])
        core = None
        report["offline_hook_probes"] = [_hook_probe(manifest, session, nonce, bound=True),
                                          _hook_probe(manifest, session, nonce, bound=False)]
        core = _start_core(Path(manifest["db"]), Path(manifest["workspace"]), manifest["port"], out)
        if core.pid == first_pid:
            raise Failed("owned Core PID did not change across persistence restart")
        report["core_restart_pids"] = [first_pid, core.pid]
        persisted_history = _api(human, "GET", f"/v1/steps/{manifest['step_id']}/history")["events"]
        persisted_task_history = _api(human, "GET", f"/v1/tasks/{manifest['task_id']}/history")["events"]
        if persisted_history != history or persisted_task_history != task_history:
            raise Failed("State history or Evidence references changed after Core restart")
        report.update(verdict="PASS", phase="real_conversation", session_id=session,
            final_task_status=final_task["status"], final_step_status=final_step["status"],
            observed_state_sequence=["IN_PROGRESS", "EXECUTED", "VERIFIED", "STALE",
                                     "IN_PROGRESS", "EXECUTED", "VERIFIED", "ACCEPTED"],
            evidence_ids={"first_verified": verified["evidence_ids"],
                          "second_verified": verified_again["evidence_ids"],
                          "step_accept": accepted_step["evidence_ids"],
                          "task_accept": accepted_task["evidence_ids"]})
    finally:
        _stop_core(core, manifest["port"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--project-root", default=str(ROOT), help="stable repo that will load project hooks")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-timeout", type=int, default=300)
    parser.add_argument("--user-reviewed-trust", action="store_true")
    args = parser.parse_args()
    report = {"case_id": "P1-T10", "phase": "prepare" if args.prepare else "preflight",
              "verdict": "BLOCKED", "provenance": _git(), "turns": []}
    status = 2
    try:
        if args.prepare:
            _prepare(args, report)
        else:
            _run(args, report)
            status = 0
    except Blocked as exc:
        report["reason"] = str(exc)
    except (Failed, subprocess.TimeoutExpired) as exc:
        report.update(verdict="FAIL", reason=str(exc), failure_class=type(exc).__name__)
        status = 1
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError, ApiError) as exc:
        report.update(verdict="FAIL" if args.run else "BLOCKED",
                      reason=f"{type(exc).__name__}: {exc}", failure_class=type(exc).__name__)
        status = 1 if args.run else 2
    out = Path(args.out).expanduser().resolve()
    record_path = out / ("p1-t10-prepare.json" if args.prepare else f"p1-t10-run-{time.time_ns()}.json")
    if out.is_dir():
        _record(record_path, report)
    print(json.dumps({"record": str(record_path),
                      "verdict": report["verdict"], "reason": report.get("reason")}, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
