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
                                    metrics, self.pack, lesson=True)
        self.assertEqual(session["verified"]["semantic_review"]["verdict"], "consistent")

    def test_a_wrong_fix_fails(self) -> None:
        session = self._session(snapshot={"calc.py": "0" * 64,
                                          "test_calc.py": hashlib.sha256(self.tests).hexdigest(),
                                          "NOTES.md": "e" * 64, "requirements.txt": "f" * 64})
        metrics = {"first_repair": 0, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        with self.assertRaises(self.module.Failed):
            self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                        metrics, self.pack, lesson=True)

    def test_a_missing_snapshot_blocks(self) -> None:
        session = self._session(snapshot={})
        metrics = {"first_repair": 0, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        with self.assertRaises(self.module.Blocked):
            self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                        metrics, self.pack, lesson=True)

    def test_a_green_run_before_the_repair_fails(self) -> None:
        session = self._session()
        metrics = {"first_repair": 5, "primary": 1, "repeated_reads": 0,
                   "invalid_attempts": 0, "exploration_rounds": 0}
        with self.assertRaises(self.module.Failed):
            self.module._g01_v2_quality(self.gate, self.record, session, "g01-lesson",
                                        metrics, self.pack, lesson=True)

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
                                        metrics, self.pack, lesson=True)


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



class V2EndToEnd(unittest.TestCase):
    """The whole v2 path over synthetic-but-bound sessions: PASS and FAIL."""

    def setUp(self) -> None:
        self.module = import_gate_module()
        self.gate = _real_gate(self, case_id="P4-V2E")
        self.gate.config = {
            "g01_version": "v2",
            "g01_v2_spec_sha256": hashlib.sha256(
                (REPO_ROOT / "docs" / "p4-g01-v2-spec.md").read_bytes()).hexdigest(),
            "g01_v2_prompt_sha256": "P" * 64,
            "g01_v2_prompt_path": "/tmp/g01-session-prompt.txt",
            "g01_v2_fixture_ref": "R" * 40,
            "g01_v2_codex_version": "codex 0.159.3",
            "g01_v2_codex_model": "deepseek",
        }
        self.record = self.module.CaseRecord(case_id="P4-G01", verdict="PASS")
        self.fixed_calc = b"def total(prices, discount):\n    return sum(prices) * (1 - discount / 100)\n"
        self.fixed_tests = (b"import unittest\n\nfrom calc import total\n\n\n"
                            b"class TotalTest(unittest.TestCase):\n"
                            b"    def test_total_applies_discount_as_percent(self):\n"
                            b"        # NOTES.md: the discount is a percentage of the subtotal.\n"
                            b"        self.assertAlmostEqual(total([10.0, 20.0], 5.0), 28.5)\n")
        self.buggy = "def total(prices, discount):\n    return sum(prices) - discount\n"
        self.memory_id = "mem_" + "M" * 26
        self.failure_evidence = "evd_" + "F" * 26
        self.source_task = "tsk_" + "T" * 26
        self.lesson_answer = (f"Historically this failed ({self.memory_id} "
                              f"{self.failure_evidence} from {self.source_task}); I verified "
                              f"the fix on this repository.")
        self.project = self.gate.seed_project("v2-e2e")
        self.baseline_pack = {"task_id": self.gate.new_task(self.project, "v2 baseline"),
                              "memory_selected": []}
        self.lesson_pack = {"task_id": self.gate.new_task(self.project, "v2 lesson"),
                            "memory_selected": [{
            "memory_id": self.memory_id, "memory_type": "FAILURE_LESSON",
            "source_task_id": self.source_task,
            "fact": {"evidence_id": self.failure_evidence}}]}

    def _repo(self, label):
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix=f"p4-v2e-{label}-")
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name).resolve()

    def _bind(self, task_id, events):
        for event in events:
            event["evidence_id"] = self.gate.collect_tool_evidence(
                task_id, command=event["command"], output=event["output"],
                exit_code=event.get("exit_code", 0), tool_name=event["tool_name"])

    def _stream(self, label, events):
        items = []
        for event in events:
            if event["kind"] == "shell":
                items.append({"type": "item.completed", "item": {
                    "type": "command_execution", "command": event["command"],
                    "aggregated_output": event["output"], "exit_code": event["exit_code"]}})
            elif event["kind"] == "file_change":
                items.append({"type": "item.completed", "item": {
                    "type": "file_change", "changes": [
                        {"path": c["path"], "kind": c["kind"]} for c in event["changes"]]}})
        text = "\n".join(json.dumps(item) for item in items)
        path = self.gate.root / f"session-{label}.codex.jsonl"
        path.write_text(text, encoding="utf-8")
        return text

    def _run_events(self, label, repo):
        snapshot = {"calc.py": hashlib.sha256(self.fixed_calc).hexdigest(),
                    "test_calc.py": hashlib.sha256(self.fixed_tests).hexdigest(),
                    "NOTES.md": "e" * 64, "requirements.txt": "f" * 64}
        repair = _file_change_event(
            [("/r/calc.py", "a" * 64, snapshot["calc.py"]),
             ("/r/test_calc.py", "c" * 64, snapshot["test_calc.py"])],
            evidence_id="pending")
        return [
            _shell_event("ls -la", "calc.py\n"),
            _shell_event("cat calc.py test_calc.py NOTES.md",
                         self.buggy + "The discount is in percent, not currency.\n"),
            _shell_event('rg "total" .', "calc.py:1\n"),
            _shell_event("cat requirements.txt", "\n"),
            _shell_event("python -B -m unittest -v", "AssertionError: 25.0 != 27.5",
                         exit_code=1),
            repair,
            _shell_event("python -B -m unittest -v", "Ran 1 test\nOK"),
        ]

    def _session(self, label, repo, task_id, answer, events, review=None):
        events = [dict(event) for event in events]
        self._bind(task_id, events)
        text = self._stream(label, events)
        session = {
            "g01_v2": {"spec_sha256": self.gate.config["g01_v2_spec_sha256"],
                       "prompt_sha256": self.gate.config["g01_v2_prompt_sha256"],
                       "fixture_ref": self.gate.config["g01_v2_fixture_ref"],
                       "codex_version": "codex 0.159.3", "codex_model": "deepseek",
                       "fixture_repo": str(repo)},
            "stream_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "tool_events": events,
            "snapshot": {"calc.py": hashlib.sha256(self.fixed_calc).hexdigest(),
                         "test_calc.py": hashlib.sha256(self.fixed_tests).hexdigest(),
                         "NOTES.md": "e" * 64, "requirements.txt": "f" * 64},
            "task_completed": True,
            "final_verification": {"command": "python -B -m unittest -v",
                                   "output": "Ran 1 test\nOK"},
            "final_answer": answer,
            "memory_seen": ([] if label == "g01-baseline" else [{
                "memory_id": self.memory_id, "evidence_ids": [self.failure_evidence],
                "source_event_ids": ["evt_" + "S" * 26]}]),
            "verified": {},
        }
        if review is not None:
            session["semantic_review"] = review
        return session

    def test_a_pre_registered_pair_decides_and_records_the_metric(self) -> None:
        baseline_repo, lesson_repo = self._repo("b"), self._repo("l")
        baseline = self._session("g01-baseline", baseline_repo, self.baseline_pack["task_id"],
                                 "The discount was subtracted as currency; I fixed it and "
                                 "the suite is green.", self._run_events("g01-baseline",
                                                                          baseline_repo))
        lesson_events = [
            _shell_event("cat calc.py NOTES.md",
                         self.buggy + "The discount is in percent, not currency.\n"),
            _shell_event("python -B -m unittest -v", "AssertionError: 25.0 != 27.5",
                         exit_code=1),
            _file_change_event(
                [("/r/calc.py", "a" * 64, hashlib.sha256(self.fixed_calc).hexdigest()),
                 ("/r/test_calc.py", "c" * 64, hashlib.sha256(self.fixed_tests).hexdigest())],
                evidence_id="pending"),
            _shell_event("python -B -m unittest -v", "Ran 1 test\nOK"),
        ]
        review = {"reviewer": "independent reviewer", "verdict": "consistent",
                  "reviewed_answer_sha256": hashlib.sha256(self.lesson_answer.encode()).hexdigest(),
                  "findings": f'The answer says "{self.lesson_answer[:40]}" and cites it all.'}
        lesson = self._session("g01-lesson", lesson_repo, self.lesson_pack["task_id"],
                               self.lesson_answer, lesson_events, review)
        self.module._verify_g01_v2(self.gate, self.record, baseline, lesson,
                                   self.baseline_pack, self.lesson_pack)
        table = self.record.engine_evidence["g01_v2"]
        self.assertTrue(table["primary_satisfied"], table)
        self.assertTrue(table["secondary_satisfied"], table)
        self.assertEqual(table["baseline"]["primary"], 4)
        self.assertEqual(table["lesson"]["primary"], 1)
        self.assertTrue(any(isinstance(receipt, dict) and
                            receipt.get("kind") == "g01-v2-metric"
                            for receipt in self.record.receipts))

    def test_an_echo_of_the_anchors_does_not_complete_the_observation_set(self) -> None:
        events = [
            _shell_event("echo 'python -B -m unittest' 'AssertionError: 25.0 != 27.5'", ""),
            _shell_event("echo 'return sum(prices) - discount'", ""),
            _shell_event("echo 'The discount is in percent, not currency.'", ""),
            _file_change_event(
                [("/r/calc.py", "a" * 64, hashlib.sha256(self.fixed_calc).hexdigest())]),
            _shell_event("python -B -m unittest -v", "Ran 1 test\nOK"),
        ]
        metrics = self.module._g01_v2_classify(
            {"tool_events": events,
             "snapshot": {"calc.py": hashlib.sha256(self.fixed_calc).hexdigest()}},
            "the lesson")
        self.assertFalse(metrics["o_star_complete"])
        self.assertIsNotNone(metrics["first_repair"])

    def test_an_unknown_native_item_blocks(self) -> None:
        event = _event("unknown", tool_name="unknown:web_search",
                       evidence_id="evd_" + "U" * 26, command="unknown:web_search",
                       output="{}", exit_code=0)
        with self.assertRaises(self.module.Blocked):
            self.module._g01_v2_classify({"tool_events": [event]}, "the lesson")

    def test_the_two_sessions_must_name_distinct_worktrees(self) -> None:
        shared = self._repo("shared")
        def side(label):
            text = self._stream(label, [])
            return {
                "g01_v2": {"spec_sha256": self.gate.config["g01_v2_spec_sha256"],
                           "prompt_sha256": self.gate.config["g01_v2_prompt_sha256"],
                           "fixture_ref": self.gate.config["g01_v2_fixture_ref"],
                           "codex_version": "codex 0.159.3", "codex_model": "deepseek",
                           "fixture_repo": str(shared)},
                "stream_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "memory_seen": [], "tool_events": [], "verified": {},
            }
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g01_v2(self.gate, self.record, side("g01-baseline"),
                                       side("g01-lesson"),
                                       self.baseline_pack, self.lesson_pack)
        self.assertIn("same worktree", str(caught.exception))


class AssemblerEndToEnd(unittest.TestCase):
    """`p4-memory-g01-session.py` produces a session the v2 Gate can bind."""

    def test_the_assembler_binds_shell_and_file_change_with_real_exit_codes(self) -> None:
        import os
        import tempfile
        gate_module = import_gate_module()
        tmp = tempfile.TemporaryDirectory(prefix="p4-v2-asm-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / "repo"
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
        hook = b"# hook\n"
        jasmine = repo / ".codex" / "jasmine"
        jasmine.mkdir(parents=True)
        (jasmine / "jasmine-p4-session-start.py").write_bytes(hook)

        work = root / "work"
        case = work / "P4-G01"
        case.mkdir(parents=True)
        config = {"run_id": "p4-asm", "bank_suffix": "p4-asm"}
        os.environ.setdefault("JASMINE_HINDSIGHT_URL", "http://127.0.0.1:1")
        gate = gate_module.Gate(work, config, "P4-G01")
        gate.open()
        try:
            project = gate.seed_project("asm")
            task = gate.new_task(project, "assembler task")
        finally:
            gate.close()
        pack = {"idempotency_key": "g01-with-lesson",
                "context_pack_id": "ctx_" + "C" * 26,
                "rendered_sha256": "r" * 64, "task_id": task,
                "memory_selected": [], "qualified_provenance": []}
        (case / "pack-ctx.json").write_text(json.dumps(pack), encoding="utf-8")

        thread = "01a110ad-fdda-7220-bb45-867cc9a8c396"
        (jasmine / f"session-start-{thread}.json").write_text(json.dumps({
            "session_id": thread, "context_pack_id": pack["context_pack_id"],
            "pack_sha256": pack["rendered_sha256"],
            "injected_sha256": pack["rendered_sha256"],
            "hook_script_sha256": hashlib.sha256(hook).hexdigest(),
            "source": "startup", "cwd": str(repo),
            "injected_via": "hookSpecificOutput.additionalContext"}),
            encoding="utf-8")
        # The session's work: read, fail, repair via apply_patch, verify green.
        (repo / "calc.py").write_bytes(
            b"def total(prices, discount):\n    return sum(prices) * (1 - discount / 100)\n")
        (repo / "test_calc.py").write_bytes(b"expected 28.5\n")
        transcript = root / "stream.jsonl"
        transcript.write_text("\n".join(json.dumps(event) for event in [
            {"type": "thread.started", "thread_id": thread},
            {"type": "item.completed", "item": {
                "type": "command_execution", "command": "cat calc.py NOTES.md",
                "aggregated_output": "return sum(prices) - discount\npercent\n",
                "exit_code": 0}},
            {"type": "item.completed", "item": {
                "type": "command_execution", "command": "python -B -m unittest -v",
                "aggregated_output": "Ran 1 test\nAssertionError: 25.0 != 27.5",
                "exit_code": 1}},
            {"type": "item.completed", "item": {
                "type": "file_change", "changes": [
                    {"path": str(repo / "calc.py"), "kind": "update"},
                    {"path": str(repo / "test_calc.py"), "kind": "update"}]}},
            {"type": "item.completed", "item": {
                "type": "command_execution", "command": "python -B -m unittest -v",
                "aggregated_output": "Ran 1 test\nOK", "exit_code": 0}},
        ]), encoding="utf-8")
        last = root / "last.txt"
        last.write_text(json.dumps({"final_answer": "The discount was subtracted as "
                                                  "currency; fixed and verified."}),
                        encoding="utf-8")
        (case / "op.json").write_text(json.dumps(config), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "p4-memory-g01-session.py"),
             "--transcript", str(transcript), "--last", str(last),
             "--label", "g01-lesson", "--pack-key", "g01-with-lesson",
             "--work", str(work), "--config", str(case / "op.json"),
             "--repo", str(repo), "--ref", ref],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(SRC)})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        session = json.loads((case / "session-g01-lesson.json").read_text())
        self.assertEqual([event["kind"] for event in session["tool_events"]],
                         ["shell", "shell", "file_change", "shell"])
        self.assertEqual(session["tool_events"][1]["exit_code"], 1)
        change = session["tool_events"][2]
        self.assertIn("file_change:", change["command"])
        self.assertNotEqual(json.loads(change["output"])[0]["before_sha256"],
                            json.loads(change["output"])[0]["after_sha256"])
        self.assertTrue(all(event["evidence_id"] for event in session["tool_events"]))
        self.assertTrue(session["tool_actions"])
        self.assertEqual(session["snapshot"]["calc.py"],
                         hashlib.sha256((repo / "calc.py").read_bytes()).hexdigest())



if __name__ == "__main__":
    unittest.main()
