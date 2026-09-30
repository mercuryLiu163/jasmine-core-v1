"""Exact selected-encoding Context components; no native injection claims."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import DbTestCase
from jasmine_core import SCHEMA_VERSION, ids
from jasmine_core.context.builder import ContextBuilder, ContextActorMismatch
from jasmine_core.context.memory_provider import MemoryProvider
from jasmine_core.context.renderer import render, RenderOverflow
from jasmine_core.context.tokens import Tokenizer, TokenizerUnavailable
from jasmine_core.events import EventStore
from jasmine_core.models import NewObject, NewEvent
from jasmine_core.objects import ObjectStore
from jasmine_core.registry import Registry
from jasmine_core.resolution_common import Conflict
from jasmine_core.task_snapshot import read_task_snapshot


class Tokens(unittest.TestCase):
    def setUp(self):
        try:self.tokenizer=Tokenizer()
        except TokenizerUnavailable as exc:self.skipTest(str(exc))
    def test_real_unicode_special_strings_and_identity(self):
        text='中文 😀 <|im_start|>\nheaders: JSON {"é":1}'
        self.assertEqual(self.tokenizer.count(text),len(self.tokenizer.encoding.encode_ordinary(text)))
        self.assertEqual(self.tokenizer.encoding.decode(self.tokenizer.encoding.encode_ordinary(text)),text)
        self.assertEqual(self.tokenizer.identity['qualification'],'EXPLICIT_ENCODING_MODEL_UNVERIFIED')
    def test_missing_offline_asset_never_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('urllib.request.urlopen',side_effect=AssertionError('no network')):
                with self.assertRaises(TokenizerUnavailable):Tokenizer(cache_dir=directory)


class ContextFixture(DbTestCase):
    def setUp(self):
        super().setUp()
        try:self.tokenizer=Tokenizer()
        except TokenizerUnavailable as exc:self.skipTest(str(exc))
        self.host=ids.new_id('hst');self.human=ids.new_id('act');self.agent=ids.new_id('act')
        reg=Registry(self.conn);reg.upsert_host(self.host)
        reg.upsert_actor(self.human,kind='human',home_host_id=self.host)
        reg.upsert_actor(self.agent,kind='agent',home_host_id=self.host)
        objects=ObjectStore(self.conn,schema_version=SCHEMA_VERSION)
        self.project=objects.create(NewObject.project({'name':'context','host_id':self.host}),actor_id=self.human)['object']['project_id']
        self.task=objects.create(NewObject.task({'project_id':self.project,'title':'Current goal','host_id':self.host}),actor_id=self.human)['object']['task_id']
        self.event=EventStore(self.conn,schema_version=SCHEMA_VERSION).append(NewEvent(event_type='user.prompt',source_system='component',occurred_at=self.clock.now(),actor_id=self.human,actor_kind='human',host_id=self.host,project_id=self.project,task_id=self.task,payload={'text':'Actual prompt'}))[0]['event_id']
        self.calls=0
        def scan(budget,*,conn):
            self.assertFalse(conn.in_transaction);budget.remaining();self.calls+=1
            return {'snapshot':{'complete':False,'partial_reasons':['component']},'sample_window':{'started_at_unix':1,'finished_at_unix':2}}
        self.builder=ContextBuilder(self.conn,schema_version=SCHEMA_VERSION,tokenizer=self.tokenizer,scanner=scan)
    def body(self,key='ctx'):
        return {'task_id':self.task,'host_id':self.host,'source_event_id':self.event,'reason':'USER_PROMPT','idempotency_key':key}
    def checking(self):return {'host_id':self.host,'source_event_id':self.event}


class Context(ContextFixture):
    def test_exact_pack_original_replay_and_current_admission(self):
        before=read_task_snapshot(self.conn,self.task)['truth_digest']
        result=self.builder.build(self.body(),actor_id=self.agent)['context']
        self.assertLessEqual(result['token_count'],2500)
        self.assertEqual(result['token_count'],self.tokenizer.count(result['rendered_content']))
        self.assertEqual(sum(m['prefix_delta_tokens'] for m in result['section_metrics']),result['token_count'])
        self.assertEqual(result['memory']['status'],'not_configured')
        self.assertEqual(before,read_task_snapshot(self.conn,self.task)['truth_digest'])
        check=self.builder.check_current(result['context_pack_id'],self.checking(),actor_id=self.agent)
        self.assertTrue(check['current'])
        with self.assertRaises(ContextActorMismatch):self.builder.check_current(result['context_pack_id'],self.checking(),actor_id=self.human)
        self.conn.execute('UPDATE tasks SET revision=revision+1 WHERE task_id=?',(self.task,))
        replay=self.builder.build(self.body(),actor_id=self.agent)
        self.assertTrue(replay['replayed']);self.assertEqual(replay['context'],result);self.assertEqual(self.calls,1)
        with self.assertRaises(Conflict) as got:self.builder.check_current(result['context_pack_id'],self.checking(),actor_id=self.agent)
        self.assertEqual(got.exception.code,'context_stale')
    def test_event_first_failure_rolls_back_and_exactkey_conflict(self):
        count=self.conn.execute('SELECT count(*) FROM events').fetchone()[0]
        original=self.builder._persist
        def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('after receipt insert')
        with patch.object(self.builder,'_persist',fail):
            with self.assertRaises(RuntimeError):self.builder.build(self.body(),actor_id=self.human)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM events').fetchone()[0],count)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM context_packs').fetchone()[0],0)
        self.builder.build(self.body(),actor_id=self.human)
        body=self.body();body['reason']='MANUAL'
        with self.assertRaises(Conflict):self.builder.build(body,actor_id=self.human)
    def test_current_configuration_change_rejects_fresh_not_historical(self):
        result=self.builder.build(self.body(),actor_id=self.agent)['context']
        self.builder.memory=MemoryProvider(provider_id='changed-off-policy')
        self.assertEqual(self.builder.build(self.body(),actor_id=self.agent)['context'],result)
        with self.assertRaises(Conflict) as got:self.builder.check_current(result['context_pack_id'],self.checking(),actor_id=self.agent)
        self.assertEqual(got.exception.code,'context_config_changed')
    def test_full_hard_and_normal_rules_mandatory_overflow(self):
        snapshot=read_task_snapshot(self.conn,self.task)['truth_projection']
        normal={'rule_id':ids.new_id('rul'),'version':1,'revision':2,'scope':{'kind':'task','task_id':self.task,'project_id':self.project},'kind':'RULE','severity':'NORMAL','enforcement':'CONTEXT','content':'Use Playwright skill','origin_event_id':self.event}
        hard={**normal,'rule_id':ids.new_id('rul'),'severity':'HARD','content':'Never weaken this boundary.'}
        snapshot['active_rules']=[normal,hard]
        comparison={'checkpoint_freshness':'ABSENT','workspace':'UNKNOWN','reconciliation_required':True,'diff':{}}
        data=render(snapshot,comparison,None,{'status':'not_configured','selected':[]},self.tokenizer,source_event_id=self.event)
        self.assertIn(normal['content'],data['rendered_content']);self.assertIn(hard['content'],data['rendered_content'])
        self.assertIn(self.event,data['rendered_content'])
        hard['content']='hard boundary '*4000
        with self.assertRaises(RenderOverflow) as got:render(snapshot,comparison,None,{'status':'empty'},self.tokenizer,source_event_id=self.event)
        self.assertEqual(got.exception.code,'AUTHORITY_TOO_LARGE')

class MemoryWorkers(unittest.TestCase):
    def worker(self,directory,code):
        import sys
        path=Path(directory)/'worker.py';path.write_text(code)
        return MemoryProvider(argv=[str(Path(sys.executable).resolve()),str(path.resolve())],provider_id='component')
    def query(self):
        return {'banks':[{'scope':'PROJECT','bank_id':'jasmine-project-'+ids.new_id('prj'),'project_id':None}]}
    def test_owned_worker_complete_bank_status_and_cleared_env(self):
        from jasmine_core.continuity_scan import Budget
        program='''import json,os,sys
q=json.load(sys.stdin)
banks=[{**b,'status':'empty','error_code':None} for b in q['banks']]
print(json.dumps({'status':'empty','provider_id':os.environ['JASMINE_MEMORY_PROVIDER_ID'],'provider_config_digest':os.environ['JASMINE_MEMORY_CONFIG_DIGEST'],'banks':banks,'items':[],'query_trace_id':None,'error_code':None}))
'''
        project=ids.new_id('prj');query={'banks':[{'scope':'PROJECT','bank_id':'jasmine-project-'+project,'project_id':project}]}
        with tempfile.TemporaryDirectory() as directory:
            provider=self.worker(directory,program)
            class NoTransaction:in_transaction=False
            with patch.dict(os.environ,{'OPENAI_API_KEY':'should-not-inherit','JASMINE_OLD_TOKEN':'secret'}):
                result=provider.recall(query,Budget(),conn=NoTransaction())
            self.assertEqual(result['status'],'empty');self.assertEqual(len(result['banks']),1)
            config=provider.configuration()
            self.assertEqual(len(config['worker_identity']),2)
            changed=Path(directory)/'worker.py';changed.write_text(program+'\n# operator update\n')
            self.assertNotEqual(config['config_digest'],provider.configuration()['config_digest'])
    def test_missing_requested_bank_degrades_and_timeout_is_bounded(self):
        from jasmine_core.continuity_scan import Budget
        class NoTransaction:in_transaction=False
        project=ids.new_id('prj');query={'banks':[{'scope':'PROJECT','bank_id':'jasmine-project-'+project,'project_id':project}]}
        with tempfile.TemporaryDirectory() as directory:
            provider=self.worker(directory,"import json,os,sys;json.load(sys.stdin);print(json.dumps({'status':'ok','provider_id':os.environ['JASMINE_MEMORY_PROVIDER_ID'],'provider_config_digest':os.environ['JASMINE_MEMORY_CONFIG_DIGEST'],'banks':[],'items':[],'query_trace_id':None,'error_code':None}))")
            self.assertEqual(provider.recall(query,Budget(),conn=NoTransaction())['status'],'degraded')
            Path(directory,'worker.py').write_text('import time;time.sleep(10)')
            import time
            start=time.monotonic();result=provider.recall(query,Budget(.1),conn=NoTransaction())
            self.assertLess(time.monotonic()-start,1);self.assertEqual(result['status'],'degraded')


class Emission(ContextFixture):
    # Inherit the fixture, but explicitly select this test class alone when
    # testing the adapter; inherited business checks remain useful regression.
    def test_component_exact_emission_no_old_snapshot_and_stale_block(self):
        from jasmine_core.capture.p3_context_adapter import prepare_emission
        from jasmine_core.capture.p2_runtime import Deadline
        state={'session_id':'native-component','turn_id':'turn','generation':'generation',
               'host_id':self.host,'project_id':self.project,'phase':'SOURCE_ADMITTED','event_id':self.event,'task_id':self.task,'step_id':None,'context_text':'obsolete P2 minimal snapshot'}
        class Lease:
            def read(inner):return copy.deepcopy(state)
            def write(inner,value):state.clear();state.update(copy.deepcopy(value))
        outer=self
        class Client:
            def post(inner,path,body,**kwargs):
                if path=='/v1/context/build':return outer.builder.build(body,actor_id=outer.agent)
                return outer.builder.check_current(path.split('/')[3],body,actor_id=outer.agent)
        request=self.body()
        text,receipt=prepare_emission(Client(),Lease(),Deadline(),request,session_id='native-component',
            turn_id='turn',generation='generation',hook_definition_hash='0'*64,tokenizer=self.tokenizer,actor_id=self.agent)
        self.assertEqual(text,self.builder.get(receipt['context_pack_id'])['rendered_content'])
        self.assertNotIn('context_text',state);self.assertEqual(state['phase'],'READY')
        self.conn.execute('UPDATE tasks SET revision=revision+1 WHERE task_id=?',(self.task,))
        with self.assertRaises(Conflict):prepare_emission(Client(),Lease(),Deadline(),request,session_id='native-component',
            turn_id='turn',generation='generation',hook_definition_hash='0'*64,tokenizer=self.tokenizer,actor_id=self.agent)
        self.assertEqual(state['phase'],'CONTEXT_PENDING')
