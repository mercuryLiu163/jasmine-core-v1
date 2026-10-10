#!/usr/bin/env python3.13
"""Author/independent isolated implementation runs of the frozen 43 variants.

Registry/auth bootstrap is private; all ordinary business fixtures use Core HTTP.
An audit hook observes real scanner Popen attempts, never replaces the scanner.
The only fault injection is into labelled offline private SQLite backup forks.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
SPEC = 'docs/validation/p6-checkpoint-negatives'
SPEC_COMMIT = '5079948808d4c8aa9cb5763c747def422c12c055'
READ = ['objects:read', 'state:read', 'authority:read', 'evidence:read', 'events:read']
FULL = READ + ['objects:write', 'events:write', 'state:write', 'state:accept',
               'authority:propose', 'authority:manage', 'checkpoint:read',
               'checkpoint:write', 'resume:read', 'resume:build']


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def snapshot(path):
    con = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                  "AND name NOT LIKE 'sqlite_%' ORDER BY name") if r[0] not in ('audit_log', 'api_keys')]
        return {t: sorted([dict(r) for r in con.execute('SELECT * FROM ' + t)], key=canonical) for t in tables}
    finally:
        con.close()


def backup(source, target):
    src = sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def serve(args):
    sys.path.insert(0, str(args.archive / 'src'))
    from jasmine_core import auth, db, ids, registry
    from jasmine_core.migrations import migrate
    from jasmine_core.api.server import Application, _LoopbackHTTPServer, _Handler
    runtime = args.runtime
    database = runtime / 'core.db'
    con = db.connect(database)
    migrate(con)
    credentials = runtime / 'private-credentials.json'
    if not credentials.exists():
        host = ids.new_id('hst')
        actors = {name: ids.new_id('act') for name in ('human', 'admin', 'system')}
        reg = registry.Registry(con)
        reg.upsert_host(host, display_name='PRIVATE synthetic checkpoint validation host')
        for name, actor in actors.items():
            reg.upsert_actor(actor, kind='system' if name == 'system' else 'human', home_host_id=host)
        store = auth.Auth(con)
        keys = {
            'human': store.issue_key(actor_id=actors['human'], label='private operator', scopes=FULL),
            'browser': store.issue_key(actor_id=actors['human'], label='private browser reads', scopes=READ + ['checkpoint:read', 'resume:read']),
            'admin': store.issue_key(actor_id=actors['admin'], label='private separate audit validator', scopes=['admin']),
            'system': store.issue_key(actor_id=actors['system'], label='private fixture evidence producer', scopes=['evidence:write']),
        }
        for kind, scopes in [('checkpoint', READ + ['checkpoint:read']), ('resume', READ + ['checkpoint:read', 'resume:read'])]:
            for missing in scopes:
                keys[kind + '_missing_' + missing.replace(':', '_')] = store.issue_key(
                    actor_id=actors['human'], label='private missing ' + missing, scopes=[s for s in scopes if s != missing])
        write(credentials, {'host': host, 'actors': actors, 'keys': keys})
    con.close()
    trace = runtime / 'observer.jsonl'
    lock = threading.Lock()
    local = threading.local()
    sequence = 0

    def observe(kind, **fields):
        nonlocal sequence
        with lock:
            sequence += 1
            with trace.open('a') as f:
                f.write(canonical({'seq': sequence, 'pid': os.getpid(), 'at_unix': time.time(),
                         'kind': kind, **fields}) + '\n')

    def hook(event, arguments):
        if event == 'subprocess.Popen':
            argv = arguments[1]
            if isinstance(argv, (list, tuple)) and 'jasmine_core.continuity_scan' in argv:
                observe('scanner_popen_attempt', request_id=getattr(local, 'request_id', None),
                        executable=str(arguments[0]), argv=list(argv))

    sys.addaudithook(hook)
    def profile(frame, event, argument):
        if frame.f_code.co_name == 'scan_workspace' and frame.f_code.co_filename == str(args.archive / 'src/jasmine_core/continuity_scan.py'):
            if event in ('call', 'return'):
                observe('scanner_call_enter' if event == 'call' else 'scanner_call_exit',
                        request_id=getattr(local, 'request_id', None))
    sys.setprofile(profile)
    threading.setprofile(profile)
    observe('observer_started', method='CPython profile entry/exit plus audit hook for every continuity scanner Popen attempt',
            scanner_source_sha256=digest((args.archive / 'src/jasmine_core/continuity_scan.py').read_bytes()),
            continuity_source_sha256=digest((args.archive / 'src/jasmine_core/continuity.py').read_bytes()))

    class ObservedApplication(Application):
        def handle(self, request):
            local.request_id = request.request_id
            observe('request_start', request_id=request.request_id, method=request.method, path=request.path)
            try:
                response = super().handle(request)
                observe('request_end', request_id=request.request_id, status=response.status)
                return response
            finally:
                local.request_id = None

    server = _LoopbackHTTPServer(('127.0.0.1', 0), _Handler)
    server.app = ObservedApplication(database)
    server.daemon_threads = True
    write(runtime / 'address.json', {'core': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid()})

    def stop(signum, frame):
        observe('observer_stopped', signal=signum)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class Client:
    def __init__(self, runtime, evidence):
        self.runtime = runtime
        self.database = runtime / 'core.db'
        self.core = json.loads((runtime / 'address.json').read_text())['core']
        self.credentials = json.loads((runtime / 'private-credentials.json').read_text())
        self.evidence = evidence
        self.calls = []

    def request(self, method, path, body=None, key='human', phase='precondition'):
        request_id = 'cp-' + uuid.uuid4().hex
        credential = self.credentials['keys'][key]
        data = None if body is None else canonical(body).encode()
        req = urllib.request.Request(self.core + path, data=data, method=method,
              headers={'Authorization': 'Bearer ' + credential['token'], 'Content-Type': 'application/json',
                       'X-Request-Id': request_id})
        started = time.time()
        try:
            response = urllib.request.urlopen(req, timeout=35)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result = {'method': method, 'path': path, 'request_body': body, 'status': response.status,
                      'body': json.loads(response.read()), 'request_id': response.headers.get('X-Request-Id'),
                      'actor_id': credential['actor_id'], 'key_id': credential['key_id'], 'scopes': credential['scopes'],
                      'started_at_unix': started, 'finished_at_unix': time.time(), 'phase': phase}
        if result['request_id'] != request_id:
            raise RuntimeError('Core request correlation mismatch')
        self.calls.append(result)
        return result

    def ok(self, method, path, body=None, key='human'):
        result = self.request(method, path, body, key)
        if result['status'] >= 400:
            raise RuntimeError(f"precondition {method} {path}: {result['status']} {result['body']}")
        return result['body']

    def operation(self, name, method, path, body=None, key='human'):
        before = snapshot(self.database)
        backup(self.database, self.evidence / (name + '-before.db'))
        r = self.request(method, path, body, key, phase='target')
        after = snapshot(self.database)
        backup(self.database, self.evidence / (name + '-after.db'))
        trace = lines(self.runtime / 'observer.jsonl')
        observed = [e for e in trace if e.get('request_id') == r['request_id']]
        audit = self.request('GET', '/v1/audit?limit=1000&actor_id=' + r['actor_id'], key='admin', phase='separate-audit-validator')
        prefix = self.evidence.name + '/'
        proof = {'http': r, 'before': before, 'after': after, 'observer': observed,
                'observer_started': next(e for e in reversed(trace) if e['kind'] == 'observer_started' and e['pid'] == observed[0]['pid']),
                'audit_read': audit, 'audit': [a for a in audit['body'].get('entries', []) if a['request_id'] == r['request_id']],
                'operation_receipt': prefix + name + '-operation.json',
                'database_receipts': [prefix + name + '-before.db', prefix + name + '-after.db']}
        write(self.evidence / (name + '-operation.json'), proof)
        return proof

    def fixture(self, label, steps=False, rule=False):
        h = self.credentials['host']
        project = self.ok('POST', '/v1/projects', {'name': 'SYNTHETIC isolated ' + label, 'host_id': h})['object']
        task = self.ok('POST', '/v1/tasks', {'title': 'SYNTHETIC ' + label, 'host_id': h, 'project_id': project['project_id']})['object']
        source = self.source(task, label)
        value = {'project': project, 'task': task, 'source': source, 'host': h}
        if steps:
            step = self.ok('POST', '/v1/tasks/' + task['task_id'] + '/steps',
                           {'title': 'SYNTHETIC Step', 'host_id': h, 'expected_revision': task['revision']})['step']
            value['step'] = step
            value['task'] = self.ok('GET', '/v1/tasks/' + task['task_id'])['task']
        if rule:
            value['rule'] = self.ok('POST', '/v1/rules/proposals', {'host_id': h, 'rule_key': 'private-' + uuid.uuid4().hex,
                'kind': 'RULE', 'severity': 'NORMAL', 'enforcement': 'CONTEXT', 'content': 'SYNTHETIC fixture Rule',
                'matcher': {}, 'scope': {'kind': 'task', 'project_id': project['project_id'], 'task_id': task['task_id']},
                'origin_event_id': source['event_id']})['rule']
        return value

    def source(self, task, label, session=None):
        body = {'event_type': 'assistant.message', 'source_system': 'synthetic-checkpoint-validation',
                'host_id': self.credentials['host'], 'project_id': task['project_id'], 'task_id': task['task_id'],
                'payload': {'text': 'SYNTHETIC private Checkpoint fixture ' + label + '; no native session claim.'}}
        if session:
            body['session_id'] = session
        return self.ok('POST', '/v1/events', body)['event']

    def checkpoint(self, fixture, label, session=None, source=None):
        body = {'host_id': fixture['host'], 'task_id': fixture['task']['task_id'],
                'source_event_id': (source or fixture['source'])['event_id'], 'reason': 'MANUAL',
                'idempotency_key': label + '-checkpoint',
                'note': {'text': 'SYNTHETIC historical note ' + label, 'source_event_id': (source or fixture['source'])['event_id']}}
        if session:
            body['session_id'] = session
        op = self.operation(label + '-checkpoint', 'POST', '/v1/checkpoints', body)
        if op['http']['status'] != 201:
            raise RuntimeError('Checkpoint creation failed: ' + canonical(op['http']))
        return op

    def resume(self, fixture, label, checkpoint=None, body=None):
        request = body or {'host_id': fixture['host'], 'source_event_id': fixture['source']['event_id'], 'idempotency_key': label + '-resume'}
        if checkpoint:
            request['checkpoint_id'] = checkpoint['checkpoint_id']
        return self.operation(label + '-resume', 'POST', '/v1/tasks/' + fixture['task']['task_id'] + '/resume', request)

    def advance(self, fixture, label):
        task = self.ok('GET', '/v1/tasks/' + fixture['task']['task_id'])['task']
        return self.ok('POST', '/v1/tasks/' + task['task_id'] + '/criteria', {'host_id': fixture['host'],
                       'expected_revision': task['revision'], 'acceptance_criteria': {'requirements': [
                           {'key': label, 'kind': 'COMMAND_RESULT', 'required_result': 'PASS'}]}})


def core_pages(client, checkpoint=None, resume=None):
    pages = []
    for kind, value, prefix in [('checkpoint', checkpoint, '/v1/checkpoints/'), ('resume', resume, '/v1/resumes/')]:
        if value is not None:
            ident = value[kind + '_id']
            path = prefix + ident
            data = client.ok('GET', path)
            pages.append({'kind': kind, 'hash': '#/checkpoints?checkpoint_id=' + ident if kind == 'checkpoint' else '#/resume/' + ident,
                          'path': path, 'expected': data, 'entity_id': ident, 'refresh': True})
    return pages


def add_lineage(client, row):
    """Read only existing successful targets; absent targets keep absence receipts."""
    row['lineage'] = []
    seen = set()
    for page in row['pages']:
        kind = page['kind']
        if kind not in ('checkpoint', 'resume'):
            continue
        entity = page['expected'][kind]
        identity = (kind, entity[kind + '_id'])
        if identity in seen:
            continue
        seen.add(identity)
        row['lineage'].append({'entity': entity,
            'source_read': client.request('GET', '/v1/events/' + entity['source_event_id'], phase='source-readback'),
            'command_read': client.request('GET', '/v1/events/' + entity['command_event_id'], phase='command-readback'),
            'mutation_label': row['mode'] if row.get('injection') else None})
    if not row['lineage']:
        row['precondition_source_read'] = client.request(
            'GET', '/v1/events/' + row['fixture']['source']['event_id'], phase='legitimate-precondition-source-only')


def inject(source, target, checkpoint, ident):
    """Only an offline private backup. Original DB and product code are untouched."""
    backup(source, target)
    con = sqlite3.connect(target, isolation_level=None)
    con.row_factory = sqlite3.Row
    schema = [dict(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    trigger = con.execute("SELECT sql FROM sqlite_master WHERE name='checkpoints_no_update'").fetchone()[0]
    original = dict(con.execute('SELECT * FROM checkpoints WHERE checkpoint_id=?', (checkpoint['checkpoint_id'],)).fetchone())
    p = json.loads(original['projection_json'])
    fields = {}
    if ident == 'CP13.default':
        fields['task_revision'] = original['task_revision'] + 1
        p['task']['revision'] = fields['task_revision']
        next(o for o in p['objects'] if o['object_type'] == 'task')['revision'] = fields['task_revision']
    elif ident == 'CP14.default':
        p['steps'][0]['revision'] += 1
        next(o for o in p['objects'] if o['object_type'] == 'step')['revision'] += 1
    elif ident == 'CP15.default':
        p['rules'][0]['version'] += 1
    elif ident in ('CP16.default', 'CP17.default', 'CP18.default'):
        key = {'CP16.default': 'projection_digest', 'CP17.default': 'full_context_digest', 'CP18.default': 'workspace_digest'}[ident]
        fields[key] = ('1' if original[key][0] != '1' else '2') + original[key][1:]
    elif ident == 'CP19.default':
        p = []
    elif ident == 'CP20.column_version':
        fields['projection_version'] = 'jasmine.task-snapshot.v0'
    elif ident == 'CP20.inner_schema':
        p['snapshot_schema'] = 'jasmine.task-snapshot.v0'
    elif ident == 'CP21.default':
        p['task_id'] = next(r[0] for r in con.execute('SELECT task_id FROM tasks WHERE task_id<>?', (original['task_id'],)))
    elif ident == 'CP22.default':
        from jasmine_core import ids
        fields['synthetic_removed_key'] = ['step', ids.new_id('stp')]
        p['objects'].append({'object_type': 'step', 'object_id': fields['synthetic_removed_key'][1], 'revision': 99})
        p['objects'].sort(key=lambda o: (o['object_type'], o['object_id']))
    else:
        raise ValueError(ident)
    synthetic = fields.pop('synthetic_removed_key', None)
    if ident not in ('CP16.default', 'CP17.default', 'CP18.default', 'CP20.column_version'):
        fields.update(projection_json=canonical(p), projection_digest=digest(p), full_context_digest=digest(p))
    sql = 'UPDATE checkpoints SET ' + ','.join(k + '=?' for k in fields) + ' WHERE checkpoint_id=?'
    try:
        con.execute('BEGIN IMMEDIATE')
        con.execute('DROP TRIGGER checkpoints_no_update')
        if ident == 'CP20.column_version':
            con.execute('PRAGMA ignore_check_constraints=ON')
        con.execute(sql, [*fields.values(), checkpoint['checkpoint_id']])
        con.execute(trigger)
        con.execute('COMMIT')
    finally:
        if con.in_transaction:
            con.rollback()
        con.execute('PRAGMA ignore_check_constraints=OFF')
    after = dict(con.execute('SELECT * FROM checkpoints WHERE checkpoint_id=?', (checkpoint['checkpoint_id'],)).fetchone())
    result = {'mode': 'REAL_CORE_CORRUPT_STORE', 'variant': ident, 'original': original, 'mutated': after,
              'original_backup_sha256': digest(Path(source).read_bytes()),
              'sql': sql, 'parameters': [*fields.values(), checkpoint['checkpoint_id']], 'synthetic_removed_key': synthetic,
              'schema_before': schema, 'schema_after': [dict(r) for r in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")],
              'integrity_check': [r[0] for r in con.execute('PRAGMA integrity_check')],
              'foreign_key_check': [list(r) for r in con.execute('PRAGMA foreign_key_check')],
              'ignore_check_constraints_after': con.execute('PRAGMA ignore_check_constraints').fetchone()[0]}
    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    con.close()
    result['fork_sha256_before_serving'] = digest(Path(target).read_bytes())
    return result


class Owned:
    def __init__(self, out):
        self.out, self.processes, self.files, self.stopped = out, [], [], {}

    def start(self, name, command, cwd, env, stdin=None):
        log = (self.out / (name + '.log')).open('w')
        self.files.append(log)
        p = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log,
                             stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, start_new_session=True)
        self.processes.append((name, p))
        if stdin is not None:
            p.stdin.write(stdin)
            p.stdin.close()
        return p

    @staticmethod
    def members(pgid):
        return [int(parts[0]) for row in subprocess.check_output(['ps', '-axo', 'pid=,pgid='], text=True).splitlines()
                if len(parts := row.split()) == 2 and int(parts[1]) == pgid]

    def stop(self, name, process):
        if process.pid in self.stopped:
            return {**self.stopped[process.pid], 'name': name, 'already_reaped': True}
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.members(process.pid):
                try:
                    os.killpg(process.pid, sig)
                except OSError:
                    if self.members(process.pid):
                        raise
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        deadline = time.monotonic() + 3
        while self.members(process.pid) and time.monotonic() < deadline:
            time.sleep(.05)
        receipt = {'name': name, 'pid': process.pid, 'pgid': process.pid, 'exit_code': process.poll(),
                   'remaining_owned_pids': self.members(process.pid), 'group_gone': not self.members(process.pid)}
        if receipt['group_gone']:
            self.stopped[process.pid] = receipt
        return receipt

    def close(self):
        receipts = []
        for name, p in reversed(self.processes):
            try:
                receipts.append(self.stop(name, p))
            except Exception as exc:
                receipts.append({'name': name, 'pid': p.pid, 'cleanup_error': str(exc), 'group_gone': False})
        for f in self.files:
            f.close()
        return receipts


def wait_file(path, process, timeout=25):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('owned process failed to publish ' + path.name)
        time.sleep(.05)


def start_core(owned, archive, runtime, env, label, evidence):
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / 'address.json').unlink(missing_ok=True)
    proc = owned.start(label, [sys.executable, '-I', str(archive / 'scripts/p6/checkpoint_negatives.py'),
                       'serve', '--archive', str(archive), '--runtime', str(runtime)], archive, env)
    wait_file(runtime / 'address.json', proc)
    return proc, Client(runtime, evidence)


def start_dashboard(owned, archive, core, env, label, enabled=True):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    proxy_env = {**env, 'P6_CORE_ORIGIN': core, 'P6_ENABLE_P3_READS': '1' if enabled else '0',
                 'P6_ENABLE_WEB_CONFIRMATIONS': '0', 'P6_ENABLE_P4_READS': '0', 'P6_ENABLE_P4_WRITES': '0', 'P6_ENABLE_P7_READS': '0'}
    p = owned.start(label, ['node', str(archive / 'web/server/serve.mjs'), '--root', str(archive / 'web/dist'),
                   '--host', '127.0.0.1', '--port', str(port)], archive, proxy_env)
    deadline = time.monotonic() + 20
    while True:
        try:
            urllib.request.urlopen(origin + '/v1/health', timeout=1).close()
            break
        except Exception:
            if p.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(label + ' startup failed')
            time.sleep(.1)
    return origin


def run(args):
    sys.path.insert(0, str(ROOT / 'scripts/p6'))
    from checkpoint_negative_gate import evaluate_bundle, adversarial_bundle_checks
    os.umask(0o077)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    candidate = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', args.ref], text=True).strip()
    report = {'candidate_commit': candidate, 'spec_commit': SPEC_COMMIT, 'role': args.role,
              'status': 'FAIL', 'scope_acceptance_signed': False, 'records': [],
              'coverage_limit': 'Frozen 26 scenarios / 43 variants only; no incompatible algorithm/coverage/extra_paths branch claim.',
              'daily_use_released': False, 'deployment_changed': False, 'item6_enabled': False}
    owned = Owned(out)
    private = Path(tempfile.mkdtemp(prefix='p6-checkpoint-private-'))
    clients, browser_cfg, profile = [], [], None
    archive_sha = None
    try:
        archive = private / 'archive'
        archive.mkdir()
        tar = subprocess.check_output(['git', '-C', str(ROOT), 'archive', '--format=tar', candidate])
        archive_sha = digest(tar)
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extractall(archive, filter='data')
        report.update(source_archive_sha256=digest(tar), source_tree=subprocess.check_output(
            ['git', '-C', str(ROOT), 'rev-parse', candidate + '^{tree}'], text=True).strip())
        matrix = json.loads((archive / SPEC / 'acceptance-matrix.v1.json').read_text())
        report['records'] = [{'id': c['id'] + '.' + v['id'], 'case_id': c['id'], 'variant_id': v['id'],
                              'status': 'NOT_RUN', 'reason': 'Preparation or collection has not reached this variant'}
                             for c in matrix['cases'] for v in c['variants']]
        def put(row):
            i = next(i for i, existing in enumerate(report['records']) if existing['id'] == row['id'])
            report['records'][i] = row
        launcher_paths = ['scripts/p6/checkpoint_negatives.py', 'scripts/p6/checkpoint_negatives.mjs', 'scripts/p6/checkpoint_negative_gate.py']
        report['launcher_source_hashes'] = {p: digest((archive / p).read_bytes()) for p in launcher_paths}
        if any(digest((ROOT / p).read_bytes()) != report['launcher_source_hashes'][p] for p in launcher_paths):
            raise RuntimeError('launcher does not match candidate archive')
        report['core_source_hashes'] = {p: digest((archive / p).read_bytes()) for p in
                                       ['src/jasmine_core/continuity.py', 'src/jasmine_core/continuity_scan.py', 'src/jasmine_core/api/handlers.py', 'src/jasmine_core/api/server.py']}
        report['matrix_sha256'] = digest((archive / SPEC / 'acceptance-matrix.v1.json').read_bytes())
        expected_matrix = subprocess.check_output(['git', '-C', str(ROOT), 'show', SPEC_COMMIT + ':' + SPEC + '/acceptance-matrix.v1.json'])
        if digest(expected_matrix) != report['matrix_sha256']:
            raise RuntimeError('frozen matrix drift')
        write(out / 'source-bindings.json', {k: report[k] for k in ('candidate_commit', 'source_tree', 'source_archive_sha256', 'core_source_hashes', 'launcher_source_hashes', 'matrix_sha256')})
        write(out / 'matrix.json', matrix)
        sys.path.insert(0, str(archive / 'src'))
        workspace = private / 'workspace'
        workspace.mkdir()
        (workspace / 'tracked.txt').write_text('controlled original\n')
        (workspace / 'declared-input.txt').write_text('controlled declared input\n')
        for cmd in [['git', 'init', '-q'], ['git', 'add', 'tracked.txt'],
                    ['git', '-c', 'user.name=Private fixture', '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'private fixture']]:
            subprocess.run(cmd, cwd=workspace, check=True, stdout=subprocess.DEVNULL)
        env = {k: os.environ[k] for k in ('HOME', 'USER', 'LANG', 'TMPDIR') if k in os.environ}
        env.update(PATH='/opt/homebrew/bin:/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                   PYTHONPATH=str(archive / 'src'),
                   npm_config_cache=str(private / 'npm-cache'), JASMINE_CORE_WORKSPACE_ROOT=str(workspace),
                   JASMINE_CORE_FINGERPRINT_EXTRA_PATHS='["declared-input.txt"]')
        for label, cmd in [('npm-ci', ['npm', 'ci', '--no-audit', '--no-fund']), ('build', ['npm', 'run', 'build'])]:
            with (out / (label + '.log')).open('w') as f:
                done = subprocess.run(cmd, cwd=archive / 'web', env=env, stdout=f, stderr=subprocess.STDOUT, timeout=120)
            if done.returncode:
                raise RuntimeError(label + ' failed')
        report['toolchain'] = {'python': sys.version, 'node': subprocess.check_output(['node', '--version'], env=env, text=True).strip(),
                              'npm': subprocess.check_output(['npm', '--version'], env=env, text=True).strip(),
                              'lockfile_sha256': digest((archive / 'web/package-lock.json').read_bytes())}
        report['python_import_root'] = str(archive / 'src')
        report['built_assets'] = {p.relative_to(archive / 'web/dist').as_posix(): digest(p.read_bytes())
                                  for p in (archive / 'web/dist').rglob('*') if p.is_file()}
        runtime = private / 'base'
        evidence = out / 'base'
        evidence.mkdir()
        core_proc, client = start_core(owned, archive, runtime, env, 'base-core', evidence)
        clients.append(client)
        health = client.ok('GET', '/v1/health')
        if health.get('schema_version') != 12:
            raise RuntimeError('schema 12 required')
        report['health'] = health
        normal = {}
        for number in range(1, 11):
            ident = f'CP{number:02d}.default'
            print('prepare ' + ident, flush=True)
            f = client.fixture(ident, rule=number == 3)
            if number in (6, 8):
                (workspace / 'declared-input.txt').unlink()
            cp_op = None if number == 9 else client.checkpoint(f, ident)
            cp = cp_op['http']['body']['checkpoint'] if cp_op else None
            row = {'id': ident, 'case_id': ident.split('.')[0], 'variant_id': 'default', 'mode': matrix['cases'][number - 1]['evidence_mode'],
                   'fixture': f, 'checkpoint_creation': cp_op, 'operations': [], 'absence': {}, 'preconditions': []}
            if number == 2:
                row['preconditions'].append(client.advance(f, ident))
            elif number == 3:
                row['preconditions'].append(client.ok('POST', '/v1/rules/' + f['rule']['rule_id'] + '/approve',
                                                     {'host_id': f['host'], 'expected_revision': f['rule']['revision']}))
            elif number == 4:
                command = [sys.executable, '-c', 'assert 2 + 2 == 4; print("private fixture assertion passed")']
                execution = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
                body = {'task_id': f['task']['task_id'], 'host_id': f['host'], 'codex_session_id': 'SYNTHETIC-private-fixture',
                        'turn_id': ident, 'tool_use_id': ident, 'tool_name': 'exec_command', 'kind': 'COMMAND_RESULT',
                        'tool_input': {'cmd': shlex.join(command)}, 'tool_response': {'exit_code': execution.returncode,
                        'stdout': execution.stdout, 'stderr': execution.stderr}}
                row['preconditions'].append({'actual_command': command, 'actual_exit_code': execution.returncode,
                                              'actual_stdout': execution.stdout, 'actual_stderr': execution.stderr,
                                              'response': client.ok('POST', '/v1/tool-results', body, key='system')})
            elif number == 5:
                (workspace / 'tracked.txt').write_text('controlled changed\n')
            elif number == 6:
                (workspace / 'declared-input.txt').write_text('controlled declared input\n')
            elif number == 7:
                (workspace / 'declared-input.txt').unlink()
            op = client.resume(f, ident, cp)
            row['operations'].append(op)
            if op['http']['status'] != 201:
                raise RuntimeError('normal Resume failed: ' + canonical(op['http']))
            result = op['http']['body']['resume']
            pages = core_pages(client, cp, result)
            if number == 9:
                path = '/v1/tasks/' + f['task']['task_id'] + '/checkpoints/latest'
                row['latest'] = client.request('GET', path)
                pages.insert(0, {'kind': 'empty', 'hash': '#/checkpoints?task_id=' + f['task']['task_id'], 'path': path,
                                 'expected': row['latest']['body'], 'entity_id': None})
                row['absence']['checkpoint'] = {'expected': None, 'observed': row['latest']['body']['checkpoint'], 'reason': 'Task has no Checkpoint'}
            if number == 10:
                row['preconditions'].append(client.advance(f, ident))
                old_read = client.operation(ident + '-historic-get', 'GET', '/v1/resumes/' + result['resume_id'])
                replay = client.resume(f, ident + '-replay', body=copy.deepcopy(op['http']['request_body']))
                request = {**op['http']['request_body'], 'idempotency_key': ident + '-fresh-key'}
                fresh = client.resume(f, ident + '-fresh', body=request)
                row['operations'] += [old_read, replay, fresh]
                pages += core_pages(client, resume=fresh['http']['body']['resume'])
            row['pages'] = pages
            normal[ident] = row
            put(row)
            if number in (5, 7, 8):
                (workspace / 'tracked.txt').write_text('controlled original\n')
                (workspace / 'declared-input.txt').write_text('controlled declared input\n')

        # Source timestamps and command creation order disagree without clock spoofing.
        ident = 'CP11.default'
        f = client.fixture(ident)
        second = client.fixture('CP11-other-task')
        sessions = [client.ok('POST', '/v1/sessions', {'project_id': f['task']['project_id'], 'task_id': f['task']['task_id'],
                                                     'host_id': f['host'], 'title': 'SYNTHETIC session ' + str(i)})['object'] for i in range(2)]
        source_a = client.source(f['task'], 'source-first')
        source_b = client.source(f['task'], 'source-second')
        creates = [client.checkpoint(f, ident + '-first', sessions[0]['session_id'], source_b),
                   client.checkpoint(f, ident + '-last', sessions[1]['session_id'], source_a)]
        cp_a, cp_b = [c['http']['body']['checkpoint'] for c in creates]
        selection = []
        pages = []
        for session, expected in [(None, cp_b), (sessions[0]['session_id'], cp_a), (sessions[1]['session_id'], cp_b)]:
            path = '/v1/tasks/' + f['task']['task_id'] + '/checkpoints/latest' + ('?session_id=' + session if session else '')
            r = client.request('GET', path)
            selection.append(r)
            pages.append({'kind': 'checkpoint', 'hash': '#/checkpoints?task_id=' + f['task']['task_id'] + ('&session_id=' + session if session else ''),
                          'path': path, 'expected': {'checkpoint': expected}, 'entity_id': expected['checkpoint_id']})
        empty_session = client.ok('POST', '/v1/sessions', {'project_id': second['task']['project_id'], 'task_id': second['task']['task_id'],
                                                          'host_id': f['host'], 'title': 'SYNTHETIC empty session'})['object']
        empty_path = '/v1/tasks/' + second['task']['task_id'] + '/checkpoints/latest?session_id=' + empty_session['session_id']
        empty = client.request('GET', empty_path)
        pages.append({'kind': 'empty', 'hash': '#/checkpoints?task_id=' + second['task']['task_id'] + '&session_id=' + empty_session['session_id'],
                      'path': empty_path, 'expected': empty['body'], 'entity_id': None})
        pages += core_pages(client, cp_a)
        pages[-1]['hash'] += '&task_id=bad-task&session_id=bad-session'
        row = {'id': ident, 'case_id': 'CP11', 'variant_id': 'default', 'mode': matrix['cases'][10]['evidence_mode'],
               'fixture': f, 'checkpoint_creations': creates, 'checkpoint_creation': creates[0], 'operations': [],
               'selection': selection, 'empty_selection': empty, 'source_order': [source_a, source_b], 'pages': pages, 'absence': {}}
        put(row)
        # Two genuine receipts of one Task have different IDs and historical notes.
        switch_op_a = client.resume(f, 'switch-A', cp_a)
        switch_op_b = client.resume(f, 'switch-B', cp_b)
        switch_resume_a = switch_op_a['http']['body']['resume']
        switch_resume_b = switch_op_b['http']['body']['resume']
        put({'id': 'CP12.default', 'case_id': 'CP12', 'variant_id': 'default', 'mode': matrix['cases'][11]['evidence_mode'],
             'fixture': f, 'operations': [], 'checkpoint_creation': creates[0], 'pages': core_pages(client, cp_a, switch_resume_a) + core_pages(client, cp_b, switch_resume_b),
             'resume_creations': [switch_op_a, switch_op_b],
             'switches': [['checkpoint', cp_a['checkpoint_id'], cp_b['checkpoint_id']], ['resume', switch_resume_a['resume_id'], switch_resume_b['resume_id']]], 'absence': {}})

        f = client.fixture('fault-base', steps=True, rule=True)
        fault_cp_op = client.checkpoint(f, 'fault-base')
        fault_cp = fault_cp_op['http']['body']['checkpoint']
        stopped = owned.stop('base-core-for-offline-backup', core_proc)
        if not stopped['group_gone']:
            raise RuntimeError('base Core not stopped for offline forks')
        write(out / 'offline-base-stop.json', stopped)
        backup(client.database, out / 'fault-base-original.db')
        for case in matrix['cases'][12:22]:
            for variant in case['variants']:
                ident = case['id'] + '.' + variant['id']
                print('prepare ' + ident, flush=True)
                fork = private / ident
                fork.mkdir()
                destination = out / ident
                destination.mkdir()
                shutil.copy2(runtime / 'private-credentials.json', fork / 'private-credentials.json')
                injection = inject(out / 'fault-base-original.db', fork / 'core.db', fault_cp, ident)
                injection.update(original_backup_path='fault-base-original.db', fork_backup_path=ident + '/fault-before-serving.db')
                write(destination / 'injection.json', injection)
                # No server has opened this fork; preserve the exact checkpointed byte image.
                shutil.copy2(fork / 'core.db', destination / 'fault-before-serving.db')
                _, c = start_core(owned, archive, fork, env, ident + '-core', destination)
                clients.append(c)
                op = c.resume(f, ident, fault_cp)
                actual_cp = c.ok('GET', '/v1/checkpoints/' + fault_cp['checkpoint_id'])['checkpoint']
                resume = op['http']['body'].get('resume')
                pages = core_pages(c, actual_cp, resume)
                put({'id': ident, 'case_id': case['id'], 'variant_id': variant['id'], 'mode': case['evidence_mode'],
                    'fixture': f, 'checkpoint_creation': fault_cp_op, 'operations': [op], 'injection': injection, 'pages': pages,
                    'absence': {} if resume else {'resume': {'expected_count': 0, 'observed_new_count': len(op['after']['resume_results']) - len(op['before']['resume_results']),
                                                           'reason': 'Core integrity rejection; no successful Resume ID exists'}},
                    'runtime_label': ident})
        old_calls = client.calls
        _, client = start_core(owned, archive, runtime, env, 'base-core-restarted', evidence)
        client.calls = old_calls
        clients[0] = client
        # Missing IDs and missing scopes are read refusals, never invented chains.
        from jasmine_core import ids
        good_cp = normal['CP01.default']['checkpoint_creation']['http']['body']['checkpoint']
        good_resume = normal['CP01.default']['operations'][0]['http']['body']['resume']
        for case in matrix['cases'][22:]:
            for variant in case['variants']:
                ident = case['id'] + '.' + variant['id']
                row = {'id': ident, 'case_id': case['id'], 'variant_id': variant['id'], 'mode': case['evidence_mode'],
                       'fixture': normal['CP01.default']['fixture'], 'operations': [], 'absence': {}, 'pages': [], 'runtime_label': 'base'}
                if case['id'] == 'CP23':
                    kind = variant['id']
                    target = ids.new_id('ckp' if kind == 'checkpoint' else 'rms')
                    path = '/v1/' + ('checkpoints/' if kind == 'checkpoint' else 'resumes/') + target
                    op = client.operation(ident, 'GET', path)
                    row['operations'] = [op]
                    row['pages'] = [{'kind': 'error', 'hash': '#/checkpoints?checkpoint_id=' + target if kind == 'checkpoint' else '#/resume/' + target,
                                     'path': path, 'expected': op['http']['body'], 'entity_id': target, 'expected_status': 404}]
                    row['absence']['target'] = {'id': target, 'expected': None, 'error_readback': op['http'], 'reason': 'Legitimate ID absent in private DB'}
                elif case['id'] == 'CP24':
                    kind = variant['id']
                    target = 'invalid-' + kind
                    row['pages'] = [{'kind': 'invalid', 'hash': '#/checkpoints?checkpoint_id=' + target if kind == 'checkpoint' else '#/resume/' + target,
                                     'path': '/v1/' + ('checkpoints/' if kind == 'checkpoint' else 'resumes/') + target,
                                     'expected': None, 'entity_id': target}]
                    row['absence']['core_request'] = {'expected_count': 0, 'reason': 'Local syntax refusal; no Core response or audit expected'}
                elif case['id'] == 'CP25':
                    key = variant['id']
                    kind = variant['endpoint']
                    value = good_cp if kind == 'checkpoint' else good_resume
                    path = '/v1/' + ('checkpoints/' if kind == 'checkpoint' else 'resumes/') + value[kind + '_id']
                    op = client.operation(ident, 'GET', path, key=key)
                    row.update(operations=[op], browser_key=key, missing_scope=variant['missing_scope'])
                    row['pages'] = [{'kind': 'error', 'hash': '#/checkpoints?checkpoint_id=' + good_cp['checkpoint_id'] if kind == 'checkpoint' else '#/resume/' + good_resume['resume_id'],
                                     'path': path, 'expected': op['http']['body'], 'entity_id': value[kind + '_id'], 'expected_status': 403}]
                    row['absence']['authorized_result'] = {'reason': 'Read scope refusal; target exists only in authorized fixture readback', 'expected': None}
                    if variant['missing_scope'] == 'objects:read':
                        signin = client.request('GET', '/v1/projects?limit=1&offset=0', key=key, phase='signin-refusal-precondition')
                        row['pages'][0].update(kind='signin-error', path=signin['path'], expected=signin['body'], expected_status=403)
                        row['absence']['target_browser_request'] = {'expected_count': 0, 'target_path': path,
                            'reason': 'Real authenticated sign-in GET refuses objects:read before a session exists; direct target GET refusal is separately observed'}
                else:
                    if variant['id'] == 'p3_off_get':
                        row['pages'] = [{'kind': 'pending', 'hash': '#/checkpoints?checkpoint_id=' + good_cp['checkpoint_id'],
                                         'path': '/v1/checkpoints/' + good_cp['checkpoint_id'], 'expected': None, 'entity_id': good_cp['checkpoint_id']}]
                        row['dashboard_off'] = True
                    else:
                        row['pages'] = core_pages(client, good_cp)
                        cp_post = normal['CP01.default']['checkpoint_creation']['http']['request_body']
                        resume_post = normal['CP01.default']['operations'][0]['http']['request_body']
                        row['proxy_probe'] = {'path': '/v1/checkpoints' if variant['id'] == 'checkpoint_post' else '/v1/tasks/' + good_cp['task_id'] + '/resume',
                                              'body': cp_post if variant['id'] == 'checkpoint_post' else resume_post}
                    row['absence']['core_request'] = {'expected_count': 0, 'reason': 'Proxy refusal; no forwarded target request/audit expected'}
                put(row)

        ui_by_runtime = {}
        for c in clients:
            label = c.runtime.name
            ui_by_runtime[label] = start_dashboard(owned, archive, c.core, env, label + '-dashboard')
        ui_off = start_dashboard(owned, archive, client.core, env, 'base-dashboard-off', enabled=False)
        for row in report['records']:
            label = row.get('runtime_label', 'base')
            c = next(c for c in clients if c.runtime.name == label)
            add_lineage(c, row)
            browser_cfg.append({'id': row['id'], 'ui': ui_off if row.get('dashboard_off') else ui_by_runtime[label],
                'core': c.core, 'database': str(c.database), 'token': c.credentials['keys'][row.get('browser_key', 'browser')]['token'],
                'key_metadata': {k: v for k, v in c.credentials['keys'][row.get('browser_key', 'browser')].items() if k != 'token'},
                'admin_token': c.credentials['keys']['admin']['token'], 'pages': row['pages'], 'switches': row.get('switches'),
                'proxy_probe': row.get('proxy_probe'), 'observer_path': str(c.runtime / 'observer.jsonl')})
        for c in clients:
            write(out / (c.runtime.name + '-before-browser.json'), snapshot(c.database))
            backup(c.database, out / (c.runtime.name + '-before-browser.db'))
        profile = private / 'browser-profile'
        profile.mkdir()
        cfg = {'out': str(out), 'profile': str(profile), 'python': sys.executable,
               'snapshot_script': str(archive / 'scripts/p6/checkpoint_negatives.py'), 'cases': browser_cfg}
        browser = owned.start('browser', ['node', str(archive / 'scripts/p6/checkpoint_negatives.mjs')], archive, env,
                              stdin=json.dumps(cfg).encode())
        try:
            browser.wait(timeout=480)
        except subprocess.TimeoutExpired:
            write(out / 'browser-timeout.json', {'status': 'BLOCKED', 'pid': browser.pid, 'owned_pgid': browser.pid, 'profile': str(profile)})
            raise RuntimeError('owned browser timed out; durable progress/logs retained')
        browser_result = json.loads((out / 'browser.json').read_text())
        report['browser'] = browser_result
        report['browser_database_invariants'] = []
        for c in clients:
            previous = json.loads((out / (c.runtime.name + '-before-browser.json')).read_text())
            current = snapshot(c.database)
            write(out / (c.runtime.name + '-after-browser.json'), current)
            backup(c.database, out / (c.runtime.name + '-after-browser.db'))
            report['browser_database_invariants'].append({'runtime': c.runtime.name, 'before': previous, 'after': current})
            write(out / (c.runtime.name + '-http-calls.json'), c.calls)
            write(out / (c.runtime.name + '-observer.json'), lines(c.runtime / 'observer.jsonl'))
        # Observer windows include failures and attempts; zero cannot mean "no success log".
        report['observer_method'] = 'CPython scan_workspace function entry/exit profiler plus subprocess.Popen audit hook; complete request windows; scanner unchanged'
        report['owned_ui_flags'] = {'P6_ENABLE_WEB_CONFIRMATIONS': '0', 'P6_ENABLE_P3_READS': '1 except owned off-gate instance',
                                    'P6_ENABLE_P4_READS': '0', 'P6_ENABLE_P4_WRITES': '0', 'P6_ENABLE_P7_READS': '0'}
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        report['cleanup'] = owned.close()
        for c in clients:
            write(out / (c.runtime.name + '-http-calls.json'), c.calls)
            if (c.runtime / 'observer.jsonl').exists():
                write(out / (c.runtime.name + '-observer.json'), lines(c.runtime / 'observer.jsonl'))
            if c.database.exists():
                backup(c.database, out / (c.runtime.name + '-final.db'))
                write(out / (c.runtime.name + '-final-state.json'), snapshot(c.database))
        profile_removed = False
        if all(r.get('group_gone') for r in report['cleanup']):
            if profile and profile.exists():
                shutil.rmtree(profile)
            profile_removed = profile is None or not profile.exists()
            shutil.rmtree(private)
        report['profile_removed'] = profile_removed
        report['private_runtime_removed'] = not private.exists()
        receipt_files = [{'path': p.relative_to(out).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p.read_bytes())}
                         for p in sorted(out.rglob('*')) if p.is_file() and p.name not in ('raw-result.json', 'result.json', 'manifest.json', 'receipt-index.json', 'harness-selfchecks.json')]
        write(out / 'receipt-index.json', {'kind': 'RAW_COLLECTION_FILE_INDEX', 'files': receipt_files})
        report['receipt_index_sha256'] = digest((out / 'receipt-index.json').read_bytes())
        write(out / 'raw-result.json', report)
        matrix_path = out / 'matrix.json'
        if matrix_path.exists():
            matrix = json.loads(matrix_path.read_text())
            verdict = evaluate_bundle(report, matrix, expected_candidate=candidate, expected_archive=archive_sha, evidence_directory=out)
            checks = adversarial_bundle_checks(report, matrix, candidate, archive_sha, evidence_directory=out)
            write(out / 'harness-selfchecks.json', checks)
            verdict['harness_selfchecks'] = checks['status']
            if verdict['status'] == 'PASS' and checks['status'] != 'PASS':
                verdict['status'] = 'FAIL'
        else:
            verdict = {'status': 'BLOCKED', 'reason': 'matrix unavailable', 'records': []}
        write(out / 'result.json', verdict)
        manifest = [{'path': p.relative_to(out).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p.read_bytes())}
                    for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'manifest.json']
        write(out / 'manifest.json', {'candidate_commit': candidate, 'source_archive_sha256': report.get('source_archive_sha256'),
              'spec_commit': SPEC_COMMIT, 'role': args.role, 'verdict': verdict['status'], 'files': manifest})
        print(json.dumps({'status': verdict['status'], 'variants': len(verdict.get('records', [])), 'out': str(out), 'error': report.get('error')}), flush=True)
    return 0 if verdict['status'] == 'PASS' else 1


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='mode', required=True)
    s = sub.add_parser('serve')
    s.add_argument('--archive', type=Path, required=True)
    s.add_argument('--runtime', type=Path, required=True)
    r = sub.add_parser('run')
    r.add_argument('--ref', required=True)
    r.add_argument('--out', type=Path, required=True)
    r.add_argument('--role', choices=('author-implementation-check', 'independent-validation'), default='author-implementation-check')
    q = sub.add_parser('snapshot')
    q.add_argument('--db', type=Path, required=True)
    q = sub.add_parser('observer')
    q.add_argument('--path', type=Path, required=True)
    args = p.parse_args()
    if args.mode == 'serve':
        serve(args)
        return 0
    if args.mode == 'snapshot':
        print(canonical(snapshot(args.db)))
        return 0
    if args.mode == 'observer':
        print(canonical(lines(args.path)))
        return 0
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
