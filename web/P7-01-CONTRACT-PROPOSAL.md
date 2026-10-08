# P7-01 frozen-contract **proposal** — the minimal Validation read slice

Status: **proposal, revision 3, awaiting delta re-review.** Nothing here is implemented yet. This is the
phase-1 deliverable the dependency taskbook asks for: the contract is proposed, independently reviewed,
revised, re-reviewed, and only then frozen as a machine-checked contract
(`web/contracts/p7-validation-read.v1.json`, derived by `scripts/p7/p7_contract.py`) and implemented.

Revision history (each revision was reviewed by an independent agent in its own context; both reviews are
kept verbatim under `validation-quarantine/REVIEW-p7-01-proposal/`):

| Rev | Commit | Independent verdict |
| --- | --- | --- |
| 1 | `3ebe46c` | **APPROVE WITH NAMED MANDATORY CHANGES** (12); facts (a)–(e) CONFIRMED — `findings.md`, 493 lines |
| 2 | `f397213` | **APPROVE WITH NAMED MANDATORY CHANGES**; rev-1 items 1, 3, 4, 8, 10, 11 only partially closed, 10 new defects N1–N10 — `RE-REVIEW-findings.md`, 658 lines |
| 3 | `c8fd597` | **APPROVE WITH NAMED MANDATORY CHANGES**; all 11 rev-2 mandatory items CLOSED, six new defects D1–D6 — `RE-REVIEW-2-findings.md`, 731 lines |
| 4 | this document | folds D1–D6 and D7, and retains the dry-run evidence |

Baseline: `integration/p6-03-realcore` @ `039f5a3e7800704e5f0478d01992f00eeb2ca3f1`, in its own worktree
`_worktrees/p7-01` on branch `p7-01-validation-read`.

## 0. What this slice is, and what it is not

It **reads existing Validation run records** written by `src/jasmine_core/validation/recorder.py`, serves
the verdict they already carry, shows what each recorded case is missing and what its evidence grade is,
and — when a bundle manifest exists — serves declared text artifacts safely. It does not run scenarios,
does not write, does not re-sign or upgrade a verdict, and does not implement the P7 stage.

Two operator decisions are already taken and are part of this proposal:

1. **Acceptance criterion #1 (read an existing real FAIL Run) is BLOCKED.** The nine real run records on
   disk (`validation-runs/**/run-*.json`, all `stage: P0`) are PASS(3) / BLOCKED(6) at run level and
   PASS(57) / BLOCKED(6) at case level; there is **no FAIL case anywhere** and no record carries
   `failure_class`/`failure_output`. The slice is verified against those real records plus a
   **clearly-labelled synthetic FAIL fixture** kept outside the operator's run root (§9), and the
   real-FAIL-run claim stays BLOCKED with its reason. A fixture is never evidence, and no verdict is
   upgraded. §9 names the unblock action.
2. **The run root is explicit configuration only** (`JASMINE_CORE_VALIDATION_RUNS`), with **no default**.
   Unset ⇒ every validation route refuses `503 validation_not_configured`. A request can never name a path.

## 1. The record shapes this slice reads (exact)

From `recorder.py` (unchanged by this slice):

* a **run** is one JSON file `<run_id>.json`; `run_id = run-YYYYmmddTHHMMSSZ`; fields `stage, created_at,
  commit, schema_version, core_version, environment{…}, executor, reviewer, verifier, cases[], notes[],
  not_implemented[]`, optional `redactions[]`/`redaction_warning`, and a **computed** `verdict` written
  into the file;
* a **case** has `case_id, name, verdict ∈ {PASS, FAIL, BLOCKED}` and the seven `SECTIONS` `user_raw,
  agent_response, tools, state_before, state_after, interpretation, context`, plus `failure_class,
  failure_output, reproduction[]`, the **case field** `evidence[]`, and `not_applied_reason`;
* the seven sections are **not uniform**, and the contract must not assume `sentinel ⇔ absent`:
  * `tools` defaults to `[]`, never to the sentinel; a real record holds `tools: [...]` or `[]`;
  * `user_raw`, `agent_response`, `interpretation` hold either a value or the sentinel
    `"N/A (P0 not implemented)"`;
  * of 63 real cases, 57 `context` values are non-sentinel and **27 of those are `{"reason": "…"}`-only
    objects** — a recorded *excuse*, not a context pack. Section presence therefore uses the
    `present_kind` rule in §1.1;
* **`evidence[]` is free-form**: the real records hold prose (`"the bundle holds the raw stdout of every
  command above"`) and **absolute host paths** (21 entries, 12 absolute). This slice treats every such
  string as **`unaddressable`** — surfaced verbatim as recorded data, **never resolved, opened or
  fetched**. Artifacts are addressable only through a manifest (§3).
* **There is no run-level project attribution field.** The real records do contain `prj_` ids *nested
  inside case data* (`.cases[].state_before.projects`, `.cases[].tools[].response.*.project_id`). This
  slice **never derives attribution from them**: attribution comes only from a manifest's `projects`
  (§3), so the nine real records are `attribution: unattributed`. Nested ids are surfaced only as
  recorded case data, under their original keys, and are explicitly not an attribution source.
* a record may carry keys this reader does not interpret. They are never dropped silently: detail returns
  `unknown_record_keys[]`, the sorted list of top-level keys found in the record that this contract does
  not name. `synthetic` and `synthetic_note` are interpreted (§4.1), so they are not "unknown".

### 1.1 Section presence (`present_kind`)

For each of the seven sections the detail response reports
`{present, present_kind ∈ {value, reason_only, sentinel, absent}, value?, recorded_reason?,
missing_reason?, evidence_grade, context_pack_present?}`, where `context_pack_present` is present on the
`context` section only:

| `present_kind` | Rule |
| --- | --- |
| `absent` | the record has no such key (`missing_reason: "the record has no such field"`) |
| `sentinel` | the value equals `"N/A (P0 not implemented)"` (`missing_reason` quotes the sentinel) |
| `reason_only` | the value is a **mapping** whose key set is a subset of `{reason, note, detail, not_applied_reason}` → **`present: false`**, `recorded_reason` carries `reason` (or the first value) |
| `value` | anything else → `present: true` |

`reason_only` applies **only to mappings**. A non-empty string is `value` (content), a non-empty list is
`value`, an empty string or empty list is `absent` with `missing_reason: "recorded as empty"`. This is a
purely structural test: no prose is pattern-matched to guess whether it is "a reason".

**`present_kind` describes whether the section was *recorded*; the chain link is stricter.** For `context`
only, the section additionally carries `context_pack_present: bool`, true iff the value is a mapping
holding at least one of `context_id, pack, sections, rendered, content, tokens, parts`, or is a non-empty
list. **The chain link `context` (§4.1) requires `present ∧ context_pack_present`**; a section can be
recorded without being a pack. This distinction is load-bearing on the real data: of the 63 cases, 6
`context` values are the sentinel, 27 are `{"reason": …}`-only, and **30 are recorded run details**
(`{"db_path": …, "processes": […]}`) — genuinely recorded, but containing no context pack. No real case has
a pack, so the Context link is missing for three distinct, nameable reasons rather than one, and the 30 are
never counted as a pack.

The **recorded verdict is always served as recorded**. `verdict_consistent` reports only whether it equals
the recorder's worst-case rule (it does, for all nine); nothing here re-signs or upgrades anything.

## 2. Discovery: root, walk, identity, ordering, pagination

The nine real records sit in **nine distinct leaf directories at depth 2–3 below `validation-runs/`** — no
single directory holds all nine — so discovery is defined completely and is the routing table for
acceptance criteria 5 and 6. This walk was dry-run against the real tree and against a synthetic
adversarial tree (§9); the real tree yields exactly 9 run-shaped records out of 288 scanned entries.

**Root.** `JASMINE_CORE_VALIDATION_RUNS` (absolute or relative; fits the existing `JASMINE_CORE_*`
convention). Unset ⇒ `503 validation_not_configured`. The configured value is read lazily on first use and
cached **against the raw configured string**, so a test or a restarted deployment can point at a different
root without a stale cache; a request can never name or extend the path. The root is opened with
`O_DIRECTORY|O_NOFOLLOW`, so a **symlinked root is refused**. Missing, not a directory, `OSError` on open,
or a permission failure ⇒ `503 validation_not_configured` with `details.reason ∈ {unset, missing,
not_a_directory, symlinked_root, root_unreadable}`.

**Walk.** Recursive, **at most 4 directory levels below the root** (the nine need ≤3), and:
* directories whose name starts with `.` or is `__pycache__` are not entered;
* a **symlinked directory is never traversed** (one `ignored_entries` entry, reason `symlinked_directory`);
* only regular files are considered; a **symlinked `run-*.json` is not a record**
  (`ignored_entries` reason `symlinked_file`).

**Run-shaped.** A file is *run-shaped* iff its basename matches `^run-[0-9]{8}T[0-9]{6}Z\.json$`. Its
**identity is the filename stem**, and the JSON must be an object whose `run_id` equals the stem with
`stage` (string), `created_at` (string), `verdict ∈ {PASS,FAIL,BLOCKED}` and `cases` (list) present.
Anything else run-shaped is `record_status: "corrupt"` — still listed, with `corrupt_reason` naming the
rule, never dropped.

**Statuses and their precedence.** A run-shaped file gets exactly one status:
`corrupt` (unparseable, wrong `run_id`, or missing a required field) > `duplicate` (parseable, but another
run-shaped file shares its stem) > `ok`. Corrupt wins because a file that cannot be parsed has no
`run_id` to compare, so duplicate detection considers only parseable records. Duplicates are **both**
listed, `corrupt_reason`/`duplicate_reason` names the sibling count, and detail refuses the stem with
`409 validation_run_ambiguous`.

**Corrupt vs ignored (the boundary).** `corrupt` = run-shaped but unusable. `ignored` = not run-shaped.
`ignored_entries` is a count of walk entries that were **not treated as records**; a **pruned directory
counts as one entry** and its contents are not enumerated, so the number is a walk diagnostic, not a file
count. Ignored entries are never silently promoted, and a corrupt record is never reported as an empty
list.

**Ordering (a strict total order).** This tuple is the single authority:

```
(status_rank asc: ok=0, duplicate=1, corrupt=2,
 created_at_key desc,
 run_id_key desc,
 relative_path asc)
```

`created_at_key` is the recorded `created_at` for a parseable record and `""` for a corrupt one (so corrupt
rows sort last and never interleave); `run_id_key` is the **filename stem** for every row (equal to the
recorded `run_id` for parseable records). `relative_path` is unique by construction and is the final
tie-break, so two files sharing a `created_at`/`run_id` can never make a page overlap or skip a row.

**Pagination and `total`.** `limit` default 20 / max 100; `offset` ≥ 0. `total` is the number of
discovered run-shaped records that **pass the filters**: with no filter that includes corrupt and
duplicate rows; with a filter it counts only matching rows. A **corrupt** row has none of the filtered
fields and therefore never matches a positive filter (it appears only in an unfiltered listing), while a
**duplicate** row is parseable and matches filters normally. `count == len(runs)` for the current page, and
`has_more = offset + count < total`; page *n* is `offset = n·limit` over the same total order.
Filters (`verdict`, `stage`, `mode`, `attribution`, `project`) use an unknown value ⇒ `400
invalid_request`, and **unknown query keys are refused** with `400 invalid_request` (precedent:
`get_resolution` refuses any unexpected query).

**Response.** `{runs[], count, total, limit, offset, has_more, corrupt_count, duplicate_count,
ignored_entries, discovery, empty_reason?, warnings[]}` where
`discovery: {root_present, max_depth, scanned_dirs, scanned_files, run_shaped_files,
unreadable_entries, capped}`. A row carries `run_id, stage, created_at, commit, schema_version,
core_version, verdict (as recorded), case_counts{pass,fail,blocked,total}, mode, mode_basis,
attribution{status, projects[]}, evidence_grade, synthetic, synthetic_note?, manifest_status,
record_status, corrupt_reason?, duplicate_reason?, record_sha256, verdict_consistent`.

**`record_sha256` is the SHA-256 of the run record file's raw bytes** — exactly what `sha256sum` over the
file returns — so the validator can recompute it without knowing the reader's parser.

**An incomplete index is never paginated.** An `OSError` while walking a subtree (a permission failure on
a directory, a failed `stat`, and so on) skips that subtree and increments
`discovery.unreadable_entries`, which is named in `warnings[]` (≤20 samples). A traversal that is knowingly
incomplete must not be paged: **if `unreadable_entries > 0`, the list route returns
`503 validation_not_configured` with `details.reason: "index_incomplete"` and the counts** — whether or not
records were found — so no page walk can silently omit rows. `complete: bool` is returned on every
successful list (always `true`, since a `false` index never returns 200), and detail/manifest/artifact
reads are unaffected because they address a known run id and need no index. When the walk was complete,
`empty_reason ∈ {no_run_shaped_files, all_filtered_out}` with the counts that produced it; a complete tree
that could not be read at all is `details.reason: "root_unreadable"`.

**Caps.** `MAX_SCANNED_ENTRIES = 20_000` and `MAX_RUN_SHAPED = 500`; exceeding either returns
`503 validation_index_too_large` naming the cap rather than a silently incomplete index.

## 3. Bundle manifest (convention frozen by this slice)

A run directory may contain `manifest.json`:

```json
{
  "schema": "jasmine.validation-bundle.v1",
  "run_id": "run-20260929T162925Z",
  "frozen_at": "2026-09-29T16:30:00Z",
  "mode": "real | component | simulated",
  "projects": ["prj_…"],
  "sources": {"recorder": {"path": "scripts/p0-acceptance.py", "sha256": "<64 lowercase hex>"}, "note": "…"},
  "files": [
    {"artifact_id": "art_stdout", "path": "artifacts/codex-stdout.txt",
     "bytes": 1234, "sha256": "<64 lowercase hex>", "media_type": "text/plain",
     "credential_shaped": false}
  ]
}
```

A manifest that breaks any rule below is **`corrupt`** (with the broken `rule` named); its artifacts are
then unaddressable rather than partially trusted.

**Grammar and identity (string rules, applied at load).**
* `schema` is exactly `jasmine.validation-bundle.v1`; `run_id` equals the run record's id;
* `files[].artifact_id` is unique and matches `^art_[a-z0-9][a-z0-9_.-]{0,63}$` — a **bundle-scoped** id,
  deliberately not a Core public id, so no ID prefix is allocated;
* `files[].path` is **relative** with every component non-empty and contains no `..`, no backslash, no
  leading `/`, no NUL and no URL scheme; `C:…` drive-relative and `\\…` UNC forms are explicitly refused;
* `files[].sha256` is exactly 64 lowercase hex; `files[].bytes` is a non-negative integer;
* a declared path that fails a string rule makes the whole manifest corrupt (verified by dry run: `../`,
  absolute, `C:`, UNC and URL all refuse the manifest with the offending entry named).

**Symlinks are refused at open, not at load — deliberately.** A symlink is a filesystem property, not a
declared string; a symlink swapped in *after* freezing is an attack to **report**, not a reason to hide
the whole manifest. Reading a declared file therefore uses the repo's existing **per-component
descriptor-relative `O_NOFOLLOW` walk** (`fingerprint.hash_workspace_file` opens every component with
`dir_fd`, rejects `.`/`..`/absolute parts, requires `S_ISREG`, and enforces a byte cap). Because that
helper returns only `(digest, length)` and cannot hand back the verified buffer, the reader **mirrors its
walk locally**: open each component with `dir_fd`+`O_NOFOLLOW`, read the file **once** into a capped
buffer, verify the digest of *that buffer*, and serve exactly that buffer. Final-component `O_NOFOLLOW`
plus an `lstat`/inode comparison is explicitly **not** sufficient: it cannot see a symlinked ancestor.
`realpath` containment is kept as a **second, non-primary** check (it is a check-then-open and therefore
races). A test asserts the mirrored walk and `hash_workspace_file` return the same digest for the same
file, so the mirror cannot drift.

**Integrity.** For every declared file the reader reports
`integrity ∈ {match, mismatch, missing, symlink_refused, unreadable}` plus `bytes` agreement;
`mismatch` is never served as content, and a `symlink_refused` file answers `404 artifact_not_found`
with `reason: "symlink_refused"`.

**Manifest response.** `status ∈ {frozen, absent, corrupt}`: `frozen` when a manifest is present and passes
every rule below, `absent` when the run directory holds no `manifest.json`, `corrupt` when one is present
but breaks a rule (and its artifacts are then unaddressable).
`{status, reason?, rule?, run_id, frozen_at?,
declared_mode?, mode, mode_basis, projects[], sources, files[], manifest_sha256,
manifest_authentication: "unauthenticated"}`. `absent` is a 200 whose reason states that the record
predates the convention and its history is **not reconstructed**; `corrupt` carries `rule`. `sources` is
returned verbatim. `manifest_sha256` is the SHA-256 of the manifest file's raw bytes;
`manifest_authentication: "unauthenticated"` states in the API itself that `frozen_at`, `mode` and
`credential_shaped` are self-declared and unsigned — the reader never calls them verified.

**Effective mode (`mode` vs `declared_mode`).** `mode` is derived, not trusted:

| Condition | Effective `mode` / `mode_basis` |
| --- | --- |
| no manifest | `unknown` / `"no bundle manifest on disk"` |
| `mode` missing | `unknown` / `"the manifest declares no mode"` |
| `mode: real` **and** `projects` non-empty **and** `sources.recorder` is `{path, sha256: <64 hex>}` or `"<relative path>@<64 lowercase hex>"` | `real` / `"declared real with recorder provenance and project attribution"` |
| `mode: real` without that provenance | `unknown` / `"declared real without recorder provenance or project attribution"` |
| `mode: component\|simulated` | as declared / `"declared by the manifest"` |

The provenance forms are the **same digest length in both spellings** (64 hex, a SHA-256); the object form
is preferred. Recorder provenance qualifies `mode: real` but does not verify it (§4.2).

**Attribution.** `attribution.status ∈ {attributed, unattributed}` and `projects[]`, derived only from the
effective manifest `projects`: non-empty ⇒ `attributed`; manifest present with `projects` empty or absent,
no manifest, or a corrupt manifest ⇒ `unattributed` with
`attribution.reason ∈ {declared_empty, manifest_absent, manifest_corrupt}`. Mode filters match on the
**effective** mode.

## 4. Chain links and evidence grade

### 4.1 The eight chain links

The taskbook's T06 chain is **Raw → Extraction → Resolution → Context → Agent → Tool → Evidence → State**.
The recorder records **six** of those sources and has **no source at all** for two of them. The mapping is
therefore:

| Chain link | Recorded source | Missing when |
| --- | --- | --- |
| Raw | `case.user_raw` | `present_kind ∈ {sentinel, absent, reason_only}` |
| Extraction | **none** — `recorder.SECTIONS` has no extraction field | always; `missing_reason: "the recorder records no Extraction section"` |
| Resolution | **none** — `recorder.SECTIONS` has no resolution field | always; `missing_reason: "the recorder records no Resolution section"` |
| Context | `case.context` — recorded **and** `context_pack_present` (§1.1) | not a context pack (three named reasons) |
| Agent | `case.agent_response` | `present_kind != value` |
| Tool | `case.tools` | `tools == []` (`missing_reason: "the case records no tool call"`) |
| Evidence | `case.evidence` (a case field, **not** one of the seven `SECTIONS`) | empty list |
| State | `case.state_before` + `case.state_after` | both `present_kind != value` |

`case.interpretation` is a recorded `SECTIONS` member and is returned **verbatim as case data**. It is
**not** equated with Resolution: ADR 0008 and the P2 taskbook keep Interpretation (a candidate) and
Resolution (the resolved decision) distinct, and the recorder records only the former. Detail returns
`chain: {raw, extraction, resolution, context, agent, tool, evidence, state}` each as
`{present, source, missing_reason?}`. A missing link is named, never fabricated, and never changes the
verdict.

Verified on the real nine: `extraction` and `resolution` missing 9/9 (no recorded source exists);
**`context` missing 9/9 — every one of the 63 cases lacks a context pack**, in three distinct forms (6
sentinel, 27 `{"reason": …}`-only, 30 recorded run details); `agent` missing in the six BLOCKED runs and
present in the three PASS runs; `raw`/`tool`/`evidence`/`state` present 9/9.

### 4.2 Evidence grade (per case *and* per section)

`evidence_grade ∈ {real, component, simulated, unknown}` — **exactly the taskbook's four modes**
(真实/组件/模拟/未知), with no invented fifth value. Grades are ordered by **trust**:
`real > component > simulated > unknown`.

**Marker mapping (a total function, not prose).** A *marker* is a truthy key in a mapping. The frozen
mapping from marker key to grade is:

| Truthy marker key | Grade |
| --- | --- |
| `synthetic` | `simulated` |
| `simulated` | `simulated` |
| `fixture` | `simulated` |
| `mock` | `simulated` |
| `component` | `component` |

Any other key is not a marker. The vocabulary is a frozen, **structural** list; there is **no substring
search over free text**.

**Case grade** — first matching rule wins, and no rule ever upgrades a graded case:

1. `run.synthetic == true` ⇒ `simulated`, `grade_basis: "the run record marks itself synthetic"`;
2. `case.agent_response` is a mapping carrying a marker (per the table) ⇒ that marker's grade,
   `grade_basis: "recorded in the case"`;
3. `run.executor` matching the frozen pattern `(?i)(fixture|mock|simulat|synthetic)` ⇒ `simulated`,
   `grade_basis: "run.executor"`;
4. otherwise the **effective bundle `mode`**, when it qualifies (§3) — `real`/`component`/`simulated`,
   `grade_basis: "the qualifying bundle manifest"`;
5. otherwise **`unknown`** — never `real` by the absence of markers.

**Section grade** = the section's own marker's grade if that section's value is a mapping carrying a
marker, otherwise the case grade. Because a marker maps into the same four-value enum, a section grade can
never exceed its case grade.

**Run grade = the least trustworthy of the effective bundle mode and every case grade** (min over the
trust order above), so a `component` bundle mode with one `simulated` case yields `simulated`, not
`component`, and a bundle whose mode is `unknown` is never improved by a case grade. Whenever the run grade
is less trustworthy than the effective bundle mode, `warnings[]` names the case that caused the downgrade:
`"manifest declares <mode> but case <case_id> is graded <grade>"`.

**Stated limit.** Detection is marker-based. A `mode: real` manifest paired with an *unmarked* mocked
`agent_response` cannot be detected by a read-only contract, and the API does not claim otherwise: the
reader never asserts `real` on its own authority, `manifest_authentication: "unauthenticated"` says the
manifest is unsigned, and closing this fully requires the **recorder** to write a per-case grade — a P7
recorder change, explicitly outside this slice (§10) and named as the residual for acceptance criterion 2.

**Labelling is contract-enforced, not just process-enforced.** `synthetic` and `synthetic_note` are read
from the record and surfaced in the list row and detail (`synthetic: false` when absent); a
`synthetic: true` record is graded `simulated` and cannot be reported as real. Fixtures are *additionally*
kept out of the operator's run root (§9), but the contract does not rely on that alone.

## 5. Routes (all `GET`, all requiring `validation:read`)

| Route | Purpose |
| --- | --- |
| `GET /v1/validation/runs?limit&offset&verdict&stage&mode&attribution&project` | list (§2) |
| `GET /v1/validation/runs/{run_id}` | recorded detail, case by case, with chain and grade |
| `GET /v1/validation/runs/{run_id}/manifest` | manifest status, sources, file list, digests, integrity |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}` | artifact metadata (no content) |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}/content?form=masked\|original&download=1` | text content, authorised, masked, digest-pinned |

**Detail** returns the recorded run fields verbatim plus `synthetic`, `synthetic_note?`,
`unknown_record_keys[]`, `evidence_grade`, and, per case:
`case_id, name, verdict (as recorded), failure_class, failure_output, reproduction[], not_applied_reason,
evidence: [{recorded, status: "unaddressable", reason}], sections{…§1.1}, chain{…§4.1},
evidence_grade, grade_basis`.

**Artifact metadata** returns `{artifact_id, path (as recorded), bytes, declared_sha256, declared_bytes,
integrity, media_type, forms: ["masked"] or ["masked","original"], digests: {original, masked},
credential_shaped, content_scan}`. The two digests are **separate named fields**; there is no bare
`sha256`. `content_scan` reports the reader's own scan of the actual bytes
(`{performed: true, mask_is_noop: bool, credential_shaped: bool}`) and is independent of the manifest's
declared flag.

**Content.** Text only: allowed iff `media_type` starts with `text/` or is one of `application/json`,
`application/jsonl`, `application/x-ndjson`, `application/xml`, `application/yaml`,
`application/x-yaml`; anything else is `415 unsupported_media_type` rather than a guessed type.
`form=masked` (default) serves `canonical.redact_text(text)` — the **substring** masker used by the audit
layer — with `mask_algorithm: "canonical.redact_text"` and
`mask_version: canonical.REDACT_TEXT_VERSION` (a new module constant in §11), so `masked_sha256` is
reproducible from `(algorithm, version, input)` and a change to the pattern table must bump the constant.
`sanitise_secrets` is deliberately **not** the masker: it is a *containment* search
(`looks_like_credential` uses `p.search`) that replaces the **entire value** on any hit, so it cannot mask a
secret **in place** inside a longer log line; the reason for choosing `redact_text` is its **broader pattern set**
(it also masks `password=…`, `api_key: …` and other assignment shapes) and its in-place, line-preserving
masking — not any claim about whole-string versus substring matching.

**`form=original` is served only when the masker is a no-op on the content**, which makes the gate at
least as strong as the masker by construction. All four must hold: the bytes were read once under the cap
and hash to the declared `sha256`; the text decodes as strict UTF-8; the manifest does not declare
`credential_shaped: true`; and **`canonical.redact_text(text) == text`**. Any failure ⇒
`403 original_withheld`, with `details.masked_sha256` telling the caller what *is* available and
`details.reason ∈ {credential_shaped_declared, mask_not_noop, not_utf8}`. This closes the rev-2 hole where
an assignment-shaped secret (`db_password=…`, `{"api_key": "…"}`) was masked by `form=masked` yet served in
cleartext by `form=original`, because `looks_like_credential` alone does not match those shapes (verified
by dry run: `looks_like_credential: False` and `redact_text` masking both samples).

**Content response envelope (implementation fact).** The shared writer `api/server.py::_respond` always
sends `Content-Type: application/json; charset=utf-8` and **drops any handler-supplied `content-type`**.
The content route therefore returns a **JSON envelope**
`{artifact_id, form, media_type, bytes, served_sha256, original_sha256, masked_sha256, mask_algorithm?,
mask_version?, content, content_is_original}` — `content_is_original` is `form == "original"`, `true` only
when the original was actually served (so it is `false` for every masked response) — note
**`served_sha256`**, never a bare `sha256`, so the
digest's meaning is unambiguous — and sets only non-`content-type` headers, which do pass through:

`X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`,
`X-Validation-Form: masked|original`, `X-Validation-Sha256: <served digest>`,
`X-Validation-Original-Sha256: <declared digest>`,
`Content-Disposition: attachment; filename="<artifact_id>.txt"` when `download=1`.

Consequently HTML or script content can **never** be served as a renderable page — the envelope is JSON
and `nosniff` is set. Serving raw original bytes (a true download) would require a change to the shared
`server.py` and is **out of this slice's scope**; that limitation is stated here rather than worked around.

## 6. Permissions and isolation

**Staged check (the "before any filesystem touch" claim, made true and testable).**

1. `dispatch` authenticates and requires `validation:read` **before the handler runs** (verified:
   `resolve` → `authenticate` → `principal.require(scope)` → `auth.touch` → handler), so no table and no
   file is touched for a caller that fails authentication or scope.
2. The handler then resolves the id grammars (§7) and reads the run record (and, when asked, the
   manifest). **No run content, path, digest or status is placed in a response before step 3 passes.**
3. Project grants are then checked, and only then is the response built.

**Grants reuse the frozen P4 mechanism — nothing parallel is invented.** The deployment mode comes from
`memory.scope.deployment_mode()` (`JASMINE_MEMORY_DEPLOYMENT_MODE`, default `single_user`), grants are rows
in `memory_project_actors`, and the check is `memory.scope.ProjectAccess.require(project_id=…, actor_id=…)`
— the same object P4 uses, constructed from the same live `core.conn` connection the reader already holds,
so no P4 migration beyond the baseline is required. `require_mode_ready()` is called first: an `acl`
deployment with no grants at all refuses `503 memory_deployment_not_ready` (the existing frozen code)
rather than serving everything.

* **`single_user`**: `validation:read` suffices; the response still reports attribution and grade.
* **`acl`**: every project in the effective `projects` list is checked (**all**, never just the first).
* **Unattributed runs (including all nine real records)** additionally require `validation:unattributed`
  in `acl` mode, so reading evidence with no project fence is an explicit operator grant rather than a
  side effect.
* **`acl` mode is indistinguishable from "no such run".** Any run an `acl`-mode caller may not read —
  a non-granted project, an unattributed run without `validation:unattributed`, a corrupt/duplicate record
  (which fails closed, since it has no readable manifest and is therefore treated as unattributed), or a
  run that does not exist — is reported with the **same** status and code: `404 validation_run_not_found`.
  `ProjectAccess.require` raising `NotFound("project", …)` is caught and **translated** to the run-level
  code, so **no validation route ever emits `project_not_found`**. Reporting `project_not_found` for a
  non-granted project while reporting `validation_run_not_found` for an unknown id would hand the caller
  exactly the existence oracle the frozen P4 `not_found` convention exists to deny (this corrects rev 2,
  which fixed the 403 oracle but left this one).
* **Actor kind.** Any actor may carry `validation:read` (read-only, no write path, no escalation). The
  `validation:unattributed` scope is **not issued to `agent` keys** — `auth.issue_key` gains the same
  `ForbiddenActorKind` guard already used there for `interpretations:manage`, `authority:manage`,
  `state:accept`, `memory:manage` and `memory:reflect`. Rationale: unattributed evidence has no project
  fence at all, so the grant should be made only by a human/system actor. (This is a fail-closed default
  proposed by the implementer; the operator can overrule it explicitly and the contract will record the
  decision.)
* Root resolution itself is also a form of isolation: the root is operator configuration, cached against
  the configured value, and no request input can name, extend or escape it.

## 7. Errors and precedence (ADR 0003 shape; seven new code strings)

Existing codes reused: `invalid_request` 400, `unauthenticated` 401, `forbidden_scope` 403,
`unsupported_media_type` 415, `payload_too_large` 413, `memory_deployment_not_ready` 503. `NotFound(kind,
id)` is used for the not-found kinds (`validation_run_not_found`, `artifact_not_found`); `NotFound` never
emits a bare `not_found`. `schema_version_unsupported` and `database_busy` are **not** listed: they are
raised by the migration/DB layers at Core construction or on a busy database, and no validation route can
produce them, so the contract does not advertise them.

**Precedence is evaluated in three stages, and within the stage that applies to a route the *first
matching row wins*.** The table is therefore route-scoped rather than one global list: the `Route` column
names the routes a row can fire on (`L` = list, `D` = detail, `M` = manifest, `A` = artifact metadata,
`C` = content, `*` = every route). Stages: **A** authentication and scope (rows 1–2); **B** deployment and
request validity (rows 3–7); **C** resource resolution and content (rows 8–16). Stage A always precedes B,
and B always precedes C, so a caller that fails authentication never learns anything about the deployment,
and a malformed id never causes a filesystem read.

| # | Routes | Situation | Response |
| --- | --- | --- | --- |
| 1 | `*` | missing/unknown token | `401 unauthenticated` |
| 2 | `*` | missing `validation:read` | `403 forbidden_scope` (`details.required`) |
| 3 | `*` | root unset / missing / not a directory / symlinked / unreadable on open | `503 validation_not_configured` (`details.reason ∈ {unset, missing, not_a_directory, symlinked_root, root_unreadable}`) |
| 4 | `L` | the walk was knowingly incomplete (`unreadable_entries > 0`, whether or not records were found) | `503 validation_not_configured` (`details.reason: "index_incomplete"`) |
| 5 | `*` | `acl` mode with no grant rows at all | `503 memory_deployment_not_ready` |
| 6 | `L` | walk caps exceeded | `503 validation_index_too_large` |
| 7 | `*` | malformed `run_id` / `artifact_id` / `limit` / `offset` / unknown filter, `form` or query key | `400 invalid_request` |
| 8 | `D M A C` | `acl` mode and the run may not be read **for any reason** (project not granted, unattributed without `validation:unattributed`, or a corrupt/duplicate record failing closed) | `404 validation_run_not_found` |
| 9 | `D M A C` | unknown run id | `404 validation_run_not_found` |
| 10 | `D M A C` | `single_user` mode only: run record unparseable / wrong `run_id` / missing required field / duplicate stem | `409 validation_run_corrupt` / `409 validation_run_ambiguous` |
| 11 | `M` | `single_user` mode only: manifest present but breaks a rule | `409 validation_manifest_corrupt` |
| 12 | `A C` | artifact not addressable (manifest absent/corrupt, unknown `artifact_id`, declared file missing or symlinked) | `404 artifact_not_found`, `reason` distinguishing the cases |
| 13 | `A C` | file bytes disagree with the declared `sha256`/`bytes` | `409 artifact_modified` |
| 14 | `A C` | artifact content exceeds `MAX_ARTIFACT_BYTES` | `413 payload_too_large` (`details.limit`) |
| 15 | `C` | original form requested and the masker is not a no-op, or `credential_shaped: true`, or not UTF-8 | `403 original_withheld` (`details.reason`, `details.masked_sha256`) |
| 16 | `C` | non-text artifact content | `415 unsupported_media_type` |

Two consequences of the scoping, both deliberate: rows 8 and 9 are the **same** status and code, so in
`acl` mode the response cannot distinguish "does not exist" from "you may not read it"; and rows 10–11 are
`single_user`-only because in `acl` mode a record that cannot be read has already been answered by row 8 —
the `409` family is never reachable in `acl` mode, which is what makes the fail-closed rule hold without a
second oracle. Rows 12–16 are the only rows that can fire on `A`/`C`, so a corrupt manifest yields
`409 validation_manifest_corrupt` from `M` (row 11) and `404 artifact_not_found` from `A`/`C` (row 12, with
`reason: "manifest_corrupt"`) — the two routes answer differently on purpose, and each is single-valued.

**Id grammars are validated before any path construction** (frozen here):
`^run-[0-9]{8}T[0-9]{6}Z$` and `^art_[a-z0-9][a-z0-9_.-]{0,63}$` — anchored matches against the raw route
capture, which `request.split_target` does **not** percent-decode, so `..%2f` simply fails the grammar. A
path is only ever built from these two validated ids plus a manifest `path` that already passed §3.

**The seven new code strings** are recorded in `docs/adr/0015-p7-validation-read-contract.md`, per the
established convention that each domain documents its own codes (ADR 0006/0007 for
`missing_evidence`/`fingerprint_unavailable`, ADR 0008 for `interpretation_*`, ADR 0004/0009 for
`actor_mismatch`, ADR 0014 for the P4 codes): `validation_not_configured` (503),
`validation_index_too_large` (503), `validation_run_corrupt` (409), `validation_run_ambiguous` (409),
`validation_manifest_corrupt` (409), `artifact_modified` (409), `original_withheld` (403), each with its
trigger and the acceptance negative that exercises it. No existing code is redefined.

## 8. Resource limits

Root walk: depth 4, `MAX_SCANNED_ENTRIES = 20_000`, `MAX_RUN_SHAPED = 500`. Manifest: ≤ 1 MiB,
`files[]` ≤ 128 entries. Declared and served artifact text: ≤ `MAX_ARTIFACT_BYTES = 8 MiB`; masked output
is capped at the same size. List: `limit` ≤ 100. A declared `bytes`/`sha256` disagreement is
`409 artifact_modified`; content larger than the cap is `413 payload_too_large`; a manifest breaking the
size rules is `409 validation_manifest_corrupt` with the rule named; exceeding a walk cap is
`503 validation_index_too_large`. The only truncation anywhere is the explicitly-stated 20-sample cap on
the `ignored_entries`/`warnings` **samples**, whose counts remain complete. No route can be made to read an
unbounded amount of data, and no route silently returns a partial index.

## 9. Acceptance mapping, and the unblock action for criterion #1

Both the real-record rules and the adversarial rules below were **dry-run** read-only before
implementation: the real tree yields 9 records / 288 scanned entries with `verdict_consistent` for all
nine, and a synthetic tree exercises every negative.

| Criterion | How it is met |
| --- | --- |
| 1. an existing real FAIL Run, classified, raw output traceable | **BLOCKED** — no P7-format FAIL run exists (0 FAIL cases, 0 `failure_class`, 0 `failure_output` on disk). The FAIL *code path* is exercised on a synthetic fixture; the validator records BLOCKED with the reason. **Unblock action: the P7 owner records one genuinely failing scenario with `recorder.py` into the configured run root** (`validation-runs/p7-<scenario>/`), after which criterion 1 is re-validated against that real record. Until then `P6-T06`/P6-03c cannot close. |
| 2. missing Context / Agent shown as missing, no fabrication, no verdict upgrade | **Demonstrable on real records**: the Context chain link is missing in 9/9 because none of the 63 cases holds a context pack (6 sentinel, 27 reason-only, 30 recorded run details — each with its own `missing_reason`), and `agent_response` is missing in the six BLOCKED runs; `chain` names Extraction and Resolution as having no recorded source at all; the recorded verdict is served unchanged and `verdict_consistent` reports only. The residual (an *unmarked* mock under `mode: real`) is stated in §4.2, not hidden. |
| 3. authorised success and refusal paired on the same resource | §6/§7: each route with a granted key (200) and with a scope-less / non-granted-project / unattributed-less key (403 or 404) on the same run and artifact, in both `single_user` and `acl`; the `acl` refusals use one code, `404 validation_run_not_found`. |
| 4. artifact normal, missing, modified, escape | fixture manifest with a good file (200 + digest), a missing file (404), a byte-flipped file (409), a manifest whose declared paths use `..`, an absolute path, a `C:` form, a UNC form and a URL (all refuse the manifest with the offending entry named), a symlinked file and a **symlinked intermediate directory** (`integrity: symlink_refused`, 404 at content), and a log whose only secret is assignment-shaped (`db_password=…` / `{"api_key": …}`) served masked and **withheld** as original while `looks_like_credential` alone would have allowed it |
| 5. pagination, no duplicates or omissions, corrupt ≠ empty | §2's single ordering tuple with a unique `relative_path` tie-break; `total` includes corrupt/duplicate rows only when unfiltered and never mismatches the page arithmetic; a page-walk over the nine real records plus a corrupt row and a duplicate stem; `corrupt_count`, `duplicate_count`, `record_status`, `discovery` and `empty_reason` visible; an unreadable or partially unreadable tree returns 503 `index_incomplete`/`root_unreadable` instead of a page that quietly omits rows, while by-id reads still work |
| 6. API values match the records and file digests | tests read the same files directly and compare every served field, `record_sha256` (raw file bytes), `manifest_sha256` (raw file bytes), `declared_sha256`, `masked_sha256` and the original scan result, including masked ≠ original being reported as distinct fields (and equal when the masker is a no-op) |

The pre-implementation dry runs cited above are **retained as evidence**, not just as narration:
`validation-quarantine/DRYRUN-p7-01/` holds the two scripts and their captured output with input SHAs, so
the claims in §2/§3/§4.1 can be re-run rather than trusted.

**Fixtures are never evidence.** The synthetic FAIL fixture lives under `tests/fixtures/p7/` and in
validator-created temporary roots, **never** in the operator's configured run root; every fixture record
labels itself `synthetic: true` (a marker the API surfaces — §4.2), and no fixture result is counted
toward criterion 1.

## 10. Non-goals

No writes, no verdict re-signing, no Scenario Runner, no 30-scenario suite, no release verdict, no binary
artifacts, no raw-byte download route (would need a shared `server.py` change), no arbitrary path or URL
access, no HTML/script rendering, no change to the recorder (so no per-case grade *written* by the
recorder — §4.2), no migration, no new Core public ID prefix, no modification of any frozen P6/P4/P3
evidence or of the operator's services/global config/V0, no live entry, no push/PR/CI. `P6-T06` remains a
separate P6 browser drill-down once this read slice is accepted.

## 11. Shared-file changes, coordination and scope allocation

| File | Change |
| --- | --- |
| `src/jasmine_core/validation/reads.py` (new) | reader + `routes(route_cls)` factory, mirroring `memory/api.py::routes` |
| `src/jasmine_core/errors.py` | +7 code classes (§7) |
| `src/jasmine_core/auth.py` | `SCOPES += ("validation:read", "validation:unattributed")`; one `ForbiddenActorKind` guard for `validation:unattributed` on agent keys (the existing pattern at `auth.py:80-92`) |
| `src/jasmine_core/canonical.py` | **additive only**: `REDACT_TEXT_VERSION = "1"` next to `redact_text`, so `mask_version` is a masker revision rather than the package version (`__version__` is `"0.1.0"` and does not track `canonical.py`) |
| `src/jasmine_core/api/handlers.py` | append `tuple(validation_api.routes(Route))` to the `ROUTES` assembly (the P4 precedent) |
| `docs/adr/0015-p7-validation-read-contract.md` (new) | records the 7 new codes, the manifest convention, the discovery rules, the permission model and the `REDACT_TEXT_VERSION` bump rule |
| `docs/api/core-api-v1.md` | documents the five routes |
| `scripts/p7/p7_contract.py` (new) | derives `web/contracts/p7-validation-read.v1.json` with a `source` block (`ref`, `kind`, `derived_by`, `derived_at_ref`, `derived_at_identical`) |
| `tests/test_validation_read.py` (new), `tests/fixtures/p7/` (new) | unit/API tests and labelled fixtures |

**Scope allocation is a shared-file change and is requested here before implementation.** Two scopes must
be added to the fixed `auth.SCOPES` tuple: `validation:read` and `validation:unattributed` (the latter not
issuable to `agent` keys). No migration and no schema change (files only); no new public ID prefix
(artifact ids are bundle-scoped, §3).

**Handoff to P6-03c (not this slice's change).** The dashboard proxy is `web/server/core-paths.json` plus
`web/server/core-proxy.ts`, which already has per-phase opt-in flags (`P6_ENABLE_P3_READS`,
`P6_ENABLE_P4_READS`) and `p3_pending_paths`/`p4_pending_paths` lists. P6-03c therefore adds a
`p7_pending_paths` list and a `P6_ENABLE_P7_READS` flag with these five patterns (the proxy matches the
pathname only, so the query string is not part of the rule):

```
^/v1/validation/runs$
^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z$
^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z/manifest$
^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z/artifacts/art_[a-z0-9][a-z0-9_.-]{0,63}$
^/v1/validation/runs/run-[0-9]{8}T[0-9]{6}Z/artifacts/art_[a-z0-9][a-z0-9_.-]{0,63}/content$
```

This is a deliberate exception to that file's "id segments use Core's own id shape" rule: run ids are the
recorder's and artifact ids are bundle-scoped, and the allowlist comment needs a sentence saying so.
