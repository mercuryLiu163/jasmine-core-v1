# P4 handoff: long-term Memory, Hindsight adapter, durable jobs

Status: **implementation complete and locally verified; Gate NOT passed.**
P4 cannot be declared passed from this environment, and nothing here claims it.

- Branch: `p4-memory`
- Base: `850fbec` (P2-01 + P2-02 resolver + P2-03 admission merged); **schema 9**
- Candidate commits, oldest first:
  `84ed560` implementation · `8f397cf` review round 1 · `8f8297b` handoff ·
  `0b3172e` review round 2 (5 blocking + 2 secondary) · `040346b` handoff ·
  `1f25555` rebase onto `850fbec` + renumber · `c0e1da6` review round 3
  (3 P1 + 1 P2)
- Design input: `docs/Jasmine-Core-V1-P4-阶段任务书.md`, frozen in
  `docs/adr/0014-p4-memory-and-durable-jobs.md`

> SHAs above are the ones on this branch **after** rebasing onto `850fbec`. The
> pre-rebase history is preserved at tag `p4-pre-rebase-850fbec`.

## What is delivered

- `src/jasmine_core/memory/` — `types`, `tokens`, `filtering`, `scope`, `transport`,
  `hindsight`, `provenance`, `adapter`, `store`, `eligibility`, `jobs`, `worker`,
  `lifecycle`, `health`, `api` (~6.0k lines).
- `src/jasmine_core/migrations/m0009_memory.py` — P4 schema, version 9.
- `scripts/p4-memory-gate.py` — the real G01–G07 runner.
- HTTP API `/v1/memory/*`, CLI `jasmine-core memory …` and `jasmine-core memory-worker`.
- Docs: ADR 0014, `docs/p4-memory-operations.md`, `docs/api/core-api-v1.md`.

The five Memory kinds, the `PENDING/RUNNING/DONE/FAILED` job, lease/retry/backoff,
append-only attempts, crash recovery, Recall/Reflect, obsolete/delete/pin/unpin, and
the Inspector all exist and are tested. Memory is a non-authoritative projection: it
never writes a Rule, State, Evidence, Checkpoint or Task revision.

### Renumbering, and the one that is stuck

| | P4 claims now | Taken by | Can P4 move? |
|---|---|---|---|
| migration | `m0009_memory`, `VERSION = 9` | P3 PR #11: `m0009_continuity`, `m0010_context` | **not yet** |
| ADR | `0014-p4-memory-…` | P3 PR #11: 0011, 0012, 0013 | moved in `1f25555`→follow-up |

History: cut as `m0007_memory` / ADR 0008 → `m0008_memory` / ADR 0009 when
P2-01 took `m0007_interpretations` → `m0009_memory` / ADR 0011 when P2-02 took
`m0008_resolutions` and P2-02/P2-03 took ADR 0009/0010 → ADR **0014** when P3
PR #11 (open, `p3-checkpoint-context-resume`) claimed 0011–0013.

**The migration cannot jump to 11 yet, and this is enforced, not a matter of
taste.** `migrations.discover()` requires versions to be contiguous from 1; with
`m0009` and `m0010` absent, a `m0011_memory` at position 9 raises at import:

```
MigrationConflict: migration versions must be contiguous from 1: position 9 holds version 11
```

(verified in a throwaway worktree, not assumed). So P4 keeps `m0009_memory` and
`SCHEMA_VERSION = 9`, and the renumber to `m0011` / `11` happens as part of the
rebase onto P3 — which P4 has to do anyway, since its Gates need P3's Context
Pack. Until P3 merges, **two branches both hold migration 9**: whoever merges
second renumbers, and P4's note is that its renumber is mechanical rather than
optional.

Nothing P2 owns was touched: `m0007_interpretations`, `m0008_resolutions`, ADR
0008/0009/0010 are all main's.

The rebase also resolved the shared files against `850fbec` — `ids.py`
(`res`/`rvw` joined the prefix set, and P4's unused `itr` was dropped),
`models.py`, `auth.py`, `api/handlers.py`, `core-api-v1.md` and the five test
files that enumerate migrations.

## Verification actually performed

| Check | Command | Result |
|---|---|---|
| Full suite | `PYTHON=python3.13 ./scripts/run-tests.sh` | `Ran 582 tests … OK` |
| Byte-compile | `python3.13 -m compileall -q src scripts tests` | clean |
| Lint | `pyflakes 4.0.0` on all P4 src, tests, gate runner | clean |
| Gate smoke | `scripts/p4-memory-gate.py` against an unreachable engine | 7/7 `BLOCKED`, 0 `PASS`, dirty `false` |

`pyflakes` also reports one pre-existing `# noqa`-ignored import in
`tests/test_memory_cli.py` (a deliberate side-effect import for `sys.path`);
it is not a P4 file and was left alone.

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
overall BLOCKED | dirty false | engine_reachable false
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

## Independent review: five blocking findings and two secondary

Three reviewers reached the same five blockers. Each was reproduced with a
script before any code was written, so the descriptions below are observed
behaviour rather than inference.

1. **A forged provenance claim could name another project's real Event.**
   `provenance.py` checked that a referenced Raw Event *exists* and nothing
   else. An id this database had never seen was refused — but an id that was
   perfectly real and belonged to a **different** project passed into
   `Provenance.source_event_ids` and was written into this project's readable
   trace, which is a cross-project forgery with an identifier attached.
   Reproduced: a project B Event accepted as project A's source, filtered
   reasons empty, the foreign id present in the Recall payload. Now both halves
   are checked (exists *and* in scope) and yield a new `foreign_source` reason;
   `jasmine_origin_event_id`, which was trusted outright, is checked the same way.

2. **An expired lease did not surrender the job.** `status='RUNNING'` does not
   identify a claim, because an expired lease leaves the status untouched.
   Worker A claims, its lease lapses, worker B claims the same job — still
   `RUNNING`, new owner, attempt 2 — and A returns with a receipt. Reproduced:
   A's stale `fail()` drove the job to `FAILED` while B owned attempt 2. `_finish`,
   `fail` and `mark_document_deleted` now match `lease_owner` *and* `retry_count`,
   and `_assert_claim_held` runs **before** the item write so a stale worker
   cannot leave a changed engine state behind.

3. **A retain and a delete for one document could interleave.** Nothing ordered
   them, so `claim_due` offered both: the delete removed the remote document,
   the slow retain (already past its `item is ACTIVE` check) wrote it back, and
   the local item stayed a `DELETED` tombstone whose remote copy no job would
   ever remove — the only job that would have removed it had already succeeded.
   Reproduced end to end. `claim_due` now offers a `PENDING` job only when no job
   for that `memory_id` is `RUNNING`; the expired-lease reclaim branch is
   deliberately exempt, or a stranded `RUNNING` job could never be picked up again.

4. **An unconfirmed receipt was recorded as success.** The official OpenAPI
   **0.10.2** (retrieved during this fix) marks `success` and `async` *required*
   on `RetainResponse` and `success` required on `DeleteDocumentResponse`, but
   the code tested `payload.get("success") is False`, so an absent field meant
   success. Reproduced: `{}`, `{"success": null}` and
   `{"async": true, "operation_id": …}` were all accepted as a completed retain,
   and delete's `bool(payload.get("success", True))` turned an explicit `false`
   into `true`. A missing or non-boolean `success` is now `engine_protocol_error`,
   an explicit false is `engine_retain_failed` / `engine_delete_failed`, a queued
   async operation is `engine_retain_unconfirmed`; the latter two are registered
   retryable, and none can reach `DONE`.

5. **G05/G06/G07 could report PASS without their mandated evidence.** Each ran
   its checks and fell off the end of the function, so the runner recorded
   `PASS` while the Context Pack, the real process restart and the audit
   evidence their taskbook entries require had never been produced. Two shapes
   are now allowed, asserted on the parsed source by
   `test_only_g06_may_return_normally`:
   * **G05 and G07 end in `raise Blocked(...)`** naming what is missing, and both
     join `NEEDS_P3_PACK` — they prove something about an actual Context Pack, so
     they do not even start without P3.
   * **G06 returns and earns it**: both windows are now crossed by real OS
     processes. One commits the Truth and dies before scanning; one runs the real
     `MemoryWorker` whose transport wrapper `os._exit(7)`s after
     `transport.retain` returned and before `jobs.complete`, killing the process
     *inside* the retain window with the remote write done and `DONE` unwritten;
     a third claims the expired lease and finishes. Reopening a connection would
     have proved nothing: the state a crash discards only ever lived in the dead
     process. New `Gate.spawn_worker` shares a bootstrap, hands the child the
     parent's host/actor ids, and turns a wrong exit code into a `Failed`.

Reported alongside them, both confirmed and fixed:

* **Invalidate with no engine still raised `AttributeError`.** It was the only
  engine call left reaching for `self._transport` directly (`# type: ignore`
  was the tell), so it got no error code, no attempt record and no backoff —
  the job just sat `RUNNING` until its lease expired, forever. Reproduced:
  `unhandled_AttributeError`, job stuck. Now `engine_not_configured`, `FAILED`.
* **Retain replaced a Decision's origin with its approval Event.**
  `Candidate.provenance.origin_event_id` was computed *and* validated by
  eligibility, then dropped — `memory_items` had no such column — so the worker
  substituted `source_event_id`, which for a Decision is the `rule.approved`
  Event, not the Event the content came from. Every recalled Decision reported
  an approval as its origin and the real one was recorded nowhere. The column is
  added to `m0009_memory.py` (P4's own unmerged migration), the item stores it,
  and the worker and `FakeTransport` both read it back.

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

Full rationale is in the commit messages of `8f397cf` and `0b3172e` and in ADR 0014.

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
   widening — the only change that alters which memories exist.
2. The five review fixes above, each with a reproduction in the section itself.
   Items 2, 3 and 4 are the ones where a plausible-looking green result was
   possible before the fix.
3. `m0009_memory.py`: `origin_event_id` was added to P4's own *unmerged*
   migration rather than shipped as a new version — confirm no downstream has
   already applied it at schema 9 without that column. Note the migration has
   been renumbered twice and `main` currently ships `m0008_resolutions` at
   schema 8, so the integration is `m0008_resolutions` **then** `m0009_memory`
   with `SCHEMA_VERSION = 9`.
4. `Window` / the recovery cursor: confirm no source can advance the cursor past an
   Event it did not examine.
5. ACL scoping: confirm no read path can be widened by omitting a filter.
6. Confirm the Gate is still `BLOCKED`-only. It must never report `PASS` without a
   real engine, a real extractor, a real Agent session and a P3 pack — and now
   G05/G07 cannot return at all until those exist, while G06 returns only after
   real processes have died.

## Not done, deliberately

- No merge to `main`, no push, no tag: the candidate SHA is handed over instead.
- No service deployed, no user service, config, hook or secret touched.
- `run-tests.sh` left as-is despite the 3.9 trap, since it is shared P0 tooling.
