"""The simulated P3 read fixture: shapes, labels, and the negatives it must produce.

The fixture exists because P6-03a's dependency is missing, so its honesty is the whole
point. Two properties matter and are checked here rather than asserted in prose:

1. every payload matches the **frozen contract** exactly — a drifted fixture would make
   every screenshot evidence for a shape nobody agreed to, so the builders assert the
   field set at construction time;
2. every reply is **labelled** as simulated, and the negative paths a browser run relies
   on (unknown id, malformed id, missing scope, a Task with no checkpoint) really produce
   the statuses the views branch on.

The HTTP cases start the fixture on an ephemeral port, so the tests exercise the same
handler the click-through Gate talks to.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "p3_read_fixture", ROOT / "scripts" / "p6" / "p3_read_fixture.py")
fixture = importlib.util.module_from_spec(_spec)
sys.modules["p3_read_fixture"] = fixture
_spec.loader.exec_module(fixture)

CONTRACT = json.loads((ROOT / "web" / "contracts" / "p3-continuity-read.v1.json")
                      .read_text(encoding="utf-8"))


def call(port: int, path: str, token: str | None = "p3fixture-token") -> tuple[int, dict, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode()), dict(error.headers)


class PayloadShapes(unittest.TestCase):
    def test_every_payload_matches_the_frozen_field_set(self) -> None:
        # The builders assert internally too; this states the expectation where a reader
        # of the tests will see it, and fails loudly if that assertion is ever removed.
        checkpoint = fixture.checkpoint_payload(
            CONTRACT, ident=fixture.CHECKPOINT, revision=1, reason="MANUAL",
            source=fixture.SOURCE_EVENT, command=fixture.COMMAND_EVENT, note=None)
        self.assertEqual(set(checkpoint), set(CONTRACT["response_fields"]["checkpoint"]))
        self.assertEqual(set(fixture.resume_payload(CONTRACT)),
                         set(CONTRACT["response_fields"]["resume"]))
        self.assertEqual(set(fixture.context_payload(CONTRACT)),
                         set(CONTRACT["response_fields"]["context"]))

    def test_a_checkpoint_without_a_note_carries_null_not_a_fabricated_one(self) -> None:
        without = fixture.checkpoint_payload(
            CONTRACT, ident=fixture.CHECKPOINT, revision=1, reason="MANUAL",
            source=fixture.SOURCE_EVENT, command=fixture.COMMAND_EVENT, note=None)
        self.assertIsNone(without["note"])

    def test_the_resume_note_denies_its_own_authority(self) -> None:
        note = fixture.resume_payload(CONTRACT)["note"]
        self.assertIs(note["authority"], False)
        self.assertIs(note["historical"], True)


class OverHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = CONTRACT
        fixture.Handler.contract = CONTRACT
        fixture.Handler.tokens = {
            "full": set(fixture.ALL_SCOPES),
            "limited": {"objects:read", "checkpoint:read"},
        }
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_the_four_reads_answer_200(self) -> None:
        for path in (f"/v1/checkpoints/{fixture.CHECKPOINT}",
                     f"/v1/context/{fixture.PACK}",
                     f"/v1/resumes/{fixture.RESUME}",
                     f"/v1/tasks/{fixture.TASK}/checkpoints/latest"):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path, "full")
                self.assertEqual(status, 200)
                key = ("resume" if "/resumes/" in path else
                       "context" if "/context/" in path else "checkpoint")
                self.assertIn(key, body)

    def test_every_reply_is_labelled_simulated(self) -> None:
        # The label is what stops a screenshot from this fixture being read as evidence
        # about a real Core.
        status, _, headers = call(self.port, f"/v1/checkpoints/{fixture.CHECKPOINT}", "full")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-P6-Fixture"), fixture.FIXTURE_HEADER)

    def test_a_task_with_no_checkpoint_is_200_and_null(self) -> None:
        status, body, _ = call(
            self.port, f"/v1/tasks/{fixture.TASK_EMPTY}/checkpoints/latest", "full")
        self.assertEqual(status, 200)
        self.assertIsNone(body["checkpoint"])

    def test_unknown_ids_are_404_and_malformed_ones_are_400(self) -> None:
        unknown = f"ckp_{'01M4B6VGC29ESRDRQR62DX8AK7'}"
        for path, expected, code in (
            (f"/v1/checkpoints/{unknown}", 404, "not_found"),
            (f"/v1/context/ctx_{'01M4B6VGC29ESRDRQR62DX8AK7'}", 404, "not_found"),
            (f"/v1/resumes/rms_{'01M4B6VGC29ESRDRQR62DX8AK7'}", 404, "not_found"),
            ("/v1/checkpoints/ckp_short", 400, "invalid_request"),
            ("/v1/context/ctx_short", 400, "invalid_request"),
            ("/v1/resumes/rms_short", 400, "invalid_request"),
        ):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path, "full")
                self.assertEqual((status, body["error"]["code"]), (expected, code))

    def test_a_missing_scope_is_403_and_names_itself(self) -> None:
        status, body, _ = call(self.port, f"/v1/context/{fixture.PACK}", "limited")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertEqual(body["error"]["details"]["required"], "context:read")
        # A resume needs two scopes, and the one it lacks is the one it must name.
        status, body, _ = call(self.port, f"/v1/resumes/{fixture.RESUME}", "limited")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["details"]["required"], "resume:read")

    def test_no_token_is_401(self) -> None:
        status, body, _ = call(self.port, f"/v1/checkpoints/{fixture.CHECKPOINT}", None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")

    def test_the_build_shapes_are_not_operations_in_this_slice(self) -> None:
        """`GET` on a build shape must not look like a served read.

        The two cases differ, and both match what real P3 would do, which is why they are
        asserted separately rather than as a blanket 404:

        * `GET /v1/context/build` matches P3's `GET /v1/context/{id}` route with the id
          `"build"`, and `ContextStore.get` rejects a non-`ctx_` id — `400 invalid_request`;
        * `GET /v1/checkpoints` matches no route at all — `404 not_found`.
        """
        status, body, _ = call(self.port, "/v1/context/build", "full")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_request"))
        status, body, _ = call(self.port, "/v1/checkpoints", "full")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))


if __name__ == "__main__":
    unittest.main()
