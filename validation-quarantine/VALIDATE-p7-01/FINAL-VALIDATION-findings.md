# FINAL VALIDATION — P7-01 minimal Validation read slice, candidate `0ae8041`

**Validator / model identifier:** `deepseek-flash` (DeepSeek Harness coding agent; delegated
validation session, parent agent id `session-e8a0a737-4ab8-4d61-8232-0f798a7b68f8`).

**Candidate SHA validated:** `0ae80411824739b11485c61ed388d86093f2be7c`, branch tip
`p7-01-validation-read` in worktree `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`
(confirmed `git rev-parse HEAD` and `git rev-parse p7-01-validation-read`).

**Supersedes:** the earlier C1-BLOCKED / C2–C6-PASS report on `a252097` (`RE-VALIDATION-findings.md`).
None of its evidence or verdicts is reused as evidence for this candidate. The earlier harness under
`validation-quarantine/VALIDATE-p7-01/` was reused as a starting point only; every command below was
**re-run against `0ae8041`**, and the criterion-5 harness was extended with per-request timing and the
new non-regular-file (FIFO/socket) probes. Tracked tree clean at start and end (`git status --porcelain`
shows only the untracked `validation-quarantine/…` directories); nothing committed or pushed; no tracked
file modified; the operator's real run tree was only ever read.

**Specification / contract validated against:** `web/P7-01-CONTRACT-PROPOSAL.md` (revision 5) and the
accepted contract `web/contracts/p7-validation-read.v1.json` (`p7-validation-read.v1`,
`source.ref = 46a33d78d03e4d8d9711c96ded62072de8a276be`; independently confirmed an ancestor of the
candidate HEAD). The one behavioural change `46a33d7` introduced — the walk refusing a non-regular
`run-*.json` with reason `not_regular_file`, per §2 "only regular files are considered" — is exercised
directly (criterion 5 below). The contract re-pin in `0ae8041` changes only `source.ref`.

All evidence is under `validation-quarantine/VALIDATE-p7-01/evidence-final/`. Every request was real
loopback HTTP (raw `urllib`) against the real `jasmine_core.api.server.Application` on a real migrated
SQLite DB (fresh `migrate()` per harness instance), in `single_user` **and** `acl`, over the nine real
P0 records plus validator-built fixture roots. No implementation test body was used as a check.

## Headline result

| # | Criterion | Verdict | Checks |
| --- | --- | --- | --- |
| 1 | Read an existing real FAIL Run (classification + raw output traceable) | **BLOCKED** | 28 |
| 2 | Missing Context/Agent shown as missing, nothing fabricated, no verdict upgrade | **PASS** | 126 |
| 3 | Authorised success and refusal paired on the same resource (5 routes incl. list) | **PASS** | 63 |
| 4 | Artifact negatives: normal, missing, tampered, escape, secret, HTML | **PASS** | 145 |
| 5 | Pagination, corrupt ≠ empty, unreadable subtree, depth/duplicate/bounds, **FIFO** | **PASS** | 145 |
| 6 | API values agree with source records and file digests, field by field | **PASS** | 397 |
|  | **Total criterion checks** |  | **904 / 904, 0 failed** |

Supplementary (not one of the six): 9/9 contract probes (`extra_probes-checks.txt`, `extra-01-probes.json`).
The implementation's own module was run from a fresh clone for corroboration (48/48 OK) but is **not** the
basis of any verdict. All seven scripts ran with 0 `[FAIL]` lines and 0 transport retries;
`grep -c '^\[FAIL\]' *-checks.txt` = 0 for every script.

## Criterion 1 — real FAIL Run: **BLOCKED** (28/28 checks)

Independently re-confirmed from the bytes, read-only:

* `final_analysis_real_records.py` → `RUN-SHAPED FILES: 9` and
  `AGGREGATE: {"agent_sentinel": 60, "agent_value": 3, "cases": 63, "context_reason_only": 27,
  "context_run_details(no pack)": 30, "context_sentinel": 6, "fail_cases": 0, "failure_class_present": 0,
  "failure_output_present": 0, "records": 9, "verdict_BLOCKED": 6, "verdict_PASS": 3}`.
  So: **0 real FAIL cases, 0 `failure_class`, 0 `failure_output`**; all nine records are `stage: P0`
  with 3 PASS / 6 BLOCKED.
* Over HTTP, `c1-03-real-root-list.json`: `total: 9`, no row with `verdict: "FAIL"`, every row
  `case_counts.fail == 0`; 3 PASS / 6 BLOCKED (`c1_fail-checks.txt`, 28/28).
* The FAIL code path is exercised **only** by the clearly-labelled synthetic fixture. The validator
  copied `tests/fixtures/p7/labelled-fail-run.json` into its **own** root and served it:
  `c1-05-fixture-root-list.json` row has `verdict: "FAIL"`, `synthetic: true`, `case_counts.fail: 1`,
  run-level `evidence_grade: "unknown"` (**never `real`**); `c1-06-labelled-fail-detail.json` serves the
  recorded `failure_class: "UI_FAILURE"` and `failure_output: "expected 200 got 500"` unchanged, with the
  case graded `simulated` and `grade_basis` naming the synthetic run. The fixture file self-labels
  `stage: P7`, `synthetic: true` and lives outside the operator root; its id 404s under the real root
  (`c1-04`). The synthetic record is never counted as evidence for criterion 1.
* Operator root unchanged: exactly 9 `run-*.json`, mtimes still `2026-09-29` (see
  `env-final-environment.txt`).

**BLOCKED reason:** there is no existing real P7-format FAIL Run to read.
**Unblock action:** the P7 owner records one genuinely failing scenario with
`src/jasmine_core/validation/recorder.py` into the configured run root
(`validation-runs/p7-<scenario>/`), after which criterion 1 is re-validated against that real record.

## Criterion 2 — missing Context/Agent, no fabrication, no verdict upgrade: **PASS** (126/126)

Evidence: `c2-01-real-details.json`, `c2-02-inconsistent-verdict-detail.json`,
`c2-07-absent-fields-detail.json`, `c2-03-criterion2-report.json`.

* Context chain link missing in all 9 runs / 63 cases, in the three real forms observed by the census:
  6 sentinel (`"recorded as N/A (P0 not implemented)"`), 27 reason-only
  (`"the case records a reason, not a context pack"`), 30 run-details served verbatim with no pack
  (`"the section records run details, not a context pack"`). No real case has `context_pack_present: true`.
* `agent_response` absent in every case of the six run-level BLOCKED runs (6 × 7 = 42 cases,
  `chain.agent.present == false`), present in the three PASS runs.
* A fourth form (field absent) reports `present_kind: "absent"`, `present: false`,
  `missing_reason: "the record has no such field"` — nothing fabricated.
* Served verdict equals the recorded verdict for all 9 runs / 63 cases; `verdict_consistent` is
  **reported, not enforced** (a fixture with recorded run `PASS` but a BLOCKED-only case is still served
  `PASS` with `verdict_consistent: false`). No verdict is upgraded.

## Criterion 3 — paired authorisation on the same resource, all five routes: **PASS** (63/63)

Evidence: `c3-01-single-user-pairs.json`, `c3-02-no-token.json`, `c3-03-acl-granted-list.json`,
`c3-04-acl-unattributed-list.json`, `c3-05-acl-pairs.json`, `c3-06-criterion3-report.json`.

* **All five routes** driven as pairs — `GET /v1/validation/runs` (list),
  `/runs/{id}`, `/runs/{id}/manifest`, `/runs/{id}/artifacts/{aid}`,
  `/runs/{id}/artifacts/{aid}/content`:
  * `single_user`: granted `validation:read` → 200 on all five; `objects:read`-only key → **403
    `forbidden_scope`** with `details.required == "validation:read"` on all five; no token → **401
    `unauthenticated`**.
  * `acl`: granted key → 200 on all five; scope-less key → 403 on all five.
* `acl` refusals on the same resources: a run attributed to a **non-granted project** → 404
  `validation_run_not_found`; an **unattributed** run without `validation:unattributed` → 404, with it →
  200; an actor holding `validation:read` but no grant → 404; a corrupt record fails closed → 404. No
  validation route ever emits `project_not_found`.
* **`acl` list is grant-filtered**: `visibility: "grant_filtered"`, `runs[] == [the granted run]`,
  `total == 1`, `discovery.run_shaped_files == 1`, `ignored_samples[] == []`; the ungranted-project run,
  the unattributed run and the corrupt run appear in neither `runs[]` nor `total`; `?project=<ungranted>`
  returns `runs: []`, `total: 0`.

## Criterion 4 — artifact negatives: **PASS** (145/145)

Evidence: `c4-01` … `c4-19`, `c4-12-validator-file-digests.json`, `c4-13-criterion4-report.json`.

* **Normal**: `art_good` 200 `integrity: "match"`; `declared_sha256`, `digests.original` and the served
  content digest all equal the SHA-256 the validator computed from the bytes on disk.
* **Missing**: 404 `artifact_not_found`, `details.reason: "missing"`, manifest `integrity: "missing"`.
* **Tampered (byte-flipped)**: 409 `artifact_modified` on metadata and content, manifest
  `integrity: "mismatch"`.
* **Escapes** (each in its own manifest, refused at load with the offending entry named, 409
  `validation_manifest_corrupt`, artifact then 404 `manifest_corrupt`): `../outside/loot.txt`,
  nested `artifacts/../../outside/loot.txt`, absolute `/tmp/outside/loot.txt`, drive-relative
  `C:foo.txt`, UNC `\\host\share\f.txt`, external URL `https://evil.example/x.txt`
  (`c4-11-escape-attempts.json`).
* **Symlinks refused at open, not at load**: a symlinked final file and a symlinked **intermediate
  directory** are both 404 `artifact_not_found`, `reason: "symlink_refused"`, manifest 200 with
  `integrity: "symlink_refused"`.
* **Assignment-shaped secret masked vs withheld**: the validator ran `canonical.looks_like_credential`
  itself on `db_password=hunter2hunter2` → **False**, while `canonical.redact_text` masks it
  (`c4-06-validator-mask-computation.json`). `form=masked` (default) → 200 masked text,
  `mask_algorithm: "canonical.redact_text"`, `mask_version: "1"` (`== REDACT_TEXT_VERSION`),
  `served_sha256 == masked_sha256 != original_sha256`, secret absent; `form=original` → **403
  `original_withheld`**, `details.reason: "mask_not_noop"` carrying the masked digest. A
  manifest-declared `credential_shaped: true` file is withheld (`reason: "credential_shaped_declared"`),
  and a realistic `{"api_key": "sk-live-…"}` value is likewise refused in original form.
* **HTML never renderable**: with `download=1`, `Content-Type: application/json; charset=utf-8`,
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`,
  `Content-Disposition: attachment; filename="art_html.txt"`; the raw body is a JSON envelope (starts
  `{`) carrying the HTML as a string field — for **both** `form=masked` and `form=original`
  (`c4-09`, `c4-10`, `c4-17`).
* Non-text (`application/octet-stream`) content → 415 `unsupported_media_type`.
* **Raw declared-path rules** (`c4-19-raw-declared-paths.json`): `a//b`, `a/./b`, `a/b/`, `./a`, `.`,
  `artifacts/note..txt`, `a..b`, `artifacts/..hidden`, `artifacts/../loot.txt`, `..%2Fb` each make the
  manifest 409 `validation_manifest_corrupt` with the rule named and the artifact unaddressable; a
  normal `artifacts/name.txt` and nested `artifacts/sub/name.txt` still serve `match` + content.

## Criterion 5 — pagination, corrupt ≠ empty, unreadable subtree, and the fixed behaviours: **PASS** (145/145)

Evidence: `c5-01` … `c5-24`, `c5-09-criterion5-report.json`, `c5-22-request-timings.json`.

* **Full page walk, no duplicates or omissions** — real nine with `limit=2` (`c5-01`): 5 pages, union
  exactly the nine filename stems, no repeats, `total` stable at 9, `count == len(runs)`,
  `has_more == (offset + count < total)`, `offset == n·limit`. The same walk with `limit=4` over a
  12-row fixture root (`c5-02`, `c5-03`) covers all 12 `(run_id, record_sha256)` pairs exactly once and
  equals the single 100-limit page. **The real nine were also paged in `acl` mode** with
  `validation:unattributed` (`c5-24`): same nine, no repeats, `total` 9, all page arithmetic holds; a
  `validation:read`-only key sees 0 real rows.
* **Corrupt record never disguised as an empty list**: corrupt row listed with `verdict: null` and a
  named `corrupt_reason`; a corrupt-only root returns `total == 1`, one `record_status: "corrupt"` row
  and is **not** `no_run_shaped_files` (`c5-05`, `c5-07`); a truly empty root → `no_run_shaped_files`.
* **Unreadable subtree refused, not silently short** (`c5-08`): list → **503
  `validation_not_configured`**, `details.reason: "index_incomplete"`,
  `counts.unreadable_entries >= 1`, `samples <= 20` naming the unreadable path; a sibling by-id read
  still 200. A by-id read of an id that cannot be ruled out returns the same 503 (documented ADR 0015
  §5.0 divergence), never a false 404 (`c5-19`).
* **Duplicate resolution, every combination, list↔detail agreement, `single_user` and `acl`**
  (`c5-13`, `c5-16`):

  | Stem | List rows | Detail | acl + `validation:unattributed` |
  | --- | --- | --- | --- |
  | parseable + corrupt | `ok` + `corrupt` (both digests) | **200** serves the parseable record | 200 |
  | two parseable | `duplicate` × 2 | **409 `validation_run_ambiguous`** | 409 ambiguous |
  | only corrupt (×2) | `corrupt` × 2 | **409 `validation_run_corrupt`** | 409 corrupt |
  | one parseable (control) | `ok` | 200 | 200 |

  For every stem the list's `record_status` set predicts the detail answer. In `acl` with only
  `validation:read` the same records are all 404 (fail-closed, no existence oracle).
* **Depth boundary** (`c5-12`): a record whose directory is 3 and one 4 levels below the root are
  served; 5 levels is pruned; `ignored_entries == 1` with a `depth_cap` sample naming `…/d4`;
  `discovery.max_depth == 4`.
* **Bounded reads** (`c5-17`, `c5-18`): a `manifest.json` of 1 MiB + 1 is 409 `validation_manifest_corrupt`
  with the rule naming the bound and **no `manifest_sha256`** (never read), its artifacts 404
  `manifest_corrupt`; a run record of 16 MiB + 2 is a `corrupt` list row with `record_sha256: ""` and a
  reason naming the bound, `corrupt_count == 1`, detail 409 `validation_run_corrupt`.
* **Counters describe the filtered served set** (`c5-14`, `c5-15`): unfiltered `7/3/2`; `verdict=PASS`
  serves 4 rows with `corrupt_count 0`, `duplicate_count 2`; a filter matching nothing → `total 0`,
  both counts 0, `empty_reason == "all_filtered_out"`.

### The new non-regular-file rule (N3) — the reason this candidate exists

`c5-20-fifo-not-regular-file.json`, `c5-21-acl-fifo-not-regular-file.json`,
`c5-23-non-regular-socket.json`; checks in `c5_pages-checks.txt`.

* The validator created a **real FIFO** on disk named `run-20261008T000402Z.json`
  (confirmed `stat.S_ISFIFO` true) beside one real regular record.
* `single_user` list (`?limit=100`, client timeout **6 s**): **status 200 in 0.0018 s** — no hang. The
  FIFO is not served as a run row; the one regular sibling is served (`total: 1`); `ignored_entries >= 1`;
  **exactly one `ignored_samples` entry with `reason: "not_regular_file"` naming the FIFO**
  (`run-20261008T000402Z.json`); no other reason is used for it.
* By-id `GET /runs/run-20261008T000402Z`: **404 `validation_run_not_found` in 0.0015 s** — the reader
  never opens the FIFO.
* Bonus same-class probe: a **Unix domain socket** named `run-20261008T000403Z.json` in a short `/tmp`
  root (created and cleaned up by the harness) is likewise refused, answered in 0.001 s, and counted
  with reason `not_regular_file`.
* In `acl` mode the same FIFO root answers in 0.0014 s with `ignored_entries == 1` and the FIFO not in
  `runs[]`; `ignored_samples[]` is empty **by documented design** (ADR 0015 §5.1 AD2 withholds relative
  paths in `acl`), so the `not_regular_file` *reason* is observable in `single_user` and the *count* is
  the `acl` observable. See "Validator corrections" below.

### Timing — no request hung

The harness times **every** request (`[timing] #n METHOD path seconds status`, and
`c5-22-request-timings.json`). Across all seven scripts, **170 requests** were issued
(c1=4, c2=12, c3=30, c4=56, c5=46, c6=17, extra=5): every one returned an HTTP status within its client
timeout, **max 0.0125 s (12.5 ms)**, `over_5s = []`, `requests_without_status = []`,
`transport_retries = 0`. The FIFO probes (list, by-id, acl, socket) were answered in **≤ 1.8 ms** each.
**Explicitly: no request hung, and no transport-level retry was needed in the recorded run.**

## Criterion 6 — API values agree with source records and file digests: **PASS** (397/397)

Evidence: `c6-01` … `c6-10`, `c6-08-field-comparison-table.json`,
`c6-09-field-comparison-table.txt`: **373 field-by-field comparisons, 0 mismatches**, plus 24
consistency checks (397 total).

Fields recomputed by the validator from the bytes on disk, then compared to the served value:

* **Real nine, list rows** — `run_id`, `stage`, `created_at`, `commit`, `schema_version`, `core_version`,
  `verdict`, `case_counts{pass,fail,blocked,total}`, `record_sha256` (validator's own SHA-256 of the raw
  `run-*.json` bytes), `synthetic`, `manifest_status` (all `absent`, and no `manifest.json` exists on
  disk for any of the nine).
* **Real nine, detail** — `run_id`, `stage`, `created_at`, `commit`, `schema_version`, `core_version`,
  `verdict`, and per case `verdict`, `failure_class`, `failure_output`.
* **Fixture bundle** — `manifest.manifest_sha256` (SHA-256 of the raw `manifest.json`),
  `manifest_authentication == "unauthenticated"`; per artifact `declared_sha256`, `declared_bytes`,
  `bytes`, `digests.original`, `digests.masked`, `integrity`; content `original_sha256`, `masked_sha256`,
  `served_sha256`, `content`, `bytes`, `mask_algorithm`, `mask_version`. Masked and original digests were
  recomputed as `sha256(canonical.redact_text(text))` and `sha256(raw bytes)`.
* An absent manifest is reported `status: "absent"` with **no** `manifest_sha256` — nothing fabricated.

## Implementation module and full suite (fresh clone)

Script `final_fullsuite_freshclone.sh`; provenance `fullsuite-provenance.txt`; raw output
`fullsuite-candidate-module.txt`, `fullsuite-candidate.txt`, `fullsuite-baseline.txt`.
Fresh clone `git clone --no-hardlinks` of `jasmine-core-v1` into `/tmp/p7-01-final-clone`, checked out at
the candidate SHA; clone `HEAD = 0ae8041…`, `git status --porcelain` empty. The operator's real root was
passed via `P7_REAL_RUNS_ROOT`; `PYTHON=python3.13`.

| Run | Result |
| --- | --- |
| Candidate module `tests/test_validation_read.py` (fresh clone) | **Ran 48 tests in 1.964s — OK** |
| Candidate **full** suite (`PYTHON=python3.13 ./scripts/run-tests.sh`) | **Ran 1414 tests in 45.427s — FAILED (failures=5, errors=4, skipped=13)** |
| Baseline `0f7b2ec` (parent of the phase-2 implementation) full suite | Ran 1366 tests in 41.880s — FAILED (failures=5, errors=4, skipped=13) |

Candidate and baseline have an **identical failure set** (diff of the `FAIL:`/`ERROR:` lines is empty):
the same nine tests, all in the **pre-existing P4 environment set** — hook-installer / native-session
tests that fail with `canonical venv launcher parent required` because the clone's `python3.13` is not a
canonical-venv launcher (8 occurrences of that reason in each transcript). They are **environmental and
pre-existing, not introduced by P7-01**. The candidate adds 48 tests (1414 − 1366), all passing, and the
P7 module is 48/48.

## Counterexamples and defects

**None affecting the six acceptance criteria.** 904/904 criterion checks passed; 0 `[FAIL]` lines across
all seven scripts.

Observations (informational; no severity; no code change implied):

1. **The literal `..`-substring rule is broader than a segment check.** `artifacts/note..txt` is refused.
   This is the proposal's own literal wording ("contains no `..`", no segment qualifier) and fail-safe
   (it can only turn a would-be 200 into a named 409), and `46a33d7` records it as deliberate in ADR 0015
   §5.3. The spec owner may wish to state whether a `..` inside an ordinary component is intended to be
   allowed.
2. **`acl` withholds `ignored_samples[]` (count only).** Documented ADR 0015 §5.1 AD2 (relative paths can
   name operator directory structure); confirmed as documented. It means the `not_regular_file` *reason*
   is observable only in `single_user`; `acl` reports it in the `ignored_entries` count.
3. **By-id unknown ids over an incomplete tree return 503 `index_incomplete`** rather than 404 (ADR 0015
   §5.0, F5), and `acl` + `validation:unattributed` can reach the 409 duplicate/corrupt family (F6);
   both behave exactly as documented, with fail-closed 404 still returned without the grant.
4. **Duplicate rows legitimately carry no `mode`/`attribution`/`evidence_grade`**, so a
   `mode`/`attribution`/`project` filter never matches a duplicate row (ADR 0015 §5.3). Fail-closed
   consequence of the duplicate rule, not a missing field.

## Validator corrections (recorded for transparency)

1. My first `acl` FIFO check expected `ignored_samples` to name the FIFO and failed. The failure was
   **my expectation, not the implementation**: ADR 0015 §5.1 AD2 deliberately empties
   `ignored_samples[]` in `acl` mode. I re-read the implementation/ADR, corrected the check to the
   *count* (`ignored_entries == 1`) plus the documented empty-samples invariant, and re-ran the whole of
   criterion 5 from scratch (raw re-run in `c5_pages-checks.txt`). No implementation finding arises.
2. The criterion-5 harness was extended for this candidate to time every request and to add the FIFO,
   socket, `acl` real-root page-walk and `acl` FIFO probes; those additions account for the criterion-5
   check count rising from 120 to 145. No pre-existing check was weakened.

## Environment record

Full record: `env-final-environment.txt`. Summary:

| Item | Value |
| --- | --- |
| Time (UTC) | 2026-10-08T14:56Z–14:59Z |
| Core version | `0.1.0` |
| Schema version | `11` (`migrations.check_version`; `PRAGMA user_version` is not the schema counter) |
| `canonical.REDACT_TEXT_VERSION` | `1` |
| Python | `/opt/homebrew/bin/python3.13` (3.13.15) |
| Platform | macOS-27.0.1-arm64 |
| Modes exercised | `single_user` **and** `acl` |
| Real root | `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs` (read-only; 9 regular `run-*.json`, digests + mtimes recorded, unchanged) |
| Fixture roots | per-run `TemporaryDirectory(prefix="validate-p7-01-")`; one short AF_UNIX root `/tmp/v7f-*` (created and removed) |
| Scopes used (token values never recorded) | `validation:read`; `validation:read` + `validation:unattributed`; `admin`+`objects:read`+`objects:write` (project creation only); `objects:read` (deliberately scope-less key) |
| Tracked tree | 0 modified tracked files; nothing committed/pushed |

## What this validation covers and does not cover

* **Covered:** the five `GET` routes of the minimal read slice, over a real loopback socket against a
  real migrated DB, in both `single_user` and `acl`, on the nine real P0 records and on validator-built
  fixture roots — criteria 2, 3, 4, 5 and 6, including the changed duplicate-resolution, discovery-depth,
  declared-path, bounded-read, counter and **non-regular-file (FIFO/socket)** behaviours, with every
  request timed. Supplementary spot-checks (separate from the six): GET-only routes (405 on POST),
  unset/symlinked-root refusals, malformed id and unknown query-key 400s. The implementation's own
  module and the full suite were run from a fresh clone at the candidate SHA.
* **Not covered / not accepted here:** criterion 1 remains **BLOCKED** pending a real P7-format FAIL run.
  This validation does **not** accept the P7 stage, the Scenario Runner, the 30-scenario suite, any
  release verdict, or the P6-T06 browser drill-down. Only the read slice described by
  `web/contracts/p7-validation-read.v1.json` is inside scope.

**Explicit repeat:** criterion 1 is **BLOCKED** — 0 real FAIL cases / 0 `failure_class` /
0 `failure_output` across the nine real records — and stays BLOCKED until the P7 owner records one
genuinely failing scenario with `recorder.py` into the configured run root.

## Reproducing this validation

```
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/VALIDATE-p7-01/evidence-final"
bash final_run_all.sh                 # census + all six criteria + supplementary probes
bash final_fullsuite_freshclone.sh    # fresh clone: module test + full suite + baseline suite
/opt/homebrew/bin/python3.13 final_env.py   # environment record
```

| Script | Purpose |
| --- | --- |
| `final_analysis_real_records.py` | read-only census of the real records |
| `final_harness.py` | live-socket harness with per-request timing, key issuance, grants, fixture builders |
| `final_c1_fail.py` … `final_c6_values.py` | criteria 1–6 |
| `final_extra_probes.py` | supplementary contract probes |
| `final_env.py` | environment record |
| `final_run_all.sh`, `final_fullsuite_freshclone.sh` | runners |
