"""Fixed historical fixture programs and independently rechecked file records."""
from __future__ import annotations

import difflib
import hashlib
import json
import shlex

INSPECTION_PROGRAM = """from pathlib import Path
import hashlib,json
for name in ['NOTES.md','calc.py','test_calc.py']:
 content=Path(name).read_text()
 print(json.dumps({'file':name,'content':content,'sha256':hashlib.sha256(content.encode()).hexdigest()}))
"""
REPAIR_PROGRAM = """from pathlib import Path
import hashlib,difflib,json
for name,old,new in [('calc.py','return sum(prices) - discount','return sum(prices) * (1 - discount / 100)'),('test_calc.py','27.5','28.5')]:
 p=Path(name); before=p.read_text()
 if old not in before: raise SystemExit('fixture precondition differs: '+name)
 after=before.replace(old,new); p.write_text(after)
 print(json.dumps({'file':name,'before':before,'after':after,'before_sha256':hashlib.sha256(before.encode()).hexdigest(),'after_sha256':hashlib.sha256(after.encode()).hexdigest(),'diff':''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=name,tofile=name))}))
"""


def verified_records(executions: list[tuple[str, dict, str]], statuses: list[str]) -> list[dict] | None:
    """Require the captured programs, outputs, same rerun and real file changes."""
    try:
        commands = [shlex.split(e[0]) for e in executions]
        if statuses != ['FAIL','PASS','PASS','PASS']:
            return None
        responses = [e[1] for e in executions]
        exits = [r.get('exit_code') for r in responses]
        if any(type(code) is not int for code in exits) or exits[0] == 0 or exits[1:] != [0,0,0]:
            return None
        python = commands[0][0]
        if commands[0] != [python,'-B','-m','unittest','-v'] or commands[3] != commands[0]:
            return None
        if commands[1] != [python,'-c',INSPECTION_PROGRAM] or commands[2] != [python,'-c',REPAIR_PROGRAM]:
            return None
        if 'AssertionError' not in responses[0]['stdout'] or 'OK' not in responses[3]['stdout']:
            return None
        inspected = [json.loads(line) for line in responses[1]['stdout'].splitlines()]
        repaired = [json.loads(line) for line in responses[2]['stdout'].splitlines()]
        if [r['file'] for r in inspected] != ['NOTES.md','calc.py','test_calc.py'] or [r['file'] for r in repaired] != ['calc.py','test_calc.py']:
            return None
        for record in inspected:
            if hashlib.sha256(record['content'].encode()).hexdigest() != record['sha256']:
                return None
        if 'percent' not in inspected[0]['content'].lower():
            return None
        for index, record in enumerate(repaired, 1):
            before, after = record['before'], record['after']
            old, new = [('return sum(prices) - discount','return sum(prices) * (1 - discount / 100)'),('27.5','28.5')][index-1]
            if before != inspected[index]['content'] or before == after or old not in before or after != before.replace(old,new):
                return None
            if hashlib.sha256(before.encode()).hexdigest() != record['before_sha256'] or hashlib.sha256(after.encode()).hexdigest() != record['after_sha256']:
                return None
            expected = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=record['file'], tofile=record['file']))
            if not expected or record['diff'] != expected:
                return None
        return repaired
    except (TypeError, ValueError, KeyError, IndexError):
        return None
