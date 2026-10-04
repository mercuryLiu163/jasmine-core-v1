"""Private report persistence regressions; no native Gate claims."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jasmine_core.adapter.gate_runner import report_atomic_json,write_turn_report,MAX_TURN_REPORT_BYTES
from jasmine_core.capture.p2_runtime import atomic_json


class TurnReportTests(unittest.TestCase):
    def test_large_report_is_private_durable_and_lease_cap_stays_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();root.chmod(0o700)
            result={'controller_result':{'status':'PENDING_REVIEW'},'notifications':'x'*(70*1024)}
            report=root/'last-turn.json'
            with patch('jasmine_core.adapter.gate_runner.os.fsync',wraps=os.fsync) as sync:
                report_atomic_json(report,result)
                self.assertEqual(sync.call_count,2)
            self.assertEqual(json.loads(report.read_bytes()),result)
            self.assertEqual(report.stat().st_mode & 0o777,0o600)
            with self.assertRaisesRegex(ValueError,'private lease exceeds cap'):
                atomic_json(root/'lease.json',result)
            self.assertFalse((root/'lease.json').exists())

    def test_oversize_and_nonfinite_preserve_existing_report_and_controller_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();root.chmod(0o700)
            report=root/'last-turn.json';report_atomic_json(report,{'previous':'preserved'})
            previous=report.read_bytes()
            for invalid in ('x'*MAX_TURN_REPORT_BYTES,float('nan'),float('inf')):
                result={'controller_result':{'status':'PENDING_REVIEW'},
                        'raw_native_receipt':'/private/runtime/native-original.jsonl','invalid':invalid}
                write_turn_report(root,result)
                self.assertEqual(report.read_bytes(),previous)
                self.assertEqual(result['controller_result']['status'],'PENDING_REVIEW')
                diagnostic=json.loads((root/'last-turn-write-failure.json').read_bytes())
                self.assertEqual(diagnostic['status'],'REPORT_WRITE_FAILED')
                self.assertEqual(diagnostic['controller_status'],'PENDING_REVIEW')
                self.assertEqual(diagnostic['raw_native_receipt'],result['raw_native_receipt'])
                self.assertEqual(result['report_write'],diagnostic)
                self.assertTrue(diagnostic['reason'])

    def test_private_canonical_report_path_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();root.chmod(0o700)
            with self.assertRaises(ValueError):report_atomic_json(root/'lease.json',{})
            target=root/'target';target.write_text('unchanged')
            report=root/'last-turn.json';report.symlink_to(target)
            with self.assertRaises(ValueError):report_atomic_json(report,{})
            self.assertEqual(target.read_text(),'unchanged')
            report.unlink()
            alias=root/'alias';alias.symlink_to(root,target_is_directory=True)
            with self.assertRaises(ValueError):report_atomic_json(alias/'last-turn.json',{})
            root.chmod(0o755)
            with self.assertRaises(ValueError):report_atomic_json(report,{})
