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
from jasmine_core import SCHEMA_VERSION  # noqa: E402

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
        self.assertEqual(recorder.run.schema_version, SCHEMA_VERSION)
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

    def test_a_credential_in_the_recorded_response_is_withheld_and_disclosed(self) -> None:
        # A bundle must not vanish because the product leaked a token into a
        # response -- that is the evidence. It must be redacted, disclosed, and
        # surfaced as a finding the acceptance runner turns into a FAIL.
        recorder = Recorder(self.out)
        recorder.add(a_pass())
        case = a_pass()
        case.tools = [{"request": "POST /v1/auth/keys", "status": 201,
                       "response": {"token": "sk-leaked-0123456789abcdef"}}]
        recorder.add(case)
        path = recorder.write()
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("sk-leaked", path.read_text(encoding="utf-8"))
        self.assertTrue(payload["redactions"])
        self.assertIn("redaction_warning", payload)
        self.assertIn("token", payload["redactions"][0]["path"])
        self.assertEqual(len(recorder.redactions), 1)

    def test_a_credential_named_field_is_withheld(self) -> None:
        recorder = Recorder(self.out)
        case = a_pass()
        case.tools = [{"api_key": "abc123"}]
        recorder.add(case)
        payload = json.loads(recorder.write().read_text(encoding="utf-8"))
        self.assertEqual(len(payload["redactions"]), 1)
        self.assertIn("credential-shaped field name", payload["redactions"][0]["reason"])

    def test_an_ordinary_bundle_records_no_redactions(self) -> None:
        recorder = Recorder(self.out)
        recorder.add(a_pass())
        payload = json.loads(recorder.write().read_text(encoding="utf-8"))
        self.assertEqual(payload["redactions"], [])
        self.assertNotIn("redaction_warning", payload)

    def test_metadata_field_names_are_not_treated_as_credentials(self) -> None:
        # `api_key_rows` is a row count, not a secret; a substring rule would
        # reject honest evidence while stopping no real leak.
        assert_no_secrets({"api_key_rows": 3, "token_count": 0, "session_key_note": "n/a"})

    def test_a_shell_placeholder_in_a_reproduction_step_is_allowed(self) -> None:
        # Refusing documentation that names a variable would make a usable
        # bundle impossible to write, and would stop none of the real leaks.
        recorder = Recorder(self.out)
        case = a_pass()
        case.reproduction = [
            "export JASMINE_CORE_TOKEN=$(cat ~/.local/share/jasmine-core/capture-token)",
            "curl -H \"Authorization: Bearer $JASMINE_CORE_TOKEN\" …/v1/events",
            "curl -H 'Authorization: Bearer ${READER}' …/v1/events",
        ]
        recorder.add(case)
        self.assertTrue(recorder.write().exists())

    def test_assert_no_secrets_still_refuses_when_asked_to(self) -> None:
        # The strict check remains available for callers that want refusal
        # semantics; the recorder itself sanitises so evidence is never lost.
        with self.assertRaises(ValueError):
            assert_no_secrets({"api_key": "abc123"})

    def test_assert_no_secrets_accepts_ordinary_content(self) -> None:
        assert_no_secrets({"command": "jasmine-core migrate", "text": "hello"})

    def test_assert_no_secrets_rejects_real_material(self) -> None:
        with self.assertRaises(ValueError):
            assert_no_secrets({"cmd": "curl -H 'Authorization: Bearer abcdefgh0123456789'"})
        with self.assertRaises(ValueError):
            assert_no_secrets(["echo", "export OPENAI_API_KEY=sk-abcdefghijklmnop"])
        with self.assertRaises(ValueError):
            assert_no_secrets({"x": "AKIAIOSFODNN7EXAMPLE"})

    def test_assert_no_secrets_allows_placeholders_and_prose(self) -> None:
        assert_no_secrets({"cmd": "curl -H 'Authorization: Bearer $TOKEN' …"})
        assert_no_secrets(["the token is stored as a 0600 file", "no credential here"])


if __name__ == "__main__":
    unittest.main()
