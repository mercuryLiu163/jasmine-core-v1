"""Route table for the Host Hook Protocol endpoint (ADR 0017 section 3).

Follows the `memory.api` / `validation.reads` pattern: the shared route table
in `api.handlers` appends this tuple instead of growing a second dispatcher.
"""

from __future__ import annotations

import re

from .. import errors
from ..api.request import Response
from .service import HostHookService

HOST_HOOK_PATH = "/v1/host-hooks/events"


def post_event(request, core, principal, match):
    if request.query:
        raise errors.InvalidRequest("host hook events take no query parameters")
    status, body = HostHookService(core).handle(request.json_body(), principal)
    return Response(status, body)


def routes(Route):
    return (Route("POST", re.compile(r"^/v1/host-hooks/events$"), post_event, "hosthook:report"),)
