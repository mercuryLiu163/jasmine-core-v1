#!/usr/bin/env python3
"""Validator harness: a real migrated DB and a real HTTP server over a socket.

Validator-authored (not the implementation's test code). It starts the real
`jasmine_core.api.server.Application` on an ephemeral loopback port, issues raw
urllib requests, and returns status, headers and the raw response bytes so the
validator can inspect `Content-Type` and every served field itself.

All run roots live under the validator's own quarantine/temp tree; the operator's
real run tree is only ever read.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from jasmine_core import auth, db, errors, ids, registry
from jasmine_core.api.server import Application, _Handler
from jasmine_core.migrations import migrate

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"


def _transport_disconnect(exc: BaseException) -> bool:
    """True when the connection closed before any HTTP status was written."""
    if isinstance(exc, (http.client.RemoteDisconnected, ConnectionResetError,
                        ConnectionAbortedError, BrokenPipeError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return _transport_disconnect(exc.reason) if isinstance(
            exc.reason, BaseException) else False
    if isinstance(exc, OSError):
        return getattr(exc, "errno", None) in (54, 104)  # ECONNRESET
    return False


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Live:
    """A live server + DB, plus env/root control and key issuance."""

    def __init__(self, *, mode: str = "single_user") -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="validate-p7-01-")
        self.base = Path(self._tmp.name)
        self.db_path = self.base / "core.db"
        conn = db.connect(self.db_path)
        migrate(conn)
        reg = registry.Registry(conn)
        with db.transaction(conn):
            reg.upsert_host(HOST, display_name="validator host")
            reg.upsert_actor(ACTOR, kind="human", display_name="validator",
                             home_host_id=HOST)
        conn.close()
        self._conn = db.connect(self.db_path)

        self._env = patch.dict(os.environ, {
            "JASMINE_MEMORY_DEPLOYMENT_MODE": mode,
            "JASMINE_CORE_VALIDATION_RUNS": str(self.base / "unset-root"),
        })
        self._env.start()

        self.port = free_port()
        self.app = Application(self.db_path)
        self.server = __import__("http.server", fromlist=["ThreadingHTTPServer"]) \
            .ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self.server.app = self.app
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.mode = mode

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.app.close()
        self._conn.close()
        self._env.stop()
        self._tmp.cleanup()

    # -- raw HTTP ------------------------------------------------------------

    def raw(self, method: str, path: str, token: str | None = None,
            extra_headers: dict[str, str] | None = None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        request.add_header("Accept", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for name, value in (extra_headers or {}).items():
            request.add_header(name, value)
        # The loopback server occasionally closes a connection before writing a
        # status (a transport event: dispatch turns every handler exception into
        # a 500 and the server logs a status for every response it sends). Record
        # it and retry, so a transient accept/close cannot masquerade as a
        # contract failure. Every retry is printed and kept in the raw log.
        attempts = 0
        while True:
            attempts += 1
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    raw = response.read()
                    status, headers = response.status, dict(response.headers)
                break
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                status, headers = exc.code, dict(exc.headers)
                break
            except Exception as exc:  # noqa: BLE001 - transport diagnosis
                if attempts >= 4 or not _transport_disconnect(exc):
                    raise
                print(f"[transport-retry] {method} {path} attempt {attempts} "
                      f"closed without a response: {type(exc).__name__}", flush=True)
                time.sleep(0.05)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = None
        return status, headers, body, raw

    def call(self, method: str, path: str, token: str | None = None):
        status, headers, body, raw = self.raw(method, path, token=token)
        return status, headers, body, raw

    # -- env / roots ---------------------------------------------------------

    @contextmanager
    def root(self, root: Path | str):
        with patch.dict(os.environ, {"JASMINE_CORE_VALIDATION_RUNS": str(root)}):
            yield

    @contextmanager
    def env(self, **values):
        with patch.dict(os.environ, {k: str(v) for k, v in values.items()}):
            yield

    def set_mode(self, mode: str) -> None:
        """Change the deployment mode and drop the per-Core service cache."""
        os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = mode
        self.app.validation_services = None
        self.mode = mode

    # -- keys / grants -------------------------------------------------------

    def key(self, scopes: list[str], actor_id: str = ACTOR, label: str = "validator") -> str:
        return auth.Auth(self._conn).issue_key(
            actor_id=actor_id, label=label, scopes=scopes)["token"]

    def new_actor(self, kind: str = "human") -> str:
        actor = ids.new_id("act")
        reg = registry.Registry(self._conn)
        with db.transaction(self._conn):
            reg.upsert_actor(actor, kind=kind, display_name=kind, home_host_id=HOST)
        return actor

    def create_project(self, name: str) -> str:
        # projects are created through the real API
        token = self.key(["admin", "objects:read", "objects:write"])
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/projects",
            data=json.dumps({"name": name, "host_id": HOST}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode())
        return payload["object"]["project_id"]

    def grant(self, project_id: str, actor_id: str) -> None:
        row = self._conn.execute(
            "SELECT source_event_id FROM projects WHERE project_id=?",
            (project_id,)).fetchone()
        with db.transaction(self._conn):
            self._conn.execute(
                "INSERT INTO memory_project_actors (project_id, actor_id,"
                " granted_by_event_id, created_at) VALUES (?,?,?,?)",
                (project_id, actor_id, row["source_event_id"], "2026-01-01T00:00:00.000000Z"))

    def guard_rows(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM memory_project_actors").fetchone()["n"]


# -- fixture builders ---------------------------------------------------------

SENTINEL = "N/A (P0 not implemented)"
BUNDLE_SCHEMA = "jasmine.validation-bundle.v1"
RECORDER_SHA = "a" * 64
FIXTURES = (Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "p7")
FILES = FIXTURES / "files"


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write_run(run_dir: Path, run_id: str, fixture: str = "ok-run.json", **overrides) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    data = fixture_json(fixture)
    data["run_id"] = run_id
    data.update(overrides)
    path = run_dir / f"{run_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_manifest(run_dir: Path, run_id: str, files: list[dict], *, mode: str = "component",
                   projects: list[str] | None = None, sources: dict | None = None,
                   frozen_at: str | None = None, schema: str = BUNDLE_SCHEMA,
                   raw: str | None = None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "manifest.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    manifest = {
        "schema": schema,
        "run_id": run_id,
        "mode": mode,
        "projects": list(projects or []),
        "sources": sources if sources is not None
        else {"recorder": {"path": "scripts/p0-acceptance.py", "sha256": RECORDER_SHA}},
        "files": files,
    }
    if frozen_at is not None:
        manifest["frozen_at"] = frozen_at
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def artifact_entry(run_dir: Path, artifact_id: str, rel_path: str, *,
                   fixture: str | None = None, content: bytes | None = None,
                   media_type: str = "text/plain", credential_shaped: bool = False,
                   declared_path: str | None = None, declared_sha: str | None = None,
                   declared_bytes: int | None = None) -> dict:
    destination = run_dir / rel_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if fixture is not None:
        destination.write_bytes((FILES / fixture).read_bytes())
    if content is not None:
        destination.write_bytes(content)
    actual = destination.read_bytes() if destination.exists() else b""
    return {
        "artifact_id": artifact_id,
        "path": rel_path if declared_path is None else declared_path,
        "bytes": len(actual) if declared_bytes is None else declared_bytes,
        "sha256": sha256_bytes(actual) if declared_sha is None else declared_sha,
        "media_type": media_type,
        "credential_shaped": credential_shaped,
    }


class Checker:
    """Collects named expectations so a script reports PASS/FAIL per assertion."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.checks: list[dict] = []

    def check(self, name: str, ok: bool, actual=None, expected=None) -> bool:
        row = {"name": name, "ok": bool(ok)}
        if expected is not None:
            row["expected"] = expected
        if actual is not None:
            row["actual"] = actual
        self.checks.append(row)
        print(f"[{'ok' if ok else 'FAIL'}] {name}"
              + (f"  actual={actual!r}" if not ok and actual is not None else "")
              + (f"  expected={expected!r}" if not ok and expected is not None else ""))
        return bool(ok)

    def summary(self) -> dict:
        failed = [c for c in self.checks if not c["ok"]]
        return {"label": self.label, "checks": len(self.checks),
                "failed": len(failed), "failures": failed, "all": self.checks}


def emit(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = str(payload)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"--- wrote {path}")
