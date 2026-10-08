"""Component subprocess tests: exercise bounds and cancellation, never claim LLM."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from support import DbTestCase
from jasmine_core.interpreter_provider import CodexProvider, ProviderFailure, _base_config, _cancel_owned_process


class ProviderSubprocess(unittest.TestCase):
    def run_script(self,script,*,timeout=3,source=None):
        with tempfile.TemporaryDirectory(prefix='jasmine-provider-component-') as temp:
            path=Path(temp)/'component.py';path.write_text(script)
            p=CodexProvider.__new__(CodexProvider)
            p.valid=True;p.executable=path;p.catalog_bytes=b'{"models":[]}'
            p.config=_base_config();p.config.update(timeout_seconds=timeout,
                executable_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            p._argv=lambda root:[sys.executable,str(path),str(root/'result.json')]
            return p.extract(source or {'text':'source data'})

    def test_bounded_native_success_and_environment_whitelist(self):
        script='''import json,os,sys
assert 'OPENAI_API_KEY' not in os.environ
assert 'OPENAI_BASE_URL' not in os.environ
assert 'JASMINE_CORE_GATE_NONCE' not in os.environ
assert 'HTTPS_PROXY' not in os.environ
sys.stdin.read()
raw='{"only":"component simulation"}'
open(sys.argv[1],'w').write(raw)
print(json.dumps({'type':'thread.started'}))
print(json.dumps({'type':'turn.started'}))
print(json.dumps({'type':'item.completed','item':{'id':'reason','type':'reasoning','text':'data-only reasoning'}}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':raw}}))
print(json.dumps({'type':'turn.completed'}))
'''
        with patch.dict(os.environ,{'OPENAI_API_KEY':'secret','OPENAI_BASE_URL':'bad','JASMINE_CORE_GATE_NONCE':'nonce','HTTPS_PROXY':'bad'}):
            self.assertEqual(json.loads(self.run_script(script)),{'only':'component simulation'})

    def test_process_timeout_is_finite(self):
        start=time.monotonic()
        with self.assertRaises(ProviderFailure) as raised:
            self.run_script('import time; time.sleep(20)',timeout=.15)
        self.assertEqual(raised.exception.code,'provider_timeout')
        self.assertLess(time.monotonic()-start,2)

    def test_output_cap_and_tool_or_incomplete_result_rejected(self):
        with self.assertRaises(ProviderFailure) as raised:
            self.run_script('import sys; sys.stdout.write("x"*300000);sys.stdout.flush()')
        self.assertEqual(raised.exception.code,'provider_output_too_large')
        for native,code in (({'type':'item.completed','item':{'type':'command_execution'}},'provider_tool_use'),
                            ({'type':'thread.started'},'provider_protocol_error')):
            script=f'import sys;open(sys.argv[1],"w").write("{{}}");print({json.dumps(json.dumps(native))})'
            with self.assertRaises(ProviderFailure) as raised:self.run_script(script)
            self.assertEqual(raised.exception.code,code)

    def test_unvalidated_binary_fails_closed(self):
        p=CodexProvider(sys.executable,'/nonexistent/catalog.json')
        self.assertFalse(p.valid)
        with self.assertRaises(ProviderFailure) as raised:p.extract({'text':'do not execute'})
        self.assertEqual(raised.exception.code,'provider_unavailable')

    def test_exited_parent_with_child_holding_pipes_is_cancelled(self):
        with tempfile.TemporaryDirectory(prefix="jasmine-child-receipt-") as temp:
            marker=Path(temp)/"still-running"
            child="import time,pathlib,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(.5);pathlib.Path("+repr(str(marker))+").write_text('leaked')"
            script="import subprocess,sys;subprocess.Popen([sys.executable,'-c',"+repr(child)+"])"
            with self.assertRaises(ProviderFailure) as raised:
                self.run_script(script,timeout=.15)
            self.assertEqual(raised.exception.code,"provider_timeout")
            time.sleep(.55)
            self.assertFalse(marker.exists(),"descendant survived process-group cancellation")

    def test_catalog_requires_explicit_null_tool_mode(self):
        with tempfile.TemporaryDirectory(prefix="jasmine-catalog-component-") as temp:
            path=Path(temp)/"catalog.json"
            binary=Path(sys.executable)
            fingerprint=hashlib.sha256(binary.read_bytes()).hexdigest()
            entry={"slug":"gpt-6.1-sol","apply_patch_tool_type":None,
                   "experimental_supported_tools":[],"supports_search_tool":False}
            with patch.object(CodexProvider,"SUPPORTED_EXECUTABLE_SHA256",fingerprint):
                for mode in ("missing","code_mode_only",None):
                    current=dict(entry)
                    if mode!="missing":current["tool_mode"]=mode
                    path.write_text(json.dumps({"models":[current]}))
                    provider=CodexProvider(str(binary),str(path))
                    self.assertEqual(provider.valid,mode is None)

    def test_validated_version_is_selected_by_exact_bytes(self):
        # Component files stand in for approved bytes; no real CLI compatibility
        # is established by this patched allowlist test.
        with tempfile.TemporaryDirectory(prefix='jasmine-version-component-') as temp:
            root=Path(temp);binary=root/'component';catalog=root/'catalog.json'
            binary.write_bytes(b'component-only newer binary')
            fingerprint=hashlib.sha256(binary.read_bytes()).hexdigest()
            catalog.write_text(json.dumps({'models':[{'slug':'gpt-6.1-sol',
                'apply_patch_tool_type':None,'experimental_supported_tools':[],
                'supports_search_tool':False,'tool_mode':None}]}))
            with patch.object(CodexProvider,'ADDITIONAL_VALIDATED_EXECUTABLES',
                              {'codex-cli/component-only':fingerprint}):
                provider=CodexProvider(str(binary),str(catalog))
                self.assertTrue(provider.valid)
                self.assertEqual(provider.configuration()['version'],'codex-cli/component-only')
                self.assertEqual(provider.configuration()['executable_sha256'],fingerprint)
                binary.write_bytes(b'unsupported replacement')
                with self.assertRaises(ProviderFailure) as raised:
                    provider.extract({'text':'source data'})
                self.assertEqual(raised.exception.code,'provider_configuration_changed')
                self.assertFalse(CodexProvider(str(binary),str(catalog)).valid)

    def test_original_validated_fingerprint_is_preserved(self):
        self.assertEqual(CodexProvider.validated_executables()['codex-cli/0.159.0'],
            'e89718aa1969bfc4a471277bdc4679a3a3529293de0a309909822dfd67ddb77a')

    def test_group_eperm_exited_parent_preserves_output_cap(self):
        spawn=subprocess.Popen
        owned=[]
        def exited(*args,**kwargs):
            proc=spawn(*args,**kwargs);proc.wait(timeout=2);owned.append(proc)
            proc.terminate=Mock(side_effect=AssertionError('exited parent must not be signalled'))
            proc.kill=Mock(side_effect=AssertionError('exited parent must not be signalled'))
            return proc
        with patch('jasmine_core.interpreter_provider.subprocess.Popen',side_effect=exited), patch('jasmine_core.interpreter_provider.os.killpg',side_effect=PermissionError('EPERM')):
            with self.assertRaises(ProviderFailure) as raised:
                self.run_script('import sys;open(sys.argv[1],"w").write("x"*70000)')
        self.assertEqual(raised.exception.code,'provider_output_too_large')
        self.assertEqual(raised.exception.cleanup_errors,('cleanup_permission_denied',))
        self.assertIsNotNone(owned[0].poll())
        self.assertTrue(all(stream.closed for stream in (owned[0].stdin,owned[0].stdout,owned[0].stderr)))

    def test_group_eperm_live_parent_uses_only_owned_parent_fallback(self):
        spawn=subprocess.Popen;owned=[]
        def live(*args,**kwargs):
            proc=spawn(*args,**kwargs);owned.append(proc);return proc
        start=time.monotonic()
        with patch('jasmine_core.interpreter_provider.subprocess.Popen',side_effect=live), patch('jasmine_core.interpreter_provider.os.killpg',side_effect=PermissionError('EPERM')):
            with self.assertRaises(ProviderFailure) as raised:
                self.run_script('import time;time.sleep(20)',timeout=.1)
        self.assertEqual(raised.exception.code,'provider_timeout')
        self.assertIn('cleanup_permission_denied',raised.exception.cleanup_errors)
        self.assertLess(time.monotonic()-start,2)
        self.assertIsNotNone(owned[0].poll())
        self.assertTrue(all(stream.closed for stream in (owned[0].stdin,owned[0].stdout,owned[0].stderr)))

    def test_cleanup_denied_and_incomplete_wait_remain_bounded_diagnostics(self):
        proc=Mock();proc.pid=12345;proc.poll.return_value=None
        proc.terminate.side_effect=PermissionError('EPERM');proc.kill.side_effect=PermissionError('EPERM')
        proc.wait.side_effect=subprocess.TimeoutExpired('owned component',1)
        with patch('jasmine_core.interpreter_provider.os.killpg',side_effect=PermissionError('EPERM')):
            diagnostics=_cancel_owned_process(proc)
        self.assertEqual(diagnostics,('cleanup_permission_denied','cleanup_incomplete'))
        self.assertEqual(proc.wait.call_count,2)
        self.assertTrue(all(call.kwargs=={'timeout':1} for call in proc.wait.call_args_list))
        proc.terminate.assert_called_once();proc.kill.assert_called_once()
