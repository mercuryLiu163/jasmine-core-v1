"""The Core API over a real socket, on a real migrated database.

These cover P0-T02, P0-T03, P0-T06 and the audit/redaction requirements. The
server is started on an ephemeral loopback port in a background thread, so the
tests exercise the real `http.server` path, not a stubbed one.
"""

from __future__ import annotations

import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from support import DbTestCase  # noqa: E402

from jasmine_core import SCHEMA_VERSION, db, registry  # noqa: E402
from jasmine_core.api.server import Application, _Handler, _LoopbackHTTPServer, serve  # noqa: E402
from jasmine_core.api.dispatch import dispatch  # noqa: E402
from jasmine_core.api.request import Request  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402
from http.server import ThreadingHTTPServer

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"
TEXT = "P0 真实对话固定测试短句，无敏感数据。"


class LoopbackServerBinding(unittest.TestCase):
    def test_actual_serve_path_answers_health_without_reverse_dns(self) -> None:
        logs = []
        def exercise(server):
            worker = threading.Thread(target=ThreadingHTTPServer.serve_forever,
                                      args=(server,), kwargs={"poll_interval": 0.01})
            worker.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/v1/health"
                with urllib.request.urlopen(url, timeout=2) as response:
                    self.assertEqual(json.load(response)["status"], "ok")
                self.assertTrue(any(f":{server.server_port} " in text for text in logs))
            finally:
                server.shutdown()
                worker.join(timeout=2)
        with tempfile.TemporaryDirectory(prefix="jasmine-no-rdns-") as temp, \
             patch("socket.getfqdn", side_effect=AssertionError("reverse DNS must not run")) as resolver, \
             patch.object(_LoopbackHTTPServer, "serve_forever", autospec=True, side_effect=exercise):
            serve(Path(temp) / "core.db", port=0, log=logs.append)
            resolver.assert_not_called()

    def test_binding_listens_without_reverse_dns(self) -> None:
        with patch("socket.getfqdn", side_effect=AssertionError("reverse DNS must not run")) as resolver:
            server = _LoopbackHTTPServer(("127.0.0.1", 0), _Handler)
        try:
            resolver.assert_not_called()
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertEqual(server.server_port, server.server_address[1])
            self.assertGreater(server.server_port, 0)
            self.assertEqual(server.address_family, socket.AF_INET)
            with socket.create_connection(server.server_address, timeout=1):
                pass
        finally:
            server.server_close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ApiTestCase(unittest.TestCase):
    """A migrated database, a registered host and actor, and a live server."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-api-")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "core.db"
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        migrate(conn)
        reg = registry.Registry(conn)
        with db.transaction(conn):
            reg.upsert_host(HOST, display_name="test host")
            reg.upsert_actor(ACTOR, kind="human", display_name="tester", home_host_id=HOST)
        conn.close()

        self.port = free_port()
        self.app = Application(self.db_path)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self.server.app = self.app
        self.server.daemon_threads = True
        # A short poll interval keeps `shutdown()` from adding 0.5s to every test.
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self._stop)

        self.admin_token = self._bootstrap_admin()

    def _stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.app.close()

    def _bootstrap_admin(self) -> str:
        from jasmine_core import auth

        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return auth.Auth(conn).issue_key(
            actor_id=ACTOR, label="test-admin",
            scopes=["admin", "objects:read", "objects:write", "events:read", "events:write"],
        )["token"]

    def call(self, method: str, path: str, body=None, token: str | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {"Accept": "application/json"}
        if data is not None:
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        request_headers.update(headers or {})
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    def key(self, scopes: list[str], label: str = "k") -> str:
        from jasmine_core import auth

        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return auth.Auth(conn).issue_key(actor_id=ACTOR, label=label, scopes=scopes)["token"]

    def event_body(self, **overrides) -> dict:
        body = {
            "event_type": "user.prompt", "source_system": "test", "host_id": HOST,
            "payload": {"text": TEXT}, "occurred_at": "2026-09-29T12:00:00.000000Z",
        }
        body.update(overrides)
        return body


class HealthAndSchema(ApiTestCase):
    def test_health_needs_no_token(self) -> None:
        status, body = self.call("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["schema_version"], SCHEMA_VERSION)

    def test_schema_meta_reports_the_applied_migrations(self) -> None:
        status, body = self.call("GET", "/v1/meta/schema")
        self.assertEqual(status, 200)
        self.assertEqual([m["name"] for m in body["migrations"]],
                         ["m0001_baseline", "m0002_api_auth_audit", "m0003_authority", "m0004_task_step_state", "m0005_evidence_fingerprint", "m0006_codex_exec_observations", "m0007_interpretations", "m0008_resolutions", "m0009_continuity", "m0010_context"])
        self.assertEqual(body["schema_version"], SCHEMA_VERSION)

    def test_an_unknown_route_is_404(self) -> None:
        status, body = self.call("GET", "/v1/nope", token=self.admin_token)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "route_not_found")

    def test_a_wrong_method_is_405_and_names_what_is_allowed(self) -> None:
        status, body = self.call("DELETE", "/v1/events", token=self.admin_token)
        self.assertEqual(status, 405)
        self.assertEqual(body["error"]["code"], "method_not_allowed")
        self.assertEqual(body["error"]["details"]["allowed"], ["GET", "POST"])

    def test_access_log_omits_credentials_in_url_path(self) -> None:
        stderr = io.StringIO()
        fixture_secret = "sk-fixture-path-secret"
        with redirect_stderr(stderr):
            for path in (f"/v1/events/Bearer%20{fixture_secret}",
                         f"/v1/events/{fixture_secret}"):
                status, _ = self.call("GET", path, token=self.admin_token)
                self.assertEqual(status, 400)
        output = stderr.getvalue()
        self.assertEqual(output.count("GET -> 400"), 2)
        self.assertNotIn(fixture_secret, output)
        self.assertNotIn("Bearer%20", output)

    def test_unknown_http_method_is_not_echoed_to_stderr(self) -> None:
        fixture_secret = "sk-fixture-method-secret"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
                connection.sendall(
                    f"{fixture_secret} /v1/health HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\nConnection: close\r\n\r\n".encode("ascii")
                )
                response = bytearray()
                while chunk := connection.recv(4096):
                    response.extend(chunk)
            status, _ = self.call("GET", "/v1/health")
        self.assertIn(b"501", response.split(b"\r\n", 1)[0])
        self.assertEqual(status, 200)
        output = stderr.getvalue()
        self.assertNotIn(fixture_secret, output)
        self.assertIn("UNKNOWN ->", output)
        self.assertIn("GET -> 200", output)

    def test_unhandled_error_log_omits_request_path_and_exception_message(self) -> None:
        fixture_secret = "sk-fixture-path-secret"
        stderr = io.StringIO()
        request = Request(method="GET", path=f"/v1/events/Bearer%20{fixture_secret}")
        with patch("jasmine_core.api.dispatch.handlers.resolve",
                   side_effect=RuntimeError(f"failed at {fixture_secret}")):
            with redirect_stderr(stderr):
                response = dispatch(self.app._core(), request)
        self.assertEqual(response.status, 500)
        output = stderr.getvalue()
        self.assertIn("unhandled error on GET: RuntimeError", output)
        self.assertNotIn(fixture_secret, output)
        self.assertNotIn("Bearer%20", output)


class P0T02ThroughTheApi(ApiTestCase):
    def test_project_task_session_and_event_are_created_and_related(self) -> None:
        token = self.admin_token
        status, project = self.call("POST", "/v1/projects",
                                    {"name": "demo", "host_id": HOST}, token=token)
        self.assertEqual(status, 201)
        project_id = project["object"]["project_id"]
        self.assertEqual(project["object"]["source_event_id"], project["event"]["event_id"])
        self.assertEqual(project["object"]["revision"], 1)

        status, task = self.call("POST", "/v1/tasks",
                                 {"title": "写 ADR", "project_id": project_id, "host_id": HOST},
                                 token=token)
        self.assertEqual(status, 201)
        task_id = task["object"]["task_id"]

        status, session = self.call("POST", "/v1/sessions",
                                    {"project_id": project_id, "task_id": task_id, "host_id": HOST},
                                    token=token)
        self.assertEqual(status, 201)
        session_id = session["object"]["session_id"]
        self.assertEqual(session["object"]["actor_id"], ACTOR)
        self.assertEqual(session["object"]["host_id"], HOST)

        status, event = self.call("POST", "/v1/events", self.event_body(
            session_id=session_id, task_id=task_id, project_id=project_id,
            source_event_id="codex-turn-1"), token=token)
        self.assertEqual(status, 201)
        event_id = event["event"]["event_id"]

        # Each of the three objects is linked to exactly one source Event.
        for kind, key, created in (("projects", "project_id", project), ("tasks", "task_id", task),
                                   ("sessions", "session_id", session)):
            status, fetched = self.call("GET", f"/v1/{kind}/{created['object'][key]}", token=token)
            self.assertEqual(status, 200)
            self.assertEqual(fetched[kind[:-1] if kind != "sessions" else "session"][
                "source_event_id"], created["event"]["event_id"])

        status, stored = self.call("GET", f"/v1/events/{event_id}", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(stored["event"]["payload"]["text"], TEXT)
        self.assertEqual(stored["event"]["actor_id"], ACTOR)
        self.assertEqual(stored["event"]["host_id"], HOST)

    def test_the_location_header_points_at_the_new_object(self) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/projects",
            data=json.dumps({"name": "demo", "host_id": HOST}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.admin_token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            location = response.headers["Location"]
            self.assertEqual(location, f"/v1/projects/{json.loads(response.read())['object']['project_id']}")

    def test_a_request_id_is_always_returned(self) -> None:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/health", method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertTrue(response.headers.get("X-Request-Id"))

    def test_an_oversized_body_is_refused(self) -> None:
        body = self.event_body(payload={"text": "x" * (1024 * 1024 + 10)})
        status, response = self.call("POST", "/v1/events", body, token=self.admin_token)
        self.assertEqual(status, 413)
        self.assertEqual(response["error"]["code"], "payload_too_large")

    def test_a_non_json_content_type_is_refused(self) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/projects",
            data=b"name=demo", headers={"Content-Type": "application/x-www-form-urlencoded",
                                       "Authorization": f"Bearer {self.admin_token}"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 415)

    def test_malformed_json_is_refused(self) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/projects", data=b"{not json",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.admin_token}"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)


class P0T04ThroughTheApi(ApiTestCase):
    def test_replaying_an_event_id_returns_the_stored_event_and_adds_nothing(self) -> None:
        body = self.event_body(event_id="evt_01K742SG00KSNSRN81BXC5ZAZC")
        first_status, first = self.call("POST", "/v1/events", body, token=self.admin_token)
        second_status, second = self.call("POST", "/v1/events", body, token=self.admin_token)
        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 200)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["event"]["event_id"], second["event"]["event_id"])
        self.assertEqual(first["event"]["seq"], second["event"]["seq"])
        _, listed = self.call("GET", "/v1/events", token=self.admin_token)
        self.assertEqual(listed["count"], 1)

    def test_the_same_event_id_with_different_content_is_a_conflict(self) -> None:
        event_id = "evt_01K742SG00KSNSRN81BXC5ZAZC"
        self.call("POST", "/v1/events", self.event_body(event_id=event_id, payload={"text": "first"}),
                  token=self.admin_token)
        status, body = self.call(
            "POST", "/v1/events",
            self.event_body(event_id=event_id, payload={"text": "second"}), token=self.admin_token)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "event_id_conflict")
        self.assertIn("existing_body_sha256", body["error"]["details"])
        _, stored = self.call("GET", f"/v1/events/{event_id}", token=self.admin_token)
        self.assertEqual(stored["event"]["payload"]["text"], "first")

    def test_a_repeated_source_event_id_is_refused(self) -> None:
        self.call("POST", "/v1/events", self.event_body(source_event_id="codex-turn-1"),
                  token=self.admin_token)
        status, body = self.call(
            "POST", "/v1/events",
            self.event_body(source_event_id="codex-turn-1", event_id="evt_01K742SG00KSNSRN81BXC5ZAZD"),
            token=self.admin_token)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "source_event_duplicate")

    def test_retrying_a_create_does_not_duplicate_the_object(self) -> None:
        body = {"name": "demo", "host_id": HOST, "event_id": "evt_01K742SG00KSNSRN81BXC5ZAZC"}
        first_status, first = self.call("POST", "/v1/projects", body, token=self.admin_token)
        second_status, second = self.call("POST", "/v1/projects", body, token=self.admin_token)
        self.assertEqual((first_status, second_status), (201, 200))
        self.assertEqual(first["object"]["project_id"], second["object"]["project_id"])
        _, listed = self.call("GET", "/v1/projects", token=self.admin_token)
        self.assertEqual(listed["count"], 1)


class P0T06Authorization(ApiTestCase):
    def truth_counts(self) -> dict:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("events", "projects", "tasks", "sessions", "actors", "hosts")
        }

    def test_a_missing_token_is_refused(self) -> None:
        before = self.truth_counts()
        status, body = self.call("POST", "/v1/events", self.event_body())
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")
        self.assertEqual(self.truth_counts(), before)

    def test_an_unknown_token_is_refused(self) -> None:
        before = self.truth_counts()
        status, body = self.call("POST", "/v1/events", self.event_body(), token="not-a-real-token")
        self.assertEqual(status, 401)
        self.assertEqual(self.truth_counts(), before)

    def test_a_revoked_token_is_refused(self) -> None:
        from jasmine_core import auth

        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        issued = auth.Auth(conn).issue_key(actor_id=ACTOR, label="temp", scopes=["events:write"])
        status, _ = self.call("POST", "/v1/events", self.event_body(), token=issued["token"])
        self.assertEqual(status, 201)
        auth.Auth(conn).revoke(issued["key_id"])
        before = self.truth_counts()
        status, body = self.call("POST", "/v1/events", self.event_body(), token=issued["token"])
        self.assertEqual(status, 401)
        self.assertEqual(self.truth_counts(), before)

    def test_a_key_without_the_scope_is_refused_and_writes_nothing(self) -> None:
        read_only = self.key(["events:read"], label="reader")
        before = self.truth_counts()
        status, body = self.call("POST", "/v1/events", self.event_body(), token=read_only)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertEqual(self.truth_counts(), before)

    def test_a_read_only_key_cannot_create_objects(self) -> None:
        read_only = self.key(["objects:read"], label="reader")
        status, body = self.call("POST", "/v1/projects", {"name": "x", "host_id": HOST},
                                 token=read_only)
        self.assertEqual(status, 403)
        self.assertEqual(self.truth_counts()["projects"], 0)

    def test_minting_a_key_requires_the_admin_scope(self) -> None:
        writer = self.key(["events:write"], label="writer")
        status, _ = self.call("POST", "/v1/auth/keys",
                              {"label": "escalate", "scopes": ["admin"]}, token=writer)
        self.assertEqual(status, 403)

    def test_a_refusal_is_still_audited_without_leaking_the_token(self) -> None:
        status, _ = self.call("POST", "/v1/events", self.event_body(), token="Bearer sk-leak-me-not")
        self.assertEqual(status, 401)
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        from jasmine_core import audit

        entries = audit.AuditLog(conn).list(limit=50)
        denials = [e for e in entries if e["decision"] == "deny"]
        self.assertTrue(denials)
        self.assertEqual(denials[-1]["error_code"], "unauthenticated")
        raw = json.dumps(entries, ensure_ascii=False)
        self.assertNotIn("sk-leak-me-not", raw)
        self.assertNotIn(TEXT, raw)
        self.assertNotIn(self.admin_token, raw)

    def test_the_prompt_text_never_reaches_the_audit_log(self) -> None:
        self.call("POST", "/v1/events", self.event_body(), token=self.admin_token)
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        from jasmine_core import audit

        raw = json.dumps(audit.AuditLog(conn).list(limit=50), ensure_ascii=False)
        self.assertNotIn(TEXT, raw)

    def test_audit_rows_are_append_only(self) -> None:
        import sqlite3

        self.call("POST", "/v1/events", self.event_body(), token=self.admin_token)
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_log SET decision = 'allow'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_log")

    def test_the_token_is_never_stored_in_clear(self) -> None:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        rows = conn.execute("SELECT token_sha256 FROM api_keys").fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(len(row["token_sha256"]), 64)
        self.assertNotIn(self.admin_token.encode(), conn.execute(
            "SELECT group_concat(token_sha256) AS t FROM api_keys").fetchone()["t"].encode())


if __name__ == "__main__":
    unittest.main()
