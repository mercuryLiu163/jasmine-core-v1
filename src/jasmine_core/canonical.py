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

_SECRET_KEY_RE = re.compile(
    r"(?i)(token|secret|password|passwd|authorization|api[-_]?key|credential|cookie|private[-_]?key)"
)
# Secret-shaped *names*. Compound prefixes are part of the name, not a word
# boundary: `\b` cannot be used here because Python's `\w` includes `_`, so a
# pattern anchored on `\b` would miss `bearer_token` and `x-api-key`.
_SECRET_NAME = (
    r"(?P<name>(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[-_.])*"
    r"(?:password|passwd|apikey|api[-_]?key|secret|access[-_]?token|refresh[-_]?token"
    r"|auth[-_]?token|token|client[-_]?secret|private[-_]?key|authorization|bearer|credential))"
)
# Two patterns rather than one alternation: Python forbids duplicate group names,
# and keeping the quoted case separate means the quote character is preserved, so
# redacting a JSON log line leaves it parseable.
_QUOTED_ASSIGNMENT_RE = re.compile(
    _SECRET_NAME + r"(?P<sep>[\"']?\s*[:=]\s*)(?P<q>[\"'`])[^\"'`]*?(?P=q)"
)
_BARE_ASSIGNMENT_RE = re.compile(
    _SECRET_NAME + r"(?P<sep>[\"']?\s*[:=]\s*)[^\s,;}\)\]\"'`]+"
)
# PEM blocks, footer optional so a truncated capture is still masked.
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)
# Percent-encoded and form-encoded separators: `Bearer%20<token>` and
# `Bearer+<token>` reach the audit log as part of a URL, and a whitespace-only
# pattern would miss both. Matching the encoded forms here means the audit layer
# does not depend on every caller having decoded first.
_BEARER_RE = re.compile(r"(?i)\bbearer(?:%20|%09|\+|\s)+\S+")
_BASIC_RE = re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{3,}\b")
# Well-known provider token shapes, matched without a length floor so short
# test fixtures are covered too.
_PROVIDER_TOKEN_RES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bASIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{10,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{10,}\b"),
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")

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


#: Shapes that indicate real credential material. A shell placeholder such as
#: `$TOKEN` in a reproduction step is documentation, not a leak, so it is
#: deliberately not matched: refusing it would make honest evidence impossible
#: to record while stopping none of the real leaks.
_LITERAL_CREDENTIAL_RES = (
    re.compile(r"(?i)\bbearer(?:%20|%09|\+|\s)+(?!\$\{?\$?)[A-Za-z0-9._~%+-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def looks_like_credential(text: str) -> bool:
    """Whether a string contains something shaped like a real secret.

    Used before anything caller-supplied is persisted, and by the validation
    bundle's own guard. `sk-` has no word-boundary anchor on purpose: inside
    `Bearer%20sk-…` the preceding `0` is a word character, so `\bsk-` would
    miss the token entirely.
    """
    return isinstance(text, str) and any(p.search(text) for p in _LITERAL_CREDENTIAL_RES)


def redact_text(value: str) -> str:
    """Mask credential-shaped substrings in free text destined for logs.

    This is the last line of defence, not the first: callers should pass
    metadata rather than raw text. It must nevertheless catch the shapes that
    actually reach a log file, including quoted values inside JSON.
    """
    if not isinstance(value, str):
        return value
    value = _PEM_RE.sub(REDACTED, value)
    value = _BEARER_RE.sub("Bearer " + REDACTED, value)
    value = _BASIC_RE.sub("Basic " + REDACTED, value)
    value = _JWT_RE.sub(REDACTED, value)
    for pattern in _PROVIDER_TOKEN_RES:
        value = pattern.sub(REDACTED, value)
    value = _SK_RE.sub(REDACTED, value)
    value = _QUOTED_ASSIGNMENT_RE.sub(_mask_assignment, value)
    value = _BARE_ASSIGNMENT_RE.sub(_mask_assignment, value)
    return value


def _mask_assignment(match: re.Match[str]) -> str:
    """Replace only the value, so key quoting and separators survive."""
    quote = match.groupdict().get("q") or ""
    return f"{match.group('name')}{match.group('sep')}{quote}{REDACTED}{quote}"


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
