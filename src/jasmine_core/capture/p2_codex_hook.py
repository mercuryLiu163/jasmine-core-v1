"""Synchronous admission-only capture. Platform hook failure is not a tool barrier."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
import stat
from pathlib import Path
from .. import ids
from ..canonical import canonical_json
from .codex_user_prompt_submit import derive_event_id
from .p1_codex_hook import _private_file, _token, _deny
from .p2_runtime import Deadline, DeadlineClient, Lease, atomic_json
from .p2_snapshot import coherent_snapshot


def _trace(config, payload, result, *, event_id=None):
    """Nonblocking diagnostic output never changes the admission decision."""
    fd = None
    try:
        if config is None:
            return False
        path = Path(config['trace_file'])
        if path.parent != path.parent.resolve(strict=True):
            return False
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            return False
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return False
        line = {'session_id': payload.get('session_id'), 'turn_id': payload.get('turn_id'),
                'hook_event_name': payload.get('hook_event_name'), 'current_trigger_turn_id': payload.get('current_trigger_turn_id'), 'result': result, 'event_id': event_id}
        raw = (canonical_json(line) + '\n').encode()
        if len(raw) > 2048:
            return False
        os.write(fd, raw)
        return True
    except Exception:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _block(reason, state=None):
    refs = {key: state.get(key) for key in ('event_id', 'interpretation_id', 'resolution_id', 'review_id', 'phase')} if state else {}
    return {'decision': 'block', 'reason': 'Jasmine P2 admission pending: ' + reason[:120] + ' ' + canonical_json(refs)}


def binding(path, payload):
    value = json.loads(_private_file(path))
    allowed = {'mode', 'run_nonce', 'project_id', 'host_id', 'core_url', 'token_file', 'human_token_file', 'trace_file'}
    if not isinstance(value, dict) or set(value) != allowed or value['mode'] != 2:
        raise ValueError('invalid P2 binding')
    nonce = value['run_nonce']
    if not isinstance(nonce, str) or len(nonce) < 32:
        raise ValueError('invalid nonce')
    if os.environ.get('JASMINE_CORE_GATE_NONCE') != nonce:
        return None
    for key, prefix in (('project_id', 'prj'), ('host_id', 'hst')):
        if not ids.is_id(value[key], prefix):
            raise ValueError('invalid binding ID')
    if not isinstance(value['core_url'], str) or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', value['core_url']):
        raise ValueError('loopback required')
    for key in ('session_id', 'turn_id'):
        if not isinstance(payload.get(key), str) or not 1 <= len(payload[key]) <= 256:
            raise ValueError('real session and turn required')
    return value


def _save_request(state, name, method, path, body, lease):
    if name not in state['requests']:
        state['requests'][name] = {'method': method, 'path': path, 'body': body}
        lease.write(state)
    return state['requests'][name]


def _call(state, name, client, lease, method, path, body=None, cap=3):
    saved = _save_request(state, name, method, path, body, lease)
    return client.request(saved['method'], saved['path'], saved['body'], cap=cap)


def _capture(state, config, payload, lease, deadline):
    capture = DeadlineClient(config['core_url'], _token(config, 'human_token_file'), deadline)
    raw_body = {'event_type': 'user.prompt', 'source_system': 'codex-p2-bound',
        'source_event_id': canonical_json([state['session_id'], state['turn_id']]),
        'event_id': state['event_id'], 'host_id': config['host_id'], 'project_id': config['project_id'],
        'task_id': state.get('task_id'), 'payload': {'text': payload['prompt'],
        'step_id': state.get('step_id'), 'source_session_id': state['session_id'], 'turn_id': state['turn_id']}}
    _call(state, 'raw', capture, lease, 'POST', '/v1/events', raw_body)
    state['phase'] = 'CAPTURED'
    lease.write(state)
    _trace(config, payload, 'p2:CAPTURED', event_id=state['event_id'])


def _admit(state, config, payload, lease, deadline):
    reader = DeadlineClient(config['core_url'], _token(config, 'token_file'), deadline)
    interpreted = _call(state, 'interpret', reader, lease, 'POST', '/v1/interpret',
        {'event_id': state['event_id'], 'idempotency_key': 'p2-int:' + state['event_id']}, cap=125)['interpretation']
    parent_id = interpreted['interpretation_id']
    current = reader.get('/v1/interpretations/' + parent_id)['interpretation']
    head = current['maintenance_head']
    if head['status'] == 'REJECTED':
        state.update(phase='REJECTED', interpretation_id=parent_id)
        lease.write(state)
        return _block('interpretation rejected; no Truth retraction', state)
    if head['status'] == 'RERUN_PENDING':
        state.update(phase='RERUN_PENDING', interpretation_id=parent_id)
        lease.write(state)
        return _block('rerun still pending', state)
    if head['current_interpretation_id'] != parent_id:
        interpreted = reader.get('/v1/interpretations/' + head['current_interpretation_id'])['interpretation']
    state['interpretation_id'] = interpreted['interpretation_id']
    state['phase'] = interpreted['status']
    lease.write(state)
    if interpreted['status'] != 'EXTRACTED':
        return _block(interpreted['status'], state)
    ident = state['interpretation_id']
    resolve_name = 'resolve:' + ident
    if resolve_name not in state['requests']:
        preview = reader.get('/v1/resolutions/preview?interpretation_id=' + ident)
        body = {'idempotency_key': 'p2-res:' + ident, 'host_id': config['host_id'],
            'policy_version': preview['policy_version'], 'expected_revisions': preview['expected_revisions'],
            'expected_context_digest': preview['expected_context_digest']}
    else:
        body = state['requests'][resolve_name]['body']
    resolved = _call(state, resolve_name, reader, lease, 'POST', '/v1/resolve/' + ident, body)['resolution']
    state['resolution_id'] = resolved['resolution_id']
    result = resolved['result']
    state['phase'] = result['disposition']
    state['review_id'] = result['review_id']
    lease.write(state)
    if result['disposition'] == 'PENDING_REVIEW':
        review = reader.get('/v1/reviews/' + result['review_id'])['review']
        if review['status'] == 'REJECTED':
            state['phase'] = 'REJECTED'
            lease.write(state)
            return _block('review rejected', state)
        if review['status'] not in ('APPROVED', 'SOURCE_REPLAYED'):
            return _block('review required', state)
        # The immutable successful approval snapshot supplies applied actions.
        changes = review['history']
        applied = [row['result'] for row in changes if row['action'] == 'APPROVE' and row['result']['disposition'] in ('APPLIED', 'SOURCE_REPLAYED')]
        if not applied:
            raise ValueError('approved application snapshot unavailable')
        result = applied[-1]
    if result['disposition'] not in ('APPLIED', 'SOURCE_REPLAYED', 'NO_ACTION'):
        return _block('application not ready', state)
    if not state.get('task_id'):
        targets = {action['target_id'] for action in result['actions'] if action['target_type'] == 'task'}
        if len(targets) != 1:
            return _block('no unique derived Task', state)
        state['task_id'] = targets.pop()
        # Binding must be a genuine interpretation.bound Event referencing this source.
        binding_ids = {action['binding_event_id'] for action in result['actions']
                       if action['target_type'] == 'task' and action['target_id'] == state['task_id'] and action['binding_event_id']}
        if len(binding_ids) != 1:
            raise ValueError('derived Task binding unavailable')
        event = reader.get('/v1/events/' + binding_ids.pop())['event']
        canonical_id = result.get('canonical_resolution_id') or resolved['resolution_id']
        canonical = reader.get('/v1/resolutions/' + canonical_id)['resolution']
        applied_snapshot = canonical['result']
        if applied_snapshot['disposition'] == 'PENDING_REVIEW':
            approved = reader.get('/v1/reviews/' + applied_snapshot['review_id'])['review']
            applied = [row['result'] for row in approved['history'] if row['action'] == 'APPROVE' and row['result']['disposition'] == 'APPLIED']
            if len(applied) != 1:
                raise ValueError('canonical approved snapshot unavailable')
            applied_snapshot = applied[0]
        expected_actor = applied_snapshot.get('review_actor_id') or applied_snapshot.get('execution_actor_id')
        if (event['event_type'] != 'interpretation.bound' or event['task_id'] != state['task_id'] or
                event['project_id'] != config['project_id'] or event['actor_kind'] not in ('human', 'system') or event['actor_id'] != expected_actor or
                event['host_id'] != config['host_id'] or event['payload']['source_event_id'] != state['event_id'] or
                event['payload']['resolution_id'] != canonical_id or
                event['payload']['interpretation_id'] != canonical['interpretation_id']):
            raise ValueError('invalid derived source binding')
        state['binding_event_id'] = event['event_id']
        lease.write(state)
    if not state.get('step_id'):
        state['phase'] = 'STEP_BINDING_PENDING'
        lease.write(state)
        task = reader.get('/v1/tasks/' + state['task_id'])['task']
        step_event = derive_event_id(config['host_id'], state['session_id'], state['turn_id'], 'p2-step:' + state['event_id'])
        step_body = {'title': 'Captured task execution', 'description': 'P2 bookkeeping source ' + state['event_id'],
            'acceptance_criteria': {'requirements': []}, 'expected_revision': task['revision'],
            'host_id': config['host_id'], 'event_id': step_event}
        step = _call(state, 'step', reader, lease, 'POST', '/v1/tasks/' + state['task_id'] + '/steps', step_body)['step']
        if step['task_id'] != state['task_id'] or step['status'] != 'PLANNED':
            raise ValueError('bookkeeping Step linkage invalid')
        state['step_id'] = step['step_id']
        lease.write(state)
    refs = {key: state.get(key) for key in ('event_id', 'interpretation_id', 'resolution_id', 'review_id', 'binding_event_id')}
    refs['project_id'] = config['project_id']
    text, snapshot = coherent_snapshot(reader, ident, state['task_id'], state['step_id'], refs)
    state.update(phase='READY', context_text=text, context_sha256=hashlib.sha256(text.encode()).hexdigest(), ready_deadline_monotonic=deadline.end, context_digest=snapshot['context_digest'], expected_revisions=snapshot['expected_revisions'])
    lease.write(state)
    _trace(config, payload, 'p2:READY', event_id=state['event_id'])
    deadline.remaining()
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}


def handle(payload, path):
    event = payload.get('hook_event_name')
    if event not in ('UserPromptSubmit', 'PreToolUse', 'PostToolUse'):
        return {}
    deadline = Deadline(140 if event == 'UserPromptSubmit' else 8)
    state, config = None, None
    try:
        config = binding(path, payload)
        if config is None:
            return {}
        lease = Lease(path, deadline)
        with lease.locked():
            state = lease.read()
            if state and state['session_id'] != payload['session_id']:
                raise ValueError('session lease mismatch')
            if event != 'UserPromptSubmit':
                # P2 main is execution-free; P1 mapping remains available only in legacy mode.
                if event == 'PreToolUse':
                    return _deny('Jasmine P2 admission-only profile does not execute tools')
                _trace(config, payload, 'p2:unsupported-producer')
                return {}
            prompt = payload.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 32768:
                raise ValueError('invalid real prompt')
            hashed = hashlib.sha256(prompt.encode()).hexdigest()
            if state and state['turn_id'] == payload['turn_id']:
                if state['prompt_sha256'] != hashed:
                    raise ValueError('same turn changed prompt')
            else:
                previous = state
                state = {'phase': 'PENDING_CAPTURE', 'session_id': payload['session_id'], 'turn_id': payload['turn_id'],
                    'prompt_sha256': hashed, 'generation': (previous['generation'] + 1 if previous else 1),
                    'event_id': derive_event_id(config['host_id'], payload['session_id'], payload['turn_id'], 'p2-bound:' + prompt),
                    'task_id': previous.get('task_id') if previous else None, 'step_id': previous.get('step_id') if previous else None,
                    'requests': {}, 'previous_ref': None}
                if previous and previous['phase'] not in ('READY', 'CAPTURED_UNBOUND_BLOCKED', 'REJECTED'):
                    if previous.get('previous_ref'):
                        state['previous_ref'] = previous['previous_ref']
                    else:
                        archive = path.with_name(path.name + '.operation-' + previous['event_id'])
                        atomic_json(archive, previous)
                        state['previous_ref'] = str(archive)
                # Durable invalidation occurs before token read or any HTTP operation.
                lease.write(state)
            # Always commit this genuine current Raw before any prior recovery.
            _capture(state, config, payload, lease, deadline)
            if state.get('previous_ref'):
                archive = Path(state['previous_ref'])
                if archive.parent != path.parent or not archive.name.startswith(path.name + '.operation-'):
                    raise ValueError('invalid operation archive')
                prior = json.loads(_private_file(archive))
                raw = prior['requests'].get('raw')
                if raw is None:
                    raise ValueError('previous capture requires recovery')
                proxy = _PreviousLease(archive, deadline)
                recovery_payload={'hook_event_name': 'P2Recovery', 'session_id': prior['session_id'], 'turn_id': prior['turn_id'], 'current_trigger_turn_id': payload['turn_id'], 'prompt': raw['body']['payload']['text']}
                _capture(prior, config, recovery_payload, proxy, deadline)
                _admit(prior, config, recovery_payload, proxy, deadline)
                if prior['phase'] == 'REJECTED':
                    state['previous_ref'] = None
                    lease.write(state)
                elif prior['phase'] != 'READY':
                    return _block('previous operation pending', state)
                # Current Raw envelope is immutable. A recovered Task cannot be
                # retroactively attached to an already captured task-null source.
                if prior['phase'] == 'READY' and not state.get('task_id'):
                    state.update(phase='CAPTURED_UNBOUND_BLOCKED', captured_unbound_blocked=True, task_id=prior['task_id'], step_id=prior['step_id'], previous_ref=None)
                    lease.write(state)
                    return _block('next real turn must use recovered Task binding', state)
                state['previous_ref'] = None
                lease.write(state)
            if state.get('captured_unbound_blocked'):
                return _block('captured source is unbound; submit next real turn', state)
            return _admit(state, config, payload, lease, deadline)
    except Exception as error:
        _trace(config, payload, 'p2:error:' + type(error).__name__, event_id=state.get('event_id') if state else None)
        if event == 'PreToolUse':
            return _deny('Jasmine admission unavailable')
        if event == 'UserPromptSubmit' and os.environ.get('JASMINE_CORE_GATE_NONCE'):
            return _block(type(error).__name__, state)
        return {}

class _PreviousLease:
    def __init__(self, archive, deadline):
        self.archive, self.deadline = archive, deadline
    def write(self, prior):
        self.deadline.remaining()
        atomic_json(self.archive, prior)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--binding', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, OSError):
        payload = {'hook_event_name': 'UserPromptSubmit'}
    print(canonical_json(handle(payload, args.binding)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
