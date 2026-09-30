"""Immutable candidate processing independent of Authority/State/Evidence."""
from __future__ import annotations
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import clock, db, errors, ids
from .canonical import canonical_json, sha256_hex
from .events import EventStore
from .interpretation_schema import InvalidOutput, MAX_OUTPUT_BYTES, validate_output
from .interpreter_provider import ProviderFailure, configured_provider

KEY = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
STATUSES = frozenset({'PROCESSING','EXTRACTED','FAILED','INTERRUPTED'})


class InterpretationStore:
    def __init__(self, conn: sqlite3.Connection, *, schema_version: int, provider=None):
        self.conn = conn
        self.schema_version = schema_version
        self.provider = provider

    def _source(self,event_id):
        event = EventStore(self.conn,schema_version=self.schema_version).get(event_id)
        if event is None:
            raise errors.NotFound('event',event_id)
        if event['event_type'] not in ('user.prompt','assistant.message'):
            raise errors.InvalidInterpretationSource('only user.prompt and assistant.message can be interpreted')
        actor = self.conn.execute('SELECT kind FROM actors WHERE actor_id=?',(event['actor_id'],)).fetchone()
        if actor is None or actor['kind']!=event['actor_kind']:
            raise errors.InvalidInterpretationSource('source actor no longer matches its immutable envelope')
        if self.conn.execute('SELECT 1 FROM hosts WHERE host_id=?',(event['host_id'],)).fetchone() is None:
            raise errors.InvalidInterpretationSource('source host is missing')
        if event['project_id'] and self.conn.execute('SELECT 1 FROM projects WHERE project_id=?',(event['project_id'],)).fetchone() is None:
            raise errors.InvalidInterpretationSource('source project is missing')
        if event['task_id']:
            task = self.conn.execute('SELECT project_id FROM tasks WHERE task_id=?',(event['task_id'],)).fetchone()
            if task is None or task['project_id']!=event['project_id']:
                raise errors.InvalidInterpretationSource('source Task must belong to its project')
        if event['session_id']:
            session = self.conn.execute('SELECT * FROM sessions WHERE session_id=?',(event['session_id'],)).fetchone()
            if session is None or any(session[name] is not None and session[name]!=event[name]
                                      for name in ('project_id','task_id','host_id')):
                raise errors.InvalidInterpretationSource('source session context does not match Event')
        text = event['payload']['text']
        if len(text.encode('utf-8'))>32768 or not text:
            raise errors.InvalidInterpretationSource('source text must be nonempty and at most 32 KiB')
        source_type = ('USER_EXPLICIT' if actor['kind']=='human' and event['event_type']=='user.prompt'
                       else 'SYSTEM_CONFIG' if actor['kind']=='system' else 'AGENT_PROPOSED')
        source = {name:event[name] for name in ('event_id','project_id','task_id','session_id','host_id','actor_id','actor_kind','event_type')}
        source.update(text=text,source_type=source_type,body_sha256=event['body_sha256'])
        if len(canonical_json(source).encode('utf-8'))>48*1024:
            raise errors.InvalidInterpretationSource('source input exceeds 48 KiB')
        return source

    def _recover(self, ident=None):
        now = clock.now_rfc3339()
        where = ' AND i.interpretation_id=?' if ident else ''
        params = [now] + ([ident] if ident else [])
        rows = self.conn.execute('SELECT i.interpretation_id FROM interpretations i LEFT JOIN interpretation_results r '
            'USING(interpretation_id) WHERE r.interpretation_id IS NULL AND i.deadline_at<=?'+where,params).fetchall()
        if rows:
            with db.translate_lock_errors(),db.transaction(self.conn):
                for row in rows:
                    self.conn.execute('INSERT OR IGNORE INTO interpretation_results '
                        '(interpretation_id,status,error_code,completed_at) VALUES(?,?,?,?)',
                        (row['interpretation_id'],'INTERRUPTED','provider_interrupted',now))

    def _get(self,ident):
        row=self.conn.execute('SELECT * FROM interpretations WHERE interpretation_id=?',(ident,)).fetchone()
        if row is None:
            raise errors.NotFound('interpretation',ident)
        value=dict(row)
        value['config']=json.loads(value.pop('config_json'))
        result=self.conn.execute('SELECT * FROM interpretation_results WHERE interpretation_id=?',(ident,)).fetchone()
        value.update(status='PROCESSING',raw_result_json=None,candidates=None,confidence=None,error_code=None,completed_at=None)
        if result:
            value.update({k:result[k] for k in ('status','raw_result_json','confidence','error_code','completed_at')})
            value['candidates']=json.loads(result['candidates_json']) if result['candidates_json'] else None
        return value

    def get(self,ident):
        if not ids.is_id(ident,'int'):
            raise errors.InvalidRequest('interpretation_id must be an int_ id')
        self._recover(ident)
        return self._get(ident)

    def list(self,*,limit=50,after_id=None,**filters):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200:
            raise errors.InvalidRequest('limit must be an integer from 1 to 200')
        self._recover()
        clauses=[];params=[]
        for name,prefix in (('event_id','evt'),('project_id','prj'),('task_id','tsk')):
            value=filters.get(name)
            if value is not None:
                if not ids.is_id(value,prefix):
                    raise errors.InvalidRequest(f'{name} has invalid ID')
                clauses.append(f'i.{name}=?');params.append(value)
        status=filters.get('status')
        if status is not None:
            if status not in STATUSES:
                raise errors.InvalidRequest('unknown interpretation status')
            clauses.append("COALESCE(r.status,'PROCESSING')=?");params.append(status)
        if after_id:
            if not ids.is_id(after_id,'int'):
                raise errors.InvalidRequest('after_id must be an int_ id')
            cursor=self._get(after_id)
            clauses.append('(i.created_at,i.interpretation_id)>(?,?)');params.extend([cursor['created_at'],after_id])
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        params.append(limit+1)
        rows=self.conn.execute('SELECT i.interpretation_id FROM interpretations i LEFT JOIN interpretation_results r '
            'USING(interpretation_id)'+where+' ORDER BY i.created_at,i.interpretation_id LIMIT ?',params).fetchall()
        items=[self._get(row['interpretation_id']) for row in rows[:limit]]
        return {'items':items,'next_after_id':items[-1]['interpretation_id'] if len(rows)>limit else None}

    def process(self,body:dict,*,actor_id:str):
        # Even in-process callers cannot accidentally keep an enclosing write lock
        # open across an external model invocation.
        if self.conn.in_transaction:
            raise errors.InvalidRequest('Interpreter cannot run inside a database transaction')
        if not isinstance(body,dict) or set(body)-{'event_id','idempotency_key','extractor_id'}:
            raise errors.InvalidRequest('unknown interpretation request fields')
        event_id=body.get('event_id');key=body.get('idempotency_key')
        if not ids.is_id(event_id,'evt') or not isinstance(key,str) or not KEY.fullmatch(key):
            raise errors.InvalidRequest('event_id and safe idempotency_key are required')
        extractor_id=body.get('extractor_id','codex-local-v1')
        if extractor_id!='codex-local-v1':
            raise errors.InvalidRequest('unknown extractor_id')
        request_hash=sha256_hex(canonical_json({'event_id':event_id,'extractor_id':extractor_id}))
        replay=None;source=None;config=None;provider=None;ident=None
        # Runtime/catalog reads occur before reserving any database write lock.
        existing_key=self.conn.execute('SELECT 1 FROM interpretation_request_keys WHERE processor_actor_id=? AND idempotency_key=?',
                                       (actor_id,key)).fetchone()
        if existing_key is None:
            provider=self.provider or configured_provider()
            config=provider.configuration()
        with db.translate_lock_errors(),db.transaction(self.conn):
            prior=self.conn.execute('SELECT * FROM interpretation_request_keys WHERE processor_actor_id=? AND idempotency_key=?',
                                    (actor_id,key)).fetchone()
            if prior:
                if prior['request_hash']!=request_hash:
                    raise errors.InterpretationIdempotencyConflict('idempotency key already names a different request')
                replay=prior['interpretation_id']
            else:
                if self.conn.execute('SELECT 1 FROM actors WHERE actor_id=?',(actor_id,)).fetchone() is None:
                    raise errors.NotFound('actor',actor_id)
                source=self._source(event_id)
                config_digest=sha256_hex(canonical_json(config));input_hash=sha256_hex(canonical_json(source))
                prior=self.conn.execute('SELECT interpretation_id FROM interpretations WHERE processor_actor_id=? '
                    'AND event_id=? AND input_hash=? AND config_digest=?',(actor_id,event_id,input_hash,config_digest)).fetchone()
                if prior:
                    replay=prior['interpretation_id']
                else:
                    created=clock.now_rfc3339()
                    deadline=(datetime.now(timezone.utc)+timedelta(seconds=config['timeout_seconds']+5)).isoformat(timespec='microseconds').replace('+00:00','Z')
                    for attempt in range(8):
                        ident=ids.new_id('int')
                        try:
                            self.conn.execute('INSERT INTO interpretations '
                                '(interpretation_id,event_id,project_id,task_id,session_id,host_id,source_actor_id,source_actor_kind,source_type,'
                                'processor_actor_id,extractor_id,extractor_model,extractor_version,provider,prompt_version,output_schema_version,'
                                'schema_digest,config_digest,input_hash,config_json,created_at,deadline_at) VALUES('+','.join('?'*22)+')',
                                (ident,event_id,source['project_id'],source['task_id'],source['session_id'],source['host_id'],source['actor_id'],
                                 source['actor_kind'],source['source_type'],actor_id,extractor_id,config['model'],config['version'],config['provider'],
                                 config['prompt_version'],config['output_schema_version'],config['schema_digest'],config_digest,input_hash,
                                 canonical_json(config),created,deadline))
                            break
                        except sqlite3.IntegrityError:
                            if self.conn.execute('SELECT 1 FROM interpretations WHERE interpretation_id=?',(ident,)).fetchone() is None or attempt==7:
                                raise
                self.conn.execute('INSERT INTO interpretation_request_keys VALUES(?,?,?,?)',(actor_id,key,request_hash,replay or ident))
        if replay:
            return {'interpretation':self.get(replay),'replayed':True}
        if self.conn.in_transaction:
            raise RuntimeError('provider must run without a database transaction')
        raw=None;result=None;error=None
        try:
            raw=provider.extract(source)
            result=validate_output(raw,source)
        except InvalidOutput as exc:
            error='invalid_output:'+str(exc)
        except ProviderFailure as exc:
            error=exc.code
            raw=exc.raw
        except Exception:
            error='provider_error'
        if raw is not None:
            if not isinstance(raw,str):
                raw=None
            else:
                raw=raw.encode('utf-8',errors='replace')[:MAX_OUTPUT_BYTES].decode('utf-8',errors='ignore')
        with db.translate_lock_errors(),db.transaction(self.conn):
            # Recovery and a late completion race: the first terminal outcome wins.
            self.conn.execute('INSERT OR IGNORE INTO interpretation_results '
                '(interpretation_id,status,raw_result_json,candidates_json,confidence,error_code,completed_at) VALUES(?,?,?,?,?,?,?)',
                (ident,'FAILED' if error else 'EXTRACTED',raw,canonical_json(result['candidates']) if result else None,
                 result['confidence'] if result else None,error,clock.now_rfc3339()))
        return {'interpretation':self.get(ident),'replayed':False}
