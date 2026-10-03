"""P3 sole Context hook adapter. Native attestation belongs to the runner."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import time
import os
import re
import sys
from pathlib import Path
from .. import ids
from ..canonical import canonical_json,sha256_hex
from ..adapter.config import private_json
from ..adapter.protocol import event_id
from ..context.tokens import Tokenizer
from .codex_user_prompt_submit import derive_event_id
from .p1_codex_hook import _private_file,_token,_deny
from .p2_runtime import Deadline,Lease,atomic_json
from ..adapter.http_client import DeadlineClient
from .p2_codex_hook import _capture,_call,_block,_trace,_PreviousLease
from .p3_context_adapter import prepare_emission

P2_FIELDS={'mode','run_nonce','project_id','host_id','core_url','token_file','human_token_file','trace_file'}
P3_FIELDS=P2_FIELDS|{'p3_adapter_config_file','p3_operator_token_file','p3_core_session_id'}

def binding(path,payload):
    value,_=private_json(Path(path),cap=65536)
    if set(value) not in (P3_FIELDS,P3_FIELDS|{'p3_review_wait'}) or value['mode']!=2: raise ValueError('invalid P3 binding')
    if type(value.get('p3_review_wait',False)) is not bool:raise ValueError('invalid ReviewWait option')
    if not isinstance(value['run_nonce'],str) or len(value['run_nonce'])<32: raise ValueError('invalid nonce')
    if os.environ.get('JASMINE_CORE_GATE_NONCE')!=value['run_nonce']: return None
    for name,prefix in [('project_id','prj'),('host_id','hst')]:
        if not ids.is_id(value[name],prefix):raise ValueError('invalid binding identity')
    if not isinstance(value['core_url'],str) or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}',value['core_url']):raise ValueError('loopback required')
    for name in ('session_id','turn_id'):
        if name=='turn_id' and payload.get('hook_event_name')=='SessionStart' and payload.get(name) is None:continue
        if not isinstance(payload.get(name),str) or not 1<=len(payload[name].encode())<=256:raise ValueError('native session/turn required')
    for name in ('p3_adapter_config_file','p3_operator_token_file'):
        if not isinstance(value[name],str) or not Path(value[name]).is_absolute():raise ValueError('private absolute path required')
    if value['p3_core_session_id'] is not None and not ids.is_id(value['p3_core_session_id'],'ses'):raise ValueError('invalid Core session')
    return value

def operator_client(config,deadline,factory):
    token=_private_file(Path(config['p3_operator_token_file'])).decode().strip()
    if not token:raise ValueError('operator token unavailable')
    return factory(config['core_url'],token,deadline)

def build_current_context(config,lease,client,deadline,*,tokenizer=None,reason='USER_PROMPT'):
    state=lease.read()
    if not state or state['phase'] not in ('SOURCE_ADMITTED','CONTEXT_PENDING','READY'):raise ValueError('source admission pending')
    if not state.get('core_session_id'):
        session_request=state.get('session_request')
        if session_request is None:
            session_request={'event_id':event_id('native-core-session',state['session_id'],state['task_id']),
                'source_event_id':state['event_id'],'source_system':'jasmine-p3-adapter',
                'project_id':config['project_id'],'task_id':state['task_id'],'host_id':config['host_id'],
                'title':'Jasmine native Task session'}
            state['session_request']=session_request;lease.write(state)
        deadline.remaining()
        session=client.post('/v1/sessions',session_request,cap=deadline.remaining(10))['object']
        if (not ids.is_id(session.get('session_id'),'ses') or session.get('task_id')!=state['task_id']
            or session.get('project_id')!=config['project_id'] or session.get('host_id')!=config['host_id']):
            raise ValueError('Core session binding mismatch')
        state['core_session_id']=session['session_id'];lease.write(state)
    adapter,_=private_json(Path(config['p3_adapter_config_file']),cap=65536)
    request=state.get('context_request') or {'idempotency_key':'p3-context:'+state['event_id']+':'+reason,
       'task_id':state['task_id'],'host_id':config['host_id'],'source_event_id':state['event_id'],
       'session_id':state.get('core_session_id'),'current_step_id':state['step_id'],'reason':reason}
    return prepare_emission(client,lease,deadline,request,session_id=state['session_id'],turn_id=state['turn_id'],
       generation=state['generation'],hook_definition_hash=adapter['hook_definition_sha256'],
       tokenizer=tokenizer or Tokenizer(),actor_id=adapter['actor_id'])

def report_lifecycle(payload,config,lease,client,deadline,*,raw_input_bytes=None):
    state=lease.read();name=payload['hook_event_name']
    if not state or not state.get('task_id') or not state.get('core_session_id'):
        return {'decision':'block','reason':'Jasmine P3 lifecycle pending binding'}
    adapter,_=private_json(Path(config['p3_adapter_config_file']),cap=65536)
    # Independent native compact turns report their real identity without adopting a
    # source turn or making its READY context valid for the compact turn.
    compact_turn=name in ('PreCompact','PostCompact')
    if state['session_id']!=payload['session_id'] or (state['turn_id']!=payload['turn_id'] and not compact_turn):
        raise ValueError('lifecycle native binding mismatch')
    raw=raw_input_bytes if raw_input_bytes is not None else canonical_json(payload).encode()
    if len(raw)>65536:raise ValueError('hook input exceeds cap')
    if json.loads(raw)!=payload:raise ValueError('raw hook input mismatch')
    input_sha=hashlib.sha256(raw).hexdigest()
    callback_id='callback_'+hashlib.sha256(canonical_json([state['session_id'],payload.get('turn_id'),name,state['generation'],input_sha]).encode()).hexdigest()
    report={'event_id':event_id('lifecycle-report',state['session_id'],callback_id),'idempotency_key':'p3-report:'+callback_id,
       'task_id':state['task_id'],'current_step_id':state.get('step_id'),'host_id':config['host_id'],
       'session_id':state['core_session_id'],'source_event_id':state['event_id'],'native_thread_id':state['session_id'],
       'native_turn_id':payload.get('turn_id'),'lease_generation':str(state['generation']),'callback_id':callback_id,
       'hook_run_id':None,'hook_event_name':name,'hook_input_sha256':input_sha,
       **{k:adapter[k] for k in ('hook_definition_sha256','profile_sha256','deployment_sha256')}}
    deadline.remaining()
    receipt={'kind':'hook_callback','report':report.copy(),'input_sha256':input_sha,'input':payload,'input_raw_utf8':raw.decode('utf-8'),'input_encoding':'RAW_STDIN_UTF8' if raw_input_bytes is not None else 'TYPED_CANONICAL_COMPONENT_FALLBACK'}
    receipt['report'].pop('idempotency_key')
    atomic_json(Path(adapter['receipt_root'])/(callback_id+'.json'),receipt)
    reply=client.post('/v1/adapter/lifecycle/report',report,cap=deadline.remaining(3))
    if reply['report']['reported_event_id']!=report['event_id'] or reply['report']['status']!='REPORT_ACCEPTED':raise ValueError('lifecycle report mismatch')
    if name=='PreCompact':
        state['last_precompact_report_id']=report['event_id'];lease.write(state)
    if name in ('PreCompact','Stop'):
        checkpoint={'task_id':state['task_id'],'host_id':config['host_id'],'source_event_id':report['event_id'],
          'session_id':state['core_session_id'],'current_step_id':state.get('step_id'),
          'idempotency_key':'p3-checkpoint:'+callback_id,'reason':'PRE_COMPACT' if name=='PreCompact' else 'SESSION_STOP'}
        result=client.post('/v1/checkpoints',checkpoint,cap=deadline.remaining(10))
        checkpoint_id=result['checkpoint']['checkpoint_id']
        if not ids.is_id(checkpoint_id,'ckp'):raise ValueError('checkpoint identity mismatch')
        state['last_lifecycle_checkpoint_id']=checkpoint_id
        atomic_json(Path(adapter['receipt_root'])/(callback_id+'_checkpoint.json'),
            {'kind':'lifecycle_checkpoint','callback_id':callback_id,'reported_event_id':report['event_id'],'checkpoint_id':checkpoint_id})
    state['last_lifecycle_report_id']=report['event_id'];lease.write(state);deadline.remaining()
    # PostCompact has no documented additionalContext stdout guarantee.
    return {}

def _admit(state, config, payload, lease, deadline, *, expected_identity=None):
    reader = DeadlineClient(config['core_url'], _token(config, 'token_file'), deadline)
    interpreted = _call(state, 'interpret', reader, lease, 'POST', '/v1/interpret',
        {'event_id': state['event_id'], 'idempotency_key': 'p2-int:' + state['event_id']}, cap=125)['interpretation']
    parent_id = interpreted['interpretation_id']
    if expected_identity is not None and parent_id!=expected_identity['interpretation_id']:
        return _block('review wait admission interpretation changed',state)
    current = reader.get('/v1/interpretations/' + parent_id)['interpretation']
    head = current['maintenance_head']
    if expected_identity is not None and (head['status']!='NORMAL' or
        head['current_interpretation_id']!=expected_identity['interpretation_id'] or
        any(state.get(key)!=value for key,value in expected_identity.items())):
        return _block('review wait admission identity changed',state)
    if head['status'] == 'REJECTED':
        state.update(phase='REJECTED', interpretation_id=parent_id)
        lease.write(state)
        return _block('interpretation rejected; no Truth retraction', state)
    if head['status'] == 'RERUN_PENDING':
        state.update(phase='RERUN_PENDING', interpretation_id=parent_id)
        lease.write(state)
        return _block('rerun still pending', state)
    if head['current_interpretation_id'] != parent_id:
        interpreted = reader.get('/v1/interpretations/' + head['current_interpretation_id'])['interpretation']
    state['interpretation_id'] = interpreted['interpretation_id']
    state['phase'] = interpreted['status']
    lease.write(state)
    if interpreted['status'] != 'EXTRACTED':
        return _block(interpreted['status'], state)
    ident = state['interpretation_id']
    resolve_name = 'resolve:' + ident
    if resolve_name not in state['requests']:
        preview = reader.get('/v1/resolutions/preview?interpretation_id=' + ident)
        body = {'idempotency_key': 'p2-res:' + ident, 'host_id': config['host_id'],
            'policy_version': preview['policy_version'], 'expected_revisions': preview['expected_revisions'],
            'expected_context_digest': preview['expected_context_digest']}
    else:
        body = state['requests'][resolve_name]['body']
    resolved = _call(state, resolve_name, reader, lease, 'POST', '/v1/resolve/' + ident, body)['resolution']
    if expected_identity is not None and (resolved['resolution_id']!=expected_identity['resolution_id'] or
        resolved['result']['review_id']!=expected_identity['review_id']):
        return _block('review wait admission resolution changed',state)
    state['resolution_id'] = resolved['resolution_id']
    result = resolved['result']
    state['phase'] = result['disposition']
    state['review_id'] = result['review_id']
    lease.write(state)
    if result['disposition'] == 'PENDING_REVIEW':
        review = reader.get('/v1/reviews/' + result['review_id'])['review']
        if review['status'] == 'REJECTED':
            state['phase'] = 'REJECTED'
            lease.write(state)
            return _block('review rejected', state)
        if review['status'] not in ('APPROVED', 'SOURCE_REPLAYED'):
            return _block('review required', state)
        # The immutable successful approval snapshot supplies applied actions.
        changes = review['history']
        applied = [row['result'] for row in changes if row['action'] == 'APPROVE' and row['result']['disposition'] in ('APPLIED', 'SOURCE_REPLAYED')]
        if not applied:
            raise ValueError('approved application snapshot unavailable')
        result = applied[-1]
    if result['disposition'] not in ('APPLIED', 'SOURCE_REPLAYED', 'NO_ACTION'):
        return _block('application not ready', state)
    if not state.get('task_id'):
        targets = {action['target_id'] for action in result['actions'] if action['target_type'] == 'task'}
        if len(targets) != 1:
            return _block('no unique derived Task', state)
        state['task_id'] = targets.pop()
        # Binding must be a genuine interpretation.bound Event referencing this source.
        binding_ids = {action['binding_event_id'] for action in result['actions']
                       if action['target_type'] == 'task' and action['target_id'] == state['task_id'] and action['binding_event_id']}
        if len(binding_ids) != 1:
            raise ValueError('derived Task binding unavailable')
        event = reader.get('/v1/events/' + binding_ids.pop())['event']
        canonical_id = result.get('canonical_resolution_id') or resolved['resolution_id']
        canonical = reader.get('/v1/resolutions/' + canonical_id)['resolution']
        applied_snapshot = canonical['result']
        if applied_snapshot['disposition'] == 'PENDING_REVIEW':
            approved = reader.get('/v1/reviews/' + applied_snapshot['review_id'])['review']
            applied = [row['result'] for row in approved['history'] if row['action'] == 'APPROVE' and row['result']['disposition'] == 'APPLIED']
            if len(applied) != 1:
                raise ValueError('canonical approved snapshot unavailable')
            applied_snapshot = applied[0]
        expected_actor = applied_snapshot.get('review_actor_id') or applied_snapshot.get('execution_actor_id')
        if (event['event_type'] != 'interpretation.bound' or event['task_id'] != state['task_id'] or
                event['project_id'] != config['project_id'] or event['actor_kind'] not in ('human', 'system') or event['actor_id'] != expected_actor or
                event['host_id'] != config['host_id'] or event['payload']['source_event_id'] != state['event_id'] or
                event['payload']['resolution_id'] != canonical_id or
                event['payload']['interpretation_id'] != canonical['interpretation_id']):
            raise ValueError('invalid derived source binding')
        state['binding_event_id'] = event['event_id']
        lease.write(state)
    if not state.get('step_id'):
        state['phase'] = 'STEP_BINDING_PENDING'
        lease.write(state)
        task = reader.get('/v1/tasks/' + state['task_id'])['task']
        step_event = derive_event_id(config['host_id'], state['session_id'], state['turn_id'], 'p2-step:' + state['event_id'])
        step_body = {'title': 'Captured task execution', 'description': 'P2 bookkeeping source ' + state['event_id'],
            'acceptance_criteria': {'requirements': []}, 'expected_revision': task['revision'],
            'host_id': config['host_id'], 'event_id': step_event}
        step = _call(state, 'step', reader, lease, 'POST', '/v1/tasks/' + state['task_id'] + '/steps', step_body)['step']
        if step['task_id'] != state['task_id'] or step['status'] != 'PLANNED':
            raise ValueError('bookkeeping Step linkage invalid')
        state['step_id'] = step['step_id']
        lease.write(state)
    if expected_identity is not None and not _review_identity_current(state,reader,expected_identity):
        return _block('review wait admission identity changed',state)
    state.update(phase='SOURCE_ADMITTED',host_id=config['host_id'],project_id=config['project_id'])
    lease.write(state)
    return None

def _review_identity_current(state,reader,identity):
    if any(state.get(key)!=value for key,value in identity.items()):return False
    interpretation=reader.get('/v1/interpretations/'+identity['interpretation_id'])['interpretation']
    head=interpretation['maintenance_head']
    if head['status']!='NORMAL' or head['current_interpretation_id']!=identity['interpretation_id']:return False
    review=reader.get('/v1/reviews/'+identity['review_id'])['review']
    matches=(review.get('status')=='APPROVED' and review.get('review_id')==identity['review_id'] and
        review.get('resolution_id')==identity['resolution_id'] and
        review.get('resolution',{}).get('interpretation_id')==identity['interpretation_id'] and
        review.get('resolution',{}).get('source_event_id')==identity['event_id'])
    if not matches:return False
    # Read the head again after Review; these reads are a pre-Context check,
    # not an atomic snapshot across concurrent maintenance writes.
    head=reader.get('/v1/interpretations/'+identity['interpretation_id'])['interpretation']['maintenance_head']
    return head['status']=='NORMAL' and head['current_interpretation_id']==identity['interpretation_id']

def _review_wait(state, config, payload, lease, deadline):
    """Managed fixture opt-in; only the external operator may approve Review.

    The caller keeps its lease lock and original admission deadline. An external
    observer reads the atomic lease without acquiring or writing that lock.
    """
    prompt=payload.get('prompt')
    if (not isinstance(prompt,str) or not prompt.strip() or len(prompt.encode())>32768 or
        config.get('p3_review_wait',False) is not True or
        os.environ.get('JASMINE_CORE_GATE_NONCE')!=config.get('run_nonce') or
        payload.get('hook_event_name')!='UserPromptSubmit' or
        state.get('phase')!='PENDING_REVIEW' or
        not ids.is_id(state.get('task_id'),'tsk') or not ids.is_id(state.get('step_id'),'stp')):
        return _block('review required',state)
    keys=('session_id','turn_id','event_id','generation','task_id','step_id','interpretation_id','resolution_id','review_id','prompt_sha256','core_session_id')
    identity={key:state.get(key) for key in keys}
    identity['requests']=json.loads(canonical_json(state.get('requests',{})))
    expected_raw={'event_type':'user.prompt','source_system':'codex-p2-bound',
        'source_event_id':canonical_json([payload['session_id'],payload['turn_id']]),
        'event_id':derive_event_id(config['host_id'],payload['session_id'],payload['turn_id'],'p2-bound:'+prompt),
        'host_id':config['host_id'],'project_id':config['project_id'],'task_id':state['task_id'],
        'payload':{'text':prompt,'step_id':state['step_id'],'source_session_id':payload['session_id'],'turn_id':payload['turn_id']}}
    if (identity['session_id']!=payload['session_id'] or identity['turn_id']!=payload['turn_id'] or
        identity['event_id']!=expected_raw['event_id'] or identity['prompt_sha256']!=hashlib.sha256(prompt.encode()).hexdigest() or
        state.get('requests',{}).get('raw')!={'method':'POST','path':'/v1/events','body':expected_raw}):
        return _block('review wait source mismatch',state)
    deadline.remaining()
    reader=DeadlineClient(config['core_url'],_token(config,'token_file'),deadline)
    source=reader.get('/v1/events/'+state['event_id'])['event']
    if source.get('actor_kind')!='human' or any(source.get(key)!=value for key,value in expected_raw.items()):
        return _block('review wait immutable source mismatch',state)
    task=reader.get('/v1/tasks/'+state['task_id'])['task']
    step=reader.get('/v1/steps/'+state['step_id'])['step']
    if task.get('project_id')!=config['project_id'] or step.get('task_id')!=state['task_id']:
        return _block('review wait Task/Step mismatch',state)
    state['review_wait_deadline_monotonic']=deadline.end;lease.write(state)
    while True:
        deadline.remaining()
        current=lease.read()
        if not current or any(current.get(key)!=value for key,value in identity.items()) or current.get('phase')!='PENDING_REVIEW':
            return _block('review wait binding changed',state)
        if reader.get('/v1/events/'+state['event_id'])['event']!=source:
            return _block('review wait immutable source changed',state)
        interpretation=reader.get('/v1/interpretations/'+state['interpretation_id'])['interpretation']
        head=interpretation['maintenance_head']
        if head['status']!='NORMAL' or head['current_interpretation_id']!=state['interpretation_id']:
            return _block('review wait interpretation changed',state)
        review=reader.get('/v1/reviews/'+state['review_id'])['review']
        current=lease.read()
        if not current or any(current.get(key)!=value for key,value in identity.items()) or current.get('phase')!='PENDING_REVIEW':
            return _block('review wait binding changed',state)
        if (review.get('review_id')!=state['review_id'] or review.get('resolution_id')!=state['resolution_id'] or
            review.get('resolution',{}).get('interpretation_id')!=state['interpretation_id'] or
            review.get('resolution',{}).get('source_event_id')!=state['event_id']):
            return _block('review wait review mismatch',state)
        if review['status']=='APPROVED':
            if reader.get('/v1/events/'+state['event_id'])['event']!=source:
                return _block('review wait immutable source changed',state)
            # Reenter the unchanged durable admission requests before Context.
            blocked=_admit(state,config,payload,lease,deadline,expected_identity=identity)
            if blocked:return blocked
            if not _review_identity_current(state,reader,identity):
                return _block('review wait pre-context identity changed',state)
            if (reader.get('/v1/events/'+state['event_id'])['event']!=source or
                reader.get('/v1/tasks/'+identity['task_id'])['task'].get('project_id')!=config['project_id'] or
                reader.get('/v1/steps/'+identity['step_id'])['step'].get('task_id')!=identity['task_id']):
                return _block('review wait pre-context source or binding changed',state)
            if not _review_identity_current(state,reader,identity):
                return _block('review wait final pre-context identity changed',state)
            return None
        if review['status']!='PENDING':
            return _block('review rejected or unavailable',state)
        time.sleep(min(.1,deadline.remaining()))

def handle(payload, path, *, client_factory=DeadlineClient, tokenizer=None, raw_input_bytes=None):
    path=Path(path)
    event = payload.get('hook_event_name')
    if event not in ('UserPromptSubmit','SessionStart','PreCompact','PostCompact','Stop','PreToolUse'):
        return {}
    deadline = Deadline(140 if event == 'UserPromptSubmit' else 8)
    state, config = None, None
    try:
        config = binding(path, payload)
        if config is None:
            return _deny('Jasmine P3 execution binding is inactive') if event=='PreToolUse' else {}
        lease = Lease(path, deadline)
        with lease.locked():
            state = lease.read()
            if state and state['session_id'] != payload['session_id']:
                raise ValueError('session lease mismatch')
            binding_only=bool(state and state.get('phase')=='BINDING_ONLY')
            if binding_only:
                seed_fields={'phase','task_id','step_id','project_id','host_id','session_id','generation','core_session_id','turn_id'}
                if (set(state)!=seed_fields or state['generation']!='0' or state['core_session_id'] is not None
                    or state['turn_id'] is not None or state['project_id']!=config['project_id'] or state['host_id']!=config['host_id']
                    or not ids.is_id(state['task_id'],'tsk') or not ids.is_id(state['step_id'],'stp')):
                    raise ValueError('invalid trusted binding-only seed')
            if event=='PreToolUse':
                allowed=('jasmine_read','jasmine_patch','jasmine_test','jasmine_playwright')
                end=state.get('ready_deadline_monotonic') if state else None
                if (payload.get('tool_name') not in allowed or not state or state.get('phase')!='READY'
                    or state.get('session_id')!=payload['session_id'] or state.get('turn_id')!=payload['turn_id']
                    or not ids.is_id(state.get('event_id'),'evt') or not ids.is_id(state.get('task_id'),'tsk')
                    or not ids.is_id(state.get('step_id'),'stp') or not ids.is_id(state.get('context_pack_id'),'ctx')
                    or state.get('host_id')!=config['host_id'] or state.get('project_id')!=config['project_id']
                    or type(end) not in (int,float) or not math.isfinite(end) or time.monotonic()>=end):
                    return _deny('Jasmine P3 native tool is not current typed execution')
                deadline.remaining()
                # This only admits the declared callback to its independent Core Guard.
                return {}
            if event != 'UserPromptSubmit':
                return report_lifecycle(payload,config,lease,
                    operator_client(config,deadline,client_factory),deadline,raw_input_bytes=raw_input_bytes)
            prompt = payload.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 32768:
                raise ValueError('invalid real prompt')
            hashed = hashlib.sha256(prompt.encode()).hexdigest()
            if state and state['turn_id'] == payload['turn_id']:
                if state['prompt_sha256'] != hashed:
                    raise ValueError('same turn changed prompt')
            else:
                previous = state
                state = {'phase': 'PENDING_CAPTURE', 'session_id': payload['session_id'], 'turn_id': payload['turn_id'],
                    'prompt_sha256': hashed, 'generation': str(int(previous['generation']) + 1 if previous else 1),
                    'event_id': derive_event_id(config['host_id'], payload['session_id'], payload['turn_id'], 'p2-bound:' + prompt),
                    'task_id': previous.get('task_id') if previous else None, 'step_id': previous.get('step_id') if previous else None,
                    'requests': {}, 'previous_ref': None, 'core_session_id': None if binding_only else ((previous.get('core_session_id') if previous else None) or config['p3_core_session_id'])}
                if previous and previous['phase'] not in ('READY', 'CAPTURED_UNBOUND_BLOCKED', 'REJECTED','BINDING_ONLY'):
                    if previous.get('previous_ref'):
                        state['previous_ref'] = previous['previous_ref']
                    else:
                        archive = path.with_name(path.name + '.operation-' + previous['event_id'])
                        atomic_json(archive, previous)
                        state['previous_ref'] = str(archive)
                # Durable invalidation occurs before token read or any HTTP operation.
                lease.write(state)
            # Always commit this genuine current Raw before any prior recovery.
            _capture(state, config, payload, lease, deadline)
            if state.get('previous_ref'):
                archive = Path(state['previous_ref'])
                if archive.parent != path.parent or not archive.name.startswith(path.name + '.operation-'):
                    raise ValueError('invalid operation archive')
                prior = json.loads(_private_file(archive))
                raw = prior['requests'].get('raw')
                if raw is None:
                    raise ValueError('previous capture requires recovery')
                proxy = _PreviousLease(archive, deadline)
                recovery_payload={'hook_event_name': 'P2Recovery', 'session_id': prior['session_id'], 'turn_id': prior['turn_id'], 'current_trigger_turn_id': payload['turn_id'], 'prompt': raw['body']['payload']['text']}
                _capture(prior, config, recovery_payload, proxy, deadline)
                _admit(prior, config, recovery_payload, proxy, deadline)
                if prior['phase'] == 'REJECTED':
                    state['previous_ref'] = None
                    lease.write(state)
                elif prior['phase'] != 'SOURCE_ADMITTED':
                    return _block('previous operation pending', state)
                # Current Raw envelope is immutable. A recovered Task cannot be
                # retroactively attached to an already captured task-null source.
                if prior['phase'] == 'SOURCE_ADMITTED' and not state.get('task_id'):
                    state.update(phase='CAPTURED_UNBOUND_BLOCKED', captured_unbound_blocked=True, task_id=prior['task_id'], step_id=prior['step_id'], previous_ref=None)
                    lease.write(state)
                    return _block('next real turn must use recovered Task binding', state)
                state['previous_ref'] = None
                lease.write(state)
            if state.get('captured_unbound_blocked'):
                return _block('captured source is unbound; submit next real turn', state)
            blocked=_admit(state, config, payload, lease, deadline)
            if blocked and state.get('phase')=='PENDING_REVIEW':
                blocked=_review_wait(state,config,payload,lease,deadline)
            if blocked: return blocked
            text,receipt=build_current_context(config,lease,operator_client(config,deadline,client_factory),deadline,tokenizer=tokenizer)
            return {'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','additionalContext':text}}
    except Exception as error:
        _trace(config, payload, 'p2:error:' + type(error).__name__, event_id=state.get('event_id') if state else None)
        if event == 'PreToolUse':
            return _deny('Jasmine admission unavailable')
        if os.environ.get('JASMINE_CORE_GATE_NONCE'):
            return _block(type(error).__name__, state)
        return {}


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--binding',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('hook input exceeds cap')
        payload=json.loads(raw)
        if not isinstance(payload,dict):raise ValueError('hook object required')
    except (ValueError,UnicodeError):payload={'hook_event_name':'UserPromptSubmit'}
    print(canonical_json(handle(payload,args.binding,raw_input_bytes=raw if payload.get("session_id") else None)))
    return 0

if __name__=='__main__':raise SystemExit(main())
