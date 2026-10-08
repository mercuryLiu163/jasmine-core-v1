# P6-03b2 compliance — the six Memory writes, criterion by criterion

**Status: IMPLEMENTED behind a default-off switch; PARTIAL / DEPENDENCY_PENDING.**
P4 is **not merged** (baseline `850fbec`, schema 8, has no `src/jasmine_core/memory/`), and
**P4 phase acceptance has not passed**. Everything below was exercised against the labelled
**simulated** P4 write fixture `scripts/p6/p4_write_fixture.py`
(`X-P6-Fixture: simulated-p4-writes`), never against real P4 code. Real-P4 verification remains a
separate outstanding item and nothing here may be read as passing it.

This file is the *implementation and validation* record. The specification it is measured against
is [`P6-03B2-WRITE-SPEC.md`](P6-03B2-WRITE-SPEC.md); the P4 facts it is built on are frozen in
`contracts/p4-memory-write.v1.json`.

## Models, stated rather than omitted

The operator asked for `gpt-6.1-sol`. That override is **unreachable in this environment** — it
returns no result while the session's default model answers, and the `opencode-go` provider has no
API key set. Every role therefore ran on the session default, **`deepseek-flash`**, with the
operator's agreement:

| Role | Who | Outcome |
| --- | --- | --- |
| Implementation | the session agent (`deepseek-flash`), plus one `deepseek-flash` subagent for the simulated fixture and its tests | — |
| Independent review | a separate `deepseek-flash` subagent, adversarial and read-only | 12 findings, 10 real and all fixed at `76ffa29`; 2 were process observations, recorded |
| Independent validation | a separate `deepseek-flash` subagent, re-running the suites, the contract checks and the Gate from clean archives | **REPRODUCED** for every claim it tested; its 2 findings were the credential leak and post-bundle `web/src` drift, both resolved by the current binding |

Independence here is between **separate agent contexts**, not between model families. Stated
because "independently reviewed" would otherwise be read as "by a different model".

## What is built

| Artefact | What it is |
| --- | --- |
| `web/src/views/MemoryWrites.tsx` | the panels: lifecycle (pin/unpin/obsolete/delete), retry, and the reflect route |
| `web/src/api/memoryWrites.ts` | the payload builder; throws on any field the frozen contract does not list, never emits `idempotency_key` |
| `web/src/api/endpoints.ts` | the three write helpers (six routes), typed `MemoryWriteResult` / `MemoryReflectResult` |
| `web/src/app/useWriteAction.ts` | the shared audited-write hook, reused with `keyKind: 'event_id'` (P4's replay key) |
| `web/server/core-paths.json` | `p4_write_paths`, gated separately from the reads |
| `web/server/serve.mjs`, `web/server/core-proxy.ts` | `P6_ENABLE_P4_WRITES`, both hosts, default off |
| `scripts/p6/p4_write_fixture.py` | the simulated P4 write Core, including four fixture-only control routes |
| `scripts/p6/freeze_bundle.py` | freezes a bundle and verifies one byte for byte (`--verify` exits 2 when there is no manifest) |
| `scripts/p6/run_p4_write_gate.sh` | builds an isolated `git archive`, serves it with its own fixture and proxy on free ports, runs the Gate on and off, and freezes the bundle |

## Criteria

Criterion numbers are the frozen specification's. `9a`/`9b` is the split the specification records.

| # | Criterion | State | Where it is checked |
| --- | --- | --- | --- |
| 1 | A frozen payload per action; no extra key, no missing required key | met (simulated) | `web/tests/memoryWrites.test.ts` (13) reads the contract JSON and asserts the builder's key set, the required fields, the throw on an unlisted field, and the absence of `idempotency_key`; the Gate records the **request body of every asserted write** in `browser-write-responses.jsonl` and checks it against the contract's allow/require lists |
| 2 | A permission Gate; Core's own refusal, never a UI pretence | met (simulated) | Gate record 6: a `memory:read`-only token gets `forbidden_scope` naming the scope on **all six** routes, from Core; record 7: a non-human actor gets `forbidden_actor_kind`; `web/tests/memoryWriteViews.test.tsx` asserts the panel offers no alternative credential |
| 3 | Audit readback per action; the object re-read after a forced reload | met (simulated) | Gate record 13 reads every returned command Event back **by id through the read surface** (`GET /v1/events/{evt}`) and compares it with the write response; the payload record and the destructive record click the panel's re-read and require a *second* GET plus a changed revision. `reflect` has no command Event in its eight-key contract, so its audit is the trace it writes, linked and read back |
| 4 | Revision conflict exercised; Core's current value shown beside the draft; no automatic retry | met (simulated) | Gate record 8: the item is drifted behind the UI (fixture control), then all four lifecycle commands answer `revision_conflict`, keep the draft, and issue exactly **one** POST each. `web/tests/memoryWriteViews.test.tsx` asserts the same, including that nothing is re-sent after 250 ms |
| 5 | A replay is shown as a replay and does not double-count | met (simulated) | Gate record 9: the reply to the first attempt is dropped *after* the transition is applied (fixture control), the panel keeps its `event_id`, the resubmit is answered with the 5-key `replayed: true` shape, and the fixture's own command log grows by exactly one |
| 6 | Retry invariants checked, not assumed | met (simulated) | Gate records 3–5: a `FAILED` job is accepted with `document_id`/`content_sha256`/`bank_id` unchanged and `retry_count` **reset to 0**; `DONE`, `RUNNING` and already-`PENDING` jobs are refused with `invalid_state_transition` while the button stays offered, so the refusal is Core's; the replay key is kept across a refusal and new after a success |
| 7 | `reflect` requires an explicit request; output marked non-authoritative from Core's field | met (simulated) | Gate record 11: the submit is disabled with no query, the body is the frozen one, the marker comes from Core's `non_authoritative`, and the trace is read back. `web/tests/memoryWriteViews.test.tsx` covers the same |
| 8 | No `before`/`after` assumption: both lifecycle shapes handled | met (simulated) | Gate record 10 (7-key fresh) and record 9 (5-key replay); `web/tests/memoryWriteViews.test.tsx` renders each shape and asserts the replayed one does not claim a fresh transition |
| **9a** | Not reachable in the default configuration | **met** | `web/tests/p4Writes.test.ts` (19) and `web/tests/p4WritesCoreProxy.test.ts` (16) check both hosts: default off, independent of the reads' switch in **both** directions, absent from `paths` and from the milestone-B `write_paths` (still 25 and 4), not reachable with GET, malformed ids refused. Gate `writes-off/` (4/4) shows the real proxy refusing by name and no command reaching the fixture |
| **9b** | Verified against a real P4 Core | **met for Core behaviour; engine layer is a labelled wire double** | `validation-runs/p6/20261008T061356Z-p6-03-realcore/`: **35/35** against an isolated **real P4 Core** at the pinned SHA (schema 11) — seven reads, real permissions **including key issuance**, replay and `event_id_conflict`, `revision_conflict`, real command Events read back by id, real traces, retry invariants on real `FAILED`/`DONE`/`RUNNING`/`PENDING` jobs, the withholding pair on a row that really carries engine text, and no Rule/State change. **Still not established**: anything engine-dependent (the engine is P4's `StubHindsightServer`, `mode: real-core+wire`, so P4's own Gate's `mode: real` bar is not met and P4 phase acceptance is not claimed), and the six writes have not yet been *submitted through the browser* against a real Core |

### Redaction on a write reply

Criterion 7's neighbouring rule — P4 withholds engine free text from any actor that is not human
or system — is observable on this surface only through `reflect`, because the five
`memory:manage` writes are human-or-system only. Gate record 12 takes the **same request** with two
keys: the human sees a non-null `safe_error_summary`, the artificial agent key gets the key present
and `null`, and the page shows the withheld state instead of the text. Both response bodies are in
`browser-write-responses.jsonl`, so the claim is checkable from the frozen evidence and not only
from the click-trace's prose — which is exactly what the P6-03b1 bundle could not do.

## Corrections found while building, and by whom

* **The wire codes were wrong in the first frozen specification.** It named the Python exception
  *classes* (`ForbiddenActorKind`, `MissingExpectedRevision`, `RevisionConflict`) where Core sends
  a snake_case `code`, and said `422` where `errors.MissingExpectedRevision` carries
  `status = 400`. Found by the fixture subagent reading `src/jasmine_core/errors.py` at the pinned
  commit, verified here, and corrected in the contract, the specification, the fixture, the panels,
  the tests and the Gate.
* **The retry rules were wrong too.** P4 refuses an already-`PENDING` job as well (`invalid_state_transition`)
  and **resets** `retry_count` to 0 rather than incrementing it. Verified in
  `memory/jobs.py::request_retry`, and corrected in the same places.
* **A `409` is not always a conflict to re-review.** `invalid_state_transition` is a refusal:
  re-reading the object cannot make the command legal. The shared classifier mapped every 409 to
  the conflict state, which would have offered a retry that cannot succeed.
* **The Gate's own defects**, each of which had produced a false reading and is recorded in its
  commit rather than quietly fixed: it ran the P2 walkthrough against the write fixture; two record
  names carried the old CamelCase codes; the readback record queried the wrong attribute; the
  replay record waited for a word the panel always contains; the dropped-reply record truncated the
  panel text before the state it was looking for; the audit readback sent no `Authorization` header;
  and the launcher used fixed ports, so one run talked to a stale dashboard from an earlier session
  and would have reported the product as broken.
* **The bundle recorded its own throwaway tokens.** The fixture printed the tokens it accepts and
  the launcher redirected that banner into the bundle, so three cleartext tokens sat in a frozen file
  while the bundle README said none did. Found by adversarial review (and independently by the
  validation pass). The banner is masked, the launcher **refuses to freeze** a bundle containing any
  token it generated, and the two leaking bundles are withdrawn with the reason in the index.
* **Three checks that could not fail**, all found by review: the shared sign-out record asserted
  `every(...)` over an *empty* request list; the tombstone record sent the *current* revision, so a
  revision-first Core would have passed it; and the "every write route has a panel" test rewrote the
  contract and compared it with the contract, so deleting every panel left it green. All three now
  drive the product and fail when it does not behave.
* **The fixture was stricter than the Core it stands in for.** P4's `retry_job` validates neither
  `host_id` nor `event_id`; the fixture answered 400 for a malformed value, which would have turned a
  real Core's acceptance into a fixture refusal. Relaxed, pinned by a test, and disclosed.

## Evidence

* Gate bundle `validation-runs/p6/20261008T053936Z-p6-03b2-clickthrough/`: **16/16** with the write
  switch on (twice, with identical step names and outcomes, restarting the fixture between runs) and
  **4/4** with it off; **45** files frozen and verified by `freeze_bundle.py --verify`; the bundle
  README is generated from the run's own click-trace files before the freeze. It binds `8be4352`;
  nothing under `web/src/`, `web/server/` or `src/` has changed since, and the commits after it are documentation plus one comment in the Gate launcher.
* What a reviewer can check from the frozen files alone: `browser-write-responses.jsonl` (25 write
  requests with request **and** response bodies), the 5-key replay with the same `event_id`, the
  7-key fresh transitions, the human/agent `reflect` pair, `writes-off/fixture-state.json` (the
  simulated Core's command log, empty before and after the refused submit), and a bundle that
  contains no generated token (the launcher refuses to freeze one that does).
* `web/tests`: **328** tests, `tsc --noEmit` clean. New for b2: `memoryWrites.test.ts` (13),
  `memoryWriteViews.test.tsx` (21), `p4Writes.test.ts` (19), `p4WritesCoreProxy.test.ts` (16),
  plus the classifier guards in `writeAction.test.tsx` and the rewritten read-only/affordance
  cases in `p4Views.test.tsx` and `endpointContract.test.ts`.
* `tests/` (Python): **521** tests. New for b2: `test_p4_write_fixture.py` (64),
  `test_freeze_bundle.py` (9), and `test_p4_write_spec.py` rewritten for the delivered state (15).
* `scripts/p6/p4_write_spec.py --check` exits 0 against the pinned SHA and **2** when it is absent.

## What is NOT evidenced

* **Real P4** — since the real-Core run, the Core half is no longer in this list: the four
  divergences the fixture admitted are **settled** against real P4 (P4 hashes the command payload ⇒
  the same `event_id` with a changed field is `event_id_conflict`; the nested event is a real Event
  row; a lifecycle reply carries a real queued job; the list reflects mutations). What remains
  unproven is engine-dependent behaviour and the browser pass against a real Core:
  * no real engine (the real-Core run uses P4's `StubHindsightServer`, `mode: real-core+wire`);
  * `explicit_user_request=False` is not modelled, because P4's reflect route *is* the explicit one;
  * the dashboard has not yet driven the six writes through the browser against real P4 — the
    simulated Gate covers the UI behaviours, and that pass is the remaining step.
* **The operator's own Core**: nothing here was pointed at it, and the live allowlist is untouched.
* **A browser user's own credential choices**: the Gate signs in with generated throwaway tokens;
  it cannot evidence what a real key with a different scope set would see.
* **`reflect`'s engine behaviour**: the fixture returns a fixed eight-key shape; no generator ran.

## Outstanding

1. **Real-P4 verification** — **the Core half is done** (35/35, bundle
   `20261008T061356Z-p6-03-realcore`). What remains: (a) the browser pass — point the dashboard at
   that Core with `P6_ENABLE_P4_WRITES=1` and submit the six writes from the UI, which the simulated
   Gate covers but real P4 has not yet seen; (b) a real Hindsight deployment, which would move the
   engine layer from `mode: boundary-fake` to `real` and is what P4's own Gate requires. Status
   stays **PARTIAL / DEPENDENCY_PENDING** until (a) is done; (b) is the operator's environment.
2. **Both independent passes are done, and the follow-up binds the final SHA.** The validation pass
   first ran against `b6fcee8` and returned REPRODUCED for every claim it tested, with two findings
   (the credential leak, and post-bundle `web/src` drift). Both are fixed; the current bundle binds
   `8be4352`, which contains the fixes, and nothing under `web/src/`, `web/server/` or `src/` has
   changed since. The follow-up pass then re-verified at the final SHA: suites (521 Python, 328 web,
   `tsc` clean), bundle integrity 45/45, no full token in the bundle, a **rebuild from
   `git archive 8be4352` matching the manifest's asset digest and tree exactly**, the three
   previously-vacuous records now carrying distinguishing detail, and a full Gate run of its own at
   `70ae389` with a step sequence identical to the frozen bundle's. No open discrepancy remains.
