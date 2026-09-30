"""Shared P4 test fixtures: a Core with real truth, plus two kinds of engine.

Two engine doubles, and the distinction matters for how their results may be
used:

* `FakeTransport` is a *boundary* double. It satisfies `MemoryTransport` and is
  used to exercise Core's own decisions — provenance verification, the injection
  guard, de-duplication, budgeting, the job state machine and the durable crash
  windows — with total control over what the "engine" returns. A test using it
  proves Core behaviour and proves nothing about Hindsight. Every test that uses
  it says so in its docstring.
* `StubHindsightServer` is a *wire* double. It is a real `http.server` on
  loopback speaking Hindsight 0.10.2's real paths, request bodies and response
  shapes, and it is driven through the real `HindsightTransport` over a real
  socket. It catches the class of bug a `FakeTransport` cannot: a wrong URL, a
  wrong field name, a method that is not the one the spec documents, a body the
  engine would reject. It still proves nothing about a real Hindsight process,
  which is what the P4 Gates require.

Neither is acceptable evidence for a Gate verdict, and the manifest schema in the
P4 taskbook has a `mode` field (`real` / `boundary-fake`) precisely so a report
cannot blur them.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jasmine_core import auth, clock, db, ids, registry  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402
from jasmine_core.memory import filtering, scope  # noqa: E402
from jasmine_core.memory.store import MemoryStore  # noqa: E402
from jasmine_core.memory.transport import TransportError  # noqa: E402
from jasmine_core.memory.types import BankRef, EngineResult  # noqa: E402

HOST = "hst_01K742SG00BMSDET9BTP151RAR"
HUMAN = "act_01K742SG00CBKP25A9ETPBTRMJ"
SYSTEM = "act_01K742SG00CBKP25A9ETPBTRMJ"

BANK_PATH = re.compile(r"^/v1/default/banks/(?P<bank>[^/]+)(?P<suffix>/.*)?$")


# -- boundary double ----------------------------------------------------------


class FakeTransport:
    """A `MemoryTransport` double. Boundary evidence only, never Gate evidence."""

    engine_id = "hindsight-fake"

    def __init__(self, store=None) -> None:
        #: The real Memory store, so `answer_for` can build honest engine
        #: metadata for a Memory item that actually exists. Without it a helper
        #: would have to invent source ids, and every provenance test would fail
        #: for the wrong reason.
        self.store = store
        self.documents: dict[tuple[str, str], dict[str, Any]] = {}
        self.retain_calls: list[dict[str, Any]] = []
        self.recall_calls: list[dict[str, Any]] = []
        self.reflect_calls: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []
        self.invalidated: list[tuple[str, str, str]] = []
        self.retain_error: TransportError | None = None
        self.recall_error: dict[str, TransportError] = {}
        self.answer: dict[str, list[EngineResult]] = {}
        self.next_memory_id = 0
        self.raise_during_retain: BaseException | None = None

    # -- MemoryTransport ---------------------------------------------------

    def version(self) -> dict[str, Any]:
        return {"reachable": True, "engine_id": self.engine_id, "origin_host": "fake",
                "api_version": "0.10.2", "features": {"observations": True, "mcp": True,
                                                      "store_document_text": True}}

    def retain(self, *, bank: BankRef, document_id: str, content: str, context: str,
               metadata: dict[str, str], tags: tuple[str, ...], memory_type: str,
               timestamp: str | None) -> dict[str, Any]:
        self.retain_calls.append({"bank": bank, "document_id": document_id, "content": content,
                                  "context": context, "metadata": dict(metadata),
                                  "tags": tuple(tags), "memory_type": memory_type,
                                  "timestamp": timestamp})
        if self.raise_during_retain is not None:
            raise self.raise_during_retain
        if self.retain_error is not None:
            raise self.retain_error
        self.next_memory_id += 1
        self.documents[(bank.bank_id, document_id)] = {
            "id": document_id, "bank_id": bank.bank_id, "content": content,
            "metadata": dict(metadata), "memory_unit_count": 1,
            "memory_unit_ids": [f"mu_{self.next_memory_id}"],
        }
        return {"document_id": document_id, "bank_id": bank.bank_id, "items_count": 1,
                "engine_api_version": "0.10.2", "operation_id": None, "async": False,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}

    def recall(self, *, bank: BankRef, query: str, max_tokens: int,
               fact_types: tuple[str, ...], tags: tuple[str, ...]) -> list[EngineResult]:
        self.recall_calls.append({"bank": bank.bank_id, "query": query,
                                  "max_tokens": max_tokens, "fact_types": fact_types,
                                  "tags": tags})
        error = self.recall_error.get(bank.bank_id)
        if error is not None:
            raise error
        return list(self.answer.get(bank.bank_id, []))

    def reflect(self, *, bank: BankRef, query: str, max_tokens: int) -> dict[str, Any]:
        self.reflect_calls.append({"bank": bank.bank_id, "query": query})
        return {"text": "fake synthesis", "based_on_ids": []}

    def list_document_memories(self, *, bank: BankRef, document_id: str, limit: int = 200,
                               offset: int = 0) -> list[dict[str, Any]]:
        stored = self.documents.get((bank.bank_id, document_id))
        if stored is None:
            return []
        return [{"id": unit, "text": stored["content"], "state": "valid"}
                for unit in stored["memory_unit_ids"]]

    def invalidate_memory(self, *, bank: BankRef, memory_id: str, reason: str) -> None:
        self.invalidated.append((bank.bank_id, memory_id, reason))

    def delete_document(self, *, bank: BankRef, document_id: str) -> dict[str, Any]:
        self.deleted.append((bank.bank_id, document_id))
        stored = self.documents.pop((bank.bank_id, document_id), None)
        return {"document_id": document_id,
                "memory_units_deleted": 1 if stored else 0, "success": True}

    def get_document(self, *, bank: BankRef, document_id: str) -> dict[str, Any] | None:
        return self.documents.get((bank.bank_id, document_id))

    # -- helpers -----------------------------------------------------------

    def answer_for(self, bank_id: str, memory_id: str, text: str, *,
                   document_id: str | None = None, engine_id: str | None = None,
                   metadata: dict[str, str] | None = None,
                   tags: tuple[str, ...] = ()) -> EngineResult:
        from jasmine_core.memory import provenance

        item = self._items().get(memory_id)
        base = dict(provenance.build_metadata(
            memory_id=memory_id,
            document_id=document_id or (item["document_id"] if item else "unknown"),
            memory_type=item["memory_type"] if item else "FAILURE_LESSON",
            provenance=provenance.Provenance(
                source_event_ids=(item["source_event_id"],) if item else ("evt_" + "0" * 26,),
                evidence_ids=tuple(item["evidence_ids"]) if item else (),
                origin_rule_id=item["origin_rule_id"] if item else None,
                origin_rule_version=item["origin_rule_version"] if item else None),
            content_sha256=item["content_sha256"] if item else "0" * 64,
            retained_at=item["created_at"] if item else clock.now_rfc3339(),
        ))
        base.update(metadata or {})
        result = EngineResult(engine_result_id=engine_id or f"er_{len(self.answer)}",
                              text=text, bank_id=bank_id,
                              document_id=document_id or (item["document_id"] if item else None),
                              engine_fact_type="experience", metadata=base, tags=tags)
        self.answer.setdefault(bank_id, []).append(result)
        return result

    def _items(self) -> dict[str, dict[str, Any]]:
        if self.store is None:
            return {}
        return {item["memory_id"]: item for item in self.store.items(limit=500)}


# -- wire double --------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    """Speaks the subset of Hindsight 0.10.2 that P4 uses.

    The behaviours modelled here are the ones P4's correctness depends on, all of
    them documented engine behaviour rather than inventions:

    * retain with a `document_id` that already exists replaces the document and
      *renumbers* its memory units, because `update_mode="replace"` deletes the
      old document and its memories before re-extracting;
    * recall returns `metadata` on every result, which is how Core gets its
      provenance back;
    * `PATCH /memories/{id}` with `state="invalidated"` is a soft retire that
      keeps the unit;
    * `DELETE /documents/{id}` removes the document and its units.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - silence the default stderr log
        return

    # -- helpers -----------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> tuple[str, str] | None:
        # `self.path` carries the query string; the route table is path-only.
        path = self.path.partition("?")[0]
        match = BANK_PATH.match(path)
        if self.path == "/version":
            return "__version__", ""
        if match is None:
            return None
        return match.group("bank"), (match.group("suffix") or "")

    def _unit(self, bank: str, unit_id: str) -> dict[str, Any] | None:
        for document in self.server.documents.values():
            for unit in document["units"].values():
                if unit["id"] == unit_id and document["bank_id"] == bank:
                    return unit
        return None

    # -- verbs -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        route = self._route()
        if route is None:
            return self._send(404, {"detail": "not found"})
        bank, suffix = route
        if bank == "__version__":
            return self._send(200, {"api_version": "0.10.2", "features": {
                "observations": True, "mcp": True, "worker": True,
                "bank_config_api": True, "store_document_text": True}})
        if suffix == "/memories/list":
            query = self.path.partition("?")[2]
            wanted = ""
            for part in query.split("&"):
                if part.startswith("document_id="):
                    wanted = _unquote(part.split("=", 1)[1])
            document = self.server.documents.get((bank, wanted))
            items = list(document["units"].values()) if document else []
            return self._send(200, {"items": items, "total": len(items), "limit": 200, "offset": 0})
        if suffix.startswith("/documents/"):
            document = self.server.documents.get((bank, _unquote(suffix.split("/", 2)[2])))
            if document is None:
                return self._send(404, {"detail": "no such document"})
            return self._send(200, {k: v for k, v in document.items() if k != "units"}
                              | {"memory_unit_count": len(document["units"])})
        if suffix == "/documents":
            return self._send(200, {"items": [], "total": 0, "limit": 100, "offset": 0})
        return self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        route = self._route()
        if route is None:
            return self._send(404, {"detail": "not found"})
        bank, suffix = route
        body = self._read()
        if suffix == "/memories":
            return self._retain(bank, body)
        if suffix == "/memories/recall":
            return self._recall(bank, body)
        if suffix == "/reflect":
            self.server.reflect_calls.append((bank, body.get("query")))
            return self._send(200, {"text": "stub synthesis", "based_on": None})
        return self._send(404, {"detail": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        route = self._route()
        if route is None:
            return self._send(404, {"detail": "not found"})
        bank, suffix = route
        body = self._read()
        if suffix.startswith("/memories/"):
            unit = self._unit(bank, _unquote(suffix.split("/", 2)[2]))
            if unit is None:
                return self._send(404, {"detail": "no such memory"})
            if body.get("state") == "invalidated":
                unit["state"] = "invalidated"
                unit["invalidation_reason"] = body.get("reason", "")
            return self._send(200, unit)
        return self._send(404, {"detail": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        route = self._route()
        if route is None:
            return self._send(404, {"detail": "not found"})
        bank, suffix = route
        if suffix.startswith("/documents/"):
            document_id = _unquote(suffix.split("/", 2)[2])
            document = self.server.documents.pop((bank, document_id), None)
            if document is None:
                return self._send(404, {"detail": "no such document"})
            return self._send(200, {"success": True, "message": "deleted",
                                    "document_id": document_id,
                                    "memory_units_deleted": len(document["units"])})
        return self._send(404, {"detail": "not found"})

    # -- behaviour ---------------------------------------------------------

    def _retain(self, bank: str, body: dict[str, Any]) -> None:
        items = body.get("items") or []
        if not items or not isinstance(items[0].get("content"), str):
            return self._send(422, {"detail": "items[0].content is required"})
        if body.get("async"):
            return self._send(422, {"detail": "this stub only models synchronous retain"})
        item = items[0]
        document_id = item.get("document_id") or f"anon-{len(self.server.documents)}"
        mode = item.get("update_mode", "replace")
        existing = self.server.documents.get((bank, document_id))
        units: dict[str, dict[str, Any]] = {}
        if existing is not None and mode == "append":
            units = dict(existing["units"])
            self.server.unit_counter += 1
            units[f"mu_{self.server.unit_counter}"] = self._unit_for(item, units, bank, document_id)
        else:
            # `replace`: the old document and every memory unit it produced are
            # deleted first, so the new units get new ids. P4 must therefore not
            # treat a memory unit id as stable across a retry.
            self.server.unit_counter += 1
            units[f"mu_{self.server.unit_counter}"] = self._unit_for(item, {}, bank, document_id)
        self.server.documents[(bank, document_id)] = {
            "id": document_id, "bank_id": bank, "original_text": item["content"],
            "content_hash": filtering.canonical.sha256_hex(item["content"]),
            "units": units, "tags": item.get("tags") or [],
            "created_at": clock.now_rfc3339(), "updated_at": clock.now_rfc3339(),
        }
        return self._send(200, {"success": True, "bank_id": bank, "items_count": 1,
                                "async": False, "operation_id": None,
                                "usage": {"input_tokens": 100, "output_tokens": 40,
                                          "total_tokens": 140}})

    def _unit_for(self, item: dict[str, Any], _existing: dict[str, str], bank: str,
                  document_id: str) -> dict[str, Any]:
        return {
            "id": f"mu_{self.server.unit_counter}",
            "text": item["content"][:200],
            "type": "experience",
            "document_id": document_id,
            "bank_id": bank,
            "metadata": item.get("metadata") or {},
            "tags": item.get("tags") or [],
            "state": "valid",
        }

    def _recall(self, bank: str, body: dict[str, Any]) -> None:
        query = body.get("query")
        if not isinstance(query, str) or not query:
            return self._send(422, {"detail": "query is required"})
        if len(query.split()) > 500:
            return self._send(422, {"detail": "query exceeds 500 tokens"})
        if self.server.unreachable:
            return self._send(503, {"detail": "the stub is switched off"})
        wanted_types = set(body.get("types") or ["world", "experience", "observation"])
        budget = int(body.get("max_tokens") or 4096)
        results: list[dict[str, Any]] = []
        used = 0
        for (unit_bank, _document_id), document in self.server.documents.items():
            if unit_bank != bank:
                continue
            for unit in document["units"].values():
                if unit["state"] == "invalidated":
                    continue
                if unit["type"] not in wanted_types:
                    continue
                if not _matches(query, unit["text"]):
                    continue
                cost = max(1, len(unit["text"]) // 4)
                if used + cost > budget:
                    continue
                used += cost
                results.append({**unit, "scores": {"final": 1.0}})
        return self._send(200, {"results": results, "chunks": None, "source_facts": None})


def _matches(query: str, text: str) -> bool:
    terms = {t for t in re.split(r"\W+", query.lower()) if len(t) > 2}
    if not terms:
        return False
    haystack = text.lower()
    return any(term in haystack for term in terms)


def _unquote(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)


class StubHindsightServer:
    """A real loopback HTTP server speaking Hindsight 0.10.2's wire format."""

    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.httpd.daemon_threads = True
        self.httpd.documents: dict[tuple[str, str], dict[str, Any]] = {}
        self.httpd.unit_counter = 0
        self.httpd.reflect_calls: list[tuple[str, Any]] = []
        self.httpd.unreachable = False
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def set_unreachable(self, value: bool) -> None:
        self.httpd.unreachable = value


# -- Core fixture -------------------------------------------------------------


class MemoryTestCase(unittest.TestCase):
    """A migrated database with a host, a human actor and a system actor."""

    #: Set False to start from an empty file, so a test can build a specific
    #: older schema shape and then migrate it forward.
    migrate_db = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-memory-test-")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "core.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        if self.migrate_db:
            migrate(self.conn)
        self.system_actor = ids.new_id("act")
        self.agent_actor = ids.new_id("act")
        if self.migrate_db:
            with db.transaction(self.conn):
                reg = registry.Registry(self.conn)
                reg.upsert_host(HOST, display_name="memory test host")
                reg.upsert_actor(HUMAN, kind="human", display_name="tester", home_host_id=HOST)
                reg.upsert_actor(self.system_actor, kind="system", display_name="worker",
                                 home_host_id=HOST)
                reg.upsert_actor(self.agent_actor, kind="agent", display_name="agent",
                                 home_host_id=HOST)
        self.store = MemoryStore(self.conn, schema_version=8)
        self.transport = FakeTransport(self.store)
        self._env = patch.dict("os.environ", {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        import os

        # A Memory test must not accidentally talk to a real engine, and must not
        # inherit an operator's deployment mode.
        for name in ("JASMINE_HINDSIGHT_URL", "JASMINE_HINDSIGHT_API_KEY",
                     "JASMINE_MEMORY_DEPLOYMENT_MODE"):
            os.environ.pop(name, None)
        self._workspace: tempfile.TemporaryDirectory | None = None

    # -- truth fixtures ----------------------------------------------------

    def project(self, name: str = "p") -> str:
        return self._object("project", name=name)

    def task(self, project_id: str, title: str = "t") -> str:
        return self._object("task", project_id=project_id, title=title)

    def session(self, *, project_id: str | None = None, task_id: str | None = None) -> str:
        from jasmine_core.objects import ObjectStore
        from jasmine_core.models import NewObject

        # ObjectStore.create opens its own transaction (ADR 0002).
        result = ObjectStore(self.conn, schema_version=8).create(
            NewObject.session({"title": "s", "project_id": project_id, "task_id": task_id,
                               "host_id": HOST}),
            actor_id=HUMAN)
        return result["object"]["session_id"]

    def _object(self, kind: str, **fields) -> str:
        from jasmine_core.objects import ObjectStore
        from jasmine_core.models import NewObject

        parser = {"project": NewObject.project, "task": NewObject.task}[kind]
        result = ObjectStore(self.conn, schema_version=8).create(
            parser({"host_id": HOST, **fields}), actor_id=HUMAN)
        return result["object"][f"{kind}_id"]

    def event(self, event_type: str, *, project_id: str | None = None,
              task_id: str | None = None, text: str = "x", **payload) -> str:
        from jasmine_core.models import NewEvent
        from jasmine_core.events import EventStore

        with db.transaction(self.conn):
            stored, _ = EventStore(self.conn, schema_version=8).append(NewEvent(
                event_type=event_type, source_system="memory-test", source_event_id=None,
                occurred_at=clock.now(), actor_id=HUMAN, actor_kind="human", host_id=HOST,
                project_id=project_id, task_id=task_id,
                payload={"text": text, **payload}))
        return stored["event_id"]

    def active_decision(self, *, project_id: str, task_id: str | None = None,
                        content: str = "V1 uses Hindsight as the long-term memory engine",
                        rule_key: str = "memory.engine") -> tuple[str, str]:
        """A confirmed Decision: PROPOSED then approved by a human."""
        from jasmine_core.authority import AuthorityStore

        origin = self.event("user.prompt", project_id=project_id, task_id=task_id,
                            text="use hindsight")
        authority = AuthorityStore(self.conn, schema_version=8)
        scope = ({"kind": "project", "project_id": project_id} if task_id is None
                 else {"kind": "task", "project_id": project_id, "task_id": task_id})
        proposed = authority.propose({
            "rule_key": rule_key, "kind": "DECISION", "severity": "HARD",
            "enforcement": "CONTEXT", "content": content, "matcher": {},
            "scope": scope, "origin_event_id": origin, "host_id": HOST,
        }, actor_id=self.agent_actor)
        rule_id = proposed["rule"]["rule_id"]
        authority.transition(rule_id, "approve", {"expected_revision": 1, "host_id": HOST},
                             actor_id=HUMAN)
        return rule_id, origin

    def global_decision(self, *, content: str = "Prefer the smallest change that works",
                        rule_key: str = "minimal.change") -> str:
        """A confirmed global-scoped Decision, which is the only route to the global bank."""
        from jasmine_core.authority import AuthorityStore

        origin = self.event("user.prompt", text="prefer minimal change")
        authority = AuthorityStore(self.conn, schema_version=8)
        proposed = authority.propose({
            "rule_key": rule_key, "kind": "DECISION", "severity": "NORMAL",
            "enforcement": "CONTEXT", "content": content, "matcher": {},
            "scope": {"kind": "global"}, "origin_event_id": origin, "host_id": HOST,
        }, actor_id=HUMAN)
        rule_id = proposed["rule"]["rule_id"]
        authority.transition(rule_id, "approve", {"expected_revision": 1, "host_id": HOST},
                             actor_id=HUMAN)
        return rule_id

    def evidence(self, *, task_id: str, status: str = "PASS",
                 detail: str = "suite passed", artifact_uri: str | None = None) -> tuple[str, str]:
        """Real Evidence through the P1 store, so `reference_status` is meaningful.

        `COMMAND_RESULT` is used because P1-03 requires an operator-provided
        producer mapping for a specialised kind; this fixture is not testing the
        producer mapping, it is testing that Memory eligibility reads the same
        reference status `GET /v1/evidence/{id}` reports.
        """
        from jasmine_core.evidence import EvidenceStore

        self.workspace()
        record = EvidenceStore(self.conn, schema_version=8).record_tool_result(
            {"task_id": task_id, "host_id": HOST, "codex_session_id": "cs",
             "turn_id": f"t-{ids.new_id('evt')[-6:]}", "tool_use_id": ids.new_id("evt")[-8:],
             "tool_name": "pytest", "tool_input": {"cmd": "pytest -q"},
             "tool_response": {"exit_code": 0 if status == "PASS" else 1},
             **({"artifact_uri": artifact_uri} if artifact_uri else {})},
            actor_id=self.system_actor)
        return record["evidence"]["evidence_id"], record["result_event"]["event_id"]

    def workspace(self) -> str:
        """A throwaway workspace root, which P1 fingerprint capture requires."""
        import os

        root = self._workspace
        if root is None:
            self._workspace = tempfile.TemporaryDirectory(prefix="jasmine-memory-ws-")
            self.addCleanup(self._workspace.cleanup)
            path = Path(self._workspace.name) / "source.txt"
            path.write_text("initial\n")
            (Path(self._workspace.name) / "report.txt").write_text("all green\n")
            os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(self._workspace.name)
            os.environ["JASMINE_CORE_FINGERPRINT_EXTRA_PATHS"] = '["source.txt"]'
        return str(root)

    def failed_step(self, *, project_id: str, task_id: str, reason: str = "device test failed") -> str:
        from jasmine_core.state import StateStore

        store = StateStore(self.conn, schema_version=8)
        created = store.create_step(task_id, {"title": "s", "host_id": HOST,
                                              "expected_revision": 1},
                                    actor_id=HUMAN, actor_kind="human", can_accept=False)
        step_id = created["step"]["step_id"]
        # P1's state machine has no PLANNED -> FAILED edge, and a real failure
        # looks like this: the work started, then it broke.
        store.transition_step(step_id, {"status": "IN_PROGRESS", "expected_revision": 1,
                                        "host_id": HOST},
                              actor_id=HUMAN, actor_kind="human", can_accept=False)
        store.transition_step(step_id, {"status": "FAILED", "reason": reason,
                                        "expected_revision": 2, "host_id": HOST},
                              actor_id=HUMAN, actor_kind="human", can_accept=False)
        return step_id

    # -- P4 wiring ---------------------------------------------------------

    def adapter(self, *, mode: str = scope.SINGLE_USER, transport=None):
        from jasmine_core.memory.adapter import MemoryAdapter

        return MemoryAdapter(self.conn, self.store,
                             self.transport if transport is None else transport,
                             mode=mode, schema_version=8)

    def job_store(self):
        from jasmine_core.memory import jobs as jobs_module

        return jobs_module.JobStore(self.conn, self.store)

    def lifecycle(self):
        from jasmine_core.memory.lifecycle import MemoryLifecycle

        return MemoryLifecycle(self.conn, self.store)

    def worker(self, *, transport=None, **kwargs):
        from jasmine_core.memory.worker import MemoryWorker

        return MemoryWorker(self.conn, self.store,
                            self.transport if transport is None else transport,
                            lease_owner=kwargs.pop("lease_owner", "worker-1"), **kwargs)

    def scan(self) -> dict[str, Any]:
        from jasmine_core.memory.eligibility import SourceRegistry

        return self.job_store().enqueue_from_sources(SourceRegistry())

    def token(self, scopes: list[str], *, actor_id: str = HUMAN) -> str:
        return auth.Auth(self.conn).issue_key(actor_id=actor_id, label="p4",
                                              scopes=scopes)["token"]

    def items(self, **kwargs):
        return self.store.items(**kwargs)

    def grant_project(self, project_id: str, *, actor_id: str = HUMAN) -> None:
        """Populate `memory_project_actors`, the only authority `acl` mode accepts."""
        from jasmine_core import db

        grant_event = self.event("project.notes", project_id=project_id, text="grant access")
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO memory_project_actors (project_id, actor_id,"
                " granted_by_event_id, created_at) VALUES (?,?,?,?)",
                (project_id, actor_id, grant_event, "2026-09-29T12:00:00.000000Z"))
