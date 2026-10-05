# P4 handoff: long-term Memory, Hindsight adapter, durable jobs

Status: **implementation candidate; fixed-list review partial; full P4 acceptance BLOCKED.**
P4 cannot be declared passed from this environment, and nothing here claims it.

- Branch: `p4-memory`
- Base: `850fbec` (P2-01 + P2-02 resolver + P2-03 admission merged); **schema 11**
- Independently reviewed candidate: `eeba6af24d5e84b8f96bd106f0d07bb6d733edb5`.
- Candidate commits, oldest first:
  `84ed560` implementation · `8f397cf` review round 1 · `8f8297b` handoff ·
  `0b3172e` review round 2 (5 blocking + 2 secondary) · `040346b` handoff ·
  `1f25555` rebase onto `850fbec` + renumber · `c0e1da6` review round 3
  (3 P1 + 1 P2) · `5e91cf2` ADR 0014 · `7fabffd` durable take-back work ·
  `39e0a8c` take-back re-arm/read-back · `b59a31a` monotonic claim generation ·
  `1abbaae` document pagination and identity · `b74bd76` unreadable-page refusal ·
  `eeba6af` full b74bd76 review fixes (one P1 and eight P2s).
- Design input: `docs/Jasmine-Core-V1-P4-阶段任务书.md`, frozen in
  `docs/adr/0014-p4-memory-and-durable-jobs.md`

> SHAs above are the ones on this branch **after** rebasing onto `850fbec`. The
> pre-rebase history is preserved at tag `p4-pre-rebase-850fbec`.

## Fixed-list review closure

The independent fixed-list review of `eeba6af` found the following local status.
Closed local findings do not constitute real Gate acceptance.

| Finding | Status | Remaining boundary |
|---|---|---|
| F01 | closed locally | Both collections validate `total`, the `offset` asked for, the `limit` echoed, and that the rows fit inside both; a `limit` of 0 with rows outstanding cannot be paged and is refused rather than read as coverage. |
| F02 | closed locally | Local review scope only. |
| F03 | closed locally | The unit listing is driven by `total` through one shared validator, not by page length; every entry must be an object with an `id`, duplicates are refused across pages, and a unit or document naming another owner is refused, so no entry can be silently skipped and then reported as done. `list_all_document_memories` is the only completeness call, and the fake and the wire stub paginate for real. |
| F04 | closed locally | Every response, traces included, passes through one walker; a trace row carries `safe_error_summary` beside its `error_code`, and `traces()` had also returned the raw `banks_json` string alongside the parsed `banks`, which the walker cannot see into. Rows now carry the parsed form or the raw one, never both. |
| F05 | closed locally | Local review scope only. |
| F06 | closed locally | Local review scope only. |
| F07 | closed locally | `ReflectOutcome.safe_error_summary` is assigned beside `error_code`, and both flow into the trace row. |
| F08 | closed locally | Local review scope only. |
| F09 | closed locally | Local review scope only. |

The complete review report will be saved in the root repository at
`artifacts/p4-review-20261003/P4-REVIEW-eeba6af/P4-REVIEW.md`.

## What is delivered

- `src/jasmine_core/memory/` — `types`, `tokens`, `filtering`, `scope`, `transport`,
  `hindsight`, `provenance`, `adapter`, `store`, `eligibility`, `jobs`, `worker`,
  `lifecycle`, `health`, `api` (~6.0k lines).
- `src/jasmine_core/migrations/m0011_memory.py` — P4 schema, version 11.
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
| migration | `m0011_memory`, `VERSION = 11` | P3 PR #11: `m0009_continuity`, `m0010_context` | **merged on `p4-integration`** |
| ADR | `0014-p4-memory-…` | P3 PR #11: 0011, 0012, 0013 | moved in `1f25555`→follow-up |

History: cut as `m0007_memory` / ADR 0008 → `m0008_memory` / ADR 0009 when
P2-01 took `m0007_interpretations` and P4 renumbered to `m0011_memory` / ADR 0014 when P2-02 took
`m0008_resolutions` and P2-02/P2-03 took ADR 0009/0010 → ADR **0014** when P3
PR #11 (open, `p3-checkpoint-context-resume`) claimed 0011–0013.

**The migration cannot jump to 11 yet, and this is enforced, not a matter of
taste.** `migrations.discover()` requires versions to be contiguous from 1; with
`m0009` and `m0010` absent, a `m0011_memory` at position 9 raises at import:

```
MigrationConflict: migration versions must be contiguous from 1: position 9 holds version 11
```

(verified in a throwaway worktree, not assumed). So P4 now ships `m0011_memory` and
`SCHEMA_VERSION = 11`, and the renumber to `m0011` / `11` happens as part of the
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

## P3 integration state

`origin/main` is still `850fbec` and P3's PR #11 (`p3-checkpoint-context-resume`) is
still **open**, but the integration is done on `p4-integration`: P3 `7016dab` is
merged and the renumber has happened (`m0009_memory` → `m0011_memory`,
`SCHEMA_VERSION` 9 → 11). It could not happen before, because
`migrations.discover()` requires versions contiguous from 1 — with `m0009`/`m0010`
absent a `m0011` at position 9 raised `MigrationConflict` at import. Merging into
`main` is still P3's PR to land; that renumber is mechanical and belongs
to the rebase onto P3.

What was done against the published P3 branch:

* **Bank ids reconciled.** P3's Memory provider validates `jasmine-global` and
  `jasmine-project-{prj}` exactly, so `JASMINE_MEMORY_BANK_NAMESPACE` now applies
  at the **transport boundary** (`scope.engine_bank_id`) and never to
  `BankRef.bank_id`. Two runs are isolated because their *engine* banks differ;
  Core's ids stay canonical, which is what P3 is handed. A wire-level test drives
  the real transport against the stub to show both halves at once.
* **The worker exists**: `scripts/p4-memory-worker.py`, driven in tests exactly
  the way P3 drives it — a real subprocess, the identity environment, one JSON
  query on stdin, one JSON result on stdout — and checked against the rules P3's
  validator enforces (exact key sets, bank echo, item cap, fact/Evidence
  equalities, digest recomputation).

P3's query carries no acting identity, so the worker takes the actor from
`JASMINE_MEMORY_ACTOR_ID` and degrades without it rather than inventing one.

`VERIFIED_OUTCOME` is now emitted as well. It was deferred while P1's
`verified_transition` lived only in P3's branch; that check turned out to be a
*verification* P3 performs on the Event the item cites, not a proof the worker has
to produce — so the worker cites the `EXECUTED -> VERIFIED` transition and P3
re-derives the rest. Confirmed against P3's own code: `verified_transition`
accepts the transition P4's writer produces, and `filter_memory` certifies the
item with `verified_transition_event_id` set to that Event and no filtering. A
`PASS` result with no verifying transition is still classified as a verified
outcome by P4 — that is the honest reading of the Evidence — but it is not sent,
because nothing verified it.

## Verification actually performed

The independent run below used the clean candidate
`eeba6af24d5e84b8f96bd106f0d07bb6d733edb5`, schema 11, and Python **3.13.15**.
The handoff and test-runner follow-up edits were made only after that run, so
these results describe the committed candidate rather than the subsequent dirty
working tree.

| Check | Command | Result |
|---|---|---|
| Full suite | `PYTHONDONTWRITEBYTECODE=1 PYTHON=python3.13 ./scripts/run-tests.sh` | `Ran 692 tests in 20.247s … OK`; exit 0; no skips |
| Byte-compile | `PYTHONPYCACHEPREFIX=/tmp/p4-review-eeba6af-pycache python3.13 -B -m compileall -q src scripts tests` | clean; exit 0 |
| Lint | previous implementation report: `pyflakes 4.0.0` on P4 src/tests/runner | not independently reproduced for this candidate; the available runtime lacks pyflakes, and no dependency was installed |
| Gate smoke | `python3.13 -B scripts/p4-memory-gate.py` against `http://127.0.0.1:1` | 7/7 `BLOCKED`, 0 `PASS`, exit 2, dirty `false` |

The full-suite log is `/tmp/p4-review-eeba6af-suite.log`. The Gate smoke manifest
is `/private/var/folders/bd/n1gc6pb14cx4ghrgfm1x2xfw0000gn/T/p4-review-eeba6af-gate-nz2_mdsv/out/p4-gate-1791093435.json`.
These are local, temporary verification artifacts; the unreachable-engine smoke
is not a real Gate receipt.

`scripts/run-tests.sh` now checks **Python >=3.11** before unittest discovery,
matching `pyproject.toml`. Its default is still `python3`; on this machine that
selects 3.9.6, so it now exits 2 with one version message instead of misleading
import tracebacks. Set `PYTHON` to a supported interpreter such as `python3.13`.

## Gate status: BLOCKED, with no receipts

The task book asks for 原始 receipts. **There are none, and none can be produced
here.** The missing dependencies across G01–G07 are:

- a real Hindsight deployment and its fact-extraction LLM (none supplied here),
- the P2 eligible candidate collector and any real input/extractor chain required by the taskbook,
- a real Agent session and a P3 Context Pack (P3 has not landed).

G06 can be evaluated independently with a real Hindsight deployment and committed
Authority sources; it does not need P3 Context or a real Agent conversation.
G01–G05 and G07 still end in `Blocked` because the actual Agent/pack acceptance
path is not wired. Declaring dependency commits in configuration does not complete
that path. Subset and `--allow-dirty` runs are explicitly ineligible for stage
acceptance, and the manifest records scope, reasons and the actual bank namespace.

The runner checks preconditions *first* and reports `BLOCKED` with the exact missing
item. A smoke run against `http://127.0.0.1:1` produced:

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
   `fail` and `mark_document_deleted` now match `lease_owner` and the monotonic
   `claim_generation`. Retry budget (`retry_count`) can reset, so it is not a
   fencing token. Each successful claim advances the generation. Re-arming clears the lease
   and returns the job to PENDING, so the old claim cannot commit; the next claim
   gets a new generation. A stale claim cannot become valid again after a manual
   retry or owner reuse.
   `_assert_claim_held` runs **before** the item write so a stale worker cannot
   leave a changed engine state behind.

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
  added to `m0011_memory.py` (P4's own unmerged migration), the item stores it,
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
3. `m0011_memory.py`: `origin_event_id` was added to P4's own *unmerged*
   migration rather than shipped as a new version — confirm no downstream has
   already applied it without that column. The migration has now been renumbered
   four times (`m0007` → `m0008` → `m0009` → `m0011`), each time because another
   phase claimed the slot first, so the applied-order list to check against a real
   database is `… m0008_resolutions, m0009_continuity, m0010_context,
   m0011_memory` with `SCHEMA_VERSION = 11`.
4. `Window` / the recovery cursor: confirm no source can advance the cursor past an
   Event it did not examine.
5. ACL scoping: confirm no read path can be widened by omitting a filter.
6. Confirm stage PASS requires every real Gate on a clean candidate. Subset and
   development runs cannot pass the stage. G01–G05/G07 still require the actual
   Agent/Context evidence path; G06 can pass independently only after real worker
   process crash/restart and real engine read-back.

## Not done, deliberately

- No merge to `main`, no push, no tag: the candidate SHA is handed over instead.
- No service deployed, no user service, config, hook or secret touched.
- The authorized follow-up edits update this handoff and add the shared test-runner
  version guard; they are uncommitted and were made after clean-candidate validation.

## Historical source audit and new execution candidate (2026-10-05)

Read-only audit of the original `/tmp/valid-p4/v1/work/P4-G01/core.db`
(`op.json` run `p4-acc5-1791222589`, excluding all tamper copies) found the
historical Task's sole FAIL Evidence `evd_01M46JZAC1Q35YM2TXH9B1QNX0`.
Its source `evt_EEG9ZQP6MWM9B7A446NSPW5Z5H` contains only
`tool_response: {exit_code: 1}`, and its artifact is `workspace:/source.txt`.
There is no failing assertion, error-to-file association, executed repair, or
post-repair validation behind that historical lesson. The earlier handwritten
callback reason cannot establish the failure's cause. Later triage Tasks contain
more outputs, but their observations are not the historical Task's chain and
cannot repair its provenance. Earlier producer commands were reported independently
as handwritten rather than real Agent execution; no old PASS is adopted here.

Fresh G01 now requires the actual historical execution configuration documented
in operations. Source association has no same-tool fallback: Evidence names the
result Event, that result names its exact call, and Task/turn/tool-use/tool-name
and command hash must agree. The worker emits only locally sourced observations;
it does not append engine prose that could invent a cause. Output artifacts now
include Task and output hash, preserving FAIL and PASS from the same command.

The added targeted regression executes an actual failing unittest fixture,
performs file edits with actual hash/diff output, then executes post-edit unittest.
It verifies all four Evidence IDs and eight source Events survive the summary.
The regression intentionally has no real engine, so it proves execution/capture
and mapping only, not retention or formal Gate acceptance. Independent review,
real engine retention, all seven Gates and repeated resume at one clean frozen
commit remain required. Nothing was pushed, merged to main, or submitted to CI.

Implementation verification: `PYTHONDONTWRITEBYTECODE=1 PYTHON=python3.13
./scripts/run-tests.sh` ran 1028 tests, OK with 10 skips (31.391s), raw log
`/tmp/p4-history-candidate-tests.log`. Final targeted checks ran 55 tests with no
skips, including exact command/source linkage, output digest binding, separate
FAIL/PASS artifacts and real failure/edit/validation admission/rendering. These
checks do not certify the skipped native/deployment paths or formal Gates.


Independent review of candidate `3321100` returned **FAIL** with two P1 findings:
missing stdout/digest could admit unbound stderr/output, and task/sequence/status
checks alone could label `echo` actions as repair and validation. The follow-up
requires stdout with its matching Evidence artifact hash, renders no other output
field, and checks integer exit/status consistency. Historical chains now require
the exact fixed capture programs, captured file content/hash/diff consistency,
and identical failure/validation commands with exit-zero/PASS validation. Missing
proof leaves only the original historical observation with repair and validation
explicitly unestablished. Negative regressions cover both review reproductions
and incorrect diff/hash, unchanged files, changed rerun commands and wrong exits.

The original 800-token Memory allocation is preserved; abbreviated sourced
observations retain all source IDs and inline affected filenames and attempted
formula/test changes. Pinned-tokenizer rendering must still be independently
verified at the final candidate. The prior 1028-suite results describe earlier
candidates, not an assertion that the final follow-up was already fully tested.

Follow-up implementation checks: production interpreter
`.../opencode/venv-p4/bin/python`, pinned cache `.../opencode/tiktoken-cache`,
`PYTHONPATH=src:tests`, and unittest modules `tests.test_p4_fix_round`,
`tests.test_p4_second_fix`, `tests.test_context` ran **89 tests, OK, no skips**
(4.220s). Raw log: `/tmp/p4-history-review-fix-targeted.log`. The actual
pinned-tokenizer regression confirms the new four-action summary renders with
all eight source Events and four Evidence IDs under the original 800-token
allocation. Full suite and formal Gate acceptance remain the independent
validator's responsibility at the final frozen commit.

## Terminal partial-failure replay follow-up

The real independent `cdfa5e7` run in
`evidence/p4-g01-3f061ba-20261005-112558` failed while the existing Hindsight
backend could not retain/recover. G01's historical execution completed but
retention failed; G05's original tracked jobs stayed pending. Its first and
second resumes then created extra setup projects: G01 hit P3's correctly enforced
idempotency conflict, and G05 hit the correctly enforced write-once receipt
refusal. All original failed manifests and Core rows remain evidence; they are
not replaced, and G01's actual controlled pair was NOT RUN, with benefit
unassessed. Read-only details and original stable IDs are in that run's
`RESUME-FINDINGS.md`.

The narrowly scoped fix adopts terminal failure replay for G01/G05 partial
setups. Phase one seals the original CaseRecord; repeated resume verifies its
receipt/source identities and returns the same FAIL before setup or engine work.
It never adds another project, Task, Rule, Evidence, job, historical execution or
Agent pair. Completed setup still takes its existing exact-pack verifier. Old
partial failures without a sealed terminal record fail explicitly rather than
re-seeding. No service was restored or restarted as part of this work.

G01's four action receipts and execution-chain receipt are now registered before
retention may fail and therefore participate in that attempt's phase seal. This
repairs coverage for new runs without claiming old omitted receipts were sealed.
New regressions use actual historical unittest/edit/verification subprocesses
and actual refused loopback retention, then replay twice: original identifiers,
SQLite object counts and bytes, receipt bytes and phase seals stay unchanged,
and each replay remains FAIL. G05 uses actual refused engine-job execution to
check the same property for its Rule and original jobs. Missing/tampered receipts,
foreign source scope and an unsealed legacy terminal record are refused.
This is verification of failed-attempt replay, not recovered Gate acceptance.

An independent budget probe on `cdfa5e7` also found a blocker: with real random
Core memory IDs, the earlier summary exceeded the unchanged 800-token allocation
(814–820 tokens) and was omitted. Probe evidence is preserved in
`evidence/p4-budget-id-probe-cdfa5e7/results.json`. The follow-up removes repeated
wording and unchanged assertion arguments; it keeps each source/Evidence pointer
once in the text, the original command/Task/memory identity, the observed short
assertion, affected filenames, the verified formula/28.5 expectation, the exact
rerun's OK observation, and the current/exclusive-cause limitation. The outer
P3 renderer and its 800-token allocation remain unchanged.

Latest implementation checks used the existing production Python and pinned
cache with `PYTHONPATH=src:tests`: modules `tests.test_p4_partial_failure_resume`,
`tests.test_p4_fix_round`, `tests.test_p4_second_fix`, `tests.test_context` ran
**94 tests, OK, no skips** (4.175s), log
`/tmp/p4-terminal-replay-targeted.log`. The budget regression starts with an
actual stored Core-generated memory ID plus sixteen additional Core-generated
random identities, checks every identity without selection, and asserts full
source/Evidence delivery and a complete pinned-tokenizer Memory block at most
760 tokens (at least 40 tokens below the unchanged 800 cap). The additional
identities are component stress inputs; the referenced execution sources and
Evidence are the actual captured chain. These regressions do not represent
native Agent samples or a completed G01 pair. Independent final review,
fullsuite, seven Gates and repeated resume remain outstanding.
