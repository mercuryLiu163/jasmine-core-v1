#!/usr/bin/env python3
"""Drive one Task to a real ``task.accepted`` acceptance, for the P6 evidence.

The Evidence panel on the Task page claims to show what Core recorded as the
gate decision. Core writes that on `step.transitioned` (to VERIFIED/ACCEPTED)
*and* on `task.accepted`, so this fixture produces the second kind through the
frozen API rather than a synthetic browser response.

Usage (against a scratch Core started by ``p6_scratch.py serve``)::

    python3 scripts/p6/p6_accept_fixture.py /private/tmp/p6-scratch-accept 8899

It prints the task/step, the `task.accepted` event id, and the evidence ids Core
attached to the acceptance, which the browser check then compares against what
the UI renders.

Notes on the frozen rules this sequence has to respect (ADR 0006/0007):

* a plain human confirmation is recorded as ``INFO``, so the requirement asks for
  ``INFO``; a ``PASS`` requirement would need a confirmed Rule version;
* an ``ACCEPTED`` transition needs a confirmation at *its* expected revision, so
  the Step is confirmed twice (once before VERIFIED, once after);
* an old prompt cannot be reused, so each confirmation gets a fresh same-task
  human ``user.prompt`` that postdates the current State Event;
* Task acceptance needs a task-scoped confirmation at the Task revision the
  accept call uses.
"""
import json, sys, urllib.request, urllib.error

DB = sys.argv[1]
PORT = int(sys.argv[2])
desc = json.load(open(f'{DB}/descriptor.json'))
tokens = json.load(open(f'{DB}/tokens.json'))
host = desc['host_id']
project = desc['seeded']['projects'][0]

def call(method, path, body=None, role='human', expect=None):
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tokens[role]})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status, payload = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        status, payload = e.code, json.loads(e.read())
    if expect is not None and status != expect:
        print(f'FAILED {method} {path} -> {status} {json.dumps(payload)[:300]}', file=sys.stderr)
        sys.exit(1)
    return payload

# A plain human confirmation is recorded as INFO (ADR 0007); a PASS-level
# USER_CONFIRMATION requirement would need a confirmed Rule version instead, so
# the fixture asks for what the frozen API actually produces.
criteria = {'requirements': [{'key': 'confirm', 'kind': 'USER_CONFIRMATION', 'required_result': 'INFO'}]}

task = call('POST', '/v1/tasks', {
    'title': 'T07 accepted task (real task.accepted)',
    'project_id': project, 'host_id': host, 'acceptance_criteria': criteria}, expect=201)['object']
rev = call('GET', f"/v1/tasks/{task['task_id']}", expect=200)['task']['revision']

step = call('POST', f"/v1/tasks/{task['task_id']}/steps", {
    'title': 'Confirm and accept', 'host_id': host, 'expected_revision': rev,
    'acceptance_criteria': criteria}, expect=201)['step']

call('POST', f"/v1/steps/{step['step_id']}/transition",
     {'status': 'IN_PROGRESS', 'expected_revision': 1, 'host_id': host}, role='agent', expect=200)
call('POST', f"/v1/steps/{step['step_id']}/transition",
     {'status': 'EXECUTED', 'expected_revision': 2, 'host_id': host}, role='agent', expect=200)

prompt = call('POST', '/v1/events', {
    'event_type': 'user.prompt', 'source_system': 'p6-accept-fixture',
    'host_id': host, 'project_id': project, 'task_id': task['task_id'],
    'payload': {'text': 'I confirm this step and accept the task.', 'step_id': step['step_id']}},
    expect=201)['event']

current = call('GET', f"/v1/steps/{step['step_id']}", expect=200)['step']
confirmed = call('POST', '/v1/evidence/confirm', {
    'task_id': task['task_id'], 'step_id': step['step_id'], 'host_id': host,
    'origin_event_id': prompt['event_id'], 'expected_revision': current['revision']}, expect=201)

verified = call('POST', f"/v1/steps/{step['step_id']}/transition",
     {'status': 'VERIFIED', 'expected_revision': current['revision'], 'host_id': host}, expect=200)

# ADR 0007: an ACCEPTED transition needs a confirmation at *its* expected
# revision, and an old prompt cannot be reused. So confirm again after VERIFIED.
prompt2 = call('POST', '/v1/events', {
    'event_type': 'user.prompt', 'source_system': 'p6-accept-fixture',
    'host_id': host, 'project_id': project, 'task_id': task['task_id'],
    'payload': {'text': 'I accept this verified step.', 'step_id': step['step_id']}},
    expect=201)['event']
current2 = call('GET', f"/v1/steps/{step['step_id']}", expect=200)['step']
confirmed2 = call('POST', '/v1/evidence/confirm', {
    'task_id': task['task_id'], 'step_id': step['step_id'], 'host_id': host,
    'origin_event_id': prompt2['event_id'], 'expected_revision': current2['revision']}, expect=201)
accepted = call('POST', f"/v1/steps/{step['step_id']}/transition",
     {'status': 'ACCEPTED', 'expected_revision': current2['revision'], 'host_id': host}, expect=200)

# Task-level acceptance needs a task-scoped confirmation at the Task revision the
# accept call will use, from a human prompt that postdates the last state Event.
prompt3 = call('POST', '/v1/events', {
    'event_type': 'user.prompt', 'source_system': 'p6-accept-fixture',
    'host_id': host, 'project_id': project, 'task_id': task['task_id'],
    'payload': {'text': 'I accept the task as a whole.'}}, expect=201)['event']
task_now = call('GET', f"/v1/tasks/{task['task_id']}", expect=200)['task']
confirm_task = call('POST', '/v1/evidence/confirm', {
    'task_id': task['task_id'], 'host_id': host, 'origin_event_id': prompt3['event_id'],
    'expected_revision': task_now['revision']}, expect=201)

accept = call('POST', f"/v1/tasks/{task['task_id']}/accept",
              {'host_id': host, 'expected_revision': task_now['revision']}, expect=200)

history = call('GET', f"/v1/tasks/{task['task_id']}/history", expect=200)['events']
accepted_events = [e for e in history if e['event_type'] == 'task.accepted']
assert accepted_events, 'no task.accepted event was recorded'
event = accepted_events[-1]
ids = event['payload'].get('evidence_ids') or event['payload']['result'].get('evidence_ids')
assert ids, 'task.accepted carries no evidence_ids'
print(json.dumps({
    'task_id': task['task_id'], 'step_id': step['step_id'], 'task_status': accept['task']['status'],
    'accepted_event_id': event['event_id'], 'accepted_event_evidence_ids': ids,
    'step_confirmed_evidence_id': confirmed['evidence']['evidence_id'],
    'step_accepted_confirmation_id': confirmed2['evidence']['evidence_id'],
    'task_confirmation_id': confirm_task['evidence']['evidence_id'],
    'accepted_step_status': accepted['step']['status'],
}, indent=2))
