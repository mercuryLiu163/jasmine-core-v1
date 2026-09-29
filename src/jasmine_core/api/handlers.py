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

from .. import SCHEMA_VERSION, __version__, audit, auth, authority, db, errors, ids, objects, registry
from ..migrations import applied_migrations, check_version, current_version
from ..models import NewEvent, NewObject
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
        Route("GET", re.compile(r"^/v1/health$"), health, None, public=True),
        Route("GET", re.compile(r"^/v1/meta/schema$"), meta_schema, None, public=True),
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
        result = core.objects.create(PARSERS[kind](request.json_body()),
                                     actor_id=principal.actor_id)
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


ROUTES = tuple(_base_routes()) + tuple(_collection_routes())
