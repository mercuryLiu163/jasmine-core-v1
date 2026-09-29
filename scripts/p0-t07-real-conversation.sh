#!/usr/bin/env bash
# P0-T07 real conversation Gate. Exit 0 PASS, 1 FAIL, 2 BLOCKED.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${JASMINE_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                   /opt/homebrew/bin/python3.11 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      PYTHON="$(command -v "$candidate")"; break
    fi
  done
fi
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo 'BLOCKED: Python 3.11+ is required; set JASMINE_PYTHON' >&2
  exit 2
fi
exec "$PYTHON" "$ROOT/scripts/p0_t07_gate.py" "$@"
