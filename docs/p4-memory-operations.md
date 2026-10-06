# P4 operations: long-term Memory

Operational notes for the Memory subsystem. Read
[ADR 0014](adr/0014-p4-memory-and-durable-jobs.md) for why; this file is what
to type, and what to believe when you read the output.

Everything here is a *core* operation. `jasmine-core` never writes a job or a
Memory row directly; the CLI calls the same service layer the API does.

## What has to be true before Memory does anything

| Requirement | If it is missing |
| --- | --- |
| `JASMINE_HINDSIGHT_URL` on the **worker**, not on the API server | jobs go to `FAILED` with `engine_not_configured`; `/v1/health` stays `ok` |
| `jasmine-core memory-worker` running | jobs accumulate as `PENDING`; Rules, State, Evidence and Checkpoints are unaffected |
| `JASMINE_HINDSIGHT_API_KEY` only if the deployment validates one | Hindsight 0.10.2 declares no `securitySchemes`; a single-user deployment needs no credential |
| `JASMINE_CORE_WORKSPACE_ROOT` for `VERIFIED_OUTCOME` | an Evidence reference cannot be verified, so nothing is retained as a verified outcome |

The API server needs the engine URL only to *read* (a Context Pack recall). The
worker needs it to *write*. In a single-process dev setup, set it for both.

```bash
export JASMINE_HINDSIGHT_URL=http://127.0.0.1:8888
export JASMINE_HINDSIGHT_TIMEOUT_SECONDS=20
export JASMINE_CORE_DB="$HOME/Library/Application Support/Jasmine/core/core.db"
```

The API key is read from the environment and never from `argv`, a request body or
a database row, and it is never logged or returned — not by
`jasmine-core memory health`, not by `GET /v1/memory/health`, and not in a Gate
manifest. If a secret ever appears in one of those, that is a defect, not a
formatting quirk.

## Normal operation

```bash
# One pass: scan committed truth, then claim and run at most one job.
jasmine-core memory-worker --max-ticks 1

# The service. Runs until SIGINT/SIGTERM; an in-flight tick finishes first and
# the current lease is left to expire rather than abandoned mid-retain.
jasmine-core memory-worker

# Is Memory working? This does not contact the engine.
jasmine-core memory health

# Is the *engine* working? This does.
jasmine-core memory health --probe
```

`memory health` reports one of `ok`, `idle`, `degraded`, `failing`, `unreachable`
or `unconfigured`, plus `core_impact`. Read `core_impact` first:

| `core_impact` | Meaning |
| --- | --- |
| `none` | Memory is fine, idle, or switched off. The Core is unaffected. |
| `memory_paused` | long-term Memory is paused. Rules, State, Evidence, Checkpoint and Guard all still work. |

`unconfigured` is not a failure. A Core with no Hindsight is a working Core.

Two fields deserve particular attention:

* `last_success_at` advances only when a retain, recall, invalidate or delete
  actually completed. `/version` answering 200 does not advance it, so a
  reachable engine with a failing queue shows `reachable: true` and
  `jobs.failed > 0`. Trust the second one.
* `last_error_code` is the branchable value. `safe_error_summary` is redacted and
  clipped and is safe to paste into a report; a raw engine body is not, and is
  never returned.

## When the queue is stuck

```bash
jasmine-core memory jobs --status PENDING
jasmine-core memory jobs --status FAILED
jasmine-core memory attempts <job_id>      # the full immutable history
```

`memory attempts` is the record to read before doing anything. `last_error_code`
on the job is a summary that gets overwritten; the attempt history is append-only
and is why it exists.

A `FAILED` job is requeued by an authorised operator, not by a retry loop:

```bash
curl -X POST "$CORE/v1/memory/jobs/$JOB/retry" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"host_id\": \"$HOST_ID\"}"
```

`memory:manage` and a human or system actor are required. A retry may not change
the document, the content hash or the bank; a `DONE` job is refused; a `RUNNING`
job is refused rather than stolen from its worker. An `event_id` makes the command
idempotent, so a retried request is a replay rather than a second transition.

Fix the cause first. A retry against an engine that is still refusing the document
just produces a second attempt with the same failure.

## Recovery

A job is a projection of committed Events, so recovery is a re-scan rather than a
replay of a lost queue.

```bash
jasmine-core memory scan             # eligible committed sources with no job
jasmine-core memory reset-cursor --seq 0   # re-scan the entire Event stream
```

A full replay is safe: the unique index on `(source_event_id, kind)` turns every
already-known source into a no-op, so a replay re-derives nothing and loses
nothing. This is also the recovery path for the window the design does not draw —
a process that died after a State transaction committed and before the job row
existed.

To remove an item from the queue without retaining it, obsolete it (below) and let
the worker pass over it, or `delete` it. There is no "discard" that leaves the
item recallable.

## Curating what the engine holds

```bash
M=mem_01K7...
curl -X POST "$CORE/v1/memory/items/$M/pin"      -d "{\"expected_revision\": 1, \"host_id\": \"$HOST_ID\"}"
curl -X POST "$CORE/v1/memory/items/$M/obsolete" -d "{\"expected_revision\": 1, \"host_id\": \"$HOST_ID\", \"reason\": \"wrong lesson\"}"
curl -X POST "$CORE/v1/memory/items/$M/delete"    -d "{\"expected_revision\": 2, \"host_id\": \"$HOST_ID\", \"reason\": \"user asked\"}"
```

What each one actually does, and what it does not:

* **pin / unpin** — Inspector ordering and nothing else. It cannot make an item
  recallable, cannot resurrect an obsolete or deleted one, cannot widen the
  Context Pack's Memory budget, and cannot make an item load as Authority.
* **obsolete** — the item stops being recalled and stops entering a Context Pack
  *immediately*. All metadata and provenance stay, every Raw Event and Evidence it
  points at stays, and the item stays visible in the Inspector. A queued job
  soft-retires the engine's memory units; the engine keeps them for audit and can
  revert. Reversible by re-retaining from the source.
* **delete** — the item is excluded from recall immediately and a tombstone row
  stays with its provenance, while a queued job removes the remote document. Check
  `engine_claim` on the item before telling anyone it is gone: the local exclusion
  is immediate and the remote removal is queued, and a failed removal leaves
  `engine_state: DELETE_FAILED` with the item still excluded. A tombstone is
  terminal; re-retaining the same source creates a new item.

Every command appends an Event with the actor, the source, `before` and the
declared `after`, and takes `expected_revision` so two operators cannot silently
overwrite each other. A rejected command writes nothing at all.

## What P4 will not do

* It will not write `jasmine-global` from a project-scoped source. Only a source
  whose own committed scope is global can reach the global bank.
* It will not retain a memory type without its committed proof: a Decision needs
  an ACTIVE Authority Decision, a verified outcome needs `PASS` Evidence whose
  artifact still verifies, a failure lesson needs a real `FAIL`.
* It will not retain content that is mostly a credential. The Raw Event keeps the
  original text under `events:read`; the engine does not get it.
* It will not render a recalled item whose text tries to instruct the reader, or
  that claims to be a Context Pack section.
* It will not render a memory of a Decision that Authority has since superseded or
  retired. The memory stays in the trace and the Inspector; it does not become the
  current answer.
* It will not make a recall look successful when a bank failed. `degraded` and
  `empty` are different facts.
* It will not hold the SQLite write lock across an engine call. If a Memory
  operation ever makes Truth writes slow, that is a defect, not a tuning problem.

## Degradation: what an operator sees

Design §14: a dead Hindsight pauses long-term Memory and nothing else.

| Symptom | Diagnosis | Action |
| --- | --- | --- |
| `status: unreachable`, `reachable: false` | the engine is not answering | check the URL and that Hindsight is running; `memory health --probe` |
| `status: failing`, `jobs.failed > 0` | the engine answers and refuses | read `memory attempts` for the real error; the code is in the table in the API doc |
| `status: degraded`, `jobs.pending > 0` | work is queued and not moving | is the worker running? |
| `status: idle` | no failures, no successes yet | nothing retained yet; expected on a fresh Core |
| `status: unconfigured` | no endpoint on this process | set `JASMINE_HINDSIGHT_URL` for the worker |
| Recall returns `skipped` / `no_memory_budget` | the Context Pack allocated no Memory partition | a P3 budget decision, not an engine problem |
| Recall returns `degraded` with items | one bank failed, the other answered | the items are usable; the trace names the bank |

### G01 historical execution input

`g01_historical_fixture` is required for a fresh G01. Supply `repository` (a
local Git repository), `ref` (the frozen commit used for both controlled Agent
copies), and `python` (the absolute existing interpreter). The fixed fixture is
`calc.py`, `test_calc.py`, and `NOTES.md`: its documented percentage behavior,
initial subtract-discount implementation, and initial 27.5 test expectation are
reviewable inputs, rather than prefilled execution results. The runner archives
that commit into a private `historical-repository`; it never edits the supplied
repository. It runs unittest, reads the source, attempts the percentage formula
and corrected 28.5 expectation, captures file hashes and the actual diff, then
runs unittest again. Every output comes from the subprocess, each action gets
its own Evidence and immutable output artifact, and any unexpected fixture or
failed verification fails the historical setup. No alternate successful trial
is selected. An interrupted historical directory must be preserved; a fresh run
is necessary rather than overwriting the attempted execution.

A task-bound `project.notes` Event links the four executed Evidence IDs. The
worker rechecks their exact result-to-call linkage, command hashes, output-byte
hashes, exit/status agreement, same Task and Step, and chronological order. It
also requires the fixed inspection/repair programs, validates the captured file
contents, before/after hashes and recomputed diffs, and requires validation to
rerun the exact failure command with exit zero. A role label or `echo OK` cannot
establish repair or verification. P3 receives all four Evidence IDs and eight source
Events, preserving complete attribution before bounded output excerpts. It may
present attempted repair followed by PASS as **HISTORICAL_NONAUTHORITATIVE**;
this establishes neither an exclusive historical cause nor the current cause.
Engine-generated prose cannot supply a missing cause, repair, or validation.
The optional Memory allocation is 1000 selected-encoding tokens (see the
mechanism-change record below), still bounded by the unchanged whole-pack limit;
omissions remain explicit.

G01's prompt, seven exploration categories, controlled baseline/lesson
comparison, real Agent execution and independent receipts remain required;
historical subprocess capture alone is not acceptance. The v1 benefit threshold
(omit at least one exploration category) produced only negative results and is
to be superseded by the versioned exploration-cost specification in
`docs/p4-g01-v2-spec.md`; that spec is frozen and confirmed by an independent
reviewer, and the v1 negatives are retained as FAIL records. The v2 pair runs
only after the implementation passes independent review and verification.

### Terminal partial-failure replay (every case)

Any attempt that ended FAIL before its durable setup unit exists is a terminal
failed attempt. The durable unit is the case's exact pack for G01-G05 and G07,
and `g06-complete.json` for G06. The phase-one seal records the original failure
reason, IDs and receipt references. `--resume` validates that seal and the
phase-one manifest anchor, rechecks every ID the record carries -- projects,
source Events, Evidence, jobs, Tasks, sessions, Context Packs and Authority rule
versions -- and receipt digests, and reproduces **FAIL** with the same IDs before
fresh setup or engine work. The fields that were re-checked and the fields that
were empty in the sealed record are both listed in `inherited_evidence`, so an
empty re-check is visible rather than passing silently. It does not retry
retention/recovery, create another project or Rule, execute another historical
repair, or start an Agent pair. Repeated resume therefore verifies stable replay
of a failed attempt; it never converts that failed Gate into PASS. A durable unit
already present continues to use the existing exact-pack/session (or completion
marker) verification path.

Legacy partial failures lacking a sealed terminal record are refused before
re-seeding. Their old records and coverage limits remain intact. A separately
registered new run, if authorized, must preserve/report all earlier failures;
service restoration cannot silently replace an old failure's result. Normal
completed-case resume and the original G01 benefit contract are unchanged.

### The functional engine preflight

Before any case runs, a Gate run executes `scripts/p4-memory-preflight.py`
in-process once, against the run's `JASMINE_HINDSIGHT_URL` and bank namespace,
in a throwaway project bank that touches no case database. The preflight asserts
the stored artifact and not the receipt: the database reports connected, a real
document is retained, extraction reaches at least one unit, the document and its
unit listing agree, and the stored content is recallable. Its report is recorded
in the manifest under `engine.functional_preflight`, including whether its own
document was cleaned up. A failed check stops the run with `verdict: BLOCKED`,
every selected case recorded against the failed check by name, and no case
database opened; fix the environment before re-running. The same checks can be
run by hand:

```bash
scripts/p4-memory-preflight.py --engine-url "$JASMINE_HINDSIGHT_URL" \
    --bank-namespace "$JASMINE_MEMORY_BANK_NAMESPACE"
```

Exit 0 is every check passed, 2 is `environment BLOCKED`, and 1 means the
preflight script itself failed (a defect, not the environment). The report names
only `HindsightConfig.origin_host`: userinfo in the URL never reaches stdout or
the report.

### Staging the native SessionStart injection

The native SessionStart hook reads `<repo>/.codex/jasmine/injection.json` and the
rendered pack file it points at. The producer of that pointer is the Gate itself:

```bash
scripts/p4-memory-gate.py --stage-injection --work <work-dir> \
    --case P4-G01 --pack-key g01-with-lesson \
    --config <operator-environment.json> \
    [--native-session-repo <repo>]
```

It loads the existing pack with that idempotency key, refuses it when its
rendered bytes do not hash to its recorded `rendered_sha256`, and writes the
pointer plus the exact bytes. Exit 0 staged, 1 refused (no such pack, or digest
mismatch), 2 the staging inputs cannot be used. Whether Codex or a human ran the
installed hook afterwards is an operator attestation recorded under
`native_session_start` in the manifest; the Gate verifies the installed script's
digest, the injected bytes, the session id, the source, the `cwd` and the
injection channel, but not the producer.

Every G01 historical action receipt and the execution-chain receipt is now
attached to its CaseRecord before retention can fail, so the phase-one seal
covers the actual outputs already produced. The baseline pack receipt is also
registered before historical setup begins. Previous runs whose historical
receipts were omitted from their seals are not retroactively claimed as sealed.

The actionable chain text now renders three labelled, capture-bound sections --
`diagnosis`, `repair`, `post-hoc` -- with every referenced Event and Evidence
pointer present in the text, as the Gate's actionable-history check requires
(attribution must survive truncation; the structured `source_event_ids` field
alone is not enough). Values come only from the verified capture: the
pre-repair line is the diff's removed line, byte-bound to the inspection record,
and the post-hoc line reports the rerun output without asserting causation.

### G01 v2 native stream: what the Gate can and cannot prove

The v2 metric is computed from the raw `codex exec --json` stream, which the
assembler copies beside the session (`session-<label>.codex.jsonl`). The Gate
seals those bytes and re-extracts them with the same parser the assembler used,
and refuses any session whose recorded events do not match the stream. What the
Gate cannot prove is that the stream is the *unfiltered* output of the Codex
process: like the session receipt and the native SessionStart receipt, it is an
operator-provided input. A stream pre-filtered before assembly is outside the
Gate's reach, and the manifest records the attestation boundary accordingly.
The classifier can also verify only so much about *what* ran: a suite run must
be a simple command whose interpreter is bare `python`/`pytest` or an absolute
path outside the tested worktree (a wrapper inside `.codex/` is not the
runner), and declared reads must not live in shell comments -- but the Gate
cannot prove that an absolute interpreter path outside the repository is
CPython. That is the same machine-level trust anchor as the operator's own
toolchain, and it is recorded here rather than claimed as a gate property.

## Mechanism change: MEMORY section budget 800 -> 1000

This is a mechanism change, separate from the G01 lesson treatment (commit
5670c15); it is not part of any benefit experiment's treatment.

- **Change**: the renderer's per-section MEMORY cap is 1000 selected-encoding
  tokens (was 800). The whole-pack `MAX_TOKENS` of 2500 is unchanged, as are all
  other section budgets and the omission reporting.
- **Why**: the labelled diagnosis/repair/post-hoc text with complete attribution
  measures ~400 tokens. At the 800 cap the item was admitted with 10-40 tokens of
  headroom or omitted whole (`optional_budget`), depending on ULID tokenisation
  jitter -- a Memory that exists only when the random IDs tokenise luckily is
  not delivered evidence.
- **Source completeness**: all 13 pointers of the verified G01 lesson (memory id,
  4 Evidence ids, 8 source Events) appear in the text. The Gate's
  actionable-history check passes end-to-end: the valid experiment at commit
  28ae83c reached the benefit comparison, so every pointer check had passed.
- **Budget regression**: on that run's lesson pack, the whole rendered pack is
  1086 tokens (limit 2500) and the `HISTORICAL_NONAUTHORITATIVE MEMORY DATA`
  block is 804 tokens (cap 1000). Both measured with the pinned o200k tokenizer.
- **Tests**: the pinned regression test now asserts the block is <= 1000 and
  that every `source_event_ids`/`evidence_ids` pointer is present in the text
  (`tests/test_p4_fix_round.py`, `TheWorkerRendersWhatCoreRecorded`); the full
  suite is green at commit 28ae83c (1116 tests).
