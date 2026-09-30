"""Owned, database-free fingerprint worker and finite server scan budget."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import errors, fingerprint
from .canonical import canonical_json

MAX_OUTPUT_BYTES = 2097152


class ScanFailure(errors.CoreError):
    status = 503
    def __init__(self, code, message='', **details):
        super().__init__(message or code, **details)
        self.code = code


class Budget:
    def __init__(self, seconds=30):
        self.end = time.monotonic() + seconds

    def remaining(self, cap=None):
        value = self.end - time.monotonic()
        if value <= 0:
            raise ScanFailure('checkpoint_scan_timeout')
        return min(value, cap) if cap is not None else value


def _cleanup(proc):
    denied = False
    # The owned group may contain a child holding stdout after its leader exits.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        denied = True
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=.5)
    except (subprocess.TimeoutExpired, OSError):
        denied = True
    finally:
        if proc.stdout:
            try:
                proc.stdout.close()
            except OSError:
                denied = True
    return denied


def scan_workspace(budget, *, conn=None, root=None):
    """Scan only server-selected root, outside every database transaction."""
    if conn is not None and conn.in_transaction:
        raise RuntimeError('workspace scan cannot run inside a database transaction')
    selected = Path(root) if root is not None else fingerprint.configured_root()
    deadline = time.monotonic() + budget.remaining(10)
    started = time.time()
    try:
        proc = subprocess.Popen(
            [sys.executable, '-m', 'jasmine_core.continuity_scan', str(selected)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise ScanFailure('checkpoint_scan_failed', 'worker could not start') from exc
    chunks = bytearray()
    selector = selectors.DefaultSelector()
    failure = None
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = min(deadline-time.monotonic(), budget.remaining())
            if remaining <= 0:
                raise ScanFailure('checkpoint_scan_timeout')
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    break
                chunks.extend(chunk)
                if len(chunks) > MAX_OUTPUT_BYTES:
                    raise ScanFailure('checkpoint_scan_too_large')
        try:
            proc.wait(timeout=min(max(deadline-time.monotonic(), .001),budget.remaining()))
        except subprocess.TimeoutExpired as exc:
            raise ScanFailure('checkpoint_scan_timeout') from exc
        budget.remaining()
        if proc.returncode == 3:
            raise ScanFailure('checkpoint_scan_too_large')
        if proc.returncode:
            raise ScanFailure('checkpoint_scan_failed', worker_exit=proc.returncode)
        try:
            snapshot = json.loads(chunks)
        except (ValueError, UnicodeError) as exc:
            raise ScanFailure('checkpoint_scan_failed', 'worker returned invalid JSON') from exc
        if not isinstance(snapshot, dict):
            raise ScanFailure('checkpoint_scan_failed')
        return {'snapshot':snapshot,'sample_window':{'started_at_unix':started,'finished_at_unix':time.time()}}
    except BaseException as exc:
        failure = exc
        raise
    finally:
        selector.close()
        denied = _cleanup(proc)
        if denied and isinstance(failure, errors.CoreError):
            failure.details['worker_cleanup'] = 'incomplete_or_denied'


def main():
    try:
        snapshot = fingerprint.capture(Path(sys.argv[1]))
        raw = canonical_json(snapshot).encode('utf-8')
        if len(raw) > MAX_OUTPUT_BYTES:
            return 3
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
