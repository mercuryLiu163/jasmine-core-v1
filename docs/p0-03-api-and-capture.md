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

## Wiring the capture entry into Codex

```bash
scripts/p0-codex-hook-install.sh --dry-run     # validate and preview
scripts/p0-codex-hook-install.sh              # write <repo>/.codex/hooks.json
```

The entry is **project-local on purpose**. `UserPromptSubmit` has no tool to
match on, so Codex ignores a `matcher` on that event: a *globally* installed
entry runs on every prompt in every project on the machine. Scoping comes from
where the hooks file lives, and the installer writes only
`<repo>/.codex/hooks.json` — it reads `~/.codex/hooks.json` to show what is
there and never modifies it. A stale entry is reported and held rather than
silently replaced, the file is backed up, and re-running is a no-op.

The command it writes names **paths only**: no token, no interpreter path, no
`PYTHONPATH`. Those are resolved at run time by
`scripts/jasmine-capture-hook.sh`, so the token stays in a 0600 file, never in a
hooks file and never in this repository. The installer validates the interpreter
*version* (not just its existence — macOS ships 3.9, below the 3.11 floor), that
the capture module imports from this checkout, that the database and a host
exist, and that a token is readable, before it writes anything.

### Trust is the operator's

Codex runs a hook command only once it has recorded a `trusted_hash` for it in
`~/.codex/config.toml`. An untrusted entry is skipped **silently**, and
`codex exec` cannot create the hash non-interactively. This work did not forge a
hash, did not pass `--dangerously-bypass-hook-trust`, and did not touch
`~/.codex/config.toml`. Review the command in the Codex UI and accept it.

## P0-T07 is BLOCKED, and that is the correct verdict

The Gate tells three states apart, and BLOCKED is not a single one:

| State | Verdict |
| --- | --- |
| no entry in the hooks file | BLOCKED — not wired up |
| entry present, no `trusted_hash` in `config.toml` | BLOCKED — registered, not trusted |
| entry trusted, but the entry's own invocation trace gained no line | BLOCKED — never invoked |
| the entry ran and did not capture | **FAIL** — the product |
| the entry ran, captured, and the text reads back after a restart | **PASS** |

The third row is why the capture entry writes a line to
`$JASMINE_CORE_STATE_DIR/hook-invocations.log` on *every* path, with no prompt
text. Without it, "Codex never ran us" and "the Core was down" look identical,
and the Gate would blame the product for a setup problem. A `FAIL` is only
reachable once the entry is demonstrably running.

What is demonstrated here: the entry is installed and trusted-state is read
correctly, the wiring captures a real-shaped payload end to end when invoked the
way Codex invokes it, and the Gate reports BLOCKED — naming the exact
`trusted_hash` key that is missing — rather than PASS or FAIL. No synthetic
payload was ever substituted for a real conversation.
