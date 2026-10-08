# P7-01 frozen-contract **proposal** — the minimal Validation read slice

Status: **proposal, revision 2, awaiting delta re-review.** Nothing here is implemented yet. This is the
phase-1 deliverable the dependency taskbook asks for: the contract is proposed, independently reviewed,
revised, re-reviewed, and only then frozen as a machine-checked contract
(`web/contracts/p7-validation-read.v1.json`, derived by `scripts/p7/p7_contract.py`) and implemented.

* rev 1 — commit `3ebe46c`. Independently reviewed (reviewer findings:
  `validation-quarantine/REVIEW-p7-01-proposal/findings.md`, 493 lines, raw commands included).
  Verdict: **APPROVE WITH NAMED MANDATORY CHANGES** (12), factual claims (a)–(e) CONFIRMED.
* rev 2 — this document. Folds all 12 mandatory changes plus three implementation facts found during
  rev-1 recon: the shared server is JSON-only (§5.6), project isolation must reuse the frozen P4
  mechanism and its `not_found` convention (§6), and the exact shared-file change list (§11).

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
* a **case** has `case_id, name, verdict ∈ {PASS, FAIL, BLOCKED}` and the seven sections `user_raw,
  agent_response, tools, state_before, state_after, interpretation, context`, plus `failure_class,
  failure_output, reproduction[], evidence[], not_applied_reason`;
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

### 1.1 Section presence (`present_kind`)

For each of the seven sections the detail response reports
`{present, present_kind ∈ {value, reason_only, sentinel, absent}, value?, missing_reason?,
recorded_reason?}`:

| `present_kind` | Rule |
| --- | --- |
| `absent` | the record has no such key (`missing_reason: "the record has no such field"`) |
| `sentinel` | the value equals `"N/A (P0 not implemented)"` (`missing_reason` quotes the sentinel) |
| `reason_only` | the value is a mapping whose keys are a subset of `{reason, note, detail, not_applied_reason}`, or a string that is only such a reason → **`present: false`**, `recorded_reason` carries the text |
| `value` | anything else → `present: true` |

`context` additionally requires **pack evidence**: `present: true` only when the value is a mapping
holding at least one of `context_id, pack, sections, rendered, content, tokens, parts`, or is a non-empty
list. A reason-only `context` is reported `present: false, present_kind: "reason_only"` — so the 27 real
cases are *not* shown as having a context pack.

The **recorded verdict is always served as recorded**. `verdict_consistent` reports only whether it equals
the recorder's worst-case rule (it does, for all nine); nothing here re-signs or upgrades anything.

## 2. Discovery: root, walk, identity, ordering, pagination

The nine real records sit in **nine distinct leaf directories at depth 2–3 below `validation-runs/`** — no
single directory holds all nine — so discovery is defined completely and is the routing table for
acceptance criteria 5 and 6.

**Root.** `JASMINE_CORE_VALIDATION_RUNS` (absolute or relative; fits the existing `JASMINE_CORE_*`
convention). Unset ⇒ `503 validation_not_configured`. The root is resolved once per process and opened
with `O_DIRECTORY|O_NOFOLLOW`, so a **symlinked root is refused**; missing or not a directory ⇒
`503 validation_not_configured` with the reason. No request input can ever name or extend a path.

**Walk.** Recursive, **at most 4 directory levels below the root** (the nine need ≤3), and:
* directories whose name starts with `.` or is `__pycache__` are not entered;
* a **symlinked directory is never traversed** (counted in `ignored_entries` as `symlinked_directory`);
* only regular files are considered; a **symlinked `run-*.json` is not a record**
  (`ignored_entries: symlinked_file`).

**Run-shaped.** A file is *run-shaped* iff its basename matches `^run-[0-9]{8}T[0-9]{6}Z\.json$`. Its
**identity is the filename stem** (`run_id`), and the JSON must be an object whose `run_id` equals the
stem with `stage` (string), `created_at` (string), `verdict ∈ {PASS,FAIL,BLOCKED}`, and `cases` (list)
present. Anything else run-shaped is **`record_status: "corrupt"`** — still listed, never dropped.

**Corrupt vs ignored (the boundary).** `corrupt` = run-shaped but unparseable, wrong `run_id`, or missing
one of the five required fields, with `corrupt_reason` naming the rule. `ignored` = not run-shaped
(anything else in the tree), counted in `ignored_entries`. Ignored entries are **never** silently
promoted, and a corrupt record is **never** reported as an empty list.

**Duplicates.** Two run-shaped files with the same stem are both listed with
`record_status: "duplicate"`; detail refuses with `409 validation_run_ambiguous` naming the count. This
keeps the ordering key and the `{run_id}` addressing unambiguous.

**Ordering (a strict total order).**

```
(record_status_rank asc: ok=0, corrupt=1, duplicate=1,
 created_at desc  (nulls last, i.e. all rank-1 rows after all rank-0 rows),
 run_id desc,
 relative_path asc)          # relative_path is unique by construction
```

The trailing `relative_path` tie-break means two files sharing a `run_id`/`created_at` can never make a
page overlap or skip a row. Corrupt rows carry `created_at: null` and are ordered within their group by
`relative_path asc`; that is their named sort key.

**Pagination and `total`.** `limit` default 20 / max 100; `offset` ≥ 0. `total` is defined as **the number
of discovered run-shaped records that match the filters, including corrupt and duplicate ones**, and
`count == len(runs)` for the current page; `has_more = offset + count < total`. Filters (`verdict`,
`stage`, `mode`, `attribution`, `project`) never match a corrupt/duplicate row (it has no such field), so
corrupt rows appear only in an unfiltered listing — different filters page over a stable, filter-specific
total order without overlap or omission. An unknown filter value is `400 invalid_request`.

**Diagnostics (so a broken root is never an empty list).** The response carries
`discovery: {root_present, max_depth, scanned_dirs, run_shaped_files, ignored_files, capped}` and, when
`total == 0`, `empty_reason ∈ {no_run_shaped_files, all_filtered_out}` with the counts that produced it.
`ignored_entries` is a count, with at most 20 named samples
(`{relative_path, reason}`) so the reason a record did not appear is inspectable.

**Caps.** `MAX_SCANNED_ENTRIES = 20_000` and `MAX_RUN_SHAPED = 500`; exceeding either returns
`503 validation_index_too_large` naming the cap rather than a silently incomplete index. List row:
`run_id, stage, created_at, commit, schema_version, core_version, verdict (as recorded),
case_counts{pass,fail,blocked,total}, mode, mode_basis, attribution{status, projects[]},
evidence_grade, manifest_status, record_status, corrupt_reason?, record_sha256, verdict_consistent`.

## 3. Bundle manifest (convention frozen by this slice)

A run directory may contain `manifest.json`:

```json
{
  "schema": "jasmine.validation-bundle.v1",
  "run_id": "run-20260929T162925Z",
  "frozen_at": "2026-09-29T16:30:00Z",
  "mode": "real | component | simulated",
  "projects": ["prj_…"],
  "sources": {"recorder": {"path": "scripts/p0-acceptance.py", "sha256": "…"}, "note": "…"},
  "files": [
    {"artifact_id": "art_stdout", "path": "artifacts/codex-stdout.txt",
     "bytes": 1234, "sha256": "…", "media_type": "text/plain", "credential_shaped": false}
  ]
}
```

A manifest that breaks any rule below is **`corrupt`** (with the broken `rule` named); its artifacts are
then unaddressable rather than partially trusted.

**Grammar and identity.**
* `schema` is exactly `jasmine.validation-bundle.v1`; `run_id` equals the run record's id;
* `files[].artifact_id` is unique and matches `^art_[a-z0-9][a-z0-9_.-]{0,63}$` — a **bundle-scoped** id,
  deliberately not a Core public id, so no ID prefix is allocated;
* `files[].path` is **relative** with every component non-empty, and contains no `..`, no backslash, no
  leading `/`, no NUL and no URL scheme; `C:…` drive-relative and `\\…` UNC forms are explicitly refused;
* every declared file must be a **regular file**, and the resolved path must stay inside the run
  directory (`realpath` containment).

**Integrity.** For every declared file the reader recomputes `sha256` and reports
`integrity ∈ {match, mismatch, missing, unreadable}` plus `bytes` agreement; `mismatch` is never served.
Reading a declared file uses the repo's existing **per-component descriptor-relative `O_NOFOLLOW` walk**
(`fingerprint.hash_workspace_file`, which opens every component with `dir_fd`, rejects `.`/`..`/absolute
parts, requires `S_ISREG`, and enforces a byte cap) — mirrored locally so the file is read **once** into a
capped buffer, the digest verified on that buffer, and exactly that buffer served. Final-component
`O_NOFOLLOW` plus an `lstat`/inode comparison is explicitly **not** sufficient: it cannot see a symlinked
ancestor.

**Manifest response.** `{status: frozen|absent|corrupt, reason?, run_id, frozen_at?, declared_mode?,
mode, mode_basis, projects[], sources, files[], manifest_sha256, manifest_authentication:
"unauthenticated"}`. `absent` is a 200 whose reason states that the record predates the convention and
its history is **not reconstructed**; `corrupt` carries `rule`. `sources` is returned verbatim (it is
operator-requested item 2). `manifest_sha256` is the digest of the manifest file's bytes;
`manifest_authentication: "unauthenticated"` states in the API itself that `frozen_at`, `mode` and
`credential_shaped` are self-declared and unsigned — the reader never calls them verified.

**Effective mode (`mode` vs `declared_mode`).** `mode` is derived, not trusted:

| Condition | Effective `mode` / `mode_basis` |
| --- | --- |
| no manifest | `unknown` / `"no bundle manifest on disk"` |
| `mode` missing | `unknown` / `"the manifest declares no mode"` |
| `mode: real` **and** `projects` non-empty **and** `sources.recorder` is `{path, sha256}` or `"<path>@<40-hex>"` | `real` / `"declared real with recorder provenance and project attribution"` |
| `mode: real` without that provenance | `unknown` / `"declared real without recorder provenance or project attribution"` |
| `mode: component\|simulated` | as declared / `"declared by the manifest"` |

**Attribution.** `attribution.status ∈ {attributed, unattributed, unknown}` and `projects[]`, derived only
from the effective manifest `projects`: non-empty ⇒ `attributed`; manifest present with `projects` empty
or absent ⇒ `unattributed`; no manifest / corrupt manifest ⇒ `unattributed` with
`attribution.reason ∈ {manifest_absent, manifest_corrupt}`. Mode filters match on the **effective** mode.

## 4. Chain links and evidence grade

Taskbook §7 requires: *"Validation 缺 Context 或 Agent transcript、仅有 mocked response → 链缺项与证据等级明确"*.
Seven recorded sections map onto the eight chain links; two links are **not recorded at all** and are
therefore always named missing, never filled:

| Chain link | Recorded source | Missing when |
| --- | --- | --- |
| Raw | `case.user_raw` | `present_kind ∈ {sentinel, absent, reason_only}` |
| Extraction | **none** — `recorder.py` records no extraction section | always; `missing_reason: "not recorded by the P0 recorder"` |
| Resolution | `case.interpretation` | `present_kind ∈ {sentinel, absent, reason_only}` |
| Context | `case.context` (§1.1 pack rule) | `present_kind != value` |
| Agent | `case.agent_response` | `present_kind != value` |
| Tool | `case.tools` | `tools == []` (`missing_reason: "the case records no tool call"`) |
| Evidence | `case.evidence` | empty list |
| State | `case.state_before` + `case.state_after` | both `present_kind != value` |

Detail returns `chain: {raw, extraction, resolution, context, agent, tool, evidence, state}` each as
`{present, source, missing_reason?}`. A missing link is named, never fabricated, and never changes the
verdict.

**Evidence grade (per case).** Every case carries `evidence_grade ∈ {real, component, simulated, mocked,
unknown}` plus `grade_basis`:

1. a **recorded per-case marker wins**: a `mock`/`fixture`/`simulated` marker in `agent_response`, a
   `not_applied_reason` naming a fixture, or an `executor` naming fixture/simulated/mock ⇒ `mocked`
   (or `simulated`), `grade_basis: "recorded in the case"`;
2. otherwise the **effective bundle `mode`**, when it qualifies (§3) — `real` or `component` or
   `simulated`, `grade_basis: "the qualifying bundle manifest"`;
3. otherwise **`unknown`** — never `real` by the absence of markers.

Two hard rules make this satisfy the §7 negative: a case marker **can never be upgraded** by a bundle
`mode`, and a manifest declaring `real` while any case carries a mock/fixture marker produces
`errors[]`-style `warnings[]` (`"manifest declares real but case <id> records a fixture"`) and a run-level
`evidence_grade` that is not `real`. The nine real records have no manifest ⇒ every case is `unknown`,
and the response says why.

## 5. Routes (all `GET`, all requiring `validation:read`)

| Route | Purpose |
| --- | --- |
| `GET /v1/validation/runs?limit&offset&verdict&stage&mode&attribution&project` | list (§2) |
| `GET /v1/validation/runs/{run_id}` | recorded detail, case by case, with chain and grade |
| `GET /v1/validation/runs/{run_id}/manifest` | manifest status, sources, file list, digests, integrity |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}` | artifact metadata (no content) |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}/content?form=masked\|original&download=1` | text content, authorised, masked, digest-pinned |

**Detail** returns the recorded run fields verbatim plus, per case:
`case_id, name, verdict (as recorded), failure_class, failure_output, reproduction[], not_applied_reason,
evidence: [{recorded, status: "unaddressable", reason}], sections{…per §1.1}, chain{…per §4},
evidence_grade, grade_basis`.

**Artifact metadata** returns `{artifact_id, path (as recorded), bytes, declared_sha256, declared_bytes,
integrity, media_type, forms: ["masked"] or ["masked","original"], digests: {original, masked},
credential_shaped, content_scan}`. The two digests are **separate named fields** — there is no ambiguous
bare `sha256`, so no client can mistake `masked` for `original`. `content_scan` reports the reader's own
scan of the actual bytes (`{performed: true, credential_shaped: true|false}`); it is independent of the
manifest's declared flag.

**Content.** Text only (`media_type` starting `text/` or a declared text type); anything else is
`415 unsupported_media_type` rather than a guessed type. `form=masked` (default) serves
`canonical.redact_text(text)` — the **substring** masker used by the audit layer — with
`mask_algorithm: "canonical.redact_text"` and `mask_version: <serving jasmine_core.__version__>`, so
`masked_sha256` is reproducible. `sanitise_secrets` is explicitly **not** used: it replaces a string only
when the *whole* string is credential-shaped, so a multi-line log would pass through unmasked.

`form=original` is served only when all three hold: the bytes were read once under the cap and hash to the
declared `sha256`; the text decodes as strict UTF-8; and an **independent credential scan**
(`canonical.looks_like_credential` over the actual text) finds nothing. Any hit ⇒
`403 original_withheld` regardless of the manifest's `credential_shaped` flag, with `details.masked_sha256`
telling the caller what *is* available. `credential_shaped: true` always withholds the original.

**Response envelope (implementation fact).** The shared writer `api/server.py::_respond` always sends
`Content-Type: application/json; charset=utf-8` and **drops any handler-supplied `content-type`**. The
content route therefore returns a **JSON envelope**
`{artifact_id, form, media_type, bytes, sha256, mask_algorithm?, mask_version?, content,
content_is_original}` and sets only non-`content-type` headers, which do pass through:

`X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`,
`X-Validation-Form: masked|original`, `X-Validation-Sha256: <served digest>`,
`X-Validation-Original-Sha256: <declared digest>`,
`Content-Disposition: attachment; filename="<artifact_id>.txt"` when `download=1`.

Consequently HTML or script content can **never** be served as a renderable page — the envelope is JSON
and `nosniff` is set. Serving raw original bytes (a true download) would require a change to the shared
`server.py` and is **out of this slice's scope**; that limitation is stated here rather than worked around.

## 6. Permissions and isolation

**Staged check (the "before any filesystem touch" claim, made true and testable).**

1. `dispatch` authenticates and requires `validation:read` **before the handler runs**, so no table and no
   file is touched for a caller that fails authentication or scope.
2. The handler then resolves the id grammars (§7) and reads the run record (and, when asked, the
   manifest). **No run content, path, digest or status is placed in a response before step 3 passes.**
3. Project grants are then checked, and only then is the response built.

**Grants reuse the frozen P4 mechanism — nothing parallel is invented.** The deployment mode comes from
`memory.scope.deployment_mode()` (`JASMINE_MEMORY_DEPLOYMENT_MODE`, default `single_user`), grants are
rows in `memory_project_actors`, and the check is `memory.scope.ProjectAccess.require(project_id=…,
actor_id=…)` — the same object P4 uses, including `require_mode_ready()` (an `acl` deployment with no
grants at all refuses `503` rather than serving everything).

* **`single_user`**: `validation:read` suffices; the response still reports attribution and grade.
* **`acl`**: every project in the effective `projects` list is checked (**all**, never just the first).
  A project the caller may not use is reported as **`404 project_not_found`** — the frozen P4 convention
  (`p4-memory-read.v1.json` invariant `authorisation_from_actor`, `memory/api.py`), deliberately not 403,
  so the Inspector cannot become a project-enumeration oracle. rev 1 proposed 403 here; that is corrected.
* **Unattributed runs (including all nine real records)** additionally require
  `validation:unattributed` in `acl` mode, so reading evidence with no project fence is an explicit
  operator grant rather than a side effect. Without it the run is reported as
  **`404 validation_run_not_found`** — the run's very existence is the protected information.
* **Corrupt / duplicate records fail closed**: in `acl` mode they have no readable manifest, so they are
  treated as unattributed and require `validation:unattributed` before the `409` is returned. Otherwise an
  unauthorised caller could probe the store by id.
* **Actor kind.** Any actor may carry `validation:read` (read-only, no write path, no escalation). The
  `validation:unattributed` scope is **not issued to `agent` keys** — `auth.issue_key` gains the same
  `ForbiddenActorKind` guard already used there for `interpretations:manage`, `authority:manage`,
  `state:accept`, `memory:manage` and `memory:reflect`. Rationale: unattributed evidence has no project
  fence at all, so the grant should be made only by a human/system actor. (This is a fail-closed default I
  propose; the operator can overrule it explicitly and the contract will record the decision.)
* Root resolution itself is also a form of isolation: the root is operator configuration, resolved once
  per process, and no request input can name, extend or escape it.

## 7. Errors and precedence (ADR 0003 shape; five new code strings)

The existing vocabulary is reused where it exists — `invalid_request` 400, `unauthenticated` 401,
`forbidden_scope` 403, `unsupported_media_type` 415, `payload_too_large` 413, `schema_version_unsupported`
/ `database_busy` 503 — and `NotFound(kind, id)` is used for the three not-found kinds
(`validation_run_not_found`, `artifact_not_found`, `project_not_found`). `NotFound` never emits a bare
`not_found` (rev 1's table was imprecise about that).

**Precedence** (the same order is used for the paired-authorisation acceptance test):

| # | Situation | Response |
| --- | --- | --- |
| 1 | missing/unknown token | `401 unauthenticated` |
| 2 | missing `validation:read` | `403 forbidden_scope` (`details.required`) |
| 3 | root unset / missing / not a directory / symlinked | `503 validation_not_configured` |
| 4 | walk caps exceeded | `503 validation_index_too_large` |
| 5 | malformed `run_id` / `artifact_id` / `limit` / `offset` / unknown filter or `form` value | `400 invalid_request` |
| 6 | `acl` mode: the run is unattributed, or corrupt/duplicate (fail-closed), without `validation:unattributed` | `404 validation_run_not_found` |
| 7 | unknown run id | `404 validation_run_not_found` |
| 8 | run record unparseable / wrong `run_id` / missing required field / duplicate stem | `409 validation_run_corrupt` / `409 validation_run_ambiguous` |
| 9 | `acl` mode: a declared project is not granted | `404 project_not_found` |
| 10 | manifest breaks a rule | `409 validation_manifest_corrupt` |
| 11 | artifact not addressable (no/absent/corrupt manifest, unknown id, missing file) | `404 artifact_not_found`, `reason` distinguishing the cases |
| 12 | file bytes disagree with the declared `sha256`/`bytes`, or exceed the cap | `409 artifact_modified` |
| 13 | original form requested for credential-shaped or scan-positive content | `403 original_withheld` |
| 14 | non-text artifact content | `415 unsupported_media_type` |

**Id grammars are validated before any path construction** (frozen here):
`^run-[0-9]{8}T[0-9]{6}Z$` and `^art_[a-z0-9][a-z0-9_.-]{0,63}$` — anchored matches against the raw route
capture, which `request.split_target` does **not** percent-decode, so `..%2f` simply fails the grammar. A
path is only ever built from these two validated ids plus a manifest `path` that already passed §3.

**New code strings** are recorded in a new ADR, per the established convention that each domain documents
its own codes (ADR 0006/0007 for `missing_evidence`/`fingerprint_unavailable`, ADR 0008 for
`interpretation_*`, ADR 0004/0009 for `actor_mismatch`, ADR 0014 for the P4 codes):
`docs/adr/0015-p7-validation-read-contract.md` records `validation_not_configured` (503),
`validation_index_too_large` (503), `validation_run_corrupt` (409), `validation_run_ambiguous` (409),
`validation_manifest_corrupt` (409), `artifact_modified` (409), `original_withheld` (403), each with its
trigger and the acceptance negative that exercises it. No existing code is redefined.

## 8. Resource limits

Root walk: depth 4, `MAX_SCANNED_ENTRIES = 20_000`, `MAX_RUN_SHAPED = 500`. Manifest: ≤ 1 MiB,
`files[]` ≤ 128 entries. Declared and served artifact text: ≤ `MAX_ARTIFACT_BYTES = 8 MiB`; masked output
is capped at the same size. List: `limit` ≤ 100. A body larger than a declared `bytes` value, or than the
cap, is `409 artifact_modified`; exceeding a walk cap is `503 validation_index_too_large`; a manifest
breaking the size rules is `409 validation_manifest_corrupt` with the rule named. No route can be made to
read an unbounded amount of data.

## 9. Acceptance mapping, and the unblock action for criterion #1

| Criterion | How it is met |
| --- | --- |
| 1. an existing real FAIL Run, classified, raw output traceable | **BLOCKED** — no P7-format FAIL run exists (0 FAIL cases, 0 `failure_class`, 0 `failure_output` on disk). The FAIL *code path* is exercised on a synthetic fixture; the validator records BLOCKED with the reason, and the row says BLOCKED. **Unblock action: the P7 owner records one genuinely failing scenario with `recorder.py` into the configured run root** (`validation-runs/p7-<scenario>/`), after which criterion 1 is re-validated against that real record. Until then `P6-T06`/P6-03c cannot close. |
| 2. missing Context / Agent shown as missing, no fabrication, no verdict upgrade | §1.1 `present_kind` (reason-only contexts are *not* present); §4 chain links name Extraction and Resolution as never-recorded; recorded verdict served unchanged; `verdict_consistent` reports only |
| 3. authorised success and refusal paired on the same resource | §6/§7: each route with a granted key (200) and with a scope-less / non-granted-project / unattributed-less key (403 or 404) on the same run and artifact, in both `single_user` and `acl` |
| 4. artifact normal, missing, modified, escape | fixture manifest with a good file (200 + digest), a missing file (404), a byte-flipped file (409), and manifest paths with `..`, an absolute path, a `C:` form, a UNC form, a URL, a symlinked file and a **symlinked intermediate directory** — all refused with the rule named (§3) |
| 5. pagination, no duplicates or omissions, corrupt ≠ empty | §2 total order with a unique path tie-break, `total` includes corrupt rows, page-walk over the nine real records plus a corrupt row, a duplicate stem and a misconfigured root; `corrupt_count`, `record_status` and `discovery`/`empty_reason` visible |
| 6. API values match the records and file digests | tests read the same files directly and compare every served field, `record_sha256`, `manifest_sha256`, `declared_sha256`, `masked_sha256` and the original scan result, including masked ≠ original being reported as distinct fields |

**Fixtures are never evidence.** The synthetic FAIL fixture lives under `tests/fixtures/p7/` and in
validator-created temporary roots, **never** in the operator's configured run root; every fixture record
and every document that cites it labels it `synthetic: true`, and no fixture result is counted toward
criterion 1.

## 10. Non-goals

No writes, no verdict re-signing, no Scenario Runner, no 30-scenario suite, no release verdict, no binary
artifacts, no raw-byte download route (would need a shared `server.py` change), no arbitrary path or URL
access, no HTML/script rendering, no change to the recorder, no migration, no new Core public ID prefix,
no modification of any frozen P6/P4/P3 evidence or of the operator's services/global config/V0, no
live entry, no push/PR/CI. `P6-T06` remains a separate P6 browser drill-down once this read slice is
accepted.

## 11. Shared-file changes, coordination and scope allocation

| File | Change |
| --- | --- |
| `src/jasmine_core/validation/reads.py` (new) | reader + `routes(route_cls)` factory, mirroring `memory/api.py::routes` |
| `src/jasmine_core/errors.py` | +7 code classes (§7) |
| `src/jasmine_core/auth.py` | `SCOPES += ("validation:read", "validation:unattributed")`; one `ForbiddenActorKind` guard for `validation:unattributed` on agent keys |
| `src/jasmine_core/api/handlers.py` | append `tuple(validation_api.routes(Route))` to the `ROUTES` assembly (the P4 precedent) |
| `docs/adr/0015-p7-validation-read-contract.md` (new) | records the 7 new codes, the manifest convention, the discovery rules and the permission model |
| `docs/api/core-api-v1.md` | documents the five routes |
| `scripts/p7/p7_contract.py` (new) | derives `web/contracts/p7-validation-read.v1.json` with a `source` block (`ref`, `kind`, `derived_by`, `derived_at_ref`, `derived_at_identical`) |
| `tests/test_validation_read.py` (new), `tests/fixtures/p7/` (new) | unit/API tests and labelled fixtures |

**Scope allocation is a shared-file change and is requested here before implementation.** Two scopes must
be added to the fixed `auth.SCOPES` tuple: `validation:read` and `validation:unattributed`. No migration
and no schema change (files only); no new public ID prefix (artifact ids are bundle-scoped, §3). The
dashboard proxy allowlist for `/v1/validation/…` is P6-03c's change, requested separately; this slice
exposes the Core API and the contract only.
