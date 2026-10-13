"""Hook runtime: native stdin -> dialect -> Core -> dialect -> native stdout (ADR 0017 section 10).

Agent-neutral. The dialect supplies translation; this module supplies binding,
resolution, transport, spool and replay. It never raises into the agent: any
internal failure degrades to the dialect's pass-through output and a stderr line.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

from ..api.client import ApiError, CoreClient
from . import dialects, resolve
from .binding import Binding, BindingError, find_binding, load_binding, read_key
from .dialects import Outcome, Translated, capabilities_sha256
from .protocol import PROTOCOL_VERSION
from .spool import Spool, SpoolError

HOOK_PATH = "/v1/host-hooks/events"
DEFAULT_TIMEOUT = {"tool.before": 2.0}
FALLBACK_TIMEOUT = 5.0
DRAIN_LIMIT = 10
DRAIN_BUDGET_SECONDS = 1.0


def _warn(stream: TextIO, message: str) -> None:
    print(f"jasmine-hook: {message}", file=stream)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_request(dialect: Any, tr: Translated, binding: Binding, now: datetime) -> dict[str, Any]:
    payload = dict(tr.payload)
    if tr.event == "tool.before" and not (payload.get("action") or payload.get("path") or payload.get("paths")):
        if tr.files is not None:
            resolved = resolve.resolve_files(tr.files, tr.cwd, tr.file_action or "access")
        elif payload.get("tool") == "shell":
            command = payload.get("input", {}).get("command") if isinstance(payload.get("input"), dict) else None
            resolved = resolve.resolve_shell(command, tr.cwd)
        else:
            resolved = {}
        if resolved.get("action"):
            payload["action"] = resolved["action"]
        if resolved.get("paths"):
            payload["paths"] = resolved["paths"]
    native: dict[str, Any] = {"session_id": tr.session_id}
    if tr.turn_id:
        native["turn_id"] = tr.turn_id
    if tr.call_id:
        native["call_id"] = tr.call_id
    agent: dict[str, Any] = {"name": dialect.NAME}
    if tr.agent_version:
        agent["version"] = tr.agent_version
    ownership = {key: value for key, value in (("project_id", binding.project_id),
                                               ("task_id", binding.task_id),
                                               ("step_id", binding.step_id)) if value}
    return {"protocol_version": PROTOCOL_VERSION, "event": tr.event, "delivery_id": tr.delivery_id,
            "agent": agent,
            "adapter": {"name": dialect.NAME, "version": dialect.ADAPTER_VERSION,
                        "capabilities_sha256": capabilities_sha256(dialect.CAPABILITIES)},
            "host_id": binding.host_id, "binding": ownership, "native": native, "trust": "NATIVE_HOOK",
            "occurred_at": _iso(now), "payload": payload}


def post(binding: Binding, request: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    token = read_key(binding.key_file_for(request["event"]))
    if timeout is None:
        timeout = binding.timeout_seconds or DEFAULT_TIMEOUT.get(request["event"], FALLBACK_TIMEOUT)
    return CoreClient(binding.core_url, token, timeout=timeout).post(HOOK_PATH, request)


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, ApiError):
        return exc.status >= 500 or exc.status == 429
    return isinstance(exc, (OSError, TimeoutError, ValueError))


def deliver(binding: Binding, request: dict[str, Any], spool: Spool, stderr: TextIO) -> Outcome:
    """Send one request; on a transient failure spool captures and flag gaps."""
    event = request["event"]
    try:
        response = post(binding, request)
        return Outcome("ok", response=response, on_unavailable=binding.on_unavailable)
    except BindingError as exc:
        reason, transient = f"key unavailable: {exc}", True
    except Exception as exc:  # noqa: BLE001 - classify, never propagate into the agent
        if isinstance(exc, ApiError) and not _transient(exc):
            _warn(stderr, f"Core rejected {event}: HTTP {exc.status} {exc.code}")
            return Outcome("rejected", on_unavailable=binding.on_unavailable, note=exc.code)
        reason, transient = f"Core unavailable: {type(exc).__name__}", True
    try:
        if event == "tool.before":
            spool.put_gap(request, reason)
        else:
            spool.put_request(request, binding.path, reason)
    except (SpoolError, OSError):
        _warn(stderr, f"could not write spool for {event}")
    if event == "tool.before":
        _warn(stderr, "tool call was not checked by Core (" + reason + ")")
    else:
        _warn(stderr, f"{event} spooled for replay (" + reason + ")")
    return Outcome("unavailable", on_unavailable=binding.on_unavailable, note=reason)


def replay(spool: Spool, binding: Binding | None, stderr: TextIO, *, limit: int | None = None,
           budget: float | None = None) -> dict[str, int]:
    """Resend spooled requests oldest first. Stops at the first transient failure."""
    counts = {"sent": 0, "replayed_by_core": 0, "rejected": 0, "remaining": 0, "gaps": 0}
    deadline = time.monotonic() + budget if budget else None
    cache: dict[str, Binding] = {}
    done = 0
    stop = False
    for path in spool.entries():
        try:
            document = spool.read(path)
        except SpoolError:
            spool.reject(path, "malformed spool entry")
            counts["rejected"] += 1
            continue
        if document["kind"] == "unchecked_gap":
            counts["gaps"] += 1
            continue
        if stop or (limit is not None and done >= limit) or (deadline and time.monotonic() > deadline):
            counts["remaining"] += 1
            continue
        use = binding
        if use is None:
            where = document.get("binding_path", "")
            try:
                use = cache.get(where) or load_binding(Path(where))
                cache[where] = use
            except BindingError as exc:
                _warn(stderr, f"cannot replay {path.name}: {exc}")
                counts["remaining"] += 1
                continue
        done += 1
        try:
            response = post(use, document["request"])
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ApiError) and not _transient(exc):
                spool.reject(path, f"HTTP {exc.status} {exc.code}")
                counts["rejected"] += 1
                continue
            stop = True
            counts["remaining"] += 1
            continue
        spool.remove(path)
        counts["sent"] += 1
        if response.get("replayed"):
            counts["replayed_by_core"] += 1
    return counts


def run_hook(agent: str, stdin_bytes: bytes, *, binding_path: str | None = None,
             environ: dict[str, str] | None = None, stdout: TextIO | None = None,
             stderr: TextIO | None = None, now: Callable[[], datetime] | None = None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    environ = os.environ if environ is None else environ
    try:
        dialect = dialects.load(agent)
    except KeyError:
        _warn(stderr, f"unknown agent dialect {agent!r}; available: {', '.join(dialects.names())}")
        return 1
    try:
        native = json.loads(stdin_bytes.decode("utf-8")) if stdin_bytes.strip() else {}
        if not isinstance(native, dict):
            native = {}
    except (UnicodeDecodeError, ValueError):
        native = {}
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    try:
        tr = dialect.translate_in(native, moment)
    except Exception as exc:  # noqa: BLE001
        _warn(stderr, f"dialect could not read this event: {type(exc).__name__}")
        tr = None
    if tr is None:
        text, code = dialect.passthrough(native)
        stdout.write(text)
        return code
    path = find_binding(binding_path, environ, tr.cwd)
    if path is None:
        text, code = dialect.translate_out(tr, Outcome("no_binding"))
        stdout.write(text)
        return code
    try:
        binding = load_binding(path)
    except BindingError as exc:
        _warn(stderr, f"binding unusable: {exc}")
        # An unusable binding is a Core-side gap like any other: policy decides.
        text, code = dialect.translate_out(tr, Outcome("unavailable", on_unavailable=_policy_hint(path),
                                                       note="binding unusable"))
        stdout.write(text)
        return code
    request = build_request(dialect, tr, binding, moment)
    spool = Spool(binding.spool_dir)
    outcome = deliver(binding, request, spool, stderr)
    text, code = dialect.translate_out(tr, outcome)
    stdout.write(text)
    if outcome.status == "ok" and tr.event != "tool.before" and spool.entries():
        try:
            replay(spool, binding, stderr, limit=DRAIN_LIMIT, budget=DRAIN_BUDGET_SECONDS)
        except Exception as exc:  # noqa: BLE001
            _warn(stderr, f"spool drain failed: {type(exc).__name__}")
    return code


def _policy_hint(path: Path) -> str:
    """Best-effort read of on_unavailable from a binding that failed full validation."""
    try:
        value = json.loads(path.read_bytes()).get("on_unavailable")
        return value if value in ("allow", "deny") else "allow"
    except (OSError, ValueError, AttributeError):
        return "allow"


def run_replay(binding_path: str | None, spool_dir: str | None, limit: int | None,
               environ: dict[str, str] | None = None, stdout: TextIO | None = None,
               stderr: TextIO | None = None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    environ = os.environ if environ is None else environ
    path = find_binding(binding_path, environ, os.getcwd())
    binding = None
    if path is not None:
        try:
            binding = load_binding(path)
        except BindingError as exc:
            _warn(stderr, f"binding unusable: {exc}")
            return 2
    directory = Path(spool_dir) if spool_dir else (binding.spool_dir if binding else None)
    if directory is None:
        _warn(stderr, "no binding and no --spool-dir; nothing to replay")
        return 2
    counts = replay(Spool(directory), binding, stderr, limit=limit)
    stdout.write(json.dumps(counts) + "\n")
    return 0 if counts["remaining"] == 0 else 1
