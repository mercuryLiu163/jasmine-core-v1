import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from jasmine_core.adapter.orchestrator import DynamicExecutor,NativeRunner,attest_completed_lifecycle
from jasmine_core.capture.p2_runtime import Deadline,atomic_json

class NativeFake:
    def __init__(self):self.messages=[]
    def send_request(self,method,params,budget):self.messages.append((method,params));return len(self.messages)
    def wait_response(self,ident,budget):
        if self.messages[ident-1][0]=='turn/start':return {'turn':{'id':'turn-real'}}
        return {'thread':{'id':'thread-real'}}
    def send_notification(self,method,params,budget):self.messages.append((method,params))

class OrchestrationTests(unittest.TestCase):
    def test_lifecycle_attestation_keeps_only_identified_current_turn_and_replays_bytes(self):
        # Collector component regression: no native lifecycle/Gate claim.
        for event in ('Stop','PreCompact'):
            with self.subTest(event=event),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp).resolve();root.chmod(0o700)
                callback={'report':{'hook_event_name':event,'native_thread_id':'thread',
                                    'native_turn_id':'current'}}
                config=SimpleNamespace(value={'receipt_root':str(root),'hook_definition_sha256':'hook',
                    'profile_sha256':'profile','deployment_sha256':'deployment'},
                    receipt=lambda ident:(callback,'callback-sha'))
                atomic_json(root/'callback_one_checkpoint.json',{'kind':'lifecycle_checkpoint',
                    'callback_id':'callback_one','reported_event_id':'reported','checkpoint_id':'checkpoint'})
                posts=[]
                client=SimpleNamespace(post=lambda endpoint,body,cap:posts.append((endpoint,body)))
                def native(method,**params):
                    return {'method':method,'params':{'threadId':'thread',**params}}
                current=[native('hook/started',turnId='current',run={'id':'hook-run'}),
                         native('hook/completed',turnId='current',run={'id':'hook-run','status':'completed'})]
                if event=='Stop':
                    current.append(native('turn/completed',turn={'id':'current','status':'completed'}))
                else:
                    current.extend(native(method,turnId='current',item={'id':'compact','type':'contextCompaction'})
                                   for method in ('item/started','item/completed'))
                attest_completed_lifecycle(client,config,current,deadline=Deadline())
                receipt=root/'attest_callback_one.json';before=receipt.read_bytes()
                ignored=[native('turn/completed',turn={'id':'later','status':'completed'}),
                    native('hook/completed',turnId='later'),native('item/completed'),
                    native('turn/completed',turnId='current',turn={'id':'later'}),
                    native('turn/completed',turnId='later',turn={'id':'current'}),
                    native('turn/completed',turnId='',turn={'id':'current'}),
                    {'method':'hook/completed','params':None},
                    {'method':'turn/completed','params':{'threadId':'foreign','turn':{'id':'current'}}}]
                attest_completed_lifecycle(client,config,current+ignored,deadline=Deadline())
                self.assertEqual(receipt.read_bytes(),before)
                self.assertEqual(json.loads(before)['notifications'],current)
                self.assertEqual(posts[0],posts[1])
                self.assertEqual(posts[0][1]['receipt_sha256'],hashlib.sha256(before).hexdigest())

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
