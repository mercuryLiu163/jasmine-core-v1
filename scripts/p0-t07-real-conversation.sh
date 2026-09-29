#!/usr/bin/env bash
# P0-T07: the real-conversation Gate.
#
# Drives an actual `codex exec` turn with an actual prompt and checks that the
# UserPromptSubmit capture entry turned it into a Raw Event. There is no
# synthetic path: if the entry is not wired up, the case is BLOCKED; if it is
# wired up and still captures nothing, the case is FAIL.
#
# Why the case is often BLOCKED
# -----------------------------
# Codex only runs a hook command whose `trusted_hash` it has recorded in
# ~/.codex/config.toml under [hooks.state]. An entry present in a hooks.json
# without a matching trusted_hash is skipped silently, and `codex exec` offers
# no non-interactive way to create one. The trust decision -- "may this command
# run automatically on every prompt?" -- belongs to the operator, so this script
# does not forge a hash and does not pass --dangerously-bypass-hook-trust.
#
# How the three outcomes are told apart
# -------------------------------------
#   hooks.json has no entry for the capture script   -> BLOCKED (not wired up)
#   entry present, no trusted_hash in config.toml     -> BLOCKED (not trusted)
#   entry present and trusted, but the entry's own
#     hook-invocations.log gained no line            -> BLOCKED (not invoked)
#   the entry ran and did not capture                 -> FAIL (the product)
#   the entry ran and captured, and the text reads
#     back after a restart                            -> PASS
#
# The third check is why the capture entry writes a trace line on every path:
# without it, "Codex never invoked us" and "the Core was down" look identical.
#
# Usage
#   scripts/p0-t07-real-conversation.sh [--out DIR] [--db PATH] [--port N]
#                                      [--hooks-json PATH] [--state-dir DIR]
#
# Prerequisites (all performed by scripts/p0-codex-hook-install.sh and by you):
#   1. jasmine-core bootstrap           -> a host, an actor, and a token
#   2. the entry present in a hooks.json
#   3. that entry trusted in the Codex UI, so Codex records its trusted_hash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${JASMINE_PYTHON:-}"
OUT="${TMPDIR:-/tmp}/jasmine-core-t07"
DB="${JASMINE_CORE_DB:-$OUT/core.db}"
STATE_DIR="${JASMINE_CORE_STATE_DIR:-$HOME/.local/share/jasmine-core}"
HOOKS_JSON="$ROOT/.codex/hooks.json"
TOKEN_FILE=""
PORT=""
PROMPT="P0-T07 jasmine capture probe: answer with the single word ack."

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --hooks-json) HOOKS_JSON="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

mkdir -p "$OUT" && chmod 700 "$OUT"
RESULT="$OUT/p0-t07-result.json"
TRACE="$STATE_DIR/hook-invocations.log"
CAPTURE_LOG="$STATE_DIR/capture.log"

die() { echo "$1" >&2; exit 64; }

# --- interpreter -------------------------------------------------------------
# Existence is not enough: this project needs 3.11+, and a system python3 on
# macOS is usually older than that. A wrong interpreter produces an import error
# inside the hook, which looks like "the entry did not capture anything".
if [ -z "$PYTHON" ]; then
  for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                   /opt/homebrew/bin/python3.11 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi
[ -n "$PYTHON" ] || die "no Python 3.11+ interpreter found; set JASMINE_PYTHON"
PYTHON="$("$PYTHON" -c 'import sys; print(sys.executable)')"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "$PYTHON is older than 3.11"

# --- token -------------------------------------------------------------------
# The capture entry reads the token at run time from a 0600 file, so the token
# never appears in hooks.json. Locate it under whichever state dir is in use.
if [ -z "$TOKEN_FILE" ]; then
  for candidate in "$STATE_DIR/capture-token" "$STATE_DIR/token" \
                   "$HOME/.local/share/jasmine-core/capture-token"; do
    [ -r "$candidate" ] && TOKEN_FILE="$candidate" && break
  done
fi
[ -n "$TOKEN_FILE" ] || die "no capture token found; run 'jasmine-core bootstrap' first"
TOKEN="$(cat "$TOKEN_FILE")"
[ -n "$TOKEN" ] || die "the capture token file is empty: $TOKEN_FILE"

# --- port --------------------------------------------------------------------
# A pre-existing server on the port would make the restart check a fiction: the
# script would talk to someone else's process, kill nothing, and read back from
# a Core that never stopped. Refuse rather than measure something untrue.
if [ -z "$PORT" ]; then
  PORT="$("$PYTHON" -c 'import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
fi
if curl -sf "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1; then
  die "something is already serving 127.0.0.1:$PORT; pick another --port"
fi

# --- is the entry wired up, and is it trusted? -------------------------------
# Read-only. config.toml is never written here.
ENTRY_STATE="$("$PYTHON" - "$HOOKS_JSON" "$ROOT" <<'PY'
import json, os, re, sys
from pathlib import Path

hooks_path, root = Path(sys.argv[1]), Path(sys.argv[2])
result = {"path": str(hooks_path), "present": hooks_path.is_file(),
          "registered": False, "index": None, "trusted": False, "reason": ""}
if not result["present"]:
    result["reason"] = f"{hooks_path} does not exist, so Codex has no entry to run"
    print(json.dumps(result)); raise SystemExit
try:
    config = json.loads(hooks_path.read_text(encoding="utf-8"))
except Exception as exc:
    result["reason"] = f"{hooks_path} is not readable JSON: {exc}"
    print(json.dumps(result)); raise SystemExit

entries = (config.get("hooks") or {}).get("UserPromptSubmit") or []
# Two shapes are recognised: the wrapper this project installs, and a direct
# `python -m jasmine_core.capture.codex_user_prompt_submit` invocation.
markers = ("jasmine-capture-hook.sh", "jasmine_core.capture.codex_user_prompt_submit")
for index, entry in enumerate(entries):
    for sub, hook in enumerate((entry or {}).get("hooks") or []):
        if any(marker in str(hook.get("command", "")) for marker in markers):
            result.update(registered=True, index=index, sub=sub,
                          command=str(hook.get("command", "")))
            break
if not result["registered"]:
    result["reason"] = (f"no UserPromptSubmit command in {hooks_path} references this "
                        f"project's capture entry (looked for {' or '.join(markers)})")
    print(json.dumps(result)); raise SystemExit

# Trust is recorded by Codex against the resolved path of the hooks file, keyed
# "<path>:<event>:<index>:<sub>". Read-only: the operator grants trust, not us.
config_toml = Path.home() / ".codex" / "config.toml"
key = f"{hooks_path.resolve()}:user_prompt_submit:{result['index']}:{result['sub']}"
if config_toml.is_file():
    text = config_toml.read_text(encoding="utf-8", errors="replace")
    # TOML bare keys are quoted when they contain ':' or '/'
    quoted = '"' + key.replace('\\', '\\\\').replace('"', '\\"') + '"'
    bare = key
    trusted = any(
        re.search(rf"hooks\.state\.({re.escape(quoted)}|{re.escape(bare)})\]\s*\n"
                  rf"[^\n]*trusted_hash\s*=", text)
        for _ in (0,)
    )
    result["trusted"] = bool(trusted)
    result["trust_key"] = key
    if not trusted:
        result["reason"] = (f"the entry is registered at {hooks_path} but {config_toml} has no "
                            f"trusted_hash for {key}, so Codex skips it silently. Trusting it is "
                            f"an operator decision; this script will not edit config.toml.")
else:
    result["reason"] = f"{config_toml} does not exist, so no hook can be trusted"
print(json.dumps(result))
PY
)"
REGISTERED="$(echo "$ENTRY_STATE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["registered"])')"
TRUSTED="$(echo "$ENTRY_STATE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["trusted"])')"
BLOCK_REASON="$(echo "$ENTRY_STATE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["reason"])')"

# `block` takes no argument: the reason is always BLOCK_REASON, computed once by
# the read-only wiring check above.
# `block` takes no argument: the reason is always BLOCK_REASON, computed once by
# the read-only wiring check above.
block() {
  "$PYTHON" - "$RESULT" "$BLOCK_REASON" "$ENTRY_STATE" <<'PY'
import json, sys
from pathlib import Path
path, reason, entry = sys.argv[1:4]
Path(path).write_text(json.dumps({
    "entry_registered": json.loads(entry)["registered"],
    "entry_trusted": json.loads(entry)["trusted"],
    "blocked": True, "captured": False, "reason": reason,
    "prompt": None, "problems": [reason], "failure_class": "environment",
    "failure_output": reason, "tools": [{"action": reason}], "reproduction": [],
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"BLOCKED: {reason}", file=sys.stderr)
PY
  exit 2
}

[ -d "$ROOT/src/jasmine_core" ] || block
[ "$REGISTERED" = "True" ] || block
[ "$TRUSTED" = "True" ] || block

# --- the Core ----------------------------------------------------------------
start_core() {
  PYTHONPATH="$ROOT/src" JASMINE_CORE_DB="$DB" "$PYTHON" -m jasmine_core.cli serve \
    --port "$PORT" >> "$OUT/api.log" 2>&1 &
  SERVER=$!
  for _ in $(seq 1 80); do
    curl -sf "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1 && return 0
    kill -0 "$SERVER" 2>/dev/null || return 1
    sleep 0.25
  done
  return 1
}
stop_core() { [ -n "${SERVER:-}" ] && kill "$SERVER" 2>/dev/null && wait "$SERVER" 2>/dev/null; }

PYTHONPATH="$ROOT/src" JASMINE_CORE_DB="$DB" "$PYTHON" -m jasmine_core.cli migrate >/dev/null \
  || die "the migration failed for $DB"

start_core || block
FIRST_PID="$SERVER"
trap 'stop_core' EXIT

# Refuse to continue if the token is not valid for this database: otherwise every
# capture is refused, the event count never moves, and the Gate reports a
# product failure for a setup mistake.
AUTH_CODE="$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/v1/events?limit=1")"
[ "$AUTH_CODE" = "200" ] || die "the token in $TOKEN_FILE is not valid for $DB (HTTP $AUTH_CODE); run 'jasmine-core bootstrap --db $DB'"

event_count() {
  curl -sf -H "Authorization: Bearer $TOKEN" \
    "http://127.0.0.1:$PORT/v1/events?limit=1000" \
    | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["count"])'
}

BEFORE_EVENTS="$(event_count)" || die "could not read the event count before the conversation"
[ -f "$TRACE" ] && cp "$TRACE" "$OUT/trace-before.log"
TRACE_LINES_BEFORE=0
[ -f "$TRACE" ] && TRACE_LINES_BEFORE="$(wc -l < "$TRACE" | tr -d ' ')"

# --- the real conversation ---------------------------------------------------
( cd "$ROOT" && codex exec --skip-git-repo-check "$PROMPT" ) \
  > "$OUT/codex-stdout.txt" 2> "$OUT/codex-stderr.txt"
CODEX_EXIT=$?
sleep 2

AFTER_EVENTS="$(event_count)" || die "could not read the event count after the conversation"
curl -sf -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:$PORT/v1/events?limit=1000" > "$OUT/events-after.json" \
  || die "could not download the events after the conversation"
[ -f "$TRACE" ] && cp "$TRACE" "$OUT/trace-after.log"
[ -f "$CAPTURE_LOG" ] && cp "$CAPTURE_LOG" "$OUT/capture.log"

# --- restart, then read the same event back ----------------------------------
# Stop the process we started and start a *new* one. Asserting the pid changed is
# what makes the read-back evidence mean anything.
stop_core
SERVER=""
start_core || die "the Core did not come back up after the restart; see $OUT/api.log"
SECOND_PID="$SERVER"
[ "$FIRST_PID" != "$SECOND_PID" ] || die "the Core pid did not change across the restart; the read-back would prove nothing"

"$PYTHON" - "$RESULT" "$PROMPT" "$BEFORE_EVENTS" "$AFTER_EVENTS" "$CODEX_EXIT" \
         "$OUT/events-after.json" "$DB" "$PORT" "$TRACE" "$TRACE_LINES_BEFORE" \
         "$TOKEN_FILE" "$FIRST_PID" "$SECOND_PID" "$ENTRY_STATE" <<'PY'
import json, subprocess, sys
from pathlib import Path

(result, prompt, before, after, codex_exit, events_file, db_path, port, trace,
 trace_before, token_file, first_pid, second_pid, entry_state) = sys.argv[1:15]
before, after, codex_exit = int(before), int(after), int(codex_exit)
trace_before = int(trace_before)
entry = json.loads(entry_state)

trace_new = []
if Path(trace).is_file():
    lines = Path(trace).read_text(encoding="utf-8", errors="replace").splitlines()
    trace_new = [json.loads(line) for line in lines[trace_before:] if line.strip()]

problems = []
captured = None
events = json.loads(Path(events_file).read_text(encoding="utf-8"))["events"]
for event in events:
    if event["payload"].get("text") == prompt:
        captured = event
        break

invoked = bool(trace_new)
if codex_exit != 0:
    problems.append(f"codex exec exited {codex_exit}")
if not invoked:
    # Registered and trusted, yet the entry left no trace. That is a fact about
    # the environment, not about the product: nothing ran to get it wrong.
    blocked = True
    reason = ("the entry is registered and trusted but left no invocation trace, so Codex "
              "did not run it for this turn")
else:
    blocked = False
    reason = None
    if after <= before:
        problems.append(
            f"the entry ran but the Core went from {before} to {after} events: the capture "
            "stored nothing. See the captured lines in the invocation trace.")
    if captured is None and after > before:
        problems.append("a new event appeared but none carries the prompt text verbatim")

readback = None
if captured is not None:
    out = subprocess.run(
        ["curl", "-sf", "-H", f"Authorization: Bearer {Path(token_file).read_text().strip()}",
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
    "entry_registered": entry["registered"],
    "entry_trusted": entry["trusted"],
    "blocked": blocked,
    "captured": (not blocked) and captured is not None and not problems,
    "reason": reason,
    "prompt": prompt,
    "agent_response": Path(events_file).parent.joinpath("codex-stdout.txt").read_text(
        encoding="utf-8", errors="replace")[-800:],
    "problems": problems,
    "failure_class": None if not problems else "capture",
    "failure_output": "; ".join(problems) if problems else None,
    "state_before": {"events": before, "invocation_trace_lines": trace_before},
    "state_after": {"events": after, "captured_event": captured,
                    "invocation_trace_lines": trace_before + len(trace_new)},
    "invocation_trace": trace_new,
    "readback_after_restart": readback,
    "context": {
        "db_path": db_path, "codex_exit": codex_exit,
        "token_file": token_file, "hooks_json": entry["path"],
        "core_pids": [first_pid, second_pid],
        "note": "a real codex process, a real session, a real prompt, the real hook path",
    },
    "tools": [
        {"command": f"codex exec --skip-git-repo-check {prompt!r}", "exit_code": codex_exit,
         "cwd": "the repository root"},
        {"action": "stop the Core", "pid": first_pid},
        {"action": "start a new Core", "pid": second_pid},
        {"action": "GET the captured event after the restart"},
    ],
    "reproduction": [
        "jasmine-core bootstrap --db $DB",
        "scripts/p0-codex-hook-install.sh   # writes .codex/hooks.json; trust is yours",
        "jasmine-core serve --port $PORT --db $DB",
        "scripts/p0-t07-real-conversation.sh --out $OUT --db $DB",
    ],
    "evidence": ["codex-stdout.txt", "codex-stderr.txt", "events-after.json",
                 "trace-before.log", "trace-after.log", "capture.log", "api.log"],
}
Path(result).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"entry_registered": entry["registered"], "entry_trusted": entry["trusted"],
                  "invoked": invoked, "captured": payload["captured"], "blocked": blocked,
                  "problems": problems}, ensure_ascii=False))
if blocked:
    print("BLOCKED: " + str(reason), file=sys.stderr)
sys.exit(0 if payload["captured"] else 1)
PY
STATUS=$?
trap - EXIT
stop_core
[ "$STATUS" -ne 0 ] && echo "P0-T07 did not pass; see $RESULT" >&2
exit "$STATUS"
