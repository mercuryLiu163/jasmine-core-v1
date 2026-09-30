"""The P4 HTTP surface (ADR 0009 §7).

Two paths are the original design and keep their exact spelling: `GET
/v1/memory/jobs` and `POST /v1/memory/jobs/{id}/retry` (design module §12). The
rest are the extensions the taskbook requires for the Inspector and the
lifecycle, and none of them can write a bank.

**There is no public retain endpoint, and no public bank selector.** Content
enters the engine only through the worker acting on a job the enqueuer derived
from committed truth, and the bank is derived from the item's own scope. An
agent therefore cannot retain arbitrary text, cannot choose `jasmine-global`, and
cannot promote project content into it — the three things design §7 singles out
as agent-forbidden. P6 consumes these endpoints; it does not get a privileged
one.

**Reflect is one endpoint, human/system only, behind `memory:reflect`.** It is
the only path that runs the engine's generator, and it is unreachable from a
Context build. An ordinary complicated question goes to `recall` and stays there.

Project authorisation is decided in `scope.ProjectAccess` from the authenticated
actor, never from a body field or a query parameter. A project the actor may not
use is reported as `not_found`, so the Inspector and the job list cannot be used
to enumerate another project's work.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING, Any, Callable

from .. import auth, errors, ids
from ..api.request import Request, Response
from . import health as health_module
from . import jobs as jobs_module
from . import scope as scope_module
from .adapter import MemoryAdapter
from .hindsight import config_from_env
from .lifecycle import MemoryLifecycle, lifecycle_summary
from .store import MemoryStore

if TYPE_CHECKING:  # pragma: no cover - import cycle broken at runtime
    # `api.handlers` imports this module to extend its route table, so the
    # reverse import exists only for type checking. `routes()` receives the
    # `Route` class as an argument for the same reason: the P4 surface must be
    # importable on its own.
    from ..api.handlers import Core

Handler = Callable[..., Response]


class MemoryServices:
    """The P4 stores, built once per `Core` and reused across requests.

    Constructed lazily from the `Core` connection rather than added to
    `handlers.Core.__init__`, so the P4 surface is a self-contained addition to
    the shared file instead of a set of edits to it.
    """

    def __init__(self, core: "Core", *, mode: str | None = None) -> None:
        self.conn: sqlite3.Connection = core.conn
        self.schema_version = core.schema_version
        self.mode = mode or scope_module.deployment_mode()
        self.store = MemoryStore(self.conn, schema_version=self.schema_version)
        self.jobs = jobs_module.JobStore(self.conn, self.store)
        self.lifecycle = MemoryLifecycle(self.conn, self.store)
        self.access = scope_module.ProjectAccess(self.conn, mode=self.mode)
        self._transport = None
        self._probed = False
        # An `acl` deployment with no grants is a misconfiguration, not a
        # permissive default; refuse the first Memory request rather than serve
        # the whole database to whoever holds any key.
        self.access.require_mode_ready()

    @property
    def transport(self):
        """The engine transport, built from the operator's environment.

        Built once per process. The configuration is read from the environment
        rather than per request so a credential cannot be rotated underneath an
        in-flight request, and so no request body, query parameter or database
        row can influence which engine is contacted.
        """
        if not self._probed:
            self._probed = True
            config = config_from_env()
            if config is not None:
                from .hindsight import HindsightTransport

                self._transport = HindsightTransport(config)
        return self._transport

    def adapter(self) -> MemoryAdapter:
        return MemoryAdapter(self.conn, self.store, self.transport, mode=self.mode,
                             schema_version=self.schema_version)

    def health(self) -> dict[str, Any]:
        return health_module.report(self.conn, self.store, self.transport, self.jobs,
                                    mode=self.mode)


def services(core: "Core") -> MemoryServices:
    """One `MemoryServices` per `Core`, cached on the Core itself.

    Attached to the instance rather than kept in a module-level mapping keyed by
    `id(conn)`: an `id` can be reused after a connection is collected, which
    would hand a later request another database's services — and a stale
    deployment mode read at first use would outlive an operator's change to the
    environment. The `Core` is created per `Application`, so its lifetime is
    exactly the right one.
    """
    found = getattr(core, "memory_services", None)
    if found is None:
        found = MemoryServices(core)
        core.memory_services = found
    return found


# -- handlers -----------------------------------------------------------------


def list_jobs(request: Request, core: "Core", principal: auth.Principal,
              match: re.Match[str]) -> Response:
    svc = services(core)
    project_id = _optional(request, "project_id")
    actor_kind = core.actor_kind(principal.actor_id)
    if project_id is not None:
        svc.access.require(project_id=project_id, actor_id=principal.actor_id)
    jobs = svc.jobs.jobs(
        project_id=project_id,
        status=_optional(request, "status"),
        kind=_optional(request, "kind"),
        memory_type=_optional(request, "memory_type"),
        limit=_int(request, "limit", 100),
        after_job_id=_optional(request, "after_job_id"),
    )
    if actor_kind not in ("human", "system"):
        # An agent key may read jobs, but only for a project it may read, and the
        # error summaries can carry engine text, so they are withheld.
        jobs = [{**job, "safe_error_summary": None} for job in jobs]
    return Response(200, {"jobs": jobs, "count": len(jobs),
                          "counts": svc.jobs.counts(project_id=project_id)})


def get_job(request: Request, core: "Core", principal: auth.Principal,
            match: re.Match[str]) -> Response:
    svc = services(core)
    job_id = match.group(1)
    _require_id(job_id, "mjb", "job_id")
    job = svc.jobs.job(job_id)
    if job is None or not _may_see(svc, principal, project_id=job["project_id"],
                                   bank_id=job["bank_id"]):
        raise errors.NotFound("memory_job", job_id)
    return Response(200, {"job": job, "attempts": svc.jobs.attempts(job_id)})


def retry_job(request: Request, core: "Core", principal: auth.Principal,
              match: re.Match[str]) -> Response:
    svc = services(core)
    job_id = match.group(1)
    _require_id(job_id, "mjb", "job_id")
    job = svc.jobs.job(job_id)
    if job is None or not _may_see(svc, principal, project_id=job["project_id"],
                                   bank_id=job["bank_id"]):
        raise errors.NotFound("memory_job", job_id)
    body = _body(request, {"host_id", "event_id"})
    result = svc.jobs.request_retry(
        job_id, actor_id=principal.actor_id,
        actor_kind=core.actor_kind(principal.actor_id),
        host_id=body.get("host_id", ""), event_id=body.get("event_id"),
    )
    return Response(200, result)


def list_items(request: Request, core: "Core", principal: auth.Principal,
               match: re.Match[str]) -> Response:
    """The Memory Inspector list: non-authoritative, with source links."""
    svc = services(core)
    project_id = _optional(request, "project_id")
    if project_id is not None:
        svc.access.require(project_id=project_id, actor_id=principal.actor_id)
    items = svc.store.items(
        project_id=project_id,
        status=_optional(request, "status"),
        memory_type=_optional(request, "memory_type"),
        bank_id=_optional(request, "bank_id"),
        task_id=_optional(request, "task_id"),
        limit=_int(request, "limit", 100),
        after_memory_id=_optional(request, "after_memory_id"),
    )
    rows = [{**item, "lifecycle": lifecycle_summary(item)} for item in items]
    return Response(200, {"items": rows, "count": len(rows), "non_authoritative": True})


def get_item(request: Request, core: "Core", principal: auth.Principal,
             match: re.Match[str]) -> Response:
    svc = services(core)
    memory_id = match.group(1)
    _require_id(memory_id, "mem", "memory_id")
    detail = svc.lifecycle.detail(memory_id, actor_id=principal.actor_id, mode=svc.mode)
    return Response(200, {**detail, "non_authoritative": True})


def _lifecycle_command(action: str) -> Handler:
    def handler(request: Request, core: "Core", principal: auth.Principal,
                match: re.Match[str]) -> Response:
        svc = services(core)
        memory_id = match.group(1)
        _require_id(memory_id, "mem", "memory_id")
        body = _body(request, {"expected_revision", "host_id", "event_id", "reason"})
        result = getattr(svc.lifecycle, action)(
            memory_id, body, actor_id=principal.actor_id,
            actor_kind=core.actor_kind(principal.actor_id),
            host_id=body.get("host_id", ""),
        )
        return Response(200, {**result, "non_authoritative": True})

    return handler


def list_traces(request: Request, core: "Core", principal: auth.Principal,
                match: re.Match[str]) -> Response:
    """Recall/Reflect traces. Query text is not stored; the hash and length are."""
    svc = services(core)
    project_id = _optional(request, "project_id")
    task_id = _optional(request, "task_id")
    if project_id is not None:
        svc.access.require(project_id=project_id, actor_id=principal.actor_id)
    elif task_id is not None and not ids.is_id(task_id, "tsk"):
        raise errors.InvalidRequest("task_id must be a tsk_ id", field="task_id", value=task_id)
    traces = svc.store.traces(project_id=project_id, task_id=task_id,
                              limit=_int(request, "limit", 50),
                              after_trace_id=_optional(request, "after_trace_id"))
    return Response(200, {"traces": traces, "count": len(traces)})


def get_trace(request: Request, core: "Core", principal: auth.Principal,
              match: re.Match[str]) -> Response:
    svc = services(core)
    trace_id = match.group(1)
    _require_id(trace_id, "mtr", "trace_id")
    trace = svc.store.trace(trace_id)
    if trace is None or not _may_see(svc, principal, project_id=trace["project_id"],
                                     bank_id=None):
        raise errors.NotFound("memory_trace", trace_id)
    return Response(200, {"trace": trace})


def memory_health(request: Request, core: "Core", principal: auth.Principal,
                  match: re.Match[str]) -> Response:
    """Memory availability, reported apart from Core liveness."""
    svc = services(core)
    return Response(200, {**svc.health(), "non_authoritative": True})


def reflect(request: Request, core: "Core", principal: auth.Principal,
            match: re.Match[str]) -> Response:
    """Explicit historical synthesis. The only path to the engine's generator.

    Requires `memory:reflect`, which `auth.issue_key` will not grant to an agent.
    The result is labelled non-authoritative in the response body, and the same
    directive guard that filters recalled text is applied to it.
    """
    from .types import ReflectRequest

    svc = services(core)
    body = _body(request, {"project_id", "task_id", "query", "max_tokens", "host_id", "event_id"})
    project_id = body.get("project_id")
    if not ids.is_id(project_id, "prj"):
        raise errors.InvalidRequest("project_id must be a prj_ id", field="project_id")
    svc.access.require(project_id=project_id, actor_id=principal.actor_id)
    query = body.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise errors.InvalidRequest("query must be nonempty text of at most 4000 characters",
                                    field="query")
    outcome = svc.adapter().reflect(ReflectRequest(
        project_id=project_id, actor_id=principal.actor_id, query=query,
        task_id=body.get("task_id"), session_id=None,
        max_tokens=body.get("max_tokens") if isinstance(body.get("max_tokens"), int) else None,
        explicit_user_request=True, requested_by=principal.actor_id,
    ))
    return Response(200, outcome.as_dict())


# -- helpers ------------------------------------------------------------------


def _body(request: Request, allowed: set[str]) -> dict[str, Any]:
    body = request.json_body()
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)
    return body


def _optional(request: Request, name: str) -> str | None:
    return request.param(name) or None


def _int(request: Request, name: str, default: int) -> int:
    raw = _optional(request, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise errors.InvalidRequest(f"{name} must be an integer", field=name, value=raw) from exc


def _require_id(value: str, prefix: str, field: str) -> None:
    if not ids.is_id(value, prefix):
        raise errors.InvalidRequest(f"{field} must be a {prefix}_ id", field=field, value=value)


def _may_see(svc: MemoryServices, principal: auth.Principal, *, project_id: str | None,
             bank_id: str | None) -> bool:
    """Whether this actor may see a row belonging to this project/bank.

    A row with no project belongs to the global bank, which is shared across
    this Core's projects, so any authorised actor may read it. A project row is
    gated by the same check the list endpoints use, so fetching by id cannot be
    used to reach around a filter — and a refusal is indistinguishable from
    "does not exist", so this is not a project-enumeration oracle.
    """
    if project_id is None:
        return bank_id == scope_module.GLOBAL_BANK_ID
    return svc.access.may_read(project_id=project_id, actor_id=principal.actor_id)


def routes(route_cls) -> tuple:
    """The P4 routes, built from the caller's `Route` class.

    Built on demand rather than at import so this module has no import-time
    dependency on `api.handlers`; `handlers` calls this once while assembling
    its table.
    """
    return (
        route_cls("GET", re.compile(r"^/v1/memory/jobs$"), list_jobs, "memory:read"),
        route_cls("GET", re.compile(r"^/v1/memory/jobs/([^/]+)$"), get_job, "memory:read"),
        route_cls("POST", re.compile(r"^/v1/memory/jobs/([^/]+)/retry$"), retry_job,
                  "memory:manage"),
        route_cls("GET", re.compile(r"^/v1/memory/items$"), list_items, "memory:read"),
        route_cls("GET", re.compile(r"^/v1/memory/items/([^/]+)$"), get_item, "memory:read"),
        route_cls("POST", re.compile(r"^/v1/memory/items/([^/]+)/obsolete$"),
                  _lifecycle_command("obsolete"), "memory:manage"),
        route_cls("POST", re.compile(r"^/v1/memory/items/([^/]+)/delete$"),
                  _lifecycle_command("delete"), "memory:manage"),
        route_cls("POST", re.compile(r"^/v1/memory/items/([^/]+)/pin$"),
                  _lifecycle_command("pin"), "memory:manage"),
        route_cls("POST", re.compile(r"^/v1/memory/items/([^/]+)/unpin$"),
                  _lifecycle_command("unpin"), "memory:manage"),
        route_cls("GET", re.compile(r"^/v1/memory/traces$"), list_traces, "memory:read"),
        route_cls("GET", re.compile(r"^/v1/memory/traces/([^/]+)$"), get_trace, "memory:read"),
        route_cls("GET", re.compile(r"^/v1/memory/health$"), memory_health, "memory:read"),
        route_cls("POST", re.compile(r"^/v1/memory/reflect$"), reflect, "memory:reflect"),
    )
