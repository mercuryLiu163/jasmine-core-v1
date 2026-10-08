# VALIDATE-p7-01 — independent behavioural validation of the minimal Validation read slice

**Validator / model identifier:** `deepseek-flash` (DeepSeek Harness coding agent; DSH session id
`971223b6-4552-4ff6-952e-2f819ab9c351`).

**Candidate SHA validated:** `1f7b0aff27198e673f264a86b7bb53d529604239` (branch tip
`p7-01-validation-read`, worktree `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`).
The tracked tree was clean at validation start; the only working-tree change created by this validation is
the untracked directory `validation-quarantine/VALIDATE-p7-01/` (`git status --porcelain` →
`?? validation-quarantine/VALIDATE-p7-01/`). No tracked file was modified, nothing was committed or pushed.

**Specification validated against:** `web/P7-01-CONTRACT-PROPOSAL.md` (frozen rev 5) and the accepted
contract `web/contracts/p7-validation-read.v1.json` (contract `source.ref`
`2eb1e5a8cc31383b064d4055e884a8a10ffe0e18`, confirmed an ancestor of the candidate HEAD).

## Environment

| Item | Value |
| --- | --- |
| Time (UTC) | 2026-10-08T14:39Z (evidence captured `env-01-environment.txt`) |
| Core version | `0.1.0` |
| Schema version | `11` |
| `canonical.REDACT_TEXT_VERSION` | `1` |
| Python used | `/opt/homebrew/bin/python3.13` (Python 3.13.15); default `python3` is 3.9.6 and cannot import the package |
| Mode(s) exercised | `single_user` **and** `acl` |
| Real vs fixture | **both** — the nine real records were served read-only; all negatives used validator-built fixture roots |
| Real root configured | `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs` (env `JASMINE_CORE_VALIDATION_RUNS`) |
| Fixture roots | validator temp roots under a per-run `TemporaryDirectory` (`validate-p7-01-*`), never the operator root |
| Scopes used (values never recorded) | `validation:read`, `validation:unattributed`; `admin`+`objects:read`+`objects:write` only to create the two ACL projects; `objects:read` used as the deliberately scope-less key |
| Real records reachable/used | **yes** — 9/9 reachable and used in criteria 1, 2, 5 and 6 (see `c1-01-real-records-scan.txt`) |

All requests were made over a real loopback socket against the real `jasmine_core.api.server` on a real
migrated SQLite DB (fresh `migrate()` per harness instance), using the validator's own harness
(`harness.py`) — not a mocked call and not the implementation's test body.

The nine real record files are unmodified: their mtimes remain `2026-09-29` and their SHA-256 values are
recorded in `env-01-environment.txt`; the operator tree was only read.

## Headline result

| # | Criterion | Verdict | Checks (all passed) |
| --- | --- | --- | --- |
| 1 | Read an existing real FAIL Run, classification + raw output traceable | **BLOCKED** | 28 |
| 2 | Missing Context/Agent shown as missing, nothing fabricated, no verdict upgrade | **PASS** | 126 |
| 3 | Authorised success and refusal paired on the same resource (list + detail) | **PASS** | 63 |
| 4 | Artifact negatives: normal, missing, tampered, escape | **PASS** | 88 |
| 5 | Pagination no dup/omission; corrupt ≠ empty; unreadable subtree refuses | **PASS** | 58 |
| 6 | API values agree with source records and file digests | **PASS** | 397 |

Supplementary (not one of the six): GET-only routes, unset/symlinked root, malformed id, unknown query key
— 9/9 passed (`extra-01-probes.json`). The implementation's own suite `tests/test_validation_read.py` was
also run as corroboration and passed 38/38, but it is **not** the basis of any verdict below.

---

## Criterion 1 — real FAIL Run: **BLOCKED**

### (a) Independent confirmation that no real P7-format FAIL run exists

Command (from `validation-quarantine/VALIDATE-p7-01/`):

```
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 analysis_real_records.py \
  2>&1 | tee evidence/c1-01-real-records-scan.txt
```

Raw result (`c1-01-real-records-scan.txt`, aggregate line):

```
RUN-SHAPED FILES: 9
AGGREGATE: {"agent_sentinel": 60, "agent_value": 3, "cases": 63,
  "context_reason_only": 27, "context_run_details(no pack)": 30, "context_sentinel": 6,
  "fail_cases": 0, "failure_class_present": 0, "failure_output_present": 0,
  "records": 9, "verdict_BLOCKED": 6, "verdict_PASS": 3}
```

Independently confirmed: **9 records, all `stage: P0`, 3 PASS / 6 BLOCKED at run level, 63 cases,
0 cases with `verdict == FAIL`, 0 `failure_class` values, 0 `failure_output` values.** There is no real
P7-format FAIL run on disk. (The proposal's dry-run claim matched exactly.)

Command / evidence:

```
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 c1_fail.py \
  2>evidence/c1-08-stderr.txt | tee evidence/c1-09-criterion1-checks.txt
```

Result: 28/28 checks ok. Real-root list (`c1-03-real-root-list.json`) serves `total: 9`, no row with
`verdict: "FAIL"`, every row `case_counts.fail == 0`, 3 PASS / 6 BLOCKED.

### (b) The FAIL code path is exercised only by a clearly labelled synthetic fixture

The validator copied `tests/fixtures/p7/labelled-fail-run.json` into its **own** fixture root (never the
operator root) and drove the real HTTP API against it:

* `c1-05-fixture-root-list.json` — the fixture row serves `verdict: "FAIL"`, `synthetic: true`,
  `case_counts.fail: 1`; its run-level `evidence_grade` is `unknown` (the documented min of the
  absent-manifest `unknown` effective mode and the `simulated` case, §4.2) and is **never `real`**.
* `c1-06-labelled-fail-detail.json` — the case's `failure_class: "UI_FAILURE"` and
  `failure_output: "expected 200 got 500"` are byte-identical to the file; the case grade is `simulated`
  with `grade_basis: "the run record marks itself synthetic"`; `synthetic_note` is surfaced verbatim.

Nothing counts the fixture as evidence and nothing upgrades a verdict:

* the fixture id `404 validation_run_not_found` under the operator's real root
  (`c1-04-fixture-id-under-real-root.json`);
* the real root still lists exactly the nine real records with no FAIL row;
* the fixture file's resolved path is outside the operator root, and the operator root still holds exactly
  9 `run-*.json` files;
* the fixture labels itself `stage: P7`, `synthetic: true` in the record itself.

### (c) BLOCKED reason and unblock action

**Criterion 1 is BLOCKED: there is no existing real P7-format FAIL Run to read.** All nine real records are
`stage: P0`, 3 PASS / 6 BLOCKED, with 0 FAIL cases, 0 `failure_class` and 0 `failure_output`. The FAIL code
path is exercised only by the clearly-labelled synthetic fixture above, which is not evidence.

**Unblock action:** the P7 owner records one genuinely failing scenario with
`src/jasmine_core/validation/recorder.py` into the configured run root
(`validation-runs/p7-<scenario>/`), then criterion 1 is re-validated against that real record. (This is the
proposal's own §9 unblock action and it remains correct.)

---

## Criterion 2 — missing Context/Agent, no fabrication, no verdict upgrade: **PASS**

Command:

```
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 c2_missing.py \
  2>evidence/c2-04-stderr.txt | tee evidence/c2-05-criterion2-checks.txt
```

126/126 checks ok. Evidence: `c2-01-real-details.json` (all nine real details),
`c2-02-inconsistent-verdict-detail.json`, `c2-07-absent-fields-detail.json`, `c2-03-criterion2-report.json`.

Real-record findings (all recomputed from the files and compared to the served details):

* **Context chain link missing 9/9, in all three real forms** (`c2-01-real-details.json`): 6 cases
  sentinel (`"recorded as N/A (P0 not implemented)"`), 27 reason-only
  (`"the case records a reason, not a context pack"`), 30 recorded-run-details with no pack
  (`"the section records run details, not a context pack"`). The three frozen strings are exactly the set
  observed. No real case has `context_pack_present: true` (0 packs), and the 30 run-details cases are served
  verbatim with `present: true` and `context_pack_present: false` — the section is not counted as a pack.
* **`agent_response` missing in the six run-level BLOCKED records**: all 6 × 7 = 42 cases have
  `chain.agent.present == false`; the three PASS runs have `agent_response` present in at least one case.
* **A fourth form (absent field)** is also handled without fabrication: with the key deleted from the
  record, the section reports `present_kind: "absent"`, `present: false`, `missing_reason: "the record has
  no such field"` (`c2-07-absent-fields-detail.json`).
* **Served verdict == recorded verdict, byte-for-byte**, for all nine runs and all 63 cases; all nine report
  `verdict_consistent: true`.
* **`verdict_consistent` is reported, not used**: a labelled fixture whose recorded run verdict is `PASS`
  while its only case is `BLOCKED` is still served as `PASS` with `verdict_consistent: false` — the API
  neither rewrites nor upgrades/downgrades the recorded verdict (`c2-02-inconsistent-verdict-detail.json`).

No counterexample found.

---

## Criterion 3 — paired authorisation on the same resource: **PASS**

Command:

```
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 c3_authz.py \
  2>evidence/c3-07-stderr.txt | tee evidence/c3-08-criterion3-checks.txt
```

63/63 checks ok. Evidence: `c3-01-single-user-pairs.json`, `c3-02-no-token.json`,
`c3-03-acl-granted-list.json`, `c3-04-acl-unattributed-list.json`, `c3-05-acl-pairs.json`.

Same resources driven with a granted key and a refusing key, on **all five routes including the list route**:

* **`single_user`**: granted `validation:read` key → 200 on list, detail, manifest, artifact metadata and
  content; key with `objects:read` only → **403 `forbidden_scope`** with
  `details.required == "validation:read"` on every one of the five paths; no token → **401
  `unauthenticated`**.
* **`acl`**: granted key → 200; scope-less key → 403 on all five; a run attributed to a **non-granted
  project** → **404 `validation_run_not_found`**; an **unattributed** run without
  `validation:unattributed` → 404 and with it → 200; an actor holding `validation:read` but no grant → 404.
* **`acl` list is grant-filtered** (`c3-03-acl-granted-list.json`): `visibility: "grant_filtered"`,
  `runs[] == [the granted run]`, `total == 1`, `discovery.run_shaped_files == 1`; the non-granted-project
  run, the unattributed run and the corrupt run appear in **neither `runs[]` nor `total`**; a
  `?project=<ungranted>` filter returns `runs: []`, `total: 0`; **`ignored_samples[] == []`**.
* **Fail-closed corrupt record**: with `validation:unattributed` the corrupt row is visible and listed with
  `record_status: "corrupt"`, `verdict: null` and a named `corrupt_reason`; without it, 404
  (`c3-04-acl-unattributed-list.json`).
* **No validation route ever emits `project_not_found`** — every refusal in `acl` uses the one run-level
  code `validation_run_not_found` (checked across all collected responses).

No counterexample found.

---

## Criterion 4 — artifact negatives: **PASS**

Command:

```
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 c4_artifacts.py \
  2>evidence/c4-14-stderr.txt | tee evidence/c4-15-criterion4-checks.txt
```

88/88 checks ok. Evidence: `c4-01` … `c4-18`, plus `c4-16-credential-shape-probe.txt`.

* **Normal**: `art_good` served 200 with `integrity: "match"`; `declared_sha256` and
  `digests.original` both equal the SHA-256 the validator computed from the bytes on disk; content
  `served_sha256` matches the same hash (`c4-01`, `c4-02`).
* **Missing**: 404 `artifact_not_found`, `details.reason == "missing"`; manifest reports
  `integrity: "missing"` (`c4-03`).
* **Tampered (byte-flipped)**: 409 `artifact_modified` on both metadata and content; manifest reports
  `integrity: "mismatch"` (`c4-04`, `c4-05`).
* **Escapes** — each in its own manifest, each refused at manifest load with the offending entry named
  (409 `validation_manifest_corrupt`) and its artifact then unaddressable (404 `artifact_not_found`,
  `reason: "manifest_corrupt"`): `../outside/loot.txt`, nested `artifacts/../../outside/loot.txt`,
  absolute `/tmp/outside/loot.txt`, drive-relative `C:foo.txt`, UNC `\\host\share\f.txt`, external URL
  `https://evil.example/x.txt` (`c4-11-escape-attempts.json`).
* **Symlinks refused at open, not at load**: a symlinked final file and a symlinked **intermediate
  directory** are both 404 `artifact_not_found` with `reason: "symlink_refused"` (metadata and content),
  while the manifest stays 200 and reports `integrity: "symlink_refused"` for each.
* **Assignment-shaped secret**: the validator ran `canonical.looks_like_credential` itself on
  `db_password=hunter2hunter2` → **False**, while `canonical.redact_text` masks it in place. Over HTTP,
  `form=masked` (default) returns 200 with the masked text, `mask_algorithm: "canonical.redact_text"`,
  `mask_version: "1"`, `served_sha256 == masked_sha256 != original_sha256` and the secret absent; the
  masked digest equals the validator's own `sha256(redact_text(text))`. `form=original` returns
  **403 `original_withheld`**, `details.reason: "mask_not_noop"`, `details.masked_sha256` = that digest
  (`c4-06`, `c4-07`, `c4-08`). A manifest-declared `credential_shaped: true` file is likewise withheld with
  `reason: "credential_shaped_declared"` even though it contains no secret (`c4-18`).
* **HTML/script with `download=1` is never renderable**: the response `Content-Type` is
  `application/json; charset=utf-8` (not `text/html`), with `X-Content-Type-Options: nosniff`, a
  `default-src 'none'; sandbox` CSP, `Content-Disposition: attachment; filename="art_html.txt"`, and a raw
  body that is a JSON envelope (starts with `{`) carrying the HTML as a string field — for **both**
  `form=masked` and `form=original` (`c4-09`, `c4-10`, `c4-17`).
* Non-text (`application/octet-stream`) content is 415 `unsupported_media_type` (`c4-15` transcript).

Observation (not a defect, no criterion impact): the proposal §5's parenthetical names
`{"api_key": "…"}` as another shape `looks_like_credential` misses; that is true for the literal
placeholder (`"..."`) but **not** for a realistic value. `looks_like_credential('{"api_key":
"sk-live-abcdef0123456789"}')` returns **True** (`c4-16-credential-shape-probe.txt`). The original gate is
masker-based and withholds the file either way, so behaviour is correct; only the prose sample is narrow.

No counterexample found.

---

## Criterion 5 — pagination, corrupt ≠ empty, unreadable subtree: **PASS**

Command:

```
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 c5_pages.py \
  2>evidence/c5-10-stderr.txt | tee evidence/c5-11-criterion5-checks.txt
```

58/58 checks ok. Evidence: `c5-01` … `c5-08`.

* **Every page over the real nine** with `limit=2` (`c5-01-real-root-pages.json`): 5 pages, the union is
  exactly the nine filename stems, no repeats, `total` stable at 9, `count == len(runs)`, and
  `has_more == (offset + count < total)` on every page; `offset` is `n·limit`.
* **Fixture root = nine copies + one corrupt record + a duplicate stem pair (12 rows)**
  (`c5-02`, `c5-03`): full list `total == count == 12`, `corrupt_count == 1`, `duplicate_count == 2`; the
  page walk with `limit=4` covers all 12 `(run_id, record_sha256)` pairs exactly once and equals the single
  100-limit page.
* **Corrupt is never disguised as an empty list**: the corrupt row is listed with `verdict: null` and a
  named `corrupt_reason`; in a corrupt-only root the list returns `total == 1`, `count == 1`, one
  `record_status: "corrupt"` row, and is **not** reported as `no_run_shaped_files` (`c5-05`). A positive
  filter (`verdict=PASS`) never matches the corrupt row while still returning rows (`c5-04`).
* **Duplicate/corrupt by-id**: duplicate stem → 409 `validation_run_ambiguous`; corrupt record → 409
  `validation_run_corrupt` (single-user) (`c5-06`, `c5-07`).
* **`empty_reason`**: a truly empty root → `no_run_shaped_files`; an all-filtered-out root →
  `all_filtered_out`.
* **Unreadable subtree refuses rather than returning a short page** (`c5-08-unreadable-subtree.json`):
  list → **503 `validation_not_configured`**, `details.reason: "index_incomplete"`,
  `counts.unreadable_entries >= 1`, `samples` ≤ 20 and naming the unreadable path; a by-id read of a
  sibling still returns 200.

No counterexample found.

---

## Criterion 6 — API values agree with the source records and file digests: **PASS**

Command:

```
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 c6_values.py \
  2>evidence/c6-11-stderr.txt | tee evidence/c6-12-criterion6-checks.txt
```

397/397 checks ok, **373 field-by-field value comparisons, 0 mismatches**. Full table:
`c6-08-field-comparison-table.json` and `c6-09-field-comparison-table.txt`; raw responses `c6-01` … `c6-07`.

Fields compared (recomputed from the bytes on disk, then compared to the served value):

* **Real nine, list rows** — `run_id`, `stage`, `created_at`, `commit`, `schema_version`, `core_version`,
  `verdict`, `case_counts{pass,fail,blocked,total}`, `record_sha256` (the validator's own SHA-256 of the raw
  `run-*.json` bytes), `synthetic`, `manifest_status` (all `absent`, and no `manifest.json` exists on disk
  for any of the nine).
* **Real nine, detail** — `run_id`, `stage`, `created_at`, `commit`, `schema_version`, `core_version`,
  `verdict`, and per case `verdict`, `failure_class`, `failure_output`.
* **Fixture bundle** — `manifest.manifest_sha256` (SHA-256 of the raw `manifest.json`),
  `manifest_authentication == "unauthenticated"`; per artifact `declared_sha256`, `declared_bytes`,
  `bytes`, `digests.original`, `digests.masked`, `integrity`; content `original_sha256`, `masked_sha256`,
  `served_sha256`, `content`, `bytes`, `mask_algorithm`, `mask_version`. The masked and original digests
  were recomputed as `sha256(canonical.redact_text(text))` and `sha256(raw bytes)` respectively and are
  **distinct fields** (`masked != original` for the assignment secret; equal for the no-op file).
* An absent manifest is reported `status: "absent"` with **no** `manifest_sha256`
  (`c6-02-real-manifest-absent.json`), i.e. nothing is fabricated where the digest does not apply.

No counterexample found.

---

## Defects found

**None affecting the six criteria.** Every one of the 760 criterion checks passed (28+126+63+88+58+397).

Two non-defect observations are recorded for completeness:

1. **Prose precision (informational, no severity):** `web/P7-01-CONTRACT-PROPOSAL.md` §5 cites
   `{"api_key": "…"}` as a shape `looks_like_credential` misses; that holds only for the literal
   placeholder. A realistic value is caught by `looks_like_credential` (True). The served behaviour is
   correct and stronger either way (the original gate is masker-based), so no code change is implied.
2. **Run-level vs case-level grade on an absent-manifest synthetic record (documented behaviour):** a
   `synthetic: true` record with no manifest reports run-level `evidence_grade: "unknown"` while its case is
   `simulated`. This is exactly the §4.2 rule (`min(absent-manifest "unknown" effective mode, case grade)`),
   and the run is never graded `real`; recorded so the two levels are not confused.

## What this validation does and does not cover

* **Covered:** the five `GET` routes of the minimal read slice, over a real socket against a real migrated
  DB, in both `single_user` and `acl`, on the nine real P0 records and on validator-built fixture roots —
  criteria 2, 3, 4, 5 and 6. Supplementary spot-checks: GET-only routes (405 on POST), unset and symlinked
  root refusals, malformed id and unknown query-key 400s.
* **Not covered / not accepted here:** criterion 1 remains **BLOCKED** pending a real P7-format FAIL run.
  This validation does **not** accept the P7 stage, the Scenario Runner, the 30-scenario suite, any release
  verdict, or the P6-T06 browser drill-down. Only the read slice described by
  `web/contracts/p7-validation-read.v1.json` is inside scope.

**Explicit repeat:** criterion 1 is **BLOCKED** — the nine real records contain 0 FAIL cases, 0
`failure_class` and 0 `failure_output` — and stays BLOCKED until the P7 owner records one genuinely failing
scenario with `recorder.py` into the configured run root.

## Reproducing this validation

All scripts and raw output are under `validation-quarantine/VALIDATE-p7-01/`:

| Script | Purpose |
| --- | --- |
| `analysis_real_records.py` | read-only census of the real records (criterion 1a/2 facts) |
| `harness.py` | live-socket harness, key issuance, projects/grants, fixture builders |
| `c1_fail.py` | criterion 1 (real absence + labelled synthetic FAIL fixture) |
| `c2_missing.py` | criterion 2 (real details + inconsistent/absent fixtures) |
| `c3_authz.py` | criterion 3 (single_user + acl paired refusals, list filtering) |
| `c4_artifacts.py` | criterion 4 (good/missing/tampered/escapes/symlinks/masking/HTML) |
| `c5_pages.py` | criterion 5 (real page walk, corrupt/duplicate, unreadable subtree) |
| `c6_values.py` | criterion 6 (field-by-field and digest comparison) |
| `extra_probes.py` | supplementary contract probes |

Run each with:

```
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/VALIDATE-p7-01"
PYTHONPATH="../../src:." /opt/homebrew/bin/python3.13 <script>.py
```

Every raw check transcript and response body is retained under `evidence/`.
