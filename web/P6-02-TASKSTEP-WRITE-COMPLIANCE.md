# P6-02 TaskStep write — compliance record

Scope: the operator's priority item 1 — the **Task/Step write slice**: freeze the payloads,
permissions, revision rules, replay rules and audit requirements *first*, then implement and validate
them. This record maps each frozen requirement to the code that implements it and to the independent
evidence that observed it. It is not a Gate bundle and it adds no acceptance round.

**Status: DELIVERED for this slice, independently validated PASS (9/9 claims, no counterexample).**
That sentence is deliberately narrow: `P6-02` as a whole stays **PARTIAL** (item 5's
VERIFIED/ACCEPTED forms and item 6's `evidence/confirm` are undelivered), `P6` is not complete, and
nothing here releases V1.

| | |
| --- | --- |
| Frozen contract | `P6-02B-TASK-STEP-WRITE-SPEC.md`, rev 3 (revision 1 reviewed M1–M6; the delta re-review of rev 3 returned **APPROVE (freeze as-is)**) |
| Implementation | `607f511c93fa5273df16187e6d1bce81a187e7e7` ("P6-02B: implement the Task/Step write slice per the frozen spec", 27 files) |
| Review fix | `acd260b3e55aae9a16b7544103c3d86dd8773ad8` (closes the implementation review's two mandatory findings M1/M2) |
| Independent reviews | `validation-runs/p6/REVIEW-p6-02-taskstep-write-spec/` (contract, 2 rounds) and `REVIEW-p6-02-taskstep-write-impl/` (implementation: APPROVE WITH NAMED MANDATORY CHANGES) |
| Independent validation | `validation-runs/p6/VALIDATE-p6-02-taskstep-write/` — real Core (`git archive` of the tip, own DB/workspace/keys/ports) + real headless Chrome over CDP; **PASS 9/9 claims**, 49/49 browser assertions, 65/65 Core/SQLite checks, 28/28 edge-sweep entries |
| Status label | `P6-02 TaskStep write` (§11 M6 namespace `taskstep`) |

## 1. Requirement → implementation → evidence

The requirement source is the P6 taskbook §5 items 4 and 5 (`docs/Jasmine-Core-V1-P6-阶段任务书.md:94-95`)
and §7 (`:134-135`), plus the operator's instruction to freeze before implementing. The seven routes
are baseline Core routes (schema 8); nothing in this slice changes Core, Schema, scopes, id prefixes,
Event types or the edge tables.

| # | Requirement (frozen) | Implementation | Independently observed |
| --- | --- | --- | --- |
| 1 | The seven route/method/scope pairs; `201` for the two creates, `200` for the rest; a replay has the same key set as a fresh write | `web/src/api/taskWrites.ts:25-84` (`ALLOWED`, `only`), the seven body builders `:87-170`; panels `web/src/views/TaskWrites.tsx` | Validation claim 1: real human key, real browser → `201/201/200×5`, `replayed:false`, response keys as frozen |
| 2 | Allowed/required body fields per route; unknown fields refused, and Core's own refusals surfaced verbatim | `only()` allowlist (throws on an unknown key); `TaskWrites.tsx:77-94` (`parseCriteriaField`) requires a JSON **object** and so cannot silently fall back to Core's `requirements: []` default | Validation claim 4 (`400 invalid_request` with `details.fields`/`details.field`); implementation-review M2 regression tests |
| 3 | `expected_revision` read from the readback; stale ⇒ `409 revision_conflict` with `expected`+`current`, draft kept, **no** automatic retry | `web/src/api/client.ts`, `web/src/components/WritePanel.tsx` (revision field + conflict block), `TaskWrites.tsx` readback hooks | Validation claim 2: `409`, both revisions, `STALE-DRAFT-MARKER` retained, exactly one POST, object unchanged |
| 4 | Idempotency: `event_id` is the replay key, `replayed` the discriminator; the same key with a changed body is a conflict, not a second write | `useWriteAction.ts:140,164,195,248` (the key is minted once per draft **there** — `taskWrites.ts:12-16` says explicitly it is *not* built in the body builders), `useWriteAction.ts` (`replayed` never fabricated) | Validation claim 5: forced post-commit response failure → retry with the same `event_id` → `200 replayed:true`, one event row, revision unchanged; changed body → `409 event_id_conflict` |
| 5 | Timeout means **unknown**, never success or failure; the request id is shown and a readback is offered (the four request-id changes the spec's M3 required) | `client.ts` (client-generated `ui-…` id + request-id header read), `errors.ts` (`isAbortError`/`isAbortedRequest`), `useWriteAction.ts:105-109,199-239` (`unreachable`/abort ⇒ `unknown`), `WritePanel.tsx` (unknown + id + readback) | Validation claim 6: Core stopped → proxy `502 proxy_core_unreachable` **with** `X-Request-Id` → panel shows unknown + id; no-HTTP-response transport failure → unknown with the client id; readback unchanged |
| 6 | Permissions: scope and actor-kind gates; an agent key can neither mint an accept-scope key nor accept, set criteria, or create criteria-bearing objects | Core enforces (`principal.require`, `ForbiddenActorKind`); the UI offers no privileged control it cannot back — the refusal is rendered, not hidden. **Deliberately not claimed:** `EXECUTED→VERIFIED` is **not** role-gated in Core — `state.py:333-336` applies `can_accept`/`_role` only to `SKIPPED`/`ACCEPTED` (and to writes on a `SKIPPED` step), while the `VERIFIED` branch at `:341,:346` is Evidence-only — so no agent→VERIFIED refusal was observed and none is guaranteed | Validation claim 7: issuance `403 forbidden_actor_kind`; agent refusals `403 forbidden_scope` on route 6, route 7, route 3→ACCEPTED and criteria-bearing create |
| 7 | Evidence thresholds: a gated edge with insufficient Evidence answers `422 missing_evidence` and **leaves the state unchanged, no Event** | Core's own thresholds; the panel renders the blocked branch (`WritePanel.tsx` evidence block) and never claims a write | Validation claim 3: real `200`s for route 6 and the gated route-3 edges with Core `evidence_ids`; negatives assert `422` (`{"step_id"}`, and the confirmation-specific branch) with revision+status unchanged and no Event |
| 8 | Audit: every manual change writes an immutable command Event; audit readback by an authorised validator; route-specific `target_id` (M1) | Core's dispatch/audit; nothing in the UI writes around it | Validation claim 1: exactly one command Event of the expected type per route; one audit row per `X-Request-Id` with `decision=allow` and `target_id = tsk_…` (route 1) / `evt_…` (other six) |
| 9 | The proxy is the browser's only path: the seven state-write patterns are always-on, Core remains the enforcer, and out-of-scope POSTs are refused by the proxy itself | `web/server/core-paths.json:37-48` (`write_paths`, +7 patterns), `core-proxy.ts`, `serve.mjs`, `serve.d.mts`; contract test `tests/p6/test_dashboard_proxy_contract.py` | Validation claim 8: `serve.mjs` with **no new flag**/no `P6_ENABLE_*`; the five out-of-scope POSTs each `404 proxy_path_not_allowed` with a proxy-minted id and no Core audit row |
| 10 | No optimistic write: what the browser claims equals a fresh Core read | `TaskWrites.tsx` readback (`useAutoReread`), never the request body | Validation claim 9: every route's browser readback equals a fresh Core read (revision + status); Step writes re-read Step **and** Task |

## 2. Verification commands

```
cd web && npx vitest run          # 30 files, 454 tests pass (after acd260b)
cd web && npx tsc --noEmit        # clean
PYTHON=python3.13 ./scripts/run-tests.sh   # P6 Python suite: 574 tests OK, exit 0
```

All three were re-run on the record commit's tree (`5d085de`, whose only tracked change is
documentation) when this record was written: the web suite by the reader who can reproduce it in one
command and by the delta reviewer, the Python suite by the parent (`Ran 574 tests … OK`). They are
reproduction aids, not the acceptance evidence — that is the bundle below.

The independent acceptance gate is the validator's, not one of these: it archives the commit, mints
its own keys, drives real Chrome over CDP and checks Core's own SQLite. Reproduce it with
`python3 evidence/harness/run_gate.py --src … --runtime … --evidence … --ref acd260b` as recorded in
`validation-runs/p6/VALIDATE-p6-02-taskstep-write/findings.md`.

## 3. Limits recorded rather than claimed

1. **The Evidence-passing environment is out-of-band.** Route 6 and the two gated Step edges were
   driven to real `200`s, but the fingerprint and `USER_CONFIRMATION` Evidence that enable them were
   minted outside the browser, because the two routes that mint them
   (`POST /v1/workspaces/fingerprint`, `POST /v1/evidence/confirm`) are deliberately proxy-refused
   (spec §1/§12). In a fresh environment those three cases must be recorded **BLOCKED/PARTIAL** — the
   spec's own permitted outcome. This is the boundary between this slice and item 6, not a defect.
2. **Not exercised (recorded, not claimed):** the explicit `ui_request_aborted` (`AbortError`)
   branch; route 1's `actor_id ≠ token actor` → `403 actor_mismatch` (the panel never sends
   `actor_id`); the terminal-state refusals other than the ones listed; a concurrent-writer `409`.
3. **The `400` probes are browser-issued, not panel-issued.** The body builders' allowlist *forbids*
   `idempotency_key`, so those requests came from the page's own authenticated `fetch` and were
   rendered into the page; Core's audit log confirms they reached Core.
4. **§9 deviation, accepted by review.** No route in schema 8 exposes `STEP_EDGES`/`TASK_EDGES`, so
   the transition panels offer Core's status **vocabulary** (a superset of the legal-target set),
   disable nothing as illegal and assume no symmetry; Core stays the only verdict on legality. The
   implementation review ruled this acceptable under §12; it is filed as the **coordination request
   to the Core interface owner** in the frozen spec's §13 and in `P6-REMAINING-TASKS.md` §6.
5. **Naming correction (parent-side, after the freeze).** The spec's §11 M6 rule reserves the token
   `p6-02b` for milestone B. The parent's brief had used it for this slice's review/validation
   directories; the validator flagged the discrepancy. What was actually done, stated precisely: the
   **two review report directories** were renamed to the `taskstep` namespace by their own authors,
   who each proved the change was path-strings-only (substituting the token in a byte-exact backup
   reproduces the new file exactly); the **validation bundle was re-created** at the compliant path
   by a fresh run at `acd260b`, so the old reserved-token bundle no longer exists. Because those
   directories are gitignored and untracked, the rename cannot be recomputed from git history — the
   evidence is each author's recorded diff plus the fresh run, and that limit is recorded rather than
   glossed. The Gate-class namespace for this slice is `<stamp>-p6-02-taskstep-write/`.
6. **Two review findings on the record commit were folded after review.** A delta review of the
   record (`validation-runs/p6/REVIEW-p6-02-taskstep-write-record/findings.md`) found a stale
   "still gated on an independent permission Gate" row still in `README.md`, and this document's row 6
   over-claiming an agent→VERIFIED refusal; both are corrected above, with row 6 now stating the
   non-gate explicitly. Its NOTE on `P6-01-COMPLIANCE.md`'s "Deliberately not delivered in this PR"
   table is **left unchanged on purpose**: that file is a frozen record of P6-01's own PR, and
   rewriting it would falsify what that PR delivered. The pointer to what later arrived lives here.
7. **Process provenance.** Every pass ran in its own agent context: proposal draft; contract review
   (rev 1, M1–M6); revision to rev 2; delta re-review (rev 3, freeze as-is); implementation;
   implementation review (M1/M2 closed in `acd260b`); independent end-to-end validation; delta review
   of this record. **Model caveat, stated rather than glossed:** the operator asked for these passes on
   `gpt-6.1-sol`; that override was unreachable in this environment — it returned no result while the
   session default did, and the `opencode-go` provider has no API key — so every pass ran on the
   session's default model (`deepseek-flash`). Independence here is between separate agent contexts,
   not between model families.

## 4. What this does not do

* It does not accept `P6-02`, `P6` or V1: item 5 (VERIFIED/ACCEPTED forms and the criteria↔Evidence
  matrix) and item 6 (`evidence/confirm` from the browser) remain undelivered.
* It does not inherit any milestone-B verdict: milestone B's PASS binds milestone B's commits
  (`P6-REMAINING-TASKS.md`, "Historical limitations") and this slice carries its own evidence.
* It does not run a Gate against the operator's own Core or a real captured conversation, and it says
  nothing about P7-01, whose criterion 1 (a real FAIL run on disk) remains **BLOCKED**.
