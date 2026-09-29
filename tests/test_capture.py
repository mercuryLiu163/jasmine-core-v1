"""The Codex UserPromptSubmit capture entry.

Scope under test: translate one Codex hook event into one Raw Event, idempotently,
and never break the user's turn. Interpretation, Task state and the full Agent
adapter are P1, P2 and P5 and are explicitly not tested here because they do not
exist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from support import DbTestCase  # noqa: E402
from test_api import ApiTestCase, free_port  # noqa: E402

from jasmine_core import db, ids, registry  # noqa: E402
from jasmine_core.api.server import Application, _Handler  # noqa: E402
from jasmine_core.capture import codex_user_prompt_submit as capture  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"
REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")
PROMPT = "P0-T07 固定测试短句 jasmine capture probe."


def codex_payload(prompt: str = PROMPT, *, session_id: str = "codex-session-probe-0001",
                  turn_id: str = "turn-1", hook: str = "UserPromptSubmit") -> dict:
    """The payload shape Codex sends to a UserPromptSubmit hook."""
    return {
        "hook_event_name": hook,
        "session_id": session_id,
        "cwd": "/tmp",
        "prompt": prompt,
        "turn_id": turn_id,
    }


class EventIdDerivation(unittest.TestCase):
    def test_the_id_has_the_frozen_shape(self) -> None:
        derived = capture.derive_event_id("s", "t")
        self.assertTrue(ids.is_id(derived, "evt"), derived)
        self.assertEqual(len(derived), 30)

    def test_the_same_turn_always_derives_the_same_id(self) -> None:
        self.assertEqual(capture.derive_event_id("s1", "t1"), capture.derive_event_id("s1", "t1"))

    def test_a_different_turn_or_session_derives_a_different_id(self) -> None:
        base = capture.derive_event_id("s1", "t1")
        self.assertNotEqual(base, capture.derive_event_id("s1", "t2"))
        self.assertNotEqual(base, capture.derive_event_id("s2", "t1"))

    def test_ids_are_well_spread(self) -> None:
        derived = {capture.derive_event_id("s", f"turn-{i}") for i in range(200)}
        self.assertEqual(len(derived), 200)


class BodyTranslation(unittest.TestCase):
    def build(self, payload: dict) -> dict:
        return capture.build_event_body(payload, host_id=HOST)

    def test_the_prompt_is_carried_verbatim(self) -> None:
        prompt = "  原文 with  double  spaces\ttab 中文 é  "
        body = self.build(codex_payload(prompt))
        self.assertEqual(body["payload"]["text"], prompt)

    def test_the_source_identity_is_recorded(self) -> None:
        body = self.build(codex_payload())
        self.assertEqual(body["source_system"], "codex")
        self.assertEqual(body["event_type"], "user.prompt")
        self.assertEqual(body["host_id"], HOST)
        self.assertEqual(body["source_event_id"], "codex-session-probe-0001:turn-1")
        self.assertEqual(body["payload"]["source_session_id"], "codex-session-probe-0001")
        self.assertEqual(body["payload"]["turn_id"], "turn-1")

    def test_a_payload_without_a_session_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build({"prompt": "x"})

    def test_an_empty_prompt_is_refused(self) -> None:
        for empty in ("", "   "):
            with self.assertRaises(ValueError):
                self.build(codex_payload(empty))

    def test_no_actor_id_is_sent_because_the_token_is_the_identity(self) -> None:
        # Sending one would be rejected as an unknown field, and the Core must
        # take the actor from the credential (ADR 0004 §1.3).
        self.assertNotIn("actor_id", self.build(codex_payload()))


class CaptureAgainstARealServer(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory(prefix="jasmine-core-capture-")
        self.addCleanup(self._tmpdir.cleanup)
        self.state_dir = Path(self._tmpdir.name)
        self.client = self._client()

    def _client(self):
        from jasmine_core.api.client import CoreClient

        return CoreClient(f"http://127.0.0.1:{self.port}", self.admin_token)

    def test_a_prompt_becomes_exactly_one_raw_event(self) -> None:
        result = capture.handle(codex_payload(), self.client, host_id=HOST,
                                state_dir=self.state_dir)
        self.assertTrue(result["captured"], result)
        self.assertFalse(result["replayed"])
        _, listed = self.call("GET", "/v1/events", token=self.admin_token)
        self.assertEqual(listed["count"], 1)
        event = listed["events"][0]
        self.assertEqual(event["payload"]["text"], PROMPT)
        self.assertEqual(event["event_type"], "user.prompt")
        self.assertEqual(event["source_system"], "codex")
        self.assertEqual(event["host_id"], HOST)
        self.assertEqual(event["actor_id"], ACTOR)
        self.assertEqual(event["source_event_id"], "codex-session-probe-0001:turn-1")
        self.assertIsNone(event["task_id"])
        self.assertIsNone(event["project_id"])

    def test_the_same_turn_delivered_twice_is_one_event(self) -> None:
        first = capture.handle(codex_payload(), self.client, host_id=HOST,
                               state_dir=self.state_dir)
        second = capture.handle(codex_payload(), self.client, host_id=HOST,
                                state_dir=self.state_dir)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["event_id"], second["event_id"])
        _, listed = self.call("GET", "/v1/events", token=self.admin_token)
        self.assertEqual(listed["count"], 1)

    def test_different_turns_are_different_events(self) -> None:
        capture.handle(codex_payload(turn_id="turn-1"), self.client, host_id=HOST,
                       state_dir=self.state_dir)
        capture.handle(codex_payload(turn_id="turn-2"), self.client, host_id=HOST,
                       state_dir=self.state_dir)
        _, listed = self.call("GET", "/v1/events", token=self.admin_token)
        self.assertEqual(listed["count"], 2)

    def test_another_hook_event_is_ignored(self) -> None:
        result = capture.handle(codex_payload(hook="Stop"), self.client, host_id=HOST,
                                state_dir=self.state_dir)
        self.assertFalse(result["captured"])
        _, listed = self.call("GET", "/v1/events", token=self.admin_token)
        self.assertEqual(listed["count"], 0)

    def test_an_unreachable_core_is_reported_not_raised(self) -> None:
        from jasmine_core.api.client import CoreClient

        offline = CoreClient("http://127.0.0.1:1", self.admin_token, timeout=0.2)
        result = capture.handle(codex_payload(), offline, host_id=HOST, state_dir=self.state_dir)
        self.assertFalse(result["captured"])
        self.assertEqual(result["reason"], "core unreachable")
        # The turn must not be blocked and the failure must be visible locally.
        self.assertTrue((self.state_dir / capture.LOG_NAME).exists())

    def test_a_refusal_from_the_core_is_logged_without_the_prompt(self) -> None:
        from jasmine_core.api.client import CoreClient

        wrong_scope = self.key(["events:read"], label="reader")
        refused = CoreClient(f"http://127.0.0.1:{self.port}", wrong_scope)
        result = capture.handle(codex_payload(), refused, host_id=HOST, state_dir=self.state_dir)
        self.assertFalse(result["captured"])
        self.assertIn("403", result["reason"])
        log = (self.state_dir / capture.LOG_NAME).read_text(encoding="utf-8")
        self.assertNotIn(PROMPT, log)
        self.assertNotIn(wrong_scope, log)

    def test_the_captured_text_is_survives_a_restart(self) -> None:
        # P0-T07 also requires reading the original text back after a restart.
        result = capture.handle(codex_payload(), self.client, host_id=HOST,
                                state_dir=self.state_dir)
        self._stop()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self.server.app = Application(self.db_path)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        status, body = self.call("GET", f"/v1/events/{result['event_id']}",
                                 token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(body["event"]["payload"]["text"], PROMPT)
        self.assertEqual(body["event"]["source_event_id"], "codex-session-probe-0001:turn-1")
        self.assertEqual(body["event"]["host_id"], HOST)
        self.assertEqual(body["event"]["actor_id"], ACTOR)


class CaptureAsASubprocess(DbTestCase):
    """The hook runs as its own process, reading stdin and writing stdout."""

    migrate_db = True

    def setUp(self) -> None:
        super().setUp()
        reg = registry.Registry(self.conn)
        with db.transaction(self.conn):
            reg.upsert_host(HOST)
            reg.upsert_actor(ACTOR, kind="human", home_host_id=HOST)
        from jasmine_core import auth

        self.token = auth.Auth(self.conn).issue_key(
            actor_id=ACTOR, label="hook", scopes=["events:write", "events:read"])["token"]
        self.port = free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self.server.app = Application(self.db_path)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)
        self.state = tempfile.TemporaryDirectory(prefix="jasmine-core-hook-")
        self.addCleanup(self.state.cleanup)

    def _shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def run_hook(self, payload: dict, extra_env: dict[str, str] | None = None
                 ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": REPO_SRC,
            "JASMINE_CORE_URL": f"http://127.0.0.1:{self.port}",
            "JASMINE_CORE_TOKEN": self.token,
            "JASMINE_CORE_HOST_ID": HOST,
            "JASMINE_CORE_STATE_DIR": self.state.name,
        })
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, "-m", "jasmine_core.capture.codex_user_prompt_submit"],
            input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30,
        )

    def test_the_hook_exits_zero_and_prints_an_empty_context_object(self) -> None:
        result = self.run_hook(codex_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        # P0 injects no context, so Codex must be told there is nothing to add.
        self.assertEqual(json.loads(result.stdout), {})
        self.assertTrue(json.loads(result.stderr.strip().splitlines()[-1])["captured"])

    def test_a_missing_token_does_not_break_the_turn(self) -> None:
        result = self.run_hook(codex_payload(), {"JASMINE_CORE_TOKEN": ""})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertIn("not set", result.stderr)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) AS n FROM events").fetchone()["n"], 0)

    def test_an_unreadable_payload_does_not_break_the_turn(self) -> None:
        env = dict(os.environ)
        env.update({"PYTHONPATH": REPO_SRC,
                    "JASMINE_CORE_STATE_DIR": self.state.name})
        result = subprocess.run(
            [sys.executable, "-m", "jasmine_core.capture.codex_user_prompt_submit"],
            input="not json at all", capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0)
        # stdout is always the Codex context object; the reason is on stderr.
        self.assertEqual(json.loads(result.stdout), {})
        self.assertFalse(json.loads(result.stderr.strip().splitlines()[-1])["captured"])

    def test_a_dead_core_does_not_break_the_turn(self) -> None:
        result = self.run_hook(codex_payload(), {"JASMINE_CORE_URL": "http://127.0.0.1:1"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})


if __name__ == "__main__":
    unittest.main()
