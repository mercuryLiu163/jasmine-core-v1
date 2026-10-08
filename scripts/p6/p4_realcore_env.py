#!/usr/bin/env python3.13
"""An **isolated, real P4 Core** with a wire-double engine — the P6-03 acceptance environment.

This is the environment `web/REAL-CORE-ACCEPTANCE-PLAN.md` describes; read that first. It is not
a product feature and it is not P4 phase acceptance.

What is real here, and what is not — the distinction is the whole point:

* **Real P4 Core.** Every route, validator, scope check, actor-kind check, Event, revision and
  idempotency decision comes from `src/jasmine_core` at the pinned commit, extracted with
  `git archive` into the directory given by `--out` and run from there. Nothing is monkey-patched
  and no row is inserted behind the Core's back: Truth is created through P4's own stores, and the
  Memory rows come from P4's own recovery scan and worker.
* **Wire-double engine.** There is no Hindsight deployment here, so the engine is P4's own
  `tests/memory_support.StubHindsightServer` — a real loopback HTTP server speaking Hindsight
  0.10.2's wire format, reached through P4's real `HindsightTransport` over a real socket. P4's
  own Gate is explicit that this is not a Gate verdict ("A case is `PASS` only with
  `mode: "real"`"), so this environment labels every result `mode: boundary-fake (engine)` and
  does not claim P4 phase acceptance.
* **Isolation.** Its own directory, `core.db`, workspace and ports; loopback only; keys minted
  in-process by P4's own `auth.issue_key` and written only into the isolated tree at mode 600.
  It never reads the operator's database, hooks, global config or accounts, and it never starts
  or reconfigures an operator service.

Commands:

    # build the environment (DB, actors, keys, real Truth, a real retain) and keep serving
    python3.13 scripts/p6/p4_realcore_env.py build --out /tmp/p6-realcore --port 8791

    # build and stop before serving, for inspection
    python3.13 scripts/p6/p4_realcore_env.py build --out /tmp/p6-realcore --seed-only

The descriptor (`--out/descriptor.json`, mode 600) carries the ids, the keys and the mode labels;
the tokens are printed nowhere. A banner says all of this on stderr, because a transcription of a
run must not be mistakable for evidence about a real deployment.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

#: The commit this environment is built from. Pinned, never a branch.
DEFAULT_REF = "0c4f8f0e2118711ac9aa2a7006fddc6982962248"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Scopes each key carries. The agent's list is deliberately without `memory:manage` and
#: `memory:reflect`: real P4 refuses to issue those to an agent, and the environment records that
#: refusal rather than working around it.
KEY_SCOPES = {
    "human_manage": ["objects:read", "events:read", "state:read", "authority:read",
                     "evidence:read", "interpretations:read", "reviews:read",
                     "resolutions:read", "memory:read", "memory:manage", "memory:reflect"],
    "human_readonly": ["objects:read", "events:read", "state:read", "memory:read"],
    "system_manage": ["objects:read", "events:read", "state:read", "memory:read",
                      "memory:manage"],
    "agent_read": ["objects:read", "events:read", "state:read", "memory:read"],
}


def log(message: str) -> None:
    print(f"p4-realcore: {message}", file=sys.stderr, flush=True)


def banner(ref: str, out: Path) -> None:
    print("=" * 78, file=sys.stderr)
    print("ISOLATED REAL P4 CORE — real Core code, WIRE-DOUBLE engine. NOT a P4 Gate verdict.",
          file=sys.stderr)
    print(f"  Core from commit {ref} (git archive), extracted into {out / 'p4'}", file=sys.stderr)
    print("  Engine: tests/memory_support.StubHindsightServer — a real loopback HTTP server",
          file=sys.stderr)
    print("  speaking Hindsight 0.10.2's wire format, reached by P4's real HindsightTransport.",
          file=sys.stderr)
    print("  Every result from this environment is mode: boundary-fake (engine). P4's own Gate",
          file=sys.stderr)
    print("  requires mode: real and a real deployment, so P4 phase acceptance is NOT claimed.",
          file=sys.stderr)
    print("  Nothing here touches the operator's Core, database, hooks, config or accounts.",
          file=sys.stderr)
    print("=" * 78, file=sys.stderr)


def extract_core(ref: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", ref],
                             check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)
    if not (destination / "src" / "jasmine_core" / "memory").is_dir():
        raise SystemExit(f"{ref} has no src/jasmine_core/memory — wrong commit?")


def build(args: argparse.Namespace) -> dict[str, Any]:
    out: Path = args.out
    if out.exists() and any(out.iterdir()):
        if not args.reuse:
            raise SystemExit(f"{out} is not empty; pass --reuse to build in place")
    out.mkdir(parents=True, exist_ok=True)
    p4 = out / "p4"
    if not (p4 / "src" / "jasmine_core").is_dir():
        extract_core(args.ref, p4)
    workspace = out / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "source.txt").write_text("initial\n", encoding="utf-8")
    (workspace / "report.txt").write_text("all green\n", encoding="utf-8")
    db_path = out / "core.db"

    sys.path.insert(0, str(p4 / "src"))
    sys.path.insert(0, str(p4 / "tests"))
    os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(workspace)
    os.environ["JASMINE_CORE_FINGERPRINT_EXTRA_PATHS"] = '["source.txt"]'
    os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = "single_user"
    os.environ.pop("JASMINE_HINDSIGHT_URL", None)

    from jasmine_core import SCHEMA_VERSION, auth, db, registry
    from jasmine_core.ids import new_id
    from jasmine_core.memory import hindsight as hindsight_module
    from jasmine_core.memory import jobs as jobs_module
    from jasmine_core.memory.eligibility import SourceRegistry
    from jasmine_core.memory.store import MemoryStore
    from jasmine_core.memory.worker import MemoryWorker
    from jasmine_core.migrations import migrate

    import memory_support

    conn = db.connect(db_path)
    migrate(conn, log=lambda line: log(line))

    # P4's own test fixture creates Truth as its fixed `HOST`/`HUMAN` ids, so the human and the
    # host come from there rather than from freshly minted ones; the worker and the agent are
    # ours, and the fixture is told about them.
    host_id = memory_support.HOST
    human_id = memory_support.HUMAN
    system_id = new_id("act")
    agent_id = new_id("act")
    with db.transaction(conn):
        reg = registry.Registry(conn)
        reg.upsert_host(host_id, display_name="p6 real-core host")
        reg.upsert_actor(human_id, kind="human", display_name="p6 operator",
                         home_host_id=host_id)
        reg.upsert_actor(system_id, kind="system", display_name="p6 worker",
                         home_host_id=host_id)
        reg.upsert_actor(agent_id, kind="agent", display_name="p6 agent",
                         home_host_id=host_id)

    # Keys, through P4's own issuance rules — including the ones that refuse.
    issued: dict[str, dict[str, Any]] = {}
    keys = auth.Auth(conn)
    actor_for = {"human_manage": human_id, "human_readonly": human_id,
                 "system_manage": system_id, "agent_read": agent_id}
    for name, scopes in KEY_SCOPES.items():
        record = keys.issue_key(actor_id=actor_for[name], label=f"p6 {name}", scopes=list(scopes))
        issued[name] = {"token": record["token"], "key_id": record.get("key_id") or
                        record.get("id"), "actor_id": actor_for[name], "scopes": scopes}
    # The refusal the fixture could not model: an agent key carrying a manage scope.
    issuance_refusals: list[dict[str, Any]] = []
    for scope in ("memory:manage", "memory:reflect"):
        try:
            keys.issue_key(actor_id=agent_id, label=f"p6 agent {scope}",
                           scopes=["objects:read", "memory:read", scope])
            issuance_refusals.append({"scope": scope, "issued": True})
        except Exception as error:  # noqa: BLE001 - the refusal is the observation
            issuance_refusals.append({
                "scope": scope, "issued": False,
                "code": getattr(error, "code", error.__class__.__name__),
                "status": getattr(error, "status", None),
            })

    # Real Truth, through P4's own stores, using P4's own test fixture for the shapes.
    class Seed(memory_support.MemoryTestCase):
        def setUp(self) -> None:  # noqa: N802 - TestCase API, deliberately not run
            """Nothing: this environment owns the connection and the paths, not the fixture."""

    seed = Seed("setUp")
    seed.conn = conn
    seed.system_actor = system_id
    seed.agent_actor = agent_id
    seed.transport = None
    seed._workspace = None  # noqa: SLF001 - the fixture's own field
    project_id = seed.project(name="p6 realcore")
    task_id = seed.task(project_id, title="verify the write slice")
    step_id, evidence_id, transition_event = seed.verified_step(project_id=project_id,
                                                               task_id=task_id)
    rule_id, rule_origin = seed.active_decision(project_id=project_id, task_id=task_id)
    # `failed_step` opens with `expected_revision: 1` on the Task, which is only true before
    # anything else touched it — so the failure path gets its own Task rather than a retry that
    # would look like a fixture bug.
    failure_task_id = seed.task(project_id, title="failure path")
    failed_step = seed.failed_step(project_id=project_id, task_id=failure_task_id)

    # The engine, and the real worker over a real socket.
    engine = memory_support.StubHindsightServer()
    os.environ["JASMINE_HINDSIGHT_URL"] = engine.base_url
    transport = hindsight_module.HindsightTransport(hindsight_module.config_from_env())
    store = MemoryStore(conn, schema_version=SCHEMA_VERSION)
    scan = jobs_module.JobStore(conn, store).enqueue_from_sources(
        SourceRegistry(), limit=100, max_attempts=3)
    worker = MemoryWorker(conn, store, transport, max_attempts=3, log=log)
    ticks = 0
    for report in worker.run(max_ticks=args.max_ticks, log=log):
        ticks += 1
        log(f"worker tick {ticks}: {report.as_dict()}")

    # A retain that really fails, so the environment holds a row with engine text on it. Without
    # one, the withholding rule is unobservable: a healthy item's `safe_error_summary` is None for
    # the human too, and "the agent saw nothing" would prove nothing. The engine is armed to be
    # unreachable for one bounded worker pass with a single attempt, so the job settles FAILED.
    failure_task = seed.task(project_id, title="retain failure")
    seed.verified_step(project_id=project_id, task_id=failure_task)
    # The engine's own `unreachable` knob only covers recall, so the failure is produced the way a
    # real outage produces it: a transport pointed at a port nothing is listening on.
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()
    dead_url = f"http://127.0.0.1:{dead_port}"
    dead_transport = hindsight_module.HindsightTransport(
        hindsight_module.HindsightConfig(dead_url))
    log(f"failure pass: engine transport at {dead_url} (nothing listening)")
    jobs_module.JobStore(conn, store).enqueue_from_sources(SourceRegistry(), limit=100,
                                                           max_attempts=1)
    failing_worker = MemoryWorker(conn, store, dead_transport, max_attempts=1, log=log)
    for report in failing_worker.run(max_ticks=args.max_ticks, log=log):
        log(f"failing-pass tick: {report.as_dict()}")

    items = conn.execute("SELECT memory_id, status, revision, pinned, memory_type, project_id,"
                         " engine_state, last_error_code, safe_error_summary"
                         " FROM memory_items").fetchall()
    jobs = conn.execute("SELECT job_id, kind, status, memory_id, retry_count,"
                        " document_id, content_sha256, bank_id, last_error_code,"
                        " safe_error_summary FROM memory_jobs").fetchall()
    traces = conn.execute("SELECT trace_id, mode, status, project_id FROM memory_query_traces"
                          ).fetchall() if _has_table(conn, "memory_query_traces") else []

    descriptor = {
        "environment": "isolated real P4 Core with a wire-double engine",
        "mode": {
            "core": "real-p4",
            "engine": "boundary-fake",
            "engine_note": "tests/memory_support.StubHindsightServer over a real socket; P4's "
                           "own Gate requires mode: real and a real deployment, so this is not a "
                           "P4 Gate verdict and P4 phase acceptance is not claimed",
        },
        "ref": args.ref,
        "schema_version": SCHEMA_VERSION,
        "out": str(out),
        "db": str(db_path),
        "workspace": str(workspace),
        "engine_url": engine.base_url,
        "core_origin": f"http://127.0.0.1:{args.port}",
        "host_id": host_id,
        "actors": {"human": human_id, "system": system_id, "agent": agent_id},
        "keys": {name: {"key_id": record["key_id"], "actor_id": record["actor_id"],
                        "scopes": record["scopes"]} for name, record in issued.items()},
        "issuance_refusals": issuance_refusals,
        "seeded": {"project_id": project_id, "task_id": task_id, "step_id": step_id,
                   "evidence_id": evidence_id, "verified_transition_event": transition_event,
                   "rule_id": rule_id, "rule_origin_event": rule_origin,
                   "failed_step_id": failed_step, "failure_task_id": failure_task_id},
        "scan": scan,
        "worker_ticks": ticks,
        "items": [dict(row) for row in items],
        "jobs": [dict(row) for row in jobs],
        "traces": [dict(row) for row in traces],
    }
    (out / "tokens.json").write_text(
        json.dumps({name: record["token"] for name, record in issued.items()}, indent=2),
        encoding="utf-8")
    os.chmod(out / "tokens.json", 0o600)
    (out / "descriptor.json").write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    os.chmod(out / "descriptor.json", 0o600)
    return {"descriptor": descriptor, "engine": engine, "db_path": db_path}


def _has_table(conn: Any, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                       ).fetchone()
    return row is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build", help="create the isolated Core and (unless --seed-only) serve it")
    build_parser.add_argument("--out", type=Path, required=True,
                              help="the isolated directory; created if missing")
    build_parser.add_argument("--ref", default=DEFAULT_REF, help="the pinned P4 commit")
    build_parser.add_argument("--port", type=int, default=8791, help="loopback port for the Core")
    build_parser.add_argument("--max-ticks", type=int, default=20,
                              help="worker ticks to run while seeding")
    build_parser.add_argument("--seed-only", action="store_true",
                              help="stop after building, before serving")
    build_parser.add_argument("--reuse", action="store_true",
                              help="build inside a non-empty --out")
    args = parser.parse_args(argv)

    if args.command != "build":
        parser.error(f"unknown command {args.command}")
    banner(args.ref, args.out)
    built = build(args)
    descriptor = built["descriptor"]
    log(f"schema {descriptor['schema_version']}; items={len(descriptor['items'])} "
        f"jobs={len(descriptor['jobs'])} traces={len(descriptor['traces'])} "
        f"worker_ticks={descriptor['worker_ticks']}")
    for entry in descriptor["issuance_refusals"]:
        log(f"issuance {entry['scope']}: issued={entry['issued']} "
            f"code={entry.get('code')} status={entry.get('status')}")
    if not descriptor["items"]:
        log("NO MEMORY ITEM WAS RETAINED — the write checks would be BLOCKED, not passed")
    if args.seed_only:
        log(f"seed-only: descriptor at {args.out / 'descriptor.json'}; engine stopped")
        built["engine"].stop()
        return 0

    from jasmine_core.api.server import serve
    log(f"serving the real P4 Core on http://127.0.0.1:{args.port} "
        f"(engine wire double at {descriptor['engine_url']})")
    try:
        serve(descriptor["db"], host="127.0.0.1", port=args.port,
              log=lambda line: log(line))
    except KeyboardInterrupt:
        pass
    finally:
        built["engine"].stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
