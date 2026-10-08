"""Bank derivation and project authorisation (ADR 0014 §3, design §7).

Two rules from the design that this module exists to make mechanical:

* **The Core chooses the bank.** `jasmine-global` and
  `jasmine-project-{project_id}` are derived from a *validated* project/task
  pair. There is no request field, no query parameter and no agent-supplied
  string that becomes a bank id, and `require_bank` refuses any bank that was
  not produced by `resolve_banks` in the same call. The working directory is
  never consulted, because a project is a Core object, not a path, and a V0
  project binding must not leak in.
* **Project A's memory cannot appear in project B's recall, Inspector listing,
  or job listing.** That is enforced in SQL by `project_id` columns, and here by
  refusing to construct a bank list that does not contain the requested project.

## Deployment mode: the one thing a reviewer must read

`jasmine-global` is global *within one trusted usage domain*. It is not shared
between users. What P4 can and cannot prove about authorisation therefore depends
on which mode the deployment is in, and the mode is recorded rather than assumed:

* `single_user` (default) — one trusted operator owns the Core. Every project
  in the database belongs to that operator, so an authenticated actor may read
  all of them. Cross-*project* isolation still holds absolutely: a recall for
  project B never returns a project A item, and a job for A is never listed
  under B. What this mode does **not** provide, and what no Gate may claim from
  it, is a user-level ACL: there is no second user to refuse. G04 in this mode
  proves scope isolation and bank-injection refusal only.
* `acl` — `memory_project_actors` is authoritative and an actor with no row for
  a project is refused. This mode requires an administrator to populate the
  table, and `require_mode` refuses to *serve* an `acl` deployment with an empty
  table, because an empty ACL table that defaults to "allow" is the exact
  misconfiguration this mode exists to prevent.

A UI filter, a `project_id` in a request body, or a Core API scope is never
treated as proof of authorisation. Only `acl` mode plus a row is.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import errors, ids
from .types import GLOBAL_BANK_ID, GLOBAL_BANK_RE, PROJECT_BANK_PREFIX, PROJECT_BANK_RE, BankRef

SINGLE_USER = "single_user"
ACL = "acl"
DEPLOYMENT_MODES = (SINGLE_USER, ACL)

MODE_ENV_VAR = "JASMINE_MEMORY_DEPLOYMENT_MODE"

#: Optional prefix on the bank id *as the engine sees it*.
#:
#: Empty by default, so a Core that never sets it addresses exactly the banks it
#: addressed before this existed. A Gate sets it from the operator's
#: `bank_suffix`, which is what makes that suffix mean anything: without it the
#: only address two runs share is `jasmine-global`, and a Gate reading it would
#: be reading the deployment's global bank while claiming its own namespace.
#:
#: It applies at the **transport boundary** (`engine_bank_id`) and never to
#: `BankRef.bank_id`. Core's own ids stay canonical — `jasmine-global`,
#: `jasmine-project-{prj}` — because they are part of the scope model other
#: phases read: P3's Memory provider validates exactly those two shapes, and a
#: namespaced deployment is still obliged to hand it canonical ids. Two Gate runs
#: are isolated because their *engine* banks differ, which is where the data
#: lives; what Core calls them is its own business.
NAMESPACE_ENV_VAR = "JASMINE_MEMORY_BANK_NAMESPACE"
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def bank_namespace(env: dict[str, str] | None = None) -> str:
    """The prefix this deployment puts on bank ids, or `""` for none.

    Read on every derivation rather than at import, because a process that
    configures itself after import — a Gate — has to be able to set it before
    the first bank is built. A malformed value is refused rather than ignored:
    a silently dropped namespace would fall back to un-namespaced ids, which is
    the exact sharing this is meant to prevent.
    """
    import os

    source = os.environ if env is None else env
    value = (source.get(NAMESPACE_ENV_VAR) or "").strip()
    if value and not _NAMESPACE_RE.fullmatch(value):
        raise errors.InvalidRequest(
            f"{NAMESPACE_ENV_VAR} must be 1 to 64 characters of "
            "letters, digits, '.', '_' or '-'",
            field=NAMESPACE_ENV_VAR, value=value)
    return value


def engine_bank_id(bank: BankRef) -> str:
    """The bank id to address the engine with.

    The one place the configured namespace is applied. Every engine path goes
    through `hindsight.HindsightTransport._bank_path`, so there is one address
    to get right rather than one per operation.
    """
    namespace = bank_namespace()
    return f"{namespace}{bank.bank_id}" if namespace else bank.bank_id


def deployment_mode(env: dict[str, str] | None = None) -> str:
    """The configured mode, defaulting to the trusted single-user domain."""
    import os

    source = os.environ if env is None else env
    mode = (source.get(MODE_ENV_VAR) or SINGLE_USER).strip()
    if mode not in DEPLOYMENT_MODES:
        raise errors.InvalidRequest(
            f"{MODE_ENV_VAR} must be one of {', '.join(DEPLOYMENT_MODES)}",
            field=MODE_ENV_VAR, value=mode,
        )
    return mode


def global_bank() -> BankRef:
    return BankRef(bank_id=GLOBAL_BANK_ID, scope_kind="global", project_id=None)


def project_bank(project_id: str) -> BankRef:
    if not ids.is_id(project_id, "prj"):
        raise errors.InvalidRequest("project_id must be a prj_ id", field="project_id",
                                    value=project_id)
    return BankRef(bank_id=f"{PROJECT_BANK_PREFIX}{project_id}", scope_kind="project",
                   project_id=project_id)


def is_valid_bank_id(bank_id: object) -> bool:
    """Whether a string is *shaped* like one of our two bank kinds.

    Shape only. This is a filter against path traversal and against a
    V0-style bank name arriving from a remote response; it is not an
    authorisation check and never used as one.

    These are Core's ids, which are canonical whatever namespace the engine is
    addressed with; `engine_bank_id` is where the two forms differ, and it is
    applied on the wire rather than here.
    """
    if not isinstance(bank_id, str):
        return False
    return bool(GLOBAL_BANK_RE.fullmatch(bank_id)
                or PROJECT_BANK_RE.fullmatch(bank_id))


def scope_of(bank_id: object) -> BankRef | None:
    """Recover the scope of a well-formed bank id, or None if it is not ours."""
    if not isinstance(bank_id, str):
        return None
    if GLOBAL_BANK_RE.fullmatch(bank_id):
        return global_bank()
    match = PROJECT_BANK_RE.fullmatch(bank_id)
    if match:
        return project_bank(match.group(1))
    return None


def require_bank(bank_id: object, allowed: tuple[BankRef, ...]) -> BankRef:
    """Refuse any bank outside the set derived for this call.

    This is the anti-injection boundary. A caller that reaches a Memory API with
    a bank id, or a remote row that claims a bank id, gets a refusal rather than
    a query: the only banks that exist for a given project are the ones
    `resolve_banks` produced.
    """
    permitted = {bank.bank_id for bank in allowed}
    if not isinstance(bank_id, str) or bank_id not in permitted:
        raise errors.NotFound("bank", bank_id if isinstance(bank_id, str) else "invalid")
    for bank in allowed:
        if bank.bank_id == bank_id:
            return bank
    raise errors.NotFound("bank", bank_id)  # pragma: no cover - unreachable


class ProjectAccess:
    """Answers "may this actor work with this project", per deployment mode."""

    def __init__(self, conn: sqlite3.Connection, *, mode: str = SINGLE_USER) -> None:
        if mode not in DEPLOYMENT_MODES:
            raise ValueError(f"unknown memory deployment mode: {mode!r}")
        self._conn = conn
        self.mode = mode

    def require(self, *, project_id: str, actor_id: str) -> None:
        """Raise unless the actor may use this project.

        A project the actor may not use is reported as `not_found`, not as a
        403. Existence of another project's memory is itself information, and
        ADR 0003 freezes 403 as a decision about *authority* that the audit log
        records; a 404 keeps the Inspector and the job list from becoming a
        project-enumeration oracle.
        """
        if not ids.is_id(project_id, "prj"):
            raise errors.InvalidRequest("project_id must be a prj_ id", field="project_id",
                                        value=project_id)
        if not self.project_exists(project_id):
            raise errors.NotFound("project", project_id)
        if self.mode == SINGLE_USER:
            # Documented trust premise of the single-user domain, not an ACL
            # decision. See the module docstring.
            return
        row = self._conn.execute(
            "SELECT 1 FROM memory_project_actors WHERE project_id=? AND actor_id=?",
            (project_id, actor_id),
        ).fetchone()
        if row is None:
            raise errors.NotFound("project", project_id)

    def may_read(self, *, project_id: str, actor_id: str) -> bool:
        try:
            self.require(project_id=project_id, actor_id=actor_id)
        except errors.CoreError:
            return False
        return True

    def project_exists(self, project_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
        ).fetchone() is not None

    def require_mode_ready(self) -> None:
        """Refuse to serve `acl` mode with no grants at all.

        A table that is empty because nobody was granted access, and a table that
        is empty because it was never populated, are indistinguishable at
        runtime — and the second must not silently behave like the first. An
        operator in `acl` mode therefore has to make the first case true.
        """
        if self.mode != ACL:
            return
        count = self._conn.execute("SELECT COUNT(*) AS n FROM memory_project_actors").fetchone()["n"]
        if not count:
            raise errors.MemoryDeploymentNotReady(
                "memory deployment mode is 'acl' but no project grant exists; "
                "grant project access before serving, or run as 'single_user'",
                mode=self.mode,
            )

    def describe(self) -> dict[str, Any]:
        """Safe description for the health API. Names no actor and no project."""
        return {
            "mode": self.mode,
            "acl_enforced": self.mode == ACL,
            "note": (
                "single_user trusts one operator for every project in this database; "
                "it is not a user-level ACL"
                if self.mode == SINGLE_USER else
                "memory_project_actors is authoritative; an actor without a grant is refused"
            ),
        }


def resolve_banks(conn: sqlite3.Connection, *, project_id: str, task_id: str | None,
                  actor_id: str, mode: str = SINGLE_USER) -> tuple[BankRef, ...]:
    """The banks a query for this project may touch, most specific first.

    Order is significant and is the merge order: the project bank comes before
    the global bank, so when both banks return the same statement the
    project-specific provenance is the one that survives the de-duplication.
    A stated `task_id` must belong to the stated project; a mismatch is refused
    rather than resolved in favour of either side.
    """
    access = ProjectAccess(conn, mode=mode)
    access.require(project_id=project_id, actor_id=actor_id)
    if task_id is not None:
        if not ids.is_id(task_id, "tsk"):
            raise errors.InvalidRequest("task_id must be a tsk_ id", field="task_id",
                                        value=task_id)
        row = conn.execute("SELECT project_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise errors.NotFound("task", task_id)
        if row["project_id"] != project_id:
            # The caller is asking about a task in a different project. Answer
            # as if the task did not exist there rather than confirming it does.
            raise errors.NotFound("task", task_id)
    return (project_bank(project_id), global_bank())


def resolve_scope_for_item(conn: sqlite3.Connection, *, project_id: str | None,
                           scope_kind: str) -> tuple[BankRef, ...]:
    """The banks a *retain* may write to, from the item's own scope.

    `global` here means the item was produced by a source whose own committed
    scope is global. A project-scoped source can only ever reach the project
    bank, which is the property `eligibility.resolve_destination` relies on
    when it builds a Memory item.

    `conn` is accepted for symmetry with the other accessors and is not used: a
    bank is derived from ids, never from a row, so this needs no lock and cannot
    observe a half-written item.
    """
    del conn
    if scope_kind == "global":
        return (global_bank(),)
    if scope_kind != "project":
        raise errors.InvalidRequest("scope_kind must be global or project", field="scope_kind")
    if not ids.is_id(project_id, "prj"):
        raise errors.InvalidRequest("project_id must be a prj_ id", field="project_id",
                                    value=project_id)
    return (project_bank(project_id),)
