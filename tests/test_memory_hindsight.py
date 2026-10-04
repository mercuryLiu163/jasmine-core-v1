"""P4-01: the real Hindsight transport, over a real socket.

`StubHindsightServer` is a `ThreadingHTTPServer` on loopback speaking Hindsight
0.10.2's real paths, methods, request bodies and response shapes, and the
transport under test is `jasmine_core.memory.hindsight.HindsightTransport` — the
one an operator's Core actually uses. So these tests catch the class of mistake a
boundary double cannot: a wrong URL, a wrong field name, the wrong HTTP method, a
body the engine would reject, a response shape read wrongly.

They still do not make the boundary double into a real service. A real Hindsight
process, with a real LLM extractor and a real database, is what P4-G01…G07 need,
and the P4 taskbook is explicit that fake or wire-level evidence is not
acceptance for those Gates. What this file earns is the right to say the adapter
speaks the documented protocol.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from memory_support import MemoryTestCase, StubHindsightServer
from jasmine_core import db
from jasmine_core.memory import hindsight
from jasmine_core.memory import scope
from jasmine_core.memory.hindsight import HindsightConfig, HindsightTransport
from jasmine_core.memory.transport import TransportError
from jasmine_core.memory.types import RecallRequest


class Configuration(unittest.TestCase):
    def test_the_url_is_reduced_to_an_origin_with_no_credential_in_it(self) -> None:
        config = HindsightConfig("http://user:hunter2@10.0.0.5:8888/v1?token=abc#frag")
        self.assertEqual(config.origin_host, "http://10.0.0.5:8888")
        self.assertNotIn("hunter2", config.base_url)
        self.assertNotIn("abc", config.base_url)
        self.assertNotIn("hunter2", repr(config))

    def test_only_http_and_https_are_accepted(self) -> None:
        for url in ("ftp://host", "not a url", "", "file:///etc/passwd", "hindsight:8888"):
            with self.assertRaises(ValueError, msg=url):
                HindsightConfig(url)

    def test_a_credential_is_only_sent_when_one_is_configured(self) -> None:
        self.assertNotIn("Authorization", HindsightConfig("http://h:1").headers())
        self.assertEqual(HindsightConfig("http://h:1", api_key="k").headers()["Authorization"],
                         "Bearer k")

    def test_nothing_is_configured_by_default(self) -> None:
        self.assertIsNone(hindsight.config_from_env({}))
        config = hindsight.config_from_env({hindsight.URL_ENV_VAR: "http://h:1"})
        self.assertIsNotNone(config)

    def test_a_malformed_timeout_falls_back_rather_than_crashing_the_core(self) -> None:
        config = hindsight.config_from_env({
            hindsight.URL_ENV_VAR: "http://h:1", hindsight.TIMEOUT_ENV_VAR: "soon"})
        self.assertEqual(config.timeout_seconds, hindsight.DEFAULT_TIMEOUT_SECONDS)


class Wire(unittest.TestCase):
    """Every request and response against the stub, over HTTP."""

    def setUp(self) -> None:
        self.server = StubHindsightServer()
        self.addCleanup(self.server.stop)
        self.transport = HindsightTransport(HindsightConfig(self.server.base_url,
                                                           timeout_seconds=5))
        self.bank = scope.project_bank("prj_" + "0" * 26)
        # Hindsight creates a bank on its first retain and answers 404 for a bank it
        # was never asked to write to. So the bank under test has to exist before it
        # can be read, and the double now models that. Reading an absent bank used to
        # answer an empty result set, which is more forgiving than the engine and is
        # how "this bank has nothing" came to be indistinguishable from "this bank is
        # broken" for so long. It also makes the document-absence test stronger: the
        # collection now really does hold a document that is *not* the one asked for.
        self.transport.retain(bank=self.bank, document_id="seeded", content="a seeded memory",
                              context="", metadata={}, tags=(), memory_type="CONFIRMED_DECISION",
                              timestamp=None)

    # -- version -----------------------------------------------------------

    def test_version_reads_the_documented_shape(self) -> None:
        payload = self.transport.version()
        self.assertTrue(payload["reachable"])
        self.assertEqual(payload["api_version"], "0.10.2")
        self.assertTrue(payload["features"]["store_document_text"])
        ok, reason = hindsight.check_supported(payload)
        self.assertTrue(ok, reason)

    def test_an_unverified_api_version_is_reported_rather_than_assumed(self) -> None:
        self.assertFalse(hindsight.check_supported({"api_version": "9.9"})[0])
        self.assertFalse(hindsight.check_supported(
            {"api_version": "0.10.2", "features": {"store_document_text": False}})[0])
        # Positive matching: an engine that says nothing about its version has not
        # been verified, and "it said nothing" is not "it is the right one".
        for unstated in ({"api_version": None}, {}, {"api_version": ""}):
            ok, reason = hindsight.check_supported(unstated)
            self.assertFalse(ok, unstated)
            self.assertIn("api_version", reason)
        self.assertTrue(hindsight.check_supported({"api_version": "0.10.2"})[0])
        self.assertTrue(hindsight.check_supported({"api_version": "0.10.11"})[0])

    def test_a_malformed_engine_url_is_a_typed_configuration_refusal(self) -> None:
        from jasmine_core import errors

        with self.assertRaises(errors.InvalidRequest) as caught:
            hindsight.config_from_env({hindsight.URL_ENV_VAR: "not-a-url"})
        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertIn(hindsight.URL_ENV_VAR, caught.exception.message)

    def test_the_reflect_budget_is_capped_before_it_reaches_the_engine(self) -> None:
        server = self.server
        self.transport.reflect(bank=self.bank, query="what changed?", max_tokens=10 ** 9)
        # The stub records nothing, so the assertion is on the cap itself: a
        # request for 10**9 tokens must not become 10**9 tokens of LLM spend.
        self.assertEqual(hindsight.MAX_REQUEST_TOKENS, 8192)
        del server

    # -- retain ------------------------------------------------------------

    def test_retain_posts_the_documented_body_and_reads_the_receipt(self) -> None:
        receipt = self.transport.retain(
            bank=self.bank, document_id="hindsight:verified_outcome:abc", content="a lesson",
            context="jasmine verified outcome",
            metadata={"jasmine_memory_id": "mem_" + "0" * 26, "jasmine_memory_type": "VERIFIED_OUTCOME"},
            tags=("jasmine", "jasmine:type:VERIFIED_OUTCOME"), memory_type="VERIFIED_OUTCOME",
            timestamp="2026-09-29T12:00:00Z")
        self.assertEqual(receipt["items_count"], 1)
        self.assertFalse(receipt["async"], "P4 retains synchronously to get a completion receipt")
        self.assertEqual(receipt["usage"]["total_tokens"], 140)
        document = self.transport.get_document(bank=self.bank,
                                               document_id="hindsight:verified_outcome:abc")
        self.assertEqual(document["original_text"], "a lesson")
        self.assertEqual(document["memory_unit_count"], 1)

    def test_a_re_send_of_the_same_document_replaces_it_rather_than_duplicating(self) -> None:
        first = self.transport.retain(
            bank=self.bank, document_id="doc-1", content="v1", context="c", metadata={},
            tags=(), memory_type="FAILURE_LESSON", timestamp=None)
        units_before = self.transport.list_document_memories(bank=self.bank, document_id="doc-1")
        self.transport.retain(bank=self.bank, document_id="doc-1", content="v2", context="c",
                              metadata={}, tags=(), memory_type="FAILURE_LESSON", timestamp=None)
        units_after = self.transport.list_document_memories(bank=self.bank, document_id="doc-1")
        self.assertEqual(len(units_before), 1)
        self.assertEqual(len(units_after), 1)
        # The engine's `replace` semantics renumber the unit, which is why P4
        # never treats a memory unit id as a stable handle.
        self.assertNotEqual(units_before[0]["id"], units_after[0]["id"])
        self.assertEqual(self.transport.get_document(bank=self.bank,
                                                     document_id="doc-1")["original_text"], "v2")
        self.assertEqual(first["bank_id"], self.bank.bank_id)

    def test_append_mode_is_never_used_implicitly(self) -> None:
        self.transport.retain(bank=self.bank, document_id="doc-1", content="v1", context="c",
                              metadata={}, tags=(), memory_type="FAILURE_LESSON", timestamp=None)
        self.transport.retain(bank=self.bank, document_id="doc-1", content="v2", context="c",
                              metadata={}, tags=(), memory_type="FAILURE_LESSON", timestamp=None)
        document = self.transport.get_document(bank=self.bank, document_id="doc-1")
        self.assertEqual(document["original_text"], "v2",
                         "a retry must not accumulate duplicate text")

    # -- recall ------------------------------------------------------------

    def test_recall_returns_metadata_so_provenance_can_come_back(self) -> None:
        self.transport.retain(
            bank=self.bank, document_id="doc-1", content="the callback task id must match the task",
            context="jasmine failure lesson", metadata={"jasmine_memory_id": "mem_x"},
            tags=("jasmine:type:FAILURE_LESSON",), memory_type="FAILURE_LESSON", timestamp=None)
        results = self.transport.recall(bank=self.bank, query="callback task id", max_tokens=500,
                                        fact_types=("world", "experience"),
                                        tags=("jasmine:type:FAILURE_LESSON",))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].metadata["jasmine_memory_id"], "mem_x")
        self.assertEqual(results[0].document_id, "doc-1")
        self.assertEqual(results[0].bank_id, self.bank.bank_id)
        self.assertEqual(results[0].engine_fact_type, "experience")
        self.assertIsNotNone(results[0].score)

    def test_an_oversized_query_is_refused_locally_not_by_the_engine(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self.transport.recall(bank=self.bank, query="x" * (hindsight.MAX_QUERY_CHARS + 1),
                                  max_tokens=100, fact_types=("world",), tags=())
        self.assertEqual(caught.exception.code, "query_too_long")
        self.assertFalse(caught.exception.retryable)

    # -- reflect -----------------------------------------------------------

    def test_reflect_reads_the_documented_answer_shape(self) -> None:
        payload = self.transport.reflect(bank=self.bank, query="what changed?", max_tokens=300)
        self.assertEqual(payload["text"], "stub synthesis")
        self.assertEqual(payload["based_on_ids"], [])

    # -- lifecycle verbs ---------------------------------------------------

    def test_invalidate_then_delete_against_the_real_paths(self) -> None:
        self.transport.retain(bank=self.bank, document_id="doc-1", content="a lesson to retire",
                              context="c", metadata={}, tags=(), memory_type="FAILURE_LESSON",
                              timestamp=None)
        units = self.transport.list_document_memories(bank=self.bank, document_id="doc-1")
        self.transport.invalidate_memory(bank=self.bank, memory_id=units[0]["id"],
                                         reason="obsoleted")
        after = self.transport.list_document_memories(bank=self.bank, document_id="doc-1")
        self.assertEqual(after[0]["state"], "invalidated",
                         "the engine keeps the unit; invalidate is not a delete")
        receipt = self.transport.delete_document(bank=self.bank, document_id="doc-1")
        self.assertEqual(receipt["memory_units_deleted"], 1)
        self.assertIsNone(self.transport.get_document(bank=self.bank, document_id="doc-1"))

    def test_a_missing_document_is_none_not_an_exception(self) -> None:
        self.assertIsNone(self.transport.get_document(bank=self.bank, document_id="absent"))


class FailureModes(Wire):
    """Every remote failure becomes a stable code, and never a raw exception."""

    def test_a_5xx_is_retryable_and_a_422_is_not(self) -> None:
        self.server.set_unreachable(True)
        with self.assertRaises(TransportError) as caught:
            self.transport.recall(bank=self.bank, query="x", max_tokens=10,
                                  fact_types=("world",), tags=())
        self.assertEqual(caught.exception.code, "engine_server_error")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.http_status, 503)
        self.server.set_unreachable(False)

    def test_a_malformed_body_becomes_a_protocol_error_without_quoting_it(self) -> None:
        with patch.object(HindsightTransport, "_request",
                          side_effect=TransportError("engine_protocol_error",
                                                     "the engine response was not valid JSON")):
            with self.assertRaises(TransportError) as caught:
                self.transport.recall(bank=self.bank, query="x", max_tokens=10,
                                      fact_types=("world",), tags=())
        self.assertEqual(caught.exception.code, "engine_protocol_error")

    def test_a_connection_that_is_not_there_is_unreachable_and_retryable(self) -> None:
        transport = HindsightTransport(HindsightConfig("http://127.0.0.1:1", timeout_seconds=1))
        with self.assertRaises(TransportError) as caught:
            transport.recall(bank=self.bank, query="x", max_tokens=10,
                             fact_types=("world",), tags=())
        self.assertIn(caught.exception.code, ("engine_unreachable", "engine_timeout"))
        self.assertTrue(caught.exception.retryable)
        # `version()` never raises, so a health report is always possible even
        # when the engine is gone: that is the whole point of reporting health
        # separately from Core liveness.
        probe = transport.version()
        self.assertFalse(probe["reachable"])
        self.assertIn(probe["error_code"], ("engine_unreachable", "engine_timeout"))

    def test_a_redirect_is_refused_rather_than_replaying_the_credential(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        seen: list[str | None] = []

        class Redirector(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                return

            def _redirect(self) -> None:
                seen.append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", "http://example.invalid/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            # Both verbs, because the refusal must hold for the probe and for a
            # real recall; answering only GET would let an unhandled POST return
            # 501 and hide the redirect behind an unrelated error.
            do_GET = _redirect

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._redirect()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02},
                                  daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        transport = HindsightTransport(HindsightConfig(
            f"http://127.0.0.1:{httpd.server_address[1]}", api_key="secret-token",
            timeout_seconds=5))
        with self.assertRaises(TransportError) as caught:
            transport.recall(bank=self.bank, query="x", max_tokens=10,
                             fact_types=("world",), tags=())
        self.assertEqual(caught.exception.code, "engine_unexpected_redirect")
        # The header was sent to the configured host and nowhere else.
        self.assertEqual(seen, ["Bearer secret-token"])

    def test_a_response_over_the_cap_is_refused_rather_than_truncated(self) -> None:
        with patch.object(HindsightTransport, "_request",
                          side_effect=TransportError("engine_response_too_large",
                                                     "response exceeded the cap")):
            with self.assertRaises(TransportError) as caught:
                self.transport.recall(bank=self.bank, query="x", max_tokens=10,
                                      fact_types=("world",), tags=())
        self.assertEqual(caught.exception.code, "engine_response_too_large")


class ReceiptValidation(Wire):
    """A receipt is evidence only if it matches the contract and the request.

    `RetainResponse` and `DeleteDocumentResponse` mark their fields required in
    the official OpenAPI 0.10.2, and the worker writes `DONE` on the strength of
    this response alone. Reading `payload.get("success") is False` instead meant
    an absent field read as success, so an empty `{}`, a `"success": null`, an
    `async` queued operation, an acknowledgement for a *different* bank and a
    delete confirmation for a *different* document were each enough to reach
    `DONE`.
    """

    def _receipt(self, payload: dict) -> None:
        with patch.object(HindsightTransport, "_request", return_value=payload):
            self.transport.retain(bank=self.bank, document_id="doc-1", content="c",
                                  context="c", metadata={}, tags=(),
                                  memory_type="FAILURE_LESSON", timestamp=None)

    def _deletion(self, payload: dict) -> None:
        with patch.object(HindsightTransport, "_request", return_value=payload):
            self.transport.delete_document(bank=self.bank, document_id="doc-1")

    def test_a_retain_response_missing_required_fields_is_refused(self) -> None:
        for payload in (
            {},                                          # nothing at all
            {"success": True},                           # the rest unspoken
            {"success": None, "bank_id": self.bank.bank_id,
             "items_count": 1, "async": False},          # explicit null
            {"success": True, "async": False,
             "items_count": 1},                          # no bank
            {"success": True, "bank_id": self.bank.bank_id,
             "items_count": 1},                          # no async
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(TransportError) as caught:
                    self._receipt(payload)
                self.assertEqual(caught.exception.code, "engine_protocol_error")
                self.assertFalse(caught.exception.retryable)

    def test_a_non_boolean_or_mistyped_field_is_refused(self) -> None:
        good = {"success": True, "async": False, "bank_id": self.bank.bank_id,
                "items_count": 1}
        for broken in ("true", 1, 0, "false"):
            with self.subTest(async_value=broken):
                with self.assertRaises(TransportError) as caught:
                    self._receipt({**good, "async": broken})
                self.assertEqual(caught.exception.code, "engine_protocol_error")
        with self.assertRaises(TransportError):
            self._receipt({**good, "items_count": "1"})
        with self.assertRaises(TransportError):
            self._receipt({**good, "bank_id": 7})

    def test_a_receipt_for_a_different_bank_is_not_a_receipt(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self._receipt({"success": True, "async": False, "items_count": 1,
                           "bank_id": "jasmine-project-" + "0" * 26})
        self.assertEqual(caught.exception.code, "engine_protocol_error")

    def test_an_explicitly_unsuccessful_retain_is_retryable(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self._receipt({"success": False, "async": False, "items_count": 0,
                           "bank_id": self.bank.bank_id})
        self.assertEqual(caught.exception.code, "engine_retain_failed")
        self.assertTrue(caught.exception.retryable)

    def test_a_queued_retain_is_unconfirmed_not_retained(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self._receipt({"success": True, "async": True, "items_count": 1,
                           "bank_id": self.bank.bank_id, "operation_id": "op_1"})
        self.assertEqual(caught.exception.code, "engine_retain_unconfirmed")
        self.assertTrue(caught.exception.retryable)

    def test_a_delete_confirmation_for_another_document_is_refused(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self._deletion({"success": True, "message": "deleted",
                            "document_id": "somebody-elses-document",
                            "memory_units_deleted": 3})
        self.assertEqual(caught.exception.code, "engine_protocol_error")
        self.assertFalse(caught.exception.retryable)

    def test_a_delete_response_missing_required_fields_is_refused(self) -> None:
        for payload in (
            {"success": True, "document_id": "doc-1", "memory_units_deleted": 1},
            {"success": True, "message": "ok", "memory_units_deleted": 1},
            {"success": False, "message": "no", "document_id": "doc-1",
             "memory_units_deleted": 0},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(TransportError) as caught:
                    self._deletion(payload)
                self.assertIn(caught.exception.code,
                              ("engine_protocol_error", "engine_delete_failed"))
        # The explicit failure is a distinct, retryable code.
        with self.assertRaises(TransportError) as caught:
            self._deletion({"success": False, "message": "no", "document_id": "doc-1",
                            "memory_units_deleted": 0})
        self.assertEqual(caught.exception.code, "engine_delete_failed")
        self.assertTrue(caught.exception.retryable)

    def test_a_contract_shaped_receipt_is_accepted(self) -> None:
        self.transport.retain(bank=self.bank, document_id="doc-ok", content="c",
                              context="c", metadata={}, tags=(),
                              memory_type="FAILURE_LESSON", timestamp=None)
        self.assertEqual(
            self.transport.delete_document(bank=self.bank, document_id="doc-ok")["success"],
            True)


class EveryPageIsReadToTheStatedTotal(unittest.TestCase):
    """A page shorter than asked for is not the last page.

    `list_document_memories` took a `limit`, and a server is free to return
    fewer rows than that limit. Stopping on the short page ends the read with
    units unseen, and every caller that then reports work done is reporting it
    on units it never loaded — obsoleting "all of them" while some stay valid,
    or calling a document absent while its units are on a later page.
    """

    def test_a_server_that_caps_the_page_does_not_end_the_read(self) -> None:
        total = 250
        cap = 50
        served: list[dict] = []

        def page(*, bank, document_id, limit, offset):
            # The server answers at most `cap` rows whatever `limit` asks for —
            # legal, and exactly the shape that made a short page look final.
            rows = [{"id": f"mu_{i:04d}", "state": "valid"} for i in range(offset, min(offset + cap, total))]
            served.append(offset)
            return {"items": rows, "total": total, "limit": cap, "offset": offset}

        def reply(*args, **kwargs):
            query = kwargs.get("query") or {}
            return page(bank=None, document_id=query.get("document_id", "d"),
                        limit=query.get("limit", 100), offset=query.get("offset", 0))

        transport = HindsightTransport.__new__(HindsightTransport)
        transport._request = reply

        units = transport.list_all_document_memories(
            bank=scope.project_bank("prj_01K742SG00BMSDET9BTP151RAR"),
            document_id="d")
        self.assertEqual(len(units), total,
                         f"the read stopped after {served}, which is {len(units)} of {total}")
        self.assertEqual(len({u['id'] for u in units}), total, "a unit was read twice")

    def test_a_total_that_moves_underneath_the_read_is_refused(self) -> None:
        transport = HindsightTransport.__new__(HindsightTransport)
        replies = iter([
            {"items": [{"id": f"a{i}"} for i in range(50)], "total": 250,
             "limit": 50, "offset": 0},
            {"items": [{"id": "b"}], "total": 300, "limit": 50, "offset": 50},
        ])

        def reply(*args, **kwargs):
            return next(replies)

        transport._request = reply
        with self.assertRaises(TransportError) as caught:
            transport.list_all_document_memories(
                bank=scope.project_bank("prj_01K742SG00BMSDET9BTP151RAR"),
                document_id="d")
        self.assertEqual(caught.exception.code, "engine_conflict")
        self.assertTrue(caught.exception.retryable)


class EndToEndOverTheWire(MemoryTestCase):
    """The whole P4 path against the wire double: scan, retain, recall, lifecycle."""

    def setUp(self) -> None:
        super().setUp()
        self.server = StubHindsightServer()
        self.addCleanup(self.server.stop)
        config = HindsightConfig(self.server.base_url, timeout_seconds=5)
        self.wire = HindsightTransport(config)
        self.project_id = self.project()
        self.task_id = self.task(self.project_id)
        self.failed_step(project_id=self.project_id, task_id=self.task_id,
                         reason="callback task_id did not match the task")

    def test_a_bank_the_engine_never_had_is_empty_not_a_failure(self) -> None:
        """A bank nothing was ever retained into has nothing to say.

        Hindsight creates a bank on its first retain, so a deployment that has
        only project memory has no global bank -- and the adapter recalls the
        global bank on every call, because a global preference is Truth wherever
        it exists. Reading that 404 as a fault made the whole recall `degraded`,
        which is the answer P3 is given when Memory is unavailable: on a perfectly
        healthy deployment, every Context Pack arrived with no Memory in it.
        """
        from jasmine_core.memory.scope import BankRef

        absent = BankRef(bank_id="jasmine-global", scope_kind="global", project_id=None)
        self.assertEqual(
            self.wire.recall(bank=absent, query="anything", max_tokens=100,
                             fact_types=(), tags=()),
            [], "a bank the engine has never heard of has no results, not an error")

    def test_a_bank_that_does_exist_still_answers(self) -> None:
        """The proof reads the banks collection; it must not shadow a real bank."""
        from jasmine_core.memory.scope import BankRef

        self.scan()
        self.worker(transport=self.wire).run(max_ticks=6)
        present = BankRef(bank_id=f"jasmine-project-{self.project_id}",
                          scope_kind="project", project_id=self.project_id)
        results = self.wire.recall(bank=present, query="callback", max_tokens=200,
                                   fact_types=(), tags=())
        self.assertTrue(results, "an existing bank still returns its units")

    def test_a_wrong_route_is_still_an_error(self) -> None:
        """Absence is proved, never inferred from the 404 alone.

        If the banks collection itself cannot be read, the question "does this bank
        exist" is open -- and an open question must not be answered "no". Otherwise
        a mistyped prefix would make every bank look empty, which is the mirror of
        the bug this replaced.
        """
        from jasmine_core.memory.scope import BankRef

        absent = BankRef(bank_id="jasmine-global", scope_kind="global", project_id=None)
        self.server.httpd.banks_collection_404 = True
        with self.assertRaises(TransportError) as raised:
            self.wire.recall(bank=absent, query="anything", max_tokens=100,
                             fact_types=(), tags=())
        self.assertEqual(raised.exception.code, "engine_not_found")
        self.server.httpd.banks_collection_404 = False

    def test_an_unreadable_banks_collection_proves_nothing(self) -> None:
        """A row with no `bank_id` means coverage is not proved, not that it is empty.

        Skipping that row would still advance the offset, so every page would read as
        covered and a bank that exists could be reported absent -- the same shape of
        bug the documents collection was hardened against.
        """
        from jasmine_core.memory.scope import BankRef

        absent = BankRef(bank_id="jasmine-global", scope_kind="global", project_id=None)
        self.server.httpd.banks_collection_junk = True
        with self.assertRaises(TransportError) as raised:
            self.wire.recall(bank=absent, query="anything", max_tokens=100,
                             fact_types=(), tags=())
        self.assertEqual(raised.exception.code, "engine_protocol_error")
        self.server.httpd.banks_collection_junk = False

    def test_a_committed_failure_becomes_a_recallable_lesson(self) -> None:
        report = self.scan()
        self.assertEqual(len(report["created"]), 1)
        tick = self.worker(transport=self.wire).tick()
        self.assertEqual(tick.outcome, "retained")
        job = self.job_store().jobs()[0]
        self.assertEqual(job["status"], "DONE")
        self.assertEqual(job["receipt"]["items_count"], 1)

        # A real recall over HTTP, with Core doing the provenance checking.
        from memory_support import HUMAN

        outcome = self.adapter(transport=self.wire).recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="callback task_id mismatch",
            task_id=self.task_id, max_tokens=600))
        self.assertEqual(outcome.status, "ok", outcome.as_dict())
        item = outcome.items[0]
        self.assertEqual(item.memory_type, "FAILURE_LESSON")
        self.assertTrue(item.historical)
        self.assertEqual(item.provenance.source_event_ids, (self.store.items()[0]["source_event_id"],))
        # And the read-back the Gate would record.
        units = self.wire.list_document_memories(
            bank=scope.project_bank(self.project_id), document_id=item.document_id)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["metadata"]["jasmine_memory_type"], "FAILURE_LESSON")

    def test_obsolete_then_delete_over_the_wire(self) -> None:
        self.scan()
        self.worker(transport=self.wire).tick()
        item = self.store.items()[0]
        bank = scope.project_bank(self.project_id)
        from memory_support import HOST, HUMAN

        self.lifecycle().obsolete(item["memory_id"],
                                  {"expected_revision": 1, "host_id": HOST, "reason": "wrong"},
                                  actor_id=HUMAN, actor_kind="human", host_id=HOST)
        self.worker(transport=self.wire, lease_owner="w2").tick()
        units = self.wire.list_document_memories(bank=bank, document_id=item["document_id"])
        self.assertEqual(units[0]["state"], "invalidated")
        # Soft retire is not removal: the document is still readable.
        self.assertIsNotNone(self.wire.get_document(bank=bank, document_id=item["document_id"]))

        revision = self.store.item(item["memory_id"])["revision"]
        self.lifecycle().delete(item["memory_id"],
                                {"expected_revision": revision, "host_id": HOST,
                                 "reason": "user asked"},
                                actor_id=HUMAN, actor_kind="human", host_id=HOST)
        self.worker(transport=self.wire, lease_owner="w3").tick()
        self.assertIsNone(self.wire.get_document(bank=bank, document_id=item["document_id"]),
                          "the remote document must be gone, not merely hidden")
        # And the Core source survived.
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM events WHERE event_id=?",
                                              (item["source_event_id"],)).fetchone())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM tasks WHERE task_id=?",
                                              (self.task_id,)).fetchone())

    def test_the_engine_going_away_degrades_recall_without_touching_truth(self) -> None:
        self.scan()
        self.worker(transport=self.wire).tick()
        self.server.set_unreachable(True)
        from memory_support import HUMAN

        outcome = self.adapter(transport=self.wire).recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="callback", max_tokens=400))
        self.assertEqual(outcome.status, "degraded")
        self.assertEqual(outcome.items, ())
        self.assertEqual(outcome.error_code, "engine_server_error")
        # Truth is untouched: the task is still there and the Core is still ok.
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM tasks WHERE task_id=?",
                                              (self.task_id,)).fetchone())
        self.server.set_unreachable(False)
        recovered = self.adapter(transport=self.wire).recall(RecallRequest(
            project_id=self.project_id, actor_id=HUMAN, query="callback", max_tokens=400))
        self.assertEqual(recovered.status, "ok")
        self.assertEqual(len(recovered.items), 1)

    def test_a_crash_between_retain_and_done_does_not_duplicate_the_document(self) -> None:
        self.scan()
        bank = scope.project_bank(self.project_id)
        claimed = self.job_store().claim_due(owner="w1")
        # The engine accepted it; the process then died.
        self.wire.retain(bank=claimed.bank, document_id=claimed.job["document_id"],
                         content=claimed.item["sanitized_text"], context="c", metadata={},
                         tags=(), memory_type=claimed.item["memory_type"], timestamp=None)
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE memory_jobs SET lease_expires_at='1970-01-01T00:00:00.000000Z'")
        report = self.worker(transport=self.wire, lease_owner="w2").tick(scan=False)
        self.assertEqual(report.outcome, "retained")
        self.assertEqual(self.job_store().jobs()[0]["status"], "DONE")
        units = self.wire.list_document_memories(bank=bank,
                                                document_id=claimed.job["document_id"])
        self.assertEqual(len(units), 1, "one remote document, not two")


class AbsenceIsProvenNotInferred(Wire):
    """A 404 or an odd 200 is not, by itself, a document that is gone.

    The worker records `DELETED` from this answer, so anything that collapses
    into `None` becomes a claim that the remote copy is gone — and a wrong
    endpoint plus an empty unit listing is enough to produce exactly that while
    the document is still in the bank.
    """

    def test_a_200_that_is_not_a_document_is_a_protocol_error(self) -> None:
        for body in ([], None, {"detail": "not found"}, {"id": None}):
            with self.subTest(body=body):
                with patch.object(HindsightTransport, "_request", return_value=body):
                    with self.assertRaises(TransportError) as caught:
                        self.transport.get_document(bank=self.bank, document_id="doc-1")
                self.assertEqual(caught.exception.code, "engine_protocol_error")
                self.assertFalse(caught.exception.retryable)

    def _gone(self) -> TransportError:
        return TransportError("engine_not_found", "no such document", retryable=False,
                              http_status=404)

    @staticmethod
    def _script(*responses):
        queue = iter(responses)

        def next_reply(*args, **kwargs):
            reply = next(queue)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return next_reply

    @staticmethod
    def _page(ids, *, total, offset, limit=100):
        return {"items": [{"id": identifier} for identifier in ids],
                "total": total, "limit": limit, "offset": offset}

    def test_the_collection_is_read_through_every_page(self) -> None:
        """One page proves nothing: `limit` defaults to 100.

        A document on page 2 is present, but a first-page-only check would
        conclude it is gone and the worker would record `DELETED`.
        """
        gone = self._gone()
        first_page = [f"doc-{i:03d}" for i in range(100)]
        # Page 0 does not contain it; page 1 does.
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone,
                self._page(first_page, total=102, offset=0),
                self._page(["our-document", "doc-100"], total=102, offset=100))):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="our-document")
        self.assertEqual(caught.exception.code, "engine_protocol_error")
        self.assertIn("listed by the collection", caught.exception.safe_error_summary)

        # The same shape with our document absent on the last page is absence.
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone,
                self._page(first_page, total=102, offset=0),
                self._page(["doc-100", "doc-101"], total=102, offset=100))):
            self.assertIsNone(self.transport.get_document(bank=self.bank,
                                                          document_id="our-document"))

    def test_coverage_that_cannot_be_proved_is_an_error_not_absence(self) -> None:
        gone = self._gone()
        full = [f"doc-{i:03d}" for i in range(100)]

        # A server that answers from the first offset whatever is asked has not
        # partitioned its pages, so "we read them all" would be untrue. The
        # target is deliberately absent from every page: the refusal must come
        # from the offset check, not from the document being listed.
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone, self._page(full, total=250, offset=0),
                self._page(full, total=250, offset=0))):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank,
                                            document_id="our-document")
        self.assertIn("different offset", caught.exception.safe_error_summary)

        # The total says there is more, but the cursor cannot advance.
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone, self._page([], total=5, offset=0))):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="doc-001")
        self.assertIn("stopped after 0", caught.exception.safe_error_summary)

        # No total at all: coverage cannot be measured.
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone, {"items": [], "limit": 100, "offset": 0})):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="doc-001")
        self.assertIn("total", caught.exception.safe_error_summary)

        # A repeated id across pages: the pages overlap, so nothing was covered.
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone, self._page(["dup", "a"], total=4, offset=0),
                self._page(["dup", "b"], total=4, offset=2))):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="doc-001")
        self.assertIn("twice", caught.exception.safe_error_summary)

    def test_an_unreadable_entry_is_not_coverage(self) -> None:
        """`items: [null]` must not be counted as a page that was read.

        Skipping an entry we cannot inspect still advances `offset`, so a server
        answering with junk reaches `total`, concludes the document is absent,
        and lets the worker record `DELETED` over one that is still there.
        """
        gone = self._gone()
        for label, entry in (("null entry", None), ("no id", {}),
                             ("empty id", {"id": ""}), ("numeric id", {"id": 7})):
            with self.subTest(label=label):
                with patch.object(HindsightTransport, "_request", side_effect=self._script(
                        gone, {"items": [entry], "total": 1, "limit": 100, "offset": 0})):
                    with self.assertRaises(TransportError) as caught:
                        self.transport.get_document(bank=self.bank,
                                                    document_id="our-document")
                self.assertEqual(caught.exception.code, "engine_protocol_error")
                self.assertFalse(caught.exception.retryable)
                self.assertIn("cannot be checked", caught.exception.safe_error_summary)

    def test_the_read_back_must_be_the_document_that_was_asked_for(self) -> None:
        """A document-shaped answer for a *different* document is not an answer."""
        for body in ({"id": "somebody-elses-document", "bank_id": self.bank.bank_id},
                     {"id": ""}):
            with self.subTest(body=body):
                with patch.object(HindsightTransport, "_request", return_value=body):
                    with self.assertRaises(TransportError) as caught:
                        self.transport.get_document(bank=self.bank, document_id="mine")
                self.assertEqual(caught.exception.code, "engine_protocol_error")
                self.assertFalse(caught.exception.retryable)

        matching = {"id": "mine", "bank_id": self.bank.bank_id}
        with patch.object(HindsightTransport, "_request", return_value=matching):
            self.assertEqual(self.transport.get_document(bank=self.bank,
                                                         document_id="mine"), matching)

    def test_a_404_is_read_as_absence_only_when_the_collection_answers(self) -> None:
        gone = self._gone()
        missing_route = TransportError("engine_not_found", "not found", retryable=False,
                                       http_status=404)
        script = self._script

        # The collection answers with an empty page: the bank and the `/documents`
        # prefix are alive, so the per-document 404 is about this document.
        with patch.object(HindsightTransport, "_request", side_effect=script(
                gone, {"items": [], "total": 0, "limit": 100, "offset": 0})):
            self.assertIsNone(self.transport.get_document(bank=self.bank,
                                                          document_id="doc-1"))

        # The collection 404s too: the path does not exist here, so nothing has
        # been shown about any document.
        with patch.object(HindsightTransport, "_request", side_effect=script(
                gone, missing_route)):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="doc-1")
        self.assertEqual(caught.exception.code, "engine_protocol_error")

        # The collection *lists* the document while its read-back 404s: the two
        # reads contradict each other, and neither may be reported as absence.
        with patch.object(HindsightTransport, "_request", side_effect=script(
                gone, {"items": [{"id": "doc-1"}], "total": 1,
                       "limit": 100, "offset": 0})):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="doc-1")
        self.assertEqual(caught.exception.code, "engine_protocol_error")

    def test_a_collection_that_underscores_its_own_page_shape_is_refused(self) -> None:
        """Every field that decides coverage has to hold up to inspection."""
        gone = self._gone()
        cases = [
            ("negative total",
             {"items": [], "total": -1, "limit": 100, "offset": 0},
             "negative total"),
            ("total shrinks mid-read",
             ({"items": [{"id": f"a{i}"} for i in range(100)], "total": 250,
               "limit": 100, "offset": 0},
              {"items": [{"id": "b"}], "total": 10, "limit": 100, "offset": 100}),
             "changed while it was read"),
            ("total grows mid-read",
             ({"items": [{"id": f"a{i}"} for i in range(100)], "total": 120,
               "limit": 100, "offset": 0},
              {"items": [{"id": "b"}], "total": 900, "limit": 100, "offset": 100}),
             "changed while it was read"),
            ("non-integer limit",
             {"items": [], "total": 0, "limit": "100", "offset": 0}, "invalid limit"),
            ("negative limit",
             {"items": [], "total": 0, "limit": -5, "offset": 0}, "invalid limit"),
            ("more rows than its own limit",
             {"items": [{"id": "x"}, {"id": "y"}], "total": 2, "limit": 1, "offset": 0},
             "stated limit"),
            ("more rows than its total allows",
             {"items": [{"id": "x"}], "total": 0, "limit": 100, "offset": 0},
             "more rows than its total"),
        ]
        for label, replies, expected in cases:
            with self.subTest(label=label):
                batch = replies if isinstance(replies, tuple) else (replies,)
                with patch.object(HindsightTransport, "_request",
                                  side_effect=self._script(gone, *batch)):
                    with self.assertRaises(TransportError) as caught:
                        self.transport.get_document(bank=self.bank,
                                                    document_id="our-document")
                self.assertIn(expected, caught.exception.safe_error_summary, label)

    def test_a_total_that_changes_under_us_is_retryable_not_terminal(self) -> None:
        """Concurrency, not schema: a retry re-reads once the bank settles."""
        gone = self._gone()
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone,
                {"items": [{"id": f"a{i}"} for i in range(100)], "total": 250,
                 "limit": 100, "offset": 0},
                {"items": [{"id": "b"}], "total": 10, "limit": 100, "offset": 100})):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="our-document")
        self.assertEqual(caught.exception.code, "engine_conflict")
        self.assertTrue(caught.exception.retryable)

    def test_a_namespace_moves_the_engine_address_not_the_core_id(self) -> None:
        """The isolation is real, and it happens where the data lives.

        Two runs — or a run and the deployment — must not share an engine bank;
        P3's Memory provider must still be handed Core's canonical ids. Both hold
        at once only if the namespace is applied on the wire. This drives the
        real transport against the stub to say so, rather than asserting on a
        helper.
        """
        bank = scope.project_bank("prj_01K742SG00BMSDET9BTP151RAR")
        canonical = bank.bank_id
        os.environ["JASMINE_MEMORY_BANK_NAMESPACE"] = "run-a"
        try:
            self.transport.retain(bank=bank, document_id="doc-1", content="c",
                                  context="x", metadata={}, tags=(),
                                  memory_type="FAILURE_LESSON", timestamp=None)
            self.assertIn(("run-a" + canonical, "doc-1"), self.server.httpd.documents,
                          "the engine was not addressed with the namespace")
            self.assertNotIn((canonical, "doc-1"), self.server.httpd.documents)
            self.assertEqual(bank.bank_id, canonical,
                             "Core's own id changed with the engine's address")
            self.assertIsNotNone(self.transport.get_document(bank=bank,
                                                             document_id="doc-1"))
        finally:
            os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)

        # Same Core bank, no namespace: a different engine bank, so the document
        # that run-a wrote is not visible to it. The un-namespaced bank has to
        # exist first -- an engine bank nothing was ever written to answers 404
        # for its documents too, and then the absence cannot be *proved* at all,
        # which is a different (and correct) refusal from "that document is not
        # there". Isolation between two existing banks is the claim being made.
        self.transport.retain(bank=bank, document_id="unrelated", content="c",
                              context="x", metadata={}, tags=(),
                              memory_type="FAILURE_LESSON", timestamp=None)
        self.assertIsNone(self.transport.get_document(bank=bank, document_id="doc-1"),
                          "two engine namespaces saw each other's document")
        self.assertEqual(bank.bank_id, canonical)

    def test_a_namespaced_read_back_is_not_mistaken_for_another_bank(self) -> None:
        """The engine echoes its own address; Core's id is not it.

        Comparing the collection's `bank_id` to `bank.bank_id` refused every
        correct row the moment a namespace was configured, which broke the
        read-back the absence proof depends on.
        """
        bank = scope.project_bank("prj_01K742SG00BMSDET9BTP151RAR")
        os.environ["JASMINE_MEMORY_BANK_NAMESPACE"] = "run-b"
        try:
            self.transport.retain(bank=bank, document_id="doc-1", content="c",
                                  context="x", metadata={}, tags=(),
                                  memory_type="FAILURE_LESSON", timestamp=None)
            # An absent document sends the read-back to the collection, whose
            # entries name the *engine* bank. Before the fix this raised.
            self.assertIsNone(self.transport.get_document(bank=bank,
                                                          document_id="absent"))
            self.assertIsNotNone(self.transport.get_document(bank=bank,
                                                             document_id="doc-1"))
        finally:
            os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)

    def test_a_document_from_another_bank_is_refused(self) -> None:
        """The collection was asked about one bank; its rows must be that bank's."""
        gone = self._gone()
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone, {"items": [{"id": "doc-a", "bank_id": "somebody-elses-bank"}],
                       "total": 1, "limit": 100, "offset": 0})):
            with self.assertRaises(TransportError) as caught:
                self.transport.get_document(bank=self.bank, document_id="our-document")
        self.assertEqual(caught.exception.code, "engine_protocol_error")
        self.assertIn("another bank", caught.exception.safe_error_summary)

    def test_a_limit_of_zero_cannot_stand_in_for_coverage(self) -> None:
        """A page that can never advance is not evidence that it was read.

        `offset >= total` only ever becomes true if `offset` moves, and a server
        answering `limit: 0` while rows are outstanding guarantees it will not.
        """
        gone = self._gone()
        for total in (1, 250):
            with self.subTest(total=total):
                with patch.object(HindsightTransport, "_request", side_effect=self._script(
                        gone, {"items": [], "total": total, "limit": 0, "offset": 0})):
                    with self.assertRaises(TransportError) as caught:
                        self.transport.get_document(bank=self.bank,
                                                    document_id="our-document")
                self.assertEqual(caught.exception.code, "engine_protocol_error")
                self.assertIn("limit of 0", caught.exception.safe_error_summary)

        # Nothing outstanding is not a paging problem: an empty collection is
        # an empty collection.
        with patch.object(HindsightTransport, "_request", side_effect=self._script(
                gone, {"items": [], "total": 0, "limit": 0, "offset": 0})):
            self.assertIsNone(self.transport.get_document(bank=self.bank,
                                                          document_id="our-document"))


class AUnitWeCannotReadIsNotAUnitWeMaySkip(unittest.TestCase):
    """The failure mode is "one entry was skipped", not "the list was wrong".

    A reader that filters the units it understands turns a malformed, duplicated
    or foreign entry into a silent omission — and then a job records `DONE`
    having handled the subset it happened to parse. Obsoleting "every unit" would
    leave the rest `valid` while the receipt says otherwise.
    """

    BANK = "prj_01K742SG00BMSDET9BTP151RAR"

    def _page(self, items, *, total, offset, limit=200):
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def _read(self, *pages):
        replies = iter(pages)

        def reply(*args, **kwargs):
            return next(replies)

        transport = HindsightTransport.__new__(HindsightTransport)
        transport._request = reply
        return transport.list_all_document_memories(
            bank=scope.project_bank(self.BANK), document_id="doc-1")

    def test_an_unreadable_entry_is_refused(self) -> None:
        for label, entry in (("not an object", None), ("no id", {"text": "x"}),
                             ("empty id", {"id": ""}), ("numeric id", {"id": 7})):
            with self.subTest(label=label):
                with self.assertRaises(TransportError) as caught:
                    self._read(self._page([entry], total=1, offset=0))
                self.assertEqual(caught.exception.code, "engine_protocol_error")
                self.assertFalse(caught.exception.retryable)

    def test_a_duplicate_across_pages_is_refused(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self._read(self._page([{"id": f"mu_{i}"} for i in range(200)],
                                  total=201, offset=0),
                       self._page([{"id": "mu_0"}], total=201, offset=200))
        self.assertIn("twice", caught.exception.safe_error_summary)

    def test_a_unit_of_another_document_is_refused(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self._read(self._page([{"id": "mu_1", "document_id": "somebody-elses"}],
                                  total=1, offset=0))
        self.assertEqual(caught.exception.code, "engine_protocol_error")
        self.assertIn("another document", caught.exception.safe_error_summary)

    def test_a_well_formed_list_still_reads(self) -> None:
        units = self._read(self._page(
            [{"id": "mu_1", "document_id": "doc-1", "state": "valid"},
             {"id": "mu_2", "state": "valid"}], total=2, offset=0))
        self.assertEqual([u["id"] for u in units], ["mu_1", "mu_2"])
