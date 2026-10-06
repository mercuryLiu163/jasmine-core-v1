"""Reverse tests for the G01 v2 exploration-cost contract.

The specification is frozen (docs/p4-g01-v2-spec.md). These tests pin the parts a
run depends on: the native stream is extracted whole and at native granularity,
every action binds real Core Evidence or blocks, the metric and its conservative
fallback are computed from the bound stream, missing snapshots block while a
wrong fix fails, and the pre-registered thresholds decide without reading a
result twice.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from support import SRC, import_gate_module  # noqa: F401
from test_p4_fix_round import _real_gate  # noqa: F401

sys.path.insert(0, str(SRC))
from jasmine_core.memory.g01_stream import action_payload, extract_actions  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _assembler():
    spec = importlib.util.spec_from_file_location(
        "p4_memory_g01_session", REPO_ROOT / "scripts" / "p4-memory-g01-session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _setup_module():
    spec = importlib.util.spec_from_file_location(
        "p4_g01_v2_setup", REPO_ROOT / "scripts" / "p4-g01-v2-setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(kind, **fields):
    entry = {"seq": 0, "kind": kind, "tool_name": fields.pop("tool_name", kind),
             "evidence_id": fields.pop("evidence_id", None)}
    entry.update(fields)
    return entry


def _file_change_event(paths, evidence_id="evd_" + "F" * 26):
    changes = []
    for path, before, after in paths:
        changes.append({"path": path, "kind": "update",
                        "before_sha256": before, "after_sha256": after,
                        "diff": f"--- {path}\n+++ {path}\n"})
    command, output = action_payload({"kind": "file_change", "changes": changes})
    return _event("file_change", tool_name="file_change", evidence_id=evidence_id,
                  command=command, output=output, changes=changes)


def _shell_event(command, output, evidence_id="evd_" + "S" * 26, exit_code=0):
    return _event("shell", tool_name="shell", evidence_id=evidence_id,
                  command=command, output=output, exit_code=exit_code)


def _snapshot_for_tmp(repo):
    snap = {}
    for name in ("calc.py", "test_calc.py", "NOTES.md", "requirements.txt"):
        target = repo / name
        snap[name] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    return snap


class StreamExtraction(unittest.TestCase):
    """The extractor keeps every native action, at native granularity."""

    def test_every_tool_item_is_extracted_and_nothing_else(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "t"},
            {"type": "item.completed",
             "item": {"type": "command_execution", "command": "cat calc.py",
                      "aggregated_output": "buggy\n", "exit_code": 0}},
            {"type": "item.completed",
             "item": {"type": "agent_message", "text": "thinking"}},
            {"type": "item.completed",
             "item": {"type": "file_change",
                      "changes": [{"path": "/r/calc.py", "kind": "update"}]}},
            {"type": "item.completed",
             "item": {"type": "mcp_tool_call", "tool": "read_file",
                      "arguments": {"path": "NOTES.md"}}},
        ]
        text = "\n".join(json.dumps(event) for event in events)
        actions = extract_actions(text)
        self.assertEqual([a["kind"] for a in actions], ["shell", "file_change", "mcp"])
        self.assertEqual(actions[0]["output"], "buggy\n")
        self.assertEqual(actions[1]["changes"], [{"path": "/r/calc.py", "kind": "update"}])
        self.assertEqual(actions[2]["tool_name"], "mcp:read_file")

    def test_batched_events_count_per_event_and_compound_shell_counts_once(self) -> None:
        text = "\n".join(json.dumps(event) for event in [
            {"type": "item.completed",
             "item": {"type": "command_execution", "command": "cat a", "aggregated_output": "a"}},
            {"type": "item.completed",
             "item": {"type": "command_execution", "command": "cat b", "aggregated_output": "b"}},
            {"type": "item.completed",
             "item": {"type": "command_execution", "command": "cat a; cat b",
                      "aggregated_output": "ab"}},
        ])
        self.assertEqual(len(extract_actions(text)), 3)

    def test_action_payload_is_a_pure_function(self) -> None:
        action = {"kind": "file_change",
                  "changes": [{"path": "/r/calc.py", "kind": "update",
                               "before_sha256": "a", "after_sha256": "b", "diff": "d"}]}
        first = action_payload(action)
        self.assertEqual(first, action_payload(json.loads(json.dumps(action))))
        self.assertEqual(first[0], "file_change:/r/calc.py:update")


class FileChangeAttribution(unittest.TestCase):
    """The assembler binds a file transition only when it can attribute it."""

    def _fixture(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix="p4-v2-fix-")
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        (repo / "calc.py").write_bytes(
            b"def total(prices, discount):\n    return sum(prices) - discount\n")
        (repo / "test_calc.py").write_bytes(b"expected 27.5\n")
        (repo / "NOTES.md").write_bytes(b"discount is a percent\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=T",
                        "-c", "user.email=t@example.invalid", "commit", "-qm", "baseline"],
                       check=True)
        return repo

    def test_a_single_touch_produces_before_after_and_diff(self) -> None:
        module = _assembler()
        repo = self._fixture()
        (repo / "calc.py").write_bytes(
            b"def total(prices, discount):\n    return sum(prices) * (1 - discount / 100)\n")
        before, after, diff = module.changes_for(repo, "HEAD", "calc.py")
        self.assertNotEqual(before, after)
        self.assertIn("discount / 100", diff)
        again = module.changes_for(repo, "HEAD", "calc.py")
        self.assertEqual((before, after, diff), again)


class MetricComputation(unittest.TestCase):
    """M, the observation anchors, the fallback and the secondary metrics."""

    def setUp(self) -> None:
        self.module = import_gate_module()
        self.buggy = "def total(prices, discount):\n    return sum(prices) - discount\n"
        self.fixed = "def total(prices, discount):\n    return sum(prices) * (1 - discount / 100)\n"
        self.read = _shell_event(
            "cat calc.py test_calc.py NOTES.md requirements.txt",
            self.buggy + "The discount is in percent, not currency.\n")
        self.reproduce = _shell_event(
            "python -B -m unittest -v", "Ran 1 test\nAssertionError: 25.0 != 27.5", exit_code=1)
        self.repair = _file_change_event(
            [("/r/calc.py", "b" * 64, hashlib.sha256(self.fixed.encode()).hexdigest()),
             ("/r/test_calc.py", "c" * 64, "d" * 64)])
        self.verify = _shell_event(
            "python -B -m unittest -v", "Ran 1 test\nOK",
            evidence_id="evd_" + "V" * 26)
        self.snapshot = {"calc.py": hashlib.sha256(self.fixed.encode()).hexdigest(),
                         "test_calc.py": "d" * 64, "NOTES.md": "e" * 64,
                         "requirements.txt": "f" * 64}

    def _session(self, events):
        return {"tool_events": events, "snapshot": dict(self.snapshot)}

    def test_metric_counts_exploration_before_the_completing_observation(self) -> None:
        metrics = self.module._g01_v2_classify(self._session(
            [self.read, self.reproduce, self.repair, self.verify]), "the baseline")
        self.assertTrue(metrics["o_star_complete"])
        self.assertEqual(metrics["diagnosis_epoch"], 1)
        self.assertEqual(metrics["primary"], 1)
        self.assertEqual(metrics["classes"], ["E", "E", "R", "V"])
        self.assertEqual(metrics["first_repair"], 2)
        self.assertEqual(metrics["invalid_attempts"], 0)

    def test_missing_observation_falls_back_to_the_end_never_to_the_first_repair(self) -> None:
        blind = _shell_event("python -B -m unittest -v", "OK\n")
        metrics = self.module._g01_v2_classify(self._session(
            [blind, self.repair, self.verify]), "the lesson")
        self.assertFalse(metrics["o_star_complete"])
        self.assertEqual(metrics["diagnosis_epoch"], 3)
        self.assertEqual(metrics["primary"], 1)  # only the pre-repair suite is E; the green one is V
        self.assertEqual(metrics["classes"], ["E", "R", "V"])

    def test_repeated_reads_rounds_and_invalid_attempts(self) -> None:
        second_read = _shell_event("cat calc.py", self.buggy)
        superseded = _file_change_event(
            [("/r/calc.py", "1" * 64, "2" * 64)])  # 2*64 is not the final snapshot
        metrics = self.module._g01_v2_classify(self._session(
            [self.read, second_read, self.reproduce, superseded, self.repair, self.verify]),
            "the lesson")
        self.assertEqual(metrics["repeated_reads"], 1)
        self.assertEqual(metrics["exploration_rounds"], 1)
        self.assertEqual(metrics["invalid_attempts"], 1)
        self.assertEqual(metrics["first_repair"], 3)

    def test_pre_registered_thresholds(self) -> None:
        verdict = self.module._g01_v2_verdict
        flat = {"repeated_reads": 0, "invalid_attempts": 0, "exploration_rounds": 0}
        self.assertTrue(verdict({"primary": 4, **flat}, {"primary": 2, **flat})["primary_satisfied"])
        self.assertTrue(verdict({"primary": 4, **flat}, {"primary": 1, **flat})["primary_satisfied"])
        self.assertFalse(verdict({"primary": 4, **flat}, {"primary": 3, **flat})["primary_satisfied"])
        self.assertFalse(verdict({"primary": 8, **flat}, {"primary": 7, **flat})["primary_satisfied"])
        self.assertTrue(verdict({"primary": 8, **flat}, {"primary": 6, **flat})["primary_satisfied"])
        self.assertTrue(verdict({"primary": 3, **flat}, {"primary": 1, **flat})["unmeasurable"])
        regressed = verdict({"primary": 4, **flat}, {"primary": 2, **flat,
                                                     "repeated_reads": 1})
        self.assertFalse(regressed["secondary_satisfied"])


class EvidenceBinding(unittest.TestCase):
    """Every action binds a real row; anything else blocks before the metric."""

    def setUp(self) -> None:
        self.module = import_gate_module()
        self.gate = _real_gate(self, case_id="P4-V2B")
        self.project = self.gate.seed_project("v2-binding")
        self.task = self.gate.new_task(self.project, "v2 task")
        self.record = self.module.CaseRecord(case_id="P4-G01", verdict="PASS")

    def _session(self, events, **extra):
        session = {"tool_events": events, "snapshot": {}, "verified": {}, "sealed": []}
        session.update(extra)
        return session

    def test_bound_actions_verify_and_are_recorded(self) -> None:
        event = _file_change_event([("/r/calc.py", "a" * 64, "b" * 64)],
                                   evidence_id="pending")
        evidence = self.gate.collect_tool_evidence(
            self.task, command=event["command"], output=event["output"],
            exit_code=0, tool_name="file_change")
        event["evidence_id"] = evidence
        self.module._g01_v2_bind(self.gate, self.record, self._session([event]),
                                 "g01-lesson", self.task)
        self.assertEqual(self.record.evidence_ids, [evidence])

    def test_an_unbound_action_blocks(self) -> None:
        event = _shell_event("cat calc.py", "x", evidence_id=None)
        with self.assertRaises(self.module.Blocked):
            self.module._g01_v2_bind(self.gate, self.record, self._session([event]),
                                     "g01-lesson", self.task)

    def test_an_unattributed_workspace_change_blocks(self) -> None:
        with self.assertRaises(self.module.Blocked):
            self.module._g01_v2_bind(
                self.gate, self.record,
                self._session([], unattributed_changes=["calc.py"]),
                "g01-lesson", self.task)

    def test_a_shell_row_cannot_impersonate_a_file_change(self) -> None:
        event = _file_change_event([("/r/calc.py", "a" * 64, "b" * 64)],
                                   evidence_id="pending")
        evidence = self.gate.collect_tool_evidence(
            self.task, command=event["command"], output=event["output"],
            exit_code=0)  # tool_name shell
        event["evidence_id"] = evidence
        with self.assertRaises(self.module.Failed):
            self.module._g01_v2_bind(self.gate, self.record, self._session([event]),
                                     "g01-lesson", self.task)


class StreamCompleteness(unittest.TestCase):
    """The sealed stream is re-extracted; a session that dropped an action fails."""

    def setUp(self) -> None:
        self.module = import_gate_module()
        self.gate = _real_gate(self, case_id="P4-V2S")
        self.record = self.module.CaseRecord(case_id="P4-G01", verdict="PASS")

    def _write_stream(self, events):
        text = "\n".join(json.dumps(event) for event in events)
        path = self.gate.root / "session-g01-lesson.codex.jsonl"
        path.write_text(text, encoding="utf-8")
        return text

    def test_a_matching_stream_seals_and_passes(self) -> None:
        text = self._write_stream([
            {"type": "item.completed",
             "item": {"type": "command_execution", "command": "cat calc.py",
                      "aggregated_output": "buggy", "exit_code": 0}}])
        session = {"stream_sha256": hashlib.sha256(text.encode()).hexdigest(),
                   "tool_events": [_shell_event("cat calc.py", "buggy")]}
        actions = self.module._g01_v2_stream(self.gate, self.record, session, "g01-lesson")
        self.assertEqual(len(actions), 1)
        self.assertIn("g01-v2-stream-g01-lesson", self.record.sealed_artifacts)

    def test_a_dropped_action_fails(self) -> None:
        text = self._write_stream([
            {"type": "item.completed",
             "item": {"type": "command_execution", "command": "cat calc.py",
                      "aggregated_output": "buggy", "exit_code": 0}},
            {"type": "item.completed",
             "item": {"type": "command_execution", "command": "cat NOTES.md",
                      "aggregated_output": "rule", "exit_code": 0}}])
        session = {"stream_sha256": hashlib.sha256(text.encode()).hexdigest(),
                   "tool_events": [_shell_event("cat calc.py", "buggy")]}
        with self.assertRaises(self.module.Failed):
            self.module._g01_v2_stream(self.gate, self.record, session, "g01-lesson")

    def test_a_missing_stream_blocks(self) -> None:
        with self.assertRaises(self.module.Blocked):
            self.module._g01_v2_stream(self.gate, self.record,
                                       {"tool_events": []}, "g01-lesson")


class QualityGate(unittest.TestCase):
    """Missing evidence blocks; a wrong fix or skipped verification fails."""

    def setUp(self) -> None:
        self.module = import_gate_module()
        self.gate = _real_gate(self, case_id="P4-V2Q")
        self.project = self.gate.seed_project("v2-quality")
        self.task = self.gate.new_task(self.project, "v2 quality")
        self.record = self.module.CaseRecord(case_id="P4-G01", verdict="PASS")
        self.fixed = b"def total(prices, discount):\n    return sum(prices) * (1 - discount / 100)\n"
        self.tests = (b"import unittest\n\nfrom calc import total\n\n\n"
                      b"class TotalTest(unittest.TestCase):\n"
                      b"    def test_total_applies_discount_as_percent(self):\n"
                      b"        # NOTES.md: the discount is a percentage of the subtotal.\n"
                      b"        self.assertAlmostEqual(total([10.0, 20.0], 5.0), 28.5)\n")
        self.memory_id = "mem_" + "M" * 26
        self.failure_evidence = "evd_" + "F" * 26
        self.source_task = "tsk_" + "T" * 26
        self.answer = (f"Historically this failed: {self.memory_id} "
                       f"{self.failure_evidence} from {self.source_task}.")
        self.pack = {"memory_selected": [{
            "memory_id": self.memory_id, "memory_type": "FAILURE_LESSON",
            "source_task_id": self.source_task,
            "fact": {"evidence_id": self.failure_evidence}}]}
        self.review = {
            "reviewer": "independent reviewer", "verdict": "consistent",
            "reviewed_answer_sha256": hashlib.sha256(self.answer.encode()).hexdigest(),
            "findings": f"The answer says \"{self.answer[:40]}\" which matches the pack."}

    def _session(self, **overrides):
        events = [
            _file_change_event([("/r/calc.py", "a" * 64, "b" * 64)],
                               evidence_id="evd_" + "P" * 26),
            _shell_event("python -B -m unittest -v", "Ran 1 test\nOK",
                         evidence_id="evd_" + "G" * 26),
        ]
        session = {
            "task_completed": True,
            "final_verification": {"command": "python -B -m unittest -v",
                                   "output": "Ran 1 test\nOK"},
            "tool_events": events,
            "snapshot": {"calc.py": hashlib.sha256(self.fixed).hexdigest(),
                         "test_calc.py": hashlib.sha256(self.tests).hexdigest(),
                         "NOTES.md": "e" * 64, "requirements.txt": "f" * 64},
            "final_answer": self.answer,
            "semantic_review": dict(self.review),
            "verified": {},
        }
        session.update(overrides)
        return session

    def test_the_complete_quality_inputs_pass(self) -> None:
        session = self._session()
        metrics = {"first_repair": 0, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                    metrics, self.pack)
        self.assertEqual(session["verified"]["semantic_review"]["verdict"], "consistent")

    def test_a_wrong_fix_fails(self) -> None:
        session = self._session(snapshot={"calc.py": "0" * 64,
                                          "test_calc.py": hashlib.sha256(self.tests).hexdigest(),
                                          "NOTES.md": "e" * 64, "requirements.txt": "f" * 64})
        metrics = {"first_repair": 0, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        with self.assertRaises(self.module.Failed):
            self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                        metrics, self.pack)

    def test_a_missing_snapshot_blocks(self) -> None:
        session = self._session(snapshot={})
        metrics = {"first_repair": 0, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        with self.assertRaises(self.module.Blocked):
            self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                        metrics, self.pack)

    def test_a_green_run_before_the_repair_fails(self) -> None:
        session = self._session()
        metrics = {"first_repair": 5, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        with self.assertRaises(self.module.Failed):
            self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                        metrics, self.pack)

    def test_an_answer_without_the_full_triple_fails(self) -> None:
        session = self._session(final_answer="Historically this failed.")
        session["semantic_review"] = {
            **self.review,
            "reviewed_answer_sha256": hashlib.sha256(
                session["final_answer"].encode()).hexdigest(),
            "findings": "Historically this failed." + " " * 30}
        metrics = {"first_repair": 0, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        with self.assertRaises(self.module.Failed):
            self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                        metrics, self.pack)


class PairPreparation(unittest.TestCase):
    """Two independent worktrees at the frozen ref, with the hook installed."""

    @unittest.skipUnless(__import__("shutil").which("codex"),
                         "codex is not on PATH")
    def test_prepare_creates_two_frozen_worktrees_and_refuses_a_second_time(self) -> None:
        import tempfile
        module = _setup_module()
        tmp = tempfile.TemporaryDirectory(prefix="p4-v2-prep-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / "fixture"
        repo.mkdir()
        (repo / "calc.py").write_bytes(
            b"def total(prices, discount):\n    return sum(prices) - discount\n")
        (repo / "test_calc.py").write_bytes(b"expected 27.5\n")
        (repo / "NOTES.md").write_bytes(b"discount is a percent\n")
        (repo / "requirements.txt").write_bytes(b"\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=T",
                        "-c", "user.email=t@example.invalid", "commit", "-qm", "baseline"],
                       check=True)
        ref = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        prompt = root / "prompt.txt"
        prompt.write_text("fix the test\n", encoding="utf-8")
        version, model = module.collect_codex()
        config = root / "op.json"
        config.write_text(json.dumps({
            "g01_version": "v2",
            "g01_v2_spec_sha256": module.sha256(module.REPO_ROOT / module.SPEC_REL),
            "g01_v2_prompt_sha256": module.sha256(prompt),
            "g01_v2_fixture_ref": ref,
            "g01_v2_codex_version": version,
            "g01_v2_codex_model": model,
        }), encoding="utf-8")
        work = root / "work"
        work.mkdir()
        inputs = module.prepare(repo, ref, root / "trees", work, config, prompt,
                       Path(sys.executable))
        for label in ("baseline", "lesson"):
            tree = root / "trees" / f"g01-v2-{label}"
            self.assertTrue((tree / "calc.py").is_file())
            self.assertEqual(subprocess.run(
                ["git", "-C", str(tree), "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip(), ref)
            self.assertTrue((tree / ".codex" / "jasmine" /
                             "jasmine-p4-session-start.py").is_file())
            self.assertEqual(inputs["worktrees"][label]["hook_sha256"],
                             module.sha256(tree / ".codex" / "jasmine" /
                                           "jasmine-p4-session-start.py"))
        self.assertTrue((work / "g01-v2-inputs.json").is_file())
        with self.assertRaises(SystemExit):
            module.prepare(repo, ref, root / "trees", work, config, prompt,
                       Path(sys.executable))

    def test_a_wrong_prompt_hash_is_refused(self) -> None:
        import tempfile
        module = _setup_module()
        tmp = tempfile.TemporaryDirectory(prefix="p4-v2-prep2-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        prompt = root / "prompt.txt"
        prompt.write_text("fix the test\n", encoding="utf-8")
        config = root / "op.json"
        config.write_text(json.dumps({
            "g01_version": "v2",
            "g01_v2_spec_sha256": module.sha256(module.REPO_ROOT / module.SPEC_REL),
            "g01_v2_prompt_sha256": "0" * 64,
            "g01_v2_fixture_ref": "deadbeef",
            "g01_v2_codex_version": "x",
            "g01_v2_codex_model": "y",
        }), encoding="utf-8")
        with self.assertRaises(SystemExit):
            module.prepare(root, "deadbeef", root / "trees", root, config, prompt)



if __name__ == "__main__":
    unittest.main()
