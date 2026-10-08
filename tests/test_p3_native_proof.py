import unittest
from jasmine_core.adapter.native_proof import typed_input,skill_input
from jasmine_core.adapter.orchestrator import NativeRunner
from jasmine_core.capture.p2_runtime import Deadline
from tests.test_p3_orchestrator import NativeFake

class NativeProofTests(unittest.TestCase):
    def test_exact_current_skill_wrapper_and_human_quote(self):
        body='---\nname: jasmine-playwright\n---\nReviewed skill.'
        path='/reviewed/SKILL.md';name='jasmine-playwright'
        wrapper=f'<skill>\n<name>{name}</name>\n<path>{path}</path>\n{body}\n</skill>'
        request={'input':[{'type':'text','text':'继续'},{'type':'skill','name':name,'path':path}]}
        rows=[{'type':'turn_context','payload':{'turn_id':'current'}},{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':wrapper}]}}]
        self.assertTrue(skill_input(rows,'current',body,native_request=request,skill_name=name,skill_path=path))
        request['input'][0]['text']=wrapper
        self.assertFalse(skill_input(rows,'current',body,native_request=request,skill_name=name,skill_path=path))
        self.assertFalse(typed_input(rows,'current',wrapper))
        self.assertFalse(skill_input(rows,'old',body,native_request=request,skill_name=name,skill_path=path))
    def test_turn_cap_and_deadline(self):
        native=NativeFake();runner=NativeRunner(native,None);runner.start(None,cwd='/fixture')
        overall=Deadline(1800)
        for _ in range(16):runner.submit('继续',overall)
        self.assertLessEqual(runner.turn_deadline.remaining(),140)
        with self.assertRaises(ValueError):runner.submit('继续',overall)
