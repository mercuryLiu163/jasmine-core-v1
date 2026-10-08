"""Explicit operator mapping of an applied P2 capability into P1 criteria."""
from .. import db,errors
from ..canonical import sha256_hex,canonical_json
from ..resolution_common import fields,key,revision,Conflict
from ..continuity_source import source_for_task
from .lifecycle import LifecycleStore
from .protocol import identifier,event_id,SOURCE

BIND={'idempotency_key','task_id','step_id','source_event_id','interpretation_id','resolution_id','candidate_index',
      'rule_id','rule_version','expected_task_revision','expected_step_revision'}


class NativeAdapterStore(LifecycleStore):
    def bind_requirement(self,body,*,principal):
        fields(body,BIND,BIND);key(body)
        for name,prefix in (('task_id','tsk'),('step_id','stp'),('source_event_id','evt'),('interpretation_id','int'),('resolution_id','res'),('rule_id','rul')):
            identifier(body[name],prefix)
        for name in ('rule_version','expected_task_revision','expected_step_revision'):revision(body[name])
        index=body['candidate_index']
        if type(index) is not int or not 0<=index<32:raise errors.InvalidRequest('bounded candidate index required')
        config=self._config();config.principal(self.conn,principal,'adapter:report')
        principal.require('authority:manage');principal.require('state:accept')
        digest=sha256_hex({k:v for k,v in body.items() if k!='idempotency_key'})
        ident=event_id('requirement-bound',body['task_id'],body['step_id'],body['rule_id'],body['rule_version'])
        with db.unit_of_work(self.conn) as uow:
            kid,_,alias=self._key(config,principal,body,'requirement')
            old=self._read(ident,principal=principal)
            if old:
                if old['payload']['request_hash']!=digest:raise Conflict('adapter_requirement_conflict')
                if not alias:self._bind_key(kid,digest,ident,{**body,'host_id':old['host_id']},principal,old['project_id'],'requirement')
                criteria=self.events.get(old['payload']['criteria_event_id'])
                return {'binding':old,'state':criteria['payload']['result'],'replayed':True}
            if alias:raise Conflict('adapter_requirement_conflict')
            _,source,task=source_for_task(self.conn,source_event_id=body['source_event_id'],task_id=body['task_id'],
                actor_id=principal.actor_id,host_id=config.value['host_id'],current_step_id=body['step_id'],schema_version=self.core.schema_version)
            interpretation=self.core.interpretations._get(body['interpretation_id'])
            if interpretation['event_id']!=body['source_event_id'] or interpretation['status']!='EXTRACTED':raise Conflict('adapter_requirement_conflict')
            candidates=interpretation['candidates']
            if index>=len(candidates):raise errors.InvalidRequest('candidate index unavailable')
            candidate=candidates[index]
            if candidate['kind'] not in ('REQUIRED_CAPABILITY','CORRECTION') or candidate['scope']['kind']!='TASK':raise Conflict('adapter_requirement_conflict')
            resolution=self.core.resolver.get(body['resolution_id'])
            if resolution['source_event_id']!=body['source_event_id'] or resolution['interpretation_id']!=body['interpretation_id']:raise Conflict('adapter_requirement_conflict')
            applied=self.core.resolver.applied_result(body['resolution_id'])
            matching=[a for a in applied['actions'] if a['candidate_index']==index and a['target_type']=='rule' and a['target_id']==body['rule_id'] and a['rule_version']==body['rule_version']]
            if len(matching)!=1:raise Conflict('adapter_requirement_conflict')
            rule=self.core.authority.get(body['rule_id']);step=self.core.state.step(body['step_id'])
            if rule['status']!='ACTIVE' or rule['version']!=body['rule_version'] or rule['scope']['kind']!='task' or rule['scope']['task_id']!=body['task_id'] or step['task_id']!=body['task_id']:
                raise Conflict('adapter_requirement_conflict')
            if task['revision']!=body['expected_task_revision'] or step['revision']!=body['expected_step_revision']:raise Conflict('adapter_operation_stale')
            previous=[]
            for row in self.conn.execute("SELECT event_id FROM events WHERE event_type='adapter.requirement_bound' AND source_system=? AND task_id=? ORDER BY seq",(SOURCE,body['task_id'])):
                event=self._read(row['event_id'],principal=principal)
                if event['payload'].get('step_id')==body['step_id'] and event['payload'].get('rule_id')==body['rule_id']:previous.append(event)
            if candidate['kind']=='CORRECTION':
                if not previous or matching[0]['action']!='SUPERSEDE_RULE':raise Conflict('adapter_requirement_conflict')
                origin=self.events.get(rule['origin_event_id'])
                # Human/system reviewed CORRECTION is a real new P2 source, not text parsing.
                if not origin or origin['event_type']!='review.approved' or origin['actor_kind'] not in ('human','system'):raise Conflict('adapter_requirement_conflict')
            requirements=list(step['acceptance_criteria']['requirements'])
            if previous:
                old_criterion=previous[-1]['payload']['criterion']
                if old_criterion not in requirements:raise Conflict('adapter_requirement_conflict')
                requirements.remove(old_criterion)
            fixed=config.value['executors'].get('jasmine_playwright')
            if not fixed or 'callback_task_id' not in fixed['choices']:raise Conflict('adapter_requirement_conflict')
            callback_command_sha=sha256_hex(canonical_json(fixed['argv']+['callback_task_id']))
            criterion={'key':'playwright-skill-'+body['rule_id']+'-v'+str(body['rule_version']),
                'kind':'TEST','required_result':'PASS','tool_name':'jasmine_playwright','command_sha256':callback_command_sha}
            if any(r['key']==criterion['key'] for r in requirements):raise Conflict('adapter_requirement_conflict')
            requirements.append(criterion)
            criteria_id=event_id('requirement-criteria',ident)
            command={**body,'host_id':config.value['host_id']}
            self._event(ident,'adapter.requirement_bound',command,{'request_hash':digest,'capability':'PLAYWRIGHT_SKILL',
                'criterion':criterion,'criteria_event_id':criteria_id,'rule_id':body['rule_id'],'rule_version':body['rule_version'],
                'step_id':body['step_id'],'source_event_id':body['source_event_id'],'interpretation_id':body['interpretation_id'],
                'resolution_id':body['resolution_id'],'candidate_index':index,'previous_binding_event_id':previous[-1]['event_id'] if previous else None},principal,task['project_id'])
            self.core.state.set_step_criteria(body['step_id'],{'expected_revision':body['expected_step_revision'],
                'host_id':config.value['host_id'],'event_id':criteria_id,'acceptance_criteria':{'requirements':requirements}},
                actor_id=principal.actor_id,actor_kind='system',can_accept=True,unit_of_work=uow)
            self._bind_key(kid,digest,ident,command,principal,task['project_id'],'requirement')
            event=self._read(ident,principal=principal);criteria=self.events.get(criteria_id)
            return {'binding':event,'state':criteria['payload']['result'],'replayed':False}

    def dynamic_observation(self,body,*,principal):
        fields(body,{'reservation_event_id','completion_event_id'},{'reservation_event_id','completion_event_id'})
        identifier(body['reservation_event_id'],'evt');identifier(body['completion_event_id'],'evt')
        config=self._config();config.principal(self.conn,principal,'adapter:read');principal.require('evidence:write')
        result=self.get(body['reservation_event_id'],principal=principal)['completion']
        if not result or result['completion_event_id']!=body['completion_event_id'] or result['status']!='SUCCEEDED' or not result.get('evidence'):
            raise Conflict('adapter_evidence_unavailable')
        return {'evidence':result['evidence'],'completion_event_id':body['completion_event_id'],'replayed':True}


def require_current_bindings(conn, task_id, step):
    """Current specialized criterion must retain its explicit active Rule version."""
    if step is None:
        return
    from ..events import EventStore
    from ..authority import AuthorityStore
    from .. import SCHEMA_VERSION
    events=EventStore(conn,schema_version=SCHEMA_VERSION)
    criteria=step['acceptance_criteria']['requirements']
    latest={}
    for row in conn.execute("SELECT event_id FROM events WHERE task_id=? AND event_type='adapter.requirement_bound' AND source_system=? ORDER BY seq",(task_id,SOURCE)):
        event=events.get(row['event_id'])
        if event['payload'].get('step_id')==step['step_id']:
            latest[event['payload']['rule_id']]=event
    for event in latest.values():
        payload=event['payload']
        if payload['criterion'] not in criteria:
            raise errors.MissingEvidence('explicit capability criterion was removed')
        rule=AuthorityStore(conn,schema_version=SCHEMA_VERSION).get(payload['rule_id'])
        if rule['status']!='ACTIVE' or rule['version']!=payload['rule_version']:
            raise errors.MissingEvidence('explicit capability binding requires current Rule version')
