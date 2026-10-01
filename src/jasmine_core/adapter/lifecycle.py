"""Trusted hook reports and later, independently immutable native attestations."""
from .. import db, errors
from ..canonical import sha256_hex
from ..continuity_source import source_for_task
from ..resolution_common import fields, key, Conflict
from .protocol import AdapterStore, event_id, identifier, text

REPORT={'event_id','idempotency_key','task_id','current_step_id','host_id','session_id','source_event_id',
        'native_thread_id','native_turn_id','lease_generation','callback_id','hook_run_id','hook_event_name',
        'hook_input_sha256','hook_definition_sha256','profile_sha256','deployment_sha256'}
NAMES={'UserPromptSubmit':'userPromptSubmit','SessionStart':'sessionStart','PreCompact':'preCompact',
       'PostCompact':'postCompact','Stop':'stop'}


class LifecycleStore(AdapterStore):
    def report(self,body,*,principal):
        fields(body,REPORT,REPORT);key(body)
        for name,prefix in (('event_id','evt'),('task_id','tsk'),('host_id','hst'),('session_id','ses'),('source_event_id','evt')):
            identifier(body[name],prefix)
        identifier(body['current_step_id'],'stp',optional=True)
        for name in ('native_thread_id','lease_generation','callback_id'):text(body[name],name)
        text(body['hook_run_id'],'hook_run_id',optional=True)
        if not isinstance(body['hook_event_name'],str) or body['hook_event_name'] not in NAMES:raise errors.InvalidRequest('unsupported lifecycle event')
        text(body['native_turn_id'],'native_turn_id',optional=body['hook_event_name']=='SessionStart')
        import re
        for name in ('hook_input_sha256','hook_definition_sha256','profile_sha256','deployment_sha256'):
            if not isinstance(body[name],str) or not re.fullmatch('[0-9a-f]{64}',body[name]):raise errors.InvalidRequest('invalid lifecycle digest')
        ident=event_id('lifecycle-report',body['native_thread_id'],body['callback_id'])
        if body['event_id']!=ident:raise errors.InvalidRequest('lifecycle report id must match callback identity')
        config=self._config();config.principal(self.conn,principal,'adapter:report')
        semantic={k:v for k,v in body.items() if k!='idempotency_key'};digest=sha256_hex(semantic)
        with db.transaction(self.conn):
            kid,_,alias=self._key(config,principal,body,'lifecycle-report');old=self._read(ident,principal=principal)
            if old:
                if old['actor_id']!=principal.actor_id or old['payload'].get('request_hash')!=digest:raise Conflict('adapter_provenance_mismatch')
                if not alias:self._bind_key(kid,digest,ident,body,principal,old['project_id'],'lifecycle-report')
                return {'report':old['payload']['result'],'replayed':True}
            if alias:raise Conflict('adapter_provenance_mismatch')
        callback,raw_sha=config.receipt(body['callback_id'])
        if callback.get('kind')!='hook_callback' or callback.get('report')!=semantic or callback.get('input_sha256')!=body['hook_input_sha256']:
            raise Conflict('adapter_provenance_mismatch')
        for name in ('hook_definition_sha256','profile_sha256','deployment_sha256'):
            if body[name]!=config.value[name]:raise Conflict('adapter_provenance_mismatch')
        lease=config.lease()
        for field,value in {'session_id':body['native_thread_id'],'turn_id':body['native_turn_id'],
            'generation':body['lease_generation'],'task_id':body['task_id'],'step_id':body['current_step_id'],'host_id':body['host_id']}.items():
            if lease.get(field)!=value:raise Conflict('adapter_provenance_mismatch')
        with db.transaction(self.conn):
            kid,_,alias=self._key(config,principal,body,'lifecycle-report');old=self._read(ident,principal=principal)
            if old:
                if old['payload'].get('request_hash')!=digest:raise Conflict('adapter_provenance_mismatch')
                if not alias:self._bind_key(kid,digest,ident,body,principal,old['project_id'],'lifecycle-report')
                return {'report':old['payload']['result'],'replayed':True}
            _,source,task=source_for_task(self.conn,source_event_id=body['source_event_id'],task_id=body['task_id'],
                actor_id=principal.actor_id,host_id=body['host_id'],session_id=body['session_id'],
                current_step_id=body['current_step_id'],schema_version=self.core.schema_version)
            result={'reported_event_id':ident,'status':'REPORT_ACCEPTED','provenance':'TRUSTED_ADAPTER_REPORT',
                'hook_event_name':body['hook_event_name'],'callback_id':body['callback_id'],'receipt_sha256':raw_sha}
            self._event(ident,'adapter.lifecycle_reported',body,{'request':semantic,'request_hash':digest,
                'adapter_config_sha256':config.config_sha256,'callback_receipt_sha256':raw_sha,'result':result},principal,task['project_id'])
            self._bind_key(kid,digest,ident,body,principal,task['project_id'],'lifecycle-report')
            return {'report':result,'replayed':False}

    def attest(self,body,*,principal):
        fields(body,{'idempotency_key','reported_event_id','checkpoint_id','receipt_id','receipt_sha256'},
               {'idempotency_key','reported_event_id','checkpoint_id','receipt_id','receipt_sha256'});key(body)
        identifier(body['reported_event_id'],'evt');identifier(body['checkpoint_id'],'ckp')
        config=self._config();config.principal(self.conn,principal,'adapter:attest')
        reported=self._read(body['reported_event_id'],principal=principal)
        if not reported or reported['event_type']!='adapter.lifecycle_reported' or reported['actor_id']!=principal.actor_id:
            raise Conflict('adapter_provenance_mismatch')
        request=reported['payload']['request'];ident=event_id('lifecycle-attestation',reported['event_id'])
        semantic={k:v for k,v in body.items() if k!='idempotency_key'};digest=sha256_hex(semantic)
        command_body={**body,'task_id':reported['task_id'],'host_id':reported['host_id'],'session_id':reported['session_id']}
        with db.transaction(self.conn):
            kid,_,alias=self._key(config,principal,body,'lifecycle-attestation');old=self._read(ident,principal=principal)
            if old:
                if old['payload'].get('request_hash')!=digest:raise Conflict('adapter_provenance_mismatch')
                if not alias:self._bind_key(kid,digest,ident,command_body,principal,reported['project_id'],'lifecycle-attestation')
                return {'attestation':old['payload']['result'],'replayed':True}
            if alias:raise Conflict('adapter_provenance_mismatch')
        receipt,_=config.receipt(body['receipt_id'],body['receipt_sha256'])
        if receipt.get('kind')!='lifecycle_native' or receipt.get('callback_id')!=request['callback_id'] or \
           receipt.get('callback_receipt_sha256')!=reported['payload']['callback_receipt_sha256']:
            raise Conflict('adapter_provenance_mismatch')
        notifications=receipt.get('notifications')
        if not isinstance(notifications,list) or len(notifications)>512 or not all(isinstance(n,dict) for n in notifications):
            raise Conflict('adapter_not_proved')
        completed=[];started=[]
        for index,n in enumerate(notifications):
            method=n.get('method');params=n.get('params',{})
            if not isinstance(params,dict):raise Conflict('adapter_not_proved')
            run=params.get('run',{})
            if method in ('hook/started','hook/completed') and isinstance(run,dict) and \
               params.get('threadId')==request['native_thread_id'] and params.get('turnId')==request['native_turn_id'] and \
               run.get('eventName')==NAMES[request['hook_event_name']]:
                if run.get('source')!='project' or run.get('handlerType')!='command' or run.get('sourcePath')!=str(__import__('pathlib').Path(config.value['work_root'])/'.codex/hooks.json'):raise Conflict('adapter_not_proved')
                (started if method=='hook/started' else completed).append((index,run))
        if len(started)!=1 or len(completed)!=1 or started[0][0]>=completed[0][0] or \
           not isinstance(started[0][1].get('id'),str) or not started[0][1]['id'] or started[0][1].get('id')!=completed[0][1].get('id') or completed[0][1].get('status')!='completed' or \
           (request['hook_run_id'] is not None and request['hook_run_id']!=completed[0][1].get('id')):
            raise Conflict('adapter_not_proved')
        if receipt.get('hook_definition_sha256')!=request['hook_definition_sha256'] or \
           receipt.get('profile_sha256')!=request['profile_sha256'] or receipt.get('deployment_sha256')!=request['deployment_sha256']:
            raise Conflict('adapter_provenance_mismatch')
        if request['hook_event_name']=='PreCompact':
            items=[(i,n) for i,n in enumerate(notifications) if n.get('method') in ('item/started','item/completed') and \
                   isinstance(n.get('params'),dict) and n['params'].get('threadId')==request['native_thread_id'] and \
                   n['params'].get('turnId')==request['native_turn_id'] and isinstance(n['params'].get('item'),dict) and n['params']['item'].get('type')=='contextCompaction']
            if len(items)!=2 or [n['method'] for _,n in items]!=['item/started','item/completed'] or \
               not isinstance(items[0][1]['params']['item'].get('id'),str) or not items[0][1]['params']['item']['id'] or items[0][1]['params']['item'].get('id')!=items[1][1]['params']['item'].get('id') or \
               completed[0][0]>=items[1][0]:raise Conflict('adapter_not_proved')
        elif request['hook_event_name']=='Stop':
            turns=[(i,n.get('params',{})) for i,n in enumerate(notifications) if n.get('method')=='turn/completed' and isinstance(n.get('params'),dict) and n['params'].get('threadId')==request['native_thread_id']]
            matched=[(i,p) for i,p in turns if isinstance(p.get('turn'),dict) and p['turn'].get('id')==request['native_turn_id'] and p['turn'].get('status')=='completed']
            if len(matched)!=1 or matched[0][0]<=completed[0][0]:raise Conflict('adapter_not_proved')
        else:raise Conflict('adapter_not_proved')
        checkpoint=self.core.continuity.get_checkpoint(body['checkpoint_id'])
        if checkpoint['task_id']!=reported['task_id'] or checkpoint['host_id']!=reported['host_id'] or \
           checkpoint['session_id']!=reported['session_id'] or checkpoint['source_event_id']!=reported['event_id'] or \
           checkpoint['reason']!=('PRE_COMPACT' if request['hook_event_name']=='PreCompact' else 'SESSION_STOP'):
            raise Conflict('adapter_provenance_mismatch')
        with db.transaction(self.conn):
            kid,_,alias=self._key(config,principal,body,'lifecycle-attestation');old=self._read(ident,principal=principal)
            if old:
                if old['payload'].get('request_hash')!=digest:raise Conflict('adapter_provenance_mismatch')
                if not alias:self._bind_key(kid,digest,ident,command_body,principal,reported['project_id'],'lifecycle-attestation')
                return {'attestation':old['payload']['result'],'replayed':True}
            result={'attestation_event_id':ident,'reported_event_id':reported['event_id'],'checkpoint_id':body['checkpoint_id'],
                'status':'NATIVE_PROVED','native_hook_run_id':completed[0][1]['id'],'receipt_sha256':body['receipt_sha256']}
            self._event(ident,'adapter.lifecycle_attested',command_body,{'request':semantic,'request_hash':digest,'result':result},principal,reported['project_id'])
            self._bind_key(kid,digest,ident,command_body,principal,reported['project_id'],'lifecycle-attestation')
            return {'attestation':result,'replayed':False}
