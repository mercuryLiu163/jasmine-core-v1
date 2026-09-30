"""P2 admission components, explicitly not actual Codex hook acceptance."""
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from jasmine_core import ids
from jasmine_core.capture.p2_runtime import Deadline, DeadlineClient, Lease, atomic_json
from jasmine_core.capture.p2_snapshot import coherent_snapshot
from jasmine_core.capture.p2_codex_hook import handle

class AdmissionComponents(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.binding = self.root / 'binding.json'
        self.config = {'mode': 2, 'run_nonce': 'a' * 40, 'project_id': ids.new_id('prj'),
            'host_id': ids.new_id('hst'), 'core_url': 'http://127.0.0.1:1',
            'token_file': str(self.root / 'processor'), 'human_token_file': str(self.root / 'capture'),
            'trace_file': str(self.root / 'trace')}
        atomic_json(self.binding, self.config)
        for name in ('capture', 'processor'):
            atomic_json(self.root / name, 'component-token')
        self.env = patch.dict(os.environ, {'JASMINE_CORE_GATE_NONCE': self.config['run_nonce']})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def payload(self, turn='turn1'):
        return {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session', 'turn_id': turn, 'prompt': 'Use Playwright'}
    def test_pending_durable_before_any_network_and_error_blocks(self):
        observations = []
        def fail(client, method, path, body=None, **kwargs):
            state = Lease(self.binding, Deadline()).read()
            observations.append((state['phase'], state['turn_id'], path))
            raise OSError('network')
        with patch.object(DeadlineClient, 'request', fail):
            result = handle(self.payload(), self.binding)
        self.assertEqual(result['decision'], 'block')
        self.assertEqual(observations, [('PENDING_CAPTURE', 'turn1', '/v1/events')])
        state = Lease(self.binding, Deadline()).read()
        self.assertEqual(state['requests']['raw']['body']['payload']['text'], 'Use Playwright')
    def test_new_turn_invalidates_ready_before_network(self):
        lease = Lease(self.binding, Deadline())
        lease.write({'phase': 'READY', 'session_id': 'session', 'turn_id': 'old', 'generation': 1,
            'task_id': ids.new_id('tsk'), 'step_id': ids.new_id('stp')})
        def fail(client, method, path, body=None, **kwargs):
            self.assertEqual(lease.read()['phase'], 'PENDING_CAPTURE')
            self.assertEqual(lease.read()['turn_id'], 'new')
            raise OSError()
        with patch.object(DeadlineClient, 'request', fail):
            self.assertEqual(handle(self.payload('new'), self.binding)['decision'], 'block')
    def test_response_loss_retry_keeps_exact_raw_request(self):
        bodies = []
        def fail(client, method, path, body=None, **kwargs):
            bodies.append(copy.deepcopy(body)); raise OSError()
        with patch.object(DeadlineClient, 'request', fail):
            handle(self.payload(), self.binding); handle(self.payload(), self.binding)
        self.assertEqual(bodies[0], bodies[1])
    def test_fifo_lock_does_not_block(self):
        lease = Lease(self.binding, Deadline(.1))
        os.mkfifo(lease.lock, 0o600)
        start = time.monotonic()
        with self.assertRaises((OSError, ValueError)):
            with lease.locked(): pass
        self.assertLess(time.monotonic() - start, .2)
    def test_expired_budget_never_writes_ready(self):
        lease = Lease(self.binding, Deadline(end=time.monotonic()-1))
        with self.assertRaises(TimeoutError): lease.write({'phase': 'READY'})
        self.assertFalse(lease.path.exists())
    def test_pretool_denies_even_ready_and_post_not_evidence(self):
        for event in ('PreToolUse', 'PostToolUse'):
            result = handle({**self.payload(), 'hook_event_name': event}, self.binding)
            if event == 'PreToolUse': self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
            else: self.assertEqual(result, {})
    def snapshot_fixture(self):
        project, task, step, rule = [ids.new_id(prefix) for prefix in ('prj', 'tsk', 'stp', 'rul')]
        members = [{'object_type': k, 'object_id': i, 'revision': 1} for k,i in [('project',project),('task',task),('step',step),('rule',rule)]]
        preview = {'expected_revisions': members, 'expected_context_digest': 'a'*64, 'maintenance_head': {'revision':1}}
        records = {'/v1/projects/'+project: {'project': {'project_id':project,'revision':1}},
            '/v1/tasks/'+task: {'task': {'task_id':task,'project_id':project,'revision':1,'status':'ACTIVE'}},
            '/v1/steps/'+step: {'step': {'step_id':step,'task_id':task,'revision':1,'status':'PLANNED'}},
            '/v1/rules/'+rule: {'rule': {'rule_id':rule,'revision':1,'version':1,'status':'RETIRED','content':'must not inject'}}}
        return project,task,step,preview,records
    def test_snapshot_record_revision_not_only_digest_and_inactive_content_not_injected(self):
        project,task,step,preview,records = self.snapshot_fixture()
        class Client:
            def get(self, path): return copy.deepcopy(preview if 'preview?' in path else records[path])
        text,value = coherent_snapshot(Client(),'int',task,step,{'project_id':project})
        self.assertNotIn('must not inject', text)
        self.assertEqual(value['active_rules'], [])
        records['/v1/tasks/'+task]['task']['revision']=2
        with self.assertRaisesRegex(ValueError,'context_conflict'):
            coherent_snapshot(Client(),'int',task,step,{'project_id':project})
    def test_snapshot_membership_race_blocks(self):
        project,task,step,preview,records = self.snapshot_fixture()
        class Client:
            count=0
            def get(self,path):
                if 'preview?' in path:
                    self.count+=1
                    result=copy.deepcopy(preview)
                    if self.count%2==0: result['expected_revisions']=result['expected_revisions'][:-1]
                    return result
                return copy.deepcopy(records[path])
        with self.assertRaisesRegex(ValueError,'context_conflict'):
            coherent_snapshot(Client(),'int',task,step,{'project_id':project})

class AdmissionBusinessGraph(unittest.TestCase):
    """Actual Core stores with a simulated provider and in-process API adapter."""
    def setUp(self):
        AdmissionComponents.setUp(self)
        from test_resolutions import Resolutions
        self.fixture = Resolutions('test_task_null_atomic_source_graph_and_replay')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config.update(project_id=self.fixture.project, host_id=self.fixture.host)
        atomic_json(self.binding, self.config)
        fixture = self.fixture
        class Adapter:
            def __init__(self, url, token, deadline): self.deadline=deadline
            def get(self,path): return self.request('GET',path)
            def request(self,method,path,body=None,**kwargs):
                from jasmine_core import db, SCHEMA_VERSION
                from jasmine_core.models import NewEvent
                if method=='POST':
                    if path=='/v1/events':
                        with db.transaction(fixture.conn):
                            event,replay=fixture.resolver.events.append(NewEvent.from_request(body,actor_id=fixture.human,actor_kind='human'))
                        return {'event':event,'replayed':replay}
                    if path=='/v1/interpret': return fixture.interpretations.process(body,actor_id=fixture.system)
                    if path.startswith('/v1/resolve/'): return fixture.resolver.resolve(path.split('/')[-1],body,actor_id=fixture.system)
                    if path.endswith('/steps'): return fixture.resolver.state.create_step(path.split('/')[-2],body,actor_id=fixture.system,actor_kind='system',can_accept=False)
                if 'preview?' in path:return fixture.resolver.preview(path.split('=')[-1])
                kind, ident = path.split('/')[2:4]
                if kind=='interpretations': return {'interpretation':fixture.resolver.maintenance.get(ident)}
                if kind=='events': return {'event':fixture.resolver.events.get(ident)}
                if kind=='resolutions': return {'resolution':fixture.resolver.get(ident)}
                if kind=='reviews': return {'review':fixture.reviews.get(ident)}
                if kind=='steps': return {'step':fixture.resolver.state.step(ident)}
                if kind=='rules': return {'rule':fixture.resolver.authority.get(ident)}
                if kind=='tasks': return {'task':fixture.resolver.state.task(ident)}
                if kind=='projects': return {'project':fixture.objects.get('project',ident)}
                raise AssertionError(path)
        self.adapter=patch('jasmine_core.capture.p2_codex_hook.DeadlineClient',Adapter)
        self.adapter.start();self.addCleanup(self.adapter.stop)
    payload = AdmissionComponents.payload
    tearDown = AdmissionComponents.tearDown
    def test_real_store_graph_task_null_step_and_exact_retry(self):
        result=handle(self.payload(),self.binding)
        self.assertIn('additionalContext',result.get('hookSpecificOutput',{}), result)
        state=Lease(self.binding,Deadline()).read()
        self.assertEqual(state['phase'],'READY')
        self.assertIsNone(self.fixture.resolver.events.get(state['event_id'])['task_id'])
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM steps').fetchone()[0],1)
        self.assertIn('additionalContext',handle(self.payload(),self.binding)['hookSpecificOutput'])
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM steps').fetchone()[0],1)
    def test_pending_rejected_releases_new_source_without_truth(self):
        from test_interpretations import output
        self.fixture.fake.action=lambda source: json.dumps({**output(source),'candidates':[{**output(source)['candidates'][0],'certainty':'TENTATIVE'}]})
        first=handle(self.payload(),self.binding)
        self.assertEqual(first['decision'],'block')
        state=Lease(self.binding,Deadline()).read()
        self.fixture.resolver.maintenance.change(state['interpretation_id'],'reject',{'idempotency_key':'op-reject','host_id':self.fixture.host,'reason':'fixture reject','expected_revision':1},actor_id=self.fixture.system)
        second=handle(self.payload('second'),self.binding)
        current=Lease(self.binding,Deadline()).read()
        self.assertEqual(second['decision'],'block')
        self.assertIsNone(current['previous_ref'])
        self.assertNotEqual(current['event_id'],state['event_id'])
        self.assertIsNotNone(self.fixture.resolver.events.get(current['event_id']))
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def _terminal_child_recovery(self, action):
        from test_interpretations import output
        original = self.fixture.fake.action
        self.fixture.fake.action=lambda source: json.dumps({**output(source),'candidates':[{**output(source)['candidates'][0],'certainty':'TENTATIVE'}]})
        self.assertEqual(handle(self.payload(),self.binding)['decision'],'block')
        pending=Lease(self.binding,Deadline()).read()
        self.fixture.fake.action=original
        request={'idempotency_key':'operator-'+action,'host_id':self.fixture.host,'reason':'explicit fixture action','expected_revision':1}
        if action=='correct':
            request['result']=json.loads(original(self.fixture.interpretations._source(pending['event_id'])))
        self.fixture.resolver.maintenance.change(pending['interpretation_id'],action,request,actor_id=self.fixture.system)
        second=handle(self.payload('second'),self.binding)
        state=Lease(self.binding,Deadline()).read()
        self.assertEqual(second['decision'],'block')
        self.assertEqual(state['phase'],'CAPTURED_UNBOUND_BLOCKED')
        self.assertIsNone(self.fixture.resolver.events.get(state['event_id'])['task_id'])
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        third=handle(self.payload('third'),self.binding)
        self.assertIn('additionalContext',third.get('hookSpecificOutput',{}),third)
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        current=Lease(self.binding,Deadline()).read()
        self.assertEqual(self.fixture.resolver.events.get(current['event_id'])['task_id'],current['task_id'])
    def test_correct_child_recovery_preserves_current_raw_null_next_realturn_binds(self):
        self._terminal_child_recovery('correct')
    def test_explicit_rerun_recovery_never_reruns_llm_itself(self):
        self._terminal_child_recovery('rerun')

    def test_review_approved_task_null_binding_uses_actual_operator_provenance(self):
        original=self.fixture.fake.action
        def tentative(source):
            value=json.loads(original(source))
            for candidate in value['candidates']:candidate['certainty']='TENTATIVE'
            return json.dumps(value)
        self.fixture.fake.action=tentative
        self.assertEqual(handle(self.payload(),self.binding)['decision'],'block')
        state=Lease(self.binding,Deadline()).read()
        review=self.fixture.reviews.get(state['review_id']);preview=review['preview']
        request={'idempotency_key':'approve-task','host_id':self.fixture.host,'reason':'explicit isolated operator',
            'expected_revision':1,'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest'],
            'actions':[{'candidate_index':0,'action':'CREATE_TASK','payload':{}},{'candidate_index':1,'action':'CREATE_RULE','payload':{}}]}
        self.fixture.reviews.change(state['review_id'],'approve',request,actor_id=self.fixture.human,scopes=self.fixture.scopes)
        result=handle(self.payload(),self.binding)
        self.assertIn('additionalContext',result.get('hookSpecificOutput',{}),result)
        ready=Lease(self.binding,Deadline()).read()
        bound=self.fixture.resolver.events.get(ready['binding_event_id'])
        self.assertEqual(bound['actor_id'],self.fixture.human)
        self.assertEqual(bound['actor_kind'],'human')

class AdmissionArtifacts(unittest.TestCase):
    @staticmethod
    def script(name):
        import importlib.util
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location(name,root/'scripts'/name)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module
    def test_injection_requires_current_typed_nonassistant_input(self):
        module=self.script('p2-real-conversation.py')
        record={'type':'response_item','payload':{'type':'message','role':'assistant','turn_id':'current','content':[{'type':'input_text','text':'exact-context'}]}}
        self.assertFalse(module.injection_in_current_turn([record],'current','exact-context'))
        record['payload']['role']='user';record['payload']['turn_id']='historical'
        self.assertFalse(module.injection_in_current_turn([record],'current','exact-context'))
        record['payload']['turn_id']='current'
        self.assertTrue(module.injection_in_current_turn([record],'current','exact-context'))
    def test_installer_precise_command_does_not_remove_echo_or_foreign_path(self):
        module=self.script('p2-codex-hook-install.py')
        wrapper=Path('/trusted/scripts/jasmine-p2-hook.sh');binding=Path('/private/binding.json')
        self.assertFalse(module.owned({'type':'command','command':'echo jasmine-p1-hook.sh'},wrapper,binding))
        self.assertFalse(module.owned({'type':'command','command':'/other/jasmine-p2-hook.sh --binding /private/binding.json'},wrapper,binding))
        self.assertTrue(module.owned({'type':'command','command':str(wrapper)+' --python /python --binding '+str(binding)},wrapper,binding))
        with self.assertRaises(ValueError):module.owned({'type':'command','command':str(wrapper)+' --python /python --binding /another'},wrapper,binding)
    def test_authenticated_api_rejects_external_and_url_credentials(self):
        for url in ('https://example.test','http://localhost:8787','http://user@127.0.0.1:8787','http://127.0.0.1:8787/other'):
            with self.assertRaises(ValueError):DeadlineClient(url,'fixture',Deadline())
    def test_profile_rejects_overlap_and_mutated_freeze(self):
        from jasmine_core.capture import p2_profile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();fixture=root/'project';private=root/'private';code=root/'code'
            for path in (fixture,private,code):path.mkdir()
            (fixture/'work').mkdir()
            binary=code/'codex';binary.write_bytes(b'component binary')
            catalog=private/'catalog.json';catalog.write_bytes(b'{}')
            class Provider:
                valid=True
                def __init__(self,*args):pass
                def configuration(self):return {'executable_path':str(binary),'catalog_path':str(catalog),'executable_sha256':__import__('hashlib').sha256(binary.read_bytes()).hexdigest(),'catalog_sha256':__import__('hashlib').sha256(catalog.read_bytes()).hexdigest()}
            with patch.object(p2_profile,'CodexProvider',Provider),patch.object(p2_profile,'user_surface',return_value={'mcp_server_names':['test-server'],'global_hook_sha256':'dummy','legacy_sandbox_mode_present':True}):
                config=p2_profile.configuration(binary,catalog,fixture,private,code)
                args=p2_profile.argv(config)
                self.assertNotIn('--ignore-user-config',args)
                self.assertIn('notify=[]',args)
                self.assertTrue(any(value.startswith('mcp_servers=') and '"enabled"=false' in value and '/usr/bin/false' in value for value in args))
                changed=copy.deepcopy(config);changed['disabled_features']=[]
                with self.assertRaises(ValueError):p2_profile.argv(changed)
                with self.assertRaises(ValueError):p2_profile.configuration(binary,catalog,fixture,private,fixture/'work')
