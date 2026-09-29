"""Fixture-only tests of P0-T07 predicates. These never count as real Gate evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
spec = importlib.util.spec_from_file_location("p0_t07_gate", ROOT/"scripts/p0_t07_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
acceptance_spec = importlib.util.spec_from_file_location("p0_acceptance", ROOT/"scripts/p0-acceptance.py")
acceptance = importlib.util.module_from_spec(acceptance_spec)
acceptance_spec.loader.exec_module(acceptance)
from jasmine_core.capture.codex_user_prompt_submit import derive_event_id, _turn_digest

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"


class GatePredicates(unittest.TestCase):
    def test_exact_hook_configuration_alignment(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            db, state, python = base/"core.db", base/"state", Path(sys.executable).resolve()
            wrapper = ROOT/"scripts/jasmine-capture-hook.sh"
            argv = [str(wrapper), "--host", HOST, "--db", str(db),
                    "--state-dir", str(state), "--python", str(python)]
            hooks = base/"hooks.json"
            hooks.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": shlex.join(argv)}]}]}}))
            entry = gate.parse_hook(hooks, state, db, python)
            self.assertEqual(entry["host_id"], HOST)
            with self.assertRaisesRegex(gate.Blocked, "--db"):
                gate.parse_hook(hooks, state, base/"other.db", python)

    def test_trust_record_is_advisory_and_parsed_as_toml(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp)/"config.toml"
            entry = {"path": "/tmp/hooks.json", "index": 0, "sub": 0}
            key = "/tmp/hooks.json:user_prompt_submit:0:0"
            config.write_text(f'[hooks.state."{key}"]\ntrusted_hash = "sha256:old"\n')
            self.assertTrue(gate.trust_record_present(entry, config))
            config.write_text(f'[hooks.state."{key}"]\nother = "yes"\n')
            self.assertFalse(gate.trust_record_present(entry, config))

    def test_only_this_session_prompt_and_endpoint_trace_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            trace_file = Path(temp)/"trace.log"
            session, prompt, url = "real-session", "nonce prompt", "http://127.0.0.1:43210"
            digest = hashlib.sha256(prompt.encode()).hexdigest()
            rows = [
                {"source_session_sha256": hashlib.sha256(session.encode()).hexdigest(), "prompt_sha256": digest, "turn_sha256": "old",
                 "hook_event_name": "UserPromptSubmit", "core_url": url},
                {"source_session_sha256": hashlib.sha256(b"other").hexdigest(), "prompt_sha256": digest, "turn_sha256": "other",
                 "hook_event_name": "UserPromptSubmit", "core_url": url},
                {"source_session_sha256": hashlib.sha256(session.encode()).hexdigest(), "prompt_sha256": digest, "turn_sha256": "wrong-port",
                 "hook_event_name": "UserPromptSubmit", "core_url": "http://127.0.0.1:8787"},
                {"source_session_sha256": hashlib.sha256(session.encode()).hexdigest(), "prompt_sha256": digest, "turn_sha256": "this-turn",
                 "hook_event_name": "UserPromptSubmit", "core_url": url},
            ]
            trace_file.write_text(json.dumps(rows[0])+"\n")
            offset = trace_file.stat().st_size
            with trace_file.open("a") as stream:
                for row in rows[1:]:
                    stream.write(json.dumps(row)+"\n")
            found = gate.matching_trace(trace_file, offset, session, prompt, url)
            self.assertEqual([item["turn_sha256"] for item in found], ["this-turn"])

    def test_event_must_be_fresh_and_bound_to_trace_host_actor_and_turn(self):
        session, turn, prompt = "real-session", "turn-7", "nonce prompt"
        event_id = derive_event_id(HOST, session, turn, prompt)
        trace = {"turn_sha256": hashlib.sha256(turn.encode()).hexdigest(), "event_id": event_id,
                 "source_turn_sha256": _turn_digest({"session_id": session, "turn_id": turn}),
                 "captured": True, "replayed": False}
        event = {"event_id": event_id, "seq": 1001, "event_type": "user.prompt",
                 "source_system": "codex", "source_event_id": f"{session}:{turn}",
                 "host_id": HOST, "actor_id": ACTOR,
                 "payload": {"text": prompt, "source_session_id": session, "turn_id": turn}}
        gate.verify_event(event, trace, session, prompt, HOST, ACTOR, 1000)
        with self.assertRaisesRegex(gate.Failed, "predates"):
            gate.verify_event(event, trace, session, prompt, HOST, ACTOR, 1001)
        with self.assertRaisesRegex(gate.Failed, "actor_id"):
            gate.verify_event(event, trace, session, prompt, HOST, "act_other", 1000)
        with self.assertRaisesRegex(gate.Failed, "turn"):
            gate.verify_event(event, trace, "other-session", prompt, HOST, ACTOR, 1000)

    def test_missing_optional_turn_id_still_binds_session_prompt_and_event(self):
        session, prompt = "real-session", "fixed prompt"
        event_id = derive_event_id(HOST, session, "", prompt)
        trace = {"turn_sha256": None, "event_id": event_id,
                 "source_turn_sha256": _turn_digest({"session_id": session}),
                 "captured": True, "replayed": False}
        event = {"event_id": event_id, "seq": 3, "event_type": "user.prompt",
                 "source_system": "codex", "source_event_id": None,
                 "host_id": HOST, "actor_id": ACTOR,
                 "payload": {"text": prompt, "source_session_id": session, "turn_id": None}}
        gate.verify_event(event, trace, session, prompt, HOST, ACTOR, 2)

    def test_session_requires_one_json_thread_started_record(self):
        self.assertEqual(gate.codex_session('{"type":"thread.started","thread_id":"s1"}\n'), "s1")
        with self.assertRaises(gate.Blocked):
            gate.codex_session('{"type":"turn.started"}\n')


class AcceptanceConsumerFixtures(unittest.TestCase):
    """Synthetic markers test classification only; they are never Gate evidence."""

    def marker(self, outcome: str) -> dict:
        event = {"event_id": "evt_example", "seq": 8, "host_id": HOST,
                 "actor_id": ACTOR, "payload": {"source_session_id": "session-1",
                                              "text": "fixed short prompt"}}
        return {"gate": "P0-T07", "outcome": outcome, "code_commit": "fixture-commit",
                "code_dirty": False, "schema_version": acceptance.SCHEMA_VERSION,
                "runtime": {"python": "fixture"}, "blocked": outcome == "BLOCKED",
                "captured": outcome == "PASS", "prompt": "fixed short prompt",
                "source_session_id": "session-1", "host_id": HOST, "actor_id": ACTOR,
                "baseline_seq": 7, "core_first_pid": 100, "core_second_pid": 101,
                "event_before_restart": event, "readback_after_restart": event,
                "matching_invocation_trace": [{
                    "captured": True, "replayed": False, "event_id": event["event_id"],
                    "source_session_sha256": hashlib.sha256(b"session-1").hexdigest(),
                    "prompt_sha256": hashlib.sha256(b"fixed short prompt").hexdigest()}],
                "problems": ["fixture failure"] if outcome == "FAIL" else [],
                "failure_class": "capture" if outcome == "FAIL" else None,
                "failure_output": "fixture failure" if outcome == "FAIL" else None,
                "reason": "fixture blocked" if outcome == "BLOCKED" else None}

    def consume(self, marker: dict | str, *, commit: str = "fixture-commit",
                clean: bool = True) -> str:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"p0-t07-result.json"
            path.write_text(marker if isinstance(marker, str) else json.dumps(marker))
            return acceptance.case_t07(Path(temp), commit, clean).verdict

    def test_pass_fail_blocked_markers_are_distinct(self):
        for outcome in ("PASS", "FAIL", "BLOCKED"):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.consume(self.marker(outcome)), outcome)

    def test_stale_dirty_and_malformed_markers_are_blocked(self):
        good = self.marker("PASS")
        self.assertEqual(self.consume(good, commit="new-commit"), "BLOCKED")
        self.assertEqual(self.consume(good, clean=False), "BLOCKED")
        good["code_dirty"] = True
        self.assertEqual(self.consume(good), "BLOCKED")
        bad_payload = self.marker("PASS")
        bad_payload["event_before_restart"]["payload"] = None
        self.assertEqual(self.consume(bad_payload), "BLOCKED")
        self.assertEqual(self.consume("{invalid"), "BLOCKED")


if __name__ == "__main__":
    unittest.main()
