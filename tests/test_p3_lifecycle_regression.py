"""Lifecycle boundary regression components; not native Gate evidence."""
import hashlib
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from jasmine_core import ids
from jasmine_core.canonical import canonical_json
from jasmine_core.adapter.lifecycle import LifecycleStore
from jasmine_core.adapter.protocol import event_id
from jasmine_core.resolution_common import Conflict

class LifecycleBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close)
        self.actor=SimpleNamespace(actor_id=ids.new_id('act'))
        self.lease={'session_id':'thread','turn_id':'source-turn','generation':'4','task_id':ids.new_id('tsk'),
            'step_id':ids.new_id('stp'),'host_id':ids.new_id('hst')}
        self.core=SimpleNamespace(conn=self.conn,schema_version=1)
        self.records={};self.callback=None
        self.config=SimpleNamespace(value={k:'a'*64 for k in ('hook_definition_sha256','profile_sha256','deployment_sha256')},
            config_sha256='d'*64,principal=lambda *a:None,hook_definition=lambda:'/reviewed/hooks.json',
            receipt=lambda *a:(self.callback,'rawsha'),lease=lambda:self.lease)
        self.store=LifecycleStore(self.core,config=self.config)
        self.store._read=lambda ident,**kw:self.records.get(ident)
        self.store._key=lambda *a:('key',None,None)
        self.store._bind_key=lambda *a:None
        def record(ident,kind,body,payload,principal,project):
            self.records[ident]={'event_type':kind,'actor_id':principal.actor_id,'payload':payload}
        self.store._event=record
        self.source=patch('jasmine_core.adapter.lifecycle.source_for_task',return_value=(None,None,{'project_id':ids.new_id('prj')}))
        self.source.start();self.addCleanup(self.source.stop)
    def body(self,name,turn='compact-turn',generation='4'):
        raw=canonical_json({'hook_event_name':name,'session_id':'thread','turn_id':turn})
        callback='callback_'+name+turn+generation
        body={'event_id':event_id('lifecycle-report','thread',callback),'idempotency_key':callback,
            'task_id':self.lease['task_id'],'current_step_id':self.lease['step_id'],'host_id':self.lease['host_id'],
            'session_id':getattr(self,'session',None) or ids.new_id('ses'),'source_event_id':getattr(self,'source_event',None) or ids.new_id('evt'),
            'native_thread_id':'thread','native_turn_id':turn,'lease_generation':generation,'callback_id':callback,
            'hook_run_id':None,'hook_event_name':name,'hook_input_sha256':hashlib.sha256(raw.encode()).hexdigest(),**self.config.value}
        self.session=body['session_id'];self.source_event=body['source_event_id']
        semantic={k:v for k,v in body.items() if k!='idempotency_key'}
        import json
        self.callback={'kind':'hook_callback','report':semantic,'input_sha256':body['hook_input_sha256'],'input_raw_utf8':raw,'input':json.loads(raw)}
        return body
    def test_real_compact_identity_pre_post_pair_and_stale_or_orphan_refusal(self):
        with self.assertRaises(Conflict):self.store.report(self.body('PostCompact'),principal=self.actor)
        pre=self.body('PreCompact');self.store.report(pre,principal=self.actor)
        self.assertEqual(self.lease['turn_id'],'source-turn')
        self.lease['last_precompact_report_id']=pre['event_id']
        self.store.report(self.body('PostCompact'),principal=self.actor)
        with self.assertRaises(Conflict):self.store.report(self.body('PostCompact','other-compact'),principal=self.actor)
        self.records[pre['event_id']]['actor_id']=ids.new_id('act')
        with self.assertRaises(Conflict):self.store.report(self.body('PostCompact','new-compact','4'),principal=self.actor)
        self.records[pre['event_id']]['actor_id']=self.actor.actor_id
        self.lease['generation']='5'
        with self.assertRaises(Conflict):self.store.report(self.body('PostCompact','compact-turn','5'),principal=self.actor)
    def test_stop_and_mismatched_generation_never_use_compact_exception(self):
        with self.assertRaises(Conflict):self.store.report(self.body('Stop'),principal=self.actor)
        with self.assertRaises(Conflict):self.store.report(self.body('PreCompact',generation='3'),principal=self.actor)
    def test_trusted_receipt_raw_must_match_real_report_identity(self):
        body=self.body('PreCompact');self.callback['input']['turn_id']='spoof'
        with self.assertRaises(Conflict):self.store.report(body,principal=self.actor)

from tests.support import DbTestCase
from jasmine_core import SCHEMA_VERSION
from jasmine_core.adapter.config import AdapterConfig
from jasmine_core.auth import Principal
from jasmine_core.continuity import ContinuityStore
from jasmine_core.events import EventStore
from jasmine_core.models import NewObject,NewEvent
from jasmine_core.objects import ObjectStore
from jasmine_core.registry import Registry
from jasmine_core.state import StateStore
from jasmine_core.capture.p2_runtime import atomic_json
from jasmine_core.capture.p3_codex_hook import report_lifecycle
from jasmine_core.adapter.orchestrator import attest_completed_lifecycle
from tests.test_p3_codex_hook import MemoryLease
from jasmine_core.capture.p2_runtime import Deadline
from pathlib import Path
import json

class LifecycleStoreIntegration(DbTestCase):
    """Real isolated Core stores, registry and protected files; synthetic native notifications."""
    def test_independent_compact_report_checkpoint_post_and_native_attestation(self):
        root=Path(self._tmp.name).resolve();receipts=root/'receipts';work=root/'work';deployment=root/'deployment';project_dir=root/'project'
        for directory in (receipts,work,deployment,project_dir):directory.mkdir(mode=0o700)
        (project_dir/'.codex').mkdir();hooks=project_dir/'.codex/hooks.json';hooks.write_text('{"hooks":{}}\n')
        host=ids.new_id('hst');human=ids.new_id('act');system=ids.new_id('act');key=ids.new_id('key')
        registry=Registry(self.conn);registry.upsert_host(host)
        registry.upsert_actor(human,kind='human',home_host_id=host);registry.upsert_actor(system,kind='system',home_host_id=host)
        objects=ObjectStore(self.conn,schema_version=SCHEMA_VERSION)
        project=objects.create(NewObject.project({'name':'isolated lifecycle','host_id':host}),actor_id=human)['object']['project_id']
        task=objects.create(NewObject.task({'project_id':project,'title':'Task','host_id':host}),actor_id=human)['object']['task_id']
        step=StateStore(self.conn,schema_version=SCHEMA_VERSION).create_step(task,{'title':'Step','host_id':host,
            'acceptance_criteria':{'requirements':[]},'expected_revision':1},actor_id=human,actor_kind='human',can_accept=True)['step']['step_id']
        session=objects.create(NewObject.session({'project_id':project,'task_id':task,'host_id':host}),actor_id=human)['object']['session_id']
        source=EventStore(self.conn,schema_version=SCHEMA_VERSION).append(NewEvent(event_type='user.prompt',source_system='isolated-test',
            occurred_at=self.clock.now(),actor_id=human,actor_kind='human',host_id=host,project_id=project,task_id=task,
            payload={'text':'actual isolated source'}))[0]['event_id']
        state={'session_id':'thread','turn_id':'source-turn','generation':'1','task_id':task,'step_id':step,
            'host_id':host,'project_id':project,'core_session_id':session,'event_id':source,'phase':'READY'}
        leasepath=receipts/'lease.json';atomic_json(leasepath,state)
        value={'actor_id':system,'host_id':host,'key_id':key,'receipt_root':str(receipts),'work_root':str(work),
            'deployment_root':str(deployment),'lease_file':str(leasepath),'hook_definition_path':str(hooks),
            'hook_definition_sha256':hashlib.sha256(hooks.read_bytes()).hexdigest(),'deployment_sha256':'b'*64,'profile_sha256':'c'*64,
            'executors':{},'executor_inputs':{}}
        config=AdapterConfig(value,config_sha256='d'*64);configpath=receipts/'adapter.json';atomic_json(configpath,value)
        principal=Principal(system,key,frozenset({'adapter:report','adapter:attest'}))
        core=SimpleNamespace(conn=self.conn,schema_version=SCHEMA_VERSION,continuity=ContinuityStore(self.conn,schema_version=SCHEMA_VERSION))
        store=LifecycleStore(core,config=config)
        lease=MemoryLease(state)
        def write(current):lease.state=dict(current);atomic_json(leasepath,current)
        lease.write=write
        class Client:
            def post(inner,path,body,**kwargs):
                if path.endswith('/report'):return store.report(body,principal=principal)
                if path.endswith('/attest'):return store.attest(body,principal=principal)
                return core.continuity.create(body,actor_id=system)
        client=Client();binding={'p3_adapter_config_file':str(configpath),'host_id':host}
        with patch.dict('os.environ',{'JASMINE_CORE_WORKSPACE_ROOT':str(work)}):
            orphan={'hook_event_name':'PostCompact','session_id':'thread','turn_id':'compact'}
            with self.assertRaises(Conflict):report_lifecycle(orphan,binding,lease,client,Deadline())
            report_lifecycle({**orphan,'hook_event_name':'PreCompact'},binding,lease,client,Deadline())
            report_lifecycle(orphan,binding,lease,client,Deadline())
            self.assertEqual(lease.read()['turn_id'],'source-turn');self.assertEqual(lease.read()['phase'],'READY')
            with self.assertRaises(Conflict):report_lifecycle({**orphan,'turn_id':'wrong'},binding,lease,client,Deadline())
            run={'id':'hook-run','eventName':'preCompact','source':'project','handlerType':'command','sourcePath':str(hooks)}
            def n(method,**params):return {'method':method,'params':{'threadId':'thread','turnId':'compact',**params}}
            native=[n('item/started',item={'id':'item','type':'contextCompaction'}),n('hook/started',run=run),
                n('hook/completed',run={**run,'status':'completed'}),n('item/completed',item={'id':'item','type':'contextCompaction'})]
            self.assertEqual(attest_completed_lifecycle(client,config,native[:2],deadline=Deadline()),[])
            self.assertEqual(attest_completed_lifecycle(client,config,native,deadline=Deadline())[0]['attestation']['status'],'NATIVE_PROVED')
            events=EventStore(self.conn,schema_version=SCHEMA_VERSION)
            pre=events.get(lease.read()['last_precompact_report_id'])
            self.assertEqual(pre['payload']['result']['status'],'REPORT_ACCEPTED')
            self.assertEqual(pre['payload']['request']['native_turn_id'],'compact')
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='adapter.lifecycle_attested'").fetchone()[0],1)
            lease.write({**lease.read(),'generation':'2'})
            with self.assertRaises(Conflict):report_lifecycle(orphan,binding,lease,client,Deadline())

class PendingProofRegression(unittest.TestCase):
    def test_only_legal_unfinished_prefix_can_wait(self):
        from jasmine_core.adapter.lifecycle_proof import validate_native_lifecycle
        report={'native_thread_id':'thread','native_turn_id':'turn','hook_event_name':'PreCompact','hook_run_id':None,
            'hook_definition_sha256':'a'*64,'profile_sha256':'b'*64,'deployment_sha256':'c'*64}
        run={'id':'run','eventName':'preCompact','source':'project','handlerType':'command','sourcePath':'/hooks'}
        def n(method,**params):return {'method':method,'params':{'threadId':'thread','turnId':'turn',**params}}
        started=n('hook/started',run=run);completed=n('hook/completed',run={**run,'status':'completed'})
        istart=n('item/started',item={'id':'item','type':'contextCompaction'});iend=n('item/completed',item={'id':'item','type':'contextCompaction'})
        terminal=n('turn/completed',turn={'id':'turn','status':'completed'})
        receipt={k:report[k] for k in ('hook_definition_sha256','profile_sha256','deployment_sha256')}
        for rows in ([started,terminal],[started,n('turn/completed',turn={'id':'turn','status':'failed'})],
                     [started,iend],[started,istart,iend],[started,completed,iend],
                     [started,completed,istart,terminal]):
            with self.subTest(rows=rows),self.assertRaises(Conflict) as error:
                validate_native_lifecycle({**receipt,'notifications':rows},report,'/hooks',allow_pending=True)
            self.assertEqual(error.exception.code,'adapter_not_proved')
        for rows in ([],[started],[istart,started],[istart,started,completed]):
            with self.subTest(rows=rows),self.assertRaises(Conflict) as error:
                validate_native_lifecycle({**receipt,'notifications':rows},report,'/hooks',allow_pending=True)
            self.assertEqual(error.exception.code,'adapter_proof_pending')
        for rows in ([],[started]):
            with self.assertRaises(Conflict) as error:
                validate_native_lifecycle({**receipt,'profile_sha256':'other','notifications':rows},report,'/hooks',allow_pending=True)
            self.assertEqual(error.exception.code,'adapter_provenance_mismatch')
        self.assertEqual(validate_native_lifecycle({**receipt,'notifications':[istart,started,completed,iend]},report,'/hooks',allow_pending=True),'run')
