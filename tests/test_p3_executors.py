import hashlib
import os
import sys
import time
from unittest.mock import patch
import unittest
import tempfile
from pathlib import Path
from jasmine_core.adapter.executors import ExecutorFailure, safe_read, safe_patch, run_fixed_worker
from jasmine_core.continuity_scan import Budget


class ExecutorTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name).resolve()

 def test_paths_and_cas(self):
    tmp_path=self.root
    file=tmp_path/'app.txt'; file.write_text('old')
    allowed={'app.txt','link','fifo'}
    (tmp_path/'link').symlink_to(file)
    os.mkfifo(tmp_path/'fifo')
    for target in ('../app.txt','link','fifo'):
        with self.assertRaises((ExecutorFailure,OSError)): safe_read(tmp_path,target,Budget(),allowed)
    with self.assertRaisesRegex(ExecutorFailure,'hash_conflict'):
        safe_patch(tmp_path,'app.txt','0'*64,'new',Budget(),allowed)
    assert file.read_text()=='old'
    safe_patch(tmp_path,'app.txt',hashlib.sha256(b'old').hexdigest(),'new',Budget(),allowed)
    assert file.read_text()=='new'


 def test_hardlink_and_existing_lock_preserved(self):
    tmp_path=self.root
    file=tmp_path/'a'; file.write_text('old'); os.link(file,tmp_path/'b')
    with self.assertRaisesRegex(ExecutorFailure,'hardlink_write'):
        safe_patch(tmp_path,'a',hashlib.sha256(b'old').hexdigest(),'new',Budget(),{'a'})
    (tmp_path/'b').unlink(); lock=tmp_path/'.jasmine-patch-lock'; lock.write_text('owned elsewhere')
    with self.assertRaisesRegex(ExecutorFailure,'patch_busy'):
        safe_patch(tmp_path,'a',hashlib.sha256(b'old').hexdigest(),'new',Budget(),{'a'})
    assert lock.read_text()=='owned elsewhere'


 def test_owned_worker_timeout_and_output_cap(self):
    tmp_path=self.root
    script=tmp_path/'worker.py'
    script.write_text("import sys,time\nif sys.argv[1]=='wait': time.sleep(5)\nelse: print('x'*1100000)\n")
    manifest={'argv':[os.path.realpath(sys.executable),str(script)],'choices':['wait','large'], 'dependencies':[], 'environment':{}}
    start=time.monotonic()
    result=run_fixed_worker(manifest,'wait',{},Budget(12),.1)
    assert result['failure']=='executor_timeout' and result['cleanup']=='complete'
    assert time.monotonic()-start<2
    result=run_fixed_worker(manifest,'large',{},Budget(15),3)
    assert result['failure']=='output_too_large' and result['cleanup']=='complete'
    with self.assertRaisesRegex(ExecutorFailure,'invalid_manifest'):
        run_fixed_worker(manifest,'arbitrary',{},Budget(),1)

 def test_cleanup_denial_preserves_timeout(self):
    script=self.root/'short.py'; script.write_text("import time; time.sleep(.2)\n")
    manifest={'argv':[os.path.realpath(sys.executable),str(script)],'choices':['short'],'dependencies':[], 'environment':{}}
    with patch('jasmine_core.adapter.executors.os.killpg',side_effect=PermissionError('denied')):
        result=run_fixed_worker(manifest,'short',{},Budget(12),.03)
    self.assertEqual(result['failure'],'executor_timeout')
    self.assertEqual(result['cleanup'],'incomplete_or_denied')
    self.assertFalse(result['ownedIDs']['descendants_verified_gone'])

 def test_patch_unlink_denial_preserves_conflict(self):
    (self.root/'a').write_text('old')
    with patch('jasmine_core.adapter.executors.os.unlink',side_effect=PermissionError('denied')):
        with self.assertRaises(ExecutorFailure) as caught:
            safe_patch(self.root,'a','0'*64,'new',Budget(),{'a'})
    self.assertEqual(caught.exception.code,'hash_conflict')
    self.assertEqual(caught.exception.details['cleanup'],'incomplete_or_denied')
    self.assertEqual((self.root/'a').read_text(),'old')

 def test_patch_cleanup_timeout_primary(self):
    (self.root/'a').write_text('old')
    with patch('jasmine_core.adapter.executors._Target.read',side_effect=ExecutorFailure('executor_timeout')), patch('jasmine_core.adapter.executors.os.unlink',side_effect=PermissionError('denied')):
        with self.assertRaises(ExecutorFailure) as caught:
            safe_patch(self.root,'a',hashlib.sha256(b'old').hexdigest(),'new',Budget(),{'a'})
    self.assertEqual(caught.exception.code,'executor_timeout')
    self.assertEqual(caught.exception.details['cleanup'],'incomplete_or_denied')

 def test_canonical_dependency_directory_and_symlink_rejection(self):
    script=self.root/'worker.py';script.write_text("print('ok')\n")
    dependency=self.root/'dependency';dependency.mkdir();(dependency/'module.js').write_text('fixed')
    manifest={'argv':[os.path.realpath(sys.executable),str(script)],'choices':['fixed'],'dependencies':[str(dependency)],'environment':{}}
    self.assertEqual(run_fixed_worker(manifest,'fixed',{},Budget(),1)['exit_code'],0)
    link=self.root/'linked';link.symlink_to(dependency,target_is_directory=True)
    manifest['dependencies']=[str(link)]
    with self.assertRaisesRegex(ExecutorFailure,'unsafe_manifest_path'):run_fixed_worker(manifest,'fixed',{},Budget(),1)
