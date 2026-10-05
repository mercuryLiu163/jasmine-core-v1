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

#: G01 asks one question, in both sessions, byte for byte. It used to ask two:
#: "the device test fails; where should I look first?" on one side and "the device
#: test fails again; where should I look first?" on the other, so a difference in
#: behaviour had a second explanation that no assertion could exclude.
G01_PROMPT = "the device test fails; where should I look first?"

#: And one task title, for the same reason. The Task *ids* cannot match -- P3 refuses
#: an idempotency key replayed with a different body, so the two sides cannot share a
#: Task -- but everything else about the two Tasks can, and has to.
G01_TASK_TITLE = "device test failure triage"

#: The closed vocabulary of exploration actions G01 is allowed to compare. Declared
#: here, closed here, and required of both sessions: the differential used to be a set
#: difference over free-text command strings, which made a rewording of the same action
#: read as a skipped investigation, and made "one action against eleven" a benefit.
EXPLORATION_CATEGORIES = ("enumerate_repository", "read_history", "inspect_toolchain",
                           "read_documentation", "reproduce_failure",
                           "inspect_build_artifacts", "probe_arithmetic")

#: Category -> command-pattern table. Exploration booleans are self-declared and
#: never cross-checked against `tool_actions` on their own, so identical work with
#: different booleans would manufacture a benefit. The gate derives each category
#: from the session's verified `tool_actions` and requires the derived set to equal
#: the declared set. Each entry is a predicate over one command string.
def _exploration_matches(category: str, command: str) -> bool:
    """Whether one recorded command evidences `category`."""
    lowered = command.lower()
    if category == "reproduce_failure":
        return re.search(r"\b(pytest|unittest)\b", lowered) is not None
    if category == "read_history":
        return re.search(r"\bgit\s+(log|diff|show)\b", lowered) is not None
    if category == "enumerate_repository":
        return re.search(r"\b(ls|find|dir)\b", lowered) is not None
    if category == "inspect_toolchain":
        return re.search(r"(--version|\bwhich\b|--collect\b)", lowered) is not None
    if category == "read_documentation":
        return any(marker in lowered for marker in
                   ("readme", "requirements", "changelog", ".md", "docs/", "notes"))
    if category == "inspect_build_artifacts":
        return any(marker in lowered for marker in
                   ("__pycache__", ".pyc", "git stash", "git fsck"))
    if category == "probe_arithmetic":
        if re.search(r"\b(bc|expr)\b", lowered) is not None:
            return True
        if re.search(r"python\d*\s+-c\b", lowered) is not None:
            return any(op in command for op in ("+", "-", "*", "/", "%"))
        if re.search(r"\bnode\s+-e\b", lowered) is not None:
            return any(op in command for op in ("+", "-", "*", "/", "%"))
        return False
    return False


def _derive_exploration_categories(commands: list[str]) -> list[str]:
    """The categories evidenced by `commands`, derived rather than declared."""
    derived: set[str] = set()
    for command in commands:
        if not isinstance(command, str):
            continue
        for category in EXPLORATION_CATEGORIES:
            if _exploration_matches(category, command):
                derived.add(category)
    return sorted(derived)


def _require_exploration_derived(session: dict[str, Any], label: str) -> list[str]:
    """The declared set must equal the set derived from the verified actions.

    This is the third leg beside the closed-vocabulary check
    (`_session_exploration`) and the backing-action check
    (`_require_exploration_backing`): a declared-true category with no matching
    command fails, and a matching command with declared-false fails, each naming
    the category.
    """
    declared = session.get("exploration") or {}
    declared_true = sorted(name for name, value in declared.items() if value is True)
    commands = [action.get("command") for action in (session.get("tool_actions") or [])
                if isinstance(action, dict)]
    derived = _derive_exploration_categories([c for c in commands if isinstance(c, str)])
    if sorted(derived) != sorted(declared_true):
        missing = sorted(set(declared_true) - set(derived))
        extra = sorted(set(derived) - set(declared_true))
        parts = []
        if missing:
            parts.append(f"declared true with no matching command: {missing}")
        if extra:
            parts.append(f"matching command with declared false: {extra}")
        raise Failed(
            f"{label} declares exploration {sorted(declared_true)} but the verified "
            f"tool actions derive {sorted(derived)}: " + "; ".join(parts))
    return derived

#: What a completed run of one test in this repository's own suite prints. G01's
#: sessions have to *finish*: an answer that stops looking is not evidence that
#: recalling the lesson saved anything.
SUITE_SUCCESS_MARKERS = ("Ran 1 test", "OK")


class Blocked(Exception):
    """A precondition is missing. Carries the exact items, not a summary."""

    def __init__(self, *items: str) -> None:
        super().__init__("; ".join(items))
        self.items = list(items)


class Failed(Exception):
    """A precondition held and the assertion did not. A real FAIL."""


#: Words that make a nearby mention a denial rather than an assertion. Shared by the
#: two cases that check an agent did not claim something from its context: both were
#: written as bare substring tests and both rejected a correct answer that said "not X"
#: while forbidding X. An LLM judge would read the sentence properly but would not be
#: reproducible between runs, and an acceptance gate that changes its mind is worse than
#: one with a blunt documented rule.
#: Two kinds of word make a nearby mention not an assertion. Denials ("nothing
#: verified") say the opposite of the phrase; obligations and conditionals ("should be
#: verified before treating this as settled", "was confirmed by the gate") are about
#: what someone ought to do or what would count, not about what happened. Both were found
#: by a real session saying something correct.
_NEGATORS = ("not ", "n't", "never", "no ", "nothing", "none", "nobody", "cannot",
             "can't", "is not", "was not", "rather than", "instead of", "does not",
             "did not", "supersed", "earlier", "previously", "instead", "without",
             "if ", "unless", "zero",
             # obligations and conditionals -- a recommendation to verify is not a
             # claim that anything was verified
             "should", "must ", "ought", "need to", "needs to", "would need",
             "before ", "until ", "verify ", "confirm ", "check ", "reconcil")


def unnegated(text: str, phrase: str, window: int = 70):
    """Yield each mention of `phrase` in `text` that nothing nearby denies.

    Auxiliary only. This is a heuristic over words, and ordinary English can
    defeat a word list ("Before you change the API shape, you may freely...",
    "If you recall, mem0..."). Wherever a case's claim rests on what an answer
    *means*, the binding judgement is the independent `semantic_review`, bound
    by digest to the exact answer; this helper only keeps the blunt
    phrase-level guard beside it.

    Every check on what an Agent *said* goes through here. A bare `in` test cannot tell
    "there is no record" from "nothing verified a record", and across G02, G03, G04,
    G05 and G07 that mistake rejected five correct answers before this was noticed --
    every one of them a session that had understood the case perfectly.

    The phrase itself may contain a negation: G05 looks for "no memory", which is an
    assertion of absence, and the sentence is excused when something *else* denies it
    ("degraded does not mean no memory"). What matters is the outer denial, not the
    words inside the phrase. A phrase whose mention is never the problem -- G03's "no
    record", which is the answer it is asking for -- must simply not be searched for.

    "no memory" and "degraded does not mean no memory" both contain the phrase; only
    the second is honest, and a substring test cannot tell them apart. Each occurrence
    is therefore counted only when no negator precedes it inside the window.
    """
    lowered = text.lower()
    needle = phrase.lower()
    for match in re.finditer(re.escape(needle), lowered):
        before = lowered[max(0, match.start() - window):match.start()]
        if not any(word in before for word in _NEGATORS):
            yield text[max(0, match.start() - window):match.end() + 40]


#: An answer that says nothing was recorded has to say it in one of these ways. They
#: are phrases, not meanings: the point of `clauses()` below is that an answer which
#: denies the record *and* asserts an outcome must not be able to satisfy both of G03's
#: requirements at once, and the only way to arrange that is to require the denial and
#: the assertion to be in different clauses.
_ABSENCE_MARKERS = ("no record", "not recorded", "never recorded", "not found",
                    "no evidence", "nothing recorded", "not in the record", "no such",
                    "does not exist", "is absent", "not present", "no trace",
                    "nothing was recorded", "nothing on record", "no mention",
                    # A bare "never" is the honest form a real session reaches for --
                    # "it never ran", "it was never verified" -- and it is an absence
                    # of an execution, which is what this case is about.
                    "never")

#: The subset that may be masked out of a negation window. Only a denial of the
#: *record* belongs here: masking an outcome's own negation would manufacture the
#: fabrication it was meant to excuse, turning "it never passed" into a claim that it
#: did.
_ABSENCE_DENIALS = tuple(marker for marker in _ABSENCE_MARKERS if marker != "never")

#: G03's second requirement: an outcome claimed about something nothing recorded is a
#: fabrication, however carefully it is hedged.
_FABRICATIONS = ("passed", "verified", "confirmed", "succeeded", "all green")


def clauses(answer: str) -> list[str]:
    """The answer cut into clauses.

    Splitting at sentence and clause punctuation is what makes G03's two requirements
    checkable against each other. The answer "There is no record of it in Core; the
    device 42 protocol test passed every time it ran" satisfied both of them when they
    were independent scans over the whole text -- one found the denial, the other found
    an assertion, and neither asked whether the sentence was two sentences. A clause is
    where an answer makes one claim, so it is the smallest unit a claim can hide in.
    """
    return [part.strip() for part in re.split(r"[.;!?\n]+", answer) if part.strip()]


def is_absence_statement(clause: str) -> bool:
    lowered = clause.lower()
    return any(marker in lowered for marker in _ABSENCE_MARKERS)


#: Prefixes that make the word they are fused to a denial rather than an assertion.
#: "unverified" and "unconfirmed" are single words, not assertions containing a
#: verification.
_NEGATING_PREFIXES = frozenset({"un", "non", "in", "never", "no"})


def asserted_outcomes(clause: str, *, absence_blind: bool) -> list[tuple[str, str]]:
    """Every outcome phrase in `clause` that nothing nearby denies.

    Auxiliary only, like `unnegated`: a blunt phrase rule that cannot read
    meaning, kept beside the `semantic_review` binding judgement rather than
    standing in for it.

    The same rule as `unnegated`, with one exception. When the clause is itself an
    absence statement the absence is masked out of the negation window first: the denial
    is the claim being judged, so it cannot also vouch for the assertion sitting beside
    it. Without that, "there is no record of it and the test passed every time" excuses
    its own fabrication, which is exactly the sentence that has to fail.
    """
    scanned = clause
    if absence_blind:
        for marker in _ABSENCE_DENIALS:
            scanned = scanned.replace(marker, "-" * len(marker))
    found: list[tuple[str, str]] = []
    for phrase in _FABRICATIONS:
        for match in _word_matches(scanned, phrase):
            before = scanned.lower()[max(0, match.start() - 70):match.start()]
            if any(word in before for word in _NEGATORS):
                continue
            found.append((phrase, clause[max(0, match.start() - 70):match.end() + 40]))
    return found


def _word_matches(text: str, phrase: str):
    """Occurrences of `phrase` that are the whole word, not part of a longer one.

    A substring search finds "verified" inside "unverified" and "confirmed" inside
    "unconfirmed", so an honest answer that describes a block as *unverified* was
    reported as asserting verification. That is not a near miss: "NOT VERIFIED BY
    CONTEXT" is the label P3 puts on the section it did not verify, so the phrase is
    the vocabulary of this very system, and an agent repeating the label back was
    failed for it. The independent semantic reviewer had called that same answer
    consistent.

    So the match has to respect word boundaries on both sides, and a negating prefix
    fused to the word -- un-, non-, in-, un- -- counts as part of the word rather than
    as an assertion. Nothing here weakens the fabrication check: "the test was
    verified" and "unverified" are different words, and only the second is a denial.
    """
    for match in re.finditer(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text,
                             re.IGNORECASE):
        prefix = re.search(r"[a-z]+$", text[:match.start()].lower())
        if prefix is not None and prefix.group(0) in _NEGATING_PREFIXES:
            continue
        yield match


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
    sealed_artifacts: dict[str, Any] = field(default_factory=dict)
    rule_versions: list[str] = field(default_factory=list)
    state_revision_before: int | None = None
    state_revision_after: int | None = None
    engine_evidence: dict[str, Any] = field(default_factory=dict)
    inherited_evidence: dict[str, Any] = field(default_factory=dict)
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

    def task_title(self, task_id: str) -> str | None:
        """A Task's title, read back from Core rather than remembered by the case."""
        conn = self.conn()
        try:
            row = conn.execute("SELECT title FROM tasks WHERE task_id=?",
                               (task_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else row["title"]

    def prompt_text(self, event_id: str) -> str | None:
        """What the user was actually asked, read back from the committed Event.

        G01 compares two sessions, so the question is part of the experiment rather
        than a detail of it: the previous version asked one session "the device test
        fails; where should I look first?" and the other "the device test fails again;
        where should I look first?", and nothing checked that the two runs were asked
        the same thing.
        """
        conn = self.conn()
        try:
            row = conn.execute("SELECT payload_json FROM events WHERE event_id=?",
                               (event_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except ValueError:
            return None
        text = payload.get("text") if isinstance(payload, dict) else None
        return text if isinstance(text, str) else None

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

    def read_receipt(self, kind: str) -> dict[str, Any]:
        """Read a receipt written by an earlier phase and re-check its digest.

        The digest is recomputed from the bytes on disk rather than copied from the
        manifest that cited it, so a file edited between phases is caught here instead
        of being reported as verified.
        """
        path = self.root / f"{kind}.json"
        if not path.exists():
            raise Failed(f"receipt {kind!r} is missing at {path}; the phase that was "
                         f"supposed to write it did not run, or its output was removed")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise Failed(f"receipt {kind!r} at {path} is not readable JSON: {exc}")
        return {"path": str(path), "sha256": digest, "kind": kind, "write": "read-back",
                "payload": payload}

    def session_path(self, label: str) -> Path:
        return self.root / f"session-{label}.json"

    def _injection_record(self, label: str, pack: dict[str, Any]) -> dict[str, Any]:
        """Exactly which bytes were injected into this session, hashed and measured.

        A pack id is not enough. The same id can be rendered twice with different
        content, and nothing in the id says which rendering the agent read -- so the
        record names the digest of the bytes themselves, which is what makes "this agent
        saw this pack" checkable rather than plausible.
        """
        body = pack["rendered_content"].encode("utf-8")
        return {"label": label,
                "pack_path": pack.get("receipt", {}).get("path"),
                "pack_file_sha256": pack.get("receipt", {}).get("sha256"),
                "injected_bytes": len(body),
                "injected_sha256": hashlib.sha256(body).hexdigest(),
                "context_pack_id": pack["context_pack_id"],
                "sections": pack["sections"],
                "token_count": pack["token_count"]}

    def _seal_injected_pack(self, label: str, pack: dict[str, Any]) -> dict[str, Any]:
        """Copy the pack file that was injected, byte for byte, once.

        If a copy already exists and differs, the pack was re-rendered between phases.
        That is refused rather than overwritten: the sealed copy is the record of what
        this session was shown, and quietly replacing it would destroy the only evidence
        that the two phases agree.
        """
        receipt = pack.get("receipt") or {}
        source = receipt.get("path")
        if not source:
            return {"kind": f"injected-{label}-pack", "write": "no-file"}
        sealed = self.root / f"injected-{label}.pack.json"
        if sealed.exists():
            on_disk = hashlib.sha256(sealed.read_bytes()).hexdigest()
            if on_disk != receipt["sha256"]:
                raise Failed(
                    f"the pack sealed for {label} hashes to {on_disk[:12]}, not the "
                    f"{receipt['sha256'][:12]} this phase rendered. The pack changed "
                    f"between phases; a session cannot be verified against a pack that "
                    f"has been re-rendered.")
            return {"path": str(sealed), "sha256": on_disk,
                    "bytes": sealed.stat().st_size,
                    "kind": f"injected-{label}-pack", "write": "unchanged"}
        sealed.write_bytes(Path(source).read_bytes())
        os.chmod(sealed, 0o600)
        return {"path": str(sealed), "sha256": receipt["sha256"],
                "bytes": sealed.stat().st_size,
                "kind": f"injected-{label}-pack", "write": "sealed"}

    def verify_session(self, pack: dict[str, Any], label: str, *,
                       require_tool_actions: bool = True,
                       require_evidence_backed_actions: bool = False) -> dict[str, Any]:
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
            actually produced;
          * when `require_evidence_backed_actions` is true -- or whenever the
            receipt carries any tool action at all -- each action must also bind
            to a Core Evidence row produced by the real Evidence store
            (`verify_tool_action`): the row is looked up by
            `(task_id, tool_name, command_sha256)` on the pack's own task, and its
            stored command hash, output artifact hash, exit status and
            task/session binding must match what the session declared. A session
            can invent a command and an output; it cannot invent a Core row.
            The flag exists so cases whose sessions take no tool actions keep
            working unchanged; the presence of actions is itself the trigger, so
            a receipt that declares actions on any label is held to the same
            binding. A session that completed its task (`task_completed: true`)
            additionally has its `final_verification` command bound the same way.

        Returns the session dict with a `verified` block the cases assert on, including
        the sealed transcript digest and the injection record.
        """
        commit = self.git_commit()
        path = self.session_path(label)
        if not path.is_file():
            raise Blocked(f"no agent session receipt for {label} at {path}; run the agent "
                          f"session and write it there -- it cannot be synthesised here")
        raw = path.read_bytes()
        session = json.loads(raw.decode("utf-8"))
        problems: list[str] = []

        # Seal the bytes as received, before interpreting them. The session file is an
        # external input; without a sealed copy of the exact bytes, "the session said X"
        # is unfalsifiable once the input file is edited. The transcript is a
        # byte-for-byte copy, so the claim can be re-checked against it later.
        transcript = self.root / f"session-{label}.transcript.json"
        transcript_sha = hashlib.sha256(raw).hexdigest()
        if transcript.exists():
            on_disk = hashlib.sha256(transcript.read_bytes()).hexdigest()
            if on_disk != transcript_sha:
                problems.append(
                    f"the sealed transcript of {label} hashes to {on_disk[:12]} but the "
                    f"session file now hashes to {transcript_sha[:12]}; the input was "
                    f"edited after it was sealed")
        else:
            transcript.write_bytes(raw)
            os.chmod(transcript, 0o600)
        sealed_session = {"path": str(transcript), "sha256": transcript_sha,
                          "kind": f"session-{label}-transcript",
                          "bytes": len(raw), "write": "sealed"}

        # The injection record: exactly which bytes were injected, hashed and measured,
        # independent of anything the session says about them. A pack id is not enough --
        # the same id could have been rendered twice with different content.
        #
        # `_seal_injected_pack` raises *this module's* `Failed`, and the handler used to
        # name `errors.Failed` from `jasmine_core.errors` -- an attribute that does not
        # exist there. So a re-rendered pack, which is the one tampering this is here to
        # catch, surfaced as `AttributeError: module 'jasmine_core.errors' has no
        # attribute 'Failed'`: the operator saw a crash in the runner's own plumbing
        # instead of the message naming the pack that changed.
        injection = self._injection_record(label, pack)
        try:
            self._seal_injected_pack(label, pack)
        except Failed as failure:
            problems.append(str(failure))

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

        # The taskbook prohibition on judging by an agent's self-description: a
        # command string plus an output string, both written by the session
        # itself, prove nothing was run. Each declared action has to bind to a
        # Core Evidence row the real Evidence store produced, on the pack's own
        # task. The flag names the requirement; the presence of actions triggers
        # it on every label, so a receipt that declares actions cannot evade the
        # binding by running under a case that did not ask for it.
        task_binding = pack.get("task_id")
        if require_evidence_backed_actions or actions:
            if not isinstance(task_binding, str) or not task_binding:
                problems.append("the session declares tool actions but the pack names no "
                                "task they could be bound to, so no action can be "
                                "Evidence-backed")
            else:
                evidence_conn = self.conn()
                try:
                    for action in actions:
                        if not isinstance(action, dict) or not action.get("command"):
                            continue
                        try:
                            self._verify_tool_action_row(evidence_conn, action,
                                                         task_id=task_binding)
                        except Failed as failure:
                            problems.append(str(failure))
                    # A session that finished has a final verification whose output
                    # carries the suite markers `_session_completion` already
                    # checked. Markers in prose are the same self-report as the
                    # actions, so the command behind them is bound here as well.
                    if session.get("task_completed") is True:
                        final = session.get("final_verification")
                        if isinstance(final, dict) and isinstance(final.get("command"),
                                                                  str):
                            candidate = {"command": final["command"],
                                         "output": final.get("output", ""),
                                         "tool_name": final.get("tool_name") or "shell",
                                         "exit_code": final.get("exit_code", 0)}
                            try:
                                self._verify_tool_action_row(evidence_conn, candidate,
                                                             task_id=task_binding)
                            except Failed as failure:
                                problems.append(
                                    "the final verification action is not "
                                    f"Evidence-backed: {failure}")
                finally:
                    evidence_conn.close()

        if problems:
            raise Failed("the agent session receipt does not stand up: " + "; ".join(problems))
        # The sealed bytes are receipts too, so they land in the seal with everything
        # else and the FINAL manifest can name them.
        sealed = [sealed_session]
        if pack.get("receipt", {}).get("path"):
            injected_path = self.root / f"injected-{label}.pack.json"
            sealed.append({"path": str(injected_path),
                           "sha256": pack["receipt"]["sha256"],
                           "bytes": (injected_path.stat().st_size
                                     if injected_path.exists() else None),
                           "kind": f"injected-{label}-pack", "write": "sealed"})
        return {**session, "verified": {
            "commit": commit, "context_pack_id": pack["context_pack_id"],
            "pack_sha256": recomputed, "memory_carried": sorted(carried),
            "tool_actions": len(actions), "pack_receipt": pack["receipt"]["path"],
            "session_transcript": sealed_session,
            "injection": injection,
            "sealed": sealed}}

    def collect_tool_evidence(self, task_id: str, *, command: str, output: str,
                                exit_code: int = 0, tool_name: str = "shell",
                                step_id: str | None = None,
                                session_id: str | None = None) -> str:
        """Record one executed tool action through the real Evidence path.

        This is the harness side of the binding `verify_session` checks: it is
        called while the session is running, around a real execution, with the
        verbatim command, its verbatim output and its exit code -- never
        reconstructed afterwards from the agent's prose. The row is written by
        the real `EvidenceStore.record_tool_result` with the system actor
        (`gate.system`, kind `system`), the output goes to the gate workspace
        as a `workspace:/...` artifact so Core hashes the bytes themselves, and
        the identity of the capture is a pure function of its content: the
        `turn_id`/`tool_use_id` derive from a content hash (`case_id|task_id|
        command|output_sha256`), and the row is keyed on the full tuple
        `(task_id, tool_name, command_sha256, artifact_sha256)`. An identical
        repeat reuses the row found by that key; a repeat whose output differs
        is a different execution and gets its own row -- which is the normal
        shape of debugging (FAIL, then OK), and which an earlier revision
        refused, making any session that re-ran a command unverifiable. Two
        executions that produced different bytes are still never merged under
        one identity; they are simply never given one.
        """
        from jasmine_core.evidence import EvidenceStore
        from jasmine_core.state import StateStore

        if not isinstance(command, str) or not command:
            raise Failed("collect_tool_evidence needs the command that actually ran")
        if not isinstance(output, str):
            raise Failed("collect_tool_evidence needs the verbatim output as a string")
        tool_name = tool_name or "shell"
        command_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest()
        output_sha256 = hashlib.sha256(output.encode("utf-8")).hexdigest()
        conn = self.conn()
        try:
            state = StateStore(conn, schema_version=SCHEMA_VERSION)
            if step_id is None:
                existing = conn.execute(
                    "SELECT step_id FROM steps WHERE task_id=? ORDER BY created_at,"
                    "step_id LIMIT 1", (task_id,)).fetchone()
                if existing is not None:
                    step_id = existing["step_id"]
                else:
                    step_id = state.create_step(
                        task_id, {"title": "gate tool evidence",
                                  "host_id": self.host, "expected_revision": 1},
                        actor_id=self.human, actor_kind="human",
                        can_accept=False)["step"]["step_id"]
            artifact_name = f"tool-output-{command_sha256[:12]}.txt"
            artifact = self.workspace / artifact_name
            artifact.write_text(output, encoding="utf-8")
            reused = conn.execute(
                "SELECT evidence_id, artifact_sha256 FROM evidence WHERE task_id=? AND"
                " tool_name=? AND command_sha256=? AND artifact_sha256=?"
                " ORDER BY created_at, evidence_id LIMIT 1",
                (task_id, tool_name, command_sha256, output_sha256)).fetchone()
            if reused is not None:
                return reused["evidence_id"]
            seed = f"{self.case_id}|{task_id}|{command}|{output_sha256}"
            turn_id = "t-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
            tool_use_id = hashlib.sha256(f"{seed}|tool".encode("utf-8")).hexdigest()[:8]
            stored = EvidenceStore(conn, schema_version=SCHEMA_VERSION).record_tool_result(
                {"task_id": task_id, "step_id": step_id, "host_id": self.host,
                 "codex_session_id": self.case_id, "turn_id": turn_id,
                 "tool_use_id": tool_use_id, "tool_name": tool_name,
                 "tool_input": {"cmd": command},
                 "tool_response": {"exit_code": exit_code, "stdout": output},
                 **({"session_id": session_id} if session_id is not None else {}),
                 "artifact_uri": f"workspace:/{artifact_name}"},
                actor_id=self.system)
            evidence = stored.get("evidence") or stored
            return evidence.get("evidence_id") or evidence["evidence_id"]
        finally:
            conn.close()

    def verify_tool_action(self, action: dict[str, Any], *, task_id: str,
                           step_id: str | None = None) -> str:
        """Bind one session-declared tool action to its Core Evidence row.

        The row is looked up by the full content tuple `(task_id, tool_name,
        command_sha256, artifact_sha256)` -- the same key `collect_tool_evidence`
        field: the stored command hash must equal the declared command's hash,
        the stored output artifact hash must equal the declared output's bytes,
        the stored exit status must match the declared one (PASS for exit 0,
        FAIL otherwise, or an explicitly declared `status`), the recorded
        `task_id` must be the session's own task, and any `session_id`,
        `step_id` or `evidence_id` the action declares must match the row.
        Anything absent or mismatched fails naming the action. Returns the
        bound `evidence_id`.
        """
        conn = self.conn()
        try:
            return self._verify_tool_action_row(conn, action, task_id=task_id,
                                                step_id=step_id)
        finally:
            conn.close()

    def _verify_tool_action_row(self, conn: sqlite3.Connection,
                                action: dict[str, Any], *, task_id: str,
                                step_id: str | None = None) -> str:
        """The row-level half of `verify_tool_action`, on the caller's connection."""
        command = action.get("command") if isinstance(action, dict) else None
        if not isinstance(command, str) or not command:
            raise Failed(f"a tool action has no command: {action!r}")
        if "output" not in action or not isinstance(action.get("output"), str):
            raise Failed(f"the tool action {command!r} recorded no output to bind")
        output = action["output"]
        tool_name = action.get("tool_name") or "shell"
        command_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest()
        output_sha256 = hashlib.sha256(output.encode("utf-8")).hexdigest()
        if "status" in action and action["status"] is not None:
            declared_status = action["status"]
        else:
            exit_code = action.get("exit_code", 0)
            declared_status = "PASS" if exit_code == 0 else "FAIL"
        row = conn.execute(
            "SELECT evidence_id, command_sha256, artifact_sha256, status, task_id,"
            " step_id, session_id, tool_name FROM evidence WHERE task_id=? AND"
            " tool_name=? AND command_sha256=? AND artifact_sha256=?"
            " ORDER BY created_at, evidence_id LIMIT 1",
            (task_id, tool_name, command_sha256, output_sha256)).fetchone()
        if row is None:
            raise Failed(
                f"the tool action {command!r} binds to no Core Evidence: no row "
                f"for task {task_id} with tool {tool_name!r}, command hash "
                f"{command_sha256[:12]} and output hash {output_sha256[:12]}. An "
                f"action only the session describes is not an action that ran.")
        if row["command_sha256"] != command_sha256:
            raise Failed(
                f"the tool action {command!r} names a command whose hash "
                f"{command_sha256[:12]} differs from the stored row's "
                f"{(row['command_sha256'] or '')[:12]}")
        if row["artifact_sha256"] != output_sha256:
            raise Failed(
                f"the tool action {command!r} binds to Evidence {row['evidence_id']} "
                f"whose stored output hash {(row['artifact_sha256'] or '')[:12]} is "
                f"not the hash {output_sha256[:12]} of the output the session "
                f"declared: the bytes differ, so this row is not this execution.")
        if row["status"] != declared_status:
            raise Failed(
                f"the tool action {command!r} declares {declared_status} but its "
                f"Evidence {row['evidence_id']} records {row['status']}")
        if row["task_id"] != task_id:
            raise Failed(
                f"the tool action {command!r} is bound to task {row['task_id']}, not "
                f"the session's task {task_id}")
        wanted_step = step_id if step_id is not None else action.get("step_id")
        if wanted_step is not None and row["step_id"] != wanted_step:
            raise Failed(
                f"the tool action {command!r} declares step {wanted_step!r} but its "
                f"Evidence {row['evidence_id']} is bound to step {row['step_id']!r}")
        if action.get("session_id") is not None and row["session_id"] != action["session_id"]:
            raise Failed(
                f"the tool action {command!r} declares session {action['session_id']!r} "
                f"but its Evidence {row['evidence_id']} is bound to session "
                f"{row['session_id']!r}")
        if action.get("evidence_id") is not None and row["evidence_id"] != action["evidence_id"]:
            raise Failed(
                f"the tool action {command!r} declares Evidence {action['evidence_id']!r} "
                f"but binds to {row['evidence_id']}")
        return row["evidence_id"]

    def seal_session_artifacts(self, record: CaseRecord, session: dict[str, Any]) -> None:
        """Register a verified session's sealed bytes with the record.

        The transcript and the injected pack are the two artefacts a reviewer cannot
        re-derive from the manifest: the first is the external input as received, the
        second is what the agent was actually shown. Both are write-once files, and both
        have to reach the phase seal or the seal does not cover them.
        """
        verified = session.get("verified") or {}
        for artifact in verified.get("sealed") or []:
            record.sealed_artifacts[artifact["kind"]] = artifact

    def receipt(self, kind: str, payload: dict[str, Any]) -> dict[str, str]:
        """Write a receipt once. A receipt that changes is a broken evidence chain.

        This used to be an unconditional `write_text`, and that is how G06 lost its
        phase 1 evidence: the final phase re-ran the case's document read-back, wrote
        `g06-document.json` over the phase 1 bytes, and the phase 1 manifest went on
        citing a sha256 that no longer described any file on disk. Nothing failed. The
        manifest still said PASS.

        So a receipt is write-once. Re-writing identical bytes is allowed and returns
        the same digest, because a resumed case legitimately re-asserts what it already
        proved. Re-writing *different* bytes is refused: either the case is not
        idempotent, or something outside this process edited sealed evidence, and both
        are reasons to stop rather than to overwrite.
        """
        path = self.root / f"{kind}.json"
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if hashlib.sha256(existing.encode("utf-8")).hexdigest() == digest:
                return {"path": str(path), "sha256": digest, "kind": kind,
                        "bytes": path.stat().st_size, "write": "unchanged"}
            raise Failed(
                f"receipt {kind!r} already exists with different content "
                f"({hashlib.sha256(existing.encode('utf-8')).hexdigest()[:12]} on disk, "
                f"{digest[:12]} now). A receipt is written once: this case is not "
                f"idempotent, or sealed evidence was edited outside this process. "
                f"Refusing rather than overwriting.")
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o600)
        return {"path": str(path), "sha256": digest, "kind": kind,
                "bytes": path.stat().st_size, "write": "created"}

    def semantic_review(self, session: dict[str, Any], label: str) -> dict[str, Any]:
        """An independent reviewer's verdict on this exact answer, or a failure.

        The phrase heuristics in `_verify_g03_pack` are deliberately blunt, and a blunt
        rule is still a rule about words rather than about meaning: it cannot tell a
        denial of a record from a denial that carries a claim, and no arrangement of
        substrings makes it able to. G03's entire claim is about what an answer *says*,
        so the answer also has to be reviewed by somebody who was not the session.

        The review is bound to the answer by digest, so a verdict about a different
        answer cannot be carried over, and the reviewer and verdict are sealed with the
        transcript so the manifest names who said the answer was consistent.

        The findings must contain at least one verbatim quote (20+ characters) from
        the `final_answer` it reviews. This forces the reviewer to engage the actual
        text; it does not make the review unforgeable.
        """
        answer = session.get("final_answer")
        check(isinstance(answer, str) and bool(answer.strip()),
              f"{label} recorded no final answer, so there is nothing for a reviewer to "
              f"have reviewed")
        review = session.get("semantic_review")
        check(isinstance(review, dict),
              f"{label} carries no semantic_review block. A required input, not a "
              f"formality: {{reviewer, verdict, reviewed_answer_sha256, findings}} from "
              f"somebody other than the session.")
        absent = sorted({"reviewer", "verdict", "reviewed_answer_sha256", "findings"}
                        - set(review))
        check(not absent, f"{label}'s semantic_review is missing {absent}")
        digest = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        check(review["reviewed_answer_sha256"] == digest,
              f"{label}'s semantic review covers an answer hashing to "
              f"{str(review['reviewed_answer_sha256'])[:12]}, and the transcript's answer "
              f"hashes to {digest[:12]}: the review is attached to a different answer")
        check(review["verdict"] == "consistent",
              f"the independent reviewer returned {review['verdict']!r} for this answer, "
              f"so the absence and the fabrication cannot both be excused")
        check(isinstance(review["reviewer"], str) and bool(review["reviewer"].strip()),
              f"{label}'s semantic review names no reviewer")
        findings = review["findings"]
        check(isinstance(findings, str) and bool(findings.strip()),
              f"{label}'s semantic review records no findings")
        quoted = False
        if len(answer) >= 20:
            for start in range(len(answer) - 19):
                if answer[start:start + 20] in findings:
                    quoted = True
                    break
        check(quoted,
              f"{label}'s semantic review quotes none of the answer it reviews: "
              f"findings must contain a verbatim 20+ character quote from the "
              f"final answer")
        return {"reviewer": review["reviewer"], "verdict": review["verdict"],
                "reviewed_answer_sha256": digest, "findings": review["findings"]}

    def retired_premise(self) -> dict[str, Any]:
        """What G07 retired, re-derived from Core rather than read from a sidecar.

        `g07-retired.json` used to be written with a bare `write_text` beside the
        receipts and read back on every resume, and it was in neither seal. Replacing its
        `memory_id` with an id that does not exist and its `text` with nonsense left G07
        reporting PASS: the "obsolete means not injected" assertions were tested against
        whatever the sidecar said, so the premise of the case was a file nobody could
        check. The identity is sealed and write-once now, and it is re-derived here -- the
        item has to exist, be in a retired state, and belong to the project the pack is
        for, or the file and the database disagree and the case fails.
        """
        sealed = self.read_receipt("g07-retired")
        payload = sealed["payload"]
        check(isinstance(payload, dict),
              f"the sealed retired-memory receipt is not an object: {payload!r}")
        memory_id = payload.get("memory_id")
        check(isinstance(memory_id, str) and memory_id.startswith("mem_"),
              f"the sealed retired-memory receipt names {memory_id!r}, which is not a "
              f"Memory id")
        conn = self.conn()
        try:
            item = MemoryStore(conn, schema_version=SCHEMA_VERSION).item(memory_id)
        finally:
            conn.close()
        check(item is not None,
              f"the sealed receipt claims {memory_id} is the memory this case retired, "
              f"and Core has no such item: the file and the database disagree about the "
              f"premise of the whole case")
        check(item["status"] in ("OBSOLETE", "DELETED"),
              f"the memory this case retired is {item['status']}, so it was never "
              f"excluded from recall and 'obsolete means not injected' had no premise")
        check(item["project_id"] == payload.get("project_id"),
              f"the sealed receipt puts the retired memory in project "
              f"{payload.get('project_id')!r} and Core has it in {item['project_id']!r}")
        core_text = item.get("sanitized_text") or item.get("content") or None
        if not isinstance(core_text, str) or not core_text:
            core_text = None
        return {**payload, "derivation": {"memory_id": item["memory_id"],
                                          "status": item["status"],
                                          "engine_state": item["engine_state"],
                                          "project_id": item["project_id"]},
                "core_text": core_text,
                "receipt_path": sealed["path"], "receipt_sha256": sealed["sha256"]}


# -- the cases ----------------------------------------------------------------


def _memory_section_split(rendered: str) -> tuple[str, str]:
    """The pack's Truth material and its Memory material, split on P3's marker.

    The marker is matched rather than spelled out because P3 renders it with an em dash,
    and a needle that does not match its own renderer's punctuation splits the pack at
    the wrong place -- or nowhere, which is the failure this exists to make impossible.
    """
    match = re.search(r"^MEMORY[^\n]*NON AUTHORITATIVE$", rendered, re.M)
    if match is None:
        raise Failed("the rendered pack has no MEMORY section marker, so its Memory "
                     "material cannot be told apart from its Truth")
    return rendered[:match.start()], rendered[match.start():]


def _without_task_identity(rendered: str, pack: dict[str, Any]) -> str:
    """The pack's own Task and prompt ids replaced by placeholders.

    Those two values *cannot* match across the two sessions: P3 refuses an idempotency
    key replayed with a different request body, so the baseline cannot be the same Task
    as the lesson-bearing session. Everything else in the render has to match, and these
    are the only two places a difference is allowed.
    """
    for name in ("task_id", "prompt_event_id"):
        value = pack.get(name)
        if isinstance(value, str) and value:
            rendered = rendered.replace(value, f"<{name}>")
    return rendered


def _first_difference(baseline: str, lesson: str) -> str:
    """Where two strings part company, in words rather than as a bare offset."""
    limit = min(len(baseline), len(lesson))
    index = next((i for i in range(limit) if baseline[i] != lesson[i]), limit)
    start = max(0, index - 40)
    return (f"at character {index}, baseline {baseline[start:index + 40]!r} against "
            f"lesson-bearing {lesson[start:index + 40]!r}")


def _history_data_blocks(rendered: str) -> list[dict[str, Any]]:
    """The Memory data records the pack rendered, parsed back out of the bytes.

    Each block after the `HISTORICAL_NONAUTHORITATIVE MEMORY DATA` marker is
    one JSON record. The agent reads these bytes, so these bytes are what the
    actionable-history checks below are asserted on -- not the pack's summary
    fields about itself.
    """
    marker = "HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n"
    blocks: list[dict[str, Any]] = []
    for chunk in rendered.split(marker)[1:]:
        line, _, _ = chunk.partition("\n")
        try:
            record = json.loads(line)
        except ValueError:
            raise Failed(f"a rendered Memory data block is not readable JSON: {line[:200]!r}")
        if not isinstance(record, dict):
            raise Failed("a rendered Memory data block is not an object")
        blocks.append(record)
    return blocks


def _evidence_command(gate: Gate, evidence_id: str) -> str | None:
    """The verbatim command behind an Evidence row, from Core's own Events.

    The row keeps the command's hash; the words are in the `tool.call` Event
    the trusted producer wrote. Returns `None` when no such Event names one.
    """
    conn = gate.conn()
    try:
        row = conn.execute("SELECT task_id, turn_id, tool_use_id, tool_name FROM evidence"
                           " WHERE evidence_id=?", (evidence_id,)).fetchone()
        if row is None:
            return None
        events = conn.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND event_type='tool.call'"
            " ORDER BY seq", (row["task_id"],)).fetchall()
    finally:
        conn.close()
    fallback: str | None = None
    for event in events:
        try:
            payload = json.loads(event["payload_json"])
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        tool_input = payload.get("tool_input")
        command = (tool_input.get("cmd", tool_input.get("command"))
                   if isinstance(tool_input, dict) else None)
        if not isinstance(command, str) or not command:
            continue
        if (payload.get("turn_id") == row["turn_id"]
                and payload.get("tool_use_id") == row["tool_use_id"]):
            return command
        if fallback is None and payload.get("tool_name") == row["tool_name"]:
            fallback = command
    return fallback


def _evidence_command_sha256(gate: Gate, evidence_id: str) -> str | None:
    """The stored `command_sha256` behind an Evidence row, or `None` if no row.

    This is the same hash the Evidence-backed action check uses. Comparing the
    rendered command's hash against it binds the rendered text to the stored
    bytes rather than to a re-derived string.
    """
    conn = gate.conn()
    try:
        row = conn.execute("SELECT command_sha256 FROM evidence WHERE evidence_id=?",
                           (evidence_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return row["command_sha256"]


def _verify_g01_actionable_history(pack: dict[str, Any], *,
                                   resolve_command=None,
                                   resolve_command_sha256=None) -> None:
    """The lesson must arrive as sourced, actionable history -- and only there.

    In addition to the Core-derived fact, each rendered Memory record must name
    the failing command, the subject (the step and task that failed) and the
    source pointers (`memory_id`, `evidence_ids`, `source_event_ids`), all of
    which Core already holds, so rendering them is attribution rather than
    invention. When `resolve_command` is given -- the live path, where a Core
    database backs the pack -- the rendered command text must equal the
    `tool_input.cmd` stored on the underlying Evidence row; a pack whose
    actionable text disagrees with Core is refused. And no history material may
    appear anywhere outside the labelled non-authoritative block: the Truth
    sections above the marker must not carry the command, the memory id or the
    fact text, or history would read as current Truth.

    The command-equality check alone is not enough: `_evidence_command` and the
    worker's `_command_for` share one fallback algorithm, so a wrong-but-consistent
    command passes on both sides. The rendered text is therefore additionally bound
    to the stored bytes: `sha256(rendered command text)` must equal the Evidence
    row's `command_sha256` (the same hash the Evidence-backed action check uses),
    resolved through `resolve_command_sha256`. If no Evidence row exists for the
    memory, the history is refused rather than fallen back.
    """
    rendered = pack["rendered_content"]
    truth, memory = _memory_section_split(rendered)
    blocks = _history_data_blocks(rendered)
    carried = {item["memory_id"]: item for item in pack.get("memory_selected") or []}
    check(bool(blocks),
          "the lesson-bearing pack rendered no Memory data block, so it carries no "
          "actionable history a session could use")
    for record in blocks:
        memory_id = record.get("memory_id")
        check(isinstance(memory_id, str) and memory_id in carried,
              f"a rendered Memory record names {memory_id!r}, which this pack did "
              f"not select: {sorted(carried)}")
        text = record.get("text")
        check(isinstance(text, str) and text.startswith("history:"),
              f"the rendered Memory record for {memory_id} carries no history line, "
              f"so a session reading it learns nothing actionable")
        match = re.search(r"from `([^`]+)`", text)
        check(match is not None,
              f"the history for {memory_id} names no failing command, so there is "
              f"nothing for a later session to run first")
        command = match.group(1)
        subject = (pack.get("memory_selected") or [{}])[0].get("source_task_id")
        check((isinstance(subject, str) and subject and f"task {subject}" in text)
              or "task tsk_" in text,
              f"the history for {memory_id} names no subject task that failed")
        for pointer in [memory_id, *(record.get("evidence_ids") or []),
                        *(record.get("source_event_ids") or [])]:
            check(isinstance(pointer, str) and pointer in text,
                  f"the history for {memory_id} does not cite its source pointer "
                  f"{pointer!r}")
        if resolve_command is not None:
            for evidence_id in record.get("evidence_ids") or []:
                stored = resolve_command(evidence_id)
                check(stored is not None,
                      f"Core holds no command behind Evidence {evidence_id}, so the "
                      f"history for {memory_id} cannot be re-derived")
                check(stored == command,
                      f"the pack renders the failing command as {command!r} but Core "
                      f"records {stored!r} on Evidence {evidence_id}: the actionable "
                      f"text and Core disagree")
                if resolve_command_sha256 is not None:
                    stored_hash = resolve_command_sha256(evidence_id)
                    check(stored_hash is not None,
                          f"Core holds no Evidence row {evidence_id} behind the history "
                          f"for {memory_id}, so the rendered command cannot be bound "
                          f"to stored bytes")
                    rendered_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
                    check(rendered_hash == stored_hash,
                          f"the pack renders the failing command as {command!r} "
                          f"(hash {rendered_hash[:12]}) but the Evidence row "
                          f"{evidence_id} stores command hash "
                          f"{(stored_hash or '')[:12]}: the rendered text is not "
                          f"the stored bytes")
        # History outside the labelled block would read as current Truth.
        check(command not in truth,
              f"the failing command {command!r} also appears in the pack's Truth "
              f"sections: history rendered as Truth is an oracle, not a lesson")
        check(memory_id not in truth,
              f"the memory {memory_id} also appears in the pack's Truth sections")
    _ = memory


def _require_only_memory_differs(pack: dict[str, Any],
                                 baseline_pack: dict[str, Any]) -> None:
    """Fail unless the two rendered packs differ in exactly one place: the Memory.

    This is what makes "the only variable was the lesson" a fact rather than a claim.
    The check did not exist: the sessions were asked different sentences, the packs were
    rendered for different Tasks, and the only assertion on the difference between them
    was that the two sessions' tool commands were not equal. So a behavioural difference
    had at least two available explanations and the Gate could not tell which one it was
    measuring.

    One limit worth stating: P3 appends its *optional* Evidence bodies and historical
    note after the Memory header, so anything of that kind is inside the "Memory" span
    and would not be compared here. G01's two Tasks are new and carry no Evidence, and
    the two checks below require the lesson side to render a Memory data block and the
    baseline to render none, so a pack cannot hide a difference by moving material into
    that span.
    """
    truth, memory = _memory_section_split(pack["rendered_content"])
    base_truth, base_memory = _memory_section_split(baseline_pack["rendered_content"])
    lesson_truth = _without_task_identity(truth, pack)
    baseline_truth = _without_task_identity(base_truth, baseline_pack)
    if lesson_truth != baseline_truth:
        raise Failed(
            "the two packs differ outside the Memory section, so the lesson was not the "
            f"only variable between these sessions: {_first_difference(baseline_truth, lesson_truth)}")
    if memory == base_memory:
        raise Failed("the lesson-bearing pack's Memory section is byte-identical to the "
                     "baseline's, so nothing was injected and there is no experiment here")
    check("HISTORICAL_NONAUTHORITATIVE MEMORY DATA" not in base_memory,
          f"the baseline pack rendered a Memory data block while its Memory section "
          f"reports empty: {base_memory[:200]!r}")
    check("HISTORICAL_NONAUTHORITATIVE MEMORY DATA" in memory,
          f"the lesson-bearing pack reports Memory but rendered no data block: "
          f"{memory[:200]!r}")


def _session_exploration(session: dict[str, Any], label: str) -> list[str]:
    """The exploration categories this session says it performed.

    A closed vocabulary, declared by the session and checked against the list declared
    in this module. The differential used to be a set difference over the free-text
    command strings of the two sessions, which made "did less work" a claim about
    wording -- a rewording of the same action read as an investigation the lesson side
    skipped -- and made taking one action against eleven read as a benefit.
    """
    declared = session.get("exploration")
    check(isinstance(declared, dict),
          f"{label} declared no exploration set, so there is nothing to compare: both "
          f"sessions must say, for each of {list(EXPLORATION_CATEGORIES)}, whether they "
          f"did that")
    unknown = sorted(set(declared) - set(EXPLORATION_CATEGORIES))
    check(not unknown,
          f"{label} declared exploration categories outside the closed set: {unknown}. A "
          f"category invented for this run is a way to report a saving nobody measured.")
    missing = sorted(set(EXPLORATION_CATEGORIES) - set(declared))
    check(not missing, f"{label} did not say whether it performed {missing}")
    wrong = sorted(name for name, value in declared.items() if not isinstance(value, bool))
    check(not wrong, f"{label} declared non-boolean exploration values for {wrong}")
    return sorted(name for name, value in declared.items() if value is True)


def _require_exploration_backing(session: dict[str, Any], label: str) -> dict[str, list[str]]:
    """Every performed category must name the tool action(s) evidencing it.

    The vocabulary check (`_session_exploration`) says which categories the
    session claims; this says what it did for each of them. A category declared
    `true` with no backing action is a claim about work nothing records, and an
    action named for a category that is not among the session's own recorded
    `tool_actions` is a reference to work the receipt does not contain. Both
    fail naming the category. Each named action must additionally pass the
    Evidence cross-check in `verify_session`, which is what makes the backing
    more than a second self-report.
    """
    declared = session.get("exploration") or {}
    backing = session.get("exploration_actions")
    check(isinstance(backing, dict),
          f"{label} names no exploration_actions mapping, so no performed category "
          f"is evidenced by a tool action: each category declared true must name "
          f"the recorded action(s) behind it")
    unknown = sorted(set(backing) - set(EXPLORATION_CATEGORIES))
    check(not unknown,
          f"{label} evidences categories outside the closed set: {unknown}")
    commands = [action.get("command") for action in (session.get("tool_actions") or [])
                if isinstance(action, dict)]
    performed = sorted(name for name, value in declared.items() if value is True)
    for name in performed:
        cited = backing.get(name)
        check(isinstance(cited, list) and bool(cited),
              f"{label} claims it performed {name!r} but names no tool action "
              f"evidencing it; a category without a backing action cannot count")
        for command in cited:
            check(command in commands,
                  f"{label} claims {name!r} is evidenced by {command!r}, which is "
                  f"not one of its recorded tool actions {commands}")
    return {name: list(backing[name]) for name in performed}


def _session_completion(session: dict[str, Any], label: str) -> dict[str, Any]:
    """Proof that this session finished the task, rather than that it stopped looking.

    The benefit G01 reports is a differential in exploration, so it is only meaningful
    between two sessions that both reached the end. Without this, a session that took
    one action and gave up scored better than one that took eleven, and "the lesson
    saved investigation" was satisfied by not finishing. The output is hashed into the
    receipt rather than quoted, so what was checked is what is recorded.
    """
    check(session.get("task_completed") is True,
          f"{label} did not declare task_completed: true. A differential against a "
          f"session that did not finish measures nothing: not completing the task "
          f"cannot be reported as a benefit.")
    final = session.get("final_verification")
    check(isinstance(final, dict) and isinstance(final.get("command"), str)
          and bool(final["command"]) and isinstance(final.get("output"), str),
          f"{label} recorded no final verification command with its verbatim output")
    output = final["output"]
    absent = [marker for marker in SUITE_SUCCESS_MARKERS if marker not in output]
    check(not absent,
          f"{label}'s verification output does not carry {absent}, so the task was "
          f"never shown to pass and the differential cannot count: {output[-300:]!r}")
    return {"command": final["command"],
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "markers": list(SUITE_SUCCESS_MARKERS)}


def _pack_header(rendered: str) -> dict[str, Any]:
    """P3's HEADER line, parsed back out of the bytes an agent would read."""
    _, _, rest = rendered.partition("\n")
    line, _, _ = rest.partition("\n")
    try:
        header = json.loads(line)
    except ValueError:
        raise Failed(f"the rendered pack has no readable HEADER line: {line[:200]!r}")
    check(isinstance(header, dict), f"the rendered pack's HEADER is not an object: {line[:200]!r}")
    return header


def _pack_authority_rules(rendered: str) -> list[dict[str, Any]]:
    """The rules the AUTHORITY section rendered, parsed back out of the text.

    The scope has to come from the pack, not from the row the Gate happens to hold: the
    pack is what was injected, so the pack is what is checked.
    """
    _, _, rest = rendered.partition("AUTHORITY\n")
    body, _, _ = rest.partition("\nCURRENT STATE")
    rules = []
    for line in body.splitlines():
        if not line.startswith("{"):
            continue
        try:
            rule = json.loads(line)
        except ValueError:
            continue
        if isinstance(rule, dict) and isinstance(rule.get("scope"), dict):
            rules.append(rule)
    return rules


def case_g01(gate: Gate, record: CaseRecord) -> None:
    """A historical failure is recalled in a new session and changes what the agent does."""
    gate.require_real_agent()
    gate.require_bank_namespace()
    key, baseline_key = "g01-with-lesson", "g01-no-lesson"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g01_packs(gate, record, resumed, gate.existing_pack(baseline_key))
    project = gate.seed_project("g01")
    record.project_ids.append(project)

    # The lesson must belong to a *historical* task. P3 filters
    # `duplicate_current_fact`, so a lesson derived from Evidence the current turn
    # already carries is correctly not injected -- the scenario only means anything
    # when the new task does not already have that Evidence, which is what "a later
    # session hits the same problem" means.
    # The baseline's pack is built FIRST, while the project has no lesson yet. It is a
    # real P3 Context Pack like any other, and its Memory section comes back empty --
    # which is the point: the two sessions must differ because of what was injected, not
    # because one of them was handed a description of a pack. Previously the baseline ran
    # with no pack at all, so "the lesson changed what it did" had a free variable in it.
    # Both sides are asked the *same question*, in the same words, about a Task of the
    # same title. They used to be asked "where should I look first?" and "where should I
    # look first?" after "fails again", and the only assertion on the difference between
    # the two runs was that their tool commands were not equal -- so the wording was a
    # second explanation for any difference, and nothing in this Gate could rule it out.
    # The ids cannot match (P3 refuses an idempotency key replayed with a different
    # body); the prompt text and the title have to, and are checked in the verifier.
    baseline_task = gate.new_task(project, G01_TASK_TITLE)
    baseline_prompt = gate.event(project, "user.prompt", G01_PROMPT, task_id=baseline_task)
    record.source_event_ids.append(baseline_prompt)
    baseline_pack = gate.build_pack(project_id=project, task_id=baseline_task,
                                     prompt_event_id=baseline_prompt, key=baseline_key)

    historical = gate.new_task(project, "device test (historical)")
    job_id, document_id, source_event_id = gate.retain_lesson(
        project, reason="callback task_id did not match the task id in the request",
        task_id=historical)
    record.job_ids.append(job_id)
    record.document_ids.append(document_id)
    record.source_event_ids.append(source_event_id)

    current = gate.new_task(project, G01_TASK_TITLE)
    prompt = gate.event(project, "user.prompt", G01_PROMPT, task_id=current)
    record.source_event_ids.append(prompt)

    pack = gate.build_pack(project_id=project, task_id=current, prompt_event_id=prompt,
                           key=key)
    _verify_g01_packs(gate, record, pack, baseline_pack)


def _verify_g01_packs(gate: Gate, record: CaseRecord, pack: dict[str, Any],
                      baseline_pack: dict[str, Any] | None) -> None:
    """Everything G01 asserts about the two packs and the two sessions that read them.

    The baseline is a first-class session against a real, honestly empty pack -- not a
    nested summary inside the lesson's receipt. Each has its own sealed transcript and
    its own injection record, so "the lesson changed what the agent did" rests on two
    comparable, independently checkable runs rather than on one run's self-description.

    A controlled experiment needs its controls, so this is where they are asserted: the
    two Tasks carry the same title, both sessions were asked the same question, and the
    two rendered packs are required to be identical outside the Memory section. The
    benefit is then defined in advance -- at least one category from a closed vocabulary
    that the baseline performed and the lesson side did not, with both sides having
    finished the task. Doing fewer steps, rewording an action, or not finishing are not
    benefit and cannot be reported as it.
    """
    check(baseline_pack is not None,
          "this run has the lesson-bearing pack but not the no-lesson baseline pack "
          "built beside it; the differential cannot be established from one side")
    record.context_pack_ids.append(baseline_pack["context_pack_id"])
    check(baseline_pack["memory_status"] == "empty",
          f"the baseline pack's Memory is {baseline_pack['memory_status']!r}, not empty: "
          f"it is supposed to be the same project before the lesson existed, so a "
          f"non-empty Memory means the comparison is not the one it claims to be")
    check(baseline_pack["memory_selected"] == [] and baseline_pack["memory_filtered"] == [],
          f"the baseline pack carried memory: {baseline_pack['memory_selected']}")
    check(baseline_pack["task_id"] != pack["task_id"],
          "the baseline and lesson-bearing sessions were given the same task, so the "
          "only thing that could differ is the pack")
    # The same question, asked in the same words. Read back from Core rather than
    # remembered by the case, because "the same question" is part of the claim.
    baseline_question = gate.prompt_text(baseline_pack["prompt_event_id"])
    lesson_question = gate.prompt_text(pack["prompt_event_id"])
    check(isinstance(baseline_question, str) and baseline_question == lesson_question,
          f"the two sessions were asked different questions, so the comparison is "
          f"between two conversations: baseline {baseline_question!r} against "
          f"lesson-bearing {lesson_question!r}")
    baseline_title = gate.task_title(baseline_pack["task_id"])
    lesson_title = gate.task_title(pack["task_id"])
    check(isinstance(baseline_title, str) and baseline_title == lesson_title,
          f"the two sessions were given differently titled Tasks ({baseline_title!r} "
          f"against {lesson_title!r}), so the difference between them is not only the "
          f"Memory")
    record.receipts.append(gate.receipt("g01-baseline-pack",
                                        {k: v for k, v in baseline_pack.items()
                                         if k != "rendered_content"}))
    baseline = gate.verify_session(baseline_pack, "g01-baseline",
                                     require_evidence_backed_actions=True)
    baseline["verified"]["exploration"] = _session_exploration(baseline, "the baseline session")
    baseline["verified"]["exploration_backing"] = _require_exploration_backing(
        baseline, "the baseline session")
    baseline["verified"]["exploration_derived"] = _require_exploration_derived(
        baseline, "the baseline session")
    baseline["verified"]["completion"] = _session_completion(baseline, "the baseline session")
    gate.seal_session_artifacts(record, baseline)
    record.receipts.append(gate.receipt("g01-baseline-session", baseline["verified"]))
    check(baseline["verified"]["memory_carried"] == [],
          f"the baseline session was handed memory {baseline['verified']['memory_carried']!r} "
          f"despite the pack being empty")

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

    # ...and the lesson is the only variable between the two packs. This is asserted on
    # the rendered bytes rather than claimed: anything else that differs between them is
    # a second explanation for whatever the sessions did.
    _require_only_memory_differs(pack, baseline_pack)

    # ...and the lesson must be usable, not merely traceable. The pack renders
    # the Core-derived fact together with the failing command, the subject and
    # the source pointers, inside the labelled non-authoritative block and
    # nowhere else. On a live Gate the rendered command is re-derived from the
    # Evidence row behind it; a scripted pack with no Core behind it is held to
    # everything except that re-derivation. After the controls above, so a
    # pack that was never a controlled experiment is refused as that first.
    _verify_g01_actionable_history(
        pack, resolve_command=((lambda evidence_id: _evidence_command(gate, evidence_id))
                               if hasattr(gate, "conn") else None),
        resolve_command_sha256=((lambda evidence_id: _evidence_command_sha256(
            gate, evidence_id)) if hasattr(gate, "conn") else None))

    session = gate.verify_session(pack, "g01-lesson",
                                  require_evidence_backed_actions=True)
    session["verified"]["exploration"] = _session_exploration(session, "the lesson-bearing session")
    session["verified"]["exploration_backing"] = _require_exploration_backing(
        session, "the lesson-bearing session")
    session["verified"]["exploration_derived"] = _require_exploration_derived(
        session, "the lesson-bearing session")
    session["verified"]["completion"] = _session_completion(session, "the lesson-bearing session")
    gate.seal_session_artifacts(record, session)
    record.receipts.append(gate.receipt("g01-session", session["verified"]))
    check(bool(session["verified"]["memory_carried"]),
          "the session was verified against a pack that carried no Memory")

    # VC-16 asks what the lesson *did*, and the taskbook is explicit that the agent's
    # own account of being helped is not the evidence. So the claim is differential and
    # checkable here: the same question, the same repository, two real sessions that
    # both finished the task, and the lesson-bearing one must have skipped at least one
    # step from a vocabulary fixed in advance. What P3's pack can deliver is recorded,
    # not imagined: by design it renders the Core-derived *fact* together with the
    # failing command, the subject and the source pointers, and never provider
    # prose, so the lesson reaches the agent as "this project recorded a FAIL result for
    # this suite before, here is the command that failed, and here are the Events behind
    # it" -- not as a diagnosis. The
    # count of tool commands is deliberately not the claim: the commands are in the
    # sealed transcripts the manifest names, and a saving is a named category that was
    # not performed, not a shorter list of strings.
    baseline_categories = baseline["verified"]["exploration"]
    lesson_categories = session["verified"]["exploration"]
    omitted = sorted(set(baseline_categories) - set(lesson_categories))
    baseline_actions = [a.get("command") for a in (baseline.get("tool_actions") or [])]
    lesson_actions = [a.get("command") for a in (session.get("tool_actions") or [])]
    record.engine_evidence["differential"] = {
        "vocabulary": list(EXPLORATION_CATEGORIES),
        "baseline_exploration": baseline_categories,
        "lesson_exploration": lesson_categories,
        "omitted_categories": omitted,
        "baseline_completion": baseline["verified"]["completion"],
        "lesson_completion": session["verified"]["completion"],
        "baseline_tool_action_count": len(baseline_actions),
        "lesson_tool_action_count": len(lesson_actions),
        "baseline_session": baseline["verified"]["session_transcript"],
        "lesson_session": session["verified"]["session_transcript"],
        "baseline_injection": baseline["verified"]["injection"],
        "lesson_injection": session["verified"]["injection"],
        "note": "P3 renders the Core-derived fact together with the failing "
                "command, the subject and the source pointers, and never provider "
                "prose, so the lesson arrives as a sourced, actionable prior "
                "failure rather than as a diagnosis. "
                "Benefit is a predefined exploration category the baseline performed and "
                "the lesson side did not, with both sessions having completed the task; "
                "fewer tool commands is not the claim, and the commands themselves are in "
                "the sealed transcripts."}
    # Both sides reached the end before either of them counts, so a session that gave up
    # cannot be reported as having been saved anything: `_session_completion` has already
    # refused it, and this names the consequence.
    check(bool(omitted),
          f"the lesson-bearing session performed every exploration category the baseline "
          f"did ({sorted(set(baseline_categories))}), so the pack changed nothing about "
          f"how it investigated; both sides finished, so this is not a difference in "
          f"outcome either")
    check(baseline.get("memory_seen") in (None, []),
          f"the baseline session claims to have seen memory {baseline.get('memory_seen')!r} "
          f"while its pack carried none")
    cited = session.get("memory_seen") or []
    check(bool(cited), "the session recorded no memory as seen, so it cannot have used it")
    check(cited[0].get("memory_id") == carried["memory_id"],
          f"the session saw {cited[0].get('memory_id')!r}, the pack carried "
          f"{carried['memory_id']!r}")
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
    # It must reach *a* cause, and it must attribute the lesson to the task the lesson
    # came from. The first version required the literal string "task_id", which is a
    # field name and not an id: a session that correctly wrote the recalled task's id
    # -- tsk_... -- failed the check, while one that wrote "task_id" in passing would
    # have passed without citing anything. The id from the pack is the thing that has to
    # appear.
    lesson_task = (pack["memory_selected"][0].get("source_task_id") or "") if \
        pack["memory_selected"] else ""
    check(bool(lesson_task) and lesson_task in answer,
          f"the answer does not name the task the lesson came from ({lesson_task!r}), so "
          f"the recalled lesson was not tied to its own provenance: {answer[:300]!r}")


def case_g02(gate: Gate, record: CaseRecord) -> None:
    """The current Truth wins over an older remembered Decision (VC-14/VC-15)."""
    gate.require_real_agent()
    gate.require_bank_namespace()
    gate.require_p3_pack()
    key = "g02-current-decision"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g02_pack(gate, record, resumed)
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
    #
    # Stated honestly, because it cannot be asserted here. `filter_memory` only reports
    # items the *Core* refused; a superseded Decision is dropped by the recall itself,
    # so it never reaches the pack and the pack's `filtered` list cannot contain one. The
    # previous form was `not filtered or any(...)`, which is true of an empty list -- and
    # the list is always empty -- so it passed on every run while the manifest implied a
    # filter had been seen. It now records whether the premise held, and the resumed
    # phase re-asserts the filter itself from the sealed `g02-recall` receipt, whose
    # `filtered` really does name the superseded version.
    superseded_in_pack = [row for row in pack["memory_filtered"]
                          if row.get("reason") == "superseded_decision"]
    other_filtered = [row for row in pack["memory_filtered"]
                      if row.get("reason") != "superseded_decision"]
    check(not other_filtered,
          f"the pack filtered something for a reason other than supersession: "
          f"{other_filtered}")
    record.engine_evidence["supersession_observed_in_pack"] = bool(superseded_in_pack)
    record.engine_evidence["supersession_premise"] = (
        "observed at the pack: its filtered list names the superseded Decision"
        if superseded_in_pack else
        "not observed at the pack, and that is the expected shape: the recall behind "
        "this pack filtered the superseded version before rendering, so `filtered` is "
        "empty by design rather than by omission. The filter is asserted against the "
        "recall outcome recorded in the sealed g02-recall receipt, which is where the "
        "superseded version actually appears.")

    # 5. The premise, required rather than recorded: the sealed `g02-recall`
    # receipt must show the superseded Decision filtered for exactly
    # `superseded_decision`. Recording "observed or not" above is honest about
    # where the filter appears; but a case whose recall never filtered the old
    # version has no premise -- "the current Truth wins" would be a claim about
    # a contest that never happened -- so it fails here with the premise named.
    recall_sealed = gate.read_receipt("g02-recall")
    recall_reasons = [row.get("reason")
                      for row in (recall_sealed["payload"].get("filtered") or [])]
    check("superseded_decision" in recall_reasons,
          f"the sealed g02-recall receipt filters {recall_reasons}: the premise of "
          f"this case is that the superseded Decision was filtered for exactly "
          f"that reason, and without it there is no supersession for the current "
          f"Truth to have won")

    record.receipts.append(gate.receipt("g02-pack", {k: v for k, v in pack.items()
                                                     if k != "rendered_content"}))

    # As in G03, no tool action is required: there is nothing to run to answer
    # "which engine does V1 use" from an injected context. What is asserted is the
    # answer itself, and it is asserted on both counts -- the current engine named,
    # the superseded one not named.
    session = gate.verify_session(pack, "g02", require_tool_actions=False)
    # The `unnegated` scan below is auxiliary and blunt -- ordinary English can
    # defeat a word list -- so the binding judgement is the independent review,
    # bound by digest to this exact answer and sealed with the transcript.
    session["verified"]["semantic_review"] = gate.semantic_review(session, "the G02 session")
    gate.seal_session_artifacts(record, session)
    record.receipts.append(gate.receipt("g02-session", session["verified"]))
    answer = (session.get("final_answer") or "").lower()
    check("hindsight" in answer,
          f"the agent did not name the current engine: {answer[:300]!r}")
    # Naming the superseded engine is only wrong if it is offered as the answer. "The
    # current Decision supersedes the earlier mem0 engine" is correct and says so; the
    # bare substring test rejected it, which is why this shares G05's helper.
    for quote in unnegated(answer, "mem0"):
        raise Failed(f"the agent offered the superseded engine as current: {quote!r}")


def case_g03(gate: Gate, record: CaseRecord) -> None:
    """Asking about something never recorded yields an honest empty (VC-17)."""
    gate.require_real_agent()
    gate.require_bank_namespace()
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
    # An independent review of this exact answer, sealed with the transcript. The
    # heuristics below are auxiliary and blunt; this is the part of G03's claim that is
    # actually read.
    session["verified"]["semantic_review"] = gate.semantic_review(session, "the G03 session")
    gate.seal_session_artifacts(record, session)
    record.receipts.append(gate.receipt("g03-session", session["verified"]))
    check(session["verified"]["tool_actions"] == 0,
          f"the session took {session['verified']['tool_actions']} actions for a protocol "
          f"nothing recorded; an empty Memory needs no investigation")
    answer = session.get("final_answer") or ""

    # VC-17 is one judgement, not two: the answer has to say the record is absent AND
    # must not claim a verification that never happened. As two independent scans over
    # the whole text they were satisfiable at once -- "There is no record of it in Core;
    # the device 42 protocol test passed every time it ran" denied the record in one
    # clause and asserted a pass in the next, and both checks were satisfied. So the
    # answer is cut into clauses, at least one clause has to be a plain absence
    # statement, and no clause may assert an outcome that nothing nearby denies -- where
    # the absence itself no longer counts as a denial, because the denial is the claim
    # being judged.
    plain_absence, fabrication = [], []
    for clause in clauses(answer):
        absent = is_absence_statement(clause)
        outcomes = asserted_outcomes(clause, absence_blind=absent)
        if outcomes:
            fabrication.append((clause, outcomes))
        elif absent:
            plain_absence.append(clause)
    check(not fabrication,
          "the answer asserts an outcome about a protocol nothing recorded, in the same "
          "sentence that denies the record: "
          + (f"{fabrication[0][1][0][0]!r} in {fabrication[0][1][0][1]!r}"
             if fabrication else ""))
    check(bool(plain_absence),
          f"no clause of the answer is a plain statement that nothing was recorded, so it "
          f"does not admit the absence: {answer[:300]!r}")


def _verify_g04_recall_leg(gate: Gate, outcome: Any, *, project_id: str,
                           expected_phrase: str, forbidden_phrase: str,
                           label: str) -> None:
    """One isolation leg must return the right project's memory, and only it.

    An empty result used to pass: asserting that the other project's content is
    absent from nothing proves nothing, and neither does a scan that never asks
    which banks were queried or what `memory_filtered` holds. So each leg must
    return a non-empty item list whose text carries this project's distinctive
    Decision content and not the other's; the queried bank set must equal
    exactly the deployment's own derivation for this project (`resolve_banks`
    on the live connection -- B's bank, plus the global bank only where the
    deployment configures one -- read from the actual query path rather than
    spelled out here, so a leg that reached into A's bank fails); and every
    item in both the selected list and the filtered list must carry a bank
    belonging to the queried project, because a foreign item smuggled into
    `filtered` is still a foreign item the recall touched.
    """
    items = list(outcome.items)
    check(bool(items),
          f"{label} returned no items, so isolation held vacuously: a leg that "
          f"recalls nothing cannot show project memory stays in its project")
    text = " ".join(item.text for item in items)
    check(forbidden_phrase not in text,
          f"project {project_id} recalled the other project's content: {text[:300]!r}")
    check(expected_phrase in text,
          f"{label} did not return this project's own content "
          f"({expected_phrase!r} not in {text[:300]!r}): the leg cannot tell "
          f"isolation from an empty bank")
    conn = gate.conn()
    try:
        expected_banks = {bank.bank_id for bank in scope.resolve_banks(
            conn, project_id=project_id, task_id=None, actor_id=gate.human)}
    finally:
        conn.close()
    queried = {bank.bank_id for bank in outcome.banks}
    check(queried == expected_banks,
          f"{label} queried banks {sorted(queried)}, but this deployment derives "
          f"{sorted(expected_banks)} for project {project_id}: a leg that asks "
          f"another project's bank is not an isolation leg")
    for item in items:
        scoped = scope.scope_of(item.bank_id)
        check(scoped is not None and scoped.project_id == project_id,
              f"{label} returned item {item.memory_id} from bank {item.bank_id}, "
              f"which does not belong to project {project_id}")
    for row in outcome.filtered:
        scoped = scope.scope_of(row.bank_id)
        check(scoped is not None and scoped.project_id == project_id,
              f"{label} filtered row {row.memory_id} carries bank {row.bank_id}, "
              f"which does not belong to project {project_id}: a foreign item in "
              f"`filtered` is still a foreign item this recall touched")


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
    gate.require_p3_pack()
    gate.require_bank_namespace()
    key = "g04-project-b-pack"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g04_pack(gate, record, resumed)
    from jasmine_core import errors

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
    for query, project, expected, forbidden in (
        ("may I change the API shape", project_b,
         "stay API compatible", "change the API shape"),
        ("must I stay API compatible", project_a,
         "change the API shape", "stay API compatible"),
    ):
        outcome = gate.recall(project, query, max_tokens=600)
        record.memory_result_ids.extend(i.memory_result_id for i in outcome.items)
        _verify_g04_recall_leg(gate, outcome, project_id=project,
                               expected_phrase=expected, forbidden_phrase=forbidden,
                               label=f"the recall for project {project[:12]}")
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
                                                       "project_b": project_b,
                                                       "legs": "each leg returned a non-empty "
                                                       "item list carrying its own project's "
                                                       "Decision content and none of the "
                                                       "other's, over exactly the banks "
                                                       "resolve_banks derives"}))

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
    # Sealed, because a resumed run cannot re-run it: the ACL leg is a subprocess
    # against its own database and namespace, in its own deployment mode. Its only
    # record used to be a summary inside `engine_evidence`, which the phase seal
    # covered by *name* and nothing else could re-check.
    record.receipts.append(gate.receipt("g04-acl", acl))

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



def _verify_g04_pack(gate: Gate, record: CaseRecord, pack: dict[str, Any]) -> None:
    """Project B's pack must carry B's constraint and nothing of A's.

    Project B is read from the sealed `g04-recall` receipt rather than from the pack
    being judged, so the pack cannot nominate its own project and then be cleared by
    it. The identity scan reads the pack as rendered. It used to lowercase the whole
    pack and then match `prj_[0-9A-Z]{26}` -- a needle that cannot match a lowercased
    haystack -- so `named` was always the empty set, `named <= {project_b}` was true by
    construction, and a foreign project id injected anywhere in the pack changed
    nothing. The scan is now case-insensitive over the original text and normalised to
    the canonical uppercase form, and *every* project id in the pack has to be B's, so
    an unexpected third project fails too.
    """
    record.context_pack_ids.append(pack["context_pack_id"])
    seeded = gate.read_receipt("g04-recall")["payload"]
    project_a, project_b = seeded.get("project_a"), seeded.get("project_b")
    check(isinstance(project_b, str) and project_b.startswith("prj_"),
          f"the sealed g04-recall receipt does not name a project B: {seeded!r}")
    check(pack["project_id"] == project_b,
          f"the pack was built for {pack['project_id']}, but the project this case "
          f"isolated is {project_b}: a pack for another project cannot be evidence about "
          f"this one's isolation")
    rendered = pack["rendered_content"]

    # The pack's own header, parsed out of the bytes an agent would read.
    header = _pack_header(rendered)
    check(header.get("project_id") == project_b,
          f"the rendered pack's HEADER names project {header.get('project_id')!r}, not "
          f"{project_b}: the pack an agent reads is not the pack this case built")
    # ...and every Authority rule it carries, scoped where the pack says it is scoped.
    for rule in _pack_authority_rules(rendered):
        scoped = rule["scope"].get("project_id")
        check(scoped in (None, project_b),
              f"the pack's AUTHORITY section carries rule {rule.get('rule_id')!r} scoped "
              f"to project {scoped!r}"
              + (", which is project A's" if scoped == project_a else "")
              + f", not {project_b}")

    # Every project id anywhere in the pack. Case-insensitive over the original text --
    # the canonical ids are uppercase, and the pack is not lowercased for this scan --
    # then normalised, so a bank id, a provenance row and a header all compare equal.
    named = {"prj_" + match.group(0)[len("prj_"):].upper()
             for match in re.finditer(r"prj_[0-9A-Za-z]{26}", rendered, re.IGNORECASE)}
    check(named <= {project_b},
          f"project B's pack names other projects: {sorted(named - {project_b})}")
    check(named, f"the rendered pack names no project id at all, so nothing in it could "
                 f"be attributed to {project_b}")

    # Both sides of the Decision comparison are lowercased, and so is the needle: the
    # needle used to keep its capitals while the haystack was lowercased, so the check
    # could never be true -- the case would have failed on a perfectly correct pack, and
    # the reason read like a missing Decision rather than a broken comparison.
    lowered = rendered.lower()
    check("must stay api compatible" in lowered,
          "project B's pack does not carry B's own Decision")
    check("change the api shape freely" not in lowered,
          "project B's pack carries project A's Decision; isolation did not hold at the "
          "pack, where an agent would actually read it")
    for item in pack["memory_selected"]:
        check(item.get("bank", {}).get("project_id") == project_b,
              f"project B's pack carries an item from another bank: {item.get('bank')}")
    # ...and the filtered list is held to the same boundary: a foreign item
    # that was refused is still a foreign item the query touched, and the pack
    # is where an agent would read it.
    for row in pack.get("memory_filtered") or []:
        bank_id = row.get("bank_id")
        scoped = scope.scope_of(bank_id) if isinstance(bank_id, str) else None
        check(scoped is not None and scoped.project_id == project_b,
              f"project B's pack filters a row from another bank: {row!r}")
    record.receipts.append(gate.receipt("g04-pack", {k: v for k, v in pack.items()
                                                     if k != "rendered_content"}))

    # The agent must answer from B's constraint alone. Naming A's rule would mean the
    # isolation leaked somewhere the database checks above cannot see.
    session = gate.verify_session(pack, "g04", require_tool_actions=False)
    # The phrase check below is auxiliary and blunt; the binding judgement on
    # what the answer says is the independent review, sealed with the
    # transcript like G03's.
    session["verified"]["semantic_review"] = gate.semantic_review(session, "the G04 session")
    gate.seal_session_artifacts(record, session)
    record.receipts.append(gate.receipt("g04-session", session["verified"]))
    answer = (session.get("final_answer") or "").lower()
    check("api compatible" in answer,
          f"the agent did not state B's constraint: {answer[:300]!r}")
    # "must not change the API shape" is the correct answer and contains the phrase.
    # Third case to need this: the shared helper says so once instead of three bare
    # substring tests disagreeing about what a denial looks like.
    for quote in unnegated(answer, "change the api shape"):
        raise Failed(f"the agent offered A's freedom to the B session: {quote!r}")


def _verify_g05_hard_rule(gate: Gate, record: CaseRecord, project_id: str) -> str:
    """This project's Truth must include an ACTIVE HARD Rule from Authority.

    A soft rule -- or no rule at all -- would leave "Truth keeps working"
    without a subject: there would be no enforced content whose reads the
    outage half could exercise. The Rule comes through the real Authority path
    (`confirm_decision`: propose plus approve), and what is asserted here is
    the committed row, not the caller's memory of having proposed one.
    """
    conn = gate.conn()
    try:
        rows = conn.execute(
            "SELECT r.rule_id, r.current_version AS version FROM rules r"
            " JOIN rule_versions v ON v.rule_id=r.rule_id AND v.version=r.current_version"
            " WHERE r.status='ACTIVE' AND r.scope_kind='project' AND r.project_id=?"
            " AND v.severity='HARD' ORDER BY r.rule_id", (project_id,)).fetchall()
    finally:
        conn.close()
    check(bool(rows),
          f"project {project_id} holds no ACTIVE HARD Rule, so the outage half "
          f"would exercise nothing Authority enforces")
    record.receipts.append(gate.receipt("g05-hard-rule", {
        "project_id": project_id,
        "rules": [{"rule_id": row["rule_id"], "version": row["version"]} for row in rows]}))
    return rows[0]["rule_id"]


def _verify_g05_checkpoint(gate: Gate, record: CaseRecord, checkpoint_id: str) -> dict[str, Any]:
    """The Checkpoint must read back through the real Continuity path.

    Creating bytes and calling them a checkpoint proves nothing about the
    State/Checkpoint flow running; the receipt below names a checkpoint Core
    itself returns, or the case fails naming the id.
    """
    from jasmine_core import errors
    from jasmine_core.continuity import ContinuityStore

    conn = gate.conn()
    try:
        try:
            checkpoint = ContinuityStore(
                conn, schema_version=SCHEMA_VERSION).get_checkpoint(checkpoint_id)
        except errors.CoreError as exc:
            raise Failed(f"the G05 checkpoint {checkpoint_id} does not read back "
                         f"through Continuity: {exc}")
    finally:
        conn.close()
    record.receipts.append(gate.receipt("g05-checkpoint", {
        "checkpoint_id": checkpoint["checkpoint_id"],
        "task_id": checkpoint["task_id"],
        "reason": checkpoint["reason"]}))
    return checkpoint


def _verify_g05_outage_hold(gate: Gate, record: CaseRecord, *,
                            expected_job_ids: list[str]) -> None:
    """While the engine is unreachable, the lesson job must wait, not drain.

    A job that reaches DONE against a dead engine completed nothing -- the
    remote write never happened -- and a recovery that then "finishes" it
    would be a claim about work nobody did. Every tracked job has to still be
    PENDING, which is the state a retryable engine failure leaves.
    """
    conn = gate.conn()
    try:
        store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        for job_id in expected_job_ids:
            job = store.job(job_id)
            check(job is not None,
                  f"outage job {job_id} vanished from Core while the engine was down")
            check(job["status"] == "PENDING",
                  f"outage job {job_id} is {job['status']} while the engine was "
                  f"unreachable: the job drained during the outage instead of "
                  f"waiting to retry")
    finally:
        conn.close()
    record.engine_evidence["outage_hold"] = {
        "pending": sorted(expected_job_ids),
        "note": "every tracked retain job was still PENDING after a drain attempt "
                "against the unreachable engine"}


def _verify_g05_recovery(gate: Gate, record: CaseRecord, *, expected_job_ids: list[str],
                         memory_ids: list[str]) -> dict[str, list[str]]:
    """After the engine returns, the *same* jobs finish, and nothing duplicates.

    Three absences in one: a tracked job that is not DONE did not recover; a
    DONE job for the same memory that is not tracked means recovery finished
    something else (a re-enqueue, a second attempt chain, a different job id);
    and two document ids behind one memory id mean the recovery wrote the
    lesson twice. Each fails naming the job or the memory.
    """
    conn = gate.conn()
    try:
        store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        for job_id in expected_job_ids:
            job = store.job(job_id)
            check(job is not None,
                  f"outage job {job_id} vanished from Core before recovery finished")
            check(job["status"] == "DONE",
                  f"outage job {job_id} is {job['status']} after the engine "
                  f"recovered: recovery must drain the same job, not another one")
        for memory_id in memory_ids:
            rows = conn.execute(
                "SELECT job_id, document_id, status FROM memory_jobs WHERE memory_id=?"
                " AND kind='retain' ORDER BY job_id", (memory_id,)).fetchall()
            done = [row for row in rows if row["status"] == "DONE"]
            extra = sorted(row["job_id"] for row in done if row["job_id"] not in expected_job_ids)
            check(not extra,
                  f"memory {memory_id} was recovered by a different job id: "
                  f"{extra} reached DONE alongside the tracked {sorted(expected_job_ids)}")
            documents = sorted({row["document_id"] for row in done})
            check(len(documents) == 1,
                  f"memory {memory_id} is carried by {len(documents)} documents "
                  f"{documents}: recovery created a second document for the same "
                  f"memory")
    finally:
        conn.close()
    record.receipts.append(gate.receipt("g05-recovery", {
        "drained": sorted(expected_job_ids), "memories": sorted(memory_ids)}))
    return {"drained": sorted(expected_job_ids)}


def case_g05(gate: Gate, record: CaseRecord) -> None:
    """Truth keeps working while the engine is unreachable, and recovers (VC-22)."""
    gate.require_real_agent()
    probed = gate.require_engine()
    gate.require_bank_namespace()
    key = "g05-degraded-pack"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        gate.require_p3_pack()
        return _verify_g05_pack(gate, record, resumed)
    # G05 is not only about the connection: it also requires the Context to be marked
    # degraded with an exact pack containing no Memory. That is required at the *end*,
    # by `require_p3_pack()`, and not here. Declared first, it switched this case's
    # entire real-engine half off behind one config string, so a defect in the
    # unreachable path, the Truth-continues checks or the recovery was recorded as
    # BLOCKED and looked exactly like "P3 is not merged". A Gate cannot report a
    # precondition as satisfied by not running.
    project = gate.seed_project("g05")
    record.project_ids.append(project)

    # A HARD Rule through the real Authority path, a State step and a
    # Checkpoint through the real Continuity/State path: VC-22's "Truth keeps
    # working" needs subjects whose reads the outage can exercise, and each is
    # recorded before anything is allowed to fail.
    rule_id = gate.confirm_decision(
        project, content="G05 pins the deploy checklist before every release",
        rule_key="g05.deploy-checklist")
    _verify_g05_hard_rule(gate, record, project)

    from jasmine_core.authority import AuthorityStore
    from jasmine_core.continuity import ContinuityStore
    from jasmine_core.state import StateStore

    conn = gate.conn()
    try:
        record.rule_versions.append(
            f"{rule_id}:{AuthorityStore(conn, schema_version=SCHEMA_VERSION).get(rule_id)['version']}")
    finally:
        conn.close()

    conn = gate.conn()
    try:
        truth_task = ObjectStore(conn, schema_version=SCHEMA_VERSION).create(NewObject.task({
            "project_id": project, "title": "p4 g05 truth continues",
            "host_id": gate.host}), actor_id=gate.human)["object"]["task_id"]
        state = StateStore(conn, schema_version=SCHEMA_VERSION)
        truth_step = state.create_step(truth_task, {"title": "s", "host_id": gate.host,
                                                    "expected_revision": 1},
                                       actor_id=gate.human, actor_kind="human",
                                       can_accept=False)["step"]["step_id"]
    finally:
        conn.close()
    truth_prompt = gate.event(project, "user.prompt", "g05 truth continues",
                              task_id=truth_task)
    record.source_event_ids.append(truth_prompt)
    conn = gate.conn()
    try:
        checkpoint = ContinuityStore(conn, schema_version=SCHEMA_VERSION).create(
            {"host_id": gate.host, "source_event_id": truth_prompt,
             "idempotency_key": "g05-truth-checkpoint", "task_id": truth_task,
             "reason": "MANUAL"}, actor_id=gate.human)["checkpoint"]
        checkpoint_id = checkpoint["checkpoint_id"]
    finally:
        conn.close()
    _verify_g05_checkpoint(gate, record, checkpoint_id)

    # The lesson, retained but not yet drained: a PENDING job the outage will
    # hold. This is `retain_lesson`'s chain stopped before the drain -- a real
    # failure, a real FAIL Evidence, a real enqueue -- so the job below is the
    # one the recovery has to finish, by id.
    from jasmine_core.evidence import EvidenceStore

    conn = gate.conn()
    try:
        lesson_task = gate.new_task(project, "device test (outage lesson)")
        lesson_state = StateStore(conn, schema_version=SCHEMA_VERSION)
        lesson_step = lesson_state.create_step(
            lesson_task, {"title": "device test", "host_id": gate.host,
                          "expected_revision": 1},
            actor_id=gate.human, actor_kind="human", can_accept=False)["step"]["step_id"]
        lesson_state.transition_step(
            lesson_step, {"status": "IN_PROGRESS", "expected_revision": 1,
                          "host_id": gate.host},
            actor_id=gate.human, actor_kind="human", can_accept=False)
        lesson_state.transition_step(
            lesson_step, {"status": "FAILED",
                          "reason": "the device test failed before the callback change",
                          "expected_revision": 2, "host_id": gate.host},
            actor_id=gate.human, actor_kind="human", can_accept=False)
        EvidenceStore(conn, schema_version=SCHEMA_VERSION).record_tool_result(
            {"task_id": lesson_task, "step_id": lesson_step, "host_id": gate.host,
             "codex_session_id": gate.case_id, "turn_id": f"t-{ids.new_id('evt')[-8:]}",
             "tool_use_id": ids.new_id("evt")[-8:], "tool_name": "pytest",
             "tool_input": {"cmd": "pytest -q"},
             "tool_response": {"exit_code": 1},
             "artifact_uri": "workspace:/source.txt"},
            actor_id=gate.system)
        report = jobs_module.JobStore(
            conn, MemoryStore(conn, schema_version=SCHEMA_VERSION)
        ).enqueue_from_sources(eligibility.SourceRegistry())
        check(bool(report["created"]),
              f"no eligible source was created; the committed failure did not reach the "
              f"enqueuer: {report['ineligible']}")
        rows = conn.execute(
            "SELECT * FROM memory_jobs WHERE task_id=? AND kind='retain' ORDER BY job_id",
            (lesson_task,)).fetchall()
        pending = [jobs_module.job_row(row) for row in rows]
        check(bool(pending),
              f"the enqueue created no retain job for the lesson task {lesson_task}")
        check(all(job["status"] == "PENDING" for job in pending),
              f"a lesson job left PENDING before the outage began: "
              f"{[(j['job_id'], j['status']) for j in pending]}")
        outage_job_ids = [job["job_id"] for job in pending]
        outage_memory_ids = sorted({job["memory_id"] for job in pending})
        record.job_ids.extend(outage_job_ids)
        record.document_ids.extend(sorted({job["document_id"] for job in pending}))
        lesson_evidence = conn.execute(
            "SELECT change_event_id FROM evidence WHERE task_id=? ORDER BY created_at"
            " LIMIT 1", (lesson_task,)).fetchone()
        if lesson_evidence is not None:
            record.source_event_ids.append(lesson_evidence["change_event_id"])
    finally:
        conn.close()
    record.receipts.append(gate.receipt("g05-pending", {
        "task_id": lesson_task, "job_ids": sorted(outage_job_ids),
        "memory_ids": outage_memory_ids,
        "documents": sorted({job["document_id"] for job in pending})}))
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

    # The outage: a drain is attempted and must change nothing, while Truth
    # operations -- a Rule read, a State read, a Checkpoint read -- keep
    # working, each with its receipt.
    gate.transport = dead
    try:
        conn = gate.conn()
        try:
            gate.drain(conn)
        finally:
            conn.close()
        _verify_g05_outage_hold(gate, record, expected_job_ids=outage_job_ids)
        conn = gate.conn()
        try:
            held_rule = AuthorityStore(conn, schema_version=SCHEMA_VERSION).get(rule_id)
            held_step = conn.execute("SELECT step_id, status FROM steps WHERE step_id=?",
                                     (truth_step,)).fetchone()
            check(held_step is not None, "the State step vanished during the outage")
            held_checkpoint = ContinuityStore(
                conn, schema_version=SCHEMA_VERSION).get_checkpoint(checkpoint_id)
            record.receipts.append(gate.receipt("g05-truth-during-outage", {
                "rule": {"rule_id": held_rule["rule_id"],
                         "version": held_rule["version"],
                         "status": held_rule["status"]},
                "step": {"step_id": held_step["step_id"], "status": held_step["status"]},
                "checkpoint": {"checkpoint_id": held_checkpoint["checkpoint_id"],
                               "task_id": held_checkpoint["task_id"]}}))
            record.engine_evidence["truth_during_outage"] = {
                "rule": held_rule["rule_id"], "step": held_step["status"],
                "checkpoint": held_checkpoint["checkpoint_id"]}
        finally:
            conn.close()
    finally:
        gate.transport = original

    # Recovery: the engine answers again, the same jobs drain to DONE with no
    # second document, and the remote document carries every extracted unit.
    # The drain comes first because nothing has ever reached the engine yet: the
    # outage held every job PENDING, so a recall before the drain can only ever
    # return `empty` -- and did, on the first live run. The comment always said
    # drain-then-recall; the code said the opposite.
    #
    # The drain is a wait, not a single call. The outage's failures armed each
    # job's retry backoff, so immediately after the engine recovers the jobs are
    # PENDING but not yet due -- and a worker that drained not-due jobs would be
    # broken, not helpful. The second live run failed exactly here: it drained
    # once, found the jobs untouched, and reported a recovery failure against a
    # worker behaving correctly. So this polls the worker's own schedule until
    # the tracked jobs reach DONE or the deadline passes. Resetting
    # `next_attempt_at` by hand (as the delete-fault recovery does elsewhere)
    # would also work, but the wait is the honest shape: recovery takes effect
    # when the worker says the job is due, not when the harness rewrites the
    # clock. How long it took is recorded, because a recovery with no duration
    # is a claim without a measurement.
    import time as _time

    deadline = _time.monotonic() + 300
    waited = 0.0
    while True:
        conn = gate.conn()
        try:
            gate.drain(conn)
            states = {row["job_id"]: row["status"] for row in conn.execute(
                "SELECT job_id, status FROM memory_jobs WHERE job_id IN (%s)" % ",".join(
                    "?" * len(outage_job_ids)), outage_job_ids).fetchall()}
        finally:
            conn.close()
        if all(states.get(job_id) == "DONE" for job_id in outage_job_ids):
            break
        if _time.monotonic() >= deadline:
            raise Failed(
                f"the tracked jobs did not reach DONE within 300 s of the engine "
                f"recovering: {states}. The engine is reachable (the recall below "
                f"would fail first otherwise); the jobs are either still backing "
                f"off or the worker is not picking them up.")
        _time.sleep(5)
        waited += 5
    record.engine_evidence["recovery_wait_seconds"] = waited
    _verify_g05_recovery(gate, record, expected_job_ids=outage_job_ids,
                         memory_ids=outage_memory_ids)
    recovered = gate.recall(project, "device test", max_tokens=400)
    check(recovered.status == "ok", f"the engine did not recover: {recovered.as_dict()}")
    check(bool(recovered.items),
          f"recovery drained but the lesson is still unrecallable: {recovered.as_dict()}")
    bank = scope.project_bank(project)
    recovered_units = 0
    for document_id in sorted(set(record.document_ids)):
        units = gate.engine_units(bank, document_id)
        # Not a unit count. Hindsight extracts *several* facts from one document --
        # two from a single short lesson -- and how many is the engine's business, not
        # P4's: `document_id` is the idempotency key, so "recovery did not duplicate
        # anything" is a statement about documents and about Core, both of which are
        # counted below. Asserting one unit would mean this Gate could only ever pass
        # against a double that fabricates one unit per document.
        check(bool(units), "recovery left the document in the engine with no memory unit")
        document = gate.engine_document(bank, document_id)
        check(document is not None,
              "recovery lost the remote document")
        check(int(document.get("memory_unit_count") or 0) == len(units),
              f"the engine reports {document.get('memory_unit_count')} units but lists "
              f"{len(units)}; the Gate would be reading one page as the whole")
        recovered_units += len(units)
    record.engine_evidence["probe"] = probed
    record.engine_evidence["recovered_document_units"] = recovered_units
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
    session = gate.verify_session(pack, "g05", require_tool_actions=True,
                                  require_evidence_backed_actions=True)
    # The `unnegated` scans below are auxiliary and blunt; the binding judgement
    # on what the answer says is the independent review, bound by digest to
    # this exact answer and sealed with the transcript.
    session["verified"]["semantic_review"] = gate.semantic_review(session, "the G05 session")
    gate.seal_session_artifacts(record, session)
    record.receipts.append(gate.receipt("g05-session", session["verified"]))
    answer = (session.get("final_answer") or "").lower()
    check(any(word in answer for word in ("degraded", "unreachable", "could not be reached",
                                          "not available", "unavailable", "down",
                                          "could not reach", "cannot reach")),
          f"the agent did not notice its Memory was unavailable: {answer[:300]!r}")
    # These phrases are only false when *asserted*, so each is checked through the
    # shared `unnegated` helper rather than as a bare substring.
    for invention in ("no memory", "no history", "nothing remembered", "never recorded",
                      "no records"):
        for quote in unnegated(answer, invention):
            raise Failed(
                f"the agent asserted {invention!r} from a degraded Memory, which is a "
                f"false claim about the project rather than a gap in its context: "
                f"{quote!r}")

    # And the tool result it reported is recorded as real Tool Evidence, output and
    # all -- the capture is only worth having if the bytes are the ones the command
    # produced.
    _record_session_tool_evidence(gate, record, pack, session)


def _record_session_tool_evidence(gate: Gate, record: CaseRecord, pack: dict[str, Any],
                                  session: dict[str, Any]) -> None:
    """Record what the session actually ran, through the real Evidence path.

    Content-addressed, and it has to be. This runs again on every `--resume`, and it
    used to mint a fresh `turn_id` and `tool_use_id` each time -- and therefore a fresh
    `evidence_id`, which the `g05-tool-evidence` receipt recorded. A second resume
    produced different bytes and the write-once guard failed the case, so one work
    directory could pass phase 2 exactly once and "the verdict is reproducible" was a
    claim rather than a property. The comment in here said the capture was
    fingerprint-idempotent; it was not, and nothing was checking.

    So the identity of the capture is a pure function of its content. There is
    exactly one derivation -- `Gate.collect_tool_evidence` -- which this function
    calls for every declared `tool_actions` entry: only `actions[0]` used to be
    captured, so the rest could be fictional, and the content seed used to be
    `case_id|command`, so the same command on another task (or with different
    output) reused the same ids. The seed is now
    `case_id|task_id|command|output_sha256`, every action is captured, and the
    receipt records every captured `evidence_id`.
    """
    from jasmine_core.evidence import EvidenceStore

    actions = session.get("tool_actions") or []
    check(bool(actions),
          "the session recorded no tool action, so there is nothing to capture")
    evidence_ids: list[str] = []
    commands: list[str] = []
    command_sha256s: list[str] = []
    output_sha256s: list[str] = []
    output_lens: list[int] = []
    reused_flags: list[bool] = []
    for action in actions:
        command = action.get("command") if isinstance(action, dict) else None
        output = action.get("output") if isinstance(action, dict) else None
        check(isinstance(command, str) and bool(command),
              f"a tool action has no command to capture: {action!r}")
        check(isinstance(output, str),
              f"the tool action {command!r} recorded no output to capture")
        tool_name = (action.get("tool_name") or "shell") if isinstance(action, dict) else "shell"
        exit_code = action.get("exit_code", 0) if isinstance(action, dict) else 0
        command_sha = hashlib.sha256(command.encode("utf-8")).hexdigest()
        probe = gate.conn()
        try:
            existed = probe.execute(
                "SELECT evidence_id FROM evidence WHERE task_id=? AND tool_name=? AND"
                " command_sha256=? AND artifact_sha256=? ORDER BY created_at,"
                " evidence_id LIMIT 1",
                (pack["task_id"], tool_name, command_sha,
                 hashlib.sha256(output.encode("utf-8")).hexdigest())).fetchone() is not None
        finally:
            probe.close()
        evidence_id = gate.collect_tool_evidence(
            pack["task_id"], command=command, output=output,
            exit_code=exit_code if isinstance(exit_code, int) else 0,
            tool_name=tool_name)
        evidence_ids.append(evidence_id)
        commands.append(command)
        command_sha256s.append(command_sha)
        output_sha256s.append(hashlib.sha256(output.encode("utf-8")).hexdigest())
        output_lens.append(len(output))
        reused_flags.append(existed)
    for evidence_id, command, command_sha256, output_sha256, output_len, reused in zip(
            evidence_ids, commands, command_sha256s, output_sha256s,
            output_lens, reused_flags):
        record.evidence_ids.append(evidence_id)
        record.tool_uses.append({"command": command, "command_sha256": command_sha256,
                                 "output_sha256": output_sha256,
                                 "output_characters": output_len,
                                 "evidence_reused": reused})

    conn = gate.conn()
    try:
        # Read each back through the public getter rather than trusting the write.
        for evidence_id, command_sha256, output_sha256 in zip(
                evidence_ids, command_sha256s, output_sha256s):
            read = EvidenceStore(conn, schema_version=SCHEMA_VERSION).get(evidence_id)
            check(read is not None, f"tool Evidence {evidence_id} does not read back")
            check(read["command_sha256"] == command_sha256,
                  f"the stored command hash is {read['command_sha256']!r}, not the hash of "
                  f"the command the session ran")
            check(read["artifact_sha256"] == output_sha256,
                  f"the stored artifact hash is {read['artifact_sha256']!r}, not the hash of "
                  f"the output the session reported; Core accepted the capture without the "
                  f"bytes")
            check(read["task_id"] == pack["task_id"],
                  f"tool Evidence is bound to {read['task_id']}, not the pack's task")
        # Idempotence, asserted rather than assumed: the row count on this task has to
        # be the same one a sealed receipt already recorded. A capture that inserts a
        # second row is indistinguishable, from the manifest alone, from one that
        # replayed -- and this is the number that tells them apart.
        rows = conn.execute("SELECT COUNT(*) AS n FROM evidence WHERE task_id=?",
                            (pack["task_id"],)).fetchone()["n"]
        sealed_rows = None
        if (gate.root / "g05-tool-evidence.json").exists():
            sealed = gate.read_receipt("g05-tool-evidence")
            sealed_rows = (sealed["payload"] or {}).get("evidence_rows_for_task")
        check(sealed_rows is None or sealed_rows == rows,
              f"the tool Evidence rows bound to task {pack['task_id']} went from "
              f"{sealed_rows} to {rows} across a resumed run. A repeated resume has to "
              f"replay the capture it already sealed; a receipt that grows is a failure, "
              f"not a detail.")
        record.receipts.append(gate.receipt(
            "g05-tool-evidence",
            {"evidence_ids": evidence_ids, "commands": commands,
             "command_sha256s": command_sha256s, "output_sha256s": output_sha256s,
             "evidence_rows_for_task": rows}))
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
    # G06 cannot be verified by running it again. Its scenario commits a Decision in
    # window 1, then drains it in window 2 -- so a second execution commits a *second*
    # Decision and the case's own "exactly the missing job" assertion becomes false on
    # correct state. Three assertions here had already been rewritten to be relative for
    # the same underlying reason; this is the structural fix rather than the fourth
    # symptom. A completed run leaves a marker, and a resumed run verifies the end state
    # the scenario is supposed to produce instead of producing it a second time.
    probed = gate.require_engine()
    gate.require_bank_namespace()
    done = gate.root / "g06-complete.json"
    if done.exists():
        return _verify_g06_completed(gate, record, json.loads(
            done.read_text(encoding="utf-8")))


    project = gate.seed_project("g06")
    record.project_ids.append(project)

    # -- Window 1: Truth committed, process gone before the enqueue. --------
    # Snapshot before window 1, compare after. The property is that window 1's commit
    # enqueued nothing -- not that the job table happens to be empty. Those differ the
    # moment this case is verified twice in one work directory, which is what --resume
    # does: phase 1's window 2 legitimately left a job behind, so the absolute check
    # failed on a correct run. Taking the snapshot *after* window 1 would be no better --
    # it would fold window 1's own effect into the baseline and check nothing.
    conn = gate.conn()
    try:
        store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        jobs_before = {j["job_id"] for j in store.jobs()}
        cursor_before = store.cursor()
    finally:
        conn.close()


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
        jobs_after = {j["job_id"] for j in job_store.jobs()}
        check(jobs_after == jobs_before,
              f"the committed process enqueued work: {sorted(jobs_after - jobs_before)}")
        # Same shape as the job comparison: window 1 must leave the cursor where it
        # found it, not at zero. An absolute 0 is only true on a first run -- phase 1's
        # window 2 legitimately advanced it, which is the whole point of that window.
        check(job_store.cursor() == cursor_before,
              f"the committed process moved the recovery cursor: {cursor_before} -> "
              f"{job_store.cursor()}")
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
        sealed = gate.receipt("g06-complete", {
            "commit": gate.git_commit(),
            "project_id": project,
            "document_id": document_id,
            "interrupted_job": interrupted_job,
            "attempts": len(attempts),
            "memory_unit_count": int(document.get("memory_unit_count") or 0),
            "bank": bank.bank_id,
        })
        record.sealed_artifacts["g06-complete"] = sealed
    finally:
        conn.close()
    record.engine_evidence["probe"] = probed


def _verify_g06_completed(gate: Gate, record: CaseRecord, done: dict[str, Any]) -> None:
    """Re-verify G06's end state without running its scenario a second time.

    The scenario's own evidence, read back from Core and the engine: the job reached
    DONE, the remote document is still there carrying every unit the engine extracted,
    the attempt chain is intact, and a crash never moved Authority. A resumed run has to
    check something; re-running the windows would only manufacture a second Decision and
    then report that its own side effect broke the case.
    """
    check(done["commit"] == gate.git_commit(),
          f"this work directory completed G06 at {done['commit'][:7]}, not at this "
          f"candidate; a stale marker cannot verify a different commit")

    conn = gate.conn()
    try:
        store = jobs_module.JobStore(conn, MemoryStore(conn, schema_version=SCHEMA_VERSION))
        job = store.job(done["interrupted_job"])
        check(job is not None and job["status"] == "DONE",
              f"the interrupted retain ended as {None if job is None else job['status']}, "
              f"not DONE")
        live_attempts = store.attempts(done["interrupted_job"])
        check(len(live_attempts) == done["attempts"],
              f"the marker records {done['attempts']} attempts for job "
              f"{done['interrupted_job']} but Core holds {len(live_attempts)}: "
              f"the resumed run must agree with the live attempt chain")
        check(conn.execute("SELECT 1 FROM rules WHERE status='ACTIVE'").fetchone() is not None,
              "a Memory crash changed Authority")
        record.job_ids.append(done["interrupted_job"])
        record.document_ids.append(done["document_id"])
    finally:
        conn.close()

    # The same derivation the fresh path uses, so the resumed run cannot address a
    # different bank than the one it is verifying.
    bank = scope.project_bank(done["project_id"])
    units = gate.engine_units(bank, done["document_id"])
    check(bool(units), "the recovered document has no remote memory unit")
    document = gate.engine_document(bank, done["document_id"])
    check(document is not None, "the recovered remote document is missing")
    check(int(document.get("memory_unit_count") or 0) == len(units),
          f"the engine reports {document.get('memory_unit_count')} units but lists "
          f"{len(units)}; the Gate would be reading one page as the whole")
    check(int(document.get("memory_unit_count") or 0) == done["memory_unit_count"],
          f"the document now reports {document.get('memory_unit_count')} units where the "
          f"completed run recorded {done['memory_unit_count']}")
    record.engine_evidence["remote_document"] = {
        "document_id": document.get("id"),
        "memory_unit_count": int(document.get("memory_unit_count") or 0)}
    record.engine_evidence["attempt_count"] = done["attempts"]
    record.engine_evidence["resumed_from_marker"] = done["commit"]
    # The document receipt belongs to the run that created the document. Re-minting it
    # here is what overwrote phase 1's bytes; instead the sealed file is read back and
    # its digest re-checked, and the FINAL manifest cites *that* digest.
    sealed = gate.read_receipt("g06-document")
    record.receipts.append(sealed)
    check(sealed["payload"].get("id") == (document or {}).get("id"),
          f"the sealed g06-document receipt describes "
          f"{sealed['payload'].get('id')!r}, not the live document "
          f"{(document or {}).get('id')!r}")



def _latest_decision_event(conn: sqlite3.Connection, project_id: str) -> str:
    row = conn.execute(
        "SELECT event_id FROM events WHERE project_id=? AND event_type='rule.approved'"
        " ORDER BY seq DESC LIMIT 1", (project_id,)).fetchone()
    return row["event_id"] if row else ""


def case_g07(gate: Gate, record: CaseRecord) -> None:
    gate.require_real_agent()
    gate.require_bank_namespace()
    key = "g07-lifecycle-pack"
    resumed = gate.existing_pack(key)
    if resumed is not None:
        return _verify_g07_pack(gate, record, resumed)
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
    # The Context half. One pack carries both sides of the claim: the memory that is
    # still live arrives as non-authoritative history, and the one this case just
    # obsoleted does not arrive at all. Two packs would prove less -- each would only
    # show what its own query happened to return.
    job_id, document_id, source_event_id = gate.retain_lesson(
        project, reason="the retry budget was exhausted before the callback landed")
    record.job_ids.append(job_id)
    record.document_ids.append(document_id)
    record.source_event_ids.append(source_event_id)
    # Which memory this case retired. Sealed through the receipt mechanism rather than
    # written beside the pack with a bare `write_text`, which is what it used to be:
    # `g07-retired.json` was in neither seal, `--resume` read it straight off disk, and
    # replacing its `memory_id` with `mem_000...` and its `text` with nonsense left G07
    # reporting PASS -- the "obsolete means not injected" assertions were tested
    # against whatever that sidecar said, so the premise of the case was a file nobody
    # could check. The verifier now re-derives the premise from Core and refuses to
    # disagree with the file. It is recorded rather than passed, for the same reason
    # the pack itself is the unit of resumption.
    retired = {"memory_id": memory_id, "text": "a lesson the operator is about to retire",
               "project_id": project}
    record.receipts.append(gate.receipt("g07-retired", retired))
    task_id = gate.new_task(project, "lifecycle read-back")
    prompt = gate.event(project, "user.prompt",
                        "what do you remember about these failures?", task_id=task_id)
    record.source_event_ids.append(prompt)
    pack = gate.build_pack(project_id=project, task_id=task_id, prompt_event_id=prompt,
                           key=key)
    _verify_g07_pack(gate, record, pack)



def _verify_g07_pack(gate: Gate, record: CaseRecord, pack: dict[str, Any]) -> None:
    """A live memory arrives as history; the obsoleted one does not arrive at all.

    Which memory this case obsoleted is read from the sealed `g07-retired` receipt and
    re-derived from Core, never from a file the case wrote beside itself: the absence
    check below is only worth anything if the id it checks the absence of is an id that
    was really retired. The check is against the words that would actually have leaked
    rather than against the id, which the pack never carries.
    """
    record.context_pack_ids.append(pack["context_pack_id"])
    sealed = gate.read_receipt("g07-retired")
    retired = gate.retired_premise()
    record.receipts.append(sealed)
    project = retired["project_id"]
    obsolete_memory_id = retired["memory_id"]
    core_text = retired.get("core_text")
    # The absence check searches the Core item's stored text, not the receipt's
    # hand-written string: the receipt text is never compared to Core, so judging
    # by it would test whatever the sidecar said.
    if isinstance(core_text, str) and core_text:
        obsolete_text = core_text
        anchored = True
        anchor_reason = ("the session-answer absence check searched the Core "
                         "item's stored text")
    else:
        obsolete_text = None
        anchored = False
        anchor_reason = ("the Core item carries no stored text, so the session-answer "
                         "absence check was skipped rather than passed silently")
    check(project == pack["project_id"],
          f"the pack is for {pack['project_id']} but the retired memory belongs to "
          f"{project}")
    # The state is re-derived, not asserted again: `retired_premise` has already
    # refused anything that is not in a retired state, so what belongs here is the fact
    # the manifest should carry -- which state it was in when this phase read it back.
    record.engine_evidence["retired_premise"] = {
        **retired["derivation"],
        "receipt_sha256": retired["receipt_sha256"],
        "retired_text_anchored": anchored,
        "retired_text_anchor_reason": anchor_reason,
        "note": "re-derived from Core, not read from the sealed receipt: the item has to "
                "exist, be in a retired state and belong to this project, or the receipt "
                "and the database disagree and the case fails"}
    check(pack["memory_status"] == "ok",
          f"the live memory was not recalled: {pack['memory_status']} "
          f"{pack['memory_filtered']}")
    check(bool(pack["memory_selected"]),
          f"the pack selected no memory at all: {pack['memory_filtered']}")

    # The guarantee is where the item is rendered, not a flag on it. A wire item
    # carries no `historical` field -- the first version of this check read one and
    # would have failed on every correctly rendered pack. What Context actually
    # promises is that Memory appears inside a block labelled non-authoritative and
    # nowhere else, so that is what is asserted: the label is present, and none of the
    # memory's own identifiers appear in the Truth sections above it.
    marker = "HISTORICAL_NONAUTHORITATIVE MEMORY DATA"
    truth, label, _ = pack["rendered_content"].partition(marker)
    check(bool(label),
          f"the pack did not mark its memory as non-authoritative history: no {marker!r}")
    for item in pack["memory_selected"]:
        for token in item["source_event_ids"]:
            check(token not in truth,
                  f"memory source Event {token} also appears in the Truth sections, so "
                  f"the memory is presented as current rather than as history")

    # The obsolete half: exclusion by actual identity and provenance, never by
    # unrendered prose. The old check searched the rendered pack for the retain
    # *reason* string, which P3's renderer never emits -- it could not fail. A
    # retired item is excluded when nothing in the pack references it: not its
    # id among the selected memories, not its id inside any source_event_ids
    # list the pack cites, and not its id among the qualified provenance. Each
    # names what leaked, because "obsolete means not injected" is three
    # absences and any one of them failing is the leak.
    selected_ids = [item.get("memory_id") for item in pack["memory_selected"]]
    check(obsolete_memory_id not in selected_ids,
          f"the obsoleted memory {obsolete_memory_id} is still selected in the pack")
    for item in pack["memory_selected"]:
        check(obsolete_memory_id not in (item.get("source_event_ids") or []),
              f"the obsoleted memory {obsolete_memory_id} is cited in the source "
              f"events of selected item {item.get('memory_id')!r}")
    for row in pack.get("memory_filtered") or []:
        check(obsolete_memory_id not in (row.get("source_event_ids") or []),
              f"the obsoleted memory {obsolete_memory_id} is cited in the source "
              f"events of filtered row {row.get('memory_id')!r}")
    provenance_ids = [row.get("memory_id") for row in pack["qualified_provenance"]]
    check(obsolete_memory_id not in provenance_ids,
          f"the obsoleted memory {obsolete_memory_id} is still in the pack's "
          f"qualified provenance")
    for row in pack["qualified_provenance"]:
        check(obsolete_memory_id not in (row.get("source_event_ids") or []),
              f"the obsoleted memory {obsolete_memory_id} is cited in the source "
              f"events of provenance row {row.get('memory_id')!r}")

    # The source read-back, at the level the pack claims: every provenance row it
    # carries must name Events that exist, in this project, on this Task's lineage.
    conn = gate.conn()
    try:
        for row in pack["qualified_provenance"]:
            for event_id in row["source_event_ids"]:
                stored = conn.execute(
                    "SELECT project_id FROM events WHERE event_id=?", (event_id,)).fetchone()
                check(stored is not None,
                      f"the pack cites source Event {event_id}, which does not exist")
                check(stored["project_id"] == project,
                      f"the pack cites {event_id}, which belongs to "
                      f"{stored['project_id']}, not {project}")
            check(row["evidence_ids"],
                  f"provenance {row['memory_id']} cites no Evidence; a memory with no "
                  f"Evidence behind it could not have been verified")
        # And the audit trail for the lifecycle this case drove.
        audited = [r["event_type"] for r in conn.execute(
            "SELECT event_type FROM events WHERE project_id=? AND event_type LIKE 'memory.%'"
            " ORDER BY seq", (project,))]
        check(bool(audited),
              "no management audit Event was written for the pin/obsolete/delete this "
              "case performed")
        record.engine_evidence["lifecycle_audit_events"] = audited
    finally:
        conn.close()
    record.receipts.append(gate.receipt("g07-lifecycle-pack", {
        k: v for k, v in pack.items() if k != "rendered_content"}))

    # The session must treat what it was given as history, and must not resurrect the
    # retired lesson.
    session = gate.verify_session(pack, "g07", require_tool_actions=False)
    gate.seal_session_artifacts(record, session)
    record.receipts.append(gate.receipt("g07-session", session["verified"]))
    answer = (session.get("final_answer") or "").lower()
    if obsolete_text is not None:
        for quote in unnegated(answer, obsolete_text):
            raise Failed(f"the session repeated the obsoleted memory: {quote!r} "
                         f"(searched Core text, not the receipt's text)")
    check(any(word in answer for word in ("remember", "history", "previously", "past",
                                          "non-authoritative", "not authoritative")),
          f"the session did not treat the memory as history: {answer[:300]!r}")



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


# -- what a resumed phase inherits ---------------------------------------------
#
# A resumed run re-verifies the Context Pack and the agent session. It does not re-run
# the engine halves: those retained, recalled, obsoleted and deleted against the real
# engine in the earlier phase, and re-running them would not re-derive anything. So
# half of what G02, G03, G04, G05 and G07 prove comes from the other phase -- and the
# FINAL manifest used to show `engine_evidence: {}` for three of them and say nothing
# at all about where the rest came from. A PASS next to an empty object reads as "this
# phase checked it".
#
# So the inherited keys are named, from the prior seal, and every inherited claim that
# can be re-checked against a sealed receipt is re-checked here. A receipt is checked
# with its digest recomputed from disk, so a re-assertion cannot be satisfied by a
# summary anybody could have written. What cannot be re-checked is left visible as
# unre-checked rather than counted as if it had been.

def _supersession_was_filtered(payload: dict[str, Any]) -> tuple[bool, str]:
    reasons = [row.get("reason") for row in (payload.get("filtered") or [])]
    return ("superseded_decision" in reasons,
            f"the sealed recall filtered {reasons}, and the premise of this case is that "
            f"the superseded Decision is filtered for exactly that reason")


def _empty_trace_was_empty(payload: dict[str, Any]) -> tuple[bool, str]:
    return (payload.get("status") == "empty",
            f"the sealed trace of the recall against a bank with no such record records "
            f"status {payload.get('status')!r}, not `empty`")


def _acl_battery_passed(payload: dict[str, Any]) -> tuple[bool, str]:
    checks = payload.get("checks") or []
    failed = [(row.get("check"), row.get("status"), row.get("error_code"))
              for row in checks if not row.get("passed")]
    return (bool(checks) and payload.get("deployment_mode") == "acl"
            and payload.get("server_saw_mode") == "acl" and not failed,
            f"the sealed ACL battery ran under {payload.get('deployment_mode')!r} with the "
            f"server reporting {payload.get('server_saw_mode')!r} and {len(failed)} "
            f"checks not passed: {failed[:4]}")


def _engine_recovered(payload: dict[str, Any]) -> tuple[bool, str]:
    degraded = (payload.get("degraded") or {}).get("status")
    recovered = (payload.get("recovered") or {}).get("status")
    return (degraded == "degraded" and recovered == "ok",
            f"the sealed recall records degraded={degraded!r} and recovered={recovered!r}; "
            f"VC-22 needs one unreachable and one healthy, in that order")


def _lifecycle_held(payload: dict[str, Any]) -> tuple[bool, str]:
    fault = payload.get("delete_fault") or {}
    statuses = ((payload.get("obsolete") or {}).get("status"),
                (payload.get("delete") or {}).get("status"),
                fault.get("status"), fault.get("recovered"))
    return (statuses[0] == "OBSOLETE" and statuses[1] == "DELETED"
            and statuses[2] == "PENDING" and statuses[3] == "DONE",
            f"the sealed lifecycle records obsolete/delete/delete_fault/recovered as "
            f"{statuses}; it needs OBSOLETE, DELETED, PENDING and DONE")


def _completed_run_recorded(payload: dict[str, Any]) -> tuple[bool, str]:
    return (bool(payload.get("interrupted_job")) and bool(payload.get("document_id")),
            f"the sealed completion marker records {payload.get('interrupted_job')!r} and "
            f"{payload.get('document_id')!r}: it has to name the interrupted job and its "
            f"document for a resumed run to have anything to re-check")


#: `(receipt kind, what it has to still say, and the check that says it)`. Only
#: receipts that exist on both phases are listed; anything the earlier phase asserted
#: without sealing a receipt is reported as inherited and not re-checked.
INHERITED_ASSERTIONS: dict[str, tuple[tuple[str, str, Any], ...]] = {
    "P4-G02": (("g02-recall",
                "the superseded Decision was filtered as superseded at the recall",
                _supersession_was_filtered),),
    "P4-G03": (("g03-empty-trace",
                "a recall against a bank with no such record left an `empty` trace",
                _empty_trace_was_empty),),
    "P4-G04": (("g04-acl",
                "the multiuser ACL battery passed every check under `acl` mode",
                _acl_battery_passed),),
    "P4-G05": (("g05-degraded",
                "an unreachable engine read as degraded and the engine recovered",
                _engine_recovered),),
    "P4-G06": (("g06-complete",
                "the completed run recorded the interrupted job and its document",
                _completed_run_recorded),),
    "P4-G07": (("g07-lifecycle",
                "the lifecycle ran obsolete, delete, fault and recovery to completion",
                _lifecycle_held),),
}


def mark_inherited_evidence(gate: Gate, record: CaseRecord,
                            prior_case: dict[str, Any] | None) -> None:
    """Say which of this case's engine evidence the earlier phase produced.

    Nothing here decides a verdict on its own except the re-assertions, and those are
    the load-bearing ones: a case whose earlier half no longer holds must fail rather
    than inherit a PASS. Keys with nothing to re-check are still named, because the
    honest statement is "this phase did not prove this", not silence.
    """
    if prior_case is None:
        return
    asserted = sorted(record.engine_evidence)
    inherited = sorted(set(prior_case.get("evidence_keys") or []) - set(asserted))
    reasserted = []
    for kind, claim, verdict in INHERITED_ASSERTIONS.get(record.case_id, ()):
        payload = gate.read_receipt(kind)["payload"]
        holds, why = verdict(payload)
        check(holds,
              f"{record.case_id} inherits this evidence from the earlier phase and the "
              f"sealed receipt no longer supports it: {why}. The receipt is "
              f"{gate.root / (kind + '.json')}; a summary of it inside a manifest is not "
              f"evidence, so the case fails rather than inherits a PASS it cannot show.")
        reasserted.append({"receipt": kind, "claim": claim})
    record.inherited_evidence = {
        "from_phase": "phase1",
        "inherited_evidence_keys": inherited,
        "asserted_in_this_phase": asserted,
        "reasserted_from_sealed_receipts": reasserted,
        "note": "the engine half of this case ran in phase 1 and is not re-run here; "
                "it was re-hashed from disk by the phase seal, and the claims listed "
                "under `reasserted_from_sealed_receipts` were checked again against "
                "their receipts. Keys with no receipt to check against are inherited and "
                "not re-verified in this phase."}


def _verify_prior_seal(work: Path, phase_name: str) -> dict[str, Any] | None:
    """Check the previous phase's seal before this phase runs a single case.

    A resumed run re-verifies packs and sessions. It does not re-run the engine halves,
    so the evidence for those lives in the earlier manifest -- and if the earlier
    manifest's receipts have been altered since, "PASS" here would be a claim about
    bytes nobody can still produce. Every sealed receipt is therefore re-hashed from
    disk and the earlier manifest's own digest is recorded, so this manifest names the
    phase it continues rather than standing alone.
    """
    if phase_name == "phase1":
        return None
    seal_path = work / "phase1-seal.json"
    if not seal_path.exists():
        raise Failed(
            f"resuming without a phase 1 seal at {seal_path}. The two phases are one "
            f"evidence chain: the final phase re-verifies what phase 1 built, so it "
            f"cannot be shown to continue anything unless phase 1 sealed its receipts. "
            f"Start a fresh work directory.")
    raw = seal_path.read_bytes()
    seal = json.loads(raw.decode("utf-8"))
    checked, mismatched, missing = 0, [], []
    for entry in seal.get("receipts") or []:
        path = Path(entry["path"])
        if not path.exists():
            missing.append(entry["path"])
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checked += 1
        if digest != entry["sha256"]:
            mismatched.append({"path": entry["path"], "sealed": entry["sha256"][:12],
                               "on_disk": digest[:12]})
    if missing or mismatched:
        raise Failed(
            f"phase 1 sealed evidence no longer matches the disk: "
            f"{len(mismatched)} altered, {len(missing)} missing"
            + (f"; altered: {mismatched[:4]}" if mismatched else "")
            + (f"; missing: {missing[:4]}" if missing else "")
            + ". Sealed receipts are write-once, so this means something outside the "
              "Gate edited them, or the run was tampered with. Re-run the affected "
              "phase in a fresh work directory rather than re-hashing anything.")
    manifest = Path(seal["manifest_path"])
    if not manifest.exists():
        raise Failed(f"the phase 1 manifest named by the seal is gone: {manifest}")
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    # The manifest named the seal, so the two must agree in that direction. A manifest
    # that does not name this seal belongs to some other run's phase.
    body = json.loads(manifest.read_text(encoding="utf-8"))
    named = (body.get("seal") or {}).get("sha256")
    seal_digest = hashlib.sha256(raw).hexdigest()
    if named != seal_digest:
        raise Failed(
            f"the phase 1 manifest names seal {str(named)[:12]}, but the seal on disk is "
            f"{seal_digest[:12]}. One work directory is one phase; this manifest and this "
            f"seal are not from the same run.")
    if seal.get("commit") != seal.get("candidate_commit"):
        raise Failed(f"the phase 1 seal disagrees with itself about the commit: "
                     f"{seal.get('commit')} vs {seal.get('candidate_commit')}")
    return {"seal_path": str(seal_path),
            "seal_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_path": str(manifest),
            "manifest_sha256": manifest_digest,
            "commit": seal["commit"],
            "receipts_verified": checked,
            "cases": seal.get("cases") or []}


def _write_phase_seal(work: Path, phase_name: str, manifest_path: Path,
                      records: list[CaseRecord], meta: dict[str, Any],
                      operator_config: dict[str, Any] | None = None
                      ) -> dict[str, Any]:
    """Seal this phase: its manifest's digest, and every receipt it produced.

    Write-once, like a receipt. A second seal under the same name would mean one work
    directory produced two phases, which is the situation the prior-seal check exists
    to catch, so it is refused rather than merged.
    """
    entries, per_case = [], {}
    if operator_config is not None:
        entries.append({"case_id": None, "kind": operator_config.get("kind"),
                        "path": operator_config["path"],
                        "sha256": operator_config["sha256"],
                        "bytes": operator_config.get("bytes")})
    for record in records:
        for receipt in record.receipts:
            path = Path(receipt["path"])
            if not path.exists():
                raise Failed(f"{record.case_id} cites receipt {receipt['path']}, which "
                             f"is not on disk; a manifest must not cite evidence it "
                             f"cannot produce")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != receipt["sha256"]:
                raise Failed(f"{record.case_id} cites receipt {receipt['path']} as "
                             f"{receipt['sha256'][:12]} but the file hashes to "
                             f"{digest[:12]}")
            entries.append({"case_id": record.case_id, "kind": receipt.get("kind"),
                            "path": str(path), "sha256": digest})
        for kind, artifact in sorted(record.sealed_artifacts.items()):
            path = Path(artifact["path"])
            if not path.exists():
                raise Failed(f"{record.case_id} sealed {kind!r} at {path}, which is not "
                             f"on disk; a seal must cover only artefacts that exist")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != artifact["sha256"]:
                raise Failed(f"{record.case_id} sealed {kind!r} as "
                             f"{artifact['sha256'][:12]} but the file hashes to "
                             f"{digest[:12]}")
            entries.append({"case_id": record.case_id, "kind": kind, "path": str(path),
                            "sha256": digest, "bytes": artifact.get("bytes")})
        per_case[record.case_id] = {
            "verdict": record.verdict,
            "packs": list(record.context_pack_ids),
            "receipts": [e["path"] for e in entries if e["case_id"] == record.case_id],
            "evidence_keys": sorted(record.engine_evidence),
            "sealed_artifacts": sorted(record.sealed_artifacts),
        }
    payload = {
        "phase": phase_name,
        "commit": meta["commit"],
        "candidate_commit": meta["commit"],
        "dirty": meta["dirty"],
        # The manifest path, deliberately without its digest. The manifest names the
        # seal, so it is written a second time after the seal exists -- which means a
        # digest taken here could never match the bytes left on disk. The chain runs
        # receipts -> seal -> manifest -> anchor, and each link is verified from the one
        # below it.
        "manifest_path": str(manifest_path),
        "operator_config": ({"path": operator_config["path"],
                             "sha256": operator_config["sha256"]}
                            if operator_config is not None else None),
        "receipts": entries,
        "cases": per_case,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    seal_path = work / f"{phase_name}-seal.json"
    if seal_path.exists():
        existing = json.loads(seal_path.read_text(encoding="utf-8"))
        sealed = {e["path"]: e["sha256"] for e in existing.get("receipts") or []}
        fresh = {e["path"]: e["sha256"] for e in entries}
        if sealed != fresh:
            changed = sorted(set(sealed) ^ set(fresh)) or [
                p for p in sealed if sealed[p] != fresh.get(p)]
            raise Failed(
                f"{phase_name} was already sealed in this work directory covering "
                f"{len(sealed)} receipts; this run covers {len(fresh)} and they differ at "
                f"{changed[:3]}. One work directory is one phase, and a phase's receipts "
                f"do not change after it is sealed.")
        return {"path": str(seal_path),
                "sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
                "receipts_sealed": len(entries)}
    seal_path.write_text(body, encoding="utf-8")
    os.chmod(seal_path, 0o600)
    return {"path": str(seal_path),
            "sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
            "receipts_sealed": len(entries)}


def _seal_operator_config(work: Path, source: Path,
                          config: dict[str, Any]) -> dict[str, Any]:
    """Copy the operator configuration into the work directory, once, byte for byte.

    `op.json` is the sole basis for `real_agent_session`, the bank namespace and the
    dependency commits the manifest reports, and it used to live only wherever the
    operator happened to put it. Nothing on disk tied those values to the run that used
    them: editing the file afterwards changed what the manifest is *about* without
    changing a byte of the manifest. So the run seals its own copy, write-once, and
    checks that the values it actually used are the ones in that copy.

    The digest is of the original bytes, not of a re-serialisation: an operator's
    formatting is part of the file they handed over.
    """
    body = source.read_bytes()
    sealed = work / "op.json"
    if sealed.exists():
        if sealed.read_bytes() != body:
            raise Failed(
                f"the operator configuration sealed in this work directory at {sealed} is "
                f"not the file this run was given ({source}). One work directory is one "
                f"run with one configuration; re-pointing it at a different file would "
                f"mean the manifest reports a deployment it did not use.")
        write = "unchanged"
    else:
        sealed.write_bytes(body)
        os.chmod(sealed, 0o600)
        write = "created"
    try:
        described = json.loads(sealed.read_bytes().decode("utf-8"))
    except ValueError as exc:
        raise Failed(f"the sealed operator configuration at {sealed} is not readable "
                     f"JSON: {exc}")
    if described != config:
        raise Failed(
            f"the operator configuration sealed at {sealed} does not describe the "
            f"configuration this run used. The manifest reports real_agent_session, the "
            f"bank namespace and the dependency commits, so those values have to be the "
            f"ones in the sealed file rather than the ones the runner happened to hold.")
    return {"path": str(sealed), "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body), "kind": "operator-config", "write": write}


def _write_manifest_anchor(work: Path, phase_name: str, manifest_path: Path,
                           meta: dict[str, Any]) -> dict[str, Any]:
    """Anchor this manifest's digest in a second, independent file.

    The chain runs receipts -> seal -> manifest, and every link is checked from the one
    below it -- except the manifest, which nothing on disk covered. Rewriting its
    `verdict` changed no digest anywhere, so a FAIL could be edited into a PASS with no
    trace. This is the missing anchor: it is written after the manifest, so it can hold
    a digest of bytes that already exist, and it is write-once, so it cannot be quietly
    re-pointed at an edited manifest.

    It is named for the manifest it anchors rather than for the phase alone. One work
    directory can legitimately produce a second manifest for the same phase -- a
    re-verification of the same sealed receipts -- and re-anchoring a fixed name would
    either refuse that or overwrite the first anchor, which is the gap this closes.
    """
    anchor = work / f"{phase_name}-manifest-{manifest_path.stem}.sha256"
    payload = {"phase": phase_name,
               "manifest_path": str(manifest_path),
               "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
               "commit": meta["commit"],
               "dirty": meta["dirty"]}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if anchor.exists():
        if anchor.read_text(encoding="utf-8") != body:
            raise Failed(
                f"the anchor for {manifest_path.name} already exists at {anchor} and names "
                f"different bytes. An anchor is written once: either the manifest was "
                f"edited after this run finished it, or something outside the Gate is "
                f"rewriting evidence.")
        return {**payload, "path": str(anchor), "write": "unchanged"}
    anchor.write_text(body, encoding="utf-8")
    os.chmod(anchor, 0o600)
    return {**payload, "path": str(anchor), "write": "created"}


def _evidence_scope(work: Path, records: list[CaseRecord],
                    operator_config: dict[str, Any]) -> dict[str, Any]:
    """What this run's evidence covers, and what it does not.

    Stated in the manifest rather than left to the reader, because a Gate report that
    lists digests without saying what was hashed is easy to read as stronger than it is.
    In particular: the databases are *hashed*, not sealed. Nothing here claims to hold
    their contents, and a reviewer who needs to know that has to be able to read it off
    the manifest instead of inferring it from silence.
    """
    databases = []
    for record in records:
        path = work / record.case_id / "core.db"
        if path.is_file():
            databases.append({"case_id": record.case_id, "path": str(path),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {
        "sealed_byte_for_byte": {
            "receipts": "every gNN-*.json receipt under the case directories: written "
                        "once, digest recomputed from disk on read-back, and listed in "
                        "the phase seal",
            "session_transcripts": "session-*.transcript.json: a byte-for-byte copy of "
                                   "each agent session receipt as it was received",
            "injected_packs": "injected-*.pack.json: the exact pack bytes an agent was "
                              "shown, copied once and refused if the pack is re-rendered",
            "operator_config": f"{operator_config['path']} "
                               f"({operator_config['sha256'][:12]}): the operator "
                               f"configuration this run used, byte for byte",
            "phase_seal": "the phase seal itself: every receipt it covers, with its digest",
            "manifest_anchor": "phase*-manifest-*.sha256 beside the work directory: "
                               "written once after the manifest, holding that "
                               "manifest's own digest, so a rewritten verdict is "
                               "visible against it",
        },
        "hashed_not_content_sealed": {
            "databases": databases,
            "note": "each case's Core database is digested here and in the phase seal. "
                    "The contents are NOT sealed, are not copied, and are not covered "
                    "byte for byte: a digest says what the file was when this manifest "
                    "was written, and nothing more.",
        },
        "out_of_scope": [
            "the engine's own store: documents and units are read back through the "
            "transport and compared by document id and unit count, not sealed",
            "the agent sessions themselves: only the receipt each session wrote and the "
            "transcript copy of it are evidence here",
            "workspace files behind an Evidence `artifact_uri`: Core stores their hash, "
            "and this Gate keeps no sealed copy of the bytes",
            "session tool Evidence rows: they are Core rows like any other, covered "
            "only by the case database digest, and bound to the session's declared "
            "actions by command, output, status and task at verification time",
            "the model: nothing in this run claims the engine's extractor was correct, "
            "only that Core verified the provenance it was given",
        ],
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
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, mode=0o700)
    # exist_ok only under --resume, which already proved the directory belongs to this
    # run; a fresh run must still create its own.
    work.mkdir(parents=True, mode=0o700, exist_ok=args.resume)
    selected = args.case or list(CASES)
    # A resumed run continues the same run's phase 1. Its seal is checked here, before
    # any case executes, so a run whose earlier evidence was altered stops before it can
    # report anything rather than after.
    phase_name = "phase2" if args.resume else "phase1"
    try:
        prior = _verify_prior_seal(work, phase_name)
    except Failed as failure:
        print(json.dumps({"result": None, "verdict": "FAIL",
                          "prior_phase": "unverifiable", "reason": str(failure)}))
        return 1
    # And the operator configuration is sealed into the work directory at the start, so
    # the manifest's inputs are evidence rather than a claim about a file that lived
    # somewhere else. Before any case runs, like the seal above it.
    try:
        operator_config = _seal_operator_config(work, config_path, config)
    except Failed as failure:
        print(json.dumps({"result": None, "verdict": "FAIL",
                          "operator_config": "unsealed", "reason": str(failure)}))
        return 1

    records: list[CaseRecord] = []
    # Captured from the first `open()`: each case's `finally` closes its Gate and
    # `close()` restores whatever `_set_env` saw, so reading the namespace after
    # the loop — or after the probe below — reports "" even though every case ran
    # with it in effect.
    namespace_used = ""
    for case_id in selected:
        record = CaseRecord(case_id, "BLOCKED", mode="not-run")
        gate = None
        # What the earlier phase recorded for this case, so the engine half it ran can
        # be named as inherited and re-asserted rather than silently absent.
        prior_case = ((prior or {}).get("cases") or {}).get(case_id)
        try:
            gate = Gate(work, config, case_id)
            gate.open()
            namespace_used = scope.bank_namespace()
            gate.require_engine()
            CASES_BY_ID[case_id](gate, record)
            # Only after the case returned: a case that raised has not earned an
            # inherited-evidence claim, and its verdict is already FAIL or BLOCKED.
            mark_inherited_evidence(gate, record, prior_case)
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
        "operator_config": operator_config,
        "evidence_scope": _evidence_scope(work, records, operator_config),
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
            "`evidence_scope` states what is sealed byte for byte and what is only "
            "digested; the case databases are digested, not sealed.",
        ],
    }
    result_path = out / f"p4-gate-{int(time.time())}.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(result_path, 0o600)

    seal = _write_phase_seal(work, phase_name, result_path, records, meta, operator_config)
    payload["seal"] = {"path": seal["path"], "sha256": seal["sha256"],
                       "phase": phase_name,
                       "receipts_sealed": seal["receipts_sealed"]}
    if prior is not None:
        payload["prior_phase"] = prior
    # The manifest is rewritten to carry the seal, so its own digest changes: the seal
    # is written *after* the manifest and hashes the manifest as first written, and the
    # manifest records the seal's digest. Each names the other; neither is self-hashed,
    # which is the only ordering that does not require a fixed point. The anchor is
    # written after that, and it is the one link that names the manifest's final bytes.
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(result_path, 0o600)
    try:
        anchor = _write_manifest_anchor(work, phase_name, result_path, meta)
    except Failed as failure:
        # The manifest is deliberately left alone: rewriting it is exactly the thing
        # the anchor exists to detect. The run reports FAIL and the operator is told
        # which anchor disagreed.
        print(json.dumps({"result": str(result_path), "verdict": "FAIL",
                          "commit": meta["commit"], "dirty": meta["dirty"],
                          "anchor": "refused", "reason": str(failure)}))
        return 1
    print(json.dumps({"result": str(result_path), "verdict": payload["verdict"],
                      "commit": meta["commit"], "dirty": meta["dirty"],
                      "engine_reachable": payload["engine"]["reachable"],
                      "seal": seal["sha256"][:12],
                      "anchor": anchor["manifest_sha256"][:12],
                      "anchor_path": anchor["path"],
                      "prior_phase_verified": (prior or {}).get("receipts_verified")}))
    # 0 PASS, 2 nothing could run, 1 something failed.
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[payload["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
