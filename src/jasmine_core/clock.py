"""Time source and timestamp normalisation.

Event rows carry two timestamps: `occurred_at` (when the source observed the
event, client supplied) and `recorded_at` (when Core accepted it, server
supplied). They must be distinguishable, and both must be RFC 3339 UTC so that
lexicographic ordering matches chronological ordering.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def to_rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def now_rfc3339() -> str:
    return to_rfc3339(now())


def parse_rfc3339(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty RFC 3339 string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
