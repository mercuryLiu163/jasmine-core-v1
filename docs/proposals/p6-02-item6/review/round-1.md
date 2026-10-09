# P6-02 item 6 independent design review — round 1

Verdict: **REQUEST_CHANGES**. Scope: document/API design only; no implementation or runtime verdict.
Reviewer context: independent delegated agent `/root/item6_independent_review`. Date: 2026-10-08 America/Los_Angeles.

## Frozen reviewed inputs

| File | SHA-256 |
| --- | --- |
| `adr.md` | `517fa655a649f782a35947df91926346c57bcc7035cbaf1b2d700c3a766598b2` |
| `api.md` | `b8a9048cb86b6e6317e3913c4dfb280672ba0c39be84a879818ffe7ebace821e` |
| `validation-plan.md` | `27df4528430c325cd92d24a77d93cc48ef4e2b61e851eb0727a4ddfdd449a047` |

| Independently checked baseline | HEAD | source schema |
| --- | --- | --- |
| Core main | `7016dabb8926d6bd6599f1f56b8f39cbda253ebf` | 10 |
| P6 consumer `_worktrees/p6-01` | `41a0dd6ae29dc559c9d3817fb6ea34229c68b66b` | 8 |
| `_worktrees/p6-03-integration` | `039f5a3e7800704e5f0478d01992f00eeb2ca3f1` | 11 |

I read all three documents only after the author explicitly declared them ready. Prior baseline inspection covered ADR0007, confirm/validator, Auth issuance/runtime, State Event-first writes/replays, Event ingress/serialization, fingerprints, immutable projections, request parsing, dispatch audit, item 5 frozen rev 3/compliance and proxy permissions. No service, browser, database, network or production-code execution was used. The author disclosed serialization concerns after readiness; I independently verified the source. This is independent review, not blind review. The fixes below are design requirements, not implementation patches.

## Required findings

### R1 [P1] The proposed masked public Event contract contradicts the retained Raw readback path

Locations: `adr.md:160`; `api.md:165-166,242,256`; `validation-plan.md:39,54,57`.

The draft calls source_event publicly masked under ADR0004 and caches a masked receipt, but instructs the browser to GET that same Event through the existing endpoint. At the reviewed main baseline, `api/handlers.py:252-273` returns EventStore raw payload for both list and GET; `events.py:53-69` returns the original payload. ADR0004 §1.1 explicitly limits masking to derived logs/audit/validation and does not define a masked HTTP API. Thus a secret-shaped note is masked in one receipt and returned raw on deep-link/list readback, while the draft claims one consistent public boundary. Prepare also returns complete Rule content and binds its bytes; masking that material without a separate view digest would make the document the person sees differ from what is signed.

Required repair: choose and state one precise authorized Raw-versus-derived-view policy for prepare/submit/status/replay and existing Event GET/list, and identify every affected serialization path. Preserving current authorized Raw API is a valid choice if the statements promising masked HTTP output are removed, the limits are explicit, and logs/validation/UI display masking are distinguished. If masked HTTP is chosen, scope changes to existing GET/list must be explicit and raw/store versus presented digests specified. Do not apply broad `redact()` indiscriminately to metadata such as key_id. In all cases define whether review_sha256 binds the exact displayed full material or a separately identified private document, and ensure no silent masked blind signing.

Verifiable repair: C18/W02/W05 must compare a note containing a synthetic secret pattern, a Rule with such a pattern, initial response, status/replay and Event GET/list against the selected policy; verify original source bytes internally and derived bundle masking independently. No live credentials should be used.

### R2 [P1] The writer-side sequence cannot guarantee the promised same-command concurrent replay

Locations: `adr.md:133-137`; `api.md:139-144,169,209-211`; `validation-plan.md:31-32`.

The first replay lookup occurs before scanning. Two identical requests can both miss it, both scan, then wait for the writer. After the first commits, the second reaches the specified writer checks of consumption/time/DB context. Consumption is now true, so this sequence produces confirmation_consumed rather than the promised 201+200. The same race exists for prepare: two misses can create different timestamps/snapshots unless the second resolves the original prepared command under the lock. Exact replay must precede expiry/context checks both at the early read and after obtaining the lock; the current writer list does not say this.

Required repair: freeze a writer-locked replay/conflict lookup for prepare and submit before consumption, time and drift checks, after current authentication/ownership. Same ID/body returns the immutable original receipt, different body conflicts, and only a different command ID on a consumed preparation returns confirmation_consumed. Specify behavior if the global command ID belongs to another Event with no matching projection. Preserve the early replay fast path.

Verifiable repair: C10/C11 require a barrier forcing both requests to miss the early lookup and finish scans before either writer commit; assert 201+200 and exactly one source/command/Evidence. Run the analogous prepare race and the different-ID submit race separately.

### R3 [P1] Source revalidation does not yet bind the original note bytes to the command digest

Locations: `adr.md:104-113`; `api.md:138,151-166,243-244`; `validation-plan.md:34,39`.

The specified validator checks material digest and pointer/identity/revision/fingerprint equality. review_sha256 covers the prepared material, which excludes the note. A source.text-only corruption therefore leaves every enumerated material/pointer check unchanged, while command semantic_request_sha256 remains the digest of the old note. Merely retaining that digest or noting private digest semantics is not a verification algorithm. The draft also calls the source field note in ADR line 108 but the actual source payload field is text. This is material for C13's claim that source corruption is refused and C18's original-byte guarantee.

Required repair: specify exact private-byte integrity reconstruction. At minimum rederive submit semantic_request_sha256 from the stored original source text and authenticated/persisted command fields and compare it to command/submission values; define and verify stored source/prepare/command Event digest semantics, accounting for any NewEvent.hash_payload override. Compare cached receipt with the selected public serialization of those verified original records. Read/replay and State validation must share this check, with the documented different error vocabulary. Never reconstruct from masked text. Resolve note-versus-text naming.

Verifiable repair: C13 must include an isolated corrupted-source case changing only raw note bytes while leaving all IDs, material digest and cached receipt intact, including two raw notes that have the same masked view. Both read/replay and subsequent accept must fail closed. Keep a legitimate secret-pattern note positive case to ensure masking does not invalidate an honest source.

### R4 [P1] Status releases the full prepared material without the scopes that authorized reading it

Locations: `api.md:23-25,175-178`; `adr.md:55-57,65`; `validation-plan.md:24-25,43`.

Prepare requires objects:read/state:read/authority:read, but status returns the same full preparation including object criteria and complete Rule content under only evidence:confirm/evidence:read. Ownership and same-key binding are not equivalent to rechecking read authorization. The draft already promises current scopes/ACL on every read/replay; if the bound key's read grants change, status still discloses those fields. No assertion that key grants can never change is frozen. The current auth schema permits mutable grants; revocation alone is not a general read-scope policy.

Required repair: status must require the full scopes needed for its complete material, or its response must use a separately frozen limited receipt shape that omits protected material. State the consistent current-scope policy for prepare replay and submit/status receipt recovery, without using key ownership as a scope substitute. ACL checks remain necessary where present.

Verifiable repair: add an isolated test reducing or substituting the relevant grants between prepare and status/readback; no Rule/object material must be available without the selected required read grants. Preserve same-actor/different-key ownership negatives.

### R5 [P2] The canonical serialization claim is not the implementation being reused

Location: `api.md:116-117`; related strict-input promises at `api.md:9-11,239`.

`canonical.py:64-66` uses ensure_ascii=False, sorted keys and compact separators, but also default=str and leaves allow_nan at its default. The document describes allow_nan=False as an existing convention. That incorrectly locates the strictness and can let a later implementation treat arbitrary material types as valid because shared serialization converts them to strings. HTTP strict parsing alone does not validate server-derived review material.

Required repair: accurately describe the existing byte algorithm and mandate bounded recursive strict validation of all request and server-derived material fields before hashing: exact documented JSON types, finite numbers, valid Unicode, no fallback object stringification. Do not change the shared canonical utility merely to make this wording true; retain compatibility for existing sources. Document rejection of invalid material as an integrity/typed error rather than a silent conversion.

Verifiable repair: C18 must include malformed stored/server-derived material in addition to duplicate-key/NaN HTTP input, and prove a legitimate Unicode material digest remains byte-compatible with current canonical_json.

## Non-blocking scope observation

N1: `adr.md:87-90` and `api.md:66-67` deliberately restrict this API to Step VERIFIED / target ACCEPTED. Existing CurrentEvidenceValidator also applies USER_CONFIRMATION Rule/criteria requirements during EXECUTED→VERIFIED. Such a Rule can therefore require an old external human prompt confirmation before the new browser API is eligible. This design does not by itself close every browser confirmation→VERIFIED loop. State this limitation in the delivery/Gate boundaries and include a pre-VERIFIED USER_CONFIRMATION requirement scenario as a declared unsupported dependency, or separately propose that additional target if intended; do not silently broaden this item. Existing CR-1…CR-5/CQ-1 exclusions are otherwise explicit.

## Positive assessment and decision boundary

The dedicated source avoids fabricated user.prompt; actual bearer-human trust limits and registered-host limitations are candid; INFO semantics, one Rule pair per Evidence, State acceptance as a separate action, actor/key binding, expiry and unknown-outcome recovery, immutable submission-as-consumption, source protection, migration numbering discipline, unchanged historical bytes, scoped proxy opt-in and best-effort refusal-audit limits are appropriate. The proposal separates design review, implementation review and a genuine browser/Core Gate and preserves prior item 5 evidence boundaries.

The above five required findings must be repaired in the three main documents and independently re-reviewed against new SHA-256 values before APPROVE. No implementation is authorized by this review; user confirmation remains the next gate after design approval.
