"""The simulated P4 read fixture: shapes, labels, and the states the views branch on.

P6-03b1's dependency is missing, so the fixture's honesty is the whole point. Three
properties are checked here rather than asserted in prose:

1. every payload matches the **frozen contract** exactly — the builders assert at
   construction time, and this states the expectation where a reader of the tests will see
   it;
2. every reply is **labelled** as simulated, so a screenshot cannot be read as a real Core;
3. the states the views must keep apart really come back distinct over HTTP — an unknown id
   and an unauthorised project are both `not_found`, a token with no grants gets an empty
   list rather than an error, a token without `memory:read` is refused naming that scope,
   and every write is refused because writes are P6-03b2.

The HTTP cases start the fixture on an ephemeral port, so they exercise the same handler the
click-through Gate talks to.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "p4_read_fixture", ROOT / "scripts" / "p6" / "p4_read_fixture.py")
fixture = importlib.util.module_from_spec(_spec)
sys.modules["p4_read_fixture"] = fixture
_spec.loader.exec_module(fixture)

CONTRACT = json.loads((ROOT / "web" / "contracts" / "p4-memory-read.v1.json")
                      .read_text(encoding="utf-8"))

_P4_SOURCE_CACHE: dict[tuple[str, str], str] = {}


def _p4_source(ref: str, path: str) -> str:
    """P4's file at `ref`, or skip the test. Cached: several tests read the same file."""
    if (ref, path) in _P4_SOURCE_CACHE:
        return _P4_SOURCE_CACHE[(ref, path)]
    try:
        source = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        raise unittest.SkipTest(
            f"the pinned P4 commit {ref[:12]} is not in this checkout, so {path} cannot be "
            f"compared ({error.__class__.__name__})") from error
    _P4_SOURCE_CACHE[(ref, path)] = source
    return source


def _table_columns(source: str, table: str) -> list[str]:
    """Columns of one `CREATE TABLE`, scoped to that table on purpose."""
    import re
    block = re.search(r"CREATE TABLE " + table + r" \((.*?)\n    \)", source, re.S)
    if block is None:
        raise AssertionError(f"table {table} not found")
    return re.findall(r"^\s+([a-z][a-z0-9_]*)\s+(?:TEXT|INTEGER|REAL|BLOB)",
                      block.group(1), re.M)


def call(port: int, path: str, token: str | None = "full",
         method: str = "GET") -> tuple[int, dict, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode()), dict(error.headers)


class PayloadShapes(unittest.TestCase):
    def test_every_payload_matches_the_frozen_field_set(self) -> None:
        sets = fixture.contract_field_sets(CONTRACT)
        observed = {
            "health": (fixture.health_payload(CONTRACT), "health"),
            "jobs": (fixture.jobs_payload(CONTRACT, empty=False), "jobs"),
            "jobs-empty": (fixture.jobs_payload(CONTRACT, empty=True), "jobs"),
            "job": (fixture.job_payload(CONTRACT), "job"),
            "items": (fixture.items_payload(CONTRACT, empty=False), "items"),
            "items-empty": (fixture.items_payload(CONTRACT, empty=True), "items"),
            "item": (fixture.item_payload(CONTRACT), "item"),
            "traces": (fixture.traces_payload(CONTRACT, empty=False), "traces"),
            "trace": (fixture.trace_payload(CONTRACT), "trace"),
        }
        for name, (payload, key) in observed.items():
            with self.subTest(payload=name):
                self.assertEqual(set(payload), set(sets[key]))

    def test_nested_rows_carry_the_contract_key_sets(self) -> None:
        nested = CONTRACT["nested_fields"]
        self.assertEqual(set(fixture.job_row(CONTRACT, ident=fixture.JOB)), set(nested["job"]))
        self.assertEqual(set(fixture.attempt_rows(CONTRACT)[0]), set(nested["attempt"]))
        self.assertEqual(set(fixture.lifecycle_summary(CONTRACT)), set(nested["lifecycle"]))
        self.assertEqual(set(fixture.item_row(CONTRACT, ident=fixture.ITEM)), set(nested["item"]))
        self.assertEqual(set(fixture.trace_row(CONTRACT, ident=fixture.TRACE)), set(nested["trace"]))
        self.assertEqual(set(fixture.health_payload(CONTRACT)["jobs"]), set(nested["job_counts"]))
        self.assertEqual(set(fixture.health_payload(CONTRACT)["items"]), set(nested["health_items"]))

    def test_the_list_row_adds_lifecycle_and_the_detail_row_does_not(self) -> None:
        """`list_items` adds `lifecycle`; `get_item` returns the bare item under `memory`."""
        listed = fixture.items_payload(CONTRACT, empty=False)["items"][0]
        self.assertEqual(set(listed), set(CONTRACT["nested_fields"]["item"]) | {"lifecycle"})
        detail = fixture.item_payload(CONTRACT)["memory"]
        self.assertEqual(set(detail), set(CONTRACT["nested_fields"]["item"]))

    def test_the_trace_detail_adds_items_and_the_list_row_does_not(self) -> None:
        listed = fixture.traces_payload(CONTRACT, empty=False)["traces"][0]
        self.assertEqual(set(listed), set(CONTRACT["nested_fields"]["trace"]))
        detail = fixture.trace_payload(CONTRACT)["trace"]
        self.assertEqual(set(detail), set(CONTRACT["nested_fields"]["trace"]) | {"items"})


class ShapesMirrorP4(unittest.TestCase):
    """The fixture serves P4's *keys*, read from P4's source — not a hardcoded copy."""

    def test_row_keys_come_from_the_p4_tables(self) -> None:
        ref = CONTRACT["source"]["ref"]
        migration = _p4_source(ref, "src/jasmine_core/migrations/m0011_memory.py")
        job_columns = _table_columns(migration, "memory_jobs")
        self.assertEqual(len(job_columns), 23)
        # `receipt_json` is decoded to `receipt`, so the key set is the columns with that
        # one substitution.
        self.assertEqual(set(CONTRACT["nested_fields"]["job"]),
                         (set(job_columns) - {"receipt_json"}) | {"receipt"})
        for table, key in (("memory_query_traces", "trace"),
                           ("memory_job_attempts", "attempt")):
            columns = _table_columns(migration, table)
            with self.subTest(table=table):
                decoded = {c.removesuffix("_json") for c in columns}
                self.assertEqual(set(CONTRACT["nested_fields"][key]), decoded)

    def test_a_rename_in_p4_would_break_the_guard(self) -> None:
        """Falsify by mutating P4's source, not by mutating the expectation."""
        ref = CONTRACT["source"]["ref"]
        migration = _p4_source(ref, "src/jasmine_core/migrations/m0011_memory.py")
        renamed = migration.replace("next_attempt_at", "next_attempt_at_v2")
        self.assertNotEqual(renamed, migration, "P4 no longer uses that column")
        self.assertNotEqual(set(_table_columns(renamed, "memory_jobs")),
                            set(_table_columns(migration, "memory_jobs")),
                            "renaming a column in P4 must change the derived key set")

    def test_p4_declares_the_scopes_this_slice_assumes(self) -> None:
        ref = CONTRACT["source"]["ref"]
        api = _p4_source(ref, "src/jasmine_core/memory/api.py")
        for scope in ("memory:read", "memory:manage", "memory:reflect"):
            with self.subTest(scope=scope):
                self.assertIn(f'"{scope}"', api)
        # The fixture enforces exactly `memory:read` for the seven reads, and the contract
        # records that no helper adds more (unlike P3's `_continuity_read`).
        self.assertEqual({r["scope"] for r in CONTRACT["read_routes"]}, {"memory:read"})
        self.assertNotIn("_continuity_read", api)


class OverHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture.Handler.contract = CONTRACT
        fixture.Handler.tokens = {
            "full": {"objects:read", "memory:read"},
            # No grants: P4 answers an empty list rather than the whole database.
            "grantless": {"objects:read", "memory:read", "projects:none"},
            # Can sign in, but cannot read Memory.
            "nomem": {"objects:read"},
            # A non-human actor: P4 withholds engine free text from it.
            "agent": {"objects:read", "memory:read", "actor:agent"},
        }
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_the_seven_reads_answer_200(self) -> None:
        for path in ("/v1/memory/health", "/v1/memory/jobs",
                     f"/v1/memory/jobs/{fixture.JOB}", "/v1/memory/items",
                     f"/v1/memory/items/{fixture.ITEM}", "/v1/memory/traces",
                     f"/v1/memory/traces/{fixture.TRACE}"):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path)
                self.assertEqual(status, 200, body)

    def test_every_reply_is_labelled_simulated(self) -> None:
        # The label is what stops a screenshot being read as evidence about a real Core.
        for path in ("/v1/memory/health", "/v1/memory/jobs", "/v1/memory/items",
                     "/v1/memory/traces"):
            with self.subTest(path=path):
                status, _, headers = call(self.port, path)
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("X-P6-Fixture"), fixture.FIXTURE_HEADER)

    def test_a_grantless_token_gets_an_empty_list_not_an_error(self) -> None:
        for path, key in (("/v1/memory/jobs", "jobs"), ("/v1/memory/items", "items"),
                          ("/v1/memory/traces", "traces")):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path, "grantless")
                self.assertEqual(status, 200, body)
                self.assertEqual(body[key], [])
                self.assertEqual(body["count"], 0)

    def test_an_unknown_id_is_not_found(self) -> None:
        # Valid-shaped but unknown: a malformed id is a different status on purpose, and
        # the views refuse it locally without a request at all.
        for path in (f"/v1/memory/jobs/{fixture.JOB_UNKNOWN}",
                     f"/v1/memory/items/{fixture.UNKNOWN}",
                     f"/v1/memory/traces/{fixture.TRACE_UNKNOWN}"):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path)
                self.assertEqual(status, 404, body)
                self.assertEqual(body["error"]["code"], "not_found")

    def test_an_unauthorised_project_is_not_found_rather_than_an_error(self) -> None:
        # P4 reports a project the actor may not use as not_found, so the inspector cannot
        # enumerate another project's work. The view must show not_found, not a failure.
        status, body, _ = call(self.port, f"/v1/memory/items?project_id={fixture.PROJECT_OTHER}")
        self.assertEqual(status, 404, body)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_a_missing_scope_is_403_and_names_itself(self) -> None:
        status, body, _ = call(self.port, "/v1/memory/jobs", "nomem")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertEqual(body["error"]["details"]["required"], "memory:read")

    def test_a_malformed_id_is_400_not_a_silent_404(self) -> None:
        status, body, _ = call(self.port, "/v1/memory/items/mem_short")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "invalid_request")

    def test_no_token_is_401(self) -> None:
        status, body, _ = call(self.port, "/v1/memory/jobs", None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")

    def test_every_write_is_refused_because_writes_are_p6_03b2(self) -> None:
        for path in ("/v1/memory/reflect", f"/v1/memory/jobs/{fixture.JOB}/retry",
                     f"/v1/memory/items/{fixture.ITEM}/obsolete",
                     f"/v1/memory/items/{fixture.ITEM}/delete",
                     f"/v1/memory/items/{fixture.ITEM}/pin",
                     f"/v1/memory/items/{fixture.ITEM}/unpin"):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path, method="POST")
                self.assertEqual(status, 405, body)
                self.assertEqual(body["error"]["code"], "method_not_allowed")

    def test_non_authoritative_is_present_where_p4_sends_it(self) -> None:
        # The views read this field rather than asserting it, so the fixture must send it
        # on exactly the payloads Core does.
        for path in ("/v1/memory/health", "/v1/memory/items",
                     f"/v1/memory/items/{fixture.ITEM}"):
            with self.subTest(path=path):
                _, body, _ = call(self.port, path)
                self.assertIs(body["non_authoritative"], True)
        for path in ("/v1/memory/jobs", f"/v1/memory/jobs/{fixture.JOB}",
                     "/v1/memory/traces", f"/v1/memory/traces/{fixture.TRACE}"):
            with self.subTest(path=path):
                _, body, _ = call(self.port, path)
                self.assertNotIn("non_authoritative", body,
                                 "P4 does not mark these payloads; the fixture must not either")

    def test_a_human_key_sees_the_engine_text(self) -> None:
        """The control for the redaction test: without this, withholding proves nothing."""
        _, job, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_2}")
        _, listing, _ = call(self.port, "/v1/memory/jobs")
        failed = next(row for row in listing["jobs"] if row["job_id"] == fixture.JOB_2)
        self.assertIsNotNone(job["job"]["safe_error_summary"])
        self.assertIsNotNone(failed["safe_error_summary"])

    def test_an_agent_key_has_the_engine_text_withheld(self) -> None:
        """P4's `_withhold_engine_text`: the key stays, the value does not."""
        _, job, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_2}", "agent")
        self.assertIn("safe_error_summary", job["job"])
        self.assertIsNone(job["job"]["safe_error_summary"])

        _, listing, _ = call(self.port, "/v1/memory/jobs", "agent")
        failed = next(row for row in listing["jobs"] if row["job_id"] == fixture.JOB_2)
        self.assertIsNone(failed["safe_error_summary"])
        # Nested occurrences too: the same job appears inside an item's jobs.
        _, item, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}", "agent")
        for nested in item["jobs"]:
            self.assertIsNone(nested["safe_error_summary"])

    def test_withholding_does_not_drop_the_field(self) -> None:
        """A dropped key would change the shape; P4 nulls the value and keeps the key."""
        _, job, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_2}", "agent")
        self.assertEqual(set(job["job"]), set(CONTRACT["nested_fields"]["job"]))

    def test_the_count_matches_the_rows_served(self) -> None:
        for path, key in (("/v1/memory/jobs", "jobs"), ("/v1/memory/items", "items"),
                          ("/v1/memory/traces", "traces")):
            with self.subTest(path=path):
                _, body, _ = call(self.port, path)
                self.assertEqual(body["count"], len(body[key]))


if __name__ == "__main__":
    unittest.main()
