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

    def test_terminal_replay_map_covers_every_pack_case(self):
        # The map is the single source of truth for two call sites (the seal and
        # the replay). If it drifts from the cases it cannot protect, the growth it
        # exists to prevent comes back silently -- one line at a time.
        import inspect

        module = _module()
        self.assertEqual(set(module.TERMINAL_SETUP_PACK_KEYS), set(module.NEEDS_P3_PACK))
        for case_id, key in module.TERMINAL_SETUP_PACK_KEYS.items():
            with self.subTest(case=case_id):
                source = inspect.getsource(module.CASES_BY_ID[case_id])
                self.assertIn(f'"{key}"', source,
                              f"{case_id} no longer builds {key}; the terminal-setup "
                              f"replay map names a pack that case does not use")

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
