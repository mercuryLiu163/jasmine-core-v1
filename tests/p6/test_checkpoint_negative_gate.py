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

class PublicAuditAbsence(unittest.TestCase):
    def setUp(self):
        self.ui = {'key_metadata': {'actor_id': 'component-actor'},
            'network': [{'request_id': 'component-public', 'method': 'GET', 'path': '/v1/health', 'status': 200, 'body': {'schema_version': 12}}],
            'audit_read': {'status': 200, 'matching': []},
            'observer_window': [{'kind': 'request_start', 'request_id': 'component-public'},
                                {'kind': 'request_end', 'request_id': 'component-public', 'status': 200}]}

    def test_public_route_requires_actual_window_and_absent_audit(self):
        self.assertTrue(gate.browser_audit_proof(self.ui))
        self.ui['observer_window'].pop()
        self.assertFalse(gate.browser_audit_proof(self.ui))

    def test_public_route_cannot_hide_scan_or_spurious_audit(self):
        self.ui['observer_window'].append({'kind': 'scanner_call_enter', 'request_id': 'component-public'})
        self.assertFalse(gate.browser_audit_proof(self.ui))
        self.ui['observer_window'].pop()
        self.ui['audit_read']['matching'].append({'request_id': 'component-public'})
        self.assertFalse(gate.browser_audit_proof(self.ui))

    def test_nonpublic_route_does_not_inherit_public_exception(self):
        self.ui['network'][0]['path'] = '/v1/projects'
        self.assertFalse(gate.browser_audit_proof(self.ui))

if __name__ == '__main__':
    unittest.main()
