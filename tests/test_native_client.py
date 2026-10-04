import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from jasmine_core.adapter.native_client import NativeClient,NativeClientFailure
from jasmine_core.continuity_scan import Budget

class NativeClientTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def client(self,script,sink=None):
    p=self.root/'child.py';p.write_text(script)
    client=NativeClient([os.path.realpath(sys.executable),str(p)],{},str(self.root),sink)
    self.addCleanup(lambda:client.close(Budget(2)))
    return client
 def test_correlated_response_preserves_requests_and_notifications(self):
    receipts=[]
    c=self.client("""import sys,json
r=json.loads(sys.stdin.readline())
for m in [{'method':'note','params':{}},{'id':'server-1','method':'tool','params':{}},{'id':r['id'],'result':{'ok':True}}]: print(json.dumps(m),flush=True)
a=json.loads(sys.stdin.readline()); print(json.dumps({'method':'answered','params':a}),flush=True)
""",lambda direction,message:receipts.append((direction,message)))
    ident=c.send_request('init',{},Budget())
    self.assertEqual(c.wait_response(ident,Budget()),{'ok':True})
    self.assertEqual(c.next_message(Budget())['method'],'note')
    self.assertEqual(c.next_message(Budget())['method'],'tool')
    c.respond('server-1',{'ok':True},Budget())
    self.assertEqual(c.next_message(Budget())['params']['id'],'server-1')
    self.assertTrue(any(d=='outbound' for d,m in receipts))
    self.assertEqual(c.close(Budget())['cleanup'],'complete')
 def test_invalid_json_and_framing(self):
    for raw,code in [(b'{"jsonrpc":"2.0","method":"n","method":"b"}\n','native_invalid_message'),(b'{"jsonrpc":"2.0","method":"n","params":{"x":NaN}}\n','native_invalid_message'),(b'{"jsonrpc":"2.0","method":"n","params":{"x":"\\ud800"}}\n','native_invalid_message'),(b'{"jsonrpc":"2.0"}','native_partial_eof'),(b'x'*1048577+b'\n','native_message_too_large')]:
        with self.subTest(code=code):
            c=self.client('import sys\nsys.stdout.buffer.write('+repr(raw)+');sys.stdout.flush()\n')
            with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget(2))
            self.assertEqual(caught.exception.code,code);c.close(Budget())
 def test_timeout_and_cleanup_denial(self):
    c=self.client('import time;time.sleep(.2)\n')
    with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget(.02))
    self.assertEqual(caught.exception.code,'native_timeout')
    with patch('jasmine_core.adapter.native_client.os.killpg',side_effect=PermissionError('denied')):
        result=c.close(Budget(1))
    self.assertEqual(result['cleanup'],'incomplete_or_denied')
 def test_sink_failure_blocks_send(self):
    c=self.client('import sys;sys.stdin.readline()\n',lambda *args:(_ for _ in ()).throw(OSError('sink failed')))
    with self.assertRaises(NativeClientFailure) as caught:c.send_request('init',{},Budget())
    self.assertEqual(caught.exception.code,'native_receipt_failed')
    with self.assertRaises(NativeClientFailure):c.send_request('again',{},Budget())
 def test_unknown_response(self):
    c=self.client("print('{\"jsonrpc\":\"2.0\",\"id\":999,\"result\":{}}',flush=True)\n")
    with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget())
    self.assertEqual(caught.exception.code,'native_response_correlation')

 def test_buffered_second_line_cap(self):
    from jasmine_core.adapter.native_client import LIMIT
    c=self.client('import time;time.sleep(.2)\n')
    c.buffer.extend(b'{"method":"short"}\n'+b'x'*(LIMIT+1)+b'\n')
    self.assertEqual(c.next_message(Budget())['method'],'short')
    with self.assertRaises(NativeClientFailure) as caught:c.next_message(Budget())
    self.assertEqual(caught.exception.code,'native_message_too_large')

 def test_next_message_response_remains_waitable(self):
    c=self.client("import sys,json\nr=json.loads(sys.stdin.readline());print(json.dumps({'id':r['id'],'result':42}),flush=True)\n")
    ident=c.send_request('init',{},Budget())
    self.assertEqual(c.next_message(Budget())['result'],42)
    self.assertEqual(c.wait_response(ident,Budget()),42)

 def test_notification_has_no_request_id(self):
    c=self.client("import sys,json\nn=json.loads(sys.stdin.readline());print(json.dumps({'method':'observed','params':n}),flush=True)\n")
    c.send_notification('initialized',{},Budget())
    observed=c.next_message(Budget())['params']
    self.assertEqual(observed,{'method':'initialized','params':{}})
    self.assertEqual(c.outstanding,set())

 def test_pending_receive_time_precedes_slow_sink_and_queue(self):
    def sink(direction,message):
      if direction=='inbound' and message.get('method')=='tool':time.sleep(.04)
    c=self.client("import json,sys\nr=json.loads(sys.stdin.readline());print(json.dumps({'id':'server-1','method':'tool','params':{}}),flush=True);print(json.dumps({'id':r['id'],'result':{}}),flush=True);sys.stdin.readline()\n",sink)
    ident=c.send_request('init',{},Budget())
    c.wait_response(ident,Budget());time.sleep(.03)
    message=c.next_message(Budget());stamp=c.pop_received_at(message)
    self.assertGreaterEqual(time.monotonic()-stamp,.06)
    with self.assertRaises(NativeClientFailure):c.pop_received_at(message)

 def test_actual_notification_timestamp_and_unknown_fields(self):
    actual={'method':'configWarning','params':{'summary':'isolated fixture warning'},'emittedAtMs':1790822400000}
    c=self.client('import json;print(json.dumps('+repr(actual)+'),flush=True)\n')
    message=c.next_message(Budget());self.assertEqual(message,actual)
    self.assertLessEqual(c.pop_received_at(message),time.monotonic())
    for extra in ({'emittedAtMs':True},{'emittedAtMs':-1},{'emittedAtMs':1.5},{'unexpected':1}):
      c=self.client('import json;print(json.dumps('+repr({**actual,**extra})+'),flush=True)\n')
      with self.assertRaisesRegex(NativeClientFailure,'native_invalid_message'):c.next_message(Budget())
