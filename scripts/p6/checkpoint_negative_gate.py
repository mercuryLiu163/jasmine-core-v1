"""Recompute the frozen predicates from raw HTTP/DB/observer/DOM receipts.

Missing data is BLOCKED, contradictory data is FAIL. No input passed flag is
accepted as evidence. Absence has an explicit reason and a measured zero/null.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

SPEC_COMMIT = '5079948808d4c8aa9cb5763c747def422c12c055'
MATRIX_SHA256 = '5e3fcdd9808437dbad7908b52255c52a73b94ee4bf4496b6da9f29b83c954514'
OP_TABLES = ('events', 'checkpoints', 'checkpoint_request_keys', 'resume_results', 'resume_request_keys')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def find(snapshot, table, field, value):
    matches = [r for r in snapshot[table] if r[field] == value]
    if len(matches) != 1:
        raise ValueError(f'{table}.{field} expected one {value}, got {len(matches)}')
    return matches[0]


def added(before, after, table):
    a, b = {canonical(r) for r in before[table]}, {canonical(r) for r in after[table]}
    return [json.loads(r) for r in sorted(b - a)], a <= b


def business(snapshot):
    return {k: v for k, v in snapshot.items() if k not in OP_TABLES}


def decode_checkpoint(row):
    result = dict(row)
    for field in ('projection', 'workspace', 'note', 'trigger_provenance'):
        raw = result.pop(field + '_json')
        result[field] = json.loads(raw) if raw is not None else None
    return result


def operation_proof(op):
    h, before, after = op['http'], op['before'], op['after']
    target = h['body'].get('checkpoint') or h['body'].get('resume')
    fresh = h['method'] == 'POST' and h['status'] == 201 and h['body'].get('replayed') is False
    deltas = {t: added(before, after, t) for t in OP_TABLES}
    allowed = {t: 0 for t in OP_TABLES}
    stored = True
    if fresh:
        kind = 'checkpoint' if 'checkpoint' in h['body'] else 'resume'
        allowed.update(events=1)
        table = 'checkpoints' if kind == 'checkpoint' else 'resume_results'
        keytable = kind + '_request_keys'
        allowed[table] = allowed[keytable] = 1
        row = find(after, table, kind + '_id', target[kind + '_id'])
        stored = decode_checkpoint(row) == target if kind == 'checkpoint' else json.loads(row['result_json']) == target
        keyrow = find(after, keytable, 'idempotency_key', h['request_body']['idempotency_key'])
        stored = stored and keyrow['actor_id'] == h['actor_id'] and keyrow[kind + '_id'] == target[kind + '_id']
        stored = stored and keyrow['request_hash'] == sha({'body': h['request_body'], 'task_id': target['task_id']})
        event = find(after, 'events', 'event_id', target['command_event_id'])
        stored = stored and event['event_type'] == ('checkpoint.created' if kind == 'checkpoint' else 'resume.built')
        stored = stored and all(event[k] == target[k] for k in ('actor_id', 'host_id', 'project_id', 'task_id', 'session_id'))
        # Raw Event source_event_id is an ingress dedup key; the causal source is in payload.
        stored = stored and json.loads(event['payload_json'])['source_event_id'] == target['source_event_id']
    elif target is not None:
        kind = 'checkpoint' if 'checkpoint' in h['body'] else 'resume'
        row = find(after, 'checkpoints' if kind == 'checkpoint' else 'resume_results', kind + '_id', target[kind + '_id'])
        stored = decode_checkpoint(row) == target if kind == 'checkpoint' else json.loads(row['result_json']) == target
    exact_delta = all(append_only and len(rows) == allowed[t] for t, (rows, append_only) in deltas.items())
    if fresh:
        exact_delta = exact_delta and deltas['events'][0][0]['event_id'] == target['command_event_id']
    observer = op['observer']
    starts = [r for r in observer if r['kind'] == 'request_start']
    ends = [r for r in observer if r['kind'] == 'request_end']
    scans = [r for r in observer if r['kind'] == 'scanner_popen_attempt']
    calls = [r for r in observer if r['kind'] == 'scanner_call_enter']
    exits = [r for r in observer if r['kind'] == 'scanner_call_exit']
    complete = len(starts) == len(ends) == 1 and starts[0]['request_id'] == h['request_id'] == ends[0]['request_id']
    complete = complete and starts[0]['pid'] == ends[0]['pid'] == op['observer_started']['pid']
    complete = complete and starts[0]['seq'] < ends[0]['seq'] and ends[0]['status'] == h['status']
    complete = complete and all(starts[0]['seq'] < r['seq'] < ends[0]['seq'] and r['pid'] == starts[0]['pid'] for r in scans)
    must_not_scan = h['method'] == 'GET' or h['status'] in (403, 404) or h['body'].get('replayed') is True
    scan_ok = complete and (len(calls) == len(exits) == len(scans) == 0 if must_not_scan else
                           len(calls) == len(exits) == len(scans) == 1 and calls[0]['seq'] < scans[0]['seq'] < exits[0]['seq'])
    audit = op['audit']
    audit_ok = op['audit_read']['status'] == 200 and len(audit) == 1
    if audit_ok:
        a = audit[0]
        audit_ok = a['request_id'] == h['request_id'] and a['method'] == h['method'] and a['path'] == h['path'].split('?')[0]
        audit_ok = audit_ok and a['actor_id'] == h['actor_id'] and a['status_code'] == h['status']
        audit_ok = audit_ok and a['error_code'] == h['body'].get('error', {}).get('code')
        audit_ok = audit_ok and a['decision'] == ('deny' if h['status'] == 403 else 'allow')
    return {'business_unchanged': business(before) == business(after), 'exact_append_or_absence': exact_delta,
            'stored_result_and_request_hash': stored, 'observer_complete_and_expected_scan_count': scan_ok,
            'scanner_popen_attempt_count': len(scans), 'audit_bound': audit_ok,
            'scanner_function_entry_count': len(calls), 'scanner_function_exit_count': len(exits),
            'append_evidence': {t: {'added_count': len(v[0]), 'append_only': v[1], 'expected_count': allowed[t]} for t, v in deltas.items()},
            'interpretation': 'OBSERVED_SINGLE_APPEND' if fresh else 'OBSERVED_ZERO_APPEND', 'request_id': h['request_id']}


def lineage_proof(row):
    mappings = row['lineage']
    if not mappings:
        absence = row['absence']
        source = row['precondition_source_read']
        source_ok = source['status'] == 200 and source['body']['event'] == row['fixture']['source']
        return source_ok and bool(absence) and all(isinstance(v, dict) and v.get('reason') for v in absence.values()), {
            'kind': 'LEGITIMATE_PRECONDITION_WITH_EXPECTED_TARGET_ABSENCE', 'absence': absence,
            'precondition_source_read': source, 'reason': 'Only an existing fixture source is read; no absent target/receipt chain invented.'}
    results = []
    for m in mappings:
        entity = m['entity']
        source, command = m['source_read'], m['command_read']
        good = source['status'] == command['status'] == 200
        s, c = source['body']['event'], command['body']['event']
        good = good and s['event_id'] == entity['source_event_id'] and c['event_id'] == entity['command_event_id']
        good = good and all(c[k] == entity[k] for k in ('actor_id', 'host_id', 'project_id', 'task_id', 'session_id'))
        good = good and c['payload']['source_event_id'] == s['event_id'] and s['project_id'] == entity['project_id'] and s['task_id'] == entity['task_id']
        good = good and s['host_id'] == entity['host_id']
        if 'resume_id' in entity and entity['note'] is not None:
            good = good and entity['note']['authority'] is False and entity['note']['historical'] is True
        results.append({'entity_id': entity.get('checkpoint_id') if 'resume_id' not in entity else entity['resume_id'],
                        'passed': good, 'source_request_id': source['request_id'], 'command_request_id': command['request_id'],
                        'mutation_label': m.get('mutation_label')})
    return all(x['passed'] for x in results), {'kind': 'OBSERVED_EXISTING_ENTITIES', 'mappings': results, 'absence': row['absence']}


def jsonblock(dom, prefix):
    values = [json.loads(b['text']) for b in dom['jsonblocks'] if b['label'].startswith(prefix) and b['text'] is not None]
    if len(values) != 1:
        raise ValueError('Missing/duplicate expanded DOM JSON block ' + prefix)
    return values[0]


def display_proof(page):
    definition = page['definition']
    kind = definition['kind']
    results = []
    for observation in page['observations']:
        dom = observation['dom']
        text = dom['text']
        exact = dom['hash'] == definition['hash'] and bool(observation['dom_file']) and bool(observation['screenshot'])
        if kind in ('checkpoint', 'resume'):
            data = definition['expected'][kind]
            exact = exact and definition['entity_id'] == data[kind + '_id']
            exact = exact and data[kind + '_id'] in text and jsonblock(dom, 'projection') == data['projection'] and jsonblock(dom, 'workspace') == data['workspace']
            for field in ('actor_id', 'host_id', 'project_id', 'task_id', 'source_event_id', 'command_event_id'):
                exact = exact and data[field] in text
            exact = exact and any(data['source_event_id'] in (link['href'] or '') for link in dom['links'])
            exact = exact and any(data['command_event_id'] in (link['href'] or '') for link in dom['links'])
            if kind == 'checkpoint':
                exact = exact and bool(dom['checkpoint_boundary']) and str(data['task_revision']) in text
                exact = exact and all(data[k] in text for k in ('projection_digest', 'full_context_digest', 'workspace_digest'))
                exact = exact and data['projection_version'] in text and jsonblock(dom, 'trigger_provenance') == data['trigger_provenance']
                if data['note']:
                    exact = exact and data['note']['text'] in text and '非权威' in text
            else:
                exact = exact and jsonblock(dom, 'comparison') == data['comparison'] and jsonblock(dom, 'sample_window') == data['sample_window']
                exact = exact and all(str(data['comparison'][k]).lower() in (dom['comparison_status'] or '').lower()
                                       for k in ('checkpoint_freshness', 'workspace', 'reconciliation_required'))
                exact = exact and '重新读取不会重新比较' in dom['comparison_status'] and data['truth_digest'] in text
                if data['note']:
                    exact = exact and data['note']['authority'] is False and data['note']['historical'] is True
                    exact = exact and data['note']['value']['text'] in dom['note'] and 'authority = false' in dom['note']
                if data['next_actions']:
                    exact = exact and '建议而非已执行' in dom['next_actions'] and all(a['kind'] in dom['next_actions'] for a in data['next_actions'])
        elif kind in ('error', 'signin-error'):
            exact = exact and bool(dom['alerts']) and definition['expected']['error']['code'] in text and not dom['pending']
            exact = exact and dom['checkpoint_boundary'] is None and dom['comparison_status'] is None
            exact = exact and jsonblock(dom, 'error.details') == definition['expected']['error']['details']
        elif kind == 'invalid':
            exact = exact and dom['invalid'] and bool(dom['alerts']) and dom['checkpoint_boundary'] is None and dom['comparison_status'] is None
        elif kind == 'pending':
            exact = exact and dom['pending'] and dom['checkpoint_boundary'] is None and dom['comparison_status'] is None
        elif kind == 'empty':
            exact = exact and definition['expected']['checkpoint'] is None and '空:Checkpoint' in text and not dom['pending']
        else:
            exact = False
        target_network = [n for n in page['network'] if n['path'] == definition['path'].split('?')[0]
                          and n['query'] == ('?' + definition['path'].split('?')[1] if '?' in definition['path'] else '')]
        if kind == 'invalid':
            exact = exact and not target_network
        elif kind == 'pending':
            exact = exact and any(n.get('status') == 404 and n.get('body', {}).get('error', {}).get('code') == 'proxy_path_not_allowed' for n in target_network)
        elif kind in ('error', 'signin-error'):
            exact = exact and any(n.get('status') == definition['expected_status'] and n.get('body', {}).get('error', {}).get('code') == definition['expected']['error']['code'] for n in target_network)
        else:
            exact = exact and any(n.get('status') == 200 and n.get('body') == definition['expected'] for n in target_network)
            if definition.get('refresh'):
                exact = exact and sum(n.get('status') == 200 and n.get('body') == definition['expected'] for n in target_network) >= 2
        results.append({'passed': exact, 'kind': kind, 'entity_id': definition['entity_id'], 'dom_file': observation['dom_file'],
                        'screenshot': observation['screenshot'], 'matching_network_count': len(target_network)})
    return bool(results) and all(r['passed'] for r in results), results


def browser_audit_proof(ui):
    audit = ui['audit_read']
    if audit['status'] != 200:
        return False
    for n in ui['network']:
        if n.get('status') is None:
            continue  # cancellations are retained, not treated as successful reads
        if n.get('body', {}).get('error', {}).get('code') == 'proxy_path_not_allowed':
            if any(e.get('request_id') == n.get('request_id') for e in ui['observer_window']):
                return False
            continue
        if n['method'] == 'GET' and n['path'] in ('/v1/health', '/v1/meta/schema'):
            # These exact candidate routes are public=True and create no audit.
            # Keep their genuine ingress/egress and zero scan windows as absence proof.
            window = [e for e in ui['observer_window'] if e.get('request_id') == n.get('request_id')]
            starts = [e for e in window if e['kind'] == 'request_start']
            ends = [e for e in window if e['kind'] == 'request_end']
            if len(starts) != 1 or len(ends) != 1 or ends[0]['status'] != n['status'] or n['status'] != 200:
                return False
            if any(e['kind'] in ('scanner_call_enter', 'scanner_popen_attempt') for e in window):
                return False
            if any(a['request_id'] == n['request_id'] for a in audit['matching']):
                return False
            continue
        matches = [a for a in audit['matching'] if a['request_id'] == n['request_id']]
        if len(matches) != 1:
            return False
        a = matches[0]
        if (a['method'], a['path'], a['status_code']) != (n['method'], n['path'], n['status']):
            return False
        if a['error_code'] != n.get('body', {}).get('error', {}).get('code'):
            return False
        if a['actor_id'] != ui['key_metadata']['actor_id']:
            return False
    return True


def scenario_checks(row, ui, common):
    case = row['case_id']
    ops = row['operations']
    first = ops[0]['http']['body'].get('resume') if ops else None
    cp_op = row.get('checkpoint_creation')
    cp = cp_op['http']['body']['checkpoint'] if cp_op else None
    pages_ok = common['G7']['passed']
    source_ok = common['G5']['passed']
    append_ok = common['G2']['passed'] and common['G3']['passed']
    if case in ('CP01', 'CP02', 'CP03', 'CP04', 'CP05', 'CP06', 'CP07', 'CP08', 'CP09'):
        p = first['projection']
        comp = first['comparison']
        state = comp['checkpoint_freshness']
        fp = comp['workspace']
        recon = comp['reconciliation_required']
        note_good = first['note'] is None if cp is None else first['note']['value'] == cp['note'] and first['note']['authority'] is False and first['note']['historical'] is True
        generic = append_ok and source_ok and note_good
        if case == 'CP01':
            same = cp['projection'] == p and cp['workspace']['complete'] is True and first['workspace']['complete'] is True
            empty = all(not comp['diff'][k] for k in ('added', 'removed', 'changed')) and not comp['diff']['evidence_changed'] and all(not v for v in comp['diff']['workspace_paths'].values())
            return [generic and same, state == 'STATE_EQUAL' and fp == 'SAME' and recon is False and comp['warnings'] == [], empty, pages_ok]
        if case == 'CP02':
            return [generic and bool(row['preconditions']) and cp['task_revision'] < p['task']['revision'],
                    state == 'STALE' and fp == 'SAME' and recon is True and ['task', p['task_id']] in comp['diff']['changed'], pages_ok, common['G1']['passed'] and first['next_actions'] == [{'kind': 'RECONCILE_CURRENT_TRUTH'}]]
        if case == 'CP03':
            rule = row['fixture']['rule']
            old = next(r for r in cp['projection']['rules'] if r['rule_id'] == rule['rule_id'])
            new = next(r for r in p['rules'] if r['rule_id'] == rule['rule_id'])
            return [generic and cp['task_revision'] == p['task']['revision'] and old['status'] == 'PROPOSED' and new['status'] == 'ACTIVE' and old['revision'] < new['revision'],
                    state == 'CONTEXT_CHANGED' and fp == 'SAME' and recon is True, ['rule', rule['rule_id']] in comp['diff']['changed'], pages_ok]
        if case == 'CP04':
            run = row['preconditions'][0]
            evd = run['response']['evidence']
            return [generic and run['actual_exit_code'] == 0 and bool(run['actual_stdout']) and evd['status'] == 'PASS' and evd['task_id'] == p['task_id'] and cp['task_revision'] == p['task']['revision'],
                    state == 'CONTEXT_CHANGED' and fp == 'SAME' and recon is True,
                    cp['projection']['evidence_digest'] != p['evidence_digest'] and comp['diff']['evidence_changed'] is True, pages_ok]
        if case == 'CP05':
            return [generic and cp['workspace']['complete'] is True and first['workspace']['complete'] is True and cp['workspace']['selected_hashes']['tracked.txt'] != first['workspace']['selected_hashes']['tracked.txt'],
                    state == 'STATE_EQUAL' and fp == 'MISMATCH' and recon is True and comp['warnings'] == ['CHECKPOINT_WORKSPACE_MISMATCH'],
                    comp['diff']['workspace_paths'] == {'added': [], 'removed': [], 'changed': ['tracked.txt']}, pages_ok]
        if case in ('CP06', 'CP07', 'CP08'):
            left, right = cp['workspace'], first['workspace']
            partial = [s for s in (left, right) if s['complete'] is False]
            expected_complete = {'CP06': [False, True], 'CP07': [True, False], 'CP08': [False, False]}[case]
            actual_scan = generic and [left['complete'], right['complete']] == expected_complete and all('unreadable file' in s['partial_reasons'] for s in partial)
            config_same = all(left[k] == right[k] for k in ('algorithm', 'coverage', 'root', 'extra_paths'))
            if case == 'CP08':
                config_same = config_same and left['selected_hashes'] == right['selected_hashes'] and left['manifest_sha256'] == right['manifest_sha256']
            return [actual_scan, cp['projection'] == p and state == 'STATE_EQUAL' and fp == 'UNKNOWN' and recon is True and comp['warnings'] == ['CHECKPOINT_WORKSPACE_UNKNOWN'], config_same, pages_ok]
        return [row['latest']['status'] == 200 and row['latest']['body']['checkpoint'] is None and pages_ok,
                first['checkpoint_id'] is None and state == 'ABSENT' and fp == 'UNKNOWN' and recon is True and comp['diff'] == {},
                first['note'] is None and first['next_actions'] == [{'kind': 'RECONCILE_CURRENT_TRUTH'}], generic]
    if case == 'CP10':
        old, get, replay, fresh = [o['http'] for o in ops]
        before = old['body']['resume']
        after = fresh['body']['resume']
        return [get['status'] == 200 and get['body']['resume'] == before,
                replay['status'] == 200 and replay['body']['replayed'] is True and replay['body']['resume'] == before and operation_proof(ops[2])['scanner_popen_attempt_count'] == 0 and append_ok,
                fresh['status'] == 201 and fresh['body']['replayed'] is False and after['resume_id'] != before['resume_id'] and after['command_event_id'] != before['command_event_id'] and after['sample_window'] != before['sample_window'] and after['comparison'] == {**after['comparison'], 'checkpoint_freshness': 'STALE', 'workspace': 'SAME', 'reconciliation_required': True}, pages_ok]
    if case == 'CP11':
        creations = row['checkpoint_creations']
        a, b = [c['http']['body']['checkpoint'] for c in creations]
        ea = find(creations[-1]['after'], 'events', 'event_id', a['command_event_id'])
        eb = find(creations[-1]['after'], 'events', 'event_id', b['command_event_id'])
        s = row['selection']
        return [source_ok and append_ok and ea['seq'] < eb['seq'] and a['source_event_id'] == row['source_order'][1]['event_id'] and b['source_event_id'] == row['source_order'][0]['event_id'] and s[0]['body']['checkpoint'] == b,
                [x['body']['checkpoint']['checkpoint_id'] for x in s[1:]] == [a['checkpoint_id'], b['checkpoint_id']] and row['empty_selection']['body']['checkpoint'] is None,
                row['pages'][-1]['expected']['checkpoint'] == a and 'task_id=bad-task' in row['pages'][-1]['hash'], pages_ok]
    if case == 'CP12':
        switches = ui['switches']
        held = len(switches) == 2 and {s['kind'] for s in switches} == {'checkpoint', 'resume'} and all(s['held']['state'] == 'held' and s['held']['body'] == next(p['expected'] for p in row['pages'] if p['kind'] == s['kind'] and p['entity_id'] == s['a']) for s in switches)
        no_old = all(s['b'] in s['after']['dom']['text'] and s['a'] not in s['after']['dom']['text'] and all(i['value'] != s['a'] for i in s['after']['dom']['inputs']) for s in switches)
        return [held, no_old, no_old and all(s['before']['dom']['hash'] == s['after']['dom']['hash'] for s in switches), pages_ok and append_ok]
    if case in ('CP13', 'CP14', 'CP15', 'CP16', 'CP17', 'CP18', 'CP19', 'CP20', 'CP21'):
        injection = row['injection']
        p = json.loads(injection['mutated']['projection_json'])
        digest_valid = injection['mutated']['projection_digest'] == sha(p) == injection['mutated']['full_context_digest']
        mutation_good = {'CP13': lambda: p['task']['revision'] == injection['mutated']['task_revision'] > row['fixture']['task']['revision'],
            'CP14': lambda: next(o for o in p['objects'] if o['object_type'] == 'step')['revision'] > row['fixture']['step']['revision'] and p['task']['revision'] == row['fixture']['task']['revision'],
            'CP15': lambda: p['rules'][0]['version'] > row['fixture']['rule']['version'] and p['rules'][0]['revision'] == row['fixture']['rule']['revision'],
            'CP16': lambda: injection['original']['projection_digest'] != injection['mutated']['projection_digest'],
            'CP17': lambda: injection['original']['full_context_digest'] != injection['mutated']['full_context_digest'],
            'CP18': lambda: injection['original']['workspace_digest'] != injection['mutated']['workspace_digest'],
            'CP19': lambda: p == [],
            'CP20': lambda: injection['mutated']['projection_version'] == 'jasmine.task-snapshot.v0' if row['variant_id'] == 'column_version' else p['snapshot_schema'] == 'jasmine.task-snapshot.v0',
            'CP21': lambda: p['task_id'] != injection['mutated']['task_id']}[case]()
        if case in ('CP13', 'CP14', 'CP15', 'CP19', 'CP21') or (case == 'CP20' and row['variant_id'] == 'inner_schema'):
            mutation_good = mutation_good and digest_valid
        valid_integrity = injection['integrity_check'] == ['CHECK constraint failed in checkpoints'] if row['id'] == 'CP20.column_version' else injection['integrity_check'] == ['ok']
        fault_ok = injection['mode'] == 'REAL_CORE_CORRUPT_STORE' and injection['schema_before'] == injection['schema_after'] and injection['foreign_key_check'] == [] and injection['ignore_check_constraints_after'] == 0 and valid_integrity
        h = ops[0]['http']
        return [mutation_good, fault_ok and source_ok, h['status'] == 409 and h['body']['error']['code'] == 'checkpoint_integrity_error' and append_ok,
                pages_ok and row['absence']['resume']['observed_new_count'] == 0 and row['absence']['resume']['reason']]
    if case == 'CP22':
        key = row['injection']['synthetic_removed_key']
        p = first['projection']
        comp = first['comparison']
        return [row['injection']['mode'] == 'REAL_CORE_CORRUPT_STORE' and key and row['injection']['schema_before'] == row['injection']['schema_after'],
                first['projection']['task']['revision'] == cp['task_revision'] and comp['checkpoint_freshness'] == 'CONTEXT_CHANGED' and comp['workspace'] == 'SAME' and comp['reconciliation_required'] is True,
                comp['diff']['removed'] == [key] and key not in comp['diff']['changed'] and key not in comp['diff']['added'] and not any(o['object_id'] == key[1] for o in p['objects']), pages_ok and source_ok]
    if case == 'CP23':
        h = ops[0]['http']
        code = 'checkpoint_not_found' if row['variant_id'] == 'checkpoint' else 'resume_not_found'
        return [row['absence']['target']['expected'] is None and bool(row['absence']['target']['reason']), h['status'] == 404 and h['body']['error']['code'] == code and common['G6']['passed'], pages_ok and append_ok]
    if case == 'CP24':
        path = row['pages'][0]['path']
        absent = not any(n['path'] == path for n in ui['network']) and not any(e.get('path') == path for e in ui['observer_window'])
        return [pages_ok and bool(row['absence']['core_request']['reason']), absent, common['G1']['passed'] and append_ok]
    if case == 'CP25':
        h = ops[0]['http']
        required = ['objects:read', 'state:read', 'authority:read', 'evidence:read', 'events:read', 'checkpoint:read'] + (['resume:read'] if row['variant_id'].startswith('resume') else [])
        scopes = ui['key_metadata']['scopes']
        signin_absence = row['missing_scope'] != 'objects:read' or (row['absence']['target_browser_request']['expected_count'] == 0 and
            not any(n['path'] == row['absence']['target_browser_request']['target_path'] for n in ui['network']) and
            not any(e.get('path') == row['absence']['target_browser_request']['target_path'] for e in ui['observer_window']))
        return [set(required) - set(scopes) == {row['missing_scope']} and h['key_id'] == ui['key_metadata']['key_id'],
                h['status'] == 403 and h['body']['error']['code'] == 'forbidden_scope' and h['body']['error']['details']['required'] == row['missing_scope'] and common['G6']['passed'], pages_ok and append_ok and signin_absence]
    if case == 'CP26':
        probe = ui.get('proxy_probe')
        if probe:
            refused = probe['status'] == 404 and probe['body']['error']['code'] == 'proxy_path_not_allowed' and probe['channel'] == 'operator-proxy-probe-not-UI-write'
            absent = not any(e['kind'] == 'request_start' and e.get('path') == probe['path'] and e.get('method') == 'POST' for e in ui['observer_window'])
        else:
            refused = pages_ok
            absent = not any(e['kind'] == 'request_start' and e.get('path') == row['pages'][0]['path'] for e in ui['observer_window'])
        return [refused, absent, pages_ok and bool(row['absence']['core_request']['reason']), common['G1']['passed'] and common['G4']['passed']]
    raise ValueError('Unknown frozen case ' + case)


def evaluate_record(row, ui, case):
    operations = row['operations'] + ([row['checkpoint_creation']] if row.get('checkpoint_creation') else [])
    operations += row.get('checkpoint_creations', []) + row.get('resume_creations', [])
    unique = {o['http']['request_id']: o for o in operations}
    proofs = [operation_proof(o) for o in unique.values()]
    lineage_ok, lineage = lineage_proof(row)
    displays = [display_proof(p) for p in ui['pages']]
    browser_unchanged = ui['before'] == ui['after']
    measured_absence = {'reason': row['absence'], 'browser_database_rows_unchanged': browser_unchanged,
                        'scanner_popen_attempt_count': sum(e['kind'] == 'scanner_popen_attempt' for e in ui['observer_window']),
                        'scanner_function_entry_count': sum(e['kind'] == 'scanner_call_enter' for e in ui['observer_window']),
                        'browser_operational_table_deltas': {t: len(ui['after'][t]) - len(ui['before'][t]) for t in OP_TABLES}}
    common = {
        'G1': {'passed': browser_unchanged and all(p['business_unchanged'] for p in proofs), 'evidence': {'browser_snapshot_equal': browser_unchanged, 'operations': proofs}},
        'G2': {'passed': browser_unchanged and all(p['exact_append_or_absence'] and p['stored_result_and_request_hash'] for p in proofs), 'evidence': proofs or measured_absence},
        'G3': {'passed': measured_absence['scanner_popen_attempt_count'] == measured_absence['scanner_function_entry_count'] == 0 and all(p['observer_complete_and_expected_scan_count'] for p in proofs), 'evidence': proofs or measured_absence},
        'G4': {'passed': all(n['method'] == 'GET' for n in ui['network']), 'evidence': {'network': ui['network'], 'explicit_operator_probe': ui.get('proxy_probe')}},
        'G5': {'passed': lineage_ok, 'evidence': lineage},
        'G6': {'passed': all(p['audit_bound'] for p in proofs) and browser_audit_proof(ui), 'evidence': {'operations': proofs, 'browser_audit': ui['audit_read'], 'observer_window': ui['observer_window']}},
        'G7': {'passed': bool(displays) and all(d[0] for d in displays), 'evidence': [d[1] for d in displays]},
    }
    outcomes = scenario_checks(row, ui, common)
    if len(outcomes) != len(case['required_predicates']):
        raise ValueError('predicate implementation count drift')
    predicates = [{'id': p['id'], 'assertion': p['assertion'], 'passed': bool(value),
                   'evidence': {'raw_record': row['id'], 'browser_record': ui['id'], 'global_invariants': list(common)}}
                  for p, value in zip(case['required_predicates'], outcomes)]
    expected = case['expected']
    if 'checkpoint_freshness' in expected:
        op = row['operations'][0]['http']
        comparison = op['body']['resume']['comparison']
        expected_ok = op['status'] == expected['http_status'] and all(comparison[k] == expected[k] for k in ('checkpoint_freshness', 'workspace', 'reconciliation_required'))
    elif 'error_code' in expected:
        h = row['operations'][0]['http']
        expected_ok = h['status'] == expected['http_status'] and h['body']['error']['code'] == expected['error_code']
    else:
        expected_ok = True
    good = expected_ok and row['mode'] == case['evidence_mode'] and ui['status'] == 'COLLECTED_NOT_A_VERDICT' and all(p['passed'] for p in predicates) and all(g['passed'] for g in common.values())
    return {'id': row['id'], 'status': 'PASS' if good else 'FAIL', 'mode': row['mode'], 'expected_comparison_or_error': expected_ok,
            'predicates': predicates, 'invariants': common}


def evaluate_bundle(raw, matrix, *, expected_candidate=None, expected_archive=None):
    required = [(case, variant, case['id'] + '.' + variant['id']) for case in matrix['cases'] for variant in case['variants']]
    output = {'status': 'BLOCKED', 'role': raw.get('role'), 'scope_acceptance_signed': False, 'records': [],
              'candidate_commit': raw.get('candidate_commit'), 'source_archive_sha256': raw.get('source_archive_sha256'),
              'spec_commit': SPEC_COMMIT, 'coverage': 'Frozen matrix only; absence not fabricated; author PASS is not scoped acceptance.'}
    records = raw.get('records', [])
    browser_records = raw.get('browser', {}).get('records', [])
    uniqueness = len(records) == len({r['id'] for r in records}) == 43 and len(browser_records) == len({r['id'] for r in browser_records}) == 43
    for case, variant, ident in required:
        try:
            r = next(r for r in records if r['id'] == ident)
            ui = next(r for r in browser_records if r['id'] == ident)
            output['records'].append(evaluate_record(r, ui, case))
        except Exception as exc:
            output['records'].append({'id': ident, 'status': 'BLOCKED', 'reason': type(exc).__name__ + ': ' + str(exc),
                'predicates': [{'id': p['id'], 'passed': False, 'reason': 'Required raw receipt unavailable or malformed'} for p in case['required_predicates']]})
    binding = raw.get('spec_commit') == SPEC_COMMIT and raw.get('matrix_sha256') == MATRIX_SHA256 == hashlib.sha256((json.dumps(matrix, ensure_ascii=False, indent=2) + '\n').encode()).hexdigest()
    binding = binding and isinstance(raw.get('candidate_commit'), str) and len(raw['candidate_commit']) == 40 and isinstance(raw.get('source_archive_sha256'), str) and len(raw['source_archive_sha256']) == 64
    binding = binding and bool(expected_candidate) and bool(expected_archive) and raw.get('candidate_commit') == expected_candidate and raw.get('source_archive_sha256') == expected_archive
    cleanup = bool(raw.get('cleanup')) and all(p.get('group_gone') is True for p in raw['cleanup']) and raw.get('profile_removed') is True and raw.get('private_runtime_removed') is True
    browser = raw.get('browser', {})
    browser_ok = browser.get('console_errors') == [] and browser.get('credential_storage') == {'local': 0, 'session': 0, 'indexed': 0} and bool(browser.get('browser_version'))
    invariant = bool(raw.get('browser_database_invariants')) and all(r['before'] == r['after'] for r in raw['browser_database_invariants'])
    sources = raw.get('core_source_hashes', {})
    observer_sources = bool(sources)
    for r in records:
        operations = r.get('operations', []) + r.get('checkpoint_creations', []) + r.get('resume_creations', [])
        if r.get('checkpoint_creation'):
            operations.append(r['checkpoint_creation'])
        for op in operations:
            s = op.get('observer_started', {})
            observer_sources = observer_sources and s.get('continuity_source_sha256') == sources.get('src/jasmine_core/continuity.py') and s.get('scanner_source_sha256') == sources.get('src/jasmine_core/continuity_scan.py')
    output['binding'] = binding
    output['complete_unique_matrix'] = uniqueness
    output['cleanup'] = cleanup
    output['actual_browser_and_no_persistent_credentials'] = browser_ok
    output['browser_database_invariants'] = invariant
    output['observed_scanner_sources_bound'] = observer_sources
    output['counts'] = {s: sum(r['status'] == s for r in output['records']) for s in ('PASS', 'FAIL', 'BLOCKED')}
    if binding and uniqueness and cleanup and browser_ok and invariant and observer_sources and not raw.get('error') and all(r['status'] == 'PASS' for r in output['records']):
        output['status'] = 'PASS'
    elif output['counts']['BLOCKED']:
        output['status'] = 'BLOCKED'
    else:
        output['status'] = 'FAIL'
    return output


def adversarial_bundle_checks(raw, matrix, expected_candidate, expected_archive):
    """Mutated in-memory copies of this real run; never rewrite its raw evidence."""
    baseline = evaluate_bundle(raw, matrix, expected_candidate=expected_candidate, expected_archive=expected_archive)
    if baseline['status'] != 'PASS':
        return {'status': 'BLOCKED', 'reason': 'Real baseline must pass before full-bundle adversaries are evaluable'}
    checks = []
    for name in ('missing_variant', 'unknown_to_same', 'missing_refusal_after', 'wrong_object_id', 'missing_fault_label', 'missing_dom', 'wrong_candidate', 'wrong_archive'):
        changed = copy.deepcopy(raw)
        record = lambda ident: next(r for r in changed['records'] if r['id'] == ident)
        browser = lambda ident: next(r for r in changed['browser']['records'] if r['id'] == ident)
        if name == 'missing_variant':
            changed['records'].pop()
        elif name == 'unknown_to_same':
            record('CP06.default')['operations'][0]['http']['body']['resume']['comparison']['workspace'] = 'SAME'
        elif name == 'missing_refusal_after':
            del record('CP13.default')['operations'][0]['after']
        elif name == 'wrong_object_id':
            browser('CP01.default')['pages'][0]['definition']['entity_id'] = 'ckp_WRONG_OBJECT_COMPONENT_ADVERSARY'
        elif name == 'missing_fault_label':
            del record('CP13.default')['injection']['mode']
        elif name == 'missing_dom':
            del browser('CP01.default')['pages'][0]['observations'][0]['dom']
        elif name == 'wrong_candidate':
            changed['candidate_commit'] = '0' * 40
        else:
            changed['source_archive_sha256'] = '0' * 64
        result = evaluate_bundle(changed, matrix, expected_candidate=expected_candidate, expected_archive=expected_archive)
        checks.append({'mutation': name, 'observed_verdict': result['status'], 'rejected': result['status'] != 'PASS', 'counts': result['counts']})
    return {'status': 'PASS' if all(c['rejected'] for c in checks) else 'FAIL', 'kind': 'COMPONENT_FULL_BUNDLE_ADVERSARIES_NOT_MATRIX_VARIANTS', 'checks': checks}


def verify_directory(directory, expected_candidate, repository):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['candidate_commit'] != expected_candidate:
        raise ValueError('wrong expected candidate')
    paths = [f['path'] for f in manifest['files']]
    if len(paths) != len(set(paths)) or not {'raw-result.json', 'matrix.json', 'browser.json', 'result.json'} <= set(paths):
        raise ValueError('missing/duplicate mandatory artifact')
    for f in manifest['files']:
        p = directory / f['path']
        if p.is_symlink() or not p.resolve().is_relative_to(directory.resolve()):
            raise ValueError('unsafe artifact path')
        data = p.read_bytes()
        if len(data) != f['bytes'] or hashlib.sha256(data).hexdigest() != f['sha256']:
            raise ValueError('artifact hash mismatch: ' + f['path'])
    raw = json.loads((directory / 'raw-result.json').read_text())
    if raw['candidate_commit'] != expected_candidate or raw['source_archive_sha256'] != manifest['source_archive_sha256']:
        raise ValueError('raw candidate/archive mismatch')
    archive = subprocess.check_output(['git', '-C', str(repository), 'archive', '--format=tar', expected_candidate])
    expected_archive = hashlib.sha256(archive).hexdigest()
    if raw['source_archive_sha256'] != expected_archive:
        raise ValueError('archive differs from independently reconstructed candidate')
    expected_tree = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', expected_candidate + '^{tree}'], text=True).strip()
    if raw['source_tree'] != expected_tree:
        raise ValueError('wrong source tree')
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for field in ('core_source_hashes', 'launcher_source_hashes'):
            for path, value in raw[field].items():
                if hashlib.sha256(source.extractfile(path).read()).hexdigest() != value:
                    raise ValueError('source bytes differ from candidate: ' + path)
        if hashlib.sha256(source.extractfile('web/package-lock.json').read()).hexdigest() != raw['toolchain']['lockfile_sha256']:
            raise ValueError('wrong lockfile')
    if raw['browser'] != json.loads((directory / 'browser.json').read_text()):
        raise ValueError('embedded browser differs from artifact')
    for r in raw.get('browser', {}).get('records', []):
        observations = [o for p in r.get('pages', []) for o in p.get('observations', [])]
        observations += [s[k] for s in r.get('switches', []) for k in ('before', 'after')]
        if r.get('failure_dom'):
            observations.append(r['failure_dom'])
        for o in observations:
            if o['dom_file'] not in paths or o['screenshot'] not in paths:
                raise ValueError('missing DOM/screenshot artifact')
            if json.loads((directory / o['dom_file']).read_text()) != o['dom']:
                raise ValueError('embedded DOM differs from artifact')
    matrix = json.loads((directory / 'matrix.json').read_text())
    value = evaluate_bundle(raw, matrix, expected_candidate=expected_candidate, expected_archive=expected_archive)
    checks = adversarial_bundle_checks(raw, matrix, expected_candidate, expected_archive)
    if checks != json.loads((directory / 'harness-selfchecks.json').read_text()):
        raise ValueError('recomputed harness selfchecks differ')
    value['harness_selfchecks'] = checks['status']
    if value['status'] == 'PASS' and checks['status'] != 'PASS':
        value['status'] = 'FAIL'
    if value != json.loads((directory / 'result.json').read_text()):
        raise ValueError('recomputed verdict differs')
    return value


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--expected-candidate', required=True)
    p.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[2])
    a = p.parse_args()
    result = verify_directory(a.bundle, a.expected_candidate, a.repository)
    print(json.dumps({'status': result['status'], 'counts': result['counts']}))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
