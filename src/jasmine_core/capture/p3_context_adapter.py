"""Component emission boundary for the sole P3 Core pack.

Not installed into frozen P2 hooks. P3-03 wires this into its reviewed lifecycle
adapter with a bounded client capable of the 4 MiB full Context response. P2's
2 MiB client is not silently declared compatible with that larger protocol.
"""
from ..canonical import sha256_hex


def _same_turn(state,session_id,turn_id,generation):
    if not state or state.get('session_id')!=session_id or state.get('turn_id')!=turn_id or \
       state.get('generation')!=generation:
        raise ValueError('context lease is not the current native turn')


def prepare_emission(client,lease,deadline,request,*,session_id,turn_id,generation,
                     hook_definition_hash,tokenizer,actor_id):
    """Persist exact request first, validate fresh receipt, then persist READY.

    Caller owns the protected turn lease lock and source admission. The output
    is exactly the Core renderer bytes. This component proves emitted bytes,
    not actual native model-visible injection or platform tool interception.
    """
    state=lease.read();_same_turn(state,session_id,turn_id,generation)
    if state.get('phase') not in ('SOURCE_ADMITTED','CONTEXT_PENDING','READY'):
        raise ValueError('source admission or same-turn recovery is required')
    admitted={k:state.get(k) for k in ('event_id','task_id','step_id','host_id','project_id','session_id','turn_id','generation')}
    if state.get('event_id')!=request['source_event_id'] or state.get('task_id')!=request['task_id'] or \
       state.get('step_id')!=request.get('current_step_id') or state.get('host_id')!=request['host_id']:
        raise ValueError('context request is not this admitted source/Task/Step')
    saved=state.get('context_request')
    if saved is not None and saved!=request:raise ValueError('exact context recovery request changed')
    for obsolete in ('context_text','context_digest','expected_revisions'):
        state.pop(obsolete,None)
    state.update(phase='CONTEXT_PENDING',context_request=request)
    lease.write(state);deadline.remaining()
    reply=client.post('/v1/context/build',request,cap=min(30,deadline.remaining()))
    pack=reply['context'];text=pack['rendered_content']
    if pack['actor_id']!=actor_id or pack['host_id']!=request['host_id'] or pack['project_id']!=admitted['project_id'] or \
       pack['source_event_id']!=request['source_event_id'] or pack['task_id']!=request['task_id'] or \
       pack.get('session_id')!=request.get('session_id') or pack.get('current_step_id')!=request.get('current_step_id') or \
       pack['rendered_sha256']!=sha256_hex(text) or pack['rendered_utf8_bytes']!=len(text.encode('utf-8')) or \
       pack['token_count']!=tokenizer.count(text) or pack['token_count']>2500 or pack['tokenizer']!=tokenizer.identity:
        raise ValueError('stored Context bytes/tokenizer or binding mismatch')
    deadline.remaining()
    checking={k:request[k] for k in ('host_id','source_event_id','session_id','current_step_id') if k in request}
    fresh=client.post('/v1/context/'+pack['context_pack_id']+'/check-current',checking,cap=deadline.remaining(3))
    if fresh.get('current') is not True or fresh['context_pack_id']!=pack['context_pack_id'] or \
       fresh['truth_digest']!=pack['truth_digest'] or fresh['selector']!=pack['selector'] or \
       fresh['rendered_sha256']!=pack['rendered_sha256']:
        raise ValueError('Context fresh comparison receipt mismatch')
    current=lease.read();_same_turn(current,session_id,turn_id,generation)
    if any(current.get(k)!=v for k,v in admitted.items()) or current.get('phase')!='CONTEXT_PENDING' or \
       current.get('event_id')!=request['source_event_id'] or current.get('task_id')!=request['task_id'] or \
       current.get('step_id')!=request.get('current_step_id') or current.get('context_request')!=request:
        raise ValueError('Context source changed before emission')
    receipt={'context_pack_id':pack['context_pack_id'],'rendered_sha256':pack['rendered_sha256'],
        'utf8_bytes':pack['rendered_utf8_bytes'],'token_count':pack['token_count'],
        'session_id':session_id,'turn_id':turn_id,'lease_generation':generation,
        'hook_definition_hash':hook_definition_hash,'fresh_checked_at':fresh['checked_at']}
    current.update(phase='READY',context_pack_id=pack['context_pack_id'],context_sha256=pack['rendered_sha256'],
        transmission_receipt=receipt,ready_deadline_monotonic=deadline.end)
    lease.write(current)
    try:deadline.remaining()
    except BaseException:
        current['phase']='CONTEXT_TIMEOUT'
        try:lease.write(current)
        except Exception:pass
        raise
    return text,receipt
