"""P2 business component tests; providers are explicitly simulations."""
import copy
import json
from unittest.mock import patch
from support import DbTestCase
from jasmine_core import SCHEMA_VERSION, db, errors, ids
from jasmine_core.registry import Registry
from jasmine_core.objects import ObjectStore
from jasmine_core.models import NewObject, NewEvent
from jasmine_core.events import EventStore
from jasmine_core.interpretations import InterpretationStore
from jasmine_core.resolver import ResolverStore
from jasmine_core.reviews import ReviewStore
from jasmine_core.resolution_common import Conflict
from test_interpretations import Simulation, output, TEXT

class Resolutions(DbTestCase):
    def setUp(self):
        super().setUp();registry=Registry(self.conn)
        self.host=ids.new_id('hst');self.human=ids.new_id('act');self.system=ids.new_id('act');self.agent=ids.new_id('act')
        registry.upsert_host(self.host)
        for ident,kind in [(self.human,'human'),(self.system,'system'),(self.agent,'agent')]:registry.upsert_actor(ident,kind=kind,home_host_id=self.host)
        self.objects=ObjectStore(self.conn,schema_version=SCHEMA_VERSION)
        self.project=self.objects.create(NewObject.project({'name':'P2','host_id':self.host}),actor_id=self.human)['object']['project_id']
        self.event=EventStore(self.conn,schema_version=SCHEMA_VERSION).append(NewEvent(event_type='user.prompt',source_system='component',occurred_at=self.clock.now(),actor_id=self.human,actor_kind='human',host_id=self.host,project_id=self.project,payload={'text':TEXT}))[0]['event_id']
        def extract(source):
            result=output(source);cap=result['candidates'][0];cap['impact']='MEDIUM'
            task=copy.deepcopy(cap);task.update(kind='TASK_CREATE_OR_ATTACH',content='Test program')
            result['candidates']=[task,cap];return json.dumps(result)
        self.fake=Simulation(extract);self.interpretations=InterpretationStore(self.conn,schema_version=SCHEMA_VERSION,provider=self.fake)
        self.resolver=ResolverStore(self.interpretations,execution_actor_id=self.system,execution_host_id=self.host)
        self.reviews=ReviewStore(self.resolver)
        self.ident=self.interpretations.process({'event_id':self.event,'idempotency_key':'initial'},actor_id=self.human)['interpretation']['interpretation_id']
        self.scopes={'authority:manage','authority:propose','objects:write','state:accept'}
    def request(self,key='resolve',ident=None):
        preview=self.resolver.preview(ident or self.ident)
        return {'idempotency_key':key,'host_id':self.host,'policy_version':preview['policy_version'],'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest']}
    def test_task_null_atomic_source_graph_and_replay(self):
        body=self.request();result=self.resolver.resolve(self.ident,body,actor_id=self.human)
        data=result['resolution']['result'];self.assertEqual(data['disposition'],'APPLIED');self.assertEqual(len(data['actions']),2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM rules WHERE status='ACTIVE'").fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM evidence').fetchone()[0],0)
        self.assertIsNone(EventStore(self.conn,schema_version=SCHEMA_VERSION).get(self.event)['task_id'])
        self.assertTrue(data['source_bindings']);self.assertEqual(self.resolver.resolve(self.ident,body,actor_id=self.human)['resolution']['result'],data)
        other=self.interpretations.process({'event_id':self.event,'idempotency_key':'other'},actor_id=self.agent)['interpretation']['interpretation_id']
        self.assertEqual(self.resolver.resolve(other,self.request('other',other),actor_id=self.agent)['resolution']['result']['disposition'],'SOURCE_REPLAYED')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
    def test_outer_failure_rolls_everything_back(self):
        before=self.conn.execute('SELECT count(*) FROM events').fetchone()[0]
        with patch.object(self.resolver.authority,'transition',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):self.resolver.resolve(self.ident,self.request(),actor_id=self.human)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM events').fetchone()[0],before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM resolutions').fetchone()[0],0)
    def test_pending_full_plan_and_global_human_apply(self):
        result=output(self.interpretations._source(self.event));result['candidates'][0].update(kind='RULE',impact='HIGH',scope={'kind':'GLOBAL','project_id':None,'task_id':None,'path':None,'tool':None})
        corrected=self.resolver.maintenance.change(self.ident,'correct',{'idempotency_key':'correct','host_id':self.host,'reason':'global','expected_revision':1,'result':result},actor_id=self.human)
        child=corrected['result']['child_interpretation_id'];body=self.request('pending',child)
        pending=self.resolver.resolve(child,body,actor_id=self.human)['resolution']['result'];self.assertEqual(pending['disposition'],'PENDING_REVIEW')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM rules').fetchone()[0],0)
        preview=self.resolver.preview(child)
        approval={'idempotency_key':'approve','host_id':self.host,'reason':'confirmed','expected_revision':1,'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest'],'actions':[{'candidate_index':0,'action':'CREATE_RULE','payload':{'kind':'RULE','severity':'HARD','enforcement':'CONTEXT','content':result['candidates'][0]['content'],'matcher':{}}}]}
        applied=self.reviews.change(pending['review_id'],'approve',approval,actor_id=self.human,scopes=self.scopes)
        rule=self.resolver.authority.get(applied['result']['actions'][0]['target_id']);self.assertEqual(rule['scope']['kind'],'global');self.assertEqual(rule['severity'],'HARD')
        canonical=self.resolver.applied_result(pending['resolution_id']);self.assertEqual(len(canonical['actions']),1)
    def test_reject_old_pending_not_approvable(self):
        source=self.interpretations._source(self.event);result=output(source);result['candidates'][0]['certainty']='TENTATIVE'
        child=self.resolver.maintenance.change(self.ident,'correct',{'idempotency_key':'correct','host_id':self.host,'reason':'tentative','expected_revision':1,'result':result},actor_id=self.human)['result']['child_interpretation_id']
        pending=self.resolver.resolve(child,self.request('pending',child),actor_id=self.human)['resolution']['result'];preview=self.resolver.preview(child)
        self.resolver.maintenance.change(child,'reject',{'idempotency_key':'reject','host_id':self.host,'reason':'not valid','expected_revision':2},actor_id=self.human)
        with self.assertRaises(Conflict) as exc:self.reviews.change(pending['review_id'],'approve',{'idempotency_key':'approve','host_id':self.host,'reason':'stale','expected_revision':1,'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest'],'actions':[{'candidate_index':0,'action':'NO_ACTION','payload':{}}]},actor_id=self.human,scopes=self.scopes)
        self.assertEqual(exc.exception.code,'interpretation_not_current')
    def test_rerun_exact_quote_metadata_and_no_truth(self):
        sources=[];old=self.fake.action
        self.fake.action=lambda source:(sources.append(source) or old(source))
        out=self.resolver.maintenance.change(self.ident,'rerun',{'idempotency_key':'rerun','host_id':self.host,'reason':'again','expected_revision':1},actor_id=self.human)
        self.assertEqual(sources[0]['text'],TEXT);self.assertEqual(sources[0]['operation_context']['type'],'rerun')
        self.assertEqual(out['interpretation']['status'],'EXTRACTED');self.assertEqual(out['interpretation']['maintenance_head']['revision'],3)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM interpretation_operation_completions').fetchone()[0],1)
    def test_uow_default_nested_and_rollback(self):
        with db.unit_of_work(self.conn) as token:
            with self.assertRaises(RuntimeError):
                with db.transaction(self.conn):pass
            with db.transaction(self.conn,unit_of_work=token):pass
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn,unit_of_work=token):pass
    def test_stale_context_and_duplicate_revisions(self):
        body=self.request();body['expected_revisions']*=2
        with self.assertRaises(errors.InvalidRequest):self.resolver.resolve(self.ident,body,actor_id=self.human)
        body=self.request();body['expected_context_digest']='0'*64
        with self.assertRaises(Conflict):self.resolver.resolve(self.ident,body,actor_id=self.human)
    def test_unready_and_operator_kind(self):
        self.resolver.actor_id=self.agent
        with self.assertRaises(errors.ForbiddenActorKind):self.resolver.resolve(self.ident,self.request(),actor_id=self.human)

    def test_task_null_manual_supersede_uses_derived_binding(self):
        first=self.resolver.resolve(self.ident,self.request(),actor_id=self.human)['resolution']['result']
        rule=next(a for a in first['actions'] if a['target_type']=='rule');task=next(a for a in first['actions'] if a['target_type']=='task')['target_id']
        result=output(self.interpretations._source(self.event));result['candidates'][0].update(kind='CORRECTION',content='Use corrected requirement')
        child=self.resolver.maintenance.change(self.ident,'correct',{'idempotency_key':'correct','host_id':self.host,'reason':'fix','expected_revision':1,'result':result},actor_id=self.human)['result']['child_interpretation_id']
        preview=self.resolver.preview(child);self.assertTrue(any(x['object_id']==task for x in preview['expected_revisions']))
        body={'idempotency_key':'manual','host_id':self.host,'reason':'replace','current_interpretation_id':child,'expected_head_revision':2,'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest'],'expected_latest_resolution_id':first['resolution_id'],'actions':[{'candidate_index':0,'action':'SUPERSEDE_RULE','payload':{'target_rule_id':rule['target_id'],'expected_revision':2,'kind':'RULE','severity':'NORMAL','enforcement':'CONTEXT','content':'Use corrected requirement','matcher':{}}}]}
        updated=self.reviews.manual_reapply(first['resolution_id'],body,actor_id=self.human,scopes=self.scopes)
        self.assertEqual(updated['resolution']['result']['actions'][0]['rule_version'],2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM source_application_changes').fetchone()[0],1)
        self.assertEqual(self.reviews.manual_reapply(first['resolution_id'],body,actor_id=self.human,scopes=self.scopes)['resolution']['result'],updated['resolution']['result'])
        self.assertIsNone(self.interpretations._get(child)['task_id'])
    def test_dead_rerun_recovers_after_manual_head_change(self):
        def crash(source):raise SystemExit('simulate process death after reservation commit')
        self.fake.action=crash
        with self.assertRaises(SystemExit):self.resolver.maintenance.change(self.ident,'rerun',{'idempotency_key':'dead','host_id':self.host,'reason':'dead','expected_revision':1},actor_id=self.human)
        pending=self.resolver.maintenance.head(self.ident);self.assertEqual(pending['status'],'RERUN_PENDING')
        self.resolver.maintenance.change(self.ident,'reject',{'idempotency_key':'reject','host_id':self.host,'reason':'stop','expected_revision':2},actor_id=self.human)
        # Deadline is already immutable; advance the recovery clock rather than rewriting it.
        with patch('jasmine_core.interpretations.clock.now_rfc3339',return_value='2099-01-01T00:00:00Z'):
            self.resolver.maintenance.get(self.ident)
        self.assertEqual(self.resolver.maintenance.head(self.ident)['status'],'REJECTED')
        completion=self.conn.execute('SELECT * FROM interpretation_operation_completions').fetchone()
        self.assertEqual(completion['processing_status'],'INTERRUPTED');self.assertEqual(completion['head_applied'],0)
    def test_pending_cross_processor_approve_source_snapshot(self):
        def high(source):
            value=output(source);value['candidates'][0].update(kind='RULE',impact='HIGH',scope={'kind':'GLOBAL','project_id':None,'task_id':None,'path':None,'tool':None});return json.dumps(value)
        self.fake.action=high
        ints=[self.interpretations.process({'event_id':self.event,'idempotency_key':'high'+a},actor_id=a)['interpretation']['interpretation_id'] for a in (self.system,self.agent)]
        pending=[self.resolver.resolve(i,self.request('pending'+i,i),actor_id=self.human)['resolution']['result'] for i in ints]
        previews=[self.resolver.preview(i) for i in ints]
        results=[]
        for index,p in enumerate(pending):
            body={'idempotency_key':'approve'+str(index),'host_id':self.host,'reason':'approve','expected_revision':1,'expected_revisions':previews[index]['expected_revisions'],'expected_context_digest':previews[index]['expected_context_digest'],'actions':[{'candidate_index':0,'action':'CREATE_RULE','payload':{}}]}
            results.append(self.reviews.change(p['review_id'],'approve',body,actor_id=self.human,scopes=self.scopes)['result'])
        self.assertEqual(results[0]['disposition'],'APPLIED');self.assertEqual(results[1]['disposition'],'SOURCE_REPLAYED');self.assertEqual(results[1]['actions'],results[0]['actions'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM rules').fetchone()[0],1)
        self.assertEqual(self.resolver.get(pending[0]['resolution_id'])['result']['disposition'],'PENDING_REVIEW')
    def test_mixed_pending_no_partial_truth_and_correction_pending(self):
        source=self.interpretations._source(self.event);result=output(source);task=copy.deepcopy(result['candidates'][0]);task.update(kind='TASK_CREATE_OR_ATTACH',content='new task');result['candidates'].append(task);result['candidates'][0]['kind']='CORRECTION'
        child=self.resolver.maintenance.change(self.ident,'correct',{'idempotency_key':'mix','host_id':self.host,'reason':'mix','expected_revision':1,'result':result},actor_id=self.human)['result']['child_interpretation_id']
        pending=self.resolver.resolve(child,self.request('mix',child),actor_id=self.human)['resolution']['result']
        self.assertEqual(pending['disposition'],'PENDING_REVIEW');self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_failed_provider_not_ready(self):
        self.fake.action=lambda source:'{}'
        failed=self.interpretations.process({'event_id':self.event,'idempotency_key':'failed'},actor_id=self.system)['interpretation']['interpretation_id']
        with self.assertRaises(Conflict) as exc:self.resolver.preview(failed)
        self.assertEqual(exc.exception.code,'interpretation_not_ready')

    def test_source_actor_home_host_mismatch_cannot_apply(self):
        other_host=ids.new_id('hst'); other_actor=ids.new_id('act');registry=Registry(self.conn)
        registry.upsert_host(other_host);registry.upsert_actor(other_actor,kind='human',home_host_id=other_host)
        raw=EventStore(self.conn,schema_version=SCHEMA_VERSION).append(NewEvent(event_type='user.prompt',source_system='component',occurred_at=self.clock.now(),actor_id=other_actor,actor_kind='human',host_id=self.host,project_id=self.project,payload={'text':TEXT}))[0]
        ident=self.interpretations.process({'event_id':raw['event_id'],'idempotency_key':'mismatch'},actor_id=self.human)['interpretation']['interpretation_id']
        with self.assertRaises(errors.ActorMismatch):self.resolver.resolve(ident,self.request('mismatch',ident),actor_id=self.human)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM resolutions').fetchone()[0],0)

    def _new_correction(self, task_id, *, kind='CORRECTION'):
        raw=EventStore(self.conn,schema_version=SCHEMA_VERSION).append(NewEvent(event_type='user.prompt',source_system='component-correction',occurred_at=self.clock.now(),actor_id=self.human,actor_kind='human',host_id=self.host,project_id=self.project,task_id=task_id,payload={'text':'Change the requirement to the corrected capability.'}))[0]
        def correction(source):
            result=output(source);result['candidates'][0].update(kind=kind,content='Corrected capability',impact='MEDIUM');return json.dumps(result)
        self.fake.action=correction
        ident=self.interpretations.process({'event_id':raw['event_id'],'idempotency_key':'correction'+raw['event_id']},actor_id=self.human)['interpretation']['interpretation_id']
        pending=self.resolver.resolve(ident,self.request('correction'+ident,ident),actor_id=self.human)['resolution']['result']
        return raw,ident,pending
    def _approve_correction_body(self, ident, target):
        preview=self.resolver.preview(ident)
        return {'idempotency_key':'approve'+ident,'host_id':self.host,'reason':'explicit correction','expected_revision':1,'expected_revisions':preview['expected_revisions'],'expected_context_digest':preview['expected_context_digest'],'actions':[{'candidate_index':0,'action':'SUPERSEDE_RULE','payload':{'target_rule_id':target['target_id'],'expected_revision':target['after_revision'],'kind':'RULE','severity':'NORMAL','enforcement':'CONTEXT','content':'Corrected capability','matcher':{}}}]}
    def test_new_source_same_task_correction_review_preserves_both_sources(self):
        first=self.resolver.resolve(self.ident,self.request(),actor_id=self.human)['resolution']['result']
        task=next(a['target_id'] for a in first['actions'] if a['target_type']=='task');rule=next(a for a in first['actions'] if a['target_type']=='rule')
        old=self.resolver.authority.get(rule['target_id'])
        raw,ident,pending=self._new_correction(task)
        self.assertEqual(pending['disposition'],'PENDING_REVIEW');self.assertEqual(self.resolver.authority.get(rule['target_id'])['version'],1)
        body=self._approve_correction_body(ident,rule)
        with self.assertRaises(errors.ForbiddenScope):self.reviews.change(pending['review_id'],'approve',body,actor_id=self.human,scopes={'authority:propose'})
        approved=self.reviews.change(pending['review_id'],'approve',body,actor_id=self.human,scopes=self.scopes)
        action=approved['result']['actions'][0];self.assertEqual(action['rule_version'],2);self.assertEqual(action['previous_rule_version'],1)
        self.assertEqual(action['previous_origin_event_id'],old['origin_event_id']);self.assertEqual(action['previous_source_event_id'],self.event)
        current=self.resolver.authority.get(rule['target_id']);self.assertEqual(current['scope']['task_id'],task)
        origin=self.resolver.events.get(current['origin_event_id']);self.assertEqual(origin['event_type'],'review.approved');self.assertEqual(origin['payload']['source_event_id'],raw['event_id'])
        self.assertEqual(self.resolver.authority.get(rule['target_id'],version=1)['origin_event_id'],old['origin_event_id'])
        with self.assertRaises(Conflict):self.reviews.manual_reapply(first['resolution_id'],{'idempotency_key':'not-manual','host_id':self.host,'reason':'must not bypass','current_interpretation_id':ident,'expected_head_revision':1,'expected_revisions':body['expected_revisions'],'expected_context_digest':body['expected_context_digest'],'expected_latest_resolution_id':first['resolution_id'],'actions':body['actions']},actor_id=self.human,scopes=self.scopes)
    def test_new_source_correction_other_task_rejected(self):
        first=self.resolver.resolve(self.ident,self.request(),actor_id=self.human)['resolution']['result'];rule=next(a for a in first['actions'] if a['target_type']=='rule')
        other=self.objects.create(NewObject.task({'title':'other','project_id':self.project,'host_id':self.host}),actor_id=self.human)['object']['task_id']
        raw,ident,pending=self._new_correction(other)
        with self.assertRaises(errors.InvalidRequest):self.reviews.change(pending['review_id'],'approve',self._approve_correction_body(ident,rule),actor_id=self.human,scopes=self.scopes)
        self.assertEqual(self.resolver.authority.get(rule['target_id'])['version'],1)
        self.assertEqual(self.reviews.get(pending['review_id'])['status'],'PENDING')
    def test_new_source_noncorrection_cannot_target_other_source_rule(self):
        first=self.resolver.resolve(self.ident,self.request(),actor_id=self.human)['resolution']['result'];rule=next(a for a in first['actions'] if a['target_type']=='rule');task=next(a['target_id'] for a in first['actions'] if a['target_type']=='task')
        raw,ident,pending=self._new_correction(task,kind='RULE')
        with self.assertRaises(errors.InvalidRequest):self.reviews.change(pending['review_id'],'approve',self._approve_correction_body(ident,rule),actor_id=self.human,scopes=self.scopes)
        self.assertEqual(self.resolver.authority.get(rule['target_id'])['version'],1)

    def test_provider_cleanup_denial_is_persisted_without_losing_primary_failure(self):
        from jasmine_core.interpreter_provider import ProviderFailure
        def denied(source):
            failure=ProviderFailure('provider_output_too_large','bounded invalid output')
            failure.cleanup_errors=('cleanup_permission_denied','cleanup_incomplete')
            raise failure
        self.fake.action=denied
        result=self.interpretations.process({'event_id':self.event,'idempotency_key':'cleanup-denied'},actor_id=self.system)['interpretation']
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['error_code'],'provider_output_too_large:cleanup_permission_denied,cleanup_incomplete')
        self.assertEqual(result['raw_result_json'],'bounded invalid output')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
