"""Core API handlers (ADR 0003 §1.2).

Every write goes through the storage layer, so the Event-first /
projection-second ordering and the idempotency rules of P0-02 hold here
unchanged. This module adds exactly what P0-03 is for: the transport surface,
authentication, scopes, and audit.

P0 exposes no PUT/PATCH/DELETE. That is not an omission: the Task state machine
is P1, and there is no method that could contradict an append-only Event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .. import SCHEMA_VERSION, __version__, audit, auth, authority, db, errors, evidence, ids, interpretations, objects, registry, state, resolver, reviews
from ..migrations import applied_migrations, check_version, current_version
from ..models import NewEvent, NewObject
from ..continuity import ContinuityStore
from ..context.builder import ContextBuilder
from ..adapter.requirements import NativeAdapterStore
from .request import Request, Response

Handler = Callable[[Request, "Core", auth.Principal | None, "re.Match[str]"], Response]

PLURAL = {"project": "projects", "task": "tasks", "session": "sessions"}
ID_PREFIX = {"project": "prj", "task": "tsk", "session": "ses"}
PRIMARY_KEY = {"project": "project_id", "task": "task_id", "session": "session_id"}
PARSERS = {"project": NewObject.project, "task": NewObject.task, "session": NewObject.session}


class Core:
    """Process-wide state: one connection, one store, one registry, one audit log."""

    def __init__(self, conn) -> None:
        check_version(conn)
        self.conn = conn
        self.schema_version = current_version(conn)
        self.objects = objects.ObjectStore(conn, schema_version=self.schema_version)
        self.evidence = evidence.EvidenceStore(conn, schema_version=self.schema_version)
        self.state = state.StateStore(conn, schema_version=self.schema_version,
                                      evidence_validator=evidence.CurrentEvidenceValidator(conn))
        self.interpretations = interpretations.InterpretationStore(conn, schema_version=self.schema_version)
        self.resolver = resolver.ResolverStore(self.interpretations)
        self.reviews = reviews.ReviewStore(self.resolver)
        self.continuity = ContinuityStore(conn,schema_version=self.schema_version)
        from ..context.memory_provider import MemoryProvider
        self.context = ContextBuilder(conn,schema_version=self.schema_version,memory=MemoryProvider.from_deployment())
        self.adapter = NativeAdapterStore(self)
        self.registry = registry.Registry(conn)
        self.auth = auth.Auth(conn)
        self.audit = audit.AuditLog(conn)
        self.authority = authority.AuthorityStore(conn, schema_version=self.schema_version)

    def actor_kind(self, actor_id: str) -> str:
        return self.registry.require_actor(actor_id)["kind"]


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern[str]
    handler: Handler
    scope: str | None
    #: Whether an anonymous caller is allowed. Only liveness and schema reporting.
    public: bool = False


def _collection_routes() -> list[Route]:
    routes: list[Route] = []
    for kind, plural in PLURAL.items():
        routes.append(Route("POST", re.compile(rf"^/v1/{plural}$"),
                            _make_create(kind), "objects:write"))
        routes.append(Route("GET", re.compile(rf"^/v1/{plural}$"),
                            _make_list(kind), "objects:read"))
        routes.append(Route("GET", re.compile(rf"^/v1/{plural}/([^/]+)$"),
                            _make_get(kind), "objects:read"))
    return routes


def _base_routes() -> list[Route]:
    return [
        Route("POST",re.compile(r"^/v1/adapter/lifecycle/report$"),adapter_report,"adapter:report"),
        Route("POST",re.compile(r"^/v1/adapter/lifecycle/attest$"),adapter_attest,"adapter:attest"),
        Route("POST",re.compile(r"^/v1/adapter/operations/reserve$"),adapter_reserve,"adapter:report"),
        Route("GET",re.compile(r"^/v1/adapter/operations/([^/]+)$"),adapter_get,"adapter:read"),
        Route("POST",re.compile(r"^/v1/adapter/operations/([^/]+)/check-current$"),adapter_check,"adapter:report"),
        Route("POST",re.compile(r"^/v1/adapter/operations/([^/]+)/complete$"),adapter_complete,"adapter:report"),
        Route("POST",re.compile(r"^/v1/adapter/requirements/bind$"),adapter_bind,"adapter:report"),
        Route("POST",re.compile(r"^/v1/evidence/codex-dynamic-observation$"),adapter_evidence,"evidence:write"),
        Route("POST", re.compile(r"^/v1/context/build$"), build_context, "context:build"),
        Route("GET", re.compile(r"^/v1/context/([^/]+)$"), get_context, "context:read"),
        Route("POST", re.compile(r"^/v1/context/([^/]+)/check-current$"), check_context_current, "context:build"),
        Route("POST", re.compile(r"^/v1/checkpoints$"), create_checkpoint, "checkpoint:write"),
        Route("GET", re.compile(r"^/v1/tasks/([^/]+)/checkpoints/latest$"), latest_checkpoint, "checkpoint:read"),
        Route("GET", re.compile(r"^/v1/checkpoints/([^/]+)$"), get_checkpoint, "checkpoint:read"),
        Route("POST", re.compile(r"^/v1/tasks/([^/]+)/resume$"), build_resume, "resume:build"),
        Route("GET", re.compile(r"^/v1/resumes/([^/]+)$"), get_resume, "resume:read"),
        Route("GET", re.compile(r"^/v1/resolutions/preview$"), resolution_preview, "resolutions:read"),
        Route("POST", re.compile(r"^/v1/resolve/([^/]+)$"), resolve_interpretation, "resolutions:process"),
        Route("GET", re.compile(r"^/v1/resolutions/([^/]+)$"), get_resolution, "resolutions:read"),
        Route("POST", re.compile(r"^/v1/resolutions/([^/]+)/manual-reapply$"), manual_reapply, "reviews:manage"),
        Route("GET", re.compile(r"^/v1/reviews/pending$"), pending_reviews, "reviews:read"),
        Route("GET", re.compile(r"^/v1/reviews/([^/]+)$"), get_review, "reviews:read"),
        Route("POST", re.compile(r"^/v1/reviews/([^/]+)/(approve|reject)$"), review_change, "reviews:manage"),
        Route("POST", re.compile(r"^/v1/interpretations/([^/]+)/(correct|reject|rerun)$"), maintenance_change, "interpretations:manage"),
        Route("GET", re.compile(r"^/v1/health$"), health, None, public=True),
        Route("GET", re.compile(r"^/v1/meta/schema$"), meta_schema, None, public=True),
        Route("POST", re.compile(r"^/v1/interpret$"), interpret_event, "interpretations:process"),
        Route("GET", re.compile(r"^/v1/interpretations$"), list_interpretations, "interpretations:read"),
        Route("GET", re.compile(r"^/v1/interpretations/([^/]+)$"), get_interpretation, "interpretations:read"),
        Route("GET", re.compile(r"^/v1/hosts$"), list_hosts, "objects:read"),
        Route("GET", re.compile(r"^/v1/actors$"), list_actors, "objects:read"),
        Route("POST", re.compile(r"^/v1/auth/keys$"), create_key, "admin"),
        Route("GET", re.compile(r"^/v1/auth/keys$"), list_keys, "admin"),
        Route("POST", re.compile(r"^/v1/auth/keys/([^/]+)/revoke$"), revoke_key, "admin"),
        Route("GET", re.compile(r"^/v1/audit$"), list_audit, "admin"),
        Route("POST", re.compile(r"^/v1/events$"), create_event, "events:write"),
        Route("GET", re.compile(r"^/v1/events$"), list_events, "events:read"),
        Route("GET", re.compile(r"^/v1/events/([^/]+)$"), get_event, "events:read"),
        Route("GET", re.compile(r"^/v1/rules/active$"), list_active_rules, "authority:read"),
        Route("GET", re.compile(r"^/v1/rules/([^/]+)$"), get_rule, "authority:read"),
        Route("GET", re.compile(r"^/v1/rules/([^/]+)/history$"), get_rule_history, "authority:read"),
        Route("POST", re.compile(r"^/v1/rules/proposals$"), propose_rule, "authority:propose"),
        Route("POST", re.compile(r"^/v1/rules/([^/]+)/approve$"), approve_rule, "authority:manage"),
        Route("POST", re.compile(r"^/v1/rules/([^/]+)/supersede$"), supersede_rule, "authority:manage"),
        Route("POST", re.compile(r"^/v1/rules/([^/]+)/retire$"), retire_rule, "authority:manage"),
        Route("POST", re.compile(r"^/v1/guard/check$"), guard_check, "guard:check"),
        Route("POST", re.compile(r"^/v1/tool-results$"), record_tool_result, "evidence:write"),
        Route("POST", re.compile(r"^/v1/codex-exec-observations$"), record_codex_exec_observation, "evidence:attest"),
        Route("POST", re.compile(r"^/v1/evidence/confirm$"), confirm_evidence, "evidence:confirm"),
        Route("GET", re.compile(r"^/v1/evidence/([^/]+)$"), get_evidence, "evidence:read"),
        Route("GET", re.compile(r"^/v1/tasks/([^/]+)/evidence$"), list_task_evidence, "evidence:read"),
        Route("POST", re.compile(r"^/v1/workspaces/fingerprint$"), capture_fingerprint, "fingerprint:scan"),
        Route("POST", re.compile(r"^/v1/workspaces/compare$"), compare_fingerprints, "fingerprint:read"),
        Route("POST", re.compile(r"^/v1/tasks/([^/]+)/steps$"), create_step, "state:write"),
        Route("GET", re.compile(r"^/v1/tasks/([^/]+)/steps$"), list_steps, "state:read"),
        Route("GET", re.compile(r"^/v1/tasks/([^/]+)/history$"), task_history, "state:read"),
        Route("POST", re.compile(r"^/v1/tasks/([^/]+)/transition$"), transition_task, "state:write"),
        Route("POST", re.compile(r"^/v1/tasks/([^/]+)/criteria$"), set_task_criteria, "state:accept"),
        Route("POST", re.compile(r"^/v1/tasks/([^/]+)/accept$"), accept_task, "state:accept"),
        Route("GET", re.compile(r"^/v1/steps/([^/]+)$"), get_step, "state:read"),
        Route("GET", re.compile(r"^/v1/steps/([^/]+)/history$"), step_history, "state:read"),
        Route("POST", re.compile(r"^/v1/steps/([^/]+)/transition$"), transition_step, "state:write"),
        Route("POST", re.compile(r"^/v1/steps/([^/]+)/criteria$"), set_step_criteria, "state:accept"),
    ]


ROUTES: tuple[Route, ...]
"""Assigned at the bottom of the module: the route table names the handlers,
so it can only be built once they exist."""


def resolve(method: str, path: str) -> tuple[Route, re.Match[str]]:
    """Return the matching route, or the frozen refusal for a bad method/path."""
    allowed: set[str] = set()
    for route in ROUTES:
        match = route.pattern.match(path)
        if match:
            if route.method == method:
                return route, match
            allowed.add(route.method)
    if allowed:
        raise errors.MethodNotAllowed(
            f"{method} is not supported for {path}", allowed=sorted(allowed)
        )
    raise errors.NotFound("route", path)


# -- handlers ---------------------------------------------------------------


def health(request: Request, core: Core, principal: auth.Principal | None,
           match: re.Match[str]) -> Response:
    return Response(200, {
        "status": "ok",
        "core_version": __version__,
        "schema_version": core.schema_version,
    })


def meta_schema(request: Request, core: Core, principal: auth.Principal | None,
                match: re.Match[str]) -> Response:
    return Response(200, {
        "core_version": __version__,
        "schema_version": current_version(core.conn),
        "expected_schema_version": SCHEMA_VERSION,
        "migrations": applied_migrations(core.conn),
    })


def create_key(request: Request, core: Core, principal: auth.Principal,
               match: re.Match[str]) -> Response:
    body = request.json_body()
    unknown = sorted(set(body) - {"actor_id", "label", "scopes"})
    if unknown:
        raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)
    assert principal is not None
    actor_id = body.get("actor_id", principal.actor_id)
    if actor_id != principal.actor_id:
        raise errors.ActorMismatch(
            "a key may only be minted for the authenticated actor",
            authenticated_actor_id=principal.actor_id, body_actor_id=actor_id,
        )
    scopes = body.get("scopes")
    if not isinstance(scopes, list):
        raise errors.InvalidRequest("scopes must be an array of strings", field="scopes")
    issued = core.auth.issue_key(
        actor_id=actor_id, label=body.get("label") or "unnamed", scopes=scopes
    )
    return Response(201, issued, {"Location": f"/v1/auth/keys/{issued['key_id']}"})


def list_keys(request: Request, core: Core, principal: auth.Principal,
            match: re.Match[str]) -> Response:
    return Response(200, {"keys": core.auth.list_keys()})


def revoke_key(request: Request, core: Core, principal: auth.Principal,
               match: re.Match[str]) -> Response:
    # From the route's captured group, not from the last path segment: the last
    # segment of /v1/auth/keys/{key_id}/revoke is the literal "revoke", which
    # made this endpoint fail every time it was called.
    key_id = match.group(1)
    _require_id(key_id, "key", "key_id")
    core.auth.revoke(key_id)
    return Response(200, {"key_id": key_id, "revoked": True})


def list_audit(request: Request, core: Core, principal: auth.Principal,
            match: re.Match[str]) -> Response:
    entries = core.audit.list(actor_id=_optional_param(request, "actor_id"),
                              limit=_int_param(request, "limit", 100))
    return Response(200, {"entries": entries, "count": len(entries)})


def create_event(request: Request, core: Core, principal: auth.Principal,
            match: re.Match[str]) -> Response:
    assert principal is not None
    spec = NewEvent.from_request(request.json_body(), actor_id=principal.actor_id,
                                 actor_kind=core.actor_kind(principal.actor_id))
    with db.translate_lock_errors(), db.transaction(core.conn):
        event, replayed = core.objects.events.append(spec)
    if replayed:
        return Response(200, {"event": event, "replayed": True})
    return Response(201, {"event": event, "replayed": False},
                    {"Location": f"/v1/events/{event['event_id']}"})


def list_events(request: Request, core: Core, principal: auth.Principal,
            match: re.Match[str]) -> Response:
    events = core.objects.events.list(
        session_id=_optional_param(request, "session_id"),
        task_id=_optional_param(request, "task_id"),
        project_id=_optional_param(request, "project_id"),
        event_type=_optional_param(request, "event_type"),
        source_system=_optional_param(request, "source_system"),
        limit=_int_param(request, "limit", 100),
        after_seq=_int_param(request, "after_seq", 0) or None,
    )
    return Response(200, {"events": events, "count": len(events)})


def get_event(request: Request, core: Core, principal: auth.Principal,
              match: re.Match[str]) -> Response:
    event_id = match.group(1)
    _require_id(event_id, "evt", "event_id")
    event = core.objects.events.get(event_id)
    if event is None:
        raise errors.NotFound("event", event_id)
    return Response(200, {"event": event})


def list_active_rules(request: Request, core: Core, principal: auth.Principal,
                      match: re.Match[str]) -> Response:
    project_id = _optional_param(request, "project_id")
    task_id = _optional_param(request, "task_id")
    if project_id is not None:
        _require_id(project_id, "prj", "project_id")
    if task_id is not None:
        _require_id(task_id, "tsk", "task_id")
    rules = core.authority.list_active(project_id=project_id, task_id=task_id)
    return Response(200, {"rules": rules, "count": len(rules)})


def get_rule(request: Request, core: Core, principal: auth.Principal,
             match: re.Match[str]) -> Response:
    rule_id = match.group(1)
    _require_id(rule_id, "rul", "rule_id")
    return Response(200, {"rule": core.authority.get(rule_id)})


def get_rule_history(request: Request, core: Core, principal: auth.Principal,
                     match: re.Match[str]) -> Response:
    rule_id = match.group(1)
    _require_id(rule_id, "rul", "rule_id")
    return Response(200, {"history": core.authority.history(rule_id)})


def propose_rule(request: Request, core: Core, principal: auth.Principal,
                 match: re.Match[str]) -> Response:
    assert principal is not None
    result = core.authority.propose(request.json_body(), actor_id=principal.actor_id)
    return Response(200 if result["replayed"] else 201, result)


def _rule_transition(request: Request, core: Core, principal: auth.Principal,
                     match: re.Match[str], action: str) -> Response:
    assert principal is not None
    rule_id = match.group(1)
    _require_id(rule_id, "rul", "rule_id")
    if core.actor_kind(principal.actor_id) not in ("human", "system"):
        raise errors.ForbiddenActorKind("only human or trusted system actors may manage Authority")
    result = core.authority.transition(rule_id, action, request.json_body(),
                                       actor_id=principal.actor_id)
    return Response(200, result)


def approve_rule(request: Request, core: Core, principal: auth.Principal,
                 match: re.Match[str]) -> Response:
    return _rule_transition(request, core, principal, match, "approve")


def supersede_rule(request: Request, core: Core, principal: auth.Principal,
                   match: re.Match[str]) -> Response:
    return _rule_transition(request, core, principal, match, "supersede")


def retire_rule(request: Request, core: Core, principal: auth.Principal,
                match: re.Match[str]) -> Response:
    return _rule_transition(request, core, principal, match, "retire")


def guard_check(request: Request, core: Core, principal: auth.Principal,
                match: re.Match[str]) -> Response:
    return Response(200, core.authority.guard(request.json_body()))


def record_tool_result(request: Request, core: Core, principal: auth.Principal,
                       match: re.Match[str]) -> Response:
    if core.actor_kind(principal.actor_id) != "system":
        raise errors.ForbiddenActorKind("tool Evidence requires a trusted system actor")
    result = core.evidence.record_tool_result(request.json_body(), actor_id=principal.actor_id)
    return Response(200 if result["replayed"] else 201, result)


def record_codex_exec_observation(request: Request, core: Core, principal: auth.Principal,
                                  match: re.Match[str]) -> Response:
    if core.actor_kind(principal.actor_id) != "system":
        raise errors.ForbiddenActorKind("native observation requires a trusted system actor")
    result = core.evidence.record_codex_exec_observation(request.json_body(), actor_id=principal.actor_id)
    return Response(200 if result["replayed"] else 201, result)


def confirm_evidence(request: Request, core: Core, principal: auth.Principal,
                     match: re.Match[str]) -> Response:
    if core.actor_kind(principal.actor_id) != "human":
        raise errors.ForbiddenActorKind("USER_CONFIRMATION requires a human actor")
    result = core.evidence.record_confirmation(request.json_body(), actor_id=principal.actor_id)
    return Response(200 if result["replayed"] else 201, result)


def get_evidence(request: Request, core: Core, principal: auth.Principal,
                 match: re.Match[str]) -> Response:
    evidence_id = match.group(1)
    _require_id(evidence_id, "evd", "evidence_id")
    return Response(200, {"evidence": core.evidence.get(evidence_id)})


def list_task_evidence(request: Request, core: Core, principal: auth.Principal,
                       match: re.Match[str]) -> Response:
    task_id = match.group(1)
    _require_id(task_id, "tsk", "task_id")
    records = core.evidence.list_task(task_id)
    return Response(200, {"evidence": records, "count": len(records)})


def capture_fingerprint(request: Request, core: Core, principal: auth.Principal,
                        match: re.Match[str]) -> Response:
    if core.actor_kind(principal.actor_id) != "system":
        raise errors.ForbiddenActorKind("fingerprint scanning requires a trusted system actor")
    body = _state_body(request, {"project_id", "host_id"})
    _require_id(body.get("project_id"), "prj", "project_id")
    _require_id(body.get("host_id"), "hst", "host_id")
    return Response(200, core.evidence.capture(body["project_id"], actor_id=principal.actor_id,
                                                host_id=body["host_id"]))


def compare_fingerprints(request: Request, core: Core, principal: auth.Principal,
                         match: re.Match[str]) -> Response:
    body = _state_body(request, {"left_sha256", "right_sha256"})
    for field in ("left_sha256", "right_sha256"):
        value = body.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise errors.InvalidRequest(f"{field} must be lowercase SHA-256 hex", field=field)
    return Response(200, core.evidence.compare(body["left_sha256"], body["right_sha256"]))


def list_hosts(request: Request, core: Core, principal: auth.Principal,
            match: re.Match[str]) -> Response:
    return Response(200, {"hosts": core.registry.list_hosts()})


def list_actors(request: Request, core: Core, principal: auth.Principal,
            match: re.Match[str]) -> Response:
    return Response(200, {"actors": core.registry.list_actors()})


def _make_create(kind: str) -> Handler:
    def handler(request: Request, core: Core, principal: auth.Principal,
               match: re.Match[str]) -> Response:
        assert principal is not None
        spec = PARSERS[kind](request.json_body())
        if kind == "task" and spec.acceptance_criteria is not None:
            criteria = state.validate_criteria(spec.acceptance_criteria)
            if criteria["requirements"]:
                principal.require("state:accept")
                if core.actor_kind(principal.actor_id) not in ("human", "system"):
                    raise errors.ForbiddenActorKind("only human or system may set Task acceptance requirements")
        result = core.objects.create(spec, actor_id=principal.actor_id)
        key = PRIMARY_KEY[kind]
        return Response(
            200 if result["replayed"] else 201,
            {"object": result["object"], "event": result["event"], "replayed": result["replayed"]},
            {"Location": f"/v1/{PLURAL[kind]}/{result['object'][key]}"},
        )

    return handler


def _make_list(kind: str) -> Handler:
    def handler(request: Request, core: Core, principal: auth.Principal,
               match: re.Match[str]) -> Response:
        rows = core.objects.list(
            kind,
            project_id=_optional_param(request, "project_id"),
            task_id=_optional_param(request, "task_id"),
            limit=_int_param(request, "limit", 100),
            offset=_int_param(request, "offset", 0),
        )
        return Response(200, {PLURAL[kind]: rows, "count": len(rows)})

    return handler


def _make_get(kind: str) -> Handler:
    def handler(request: Request, core: Core, principal: auth.Principal,
               match: re.Match[str]) -> Response:
        object_id = match.group(1)
        _require_id(object_id, ID_PREFIX[kind], "id")
        return Response(200, {kind: core.objects.require(kind, object_id)})

    return handler


# -- small request helpers ---------------------------------------------------


def _require_id(value: str, prefix: str, field: str) -> None:
    if not ids.is_id(value, prefix):
        raise errors.InvalidRequest(
            f"{field} must be a {prefix}_ id", field=field, value=value
        )


def _optional_param(request: Request, name: str) -> str | None:
    value = request.param(name)
    return value or None


def _int_param(request: Request, name: str, default: int) -> int:
    raw = _optional_param(request, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise errors.InvalidRequest(f"{name} must be an integer", field=name, value=raw) from exc



def _state_body(request: Request, allowed: set[str]) -> dict[str, Any]:
    body = request.json_body()
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)
    return body


def create_step(request: Request, core: Core, principal: auth.Principal,
                match: re.Match[str]) -> Response:
    task_id = match.group(1)
    _require_id(task_id, "tsk", "task_id")
    result = core.state.create_step(task_id, _state_body(request, {"title", "description", "acceptance_criteria", "expected_revision", "host_id", "event_id"}),
                                    actor_id=principal.actor_id, actor_kind=core.actor_kind(principal.actor_id),
                                    can_accept="state:accept" in principal.scopes)
    return Response(200 if result["replayed"] else 201, result)


def list_steps(request: Request, core: Core, principal: auth.Principal,
               match: re.Match[str]) -> Response:
    task_id = match.group(1)
    _require_id(task_id, "tsk", "task_id")
    return Response(200, {"steps": core.state.steps(task_id)})


def get_step(request: Request, core: Core, principal: auth.Principal,
             match: re.Match[str]) -> Response:
    step_id = match.group(1)
    _require_id(step_id, "stp", "step_id")
    return Response(200, {"step": core.state.step(step_id)})


def task_history(request: Request, core: Core, principal: auth.Principal,
                 match: re.Match[str]) -> Response:
    task_id = match.group(1)
    _require_id(task_id, "tsk", "task_id")
    return Response(200, {"events": core.state.history(task_id)})


def step_history(request: Request, core: Core, principal: auth.Principal,
                 match: re.Match[str]) -> Response:
    step_id = match.group(1)
    _require_id(step_id, "stp", "step_id")
    step = core.state.step(step_id)
    return Response(200, {"events": core.state.history(step["task_id"], step_id=step_id)})


def set_step_criteria(request: Request, core: Core, principal: auth.Principal,
                      match: re.Match[str]) -> Response:
    step_id = match.group(1)
    _require_id(step_id, "stp", "step_id")
    body = _state_body(request, {"acceptance_criteria", "expected_revision", "host_id", "event_id"})
    return Response(200, core.state.set_step_criteria(step_id, body, actor_id=principal.actor_id,
        actor_kind=core.actor_kind(principal.actor_id), can_accept=True))


def transition_step(request: Request, core: Core, principal: auth.Principal,
                    match: re.Match[str]) -> Response:
    step_id = match.group(1)
    _require_id(step_id, "stp", "step_id")
    body = _state_body(request, {"status", "reason", "expected_revision", "host_id", "event_id"})
    result = core.state.transition_step(step_id, body, actor_id=principal.actor_id,
        actor_kind=core.actor_kind(principal.actor_id), can_accept="state:accept" in principal.scopes)
    return Response(200, result)


def transition_task(request: Request, core: Core, principal: auth.Principal,
                    match: re.Match[str]) -> Response:
    task_id = match.group(1)
    _require_id(task_id, "tsk", "task_id")
    body = _state_body(request, {"status", "reason", "expected_revision", "host_id", "event_id"})
    result = core.state.transition_task(task_id, body, actor_id=principal.actor_id,
        actor_kind=core.actor_kind(principal.actor_id), can_accept="state:accept" in principal.scopes)
    return Response(200, result)


def set_task_criteria(request: Request, core: Core, principal: auth.Principal,
                      match: re.Match[str]) -> Response:
    task_id = match.group(1)
    _require_id(task_id, "tsk", "task_id")
    body = _state_body(request, {"acceptance_criteria", "expected_revision", "host_id", "event_id"})
    return Response(200, core.state.set_task_criteria(task_id, body, actor_id=principal.actor_id,
        actor_kind=core.actor_kind(principal.actor_id), can_accept=True))


def accept_task(request: Request, core: Core, principal: auth.Principal,
                match: re.Match[str]) -> Response:
    task_id = match.group(1)
    _require_id(task_id, "tsk", "task_id")
    body = _state_body(request, {"expected_revision", "host_id", "event_id"})
    return Response(200, core.state.accept_task(task_id, body, actor_id=principal.actor_id,
        actor_kind=core.actor_kind(principal.actor_id), can_accept=True))

def _interpretation_read(principal):
    principal.require("events:read")
    principal.require("interpretations:read")


def interpret_event(request, core, principal, match):
    _interpretation_read(principal)
    if request.query:
        raise errors.InvalidRequest("POST interpret takes no query parameters")
    result = core.interpretations.process(request.json_body(), actor_id=principal.actor_id)
    return Response(200 if result["replayed"] else 201, result)


def get_interpretation(request, core, principal, match):
    _interpretation_read(principal)
    if request.query:
        raise errors.InvalidRequest("GET interpretation takes no query parameters")
    return Response(200, {"interpretation":core.resolver.maintenance.get(match.group(1))})


def list_interpretations(request, core, principal, match):
    _interpretation_read(principal)
    allowed={"event_id","project_id","task_id","status","after_id","limit"}
    if set(request.query)-allowed or any(len(v)!=1 for v in request.query.values()):
        raise errors.InvalidRequest("unknown or duplicate interpretation query parameters")
    params={k:v[0] for k,v in request.query.items()}
    if "limit" in params:
        try:
            if not re.fullmatch(r"[0-9]+",params["limit"]):
                raise ValueError()
            params["limit"]=int(params["limit"])
        except ValueError:
            raise errors.InvalidRequest("limit must be an integer")
    return Response(200,core.interpretations.list(**params))



def _p2_read(principal, scope):
    _interpretation_read(principal)
    principal.require(scope)


def _p2_write(request):
    if request.query:raise errors.InvalidRequest('write takes no query parameters')
    return request.json_body()


def resolution_preview(request,core,principal,match):
    _p2_read(principal,'resolutions:read')
    if set(request.query)!={'interpretation_id'} or len(request.query['interpretation_id'])!=1:raise errors.InvalidRequest('interpretation_id query required')
    _require_id(request.query['interpretation_id'][0],'int','interpretation_id')
    return Response(200,core.resolver.preview(request.query['interpretation_id'][0]))


def resolve_interpretation(request,core,principal,match):
    _require_id(match.group(1),"int","id")
    _p2_read(principal,'resolutions:read')
    result=core.resolver.resolve(match.group(1),_p2_write(request),actor_id=principal.actor_id)
    return Response(200 if result['replayed'] or result['resolution']['result']['disposition']=='SOURCE_REPLAYED' else 201,result)


def get_resolution(request,core,principal,match):
    _require_id(match.group(1),"res","id")
    _p2_read(principal,'resolutions:read')
    if request.query:raise errors.InvalidRequest('unexpected query')
    return Response(200,{'resolution':core.resolver.get(match.group(1))})


def pending_reviews(request,core,principal,match):
    _p2_read(principal,'reviews:read')
    allowed={'project_id','task_id','limit','after_id'}
    if set(request.query)-allowed or any(len(v)!=1 for v in request.query.values()):raise errors.InvalidRequest('unknown or duplicate query')
    args={k:v[0] for k,v in request.query.items()}
    if 'limit' in args:
        if not re.fullmatch(r'[0-9]+',args['limit']):raise errors.InvalidRequest('invalid limit')
        args['limit']=int(args['limit'])
    return Response(200,core.reviews.list(**args))


def get_review(request,core,principal,match):
    _require_id(match.group(1),"rvw","id")
    _p2_read(principal,'reviews:read')
    if request.query:raise errors.InvalidRequest('unexpected query')
    return Response(200,{'review':core.reviews.get(match.group(1))})


def review_change(request,core,principal,match):
    _require_id(match.group(1),"rvw","id")
    _p2_read(principal,'reviews:read')
    result=core.reviews.change(match.group(1),match.group(2),_p2_write(request),actor_id=principal.actor_id,scopes=principal.scopes)
    return Response(200 if result['replayed'] or result['result']['disposition']=='SOURCE_REPLAYED' else 201,result)


def manual_reapply(request,core,principal,match):
    _require_id(match.group(1),"res","id")
    _p2_read(principal,'resolutions:read')
    principal.require('interpretations:manage')
    result=core.reviews.manual_reapply(match.group(1),_p2_write(request),actor_id=principal.actor_id,scopes=principal.scopes)
    return Response(200 if result['replayed'] else 201,result)


def maintenance_change(request,core,principal,match):
    _require_id(match.group(1),"int","id")
    _interpretation_read(principal)
    if match.group(2)=='rerun':principal.require('interpretations:process')
    result=core.resolver.maintenance.change(match.group(1),match.group(2),_p2_write(request),actor_id=principal.actor_id)
    return Response(200 if result['replayed'] else 201,result)





# -- P3 coherent continuity -------------------------------------------------

def _continuity_read(principal):
    for scope in ('objects:read','state:read','authority:read','evidence:read','events:read'):
        principal.require(scope)


def create_checkpoint(request,core,principal,match):
    _continuity_read(principal)
    if request.query:raise errors.InvalidRequest('checkpoint POST accepts no query')
    result=core.continuity.create(request.json_body(),actor_id=principal.actor_id)
    return Response(200 if result['replayed'] else 201,result)


def get_checkpoint(request,core,principal,match):
    _continuity_read(principal)
    if request.query:raise errors.InvalidRequest('checkpoint GET accepts no query')
    return Response(200,{'checkpoint':core.continuity.get_checkpoint(match.group(1))})


def latest_checkpoint(request,core,principal,match):
    _continuity_read(principal)
    if set(request.query)-{'session_id'}:raise errors.InvalidRequest('unknown latest checkpoint query')
    session=None
    if 'session_id' in request.query:
        values=request.query['session_id']
        if len(values)!=1 or not ids.is_id(values[0],'ses'):raise errors.InvalidRequest('invalid session_id query')
        session=values[0]
    return Response(200,{'checkpoint':core.continuity.latest(match.group(1),session_id=session)})


def build_resume(request,core,principal,match):
    _continuity_read(principal);principal.require('checkpoint:read')
    if request.query:raise errors.InvalidRequest('resume POST accepts no query')
    result=core.continuity.resume(match.group(1),request.json_body(),actor_id=principal.actor_id)
    return Response(200 if result['replayed'] else 201,result)


def get_resume(request,core,principal,match):
    _continuity_read(principal);principal.require('checkpoint:read')
    if request.query:raise errors.InvalidRequest('resume GET accepts no query')
    return Response(200,{'resume':core.continuity.get_resume(match.group(1))})


def _context_read(principal):
    _continuity_read(principal)
    principal.require('checkpoint:read')


def build_context(request,core,principal,match):
    _context_read(principal)
    if request.query:raise errors.InvalidRequest('context POST accepts no query')
    result=core.context.build(request.json_body(),actor_id=principal.actor_id)
    return Response(200 if result['replayed'] else 201,result)


def get_context(request,core,principal,match):
    _context_read(principal)
    if request.query:raise errors.InvalidRequest('context GET accepts no query')
    return Response(200,{'context':core.context.get(match.group(1))})


def check_context_current(request,core,principal,match):
    _context_read(principal);principal.require('context:read')
    if request.query:raise errors.InvalidRequest('context current check accepts no query')
    return Response(200,core.context.check_current(match.group(1),request.json_body(),actor_id=principal.actor_id))


def _adapter_request(request,principal,*,context=False):
    _continuity_read(principal)
    if request.query:raise errors.InvalidRequest('adapter protocol accepts no query')
    if context:
        for scope in ('context:build','context:read','checkpoint:read','guard:check'):principal.require(scope)


def adapter_report(request,core,principal,match):
    _adapter_request(request,principal)
    result=core.adapter.report(request.json_body(),principal=principal)
    return Response(200 if result['replayed'] else 201,result)


def adapter_attest(request,core,principal,match):
    _adapter_request(request,principal);principal.require('checkpoint:read')
    result=core.adapter.attest(request.json_body(),principal=principal)
    return Response(200 if result['replayed'] else 201,result)


def adapter_reserve(request,core,principal,match):
    _adapter_request(request,principal,context=True)
    result=core.adapter.reserve(request.json_body(),principal=principal)
    return Response(200 if result['replayed'] or result['operation']['status']=='DENIED' else 201,result)


def adapter_get(request,core,principal,match):
    _adapter_request(request,principal)
    return Response(200,core.adapter.get(match.group(1),principal=principal))


def adapter_check(request,core,principal,match):
    _adapter_request(request,principal,context=True)
    return Response(200,core.adapter.check_current(match.group(1),request.json_body(),principal=principal))


def adapter_complete(request,core,principal,match):
    _adapter_request(request,principal,context=True)
    result=core.adapter.complete(match.group(1),request.json_body(),principal=principal)
    return Response(200 if result['replayed'] else 201,result)


def adapter_bind(request,core,principal,match):
    _adapter_request(request,principal);principal.require('authority:manage');principal.require('state:accept')
    result=core.adapter.bind_requirement(request.json_body(),principal=principal)
    return Response(200 if result['replayed'] else 201,result)


def adapter_evidence(request,core,principal,match):
    _adapter_request(request,principal);principal.require('adapter:read')
    return Response(200,core.adapter.dynamic_observation(request.json_body(),principal=principal))


ROUTES = tuple(_base_routes()) + tuple(_collection_routes())
