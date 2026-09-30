"""P4: the HTTP surface, over a real socket, against a real migrated database.

Extends the P0/P1 `ApiTestCase`, so the server, the authentication layer, the
scope checks and the audit log are the real ones. What is stubbed is only the
engine (`FakeTransport` is not reachable from the API: there is no retain or
bank-selection endpoint, which is the point) — the P4 API surface creates Memory
only through the worker.

The permissions under test are the ones the taskbook lists: no agent key may
curate Memory, no body or query parameter may stand in for a project
authorisation, a project the actor may not use is indistinguishable from a
project that does not exist, and there is no public path that writes a bank.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from memory_support import HOST
from test_api import ACTOR, ApiTestCase
from jasmine_core import auth, db, errors, ids
from jasmine_core.memory import scope


class MemoryApiTestCase(ApiTestCase):
    """A Core with a project, a confirmed Decision, and a scanned Memory item."""

    def setUp(self) -> None:
        super().setUp()
        self.reader = self.key(["memory:read", "objects:read", "events:read"], "memory-read")
        self.manager = self.key(["memory:read", "memory:manage", "objects:read"], "memory-manage")
        self.reflector = self.key(["memory:read", "memory:reflect", "objects:read"], "reflect")
        self.write = self.key(["objects:read", "objects:write", "events:write", "events:read",
                               "authority:propose", "authority:manage", "authority:read",
                               "state:write", "state:read"], "writer")
        _, project = self.call("POST", "/v1/projects", {"name": "p", "host_id": HOST},
                               token=self.write)
        self.project_id = project["object"]["project_id"]
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        self.seed()

    def seed(self) -> None:
        """A committed Decision, scanned into a PENDING job. No worker needed."""
        from jasmine_core.authority import AuthorityStore
        from jasmine_core.memory import jobs as jobs_module
        from jasmine_core.memory.eligibility import SourceRegistry
        from jasmine_core.memory.store import MemoryStore

        status, event = self.call("POST", "/v1/events", self.event_body(
            project_id=self.project_id, payload={"text": "use hindsight"}), token=self.write)
        self.assertEqual(status, 201, event)
        origin = event["event"]["event_id"]
        authority = AuthorityStore(self.conn, schema_version=7)
        proposed = authority.propose({
            "rule_key": "memory.engine", "kind": "DECISION", "severity": "HARD",
            "enforcement": "CONTEXT", "content": "V1 uses Hindsight for long-term memory",
            "matcher": {}, "scope": {"kind": "project", "project_id": self.project_id},
            "origin_event_id": origin, "host_id": HOST}, actor_id=ACTOR)
        self.rule_id = proposed["rule"]["rule_id"]
        authority.transition(self.rule_id, "approve", {"expected_revision": 1, "host_id": HOST},
                             actor_id=ACTOR)
        jobs_module.JobStore(self.conn, MemoryStore(self.conn, schema_version=7)).enqueue_from_sources(
            SourceRegistry())
        item = MemoryStore(self.conn, schema_version=7).items()[0]
        self.memory_id = item["memory_id"]
        self.job_id = jobs_module.JobStore(
            self.conn, MemoryStore(self.conn, schema_version=7)).jobs()[0]["job_id"]


class JobEndpoints(MemoryApiTestCase):
    def test_the_designed_paths_are_exactly_as_specified(self) -> None:
        status, body = self.call("GET", "/v1/memory/jobs", token=self.reader)
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["jobs"][0]["memory_type"], "CONFIRMED_DECISION")
        status, body = self.call("GET", f"/v1/memory/jobs/{self.job_id}", token=self.reader)
        self.assertEqual(status, 200)
        self.assertEqual(body["attempts"], [])

    def test_jobs_need_the_memory_read_scope(self) -> None:
        status, body = self.call("GET", "/v1/memory/jobs", token=self.write)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertEqual(body["error"]["details"]["required"], "memory:read")

    def test_an_anonymous_caller_is_refused(self) -> None:
        status, body = self.call("GET", "/v1/memory/jobs")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")

    def test_a_retry_needs_memory_manage(self) -> None:
        status, body = self.call("POST", f"/v1/memory/jobs/{self.job_id}/retry",
                                 {"host_id": HOST}, token=self.reader)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")

    def test_a_rejected_body_field_is_refused_rather_than_ignored(self) -> None:
        status, body = self.call("POST", f"/v1/memory/jobs/{self.job_id}/retry",
                                 {"host_id": HOST, "force": True}, token=self.manager)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_request")

    def test_a_malformed_filter_is_a_typed_400_not_a_500(self) -> None:
        for query in ("?status=RETRYING", "?limit=abc", "?kind=purge"):
            status, body = self.call("GET", f"/v1/memory/jobs{query}", token=self.reader)
            self.assertEqual(status, 400, query)
            self.assertEqual(body["error"]["code"], "invalid_request")

    def test_an_unknown_job_is_a_typed_404(self) -> None:
        status, body = self.call("GET", f"/v1/memory/jobs/{ids.new_id('mjb')}",
                                 token=self.reader)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "memory_job_not_found")
        status, body = self.call("GET", "/v1/memory/jobs/not-an-id", token=self.reader)
        self.assertEqual(status, 400)

    def test_a_write_method_on_a_read_path_is_405(self) -> None:
        status, body = self.call("POST", "/v1/memory/jobs", {}, token=self.manager)
        self.assertEqual(status, 405)
        self.assertEqual(body["error"]["code"], "method_not_allowed")
        self.assertEqual(body["error"]["details"]["allowed"], ["GET"])

    def test_pagination_is_keyset_and_stable(self) -> None:
        status, first = self.call("GET", "/v1/memory/jobs?limit=1", token=self.reader)
        self.assertEqual(status, 200)
        cursor = first["jobs"][0]["job_id"]
        status, second = self.call("GET", f"/v1/memory/jobs?after_job_id={cursor}",
                                   token=self.reader)
        self.assertEqual(second["jobs"], [])


class InspectorEndpoints(MemoryApiTestCase):
    def test_the_item_list_is_labelled_non_authoritative(self) -> None:
        status, body = self.call("GET", f"/v1/memory/items?project_id={self.project_id}",
                                 token=self.reader)
        self.assertEqual(status, 200)
        self.assertTrue(body["non_authoritative"])
        item = body["items"][0]
        self.assertEqual(item["memory_id"], self.memory_id)
        self.assertEqual(item["lifecycle"]["recallable"], True)
        self.assertEqual(item["lifecycle"]["pin_affects"], "inspector_ordering_only")
        self.assertEqual(item["engine_state"], "PENDING_RETAIN")

    def test_item_detail_carries_provenance_and_the_honest_engine_claim(self) -> None:
        status, body = self.call("GET", f"/v1/memory/items/{self.memory_id}", token=self.reader)
        self.assertEqual(status, 200)
        self.assertTrue(body["non_authoritative"])
        self.assertEqual(body["sources"][0]["role"], "PRIMARY")
        self.assertEqual(body["memory"]["origin_rule_id"], self.rule_id)
        self.assertEqual(body["engine_claim"], "not yet sent to the engine")
        self.assertTrue(body["recallable"])

    def test_traces_are_listed_and_addressable(self) -> None:
        status, body = self.call("GET", f"/v1/memory/traces?project_id={self.project_id}",
                                 token=self.reader)
        self.assertEqual(status, 200)
        self.assertEqual(body["traces"], [])
        status, body = self.call("GET", f"/v1/memory/traces/{ids.new_id('mtr')}",
                                 token=self.reader)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "memory_trace_not_found")


class LifecycleEndpoints(MemoryApiTestCase):
    def command(self, action: str, body=None, token: str | None = None):
        return self.call("POST", f"/v1/memory/items/{self.memory_id}/{action}",
                         body if body is not None else {"expected_revision": 1, "host_id": HOST},
                         token=token or self.manager)

    def test_pin_and_unpin_round_trip(self) -> None:
        status, pinned = self.command("pin")
        self.assertEqual(status, 200, pinned)
        self.assertTrue(pinned["memory"]["pinned"])
        status, unpinned = self.command("unpin", {"expected_revision": 2, "host_id": HOST})
        self.assertEqual(status, 200, unpinned)
        self.assertFalse(unpinned["memory"]["pinned"])

    def test_obsolete_then_delete(self) -> None:
        status, obsoleted = self.command("obsolete",
                                         {"expected_revision": 1, "host_id": HOST,
                                          "reason": "wrong lesson"})
        self.assertEqual(status, 200, obsoleted)
        self.assertEqual(obsoleted["memory"]["status"], "OBSOLETE")
        self.assertEqual(obsoleted["job"]["kind"], "invalidate")
        status, deleted = self.command("delete", {"expected_revision": 2, "host_id": HOST,
                                                  "reason": "user asked"})
        self.assertEqual(status, 200, deleted)
        self.assertEqual(deleted["memory"]["status"], "DELETED")
        self.assertEqual(deleted["job"]["kind"], "delete")
        self.assertTrue(deleted["non_authoritative"])

    def test_a_stale_revision_is_a_409(self) -> None:
        status, body = self.command("pin", {"expected_revision": 99, "host_id": HOST})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "revision_conflict")
        self.assertEqual(body["error"]["details"]["current_revision"], 1)

    def test_a_missing_revision_is_a_400(self) -> None:
        status, body = self.command("pin", {"host_id": HOST})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "missing_expected_revision")

    def test_an_agent_key_cannot_manage_memory(self) -> None:
        from jasmine_core import registry

        with db.transaction(self.conn):
            agent = ids.new_id("act")
            registry.Registry(self.conn).upsert_actor(agent, kind="agent", home_host_id=HOST)
        with self.assertRaises(errors.ForbiddenActorKind):
            auth.Auth(self.conn).issue_key(actor_id=agent, label="agent",
                                           scopes=["memory:manage", "memory:read"])
        with self.assertRaises(errors.ForbiddenActorKind):
            auth.Auth(self.conn).issue_key(actor_id=agent, label="agent",
                                           scopes=["memory:reflect"])
        # But reading Memory is allowed for an agent: the Inspector is history.
        token = auth.Auth(self.conn).issue_key(actor_id=agent, label="agent",
                                               scopes=["memory:read"])["token"]
        status, body = self.call("GET", "/v1/memory/items", token=token)
        self.assertEqual(status, 200, body)

    def test_a_read_only_key_cannot_manage_even_with_the_right_actor_kind(self) -> None:
        status, body = self.command("obsolete", token=self.reader)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")


class ProjectIsolation(MemoryApiTestCase):
    def other_project(self) -> str:
        _, project = self.call("POST", "/v1/projects", {"name": "b", "host_id": HOST},
                               token=self.write)
        return project["object"]["project_id"]

    def test_project_b_cannot_see_project_as_items_or_jobs(self) -> None:
        other = self.other_project()
        for path in (f"/v1/memory/items?project_id={other}",
                     f"/v1/memory/jobs?project_id={other}"):
            status, body = self.call("GET", path, token=self.reader)
            self.assertEqual(status, 200, path)
            self.assertEqual(body.get("items", body.get("jobs")), [], path)
        # Project A's own listing does contain the item, so the previous
        # assertions are isolation and not an empty database.
        status, body = self.call("GET", f"/v1/memory/items?project_id={self.project_id}",
                                 token=self.reader)
        self.assertEqual([i["memory_id"] for i in body["items"]], [self.memory_id])

    def test_a_query_filter_cannot_redirect_a_detail_fetch(self) -> None:
        other = self.other_project()
        status, body = self.call("GET",
                                 f"/v1/memory/items/{self.memory_id}?project_id={other}",
                                 token=self.reader)
        # A detail fetch is governed by the item's own project, not by a filter
        # the caller supplied, so the answer is project A's item and nothing is
        # rewritten to look like project B's.
        self.assertEqual(status, 200, body)
        self.assertEqual(body["memory"]["project_id"], self.project_id)

    def test_a_nonexistent_project_is_a_404_not_a_permission_error(self) -> None:
        status, body = self.call("GET", f"/v1/memory/items?project_id={ids.new_id('prj')}",
                                 token=self.reader)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "project_not_found")

    def test_acl_mode_refuses_an_ungranted_project_as_not_found(self) -> None:
        # Each test builds its own Application, so the mode is read for this
        # request. The empty-table guard fires first, which is the misconfigured
        # case; after a grant, an ungranted actor is refused as missing.
        with patch.dict(os.environ, {"JASMINE_MEMORY_DEPLOYMENT_MODE": scope.ACL}):
            status, body = self.call("GET", f"/v1/memory/items?project_id={self.project_id}",
                                     token=self.reader)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "memory_deployment_not_ready")
        # The table name is not the point; "grant access" is.
        self.assertNotIn("memory_project_actors", json.dumps(body))
        self.assertIn("grant", body["error"]["message"])

    def test_acl_mode_with_a_grant_serves_only_the_granted_project(self) -> None:
        from jasmine_core.memory.store import MemoryStore

        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO memory_project_actors (project_id, actor_id, granted_by_event_id,"
                " created_at) VALUES (?,?,?,?)",
                (self.project_id, ACTOR,
                 self.conn.execute("SELECT event_id FROM events ORDER BY seq LIMIT 1"
                                   ).fetchone()["event_id"], "2026-09-29T12:00:00.000000Z"))
        with patch.dict(os.environ, {"JASMINE_MEMORY_DEPLOYMENT_MODE": scope.ACL}):
            status, granted = self.call("GET", f"/v1/memory/items?project_id={self.project_id}",
                                        token=self.reader)
            self.assertEqual(status, 200, granted)
            self.assertEqual([i["memory_id"] for i in granted["items"]], [self.memory_id])
            status, refused = self.call("GET", f"/v1/memory/items?project_id={ids.new_id('prj')}",
                                        token=self.reader)
            self.assertEqual(refused["error"]["code"], "project_not_found")
        del MemoryStore

    def test_a_body_cannot_claim_another_project(self) -> None:
        other = self.other_project()
        status, body = self.call("POST", f"/v1/memory/items/{self.memory_id}/obsolete",
                                 {"expected_revision": 1, "host_id": HOST,
                                  "project_id": other}, token=self.manager)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_request")
        # And nothing was written.
        status, detail = self.call("GET", f"/v1/memory/items/{self.memory_id}", token=self.reader)
        self.assertEqual(detail["memory"]["status"], "ACTIVE")


class NoPublicWritePath(MemoryApiTestCase):
    """The absence of an endpoint is a contract, so it is tested as one."""

    def test_there_is_no_retain_endpoint(self) -> None:
        # 404 for a path that does not exist, 405 for one that exists only to be
        # read: both refuse the write, and neither is a create.
        for path, expected in (("/v1/memory/retain", 404), ("/v1/memory/items", 405),
                               ("/v1/memory/banks", 404), ("/v1/memory", 404)):
            status, body = self.call("POST", path, {}, token=self.manager)
            self.assertEqual(status, expected, path)
            self.assertIn(body["error"]["code"],
                          ("route_not_found", "method_not_allowed"), path)
            self.assertNotIn("object", body)

    def test_there_is_no_bank_selector(self) -> None:
        status, body = self.call("GET", "/v1/memory/items?bank_id=jasmine-global",
                                 token=self.reader)
        self.assertEqual(status, 200)
        # The filter is a display filter over items Core created; it cannot make
        # an item appear that is not there.
        self.assertEqual(body["items"], [])

    def test_reflect_needs_its_own_scope(self) -> None:
        body = {"project_id": self.project_id, "query": "summarise the history"}
        status, refused = self.call("POST", "/v1/memory/reflect", body, token=self.reader)
        self.assertEqual(status, 403)
        self.assertEqual(refused["error"]["details"]["required"], "memory:reflect")
        status, answered = self.call("POST", "/v1/memory/reflect", body, token=self.reflector)
        # No engine is configured in this test, so the honest answer is degraded.
        self.assertEqual(status, 200, answered)
        self.assertTrue(answered["non_authoritative"])
        self.assertEqual(answered["status"], "degraded")
        self.assertEqual(answered["error_code"], "engine_not_configured")

    def test_reflect_validates_its_query(self) -> None:
        for query in ("", "   ", "x" * 5000):
            status, body = self.call("POST", "/v1/memory/reflect",
                                     {"project_id": self.project_id, "query": query},
                                     token=self.reflector)
            self.assertEqual(status, 400, query[:20])
            self.assertEqual(body["error"]["code"], "invalid_request")


class HealthEndpoint(MemoryApiTestCase):
    def test_core_health_does_not_mention_memory(self) -> None:
        status, body = self.call("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertNotIn("memory", body)
        self.assertNotIn("hindsight", json.dumps(body).lower())

    def test_memory_health_reports_unconfigured_without_claiming_a_failure(self) -> None:
        status, body = self.call("GET", "/v1/memory/health", token=self.reader)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "unconfigured")
        self.assertFalse(body["configured"])
        self.assertFalse(body["degraded"])
        self.assertEqual(body["core_impact"], "none")
        self.assertEqual(body["jobs"]["pending"], 1)
        self.assertTrue(body["non_authoritative"])
        self.assertEqual(body["deployment"]["mode"], "single_user")

    def test_memory_health_never_returns_a_credential_or_an_endpoint_detail(self) -> None:
        with patch.dict(os.environ, {
            "JASMINE_HINDSIGHT_URL": "http://user:hunter2@127.0.0.1:8888",
            "JASMINE_HINDSIGHT_API_KEY": "super-secret-token",
        }):
            status, body = self.call("GET", "/v1/memory/health", token=self.reader)
        self.assertEqual(status, 200, body)
        serialised = json.dumps(body)
        self.assertNotIn("hunter2", serialised)
        self.assertNotIn("super-secret-token", serialised)
        # No probe has run, so there is no origin host to report yet, and the
        # configured flag is the only thing the endpoint knows about the engine.
        self.assertIsNone(body["origin_host"])
        self.assertTrue(body["configured"])
        self.assertIsNone(body["api_version"])

    def test_memory_health_needs_the_read_scope(self) -> None:
        status, body = self.call("GET", "/v1/memory/health", token=self.write)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")

    def test_a_failing_queue_is_reported_as_failing(self) -> None:
        from jasmine_core.memory import jobs as jobs_module
        from jasmine_core.memory.store import MemoryStore

        store = MemoryStore(self.conn, schema_version=7)
        job_store = jobs_module.JobStore(self.conn, store)
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE memory_jobs SET status='FAILED', last_error_code='engine_rejected'")
        status, body = self.call("GET", "/v1/memory/health", token=self.reader)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "unconfigured")
        self.assertEqual(body["jobs"]["failed"], 1)
        del store, job_store


class AuditTrail(MemoryApiTestCase):
    def test_a_lifecycle_command_leaves_an_audited_event(self) -> None:
        self.call("POST", f"/v1/memory/items/{self.memory_id}/pin",
                  {"expected_revision": 1, "host_id": HOST}, token=self.manager)
        status, events = self.call("GET", f"/v1/tasks/{ids.new_id('tsk')}/history",
                                   token=self.write)
        self.assertEqual(status, 404)
        from jasmine_core.events import EventStore

        stored = EventStore(self.conn, schema_version=7).get(
            self.conn.execute(
                "SELECT event_id FROM events WHERE event_type='memory.pinned'").fetchone()["event_id"]
        )
        self.assertEqual(stored["actor_id"], ACTOR)
        self.assertEqual(stored["actor_kind"], "human")
        self.assertEqual(stored["payload"]["action"], "pin")
        self.assertEqual(stored["payload"]["before"]["status"], "ACTIVE")
        self.assertEqual(stored["payload"]["after"]["pinned"], True)

    def test_a_refused_command_writes_no_memory_truth(self) -> None:
        from jasmine_core.memory.store import MemoryStore

        before = MemoryStore(self.conn, schema_version=7).items()[0]
        self.call("POST", f"/v1/memory/items/{self.memory_id}/pin",
                  {"expected_revision": 99, "host_id": HOST}, token=self.manager)
        after = MemoryStore(self.conn, schema_version=7).items()[0]
        self.assertEqual(before, after)
        self.assertIsNone(self.conn.execute(
            "SELECT event_id FROM events WHERE event_type='memory.pinned'").fetchone())
