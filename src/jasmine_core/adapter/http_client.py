"""P3 owned HTTP child with exact 4 MiB response cap and total deadline."""
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
from ..capture.p2_runtime import Deadline

MAX_RESPONSE = 4 * 1024 * 1024

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
