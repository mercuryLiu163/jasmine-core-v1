"""Event-first immutable Checkpoints and read-only Resume comparisons."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

from . import clock, db, errors, fingerprint, ids
from .canonical import canonical_json, sha256_hex
from .continuity_scan import Budget, scan_workspace
from .continuity_source import source_for_task, verified_transition
from .events import EventStore
from .models import NewEvent
from .resolution_common import Conflict, fields, key
from .task_snapshot import SNAPSHOT_VERSION, read_task_snapshot

REASONS = {'PRE_COMPACT','SESSION_STOP','HANDOFF','BLOCKED','STEP_VERIFIED','MANUAL'}


class ContinuityStore:
    def __init__(self, conn, *, schema_version, scanner=scan_workspace):
        self.conn=conn; self.schema_version=schema_version
        self.events=EventStore(conn,schema_version=schema_version)
        self.scanner=scanner

    def _syntax(self, body, *, resume=False, task_id=None):
        required={'host_id','source_event_id','idempotency_key'}
        allowed=required|{'session_id'}
        if resume:
            allowed|={'checkpoint_id'}
        else:
            required|={'task_id','reason'};allowed|={'task_id','reason','current_step_id','note'}
        fields(body,allowed,required);key(body)
        task_id=task_id if resume else body['task_id']
        if any(value is None for value in (task_id,body['host_id'],body['source_event_id'])):
            raise errors.InvalidRequest('required identity fields must be nonnull')
        for name,value,prefix in [('task_id',task_id,'tsk'),('host_id',body['host_id'],'hst'),
                ('source_event_id',body['source_event_id'],'evt'),('session_id',body.get('session_id'),'ses'),
                ('current_step_id',body.get('current_step_id'),'stp'),('checkpoint_id',body.get('checkpoint_id'),'ckp')]:
            if value is not None and not ids.is_id(value,prefix):
                raise errors.InvalidRequest('invalid '+name)
        if not resume:
            if not isinstance(body['reason'],str) or body['reason'] not in REASONS:
                raise errors.InvalidRequest('invalid checkpoint reason')
            note=body.get('note')
            if note is not None:
                fields(note,{'text','source_event_id'},{'text','source_event_id'})
                if not isinstance(note['text'],str) or not 1<=len(note['text'])<=2000 or not ids.is_id(note['source_event_id'],'evt'):
                    raise errors.InvalidRequest('note requires bounded text and source_event_id')
        return task_id,sha256_hex({'body':body,'task_id':task_id})

    def _replay(self, body, actor_id, hashed, *, resume):
        table='resume_request_keys' if resume else 'checkpoint_request_keys'
        row=self.conn.execute(f'SELECT * FROM {table} WHERE actor_id=? AND idempotency_key=?',
                              (actor_id,body['idempotency_key'])).fetchone()
        if row is None:return None
        if row['request_hash']!=hashed:raise Conflict('idempotency_conflict')
        ident=row['resume_id'] if resume else row['checkpoint_id']
        return {'resume' if resume else 'checkpoint':self.get_resume(ident) if resume else self.get_checkpoint(ident),
                'replayed':True}

    @contextmanager
    def _busy(self,budget):
        old=self.conn.execute('PRAGMA busy_timeout').fetchone()[0]
        timeout=max(1,int(budget.remaining(3)*1000))
        self.conn.execute(f'PRAGMA busy_timeout={timeout}')
        try:
            yield
        except sqlite3.OperationalError as exc:
            if db.is_locked(exc):raise errors.DatabaseBusy('continuity writer lock timed out',busy_timeout_ms=timeout) from exc
            raise
        finally:self.conn.execute(f'PRAGMA busy_timeout={old}')

    def _validate_source(self,body,task_id,actor_id):
        caller,source,task=source_for_task(self.conn,source_event_id=body['source_event_id'],task_id=task_id,
            host_id=body['host_id'],actor_id=actor_id,session_id=body.get('session_id'),
            current_step_id=body.get('current_step_id'),schema_version=self.schema_version)
        if body.get('note'):
            source_for_task(self.conn,source_event_id=body['note']['source_event_id'],task_id=task_id,
                host_id=body['host_id'],actor_id=actor_id,schema_version=self.schema_version)
        return caller,source,task

    def get_checkpoint(self,ident):
        if not ids.is_id(ident,'ckp'):raise errors.InvalidRequest('invalid checkpoint_id')
        row=self.conn.execute('SELECT * FROM checkpoints WHERE checkpoint_id=?',(ident,)).fetchone()
        if row is None:raise errors.NotFound('checkpoint',ident)
        data=dict(row)
        for name in ('projection','workspace','note','trigger_provenance'):
            raw=data.pop(name+'_json');data[name]=json.loads(raw) if raw is not None else None
        return data

    def get_resume(self,ident):
        if not ids.is_id(ident,'rms'):raise errors.InvalidRequest('invalid resume_id')
        row=self.conn.execute('SELECT result_json FROM resume_results WHERE resume_id=?',(ident,)).fetchone()
        if row is None:raise errors.NotFound('resume',ident)
        return json.loads(row['result_json'])

    def latest(self,task_id,*,session_id=None):
        if session_id is not None:
            if not ids.is_id(session_id,'ses'):raise errors.InvalidRequest('invalid session_id')
            session=self.conn.execute('SELECT * FROM sessions WHERE session_id=?',(session_id,)).fetchone()
            if session is None:raise errors.NotFound('session',session_id)
            if session['task_id']!=task_id:raise errors.InvalidRequest('session does not belong to requested Task')
        snap=read_task_snapshot(self.conn,task_id,session_id=session_id)
        ident=snap['auxiliary']['latest_checkpoint_id']
        return self.get_checkpoint(ident) if ident else None

    def create(self,body,*,actor_id):
        return self._build(body,actor_id=actor_id,resume=False)

    def resume(self,task_id,body,*,actor_id):
        return self._build(body,actor_id=actor_id,resume=True,task_id=task_id)

    def _build(self,body,*,actor_id,resume,task_id=None):
        task_id,hashed=self._syntax(body,resume=resume,task_id=task_id)
        prior=self._replay(body,actor_id,hashed,resume=resume)
        if prior:return prior
        budget=Budget();self._validate_source(body,task_id,actor_id)
        selector={'checkpoint_id':body.get('checkpoint_id'),'session_id':body.get('session_id')} if resume else {}
        for attempt in range(2):
            budget.remaining()
            before=read_task_snapshot(self.conn,task_id,**selector)
            scan=self.scanner(budget,conn=self.conn)
            after=read_task_snapshot(self.conn,task_id,**selector)
            if before!=after:
                continue
            with self._busy(budget),db.transaction(self.conn):
                prior=self._replay(body,actor_id,hashed,resume=resume)
                if prior:return prior
                caller,source,task=self._validate_source(body,task_id,actor_id)
                current=read_task_snapshot(self.conn,task_id,**selector)
                if after!=current:
                    continue
                budget.remaining()
                result=self._persist(body,actor_id,caller,source,task,current,scan,hashed,resume)
                budget.remaining()
            # A post-COMMIT expiry keeps the immutable receipt recoverable by key.
            budget.remaining()
            return {'resume' if resume else 'checkpoint':result,'replayed':False}
        raise Conflict('resume_context_conflict' if resume else 'checkpoint_context_conflict')

    def _persist(self,body,actor_id,caller,source,task,snapshot,scan,hashed,resume):
        now=clock.now_rfc3339();ident=ids.new_id('rms' if resume else 'ckp');event_id=ids.new_id('evt')
        projection=snapshot['truth_projection'];workspace=scan['snapshot']
        if resume:
            selected=snapshot['auxiliary']['latest_checkpoint_id']
            cp=self.get_checkpoint(selected) if selected else None
            comparison=compare_checkpoint(cp,projection,workspace)
            result={'resume_id':ident,'command_event_id':event_id,'source_event_id':source['event_id'],
                'actor_id':actor_id,'host_id':body['host_id'],'project_id':task['project_id'],'task_id':task['task_id'],
                'session_id':body.get('session_id'),'checkpoint_id':selected,'created_at':now,
                'projection':projection,'truth_digest':snapshot['truth_digest'],'workspace':workspace,
                'sample_window':scan['sample_window'],'comparison':comparison,
                'note':{'value':cp['note'],'authority':False,'historical':True} if cp and cp['note'] else None,
                'next_actions':next_actions(projection,comparison)}
            raw=canonical_json(result)
            if len(raw.encode('utf-8'))>262144:raise Conflict('checkpoint_context_too_large')
            payload={'resume_id':ident,'checkpoint_id':selected,'truth_digest':snapshot['truth_digest'],
                     'comparison':comparison,'source_event_id':source['event_id']}
        else:
            provenance={'level':'UNVERIFIED_REQUEST','source_event_id':source['event_id']}
            if body['reason']=='STEP_VERIFIED':provenance=verified_transition(self.conn,source,body.get('current_step_id'))
            payload={'checkpoint_id':ident,'truth_digest':snapshot['truth_digest'],'reason':body['reason'],
                     'trigger_provenance':provenance,'source_event_id':source['event_id']}
        self.events.append(NewEvent(event_id=event_id,event_type='resume.built' if resume else 'checkpoint.created',
            source_system='core-continuity',occurred_at=clock.now(),actor_id=actor_id,actor_kind=caller['kind'],
            host_id=body['host_id'],project_id=task['project_id'],task_id=task['task_id'],session_id=body.get('session_id'),
            payload={'text':'',**payload}))
        if resume:
            self.conn.execute('INSERT INTO resume_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident,event_id,source['event_id'],task['project_id'],task['task_id'],body['host_id'],actor_id,
                 body.get('session_id'),selected,hashed,raw,now))
            self.conn.execute('INSERT INTO resume_request_keys VALUES(?,?,?,?)',(actor_id,body['idempotency_key'],hashed,ident))
            return result
        rawprojection=canonical_json(projection);rawworkspace=canonical_json(workspace)
        if len(rawworkspace.encode('utf-8'))>2097152:raise Conflict('checkpoint_context_too_large')
        self.conn.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident,event_id,source['event_id'],task['project_id'],task['task_id'],body['host_id'],actor_id,body.get('session_id'),
             body['reason'],canonical_json(provenance),SNAPSHOT_VERSION,rawprojection,sha256_hex(rawprojection),
             task['revision'],snapshot['truth_digest'],rawworkspace,sha256_hex(rawworkspace),
             canonical_json(body['note']) if body.get('note') else None,now))
        self.conn.execute('INSERT INTO checkpoint_request_keys VALUES(?,?,?,?)',(actor_id,body['idempotency_key'],hashed,ident))
        return self.get_checkpoint(ident)


def compare_checkpoint(cp,current,workspace):
    try:
        return _compare_checkpoint(cp,current,workspace)
    except (KeyError,TypeError,ValueError,UnicodeError,OverflowError,AttributeError) as exc:
        raise Conflict('checkpoint_integrity_error','malformed checkpoint projection') from exc


def _compare_checkpoint(cp,current,workspace):
    if cp is None:return {'checkpoint_freshness':'ABSENT','workspace':'UNKNOWN','reconciliation_required':True,'diff':{}}
    previous=cp['projection']
    if cp['projection_version']!=SNAPSHOT_VERSION or previous.get('snapshot_schema')!=SNAPSHOT_VERSION or \
       cp['projection_digest']!=sha256_hex(previous) or cp['full_context_digest']!=sha256_hex(previous) or \
       cp['workspace_digest']!=sha256_hex(cp['workspace']) or \
       cp['task_id']!=current['task_id'] or cp['project_id']!=current['project_id'] or \
       previous.get('task_id')!=cp['task_id'] or previous.get('project_id')!=cp['project_id'] or \
       previous.get('task',{}).get('revision')!=cp['task_revision']:
        raise Conflict('checkpoint_integrity_error','checkpoint identity or stored digest mismatch')
    old={(r['object_type'],r['object_id']):r['revision'] for r in previous['objects']}
    new={(r['object_type'],r['object_id']):r['revision'] for r in current['objects']}
    if cp['task_revision']>current['task']['revision'] or any(k in new and v>new[k] for k,v in old.items()):
        raise Conflict('checkpoint_integrity_error','checkpoint is ahead of current Truth')
    oldrules={r['rule_id']:r for r in previous['rules']};newrules={r['rule_id']:r for r in current['rules']}
    if any(k in newrules and r['version']>newrules[k]['version'] for k,r in oldrules.items()):
        raise Conflict('checkpoint_integrity_error','Rule version is ahead of current Truth')
    diff={'added':[list(k) for k in sorted(new.keys()-old.keys())],
          'removed':[list(k) for k in sorted(old.keys()-new.keys())],
          'changed':[list(k) for k in sorted(old.keys()&new.keys()) if old[k]!=new[k]],
          'evidence_changed':previous['evidence_digest']!=current['evidence_digest']}
    equal=sha256_hex(previous)==sha256_hex(current)
    state='STALE' if cp['task_revision']<current['task']['revision'] else ('STATE_EQUAL' if equal else 'CONTEXT_CHANGED')
    fp=fingerprint.compare(cp['workspace'],workspace)
    oldfiles=cp['workspace'].get('selected_hashes',{});newfiles=workspace.get('selected_hashes',{})
    diff['workspace_paths']={'added':sorted(newfiles.keys()-oldfiles.keys()),
        'removed':sorted(oldfiles.keys()-newfiles.keys()),
        'changed':sorted(k for k in oldfiles.keys()&newfiles.keys() if oldfiles[k]!=newfiles[k])}
    warnings=[] if fp=='SAME' else ['CHECKPOINT_WORKSPACE_'+fp]
    return {'checkpoint_freshness':state,'workspace':fp,'warnings':warnings,'reconciliation_required':state!='STATE_EQUAL' or fp!='SAME','diff':diff}


def next_actions(projection,comparison):
    if comparison['reconciliation_required']:return [{'kind':'RECONCILE_CURRENT_TRUTH'}]
    return [{'kind':'VERIFY_EVIDENCE' if s['status']=='EXECUTED' else 'INSPECT_STEP',
             'step_id':s['step_id'],'status':s['status']} for s in projection['steps'] if s['status'] not in ('ACCEPTED','SKIPPED')]
