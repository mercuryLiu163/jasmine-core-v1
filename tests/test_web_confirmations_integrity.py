"""Component simulation with actual SQLite concurrency, scans and subprocess crashes."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import test_web_confirmations as helpers
from jasmine_core import auth, db, errors, ids
from jasmine_core.canonical import canonical_json
from jasmine_core.evidence import EvidenceStore
from jasmine_core.web_confirmations import WebConfirmationStore, PREPARE_PATH, SUBMIT_PATH, digest
from test_api import HOST


class WebIntegrity(helpers.WebHttp):
    def test_c11_barrier_concurrent_prepare_and_submit_same_id(self):
        original = WebConfirmationStore._scan
        for operation in ("prepare", "submit"):
            body = self.prep_body() if operation == "prepare" else self.sub_body(self.prepare()[1])
            barrier = threading.Barrier(2, timeout=10)
            results = []
            def scan(store, deadline):
                result = original(store, deadline)
                barrier.wait() # both early lookup misses and scans must finish
                return result
            def execute():
                results.append(self.prepare(body) if operation == "prepare" else self.submit(body))
            with patch.object(WebConfirmationStore, "_scan", scan):
                threads = [threading.Thread(target=execute) for _ in range(2)]
                for thread in threads: thread.start()
                for thread in threads: thread.join(15)
                self.assertTrue(all(not t.is_alive() for t in threads))
            self.assertEqual(sorted(x[0] for x in results), [200, 201], results)
            first = next(x[1] for x in results if x[0] == 201)
            replay = next(x[1] for x in results if x[0] == 200)
            self.assertEqual(replay, {**first, "replayed": True})
        self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_submissions").fetchone()[0], 1)

    def test_c11_different_command_ids_only_one_consumes(self):
        _, prep = self.prepare()
        bodies = [self.sub_body(prep) for _ in range(2)]
        barrier = threading.Barrier(2, timeout=10)
        original = WebConfirmationStore._scan
        results = []
        def scan(store, deadline):
            result = original(store, deadline)
            barrier.wait()
            return result
        with patch.object(WebConfirmationStore, "_scan", scan):
            threads = [threading.Thread(target=lambda body=b: results.append(self.submit(body))) for b in bodies]
            for t in threads: t.start()
            for t in threads: t.join(15)
        self.assertEqual(sorted(x[0] for x in results), [201, 409], results)
        failed = next(x[1] for x in results if x[0] == 409)
        self.assertEqual(failed["error"]["code"], "confirmation_consumed")
        self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 1)

    def test_c12_each_submit_write_rolls_back(self):
        _, prep = self.prepare()
        for point in ("source", "command", "evidence", "projection"):
            body = self.sub_body(prep)
            original_append = WebConfirmationStore._append
            original_insert = EvidenceStore._insert
            original_projection = WebConfirmationStore._insert_projection
            def append(store, event_type, *args, **kwargs):
                value = original_append(store, event_type, *args, **kwargs)
                if (point == "source" and event_type == "human.confirmation_submitted") or (point == "command" and event_type == "evidence.confirmed"):
                    raise RuntimeError("injected after " + point)
                return value
            def insert(store, *args, **kwargs):
                value = original_insert(store, *args, **kwargs)
                if point == "evidence": raise RuntimeError("injected after evidence")
                return value
            def projection(store, *args, **kwargs):
                value = original_projection(store, *args, **kwargs)
                if point == "projection": raise RuntimeError("injected after projection")
                return value
            before = self.c.execute("SELECT count(*) FROM events").fetchone()[0]
            with patch.object(WebConfirmationStore, "_append", append), patch.object(EvidenceStore, "_insert", insert), patch.object(WebConfirmationStore, "_insert_projection", projection):
                code, result = self.submit(body)
            self.assertEqual(code, 500, result)
            self.assertEqual(self.c.execute("SELECT count(*) FROM events").fetchone()[0], before)
            self.assertEqual(self.c.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
            self.assertEqual(self.status(prep)[1]["availability"], "OPEN")

    def test_c12_real_process_crash_before_and_after_commit(self):
        child = '''
import sys,json,os
from jasmine_core import db,auth
from jasmine_core.web_confirmations import WebConfirmationStore
value=json.load(sys.stdin)
conn=db.connect(value['db'])
store=WebConfirmationStore(conn,schema_version=12)
principal=auth.Auth(conn).authenticate('Bearer '+value['token'])
if value['point']=='before':
    original=store._insert_projection
    def projection(*args,**kwargs):
        original(*args,**kwargs)
        print('actual process crash before COMMIT',flush=True)
        os._exit(73)
    store._insert_projection=projection
store.submit(value['body'],principal,'component-crash')
print('actual process crash after COMMIT before response',flush=True)
os._exit(74)
'''
        for point, returncode in (("before", 73), ("after", 74)):
            _, prep = self.prepare()
            body = self.sub_body(prep)
            before = self.c.execute("SELECT count(*) FROM evidence").fetchone()[0]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            proc = subprocess.run([sys.executable, "-c", child], input=json.dumps({"db": str(self.db_path), "token": self.human_token, "body": body, "point": point}),
                                  text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=15)
            self.assertEqual(proc.returncode, returncode, proc.stderr)
            self.assertIn("actual process crash", proc.stdout)
            # Fresh connection, not a stale in-process read, proves restart recovery.
            restarted = db.connect(self.db_path)
            try:
                principal = auth.Auth(restarted).authenticate(f"Bearer {self.human_token}")
                store = WebConfirmationStore(restarted, schema_version=12)
                status = store.status(prep["preparation_id"], principal)
                self.assertEqual(status["availability"], "OPEN" if point == "before" else "CONSUMED")
                recovered = store.submit(body, principal, "component-restarted")
                self.assertEqual(recovered["replayed"], point == "after")
                self.assertEqual(restarted.execute("SELECT count(*) FROM evidence").fetchone()[0], before + 1)
                print(json.dumps({"qualification": "component_simulation_actual_process_crash", "operation": "submit",
                    "point": point, "returncode": proc.returncode, "child_stdout": proc.stdout.strip(),
                    "restarted_availability": status["availability"], "recovered_replayed": recovered["replayed"],
                    "command_event_id": body["event_id"], "receipt_sha256": digest(recovered)}, ensure_ascii=False))
            finally:
                restarted.close()

    def test_c12_prepare_each_write_rolls_back_and_process_crash_recovers(self):
        original_append = WebConfirmationStore._append
        original_snapshot = EvidenceStore._snapshot
        original_projection = WebConfirmationStore._insert_projection
        for point in ("event", "snapshot", "projection"):
            def append(store, *args, **kwargs):
                value = original_append(store, *args, **kwargs)
                if point == "event": raise RuntimeError("after preparation event")
                return value
            def snapshot(store, *args, **kwargs):
                value = original_snapshot(store, *args, **kwargs)
                if point == "snapshot": raise RuntimeError("after preparation snapshot")
                return value
            def projection(store, *args, **kwargs):
                value = original_projection(store, *args, **kwargs)
                if point == "projection": raise RuntimeError("after preparation projection")
                return value
            before = self.c.execute("SELECT count(*) FROM events").fetchone()[0]
            with patch.object(WebConfirmationStore, "_append", append), patch.object(EvidenceStore, "_snapshot", snapshot), patch.object(WebConfirmationStore, "_insert_projection", projection):
                self.assertEqual(self.prepare()[0], 500)
            self.assertEqual(self.c.execute("SELECT count(*) FROM events").fetchone()[0], before)
            self.assertEqual(self.c.execute("SELECT count(*) FROM workspace_fingerprints").fetchone()[0], 0)
            self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_preparations").fetchone()[0], 0)
        child = """
import sys,json,os
from jasmine_core import db,auth
from jasmine_core.web_confirmations import WebConfirmationStore
v=json.load(sys.stdin)
c=db.connect(v['db'])
s=WebConfirmationStore(c,schema_version=12)
p=auth.Auth(c).authenticate('Bearer '+v['token'])
if v['point']=='before':
    original=s._insert_projection
    def projection(*args,**kwargs):
        original(*args,**kwargs)
        print('actual prepare process crash before COMMIT',flush=True)
        os._exit(75)
    s._insert_projection=projection
s.prepare(v['body'],p,'component-prepare-crash')
print('actual prepare process crash after COMMIT before response',flush=True)
os._exit(76)
"""
        for point, code in (("before", 75), ("after", 76)):
            body = self.prep_body()
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            proc = subprocess.run([sys.executable, "-c", child], input=json.dumps({"db": str(self.db_path), "token": self.human_token, "body": body, "point": point}),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=15)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertIn("actual prepare process crash", proc.stdout)
            restored = db.connect(self.db_path)
            try:
                store = WebConfirmationStore(restored, schema_version=12)
                principal = auth.Auth(restored).authenticate(f"Bearer {self.human_token}")
                if point == "before":
                    with self.assertRaises(errors.NotFound): store.status(body["event_id"], principal)
                else:
                    self.assertEqual(store.status(body["event_id"], principal)["availability"], "OPEN")
                replay = store.prepare(body, principal, "component-prepare-restart")
                self.assertEqual(replay["replayed"], point == "after")
                print(json.dumps({"qualification": "component_simulation_actual_process_crash", "operation": "prepare",
                    "point": point, "returncode": proc.returncode, "child_stdout": proc.stdout.strip(),
                    "restarted_availability": "NOT_FOUND" if point == "before" else "OPEN", "recovered_replayed": replay["replayed"],
                    "preparation_id": body["event_id"], "receipt_sha256": digest(replay)}, ensure_ascii=False))
            finally: restored.close()

    def test_c13_each_original_record_tamper_refuses_read_replay_and_state(self):
        # Each independent case damages only a disposable DB backup.
        _, prep = self.prepare()
        body = self.sub_body(prep, "token=synthetic-original-123456789")
        _, receipt = self.submit(body)
        cases = ("source_text", "source_text_rehashed", "source_hash", "prepare_json", "command_digest", "evidence", "receipt", "submission_ref")
        for case in cases:
            path = Path(self._tmp.name) / (case + ".db")
            damaged = db.connect(path)
            self.c.backup(damaged)
            try:
                for trigger in damaged.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                    damaged.execute('DROP TRIGGER "' + trigger[0] + '"')
                if case in ("source_text", "source_text_rehashed"):
                    payload = dict(receipt["source_event"]["payload"])
                    payload["text"] = "token=synthetic-altered-123456789" # same masked view, different bytes
                    damaged.execute("UPDATE events SET payload_json=? WHERE event_id=?", (canonical_json(payload), receipt["source_event"]["event_id"]))
                    if case == "source_text_rehashed":
                        altered_source = {**receipt["source_event"], "payload": payload}
                        fields = {key: altered_source[key] for key in ("event_type", "source_system", "source_event_id", "actor_id", "host_id", "session_id", "project_id", "task_id", "payload")}
                        fields["occurred_at"] = None
                        altered_source["body_sha256"] = digest(fields)
                        damaged.execute("UPDATE events SET body_sha256=? WHERE event_id=?", (altered_source["body_sha256"], altered_source["event_id"]))
                        changed_receipt = {**receipt, "source_event": altered_source}
                        damaged.execute("UPDATE web_confirmation_submissions SET response_json=?", (canonical_json(changed_receipt),))
                elif case == "source_hash":
                    damaged.execute("UPDATE events SET body_sha256=? WHERE event_id=?", ("0" * 64, receipt["source_event"]["event_id"]))
                elif case == "prepare_json":
                    damaged.execute("UPDATE web_confirmation_preparations SET review_json='{}'")
                elif case == "command_digest":
                    payload = dict(receipt["event"]["payload"])
                    payload["semantic_request_sha256"] = "0" * 64
                    damaged.execute("UPDATE events SET payload_json=? WHERE event_id=?", (canonical_json(payload), receipt["event"]["event_id"]))
                elif case == "evidence":
                    damaged.execute("UPDATE evidence SET result_json='{\"confirmed\":false}'")
                elif case == "receipt":
                    damaged.execute("UPDATE web_confirmation_submissions SET response_json='{}'")
                else:
                    damaged.execute("UPDATE web_confirmation_submissions SET source_event_id=?", (self.truth()[0]["source_event_id"],))
                principal = auth.Auth(damaged).authenticate(f"Bearer {self.human_token}")
                store = WebConfirmationStore(damaged, schema_version=12)
                for action in (lambda: store.status(prep["preparation_id"], principal), lambda: store.submit(body, principal, "tamper-replay")):
                    with self.assertRaises(errors.CoreError) as raised: action()
                    self.assertEqual(raised.exception.code, "confirmation_integrity_error", case)
                from jasmine_core.api.handlers import Core
                with self.assertRaises(errors.MissingEvidence):
                    Core(damaged).state.transition_step(self.step, {"status": "VERIFIED", "expected_revision": self.truth()[1]["revision"], "host_id": HOST}, actor_id=principal.actor_id, actor_kind="human", can_accept=True)
            finally:
                damaged.close()

    def test_c21_scan_allows_another_actual_http_writer_and_reader(self):
        entered, release = threading.Event(), threading.Event()
        original = WebConfirmationStore._scan
        result = []
        body = self.prep_body()
        def scan(store, deadline):
            self.assertFalse(store.conn.in_transaction)
            sampled = original(store, deadline)
            entered.set()
            self.assertTrue(release.wait(10))
            return sampled
        with patch.object(WebConfirmationStore, "_scan", scan):
            worker = threading.Thread(target=lambda: result.append(self.prepare(body)))
            worker.start()
            self.assertTrue(entered.wait(5))
            try:
                self.assertEqual(self.call("POST", "/v1/projects", {"name": "concurrent-real-writer", "host_id": HOST}, token=self.human_token)[0], 201)
                self.assertEqual(self.call("GET", f"/v1/steps/{self.step}", token=self.human_token)[0], 200)
            finally: release.set()
            worker.join(10)
        self.assertEqual(result[0][0], 201, result)

    def test_c07_writer_phase_detects_actual_state_edit_during_scan(self):
        entered, release = threading.Event(), threading.Event()
        original = WebConfirmationStore._scan
        result = []
        body = self.prep_body()
        def scan(store, deadline):
            sampled = original(store, deadline)
            entered.set()
            self.assertTrue(release.wait(10))
            return sampled
        with patch.object(WebConfirmationStore, "_scan", scan):
            worker = threading.Thread(target=lambda: result.append(self.prepare(body)))
            worker.start()
            self.assertTrue(entered.wait(5))
            try:
                task, _ = self.truth()
                code, edit = self.call("POST", f"/v1/tasks/{self.task}/criteria", {"acceptance_criteria": helpers.REQ,
                    "expected_revision": task["revision"], "host_id": HOST}, token=self.human_token)
                self.assertEqual(code, 200, edit)
            finally: release.set()
            worker.join(10)
        self.assertEqual((result[0][0], result[0][1]["error"]["code"]), (409, "confirmation_context_conflict"))
        self.assertEqual(self.c.execute("SELECT count(*) FROM web_confirmation_preparations").fetchone()[0], 0)

# Fixture inheritance only.
for _name in dir(helpers.WebHttp):
    if _name.startswith("test_") and _name not in WebIntegrity.__dict__:
        setattr(WebIntegrity, _name, None)
