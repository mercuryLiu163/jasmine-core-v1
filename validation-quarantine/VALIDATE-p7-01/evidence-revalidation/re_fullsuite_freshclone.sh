#!/usr/bin/env bash
# Re-validation: full suite from a FRESH CLONE at the candidate SHA.
# Writes raw output next to this script (allowed quarantine path).
set -uo pipefail

SHA="a252097bbc46763e7920018649ccac5dad5c06fe"
SRC="/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
DST="/tmp/p7-01-reval-clone"
OUT="/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/VALIDATE-p7-01/evidence-revalidation"
REAL="/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs"

rm -rf "$DST"
echo "=== git clone --no-hardlinks $SRC -> $DST"
git clone --quiet --no-hardlinks "$SRC" "$DST" 2>&1
cd "$DST"
git checkout --quiet "$SHA" 2>&1
echo "=== HEAD: $(git rev-parse HEAD)"
echo "=== status --porcelain:"; git status --porcelain

echo "=== cloning also a baseline worktree at 0f7b2ec (pre phase-2 implementation)"
if git cat-file -e 0f7b2ec^{commit} 2>/dev/null; then
  git worktree add --quiet --detach /tmp/p7-01-baseline 0f7b2ec 2>&1
  echo "baseline HEAD: $(git -C /tmp/p7-01-baseline rev-parse HEAD)"
fi

echo
echo "=== candidate module test: tests/test_validation_read.py"
P7_REAL_RUNS_ROOT="$REAL" PYTHONPATH="$DST/src:$DST/tests" \
  python3.13 -m unittest -v test_validation_read 2>&1 | tee "$OUT/fullsuite-candidate-module.txt"
echo "module exit: ${PIPESTATUS[0]}"

echo
echo "=== candidate FULL suite"
/usr/bin/time -p env PYTHON=python3.13 P7_REAL_RUNS_ROOT="$REAL" \
  ./scripts/run-tests.sh 2>&1 | tee "$OUT/fullsuite-candidate.txt"
echo "full suite exit: ${PIPESTATUS[0]}"

echo
echo "=== baseline (0f7b2ec) FULL suite"
cd /tmp/p7-01-baseline
/usr/bin/time -p env PYTHON=python3.13 P7_REAL_RUNS_ROOT="$REAL" \
  ./scripts/run-tests.sh 2>&1 | tee "$OUT/fullsuite-baseline.txt"
echo "baseline full suite exit: ${PIPESTATUS[0]}"
echo "=== DONE"
