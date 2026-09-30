"""Private admission durability and bounded host-side API calls.

File modes are hygiene, not the main Codex credential boundary. That boundary
requires the separately verified named permission profile.
"""
from __future__ import annotations
import fcntl
import json
import os
import re
import subprocess
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from ..api.client import ApiError
from ..canonical import canonical_json
from .p1_codex_hook import _private_file

MAX_RESPONSE = 2 * 1024 * 1024

class Deadline:
    def __init__(self, seconds=140, *, end=None):
        self.end = time.monotonic() + seconds if end is None else end
    def remaining(self, cap=None):
        value = self.end - time.monotonic()
        if value <= 0:
            raise TimeoutError('admission_deadline')
        return min(value, cap) if cap is not None else value

class DeadlineClient:
    """A process deadline also bounds slow headers and repeated socket reads.

    Tokens travel only over child stdin. Killing our owned HTTP child on expiry
    cannot cancel a server-side commit, so callers persist exact requests first.
    """
    def __init__(self, base_url, token, deadline):
        if not isinstance(base_url, str) or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', base_url):
            raise ValueError('authenticated API requires fixed loopback URL')
        self.base_url, self.token, self.deadline = base_url, token, deadline
    def request(self, method, path, body=None, *, cap=3):
        remaining = self.deadline.remaining(cap)
        request = canonical_json({'url': self.base_url + path, 'token': self.token,
                                  'method': method, 'body': body, 'timeout': remaining})
        proc = subprocess.Popen([sys.executable, '-m', __name__, '--http-worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            output, _ = proc.communicate(request.encode(), timeout=self.deadline.remaining(cap))
        except BaseException:
            try:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass  # Preserve original timeout; only this owned child is targeted.
            finally:
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None:
                        stream.close()
            raise
        finally:
            if proc.poll() is not None:
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None:
                        stream.close()
        self.deadline.remaining()
        if proc.returncode or len(output) > MAX_RESPONSE + 1024:
            raise ValueError('bounded HTTP response unavailable')
        result = json.loads(output)
        if result['status'] >= 400:
            raise ApiError(result['status'], result['body'])
        return result['body']
    def get(self, path):
        return self.request('GET', path)
    def post(self, path, body, *, cap=3):
        return self.request('POST', path, body, cap=cap)


def atomic_json(path: Path, value):
    # Resolved owner-only parent is enforced through the binding reader.
    if path.parent != path.parent.resolve(strict=True) or path.is_symlink():
        raise ValueError('noncanonical private state path')
    metadata = path.parent.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError('private state parent required')
    raw = (canonical_json(value) + '\n').encode()
    if len(raw) > 64 * 1024:
        raise ValueError('private lease exceeds cap')
    fd, temporary = tempfile.mkstemp(prefix='.admission-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Lease:
    def __init__(self, binding_path, deadline):
        self.path = binding_path.with_name(binding_path.name + '.p2-lease')
        self.lock = binding_path.with_name(binding_path.name + '.p2-lock')
        self.deadline = deadline
    @contextmanager
    def locked(self):
        if self.lock.parent != self.lock.parent.resolve(strict=True):
            raise ValueError("noncanonical lease parent")
        parent = self.lock.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError("private lease parent required")
        fd = os.open(self.lock, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise ValueError("private regular lease lock required")
            end = time.monotonic() + self.deadline.remaining(1)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise TimeoutError('lease_busy')
                    time.sleep(min(.01, self.deadline.remaining()))
            yield self
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    def read(self):
        return json.loads(_private_file(self.path)) if self.path.exists() else None
    def write(self, value):
        self.deadline.remaining()
        atomic_json(self.path, value)
        self.deadline.remaining()


def _worker():
    request = json.load(sys.stdin)
    body = request['body']
    data = None if body is None else canonical_json(body).encode()
    req = urllib.request.Request(request['url'], data=data, method=request['method'],
        headers={'Authorization': 'Bearer ' + request['token'],
                 'Content-Type': 'application/json', 'Accept': 'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(req, timeout=request['timeout'])
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('response exceeds cap')
        result = {'status': response.code, 'body': json.loads(raw)}
    sys.stdout.write(canonical_json(result))

if __name__ == '__main__':
    if sys.argv[1:] != ['--http-worker']:
        raise SystemExit(2)
    _worker()
