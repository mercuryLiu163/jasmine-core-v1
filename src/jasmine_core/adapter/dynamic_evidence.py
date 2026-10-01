"""Exact internal dynamic provenance; legacy Bash attestation is separate."""
from .. import errors,ids
from ..canonical import canonical_json,sha256_hex,body_hash
from ..events import EventStore
from ..resolution_common import fields

PROVENANCE={'protocol','reservation_event_id','completion_event_id','origin_prompt_event_id','context_pack_id',
 'native_thread_id','native_turn_id','native_call_id','lease_generation','tool_name','args_sha256',
 'executor_manifest_sha256','producer_config_sha256','worker_receipt_id','worker_receipt_sha256','artifact_uri','artifact_sha256'}


def validate_provenance(value):
    import re
    fields(value,PROVENANCE,PROVENANCE)
    if value['protocol']!='jasmine.dynamic-evidence.v1':raise errors.InvalidRequest('unknown dynamic provenance')
    for name,prefix in (('reservation_event_id','evt'),('completion_event_id','evt'),('origin_prompt_event_id','evt'),('context_pack_id','ctx')):
        if not ids.is_id(value[name],prefix):raise errors.InvalidRequest('invalid dynamic provenance reference')
    for name in ('args_sha256','executor_manifest_sha256','producer_config_sha256','worker_receipt_sha256','artifact_sha256'):
        if not isinstance(value[name],str) or not re.fullmatch('[0-9a-f]{64}',value[name]):raise errors.InvalidRequest('invalid dynamic provenance SHA')
    for name in ('native_thread_id','native_turn_id','native_call_id','lease_generation','worker_receipt_id'):
        if not isinstance(value[name],str) or not 1<=len(value[name].encode('utf-8'))<=256:raise errors.InvalidRequest('invalid native identity')
    if value['tool_name'] not in ('jasmine_test','jasmine_playwright') or not isinstance(value['artifact_uri'],str) or not value['artifact_uri'].startswith('workspace:/'):
        raise errors.InvalidRequest('qualified TEST artifact required')


def _internal(value):
    if not value or value['source_system']!='core-native-adapter' or value['source_event_id']!=value['event_id'] or value['actor_kind']!='system':return False
    hashed={k:value[k] for k in ('event_type','source_system','source_event_id','actor_id','host_id','session_id','project_id','task_id','payload')}
    hashed['occurred_at']=None
    return body_hash(hashed)==value['body_sha256']


def source_valid(conn,row,event,project_id):
    """Read immutable graph under State's existing transaction, no worker IO."""
    from .. import SCHEMA_VERSION
    store=EventStore(conn,schema_version=SCHEMA_VERSION)
    try:
        p=event['payload']['operation_provenance'];validate_provenance(p)
        if event['payload'].get('operation_provenance_sha256')!=sha256_hex(p):return False
        reserve=store.get(p['reservation_event_id']);complete=store.get(p['completion_event_id'])
        if not _internal(reserve) or not _internal(complete) or reserve['event_type']!='adapter.operation_reserved' or complete['event_type']!='adapter.operation_completed':return False
        request=reserve['payload']['request'];outcome=complete['payload']['result']
        if outcome['status']!='SUCCEEDED' or outcome.get('evidence_provenance')!=p or outcome.get('evidence_provenance_sha256')!=sha256_hex(p):return False
        if not outcome.get('evidence') or outcome['evidence']['evidence_id']!=row['evidence_id']:return False
        if complete['seq']<=reserve['seq'] or outcome['reservation_event_id']!=reserve['event_id']:return False
        for obj in (reserve,complete,event):
            if obj['actor_id']!=row['actor_id'] or obj['actor_kind']!='system' or obj['host_id']!=row['host_id'] or obj['project_id']!=project_id or obj['task_id']!=row['task_id'] or obj['session_id']!=row['session_id']:return False
        if any(p[name]!=request[name] for name in ('native_thread_id','native_turn_id','native_call_id','lease_generation','context_pack_id')):return False
        if p['origin_prompt_event_id']!=request['source_event_id'] or p['args_sha256']!=sha256_hex(request['args']) or p['tool_name']!=request['tool']:return False
        if p['executor_manifest_sha256']!=reserve['payload']['result']['executor_manifest_sha256'] or p['worker_receipt_id']!=outcome['worker_receipt_id'] or p['worker_receipt_sha256']!=outcome['worker_receipt_sha256']:return False
        if row['kind']!='TEST' or row['tool_name']!=p['tool_name'] or row['artifact_sha256']!=p['artifact_sha256'] or row['artifact_uri']!=p['artifact_uri'] or row['producer_config_sha256']!=p['producer_config_sha256']:return False
        if row['turn_id']!=p['native_turn_id'] or row['tool_use_id']!=p['native_call_id'] or request['step_id']!=row['step_id']:return False
        call=store.get(event['payload']['call_event_id']);change=store.get(row['change_event_id'])
        if not call or not change or call['event_type']!='tool.call' or change['event_type']!='evidence.recorded' or not call['seq']<event['seq']<change['seq']:return False
        for linked in (call,change):
            if linked['payload'].get('operation_provenance')!=p or linked['payload'].get('operation_provenance_sha256')!=sha256_hex(p) or linked['actor_id']!=row['actor_id'] or linked['task_id']!=row['task_id'] or linked['host_id']!=row['host_id']:return False
        if change['payload'].get('evidence_id')!=row['evidence_id'] or change['payload'].get('source_event_id')!=event['event_id']:return False
        if any(change['payload'].get(k)!=row[k] for k in ('kind','status','step_id','fingerprint_sha256','artifact_uri','artifact_sha256')):return False
        import json
        if change['payload'].get('parsed_result')!=json.loads(row['result_json']):return False
        if outcome['evidence'].get('source_event_id')!=row['source_event_id'] or outcome['evidence'].get('fingerprint_sha256')!=row['fingerprint_sha256']:return False
        command=canonical_json(reserve['payload']['result']['executor_manifest']['argv'])
        if call['payload']['tool_input'].get('command')!=command or row['command_sha256']!=sha256_hex(command):return False
        from ..evidence import _tool_result
        status,_=_tool_result(event['payload'].get('tool_response'))
        return status==row['status']
    except (KeyError,TypeError,ValueError,errors.CoreError):return False
