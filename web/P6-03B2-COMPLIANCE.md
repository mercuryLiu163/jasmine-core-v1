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

| Role | Who |
| --- | --- |
| Implementation | the session agent (`deepseek-flash`), plus one `deepseek-flash` subagent for the simulated fixture and its 63 tests |
| Independent review | a separate `deepseek-flash` subagent, adversarial, read-only, on the final commit |
| Independent validation | a separate `deepseek-flash` subagent, re-running the suites and the Gate from a clean archive on the final commit |

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
| **9b** | Verified against a real P4 Core | **NOT met** | Nothing in this slice has been served by P4 code. The Gate's Core is a simulation |

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

## Evidence

* Gate bundle `validation-runs/p6/20261008T053138Z-p6-03b2-clickthrough/`: **16/16** with the write
  switch on (twice, with identical step sequences, restarting the fixture between runs) and
  **4/4** with it off; 44 files frozen by `freeze_bundle.py --verify`; the bundle README is
  generated from the run's own click-trace files before the freeze. (The bundle binds
  `422923b`; the commits after it touch documentation, the launcher's README step and two record
  names — no product source — which the evidence index records.)
* `web/tests`: **322** tests, `tsc --noEmit` clean. New for b2: `memoryWrites.test.ts` (13),
  `memoryWriteViews.test.tsx` (15), `p4Writes.test.ts` (19), `p4WritesCoreProxy.test.ts` (16),
  plus the classifier guards in `writeAction.test.tsx` and the rewritten read-only/affordance
  cases in `p4Views.test.tsx` and `endpointContract.test.ts`.
* `tests/` (Python): **520** tests. New for b2: `test_p4_write_fixture.py` (63),
  `test_freeze_bundle.py` (9), and `test_p4_write_spec.py` rewritten for the delivered state (15).
* `scripts/p6/p4_write_spec.py --check` exits 0 against the pinned SHA and **2** when it is absent.

## What is NOT evidenced

* **Real P4**: no route, no validator, no engine, no job. The fixture's own docstring lists what it
  cannot reproduce (no Truth store or engine; the actor kind is a scope stand-in; its replay
  identity is `(object_id, command)` while P4 hashes the command payload; the nested command
  `event` shape is fixture-invented; `job` is always `null` on a lifecycle reply; the read **list**
  routes do not reflect mutations; `explicit_user_request=False` is not modelled because the
  fixture *is* the explicit route).
* **The operator's own Core**: nothing here was pointed at it, and the live allowlist is untouched.
* **A browser user's own credential choices**: the Gate signs in with generated throwaway tokens;
  it cannot evidence what a real key with a different scope set would see.
* **`reflect`'s engine behaviour**: the fixture returns a fixed eight-key shape; no generator ran.

## Outstanding

1. **Real-P4 verification** — the single item the operator deferred: point the dashboard at a Core
   built from `0c4f8f0e2118711ac9aa2a7006fddc6982962248` with `P6_ENABLE_P4_WRITES=1`, and re-run
   this Gate's assertions against it. Until then, 9b is unmet and the status is PARTIAL /
   DEPENDENCY_PENDING.
2. Independent review and validation reports on the final commit (recorded in the evidence index).
