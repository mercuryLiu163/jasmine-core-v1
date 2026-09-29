#!/usr/bin/env bash
# Register the Jasmine Core capture entry with Codex.
#
# Scope: PROJECT-LOCAL by default. The entry is written to
# <repo>/.codex/hooks.json, never to ~/.codex/hooks.json, because
# UserPromptSubmit has no tool to match on -- Codex ignores `matcher` for that
# event -- so a globally installed entry runs on every prompt in every project
# on the machine. Scoping comes from where the file lives, not from a matcher.
# Your existing global ~/.codex/hooks.json is read to show what is there and is
# never modified.
#
# This script will NOT:
#   * write or edit ~/.codex/config.toml
#   * compute, guess or inject a trusted_hash
#   * put the API token in any hooks file
# Trusting the entry is a decision about "may this command run automatically on
# every prompt I type in this repository", and it is yours to make in the Codex
# UI. Until you do, P0-T07 is BLOCKED -- which is the correct, honest verdict,
# not a workaround.
#
# Usage
#   scripts/p0-codex-hook-install.sh [options]
#
#     --dry-run          validate and print the change; write nothing
#     --global           write ~/.codex/hooks.json instead (asks first)
#     --hooks-json PATH  target a specific file
#     --python PATH      interpreter to use (default: auto-detected 3.11+)
#     --db PATH          core.db (default: $JASMINE_CORE_DB or the state dir)
#     --force            rewrite the entry even if an identical one is present
#     -h, --help         this text
#
# Everything is validated before anything is written, the file is backed up, and
# re-running is a no-op.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${JASMINE_CORE_STATE_DIR:-$HOME/.local/share/jasmine-core}"
DB="${JASMINE_CORE_DB:-$STATE_DIR/core.db}"
PYTHON="${JASMINE_PYTHON:-}"
HOOKS_JSON="$ROOT/.codex/hooks.json"
GLOBAL_HOOKS="$HOME/.codex/hooks.json"
DRY_RUN=0
FORCE=0
GLOBAL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --global) GLOBAL=1; shift ;;
    --hooks-json) HOOKS_JSON="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- 1. interpreter ------------------------------------------------------------
# Existence is not enough and a wrong one fails confusingly. The system python3
# on macOS is 3.9, below this project's 3.11 floor, and a hook that cannot import
# the capture module is indistinguishable from a hook that captured nothing.
step "Checking the Python interpreter"
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
[ -n "$PYTHON" ] || fail "no Python 3.11+ interpreter found; pass --python /path/to/python3.11+"
PYTHON="$("$PYTHON" -c 'import sys; print(sys.executable)')"
say "   interpreter : $PYTHON ($("$PYTHON" -V 2>&1))"

# --- 2. the capture module must import from this checkout ---------------------
step "Checking the capture entry imports from this checkout"
HOOK_SCRIPT="$ROOT/scripts/jasmine-capture-hook.sh"
[ -x "$HOOK_SCRIPT" ] || chmod +x "$HOOK_SCRIPT" 2>/dev/null
[ -r "$HOOK_SCRIPT" ] || fail "missing $HOOK_SCRIPT"
PYTHONPATH="$ROOT/src" "$PYTHON" -c 'import jasmine_core.capture.codex_user_prompt_submit as m; assert m.EVENT_TYPE' \
  || fail "jasmine_core does not import from $ROOT/src"
say "   module      : jasmine_core.capture.codex_user_prompt_submit (from $ROOT/src)"

# --- 3. the database and its host ---------------------------------------------
step "Checking the Core database"
[ -f "$DB" ] || fail "no core.db at $DB; run: JASMINE_CORE_DB=$DB PYTHONPATH=$ROOT/src $PYTHON -m jasmine_core.cli bootstrap"
HOST_ID="$(PYTHONPATH="$ROOT/src" JASMINE_CORE_DB="$DB" "$PYTHON" -m jasmine_core.cli schema 2>/dev/null \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["schema_version"])' 2>/dev/null)" \
  || fail "cannot read the schema of $DB"
say "   database    : $DB (schema v$HOST_ID)"

HOST_ID="$(PYTHONPATH="$ROOT/src" "$PYTHON" - "$DB" <<'PY'
import sqlite3, sys
# A plain sqlite3 connection has no row factory, so index the row positionally.
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
row = con.execute("SELECT host_id FROM hosts ORDER BY first_seen_at LIMIT 1").fetchone()
print(row[0] if row else "")
PY
)"
[ -n "$HOST_ID" ] || fail "no host is registered in $DB; run jasmine-core bootstrap"
say "   host_id     : $HOST_ID"

# --- 4. the token --------------------------------------------------------------
# The entry reads the token at run time from this 0600 file. The token is
# therefore never written into a hooks.json, and never into this repository.
step "Checking the capture token"
TOKEN_FILE="$STATE_DIR/capture-token"
if [ ! -r "$TOKEN_FILE" ]; then
  TOKEN_FILE="$(ls "$STATE_DIR"/*.token 2>/dev/null | head -1)"
fi
[ -n "$TOKEN_FILE" ] && [ -r "$TOKEN_FILE" ] || fail "no capture token under $STATE_DIR; run jasmine-core bootstrap"
[ -s "$TOKEN_FILE" ] || fail "the token file is empty: $TOKEN_FILE"
TOKEN="$(cat "$TOKEN_FILE")"
[ -n "$TOKEN" ] || fail "the token file is empty: $TOKEN_FILE"
say "   token file  : $TOKEN_FILE ($(wc -c < "$TOKEN_FILE" | tr -d ' ') bytes, read at run time, never written to a hooks file)"

# --- 5. what is already installed ---------------------------------------------
step "Reporting the current hook configuration"
if [ -f "$GLOBAL_HOOKS" ]; then
  say "   global      : $GLOBAL_HOOKS"
  "$PYTHON" - "$GLOBAL_HOOKS" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    config = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"                 (unreadable: {exc})"); raise SystemExit
for event, entries in (config.get("hooks") or {}).items():
    for index, entry in enumerate(entries or []):
        for hook in (entry or {}).get("hooks") or []:
            print(f"                 {event}[{index}]: {str(hook.get('command'))[:88]}")
PY
  say "                 ^ left untouched by this script."
else
  say "   global      : $GLOBAL_HOOKS (absent)"
fi

# --- 6. the entry ---------------------------------------------------------------
# The command names paths only. No token, no environment secret, nothing that
# would become a leaked secret if the hooks file were ever committed.
#
# Every path is single-quoted because Codex runs the command through a shell and
# this checkout's path contains a space. An unquoted path fails at the hook, which
# looks exactly like a hook that captured nothing.
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
COMMAND="$(quote "$HOOK_SCRIPT") --host $(quote "$HOST_ID") --db $(quote "$DB") --state-dir $(quote "$STATE_DIR")"
step "Building the entry"
say "   command     : $COMMAND"
say "   target      : $HOOKS_JSON"

CHANGES="$("$PYTHON" - "$HOOKS_JSON" "$COMMAND" "$FORCE" "$DRY_RUN" <<'PY'
import json, sys
from pathlib import Path

path, command, force, dry = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1", sys.argv[4] == "1"
entry = {"hooks": [{"type": "command", "command": command, "timeout": 8,
                    "additionalContextLimit": 1500}]}

if path.is_file():
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps({"status": "refused", "reason": f"{path} is not valid JSON: {exc}"}))
        raise SystemExit
else:
    config = {"description": "Jasmine Core capture entry (project-local)",
              "hooks": {"SessionStart": [], "UserPromptSubmit": [], "Stop": [],
                        "PostToolUse": []}}

hooks = config.setdefault("hooks", {})
existing = list(hooks.get("UserPromptSubmit") or [])
mine = [e for e in existing
        if any("jasmine-capture-hook.sh" in str(h.get("command", ""))
               for h in (e or {}).get("hooks") or [])]

if mine and not force:
    same = all(h.get("command") == command for e in mine for h in (e or {}).get("hooks") or [])
    print(json.dumps({
        "status": "unchanged" if same else "update",
        "reason": "an entry from this installer is already present"
                  + (" with the same command" if same else " with a different command; --force will replace it"),
        "index": existing.index(mine[0]),
        "command": command,
    }))
    raise SystemExit

kept = [e for e in existing if e not in mine]
hooks["UserPromptSubmit"] = kept + [entry]
config["description"] = config.get("description") or "Jasmine Core capture entry (project-local)"
if dry:
    print(json.dumps({"status": "would-write", "command": command,
                      "preserved_entries": len(kept), "path": str(path)}))
    raise SystemExit

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"status": "written", "command": command, "path": str(path),
                  "preserved_entries": len(kept)}))
PY
)"
STATUS="$(echo "$CHANGES" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
REASON="$(echo "$CHANGES" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("reason",""))')"
say "   result      : $STATUS${REASON:+ ($REASON)}"

if [ "$DRY_RUN" = "1" ]; then
  step "Dry run: nothing was written"
  say "   To apply: scripts/p0-codex-hook-install.sh"
  exit 0
fi

case "$STATUS" in
  written)
    step "Backing up and reviewing"
    if [ -f "$HOOKS_JSON" ]; then
      BACKUP="$HOOKS_JSON.bak.$(date +%Y%m%dT%H%M%SZ)"
      cp -p "$HOOKS_JSON" "$BACKUP"
      say "   backup      : $BACKUP"
    fi
    say "   the file now reads:"
    sed 's/^/                 /' "$HOOKS_JSON"
    ;;
  unchanged)
    step "Nothing to do"
    say "   the entry is already installed with the same command"
    ;;
  update)
    # Never silent: an out-of-date entry left in place is exactly the state that
    # makes the Gate report a product failure for a setup problem.
    fail "$REASON -- re-run with --force to replace it"
    ;;
  refused)
    fail "$REASON"
    ;;
  *)
    fail "unexpected installer status: $STATUS"
    ;;
esac

# --- 7. trust is the operator's ------------------------------------------------
step "Next: trust the entry (this script will not do it for you)"
cat <<EOF
  Codex only runs a hook command whose trusted_hash it has recorded in
  ~/.codex/config.toml. An untrusted entry is skipped silently, and
  'codex exec' cannot create the hash non-interactively, so P0-T07 stays
  BLOCKED until you do this in the Codex UI:

    1. Open this repository in Codex.
    2. Accept the prompt to review and trust the new hook command
       (it names only paths -- no token is in it).
    3. Confirm a trusted_hash now exists for:
         $HOOKS_JSON:user_prompt_submit:<index>

  Then run the gate:

    scripts/p0-t07-real-conversation.sh --out /tmp/p0-t07 --db $DB

  This script did not modify ~/.codex/hooks.json, ~/.codex/config.toml, or
  your global V0 entry.
EOF
