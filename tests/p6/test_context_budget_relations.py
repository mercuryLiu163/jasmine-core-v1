"""Independent mutation checks for creation and authorized audit consumption.

Fixtures use production event digest and SQLite rows, without a live Core.
"""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts/p6')]
from jasmine_core import SCHEMA_VERSION, clock, ids
from jasmine_core.canonical import sha256_hex
from jasmine_core.models import NewEvent
from context_budget_integrity import creation_relation
from context_budget_gate import audit_consumption


class CreationRelations(unittest.TestCase):
    def fixture(self, checkpoint):
        actor = {'actor_id': 'actor-one', 'kind': 'human'}
        body = {'host_id': 'host-one', 'task_id': 'task-one', 'session_id': 'session-one',
                'idempotency_key': 'request-key', 'reason': 'MANUAL', 'note': None}
        column = 'checkpoint_id' if checkpoint else 'context_pack_id'
        table = 'checkpoint_request_keys' if checkpoint else 'context_request_keys'
        result = {column: ids.new_id('ckp' if checkpoint else 'ctx'), 'command_event_id': ids.new_id('evt'),
                  'actor_id': 'actor-one', 'host_id': 'host-one', 'task_id': 'task-one',
                  'session_id': 'session-one', 'project_id': 'project-one',
                  'projection': {'project_id': 'project-one'}, 'created_at': '2026-10-10T00:00:01Z'}
        payload = {'text': '', column: result[column], 'source_event_id': ids.new_id('evt'), 'truth_digest': 'a' * 64}
        identity = {k: result[k] for k in ('actor_id', 'host_id', 'task_id', 'session_id', 'project_id')}
        spec = NewEvent(event_type='checkpoint.created' if checkpoint else 'context.built',
                        source_system='core-continuity' if checkpoint else 'core-context',
                        actor_kind='human', payload=payload, occurred_at=clock.parse_rfc3339('2026-10-10T00:00:02Z'),
                        event_id=result['command_event_id'], **identity)
        event = dict(identity, actor_kind='human', event_id=spec.event_id, seq=2, schema_version=SCHEMA_VERSION,
                     event_type=spec.event_type, source_system=spec.source_system, source_event_id=None,
                     occurred_at='2026-10-10T00:00:02Z', recorded_at='2026-10-10T00:00:03Z',
                     payload_json=json.dumps(payload), body_sha256=spec.digest())
        key = dict(actor_id=actor['actor_id'], idempotency_key=body['idempotency_key'],
                   request_hash=sha256_hex({'body': body, 'task_id': body['task_id']}) if checkpoint else sha256_hex(body),
                   **{column: result[column]})
        return actor, body, result, payload, event, key, table

    def run_fixture(self, checkpoint, mutation=None):
        actor, body, result, payload, event, key, table = self.fixture(checkpoint)
        item = {'body': body, 'http': {'started_at_unix': 1791590400, 'finished_at_unix': 1791590404}}
        previous = dict(key, actor_id='prior-actor', idempotency_key='prior-key')
        keys = [previous.copy(), key]
        if mutation: mutation(item, result, payload, event, keys)
        connections = []
        try:
            for rows, events in (([previous], [{'seq': 1}]), (keys, [event])):
                con = sqlite3.connect(':memory:'); con.row_factory = sqlite3.Row; connections.append(con)
                columns = list(event)
                con.execute('CREATE TABLE events (' + ','.join(k + (' INTEGER' if k in ('seq', 'schema_version') else ' TEXT') for k in columns) + ')')
                for row in events:
                    con.execute('INSERT INTO events (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', list(row.values()))
                con.execute('CREATE TABLE ' + table + ' (' + ','.join(k + ' TEXT' for k in key) + ')')
                for row in rows: con.execute('INSERT INTO ' + table + ' VALUES (?,?,?,?)', list(row.values()))
            creation_relation(*connections, item, result, payload, actor, checkpoint)
        finally:
            for con in connections: con.close()

    def test_legitimate_context_and_checkpoint(self):
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint): self.run_fixture(checkpoint)

    def test_envelope_identity_digest_time_and_mapping_mutations(self):
        changes = []
        for field, value in {'schema_version': -1, 'event_type': 'resume.built', 'source_system': 'forged',
                             'source_event_id': 'source', 'payload_json': '{}', 'body_sha256': '0'*64,
                             'event_id': 'invalid', 'seq': 9, 'actor_id': 'other', 'actor_kind': 'agent',
                             'host_id': 'other', 'task_id': 'other', 'project_id': 'other', 'session_id': None,
                             'occurred_at': '2026-10-09T23:59:59Z', 'recorded_at': '2026-10-10T00:00:05Z'}.items():
            changes.append(('event '+field, lambda i,r,p,e,k,f=field,v=value: e.__setitem__(f,v)))
        for field in ('actor_id','host_id','task_id','session_id','project_id','created_at'):
            value = '2026-10-10T00:00:05Z' if field == 'created_at' else 'forged'
            changes.append(('result '+field, lambda i,r,p,e,k,f=field,v=value: r.__setitem__(f,v)))
        for field in ('actor_id','idempotency_key','request_hash'):
            changes.append(('key '+field, lambda i,r,p,e,k,f=field: k[-1].__setitem__(f,'forged')))
        changes += [('missing key', lambda i,r,p,e,k: k.pop()), ('extra key', lambda i,r,p,e,k: k.append(dict(k[-1],idempotency_key='extra'))),
                    ('changed prior key', lambda i,r,p,e,k: k[0].__setitem__('request_hash','forged'))]
        for checkpoint in (False, True):
            column = 'checkpoint_id' if checkpoint else 'context_pack_id'
            specific = [('result id',lambda i,r,p,e,k: r.__setitem__(column,'invalid')),
                        ('key result id',lambda i,r,p,e,k: k[-1].__setitem__(column,ids.new_id('ckp' if checkpoint else 'ctx')))]
            for name, mutate in changes + specific:
                with self.subTest(checkpoint=checkpoint,mutation=name), self.assertRaises(ValueError): self.run_fixture(checkpoint,mutate)

    def test_complete_row_json_and_scalar_relations_preserve_types(self):
        from context_budget_integrity import scalar_columns
        row={'projection_version':1,'selector_event_seq':2,'token_count':3,
             'projection_json':json.dumps({'snapshot_schema':1}),
             'full_result_json':json.dumps({'projection':{'snapshot_schema':1},'selector':{'selection_event_seq':2},'token_count':3})}
        pack={'projection':{'snapshot_schema':1},'selector':{'selection_event_seq':2},'token_count':3}
        scalar_columns(row,pack)
        for change in (lambda p:p['projection'].__setitem__('snapshot_schema',True),
                       lambda p:p['selector'].__setitem__('selection_event_seq',2.0),
                       lambda p:p.__setitem__('token_count',3.0)):
            altered=copy.deepcopy(pack);change(altered)
            with self.assertRaises(ValueError):scalar_columns(row,altered)

    def test_command_payload_json_type_must_match_the_expected_payload(self):
        for checkpoint in (False,True):
            def change(i,r,p,e,k):
                p['token_count']=1
                spec=NewEvent(event_type=e['event_type'],source_system=e['source_system'],
                    actor_kind=e['actor_kind'],payload=p,occurred_at=clock.parse_rfc3339(e['occurred_at']),
                    event_id=e['event_id'],**{f:e[f] for f in ('actor_id','host_id','task_id','project_id','session_id')})
                e['body_sha256']=spec.digest()
                e['payload_json']=json.dumps({**p,'token_count':True})
            with self.subTest(checkpoint=checkpoint),self.assertRaisesRegex(ValueError,'envelope/source/payload'):
                self.run_fixture(checkpoint,change)

    def test_actor_kind_counterexample_reaches_a_valid_database_enum(self):
        from unittest.mock import patch
        import context_budget_review_regressions as regressions
        for checkpoint in (False,True):
            actor,body,result,payload,event,key,table=self.fixture(checkpoint)
            raw={'cases':[{'pack':result,'fixture':{'source':{'event_id':ids.new_id('evt')}}},
                          {'checkpoint_operation':{'http':{'body':{'checkpoint':result}}}}],
                 'before_browser':{'events':[event]}}
            role='checkpoint' if checkpoint else 'context'
            with patch.object(regressions,'change_DB') as physical:
                regressions.mutation(Path('.'),raw,[],'complete_relations_creation_'+role+'_actor_kind',None)
            changed=physical.call_args.args[-1]['actor_kind']
            with self.subTest(role=role):
                self.assertIn(changed,('human','agent','system'))
                self.assertNotEqual(changed,actor['kind'])

    def test_relation_mutations_keep_the_production_server_command_digest(self):
        from unittest.mock import patch
        import context_budget_review_regressions as regressions
        for checkpoint in (False, True):
            for field in ('source_system', 'source_event_id', 'timewindow'):
                actor,body,result,payload,event,key,table=self.fixture(checkpoint)
                role='checkpoint' if checkpoint else 'context'
                raw={'cases':[{'pack':result,'fixture':{'source':{'event_id':ids.new_id('evt')}}},
                              {'checkpoint_operation':{'http':{'body':{'checkpoint':result}}}}],
                     'before_browser':{'events':[event]}}
                with patch.object(regressions,'change_DB') as physical:
                    regressions.mutation(Path('.'),raw,[], 'complete_relations_creation_'+role+'_'+field,None)
                changed=raw['before_browser']['events'][0]
                spec=NewEvent(event_type=changed['event_type'],source_system=changed['source_system'],
                    source_event_id=changed['source_event_id'],actor_kind=changed['actor_kind'],
                    occurred_at=clock.parse_rfc3339(changed['occurred_at']),payload=json.loads(changed['payload_json']),
                    event_id=changed['event_id'], **{k:changed[k] for k in ('actor_id','host_id','project_id','task_id','session_id')})
                with self.subTest(role=role,field=field):
                    self.assertEqual(changed['body_sha256'],spec.digest())
                    self.assertEqual(physical.call_args.args[-1]['body_sha256'],spec.digest())


class AuditRelations(unittest.TestCase):
    def fixture(self, phase):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        target = {'request_id':'action', 'actor_id':'actor-one', 'method':'POST','path':'/v1/context/build',
                  'status':409, 'body':{'error':{'code':'context_required_overflow'}}}
        def row(seq, request, actor, method, path, status, error=None):
            return dict(seq=seq,request_id=request,actor_id=actor,method=method,path=path,status_code=status,
                        error_code=error,scope='admin',detail_json='{}')
        rows = [row(1,'unrelated','other','GET','/v1/context/missing',404),
                row(2,'action','actor-one','POST','/v1/context/build',409,'context_required_overflow'),
                row(3,'audit','admin-actor','GET','/v1/audit',200), row(4,'later','actor-one','GET','/v1/tasks',200)]
        con=sqlite3.connect(directory/'after-audit.db')
        con.executescript('CREATE TABLE actors(actor_id TEXT,kind TEXT,home_host_id TEXT); CREATE TABLE api_keys(key_id TEXT,actor_id TEXT,scopes TEXT,revoked_at TEXT);')
        con.execute('INSERT INTO actors VALUES(?,?,?)',('admin-actor','human','host'))
        con.execute('INSERT INTO api_keys VALUES(?,?,?,NULL)',('key','admin-actor','["admin"]'))
        con.execute('CREATE TABLE audit_log ('+','.join(k+(' INTEGER' if k in ('seq','status_code') else ' TEXT') for k in rows[0])+')')
        for r in rows: con.execute('INSERT INTO audit_log VALUES ('+','.join('?' for _ in r)+')',list(r.values()))
        con.commit();con.close()
        physical=[dict(r) for r in rows[:2] if phase!='separate-audit-validator' or r['actor_id']=='actor-one']
        for r in physical:r['detail']=json.loads(r.pop('detail_json'))
        h={'phase':phase,'request_id':'audit','actor_id':'admin-actor','key_id':'key','scopes':['admin'],
           'method':'GET','status':200,'request_body':None,'path':'/v1/audit?limit=1000'+('&actor_id=actor-one' if phase=='separate-audit-validator' else ''),
           'body':{'entries':physical,'count':len(physical)},'started_at_unix':3,'finished_at_unix':4}
        observer=[{'request_id':'action','kind':'request_start','at_unix':1,'method':'POST','path':'/v1/context/build'}, {'request_id':'action','kind':'request_end','at_unix':2,'status':409},
                  {'request_id':'audit','kind':'request_start','method':'GET','path':'/v1/audit','at_unix':3},
                  {'request_id':'audit','kind':'request_end','status':200,'at_unix':4}]
        matching=[copy.deepcopy(r) for r in physical if r['request_id']=='action']
        return directory,h,[target],matching,[copy.deepcopy(h)],observer

    def test_legitimate_browser_and_filtered_validator(self):
        for phase in ('browser-authorized-audit','separate-audit-validator'):
            directory,h,targets,matching,calls,observer=self.fixture(phase)
            with self.subTest(phase=phase): audit_consumption(directory,h,phase,targets,matching,calls,observer)

    def test_public_actor_null_and_stale_action_are_consumed(self):
        directory,h,targets,matching,calls,observer=self.fixture('browser-authorized-audit')
        targets[0].update(actor_id=None,method='GET',path='/v1/health',status=200,body={})
        con=sqlite3.connect(directory/'after-audit.db')
        con.execute("UPDATE audit_log SET actor_id=NULL,method='GET',path='/v1/health',status_code=200,error_code=NULL WHERE request_id='action'")
        con.commit();con.close()
        for r in (h['body']['entries'][1],matching[0]):r.update(actor_id=None,method='GET',path='/v1/health',status_code=200,error_code=None)
        observer[0].update(method='GET',path='/v1/health',at_unix=-100)
        observer[1].update(status=200,at_unix=-99)
        audit_consumption(directory,h,'browser-authorized-audit',targets,matching,[copy.deepcopy(h)],observer)

    def test_successful_checkpoint_action_consumed(self):
        directory,h,targets,matching,calls,observer=self.fixture('separate-audit-validator')
        con=sqlite3.connect(directory/'after-audit.db')
        con.execute("UPDATE audit_log SET path='/v1/checkpoints', status_code=201, error_code=NULL WHERE request_id='action'")
        con.commit();con.close()
        targets[0].update(path='/v1/checkpoints',status=201,body={'replayed':False,'checkpoint':{}})
        for r in (h['body']['entries'][0],matching[0]):r.update(path='/v1/checkpoints',status_code=201,error_code=None)
        observer[0]['path']='/v1/checkpoints';observer[1]['status']=201
        audit_consumption(directory,h,'separate-audit-validator',targets,matching,[copy.deepcopy(h)],observer)

    def test_audit_ascending_limit_excludes_target_beyond_first_thousand(self):
        directory,h,targets,matching,calls,observer=self.fixture('browser-authorized-audit')
        con=sqlite3.connect(directory/'after-audit.db')
        con.execute("UPDATE audit_log SET seq=1002 WHERE request_id='audit'")
        con.execute("UPDATE audit_log SET seq=1001 WHERE request_id='action'")
        con.execute("DELETE FROM audit_log WHERE request_id='later'")
        for seq in range(2,1001):
            con.execute('INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)',(seq,'old-'+str(seq),'other','GET','/v1/tasks',200,None,'admin','{}'))
        con.row_factory=sqlite3.Row
        entries=[dict(r) for r in con.execute('SELECT * FROM audit_log WHERE seq<=1000 ORDER BY seq')]
        for r in entries:r['detail']=json.loads(r.pop('detail_json'))
        con.commit();con.close()
        h['body']={'entries':entries,'count':1000}
        with self.assertRaisesRegex(ValueError,'every consumed target'):
            audit_consumption(directory,h,'browser-authorized-audit',targets,matching,[copy.deepcopy(h)],observer)

    def test_json_numeric_type_substitution_is_not_the_recorded_response(self):
        for phase,change in (
            ('browser-authorized-audit',lambda h:h['body']['entries'][0].__setitem__('seq',True)),
            ('separate-audit-validator',lambda h:h['body'].__setitem__('count',True)),
            ('browser-authorized-audit',lambda h:h['body'].__setitem__('count',float(h['body']['count']))),
            ('separate-audit-validator',lambda h:h['body']['entries'][0].__setitem__('seq',float(h['body']['entries'][0]['seq'])))):
            directory,h,targets,matching,calls,observer=self.fixture(phase)
            change(h);calls[:]=[copy.deepcopy(h)]
            with self.subTest(phase=phase),self.assertRaisesRegex(ValueError,'physical query'):
                audit_consumption(directory,h,phase,targets,matching,calls,observer)

    def test_action_and_observer_numeric_types_are_not_coerced(self):
        for phase in ('browser-authorized-audit','separate-audit-validator'):
            for field in ('action_status','observer_status','read_status'):
                directory,h,targets,matching,calls,observer=self.fixture(phase)
                if field=='action_status':targets[0]['status']=409.0
                elif field=='observer_status':observer[1]['status']=409.0
                else:h['status']=200.0;calls[:]=[copy.deepcopy(h)]
                with self.subTest(phase=phase,field=field),self.assertRaises(ValueError):
                    audit_consumption(directory,h,phase,targets,matching,calls,observer)

    def test_typed_body_counterexamples_are_actually_applied(self):
        import context_budget_review_regressions as regressions
        for role in ('browser','operation'):
            for field in ('typed_count','typed_entry'):
                phase='browser-authorized-audit' if role=='browser' else 'separate-audit-validator'
                directory,h,targets,matching,calls,observer=self.fixture(phase)
                original=json.dumps(h['body'],sort_keys=True)
                raw={'cases':[{'operation':{'audit_read':copy.deepcopy(h)}},
                              {'checkpoint_operation':{'audit_read':h,'http':targets[0],'audit':matching}}],
                     'browser_audit':{'http':h,'matching':matching}}
                regressions.mutation(directory,raw,calls,'complete_relations_audit_'+role+'_'+field,None)
                changed=raw['browser_audit']['http'] if role=='browser' else raw['cases'][1]['checkpoint_operation']['audit_read']
                with self.subTest(role=role,field=field):
                    self.assertNotEqual(json.dumps(changed['body'],sort_keys=True),original)
                    with self.assertRaisesRegex(ValueError,'physical query'):
                        audit_consumption(directory,changed,phase,targets,matching,calls,observer)

    def test_consumer_response_target_and_chronology_mutations(self):
        mutations=[('phase',lambda h,t,m,c,o:h.__setitem__('phase','other')),
                   ('receipt',lambda h,t,m,c,o:c.clear()), ('method',lambda h,t,m,c,o:h.__setitem__('method','POST')),
                   ('status',lambda h,t,m,c,o:h.__setitem__('status',403)), ('request body',lambda h,t,m,c,o:h.__setitem__('request_body',{})),
                   ('endpoint',lambda h,t,m,c,o:h.__setitem__('path','/v1/events?limit=1000')),
                   ('limit',lambda h,t,m,c,o:h.__setitem__('path',h['path'].replace('1000','10'))),
                   ('absolute URL',lambda h,t,m,c,o:h.__setitem__('path','https://forged.example'+h['path'])),
                   ('fragment',lambda h,t,m,c,o:h.__setitem__('path',h['path']+'#forged')),
                   ('extra filter',lambda h,t,m,c,o:h.__setitem__('path',h['path']+'&unexpected=1')),
                   ('forged credential',lambda h,t,m,c,o:h.__setitem__('actor_id','forged')),
                   ('missing admin scope',lambda h,t,m,c,o:h.__setitem__('scopes',[])),
                   ('reversed read window',lambda h,t,m,c,o:h.__setitem__('finished_at_unix',2)),
                   ('target observer method',lambda h,t,m,c,o:o[0].__setitem__('method','GET')),
                   ('target observer path',lambda h,t,m,c,o:o[0].__setitem__('path','/v1/tasks')),
                   ('target observer status',lambda h,t,m,c,o:o[1].__setitem__('status',200)),
                   ('response count',lambda h,t,m,c,o:h['body'].__setitem__('count',999)),
                   ('response omitted',lambda h,t,m,c,o:h['body']['entries'].clear()),
                   ('matching omitted',lambda h,t,m,c,o:m.clear()), ('duplicate target',lambda h,t,m,c,o:t.append(t[0].copy())),
                   ('target absent',lambda h,t,m,c,o:t[0].__setitem__('request_id','unknown')),
                   ('observer absent',lambda h,t,m,c,o:o.__setitem__(slice(0,2),[])),
                   ('chronology',lambda h,t,m,c,o:o[1].__setitem__('at_unix',5))]
        for field,value in {'actor_id':'other','method':'GET','path':'/v1/checkpoints','status':200,'body':{'error':{'code':'other'}}}.items():
            mutations.append(('target '+field,lambda h,t,m,c,o,f=field,v=value:t[0].__setitem__(f,v)))
        for phase in ('browser-authorized-audit','separate-audit-validator'):
            for name,mutate in mutations:
                directory,h,targets,matching,calls,observer=self.fixture(phase)
                mutate(h,targets,matching,calls,observer)
                # Keep the presence check satisfied to reach deeper checks.
                if name!='receipt':calls[:]=[copy.deepcopy(h)]
                with self.subTest(phase=phase,mutation=name),self.assertRaises(ValueError):
                    audit_consumption(directory,h,phase,targets,matching,calls,observer)

if __name__=='__main__': unittest.main()
