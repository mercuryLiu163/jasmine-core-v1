#!/usr/bin/env bash
# P6-03b2 Gate: build the commit in isolation, run the write walkthrough twice with the write
# switch ON and once with it OFF, and freeze the whole bundle.
#
# Why a script rather than a sequence typed into a shell: the P6-03b1 bundle recorded "ran from an
# isolated git archive of the commit, its own fixture and proxy", and reproducing that by hand is
# where a Gate quietly stops testing the commit — a stale `dist/`, a proxy started from the
# worktree, a fixture left over from the previous run. Everything here is derived from
# `git rev-parse HEAD` and a fresh `git archive`, so the evidence cannot come from anything else.
#
# It never touches the operator's Core, database or config: the "Core" is the simulated P4 write
# fixture, on its own loopback port, with randomly generated throwaway tokens that are written
# only into the temporary copy and never into the bundle.
#
# The fixture is restarted between the two ON runs, because this Gate *mutates* the fixture (pin,
# obsolete, delete). Two runs against one process would see different starting states and could not
# be compared step for step; that is why b1, which only read, did not need this.
#
#   scripts/p6/run_p4_write_gate.sh [--keep]
#
# Exits non-zero if any record fails, if the two ON runs disagree, or if the OFF run does not show
# the default-closed refusal.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.13}"
COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/validation-runs/p6/${STAMP}-p6-03b2-clickthrough"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/p6-03b2-gate.XXXXXX")"
FIXTURE_PORT="${FIXTURE_PORT:-8918}"
UI_PORT="${UI_PORT:-5181}"
UI_OFF_PORT="${UI_OFF_PORT:-5182}"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

# The agent token deliberately carries `memory:manage` too. P4's API layer checks the *scope*
# before the handler checks the *actor kind*, so an agent key without the scope would be refused
# with `forbidden_scope` and the actor-kind rule would never be observable. Real P4 would not
# issue an agent a manage scope; the fixture's actor kind is a scope stand-in, and the bundle
# README says so.
HUMAN="p6b2-human-$(openssl rand -hex 12)"
AGENT="p6b2-agent-$(openssl rand -hex 12)"
READONLY="p6b2-readonly-$(openssl rand -hex 12)"

FIXTURE_PID=""
UI_PID=""
UI_OFF_PID=""
cleanup() {
  for pid in "$UI_PID" "$UI_OFF_PID" "$FIXTURE_PID"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  if [ "$KEEP" = "1" ]; then
    echo "kept the isolated tree at $WORK" >&2
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

echo "== P6-03b2 Gate ==" >&2
echo "commit  $COMMIT" >&2
echo "bundle  $OUT" >&2
echo "work    $WORK" >&2

# --- 1. an isolated copy of exactly this commit ------------------------------------------------
mkdir -p "$OUT"
git -C "$ROOT" archive "$COMMIT" | tar -x -C "$WORK"
# The archive has no dependencies and the build must not reach the network, so the worktree's
# installed modules are borrowed read-only.
ln -s "$ROOT/web/node_modules" "$WORK/web/node_modules"

# Throwaway tokens, written to the temporary copy only (mode 600), never into the bundle.
"$PYTHON" - "$WORK/tokens.json" "$HUMAN" "$AGENT" "$READONLY" <<'PY'
import json, os, sys
path, human, agent, readonly = sys.argv[1:5]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"human": human, "agent": agent, "readonly": readonly}, handle)
os.chmod(path, 0o600)
PY

# --- 2. build the dashboard from that copy -----------------------------------------------------
( cd "$WORK/web" && npx vite build > "$OUT/vite-build.log" 2>&1 )
ASSET="$(cd "$WORK/web/dist" && ls assets/index-*.js | head -1 | xargs basename)"
ASSET_SHA="$(shasum -a 256 "$WORK/web/dist/assets/$ASSET" | awk '{print $1}')"
TREE_SHA="$(cd "$WORK/web/dist" && find . -type f | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | awk '{print $1}')"
echo "asset   $ASSET / $ASSET_SHA" >&2
echo "tree    $TREE_SHA" >&2

wait_for() {
  for _ in $(seq 1 150); do
    if curl -fsS -o /dev/null "$1"; then return 0; fi
    sleep 0.2
  done
  echo "timed out waiting for $1" >&2
  return 1
}

start_fixture() { # log-suffix
  "$PYTHON" "$WORK/scripts/p6/p4_write_fixture.py" \
    --port "$FIXTURE_PORT" \
    --token "$HUMAN" --scopes "objects:read,memory:read,memory:manage,memory:reflect" \
    --token "$AGENT" --scopes "objects:read,memory:read,memory:manage,memory:reflect,actor:agent" \
    --token "$READONLY" --scopes "objects:read,memory:read" \
    >> "$OUT/fixture.stdout.txt" 2>> "$OUT/fixture.stderr.txt" &
  FIXTURE_PID=$!
  wait_for "http://127.0.0.1:${FIXTURE_PORT}/v1/health"
}
stop_fixture() {
  kill "$FIXTURE_PID" 2>/dev/null || true
  wait "$FIXTURE_PID" 2>/dev/null || true
  FIXTURE_PID=""
}

# --- 3. the simulated P4 write fixture, and the two dashboard hosts in front of it --------------
start_fixture

# Writes ON: both switches, so the Gate exercises the write surface for real.
P6_CORE_ORIGIN="http://127.0.0.1:${FIXTURE_PORT}" P6_ENABLE_P4_READS=1 P6_ENABLE_P4_WRITES=1 \
  node "$WORK/web/server/serve.mjs" --root "$WORK/web/dist" --port "$UI_PORT" \
  > "$OUT/serve.stdout.txt" 2> "$OUT/serve.stderr.txt" &
UI_PID=$!
# Writes OFF, reads on: the default-closed evidence, from a host that differs only in that one var.
P6_CORE_ORIGIN="http://127.0.0.1:${FIXTURE_PORT}" P6_ENABLE_P4_READS=1 \
  node "$WORK/web/server/serve.mjs" --root "$WORK/web/dist" --port "$UI_OFF_PORT" \
  > "$OUT/serve-off.stdout.txt" 2> "$OUT/serve-off.stderr.txt" &
UI_OFF_PID=$!
wait_for "http://127.0.0.1:${UI_PORT}/v1/health"
wait_for "http://127.0.0.1:${UI_OFF_PORT}/v1/health"

# --- 4. the OFF run: default-closed ------------------------------------------------------------
mkdir -p "$OUT/writes-off"
node "$WORK/scripts/p6/browser_trace.mjs" \
  --ui "http://127.0.0.1:${UI_OFF_PORT}" \
  --fixture "http://127.0.0.1:${FIXTURE_PORT}" \
  --out "$OUT/writes-off" \
  --token-file "$WORK/tokens.json" --token-key human \
  --p4-writes --p4-writes-off \
  > "$OUT/writes-off/trace.stdout.txt" 2>&1 || true

# --- 5. the ON run, twice, restarting the fixture in between so both start from the same state --
for run in 1 2; do
  dir="$OUT"; [ "$run" = "2" ] && dir="$OUT/stability"
  mkdir -p "$dir"
  if [ "$run" = "2" ]; then
    stop_fixture
    start_fixture
  fi
  node "$WORK/scripts/p6/browser_trace.mjs" \
    --ui "http://127.0.0.1:${UI_PORT}" \
    --fixture "http://127.0.0.1:${FIXTURE_PORT}" \
    --out "$dir" \
    --token-file "$WORK/tokens.json" --token-key human \
    --p4-writes \
    --p4-readonly-token "$READONLY" \
    --p4-agent-token "$AGENT" \
    --p4-write-contract "$WORK/web/contracts/p4-memory-write.v1.json" \
    >> "$dir/trace.stdout.txt" 2>&1 || echo "run $run reported a failure; see $dir/trace.stdout.txt" >&2
done

# --- 6. the two ON runs must describe the same steps with the same outcomes ---------------------
"$PYTHON" - "$OUT/click-trace.jsonl" "$OUT/stability/click-trace.jsonl" > "$OUT/stability-diff.txt" <<'PY'
import json, sys
def rows(path):
    return [(e["step"], e["ok"]) for e in (json.loads(line) for line in open(path))]
first, second = rows(sys.argv[1]), rows(sys.argv[2])
if first == second:
    print(f"identical: {len(first)} records")
    raise SystemExit(0)
print("DIFFERENT")
for index, (left, right) in enumerate(zip(first, second)):
    if left != right:
        print(f"  #{index + 1}: {left} vs {right}")
if len(first) != len(second):
    print(f"  length {len(first)} vs {len(second)}")
raise SystemExit(1)
PY
cat "$OUT/stability-diff.txt" >&2

# --- 7. every record in both bundles must have passed ------------------------------------------
"$PYTHON" - "$OUT/click-trace.jsonl" "$OUT/writes-off/click-trace.jsonl" <<'PY'
import json, sys
failures = []
for path in sys.argv[1:]:
    entries = [json.loads(line) for line in open(path)]
    bad = [entry["step"] for entry in entries if not entry["ok"]]
    print(f"{path}: {len(entries) - len(bad)}/{len(entries)} ok")
    failures.extend(f"{path}: {step}" for step in bad)
if failures:
    print("FAILED records:")
    for failure in failures:
        print(" -", failure)
    raise SystemExit(1)
PY

# --- 8. freeze ---------------------------------------------------------------------------------
rm -f "$OUT/BUNDLE-FROZEN.json" "$OUT/stability/BUNDLE-FROZEN.json"
"$PYTHON" "$WORK/scripts/p6/freeze_bundle.py" \
  --freeze "$OUT" \
  --commit "$COMMIT" \
  --kind "browser click-through gate (P6-03b2 memory writes, SIMULATED fixture)" \
  --runs "ON twice with identical step sequences (fixture restarted between runs); OFF once for the default-closed evidence" \
  --asset "$ASSET" --asset-sha256 "$ASSET_SHA" --tree-sha256 "$TREE_SHA" \
  --fixture-value "simulated-p4-writes" \
  --supersedes "none — the first P6-03b2 bundle" \
  --dependency-json '{"name":"P4 memory write slice","status":"NOT MERGED","source_ref":"0c4f8f0e2118711ac9aa2a7006fddc6982962248","acceptance":"P4 phase acceptance NOT passed","consequence":"the Core behind this proxy was a simulated fixture, not P4 code"}'
"$PYTHON" "$WORK/scripts/p6/freeze_bundle.py" --verify "$OUT"
echo "bundle: $OUT" >&2
