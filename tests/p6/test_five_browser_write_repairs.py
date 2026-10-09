"""Real owned-process failure paths and the predicates used by the JS success gate."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from scripts.p6.five_browser_writes import run_owned_browser

ROOT=Path(__file__).resolve().parents[2]

class BrowserOwnership(unittest.TestCase):
    def exercise(self,hang):
        with tempfile.TemporaryDirectory(prefix='five-owned-test-') as tmp:
            root=Path(tmp);out=root/'evidence';out.mkdir()
            child=root/'child.py'
            child.write_text("import json,sys,subprocess,time\nfrom pathlib import Path\ncfg=json.load(sys.stdin)\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nPath(cfg['out'],'owned-child.pid').write_text(str(p.pid))\nprint('partial-native-run-output',flush=True)\n"+('time.sleep(90)\n' if hang else 'sys.exit(0)\n'))
            cfg={'database':str(root/'core.db'),'out':str(out)}
            bystander=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],start_new_session=True)
            try:
                if hang:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        run_owned_browser([sys.executable,str(child)],cfg,out,os.environ.copy(),root,timeout=.5)
                else:
                    result=run_owned_browser([sys.executable,str(child)],cfg,out,os.environ.copy(),root,timeout=5)
                    self.assertEqual(result.returncode,0)
                receipt=json.loads((out/'browser-lifecycle.json').read_text())
                self.assertEqual(receipt['timed_out'],hang)
                self.assertTrue(receipt['cleanup_passed'],receipt)
                self.assertFalse(Path(receipt['profile_path']).exists())
                self.assertIn('partial-native-run-output',(out/'browser.stdout.log').read_text())
                owned=int((out/'owned-child.pid').read_text())
                with self.assertRaises(ProcessLookupError):os.kill(owned,0)
                self.assertIsNone(bystander.poll(),'unrelated process must not be killed')
            finally:
                bystander.terminate();bystander.wait(timeout=5)
    def test_timeout_reaps_owned_descendant_profile_and_retains_failure(self):self.exercise(True)
    def test_normal_node_exit_cannot_leave_its_child(self):self.exercise(False)

class ReviewLineage(unittest.TestCase):
    def setUp(self):
        self.value={'applications':[{'source_event_id':'evt_source','canonical_resolution_id':'res_same'}],
                    'sourceId':'evt_source','resolutionId':'res_same','reviewId':'rvw_same',
                    'resolution':{'resolution_id':'res_same','source_event_id':'evt_source'},
                    'rule':{'origin_event_id':'evt_command','scope':{'project_id':'prj_a','task_id':'tsk_a'}},
                    'command':{'event_id':'evt_command','event_type':'review.approved','source_event_id':None,
                               'project_id':'prj_a','task_id':'tsk_a',
                               'payload':{'source_event_id':'evt_source','review_id':'rvw_same'}}}
    def invoke(self,value):
        script="""import {sourceApplicationBound,reviewRuleOriginBound} from './scripts/p6/five_browser_write_lineage.mjs';
let data='';for await(const b of process.stdin)data+=b;const v=JSON.parse(data);
console.log(JSON.stringify({application:sourceApplicationBound(v.applications,v.sourceId,v.resolutionId,v.resolution),origin:reviewRuleOriginBound(v.rule,v.command,v.sourceId,v.reviewId,v.resolution,v.resolutionId)}));"""
        r=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(value),capture_output=True,text=True,cwd=ROOT,check=True)
        return json.loads(r.stdout)
    def test_actual_contract_shape_passes(self):self.assertEqual(self.invoke(self.value),{'application':True,'origin':True})
    def test_absent_duplicate_wrong_source_or_resolution_application_refused(self):
        for rows in [[],self.value['applications']*2,[{'source_event_id':'evt_other','canonical_resolution_id':'res_same'}],[{'source_event_id':'evt_source','canonical_resolution_id':'res_other'}]]:
            v=copy.deepcopy(self.value);v['applications']=rows
            with self.subTest(rows=rows):self.assertFalse(self.invoke(v)['application'])
    def test_rule_command_source_review_and_scope_must_match(self):
        changes=[('rule',('origin_event_id',),'evt_other'),('command',('event_id',),'evt_other'),
                 ('command',('event_type',),'review.rejected'),('command',('payload','source_event_id'),'evt_other'),
                 ('command',('payload','review_id'),'rvw_other'),('rule',('scope','task_id'),'tsk_other'),
                 ('rule',('scope','project_id'),'prj_other'),('command',('source_event_id',),'evt_other')]
        for obj,keys,value in changes:
            v=copy.deepcopy(self.value);dest=v[obj]
            for k in keys[:-1]:dest=dest[k]
            dest[keys[-1]]=value
            with self.subTest(obj=obj,keys=keys):self.assertFalse(self.invoke(v)['origin'])
    def test_resolution_receipt_and_source_must_match_both_predicates(self):
        for key,value in [('resolution_id','res_other'),('source_event_id','evt_other')]:
            v=copy.deepcopy(self.value);v['resolution'][key]=value
            with self.subTest(key=key):self.assertEqual(self.invoke(v),{'application':False,'origin':False})

if __name__=='__main__':unittest.main()
