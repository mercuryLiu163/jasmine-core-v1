"""One coherent read of complete Task Truth; checkpoint selection is auxiliary."""
from __future__ import annotations

import json
from contextlib import contextmanager

from . import SCHEMA_VERSION, errors, ids
from .authority import AuthorityStore
from .canonical import canonical_json, sha256_hex
from .objects import ObjectStore

SNAPSHOT_VERSION = 'jasmine.task-snapshot.v1'
MAX_PROJECTION_BYTES = 262144


class ContextTooLarge(errors.CoreError):
    status = 409
    code = 'checkpoint_context_too_large'


@contextmanager
def read_transaction(conn):
    """Reuse an explicit existing business transaction, otherwise own DEFERRED."""
    owned = not conn.in_transaction
    if owned:
        conn.execute('BEGIN DEFERRED')
    try:
        yield
    except BaseException:
        if owned:
            conn.rollback()
        raise
    else:
        if owned:
            conn.commit()


def _decode_row(row):
    result = dict(row)
    for key in tuple(result):
        if key.endswith('_json'):
            value = result.pop(key)
            result[key[:-5]] = json.loads(value) if value is not None else None
    return result


def read_task_snapshot(conn, task_id, *, checkpoint_id=None, session_id=None):
    if not ids.is_id(task_id, 'tsk'):
        raise errors.InvalidRequest('invalid task_id')
    with read_transaction(conn):
        objects = ObjectStore(conn, schema_version=SCHEMA_VERSION)
        task = objects.require('task', task_id)
        project = objects.require('project', task['project_id'])
        steps = [_decode_row(r) for r in conn.execute(
            'SELECT * FROM steps WHERE task_id=? ORDER BY step_id', (task_id,))]
        authority = AuthorityStore(conn, schema_version=SCHEMA_VERSION)
        rules = [authority.get(r['rule_id']) for r in conn.execute(
            "SELECT rule_id FROM rules WHERE scope_kind='global' OR "
            "(project_id=? AND (scope_kind='project' OR task_id=?)) ORDER BY rule_id",
            (task['project_id'], task_id))]
        # Evidence has no revision/version. Every immutable row contributes to the
        # complete digest, including bodies omitted from future display renderers.
        evidence = [_decode_row(r) for r in conn.execute(
            'SELECT * FROM evidence WHERE task_id=? ORDER BY evidence_id', (task_id,))]
        bindings = [dict(r) for r in conn.execute(
            'SELECT * FROM resolution_source_bindings WHERE task_id=? ORDER BY binding_event_id',
            (task_id,))]
        members = [
            {'object_type':'project','object_id':project['project_id'],'revision':project['revision']},
            {'object_type':'task','object_id':task_id,'revision':task['revision']},
        ]
        members.extend({'object_type':'step','object_id':s['step_id'],'revision':s['revision']} for s in steps)
        members.extend({'object_type':'rule','object_id':r['rule_id'],'revision':r['revision']} for r in rules)
        if len(members) > 512:
            raise ContextTooLarge('complete Truth exceeds 512 object members')
        members.sort(key=lambda r:(r['object_type'],r['object_id']))
        projection = {
            'snapshot_schema':SNAPSHOT_VERSION, 'project_id':task['project_id'], 'task_id':task_id,
            'project':project, 'task':task, 'steps':steps, 'rules':rules,
            'active_rules':[r for r in rules if r['status']=='ACTIVE'],
            'objects':members, 'source_bindings':bindings,
            'evidence':evidence, 'evidence_count':len(evidence),
            'evidence_digest':sha256_hex(evidence),
            'evidence_refs':[{k:e.get(k) for k in ('evidence_id','kind','status','task_revision',
                'step_revision','source_event_id','change_event_id','fingerprint_sha256')} for e in evidence],
        }
        raw = canonical_json(projection)
        if len(raw.encode('utf-8')) > MAX_PROJECTION_BYTES:
            raise ContextTooLarge('complete Truth projection exceeds 256 KiB')
        if checkpoint_id is not None:
            selector = conn.execute('SELECT c.checkpoint_id,e.seq FROM checkpoints c JOIN events e '
                'ON e.event_id=c.command_event_id WHERE c.checkpoint_id=? AND c.task_id=?',
                (checkpoint_id,task_id)).fetchone()
            if selector is None:
                raise errors.NotFound('checkpoint', checkpoint_id)
        else:
            clause = ' AND c.session_id=?' if session_id is not None else ''
            args = (task_id,session_id) if session_id is not None else (task_id,)
            selector = conn.execute('SELECT c.checkpoint_id,e.seq FROM checkpoints c JOIN events e '
                'ON e.event_id=c.command_event_id WHERE c.task_id=?'+clause+
                ' ORDER BY e.seq DESC,c.checkpoint_id DESC LIMIT 1', args).fetchone()
        return {'truth_projection':projection,'truth_digest':sha256_hex(raw),
                'auxiliary':{'latest_checkpoint_id':selector['checkpoint_id'] if selector else None,
                             'selection_event_seq':selector['seq'] if selector else None}}
