"""Remaining Core matrix: Rules, cycles, caps and forward migration evidence."""
from __future__ import annotations
import json
import os
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import test_web_confirmations as helpers
from test_api import HOST, ACTOR
from jasmine_core import auth, clock, db, errors, ids, registry, SCHEMA_VERSION
from jasmine_core.api.handlers import Core
from jasmine_core.migrations import migrate, discover, check_version, applied_migrations, current_version
from jasmine_core.models import NewEvent
from jasmine_core.events import EventStore
from jasmine_core.canonical import canonical_json
from jasmine_core.web_confirmations import WebConfirmationStore


class WebRulesSchema(helpers.WebHttp):
    def rule(self, key="require-human", content="人工规则 token=synthetic-secret-pattern-123456789", requirements=helpers.REQ):
        code, source = self.call("POST", "/v1/events", {"event_type": "user.prompt", "source_system": "component-rule-fixture",
            "host_id": HOST, "task_id": self.task, "project_id": self.project, "payload": {"text": "批准测试规则"}}, token=self.human_token)
        self.assertEqual(code, 201, source)
        code, proposal = self.call("POST", "/v1/rules/proposals", {
            "rule_key": key, "kind": "ACCEPTANCE", "severity": "NORMAL", "enforcement": "VERIFY", "content": content,
            "matcher": {}, "verification_requirements": requirements,
            "scope": {"kind": "task", "project_id": self.project, "task_id": self.task},
            "origin_event_id": source["event"]["event_id"], "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 201, proposal)
        rule_id = proposal["rule"]["rule_id"]
        code, approved = self.call("POST", f"/v1/rules/{rule_id}/approve", {"expected_revision": 1, "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 200, approved)
        return rule_id

    def test_c06_active_other_project_rule_is_not_applicable_to_this_step(self):
        code, created = self.call("POST", "/v1/projects", {"name": "other project", "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 201, created)
        other_project = created["object"]["project_id"]
        code, origin = self.call("POST", "/v1/events", {"event_type": "user.prompt", "source_system": "component-other-project-rule",
            "project_id": other_project, "host_id": HOST, "payload": {"text": "测试其他项目规则"}}, token=self.human_token)
        self.assertEqual(code, 201, origin)
        code, proposed = self.call("POST", "/v1/rules/proposals", {"rule_key": "other-project-human-rule", "kind": "ACCEPTANCE",
            "severity": "NORMAL", "enforcement": "VERIFY", "content": "Other project only", "matcher": {},
            "verification_requirements": helpers.REQ, "scope": {"kind": "project", "project_id": other_project},
            "origin_event_id": origin["event"]["event_id"], "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 201, proposed)
        rule_id = proposed["rule"]["rule_id"]
        self.assertEqual(self.call("POST", f"/v1/rules/{rule_id}/approve", {"host_id": HOST, "expected_revision": 1}, token=self.human_token)[0], 200)
        code, refused = self.prepare({**self.prep_body(), "confirmed_rule_id": rule_id, "confirmed_rule_version": 1})
        self.assertEqual((code, refused["error"]["code"]), (409, "confirmation_context_conflict"))
        self.assertEqual(refused["error"]["details"]["changed"], ["rules"])
        self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_preparations").fetchone()[0], 0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)

    def test_c14_task_confirmation_invalid_after_skipped_step_restarts(self):
        task, first = self.truth()
        code, created = self.call("POST", f"/v1/tasks/{self.task}/steps", {"title": "optional skipped step", "host_id": HOST,
            "expected_revision": task["revision"], "acceptance_criteria": helpers.REQ}, token=self.human_token)
        self.assertEqual(code, 201, created)
        skipped = created["step"]["step_id"]
        self.assertEqual(self.call("POST", f"/v1/steps/{skipped}/transition", {"status": "SKIPPED", "reason": "not required",
            "host_id": HOST, "expected_revision": 1}, token=self.human_token)[0], 200)
        _, verified_prep = self.prepare()
        self.assertEqual(self.submit(self.sub_body(verified_prep))[0], 201)
        self.assertEqual(self.transition("VERIFIED", self.truth()[1]["revision"], self.human_token)[0], 200)
        _, accepted_prep = self.prepare(self.prep_body("ACCEPTED"))
        self.assertEqual(self.submit(self.sub_body(accepted_prep))[0], 201)
        self.assertEqual(self.transition("ACCEPTED", self.truth()[1]["revision"], self.human_token)[0], 200)
        code, task_prep = self.prepare(self.prep_body("ACCEPTED", step=False))
        self.assertEqual(code, 201, task_prep)
        self.assertEqual(sorted(x["status"] for x in task_prep["review_document"]["task_steps"]), ["ACCEPTED", "SKIPPED"])
        code, receipt = self.submit(self.sub_body(task_prep))
        self.assertEqual(code, 201, receipt)
        self.assertEqual(self.call("POST", f"/v1/steps/{skipped}/transition", {"status": "IN_PROGRESS", "reason": "new cycle",
            "host_id": HOST, "expected_revision": 2}, token=self.human_token)[0], 200)
        current_task, _ = self.truth()
        code, refused = self.call("POST", f"/v1/tasks/{self.task}/accept", {"host_id": HOST, "expected_revision": current_task["revision"]}, token=self.human_token)
        self.assertEqual(code, 409, refused)
        self.assertEqual(self.truth()[0]["status"], "ACTIVE")
        from jasmine_core.evidence import CurrentEvidenceValidator
        from jasmine_core import fingerprint
        row = dict(self.c.execute("SELECT * FROM evidence WHERE evidence_id=?", (receipt["evidence"]["evidence_id"],)).fetchone())
        self.assertFalse(CurrentEvidenceValidator(self.c)._fresh(row, fingerprint.capture(), self.project,
            current_task["revision"], current_task["revision"], None, 0))
        self.assertEqual(self.status(task_prep)[1]["consumption"]["receipt"], receipt)

    def test_c15_plain_vs_exact_multiple_rule_pairs_and_info_not_pass(self):
        rules = [self.rule("rule-a"), self.rule("rule-b")]
        _, generic = self.prepare()
        self.assertEqual(len(generic["review_document"]["applicable_rules"]), 2)
        self.assertEqual(self.submit(self.sub_body(generic))[0], 201)
        code, missing = self.transition("VERIFIED", self.truth()[1]["revision"], self.human_token)
        self.assertEqual((code, missing["error"]["code"]), (422, "missing_evidence"))
        for rule_id in rules:
            body = {**self.prep_body(), "confirmed_rule_id": rule_id, "confirmed_rule_version": 1}
            code, prepared = self.prepare(body)
            self.assertEqual(code, 201, prepared)
            self.assertIn("token=synthetic-secret-pattern", prepared["review_document"]["applicable_rules"][0]["content"])
            self.assertEqual(self.submit(self.sub_body(prepared))[0], 201)
        self.assertEqual(self.transition("VERIFIED", self.truth()[1]["revision"], self.human_token)[0], 200)
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 3)

    def test_c15_c24_user_confirmation_pass_requirement_stays_unsatisfied(self):
        task, step = self.truth()
        requirement = {"requirements": [{"key": "must-pass", "kind": "USER_CONFIRMATION", "required_result": "PASS"}]}
        code, result = self.call("POST", f"/v1/steps/{self.step}/criteria", {"acceptance_criteria": requirement,
            "expected_revision": step["revision"], "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 200, result)
        _, prep = self.prepare()
        self.assertEqual(self.submit(self.sub_body(prep))[0], 201)
        code, result = self.transition("VERIFIED", self.truth()[1]["revision"], self.human_token)
        self.assertEqual((code, result["error"]["code"]), (422, "missing_evidence"))
        self.assertEqual(json.loads(self.truth()[1]["acceptance_criteria_json"]), requirement)

    def test_c07_rules_added_retired_superseded_and_writer_drift(self):
        _, prep = self.prepare()
        rule_id = self.rule()
        code, refusal = self.submit(self.sub_body(prep))
        self.assertEqual(refusal["error"]["details"]["changed"], ["rules"])
        body = {**self.prep_body(), "confirmed_rule_id": rule_id, "confirmed_rule_version": 1}
        _, prep = self.prepare(body)
        code, retired = self.call("POST", f"/v1/rules/{rule_id}/retire", {"expected_revision": 2, "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 200, retired)
        self.assertEqual(self.submit(self.sub_body(prep))[1]["error"]["details"]["changed"], ["rules"])
        # A new ACTIVE Rule added during the actual scan is detected in writer.
        entered, release = threading.Event(), threading.Event()
        original = WebConfirmationStore._scan
        results = []
        request = self.prep_body()
        def scan(store, deadline):
            snapshot = original(store, deadline)
            entered.set()
            self.assertTrue(release.wait(10))
            return snapshot
        with patch.object(WebConfirmationStore, "_scan", scan):
            worker = threading.Thread(target=lambda: results.append(self.prepare(request)))
            worker.start()
            self.assertTrue(entered.wait(5))
            try: self.rule("new-rule-during-scan")
            finally: release.set()
            worker.join(10)
        self.assertEqual(results[0][1]["error"]["details"]["changed"], ["rules"])

    def test_c14_revision_cycles_prevent_old_web_confirmation(self):
        _, prep = self.prepare()
        _, result = self.submit(self.sub_body(prep))
        revision = self.truth()[1]["revision"]
        self.assertEqual(self.transition("STALE", revision)[0], 200)
        self.assertEqual(self.transition("IN_PROGRESS", revision + 1)[0], 200)
        self.assertEqual(self.transition("EXECUTED", revision + 2)[0], 200)
        code, denied = self.transition("VERIFIED", revision + 3, self.human_token)
        self.assertEqual((code, denied["error"]["code"]), (422, "missing_evidence"))
        # Historical receipt still reads; it must not become fresh evidence.
        self.assertEqual(self.status(prep)[1]["consumption"]["receipt"], result)
        _, new = self.prepare()
        self.assertEqual(self.submit(self.sub_body(new))[0], 201)
        self.assertEqual(self.transition("VERIFIED", revision + 3, self.human_token)[0], 200)

    def test_c14_rule_version_drift_requires_new_exact_confirmation(self):
        rule_id = self.rule("versioned-rule")
        _, prep = self.prepare({**self.prep_body(), "confirmed_rule_id": rule_id, "confirmed_rule_version": 1})
        self.assertEqual(self.submit(self.sub_body(prep))[0], 201)
        origin = self.c.execute("SELECT origin_event_id FROM rule_versions WHERE rule_id=? AND version=1", (rule_id,)).fetchone()[0]
        code, superseded = self.call("POST", f"/v1/rules/{rule_id}/supersede", {
            "host_id": HOST, "expected_revision": 2, "scope": {"kind": "task", "project_id": self.project, "task_id": self.task},
            "origin_event_id": origin, "kind": "ACCEPTANCE", "severity": "NORMAL", "enforcement": "VERIFY", "content": "new exact version",
            "matcher": {}, "verification_requirements": helpers.REQ}, token=self.human_token)
        self.assertEqual(code, 200, superseded)
        self.assertEqual(superseded["rule"]["version"], 2)
        self.assertEqual(self.transition("VERIFIED", self.truth()[1]["revision"], self.human_token)[1]["error"]["code"], "missing_evidence")
        _, new = self.prepare({**self.prep_body(), "confirmed_rule_id": rule_id, "confirmed_rule_version": 2})
        self.assertEqual(self.submit(self.sub_body(new))[0], 201)
        self.assertEqual(self.transition("VERIFIED", self.truth()[1]["revision"], self.human_token)[0], 200)

    def test_c16_legacy_prompt_path_still_valid_and_web_seq_strict(self):
        task, step = self.truth()
        code, prompt = self.call("POST", "/v1/events", {"event_type": "user.prompt", "source_system": "component-legacy-fixture", "host_id": HOST,
            "task_id": self.task, "project_id": self.project, "payload": {"text": "原路径确认", "step_id": self.step}}, token=self.human_token)
        self.assertEqual(code, 201, prompt)
        code, legacy = self.call("POST", "/v1/evidence/confirm", {"origin_event_id": prompt["event"]["event_id"],
            "task_id": self.task, "step_id": self.step, "expected_revision": step["revision"], "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 201, legacy)
        self.assertEqual(set(legacy), {"evidence", "event", "replayed"})
        self.assertEqual(self.transition("VERIFIED", step["revision"], self.human_token)[0], 200)
        _, prep = self.prepare(self.prep_body("ACCEPTED"))
        _, web = self.submit(self.sub_body(prep))
        prepared_seq = self.c.execute("SELECT seq FROM events WHERE event_id=?", (prep["preparation_id"],)).fetchone()[0]
        self.assertLess(prepared_seq, web["source_event"]["seq"])
        self.assertLess(web["source_event"]["seq"], web["event"]["seq"])
        self.assertEqual(self.transition("ACCEPTED", self.truth()[1]["revision"], self.human_token)[0], 200)

    def test_c17_empty_criteria_and_skipped_only_tasks_are_ineligible(self):
        _, step = self.truth()
        self.assertEqual(self.call("POST", f"/v1/steps/{self.step}/criteria", {"acceptance_criteria": {"requirements": []},
            "expected_revision": step["revision"], "host_id": HOST}, token=self.human_token)[0], 200)
        code, refusal = self.prepare()
        self.assertEqual(refusal["error"]["details"]["reason"], "empty_criteria")
        self.assertEqual(self.call("POST", f"/v1/steps/{self.step}/transition", {"status": "SKIPPED", "reason": "component skip", "expected_revision": self.truth()[1]["revision"], "host_id": HOST}, token=self.human_token)[0], 200)
        code, refusal = self.prepare(self.prep_body("ACCEPTED", step=False))
        self.assertEqual(refusal["error"]["details"]["reason"], "task_steps_ineligible")

    def test_c08_real_git_head_coverage_and_twenty_thousand_file_limit(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, stdout=subprocess.PIPE)
        subprocess.run(["git", "-C", str(self.root), "add", "source.txt"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=component", "-c", "user.email=component@example.invalid", "commit", "-qm", "initial"], check=True)
        _, prep = self.prepare()
        # Same files, different HEAD still invalidates the prepared workspace.
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=component", "-c", "user.email=component@example.invalid", "commit", "--allow-empty", "-qm", "head-changed"], check=True)
        self.assertEqual(self.submit(self.sub_body(prep))[1]["error"]["details"]["changed"], ["fingerprint"])
        paths = self.root / "members"
        paths.mkdir()
        for index in range(20_001):
            (paths / f"member-{index}").touch()
        code, refusal = self.prepare()
        self.assertEqual((code, refusal["error"]["code"]), (503, "confirmation_scan_unavailable"))
        self.assertEqual(refusal["error"]["details"]["reason"], "too_large")
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)

    def test_c09_scan_timeout_final_expiry_and_database_busy(self):
        import jasmine_core.web_confirmations as module
        with patch.object(module, "SCAN_SECONDS", 0):
            self.assertEqual(self.prepare()[1]["error"]["details"]["reason"], "timeout")
        _, prep = self.prepare()
        body = self.sub_body(prep)
        original = WebConfirmationStore._scan
        frozen = clock.parse_rfc3339(prep["created_at"]) + timedelta(seconds=1)
        now = [frozen]
        def scan(store, deadline):
            snapshot = original(store, deadline)
            now[0] = clock.parse_rfc3339(prep["expires_at"])
            return snapshot
        with patch.object(WebConfirmationStore, "_scan", scan), patch("jasmine_core.clock.now", side_effect=lambda: now[0]):
            code, refused = self.submit(body)
        self.assertEqual((code, refused["error"]["code"]), (409, "confirmation_expired"))
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
        # Real competing SQLite lock; only test connection shortens its wait.
        independent = db.connect(self.db_path)
        try:
            independent.execute("PRAGMA busy_timeout=20")
            principal = auth.Auth(independent).authenticate(f"Bearer {self.human_token}")
            self.c.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaises(errors.DatabaseBusy):
                    WebConfirmationStore(independent, schema_version=12).prepare(self.prep_body(), principal, "component-busy")
            finally: self.c.execute("ROLLBACK")
        finally: independent.close()

    def test_c18_complete_review_oversize_is_refused_not_truncated(self):
        # Each Rule is legal <=4096 chars. Full readback exceeds the review cap.
        for index in range(70):
            self.rule(f"large-{index}", content="完整规则" * 1000)
        code, refusal = self.prepare()
        self.assertEqual((code, refusal["error"]["code"]), (409, "confirmation_review_too_large"))
        self.assertEqual(refusal["error"]["details"]["max_bytes"], 262144)
        self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_preparations").fetchone()[0], 0)

    def test_c19_migrate_all_supported_prefixes_old_bytes_and_append_only(self):
        for version in range(1, 12):
            path = Path(self._tmp.name) / f"old-schema-{version}.db"
            connection = db.connect(path)
            try:
                # Apply exactly shipped historical bytes/checksums, not altered migration fixtures.
                with patch("jasmine_core.SCHEMA_VERSION", version), patch("jasmine_core.migrations.discover", return_value=discover()[:version]):
                    migrate(connection)
                before = [(x["name"], x["checksum"]) for x in applied_migrations(connection)]
                with db.transaction(connection):
                    reg = registry.Registry(connection)
                    reg.upsert_host(HOST, display_name="historical fixture")
                    reg.upsert_actor(ACTOR, kind="human", home_host_id=HOST)
                    EventStore(connection, schema_version=version).append(NewEvent("user.prompt", "component-old-schema", ACTOR, "human", HOST,
                        {"text": "historical Unicode 原始字节"}, clock.now()))
                historical = [tuple(x) for x in connection.execute("SELECT * FROM events ORDER BY seq")]
                migrate(connection)
                self.assertEqual(current_version(connection), 12)
                self.assertEqual([tuple(x) for x in connection.execute("SELECT * FROM events ORDER BY seq")], historical)
                self.assertEqual([(x["name"], x["checksum"]) for x in applied_migrations(connection)][:version], before)
                self.assertEqual(migrate(connection), [])
                with patch("jasmine_core.SCHEMA_VERSION", 11), self.assertRaises(errors.SchemaVersionUnsupported):
                    check_version(connection)
            finally: connection.close()
        before_events = self.c.execute("SELECT event_id,payload_json,body_sha256 FROM events ORDER BY seq").fetchall()
        self.assertEqual(migrate(self.c), [])
        self.assertEqual(self.c.execute("SELECT event_id,payload_json,body_sha256 FROM events ORDER BY seq").fetchall(), before_events)
        _, prep = self.prepare()
        _, receipt = self.submit(self.sub_body(prep))
        for table, id_column, ident in (("web_confirmation_preparations", "preparation_id", prep["preparation_id"]),
                                       ("web_confirmation_submissions", "command_event_id", receipt["event"]["event_id"])):
            for sql in (f"UPDATE {table} SET created_at=created_at WHERE {id_column}=?", f"DELETE FROM {table} WHERE {id_column}=?",
                        f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} WHERE {id_column}=?"):
                with self.assertRaises(Exception) as raised: self.c.execute(sql, (ident,))
                self.assertIn("append-only", str(raised.exception))

for _name in dir(helpers.WebHttp):
    if _name.startswith("test_") and _name not in WebRulesSchema.__dict__:
        setattr(WebRulesSchema, _name, None)
