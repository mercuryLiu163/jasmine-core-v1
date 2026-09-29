#!/usr/bin/env bash
# P0-T07: the real-conversation Gate.
#
# This drives an actual Codex session with an actual prompt and checks that the
# UserPromptSubmit capture entry turned it into a Raw Event. There is no
# synthetic path: if the hook does not fire, the case is BLOCKED, not PASS.
#
# Why this may be BLOCKED
# -----------------------
# Codex only runs a hook command whose `trusted_hash` is recorded in
# ~/.codex/config.toml under [hooks.state]. An entry added to
# ~/.codex/hooks.json without a matching trusted_hash is silently skipped, and
# `codex exec` has no non-interactive way to create one. The trust decision
# ("may this command run automatically on every prompt?") belongs to the
# operator, so this script does not forge a hash and does not pass
# --dangerously-bypass-hook-trust. Until the operator trusts the hook, this
# script exits 2 and the case stays BLOCKED.
#
# Usage
#   scripts/p0-t07-real-conversation.sh --out <dir> [--db <path>]
#
# Required before the first run (one time, by the operator):
#   1. jasmine-core bootstrap   (creates a host, an actor and a token)
#   2. add the hook to ~/.codex/hooks.json:
#        {"hooks": [{"type": "command",
#                    "command": "JASMINE_CORE_HOST_ID=<hst_…> JASMINE_CORE_STATE_DIR=<dir> \
#                               /path/to/python3 -m jasmine_core.capture.codex_user_prompt_submit",
#                    "timeout": 8}]}
#   3. trust it in the Codex UI / app, so Codex records the trusted_hash.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
OUT="${TMPDIR:-/tmp}/jasmine-core-t07"
DB="${JASMINE_CORE_DB:-$OUT/core.db}"
STATE_DIR="${JASMINE_CORE_STATE_DIR:-$HOME/.local/share/jasmine-core}"
PROMPT="P0-T07 jasmine capture probe: answer with the single word ack."
PORT="${JASMINE_CORE_PORT:-8787}"

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

mkdir -p "$OUT" && chmod 700 "$OUT"
RESULT="$OUT/p0-t07-result.json"
TOKEN_FILE="$STATE_DIR/capture-token"

fail() {
  "$PYTHON" - "$RESULT" "$1" <<'PY'
import json, sys
path, reason = sys.argv[1], sys.argv[2]
json.dump({
    "captured": False,
    "reason": reason,
    "prompt": None,
    "problems": [reason],
    "failure_class": "environment",
    "failure_output": reason,
    "tools": [{"action": reason}],
    "reproduction": ["scripts/p0-t07-real-conversation.sh"],
}, open(path, "w"), ensure_ascii=False, indent=2)
print(f"BLOCKED: {reason}", file=sys.stderr)
PY
  exit 2
}

[ -f "$TOKEN_FILE" ] || fail "no capture token at $TOKEN_FILE; run 'jasmine-core bootstrap' first"

PYTHONPATH="$ROOT/src" JASMINE_CORE_DB="$DB" "$PYTHON" -m jasmine_core.cli migrate >/dev/null \
  || fail "the migration failed for $DB"

PYTHONPATH="$ROOT/src" JASMINE_CORE_DB="$DB" "$PYTHON" -m jasmine_core.cli serve --port "$PORT" \
  > "$OUT/api.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/v1/health" >/dev/null && break
  sleep 0.25
done
curl -sf "http://127.0.0.1:$PORT/v1/health" > "$OUT/health-before.json" \
  || fail "the Core API did not start on port $PORT; see $OUT/api.log"

event_count() {
  curl -sf -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
    "http://127.0.0.1:$PORT/v1/events?limit=1000" \
    | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["count"])'
}

# Fail loudly, and before spending a real conversation, if the token does not
# belong to this database. A 401 here would otherwise surface as an empty
# events file and an unexplained capture failure.
AUTH_PROBE=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "http://127.0.0.1:$PORT/v1/events?limit=1")
[ "$AUTH_PROBE" = "200" ] || fail \
  "the capture token in $TOKEN_FILE is not valid for $DB (HTTP $AUTH_PROBE); run 'jasmine-core bootstrap --db $DB' and update the token file"

BEFORE_EVENTS=$(event_count) || fail "could not read the event count before the conversation"

# The real conversation. Everything about this turn is real: a real codex
# process, a real session, a real prompt delivered through the real hook path.
codex exec --skip-git-repo-check "$PROMPT" > "$OUT/codex-stdout.txt" 2> "$OUT/codex-stderr.txt"
CODEX_EXIT=$?

sleep 2

AFTER_EVENTS=$(event_count) || fail "could not read the event count after the conversation"
curl -sf -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
  "http://127.0.0.1:$PORT/v1/events?limit=1000" > "$OUT/events-after.json" \
  || fail "could not download the events after the conversation"

# Restart, then read the same event back: P0-T07 requires the text to survive.
kill $SERVER 2>/dev/null; wait $SERVER 2>/dev/null
PYTHONPATH="$ROOT/src" JASMINE_CORE_DB="$DB" "$PYTHON" -m jasmine_core.cli serve --port "$PORT" \
  >> "$OUT/api.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/v1/health" >/dev/null && break
  sleep 0.25
done

EVENTS_FILE="$OUT/events-after.json"

# Is the capture entry actually registered with Codex? An entry present in
# ~/.codex/hooks.json still does not run unless Codex has recorded its
# trusted_hash, so this distinguishes "not wired up" (BLOCKED: no real entry)
# from "wired up and still captured nothing" (FAIL: the product is wrong).
HOOKS_JSON="${CODEX_HOOKS_JSON:-$HOME/.codex/hooks.json}"
if [ -f "$HOOKS_JSON" ] && grep -q "jasmine_core.capture.codex_user_prompt_submit" "$HOOKS_JSON"; then
  ENTRY_REGISTERED=true
else
  ENTRY_REGISTERED=false
fi

"$PYTHON" - "$RESULT" "$PROMPT" "$BEFORE_EVENTS" "$AFTER_EVENTS" "$CODEX_EXIT" \
         "$EVENTS_FILE" "$DB" "$PORT" "$ENTRY_REGISTERED" <<'PY'
import json, subprocess, sys
from pathlib import Path

result, prompt, before, after, codex_exit, events_file, db_path, port, registered = sys.argv[1:10]
before, after, codex_exit = int(before), int(after), int(codex_exit)
registered = registered == "true"
events = json.loads(Path(events_file).read_text(encoding="utf-8"))["events"]

captured = None
for event in events:
    if event["payload"].get("text") == prompt:
        captured = event
        break

# The two outcomes are not the same claim. No capture entry wired up is a
# BLOCKED environment, and saying FAIL would blame the product for a step the
# operator has not taken. A wired-up entry that captures the wrong thing, or
# nothing, is a real failure.
if not registered:
    blocked_reason = (
        "no real capture entry: ~/.codex/hooks.json has no UserPromptSubmit command for "
        "jasmine_core.capture.codex_user_prompt_submit, so Codex was never asked to deliver "
        "this turn to the Core. A real conversation ran and the Core was reachable, but the "
        "entry that P0-T07 is meant to validate was not in place. A synthetic payload was "
        "NOT substituted for the real one."
    )
    problems = [blocked_reason]
elif after <= before:
    problems = [
        f"the entry is registered but the Core went from {before} to {after} events: the "
        "capture entry ran and stored nothing"
    ]
else:
    problems = []

if codex_exit != 0:
    problems.append(f"codex exec exited {codex_exit}")
if after > before and captured is None:
    problems.append("a new event appeared but none carries the prompt text verbatim")

readback = None
if captured is not None:
    out = subprocess.run(
        ["curl", "-sf", "-H", f"Authorization: Bearer {(Path.home() / '.local/share/jasmine-core/capture-token').read_text().strip()}",
         f"http://127.0.0.1:{port}/v1/events/{captured['event_id']}"],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        problems.append(f"the event could not be read back after the restart: {out.stderr[:200]}")
    else:
        readback = json.loads(out.stdout)["event"]
        if readback["payload"]["text"] != prompt:
            problems.append("the text read back after the restart differs from what was typed")
        for field in ("actor_id", "host_id", "source_system", "source_event_id"):
            if readback[field] != captured[field]:
                problems.append(f"{field} changed across the restart")

payload = {
    "entry_registered": registered,
    "blocked": (not registered) and codex_exit == 0,
    "captured": registered and captured is not None and not problems,
    "reason": blocked_reason if not registered else (None if not problems else "; ".join(problems)),
    "prompt": prompt,
    "agent_response": (Path(events_file).parent / "codex-stdout.txt").read_text(
        encoding="utf-8")[-800:],
    "problems": problems,
    "failure_class": "environment" if not registered else ("capture" if problems else None),
    "failure_output": "; ".join(problems) if problems else None,
    "state_before": {"events": before, "capture_entry_registered": registered},
    "state_after": {"events": after, "captured_event": captured},
    "readback_after_restart": readback,
    "context": {
        "db_path": db_path,
        "codex_exit": codex_exit,
        "note": "real codex process, real session, real prompt, real hook path",
    },
    "tools": [
        {"command": f"codex exec --skip-git-repo-check {prompt!r}", "exit_code": codex_exit},
        {"action": "restart the Core, then GET the captured event"},
    ],
    "reproduction": [
        "jasmine-core bootstrap",
        "add the capture hook to ~/.codex/hooks.json and trust it in the Codex UI",
        "jasmine-core serve --port 8787",
        "scripts/p0-t07-real-conversation.sh --out <dir>",
    ],
    "evidence": [
        "codex-stdout.txt / codex-stderr.txt",
        "events-after.json",
        "api.log",
    ],
}
Path(result).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"entry_registered": registered, "captured": payload["captured"],
                  "blocked": payload["blocked"], "problems": problems}, ensure_ascii=False))
if payload["blocked"]:
    print("BLOCKED: " + blocked_reason, file=sys.stderr)
sys.exit(0 if payload["captured"] else 1)
PY
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  echo "P0-T07 did not pass; see $RESULT" >&2
fi
exit "$STATUS"
