#!/usr/bin/env python3
"""Review/install fixture-local admission hooks; never modify Codex trust."""
import argparse
import difflib
import hashlib
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jasmine_core.capture.p1_codex_hook import _private_file

EVENTS = ('UserPromptSubmit', 'SessionStart', 'PreCompact', 'PostCompact', 'Stop', 'PreToolUse')
MARKERS = ('jasmine-p3-hook.sh',)

def owned(hook, wrapper, binding):
    command = hook.get('command')
    if hook.get('type') != 'command' or not isinstance(command, str):
        return False
    try: tokens = shlex.split(command)
    except ValueError: return False
    if not tokens or tokens[0] != str(wrapper):
        return False
    if len(tokens) != 5 or tokens[1] != '--python' or tokens[3] != '--binding' or tokens[4] != str(binding):
        raise ValueError('ambiguous/other binding command for this wrapper; explicit operator migration required')
    return True


def plan(project, binding, python):
    project = project.resolve(strict=True)
    target = project / '.codex' / 'hooks.json'
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('regular project-local hooks path required')
    config = json.loads(_private_file(binding))
    if config.get('mode') != 2 or 'p3_adapter_config_file' not in config:
        raise ValueError('P3 binding required')
    for field in ('token_file', 'human_token_file'):
        _private_file(Path(config[field]))
    old = target.read_bytes() if target.exists() else b''
    document = json.loads(old) if old else {'description': 'Jasmine P3 admission-only project hooks', 'hooks': {}}
    if not isinstance(document, dict) or not isinstance(document.get('hooks'), dict):
        raise ValueError('invalid hooks document')
    wrapper = ROOT / 'scripts' / 'jasmine-p3-hook.sh'
    python=python.absolute()
    if python.parent!=python.parent.resolve(strict=True) or not python.exists():raise ValueError('canonical venv launcher parent required')
    # Preserve the venv launcher symlink: resolving it discards its site-packages.
    command = ' '.join(shlex.quote(str(p)) for p in (wrapper, '--python', python, '--binding', binding))
    for event in EVENTS:
        kept = []
        for entry in document['hooks'].get(event, []):
            if not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list):
                raise ValueError('invalid hook entry')
            remaining = [h for h in entry['hooks'] if not owned(h, wrapper, binding)]
            if remaining:
                kept.append({**entry, 'hooks': remaining})
        kept.append({**({'matcher': '.*'} if event == 'PreToolUse' else {}),
            'hooks': [{'type': 'command', 'command': command,
                'timeout': 150 if event == 'UserPromptSubmit' else 95,
                **({'additionalContextLimit': 0} if event == 'UserPromptSubmit' else {}),
                'statusMessage': 'Jasmine P3 semantic admission ' + event}]})
        document['hooks'][event] = kept
    new = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode()
    return target, old, new, wrapper


def install(target, old, new):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if old == new:
        return
    if old:
        backup = target.with_name('hooks.json.backup-' + hashlib.sha256(old).hexdigest())
        if not backup.exists():
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(old)
    fd, temporary = tempfile.mkstemp(prefix='.hooks-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(new); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path('/opt/homebrew/opt/python@3.13/bin/python3.13'))
    parser.add_argument('--write', action='store_true', help='install exact diff; default is review only')
    args = parser.parse_args()
    target, old, new, wrapper = plan(args.project_root, args.binding.resolve(strict=True), args.python)
    if args.write: install(target, old, new)
    print(json.dumps({'result': 'written' if args.write else 'would-write', 'target': str(target),
        'old_sha256': hashlib.sha256(old).hexdigest(), 'new_sha256': hashlib.sha256(new).hexdigest(),
        'wrapper_sha256': hashlib.sha256(wrapper.read_bytes()).hexdigest(), 'trust_changed': False,
        'diff': ''.join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True), fromfile='old/hooks.json', tofile='new/hooks.json'))}, ensure_ascii=False))

if __name__ == '__main__': main()
