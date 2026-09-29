"""Request dispatch: authenticate, authorise, call the handler, audit the outcome.

The order is deliberate and is the property P0-T06 checks:

1. resolve the route (405 vs 404, before authentication, so a wrong method on a
   known path is not reported as a missing token),
2. authenticate unless the route is public,
3. check the scope,
4. run the handler,
5. write the audit row in its own transaction.

A failure at step 2, 3 or 4 writes no Truth, but step 5 still runs.
"""

from __future__ import annotations

from typing import Any

from .. import audit as audit_module
from .. import errors, ids
from . import handlers
from .handlers import Core, Route
from .request import Request, Response

JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def dispatch(core: Core, request: Request) -> Response:
    request_id = request.request_id or ids.new_id("aud")
    principal = None
    scope: str | None = None
    target: str | None = None
    decision = "allow"
    error_code: str | None = None
    audit_detail: dict[str, Any] = {}
    try:
        route, _ = handlers.resolve(request.method, request.path)
        scope = route.scope
        if not route.public:
            principal = core.auth.authenticate(request.header("authorization"))
            if route.scope:
                principal.require(route.scope)
        response = route.handler(request, core, principal)
        status = response.status
        error_code = None
        target = _target_of(response)
    except errors.CoreError as exc:
        status = exc.status
        error_code = exc.code
        # A refusal about the caller is a deny; a 404 on a real route is not a
        # decision about authority, and a 5xx is neither.
        decision = "deny" if exc.status in (401, 403) else ("error" if exc.status >= 500 else "allow")
        audit_detail = audit_module.summarise_error(exc)
        response = Response(status, exc.to_payload(request_id))
    except Exception as exc:  # noqa: BLE001 - last line of defence
        status = 500
        decision = "error"
        error_code = "internal_error"
        # No type, message or traceback from the exception reaches the client
        # (ADR 0003 §1.4); the log line goes through redaction instead.
        from ..canonical import redact_text

        _log(f"unhandled error on {request.method} {request.path}: "
             f"{type(exc).__name__}: {redact_text(str(exc))[:200]}")
        audit_detail = {"code": "internal_error"}
        response = Response(500, errors.CoreError("internal error").to_payload(request_id))

    try:
        core.audit.record(
            request_id=request_id,
            method=request.method,
            path=request.path,
            decision=decision,
            actor_id=principal.actor_id if principal else None,
            status_code=status,
            scope=scope,
            target_id=target,
            error_code=error_code,
            detail=audit_detail,
        )
    except Exception as exc:  # noqa: BLE001
        # A failed audit must not fail the request, and must be visible.
        from ..canonical import redact_text

        _log(f"audit write failed for {request_id}: "
             f"{type(exc).__name__}: {redact_text(str(exc))[:200]}")

    headers = dict(response.headers)
    headers["X-Request-Id"] = request_id
    return Response(response.status, response.body, headers)


def _target_of(response: Response) -> str | None:
    if not isinstance(response.body, dict):
        return None
    for key in ("project_id", "task_id", "session_id", "event_id", "key_id", "audit_id"):
        value = response.body.get(key)
        if isinstance(value, str):
            return value
    inner = response.body.get("object")
    if isinstance(inner, dict):
        return next((v for v in inner.values() if isinstance(v, str) and "_" in v), None)
    inner = response.body.get("event")
    if isinstance(inner, dict):
        return inner.get("event_id")
    return None


def _log(message: str) -> None:
    import sys

    print(f"jasmine-core: {message}", file=sys.stderr)


def response_bytes(response: Response) -> tuple[int, bytes, dict[str, Any]]:
    return response.status, response.json_bytes(), {"Content-Type": JSON_CONTENT_TYPE, **response.headers}
