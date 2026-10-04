"""Fixed owned executors. Filesystem checks sample an interval; no kernel cancellation claim."""
from __future__ import annotations
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import time
import uuid
from pathlib import Path

class ExecutorFailure(RuntimeError):
    def __init__(self, code, **details):
        super().__init__(code)
        self.code, self.details = code, details

def _remaining(budget, cap=5):
    try: return min(cap, budget.remaining())
    except Exception as exc: raise ExecutorFailure('executor_timeout') from exc

def validate_roots(*roots):
    paths = [Path(p).absolute() for p in roots]
    if any(p.resolve(strict=True) != p for p in paths): raise ExecutorFailure('unsafe_root')
    for i, p in enumerate(paths):
        for q in paths[i+1:]:
            if p == q or p in q.parents or q in p.parents: raise ExecutorFailure('overlapping_roots')
    return paths

def _parts(path, allowed):
    if not isinstance(path, str) or path not in allowed or not path or '\x00' in path:
        raise ExecutorFailure('path_not_allowed')
    parts = path.split('/')
    if any(p in ('', '.', '..') for p in parts) or path.startswith('/'):
        raise ExecutorFailure('unsafe_path')
    return parts

def _identity(st): return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

class _Target:
    def __init__(self, root, path, allowed):
        self.fds=[]; self.edges=[]
        parts=_parts(path, allowed)
        root=Path(root).absolute()
        try:
            # Walk the absolute ancestor chain, never follow symlinks at any segment.
            fd=os.open('/', os.O_RDONLY|os.O_DIRECTORY)
            self.fds.append(fd)
            for part in list(root.parts[1:])+parts[:-1]:
                child=os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
                self.edges.append((fd,part,os.fstat(child)))
                self.fds.append(child); fd=child
            self.parent=fd; self.name=parts[-1]
            self.fd=os.open(self.name, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK, dir_fd=fd)
            self.fds.append(self.fd); self.before=os.fstat(self.fd)
            if not stat.S_ISREG(self.before.st_mode): raise ExecutorFailure('not_regular')
            self.revalidate()
        except BaseException:
            self.close(); raise
    def revalidate(self):
        for fd,name,old in self.edges:
            new=os.stat(name,dir_fd=fd,follow_symlinks=False)
            if (new.st_dev,new.st_ino)!=(old.st_dev,old.st_ino) or not stat.S_ISDIR(new.st_mode):
                raise ExecutorFailure('path_changed')
        if _identity(os.stat(self.name,dir_fd=self.parent,follow_symlinks=False)) != _identity(self.before):
            raise ExecutorFailure('file_changed')
    def read(self, cap, budget):
        _remaining(budget)
        if self.before.st_size>cap: raise ExecutorFailure('file_too_large')
        os.lseek(self.fd,0,0); data=bytearray()
        while True:
            _remaining(budget)
            chunk=os.read(self.fd,min(65536,cap+1-len(data)))
            if not chunk: break
            data.extend(chunk)
            if len(data)>cap: raise ExecutorFailure('file_too_large')
        if _identity(os.fstat(self.fd))!=_identity(self.before): raise ExecutorFailure('file_changed')
        self.revalidate(); _remaining(budget)
        return bytes(data)
    def close(self):
        for fd in reversed(self.fds): os.close(fd)
        self.fds=[]

class _Slice:
    def __init__(self, parent, cap): self.end=min(parent.end-10,time.monotonic()+cap)
    def remaining(self):
        left=self.end-time.monotonic()
        if left<=0: raise ExecutorFailure("executor_timeout")
        return left

def safe_read(work_root, relative_path, budget, allowed_paths):
    budget=_Slice(budget,5)
    t=_Target(work_root,relative_path,allowed_paths)
    try:
        data=t.read(256*1024,budget)
        return {'content':data.decode('utf-8'),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    finally: t.close()

def safe_patch(work_root, relative_path, expected_sha256, new_content, budget, allowed_paths):
    budget=_Slice(budget,5)
    if not isinstance(new_content,str): raise ExecutorFailure('invalid_content')
    data=new_content.encode('utf-8')
    if len(data)>128*1024: raise ExecutorFailure('patch_too_large')
    t=_Target(work_root,relative_path,allowed_paths); temp=None; lock=None; primary=None; result=None
    try:
        if t.before.st_nlink!=1: raise ExecutorFailure('hardlink_write')
        lockname='.jasmine-patch-lock'
        try: lockfd=os.open(lockname,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=t.parent)
        except FileExistsError as exc: raise ExecutorFailure('patch_busy') from exc
        lock=lockname
        os.close(lockfd)
        old=t.read(256*1024,budget)
        if hashlib.sha256(old).hexdigest()!=expected_sha256: raise ExecutorFailure('hash_conflict')
        temp='.jasmine-patch-'+uuid.uuid4().hex
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,stat.S_IMODE(t.before.st_mode),dir_fd=t.parent)
        try:
            view=memoryview(data)
            while view:
                _remaining(budget); n=os.write(fd,view); view=view[n:]
            os.fsync(fd)
        finally: os.close(fd)
        # Re-read original through retained descriptor and verify namespace immediately before rename.
        if t.read(256*1024,budget)!=old: raise ExecutorFailure('file_changed')
        if os.fstat(t.fd).st_nlink!=1: raise ExecutorFailure('hardlink_write')
        t.revalidate(); _remaining(budget)
        os.rename(temp,t.name,src_dir_fd=t.parent,dst_dir_fd=t.parent); temp=None
        os.fsync(t.parent); _remaining(budget)
        result={'path':relative_path,'before_sha256':hashlib.sha256(old).hexdigest(),'after_sha256':hashlib.sha256(data).hexdigest(),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'sample_window_only':True,'cleanup':'complete'}
    except BaseException as exc:
        primary=exc
        raise
    finally:
        denied=False
        for name in (temp,lock):
            if name:
                try: os.unlink(name,dir_fd=t.parent)
                except FileNotFoundError: pass
                except OSError: denied=True
        try: t.close()
        except OSError: denied=True
        if denied:
            if isinstance(primary,ExecutorFailure): primary.details['cleanup']='incomplete_or_denied'
            elif primary is None: raise ExecutorFailure('cleanup_incomplete',cleanup='incomplete_or_denied')
    return result

def run_fixed_worker(manifest, choice, stdin_payload, budget, cap_seconds):
    """Only reviewed two-file argv, fixed enum and fresh owned group; no shell."""
    argv=manifest.get('argv'); choices=manifest.get('choices'); env=manifest.get('environment')
    if (set(manifest)!={'argv','choices','dependencies','environment'} or not isinstance(argv,list)
        or len(argv)!=2 or any(not isinstance(p,str) or not os.path.isabs(p) for p in argv)
        or not isinstance(choices,list) or choice not in choices or not isinstance(choice,str)
        or not isinstance(env,dict) or set(env)-{'PATH','LANG','LC_ALL','NODE_PATH'}
        or any(not isinstance(v,str) for v in env.values())
        or not isinstance(manifest['dependencies'],list) or len(manifest['dependencies'])>16):
        raise ExecutorFailure('invalid_manifest')
    for index,p in enumerate(argv+manifest['dependencies']):
        if not isinstance(p,str) or not os.path.isabs(p) or Path(p).resolve(strict=True)!=Path(p):
            raise ExecutorFailure('unsafe_manifest_path')
        mode=os.stat(p,follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode) and not (index>=2 and stat.S_ISDIR(mode)): raise ExecutorFailure('unsafe_manifest_path')
        # Core pins bounded directory membership/content before effect; do not
        # duplicate that tree scan here or reset the caller's absolute budget.
    payload=json.dumps(stdin_payload,ensure_ascii=False,separators=(',',':')).encode()
    if len(payload)>16384: raise ExecutorFailure('input_too_large')
    started=time.monotonic(); remaining=_remaining(budget,cap_seconds+10)-10
    if remaining<=0: raise ExecutorFailure('executor_timeout')
    deadline=min(budget.end-10,started+cap_seconds)
    try:
        proc=subprocess.Popen(argv+[choice],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              env=env,start_new_session=True)
    except OSError as exc:
        raise ExecutorFailure('executor_spawn_failed',cleanup='complete') from exc
    selector=selectors.DefaultSelector(); chunks={'output':bytearray(),'stderr':bytearray()}; failure=None; cleanup='complete'
    pending=memoryview(payload)
    try:
        for pipe,label,event in ((proc.stdin,'stdin',selectors.EVENT_WRITE),(proc.stdout,'output',selectors.EVENT_READ),(proc.stderr,'stderr',selectors.EVENT_READ)):
            os.set_blocking(pipe.fileno(),False); selector.register(pipe,event,label)
        while selector.get_map():
            left=min(deadline-time.monotonic(),_remaining(budget,cap_seconds))
            if left<=0: raise ExecutorFailure('executor_timeout')
            for key,_ in selector.select(left):
                if key.data=='stdin':
                    if pending:
                        try: pending=pending[os.write(key.fd,pending):]
                        except BrokenPipeError: pending=memoryview(b'')
                    if not pending: selector.unregister(key.fileobj); key.fileobj.close()
                else:
                    chunk=os.read(key.fd,65536)
                    if not chunk: selector.unregister(key.fileobj)
                    else:
                        chunks[key.data].extend(chunk)
                        if sum(map(len,chunks.values()))>1024*1024: raise ExecutorFailure('output_too_large')
        proc.wait(timeout=max(.001,deadline-time.monotonic()))
    except (ExecutorFailure,subprocess.TimeoutExpired,OSError) as exc:
        failure=exc.code if isinstance(exc,ExecutorFailure) else ('executor_timeout' if isinstance(exc,subprocess.TimeoutExpired) else 'executor_io_failed')
    finally:
        selector.close()
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        except OSError: cleanup='incomplete_or_denied'
        try: proc.wait(timeout=min(1,max(.001,budget.end-time.monotonic())))
        except (OSError,subprocess.TimeoutExpired): cleanup='incomplete_or_denied'
        for pipe in (proc.stdin,proc.stdout,proc.stderr):
            try: pipe.close()
            except OSError: cleanup='incomplete_or_denied'
    output=chunks['output'].decode('utf-8',errors='replace')
    result={'exit_code':proc.returncode,'output':output,'stderr':chunks['stderr'].decode('utf-8',errors='replace'),
            'started_at_monotonic':started,'finished_at_monotonic':time.monotonic(),'cleanup':cleanup,
            'ownedIDs':{'pid':proc.pid,'process_group_id':proc.pid,'descendants_verified_gone':False},
            'artifact_uri':None,'artifact_sha256':None,'failure':failure}
    # Artifact claims in stdout are only candidates; trusted runner verifies their bytes before Evidence.
    return result
