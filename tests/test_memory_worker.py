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
               identity: bool = True,
               extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
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
        env.pop("JASMINE_MEMORY_DEPLOYMENT_MODE", None)
        if extra_env:
            env.update(extra_env)
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

    def _outcome_query(self) -> dict:
        query = self._query()
        query["types"] = ["VERIFIED_OUTCOME"]
        return query

    def _retain_outcome(self) -> tuple[dict, str, str]:
        """A `VERIFIED_OUTCOME` backed by real PASS Evidence and a real transition.

        The transition is written by P4's own state machine, so the item cites an
        Event that happened rather than one the worker inferred — which is the
        whole requirement, since P3 re-derives the proof from that Event.
        """
        _, evidence_id, transition_id = self.verified_step(
            project_id=self.project_id, task_id=self.task_id)
        self.scan()
        self.worker(transport=self.wire).run(max_ticks=6)
        backed = [item for item in self.store.items() if evidence_id in item["evidence_ids"]]
        self.assertEqual(len(backed), 1,
                         f"expected one Evidence-backed item, got "
                         f"{[(i['memory_type'], i['evidence_ids']) for i in self.store.items()]}")
        self.assertEqual(backed[0]["memory_type"], "VERIFIED_OUTCOME")
        return backed[0], evidence_id, transition_id

    def test_a_verified_outcome_names_the_transition_that_verified_it(self) -> None:
        item, evidence_id, transition_id = self._retain_outcome()
        completed = self._spawn(self._outcome_query())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)

        mapped = next((entry for entry in result["items"]
                       if evidence_id in entry["evidence_ids"]), None)
        self.assertIsNotNone(mapped, "the verified outcome was not offered to P3")
        self.assertEqual(set(mapped), ITEM_KEYS)
        self.assertEqual(mapped["memory_id"], item["memory_id"])
        self.assertEqual(mapped["memory_type"], "VERIFIED_OUTCOME")
        # P3 refuses the item as `unverified_outcome` unless it names the
        # transition, and refuses again unless that id is among the sources.
        self.assertEqual(mapped["verified_transition_event_id"], transition_id)
        self.assertIn(transition_id, mapped["source_event_ids"])
        row = self.conn.execute(
            "SELECT source_event_id, change_event_id FROM evidence WHERE evidence_id=?",
            (evidence_id,)).fetchone()
        self.assertLessEqual({row["source_event_id"], row["change_event_id"]},
                             set(mapped["source_event_ids"]))
        # ...and it must be a transition that really verified this Evidence.
        cited = json.loads(self.conn.execute(
            "SELECT payload_json FROM events WHERE event_id=?",
            (transition_id,)).fetchone()["payload_json"])
        self.assertEqual(cited["to"], "VERIFIED")
        self.assertEqual(cited["from"], "EXECUTED")
        self.assertEqual(cited["step_id"], self.conn.execute(
            "SELECT step_id FROM evidence WHERE evidence_id=?",
            (evidence_id,)).fetchone()["step_id"])
        self.assertIn(evidence_id, cited["result"]["evidence_ids"])
        self.assertEqual(mapped["provenance_digest"], _provenance_digest(mapped))

    def test_several_engine_facts_for_one_memory_become_one_item(self) -> None:
        """One document, several extracted facts, one P3 item.

        Real Hindsight 0.10.2 extracts *several* memory units from one document and
        every unit carries that document's `jasmine_memory_id`. P3 admits one item
        per `(bank_id, memory_id)` and refuses the whole envelope otherwise
        (`duplicate or undeclared Memory identity`), which the provider then reports
        as `memory_invalid_or_unavailable` -- so before this was handled, a healthy
        engine holding a genuine, locally re-verifiable memory produced a degraded
        Context Pack with no Memory in it at all. No repo test could say so: the
        wire double produced exactly one unit per document.
        """
        from jasmine_core.context.memory_provider import validate_result

        self.server.units_per_document = 3
        item, evidence_id = self._retain_lesson()

        # The engine really does hand back several facts for the one document, as
        # *distinct* text: units with equal text are de-duplicated upstream and the
        # identity collision would never reach the worker at all.
        self.assertEqual(self.server._unit_count(item["memory_id"]), 3,
                         "the wire double must really extract several units")
        texts = self.server._unit_texts(item["memory_id"])
        self.assertEqual(len(set(texts)), 3, f"unit text must differ: {texts}")
        completed = self._spawn(self._query())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)

        mapped = [entry for entry in result["items"] if evidence_id in entry["evidence_ids"]]
        self.assertEqual(len(mapped), 1,
                         f"one memory must reach P3 once, got {[e['memory_id'] for e in mapped]}")
        self.assertEqual(mapped[0]["memory_id"], item["memory_id"])
        identities = [(entry["bank"]["bank_id"], entry["memory_id"])
                      for entry in result["items"]]
        self.assertEqual(len(identities), len(set(identities)),
                         "P3 refuses a result that names one identity twice")
        # P3's own validator, not a restatement of its rule.
        validate_result(result)

    def test_a_pass_result_nothing_verified_is_not_claimed_as_an_outcome(self) -> None:
        """A PASS with no verifying transition is not a verified outcome.

        P4 classifies it as one, because that is the honest reading of the
        Evidence. What it must not do is *tell* P3 it was verified: the claim is
        only worth anything if something verified it, and shipping one to be
        filtered is indistinguishable downstream from shipping one believed.
        """
        evidence_id, _ = self.evidence(task_id=self.task_id, status="PASS",
                                       detail="suite passed")
        self.scan()
        self.worker(transport=self.wire).run(max_ticks=6)
        stored = [item for item in self.store.items() if evidence_id in item["evidence_ids"]]
        self.assertEqual(len(stored), 1, [(i["memory_type"], i["evidence_ids"])
                                          for i in self.store.items()])
        self.assertEqual(stored[0]["memory_type"], "VERIFIED_OUTCOME",
                         "P4 does read an unverified PASS as a verified outcome")
        self.assertIsNone(self.conn.execute(
            "SELECT step_id FROM evidence WHERE evidence_id=?",
            (evidence_id,)).fetchone()["step_id"],
            "this fixture is only meaningful with a step-less PASS")

        completed = self._spawn(self._outcome_query())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual([entry for entry in result["items"]
                          if evidence_id in entry["evidence_ids"]], [],
                         "a PASS nothing verified must not be claimed to P3")

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

    def test_the_request_may_not_name_the_actor(self) -> None:
        """The query is built from an HTTP body: a caller must not choose identity."""
        self._retain_lesson()
        query = self._query()
        query["actor_id"] = "act_" + "0" * 26
        result = json.loads(self._spawn(query).stdout)
        self.assertEqual(result["error_code"], "memory_actor_not_trusted",
                         "a caller-supplied actor was accepted")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["banks"][0]["status"], "degraded")

    def test_a_cli_deployment_mode_is_honoured_not_ignored(self) -> None:
        """`acl` with no grant for this actor reads nothing.

        The adapter's own default is `single_user`, so a worker that does not
        pass the deployment's mode answers with every project in the database —
        the mode exists to stop exactly that.
        """
        self._retain_lesson()
        other = self.project("other")
        # A populated table with somebody else's grant: the deployment is a
        # legitimate `acl` one, and the worker's actor still has no row.
        self.grant_project(other, actor_id=self.agent_actor)
        in_acl = json.loads(self._spawn(
            self._query(), extra_env={"JASMINE_MEMORY_DEPLOYMENT_MODE": "acl"}).stdout)
        self.assertEqual(in_acl["items"], [],
                         "an actor with no grant read this project's memory")
        self.assertEqual(in_acl["banks"][0]["status"], "degraded")

        # The grant is the same authority the rest of Core uses, so granting it
        # is all that changes.
        self.grant_project(self.project_id, actor_id=HUMAN)
        granted = json.loads(self._spawn(
            self._query(), extra_env={"JASMINE_MEMORY_DEPLOYMENT_MODE": "acl"}).stdout)
        self.assertTrue(granted["items"], granted)

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

    def test_a_retain_that_stored_nothing_is_not_a_completed_retain(self) -> None:
        """An accepted document with no memory unit in it is not a stored memory.

        Hindsight extracts with a model and reports the same `items_count` for a
        document it extracted nothing from as for one it extracted three facts from.
        A live deployment did exactly that, and because the job completed on the
        receipt alone the item was marked `RETAINED`, the job `DONE`, and nothing
        would ever be recalled from it -- with the one job that could have retried
        it closed. The receipt is not evidence; the unit listing is.
        """
        self.server.httpd.extract_nothing = True
        self._retain_lesson()

        job = self.job_store().jobs()[0]
        self.assertEqual(job["status"], "PENDING",
                         f"a retain that stored nothing must not be DONE: {job}")
        self.assertEqual(job["last_error_code"], "engine_extracted_no_units")
        item = self.store.items()[0]
        self.assertNotEqual(item["engine_state"], "RETAINED",
                            "an item with nothing in the engine is not retained")
        self.assertTrue(job["safe_error_summary"])

        # And the engine really is in that state, so the check is reading the
        # engine rather than believing the receipt.
        from jasmine_core.memory import scope as scope_module

        units = self.wire.list_all_document_memories(
            bank=scope_module.project_bank(self.project_id),
            document_id=item["document_id"])
        self.assertEqual(units, [])

    def test_a_bank_reports_its_own_failure_not_another_banks(self) -> None:
        """A bank that answered is not stamped with a neighbour's error code.

        The adapter always recalls `jasmine-global`, which does not exist on a
        project-only deployment, so that recall fails on every single call. The
        top-level `error_code` is therefore almost always set even when the bank
        P3 asked about answered with items. Falling back to it per bank reported a
        project bank that had just answered `ok` as `engine_not_found`, and a
        degraded Context Pack would then name a bank P3 never asked about as the
        one that failed.
        """
        self._retain_lesson()
        self.server.set_bank_failing("jasmine-global")
        try:
            result = json.loads(self._spawn(self._query()).stdout)
        finally:
            self.server.set_bank_failing("jasmine-global", failing=False)

        self.assertEqual(len(result["banks"]), 1)
        bank = result["banks"][0]
        self.assertEqual(bank["bank_id"], f"jasmine-project-{self.project_id}")
        self.assertEqual(bank["status"], "ok")
        self.assertIsNone(bank["error_code"],
                          "a bank that answered has no error of its own to report")
        self.assertTrue(result["items"], "the project bank's own items are still offered")

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


class TheDeploymentEnvironmentIsTheWholeInterface(unittest.TestCase):
    """P3 hands the child a fixed environment; the worker must work in it.

    `MemoryProvider` builds the child's environment from five fixed values, the
    two identity variables and whatever the operator configured — allowlisted to
    `PYTHONPATH`/`LANG`/`LC_ALL`. A worker that needs anything else is a worker
    that returns degraded in production while passing every test run from a
    shell.
    """

    #: Exactly what P3 constructs, before the operator's allowlisted fields.
    P3_CHILD_ENV = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONHASHSEED": "0",
                    "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    def setUp(self) -> None:
        from test_memory_hindsight import EndToEndOverTheWire

        self.fixture = EndToEndOverTheWire("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        evidence_id, _ = self.fixture.evidence(
            task_id=self.fixture.task_id, status="FAIL",
            detail="callback task_id did not match the task")
        self.evidence_id = evidence_id
        self.fixture.scan()
        self.fixture.worker(transport=self.fixture.wire).run(max_ticks=6)

    def _env(self, **extra: str) -> dict[str, str]:
        return {**self.P3_CHILD_ENV,
                "JASMINE_MEMORY_PROVIDER_ID": "p4-env-test",
                "JASMINE_MEMORY_CONFIG_DIGEST": "c" * 64, **extra}

    def _query(self) -> dict:
        project = self.fixture.project_id
        return {"project_id": project, "task_id": self.fixture.task_id,
                "query_text": "COMMAND_RESULT FAIL evidence", "query_sha256": "0" * 64,
                "types": ["FAILURE_LESSON"],
                "banks": [{"scope": "PROJECT", "bank_id": f"jasmine-project-{project}",
                           "project_id": project}],
                "max_items": 8, "max_tokens": 600}

    def _run(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(WORKER)],
                              input=json.dumps(self._query()).encode(),
                              capture_output=True, env=env, timeout=60)

    def test_it_answers_in_the_environment_p3_builds(self) -> None:
        completed = self._run(self._env(
            JASMINE_CORE_DB=str(self.fixture.db_path),
            JASMINE_MEMORY_ACTOR_ID=HUMAN,
            JASMINE_HINDSIGHT_URL=self.fixture.server.base_url))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["items"],
                        "the worker found nothing under P3's own environment shape")
        self.assertEqual(result["items"][0]["memory_id"],
                         next(i["memory_id"] for i in self.fixture.store.items()
                              if self.evidence_id in i["evidence_ids"]))

    def test_the_reporting_is_specific_when_the_environment_is_incomplete(self) -> None:
        """Each missing variable names itself, so the operator knows what to add."""
        without_db = self._run(self._env(JASMINE_MEMORY_ACTOR_ID=HUMAN))
        self.assertNotEqual(without_db.returncode, 0)
        self.assertIn(b"JASMINE_CORE_DB", without_db.stderr)

        without_actor = self._run(self._env(
            JASMINE_CORE_DB=str(self.fixture.db_path),
            JASMINE_HINDSIGHT_URL=self.fixture.server.base_url))
        self.assertEqual(without_actor.returncode, 0)
        result = json.loads(without_actor.stdout)
        self.assertEqual(result["error_code"], "memory_actor_not_supplied")
        self.assertEqual(result["items"], [])
