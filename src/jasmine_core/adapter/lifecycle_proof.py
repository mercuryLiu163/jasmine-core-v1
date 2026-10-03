"""Shared complete lifecycle native proof validation; no receipt writes."""
from ..resolution_common import Conflict

NAMES={'UserPromptSubmit':'userPromptSubmit','SessionStart':'sessionStart','PreCompact':'preCompact',
       'PostCompact':'postCompact','Stop':'stop'}


def validate_native_lifecycle(receipt,request,hook_definition_path,*,allow_pending=False):
    # Provenance and invalid terminal evidence must never be hidden by pending.
    for name in ('hook_definition_sha256','profile_sha256','deployment_sha256'):
        if receipt.get(name)!=request[name]:raise Conflict('adapter_provenance_mismatch')
    notifications=receipt.get('notifications')
    if not isinstance(notifications,list) or len(notifications)>512 or not all(isinstance(n,dict) for n in notifications):
        raise Conflict('adapter_not_proved')
    started=[];completed=[];items=[];turns=[]
    for index,n in enumerate(notifications):
        method=n.get('method');params=n.get('params')
        if not isinstance(params,dict):raise Conflict('adapter_not_proved')
        turn=params.get('turn');nested=turn.get('id') if isinstance(turn,dict) else None
        direct=params.get('turnId')
        if direct is not None and nested is not None and direct!=nested:raise Conflict('adapter_not_proved')
        if params.get('threadId')!=request['native_thread_id'] or (direct if direct is not None else nested)!=request['native_turn_id']:
            raise Conflict('adapter_not_proved')
        run=params.get('run')
        if method in ('hook/started','hook/completed'):
            if not isinstance(run,dict):raise Conflict('adapter_not_proved')
            if run.get('eventName')!=NAMES[request['hook_event_name']]:continue
            if run.get('source')!='project' or run.get('handlerType')!='command' or run.get('sourcePath')!=hook_definition_path or \
               not isinstance(run.get('id'),str) or not run['id'] or \
               (request['hook_run_id'] is not None and request['hook_run_id']!=run['id']):raise Conflict('adapter_not_proved')
            if method=='hook/completed' and run.get('status')!='completed':raise Conflict('adapter_not_proved')
            (started if method=='hook/started' else completed).append((index,run))
        elif method in ('item/started','item/completed'):
            item=params.get('item')
            if not isinstance(item,dict):raise Conflict('adapter_not_proved')
            if item.get('type')!='contextCompaction':continue
            if not isinstance(item.get('id'),str) or not item['id']:raise Conflict('adapter_not_proved')
            items.append((index,method,item['id']))
        elif method=='turn/completed':
            if not isinstance(turn,dict) or turn.get('id')!=request['native_turn_id'] or turn.get('status')!='completed':
                raise Conflict('adapter_not_proved')
            turns.append(index)
        else:raise Conflict('adapter_not_proved')
    if len(started)>1 or len(completed)>1 or len(turns)>1 or len(items)>2:raise Conflict('adapter_not_proved')
    if completed and (not started or started[0][0]>=completed[0][0] or started[0][1]['id']!=completed[0][1]['id']):
        raise Conflict('adapter_not_proved')
    if items and (items[0][1]!='item/started' or (len(items)==2 and
        (items[1][1]!='item/completed' or items[0][2]!=items[1][2]))):raise Conflict('adapter_not_proved')
    if len(items)==2 and (not completed or completed[0][0]>=items[1][0]):raise Conflict('adapter_not_proved')
    if turns and (not completed or turns[0]<=completed[0][0] or (items and (len(items)!=2 or turns[0]<=items[-1][0]))):
        raise Conflict('adapter_not_proved')
    name=request['hook_event_name']
    if name not in ('PreCompact','Stop'):raise Conflict('adapter_not_proved')
    full=bool(started and completed and (len(items)==2 if name=='PreCompact' else turns))
    if not full:
        if allow_pending and not turns:raise Conflict('adapter_proof_pending')
        raise Conflict('adapter_not_proved')
    return completed[0][1]['id']
