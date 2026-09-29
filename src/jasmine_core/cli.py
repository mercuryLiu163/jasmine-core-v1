"""Command line entry point.

P0-01 ships `migrate` and `schema`; the HTTP `serve` command arrives with
P0-03. Anything that needs a secret takes it from the environment, never from
argv, so tokens do not land in shell history or in `ps` output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
