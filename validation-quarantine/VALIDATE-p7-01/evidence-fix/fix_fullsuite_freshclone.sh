#!/usr/bin/env bash
# FIX-REVALIDATION: implementation module + FULL suite from a FRESH CLONE at
# the candidate SHA, plus a baseline worktree at 0f7b2ec for comparison.
set -uo pipefail

SHA="f0aea886186a6c1e4704eceb2552c74845f333b2"
SRC="/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
DST="/tmp/p7-01-fix-clone"
BASE="/tmp/p7-01-fix-baseline"
OUT="/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/VALIDATE-p7-01/evidence-fix"
REAL="/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs"

rm -rf "$DST" "$BASE"
echo "=== git clone --no-hardlinks $SRC -> $DST"
git clone --quiet --no-hardlinks "$SRC" "$DST" 2>&1
cd "$DST"
git checkout --quiet "$SHA" 2>&1
echo "=== HEAD: $(git rev-parse HEAD)"
echo "=== status --porcelain (must be empty):"; git status --porcelain

echo
echo "=== candidate module test: tests/test_validation_read.py"
P7_REAL_RUNS_ROOT="$REAL" PYTHONPATH="$DST/src:$DST/tests" \
  python3.13 -m unittest -v test_validation_read 2>&1 \
  | tee "$OUT/fullsuite-candidate-module.txt"
echo "module exit: ${PIPESTATUS[0]}"

echo
echo "=== candidate FULL suite"
/usr/bin/time -p env PYTHON=python3.13 P7_REAL_RUNS_ROOT="$REAL" \
  ./scripts/run-tests.sh 2>&1 | tee "$OUT/fullsuite-candidate.txt"
echo "full suite exit: ${PIPESTATUS[0]}"

echo
echo "=== baseline (0f7b2ec, parent of the phase-2 implementation) FULL suite"
git worktree add --quiet --detach "$BASE" 0f7b2ec 2>&1
echo "baseline HEAD: $(git -C "$BASE" rev-parse HEAD)"
cd "$BASE"
/usr/bin/time -p env PYTHON=python3.13 P7_REAL_RUNS_ROOT="$REAL" \
  ./scripts/run-tests.sh 2>&1 | tee "$OUT/fullsuite-baseline.txt"
echo "baseline full suite exit: ${PIPESTATUS[0]}"
echo "=== DONE"
