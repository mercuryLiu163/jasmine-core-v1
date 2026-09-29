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

The token is printed once and only its SHA-256 is stored. To wire the capture
entry into Codex, see `scripts/p0-t07-real-conversation.sh`: the entry must be
present in `~/.codex/hooks.json` **and** carry a `trusted_hash` in
`~/.codex/config.toml`, which only the operator can grant.

## Authorisation

The dispatcher resolves the route, authenticates, checks the scope, runs the
handler, and audits. A caller who fails at any step writes nothing at all — not
to `events`, the object tables, or `api_keys`. `last_used_at` is recorded only
after the scope check passes, so a refused caller leaves no trace beyond its
audit row.

## What the audit log does not keep

`path`, a caller-supplied `X-Request-Id`, and any `details` value are all
redacted before they are stored, because a client can put a token in any of
them. A `Bearer%20…`, `sk-…`, `ghp_…`, JWT or PEM in a URL is masked, and a
`X-Request-Id` that looks like a credential is replaced with a generated one.
Field values are length-clipped so a row cannot grow with the request body.

## Deliberately not implemented

No `PUT`/`PATCH`/`DELETE`, no Task state machine, no `expected_revision`
optimistic locking, no Evidence, no Interpreter, no Rule/Guard, no Dashboard, no
offline sync, no multi-client adapter. The capture entry translates one hook
event into one Raw Event; it is not an Agent adapter and does not interpret
anything.

## Tests

The standard-library suite and a seven-case acceptance matrix. The acceptance
runner was itself checked by mutation: removing the scope check, the audit
redaction, the revoked-key refusal, the per-write timestamp, the `after_seq`
validation, the no-replace trigger, the cross-kind triggers, the append-only
triggers, the rollback, the registry-sourced `actor_kind`, the
`recursive_triggers` pragma, and the replay conflict each turn the relevant case
FAIL. A case that crashes is recorded as FAIL with the exception and the bundle
is still written; the runner exits 0 / 1 / 2 for PASS / FAIL / BLOCKED.

## P0-T07 is BLOCKED, and that is the correct verdict

Codex only runs a hook command whose `trusted_hash` it has recorded in
`~/.codex/config.toml`. An entry added to `~/.codex/hooks.json` without one is
silently skipped, and `codex exec` has no non-interactive way to create it. The
trust decision — may this command run automatically on every prompt — belongs to
the operator, so this work did not forge a hash and did not pass
`--dangerously-bypass-hook-trust`, and a synthetic payload was not substituted
for a real conversation. What is demonstrated is that the Gate detects this
accurately: it ran a real `codex exec` turn, found zero captured events, and
reported BLOCKED rather than PASS or FAIL.
