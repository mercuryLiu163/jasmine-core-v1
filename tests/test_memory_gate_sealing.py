"""P4: the evidence chain must survive being finished twice.

These three properties are about *sealing*, not about behaviour, and none of them can
be caught by running the happy path once -- each only shows up when a run is finished a
second time, or when something outside the Gate touches a file:

  * a receipt is write-once, because an unconditional rewrite is how G06 lost its
    phase 1 evidence while the manifest went on citing a digest nothing matched;
  * a resumed run must verify the previous phase's seal *before* running a case, and
    must name that phase in its own manifest;
  * an agent session's raw bytes and the exact pack bytes injected into it are sealed,
    so "the agent saw this pack" stays checkable after the input file is edited.

Each test drives the real helpers. A mock that cannot be rewritten is not evidence that
the real file is protected.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import import_gate_module


_FAILED = import_gate_module().Failed


class AReceiptIsWrittenOnce(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-seal-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.gate = import_gate_module().Gate.__new__(import_gate_module().Gate)
        self.gate.root = self.root

    def test_the_first_write_seals(self) -> None:
        sealed = self.gate.receipt("g06-document", {"id": "doc-1", "units": 2})
        path = Path(sealed["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(sealed["write"], "created")
        self.assertEqual(sealed["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600, "a receipt is readable by "
                                                            "anyone on the machine")

    def test_writing_identical_bytes_again_is_allowed(self) -> None:
        # A resumed case legitimately re-asserts what it already proved; refusing that
        # would make every case fail on its second phase for the wrong reason.
        first = self.gate.receipt("g06-attempts", {"attempts": 3})
        again = self.gate.receipt("g06-attempts", {"attempts": 3})
        self.assertEqual(first["sha256"], again["sha256"])
        self.assertEqual(again["write"], "unchanged")

    def test_writing_different_bytes_is_refused(self) -> None:
        # This is the G06 failure. The old code overwrote the file, and phase 1's
        # manifest went on citing a sha256 that no longer described anything on disk.
        self.gate.receipt("g06-document", {"id": "doc-1", "memory_unit_count": 2})
        with self.assertRaises(_FAILED) as caught:
            self.gate.receipt("g06-document", {"id": "doc-1", "memory_unit_count": 3})
        message = str(caught.exception)
        self.assertIn("written once", message)
        self.assertIn("Refusing rather than overwriting", message)
        # ...and the original bytes are still the ones on disk.
        on_disk = json.loads((self.root / "g06-document.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["memory_unit_count"], 2)

    def test_a_read_back_receipt_recomputes_its_digest(self) -> None:
        sealed = self.gate.receipt("g06-document", {"id": "doc-1"})
        read = self.gate.read_receipt("g06-document")
        self.assertEqual(read["sha256"], sealed["sha256"])
        self.assertEqual(read["payload"], {"id": "doc-1"})
        self.assertEqual(read["write"], "read-back")

    def test_a_missing_receipt_is_an_error_not_an_empty_dict(self) -> None:
        with self.assertRaises(_FAILED) as caught:
            self.gate.read_receipt("never-written")
        self.assertIn("is missing", str(caught.exception))

    def test_an_edited_receipt_does_not_claim_its_original_digest(self) -> None:
        # The digest is recomputed from the bytes on disk, not copied from the manifest
        # that cited it, so tampering between phases is visible here.
        sealed = self.gate.receipt("g06-document", {"id": "doc-1"})
        (self.root / "g06-document.json").write_text('{"id": "doc-2"}', encoding="utf-8")
        read = self.gate.read_receipt("g06-document")
        self.assertNotEqual(read["sha256"], sealed["sha256"])
        self.assertEqual(read["payload"], {"id": "doc-2"})


class AResumedRunVerifiesTheSealItContinues(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-phase-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)
        self.module = import_gate_module()

    def _phase1(self) -> tuple[Path, Path, dict]:
        """A minimal but real phase 1: a manifest and one sealed receipt."""
        (self.work / "P4-G06").mkdir(parents=True)
        receipt = self.work / "P4-G06" / "g06-document.json"
        receipt.write_text(json.dumps({"id": "doc-1"}), encoding="utf-8")
        record = self.module.CaseRecord("P4-G06", "PASS", mode="real")
        record.receipts.append({"path": str(receipt), "kind": "g06-document",
                                "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()})
        out = self.work / "phase1-out"
        out.mkdir()
        manifest = out / "p4-gate-1.json"
        manifest.write_text(json.dumps({"verdict": "PASS", "phase": "P4"}), encoding="utf-8")
        seal = self.module._write_phase_seal(
            self.work, "phase1", manifest, [record],
            {"commit": "abc1234", "dirty": False, "schema_version": 11})
        # The real runner writes the manifest a second time to name the seal; the seal
        # must therefore accept the pair, and the resumed run must find them consistent.
        body = json.loads(manifest.read_text(encoding="utf-8"))
        body["seal"] = {"sha256": seal["sha256"], "phase": "phase1",
                        "receipts_sealed": seal["receipts_sealed"]}
        manifest.write_text(json.dumps(body), encoding="utf-8")
        return manifest, self.work / "phase1-seal.json", seal

    def test_phase_one_seals_its_receipts_and_names_its_manifest(self) -> None:
        manifest, seal_path, seal = self._phase1()
        body = json.loads(seal_path.read_text(encoding="utf-8"))
        self.assertEqual(body["manifest_path"], str(manifest))
        self.assertEqual([r["kind"] for r in body["receipts"]], ["g06-document"])
        self.assertEqual(seal["receipts_sealed"], 1)
        self.assertEqual(seal_path.stat().st_mode & 0o777, 0o600)
        # The seal deliberately carries no digest of the manifest: the manifest is
        # rewritten after the seal so it can name the seal, so a digest taken at seal
        # time could never match the bytes left behind. The chain is
        # receipts -> seal -> manifest.
        self.assertNotIn("manifest_sha256", body)

    def test_a_resumed_run_passes_when_nothing_moved(self) -> None:
        self._phase1()
        prior = self.module._verify_prior_seal(self.work, "phase2")
        self.assertIsNotNone(prior)
        self.assertEqual(prior["receipts_verified"], 1)
        self.assertEqual(prior["commit"], "abc1234")
        self.assertEqual(prior["manifest_sha256"],
                         hashlib.sha256(Path(prior["manifest_path"]).read_bytes()).hexdigest())

    def test_a_resumed_run_refuses_when_a_receipt_was_edited(self) -> None:
        self._phase1()
        (self.work / "P4-G06" / "g06-document.json").write_text('{"id": "doc-2"}',
                                                                encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.module._verify_prior_seal(self.work, "phase2")
        message = str(caught.exception)
        self.assertIn("no longer matches the disk", message)
        self.assertIn("edited", message)

    def test_a_resumed_run_refuses_when_a_receipt_vanished(self) -> None:
        self._phase1()
        (self.work / "P4-G06" / "g06-document.json").unlink()
        with self.assertRaises(_FAILED) as caught:
            self.module._verify_prior_seal(self.work, "phase2")
        self.assertIn("missing", str(caught.exception))

    def test_a_resumed_run_refuses_when_the_manifest_names_another_seal(self) -> None:
        # A manifest that does not name this seal came from some other run's phase, so
        # the two files existing side by side does not make them one evidence chain.
        manifest, _, _ = self._phase1()
        body = json.loads(manifest.read_text(encoding="utf-8"))
        body["seal"] = {"sha256": "a" * 64}
        manifest.write_text(json.dumps(body), encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.module._verify_prior_seal(self.work, "phase2")
        self.assertIn("names seal", str(caught.exception))

    def test_a_resumed_run_refuses_when_the_manifest_is_gone(self) -> None:
        manifest, _, _ = self._phase1()
        manifest.unlink()
        with self.assertRaises(_FAILED) as caught:
            self.module._verify_prior_seal(self.work, "phase2")
        self.assertIn("manifest named by the seal is gone", str(caught.exception))

    def test_resuming_without_a_seal_is_refused(self) -> None:
        # Otherwise the final phase can be produced by pointing it at a work directory
        # whose earlier evidence was never sealed, and the two manifests look related.
        with self.assertRaises(_FAILED) as caught:
            self.module._verify_prior_seal(self.work, "phase2")
        self.assertIn("without a phase 1 seal", str(caught.exception))

    def test_phase_one_needs_no_prior_seal(self) -> None:
        self.assertIsNone(self.module._verify_prior_seal(self.work, "phase1"))

    def test_a_re_seal_against_different_receipts_is_refused(self) -> None:
        self._phase1()
        (self.work / "P4-G06" / "g06-document.json").write_text('{"id": "doc-2"}',
                                                                encoding="utf-8")
        record = self.module.CaseRecord("P4-G06", "PASS", mode="real")
        record.receipts.append({
            "path": str(self.work / "P4-G06" / "g06-document.json"),
            "kind": "g06-document",
            "sha256": hashlib.sha256(
                (self.work / "P4-G06" / "g06-document.json").read_bytes()).hexdigest()})
        out = self.work / "out3"
        out.mkdir()
        manifest = out / "p4-gate-9.json"
        manifest.write_text("{}", encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.module._write_phase_seal(self.work, "phase1", manifest, [record],
                                         {"commit": "abc1234", "dirty": False,
                                          "schema_version": 11})
        self.assertIn("already sealed", str(caught.exception))

    def test_one_work_directory_is_one_phase(self) -> None:
        # Sealing twice against different manifests means one directory produced two
        # phases, which is exactly what the prior-seal check is meant to catch.
        self._phase1()
        record = self.module.CaseRecord("P4-G06", "PASS", mode="real")
        out = self.work / "other-out"
        out.mkdir()
        manifest = out / "p4-gate-2.json"
        manifest.write_text('{"verdict": "PASS"}', encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.module._write_phase_seal(self.work, "phase1", manifest, [record],
                                         {"commit": "abc1234", "dirty": False,
                                          "schema_version": 11})
        self.assertIn("already sealed", str(caught.exception))

    def test_a_seal_refuses_to_cover_a_receipt_that_is_not_on_disk(self) -> None:
        record = self.module.CaseRecord("P4-G06", "PASS", mode="real")
        record.receipts.append({"path": str(self.work / "absent.json"),
                                "kind": "g06-document", "sha256": "0" * 64})
        out = self.work / "out"
        out.mkdir()
        manifest = out / "p4-gate-3.json"
        manifest.write_text("{}", encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.module._write_phase_seal(self.work, "phase1", manifest, [record],
                                         {"commit": "abc1234", "dirty": False,
                                          "schema_version": 11})
        self.assertIn("not on disk", str(caught.exception))


class AnInjectedPackIsHashedAndSealed(unittest.TestCase):
    """The bytes an agent was shown, sealed next to the bytes it wrote down."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-inject-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.module = import_gate_module()
        self.gate = self.module.Gate.__new__(self.module.Gate)
        self.gate.root = self.root
        self.gate.case_id = "P4-G01"

    def test_the_pack_file_is_copied_byte_for_byte(self) -> None:
        original = self.root / "pack-ctx_1.json"
        rendered = "JASMINE CONTEXT v1\n{}\nMEMORY — NON AUTHORITATIVE\n"
        original.write_text(json.dumps({"rendered_content": rendered}), encoding="utf-8")
        pack = {"context_pack_id": "ctx_1", "rendered_content": rendered,
                "sections": ["HEADER", "MEMORY"], "token_count": 12,
                "receipt": {"path": str(original),
                            "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}}
        sealed = self.gate._seal_injected_pack("g01-lesson", pack)
        self.assertEqual(sealed["write"], "sealed")
        copied = Path(sealed["path"])
        self.assertEqual(copied.read_bytes(), original.read_bytes())
        self.assertEqual(sealed["sha256"], pack["receipt"]["sha256"])
        self.assertEqual(copied.stat().st_mode & 0o777, 0o600)

    def test_an_already_sealed_pack_that_differs_is_reported(self) -> None:
        original = self.root / "pack-ctx_1.json"
        original.write_text(json.dumps({"rendered_content": "A"}), encoding="utf-8")
        pack = {"context_pack_id": "ctx_1", "rendered_content": "A", "sections": [],
                "token_count": 1,
                "receipt": {"path": str(original),
                            "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}}
        self.gate._seal_injected_pack("g01-lesson", pack)
        # The pack is re-rendered differently on a later phase; the sealed copy stands.
        pack["receipt"]["sha256"] = "f" * 64
        with self.assertRaises(_FAILED) as caught:
            self.gate._seal_injected_pack("g01-lesson", pack)
        self.assertIn("hashes to", str(caught.exception))

    def test_the_injection_record_names_the_bytes_not_the_description(self) -> None:
        rendered = "JASMINE CONTEXT v1\n{\"task_id\":\"tsk_1\"}\n"
        record = self.gate._injection_record("g01-lesson", {
            "context_pack_id": "ctx_1", "rendered_content": rendered,
            "sections": ["HEADER"], "token_count": 9,
            "receipt": {"path": None, "sha256": None}})
        self.assertEqual(record["injected_sha256"],
                         hashlib.sha256(rendered.encode("utf-8")).hexdigest())
        self.assertEqual(record["injected_bytes"], len(rendered.encode("utf-8")))
        self.assertEqual(record["context_pack_id"], "ctx_1")
        # Two packs with one id and different content must be distinguishable.
        other = self.gate._injection_record("g01-lesson", {
            "context_pack_id": "ctx_1", "rendered_content": rendered + "x",
            "sections": ["HEADER"], "token_count": 9,
            "receipt": {"path": None, "sha256": None}})
        self.assertNotEqual(record["injected_sha256"], other["injected_sha256"])


class G01ComparesTwoIndependentlySealedSessions(unittest.TestCase):
    """The baseline must be a real session over a real empty pack, not a summary."""

    def test_the_baseline_pack_is_required(self) -> None:
        module = import_gate_module()
        with self.assertRaises(_FAILED) as caught:
            module._verify_g01_packs(mock.Mock(), module.CaseRecord("P4-G01", "PASS"), {},
                                     None)
        self.assertIn("baseline pack", str(caught.exception))

    def test_a_baseline_pack_that_carries_memory_is_refused(self) -> None:
        # Otherwise the "before" side of the comparison is not the before, and the
        # differential would be measuring nothing.
        module = import_gate_module()
        gate = mock.Mock()
        record = module.CaseRecord("P4-G01", "PASS")
        baseline_pack = {"context_pack_id": "ctx_b", "memory_status": "ok",
                         "memory_selected": [{"memory_id": "mem_1"}],
                         "memory_filtered": [], "task_id": "tsk_b"}
        with self.assertRaises(_FAILED) as caught:
            module._verify_g01_packs(gate, record, {"context_pack_id": "ctx_l",
                                                     "task_id": "tsk_l"}, baseline_pack)
        self.assertIn("not empty", str(caught.exception))

    def test_the_same_task_on_both_sides_is_refused(self) -> None:
        module = import_gate_module()
        record = module.CaseRecord("P4-G01", "PASS")
        baseline_pack = {"context_pack_id": "ctx_b", "memory_status": "empty",
                         "memory_selected": [], "memory_filtered": [], "task_id": "tsk_1"}
        with self.assertRaises(_FAILED) as caught:
            module._verify_g01_packs(mock.Mock(), record,
                                     {"context_pack_id": "ctx_l", "task_id": "tsk_1"},
                                     baseline_pack)
        self.assertIn("same task", str(caught.exception))


class G05sToolCaptureIsContentAddressed(unittest.TestCase):
    """A repeated resume must add no Evidence row, no turn, and no new receipt bytes.

    The capture minted a fresh `turn_id`/`tool_use_id` on every invocation, so the
    `evidence_id` it recorded changed and the write-once guard failed the case on the
    second `--resume`. One work directory could pass phase 2 exactly once.
    """

    def setUp(self) -> None:
        from jasmine_core import SCHEMA_VERSION, db, ids, registry
        from jasmine_core.migrations import migrate

        self.module = import_gate_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-g05-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G05"
        self.root.mkdir(parents=True)
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "source.txt").write_text("v1\n", encoding="utf-8")
        self.gate = self.module.Gate.__new__(self.module.Gate)
        self.gate.root = self.root
        self.gate.case_id = "P4-G05"
        self.gate.workspace = self.workspace
        self.gate.host = ids.new_id("hst")
        self.gate.human = ids.new_id("act")
        self.gate.system = ids.new_id("act")
        self.gate.agent = ids.new_id("act")
        self.gate.db_path = self.root / "core.db"
        # The capture puts the command's output in the workspace and Core hashes it, so
        # the fingerprint root is part of what this test needs to be able to reach.
        environment = mock.patch.dict("os.environ", {
            "JASMINE_CORE_WORKSPACE_ROOT": str(self.workspace),
            "JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["source.txt"]'}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        from jasmine_core.models import NewObject
        from jasmine_core.objects import ObjectStore

        conn = db.connect(self.gate.db_path)
        migrate(conn)
        reg = registry.Registry(conn)
        reg.upsert_host(self.gate.host, display_name="h")
        for actor_id, kind in ((self.gate.human, "human"), (self.gate.system, "system"),
                               (self.gate.agent, "agent")):
            reg.upsert_actor(actor_id, kind=kind, home_host_id=self.gate.host)
        # ObjectStore.create opens its own transaction (ADR 0002), so these are outside.
        self.project = ObjectStore(conn, schema_version=SCHEMA_VERSION).create(
            NewObject.project({"name": "g05", "host_id": self.gate.host}),
            actor_id=self.gate.human)["object"]["project_id"]
        self.task = ObjectStore(conn, schema_version=SCHEMA_VERSION).create(
            NewObject.task({"project_id": self.project, "title": "g05",
                            "host_id": self.gate.host}),
            actor_id=self.gate.human)["object"]["task_id"]
        conn.close()
        self.pack = {"context_pack_id": "ctx_" + "C" * 26, "task_id": self.task,
                     "project_id": self.project}
        self.session = {"tool_actions": [
            {"command": "python -m unittest tests/test_x.py", "output": "Ran 1 test\n\nOK\n"}]}

    def _capture(self):
        record = self.module.CaseRecord("P4-G05", "PASS")
        self.module._record_session_tool_evidence(self.gate, record, self.pack,
                                                  self.session)
        return record

    def _evidence_rows(self):
        from jasmine_core import db

        conn = db.connect(self.gate.db_path)
        try:
            return conn.execute("SELECT COUNT(*) AS n FROM evidence WHERE task_id=?",
                                (self.task,)).fetchone()["n"]
        finally:
            conn.close()

    def _tool_turns(self):
        """How many distinct turns the tool Events on this task carry.

        A fresh `turn_id` per invocation is what minted a fresh `evidence_id` before, so
        this is the number that has to stay at one across a resume.
        """
        from jasmine_core import db

        conn = db.connect(self.gate.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM events WHERE event_type IN"
                " ('tool.call','tool.result') AND task_id=?", (self.task,)).fetchall()
            return len({json.loads(row["payload_json"])["turn_id"] for row in rows})
        finally:
            conn.close()

    def test_two_captures_produce_one_row_one_turn_and_the_same_receipt(self) -> None:
        first = self._capture()
        second = self._capture()
        self.assertEqual(first.evidence_ids, second.evidence_ids)
        self.assertEqual(self._evidence_rows(), 1)
        self.assertEqual(self._tool_turns(), 1)
        self.assertEqual([r["sha256"] for r in first.receipts],
                         [r["sha256"] for r in second.receipts])
        # The manifest shows that the second run replayed rather than inserted.
        self.assertFalse(first.tool_uses[0]["evidence_reused"])
        self.assertTrue(second.tool_uses[0]["evidence_reused"])

    def test_the_receipt_records_the_row_count_and_the_content_addressed_ids(self) -> None:
        import hashlib

        record = self._capture()
        sealed = json.loads(Path(record.receipts[0]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(sealed["evidence_rows_for_task"], 1)
        # The ids are a pure function of the command, which is what makes the replay
        # path in `record_tool_result` reachable at all.
        seed = f"P4-G05|{self.session['tool_actions'][0]['command']}"
        self.assertEqual(sealed["turn_id"],
                         "t-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8])
        self.assertEqual(sealed["tool_use_id"],
                         hashlib.sha256(f"{seed}|tool".encode("utf-8")).hexdigest()[:8])
        self.assertEqual(sealed["command_sha256"],
                         hashlib.sha256(
                             self.session["tool_actions"][0]["command"].encode("utf-8")
                         ).hexdigest())

    def _insert_another_row_on_the_task(self) -> None:
        """A second real Evidence row on this task, the way a second capture would.

        Not a hand-written row: the same `record_tool_result` the case uses, with a
        different command, so the extra row is indistinguishable from one the defective
        capture would have left behind.
        """
        from jasmine_core import SCHEMA_VERSION
        from jasmine_core.evidence import EvidenceStore

        conn = self.gate.conn()
        try:
            EvidenceStore(conn, schema_version=SCHEMA_VERSION).record_tool_result(
                {"task_id": self.task, "host_id": self.gate.host,
                 "codex_session_id": "P4-G05", "turn_id": "t-0000beef",
                 "tool_use_id": "0000beef", "tool_name": "shell",
                 "tool_input": {"cmd": "git log --oneline"},
                 "tool_response": {"exit_code": 0, "stdout": "abc123 v1"}},
                actor_id=self.gate.system)
        finally:
            conn.close()

    def test_a_row_that_grows_across_a_resume_fails_with_the_count(self) -> None:
        # The reverse test for the idempotence assertion. The sealed receipt says one
        # row on this task and the task now carries two: a receipt that grows is a
        # failure, and it has to be reported as growth rather than surfacing later as a
        # write-once refusal whose message is about the receipt.
        self._capture()
        self._insert_another_row_on_the_task()
        self.assertEqual(self._evidence_rows(), 2)
        with self.assertRaises(_FAILED) as caught:
            self._capture()
        message = str(caught.exception)
        self.assertIn("went from 1 to 2", message)
        self.assertIn("replay the capture it already sealed", message)

    def test_a_different_command_is_a_different_capture(self) -> None:
        # The lookup is keyed on the command, so a genuinely different command gets its
        # own capture and its own ids: content-addressing is not "one row per task".
        # Checked in a second work directory, because in this one a new command is a
        # row the sealed count no longer accounts for -- which is the refusal above.
        self._capture()
        other_root = self.root.parent / "P4-G05-OTHER"
        other_root.mkdir()
        other = self.module.Gate.__new__(self.module.Gate)
        other.root = other_root
        other.case_id = "P4-G05"
        other.workspace = self.workspace
        other.host, other.human, other.system = (self.gate.host, self.gate.human,
                                                 self.gate.system)
        other.db_path = self.gate.db_path
        record = self.module.CaseRecord("P4-G05", "PASS")
        self.module._record_session_tool_evidence(
            other, record, self.pack,
            {"tool_actions": [{"command": "git log --oneline", "output": "abc123 v1"}]})
        sealed = json.loads(Path(record.receipts[0]["path"]).read_text(encoding="utf-8"))
        first = json.loads((self.root / "g05-tool-evidence.json").read_text(
            encoding="utf-8"))
        self.assertNotEqual(sealed["turn_id"], first["turn_id"])
        self.assertNotEqual(sealed["tool_use_id"], first["tool_use_id"])
        self.assertNotEqual(sealed["evidence_id"], first["evidence_id"])
        self.assertFalse(record.tool_uses[0]["evidence_reused"])


class G07sRetiredIdentityIsSealedAndRederived(unittest.TestCase):
    """The premise of G07 was an unsealed sidecar the case wrote for itself.

    Replacing its `memory_id` with `mem_000...` and its `text` with nonsense left G07
    reporting PASS, because the absence assertions were tested against whatever that
    file said.
    """

    def setUp(self) -> None:
        from jasmine_core import db
        from jasmine_core.migrations import migrate

        self.module = import_gate_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-g07-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G07"
        self.root.mkdir(parents=True)
        self.gate = self.module.Gate.__new__(self.module.Gate)
        self.gate.root = self.root
        self.gate.case_id = "P4-G07"
        self.gate.db_path = self.root / "core.db"
        conn = db.connect(self.gate.db_path)
        migrate(conn)
        conn.close()
        self.project = "prj_" + "A" * 26
        self.memory_id = "mem_" + "B" * 26

    def test_a_retired_identity_that_core_does_not_hold_is_refused(self) -> None:
        # The reverse test for the whole fix: the file says `mem_000...`, Core has no
        # such item, and the case that used to pass has to fail.
        self.gate.receipt("g07-retired", {"memory_id": "mem_" + "0" * 26,
                                          "text": "nonsense", "project_id": self.project})
        with self.assertRaises(_FAILED) as caught:
            self.gate.retired_premise()
        message = str(caught.exception)
        self.assertIn("Core has no such item", message)
        self.assertIn("disagree about the premise", message)

    def test_a_receipt_that_was_edited_after_sealing_is_not_its_own_digest(self) -> None:
        sealed = self.gate.receipt("g07-retired", {"memory_id": self.memory_id,
                                                   "text": "a lesson",
                                                   "project_id": self.project})
        (self.root / "g07-retired.json").write_text(json.dumps(
            {"memory_id": "mem_" + "0" * 26, "text": "nonsense",
             "project_id": self.project}), encoding="utf-8")
        read = self.gate.read_receipt("g07-retired")
        self.assertNotEqual(read["sha256"], sealed["sha256"])
        self.assertEqual(read["payload"]["memory_id"], "mem_" + "0" * 26)

    def test_the_retired_identity_is_a_sealed_receipt_not_a_bare_file(self) -> None:
        source = Path(self.module.__file__).read_text(encoding="utf-8")
        self.assertIn('gate.receipt("g07-retired", retired)', source)
        self.assertNotIn('(gate.root / "g07-retired.json").write_text', source,
                         "the retired identity must not be written beside the receipts "
                         "again: it was in neither seal and could be edited freely")


class ATamperedPackIsReportedNotCrashed(unittest.TestCase):
    """`except errors.Failed` where `errors` is `jasmine_core.errors`.

    `_seal_injected_pack` raises the runner's own module-level `Failed`, so a
    re-rendered pack surfaced as `AttributeError: module 'jasmine_core.errors' has no
    attribute 'Failed'` -- the operator got a crash in the plumbing instead of the
    message naming the pack that changed.
    """

    def setUp(self) -> None:
        from jasmine_core import db
        from jasmine_core.migrations import migrate

        self.module = import_gate_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-sealfail-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G01"
        self.root.mkdir(parents=True)
        self.gate = self.module.Gate.__new__(self.module.Gate)
        self.gate.root = self.root
        self.gate.case_id = "P4-G01"
        self.gate.db_path = self.root / "core.db"
        conn = db.connect(self.gate.db_path)
        migrate(conn)
        conn.close()
        self.rendered = "JASMINE CONTEXT v1\n{}\nMEMORY — NON AUTHORITATIVE\n"
        original = self.root / "pack-ctx_1.json"
        original.write_text(json.dumps({"rendered_content": self.rendered}),
                            encoding="utf-8")
        self.pack = {"context_pack_id": "ctx_1", "rendered_content": self.rendered,
                     "sections": ["HEADER"], "token_count": 4,
                     "rendered_sha256": "0" * 64, "truth_digest": "0" * 64,
                     "check_current": True, "qualified_provenance": [],
                     "memory_selected": [], "receipt": {
                         "path": str(original),
                         "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}}
        session = {"commit": None, "context_pack_id": "ctx_1", "pack_sha256": None,
                   "memory_seen": [], "tool_actions": []}
        (self.root / "session-g01-lesson.json").write_text(json.dumps(session),
                                                           encoding="utf-8")

    def test_a_rerendered_pack_reaches_the_operator_as_a_failure(self) -> None:
        # Seal the pack, then re-render it: the sealing check has to fire and be caught.
        self.gate._seal_injected_pack("g01-lesson", self.pack)
        self.pack["receipt"]["sha256"] = "f" * 64
        self.pack["rendered_content"] = self.rendered + "extra\n"
        (self.root / "session-g01-lesson.json").write_text(json.dumps(
            {"commit": self.gate.git_commit(), "context_pack_id": "ctx_1",
             "pack_sha256": hashlib.sha256(
                 self.pack["rendered_content"].encode("utf-8")).hexdigest(),
             "memory_seen": [], "tool_actions": []}), encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.gate.verify_session(self.pack, "g01-lesson")
        message = str(caught.exception)
        self.assertIn("The pack changed between phases", message)
        self.assertIn("does not stand up", message)


class TheFinalManifestIsAnchored(unittest.TestCase):
    """Nothing on disk held the FINAL manifest's digest, so its verdict was editable.

    The chain runs receipts -> seal -> manifest, and every link is checked from the one
    below it -- except the manifest. Rewriting `verdict` there changed no digest
    anywhere.
    """

    def setUp(self) -> None:
        self.module = import_gate_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-anchor-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name)
        self.meta = {"commit": "abc1234", "dirty": False}
        self.out = self.work / "out"
        self.out.mkdir()
        self.manifest = self.out / "p4-gate-1700000000.json"
        self.manifest.write_text(json.dumps({"verdict": "PASS", "phase": "P4"}),
                                 encoding="utf-8")

    def test_the_anchor_holds_the_manifest_digest_phase_and_commit(self) -> None:
        anchor = self.module._write_manifest_anchor(self.work, "phase2", self.manifest,
                                                    self.meta)
        path = Path(anchor["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(anchor["manifest_sha256"],
                         hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual(anchor["phase"], "phase2")
        self.assertEqual(anchor["commit"], "abc1234")
        self.assertEqual(anchor["write"], "created")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["manifest_path"],
                         str(self.manifest))

    def test_editing_the_manifest_is_visible_against_the_anchor(self) -> None:
        # The reverse test. A FAIL rewritten as a PASS leaves the anchor's digest
        # unmatched, and that is the only way anybody can tell.
        anchor = self.module._write_manifest_anchor(self.work, "phase2", self.manifest,
                                                    self.meta)
        self.manifest.write_text(json.dumps({"verdict": "PASS", "phase": "P4",
                                             "note": "rewritten"}), encoding="utf-8")
        self.assertNotEqual(
            hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            anchor["manifest_sha256"])

    def test_the_anchor_cannot_be_re_pointed_at_edited_bytes(self) -> None:
        self.module._write_manifest_anchor(self.work, "phase2", self.manifest, self.meta)
        self.manifest.write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.module._write_manifest_anchor(self.work, "phase2", self.manifest,
                                              self.meta)
        self.assertIn("An anchor is written once", str(caught.exception))

    def test_the_same_manifest_anchors_again_without_changing(self) -> None:
        first = self.module._write_manifest_anchor(self.work, "phase2", self.manifest,
                                                   self.meta)
        again = self.module._write_manifest_anchor(self.work, "phase2", self.manifest,
                                                   self.meta)
        self.assertEqual(again["write"], "unchanged")
        self.assertEqual(first["manifest_sha256"], again["manifest_sha256"])


class TheOperatorConfigurationIsSealed(unittest.TestCase):
    """`op.json` was the sole basis for `real_agent_session`, the bank namespace and the
    dependency commits, and it lived wherever the operator put it."""

    def setUp(self) -> None:
        self.module = import_gate_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-op-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()
        self.source = Path(self._tmp.name) / "op.json"
        self.config = {"run_id": "p4-test", "bank_suffix": "p4-test",
                       "real_agent_session": True,
                       "dependency_commits": {"P2": "x" * 40, "P3": "y" * 40}}
        self.source.write_text(json.dumps(self.config, indent=4), encoding="utf-8")

    def test_the_run_seals_a_byte_for_byte_copy(self) -> None:
        sealed = self.module._seal_operator_config(self.work, self.source, self.config)
        self.assertTrue(Path(sealed["path"]).is_file())
        self.assertEqual(Path(sealed["path"]).read_bytes(), self.source.read_bytes())
        self.assertEqual(sealed["sha256"],
                         hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(sealed["write"], "created")
        self.assertEqual(sealed["bytes"], self.source.stat().st_size)

    def test_a_different_configuration_for_the_same_work_directory_is_refused(self) -> None:
        self.module._seal_operator_config(self.work, self.source, self.config)
        other = Path(self._tmp.name) / "other.json"
        other.write_text(json.dumps({**self.config, "real_agent_session": False}),
                         encoding="utf-8")
        with self.assertRaises(_FAILED) as caught:
            self.module._seal_operator_config(self.work, other, self.config)
        self.assertIn("one run with one configuration", str(caught.exception))

    def test_a_configuration_that_does_not_describe_the_run_is_refused(self) -> None:
        # The bytes are sealed but the values the run used are not the ones in them:
        # then the manifest would be reporting a deployment it did not use.
        with self.assertRaises(_FAILED) as caught:
            self.module._seal_operator_config(
                self.work, self.source, {**self.config, "bank_suffix": "other"})
        self.assertIn("does not describe the configuration this run used",
                      str(caught.exception))

    def test_it_reaches_the_phase_seal(self) -> None:
        sealed = self.module._seal_operator_config(self.work, self.source, self.config)
        record = self.module.CaseRecord("P4-G06", "PASS")
        out = self.work / "out"
        out.mkdir()
        manifest = out / "p4-gate-1.json"
        manifest.write_text("{}", encoding="utf-8")
        self.module._write_phase_seal(self.work, "phase1", manifest, [record],
                                      {"commit": "abc", "dirty": False,
                                       "schema_version": 11}, sealed)
        body = json.loads((self.work / "phase1-seal.json").read_text(encoding="utf-8"))
        self.assertIn(sealed["path"], [entry["path"] for entry in body["receipts"]])
        self.assertEqual(body["operator_config"]["sha256"], sealed["sha256"])


class SealedArtifactsAreMeasuredAndTheScopeIsStated(unittest.TestCase):
    """`sealed_artifacts` byte counts were `null`, and nothing said what a digest covers.

    A manifest that lists digests without saying whether the artefacts are sealed or
    only hashed is easy to read as stronger than it is.
    """

    def setUp(self) -> None:
        self.module = import_gate_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-scope-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"

    def test_a_receipt_reports_how_many_bytes_it_is(self) -> None:
        gate = self.module.Gate.__new__(self.module.Gate)
        root = self.work / "P4-G06"
        root.mkdir(parents=True)
        gate.root = root
        sealed = gate.receipt("g06-document", {"id": "doc-1"})
        self.assertEqual(sealed["bytes"], Path(sealed["path"]).stat().st_size)
        self.assertEqual(gate.receipt("g06-document", {"id": "doc-1"})["bytes"],
                         sealed["bytes"])

    def test_a_sealed_injected_pack_reports_how_many_bytes_it_is(self) -> None:
        gate = self.module.Gate.__new__(self.module.Gate)
        root = self.work / "P4-G01"
        root.mkdir(parents=True)
        gate.root = root
        original = root / "pack-ctx_1.json"
        original.write_text(json.dumps({"rendered_content": "A"}), encoding="utf-8")
        pack = {"context_pack_id": "ctx_1", "rendered_content": "A", "sections": [],
                "token_count": 1,
                "receipt": {"path": str(original),
                            "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}}
        first = gate._seal_injected_pack("g01-lesson", pack)
        self.assertEqual(first["bytes"], Path(first["path"]).stat().st_size)
        self.assertEqual(gate._seal_injected_pack("g01-lesson", pack)["bytes"],
                         first["bytes"])

    def test_the_scope_says_sealed_hashed_and_out_of_scope(self) -> None:
        (self.work / "P4-G01").mkdir(parents=True)
        (self.work / "P4-G01" / "core.db").write_bytes(b"SQLite format 3\x00")
        operator = {"path": str(self.work / "op.json"), "sha256": "0" * 64,
                    "bytes": 12, "kind": "operator-config"}
        (self.work / "op.json").write_text("{}", encoding="utf-8")
        record = self.module.CaseRecord("P4-G01", "PASS")
        scope = self.module._evidence_scope(self.work, [record], operator)
        self.assertIn("receipts", scope["sealed_byte_for_byte"])
        self.assertIn("injected_packs", scope["sealed_byte_for_byte"])
        self.assertIn(operator["path"], scope["sealed_byte_for_byte"]["operator_config"])
        databases = scope["hashed_not_content_sealed"]["databases"]
        self.assertEqual([row["case_id"] for row in databases], ["P4-G01"])
        self.assertEqual(databases[0]["sha256"],
                         hashlib.sha256((self.work / "P4-G01" / "core.db").read_bytes()
                                        ).hexdigest())
        self.assertIn("NOT sealed", scope["hashed_not_content_sealed"]["note"])
        self.assertTrue(scope["out_of_scope"])


class ANegationInsideOneWordIsNotAnAssertion(unittest.TestCase):
    """"unverified" is a denial, and it is this system's own vocabulary.

    P3 labels the section it did not verify "NOT VERIFIED BY CONTEXT", so an agent
    that repeats the label back was describing the context accurately. A substring
    search found "verified" inside "unverified" and failed the case for it -- while the
    independent semantic reviewer had called that same answer consistent. The same
    applied to "unconfirmed".
    """

    def _asserted(self, clause: str):
        module = import_gate_module()
        return module.asserted_outcomes(clause, absence_blind=module.is_absence_statement(clause))

    def test_the_real_answer_that_failed_is_now_clean(self) -> None:
        clause = ("the Memory block is empty, and the evidence-hints block "
                  "(explicitly unverified) is also empty")
        self.assertEqual(self._asserted(clause), [],
                         "an honest description of an unverified block is not an "
                         "assertion that something was verified")

    def test_a_negating_prefix_fused_to_the_word_is_a_denial(self) -> None:
        for clause in ("explicitly unverified", "unconfirmed", "was unverified by the gate",
                       "the section is NOT VERIFIED BY CONTEXT"):
            with self.subTest(clause=clause):
                self.assertEqual(self._asserted(clause), [])

    def test_fabrication_is_still_caught(self) -> None:
        # The fix must not have blunted the check it was fixing.
        for clause in ("the test was verified", "it passed", "every run succeeded",
                       "all green", "passed every time it ran"):
            with self.subTest(clause=clause):
                self.assertTrue(self._asserted(clause), f"{clause!r} must be caught")

    def test_the_auditors_sentence_still_fails(self) -> None:
        module = import_gate_module()
        clause = ("There is no record of it in Core; the device 42 protocol test passed "
                  "every time it ran.")
        self.assertTrue(module.asserted_outcomes(
            clause, absence_blind=module.is_absence_statement(clause)),
            "denying the record in one clause does not excuse asserting an outcome "
            "in the next")

    def test_a_longer_word_containing_the_phrase_is_not_a_match(self) -> None:
        # "unverifiedness" and "preverified" are not the word either.
        for clause in ("preverified by an earlier gate", "unverifiedness"):
            with self.subTest(clause=clause):
                self.assertEqual(self._asserted(clause), [])

