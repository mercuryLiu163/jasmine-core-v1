"""Atomic Event-first resolutions using the original P1 business kernels."""
import json
import os
from . import clock, db, errors, ids
from .authority import AuthorityStore
from .objects import ObjectStore
from .state import StateStore
from .models import NewObject
from .resolution_policy import compile_plan, POLICY_VERSION
from .resolution_common import fields, key, actor, command, digest, revision, Conflict, bounded
from .canonical import canonical_json
from .interpretation_maintenance import MaintenanceStore

class ResolverStore:
    def __init__(self, interpretations, *, execution_actor_id=None, execution_host_id=None):
        self.interpretations=interpretations; self.conn=interpretations.conn
        self.maintenance=MaintenanceStore(interpretations); self.events=self.maintenance.events
        self.actor_id=execution_actor_id or os.environ.get('JASMINE_CORE_RESOLVER_ACTOR_ID')
        self.host_id=execution_host_id or os.environ.get('JASMINE_CORE_RESOLVER_HOST_ID')
        self.objects=ObjectStore(self.conn,schema_version=interpretations.schema_version)
        self.authority=AuthorityStore(self.conn,schema_version=interpretations.schema_version)
        self.state=StateStore(self.conn,schema_version=interpretations.schema_version)

    def _executor(self, source, caller_id, host_id):
        who=actor(self.conn,self.actor_id,self.host_id)
        if who['kind']!='system':raise errors.ForbiddenActorKind('operator resolver principal must be system')
        actor(self.conn,caller_id,host_id)
        actor(self.conn,source['actor_id'],source['host_id'])
        if host_id!=self.host_id or source['host_id']!=self.host_id:
            raise errors.ActorMismatch('source, caller and executor host must agree')
        return who

    def bound_task(self, source):
        if source['task_id']:return source['task_id'],None
        canonical=self._canonical(source)
        if not canonical:return None,None
        row=self.conn.execute('SELECT b.* FROM resolution_source_bindings b WHERE b.source_event_id=? AND b.resolution_id=?',(source['event_id'],canonical)).fetchone()
        if not row:return None,None
        task=self.state.task(row['task_id']);event=self.events.get(row['binding_event_id'])
        if task['project_id']!=source['project_id'] or event['task_id']!=task['task_id'] or event['payload'].get('source_event_id')!=source['event_id'] or event['payload'].get('resolution_id')!=canonical:
            raise errors.InvalidRequest('invalid trusted source binding')
        return task['task_id'],row['binding_event_id']

    def _context(self, source, ident):
        objects=[]; rules=[]
        if source['project_id']:
            project=self.objects.get('project',source['project_id'])
            objects.append({'object_type':'project','object_id':source['project_id'],'revision':project['revision']})
        task,binding=self.bound_task(source)
        if task:
            t=self.state.task(task); objects.append({'object_type':'task','object_id':task,'revision':t['revision']})
            steps=[dict(r) for r in self.conn.execute('SELECT * FROM steps WHERE task_id=? ORDER BY step_id',(task,))]
            for step in steps:objects.append({'object_type':'step','object_id':step['step_id'],'revision':step['revision']})
        else:t=None;steps=[]
        for row in self.conn.execute("SELECT * FROM rules WHERE scope_kind='global' OR (project_id=? AND (scope_kind='project' OR task_id=?)) ORDER BY rule_id",(source['project_id'],task)):
            objects.append({'object_type':'rule','object_id':row['rule_id'],'revision':row['revision']})
            rules.append(self.authority.get(row['rule_id']))
        if len(objects)>512:raise Conflict('context_too_large')
        objects.sort(key=lambda r:(r['object_type'],r['object_id']))
        context={'objects':objects,'task':t,'steps':steps,'rules':rules,'head':self.maintenance.head(ident),'binding_event_id':binding}
        try:bounded(context)
        except errors.InvalidRequest:raise Conflict('context_too_large')
        return objects,digest(context)

    def _validate_revisions(self, body):
        values=body.get('expected_revisions')
        if not isinstance(values,list) or len(values)>512:raise errors.InvalidRequest('expected_revisions must be bounded array')
        seen=set()
        for row in values:
            fields(row,{'object_type','object_id','revision'},{'object_type','object_id','revision'})
            if not isinstance(row['object_type'],str):raise errors.InvalidRequest('invalid revision member')
            prefix={'project':'prj','task':'tsk','step':'stp','rule':'rul'}.get(row['object_type'])
            if not prefix or not ids.is_id(row['object_id'],prefix):raise errors.InvalidRequest('invalid revision member')
            revision(row['revision']); pair=(row['object_type'],row['object_id'])
            if pair in seen:raise errors.InvalidRequest('duplicate revision member')
            seen.add(pair)
        if not isinstance(body.get('expected_context_digest'),str) or len(body['expected_context_digest'])!=64 or any(c not in '0123456789abcdef' for c in body['expected_context_digest']):
            raise errors.InvalidRequest('invalid context digest')
        return sorted(values,key=lambda r:(r['object_type'],r['object_id']))

    def _check_context(self, body, source, ident):
        ordered=self._validate_revisions(body)
        current,hashed=self._context(source,ident)
        if ordered!=current or body.get('expected_context_digest')!=hashed:raise Conflict('context_conflict',current_revisions=current,current_context_digest=hashed)
        return current,hashed

    def _preview(self, ident):
        row=self.maintenance.check_current(ident); source=self.interpretations._source(row['event_id'])
        plan=compile_plan(source,row['candidates']); values,hashed=self._context(source,ident)
        canonical=self.conn.execute('SELECT canonical_resolution_id FROM source_applications WHERE source_event_id=?',(row['event_id'],)).fetchone()
        return {'interpretation_id':ident,'source_event_id':row['event_id'],'policy_version':POLICY_VERSION,'plan':plan,
                'expected_revisions':values,'expected_context_digest':hashed,'maintenance_head':self.maintenance.head(ident),
                'source_application':dict(canonical) if canonical else None}

    def preview(self, ident):
        self.maintenance.recover(ident)
        if self.conn.in_transaction:
            return self._preview(ident)
        self.conn.execute('BEGIN')
        try:
            result=self._preview(ident)
            self.conn.execute('COMMIT')
            return result
        except BaseException:
            if self.conn.in_transaction:self.conn.execute('ROLLBACK')
            raise

    def get(self, ident):
        row=self.conn.execute('SELECT * FROM resolutions WHERE resolution_id=?',(ident,)).fetchone()
        if not row:raise errors.NotFound('resolution',ident)
        result=self.conn.execute('SELECT result_json FROM resolution_results WHERE resolution_id=?',(ident,)).fetchone()
        value=dict(row)
        for name in ('expected_context_json','plan_json'):value[name[:-5]]=json.loads(value.pop(name))
        value['result']=json.loads(result['result_json'])
        value['manual_application_history']=[dict(r) for r in self.conn.execute('SELECT * FROM source_application_changes WHERE source_event_id=? ORDER BY created_at,new_resolution_id',(row['source_event_id'],))]
        return value

    def applied_result(self, canonical_id):
        result=self.get(canonical_id)['result']
        if result['disposition']=='APPLIED':return result
        for row in self.conn.execute("SELECT c.result_json FROM review_changes c JOIN reviews v USING(review_id) WHERE v.resolution_id=? AND c.action='APPROVE' ORDER BY c.to_revision",(canonical_id,)):
            applied=json.loads(row['result_json'])
            if applied['disposition']=='APPLIED':return applied
        raise Conflict('application_conflict','canonical source has no applied snapshot')

    def _canonical(self, source):
        row=self.conn.execute('SELECT canonical_resolution_id FROM source_applications WHERE source_event_id=?',(source['event_id'],)).fetchone()
        return row['canonical_resolution_id'] if row else None

    def _apply(self, ident, source, plan, resolution_id, executor, host, uow, *, review_origin=None):
        task_id,binding=self.bound_task(source); origin=binding or review_origin or source['event_id']; actions=[]
        # Exactly one Task per complete source, even with multiple task candidates.
        for item in plan['items']:
            if item['action']!='CREATE_TASK' or task_id:continue
            candidate=item['candidate']
            created=self.objects.create(NewObject.task({'title':candidate['content'][:500], 'description':candidate['content'],
                'project_id':source['project_id'],'host_id':host,'source_system':'core-resolver'}),actor_id=executor,unit_of_work=uow)
            task_id=created['object']['task_id']
            scoped={**source,'task_id':task_id}
            event=command(self.events,scoped,executor,self.conn.execute('SELECT kind FROM actors WHERE actor_id=?',(executor,)).fetchone()['kind'],host,'interpretation.bound',
                {'source_event_id':source['event_id'],'interpretation_id':ident,'resolution_id':resolution_id,'task_id':task_id})
            binding=origin=event['event_id']
            self.conn.execute('INSERT INTO resolution_source_bindings VALUES(?,?,?,?,?)',(binding,source['event_id'],ident,resolution_id,task_id))
            actions.append(self._action_result(item,'task',task_id,None,created['object']['revision'],None,[created['event']['event_id']],binding))
        for item in plan['items']:
            action=item['action']; candidate=item['candidate']; payload=item.get('payload',{})
            if action=='CREATE_TASK':
                if not any(a['candidate_index']==item['candidate_index'] for a in actions):
                    t=self.state.task(task_id)
                    actions.append(self._action_result({**item,'action':'ATTACH_TASK'},'task',task_id,t['revision'],t['revision'],None,[],binding))
                continue
            if action=='NO_ACTION':
                actions.append(self._action_result(item,None,None,None,None,None,[],binding));continue
            if action=='ATTACH_TASK':
                if not task_id:raise errors.InvalidRequest('task binding required')
                t=self.state.task(task_id);actions.append(self._action_result(item,'task',task_id,t['revision'],t['revision'],None,[],binding));continue
            if action=='CREATE_RULE':
                candidate_scope=candidate['scope']
                scope={'kind':candidate_scope['kind'].lower(),'project_id':candidate_scope['project_id'],'task_id':task_id if candidate_scope['kind']=='TASK' else None}
                if candidate_scope['kind']=='TASK' and not task_id:raise errors.InvalidRequest('task binding required for requirement')
                item_origin=origin
                if review_origin and (scope['kind']!='task' or source['task_id']!=task_id):
                    scoped={**source,'project_id':scope['project_id'],'task_id':scope['task_id']}
                    item_origin=command(self.events,scoped,executor,self.conn.execute('SELECT kind FROM actors WHERE actor_id=?',(executor,)).fetchone()['kind'],host,'review.approved',{'review_command_event_id':review_origin,'source_event_id':source['event_id'],'interpretation_id':ident,'resolution_id':resolution_id,'candidate_index':item['candidate_index']})['event_id']
                proposal=self.authority.propose({'rule_key':'p2-'+resolution_id[4:].lower()+'-'+str(item['candidate_index']),
                    'kind':'DECISION' if candidate['kind']=='DECISION' else 'RULE','severity':'NORMAL','enforcement':'CONTEXT','content':candidate['content'],'matcher':{},
                    'scope':scope,'origin_event_id':item_origin,'host_id':host,**payload},actor_id=executor,unit_of_work=uow)
                approved=self.authority.transition(proposal['rule']['rule_id'],'approve',{'expected_revision':1,'host_id':host},actor_id=executor,unit_of_work=uow)
                rule=approved['rule'];actions.append(self._action_result(item,'rule',rule['rule_id'],None,rule['revision'],rule['version'],[proposal['event']['event_id'],approved['event']['event_id']],binding));continue
            if action=='SUPERSEDE_RULE':
                target=payload['target_rule_id']; old=self.authority.get(target)
                item_origin=origin
                if review_origin and (old['scope']['project_id']!=source['project_id'] or old['scope']['task_id']!=source['task_id']):
                    scoped={**source,'project_id':old['scope']['project_id'],'task_id':old['scope']['task_id']}
                    item_origin=command(self.events,scoped,executor,self.conn.execute('SELECT kind FROM actors WHERE actor_id=?',(executor,)).fetchone()['kind'],host,'review.approved',{'review_command_event_id':review_origin,'source_event_id':source['event_id'],'interpretation_id':ident,'resolution_id':resolution_id,'candidate_index':item['candidate_index']})['event_id']
                output=self.authority.transition(target,'supersede',{'expected_revision':payload['expected_revision'],'host_id':host,
                    'kind':'DECISION' if candidate['kind']=='DECISION' else 'RULE','severity':'NORMAL','enforcement':'CONTEXT','content':candidate['content'],'matcher':{},
                    'scope':{'kind':old['scope']['kind'],'project_id':old['scope']['project_id'],'task_id':old['scope']['task_id']},'origin_event_id':item_origin,**{k:v for k,v in payload.items() if k not in ('target_rule_id','expected_revision')}},actor_id=executor,unit_of_work=uow)
                rule=output['rule'];action_result=self._action_result(item,'rule',target,old['revision'],rule['revision'],rule['version'],[output['event']['event_id']],binding)
                old_origin=self.events.get(old['origin_event_id'])
                action_result.update(previous_origin_event_id=old['origin_event_id'],previous_rule_version=old['version'],previous_source_event_id=old_origin['payload'].get('source_event_id',old['origin_event_id']))
                actions.append(action_result);continue
            if action in ('SET_TASK_CRITERIA','SET_STEP_CRITERIA'):
                target=payload['target_id'];kind='task' if action=='SET_TASK_CRITERIA' else 'step'
                old=self.state.task(target) if kind=='task' else self.state.step(target)
                method=self.state.set_task_criteria if kind=='task' else self.state.set_step_criteria
                out=method(target,{'expected_revision':payload['expected_revision'],'host_id':host,'acceptance_criteria':payload['acceptance_criteria']},actor_id=executor,actor_kind=self.conn.execute('SELECT kind FROM actors WHERE actor_id=?',(executor,)).fetchone()['kind'],can_accept=True,unit_of_work=uow)
                actions.append(self._action_result(item,kind,target,old['revision'],out[kind]['revision'],None,[out['event']['event_id']],binding));continue
            raise errors.InvalidRequest('unsupported manual action')
        return actions

    @staticmethod
    def _action_result(item,kind,ident,before,after,version,events,binding):
        return {'candidate_index':item['candidate_index'],'action':item['action'],'target_type':kind,'target_id':ident,
            'before_revision':before,'after_revision':after,'rule_version':version,'change_event_ids':events,'binding_event_id':binding}

    def resolve(self, ident, body, *, actor_id):
        fields(body,{'idempotency_key','host_id','policy_version','expected_revisions','expected_context_digest'}, {'idempotency_key','host_id','policy_version','expected_revisions','expected_context_digest'})
        self._validate_revisions(body)
        request_key=key(body);hashed=digest({'interpretation_id':ident,'body':body})
        prior=self.conn.execute('SELECT * FROM resolution_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,request_key)).fetchone()
        if prior:
            if prior['request_hash']!=hashed:raise Conflict('idempotency_conflict')
            return {'resolution':self.get(prior['resolution_id']),'replayed':True}
        self.maintenance.recover(ident)
        with db.unit_of_work(self.conn) as uow:
            # Recheck after acquiring the sole writer lock.
            prior=self.conn.execute('SELECT * FROM resolution_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,request_key)).fetchone()
            if prior:
                if prior['request_hash']!=hashed:raise Conflict('idempotency_conflict')
                return {'resolution':self.get(prior['resolution_id']),'replayed':True}
            row=self.maintenance.check_current(ident);source=self.interpretations._source(row['event_id'])
            if body['policy_version']!=POLICY_VERSION:raise errors.InvalidRequest('unknown policy version')
            who=self._executor(source,actor_id,body['host_id']); canonical=self._canonical(source)
            plan=compile_plan(source,row['candidates'])
            if canonical:context=body['expected_revisions'];context_hash=body['expected_context_digest']
            else:context,context_hash=self._check_context(body,source,ident)
            rid=ids.new_id('res');at=clock.now_rfc3339()
            event=command(self.events,source,actor_id,self.conn.execute('SELECT kind FROM actors WHERE actor_id=?',(actor_id,)).fetchone()['kind'],body['host_id'],'resolution.command',{'interpretation_id':ident,'resolution_id':rid,'policy_version':POLICY_VERSION,'plan':plan})
            self.conn.execute('INSERT INTO resolutions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,source['event_id'],ident,event['event_id'],actor_id,POLICY_VERSION,hashed,canonical_json(context),context_hash,canonical_json(plan),at))
            disposition='SOURCE_REPLAYED' if canonical else plan['disposition']; review_id=None; actions=[]
            if canonical:actions=self.applied_result(canonical)['actions']
            elif disposition=='APPLIED':
                actions=self._apply(ident,source,plan,rid,self.actor_id,self.host_id,uow)
                self.conn.execute('INSERT INTO source_applications VALUES(?,?,?)',(source['event_id'],rid,at))
            elif disposition=='PENDING_REVIEW':
                review_id=ids.new_id('rvw');self.conn.execute("INSERT INTO reviews VALUES(?,?,'PENDING',1,?,?)",(review_id,rid,at,at))
            result={'resolution_id':rid,'disposition':disposition,'source_event_id':source['event_id'],'interpretation_id':ident,
                'command_event_id':event['event_id'],'execution_actor_id':self.actor_id if disposition=='APPLIED' else None,
                'review_actor_id':None,'review_command_event_id':None,'canonical_resolution_id':canonical,'latest_application_id':canonical or (rid if disposition=='APPLIED' else None),
                'actions':actions,'review_id':review_id,'policy_version':POLICY_VERSION,'reasons':[i['reason'] for i in plan['items']],
                'source_bindings':[dict(r) for r in self.conn.execute('SELECT * FROM resolution_source_bindings WHERE resolution_id=?',(rid,))]}
            self.conn.execute('INSERT INTO resolution_results VALUES(?,?,?,?)',(rid,disposition,canonical_json(result),at))
            self.conn.execute('INSERT INTO resolution_request_keys VALUES(?,?,?,?)',(actor_id,request_key,hashed,rid))
            return {'resolution':self.get(rid),'replayed':False}
