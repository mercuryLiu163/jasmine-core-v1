"""The CLI is the documented way to create core.db, so it is part of P0-T01."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import SRC  # noqa: F401  (path setup)

from jasmine_core import SCHEMA_VERSION, cli  # noqa: E402


class MigrateCommand(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "nested" / "core.db"
        self.env = dict(os.environ, JASMINE_CORE_DB=str(self.db_path))
        self._previous = os.environ.get("JASMINE_CORE_DB")
        os.environ["JASMINE_CORE_DB"] = str(self.db_path)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._previous is None:
            os.environ.pop("JASMINE_CORE_DB", None)
        else:
            os.environ["JASMINE_CORE_DB"] = self._previous

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_migrate_creates_the_database_and_reports_the_version(self) -> None:
        code, output, _ = self._run(["migrate"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["applied"], ["m0001_baseline", "m0002_api_auth_audit"])
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertTrue(self.db_path.exists())

    def test_migrate_is_idempotent_from_the_command_line(self) -> None:
        self._run(["migrate"])
        code, output, _ = self._run(["migrate"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["applied"], [])

    def test_schema_reports_the_applied_migrations(self) -> None:
        self._run(["migrate"])
        code, output, _ = self._run(["schema"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["migrations"][0]["name"], "m0001_baseline")
        self.assertEqual(len(payload["migrations"][0]["checksum"]), 64)
        self.assertEqual(payload["drift"], [])

    def _force_schema_version(self, value: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE core_meta SET value = ? WHERE key = 'schema_version'", (value,))
        conn.commit()
        conn.close()

    def test_schema_refuses_a_database_from_the_future(self) -> None:
        self._run(["migrate"])
        self._force_schema_version(str(SCHEMA_VERSION + 5))
        code, out, err = self._run(["schema"])
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("newer than this build supports", err)
        self.assertNotIn("Traceback", err)

    def test_migrate_also_refuses_a_database_from_the_future(self) -> None:
        # Regression: migrate skipped the version guard, exited 0, and reported
        # this build's SCHEMA_VERSION for a database that was not on it.
        self._run(["migrate"])
        self._force_schema_version("99")
        code, out, err = self._run(["migrate"])
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("newer than this build supports", err)

    def test_migrate_reports_the_on_disk_version_not_the_build_constant(self) -> None:
        self._run(["migrate"])
        code, out, _ = self._run(["migrate"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["core_schema_version"], SCHEMA_VERSION)

    def test_schema_flags_a_tampered_checksum(self) -> None:
        self._run(["migrate"])
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE schema_migrations SET checksum = ? WHERE name = 'm0001_baseline'",
                     ("0" * 64,))
        conn.commit()
        conn.close()
        code, out, err = self._run(["schema"])
        self.assertEqual(code, 3)
        self.assertTrue(json.loads(out)["drift"])
        self.assertIn("drift", err)

    def test_database_file_is_created_in_wal_mode(self) -> None:
        self._run(["migrate"])
        conn = sqlite3.connect(self.db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_missing_database_path_fails_with_a_message(self) -> None:
        os.environ.pop("JASMINE_CORE_DB", None)
        with self.assertRaises(SystemExit):
            self._run(["migrate"])


if __name__ == "__main__":
    unittest.main()
