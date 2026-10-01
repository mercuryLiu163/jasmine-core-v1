import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from jasmine_core.adapter.orchestrator import DynamicExecutor,NativeRunner

class NativeFake:
    def __init__(self):self.messages=[]
    def send_request(self,method,params,budget):self.messages.append((method,params));return len(self.messages)
    def wait_response(self,ident,budget):
        if self.messages[ident-1][0]=='turn/start':return {'turn':{'id':'turn-real'}}
        return {'thread':{'id':'thread-real'}}
    def send_notification(self,method,params,budget):self.messages.append((method,params))

class OrchestrationTests(unittest.TestCase):
    def test_handshake_skill_and_compact_ack_are_distinct(self):
        native=NativeFake();runner=NativeRunner(native,None)
        runner.initialize(None)
        self.assertEqual([m[0] for m in native.messages],['initialize','initialized'])
        runner.start(None,cwd='/private/tmp/fixture')
        self.assertEqual(len(native.messages[-1][1]['dynamicTools']),4)
        self.assertTrue(all(t['type']=='function' for t in native.messages[-1][1]['dynamicTools']))
        runner.submit('继续',None,skill_path='/fixture/.agents/skills/jasmine-playwright/SKILL.md')
        self.assertEqual(native.messages[-1][1]['input'][0]['text'],'继续')
        self.assertEqual(native.messages[-1][1]['input'][1]['type'],'skill')
        self.assertEqual(runner.last_turn_id,'turn-real')
        runner.compact(None)
        self.assertEqual(native.messages[-1][0],'thread/compact/start')
        self.assertEqual(runner.notifications,[])

    def test_receipt_identity_safe_and_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).chmod(0o700)
            executor=DynamicExecutor(None,None,SimpleNamespace(value={'receipt_root':str(Path(tmp).resolve()),'executor_inputs':{}}),worker_inputs={},allowed_paths=[])
            with self.assertRaises(ValueError):executor.receipt('../escape',{})
            digest=executor.receipt('call',{'kind':'dynamic_call','request':{'tool':'jasmine_read'},'received_at_monotonic':1})
            self.assertEqual(digest,executor.receipt('call',{'kind':'dynamic_call','request':{'tool':'jasmine_read'},'received_at_monotonic':2}))
            with self.assertRaises(ValueError):executor.receipt('call',{'kind':'dynamic_call','request':{'tool':'jasmine_patch'},'received_at_monotonic':2})
