"""Strict request and immutable command helpers shared by P2 business stores."""
import json
from . import clock, errors, ids
from .canonical import canonical_json, sha256_hex
from .models import NewEvent
from .interpretations import KEY

class Conflict(errors.CoreError):
    status = 409
    def __init__(self, code, message='', **details):
        self.code = code
        super().__init__(message or code, **details)


def bounded(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        raise errors.InvalidRequest('request must be finite JSON')
    try:
        encoded=raw.encode('utf-8')
    except UnicodeError:
        raise errors.InvalidRequest('JSON must contain valid Unicode')
    if len(encoded) > 128*1024:
        raise errors.InvalidRequest('request exceeds 128 KiB')
    return raw


def fields(body, allowed, required=()):
    if not isinstance(body, dict) or set(body)-set(allowed) or set(required)-set(body):
        raise errors.InvalidRequest('unknown or missing request fields')
    bounded(body)


def revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63-1:
        raise errors.InvalidRequest('revision must be a positive bounded integer')
    return value


def key(body):
    value = body.get('idempotency_key')
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise errors.InvalidRequest('safe idempotency_key is required')
    return value


def reason(body):
    value = body.get('reason')
    if not isinstance(value, str) or not 1 <= len(value) <= 2000:
        raise errors.InvalidRequest('reason must contain 1..2000 characters')
    return value


def actor(conn, actor_id, host_id, *, human=False):
    row = conn.execute('SELECT * FROM actors WHERE actor_id=?', (actor_id,)).fetchone()
    host = conn.execute('SELECT 1 FROM hosts WHERE host_id=?', (host_id,)).fetchone()
    if row is None or host is None:
        raise errors.InvalidRequest('actor and host must exist')
    if not row['home_host_id'] or row['home_host_id'] != host_id:
        raise errors.ActorMismatch('actor must be bound to this host')
    if human and row['kind'] not in ('human', 'system'):
        raise errors.ForbiddenActorKind('human or system actor is required')
    return dict(row)


def command(events, source, actor_id, actor_kind, host_id, event_type, payload, *, event_id=None):
    return events.append(NewEvent(event_type=event_type, source_system='core-resolver',
        occurred_at=clock.now(), actor_id=actor_id, actor_kind=actor_kind, host_id=host_id,
        project_id=source['project_id'], task_id=source['task_id'],
        payload={'text': '', **payload}, event_id=event_id))[0]


def digest(value):
    return sha256_hex(bounded(value))
