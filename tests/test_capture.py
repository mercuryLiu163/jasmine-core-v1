"""The Codex UserPromptSubmit capture entry.

Scope under test: translate one Codex hook event into one Raw Event, idempotently,
and never break the user's turn. Interpretation, Task state and the full Agent
adapter are P1, P2 and P5 and are explicitly not tested here because they do not
exist.
"""

from __future__ import annotations

import hashlib
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
    def derive(self, prompt: str = "hello", session: str = "s1", turn: str = "t1",
               host: str = HOST) -> str:
        return capture.derive_event_id(host, session, turn, prompt)

    def test_the_id_has_the_frozen_shape(self) -> None:
        derived = self.derive()
        self.assertTrue(ids.is_id(derived, "evt"), derived)
        self.assertEqual(len(derived), 30)

    def test_the_same_turn_always_derives_the_same_id(self) -> None:
        self.assertEqual(self.derive(), self.derive())

    def test_a_different_turn_session_host_or_prompt_derives_a_different_id(self) -> None:
        base = self.derive()
        self.assertNotEqual(base, self.derive(turn="t2"))
        self.assertNotEqual(base, self.derive(session="s2"))
        self.assertNotEqual(base, self.derive(host="hst_01M3PGHM5X336XRWCS1D2B3N2F"))
        self.assertNotEqual(base, self.derive(prompt="a different message"))

    def test_two_devices_capturing_the_same_turn_do_not_collide(self) -> None:
        # Omitting host_id made both devices derive the same id; the differing
        # host_id then changed the body hash and the second prompt was rejected
        # as a conflict and lost.
        self.assertNotEqual(
            self.derive(host="hst_01K742SG00BMSDET9BTP151RAR"),
            self.derive(host="hst_01M3PGHM5X336XRWCS1D2B3N2F"),
        )

    def test_every_character_carries_its_full_five_bits(self) -> None:
        # A byte-aligned extraction left most positions with one or two
        # significant bits: 98 bits of entropy in a 130-bit id.
        derived = [self.derive(prompt=f"p{index}") for index in range(4000)]
        for position in range(4, 30):
            with self.subTest(position=position):
                self.assertEqual(len({value[position] for value in derived}), 32)

    def test_ids_are_distinct_across_many_prompts(self) -> None:
        self.assertEqual(len({self.derive(prompt=f"p{index}") for index in range(2000)}), 2000)


class BodyTranslation(unittest.TestCase):
    def build(self, payload: dict) -> dict:
        return capture.build_event_body(payload, host_id=HOST)

    def test_the_prompt_is_carried_verbatim(self) -> None:
        prompt = "  原文 with  double  spaces\ttab 中文 é  "
        body = self.build(codex_payload(prompt))
        self.assertEqual(body["payload"]["text"], prompt)

    def test_the_source_identity_includes_the_device(self) -> None:
        body = self.build(codex_payload())
        self.assertEqual(body["host_id"], HOST)
        self.assertTrue(body["event_id"].startswith("evt_"))

    def test_the_source_identity_is_recorded(self) -> None:
        body = self.build(codex_payload())
        self.assertEqual(body["source_system"], "codex")
        self.assertEqual(body["event_type"], "user.prompt")
        self.assertEqual(body["host_id"], HOST)
        self.assertEqual(body["source_event_id"], "codex-session-probe-0001:turn-1")
        self.assertEqual(body["payload"]["source_session_id"], "codex-session-probe-0001")
        self.assertEqual(body["payload"]["turn_id"], "turn-1")

    def test_the_working_directory_is_not_part_of_the_turn_identity(self) -> None:
        # Codex reports cwd inconsistently between deliveries of one turn; it is
        # provenance, not identity, so it must not be in the hashed payload.
        body = self.build(codex_payload())
        self.assertNotIn("cwd", body["payload"])
        self.assertNotIn("cwd", body)

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
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    @staticmethod
    def _txn(conn):
        return db.transaction(conn)

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

    def test_the_same_turn_from_a_different_device_is_reported_as_a_source_duplicate(self) -> None:
        # Two devices seeing one Codex turn is the same *source* event reported
        # twice, so ADR 0003 §1.3's (source_system, source_event_id) guard
        # refuses the second one. Before the derived id included host_id, both
        # devices derived the same event_id and the refusal was mislabelled as
        # `event_id_conflict`, i.e. a content conflict, which it is not.
        other_host = "hst_01M3PGHM5X336XRWCS1D2B3N2F"
        with self._txn(self.conn):
            registry.Registry(self.conn).upsert_host(other_host)
        first = capture.handle(codex_payload(), self.client, host_id=HOST, state_dir=self.state_dir)
        second = capture.handle(codex_payload(), self.client, host_id=other_host,
                                state_dir=self.state_dir)
        self.assertTrue(first["captured"], first)
        self.assertFalse(second["captured"], second)
        self.assertIn("409 source_event_duplicate", second["reason"])
        _, listed = self.call("GET", "/v1/events", token=self.admin_token)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["events"][0]["host_id"], HOST)
        # The refusal must be visible locally, not silent.
        log = (self.state_dir / capture.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("source_event_duplicate", log)

    def test_the_same_turn_retried_from_another_directory_is_a_replay(self) -> None:
        # cwd travels in the hashed payload, so it used to turn a retry into a
        # conflict; the derived id now covers everything the hash does.
        first = capture.handle(codex_payload(), self.client, host_id=HOST, state_dir=self.state_dir)
        moved = dict(codex_payload(), cwd="/somewhere/else")
        second = capture.handle(moved, self.client, host_id=HOST, state_dir=self.state_dir)
        self.assertTrue(second["captured"], second)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["event_id"], second["event_id"])

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


class InvocationTrace(DbTestCase):
    """The trace is what tells "Codex never ran us" from "the Core was down"."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-trace-")
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)
        self.token = "x" * 40
        self.payload = codex_payload(session_id="trace-session", turn_id="trace-1")

    def run_entry(self, payload: dict | str, *, token: str | None = None,
                  host: str = HOST,
                  env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        # token=None means the real (fake) token; pass "" to test the unset case.
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": REPO_SRC,
            "JASMINE_CORE_TOKEN": self.token if token is None else token,
            "JASMINE_CORE_HOST_ID": host,
            "JASMINE_CORE_STATE_DIR": str(self.state),
            "JASMINE_CORE_URL": "http://127.0.0.1:1",
        })
        env.update(env_extra or {})
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            [sys.executable, "-m", "jasmine_core.capture.codex_user_prompt_submit"],
            input=raw, capture_output=True, text=True, env=env, timeout=30,
        )

    def trace_lines(self) -> list[dict]:
        path = self.state / capture.TRACE_NAME
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_a_successful_capture_leaves_a_trace_with_no_prompt_text(self) -> None:
        result = self.run_entry(self.payload, env_extra={
            "JASMINE_CORE_URL": "http://127.0.0.1:1"})
        self.assertEqual(json.loads(result.stdout), {})
        lines = self.trace_lines()
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0]["captured"])
        self.assertEqual(lines[0]["hook_event_name"], "UserPromptSubmit")
        self.assertTrue(lines[0]["request_id"].startswith("aud_"))
        self.assertTrue(lines[0]["host_id_configured"])
        self.assertTrue(lines[0]["token_configured"])
        raw = (self.state / capture.TRACE_NAME).read_text(encoding="utf-8")
        self.assertNotIn(PROMPT, raw)

    def test_every_failure_path_also_leaves_a_trace(self) -> None:
        for label, kwargs in (
            ("unreadable payload", {"payload": "not json"}),
            ("payload is not an object", {"payload": "[1, 2, 3]"}),
            ("no token", {"payload": self.payload, "token": ""}),
            ("no host", {"payload": self.payload, "host": ""}),
            ("empty prompt", {"payload": codex_payload(prompt="   ")}),
            ("no session", {"payload": {"hook_event_name": "UserPromptSubmit", "prompt": "x"}}),
        ):
            with self.subTest(label=label):
                self.run_entry(kwargs["payload"], token=kwargs.get("token", ""),
                               host=kwargs.get("host", HOST))
                self.assertTrue(self.trace_lines(), f"{label} left no trace")
                (self.state / capture.TRACE_NAME).unlink()

    def test_the_trace_records_the_turn_digest_so_the_gate_can_match_it(self) -> None:
        self.run_entry(self.payload)
        line = self.trace_lines()[0]
        self.assertEqual(len(line["source_turn_sha256"]), 32)
        self.assertEqual(line["source_session_sha256"],
                         hashlib.sha256(self.payload["session_id"].encode()).hexdigest())
        self.assertEqual(line["turn_sha256"],
                         hashlib.sha256(self.payload["turn_id"].encode()).hexdigest())
        self.assertEqual(line["prompt_sha256"],
                         hashlib.sha256(self.payload["prompt"].encode()).hexdigest())
        self.assertEqual(line["core_url"], "http://127.0.0.1:1")
        # Same turn, same digest; a different turn, a different one.
        self.run_entry(codex_payload(session_id="trace-session", turn_id="trace-2"))
        self.assertNotEqual(self.trace_lines()[0]["source_turn_sha256"],
                            self.trace_lines()[1]["source_turn_sha256"])

    def test_a_capture_against_a_live_core_records_the_event_id(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", free_port()), _Handler)
        server.app = Application(self.db_path)
        server.daemon_threads = True
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        from jasmine_core import auth, registry

        reg = registry.Registry(self.conn)
        with db.transaction(self.conn):
            reg.upsert_host(HOST)
            reg.upsert_actor(ACTOR, kind="human", home_host_id=HOST)
        real_token = auth.Auth(self.conn).issue_key(
            actor_id=ACTOR, label="trace", scopes=["events:write"])["token"]
        result = self.run_entry(self.payload, token=real_token,
                                env_extra={"JASMINE_CORE_URL": f"http://127.0.0.1:{port}"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        line = self.trace_lines()[0]
        self.assertTrue(line["captured"], line)
        self.assertTrue(line["event_id"].startswith("evt_"))
        self.assertIsNone(line["reason"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) AS n FROM events").fetchone()["n"], 1)


if __name__ == "__main__":
    unittest.main()
