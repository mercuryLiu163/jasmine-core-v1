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
import unittest
from pathlib import Path
from unittest import mock

from support import import_gate_module


_FAILED = import_gate_module().Failed


class AReceiptIsWrittenOnce(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

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
        import tempfile

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
        import tempfile

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
