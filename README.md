# Jasmine Core V1

Private repository for the Jasmine Core V1 implementation and validation.

## Status

P0 Baseline is in progress. The table below lists only what has actually been
implemented **and** exercised; anything not listed is not implemented.

| Capability | P0 state |
| --- | --- |
| Public ID contract, package boundary | implemented (ADR 0001) |
| `core.db` versioned schema v2, WAL, empty-database migration | implemented (ADR 0001) |
| Transaction / `revision` / atomicity contract | implemented (ADR 0002); the `expected_revision` update path is P1 |
| Core API, error and idempotency contract | implemented ([ADR 0003](docs/adr/0003-core-api-errors-and-idempotency.md), [API doc](docs/api/core-api-v1.md)) |
| Raw text retention, bearer auth, append-only audit | implemented (ADR 0004) |
| Immutable idempotent Event Store, project/task/session | implemented ([P0-02](docs/p0-02-event-store.md)) |
| Codex `UserPromptSubmit` capture entry | implemented, but **not registered with Codex** — see below |
| Minimal Validation Recorder and the P0 acceptance matrix | implemented ([P0-03](docs/p0-03-api-and-capture.md)) |
| Real-conversation Gate (P0-T07) | **BLOCKED**: the capture entry is not installed in `~/.codex/hooks.json` |

Not implemented in P0 and not claimed anywhere in this repository: Rule/Guard,
the full Task state machine, Evidence sufficiency, LLM Interpreter/Resolver,
Context Pack/Hindsight, the full Agent lifecycle adapter, offline sync,
Dashboard, and the 30-scenario acceptance suite.

## Planning documents

- [Multi-stage PR plan](docs/Jasmine-Core-V1-多阶段PR计划.md)
- [P0 Baseline implementation and validation taskbook](docs/Jasmine-Core-V1-P0-阶段任务书.md)

The design documents are planning inputs and are not proof of implemented
features.

## ADRs

- [ADR 0001 — public IDs, schema version, package boundary](docs/adr/0001-public-id-schema-and-package-boundary.md)
- [ADR 0002 — transactions, revision, atomicity](docs/adr/0002-transaction-revision-and-atomicity.md)
- [ADR 0003 — Core API, errors, idempotency](docs/adr/0003-core-api-errors-and-idempotency.md)
- [ADR 0004 — raw text, authn, audit](docs/adr/0004-raw-text-authn-and-audit.md)

## Getting started

Requires Python 3.11+ and no third-party packages.

```bash
export JASMINE_CORE_DB="$HOME/.local/share/jasmine-core/core.db"
PYTHONPATH=src python3 -m jasmine_core.cli migrate
PYTHONPATH=src python3 -m jasmine_core.cli schema
./scripts/run-tests.sh          # PYTHON=/path/to/python3 to pick an interpreter
```

## V0 boundary

The existing Jasmine Memory V0 workspace (`jasmine_memory`, its `codex_hook.py`
and its state directory) is a separate product. V1 does not import it, reuse its
identifiers, or read its tables. V0's existing hooks are candidates for
migration assessment only; nothing V0 has implemented counts towards V1
acceptance.

Runtime databases, tokens, raw logs, artifacts, and validation bundles must not
be committed; see `.gitignore`.
