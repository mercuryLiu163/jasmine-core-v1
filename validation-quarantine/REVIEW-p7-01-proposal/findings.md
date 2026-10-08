# Independent review — `web/P7-01-CONTRACT-PROPOSAL.md`

Reviewer: independent (delegated). Date of review run: see shell history (2026-10-08 host date).
Artifacts reviewed:

* proposal: `_worktrees/p7-01/web/P7-01-CONTRACT-PROPOSAL.md`
* worktree under review: `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`, branch `p7-01-validation-read`, HEAD `3ebe46c`, parent `039f5a3e7800704e5f0478d01992f00eeb2ca3f1` (`integration/p6-03-realcore`)
* source repo (shared object store, gitignored records): `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1`
* authority: `docs/Jasmine-Core-V1-P6-阶段任务书.md` §6, §7 (T06), §8; operator's P7-01 dependency taskbook
* recorder: `src/jasmine_core/validation/recorder.py`; tests `tests/test_validation.py`
* real records: `validation-runs/**/run-*.json` (gitignored)

Baseline integrity check (`git diff --stat 039f5a3e… 3ebe46c`): **only `web/P7-01-CONTRACT-PROPOSAL.md` was added**
(192 insertions); no code, schema, recorder, contract or test file is touched. The proposal therefore *describes*
a contract that does not exist yet, as stated. `web/contracts/p7-validation-read.v1.json`,
`scripts/p7/p7_contract.py` and the scopes `validation:read` / `validation:unattributed` do **not** exist
(`ls scripts/p7` → `No such file or directory`; `grep -c validation:read src/jasmine_core/auth.py` → `0`).

---

## 1. Factual claims — verified

All raw commands were run from the source repo unless noted. Outputs are verbatim.

### 1.1 Command battery

```
$ find validation-runs -name 'run-*.json' | wc -l
9

$ python3  # stage / verdict / top-level keys / nested project keys
stages: {'P0': 9}
run verdicts: {'PASS': 3, 'BLOCKED': 6}
case verdicts: {'PASS': 57, 'BLOCKED': 6}
top-level union keys: ['cases', 'commit', 'core_version', 'created_at', 'environment',
 'executor', 'not_implemented', 'notes', 'redactions', 'reviewer', 'run_id',
 'schema_version', 'stage', 'verdict', 'verifier']
'project' at top level in any file: False

$ find validation-runs -name 'manifest.json' | wc -l
0

$ python3  # evidence entries
evidence entries: 21 absolute-path entries: 12

$ grep -n 'NOT_IMPLEMENTED = \|^SECTIONS\|def verdict' src/jasmine_core/validation/recorder.py
29:SECTIONS = (
49:NOT_IMPLEMENTED = "N/A (P0 not implemented)"
106:    def verdict(self) -> str:

$ sed -n '105,113p' src/jasmine_core/validation/recorder.py
    @property
    def verdict(self) -> str:
        """Worst case wins, and an unrun case is never quietly a pass."""
        if any(case.verdict == "FAIL" for case in self.cases):
            return "FAIL"
        if any(case.verdict == "BLOCKED" for case in self.cases):
            return "BLOCKED"
        return "PASS"

$ grep -oE 'code = "[a-z_]+"' src/jasmine_core/errors.py | sort -u
actor_mismatch, database_busy, event_id_conflict, fingerprint_unavailable,
forbidden_actor_kind, forbidden_scope, internal_error,
interpretation_idempotency_conflict, invalid_interpretation_source,
invalid_request, invalid_state_transition, method_not_allowed, migration_conflict,
missing_evidence, missing_expected_revision, not_found, payload_too_large,
revision_conflict, rule_key_conflict, schema_version_unsupported,
source_event_duplicate, unauthenticated, unexpected_expected_revision,
unsupported_media_type

$ # collision probe for the seven proposed new codes (excluding the proposal itself)
$ for c in validation_not_configured validation_run_not_found validation_run_corrupt \
      validation_manifest_corrupt artifact_modified original_withheld artifact_not_found; do
      grep -rl "$c" --include=*.py --include=*.md --include=*.json . | grep -v P7-01-CONTRACT-PROPOSAL; done
(no output — no collisions)
```

### 1.2 (a) Nine records, `stage: P0`, PASS(3)/BLOCKED(6), no FAIL — **CONFIRMED**

`9` records; every one `stage: P0`; run verdicts `PASS×3 / BLOCKED×6`; **zero** run-level FAIL.
Stronger: there is **no FAIL case either** — case verdicts are `PASS×57 / BLOCKED×6` and
`0` cases carry `failure_class` or `failure_output`. So the FAIL path (recorder `Case.__post_init__`
requires `failure_class` + `failure_output` for a FAIL) has **no real data at all**, not merely no FAIL run.
A repo-wide search found no other run record and no `validation-runs/p6/` evidence tree
(`find . -name 'run-*.json'` outside `node_modules` returns only the nine; `ls validation-runs/p6` → not found),
and `grep -rl '"verdict": "FAIL"' validation-runs/` → empty.

Recorded verdicts were independently recomputed from `cases[]` with the recorder's worst-case rule:
`recorded verdict == worst-case for all 9: True`. The `verdict_consistent` idea is therefore well-grounded.

### 1.3 (b) No bundle manifest and no project attribution — **CONFIRMED, with one nuance**

* `find validation-runs -name 'manifest.json'` → `0`.
* No run record has a top-level `project` field; the union of top-level keys contains none.
* **Nuance:** the records *do* contain project ids nested inside case data, e.g.
  `.cases[1].state_before.projects`, `.cases[1].state_after.projects`,
  `.cases[1].tools[*].response.event.payload.project_id`, `.cases[1].tools[*].response.object.project_id`.
  So "no project attribution" is true at the record/run level, but the data needed to *derive* a project
  association is present inside the cases. The proposal should say "no **run-level** attribution field"
  rather than "no project attribution", because the nested ids are exactly the sort of data a future
  attribution derivation could (mis)use. This matters for §2.4 below.

### 1.4 (c) `evidence[]` is free-form and contains absolute host paths — **CONFIRMED**

21 evidence entries across the nine records, 12 of them absolute host paths, e.g.:

```
run-20260929T162925Z.json P0-T07 | ABS | '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/artifacts/p0-t07-capture-20260929T144305Z/real-main-46cf99f-01/codex-stdout.jsonl'
run-20260929T162925Z.json P0-T01 |     | 'the bundle holds the raw stdout of every command above'
```

So prose + absolute paths is correct, and treating them as `unaddressable` (never resolved) is the right call.

### 1.5 (d) Recorder field names, sections, sentinel, worst-case rule — **CONFIRMED (one wording imprecision)**

* `Case` fields: `case_id, name, verdict, user_raw, agent_response, tools, state_before, state_after,
  interpretation, context, failure_class, failure_output, reproduction, evidence, not_applied_reason` —
  matches §1.
* `Run` fields: `run_id, stage, created_at, commit, schema_version, core_version, environment, executor,
  reviewer, verifier, cases, notes, not_implemented` — matches §1; `verdict` is a computed property written
  into the file by `to_dict()`; `redactions` / `redaction_warning` are optional — matches.
* Seven sections = `SECTIONS` exactly; sentinel = `"N/A (P0 not implemented)"` exactly.
* Worst-case rule = FAIL > BLOCKED > PASS exactly.
* **Wording imprecision:** §1 says each of the seven sections is "either a recorded value or the
  recorder's sentinel". `tools` defaults to `[]`, not the sentinel, and real records hold
  `tools: [...]` (and `state_before/after` often hold real dicts). In the real data the sentinel appears in
  `user_raw` (24), `agent_response` (60), `interpretation` (63), `state_before` (6), `state_after` (6),
  `context` (6) across 63 cases. This does not falsify the claim but the contract must not assume
  "sentinel ⇔ absent" for `tools`.
* **Second, more consequential imprecision:** 57 of 63 `context` values are non-sentinel, and **27 of those
  57 are `{"reason": "…"}`-only objects with no context content** (e.g. `{"reason": "no Core API involvement
  in a migration-only case"}`). A presence test of "field exists and is not the sentinel" will report
  Context **present** for 27 cases that contain no context pack. See §2.2/§3.

### 1.6 (e) Cited error codes/statuses exist; new codes collide with nothing — **CONFIRMED**, with a governance caveat

All cited existing vocabulary exists in `src/jasmine_core/errors.py`:

| Proposal row | Existing class | Status |
| --- | --- | --- |
| `400 invalid_request` | `InvalidRequest` | ✅ |
| `401 unauthenticated` | `Unauthenticated` | ✅ |
| `403 forbidden_scope` | `ForbiddenScope` (`Principal.require` adds `required`/`granted`) | ✅ |
| `404 not_found` | `NotFound` (code becomes `f"{kind}_not_found"`) | ✅ |
| `415 unsupported_media_type` | `UnsupportedMediaType` | ✅ |
| 503 family | `SchemaVersionUnsupported`, `MigrationConflict`, `DatabaseBusy` | ✅ |

None of the seven proposed codes collides with an existing code string (probe output empty):

* `validation_run_not_found` and `artifact_not_found` are **not new classes** — they are exactly what
  `NotFound(kind, ident)` already produces (`self.code = f"{kind}_not_found"`). They fit the frozen pattern.
* `validation_not_configured` (503), `validation_run_corrupt` (409), `validation_manifest_corrupt` (409),
  `artifact_modified` (409), `original_withheld` (403) are **genuinely new code strings**.

**Governance caveat (not a collision):** `errors.py`'s own docstring says the codes "are frozen in ADR 0003
§1.4". ADR 0003 §1.4 does **not** contain these five. Later ADRs do extend the table
(`missing_evidence`/`fingerprint_unavailable` are documented in ADR 0006/0007; `interpretation_*` in ADR 0008;
`actor_mismatch` in ADR 0004/0009), so the established convention is that a new domain records its new codes
in that domain's ADR. The proposal's claim that it "reuses the ADR 0003 vocabulary" is only true of the
*shape*; it extends the *code table* and cites no ADR. Also note the §5 row "`404 not_found
(validation_run_not_found)`" is imprecise: `NotFound` never emits bare `not_found`, only
`validation_run_not_found`.

### 1.7 Could not verify (honest limits)

* **Provenance of the nine records as "real".** They are labelled real by path and commit, but I cannot
  prove from the files that they were produced by a genuine conversation rather than a script; that is the
  operator's claim, not something the records attest. (The proposal itself only relies on them being the
  existing records.)
* **Acceptance criteria 1–6** cannot be verified: nothing is implemented. This review checks the proposal
  text, not runtime behaviour.
* Whether a manifest will ever set `mode: "real"` with real provenance — the proposal does not require it,
  so it is unverifiable by construction (see §3).

---

## 2. Completeness against the taskbook

### 2.1 The five required proposal items

| # | Required | Covered? | Holes / contradictions |
| --- | --- | --- | --- |
| 1 | run list/detail: identity, pagination, filtering, candidate commit, environment, evidence mode, existing verdict, failure classification, missing items | **Partial** | Discovery is undefined (see below). `environment` only appears under "recorded run fields verbatim" (detail), not in the list row — acceptable but must be stated. `failure_class`/`failure_output` are returned; **`not_applied_reason` (the BLOCKED case's whole reason) is listed in §1 but omitted from the §3 "Detail" enumeration.** `mode_basis` and the `attribution` enum are used but never defined. |
| 2 | bundle manifest: file list, digests, sources, integrity status | **Partial** | The manifest **response shape omits `sources`** (`{status, reason?, run_id, frozen_at?, mode?, projects?, files[]}`) even though the convention's example defines `sources`. File list, digests, integrity are present. |
| 3 | safe artifact read by run + artifact id, never arbitrary path/URL | **Mostly** | Addressed by the bundle-scoped `art_` id + manifest-only paths. Gaps are the symlink/TOCTOU and grammar issues in §3. |
| 4 | permissions: scopes, actor limits, project isolation, unattributed handling | **Partial** | `acl` mode is asserted but undefined for validation (no config source, no grant model); project isolation contradicts the frozen P4 convention; the "check before any filesystem touch" claim is impossible for manifest-declared projects. See §2.4. |
| 5 | error semantics: not found, forbidden, corrupt, missing/modified artifact | **Mostly** | Covered, with the ADR/governance caveat and precedence gaps in §3. |

### 2.2 Implementation requirements

| Requirement | Covered? | Holes |
| --- | --- | --- |
| Preserve original verdict, never re-sign/upgrade to PASS | **Yes** | `verdict` as recorded; `verdict_consistent` reports only. Real FAIL run absent and honestly marked BLOCKED. |
| Missing Raw/Extraction/Resolution/Context/Agent/Tool/Evidence/State → missing reason per item | **Partial** | The eight chain links are mapped onto **seven** sections with no stated mapping for **Extraction** and **Resolution** (there is no such recorded section). `evidence` is a section too. Also, a non-sentinel `{"reason": …}` context counts as *present* (27 cases), so "missing Context shown as missing" is not guaranteed by the stated rule. |
| Distinguish real/component/simulated/unknown; never read a missing field as real | **Partial — major gap** | `mode` is a single **manifest-level** field. There is no per-case/per-section **evidence grade**, so a manifest can say `mode: real` while a case's `agent_response` is a mocked/fixture response — exactly the §7 negative "Validation 缺 Context 或 Agent transcript、仅有 mocked response → 链缺项与证据等级明确". The proposal grades *missing* fields but has no way to grade *present-but-mocked* content. |
| Multi-project run authorised against every project | **Stated, not checkable** | The rule is asserted, but (i) no run carries a manifest on disk, (ii) the grant mechanism is not defined in Core, (iii) the records' only project ids are nested inside cases and are not used. See §2.4. |
| Never generate placeholder content for a missing artifact | **Yes** | 404, no placeholder. |
| Refuse traversal, absolute paths, encoding bypasses, symlink escapes, external URLs | **Partial** | Manifest-path filtering is described, but intermediate-directory symlinks are not covered, and "encoding bypasses" is not operationalised. See §3. |
| Browser display/download authorised and masked; masked and original recorded under separate digests, never mixed | **Partial** | Separate fields exist, but there is no masking-version/algorithm identifier (so `masked_sha256` is not reproducible/auditable), `sha256` vs `original_sha256` are ambiguous duplicates, and original-serving trusts a self-declared `credential_shaped` boolean. See §3. |
| Untrusted HTML/script never served as an executable page | **Yes** | Forced `text/plain`, `nosniff`, `CSP: default-src 'none'; sandbox`, no inline rendering. |
| Old record without frozen manifest: say so, do not fabricate sealed history | **Yes** | `manifest_status: absent` with the reason; history not reconstructed. |

### 2.3 Pagination / discovery / `total` (the sharpest correctness hole)

The proposal says "a run is one JSON file `<run_id>.json` in a directory chosen by the operator" and orders
pages by `(created_at desc, run_id desc)` on a "unique" run id. But the real records are **not** in one
directory — they are at depth 3–4 in nine different leaf directories under four trees:

```
validation-runs/p0-resume-20260929T143554Z/main-46cf99f-real-acceptance/run-20260929T162925Z.json
validation-runs/p0-resume-20260929T143554Z/p003-59ee8c4-real-acceptance/run-20260929T162307Z.json
... (7 more, all in distinct leaf dirs)
validation-runs/p1-implementation/p1-02-67bdbfc/p0-regression/run-20260929T173423Z.json
```

There is **no single directory that contains all nine**. The proposal never says whether the configured root
is walked **recursively**, or whether only `<root>/<run_id>.json` is read, or whether each leaf is a separate
run root. This is not a detail: it decides whether the list can show the real records at all, and it decides
the uniqueness claim. Concrete consequences:

* **Recursive walk:** run ids are only unique *if* the filename grammar is enforced; two files with the same
  `<run_id>.json` in different trees would collide on the "unique" ordering key, producing duplicate/skipped
  pages. A stable tie-break on a globally unique path (or a derived id) is required.
* **Single directory:** a root pointed at `validation-runs/` finds **zero** run-shaped entries, and all nine
  land in `ignored_entries` — which the proposal would report as "an empty list of readable records", the
  exact failure criterion 5 forbids ("a corrupt record not disguised as an empty list" has a sibling: a
  *misconfigured root* disguised as an empty list). The proposal needs a distinct "root has N subdirectories
  but no run-shaped files" diagnostic.
* **`total` vs `corrupt_count`:** `{count, total, limit, offset, has_more, corrupt_count, ignored_entries}`
  is listed without defining whether `total` includes corrupt rows. If a corrupt row appears in `runs[]` but
  is excluded from `total`, then `offset/limit/has_more` arithmetic no longer matches the served array and a
  page walk duplicates or omits rows. **`total` must be explicitly defined as the number of discovered
  run-shaped records including corrupt ones, with `count = len(runs)` for the current page.**
* **Ordering a corrupt record:** a record that cannot be parsed has no reliable `created_at`. The order key
  is undefined for it; if it is sorted by a fallback (filename, mtime), the key is no longer `created_at desc`
  and the "total order on a unique key" guarantee is lost. The proposal must name the corrupt-row sort key.
* **Corrupt vs ignored boundary:** no rule distinguishes "JSON file whose name is `<run_id>.json` but is
  unparseable/missing fields" (= corrupt, stays in list) from "not run-shaped" (= ignored, counted and
  dropped). Without it, `total`, `corrupt_count` and criterion 5 are not testable.

### 2.4 Permissions / isolation (contradiction with a frozen convention)

* **`403` for an unauthorised project contradicts the established Core convention.** The frozen
  `web/contracts/p4-memory-read.v1.json` invariant `authorisation_from_actor` states: "an unauthorised
  project is reported as `not_found` so the inspector cannot enumerate another project's work", and
  `src/jasmine_core/memory/api.py` repeats it ("use is reported as `not_found`, so the Inspector and the job
  list cannot be used to enumerate another project's work."). The proposal's §5 row says
  "`acl` mode and a declared project is not granted … → `403 forbidden_scope` naming the missing grant."
  That gives an unauthorised caller a project-existence oracle and breaks consistency with the P4 read
  contract the same Dashboard consumes. It must be reconciled (either follow `not_found`, or amend the
  convention deliberately and say so).
* **"`validation:read` … the check runs before any filesystem touch" is not achievable as written.** The
  per-project checks depend on `manifest.projects`, which can only be known by reading the manifest from
  disk. The scope check can precede disk I/O; the project-grant check cannot. The proposal should specify a
  two-stage check (auth+scope → read+validate manifest → grant check over every project → serve) and state
  that no record content is returned before grants pass, while existence is masked consistently (see above).
* **`acl` mode is undefined for validation.** `single_user`/`acl` is a *Memory* deployment mode
  (`JASMINE_MEMORY_DEPLOYMENT_MODE`; `src/jasmine_core/context/memory_provider.py`). The proposal reuses the
  words without naming the config source, the principal's project grants, or how a key comes to "hold a
  grant for all of P". `errors.py`/`auth.py` have no per-project grant model (scopes are global strings).
  "a key holds a grant for all of P" is therefore not checkable from the data that exists.
* **Unattributed rule coherence.** The proposal says unattributed real records "additionally require
  `validation:unattributed`" in `acl` mode, and reports `untrusted` for "a manifest whose projects … provenance
  fields are absent". But (i) the nine records have **no manifest**, so "declares no project" and "manifest
  absent" are conflated; (ii) there is no definition of a project's "provenance fields" (`sources` is a
  free-form string map in the example, unrelated to projects); (iii) in `single_user` mode the
  `validation:unattributed` scope is silently unnecessary. The rule needs to be restated over a single
  attribution enum with explicit precedence.
* **`validation:unattributed` grantability.** New scopes must be added to `auth.SCOPES` (currently absent),
  and `auth.issue_key` rejects unknown scopes. The proposal should say the scope is added there and whether
  any actor-kind restriction applies (it says none, which is fine — but note `validation:unattributed` is a
  sensitive capability that lets any human/agent read un-attributed evidence, so "any actor carrying the
  scope" deserves an explicit operator decision).

### 2.5 Manifest / mode / digests

* **`mode: real` can be claimed with no provenance.** §2 only requires `mode` to be one of the three words
  when present; a missing mode is `unknown`. Nothing requires `sources` or `projects` for `real`, and
  `frozen_at` is an unauthenticated self-declared timestamp. A manifest that claims `real` with no recorder
  provenance is accepted. Tightening: `mode: real` must additionally require a non-empty `sources.recorder`
  pointing at a hashed artifact/commit, and non-empty `projects`; anything less is `component`/`unknown`.
* **`sources` is not returned** by the manifest response, so the operator's item (2) "sources" is not
  actually exposed.
* **Masked/original separation is not fully enforceable as described.** The response has separate
  `original_sha256` and `masked_sha256`, but: no masking-version/algorithm id means `masked_sha256` is not
  reproducible across code versions; `sha256 (declared)` and `original_sha256` are ambiguous duplicates; the
  masked digest is computed per request rather than "recorded", so "recorded under separate digests" only
  covers the original; and nothing structurally prevents a client from treating `masked_sha256` as the
  original digest. Tightening: add `mask_algorithm`/`mask_version`, define `sha256 == original_sha256` (or
  drop one), and have the artifact metadata expose which digest is which explicitly (`digests: {original,
  masked}`).
* **"the recorder's redaction semantics" is ambiguous and probably wrong for file text.** The recorder's
  guard is `sanitise_secrets`, which only replaces a string if the **whole string** matches a credential
  pattern (`canonical.looks_like_credential`), and only substitutes dict *fields* by name. Applied to a
  multi-line log artifact it will usually return the content unchanged. The substring masker is
  `canonical.redact_text` (docstring: "destined for logs"). The proposal must name exactly which function,
  and require a content scan rather than a whole-string test, or the "masked" form will equal the original
  for nearly every real artifact.

---

## 3. Design risks, weakest first, each with a concrete tightening

1. **Original content is released on a self-declared flag (`credential_shaped`).**
   A manifest author sets `credential_shaped: false`; the reader then serves the raw bytes (`form=original`)
   even if the file is a leaked token dump. There is no independent content scan and the manifest is unsigned.
   *Tightening:* before serving `original`, run the credential scan (`canonical.redact_text` /
   `looks_like_credential` patterns, plus PEM/JWT/`sk-`/`ghp_` shapes) over the actual bytes; if anything
   matches, return `403 original_withheld` and record the finding, independent of the declared flag. Add a
   manifest `scan` result and require the declared flag and the scan to agree.

2. **Intermediate-directory symlink escape is not covered.**
   §4 says "every file … is opened with `O_NOFOLLOW`, and `st_dev`/`st_ino` are compared with the pre-open
   `lstat`". `O_NOFOLLOW` protects only the **final** component; `a/b/file` where `a` or `b` is a symlink
   pointing outside the root is still followed, and the final `lstat`/inode check cannot see it.
   This is a working traversal primitive even though the manifest path is `..`-free and relative.
   The repo already contains the correct primitive:
   `fingerprint.hash_workspace_file(root, relative, remaining_bytes)` opens **each component with
   `O_NOFOLLOW` via `dir_fd`**, rejects `.`/`..`/empty/absolute parts, checks `S_ISREG`, and enforces a byte
   cap (`src/jasmine_core/fingerprint.py`); `adapter/config.py` has the same descriptor-relative walk.
   *Tightening:* mandate reuse (or exact mirroring) of that descriptor-relative per-component open, and
   `realpath` containment of the resolved root as a second check; state that a symlinked **root** and a
   symlinked **ancestor** are both refused.

3. **TOCTOU between digest verification and serving.**
   If the reader hashes the file in one open and re-opens it to stream content, the served bytes are not tied
   to the verified digest (an inode check per open narrows but does not close this).
   *Tightening:* read the file once under the byte cap, verify the digest of the buffer, then serve exactly
   that buffer (or hash-while-streaming the same descriptor). Name a maximum artifact size and a maximum
   `files[]` count so a hostile manifest cannot exhaust memory/IO.

4. **Run id grammar / path construction unspecified.**
   §5 says "malformed `run_id` → 400" but never gives the grammar. The route pattern is `([^/]+)`, and
   `request.split_target` does **not** percent-decode, so `..%2f` stays literal and would be rejected only
   if a strict regex is applied before any path join. If the implementation instead maps `run_id` to
   `<root>/<run_id>/…` or `<root>/<run_id>.json` without an exact `^run-[0-9]{8}T[0-9]{6}Z$` check, an id
   like `..` becomes a traversal.
   *Tightening:* freeze `^run-[0-9]{8}T[0-9]{6}Z$` and `^art_[a-z0-9][a-z0-9_.-]{0,63}$` in the contract;
   require validation before any join, and forbid constructing a path from any raw input other than these
   two validated ids plus a manifest path that already passed the relative/no-`..`/regular-file rules.
   Also forbid Windows drive-relative (`C:foo`) forms and UNC prefixes explicitly.

5. **Discovery/pagination/`total` undefined (see §2.3).** This is a correctness hole that would fail
   acceptance criteria 5 and 6. *Tightening:* define discovery (recursive walk with an explicit depth and a
   documented "run root" that actually contains the nine records), define `total` to include corrupt rows,
   define the corrupt sort key, define the corrupt-vs-ignored boundary, and page over a key that includes a
   path or id that is unique even if two files share a run id.

6. **No evidence grade for present-but-mocked content (see §2.2).** *Tightening:* add a per-case (and
   ideally per-section) `provenance`/`evidence_grade` derived from recorded markers (fixture flags, mode,
   agent provider), defaulting to `unknown`, and require the API to render "mocked/fixture" explicitly. A
   manifest-level `mode` alone cannot satisfy the §7 negative.

7. **Chain-link mapping missing Extraction/Resolution (see §2.2).** *Tightening:* publish an explicit
   table mapping each of the eight taskbook links to a recorded section or to `missing` (e.g. Extraction ←
   interpretation? Resolution ← none recorded ⇒ always `missing` for these records), so
   "缺项可定位" is deterministic and testable.

8. **`403` vs the frozen `not_found` isolation convention (see §2.4).** *Tightening:* follow the P4
   convention (unauthorised project ⇒ `not_found`) or record an explicit, reviewed deviation.

9. **Authorization-before-filesystem contradiction (see §2.4).** *Tightening:* replace with a staged check
   and state that no content, path or digest is returned before the grant check passes.

10. **New error codes extend a frozen vocabulary with no ADR (see §1.6).** *Tightening:* either amend
    ADR 0003 §1.4 / add a short P7 validation ADR listing the five new codes and their triggers, or map them
    to existing codes. Also fix the `not_found` vs `{kind}_not_found` wording and specify precedence
    (auth ≥ config ≥ parse ≥ manifest ≥ artifact) so the paired 200/403 acceptance test is deterministic.

11. **"Frozen" manifest is not authenticated.** `frozen_at`, `mode: real` and `credential_shaped` are all
    self-declared and unsigned. *Tightening:* either stop calling it "frozen" (call it "declared") or bind
    the manifest to a recorder-produced digest/attestation. At minimum, `manifest_sha256` should be returned
    and the acceptance test should confirm the reader surfaces that the manifest is unauthenticated.

12. **Resource limits absent.** No cap on `files[]`, artifact bytes, or masked-output size. *Tightening:*
    reuse `fingerprint.MAX_BYTES`-style caps and return a typed error (`payload_too_large` / 413 exists).

13. **Acceptance #1 "BLOCKED" has no unblock path.** The proposal correctly marks the real-FAIL-run
    criterion BLOCKED, but does not name the owner/action that would produce a real FAIL run (the P7 owner
    can run the existing recorder against a genuinely failing scenario). Without that, P6-T06 and P6-03c
    acceptance can never close. *Tightening:* add an explicit "unblock: P7 owner records one real FAIL run
    into `validation-runs/` via `recorder.py`" note and keep the fixture clearly non-evidence.

---

## 4. Verdict

### 4.1 Per-item verdicts (task item 1)

| Claim | Verdict |
| --- | --- |
| (a) nine real records, `stage: P0`, PASS(3)/BLOCKED(6), no FAIL in that format | **CONFIRMED** (stronger: no FAIL case and no `failure_class`/`failure_output` anywhere) |
| (b) no bundle manifest and no project attribution | **CONFIRMED** with nuance: no run-level `project` field and no `manifest.json`, but nested `project_id` values exist inside `cases[].state_*`/`tools[]` |
| (c) `case.evidence[]` free-form, includes absolute host paths | **CONFIRMED** (21 entries, 12 absolute) |
| (d) recorder `Case`/`Run` names, seven sections, sentinel, worst-case rule | **CONFIRMED** (minor wording: `tools` defaults to `[]`, not the sentinel; non-sentinel `{"reason": …}` contexts are not real context packs) |
| (e) cited codes/statuses exist; new codes do not collide | **CONFIRMED** for existence and non-collision; caveat: the five genuinely-new codes are not in ADR 0003 §1.4 and no ADR records them; `validation_run_not_found`/`artifact_not_found` are just `NotFound(kind, …)` |

### 4.2 Recommendation

**APPROVE WITH NAMED MANDATORY CHANGES** — do not implement as-is, do not reject. The proposal is
substantially sound, its factual claims hold up, and it explicitly refuses the dangerous shortcuts (no
verdict upgrade, no placeholder content, no fabricated sealed history). But it must not be frozen/derived
into `p7-validation-read.v1.json` until the following are closed, because several are security-relevant and
several would make the contract fail its own acceptance criteria.

**Mandatory before the contract is frozen** (ordered by severity):

1. **Define run discovery and pagination completely** so the nine real records are actually found from one
   configured root: recursive vs single-directory, depth, `total` includes corrupt rows, corrupt-row sort
   key, corrupt-vs-ignored boundary, and a tie-break that stays unique if two files share a run id.
   (Fixes acceptance 5; without it, criterion 5/6 fail.)
2. **Mandate per-component descriptor-relative `O_NOFOLLOW`** (reuse `fingerprint.hash_workspace_file`'s
   walk) and `realpath` containment, and refuse symlinked root/ancestors — the current final-component
   `O_NOFOLLOW` + `lstat` does not stop intermediate-directory symlink escape.
3. **Scan original artifact bytes for credential shapes before `form=original`**, independent of the
   self-declared `credential_shaped` flag; withhold on any match. Name exactly which masking function
   (`canonical.redact_text` substring semantics, not `sanitise_secrets` whole-string semantics) produces
   `masked_sha256`, and add a `mask_algorithm`/`mask_version`.
4. **Add a per-case/per-section evidence grade** so present-but-mocked responses are labelled; a
   manifest-level `mode` cannot satisfy the §7 negative "仅有 mocked response ⇒ 证据等级明确".
5. **Reconcile `403 forbidden_scope` for unauthorised projects with the frozen `not_found` isolation
   convention** (P4 read contract + `memory/api.py`), or record an explicit reviewed deviation; and replace
   "grant check before any filesystem touch" with a staged check that returns no content/path/digest before
   grants pass.
6. **Restate the project/`acl`/unattributed model over real, named mechanisms**: the config source for
   `acl` mode, how a key "holds a grant" for a project (Core has no per-project grant model today), and a
   single attribution enum with precedence. State that `validation:read`/`validation:unattributed` are added
   to `auth.SCOPES`, and confirm the grantability/actor-kind policy for `validation:unattributed`.
7. **Freeze the id grammars** (`^run-[0-9]{8}T[0-9]{6}Z$`, `^art_[a-z0-9][a-z0-9_.-]{0,63}$`) and require
   validation before any path construction; explicitly forbid drive-relative/UNC forms.
8. **Publish the eight-chain-link mapping** (Raw/Raw, Extraction←?, Resolution←?, Context, Agent, Tool,
   Evidence, State) so missing links are deterministic; and make the Context presence rule treat
   `{"reason": …}`-only objects as "present but not a context pack" rather than present.
9. **Add `not_applied_reason` to the detail response**, add `sources` and `manifest_sha256` to the manifest
   response, and define `mode_basis` and the `attribution` enum.
10. **Record the five new error codes in an ADR** (or map them to existing codes), fix the `not_found` vs
    `{kind}_not_found` wording, and specify error precedence (auth ≥ config ≥ parse ≥ manifest ≥ artifact).
11. **Add resource caps** (manifest `files[]` count, artifact bytes, masked-output bytes) and a
    root-present-but-unreadable/misconfigured error distinct from "empty list", so a broken root is never
    reported as zero records.
12. **Name the unblock path for acceptance #1** (who records a real FAIL run via `recorder.py`, and where),
    keeping the synthetic fixture permanently labelled as non-evidence.

---

## Appendix A — exact commands used

```bash
# Baselines
git -C "_worktrees/p7-01" log --oneline -3
git -C "_worktrees/p7-01" diff --stat 039f5a3e7800704e5f0478d01992f00eeb2ca3f1 3ebe46c
git -C "_worktrees/p7-01" status --porcelain

# Records
cd jasmine-core-v1
find validation-runs -name 'run-*.json' | wc -l
find validation-runs -name 'run-*.json' | sort
find validation-runs -name 'manifest.json' | wc -l
find validation-runs -name 'run-*.json' -exec dirname {} \; | sort -u
grep -rl '"verdict": "FAIL"' validation-runs/
find . -name 'run-*.json' -not -path '*/node_modules/*'

# Record analysis (python3)
#   stages/run verdicts/case verdicts/top-level keys
#   nested 'project' key walk
#   evidence[] absolute-path detection
#   worst-case verdict recomputation
#   {'reason':...}-only context count

# Recorder / errors
sed -n '28,120p' src/jasmine_core/validation/recorder.py
sed -n '105,113p' src/jasmine_core/validation/recorder.py
grep -oE 'code = "[a-z_]+"' src/jasmine_core/errors.py | sort -u
for c in validation_not_configured validation_run_not_found validation_run_corrupt \
    validation_manifest_corrupt artifact_modified original_withheld artifact_not_found; do
  grep -rl "$c" --include=*.py --include=*.md --include=*.json . | grep -v P7-01-CONTRACT-PROPOSAL; done

# Conventions / feasibility
sed -n '20,22p' src/jasmine_core/auth.py          # SCOPES tuple (no validation scopes)
sed -n '60,120p' src/jasmine_core/fingerprint.py  # per-component O_NOFOLLOW helper
sed -n '60,100p' docs/adr/0003-core-api-errors-and-idempotency.md
grep -n "not_found\|enumerat" src/jasmine_core/memory/api.py
sed -n '390,410p' web/contracts/p4-memory-read.v1.json   # authorisation_from_actor invariant
grep -n "unquote\|split_target\|parts.path" src/jasmine_core/api/request.py
cat web/server/core-paths.json
ls web/contracts/ ; ls scripts/p7
```

## Appendix B — files created by this review

Only this file was written:
`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/REVIEW-p7-01-proposal/findings.md`
No tracked file was modified, and nothing was committed or pushed.
