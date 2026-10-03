"""Trusted hook reports and later, independently immutable native attestations."""
import hashlib
import json
from .. import db, errors
from ..canonical import sha256_hex
from ..continuity_source import source_for_task
from ..resolution_common import fields, key, Conflict
from .lifecycle_proof import validate_native_lifecycle
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
        config.hook_definition()
        callback,raw_sha=config.receipt(body['callback_id'])
        if callback.get('kind')!='hook_callback' or callback.get('report')!=semantic or callback.get('input_sha256')!=body['hook_input_sha256']:
            raise Conflict('adapter_provenance_mismatch')
        raw=callback.get('input_raw_utf8');hook_input=callback.get('input')
        if not isinstance(raw,str) or not isinstance(hook_input,dict):raise Conflict('adapter_provenance_mismatch')
        try:decoded=json.loads(raw)
        except (ValueError,UnicodeError):raise Conflict('adapter_provenance_mismatch')
        if decoded!=hook_input or hashlib.sha256(raw.encode()).hexdigest()!=body['hook_input_sha256'] or any(
            hook_input.get(k)!=body[v] for k,v in (('session_id','native_thread_id'),('turn_id','native_turn_id'),('hook_event_name','hook_event_name'))):
            raise Conflict('adapter_provenance_mismatch')
        for name in ('hook_definition_sha256','profile_sha256','deployment_sha256'):
            if body[name]!=config.value[name]:raise Conflict('adapter_provenance_mismatch')
        lease=config.lease()
        for field,value in {'session_id':body['native_thread_id'],'turn_id':body['native_turn_id'],
            'generation':body['lease_generation'],'task_id':body['task_id'],'step_id':body['current_step_id'],'host_id':body['host_id']}.items():
            if field=='turn_id' and body['hook_event_name'] in ('PreCompact','PostCompact'):continue
            if lease.get(field)!=value:raise Conflict('adapter_provenance_mismatch')
        if body['hook_event_name']=='PostCompact':
            pre=self._read(lease.get('last_precompact_report_id',''),principal=principal)
            if not pre or pre['event_type']!='adapter.lifecycle_reported' or pre['actor_id']!=principal.actor_id:
                raise Conflict('adapter_provenance_mismatch')
            previous=pre['payload']['request']
            if previous['hook_event_name']!='PreCompact' or any(previous[k]!=body[k] for k in
                ('native_thread_id','native_turn_id','lease_generation','task_id','current_step_id','host_id','session_id','source_event_id')):
                raise Conflict('adapter_provenance_mismatch')
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
        hook_definition_path=config.hook_definition()
        receipt,_=config.receipt(body['receipt_id'],body['receipt_sha256'])
        if receipt.get('kind')!='lifecycle_native' or receipt.get('callback_id')!=request['callback_id'] or \
           receipt.get('callback_receipt_sha256')!=reported['payload']['callback_receipt_sha256']:
            raise Conflict('adapter_provenance_mismatch')
        native_hook_run_id=validate_native_lifecycle(receipt,request,hook_definition_path)
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
                'status':'NATIVE_PROVED','native_hook_run_id':native_hook_run_id,'receipt_sha256':body['receipt_sha256']}
            self._event(ident,'adapter.lifecycle_attested',command_body,{'request':semantic,'request_hash':digest,'result':result},principal,reported['project_id'])
            self._bind_key(kid,digest,ident,command_body,principal,reported['project_id'],'lifecycle-attestation')
            return {'attestation':result,'replayed':False}
