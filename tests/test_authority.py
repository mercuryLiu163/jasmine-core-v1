"""P1-01 Authority: real HTTP, version history, permission and Guard behavior."""

from __future__ import annotations

import sqlite3

from test_api import ACTOR, HOST, ApiTestCase

from jasmine_core import auth, db, ids, registry

AGENT = ids.new_id("act")


class AuthorityHttp(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with db.transaction(conn):
            registry.Registry(conn).upsert_actor(AGENT, kind="agent", home_host_id=HOST)
        self.agent_key = auth.Auth(conn).issue_key(
            actor_id=AGENT, label="agent", scopes=["authority:propose", "guard:check", "admin"]
        )["token"]
        self.manage_key = self.key(["authority:manage", "authority:read", "authority:propose",
                                    "guard:check", "objects:write", "events:write"])
        _, project = self.call("POST", "/v1/projects", {"name": "p", "host_id": HOST},
                               token=self.manage_key)
        self.project_id = project["object"]["project_id"]
        _, task = self.call("POST", "/v1/tasks", {"title": "t", "project_id": self.project_id,
                                                   "host_id": HOST}, token=self.manage_key)
        self.task_id = task["object"]["task_id"]
        _, event = self.call("POST", "/v1/events", self.event_body(
            project_id=self.project_id, task_id=self.task_id, event_id=ids.new_id("evt")),
            token=self.manage_key)
        self.origin = event["event"]["event_id"]

    def proposal(self, **changes):
        body = {"rule_key": "no-smb-write", "kind": "RULE", "severity": "HARD",
                "enforcement": "DENY", "content": "Do not write SMB snapshot",
                "matcher": {"action": "write", "path_prefix": "/Volumes/smb"},
                "scope": {"kind": "task", "project_id": self.project_id, "task_id": self.task_id},
                "origin_event_id": self.origin, "host_id": HOST, "event_id": ids.new_id("evt")}
        body.update(changes)
        return body

    def test_malformed_content_enums_are_typed_400(self) -> None:
        # Compatibility guard: P1-01 _content already checks JSON types before
        # frozenset membership. Keep that behavior while State adds new enums.
        for field in ("kind", "severity", "enforcement"):
            body = self.proposal()
            body[field] = []
            status, refusal = self.call("POST", "/v1/rules/proposals", body,
                                        token=self.agent_key)
            self.assertEqual((status, refusal["error"]["code"]), (400, "invalid_request"))

    def test_agent_proposal_human_approval_guard_and_replay(self) -> None:
        proposal = self.proposal()
        status, first = self.call("POST", "/v1/rules/proposals", proposal, token=self.agent_key)
        self.assertEqual(status, 201, first)
        rule_id = first["rule"]["rule_id"]
        self.assertEqual(first["rule"]["status"], "PROPOSED")
        status, replay = self.call("POST", "/v1/rules/proposals", proposal, token=self.agent_key)
        self.assertEqual(status, 200, replay)
        self.assertEqual(replay["rule"], first["rule"])
        self.assertEqual(replay["event"], first["event"])
        check = {"task_id": self.task_id, "tool": "exec", "action": "write",
                 "path": "/Volumes/smb/src/a.py"}
        status, before = self.call("POST", "/v1/guard/check", check, token=self.agent_key)
        self.assertEqual((status, before["decision"]), (200, "allow"))
        approval = {"expected_revision": 1, "host_id": HOST, "event_id": ids.new_id("evt")}
        status, denied = self.call("POST", f"/v1/rules/{rule_id}/approve", approval,
                                   token=self.agent_key)
        self.assertEqual(status, 403, denied)
        status, approved = self.call("POST", f"/v1/rules/{rule_id}/approve", approval,
                                     token=self.manage_key)
        self.assertEqual(status, 200, approved)
        self.assertEqual((approved["rule"]["status"], approved["rule"]["revision"]),
                         ("ACTIVE", 2))
        status, hit = self.call("POST", "/v1/guard/check", check, token=self.agent_key)
        self.assertEqual((status, hit["decision"], hit["capability"]), (200, "deny", "ADVISORY"))
        self.assertEqual(hit["matched"][0]["version"], 1)
        status, audit = self.call("GET", "/v1/audit", token=self.admin_token)
        self.assertEqual(status, 200)
        guard_rows = [entry for entry in audit["entries"] if entry["path"] == "/v1/guard/check"]
        self.assertEqual(guard_rows[-1]["decision"], "deny")
        self.assertEqual(guard_rows[-1]["detail"]["hits"][0]["rule_id"], rule_id)
        self.assertNotIn("/Volumes/smb", str(guard_rows[-1]["detail"]))
        status, replay_approval = self.call("POST", f"/v1/rules/{rule_id}/approve", approval,
                                            token=self.manage_key)
        self.assertEqual(status, 200, replay_approval)
        self.assertTrue(replay_approval["replayed"])
        self.assertEqual(replay_approval["rule"], approved["rule"])
        self.assertEqual(replay_approval["event"], approved["event"])

    def test_revision_version_retire_and_immutable_history(self) -> None:
        _, proposed = self.call("POST", "/v1/rules/proposals", self.proposal(), token=self.agent_key)
        rule_id = proposed["rule"]["rule_id"]
        self.call("POST", f"/v1/rules/{rule_id}/approve",
                  {"host_id": HOST, "expected_revision": 1}, token=self.manage_key)
        replacement = {"host_id": HOST, "expected_revision": 2,
                       "scope": {"kind": "task", "project_id": self.project_id,
                                 "task_id": self.task_id}, "origin_event_id": self.origin,
                       "kind": "RULE", "severity": "HARD", "enforcement": "CONFIRM",
                       "content": "Ask before SMB write", "matcher": {"action": "write",
                                                                 "path_prefix": "/Volumes/smb"}}
        status, updated = self.call("POST", f"/v1/rules/{rule_id}/supersede", replacement,
                                    token=self.manage_key)
        self.assertEqual(status, 200, updated)
        self.assertEqual((updated["rule"]["version"], updated["rule"]["revision"]), (2, 3))
        status, history = self.call("GET", f"/v1/rules/{rule_id}/history", token=self.manage_key)
        self.assertEqual(status, 200)
        self.assertEqual([(item["rule"]["version"], item["rule"]["status"])
                          for item in history["history"]],
                         [(1, "PROPOSED"), (1, "ACTIVE"), (1, "SUPERSEDED"), (2, "ACTIVE")])
        status, stale = self.call("POST", f"/v1/rules/{rule_id}/retire",
                                  {"host_id": HOST, "expected_revision": 2}, token=self.manage_key)
        self.assertEqual((status, stale["error"]["code"]), (409, "revision_conflict"))
        status, retired = self.call("POST", f"/v1/rules/{rule_id}/retire",
                                    {"host_id": HOST, "expected_revision": 3}, token=self.manage_key)
        self.assertEqual((status, retired["rule"]["status"]), (200, "RETIRED"))
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        rows = conn.execute("SELECT version,content FROM rule_versions WHERE rule_id=? ORDER BY version",
                            (rule_id,)).fetchall()
        self.assertEqual([(r["version"], r["content"]) for r in rows],
                         [(1, "Do not write SMB snapshot"), (2, "Ask before SMB write")])
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE rule_versions SET content='changed' WHERE rule_id=?", (rule_id,))
        conn.execute("PRAGMA recursive_triggers=OFF")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT OR REPLACE INTO rule_versions SELECT * FROM rule_versions "
                         "WHERE rule_id=? AND version=1", (rule_id,))

    def test_scope_source_and_unsupported_matcher_rejected(self) -> None:
        bad = self.proposal(matcher={"regex": ".*"})
        status, response = self.call("POST", "/v1/rules/proposals", bad, token=self.agent_key)
        self.assertEqual((status, response["error"]["code"]), (400, "invalid_request"))
        bad = self.proposal(scope={"kind": "global"})
        status, response = self.call("POST", "/v1/rules/proposals", bad, token=self.agent_key)
        self.assertEqual((status, response["error"]["code"]), (400, "invalid_request"))
        _, first = self.call("POST", "/v1/rules/proposals", self.proposal(), token=self.agent_key)
        status, duplicate = self.call("POST", "/v1/rules/proposals", self.proposal(),
                                      token=self.agent_key)
        self.assertEqual((status, duplicate["error"]["code"]), (409, "rule_key_conflict"))
        rule_id = first["rule"]["rule_id"]
        status, response = self.call("POST", f"/v1/rules/{rule_id}/approve",
                                      {"host_id": HOST, "expected_revision": True},
                                      token=self.manage_key)
        self.assertEqual((status, response["error"]["code"]), (400, "invalid_request"))

    def test_missing_context_never_claims_allow_for_scoped_hard_rule(self) -> None:
        _, proposed = self.call("POST", "/v1/rules/proposals", self.proposal(), token=self.agent_key)
        self.call("POST", f"/v1/rules/{proposed['rule']['rule_id']}/approve",
                  {"host_id": HOST, "expected_revision": 1}, token=self.manage_key)
        status, result = self.call("POST", "/v1/guard/check",
                                   {"tool": "exec", "action": "write", "path": "/Volumes/smb/a"},
                                   token=self.agent_key)
        self.assertEqual((status, result["decision"]), (200, "confirm"))

    def test_root_prefix_covers_descendants(self) -> None:
        proposal = self.proposal(rule_key="all-write", matcher={"action": "write",
                                                               "path_prefix": "/"})
        _, proposed = self.call("POST", "/v1/rules/proposals", proposal, token=self.agent_key)
        self.call("POST", f"/v1/rules/{proposed['rule']['rule_id']}/approve",
                  {"host_id": HOST, "expected_revision": 1}, token=self.manage_key)
        status, result = self.call("POST", "/v1/guard/check",
                                   {"task_id": self.task_id, "tool": "exec", "action": "write",
                                    "path": "/tmp/file"}, token=self.agent_key)
        self.assertEqual((status, result["decision"]), (200, "deny"))

    def test_uncertain_deny_takes_precedence_over_matched_verify(self) -> None:
        _, deny = self.call("POST", "/v1/rules/proposals", self.proposal(),
                            token=self.agent_key)
        self.call("POST", f"/v1/rules/{deny['rule']['rule_id']}/approve",
                  {"host_id": HOST, "expected_revision": 1}, token=self.manage_key)
        verify = self.proposal(rule_key="verify-all", kind="ACCEPTANCE",
                               severity="NORMAL", enforcement="VERIFY", matcher={},
                               content="Collect evidence for every tool action")
        _, verify_result = self.call("POST", "/v1/rules/proposals", verify,
                                      token=self.agent_key)
        self.call("POST", f"/v1/rules/{verify_result['rule']['rule_id']}/approve",
                  {"host_id": HOST, "expected_revision": 1}, token=self.manage_key)
        status, result = self.call("POST", "/v1/guard/check",
                                   {"task_id": self.task_id, "tool": "exec", "action": "write"},
                                   token=self.agent_key)
        self.assertEqual((status, result["decision"]), (200, "confirm"))
        self.assertEqual(result["uncertain"][0]["rule_id"], deny["rule"]["rule_id"])
        self.assertEqual(result["matched"][0]["rule_id"], verify_result["rule"]["rule_id"])

    def test_agent_admin_cannot_mint_human_key_or_manage(self) -> None:
        status, body = self.call("POST", "/v1/auth/keys", {"actor_id": ACTOR,
                             "label": "stolen", "scopes": ["authority:manage"]},
                             token=self.agent_key)
        self.assertEqual((status, body["error"]["code"]), (403, "actor_mismatch"))
        status, body = self.call("POST", "/v1/auth/keys", {"actor_id": AGENT,
                             "label": "self", "scopes": ["authority:manage"]},
                             token=self.agent_key)
        self.assertEqual((status, body["error"]["code"]), (403, "forbidden_actor_kind"))
