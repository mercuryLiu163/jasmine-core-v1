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
# Ports are chosen free at start-up unless the operator pins them. A fixed port is how the first
# OFF run ended up talking to a stale dashboard from an earlier session that happened to hold
# 5182 — it answered 401 for a token it had never been given, and the Gate blamed the product.
free_port() {
  "$PYTHON" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}
FIXTURE_PORT="${FIXTURE_PORT:-$(free_port)}"
UI_PORT="${UI_PORT:-$(free_port)}"
UI_OFF_PORT="${UI_OFF_PORT:-$(free_port)}"
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
# `/v1/health` is public and answers 200 for any token, so a *stale* dashboard on the same port
# would pass the wait. The host prints its own bundle root and Core origin; require that line,
# so the Gate cannot run against a server it did not start.
for pair in "${UI_PORT}:${UI_OFF_PORT}=writes on" "${UI_OFF_PORT}:${UI_PORT}=writes off"; do
  port="${pair%%:*}"
  log="$OUT/serve.stderr.txt"
  [ "$port" = "$UI_OFF_PORT" ] && log="$OUT/serve-off.stderr.txt"
  grep -q "p6 dashboard on http://127.0.0.1:${port}" "$log" || {
    echo "the host on ${port} is not the one this Gate started (see ${log})" >&2
    exit 1
  }
done

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

# --- 8. the bundle README, written from the run's own artifacts --------------------------------
# Written *before* the freeze so the manifest covers it: a README added afterwards would either
# invalidate the manifest or force an edit to a frozen file. Every number below is read from the
# run's own click-trace files, and the asset digests are the ones the freeze records.
"$PYTHON" - "$OUT" "$COMMIT" "$ASSET" "$ASSET_SHA" "$TREE_SHA" <<'READMEPY'
import json, sys
from pathlib import Path

out, commit, asset, asset_sha, tree_sha = sys.argv[1:6]
root = Path(out)


def rows(path):
    return [json.loads(line) for line in (root / path).read_text(encoding="utf-8").splitlines()]


on = rows("click-trace.jsonl")
on2 = rows("stability/click-trace.jsonl")
off = rows("writes-off/click-trace.jsonl")
same = [(e["step"], e["ok"]) for e in on] == [(e["step"], e["ok"]) for e in on2]

lines = [
    "# P6-03b2 browser click-through — Memory writes (retry / lifecycle / reflect)",
    "",
    f"Commit `{commit}` (see `BUNDLE-FROZEN.json`). Product asset `{asset}` / `{asset_sha}`.",
    "",
    f"Result: **{sum(1 for e in on if e['ok'])}/{len(on)}** with the write switch ON, run twice"
    f"{' with identical step sequences and outcomes' if same else ' AND THE TWO RUNS DIFFER'}; "
    f"**{sum(1 for e in off if e['ok'])}/{len(off)}** with the switch OFF. The fixture is restarted"
    " between the two ON runs, because this Gate mutates it.",
    "",
    "## What this proves, and what it does not",
    "",
    "P4 is **not merged**: baseline `850fbec` (schema 8) has no `src/jasmine_core/memory/`, and the",
    "contract is pinned to `0c4f8f0e2118711ac9aa2a7006fddc6982962248`, whose phase acceptance has",
    "**not** passed. The Core behind this proxy was the **simulated P4 write fixture**",
    "`scripts/p6/p4_write_fixture.py`, which stamps every reply `X-P6-Fixture: simulated-p4-writes`.",
    "",
    "* It proves: the six write routes send the frozen payloads, Core's own refusals are what the",
    "  pages show, a conflict is not auto-retried, a replay is rendered as a replay with no second",
    "  transition, each write is read back from the object's own row, and `reflect` is an explicit",
    "  request marked non-authoritative from Core's field.",
    "* It does **not** prove: that P4 works, that its acceptance has passed, or that any of this",
    "  would behave identically against a real P4 Core. Nothing here was served by P4 code.",
    "* It does **not** prove the fixture's values are P4's. The fixture's docstring lists exactly",
    "  what it cannot reproduce (no Truth store, engine or real jobs; the actor kind is a scope",
    "  stand-in; its replay identity is `(object_id, command)` while P4 hashes the command payload;",
    "  the nested command `event` shape is fixture-invented; `job` is always null on a lifecycle",
    "  reply).",
    "",
    "## The two invocations",
    "",
    "| Bundle | Proxy | Records |",
    "| --- | --- | --- |",
    f"| this directory | `P6_ENABLE_P4_READS=1 P6_ENABLE_P4_WRITES=1` | {sum(1 for e in on if e['ok'])}/{len(on)} |",
    f"| `stability/` | the same, after restarting the fixture | {sum(1 for e in on2 if e['ok'])}/{len(on2)} |",
    f"| `writes-off/` | `P6_ENABLE_P4_READS=1` only | {sum(1 for e in off if e['ok'])}/{len(off)} |",
    "",
    "## The ON records",
    "",
    "| # | Record |",
    "| --- | --- |",
]
for index, entry in enumerate(on, start=1):
    mark = "PASS" if entry["ok"] else "**FAIL**"
    lines.append(f"| {index} | {mark} {entry['step']} |")
lines += ["", "## The OFF record", "", "| # | Record |", "| --- | --- |"]
for index, entry in enumerate(off, start=1):
    mark = "PASS" if entry["ok"] else "**FAIL**"
    lines.append(f"| {index} | {mark} {entry['step']} |")
lines += [
    "",
    "## What the recorded traffic does and does not contain",
    "",
    "`browser-write-responses.jsonl` records, for each asserted write: the method and path, the",
    "status, the fixture label, **the request body** and **the response body**. That is what makes",
    "the payload and the withholding assertions checkable from the frozen evidence rather than only",
    "from the click-trace's prose — the P6-03b1 bundle could not evidence its redaction rule because",
    "it recorded no bodies.",
    "",
    "`browser-requests.jsonl` records Authorization only as `present(redacted)`/`absent`, and the",
    "fixture's startup banner masks the tokens it accepts. The launcher additionally **refuses to",
    "freeze a bundle that contains any token it generated** — a check, not a promise: an earlier",
    "bundle recorded the fixture's banner verbatim and carried all three cleartext tokens while",
    "this paragraph claimed otherwise. Tokens are generated per run, written to the isolated tree",
    "only (mode 600) and deleted with it.",
    "",
    "## The artificial keys, stated rather than glossed",
    "",
    "* The **agent** key carries `memory:manage` and `memory:reflect` as well as `actor:agent`. Real",
    "  P4 key issuance would not give an agent those scopes; the fixture's actor kind is a scope",
    "  stand-in, and without the scopes Core's scope check would fire first and the",
    "  `forbidden_actor_kind` refusal would be unobservable.",
    "* The **read-only** key carries `memory:read` only, which is what makes `forbidden_scope` the",
    "  answer on all six routes.",
    "",
    "## The fixture's control routes (not product surface)",
    "",
    "`/v1/_p6_fixture/state`, `/drift`, `/drop-next` and `/set-job-status` are called **directly on",
    "the fixture's port**, never through the dashboard proxy, and are not in the frozen contract or",
    "any allowlist. They exist because a browser cannot make another actor change an item mid-flight",
    "(`drift`), cannot make a reply vanish after it was applied (`drop-next`), and cannot put a job",
    "into `DONE`/`RUNNING`/`PENDING` (`set-job-status`) — the states whose refusals the Gate must",
    "observe.",
    "",
    "## Determinism of the asset",
    "",
    "Built with `vite build` from an isolated `git archive` of the commit (dependencies borrowed",
    "read-only from the worktree; the build log is `vite-build.log`). The tree digest over the sorted",
    "`sha256  ./relative/path` lines:",
    "",
    "```",
    tree_sha,
    "```",
    "",
    "## Scope limits",
    "",
    "* Nothing here exercises real P4 code, a merged P4, or a live engine.",
    "* `P6_ENABLE_P4_WRITES` defaults **off**; the live read allowlist is unchanged at 25 paths and",
    "  the always-on milestone-B write list is unchanged at 4.",
    "The two ON runs are compared on **step name and outcome**, not on their detail strings: those",
    "legitimately carry per-run values (generated ids, timestamps), so comparing them would fail for",
    "a reason that says nothing about the product.",
    "",
    "* Status is **PARTIAL / DEPENDENCY_PENDING**, P4 phase acceptance has **not** passed, and the",
    "  real-P4 verification remains a separate outstanding item.",
    "",
]
(root / "README.md").write_text("\n".join(lines), encoding="utf-8")
print(f"README written from {len(on)} ON and {len(off)} OFF records", file=sys.stderr)
READMEPY

# --- 8. the bundle must not contain a credential ------------------------------------------------
# The fixture banner used to print the tokens it accepts, and this script redirects that banner
# into the bundle, so the frozen evidence carried all three cleartext tokens while its own README
# claimed none did — found by adversarial review. The banner is masked now, and this checks the
# claim instead of asserting it: a bundle that contains any token this run generated is not
# frozen at all.
for secret in "$HUMAN" "$AGENT" "$READONLY"; do
  if grep -rqF -- "$secret" "$OUT"; then
    echo "REFUSING TO FREEZE: a generated token appears in $OUT" >&2
    grep -rlF -- "$secret" "$OUT" >&2
    exit 1
  fi
done
echo "credential check: no generated token appears anywhere in the bundle" >&2

# --- 9. freeze ---------------------------------------------------------------------------------
rm -f "$OUT/BUNDLE-FROZEN.json" "$OUT/stability/BUNDLE-FROZEN.json"
"$PYTHON" "$WORK/scripts/p6/freeze_bundle.py" \
  --freeze "$OUT" \
  --commit "$COMMIT" \
  --kind "browser click-through gate (P6-03b2 memory writes, SIMULATED fixture)" \
  --runs "ON twice with identical step sequences (fixture restarted between runs); OFF once for the default-closed evidence" \
  --asset "$ASSET" --asset-sha256 "$ASSET_SHA" --tree-sha256 "$TREE_SHA" \
  --fixture-value "simulated-p4-writes" \
  --supersedes "20261008T053138Z (withdrawn: its frozen fixture.stderr.txt recorded the run's throwaway tokens; see the index)" \
  --dependency-json '{"name":"P4 memory write slice","status":"NOT MERGED","source_ref":"0c4f8f0e2118711ac9aa2a7006fddc6982962248","acceptance":"P4 phase acceptance NOT passed","consequence":"the Core behind this proxy was a simulated fixture, not P4 code"}'
"$PYTHON" "$WORK/scripts/p6/freeze_bundle.py" --verify "$OUT"
echo "bundle: $OUT" >&2
