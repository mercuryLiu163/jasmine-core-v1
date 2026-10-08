"""The simulated P4 write fixture: the frozen contract, the permission split, and the states.

P6-03b2's dependency is missing, so the fixture's honesty is the whole point. Three
properties are checked here rather than asserted in prose:

1. the response shapes and the six routes are checked **against the frozen contract**, read
   from `contracts/p4-memory-write.v1.json` rather than copied into the test — a drifted
   contract or fixture fails here;
2. every reply is **labelled** `simulated-p4-writes`, so a screenshot cannot be read as a
   real Core;
3. the behaviours the pinned commit pins really come back distinct over HTTP: the scope
   refusal names the missing scope, an agent key is refused with `forbidden_actor_kind`, a
   missing `expected_revision` is 400 `missing_expected_revision`, a stale one is 409
   `revision_conflict`, a tombstone is refused (`invalid_state_transition`) before the
   revision is even looked at, a replay is 5 keys and a fresh transition 7, and a retry
   neither changes what is retained, steals a running job, re-queues a pending one, nor
   increments the budget (it resets it).

The HTTP cases start the fixture on an ephemeral port, so they exercise the same handler a
click-through Gate would talk to.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "p4_write_fixture", ROOT / "scripts" / "p6" / "p4_write_fixture.py")
fixture = importlib.util.module_from_spec(_spec)
sys.modules["p4_write_fixture"] = fixture
_spec.loader.exec_module(fixture)

#: The read fixture this one subclasses, reached through the write fixture's own import so
#: the test cannot disagree with the fixture about which copy it extended.
base = fixture.base

WRITE_CONTRACT = json.loads((ROOT / "web" / "contracts" / "p4-memory-write.v1.json")
                            .read_text(encoding="utf-8"))
READ_CONTRACT = json.loads((ROOT / "web" / "contracts" / "p4-memory-read.v1.json")
                           .read_text(encoding="utf-8"))

CALL_COUNTER = itertools.count(1)


def unique_event() -> str:
    """A valid `evt_` id the fixture has never seen. Offset past the generated ones."""
    return fixture.generated_id("evt", 1000 + next(CALL_COUNTER))


def spec_for(path: str) -> dict:
    """The frozen write whose recorded pattern matches this exact path."""
    for entry in WRITE_CONTRACT["writes"]:
        if re.fullmatch(entry["path_pattern"], path):
            return entry
    raise AssertionError(f"the frozen contract records no write for {path}")


def write_paths() -> list[str]:
    """One concrete path per recorded write, derived from the patterns themselves."""
    return [
        f"/v1/memory/jobs/{fixture.JOB_2}/retry",
        f"/v1/memory/items/{fixture.ITEM}/obsolete",
        f"/v1/memory/items/{fixture.ITEM}/delete",
        f"/v1/memory/items/{fixture.ITEM}/pin",
        f"/v1/memory/items/{fixture.ITEM}/unpin",
        "/v1/memory/reflect",
    ]


def state_item(state: dict, ident: str) -> dict:
    """The `/v1/_p6_fixture/state` `items` entry for one item."""
    return next(row for row in state["items"] if row["memory_id"] == ident)


def state_job(state: dict, ident: str) -> dict:
    """The `/v1/_p6_fixture/state` `jobs` entry for one job."""
    return next(row for row in state["jobs"] if row["job_id"] == ident)


#: A valid value per allowed body field, so a body can be built from the contract alone.
VALID_VALUES = {
    "event_id": None,          # replaced per call by a fresh id
    "host_id": fixture.HOST,
    "expected_revision": 1,
    "reason": "operator request",
    "project_id": fixture.PROJECT,
    "query": "what changed about the deployment?",
    "max_tokens": 256,
    "task_id": base.TASK,
}


def allowed_body(path: str, **overrides) -> dict:
    fields = spec_for(path)["allowed_body_fields"]
    body = {}
    for field in fields:
        body[field] = unique_event() if field == "event_id" else VALID_VALUES[field]
    body.update(overrides)
    return body


def call(port: int, path: str, token: str | None = "full", *,
         method: str = "GET", body: dict | None = None) -> tuple[int, dict, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     data=data)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode()), dict(error.headers)


class ContractDriven(unittest.TestCase):
    """No HTTP: the contract, the route table and the builders must agree exactly."""

    def test_the_six_writes_are_exactly_the_routes_do_post_implements(self) -> None:
        routes = fixture.assert_six_writes(WRITE_CONTRACT)
        self.assertEqual(len(routes), 6)
        self.assertEqual({(route["kind"], route["command"]) for route in routes},
                         set(fixture.EXPECTED_ROUTES))
        self.assertEqual({route["spec"]["path_pattern"] for route in routes},
                         {entry["path_pattern"] for entry in WRITE_CONTRACT["writes"]})
        for route in routes:
            with self.subTest(pattern=route["spec"]["path_pattern"]):
                self.assertEqual(route["pattern"].pattern, route["spec"]["path_pattern"])
                self.assertEqual(route["spec"]["method"], "POST")

    def test_every_recorded_response_shape_matches_the_frozen_contract(self) -> None:
        report = fixture.shape_report(READ_CONTRACT, WRITE_CONTRACT)
        observed = {
            "retry": ("retry_job", 0),
            "lifecycle": ('_lifecycle_command("pin")', 0),
            "lifecycle-replay": ('_lifecycle_command("pin")', 1),
            "reflect": ("reflect", 0),
        }
        self.assertEqual(set(report), set(observed))
        for name, (handler, index) in observed.items():
            with self.subTest(shape=name):
                expected = set(fixture.find_write(WRITE_CONTRACT, handler=handler)
                               ["response_shapes"][index])
                self.assertEqual(set(report[name]), expected)
                # The builders assert too; this states the expectation where a reader looks.
                self.assertTrue(fixture.write_shape(WRITE_CONTRACT, handler=handler,
                                                    index=index) == expected)

    def test_the_pre_serve_check_reports_one_line_per_shape(self) -> None:
        report = fixture.pre_serve_checks(READ_CONTRACT, WRITE_CONTRACT)
        self.assertEqual(set(report), {"retry", "lifecycle", "lifecycle-replay", "reflect"})

    def test_the_two_lifecycle_shapes_differ_by_before_and_after(self) -> None:
        shapes = fixture.find_write(WRITE_CONTRACT, handler='_lifecycle_command("pin")'
                                    )["response_shapes"]
        self.assertEqual(len(shapes), 2)
        self.assertEqual(set(shapes[0]) - set(shapes[1]), {"before", "after"})
        self.assertEqual(set(shapes[1]) - set(shapes[0]), set())

    def test_the_write_label_is_not_the_read_fixtures(self) -> None:
        self.assertEqual(fixture.FIXTURE_HEADER, "simulated-p4-writes")
        self.assertNotEqual(fixture.FIXTURE_HEADER, base.FIXTURE_HEADER)

    def test_the_retry_write_requires_no_body_field(self) -> None:
        # The contract records an empty `required_body_fields`; a body-less retry is legal.
        entry = spec_for(f"/v1/memory/jobs/{fixture.JOB_2}/retry")
        self.assertEqual(entry["required_body_fields"], [])
        self.assertEqual(set(entry["allowed_body_fields"]), {"event_id", "host_id"})

    def test_the_fixture_controls_are_not_part_of_the_write_surface(self) -> None:
        """The controls are fixture-internal: not in the contract, not in the route table."""
        self.assertEqual(fixture.CONTROL_PREFIX, "/v1/_p6_fixture/")
        for entry in WRITE_CONTRACT["writes"]:
            with self.subTest(pattern=entry["path_pattern"]):
                self.assertNotIn("_p6_fixture", entry["path_pattern"])
        routes = fixture.write_routes(WRITE_CONTRACT)
        self.assertEqual(len(routes), 6)
        for control in ("state", "drift", "set-job-status", "drop-next"):
            path = f"{fixture.CONTROL_PREFIX}{control}"
            with self.subTest(control=control):
                self.assertFalse(any(route["pattern"].fullmatch(path) for route in routes))


class HttpHarness(unittest.TestCase):
    """A live fixture on an ephemeral port, with a fresh state per test.

    Split out of `OverHttp` so the audit-readback class can use the same harness
    without inheriting — and therefore re-running — every one of its tests.
    """

    @classmethod
    def setUpClass(cls) -> None:
        fixture.Handler.contract = READ_CONTRACT
        fixture.Handler.write_contract = WRITE_CONTRACT
        fixture.Handler.tokens = {
            # A human key with every scope: the control for the withholding tests.
            "full": {"objects:read", "memory:read", "memory:manage", "memory:reflect"},
            # Can sign in, but holds neither write scope.
            "reader": {"objects:read", "memory:read"},
            # Non-human, holding the manage scope: P4 refuses it on actor kind.
            "agent": {"objects:read", "memory:read", "memory:manage", "actor:agent"},
            # Artificial: see `test_the_artificial_agent_reflect_token...`.
            "agent-reflect": {"objects:read", "memory:read", "memory:reflect", "actor:agent"},
        }
        fixture.Handler.reset_state()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        # Every test starts from the fixture's documented initial state; without this a
        # mutation would leak into whatever runs next.
        fixture.Handler.reset_state()

    def post(self, path: str, body: dict | None = None, token: str | None = "full"):
        return call(self.port, path, token, method="POST", body=body)

class OverHttp(HttpHarness):

    # -- 1. authentication ------------------------------------------------------
    def test_no_token_is_401_unauthenticated_on_every_write(self) -> None:
        for path in write_paths():
            with self.subTest(path=path):
                status, body, _ = self.post(path, allowed_body(path), token=None)
                self.assertEqual(status, 401, body)
                self.assertEqual(body["error"]["code"], "unauthenticated")

    def test_an_unknown_token_is_401(self) -> None:
        status, body, _ = self.post(write_paths()[0], {}, token="not-a-fixture-token")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")

    # -- 2. scope ---------------------------------------------------------------
    def test_a_missing_manage_scope_is_403_and_names_itself(self) -> None:
        for path in write_paths():
            if path.endswith("/reflect"):
                continue
            with self.subTest(path=path):
                status, body, _ = self.post(path, {}, token="reader")
                self.assertEqual(status, 403, body)
                self.assertEqual(body["error"]["code"], "forbidden_scope")
                self.assertIn("memory:manage", body["error"]["message"])
                self.assertEqual(body["error"]["details"]["required"], "memory:manage")
                self.assertIn("objects:read", body["error"]["details"]["granted"])

    def test_a_missing_reflect_scope_is_403_and_names_itself(self) -> None:
        status, body, _ = self.post("/v1/memory/reflect", {}, token="reader")
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertIn("memory:reflect", body["error"]["message"])
        self.assertEqual(body["error"]["details"]["required"], "memory:reflect")

    # -- 3. actor kind ----------------------------------------------------------
    def test_an_agent_token_cannot_issue_a_manage_command(self) -> None:
        for path in write_paths():
            if path.endswith("/reflect"):
                continue
            with self.subTest(path=path):
                status, body, _ = self.post(path, allowed_body(path), token="agent")
                self.assertEqual(status, 403, body)
                self.assertEqual(body["error"]["code"], "forbidden_actor_kind")
                self.assertIn("human", body["error"]["message"])
                self.assertIn("system", body["error"]["message"])

    # -- 4. the body allow-list and field constraints ---------------------------
    def test_a_field_outside_the_allow_list_is_refused_naming_it(self) -> None:
        for path in write_paths():
            with self.subTest(path=path):
                body = allowed_body(path, surprise="not in the contract")
                status, reply, _ = self.post(path, body)
                self.assertEqual(status, 400, reply)
                self.assertEqual(reply["error"]["code"], "invalid_request")
                self.assertIn("surprise", reply["error"]["message"])
                self.assertIn("surprise", reply["error"]["details"]["fields"])

    def test_every_allowed_field_is_accepted(self) -> None:
        for path in write_paths():
            with self.subTest(path=path):
                # Each write starts from the documented initial state, so the lifecycle
                # commands do not invalidate one another's revisions.
                fixture.Handler.reset_state()
                expected = spec_for(path)["allowed_body_fields"]
                body = allowed_body(path)
                # The contract's own allow-list, filled completely, must be a legal body.
                self.assertEqual(set(body), set(expected))
                status, reply, _ = self.post(path, body)
                self.assertEqual(status, 200, reply)

    def test_a_missing_host_id_is_400_named(self) -> None:
        status, body, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                    {"expected_revision": 1})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "invalid_request")
        self.assertEqual(body["error"]["details"]["field"], "host_id")

    def test_a_missing_expected_revision_is_400(self) -> None:
        # `errors.MissingExpectedRevision` is status 400, wire code `missing_expected_revision`.
        status, body, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                    {"host_id": fixture.HOST})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "missing_expected_revision")

    def test_a_malformed_host_id_is_400_named(self) -> None:
        status, body, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                    {"expected_revision": 1, "host_id": "host-1"})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["details"]["field"], "host_id")

    def test_a_malformed_or_nonpositive_expected_revision_is_400(self) -> None:
        for value in (0, -1, "1", 1.5, True, None):
            with self.subTest(value=value):
                status, body, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                            {"expected_revision": value,
                                             "host_id": fixture.HOST})
                self.assertEqual(status, 400, body)
                self.assertEqual(body["error"]["code"], "invalid_request")
                self.assertEqual(body["error"]["details"]["field"], "expected_revision")

    def test_a_reason_longer_than_500_characters_is_400(self) -> None:
        status, body, _ = self.post(
            f"/v1/memory/items/{fixture.ITEM}/pin",
            {"expected_revision": 1, "host_id": fixture.HOST, "reason": "x" * 501})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["details"]["field"], "reason")
        # The boundary is legal: 500 is the documented maximum.
        status, body, _ = self.post(
            f"/v1/memory/items/{fixture.ITEM}/pin",
            {"expected_revision": 1, "host_id": fixture.HOST, "reason": "x" * 500})
        self.assertEqual(status, 200, body)

    def test_a_malformed_event_id_is_400_on_a_lifecycle_command(self) -> None:
        path = f"/v1/memory/items/{fixture.ITEM}/pin"
        status, reply, _ = self.post(path, allowed_body(path, event_id="evt_short"))
        self.assertEqual(status, 400, reply)
        self.assertEqual(reply["error"]["details"]["field"], "event_id")

    def test_retry_passes_a_malformed_event_id_and_host_id_through(self) -> None:
        """P4 validates neither on retry, so the fixture must not either.

        `memory/api.py::retry_job` calls `request_retry` with `body.get("host_id", "")` and
        `body.get("event_id")` and checks nothing; the shape checks live in the lifecycle path.
        The fixture used to answer 400 for both — stricter than the Core it stands in for, which
        would have turned a real Core's acceptance into a fixture refusal. Found by adversarial
        review.
        """
        path = f"/v1/memory/jobs/{fixture.JOB_2}/retry"
        self.post(f"/v1/_p6_fixture/set-job-status?job={fixture.JOB_2}&status=FAILED")
        status, reply, _ = self.post(path, {"host_id": "host-1", "event_id": "not-an-evt"})
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["replayed"], False)
        self.assertEqual(reply["event"]["event_id"], "not-an-evt")
        # …and the lifecycle path still refuses the same malformed value.
        item = f"/v1/memory/items/{fixture.ITEM}/pin"
        status, reply, _ = self.post(item, allowed_body(item, host_id="host-1"))
        self.assertEqual(status, 400, reply)
        self.assertEqual(reply["error"]["details"]["field"], "host_id")

    def test_a_malformed_path_id_is_400(self) -> None:
        for path in (f"/v1/memory/items/mem_short/pin",
                     f"/v1/memory/jobs/mjb_short/retry"):
            with self.subTest(path=path):
                status, body, _ = self.post(path, {})
                self.assertEqual(status, 400, body)
                self.assertEqual(body["error"]["code"], "invalid_request")

    def test_a_valid_but_unknown_id_is_not_found(self) -> None:
        # `NotFound(kind, id)` -> `{kind}_not_found`; the kind is not "not_found" alone.
        cases = ((f"/v1/memory/items/{fixture.ITEM_UNKNOWN}/pin",
                  f"/v1/memory/items/{fixture.ITEM}/pin", "memory_not_found"),
                 (f"/v1/memory/jobs/{fixture.JOB_UNKNOWN}/retry",
                  f"/v1/memory/jobs/{fixture.JOB_2}/retry", "memory_job_not_found"))
        for path, spec_path, code in cases:
            with self.subTest(path=path):
                status, body, _ = self.post(path, allowed_body(spec_path))
                self.assertEqual(status, 404, body)
                self.assertEqual(body["error"]["code"], code)

    # -- 6. revision ------------------------------------------------------------
    def test_a_stale_expected_revision_is_revision_conflict(self) -> None:
        status, body, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                    {"expected_revision": 99, "host_id": fixture.HOST})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "revision_conflict")
        self.assertEqual(body["error"]["details"]["current_revision"], 1)
        self.assertEqual(body["error"]["details"]["expected_revision"], 99)

    # -- 5. state before revision ----------------------------------------------
    def test_a_tombstone_is_refused_any_command_before_the_revision(self) -> None:
        status, body, _ = self.post(
            f"/v1/memory/items/{fixture.ITEM}/delete",
            {"expected_revision": 1, "host_id": fixture.HOST, "event_id": unique_event()})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["memory"]["status"], "DELETED")
        self.assertEqual(body["memory"]["engine_state"], "DELETE_PENDING")
        self.assertEqual(body["memory"]["revision"], 2)
        for command in ("obsolete", "delete", "pin", "unpin"):
            for revision in (1, 2):  # stale *and* current: the state answer wins either way
                with self.subTest(command=command, revision=revision):
                    status, body, _ = self.post(
                        f"/v1/memory/items/{fixture.ITEM}/{command}",
                        {"expected_revision": revision, "host_id": fixture.HOST,
                         "event_id": unique_event()})
                    self.assertEqual(status, 409, body)
                    self.assertEqual(body["error"]["code"], "invalid_state_transition")
                    self.assertNotEqual(body["error"]["code"], "revision_conflict")

    # -- 7. replay --------------------------------------------------------------
    def test_a_fresh_lifecycle_transition_has_seven_keys(self) -> None:
        path = f"/v1/memory/items/{fixture.ITEM}/pin"
        event = unique_event()
        body = {"expected_revision": 1, "host_id": fixture.HOST, "event_id": event}
        status, reply, _ = self.post(path, body)
        self.assertEqual(status, 200, reply)
        self.assertEqual(set(reply), set(spec_for(path)["response_shapes"][0]))
        self.assertEqual(len(reply), 7)
        self.assertIs(reply["replayed"], False)
        self.assertEqual(reply["memory"]["revision"], 2)
        self.assertIs(reply["memory"]["pinned"], True)
        self.assertEqual(reply["before"]["revision"], 1)
        self.assertEqual(reply["after"]["revision"], 2)
        self.assertIs(reply["non_authoritative"], True)

    def test_a_replay_has_five_keys_and_writes_no_second_transition(self) -> None:
        path = f"/v1/memory/items/{fixture.ITEM}/pin"
        event = unique_event()
        body = {"expected_revision": 1, "host_id": fixture.HOST, "event_id": event}
        first_status, first, _ = self.post(path, body)
        self.assertEqual(first_status, 200, first)
        second_status, second, _ = self.post(path, body)
        self.assertEqual(second_status, 200, second)
        self.assertEqual(set(second), set(spec_for(path)["response_shapes"][1]))
        self.assertEqual(len(second), 5)
        self.assertNotIn("before", second)
        self.assertNotIn("after", second)
        self.assertIs(second["replayed"], True)
        self.assertEqual(second["event"], first["event"])
        # No second transition: the item is still at revision 2 and pinned once.
        _, item, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertEqual(item["memory"]["revision"], 2)
        self.assertIs(item["memory"]["pinned"], True)

    def test_a_command_without_an_event_id_is_never_a_replay(self) -> None:
        path = f"/v1/memory/items/{fixture.ITEM}/pin"
        for expected_revision in (1, 2):
            with self.subTest(expected_revision=expected_revision):
                status, reply, _ = self.post(path, {"expected_revision": expected_revision,
                                                    "host_id": fixture.HOST})
                self.assertEqual(status, 200, reply)
                self.assertIs(reply["replayed"], False)
        _, item, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertEqual(item["memory"]["revision"], 3)

    def test_reusing_an_event_id_on_another_object_is_event_id_conflict(self) -> None:
        event = unique_event()
        body = {"expected_revision": 1, "host_id": fixture.HOST, "event_id": event}
        self.assertEqual(self.post(f"/v1/memory/items/{fixture.ITEM}/pin", body)[0], 200)
        status, reply, _ = self.post(f"/v1/memory/items/{fixture.ITEM_2}/pin", body)
        self.assertEqual(status, 409, reply)
        self.assertEqual(reply["error"]["code"], "event_id_conflict")

    def test_reusing_an_event_id_for_another_command_is_event_id_conflict(self) -> None:
        event = unique_event()
        body = {"expected_revision": 1, "host_id": fixture.HOST, "event_id": event}
        self.assertEqual(self.post(f"/v1/memory/items/{fixture.ITEM}/pin", body)[0], 200)
        status, reply, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/obsolete", body)
        self.assertEqual(status, 409, reply)
        self.assertEqual(reply["error"]["code"], "event_id_conflict")

    # -- 8. retry invariants ----------------------------------------------------
    def test_a_done_job_is_refused_rather_than_retained_twice(self) -> None:
        status, body, _ = self.post(f"/v1/memory/jobs/{fixture.JOB}/retry",
                                    {"host_id": fixture.HOST})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "invalid_state_transition")

    def test_a_running_job_is_refused_rather_than_stolen(self) -> None:
        status, body, _ = self.post(f"/v1/memory/jobs/{fixture.JOB_RUNNING}/retry",
                                    {"host_id": fixture.HOST})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "invalid_state_transition")

    def test_a_pending_job_is_refused_rather_than_double_queued(self) -> None:
        # `request_retry`: "this Memory job is already queued".
        status, body, _ = self.post(f"/v1/memory/jobs/{fixture.JOB_PENDING}/retry",
                                    {"host_id": fixture.HOST})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "invalid_state_transition")

    def test_a_failed_job_is_requeued_with_the_retry_count_reset(self) -> None:
        status, body, _ = self.post(f"/v1/memory/jobs/{fixture.JOB_2}/retry",
                                    {"host_id": fixture.HOST})
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body), set(spec_for(
            f"/v1/memory/jobs/{fixture.JOB_2}/retry")["response_shapes"][0]))
        self.assertIs(body["replayed"], False)
        self.assertEqual(body["job"]["status"], "PENDING")
        self.assertEqual(body["job"]["retry_count"], 0)

    def test_a_retry_resets_a_nonzero_retry_count_to_zero(self) -> None:
        """`SET status='PENDING', retry_count=0`: a reset, not an increment."""
        _, before, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_FAILED}")
        self.assertEqual(before["job"]["status"], "FAILED")
        self.assertGreater(before["job"]["retry_count"], 0,
                           "the fixture must start this job with a spent budget")
        status, body, _ = self.post(f"/v1/memory/jobs/{fixture.JOB_FAILED}/retry",
                                    {"host_id": fixture.HOST})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["job"]["status"], "PENDING")
        self.assertEqual(body["job"]["retry_count"], 0)
        _, after, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_FAILED}")
        self.assertEqual(after["job"]["status"], "PENDING")
        self.assertEqual(after["job"]["retry_count"], 0)

    def test_a_retry_does_not_change_what_is_retained(self) -> None:
        path = f"/v1/memory/jobs/{fixture.JOB_2}/retry"
        _, before, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_2}")
        status, body, _ = self.post(path, {"host_id": fixture.HOST})
        self.assertEqual(status, 200, body)
        _, after, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_2}")
        for field in ("document_id", "content_sha256", "bank_id"):
            with self.subTest(field=field):
                # In the response and on a fresh read, not just where P4 says it is stable.
                self.assertEqual(body["job"][field], before["job"][field])
                self.assertEqual(after["job"][field], before["job"][field])
        self.assertEqual(after["job"]["retry_count"], 0)
        self.assertEqual(after["job"]["status"], "PENDING")

    def test_a_retry_replay_returns_the_original_event_and_does_not_double_count(self) -> None:
        path = f"/v1/memory/jobs/{fixture.JOB_2}/retry"
        event = unique_event()
        body = {"host_id": fixture.HOST, "event_id": event}
        first_status, first, _ = self.post(path, body)
        self.assertEqual(first_status, 200, first)
        self.assertEqual(first["job"]["retry_count"], 0)
        second_status, second, _ = self.post(path, body)
        self.assertEqual(second_status, 200, second)
        self.assertIs(second["replayed"], True)
        self.assertEqual(second["event"], first["event"])
        self.assertEqual(second["job"]["retry_count"], 0)

    def test_a_job_read_reports_the_retry(self) -> None:
        self.assertEqual(self.post(f"/v1/memory/jobs/{fixture.JOB_2}/retry",
                                   {"host_id": fixture.HOST})[0], 200)
        _, body, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB_2}")
        self.assertEqual(body["job"]["status"], "PENDING")
        self.assertEqual(body["job"]["retry_count"], 0)
        self.assertEqual(set(body), set(base.contract_field_sets(READ_CONTRACT)["job"]))

    def test_the_read_job_row_keeps_its_exact_retained_identity(self) -> None:
        """The Gate asserts these exact values from the response and from the readback."""
        _, before, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB}")
        self.assertEqual(before["job"]["document_id"], f"doc_{base.ID_BODY}")
        self.assertEqual(before["job"]["content_sha256"], "a" * 64)
        self.assertEqual(before["job"]["bank_id"], f"bank_{base.ID_BODY}")
        # Put the fixture's own DONE job into FAILED through the control, then retry it.
        self.assertEqual(call(self.port, f"{fixture.CONTROL_PREFIX}set-job-status", None,
                              method="POST",
                              body={"job": fixture.JOB, "status": "FAILED"})[0], 200)
        status, body, _ = self.post(f"/v1/memory/jobs/{fixture.JOB}/retry",
                                    {"host_id": fixture.HOST})
        self.assertEqual(status, 200, body)
        for field, expected in (("document_id", f"doc_{base.ID_BODY}"),
                                ("content_sha256", "a" * 64),
                                ("bank_id", f"bank_{base.ID_BODY}")):
            with self.subTest(field=field):
                self.assertEqual(body["job"][field], expected)
        self.assertEqual(body["job"]["retry_count"], 0)
        _, after, _ = call(self.port, f"/v1/memory/jobs/{fixture.JOB}")
        for field, expected in (("document_id", f"doc_{base.ID_BODY}"),
                                ("content_sha256", "a" * 64),
                                ("bank_id", f"bank_{base.ID_BODY}")):
            with self.subTest(readback=field):
                self.assertEqual(after["job"][field], expected)
        self.assertEqual(after["job"]["status"], "PENDING")
        self.assertEqual(after["job"]["retry_count"], 0)

    # -- readback through the read routes --------------------------------------
    def test_a_pin_is_visible_on_the_item_read(self) -> None:
        self.assertEqual(self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                   {"expected_revision": 1, "host_id": fixture.HOST})[0], 200)
        _, body, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertIs(body["memory"]["pinned"], True)
        self.assertEqual(body["memory"]["revision"], 2)
        self.assertIs(body["lifecycle"]["pinned"], True)

    def test_an_obsolete_is_visible_on_the_item_read(self) -> None:
        self.assertEqual(self.post(
            f"/v1/memory/items/{fixture.ITEM}/obsolete",
            {"expected_revision": 1, "host_id": fixture.HOST, "reason": "superseded"})[0], 200)
        _, body, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertEqual(body["memory"]["status"], "OBSOLETE")
        self.assertEqual(body["memory"]["lifecycle_reason"], "superseded")
        self.assertEqual(body["lifecycle"]["status"], "OBSOLETE")
        self.assertIs(body["lifecycle"]["recallable"], False)
        self.assertIs(body["recallable"], False)

    def test_a_delete_is_visible_on_the_item_read(self) -> None:
        self.assertEqual(self.post(f"/v1/memory/items/{fixture.ITEM}/delete",
                                   {"expected_revision": 1, "host_id": fixture.HOST})[0], 200)
        _, body, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertEqual(body["memory"]["status"], "DELETED")
        self.assertEqual(body["memory"]["engine_state"], "DELETE_PENDING")
        self.assertEqual(body["lifecycle"]["status"], "DELETED")

    def test_obsolete_and_delete_leave_different_engine_states(self) -> None:
        """The Gate asserts the tombstone and the soft-retire are distinguishable."""
        self.assertEqual(self.post(f"/v1/memory/items/{fixture.ITEM}/obsolete",
                                   {"expected_revision": 1, "host_id": fixture.HOST})[0], 200)
        _, obsolete, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertEqual(self.post(f"/v1/memory/items/{fixture.ITEM_2}/delete",
                                   {"expected_revision": 1, "host_id": fixture.HOST})[0], 200)
        _, deleted, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM_2}")
        self.assertNotEqual(obsolete["memory"]["engine_state"],
                            deleted["memory"]["engine_state"])
        self.assertEqual(obsolete["memory"]["engine_state"], "RETAINED")
        self.assertEqual(deleted["memory"]["engine_state"], "DELETE_PENDING")
        # Each readback bumped its own item's revision by exactly one.
        self.assertEqual(obsolete["memory"]["revision"], 2)
        self.assertEqual(deleted["memory"]["revision"], 2)

    def test_the_read_routes_are_served_unchanged(self) -> None:
        for path in ("/v1/health", "/v1/projects", "/v1/hosts", "/v1/meta/schema",
                     "/v1/actors", "/v1/memory/health", "/v1/memory/jobs",
                     f"/v1/memory/jobs/{fixture.JOB}", "/v1/memory/items",
                     f"/v1/memory/items/{fixture.ITEM}", "/v1/memory/traces",
                     f"/v1/memory/traces/{fixture.TRACE}"):
            with self.subTest(path=path):
                status, body, headers = call(self.port, path)
                self.assertEqual(status, 200, body)
                self.assertEqual(headers.get("X-P6-Fixture"), fixture.FIXTURE_HEADER)

    # -- 9. reflect -------------------------------------------------------------
    def test_reflect_success_has_the_eight_key_shape(self) -> None:
        status, body, _ = self.post("/v1/memory/reflect",
                                    {"project_id": fixture.PROJECT,
                                     "query": "what changed about the deployment?"})
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body), set(spec_for("/v1/memory/reflect")["response_shapes"][0]))
        self.assertEqual(len(body), 8)
        self.assertEqual(body["status"], "ok")
        self.assertIsNotNone(body["text"])
        self.assertIs(body["non_authoritative"], True)
        self.assertIsNone(body["error_code"])
        self.assertIsNotNone(body["safe_error_summary"])
        self.assertIsInstance(body["banks"], list)
        self.assertIsInstance(body["based_on_memory_ids"], list)
        self.assertRegex(body["trace_id"], fixture.ID_RE)
        self.assertTrue(body["trace_id"].startswith("mtr_"))

    def test_an_unauthorised_project_is_not_found(self) -> None:
        # `ProjectAccess.require` raises `NotFound("project", ...)` -> `project_not_found`.
        status, body, _ = self.post("/v1/memory/reflect",
                                    {"project_id": fixture.PROJECT_OTHER,
                                     "query": "what changed?"})
        self.assertEqual(status, 404, body)
        self.assertEqual(body["error"]["code"], "project_not_found")

    def test_a_malformed_project_id_is_400(self) -> None:
        status, body, _ = self.post("/v1/memory/reflect",
                                    {"project_id": "project-1", "query": "what changed?"})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["details"]["field"], "project_id")

    def test_a_bad_query_is_400(self) -> None:
        for query in ("", "   ", "x" * 4001, 17, None):
            with self.subTest(query=repr(query)[:20]):
                status, body, _ = self.post("/v1/memory/reflect",
                                            {"project_id": fixture.PROJECT, "query": query})
                self.assertEqual(status, 400, body)
                self.assertEqual(body["error"]["details"]["field"], "query")

    def test_a_non_integer_max_tokens_is_400(self) -> None:
        status, body, _ = self.post("/v1/memory/reflect",
                                    {"project_id": fixture.PROJECT,
                                     "query": "what changed?", "max_tokens": "many"})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["details"]["field"], "max_tokens")

    def test_reflect_accepts_the_optional_fields(self) -> None:
        status, body, _ = self.post("/v1/memory/reflect", allowed_body(
            "/v1/memory/reflect", max_tokens=128))
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body), 8)

    def test_the_artificial_agent_reflect_token_loses_the_summary_but_a_human_sees_it(self) -> None:
        """The withholding rule on a *write* reply, reachable only artificially.

        A real P4 key issuance would not grant `memory:reflect` to an agent, so the
        `agent-reflect` token in this suite is artificial: it exists only so the
        `_withhold_engine_text` rule — which P4 applies to every reply, writes included —
        can be observed on a write response. `for_actor` nulls `safe_error_summary` and keeps
        the key; it does not touch `text`, which is what P4's walker does too.
        """
        request = {"project_id": fixture.PROJECT, "query": "what changed?"}
        human_status, human, _ = self.post("/v1/memory/reflect", request, token="full")
        agent_status, agent, _ = self.post("/v1/memory/reflect", request, token="agent-reflect")
        self.assertEqual(human_status, 200, human)
        self.assertEqual(agent_status, 200, agent)
        self.assertIsNotNone(human["safe_error_summary"])
        self.assertIsNotNone(human["text"])
        self.assertIn("safe_error_summary", agent, "P4 nulls the value, never the key")
        self.assertIsNone(agent["safe_error_summary"])
        self.assertEqual(set(agent), set(spec_for("/v1/memory/reflect")["response_shapes"][0]))
        # Documented consequence of reusing P4's walker: only `safe_error_summary` is named.
        self.assertIsNotNone(agent["text"])

    # -- fixture-only controls (no token, not P4, not the product surface) --------
    def test_drift_produces_a_real_revision_conflict(self) -> None:
        """The control exists so the Gate can make `expected_revision` genuinely stale."""
        status, body, _ = call(self.port, f"{fixture.CONTROL_PREFIX}drift", None,
                               method="POST", body={"item": fixture.ITEM})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["control"], "drift")
        self.assertEqual(body["revision"], 2)
        status, body, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                    {"expected_revision": 1, "host_id": fixture.HOST})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "revision_conflict")
        self.assertEqual(body["error"]["details"]["current_revision"], 2)
        self.assertEqual(body["error"]["details"]["expected_revision"], 1)
        # A drift is not a command: it appends no Event and no command-log entry.
        _, state, _ = call(self.port, f"{fixture.CONTROL_PREFIX}state", None)
        self.assertEqual(state["commands"], [])
        self.assertEqual(state_item(state, fixture.ITEM)["revision"], 2)
        # The shape the browser Gate reads: lists, and an integer revision.
        self.assertIsInstance(state["items"], list)
        self.assertIsInstance(state["jobs"], list)
        self.assertIsInstance(state_item(state, fixture.ITEM)["revision"], int)

    def test_drift_accepts_the_query_form_and_refuses_a_bad_item(self) -> None:
        status, body, _ = call(self.port,
                               f"{fixture.CONTROL_PREFIX}drift?item={fixture.ITEM}", None,
                               method="POST")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["revision"], 2)
        status, body, _ = call(self.port, f"{fixture.CONTROL_PREFIX}drift", None,
                               method="POST", body={"item": fixture.ITEM_UNKNOWN})
        self.assertEqual(status, 404, body)
        status, body, _ = call(self.port, f"{fixture.CONTROL_PREFIX}drift", None,
                               method="POST", body={"item": "mem_short"})
        self.assertEqual(status, 400, body)

    def test_drop_next_applies_the_transition_then_the_same_event_id_replays(self) -> None:
        """The ordering this pins: replay beats a stale `expected_revision`.

        The first attempt applies for real (revision 1 -> 2) and its reply is dropped. The
        second attempt carries the *same, now stale* `expected_revision` and the same
        `event_id`: it must answer the 5-key replay, not `409 revision_conflict`, and the
        command log must still hold exactly one entry.
        """
        event = unique_event()
        request = {"expected_revision": 1, "host_id": fixture.HOST, "event_id": event}
        status, armed, _ = call(self.port, f"{fixture.CONTROL_PREFIX}drop-next", None,
                                method="POST")
        self.assertEqual(status, 200, armed)
        self.assertIs(armed["armed"], True)

        first_status, first, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin", request)
        self.assertEqual(first_status, 504, first)
        self.assertEqual(first["error"]["code"], "fixture_write_reply_dropped")

        # The write applied for real, even though the caller never saw the 200.
        _, item, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertIs(item["memory"]["pinned"], True)
        self.assertEqual(item["memory"]["revision"], 2)
        _, state, _ = call(self.port, f"{fixture.CONTROL_PREFIX}state", None)
        self.assertEqual(len(state["commands"]), 1)
        self.assertIs(state["drop_next_armed"], False)
        self.assertEqual(state["commands"][0], {"event_id": event, "command": "pin",
                                                "object_id": fixture.ITEM, "replayed": False})

        second_status, second, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin", request)
        self.assertEqual(second_status, 200, second)
        self.assertNotEqual(second_status, 409, "a replay must beat the stale revision")
        self.assertEqual(set(second), set(spec_for(
            f"/v1/memory/items/{fixture.ITEM}/pin")["response_shapes"][1]))
        self.assertEqual(len(second), 5)
        self.assertIs(second["replayed"], True)
        # The replay writes no second transition and adds no second log entry.
        _, item, _ = call(self.port, f"/v1/memory/items/{fixture.ITEM}")
        self.assertEqual(item["memory"]["revision"], 2)
        _, state, _ = call(self.port, f"{fixture.CONTROL_PREFIX}state", None)
        self.assertEqual(len(state["commands"]), 1)

    def test_state_reports_every_revision_and_the_command_log(self) -> None:
        _, state, _ = call(self.port, f"{fixture.CONTROL_PREFIX}state", None)
        self.assertEqual(state_item(state, fixture.ITEM),
                         {"memory_id": fixture.ITEM, "revision": 1, "pinned": False,
                          "status": "ACTIVE", "engine_state": "RETAINED"})
        self.assertEqual(state_job(state, fixture.JOB)["status"], "DONE")
        self.assertEqual(state["commands"], [])

        item_event, job_event = unique_event(), unique_event()
        self.assertEqual(self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                   {"expected_revision": 1, "host_id": fixture.HOST,
                                    "event_id": item_event})[0], 200)
        self.assertEqual(self.post(f"/v1/memory/jobs/{fixture.JOB_2}/retry",
                                   {"host_id": fixture.HOST,
                                    "event_id": job_event})[0], 200)
        _, state, _ = call(self.port, f"{fixture.CONTROL_PREFIX}state", None)
        self.assertEqual(state_item(state, fixture.ITEM)["revision"], 2)
        self.assertIs(state_item(state, fixture.ITEM)["pinned"], True)
        self.assertEqual(state_item(state, fixture.ITEM)["status"], "ACTIVE")
        self.assertEqual(state_job(state, fixture.JOB_2),
                         {"job_id": fixture.JOB_2, "status": "PENDING", "retry_count": 0})
        self.assertEqual(state["commands"],
                         [{"event_id": item_event, "command": "pin",
                           "object_id": fixture.ITEM, "replayed": False},
                          {"event_id": job_event, "command": "retry",
                           "object_id": fixture.JOB_2, "replayed": False}])

    def test_a_refused_command_adds_no_command_log_entry(self) -> None:
        status, body, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/delete",
                                    {"expected_revision": 1, "host_id": fixture.HOST,
                                     "event_id": unique_event()})
        self.assertEqual(status, 200, body)
        refused = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                            {"expected_revision": 2, "host_id": fixture.HOST,
                             "event_id": unique_event()})
        self.assertEqual(refused[0], 409, refused[1])
        _, state, _ = call(self.port, f"{fixture.CONTROL_PREFIX}state", None)
        self.assertEqual(len(state["commands"]), 1)
        self.assertEqual(state["commands"][0]["command"], "delete")

    def test_set_job_status_control_reaches_every_retry_branch(self) -> None:
        """The Gate uses this to make a DONE/RUNNING/FAILED job by hand, then click retry."""
        for status in ("RUNNING", "DONE"):
            with self.subTest(status=status):
                fixture.Handler.reset_state()
                reply_status, reply, _ = call(
                    self.port,
                    f"{fixture.CONTROL_PREFIX}set-job-status?job={fixture.JOB}&status={status}",
                    None, method="POST")
                self.assertEqual(reply_status, 200, reply)
                self.assertIs(reply["ok"], True)
                self.assertEqual(reply["job"]["job_id"], fixture.JOB)
                self.assertEqual(reply["job"]["status"], status)
                retry_status, retry, _ = self.post(f"/v1/memory/jobs/{fixture.JOB}/retry", {})
                self.assertEqual(retry_status, 409, retry)
                self.assertEqual(retry["error"]["code"], "invalid_state_transition")
        # And the other direction: a FAILED job set through the control retries for real.
        fixture.Handler.reset_state()
        reply_status, reply, _ = call(
            self.port, f"{fixture.CONTROL_PREFIX}set-job-status", None, method="POST",
            body={"job": fixture.JOB, "status": "FAILED"})
        self.assertEqual(reply_status, 200, reply)
        self.assertEqual(reply["job"]["status"], "FAILED")
        retry_status, retry, _ = self.post(f"/v1/memory/jobs/{fixture.JOB}/retry",
                                           {"host_id": fixture.HOST})
        self.assertEqual(retry_status, 200, retry)
        self.assertEqual(retry["job"]["status"], "PENDING")
        # A retry resets the budget, so a FAILED job reports retry_count 0 after it.
        self.assertEqual(retry["job"]["retry_count"], 0)

    def test_set_job_status_refuses_an_unknown_job_or_status(self) -> None:
        status, body, _ = call(self.port, f"{fixture.CONTROL_PREFIX}set-job-status", None,
                               method="POST",
                               body={"job": fixture.JOB_UNKNOWN, "status": "FAILED"})
        self.assertEqual(status, 404, body)
        status, body, _ = call(self.port, f"{fixture.CONTROL_PREFIX}set-job-status", None,
                               method="POST", body={"job": fixture.JOB, "status": "SLEEPING"})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["details"]["field"], "status")

    def test_the_controls_need_no_token(self) -> None:
        for path, method, body in (
                (f"{fixture.CONTROL_PREFIX}state", "GET", None),
                (f"{fixture.CONTROL_PREFIX}drop-next", "POST", {}),
                (f"{fixture.CONTROL_PREFIX}drift", "POST", {"item": fixture.ITEM}),
                (f"{fixture.CONTROL_PREFIX}set-job-status", "POST",
                 {"job": fixture.JOB, "status": "FAILED"})):
            with self.subTest(path=path):
                status, reply, _ = call(self.port, path, None, method=method, body=body)
                self.assertEqual(status, 200, reply)

    # -- labels -----------------------------------------------------------------
    def test_every_reply_carries_the_write_label(self) -> None:
        for path in write_paths():
            with self.subTest(path=path):
                # Even a refusal: a 401 from this process must be labelled simulated too.
                status, _, headers = self.post(path, {}, token=None)
                self.assertEqual(status, 401)
                self.assertEqual(headers.get("X-P6-Fixture"), fixture.FIXTURE_HEADER)
        status, _, headers = self.post("/v1/memory/reflect",
                                       {"project_id": fixture.PROJECT, "query": "hi"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-P6-Fixture"), fixture.FIXTURE_HEADER)


class TheCommandEventCanBeReadBack(HttpHarness):
    """`GET /v1/events/{evt}` — the audit readback the dashboard performs.

    The write response carries the command Event, but a client that trusts only its own
    response has not checked the audit log. The Gate re-reads the Event by id through the read
    surface, so the fixture has to serve it; the read fixture has no events route because its
    Gate never needed one.
    """

    def test_a_recorded_command_event_is_served_by_id(self) -> None:
        status, written, _ = self.post(f"/v1/memory/items/{fixture.ITEM}/pin",
                                       allowed_body(f"/v1/memory/items/{fixture.ITEM}/pin"))
        self.assertEqual(status, 200, written)
        event_id = written["event"]["event_id"]
        status, read, headers = call(self.port, f"/v1/events/{event_id}")
        self.assertEqual(status, 200, read)
        self.assertEqual(read["event"]["event_id"], event_id)
        self.assertEqual(read["event"], written["event"],
                         "the audit read must be the same record the response carried")
        self.assertEqual(headers.get("X-P6-Fixture"), fixture.FIXTURE_HEADER)

    def test_a_replayed_command_is_readable_under_the_same_id(self) -> None:
        path = f"/v1/memory/items/{fixture.ITEM}/pin"
        body = allowed_body(path)
        first = self.post(path, body)[1]
        replay = self.post(path, body)[1]
        self.assertTrue(replay["replayed"])
        status, read, _ = call(self.port, f"/v1/events/{body['event_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(read["event"], first["event"])

    def test_an_unknown_event_is_404_and_a_malformed_one_is_400(self) -> None:
        status, body, _ = call(self.port, f"/v1/events/evt_{'0' * 26}")
        self.assertEqual(status, 404, body)
        self.assertEqual(body["error"]["code"], "not_found")
        status, body, _ = call(self.port, "/v1/events/not-an-id")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["details"]["field"], "event_id")

    def test_the_events_route_needs_a_token(self) -> None:
        # Same rule as every other read: the proxy forwards it, Core authorises it.
        status, body, _ = call(self.port, f"/v1/events/evt_{'0' * 26}", token=None)
        self.assertEqual(status, 401, body)


if __name__ == "__main__":
    unittest.main()
