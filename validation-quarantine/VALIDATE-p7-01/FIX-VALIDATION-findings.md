# FIX-VALIDATION — P7-01 minimal Validation read slice, candidate `f0aea88`

**Validator / model identifier:** `deepseek-flash` (DeepSeek Harness coding agent; delegated
validation session, parent agent id `session-e8a0a737-4ab8-4d61-8232-0f798a7b68f8`).

**Candidate SHA validated:** `f0aea886186a6c1e4704eceb2552c74845f333b2` (branch tip
`p7-01-validation-read` in worktree
`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`; confirmed by `git rev-parse HEAD`
and `git rev-parse p7-01-validation-read`). The accepted contract
`web/contracts/p7-validation-read.v1.json` is pinned to `264163b9406bbb0921ff0edb8692f4d9c7d6dcc8`
(the four-defect fix commit), and `git merge-base --is-ancestor 264163b f0aea88` is **True**.

**Supersedes every earlier validation report** (`findings.md`, `RE-VALIDATION-findings.md`,
`FINAL-VALIDATION-findings.md`). None of their evidence or verdicts is reused. The earlier harness
was copied and re-run from scratch; **every criterion, every probe and the regression suite were
re-executed against `f0aea88`**, and the four defect classes the previous rounds did not cover are
now tested end to end with the filesystem changing under the running server. The tracked tree was
clean at start and end (`git status --porcelain` shows only the untracked
`validation-quarantine/…` tree); nothing was committed or pushed; no tracked file was modified; the
operator's real run tree was only ever read.

All evidence is under `validation-quarantine/VALIDATE-p7-01/evidence-fix/`. Every request was real
loopback HTTP (raw `urllib`) against the real `jasmine_core.api.server.Application` on a real
migrated SQLite DB (fresh `migrate()` per harness instance), in `single_user` **and** `acl`, over
the nine real P0 records plus validator-built fixture roots. No implementation test body was used
as a check.

## Headline result

| # | Criterion | Verdict | Checks |
| --- | --- | --- | --- |
| 1 | Read an existing real FAIL Run (classification + raw output traceable) | **BLOCKED** | 28 |
| 2 | Missing Context/Agent shown as missing, nothing fabricated, no verdict upgrade | **PASS** | 126 |
| 3 | Authorised success and refusal paired on the same resource (5 routes incl. list) | **PASS** | 63 |
| 4 | Artifact negatives: normal, missing, tampered, escape, secret, HTML | **PASS** | 145 |
| 5 | Pagination, corrupt ≠ empty, unreadable subtree, depth/duplicate/bounds/`not_regular_file` | **PASS** | 145 |
| 6 | API values agree with source records and file digests, field by field | **PASS** | 397 |
|  | **Total criterion checks** |  | **904 / 904, 0 failed** |

Supplementary (not one of the six): 9/9 contract probes (`extra-01-probes.json`) and **100/100
defect-class probes** (`fix-defects-report.json`). Every script ran with 0 `[FAIL]` lines:
`grep -c '^\[FAIL\]' *-checks.txt` = 0 for all eight scripts.

### The four fixed defect classes (the new coverage this round)

| Defect class (as reported in `0ae8041`) | Probe | Result on `f0aea88` |
| --- | --- | --- |
| Manifest re-read after the grant check | Part A: swap `manifest.json` after the grant check, sequential + 120-request swap race in `acl` | **PASS** — no response ever mixed one manifest's permission with another's payload |
| `stat`-then-`read_bytes()`: FIFO blocks, growth read whole | Part B/C: FIFO at a record path, at `manifest.json`, at an artifact path; regular↔FIFO record swap race; grown record/manifest | **PASS** — every request answered; over-cap reads refused with no digest |
| Cached resolved root followed after a swap to a symlink | Part D: root replaced by a symlink to an outside directory holding a valid record | **PASS** — list/detail/manifest/artifact all `503 symlinked_root`, nothing served from outside; restored root reads again |
| Masked output never measured against the cap | Part E: raw 4.8 MB, masked 10.2 MB | **PASS** — `413 payload_too_large`, masked body not returned |
| Hardening must not break normal reads | Part F: record 1 and 3 directories down; artifact `artifacts/sub/name.txt` | **PASS** — all serve the exact bytes |

A **negative control** (Part A race + Part B race + Part D + Part E re-run against the superseded
commit `0ae8041`, `src/` extracted read-only with `git archive`) detected **all four classes**
(12 failed checks, including a served `BBB-CONTENT…` payload mixed with a granted permission
decision, a 5.0 s blocked list request, an outside record served 200 through the symlinked root,
and a 10,200,458-byte masked body returned 200). The probes therefore have teeth; they pass on
`f0aea88` because the defects are fixed, not because they are unfalsifiable. The control is
recorded under `evidence-fix/control-old/` and is **not** acceptance evidence for `f0aea88`.

## Commands run (all from `evidence-fix/`)

```
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 fix_analysis_real_records.py 2>analysis-real-records-stderr.txt | tee analysis-real-records.txt
for s in c1_fail c2_missing c3_authz c4_artifacts c5_pages c6_values extra_probes defects; do
  PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 fix_$s.py 2>${s}-stderr.txt | tee ${s}-checks.txt
done
# equivalently: bash fix_run_all.sh > run-all.txt 2>&1
bash fix_fullsuite_freshclone.sh > freshclone-run.txt 2>&1
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 fix_env.py
```

Harness scripts: `fix_analysis_real_records.py`, `fix_harness.py`, `fix_c1_fail.py` …
`fix_c6_values.py`, `fix_extra_probes.py`, `fix_defects.py`, `fix_env.py`, `fix_run_all.sh`,
`fix_fullsuite_freshclone.sh`.

---

## Criterion 1 — real FAIL Run: **BLOCKED** (28/28 checks)

Independently re-confirmed from the bytes, read-only (`analysis-real-records.txt`):
`RUN-SHAPED FILES: 9` and
`AGGREGATE: {"agent_sentinel": 60, "agent_value": 3, "cases": 63, "context_reason_only": 27,
"context_run_details(no pack)": 30, "context_sentinel": 6, "fail_cases": 0,
"failure_class_present": 0, "failure_output_present": 0, "records": 9, "verdict_BLOCKED": 6,
"verdict_PASS": 3}`. So: **0 real FAIL cases, 0 `failure_class`, 0 `failure_output`**; all nine
records are `stage: P0` (schema_version 2 or 4), 3 PASS / 6 BLOCKED.

Over HTTP (`c1-03-real-root-list.json`, 28/28 checks): `total: 9`, no row with `verdict: "FAIL"`,
every row `case_counts.fail == 0`; 3 PASS / 6 BLOCKED. The FAIL code path is exercised **only** by
the clearly-labelled synthetic fixture copied into the validator's own root
(`c1-05-fixture-root-list.json`, `c1-06-labelled-fail-detail.json`): `verdict: "FAIL"`,
`synthetic: true`, `case_counts.fail: 1`, run-level `evidence_grade: "unknown"` (**never `real`**),
the recorded `failure_class`/`failure_output` served byte-identically and the case graded
`simulated`; the fixture id 404s under the operator root (`c1-04`). The operator root still holds
exactly 9 `run-*.json` with mtimes unchanged at `2026-09-29`.

**BLOCKED reason:** there is no existing real P7-format FAIL Run to read. **Unblock action:** the P7
owner records one genuinely failing scenario with `src/jasmine_core/validation/recorder.py` into the
configured run root, after which criterion 1 is re-validated against that real record. The 28
checks that pass confirm the *absence* of a real FAIL run and the labelled-synthetic behaviour; they
are not a PASS for criterion 1.

## Criterion 2 — missing Context/Agent, no fabrication, no verdict upgrade: **PASS** (126/126)

`c2-01-real-details.json`, `c2-02-inconsistent-verdict-detail.json`,
`c2-07-absent-fields-detail.json`, `c2-03-criterion2-report.json`. The context chain link is missing
in all 9 runs / 63 cases in the three real forms (6 sentinel, 27 reason-only, 30 run-details with no
pack); no real case has `context_pack_present: true`. `agent_response` is absent in the six
run-level BLOCKED runs (6 × 7 = 42 cases) and present in the three PASS runs. A fourth form (field
deleted) reports `present_kind: "absent"`, `missing_reason: "the record has no such field"` — nothing
fabricated. Served verdict equals the recorded verdict for all 9 runs / 63 cases;
`verdict_consistent` is **reported, not enforced** (a fixture with recorded run `PASS` and a
BLOCKED-only case is still served `PASS` with `verdict_consistent: false`). No verdict is upgraded.

## Criterion 3 — paired authorisation on the same resource, all five routes: **PASS** (63/63)

`c3-01` … `c3-06`. All five routes (`/runs`, `/runs/{id}`, `/runs/{id}/manifest`,
`/runs/{id}/artifacts/{aid}`, `/runs/{id}/artifacts/{aid}/content`) driven as pairs:
`single_user` granted `validation:read` → 200 on all five; `objects:read`-only key → **403
`forbidden_scope`** with `details.required == "validation:read"`; no token → **401
`unauthenticated`**. `acl`: granted key → 200; scope-less key → 403; a run attributed to a
non-granted project → 404 `validation_run_not_found`; an unattributed run without
`validation:unattributed` → 404 and with it → 200; an actor with `validation:read` but no grant →
404. The `acl` list is grant-filtered (`visibility: "grant_filtered"`, only the granted row,
`total == 1`). No validation route ever emits `project_not_found`.

## Criterion 4 — artifact negatives: **PASS** (145/145)

`c4-01` … `c4-19`. Normal (`integrity: "match"`, digests equal to the validator's SHA-256 of the
bytes on disk), missing (404, `reason: "missing"`), tampered byte-flip (409 `artifact_modified`),
six escape shapes refused at manifest load with the offending entry named,
symlinked final file and symlinked intermediate directory refused at open (`reason:
"symlink_refused"`), assignment-shaped secret masked vs withheld (`403 original_withheld`,
`reason: "mask_not_noop"`), declared-credential withheld, HTML/script `download=1` returned only as
a JSON envelope with `nosniff` + CSP + `Content-Disposition: attachment` for both forms, non-text
415. Raw declared-path rules refuse `a//b`, `a/./b`, `a/b/`, `./a`, `.`, `artifacts/note..txt`,
`a..b`, `artifacts/..hidden`, `artifacts/../loot.txt`, `..%2Fb`, while **`artifacts/name.txt` and
`artifacts/sub/name.txt` still serve `match` + content** (`c4-19-raw-declared-paths.json`).

## Criterion 5 — pagination, corrupt ≠ empty, unreadable subtree, bounded/non-regular reads: **PASS** (145/145)

`c5-01` … `c5-24`. Full page walk over the real nine with `limit=2` (5 pages, union exactly the nine
stems, no repeats, stable `total`, page arithmetic holds) and in `acl` with
`validation:unattributed`; a 12-row fixture root walked with `limit=4` equals the single 100-limit
page. Corrupt records are listed (`verdict: null`, named `corrupt_reason`) and never disguised as
`no_run_shaped_files`; a truly empty root is. Unreadable subtree → 503 `index_incomplete` with the
count and a ≤20-sample list, while a sibling by-id read still 200. Duplicate resolution across every
combination in `single_user` and `acl`, with list `record_status` predicting the detail answer.
Depth boundary (directory 4 levels down inside, 5 pruned and counted `depth_cap`), counters describe
the filtered served set, `files`/record caps refuse oversize reads with no digest. The non-regular
rule is covered by a real **FIFO** and a real **Unix socket** named `run-*.json`: both answered in
≤ 1.8 ms, counted `not_regular_file`, never served.

## Criterion 6 — API values agree with source records and file digests: **PASS** (397/397)

`c6-08-field-comparison-table.json` (373 field-by-field comparisons, 0 mismatches) plus 24
consistency checks. List and detail fields (`run_id`, `stage`, `created_at`, `commit`,
`schema_version`, `core_version`, `verdict`, `case_counts{…}`, `record_sha256`, `synthetic`,
`manifest_status`, per-case `verdict`/`failure_class`/`failure_output`) recomputed by the validator
from the bytes on disk; fixture-bundle `manifest_sha256`, declared/actual artifact
`sha256`/`bytes`, `digests.original`/`digests.masked`, `integrity`, and content
`original_sha256`/`masked_sha256`/`served_sha256`, re-derived as `sha256(raw)` and
`sha256(canonical.redact_text(text))`. An absent manifest is reported `status: "absent"` with **no**
`manifest_sha256` — nothing fabricated.

---

## The four fixed defect classes, end to end under a changing filesystem

Evidence: `fix-defects-01` … `fix-defects-08`, `fix_defects-checks.txt`,
`fix-defects-report.json` (100/100). Requests: 195; max 1.7246 s; 0 without a status; 0 transport
retries.

**1. One manifest read per request (`fix-defects-01-manifest-swap.json`).** In `acl`, run
`run-20261008T000501Z` has two manifests: A (`projects=[granted project]`, `art_good` →
`artifacts/a.txt`, bytes `AAA-CONTENT…`) and B (`projects=[ungranted project]`, `art_good` →
`artifacts/b.txt`, bytes `BBB-CONTENT…`). With A on disk, the grant check was exercised: content
200 with A's bytes and A's served digest; manifest 200 with `manifest_sha256 == sha256(A)` and
`files[0].path == "artifacts/a.txt"`. After atomically replacing the manifest with B, both routes
return **404 `validation_run_not_found`** and serve nothing from B. Swapping back to A restores
200/A. A background thread then swapped A↔B at ~1 243 swaps across 120 requests: 29 content-200,
31 content-404, 28 manifest-200, 32 manifest-404, and **0 mixed responses** — every 200 carried A's
bytes/digest/path (the manifest read once), every 404 served nothing. On the superseded `0ae8041`
the same race produced `content-200-mixed` with `BBB-CONTENT-SERVED-FROM-MANIFEST-B` — a granted
decision from A with B's payload — which is exactly the defect class now closed.

**2. FIFO at a record path, at `manifest.json`, and at an artifact path
(`fix-defects-02-fifo.json`).** Three real FIFOs (`stat.S_ISFIFO` verified) in one root alongside
regular records:
* list answered in **0.0018 s** (200, `total: 3`); the FIFO record is not a row and is counted with
  reason `not_regular_file`;
* detail of the FIFO stem answered in **0.0012 s** with 404 `validation_run_not_found`;
* the FIFO `manifest.json` answered in **0.0015 s** with 409 `validation_manifest_corrupt`, rule
  `manifest.json is not a regular readable file: not_regular_file`;
* the FIFO artifact answered in **0.0016 s / 0.0015 s** on metadata and content with 503
  `artifact_unreadable`; the manifest route lists it `integrity: "unreadable"`.
A regular↔FIFO **record swap race** (83 swaps, 15 list requests) produced no block: all 200, max
0.0043 s, every swapped-in FIFO refused as not a regular file. On `0ae8041` the same race hit a
`TimeoutError` after 5.0013 s (status `null`) — the recorded client-timeout hang.

**3. Growth past the caps is refused, never read whole (`fix-defects-03-cap-growth.json`).** A
record grown past `MAX_RECORD_BYTES = 16 MiB` (16 777 216 bytes, still valid JSON) is a `corrupt`
list row with `record_sha256: ""` and reason `record exceeds 16777216 bytes`, and detail 409
`validation_run_corrupt`. A `manifest.json` grown past `MAX_MANIFEST_BYTES = 1 MiB` is 409
`validation_manifest_corrupt`, rule `manifest exceeds 1048576 bytes`, with **no `manifest_sha256`**;
its list row is `manifest_status: "corrupt"`. Restoring both restores normal reads. The in-loop
primitive was exercised directly: a descriptor grown by one byte after open/fstat is refused
`too_large` by `_read_capped`, while a file exactly at the cap is still read. A targeted
grow-during-read race (18 iterations sweeping the append across the read window) produced 12 `ok`
rows carrying only the pre-growth digest and 6 over-cap refusals, **0 violations**, max 0.0177 s.
Note: the record over-cap case keeps the pre-existing `stat`-based observable from criterion 5; the
new in-loop check is what closes the grow-*after*-the-check window, and it is asserted by the
descriptor-level primitive plus the race invariant (no `ok` row ever carried a grown digest).

**4. Root replaced by a symlink to an outside directory (`fix-defects-04-root-symlink.json`).** The
inside root was first served normally (list 200 with the inside id, detail 200). The root directory
was then renamed away and replaced by a symlink to an outside directory holding a valid record +
manifest + artifact (`OUTSIDE-SECRET-CONTENT`). **Every** route — list, inside detail, outside
detail, outside manifest, outside artifact content — returned **503 `validation_not_configured`,
`details.reason: "symlinked_root"`** in ≤ 0.0013 s and served **nothing** from outside
(`served_outside: false` for all five). Removing the symlink and restoring the real directory made
the list 200 with the inside id again, the inside detail 200, and the inside artifact content 200
with the exact bytes. On `0ae8041` the cached root was followed: the list returned 200 and served
the outside record, and outside detail/manifest/artifact all returned 200.

**5. Masked form measured against `MAX_ARTIFACT_BYTES` (`fix-defects-05-masked-cap.json`).** The
masker expands: raw `b"token=x " * 600000` = **4 800 000 bytes** (< 8 388 608) becomes
**10 200 000 bytes** when masked. The metadata route is 200 `integrity: "match"` with
`digests.masked` equal to the validator's own `sha256(redact_text(text))`. The masked content route
is **413 `payload_too_large`** with `details: {"form": "masked", "limit": 8388608,
"original_bytes": 4800000}` and a **216-byte** response carrying no `content`; `form=original` is
403 `original_withheld`. A control artifact that expands but stays under the cap is served 200 with
the correct masked digest. On `0ae8041` the masked route returned 200 with the full 10 200 458-byte
body.

**6. Legitimate deep reads survive (`fix-defects-06-legit-deep.json`).** A record three directory
levels below the root and one one level below both list, serve detail 200, serve a manifest whose
declared path is `artifacts/sub/name.txt` with `integrity: "match"`, and serve the nested content
200 with the exact bytes and digest (≤ 0.0024 s). Criterion 4's `c4-19-raw-declared-paths.json`
independently confirms `artifacts/name.txt` and `artifacts/sub/name.txt` are served `match` while
the escape forms are refused.

## Explicit no-hang statement, with timings

The harness times **every** request (`[timing] #n METHOD path seconds status`) and each script emits
a `[timing-summary]`; `fix-defects-07/08` and `c5-22` hold the raw lists.

| Script | Requests | Max seconds | Requests without status | Transport retries | Over 5 s |
| --- | --- | --- | --- | --- | --- |
| c1 | 4 | 0.0130 | 0 | 0 | 0 |
| c2 | 12 | 0.0101 | 0 | 0 | 0 |
| c3 | 30 | 0.0037 | 0 | 0 | 0 |
| c4 | 56 | 0.0068 | 0 | 0 | 0 |
| c5 | 46 | 0.0128 | 0 | 0 | 0 |
| c6 | 17 | 0.0112 | 0 | 0 | 0 |
| extra probes | 5 | 0.0064 | 0 | 0 | 0 |
| defect probes | 195 | 1.7246 | 0 | 0 | 0 |
| **Total** | **365** | **1.7246** | **0** | **0** | **0** |

**Explicitly: no request hung.** All 365 requests returned an HTTP status inside their client
timeout; the single slowest response (1.7246 s) is the deliberately crafted 4.8 MB → 10.2 MB
masked-expansion probe, not a block; the FIFO, socket, swap-race and symlinked-root probes were each
answered in ≤ 5 ms. The superseded commit, by contrast, blocked a list request for the full 5 s
client timeout on the same regular↔FIFO race.

## Regression — fresh clone of `f0aea88`

`fix_fullsuite_freshclone.sh` → `freshclone-run.txt`, `fullsuite-candidate-module.txt`,
`fullsuite-candidate.txt`, `fullsuite-baseline.txt`. `git clone --no-hardlinks` of the shared repo,
`git checkout f0aea88`, clone `HEAD = f0aea886186a6c1e4704eceb2552c74845f333b2`, `git status
--porcelain` empty; `PYTHON=python3.13`, `P7_REAL_RUNS_ROOT` pointed at the operator's real root.

| Run | Result |
| --- | --- |
| Candidate module `tests/test_validation_read.py` | **Ran 53 tests in 2.027s — OK** |
| Candidate **full** suite (`PYTHON=python3.13 ./scripts/run-tests.sh`) | **Ran 1419 tests in 45.202s — FAILED (failures=5, errors=4, skipped=13)** |
| Baseline `0f7b2ec` (parent of the phase-2 implementation) full suite | Ran 1366 tests in 43.458s — FAILED (failures=5, errors=4, skipped=13) |

The failure set is **identical** between candidate and baseline: `diff` of the `FAIL:`/`ERROR:`
lines is empty, both have the same nine tests, all in the **pre-existing P4 environment set** —
hook-installer / native-session tests failing with `canonical venv launcher parent required`
(8 occurrences in each transcript) because the clone's `python3.13` is not a canonical-venv
launcher. They are environmental and pre-existing, **not introduced by P7-01**. The candidate adds
53 tests over the 1366-test baseline (all passing) and the P7 module is 53/53.

## Environment record

Full record: `env-fix-environment.txt`.

| Item | Value |
| --- | --- |
| Time (UTC) | 2026-10-08T16:51Z |
| Candidate | `f0aea886186a6c1e4704eceb2552c74845f333b2`, branch `p7-01-validation-read` |
| Contract | `p7-validation-read.v1`, `source.ref 264163b…`, ancestor of the candidate: **True** |
| Core version / schema version | `0.1.0` / `11` (`migrations.check_version`; `PRAGMA user_version` is not the schema counter) |
| `canonical.REDACT_TEXT_VERSION` | `1` |
| Python | `/opt/homebrew/bin/python3.13` (3.13.15) |
| Platform | macOS-27.0.1-arm64 |
| Modes exercised | `single_user` **and** `acl` |
| Real root (read-only) | `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs` — 9 regular `run-*.json`, digests and `2026-09-29` mtimes recorded, unchanged |
| Fixture roots | per-run `TemporaryDirectory(prefix="validate-p7-01-")`; one short AF_UNIX root `/tmp/v7f-*`; a read-only `git archive 0ae8041 src` extraction under `/tmp` for the negative control |
| Scope sets (token values never recorded) | `validation:read`; `validation:read` + `validation:unattributed`; `admin`+`objects:read`+`objects:write` (project creation only); `objects:read` (scope-less key) |
| Tracked tree | 0 modified tracked files; nothing committed/pushed |

## Counterexamples and defects

**None affecting the six acceptance criteria.** 904/904 criterion checks passed, plus 100/100
defect probes and 9/9 supplementary probes; 0 `[FAIL]` lines across all eight scripts. No
counterexample was found in the four fixed defect classes.

Observations (informational; no severity; no code change implied, all carried over and re-confirmed
on this candidate):

1. **The literal `..`-substring rule is broader than a segment check** (`artifacts/note..txt` is
   refused). It is the proposal's own literal wording and fail-safe; the spec owner may wish to say
   whether a `..` inside an ordinary component is intended to be allowed.
2. **`acl` withholds `ignored_samples[]` (count only)**, per ADR 0015 §5.1 AD2, so the
   `not_regular_file` *reason* is observable only in `single_user` while `acl` reports the count.
3. **By-id unknown ids over an incomplete tree return 503 `index_incomplete`** rather than 404
   (ADR 0015 §5.0) and `acl` + `validation:unattributed` can reach the 409 duplicate/corrupt family;
   both behave exactly as documented, with fail-closed 404 without the grant.
4. **Duplicate rows legitimately carry no `mode`/`attribution`/`evidence_grade`**, a fail-closed
   consequence of the duplicate rule, documented in ADR 0015 §5.3.
5. **The manifest-corrupt rule text changed** with the fix: a FIFO `manifest.json` now reports
   `manifest.json is not a regular readable file: not_regular_file` instead of
   `manifest.json is not a regular file`. Both refuse and neither blocks; the wording is merely more
   specific. Noted because a consumer matching the old string would need to update.

Validator corrections recorded for transparency:

1. My first Part D check compared `os.path.realpath(root)` with the raw outside path and failed on
   macOS because `/var` is itself a symlink to `/private/var`. That was **my expectation bug**, not
   an implementation defect; I corrected the check to compare realpaths and re-ran the whole
   battery. The implementation had already returned the correct `503 symlinked_root`.
2. My first Part C evidence capture stored the list response's status under the `grown_manifest`
   key instead of the manifest response's; the check itself asserted 409 at the time and passed. I
   corrected the capture and re-ran the whole battery so the raw evidence matches the assertion.

## What this validation covers and does not cover

* **Covered:** the five `GET` routes of the minimal read slice over a real loopback socket against a
  real migrated DB, in `single_user` and `acl`, on the nine real P0 records and validator-built
  fixture roots — criteria 2–6 (904/904), the four fixed defect classes under a filesystem changing
  beneath the running server (100/100), supplementary contract probes (9/9), and the candidate
  module and full suite from a fresh clone at the candidate SHA.
* **Not acceptance-covered:** **criterion 1 remains BLOCKED** — the nine real records contain 0 FAIL
  cases, 0 `failure_class` and 0 `failure_output` — and stays BLOCKED until the P7 owner records one
  genuinely failing scenario with `recorder.py` into the configured run root. The labelled synthetic
  FAIL fixture exercises only the code path and is never counted as criterion-1 evidence.
* **Not accepted here:** this report does **not** accept the P7 stage, the Scenario Runner, the
  30-scenario suite, any release verdict, or the P6-T06 browser drill-down. Only the read slice
  described by `web/contracts/p7-validation-read.v1.json` is inside scope.
* **Residual limitation:** defect class 2's grow-*during*-read window is proven deterministically at
  the `_read_capped` descriptor level and by a race invariant that never served a grown record; the
  race is probabilistic in *which* branch fires and is reported as such.

## Reproducing this validation

```
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/VALIDATE-p7-01/evidence-fix"
bash fix_run_all.sh                 # census + six criteria + supplementary + defect probes
bash fix_fullsuite_freshclone.sh    # fresh clone: module test + full suite + baseline suite
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 fix_env.py
# negative control (superseded 0ae8041, not evidence for f0aea88):
/opt/homebrew/bin/python3.13 control-old/fix_control_old.py
```

**Bottom line:** on `f0aea88`, criteria 2–6 are **PASS** (904/904), the four fixed defect classes are
closed end to end (100/100, and a negative control on `0ae8041` detects all four), no request hung
(365 requests, max 1.72 s, 0 timeouts), and the regression failure set is identical to the
pre-existing P4 environment set. **Criterion 1 remains BLOCKED** pending a real P7-format FAIL run.
