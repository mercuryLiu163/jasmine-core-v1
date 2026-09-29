#!/usr/bin/env bash
# Install the P0 UserPromptSubmit capture hook in this checkout only.
# Trust remains an operator action in Codex; this script never edits config.toml.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${JASMINE_CORE_STATE_DIR:-$HOME/.local/share/jasmine-core}"
DB="${JASMINE_CORE_DB:-}"
PYTHON="${JASMINE_PYTHON:-}"
HOOKS_JSON="$ROOT/.codex/hooks.json"
DRY_RUN=0
FORCE=0

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: p0-codex-hook-install.sh [--dry-run] [--force] [--hooks-json PATH]
       [--python PATH] [--db PATH] [--state-dir DIR]
  --dry-run          validate and report; write nothing
  --force            replace a changed Jasmine capture entry
  --hooks-json PATH  alternate file inside this checkout's .codex directory
  --global           unsupported: P0 capture is project-scoped
EOF
}
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --global) fail "--global is unsupported: P0 capture must be project-local" ;;
    --hooks-json|--python|--db|--state-dir)
      [ $# -ge 2 ] || fail "$1 requires a value"
      case "$1" in
        --hooks-json) HOOKS_JSON="$2" ;;
        --python) PYTHON="$2" ;;
        --db) DB="$2" ;;
        --state-dir) STATE_DIR="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
DB="${DB:-$STATE_DIR/core.db}"
export PYTHONDONTWRITEBYTECODE=1

# A custom destination may be useful for local validation, but cannot expand
# the hook's scope beyond this checkout. Do not follow target symlinks.
[ "$HOOKS_JSON" != "$HOME/.codex/hooks.json" ] || fail "global hooks.json is outside P0 project scope"
[ ! -L "$ROOT/.codex" ] || fail "refusing a symlinked project .codex directory: $ROOT/.codex"
[ ! -L "$HOOKS_JSON" ] || fail "refusing a symlink target: $HOOKS_JSON"
[ -x "$ROOT/scripts/jasmine-capture-hook.sh" ] || fail "capture wrapper is missing or not executable"

if [ -z "$PYTHON" ]; then
  for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                   /opt/homebrew/bin/python3.11 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
      PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi
[ -n "$PYTHON" ] || fail "no Python 3.11+ interpreter found; pass --python PATH"
PYTHON="$("$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11); print(sys.executable)' 2>/dev/null)" \
  || fail "--python must be a working Python 3.11+ interpreter"
PYTHONPATH="$ROOT/src" "$PYTHON" -c 'import jasmine_core.capture.codex_user_prompt_submit as m; assert m.EVENT_TYPE' \
  || fail "capture module does not import from $ROOT/src"
[ -f "$DB" ] || fail "no core.db at $DB; run jasmine-core bootstrap first"
[ -s "$STATE_DIR/capture-token" ] && [ -r "$STATE_DIR/capture-token" ] \
  || fail "no capture token at $STATE_DIR/capture-token; run jasmine-core bootstrap first"

# All database inspection is read-only. In particular, dry run must never call
# the schema CLI, whose normal connection may create journal sidecars.
CHECK="$(PYTHONPATH="$ROOT/src" "$PYTHON" - "$DB" "$HOOKS_JSON" "$ROOT/.codex" <<'PY'
import json, sqlite3, sys
from pathlib import Path
from jasmine_core import SCHEMA_VERSION
from jasmine_core.migrations import check_version, verify_checksums

path, target, allowed = map(Path, sys.argv[1:])
if allowed.is_symlink() or allowed.resolve() != allowed.parent.resolve() / allowed.name:
    raise SystemExit('project .codex directory must resolve inside this checkout')
wal = Path(str(path) + '-wal')
if wal.exists() and wal.stat().st_size:
    raise SystemExit('database has an active WAL; stop Core and checkpoint it before installing')
if target.parent.resolve() != allowed.resolve() or target.name in ('', '.', '..'):
    raise SystemExit('hooks target must be in this checkout .codex directory')
if target.exists() and not target.is_file():
    raise SystemExit('hooks target must be a regular file')
con = sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro&immutable=1', uri=True)
con.row_factory = sqlite3.Row
try:
    version = check_version(con)
    if version != SCHEMA_VERSION or verify_checksums(con):
        raise SystemExit('database schema does not match this checkout')
    row = con.execute('SELECT host_id FROM hosts ORDER BY first_seen_at LIMIT 1').fetchone()
    if not row:
        raise SystemExit('no registered host in database')
    print(json.dumps({'schema_version': version, 'host_id': row[0]}))
finally:
    con.close()
PY
)" || fail "cannot validate database or hooks target"
HOST_ID="$(printf '%s' "$CHECK" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["host_id"])')"
printf 'interpreter: %s\ndatabase: %s\nhost_id: %s\ntoken file: %s\n' \
  "$PYTHON" "$DB" "$HOST_ID" "$STATE_DIR/capture-token"

# The command contains only paths and public host ID. The token is read by the
# wrapper at execution time and never enters argv, hooks.json, or installer logs.
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
COMMAND="$(quote "$ROOT/scripts/jasmine-capture-hook.sh") --python $(quote "$PYTHON") --host $(quote "$HOST_ID") --db $(quote "$DB") --state-dir $(quote "$STATE_DIR")"
printf 'target: %s\ncommand: %s\n' "$HOOKS_JSON" "$COMMAND"

RESULT="$("$PYTHON" - "$HOOKS_JSON" "$COMMAND" "$FORCE" "$DRY_RUN" <<'PY'
import json, os, shutil, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

path, command = Path(sys.argv[1]), sys.argv[2]
force, dry = sys.argv[3] == '1', sys.argv[4] == '1'
if path.parent.is_symlink():
    raise SystemExit('refusing a symlinked hooks directory')
entry = {'hooks': [{'type': 'command', 'command': command, 'timeout': 8,
                    'additionalContextLimit': 1500}]}
old = None
if path.exists():
    old = path.read_bytes()
    try:
        config = json.loads(old)
    except (UnicodeError, ValueError) as exc:
        raise SystemExit(f'refusing invalid hooks JSON: {exc}')
    if not isinstance(config, dict):
        raise SystemExit('refusing hooks JSON that is not an object')
else:
    config = {'description': 'Jasmine Core capture entry (project-local)', 'hooks': {}}
hooks = config.setdefault('hooks', {})
if not isinstance(hooks, dict):
    raise SystemExit('refusing hooks field that is not an object')
existing = hooks.get('UserPromptSubmit', [])
if not isinstance(existing, list):
    raise SystemExit('refusing UserPromptSubmit field that is not an array')

def owned_hook(hook):
    return isinstance(hook, dict) and 'jasmine-capture-hook.sh' in str(hook.get('command', ''))

def owned(item):
    return (isinstance(item, dict) and isinstance(item.get('hooks'), list)
            and any(owned_hook(hook) for hook in item['hooks']))

mine = [item for item in existing if owned(item)]
if mine and not force:
    if len(mine) == 1 and mine[0] == entry:
        print('unchanged'); raise SystemExit
    raise SystemExit('capture entry changed or duplicated; rerun with --force')
kept = []
for item in existing:
    if not owned(item):
        kept.append(item)
        continue
    # A shared event entry can contain other commands. Preserve those exactly.
    remaining = [hook for hook in item['hooks'] if not owned_hook(hook)]
    if remaining:
        kept.append({**item, 'hooks': remaining})
hooks['UserPromptSubmit'] = kept + [entry]
config.setdefault('description', 'Jasmine Core capture entry (project-local)')
new = (json.dumps(config, indent=2, ensure_ascii=False) + '\n').encode()
if old == new:
    print('unchanged'); raise SystemExit
if dry:
    print('would-write'); raise SystemExit
path.parent.mkdir(parents=True, exist_ok=True)
if old is not None:
    # Exclusive name prevents a repeated install from overwriting a backup.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = path.with_name(f'{path.name}.bak.{stamp}')
    with backup.open('xb') as handle:
        handle.write(old)
        handle.flush()
        os.fsync(handle.fileno())
    shutil.copymode(path, backup)
    print(f'backup: {backup}', file=sys.stderr)
fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
try:
    with os.fdopen(fd, 'wb') as handle:
        handle.write(new)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, path.stat().st_mode & 0o777 if old is not None else 0o600)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print('written')
PY
)" || fail "hook entry not changed"
printf 'result: %s\n' "$RESULT"
if [ "$DRY_RUN" = 1 ]; then
  printf 'Dry run: nothing was written.\n'
elif [ "$RESULT" = written ]; then
  cat <<EOF
Trust the new hook command in the Codex UI for this checkout. This installer
will NOT compute, guess or inject a trusted_hash in ~/.codex/config.toml.
Trusting the entry is a decision for the operator. Until trusted, P0-T07 is BLOCKED.
EOF
fi
