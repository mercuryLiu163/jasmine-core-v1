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
