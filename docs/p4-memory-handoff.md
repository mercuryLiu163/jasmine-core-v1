# P4 handoff: long-term Memory, Hindsight adapter, durable jobs

Status: **implementation complete and locally verified; Gate NOT passed.**
P4 cannot be declared passed from this environment, and nothing here claims it.

- Branch: `p4-memory`
- Candidate commit: `549fd721c96006d2408c6aba0deb4d2ad5aa4596` (`549fd72`)
- Base: `7ac8e94` (P2-01 merged), rebased; schema version 8
- Design input: `docs/Jasmine-Core-V1-P4-阶段任务书.md`, frozen in
  `docs/adr/0009-p4-memory-and-durable-jobs.md`

## What is delivered

- `src/jasmine_core/memory/` — `types`, `tokens`, `filtering`, `scope`, `transport`,
  `hindsight`, `provenance`, `adapter`, `store`, `eligibility`, `jobs`, `worker`,
  `lifecycle`, `health`, `api` (~6.0k lines).
- `src/jasmine_core/migrations/m0008_memory.py` — P4 schema, version 8.
- `scripts/p4-memory-gate.py` — the real G01–G07 runner.
- HTTP API `/v1/memory/*`, CLI `jasmine-core memory …` and `jasmine-core memory-worker`.
- Docs: ADR 0009, `docs/p4-memory-operations.md`, `docs/api/core-api-v1.md`.

The five Memory kinds, the `PENDING/RUNNING/DONE/FAILED` job, lease/retry/backoff,
append-only attempts, crash recovery, Recall/Reflect, obsolete/delete/pin/unpin, and
the Inspector all exist and are tested. Memory is a non-authoritative projection: it
never writes a Rule, State, Evidence, Checkpoint or Task revision.

Renumbering after rebasing onto P2-01: `m0007_memory` → `m0008_memory`,
`SCHEMA_VERSION = 8`, ADR 0008 → 0009. `m0007_interpretations` and ADR 0008 belong to
P2 and were left untouched.

## Verification actually performed

| Check | Command | Result |
|---|---|---|
| Full suite | `PYTHON=python3.13 ./scripts/run-tests.sh` | `Ran 577 tests … OK` |
| Byte-compile | `python3.13 -m compileall -q src scripts tests` | clean |
| Lint | `pyflakes 4.0.0` on all P4 src, tests, gate runner | clean |
| Gate smoke | `scripts/p4-memory-gate.py` against an unreachable engine | 7/7 `BLOCKED`, 0 `PASS` |

**`scripts/run-tests.sh` must be run with `PYTHON=python3.13`.** Its default is
`python3`, which on this machine is 3.9.6 while `pyproject.toml` requires
`>=3.11`; under 3.9 the suite produces 16 misleading tracebacks. This is a
pre-existing environment trap, not a P4 defect, and `run-tests.sh` was not changed
because it is a shared P0 file.

## Gate status: BLOCKED, with no receipts

The task book asks for 原始 receipts. **There are none, and none can be produced
here.** Each of G01–G07 needs at least one of:

- a real Hindsight deployment (no instance, and no network egress to one),
- a real LLM extractor (P2 has no interpreter wired in),
- a real Agent session and a P3 Context Pack (P3 has not landed).

The runner checks preconditions *first* and reports `BLOCKED` with the exact missing
item, never `PASS`. A smoke run against `http://127.0.0.1:1` produced:

```
P4-G01..G07  BLOCKED — the engine at http://127.0.0.1:1 did not answer:
             engine_unreachable … [Errno 61] Connection refused
overall BLOCKED | commit 549fd721… | dirty false | engine_reachable false
```

The report contains no secret material — the operator configuration references
credentials by environment-variable name only.

`FakeTransport` and `StubHindsightServer` are boundary and wire doubles. They prove
the adapter's behaviour against a contract, and they are **not** Gate evidence.

## The bug worth reviewing first

A `CONFIRMED_DECISION` recorded `origin_rule_version` read from
`rules.current_version` **at scan time**. A Decision approved at version 1 and later
superseded was therefore stored as version 2, so both memories claimed to be current,
neither was ever filtered as `superseded_decision`, and the superseded Decision could
be rendered as the live answer — the exact VC-14 failure the mechanism exists to
prevent. Since a scan runs after the fact, the version current for an Event is only
recoverable from that Event's own row in `rule_changes`, which is where eligibility
now reads it (`eligibility.py::_version_at`).

A consequence is that a `rule.approved` Event is now eligible even when the Rule is
`SUPERSEDED` or `RETIRED`, because the approval is committed history. That is only
safe because the recall filter refuses those by *status*, not just by version — a
retired Rule has not bumped `current_version`, so the version comparison alone would
have served it. Both halves are pinned by
`tests/test_memory_regressions.py::GateRunnerG02WouldNotHaveFailed`.

## Other substantive fixes in this commit

- **Recovery cursor livelock.** The cursor advanced only past Events that produced
  candidates. A window containing no eligible source never moved it, so it was read
  again forever and every eligible Event behind it was never enqueued. Since
  `step.transitioned` is a source Event type, an ordinary project fills a window with
  refused Events within a few hundred transitions. The cursor now advances past every
  Event *examined*.
- **Write lock held across a scan.** Eligibility hashed filesystem artifacts inside
  the write transaction. It no longer does.
- **One bad row voided a window.** An unverifiable Evidence reference, or a
  `CoreError` from one row, reached a blanket `except Exception` that discarded every
  other candidate in the window and left a report whose only clue was a class name.
- **Stale-worker lost update.** A worker could overwrite `RETAINED`/`DONE`; completion
  now checks `rowcount`.
- **Delete was unreachable before retain ran**, and with no engine configured every
  job raised `AttributeError` on `None` rather than a typed `TransportError`, taking
  the worker service down on its second tick.
- **Engine failure mis-reported as success**: a fully degraded recall advanced
  `last_success`.
- **ACL**: the lifecycle *write* path did not enforce ACL; an omitted filter listed
  every project; the bank filter was unchecked; filtered foreign/unverified rows
  carried `memory_id`/`document_id` into a readable trace, making the trace a
  project-enumeration oracle.
- A missing or empty `api_version` was treated as verified; a malformed engine URL
  raised a raw parse error; secret redaction missed case-varied and bare assignments.

Full rationale is in the commit message of `549fd72` and ADR 0009.

## Two shared fixtures had their own defects

Found while fixing the above, and worth knowing because they silently weakened tests:

- `MemoryTestCase.worker(transport=None)` used `self.transport if transport is None
  else transport`, so `None` — a Core with *no engine configured* — silently became
  the default fake. Every "no engine" test was testing a working engine. Replaced with
  an explicit sentinel.
- `FakeTransport.answer_for` omitted `origin_event_id`, so the double diverged from
  the worker on the retain path.

## Reviewer checklist

1. The `rule_changes` version fix and the `SUPERSEDED`/`RETIRED` eligibility
   widening — the only change here that alters which memories exist.
2. `Window` / the recovery cursor: confirm no source can advance the cursor past an
   Event it did not examine.
3. ACL scoping: confirm no read path can be widened by omitting a filter.
4. Confirm the Gate is still `BLOCKED`-only. It must never report `PASS` without a
   real engine, a real extractor, a real Agent session and a P3 pack.

## Not done, deliberately

- No merge to `main`, no push, no tag: the candidate SHA is handed over instead.
- No service deployed, no user service, config, hook or secret touched.
- `run-tests.sh` left as-is despite the 3.9 trap, since it is shared P0 tooling.
