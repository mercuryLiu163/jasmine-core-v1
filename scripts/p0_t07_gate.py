"""P0-T07 real Codex conversation Gate. Fixture tests cannot serve as Gate evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.request
import platform

ROOT = Path(__file__).resolve().parents[1]


class Blocked(Exception):
    pass


class Failed(Exception):
    pass


def same_path(a: str | Path, b: str | Path) -> bool:
    return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()


def parse_hook(path: Path, state_dir: Path, db: Path, python: Path) -> dict:
    if not path.is_file():
        raise Blocked(f"hook configuration is absent: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blocked(f"hook configuration is unreadable: {exc}") from exc
    expected = ROOT / "scripts/jasmine-capture-hook.sh"
    for i, entry in enumerate(config.get("hooks", {}).get("UserPromptSubmit", [])):
        for j, hook in enumerate(entry.get("hooks", [])):
            try:
                argv = shlex.split(hook["command"])
            except (KeyError, ValueError):
                continue
            if not argv or not same_path(argv[0], expected):
                continue
            if hook.get("type") != "command" or len(argv[1:]) % 2:
                raise Blocked("capture command has invalid type or malformed options")
            opts = {}
            for key, value in zip(argv[1::2], argv[2::2]):
                if key in opts or key not in {"--host", "--db", "--state-dir", "--python", "--timeout"}:
                    raise Blocked(f"capture command has unsupported or repeated option {key}")
                opts[key] = value
            for key, wanted in (("--db", db), ("--state-dir", state_dir), ("--python", python)):
                if key not in opts or not same_path(opts[key], wanted):
                    raise Blocked(f"capture command {key} does not match this Gate run")
            if not opts.get("--host"):
                raise Blocked("capture command has no --host")
            return {"index": i, "sub": j, "host_id": opts["--host"], "path": str(path.resolve())}
    raise Blocked(f"no UserPromptSubmit capture command from this checkout in {path}")


def trust_record_present(entry: dict, config_path: Path) -> bool:
    """Advisory: a record may be stale after the hook definition changes."""
    if not config_path.is_file():
        return False
    try:
        doc = tomllib.loads(config_path.read_text(encoding="utf-8"))
        key = f"{entry['path']}:user_prompt_submit:{entry['index']}:{entry['sub']}"
        record = doc.get("hooks", {}).get("state", {}).get(key, {})
        return isinstance(record.get("trusted_hash"), str) and bool(record["trusted_hash"])
    except (OSError, ValueError, AttributeError):
        return False


def check_identity(db: Path, token_file: Path, host_id: str) -> tuple[str, str, int]:
    if not db.is_file():
        raise Blocked(f"Core database does not exist: {db}")
    if not token_file.is_file():
        raise Blocked(f"capture token file does not exist: {token_file}")
    if token_file.stat().st_mode & 0o077:
        raise Blocked(f"capture token file must be mode 0600: {token_file}")
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise Blocked(f"capture token file is empty: {token_file}")
    try:
        con = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
        try:
            host = con.execute("SELECT host_id FROM hosts WHERE host_id=?", (host_id,)).fetchone()
            key = con.execute("SELECT actor_id, scopes, revoked_at FROM api_keys WHERE token_sha256=?",
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            max_seq = con.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise Blocked(f"Core database cannot be validated: {exc}") from exc
    if not host:
        raise Blocked(f"configured host {host_id} is absent from {db}")
    if not key or key[2] is not None:
        raise Blocked("capture token is not an active key for the selected Core database")
    scopes = json.loads(key[1])
    if not {"events:write", "events:read"} <= set(scopes):
        raise Blocked("capture token lacks events:write or events:read scope")
    return token, key[0], int(max_seq)


def provenance(db: Path) -> dict:
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=10)
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10)
    if commit.returncode or dirty.returncode:
        raise Blocked("Git commit or checkout state cannot be determined")
    con = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT value FROM core_meta WHERE key='schema_version'").fetchone()
    except sqlite3.Error as exc:
        raise Blocked(f"Core schema version cannot be read: {exc}") from exc
    finally:
        con.close()
    if row is None:
        raise Blocked("Core schema version is absent")
    from jasmine_core import SCHEMA_VERSION
    version = int(row[0])
    if version != SCHEMA_VERSION:
        raise Blocked(f"Core schema version {version} does not match code schema {SCHEMA_VERSION}")
    return {"code_commit": commit.stdout.strip(), "code_dirty": bool(dirty.stdout.strip()),
            "schema_version": version, "runtime": {
                "python": platform.python_version(), "platform": platform.platform()}}


def request(url: str, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=4) as response:
        return json.load(response)


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def port_owners(port: int) -> set[int]:
    if not shutil.which("lsof"):
        raise Blocked("lsof is required to prove Core endpoint ownership")
    out = subprocess.run(["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                         capture_output=True, text=True, timeout=5)
    if out.returncode not in (0, 1):
        raise Blocked(f"lsof could not inspect port {port}: {out.stderr.strip()[:160]}")
    return {int(line) for line in out.stdout.splitlines() if line.isdecimal()}


def start_core(db: Path, port: int, python: Path, out: Path) -> subprocess.Popen:
    if port_open(port) or port_owners(port):
        raise Blocked(f"port {port} is already occupied")
    log = (out / "api.log").open("ab")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), JASMINE_CORE_DB=str(db))
    proc = subprocess.Popen([str(python), "-m", "jasmine_core.cli", "serve", "--port", str(port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
    log.close()
    try:
        for _ in range(80):
            if proc.poll() is not None:
                raise Blocked(f"Core exited before readiness with status {proc.returncode}; see {out/'api.log'}")
            if port_open(port):
                owners = port_owners(port)
                if not owners:
                    time.sleep(0.25)
                    continue
                if owners != {proc.pid}:
                    raise Blocked(f"Core port {port} is owned by {sorted(owners)}, expected {proc.pid}")
                try:
                    if request(f"http://127.0.0.1:{port}/v1/health").get("status") == "ok":
                        return proc
                except (OSError, ValueError):
                    pass
            time.sleep(0.25)
        raise Blocked(f"Core did not become healthy on port {port}; see {out/'api.log'}")
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        raise


def stop_core(proc: subprocess.Popen | None, port: int) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    for _ in range(40):
        if not port_open(port) and not port_owners(port):
            return
        time.sleep(0.25)
    raise Failed(f"port {port} remained open after stopping owned Core process {proc.pid}")


def codex_session(stdout: str) -> str:
    sessions = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("type") == "thread.started" and isinstance(item.get("thread_id"), str):
            sessions.append(item["thread_id"])
    if len(sessions) != 1:
        raise Blocked(f"codex exec --json reported {len(sessions)} thread IDs; source session unproven")
    return sessions[0]


def matching_trace(path: Path, offset: int, session: str, prompt: str, url: str) -> list[dict]:
    if not path.is_file():
        return []
    with path.open("rb") as stream:
        stream.seek(offset)
        raw = stream.read()
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    matches = []
    for line in raw.splitlines():
        try:
            trace = json.loads(line)
        except ValueError:
            continue
        if (trace.get("source_session_sha256") == hashlib.sha256(session.encode()).hexdigest() and
                trace.get("prompt_sha256") == digest and
                trace.get("hook_event_name") == "UserPromptSubmit" and
                trace.get("core_url") == url):
            matches.append(trace)
    return matches


def verify_event(event: dict, trace: dict, session: str, prompt: str,
                 host: str, actor: str, baseline_seq: int) -> None:
    from jasmine_core.capture.codex_user_prompt_submit import derive_event_id, _turn_digest
    raw_turn = event.get("payload", {}).get("turn_id")
    turn = raw_turn if isinstance(raw_turn, str) else ""
    expected_id = derive_event_id(host, session, turn, prompt)
    expected_digest = _turn_digest({"session_id": session, "turn_id": turn})
    expected = {"event_id": expected_id, "event_type": "user.prompt",
                "source_system": "codex", "source_event_id": f"{session}:{turn}" if turn else None,
                "host_id": host, "actor_id": actor}
    for key, value in expected.items():
        if event.get(key) != value:
            raise Failed(f"captured event {key} differs from this Codex turn")
    if event.get("seq", 0) <= baseline_seq:
        raise Failed("captured event sequence predates this Gate run")
    payload = event.get("payload", {})
    if (payload.get("text") != prompt or payload.get("source_session_id") != session or
            payload.get("turn_id") != (turn or None)):
        raise Failed("captured event payload does not identify this Codex turn and prompt")
    if trace.get("event_id") != expected_id or trace.get("source_turn_sha256") != expected_digest:
        raise Failed("hook invocation trace does not identify the stored event and turn")
    if trace.get("turn_sha256") != (hashlib.sha256(turn.encode()).hexdigest() if turn else None):
        raise Failed("hook invocation trace turn hash differs from stored event")
    if not trace.get("captured") or trace.get("replayed"):
        raise Failed("same-turn hook invocation did not create a fresh captured event")


def run(args: argparse.Namespace, result: dict) -> None:
    out, db, state, hooks = (Path(p).expanduser().resolve() for p in
                            (args.out, args.db, args.state_dir, args.hooks_json))
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    out.chmod(0o700)
    python = Path(sys.executable).resolve()
    entry = parse_hook(hooks, state, db, python)
    result.update(provenance(db))
    result.update(hooks_json=str(hooks), entry_registered=True,
                  trust_record_present=trust_record_present(entry, Path.home()/".codex/config.toml"))
    if not result["trust_record_present"]:
        raise Blocked("no Codex trust record for this hook entry; operator review is required")
    token, actor, before_seq = check_identity(db, state/"capture-token", entry["host_id"])
    result.update(db_path=str(db), state_dir=str(state), host_id=entry["host_id"],
                  actor_id=actor, baseline_seq=before_seq)
    if not shutil.which("codex"):
        raise Blocked("codex CLI is unavailable")
    if not shutil.which("lsof"):
        raise Blocked("lsof is unavailable, so port ownership cannot be proven")
    port = args.port
    if port is None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    if not 1 <= port <= 65535:
        raise Blocked("port must be in 1..65535")
    url = f"http://127.0.0.1:{port}"
    result["core_url"] = url
    prompt = "P0-T07 jasmine capture probe: please answer with the single word ack."
    result["prompt"] = prompt
    trace_file = state/"hook-invocations.log"
    offset = trace_file.stat().st_size if trace_file.exists() else 0
    core = None
    try:
        core = start_core(db, port, python, out)
        first_pid = core.pid
        result["core_first_pid"] = first_pid
        env = dict(os.environ, JASMINE_CORE_URL=url, JASMINE_CORE_DB=str(db),
                   JASMINE_CORE_STATE_DIR=str(state), JASMINE_PYTHON=str(python))
        completed = subprocess.run(["codex", "exec", "--json", "--skip-git-repo-check", prompt],
                                   cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        (out/"codex-stdout.jsonl").write_text(completed.stdout, encoding="utf-8")
        (out/"codex-stderr.txt").write_text(completed.stderr, encoding="utf-8")
        result["agent_response"] = completed.stdout[-800:]
        result["codex_exit"] = completed.returncode
        session = codex_session(completed.stdout)
        result["source_session_id"] = session
        traces = matching_trace(trace_file, offset, session, prompt, url)
        result["matching_invocation_trace"] = traces
        if not traces:
            raise Blocked("no same-session, same-prompt hook trace for this Codex turn; current trust, hook loading, or runtime invocation is unproven")
        if completed.returncode:
            raise Failed(f"codex exec exited {completed.returncode}; see codex-stderr.txt")
        if len(traces) != 1:
            raise Failed(f"expected one same-turn hook invocation, found {len(traces)}")
        trace = traces[0]
        if not trace.get("captured"):
            raise Failed(f"same-turn hook ran but capture failed: {trace.get('reason')}")
        event_id = trace.get("event_id")
        if not isinstance(event_id, str):
            raise Failed("same-turn hook trace has no event ID")
        try:
            event = request(f"{url}/v1/events/{event_id}", token)["event"]
        except (OSError, ValueError, KeyError) as exc:
            raise Failed(f"same-turn event cannot be read before restart: {exc}") from exc
        verify_event(event, trace, session, prompt, entry["host_id"], actor, before_seq)
        result["event_before_restart"] = event
        stop_core(core, port)
        core = None
        if port_open(port):
            raise Failed("Core port remained open after first process stopped")
        core = start_core(db, port, python, out)
        result["core_second_pid"] = core.pid
        if core.pid == first_pid:
            raise Failed("Core process ID did not change across restart")
        try:
            readback = request(f"{url}/v1/events/{event_id}", token)["event"]
        except (OSError, ValueError, KeyError) as exc:
            raise Failed(f"same-turn event cannot be read after restart: {exc}") from exc
        result["event_after_restart"] = readback
        result["readback_after_restart"] = readback
        if readback != event:
            raise Failed("Raw Event changed across the owned Core process restart")
    finally:
        stop_core(core, port)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "jasmine-core-t07"))
    parser.add_argument("--db", default=os.environ.get("JASMINE_CORE_DB",
                        os.path.join(os.environ.get("TMPDIR", "/tmp"), "jasmine-core-t07/core.db")))
    parser.add_argument("--state-dir", default=os.environ.get("JASMINE_CORE_STATE_DIR",
                        str(Path.home()/".local/share/jasmine-core")))
    parser.add_argument("--hooks-json", default=str(ROOT/".codex/hooks.json"))
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = {"gate": "P0-T07", "real_codex_required": True, "outcome": "BLOCKED",
              "entry_registered": False, "trust_record_present": False,
              "blocked": True, "captured": False, "problems": [],
              "tools": [], "reproduction": [
                  "scripts/p0-t07-real-conversation.sh --out OUT --db DB --state-dir STATE"],
              "evidence": ["p0-t07-result.json", "api.log", "codex-stdout.jsonl",
                           "codex-stderr.txt"]}
    status = 2
    try:
        run(args, result)
        result["outcome"] = "PASS"
        result["captured"] = True
        result["blocked"] = False
        status = 0
    except Blocked as exc:
        result["reason"] = str(exc)
    except (Failed, subprocess.TimeoutExpired) as exc:
        result["outcome"] = "FAIL"
        result["reason"] = str(exc)
        result["blocked"] = False
        result["problems"] = [str(exc)]
        status = 1
    except Exception as exc:
        result["reason"] = f"Gate execution error ({type(exc).__name__}): {exc}"
    result["state_before"] = {"max_event_seq": result.get("baseline_seq"),
                               "core_first_pid": result.get("core_first_pid")}
    result["state_after"] = {"event": result.get("event_before_restart"),
                              "core_second_pid": result.get("core_second_pid")}
    result["context"] = {"db_path": result.get("db_path"),
                         "source_session_id": result.get("source_session_id"),
                         "host_id": result.get("host_id"), "actor_id": result.get("actor_id"),
                         "core_url": result.get("core_url")}
    result["tools"] = [{"command": "codex exec --json --skip-git-repo-check <fixed prompt>",
                        "exit_code": result.get("codex_exit")},
                       {"action": "stop owned Core", "pid": result.get("core_first_pid")},
                       {"action": "start owned Core", "pid": result.get("core_second_pid")},
                       {"action": "GET same event after restart"}]
    result["failure_class"] = None if status == 0 else ("environment" if status == 2 else "capture")
    result["failure_output"] = result.get("reason")
    result["evidence"] = [str(out/name) for name in
                          ("p0-t07-result.json", "api.log", "codex-stdout.jsonl",
                           "codex-stderr.txt") if name == "p0-t07-result.json" or (out/name).exists()]
    (out/"p0-t07-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{result['outcome']}: {result.get('reason', 'same-turn event survived owned Core restart')}",
          file=sys.stderr)
    print(f"Evidence: {out/'p0-t07-result.json'}", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT/"src"))
    raise SystemExit(main())
