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
import re
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jasmine_core import SCHEMA_VERSION, clock, db, ids, registry  # noqa: E402
from jasmine_core.models import NewEvent, NewObject  # noqa: E402
from jasmine_core.objects import ObjectStore  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402
from jasmine_core.memory import eligibility, jobs as jobs_module  # noqa: E402
from jasmine_core.memory import scope
from jasmine_core.memory.scope import NAMESPACE_ENV_VAR  # noqa: E402
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
#: Cases whose mandated evidence includes an actual Context Pack.
#:
#: G05 and G07 used to be missing from this set, which is how they could reach a
#: verdict without it: G05's taskbook entry requires the Context to be marked
#: degraded with an exact pack containing no Memory, and G07's requires the pack
#: to show a recalled memory as non-authoritative and an obsolete one as not
#: injected. Both name Context as part of what they prove, so both belong here.
NEEDS_P3_PACK = frozenset({"P4-G01", "P4-G02", "P4-G03", "P4-G04",
                           "P4-G05", "P4-G07"})
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
    tool_uses: list[dict] = field(default_factory=list)
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
        # `exist_ok`: G06 runs real worker *processes*, and a process that
        # restarts has to re-enter a work directory another process created.
        # A fresh `Gate` for the same case is a restart, not an error.
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(exist_ok=True)
        (self.workspace / "source.txt").write_text("v1\n", encoding="utf-8")
        self.config = config
        self.run_id = config.get("run_id") or f"p4-{int(time.time())}"
        # The bank namespace is namespaced per run so two Gate runs against one
        # deployment cannot see each other's documents, and a rerun cannot pass
        # on the previous run's content.
        self.bank_suffix = config.get("bank_suffix") or self.run_id.replace("/", "-")
        self.db_path = self.root / "core.db"
        # Identity is persisted per work directory, so a second invocation re-entering
        # the same directory is the *same* Gate rather than a fresh one with new ids.
        # That is what makes the two-phase flow sound: the pack is built and written
        # by one invocation, an agent reads it, and a later invocation has to
        # reproduce the same pack bytes to verify the session against Core. New ids
        # per invocation would make that impossible -- and G06 already relied on
        # re-entering a work directory another process created.
        # The run's identity lives beside it, so `--resume` can only continue *this*
        # run: a different `run_id` means a different bank namespace, and continuing
        # one run's Core under another's namespace would silently mix two runs.
        run_marker = self.root.parent / "run-id"
        if run_marker.is_file():
            recorded = run_marker.read_text(encoding="utf-8").strip()
            if recorded != self.run_id:
                raise Blocked(
                    f"this work directory belongs to run {recorded!r}, the configuration "
                    f"declares {self.run_id!r}; a resumed run must be the same run, because "
                    f"the bank namespace is derived from it")
        else:
            run_marker.parent.mkdir(parents=True, exist_ok=True)
            run_marker.write_text(self.run_id, encoding="utf-8")
        identity_path = self.root / "gate-identity.json"
        if identity_path.is_file():
            saved = json.loads(identity_path.read_text(encoding="utf-8"))
            self.host, self.human = saved["host"], saved["human"]
            self.system, self.agent = saved["system"], saved["agent"]
        else:
            self.host = ids.new_id("hst")
            self.human = ids.new_id("act")
            self.system = ids.new_id("act")
            self.agent = ids.new_id("act")
            identity_path.write_text(json.dumps(
                {"host": self.host, "human": self.human,
                 "system": self.system, "agent": self.agent}), encoding="utf-8")
            os.chmod(identity_path, 0o600)
        # The Context Pack is built for an *agent*: P3's builder refuses a pack for
        # the operator and refuses `check_current` across actors, so the session this
        # Gate verifies has to belong to a real agent actor.
        self.transport: HindsightTransport | None = None
        self._previous: dict[str, str | None] = {}

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        # Before any bank is derived: `scope` reads the namespace on every
        # construction, so setting it here is what makes `bank_suffix` shape the
        # addresses this run writes to rather than being a string nothing uses.
        suffix = self.config.get("bank_suffix")
        if suffix:
            self._set_env(NAMESPACE_ENV_VAR, str(suffix))
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
            reg.upsert_actor(self.agent, kind="agent", display_name="p4 gate agent",
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
        """The declared namespace, refused unless it is actually in effect.

        Checking that a string is non-empty proves nothing about where the data
        goes. The suffix has to reach `scope`, or two runs — and a run and the
        production deployment — share `jasmine-global` and a Gate's recall can
        return memory it did not write while reporting its own namespace.
        """
        suffix = self.config.get("bank_suffix")
        if not suffix:
            raise Blocked(
                "no bank namespace is declared in the operator configuration; a Gate that "
                "shares the production bank cannot prove project isolation"
            )
        suffix = str(suffix)
        if scope.bank_namespace() != suffix:
            raise Blocked(
                f"the declared bank namespace {suffix!r} is not the one this process "
                f"derives banks with ({scope.bank_namespace()!r}); addresses are not "
                "namespaced, so the run would touch the deployment's banks"
            )
        # The namespace lives on the engine address, which is where the data is;
        # Core's own ids stay canonical so a P3 Memory provider accepts them.
        derived = {scope.engine_bank_id(scope.global_bank()),
                   scope.engine_bank_id(scope.project_bank(ids.new_id("prj")))}
        outside = {bank for bank in derived if not bank.startswith(suffix)}
        if outside:
            raise Blocked(
                f"derived bank ids are outside the declared namespace {suffix!r}: "
                f"{sorted(outside)}"
            )
        return suffix

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

    def event(self, project_id: str, event_type: str, text: str, *,
              task_id: str | None = None) -> str:
        """Append one Raw Event. Returns its id.

        `task_id` matters for anything P3 will admit as a Context source: a pack is
        built for a Task, and P3 refuses a source Event that belongs to a different
        Task (or to none) unless it carries a trusted derived binding
        (`SourceScopeMismatch: source has no trusted derived Task binding`). A prompt
        the pack is built for belongs to that prompt's own Task, so it says so.
        """
        from jasmine_core.events import EventStore

        conn = self.conn()
        try:
            stored, _ = EventStore(conn, schema_version=SCHEMA_VERSION).append(NewEvent(
                event_type=event_type, source_system=f"p4-gate-{self.case_id}",
                source_event_id=None, occurred_at=clock.now(), actor_id=self.human,
                actor_kind="human", host_id=self.host, project_id=project_id,
                task_id=task_id, payload={"text": text}))
            return stored["event_id"]
        finally:
            conn.close()

    def seed_project(self, name: str) -> str:
        from jasmine_core.models import NewObject
        from jasmine_core.objects import ObjectStore

        conn = self.conn()
        try:
            # ObjectStore.create opens its own transaction (ADR 0002).
            return ObjectStore(conn, schema_version=SCHEMA_VERSION).create(
                NewObject.project({"name": f"{self.run_id}-{name}", "host_id": self.host}),
                actor_id=self.human)["object"]["project_id"]
        finally:
            conn.close()

    def confirm_decision(self, project_id: str, *, content: str, rule_key: str) -> str:
        """A real human-confirmed Decision: the only source of a Decision memory."""
        from jasmine_core.authority import AuthorityStore

        origin_id = self.event(project_id, "user.prompt", content)
        conn = self.conn()
        try:
            authority = AuthorityStore(conn, schema_version=SCHEMA_VERSION)
            proposed = authority.propose({
                "rule_key": rule_key, "kind": "DECISION", "severity": "HARD",
                "enforcement": "CONTEXT", "content": content, "matcher": {},
                "scope": {"kind": "project", "project_id": project_id},
                "origin_event_id": origin_id, "host_id": self.host},
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
            tick = MemoryWorker(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION), self.transport,
                                lease_owner=f"p4-gate-{self.case_id}").tick()
            if tick.claimed is None:
                break
            reports.append(tick)
        return reports

    @staticmethod
    def all_items(store: MemoryStore, *, page: int = 500) -> list[dict[str, Any]]:
        """Every item, keyed past the page boundary rather than capped at one.

        `items(limit=200)` answers a window, and a Gate that filters that window
        for `RETAINED` reports "nothing was retained" whenever the item it wants
        sits past the 200th — a false failure built from a page, exactly the
        shape the worker-side completeness checks exist to prevent.
        """
        items: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(1000):
            batch = store.items(limit=page, after_memory_id=after)
            items.extend(batch)
            if len(batch) < page:
                return items
            after = batch[-1]["memory_id"]
        raise Failed("the item store exceeds 1000 pages; retention cannot be checked")

    def retained_item(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """The Memory item the engine actually confirmed, or a failure saying why."""
        everything = self.all_items(MemoryStore(conn, schema_version=SCHEMA_VERSION))
        items = [item for item in everything if item["engine_state"] == "RETAINED"]
        if not items:
            states = [(item["memory_type"], item["engine_state"], item["last_error_code"])
                      for item in everything]
            raise Failed(f"no Memory item reached RETAINED: {states}")
        return items[0]

    def new_task(self, project_id: str, title: str) -> str:
        """A real Task. Separate from `retain_lesson` so a lesson can belong to one
        task while the pack is built for another -- P3 filters `duplicate_current_fact`,
        so a lesson and the pack sharing a task would test nothing."""
        conn = self.conn()
        try:
            return ObjectStore(conn, schema_version=SCHEMA_VERSION).create(NewObject.task({
                "project_id": project_id, "title": f"{self.case_id} {title}",
                "host_id": self.host}), actor_id=self.human)["object"]["task_id"]
        finally:
            conn.close()

    def retain_lesson(self, project_id: str, *, reason: str,
                      task_id: str | None = None) -> tuple[str, str, str]:
        """A real failure -> a real FAIL Evidence -> a real Failure Lesson memory.

        Returns `(job_id, document_id, source_event_id)`. The whole chain is
        committed P1/P4 truth; nothing here fabricates an event id.
        """
        from jasmine_core.evidence import EvidenceStore
        from jasmine_core.state import StateStore

        conn = self.conn()
        try:
            if task_id is None:
                task_id = self.new_task(project_id, "device test")
            state = StateStore(conn, schema_version=SCHEMA_VERSION)
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
            EvidenceStore(conn, schema_version=SCHEMA_VERSION).record_tool_result(
                {"task_id": task_id, "step_id": step, "host_id": self.host,
                 "codex_session_id": self.case_id, "turn_id": f"t-{ids.new_id('evt')[-8:]}",
                 "tool_use_id": ids.new_id("evt")[-8:], "tool_name": "pytest",
                 "tool_input": {"cmd": "pytest -q"},
                 "tool_response": {"exit_code": 1},
                 "artifact_uri": "workspace:/source.txt"},
                actor_id=self.system)
            job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
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

    def engine_units(self, bank: scope.BankRef, document_id: str,
                     *, state: str | None = None) -> list[dict[str, Any]]:
        """Every unit of the document.

        The Gate's assertions are counts — "exactly one document", "every unit
        is `invalidated`" — and a count taken from one page is a count of one
        page. G07 would report all units retired from the first 200 while the
        rest were still `valid`, and G05/G06 would accept or reject a document
        on a partial list.
        """
        assert self.transport is not None
        return self.transport.list_all_document_memories(bank=bank,
                                                         document_id=document_id,
                                                         state=state)

    def recall(self, project_id: str, query: str, *, task_id: str | None = None,
               max_tokens: int = 600):
        from jasmine_core.memory.adapter import MemoryAdapter
        from jasmine_core.memory.types import RecallRequest

        conn = self.conn()
        try:
            return MemoryAdapter(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION), self.transport,
                                 schema_version=SCHEMA_VERSION).recall(RecallRequest(
                project_id=project_id, actor_id=self.human, query=query, task_id=task_id,
                max_tokens=max_tokens))
        finally:
            conn.close()

    # -- real worker processes -------------------------------------------

    def spawn_worker(self, source: str, *, label: str, expect_exit: int | None = None,
                     extra: dict[str, Any] | None = None
                     ) -> subprocess.CompletedProcess:
        """Run `source` as a real OS process and return it.

        G06 asks for a worker process that *exits* between two windows. Reopening
        a connection in the parent proves nothing about that: the state a crash
        discards is the state that only ever lived inside the process, and no
        amount of reconnecting exercises it. So the windows are crossed by
        starting a process, letting it die, and continuing in another.

        The child inherits this process's environment — including
        `JASMINE_HINDSIGHT_URL`, which is how it builds a real transport — and
        is given the parent's host/actor ids so the Events it commits belong to
        the same identities the rest of the case checks against.
        """
        script = self.root / f"{label}.py"
        script.write_text(textwrap.dedent(self.CHILD_BOOTSTRAP) + textwrap.dedent(source),
                          encoding="utf-8")
        os.chmod(script, 0o600)
        options = json.dumps({
            "src": str(ROOT / "src"),
            "gate": str(ROOT / "scripts" / "p4-memory-gate.py"),
            "work": str(self.root.parent),
            "case_id": self.case_id,
            "config": self.config,
            "host": self.host, "human": self.human, "system": self.system,
            "db_path": str(self.db_path),
            **(extra or {}),
        }, ensure_ascii=False)
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(
            os.pathsep)
        proc = subprocess.run([sys.executable, str(script), options],
                              capture_output=True, text=True,
                              timeout=self.child_timeout(), env=env)
        if expect_exit is not None and proc.returncode != expect_exit:
            raise Failed(
                f"{label}: the worker process exited {proc.returncode}, expected "
                f"{expect_exit}; stderr={(proc.stderr or '')[-1200:]}"
            )
        return proc

    def child_timeout(self) -> float:
        """How long a spawned worker process may take.

        A child that retains has to wait for the engine, and a real engine's
        extraction is a model call: the deployment this Gate was written against
        answered `retain` in well under the old fixed 180 s, but a local
        llama.cpp extraction on the same engine measured 51 s to 510 s per
        document depending on chunk length. A cap that expires mid-retain reports
        a harness timeout as a P4 failure, which is the one thing this runner
        exists not to do -- it cannot tell a slow engine from a stuck worker.

        So the cap is the engine's own timeout plus room for the rest of the tick,
        and the operator can still set it outright. `JASMINE_HINDSIGHT_TIMEOUT_SECONDS`
        is the same number P4's transport uses, so by default the child is never
        killed while its own engine call is still legitimately in flight.
        """
        configured = self.config.get("worker_timeout_seconds")
        if configured is not None:
            return float(configured)
        try:
            engine = float(config_from_env().timeout_seconds) if config_from_env() else 0.0
        except (AttributeError, TypeError, ValueError):
            engine = 0.0
        return max(180.0, engine * 2 + 60.0)

    #: Source every child starts with: rebuild this same Gate in its own process.
    CHILD_BOOTSTRAP = """
    import json, os, sys
    opts = json.loads(sys.argv[1])
    sys.path.insert(0, opts["src"])
    import importlib.util
    from pathlib import Path
    # Every child names `SCHEMA_VERSION` rather than the number, so the bootstrap
    # imports it once here instead of each snippet importing its own copy.
    from jasmine_core import SCHEMA_VERSION
    spec = importlib.util.spec_from_file_location("p4_memory_gate", opts["gate"])
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)

    def boot():
        g = runner.Gate(Path(opts["work"]), opts["config"], opts["case_id"])
        g.host, g.human, g.system = opts["host"], opts["human"], opts["system"]
        g.db_path = Path(opts["db_path"])
        g.open()
        return g
    """

    # -- real P3 Context Pack, and the agent session that consumed it -------
    #
    # The Pack is built by P3's own `ContextBuilder` with Memory served by P3's
    # own `MemoryProvider` running *this* worker script -- so the bytes an agent
    # receives are the bytes P3 renders, not a reconstruction. The receipt below is
    # what binds an agent session to those bytes: it names the pack's rendered
    # sha256, and `verify_session` re-derives that hash from the pack this Gate
    # built rather than believing the session's own claim about what it saw.

    def memory_provider(self, *, engine_url: str | None = None):
        """P3's `MemoryProvider` over this repo's worker, with a fixed environment.

        `engine_url` overrides the engine the worker is told to reach. It has to be a
        parameter: the worker reads its URL from this environment, so pointing the
        process at another engine by setting `os.environ` did nothing here -- the
        provider was still handing the worker *this* Gate's transport URL. G05 asked
        for a pack built against an unreachable engine and got one built against the
        live one, with the receipt naming the unreachable URL.
        """
        from jasmine_core.context.memory_provider import MemoryProvider

        return MemoryProvider(
            argv=[sys.executable, str(ROOT / "scripts" / "p4-memory-worker.py")],
            provider_id=f"{self.case_id}-p4",
            environment={"JASMINE_CORE_DB": str(self.db_path.resolve()),
                         "JASMINE_MEMORY_ACTOR_ID": self.human,
                         "JASMINE_MEMORY_DEPLOYMENT_MODE": os.environ.get(
                             "JASMINE_MEMORY_DEPLOYMENT_MODE", "single_user"),
                         # The namespace has to travel with the request. Without it the
                         # worker recalls `jasmine-project-...` while everything this Gate
                         # retained went to `<suffix>jasmine-project-...`, so the pack came
                         # back `empty` against an engine that held the lesson -- the same
                         # isolation the namespace exists for, applied to the Gate itself.
                         "JASMINE_MEMORY_BANK_NAMESPACE": self.bank_suffix,
                         "JASMINE_HINDSIGHT_URL": (engine_url
                                                   or str(self.transport.config.base_url)),
                         "JASMINE_HINDSIGHT_TIMEOUT_SECONDS": os.environ.get(
                             "JASMINE_HINDSIGHT_TIMEOUT_SECONDS", "300")})

    def build_pack(self, *, project_id: str, task_id: str, prompt_event_id: str,
                   key: str, engine_url: str | None = None):
        """One real P3 Context Pack, plus the receipt that binds a session to it.

        `engine_url` overrides the engine the worker is told to reach, which is how
        G05 builds a pack against a deliberately unreachable engine. It is recorded
        in the receipt, so a pack built against a dead engine can never be presented
        as one built against a healthy one.
        """
        from jasmine_core.context.builder import ContextBuilder
        from jasmine_core.context.tokens import Tokenizer

        provider = self.memory_provider(engine_url=engine_url)
        conn = self.conn()
        try:
            builder = ContextBuilder(conn, schema_version=SCHEMA_VERSION,
                                     tokenizer=Tokenizer(), memory=provider)
            context = builder.build(
                {"task_id": task_id, "host_id": self.host,
                 "source_event_id": prompt_event_id, "reason": "USER_PROMPT",
                 "idempotency_key": key}, actor_id=self.agent)["context"]
            current = builder.check_current(
                context["context_pack_id"],
                {"host_id": self.host, "source_event_id": prompt_event_id},
                actor_id=self.agent)
        finally:
            conn.close()

        rendered = context["rendered_content"]
        pack = {
            "context_pack_id": context["context_pack_id"],
            "rendered_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "rendered_content": rendered,
            "token_count": context["token_count"],
            "sections": [m["section"] for m in context["section_metrics"]],
            "memory_status": context["memory"]["status"],
            "memory_selected": context["memory"].get("selected") or [],
            "memory_filtered": context["memory"].get("filtered") or [],
            "qualified_provenance": context["memory"].get("qualified_provenance") or [],
            "truth_digest": current["truth_digest"],
            "engine_url": engine_url or str(self.transport.config.base_url),
            "project_id": project_id, "task_id": task_id,
            "prompt_event_id": prompt_event_id,
            "check_current": current["current"],
        }
        pack["idempotency_key"] = key
        path = self.root / f"pack-{context['context_pack_id']}.json"
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
        pack["receipt"] = {"path": str(path),
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return pack

    def git_commit(self) -> str:
        try:
            return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=15).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return ""

    def existing_pack(self, key: str) -> dict[str, Any] | None:
        """The pack a previous phase of *this* run already built, or `None`.

        The pack is the unit of resumption. Re-running a case must not re-seed: P3
        refuses an idempotency key replayed with a different request body
        (`Conflict: idempotency_conflict`), and re-seeding produces a different Task
        and a different source Event, so the second phase would be refused for
        having built a second, different pack rather than continuing the first.
        """
        for path in sorted(self.root.glob("pack-*.json")):
            try:
                pack = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if pack.get("idempotency_key") == key:
                pack["receipt"] = {"path": str(path),
                                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                return pack
        return None

    def session_path(self, label: str) -> Path:
        return self.root / f"session-{label}.json"

    def verify_session(self, pack: dict[str, Any], label: str, *,
                       require_tool_actions: bool = True) -> dict[str, Any]:
        """Import an agent session receipt and check it against Core, not against itself.

        The session file is an *input from outside this process* -- a real agent ran
        somewhere and wrote down what it did. So nothing in it is taken on trust:

          * `commit` must be this candidate commit, so a session cannot be reused
            across a code change;
          * `pack_sha256` must equal the hash of the bytes *this Gate rendered*,
            recomputed here, which is what proves the agent read this pack rather
            than a description of it;
          * `context_pack_id` must exist in Core and `check_current` must still be
            true, so the pack is admitted as current Truth for that task;
          * every id the session claims to have seen must exist in Core, and the
            source event ids must be the ones P3's `qualified_provenance` recorded;
          * the tool actions must be non-empty and each must carry the output it
            actually produced.

        Returns the session dict with a `verified` block the cases assert on.
        """
        commit = self.git_commit()
        path = self.session_path(label)
        if not path.is_file():
            raise Blocked(f"no agent session receipt for {label} at {path}; run the agent "
                          f"session and write it there -- it cannot be synthesised here")
        session = json.loads(path.read_text(encoding="utf-8"))
        problems: list[str] = []

        if session.get("commit") != commit:
            problems.append(f"the session ran against {session.get('commit')!r}, this "
                            f"candidate is {commit}")
        if session.get("context_pack_id") != pack["context_pack_id"]:
            problems.append(f"the session names pack {session.get('context_pack_id')!r}, the "
                            f"pack built here is {pack['context_pack_id']!r}")
        recomputed = hashlib.sha256(pack["rendered_content"].encode("utf-8")).hexdigest()
        if session.get("pack_sha256") != recomputed:
            problems.append(f"the session saw pack bytes hashing to {session.get('pack_sha256')!r}, "
                            f"this Gate rendered {recomputed}")
        if not pack["check_current"]:
            problems.append("the pack is no longer current for its task, so a session that "
                            "read it would have been reasoning from stale Truth")

        # The Memory the pack actually carried, re-derived from Core rather than
        # from either file's own summary of it.
        conn = self.conn()
        try:
            row = conn.execute("SELECT rendered_sha256, truth_digest FROM context_packs"
                               " WHERE context_pack_id=?",
                               (pack["context_pack_id"],)).fetchone()
            if row is None:
                problems.append(f"Core has no Context Pack {pack['context_pack_id']}")
            else:
                if row["rendered_sha256"] != recomputed:
                    problems.append("the pack stored in Core hashes differently from the one "
                                    "built here")
                if row["truth_digest"] != pack["truth_digest"]:
                    problems.append("the pack's truth digest changed between build and import")
            carried = {q["memory_id"]: q for q in pack["qualified_provenance"]}
            for seen in session.get("memory_seen") or []:
                memory_id = seen.get("memory_id")
                if memory_id not in carried:
                    problems.append(f"the session claims memory {memory_id!r}, which this pack "
                                    f"did not carry: {sorted(carried)}")
                    continue
                if sorted(seen.get("evidence_ids") or []) != sorted(carried[memory_id]["evidence_ids"]):
                    problems.append(f"the session's Evidence for {memory_id} differs from the "
                                    f"pack's")
                if sorted(seen.get("source_event_ids") or []) != sorted(carried[memory_id]["source_event_ids"]):
                    problems.append(f"the session's source Events for {memory_id} differ from "
                                    f"the pack's")
                # And the cited Events must be real rows, not ids the session made up.
                for event_id in carried[memory_id]["source_event_ids"]:
                    if conn.execute("SELECT 1 FROM events WHERE event_id=?",
                                    (event_id,)).fetchone() is None:
                        problems.append(f"the pack cites Event {event_id}, which Core has not got")
        finally:
            conn.close()

        actions = session.get("tool_actions") or []
        if require_tool_actions and not actions:
            problems.append("the session recorded no tool action, so nothing was actually done")
        for action in actions:
            if not isinstance(action, dict) or not action.get("command"):
                problems.append(f"a tool action has no command: {action!r}")
                continue
            if "output" not in action:
                problems.append(f"the tool action {action.get('command')!r} recorded no output")

        if problems:
            raise Failed("the agent session receipt does not stand up: " + "; ".join(problems))
        return {**session, "verified": {
            "commit": commit, "context_pack_id": pack["context_pack_id"],
            "pack_sha256": recomputed, "memory_carried": sorted(carried),
            "tool_actions": len(actions), "pack_receipt": pack["receipt"]["path"]}}

    def receipt(self, kind: str, payload: dict[str, Any]) -> dict[str, str]:
        path = self.root / f"{kind}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "kind": kind}


# -- the cases ----------------------------------------------------------------


def case_g01(gate: Gate, record: CaseRecord) -> None:
    """A historical failure is recalled in a new session and changes what the agent does."""
    gate.require_real_agent()
    key = "g01-with-lesson"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g01_pack(gate, record, resumed, key)
    project = gate.seed_project("g01")
    record.project_ids.append(project)

    # The lesson must belong to a *historical* task. P3 filters
    # `duplicate_current_fact`, so a lesson derived from Evidence the current turn
    # already carries is correctly not injected -- the scenario only means anything
    # when the new task does not already have that Evidence, which is what "a later
    # session hits the same problem" means.
    historical = gate.new_task(project, "device test (historical)")
    job_id, document_id, source_event_id = gate.retain_lesson(
        project, reason="callback task_id did not match the task id in the request",
        task_id=historical)
    record.job_ids.append(job_id)
    record.document_ids.append(document_id)
    record.source_event_ids.append(source_event_id)

    current = gate.new_task(project, "device test (new session)")
    prompt = gate.event(project, "user.prompt",
                        "the device test fails again; where should I look first?",
                        task_id=current)
    record.source_event_ids.append(prompt)

    pack = gate.build_pack(project_id=project, task_id=current, prompt_event_id=prompt,
                           key=key)
    _verify_g01_pack(gate, record, pack, key)


def _verify_g01_pack(gate: Gate, record: CaseRecord, pack: dict[str, Any],
                     key: str) -> None:
    """Everything G01 asserts about the pack and the session that consumed it."""
    record.context_pack_ids.append(pack["context_pack_id"])
    check(pack["memory_status"] == "ok",
          f"the pack's Memory section is {pack['memory_status']}, not ok: {pack}")
    check(bool(pack["memory_selected"]),
          f"the pack carried no Memory for a lesson the project has: {pack['memory_filtered']}")
    carried = pack["memory_selected"][0]
    record.memory_result_ids.append(carried["memory_id"])
    check(carried["memory_type"] == "FAILURE_LESSON", carried)
    check(bool(pack["qualified_provenance"]),
          "P3 qualified no provenance, so the pack's Memory cannot be attributed")
    record.receipts.append(gate.receipt("g01-pack", {k: v for k, v in pack.items()
                                                     if k != "rendered_content"}))

    # The rendered pack must present the lesson as history, never as current Truth.
    rendered = pack["rendered_content"]
    check("NON AUTHORITATIVE" in rendered or "NON-AUTHORITATIVE" in rendered,
          "the pack does not mark its Memory section non-authoritative")
    # Traced means the pack names the Events P3 qualified -- not merely that a lesson
    # was retained. The id the scan happened to record on the Memory item is not the
    # set the pack has to carry; the qualified provenance is.
    for source_id in carried["source_event_ids"]:
        check(source_id in rendered,
              f"the pack does not carry {source_id}, so the injected fact cannot be "
              f"traced to the Events behind it")

    session = gate.verify_session(pack, "g01")
    record.receipts.append(gate.receipt("g01-session", session["verified"]))
    check(bool(session["verified"]["memory_carried"]),
          "the session was verified against a pack that carried no Memory")

    # VC-16 asks what the lesson *did*, and the taskbook is explicit that the agent's
    # own account of being helped is not the evidence. So the claim is differential
    # and checkable here: the same task, the same repository, two real sessions, and
    # the lesson-bearing one must have taken a diagnostic action the baseline did not.
    baseline = session.get("baseline")
    check(isinstance(baseline, dict),
          "the session receipt carries no `baseline`: without the pack-without-the-lesson "
          "run there is nothing to compare, and 'the agent said it helped' is not evidence")
    baseline_actions = [a.get("command") for a in (baseline.get("tool_actions") or [])]
    lesson_actions = [a.get("command") for a in (session.get("tool_actions") or [])]
    check(bool(baseline_actions) and bool(lesson_actions),
          f"one of the two sessions recorded no tool action: baseline={baseline_actions}, "
          f"lesson={lesson_actions}")
    # What P3's pack can actually deliver is recorded, not imagined. By design it
    # renders the Core-derived *fact* and never provider prose, so the lesson reaches
    # the agent as "this project recorded a FAIL result for this suite before, and
    # here are the Events behind it" -- not as a diagnosis. Asserting that the lesson
    # saved a specific number of investigative steps would be claiming a benefit this
    # design does not provide, so the differential is recorded as evidence and the
    # assertions are the properties the pack really does carry.
    record.engine_evidence["differential"] = {
        "baseline_actions": baseline_actions, "with_lesson_actions": lesson_actions,
        "skipped_investigation": [c for c in baseline_actions if c not in lesson_actions],
        "note": "P3 renders the Core-derived fact only, never provider prose, so the "
                "lesson arrives as a traceable prior failure rather than as a diagnosis"}
    cited = session.get("memory_seen") or []
    check(bool(cited), "the session recorded no memory as seen, so it cannot have used it")
    check(cited[0].get("memory_id") == carried["memory_id"],
          f"the session saw {cited[0].get('memory_id')!r}, the pack carried "
          f"{carried['memory_id']!r}")
    # It must have been *used*: a session that recorded the id and then investigated
    # exactly as the baseline did has not had anything recalled for it.
    check(lesson_actions != baseline_actions,
          f"the lesson-bearing session took the identical steps to the baseline, so "
          f"nothing about the pack reached it: {lesson_actions}")
    # And the lesson must actually be cited, with the claim kept to what it supports:
    # a historical cause is not this turn's cause.
    answer = session.get("final_answer") or ""
    check(carried["memory_id"] in answer or cited[0].get("evidence_ids", [""])[0] in answer,
          f"the answer cites neither the memory nor its Evidence, so the recalled "
          f"lesson did not reach what it said: {answer[:300]!r}")
    # The property the taskbook is explicit about: a historical cause is not this
    # turn's cause. The pack says a FAIL happened before; it does not say why this
    # run failed, and an answer that asserts the old cause as the current one has
    # turned history into an oracle.
    check(any(marker in answer.lower() for marker in
              ("histor", "previously", "last time", "earlier", "before", "prior")),
          f"the answer does not mark the recalled lesson as historical: {answer[:300]!r}")
    check("task_id" in answer,
          f"the answer reaches no cause at all, so it cannot have arranged a useful "
          f"diagnosis from the pack: {answer[:300]!r}")


def case_g02(gate: Gate, record: CaseRecord) -> None:
    """The current Truth wins over an older remembered Decision (VC-14/VC-15)."""
    gate.require_real_agent()
    key = "g02-current-decision"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g02_pack(gate, record, resumed)
    gate.require_p3_pack()
    from jasmine_core.authority import AuthorityStore

    project = gate.seed_project("g02")
    record.project_ids.append(project)
    old_rule = gate.confirm_decision(project, content="V1 will use mem0 for long-term memory",
                                     rule_key="memory.engine")
    # Retain the old Decision, then confirm a new one through the real Authority path.
    conn = gate.conn()
    try:
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        job_store.enqueue_from_sources(eligibility.SourceRegistry())
        MemoryWorker(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION), gate.transport,
                     lease_owner=gate.case_id).tick(scan=False)
    finally:
        conn.close()
    # The new Decision must *supersede* the old one. Creating a second rule with a
    # different key would leave the old rule ACTIVE at version 1, so the
    # superseded-decision filter would never fire and this case would report a
    # false FAIL against a healthy Core.
    conn = gate.conn()
    try:
        authority = AuthorityStore(conn, schema_version=SCHEMA_VERSION)
        authority.transition(old_rule, "supersede", {
            "expected_revision": 2, "host_id": gate.host, "kind": "DECISION",
            "severity": "HARD", "enforcement": "CONTEXT",
            "content": "V1 will use Hindsight for long-term memory", "matcher": {},
            "scope": {"kind": "project", "project_id": project},
            "origin_event_id": gate.event(project, "user.prompt", "switch to Hindsight"),
        }, actor_id=gate.human)
    finally:
        conn.close()
    conn = gate.conn()
    try:
        rule = AuthorityStore(conn, schema_version=SCHEMA_VERSION).get(old_rule)
        record.rule_versions.append(f"{rule['rule_id']}:{rule['version']}")
        check(rule["version"] == 2, f"the old rule is still at version {rule['version']}")
    finally:
        conn.close()
    # The supersede created rule version 2, and nothing has sent it to the engine yet.
    # Without this the bank holds only the version-1 document, so the recall below can
    # only ever return the superseded item, filter it, and answer `empty` -- which is
    # the honest answer but tests nothing about the *current* Truth winning. Both
    # versions have to be in the bank for "current Truth first" to be a claim at all.
    conn = gate.conn()
    try:
        jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)
                             ).enqueue_from_sources(eligibility.SourceRegistry())
        MemoryWorker(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION), gate.transport,
                     lease_owner=gate.case_id).tick(scan=False)
    finally:
        conn.close()
    # The invariant VC-14/15 also names: Recall reads State and must not move it. What
    # G02 creates is a *Rule*, so the Rule's revision is the thing to watch -- the
    # previous form read `tasks.revision`, which is empty in this case, so the value
    # was `None`, `CaseRecord.as_dict` dropped it, and nothing was ever compared. An
    # unasserted invariant and an absent one look identical in the manifest.
    conn = gate.conn()
    try:
        rule_before = AuthorityStore(conn, schema_version=SCHEMA_VERSION).get(old_rule)
        task_before = conn.execute(
            "SELECT task_id, revision FROM tasks ORDER BY task_id").fetchall()
        record.state_revision_before = rule_before["version"]
    finally:
        conn.close()
    outcome = gate.recall(project, "which memory engine does V1 use", max_tokens=600)
    check(outcome.status == "ok", f"recall returned {outcome.status}: {outcome.as_dict()}")
    superseded = [f for f in outcome.filtered if f.reason == "superseded_decision"]
    check(bool(superseded),
          f"the old Decision was not filtered as superseded: {outcome.as_dict()}")
    # Not "no CONFIRMED_DECISION at all": the current Decision is a CONFIRMED_DECISION
    # and it is exactly what must win. What must not arrive is the *superseded version*
    # -- and "which version" is a fact on the item, so that is what is asserted. The
    # previous form (`no CONFIRMED_DECISION in items`) also contradicted the
    # `status == "ok"` assertion above it, which requires at least one item.
    decisions = [item for item in outcome.items if item.memory_type == "CONFIRMED_DECISION"]
    check(bool(decisions), f"the current Decision was not offered at all: {outcome.as_dict()}")
    check(all(item.provenance.origin_rule_version == 2 for item in decisions),
          "a superseded version of the Decision reached the Context Pack: "
          f"{[(i.provenance.origin_rule_id, i.provenance.origin_rule_version) for i in decisions]}")
    check(all(item.historical for item in decisions),
          "a Decision must reach the Context Pack as history, never as current Truth")
    # ...and the comparison that makes those two numbers mean something.
    conn = gate.conn()
    try:
        rule_after = AuthorityStore(conn, schema_version=SCHEMA_VERSION).get(old_rule)
        task_after = conn.execute(
            "SELECT task_id, revision FROM tasks ORDER BY task_id").fetchall()
        record.state_revision_after = rule_after["version"]
        check(rule_after["version"] == record.state_revision_before,
              f"Recall moved the Rule's revision: {record.state_revision_before} -> "
              f"{rule_after['version']}. Memory is history; it never rewrites Authority.")
        check([tuple(row) for row in task_after] == [tuple(row) for row in task_before],
              "Recall moved a Task revision: "
              f"{[(r['task_id'], r['revision']) for r in task_before]} -> "
              f"{[(r['task_id'], r['revision']) for r in task_after]}")
        check(rule_after["status"] == "ACTIVE",
              f"Recall changed the Rule's status to {rule_after['status']}")
    finally:
        conn.close()
    record.receipts.append(gate.receipt("g02-recall", outcome.as_dict()))

    # A Task whose prompt is the question the engine just answered, so the pack asks
    # for the same thing the recall did. Both versions of the Decision are in the
    # bank; only version 2 may arrive.
    task_id = gate.new_task(project, "V1 memory engine")
    prompt = gate.event(project, "user.prompt",
                        "which memory engine does V1 use?", task_id=task_id)
    record.source_event_ids.append(prompt)
    pack = gate.build_pack(project_id=project, task_id=task_id, prompt_event_id=prompt,
                           key=key)
    _verify_g02_pack(gate, record, pack)

def _verify_g02_pack(gate: Gate, record: CaseRecord, pack: dict[str, Any]) -> None:
    """The current Decision must arrive as Truth, and the superseded one nowhere.

    The first form of this asserted the pack's Memory section carried version 2. That
    was wrong about what Memory is for, and it failed for the right reason: a Decision
    has no Evidence behind it, so the worker never bridges one -- `_P3_ELIGIBLE` is
    `FAILURE_LESSON`/`VERIFIED_OUTCOME` only, because sending a Decision would be a
    claim P3's `filter_memory` cannot verify. The current Decision does not need
    Memory at all: it is Authority, and the pack's AUTHORITY section carries it as
    CURRENT TRUTH. So the invariant is about which version is in the pack, not about
    which section it is in.
    """
    record.context_pack_ids.append(pack["context_pack_id"])
    rendered = pack["rendered_content"]

    # 1. The current Decision is present, as Truth, naming the current engine.
    authority = rendered.split("AUTHORITY\n", 1)[-1].split("\nCURRENT STATE", 1)[0]
    check("hindsight" in authority.lower(),
          f"the pack's AUTHORITY section does not carry the current Decision: {authority!r}")
    check('"version":2' in authority.replace(" ", ""),
          f"the pack's AUTHORITY section is not at version 2: {authority!r}")
    check('"kind":"DECISION"' in authority.replace(" ", ""),
          f"the pack's AUTHORITY section does not carry a Decision: {authority!r}")

    # 2. No Decision reaches the pack as memory at all -- current or superseded.
    decisions = [item for item in pack["memory_selected"] + pack["memory_filtered"]
                 if item.get("memory_type") == "CONFIRMED_DECISION"]
    check(not decisions,
          f"a Decision reached the pack as memory: {decisions}; Authority is not Memory")

    # 3. 'mem0' was the superseded engine. If it appears anywhere in the rendered pack
    # the superseded version leaked -- filtered items are dropped, not rendered with a
    # warning beside them, so a pack carrying both would look complete while being
    # wrong. This is the assertion that fails if the supersession filter regresses.
    check("mem0" not in rendered.lower(),
          "the superseded engine (mem0) is still in the rendered pack, so the current "
          "Truth did not win")

    # 4. And the engine agreed one level below: the recall behind this pack reported
    # the old version as filtered-for-supersession rather than dropping both silently.
    filtered = pack["memory_filtered"]
    check(not filtered or any(row.get("reason") == "superseded_decision" for row in filtered),
          f"the pack filtered something for a reason other than supersession: {filtered}")

    record.receipts.append(gate.receipt("g02-pack", {k: v for k, v in pack.items()
                                                     if k != "rendered_content"}))

    # As in G03, no tool action is required: there is nothing to run to answer
    # "which engine does V1 use" from an injected context. What is asserted is the
    # answer itself, and it is asserted on both counts -- the current engine named,
    # the superseded one not named.
    session = gate.verify_session(pack, "g02", require_tool_actions=False)
    record.receipts.append(gate.receipt("g02-session", session["verified"]))
    answer = (session.get("final_answer") or "").lower()
    check("hindsight" in answer,
          f"the agent did not name the current engine: {answer[:300]!r}")
    check("mem0" not in answer,
          f"the agent named the superseded engine: {answer[:300]!r}")


def case_g03(gate: Gate, record: CaseRecord) -> None:
    """Asking about something never recorded yields an honest empty (VC-17)."""
    gate.require_real_agent()
    key = "g03-unknown-protocol"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g03_pack(gate, record, resumed)
    project = gate.seed_project("g03")
    record.project_ids.append(project)
    outcome = gate.recall(project, "device 42 new protocol test results", max_tokens=600)
    check(outcome.status == "empty",
          f"a bank with no such record must be empty, not ok: {outcome.as_dict()}")
    check(outcome.items == (), "recall invented an item for a record that does not exist")
    check(outcome.trace_id is not None, "an empty recall must still leave a trace")
    trace = MemoryStore(gate.conn(), schema_version=SCHEMA_VERSION).trace(outcome.trace_id)
    check(trace is not None and trace["status"] == "empty", "the empty trace is missing")
    record.receipts.append(gate.receipt("g03-empty-trace", trace or {}))

    # A pack for a Task that has never recorded this protocol. The Memory section
    # must come back honest -- `empty`, with no injected fact at all.
    task_id = gate.new_task(project, "device 42 protocol (never recorded)")
    prompt = gate.event(project, "user.prompt",
                        "did we ever record results for device 42's new protocol test?",
                        task_id=task_id)
    record.source_event_ids.append(prompt)
    pack = gate.build_pack(project_id=project, task_id=task_id, prompt_event_id=prompt,
                           key=key)
    _verify_g03_pack(gate, record, pack)


def _verify_g03_pack(gate: Gate, record: CaseRecord, pack: dict[str, Any]) -> None:
    """The pack must be honestly empty, and the session must say so."""
    record.context_pack_ids.append(pack["context_pack_id"])
    check(pack["memory_status"] == "empty",
          f"a protocol nothing recorded came back {pack['memory_status']}: "
          f"{pack['memory_selected']} {pack['memory_filtered']}")
    check(pack["memory_selected"] == [] and pack["qualified_provenance"] == [],
          "the pack injected a memory for a record that does not exist")
    rendered = pack["rendered_content"]
    check("HISTORICAL_NONAUTHORITATIVE MEMORY DATA" not in rendered,
          "the pack rendered a Memory data block for an unknown protocol")
    check(f'"status":"{pack["memory_status"]}"' in rendered,
          "the pack does not state its Memory status, so a reader cannot tell an "
          "empty answer from a missing one")
    record.receipts.append(gate.receipt("g03-pack", {k: v for k, v in pack.items()
                                                     if k != "rendered_content"}))

    # Tool actions are *not* required here, and requiring them would assert the
    # opposite of what VC-17 is about. There is nothing to run: the honest answer to
    # "was this ever recorded" when the Memory section is empty is to say so, and a
    # session that went looking for something to do would be manufacturing evidence
    # of work. What is asserted instead is that the answer admits the absence and
    # asserts nothing about a result.
    session = gate.verify_session(pack, "g03", require_tool_actions=False)
    record.receipts.append(gate.receipt("g03-session", session["verified"]))
    check(session["verified"]["tool_actions"] == 0,
          f"the session took {session['verified']['tool_actions']} actions for a protocol "
          f"nothing recorded; an empty Memory needs no investigation")
    answer = session.get("final_answer") or ""
    # VC-17: it must say it is not recorded. Accept an honest negative and nothing
    # that asserts a PASS or a historical verification that never happened.
    lowered = answer.lower()
    check(any(marker in lowered for marker in
              ("not recorded", "no record", "never", "not found", "no evidence",
               "nothing recorded", "not in the record", "no such")),
          f"the answer does not say the record is absent: {answer[:300]!r}")
    for fabrication in ("passed", "verified", "confirmed", "succeeded", "all green"):
        check(fabrication not in lowered,
              f"the answer asserts {fabrication!r} for a protocol nothing recorded: "
              f"{answer[:300]!r}")


def _acl_evidence(gate: Gate, record: CaseRecord) -> dict[str, Any]:
    """Run the ACL battery in its own database, namespace and server process."""
    import subprocess
    import tempfile

    acl_root = Path(tempfile.mkdtemp(prefix=f"p4-acl-{gate.case_id}-", dir=str(gate.root)))
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "p4-memory-acl.py"),
         "--db", str(acl_root / "core.db"), "--namespace", f"{gate.bank_suffix}-acl"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "JASMINE_MEMORY_DEPLOYMENT_MODE": "acl"})
    try:
        return json.loads(completed.stdout)
    except ValueError:
        raise Failed("the ACL battery produced no JSON: "
                     f"{(completed.stderr or completed.stdout)[-600:]}")


def case_g04(gate: Gate, record: CaseRecord) -> None:
    """Project A's memory never appears in project B (VC-18/VC-19)."""
    gate.require_real_agent()
    key = "g04-project-b-pack"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g04_pack(gate, record, resumed)
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
            jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)).enqueue_from_sources(
                eligibility.SourceRegistry())
            MemoryWorker(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION), gate.transport,
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

    # Project B's own pack: the question A was never allowed to see, asked in B.
    # A pack is where cross-project isolation has to hold, because a pack is what
    # an agent actually reads.
    task_id = gate.new_task(project_b, "API compatibility")
    prompt = gate.event(project_b, "user.prompt",
                        "may I change the API shape?", task_id=task_id)
    record.source_event_ids.append(prompt)
    pack = gate.build_pack(project_id=project_b, task_id=task_id,
                           prompt_event_id=prompt, key=key)
    _verify_g04_pack(gate, record, pack)

    # The ACL leg, in its own database and its own namespace, over the real
    # authenticated API with real keys. It cannot run in this one: `acl` is read from
    # the environment on every `ProjectAccess` construction, so proving it would mean
    # changing the mode the rest of the run is asserting under.
    acl = _acl_evidence(gate, record)
    check(acl["all_passed"],
          f"the multiuser ACL evidence did not hold: "
          f"{[(c['check'], c['status'], c['error_code']) for c in acl['checks'] if not c['passed']]}")
    check(acl["deployment_mode"] == "acl" and acl["server_saw_mode"] == "acl",
          f"the ACL run declared {acl['deployment_mode']!r} and the server reported "
          f"{acl['server_saw_mode']!r}; under `single_user` no refusal is expected at all")
    record.engine_evidence["acl"] = {
        "deployment_mode": acl["deployment_mode"],
        "server_saw_mode": acl["server_saw_mode"],
        "grants": acl["grants"],
        "checks": [{k: c[k] for k in ("check", "actor", "method", "path", "granted",
                                       "status", "error_code", "passed")}
                   for c in acl["checks"]],
        "agent_manage_key_refused_at_issuance":
            acl["agent_manage_key_refused_at_issuance"],
    }



def _verify_g04_pack(gate: Gate, record: CaseRecord, pack: dict[str, Any]) -> None:
    """Project B's pack must carry B's constraint and nothing of A's.

    It reads project B out of the pack rather than taking it as an argument, because
    the resumed path has no project ids of its own: `--resume` re-derives nothing. It
    also makes the isolation check stronger than naming A: *every* project id in the
    rendered text has to be B's, so an unexpected third project fails too.
    """
    record.context_pack_ids.append(pack["context_pack_id"])
    project_b = pack["project_id"]

    # Both sides are lowercased. The needle used to keep its capitals while the
    # haystack was lowercased, so the check could never be true -- the case would
    # have failed on a perfectly correct pack, and the reason read like a missing
    # Decision rather than a broken comparison.
    rendered = pack["rendered_content"].lower()
    check("must stay api compatible" in rendered,
          "project B's pack does not carry B's own Decision")
    check("change the api shape freely" not in rendered,
          "project B's pack carries project A's Decision; isolation did not hold at the "
          "pack, where an agent would actually read it")
    named = set(re.findall(r"prj_[0-9A-Z]{26}", rendered))
    check(named <= {project_b},
          f"project B's pack names other projects: {sorted(named - {project_b})}")
    for item in pack["memory_selected"]:
        check(item.get("bank", {}).get("project_id") in (None, project_b),
              f"project B's pack carries an item from another bank: {item.get('bank')}")
    record.receipts.append(gate.receipt("g04-pack", {k: v for k, v in pack.items()
                                                     if k != "rendered_content"}))

    # The agent must answer from B's constraint alone. Naming A's rule would mean the
    # isolation leaked somewhere the database checks above cannot see.
    session = gate.verify_session(pack, "g04", require_tool_actions=False)
    record.receipts.append(gate.receipt("g04-session", session["verified"]))
    answer = (session.get("final_answer") or "").lower()
    check("api compatible" in answer,
          f"the agent did not state B's constraint: {answer[:300]!r}")
    check("change the api shape" not in answer,
          f"the agent offered A's freedom to the B session: {answer[:300]!r}")


def case_g05(gate: Gate, record: CaseRecord) -> None:
    gate.require_real_agent()
    key = "g05-degraded-pack"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g05_pack(gate, record, resumed)
    """Truth keeps working while the engine is unreachable, and recovers (VC-22)."""
    # G05 is not only about the connection: it also requires the Context to be marked
    # degraded with an exact pack containing no Memory. That is required at the *end*,
    # by `require_p3_pack()`, and not here. Declared first, it switched this case's
    # entire real-engine half off behind one config string, so a defect in the
    # unreachable path, the Truth-continues checks or the recovery was recorded as
    # BLOCKED and looked exactly like "P3 is not merged". A Gate cannot report a
    # precondition as satisfied by not running.
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
        task_id = ObjectStore(conn, schema_version=SCHEMA_VERSION).create(NewObject.task({
            "project_id": project, "title": "p4 g05 continues", "host_id": gate.host}),
            actor_id=gate.human)["object"]["task_id"]
        state = StateStore(conn, schema_version=SCHEMA_VERSION)
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
    # Not a unit count. Hindsight extracts *several* facts from one document --
    # two from a single short lesson -- and how many is the engine's business, not
    # P4's: `document_id` is the idempotency key, so "recovery did not duplicate
    # anything" is a statement about documents and about Core, both of which are
    # counted below. Asserting one unit would mean this Gate could only ever pass
    # against a double that fabricates one unit per document.
    check(bool(units), "recovery left the document in the engine with no memory unit")
    check(gate.engine_document(bank, document_id) is not None,
          "recovery lost the remote document")
    record.engine_evidence["probe"] = probed
    record.engine_evidence["recovered_document_units"] = len(units)
    record.receipts.append(gate.receipt("g05-degraded", {
        "degraded": outcome.as_dict(), "recovered": recovered.as_dict()}))
    # The connection, Truth-continues and recovery halves are real. G05 also
    # mandates that the *Context* is marked degraded with an exact pack that
    # contains no Memory, that a real Tool Evidence is captured, and that a
    # legal State/Checkpoint flow runs — all of which need a merged P3 and a
    # real session. Without them this case must not return, because a returning
    # case is recorded as PASS. Required here, after the real half has run.
    gate.require_p3_pack()
    # The degraded pack: built against an engine that is not there. Truth still has
    # to render -- that is the whole of VC-22 -- but the Memory section must say
    # degraded and carry nothing, rather than quietly rendering an empty Memory that
    # reads like "nothing was ever remembered".
    task_id = gate.new_task(project, "engine unreachable")
    prompt = gate.event(project, "user.prompt",
                        "carry on while the engine is down", task_id=task_id)
    record.source_event_ids.append(prompt)
    pack = gate.build_pack(project_id=project, task_id=task_id, prompt_event_id=prompt,
                           key=key, engine_url="http://127.0.0.1:1")
    _verify_g05_pack(gate, record, pack)


def _verify_g05_pack(gate: Gate, record: CaseRecord, pack: dict[str, Any]) -> None:
    """A degraded Memory must be *labelled*, and Truth must still be there.

    The distinction this case turns on: `empty` means nothing was ever remembered,
    `degraded` means the engine could not be asked. Both render a Memory section with
    no items, so only the status tells them apart -- and an agent that reads `empty`
    concludes the project has no history, which is a false statement about the world
    rather than a gap in its context.
    """
    record.context_pack_ids.append(pack["context_pack_id"])
    check(pack["memory_status"] == "degraded",
          f"a pack built against an unreachable engine reports {pack['memory_status']!r}; "
          f"an unreachable engine is not the same as an empty one")
    check(pack["memory_selected"] == [] and pack["memory_filtered"] == [],
          f"a degraded pack carried memory: {pack['memory_selected']} "
          f"{pack['memory_filtered']}")
    check("HISTORICAL_NONAUTHORITATIVE MEMORY DATA" not in pack["rendered_content"],
          "a degraded pack rendered a Memory data block")

    rendered = pack["rendered_content"]
    check('"status":"degraded"' in rendered.replace(" ", ""),
          "the pack does not state that its Memory is degraded, so a reader cannot "
          "tell an unreachable engine from an empty one")
    check(pack["engine_url"] == "http://127.0.0.1:1",
          f"the pack claims engine {pack['engine_url']}, not the unreachable one it was "
          f"built against")

    # Truth is the point of VC-22: the engine being down must not cost the agent its
    # current state. Without this a pack could be entirely omissions and still be
    # 'degraded' in the right way.
    check("CURRENT STATE" in rendered and pack["task_id"] in rendered,
          "a degraded pack lost CURRENT STATE; the engine going down must not cost the "
          "agent its Truth")
    check(pack["token_count"] > 0, "a degraded pack rendered nothing at all")
    check(pack["check_current"] is True,
          "check_current did not hold for a pack built against a dead engine")
    record.receipts.append(gate.receipt("g05-degraded-pack", pack["context_pack_id"]))

    # The agent has to keep working from Truth, and has to say that Memory was not
    # consulted rather than pretending the project has no history.
    session = gate.verify_session(pack, "g05", require_tool_actions=True)
    record.receipts.append(gate.receipt("g05-session", session["verified"]))
    answer = (session.get("final_answer") or "").lower()
    check(any(word in answer for word in ("degraded", "unreachable", "could not be reached",
                                          "not available", "unavailable", "down",
                                          "could not reach", "cannot reach")),
          f"the agent did not notice its Memory was unavailable: {answer[:300]!r}")
    # These phrases are only false when *asserted*. "degraded does not mean the project
    # has no history" says the opposite of "there is no history", and a substring test
    # cannot tell them apart -- it rejected a correct answer on exactly that
    # distinction. So each occurrence is only counted when nothing negates it nearby.
    # This is a heuristic and it is documented as one: an LLM judge would read the
    # sentence properly but would not be reproducible run to run, and an acceptance
    # gate that changes its mind is worse than one with a blunt rule.
    negated = ("not ", "n't", "never", "no ", "cannot", "can't", "is not", "was not",
               "rather than", "instead of", "does not", "did not")
    for invention in ("no memory", "no history", "nothing remembered", "never recorded",
                      "no records"):
        for match in re.finditer(re.escape(invention), answer):
            window = answer[max(0, match.start() - 60):match.start()]
            if not any(neg in window for neg in negated):
                raise Failed(
                    f"the agent asserted {invention!r} from a degraded Memory, which is "
                    f"a false claim about the project rather than a gap in its context: "
                    f"{answer[max(0, match.start() - 60):match.end() + 40]!r}")

    # And the tool result it reported is recorded as real Tool Evidence, output and
    # all -- the capture is only worth having if the bytes are the ones the command
    # produced.
    _record_session_tool_evidence(gate, record, pack, session)


def _record_session_tool_evidence(gate: Gate, record: CaseRecord, pack: dict[str, Any],
                                  session: dict[str, Any]) -> None:
    """Record what the session actually ran, through the real Evidence path."""
    from jasmine_core.evidence import EvidenceStore
    from jasmine_core.state import StateStore

    actions = session.get("tool_actions") or []
    command, output = actions[0]["command"], actions[0]["output"]
    record.tool_uses.append({"command": command, "output_sha256": hashlib.sha256(
        output.encode("utf-8")).hexdigest(), "output_characters": len(output)})

    conn = gate.conn()
    try:
        state = StateStore(conn, schema_version=SCHEMA_VERSION)
        step = state.create_step(pack["task_id"],
                                 {"title": "engine unreachable", "host_id": gate.host,
                                  "expected_revision": 1},
                                 actor_id=gate.human, actor_kind="human",
                                 can_accept=False)["step"]["step_id"]
        stored = EvidenceStore(conn, schema_version=SCHEMA_VERSION).record_tool_result(
            {"task_id": pack["task_id"], "step_id": step, "host_id": gate.host,
             "codex_session_id": gate.case_id, "turn_id": f"t-{ids.new_id('evt')[-8:]}",
             "tool_use_id": ids.new_id("evt")[-8:], "tool_name": "shell",
             "tool_input": {"cmd": command}, "tool_response": {"stdout": output},
             "artifact_uri": "workspace:/session-tool-output"},
            actor_id=gate.system)
        evidence_id = stored.get("evidence_id") or stored["evidence"]["evidence_id"]
        record.evidence_ids.append(evidence_id)

        # Read it back through the public getter rather than trusting the write: the
        # claim is that the bytes the command produced are now in Core. What the row
        # keeps is `command_sha256` and the parsed result, so the command is compared
        # by hash and the output by looking for it in what was stored -- an Evidence
        # record that quietly dropped the output would fail here.
        read = EvidenceStore(conn, schema_version=SCHEMA_VERSION).get(evidence_id)
        check(read is not None, f"tool Evidence {evidence_id} does not read back")
        check(read["command_sha256"] == hashlib.sha256(command.encode("utf-8")).hexdigest(),
              f"the stored command hash is {read['command_sha256']!r}, not the hash of "
              f"the command the session ran")
        check(output in json.dumps(read, ensure_ascii=False),
              "the stored tool Evidence does not contain the output the session reported")
        check(read["task_id"] == pack["task_id"],
              f"tool Evidence is bound to {read['task_id']}, not the pack's task")
        record.receipts.append(gate.receipt("g05-tool-evidence",
                                            {"evidence_id": evidence_id,
                                             "command": command,
                                             "output_sha256": hashlib.sha256(
                                                 output.encode("utf-8")).hexdigest()}))
    finally:
        conn.close()



def case_g06(gate: Gate, record: CaseRecord) -> None:
    """The two durability windows, each crossed by a real process that dies.

    The taskbook asks for the worker process itself to exit between
    commit/enqueue and between remote-success/DONE. Reopening a connection in
    this process would satisfy the letter of nothing: the state a crash
    discards is the state that only ever lived in the dead process, and no
    reconnect exercises it. So both windows are crossed with
    `Gate.spawn_worker` — one process commits and disappears, another picks up
    the work — and the assertions are made here, on what a restarted worker
    actually finds.
    """
    probed = gate.require_engine()
    project = gate.seed_project("g06")
    record.project_ids.append(project)

    # -- Window 1: Truth committed, process gone before the enqueue. --------
    gate.spawn_worker(
        """
        g = boot()
        g.confirm_decision(opts["project"],
                           content="Always run the migration before deploying",
                           rule_key="migrate.first")
        # The Event is committed and this process never scans. It exits now, so
        # the enqueue that should follow has to survive being done by somebody
        # else later, from the persisted cursor.
        sys.exit(0)
        """,
        label="g06-window1-commit", expect_exit=0, extra={"project": project})

    conn = gate.conn()
    try:
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        check(job_store.jobs() == [],
              "a dead process still left a job: the enqueue is not where it should be")
        check(job_store.cursor() == 0,
              "a dead process still moved the recovery cursor")
        record.source_event_ids.append(_latest_decision_event(conn, project))
    finally:
        conn.close()

    scan = json.loads(gate.spawn_worker(
        """
        from jasmine_core import SCHEMA_VERSION, db
        from jasmine_core.memory import eligibility, jobs as jobs_module
        from jasmine_core.memory.store import MemoryStore

        g = boot()
        conn = db.connect(g.db_path)
        report = jobs_module.JobStore(
            conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)).enqueue_from_sources(
            eligibility.SourceRegistry())
        outcomes = [t.outcome for t in g.drain(conn)]
        store = MemoryStore(conn, schema_version=SCHEMA_VERSION)
        retained = [i for i in runner.Gate.all_items(store)
                    if i["engine_state"] == "RETAINED"]
        jobs = jobs_module.JobStore(conn, store).jobs()
        print(json.dumps({
            "created": len(report["created"]),
            "outcomes": outcomes,
            "job_statuses": sorted({j["status"] for j in jobs}),
            "retained": len(retained),
            "document_ids": [i["document_id"] for i in retained],
        }))
        """,
        label="g06-window1-scan", expect_exit=0, extra={"project": project}).stdout.strip())
    check(scan["created"] == 1,
          f"the restarted worker did not create exactly the missing job: {scan}")
    check(scan["retained"] == 1 and scan["job_statuses"] == ["DONE"],
          f"the restarted worker did not finish the job cleanly: {scan}")
    record.document_ids.extend(scan["document_ids"])
    record.engine_evidence["window1"] = scan

    # -- Window 2: remote success, process gone before DONE. ----------------
    gate.confirm_decision(project, content="Pin every dependency version",
                          rule_key="deps.pinned")
    conn = gate.conn()
    try:
        report = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)
                                      ).enqueue_from_sources(eligibility.SourceRegistry())
        check(len(report["created"]) == 1, f"the second source produced no job: {report}")
        pending = [j for j in jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)
                                                   ).jobs() if j["status"] == "PENDING"]
        check(len(pending) == 1, f"expected one pending job, got {pending}")
        interrupted_job = pending[0]["job_id"]
        document_id = pending[0]["document_id"]
        bank_id = pending[0]["bank_id"]
    finally:
        conn.close()

    # A real worker process, killed by the real transport wrapper at the exact
    # point the remote write has succeeded and DONE has not been written. It
    # exits 7; reaching the line after `tick` would mean the window was missed.
    gate.spawn_worker(
        """
        from jasmine_core import db
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.memory.worker import MemoryWorker

        class DieAfterRemoteWrite:
            # Delegates to the real engine, then takes this process down. The
            # point is to die inside `_process_retain`, after
            # `transport.retain(...)` has returned and before `jobs.complete(...)`.
            # (A comment, not a docstring: this source lives inside a triple
            #  quoted string, where one would end it.)
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def retain(self, **kwargs):
                self._real.retain(**kwargs)
                os._exit(7)

        g = boot()
        conn = db.connect(g.db_path)
        MemoryWorker(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION),
                     DieAfterRemoteWrite(g.transport),
                     lease_owner="p4-gate-g06-doomed").tick(scan=False)
        os._exit(99)
        """,
        label="g06-window2-crash", expect_exit=7)

    conn = gate.conn()
    try:
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        crashed = job_store.job(interrupted_job)
        check(crashed["status"] == "RUNNING",
              f"a crashed worker did not leave the job RUNNING: {crashed['status']}")
        check(crashed["lease_owner"] == "p4-gate-g06-doomed",
              f"the crashed worker does not hold the lease: {crashed['lease_owner']}")
        from jasmine_core.memory import scope as _scope
        bank = _scope.project_bank(project)
        units_before = gate.engine_units(bank, document_id)
        # The window is "the engine has it, Core does not know yet", so what has to
        # be true is that the document reached the engine with at least one
        # extracted unit -- not that it has exactly one. The real engine extracts
        # several facts per document.
        check(bool(units_before),
              f"the doomed retain did not reach the engine: {len(units_before)} units")
        # Time passes; the lease is what makes a second worker allowed to take over.
        with db.transaction(conn):
            conn.execute(
                "UPDATE memory_jobs SET lease_expires_at='1970-01-01T00:00:00.000000Z'"
                " WHERE job_id=?", (interrupted_job,))
    finally:
        conn.close()

    restarted = json.loads(gate.spawn_worker(
        """
        from jasmine_core import db
        from jasmine_core.memory import jobs as jobs_module
        from jasmine_core.memory.store import MemoryStore
        from jasmine_core.memory.worker import MemoryWorker

        g = boot()
        conn = db.connect(g.db_path)
        store = MemoryStore(conn, schema_version=SCHEMA_VERSION)
        report = MemoryWorker(conn, store, g.transport,
                              lease_owner="p4-gate-g06-restarted").tick(scan=False)
        job = jobs_module.JobStore(conn, store).job(opts["job_id"])
        attempts = jobs_module.JobStore(conn, store).attempts(opts["job_id"])
        print(json.dumps({
            "outcome": report.outcome,
            "error_code": report.error_code,
            "status": job["status"],
            "retained_attempts": len([a for a in attempts if a["phase"] == "RETAINED"]),
            "attempt_count": len(attempts),
            "bank": opts["bank_id"],
            "document_id": opts["document_id"],
        }))
        """,
        label="g06-window2-restart", expect_exit=0,
        extra={"job_id": interrupted_job, "document_id": document_id,
               "bank_id": bank_id}).stdout.strip())
    check(restarted["outcome"] == "retained",
          f"the re-send did not complete: {restarted}")
    check(restarted["status"] == "DONE",
          f"the job is {restarted['status']} after recovery: {restarted}")
    check(restarted["retained_attempts"] == 1,
          f"the crash-then-resend produced {restarted['retained_attempts']} completions, "
          f"expected 1: {restarted}")

    conn = gate.conn()
    try:
        job_store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        attempts = job_store.attempts(interrupted_job)
        bank = scope.project_bank(project)
        units_after = gate.engine_units(bank, document_id)
        check(bool(units_after),
              "the interrupted retain left the document with no remote memory unit "
              "after recovery")
        document = gate.engine_document(bank, document_id)
        check(document is not None, "the remote document is missing after recovery")
        # Idempotency where it is actually countable: one document, carrying every
        # unit the engine extracted from it, and no second document for the same
        # memory. The count of units is the engine's; the count of documents is
        # the property P4 promised.
        check(int(document.get("memory_unit_count") or 0) == len(units_after),
              f"the engine reports {document.get('memory_unit_count')} units but lists "
              f"{len(units_after)}; the Gate would be reading one page as the whole")
        # The Core source is untouched by either window.
        check(conn.execute("SELECT 1 FROM rules WHERE status='ACTIVE'").fetchone() is not None,
              "a Memory crash changed Authority")
        record.document_ids.append(document_id)
        record.job_ids.append(interrupted_job)
        record.engine_evidence["remote_document"] = {
            "document_id": document.get("id"), "memory_unit_count": len(units_after)}
        record.engine_evidence["attempt_count"] = len(attempts)
        record.engine_evidence["windows"] = {
            "window1": record.engine_evidence.get("window1"),
            "window2_crash_exit": 7, "window2": restarted}
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

    # G07's Context half — a recalled memory shown non-authoritative, an obsolete
    # one shown not injected, and the source read back *from the pack* — needs P3.
    # The Context Pack half is required at the end of this case, not here: gating the
    # whole lifecycle on it would mean a broken pin/obsolete/delete path could never
    # be reported at all.
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
            return getattr(MemoryLifecycle(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)), action)(
                memory_id, body, actor_id=gate.human, actor_kind="human", host_id=gate.host)
        finally:
            conn.close()

    def revision() -> int:
        return MemoryStore(gate.conn(), schema_version=SCHEMA_VERSION).item(memory_id)["revision"]

    # pin: ordering only, no recall effect, and refused on an obsolete item.
    lifecycle("pin", 1)
    before = gate.recall(project, "a lesson the operator", max_tokens=400)
    check(before.status == "ok", f"a pinned item stopped being recallable: {before.as_dict()}")
    check(all(item.historical for item in before.items),
          "a pinned item stopped being historical")

    # A stale `expected_revision` is a refusal, not a silent overwrite: two
    # operators acting on the same item cannot clobber each other. The item is
    # at revision 2 now (pin bumped it), so revision 1 is stale.
    stale_refused = False
    try:
        lifecycle("unpin", 1, "a second operator who read the item before the pin")
    except errors.RevisionConflict:
        stale_refused = True
    check(stale_refused, "a stale expected_revision was accepted")
    check(MemoryStore(gate.conn(), schema_version=SCHEMA_VERSION).item(memory_id)["revision"] == 2,
          "a refused stale revision still changed the item")

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
    # Asked for by name: the engine's default listing *excludes* invalidated units,
    # so reading it here would prove nothing -- an engine that ignored the invalidate
    # entirely would answer exactly the same way. The soft-retire is only observable
    # by asking for the retired units.
    retired = gate.engine_units(bank, document_id, state="invalidated")
    check(bool(retired), "the engine holds no retired unit for an obsoleted document")
    check(all(u.get("state") == "invalidated" for u in retired),
          f"the engine did not soft-retire its units: {retired}")
    check(gate.engine_units(bank, document_id) == [],
          "a soft-retired unit is still in the default listing")
    check(gate.engine_document(bank, document_id) is not None,
          "obsolete must not physically remove the document")

    # delete: tombstone locally, physical removal remotely, source preserved.
    deleted = lifecycle("delete", revision(), "user asked")
    check(deleted["memory"]["status"] == "DELETED", "delete did not take")
    check(deleted["job"]["kind"] == "delete", "delete did not queue the engine work")
    check(MemoryStore(gate.conn(), schema_version=SCHEMA_VERSION).item(memory_id)["remote_deleted_at"] is None,
          "delete claimed the remote copy was already gone")

    # Remote delete failure first: the engine becomes unreachable while the
    # delete is queued. The item must stay excluded locally, the job must not
    # claim to be done, and the document must still be there — and then a later
    # attempt must finish the job. This is the "远端删除故障保留排除状态并恢复成功"
    # half, and it needs a real unreachable endpoint, not a fake exception.
    live_transport = gate.transport
    gate.transport = HindsightTransport(HindsightConfig("http://127.0.0.1:1",
                                                        timeout_seconds=1))
    try:
        conn = gate.conn()
        try:
            gate.drain(conn)
        finally:
            conn.close()
        during = gate.recall(project, "a lesson the operator", max_tokens=400)
        check(all(i.memory_id != memory_id for i in during.items),
              "a failed remote delete stopped excluding the item locally")
        conn = gate.conn()
        try:
            jobs = [j for j in jobs_module.JobStore(
                conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)).jobs()
                if j["memory_id"] == memory_id and j["kind"] == "delete"]
            check(len(jobs) == 1, f"expected one delete job, got {jobs}")
            failed_delete = jobs[0]
            check(failed_delete["status"] == "PENDING",
                  f"the delete job is {failed_delete['status']} after an unreachable "
                  f"engine; it should be waiting to retry, not DONE and not wedged")
            check(failed_delete["last_error_code"] is not None,
                  "the failed delete recorded no error code")
            item = MemoryStore(conn, schema_version=SCHEMA_VERSION).item(memory_id)
            check(item["status"] == "DELETED", f"the item is {item['status']}")
            check(item["remote_deleted_at"] is None,
                  "a delete that could not reach the engine claimed it completed")
        finally:
            conn.close()
    finally:
        gate.transport = live_transport

    # "The delete did not remove the document" can only be asked of an engine that
    # answers. This assertion used to sit inside the dead-transport block above, where
    # it was guaranteed to raise `engine_unreachable` -- a transport pointed at
    # 127.0.0.1:1 cannot confirm anything, so the case failed against every
    # deployment rather than testing the thing it names.
    check(gate.engine_document(bank, document_id) is not None,
          "the document was removed by a delete that could not reach the engine")

    # Recovery: the engine is reachable again and the same job finishes.
    conn = gate.conn()
    try:
        with db.transaction(conn):
            conn.execute(
                "UPDATE memory_jobs SET next_attempt_at='1970-01-01T00:00:00.000000Z'"
                " WHERE memory_id=? AND kind='delete' AND status='PENDING'", (memory_id,))
        gate.drain(conn)
        final_job = [j for j in jobs_module.JobStore(
            conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)).jobs()
            if j["memory_id"] == memory_id and j["kind"] == "delete"][0]
        check(final_job["status"] == "DONE",
              f"the delete job is {final_job['status']} after the engine recovered")
        final_item = MemoryStore(conn, schema_version=SCHEMA_VERSION).item(memory_id)
        check(final_item["engine_state"] == "DELETED",
              f"the item is {final_item['engine_state']} after a recovered delete")
        check(final_item["remote_deleted_at"] is not None,
              "the recovered delete did not record when the remote copy went away")
    finally:
        conn.close()
    check(gate.engine_document(bank, document_id) is None,
          "the remote document still exists after a confirmed delete")
    conn = gate.conn()
    try:
        check(conn.execute("SELECT 1 FROM events WHERE event_id=?",
                           (source_event_id,)).fetchone() is not None,
              "delete removed the source Raw Event; it must not")
        item = MemoryStore(conn, schema_version=SCHEMA_VERSION).item(memory_id)
        check(item["engine_state"] == "DELETED", f"the item is {item['engine_state']}")
        sources = MemoryStore(conn, schema_version=SCHEMA_VERSION).sources_of(memory_id)
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
            MemoryLifecycle(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)).pin(
                ids.new_id("mem"), {"expected_revision": 1, "host_id": gate.host},
                actor_id=agent, actor_kind="agent", host_id=gate.host)
        except errors.ForbiddenActorKind:
            refused = True
        check(refused, "an agent actor was allowed to curate Memory")
    finally:
        conn.close()
    record.engine_evidence["probe"] = probed
    record.engine_evidence["units_after_obsolete"] = len(retired)
    record.engine_evidence["delete_fault_then_recovery"] = {
        "failed_error_code": failed_delete["last_error_code"],
        "recovered_status": final_job["status"],
    }
    gate.require_p3_pack()
    record.receipts.append(gate.receipt("g07-lifecycle", {
        "pin": before.as_dict(), "obsolete": result["after"], "delete": deleted["after"],
        "delete_fault": {"status": failed_delete["status"],
                         "error_code": failed_delete["last_error_code"],
                         "recovered": final_job["status"]}}))
    # The lifecycle and engine halves above are real and recorded. The Context
    # half is not, and G07's taskbook entry requires it: an actual Context Pack
    # proving a recalled memory is not authoritative and an obsolete one is not
    # injected, plus the source read-back *from the pack*. That needs a merged P3
    # and a real session, neither of which exists here, so this case refuses to
    # return — a returning case is recorded as PASS.
    raise Blocked(
        "the pin/obsolete/delete lifecycle, the remote-delete fault and recovery, the "
        "stale-revision refusal and the actor refusal are recorded against a real engine. "
        "Still missing for G07: an actual Context Pack showing a recalled memory marked "
        "non-authoritative and an obsolete one not injected, the pack-level source "
        "read-back, and the management audit Event for these lifecycle commands. Those "
        "need a P3 Context Pack built in a real Agent session; until one is supplied "
        "this case is BLOCKED, not PASS. Everything above it ran against the real engine."
    )


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
    parser.add_argument("--resume", action="store_true",
                        help="continue a run whose work directory already exists, for the "
                             "same run_id. The agent-session flow needs it: one invocation "
                             "builds the Context Pack, an agent reads it outside this "
                             "process, and a later invocation has to verify its receipt "
                             "against the same Core. Isolation is unchanged -- the bank "
                             "namespace is derived from run_id, so a *new* run still cannot "
                             "see a previous run's documents")
    args = parser.parse_args()
    out, work = Path(args.out).resolve(), Path(args.work).resolve()
    if out.exists() or out == work or out in work.parents or work in out.parents:
        parser.error("--out must be a new directory, distinct from --work")
    if work.exists() and not args.resume:
        parser.error(f"--work {work} already exists; pass --resume to continue that run, "
                     f"or choose a new directory so a rerun cannot pass on its content")
    if args.resume and not work.is_dir():
        parser.error(f"--resume was given but --work {work} does not exist")
    meta = run_provenance()
    if meta["dirty"] and not args.allow_dirty:
        print("BLOCKED: checkout is dirty; freeze a candidate commit before a Gate run",
              file=sys.stderr)
        return 2
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out.mkdir(parents=True, mode=0o700)
    # exist_ok only under --resume, which already proved the directory belongs to this
    # run; a fresh run must still create its own.
    work.mkdir(parents=True, mode=0o700, exist_ok=args.resume)
    selected = args.case or list(CASES)

    records: list[CaseRecord] = []
    # Captured from the first `open()`: each case's `finally` closes its Gate and
    # `close()` restores whatever `_set_env` saw, so reading the namespace after
    # the loop — or after the probe below — reports "" even though every case ran
    # with it in effect.
    namespace_used = ""
    for case_id in selected:
        record = CaseRecord(case_id, "BLOCKED", mode="not-run")
        gate = None
        try:
            gate = Gate(work, config, case_id)
            gate.open()
            namespace_used = scope.bank_namespace()
            gate.require_engine()
            CASES_BY_ID[case_id](gate, record)
            record.verdict = "PASS"
            record.mode = "real"
        except Blocked as blocked:
            record.verdict = "BLOCKED"
            # `not-run` is a claim that nothing happened, and it was recorded for
            # every case that ended in `Blocked` -- including the six that retained,
            # recalled, obsoleted, deleted, faulted and recovered against the real
            # engine first and only then found a precondition missing. A manifest
            # that says `not-run` next to six receipts is telling a reviewer the
            # opposite of what happened.
            record.mode = ("real" if (record.receipts or record.engine_evidence
                                      or record.job_ids or record.document_ids)
                           else "not-run")
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

    # A run that did not evaluate every case, or that was taken on a dirty
    # checkout, is evidence about *something* but it is not the stage
    # acceptance. Reporting PASS from it is how a subset run gets read as a
    # full one, and `--allow-dirty` exists precisely so a development run can
    # never be mistaken for the frozen candidate.
    incomplete = [case_id for case_id in CASES if case_id not in selected]
    stage_reasons: list[str] = []
    if incomplete:
        stage_reasons.append(
            f"only {len(selected)} of {len(CASES)} cases were selected; not run: "
            f"{', '.join(incomplete)}")
    if args.allow_dirty:
        stage_reasons.append(
            "the checkout is dirty (--allow-dirty): a development run, never a "
            "stage acceptance")
    stage_acceptance = not stage_reasons
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
        "bank_namespace": namespace_used,
        "provenance": meta,
        "cases": [record.as_dict() for record in records],
        "scope": {"selected": sorted(selected), "ran": sorted(selected),
                  "complete": not incomplete, "not_run": sorted(incomplete)},
        "stage_acceptance": {"eligible": stage_acceptance, "reasons": stage_reasons},
        "verdict": (
            "PASS" if (stage_acceptance and all(r.verdict == "PASS" for r in records))
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
