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
from unittest.mock import patch
from support import DbTestCase
from jasmine_core.interpreter_provider import CodexProvider, ProviderFailure, _base_config


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
