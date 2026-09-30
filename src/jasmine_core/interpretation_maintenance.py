"""Versioned manual interpretation changes and recoverable rerun completion."""
import json
from . import clock, db, errors, ids
from .canonical import canonical_json
from .interpretation_schema import validate_output, InvalidOutput
from .resolution_common import fields, key, reason, revision, actor, command, digest, Conflict

class _Replay(Exception):
    def __init__(self,result):self.result=result

class MaintenanceStore:
    def __init__(self, interpretations):
        self.interpretations = interpretations
        self.conn = interpretations.conn
        from .events import EventStore
        self.events = EventStore(self.conn, schema_version=interpretations.schema_version)

    def root(self, ident):
        seen=set()
        while True:
            if ident in seen:
                raise errors.InvalidRequest('cyclic interpretation ancestry')
            seen.add(ident)
            row=self.interpretations._get(ident)
            if not row['parent_interpretation_id']:
                return ident
            ident=row['parent_interpretation_id']

    def head(self, ident):
        root=self.root(ident)
        row=self.conn.execute('SELECT * FROM maintenance_heads WHERE root_interpretation_id=?',(root,)).fetchone()
        return dict(row) if row else {'root_interpretation_id':root,'current_interpretation_id':root,
            'revision':1,'status':'NORMAL','pending_operation_event_id':None,'pending_child_interpretation_id':None}

    def recover(self, ident):
        root=self.root(ident)
        pending=self.conn.execute("SELECT h.* FROM interpretation_changes h LEFT JOIN interpretation_operation_completions c USING(operation_event_id) WHERE h.root_interpretation_id=? AND h.action='RERUN' AND c.operation_event_id IS NULL",(root,)).fetchall()
        for operation in pending:
            child=self.interpretations.get(operation['child_interpretation_id'])
            if child['status']!='PROCESSING':
                self.finish(operation['operation_event_id'],child['interpretation_id'],operation['to_revision'])

    def finish(self, operation_id, child_id, expected):
        child=self.interpretations._get(child_id)
        if child['status']=='PROCESSING':
            return
        with db.transaction(self.conn):
            if self.conn.execute('SELECT 1 FROM interpretation_operation_completions WHERE operation_event_id=?',(operation_id,)).fetchone():
                return
            head=self.head(child_id)
            applies=(head['revision']==expected and head['pending_operation_event_id']==operation_id and
                     head['pending_child_interpretation_id']==child_id and head['status']=='RERUN_PENDING')
            at=clock.now_rfc3339()
            self.conn.execute('INSERT INTO interpretation_operation_completions VALUES(?,?,?,?,?,?,?,?)',
                (operation_id,child_id,child['status'],int(applies),expected,head['revision'],at,canonical_json({'child':child_id,'status':child['status']})))
            if applies:
                op=self.events.get(operation_id)
                self.conn.execute("UPDATE maintenance_heads SET current_interpretation_id=?,status='NORMAL',revision=revision+1,pending_operation_event_id=NULL,pending_child_interpretation_id=NULL,updated_at=? WHERE root_interpretation_id=?",(child_id,at,head['root_interpretation_id']))
                self.conn.execute('INSERT INTO interpretation_changes VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (operation_id,head['root_interpretation_id'],child['parent_interpretation_id'],child_id,'RERUN_COMPLETE',op['actor_id'],op['payload']['reason'],expected,expected+1,at))

    def get(self, ident):
        self.recover(ident)
        value=self.interpretations.get(ident)
        value['maintenance_head']=self.head(ident)
        root=value['maintenance_head']['root_interpretation_id']
        value['maintenance_history']=[dict(r) for r in self.conn.execute('SELECT * FROM interpretation_changes WHERE root_interpretation_id=? ORDER BY to_revision',(root,))]
        value['operation_completions']=[dict(r) for r in self.conn.execute('SELECT c.* FROM interpretation_operation_completions c JOIN interpretation_changes h USING(operation_event_id) WHERE h.root_interpretation_id=? GROUP BY c.operation_event_id',(root,))]
        return value

    def check_current(self, ident):
        head=self.head(ident)
        if head['current_interpretation_id']!=ident or head['status'] in ('REJECTED','RERUN_PENDING'):
            raise Conflict('interpretation_not_current',current=head)
        row=self.interpretations._get(ident)
        if row['status']!='EXTRACTED':
            raise Conflict('interpretation_not_ready',processing_status=row['status'])
        return row

    def change(self, ident, action, body, *, actor_id):
        allowed={'idempotency_key','host_id','reason','expected_revision'}|({'result'} if action=='correct' else set())
        fields(body,allowed,allowed); request_key=key(body); reason(body); expected=revision(body['expected_revision'])
        request_hash=digest({'interpretation_id':ident,'action':action,'body':body})
        prior=self.conn.execute('SELECT * FROM maintenance_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,request_key)).fetchone()
        if prior:
            if prior['request_hash']!=request_hash:raise Conflict('idempotency_conflict')
            self.recover(ident)
            return {'result':json.loads(prior['result_json']),'replayed':True}
        who=actor(self.conn,actor_id,body['host_id'],human=True)
        self.recover(ident)
        source=self.interpretations._source(self.interpretations._get(ident)['event_id'])
        actor(self.conn,source['actor_id'],source['host_id'])
        root=self.root(ident); operation_id=ids.new_id('evt')
        def reserve(child=None):
            # Invoked inside process's reservation transaction for rerun.
            prior=self.conn.execute('SELECT * FROM maintenance_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,request_key)).fetchone()
            if prior:
                if prior['request_hash']!=request_hash:raise Conflict('idempotency_conflict')
                raise _Replay(json.loads(prior['result_json']))
            head=self.head(ident)
            if head['revision']!=expected or head['current_interpretation_id']!=ident:
                raise Conflict('interpretation_not_current',current=head)
            if self.conn.execute('SELECT 1 FROM maintenance_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,request_key)).fetchone():
                raise Conflict('idempotency_conflict')
            event=command(self.events,source,actor_id,who['kind'],body['host_id'],'interpretation.'+{'correct':'corrected','reject':'rejected','rerun':'rerun'}[action],
                {'reason':body['reason'],'parent_interpretation_id':ident,'child_interpretation_id':child,'operation_event_id':operation_id}, event_id=operation_id)
            # Event ID is server-created before reserving the rerun; append exact ID.
            if action=='rerun' and event['event_id']!=operation_id:
                raise RuntimeError('operation identity mismatch')
            op=event['event_id']
            status={'correct':'CORRECTED','reject':'REJECTED','rerun':'RERUN_PENDING'}[action]
            at=clock.now_rfc3339(); current=child if action=='correct' else ident
            self.conn.execute('INSERT INTO maintenance_heads VALUES(?,?,?,?,?,?,?) ON CONFLICT(root_interpretation_id) DO UPDATE SET current_interpretation_id=excluded.current_interpretation_id,status=excluded.status,revision=excluded.revision,pending_operation_event_id=excluded.pending_operation_event_id,pending_child_interpretation_id=excluded.pending_child_interpretation_id,updated_at=excluded.updated_at',
                (root,current,status,expected+1,op if action=='rerun' else None,child if action=='rerun' else None,at))
            self.conn.execute('INSERT INTO interpretation_changes VALUES(?,?,?,?,?,?,?,?,?,?)',(op,root,ident,child,action.upper(),actor_id,body['reason'],expected,expected+1,at))
            result={'operation_event_id':op,'child_interpretation_id':child,'head':self.head(ident)}
            self.conn.execute('INSERT INTO maintenance_request_keys VALUES(?,?,?,?,?)',(actor_id,request_key,request_hash,op,canonical_json(result)))
            return result
        if action=='rerun':
            context={'version':1,'type':'rerun','parent_interpretation_id':ident,'operation_event_id':operation_id,'actor_id':actor_id}
            # command helper receives explicit event ID for this reservation.
            self._reserved_event_id=operation_id
            try:
                output=self.interpretations.process({'event_id':source['event_id'],'idempotency_key':'rerun:'+operation_id},actor_id=actor_id,
                    operation_context=context,parent_interpretation_id=ident,on_reserved=reserve)
            except _Replay as replay:
                return {'result':replay.result,'replayed':True}
            finally:
                self._reserved_event_id=None
            self.finish(operation_id,output['interpretation']['interpretation_id'],expected+1)
            prior=self.conn.execute('SELECT result_json FROM maintenance_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,request_key)).fetchone()
            return {'result':json.loads(prior['result_json']),'replayed':False,'interpretation':self.get(output['interpretation']['interpretation_id'])}
        with db.transaction(self.conn):
            child=None
            if action=='correct':
                try:valid=validate_output(canonical_json(body['result']),source)
                except InvalidOutput as exc:raise errors.InvalidRequest('invalid manual interpretation',reason=str(exc))
                prior=self.conn.execute('SELECT * FROM maintenance_request_keys WHERE actor_id=? AND idempotency_key=?',(actor_id,request_key)).fetchone()
                if prior:
                    if prior['request_hash']!=request_hash:raise Conflict('idempotency_conflict')
                    return {'result':json.loads(prior['result_json']),'replayed':True}
                head=self.head(ident)
                if head['revision']!=expected or head['current_interpretation_id']!=ident:raise Conflict('interpretation_not_current',current=head)
                parent=self.interpretations._get(ident); child=ids.new_id('int');at=clock.now_rfc3339()
                columns=[r['name'] for r in self.conn.execute('PRAGMA table_info(interpretations)')]
                original=dict(self.conn.execute('SELECT * FROM interpretations WHERE interpretation_id=?',(ident,)).fetchone())
                original.update(interpretation_id=child,parent_interpretation_id=ident,processor_actor_id=actor_id,extractor_id='manual-correction-v1',extractor_model='manual',provider='manual',extractor_version='1',created_at=at,deadline_at=at,input_hash=digest({'source':source,'manual_result':valid,'operation':operation_id}))
                manual_config={'provider':'manual','model':'manual','version':'1','extractor_id':'manual-correction-v1','source_schema_digest':parent['schema_digest'],'operator_actor_id':actor_id,'operation_event_id':operation_id}
                original.update(config_json=canonical_json(manual_config),config_digest=digest(manual_config),prompt_version='manual-correction-v1')
                self.conn.execute('INSERT INTO interpretations('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[original[c] for c in columns])
                self.conn.execute('INSERT INTO interpretation_results VALUES(?,?,?,?,?,?,?)',(child,'EXTRACTED',canonical_json(valid),canonical_json(valid['candidates']),valid['confidence'],None,at))
            try:
                return {'result':reserve(child),'replayed':False}
            except _Replay as replay:
                return {'result':replay.result,'replayed':True}
