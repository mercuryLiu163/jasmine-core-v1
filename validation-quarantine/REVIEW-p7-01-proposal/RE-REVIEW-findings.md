# Delta re-review — `web/P7-01-CONTRACT-PROPOSAL.md` revision 2

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent, delegated independent reviewer).
* **Date of review run:** 2026-10-08 (host `Thu Oct  8 06:57:54 PDT 2026`; UTC `2026-10-08T13:57:54Z`).
* **Commits reviewed:**
  * rev 1 (the delta baseline): `3ebe46c` — `P7-01 phase 1: propose the minimal Validation read contract`
  * rev 2 (the artifact under review / worktree HEAD): `f397213` — `P7-01 phase 1 rev 2: fold the independent review's 12 mandatory changes`
  * proposal baseline: `integration/p6-03-realcore` @ `039f5a3e7800704e5f0478d01992f00eeb2ca3f1`
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`, branch `p7-01-validation-read`.
* **Authority:** rev-1 findings `validation-quarantine/REVIEW-p7-01-proposal/findings.md` (493 lines, read in full);
  `docs/Jasmine-Core-V1-P6-阶段任务书.md` §5/§7 (T06).
* **Method note (important):** the shared source repo `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1`
  is currently checked out on branch `p3-checkpoint-context-resume` @ `7016dabb8926d6bd6599f1f56b8f39cbda253ebf`
  and its working tree contains **neither** `src/jasmine_core/memory/` **nor** `web/contracts/`. All P4/code facts in
  this review were therefore re-verified from the **p7-01 worktree** at `f397213` (the proposal's own baseline). For
  every file quoted below I confirmed `git diff --quiet 039f5a3 -- <file>` ⇒ identical to the proposal baseline,
  i.e. the code facts are baseline facts, not artifacts of the revision. The gitignored real records
  (`validation-runs/**/run-*.json`) live in the source repo and were inspected there read-only.

---

## 1. Baseline integrity

Verbatim (run from the p7-01 worktree):

```
$ git rev-parse HEAD
f3972137aeb996e0c3f0f6da0884c3353cd18f58

$ git status --porcelain
?? validation-quarantine/

$ git diff --cached --stat
(empty — nothing staged)

$ git diff --stat 3ebe46c f397213
 web/P7-01-CONTRACT-PROPOSAL.md | 541 +++++++++++++++++++++++++++++------------
 1 file changed, 388 insertions(+), 153 deletions(-)

$ git diff --stat 3ebe46c f397213 -- . ':(exclude)web/P7-01-CONTRACT-PROPOSAL.md'
(empty — no other tracked file changed)
```

* `git diff --stat 3ebe46c f397213` touches **only** `web/P7-01-CONTRACT-PROPOSAL.md`: **CONFIRMED**.
* HEAD is `f3972137aeb996e0c3f0f6da0884c3353cd18f58`: **CONFIRMED**.
* Tracked working tree is clean (`git diff --stat` empty), nothing staged (`git diff --cached --stat` empty); the
  only untracked entry is this review directory: **CONFIRMED**.
* `git diff --stat 039f5a3 f397213` shows the proposal file as 427 added lines, i.e. it did not exist at the
  baseline; the revision is a pure document revision.

---

## 2. Closure table for the 12 rev-1 mandatory items (§4.2 of `findings.md`)

Summary (detail and quotes follow the table):

| # | Mandate (rev-1 §4.2) | Verdict | Anchor in rev 2 |
| --- | --- | --- | --- |
| 1 | Define discovery/pagination completely (nine findable from one root; `total`; corrupt sort key; corrupt-vs-ignored; unique tie-break) | **PARTIALLY CLOSED** | §2 (lines 86–147); core requirement verified, sort-key prose contradicts the tuple |
| 2 | Per-component descriptor-relative `O_NOFOLLOW`; `realpath` containment; refuse symlinked root/ancestors | **CLOSED** | §2 (97–101), §3 (180–187) |
| 3 | Independent credential scan before `form=original`; name the masker + `mask_algorithm`/`mask_version` | **PARTIALLY CLOSED** | §5 (271–282); scan weaker than the masker; rationale false; `mask_version` weak |
| 4 | Per-case/per-section evidence grade so present-but-mocked is labelled | **PARTIALLY CLOSED** | §4 (233–247); markers only; `mode: real` still covers unmarked mocks; no per-section grade |
| 5 | Reconcile `403` with P4 `not_found`; replace "before any filesystem touch" with a staged check | **CLOSED** | §6 (299–323) |
| 6 | Restate project/`acl`/unattributed over real named mechanisms + `auth.SCOPES` + actor-kind policy | **CLOSED** | §3 (207–210), §6 (309–334), §11 (416) |
| 7 | Freeze id grammars; forbid drive-relative/UNC | **CLOSED** | §7 (363–366), §3 (175–177) |
| 8 | Publish the eight-chain-link mapping; treat reason-only Context as not present | **PARTIALLY CLOSED** | §1.1 (65–84) fixed; §4 (215–227) mapping internally contradictory |
| 9 | Add `not_applied_reason`; add `sources` + `manifest_sha256`; define `mode_basis` + attribution enum | **CLOSED** (one dead enum value) | §3 (189–210), §5 (259–262) |
| 10 | Record new codes in an ADR; fix `not_found` wording; specify precedence | **PARTIALLY CLOSED** | §7 (336–374); heading says "five" vs seven; unused/missing codes; ADR not yet written |
| 11 | Resource caps + a root-present-but-unreadable/misconfigured error distinct from "empty list" | **PARTIALLY CLOSED** | §8 (376–383), §2 (137–141); unreadable subtree unhandled |
| 12 | Name the acceptance-#1 unblock path; keep the synthetic fixture labelled non-evidence | **CLOSED** (with a contract-exposure caveat) | §9 (385–399) |

### Item 1 — discovery / pagination / `total` — PARTIALLY CLOSED

Rev-2 text that closes the mandate (§2, lines 92–147):

> "**Root.** `JASMINE_CORE_VALIDATION_RUNS` … Unset ⇒ `503 validation_not_configured`. … opened with
> `O_DIRECTORY|O_NOFOLLOW`, so a **symlinked root is refused** …"
> "**Walk.** Recursive, **at most 4 directory levels below the root** (the nine need ≤3) …"
> "**Run-shaped.** A file is *run-shaped* iff its basename matches `^run-[0-9]{8}T[0-9]{6}Z\.json$`. Its
> **identity is the filename stem** (`run_id`) … Anything else run-shaped is **`record_status: "corrupt"`** …"
> "**Duplicates.** Two run-shaped files with the same stem are both listed with `record_status: "duplicate"` …"
> "`total` is defined as **the number of discovered run-shaped records that match the filters, including corrupt
> and duplicate ones**, and `count == len(runs)` for the current page; `has_more = offset + count < total`."

Independent re-verification (`find validation-runs -name 'run-*.json'`, source repo) — 9 files, directories at
directory-depth 2 (8 files) and 3 (1 file) below `validation-runs/`:

```
validation-runs/p0-resume-20260929T143554Z/main-46cf99f-real-acceptance/run-20260929T162925Z.json   depth 2
validation-runs/p0-resume-20260929T143554Z/p003-59ee8c4-real-acceptance/run-20260929T162307Z.json  depth 2
validation-runs/p0-resume-20260929T143554Z/p003-6dd2cc4-acceptance/run-20260929T145257Z.json       depth 2
validation-runs/p0-resume-20260929T143554Z/p003-6dd2cc4-real-acceptance/run-20260929T161915Z.json  depth 2
validation-runs/p0-resume-20260929T143554Z/p003-9dfc855-acceptance/run-20260929T144539Z.json       depth 2
validation-runs/p0-resume-20260929T143554Z/p003-final-acceptance/run-20260929T143845Z.json         depth 2
validation-runs/p0-takeover-20260929T142645Z/acceptance/run-20260929T142847Z.json                  depth 2
validation-runs/p0-takeover-20260929T142645Z/committed-acceptance/run-20260929T143156Z.json        depth 2
validation-runs/p1-implementation/p1-02-67bdbfc/p0-regression/run-20260929T173423Z.json            depth 3
```

A simulation of the stated walk (skips dot-dirs/`__pycache__`, no symlink traversal, depth ≤4) against the real
tree returns **9 run-shaped files** and only **288 scanned entries** — far below `MAX_SCANNED_ENTRIES = 20_000`:

```
maxdepth 3 (scanned, files, dirs, links, run_shaped) -> (229, 171, 58, 0, 9)
maxdepth 4 (scanned, files, dirs, links, run_shaped) -> (288, 217, 71, 0, 9)
maxdepth 100                                            -> (313, 230, 82, 1, 9)
```

Every real record also satisfies rev-2's five-field run-shaped rule (`run_id == stem`, `stage`, `created_at`,
`verdict ∈ {PASS,FAIL,BLOCKED}`, `cases` present) — so all nine are `record_status: ok`, not `corrupt`.

**Where it falls short (kept open):** the named corrupt-row sort key contradicts the declared total order.

* Declared tuple (lines 119–124):
  `(record_status_rank asc: ok=0, corrupt=1, duplicate=1, created_at desc (nulls last), run_id desc, relative_path asc)`.
* Prose (lines 126–128): "Corrupt rows carry `created_at: null` and are ordered within their group by
  `relative_path asc`; that is their named sort key."
* Contradictions: (i) rank-1 is `{corrupt, duplicate}` — duplicate rows are valid and carry a non-null
  `created_at`, so the parenthetical "nulls last, i.e. all rank-1 rows after all rank-0 rows" is a non-sequitur;
  (ii) within rank 1 the tuple orders by `run_id desc` before `relative_path asc`, so corrupt rows with different
  stems are **not** ordered "by `relative_path asc`"; (iii) a file that is both corrupt **and** shares a stem with
  another run-shaped file has no defined `record_status` ("corrupt" vs "duplicate").
The mandate explicitly required "the corrupt-row sort key"; two incompatible answers are given. This is cheap to
fix (make the tuple authoritative and delete/correct the prose; assign a rank per status), but it is not closed.

### Item 2 — symlink / traversal defense — CLOSED

Rev-2 text (§3, lines 182–187):

> "Reading a declared file uses the repo's existing **per-component descriptor-relative `O_NOFOLLOW` walk**
> (`fingerprint.hash_workspace_file`, which opens every component with `dir_fd`, rejects `.`/`..`/absolute parts,
> requires `S_ISREG`, and enforces a byte cap) — mirrored locally so the file is read **once** into a capped
> buffer, the digest verified on that buffer, and exactly that buffer served. Final-component `O_NOFOLLOW` plus an
> `lstat`/inode comparison is explicitly **not** sufficient: it cannot see a symlinked ancestor."

Plus §2 (lines 97–101): symlinked directories never traversed (`ignored_entries: symlinked_directory`); a symlinked
`run-*.json` is not a record; and §2 root "opened with `O_DIRECTORY|O_NOFOLLOW`, so a **symlinked root is
refused**".

Re-verified against `src/jasmine_core/fingerprint.py::hash_workspace_file` (baseline-identical):

```
69:def hash_workspace_file(root: Path, relative: str, remaining_bytes: int = MAX_BYTES) -> tuple[str, int]:
70:    """Open each path component with O_NOFOLLOW; never hash a symlink target.
76:    if not parts or any(part in (".", "..", "") for part in parts) or Path(relative).is_absolute():
80:    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
81:    directory = os.open(root, flags | os.O_DIRECTORY)
84:            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
87:        file_descriptor = os.open(parts[-1], flags, dir_fd=directory)
90:            if not stat.S_ISREG(metadata.st_mode):
92:            if metadata.st_size > remaining_bytes:
```

The mandated behavior is exactly what the primitive does; "mirrored locally, read once into a capped buffer" is
coherent because `hash_workspace_file` returns only `(digest, bytes)` and cannot hand back the verified buffer, so
a local variant that reads into a buffer, hashes that buffer, and serves that buffer is the correct adaptation.
The "realpath containment" second check is also present (line 178). **CLOSED.**

### Item 3 — original-bytes scan, masker naming, `mask_algorithm`/`mask_version` — PARTIALLY CLOSED

Rev-2 text (§5, lines 271–282):

> "`form=masked` (default) serves `canonical.redact_text(text)` — the **substring** masker used by the audit layer —
> with `mask_algorithm: "canonical.redact_text"` and `mask_version: <serving jasmine_core.__version__>`, so
> `masked_sha256` is reproducible. `sanitise_secrets` is explicitly **not** used: it replaces a string only when
> the *whole* string is credential-shaped, so a multi-line log would pass through unmasked."
> "`form=original` is served only when all three hold: the bytes were read once under the cap and hash to the
> declared `sha256`; the text decodes as strict UTF-8; and an **independent credential scan**
> (`canonical.looks_like_credential` over the actual text) finds nothing. Any hit ⇒ `403 original_withheld`
> regardless of the manifest's `credential_shaped` flag …"

The independent scan of actual bytes, and the naming of `canonical.redact_text` as the masker, are present. But the
item is not closed:

1. **The scan is strictly weaker than the masker it coexists with.** `canonical.looks_like_credential`
   (`canonical.py:97-105`) only tests `_LITERAL_CREDENTIAL_RES` (bearer/`sk-`/`ghp_`/JWT/PEM/AKIA/AIza/xox/
   github_pat). `canonical.redact_text` (`canonical.py:108-126`) additionally masks `_QUOTED_ASSIGNMENT_RE` and
   `_BARE_ASSIGNMENT_RE` (`password=…`, `api_key: …`, `token=…`). Empirically (read-only import of the library):

```
--- bare password
  looks_like_credential: False
  redact_text         : 'db_password=[REDACTED] in the log line'
  sanitise_secrets    : 'db_password=hunter2hunter2 in the log line'
--- quoted json secret
  looks_like_credential: False
  redact_text         : '{"api_key": "[REDACTED]"}'
  sanitise_secrets    : '{"api_key": "abcdef1234567890"}'
--- provider token in multiline
  looks_like_credential: True
  redact_text         : 'line1\nAuthorization: Bearer [REDACTED]\nline3'
  sanitise_secrets    : '[REDACTED: withheld from the evidence bundle]'
```

   Consequence: an artifact whose only secret is assignment-shaped (`password=`, `api_key:`) passes the
   `looks_like_credential` gate and is served in cleartext via `form=original`, even though the masker would have
   hidden it in `form=masked`. The mandate expected the scan to be at least as strong as the masker.
2. **The stated reason for rejecting `sanitise_secrets` is factually wrong.** `looks_like_credential` is a
   **containment search** (`any(p.search(text) …)`), not a whole-string match; when it hits, `sanitise_secrets`
   replaces the **entire value** (`recorder.py:179-181`). The third sample above shows a multi-line log being
   withheld wholesale — the opposite of "would pass through unmasked". The real difference is the *pattern set*,
   not whole-string vs substring matching.
3. **`mask_version` is not well-defined.** `jasmine_core.__version__ = "0.1.0"` (`src/jasmine_core/__init__.py:11`)
   and has not changed across P0–P6. If `canonical.redact_text`'s pattern table changes without a package version
   bump, `masked_sha256` is not reproducible from `(mask_algorithm, mask_version, input)`, which is the property
   the text claims.

### Item 4 — per-case/per-section evidence grade — PARTIALLY CLOSED

Rev-2 text (§4, lines 233–247):

> "**Evidence grade (per case).** Every case carries `evidence_grade ∈ {real, component, simulated, mocked,
> unknown}` plus `grade_basis`: 1. a **recorded per-case marker wins**: a `mock`/`fixture`/`simulated` marker in
> `agent_response`, a `not_applied_reason` naming a fixture, or an `executor` naming fixture/simulated/mock ⇒
> `mocked` (or `simulated`) … 2. otherwise the **effective bundle `mode`**, when it qualifies (§3) … 3. otherwise
> **`unknown`** …"
> "a case marker **can never be upgraded** by a bundle `mode`, and a manifest declaring `real` while any case
> carries a mock/fixture marker produces `errors[]`-style `warnings[]` … and a run-level `evidence_grade` that is
> not `real`."

The per-case grade and marker precedence are a real improvement, and `mode: real` now requires
`projects` non-empty plus a `sources.recorder` value (§3, line 203). But the §7 negative ("仅有 mocked response ⇒
证据等级明确") is only partly satisfied:

* Detection is **textual-marker only**. An unmarked mocked/fixture `agent_response` inside a `mode: real` bundle is
  graded `real` (`grade_basis: "the qualifying bundle manifest"`); nothing can detect it. So a `mode: real`
  manifest can still cover a mocked case, provided the mock does not literally contain `mock`/`fixture`/`simulated`.
* The "recorder provenance" that qualifies `mode: real` is itself a **self-declared, unsigned** manifest field
  (`sources.recorder = {path, sha256}`), so it can be fabricated; it is provenance-shaped, not provenance-verified.
* The mandate said "per-case/per-section"; the proposal delivers per-case only (no section grade).
* Note: the rev-2 commit message claims "per-case and per-section evidence grade"; the proposal does not contain a
  per-section grade (this is a commit-message overclaim, not a document defect).

### Item 5 — `not_found` reconciliation + staged check — CLOSED

Rev-2 text (§6, lines 301–323):

> "1. `dispatch` authenticates and requires `validation:read` **before the handler runs** … 2. The handler then
> resolves the id grammars (§7) and reads the run record (and, when asked, the manifest). **No run content, path,
> digest or status is placed in a response before step 3 passes.** 3. Project grants are then checked …"
> "**`acl`**: … A project the caller may not use is reported as **`404 project_not_found`** — the frozen P4
> convention (`p4-memory-read.v1.json` invariant `authorisation_from_actor`, `memory/api.py`), deliberately not
> 403, so the Inspector cannot become a project-enumeration oracle. rev 1 proposed 403 here; that is corrected."

Re-verified:

* `web/contracts/p4-memory-read.v1.json:400-401`: `"key": "authorisation_from_actor"`, `"statement": "… an
  unauthorised project is reported as \`not_found\` so the inspector cannot enumerate another project's work."`
* `src/jasmine_core/memory/scope.py:186-209`: `ProjectAccess.require` raises
  `errors.NotFound("project", project_id)` for a non-existent project and, in `acl` mode, for an actor without a
  `memory_project_actors` row ⇒ code `project_not_found`, status 404.
* `src/jasmine_core/api/dispatch.py:43-52`: `resolve` → `authenticate` → `principal.require(route.scope)` →
  `auth.touch` → `route.handler(...)`. So auth/scope genuinely precede the handler and any table/file work.

The `403`→`404` correction and the staged check are exactly what the mandate asked. **CLOSED.**

### Item 6 — named project/`acl`/unattributed mechanisms — CLOSED

Rev-2 text (§6, lines 309–334) names `memory.scope.deployment_mode()` / `JASMINE_MEMORY_DEPLOYMENT_MODE`
(default `single_user`), `memory_project_actors` rows, `ProjectAccess.require(...)`, `require_mode_ready()`, the
single attribution rule (§3, lines 207–210), the `auth.SCOPES` addition and the agent-key guard (§11, line 416:
"`SCOPES += ("validation:read", "validation:unattributed")`; one `ForbiddenActorKind` guard for
`validation:unattributed` on agent keys").

Re-verified: `scope.py:52` `MODE_ENV_VAR = "JASMINE_MEMORY_DEPLOYMENT_MODE"`; `scope.py:105-116` `deployment_mode`;
`scope.py:223-239` `require_mode_ready`; `auth.py:80-92` already contains the identical per-scope
`ForbiddenActorKind` guard pattern; `validation:read`/`validation:unattributed` are absent from
`auth.SCOPES` (`grep` returns nothing), so adding them is a genuine, correctly-identified shared-file change.
**CLOSED.**

### Item 7 — id grammars — CLOSED

Rev-2 text (§7, lines 363–366):

> "**Id grammars are validated before any path construction** (frozen here): `^run-[0-9]{8}T[0-9]{6}Z$` and
> `^art_[a-z0-9][a-z0-9_.-]{0,63}$` — anchored matches against the raw route capture, which
> `request.split_target` does **not** percent-decode, so `..%2f` simply fails the grammar. A path is only ever
> built from these two validated ids plus a manifest `path` that already passed §3."

Plus §3 (lines 175–177) refusing `C:…` drive-relative and `\\…` UNC forms. Re-verified: `request.py:72-74`
`split_target` uses `urlsplit` and returns `parts.path` without percent-decoding. **CLOSED.**

### Item 8 — eight-chain-link mapping + reason-only Context — PARTIALLY CLOSED

Rev-2 text (§1.1, lines 78–81) closes the Context half:

> "`context` additionally requires **pack evidence**: `present: true` only when the value is a mapping holding at
> least one of `context_id, pack, sections, rendered, content, tokens, parts`, or is a non-empty list. A
> reason-only `context` is reported `present: false, present_kind: "reason_only"` — so the 27 real cases are *not*
> shown as having a context pack."

Re-verified: 57 of 63 `context` values are non-sentinel and **27** are reason-only mappings. Good.

The mapping table (§4, lines 215–227) is published, but it is internally contradictory and its Resolution row is
not honestly derivable:

> "Seven recorded sections map onto the eight chain links; **two links are not recorded at all** and are therefore
> always named missing, never filled:"
> `| Extraction | **none** — recorder.py records no extraction section | always … |`
> `| Resolution | case.interpretation | present_kind ∈ {sentinel, absent, reason_only} |`

* `recorder.py:29-37` `SECTIONS` contains no `extraction` and no `resolution`; it contains `interpretation`. So
  **Extraction** is genuinely never recorded, but **Resolution is not** marked unrecorded — the table maps it to
  `interpretation`. The prose "two links are not recorded at all" therefore describes only one row. §9 criterion 2
  repeats the error: "§4 chain links name Extraction **and Resolution** as never-recorded".
* `interpretation` is not `Resolution`. ADR 0008 / the P2 taskbook treat Interpretation (a candidate) and
  Resolution (the resolved decision) as distinct (`Jasmine-Core-V1-P2-阶段任务书.md:107,111`). Mapping
  `Resolution ← case.interpretation` is a judgment the proposal never justifies, and it silently equates two
  P2 concepts.
* "Seven recorded sections" is also inaccurate: the table consumes eight recorded case fields, including
  `case.evidence`, which is **not** one of the seven `SECTIONS` (`recorder.py:29-37`).

### Item 9 — `not_applied_reason`, `sources`, `manifest_sha256`, `mode_basis`, attribution enum — CLOSED

Rev-2 text: §5 Detail (line 260) adds `not_applied_reason`; §3 Manifest response (lines 189–195) adds
`projects[], sources, files[], manifest_sha256, manifest_authentication`; §3 effective-mode table (199–205) defines
exact `mode_basis` strings; §3 Attribution (207–210) defines `status ∈ {attributed, unattributed, unknown}` with a
rule and reasons. **CLOSED**, with one dead value: no condition ever yields `attribution.status = "unknown"`
(every branch is `attributed` or `unattributed`), so the enum advertises a state the contract cannot produce.
Also §2's list row names `record_sha256`, which is never defined (see §3 below).

### Item 10 — ADR for new codes, wording, precedence — PARTIALLY CLOSED

Rev-2 text (§7, lines 336–374):

> "`NotFound` never emits a bare `not_found` (rev 1's table was imprecise about that)."
> "**Precedence** … | 1 | missing/unknown token | `401 unauthenticated` | 2 | missing `validation:read` | `403
> forbidden_scope` … | 3 | root unset / missing / not a directory / symlinked | `503 validation_not_configured` |
> 4 | walk caps exceeded | `503 validation_index_too_large` | 5 | malformed … | `400 invalid_request` | …"
> "`docs/adr/0015-p7-validation-read-contract.md` records `validation_not_configured` (503),
> `validation_index_too_large` (503), `validation_run_corrupt` (409), `validation_run_ambiguous` (409),
> `validation_manifest_corrupt` (409), `artifact_modified` (409), `original_withheld` (403) …"

Re-verified:
* `NotFound` (`errors.py:70-77`) sets `self.code = f"{kind}_not_found"` — the wording fix is correct.
* `PayloadTooLarge` is real: `errors.py:104-106` `status = 413`, `code = "payload_too_large"`.
* `MemoryDeploymentNotReady` is real: `errors.py:133-143` `status = 503`,
  `code = "memory_deployment_not_ready"`.
* Collision probe for the seven new strings against all non-proposal `.py/.md/.json` returns **nothing** for
  `validation_not_configured`, `validation_index_too_large`, `validation_run_corrupt`,
  `validation_run_ambiguous`, `validation_manifest_corrupt`, `artifact_modified`, `original_withheld` (only the
  rev-1 findings file mentions them). `project_not_found` is not new — it is the existing `NotFound` pattern.
* `docs/adr/` currently ends at `0014-p4-memory-and-durable-jobs.md`; `0015` does not exist, so there is no
  numbering collision — but the ADR is a forward commitment, not a present artifact.

Not closed:
* §7's heading still says "**five** new code strings" while the section lists **seven**, and §11/ADR 0015 say seven.
  This is a rev-2-introduced inconsistency (rev 1 had five genuinely new codes).
* §7's introduction lists `payload_too_large` 413, `schema_version_unsupported` 503 and `database_busy` 503 as
  "the existing vocabulary is reused where it exists", but **no precedence row and no route in this all-`GET`
  slice can produce any of them** (caps use 503 `validation_index_too_large`, size problems use 409
  `validation_manifest_corrupt`/`artifact_modified`).
* §6 mandates `require_mode_ready()` ("an `acl` deployment with no grants at all refuses `503`"), whose real code
  is `memory_deployment_not_ready` — but that code string appears **nowhere** in §7.
* The precedence order itself is deterministic (401/403 before 503; corrupt 409 is unreachable from
  `project_not_found` 404 because a corrupt manifest has no effective projects and falls to row 6), so the
  *ordering* concern is addressed; the *vocabulary* is not.

### Item 11 — caps + misconfigured-root error — PARTIALLY CLOSED

Rev-2 text (§8, lines 376–383): "Root walk: depth 4, `MAX_SCANNED_ENTRIES = 20_000`, `MAX_RUN_SHAPED = 500`.
Manifest: ≤ 1 MiB, `files[]` ≤ 128 entries. Declared and served artifact text: ≤ `MAX_ARTIFACT_BYTES = 8 MiB`;
masked output is capped at the same size. List: `limit` ≤ 100." And §2 (137–141): "`discovery: {root_present,
max_depth, scanned_dirs, run_shaped_files, ignored_files, capped}` and, when `total == 0`, `empty_reason ∈
{no_run_shaped_files, all_filtered_out}` … `ignored_entries` is a count, with at most 20 named samples".

* Caps exist and are enforced by typed errors, not silent truncation: over-cap ⇒ 503
  `validation_index_too_large`; over-size manifest ⇒ 409 `validation_manifest_corrupt`; over-size artifact ⇒ 409
  `artifact_modified`. The only truncation is the explicitly-stated 20-sample cap on `ignored_entries`, whose
  count remains complete. **No silent truncation found.**
* The "misconfigured root" half is addressed via `root_present` + `empty_reason` (a present-but-record-less root
  reports `no_run_shaped_files`, not an empty list).
* The "**root-present-but-unreadable**" half is **not** addressed: there is no rule for an `OSError`/permission
  failure while walking a subtree, and `empty_reason` has no value for it. A root that exists and is a directory
  but cannot be read (or a subtree that cannot be stat'd) has no defined response. The mandate named this case
  explicitly.

### Item 12 — acceptance-#1 unblock path + fixtures — CLOSED

Rev-2 text (§9, lines 389, 396–399):

> "**Unblock action: the P7 owner records one genuinely failing scenario with `recorder.py` into the configured run
> root** (`validation-runs/p7-<scenario>/`), after which criterion 1 is re-validated against that real record.
> Until then `P6-T06`/P6-03c cannot close."
> "**Fixtures are never evidence.** The synthetic FAIL fixture lives under `tests/fixtures/p7/` and in
> validator-created temporary roots, **never** in the operator's configured run root; every fixture record and
> every document that cites it labels it `synthetic: true` …"

The unblock action names owner, tool, destination and re-validation — actionable. **CLOSED**, with the
contract-exposure caveat recorded under new-defect N10 (no response shape surfaces `synthetic`).

---

## 3. New defects introduced by rev 2

Each cites a rev-2 section. Ordered roughly by severity.

* **N1 (security, §5 lines 271–282).** The `form=original` gate uses only `canonical.looks_like_credential`, which
  is weaker than the `canonical.redact_text` masker it is paired with: assignment-shaped secrets
  (`password=…`, `api_key: …`, `token=…`) are masked by `redact_text` but not detected by `looks_like_credential`,
  so they are served in cleartext as original. Demonstrated by the empirical transcript in item 3 above. This
  narrows item 3's closure and is security-relevant.
* **N2 (ungrounded claim, §5 lines 275–276).** "`sanitise_secrets` … replaces a string only when the *whole*
  string is credential-shaped, so a multi-line log would pass through unmasked" is false: `looks_like_credential`
  is a containment search and `sanitise_secrets` replaces the whole value on any hit (a multi-line log containing a
  provider token is withheld wholesale, not passed through). The choice of `redact_text` is defensible; the stated
  reason is not.
* **N3 (internal contradiction, §4 lines 215–216 vs table and §9 line 390).** "two links are **not recorded at
  all**" while the table marks only Extraction as `none` (Resolution ← `case.interpretation`); §9 criterion 2
  repeats "Extraction and Resolution as never-recorded". One of the two statements is wrong. Related:
  "Seven recorded sections map onto the eight chain links" counts eight recorded fields and treats `evidence`,
  which is not in `SECTIONS`, as a section.
* **N4 (internal contradiction, §5 lines 264–269 vs 284–288).** Artifact metadata promises "there is no ambiguous
  bare `sha256`", but the content envelope introduced two paragraphs later is
  `{artifact_id, form, media_type, bytes, sha256, …}` — a bare `sha256` whose meaning depends on `form`.
* **N5 (factual error, §3 line 203).** The effective-mode rule accepts `sources.recorder` as `"<path>@<40-hex>"`,
  but 40 hex is a SHA-1/Git-commit length; the field is a `sha256` (64 hex, as the sibling `{path, sha256}` form
  and the example on line 160 both imply). The rule is self-inconsistent and ambiguous about what digest qualifies.
* **N6 (undefined field, §2 lines 145–147).** The list row includes `record_sha256`, but no rule defines what it
  hashes (raw file bytes? canonical JSON? record identity?). Acceptance criterion 6 (§9 line 394) requires the
  validator to compare `record_sha256`, so an undefined field is not testable. Related dead value:
  `attribution.status = unknown` (§3 line 207) has no derivation rule.
* **N7 (heading vs content, §7 line 336 + §11 line 415).** Heading: "five new code strings"; the section lists
  seven and ADR 0015 records seven.
* **N8 (vocabulary, §7 lines 338–341, 363–374; §6 line 313).** `payload_too_large` (413),
  `schema_version_unsupported` (503) and `database_busy` (503) are advertised but no row/route can produce them;
  conversely `memory_deployment_not_ready` (503), which §6's mandated `require_mode_ready()` raises, is absent
  from §7. Real code/status re-verified: `payload_too_large` = 413; `memory_deployment_not_ready` = 503.
* **N9 (contract cannot enforce its own claim, §9 lines 396–399).** "every fixture record … labels it
  `synthetic: true`" is not exposed by any response shape in §2/§5, and §10 forbids recorder changes (the
  `Run`/`Case` dataclasses have no `synthetic` field). A `synthetic: true` file dropped into the configured root
  would be served indistinguishably from a real record; "never evidence" is process-enforced, not
  contract-enforced.
* **N10 (minor, §1.1 line 75).** `reason_only` covers "a string that is only such a reason" but no rule defines
  how a string is recognised as reason-only.

Positive rev-2 additions verified as accurate (not defects): the JSON-only envelope claim. §5 lines 284–288 state
`api/server.py::_respond` "always sends `Content-Type: application/json; charset=utf-8` and **drops any
handler-supplied `content-type`**". Verified in `api/server.py:153-168`:

```
159:        self.send_header("Content-Type", JSON_CONTENT_TYPE)
163:        for key, value in (headers or {}).items():
164:            if key.lower() != "content-type":
165:                self.send_header(key, value)
```

with `dispatch.py:25 JSON_CONTENT_TYPE = "application/json; charset=utf-8"` and `Response.headers` a plain dict
(`request.py:61-64`), so non-`content-type` headers do pass through and HTML can never be rendered. The §2 walk
caps, §2 nine-records-depth claim, §1 record facts, and §5 `present_kind` context rule are also independently
verified.

---

## 4. Residual risk register (what the repository alone cannot settle)

Genuinely unverifiable from the repository; a validator must exercise these at runtime.

1. **Provenance of the nine records as genuinely "real".** Their paths/commits label them, but nothing in the files
   proves a real conversation produced them; that remains the operator's claim.
2. **Acceptance criteria 1–6 behaviour.** Nothing is implemented; all closure is textual. Every route, status,
   header, cap and error must be exercised at runtime.
3. **Feasibility of reusing `ProjectAccess` in the validation reader.** `ProjectAccess` needs a live
   `sqlite3.Connection`, the `projects` table and `memory_project_actors`; the proposal does not state where the
   validation reader gets that connection, nor what happens when the P4 migration is absent. Reuse is asserted,
   not demonstrated.
4. **Whether the local "mirrored" walk really reads once and serves the verified buffer.** Correct only if the
   implementation does not re-open; the contract mandates it, tests must assert it (the digest must match the
   served bytes under a swap race).
5. **Whether `masked_sha256` is reproducible across code revisions.** Depends on `mask_version` being a real
   masker revision (currently `"0.1.0"`), and on the `redact_text` pattern table being frozen.
6. **Whether any unmarked mocked response exists in practice.** Marker-based grading cannot detect it; only a real
   recorder run can show what the P7 FAIL fixture looks like.
7. **Whether the scope allocation and agent-key guard are granted.** §11 requests `auth.SCOPES` changes and the
   `ForbiddenActorKind` guard from the Core owner; until accepted, the permission model is a proposal.
8. **Whether the configured root is actually `validation-runs/`.** In-repo it makes all nine findable under the
   stated walk (verified), but this depends on operator configuration and on the walk avoiding the two repo
   snapshot trees (which it does within the 20 000-entry cap).
9. **Whether ADR 0015 is actually written before the contract is frozen.** The proposal commits to it; it does not
   yet exist.
10. **`realpath` containment is a check-then-open (TOCTOU).** The per-component `O_NOFOLLOW` walk is the real
    defense; the containment check must not be treated as the primary control.
11. **Whether a `synthetic: true` fixture can be distinguished by the API.** Currently no response field surfaces
    it (N9).

---

## 5. Verdict

**APPROVE WITH NAMED MANDATORY CHANGES.**

Not `APPROVE (freeze as-is)`: six of the twelve rev-1 mandatory items are only **partially** closed (items 1, 3,
4, 8, 10, 11), and one of those (item 3, via N1) leaves a demonstrated security hole.

Not `REJECT`: every rev-1 factual claim re-verified here still holds (nine records, depths, verdict counts, no
FAIL, context/evidence counts, error-code existence and non-collision), the baseline is clean, and the revision's
architecture is sound. The remaining work is targeted edits to a sound proposal, not a rewrite.

Plainly, the rev-1 mandatory items that are **not closed** are: **1, 3, 4, 8, 10, 11**.

**Mandatory before the contract is frozen** (ordered by severity; each is concrete to implement):

1. **Fix the original-form credential scan so it is at least as strong as the masker (§5).** Run the
   `canonical.redact_text` pattern set (or `looks_like_credential` **plus** the quoted/bare assignment patterns)
   over the decoded text and withhold on any match. Today `form=original` serves `password=…`/`api_key: …`
   content that `form=masked` would hide. (N1)
2. **Correct the `sanitise_secrets` rationale (§5), or delete it.** State that `looks_like_credential` is a
   containment search that makes `sanitise_secrets` replace the whole value, and that the reason for choosing
   `redact_text` is its broader pattern set / in-place masking. (N2)
3. **Define `mask_version` as a masker-revision value, not the package version (§5).** `jasmine_core.__version__`
   is `"0.1.0"` and does not track `canonical.py`. Use a named constant or a digest of the pattern table, and state
   that `masked_sha256` is computed over the served masked bytes. (Item 3)
4. **Reconcile the eight-chain-link mapping (§4 and §9).** Either (a) mark **both** Extraction and Resolution
   `missing` (`Resolution` is not a recorded section; `interpretation` is a different P2 concept), or (b) justify
   `Resolution ← interpretation` against P2's Interpretation/Resolution distinction. Delete the contradictory
   "Extraction and Resolution as never-recorded" in §9 criterion 2, and correct "Seven recorded sections" to match
   the fields actually used (which include `case.evidence`, not a `SECTIONS` member). (N3)
5. **Fix the §7 error vocabulary.** Change the heading "five new code strings" to **seven**; add
   `memory_deployment_not_ready` (503) to the precedence table for `require_mode_ready()`; and either map or remove
   the advertised-but-unreachable `payload_too_large` (413), `schema_version_unsupported` (503) and
   `database_busy` (503). (N7, N8)
6. **Fix the corrupt-row ordering and status rules (§2).** Make the declared tuple the single authority for
   corrupt/duplicate rows (delete or correct "ordered within their group by `relative_path asc`" and the "nulls
   last, i.e. all rank-1 rows …" parenthetical), and define the `record_status` of a file that is both corrupt and
   shares a stem. (Item 1)
7. **Fix the manifest provenance grammar (§3).** Replace `"<path>@<40-hex>"` with the correct `sha256` form
   (64 hex) or explicitly define it as a Git commit; keep it consistent with the `{path, sha256}` form. (N5)
8. **Remove the ambiguous bare `sha256` from the content envelope (§5).** Use `served_sha256` (plus
   `original_sha256`/`masked_sha256` or `form`) so the envelope matches the "no ambiguous bare `sha256`" promise.
   (N4)
9. **Define `record_sha256` (§2) and resolve the dead `attribution.status = unknown` (§3).** State exactly what
   bytes `record_sha256` hashes; either give `unknown` a derivation rule or drop it from the enum. (N6)
10. **Specify the root-present-but-unreadable case (§2/§7/§8).** Define the response for an unreadable root or
    subtree (e.g. a third `empty_reason` / a `discovery.unreadable` counter and a typed 503), completing
    mandatory item 11. (Item 11)
11. **Surface the `synthetic` label or state it is process-only (§9 with §2/§5).** Add a `synthetic` field to the
    list/detail row, or state explicitly that synthetic detection is out of the read contract and enforced only by
    keeping fixtures out of the root. (N9)

---

## Appendix A — exact commands run (verbatim key output)

Run from `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01` unless noted.

```bash
# --- baseline integrity ---
git rev-parse HEAD
# f3972137aeb996e0c3f0f6da0884c3353cd18f58
git status --porcelain
# ?? validation-quarantine/
git diff --cached --stat
# (empty)
git diff --stat 3ebe46c f397213
#  web/P7-01-CONTRACT-PROPOSAL.md | 541 +++++++++++++++++++++++++++++------------
#  1 file changed, 388 insertions(+), 153 deletions(-)
git diff --stat 3ebe46c f397213 -- . ':(exclude)web/P7-01-CONTRACT-PROPOSAL.md'
# (empty)
git diff --stat 039f5a3 f397213
#  web/P7-01-CONTRACT-PROPOSAL.md | 427 +++++++++++++++++++++++++++++++++++++++++
#  1 file changed, 427 insertions(+)
for f in src/jasmine_core/memory/scope.py src/jasmine_core/api/server.py \
         src/jasmine_core/canonical.py src/jasmine_core/validation/recorder.py \
         src/jasmine_core/errors.py; do
  git diff --quiet 039f5a3 -- "$f" && echo "$f same-as-039f5a3" || echo "$f DIFFERS"; done
# all five: same-as-039f5a3
```

```bash
# --- source repo state (code facts read from the worktree, records from here) ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
git rev-parse HEAD          # 7016dabb8926d6bd6599f1f56b8f39cbda253ebf
git rev-parse --abbrev-ref HEAD   # p3-checkpoint-context-resume
ls src/jasmine_core/memory  # No such file or directory
ls web/contracts           # No such file or directory

# --- real records: count, depth, run-shaped conformance, facts ---
find validation-runs -name 'run-*.json' | wc -l        # 9
find validation-runs -name 'run-*.json' | sort | awk -F/ '{print NF-1, $0}'
# 3 validation-runs/p0-resume-.../main-.../run-...json   (x7 more, same shape)
# 4 validation-runs/p1-implementation/p1-02-67bdbfc/p0-regression/run-...json
find validation-runs -name 'manifest.json' | wc -l     # 0
find validation-runs -type l                           # one link: .../P1-T07/workspace/link.txt
# python3 re-verification of all nine (see item 1): stages {P0:9}, run verdicts {PASS:3,BLOCKED:6},
# case verdicts {PASS:57,BLOCKED:6}, cases 63, evidence 21/12 absolute,
# context non-sentinel 57 / reason-only 27, 63/63 cases with neither failure_class nor failure_output,
# worst-case==recorded for all: True, run_id_ok True and req_present True for every file.

# --- walk simulation (skips dot-dirs/__pycache__, no symlink traversal) ---
# maxdepth 3 -> (scanned 229, files 171, dirs 58, links 0, run_shaped 9)
# maxdepth 4 -> (scanned 288, files 217, dirs 71, links 0, run_shaped 9)
# maxdepth 100 -> (scanned 313, files 230, dirs 82, links 1, run_shaped 9)
```

```bash
# --- code facts (p7-01 worktree) ---
grep -n "def hash_workspace_file" -A40 src/jasmine_core/fingerprint.py
# per-component O_NOFOLLOW via dir_fd; rejects . .. "" and absolute; S_ISREG; byte cap (lines quoted in item 2)
grep -n "class PayloadTooLarge" -A3 src/jasmine_core/errors.py
# class PayloadTooLarge: status = 413, code = "payload_too_large"
grep -n "class MemoryDeploymentNotReady" -A11 src/jasmine_core/errors.py
# class MemoryDeploymentNotReady: status = 503, code = "memory_deployment_not_ready"
grep -oE 'code = "[a-z_]+"' src/jasmine_core/errors.py | sort -u
# actor_mismatch, database_busy, event_id_conflict, fingerprint_unavailable, forbidden_actor_kind,
# forbidden_scope, internal_error, interpretation_idempotency_conflict, invalid_interpretation_source,
# invalid_request, invalid_state_transition, memory_deployment_not_ready, method_not_allowed,
# migration_conflict, missing_evidence, missing_expected_revision, not_found, payload_too_large,
# revision_conflict, rule_key_conflict, schema_version_unsupported, source_event_duplicate,
# unauthenticated, unexpected_expected_revision, unsupported_media_type
grep -n "def require" -A24 src/jasmine_core/memory/scope.py   # ProjectAccess.require -> NotFound("project", ...)
grep -n "def require_mode_ready" -A18 src/jasmine_core/memory/scope.py  # raises MemoryDeploymentNotReady
grep -n "MODE_ENV_VAR\|def deployment_mode" src/jasmine_core/memory/scope.py
# MODE_ENV_VAR = "JASMINE_MEMORY_DEPLOYMENT_MODE"; def deployment_mode(...)
sed -n '153,168p' src/jasmine_core/api/server.py
# _respond: send_header("Content-Type", JSON_CONTENT_TYPE); skips handler "content-type"
sed -n '43,52p' src/jasmine_core/api/dispatch.py
# resolve -> authenticate -> principal.require(scope) -> auth.touch -> route.handler
sed -n '400,401p' web/contracts/p4-memory-read.v1.json
# "key": "authorisation_from_actor", "statement": "... an unauthorised project is reported as `not_found` ..."
sed -n '20,37p' src/jasmine_core/validation/recorder.py   # SECTIONS has no extraction/resolution
sed -n '179,182p' src/jasmine_core/validation/recorder.py # sanitise_secrets replaces whole string on looks_like_credential
sed -n '97,126p' src/jasmine_core/canonical.py           # looks_like_credential = search; redact_text = substring mask
grep -n "__version__" src/jasmine_core/__init__.py        # __version__ = "0.1.0"
ls docs/adr/                                              # ... 0014-p4-memory-and-durable-jobs.md (no 0015)
for c in validation_not_configured validation_index_too_large validation_run_corrupt \
         validation_run_ambiguous validation_manifest_corrupt artifact_modified original_withheld; do
  grep -rl "$c" --include=*.py --include=*.md --include=*.json . | grep -v P7-01-CONTRACT-PROPOSAL; done
# every hit is only validation-quarantine/REVIEW-p7-01-proposal/findings.md; the two new strings
# (validation_index_too_large, validation_run_ambiguous) have no hits at all
grep -rn "validation:read\|validation:unattributed" --include=*.py src/   # (nothing)
```

```bash
# --- empirical masker / scan semantics (read-only import; worktree src on PYTHONPATH) ---
PYTHONPATH="$PWD/src" python3 - <<'PY'
from jasmine_core import canonical
from jasmine_core.validation import recorder
for name, s in {
 "bare password": "db_password=hunter2hunter2 in the log line",
 "quoted json secret": '{"api_key": "abcdef1234567890"}',
 "provider token in multiline": "line1\nAuthorization: Bearer sk-abcdefgh12345678\nline3",
}.items():
    print(name, canonical.looks_like_credential(s),
          repr(canonical.redact_text(s)), repr(recorder.sanitise_secrets(s)))
PY
# bare password           -> False | 'db_password=[REDACTED] in the log line' | unchanged
# quoted json secret      -> False | '{"api_key": "[REDACTED]"}'              | unchanged
# provider token in multiline -> True | 'line1\nAuthorization: Bearer [REDACTED]\nline3' | whole value withheld
```

## Appendix B — files written

Only this file was written:

`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-findings.md`

No tracked file was modified. Nothing was staged, committed or pushed; HEAD remains `f397213`. One scratch file
used to hold the revision diff (`/tmp/p701-diff.txt`) was removed. All other reads were read-only.
