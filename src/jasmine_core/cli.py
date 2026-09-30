"""Command line entry point.

P0-01 ships `migrate` and `schema`; the HTTP `serve` command arrives with
P0-03. Anything that needs a secret takes it from the environment, never from
argv, so tokens do not land in shell history or in `ps` output.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__, db, errors, ids
from .migrations import applied_migrations, check_version, current_version, migrate, verify_checksums
from .registry import ACTOR_KINDS as AUTH_ACTOR_KINDS

DB_ENV_VAR = "JASMINE_CORE_DB"

#: Error code -> process exit code, so a script can tell "refused" (3) from
#: "the database is busy" (4) from a crash (1).
EXIT_CODES = {
    "schema_version_unsupported": 3,
    "migration_conflict": 3,
    "database_busy": 4,
}


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _resolve_db(value: str | None) -> Path:
    # Read the environment at call time so tests and callers can change it.
    chosen = value or os.environ.get(DB_ENV_VAR, "")
    if not chosen:
        raise SystemExit(f"no database path: pass --db or set {DB_ENV_VAR}")
    return Path(chosen).expanduser()


def cmd_migrate(args: argparse.Namespace) -> int:
    path = _resolve_db(args.db)
    conn = db.connect(path)
    try:
        applied = migrate(conn, log=_log)
        # Report what is on disk, not what this build happens to declare.
        on_disk = current_version(conn)
    finally:
        conn.close()
    print(json.dumps(
        {"db": str(path), "applied": applied, "schema_version": on_disk,
         "core_schema_version": SCHEMA_VERSION},
        ensure_ascii=False,
    ))
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    path = _resolve_db(args.db)
    conn = db.connect(path)
    try:
        check_version(conn)
        drift = verify_checksums(conn)
        payload: dict[str, Any] = {
            "db": str(path),
            "schema_version": current_version(conn),
            "expected_schema_version": SCHEMA_VERSION,
            "core_version": __version__,
            "migrations": applied_migrations(conn),
            "drift": drift,
        }
    finally:
        conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if drift:
        _log("error: migration drift detected; the database was written by a different build")
        return 3
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .api.server import serve

    path = _resolve_db(args.db)
    serve(path, host=args.host, port=args.port, log=_log)
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Create (or reuse) a host, an actor and an API key, and print the token.

    This is the P0 setup path for the capture entry: the hook needs a `host_id`
    that exists and a token whose actor exists, and neither can be guessed. The
    token is printed once and only its SHA-256 is stored.
    """
    from . import auth, registry

    path = _resolve_db(args.db)
    conn = db.connect(path)
    try:
        migrate(conn, log=_log)
        host_id = args.host_id or ids.new_id("hst")
        actor_id = args.actor_id or ids.new_id("act")
        reg = registry.Registry(conn)
        with db.transaction(conn):
            reg.upsert_host(host_id, display_name=args.host_name or host_id)
            reg.upsert_actor(actor_id, kind=args.actor_kind, display_name=args.actor_name or actor_id,
                             home_host_id=host_id)
        issued = auth.Auth(conn).issue_key(actor_id=actor_id, label=args.label,
                                           scopes=list(args.scopes))
    finally:
        conn.close()
    print(json.dumps({
        "db": str(path),
        "host_id": host_id,
        "actor_id": actor_id,
        "key_id": issued["key_id"],
        "token": issued["token"],
        "scopes": issued["scopes"],
        "warning": issued["warning"],
        "next": "export JASMINE_CORE_TOKEN=<token> JASMINE_CORE_HOST_ID=" + host_id,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    from . import registry

    path = _resolve_db(args.db)
    conn = db.connect(path)
    try:
        reg = registry.Registry(conn)
        with db.transaction(conn):
            if args.kind == "host":
                record = reg.upsert_host(args.id, display_name=args.name, kind=args.host_kind)
            else:
                record = reg.upsert_actor(args.id, kind=args.actor_kind, display_name=args.name,
                                          home_host_id=args.home_host_id)
    finally:
        conn.close()
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


# -- P4: Memory ---------------------------------------------------------------
#
# These exist because the Memory worker is a separate service in the deployment
# topology (design §13) and because recovery has to be runnable by an operator
# without a Dashboard. They call the same service layer the API uses; there is no
# CLI path that writes a job or a Memory row directly (design module §20).

#: Set by SIGINT/SIGTERM so an in-flight tick finishes and the current lease is
#: left to expire rather than being abandoned mid-retain.
_STOP = threading.Event()


def _install_stop_handler() -> None:
    def _handler(signum, frame):  # noqa: ARG001
        _STOP.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            pass


def _memory_conn(args: argparse.Namespace):
    from .migrations import migrate as run_migrate

    path = _resolve_db(args.db)
    conn = db.connect(path)
    run_migrate(conn, log=_log)
    return conn


def _memory_transport():
    from .memory.hindsight import HindsightTransport, config_from_env

    config = config_from_env()
    return HindsightTransport(config) if config is not None else None


def cmd_memory(args: argparse.Namespace) -> int:
    from .memory import health as health_module
    from .memory import jobs as jobs_module
    from .memory import scope as scope_module
    from .memory.eligibility import SourceRegistry
    from .memory.store import MemoryStore

    conn = _memory_conn(args)
    try:
        store = MemoryStore(conn, schema_version=0)
        job_store = jobs_module.JobStore(conn, store)
        command = args.memory_command
        if command == "scan":
            report = job_store.enqueue_from_sources(
                SourceRegistry(), limit=args.limit, max_attempts=args.max_attempts
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if command == "reset-cursor":
            # Replay the whole Event stream. Safe by construction: the unique
            # index makes every already-known source a no-op.
            job_store.reset_cursor(args.seq)
            print(json.dumps({"cursor": job_store.cursor()}, indent=2))
            return 0
        if command == "jobs":
            rows = job_store.jobs(project_id=args.project_id, status=args.status,
                                  kind=args.kind, limit=args.limit)
            print(json.dumps({"jobs": rows, "count": len(rows),
                              "counts": job_store.counts(project_id=args.project_id)},
                             ensure_ascii=False, indent=2))
            return 0
        if command == "attempts":
            job = job_store.job(args.job_id)
            if job is None:
                _log(f"error: memory_job_not_found: {args.job_id}")
                return 3
            print(json.dumps({"job": job, "attempts": job_store.attempts(args.job_id)},
                             ensure_ascii=False, indent=2))
            return 0
        if command == "health":
            payload = health_module.report(conn, store, _memory_transport(), job_store,
                                           mode=scope_module.deployment_mode(),
                                           probe=args.probe)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        _log(f"error: unknown memory command: {command}")
        return 3
    finally:
        conn.close()


def cmd_memory_worker(args: argparse.Namespace) -> int:
    """Run the Memory worker: the only process that writes to the engine."""
    from .memory.store import MemoryStore
    from .memory.worker import MemoryWorker

    _install_stop_handler()
    conn = _memory_conn(args)
    try:
        worker = MemoryWorker(conn, MemoryStore(conn, schema_version=0), _memory_transport(),
                              lease_seconds=args.lease_seconds,
                              max_attempts=args.max_attempts)
        if args.max_ticks is not None:
            for report in worker.run(max_ticks=args.max_ticks, log=_log):
                print(json.dumps(report.as_dict(), ensure_ascii=False))
            return 0
        _log(f"jasmine-core memory worker {worker.lease_owner}: running; Ctrl-C to stop")
        worker.run(log=_log, should_stop=_STOP.is_set)
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jasmine-core", description="Jasmine Core V1 baseline")
    parser.add_argument("--version", action="version", version=f"jasmine-core {__version__} (schema v{SCHEMA_VERSION})")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate_parser = sub.add_parser("migrate", help="create or upgrade core.db from an empty database")
    migrate_parser.add_argument("--db", help="path to core.db (or set JASMINE_CORE_DB)")
    migrate_parser.set_defaults(func=cmd_migrate)

    schema_parser = sub.add_parser("schema", help="print the applied schema version and migrations")
    schema_parser.add_argument("--db", help="path to core.db (or set JASMINE_CORE_DB)")
    schema_parser.set_defaults(func=cmd_schema)

    serve_parser = sub.add_parser("serve", help="run the Core API on loopback")
    serve_parser.add_argument("--db", help="path to core.db (or set JASMINE_CORE_DB)")
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="bind address; only loopback is permitted in P0")
    serve_parser.add_argument("--port", type=int, default=8787)
    serve_parser.set_defaults(func=cmd_serve)

    boot_parser = sub.add_parser(
        "bootstrap", help="create a host, an actor and an API key for the capture entry"
    )
    boot_parser.add_argument("--db")
    boot_parser.add_argument("--host-id", help="reuse an existing hst_ id instead of generating one")
    boot_parser.add_argument("--host-name")
    boot_parser.add_argument("--actor-id", help="reuse an existing act_ id instead of generating one")
    boot_parser.add_argument("--actor-name")
    boot_parser.add_argument("--actor-kind", default="human", choices=list(AUTH_ACTOR_KINDS))
    boot_parser.add_argument("--label", default="capture")
    boot_parser.add_argument("--scopes", nargs="+", default=["events:write", "events:read"])
    boot_parser.set_defaults(func=cmd_bootstrap)

    register_parser = sub.add_parser("register", help="register or update a host or an actor")
    register_parser.add_argument("--db")
    register_parser.add_argument("kind", choices=["host", "actor"])
    register_parser.add_argument("id", help="an existing hst_ or act_ id")
    register_parser.add_argument("--name")
    register_parser.add_argument("--host-kind", default="workstation")
    register_parser.add_argument("--actor-kind", default="human", choices=list(AUTH_ACTOR_KINDS))
    register_parser.add_argument("--home-host-id")
    register_parser.set_defaults(func=cmd_register)

    memory_parser = sub.add_parser(
        "memory", help="long-term Memory operations: recovery scan, jobs, health"
    )
    memory_parser.add_argument("--db", help="path to core.db (or set JASMINE_CORE_DB)")
    # Set on the group, not on each leaf: the leaf only names which operation to
    # run, and `main` dispatches through `func` exactly as the other commands do.
    memory_parser.set_defaults(func=cmd_memory)
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)

    scan_parser = memory_sub.add_parser(
        "scan",
        help="create PENDING jobs for eligible committed sources that have none",
    )
    scan_parser.add_argument("--limit", type=int, default=200)
    scan_parser.add_argument("--max-attempts", type=int, default=5)

    cursor_parser = memory_sub.add_parser(
        "reset-cursor", help="move the recovery scan cursor (a full replay is safe)"
    )
    cursor_parser.add_argument("--seq", type=int, default=0)

    jobs_parser = memory_sub.add_parser("jobs", help="list durable Memory jobs")
    jobs_parser.add_argument("--project-id")
    jobs_parser.add_argument("--status", choices=["PENDING", "RUNNING", "DONE", "FAILED"])
    jobs_parser.add_argument("--kind", choices=["retain", "delete"])
    jobs_parser.add_argument("--limit", type=int, default=100)

    attempts_parser = memory_sub.add_parser("attempts",
                                            help="the append-only history of one job")
    attempts_parser.add_argument("job_id")

    health_parser = memory_sub.add_parser("health", help="Memory availability, apart from Core")
    health_parser.add_argument("--probe", action="store_true",
                               help="contact the engine now instead of reporting the last probe")

    worker_parser = sub.add_parser(
        "memory-worker", help="run the Memory Job Worker (the only writer to the engine)"
    )
    worker_parser.add_argument("--db", help="path to core.db (or set JASMINE_CORE_DB)")
    worker_parser.add_argument("--max-ticks", type=int,
                               help="stop after N passes instead of running until stopped")
    worker_parser.add_argument("--lease-seconds", type=int, default=120)
    worker_parser.add_argument("--max-attempts", type=int, default=5)
    worker_parser.set_defaults(func=cmd_memory_worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except errors.CoreError as exc:
        # Refusals are a normal outcome, not a crash: one clean line, no traceback.
        _log(f"error: {exc.code}: {exc.message}")
        return EXIT_CODES.get(exc.code, 3)


if __name__ == "__main__":
    raise SystemExit(main())
