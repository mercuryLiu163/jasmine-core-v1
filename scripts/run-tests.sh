#!/usr/bin/env bash
# Run the P0 test suite with the standard library only (ADR 0001 §2.1).
# Usage: scripts/run-tests.sh [unittest args...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

cd "$ROOT"
PYTHONPATH="$ROOT/src:$ROOT/tests" "$PY" -m unittest discover -s tests -t tests -v "$@"
