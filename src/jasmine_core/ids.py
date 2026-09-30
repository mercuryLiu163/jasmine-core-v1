"""Public ID generation and validation.

See docs/adr/0001-public-id-schema-and-package-boundary.md. Every ID has the
shape ``<prefix>_<26 Crockford base32 chars>``. For prefixes other than
``evt``, the first 10 characters encode a 48-bit creation timestamp in
milliseconds and the remaining 16 are random. An ``evt`` body is opaque:
clients may derive it deterministically for idempotent retries.
"""

from __future__ import annotations

import re
import secrets
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENCODE = {c: i for i, c in enumerate(CROCKFORD)}

TIMESTAMP_CHARS = 10
RANDOM_CHARS = 16
TOTAL_CHARS = TIMESTAMP_CHARS + RANDOM_CHARS
RANDOM_BITS = RANDOM_CHARS * 5
#: 10 bytes is exactly the 80 bits the 16 random characters carry.
RANDOM_BYTES = RANDOM_BITS // 8

#: prefix -> what the identifier names. `stp` and `evd` are reserved here so
#: that P1 cannot introduce a different shape; the columns are not created yet.
PREFIXES = frozenset({"prj", "tsk", "stp", "ses", "hst", "act", "evt", "evd", "aud", "key", "rul", "int", "res", "rvw", "ckp", "rms", "ctx"})

#: Every shipped prefix is three characters; the pattern is exact rather than a
#: 3-4 character range so a future one cannot slip in unnoticed.
ID_RE = re.compile(r"^([a-z]{3})_([0-9ABCDEFGHJKMNPQRSTVWXYZ]{26})$")


class IdError(ValueError):
    """Raised when an identifier is malformed or uses an unknown prefix."""


def _b32(value: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_id(prefix: str, *, now_ms: int | None = None) -> str:
    """Return a server-generated ID, including a valid opaque ``evt_`` ID."""
    if prefix not in PREFIXES:
        raise IdError(f"unknown id prefix: {prefix!r}")
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if not 0 <= ms < (1 << 48):
        raise IdError("timestamp out of range for a 48-bit millisecond clock")
    randomness = int.from_bytes(secrets.token_bytes(RANDOM_BYTES), "big")
    return f"{prefix}_{_b32(ms, TIMESTAMP_CHARS)}{_b32(randomness, RANDOM_CHARS)}"


def parse_id(value: str, *, expect_prefix: str | None = None) -> tuple[str, int | None]:
    """Return ``(prefix, created_ms)``; ``created_ms`` is ``None`` for events."""
    if not isinstance(value, str):
        raise IdError("id must be a string")
    match = ID_RE.fullmatch(value)
    if match is None:
        raise IdError(f"malformed id: {value!r}")
    prefix, body = match.group(1), match.group(2)
    if prefix not in PREFIXES:
        raise IdError(f"unknown id prefix: {prefix!r}")
    if expect_prefix is not None and prefix != expect_prefix:
        raise IdError(f"expected a {expect_prefix}_ id, got {prefix}_")
    if prefix == "evt":
        return prefix, None
    ms = 0
    for char in body[:TIMESTAMP_CHARS]:
        ms = (ms << 5) | _ENCODE[char]
    if ms >= (1 << 48):
        raise IdError("timestamp out of range for a 48-bit millisecond clock")
    return prefix, ms


def is_id(value: object, prefix: str | None = None) -> bool:
    try:
        parse_id(value, expect_prefix=prefix)  # type: ignore[arg-type]
    except IdError:
        return False
    return True
