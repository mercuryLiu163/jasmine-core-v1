"""Admission falsifications for the browser record exit gate; no UI PASS claim."""
import copy
import unittest
from scripts.p6.five_browser_writes import REQUIRED, verdict

class RequiredRecordGate(unittest.TestCase):
    def setUp(self):
        self.rows=[{'id':name,'passed':True,'checks':[{'name':'actual chain assertion','passed':True}]} for name in REQUIRED]
    def test_complete(self):self.assertTrue(verdict(self.rows))
    def test_missing_each_required_row(self):
        for name in REQUIRED:
            with self.subTest(name=name):self.assertFalse(verdict([r for r in self.rows if r['id']!=name]))
    def test_false_each_required_row(self):
        for index in range(len(self.rows)):
            value=copy.deepcopy(self.rows);value[index]['passed']=False
            with self.subTest(index=index):self.assertFalse(verdict(value))
    def test_failed_child_assertion(self):
        self.rows[0]['checks'][0]['passed']=False;self.assertFalse(verdict(self.rows))
    def test_unknown_child_assertion(self):
        self.rows[0]['checks'][0]['passed']=None;self.assertFalse(verdict(self.rows))
    def test_empty_assertions(self):
        self.rows[0]['checks']=[];self.assertFalse(verdict(self.rows))
    def test_duplicate_replaces_required(self):
        self.rows[-1]=self.rows[0];self.assertFalse(verdict(self.rows))
    def test_unknown_replaces_required(self):
        self.rows[-1]['id']='unregistered';self.assertFalse(verdict(self.rows))
    def test_non_boolean_success(self):
        self.rows[0]['passed']=1;self.assertFalse(verdict(self.rows))
    def test_empty_bundle(self):self.assertFalse(verdict([]))
    def test_extra_record(self):self.assertFalse(verdict(self.rows+[self.rows[0]]))
    def test_unknown_bundle(self):self.assertFalse(verdict(None))

if __name__=='__main__':unittest.main()
