"""The sole P2 injection serializer; P3 extends this same authoritative path."""
from ..canonical import canonical_json

PLURALS = {'project': 'projects', 'task': 'tasks', 'step': 'steps', 'rule': 'rules'}


def coherent_snapshot(client, interpretation_id, task_id, step_id, references):
    """Compare every independently fetched record to both complete previews.

    A bounded optimistic retry accommodates one concurrent writer; membership,
    revision and full context digest must all stay equal. No HTTP write locks.
    """
    path = '/v1/resolutions/preview?interpretation_id=' + interpretation_id
    for attempt in range(2):
        before = client.get(path)
        members = before['expected_revisions']
        records = {}
        for member in members:
            kind, ident = member['object_type'], member['object_id']
            record = client.get('/v1/' + PLURALS[kind] + '/' + ident)[kind]
            if record[kind + '_id'] != ident or record['revision'] != member['revision']:
                break
            records[(kind, ident)] = record
        after = client.get(path)
        if (len(records) != len(members) or members != after['expected_revisions'] or
                before['expected_context_digest'] != after['expected_context_digest'] or
                before['maintenance_head'] != after['maintenance_head']):
            continue
        task = records.get(('task', task_id))
        step = records.get(('step', step_id))
        if (not task or not step or task['status'] != 'ACTIVE' or
                step['task_id'] != task_id or task['project_id'] != references['project_id']):
            raise ValueError('bound Task/Step unavailable')
        rules = [record for (kind, _), record in records.items() if kind == 'rule' and record['status'] == 'ACTIVE']
        history_refs = [{'rule_id': record['rule_id'], 'version': record['version'], 'revision': record['revision'], 'status': record['status']}
                        for (kind, _), record in records.items() if kind == 'rule' and record['status'] != 'ACTIVE']
        value = {'schema': 'jasmine.p2.current-state.v1', 'references': references,
                 'task': task, 'step': step,
                 'active_rules': rules, 'inactive_rule_refs': history_refs, 'expected_revisions': members,
                 'context_digest': before['expected_context_digest'],
                 'maintenance_head': before['maintenance_head']}
        text = 'Jasmine P2 current state: ' + canonical_json(value)
        if len(text.encode('utf-8')) > 8192:
            raise ValueError('context_too_large')
        return text, value
    raise ValueError('context_conflict')
