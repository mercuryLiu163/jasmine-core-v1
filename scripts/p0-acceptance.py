#!/usr/bin/env python3
"""Run the P0 acceptance cases and write a Validation Run bundle.

Usage:
  python3 scripts/p0-acceptance.py --out <dir> [--db <path>] [--commit <sha>]

Each case records the taskbook's seven sections. A case that could not be
executed is BLOCKED with the reason; it is never reported as PASS. The P0-T07
real-conversation case is driven by scripts/p0-t07-real-conversation.sh, which
must be run against a real Codex session; this script only records what that
script left behind, or marks the case BLOCKED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jasmine_core import SCHEMA_VERSION, db, errors  # noqa: E402
from jasmine_core import auth, objects, registry  # noqa: E402
from jasmine_core.migrations import applied_migrations, current_version, migrate  # noqa: E402
from jasmine_core.validation.recorder import NOT_IMPLEMENTED, Case, Recorder  # noqa: E402

T07_MARKER = "p0-t07-result.json"
PROMPT = "P0-T07 jasmine capture probe: please answer with the single word ack."


# -- helpers -----------------------------------------------------------------


def child_env(**extra: str) -> dict:
    """Every child process gets the repo's src on the path, like CI does."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    env.update(extra)
    return env


def run(cmd: list[str], **kwargs) -> dict:
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kwargs)
    return {
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
        "seconds": round(time.time() - started, 3),
    }


def post(base: str, path: str, body, token: str | None = None,
         headers: dict[str, str] | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        merged["Authorization"] = f"Bearer {token}"
    merged.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=merged, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw) if raw else {}


def get(base: str, path: str, token: str | None = None,
        headers: dict[str, str] | None = None) -> tuple[int, dict]:
    merged = {"Authorization": f"Bearer {token}"} if token else {}
    merged.update(headers or {})
    req = urllib.request.Request(base + path, headers=merged, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw) if raw else {}


def counts(conn) -> dict:
    # `api_keys` and its `last_used_at` column are counted deliberately: a
    # refused caller used to write to that table before the scope check, and a
    # count list that omitted it could never see that. The field is named
    # `api_key_rows` because a *row count* is not a credential, and the bundle's
    # secret guard rejects a field merely named like one.
    tables = ("events", "projects", "tasks", "sessions", "actors", "hosts", "api_keys", "audit_log")
    field = {"api_keys": "api_key_rows"}
    conn_rows = {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in tables
    }
    return {field.get(table, table): value for table, value in conn_rows.items()}


def key_state(db_path: Path) -> dict:
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(last_used_at) AS used FROM api_keys"
        ).fetchone()
        return {"api_key_rows": row["n"], "api_key_rows_never_used": row["used"]}
    finally:
        conn.close()


def query(db_path: Path, fn):
    """Open a connection, run `fn`, and always close it."""
    conn = db.connect(db_path)
    try:
        return fn(conn)
    finally:
        conn.close()


def write_sentinel(db_path: Path) -> str:
    sentinel = "act_01K742SG00YPWF74TSXEKA3254"
    conn = db.connect(db_path)
    try:
        with db.transaction(conn):
            conn.execute(
                "INSERT INTO actors (actor_id, kind, display_name, created_at)"
                " VALUES (?, 'human', 'sentinel row', '2026-09-29T00:00:00Z')", (sentinel,)
            )
    finally:
        conn.close()
    return sentinel


def blocked(case_id: str, name: str, reason: str, reproduction: list[str],
            evidence: list[str] | None = None) -> Case:
    return Case(
        case_id=case_id, name=name, verdict="BLOCKED", not_applied_reason=reason,
        reproduction=reproduction, evidence=evidence or [],
    )


# -- cases -------------------------------------------------------------------


def case_t01(work: Path, db_path: Path) -> Case:
    """Migrate from empty, check the schema version, re-run without damage."""
    fresh = work / "t01.db"
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(fresh) + suffix)
        if path.exists():
            path.unlink()

    env = child_env(JASMINE_CORE_DB=str(fresh))
    first = run([sys.executable, "-m", "jasmine_core.cli", "migrate"], env=env, cwd=ROOT)
    second = run([sys.executable, "-m", "jasmine_core.cli", "migrate"], env=env, cwd=ROOT)
    third = run([sys.executable, "-m", "jasmine_core.cli", "migrate"], env=env, cwd=ROOT)
    schema = run([sys.executable, "-m", "jasmine_core.cli", "schema"], env=env, cwd=ROOT)

    before_migrations = query(fresh, counts)
    sentinel = write_sentinel(fresh)
    fourth = run([sys.executable, "-m", "jasmine_core.cli", "migrate"], env=env, cwd=ROOT)
    after = query(fresh, counts)
    survived = query(fresh, lambda c: c.execute(
        "SELECT COUNT(*) AS n FROM actors WHERE actor_id = ?", (sentinel,)
    ).fetchone()["n"])
    integrity = query(fresh, lambda c: c.execute("PRAGMA integrity_check").fetchone()[0])
    # The pragma set is frozen by ADR 0002 §2.1. `recursive_triggers` in
    # particular is not cosmetic: without it SQLite skips the DELETE trigger
    # during REPLACE conflict resolution. T05 also attacks the Event with the
    # pragma off, so a lost guard there is caught by behaviour, not by this
    # configuration reading.
    pragmas = query(fresh, lambda c: {
        name: c.execute(f"PRAGMA {name}").fetchone()[0] for name, _ in db.PRAGMAS
    })

    applied_after = json.loads(fourth["stdout"])["applied"]
    tools = [first, second, third, schema, fourth]

    parsed = json.loads(schema["stdout"])
    problems = []
    if first["exit_code"] != 0:
        problems.append("first migrate exited non-zero")
    if json.loads(second["stdout"])["applied"] != []:
        problems.append("second migrate applied migrations again")
    if json.loads(third["stdout"])["applied"] != []:
        problems.append("third migrate applied migrations again")
    if applied_after != []:
        problems.append("the fourth migrate applied migrations again")
    if parsed["schema_version"] != SCHEMA_VERSION:
        problems.append(f"schema_version {parsed['schema_version']} != {SCHEMA_VERSION}")
    if len(parsed["migrations"]) != SCHEMA_VERSION:
        problems.append("migration count does not match the schema version")
    if after["events"] != before_migrations["events"]:
        problems.append("re-running the migration changed the event count")
    # The sentinel is the one deliberate write between the two measurements, so
    # the actor count must be exactly one higher, not merely unchanged.
    if after["actors"] != before_migrations["actors"] + 1:
        problems.append(f"actor count went {before_migrations['actors']} -> {after['actors']}, "
                        "expected the one sentinel row and nothing else")
    if survived != 1:
        problems.append("the sentinel row did not survive re-migration")
    if integrity != "ok":
        problems.append(f"integrity_check returned {integrity!r}")
    expected = {"journal_mode": "wal", "foreign_keys": 1, "synchronous": 2,
                "recursive_triggers": 1, "busy_timeout": 5000}
    for name, want in expected.items():
        if str(pragmas.get(name)).lower() != str(want).lower():
            problems.append(f"PRAGMA {name} is {pragmas.get(name)!r}, expected {want!r}")

    return Case(
        case_id="P0-T01",
        name="Empty database migration, schema version, replay safety",
        verdict="FAIL" if problems else "PASS",
        user_raw=NOT_IMPLEMENTED,
        agent_response=NOT_IMPLEMENTED,
        tools=tools,
        state_before={"events": before_migrations["events"],
                      "actors": before_migrations["actors"],
                      "sentinel_written": sentinel},
        state_after={"events": after["events"], "actors": after["actors"],
                     "sentinel_rows_after": survived,
                     "fourth_run_applied": applied_after,
                     "schema_version": parsed["schema_version"],
                     "migrations": [m["name"] for m in parsed["migrations"]],
                     "integrity_check": integrity, "pragmas": pragmas},
        interpretation=NOT_IMPLEMENTED,
        context={"reason": "no Core API involvement in a migration-only case"},
        failure_class="acceptance" if problems else None,
        failure_output="; ".join(problems) if problems else None,
        reproduction=[
            "JASMINE_CORE_DB=<fresh> PYTHONPATH=src python3 -m jasmine_core.cli migrate",
            "… migrate   # again, twice more",
            "… schema",
            "sqlite3 <fresh> 'INSERT INTO actors …'",
            "… migrate   # again",
        ],
        evidence=["the bundle holds the raw stdout of every command above"],
    )


class LiveCore:
    """The API on a real port, with a real token, for T02-T06."""

    def __init__(self, db_path: Path, work: Path) -> None:
        self.db_path = db_path
        self.work = work
        self.port = self._free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self.generations: list[dict] = []
        self.token = ""
        self.actor_id = ""
        self.host_id = ""
        self.log = work / "api.log"

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def start(self) -> None:
        env = child_env(JASMINE_CORE_DB=str(self.db_path))
        # Append, never truncate: T03's evidence is that a *different* process
        # served the reads, and truncating the log threw that away.
        handle = self.log.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "jasmine_core.cli", "serve", "--port", str(self.port)],
            cwd=ROOT, env=env, stdout=handle, stderr=handle,
        )
        self.generations.append({
            "pid": self.process.pid, "port": self.port, "db": str(self.db_path),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        for _ in range(80):
            try:
                status, body = get(self.base, "/v1/health")
                if status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("the Core API did not come up")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.process.kill()

    def provision(self) -> None:
        conn = db.connect(self.db_path)
        try:
            reg = registry.Registry(conn)
            with db.transaction(conn):
                self.host_id = "hst_01K742SG00BMSDET9BTP151RAR"
                self.actor_id = "act_01K742SG00CBKP25A9ETPBTRMJ"
                reg.upsert_host(self.host_id, display_name="acceptance host")
                reg.upsert_actor(self.actor_id, kind="human", display_name="acceptance user",
                                 home_host_id=self.host_id)
            self.token = auth.Auth(conn).issue_key(
                actor_id=self.actor_id, label="acceptance",
                scopes=["admin", "objects:read", "objects:write", "events:read", "events:write"],
            )["token"]
        finally:
            conn.close()

    def actor_kind(self, actor_id: str) -> str:
        conn = db.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT kind FROM actors WHERE actor_id = ?", (actor_id,)
            ).fetchone()["kind"]
        finally:
            conn.close()

    def mint(self, scopes: list[str], label: str) -> str:
        conn = db.connect(self.db_path)
        try:
            return auth.Auth(conn).issue_key(actor_id=self.actor_id, label=label,
                                             scopes=scopes)["token"]
        finally:
            conn.close()


def case_t02(core: LiveCore) -> Case:
    """Create project, task, session and event through the API."""
    before = query(core.db_path, counts)
    s1, project = post(core.base, "/v1/projects", {"name": "P0 acceptance project",
                                                   "host_id": core.host_id}, core.token)
    s2, task = post(core.base, "/v1/tasks",
                    {"title": "P0 acceptance task",
                     "project_id": project["object"]["project_id"],
                     "host_id": core.host_id}, core.token)
    s3, session = post(core.base, "/v1/sessions",
                       {"project_id": project["object"]["project_id"],
                        "task_id": task["object"]["task_id"],
                        "host_id": core.host_id}, core.token)
    # recorded_at is sampled per write. Two objects created a moment apart must
    # not share a timestamp, and it must not be the process start time.
    time.sleep(1.05)
    s_later, later = post(core.base, "/v1/projects",
                          {"name": "P0 acceptance project (later)", "host_id": core.host_id},
                          core.token)
    s4, event = post(core.base, "/v1/events",
                     {"event_type": "user.prompt", "source_system": "acceptance",
                      "source_event_id": "acceptance-turn-1", "host_id": core.host_id,
                      "session_id": session["object"]["session_id"],
                      "task_id": task["object"]["task_id"],
                      "project_id": project["object"]["project_id"],
                      "payload": {"text": PROMPT}}, core.token)

    problems = []
    if [s1, s2, s3, s4] != [201, 201, 201, 201]:
        problems.append(f"statuses {[s1, s2, s3, s4]} != [201, 201, 201, 201]")
    for name, created in (("project", project), ("task", task), ("session", session)):
        obj, evt = created["object"], created["event"]
        if obj["source_event_id"] != evt["event_id"]:
            problems.append(f"{name}.source_event_id does not match its Event")
        if obj["revision"] != 1:
            problems.append(f"{name}.revision is {obj['revision']}, expected 1")
        if evt["payload"]["object_id"] != obj[f"{name}_id"]:
            problems.append(f"{name} Event does not point back at the object")
        if evt["actor_id"] != core.actor_id or evt["host_id"] != core.host_id:
            problems.append(f"{name} Event lost its provenance")
    if event["event"]["payload"]["text"] != PROMPT:
        problems.append("the stored text is not the text that was sent")
    # actor_kind must come from the registry, not from the caller: an Event is
    # append-only, so a row that misstates who acted can never be corrected.
    for name, created in (("project", project), ("task", task), ("session", session),
                          ("event", event)):
        evt = created["event"]
        if evt["actor_kind"] != core.actor_kind(core.actor_id):
            problems.append(
                f"the {name} Event records actor_kind {evt['actor_kind']!r} but the registry "
                f"says {core.actor_kind(core.actor_id)!r}"
            )
        if evt["actor_id"] != core.actor_id:
            problems.append(f"the {name} Event records a different actor than the token")
    if s_later != 201:
        problems.append(f"the second project create returned {s_later}")
    elif later["object"]["created_at"] == project["object"]["created_at"]:
        problems.append(
            "two objects created more than a second apart share created_at: recorded_at is "
            "being sampled once per store instead of once per write"
        )
    after = query(core.db_path, counts)
    fk = query(core.db_path, lambda c: c.execute("PRAGMA foreign_key_check").fetchall())
    integrity = query(core.db_path, lambda c: c.execute("PRAGMA integrity_check").fetchone()[0])
    if fk:
        problems.append(f"foreign_key_check reported {len(fk)} violations")
    if integrity != "ok":
        problems.append(f"integrity_check returned {integrity!r}")

    return Case(
        case_id="P0-T02",
        name="Create project, task, session and event through the Core API",
        verdict="FAIL" if problems else "PASS",
        user_raw=PROMPT,
        agent_response=NOT_IMPLEMENTED,
        tools=[{"request": "POST /v1/projects", "status": s1, "response": project},
               {"request": "POST /v1/tasks", "status": s2, "response": task},
               {"request": "POST /v1/sessions", "status": s3, "response": session},
               {"request": "POST /v1/projects (1s later, timestamp check)", "status": s_later,
                "response": later},
               {"request": "POST /v1/events", "status": s4, "response": event}],
        state_before=before,
        state_after={**after, "integrity_check": integrity,
                     "foreign_key_violations": len(fk)},
        interpretation=NOT_IMPLEMENTED,
        context={"reason": "P0 has no Interpreter; the Core stores and returns only"},
        failure_class="acceptance" if problems else None,
        failure_output="; ".join(problems) if problems else None,
        reproduction=[
            "jasmine-core bootstrap --scopes admin objects:read objects:write events:read events:write",
            "jasmine-core serve --port 8787",
            "curl -X POST localhost:8787/v1/projects -H 'Authorization: Bearer $T' -d '{...}'",
            "… /v1/tasks, … /v1/sessions, … /v1/events",
        ],
    )


def case_t03(core: LiveCore, previous: dict) -> Case:
    """Restart the Core and re-read all four objects."""
    first = dict(core.generations[-1]) if core.generations else {}
    core.stop()
    time.sleep(0.5)
    core.start()
    second = dict(core.generations[-1]) if core.generations else {}

    problems = []
    if not first or not second or first.get("pid") == second.get("pid"):
        problems.append("the Core process was not actually restarted, so the restart "
                        "evidence would be self-asserting")
    reread = {}
    for name, key, id_field in (("project", "projects", "project_id"),
                                ("task", "tasks", "task_id"),
                                ("session", "sessions", "session_id")):
        status, body = get(core.base, f"/v1/{key}/{previous[name][id_field]}", core.token)
        reread[name] = {"status": status, "id": previous[name][id_field], "body": body}
        if status != 200:
            problems.append(f"{name} read back as {status}")
            continue
        if body[name] != previous[name]:
            problems.append(f"{name} differs after the restart: "
                            f"{previous[name]} != {body[name]}")
    status, body = get(core.base, f"/v1/events/{previous['event_id']}", core.token)
    reread["event"] = {"status": status, "body": body}
    if status != 200:
        problems.append(f"event read back as {status}")
    elif body["event"]["payload"]["text"] != PROMPT:
        problems.append("the Raw Event text changed across the restart")

    return Case(
        case_id="P0-T03",
        name="Restart the Core and read every object and the Raw Event back",
        verdict="FAIL" if problems else "PASS",
        user_raw=NOT_IMPLEMENTED,
        agent_response=NOT_IMPLEMENTED,
        tools=[{"action": "stop the Core process", "process": first},
               {"action": "start a new process on the same core.db", "process": second},
               {"action": "re-read all four objects and the Event"}],
        state_before=previous,
        state_after=reread,
        interpretation=NOT_IMPLEMENTED,
        context={"db_path": str(core.db_path), "processes": [first, second],
                 "reason": "no interpretation in P0"},
        failure_class="acceptance" if problems else None,
        failure_output="; ".join(problems) if problems else None,
        reproduction=[
            "jasmine-core serve --port <p>   # note the ids created by T02",
            "kill the process",
            "jasmine-core serve --port <p>",
            "curl -H 'Authorization: Bearer $T' localhost:<p>/v1/projects/<prj_…>",
        ],
    )


def case_t04(core: LiveCore) -> Case:
    """Same event_id twice, and the same id with different content."""
    event_id = "evt_01K742SG00KSNSRN81BXC5ZAZC"
    body = {"event_type": "user.prompt", "source_system": "acceptance", "host_id": core.host_id,
            "event_id": event_id, "payload": {"text": "P0-T04 idempotency probe"}}
    before = query(core.db_path, counts)
    s1, first = post(core.base, "/v1/events", body, core.token)
    s2, second = post(core.base, "/v1/events", body, core.token)
    mid = query(core.db_path, counts)
    changed = dict(body, payload={"text": "P0-T04 different content"})
    s3, conflict = post(core.base, "/v1/events", changed, core.token)
    s4, duplicate_source = post(
        core.base, "/v1/events",
        {"event_type": "user.prompt", "source_system": "acceptance",
         "source_event_id": "acceptance-turn-1", "host_id": core.host_id,
         "payload": {"text": "P0-T04 same source turn"}}, core.token)
    after = query(core.db_path, counts)

    problems = []
    if s1 != 201 or s2 != 200:
        problems.append(f"replay statuses were {s1} then {s2}, expected 201 then 200")
    if not second.get("replayed"):
        problems.append("the second identical submission was not reported as a replay")
    if first["event"]["event_id"] != second["event"]["event_id"]:
        problems.append("the replay returned a different event_id")
    if first["event"]["seq"] != second["event"]["seq"]:
        problems.append("the replay returned a different seq")
    if after["events"] != before["events"] + 1:
        problems.append(f"event count went {before['events']} -> {after['events']}, expected +1")
    if s3 != 409 or conflict["error"]["code"] != "event_id_conflict":
        problems.append(f"a differing body returned {s3} {conflict.get('error', {}).get('code')}")
    if s4 != 409 or duplicate_source["error"]["code"] != "source_event_duplicate":
        problems.append(f"a repeated source_event_id returned {s4}")
    stored_status, stored = get(core.base, f"/v1/events/{event_id}", core.token)
    if stored["event"]["payload"]["text"] != "P0-T04 idempotency probe":
        problems.append("the stored Event was modified by the conflicting submission")

    return Case(
        case_id="P0-T04",
        name="Idempotent replay and conflicting reuse of one event_id",
        verdict="FAIL" if problems else "PASS",
        user_raw="P0-T04 idempotency probe",
        agent_response=NOT_IMPLEMENTED,
        tools=[{"request": "POST /v1/events (identical, 1st)", "status": s1, "response": first},
               {"request": "POST /v1/events (identical, 2nd)", "status": s2, "response": second},
               {"request": "POST /v1/events (same id, new body)", "status": s3, "response": conflict},
               {"request": "POST /v1/events (repeated source_event_id)", "status": s4,
                "response": duplicate_source},
               {"request": f"GET /v1/events/{event_id}", "status": stored_status,
                "response": stored}],
        state_before={"events": before["events"]},
        state_after={"events": mid["events"], "events_final": after["events"]},
        interpretation=NOT_IMPLEMENTED,
        context={"reason": "idempotency is storage behaviour, not interpretation"},
        failure_class="acceptance" if problems else None,
        failure_output="; ".join(problems) if problems else None,
        reproduction=[
            "curl -X POST …/v1/events -d '{\"event_id\":\"evt_…\",\"payload\":{\"text\":\"a\"}}'",
            "… repeat unchanged   -> expect 200 and replayed: true",
            "… with text \"b\"     -> expect 409 event_id_conflict",
        ],
    )


def case_t05(core: LiveCore, work: Path) -> Case:
    """Try to rewrite an Event, and inject a failure mid-transaction."""
    problems = []
    tools: list[dict] = []
    scratch = work / "t05.db"
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(scratch) + suffix)
        if path.exists():
            path.unlink()
    conn = db.connect(scratch)
    try:
        migrate(conn)
        reg = registry.Registry(conn)
        host, actor = "hst_01K742SG00BMSDET9BTP151RAR", "act_01K742SG00CBKP25A9ETPBTRMJ"
        with db.transaction(conn):
            reg.upsert_host(host)
            reg.upsert_actor(actor, kind="human", home_host_id=host)
        store = objects.ObjectStore(conn, schema_version=SCHEMA_VERSION)
        with db.transaction(conn):
            from jasmine_core.clock import parse_rfc3339
            from jasmine_core.models import NewEvent

            written, _ = store.events.append(NewEvent(
                event_type="user.prompt", source_system="acceptance", actor_id=actor,
                actor_kind="human", host_id=host, payload={"text": "P0-T05 immutability probe"},
                occurred_at=parse_rfc3339("2026-09-29T12:00:00Z"),
                client_occurred_at=parse_rfc3339("2026-09-29T12:00:00Z"),
            ))
        # The attack must target the row that actually exists. An earlier version
        # of this script attacked a hard-coded id that the Core had never issued,
        # so the REPLACE succeeded by creating a new row and the case reported a
        # false failure.
        victim = written["event_id"]
        before = counts(conn)

        import sqlite3

        for label, statement in (
            ("UPDATE events", "UPDATE events SET payload_json = '{\"text\":\"rewritten\"}'"),
            ("DELETE FROM events", "DELETE FROM events"),
        ):
            try:
                conn.execute(statement)
                problems.append(f"{label} was accepted")
                tools.append({"attack": label, "target": victim, "result": "ACCEPTED"})
            except sqlite3.Error as exc:
                tools.append({"attack": label, "target": victim, "result": "refused",
                              "error": str(exc)})
                if "append-only" not in str(exc):
                    problems.append(f"{label} was refused for the wrong reason: {exc}")

        def replace_attack(recursive: int) -> tuple[str, str]:
            other = sqlite3.connect(str(scratch), isolation_level=None)
            try:
                other.execute(f"PRAGMA recursive_triggers={recursive}")
                other.execute("PRAGMA foreign_keys=ON")
                other.execute(
                    "INSERT OR REPLACE INTO events (event_id, seq, schema_version, event_type,"
                    " source_system, source_event_id, occurred_at, recorded_at, actor_id,"
                    " actor_kind, host_id, payload_json, body_sha256) VALUES"
                    " (?, 999, 1, 'user.prompt', 'attacker',"
                    " NULL, '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z', ?, 'human', ?,"
                    " '{\"text\":\"rewritten\"}', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')",
                    (victim, actor, host),
                )
                return "ACCEPTED", ""
            except sqlite3.Error as exc:
                return "refused", str(exc)
            finally:
                other.close()

        for recursive in (1, 0):
            result, message = replace_attack(recursive)
            tools.append({"attack": f"INSERT OR REPLACE (recursive_triggers={recursive})",
                          "target": victim, "result": result, "error": message})
            if result == "ACCEPTED":
                problems.append(f"INSERT OR REPLACE succeeded with recursive_triggers={recursive}")
            elif "append-only" not in message:
                problems.append(f"REPLACE refused for the wrong reason: {message}")

        # Each projection may only reference its own creating event type, so at
        # most one object of any kind can claim a given Event. `UNIQUE` is per
        # table and would not stop a task claiming a project's Event.
        from jasmine_core.models import NewObject

        project_record = store.create(NewObject.project({"name": "cross kind", "host_id": host}),
                                      actor_id=actor)
        event_id = project_record["event"]["event_id"]
        project_id = project_record["object"]["project_id"]
        for kind, insert, bindings in (
            ("task",
             "INSERT INTO tasks (task_id, project_id, title, description, status, revision,"
             " source_event_id, created_at, updated_at) VALUES"
             " ('tsk_01K742SG00YPWF74TSXEKA3254', ?, 'x', '', 'ACTIVE', 1, ?,"
             " '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z')",
             (project_id, event_id)),
            # A session, not a second project: a second project is already
            # stopped by its own UNIQUE column, so it would not exercise the
            # cross-kind trigger at all.
            ("session",
             "INSERT INTO sessions (session_id, project_id, task_id, host_id, actor_id, status,"
             " revision, source_event_id, started_at, created_at, updated_at)"
             " VALUES ('ses_01K742SG00YPWF74TSXEKA3254', NULL, NULL, ?, ?, 'open', 1, ?,"
             " '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z', '2026-09-29T12:00:00Z')",
             (host, actor, event_id)),
        ):
            try:
                with db.transaction(conn):
                    conn.execute(insert, bindings)
                problems.append(f"a {kind} was allowed to claim a project.created event")
                tools.append({"attack": f"a {kind} claims a project.created event",
                              "result": "ACCEPTED"})
            except sqlite3.Error as exc:
                tools.append({"attack": f"a {kind} claims a project.created event",
                              "result": "refused", "error": str(exc)})
                # The refusal must name the required creating event type; an
                # unrelated constraint would also "refuse" and prove nothing.
                if not any(phrase in str(exc) for phrase in
                           ("must reference a task.created event",
                            "must reference a session.started event")):
                    problems.append(f"the cross-kind claim was refused for the wrong "
                                    f"reason: {exc}")

        # Fault injection after the Event insert.
        original = store._insert_projection
        store._insert_projection = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("injected failure after the Event insert")
        )
        try:
            from jasmine_core.models import NewObject

            store.create(NewObject.project({"name": "doomed", "host_id": host}),
                         actor_id=actor)
            problems.append("the injected failure did not propagate")
        except RuntimeError as exc:
            tools.append({"attack": "fault injection after the Event insert",
                          "result": "raised as expected", "error": str(exc)})
        finally:
            store._insert_projection = original

        after = counts(conn)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        in_transaction = conn.in_transaction
        survivor = store.events.get(victim)
        # One project and its Event were created by the cross-kind probe, so the
        # comparison is against the counts taken just before the faults.
        if after["events"] != before["events"] + 1 or after["projects"] != before["projects"] + 1:
            problems.append(f"an unexpected number of rows appeared: {before} -> {after}")
        if integrity != "ok":
            problems.append(f"integrity_check returned {integrity!r}")
        if fk:
            problems.append(f"foreign_key_check reported {len(fk)} violations")
        if in_transaction:
            problems.append("the connection was left inside a transaction")
        if survivor is None or survivor["payload"]["text"] != "P0-T05 immutability probe":
            problems.append("the stored Event text did not survive")
    finally:
        conn.close()

    return Case(
        case_id="P0-T05",
        name="Raw Events cannot be modified or deleted; an injected failure leaves nothing",
        verdict="FAIL" if problems else "PASS",
        user_raw="P0-T05 immutability probe",
        agent_response=NOT_IMPLEMENTED,
        tools=tools,
        state_before=before,
        state_after={"counts": after, "integrity_check": integrity,
                     "foreign_key_violations": len(fk), "conn.in_transaction": in_transaction},
        interpretation=NOT_IMPLEMENTED,
        context={"scratch_db": str(scratch),
                 "reason": "a throwaway database; the live one is never attacked"},
        failure_class="acceptance" if problems else None,
        failure_output="; ".join(problems) if problems else None,
        reproduction=[
            "sqlite3 <db> \"UPDATE events SET payload_json='…'\"   -> append-only",
            "sqlite3 <db> 'DELETE FROM events'                      -> append-only",
            "sqlite3 <db> \"INSERT OR REPLACE INTO events …\"       -> append-only",
            "python3 -c '<patch ObjectStore._insert_projection to raise>'",
        ],
    )


def case_t06(core: LiveCore) -> Case:
    """Unauthorised and wrong-scope writes are refused and change nothing."""
    before = query(core.db_path, counts)
    reader = core.mint(["events:read", "objects:read"], "reader")
    events_only = core.mint(["events:write"], "events-only")
    revoked = core.mint(["events:write"], "revoked")
    conn = db.connect(core.db_path)
    try:
        revoked_key = conn.execute(
            "SELECT key_id FROM api_keys WHERE label = 'revoked' ORDER BY created_at DESC"
        ).fetchone()["key_id"]
        auth.Auth(conn).revoke(revoked_key)
    finally:
        conn.close()
    # The three keys above are minted by this harness, so the key comparison has
    # to start here: only the refusals may follow.
    after_minting = key_state(core.db_path)

    body = {"event_type": "user.prompt", "source_system": "acceptance", "host_id": core.host_id,
            "payload": {"text": "P0-T06 authorisation probe"}}
    s1, r1 = post(core.base, "/v1/events", body)
    s2, r2 = post(core.base, "/v1/events", body, token="definitely-not-a-token")
    s3, r3 = post(core.base, "/v1/events", body, token=revoked)
    s4, r4 = post(core.base, "/v1/events", body, token=reader)
    s5, r5 = post(core.base, "/v1/projects", {"name": "x", "host_id": core.host_id}, token=events_only)
    s6, r6 = post(core.base, "/v1/auth/keys", {"label": "escalate", "scopes": ["admin"]},
                   token=events_only)
    # Measured here, before the authorised control: the control is expected to
    # write, so folding it into this comparison would blame it for the refusals.
    after = query(core.db_path, counts)
    keys_after_refusals = key_state(core.db_path)
    s7, r7 = post(core.base, "/v1/projects", {"name": "x", "host_id": core.host_id}, token=core.token)
    after_control = query(core.db_path, counts)
    # The three channels through which a caller can get a secret into the audit
    # table: a path segment, a query parameter, and a caller-supplied
    # X-Request-Id. Checking only the rendered API response missed all three.
    canary = "sk-canary-0123456789abcdefghij"
    canary_sha = hashlib.sha256(canary.encode()).hexdigest()
    encoded = "Bearer%20" + canary
    s8, r8 = post(core.base, "/v1/events",
                  {"event_type": "user.prompt", "source_system": "acceptance",
                   "host_id": encoded, "payload": {"text": "P0-T06 canary probe"}}, core.token,
                  headers={"X-Request-Id": encoded})
    s9, r9 = get(core.base, f"/v1/events?limit={encoded}", core.token)
    # A non-numeric query parameter must be a typed 400. `after_seq` and `limit`
    # are the two that exist today; both used to be able to raise a bare
    # ValueError and surface as an untyped 500.
    s10, r10 = get(core.base, "/v1/events?after_seq=abc", core.token)
    s11, r11 = get(core.base, "/v1/events?limit=abc", core.token)
    s12, r12 = get(core.base, "/v1/audit?limit=abc", core.token)
    s13, r13 = get(core.base, "/v1/tasks?offset=abc", core.token)
    audit_status, audit = get(core.base, "/v1/audit?limit=500", core.token)
    audit_raw = json.dumps(audit, ensure_ascii=False)
    # Read the table directly: the rendered view is one step removed from what
    # is actually stored.
    from jasmine_core import audit as audit_module

    stored_rows = query(core.db_path, lambda c: c.execute(
        "SELECT * FROM audit_log ORDER BY seq").fetchall())
    stored_raw = json.dumps([dict(row) for row in stored_rows], ensure_ascii=False)

    problems = []
    for label, status, response, want_status, want_code in (
        ("no token", s1, r1, 401, "unauthenticated"),
        ("unknown token", s2, r2, 401, "unauthenticated"),
        ("revoked token", s3, r3, 401, "unauthenticated"),
        ("read-only scope", s4, r4, 403, "forbidden_scope"),
        ("object create without objects:write", s5, r5, 403, "forbidden_scope"),
        ("key mint without admin", s6, r6, 403, "forbidden_scope"),
    ):
        actual = response.get("error", {}).get("code")
        if (status, actual) != (want_status, want_code):
            problems.append(f"{label} returned {status} {actual}, expected {want_status} {want_code}")
    if s7 != 201:
        problems.append("the authorised control request failed")
    if after["events"] != before["events"]:
        problems.append(f"refused writes still changed the event count: "
                        f"{before['events']} -> {after['events']}")
    if after["projects"] != before["projects"]:
        problems.append("refused writes still created a project")
    if after["tasks"] != before["tasks"] or after["sessions"] != before["sessions"]:
        problems.append("refused writes still created a task or a session")
    if keys_after_refusals["api_key_rows"] != after_minting["api_key_rows"]:
        problems.append("a refused request created an api key")
    if keys_after_refusals["api_key_rows_never_used"] != after_minting["api_key_rows_never_used"]:
        problems.append(
            f"a refused caller recorded last_used_at before the scope check: "
            f"{after_minting['api_key_rows_never_used']} unused -> "
            f"{keys_after_refusals['api_key_rows_never_used']} unused"
        )
    if s7 != 201 or after_control["events"] != after["events"] + 1:
        problems.append("the authorised control request did not write, so the refusals proved nothing")
    for secret, label in ((core.token, "the admin token"), (reader, "the reader token"),
                          (events_only, "the events-only token"), (revoked, "the revoked token")):
        if secret in audit_raw:
            problems.append(f"{label} appears in the audit log")
    for label, haystack in (("the rendered audit view", audit_raw),
                            ("the stored audit rows", stored_raw)):
        if "P0-T06 authorisation probe" in haystack:
            problems.append(f"the prompt text appears in {label}")
        for secret, name in ((core.token, "the admin token"), (reader, "the reader token"),
                             (events_only, "the events-only token"),
                             (revoked, "the revoked token"), (canary, "the canary token")):
            if secret in haystack:
                problems.append(f"{name} appears in {label}")
    if len(stored_rows) == 0:
        problems.append("the audit table is empty, so nothing was recorded at all")
    for label, status, response, want in (
        ("after_seq=abc", s10, r10, (400, "invalid_request")),
        ("limit=abc", s11, r11, (400, "invalid_request")),
        ("audit limit=abc", s12, r12, (400, "invalid_request")),
        ("offset=abc", s13, r13, (400, "invalid_request")),
    ):
        actual = (status, response.get("error", {}).get("code"))
        if actual != want:
            problems.append(f"{label} returned {actual[0]} {actual[1]}, expected {want[0]} {want[1]}")
    if audit_status != 200:
        problems.append(f"the audit log could not be read: {audit_status}")
    denials = [e for e in audit.get("entries", []) if e["decision"] == "deny"]
    if len(denials) < 4:
        problems.append(f"only {len(denials)} denials were audited, expected at least 4")

    return Case(
        case_id="P0-T06",
        name="Unauthorised and wrong-scope writes are refused, audited and change nothing",
        verdict="FAIL" if problems else "PASS",
        user_raw="P0-T06 authorisation probe",
        agent_response=NOT_IMPLEMENTED,
        tools=[
            {"request": "POST /v1/events (no token)", "status": s1, "response": r1},
            {"request": "POST /v1/events (unknown token)", "status": s2, "response": r2},
            {"request": "POST /v1/events (revoked token)", "status": s3, "response": r3},
            {"request": "POST /v1/events (events:read only)", "status": s4, "response": r4},
            {"request": "POST /v1/projects (events:write only)", "status": s5, "response": r5},
            {"request": "POST /v1/auth/keys (no admin)", "status": s6, "response": r6},
            {"request": "POST /v1/projects (authorised control)", "status": s7, "response": r7},
            # The canary is recorded as a hash: the bundle must not contain the
            # very material it is evidence about.
            {"request": "POST /v1/events (canary in host_id and X-Request-Id)",
             "status": s8, "canary_sha256": canary_sha,
             "response_code": r8.get("error", {}).get("code")},
            {"request": "GET /v1/events (canary in the query string)", "status": s9,
             "canary_sha256": canary_sha,
             "response_code": r9.get("error", {}).get("code")},
            {"request": "GET /v1/events?after_seq=abc", "status": s10, "response": r10},
            {"request": "GET /v1/events?limit=abc", "status": s11, "response": r11},
            {"request": "GET /v1/audit?limit=abc", "status": s12, "response": r12},
            {"request": "GET /v1/tasks?offset=abc", "status": s13, "response": r13},
            {"request": "GET /v1/audit?limit=500", "status": audit_status,
             "denials": len(audit.get("entries", []) and
                            [e for e in audit["entries"] if e["decision"] == "deny"] or [])},
        ],
        state_before=before,
        state_after={**after, "key_rows_after_minting": after_minting,
                     "key_rows_after_refusals": keys_after_refusals,
                     "after_authorised_control": after_control},
        interpretation=NOT_IMPLEMENTED,
        context={"audit_entries": len(audit.get("entries", [])),
                 "audit_rows_stored": len(stored_rows),
                 "reason": "the audit log holds metadata only, so prompt text is absent by design"},
        failure_class="acceptance" if problems else None,
        failure_output="; ".join(problems) if problems else None,
        reproduction=[
            "curl -X POST …/v1/events -d '{…}'                                -> 401",
            "curl -H 'Authorization: Bearer <wrong>' …                          -> 401",
            "curl -H 'Authorization: Bearer $READER' …                          -> 403",
            "sqlite3 <db> 'SELECT count(*) FROM events'                        -> unchanged",
            "canary=$(head -c 24 /dev/urandom -t 0 | base64 | tr -d '/+=' | head -c 20)",
            r"canary=\"sk-\$canary\"; sha256=\$(printf %s \"\$canary\" | shasum -a 256)",
            r"curl -H \"X-Request-Id: Bearer%20\$canary\" -X POST …/v1/events -d …",
            r"sqlite3 <db> 'SELECT path, request_id, detail_json FROM audit_log' | grep \"\$canary\"",
            "  -> no match; and the sha256 above must not appear either",
            "curl -H 'Authorization: Bearer $ADMIN' …/v1/audit | grep -i token -> no hit",
        ],
    )


def case_t07(work: Path, candidate_commit: str, checkout_clean: bool) -> Case:
    """The real-conversation Gate, driven by scripts/p0-t07-real-conversation.sh."""
    marker = work / T07_MARKER
    if not marker.exists():
        return blocked(
            "P0-T07",
            "Real Codex UserPromptSubmit capture and read-back after a restart",
            "The result file from scripts/p0-t07-real-conversation.sh is absent, so no real "
            "conversation was exercised. Codex requires a newly added hook command to carry a "
            "trusted_hash in ~/.codex/config.toml before it will run; that trust decision is "
            "reserved for the operator and was not made here, so a synthetic payload was not "
            "substituted for a real one.",
            [f"scripts/p0-t07-real-conversation.sh --out {work} "
             f"--db <existing-capture-core.db> --state-dir <capture-state>",
             f"python3 scripts/p0-acceptance.py --work {work} --out <bundle> "
             "--db <fresh-separate-scratch.db>"],
        )
    try:
        result = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                       f"Gate result marker is unreadable: {exc}", [str(marker)])
    if not isinstance(result, dict) or result.get("gate") != "P0-T07":
        return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                       "Gate result marker has an invalid schema", [str(marker)])
    if (result.get("code_commit") != candidate_commit or result.get("code_dirty") is not False
            or not checkout_clean):
        return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                       "Gate result is not bound to this clean candidate commit", [str(marker)])
    if result.get("schema_version") != SCHEMA_VERSION or not isinstance(result.get("runtime"), dict):
        return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                       "Gate result has missing or mismatched schema/runtime provenance", [str(marker)])
    outcome = result.get("outcome")
    if outcome not in ("PASS", "FAIL", "BLOCKED"):
        return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                       "Gate result has no valid outcome", [str(marker)])
    if outcome == "PASS":
        before = result.get("event_before_restart")
        after = result.get("readback_after_restart")
        traces = result.get("matching_invocation_trace")
        payload = before.get("payload") if isinstance(before, dict) else None
        if (not isinstance(before, dict) or before != after or
                not isinstance(payload, dict) or
                not isinstance(traces, list) or len(traces) != 1 or
                not isinstance(traces[0], dict) or not traces[0].get("captured") or
                traces[0].get("replayed") or
                traces[0].get("event_id") != before.get("event_id") or
                traces[0].get("source_session_sha256") !=
                    hashlib.sha256(str(result.get("source_session_id", "")).encode()).hexdigest() or
                traces[0].get("prompt_sha256") !=
                    hashlib.sha256(str(result.get("prompt", "")).encode()).hexdigest() or
                not result.get("source_session_id") or
                payload.get("source_session_id") != result.get("source_session_id") or
                before.get("actor_id") != result.get("actor_id") or
                before.get("host_id") != result.get("host_id") or
                payload.get("text") != result.get("prompt") or
                not isinstance(result.get("baseline_seq"), int) or
                not isinstance(before.get("seq"), int) or
                before["seq"] <= result["baseline_seq"] or
                not result.get("core_first_pid") or not result.get("core_second_pid") or
                result["core_first_pid"] == result["core_second_pid"] or
                result.get("blocked") or not result.get("captured") or result.get("problems")):
            return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                           "Gate PASS marker lacks valid same-turn event and restart evidence",
                           [str(marker)])
    elif outcome == "FAIL" and not result.get("problems"):
        return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                       "Gate FAIL marker lacks failure details", [str(marker)])
    elif outcome == "BLOCKED" and not result.get("reason"):
        return blocked("P0-T07", "Real Codex UserPromptSubmit capture and read-back",
                       "Gate BLOCKED marker lacks a reason", [str(marker)])
    # An environment condition and a product failure are different claims and
    # must not collapse into one verdict.
    if outcome == "BLOCKED":
        return Case(
            case_id="P0-T07",
            name="Real Codex conversation captured as a Raw Event and read back after a restart",
            verdict="BLOCKED",
            user_raw=result.get("prompt"),
            agent_response=result.get("agent_response", NOT_IMPLEMENTED),
            tools=result.get("tools", []),
            state_before=result.get("state_before", NOT_IMPLEMENTED),
            state_after=result.get("state_after", NOT_IMPLEMENTED),
            interpretation=NOT_IMPLEMENTED,
            context={**result.get("context", {}),
                     "reason": "P0 judges capture and persistence only, not interpretation or state"},
            not_applied_reason=result.get("reason"),
            reproduction=result.get("reproduction", []),
            evidence=result.get("evidence", []),
        )
    problems = result.get("problems") or []
    return Case(
        case_id="P0-T07",
        name="Real Codex conversation captured as a Raw Event and read back after a restart",
        verdict=outcome,
        user_raw=result.get("prompt"),
        agent_response=result.get("agent_response", NOT_IMPLEMENTED),
        tools=result.get("tools", []),
        state_before=result.get("state_before", NOT_IMPLEMENTED),
        state_after=result.get("state_after", NOT_IMPLEMENTED),
        interpretation=NOT_IMPLEMENTED,
        context={**result.get("context", {}),
                 "reason": "P0 judges capture and persistence only, not interpretation or state"},
        failure_class=result.get("failure_class"),
        failure_output=result.get("failure_output"),
        reproduction=result.get("reproduction", []),
        evidence=result.get("evidence", []),
        not_applied_reason=None if result.get("captured") else result.get("reason"),
    )


def run_case(recorder: "Recorder", case_id: str, name: str, fn, *fn_args) -> None:
    """Run one case, turning any crash into a FAIL rather than a lost bundle.

    Without this, a case that raised -- a broken guarantee surfacing as an
    unhandled exception -- took the whole run down and no bundle was written,
    which is the opposite of what a Validation Run is for.
    """
    try:
        recorder.add(fn(*fn_args))
    except Exception as exc:  # noqa: BLE001 - the point is to record, not to raise
        recorder.add(Case(
            case_id=case_id, name=name, verdict="FAIL",
            user_raw=NOT_IMPLEMENTED, agent_response=NOT_IMPLEMENTED,
            tools=[{"error": f"{type(exc).__name__}: {exc}"}],
            state_before=NOT_IMPLEMENTED, state_after=NOT_IMPLEMENTED,
            interpretation=NOT_IMPLEMENTED, context=NOT_IMPLEMENTED,
            failure_class="runner",
            failure_output=f"{type(exc).__name__}: {exc}",
            reproduction=[f"python3 scripts/p0-acceptance.py  # {case_id} raised before it "
                         "could report its own verdict"],
        ))


def _finish(recorder: Recorder) -> int:
    """Write the bundle and map the run verdict onto an exit code."""
    path = recorder.write()
    if recorder.redactions:
        # The guard had to remove credential material from a recorded response.
        # That is a product finding, not a formatting problem, so it is recorded
        # as a failing case rather than quietly cleaned up.
        recorder.add(Case(
            case_id="P0-SEC", name="No credential material reached the evidence bundle",
            verdict="FAIL", user_raw=NOT_IMPLEMENTED, agent_response=NOT_IMPLEMENTED,
            tools=[item.to_dict() for item in recorder.redactions],
            state_before=NOT_IMPLEMENTED, state_after=NOT_IMPLEMENTED,
            interpretation=NOT_IMPLEMENTED, context=NOT_IMPLEMENTED,
            failure_class="secret-leak",
            failure_output=(
                f"{len(recorder.redactions)} recorded value(s) contained credential material: "
                + ", ".join(item.path for item in recorder.redactions[:8])
            ),
            reproduction=["python3 scripts/p0-acceptance.py", "inspect bundle['redactions']"],
        ))
        path = recorder.write()
    print(json.dumps({
        "bundle": str(path),
        "commit": recorder.run.commit,
        "schema_version": recorder.run.schema_version,
        "verdict": recorder.run.verdict,
        "cases": [{"case_id": c.case_id, "verdict": c.verdict} for c in recorder.run.cases],
    }, ensure_ascii=False, indent=2))
    # A run whose cases failed must not exit 0: a pipeline that gates on the
    # exit code would read that as a pass.
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[recorder.run.verdict]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the P0 acceptance cases.")
    parser.add_argument("--out", required=True, help="directory for the Validation Run bundle")
    parser.add_argument("--work", help="scratch directory for databases and logs; "
                                       "defaults to a sibling of --out, never inside it")
    parser.add_argument("--db", required=True, help="a scratch core.db for this run")
    parser.add_argument("--executor", default="unassigned")
    parser.add_argument("--reviewer", default="unassigned")
    parser.add_argument("--verifier", default="unassigned")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    # The bundle is an evidence artefact. Scratch databases and logs must not sit
    # inside it: ADR 0004 §1.4 forbids a database file in a validation bundle,
    # and a bundle is what gets handed to a reviewer.
    work = Path(args.work).resolve() if args.work else out.parent / f"{out.name}-work"
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)

    recorder = Recorder(out, executor=args.executor, reviewer=args.reviewer,
                        verifier=args.verifier, repo=ROOT)
    recorder.not_implemented(
        "Rule and Guard (P1); the full Task state machine and expected_revision (P1); "
        "Evidence sufficiency (P1); the LLM Interpreter and Resolver (P2); Context Pack and "
        "Hindsight (P3, P4); the full Agent lifecycle adapter and offline sync (P5); the "
        "Dashboard (P6); the 30-scenario acceptance suite and the full Validation Recorder (P7)"
    )

    db_path = Path(args.db).resolve()
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        if path.exists():
            print(f"BLOCKED: acceptance scratch database path already exists: {path}; "
                  "choose a fresh --db path", file=sys.stderr)
            return 2

    core = LiveCore(db_path, work)
    previous: dict = {}
    try:
        run_case(recorder, "P0-T01", "Empty database migration, schema version, replay safety",
                 case_t01, work, db_path)
        try:
            core.start()
            core.provision()
        except Exception as exc:  # noqa: BLE001
            # A Core that will not start is a fact to record, not a reason to
            # leave the reviewer with no bundle at all.
            recorder.add(Case(
                case_id="P0-T02", name="Create project, task, session and event through the Core API",
                verdict="FAIL", user_raw=PROMPT, agent_response=NOT_IMPLEMENTED,
                tools=[{"error": f"{type(exc).__name__}: {exc}"},
                       {"action": "see the api log in the work directory"}],
                state_before=NOT_IMPLEMENTED, state_after=NOT_IMPLEMENTED,
                interpretation=NOT_IMPLEMENTED, context=NOT_IMPLEMENTED,
                failure_class="availability",
                failure_output=f"the Core API did not start: {type(exc).__name__}: {exc}",
                reproduction=["python3 scripts/p0-acceptance.py"],
            ))
            return _finish(recorder)
        try:
            t02 = case_t02(core)
        except Exception as exc:  # noqa: BLE001
            recorder.add(Case(
                case_id="P0-T02", name="Create project, task, session and event through the Core API",
                verdict="FAIL", user_raw=PROMPT, agent_response=NOT_IMPLEMENTED,
                tools=[{"error": f"{type(exc).__name__}: {exc}"}],
                state_before=NOT_IMPLEMENTED, state_after=NOT_IMPLEMENTED,
                interpretation=NOT_IMPLEMENTED, context=NOT_IMPLEMENTED,
                failure_class="runner", failure_output=f"{type(exc).__name__}: {exc}",
                reproduction=["python3 scripts/p0-acceptance.py"],
            ))
        else:
            recorder.add(t02)
            # Indexed by the request name, not by position: T02 records the
            # timestamp-probe project as well, and a positional index silently
            # pointed at the wrong response.
            by_request = {tool["request"]: tool for tool in t02.tools}
            previous = {
                "project": by_request["POST /v1/projects"]["response"]["object"],
                "task": by_request["POST /v1/tasks"]["response"]["object"],
                "session": by_request["POST /v1/sessions"]["response"]["object"],
                "event_id": by_request["POST /v1/events"]["response"]["event"]["event_id"],
            }
            run_case(recorder, "P0-T03", "Restart the Core and read every object back",
                     case_t03, core, previous)
        run_case(recorder, "P0-T04", "Idempotent replay and conflicting reuse of one event_id",
                 case_t04, core)
        run_case(recorder, "P0-T05", "Raw Events cannot be modified; a failure leaves nothing",
                 case_t05, core, work)
        run_case(recorder, "P0-T06", "Unauthorised and wrong-scope writes are refused",
                 case_t06, core)
    finally:
        core.stop()

    checkout_status = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10)
    checkout_clean = checkout_status.returncode == 0 and not checkout_status.stdout.strip()
    run_case(recorder, "P0-T07", "Real Codex UserPromptSubmit capture and read-back",
             case_t07, work, recorder.run.commit, checkout_clean)
    return _finish(recorder)


if __name__ == "__main__":
    raise SystemExit(main())
