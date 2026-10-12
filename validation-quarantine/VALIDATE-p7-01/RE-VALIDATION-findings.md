# RE-VALIDATION — P7-01 minimal Validation read slice, candidate `a252097`

**Validator / model identifier:** `deepseek-flash` (DeepSeek Harness coding agent; DSH session
`f1bac6c2-768e-40fc-a997-07dc59dc7e53`).

**Candidate SHA validated:** `a252097bbc46763e7920018649ccac5dad5c06fe` (branch tip
`p7-01-validation-read`, worktree `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`).
Tracked tree clean at start and end (`git status --porcelain` shows only the untracked
`validation-quarantine/…` directories). Nothing was committed or pushed; no tracked file was modified.

**This supersedes the `1f7b0af` report** at `validation-quarantine/VALIDATE-p7-01/findings.md`. That
report was read only to reuse the validator harness; none of its evidence or verdicts is reused here.
Every check below was re-run from scratch against `a252097`.

**Specification validated against:** `web/P7-01-CONTRACT-PROPOSAL.md` (frozen) and the accepted contract
`web/contracts/p7-validation-read.v1.json` (contract `p7-validation-read.v1`, `source.ref`
`f0be738fb4460581733231a7b4f513272ee4f52b`, independently confirmed an ancestor of the candidate HEAD).

All raw output is under `validation-quarantine/VALIDATE-p7-01/evidence-revalidation/`; the validator
harness and criterion scripts (`re_harness.py`, `re_c1_fail.py` … `re_c6_values.py`, `re_extra_probes.py`)
are there too. Every request was real loopback HTTP against the real `jasmine_core.api.server` on a real
migrated SQLite DB (fresh `migrate()` per harness instance) — not a mocked call and not the
implementation's test body. The real nine records were served read-only; all negatives used
validator-built fixture roots under a per-run `TemporaryDirectory`.

## Headline result

| # | Criterion | Verdict | Checks (all passed) |
| --- | --- | --- | --- |
| 1 | Read an existing real FAIL Run (classification + raw output traceable) | **BLOCKED** | 28 |
| 2 | Missing Context/Agent shown as missing, nothing fabricated, no verdict upgrade | **PASS** | 126 |
| 3 | Authorised success and refusal paired on the same resource (5 routes incl. list) | **PASS** | 63 |
| 4 | Artifact negatives: normal, missing, tampered, escape, secret, HTML | **PASS** | 145 |
| 5 | Pagination + corrupt ≠ empty + unreadable subtree + new depth/duplicate/bounds/counters | **PASS** | 120 |
| 6 | API values agree with source records and file digests, field by field | **PASS** | 397 |
|  | **Total criterion checks** |  | **879 / 879** |

Supplementary (not one of the six): 9/9 contract probes (`extra_probes-checks.txt`, `extra-01-probes.json`).
The implementation's own module `tests/test_validation_read.py` was run for corroboration (47/47 OK) but is
**not** the basis of any verdict.

### New-in-this-candidate behaviours, all independently confirmed

| Behaviour | Result | Evidence |
| --- | --- | --- |
| Parseable + corrupt stem → list serves both rows, detail 200 serves the parseable record; list `record_status` agrees with detail | PASS | `c5-13-…json`, `c5-16-acl-combos.json` |
| Two parseable members → `409 validation_run_ambiguous` (list: two `duplicate` rows) | PASS | `c5-13-…json` |
| Only-corrupt members → `409 validation_run_corrupt` (list: `corrupt` rows); acl + `validation:unattributed` also reaches 409 | PASS | `c5-13-…json`, `c5-16-acl-combos.json` |
| Directory 4 levels below root is found; 5 levels is pruned and counted `depth_cap` | PASS | `c5-12-depth-boundary.json` |
| `a//b`, `a/./b`, `a/b/`, `./a`, `.`, `..%2Fb` and any literal `..` substring make the manifest `corrupt` | PASS | `c4-19-raw-declared-paths.json` |
| Legitimate `artifacts/name.txt` and `artifacts/sub/name.txt` still serve `match` + content | PASS | `c4-19-raw-declared-paths.json` |
| Manifest > 1 MiB refused by `stat` before read, no `manifest_sha256` reported; artifacts unaddressable | PASS | `c5-18-oversize-manifest.json` |
| Run record > 16 MiB `corrupt` with `record_sha256: ""` (never read) | PASS | `c5-17-bounded-reads-list.json` |
| `corrupt_count`/`duplicate_count` describe the filtered served set | PASS | `c5-14-counters-verdict-pass.json`, `c5-15-counters-filtered-empty.json` |

## Environment

Record: `env-01-environment.txt`.

| Item | Value |
| --- | --- |
| Time (UTC) | 2026-10-08T14:52Z |
| Core version | `0.1.0` |
| Schema version | `11` (`migrations.check_version`; `PRAGMA user_version` is not the schema counter) |
| `canonical.REDACT_TEXT_VERSION` | `1` |
| Python used | `/opt/homebrew/bin/python3.13` (3.13.15); default `python3` is 3.9.6 and cannot import the package |
| Modes exercised | `single_user` **and** `acl` |
| Real root configured | `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs` (env `JASMINE_CORE_VALIDATION_RUNS`) |
| Fixture roots | validator temp roots under `TemporaryDirectory(prefix="validate-p7-01-")`; never the operator root |
| Scopes used (values never recorded) | `validation:read`, `validation:unattributed`; `admin`+`objects:read`+`objects:write` only to create ACL projects; `objects:read` as the deliberately scope-less key |
| Real nine unchanged | read-only; SHA-256 and mtimes recorded in `env-01-environment.txt` (mtimes still `2026-09-29`) |

Commands run (from `validation-quarantine/VALIDATE-p7-01/evidence-revalidation/`):

```
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_analysis_real_records.py
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_c1_fail.py     2>c1_fail-stderr.txt     | tee c1_fail-checks.txt
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_c2_missing.py  2>c2_missing-stderr.txt  | tee c2_missing-checks.txt
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_c3_authz.py    2>c3_authz-stderr.txt    | tee c3_authz-checks.txt
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_c4_artifacts.py 2>c4_artifacts-stderr.txt | tee c4_artifacts-checks.txt
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_c5_pages.py    2>c5_pages-stderr.txt    | tee c5_pages-checks.txt
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_c6_values.py   2>c6_values-stderr.txt   | tee c6_values-checks.txt
PYTHONPATH="../../../src:." /opt/homebrew/bin/python3.13 re_extra_probes.py 2>extra_probes-stderr.txt | tee extra_probes-checks.txt
```

(equivalently `bash re_run_all.sh`; the recorded run had 0 transport-level retries — see "Validator
correction" below).

---

## Criterion 1 — real FAIL Run: **BLOCKED**

### (a) Independently confirmed: no real P7-format FAIL run exists

`re_analysis_real_records.py` (read-only census) result, `RUN-SHAPED FILES: 9`:

```
AGGREGATE: {"agent_sentinel": 60, "agent_value": 3, "cases": 63,
  "context_reason_only": 27, "context_run_details(no pack)": 30, "context_sentinel": 6,
  "fail_cases": 0, "failure_class_present": 0, "failure_output_present": 0,
  "records": 9, "verdict_BLOCKED": 6, "verdict_PASS": 3}
```

Independently confirmed from the bytes: **9 records, all `stage: P0` (schema_version 2 or 4),
3 PASS / 6 BLOCKED at run level, 63 cases, 0 cases with `verdict == FAIL`, 0 `failure_class`
values, 0 `failure_output` values.** No real P7-format FAIL run exists on disk. The API agrees:
`c1-03-real-root-list.json` serves `total: 9`, no row with `verdict: "FAIL"`, every row
`case_counts.fail == 0`, 3 PASS / 6 BLOCKED (28/28 checks in `c1_fail-checks.txt`).

### (b) The FAIL code path is exercised only by a clearly labelled synthetic fixture

The validator copied `tests/fixtures/p7/labelled-fail-run.json` into its **own** fixture root and drove
the real HTTP API against it:

* `c1-05-fixture-root-list.json` — the fixture row serves `verdict: "FAIL"`, `synthetic: true`,
  `case_counts.fail: 1`; run-level `evidence_grade` is `unknown` (the documented min of the
  absent-manifest `unknown` effective mode and the `simulated` case, §4.2), **never `real`**.
* `c1-06-labelled-fail-detail.json` — the case's `failure_class: "UI_FAILURE"` and
  `failure_output: "expected 200 got 500"` are byte-identical to the file; the case grade is
  `simulated` with `grade_basis` naming the synthetic run; `synthetic_note` is surfaced verbatim;
  the recorded verdict is served unchanged.
* The fixture id `404 validation_run_not_found` under the operator's real root
  (`c1-04-fixture-id-under-real-root.json`); the operator root still holds exactly 9 `run-*.json`
  files and still lists exactly nine with no FAIL row; the fixture file resolves outside the operator
  root and self-labels `stage: P7`, `synthetic: true`.

### (c) BLOCKED reason and unblock action

**Criterion 1 is BLOCKED: there is no existing real P7-format FAIL Run to read.** The unblock action is
unchanged and remains correct: **the P7 owner records one genuinely failing scenario with
`src/jasmine_core/validation/recorder.py` into the configured run root**
(`validation-runs/p7-<scenario>/`), after which criterion 1 is re-validated against that real record.

---

## Criterion 2 — missing Context/Agent, no fabrication, no verdict upgrade: **PASS** (126/126)

Evidence: `c2-01-real-details.json`, `c2-02-inconsistent-verdict-detail.json`,
`c2-07-absent-fields-detail.json`, `c2-03-criterion2-report.json`.

* **Context chain link missing 9/9, in the three real forms**: 6 sentinel
  (`"recorded as N/A (P0 not implemented)"`), 27 reason-only
  (`"the case records a reason, not a context pack"`), 30 recorded-run-details with no pack
  (`"the section records run details, not a context pack"`). The three frozen strings are exactly the
  set observed. No real case has `context_pack_present: true` (0 packs); the 30 run-details cases are
  served verbatim with `present: true`, `context_pack_present: false`.
* **`agent_response` missing in the six run-level BLOCKED records**: 6 × 7 = 42 cases all have
  `chain.agent.present == false`; the three PASS runs have it present in at least one case.
* **A fourth form (field absent)** is handled without fabrication: with the key deleted, the section
  reports `present_kind: "absent"`, `present: false`, `missing_reason: "the record has no such field"`.
* **Served verdict == recorded verdict, byte-for-byte**, for all nine runs and all 63 cases; all nine
  report `verdict_consistent: true`.
* **`verdict_consistent` is reported, not used**: a fixture whose recorded run verdict is `PASS` while
  its only case is `BLOCKED` is still served as `PASS` with `verdict_consistent: false`.

No counterexample found.

---

## Criterion 3 — paired authorisation on the same resource: **PASS** (63/63)

Evidence: `c3-01-single-user-pairs.json`, `c3-02-no-token.json`, `c3-03-acl-granted-list.json`,
`c3-04-acl-unattributed-list.json`, `c3-05-acl-pairs.json`, `c3-06-criterion3-report.json`.

Same resources driven with a granted key and a refusing key, on **all five routes including the list**:

* **`single_user`**: granted `validation:read` key → 200 on list, detail, manifest, artifact metadata and
  content; a key with `objects:read` only → **403 `forbidden_scope`** with
  `details.required == "validation:read"` on every one of the five paths; no token → **401
  `unauthenticated`**.
* **`acl`**: granted key → 200; scope-less key → 403 on all five; a run attributed to a **non-granted
  project** → **404 `validation_run_not_found`**; an **unattributed** run without
  `validation:unattributed` → 404 and with it → 200; an actor holding `validation:read` but no grant →
  404.
* **`acl` list is grant-filtered**: `visibility: "grant_filtered"`, `runs[] == [the granted run]`,
  `total == 1`, `discovery.run_shaped_files == 1`; the non-granted-project run, the unattributed run
  and the corrupt run appear in neither `runs[]` nor `total`; `?project=<ungranted>` returns
  `runs: []`, `total: 0`; `ignored_samples[] == []`.
* **Fail-closed corrupt record**: with `validation:unattributed` the corrupt row is visible and listed
  with `record_status: "corrupt"`, `verdict: null`, a named `corrupt_reason`; without it, 404.
* **No validation route ever emits `project_not_found`** — every `acl` refusal uses the one run-level
  code `validation_run_not_found`.

No counterexample found.

---

## Criterion 4 — artifact negatives: **PASS** (145/145)

Evidence: `c4-01` … `c4-19`, `c4-12-validator-file-digests.json`, `c4-13-criterion4-report.json`.

* **Normal**: `art_good` 200 with `integrity: "match"`; `declared_sha256`, `digests.original` and the
  content `served_sha256` all equal the SHA-256 the validator computed from the bytes on disk.
* **Missing**: 404 `artifact_not_found`, `details.reason: "missing"`; manifest `integrity: "missing"`.
* **Tampered (byte-flipped)**: 409 `artifact_modified` on metadata and content; manifest
  `integrity: "mismatch"`.
* **Escapes** — each in its own manifest, each refused at load with the offending entry named
  (409 `validation_manifest_corrupt`) and its artifact then unaddressable (404 `artifact_not_found`,
  `reason: "manifest_corrupt"`): `../outside/loot.txt`, nested `artifacts/../../outside/loot.txt`,
  absolute `/tmp/outside/loot.txt`, drive-relative `C:foo.txt`, UNC `\\host\share\f.txt`, external URL
  `https://evil.example/x.txt` (`c4-11-escape-attempts.json`).
* **Symlinks refused at open, not at load**: a symlinked final file and a symlinked **intermediate
  directory** are both 404 `artifact_not_found` with `reason: "symlink_refused"`, while the manifest
  stays 200 and reports `integrity: "symlink_refused"`.
* **Assignment-shaped secret**: the validator ran `canonical.looks_like_credential` itself on
  `db_password=hunter2hunter2` → **False**, while `canonical.redact_text` masks it. Over HTTP,
  `form=masked` (default) → 200 with masked text, `mask_algorithm: "canonical.redact_text"`,
  `mask_version: "1"`, `served_sha256 == masked_sha256 != original_sha256`, secret absent;
  `form=original` → **403 `original_withheld`**, `details.reason: "mask_not_noop"` with the masked
  digest. A manifest-declared `credential_shaped: true` file is likewise withheld
  (`reason: "credential_shaped_declared"`). A realistic `{"api_key":"sk-live-…"}` value is also refused
  on `form=original`.
* **HTML/script with `download=1` is never renderable**: `Content-Type: application/json; charset=utf-8`,
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`,
  `Content-Disposition: attachment; filename="art_html.txt"`, raw body is a JSON envelope (starts `{`)
  carrying the HTML as a string field — for **both** `form=masked` and `form=original`.
* Non-text (`application/octet-stream`) content is 415 `unsupported_media_type`.
* **Raw declared-path rules (the F3 fix)** (`c4-19-raw-declared-paths.json`): each of
  `a//b`, `a/./b`, `a/b/`, `./a`, `.`, `artifacts/note..txt`, `a..b`, `artifacts/..hidden`,
  `artifacts/../loot.txt`, `..%2Fb` makes the manifest `409 validation_manifest_corrupt` with the
  `path` rule named and the artifact unaddressable (404 `manifest_corrupt`).
* **No legitimate declared path was broken**: a normal `artifacts/name.txt` and a nested
  `artifacts/sub/name.txt` both serve `integrity: "match"` and 200 content with the recomputed digest.

No counterexample found.

**Judgement on the `..`-substring rule (asked explicitly).** The spec (§3, line 248) says
`files[].path` "is relative with every component non-empty and contains no `..`". The implementation
tests the **raw string** for the substring `..`, so a name such as `artifacts/note..txt` is refused even
though it is not a traversal segment. I consider that **acceptable**: the rule is a faithful reading of
the spec's own wording ("contains no `..`", with no segment qualifier), it is fail-safe (it can only
turn a would-be-200 manifest into a named `409`, never the reverse), and it closes the class of
normalisation bypasses the review found. It is, however, broader than the escape it targets (a `..`
*segment*): a legitimate filename containing `..` inside an ordinary component is a false positive. I
record it as **informational / no criterion impact**, not a defect, and recommend the spec owner state
whether "contains no `..`" is intended literally or per component.

---

## Criterion 5 — pagination, corrupt ≠ empty, unreadable subtree, and the fixed behaviours: **PASS** (120/120)

Evidence: `c5-01` … `c5-19`, `c5-09-criterion5-report.json`.

* **Full page walk over the real nine** with `limit=2` (`c5-01-real-root-pages.json`): 5 pages, the
  union is exactly the nine filename stems, no repeats, `total` stable at 9, `count == len(runs)`,
  `has_more == (offset + count < total)` on every page, `offset == n·limit`.
* **Fixture root = nine copies + one corrupt record + a parseable duplicate pair (12 rows)**
  (`c5-02`, `c5-03`): full list `total == count == 12`, `corrupt_count == 1`, `duplicate_count == 2`;
  the `limit=4` page walk covers all 12 `(run_id, record_sha256)` pairs exactly once and equals the
  single 100-limit page.
* **Discovery depth** (`c5-12-depth-boundary.json`): a record in a directory **3** and one **4** levels
  below the root are served; a record **5** levels below is not; `ignored_entries == 1` with an
  `ignored_sample` whose `reason == "depth_cap"` names the pruned directory (`…/d4`), and
  `discovery.max_depth == 4`.
* **Duplicate resolution for every combination, single_user and acl** (`c5-13`, `c5-16`):

  | Stem | List rows | Detail | acl + `validation:unattributed` detail |
  | --- | --- | --- | --- |
  | parseable + corrupt | `ok` + `corrupt` (digests are the two files') | **200**, serves the parseable record | 200 |
  | two parseable | `duplicate` × 2 | **409 `validation_run_ambiguous`** | 409 ambiguous |
  | only corrupt (×2) | `corrupt` × 2 | **409 `validation_run_corrupt`** | 409 corrupt |
  | one parseable (control) | `ok` | 200 | 200 |

  For every stem the list's `record_status` set predicts the detail answer (an `ok` row ⇒ 200;
  otherwise `duplicate` ⇒ 409 ambiguous; otherwise ⇒ 409 corrupt). In `acl` with only
  `validation:read` the same records are all `404 validation_run_not_found` (fail-closed, no
  existence oracle).
* **Counters describe the filtered served set (F7)** (`c5-14`, `c5-15`): unfiltered
  `total/corrupt_count/duplicate_count == 7/3/2`; `verdict=PASS` serves 4 rows with
  `corrupt_count == 0` and `duplicate_count == 2` (matching the rows present); a filter matching
  nothing returns `total == 0`, both counts `0`, `empty_reason == "all_filtered_out"`.
* **Corrupt is never disguised as an empty list**: the corrupt row is listed with `verdict: null` and a
  named `corrupt_reason`; a corrupt-only root returns `total == 1`, one `record_status: "corrupt"` row,
  and is **not** `no_run_shaped_files` (`c5-05`, `c5-07`). A truly empty root → `no_run_shaped_files`.
* **Bounded reads** (`c5-17`, `c5-18`): the validator's `manifest.json` was `1 MiB + 1` bytes and its
  record `16 MiB + 2` bytes on disk. The oversize manifest is `409 validation_manifest_corrupt` with a
  rule naming the bound, **no `manifest_sha256`** in the error details (never read), and its artifacts
  `404 manifest_corrupt`; the list row reports `manifest_status: "corrupt"`. The oversize record is a
  `corrupt` list row with `record_sha256: ""` and a reason naming the bound, `corrupt_count == 1`, and
  detail `409 validation_run_corrupt`.
* **Unreadable subtree refuses rather than silently shortening** (`c5-08`): list → **503
  `validation_not_configured`**, `details.reason: "index_incomplete"`,
  `counts.unreadable_entries >= 1`, `samples <= 20` and naming the unreadable path; a by-id read of a
  sibling still returns 200. A by-id read of an id that **cannot be ruled out** on a knowingly
  incomplete tree returns the same 503 `index_incomplete` rather than asserting 404 — the documented
  ADR 0015 §5.0 divergence (F5), confirmed independently (`c5-19-byid-unknown-under-unreadable.json`).

No counterexample found.

---

## Criterion 6 — API values agree with source records and file digests: **PASS** (397/397)

Evidence: `c6-01` … `c6-10`, `c6-08-field-comparison-table.json`, `c6-09-field-comparison-table.txt`.
**373 field-by-field comparisons, 0 mismatches.**

Fields recomputed by the validator from the bytes on disk, then compared to the served value:

* **Real nine, list rows** — `run_id`, `stage`, `created_at`, `commit`, `schema_version`,
  `core_version`, `verdict`, `case_counts{pass,fail,blocked,total}`, `record_sha256` (the validator's own
  SHA-256 of the raw `run-*.json` bytes), `synthetic`, `manifest_status` (all `absent`, and no
  `manifest.json` exists on disk for any of the nine).
* **Real nine, detail** — `run_id`, `stage`, `created_at`, `commit`, `schema_version`, `core_version`,
  `verdict`, and per case `verdict`, `failure_class`, `failure_output`.
* **Fixture bundle** — `manifest.manifest_sha256` (SHA-256 of the raw `manifest.json`),
  `manifest_authentication == "unauthenticated"`; per artifact `declared_sha256`, `declared_bytes`,
  `bytes`, `digests.original`, `digests.masked`, `integrity`; content `original_sha256`,
  `masked_sha256`, `served_sha256`, `content`, `bytes`, `mask_algorithm`, `mask_version`. Masked and
  original digests were recomputed as `sha256(canonical.redact_text(text))` and `sha256(raw bytes)` and
  are distinct fields.
* An absent manifest is reported `status: "absent"` with **no** `manifest_sha256` — nothing fabricated.

No counterexample found.

---

## Implementation suite and full suite

Run from a **fresh clone** (`git clone --no-hardlinks` of the shared repo into `/tmp/p7-01-reval-clone`,
`git checkout a252097`, real root passed via `P7_REAL_RUNS_ROOT`); script
`re_fullsuite_freshclone.sh`; raw output `fullsuite-candidate-module.txt`,
`fullsuite-candidate.txt`, `fullsuite-baseline.txt`.

| Run | Result |
| --- | --- |
| Candidate module `tests/test_validation_read.py` (fresh clone) | **Ran 47 tests — OK** |
| Candidate **full** suite (`PYTHON=python3.13 ./scripts/run-tests.sh`) | **Ran 1413 tests in 42.7s — FAILED (failures=5, errors=4, skipped=13)** |
| Baseline `0f7b2ec` (parent of the phase-2 implementation) full suite | Ran 1366 tests in 42.1s — FAILED (failures=5, errors=4, skipped=13) |

The **failure set is identical** between candidate and baseline — same nine test names (diff of the
`FAIL:`/`ERROR:` lines is empty). All nine are P4 hook-installer/native-session tests that fail with
`canonical venv launcher parent required` (the clone's `python3.13` is not a canonical-venv launcher),
i.e. **pre-existing and environmental, not introduced by P7-01**. The candidate adds 47 tests
(1413 − 1366), all passing, and the P7 module is 47/47.

## Validator correction (recorded for transparency)

An early exploratory `re_c5_pages.py` run showed intermittent `RemoteDisconnected` errors. They were
**my own harness-usage bug**, not an implementation defect: several `live.call(...)` invocations were
written with the method argument missing (`live.call(f"/v1/…", token)` instead of
`live.call("GET", f"/v1/…", token)`), so the path was sent as the HTTP method and the server closed a
malformed request. Root-caused by isolating the request and fixed before the recorded run. The final
recorded run has **0 transport events** (`grep -c transport c5_pages-checks.txt` = 0), and the harness
keeps a logged one-retry guard as a defensive measure (`re_harness.py`). No implementation finding
arises from this.

## Defects found

**None affecting the six criteria.** 879/879 criterion checks passed.

Observations (informational, no severity, no code change implied):

1. **The literal `..`-substring rule is broader than a segment check.** `artifacts/note..txt` is
   refused; this is spec-literal ("contains no `..`") and fail-safe, but the spec owner may wish to say
   whether a `..` inside an ordinary component should be allowed. See the criterion 4 judgement.
2. **F5 / F6 are documented divergences, confirmed as documented.** By-id unknown ids over an
   incomplete tree return 503 `index_incomplete` (not 404), and `acl` + `validation:unattributed`
   reaches the 409 family; both are recorded in ADR 0015 §5.0 and behave exactly as written, with the
   fail-closed `404` still returned without the explicit grant.

## What this validation covers and does not cover

* **Covered:** the five `GET` routes of the minimal read slice, over a real loopback socket against a
  real migrated DB, in both `single_user` and `acl`, on the nine real P0 records and on validator-built
  fixture roots — criteria 2, 3, 4, 5 and 6, including the changed duplicate-resolution, discovery-depth,
  declared-path, bounded-read and counter behaviours. Supplementary spot-checks: GET-only routes
  (405 on POST), unset/symlinked root refusals, malformed id and unknown query-key 400s.
* **Not covered / not accepted here:** criterion 1 remains **BLOCKED** pending a real P7-format FAIL
  run. This validation does **not** accept the P7 stage, the Scenario Runner, the 30-scenario suite, any
  release verdict, or the P6-T06 browser drill-down. Only the read slice described by
  `web/contracts/p7-validation-read.v1.json` is inside scope.

**Explicit repeat:** criterion 1 is **BLOCKED** — the nine real records contain 0 FAIL cases, 0
`failure_class` and 0 `failure_output` — and stays BLOCKED until the P7 owner records one genuinely
failing scenario with `recorder.py` into the configured run root.

## Reproducing this validation

```
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/VALIDATE-p7-01/evidence-revalidation"
bash re_run_all.sh                 # all six criteria + supplementary probes
bash re_fullsuite_freshclone.sh    # fresh clone: module test + full suite + baseline suite
```

| Script | Purpose |
| --- | --- |
| `re_analysis_real_records.py` | read-only census of the real records |
| `re_harness.py` | live-socket harness, key issuance, projects/grants, fixture builders |
| `re_c1_fail.py` | criterion 1 (real absence + labelled synthetic FAIL fixture) |
| `re_c2_missing.py` | criterion 2 |
| `re_c3_authz.py` | criterion 3 |
| `re_c4_artifacts.py` | criterion 4 (incl. raw declared-path rules and legit paths) |
| `re_c5_pages.py` | criterion 5 (pagination, depth, duplicates, bounds, counters, unreadable) |
| `re_c6_values.py` | criterion 6 |
| `re_extra_probes.py` | supplementary contract probes |
| `re_run_all.sh`, `re_fullsuite_freshclone.sh` | runners |
