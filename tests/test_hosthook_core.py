"""Host Hook Protocol, Core side (ADR 0017). Component level only.

Everything here uses constructed requests against a scratch Core. Nothing in
this module shows that any real agent calls a hook, honours a deny, or shows
injected context to a model.
"""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from hosthook_support import AGENT, SYSTEM, HostHookHttp

from jasmine_core import auth, db, ids
from test_api import HOST


class Envelope(HostHookHttp):
    def test_agent_actor_cannot_hold_the_scope(self) -> None:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with self.assertRaises(Exception) as got:
            auth.Auth(conn).issue_key(actor_id=AGENT, label="x", scopes=["hosthook:report"])
        self.assertEqual(got.exception.code, "forbidden_actor_kind")

    def test_missing_scope_and_missing_token(self) -> None:
        body = self.request("session.end", {})
        code, refusal = self.call("POST", "/v1/host-hooks/events", body, token=self.manage)
        self.assertEqual((code, refusal["error"]["code"]), (403, "forbidden_scope"))
        code, refusal = self.call("POST", "/v1/host-hooks/events", body)
        self.assertEqual((code, refusal["error"]["code"]), (401, "unauthenticated"))

    def test_shape_validation(self) -> None:
        good = self.request("session.end", {})
        cases = {
            "version": {**good, "protocol_version": "2"},
            "event": {**good, "event": "tool.sideways"},
            "trust": {**good, "trust": "TRUSTED"},
            "unknown field": {**good, "extra": 1},
            "delivery id": {**good, "delivery_id": "has space"},
            "caps hash": {**good, "adapter": {**good["adapter"], "capabilities_sha256": "xyz"}},
            "payload field": {**good, "payload": {"bogus": 1}},
        }
        expected = {"version": "host_hook_protocol_unsupported"}
        for name, body in cases.items():
            with self.subTest(name):
                code, refusal = self.send(body)
                self.assertEqual(code, 400, refusal)
                self.assertEqual(refusal["error"]["code"], expected.get(name, "invalid_request"))

    def test_tool_payload_validation(self) -> None:
        bad = [{"tool": "bash", "native_tool": "Bash", "input": {}},
               {"tool": "shell", "native_tool": "Bash", "input": "ls"},
               {"tool": "shell", "native_tool": "Bash", "input": {}, "path": "relative/x"},
               {"tool": "shell", "native_tool": "Bash", "input": {}, "paths": ["/a"] * 9}]
        for payload in bad:
            with self.subTest(payload):
                code, refusal = self.send(self.request("tool.before", payload))
                self.assertEqual((code, refusal["error"]["code"]), (400, "invalid_request"))

    def test_references_never_guessed(self) -> None:
        missing = ids.new_id("tsk")
        code, refusal = self.send(self.request("session.end", {}, binding={"task_id": missing}))
        self.assertEqual((code, refusal["error"]["code"]), (404, "task_not_found"))
        other = self.call("POST", "/v1/projects", {"name": "other", "host_id": HOST}, token=self.manage)[1]
        code, refusal = self.send(self.request("session.end", {}, binding={
            "project_id": other["object"]["project_id"], "task_id": self.task}))
        self.assertEqual((code, refusal["error"]["code"]), (400, "invalid_request"))
        # An unknown host cannot be this actor's home host.
        code, refusal = self.send(self.request("session.end", {}, host_id=ids.new_id("hst")))
        self.assertEqual((code, refusal["error"]["code"]), (403, "actor_mismatch"))

    def test_actor_must_be_homed_on_the_stated_host(self) -> None:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        other_host = ids.new_id("hst")
        with db.transaction(conn):
            from jasmine_core import registry
            registry.Registry(conn).upsert_host(other_host)
        code, refusal = self.send(self.request("session.end", {}, host_id=other_host))
        self.assertEqual((code, refusal["error"]["code"]), (403, "actor_mismatch"))


class Idempotency(HostHookHttp):
    def test_replay_returns_original_and_conflict_is_409(self) -> None:
        body = self.request("prompt.submit", {"text": "once"}, delivery="test:prompt:1")
        code, first = self.send(body, token=self.human_hook_token)
        self.assertEqual(code, 201, first)
        self.assertFalse(first["replayed"])
        code, again = self.send(body, token=self.human_hook_token)
        self.assertEqual(code, 200)
        self.assertTrue(again["replayed"])
        self.assertEqual({**again, "replayed": False}, first)
        different = copy.deepcopy(body)
        different["payload"]["text"] = "twice"
        code, conflict = self.send(different, token=self.human_hook_token)
        self.assertEqual((code, conflict["error"]["code"]), (409, "host_hook_delivery_conflict"))
        events = self.db_rows("SELECT * FROM events WHERE source_system='host-hook-v1' AND event_type='user.prompt'")
        self.assertEqual(len(events), 1)

    def test_receipt_table_is_append_only(self) -> None:
        self.send(self.request("session.end", {}, delivery="test:end:1"))
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE host_hook_deliveries SET trust='AGENT_REPORTED'")
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM host_hook_deliveries")


class SessionAndPrompt(HostHookHttp):
    def test_session_start_records_event_and_injects_context(self) -> None:
        code, out = self.send(self.request("session.start", {"source": "startup"}))
        self.assertEqual(code, 201, out)
        self.assertEqual(out["degraded"], [])
        self.assertIn("Hook goal", out["inject_context"]["text"])
        self.assertTrue(out["inject_context"]["context_pack_id"].startswith("ctx_"))
        row = self.db_rows("SELECT * FROM events WHERE event_type='session.started'")[0]
        self.assertEqual((row["source_system"], row["task_id"], row["actor_kind"]), ("host-hook-v1", self.task, "system"))
        self.assertIn(row["event_id"], out["recorded"]["event_ids"])
        packs = self.db_rows("SELECT reason FROM context_packs")
        self.assertEqual([p["reason"] for p in packs], ["SESSION_START"])

    def test_without_task_binding_only_a_raw_event(self) -> None:
        code, out = self.send(self.request("session.start", {}, task=False))
        self.assertEqual(code, 201)
        self.assertIn("no_task_binding", out["degraded"])
        self.assertIsNone(out["inject_context"])
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM context_packs")[0]["n"], 0)
        row = self.db_rows("SELECT * FROM events WHERE event_type='session.started'")[0]
        self.assertIsNone(row["task_id"])

    def test_prompt_is_verbatim_and_human_attributed_with_human_key(self) -> None:
        text = "  keep\tevery   byte é中文\n\n  "
        code, out = self.send(self.request("prompt.submit", {"text": text}), token=self.human_hook_token)
        self.assertEqual(code, 201, out)
        row = self.db_rows("SELECT * FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1'")[0]
        self.assertEqual(row["actor_kind"], "human")
        import json
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["text"], text)
        self.assertEqual(payload["trust"], "NATIVE_HOOK")
        self.assertEqual(payload["native"]["session_id"], "native-session-1")
        self.assertIsNotNone(out["inject_context"])
        self.assertEqual(self.db_rows("SELECT reason FROM context_packs")[0]["reason"], "USER_PROMPT")

    def test_prompt_with_system_key_is_recorded_but_not_passed_off_as_human(self) -> None:
        code, out = self.send(self.request("prompt.submit", {"text": "hello"}))
        self.assertEqual(code, 201, out)
        self.assertIn("prompt_not_human_attributed", out["degraded"])
        self.assertIsNone(out["inject_context"])
        row = self.db_rows("SELECT * FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1'")[0]
        self.assertEqual(row["actor_kind"], "system")

    def test_oversize_prompt_is_refused_not_truncated(self) -> None:
        code, refusal = self.send(self.request("prompt.submit", {"text": "x" * (256 * 1024 + 1)}),
                                  token=self.human_hook_token)
        self.assertEqual((code, refusal["error"]["code"]), (413, "payload_too_large"))
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM events WHERE event_type='user.prompt' "
                                      "AND source_system='host-hook-v1'")[0]["n"], 0)

    def test_context_failure_degrades_but_keeps_the_event(self) -> None:
        from unittest.mock import patch
        from jasmine_core.context import builder
        with patch.object(builder.ContextBuilder, "build", side_effect=__import__("jasmine_core").errors.CoreError("boom")):
            code, out = self.send(self.request("session.start", {}))
        self.assertEqual(code, 201)
        self.assertEqual(out["degraded"], ["context_unavailable:internal_error"])
        self.assertEqual(len(out["recorded"]["event_ids"]), 1)

    def test_session_end(self) -> None:
        code, out = self.send(self.request("session.end", {"reason": "other"}))
        self.assertEqual(code, 201, out)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM events WHERE event_type='session.ended'")[0]["n"], 1)


class GuardMapping(HostHookHttp):
    def setUp(self) -> None:
        super().setUp()
        self.deny_rule = self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})
        self.confirm_rule = self.rule("ask-chmod", "CONFIRM", {"tool": "Bash", "action": "chmod", "path_prefix": "/etc"})
        self.verify_rule = self.rule("verify-touch", "VERIFY", {"tool": "Bash", "action": "touch", "path_prefix": "/work"})

    def test_deny_maps_to_deny_with_reason_and_stays_advisory(self) -> None:
        code, out = self.tool_before("rm", ["/protected/a"])
        self.assertEqual(code, 201, out)
        self.assertEqual(out["decision"], "deny")
        self.assertIn(self.deny_rule, out["reason"])
        self.assertEqual(out["guard"]["capability"], "ADVISORY")
        self.assertEqual(out["guard"]["matched"][0]["rule_id"], self.deny_rule)

    def test_confirm_allow_and_verify(self) -> None:
        self.assertEqual(self.tool_before("chmod", ["/etc/x"])[1]["decision"], "ask")
        code, out = self.tool_before("rm", ["/elsewhere/a"])
        self.assertEqual((out["decision"], out["reason"]), ("allow", None))
        code, out = self.tool_before("touch", ["/work/a"])
        self.assertEqual((out["decision"], out["guard"]["decision"]), ("allow", "verify"))

    def test_unresolved_action_is_ask_not_allow(self) -> None:
        code, out = self.tool_before(None, None)
        self.assertEqual(out["decision"], "ask")
        self.assertIn("insufficient context", out["reason"])

    def test_strictest_path_wins(self) -> None:
        code, out = self.tool_before("rm", ["/elsewhere/a", "/protected/b"])
        self.assertEqual(out["decision"], "deny")

    def test_no_task_still_checks_but_is_reported(self) -> None:
        code, out = self.tool_before("ls", None, task=False)
        self.assertEqual(code, 201)
        self.assertIn("no_task_binding", out["degraded"])

    def test_replayed_tool_before_returns_the_original_decision(self) -> None:
        body = self.request("tool.before", {"tool": "shell", "native_tool": "Bash", "input": {"command": "x"},
                                            "action": "rm", "paths": ["/protected/a"]}, delivery="test:pre:1")
        first = self.send(body)[1]
        self.assertEqual(first["decision"], "deny")
        code, again = self.send(body)
        self.assertEqual((code, again["decision"], again["replayed"]), (200, "deny", True))

    def test_audit_records_the_mapped_decision_without_arguments(self) -> None:
        self.tool_before("rm", ["/protected/a"])
        code, audit = self.call("GET", "/v1/audit?limit=20", token=self.manage)
        entry = next(e for e in audit["entries"] if e["path"] == "/v1/host-hooks/events")
        self.assertEqual(entry["decision"], "deny")
        text = str(entry)
        self.assertIn(self.deny_rule, text)
        self.assertNotIn("/protected/a", text)


class ToolResults(HostHookHttp):
    def after(self, **extra):
        payload = {"tool": "shell", "native_tool": "Bash", "input": {"command": "true"}, "response": {"stdout": "ok"}}
        payload.update(extra)
        native = extra.pop("native", None)
        return self.send(self.request("tool.after", payload, native=native))

    def test_exit_zero_is_pass_with_trust_on_the_source_event(self) -> None:
        code, out = self.after(exit_code=0)
        self.assertEqual(code, 201, out)
        self.assertEqual(out["degraded"], [])
        evidence_id = out["recorded"]["evidence_ids"][0]
        code, got = self.call("GET", f"/v1/evidence/{evidence_id}", token=self.manage)
        self.assertEqual((got["evidence"]["status"], got["evidence"]["kind"]), ("PASS", "COMMAND_RESULT"))
        self.assertEqual(got["evidence"]["step_id"], self.step)
        import json
        source = self.db_rows("SELECT * FROM events WHERE event_id=?", (got["evidence"]["source_event_id"],))[0]
        payload = json.loads(source["payload_json"])
        self.assertEqual(source["source_system"], "host-hook-v1")
        self.assertEqual(source["event_type"], "tool.result")
        self.assertEqual(payload["host_hook"]["trust"], "NATIVE_HOOK")
        self.assertEqual(payload["tool_response"]["native_response"], {"stdout": "ok"})

    def test_exit_rules_are_the_existing_ones(self) -> None:
        pairs = [({"exit_code": 1}, "FAIL"), ({"exit_code": 0, "is_error": True}, "FAIL"), ({}, "INFO")]
        for number, (extra, status) in enumerate(pairs):
            with self.subTest(extra):
                code, out = self.send(self.request("tool.after", {
                    "tool": "shell", "native_tool": "Bash", "input": {"command": "x"}, "response": "text", **extra},
                    native={"session_id": "s", "turn_id": "t", "call_id": f"c{number}"}))
                self.assertEqual(code, 201, out)
                record = self.call("GET", f"/v1/evidence/{out['recorded']['evidence_ids'][0]}", token=self.manage)[1]
                self.assertEqual(record["evidence"]["status"], status)

    def test_replay_and_missing_call_id(self) -> None:
        body = self.request("tool.after", {"tool": "shell", "native_tool": "Bash", "input": {"command": "x"},
                                           "response": {}, "exit_code": 0}, delivery="test:post:1",
                            native={"session_id": "s"})
        first = self.send(body)
        again = self.send(body)
        self.assertEqual((first[0], again[0]), (201, 200))
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 1)
        row = self.db_rows("SELECT turn_id, tool_use_id FROM evidence")[0]
        self.assertEqual((row["turn_id"], row["tool_use_id"]), ("none", "test:post:1"))

    def test_human_actor_cannot_write_tool_results(self) -> None:
        code, refusal = self.send(self.request("tool.after", {
            "tool": "shell", "native_tool": "Bash", "input": {}, "response": {}, "exit_code": 0}),
            token=self.human_hook_token)
        self.assertEqual((code, refusal["error"]["code"]), (403, "forbidden_actor_kind"))
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 0)

    def test_without_task_a_raw_tool_result_only(self) -> None:
        code, out = self.send(self.request("tool.after", {
            "tool": "shell", "native_tool": "Bash", "input": {"command": "x"}, "response": {}, "exit_code": 0}, task=False))
        self.assertEqual(code, 201, out)
        self.assertIn("no_task_binding", out["degraded"])
        self.assertEqual(out["recorded"]["evidence_ids"], [])
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 0)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM events WHERE event_type='tool.result'")[0]["n"], 1)

    def test_changed_workspace_stales_a_verified_step_path_is_exercised(self) -> None:
        # The refresh is best effort; here only that it runs and does not degrade.
        (self.root / "source.txt").write_text("changed\n")
        code, out = self.after(exit_code=0)
        self.assertNotIn("fingerprint_refresh_failed", out["degraded"])


class ToolResultsWithoutWorkspace(HostHookHttp):
    with_workspace = False

    def test_unconfigured_workspace_degrades_to_a_raw_event(self) -> None:
        code, out = self.send(self.request("tool.after", {
            "tool": "shell", "native_tool": "Bash", "input": {"command": "x"}, "response": {}, "exit_code": 0}))
        self.assertEqual(code, 201, out)
        self.assertEqual(out["degraded"], ["evidence_unavailable:fingerprint_unavailable"])
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 0)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM events WHERE event_type='tool.result'")[0]["n"], 1)


class CompactAndStop(HostHookHttp):
    def test_before_compact_makes_a_checkpoint(self) -> None:
        code, out = self.send(self.request("context.compact", {"phase": "before", "trigger": "auto"}))
        self.assertEqual(code, 201, out)
        self.assertTrue(out["recorded"]["checkpoint_id"].startswith("ckp_"))
        row = self.db_rows("SELECT reason, trigger_provenance_json FROM checkpoints")[0]
        self.assertEqual(row["reason"], "PRE_COMPACT")
        self.assertIn("UNVERIFIED_REQUEST", row["trigger_provenance_json"])

    def test_after_compact_context_only_when_deliverable(self) -> None:
        code, out = self.send(self.request("context.compact", {"phase": "after", "deliver_context": False}))
        self.assertIsNone(out["inject_context"])
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM context_packs")[0]["n"], 0)
        code, out = self.send(self.request("context.compact", {"phase": "after"}))
        self.assertIsNotNone(out["inject_context"])
        self.assertEqual(self.db_rows("SELECT reason FROM context_packs")[0]["reason"], "POST_COMPACT")

    def test_turn_stop_checkpoint_only_with_a_task(self) -> None:
        code, out = self.send(self.request("turn.stop", {"last_message_sha256": "b" * 64}))
        self.assertEqual(code, 201, out)
        self.assertIsNotNone(out["recorded"]["checkpoint_id"])
        self.assertEqual(self.db_rows("SELECT reason FROM checkpoints")[0]["reason"], "SESSION_STOP")
        code, out = self.send(self.request("turn.stop", {}, task=False))
        self.assertIsNone(out["recorded"]["checkpoint_id"])
        self.assertIn("no_task_binding", out["degraded"])
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM checkpoints")[0]["n"], 1)

    def test_replayed_turn_stop_does_not_make_a_second_checkpoint(self) -> None:
        body = self.request("turn.stop", {}, delivery="test:stop:1")
        self.send(body)
        self.send(body)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM checkpoints")[0]["n"], 1)


class CoreIsAgentNeutral(HostHookHttp):
    def test_no_agent_names_outside_dialects(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "jasmine_core" / "hosthook"
        offenders = []
        for path in root.rglob("*.py"):
            if "dialects" in path.relative_to(root).parts[:1]:
                continue
            text = path.read_text().lower()
            offenders += [f"{path.name}:{name}" for name in ("codex", "claude") if name in text]
        self.assertEqual(offenders, [])
