# P6-02 item 6 independent design review — round 2

Verdict: **APPROVE**, for the exact document hashes below and **design only**. No required findings remain. User confirmation is still required before implementation.
Reviewer: independent delegated context `/root/item6_independent_review`. Date: 2026-10-08 America/Los_Angeles.

## Frozen inputs

| File | SHA-256 |
| --- | --- |
| `adr.md` | `540568b257c9419dd6c4f749f7ef40a8b94994f6750892fcbb609397e4e91cef` |
| `api.md` | `aefccb63087ad3d4c7fc5ff2dc87cbbca29e10df260988384ab5d2178f426da5` |
| `validation-plan.md` | `125c2e2ee8f34bef72da301332d31b192412e19534e9a7a246614b26b40f59e2` |

| Independently rechecked baseline | HEAD | source schema |
| --- | --- | --- |
| Core main | `7016dabb8926d6bd6599f1f56b8f39cbda253ebf` | 10 |
| P6 consumer `_worktrees/p6-01` | `41a0dd6ae29dc559c9d3817fb6ea34229c68b66b` | 8 |
| `_worktrees/p6-03-integration` | `039f5a3e7800704e5f0478d01992f00eeb2ca3f1` | 11 |

All three revised documents were read in full. I independently checked the new digest algorithm against `models.NewEvent.hash_fields` and the first Evidence reference status against `EvidenceStore._reference_status`. The three baseline HEADs remain unchanged, and tracked production diff is empty. No runtime tests, services, browser, database or network were used. This report only changes this review directory.

## Round 1 closure

| Finding | Revised contract and falsifiable check | Decision |
| --- | --- | --- |
| R1 [P1] Raw/public masking inconsistency | `adr.md:167-170`, `api.md:137-151,197-201,280` explicitly preserve existing authorized Raw HTTP content for material/notes, bind review_sha256 to the full material shown, and put masking in derived logs/audit/shared bundles. Existing GET/list serialization stays unchanged. C18/W02/W05 independently test raw equality and derived masking. Actual bearer/header material is not copied into Events. | CLOSED |
| R2 [P1] Concurrent writer replay order | `adr.md:128-144`, `api.md:130-135,167-175,245-248` now require the second replay/conflict lookup under the writer lock, before consumption/expiry/current drift for both commands. Global Event-ID collision without a matching operation projection is explicit. C11 forces both early lookups to miss with a barrier and separately checks prepare, same-ID submit and different-ID submit races. | CLOSED |
| R3 [P1] Note-byte binding and source integrity | `adr.md:107-119`, `api.md:285-309` freeze no hash_payload override, client_occurred_at=None, the exact private Event hash object, raw-text reconstruction of submit semantic hash, prepare semantic reconstruction, cross-record field checks and reconstruction of first receipt. This matches current NewEvent.hash_fields, including occurred_at:null and separate actor_kind/seq/time checks. C13 includes text-only mutation and equal-masked-view counterexamples. | CLOSED |
| R4 [P1] Full-material status read scopes | `api.md:27-29,143-146` require every prepare read scope on status and prepare replay. submit/replay receipt is a separately defined same-key self-submission read grant under evidence:confirm and does not include Rule/body material. C23 removes read grants between prepare and recovery and checks denial without material leakage. | CLOSED |
| R5 [P2] Canonical strictness attribution | `api.md:12-15,122-125` accurately describe existing canonical bytes and require bounded exact-type/finite/Unicode validation on request, server and stored material before hashing, without changing the shared utility. C18 separately covers malformed server/stored material and legitimate Unicode byte compatibility. | CLOSED |

## Full review of target-status delta

The revision extends Step confirmation to target VERIFIED when the Step is EXECUTED, as well as target ACCEPTED when it is VERIFIED; Task remains ACCEPTED only. This addresses round 1 N1 rather than merely documenting an unsupported dependency.

`adr.md:74-97` and `api.md:60-74,118,127-128` bind target_status into the immutable material, normalized prepare request and digest. `api.md:182-190,264-266,298-300` bind it into source, command, preparation projection constraints and integrity reconstruction. A submitted confirmation still yields only USER_CONFIRMATION/INFO and never transitions State. Existing State transition rules/permissions remain authoritative; there is no target-based grant of state:accept and no solution of CQ-1 by implication.

The existing validator requires confirmation revision to equal the current object revision. Thus EXECUTED→VERIFIED increments Step/Task revisions and invalidates that confirmation for the later ACCEPTED request, as `adr.md:96` and `api.md:205-206` explicitly require. Task-only confirmation cannot use target VERIFIED, and every Task preparation is delayed until the final Step-state prerequisite. C01/C17/C24 and G-I6-01 now cover this ordering, Rule-bound USER_CONFIRMATION/INFO, re-preparation and the continued inability to satisfy USER_CONFIRMATION/PASS. This is an item 6 confirmation-source contract expansion with explicit State integration; it does not silently introduce a new State edge, authoritative Evidence matrix or actor-kind gate.

The new top-level source_event_id choices (`api.md:85-87,185-190`) are compatible with the existing unique source index: prepare uses null, one source per preparation uses its prepare ID, and the command uses the source Event ID. Source and command are distinct global evt_ IDs. New Event schema_version is treated as creation-time schema under the supported contract, rather than requiring equality with later current schema (`adr.md:109-111`); historical sources therefore need not be rewritten on future migrations.

## Retained boundaries

No physical-human/presence/read-attestation guarantee is inferred from bearer authentication or automated browser clicking. Registered host identity does not attest the actual browser device. Authorized Raw product reads remain separate from safe shared evidence, and actual private material must not enter shared bundles. Filesystem sampling and SQLite commit remain separate domains, with the stated sampling window; future implementation must enforce the frozen scan/request deadlines and prove no writer lock spans the scan. Refusal audit remains best effort; a complete audit Gate requires actual authorized audit readback. CR-1…CR-5 and CQ-1 stay excluded, and old prompt confirmation remains unchanged.

The implementation reviewer and independent validator must still verify source-reservation coverage across real ingress paths, immutable/REPLACE protections, deadline enforcement, both writer replay lookups, all private-byte integrity checks, the exact read-scope table and C/W/G evidence. None of these have been executed by this design review. Migration/ADR numbers and one actual integration baseline still need owner coordination at implementation start, as already frozen in the documents.

APPROVE means the reviewed proposal is sufficiently concrete and internally consistent to present for user confirmation. It grants no implementation, migration, service-start, browser-operation or release authorization, and does not upgrade item 5, P6, P4/P7 or V1 acceptance.
