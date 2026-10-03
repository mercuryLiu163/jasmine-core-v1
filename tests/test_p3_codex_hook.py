import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from jasmine_core import ids
from jasmine_core.capture import p3_codex_hook as hook
from jasmine_core.capture.p2_runtime import Deadline

class MemoryLease:
 def __init__(self,state=None):self.state=state;self.writes=[]
 def read(self):return self.state
 def write(self,state):self.state=dict(state);self.writes.append(dict(state))
 @contextlib.contextmanager
 def locked(self):yield self

class HookTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();os.chmod(self.root,0o700)
    self.adapter={'actor_id':ids.new_id('act'),'receipt_root':str(self.root),'hook_definition_sha256':'a'*64,'profile_sha256':'b'*64,'deployment_sha256':'c'*64}
    path=self.root/'adapter.json';path.write_text(json.dumps(self.adapter));os.chmod(path,0o600)
    self.config={'host_id':ids.new_id('hst'),'project_id':ids.new_id('prj'),'p3_adapter_config_file':str(path),'p3_core_session_id':ids.new_id('ses')}
    self.state={'session_id':'native-thread','turn_id':'native-turn','generation':'1','task_id':ids.new_id('tsk'),'step_id':ids.new_id('stp'),'core_session_id':self.config['p3_core_session_id'],'event_id':ids.new_id('evt'),'phase':'READY'}
 def test_raw_first_failure_durably_blocks(self):
    lease=MemoryLease();payload={'hook_event_name':'UserPromptSubmit','session_id':'native-thread','turn_id':'native-turn','prompt':'actual original'}
    def fail_capture(state,*args):
        self.assertEqual(lease.writes[0]['phase'],'PENDING_CAPTURE')
        self.assertEqual(state['prompt_sha256'],hashlib.sha256(b'actual original').hexdigest())
        raise TimeoutError()
    with patch.object(hook,'binding',return_value=self.config),patch.object(hook,'Lease',return_value=lease),patch.object(hook,'_capture',side_effect=fail_capture),patch.object(hook,'_trace'),patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':'active'}):
        result=hook.handle(payload,self.root/'binding')
    self.assertEqual(result['decision'],'block');self.assertNotIn('hookSpecificOutput',result)
 def test_only_p3_context_result_emitted(self):
    lease=MemoryLease();payload={'hook_event_name':'UserPromptSubmit','session_id':'native-thread','turn_id':'native-turn','prompt':'original'}
    def admit(state,*args):state.update(phase='SOURCE_ADMITTED',task_id=self.state['task_id'],step_id=self.state['step_id']);lease.write(state)
    with patch.object(hook,'binding',return_value=self.config),patch.object(hook,'Lease',return_value=lease),patch.object(hook,'_capture'),patch.object(hook,'_admit',side_effect=admit),patch.object(hook,'operator_client'),patch.object(hook,'build_current_context',return_value=('exact P3 bytes',{})),patch.object(hook,'_trace'):
        result=hook.handle(payload,self.root/'binding')
    self.assertEqual(result,{'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','additionalContext':'exact P3 bytes'}})
 def test_lifecycle_raw_receipt_order_and_postcompact_no_context(self):
    calls=[]
    class Client:
      def post(self,path,body,**kwargs):
        calls.append((path,body))
        if path.endswith('/report'):return {'report':{'reported_event_id':body['event_id'],'status':'REPORT_ACCEPTED'}}
        return {'checkpoint':{'checkpoint_id':ids.new_id('ckp')}}
    for name in ('Stop','PreCompact','PostCompact'):
      calls.clear();payload={'hook_event_name':name,'session_id':'native-thread','turn_id':'native-turn'}
      raw=('  '+json.dumps(payload)+'\n').encode()
      result=hook.report_lifecycle(payload,self.config,MemoryLease(self.state),Client(),Deadline(),raw_input_bytes=raw)
      self.assertEqual(result,{})
      self.assertEqual(calls[0][0],'/v1/adapter/lifecycle/report')
      self.assertIsNone(calls[0][1]['hook_run_id'])
      receipt=json.loads((self.root/(calls[0][1]['callback_id']+'.json')).read_text())
      self.assertEqual(receipt['input_sha256'],hashlib.sha256(raw).hexdigest())
      self.assertEqual(receipt['input_raw_utf8'],raw.decode())
      self.assertEqual(len(calls),1 if name=='PostCompact' else 2)
 def test_independent_compact_turn_keeps_ready_source_binding(self):
    class Client:
      def post(self,path,body,**kwargs):
        if path.endswith('/report'):return {'report':{'reported_event_id':body['event_id'],'status':'REPORT_ACCEPTED'}}
        return {'checkpoint':{'checkpoint_id':ids.new_id('ckp')}}
    lease=MemoryLease(dict(self.state))
    for name in ('PreCompact','PostCompact'):
      payload={'hook_event_name':name,'session_id':'native-thread','turn_id':'compact-turn'}
      hook.report_lifecycle(payload,self.config,lease,Client(),Deadline())
      self.assertEqual(lease.state['turn_id'],'native-turn')
      self.assertEqual(lease.state['phase'],'READY')
      self.assertEqual(lease.state['generation'],'1')
    lease=MemoryLease(dict(self.state));before=dict(lease.state)
    payload={'hook_event_name':'PreCompact','session_id':'native-thread','turn_id':'compact-turn'}
    with self.assertRaises(ValueError):hook.report_lifecycle(payload,self.config,lease,Client(),Deadline(),raw_input_bytes=b'{}')
    self.assertEqual(lease.state,before);self.assertEqual(lease.writes,[])
    with self.assertRaises(TimeoutError):hook.report_lifecycle(payload,self.config,lease,Client(),Deadline(end=0))
    self.assertEqual(lease.state,before);self.assertEqual(lease.writes,[])

 def test_compact_report_http_failure_and_checkpoint_failure_do_not_adopt_turn(self):
    payload={'hook_event_name':'PreCompact','session_id':'native-thread','turn_id':'compact-turn'}
    class Client:
      def __init__(self,report_fails):self.report_fails=report_fails
      def post(self,path,body,**kwargs):
        if path.endswith('/report') and not self.report_fails:return {'report':{'reported_event_id':body['event_id'],'status':'REPORT_ACCEPTED'}}
        raise TimeoutError()
    for fails in (True,False):
      lease=MemoryLease(dict(self.state))
      with self.assertRaises(TimeoutError):hook.report_lifecycle(payload,self.config,lease,Client(fails),Deadline())
      self.assertEqual(lease.state['turn_id'],'native-turn');self.assertEqual(lease.state['phase'],'READY')
      self.assertEqual(bool(lease.state.get('last_precompact_report_id')),not fails)
      self.assertNotIn('last_lifecycle_checkpoint_id',lease.state)
      self.assertFalse(list(self.root.glob('*_checkpoint.json')))

 def test_compact_turn_tool_denied_and_next_prompt_gets_new_generation_without_recovery(self):
    state={**self.state,'host_id':self.config['host_id'],'project_id':self.config['project_id'],
        'context_pack_id':ids.new_id('ctx'),'ready_deadline_monotonic':time.monotonic()+30,
        'last_precompact_report_id':ids.new_id('evt')}
    lease=MemoryLease(state)
    def capture(current,*args):
      self.assertEqual(current['turn_id'],'next-prompt');self.assertEqual(current['generation'],'2')
      self.assertIsNone(current['previous_ref']);raise TimeoutError()
    with patch.object(hook,'binding',return_value=self.config),patch.object(hook,'Lease',return_value=lease),patch.object(hook,'_trace'),patch.object(hook,'_capture',side_effect=capture) as captured,patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':'active'}):
      result=hook.handle({'hook_event_name':'PreToolUse','session_id':'native-thread','turn_id':'compact-turn','tool_name':'jasmine_read'},self.root/'binding')
      self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
      hook.handle({'hook_event_name':'UserPromptSubmit','session_id':'native-thread','turn_id':'next-prompt','prompt':'real next'},self.root/'binding')
      self.assertEqual(captured.call_count,1)
      self.assertNotIn('last_precompact_report_id',lease.state)

 def test_next_prompt_completes_real_hook_admit_and_context_ready_component_chain(self):
    # HTTP replies are synthetic components. Capture/admit/context helpers are real;
    # this is not extractor/model/native proof or a real Core integration test.
    from jasmine_core.canonical import sha256_hex
    from jasmine_core.capture import p2_codex_hook
    token=self.root/'token';token.write_text('component-token');token.chmod(0o600)
    config={**self.config,'core_url':'http://127.0.0.1:1','token_file':str(token),
        'human_token_file':str(token),'p3_operator_token_file':str(token),'trace_file':str(self.root/'trace')}
    lease=MemoryLease({**self.state,'prompt_sha256':'old','last_precompact_report_id':ids.new_id('evt')})
    calls=[];interpretation=ids.new_id('int');resolution=ids.new_id('res');pack_id=ids.new_id('ctx')
    tokenizer=type('Tokenizer',(),{'identity':{'component':'synthetic'},'count':lambda self,text:len(text)})()
    class Client:
      def request(self,method,path,body=None,**kwargs):
        return self.post(path,body,**kwargs) if method=='POST' else self.get(path)
      def post(self,path,body,**kwargs):
        calls.append((path,body))
        if path=='/v1/events':return {'event':{'event_id':body['event_id']}}
        if path=='/v1/interpret':return {'interpretation':{'interpretation_id':interpretation,'status':'EXTRACTED'}}
        if path.startswith('/v1/resolve/'):
          return {'resolution':{'resolution_id':resolution,'result':{'disposition':'NO_ACTION','review_id':None,'actions':[]}}}
        if path=='/v1/context/build':
          text='fresh component context'
          return {'context':{'context_pack_id':pack_id,'rendered_content':text,'actor_id':self_actor,
            'host_id':body['host_id'],'project_id':config['project_id'],'source_event_id':body['source_event_id'],
            'task_id':body['task_id'],'session_id':body['session_id'],'current_step_id':body['current_step_id'],
            'rendered_sha256':sha256_hex(text),'rendered_utf8_bytes':len(text.encode()),'token_count':len(text),
            'tokenizer':tokenizer.identity,'truth_digest':'truth','selector':{'version':'component'}}}
        if path.endswith('/check-current'):
          return {'current':True,'context_pack_id':pack_id,'truth_digest':'truth','selector':{'version':'component'},
            'rendered_sha256':sha256_hex('fresh component context'),'checked_at':'component-time'}
        raise AssertionError(path)
      def get(self,path):
        if path.startswith('/v1/interpretations/'):
          return {'interpretation':{'maintenance_head':{'status':'CURRENT','current_interpretation_id':interpretation}}}
        if path.startswith('/v1/resolutions/preview'):
          return {'policy_version':'component','expected_revisions':{},'expected_context_digest':'component'}
        raise AssertionError(path)
    self_actor=self.adapter['actor_id'];client=Client();factory=lambda *a:client
    payload={'hook_event_name':'UserPromptSubmit','session_id':'native-thread','turn_id':'next-prompt','prompt':'real next input'}
    with patch.object(hook,'binding',return_value=config),patch.object(hook,'Lease',return_value=lease),patch.object(hook,'DeadlineClient',side_effect=factory),patch.object(p2_codex_hook,'DeadlineClient',side_effect=factory):
      result=hook.handle(payload,self.root/'binding',client_factory=factory,tokenizer=tokenizer)
    self.assertEqual(result['hookSpecificOutput']['additionalContext'],'fresh component context')
    self.assertEqual(lease.state['phase'],'READY');self.assertEqual(lease.state['generation'],'2')
    self.assertEqual(lease.state['turn_id'],'next-prompt');self.assertIsNone(lease.state['previous_ref'])
    self.assertEqual(lease.state['context_pack_id'],pack_id)
    self.assertEqual(lease.state['transmission_receipt']['turn_id'],'next-prompt')
    self.assertEqual([path for path,_ in calls],['/v1/events','/v1/interpret','/v1/resolve/'+interpretation,'/v1/context/build','/v1/context/'+pack_id+'/check-current'])
    self.assertEqual(calls[0][1]['payload']['text'],'real next input')
    self.assertNotIn('last_precompact_report_id',lease.state)

 def test_expired_and_unbound_lifecycle_never_reports(self):
    class Client:
      def post(self,*args,**kwargs):raise AssertionError('must not call')
    payload={'hook_event_name':'Stop','session_id':'native-thread','turn_id':'native-turn'}
    self.assertEqual(hook.report_lifecycle(payload,self.config,MemoryLease(),Client(),Deadline())['decision'],'block')
    with self.assertRaises(TimeoutError):hook.report_lifecycle(payload,self.config,MemoryLease(self.state),Client(),Deadline(end=0))
 def test_sessionstart_null_turn_unbound_is_explicit(self):
    payload={'hook_event_name':'SessionStart','session_id':'fresh-native','turn_id':None}
    config={**self.config,'mode':2,'run_nonce':'n'*32,'core_url':'http://127.0.0.1:12345','token_file':str(self.root/'token'),'human_token_file':str(self.root/'human'),'trace_file':str(self.root/'trace'),'p3_operator_token_file':str(self.root/'operator')}
    path=self.root/'binding';path.write_text(json.dumps(config));os.chmod(path,0o600)
    with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':'n'*32}):
        self.assertEqual(hook.binding(path,payload),config)
        with patch.object(hook,'Lease',return_value=MemoryLease()),patch.object(hook,'operator_client'),patch.object(hook,'_capture') as capture:
            result=hook.handle(payload,path)
    self.assertEqual(result['decision'],'block');capture.assert_not_called()

 def test_pretool_exact_ready_and_stale_unknown_denials(self):
    state={**self.state,'host_id':self.config['host_id'],'project_id':self.config['project_id'],'context_pack_id':ids.new_id('ctx'),'ready_deadline_monotonic':time.monotonic()+30}
    payload={'hook_event_name':'PreToolUse','session_id':'native-thread','turn_id':'native-turn','tool_name':'jasmine_read'}
    for change,tool,allowed in [({},'jasmine_read',True),({},'exec_command',False),({'phase':'CONTEXT_PENDING'},'jasmine_patch',False),({'ready_deadline_monotonic':0},'jasmine_test',False),({'turn_id':'older'},'jasmine_playwright',False)]:
      with self.subTest(change=change,tool=tool),patch.object(hook,'binding',return_value=self.config),patch.object(hook,'Lease',return_value=MemoryLease({**state,**change})),patch.object(hook,'_trace'):
        result=hook.handle({**payload,'tool_name':tool},self.root/'binding')
        if allowed:self.assertEqual(result,{})
        else:self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')

 def test_pretool_binding_failure_denies(self):
    payload={'hook_event_name':'PreToolUse','session_id':'native-thread','turn_id':'native-turn','tool_name':'jasmine_read'}
    for result in (None,ValueError('invalid binding')):
      with patch.object(hook,'binding',**({'side_effect':result} if isinstance(result,Exception) else {'return_value':result})),patch.object(hook,'_trace'):
        denied=hook.handle(payload,self.root/'binding')
      self.assertEqual(denied['hookSpecificOutput']['permissionDecision'],'deny')
 def test_core_session_request_survives_lost_response(self):
    state={**self.state,'phase':'SOURCE_ADMITTED','core_session_id':None};lease=MemoryLease(state);calls=[]
    session_id=ids.new_id('ses')
    class Client:
      def post(self,path,body,**kwargs):
        calls.append((path,dict(body)))
        if len(calls)==1:raise TimeoutError('response lost')
        return {'object':{'session_id':session_id,'task_id':state['task_id'],'project_id':self_config['project_id'],'host_id':self_config['host_id']}}
    self_config=self.config;client=Client()
    with self.assertRaises(TimeoutError):hook.build_current_context(self.config,lease,client,Deadline(),tokenizer=object())
    saved=dict(lease.state['session_request'])
    with patch.object(hook,'prepare_emission',return_value=('context',{})) as emission:
      hook.build_current_context(self.config,lease,client,Deadline(),tokenizer=object())
    self.assertEqual(calls[0],calls[1]);self.assertEqual(calls[1][1],saved)
    self.assertEqual(lease.state['core_session_id'],session_id)
    self.assertEqual(emission.call_args.args[3]['session_id'],session_id)

 def test_binding_only_new_raw_precedes_admission_no_old_recovery(self):
    seed={'phase':'BINDING_ONLY','task_id':self.state['task_id'],'step_id':self.state['step_id'],'project_id':self.config['project_id'],'host_id':self.config['host_id'],'session_id':'new-native','generation':'0','core_session_id':None,'turn_id':None}
    lease=MemoryLease(seed);payload={'hook_event_name':'UserPromptSubmit','session_id':'new-native','turn_id':'new-turn','prompt':'继续'};sequence=[]
    def capture(state,*args):
      sequence.append('raw');self.assertEqual(state['task_id'],seed['task_id']);self.assertIsNone(state['core_session_id']);self.assertIsNone(state['previous_ref']);self.assertEqual(state['generation'],'1')
    def admit(state,*args):sequence.append('admit');state['phase']='SOURCE_ADMITTED';lease.write(state)
    with patch.object(hook,'binding',return_value=self.config),patch.object(hook,'Lease',return_value=lease),patch.object(hook,'_capture',side_effect=capture),patch.object(hook,'_admit',side_effect=admit),patch.object(hook,'operator_client'),patch.object(hook,'build_current_context',return_value=('fresh P3 context',{})),patch.object(hook,'_trace'):
      result=hook.handle(payload,self.root/'binding')
    self.assertEqual(sequence,['raw','admit']);self.assertEqual(result['hookSpecificOutput']['additionalContext'],'fresh P3 context')
    with patch.object(hook,'binding',return_value=self.config),patch.object(hook,'Lease',return_value=MemoryLease(seed)),patch.object(hook,'_trace'):
      denied=hook.handle({**payload,'hook_event_name':'PreToolUse','tool_name':'jasmine_read'},self.root/'binding')
    self.assertEqual(denied['hookSpecificOutput']['permissionDecision'],'deny')
 def test_old_ready_cross_thread_still_blocks(self):
    payload={'hook_event_name':'UserPromptSubmit','session_id':'other-thread','turn_id':'new-turn','prompt':'继续'}
    with patch.object(hook,'binding',return_value=self.config),patch.object(hook,'Lease',return_value=MemoryLease(self.state)),patch.object(hook,'_trace'),patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':'active'}),patch.object(hook,'_capture') as capture:
      result=hook.handle(payload,self.root/'binding')
    self.assertEqual(result['decision'],'block');capture.assert_not_called()

class ReviewWaitTests(unittest.TestCase):
 setUp=HookTests.setUp
 def pending(self):
    from jasmine_core.canonical import canonical_json
    from jasmine_core.capture.codex_user_prompt_submit import derive_event_id
    token=self.root/'reader';token.write_text('synthetic');token.chmod(0o600)
    config={**self.config,'p3_review_wait':True,'run_nonce':'n'*32,'core_url':'http://127.0.0.1:1','token_file':str(token)}
    payload={'hook_event_name':'UserPromptSubmit','session_id':'native-thread','turn_id':'native-turn','prompt':'继续'}
    state={**self.state,'phase':'PENDING_REVIEW','prompt_sha256':hashlib.sha256('继续'.encode()).hexdigest(),
       'event_id':derive_event_id(config['host_id'],payload['session_id'],payload['turn_id'],'p2-bound:继续'),
       'interpretation_id':ids.new_id('int'),'resolution_id':ids.new_id('res'),'review_id':ids.new_id('rvw')}
    raw={'event_type':'user.prompt','source_system':'codex-p2-bound','source_event_id':canonical_json([payload['session_id'],payload['turn_id']]),
       'event_id':state['event_id'],'host_id':config['host_id'],'project_id':config['project_id'],'task_id':state['task_id'],
       'payload':{'text':'继续','step_id':state['step_id'],'source_session_id':payload['session_id'],'turn_id':payload['turn_id']}}
    state['requests']={'raw':{'method':'POST','path':'/v1/events','body':raw}}
    return config,payload,state
 def client(self,state,statuses,change=None):
    owner=self
    class Client:
      def get(self,path):
        if path.startswith('/v1/events/'):
          return {'event':{**state['requests']['raw']['body'],'actor_kind':'human'}}
        if path.startswith('/v1/tasks/'):
          return {'task':{'project_id':owner.config['project_id']}}
        if path.startswith('/v1/steps/'):
          return {'step':{'task_id':state['task_id']}}
        if path.startswith('/v1/interpretations/'):
          return {'interpretation':{'maintenance_head':{'status':'NORMAL','current_interpretation_id':state['interpretation_id']}}}
        status=statuses.pop(0) if len(statuses)>1 else statuses[0]
        if change:change()
        return {'review':{'review_id':state['review_id'],'resolution_id':state['resolution_id'],'status':status,
           'resolution':{'interpretation_id':state['interpretation_id'],'source_event_id':state['event_id']}}}
      def post(self,*args,**kwargs):owner.fail('Hook must not approve')
    return Client()
 def test_review_wait_same_state_same_deadline_reenters_admission(self):
    # Synthetic HTTP replies: this proves the component contract only.
    config,payload,state=self.pending();lease=MemoryLease(state);deadline=Deadline();requests=json.loads(json.dumps(state['requests']))
    with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':config['run_nonce']}),patch.object(hook,'DeadlineClient',return_value=self.client(state,['PENDING','APPROVED'])),patch.object(hook.time,'sleep'),patch.object(hook,'_admit',return_value=None) as admit:
      self.assertIsNone(hook._review_wait(state,config,payload,lease,deadline))
    self.assertIs(admit.call_args.args[0],state);self.assertIs(admit.call_args.args[-1],deadline)
    self.assertEqual(state['requests'],requests);self.assertEqual(state['review_wait_deadline_monotonic'],deadline.end)
 def test_review_wait_ineligible_does_not_poll(self):
    for config_change,state_change,payload_change,nonce in [({'p3_review_wait':False},{},{},'n'*32),({}, {'task_id':None},{},'n'*32),({}, {},{'prompt':'继续 '},'n'*32),({}, {},{},'other'),({}, {'event_id':ids.new_id('evt')},{},'n'*32)]:
      config,payload,state=self.pending();config.update(config_change);payload.update(payload_change);state.update(state_change)
      with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':nonce}),patch.object(hook,'DeadlineClient') as client:
        self.assertEqual(hook._review_wait(state,config,payload,MemoryLease(state),Deadline())['decision'],'block')
      client.assert_not_called()
 def test_review_wait_rejection_unknown_identity_change_and_expiry(self):
    for status in ('REJECTED','UNKNOWN','SOURCE_REPLAYED'):
      config,payload,state=self.pending()
      with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':config['run_nonce']}),patch.object(hook,'DeadlineClient',return_value=self.client(state,[status])),patch.object(hook,'_admit') as admit:
        self.assertEqual(hook._review_wait(state,config,payload,MemoryLease(state),Deadline())['decision'],'block');admit.assert_not_called()
    config,payload,state=self.pending();lease=MemoryLease(state)
    def changed():lease.state={**lease.state,'task_id':ids.new_id('tsk')}
    with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':config['run_nonce']}),patch.object(hook,'DeadlineClient',return_value=self.client(state,['PENDING'],changed)),patch.object(hook.time,'sleep'),patch.object(hook,'_admit') as admit:
      self.assertEqual(hook._review_wait(state,config,payload,lease,Deadline())['decision'],'block');admit.assert_not_called()
    with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':config['run_nonce']}),patch.object(hook,'DeadlineClient'),self.assertRaises(TimeoutError):
      hook._review_wait(state,config,payload,MemoryLease(state),Deadline(end=0))
 def test_review_wait_binding_option_is_strict_optional_boolean(self):
    config,payload,state=self.pending()
    config.update(mode=2,human_token_file=str(self.root/'human'),trace_file=str(self.root/'trace'),p3_operator_token_file=str(self.root/'operator'))
    for value in (False,True,None,1,'true'):
      candidate={**config,'p3_review_wait':value};path=self.root/'binding';path.write_text(json.dumps(candidate));path.chmod(0o600)
      with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':config['run_nonce']}):
        if type(value) is bool:self.assertEqual(hook.binding(path,payload),candidate)
        else:
          with self.assertRaises(ValueError):hook.binding(path,payload)
 def test_approved_same_raw_emits_once_current_generation_context(self):
    config,payload,pending=self.pending();lease=MemoryLease({**self.state,'turn_id':'older','generation':'7','prompt_sha256':'old'})
    seen=[];captured=[]
    def capture(state,*args):
      captured.append(state['event_id']);state['requests']=pending['requests'];state['phase']='CAPTURED';lease.write(state)
    def admit(state,*args,**kwargs):
      seen.append((state['event_id'],state['generation'],dict(state['requests'])))
      if len(seen)==1:
        state.update(phase='PENDING_REVIEW',interpretation_id=pending['interpretation_id'],resolution_id=pending['resolution_id'],review_id=pending['review_id']);lease.write(state)
        return hook._block('review required',state)
      state['phase']='SOURCE_ADMITTED';lease.write(state)
    def context(config,lease,client,deadline,**kwargs):
      self.assertEqual(lease.state['phase'],'SOURCE_ADMITTED');self.assertEqual(lease.state['generation'],'8')
      self.assertEqual(lease.state['event_id'],pending['event_id']);self.assertNotIn('context_pack_id',lease.state)
      return ('fresh current generation context',{})
    with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':config['run_nonce']}),patch.object(hook,'binding',return_value=config),patch.object(hook,'Lease',return_value=lease),patch.object(hook,'_capture',side_effect=capture),patch.object(hook,'_admit',side_effect=admit),patch.object(hook,'DeadlineClient',return_value=self.client(pending,['APPROVED'])),patch.object(hook,'operator_client'),patch.object(hook,'build_current_context',side_effect=context) as build:
      result=hook.handle(payload,self.root/'binding')
    self.assertEqual(result['hookSpecificOutput']['additionalContext'],'fresh current generation context')
    self.assertEqual(len(captured),1);self.assertEqual(seen[0],seen[1]);self.assertEqual(build.call_count,1)
