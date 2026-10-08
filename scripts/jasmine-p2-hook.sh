#!/usr/bin/env bash
# Host-side trusted hook; main tool sandbox cannot modify this readonly tree.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="/opt/homebrew/opt/python@3.13/bin/python3.13"
if [ "${1:-}" = "--python" ]; then
  [ "$#" -ge 2 ] || exit 2
  PYTHON="$2"
  shift 2
fi
export PYTHONPATH="$ROOT/src"
exec "$PYTHON" -m jasmine_core.capture.p2_codex_hook "$@"
