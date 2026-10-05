"""The P4 fix round: every vacuous PASS from the audit must now be refusable.

Each class below drives the real verifier against a throwaway database and
supplies the bad input the audit proved the old code accepted: a forged tool
action with no Core row, a history the pack invented, an obsoleted memory
leaking by id, an empty recall leg, a job that drained during the outage, an
answer reviewed against a different digest. Happy-path-only tests do not count
here -- every fix needs the reverse test that proves the gate refuses.

No test here needs the engine or the network: the Evidence store, the
Authority/State/Continuity stores and the verifiers all run against a fresh
migrated Core per test. The live engine halves remain the orchestrator's run.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import SRC, import_gate_module


def _module():
    return import_gate_module()


# -- a real Gate over a throwaway Core --------------------------------------

def _real_gate(testcase, *, case_id="P4-TST"):
    """A genuine `Gate` with a migrated database, registry identities and env.

    `Gate.__new__` rather than the constructor: the constructor derives state
    from an operator config and a work layout the tests build by hand instead.
    Everything the verifiers touch -- `conn()`, `workspace`, the actor ids,
    `receipt`, `git_commit` -- is the real attribute.
    """
    from jasmine_core import db, ids, registry
    from jasmine_core.migrations import migrate

    module = _module()
    tmp = tempfile.TemporaryDirectory(prefix="p4-fix-")
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
    gate.config = {"run_id": "p4-fix", "bank_suffix": "p4-fix"}
    gate.run_id = "p4-fix"
    gate.bank_suffix = "p4-fix"
    gate.transport = None
    gate._previous = {}
    conn = db.connect(gate.db_path)
    migrate(conn)
    with db.transaction(conn):
        reg = registry.Registry(conn)
        reg.upsert_host(gate.host, display_name="p4 fix round")
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


def _insert_pack_row(gate, *, pack_id, project, task, rendered, truth_digest,
                     command_event, source_event):
    """A `context_packs` row the session verifier re-derives its digests from."""
    from jasmine_core import db

    columns = ("context_pack_id, command_event_id, source_event_id, actor_id, host_id,"
               " project_id, task_id, reason, trigger_provenance_json, projection_version,"
               " projection_json, truth_digest, resume_comparison_json, workspace_json,"
               " workspace_digest, sample_window_json, config_digest, renderer_version,"
               " rendered_content, rendered_sha256, rendered_utf8_bytes, tokenizer_json,"
               " token_count, section_metrics_json, omissions_json, memory_json,"
               " full_result_json, created_at").split(", ")
    values = (pack_id, command_event, source_event, gate.agent, gate.host, project,
              task, "USER_PROMPT", "{}", "jasmine.task-snapshot.v1", "{}",
              truth_digest, "{}", "{}", "a" * 64, "{}", "b" * 64,
              "jasmine.context.v1", rendered,
              hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
              len(rendered.encode("utf-8")), "{}", 0, "[]", "[]", "{}", "{}",
              "2026-01-01T00:00:00.000000Z")
    assert len(columns) == len(values), (len(columns), len(values))
    conn = gate.conn()
    try:
        with db.transaction(conn):
            conn.execute(
                f"INSERT INTO context_packs ({', '.join(columns)})"
                f" VALUES ({', '.join('?' * len(values))})", values)
    finally:
        conn.close()


def _write_session(gate, label, session):
    path = gate.session_path(label)
    path.write_bytes(json.dumps(session, ensure_ascii=False).encode("utf-8"))
    return path


def _pack_dict(*, pack_id, project, task, prompt, rendered, truth_digest="d" * 64,
               selected=(), provenance=(), status="ok", check_current=True):
    return {
        "context_pack_id": pack_id, "project_id": project, "task_id": task,
        "prompt_event_id": prompt, "memory_status": status,
        "memory_selected": list(selected), "memory_filtered": [],
        "qualified_provenance": list(provenance), "truth_digest": truth_digest,
        "check_current": check_current, "rendered_content": rendered,
        "sections": [], "token_count": 0,
        "receipt": {"path": None, "sha256": None},
    }


def _review_for(answer, *, verdict="consistent", digest=None, reviewer="a reviewer"):
    return {"reviewer": reviewer, "verdict": verdict,
            "reviewed_answer_sha256": (digest if digest is not None
                                       else hashlib.sha256(answer.encode("utf-8")).hexdigest()),
            "findings": f"reviewed the quoted answer {answer[:60]!r}: consistent"}


# -- STEP 1: tool actions bound to Core Evidence ------------------------------

class ToolActionsBindToCoreEvidence(unittest.TestCase):
    """`tool_actions` were pure self-report: a command plus an output key.

    A session could invent both. Now each declared action must bind to a Core
    Evidence row on the pack's own task -- command hash, output artifact hash,
    exit status and task binding -- or the case fails naming the action.
    """

    def setUp(self) -> None:
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G01")
        self.project = self.gate.seed_project("fix1")
        self.task = self.gate.new_task(self.project, "evidence binding")
        self.prompt = self.gate.event(self.project, "user.prompt", "go",
                                      task_id=self.task)
        self.rendered = ("JASMINE CONTEXT v1\n{}\nAUTHORITY\nCURRENT STATE\n"
                         "MEMORY — NON AUTHORITATIVE\n")
        self.pack_id = "ctx_" + "C" * 26

    def _pack(self):
        return _pack_dict(pack_id=self.pack_id, project=self.project, task=self.task,
                          prompt=self.prompt, rendered=self.rendered)

    def _verify(self, session, **kwargs):
        _insert_pack_row(self.gate, pack_id=self.pack_id, project=self.project,
                         task=self.task, rendered=self.rendered,
                         truth_digest="d" * 64, command_event=self.prompt,
                         source_event=self.prompt)
        _write_session(self.gate, "t1", {
            "commit": self.gate.git_commit(), "context_pack_id": self.pack_id,
            "pack_sha256": hashlib.sha256(self.rendered.encode("utf-8")).hexdigest(),
            **session})
        return self.gate.verify_session(self._pack(), "t1",
                                        require_tool_actions=False,
                                        require_evidence_backed_actions=True,
                                        **kwargs)

    def test_a_fully_backed_session_verifies(self) -> None:
        evidence_id = self.gate.collect_tool_evidence(
            self.task, command="pytest -q", output="Ran 1 test\n\nOK\n", exit_code=0)
        verified = self._verify({"tool_actions": [
            {"command": "pytest -q", "output": "Ran 1 test\n\nOK\n"}]})
        self.assertEqual(verified["verified"]["tool_actions"], 1)
        # ...and the collection is content-addressed: a repeat reuses the row.
        again = self.gate.collect_tool_evidence(
            self.task, command="pytest -q", output="Ran 1 test\n\nOK\n", exit_code=0)
        self.assertEqual(again, evidence_id)

    def test_a_forged_action_with_no_evidence_row_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._verify({"tool_actions": [
                {"command": "rm -rf /tmp/elsewhere", "output": "done"}]})
        message = str(caught.exception)
        self.assertIn("binds to no Core Evidence", message)
        self.assertIn("rm -rf /tmp/elsewhere", message)

    def test_an_action_whose_output_has_no_row_is_refused(self) -> None:
        # The lookup is keyed on the full content tuple, so an action declaring
        # output no collected row holds simply misses -- it fails as "no Core
        # Evidence", not as a hash comparison against somebody else's row.
        self.gate.collect_tool_evidence(self.task, command="pytest -q",
                                       output="Ran 1 test\n\nOK\n", exit_code=0)
        with self.assertRaises(self.module.Failed) as caught:
            self._verify({"tool_actions": [
                {"command": "pytest -q", "output": "Ran 9 tests\n\nOK\n"}]})
        self.assertIn("binds to no Core Evidence", str(caught.exception))

    def test_a_row_bound_to_a_different_task_is_refused(self) -> None:
        other = self.gate.new_task(self.project, "another task")
        self.gate.collect_tool_evidence(other, command="pytest -q",
                                       output="Ran 1 test\n\nOK\n", exit_code=0)
        with self.assertRaises(self.module.Failed) as caught:
            self._verify({"tool_actions": [
                {"command": "pytest -q", "output": "Ran 1 test\n\nOK\n"}]})
        self.assertIn("binds to no Core Evidence", str(caught.exception))

    def test_a_declared_failure_against_a_pass_row_is_refused(self) -> None:
        self.gate.collect_tool_evidence(self.task, command="pytest -q",
                                       output="Ran 1 test\n\nOK\n", exit_code=0)
        with self.assertRaises(self.module.Failed) as caught:
            self._verify({"tool_actions": [
                {"command": "pytest -q", "output": "Ran 1 test\n\nOK\n",
                 "exit_code": 1}]})
        self.assertIn("declares FAIL but its Evidence", str(caught.exception))

    def test_a_repeat_with_different_bytes_is_a_different_capture(self) -> None:
        # Same command run twice with different outputs -- the normal shape of
        # debugging (FAIL, then OK). The two executions get distinct rows rather
        # than the second being refused or merged into the first; an earlier
        # revision refused here, which made any session that re-ran a command
        # unverifiable.
        first = self.gate.collect_tool_evidence(
            self.task, command="pytest -q", output="Ran 1 test\n\nOK\n", exit_code=0)
        second = self.gate.collect_tool_evidence(
            self.task, command="pytest -q", output="something else entirely",
            exit_code=0)
        self.assertNotEqual(first, second)
        conn = self.gate.conn()
        try:
            rows = conn.execute("SELECT evidence_id FROM evidence WHERE task_id=?",
                                (self.task,)).fetchall()
        finally:
            conn.close()
        self.assertEqual([r["evidence_id"] for r in rows], [first, second])

    def test_an_unbacked_final_verification_is_refused(self) -> None:
        self.gate.collect_tool_evidence(self.task, command="git log --oneline",
                                       output="abc123\n", exit_code=0)
        with self.assertRaises(self.module.Failed) as caught:
            self._verify({
                "tool_actions": [{"command": "git log --oneline", "output": "abc123\n"}],
                "task_completed": True,
                "final_verification": {"command": "pytest -q",
                                       "output": "Ran 1 test\n\nOK\n"}})
        self.assertIn("final verification action is not Evidence-backed",
                      str(caught.exception))


class ExplorationCategoriesNameTheirActions(unittest.TestCase):
    """A category declared `true` with no backing action is a claim about work
    nothing records. Each performed category must name recorded action(s)."""

    def setUp(self) -> None:
        self.module = _module()

    def _session(self, performed, backing="default", commands=("pytest -q",)):
        session = {
            "tool_actions": [{"command": command, "output": "out"}
                             for command in commands],
            "exploration": {name: name in performed
                            for name in self.module.EXPLORATION_CATEGORIES}}
        if backing == "default":
            session["exploration_actions"] = {name: list(commands) for name in performed}
        elif backing is not None:
            session["exploration_actions"] = backing
        return session

    def test_backed_categories_pass(self) -> None:
        backing = self.module._require_exploration_backing(
            self._session({"read_history"}, commands=("pytest -q",)), "the session")
        self.assertEqual(backing, {"read_history": ["pytest -q"]})

    def test_a_category_claimed_with_no_backing_action_is_refused(self) -> None:
        session = self._session({"reproduce_failure"}, backing={})
        with self.assertRaises(self.module.Failed) as caught:
            self.module._require_exploration_backing(session, "the session")
        message = str(caught.exception)
        self.assertIn("reproduce_failure", message)
        self.assertIn("no tool action", message)

    def test_a_missing_mapping_is_refused(self) -> None:
        session = self._session({"read_history"}, backing=None)
        with self.assertRaises(self.module.Failed) as caught:
            self.module._require_exploration_backing(session, "the session")
        self.assertIn("exploration_actions", str(caught.exception))

    def test_an_action_outside_the_receipt_is_refused(self) -> None:
        session = self._session({"read_history"},
                                backing={"read_history": ["an action never recorded"]})
        with self.assertRaises(self.module.Failed) as caught:
            self.module._require_exploration_backing(session, "the session")
        self.assertIn("not one of its recorded tool actions", str(caught.exception))

    def test_a_category_outside_the_vocabulary_is_refused(self) -> None:
        session = self._session({"read_history"},
                                backing={"read_history": ["pytest -q"],
                                         "ask_the_model": ["pytest -q"]})
        with self.assertRaises(self.module.Failed) as caught:
            self.module._require_exploration_backing(session, "the session")
        self.assertIn("outside the closed set", str(caught.exception))


# -- STEP 2: sourced, actionable history -------------------------------------

def _history_block(*, memory_id, command, task, step, evidence_id, sources,
                   project, provenance="the callback task id did not match"):
    history = (f"history: prior FAIL result from `{command}` (task {task}, step "
               f"{step}; memory {memory_id}; evidence {evidence_id}; sources "
               f"{','.join(sources)})")
    return ("HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n"
            + json.dumps({"memory_id": memory_id, "memory_type": "FAILURE_LESSON",
                          "bank": {"scope": "PROJECT",
                                   "bank_id": f"jasmine-project-{project}",
                                   "project_id": project},
                          "source_event_ids": list(sources), "source_task_id": task,
                          "evidence_ids": [evidence_id], "fact": provenance,
                          "text": history + "\n" + provenance},
                         separators=(",", ":")) + "\n")


class ActionableHistoryIsRenderedAndRederived(unittest.TestCase):
    """The pack must carry the failing command, the subject and the source
    pointers -- and the rendered command must equal Core's `tool_input.cmd`."""

    def setUp(self) -> None:
        from jasmine_core import ids

        self.module = _module()
        self.project = ids.new_id("prj")
        self.task = ids.new_id("tsk")
        self.step = "stp_" + "S" * 26
        self.memory_id = ids.new_id("mem")
        self.evidence_id = ids.new_id("evd")
        self.source = ids.new_id("evt")

    def _pack(self, *, block, truth_extra=""):
        rendered = ("JASMINE CONTEXT v1\n{}\nAUTHORITY\nCURRENT STATE\n" + truth_extra
                    + "MEMORY — NON AUTHORITATIVE\n{\"status\":\"ok\"}\n" + block)
        return {"context_pack_id": "ctx_" + "C" * 26, "project_id": self.project,
                "task_id": self.task, "memory_selected": [{
                    "memory_id": self.memory_id, "source_task_id": self.task}],
                "rendered_content": rendered}

    def _block(self, **kwargs):
        return _history_block(memory_id=self.memory_id, command="pytest -q",
                              task=self.task, step=self.step,
                              evidence_id=self.evidence_id, sources=[self.source],
                              project=self.project, **kwargs)

    def test_a_pack_carrying_the_new_fields_verifies(self) -> None:
        self.module._verify_g01_actionable_history(
            self._pack(block=self._block()),
            resolve_command=lambda evidence_id: "pytest -q")

    def test_a_pack_whose_command_disagrees_with_core_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g01_actionable_history(
                self._pack(block=self._block()),
                resolve_command=lambda evidence_id: "pytest -q -k other")
        message = str(caught.exception)
        self.assertIn("disagree", message)
        self.assertIn("pytest -q", message)

    def test_a_pack_with_no_command_is_refused(self) -> None:
        block = ("HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n"
                 + json.dumps({"memory_id": self.memory_id, "text": "history: it failed"}) + "\n")
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g01_actionable_history(self._pack(block=block))
        self.assertIn("no failing command", str(caught.exception))

    def test_a_pack_missing_a_source_pointer_is_refused(self) -> None:
        block = _history_block(memory_id=self.memory_id, command="pytest -q",
                               task=self.task, step=self.step,
                               evidence_id=self.evidence_id, sources=[self.source],
                               project=self.project)
        record = json.loads(block.split("\n", 1)[1])
        record["text"] = record["text"].replace(self.evidence_id, "evd_" + "0" * 26)
        block = ("HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n"
                 + json.dumps(record, separators=(",", ":")) + "\n")
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g01_actionable_history(self._pack(block=block))
        self.assertIn("source pointer", str(caught.exception))

    def test_history_rendered_as_truth_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g01_actionable_history(
                self._pack(block=self._block(), truth_extra="ran pytest -q here\n"))
        self.assertIn("Truth", str(caught.exception))


class TheWorkerRendersWhatCoreRecorded(unittest.TestCase):
    """The worker's history line is read back from Core's own Events, and the
    renderer keeps it inside the labelled non-authoritative block."""

    def setUp(self) -> None:
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-WRK")
        self.project = self.gate.seed_project("worker-history")
        self.task = self.gate.new_task(self.project, "history task")

    def test_the_command_comes_from_the_tool_call_event(self) -> None:
        import importlib.util

        self.gate.collect_tool_evidence(self.task, command="pytest -q -k device",
                                       output="FAILED\n", exit_code=1)
        from jasmine_core import db

        conn = db.connect(self.gate.db_path)
        try:
            row = conn.execute("SELECT task_id, turn_id, tool_use_id, tool_name, source_event_id, command_sha256, artifact_sha256, status"
                               " FROM evidence WHERE task_id=?", (self.task,)).fetchone()
            spec = importlib.util.spec_from_file_location(
                "p4_memory_worker",
                Path(__file__).resolve().parents[1] / "scripts" / "p4-memory-worker.py")
            worker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(worker)
            self.assertEqual(worker._command_for(conn, row), "pytest -q -k device")
        finally:
            conn.close()

    def test_the_item_text_leads_with_actionable_history(self) -> None:
        import importlib.util

        evidence_id = self.gate.collect_tool_evidence(
            self.task, command="pytest -q", output="FAILED\n", exit_code=1)
        spec = importlib.util.spec_from_file_location(
            "p4_memory_worker_b",
            Path(__file__).resolve().parents[1] / "scripts" / "p4-memory-worker.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        from jasmine_core import db

        conn = db.connect(self.gate.db_path)
        try:
            item = SimpleNamespace(
                memory_type="FAILURE_LESSON", memory_id="mem_" + "M" * 26,
                scope_kind="project", bank_id=f"jasmine-project-{self.project}",
                project_id=self.project, text="engine prose about the failure",
                provenance=SimpleNamespace(evidence_ids=(evidence_id,)))
            out = worker._item_for(conn, item)
            self.assertIsNotNone(out)
        finally:
            conn.close()
        self.assertTrue(out["text"].startswith("history:"))
        self.assertIn("`pytest -q`", out["text"])
        self.assertIn(self.task, out["text"])
        self.assertIn(evidence_id, out["text"])
        self.assertLessEqual(len(out["text"]), 2000)

    def test_missing_exact_call_link_cannot_borrow_another_command(self) -> None:
        import importlib.util
        from jasmine_core import db
        evidence_id = self.gate.collect_tool_evidence(self.task, command="python -m unittest", output="AssertionError: 25 != 27.5", exit_code=1)
        spec = importlib.util.spec_from_file_location("strict_history", Path(__file__).resolve().parents[1] / "scripts" / "p4-memory-worker.py")
        worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
        conn = db.connect(self.gate.db_path)
        try:
            row = conn.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
            chain = worker._execution_for(conn, row)
            self.assertEqual(chain[0], "python -m unittest")
            text = worker._history_text("history: observation", chain)
            self.assertIn("AssertionError: 25 != 27.5", text)
            self.assertIn(chain[2], text)
            self.assertIn("current cause not established", text)
            changed = dict(row); changed['source_event_id'] = row['change_event_id']
            self.assertIsNone(worker._execution_for(conn, changed))
            changed = dict(row); changed['command_sha256'] = '0' * 64
            self.assertIsNone(worker._execution_for(conn, changed))
        finally:
            conn.close()

    def test_unhashed_stderr_or_output_cannot_enter_history(self) -> None:
        import importlib.util
        from jasmine_core import db
        evidence_id = self.gate.collect_tool_evidence(self.task,command='python -m unittest',output='actual bytes',exit_code=1)
        spec=importlib.util.spec_from_file_location('output_bound_worker',Path(__file__).resolve().parents[1]/'scripts'/'p4-memory-worker.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        conn=db.connect(self.gate.db_path)
        try:
            row=conn.execute('SELECT * FROM evidence WHERE evidence_id=?',(evidence_id,)).fetchone()
            payload=json.loads(conn.execute('SELECT payload_json FROM events WHERE event_id=?',(row['source_event_id'],)).fetchone()[0])
            for response in ({'stderr':'INVENTED ASSERTION AND CAUSE','exit_code':1},{'output':'INVENTED ASSERTION AND CAUSE','exit_code':1},{'stdout':'wrong bytes','exit_code':1}):
                payload['tool_response']=response
                # Roll back each independent tamper; the production event log is immutable.
                conn.execute('DROP TRIGGER IF EXISTS trg_events_immutable_update')
                conn.execute('UPDATE events SET payload_json=? WHERE event_id=?',(json.dumps(payload),row['source_event_id']))
                self.assertIsNone(worker._execution_for(conn,row))
                conn.rollback()
            payload['tool_response']={'stdout':'actual bytes','stderr':'INVENTED ASSERTION AND CAUSE','output':'INVENTED ASSERTION AND CAUSE','exit_code':1}
            conn.execute('DROP TRIGGER IF EXISTS trg_events_immutable_update')
            conn.execute('UPDATE events SET payload_json=? WHERE event_id=?',(json.dumps(payload),row['source_event_id']))
            execution=worker._execution_for(conn,row)
            text=worker._history_text('history: original',execution)
            self.assertIn('actual bytes',text)
            self.assertNotIn('INVENTED',text)
            changed=dict(row);changed['artifact_sha256']=None
            self.assertIsNone(worker._execution_for(conn,changed))
            conn.rollback()
        finally:
            conn.close()

    def test_repeated_command_preserves_each_output_artifact(self) -> None:
        from jasmine_core import db
        first = self.gate.collect_tool_evidence(self.task, command="python -m unittest", output="AssertionError\n", exit_code=1)
        second = self.gate.collect_tool_evidence(self.task, command="python -m unittest", output="OK\n", exit_code=0)
        conn = db.connect(self.gate.db_path)
        try:
            rows = conn.execute("SELECT artifact_uri FROM evidence WHERE evidence_id IN (?,?)", (first, second)).fetchall()
            self.assertEqual(len({r['artifact_uri'] for r in rows}), 2)
            contents = {(self.gate.workspace / r['artifact_uri'].removeprefix('workspace:/')).read_text() for r in rows}
            self.assertEqual(contents, {"AssertionError\n", "OK\n"})
        finally:
            conn.close()

    def test_declared_echo_actions_do_not_establish_repair_or_validation(self) -> None:
        import importlib.util
        from jasmine_core import db
        actions=[]
        for role,command,output,exit_code in [('failure','python -B -m unittest -v','AssertionError: actual failed',1),('inspection','echo inspected','inspected',0),('repair','echo nothing changed','nothing changed',0),('validation','echo OK','OK',0)]:
            eid=self.gate.collect_tool_evidence(self.task,command=command,output=output,exit_code=exit_code)
            actions.append({'role':role,'evidence_id':eid})
        self.gate.event(self.project,'project.notes',json.dumps({'schema':'jasmine.historical-execution.v1','actions':actions}),task_id=self.task)
        spec=importlib.util.spec_from_file_location('false_role_worker',Path(__file__).resolve().parents[1]/'scripts'/'p4-memory-worker.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        conn=db.connect(self.gate.db_path)
        try:
            item=SimpleNamespace(memory_type='FAILURE_LESSON',memory_id='mem_'+'M'*26,scope_kind='project',bank_id=f'jasmine-project-{self.project}',project_id=self.project,text='engine prose',provenance=SimpleNamespace(evidence_ids=(actions[0]['evidence_id'],)))
            result=worker._item_for(conn,item)
            self.assertEqual(result['evidence_ids'],[actions[0]['evidence_id']])
            self.assertIn('not established by this execution',result['text'])
            self.assertNotIn('attempted repair then PASS',result['text'])
        finally:
            conn.close()

    def test_real_historical_execution_keeps_assertion_edit_and_validation_sources(self) -> None:
        import subprocess, sys, importlib.util
        from jasmine_core import db
        directory = self.gate.root / 'fixture'; directory.mkdir()
        (directory/'NOTES.md').write_text('discount is a percentage of subtotal\n')
        (directory/'calc.py').write_text('def total(prices, discount):\n    return sum(prices) - discount\n')
        (directory/'test_calc.py').write_text('import unittest\nfrom calc import total\nclass Test(unittest.TestCase):\n def test_total(self): self.assertAlmostEqual(total([10.,20.],5.),27.5)\n')
        subprocess.run(['git','init','-q',str(directory)],check=True)
        subprocess.run(['git','-C',str(directory),'add','.'],check=True)
        subprocess.run(['git','-C',str(directory),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','baseline'],check=True)
        self.gate.config['g01_historical_fixture'] = {'repository':str(directory),'ref':'HEAD','python':sys.executable}
        # No engine is supplied: actual execution/capture happens, retention is
        # honestly unavailable. This test does not masquerade as a real Gate.
        with self.assertRaises(self.module.Failed):
            self.gate.retain_g01_history(self.project,self.task)
        conn = db.connect(self.gate.db_path)
        try:
            rows = conn.execute('SELECT * FROM evidence WHERE task_id=? ORDER BY created_at,evidence_id',(self.task,)).fetchall()
            self.assertEqual(len(rows),4)
            spec=importlib.util.spec_from_file_location('chain_worker',Path(__file__).resolve().parents[1]/'scripts'/'p4-memory-worker.py')
            worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
            failure=next(r for r in rows if r['status']=='FAIL')
            actual_memory=conn.execute("SELECT memory_id FROM memory_items WHERE source_event_id=? AND memory_type='FAILURE_LESSON'",(failure['change_event_id'],)).fetchone()
            self.assertIsNotNone(actual_memory)
            item=SimpleNamespace(memory_type='FAILURE_LESSON',memory_id=actual_memory['memory_id'],scope_kind='project',bank_id=f'jasmine-project-{self.project}',project_id=self.project,text='invented engine cause',provenance=SimpleNamespace(evidence_ids=(failure['evidence_id'],)))
            result=worker._item_for(conn,item)
            from jasmine_core.memory.history import verified_records
            ordered=[conn.execute('SELECT * FROM evidence WHERE evidence_id=?',(eid,)).fetchone() for eid in result['evidence_ids']]
            executions=[worker._execution_for(conn,row) for row in ordered]
            self.assertIsNotNone(verified_records(executions,[row['status'] for row in ordered]))
            import copy
            for mutation in ('wrong_validation','wrong_exit','empty_diff','wrong_hash','unchanged_file'):
                changed=copy.deepcopy(executions)
                if mutation=='wrong_validation': changed[3]=('echo OK',changed[3][1],changed[3][2])
                elif mutation=='wrong_exit': changed[3][1]['exit_code']=1
                else:
                    records=[json.loads(line) for line in changed[2][1]['stdout'].splitlines()]
                    if mutation=='empty_diff': records[0]['diff']=''
                    elif mutation=='wrong_hash': records[0]['after_sha256']='0'*64
                    else: records[0]['after']=records[0]['before']
                    changed[2][1]['stdout']='\n'.join(json.dumps(r) for r in records)
                self.assertIsNone(verified_records(changed,[row['status'] for row in ordered]),mutation)
            self.assertEqual(len(result['evidence_ids']),4)
            self.assertEqual(len(result['source_event_ids']),8)
            self.assertNotIn('invented engine cause',result['text'])
            for source in result['source_event_ids']+result['evidence_ids']:
                self.assertIn(source,result['text'])
            self.assertIn('AssertionError',result['text'])
            self.assertIn('repair',result['text'])
            self.assertIn('validation',result['text'])
            self.assertIn('discount / 100',result['text'])
            self.assertIn('28.5',result['text'])
            self.assertIn('OK',result['text'])
            self.assertLessEqual(len(result['text']),2000)
            from jasmine_core.context.memory_provider import filter_memory
            from jasmine_core.context import renderer
            projection={'project_id':self.project,'task_id':self.task,'task':{'revision':1},'active_rules':[],'steps':[],'evidence_refs':[],'evidence':[]}
            admitted=filter_memory(conn,{'items':[result],'banks':[{**result['bank'],'status':'ok'}],'status':'ok'},projection,schema_version=self.module.SCHEMA_VERSION)
            self.assertEqual(admitted['filtered'],[])
            from jasmine_core.context.tokens import Tokenizer, TokenizerUnavailable
            try:
                tokenizer=Tokenizer()
            except TokenizerUnavailable:
                tokenizer=SimpleNamespace(count=lambda text:len(text)//3,identity={'qualification':'test conservative approximation'})
            rendered=renderer.render(projection,{'reconciliation_required':False},None,admitted,tokenizer,source_event_id=result['source_event_ids'][0])
            self.assertEqual(rendered['omissions'],[])
            for source in result['source_event_ids']+result['evidence_ids']:
                self.assertIn(source,rendered['rendered_content'])
            # The first identity is an actual stored Core item. Stress every
            # additional Core-generated random identity, never pick a short one.
            # All referenced Tasks, Events and Evidence remain the actual capture.
            from jasmine_core import ids
            for identity in [actual_memory['memory_id']] + [ids.new_id('mem') for _ in range(16)]:
                item.memory_id=identity
                mapped=worker._item_for(conn,item)
                one=filter_memory(conn,{'items':[mapped],'banks':[{**mapped['bank'],'status':'ok'}],'status':'ok'},projection,schema_version=self.module.SCHEMA_VERSION)
                self.assertEqual(len(mapped['source_event_ids']),8,identity)
                self.assertEqual(len(mapped['evidence_ids']),4,identity)
                self.assertEqual([entry['memory_id'] for entry in one['selected']],[identity])
                budgeted=renderer.render(projection,{'reconciliation_required':False},None,one,tokenizer,source_event_id=mapped['source_event_ids'][0])
                self.assertEqual(budgeted['omissions'],[],identity)
                marker='HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n'
                memory_block=marker+budgeted['rendered_content'].split(marker,1)[1]
                if tokenizer.identity.get('name') == 'tiktoken':
                    self.assertLessEqual(tokenizer.count(memory_block),800,identity)
                for pointer in mapped['source_event_ids']+mapped['evidence_ids']:
                    self.assertIn(pointer,memory_block)
        finally:
            conn.close()

    def test_the_renderer_keeps_actionable_text_in_the_labelled_block(self) -> None:
        from jasmine_core.context import renderer

        bank = {"scope": "PROJECT", "bank_id": f"jasmine-project-{self.project}",
                "project_id": self.project}
        item = {"memory_id": "mem_" + "M" * 26, "memory_type": "FAILURE_LESSON",
                "bank": bank, "source_event_ids": ["evt_" + "E" * 26],
                "source_task_id": self.task,
                "fact": {"fact_type": "EVIDENCE_RESULT",
                         "subject_event_id": "evt_" + "E" * 26,
                         "evidence_id": "evd_" + "V" * 26, "key": "status",
                         "value": "FAIL"},
                "text": "history: prior FAIL result from `pytest -q` (task x)",
                "confidence": 1.0, "recorded_at": "2026-01-01T00:00:00.000000Z"}
        tokenizer = SimpleNamespace(count=lambda text: len(text) // 4,
                                    identity={"qualification": "test"})
        rendered = renderer.render(
            {"project_id": self.project, "task_id": self.task,
             "task": {"revision": 1}, "active_rules": [], "steps": [],
             "evidence_refs": [], "evidence": []},
            {"reconciliation_required": False}, None,
            {"status": "ok", "selected": [item]}, tokenizer,
            source_event_id="evt_" + "E" * 26)["rendered_content"]
        marker = "HISTORICAL_NONAUTHORITATIVE MEMORY DATA"
        self.assertIn(marker, rendered)
        truth, _, memory = rendered.partition(marker)
        self.assertIn("pytest -q", memory)
        self.assertNotIn("pytest -q", truth)


# -- STEP 3a: obsolete exclusion by identity ---------------------------------

class ObsoleteMeansNotInjectedById(unittest.TestCase):
    """The retain-reason prose never appears in a rendered pack, so searching
    for it could not fail. Exclusion is by id: the retired memory must be
    absent from `memory_selected`, from every `source_event_ids` list, and from
    `qualified_provenance`."""

    def setUp(self) -> None:
        from jasmine_core import SCHEMA_VERSION, clock, db
        from jasmine_core.events import EventStore
        from jasmine_core.memory import scope
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.memory.types import Provenance
        from jasmine_core.models import NewEvent

        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G07")
        self.project = self.gate.seed_project("g07-exclusion")
        self.live_task = self.gate.new_task(self.project, "live lesson")
        self.live_event = self.gate.event(self.project, "user.prompt", "failures?",
                                          task_id=self.live_task)
        self.live_id = "mem_" + "L" * 26
        self.live_evidence = "evd_" + "1" * 26
        # The retired premise, as a real Core row in a retired state.
        retired_task = self.gate.new_task(self.project, "retired lesson")
        retired_event = self.gate.event(self.project, "user.prompt", "old failure",
                                        task_id=retired_task)
        conn = self.gate.conn()
        try:
            with db.transaction(conn):
                store = MemoryStore(conn, schema_version=SCHEMA_VERSION)
                self.retired_id = store.insert_item(
                    bank=scope.project_bank(self.project), memory_type="FAILURE_LESSON",
                    document_id="doc-retired-1", source_event_id=retired_event,
                    provenance=Provenance(source_event_ids=(retired_event,),
                                          evidence_ids=("evd_" + "2" * 26,)),
                    sanitized_text="an old lesson", content_sha256="c" * 64,
                    task_id=retired_task, at=clock.now_rfc3339())
                conn.execute("UPDATE memory_items SET status='OBSOLETE' WHERE memory_id=?",
                             (self.retired_id,))
                EventStore(conn, schema_version=SCHEMA_VERSION).append(NewEvent(
                    event_type="memory.obsoleted", source_system="test",
                    source_event_id=None, occurred_at=clock.now(),
                    actor_id=self.gate.human, actor_kind="human", host_id=self.gate.host,
                    project_id=self.project, task_id=self.live_task,
                    payload={"text": ""}))
            self.obsolete_text = "a lesson the operator is about to retire"
            self.gate.receipt("g07-retired", {"memory_id": self.retired_id,
                                              "text": self.obsolete_text,
                                              "project_id": self.project})
        finally:
            conn.close()
        self.rendered_marker = "HISTORICAL_NONAUTHORITATIVE MEMORY DATA"

    def _run(self, *, selected=None, provenance=None, selected_sources=None,
             provenance_sources=None, answer=None):
        selected = ([{"memory_id": self.live_id,
                      "source_event_ids": ([self.live_event] if selected_sources is None
                                           else selected_sources)}]
                    if selected is None else selected)
        provenance = ([{"memory_id": self.live_id, "evidence_ids": [self.live_evidence],
                        "source_event_ids": ([self.live_event]
                                             if provenance_sources is None
                                             else provenance_sources)}]
                      if provenance is None else provenance)
        rendered = ("JASMINE CONTEXT v1\n{}\nAUTHORITY\nCURRENT STATE\n"
                    "MEMORY — NON AUTHORITATIVE\n{\"status\":\"ok\"}\n"
                    f"{self.rendered_marker}\n"
                    + json.dumps({"memory_id": self.live_id,
                                  "source_event_ids": [self.live_event]},
                                 separators=(",", ":")) + "\n")
        pack_id = "ctx_" + "7" * 26
        pack = {"context_pack_id": pack_id, "project_id": self.project,
                "task_id": self.live_task, "prompt_event_id": self.live_event,
                "memory_status": "ok", "memory_selected": selected,
                "memory_filtered": [], "qualified_provenance": provenance,
                "truth_digest": "d" * 64, "check_current": True,
                "rendered_content": rendered, "sections": [], "token_count": 1,
                "receipt": {"path": None, "sha256": None}}
        _insert_pack_row(self.gate, pack_id=pack_id, project=self.project,
                         task=self.live_task, rendered=rendered, truth_digest="d" * 64,
                         command_event=self.live_event, source_event=self.live_event)
        final = (answer if answer is not None
                 else "I remember the live lesson from history; it is non-authoritative.")
        _write_session(self.gate, "g07", {
            "commit": self.gate.git_commit(), "context_pack_id": pack_id,
            "pack_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "memory_seen": [{"memory_id": self.live_id,
                             "evidence_ids": [self.live_evidence],
                             "source_event_ids": [self.live_event]}],
            "tool_actions": [], "final_answer": final})
        record = self.module.CaseRecord("P4-G07", "PASS")
        self.module._verify_g07_pack(self.gate, record, pack)
        return record

    def test_a_clean_pack_passes(self) -> None:
        self.assertEqual(self._run().verdict, "PASS")

    def test_a_retired_id_in_selected_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run(selected=[{"memory_id": self.retired_id,
                                 "source_event_ids": [self.live_event]},
                                {"memory_id": self.live_id,
                                 "source_event_ids": [self.live_event]}])
        self.assertIn(self.retired_id, str(caught.exception))
        self.assertIn("still selected", str(caught.exception))

    def test_a_retired_id_in_a_source_list_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run(selected_sources=[self.live_event, self.retired_id])
        message = str(caught.exception)
        self.assertIn(self.retired_id, message)
        self.assertIn("source", message)

    def test_a_retired_id_in_provenance_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run(provenance=[{"memory_id": self.live_id,
                                   "evidence_ids": [self.live_evidence],
                                   "source_event_ids": [self.live_event]},
                                  {"memory_id": self.retired_id,
                                   "evidence_ids": ["evd_" + "9" * 26],
                                   "source_event_ids": [self.live_event]}])
        message = str(caught.exception)
        self.assertIn(self.retired_id, message)
        self.assertIn("provenance", message)


# -- STEP 3b: non-empty, distinguishable recall legs --------------------------

class IsolationLegsAreNonEmptyAndScoped(unittest.TestCase):
    """The two recall legs used to pass on an empty result and never looked at
    `memory_filtered` or the queried banks. Each leg must return the right
    project's content, over exactly its own banks, with every selected and
    filtered item banked to its project."""

    def setUp(self) -> None:
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G04")
        self.project_a = self.gate.seed_project("g04-leg-a")
        self.project_b = self.gate.seed_project("g04-leg-b")
        self.bank_a = f"jasmine-project-{self.project_a}"
        self.bank_b = f"jasmine-project-{self.project_b}"

    def _make_outcome(self, *, items, banks="default", filtered=()):
        if banks == "default":
            banks = [self.bank_b, "jasmine-global"]
        return SimpleNamespace(
            items=[SimpleNamespace(text=text, bank_id=bank, memory_id=f"mem_{i:026d}",
                                    memory_result_id=f"mres_{i:026d}")
                   for i, (text, bank) in enumerate(items)],
            filtered=[SimpleNamespace(bank_id=bank, memory_id=memory_id, reason=reason)
                      for bank, memory_id, reason in filtered],
            banks=[SimpleNamespace(bank_id=bank) for bank in banks])

    def _leg(self, outcome):
        self.module._verify_g04_recall_leg(
            self.gate, outcome, project_id=self.project_b,
            expected_phrase="stay API compatible", forbidden_phrase="change the API shape",
            label="the B leg")

    def test_a_leg_with_the_right_content_passes(self) -> None:
        self._leg(self._make_outcome(items=[("Project B must stay API compatible",
                                        self.bank_b)]))

    def test_an_empty_leg_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._leg(self._make_outcome(items=[]))
        self.assertIn("no items", str(caught.exception))

    def test_a_leg_with_the_wrong_projects_content_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._leg(self._make_outcome(
                items=[("Project A may change the API shape freely", self.bank_b)]))
        message = str(caught.exception)
        self.assertIn("other project's content", message)

    def test_a_leg_missing_its_own_content_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._leg(self._make_outcome(items=[("something unrelated", self.bank_b)]))
        self.assertIn("did not return this project's own content", str(caught.exception))

    def test_a_foreign_item_in_filtered_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._leg(self._make_outcome(
                items=[("Project B must stay API compatible", self.bank_b)],
                filtered=[(self.bank_a, "mem_" + "F" * 26, "foreign_project")]))
        message = str(caught.exception)
        self.assertIn("filtered", message)
        self.assertIn(self.bank_a, message)

    def test_a_bank_set_reaching_into_a_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._leg(self._make_outcome(
                items=[("Project B must stay API compatible", self.bank_b)],
                banks=[self.bank_b, "jasmine-global", self.bank_a]))
        self.assertIn("queried banks", str(caught.exception))

    def test_a_pack_filtering_a_foreign_bank_row_is_refused(self) -> None:
        answer = "you must stay API compatible"
        pack = {"context_pack_id": "ctx_" + "C" * 26, "project_id": self.project_b,
                "task_id": "tsk_" + "D" * 26, "prompt_event_id": "evt_" + "E" * 26,
                "memory_status": "ok", "memory_selected": [],
                "memory_filtered": [{"bank_id": self.bank_a,
                                     "memory_id": "mem_" + "F" * 26,
                                     "reason": "foreign_project"}],
                "qualified_provenance": [],
                "rendered_content": (
                    "JASMINE CONTEXT v1\n"
                    + json.dumps({"project_id": self.project_b,
                                  "task_id": "tsk_" + "D" * 26}) + "\n"
                    + "AUTHORITY\n"
                    + json.dumps({"rule_id": "rul_" + "B" * 26, "version": 1,
                                  "scope": {"kind": "project",
                                            "project_id": self.project_b},
                                  "content": "Project B must stay API compatible"})
                    + "\nProject B must stay API compatible\n"
                    + "CURRENT STATE\n{}\nMEMORY — NON AUTHORITATIVE\n")}

        class _Gate:
            def __init__(self, gate, module, root):
                self._gate = gate
                self.module = module
                self.root = root

            def receipt(self, kind, payload):
                return self._gate.receipt(kind, payload)

            def read_receipt(self, kind):
                return self._gate.read_receipt(kind)

            def verify_session(self, pack, label, *, require_tool_actions=True,
                               require_evidence_backed_actions=False):
                return {"final_answer": answer, "tool_actions": [],
                        "semantic_review": _review_for(answer),
                        "verified": {"tool_actions": 0, "memory_carried": [],
                                     "session_transcript": {},
                                     "injection": {}, "sealed": []}}

            def seal_session_artifacts(self, record, session):
                pass

            def semantic_review(self, session, label):
                return self._gate.semantic_review(session, label)

        root = self.gate.root
        inner = _Gate(self.gate, self.module, root)
        inner.receipt("g04-recall", {"project_a": self.project_a,
                                    "project_b": self.project_b})
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g04_pack(inner, self.module.CaseRecord("P4-G04", "PASS"),
                                         pack)
        self.assertIn("another bank", str(caught.exception))


# -- STEP 3c: a real HARD/State/Checkpoint/fault/recovery flow ----------------

class OutageHoldsAndRecoveryFinishesTheSameJob(unittest.TestCase):
    """G05 created no HARD Rule, never touched a Checkpoint, never had a
    PENDING job during the outage, and never asserted same-job recovery. Each
    half below is refused when its premise is missing."""

    def setUp(self) -> None:
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G05")
        self.project = self.gate.seed_project("g05-flow")

    def _pending_lesson(self, task_title="outage lesson"):
        from jasmine_core import SCHEMA_VERSION, ids
        from jasmine_core.evidence import EvidenceStore
        from jasmine_core.memory import eligibility
        from jasmine_core.memory import jobs as jobs_module
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.state import StateStore

        task = self.gate.new_task(self.project, task_title)
        conn = self.gate.conn()
        try:
            state = StateStore(conn, schema_version=SCHEMA_VERSION)
            step = state.create_step(task, {"title": "device test",
                                            "host_id": self.gate.host,
                                            "expected_revision": 1},
                                     actor_id=self.gate.human, actor_kind="human",
                                     can_accept=False)["step"]["step_id"]
            state.transition_step(step, {"status": "IN_PROGRESS", "expected_revision": 1,
                                         "host_id": self.gate.host},
                                  actor_id=self.gate.human, actor_kind="human",
                                  can_accept=False)
            state.transition_step(step, {"status": "FAILED", "reason": "device failed",
                                         "expected_revision": 2, "host_id": self.gate.host},
                                  actor_id=self.gate.human, actor_kind="human",
                                  can_accept=False)
            EvidenceStore(conn, schema_version=SCHEMA_VERSION).record_tool_result(
                {"task_id": task, "step_id": step, "host_id": self.gate.host,
                 "codex_session_id": self.gate.case_id,
                 "turn_id": f"t-{ids.new_id('evt')[-8:]}",
                 "tool_use_id": ids.new_id("evt")[-8:], "tool_name": "pytest",
                 "tool_input": {"cmd": "pytest -q"},
                 "tool_response": {"exit_code": 1},
                 "artifact_uri": "workspace:/source.txt"},
                actor_id=self.gate.system)
            report = jobs_module.JobStore(
                conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)
            ).enqueue_from_sources(eligibility.SourceRegistry())
            assert report["created"], report["ineligible"]
            rows = conn.execute(
                "SELECT * FROM memory_jobs WHERE task_id=? AND kind='retain'"
                " ORDER BY job_id", (task,)).fetchall()
            return task, [jobs_module.job_row(row) for row in rows]
        finally:
            conn.close()

    def test_a_hard_rule_is_required(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g05_hard_rule(
                self.gate, self.module.CaseRecord("P4-G05", "PASS"), self.project)
        self.assertIn("no ACTIVE HARD Rule", str(caught.exception))

    def test_a_confirmed_hard_rule_passes(self) -> None:
        self.gate.confirm_decision(self.project, content="pins the deploy checklist",
                                   rule_key="g05.test-checklist")
        record = self.module.CaseRecord("P4-G05", "PASS")
        rule_id = self.module._verify_g05_hard_rule(self.gate, record, self.project)
        self.assertTrue(rule_id.startswith("rul_"))
        self.assertEqual(record.receipts[-1]["kind"], "g05-hard-rule")

    def test_a_missing_checkpoint_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g05_checkpoint(
                self.gate, self.module.CaseRecord("P4-G05", "PASS"),
                "ckp_" + "0" * 26)
        self.assertIn("does not read back", str(caught.exception))

    def test_a_created_checkpoint_reads_back(self) -> None:
        from jasmine_core import SCHEMA_VERSION
        from jasmine_core.continuity import ContinuityStore
        from jasmine_core.state import StateStore

        task = self.gate.new_task(self.project, "checkpoint task")
        prompt = self.gate.event(self.project, "user.prompt", "checkpoint please",
                                 task_id=task)
        conn = self.gate.conn()
        try:
            StateStore(conn, schema_version=SCHEMA_VERSION).create_step(
                task, {"title": "s", "host_id": self.gate.host, "expected_revision": 1},
                actor_id=self.gate.human, actor_kind="human", can_accept=False)
            checkpoint = ContinuityStore(
                conn, schema_version=SCHEMA_VERSION,
                scanner=lambda budget, *, conn: {
                    "snapshot": {"selected_hashes": {}, "complete": True},
                    "sample_window": {"started_at_unix": 1, "finished_at_unix": 2}}).create(
                {"host_id": self.gate.host, "source_event_id": prompt,
                 "idempotency_key": "fix-round-checkpoint", "task_id": task,
                 "reason": "MANUAL"}, actor_id=self.gate.human)["checkpoint"]
        finally:
            conn.close()
        record = self.module.CaseRecord("P4-G05", "PASS")
        read = self.module._verify_g05_checkpoint(self.gate, record,
                                                  checkpoint["checkpoint_id"])
        self.assertEqual(read["checkpoint_id"], checkpoint["checkpoint_id"])

    def test_a_pending_job_holds(self) -> None:
        _, pending = self._pending_lesson()
        self.assertTrue(pending)
        record = self.module.CaseRecord("P4-G05", "PASS")
        self.module._verify_g05_outage_hold(
            self.gate, record, expected_job_ids=[job["job_id"] for job in pending])
        self.assertEqual(record.engine_evidence["outage_hold"]["pending"],
                         sorted(job["job_id"] for job in pending))

    def test_a_job_that_drained_during_the_outage_is_refused(self) -> None:
        from jasmine_core import db

        _, pending = self._pending_lesson()
        job_id = pending[0]["job_id"]
        conn = self.gate.conn()
        try:
            with db.transaction(conn):
                conn.execute("UPDATE memory_jobs SET status='DONE', completed_at='2026-01-02T00:00:00.000000Z' WHERE job_id=?",
                             (job_id,))
        finally:
            conn.close()
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g05_outage_hold(
                self.gate, self.module.CaseRecord("P4-G05", "PASS"),
                expected_job_ids=[job_id])
        message = str(caught.exception)
        self.assertIn(job_id, message)
        self.assertIn("drained during the outage", message)

    def test_recovery_drains_the_same_job(self) -> None:
        from jasmine_core import db

        _, pending = self._pending_lesson()
        expected = [job["job_id"] for job in pending]
        memories = sorted({job["memory_id"] for job in pending})
        conn = self.gate.conn()
        try:
            with db.transaction(conn):
                for job_id in expected:
                    conn.execute("UPDATE memory_jobs SET status='DONE', completed_at='2026-01-02T00:00:00.000000Z' WHERE job_id=?",
                                 (job_id,))
        finally:
            conn.close()
        record = self.module.CaseRecord("P4-G05", "PASS")
        self.module._verify_g05_recovery(self.gate, record, expected_job_ids=expected,
                                         memory_ids=memories)
        self.assertEqual(record.receipts[-1]["kind"], "g05-recovery")

    def test_recovery_of_a_different_job_id_is_refused(self) -> None:
        from jasmine_core import db

        _, pending = self._pending_lesson()
        memories = sorted({job["memory_id"] for job in pending})
        conn = self.gate.conn()
        try:
            with db.transaction(conn):
                for job in pending:
                    conn.execute("UPDATE memory_jobs SET status='DONE', completed_at='2026-01-02T00:00:00.000000Z' WHERE job_id=?",
                                 (job["job_id"],))
        finally:
            conn.close()
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g05_recovery(
                self.gate, self.module.CaseRecord("P4-G05", "PASS"),
                expected_job_ids=["job_" + "0" * 26], memory_ids=memories)
        message = str(caught.exception)
        self.assertIn("vanished", message)

    def test_a_second_document_for_the_same_memory_is_refused(self) -> None:
        from jasmine_core import db, ids

        task, pending = self._pending_lesson()
        expected = [job["job_id"] for job in pending]
        memories = sorted({job["memory_id"] for job in pending})
        first = pending[0]
        extra_source = self.gate.event(self.project, "user.prompt", "a second source",
                                       task_id=task)
        conn = self.gate.conn()
        try:
            with db.transaction(conn):
                for job in pending:
                    conn.execute("UPDATE memory_jobs SET status='DONE', completed_at='2026-01-02T00:00:00.000000Z' WHERE job_id=?",
                                 (job["job_id"],))
                # A second retain job for the same memory behind another
                # document: the shape a recovery that wrote twice would leave.
                extra_id = ids.new_id("mjb")
                conn.execute(
                    "INSERT INTO memory_jobs (job_id, kind, memory_id, source_event_id,"
                    " project_id, task_id, memory_type, bank_id, document_id,"
                    " content_sha256, status, retry_count, max_attempts, next_attempt_at,"
                    " created_at, updated_at, completed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (extra_id, "retain", first["memory_id"], extra_source,
                     self.project, task, first["memory_type"], first["bank_id"],
                     "doc-second-1", "d" * 64, "DONE", 0, 5,
                     "2026-01-02T00:00:00.000000Z", "2026-01-02T00:00:00.000000Z",
                     "2026-01-02T00:00:00.000000Z", "2026-01-02T00:00:00.000000Z"))
        finally:
            conn.close()
        # Tracked alongside, so the job-identity half holds and the document
        # half is what fails: one memory behind two documents.
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g05_recovery(
                self.gate, self.module.CaseRecord("P4-G05", "PASS"),
                expected_job_ids=expected + [extra_id], memory_ids=memories)
        self.assertIn("second document", str(caught.exception))


# -- STEP 3d: semantic review bound to the transcript --------------------------

class SemanticReviewBindsTheAnswer(unittest.TestCase):
    """Word lists are defeatable by ordinary English, so G02, G04 and G05
    answers carry the same bound review G03 has: required block, reviewer
    name, verdict, and a digest of the exact `final_answer`."""

    def setUp(self) -> None:
        self.module = _module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-fix-review-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _review_case(self, case, pack_extra, answer, review):
        from jasmine_core import ids

        project = ids.new_id("prj")
        gate_cls = self.module.Gate.__new__(self.module.Gate)
        gate_cls.root = self.root
        pack = {"context_pack_id": "ctx_" + "C" * 26, "project_id": project,
                "task_id": "tsk_" + "D" * 26, "prompt_event_id": "evt_" + "E" * 26,
                "memory_status": "ok", "memory_selected": [], "memory_filtered": [],
                "qualified_provenance": [],
                "rendered_content": ("JASMINE CONTEXT v1\n"
                                     + json.dumps({"project_id": project}) + "\n"
                                     + "AUTHORITY\n"
                                     + json.dumps({"rule_id": "rul_" + "B" * 26,
                                                   "version": 2, "kind": "DECISION",
                                                   "scope": {"kind": "project",
                                                             "project_id": project}})
                                     + "\nV1 will use Hindsight for long-term memory\n"
                                     + "CURRENT STATE\nMEMORY — NON AUTHORITATIVE\n"),
                **pack_extra}
        session = {"final_answer": answer, "tool_actions": [],
                   "verified": {"tool_actions": 0, "memory_carried": [],
                                "session_transcript": {}, "injection": {},
                                "sealed": []}}
        if review != "absent":
            session["semantic_review"] = review
        return gate_cls, pack, session

    def test_g02_missing_block_digest_verdict_reviewer(self) -> None:
        answer = "V1 uses Hindsight for long-term memory"
        for name, review, needle in (
            ("missing", "absent", "no semantic_review block"),
            ("digest", _review_for(answer, digest="a" * 64),
             "attached to a different answer"),
            ("verdict", _review_for(answer, verdict="inconsistent"),
             "returned 'inconsistent'"),
            ("reviewer", _review_for(answer, reviewer="  "),
             "names no reviewer"),
        ):
            with self.subTest(name=name):
                gate, pack, session = self._review_case("g02", {}, answer, review)
                with self.assertRaises(self.module.Failed) as caught:
                    gate.semantic_review(session, "the G02 session")
                self.assertIn(needle, str(caught.exception))

    def test_g04_missing_block_digest_verdict_reviewer(self) -> None:
        answer = "you must stay API compatible"
        for name, review, needle in (
            ("missing", "absent", "no semantic_review block"),
            ("digest", _review_for(answer, digest="b" * 64),
             "attached to a different answer"),
            ("verdict", _review_for(answer, verdict="inconsistent"),
             "returned 'inconsistent'"),
            ("reviewer", _review_for(answer, reviewer=""),
             "names no reviewer"),
        ):
            with self.subTest(name=name):
                gate, pack, session = self._review_case("g04", {}, answer, review)
                with self.assertRaises(self.module.Failed) as caught:
                    gate.semantic_review(session, "the G04 session")
                self.assertIn(needle, str(caught.exception))
        gate, pack, session = self._review_case("g04", {}, answer,
                                               _review_for(answer))
        reviewed = gate.semantic_review(session, "the G04 session")
        self.assertEqual(reviewed["verdict"], "consistent")

    def test_g05_missing_block_digest_verdict_reviewer(self) -> None:
        answer = "memory is degraded and unreachable"
        for name, review, needle in (
            ("missing", "absent", "no semantic_review block"),
            ("digest", _review_for(answer, digest="c" * 64),
             "attached to a different answer"),
            ("verdict", _review_for(answer, verdict="inconsistent"),
             "returned 'inconsistent'"),
            ("reviewer", _review_for(answer, reviewer="  "),
             "names no reviewer"),
        ):
            with self.subTest(name=name):
                gate, pack, session = self._review_case("g05", {}, answer, review)
                with self.assertRaises(self.module.Failed) as caught:
                    gate.semantic_review(session, "the G05 session")
                self.assertIn(needle, str(caught.exception))


# -- STEP 3d continued: the degraded pack contract -------------------------------

class G05DegradedPackHoldsSessionAndReview(unittest.TestCase):
    """The degraded pack leg keeps its Truth checks and now binds the session's
    actions to Evidence and its answer to an independent review."""

    def setUp(self) -> None:
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G05")
        self.project = self.gate.seed_project("g05-pack")
        self.task = self.gate.new_task(self.project, "engine unreachable")
        self.answer = ("Memory is degraded and unreachable, so I worked from "
                       "current Truth alone.")
        compact = dict(separators=(",", ":"), ensure_ascii=False)
        self.rendered = (
            "JASMINE CONTEXT v1\n"
            + json.dumps({"project_id": self.project, "task_id": self.task},
                         **compact) + "\n"
            + "AUTHORITY\nCURRENT STATE\n"
            + json.dumps({"task": {"task_id": self.task}}, **compact) + "\n"
            + "MEMORY — NON AUTHORITATIVE\n"
            + json.dumps({"status": "degraded"}, **compact) + "\n")
        self.pack = {"context_pack_id": "ctx_" + "5" * 26, "project_id": self.project,
                     "task_id": self.task, "prompt_event_id": "evt_" + "E" * 26,
                     "memory_status": "degraded", "memory_selected": [],
                     "memory_filtered": [], "qualified_provenance": [],
                     "truth_digest": "d" * 64, "check_current": True,
                     "rendered_content": self.rendered, "sections": [],
                     "token_count": 12, "engine_url": "http://127.0.0.1:1",
                     "receipt": {"path": None, "sha256": None}}

    def _run(self, review="default"):
        session = {
            "final_answer": self.answer,
            "tool_actions": [{"command": "git status", "output": "clean\n"}],
            "verified": {"tool_actions": 1, "memory_carried": [],
                         "session_transcript": {}, "injection": {}, "sealed": []}}
        session["semantic_review"] = (_review_for(self.answer) if review == "default"
                                      else review)
        if review is None:
            del session["semantic_review"]

        real = self.gate

        class _Gate:
            case_id = real.case_id

            def __init__(self, module):
                self.module = module

            def receipt(self, kind, payload):
                return real.receipt(kind, payload)

            def read_receipt(self, kind):
                return real.read_receipt(kind)

            def conn(self):
                return real.conn()

            @property
            def workspace(self):
                return real.workspace

            @property
            def root(self):
                return real.root

            @property
            def host(self):
                return real.host

            @property
            def human(self):
                return real.human

            @property
            def system(self):
                return real.system

            def verify_session(self, pack, label, *, require_tool_actions=True,
                               require_evidence_backed_actions=False):
                if require_tool_actions and not session.get("tool_actions"):
                    raise self.module.Failed("no tool action")
                return session

            def seal_session_artifacts(self, record, session):
                self.module.Gate.seal_session_artifacts(self, record, session)

            def semantic_review(self, session, label):
                return real.semantic_review(session, label)

            def collect_tool_evidence(self, task_id, **kwargs):
                return real.collect_tool_evidence(task_id, **kwargs)

        record = self.module.CaseRecord("P4-G05", "PASS")
        self.module._verify_g05_pack(_Gate(self.module), record, dict(self.pack))
        return record

    def test_a_degraded_pack_with_review_and_tool_output_passes(self) -> None:
        record = self._run()
        kinds = [receipt["kind"] for receipt in record.receipts]
        self.assertIn("g05-degraded-pack", kinds)
        self.assertIn("g05-session", kinds)
        self.assertIn("g05-tool-evidence", kinds)

    def test_each_review_failure_is_refused(self) -> None:
        for name, review, needle in (
            ("missing", None, "no semantic_review block"),
            ("digest", _review_for(self.answer, digest="d" * 64),
             "attached to a different answer"),
            ("verdict", _review_for(self.answer, verdict="inconsistent"),
             "returned 'inconsistent'"),
            ("reviewer", _review_for(self.answer, reviewer=""),
             "names no reviewer"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(self.module.Failed) as caught:
                    self._run(review)
                self.assertIn(needle, str(caught.exception))


# -- STEP 4: G01 executable with the new wiring --------------------------------

def _render_pack_for_test(*, project, task_id, source_event_id, title, status,
                          memory_block=""):
    compact = dict(separators=(",", ":"), ensure_ascii=False)
    header = "JASMINE CONTEXT v1\n" + json.dumps({
        "project_id": project, "task_id": task_id, "task_revision": 1,
        "source_event_id": source_event_id,
        "precedence": "CURRENT TRUTH > CHECKPOINT > NON-AUTHORITATIVE MEMORY",
        "tokenizer_qualification": "cl100k_base", "reconciliation_required": True},
        **compact) + "\n"
    state = "CURRENT STATE\n" + json.dumps({
        "task": {"task_id": task_id, "title": title, "description": None,
                 "status": "PLANNED", "revision": 1, "acceptance_criteria": None},
        "current_step_id": None, "steps": [],
        "safety": "Context never verifies or accepts Evidence or State."}, **compact) + "\n"
    memory = "MEMORY — NON AUTHORITATIVE\n" + json.dumps({"status": status},
                                                         **compact) + "\n" + memory_block
    return header + "AUTHORITY\n" + state + memory


class G01RunsEndToEndOnEvidenceBackedSessions(unittest.TestCase):
    """The new session-receipt contract, proved against a real Core: every
    declared tool action bound to an Evidence row on the pack's task, every
    performed category naming its backing action(s), the final verification
    bound like any other action, and the lesson's command re-derived from the
    Evidence row behind the pack."""

    def setUp(self) -> None:
        self.module = _module()
        self.gate = _real_gate(self, case_id="P4-G01")
        self.project = self.gate.seed_project("g01-e2e")
        title = f"P4-G01 {self.module.G01_TASK_TITLE}"
        self.baseline_task = self.gate.new_task(self.project, self.module.G01_TASK_TITLE)
        self.lesson_task = self.gate.new_task(self.project, self.module.G01_TASK_TITLE)
        self.historical_task = self.gate.new_task(self.project, "device test (historical)")
        self.baseline_prompt = self.gate.event(self.project, "user.prompt",
                                               self.module.G01_PROMPT,
                                               task_id=self.baseline_task)
        self.lesson_prompt = self.gate.event(self.project, "user.prompt",
                                             self.module.G01_PROMPT,
                                             task_id=self.lesson_task)
        # The historical failure, through the real Evidence path.
        self.lesson_evidence = self.gate.collect_tool_evidence(
            self.historical_task, command="pytest -q",
            output="FAILED test_device.py\n", exit_code=1)
        conn = self.gate.conn()
        try:
            row = conn.execute(
                "SELECT change_event_id FROM evidence WHERE evidence_id=?",
                (self.lesson_evidence,)).fetchone()
            self.lesson_source = row["change_event_id"]
        finally:
            conn.close()
        self.memory_id = "mem_" + "M" * 26
        self._title = title

    def _rendered(self, *, task, prompt, status, block=""):
        return _render_pack_for_test(project=self.project, task_id=task,
                                     source_event_id=prompt, title=self._title,
                                     status=status, memory_block=block)

    def _history_block(self):
        return _history_block(memory_id=self.memory_id, command="pytest -q",
                              task=self.historical_task, step="stp_" + "S" * 26,
                              evidence_id=self.lesson_evidence,
                              sources=[self.lesson_source], project=self.project)

    def _run(self):
        baseline_rendered = self._rendered(task=self.baseline_task,
                                           prompt=self.baseline_prompt, status="empty")
        lesson_rendered = self._rendered(task=self.lesson_task, prompt=self.lesson_prompt,
                                         status="ok", block=self._history_block())
        baseline_pack = _pack_dict(
            pack_id="ctx_" + "B" * 26, project=self.project, task=self.baseline_task,
            prompt=self.baseline_prompt, rendered=baseline_rendered, status="empty")
        lesson_pack = _pack_dict(
            pack_id="ctx_" + "L" * 26, project=self.project, task=self.lesson_task,
            prompt=self.lesson_prompt, rendered=lesson_rendered, status="ok",
            selected=[{"memory_id": self.memory_id, "memory_type": "FAILURE_LESSON",
                       "bank": {"bank_id": f"jasmine-project-{self.project}",
                                "scope": "PROJECT", "project_id": self.project},
                       "source_event_ids": [self.lesson_source],
                       "source_task_id": self.historical_task,
                       "fact": "the callback task id did not match"}],
            provenance=[{"memory_id": self.memory_id,
                         "evidence_ids": [self.lesson_evidence],
                         "source_event_ids": [self.lesson_source]}])
        for pack_id, task, prompt, rendered in (
                ("ctx_" + "B" * 26, self.baseline_task, self.baseline_prompt,
                 baseline_rendered),
                ("ctx_" + "L" * 26, self.lesson_task, self.lesson_prompt,
                 lesson_rendered)):
            _insert_pack_row(self.gate, pack_id=pack_id, project=self.project, task=task,
                             rendered=rendered, truth_digest="d" * 64,
                             command_event=prompt, source_event=prompt)
        final_output = "Ran 1 test in 0.001s\n\nOK\n"
        # Every declared command is collected on its pack's task first: that is
        # the harness side of the contract the orchestrator must honour.
        self.gate.collect_tool_evidence(self.baseline_task, command="ls",
                                        output="source.txt\n", exit_code=0)
        self.gate.collect_tool_evidence(self.baseline_task, command="git log --oneline",
                                        output="abc123\n", exit_code=0)
        self.gate.collect_tool_evidence(self.baseline_task, command="pytest -q",
                                        output=final_output, exit_code=0)
        self.gate.collect_tool_evidence(self.lesson_task, command="pytest -q",
                                        output=final_output, exit_code=0)
        _write_session(self.gate, "g01-baseline", {
            "commit": self.gate.git_commit(), "context_pack_id": "ctx_" + "B" * 26,
            "pack_sha256": hashlib.sha256(baseline_rendered.encode("utf-8")).hexdigest(),
            "tool_actions": [{"command": "ls", "output": "source.txt\n"},
                             {"command": "git log --oneline", "output": "abc123\n"}],
            "task_completed": True,
            "final_verification": {"command": "pytest -q", "output": final_output},
            "exploration": {name: name in {"enumerate_repository", "read_history"}
                            for name in self.module.EXPLORATION_CATEGORIES},
            "exploration_actions": {"enumerate_repository": ["ls"],
                                    "read_history": ["git log --oneline"]},
            "final_answer": "done"})
        _write_session(self.gate, "g01-lesson", {
            "commit": self.gate.git_commit(), "context_pack_id": "ctx_" + "L" * 26,
            "pack_sha256": hashlib.sha256(lesson_rendered.encode("utf-8")).hexdigest(),
            "memory_seen": [{"memory_id": self.memory_id,
                             "evidence_ids": [self.lesson_evidence],
                             "source_event_ids": [self.lesson_source]}],
            "tool_actions": [{"command": "pytest -q", "output": final_output}],
            "task_completed": True,
            "final_verification": {"command": "pytest -q", "output": final_output},
            "exploration": {name: name in {"reproduce_failure"}
                            for name in self.module.EXPLORATION_CATEGORIES},
            "exploration_actions": {"reproduce_failure": ["pytest -q"]},
            "final_answer": (
                f"Historically {self.memory_id} from task {self.historical_task} "
                f"recorded the callback mismatch ({self.lesson_evidence}); "
                f"re-running pytest -q now.")})
        record = self.module.CaseRecord("P4-G01", "PASS")
        self.module._verify_g01_packs(self.gate, record, lesson_pack, baseline_pack)
        return record

    def test_the_full_g01_contract_verifies(self) -> None:
        record = self._run()
        self.assertEqual(record.verdict, "PASS")
        self.assertEqual(record.engine_evidence["differential"]["omitted_categories"],
                         ["enumerate_repository", "read_history"])
        self.assertEqual(
            record.engine_evidence["differential"]["lesson_completion"]["markers"],
            list(self.module.SUITE_SUCCESS_MARKERS))


# -- lesser items --------------------------------------------------------------

class ManifestAnchorCoversEveryVerdict(unittest.TestCase):
    """The anchor is written write-once for every manifest `main()` emits --
    PASS, FAIL and BLOCKED alike -- and a rewritten manifest never matches."""

    def setUp(self) -> None:
        self.module = _module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-fix-anchor-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)
        self.meta = {"commit": "abc1234", "dirty": False}

    def _manifest(self, name, verdict):
        out = self.work / "out"
        out.mkdir(exist_ok=True)
        manifest = out / name
        manifest.write_text(json.dumps({"verdict": verdict, "phase": "P4"}),
                            encoding="utf-8")
        return manifest

    def test_blocked_and_fail_manifests_anchor_write_once(self) -> None:
        for verdict in ("BLOCKED", "FAIL"):
            with self.subTest(verdict=verdict):
                manifest = self._manifest(f"p4-gate-{verdict}.json", verdict)
                anchor = self.module._write_manifest_anchor(self.work, "phase1", manifest,
                                                           self.meta)
                self.assertEqual(anchor["write"], "created")
                manifest.write_text(json.dumps({"verdict": "PASS", "phase": "P4"}),
                                    encoding="utf-8")
                with self.assertRaises(self.module.Failed) as caught:
                    self.module._write_manifest_anchor(self.work, "phase1", manifest,
                                                       self.meta)
                self.assertIn("written once", str(caught.exception))
                self.assertNotEqual(
                    hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    anchor["manifest_sha256"])

    def test_a_blocked_run_still_anchors_its_manifest(self) -> None:
        out = self.work / "e2e-out"
        work = self.work / "e2e-work"
        config = self.work / "op.json"
        config.write_text(json.dumps({"run_id": "p4-anchor-blocked",
                                      "bank_suffix": "p4-anchor-blocked",
                                      "dependency_commits": {"P2": "blocked",
                                                             "P3": "blocked"}}),
                          encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(SRC),
                   JASMINE_HINDSIGHT_URL="http://127.0.0.1:1",
                   JASMINE_HINDSIGHT_TIMEOUT_SECONDS="1")
        env.pop("JASMINE_HINDSIGHT_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, str(Path(SRC).parent / "scripts" / "p4-memory-gate.py"),
             "--out", str(out), "--work", str(work), "--config", str(config),
             "--allow-dirty"],
            capture_output=True, text=True, env=env, timeout=300)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        manifest_path = Path(summary["result"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["verdict"], "BLOCKED")
        anchor = Path(summary["anchor_path"])
        self.assertTrue(anchor.is_file())
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                         json.loads(anchor.read_text(encoding="utf-8"))["manifest_sha256"])


class SealedArtifactsCarryByteCounts(unittest.TestCase):
    """`sealed_artifacts` entries with a null byte count cannot be re-hashed
    from the manifest alone. Both the created and the unchanged paths must
    report the file's size."""

    def setUp(self) -> None:
        self.module = _module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-fix-bytes-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.gate = self.module.Gate.__new__(self.module.Gate)
        self.gate.root = self.root

    def test_receipt_bytes_on_created_and_unchanged(self) -> None:
        created = self.gate.receipt("g06-document", {"id": "doc-1"})
        unchanged = self.gate.receipt("g06-document", {"id": "doc-1"})
        self.assertEqual(created["write"], "created")
        self.assertEqual(unchanged["write"], "unchanged")
        size = Path(created["path"]).stat().st_size
        self.assertEqual(created["bytes"], size)
        self.assertEqual(unchanged["bytes"], size)

    def test_every_sealed_session_artifact_reports_bytes(self) -> None:
        gate = _real_gate(self, case_id="P4-BYTES")
        project = gate.seed_project("bytes")
        task = gate.new_task(project, "byte counts")
        prompt = gate.event(project, "user.prompt", "go", task_id=task)
        rendered = "JASMINE CONTEXT v1\n{}\n"
        pack_id = "ctx_" + "9" * 26
        pack_file = gate.root / "pack-sealed.json"
        pack_file.write_text(json.dumps({"rendered_content": rendered}), encoding="utf-8")
        pack = _pack_dict(pack_id=pack_id, project=project, task=task, prompt=prompt,
                          rendered=rendered, status="empty")
        pack["receipt"] = {"path": str(pack_file),
                           "sha256": hashlib.sha256(pack_file.read_bytes()).hexdigest()}
        _insert_pack_row(gate, pack_id=pack_id, project=project, task=task,
                         rendered=rendered, truth_digest="d" * 64,
                         command_event=prompt, source_event=prompt)
        gate.collect_tool_evidence(task, command="ls", output="a\n", exit_code=0)
        _write_session(gate, "bytes", {
            "commit": gate.git_commit(), "context_pack_id": pack_id,
            "pack_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "tool_actions": [{"command": "ls", "output": "a\n"}]})
        session = gate.verify_session(pack, "bytes", require_tool_actions=True,
                                      require_evidence_backed_actions=True)
        for artifact in session["verified"]["sealed"]:
            self.assertIsInstance(artifact.get("bytes"), int)
            self.assertEqual(artifact["bytes"], Path(artifact["path"]).stat().st_size)


class EvidenceScopeStatesAnchorAndToolEvidence(unittest.TestCase):
    def test_the_scope_names_the_anchor_and_the_tool_rows(self) -> None:
        module = _module()
        operator = {"path": "/work/op.json", "sha256": "0" * 64, "bytes": 12,
                    "kind": "operator-config"}
        scope = module._evidence_scope(Path("/work"), [], operator)
        self.assertIn("manifest_anchor", scope["sealed_byte_for_byte"])
        self.assertTrue(any("tool Evidence" in entry for entry in scope["out_of_scope"]))


class BankTouchingCasesRunUnderANamespace(unittest.TestCase):
    """`require_bank_namespace()` for every case that touches banks, enforced
    the same static way the P3 dependency is: the case's own bytecode must
    name it, so dropping it is a visible diff."""

    def test_every_bank_touching_case_enforces_the_namespace(self) -> None:
        import dis

        module = _module()
        for case_id in ("P4-G01", "P4-G02", "P4-G03", "P4-G04", "P4-G05", "P4-G06",
                        "P4-G07"):
            with self.subTest(case=case_id):
                names = {instruction.argval
                         for instruction in dis.get_instructions(
                             module.CASES_BY_ID[case_id])}
                self.assertIn("require_bank_namespace", names,
                              f"{case_id} touches banks without enforcing the namespace")


if __name__ == "__main__":
    unittest.main()
