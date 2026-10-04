"""P4's Memory worker, driven exactly the way P3's provider drives it.

P3 owns the `jasmine.memory-slot.v1` contract: it spawns an operator-configured
`argv` as a subprocess, writes one JSON query to stdin, reads one JSON result
from stdout, and refuses the answer unless it satisfies a long list of shape
rules before anything reaches a Context Pack. P4 owns the worker.

So these tests do not call a function — they start a real process with the same
environment and the same request, and then check the answer against the rules
P3 enforces. A worker that only works in-process would pass a unit test and fail
in the provider.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from support import SRC

from memory_support import HUMAN
from test_memory_hindsight import EndToEndOverTheWire

from jasmine_core.canonical import sha256_hex

WORKER = SRC.parent / "scripts" / "p4-memory-worker.py"
#: P3 asks for at most this many, and refuses more.
MAX_ITEMS = 8
#: The exact key sets P3 validates. It rejects unknown keys, so these are not
#: "at least" — they are the whole object.
RESULT_KEYS = {"status", "provider_id", "provider_config_digest", "banks", "items",
               "query_trace_id", "error_code"}
BANK_KEYS = {"scope", "bank_id", "project_id", "status", "error_code"}
ITEM_KEYS = {"memory_id", "memory_type", "bank", "source_event_ids", "source_task_id",
             "origin_rule_ref", "evidence_ids", "backend_classification", "text",
             "confidence", "verified_transition_event_id", "recorded_at",
             "provenance_digest", "fact"}
FACT_KEYS = {"fact_type", "subject_event_id", "evidence_id", "key", "value"}


def _provenance_digest(item: dict) -> str:
    """P3's digest, recomputed the way P3 recomputes it from what it receives."""
    return sha256_hex({key: item[key] for key in (
        "memory_type", "bank", "source_event_ids", "source_task_id", "origin_rule_ref",
        "evidence_ids", "verified_transition_event_id", "fact")})


class P4sWorkerSpeaksP3sSlotProtocol(EndToEndOverTheWire):
    """A real subprocess, a real query, and P3's own acceptance rules."""

    PROVIDER_ID = "p4-worker-component"
    CONFIG_DIGEST = "b" * 64

    def _query(self, *, with_global: bool = False) -> dict:
        banks = [{"scope": "PROJECT",
                  "bank_id": f"jasmine-project-{self.project_id}",
                  "project_id": self.project_id}]
        if with_global:
            banks.insert(0, {"scope": "GLOBAL", "bank_id": "jasmine-global",
                             "project_id": None})
        # The evidence-backed lesson's text leads with its heading, and the
        # stub's recall is a substring double that truncates unit text at 200
        # characters — so this query is the one that reaches *that* document
        # rather than the transition-only lesson beside it. A real engine matches
        # semantically; the double has to be given something it can find.
        return {"project_id": self.project_id, "task_id": self.task_id,
                "query_text": "COMMAND_RESULT FAIL evidence", "query_sha256": "0" * 64,
                "types": ["CONFIRMED_DECISION", "FAILURE_LESSON"],
                "banks": banks, "max_items": MAX_ITEMS, "max_tokens": 600}

    def _spawn(self, payload: bytes | str, *, db: str | None = None,
               identity: bool = True) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["JASMINE_CORE_DB"] = db if db is not None else str(self.db_path)
        env["JASMINE_HINDSIGHT_URL"] = self.server.base_url
        # P3's query carries no acting identity, so the operator's environment
        # supplies one; the trace records it, so it has to be a real actor.
        env["JASMINE_MEMORY_ACTOR_ID"] = HUMAN
        env.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)
        if identity:
            env["JASMINE_MEMORY_PROVIDER_ID"] = self.PROVIDER_ID
            env["JASMINE_MEMORY_CONFIG_DIGEST"] = self.CONFIG_DIGEST
        else:
            env.pop("JASMINE_MEMORY_PROVIDER_ID", None)
            env.pop("JASMINE_MEMORY_CONFIG_DIGEST", None)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return subprocess.run([sys.executable, str(WORKER)], input=body,
                              capture_output=True, env=env, timeout=60)

    def _retain_lesson(self) -> tuple[dict, str]:
        """A FAILURE_LESSON backed by a real Evidence row.

        The base fixture only transitions a step, which produces a lesson with no
        Evidence behind it — and P3 will not accept a fact that does not name an
        Evidence row, correctly: a failure nobody recorded a result for is not a
        verified lesson. This is the Gate's own chain: fail the step, record the
        tool result, scan, retain both.
        """
        evidence_id, _ = self.evidence(task_id=self.task_id, status="FAIL",
                                       detail="callback task_id did not match the task")
        report = self.scan()
        self.assertGreaterEqual(len(report["created"]), 1, report)
        self.worker(transport=self.wire).run(max_ticks=6)
        backed = [item for item in self.store.items() if item["evidence_ids"]]
        self.assertEqual(len(backed), 1,
                         f"expected one Evidence-backed item, got "
                         f"{[(i['memory_type'], i['evidence_ids']) for i in self.store.items()]}")
        return backed[0], evidence_id

    # -- the contract ------------------------------------------------------

    def test_the_answer_is_the_shape_p3_validates(self) -> None:
        item, evidence_id = self._retain_lesson()
        completed = self._spawn(self._query())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)

        self.assertEqual(set(result), RESULT_KEYS, "P3 rejects unknown result keys")
        self.assertEqual(result["provider_id"], self.PROVIDER_ID)
        self.assertEqual(result["provider_config_digest"], self.CONFIG_DIGEST)
        self.assertIn(result["status"], ("ok", "empty", "degraded"))
        self.assertEqual([bank["scope"] for bank in result["banks"]], ["PROJECT"])
        self.assertEqual(set(result["banks"][0]), BANK_KEYS)
        self.assertEqual(result["banks"][0]["bank_id"], f"jasmine-project-{self.project_id}")
        self.assertIn(result["banks"][0]["status"], ("ok", "empty", "degraded"))
        self.assertLessEqual(len(result["items"]), MAX_ITEMS)
        if result["status"] == "empty":
            self.assertEqual(result["items"], [], "an empty result cannot carry items")
        if any(bank["status"] == "degraded" for bank in result["banks"]):
            self.assertEqual(result["status"], "degraded",
                             "a degraded bank must degrade the whole answer")

        self.assertTrue(result["items"], "the retained lesson was not offered to P3")
        mapped = next((entry for entry in result["items"]
                       if evidence_id in entry["evidence_ids"]), None)
        self.assertIsNotNone(mapped, "the Evidence-backed lesson was not offered")
        self.assertEqual(set(mapped), ITEM_KEYS)
        self.assertEqual(mapped["memory_id"], item["memory_id"])
        self.assertEqual(mapped["memory_type"], "FAILURE_LESSON")
        self.assertEqual(set(mapped["bank"]), {"scope", "bank_id", "project_id"})
        self.assertTrue(0.0 <= mapped["confidence"] <= 1.0)
        self.assertIsNone(mapped["verified_transition_event_id"],
                          "a FAILURE_LESSON may not claim VERIFIED qualification")
        self.assertEqual(mapped["provenance_digest"], _provenance_digest(mapped))

    def test_the_fact_points_at_a_real_evidence_row(self) -> None:
        """P3 re-derives the fact from the database; a plausible one is not enough."""
        _, evidence_id = self._retain_lesson()
        result = json.loads(self._spawn(self._query()).stdout)
        mapped = next(entry for entry in result["items"]
                      if evidence_id in entry["evidence_ids"])
        fact = mapped["fact"]
        self.assertEqual(set(fact), FACT_KEYS)
        self.assertEqual(fact["fact_type"], "EVIDENCE_RESULT")
        self.assertIn(fact["key"], ("kind", "status"))
        self.assertIn(fact["evidence_id"], mapped["evidence_ids"])

        row = self.conn.execute(
            "SELECT task_id, status, source_event_id, change_event_id FROM evidence"
            " WHERE evidence_id=?", (fact["evidence_id"],)).fetchone()
        self.assertIsNotNone(row, "the fact named Evidence this Core does not have")
        # The three equalities P3 checks before it will inject anything.
        self.assertEqual(fact["subject_event_id"], row["change_event_id"])
        self.assertEqual(fact["value"], row[fact["key"]])
        self.assertEqual(row["status"], "FAIL",
                         "only failure evidence reaches a FAILURE_LESSON")
        self.assertEqual(mapped["source_task_id"], row["task_id"])
        self.assertEqual(set(mapped["source_event_ids"]),
                         {row["source_event_id"], row["change_event_id"]},
                         "P3 requires both of the Evidence's own events")
        for source_id in mapped["source_event_ids"]:
            self.assertIsNotNone(self.conn.execute(
                "SELECT 1 FROM events WHERE event_id=?", (source_id,)).fetchone())
        self.assertTrue(0 < len(mapped["text"]) <= 2000)

    def test_a_query_with_the_global_bank_echoes_both(self) -> None:
        """P3 compares the declared banks to the requested ones exactly."""
        self._retain_lesson()
        result = json.loads(self._spawn(self._query(with_global=True)).stdout)
        self.assertEqual([bank["scope"] for bank in result["banks"]],
                         ["GLOBAL", "PROJECT"])
        self.assertEqual(result["banks"][0]["bank_id"], "jasmine-global")
        self.assertIsNone(result["banks"][0]["project_id"])

    def test_the_identity_is_echoed_from_the_environment(self) -> None:
        self._retain_lesson()
        result = json.loads(self._spawn(self._query()).stdout)
        self.assertEqual(result["provider_id"], self.PROVIDER_ID)
        self.assertEqual(result["provider_config_digest"], self.CONFIG_DIGEST)

    # -- refusals ----------------------------------------------------------

    def test_an_unreadable_request_is_a_dead_worker_not_a_fake_answer(self) -> None:
        for label, payload in (("not json", b"{not json"),
                               ("not an object", b"[1,2,3]"),
                               ("no banks", b'{"project_id":"x"}')):
            with self.subTest(label=label):
                completed = self._spawn(payload)
                self.assertNotEqual(completed.returncode, 0,
                                    "an unreadable request was answered")
                self.assertEqual(completed.stdout, b"")

    def test_a_missing_or_unknown_actor_degrades_rather_than_inventing_one(self) -> None:
        """The trace records who asked, so the worker must be told, not guess."""
        self._retain_lesson()
        env = dict(os.environ, JASMINE_CORE_DB=str(self.db_path),
                   JASMINE_HINDSIGHT_URL=self.server.base_url,
                   JASMINE_MEMORY_PROVIDER_ID=self.PROVIDER_ID,
                   JASMINE_MEMORY_CONFIG_DIGEST=self.CONFIG_DIGEST)
        env.pop("JASMINE_MEMORY_ACTOR_ID", None)
        env.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)
        for label, actor in (("missing", None), ("unknown", "act_" + "0" * 26)):
            with self.subTest(label=label):
                if actor is not None:
                    env["JASMINE_MEMORY_ACTOR_ID"] = actor
                completed = subprocess.run(
                    [sys.executable, str(WORKER)],
                    input=json.dumps(self._query()).encode(),
                    capture_output=True, env=env, timeout=60)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["status"], "degraded")
                self.assertEqual(result["items"], [])
                self.assertIn(result["error_code"],
                              ("memory_actor_not_supplied", "memory_actor_unknown"))
                self.assertEqual(result["banks"][0]["status"], "degraded")

    def test_no_identity_is_refused(self) -> None:
        self._retain_lesson()
        completed = self._spawn(self._query(), identity=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"",
                         "a worker without an identity still answered")

    def test_a_degraded_engine_still_answers_with_the_banks(self) -> None:
        """An engine that cannot be reached omits memory; it does not omit the answer."""
        self._retain_lesson()
        env_broken = self._spawn(self._query())
        self.assertEqual(env_broken.returncode, 0)
        self.server.set_unreachable(True)
        try:
            result = json.loads(self._spawn(self._query()).stdout)
        finally:
            self.server.set_unreachable(False)
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["items"], [])
        self.assertEqual(len(result["banks"]), 1)
        self.assertEqual(result["banks"][0]["status"], "degraded")
        self.assertTrue(result["banks"][0]["error_code"])


class TheWorkerBuildsItsOwnPathToTheCore(unittest.TestCase):
    """It is spawned by another process, from a directory it does not choose."""

    def test_it_can_be_run_from_anywhere(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(WORKER)], input=b"{not json}",
            capture_output=True, cwd="/", timeout=60)
        # It refused the request rather than failing to import Core.
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(b"ModuleNotFoundError", completed.stderr)
