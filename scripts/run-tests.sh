#!/usr/bin/env bash
# Run the P0 test suite with the standard library only (ADR 0001 §2.1).
# Usage: scripts/run-tests.sh [unittest args...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

"$PY" -c 'import sys
if sys.version_info < (3, 11):
    sys.stderr.write("Jasmine Core tests require Python >=3.11; selected Python is %s. Set PYTHON to a supported interpreter.\n" % sys.version.split()[0])
    sys.exit(2)
'

cd "$ROOT"
PYTHONPATH="$ROOT/src:$ROOT/tests" "$PY" -m unittest discover -s tests -t tests -v "$@"
