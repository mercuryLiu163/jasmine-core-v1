"""Canonical serialization, content hashing and credential redaction.

`body_sha256` from ADR 0003 is computed over `canonical_json`, so the same
logical request always hashes identically regardless of key order or
whitespace. `redact_text` is the single implementation used by the audit
layer, the server log and the validation recorder; it is never applied to
Event payloads, which are stored verbatim.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SECRET_KEY_RE = re.compile(r"(?i)(token|secret|password|passwd|authorization|api[-_]?key|credential|cookie)")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|api[-_]?key|secret|access[-_]?token|refresh[-_]?token|token)\s*[:=]\s*[^\s,;\"']+"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

REDACTED = "[REDACTED]"


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no padding, non-ASCII preserved."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(value: Any) -> str:
    if not isinstance(value, str):
        value = canonical_json(value)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def body_hash(fields: dict[str, Any]) -> str:
    """Hash of the semantic fields of a write request (see ADR 0003 §1.3)."""
    return sha256_hex(canonical_json(fields))


def redact_text(value: str) -> str:
    """Mask credential-shaped substrings in free text destined for logs."""
    if not isinstance(value, str):
        return value
    value = _BEARER_RE.sub("Bearer " + REDACTED, value)
    value = _JWT_RE.sub(REDACTED, value)
    value = _SK_RE.sub(REDACTED, value)
    value = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", value)
    return value


def redact(value: Any) -> Any:
    """Recursively redact a structure: secret-looking keys and value patterns."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                out[key] = REDACTED
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
