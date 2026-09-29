#!/usr/bin/env bash
# Installed only as a project-local Codex hook. The binding file contains
# private token-file paths; token contents never enter argv or hooks.json.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="${JASMINE_P1_PYTHON:-/opt/homebrew/bin/python3.13}"
if [ "${1:-}" = "--python" ]; then
  [ "$#" -ge 2 ] || exit 2
  PYTHON="$2"
  shift 2
fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m jasmine_core.capture.p1_codex_hook "$@"
