"""Real local refusal/capture with stable terminal-failure replay, never Gate acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from test_p4_fix_round import _module, _real_gate


class PartialFailureReplay(unittest.TestCase):
    def _fixture(self, gate):
        directory = gate.root / 'fixture'
        directory.mkdir()
        (directory/'NOTES.md').write_text('discount is a percentage of subtotal\n')
        (directory/'calc.py').write_text('def total(prices, discount):\n    return sum(prices) - discount\n')
        (directory/'test_calc.py').write_text('import unittest\nfrom calc import total\nclass Test(unittest.TestCase):\n def test_total(self): self.assertAlmostEqual(total([10.,20.],5.),27.5)\n')
        subprocess.run(['git','init','-q',str(directory)],check=True)
        subprocess.run(['git','-C',str(directory),'add','.'],check=True)
        subprocess.run(['git','-C',str(directory),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','baseline'],check=True)
        gate.config['g01_historical_fixture'] = {'repository':str(directory),'ref':'HEAD','python':sys.executable}

    def _dead_engine(self, gate):
        from jasmine_core.memory.hindsight import HindsightTransport, HindsightConfig
        gate.transport=HindsightTransport(HindsightConfig('http://127.0.0.1:1',timeout_seconds=0.2))

    def _seal(self, module, gate, record, phase, name):
        path=gate.root.parent/(name+'.json')
        meta={'commit':gate.git_commit(),'dirty':False}
        payload={'cases':[record.as_dict()]}
        path.write_text(json.dumps(payload))
        seal=module._write_phase_seal(gate.root.parent,phase,path,[record],meta)
        payload['seal']=seal
        path.write_text(json.dumps(payload))
        module._write_manifest_anchor(gate.root.parent,phase,path,meta)
        return seal

    def _counts(self, gate):
        conn=gate.conn()
        try:
            return {name:conn.execute('SELECT count(*) FROM '+name).fetchone()[0]
                    for name in ('projects','tasks','steps','rules','checkpoints','events','evidence','memory_items','memory_jobs','memory_job_attempts')}
        finally:
            conn.close()

    def _two_resumes(self, module, gate, record):
        phase1=self._seal(module,gate,record,'phase1','original-failure')
        original_seal=Path(phase1['path']).read_bytes()
        receipt_bytes={r['path']:Path(r['path']).read_bytes() for r in record.receipts}
        counts=self._counts(gate)
        database_hash=hashlib.sha256(gate.db_path.read_bytes()).hexdigest()
        original_reason=record.reason
        phase2=None
        for index in (1,2):
            prior=module._verify_prior_seal(gate.root.parent,'phase2')
            replayed=module.CaseRecord(gate.case_id,'BLOCKED',mode='not-run')
            self.assertTrue(module._replay_terminal_setup_failure(gate,replayed,prior['cases'][gate.case_id]))
            self.assertEqual(replayed.verdict,'FAIL')
            self.assertEqual(replayed.reason,original_reason)
            self.assertEqual(replayed.project_ids,record.project_ids)
            self.assertFalse(replayed.inherited_evidence['terminal_failure_replay']['setup_reexecuted'])
            resumed_seal=self._seal(module,gate,replayed,'phase2','resume-'+str(index))
            if phase2 is None: phase2=Path(resumed_seal['path']).read_bytes()
            else: self.assertEqual(Path(resumed_seal['path']).read_bytes(),phase2)
            self.assertEqual(self._counts(gate),counts)
            self.assertEqual(hashlib.sha256(gate.db_path.read_bytes()).hexdigest(),database_hash)
            self.assertEqual(Path(phase1['path']).read_bytes(),original_seal)
            self.assertEqual({p:Path(p).read_bytes() for p in receipt_bytes},receipt_bytes)
        return phase1

    def test_g01_actual_capture_unreachable_retention_two_resume_keep_failed_attempt(self):
        module=_module();gate=_real_gate(self,case_id='P4-G01')
        project=gate.seed_project('partial-g01');task=gate.new_task(project,'history')
        self._fixture(gate);self._dead_engine(gate)
        record=module.CaseRecord('P4-G01','BLOCKED',mode='real',project_ids=[project])
        with self.assertRaises(module.Failed) as caught:
            gate.retain_g01_history(project,task,record)
        record.verdict='FAIL';record.reason=str(caught.exception);record.failure_class='AssertionFailure'
        self.assertEqual(len(record.evidence_ids),4)
        self.assertEqual({r['kind'] for r in record.receipts},
                         {'g01-historical-failure','g01-historical-inspection','g01-historical-repair','g01-historical-validation','g01-historical-chain'})
        phase1=self._two_resumes(module,gate,record)
        sealed=json.loads(Path(phase1['path']).read_text())
        self.assertEqual({e['path'] for e in sealed['receipts']},{r['path'] for r in record.receipts})
        # New evidence participates in the seal: tampering is refused before a resume.
        Path(record.receipts[0]['path']).write_text('edited output')
        with self.assertRaises(module.Failed):
            module._verify_prior_seal(gate.root.parent,'phase2')

    def test_g05_actual_engine_refusal_two_resume_do_not_replace_rule_or_jobs(self):
        module=_module();gate=_real_gate(self,case_id='P4-G05')
        project=gate.seed_project('partial-g05')
        record=module.CaseRecord('P4-G05','BLOCKED',mode='real',project_ids=[project])
        gate.confirm_decision(project,content='deploy checklist',rule_key='g05.deploy-checklist')
        module._verify_g05_hard_rule(gate,record,project)
        self._dead_engine(gate)
        from jasmine_core.memory import eligibility
        from jasmine_core.memory.jobs import JobStore
        from jasmine_core.memory.store import MemoryStore
        conn=gate.conn()
        try:
            jobs=JobStore(conn,MemoryStore(conn,schema_version=module.SCHEMA_VERSION))
            jobs.enqueue_from_sources(eligibility.SourceRegistry())
            gate.drain(conn)
            rows=jobs.jobs();self.assertTrue(rows)
            record.job_ids=[r['job_id'] for r in rows]
            record.document_ids=[r['document_id'] for r in rows]
            with self.assertRaises(module.Failed) as caught:
                module._verify_g05_recovery(gate,record,expected_job_ids=record.job_ids,
                                           memory_ids=[r['memory_id'] for r in rows])
            record.verdict='FAIL';record.reason=str(caught.exception);record.failure_class='AssertionFailure'
        finally:
            conn.close()
        self._two_resumes(module,gate,record)

    def test_g04_terminal_setup_failure_two_resume_keep_counts(self):
        # G04's setup creates two projects, two rules and their Events before its
        # pack exists, and the ACL leg can fail after all of that. The replay used
        # to cover G01/G05 only, so the next resume ran `case_g04` again: measured
        # growth was +2 projects, +2 rules, +8 Events, +2 memory items, +2 jobs and
        # a recall trace per resume, doubling the case's counts every time.
        import os
        from unittest import mock

        module = _module()
        gate = _real_gate(self, case_id='P4-G04')
        gate.config.update({'real_agent_session': True, 'bank_suffix': 'p4-partial-g04',
                            'dependency_commits': {'P2': '850fbec', 'P3': '7016dab'}})
        gate.bank_suffix = 'p4-partial-g04'
        record = module.CaseRecord('P4-G04', 'BLOCKED', mode='real')
        self._dead_engine(gate)
        with mock.patch.dict(os.environ, {
                'JASMINE_MEMORY_BANK_NAMESPACE': 'p4-partial-g04',
                'JASMINE_HINDSIGHT_URL': 'http://127.0.0.1:1'}):
            with self.assertRaises(module.Failed) as caught:
                module.case_g04(gate, record)
        record.verdict = 'FAIL'
        record.reason = str(caught.exception)
        record.failure_class = 'AssertionFailure'
        # The terminal failure is real and terminal: the setup objects exist, and
        # no pack was built, so a resume has nothing to fall back on but the seal.
        self.assertEqual(len(record.project_ids), 2)
        self.assertEqual(record.receipts, [])
        self.assertEqual(list(gate.root.glob('pack-*.json')), [])
        phase1 = self._two_resumes(module, gate, record)
        sealed = json.loads(Path(phase1['path']).read_text())
        terminal = sealed['cases']['P4-G04']['terminal_failure']
        self.assertEqual(terminal['reason'], record.reason)
        self.assertEqual(terminal['project_ids'], record.project_ids)

    def test_a_down_engine_still_replays_the_sealed_failure(self):
        # N1: the functional preflight ran before the case loop, so a resume of a
        # sealed terminal FAIL against a down engine recorded BLOCKED instead of
        # replaying the FAIL -- the reproduction depended on the very thing it is
        # meant to be independent of. This drives `main()` end to end with a dead
        # engine URL and the real predicate (no mock), and requires the sealed
        # failure to come back unchanged.
        import contextlib
        import io
        import os
        from unittest import mock

        module = _module()
        gate = _real_gate(self, case_id='P4-G01')
        project = gate.seed_project('partial-g01-down')
        task = gate.new_task(project, 'history')
        self._fixture(gate)
        self._dead_engine(gate)
        record = module.CaseRecord('P4-G01', 'BLOCKED', mode='real',
                                   project_ids=[project])
        with self.assertRaises(module.Failed) as caught:
            gate.retain_g01_history(project, task, record)
        record.verdict = 'FAIL'
        record.reason = str(caught.exception)
        record.failure_class = 'AssertionFailure'
        self._seal(module, gate, record, 'phase1', 'original-failure')

        work = gate.root.parent
        root = work.parent
        out = root / 'out-down-engine'
        config = root / 'op.json'
        config.write_text(json.dumps({
            'run_id': 'p4-down-engine', 'bank_suffix': 'p4-down-engine',
            'dependency_commits': {'P2': '0' * 40, 'P3': '0' * 40}}), encoding='utf-8')
        argv = [str(Path(module.__file__)), '--out', str(out), '--work', str(work),
                '--config', str(config), '--resume', '--case', 'P4-G01',
                '--allow-dirty']
        with mock.patch.dict(os.environ,
                             {'JASMINE_HINDSIGHT_URL': 'http://127.0.0.1:1'},
                             clear=False), \
                mock.patch.object(sys, 'argv', argv), \
                contextlib.redirect_stdout(io.StringIO()):
            code = module.main()
        # The runner maps PASS->0, BLOCKED->2, FAIL->1; a replayed failure is a
        # FAIL run, so it must exit 1, not be softened into BLOCKED's 2.
        self.assertEqual(code, 1, 'a replayed FAIL exits 1 (FAIL), not BLOCKED')
        manifest = json.loads(sorted(out.glob('p4-gate-*.json'))[-1].read_text())
        case = [c for c in manifest['cases'] if c['case_id'] == 'P4-G01'][0]
        self.assertEqual(case['verdict'], 'FAIL')
        self.assertEqual(case['reason'], record.reason)
        self.assertEqual(manifest['engine']['functional_preflight']['verdict'],
                         'SKIPPED')

    def test_terminal_replay_map_covers_every_case(self):
        # The map is the single source of truth for two call sites (the seal and
        # the replay). If it drifts from the cases it cannot protect, the growth it
        # exists to prevent comes back silently -- one line at a time. G06's durable
        # unit is its completion marker, not a pack, and its map value is None.
        import inspect

        module = _module()
        pack_cases = {case_id: key
                      for case_id, key in module.TERMINAL_SETUP_PACK_KEYS.items()
                      if key is not None}
        self.assertEqual(set(pack_cases), set(module.NEEDS_P3_PACK))
        for case_id, key in pack_cases.items():
            with self.subTest(case=case_id):
                source = inspect.getsource(module.CASES_BY_ID[case_id])
                self.assertIn(f'"{key}"', source,
                              f"{case_id} no longer builds {key}; the terminal-setup "
                              f"replay map names a pack that case does not use")
        self.assertIsNone(module.TERMINAL_SETUP_PACK_KEYS["P4-G06"])
        self.assertIn("g06-complete.json",
                      inspect.getsource(module.CASES_BY_ID["P4-G06"]))

    def test_terminal_replay_records_which_ids_it_rechecked(self):
        # Every recorded id field is rechecked against Core, and the fields that
        # were empty in the sealed record are named, so an empty recheck is visible
        # rather than silently passing for coverage.
        module = _module()
        gate = _real_gate(self, case_id='P4-G01')
        project = gate.seed_project('replay-rechecked')
        record = module.CaseRecord('P4-G01', 'FAIL', mode='real', reason='failure',
                                   project_ids=[project])
        replayed = module.CaseRecord('P4-G01', 'BLOCKED')
        self.assertTrue(module._replay_terminal_setup_failure(
            gate, replayed, {'verdict': 'FAIL', 'terminal_failure': record.as_dict()}))
        evidence = replayed.inherited_evidence['terminal_failure_replay']
        self.assertEqual(evidence['rechecked_core_ids'],
                         {'task_ids': 0, 'session_ids': 0, 'context_pack_ids': 0,
                          'rule_versions': 0})
        self.assertEqual(evidence['empty_rechecked_fields'],
                         ['context_pack_ids', 'rule_versions', 'session_ids', 'task_ids'])

    def test_terminal_replay_refuses_a_recorded_id_core_does_not_hold(self):
        # A sealed failure that names a Task/Context Pack/Session/rule version Core
        # no longer holds is not the same failure any more; the replay has to refuse
        # rather than reproduce a reason about objects that are gone.
        module = _module()
        cases = (
            ('task_ids', 'Task is missing', 'tsk_' + 'Q' * 26),
            ('context_pack_ids', 'Context Pack is missing', 'ctx_' + 'Q' * 26),
            ('session_ids', 'Session is missing', 'native-' + 'Q' * 10),
            ('rule_versions', 'rule version', 'rule_' + 'Q' * 26 + ':1'),
        )
        for field, marker, value in cases:
            with self.subTest(field=field):
                gate = _real_gate(self, case_id='P4-G01')
                project = gate.seed_project('replay-' + field)
                record = module.CaseRecord('P4-G01', 'FAIL', mode='real',
                                           reason='failure', project_ids=[project])
                if hasattr(record, field):
                    getattr(record, field).append(value)
                else:
                    setattr(record, field, [value])
                before = self._counts(gate)
                with self.assertRaises(module.Failed) as caught:
                    module._replay_terminal_setup_failure(
                        gate, module.CaseRecord('P4-G01', 'BLOCKED'),
                        {'verdict': 'FAIL',
                         'terminal_failure': record.as_dict()})
                self.assertIn(marker, str(caught.exception))
                self.assertEqual(self._counts(gate), before)

    def test_g06_terminal_failure_before_marker_two_resume_keep_counts(self):
        # G06's pre-marker FAIL had the same growth shape as G04: its completion
        # marker did not exist, `case_g06` re-ran, and every resume seeded another
        # project and committed another Decision. The map's None entry makes the
        # replay check the marker instead of a pack; this drives a real terminal
        # FAIL before the marker and proves two resumes add nothing.
        import os
        from unittest import mock

        module = _module()
        gate = _real_gate(self, case_id='P4-G06')
        self._dead_engine(gate)
        # The case's preconditions are made to pass so the run reaches its setup
        # and dies on the dead engine's retain, after the project and Decision
        # exist and long before `g06-complete.json` would be written.
        gate.require_engine = lambda: {'reachable': True}
        gate.require_bank_namespace = lambda: 'p4-partial-g06'
        record = module.CaseRecord('P4-G06', 'BLOCKED', mode='real')
        with mock.patch.dict(os.environ, {
                'JASMINE_MEMORY_BANK_NAMESPACE': 'p4-partial-g06',
                'JASMINE_HINDSIGHT_URL': 'http://127.0.0.1:1',
                'JASMINE_HINDSIGHT_TIMEOUT_SECONDS': '0.2'}):
            with self.assertRaises(module.Failed) as caught:
                module.case_g06(gate, record)
        record.verdict = 'FAIL'
        record.reason = str(caught.exception)
        record.failure_class = 'AssertionFailure'
        self.assertEqual(list(gate.root.glob('g06-complete.json')), [])
        self.assertTrue(record.project_ids)
        phase1 = self._two_resumes(module, gate, record)
        sealed = json.loads(Path(phase1['path']).read_text())
        terminal = sealed['cases']['P4-G06']['terminal_failure']
        self.assertEqual(terminal['reason'], record.reason)
        self.assertEqual(terminal['project_ids'], record.project_ids)

    def test_unsealed_legacy_partial_failure_refuses_reseed(self):
        module=_module();gate=_real_gate(self,case_id='P4-G01')
        before=self._counts(gate)
        for prior in ({'verdict':'FAIL'}, {'verdict':'FAIL','terminal_failure':{'case_id':'P4-G05','verdict':'FAIL','reason':'foreign'}}):
            with self.assertRaises(module.Failed):
                module._replay_terminal_setup_failure(gate,module.CaseRecord('P4-G01','BLOCKED'),prior)
            self.assertEqual(self._counts(gate),before)

    def test_foreign_source_cannot_be_replayed_as_original_failure(self):
        module=_module();gate=_real_gate(self,case_id='P4-G01')
        first=gate.seed_project('original');other=gate.seed_project('foreign')
        source=gate.event(first,'user.prompt','original task')
        original=module.CaseRecord('P4-G01','FAIL',mode='real',reason='failure',project_ids=[other],source_event_ids=[source])
        before=self._counts(gate)
        with self.assertRaises(module.Failed):
            module._replay_terminal_setup_failure(gate,module.CaseRecord('P4-G01','BLOCKED'),{'verdict':'FAIL','terminal_failure':original.as_dict()})
        self.assertEqual(self._counts(gate),before)

    def test_completed_setup_retains_existing_pack_verification_path(self):
        module=_module();gate=_real_gate(self,case_id='P4-G01')
        (gate.root/'pack-complete.json').write_text(json.dumps({'idempotency_key':'g01-with-lesson'}))
        self.assertFalse(module._replay_terminal_setup_failure(gate,module.CaseRecord('P4-G01','BLOCKED'),{'verdict':'FAIL'}))


if __name__=='__main__':
    unittest.main()
