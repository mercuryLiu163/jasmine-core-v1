#!/usr/bin/env bash
# The command Codex runs for a Jasmine Core UserPromptSubmit hook.
#
# It exists so that a hooks.json entry can contain nothing but paths: no token, no
# token or PYTHONPATH. The installer pins a validated interpreter path and
# this wrapper resolves the module from its own checkout at run time.
#
# stdin  : the Codex hook payload (JSON)
# stdout : the Codex context object; P0 injects none, so it is always {}
# exit   : always 0. A Core that is down must not cost the user their turn.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

HOST_ID=""
PYTHON=""
DB=""
STATE_DIR=""
TIMEOUT="${JASMINE_CORE_TIMEOUT:-5}"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST_ID="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

DB="${DB:-${JASMINE_CORE_DB:-$HOME/.local/share/jasmine-core/core.db}}"
STATE_DIR="${STATE_DIR:-${JASMINE_CORE_STATE_DIR:-$HOME/.local/share/jasmine-core}}"

# Interpreter: an explicit JASMINE_PYTHON, else the first 3.11+ on PATH. macOS
# ships 3.9 as /usr/bin/python3, below this project's floor, and a hook that
# cannot import the capture module is indistinguishable from a hook that
# captured nothing -- so an explicitly configured one is version-checked too.
usable_python() {
  [ -n "${1:-}" ] || return 1
  command -v "$1" >/dev/null 2>&1 || return 1
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

if [ -n "$PYTHON" ] && ! usable_python "$PYTHON"; then
  printf '{}\n'
  echo '{"captured": false, "reason": "configured --python is unavailable or below 3.11"}' >&2
  exit 0
fi
PYTHON="${PYTHON:-${JASMINE_PYTHON:-}}"
if ! usable_python "$PYTHON"; then
  PYTHON=""
  for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                   /opt/homebrew/bin/python3.11 /usr/local/bin/python3 python3; do
    if usable_python "$candidate"; then
      PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  printf '{}\n'
  echo '{"captured": false, "reason": "no Python 3.11+ interpreter available"}' >&2
  exit 0
fi

# Token: read at run time from the 0600 file, never embedded in a hooks file and
# never passed as an argument, so it cannot land in a shell history or a `ps`.
# The *decision* to refuse a missing token belongs to the capture module, not
# here: exiting early from the wrapper would skip the module's invocation trace,
# and the Gate relies on that trace to tell "Codex never ran the entry" from
# "the entry ran and could not capture".
TOKEN_FILE="$STATE_DIR/capture-token"
if [ -z "$TOKEN_FILE" ] || [ ! -r "$TOKEN_FILE" ]; then
  unset JASMINE_CORE_TOKEN
else
  JASMINE_CORE_TOKEN="$(cat "$TOKEN_FILE" 2>/dev/null)"
  export JASMINE_CORE_TOKEN
fi
unset TOKEN

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export JASMINE_CORE_DB="$DB"
export JASMINE_CORE_STATE_DIR="$STATE_DIR"
export JASMINE_CORE_URL="${JASMINE_CORE_URL:-http://127.0.0.1:8787}"
[ -n "$HOST_ID" ] && export JASMINE_CORE_HOST_ID="$HOST_ID"
export JASMINE_CORE_TIMEOUT="$TIMEOUT"

exec "$PYTHON" -m jasmine_core.capture.codex_user_prompt_submit
