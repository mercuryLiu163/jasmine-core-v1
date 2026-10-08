"""Registry, source and optional Core binding checks for continuity commands."""
import json
from . import errors, ids
from .canonical import body_hash
from .events import EventStore
from .resolution_common import actor


class SourceScopeMismatch(errors.InvalidRequest):
    code = 'source_scope_mismatch'


def source_for_task(conn, *, source_event_id, task_id, host_id, actor_id, session_id=None,
                    current_step_id=None, schema_version):
    caller = actor(conn,actor_id,host_id)
    source = EventStore(conn,schema_version=schema_version).get(source_event_id)
    if source is None:
        raise errors.NotFound('event',source_event_id)
    owner = actor(conn,source['actor_id'],source['host_id'])
    if owner['kind'] != source['actor_kind'] or source['host_id'] != host_id:
        raise errors.ActorMismatch('source actor kind and host must match Registry and caller host')
    task = conn.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone()
    if task is None:
        raise errors.NotFound('task',task_id)
    if source['project_id'] != task['project_id']:
        raise SourceScopeMismatch('source must belong to Task project')
    if source['task_id'] != task_id:
        if source['task_id'] is not None:
            raise SourceScopeMismatch('source belongs to another Task')
        binding = conn.execute('SELECT b.*,s.canonical_resolution_id FROM resolution_source_bindings b '
            'JOIN source_applications s ON s.source_event_id=b.source_event_id '
            'WHERE b.source_event_id=? AND b.task_id=? AND b.resolution_id=s.canonical_resolution_id',
            (source_event_id,task_id)).fetchone()
        if binding is None:
            raise SourceScopeMismatch('source has no trusted derived Task binding')
        event = EventStore(conn,schema_version=schema_version).get(binding['binding_event_id'])
        payload = event['payload'] if event else {}
        if not event or event['event_type']!='interpretation.bound' or event['task_id']!=task_id or \
           event['project_id']!=task['project_id'] or \
           payload.get('source_event_id')!=source_event_id or \
           payload.get('resolution_id')!=binding['resolution_id'] or \
           payload.get('interpretation_id')!=binding['interpretation_id']:
            raise errors.InvalidRequest('invalid derived Task binding provenance')
        application=conn.execute('SELECT result_json FROM resolution_results WHERE resolution_id=?',
            (binding['resolution_id'],)).fetchone()
        applied=json.loads(application['result_json']) if application else {}
        approval=None
        if applied.get('disposition')!='APPLIED':
            approved=conn.execute("SELECT c.result_json,c.command_event_id,c.actor_id FROM review_changes c "
                "JOIN reviews r USING(review_id) WHERE r.resolution_id=? AND c.action='APPROVE' "
                "ORDER BY c.to_revision",(binding['resolution_id'],)).fetchall()
            applied={}
            for row in approved:
                value=json.loads(row['result_json'])
                if value.get('disposition')=='APPLIED':
                    review=EventStore(conn,schema_version=schema_version).get(row['command_event_id'])
                    reviewer=actor(conn,row['actor_id'],review['host_id']) if review else {}
                    if not review or review['event_type']!='review.approved' or reviewer.get('kind') not in ('human','system') or \
                       review['actor_id']!=row['actor_id'] or review['actor_kind']!=reviewer['kind'] or \
                       review['project_id']!=task['project_id'] or review['task_id']!=source['task_id'] or \
                       review['payload'].get('source_event_id')!=source_event_id or \
                       review['payload'].get('interpretation_id')!=binding['interpretation_id']:
                        raise errors.InvalidRequest('invalid derived binding review application')
                    approval=review;applied=value;break
        if not any(a.get('binding_event_id')==binding['binding_event_id'] for a in applied.get('actions',[])):
            raise errors.InvalidRequest('derived binding is absent from canonical committed actions')
        executor = actor(conn,event['actor_id'],event['host_id'])
        if event['actor_kind']!=executor['kind'] or (approval is None and executor['kind']!='system') or \
           (approval is not None and (event['actor_id']!=approval['actor_id'] or event['host_id']!=approval['host_id'])):
            raise errors.InvalidRequest('derived binding must match canonical executor or review principal')
    for value in (session_id, source['session_id']):
        if value is None:
            continue
        session = conn.execute('SELECT * FROM sessions WHERE session_id=?',(value,)).fetchone()
        if session is None:
            raise errors.NotFound('session',value)
        if session['project_id']!=task['project_id'] or \
           session['task_id']!=task_id or session['host_id']!=host_id:
            raise SourceScopeMismatch('Core session binding disagrees with Task or host')
    if current_step_id is not None:
        step = conn.execute('SELECT task_id FROM steps WHERE step_id=?',(current_step_id,)).fetchone()
        if step is None or step['task_id']!=task_id:
            raise SourceScopeMismatch('current Step must belong to Task')
    return caller,source,dict(task)


def verified_transition(conn, source, current_step_id):
    """Validate an existing internal State command, not a caller reason string.

    The internal hash distinguishes the public Raw API representation; it is
    integrity metadata, not a secret signature. In-process Event producers are
    already inside the trusted Core business boundary.
    """
    p=source['payload']; result=p.get('result',{})
    step=result.get('step',{}) if isinstance(result,dict) else {}
    if not isinstance(step,dict):
        raise errors.InvalidRequest('invalid State transition result')
    ident=p.get('step_id')
    if source['event_type']!='step.transitioned' or source['source_system']!='core-api' or \
       p.get('to')!='VERIFIED' or p.get('from')!='EXECUTED' or \
       not ids.is_id(ident,'stp') or current_step_id not in (None,ident):
        raise errors.InvalidRequest('STEP_VERIFIED requires a genuine verified State transition')
    stored=conn.execute('SELECT * FROM steps WHERE step_id=?',(ident,)).fetchone()
    expected=p.get('expected_revision')
    if stored is None or stored['task_id']!=source['task_id'] or type(expected) is not int or \
       expected<1 or step.get('step_id')!=ident or step.get('task_id')!=stored['task_id'] or \
       step.get('status')!='VERIFIED' or step.get('revision')!=expected+1 or \
       stored['revision']<expected+1 or type(result.get('task_revision')) is not int:
        raise errors.InvalidRequest('verified transition does not match State lineage')
    reduced={k:v for k,v in p.items() if k not in ('from','result','projection_at','evidence_ids','rule_versions')}
    hashed={k:source[k] for k in ('event_type','source_system','source_event_id','actor_id',
                                'host_id','session_id','project_id','task_id')}
    hashed.update(occurred_at=None,payload=reduced)
    if body_hash(hashed)!=source['body_sha256']:
        raise SourceScopeMismatch('source is a Raw event, not an internal State command')
    evds=result.get('evidence_ids')
    if not isinstance(evds,list) or not all(ids.is_id(e,'evd') for e in evds) or evds!=p.get('evidence_ids') or len(set(evds))!=len(evds):
        raise errors.InvalidRequest('invalid verified Evidence lineage')
    for ident in evds:
        evidence=conn.execute('SELECT * FROM evidence WHERE evidence_id=?',(ident,)).fetchone()
        if evidence is None or evidence['task_id']!=source['task_id'] or \
           evidence['step_id'] not in (None,stored['step_id']) or evidence['status'] not in ('PASS','INFO'):
            raise errors.InvalidRequest('verified Evidence does not belong to State transition')
    task=conn.execute('SELECT revision FROM tasks WHERE task_id=?',(source['task_id'],)).fetchone()
    if task is None or not 1<=result['task_revision']<=task['revision']:
        raise errors.InvalidRequest('verified Task revision is ahead of current State')
    return {'level':'CORE_STATE_EVENT','source_event_id':source['event_id'], 'step_id':stored['step_id']}
