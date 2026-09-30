"""Native observations require complete independent sources and immutable links."""
import copy
import hashlib
import json
import shlex

import test_evidence
from test_evidence import SYSTEM
from test_api import HOST
from jasmine_core import auth, db, ids, registry


class CodexObservationHttp(test_evidence.EvidenceHttp):
    def observation(self, exit_code=0):
        self.transition('IN_PROGRESS', 1)
        conn = db.connect(self.db_path)
        try:
            token = auth.Auth(conn).issue_key(actor_id=SYSTEM, label='observer',
                scopes=['evidence:attest'])['token']
        finally:
            conn.close()
        self.observer_token = token
        code, prompt = self.call('POST', '/v1/events', {
            'event_type': 'user.prompt', 'source_system': 'codex-p1-bound',
            'host_id': HOST, 'project_id': self.project, 'task_id': self.task,
            'payload': {'text': 'run true', 'step_id': self.step,
                        'source_session_id': 'native-session', 'turn_id': 'native-turn'}},
            token=self.human_token)
        self.assertEqual(code, 201, prompt)
        code, post = self.call('POST', '/v1/tool-results', {
            'task_id': self.task, 'step_id': self.step, 'host_id': HOST,
            'codex_session_id': 'native-session', 'turn_id': 'native-turn',
            'tool_use_id': 'native-use', 'tool_name': 'Bash',
            'tool_input': {'command': 'true'}, 'tool_response': {'raw_response': ''}},
            token=self.system_token)
        self.assertEqual(code, 201, post)
        self.original = post
        command = '/bin/bash -c ' + shlex.quote('true')
        cli = [{'type': 'thread.started', 'thread_id': 'native-session'},
               {'type': 'turn.started'},
               {'type': 'item.started', 'item': {'id': 'item-1', 'type': 'command_execution',
                  'command': command, 'status': 'in_progress'}},
               {'type': 'item.completed', 'item': {'id': 'item-1', 'type': 'command_execution',
                  'command': command, 'status': 'completed', 'exit_code': exit_code}},
               {'type': 'turn.completed'}]
        input_sha = hashlib.sha256(b'{"command":"true"}').hexdigest()
        trace = [dict(hook_event_name='UserPromptSubmit', result='captured',
                      event_id=prompt['event']['event_id']),
                 dict(hook_event_name='PreToolUse', result='guard:verify',
                      tool_use_id='native-use', tool_input_sha256=input_sha),
                 dict(hook_event_name='PostToolUse', result='captured',
                      event_id=post['result_event']['event_id'], tool_use_id='native-use',
                      tool_input_sha256=input_sha)]
        for item in trace:
            item.update(session_id='native-session', turn_id='native-turn')
        body = dict(task_id=self.task, step_id=self.step, host_id=HOST,
                    origin_prompt_event_id=prompt['event']['event_id'],
                    related_posttool_event_id=post['result_event']['event_id'],
                    codex_executable=dict(path='/bin/codex-test', version='test', sha256='a'*64))
        for field, lines in [('codex_jsonl', cli), ('hook_trace_jsonl', trace)]:
            body[field] = ''.join(json.dumps(item) + '\n' for item in lines)
            body[('hook_trace_sha256' if field == 'hook_trace_jsonl' else field+'_sha256')] = hashlib.sha256(body[field].encode()).hexdigest()
        return body

    def observe(self, body):
        return self.call('POST', '/v1/codex-exec-observations', body,
                         token=self.observer_token)

    def test_native_pass_verified_replay_and_original_info(self):
        body = self.observation()
        code, result = self.observe(body)
        self.assertEqual(code, 201, result)
        self.assertEqual(result['evidence']['status'], 'PASS')
        self.assertEqual(self.observe(body), (200, {**result, 'replayed': True}))
        conn = db.connect(self.db_path)
        try:
            other = ids.new_id('act')
            with db.transaction(conn):
                registry.Registry(conn).upsert_actor(other, kind='system', home_host_id=HOST)
            token = auth.Auth(conn).issue_key(actor_id=other, label='other',
                scopes=['evidence:attest'])['token']
        finally:
            conn.close()
        self.assertEqual(self.call('POST', '/v1/codex-exec-observations', body, token=token)[0], 409)
        changed = copy.deepcopy(body)
        changed['codex_executable']['version'] = 'different'
        self.assertEqual(self.observe(changed)[0], 409)
        code, original = self.call('GET', '/v1/evidence/'+self.original['evidence']['evidence_id'],
                                   token=self.system_token)
        self.assertEqual(original['evidence']['status'], 'INFO')
        self.transition('EXECUTED', 2)
        code, verified = self.transition('VERIFIED', 3)
        self.assertEqual(code, 200, verified)
        self.assertEqual(verified['evidence_ids'], [result['evidence']['evidence_id']])

    def test_invalid_sources_leave_no_native_result(self):
        body = self.observation()
        for field, mutate in [
            ('codex_jsonl', lambda xs: xs[3]['item'].update(exit_code=True)),
            ('codex_jsonl', lambda xs: xs.insert(3, copy.deepcopy(xs[2]))),
            ('codex_jsonl', lambda xs: xs.pop()),
            ('codex_jsonl', lambda xs: xs.append({'type': 'error', 'message': 'oops'})),
            ('codex_jsonl', lambda xs: xs.insert(3, {'type': 'item.started', 'item': 'command_execution'})),
            ('codex_jsonl', lambda xs: xs[3]['item'].update(exit_code='0')),
            ('codex_jsonl', lambda xs: xs[0].update(thread_id='other')),
            ('codex_jsonl', lambda xs: xs[3]['item'].update(exit_code=0.0)),
            ('codex_jsonl', lambda xs: xs[3]['item'].pop('exit_code')),
            ('codex_jsonl', lambda xs: xs.pop(2)),
            ('codex_jsonl', lambda xs: xs.insert(4, copy.deepcopy(xs[3]))),
            ('codex_jsonl', lambda xs: [x['item'].update(command='/bin/bash -c true extra') for x in xs if 'item' in x]),
            ('codex_jsonl', lambda xs: [x['item'].update(command='/bin/bash -c false') for x in xs if 'item' in x]),
            ('hook_trace_jsonl', lambda xs: xs.insert(1, dict(xs[1], result='guard:deny'))),
            ('hook_trace_jsonl', lambda xs: xs[2].update(tool_use_id='other'))]:
            bad = copy.deepcopy(body)
            lines = [json.loads(line) for line in bad[field].splitlines()]
            mutate(lines)
            bad[field] = ''.join(json.dumps(item)+'\n' for item in lines)
            bad[('hook_trace_sha256' if field == 'hook_trace_jsonl' else field+'_sha256')] = hashlib.sha256(bad[field].encode()).hexdigest()
            code, result = self.observe(bad)
            self.assertEqual(code, 400, result)
        self.assertEqual(self.observe(body)[0], 201)

    def test_nonzero_native_failure_and_changed_workspace(self):
        body = self.observation(7)
        (self.root / 'source.txt').write_text('changed')
        self.assertEqual(self.observe(body)[0], 422)
        (self.root / 'source.txt').write_text('initial\n')
        code, result = self.observe(body)
        self.assertEqual(code, 201, result)
        self.assertEqual(result['evidence']['status'], 'FAIL')
        self.transition('EXECUTED', 2)
        self.assertEqual(self.transition('VERIFIED', 3)[0], 422)
