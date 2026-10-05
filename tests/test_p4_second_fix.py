"""Second fix round: reverse tests for items A-G.

Each class proves its gate fix refuses the defect it closes. No engine/network.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import import_gate_module


def _module():
    return import_gate_module()


def _real_gate(testcase, *, case_id="P4-TST"):
    from jasmine_core import db, ids, registry
    from jasmine_core.migrations import migrate

    module = _module()
    tmp = tempfile.TemporaryDirectory(prefix="p4-second-")
    testcase.addCleanup(tmp.cleanup)
    root = Path(tmp.name) / "work" / case_id
    root.mkdir(parents=True)
    workspace = Path(tmp.name) / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "source.txt").write_text("v1\n", encoding="utf-8")
    gate = module.Gate.__new__(module.Gate)
    gate.root = root
    gate.case_id = case_id
    gate.workspace = workspace
    gate.host = ids.new_id("hst")
    gate.human = ids.new_id("act")
    gate.system = ids.new_id("act")
    gate.agent = ids.new_id("act")
    gate.db_path = root / "core.db"
    gate.config = {"run_id": "p4-second", "bank_suffix": "p4-second"}
    gate.run_id = "p4-second"
    gate.bank_suffix = "p4-second"
    gate.transport = None
    gate._previous = {}
    conn = db.connect(gate.db_path)
    migrate(conn)
    with db.transaction(conn):
        reg = registry.Registry(conn)
        reg.upsert_host(gate.host, display_name="p4 second fix")
        for actor_id, kind in ((gate.human, "human"), (gate.system, "system"),
                               (gate.agent, "agent")):
            reg.upsert_actor(actor_id, kind=kind, home_host_id=gate.host)
    conn.close()
    environment = mock.patch.dict("os.environ", {
        "JASMINE_CORE_WORKSPACE_ROOT": str(workspace),
        "JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["source.txt"]'}, clear=False)
    environment.start()
    testcase.addCleanup(environment.stop)
    return gate


def _review_for(answer, **kwargs):
    reviewer = kwargs.get("reviewer", "a reviewer")
    verdict = kwargs.get("verdict", "consistent")
    digest = kwargs.get("digest", hashlib.sha256(answer.encode("utf-8")).hexdigest())
    findings = kwargs.get("findings", f"quotes {answer[:40]!r}: consistent")
    return {"reviewer": reviewer, "verdict": verdict,
            "reviewed_answer_sha256": digest, "findings": findings}


# -- Item A ---------------------------------------------------------------

class ExplorationDerivedFromVerifiedActions(unittest.TestCase):
    def setUp(self):
        self.module = _module()

    def _session(self, commands, declared_true):
        return {
            "tool_actions": [{"command": c, "output": "out"} for c in commands],
            "exploration": {name: (name in declared_true)
                            for name in self.module.EXPLORATION_CATEGORIES},
        }

    def test_honest_full_declaration_passes(self):
        commands = ["ls", "git log --oneline", "python --version",
                    "cat README.md", "pytest -q", "git stash list",
                    'python -c "print(1+1)"']
        declared = {"enumerate_repository", "read_history", "inspect_toolchain",
                    "read_documentation", "reproduce_failure",
                    "inspect_build_artifacts", "probe_arithmetic"}
        derived = self.module._require_exploration_derived(
            self._session(commands, declared), "the session")
        self.assertEqual(sorted(derived), sorted(declared))

    def test_same_commands_with_different_booleans_fail(self):
        commands = ["ls", "git log --oneline"]
        honest = {"enumerate_repository", "read_history"}
        self.module._require_exploration_derived(
            self._session(commands, honest), "the session")
        dishonest = {"enumerate_repository"}
        with self.assertRaises(self.module.Failed) as caught:
            self.module._require_exploration_derived(
                self._session(commands, dishonest), "the session")
        self.assertIn("read_history", str(caught.exception))

    def test_declared_true_with_no_matching_command_fails(self):
        with self.assertRaises(self.module.Failed) as caught:
            self.module._require_exploration_derived(
                self._session(["ls"], {"reproduce_failure"}), "the session")
        self.assertIn("reproduce_failure", str(caught.exception))
        self.assertIn("no matching command", str(caught.exception))

    def test_matching_command_with_declared_false_fails(self):
        with self.assertRaises(self.module.Failed) as caught:
            self.module._require_exploration_derived(
                self._session(["pytest -q"], set()), "the session")
        self.assertIn("reproduce_failure", str(caught.exception))
        self.assertIn("declared false", str(caught.exception))


# -- Item B ---------------------------------------------------------------

def _history_block(*, memory_id, command, task, step, evidence_id, sources, project):
    history = (f"history: prior FAIL result from `{command}` (task {task}, step "
               f"{step}; memory {memory_id}; evidence {evidence_id}; sources "
               f"{','.join(sources)})")
    return ("HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n"
            + json.dumps({"memory_id": memory_id, "memory_type": "FAILURE_LESSON",
                          "bank": {"scope": "PROJECT",
                                   "bank_id": f"jasmine-project-{project}",
                                   "project_id": project},
                          "source_event_ids": list(sources), "source_task_id": task,
                          "evidence_ids": [evidence_id], "fact": "x",
                          "text": history + "\nx"},
                         separators=(",", ":")) + "\n")


class HistoryBoundToStoredHash(unittest.TestCase):
    def setUp(self):
        from jasmine_core import ids
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G01")
        self.project = self.gate.seed_project("b-hash")
        self.task = self.gate.new_task(self.project, "hash task")
        self.evidence_id = self.gate.collect_tool_evidence(
            self.task, command="pytest -q", output="FAILED\n", exit_code=1)
        conn = self.gate.conn()
        try:
            row = conn.execute("SELECT change_event_id FROM evidence WHERE evidence_id=?",
                               (self.evidence_id,)).fetchone()
            self.source = row["change_event_id"]
        finally:
            conn.close()
        self.memory_id = ids.new_id("mem")
        self.step = "stp_" + "S" * 26

    def _pack(self, command):
        block = _history_block(memory_id=self.memory_id, command=command,
                               task=self.task, step=self.step,
                               evidence_id=self.evidence_id,
                               sources=[self.source], project=self.project)
        rendered = ("JASMINE CONTEXT v1\n{}\nAUTHORITY\nCURRENT STATE\n"
                    "MEMORY \u2014 NON AUTHORITATIVE\n{\"status\":\"ok\"}\n" + block)
        return {"context_pack_id": "ctx_" + "C" * 26, "project_id": self.project,
                "task_id": self.task, "memory_selected": [{
                    "memory_id": self.memory_id, "source_task_id": self.task}],
                "rendered_content": rendered}

    def test_matching_fallback_but_not_stored_hash_is_refused(self):
        # Both sides re-derive "pytest -q -k other" through the shared fallback,
        # so the string check passes; the stored hash still names "pytest -q".
        pack = self._pack("pytest -q -k other")
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g01_actionable_history(
                pack,
                resolve_command=lambda eid: "pytest -q -k other",
                resolve_command_sha256=lambda eid: hashlib.sha256(
                    "pytest -q".encode()).hexdigest())
        self.assertIn("not the stored bytes", str(caught.exception))

    def test_honest_hash_passes(self):
        pack = self._pack("pytest -q")
        self.module._verify_g01_actionable_history(
            pack,
            resolve_command=lambda eid: "pytest -q",
            resolve_command_sha256=lambda eid: hashlib.sha256(
                "pytest -q".encode()).hexdigest())

    def test_memory_with_no_evidence_row_is_refused(self):
        from jasmine_core import ids
        missing = ids.new_id("evd")
        block = _history_block(memory_id=self.memory_id, command="pytest -q",
                               task=self.task, step=self.step,
                               evidence_id=missing,
                               sources=[self.source], project=self.project)
        rendered = ("JASMINE CONTEXT v1\n{}\nAUTHORITY\nCURRENT STATE\n"
                    "MEMORY \u2014 NON AUTHORITATIVE\n{\"status\":\"ok\"}\n" + block)
        pack = {"context_pack_id": "ctx_" + "C" * 26, "project_id": self.project,
                "task_id": self.task, "memory_selected": [{
                    "memory_id": self.memory_id, "source_task_id": self.task}],
                "rendered_content": rendered}
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g01_actionable_history(
                pack,
                resolve_command=lambda eid: None,
                resolve_command_sha256=lambda eid: None)
        self.assertIn("no", str(caught.exception).lower())


# -- Item C ---------------------------------------------------------------

class FindingsMustQuoteTheAnswer(unittest.TestCase):
    def setUp(self):
        self.module = _module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-quote-")
        self.addCleanup(self._tmp.cleanup)
        self.gate = self.module.Gate.__new__(self.module.Gate)
        self.gate.root = Path(self._tmp.name)

    def _session(self, answer, findings, **kwargs):
        session = {"final_answer": answer,
                   "semantic_review": _review_for(answer, findings=findings, **kwargs)}
        return session

    def test_findings_without_any_quote_refused(self):
        answer = "There is no record of the device 42 protocol test in Core today."
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.semantic_review(
                self._session(answer, "looks good to me"), "the G03 session")
        self.assertIn("quotes none", str(caught.exception))

    def test_findings_quoting_a_different_answer_refused(self):
        answer = "There is no record of the device 42 protocol test in Core today."
        other = "Zebras ponder quantum foam while juggling small bright lanterns."
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.semantic_review(
                self._session(answer, f"quotes {other[:30]!r}: consistent"),
                "the G03 session")
        self.assertIn("quotes none", str(caught.exception))

    def test_findings_quoting_the_reviewed_answer_pass(self):
        answer = "There is no record of the device 42 protocol test in Core today."
        reviewed = self.gate.semantic_review(
            self._session(answer, f"quotes {answer[:30]!r}: consistent"),
            "the G03 session")
        self.assertEqual(reviewed["verdict"], "consistent")

    def test_docstring_disclaims_unforgeability(self):
        self.assertIn("it does not make the review unforgeable",
                      self.module.Gate.semantic_review.__doc__)


# -- Item D ---------------------------------------------------------------

class RetiredTextAnchoredToCore(unittest.TestCase):
    def setUp(self):
        from jasmine_core import SCHEMA_VERSION, clock, db
        from jasmine_core.events import EventStore
        from jasmine_core.memory import scope
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.memory.types import Provenance
        from jasmine_core.models import NewEvent

        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G07")
        self.project = self.gate.seed_project("g07-core-text")
        self.live_task = self.gate.new_task(self.project, "live")
        self.live_event = self.gate.event(self.project, "user.prompt", "failures?",
                                          task_id=self.live_task)
        retired_task = self.gate.new_task(self.project, "retired")
        retired_event = self.gate.event(self.project, "user.prompt", "old",
                                        task_id=retired_task)
        conn = self.gate.conn()
        try:
            with db.transaction(conn):
                store = MemoryStore(conn, schema_version=SCHEMA_VERSION)
                self.retired_id = store.insert_item(
                    bank=scope.project_bank(self.project),
                    memory_type="FAILURE_LESSON", document_id="doc-r-1",
                    source_event_id=retired_event,
                    provenance=Provenance(source_event_ids=(retired_event,),
                                          evidence_ids=("evd_" + "2" * 26,)),
                    sanitized_text="core lesson text about the retry budget",
                    content_sha256="c" * 64, task_id=retired_task,
                    at=clock.now_rfc3339())
                conn.execute("UPDATE memory_items SET status='OBSOLETE' WHERE memory_id=?",
                             (self.retired_id,))
                EventStore(conn, schema_version=SCHEMA_VERSION).append(NewEvent(
                    event_type="memory.obsoleted", source_system="test",
                    source_event_id=None, occurred_at=clock.now(),
                    actor_id=self.gate.human, actor_kind="human",
                    host_id=self.gate.host, project_id=self.project,
                    task_id=self.live_task, payload={"text": ""}))
            # Receipt text deliberately disagrees with Core.
            self.gate.receipt("g07-retired", {"memory_id": self.retired_id,
                                              "text": "receipt nonsense about owls",
                                              "project_id": self.project})
        finally:
            conn.close()
        from test_p4_fix_round import _insert_pack_row, _write_session
        self._insert_pack_row = _insert_pack_row
        self._write_session = _write_session

    def _run(self, answer, suffix="a"):
        from jasmine_core import ids
        live_id = "mem_" + "L" * 26
        live_evidence = "evd_" + "1" * 26
        live_task = self.gate.new_task(self.project, f"live-{suffix}")
        live_event = self.gate.event(self.project, "user.prompt", "failures?",
                                     task_id=live_task)
        marker = "HISTORICAL_NONAUTHORITATIVE MEMORY DATA"
        rendered = ("JASMINE CONTEXT v1\n{}\nAUTHORITY\nCURRENT STATE\n"
                    "MEMORY \u2014 NON AUTHORITATIVE\n{\"status\":\"ok\"}\n"
                    f"{marker}\n"
                    + json.dumps({"memory_id": live_id,
                                  "source_event_ids": [live_event]},
                                 separators=(",", ":")) + "\n")
        pack_id = ids.new_id("ctx")
        pack = {"context_pack_id": pack_id, "project_id": self.project,
                "task_id": live_task, "prompt_event_id": live_event,
                "memory_status": "ok",
                "memory_selected": [{"memory_id": live_id,
                                     "source_event_ids": [live_event]}],
                "memory_filtered": [], "qualified_provenance": [{
                    "memory_id": live_id, "evidence_ids": [live_evidence],
                    "source_event_ids": [live_event]}],
                "truth_digest": "d" * 64, "check_current": True,
                "rendered_content": rendered, "sections": [], "token_count": 1,
                "receipt": {"path": None, "sha256": None}}
        self._insert_pack_row(self.gate, pack_id=pack_id, project=self.project,
                              task=live_task, rendered=rendered,
                              truth_digest="d" * 64, command_event=live_event,
                              source_event=live_event)
        # Fresh session bytes per attempt: remove any prior receipt/transcript so
        # the seal-mismatch path is not what is exercised here.
        for name in ("session-g07.json", "session-g07.transcript.json",
                     "injected-g07.pack.json", "g07-lifecycle-pack.json",
                     "g07-session.json"):
            try:
                (self.gate.root / name).unlink()
            except FileNotFoundError:
                pass
        self._write_session(self.gate, "g07", {
            "commit": self.gate.git_commit(), "context_pack_id": pack_id,
            "pack_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "memory_seen": [{"memory_id": live_id, "evidence_ids": [live_evidence],
                             "source_event_ids": [live_event]}],
            "tool_actions": [], "final_answer": answer})
        record = self.module.CaseRecord("P4-G07", "PASS")
        self.module._verify_g07_pack(self.gate, record, pack)
        return record

    def test_receipt_text_disagreement_judged_by_core_text(self):
        # Answer repeats the receipt's nonsense: passes, proving the check did
        # not search the receipt's text.
        record = self._run("I remember the live lesson from history; "
                           "receipt nonsense about owls was mentioned.", suffix="r1")
        self.assertTrue(record.engine_evidence["retired_premise"]["retired_text_anchored"])
        # Answer repeats Core's stored text: refused, proving the check searched
        # Core's text.
        with self.assertRaises(self.module.Failed) as caught:
            self._run("I remember the live lesson from history; "
                      "core lesson text about the retry budget was mentioned.", suffix="r2")
        self.assertIn("obsoleted memory", str(caught.exception))

    def test_row_with_no_text_records_explicit_false(self):
        from jasmine_core import SCHEMA_VERSION, clock, db
        from jasmine_core.memory import scope
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.memory.types import Provenance
        empty_task = self.gate.new_task(self.project, "empty retired")
        empty_event = self.gate.event(self.project, "user.prompt", "empty",
                                      task_id=empty_task)
        conn = self.gate.conn()
        try:
            with db.transaction(conn):
                store = MemoryStore(conn, schema_version=SCHEMA_VERSION)
                empty_id = store.insert_item(
                    bank=scope.project_bank(self.project),
                    memory_type="FAILURE_LESSON", document_id="doc-r-empty",
                    source_event_id=empty_event,
                    provenance=Provenance(source_event_ids=(empty_event,),
                                          evidence_ids=("evd_" + "3" * 26,)),
                    sanitized_text="", content_sha256="d" * 64,
                    task_id=empty_task, at=clock.now_rfc3339())
                conn.execute("UPDATE memory_items SET status='OBSOLETE' WHERE memory_id=?",
                             (empty_id,))
            try:
                (self.gate.root / "g07-retired.json").unlink()
            except FileNotFoundError:
                pass
            self.gate.receipt("g07-retired", {"memory_id": empty_id,
                                              "text": "whatever receipt says",
                                              "project_id": self.project})
        finally:
            conn.close()
        record = self._run("I remember the live lesson from history.", suffix="e1")
        premise = record.engine_evidence["retired_premise"]
        self.assertFalse(premise["retired_text_anchored"])
        self.assertIn("no stored text", premise["retired_text_anchor_reason"])


# -- Item E ---------------------------------------------------------------

class EveryActionCapturedWithTaskAndOutputSeed(unittest.TestCase):
    def setUp(self):
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G05")
        self.project = self.gate.seed_project("g05-every")
        self.task = self.gate.new_task(self.project, "every action")

    def test_second_fictional_action_refused(self):
        self.gate.collect_tool_evidence(self.task, command="git status",
                                        output="clean\n", exit_code=0)
        from test_p4_fix_round import _insert_pack_row, _pack_dict, _write_session
        rendered = ("JASMINE CONTEXT v1\n{}\nAUTHORITY\nCURRENT STATE\n"
                    "MEMORY \u2014 NON AUTHORITATIVE\n")
        pack_id = "ctx_" + "E" * 26
        prompt = self.gate.event(self.project, "user.prompt", "go", task_id=self.task)
        pack = _pack_dict(pack_id=pack_id, project=self.project, task=self.task,
                          prompt=prompt, rendered=rendered)
        _insert_pack_row(self.gate, pack_id=pack_id, project=self.project,
                         task=self.task, rendered=rendered, truth_digest="d" * 64,
                         command_event=prompt, source_event=prompt)
        _write_session(self.gate, "t1", {
            "commit": self.gate.git_commit(), "context_pack_id": pack_id,
            "pack_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "tool_actions": [{"command": "git status", "output": "clean\n"},
                             {"command": "rm -rf /tmp/elsewhere", "output": "done"}]})
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.verify_session(pack, "t1", require_tool_actions=True,
                                     require_evidence_backed_actions=True)
        self.assertIn("rm -rf /tmp/elsewhere", str(caught.exception))

    def test_same_command_different_output_yields_different_ids(self):
        # Distinct executions get distinct rows; nothing is merged and nothing
        # is refused. Identical repeats still reuse (see
        # test_unified_derivation_same_inputs_same_id).
        first = self.gate.collect_tool_evidence(self.task, command="pytest -q",
                                                output="Ran 1 test\n\nOK\n")
        second = self.gate.collect_tool_evidence(self.task, command="pytest -q",
                                                  output="something else entirely")
        self.assertNotEqual(first, second)
        conn = self.gate.conn()
        try:
            rows = conn.execute("SELECT evidence_id FROM evidence WHERE task_id=?",
                                (self.task,)).fetchall()
        finally:
            conn.close()
        self.assertEqual([r["evidence_id"] for r in rows], [first, second])

    def test_unified_derivation_same_inputs_same_id(self):
        first = self.gate.collect_tool_evidence(self.task, command="ls",
                                                output="a\n", exit_code=0)
        again = self.gate.collect_tool_evidence(self.task, command="ls",
                                                output="a\n", exit_code=0)
        self.assertEqual(first, again)

    def test_record_captures_every_action(self):
        self.gate.collect_tool_evidence(self.task, command="ls",
                                        output="a\n", exit_code=0)
        self.gate.collect_tool_evidence(self.task, command="git status",
                                        output="clean\n", exit_code=0)
        pack = {"task_id": self.task}
        session = {"tool_actions": [{"command": "ls", "output": "a\n"},
                                    {"command": "git status", "output": "clean\n"}]}
        record = self.module.CaseRecord("P4-G05", "PASS")
        self.module._record_session_tool_evidence(self.gate, record, pack, session)
        self.assertEqual(len(record.evidence_ids), 2)
        payload = self.gate.read_receipt("g05-tool-evidence")["payload"]
        self.assertEqual(payload["evidence_ids"], record.evidence_ids)
        self.assertEqual(len(payload["evidence_ids"]), 2)
        conn = self.gate.conn()
        try:
            n = conn.execute("SELECT COUNT(*) AS n FROM evidence WHERE task_id=?",
                             (self.task,)).fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(payload["evidence_rows_for_task"], n)


# -- Item F ---------------------------------------------------------------

class ResumePathsReestablishPreconditions(unittest.TestCase):
    def test_every_case_calls_namespace_before_any_early_return(self):
        import inspect
        module = _module()
        for case_id, func in module.CASES_BY_ID.items():
            with self.subTest(case=case_id):
                source = inspect.getsource(func)
                lines = source.splitlines()
                ns_at = next((i for i, line in enumerate(lines)
                              if "require_bank_namespace" in line), None)
                self.assertIsNotNone(ns_at, f"{case_id} never calls require_bank_namespace")
                returns = [i for i, line in enumerate(lines)
                           if line.strip().startswith("return")]
                self.assertTrue(returns, f"{case_id} has no return")
                self.assertLess(
                    ns_at, min(returns),
                    f"{case_id} returns before establishing the bank namespace")


class RecoveryDrainsBeforeItRecalls(unittest.TestCase):
    def test_g05_recovery_drain_precedes_recall(self):
        # Found by the first live run under the new G05 flow, not by any unit
        # test: the recovery recall ran before the post-recovery drain, at which
        # point the outage had held every job PENDING and the engine genuinely
        # held no documents -- so the recall correctly returned `empty` and the
        # `status == "ok"` check failed on correct engine behaviour. The comment
        # above the block always described drain-then-recall; the code said the
        # opposite. This pins the order.
        import inspect

        module = _module()
        source = inspect.getsource(module.case_g05)
        lines = source.splitlines()
        recovery_at = next(i for i, line in enumerate(lines)
                           if "Recovery: the engine answers again" in line)
        tail = lines[recovery_at:]
        drain_at = next(i for i, line in enumerate(tail)
                        if "gate.drain(conn)" in line)
        recall_at = next(i for i, line in enumerate(tail)
                         if 'gate.recall(project, "device test"' in line)
        self.assertLess(
            drain_at, recall_at,
            "G05 recovery recalls before draining: with every job still PENDING "
            "the engine holds nothing and the recall can only return empty")


# -- Item G ---------------------------------------------------------------

class CompletedComparesLiveAttempts(unittest.TestCase):
    def _pending_job(self, gate, project):
        from jasmine_core import SCHEMA_VERSION, ids
        from jasmine_core.evidence import EvidenceStore
        from jasmine_core.memory import eligibility
        from jasmine_core.memory import jobs as jobs_module
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.state import StateStore

        task = gate.new_task(project, "g06 job")
        conn = gate.conn()
        try:
            state = StateStore(conn, schema_version=SCHEMA_VERSION)
            step = state.create_step(task, {"title": "t", "host_id": gate.host,
                                            "expected_revision": 1},
                                     actor_id=gate.human, actor_kind="human",
                                     can_accept=False)["step"]["step_id"]
            state.transition_step(step, {"status": "IN_PROGRESS", "expected_revision": 1,
                                         "host_id": gate.host},
                                  actor_id=gate.human, actor_kind="human",
                                  can_accept=False)
            state.transition_step(step, {"status": "FAILED", "reason": "x",
                                         "expected_revision": 2, "host_id": gate.host},
                                  actor_id=gate.human, actor_kind="human",
                                  can_accept=False)
            EvidenceStore(conn, schema_version=SCHEMA_VERSION).record_tool_result(
                {"task_id": task, "step_id": step, "host_id": gate.host,
                 "codex_session_id": gate.case_id,
                 "turn_id": f"t-{ids.new_id('evt')[-8:]}",
                 "tool_use_id": ids.new_id("evt")[-8:], "tool_name": "pytest",
                 "tool_input": {"cmd": "pytest -q"},
                 "tool_response": {"exit_code": 1},
                 "artifact_uri": "workspace:/source.txt"},
                actor_id=gate.system)
            report = jobs_module.JobStore(
                conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)
            ).enqueue_from_sources(eligibility.SourceRegistry())
            assert report["created"]
            job = [jobs_module.job_row(r) for r in conn.execute(
                "SELECT * FROM memory_jobs WHERE task_id=? AND kind='retain'",
                (task,)).fetchall()][0]
            return task, job
        finally:
            conn.close()

    def _finish_job_with_attempts(self, gate, job, n_attempts):
        from jasmine_core import db, ids

        conn = gate.conn()
        try:
            with db.transaction(conn):
                conn.execute("UPDATE memory_jobs SET status='DONE',"
                             " completed_at='2026-01-02T00:00:00.000000Z'"
                             " WHERE job_id=?", (job["job_id"],))
                for n in range(1, n_attempts + 1):
                    conn.execute(
                        "INSERT INTO memory_job_attempts (attempt_id, job_id,"
                        " attempt_number, phase, outcome, lease_owner, started_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (ids.new_id("mja"), job["job_id"], n, "RETAINED",
                         "SUCCESS", "owner", "2026-01-02T00:00:00.000000Z"))
        finally:
            conn.close()

    def _completed_case(self, gate, project, job, n_live, n_marker):
        doc_id = job["document_id"]
        gate.receipt("g06-document", {"id": doc_id})
        gate.engine_units = lambda bank, document_id, **kw: [{"state": "valid"}]
        gate.engine_document = lambda bank, document_id: {
            "id": doc_id, "memory_unit_count": 1}
        done = {"commit": gate.git_commit(), "project_id": project,
                "document_id": doc_id, "interrupted_job": job["job_id"],
                "attempts": n_marker, "memory_unit_count": 1}
        return done

    def test_marker_count_must_equal_live_count(self):
        module = _module()
        gate = _real_gate(self, case_id="P4-G06")
        project = gate.seed_project("g06-attempts")
        gate.confirm_decision(project, content="Always run migrations",
                              rule_key="migrate.first")
        _, job = self._pending_job(gate, project)
        self._finish_job_with_attempts(gate, job, 2)
        done = self._completed_case(gate, project, job, 2, 5)
        with self.assertRaises(module.Failed) as caught:
            module._verify_g06_completed(gate, module.CaseRecord("P4-G06", "PASS"),
                                         done)
        message = str(caught.exception)
        self.assertIn("5", message)
        self.assertIn("2", message)

    def test_matching_counts_pass(self):
        module = _module()
        gate = _real_gate(self, case_id="P4-G06")
        project = gate.seed_project("g06-attempts-ok")
        gate.confirm_decision(project, content="Always run migrations",
                              rule_key="migrate.first")
        _, job = self._pending_job(gate, project)
        self._finish_job_with_attempts(gate, job, 1)
        done = self._completed_case(gate, project, job, 1, 1)
        record = module.CaseRecord("P4-G06", "PASS")
        module._verify_g06_completed(gate, record, done)
        self.assertEqual(record.engine_evidence["attempt_count"], 1)


if __name__ == "__main__":
    unittest.main()
