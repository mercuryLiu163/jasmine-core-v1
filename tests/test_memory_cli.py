"""P4: the Memory CLI. Recovery has to be runnable without a Dashboard.

Same pattern as the P0 CLI tests: `cli.main(argv)` is invoked in-process with
stdout and stderr captured, so argument parsing, the exit-code mapping and the
JSON output are the real ones. The engine is never reachable from these commands
except through the worker, and the worker reports honestly when there is no
endpoint.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401  (puts `src` on sys.path for the CLI under test)

from jasmine_core import SCHEMA_VERSION, cli, db, ids  # noqa: E402
from jasmine_core.memory.store import MemoryStore  # noqa: E402

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
HUMAN = "act_01K742SG00CBKP25A9ETPBTRMJ"


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-memory-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "nested" / "core.db"
        self._previous = os.environ.get("JASMINE_CORE_DB")
        os.environ["JASMINE_CORE_DB"] = str(self.db_path)
        self.addCleanup(self._restore_env)
        for name in ("JASMINE_HINDSIGHT_URL", "JASMINE_HINDSIGHT_API_KEY",
                     "JASMINE_MEMORY_DEPLOYMENT_MODE"):
            os.environ.pop(name, None)
        cli.main(["migrate"])

    def _restore_env(self) -> None:
        if self._previous is None:
            os.environ.pop("JASMINE_CORE_DB", None)
        else:
            os.environ["JASMINE_CORE_DB"] = self._previous

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self) -> str:
        """A project, a task and a human-confirmed Decision: real P1 truth."""
        from jasmine_core import clock, registry
        from jasmine_core.authority import AuthorityStore
        from jasmine_core.models import NewEvent, NewObject
        from jasmine_core.objects import ObjectStore

        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with db.transaction(conn):
            reg = registry.Registry(conn)
            reg.upsert_host(HOST, display_name="cli host")
            reg.upsert_actor(HUMAN, kind="human", display_name="cli", home_host_id=HOST)
        objects = ObjectStore(conn, schema_version=SCHEMA_VERSION)
        project_id = objects.create(NewObject.project({"name": "cli", "host_id": HOST}),
                                    actor_id=HUMAN)["object"]["project_id"]
        from jasmine_core.events import EventStore

        with db.transaction(conn):
            stored, _ = EventStore(conn, schema_version=SCHEMA_VERSION).append(NewEvent(
                event_type="user.prompt", source_system="cli", source_event_id=None,
                occurred_at=clock.now(), actor_id=HUMAN, actor_kind="human", host_id=HOST,
                project_id=project_id, payload={"text": "use hindsight"}))
        authority = AuthorityStore(conn, schema_version=SCHEMA_VERSION)
        proposed = authority.propose({
            "rule_key": "memory.engine", "kind": "DECISION", "severity": "HARD",
            "enforcement": "CONTEXT", "content": "V1 uses Hindsight", "matcher": {},
            "scope": {"kind": "project", "project_id": project_id},
            "origin_event_id": stored["event_id"], "host_id": HOST}, actor_id=HUMAN)
        authority.transition(proposed["rule"]["rule_id"], "approve",
                             {"expected_revision": 1, "host_id": HOST}, actor_id=HUMAN)
        return project_id


class MemoryCommands(CliTestCase):
    def test_scan_creates_pending_jobs_and_is_idempotent(self) -> None:
        self.seed()
        code, output, _ = self._run(["memory", "scan"])
        self.assertEqual(code, 0)
        first = json.loads(output)
        self.assertEqual(len(first["created"]), 1)
        self.assertGreater(first["cursor_after"], 0)
        code, output, _ = self._run(["memory", "scan"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["created"], [])

    def test_reset_cursor_replays_the_whole_stream_safely(self) -> None:
        self.seed()
        self._run(["memory", "scan"])
        code, output, _ = self._run(["memory", "reset-cursor", "--seq", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["cursor"], 0)
        code, output, _ = self._run(["memory", "scan"])
        self.assertEqual(json.loads(output)["created"], [],
                         "a full replay re-derives nothing and loses nothing")
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(len(MemoryStore(conn, schema_version=SCHEMA_VERSION).items()), 1)

    def test_jobs_lists_and_counts(self) -> None:
        self.seed()
        self._run(["memory", "scan"])
        code, output, _ = self._run(["memory", "jobs"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["counts"]["pending"], 1)
        job_id = payload["jobs"][0]["job_id"]
        code, output, _ = self._run(["memory", "attempts", job_id])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["attempts"], [])

    def test_a_status_filter_is_validated_by_argparse(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self._run(["memory", "jobs", "--status", "RETRYING"])

    def test_attempts_for_an_unknown_job_exits_three(self) -> None:
        code, _, err = self._run(["memory", "attempts", ids.new_id("mjb")])
        self.assertEqual(code, 3)
        self.assertIn("memory_job_not_found", err)

    def test_health_reports_unconfigured_and_names_no_credential(self) -> None:
        code, output, _ = self._run(["memory", "health"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["status"], "unconfigured")
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["core_impact"], "none")
        self.assertNotIn("JASMINE_HINDSIGHT", output)

    def test_a_probed_health_report_never_prints_the_credential(self) -> None:
        with patch.dict(os.environ, {
            "JASMINE_HINDSIGHT_URL": "http://user:hunter2@127.0.0.1:1",
            "JASMINE_HINDSIGHT_API_KEY": "super-secret-token",
            "JASMINE_HINDSIGHT_TIMEOUT_SECONDS": "1",
        }):
            code, output, _ = self._run(["memory", "health", "--probe"])
        self.assertEqual(code, 0)
        self.assertNotIn("hunter2", output)
        self.assertNotIn("super-secret-token", output)
        payload = json.loads(output)
        self.assertEqual(payload["origin_host"], "http://127.0.0.1:1")
        self.assertIn(payload["status"], ("unreachable", "degraded", "failing"))

    def test_the_worker_without_an_engine_queues_and_reports(self) -> None:
        self.seed()
        code, output, _ = self._run(["memory-worker", "--max-ticks", "2"])
        self.assertEqual(code, 0)
        reports = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        self.assertEqual(reports[0]["outcome"], "failed")
        self.assertEqual(reports[0]["error_code"], "engine_not_configured")
        # An unconfigured engine is a *permanent* failure, not a retryable one:
        # retrying cannot configure anything, and a silent retry loop against an
        # endpoint the operator never set is worse than a FAILED job an operator
        # can see and requeue after configuring the engine.
        from jasmine_core.memory import jobs as jobs_module

        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        self.assertEqual(store.counts()["failed"], 1)
        job = store.jobs()[0]
        self.assertEqual(job["last_error_code"], "engine_not_configured")
        # The Memory item survives, and the job is requeueable by an operator.
        self.assertEqual(MemoryStore(conn, schema_version=SCHEMA_VERSION).items()[0]["status"],
                         "ACTIVE")

    def test_a_malformed_subcommand_is_refused(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self._run(["memory", "purge-everything"])

    def test_the_schema_still_reports_the_p4_migration(self) -> None:
        code, output, _ = self._run(["schema"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertIn("m0011_memory", [m["name"] for m in payload["migrations"]])
        self.assertEqual(payload["drift"], [])


class TransportFromTheEnvironment(unittest.TestCase):
    def test_no_endpoint_means_no_transport(self) -> None:
        from jasmine_core.cli import _memory_transport

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_memory_transport())

    def test_a_configured_endpoint_builds_a_transport(self) -> None:
        from jasmine_core.cli import _memory_transport

        with patch.dict(os.environ, {"JASMINE_HINDSIGHT_URL": "http://127.0.0.1:8888"}):
            transport = _memory_transport()
            self.assertIsNotNone(transport)
            self.assertEqual(transport.config.origin_host, "http://127.0.0.1:8888")
            self.assertFalse(transport.config.has_credential)
