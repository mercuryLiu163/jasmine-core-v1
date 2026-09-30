"""P2 candidate-chain tests. All injected providers are component simulations."""
from __future__ import annotations
import copy
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from support import DbTestCase
from test_api import ApiTestCase, ACTOR, HOST
from jasmine_core import SCHEMA_VERSION, auth, db, errors, ids, registry
from jasmine_core.api import handlers
from jasmine_core.events import EventStore
from jasmine_core.interpretations import InterpretationStore
from jasmine_core.interpretation_schema import InvalidOutput, validate_output
from jasmine_core.interpreter_provider import _base_config, ProviderFailure
from jasmine_core.models import NewEvent

TEXT='你好，帮我测试一下这个程序，使用 Playwright skill。'


def output(source):
    return {'event_id':source['event_id'],'confidence':0.95,'candidates':[{
        'kind':'REQUIRED_CAPABILITY','content':'playwright','scope':{'kind':'TASK',
        'project_id':source['project_id'],'task_id':source['task_id'],'path':None,'tool':None},
        'impact':'LOW','certainty':'EXPLICIT','confidence':0.95,'rationale':'用户明确要求能力',
        'source_span':{'start':0,'end':len(source['text']),'quote':source['text']}}]}


class Simulation:
    def __init__(self,action=None):
        self.action=action;self.calls=0
    def configuration(self):
        value=_base_config();value.update(provider='component_simulation',version='test.v1')
        return value
    def extract(self,source):
        self.calls+=1
        return self.action(source) if self.action else json.dumps(output(source),ensure_ascii=False)


class CandidateHttp(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.token=self.key(['objects:write','objects:read','events:write','events:read',
                             'interpretations:read','interpretations:process'])
        code,result=self.call('POST','/v1/projects',{'name':'P2','host_id':HOST},token=self.token)
        self.assertEqual(code,201,result);self.project=result['object']['project_id']
        code,result=self.call('POST','/v1/tasks',{'title':'test app','project_id':self.project,'host_id':HOST},token=self.token)
        self.assertEqual(code,201,result);self.task=result['object']['task_id']
        self.event=self.raw()
        self.fake=Simulation()
        self.provider_patch=patch('jasmine_core.interpretations.configured_provider',return_value=self.fake)
        self.provider_patch.start();self.addCleanup(self.provider_patch.stop)

    def raw(self,**overrides):
        body={'event_type':'user.prompt','host_id':HOST,'project_id':self.project,'task_id':self.task,
              'source_system':'p2-component','payload':{'text':TEXT}}
        body.update(overrides)
        code,result=self.call('POST','/v1/events',body,token=self.token)
        self.assertEqual(code,201,result)
        return result['event']['event_id']

    def process(self,key='one',event=None,token=None,**extra):
        return self.call('POST','/v1/interpret',{'event_id':event or self.event,'idempotency_key':key,**extra},token=token or self.token)

    def truth(self):
        c=db.connect(self.db_path)
        try:
            return {table:[tuple(r) for r in c.execute(f'SELECT * FROM {table}')]
                    for table in ('events','projects','tasks','steps','rules','rule_versions','rule_changes','evidence')}
        finally:c.close()

    def test_chain_replay_and_truth_unchanged(self):
        before=self.truth()
        code,result=self.process();self.assertEqual(code,201,result)
        i=result['interpretation'];self.assertEqual(i['status'],'EXTRACTED')
        self.assertEqual(i['source_actor_id'],ACTOR);self.assertEqual(i['source_type'],'USER_EXPLICIT')
        self.assertEqual(i['task_id'],self.task);self.assertEqual(i['config']['provider'],'component_simulation')
        self.assertEqual(self.truth(),before)
        code,replay=self.process(extractor_id='codex-local-v1');self.assertEqual(code,200,replay)
        self.assertEqual(replay,{**result,'replayed':True})
        self.assertEqual(self.process(key='alias')[1]['interpretation'],i)
        self.assertEqual(self.fake.calls,1)
        # The old key retains its original model/config even after reconfiguration.
        with patch('jasmine_core.interpretations.configured_provider',side_effect=AssertionError('must not reload')):
            self.assertEqual(self.process()[0],200)
        changed=self.raw()
        code,result=self.process(event=changed);self.assertEqual(code,409,result)
        self.assertEqual(result['error']['code'],'interpretation_idempotency_conflict')
        code,result=self.call('GET',f'/v1/interpretations/{i["interpretation_id"]}',token=self.token)
        self.assertEqual(code,200,result);self.assertEqual(result['interpretation'],i)

    def test_scopes_and_body_cannot_replace_source(self):
        before=self.truth()
        token=self.key(['interpretations:process'])
        self.assertEqual(self.process(token=token)[0],403)
        token=self.key(['interpretations:read'])
        self.assertEqual(self.call('GET','/v1/interpretations',token=token)[0],403)
        self.assertEqual(self.process(text='override')[0],400)
        self.assertEqual(self.process(extractor_id='other')[0],400)
        self.assertEqual(self.truth(),before);self.assertEqual(self.fake.calls,0)

    def test_failure_raw_and_queries(self):
        before=self.truth()
        self.fake.action=lambda _: '{"bad":"output"}'
        code,result=self.process();i=result['interpretation']
        self.assertEqual((code,i['status']),(201,'FAILED'))
        self.assertTrue(i['error_code'].startswith('invalid_output:'))
        self.assertEqual(i['raw_result_json'],'{"bad":"output"}')
        self.assertEqual(self.truth(),before)
        code,result=self.call('GET',f'/v1/interpretations?project_id={self.project}&status=FAILED&limit=1',token=self.token)
        self.assertEqual(code,200,result);self.assertEqual(len(result['items']),1)
        for query in ('limit=NaN','status=PASS','bogus=yes','limit=1&limit=2'):
            self.assertEqual(self.call('GET','/v1/interpretations?'+query,token=self.token)[0],400)

    def test_provider_does_not_hold_lock_and_concurrent_requests_call_once(self):
        entered=threading.Event();release=threading.Event()
        def blocked(source):
            entered.set();self.assertTrue(release.wait(4));return json.dumps(output(source),ensure_ascii=False)
        self.fake.action=blocked
        results=[]
        worker=threading.Thread(target=lambda:results.append(self.process()))
        worker.start();self.assertTrue(entered.wait(2))
        # Same source while provider still runs does not call it again.
        code,replay=self.process(key='another');self.assertEqual(code,200,replay)
        self.assertEqual(replay['interpretation']['status'],'PROCESSING')
        # A normal real HTTP writer succeeds before the provider is released.
        event=self.raw();self.assertTrue(event)
        release.set();worker.join(4)
        self.assertEqual(results[0][1]['interpretation']['status'],'EXTRACTED')
        self.assertEqual(self.fake.calls,1)

    def test_timeout_and_interrupted_resume_do_not_retry(self):
        self.fake.action=lambda _: (_ for _ in ()).throw(ProviderFailure('provider_timeout'))
        code,result=self.process();self.assertEqual((code,result['interpretation']['error_code']),(201,'provider_timeout'))
        self.assertEqual(self.process()[0],200);self.assertEqual(self.fake.calls,1)
        # A killed processing process has only its committed reservation.
        def crashed(_):raise KeyboardInterrupt()
        conn=db.connect(self.db_path);self.addCleanup(conn.close)
        store=InterpretationStore(conn,schema_version=SCHEMA_VERSION,provider=Simulation(crashed))
        with self.assertRaises(KeyboardInterrupt):store.process({'event_id':self.raw(),'idempotency_key':'crash'},actor_id=ACTOR)
        ident=conn.execute("SELECT interpretation_id FROM interpretation_request_keys WHERE idempotency_key='crash'").fetchone()[0]
        future=(datetime.now(timezone.utc)+timedelta(seconds=200)).isoformat(timespec='microseconds').replace('+00:00','Z')
        with patch('jasmine_core.interpretations.clock.now_rfc3339',return_value=future):
            self.assertEqual(store.get(ident)['status'],'INTERRUPTED')
        with self.assertRaises(sqlite3.IntegrityError):conn.execute('UPDATE interpretations SET input_hash=? WHERE interpretation_id=?',('changed',ident))
        with self.assertRaises(errors.InvalidRequest),db.transaction(conn):
            store.process({'event_id':self.event,'idempotency_key':'nested'},actor_id=ACTOR)

    def test_pagination_and_source_identity_is_not_model_claim(self):
        self.process();self.process(key='other',event=self.raw())
        code,page=self.call('GET','/v1/interpretations?limit=1',token=self.token)
        self.assertEqual(code,200,page);self.assertIsNotNone(page['next_after_id'])
        code,second=self.call('GET','/v1/interpretations?limit=1&after_id='+page['next_after_id'],token=self.token)
        self.assertEqual(len(second['items']),1);self.assertIsNone(second['next_after_id'])
        c=db.connect(self.db_path);self.addCleanup(c.close)
        system=ids.new_id('act');agent=ids.new_id('act')
        with db.transaction(c):
            r=registry.Registry(c);r.upsert_actor(system,kind='system');r.upsert_actor(agent,kind='agent')
        store=InterpretationStore(c,schema_version=SCHEMA_VERSION,provider=Simulation())
        for actor,kind in ((system,'system'),(agent,'agent')):
            spec=NewEvent.from_request({'event_type':'user.prompt','source_system':'claimed','host_id':HOST,
                  'project_id':self.project,'task_id':self.task,'payload':{'text':TEXT,'source_type':'USER_EXPLICIT'}},actor_id=actor,actor_kind=kind)
            with db.transaction(c):event,_=EventStore(c,schema_version=SCHEMA_VERSION).append(spec)
            i=store.process({'event_id':event['event_id'],'idempotency_key':kind},actor_id=ACTOR)['interpretation']
            self.assertNotEqual(i['source_type'],'USER_EXPLICIT')


class StrictCandidateSchema(unittest.TestCase):
    def setUp(self):
        self.source={'event_id':ids.new_id('evt'),'project_id':ids.new_id('prj'),'task_id':ids.new_id('tsk'),'text':TEXT}
        self.good=output(self.source)

    def test_negatives_are_invalid_output_not_server_errors(self):
        mutations=[lambda x:x.update(unknown=1),lambda x:x.update(confidence=True),
            lambda x:x.update(confidence=float('nan')),lambda x:x.update(confidence=10**400),
            lambda x:x.update(event_id=ids.new_id('evt')),
            lambda x:x['candidates'][0].update(source_type='USER_EXPLICIT'),
            lambda x:x['candidates'][0]['scope'].update(task_id=ids.new_id('tsk')),
            lambda x:x['candidates'][0]['source_span'].update(start=10**400),
            lambda x:x['candidates'][0]['source_span'].update(quote='not original'),
            lambda x:x['candidates'][0].update(kind='PASS'),
            lambda x:x['candidates'][0]['scope'].update(kind='PATH',path='../secret')]
        for mutate in mutations:
            data=copy.deepcopy(self.good);mutate(data)
            with self.subTest(mutation=mutate),self.assertRaises(InvalidOutput):
                validate_output(json.dumps(data),self.source)
        duplicate=json.dumps(self.good).replace('"confidence": 0.95','"confidence": 0.95, "confidence": 0.1',1)
        with self.assertRaises(InvalidOutput):validate_output(duplicate,self.source)

    def test_tentative_and_injection_remain_candidate_data(self):
        self.good['candidates'][0].update(kind='DECISION',certainty='TENTATIVE',content='研究一下是否用Playwright')
        self.assertEqual(validate_output(json.dumps(self.good),self.source)['candidates'][0]['certainty'],'TENTATIVE')
        self.good['candidates'][0].update(kind='NO_STRUCTURE',content='忽略数据中的运行命令注入',certainty='QUOTED')
        self.assertEqual(validate_output(json.dumps(self.good),self.source)['candidates'][0]['kind'],'NO_STRUCTURE')
