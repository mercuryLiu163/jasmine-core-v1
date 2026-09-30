"""Human review CAS, source dedup and explicit manual reapplication."""
import json
from . import clock, db, errors, ids
from .canonical import canonical_json
from .resolution_common import fields, key, reason, revision, actor, command, digest, Conflict

class ReviewStore:
    def __init__(self,resolver):
        self.resolver=resolver;self.conn=resolver.conn

    def get(self, ident):
        row=self.conn.execute('SELECT * FROM reviews WHERE review_id=?',(ident,)).fetchone()
        if not row:raise errors.NotFound('review',ident)
        resolution=self.resolver.get(row['resolution_id'])
        self.resolver.maintenance.recover(resolution['interpretation_id'])
        out=dict(row);out['resolution']=resolution
        out['history']=[{**dict(r),'result':json.loads(r['result_json'])} for r in self.conn.execute('SELECT * FROM review_changes WHERE review_id=? ORDER BY to_revision',(ident,))]
        try:out['preview']=self.resolver.preview(resolution['interpretation_id'])
        except Conflict as exc:out['preview_error']={'code':exc.code,'details':exc.details}
        return out

    def list(self, *, limit=50, after_id=None, project_id=None, task_id=None):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200:raise errors.InvalidRequest('invalid limit')
        clauses=["v.status='PENDING'"];params=[]
        for name,value,prefix in [('project_id',project_id,'prj'),('task_id',task_id,'tsk')]:
            if value is not None:
                if not ids.is_id(value,prefix):raise errors.InvalidRequest('invalid filter ID')
                clauses.append('i.'+name+'=?');params.append(value)
        if after_id:
            previous=self.get(after_id);clauses.append('(v.created_at,v.review_id)>(?,?)');params.extend([previous['created_at'],after_id])
        rows=self.conn.execute('SELECT v.review_id FROM reviews v JOIN resolutions r USING(resolution_id) JOIN interpretations i USING(interpretation_id) WHERE '+' AND '.join(clauses)+' ORDER BY v.created_at,v.review_id LIMIT ?',params+[limit+1]).fetchall()
        items=[self.get(r['review_id']) for r in rows[:limit]]
        return {'items':items,'next_after_id':items[-1]['review_id'] if len(rows)>limit else None}

    def _plan(self, original, supplied, source, scopes):
        if not isinstance(supplied,list) or len(supplied)!=len(original['items']) or len(supplied)>16:raise errors.InvalidRequest('actions must cover each candidate')
        mapped={}
        for action in supplied:
            fields(action,{'candidate_index','action','payload'},{'candidate_index','action','payload'})
            index=action['candidate_index']
            if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(original['items']) or index in mapped:raise errors.InvalidRequest('duplicate or invalid candidate index')
            if not isinstance(action['action'],str) or not isinstance(action['payload'],dict):raise errors.InvalidRequest('invalid action shape')
            mapped[index]=action
        plan={'policy_version':original['policy_version'],'disposition':'APPLIED','items':[]}
        for item in original['items']:
            supplied=mapped[item['candidate_index']]; act=supplied['action']; payload=supplied['payload'];candidate=item['candidate']; scope=candidate['scope']
            if scope['kind']=='GLOBAL':
                if scope['project_id'] is not None or scope['task_id'] is not None:raise errors.InvalidRequest('invalid global scope')
            elif scope['project_id']!=source['project_id'] or (scope['kind']=='TASK' and scope['task_id']!=source['task_id']):raise errors.InvalidRequest('candidate scope changed')
            allowed={'NO_ACTION'}
            if candidate['kind']=='TASK_CREATE_OR_ATTACH':allowed|={'ATTACH_TASK' if source['task_id'] else 'CREATE_TASK'}
            elif candidate['kind'] in ('REQUIRED_CAPABILITY','RULE','CORRECTION','DECISION'):allowed|={'CREATE_RULE','SUPERSEDE_RULE'}
            elif candidate['kind']=='ACCEPTANCE':allowed|={'SET_TASK_CRITERIA','SET_STEP_CRITERIA'}
            if act not in allowed:raise errors.InvalidRequest('action does not map this candidate')
            required=({'objects:write'} if act=='CREATE_TASK' else {'authority:propose','authority:manage'} if act=='CREATE_RULE' else {'authority:manage'} if act=='SUPERSEDE_RULE' else {'state:accept'} if act.startswith('SET_') else set())
            if not required<=scopes:raise errors.ForbiddenScope('manual action requires P1 scopes',required=sorted(required))
            if act in ('NO_ACTION','ATTACH_TASK','CREATE_TASK'):fields(payload,set())
            elif act=='CREATE_RULE':
                fields(payload,{'kind','severity','enforcement','content','matcher'})
                if payload:
                    fields(payload,{'kind','severity','enforcement','content','matcher'},{'kind','severity','enforcement','content','matcher'})
                    if payload['content']!=candidate['content']:raise errors.InvalidRequest('manual rule content must map exact candidate')
                    from .authority import _content
                    _content(payload)
            elif act=='SUPERSEDE_RULE':
                fields(payload,{'target_rule_id','expected_revision','kind','severity','enforcement','content','matcher'},{'target_rule_id','expected_revision','kind','severity','enforcement','content','matcher'});revision(payload['expected_revision'])
                if payload['content']!=candidate['content']:raise errors.InvalidRequest('manual content must map exact candidate')
                from .authority import _content
                _content(payload)
                rule=self.resolver.authority.get(payload['target_rule_id'])
                bound_task,_=self.resolver.bound_task(source)
                if rule['scope']!={'kind':scope['kind'].lower(),'project_id':scope['project_id'],'task_id':bound_task if scope['kind']=='TASK' else None}:raise errors.InvalidRequest('supersede scope mismatch')
                linked=[]
                canonical=self.resolver._canonical(source)
                if canonical:linked.append(self.resolver.applied_result(canonical))
                for row in self.conn.execute('SELECT new_resolution_id FROM source_application_changes WHERE source_event_id=?',(source['event_id'],)):
                    linked.append(self.resolver.get(row['new_resolution_id'])['result'])
                if not any(any(a['target_id']==rule['rule_id'] for a in result['actions']) for result in linked):raise errors.InvalidRequest('supersede target must belong to source application')
            else:
                fields(payload,{'target_id','expected_revision','acceptance_criteria'},{'target_id','expected_revision','acceptance_criteria'});revision(payload['expected_revision'])
                target=payload['target_id'];task,_=self.resolver.bound_task(source)
                if not task:raise errors.InvalidRequest('criteria requires source Task')
                if act=='SET_TASK_CRITERIA' and target!=task:raise errors.InvalidRequest('criteria target mismatch')
                if act=='SET_STEP_CRITERIA' and self.resolver.state.step(target)['task_id']!=task:raise errors.InvalidRequest('Step belongs to another Task')
                from .state import validate_criteria
                validate_criteria(payload['acceptance_criteria'])
            # P2 semantic manual mapper cannot author path/tool matcher or widen scope.
            if act not in ('NO_ACTION','ATTACH_TASK') and scope['kind'] not in ('TASK','PROJECT','GLOBAL'):raise errors.InvalidRequest('path/tool actions require independent P1 command')
            plan['items'].append({**item,'action':act,'payload':payload,'pending':False,'reason':'user_confirmed'})
        if all(i['action']=='NO_ACTION' for i in plan['items']):plan['disposition']='NO_ACTION'
        return plan

    def change(self, ident, action, body, *, actor_id, scopes):
        allowed={'idempotency_key','host_id','reason','expected_revision'}|({'expected_revisions','expected_context_digest','actions'} if action=='approve' else set())
        fields(body,allowed,allowed);k=key(body);reason(body);expected=revision(body['expected_revision']);h=digest({'review_id':ident,'action':action,'body':body})
        prior=self.conn.execute('SELECT * FROM review_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,k)).fetchone()
        if prior:
            if prior['request_hash']!=h:raise Conflict('idempotency_conflict')
            return {'result':json.loads(prior['result_json']),'replayed':True}
        review=self.get(ident);intid=review['resolution']['interpretation_id'];self.resolver.maintenance.recover(intid)
        with db.unit_of_work(self.conn) as uow:
            prior=self.conn.execute('SELECT * FROM review_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,k)).fetchone()
            if prior:
                if prior['request_hash']!=h:raise Conflict('idempotency_conflict')
                return {'result':json.loads(prior['result_json']),'replayed':True}
            row=self.conn.execute('SELECT * FROM reviews WHERE review_id=?',(ident,)).fetchone()
            if row['status']!='PENDING' or row['revision']!=expected:raise Conflict('revision_conflict')
            who=actor(self.conn,actor_id,body['host_id'],human=True)
            interpretation=self.resolver.interpretations._get(intid);source=self.resolver.interpretations._source(interpretation['event_id'])
            actor(self.conn,source['actor_id'],source['host_id'])
            canonical=None;actions=[];disposition='REJECTED';plan=review['resolution']['plan']
            if action=='approve':
                self.resolver.maintenance.check_current(intid)
                canonical=self.resolver._canonical(source)
                self.resolver._validate_revisions(body)
                validated_plan=self._plan(plan,body['actions'],source,set(scopes))
                if canonical:disposition='SOURCE_REPLAYED';actions=self.resolver.applied_result(canonical)['actions']
                else:
                    self.resolver._check_context(body,source,intid)
                    plan=validated_plan;disposition=plan['disposition']
            event=command(self.resolver.events,source,actor_id,who['kind'],body['host_id'],'review.approved' if action=='approve' else 'review.rejected',{'review_id':ident,'reason':body['reason'],'source_event_id':source['event_id'],'interpretation_id':intid,'plan':plan,'provenance':'USER_CONFIRMED' if action=='approve' else None})
            if action=='approve' and not canonical and disposition=='APPLIED':
                actions=self.resolver._apply(intid,source,plan,review['resolution_id'],actor_id,body['host_id'],uow, review_origin=event['event_id'])
                self.conn.execute('INSERT INTO source_applications VALUES(?,?,?)',(source['event_id'],review['resolution_id'],event['recorded_at']))
            status='REJECTED' if action=='reject' else 'SOURCE_REPLAYED' if canonical else 'APPROVED'
            at=clock.now_rfc3339();result={'review_id':ident,'resolution_id':review['resolution_id'],'disposition':disposition,'actions':actions,'canonical_resolution_id':canonical,'source_event_id':source['event_id'],'interpretation_id':intid,'review_actor_id':actor_id,'review_command_event_id':event['event_id'],'revision':expected+1}
            self.conn.execute('UPDATE reviews SET status=?,revision=revision+1,updated_at=? WHERE review_id=?',(status,at,ident))
            self.conn.execute('INSERT INTO review_changes VALUES(?,?,?,?,?,?,?,?,?,?)',(event['event_id'],ident,expected,expected+1,action.upper(),actor_id,body['reason'],canonical_json(body.get('expected_revisions',[])),canonical_json(result),at))
            self.conn.execute('INSERT INTO review_request_keys VALUES(?,?,?,?,?)',(actor_id,k,h,event['event_id'],canonical_json(result)))
            return {'result':result,'replayed':False}

    def manual_reapply(self, canonical_id, body, *, actor_id, scopes):
        required={'idempotency_key','host_id','reason','current_interpretation_id','expected_head_revision','expected_revisions','expected_context_digest','expected_latest_resolution_id','actions'}
        fields(body,required,required);k=key(body);reason(body);revision(body['expected_head_revision'])
        h=digest({'canonical_resolution_id':canonical_id,'body':body})
        prior=self.conn.execute('SELECT * FROM resolution_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,k)).fetchone()
        if prior:
            if prior['request_hash']!=h:raise Conflict('idempotency_conflict')
            return {'resolution':self.resolver.get(prior['resolution_id']),'replayed':True}
        ident=body['current_interpretation_id'];self.resolver.maintenance.recover(ident)
        with db.unit_of_work(self.conn) as uow:
            prior=self.conn.execute('SELECT * FROM resolution_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,k)).fetchone()
            if prior:
                if prior['request_hash']!=h:raise Conflict('idempotency_conflict')
                return {'resolution':self.resolver.get(prior['resolution_id']),'replayed':True}
            who=actor(self.conn,actor_id,body['host_id'],human=True)
            row=self.resolver.maintenance.check_current(ident);source=self.resolver.interpretations._source(row['event_id'])
            actor(self.conn,source['actor_id'],source['host_id'])
            if self.resolver._canonical(source)!=canonical_id:raise Conflict('application_conflict')
            previous=canonical_id
            while True:
                nxt=self.conn.execute('SELECT new_resolution_id FROM source_application_changes WHERE source_event_id=? AND previous_resolution_id=?',(source['event_id'],previous)).fetchone()
                if not nxt:break
                previous=nxt['new_resolution_id']
            if previous!=body['expected_latest_resolution_id']:raise Conflict('application_conflict',latest_resolution_id=previous)
            if self.resolver.maintenance.head(ident)['revision']!=body['expected_head_revision']:raise Conflict('interpretation_not_current')
            context,context_hash=self.resolver._check_context(body,source,ident)
            from .resolution_policy import compile_plan, POLICY_VERSION
            plan=self._plan(compile_plan(source,row['candidates']),body['actions'],source,set(scopes))
            if any(i['action'] in ('CREATE_TASK','CREATE_RULE') for i in plan['items']):raise errors.InvalidRequest('manual reapplication must target existing application through supersede or criteria')
            rid=ids.new_id('res');at=clock.now_rfc3339()
            event=command(self.resolver.events,source,actor_id,who['kind'],body['host_id'],'resolution.command',{'resolution_id':rid,'source_event_id':source['event_id'],'interpretation_id':ident,'previous_resolution_id':previous,'reason':body['reason'],'provenance':'USER_CONFIRMED','plan':plan})
            self.conn.execute('INSERT INTO resolutions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,source['event_id'],ident,event['event_id'],actor_id,POLICY_VERSION,h,canonical_json(context),context_hash,canonical_json(plan),at))
            actions=self.resolver._apply(ident,source,plan,rid,actor_id,body['host_id'],uow,review_origin=event['event_id'])
            result={'resolution_id':rid,'disposition':plan['disposition'],'source_event_id':source['event_id'],'interpretation_id':ident,'command_event_id':event['event_id'],'execution_actor_id':actor_id,'review_actor_id':actor_id,'review_command_event_id':event['event_id'],'canonical_resolution_id':canonical_id,'latest_application_id':rid if plan['disposition']=='APPLIED' else previous,'actions':actions,'review_id':None,'policy_version':POLICY_VERSION,'reasons':['explicit_manual_reapplication'],'source_bindings':[]}
            self.conn.execute('INSERT INTO resolution_results VALUES(?,?,?,?)',(rid,plan['disposition'],canonical_json(result),at))
            self.conn.execute('INSERT INTO resolution_request_keys VALUES(?,?,?,?)',(actor_id,k,h,rid))
            if plan['disposition']=='APPLIED':
                self.conn.execute('INSERT INTO source_application_changes VALUES(?,?,?,?,?,?,?)',(source['event_id'],previous,rid,event['event_id'],body['reason'],actor_id,at))
            return {'resolution':self.resolver.get(rid),'replayed':False}
