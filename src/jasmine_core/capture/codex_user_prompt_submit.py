"""Codex `UserPromptSubmit` capture entry (P0-T07).

Scope, stated exactly: this translates ONE Codex hook event into one Raw Event.
It is not an Agent adapter, it does not interpret the text, it does not touch
Task state, and it must not be described as any of those. The complete adapter
lands in P5.

Contract with Codex, derived from the V0 hook's observed payload:
  stdin  <- {"hook_event_name": "UserPromptSubmit", "session_id", "cwd",
             "prompt", "turn_id", ...}
  stdout -> a JSON object. An empty object means "no context to add"; the P0
            entry never injects context, so it normally prints {}.

Design constraints that come from ADR 0004:
  * the original prompt text is sent verbatim; the Event is the truth,
  * a Codex turn must never be blocked by a Core that is down, so every failure
    is logged locally and swallowed, and the hook always exits 0,
  * the token comes from the environment, never from argv or the payload,
  * `event_id` is derived deterministically from the source identity, so a Codex
    retry of the same turn is idempotent at the Core instead of duplicating the
    user's message.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.error import URLError
from pathlib import Path
from typing import Any

from ..api.client import ApiError, CoreClient

EVENT_TYPE = "user.prompt"
SOURCE_SYSTEM = "codex"
DEFAULT_BASE_URL = "http://127.0.0.1:8787"
STATE_DIR_ENV = "JASMINE_CORE_STATE_DIR"
TOKEN_ENV = "JASMINE_CORE_TOKEN"
HOST_ENV = "JASMINE_CORE_HOST_ID"
LOG_NAME = "capture.log"
TRACE_NAME = "hook-invocations.log"


def _append(target: Path, message: str) -> None:
    """Append one redacted, local-only line. Never raises."""
    from ..canonical import redact_text

    line = redact_text(message).replace("\n", " ")[:500]
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _state_dir() -> Path:
    configured = os.environ.get(STATE_DIR_ENV)
    return Path(configured) if configured else Path.home() / ".local/share/jasmine-core"


def _log(state_dir: Path | None, message: str) -> None:
    try:
        target = (state_dir or _state_dir()) / LOG_NAME
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        _append(target, message)
    except OSError:
        # A capture hook must never fail the conversation, not even to log.
        pass


def _trace(state_dir: Path | None, outcome: dict) -> None:
    """Leave evidence that the entry ran at all, with no prompt text.

    This is what makes "Codex never invoked the entry" distinguishable from "the
    entry ran and the capture failed". Without it, the only evidence of a skipped
    hook is the absence of an event, which is also exactly what a Core outage
    looks like. The event id is a hash of the prompt, so the trace records *which*
    turn was seen without recording the turn.
    """
    from .. import ids
    from ..clock import now_rfc3339

    try:
        target = (state_dir or _state_dir()) / TRACE_NAME
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        fields = {
            "at": now_rfc3339(),
            "request_id": outcome.get("request_id") or ids.new_id("aud"),
            "captured": bool(outcome.get("captured")),
            "event_id": outcome.get("event_id"),
            "replayed": outcome.get("replayed"),
            "reason": outcome.get("reason"),
            "hook_event_name": outcome.get("hook_event_name"),
            # A hash of (source, session, turn), so the Gate can confirm which
            # turn the entry saw without the prompt text being written down.
            "source_turn_sha256": outcome.get("source_turn_sha256"),
            "source_session_sha256": outcome.get("source_session_sha256"),
            "turn_sha256": outcome.get("turn_sha256"),
            "prompt_sha256": outcome.get("prompt_sha256"),
            "core_url": _trace_core_url(),
            "host_id_configured": bool(os.environ.get(HOST_ENV)),
            "token_configured": bool(os.environ.get(TOKEN_ENV)),
        }
        # Trace lines must stay valid JSON. The general log helper truncates at
        # 500 characters, which can cut a closing quote after these identity
        # fields were added and make the Gate mistake an invocation for absence.
        for key in ("reason",):
            if isinstance(fields.get(key), str):
                fields[key] = fields[key][:256]
        line = json.dumps(fields, ensure_ascii=False, sort_keys=True) + "\n"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        pass


def derive_event_id(host_id: str, session_id: str, turn_id: str, prompt: str) -> str:
    """A stable `evt_` id for one user turn on one device.

    Deterministic, so a retried hook is recognised as a replay by the Core's
    `event_id` idempotency instead of writing the user's message twice. Every
    input to the *identity* of the turn is included -- host, session, turn and
    the prompt itself -- so two devices capturing the same conversation, or the
    same turn delivered with a different working directory, cannot collide.
    Omitting `host_id` was a real defect: two devices produced the same id, the
    different `host_id` changed the body hash, and the second device's prompt
    was rejected as a conflict and lost.

    A client-supplied id carries no timestamp component. That is deliberate:
    the client cannot know the Core's clock, and ordering comes from
    `events.seq`, which the Core assigns. The 26-character Crockford body is
    still enforced by `ids.parse_id`.
    """
    import hashlib

    from ..ids import CROCKFORD

    material = "\x1f".join((SOURCE_SYSTEM, host_id, session_id, turn_id, prompt))
    raw = hashlib.sha256(material.encode("utf-8")).digest()  # 32 bytes = 256 bits
    # Take 26 groups of 5 bits from 30 bytes. Reading bit-by-bit across byte
    # boundaries is what makes every character independent; a byte-aligned
    # shift silently degrades most of the id to one or two significant bits.
    value = int.from_bytes(raw[:30], "big") >> 6  # drop the low 6 unused bits
    return "evt_" + "".join(
        CROCKFORD[(value >> (5 * (25 - index))) & 0x1F] for index in range(26)
    )


def build_event_body(payload: dict[str, Any], *, host_id: str) -> dict[str, Any]:
    """Translate a Codex hook payload into a Core `/v1/events` request body.

    The prompt is sent verbatim: the Event is the truth, and masking it here
    would make the later interpretation and audit work on a different sentence
    than the user actually typed. Redaction belongs on the derived surfaces.

    `occurred_at` is deliberately NOT sent. The Codex hook payload carries no
    timestamp, so any value here would be invented, and inventing a fresh one on
    every delivery would turn a retried turn into an `event_id_conflict` instead
    of the replay it is. The Core records its own acceptance time.

    `cwd` is deliberately not sent either. It is provenance rather than part of
    the turn's identity, it is not part of the idempotency hash, and Codex
    reports it inconsistently (a resolved path on one delivery, the symlink on
    the next), so including it turned a legitimate retry into a conflict. The
    device is already recorded as `host_id`, and the transcript path in the
    Codex session file remains the place to look for a working directory.
    """
    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("hook payload has no session_id")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("hook payload has an empty prompt")
    return {
        "event_type": EVENT_TYPE,
        "source_system": SOURCE_SYSTEM,
        "source_event_id": f"{session_id}:{turn_id}" if isinstance(turn_id, str) and turn_id else None,
        "host_id": host_id,
        "event_id": derive_event_id(host_id, session_id, str(turn_id or ""), prompt),
        "payload": {
            "text": prompt,
            "turn_id": turn_id if isinstance(turn_id, str) else None,
            "source_session_id": session_id,
        },
    }


def handle(payload: dict[str, Any], client: CoreClient, *, host_id: str,
           state_dir: Path | None = None) -> dict[str, Any]:
    """Return a result record for the caller. Never raises."""
    try:
        if payload.get("hook_event_name") != "UserPromptSubmit":
            return {"captured": False, "reason": "not a UserPromptSubmit event"}
        body = build_event_body(payload, host_id=host_id)
        result = client.post("/v1/events", body)
        event = result["event"]
        return {"captured": True, "event_id": event["event_id"], "seq": event["seq"],
                "source_event_id": event["source_event_id"], "replayed": result["replayed"],
                "actor_id": event["actor_id"], "host_id": event["host_id"],
                "occurred_at": event["occurred_at"], "recorded_at": event["recorded_at"]}
    except ApiError as exc:
        _log(state_dir, f"capture refused: HTTP {exc.status} {exc.code}")
        return {"captured": False, "reason": f"core refused: {exc.status} {exc.code}",
                "request_id": exc.request_id}
    except URLError as exc:
        _log(state_dir, f"core unreachable: {type(exc.reason).__name__}: {exc.reason}")
        return {"captured": False, "reason": "core unreachable"}
    except (ValueError, OSError) as exc:
        _log(state_dir, f"capture skipped: {type(exc).__name__}: {exc}")
        return {"captured": False, "reason": f"skipped: {type(exc).__name__}"}
    except Exception as exc:  # noqa: BLE001 - a hook must never break the turn
        _log(state_dir, f"capture failed: {type(exc).__name__}: {str(exc)[:180]}")
        return {"captured": False, "reason": f"failed: {type(exc).__name__}"}


def main(argv: list[str] | None = None) -> int:
    """Read the hook payload on stdin, print the Codex context object.

    stdout is the channel Codex reads as model-visible context, so it is always
    the context object and nothing else. P0 adds no context, so the normal
    output is `{}`. Every diagnostic goes to stderr, which Codex logs but does
    not inject. The exit code is always 0: a Core that is down must not cost the
    user their turn.

    Every path leaves a trace line, so "Codex never ran this" and "this ran and
    could not capture" are distinguishable after the fact.
    """
    state_dir_env = os.environ.get(STATE_DIR_ENV)
    state_dir = Path(state_dir_env) if state_dir_env else None
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as exc:
        _log(state_dir, f"unreadable hook payload: {type(exc).__name__}")
        _trace(state_dir, {"captured": False, "reason": "unreadable payload"})
        _report({"captured": False, "reason": "unreadable payload"})
        return 0

    if not isinstance(payload, dict):
        _trace(state_dir, {"captured": False, "reason": "payload is not an object"})
        _report({"captured": False, "reason": "payload is not an object"})
        return 0

    outcome: dict[str, Any] = {
        "hook_event_name": payload.get("hook_event_name"),
        "source_turn_sha256": _turn_digest(payload),
        "source_session_sha256": _identity_digest(payload.get("session_id")),
        "turn_sha256": _identity_digest(payload.get("turn_id")),
        "prompt_sha256": _prompt_digest(payload),
    }

    token = os.environ.get(TOKEN_ENV)
    if not token:
        _log(state_dir, f"{TOKEN_ENV} is not set; prompt not captured")
        outcome["reason"] = f"{TOKEN_ENV} is not set"
        _trace(state_dir, outcome)
        _report({"captured": False, "reason": outcome["reason"]})
        return 0

    client = CoreClient(os.environ.get("JASMINE_CORE_URL", DEFAULT_BASE_URL), token)
    result = handle(payload, client, host_id=os.environ.get(HOST_ENV, ""), state_dir=state_dir)
    outcome.update(result)
    _trace(state_dir, outcome)
    _report(result)
    return 0


def _turn_digest(payload: dict[str, Any]) -> str | None:
    """A stable digest of the turn identity, with no prompt text in it.

    `derive_event_id` already hashes (host, session, turn, prompt) into the event
    id, so when a capture succeeded the event id itself identifies the turn.
    This covers the case where nothing was captured, letting the Gate confirm the
    entry saw the turn it was looking for.
    """
    import hashlib

    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    material = "\x1f".join((SOURCE_SYSTEM, str(session_id), str(turn_id or "")))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _prompt_digest(payload: dict[str, Any]) -> str | None:
    import hashlib

    prompt = payload.get("prompt")
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest() if isinstance(prompt, str) else None


def _identity_digest(value: Any) -> str | None:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest() if isinstance(value, str) and value else None


def _trace_core_url() -> str | None:
    """Trace the local endpoint without writing URL credentials or query data."""
    from urllib.parse import urlsplit

    url = os.environ.get("JASMINE_CORE_URL", DEFAULT_BASE_URL)
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"http://{host}:{port}"


def _report(result: dict[str, Any]) -> None:
    print(json.dumps({}))
    sys.stderr.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
