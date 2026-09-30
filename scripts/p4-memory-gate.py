#!/usr/bin/env python3
"""P4 real Gate runner: P4-G01..P4-G07 against a real Hindsight deployment.

Every case is checked for its preconditions *first*. A missing prerequisite is
reported as `BLOCKED` with the exact item that is missing, never as a `PASS` and
never as a `FAIL` — a Gate that cannot run has not failed, and reporting it as
either would be a lie in the direction that matters.

Three rules this runner will not break, because each of them is a way a Gate
report becomes worthless:

* **A case is `PASS` only with `mode: "real"`.** There is no code path here that
  produces `mode: "boundary-fake"`, because a fake is not a Gate. Component
  coverage lives in `tests/test_memory_*.py` and is labelled as such.
* **HTTP 200, an enqueue acknowledgement, a `RUNNING` job, a recall that returned
  rows, and an old memory are all not success.** A case asserts on engine
  documents, provenance, and the source events behind them.
* **Nothing fabricated.** If a precondition is missing, the reason says which
  one, and the run stops claiming coverage.

G01, G02, G03 and the pack half of G04 additionally need a real Agent session and
a P3 Context Pack, neither of which exists in this repository. Those cases stay
`BLOCKED` until P2 and P3 land, and the reason names them.

Usage:

    scripts/p4-memory-gate.py --out <new-out-dir> --work <new-scratch-dir> \
        --config <operator-environment.json>

The operator configuration names the real deployment: the engine, the bank
namespace this run may write to, the test projects, and the P2/P3 commits it
depends on. Secrets are referenced by environment variable name, never by value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jasmine_core import SCHEMA_VERSION, clock, db, ids, registry  # noqa: E402
from jasmine_core.models import NewObject  # noqa: E402
from jasmine_core.objects import ObjectStore  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402
from jasmine_core.memory import eligibility, jobs as jobs_module  # noqa: E402
from jasmine_core.memory import scope  # noqa: E402
from jasmine_core.memory.hindsight import (  # noqa: E402
    HindsightConfig,
    HindsightTransport,
    check_supported,
    config_from_env,
)
from jasmine_core.memory.store import MemoryStore  # noqa: E402
from jasmine_core.memory.worker import MemoryWorker  # noqa: E402

CASES = ("P4-G01", "P4-G02", "P4-G03", "P4-G04", "P4-G05", "P4-G06", "P4-G07")

#: Which cases can be decided without a real Agent session and a P3 Context Pack.
#: The rest are structurally BLOCKED until P2 and P3 are merged, and saying so
#: precisely is more useful than a partial run that looks like coverage.
NEEDS_P3_PACK = frozenset({"P4-G01", "P4-G02", "P4-G03", "P4-G04"})
NEEDS_REAL_AGENT = frozenset({"P4-G01"})


class Blocked(Exception):
    """A precondition is missing. Carries the exact items, not a summary."""

    def __init__(self, *items: str) -> None:
        super().__init__("; ".join(items))
        self.items = list(items)


class Failed(Exception):
    """A precondition held and the assertion did not. A real FAIL."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


@dataclass
class CaseRecord:
    case_id: str
    verdict: str                      # PASS | FAIL | BLOCKED
    mode: str = "real"                # real | boundary-fake | not-run
    reason: str = ""
    blocked_on: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    memory_result_ids: list[str] = field(default_factory=list)
    context_pack_ids: list[str] = field(default_factory=list)
    rule_versions: list[str] = field(default_factory=list)
    state_revision_before: int | None = None
    state_revision_after: int | None = None
    engine_evidence: dict[str, Any] = field(default_factory=dict)
    failure_class: str | None = None
    failure_output: str | None = None
    receipts: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()
                if v not in (None, [], {}) or k in ("verdict", "mode")}


class Gate:
    """One real run: a private Core, a private bank namespace, a real engine."""

    def __init__(self, work: Path, config: dict[str, Any], case_id: str) -> None:
        self.case_id = case_id
        self.root = work / case_id
        self.root.mkdir(mode=0o700)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "source.txt").write_text("v1\n", encoding="utf-8")
        self.config = config
        self.run_id = config.get("run_id") or f"p4-{int(time.time())}"
        # The bank namespace is namespaced per run so two Gate runs against one
        # deployment cannot see each other's documents, and a rerun cannot pass
        # on the previous run's content.
        self.bank_suffix = config.get("bank_suffix") or self.run_id.replace("/", "-")
        self.db_path = self.root / "core.db"
        self.host = ids.new_id("hst")
        self.human = ids.new_id("act")
        self.system = ids.new_id("act")
        self.transport: HindsightTransport | None = None
        self._previous: dict[str, str | None] = {}

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        self._set_env("JASMINE_CORE_WORKSPACE_ROOT", str(self.workspace))
        self._set_env("JASMINE_CORE_FINGERPRINT_EXTRA_PATHS", '["source.txt"]')
        env_config = config_from_env()
        if env_config is None:
            raise Blocked("JASMINE_HINDSIGHT_URL is not set for this process")
        self.transport = HindsightTransport(env_config)
        conn = db.connect(self.db_path)
        migrate(conn)
        with db.transaction(conn):
            reg = registry.Registry(conn)
            reg.upsert_host(self.host, display_name=f"p4 gate {self.case_id}")
            reg.upsert_actor(self.human, kind="human", display_name="p4 gate operator",
                             home_host_id=self.host)
            reg.upsert_actor(self.system, kind="system", display_name="p4 gate worker",
                             home_host_id=self.host)
        conn.close()

    def close(self) -> None:
        for name, value in self._previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _set_env(self, name: str, value: str) -> None:
        self._previous.setdefault(name, os.environ.get(name))
        os.environ[name] = value

    # -- engine preconditions ---------------------------------------------

    def require_engine(self) -> dict[str, Any]:
        """The engine must answer, be the version this adapter was verified against,
        and be storing document text, or a read-back Gate cannot mean anything."""
        assert self.transport is not None
        probed = self.transport.version()
        if not probed.get("reachable"):
            raise Blocked(
                f"the engine at {probed.get('origin_host')} did not answer: "
                f"{probed.get('error_code')} {probed.get('safe_error_summary')}"
            )
        ok, reason = check_supported(probed)
        if not ok:
            raise Blocked(reason or "the engine version is not the verified one")
        if not probed.get("features", {}).get("store_document_text", True):
            raise Blocked("the deployment does not store document text, so read-back is unavailable")
        return probed

    def require_bank_namespace(self) -> str:
        suffix = self.config.get("bank_suffix")
        if not suffix:
            raise Blocked(
                "no bank namespace is declared in the operator configuration; a Gate that "
                "shares the production bank cannot prove project isolation"
            )
        return str(suffix)

    def require_dependency(self, phase: str) -> str:
        """A phase counts as available only with a real commit in the manifest.

        `blocked`, an empty string and `None` are all the same statement — the
        phase is not merged — and none of them is a commit.
        """
        commit = (self.config.get("dependency_commits") or {}).get(phase)
        if not commit or str(commit).strip().lower() in ("blocked", "none", "n/a", "-"):
            raise Blocked(f"{phase} is not merged; the manifest records {phase} as blocked")
        return str(commit)

    def require_real_agent(self) -> None:
        if not self.config.get("real_agent_session"):
            raise Blocked(
                "no real Agent session is declared; a recall that helps must be observed in a "
                "new real session with real tool actions, not simulated"
            )

    def require_p3_pack(self) -> None:
        self.require_dependency("P3")

    # -- fixtures ----------------------------------------------------------

    def conn(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def seed_project(self, name: str) -> str:
        from jasmine_core.models import NewObject
        from jasmine_core.objects import ObjectStore

        conn = self.conn()
        try:
            # ObjectStore.create opens its own transaction (ADR 0002).
            return ObjectStore(conn, schema_version=7).create(
                NewObject.project({"name": f"{self.run_id}-{name}", "host_id": self.host}),
                actor_id=self.human)["object"]["project_id"]
        finally:
            conn.close()

    def confirm_decision(self, project_id: str, *, content: str, rule_key: str) -> str:
        """A real human-confirmed Decision: the only source of a Decision memory."""
        from jasmine_core.authority import AuthorityStore
        from jasmine_core.events import EventStore
        from jasmine_core.models import NewEvent

        conn = self.conn()
        try:
            origin, _ = EventStore(conn, schema_version=7).append(NewEvent(
                event_type="user.prompt", source_system=f"p4-gate-{self.case_id}",
                source_event_id=None, occurred_at=clock.now(), actor_id=self.human,
                actor_kind="human", host_id=self.host, project_id=project_id,
                payload={"text": content}))
            authority = AuthorityStore(conn, schema_version=7)
            proposed = authority.propose({
                "rule_key": rule_key, "kind": "DECISION", "severity": "HARD",
                "enforcement": "CONTEXT", "content": content, "matcher": {},
                "scope": {"kind": "project", "project_id": project_id},
                "origin_event_id": origin["event_id"], "host_id": self.host},
                actor_id=self.human)
            rule_id = proposed["rule"]["rule_id"]
            authority.transition(rule_id, "approve",
                                 {"expected_revision": 1, "host_id": self.host},
                                 actor_id=self.human)
            return rule_id
        finally:
            conn.close()

    def drain(self, conn: sqlite3.Connection, *, limit: int = 8) -> list[Any]:
        """Run the worker until nothing is due.

        One committed failure legitimately produces more than one eligible
        source (a `FAILED` step transition *and* a `FAIL` Evidence), and the
        worker handles one job per tick. Draining is what makes "the engine has
        this document" a fact about the item the caller is about to check,
        instead of a fact about whichever job happened to be first.
        """
        reports = []
        for _ in range(limit):
            tick = MemoryWorker(conn, MemoryStore(conn, schema_version=7), self.transport,
                                lease_owner=f"p4-gate-{self.case_id}").tick()
            if tick.claimed is None:
                break
            reports.append(tick)
        return reports

    def retained_item(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """The Memory item the engine actually confirmed, or a failure saying why."""
        items = [item for item in MemoryStore(conn, schema_version=7).items(limit=200)
                 if item["engine_state"] == "RETAINED"]
        if not items:
            states = [(item["memory_type"], item["engine_state"], item["last_error_code"])
                      for item in MemoryStore(conn, schema_version=7).items(limit=200)]
            raise Failed(f"no Memory item reached RETAINED: {states}")
        return items[0]

    def retain_lesson(self, project_id: str, *, reason: str) -> tuple[str, str, str]:
        """A real failure -> a real FAIL Evidence -> a real Failure Lesson memory.

        Returns `(job_id, document_id, source_event_id)`. The whole chain is
        committed P1/P4 truth; nothing here fabricates an event id.
        """
        from jasmine_core.evidence import EvidenceStore
        from jasmine_core.state import StateStore

        conn = self.conn()
        try:
            task_id = ObjectStore(conn, schema_version=7).create(NewObject.task({
                "project_id": project_id, "title": f"p4 gate {self.case_id}",
                "host_id": self.host}), actor_id=self.human)["object"]["task_id"]
            state = StateStore(conn, schema_version=7)
            step = state.create_step(task_id, {"title": "device test", "host_id": self.host,
                                               "expected_revision": 1},
                                     actor_id=self.human, actor_kind="human",
                                     can_accept=False)["step"]["step_id"]
            state.transition_step(step, {"status": "IN_PROGRESS", "expected_revision": 1,
                                         "host_id": self.host},
                                  actor_id=self.human, actor_kind="human", can_accept=False)
            state.transition_step(step, {"status": "FAILED", "reason": reason,
                                         "expected_revision": 2, "host_id": self.host},
                                  actor_id=self.human, actor_kind="human", can_accept=False)
            EvidenceStore(conn, schema_version=7).record_tool_result(
                {"task_id": task_id, "step_id": step, "host_id": self.host,
                 "codex_session_id": self.case_id, "turn_id": f"t-{ids.new_id('evt')[-8:]}",
                 "tool_use_id": ids.new_id("evt")[-8:], "tool_name": "pytest",
                 "tool_input": {"cmd": "pytest -q"},
                 "tool_response": {"exit_code": 1},
                 "artifact_uri": "workspace:/source.txt"},
                actor_id=self.system)
            job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=7))
            report = job_store.enqueue_from_sources(eligibility.SourceRegistry())
            check(bool(report["created"]),
                  f"no eligible source was created; the committed failure did not reach the "
                  f"enqueuer: {report['ineligible']}")
            reports = self.drain(conn)
            check(any(r.outcome == "retained" for r in reports),
                  f"no retain completed: {[(r.outcome, r.error_code) for r in reports]}")
            item = self.retained_item(conn)
            job = job_store.job(next(j["job_id"] for j in job_store.jobs()
                                     if j["memory_id"] == item["memory_id"]
                                     and j["kind"] == "retain"))
            check(job["status"] == "DONE", f"the job is {job['status']}")
            check(item["engine_state"] == "RETAINED", f"the item is {item['engine_state']}")
            return job["job_id"], item["document_id"], item["source_event_id"]
        finally:
            conn.close()

    def engine_document(self, bank: scope.BankRef, document_id: str) -> dict[str, Any] | None:
        assert self.transport is not None
        return self.transport.get_document(bank=bank, document_id=document_id)

    def engine_units(self, bank: scope.BankRef, document_id: str) -> list[dict[str, Any]]:
        assert self.transport is not None
        return self.transport.list_document_memories(bank=bank, document_id=document_id)

    def recall(self, project_id: str, query: str, *, task_id: str | None = None,
               max_tokens: int = 600):
        from jasmine_core.memory.adapter import MemoryAdapter
        from jasmine_core.memory.types import RecallRequest

        conn = self.conn()
        try:
            return MemoryAdapter(conn, MemoryStore(conn, schema_version=7), self.transport,
                                 schema_version=7).recall(RecallRequest(
                project_id=project_id, actor_id=self.human, query=query, task_id=task_id,
                max_tokens=max_tokens))
        finally:
            conn.close()

    def receipt(self, kind: str, payload: dict[str, Any]) -> dict[str, str]:
        path = self.root / f"{kind}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "kind": kind}


# -- the cases ----------------------------------------------------------------


def case_g01(gate: Gate, record: CaseRecord) -> None:
    """A historical failure is recalled in a new session and changes what the agent does."""
    gate.require_p3_pack()
    gate.require_real_agent()
    project = gate.seed_project("g01")
    record.project_ids.append(project)
    job_id, document_id, source_event_id = gate.retain_lesson(
        project, reason="callback task_id did not match the task id in the request")
    record.job_ids.append(job_id)
    record.document_ids.append(document_id)
    record.source_event_ids.append(source_event_id)
    outcome = gate.recall(project, "callback task_id mismatch", max_tokens=600)
    check(outcome.status == "ok", f"recall returned {outcome.status}: {outcome.as_dict()}")
    check(bool(outcome.items), "recall returned no item for a lesson that was just retained")
    lesson = outcome.items[0]
    record.memory_result_ids.append(lesson.memory_result_id)
    check(lesson.provenance.source_event_ids == (source_event_id,),
          "the recalled lesson does not point at its source Event")
    check(lesson.historical, "a recalled lesson must be marked historical")
    record.receipts.append(gate.receipt("g01-recall", outcome.as_dict()))
    # The remaining half of G01 needs a real session: the agent has to *use* the
    # lesson. That cannot be asserted here, so this case stops with the recall
    # evidence and says so rather than claiming the benefit.
    raise Blocked(
        "the recall half is recorded; the benefit half needs a real new Agent session, "
        "a baseline pack without the lesson and a pack with it, and the tool actions "
        "that differ between them. No real session is declared in the configuration."
    )


def case_g02(gate: Gate, record: CaseRecord) -> None:
    """The current Truth wins over an older remembered Decision (VC-14/VC-15)."""
    gate.require_p3_pack()
    from jasmine_core.authority import AuthorityStore

    project = gate.seed_project("g02")
    record.project_ids.append(project)
    old_rule = gate.confirm_decision(project, content="V1 will use mem0 for long-term memory",
                                     rule_key="memory.engine")
    # Retain the old Decision, then confirm a new one through the real Authority path.
    conn = gate.conn()
    try:
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=7))
        job_store.enqueue_from_sources(eligibility.SourceRegistry())
        MemoryWorker(conn, MemoryStore(conn, schema_version=7), gate.transport,
                     lease_owner=gate.case_id).tick(scan=False)
    finally:
        conn.close()
    gate.confirm_decision(project, content="V1 will use Hindsight for long-term memory",
                          rule_key="memory.engine.v2")
    conn = gate.conn()
    try:
        rule = AuthorityStore(conn, schema_version=7).get(old_rule)
        record.rule_versions.append(f"{rule['rule_id']}:{rule['version']}")
        revision_before = conn.execute(
            "SELECT revision FROM tasks WHERE task_id IS NOT NULL").fetchone()
        record.state_revision_before = revision_before[0] if revision_before else None
    finally:
        conn.close()
    outcome = gate.recall(project, "which memory engine does V1 use", max_tokens=600)
    check(outcome.status == "ok", f"recall returned {outcome.status}: {outcome.as_dict()}")
    superseded = [f for f in outcome.filtered if f.reason == "superseded_decision"]
    check(bool(superseded),
          f"the old Decision was not filtered as superseded: {outcome.as_dict()}")
    check(all(item.memory_type != "CONFIRMED_DECISION" for item in outcome.items),
          "a superseded Decision reached the Context Pack")
    record.receipts.append(gate.receipt("g02-recall", outcome.as_dict()))
    raise Blocked(
        "the filtering half is recorded; the answering half needs a real P3 Context Pack "
        "and a real new session, so the current Decision is what the agent actually says."
    )


def case_g03(gate: Gate, record: CaseRecord) -> None:
    """Asking about something never recorded yields an honest empty (VC-17)."""
    gate.require_p3_pack()
    project = gate.seed_project("g03")
    record.project_ids.append(project)
    outcome = gate.recall(project, "device 42 new protocol test results", max_tokens=600)
    check(outcome.status == "empty",
          f"a bank with no such record must be empty, not ok: {outcome.as_dict()}")
    check(outcome.items == (), "recall invented an item for a record that does not exist")
    check(outcome.trace_id is not None, "an empty recall must still leave a trace")
    trace = MemoryStore(gate.conn(), schema_version=7).trace(outcome.trace_id)
    check(trace is not None and trace["status"] == "empty", "the empty trace is missing")
    record.receipts.append(gate.receipt("g03-empty-trace", trace or {}))
    raise Blocked(
        "the empty trace is recorded; answering 'not found' honestly needs a real P3 "
        "Context Pack and a real Agent response, which must not be simulated."
    )


def case_g04(gate: Gate, record: CaseRecord) -> None:
    """Project A's memory never appears in project B (VC-18/VC-19)."""
    from jasmine_core import errors

    gate.require_p3_pack()
    gate.require_bank_namespace()
    project_a = gate.seed_project("g04-a")
    project_b = gate.seed_project("g04-b")
    record.project_ids.extend([project_a, project_b])
    gate.confirm_decision(project_a, content="Project A may change the API shape freely",
                          rule_key="api.mutable")
    gate.confirm_decision(project_b, content="Project B must stay API compatible",
                          rule_key="api.compatible")
    for project in (project_a, project_b):
        conn = gate.conn()
        try:
            jobs_module.JobStore(conn, MemoryStore(conn, schema_version=7)).enqueue_from_sources(
                eligibility.SourceRegistry())
            MemoryWorker(conn, MemoryStore(conn, schema_version=7), gate.transport,
                         lease_owner=gate.case_id).tick(scan=False)
        finally:
            conn.close()
    for query, project, forbidden in (
        ("may I change the API shape", project_b, "change the API shape"),
        ("must I stay API compatible", project_a, "stay API compatible"),
    ):
        outcome = gate.recall(project, query, max_tokens=600)
        record.memory_result_ids.extend(i.memory_result_id for i in outcome.items)
        text = " ".join(item.text for item in outcome.items)
        check(forbidden not in text,
              f"project {project} recalled the other project's content: {text!r}")
    # A bank the query never derived cannot be injected by the caller.
    conn = gate.conn()
    try:
        banks = scope.resolve_banks(conn, project_id=project_b, task_id=None,
                                   actor_id=gate.human)
        refused = False
        try:
            # The only banks that exist for project B are B's own and the global
            # one. A caller naming A's bank is asking for something this call
            # never derived.
            scope.require_bank(f"jasmine-project-{project_a}", banks)
        except errors.NotFound:
            refused = True
        check(refused, "a bank outside the resolved set was not refused")
    finally:
        conn.close()
    record.receipts.append(gate.receipt("g04-recall", {"project_a": project_a,
                                                       "project_b": project_b}))
    raise Blocked(
        "cross-project isolation and bank-injection refusal are recorded against a real "
        "engine. Remaining: the actual P3 pack for B, and a real multiuser ACL run. The "
        f"deployment mode is {scope.deployment_mode()}, so no user-level ACL claim is made."
    )


def case_g05(gate: Gate, record: CaseRecord) -> None:
    """Truth keeps working while the engine is unreachable, and recovers (VC-22)."""
    probed = gate.require_engine()
    project = gate.seed_project("g05")
    record.project_ids.append(project)
    task_revision_before = None
    conn = gate.conn()
    try:
        job_id, document_id, source_event_id = gate.retain_lesson(
            project, reason="the device test failed before the callback change")
        record.job_ids.append(job_id)
        record.document_ids.append(document_id)
        record.source_event_ids.append(source_event_id)
        task_revision_before = conn.execute("SELECT MAX(revision) AS r FROM tasks").fetchone()["r"]
        record.state_revision_before = task_revision_before
    finally:
        conn.close()
    # Inject unreachability for the *isolated client configuration* only. This
    # points a second transport at a dead port; it does not stop the operator's
    # service, and it does not use a fake exception.
    dead = HindsightTransport(HindsightConfig("http://127.0.0.1:1", timeout_seconds=1))
    original = gate.transport
    gate.transport = dead
    try:
        outcome = gate.recall(project, "device test", max_tokens=400)
    finally:
        gate.transport = original
    check(outcome.status == "degraded",
          f"an unreachable engine must be degraded, not ok: {outcome.as_dict()}")
    check(outcome.items == (), "no item can come from an unreachable engine")
    check(bool(outcome.degraded_banks), "the failing bank is not named")
    check(outcome.trace_id is not None, "a degraded recall must still leave a trace")

    # Truth continues: a State write and an Evidence capture with the engine down.
    from jasmine_core.state import StateStore

    gate.transport = dead
    conn = gate.conn()
    try:
        task_id = ObjectStore(conn, schema_version=7).create(NewObject.task({
            "project_id": project, "title": "p4 g05 continues", "host_id": gate.host}),
            actor_id=gate.human)["object"]["task_id"]
        state = StateStore(conn, schema_version=7)
        step = state.create_step(task_id, {"title": "s", "host_id": gate.host,
                                           "expected_revision": 1},
                                 actor_id=gate.human, actor_kind="human",
                                 can_accept=False)["step"]["step_id"]
        before = conn.execute("SELECT revision FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()["revision"]
        moved = state.transition_step(step, {"status": "IN_PROGRESS", "expected_revision": 1,
                                             "host_id": gate.host},
                                      actor_id=gate.human, actor_kind="human",
                                      can_accept=False)
        record.state_revision_after = moved["task_revision"]
        record.state_revision_before = before
        record.engine_evidence["truth_during_outage"] = {
            "step": moved["step"]["status"], "task_revision": moved["task_revision"]}
    finally:
        gate.transport = original
        conn.close()
    check(record.state_revision_after == (record.state_revision_before or 0) + 1,
          f"Truth did not advance while Memory was down: "
          f"{record.state_revision_before} -> {record.state_revision_after}")
    del task_revision_before

    # Recovery: the same job path completes and there is no duplicate document.
    recovered = gate.recall(project, "device test", max_tokens=400)
    check(recovered.status == "ok", f"the engine did not recover: {recovered.as_dict()}")
    bank = scope.project_bank(project)
    units = gate.engine_units(bank, document_id)
    check(len(units) == 1, f"recovery produced {len(units)} remote documents, expected 1")
    record.engine_evidence["probe"] = probed
    record.engine_evidence["recovered_document_units"] = len(units)
    record.receipts.append(gate.receipt("g05-degraded", {
        "degraded": outcome.as_dict(), "recovered": recovered.as_dict()}))


def case_g06(gate: Gate, record: CaseRecord) -> None:
    """The two durability windows: commit-to-enqueue, and remote-success-to-DONE."""
    probed = gate.require_engine()
    project = gate.seed_project("g06")
    record.project_ids.append(project)

    # Window 1: the Truth is committed and the process "dies" before the enqueue.
    # `job_store.jobs()` on a brand-new connection is what a restarted worker
    # would see, and it must be empty while the Event is already committed.
    gate.confirm_decision(project, content="Always run the migration before deploying",
                          rule_key="migrate.first")
    conn = gate.conn()
    try:
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=7))
        check(job_store.jobs() == [], "a fresh scan should find nothing before the source commits")
        check(job_store.cursor() == 0, "the recovery cursor should not have moved")
        record.source_event_ids.append(MemoryStore(conn, schema_version=7).items and
                                       _latest_decision_event(conn, project))
    finally:
        conn.close()
    # A new process, a new connection: the scan runs from the persisted cursor.
    restarted = db.connect(gate.db_path)
    try:
        report = jobs_module.JobStore(
            restarted, MemoryStore(restarted, schema_version=7)).enqueue_from_sources(
            eligibility.SourceRegistry())
        check(len(report["created"]) == 1,
              f"the recovery scan did not create the missing job: {report}")
    finally:
        restarted.close()
    conn = gate.conn()
    try:
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=7))
        check(any(t.outcome == "retained" for t in gate.drain(conn)),
              "the recovered job did not complete")
        item = gate.retained_item(conn)
        record.job_ids.append(next(j["job_id"] for j in job_store.jobs()
                                   if j["memory_id"] == item["memory_id"]))
        record.document_ids.append(item["document_id"])

        # Window 2: a second source, a successful retain, then a crash before DONE.
        gate.confirm_decision(project, content="Pin every dependency version",
                              rule_key="deps.pinned")
        jobs_module.JobStore(conn, MemoryStore(conn, schema_version=7)).enqueue_from_sources(
            eligibility.SourceRegistry())
        pending = [j for j in jobs_module.JobStore(
            conn, MemoryStore(conn, schema_version=7)).jobs() if j["status"] == "PENDING"]
        check(bool(pending), "the second source did not produce a second job")
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=7))
        claimed = job_store.claim_due(owner="p4-gate-g06-doomed")
        check(claimed is not None, "no job to interrupt")
        assert gate.transport is not None
        gate.transport.retain(bank=claimed.bank, document_id=claimed.job["document_id"],
                              content=claimed.item["sanitized_text"],
                              context="jasmine confirmed decision", metadata={}, tags=(),
                              memory_type=claimed.item["memory_type"], timestamp=None)
        units_before = gate.engine_units(claimed.bank, claimed.job["document_id"])
        check(len(units_before) == 1, "the doomed retain did not reach the engine")
        # The process dies here: DONE was never written and the lease still holds.
        interrupted_job = claimed.job["job_id"]
        with db.transaction(conn):
            conn.execute("UPDATE memory_jobs SET lease_expires_at='1970-01-01T00:00:00.000000Z'"
                         " WHERE job_id=?", (interrupted_job,))
        second = MemoryWorker(conn, MemoryStore(conn, schema_version=7), gate.transport,
                              lease_owner="p4-gate-g06-recovered")
        check(second.tick(scan=False).outcome == "retained", "the re-send did not complete")
        final = job_store.job(interrupted_job)
        check(final["status"] == "DONE", f"the job is {final['status']} after recovery")
        attempts = job_store.attempts(interrupted_job)
        check(len([a for a in attempts if a["phase"] == "RETAINED"]) == 1,
              f"the crash-then-resend produced "
              f"{len([a for a in attempts if a['phase'] == 'RETAINED'])} completions, expected 1")
        units_after = gate.engine_units(claimed.bank, final["document_id"])
        check(len(units_after) == 1,
              f"the interrupted retain left {len(units_after)} remote memory units, expected 1")
        document = gate.engine_document(claimed.bank, final["document_id"])
        check(document is not None, "the remote document is missing after recovery")
        # The Core source is untouched by either window.
        check(conn.execute("SELECT 1 FROM rules WHERE status='ACTIVE'").fetchone() is not None,
              "a Memory crash changed Authority")
        record.engine_evidence["remote_document"] = {
            "document_id": document.get("id"), "memory_unit_count": len(units_after)}
        record.engine_evidence["attempt_count"] = len(attempts)
        record.receipts.append(gate.receipt("g06-attempts", attempts))
        record.receipts.append(gate.receipt("g06-document", document or {}))
    finally:
        conn.close()
    record.engine_evidence["probe"] = probed


def _latest_decision_event(conn: sqlite3.Connection, project_id: str) -> str:
    row = conn.execute(
        "SELECT event_id FROM events WHERE project_id=? AND event_type='rule.approved'"
        " ORDER BY seq DESC LIMIT 1", (project_id,)).fetchone()
    return row["event_id"] if row else ""


def case_g07(gate: Gate, record: CaseRecord) -> None:
    """obsolete / delete / pin against a real engine, with real receipts."""
    from jasmine_core import errors

    probed = gate.require_engine()
    project = gate.seed_project("g07")
    record.project_ids.append(project)
    job_id, document_id, source_event_id = gate.retain_lesson(
        project, reason="a lesson the operator is about to retire")
    record.job_ids.append(job_id)
    record.document_ids.append(document_id)
    record.source_event_ids.append(source_event_id)
    bank = scope.project_bank(project)

    memory_id = gate.retained_item(gate.conn())["memory_id"]

    def lifecycle(action: str, revision: int, reason: str | None = None) -> dict[str, Any]:
        from jasmine_core.memory.lifecycle import MemoryLifecycle

        conn = gate.conn()
        try:
            body: dict[str, Any] = {"expected_revision": revision, "host_id": gate.host}
            if reason:
                body["reason"] = reason
            return getattr(MemoryLifecycle(conn, MemoryStore(conn, schema_version=7)), action)(
                memory_id, body, actor_id=gate.human, actor_kind="human", host_id=gate.host)
        finally:
            conn.close()

    def revision() -> int:
        return MemoryStore(gate.conn(), schema_version=7).item(memory_id)["revision"]

    # pin: ordering only, no recall effect, and refused on an obsolete item.
    lifecycle("pin", 1)
    before = gate.recall(project, "a lesson the operator", max_tokens=400)
    check(before.status == "ok", f"a pinned item stopped being recallable: {before.as_dict()}")
    check(all(item.historical for item in before.items),
          "a pinned item stopped being historical")

    # obsolete: excluded from recall immediately, provenance intact, engine
    # soft-retired by the worker.
    result = lifecycle("obsolete", 2, "wrong lesson")
    check(result["memory"]["status"] == "OBSOLETE", "obsolete did not take")
    check(result["job"]["kind"] == "invalidate", "obsolete did not queue the engine work")
    after = gate.recall(project, "a lesson the operator", max_tokens=400)
    check(all(item.memory_id != memory_id for item in after.items),
          "an obsolete item was still recalled")
    conn = gate.conn()
    try:
        gate.drain(conn)
    finally:
        conn.close()
    units = gate.engine_units(bank, document_id)
    check(bool(units) and all(u.get("state") == "invalidated" for u in units),
          f"the engine did not soft-retire its units: {units}")
    check(gate.engine_document(bank, document_id) is not None,
          "obsolete must not physically remove the document")

    # delete: tombstone locally, physical removal remotely, source preserved.
    deleted = lifecycle("delete", revision(), "user asked")
    check(deleted["memory"]["status"] == "DELETED", "delete did not take")
    check(deleted["job"]["kind"] == "delete", "delete did not queue the engine work")
    check(MemoryStore(gate.conn(), schema_version=7).item(memory_id)["remote_deleted_at"] is None,
          "delete claimed the remote copy was already gone")
    conn = gate.conn()
    try:
        gate.drain(conn)
    finally:
        conn.close()
    check(gate.engine_document(bank, document_id) is None,
          "the remote document still exists after a confirmed delete")
    conn = gate.conn()
    try:
        check(conn.execute("SELECT 1 FROM events WHERE event_id=?",
                           (source_event_id,)).fetchone() is not None,
              "delete removed the source Raw Event; it must not")
        item = MemoryStore(conn, schema_version=7).item(memory_id)
        check(item["engine_state"] == "DELETED", f"the item is {item['engine_state']}")
        sources = MemoryStore(conn, schema_version=7).sources_of(memory_id)
        check(bool(sources), "the tombstone lost its provenance")
        for action in ("obsolete", "delete", "pin", "unpin"):
            refused = False
            try:
                # The current revision, so what refuses this is the tombstone
                # and not a stale-revision conflict.
                lifecycle(action, item["revision"])
            except errors.InvalidStateTransition:
                refused = True
            check(refused, f"a tombstone accepted {action}")
    finally:
        conn.close()
    # An unpermitted actor is refused.
    conn = gate.conn()
    try:
        from jasmine_core.memory.lifecycle import MemoryLifecycle

        agent = ids.new_id("act")
        with db.transaction(conn):
            registry.Registry(conn).upsert_actor(agent, kind="agent", home_host_id=gate.host)
        refused = False
        try:
            MemoryLifecycle(conn, MemoryStore(conn, schema_version=7)).pin(
                ids.new_id("mem"), {"expected_revision": 1, "host_id": gate.host},
                actor_id=agent, actor_kind="agent", host_id=gate.host)
        except errors.ForbiddenActorKind:
            refused = True
        check(refused, "an agent actor was allowed to curate Memory")
    finally:
        conn.close()
    record.engine_evidence["probe"] = probed
    record.engine_evidence["units_after_obsolete"] = len(units)
    record.receipts.append(gate.receipt("g07-lifecycle", {
        "pin": before.as_dict(), "obsolete": result["after"], "delete": deleted["after"]}))


CASES_BY_ID = {
    "P4-G01": case_g01, "P4-G02": case_g02, "P4-G03": case_g03, "P4-G04": case_g04,
    "P4-G05": case_g05, "P4-G06": case_g06, "P4-G07": case_g07,
}


def run_provenance() -> dict[str, Any]:
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                                  capture_output=True, text=True, timeout=15).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return ""

    status = git("status", "--porcelain")
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(status),
        "dirty_paths": status.splitlines()[:20],
        "schema_version": SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "host": socket.gethostname(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="new directory for the manifest")
    parser.add_argument("--work", required=True, help="new scratch directory")
    parser.add_argument("--config", required=True,
                        help="operator JSON describing the real deployment")
    parser.add_argument("--case", action="append", choices=list(CASES),
                        help="run only these cases (default: all)")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="development only; cannot count as a Gate")
    args = parser.parse_args()
    out, work = Path(args.out).resolve(), Path(args.work).resolve()
    if out.exists() or work.exists() or out == work or out in work.parents or work in out.parents:
        parser.error("--out and --work must be distinct new directories")
    meta = run_provenance()
    if meta["dirty"] and not args.allow_dirty:
        print("BLOCKED: checkout is dirty; freeze a candidate commit before a Gate run",
              file=sys.stderr)
        return 2
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out.mkdir(parents=True, mode=0o700)
    work.mkdir(parents=True, mode=0o700)
    selected = args.case or list(CASES)

    records: list[CaseRecord] = []
    for case_id in selected:
        record = CaseRecord(case_id, "BLOCKED", mode="not-run")
        gate = None
        try:
            gate = Gate(work, config, case_id)
            gate.open()
            gate.require_engine()
            CASES_BY_ID[case_id](gate, record)
            record.verdict = "PASS"
            record.mode = "real"
        except Blocked as blocked:
            record.verdict = "BLOCKED"
            record.mode = "not-run"
            record.blocked_on = blocked.items
            record.reason = "; ".join(blocked.items)
        except Failed as failure:
            record.verdict = "FAIL"
            record.mode = "real"
            record.reason = str(failure)
            record.failure_class = "AssertionFailure"
        except BaseException as exc:  # keep going; the remaining cases are independent
            record.verdict = "FAIL"
            record.mode = "real"
            record.reason = f"{type(exc).__name__}: {exc}"
            record.failure_class = type(exc).__name__
            record.failure_output = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
        finally:
            if gate is not None:
                gate.close()
        records.append(record)
        print(f"{case_id} {record.verdict}"
              + (f" — {record.reason}" if record.reason else ""))

    engine_probe: dict[str, Any] = {}
    probe_gate = Gate(work, config, "P4-probe")
    try:
        probe_gate.open()
        engine_probe = probe_gate.transport.version() if probe_gate.transport else {}
        if probe_gate.transport is not None:
            engine_probe["supported"] = check_supported(engine_probe)
    except BaseException as exc:  # a missing engine is reported, not raised
        engine_probe = {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        probe_gate.close()

    payload = {
        "run_id": config.get("run_id") or f"p4-{int(time.time())}",
        "phase": "P4",
        "candidate_commit": meta["commit"],
        "final_commit": None,
        "schema_version": meta["schema_version"],
        "dependency_commits": config.get("dependency_commits") or {"P2": "blocked", "P3": "blocked"},
        "engine": {
            "name": "Hindsight",
            "api_version": engine_probe.get("api_version"),
            "transport": "http",
            "origin_host": engine_probe.get("origin_host"),
            "reachable": engine_probe.get("reachable", False),
            "supported": engine_probe.get("supported"),
            "features": engine_probe.get("features") or {},
        },
        "deployment_mode": scope.deployment_mode(),
        "provenance": meta,
        "cases": [record.as_dict() for record in records],
        "verdict": (
            "PASS" if all(r.verdict == "PASS" for r in records)
            else ("BLOCKED" if all(r.verdict != "FAIL" for r in records) else "FAIL")
        ),
        "notes": [
            "Component coverage lives in tests/test_memory_*.py and is not Gate evidence.",
            "A fake transport or a wire-level stub can never produce PASS here.",
            "G01/G02/G03/G04 need a real Agent session and a P3 Context Pack; they stay "
            "BLOCKED until P2 and P3 are merged and a real session is declared.",
            f"deployment mode is {scope.deployment_mode()}; no user-level ACL claim is made "
            "in single_user mode.",
        ],
    }
    result_path = out / f"p4-gate-{int(time.time())}.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(result_path, 0o600)
    print(json.dumps({"result": str(result_path), "verdict": payload["verdict"],
                      "commit": meta["commit"], "dirty": meta["dirty"],
                      "engine_reachable": payload["engine"]["reachable"]}))
    # 0 PASS, 2 nothing could run, 1 something failed.
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[payload["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
