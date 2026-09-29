# Jasmine Core V1 — P0-03 API, auth, audit, capture, validation

Delivers the P0-03 row of the taskbook: a callable Core API over the P0-02
storage layer, with identity, authorisation, audit, one real capture entry, and
the minimum validation recording the taskbook demands.

## What is implemented

| Piece | Where |
| --- | --- |
| Request/response values, transport-independent handlers | `src/jasmine_core/api/` |
| Loopback-only HTTP adapter, one connection per request thread | `src/jasmine_core/api/server.py` |
| Bearer tokens, scopes, revocation | `src/jasmine_core/auth.py` |
| Append-only, redacted, length-clipped audit log | `src/jasmine_core/audit.py` |
| Resolve → authenticate → authorise → handle → audit | `src/jasmine_core/api/dispatch.py` |
| Codex `UserPromptSubmit` capture entry | `src/jasmine_core/capture/` |
| Minimal Validation Recorder | `src/jasmine_core/validation/recorder.py` |
| The P0 acceptance matrix | `scripts/p0-acceptance.py`, `scripts/p0-t07-real-conversation.sh` |
| `bootstrap` / `register` setup commands | `src/jasmine_core/cli.py` |

## Running it

```bash
export JASMINE_CORE_DB=~/.local/share/jasmine-core/core.db
PYTHONPATH=src python3 -m jasmine_core.cli bootstrap     # host, actor, and a token
PYTHONPATH=src python3 -m jasmine_core.cli serve        # http://127.0.0.1:8787
```

The token is printed once and only its SHA-256 is stored. The capture installer
registers a `UserPromptSubmit` entry in this checkout's `.codex/hooks.json`.
The operator must review and trust that command in Codex before the real
conversation Gate can run it; the installer does not edit
`~/.codex/config.toml`.

## Authorisation

The dispatcher resolves the route, authenticates, checks the scope, runs the
handler, and audits. Missing, unknown, revoked, or wrong-scope credentials
cannot write Truth or update `api_keys.last_used_at`; those refusals still get
an audit row. After successful authentication and scope checking,
`last_used_at` is updated on a best-effort basis before the handler runs. A
later handler refusal can therefore leave that timestamp updated while its
Truth write is rolled back or never attempted.

## What the audit log does not keep

`path`, a caller-supplied `X-Request-Id`, and any `details` value are all
redacted before they are stored, because a client can put a token in any of
them. A `Bearer%20…`, `sk-…`, `ghp_…`, JWT or PEM in a URL is masked, and a
`X-Request-Id` that looks like a credential is replaced with a generated one.
Field values are length-clipped so a row cannot grow with the request body.
The HTTP access log omits the request target entirely because both its path and
query can carry credentials.

## Deliberately not implemented

No `PUT`/`PATCH`/`DELETE`, no Task state machine, no `expected_revision`
optimistic locking, no Evidence, no Interpreter, no Rule/Guard, no Dashboard, no
offline sync, no multi-client adapter. The capture entry translates one hook
event into one Raw Event; it is not an Agent adapter and does not interpret
anything.

## Tests

The standard-library suite covers the API and capture components. The P0
acceptance runner records a seven-case matrix, including P0-T07; it records a
case crash as FAIL and still writes the evidence bundle. The runner exits 0 / 1
/ 2 for PASS / FAIL / BLOCKED. A fixture or direct hook invocation does not
satisfy the real-conversation P0-T07 Gate.

## Wiring the capture entry into Codex

```bash
scripts/p0-codex-hook-install.sh --dry-run     # validate and preview
scripts/p0-codex-hook-install.sh               # write <repo>/.codex/hooks.json
```

The entry is **project-local on purpose**. `UserPromptSubmit` has no tool to
match on, so Codex ignores a `matcher` on that event: a *globally* installed
entry runs on every prompt in every project on the machine. Scoping comes from
where the hooks file lives, and the installer writes only
`<repo>/.codex/hooks.json` by default. A custom `--hooks-json` target must also
be inside this checkout's `.codex` directory. The installer rejects
`--global`; it does not modify `~/.codex/hooks.json`. A changed or duplicate
Jasmine entry requires explicit `--force`. It rejects a symlinked project
`.codex` directory so the default target cannot resolve to a global hooks file.
The installer backs up an existing
hooks file before changing it; an unchanged entry is a no-op.

The command contains the wrapper path plus validated `--python`, host, database,
and state-directory paths, but no token or `PYTHONPATH`. The installer accepts
`--python PATH` or chooses Python 3.11+ and pins its resolved executable path
in the hook command. The wrapper does not fall back to another interpreter if
that pinned path later becomes invalid. At run time it reads the token from
`<state-dir>/capture-token`; the Gate requires owner-only permissions (normally
0600).
The token is never placed in the hooks file or command arguments. The installer
checks that the capture module imports, the database schema and host are
available, and the token file is readable and nonempty. Even `--dry-run`
refuses a nonempty database WAL, because its immutable read could otherwise
see stale data; stop Core and checkpoint the WAL before retrying.

### Trust is the operator's

Review the project-local command in the Codex UI and accept it there. The Gate
checks for a `trusted_hash` record in `~/.codex/config.toml`, but treats that
record as advisory: it may be stale after a hook change. A same-turn invocation
trace is required to prove that Codex actually ran the entry. Neither the
installer nor the Gate writes a trust hash.

## P0-T07 Gate and verdict

The Gate records PASS, FAIL, or BLOCKED for the exact source commit under test.
Consult the latest Validation Run linked from the
[P0-03 PR](https://github.com/mercuryLiu163/jasmine-core-v1/pull/3) for the
current candidate verdict; after merge, use the final main-head run. The Gate
distinguishes these conditions:

| State | Verdict |
| --- | --- |
| no matching entry in the project hooks file | BLOCKED — not wired up |
| entry present, no `trusted_hash` record | BLOCKED — operator review required |
| trust record present, but no matching same-turn invocation trace | BLOCKED — hook invocation unproven |
| matching same-turn entry ran and failed to capture | **FAIL** |
| matching entry captured a fresh event, and the same event reads back after an owned Core restart | **PASS** |

Once the capture module starts, it appends a trace to
`<state-dir>/hook-invocations.log` on each invocation without prompt text. The
Gate matches the new trace to the `codex exec --json` session, turn, prompt
digest, and Core URL; checks the fresh Raw Event's ID, sequence, host, actor,
and prompt; then reads it before and after restarting the Core process it owns.
That same-turn evidence, rather than the trust record alone, determines whether
capture actually ran.

### Final operator sequence

After installing and trusting the project-local hook, run the Gate against the
existing dedicated capture database and state directory. Then run acceptance
with the Gate evidence directory as `--work` and a fresh, separate scratch
database. The acceptance bundle goes to its own `--out` directory:

```bash
export JASMINE_PYTHON=/absolute/path/to/python3.11-or-newer
"$JASMINE_PYTHON" -c 'import sys; assert sys.version_info >= (3, 11)'

scripts/p0-t07-real-conversation.sh \
  --out <gate-evidence> \
  --db <existing-dedicated-capture-db> \
  --state-dir <capture-state-dir>

"$JASMINE_PYTHON" scripts/p0-acceptance.py \
  --work <same-gate-evidence> \
  --out <acceptance-bundle> \
  --db <fresh-separate-scratch-db> \
  --executor <executor-id> \
  --reviewer <reviewer-id> \
  --verifier <verifier-id>
```

Acceptance reads the P0-T07 marker from `--work`, not `--out`. Preserve the
existing capture database for the Gate's restart readback; do not reset or
reuse it as the acceptance scratch database. Report status from the evidence
bundle for the exact commit under test, rather than from an earlier test count.
