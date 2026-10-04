"""Trusted Memory worker settings: fixed private config, never parent inheritance."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jasmine_core import ids
from jasmine_core.context.memory_provider import MemoryProvider
from jasmine_core.continuity_scan import Budget
from jasmine_core.errors import InvalidRequest

class MemoryEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.work=self.root/'work';self.work.mkdir()
        self.database=self.root/'core.db';sqlite3.connect(self.database).close()
        self.actor=ids.new_id('act')
        self.script=self.root/'worker.py'
        self.script.write_text('''import json,os,sys
q=json.load(sys.stdin)
assert os.environ['JASMINE_CORE_DB']==q['expected_db']
assert os.environ['JASMINE_MEMORY_ACTOR_ID']==q['expected_actor']
assert os.environ['JASMINE_MEMORY_DEPLOYMENT_MODE']=='acl'
assert os.environ['JASMINE_HINDSIGHT_API_KEY']=='synthetic-private-key'
assert not any(k in os.environ for k in ('OPENAI_API_KEY','JASMINE_OLD_TOKEN','PYTHONHOME','PARENT_ONLY'))
print(json.dumps({'status':'empty','provider_id':os.environ['JASMINE_MEMORY_PROVIDER_ID'],'provider_config_digest':os.environ['JASMINE_MEMORY_CONFIG_DIGEST'],'banks':[{**b,'status':'empty','error_code':None} for b in q['banks']],'items':[],'query_trace_id':None,'error_code':None}))
''')
        self.environment={'JASMINE_CORE_DB':str(self.database),'JASMINE_MEMORY_ACTOR_ID':self.actor,
            'JASMINE_MEMORY_DEPLOYMENT_MODE':'acl','JASMINE_MEMORY_BANK_NAMESPACE':'native-run',
            'JASMINE_HINDSIGHT_URL':'http://127.0.0.1:1','JASMINE_HINDSIGHT_API_PREFIX':'/v1/default',
            'JASMINE_HINDSIGHT_TIMEOUT_SECONDS':'1','JASMINE_HINDSIGHT_API_KEY':'synthetic-private-key'}
    def provider(self,environment=None):
        return MemoryProvider(argv=[str(Path(sys.executable).resolve()),str(self.script)],
            provider_id='trusted',environment=self.environment if environment is None else environment)
    def query(self):
        project=ids.new_id('prj')
        return {'banks':[{'scope':'PROJECT','bank_id':'jasmine-project-'+project,'project_id':project}],
            'expected_db':str(self.database),'expected_actor':self.actor}
    def test_native_child_gets_fixed_settings_and_no_parent_secrets(self):
        provider=self.provider()
        class Connection:in_transaction=False
        with patch.dict(os.environ,{'OPENAI_API_KEY':'parent-private','JASMINE_OLD_TOKEN':'secret',
            'PARENT_ONLY':'unexpected','JASMINE_MEMORY_ACTOR_ID':ids.new_id('act')}):
            result=provider.recall(self.query(),Budget(),conn=Connection())
        self.assertEqual(result['status'],'empty')
        self.assertNotIn('synthetic-private-key',json.dumps(provider.configuration()))
    def test_disallowed_keys_and_non_string_values_rejected(self):
        for key in ('JASMINE_MEMORY_PROVIDER_ID','JASMINE_MEMORY_CONFIG_DIGEST','PYTHONHOME',
                    'PYTHONSTARTUP','LD_PRELOAD','DYLD_INSERT_LIBRARIES','OPENAI_API_KEY'):
            with self.subTest(key=key),self.assertRaises(ValueError):self.provider({**self.environment,key:'forged'})
        for value in (False,[],{'JASMINE_CORE_DB':False},{'JASMINE_CORE_DB':None}):
            with self.subTest(value=value),self.assertRaises(ValueError):self.provider(value)
    def test_authorization_path_and_engine_settings_validated(self):
        for key,value in [('JASMINE_CORE_DB','relative.db'),('JASMINE_MEMORY_ACTOR_ID','human'),
            ('PYTHONPATH','.'),('PYTHONPATH',''),
            ('JASMINE_MEMORY_DEPLOYMENT_MODE','typo'),('JASMINE_MEMORY_BANK_NAMESPACE','../../bank'),
            ('JASMINE_HINDSIGHT_URL','http://user:private@host'),
            ('JASMINE_HINDSIGHT_TIMEOUT_SECONDS','nan'),('JASMINE_HINDSIGHT_TIMEOUT_SECONDS','0'),
            ('JASMINE_HINDSIGHT_API_PREFIX','/../v1'),('JASMINE_HINDSIGHT_API_KEY','x\r\ny')]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.provider({**self.environment,key:value})
        alias=self.root/'alias.db';alias.symlink_to(self.database)
        with self.assertRaises(ValueError):self.provider({**self.environment,'JASMINE_CORE_DB':str(alias)})
        with self.assertRaises(ValueError):self.provider({**self.environment,'JASMINE_CORE_DB':str(self.root/'missing.db')})
    def test_settings_change_digest_without_exposing_values(self):
        original=self.provider().config_digest
        for key,value in [('JASMINE_MEMORY_ACTOR_ID',ids.new_id('act')),
            ('JASMINE_MEMORY_DEPLOYMENT_MODE','single_user'),('JASMINE_MEMORY_BANK_NAMESPACE','other'),
            ('JASMINE_HINDSIGHT_API_KEY','replacement-private-key')]:
            self.assertNotEqual(original,self.provider({**self.environment,key:value}).config_digest)
    def test_private_deployment_uses_same_validation_and_protects_database_path(self):
        config=self.root/'memory.json'
        value={'argv':[str(Path(sys.executable).resolve()),str(self.script)],'provider_id':'trusted',
            'global_bank':False,'environment':self.environment}
        def write():config.write_text(json.dumps(value));config.chmod(0o600)
        with patch.dict(os.environ,{'JASMINE_CORE_MEMORY_CONFIG':str(config),'JASMINE_CORE_WORKSPACE_ROOT':str(self.work)}):
            write();provider=MemoryProvider.from_deployment()
            self.assertEqual(provider.environment['JASMINE_MEMORY_ACTOR_ID'],self.actor)
            self.assertNotIn('synthetic-private-key',json.dumps(provider.configuration()))
            config.chmod(0o644)
            with self.assertRaises(InvalidRequest):MemoryProvider.from_deployment()
            database=self.work/'model.db';sqlite3.connect(database).close()
            value['environment']={**self.environment,'JASMINE_CORE_DB':str(database)};write()
            with self.assertRaises(InvalidRequest):MemoryProvider.from_deployment()
            value['environment']={**self.environment,'JASMINE_MEMORY_CONFIG_DIGEST':'spoof'};write()
            with self.assertRaises(InvalidRequest):MemoryProvider.from_deployment()
            value['environment']={**self.environment,'JASMINE_CORE_DB':str(self.root/'missing.db')};write()
            with self.assertRaises(InvalidRequest):MemoryProvider.from_deployment()
    def test_native_provider_starts_actual_p4_worker_with_explicit_settings(self):
        # P3's layout puts this file in the P3 checkout, whose P4 sibling is at
        # `<parent>/_worktrees/p4-memory`. On the integrated branch this same file
        # is *inside* the P4 checkout, so that path never resolves and the test
        # skipped for a reason that had nothing to do with the contract. Look in
        # both places, and let an operator name the checkout outright.
        override=os.environ.get('JASMINE_P4_CHECKOUT')
        candidates=[]
        if override:candidates.append(Path(override))
        here=Path(__file__).resolve()
        candidates.append(here.parents[2]/'_worktrees'/'p4-memory')
        candidates.append(here.parents[1])
        p4=next((path for path in candidates if (path/'scripts'/'p4-memory-worker.py').is_file()),None)
        if p4 is None:self.skipTest('optional isolated P4 checkout is unavailable')
        worker=p4/'scripts'/'p4-memory-worker.py'
        code='''import sqlite3,json,sys
from memory_support import MemoryTestCase,HUMAN
x=MemoryTestCase();x.setUp()
try:
 project=x.project();x.grant_project(project)
 target=sqlite3.connect(sys.argv[1]);x.conn.backup(target);target.close()
 print(json.dumps({'project':project,'actor':HUMAN}))
finally:x.doCleanups()
'''
        prepared=json.loads(subprocess.check_output([sys.executable,'-c',code,str(self.database)],
            cwd=p4,text=True,env={'PATH':os.defpath,'PYTHONPATH':str(p4/'src')+os.pathsep+str(p4/'tests'),
                                'PYTHONDONTWRITEBYTECODE':'1'}))
        env={**self.environment,'JASMINE_MEMORY_ACTOR_ID':prepared['actor']}
        provider=MemoryProvider(argv=[str(Path(sys.executable).resolve()),str(worker.resolve())],
            provider_id='actual-p4',global_bank=True,environment=env)
        project=prepared['project'];query={'project_id':project,'query_text':'history','max_tokens':100,
            'banks':[{'scope':'PROJECT','bank_id':'jasmine-project-'+project,'project_id':project},
                     {'scope':'GLOBAL','bank_id':'jasmine-global','project_id':None}]}
        class Connection:in_transaction=False
        result=provider.recall(query,Budget(),conn=Connection())
        self.assertEqual(result['status'],'degraded',result)
        self.assertEqual(result['error_code'],'engine_unreachable',result)
        self.assertEqual(result['provider_config_digest'],provider.config_digest)
        self.assertEqual(len(result['banks']),2)
