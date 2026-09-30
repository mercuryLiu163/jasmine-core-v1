"""Ordered, bounded semantic policy. Model impact is never rewritten."""
POLICY_VERSION = 'jasmine.resolution.v1'


def compile_plan(source, candidates):
    result = []
    for index, candidate in enumerate(candidates):
        kind = candidate['kind']
        action = 'NO_ACTION'
        reason = 'no_structure'
        pending = False
        if kind != 'NO_STRUCTURE':
            pending = True
            reason = 'manual_plan_required'
            if candidate['certainty'] != 'EXPLICIT':
                reason = 'not_explicit'
            elif source['source_type'] != 'USER_EXPLICIT':
                reason = 'source_not_user_explicit'
            elif candidate['scope']['kind'] != 'TASK' or candidate['impact'] == 'HIGH':
                reason = 'scope_or_impact_requires_review'
            elif kind == 'TASK_CREATE_OR_ATTACH':
                action = 'ATTACH_TASK' if source['task_id'] else 'CREATE_TASK'
                pending = False
                reason = 'explicit_task_binding'
            elif kind == 'REQUIRED_CAPABILITY':
                action = 'CREATE_RULE'
                pending = False
                reason = 'task_additive_normal_context'
        result.append({'candidate_index': index, 'candidate': candidate, 'action': action,
                       'pending': pending, 'reason': reason})
    # A task-null capability alone cannot select or invent a Task.
    if not source['task_id'] and not any(r['action'] == 'CREATE_TASK' for r in result):
        for item in result:
            if item['action'] == 'CREATE_RULE':
                item.update(pending=True, reason='task_binding_required')
    disposition = ('PENDING_REVIEW' if any(r['pending'] for r in result) else
                   'APPLIED' if any(r['action'] != 'NO_ACTION' for r in result) else 'NO_ACTION')
    return {'policy_version': POLICY_VERSION, 'disposition': disposition, 'items': result}
