"""ADR0015 Core matrix: isolated real HTTP and real workspace scans.

Fixtures are component_simulation actors and criteria; no physical-human claim.
"""
from __future__ import annotations
import hashlib
import json
import os
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from test_evidence import EvidenceHttp
from test_api import ACTOR, HOST
from jasmine_core import auth, clock, db, errors, fingerprint, ids, registry
from jasmine_core.api.handlers import Core
from jasmine_core.api.dispatch import dispatch
from jasmine_core.api.request import Request
from jasmine_core.canonical import canonical_json
from jasmine_core.events import EventStore
from jasmine_core.models import NewEvent
from jasmine_core.web_confirmations import (CONTRACT, PREPARE_PATH, SUBMIT_PATH, WebConfirmationStore,
                                            strict_json, digest, parse_json)

REQ = {"requirements": [{"key": "human", "kind": "USER_CONFIRMATION", "required_result": "INFO"}]}


class WebHttp(EvidenceHttp):
    # Do not inherit the unrelated tests; the shared fixture is intentional.
    def setUp(self):
        super().setUp()
        self.human_token = self.key(["objects:write", "objects:read", "events:write", "events:read",
            "authority:manage", "authority:propose", "authority:read", "state:write", "state:accept",
            "state:read", "evidence:confirm", "evidence:read"])
        self.c = db.connect(self.db_path)
        self.addCleanup(self.c.close)
        # Explicit human criteria changes through Core. Step can confirm EXECUTED.
        self.assertEqual(self.call("POST", f"/v1/tasks/{self.task}/criteria", {
            "acceptance_criteria": REQ, "expected_revision": 2, "host_id": HOST}, token=self.human_token)[0], 200)
        self.assertEqual(self.call("POST", f"/v1/steps/{self.step}/criteria", {
            "acceptance_criteria": REQ, "expected_revision": 1, "host_id": HOST}, token=self.human_token)[0], 200)
        self.assertEqual(self.transition("IN_PROGRESS", 2)[0], 200)
        self.assertEqual(self.transition("EXECUTED", 3)[0], 200)

    def truth(self):
        return (dict(self.c.execute("SELECT * FROM tasks WHERE task_id=?", (self.task,)).fetchone()),
                dict(self.c.execute("SELECT * FROM steps WHERE step_id=?", (self.step,)).fetchone()))

    def prep_body(self, target="VERIFIED", step=True):
        task, current_step = self.truth()
        return {"event_id": ids.new_id("evt"), "task_id": self.task, "step_id": self.step if step else None,
                "expected_task_revision": task["revision"], "expected_step_revision": current_step["revision"] if step else None,
                "target_status": target}

    def prepare(self, body=None):
        return self.call("POST", PREPARE_PATH, body or self.prep_body(), token=self.human_token)

    def sub_body(self, prep, note="注释 <b>只显示</b>"):
        return {"event_id": ids.new_id("evt"), "preparation_id": prep["preparation_id"],
                "review_sha256": prep["review_sha256"], "affirmed": True, "note": note}

    def submit(self, body):
        return self.call("POST", SUBMIT_PATH, body, token=self.human_token)

    def status(self, prep):
        return self.call("GET", f'/v1/evidence/confirmations/{prep["preparation_id"]}', token=self.human_token)

    def test_c01_c24_full_step_and_task_info_state_separation(self):
        before = self.truth()
        code, prep = self.prepare()
        self.assertEqual(code, 201, prep)
        self.assertEqual(prep["review_sha256"], hashlib.sha256(canonical_json(prep["review_document"]).encode()).hexdigest())
        self.assertEqual(self.truth(), before)
        code, result = self.submit(self.sub_body(prep))
        self.assertEqual(code, 201, result)
        self.assertEqual(result["evidence"]["status"], "INFO")
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.status(prep)[1]["availability"], "CONSUMED")
        for event in (result["source_event"], result["event"]):
            code, read = self.call("GET", f'/v1/events/{event["event_id"]}', token=self.human_token)
            self.assertEqual((code, read["event"]), (200, event))
        code, verified = self.transition("VERIFIED", before[1]["revision"], self.human_token)
        self.assertEqual(code, 200, verified)
        code, refused = self.transition("ACCEPTED", verified["step"]["revision"], self.human_token)
        self.assertEqual((code, refused["error"]["code"]), (422, "missing_evidence"))
        code, accepted_prep = self.prepare(self.prep_body("ACCEPTED"))
        self.assertEqual(code, 201, accepted_prep)
        self.assertEqual(self.submit(self.sub_body(accepted_prep))[0], 201)
        code, accepted = self.transition("ACCEPTED", verified["step"]["revision"], self.human_token)
        self.assertEqual(code, 200, accepted)
        code, task_prep = self.prepare(self.prep_body("ACCEPTED", step=False))
        self.assertEqual(code, 201, task_prep)
        self.assertEqual(len(task_prep["review_document"]["task_steps"]), 1)
        self.assertEqual(self.submit(self.sub_body(task_prep))[0], 201)
        task, _ = self.truth()
        code, accepted = self.call("POST", f"/v1/tasks/{self.task}/accept", {
            "expected_revision": task["revision"], "host_id": HOST}, token=self.human_token)
        self.assertEqual(code, 200, accepted)

    def test_c02_c17_ineligible_and_affirmation(self):
        for value in (False, 1, "true", None):
            _, prep = self.prepare()
            body = self.sub_body(prep)
            body["affirmed"] = value
            code, refusal = self.submit(body)
            self.assertEqual((code, refusal["error"]["code"]), (400, "invalid_request"))
            self.assertEqual(self.status(prep)[1]["consumption"], None)
        code, refusal = self.prepare(self.prep_body("ACCEPTED"))
        self.assertEqual((code, refusal["error"]["code"]), (409, "confirmation_ineligible"))
        code, refusal = self.prepare(self.prep_body("ACCEPTED", step=False))
        self.assertEqual((code, refusal["error"]["code"]), (409, "confirmation_ineligible"))
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)

    def test_c03_c04_identity_scopes_and_host(self):
        code, me = self.call("GET", "/v1/auth/me", token=self.human_token)
        self.assertEqual(code, 200)
        self.assertEqual((me["actor_id"], me["actor_kind"], me["home_host_id"]), (ACTOR, "human", HOST))
        self.assertNotIn("token", me)
        self.assertEqual(me["capabilities"], {CONTRACT: True})
        for token in (self.key(["admin"]), self.system_token):
            code, error = self.call("POST", PREPARE_PATH, self.prep_body(), token=token)
            self.assertEqual((code, error["error"]["code"]), (403, "forbidden_scope"))
        # Out-of-band bad grant verifies business role enforcement.
        actor = ids.new_id("act")
        with db.transaction(self.c):
            registry.Registry(self.c).upsert_actor(actor, kind="agent", home_host_id=HOST)
        issued = auth.Auth(self.c).issue_key(actor_id=actor, label="bad-grant", scopes=["objects:read"])
        self.c.execute("UPDATE api_keys SET scopes=? WHERE key_id=?", (canonical_json(["evidence:confirm", "objects:read", "state:read", "authority:read"]), issued["key_id"]))
        code, error = self.call("POST", PREPARE_PATH, self.prep_body(), token=issued["token"])
        self.assertEqual((code, error["error"]["code"]), (403, "forbidden_actor_kind"))
        self.c.execute("UPDATE actors SET home_host_id=NULL WHERE actor_id=?", (ACTOR,))
        self.assertEqual(self.call("GET", "/v1/auth/me", token=self.human_token)[1]["home_host_id"], None)
        code, error = self.prepare()
        self.assertEqual((code, error["error"]["code"]), (409, "confirmation_identity_unbound"))

    def test_c03_system_with_forced_confirm_scope_is_still_forbidden_actor(self):
        principal = auth.Auth(self.c).authenticate(f"Bearer {self.system_token}")
        granted = ["evidence:confirm", "objects:read", "state:read", "authority:read"]
        self.c.execute("UPDATE api_keys SET scopes=? WHERE key_id=?", (canonical_json(granted), principal.key_id))
        self.assertEqual(self.c.execute("SELECT kind FROM actors WHERE actor_id=?", (principal.actor_id,)).fetchone()[0], "system")
        code, refusal = self.call("POST", PREPARE_PATH, self.prep_body(), token=self.system_token)
        self.assertEqual((code, refusal["error"]["code"]), (403, "forbidden_actor_kind"))
        self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_preparations").fetchone()[0], 0)

    def test_c05_reserved_raw_and_same_actor_other_key(self):
        _, prep = self.prepare()
        body = self.sub_body(prep)
        other = self.key(["evidence:confirm", "objects:read", "state:read", "authority:read", "evidence:read"])
        code, error = self.call("POST", SUBMIT_PATH, body, token=other)
        self.assertEqual((code, error["error"]["code"]), (403, "confirmation_owner_mismatch"))
        for extra in ("actor_id", "host_id", "source_system"):
            code, error = self.submit({**body, extra: HOST})
            self.assertEqual(code, 400, error)
        for event_type, system, payload in (("human.confirmation_submitted", "else", {"text": ""}),
                ("user.prompt", "core-web-confirmation-v1", {"text": ""}),
                ("evidence.confirmed", "else", {"text": "", "confirmation_source_kind": "WEB_STRUCTURED_V1"})):
            code, error = self.call("POST", "/v1/events", {"event_type": event_type, "source_system": system,
                "host_id": HOST, "task_id": self.task, "payload": payload}, token=self.human_token)
            self.assertEqual(code, 400, error)
            spec = NewEvent(event_type, system, ACTOR, "human", HOST, payload, clock.now(), task_id=self.task)
            with self.assertRaises(errors.InvalidRequest), db.transaction(self.c):
                EventStore(self.c, schema_version=12).append(spec)

    def test_c06_c07_reference_revision_and_rule_pair(self):
        body = self.prep_body()
        for changes, expected in (({"expected_task_revision": 1}, 409), ({"expected_step_revision": 1}, 409),
                ({"task_id": ids.new_id("tsk")}, 404), ({"step_id": ids.new_id("stp")}, 404),
                ({"confirmed_rule_id": ids.new_id("rul")}, 400),
                ({"confirmed_rule_id": ids.new_id("rul"), "confirmed_rule_version": 1}, 404)):
            code, error = self.prepare({**body, **changes})
            self.assertEqual(code, expected, error)
        _, prep = self.prepare()
        current, _ = self.truth()
        self.assertEqual(self.call("POST", f"/v1/tasks/{self.task}/criteria", {
            "acceptance_criteria": REQ, "expected_revision": current["revision"], "host_id": HOST}, token=self.human_token)[0], 200)
        code, error = self.submit(self.sub_body(prep))
        self.assertEqual((code, error["error"]["code"]), (409, "confirmation_context_conflict"))
        self.assertIn("revision", error["error"]["details"]["changed"])

    def test_c08_real_files_symlink_config_and_partial(self):
        _, prep = self.prepare()
        before = self.truth()
        (self.root / "source.txt").write_text("actual change")
        code, error = self.submit(self.sub_body(prep))
        self.assertEqual((code, error["error"]["code"]), (409, "confirmation_context_conflict"))
        self.assertEqual(self.truth(), before)
        (self.root / "source.txt").unlink()
        (self.root / "source.txt").symlink_to(self.root / "target.txt")
        (self.root / "target.txt").write_text("must not read")
        code, error = self.prepare()
        self.assertEqual((code, error["error"]["code"]), (503, "confirmation_scan_unavailable"))
        (self.root / "source.txt").unlink()
        (self.root / "source.txt").write_text("restored")
        _, prep = self.prepare()
        with patch.dict(os.environ, {"JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["target.txt"]'}):
            code, error = self.submit(self.sub_body(prep))
        self.assertEqual(error["error"]["details"]["changed"], ["configuration"])
        with patch("jasmine_core.web_confirmations.MAX_SCAN_BYTES", 2):
            code, error = self.prepare()
        self.assertEqual(error["error"]["details"]["reason"], "too_large")

    def test_c09_expiry_clock_and_budget(self):
        _, prep = self.prepare()
        for when, expected in ((clock.parse_rfc3339(prep["expires_at"]), (409, "confirmation_expired")),
                (clock.parse_rfc3339(prep["created_at"]) - timedelta(seconds=1), (503, "confirmation_clock_invalid"))):
            with patch("jasmine_core.clock.now", return_value=when):
                code, error = self.submit(self.sub_body(prep))
            self.assertEqual((code, error["error"]["code"]), expected)
        with patch("jasmine_core.web_confirmations.REQUEST_SECONDS", -1):
            code, error = self.prepare()
        self.assertEqual((code, error["error"]["code"]), (503, "confirmation_scan_unavailable"))

    def test_c10_c22_exact_replays_after_drift_and_revocation(self):
        body = self.prep_body()
        code, prep = self.prepare(body)
        code, replay = self.prepare(body)
        self.assertEqual((code, replay), (200, {**prep, "replayed": True}))
        code, error = self.prepare({**body, "target_status": "ACCEPTED"})
        self.assertEqual((code, error["error"]["code"]), (409, "event_id_conflict"))
        sub_body = self.sub_body(prep)
        code, result = self.submit(sub_body)
        self.assertEqual(code, 201, result)
        (self.root / "source.txt").write_text("changed after success")
        with patch("jasmine_core.fingerprint.capture", side_effect=AssertionError("must not scan replay")), patch("jasmine_core.clock.now", return_value=clock.parse_rfc3339(prep["expires_at"]) + timedelta(seconds=10)):
            self.assertEqual(self.submit(sub_body), (200, {**result, "replayed": True}))
            self.assertEqual(self.prepare(body), (200, {**prep, "replayed": True}))
        self.assertEqual(self.submit({**sub_body, "note": "different"})[1]["error"]["code"], "event_id_conflict")
        self.assertEqual(self.submit({**sub_body, "preparation_id": ids.new_id("evt")})[1]["error"]["code"], "event_id_conflict")
        key = auth.Auth(self.c).authenticate(f"Bearer {self.human_token}")
        auth.Auth(self.c).revoke(key.key_id)
        self.assertEqual(self.submit(sub_body)[0], 401)
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 1)

    def test_c18_strict_request_and_material_types(self):
        for value in (float("nan"), b"bytes", (1, 2), {1: "bad"}, "\ud800", [None] * 50_001):
            with self.assertRaises(errors.CoreError):
                strict_json(value, stored=True)
        for raw in (b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}', b'{"x":NaN}', b'{"x":"\\ud800"}', b'[]'):
            request = Request("POST", PREPARE_PATH, headers={"authorization": f"Bearer {self.human_token}", "content-type": "application/json"}, body=raw)
            result = dispatch(Core(self.c), request)
            self.assertEqual(result.status, 400, result.body)
            self.assertEqual(result.headers["Cache-Control"], "no-store")
        for body in ({**self.prep_body(), "expected_task_revision": True}, {**self.prep_body(), "actor_id": ACTOR}):
            self.assertEqual(self.prepare(body)[0], 400)
        self.assertEqual(self.call("POST", PREPARE_PATH + "?x=1", self.prep_body(), token=self.human_token)[0], 400)
        response = dispatch(Core(self.c), Request("POST", PREPARE_PATH, headers={"authorization": f"Bearer {self.human_token}"}, body=b'{}'))
        self.assertEqual(response.status, 415)
        response = dispatch(Core(self.c), Request("POST", PREPARE_PATH, headers={"authorization": f"Bearer {self.human_token}", "content-type": "application/json"}, body=b' ' * 16385))
        self.assertEqual(response.status, 413)

    def test_c18_c20_raw_note_and_best_effort_audit(self):
        _, prep = self.prepare()
        note = "token=synthetic-not-a-real-secret-123456789; <script>alert(1)</script> 保留空格  "
        body = self.sub_body(prep, note)
        code, result = self.submit(body)
        self.assertEqual(code, 201, result)
        self.assertEqual(result["source_event"]["payload"]["text"], note)
        self.assertEqual(self.status(prep)[1]["consumption"]["receipt"], result)
        self.assertEqual(self.submit(body), (200, {**result, "replayed": True}))
        audit = self.c.execute("SELECT * FROM audit_log WHERE path=? ORDER BY rowid DESC LIMIT 1", (SUBMIT_PATH,)).fetchone()
        self.assertIsNotNone(audit)
        self.assertNotIn(note, str(dict(audit)))
        # An audit exception is not allowed to roll back the atomic receipt.
        _, other = self.prepare()
        with patch("jasmine_core.audit.AuditLog.record", side_effect=RuntimeError("synthetic audit outage")):
            code, result = self.submit(self.sub_body(other))
        self.assertEqual(code, 201, result)
        self.assertEqual(self.status(other)[1]["availability"], "CONSUMED")

    def test_c04_c05_c10_registered_host_drift_and_other_actor(self):
        request = self.prep_body()
        _, prep = self.prepare(request)
        body = self.sub_body(prep)
        self.assertEqual(self.submit(body)[0], 201)
        actor, host = ids.new_id("act"), ids.new_id("hst")
        with db.transaction(self.c):
            registry.Registry(self.c).upsert_host(host, display_name="another registered host")
            registry.Registry(self.c).upsert_actor(actor, kind="human", home_host_id=host)
        key = auth.Auth(self.c).issue_key(actor_id=actor, label="other actor", scopes=["evidence:confirm", "objects:read", "state:read", "authority:read", "evidence:read"])
        self.assertEqual(self.call("POST", SUBMIT_PATH, body, token=key["token"])[1]["error"]["code"], "confirmation_owner_mismatch")
        self.c.execute("UPDATE actors SET home_host_id=? WHERE actor_id=?", (host, ACTOR))
        self.assertEqual(self.submit(body)[1]["error"]["details"]["changed"], ["identity"])
        self.assertEqual(self.prepare(request)[1]["error"]["details"]["changed"], ["identity"])

    def test_c18_illegal_current_server_snapshot_returns_integrity_error(self):
        original = fingerprint.capture()
        for changes in ({"selected_hashes": {"source.txt": b"bytes"}}, {"dirty": float("nan")}, {"extra_paths": ("source.txt",)}, {"coverage": {"wrong": "shape"}}):
            with patch("jasmine_core.fingerprint.capture", return_value={**original, **changes}):
                code, refusal = self.prepare()
            self.assertEqual((code, refusal["error"]["code"]), (409, "confirmation_integrity_error"))
        self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_preparations").fetchone()[0], 0)

    def test_c22_revoked_confirmation_key_does_not_erase_historical_evidence(self):
        _, prep = self.prepare()
        body = self.sub_body(prep)
        code, receipt = self.submit(body)
        self.assertEqual(code, 201, receipt)
        old_token = self.human_token
        principal = auth.Auth(self.c).authenticate(f"Bearer {old_token}")
        auth.Auth(self.c).revoke(principal.key_id)
        self.assertEqual(self.call("POST", SUBMIT_PATH, body, token=old_token)[0], 401)
        self.human_token = self.key(["objects:read", "state:write", "state:accept", "state:read", "evidence:read"])
        code, verified = self.transition("VERIFIED", self.truth()[1]["revision"], self.human_token)
        self.assertEqual(code, 200, verified)
        self.assertIn(receipt["evidence"]["evidence_id"], verified["evidence_ids"])
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 1)

    def test_c06_cross_task_step_is_400_without_a_partial_preparation(self):
        code, task = self.call("POST", "/v1/tasks", {"title": "other task", "project_id": self.project, "host_id": HOST,
            "acceptance_criteria": REQ}, token=self.human_token)
        self.assertEqual(code, 201, task)
        code, step = self.call("POST", f'/v1/tasks/{task["object"]["task_id"]}/steps', {"title": "other step", "host_id": HOST,
            "expected_revision": 1, "acceptance_criteria": REQ}, token=self.human_token)
        self.assertEqual(code, 201, step)
        code, refusal = self.prepare({**self.prep_body(), "step_id": step["step"]["step_id"]})
        self.assertEqual((code, refusal["error"]["code"]), (400, "invalid_request"))
        self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_preparations").fetchone()[0], 0)

    def test_c23_current_read_scope_rechecks(self):
        prep_body = self.prep_body()
        _, prep = self.prepare(prep_body)
        body = self.sub_body(prep)
        _, receipt = self.submit(body)
        principal = auth.Auth(self.c).authenticate(f"Bearer {self.human_token}")
        self.c.execute("UPDATE api_keys SET scopes=? WHERE key_id=?", ('["evidence:confirm"]', principal.key_id))
        self.assertEqual(self.status(prep)[0], 403)
        self.assertEqual(self.prepare(prep_body)[0], 403)
        self.assertEqual(self.submit(body), (200, {**receipt, "replayed": True}))
        self.assertNotIn("review_document", receipt)

# Inherit only fixture/helper methods, avoiding duplicate legacy test execution.
for _name in dir(EvidenceHttp):
    if _name.startswith("test_") and _name not in WebHttp.__dict__:
        setattr(WebHttp, _name, None)

del EvidenceHttp
