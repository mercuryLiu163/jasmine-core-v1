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

from . import SCHEMA_VERSION, __version__, db, errors
from .migrations import applied_migrations, check_version, current_version, migrate, verify_checksums

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
    from .api.server import serve  # imported lazily; the API layer is a later P0 PR

    path = _resolve_db(args.db)
    serve(path, host=args.host, port=args.port, log=_log)
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
