# P6 02 item 5 compliance record

The VERIFIED/ACCEPTED forms and criteria-to-Evidence matrix are implemented and independently reviewed.
The final implementation Gate passed on an isolated real Core and real Chrome after the required
fingerprint, Evidence and human confirmations were prepared through Core's public API outside the
browser. **Item 5 is PARTIAL**: this is observed browser submission/readback evidence, not a
browser-only lifecycle. `P6-02` remains **PARTIAL**; P6/P7 and V1 are not accepted by this record.

## Implementation and evidence binding

| Artifact | Binding |
| --- | --- |
| Frozen contract | `P6-02-ITEM5-VERIFIED-ACCEPTED-SPEC.md`, rev 3. Rev 2 supplied the implementation contract; independently reviewed rev 3 corrects only the existing `events:read` scope summaries in §3.1, §6 and §10. |
| Initial implementation | `f63658471a8bc4ef75470409ca497a4fabd6e115` |
| Implementation review fix | `613a022bf5e58e1ae45d147dcfcd59771422b66d`: Step-specific Rule projections and neutral USER_CONFIRMATION/PASS authoring-hazard explanation; only `CriteriaEvidenceMatrix.tsx` and `verifiedAccepted.test.tsx` changed. |
| Independently reviewed and validated candidate | `551c95a6d77b2deb4826c44938eebcba4365687d`; its only change after `613a022` is the scope erratum document. |
| Independent implementation review | Initial **REQUEST_CHANGES** at `f636584`; final **APPROVE** at `551c95a`. Reports in `validation-runs/p6/REVIEW-p6-02-item5-implementation/`. |
| Independent Gate | `validation-runs/p6/VALIDATE-p6-02-item5-verified-accepted/`: report, exact raw reports and evidence ZIP. Final run4: **81/81 browser observations**, **65/65 Core/SQLite checks**, **27/27 same-run crosschecks**, **28/28 edge checks**. |
| Scope erratum review | `validation-runs/p6/REVIEW-p6-02-item5-scope-errata/`: initial REQUEST_CHANGES for the remaining §10 count, then delta and promotion **APPROVE**. |

The implementation's full web suite passed **32 files / 501 tests**, with clean TypeScript checking.
The reviewer independently reran the unchanged original counterexamples (**2/2**), **85** focused
tests, **4** additional context negatives and **6** isolated proxy checks. These component and proxy
results support implementation review; the separate real Core/Chrome Gate supports browser claims.
The Core Python suite was not rerun for this web-only repair; the handoff's older Python result is
not relabelled as a run on the final candidate.

## Contract to implementation to observations

| Contract | Implementation | Independent observation |
| --- | --- | --- |
| §3.2 candidate is never a verdict; F1/F2/F8/F9d/F9e remain unknown | `src/lib/criteriaMatrix.ts`, `src/views/CriteriaEvidenceMatrix.tsx` | Gate case `item5 candidate is non-verdict and names F1/F2/F8/F9d/F9e`; unknown current fingerprint is distinguished from the stored digest. |
| §3.2/§3.3 object and Rule requirements use the submitted object's scope and revision | `CriteriaEvidenceMatrix.tsx` renders Task and each Step's Rule projection; `criteriaMatrix.ts` filters rows and checks readable freshness | Cases `item5 Rule projection Task candidate, Step B candidate, Step A missing with no candidate`, `item5 Rule USER_CONFIRMATION uses Step exact revision and separate Task revision`, and `item5 Rule confirmation revision change proves F6 stale for Step and Task`. |
| §3.3 Rule version, activation source and missing mapping | `CriteriaEvidenceMatrix.tsx` | Browser Rule matrix and `item5 Rule-no-mapping`: real `422 missing_evidence` with `{rule_id, version}`. Setup uses Core API; no claim that Rule setup is inherently impossible through existing browser Authority routes. |
| §3.2 INFO never becomes PASS | Shared requirement rows and Core status badge | `item5 MUST INFO stays INFO; PASS requirement missing; no operator blame`; USER_CONFIRMATION/PASS also names the permanent authoring hazard without a mint control. |
| §3.1/§6 missing `events:read` is a real refusal | Existing R-EV read via `getEvent` and matrix error panel | `item5 absent events:read renders real 403 required scope`; no fallback credential or empty-panel substitution. |
| §4 submissions reuse item-4 bodies/revision/replay/unknown handling | Existing `TaskWrites.tsx`, `useWriteAction.ts`, `WritePanel.tsx`; matrix entry from write/evidence tabs | Item-4 real-route regression, Core checks and edge sweep passed; `item5 stale VERIFIED displays Core current snapshot and keeps draft without retry`. This does not transfer item-4's old verdict to unrelated code. |
| §5.1/§5.2 missing-Evidence branches preserve Core details | `WritePanel.tsx` names the five details shapes and renders raw details, including `version` | Observed missing Step/Task criteria, requirement, current human confirmation and applicable Rule mapping branches; defensive missing activation Event and malformed-validator-result branches are not claimed as observed. |
| §5.3 Evidence insufficiency leaves Truth unchanged | Core transaction plus UI refusal/readback | **17** refusal invariant observations (**10** `missing_evidence`, **4** `forbidden_scope`, **2** `invalid_state_transition`, **1** `revision_conflict`): both Task/Step `(status, revision)` pairs, both history `event_id` sets and `max(seq)` unchanged; intended Event absent. SQLite projections and Events COUNT/MAX unchanged; each request has one audit row. SQLite/audit is Core-side corroboration. |
| §6 actor and scope boundaries | Existing Core dispatch/state gates; UI displays the actual refusal | Browser agent ACCEPT and criteria writes receive `403 forbidden_scope`; agent VERIFIED reaches Evidence validation and receives `422`, not an invented actor-kind refusal. Issuing agent `state:accept` is direct Core HTTP evidence. |
| §7 recorded verdict is Core readback | `TaskEvidence.tsx` displays recorded Event results; submission hook re-reads Core | Final positive routes use real Core reads for status/revision and recorded `evidence_ids`/`rule_versions`; no optimistic acceptance. Their API-prepared environment is an explicit prerequisite. |
| §8.9 confirmation and producer POST routes stay closed | Unchanged proxy allowlist; no confirmation control | Served-browser requests to `evidence/confirm`, `workspaces/fingerprint`, `workspaces/compare`, `tool-results`, `events`, `auth/keys` receive `404 proxy_path_not_allowed`; Core audit has zero rows for those proxy request IDs. |

## Negative cases and environment limits

The Gate report maps all nine §8 negatives individually. Negatives 1 and 2 include real browser
submission/refusal after API-prepared prerequisites. Negative 3 includes real browser submission and
history equality. Negative 4(a) is Core-only key issuance; 4(b)/4(c) are browser submissions. Negative 5
is a real stale browser draft and `409`. Negative 6 uses the **10** `missing_evidence` observations for Evidence-insufficiency equality;
**7** other refusal observations corroborate the same invariants across scope/state/revision guards.
Browser equality and SQLite checks are labelled separately. Negative 7 is browser missing-mapping rendering/submission with Core-side setup.
Negative 8 checks INFO, missing and no operator blame. Negative 9 combines served-browser proxy
refusal with Core-side proof that those requests never reached Core. These distinctions replace a
blanket claim that negatives 1, 2, 3-equality, 7 or 9-proxy cannot be browser-driven.

Evidence and confirmation preparation used real Core public APIs: fingerprint, human `user.prompt`,
`evidence/confirm`, system `tool-results`, and Rule propose/approve. No SQL inserted Evidence, no fake
validator was installed and no Truth projection was edited to make a positive route pass. SQLite was
read for corroboration. `api_keys.last_used_at` and audit operational writes are permitted; unchanged
Truth is not a claim that every database table is unchanged.

Browser-only production of fingerprint/Evidence/confirmation remains **BLOCKED/PARTIAL**. The
defensive missing Rule activation Event and malformed-validator-result branches were not exercised.
The Gate does not establish a production-user-Core lifecycle, a captured-conversation chain, or an
exhaustive environment test of old fingerprints/BROKEN_REFERENCE. Those scopes remain unverified.

## Original failure and unchanged repair criteria

The initial review's R1 showed Rule requirements evaluated only in Task scope. Evidence from Step B
therefore appeared as a Task candidate while Step A's Rule missing projection was absent. The original
reviewer counterexample file was preserved byte-for-byte and passed after the implementation repair.

The final frozen harness ran natively on both candidates. Original run3 at `f636584` had **81 checks,
3 R1-related FAILs**; final run4 at `551c95a` had **81 checks, 0 FAILs**. Both used `gate.mjs` SHA-256
`3c4b4e7bb55b9641010b1140aafc572fd40a44df32a705f56295e627c26cab95`; all twelve execution-script
digests were checked. Each run actively generated its own crosschecks from its own browser/Core
outputs, with matching input digests and timestamps. Neither result is a posthoc recomputation.
Each source archive was checked against all **278** Git blobs of its exact candidate.

Earlier run1 is retained as **INCOMPLETE** because a harness regex error stopped execution. Run2's
raw output and `HARNESS_ASSERTION_TOO_STRONG` adjudication are retained: the compound hazard-text
assertion exceeded the contract's MUST. The final harness separates the MUST from the SHOULD; R1
remains a product failure in the original candidate. No old failure or raw script error was overwritten.

## Decision boundaries

`evidence/confirm` remains absent pending the Core ADR/API decision, with CQ-1 and CR-1 through CR-7
in the frozen contract. P7-01 criterion 1 remains **BLOCKED** for lack of a genuine FAIL run. Neither
is upgraded by this record. The browser cannot establish the unreadable freshness remainder.

The independent review and validator used separate agent contexts. Their spawn configuration was
`gpt-6.1-sol`; the actual backend model has no independently attested receipt. The validator was told
the reviewer's scope concern before execution, so this is independent validation, not a blind test.

Temporary owned Core/dashboard/Chrome processes, ports, DB and keys were cleaned and verified.
Existing user services/global configuration and earlier frozen evidence were preserved. No push,
PR, CI or main change is part of these results. The goal remains **active**. This record documents
the bounded implementation and its evidence; it does not accept P6-02, P6/P7 or V1.
