#!/usr/bin/env python3.13
"""Negative component copies of an actual sealed run; never acceptance sessions.

Every changed JSON artifact is replaced atomically in a private copy, with a new
index/manifest. Unchanged hard links are strictly read-only. Original bytes stay
sealed. Thus failures must follow content/completeness checks, not stale hashes.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint_negative_gate as gate


def load(path):
    return json.loads(path.read_text())


def write(path, value):
    temporary = path.with_name(path.name + '.private-write')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)  # never write through a link to a sealed original


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(raw, ident):
    return next(r for r in raw['records'] if r['id'] == ident)


def browser(raw, ident):
    return next(r for r in raw['browser']['records'] if r['id'] == ident)


def action_dom(raw, ident):
    return next(p for p in browser(raw, ident)['pages'] if p['definition']['kind'] == 'resume')['observations'][0]['dom']


def object_page(raw, ident, kind):
    return next(p for p in browser(raw, ident)['pages'] if p['definition']['kind'] == kind)


def replace_dom(dom, old, new):
    for key in ('text', 'html'):
        dom[key] = dom[key].replace(old, new)


def all_networks(raw):
    yield raw['browser']['network']
    for ui in raw['browser']['records']:
        yield ui['network']
        for p in ui['pages']: yield p['network']
        for s in ui['switches']: yield s['network']


def readback_mutation(raw, name):
    """Sync all derived expectations/wire/DOM; physical DB and lineage stay true."""
    if name in ('browser_expected_note_disagrees_with_DB', 'browser_resume_note_disagrees_with_DB', 'browser_wire_note_disagrees_with_DB'):
        kind = 'resume' if name == 'browser_resume_note_disagrees_with_DB' else 'checkpoint'
        ui = browser(raw, 'CP12.default'); target = next(s for s in ui['switches'] if s['kind'] == kind)['b']
        p = next(p for p in record(raw, 'CP12.default')['pages'] if p['entity_id'] == target)
        old = p['expected'][kind]['note']['value']['text'] if kind == 'resume' else p['expected'][kind]['note']['text']
        new = 'WRONG B NOTE: copied from an unrelated historical source'
        def note(data):
            (data['note']['value'] if kind == 'resume' else data['note'])['text'] = new
        if name != 'browser_wire_note_disagrees_with_DB':
            note(p['expected'][kind])
            for page in ui['pages']:
                if page['definition']['entity_id'] != target: continue
                note(page['definition']['expected'][kind])
                for o in page['observations']:
                    o['dom']['rawtexts'][0]['text'] = new; replace_dom(o['dom'], old, new)
            for s in ui['switches']:
                if s['kind'] != kind: continue
                for phase in ('before', 'after'):
                    s[phase]['dom']['rawtexts'][0]['text'] = new; replace_dom(s[phase]['dom'], old, new)
        for network in all_networks(raw):
            for n in network:
                if n['path'] == '/v1/' + ('resumes/' if kind == 'resume' else 'checkpoints/') + target and n.get('status') == 200:
                    note(n['body'][kind])
        return {'variant': 'CP12.default', 'target': target, 'original_note': old, 'wrong_note': new, 'DB_and_lineage_unchanged': True}
    ui = browser(raw, 'CP11.default')
    p = ui['pages'][0]; definition = p['definition']; path = definition['path']
    prepared = record(raw, 'CP11.default')['pages'][0]
    if name == 'browser_latest_wrong_selection':
        other = ui['pages'][1]
        new = copy.deepcopy(other['definition']['expected'])
        definition['entity_id'] = prepared['entity_id'] = new['checkpoint']['checkpoint_id']
        replacement = other['observations'][0]['dom']
    else:
        new = {'checkpoint': None}; replacement = next(p for p in ui['pages'] if p['definition']['kind'] == 'empty')['observations'][0]['dom']
        definition['kind'] = prepared['kind'] = 'empty'
    definition['expected'] = copy.deepcopy(new); prepared['expected'] = copy.deepcopy(new)
    for o in p['observations']:
        o['dom'] = copy.deepcopy(replacement); o['dom']['hash'] = definition['hash']
    for network in all_networks(raw):
        for n in network:
            if n['path'] + n.get('query', '') == path and n.get('status') == 200:
                n['body'] = copy.deepcopy(new)
    return {'variant': 'CP11.default', 'mutation': name, 'prepared_wrong_body': new, 'DB_and_lineage_unchanged': True}


def mutate(raw, name, directory):
    if name.startswith('browser_'):
        return readback_mutation(raw, name)
    if name.startswith('stale_success_') or name == 'old_success_data_on_404':
        targets = {'old_success_data_on_404': 'CP23.checkpoint', 'stale_success_resume_404': 'CP23.resume',
            'stale_success_checkpoint_invalid': 'CP24.checkpoint', 'stale_success_resume_invalid': 'CP24.resume',
            'stale_success_signin': 'CP25.checkpoint_missing_objects_read', 'stale_success_scope': 'CP25.checkpoint_missing_state_read',
            'stale_success_pending': 'CP26.p3_off_get', 'stale_success_empty': 'CP09.default'}
        ident = targets[name]; p = browser(raw, ident)['pages'][0]
        source_kind = 'resume' if 'resume' in name else 'checkpoint'
        source = object_page(raw, 'CP01.default', source_kind)['observations'][0]['dom']
        for o in p['observations']:
            d = o['dom']
            for k in ('kv', 'digests', 'rawtexts'): d[k] = copy.deepcopy(source[k])
            d['jsonblocks'] += copy.deepcopy(source['jsonblocks'])
            d['text'] += '\n' + str(source['kv']) + str(source['rawtexts']) + str(source['jsonblocks'])
            d['html'] += '\n<dl class="kv">' + ''.join('<dt>'+v['label']+'</dt><dd>'+v['text']+'</dd>' for v in source['kv']) + '</dl>'
        return {'variant': ident, 'mutation': name, 'dom': p['observations'][0]['dom']}
    if name in ('incorrect_created_at', 'incorrect_resume_created_at', 'wrong_event_navigation', 'wrong_command_navigation', 'wrong_resume_event_navigation', 'wrong_resume_command_navigation', 'wrong_project_navigation', 'wrong_task_navigation', 'wrong_resume_checkpoint_navigation', 'wrong_trigger_level', 'wrong_resume_checkpoint_id'):
        kind = 'resume' if 'resume' in name else 'checkpoint'; p = object_page(raw, 'CP02.default', kind)
        for o in p['observations']:
            d = o['dom']
            if 'created_at' in name:
                field = next(v for v in d['kv'] if v['label'] == 'created_at'); old = field['text']; field['text'] = '1999-01-01 00:00:00.000Z'; replace_dom(d, old, field['text'])
            elif name in ('wrong_trigger_level', 'wrong_resume_checkpoint_id'):
                label = 'trigger_provenance' if kind == 'checkpoint' else 'checkpoint'
                next(v for v in d['kv'] if v['label'] == label)['text'] = 'WRONG'
            else:
                prefix = '≡command Event' if 'command' in name else '≡source Event'
                if 'project' in name or 'task' in name or 'checkpoint_navigation' in name:
                    field = 'project_id' if 'project' in name else 'task_id' if 'task' in name else 'checkpoint_id'
                    link = next(v for v in d['links'] if v['text'] == p['definition']['expected'][kind][field])
                else:
                    link = next(v for v in d['links'] if v['text'].startswith(prefix))
                old = link['href']; link['href'] = '#/event/evt_00000000000000000000000000?original=' + old.rsplit('/', 1)[-1]; replace_dom(d, old, link['href'])
        return {'variant': 'CP02.default', 'mutation': name, 'dom': p['observations'][0]['dom']}
    if name.startswith('late_') and name != 'late_switch_wrong_dynamic_details':
        kind, fault = name[len('late_'):].split('_', 1)
        switch = next(s for s in browser(raw, 'CP12.default')['switches'] if s['kind'] == kind)
        dom = switch['after']['dom']
        if fault == 'note':
            dom['rawtexts'][0]['text'] = 'WRONG historical note from A'
        elif fault == 'digest':
            dom['digests'][0]['value'] = '0' * 64
        elif fault in ('projection', 'workspace', 'comparison'):
            block = next(b for b in dom['jsonblocks'] if b['label'].startswith(fault))
            block['text'] = '{"WRONG_FROM_A":true}'
        elif fault in ('empty_input', 'third_input'):
            next(i for i in dom['inputs'] if i['label'] == kind + '_id')['value'] = '' if fault == 'empty_input' else switch['a'][:-1] + ('0' if switch['a'][-1] != '0' else '1')
        elif fault == 'missing_release':
            del switch['released']
        elif fault == 'wrong_release_request':
            switch['released']['request_id'] = 'WRONG_HELD_REQUEST'
        elif fault == 'release_after_capture':
            switch['released']['released_at_unix'] = switch['after']['at_unix'] + 10
        return {'variant': 'CP12.default', 'mutation': name, 'after_dom': dom, 'released': switch.get('released')}
    if name == 'late_switch_wrong_dynamic_details':
        for switch in browser(raw, 'CP12.default')['switches']:
            d = switch['after']['dom']; d['jsonblocks'] = []; d['note'] = 'WRONG note'; d['comparison_status'] = 'WRONG comparison'
        return {'variant': 'CP12.default', 'mutation': name, 'switches': browser(raw, 'CP12.default')['switches']}
    if name in ('empty_actions_restored_claim', 'extra_unauthorized_action', 'duplicate_action', 'wrong_action_step', 'wrong_action_status', 'missing_action'):
        ident = 'CP01.default' if name == 'empty_actions_restored_claim' else 'CP02.default'
        dom = action_dom(raw, ident)
        if name in ('empty_actions_restored_claim', 'extra_unauthorized_action'):
            dom['next_actions'] = (dom['next_actions'] or '') + ' RESTORE_OLD_TRUTH 已执行成功'
        elif name == 'duplicate_action':
            dom['next_action_rows'].append(copy.deepcopy(dom['next_action_rows'][0]))
        elif name == 'missing_action':
            dom['next_action_rows'] = []
        else:
            dom['next_action_rows'][0]['fields'] = [' step_id = stp_WRONG_SUBJECT' if name == 'wrong_action_step' else ' status = EXECUTED_SUCCESSFULLY']
        return {'variant': ident, 'mutation': name, 'action_dom': dom}
    if name == 'fault_sql_original_hashes_wrong':
        i = record(raw, 'CP13.default')['injection']
        i.update(sql='SELECT 1', parameters=[], original_backup_sha256='0' * 64, fork_sha256_before_serving='0' * 64)
        i['original']['task_revision'] = 90000
        return {'variant': 'CP13.default', 'injection': i}
    if name == 'task_ahead_shadows_rule_ahead':
        i = record(raw, 'CP15.default')['injection']; p = json.loads(i['mutated']['projection_json'])
        p['task']['revision'] += 100; i['mutated']['task_revision'] = p['task']['revision']
        next(o for o in p['objects'] if o['object_type'] == 'task')['revision'] = p['task']['revision']
        i['mutated'].update(projection_json=gate.canonical(p), projection_digest=gate.sha(p), full_context_digest=gate.sha(p))
        return {'variant': 'CP15.default', 'injection': i}
    if name == 'missing_operation_database_references':
        for r in raw['records']:
            for op in gate.all_operations(r): op['database_receipts'] = ['missing-before.db', 'missing-after.db']
        return {'mutation': name, 'replacement': ['missing-before.db', 'missing-after.db']}
    if name == 'missing_required_scanner_source_keys':
        raw['core_source_hashes'] = {}
        for r in raw['records']:
            for op in gate.all_operations(r):
                op['observer_started'].pop('continuity_source_sha256', None); op['observer_started'].pop('scanner_source_sha256', None)
        return {'mutation': name}
    if name == 'missing_all_database_files':
        dbs = [p.relative_to(directory).as_posix() for p in directory.rglob('*.db')]
        assert len(dbs) == 154
        for path in dbs: (directory / path).unlink()
        return {'mutation': name, 'omitted_database_files': dbs, 'omitted_count': len(dbs)}
    if name == 'wrong_operation_file':
        record(raw, 'CP01.default')['operations'][0]['operation_receipt'] = 'base/MISSING-operation.json'
        return {'mutation': name}
    if name == 'observer_window_truncated':
        record(raw, 'CP10.default')['operations'][2]['observer'].pop()
        return {'mutation': name}
    if name == 'source_event_payload_changed':
        record(raw, 'CP01.default')['lineage'][0]['source_read']['body']['event']['payload'] = {'WRONG': 'not the source Event'}
        return {'mutation': name}
    if name == 'CONTROL_unknown_to_same':
        record(raw, 'CP06.default')['operations'][0]['http']['body']['resume']['comparison']['workspace'] = 'SAME'
        return {'mutation': name}
    raise ValueError(name)


def sync_artifacts(directory, raw, original_manifest):
    write(directory / 'browser.json', raw['browser'])
    temporary = directory / 'browser-requests.jsonl.private-write'
    temporary.write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in raw['browser']['network']))
    os.replace(temporary, directory / 'browser-requests.jsonl')
    for ui in raw['browser']['records']:
        observations = [o for p in ui['pages'] for o in p['observations']] + [s[k] for s in ui['switches'] for k in ('before', 'after')]
        for o in observations: write(directory / o['dom_file'], o['dom'])
    for row in raw['records']:
        for op in gate.all_operations(row):
            if 'MISSING' not in op['operation_receipt']: write(directory / op['operation_receipt'], op)
        if row.get('injection'): write(directory / row['id'] / 'injection.json', row['injection'])
    fields = ('candidate_commit', 'source_tree', 'source_archive_sha256', 'core_source_hashes', 'launcher_source_hashes', 'matrix_sha256')
    write(directory / 'source-bindings.json', {k: raw[k] for k in fields})
    index = load(directory / 'receipt-index.json')
    index['files'] = [{'path': e['path'], 'bytes': (directory / e['path']).stat().st_size, 'sha256': digest(directory / e['path'])} for e in index['files'] if (directory / e['path']).exists()]
    write(directory / 'receipt-index.json', index)
    raw['receipt_index_sha256'] = digest(directory / 'receipt-index.json')
    write(directory / 'raw-result.json', raw)
    manifest = copy.deepcopy(original_manifest)
    manifest['files'] = [{'path': e['path'], 'bytes': (directory / e['path']).stat().st_size, 'sha256': digest(directory / e['path'])} for e in manifest['files'] if (directory / e['path']).exists()]
    write(directory / 'manifest.json', manifest)


def run(bundle, repository, out):
    os.umask(0o077); out.mkdir(parents=True, exist_ok=False)
    raw = load(bundle / 'raw-result.json'); matrix = load(bundle / 'matrix.json'); manifest = load(bundle / 'manifest.json')
    candidate = raw['candidate_commit']; before = digest(bundle / 'manifest.json')
    assert gate.verify_directory(bundle, candidate, repository)['status'] == 'PASS'
    names = ['late_switch_wrong_dynamic_details', 'empty_actions_restored_claim', 'extra_unauthorized_action',
        'fault_sql_original_hashes_wrong', 'task_ahead_shadows_rule_ahead', 'missing_operation_database_references',
        'missing_required_scanner_source_keys', 'missing_all_database_files']
    for kind in ('checkpoint', 'resume'):
        names += ['late_' + kind + '_' + f for f in ('note', 'digest', 'projection', 'workspace', 'empty_input', 'third_input', 'missing_release', 'wrong_release_request', 'release_after_capture')]
    names += ['late_resume_comparison', 'duplicate_action', 'wrong_action_step', 'wrong_action_status', 'missing_action',
        'wrong_operation_file', 'observer_window_truncated', 'source_event_payload_changed', 'CONTROL_unknown_to_same']
    new_names = ['browser_expected_note_disagrees_with_DB', 'old_success_data_on_404', 'incorrect_created_at', 'wrong_event_navigation',
        'browser_resume_note_disagrees_with_DB', 'browser_wire_note_disagrees_with_DB', 'browser_latest_wrong_selection', 'browser_empty_conceals_DB_checkpoint',
        'stale_success_resume_404', 'stale_success_checkpoint_invalid', 'stale_success_resume_invalid', 'stale_success_signin',
        'stale_success_scope', 'stale_success_pending', 'stale_success_empty', 'incorrect_resume_created_at',
        'wrong_command_navigation', 'wrong_resume_event_navigation', 'wrong_resume_command_navigation', 'wrong_project_navigation',
        'wrong_task_navigation', 'wrong_resume_checkpoint_navigation', 'wrong_trigger_level', 'wrong_resume_checkpoint_id']
    names += new_names
    checks = []
    for name in names:
        with tempfile.TemporaryDirectory(prefix='NEGATIVE-private-', dir=out) as temporary:
            directory = Path(temporary) / 'copy'
            shutil.copytree(bundle, directory, copy_function=os.link)
            changed = copy.deepcopy(raw)
            patch = mutate(changed, name, directory)
            sync_artifacts(directory, changed, manifest)
            value = gate.evaluate_bundle(changed, matrix, expected_candidate=candidate, expected_archive=raw['source_archive_sha256'], evidence_directory=directory)
            rejected = value['status'] != 'PASS'
            cli_rejected = None
            if name == 'missing_all_database_files' or name in new_names:
                selfchecks = gate.adversarial_bundle_checks(changed, matrix, candidate, raw['source_archive_sha256'], evidence_directory=directory)
                value['harness_selfchecks'] = selfchecks['status']
                write(directory / 'result.json', value)
                write(directory / 'harness-selfchecks.json', selfchecks)
                sync_artifacts(directory, changed, manifest)
                try: cli_rejected = gate.verify_directory(directory, candidate, repository)['status'] != 'PASS'
                except Exception as e: cli_rejected = True; patch['cli_rejection'] = type(e).__name__ + ': ' + str(e)
                rejected = rejected and cli_rejected
            if name.startswith('browser_'):
                assert value['physical_evidence_binding']['status'] == 'BLOCKED' and 'actual historical DB GET' in value['physical_evidence_binding'].get('reason', ''), 'DB-to-browser readback gate did not reject'
            elif name in new_names:
                assert value['physical_evidence_binding']['status'] == 'PASS', 'unrelated physical binding failed for DOM component'
                ident = patch['variant']
                assert not next(r for r in value['records'] if r['id'] == ident)['invariants']['G7']['passed'], 'state/metadata G7 did not reject'
            if name.startswith('late_'):
                assert not next(r for r in value['records'] if r['id'] == 'CP12.default').get('invariants', {}).get('G7', {}).get('passed', False), 'late dynamic check did not reject'
            if name in ('empty_actions_restored_claim', 'extra_unauthorized_action', 'duplicate_action', 'wrong_action_step', 'wrong_action_status', 'missing_action'):
                ident = 'CP01.default' if name == 'empty_actions_restored_claim' else 'CP02.default'
                assert not next(r for r in value['records'] if r['id'] == ident)['invariants']['G7']['passed'], 'action check did not reject'
            check = {'name': name, 'kind': 'REBOUND_NEGATIVE_COMPONENT_COPY_NOT_ACCEPTANCE', 'observed_verdict': value['status'],
                'rejected': rejected, 'physical_evidence_binding': value['physical_evidence_binding']['status'], 'physical_reason': value['physical_evidence_binding'].get('reason'),
                'cli_rejected': cli_rejected, 'counts': value['counts']}
            write(out / (name + '.json'), {'check': check, 'input_patch': patch})
            checks.append(check)
            print(json.dumps({'counterexample': name, 'rejected': rejected}), flush=True)
    # Re-read every original bound byte; not merely the unchanged manifest header.
    for file in manifest['files']:
        assert digest(bundle / file['path']) == file['sha256']
    assert digest(bundle / 'manifest.json') == before
    result = {'status': 'PASS' if all(c['rejected'] for c in checks) else 'FAIL', 'candidate_commit': candidate,
        'source_archive_sha256': raw['source_archive_sha256'], 'positive_manifest_sha256': before,
        'kind': 'AUTHOR_GATE_REGRESSION_COMPONENTS_NOT_MATRIX_VARIANTS', 'original_bundle_unchanged': True,
        'count': len(checks), 'legacy_review_bad_inputs_rejected': sum(c['rejected'] for c in checks[:8]), 'checks': checks,
        'r01_r03_original_counterexamples_rejected': sum(c['rejected'] for c in checks[35:39]),
        'scope_acceptance_signed': False, 'verifier_sha256': digest(Path(__file__))}
    write(out / 'result.json', result)
    files = [{'path': p.name, 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in sorted(out.iterdir()) if p.is_file() and p.name != 'manifest.json']
    write(out / 'manifest.json', {'candidate_commit': candidate, 'kind': result['kind'], 'files': files})
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[2]); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); raise SystemExit(run(a.bundle.resolve(), a.repository, a.out.resolve()))
