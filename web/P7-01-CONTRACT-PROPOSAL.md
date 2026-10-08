# P7-01 frozen-contract **proposal** — the minimal Validation read slice

Status: **proposal, awaiting independent review.** Nothing here is implemented yet. This document is
the phase-1 deliverable the dependency taskbook asks for: the contract is proposed, reviewed, and only
then frozen as a machine-checked contract (`web/contracts/p7-validation-read.v1.json`, derived by
`scripts/p7/p7_contract.py`) and implemented.

Baseline: `integration/p6-03-realcore` @ `039f5a3e7800704e5f0478d01992f00eeb2ca3f1`, in its own
worktree `_worktrees/p7-01` on branch `p7-01-validation-read`.

## 0. What this slice is, and what it is not

It **reads existing Validation run records** written by `src/jasmine_core/validation/recorder.py`,
serves the verdict they already carry, shows what each recorded case is missing and, when a frozen
bundle manifest exists, serves declared artifacts safely. It does not run scenarios, does not write,
does not re-sign or upgrade a verdict, and does not implement the P7 stage.

Two operator decisions are already taken and are part of this proposal:

1. **Acceptance criterion #1 (read an existing real FAIL Run) is BLOCKED.** The nine real run records
   on disk (`validation-runs/**/run-*.json`, all `stage: P0`) are PASS(3) / BLOCKED(6); no P7-format
   FAIL run exists. The slice is verified against those real records plus a **clearly-labelled
   synthetic FAIL fixture**, and the real-FAIL-run claim stays BLOCKED with its reason. A fixture is
   never evidence, and no verdict is upgraded.
2. **The run root is explicit configuration only** (`JASMINE_CORE_VALIDATION_RUNS`), with **no
   default**. Unset ⇒ every validation route refuses `503 validation_not_configured`. A request can
   never name a path.

Coordination stated up front: **no migration and no schema change** (files only), one new read scope
`validation:read` plus one `validation:unattributed`, and **no new Core public ID prefix** — run ids
are the recorder's, artifact ids are bundle-scoped (below). The dashboard proxy allowlist for
`/v1/validation/…` is P6-03c's change, requested separately; this slice exposes the Core API and the
contract only.

## 1. Record shapes this slice reads

From `recorder.py` (unchanged by this slice):

* a **run** is one JSON file `<run_id>.json` in a directory chosen by the operator;
  `run_id = run-YYYYmmddTHHMMSSZ`; fields `stage, created_at, commit, schema_version, core_version,
  environment{...}, executor, reviewer, verifier, cases[], notes[], not_implemented[]`, an optional
  `redactions[]` / `redaction_warning`, and a **computed** `verdict` written into the file;
* a **case** has `case_id, name, verdict ∈ {PASS, FAIL, BLOCKED}` and the seven sections
  `user_raw, agent_response, tools, state_before, state_after, interpretation, context`, each either a
  recorded value or the recorder's sentinel `"N/A (P0 not implemented)"`, plus `failure_class`,
  `failure_output`, `reproduction[]`, `evidence[]`, `not_applied_reason`;
* **`evidence[]` is free-form**: the real records hold prose (`"the bundle holds the raw stdout of
  every command above"`) and **absolute host paths**. This slice treats every such string as
  **`unaddressable`** — it is surfaced verbatim as recorded data and **never resolved, opened or
  fetched**. Artifacts are addressable only through a manifest (below).

**The nine real records have no manifest and no project attribution.** They are `mode: unknown`,
`attribution: unattributed`, `manifest_status: absent` — said in the response, not worked around.

## 2. Bundle manifest (proposed convention, frozen by this slice)

A run directory may contain `manifest.json`:

```json
{
  "schema": "jasmine.validation-bundle.v1",
  "run_id": "run-20260929T162925Z",
  "frozen_at": "2026-09-29T16:30:00Z",
  "mode": "real | component | simulated",
  "projects": ["prj_01…"],
  "sources": {"recorder": "scripts/p0-acceptance.py@<sha>", "note": "…"},
  "files": [
    {"artifact_id": "art_stdout", "path": "artifacts/codex-stdout.txt",
     "bytes": 1234, "sha256": "…", "media_type": "text/plain",
     "credential_shaped": false}
  ]
}
```

Rules the reader enforces (a manifest that breaks any of them is **`corrupt`**, and its artifacts are
then unaddressable rather than partially trusted):

* `run_id` matches the directory's run record; `schema` is exactly the string above;
* `files[].artifact_id` is unique and matches `^art_[a-z0-9][a-z0-9_.-]{0,63}$` — a **bundle-scoped**
  id, deliberately not a Core public id, so no ID prefix is allocated;
* `files[].path` is **relative**, contains no `..` segment, no backslash, no leading `/`, no NUL, no
  URL scheme, and each file is a **regular file** (a symlink is refused, not followed);
* `mode`, when present, is one of the three words; **a missing `mode` is `unknown`, never `real`**;
* `projects`, when present, is a list of project ids (possibly empty).

Integrity: for every declared file the reader recomputes `sha256` and reports
`match | mismatch | missing`, plus `bytes` agreement. `mismatch` is never served as content.

## 3. Routes (proposed; all `GET`, all requiring `validation:read`)

| Route | Purpose |
| --- | --- |
| `GET /v1/validation/runs?limit&offset&verdict&stage&mode&attribution` | list |
| `GET /v1/validation/runs/{run_id}` | recorded detail, case by case |
| `GET /v1/validation/runs/{run_id}/manifest` | manifest status, file list, digests, integrity |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}` | artifact metadata (no content) |
| `GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}/content?form=masked\|original&download=1` | text content, authorised and masked |

**List.** `limit` default 20, max 100; `offset` default 0; total order is `(created_at desc, run_id
desc)` on the **run id as recorded**, which is unique — so pages cannot overlap or skip. Response:
`{runs[], count, total, limit, offset, has_more, corrupt_count, ignored_entries}`.
A row carries `run_id, stage, created_at, commit, schema_version, core_version, verdict (as
recorded), case_counts{pass,fail,blocked,total}, mode, mode_basis, attribution{status, projects[]},
manifest_status, record_status, record_sha256, verdict_consistent`.
A record that cannot be parsed **stays in the list** as `record_status: "corrupt"` (no fabricated
fields), is counted in `corrupt_count`, and detail refuses it. Entries in the root that are not
run-shaped are counted in `ignored_entries` — nothing disappears silently, and an empty list is only
ever an empty list of readable records.

**Detail.** Returns the recorded run fields verbatim, and for each case:
`case_id, name, verdict (as recorded), failure_class, failure_output, reproduction[]`, and
`evidence: [{recorded, status: "unaddressable", reason}]`, plus
`sections: {user_raw|agent_response|tools|state_before|state_after|interpretation|context:
{present, value?, missing_reason}}` — `present:false` with the reason `"recorded as N/A (P0 not
implemented)"` or `"the record has no such field"`. The eight chain links the taskbook names (Raw,
Extraction, Resolution, Context, Agent, Tool, Evidence, State) are derived **only** from those
recorded sections; a missing link is named, never filled.
`verdict` is the recorded one. `verdict_consistent` reports whether the recorded run verdict equals
the recorder's own worst-case rule — an inconsistency is surfaced, and the served verdict is still the
recorded one. Nothing here re-signs or upgrades anything.

**Manifest.** `{status: frozen|absent|corrupt, reason?, run_id, frozen_at?, mode?, projects?,
files:[{artifact_id, path (as recorded), bytes, sha256, integrity, media_type, credential_shaped,
masked_sha256?}]}`. `absent` is a 200 with the reason that the record predates the convention and its
history is **not reconstructed**. `corrupt` carries the rule that was broken.

**Artifact metadata.** `{artifact_id, path, bytes, sha256 (declared), integrity, media_type,
forms: ["masked"] or ["masked","original"], original_sha256, masked_sha256, credential_shaped}`.
Digests of the two forms are **separate fields** and never interchangeable.

**Content.** Text only (`media_type` starting `text/` or a declared text type); anything else is
`415 unsupported_media_type` rather than a guessed type. `form=masked` (default) applies the
recorder's redaction semantics to the file's text and reports the digest **of what was served**;
`form=original` is served only when the bytes hash to the declared sha256 **and** the manifest does
not mark them credential-shaped (otherwise `403 original_withheld`, with `masked_sha256` telling the
caller what is available). Response headers: `Content-Type: text/plain; charset=utf-8`,
`X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`,
`Content-Disposition: attachment; filename="<artifact_id>.txt"` when `download=1`,
`X-Validation-Form: masked|original`, `X-Validation-Sha256: <served digest>`. HTML or script content
is therefore never served as a page, and the browser renders it as text.

## 4. Permissions and isolation

* `validation:read` is required on every route; the check runs before any filesystem touch.
* **Actor kind**: any actor carrying the scope. This slice has no write path, so there is no
  actor-kind escalation to guard; stating that is the whole actor rule.
* **Project isolation** — runs are not project objects, so isolation is about the manifest's declared
  projects, and **every** declared project is checked (never just the first):
  * `single_user` mode: `validation:read` suffices; the response still reports attribution.
  * `acl` mode: a run that declares projects `P` is readable only if the key holds a grant for **all**
    of `P`; a run that declares **no** project (the nine real records) additionally requires
    `validation:unattributed`, so reading unattributed evidence is an explicit operator grant rather
    than a side effect. `untrusted` is reported for a run whose manifest declares projects whose
    provenance fields are absent — it is a label, not a substitute for the grant check.
* The configured root is resolved once and must be a real directory (not a symlink). Every file the
  reader opens is opened with `O_NOFOLLOW`, and `st_dev`/`st_ino` are compared with the pre-open
  `lstat` so a swapped file cannot be read under a sealed digest.

## 5. Error semantics (proposed; reuses the ADR 0003 vocabulary)

| Situation | Response |
| --- | --- |
| run root unset | `503 validation_not_configured` |
| malformed `run_id` / `artifact_id` / `limit` / unknown filter value | `400 invalid_request` |
| no or unknown token | `401 unauthenticated` |
| missing `validation:read` | `403 forbidden_scope` (`details.required`) |
| `acl` mode and a declared project is not granted, or unattributed without `validation:unattributed` | `403 forbidden_scope` naming the missing grant |
| unknown run id | `404 not_found` (`validation_run_not_found`) |
| artifact not addressable (no manifest, absent manifest, unknown id, file missing) | `404 artifact_not_found`, reason distinguishing the three |
| run record unparseable | `409 validation_run_corrupt` |
| manifest breaks a rule | `409 validation_manifest_corrupt` |
| file bytes disagree with the declared sha256 | `409 artifact_modified` |
| original form requested for credential-shaped content | `403 original_withheld` |
| non-text artifact content | `415 unsupported_media_type` |

## 6. What the validator will check (acceptance mapping)

| Criterion | How it is met here |
| --- | --- |
| 1. an existing real FAIL Run, classified, raw output traceable | **BLOCKED** — no P7-format FAIL run exists; the FAIL path is exercised on a clearly-labelled synthetic fixture, and the row says BLOCKED with the reason |
| 2. missing Context / Agent transcript shown as missing, no fabrication, no verdict upgrade | `sections.*.present=false` with reasons; recorded verdict served unchanged; `verdict_consistent` reports only |
| 3. authorised success and refusal paired on the same resource | each route tested with a granted key (200) and a scope-less / project-less / unattributed-less key (403) on the same run and artifact |
| 4. artifact normal, missing, modified, escape | fixture manifest with a good file (200 + digest), a missing file (404), a byte-flipped file (409), and manifest paths with `..`, absolute paths, a symlink and a URL — all refused at manifest load with the rule named |
| 5. pagination, no duplicates or omissions, corrupt ≠ empty | total order on a unique key, page-walk test over the real nine records plus a corrupt record; `corrupt_count` and the corrupt row are visible |
| 6. API values match the records and file digests | tests read the same files directly and compare every served field and sha256, including the masked and original digests being distinct |

## 7. Non-goals

No writes, no verdict re-signing, no Scenario Runner, no 30-scenario suite, no release verdict, no
binary artifacts, no arbitrary path or URL access, no HTML/script rendering, no change to the
recorder, no migration, no modification of any frozen P6/P4/P3 evidence, no live entry, no push/PR/CI.
`P6-T06` remains a separate P6 browser drill-down once this read slice is accepted.
