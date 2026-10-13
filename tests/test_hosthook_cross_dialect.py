"""Cross-dialect structure check: the same neutral events from two dialects must give
structurally identical Core records, differing only in source fields.

Component level. Native payloads for both agents are constructed from docs. The Claude Code
PostToolUse payload used here carries a hypothetical integer `exit_code` (the page does not
document one); with the documented shape alone its Evidence would be INFO.
"""

from __future__ import annotations

import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hosthook_support import HostHookHttp
from test_api import HOST
from test_hosthook_dialect_claude_code import fixture as cc_fixture
from test_hosthook_dialect_codex import fixture as codex_fixture

from jasmine_core.hosthook import runtime

NOW = datetime(2026, 10, 12, 8, 30, 15, tzinfo=timezone.utc)
PROMPT = "  Please remove the temp file — 请删除\n"

#: (neutral label, codex fixture, claude-code fixture)
SCRIPT = [
    ("session.start", "session_start", "session_start"),
    ("prompt.submit", "user_prompt_submit", "user_prompt_submit"),
    ("tool.before/deny", "pre_tool_use_bash", "pre_tool_use_bash"),
    ("tool.before/allow", "pre_tool_use_bash", "pre_tool_use_bash"),
    ("tool.after", "post_tool_use_bash", "post_tool_use_bash_with_exit_code"),
    ("context.compact/before", "pre_compact", "pre_compact"),
    ("turn.stop", "stop", "stop"),
    ("session.end", "session_end", "session_end"),
]
#: payload keys that identify the source (agent, adapter, native ids, delivery); everything else must match
SOURCE_KEYS = {"agent", "adapter", "native", "delivery_id", "codex_session_id", "native_session_id",
               "capabilities_sha256", "turn_id", "tool_use_id", "tool_response"}
#: per-record identifiers (differ between any two records, whatever the source)
RECORD_ID_SUFFIXES = ("event_id", "evidence_id")


def _strip(value):
    """Drop source fields and the ids/timestamps that differ per record, recursively."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items()
                if k not in SOURCE_KEYS and not k.endswith(RECORD_ID_SUFFIXES)}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


class CrossDialect(HostHookHttp):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-cross-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        (self.dir / "private").mkdir(mode=0o700)
        for name, token in (("system.key", self.system_token), ("human.key", self.human_hook_token)):
            path = self.dir / "private" / name
            path.write_text(token)
            path.chmod(0o600)
        (self.dir / ".jasmine").mkdir()
        (self.dir / ".jasmine" / "host-binding.json").write_text(json.dumps({
            "version": 1, "core_url": f"http://127.0.0.1:{self.port}", "host_id": HOST,
            "project_id": self.project, "task_id": self.task, "step_id": self.step,
            "api_key_file": str(self.dir / "private" / "system.key"),
            "human_api_key_file": str(self.dir / "private" / "human.key")}))
        self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})

    def run_script(self, agent: str) -> list[dict]:
        outputs = []
        index = 1 if agent == "codex" else 2
        load = codex_fixture if agent == "codex" else cc_fixture
        session = f"{agent}-session"
        for position, row in enumerate(SCRIPT):
            native = {**load(row[index]), "cwd": str(self.dir), "session_id": session}
            if row[0] == "tool.before/allow":
                native["tool_input"] = {"command": "ls /elsewhere"}
                native["tool_use_id"] = "call-allow"
            elif agent == "claude-code" and "tool_use_id" in native:
                pass
            out, err = io.StringIO(), io.StringIO()
            code = runtime.run_hook(agent, json.dumps(native).encode(), stdout=out, stderr=err, environ={},
                                    now=lambda: NOW)
            self.assertEqual((code, err.getvalue()), (0, ""), (agent, row[0]))
            outputs.append(out.getvalue())
        return outputs

    def records(self, agent: str) -> dict:
        def mine(payload: dict) -> bool:
            who = payload.get("agent") or payload.get("host_hook", {}).get("agent")
            return (who.get("name") if isinstance(who, dict) else who) == agent
        events = []
        for row in self.db_rows("SELECT * FROM events WHERE source_system='host-hook-v1' ORDER BY seq"):
            payload = json.loads(row["payload_json"])
            if mine(payload):
                events.append({"type": row["event_type"], "actor_kind": row["actor_kind"], "has_task": bool(row["task_id"]),
                               "payload": _strip(payload)})
        evidence = [{k: row[k] for k in ("status", "kind", "tool_name")}
                    for row in self.db_rows("SELECT e.* FROM evidence e JOIN events v ON v.event_id=e.source_event_id "
                                            "ORDER BY e.rowid")
                    if mine(json.loads(self.db_rows("SELECT payload_json FROM events WHERE event_id=?",
                                                    (row["source_event_id"],))[0]["payload_json"]))]
        return {"events": events, "evidence": evidence}

    def test_same_neutral_events_give_the_same_core_records(self) -> None:
        out_codex = self.run_script("codex")
        out_claude = self.run_script("claude-code")
        a, b = self.records("codex"), self.records("claude-code")
        self.assertGreaterEqual(len(a["events"]), 8)
        self.assertEqual(a["evidence"], [{"status": "PASS", "kind": "COMMAND_RESULT", "tool_name": "Bash"}])
        self.assertEqual(sorted(e["type"] for e in a["events"]), sorted([
            "session.started", "user.prompt", "tool.call", "tool.result", "evidence.recorded",
            "host.compaction_reported", "host.turn_stopped", "session.ended"]))
        self.assertEqual(a, b)
        # derived objects: same reasons in the same order, one set per agent
        packs = [r["reason"] for r in self.db_rows("SELECT reason FROM context_packs ORDER BY rowid")]
        self.assertEqual(packs, ["SESSION_START", "USER_PROMPT"] * 2)
        checkpoints = [r["reason"] for r in self.db_rows("SELECT reason FROM checkpoints ORDER BY rowid")]
        self.assertEqual(checkpoints, ["PRE_COMPACT", "SESSION_STOP"] * 2)
        # the user's words are identical, byte for byte
        texts = [json.loads(r["payload_json"])["text"] for r in
                 self.db_rows("SELECT payload_json FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1'")]
        self.assertEqual(texts, [PROMPT, PROMPT])
        # the decisions recorded by Core (receipt responses) agree per step, ignoring source-specific ids
        def decisions(agent):
            rows = self.db_rows("SELECT event, response_json FROM host_hook_deliveries WHERE agent_name=? ORDER BY rowid", (agent,))
            return [(r["event"], json.loads(r["response_json"])["decision"],
                     json.loads(r["response_json"])["degraded"]) for r in rows]
        self.assertEqual(decisions("codex"), decisions("claude-code"))
        self.assertEqual([d for e, d, _ in decisions("codex") if e == "tool.before"], ["deny", "allow"])
        # native outputs differ only where the dialects differ: deny shape is shared, Stop output is not
        deny = lambda text: json.loads(text)["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual((deny(out_codex[2]), deny(out_claude[2])), ("deny", "deny"))
        self.assertEqual((out_codex[6], out_claude[6]), ("{}", ""))

    def test_source_fields_are_what_differs(self) -> None:
        self.run_script("codex")
        self.run_script("claude-code")
        rows = self.db_rows("SELECT payload_json FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1' ORDER BY seq")
        first, second = (json.loads(r["payload_json"]) for r in rows)
        self.assertEqual((first["agent"]["name"], second["agent"]["name"]), ("codex", "claude-code"))
        self.assertNotEqual(first["adapter"]["capabilities_sha256"], second["adapter"]["capabilities_sha256"])
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        self.assertEqual({k for k in first if k not in SOURCE_KEYS}, {k for k in second if k not in SOURCE_KEYS})


if __name__ == "__main__":
    import unittest
    unittest.main()
