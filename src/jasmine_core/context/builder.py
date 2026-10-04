"""Single Event-first Context Builder; historical receipts never imply freshness."""
from __future__ import annotations

import json
from pathlib import Path

from .. import clock, db, errors, ids
from ..canonical import canonical_json, sha256_hex
from ..continuity import ContinuityStore, compare_checkpoint
from ..continuity_scan import Budget, scan_workspace
from ..continuity_source import source_for_task
from ..events import EventStore
from ..models import NewEvent
from ..resolution_common import Conflict, fields, key
from ..task_snapshot import read_task_snapshot, read_transaction, SNAPSHOT_VERSION
from .memory_provider import MemoryProvider, TYPES, filter_memory
from .renderer import render, RENDERER_VERSION
from .tokens import Tokenizer

REASONS=('SESSION_START','USER_PROMPT','POST_COMPACT','HANDOFF','MANUAL')


class ContextActorMismatch(errors.ActorMismatch):
    code='context_actor_mismatch'


class ContextBuilder:
    def __init__(self,conn,*,schema_version,tokenizer=None,memory=None,scanner=scan_workspace):
        self.conn=conn;self.schema_version=schema_version;self.tokenizer=tokenizer
        self.memory=memory or MemoryProvider();self.scanner=scanner
        self.events=EventStore(conn,schema_version=schema_version)
        self.continuity=ContinuityStore(conn,schema_version=schema_version,scanner=scanner)

    def _configuration(self):
        if self.conn.in_transaction:raise RuntimeError('Context configuration cannot load inside a transaction')
        if self.tokenizer is None:self.tokenizer=Tokenizer()
        from . import renderer, memory_provider, tokens
        value={'renderer_version':RENDERER_VERSION,'tokenizer':self.tokenizer.identity,
            'memory':self.memory.configuration(),'protocol':'jasmine.context.v1',
            'builder_code_sha256':sha256_hex(Path(__file__).read_text()),
            'policy':'all-active-mandatory.v1','maximum_tokens':2500,'maximum_utf8_bytes':131072,
            'code_sha256':{m.__name__:sha256_hex(Path(m.__file__).read_text()) for m in (renderer,memory_provider,tokens)}}
        return value,sha256_hex(value)

    def _syntax(self,body,*,checking=False):
        required={'host_id','source_event_id'} if checking else {'task_id','host_id','source_event_id','idempotency_key','reason'}
        allowed=required|{'session_id','current_step_id'}
        fields(body,allowed,required)
        if not checking:
            key(body)
            if body['reason'] not in REASONS:raise errors.InvalidRequest('invalid context reason')
        for name,prefix in [('task_id','tsk'),('host_id','hst'),('source_event_id','evt'),('session_id','ses'),('current_step_id','stp')]:
            if name in required and not ids.is_id(body[name],prefix):raise errors.InvalidRequest('invalid '+name)
            if name not in required and body.get(name) is not None and not ids.is_id(body[name],prefix):raise errors.InvalidRequest('invalid '+name)
        return sha256_hex(body)

    def get(self,ident):
        if not ids.is_id(ident,'ctx'):raise errors.InvalidRequest('invalid context_pack_id')
        row=self.conn.execute('SELECT full_result_json FROM context_packs WHERE context_pack_id=?',(ident,)).fetchone()
        if row is None:raise errors.NotFound('context',ident)
        return json.loads(row['full_result_json'])

    def _replay(self,body,actor_id,hashed):
        row=self.conn.execute('SELECT * FROM context_request_keys WHERE actor_id=? AND idempotency_key=?',
            (actor_id,body['idempotency_key'])).fetchone()
        if row is None:return None
        if row['request_hash']!=hashed:raise Conflict('idempotency_conflict')
        return {'context':self.get(row['context_pack_id']),'replayed':True}

    def _source(self,body,actor_id):
        caller,source,task=source_for_task(self.conn,source_event_id=body['source_event_id'],task_id=body['task_id'],
            host_id=body['host_id'],actor_id=actor_id,session_id=body.get('session_id'),
            current_step_id=body.get('current_step_id'),schema_version=self.schema_version)
        if body.get('reason')=='USER_PROMPT' and (source['event_type']!='user.prompt' or source['actor_kind']!='human'):
            raise errors.InvalidRequest('USER_PROMPT context requires genuine human prompt source')
        return caller,source,task

    def _query(self,body,source,snapshot):
        text=source['payload'].get('text','') if body['reason']=='USER_PROMPT' else snapshot['truth_projection']['task']['title']
        if not isinstance(text,str):text=''
        banks=[{'scope':'PROJECT','bank_id':'jasmine-project-'+body_project(snapshot),'project_id':body_project(snapshot)}]
        if self.memory.global_bank:banks.insert(0,{'scope':'GLOBAL','bank_id':'jasmine-global','project_id':None})
        return {'project_id':body_project(snapshot),'task_id':body['task_id'],'session_id':body.get('session_id'),
            'source_event_id':body['source_event_id'],'query_text':text[:2000],'query_sha256':sha256_hex(text[:2000]),'types':list(TYPES),'banks':banks,'max_items':8,'max_tokens':600,
            'authority_refs':[{k:r[k] for k in ('rule_id','revision','version','status')} for r in snapshot['truth_projection']['rules']],
            'state_revision':snapshot['truth_projection']['task']['revision'],'truth_digest':snapshot['truth_digest']}

    def build(self,body,*,actor_id):
        hashed=self._syntax(body);prior=self._replay(body,actor_id,hashed)
        if prior:return prior
        budget=Budget();configuration,config_digest=self._configuration()
        budget.remaining();self._source(body,actor_id)
        for attempt in range(2):
            before=read_task_snapshot(self.conn,body['task_id'],session_id=body.get('session_id'))
            scan=self.scanner(budget,conn=self.conn)
            query=self._query(body,self.events.get(body['source_event_id']),before)
            query['deadline_remaining']=budget.remaining(3)
            memory_result=self.memory.recall(query,budget,conn=self.conn)
            budget.remaining()
            with read_transaction(self.conn):
                after=read_task_snapshot(self.conn,body['task_id'],session_id=body.get('session_id'))
                memory=filter_memory(self.conn,memory_result,after['truth_projection'],schema_version=self.schema_version)
                if len(canonical_json(memory).encode('utf-8'))>60000:
                    memory_result={'status':'degraded','items':[],'banks':[],
                        'provider_id':memory_result.get('provider_id'),'provider_config_digest':memory_result.get('provider_config_digest'),
                        'query_trace_id':memory_result.get('query_trace_id'),'error_code':'memory_trace_too_large'}
                    memory=filter_memory(self.conn,memory_result,after['truth_projection'],schema_version=self.schema_version)
                selected=after['auxiliary']['latest_checkpoint_id']
                checkpoint=self.continuity.get_checkpoint(selected) if selected else None
            if before!=after:continue
            comparison=compare_checkpoint(checkpoint,after['truth_projection'],scan['snapshot'])
            rendering=render(after['truth_projection'],comparison,checkpoint,memory,self.tokenizer,
                source_event_id=body['source_event_id'],current_step_id=body.get('current_step_id'))
            budget.remaining()
            # Re-read operator config outside writer; no tokenizer/provider work under lock.
            if self._configuration()[1]!=config_digest:raise Conflict('context_config_changed')
            with self.continuity._busy(budget),db.transaction(self.conn):
                prior=self._replay(body,actor_id,hashed)
                if prior:return prior
                caller,source,task=self._source(body,actor_id)
                current=read_task_snapshot(self.conn,body['task_id'],session_id=body.get('session_id'))
                if current!=after:continue
                current_memory=filter_memory(self.conn,memory_result,current['truth_projection'],schema_version=self.schema_version)
                if current_memory!=memory:continue
                budget.remaining()
                result=self._persist(body,actor_id,caller,source,task,current,scan,comparison,
                    memory,rendering,configuration,config_digest,hashed)
                budget.remaining()
            budget.remaining()
            return {'context':result,'replayed':False}
        raise Conflict('context_context_conflict')

    def _persist(self,body,actor_id,caller,source,task,snapshot,scan,comparison,memory,rendering,configuration,config_digest,hashed):
        ident=ids.new_id('ctx');event_id=ids.new_id('evt');now=clock.now_rfc3339()
        provenance={'level':'UNVERIFIED_REQUEST','source_event_id':source['event_id']}
        query_text=source['payload'].get('text','') if body['reason']=='USER_PROMPT' else task['title']
        if not isinstance(query_text,str):query_text=''
        memory={**memory,'query_metadata':{'source_event_id':source['event_id'],
            'original_sha256':sha256_hex(query_text),'query_sha256':sha256_hex(query_text[:2000]),
            'truncated':len(query_text)>2000,'query_characters':min(len(query_text),2000)}}
        result={'context_pack_id':ident,'command_event_id':event_id,'source_event_id':source['event_id'],
            'actor_id':actor_id,'host_id':body['host_id'],'project_id':task['project_id'],'task_id':task['task_id'],
            'session_id':body.get('session_id'),'current_step_id':body.get('current_step_id'),
            'reason':body['reason'],'trigger_provenance':provenance,'created_at':now,
            'projection':snapshot['truth_projection'],'truth_digest':snapshot['truth_digest'],
            'selector':snapshot['auxiliary'],'resume_comparison':comparison,'workspace':scan['snapshot'],
            'workspace_digest':sha256_hex(scan['snapshot']),'sample_window':scan['sample_window'],
            'configuration':configuration,'config_digest':config_digest,'tokenizer':self.tokenizer.identity,
            'memory':memory,**rendering}
        raw=canonical_json(result)
        if len(raw.encode('utf-8'))>4194304:raise Conflict('context_context_too_large')
        rawmemory=canonical_json(memory)
        if len(rawmemory.encode('utf-8'))>65536:raise Conflict('context_context_too_large')
        self.events.append(NewEvent(event_id=event_id,event_type='context.built',source_system='core-context',
            occurred_at=clock.now(),actor_id=actor_id,actor_kind=caller['kind'],host_id=body['host_id'],
            project_id=task['project_id'],task_id=task['task_id'],session_id=body.get('session_id'),
            payload={'text':'','context_pack_id':ident,'source_event_id':source['event_id'],
                'truth_digest':snapshot['truth_digest'],'rendered_sha256':rendering['rendered_sha256'],
                'config_digest':config_digest,'token_count':rendering['token_count']}))
        values=(ident,event_id,source['event_id'],actor_id,body['host_id'],task['project_id'],task['task_id'],
            body.get('session_id'),body.get('current_step_id'),body['reason'],canonical_json(provenance),
            SNAPSHOT_VERSION,canonical_json(snapshot['truth_projection']),snapshot['truth_digest'],
            snapshot['auxiliary']['latest_checkpoint_id'],snapshot['auxiliary']['selection_event_seq'],
            canonical_json(comparison),canonical_json(scan['snapshot']),sha256_hex(scan['snapshot']),
            canonical_json(scan['sample_window']),config_digest,RENDERER_VERSION,
            rendering['rendered_content'],rendering['rendered_sha256'],rendering['rendered_utf8_bytes'],
            canonical_json(self.tokenizer.identity),rendering['token_count'],canonical_json(rendering['section_metrics']),
            canonical_json(rendering['omissions']),rawmemory,raw,now)
        self.conn.execute('INSERT INTO context_packs VALUES('+','.join('?' for _ in values)+')',values)
        self.conn.execute('INSERT INTO context_request_keys VALUES(?,?,?,?)',(actor_id,body['idempotency_key'],hashed,ident))
        return result

    def check_current(self,ident,body,*,actor_id):
        budget=Budget()
        self._syntax(body,checking=True);configuration,digest=self._configuration()
        budget.remaining()
        with read_transaction(self.conn):
            stored=self.get(ident)
            if stored['actor_id']!=actor_id:raise ContextActorMismatch('only the original pack actor may admit it')
            if any(body.get(k)!=stored.get(k) for k in ('host_id','source_event_id','session_id','current_step_id')):
                raise Conflict('context_binding_conflict')
            if stored['config_digest']!=digest or stored['configuration']!=configuration or stored['tokenizer']!=self.tokenizer.identity:
                raise Conflict('context_config_changed')
            self._source({**body,'task_id':stored['task_id'],'reason':stored['reason']},actor_id)
            text=stored['rendered_content']
            if sha256_hex(text)!=stored['rendered_sha256'] or len(text.encode('utf-8'))!=stored['rendered_utf8_bytes'] or \
               self.tokenizer.count(text)!=stored['token_count'] or stored['token_count']>2500:
                raise Conflict('context_integrity_error')
            current=read_task_snapshot(self.conn,stored['task_id'],session_id=stored['session_id'])
            if current['truth_digest']!=stored['truth_digest'] or current['truth_projection']!=stored['projection'] or \
               current['auxiliary']!=stored['selector']:raise Conflict('context_stale')
            budget.remaining()
            return {'context_pack_id':ident,'current':True,'truth_digest':stored['truth_digest'],
                'selector':stored['selector'],'rendered_sha256':stored['rendered_sha256'],'checked_at':clock.now_rfc3339()}


def body_project(snapshot):
    return snapshot['truth_projection']['project_id']
