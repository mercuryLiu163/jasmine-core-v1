"""Component adversaries for absence/zero-scan evidence, not real acceptance."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import sys
import os
import hashlib
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/p6' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

gate = load('checkpoint_negative_gate')
runner = load('checkpoint_negatives')

class ZeroObservation(unittest.TestCase):
    def setUp(self):
        value = {'resume_id': 'component-only-receipt', 'comparison': {'workspace': 'UNKNOWN'}}
        state = {t: [] for t in gate.OP_TABLES}
        state['resume_results'] = [{'resume_id': value['resume_id'], 'result_json': gate.canonical(value)}]
        self.op = {'http': {'method': 'GET', 'path': '/v1/resumes/component-only-receipt', 'status': 200,
                    'request_id': 'component-request', 'actor_id': 'component-actor', 'body': {'resume': value}},
            'before': state, 'after': copy.deepcopy(state),
            'observer_started': {'kind': 'observer_started', 'pid': 17},
            'observer': [{'kind': 'request_start', 'pid': 17, 'seq': 1, 'request_id': 'component-request'},
                         {'kind': 'request_end', 'pid': 17, 'seq': 4, 'request_id': 'component-request', 'status': 200}],
            'audit_read': {'status': 200}, 'audit': [{'request_id': 'component-request', 'method': 'GET',
                'path': '/v1/resumes/component-only-receipt', 'actor_id': 'component-actor', 'status_code': 200,
                'error_code': None, 'decision': 'allow'}]}

    def test_complete_zero_entry_and_popen_observation(self):
        p = gate.operation_proof(self.op)
        self.assertTrue(p['observer_complete_and_expected_scan_count'])
        self.assertEqual(p['scanner_function_entry_count'], 0)
        self.assertEqual(p['scanner_popen_attempt_count'], 0)
        self.assertTrue(p['exact_append_or_absence'])

    def test_no_successful_popen_is_not_zero_function_calls(self):
        self.op['observer'].insert(1, {'kind': 'scanner_call_enter', 'pid': 17, 'seq': 2, 'request_id': 'component-request'})
        self.op['observer'].insert(2, {'kind': 'scanner_call_exit', 'pid': 17, 'seq': 3, 'request_id': 'component-request'})
        self.assertFalse(gate.operation_proof(self.op)['observer_complete_and_expected_scan_count'])

    def test_missing_request_end_cannot_prove_zero_scan(self):
        self.op['observer'].pop()
        self.assertFalse(gate.operation_proof(self.op)['observer_complete_and_expected_scan_count'])

    def test_refused_operation_cannot_append_a_command(self):
        self.op['http'].update(status=409, body={'error': {'code': 'checkpoint_integrity_error'}})
        self.op['after']['events'].append({'event_id': 'component-illegal-append'})
        self.assertFalse(gate.operation_proof(self.op)['exact_append_or_absence'])

    def test_immutable_read_requires_exact_stored_result(self):
        self.op['after']['resume_results'][0]['result_json'] = gate.canonical({'resume_id': 'component-only-receipt', 'changed': True})
        self.assertFalse(gate.operation_proof(self.op)['stored_result_and_request_hash'])

    def test_missing_audit_is_not_a_successful_read(self):
        self.op['audit'] = []
        self.assertFalse(gate.operation_proof(self.op)['audit_bound'])

    def test_missing_matrix_execution_still_has_all_blocked_slots(self):
        matrix = json.loads((ROOT / 'docs/validation/p6-checkpoint-negatives/acceptance-matrix.v1.json').read_text())
        result = gate.evaluate_bundle({}, matrix)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['counts'], {'PASS': 0, 'FAIL': 0, 'BLOCKED': 43})

class OwnedCleanup(unittest.TestCase):
    def test_failed_owned_process_reaps_child_and_preserves_durable_output(self):
        with tempfile.TemporaryDirectory() as d:
            owned = runner.Owned(Path(d))
            code = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); print("owned-child",p.pid,flush=True); time.sleep(60)'
            p = owned.start('component-timeout', [sys.executable, '-c', code], Path(d), dict(os.environ))
            deadline = time.monotonic() + 4
            log = Path(d) / 'component-timeout.log'
            while 'owned-child' not in log.read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.03)
            child = int(log.read_text().split()[1])
            receipts = owned.close()
            self.assertTrue(receipts[0]['group_gone'])
            self.assertIn('owned-child', log.read_text())
            self.assertNotIn(child, runner.Owned.members(p.pid))

class PublicAnonymousAudit(unittest.TestCase):
    def setUp(self):
        self.ui = {'key_metadata': {'actor_id': 'component-actor'},
            'network': [{'request_id': 'component-public', 'method': 'GET', 'path': '/v1/health', 'status': 200, 'body': {'schema_version': 12}}],
            'audit_read': {'status': 200, 'matching': [{'request_id': 'component-public', 'method': 'GET', 'path': '/v1/health', 'status_code': 200, 'error_code': None, 'actor_id': None, 'scope': None}]},
            'observer_window': [{'kind': 'request_start', 'request_id': 'component-public'},
                                {'kind': 'request_end', 'request_id': 'component-public', 'status': 200}]}

    def test_public_route_requires_actual_window_and_anonymous_audit(self):
        self.assertTrue(gate.browser_audit_proof(self.ui))
        self.ui['observer_window'].pop()
        self.assertFalse(gate.browser_audit_proof(self.ui))

    def test_public_route_cannot_hide_scan_or_missing_audit(self):
        self.ui['observer_window'].append({'kind': 'scanner_call_enter', 'request_id': 'component-public'})
        self.assertFalse(gate.browser_audit_proof(self.ui))
        self.ui['observer_window'].pop()
        self.ui['audit_read']['matching'].clear()
        self.assertFalse(gate.browser_audit_proof(self.ui))

    def test_nonpublic_route_does_not_inherit_public_exception(self):
        self.ui['network'][0]['path'] = '/v1/projects'
        self.assertFalse(gate.browser_audit_proof(self.ui))

class ActionDisplayAdversaries(unittest.TestCase):
    def test_empty_actions_cannot_claim_restoration_or_hide_extra_rows(self):
        dom = {'next_actions': None, 'next_action_header': None, 'next_action_rows': [], 'jsonblocks': [{'label': 'next_actions', 'text': '[]'}]}
        self.assertTrue(gate.actions_proof(dom, []))
        for field, value in [('next_actions', 'RESTORE_OLD_TRUTH 已执行成功'), ('next_action_rows', [{'text': 'RESTORE_OLD_TRUTH 已执行成功', 'codes': ['RESTORE_OLD_TRUTH'], 'fields': []}])]:
            with self.subTest(field=field):
                bad = copy.deepcopy(dom); bad[field] = value
                self.assertFalse(gate.actions_proof(bad, []))

    def test_exact_step_subject_status_count_order_and_no_execution_claim(self):
        actions = [{'kind': 'VERIFY_EVIDENCE', 'step_id': 'stp_COMPONENT_A', 'status': 'EXECUTED'}, {'kind': 'INSPECT_STEP', 'step_id': 'stp_COMPONENT_B', 'status': 'BLOCKED'}]
        rows = [{'text': 'VERIFY_EVIDENCE step_id = stp_COMPONENT_A, status = EXECUTED', 'codes': ['VERIFY_EVIDENCE'], 'fields': [' step_id = stp_COMPONENT_A, status = EXECUTED']},
                {'text': 'INSPECT_STEP step_id = stp_COMPONENT_B, status = BLOCKED', 'codes': ['INSPECT_STEP'], 'fields': [' step_id = stp_COMPONENT_B, status = BLOCKED']}]
        header = 'next_actions(2 条,建议而非已执行)'
        dom = {'next_actions': header + ''.join(r['text'] for r in rows), 'next_action_header': header, 'next_action_rows': rows}
        self.assertTrue(gate.actions_proof(dom, actions))
        for name in ('extra', 'missing', 'duplicate', 'reordered', 'wrong_step', 'wrong_status', 'execution_claim'):
            with self.subTest(name=name):
                bad = copy.deepcopy(dom)
                if name == 'extra': bad['next_action_rows'].append({'text': 'RESTORE_OLD_TRUTH', 'codes': ['RESTORE_OLD_TRUTH'], 'fields': []})
                elif name == 'missing': bad['next_action_rows'].pop()
                elif name == 'duplicate': bad['next_action_rows'][1] = copy.deepcopy(rows[0])
                elif name == 'reordered': bad['next_action_rows'].reverse()
                elif name == 'wrong_step': bad['next_action_rows'][0]['fields'] = [' step_id = stp_COMPONENT_WRONG, status = EXECUTED']
                elif name == 'wrong_status': bad['next_action_rows'][1]['text'] = 'INSPECT_STEP step_id = stp_COMPONENT_B, status = EXECUTED'
                else: bad['next_actions'] += ' RESTORE_OLD_TRUTH 已执行成功'
                self.assertFalse(gate.actions_proof(bad, actions))

class FrozenFaultMutation(unittest.TestCase):
    def test_rule_ahead_cannot_be_shadowed_by_task_ahead_or_other_fields(self):
        p = {'task': {'revision': 1}, 'objects': [{'object_type': 'task', 'object_id': 'tsk_COMPONENT', 'revision': 1}], 'rules': [{'rule_id': 'rul_COMPONENT', 'version': 1}]}
        original = {'checkpoint_id': 'ckp_COMPONENT', 'task_revision': 1, 'projection_json': gate.canonical(p), 'projection_digest': gate.sha(p), 'full_context_digest': gate.sha(p)}
        expected, sql, parameters = gate.expected_injection(original, 'CP15.default', {}, {})
        self.assertEqual(json.loads(expected['projection_json'])['task']['revision'], 1)
        self.assertEqual(json.loads(expected['projection_json'])['rules'][0]['version'], 2)
        self.assertEqual(expected['task_revision'], 1)
        shadow = copy.deepcopy(expected)
        changed = json.loads(shadow['projection_json']); changed['task']['revision'] = 101; changed['objects'][0]['revision'] = 101
        shadow.update(task_revision=101, projection_json=gate.canonical(changed), projection_digest=gate.sha(changed), full_context_digest=gate.sha(changed))
        self.assertNotEqual(expected, shadow)
        self.assertNotIn('task_revision=?', sql)
        self.assertEqual(parameters[-1], original['checkpoint_id'])

class LateDynamicAdversaries(unittest.TestCase):
    def test_each_dynamic_field_and_exact_input_are_checked_after_release(self):
        fixture = json.loads((ROOT / 'tests/p6/fixtures/cp12-author-143845d.component.json').read_text())
        for original in fixture['switches']:
            self.assertTrue(gate.switch_proof(fixture['row'], original)[0])
            kind = original['kind']
            for fault in ('note', 'digest', 'projection', 'workspace', 'empty_input', 'third_input', 'wrong_release') + (('comparison',) if kind == 'resume' else ()):
                with self.subTest(kind=kind, fault=fault):
                    s = copy.deepcopy(original); dom = s['after']['dom']
                    if fault == 'note': dom['rawtexts'][0]['text'] = 'WRONG_NOTE'
                    elif fault == 'digest': dom['digests'][0]['value'] = '0' * 64
                    elif fault in ('projection', 'workspace', 'comparison'): next(b for b in dom['jsonblocks'] if b['label'].startswith(fault))['text'] = '{}'
                    elif fault in ('empty_input', 'third_input'): next(i for i in dom['inputs'] if i['label'] == kind + '_id')['value'] = '' if fault == 'empty_input' else 'COMPONENT_THIRD_ID'
                    else: s['released']['request_id'] = 'WRONG_RELEASE'
                    # The IDs and ordinary page snapshots are deliberately still right.
                    self.assertIn(s['b'], dom['text'])
                    self.assertFalse(gate.switch_proof(fixture['row'], s)[0])

class ReadonlySQLiteConstraintProof(unittest.TestCase):
    def test_check_constraints_are_verified_on_private_memory_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); path = directory / 'component.db'
            con = sqlite3.connect(path); con.execute('CREATE TABLE fixture(value TEXT CHECK(value="valid"))')
            con.execute('PRAGMA ignore_check_constraints=ON'); con.execute('INSERT INTO fixture VALUES("old-version")'); con.commit(); con.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            index = directory / 'receipt-index.json'; index.write_text(json.dumps({'files': [{'path': path.name, 'bytes': path.stat().st_size, 'sha256': before}]}))
            store = gate.EvidenceStore(directory, hashlib.sha256(index.read_bytes()).hexdigest())
            self.assertEqual(store.database(path.name)['integrity'], ['CHECK constraint failed in fixture'])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

if __name__ == '__main__':
    unittest.main()
