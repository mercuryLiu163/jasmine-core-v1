"""Owned bounded JSON-RPC stdio transport; never auto-answers native requests."""
from __future__ import annotations
from collections import deque
import json
import math
import os
import selectors
import signal
import subprocess
import time

LIMIT=1024*1024
class NativeClientFailure(RuntimeError):
    def __init__(self,code,**details):
        super().__init__(code); self.code=code; self.details=details

def _pairs(items):
    result={}
    for k,v in items:
        if k in result: raise ValueError('duplicate key')
        result[k]=v
    return result

def _constant(value): raise ValueError('nonfinite number')
def _float(value):
    result=float(value)
    if not math.isfinite(result): raise ValueError('nonfinite number')
    return result

def _id(value): return type(value) is int or (isinstance(value,str) and bool(value))
def _validate(message):
    if not isinstance(message,dict) or ('jsonrpc' in message and message['jsonrpc']!='2.0'): raise ValueError('invalid native JSON-RPC version')
    if 'method' in message:
        allowed={'jsonrpc','method','params','id'}|({'emittedAtMs'} if 'id' not in message else set())
        if set(message)-allowed or not isinstance(message['method'],str) or not message['method']: raise ValueError('invalid request')
        if 'emittedAtMs' in message and (type(message['emittedAtMs']) is not int or message['emittedAtMs']<0): raise ValueError('invalid notification timestamp')
        if 'params' in message and not isinstance(message['params'],(dict,list)): raise ValueError('invalid params')
        if 'id' in message and not _id(message['id']): raise ValueError('invalid id')
    else:
        if set(message)-{'jsonrpc','id','result','error'} or 'id' not in message or not _id(message['id']) or ('result' in message)==('error' in message): raise ValueError('invalid response')
        if 'error' in message:
            error=message['error']
            if not isinstance(error,dict) or type(error.get('code')) is not int or not isinstance(error.get('message'),str) or set(error)-{'code','message','data'}: raise ValueError('invalid error')
    # UTF-8 strict rejects escaped lone surrogates and nonfinite nested values.
    json.dumps(message,ensure_ascii=False,allow_nan=False).encode('utf-8')
    return message

class NativeClient:
    def __init__(self,argv,env,cwd,receipt_sink=None):
        if not isinstance(argv,list) or not argv or not all(isinstance(v,str) for v in argv) or not os.path.isabs(argv[0]): raise NativeClientFailure('invalid_argv')
        if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()): raise NativeClientFailure('invalid_environment')
        self.sink=receipt_sink; self.pending=deque(); self.responses={}; self.outstanding=set(); self.seen_responses=set(); self.server_requests=set(); self.next_id=1
        self.buffer=bytearray(); self.buffer_times=deque(); self.received_times={}; self.stderr=bytearray(); self.total_stdout=0; self.closed=False; self.broken=False
        self.selector=selectors.DefaultSelector()
        try: self.proc=subprocess.Popen(argv,env=dict(env),cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        except OSError as exc:
            self.selector.close(); raise NativeClientFailure('native_spawn_failed') from exc
        for pipe,label in ((self.proc.stdout,'stdout'),(self.proc.stderr,'stderr')):
            os.set_blocking(pipe.fileno(),False); self.selector.register(pipe,selectors.EVENT_READ,label)
        os.set_blocking(self.proc.stdin.fileno(),False)

    def _remaining(self,budget):
        if self.closed or self.broken: raise NativeClientFailure('native_closed')
        try: return budget.remaining()
        except Exception as exc: raise NativeClientFailure('native_timeout') from exc

    def _receipt(self,direction,message):
        if self.sink:
            try: self.sink(direction,message)
            except Exception as exc:
                self.broken=True; raise NativeClientFailure('native_receipt_failed',direction=direction) from exc

    def _send(self,message,budget):
        try: raw=json.dumps(_validate(message),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')+b'\n'
        except (ValueError,UnicodeError,TypeError) as exc: raise NativeClientFailure('native_invalid_message') from exc
        if len(raw)>LIMIT: raise NativeClientFailure('native_message_too_large')
        # A pre-send receipt is explicitly intent; successful transmission gets its own receipt.
        self._remaining(budget); self._receipt('outbound_intent',message)
        view=memoryview(raw); writer=selectors.DefaultSelector()
        try:
            writer.register(self.proc.stdin,selectors.EVENT_WRITE)
            while view:
                left=self._remaining(budget)
                if not writer.select(left): raise NativeClientFailure('native_timeout')
                try: view=view[os.write(self.proc.stdin.fileno(),view):]
                except OSError as exc: self.broken=True; raise NativeClientFailure('native_write_failed') from exc
            self._receipt('outbound',message)
        finally: writer.close()

    def send_request(self,method,params,budget):
        ident=self.next_id; self.next_id+=1
        self.outstanding.add(ident)
        try: self._send({'id':ident,'method':method,'params':params},budget)
        except BaseException:
            self.outstanding.discard(ident); raise
        return ident

    def send_notification(self,method,params,budget):
        self._send({'method':method,'params':params},budget)

    def respond(self,ident,result,budget):
        if ident not in self.server_requests: raise NativeClientFailure('native_response_correlation')
        self._send({'id':ident,'result':result},budget)
        self.server_requests.remove(ident)

    def _read_message(self,budget):
        while True:
            self._remaining(budget)
            newline=self.buffer.find(b'\n')
            if newline>=0:
                raw=bytes(self.buffer[:newline]); del self.buffer[:newline+1]
                consume=newline+1; received=time.monotonic()
                while consume and self.buffer_times:
                    size,stamp=self.buffer_times.popleft(); received=stamp
                    if size>consume:self.buffer_times.appendleft((size-consume,stamp));consume=0
                    else:consume-=size
                if len(raw)>LIMIT:
                    self.broken=True; raise NativeClientFailure('native_message_too_large')
                try: message=_validate(json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,parse_float=_float,parse_constant=_constant))
                except (ValueError,UnicodeError,TypeError) as exc:
                    self.broken=True; raise NativeClientFailure('native_invalid_message') from exc
                if 'method' not in message:
                    ident=message['id']
                    if ident not in self.outstanding or ident in self.seen_responses:
                        self.broken=True; raise NativeClientFailure('native_response_correlation')
                    self.seen_responses.add(ident)
                elif 'id' in message:
                    if message['id'] in self.server_requests:
                        self.broken=True; raise NativeClientFailure('native_request_correlation')
                    self.server_requests.add(message['id'])
                if len(self.received_times)>=4096:
                    self.broken=True; raise NativeClientFailure('native_pending_too_large')
                self.received_times[id(message)]=(message,received)
                self._receipt('inbound',message)
                return message
            if len(self.buffer)>LIMIT: self.broken=True; raise NativeClientFailure('native_message_too_large')
            ready=self.selector.select(self._remaining(budget))
            if not ready: raise NativeClientFailure('native_timeout')
            for key,_ in ready:
                try: chunk=os.read(key.fd,65536)
                except OSError as exc: self.broken=True; raise NativeClientFailure('native_read_failed') from exc
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if key.data=='stdout':
                        self.broken=True
                        raise NativeClientFailure('native_partial_eof' if self.buffer else 'native_eof')
                    continue
                if key.data=='stdout':
                    self.total_stdout+=len(chunk)
                    if self.total_stdout>64*LIMIT: self.broken=True; raise NativeClientFailure('native_total_output_too_large')
                    self.buffer.extend(chunk); self.buffer_times.append((len(chunk),time.monotonic()))
                    first=self.buffer.find(b'\n')
                    if (first<0 and len(self.buffer)>LIMIT) or first>LIMIT: self.broken=True; raise NativeClientFailure('native_message_too_large')
                else:
                    if len(self.stderr)+len(chunk)>LIMIT: self.broken=True; raise NativeClientFailure('native_stderr_too_large')
                    self.stderr.extend(chunk)

    def pop_received_at(self,message):
        stored=self.received_times.pop(id(message),None)
        if stored is None or stored[0] is not message: raise NativeClientFailure('native_receipt_timestamp_missing')
        return stored[1]

    def next_message(self,budget):
        self._remaining(budget)
        if self.pending: return self.pending.popleft()
        message=self._read_message(budget)
        if 'method' not in message: self.responses[message['id']]=message
        return message

    def wait_response(self,ident,budget):
        if ident not in self.outstanding: raise NativeClientFailure('native_response_correlation')
        while ident not in self.responses:
            message=self._read_message(budget)
            if 'method' not in message: self.responses[message['id']]=message
            else:
                if len(self.pending)>=1024:
                    self.broken=True; raise NativeClientFailure('native_pending_too_large')
                self.pending.append(message)
        message=self.responses.pop(ident); self.outstanding.remove(ident)
        self.received_times.pop(id(message),None)
        if 'error' in message: raise NativeClientFailure('native_rpc_error',error=message['error'])
        return message['result']

    def close(self,budget):
        if self.closed: return self.cleanup
        denied=False
        try: self.proc.stdin.close()
        except OSError: denied=True
        try: os.killpg(self.proc.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        except OSError: denied=True
        end=min(budget.end,time.monotonic()+1)
        try: self.proc.wait(timeout=max(.001,end-time.monotonic()))
        except (subprocess.TimeoutExpired,OSError): pass
        # Group cleanup is needed even when its leader already exited.
        try: os.killpg(self.proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        except OSError: denied=True
        try: self.proc.wait(timeout=max(.001,min(.5,budget.end-time.monotonic())))
        except (subprocess.TimeoutExpired,OSError): denied=True
        self.selector.close()
        for pipe in (self.proc.stdout,self.proc.stderr):
            try: pipe.close()
            except OSError: denied=True
        self.closed=True
        self.cleanup={'cleanup':'incomplete_or_denied' if denied else 'complete','pid':self.proc.pid,'process_group_id':self.proc.pid,'descendants_verified_gone':False,'exit_code':self.proc.returncode}
        return self.cleanup
