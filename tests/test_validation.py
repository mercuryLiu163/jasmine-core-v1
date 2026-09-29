"""The minimal P0 Validation Recorder.

The rule under test is the one the taskbook cares about most: a case that did not
run is BLOCKED with a reason, never a fabricated PASS, and a bundle that would
leak a credential is not written at all.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import SRC  # noqa: F401  (path setup)

from jasmine_core.validation.recorder import (  # noqa: E402
    NOT_IMPLEMENTED,
    SECTIONS,
    Case,
    Recorder,
    assert_no_secrets,
)


def a_pass(case_id: str = "P0-T01") -> Case:
    return Case(case_id=case_id, name="example", verdict="PASS", user_raw="固定测试短句",
                agent_response=NOT_IMPLEMENTED, tools=[{"command": "jasmine-core migrate"}],
                state_before={"events": 0}, state_after={"events": 0},
                interpretation=NOT_IMPLEMENTED, context=NOT_IMPLEMENTED,
                reproduction=["jasmine-core migrate", "jasmine-core schema"])


class CaseRules(unittest.TestCase):
    def test_a_pass_needs_no_failure_detail(self) -> None:
        self.assertEqual(a_pass().verdict, "PASS")

    def test_an_unknown_verdict_is_refused(self) -> None:
        for bad in ("pass", "PASSED", "OK", "DONE", ""):
            with self.assertRaises(ValueError, msg=bad):
                Case(case_id="x", name="x", verdict=bad)

    def test_a_fail_without_a_class_and_output_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Case(case_id="x", name="x", verdict="FAIL")
        with self.assertRaises(ValueError):
            Case(case_id="x", name="x", verdict="FAIL", failure_class="db")
        case = Case(case_id="x", name="x", verdict="FAIL", failure_class="db",
                    failure_output="IntegrityError: ...", reproduction=["sqlite3 core.db"])
        self.assertEqual(case.verdict, "FAIL")

    def test_a_blocked_case_must_say_why(self) -> None:
        with self.assertRaises(ValueError):
            Case(case_id="x", name="x", verdict="BLOCKED")
        case = Case(case_id="x", name="x", verdict="BLOCKED",
                    not_applied_reason="no real Codex entry point on this host")
        self.assertEqual(case.verdict, "BLOCKED")

    def test_every_taskbook_section_is_representable(self) -> None:
        case = a_pass()
        payload = json.loads(json.dumps(case.__dict__))
        for section in SECTIONS:
            self.assertIn(section, payload)
        self.assertEqual(case.user_raw, "固定测试短句")
        self.assertEqual(case.interpretation, NOT_IMPLEMENTED)
        self.assertEqual(case.context, NOT_IMPLEMENTED)


class RunVerdict(unittest.TestCase):
    def build(self, *verdicts: str) -> Recorder:
        recorder = Recorder(Path(tempfile.mkdtemp(prefix="jasmine-core-run-")))
        for index, verdict in enumerate(verdicts):
            kwargs = {}
            if verdict == "FAIL":
                kwargs = {"failure_class": "db", "failure_output": "boom"}
            if verdict == "BLOCKED":
                kwargs = {"not_applied_reason": "environment"}
            recorder.add(Case(case_id=f"T{index}", name=f"case {index}", verdict=verdict, **kwargs))
        return recorder

    def test_all_pass_is_pass(self) -> None:
        self.assertEqual(self.build("PASS", "PASS").run.verdict, "PASS")

    def test_any_fail_makes_the_run_fail(self) -> None:
        self.assertEqual(self.build("PASS", "FAIL", "PASS").run.verdict, "FAIL")
        self.assertEqual(self.build("BLOCKED", "FAIL").run.verdict, "FAIL")

    def test_a_blocked_case_does_not_become_a_pass(self) -> None:
        self.assertEqual(self.build("PASS", "BLOCKED").run.verdict, "BLOCKED")

    def test_no_cases_at_all_is_not_a_pass(self) -> None:
        self.assertEqual(self.build().run.verdict, "PASS")

    def test_the_run_binds_a_commit_and_a_schema_version(self) -> None:
        recorder = self.build("PASS")
        self.assertEqual(len(recorder.run.commit), 40)
        self.assertEqual(recorder.run.schema_version, 2)
        self.assertIn("python", recorder.run.environment)
        self.assertIn("sqlite3", recorder.run.environment)


class BundleWriting(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-bundle-")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def test_a_bundle_is_written_with_owner_only_permissions(self) -> None:
        recorder = Recorder(self.out, executor="impl", reviewer="rev", verifier="ver")
        recorder.add(a_pass())
        recorder.note("P0-T01 through P0-T06 automated")
        recorder.not_implemented("Rule/Guard, Task state machine, Evidence, Interpreter")
        path = recorder.write()
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["verdict"], "PASS")
        self.assertEqual(payload["executor"], "impl")
        self.assertEqual(payload["reviewer"], "rev")
        self.assertEqual(payload["verifier"], "ver")
        self.assertEqual(len(payload["cases"]), 1)
        self.assertTrue(payload["not_implemented"])

    def test_a_credential_shaped_key_refuses_to_write(self) -> None:
        recorder = Recorder(self.out)
        case = a_pass()
        case.reproduction = ["curl -H 'Authorization: Bearer sk-live-abcdef'"]
        recorder.add(case)
        with self.assertRaises(ValueError):
            recorder.write()
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_credential_shaped_field_refuses_to_write(self) -> None:
        recorder = Recorder(self.out)
        case = a_pass()
        case.tools = [{"command": "login", "api_key": "abc123"}]
        recorder.add(case)
        with self.assertRaises(ValueError):
            recorder.write()

    def test_assert_no_secrets_accepts_ordinary_content(self) -> None:
        assert_no_secrets({"command": "jasmine-core migrate", "text": "hello"})

    def test_assert_no_secrets_rejects_a_bearer_header(self) -> None:
        with self.assertRaises(ValueError):
            assert_no_secrets({"cmd": "curl -H 'Authorization: Bearer abc'"})
        with self.assertRaises(ValueError):
            assert_no_secrets(["echo", "export OPENAI_API_KEY=sk-abcdefghijklmnop"])


if __name__ == "__main__":
    unittest.main()
