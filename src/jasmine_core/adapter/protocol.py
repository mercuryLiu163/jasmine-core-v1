"""Event-only unique native calls; effects remain outside Core transactions."""
from __future__ import annotations
import uuid
from pathlib import Path
import hashlib
import time
from .. import clock, db, errors, ids
from ..canonical import canonical_json, sha256_hex, body_hash
from ..models import NewEvent
from ..events import EventStore
from ..resolution_common import fields, key, revision, Conflict
from ..task_snapshot import read_task_snapshot
from ..continuity_source import source_for_task
from .config import AdapterConfig

NAMESPACE=uuid.UUID('4c1e7c50-9b93-5c8f-91d2-a9d18bcba8f1')
SOURCE='core-native-adapter'
TOOLS={'jasmine_read':('JasmineRead','read'),'jasmine_patch':('JasminePatch','write'),
       'jasmine_test':('JasmineTest','execute'),'jasmine_playwright':('Playwright','execute')}
RESERVE={'idempotency_key','source_event_id','context_pack_id','task_id','step_id','host_id','session_id',
         'native_thread_id','native_turn_id','native_call_id','lease_generation','tool','args',
         'expected_task_revision','expected_step_revision'}
CHECK={'host_id','native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id'}


def event_id(domain,*parts):
    value='evt_'+ids._b32(uuid.uuid5(NAMESPACE,canonical_json([domain,*parts])).int,26)
    assert ids.is_id(value,'evt')
    return value


def input_fingerprint_sha256(snapshot):
    # Actual independently sampled input identity excludes sampling timestamps.
    return sha256_hex({k:snapshot.get(k) for k in ('root','git_head','is_git','algorithm','coverage','extra_paths','selected_hashes','manifest_sha256','complete')})


def text(value,name,*,optional=False):
    if optional and value is None:return
    if not isinstance(value,str) or not 1<=len(value.encode('utf-8'))<=256:raise errors.InvalidRequest('invalid '+name)


def identifier(value,prefix,*,optional=False):
    if optional and value is None:return
    if not ids.is_id(value,prefix):raise errors.InvalidRequest('invalid '+prefix+' reference')


class AdapterStore:
    def __init__(self,core,*,config=None):
        self.core=core;self.conn=core.conn;self.events=EventStore(self.conn,schema_version=core.schema_version)
        self.config=config

    def _config(self):
        if self.conn.in_transaction:raise RuntimeError('adapter config cannot load inside transaction')
        return self.config or AdapterConfig.load()

    def _event(self,ident,kind,body,payload,principal,project):
        return self.events.append(NewEvent(event_id=ident,event_type=kind,source_system=SOURCE,
            source_event_id=ident,occurred_at=clock.now(),actor_id=principal.actor_id,actor_kind='system',
            host_id=body['host_id'],task_id=body['task_id'],project_id=project,session_id=body.get('session_id'),
            payload={'text':'',**payload}))[0]

    def _internal(self,value,*,principal=None):
        if value is None:return None
        if value['source_system']!=SOURCE or value['source_event_id']!=value['event_id'] or value['actor_kind']!='system' or not value['event_type'].startswith('adapter.'):
            raise Conflict('adapter_provenance_mismatch')
        if principal is not None and value['actor_id']!=principal.actor_id:raise Conflict('adapter_provenance_mismatch')
        hashed={k:value[k] for k in ('event_type','source_system','source_event_id','actor_id','host_id','session_id','project_id','task_id','payload')}
        hashed['occurred_at']=None
        if body_hash(hashed)!=value['body_sha256']:raise Conflict('adapter_provenance_mismatch')
        return value

    def _read(self,ident,*,principal=None):return self._internal(self.events.get(ident),principal=principal)

    def _key(self,config,principal,body,operation):
        ident=event_id(operation+'-key',principal.actor_id,key(body))
        old=self._read(ident,principal=principal)
        digest=sha256_hex({k:v for k,v in body.items() if k!='idempotency_key'})
        if old and (old['event_type']!='adapter.operation_key_bound' or old['actor_id']!=principal.actor_id or
                    old['payload']['request_hash']!=digest):raise Conflict('adapter_operation_conflict')
        return ident,digest,old

    def _bind_key(self,key_id,digest,target,body,principal,project,operation):
        self._event(key_id,'adapter.operation_key_bound',body,{'request_hash':digest,'operation_event_id':target,
            'operation':operation},principal,project)

    def _lease(self,lease,body,*,phase='READY'):
        expected={'event_id':body['source_event_id'],'task_id':body['task_id'],'step_id':body['step_id'],
            'host_id':body['host_id'],'session_id':body['native_thread_id'],'turn_id':body['native_turn_id'],
            'generation':body['lease_generation'],'context_pack_id':body['context_pack_id']}
        if lease.get('phase')!=phase or any(lease.get(k)!=v for k,v in expected.items()):raise Conflict('adapter_operation_stale')
        import time,math
        end=lease.get('ready_deadline_monotonic')
        if type(end) not in (int,float) or (isinstance(end,float) and not math.isfinite(end)) or not 0<end<1e15:raise Conflict('adapter_operation_stale')
        if time.monotonic()>=end:raise Conflict('adapter_operation_stale')
        return lease

    def _pack_integrity(self,body):
        pack=self.core.context.get(body['context_pack_id']);text_value=pack['rendered_content']
        if sha256_hex(text_value)!=pack['rendered_sha256'] or len(text_value.encode('utf-8'))!=pack['rendered_utf8_bytes'] or pack['tokenizer']!=self.core.context.tokenizer.identity or self.core.context.tokenizer.count(text_value)!=pack['token_count'] or pack['token_count']>2500:
            raise Conflict('adapter_operation_stale')
        return pack

    def _fresh(self,config,body,principal,configuration,config_digest,validated_pack):
        source_for_task(self.conn,task_id=body['task_id'],source_event_id=body['source_event_id'],actor_id=principal.actor_id,host_id=body['host_id'],
            session_id=body['session_id'],current_step_id=body['step_id'],schema_version=self.core.schema_version)
        pack=self.core.context.get(body['context_pack_id'])
        if pack!=validated_pack:raise Conflict('adapter_operation_stale')
        if pack['actor_id']!=principal.actor_id or any(pack.get(k)!=v for k,v in {
            'task_id':body['task_id'],'host_id':body['host_id'],'source_event_id':body['source_event_id'],
            'session_id':body['session_id'],'current_step_id':body['step_id']}.items()):raise Conflict('adapter_operation_stale')
        if pack['config_digest']!=config_digest or pack['configuration']!=configuration:raise Conflict('adapter_operation_stale')
        current=read_task_snapshot(self.conn,body['task_id'],session_id=body['session_id'])
        if current['truth_digest']!=pack['truth_digest'] or current['truth_projection']!=pack['projection'] or current['auxiliary']!=pack['selector']:
            raise Conflict('adapter_operation_stale')
        task=self.conn.execute('SELECT revision FROM tasks WHERE task_id=?',(body['task_id'],)).fetchone()
        step=self.conn.execute('SELECT revision FROM steps WHERE step_id=?',(body['step_id'],)).fetchone()
        if not task or not step or task['revision']!=body['expected_task_revision'] or step['revision']!=body['expected_step_revision']:
            raise Conflict('adapter_operation_stale')
        return pack,current

    def _syntax(self,body):
        fields(body,RESERVE,RESERVE);key(body)
        if len(canonical_json(body).encode('utf-8'))>65536:raise errors.InvalidRequest('adapter request exceeds 64 KiB')
        for name,prefix in (('source_event_id','evt'),('context_pack_id','ctx'),('task_id','tsk'),('step_id','stp'),('host_id','hst'),('session_id','ses')):
            identifier(body[name],prefix)
        for name in ('native_thread_id','native_turn_id','native_call_id','lease_generation'):text(body[name],name)
        revision(body['expected_task_revision']);revision(body['expected_step_revision'])
        if not isinstance(body['tool'],str) or body['tool'] not in TOOLS:raise errors.InvalidRequest('unsupported dynamic tool')
        args=body['args'];tool=body['tool']
        if tool in ('jasmine_read','jasmine_patch'):
            allowed={'path'} if tool=='jasmine_read' else {'path','expected_sha256','new_content'}
            fields(args,allowed,allowed);text(args['path'],'path')
            path=Path(args['path'])
            if path.is_absolute() or '..' in path.parts or not path.parts or path.as_posix()!=args['path']:raise errors.InvalidRequest('relative safe work path required')
            if tool=='jasmine_patch':
                import re
                if not isinstance(args['expected_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',args['expected_sha256']):raise errors.InvalidRequest('invalid patch SHA')
                if not isinstance(args['new_content'],str) or len(args['new_content'].encode('utf-8'))>131072:raise errors.InvalidRequest('patch exceeds cap')
        else:
            field='suite' if tool=='jasmine_test' else 'scenario';fields(args,{field},{field});text(args[field],field)
        if len(canonical_json(args).encode('utf-8'))>16384:raise errors.InvalidRequest('dynamic args exceed cap')

    def _mapping(self,config,body):
        tool,action=TOOLS[body['tool']]
        if body['tool'] in ('jasmine_read','jasmine_patch'):
            path=str(Path(config.value['work_root'])/body['args']['path'])
        else:
            manifest=config.value['executors'].get(body['tool'])
            field='suite' if body['tool']=='jasmine_test' else 'scenario'
            if not manifest or body['args'][field] not in manifest.get('choices',[]):raise errors.InvalidRequest('unknown fixed executor choice')
            path=manifest['argv'][1]
        return {'tool':tool,'action':action,'path':path}

    def _executor_identity(self,config,body):
        import hashlib, os, stat
        tool=body['tool']
        if tool in ('jasmine_read','jasmine_patch'):
            return {'kind':'builtin','protocol_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'implementation_sha256':hashlib.sha256(Path(__file__).with_name('executors.py').read_bytes()).hexdigest(),
                    'mapping':self._mapping(config,body)}
        manifest=config.value['executors'].get(tool)
        fields(manifest,{'argv','choices','dependencies','environment'},{'argv','choices','dependencies','environment'})
        argv=manifest['argv'];dependencies=manifest['dependencies']
        if not isinstance(argv,list) or len(argv)!=2 or not all(isinstance(v,str) and Path(v).is_absolute() for v in argv):
            raise errors.InvalidRequest('fixed two-file executor argv required')
        if not isinstance(dependencies,list) or len(dependencies)>16:raise errors.InvalidRequest('bounded dependency list required')
        identities=[]
        for name in argv+dependencies:
            if not isinstance(name,str):raise errors.InvalidRequest('dependency path required')
            path=Path(name)
            if path!=path.resolve(strict=True):raise errors.InvalidRequest('canonical executor path required')
            if path.is_dir():
                if name in argv:raise errors.InvalidRequest('executor argv must be files')
                from .dependency_identity import directory_identity
                try:identities.append(directory_identity(path))
                except (OSError,ValueError) as exc:raise errors.InvalidRequest('dependency identity unavailable') from exc
                continue
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            try:
                before=os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size>268435456:raise errors.InvalidRequest('executor file exceeds cap')
                h=hashlib.sha256();total=0
                while chunk:=os.read(fd,1048576):
                    total+=len(chunk)
                    if total>268435456:raise errors.InvalidRequest('executor file exceeds cap')
                    h.update(chunk)
                after=os.fstat(fd)
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise Conflict('adapter_operation_stale')
                identities.append({'path':str(path),'sha256':h.hexdigest(),'bytes':total})
            finally:os.close(fd)
        field='suite' if tool=='jasmine_test' else 'scenario'
        environment=manifest['environment']
        if not isinstance(environment,dict) or set(environment)-{'PATH','LANG','LC_ALL','NODE_PATH'} or not all(isinstance(v,str) for v in environment.values()):
            raise errors.InvalidRequest('executor environment must use fixed whitelist')
        return {'kind':'worker','argv':argv+[body['args'][field]],'files':identities,'environment':environment,
                'environment_sha256':sha256_hex(environment),'stdin_sha256':sha256_hex(config.value['executor_inputs'][tool])}

    def reserve(self,body,*,principal):
        self._syntax(body);config=self._config();config.principal(self.conn,principal,'adapter:report')
        config.principal(self.conn,principal,'guard:check')
        semantic={k:v for k,v in body.items() if k!='idempotency_key'};digest=sha256_hex(semantic)
        ident=event_id('native-call',body['native_thread_id'],body['native_turn_id'],body['native_call_id'])
        # Historical replay precedes dependency/receipt/current-state reads.
        with db.transaction(self.conn):
            alias_id,_,alias=self._key(config,principal,body,'reserve');old=self._read(ident,principal=principal)
            if alias and (not old or alias['payload']['operation_event_id']!=ident):raise Conflict('adapter_operation_conflict')
            if old:
                if old['actor_id']!=principal.actor_id or old['payload'].get('request_hash')!=digest or old['event_type'] not in ('adapter.operation_reserved','adapter.operation_denied'):
                    raise Conflict('adapter_operation_conflict')
                if not alias:self._bind_key(alias_id,digest,ident,body,principal,old['project_id'],'reserve')
                return {'operation':old['payload']['result'],'replayed':True}
        # Configuration loading is always outside the short writer transaction.
        configuration,current_config=self.core.context._configuration()
        mapping=self._mapping(config,body)
        validated_pack=self._pack_integrity(body)
        executor_identity=self._executor_identity(config,body)
        from ..continuity_scan import Budget
        before_scan=self.core.context.scanner(Budget(10),conn=self.conn)
        lease=config.lease()
        native,native_sha=config.receipt(body['native_call_id'])
        expected={'threadId':body['native_thread_id'],'turnId':body['native_turn_id'],'callId':body['native_call_id'],
                  'tool':body['tool'],'arguments':body['args']}
        if native.get('kind')!='dynamic_call' or native.get('request')!=expected:raise Conflict('adapter_provenance_mismatch')
        received=native.get('received_at_monotonic')
        import math
        if type(received) not in (int,float) or (isinstance(received,float) and not math.isfinite(received)) or not 0<received<=time.monotonic() or time.monotonic()-received>=90:raise Conflict('adapter_operation_stale')
        if self._config().config_sha256!=config.config_sha256 or self.core.context._configuration()[1]!=current_config or self._executor_identity(config,body)!=executor_identity:
            raise Conflict('adapter_operation_stale')
        with db.transaction(self.conn):
            key_id,_,alias=self._key(config,principal,body,'reserve');old=self._read(ident,principal=principal)
            if alias and (not old or alias['payload']['operation_event_id']!=ident):raise Conflict('adapter_operation_conflict')
            if old:
                if old['actor_id']!=principal.actor_id or old['payload'].get('request_hash')!=digest or old['event_type'] not in ('adapter.operation_reserved','adapter.operation_denied'):
                    raise Conflict('adapter_operation_conflict')
                if not alias:self._bind_key(key_id,digest,ident,body,principal,old['project_id'],'reserve')
                return {'operation':old['payload']['result'],'replayed':True}
            self._lease(lease,body);pack,snapshot=self._fresh(config,body,principal,configuration,current_config,validated_pack)
            decision=self.core.authority.guard({'project_id':pack['project_id'],'task_id':body['task_id'],**mapping})
            # Unsupported confirmation/precondition requests never become ALLOW.
            status='RESERVED' if decision['decision']=='allow' else 'DENIED'
            result={'operation_event_id':ident,'status':status,'decision':decision,'request_hash':digest,
                'context_pack_id':body['context_pack_id'],'truth_digest':snapshot['truth_digest'],'selector':snapshot['auxiliary'],
                'expected_task_revision':body['expected_task_revision'],'expected_step_revision':body['expected_step_revision'],
                'expected_members':snapshot['truth_projection']['objects'],
                'rule_versions':[{k:r[k] for k in ('rule_id','version','revision','status','origin_event_id')} for r in snapshot['truth_projection']['rules']],
                'mapping':mapping,
                'executor_manifest':executor_identity,'executor_manifest_sha256':sha256_hex(executor_identity),
                'before_fingerprint':before_scan['snapshot'],'before_sample_window':before_scan['sample_window'],
                'native_receipt_sha256':native_sha,'callback_deadline_monotonic':received+90,'input_fingerprint_sha256':input_fingerprint_sha256(before_scan['snapshot'])}
            self._event(ident,'adapter.operation_reserved' if status=='RESERVED' else 'adapter.operation_denied',body,
                {'request':semantic,'request_hash':digest,'adapter_config_sha256':config.config_sha256,'result':result},principal,pack['project_id'])
            self._bind_key(key_id,digest,ident,body,principal,pack['project_id'],'reserve')
            return {'operation':result,'replayed':False}

    def get(self,ident,*,principal):
        identifier(ident,'evt');config=self._config();config.principal(self.conn,principal,'adapter:read')
        old=self._read(ident,principal=principal)
        if not old or old['source_system']!=SOURCE or old['event_type'] not in ('adapter.operation_reserved','adapter.operation_denied'):
            raise errors.NotFound('adapter operation',ident)
        if old['actor_id']!=principal.actor_id:raise errors.ForbiddenActorKind('operation belongs to another adapter')
        completed=self._read(event_id('operation-completion',ident),principal=principal)
        aggregate=old['payload']['result']['status']
        unknown_reason=None
        if completed:aggregate=completed['payload']['result']['status']
        elif aggregate=='RESERVED':
            path=Path(config.value['receipt_root'])/('effect_'+ident+'.json')
            try:path.lstat()
            except FileNotFoundError:pass
            else:
                aggregate='UNKNOWN_OUTCOME'
                try:config.receipt('effect_'+ident)
                except errors.CoreError:unknown_reason='effect_receipt_invalid'
        return {'operation':old['payload']['result'],'completion':completed['payload']['result'] if completed else None,
                'status':aggregate,'unknown_reason':unknown_reason,'effect_authorized':False}

    def check_current(self,ident,body,*,principal):
        identifier(ident,'evt');fields(body,CHECK,CHECK)
        for name in ('native_thread_id','native_turn_id','native_call_id','lease_generation'):text(body[name],name)
        identifier(body['host_id'],'hst');identifier(body['context_pack_id'],'ctx')
        config=self._config();config.principal(self.conn,principal,'adapter:report');config.principal(self.conn,principal,'guard:check')
        configuration,digest=self.core.context._configuration()
        original=self._read(ident,principal=principal)
        if not original or original['event_type']!='adapter.operation_reserved':raise Conflict('adapter_operation_terminal')
        validated_pack=self._pack_integrity(original['payload']['request'])
        executor_identity=self._executor_identity(config,original['payload']['request'])
        from ..continuity_scan import Budget
        from .. import fingerprint
        checked_scan=self.core.context.scanner(Budget(10),conn=self.conn)
        if fingerprint.compare(original['payload']['result']['before_fingerprint'],checked_scan['snapshot'])!='SAME':raise Conflict('adapter_operation_stale')
        if executor_identity!=original['payload']['result']['executor_manifest']:raise Conflict('adapter_operation_stale')
        from ..task_snapshot import read_transaction
        effect_path=Path(config.value['receipt_root'])/('effect_'+ident+'.json')
        try:effect_path.lstat()
        except FileNotFoundError:started=None
        else:started=config.receipt('effect_'+ident)[0]
        lease=config.lease()
        with read_transaction(self.conn):
            old=self._read(ident,principal=principal)
            if not old or old['event_type']!='adapter.operation_reserved' or old['actor_id']!=principal.actor_id:raise Conflict('adapter_operation_terminal')
            if old['payload']['adapter_config_sha256']!=config.config_sha256:raise Conflict('adapter_operation_stale')
            saved=old['payload']['request']
            if any(saved[k]!=v for k,v in body.items()):raise Conflict('adapter_operation_stale')
            if self._read(event_id('operation-completion',ident),principal=principal):raise Conflict('adapter_operation_terminal')
            # A persisted effect-start is never a fresh authorization to repeat.
            if started is not None:raise Conflict('adapter_operation_pending')
            self._lease(lease,saved);pack,current=self._fresh(config,saved,principal,configuration,digest,validated_pack)
            return {**old['payload']['result'],'current':True,'checked_at':clock.now_rfc3339()}

    def complete(self,ident,body,*,principal):
        """Persist a terminal outcome once; never authorize a repeated effect."""
        identifier(ident,'evt')
        fields(body,{'idempotency_key','reservation_event_id','receipt_id','receipt_sha256'},
               {'idempotency_key','reservation_event_id','receipt_id','receipt_sha256'});key(body)
        if body['reservation_event_id']!=ident:raise errors.InvalidRequest('reservation path/body mismatch')
        config=self._config();config.principal(self.conn,principal,'adapter:report')
        old=self._read(ident,principal=principal)
        if not old or old['event_type']!='adapter.operation_reserved':raise Conflict('adapter_operation_terminal')
        request=old['payload']['request'];terminal_id=event_id('operation-completion',ident)
        digest=sha256_hex({k:v for k,v in body.items() if k!='idempotency_key'})
        command_body={**body,**{k:request[k] for k in ('task_id','host_id','session_id')}}
        with db.transaction(self.conn):
            kid,_,alias=self._key(config,principal,body,'complete');terminal=self._read(terminal_id,principal=principal)
            if terminal:
                if terminal['payload']['request_hash']!=digest:raise Conflict('adapter_operation_conflict')
                if not alias:self._bind_key(kid,digest,terminal_id,command_body,principal,old['project_id'],'complete')
                return {'completion':terminal['payload']['result'],'replayed':True}
            if alias:raise Conflict('adapter_operation_conflict')
        worker,worker_sha=config.receipt(body['receipt_id'],body['receipt_sha256'])
        started,_=config.receipt('effect_'+ident)
        if started.get('kind')!='effect_started' or started.get('operation_event_id')!=ident or \
           worker.get('kind')!='worker_result' or worker.get('operation_event_id')!=ident or \
           worker.get('executor_manifest_sha256')!=old['payload']['result']['executor_manifest_sha256'] or \
           worker.get('native_thread_id')!=request['native_thread_id'] or worker.get('native_turn_id')!=request['native_turn_id'] or \
           worker.get('native_call_id')!=request['native_call_id'] or worker.get('args_sha256')!=sha256_hex(request['args']):
            raise Conflict('adapter_operation_conflict')
        fields(started,{'kind','operation_event_id','native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id','args_sha256','executor_manifest_sha256','started_at_monotonic'},
               {'kind','operation_event_id','native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id','args_sha256','executor_manifest_sha256','started_at_monotonic'})
        worker_fields={'kind','operation_event_id','native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id','args_sha256','executor_manifest_sha256','started_at_monotonic','finished_at_monotonic','status','exit_code','cleanup','artifact_uri','artifact_sha256','output','output_sha256','input_fingerprint_sha256'}
        fields(worker,worker_fields,worker_fields)
        for value in (started,worker):
            if any(value[k]!=request[k] for k in ('native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id')) or value['args_sha256']!=sha256_hex(request['args']) or value['executor_manifest_sha256']!=old['payload']['result']['executor_manifest_sha256']:
                raise Conflict('adapter_operation_conflict')
        import math
        times=(started['started_at_monotonic'],worker['started_at_monotonic'],worker['finished_at_monotonic'])
        if any(type(t) not in (int,float) or (isinstance(t,float) and not math.isfinite(t)) or not 0<t<1e15 for t in times) or not times[0]<=times[1]<=times[2]:
            raise Conflict('adapter_operation_conflict')
        if not isinstance(worker['output'],str) or len(worker['output'].encode('utf-8'))>1048576 or sha256_hex(worker['output'])!=worker['output_sha256']:
            raise Conflict('adapter_operation_conflict')
        status=worker.get('status')
        if status not in ('SUCCEEDED','FAILED','INTERRUPTED','UNKNOWN_OUTCOME'):raise errors.InvalidRequest('invalid worker terminal status')
        if status=='SUCCEEDED' and (type(worker.get('exit_code')) is not int or worker['exit_code']!=0 or worker.get('cleanup')!='complete'):
            raise Conflict('adapter_operation_conflict')
        stale=old['payload']['adapter_config_sha256']!=config.config_sha256
        limit=60 if request['tool']=='jasmine_playwright' else 30 if request['tool']=='jasmine_test' else 5
        if times[2]-times[1]>limit or times[2]>old['payload']['result']['callback_deadline_monotonic'] or time.monotonic()>old['payload']['result']['callback_deadline_monotonic']:stale=True
        try:self._lease(config.lease(),request)
        except errors.CoreError:stale=True
        if worker['input_fingerprint_sha256']!=old['payload']['result']['input_fingerprint_sha256']:stale=True
        configuration=current_config=validated_pack=None
        try:
            configuration,current_config=self.core.context._configuration();validated_pack=self._pack_integrity(request)
            if self._executor_identity(config,request)!=old['payload']['result']['executor_manifest']:stale=True
        except errors.CoreError:stale=True
        from ..continuity_scan import Budget
        scan_error=None
        try:after_scan=self.core.context.scanner(Budget(10),conn=self.conn)
        except errors.CoreError as exc:
            scan_error=exc.code;after_scan={'snapshot':{'complete':False,'partial_reasons':[exc.code]},'sample_window':None}
            status='UNKNOWN_OUTCOME'
        from .. import fingerprint
        before=old['payload']['result']['before_fingerprint'];after=after_scan['snapshot']
        if status=='SUCCEEDED':
            if request['tool']!='jasmine_patch':
                if fingerprint.compare(before,after)!='SAME':stale=True
            else:
                try:
                    import json
                    patch=json.loads(worker['output']);target=request['args']['path']
                    if patch.get('path')!=target or patch.get('before_sha256')!=request['args']['expected_sha256'] or patch.get('after_sha256')!=hashlib.sha256(request['args']['new_content'].encode('utf-8')).hexdigest():stale=True
                    if not before.get('complete') or not after.get('complete') or before['git_head']!=after['git_head']:stale=True
                    a=before['selected_hashes'];b=after['selected_hashes']
                    changed={name for name in set(a)|set(b) if a.get(name)!=b.get(name)}
                    if changed!={target} or a.get(target)!=request['args']['expected_sha256'] or b.get(target)!=hashlib.sha256(request['args']['new_content'].encode('utf-8')).hexdigest():stale=True
                except (KeyError,TypeError,ValueError):stale=True
        if status=='SUCCEEDED' and request['tool']=='jasmine_playwright':
            try:
                import json
                from urllib.parse import urlsplit
                browser=json.loads(worker['output'])
                fields(browser,{'scenario','events','artifact_uri','artifact_sha256','artifact_bytes','cleanup'},
                       {'scenario','events','artifact_uri','artifact_sha256','artifact_bytes','cleanup'})
                events=browser['events']
                if not isinstance(events,list) or len(events)!=4 or not all(isinstance(e,dict) for e in events) or [e.get('event') for e in events]!=['browser_launched','navigation','assertion','cleanup']:
                    raise Conflict('adapter_browser_receipt_invalid')
                origin=urlsplit(events[1]['origin'])
                if origin.scheme!='http' or origin.hostname!='127.0.0.1' or not origin.port or origin.username or origin.password or origin.path not in ('','/') or origin.query or origin.fragment:
                    raise Conflict('adapter_browser_receipt_invalid')
                if events[1].get('origin')!=config.value['executor_inputs']['jasmine_playwright']['origin']:
                    raise Conflict('adapter_browser_receipt_invalid')
                if events[0].get('fresh_owned') is not True or events[2].get('name')!=request['args']['scenario'] or events[2].get('passed') is not True or events[3].get('status')!='complete' or browser['cleanup']!='complete':
                    raise Conflict('adapter_browser_receipt_invalid')
                if browser['scenario']!=request['args']['scenario'] or browser['artifact_uri']!=worker['artifact_uri'] or browser['artifact_sha256']!=worker['artifact_sha256'] or type(browser['artifact_bytes']) is not int or not 1<=browser['artifact_bytes']<=16777216:
                    raise Conflict('adapter_browser_receipt_invalid')
            except (KeyError,TypeError,ValueError,errors.CoreError):status='FAILED'
        evidence_body=prepared=provenance=None;evidence_id=None;qualification_error=None
        if status=='SUCCEEDED' and request['tool'] in ('jasmine_test','jasmine_playwright'):
            try:
                principal.require('evidence:write')
                if not after_scan['snapshot'].get('complete'):raise Conflict('adapter_fingerprint_incomplete')
                evidence_body={k:request[k] for k in ('task_id','step_id','host_id','session_id')}
                evidence_body.update(codex_session_id=request['native_thread_id'],turn_id=request['native_turn_id'],
                    tool_use_id=request['native_call_id'],tool_name=request['tool'],kind='TEST',artifact_uri=worker['artifact_uri'],
                    tool_input={'command':canonical_json(old['payload']['result']['executor_manifest']['argv']),
                        'scenario':request['args'],'executor_manifest_sha256':old['payload']['result']['executor_manifest_sha256']},
                    tool_response={'exit_code':worker['exit_code'],'output_sha256':worker['output_sha256'],'artifact_sha256':worker['artifact_sha256']})
                prepared=self.core.evidence.prepare_dynamic_tool_result(evidence_body,after_scan['snapshot'])
                if prepared.artifact[0] is None or prepared.artifact[1]!=worker['artifact_sha256']:raise Conflict('adapter_artifact_mismatch')
                evidence_id=ids.new_id('evd')
                provenance={'protocol':'jasmine.dynamic-evidence.v1','reservation_event_id':ident,'completion_event_id':terminal_id,
                    'origin_prompt_event_id':request['source_event_id'],'context_pack_id':request['context_pack_id'],
                    **{k:request[k] for k in ('native_thread_id','native_turn_id','native_call_id','lease_generation')},
                    'tool_name':request['tool'],'args_sha256':sha256_hex(request['args']),
                    'executor_manifest_sha256':old['payload']['result']['executor_manifest_sha256'],
                    'producer_config_sha256':prepared.producer_config_sha256,'worker_receipt_id':body['receipt_id'],
                    'worker_receipt_sha256':worker_sha,'artifact_uri':worker['artifact_uri'],'artifact_sha256':worker['artifact_sha256']}
                from .dynamic_evidence import validate_provenance
                validate_provenance(provenance)
            except errors.CoreError as exc:
                status='FAILED';qualification_error=exc.code;prepared=provenance=evidence_body=None
        # Sample the protected lease after every outside-DB scan/producer read.
        # The runner holds its lease lock across effect and completion; this is
        # an explicit filesystem sample boundary, not a cross-store transaction.
        try:self._lease(config.lease(),request)
        except errors.CoreError:stale=True
        if time.monotonic()>=old['payload']['result']['callback_deadline_monotonic']:stale=True
        with db.unit_of_work(self.conn) as uow:
            kid,_,alias=self._key(config,principal,body,'complete');terminal=self._read(terminal_id,principal=principal)
            if terminal:
                if terminal['payload']['request_hash']!=digest:raise Conflict('adapter_operation_conflict')
                if not alias:self._bind_key(kid,digest,terminal_id,command_body,principal,old['project_id'],'complete')
                return {'completion':terminal['payload']['result'],'replayed':True}
            if not stale:
                try:self._fresh(config,request,principal,configuration,current_config,validated_pack)
                except errors.CoreError:stale=True
            if time.monotonic()>=old['payload']['result']['callback_deadline_monotonic']:stale=True
            actual='STALE_AFTER_EFFECT' if stale else status
            result={'completion_event_id':terminal_id,'reservation_event_id':ident,'status':actual,
                'worker_status':worker['status'],'worker_receipt_id':body['receipt_id'],'worker_receipt_sha256':worker_sha,
                'before_fingerprint':old['payload']['result']['before_fingerprint'],'after_fingerprint':after_scan['snapshot'],
                'after_sample_window':after_scan['sample_window'],'scan_error':scan_error,'qualification_error':qualification_error,
                'state_advanced':False,'evidence':None}
            if actual=='SUCCEEDED' and prepared is not None:
                result['evidence_provenance']=provenance;result['evidence_provenance_sha256']=sha256_hex(provenance)
                evidence_result=self.core.evidence.record_tool_result(evidence_body,actor_id=principal.actor_id,
                    unit_of_work=uow,prepared=prepared,operation_provenance=provenance,evidence_id=evidence_id)
                result['evidence']=evidence_result['evidence']
            self._event(terminal_id,'adapter.operation_completed',command_body,{'request_hash':digest,'result':result},principal,old['project_id'])
            self._bind_key(kid,digest,terminal_id,command_body,principal,old['project_id'],'complete')
            return {'completion':result,'replayed':False}
