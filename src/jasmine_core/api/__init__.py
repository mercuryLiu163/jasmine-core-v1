"""The P0 Core API. See docs/api/core-api-v1.md and ADR 0003."""

from __future__ import annotations

__all__ = ["Core", "dispatch", "Request", "Response", "serve"]


def __getattr__(name: str):
    # Imported lazily so `import jasmine_core.api` does not pull in http.server.
    if name in ("Core",):
        from .handlers import Core

        return Core
    if name == "dispatch":
        from .dispatch import dispatch

        return dispatch
    if name in ("Request", "Response"):
        from . import request

        return getattr(request, name)
    if name == "serve":
        from .server import serve

        return serve
    raise AttributeError(name)
