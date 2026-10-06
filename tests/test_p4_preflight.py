"""The functional engine preflight: each check, each refusal, no residue.

The preflight exists because `/version` answering is not the engine working. It
runs the real chain -- database connected, retain, extraction, read-back, recall
-- and these tests drive it with a stub transport: every failing check has to
block by name, cleanup has to run after a successful retain on every exit path,
the report may not contain URL credentials, and an operator's broken URL is an
environment BLOCKED (exit 2), never a defect in the script (exit 1).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO / "scripts" / "p4-memory-preflight.py"


def _preflight_module():
    """Load `scripts/p4-memory-preflight.py` by path, once per process."""
    name = "p4_memory_preflight"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PREFLIGHT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeClock:
    """A monotonic source whose values the test dictates."""

    def __init__(self, values):
        self._values = list(values)
        self._last = 0.0

    def __call__(self) -> float:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


class StubTransport:
    """A stand-in for `HindsightTransport`; no network, every leg controllable."""

    def __init__(self, *, unit_count=1, units=None, retain_error=None,
                 document_error=None, recall_results=None, delete_error=None,
                 document_missing=False):
        self.unit_count = unit_count
        self.units = ([{"id": "unit-1"}] if units is None else units)
        self.retain_error = retain_error
        self.document_error = document_error
        self.recall_results = recall_results
        self.delete_error = delete_error
        self.document_missing = document_missing
        self.retained = None
        self.deleted: list[str] = []
        self.delete_calls = 0

    def version(self):
        return {"api_version": "0.10.2"}

    def retain(self, **kwargs):
        if self.retain_error is not None:
            raise self.retain_error
        self.retained = kwargs
        return {"items_count": 1, "operation_id": "op-1"}

    def get_document(self, *, bank, document_id):
        if self.document_error is not None:
            raise self.document_error
        if self.document_missing or document_id in self.deleted:
            return None
        return {"id": document_id, "memory_unit_count": self.unit_count}

    def list_all_document_memories(self, *, bank, document_id, state=None):
        return list(self.units)

    def recall(self, **kwargs):
        if self.recall_results is not None:
            return list(self.recall_results)
        document_id = (self.retained or {}).get("document_id")
        return [SimpleNamespace(document_id=document_id, text="")]

    def delete_document(self, *, bank, document_id):
        self.delete_calls += 1
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(document_id)


HEALTHY = {"status": "ok", "database": "connected"}


class PreflightTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _preflight_module()
        self._saved_namespace = os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)

        def restore():
            if self._saved_namespace is None:
                os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)
            else:
                os.environ["JASMINE_MEMORY_BANK_NAMESPACE"] = self._saved_namespace

        self.addCleanup(restore)

    def _run(self, transport, *, health=None, namespace="preflight-test",
             monotonic=None, keep=False, engine="http://127.0.0.1:9", timeout=5.0):
        health_getter = (lambda origin, seconds: dict(HEALTHY) if health is None
                         else health(origin, seconds))
        return self.module.run_preflight(
            engine, namespace, timeout, keep,
            transport=transport, health_getter=health_getter,
            sleeper=lambda seconds: None,
            monotonic=monotonic or FakeClock([0.0, 0.0, 0.0, 0.0]))


class EveryCheckPassesOrBlocksByName(PreflightTestCase):
    def test_a_functional_engine_passes_all_five_checks(self):
        transport = StubTransport()
        report = self._run(transport)
        self.assertEqual(report["verdict"], "PASS")
        self.assertIsNone(report["failed_check"])
        self.assertEqual(set(report["checks"]), set(self.module.CHECKS))
        self.assertTrue(all(check["passed"] for check in report["checks"].values()))
        self.assertEqual(report["engine"], "http://127.0.0.1:9")
        self.assertEqual(report["bank_namespace"], "preflight-test")
        self.assertTrue(report["cleanup"]["performed"])
        self.assertTrue(report["cleanup"]["document_gone"])

    def test_a_dead_database_blocks_at_database_connected(self):
        report = self._run(StubTransport(),
                           health=lambda *_: {"status": "ok", "database": "broken"})
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["failed_check"], "database_connected")

    def test_an_unreachable_health_endpoint_blocks_at_database_connected(self):
        def unreachable(origin, seconds):
            raise OSError("connection refused")

        report = self._run(StubTransport(), health=unreachable)
        self.assertEqual(report["failed_check"], "database_connected")
        self.assertFalse(report["checks"]["database_connected"]["passed"])

    def test_a_refused_retain_blocks_at_real_retain(self):
        report = self._run(StubTransport(retain_error=RuntimeError("engine said no")))
        self.assertEqual(report["failed_check"], "real_retain")
        self.assertIn("engine said no", report["checks"]["real_retain"]["error"])

    def test_a_document_stuck_at_zero_units_blocks_at_extraction(self):
        report = self._run(StubTransport(unit_count=0, units=[]),
                           monotonic=FakeClock([0.0, 100.0]))
        self.assertEqual(report["failed_check"], "extraction_complete")
        self.assertEqual(report["checks"]["extraction_complete"]["memory_unit_count"], 0)

    def test_a_units_read_back_that_disagrees_blocks_at_read_back(self):
        report = self._run(StubTransport(unit_count=2, units=[{"id": "unit-1"}]))
        self.assertEqual(report["failed_check"], "document_units_read_back")

    def test_an_unrecallable_document_blocks_at_recall(self):
        report = self._run(StubTransport(recall_results=[]))
        self.assertEqual(report["failed_check"], "recall")

    def test_a_recalled_document_binds_by_the_retained_id(self):
        report = self._run(StubTransport())
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["checks"]["recall"]["bound_to_document"], 1)


class CleanupIsNotOneOfTheFiveChecks(PreflightTestCase):
    def test_a_blocked_extraction_still_attempts_cleanup(self):
        transport = StubTransport(unit_count=0, units=[])
        report = self._run(transport, monotonic=FakeClock([0.0, 100.0]))
        self.assertEqual(report["failed_check"], "extraction_complete")
        self.assertEqual(transport.delete_calls, 1)
        self.assertTrue(report["cleanup"]["performed"])
        self.assertTrue(report["cleanup"]["document_gone"])

    def test_a_failed_delete_is_a_warning_and_does_not_change_the_verdict(self):
        transport = StubTransport(delete_error=RuntimeError("delete refused"))
        report = self._run(transport)
        self.assertEqual(report["verdict"], "PASS")
        self.assertFalse(report["cleanup"]["performed"])
        self.assertIn("RuntimeError", report["cleanup"]["warning"])
        self.assertIn("delete refused", report["cleanup"]["warning"])

    def test_a_failed_retain_attempts_no_cleanup(self):
        transport = StubTransport(retain_error=RuntimeError("no retain"))
        report = self._run(transport)
        self.assertEqual(report["failed_check"], "real_retain")
        self.assertEqual(transport.delete_calls, 0)
        self.assertIsNone(report["cleanup"])

    def test_keep_leaves_the_document_and_says_so(self):
        transport = StubTransport()
        report = self._run(transport, keep=True)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(transport.delete_calls, 0)
        self.assertFalse(report["cleanup"]["performed"])
        self.assertEqual(report["cleanup"]["reason"], "--keep")


class NoCredentialEverReachesTheReport(PreflightTestCase):
    def test_userinfo_is_stripped_from_the_report_and_the_printed_block(self):
        report = self._run(StubTransport(), engine="http://user:pass@127.0.0.1:9")
        self.assertEqual(report["engine"], "http://127.0.0.1:9")
        self.assertNotIn("user:pass", json.dumps(report))
        text = self.module._blocked(report)
        self.assertNotIn("user:pass", text)
        self.assertIn("http://127.0.0.1:9", text)

    def test_the_cli_never_prints_a_credential_for_a_dead_engine(self):
        module = _preflight_module()
        output = io.StringIO()
        argv = ["p4-memory-preflight", "--engine-url", "http://user:pass@127.0.0.1:1",
                "--bank-namespace", "credential-test", "--timeout", "0.5"]
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(output):
            code = module.main()
        self.assertEqual(code, 2)
        self.assertNotIn("user:pass", output.getvalue())
        self.assertIn("database_connected", output.getvalue())


class ExitCodesSeparateTheEnvironmentFromTheScript(PreflightTestCase):
    def test_an_invalid_url_is_environment_blocked(self):
        module = _preflight_module()
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["p4-memory-preflight",
                                             "--engine-url", "not-a-url"]), \
                contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(output):
            code = module.main()
        self.assertEqual(code, 2)
        self.assertIn("configuration", output.getvalue())

    def test_a_non_integer_unit_count_blocks_instead_of_crashing(self):
        report = self._run(StubTransport(unit_count="many",
                                         units=[{"id": "unit-1"}]))
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["failed_check"], "extraction_complete")
        detail = report["checks"]["extraction_complete"]["detail"]
        self.assertIn("not an integer", detail)

    def test_an_unexpected_exception_is_a_script_defect(self):
        module = _preflight_module()
        output = io.StringIO()
        with mock.patch.object(module, "run_preflight",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "argv", ["p4-memory-preflight",
                                                "--engine-url",
                                                "http://127.0.0.1:1"]), \
                contextlib.redirect_stderr(output):
            code = module.main()
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", output.getvalue())


if __name__ == "__main__":
    unittest.main()
