# P7-01 phase report — the minimal Validation read slice

**Status: delivered and independently gated.** The phase-1 contract proposal was frozen after five
independent reviews; the implementation was then reviewed three times and validated three times, each in a
separate agent context. The **final candidate** is

```
0ae80411824739b11485c61ed388d86093f2be7c
```

on branch `p7-01-validation-read` in worktree `_worktrees/p7-01`.

| Role | Agent context | Model | Result |
| --- | --- | --- | --- |
| Proposal author, implementer, coordinator | this session | `deepseek-flash` | — |
| Proposal reviews (5 rounds) | fresh context each round | `deepseek-flash` | final round **APPROVE (freeze as-is)** |
| Implementation reviews (3 rounds) | fresh context each round | `deepseek-flash` | final round **APPROVE** |
| Acceptance validations (3 rounds) | fresh context each round | `deepseek-flash` | final round **904/904 checks**, criteria 2–6 PASS, criterion 1 BLOCKED |

`gpt-6.1-sol` was unreachable in this deployment, so every independent role ran on `deepseek-flash`; that
is recorded as a limitation, not hidden.

## 1. What was delivered

Five `GET` routes that read the Validation run records the existing recorder already wrote, serving the
recorded verdict unchanged and naming what each record is missing. Nothing is written, no verdict is
re-signed, and no artifact is fabricated.

| Route | Purpose |
| --- | --- |
| `GET /v1/validation/runs` | run list: identity, pagination, filters, recorded verdict, mode, evidence grade, attribution, missing manifest |
| `GET /v1/validation/runs/{run_id}` | recorded detail, case by case, with the eight chain links and per-section grades |
| `GET /v1/validation/runs/{run_id}/manifest` | manifest status, sources, file list, digests, integrity |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}` | artifact metadata, no content |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}/content` | text content, masked by default, digest-pinned |

Deliverables, all bound to the final candidate:

| Artifact | Path |
| --- | --- |
| Frozen, independently approved specification | `web/P7-01-CONTRACT-PROPOSAL.md` (revision 5) |
| Derived machine-checked contract | `web/contracts/p7-validation-read.v1.json` (`source.ref` = `46a33d78d03e4d8d9711c96ded62072de8a276be`) |
| Contract generator | `scripts/p7/p7_contract.py` (`--print`/`--write`/`--check`/`--derived-at`; `--check` exits 2 when it cannot look) |
| Reader | `src/jasmine_core/validation/reads.py` |
| Decisions, codes, invariants | `docs/adr/0015-p7-validation-read-contract.md` |
| API documentation | `docs/api/core-api-v1.md` (`## P7-01 Validation read extension`) |
| Tests | `tests/test_validation_read.py` (48 tests), `tests/fixtures/p7/` |
| Evidence index | `web/P7-01-EVIDENCE-INDEX.md` |

## 2. Commit history (all on `p7-01-validation-read`; `main` untouched at `b9985e2`)

| Commit | What |
| --- | --- |
| `039f5a3` | baseline: `integration/p6-03-realcore` |
| `3ebe46c`, `f397213`, `c8fd597`, `1b9791a` | proposal revisions 1–4, each reviewed |
| `0f7b2ec` | proposal revision 5 — **approved** by the fifth review |
| `35f7e01` | implementation |
| `2eb1e5a` | fixture files moved out of the gitignored `artifacts/` and `*.log` paths |
| `087b298` | derived contract frozen, generator `--ref` default fixed |
| `1f7b0af` | real-record tests follow `P7_REAL_RUNS_ROOT` |
| `f0be738` | implementation review F1–F9 |
| `a252097` | contract re-pinned |
| `46a33d7` | re-review N3 (non-regular run-shaped entries) and N5 |
| `0ae8041` | **final candidate** — contract re-pinned to `46a33d7` |

A contract cannot record the SHA of the commit that contains it, so the snapshot pins the commit it was
*derived from* and `--check` re-derives at that pin. This is the same stamp-lag arrangement the P6
contracts use.

## 3. Acceptance results on the final candidate

Validated by an independent agent over real loopback HTTP against a real migrated database, over the nine
real records plus validator-built fixture roots, in both `single_user` and `acl` modes: **904/904 checks,
0 failed, 0 `[FAIL]` lines, 170 timed requests, max 12.5 ms, nothing hung.**

| # | Criterion | Verdict | Checks |
| --- | --- | --- | --- |
| 1 | Read an existing real FAIL Run, classification and raw output traceable | **BLOCKED** | 28 |
| 2 | Missing Context/Agent shown as missing, no fabrication, no verdict upgrade | **PASS** | 126 |
| 3 | Authorised success and refusal paired on the same resource (all five routes) | **PASS** | 63 |
| 4 | Artifact normal / missing / tampered / escape | **PASS** | 145 |
| 5 | Pagination without duplicates or omissions; a corrupt record is not an empty list | **PASS** | 145 |
| 6 | API values agree with the source records and the file digests | **PASS** | 397 |

**Criterion 1 is BLOCKED, on the operator's decision and on the data.** The nine real records on disk are
all `stage: P0`, 3 PASS / 6 BLOCKED at run level, 63 cases with **0 FAIL cases, 0 `failure_class` values
and 0 `failure_output` values**; the validator recomputed that census independently. The FAIL code path is
exercised end to end only by a fixture that labels itself `synthetic: true` and is served from a
validator-owned root, never from the operator's; its id does not exist under the real root, it is never
graded `real`, and it is never counted as evidence. **Unblock: the P7 owner records one genuinely failing
scenario with `recorder.py` into the configured run root**, after which criterion 1 is re-validated against
that real record. Until then `P6-T06` and P6-03c cannot close.

Selected evidence the validator produced:

* Context is missing for **all 63 cases** in three distinct recorded forms — 6 sentinel, 27
  `{"reason": …}`-only, 30 "recorded run details with no context pack" — each with its own frozen
  `missing_reason`; `agent_response` is missing in all 42 cases of the six BLOCKED runs and present in the
  three PASS runs; Extraction and Resolution have **no recorded source at all**.
* The served verdict equals the file's verdict for all nine runs and 63 cases; `verdict_consistent` is
  reported, never used (a fixture whose run verdict contradicts its cases is still served as recorded).
* In `acl` mode an ungranted project, an unattributed run without `validation:unattributed`, a corrupt
  record and a nonexistent id all return one identical `404 validation_run_not_found`; `project_not_found`
  is never emitted. The list is grant-filtered, so the run appears in neither `runs[]` nor `total`.
* Escapes refused: `../`, absolute, `C:`, UNC, URL, a symlinked file and a **symlinked intermediate
  directory**; a log whose only secret is assignment-shaped (`db_password=…`) is masked by `form=masked`
  and withheld by `form=original` even though `looks_like_credential` returns False for it; HTML requested
  with `download=1` is a JSON envelope with `nosniff` and a sandbox CSP, never a renderable page.
* `record_sha256`, `manifest_sha256`, `declared_sha256`, and the masked and original digests were
  recomputed from disk and compared field by field: 373 comparisons, 0 mismatches.

## 4. What the independent reviews changed

The gates were not ceremonial. Across the eight review rounds, the following were found and fixed before
the final candidate existed:

* **Proposal (rev 1 → rev 5).** Undefined discovery (no single directory held the nine records); a
  credential scan *weaker* than the masker it accompanied (assignment-shaped secrets would have been
  served as original); project isolation returning `403` (an enumeration oracle) and then, after the first
  fix, a subtler oracle from two different `404` codes; an invented fifth evidence grade; a context
  presence rule that counted reason-only objects as packs; and an unreachable/inconsistent error
  vocabulary. Each revision was re-reviewed; the fifth returned APPROVE.
* **Implementation (three rounds).** One mandatory defect: for a stem shared by a parseable and a corrupt
  record the list certified `ok` while detail refused the same id — the two routes now share one
  predicate, proven against an eight-combination matrix. Then the depth cap off by one (a record four
  levels below the root was never found), declared paths that `pathlib` normalised past the component
  rules, manifests read whole before their size cap, counters that ignored the caller's filters, and a
  **FIFO named `run-*.json` that made every request hang** (§2 says only regular files are considered).
  Each fix landed with a regression test; the new tests were shown to fail against the unfixed reader.

## 5. Advisory follow-ups (not defects in this slice)

Recorded so they are not lost; none blocks the six criteria.

| # | Observation | Disposition |
| --- | --- | --- |
| N1 | A declared path containing a literal `..` substring (`artifacts/note..txt`) is refused, which is broader than a per-segment test. | Kept: the specification says "contains no `..`", it fails closed, and the descriptor-relative artifact walk independently rejects `..` components. ADR 0015 §5.3. |
| N2 | A run record over `MAX_RECORD_BYTES` (16 MiB) is reported corrupt with `record_sha256: ""`. | Kept and documented (ADR 0015 §5.2); it is what "no route can read unbounded data" requires, and a real record is a few kilobytes. |
| N4 | Duplicate rows carry no `mode`/`attribution`, so a `mode`/`attribution` filter never matches them. | By design: a stem with more than one parseable member is refused rather than resolved. ADR 0015 §5.3. |
| A1 | `Path.is_file()` swallows `OSError`, so a stat failure is labelled `not_regular_file` rather than `corrupt`. | Fail-closed and cosmetic; a follow-up could distinguish the two reasons. |
| A2 | The regular-file rule is a check, not a lock: a concurrent swap of a regular file for a FIFO could still block one request. | Pre-existing and optional hardening (`O_NONBLOCK` on the record open). |
| A3 | ADR 0015 §5.3's heading says "two deliberate over-rejections" while it lists three bullets (the third is a spec-conformant rule, not an over-rejection). | Wording only. |

## 6. Boundaries honoured

No modification of any frozen P6/P4/P3 evidence, the recorder, `tests/test_validation.py`, the operator's
services, global configuration or V0. No migration and no schema change. No new Core public ID prefix. The
shared-file changes are exactly those on the approved list: `errors.py` (seven code classes), `auth.py`
(two scopes plus an agent-key guard), `canonical.py` (`REDACT_TEXT_VERSION`, additive), `api/handlers.py`
(one route-table line), `docs/api/core-api-v1.md`, and the new ADR 0015. Nothing was pushed, no PR was
opened, no CI was run, and `main` remains at `b9985e21f7971079e1a0b3d4efa87ee9b1aede3d`. The nine real
run records were only ever read (their mtimes are unchanged).

## 7. Residual risk

1. **Criterion 1 is unmet by data, not by code.** Until a real P7-format FAIL run exists, the FAIL
   classification is proven only against a labelled fixture.
2. **The nine records' provenance is the operator's claim.** Their paths and commits label them as real;
   nothing in the files proves a genuine conversation produced them.
3. **Unmarked mocked content is undetectable** by a read-only contract. Grading is marker-based; the API
   says so and never asserts `real` on its own authority. Closing this needs the recorder to write a
   per-case grade.
4. **The manifest is unauthenticated.** `frozen_at`, `mode` and `credential_shaped` are self-declared; the
   API reports `manifest_authentication: "unauthenticated"` rather than implying otherwise.
5. **`P6-T06` remains a separate P6 browser drill-down**, and the dashboard proxy allowlist for
   `/v1/validation/…` (a `p7_pending_paths` list plus a `P6_ENABLE_P7_READS` flag in
   `web/server/core-paths.json`, exactly as P3 and P4 did) is P6-03c's change, requested in the proposal's
   §11.
