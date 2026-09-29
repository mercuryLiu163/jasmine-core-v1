# Jasmine Core V1

Private repository for the Jasmine Core V1 implementation and validation.

## Status

The table below lists only what has actually been implemented **and** exercised;
anything not listed is not implemented. The P0 stage verdict comes from the
latest commit-bound Validation Run linked from the [P0-03 PR](https://github.com/mercuryLiu163/jasmine-core-v1/pull/3),
followed by final main-head validation after merge. A passing CI matrix alone
does not establish the stage verdict.

| Capability | Current state |
| --- | --- |
| Public ID contract, package boundary | implemented (ADR 0001) |
| `core.db` versioned schema v5, WAL, forward-only migration | implemented (ADRs 0001–0007) |
| Transaction / `revision` / atomicity contract | implemented (ADRs 0002, 0006) |
| Core API, error and idempotency contract | implemented ([ADR 0003](docs/adr/0003-core-api-errors-and-idempotency.md), [API doc](docs/api/core-api-v1.md)) |
| Raw text retention, bearer auth, append-only audit | implemented (ADR 0004) |
| Immutable idempotent Event Store, project/task/session | implemented ([P0-02](docs/p0-02-event-store.md)) |
| Codex `UserPromptSubmit` capture entry | implemented; project-local registration is described in [P0-03](docs/p0-03-api-and-capture.md), and runtime invocation is verified by P0-T07 evidence |
| Minimal Validation Recorder and the P0 acceptance matrix | implemented ([P0-03](docs/p0-03-api-and-capture.md)) |
| Real-conversation Gate (P0-T07) | verdict comes from the latest commit-bound Validation Run linked from the [P0-03 PR](https://github.com/mercuryLiu163/jasmine-core-v1/pull/3) |

P1-01 adds versioned Authority and deterministic advisory Guard
([ADR 0005](docs/adr/0005-p1-authority-and-guard.md)). P1-02 adds Task/Step
revision transitions and fail-closed acceptance
([ADR 0006](docs/adr/0006-task-step-state-and-evidence-boundaries.md)). P1-03
provides an Evidence/fingerprint implementation and project-local Codex hook
candidate ([ADR 0007](docs/adr/0007-p1-evidence-fingerprint-and-codex-hook.md)).
Component tests and synthetic hook calls do not establish real native denial.
P1-T10 requires a separate trusted real Codex conversation; until that Gate is
run and reviewed, native enforcement coverage is unverified. LLM
Interpreter/Resolver, Context Pack/Hindsight, the full Agent lifecycle
adapter, offline sync, Dashboard, and the 30-scenario acceptance suite remain
outside P1.

## Planning documents

- [Multi-stage PR plan](docs/Jasmine-Core-V1-多阶段PR计划.md)
- [P0 Baseline implementation and validation taskbook](docs/Jasmine-Core-V1-P0-阶段任务书.md)
- [P1 Truth Core implementation and validation taskbook](docs/Jasmine-Core-V1-P1-阶段任务书.md)

The design documents are planning inputs and are not proof of implemented
features.

## ADRs

- [ADR 0001 — public IDs, schema version, package boundary](docs/adr/0001-public-id-schema-and-package-boundary.md)
- [ADR 0002 — transactions, revision, atomicity](docs/adr/0002-transaction-revision-and-atomicity.md)
- [ADR 0003 — Core API, errors, idempotency](docs/adr/0003-core-api-errors-and-idempotency.md)
- [ADR 0004 — raw text, authn, audit](docs/adr/0004-raw-text-authn-and-audit.md)
- [ADR 0005 — Authority and Guard](docs/adr/0005-p1-authority-and-guard.md)
- [ADR 0006 — Task and Step state](docs/adr/0006-task-step-state-and-evidence-boundaries.md)
- [ADR 0007 — Evidence, fingerprint and bounded Codex hooks](docs/adr/0007-p1-evidence-fingerprint-and-codex-hook.md)

## Getting started

Requires Python 3.11+ and no third-party packages.

```bash
export JASMINE_CORE_DB="$HOME/.local/share/jasmine-core/core.db"
PYTHONPATH=src python3 -m jasmine_core.cli migrate
PYTHONPATH=src python3 -m jasmine_core.cli schema
./scripts/run-tests.sh          # PYTHON=/path/to/python3 to pick an interpreter
```

P1 component acceptance uses a new scratch workspace and database per case:

```bash
python3 scripts/p1-acceptance.py --out /private/tmp/p1-component-records \
  --work /private/tmp/p1-component-work
```

For the real P1-T10 Gate, prepare a separate nonsensitive fixture and
reviewable binding:

```bash
python3 scripts/p1-real-conversation.py --prepare \
  --out /absolute/private/run-directory
```

The coordinator then reviews the
project-local installer dry run, installs its entries from a stable checkout,
and obtains Codex project trust from the user. After that explicit trust step,
run:

```bash
python3 scripts/p1-real-conversation.py --run \
  --out /absolute/private/run-directory --user-reviewed-trust
```

This can exercise an actual
Codex session and verify its native hook traces. `--run` without that trust
returns a distinct BLOCKED result; it cannot be replaced by a component
simulation. The coordinator removes or restores the temporary P1 hook entries
and stops the scratch Core after the Gate. The installer never changes global
Codex configuration or trust.

## V0 boundary

The retired Jasmine Memory V0 workspace is historical reference only. V1 does
not import it, reuse its identifiers, read its tables, or reconnect its hooks.

Runtime databases, tokens, raw logs, artifacts, and validation bundles must not
be committed; see `.gitignore`.
