"""The P4 Gate runner's precondition logic.

This is coverage of the *runner*, not of the Gates. What matters is that the
runner cannot be mistaken for a Gate that passed: every missing prerequisite has
to come out as `BLOCKED` with the exact item named, and there must be no code
path that produces `mode: "boundary-fake"`.

The real-Gate decisions themselves — G05, G06, G07 against a real engine — are
made by `scripts/p4-memory-gate.py` with an operator-supplied configuration.
None of them can run in this repository: there is no Hindsight process, no LLM
extractor, no real Agent session, and no P3 Context Pack.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SRC  # noqa: F401  (path setup)

from jasmine_core.memory import scope  # noqa: E402

GATE = SRC.parent / "scripts" / "p4-memory-gate.py"
CASES = ("P4-G01", "P4-G02", "P4-G03", "P4-G04", "P4-G05", "P4-G06", "P4-G07")


def load_runner():
    """Import the runner as a module, registered in `sys.modules`.

    The registration matters: the runner defines dataclasses, and `@dataclass`
    looks its own module up in `sys.modules` while decorating. Loading it
    anonymously would fail inside the standard library rather than in the runner.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("p4_memory_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    self_addCleanup = getattr(module, "__spec__", None)
    del self_addCleanup
    spec.loader.exec_module(module)
    return module


class RunnerSource(unittest.TestCase):
    """Static checks on the runner: it must not be able to fake a verdict."""

    def test_the_runner_exists_and_is_executable_python(self) -> None:
        self.assertTrue(GATE.exists(), f"{GATE} is missing")
        source = GATE.read_text(encoding="utf-8")
        self.assertIn("P4-G01", source)
        for case_id in CASES:
            self.assertIn(case_id, source, case_id)

    def test_no_boundary_fake_verdict_can_be_produced(self) -> None:
        source = GATE.read_text(encoding="utf-8")
        # The vocabulary exists so the documentation can name what is excluded.
        self.assertIn('"boundary-fake"', source, "the vocabulary must exist to be excluded")
        # The only two `mode` assignments are the two honest values, and neither
        # is the fake one. A case that has not run says so.
        assignments = set(re.findall(r'record\.mode = "([a-z-]+)"', source))
        self.assertEqual(assignments, {"not-run", "real"})
        self.assertNotIn("boundary-fake", assignments)
        self.assertEqual(self.runner_module().CaseRecord("x", "BLOCKED").mode, "real",
                         "the default mode must be the honest one")
        self.assertEqual(self.runner_module().CaseRecord.__dataclass_fields__["mode"].default,
                         "real")

    @staticmethod
    def runner_module():
        return load_runner()

    def test_a_dirty_checkout_is_refused_without_an_explicit_override(self) -> None:
        source = GATE.read_text(encoding="utf-8")
        self.assertIn("checkout is dirty", source)
        self.assertIn('"--allow-dirty"', source)


class Preconditions(unittest.TestCase):
    """Each missing prerequisite yields BLOCKED with the item named."""

    def setUp(self) -> None:
        self.runner = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-test-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()

    def gate(self, *, case_id: str = "P4-TEST", **config) -> object:
        return self.runner.Gate(self.work, config, case_id)

    def test_no_engine_url_blocks_with_the_variable_named(self) -> None:
        gate = self.gate()
        previous = os.environ.pop("JASMINE_HINDSIGHT_URL", None)
        self.addCleanup(lambda: os.environ.__setitem__("JASMINE_HINDSIGHT_URL", previous)
                        if previous else None)
        with self.assertRaises(self.runner.Blocked) as caught:
            gate.open()
        self.assertIn("JASMINE_HINDSIGHT_URL", "; ".join(caught.exception.items))

    def test_an_unreachable_engine_blocks_with_the_code_and_the_host(self) -> None:
        gate = self.gate()
        os.environ["JASMINE_HINDSIGHT_URL"] = "http://127.0.0.1:1"
        os.environ["JASMINE_HINDSIGHT_TIMEOUT_SECONDS"] = "1"
        self.addCleanup(gate.close)
        gate.open()
        with self.assertRaises(self.runner.Blocked) as caught:
            gate.require_engine()
        message = "; ".join(caught.exception.items)
        self.assertIn("did not answer", message)
        self.assertIn("engine_unreachable", message)
        self.assertIn("127.0.0.1:1", message)

    def test_an_unmerged_phase_blocks_and_names_the_phase(self) -> None:
        gate = self.gate(dependency_commits={"P2": "blocked", "P3": None})
        for phase, marker in (("P2", "P2"), ("P3", "P3")):
            with self.assertRaises(self.runner.Blocked) as caught:
                gate.require_dependency(phase)
            self.assertIn(marker, "; ".join(caught.exception.items))
        # A real commit satisfies it.
        merged = self.gate(case_id="P4-TEST-MERGED", dependency_commits={"P3": "0" * 40})
        self.assertEqual(merged.require_dependency("P3"), "0" * 40)

    def test_a_missing_bank_namespace_blocks(self) -> None:
        with self.assertRaises(self.runner.Blocked) as caught:
            self.gate().require_bank_namespace()
        self.assertIn("bank namespace", "; ".join(caught.exception.items))

    def test_no_declared_real_agent_session_blocks(self) -> None:
        with self.assertRaises(self.runner.Blocked) as caught:
            self.gate().require_real_agent()
        self.assertIn("real Agent session", "; ".join(caught.exception.items))

    def test_the_structurally_blocked_cases_are_declared_up_front(self) -> None:
        # G01/G02/G03/G04 need a P3 Context Pack and G01 also needs a real
        # session. The set is recorded in the module so a future edit cannot
        # drop one of them from the list without a visible diff, and the check is
        # on the *code* of each case rather than on a comment.
        import dis

        self.assertEqual(self.runner.NEEDS_P3_PACK, frozenset(CASES[:4]))
        self.assertEqual(self.runner.NEEDS_REAL_AGENT, frozenset({"P4-G01"}))
        for case_id in sorted(self.runner.NEEDS_P3_PACK):
            names = {instruction.argval
                     for instruction in dis.get_instructions(self.runner.CASES_BY_ID[case_id])}
            self.assertIn("require_p3_pack", names, case_id)
        names = {instruction.argval
                 for instruction in dis.get_instructions(self.runner.CASES_BY_ID["P4-G01"])}
        self.assertIn("require_real_agent", names)

    def test_a_dry_run_against_no_engine_reports_blocked_for_every_case(self) -> None:
        """The end-to-end honesty check: nothing available means nothing passes."""
        out = Path(self._tmp.name) / "out"
        config = Path(self._tmp.name) / "config.json"
        config.write_text(json.dumps({"run_id": "p4-test-dryrun",
                                      "bank_suffix": "p4-test-dryrun",
                                      "dependency_commits": {"P2": "blocked", "P3": "blocked"}}),
                          encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(SRC),
                   JASMINE_HINDSIGHT_URL="http://127.0.0.1:1",
                   JASMINE_HINDSIGHT_TIMEOUT_SECONDS="1")
        env.pop("JASMINE_HINDSIGHT_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, str(GATE), "--out", str(out), "--work", str(self.work / "run"),
             "--config", str(config), "--allow-dirty"],
            capture_output=True, text=True, env=env, timeout=300,
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        manifest = json.loads(Path(summary["result"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["verdict"], "BLOCKED")
        self.assertEqual({case["case_id"] for case in manifest["cases"]}, set(CASES))
        for case in manifest["cases"]:
            self.assertEqual(case["verdict"], "BLOCKED", case["case_id"])
            self.assertEqual(case["mode"], "not-run")
            self.assertTrue(case["blocked_on"], case["case_id"])
            self.assertIn("did not answer", case["reason"])
        self.assertFalse(manifest["engine"]["reachable"])
        self.assertEqual(manifest["phase"], "P4")
        self.assertIsNone(manifest["final_commit"])
        self.assertIn("not Gate evidence", " ".join(manifest["notes"]))


class DeploymentModeIsReported(unittest.TestCase):
    def test_the_manifest_records_the_mode_so_no_acl_claim_is_implicit(self) -> None:
        runner = load_runner()
        source = GATE.read_text(encoding="utf-8")
        self.assertIn('"deployment_mode": scope.deployment_mode()', source)
        self.assertIn("no user-level ACL claim is made", " ".join(
            line for line in source.splitlines() if "ACL" in line))
        self.assertEqual(runner.CASES_BY_ID["P4-G04"].__name__, "case_g04")
        self.assertEqual(scope.SINGLE_USER, "single_user")
