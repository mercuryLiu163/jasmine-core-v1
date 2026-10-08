# Third-pass delta re-review — `web/P7-01-CONTRACT-PROPOSAL.md` revision 3

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent, delegated independent reviewer).
* **Date of review run:** 2026-10-08 (host `Thu Oct  8 07:03:41 PDT 2026`; UTC `2026-10-08T14:03:41Z`).
* **Commits reviewed:**
  * rev 2 (the delta baseline): `f3972137aeb996e0c3f0f6da0884c3353cd18f58` — `P7-01 phase 1 rev 2: fold the independent review's 12 mandatory changes`
  * rev 3 (the artifact under review / worktree HEAD): `c8fd597ac6f6617072a1772595c54ec141d9c01b` — `P7-01 phase 1 rev 3: close the delta review's 11 mandatory changes`
  * proposal baseline: `integration/p6-03-realcore` @ `039f5a3e7800704e5f0478d01992f00eeb2ca3f1`
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`, branch `p7-01-validation-read`.
* **Authority:** `validation-quarantine/REVIEW-p7-01-proposal/findings.md` (493 lines, read in full) and
  `validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-findings.md` (658 lines, read in full);
  `docs/Jasmine-Core-V1-P6-阶段任务书.md` §5/§7 (T06).
* **Method note:** every code fact below was re-verified from the p7-01 worktree at `c8fd597`; the nine
  gitignored real records were read read-only from the shared source repo
  `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1`. I did not modify any tracked file, did
  not commit and did not push. My only write is this file.

---

## 1. Baseline integrity

Verbatim, run from the p7-01 worktree:

```
$ git rev-parse HEAD
c8fd597ac6f6617072a1772595c54ec141d9c01b

$ git branch --show-current
p7-01-validation-read

$ git status --porcelain
?? validation-quarantine/

$ git diff --cached --stat
(empty)

$ git diff --stat f397213 c8fd597
 web/P7-01-CONTRACT-PROPOSAL.md | 504 +++++++++++++++++++++++++----------------
 1 file changed, 312 insertions(+), 192 deletions(-)

$ git diff --stat f397213 c8fd597 -- . ':(exclude)web/P7-01-CONTRACT-PROPOSAL.md'
(empty)

$ git diff --quiet f397213 c8fd597 -- web/P7-01-CONTRACT-PROPOSAL.md; echo $?
exit=1
```

* `git diff --stat f397213 c8fd597` touches **only** `web/P7-01-CONTRACT-PROPOSAL.md`; the exclusion
  variant is empty: **CONFIRMED**.
* HEAD is `c8fd597ac6f6617072a1772595c54ec141d9c01b` on branch `p7-01-validation-read`: **CONFIRMED**.
* Nothing is staged (`git diff --cached --stat` empty) and the only working-tree entry is the untracked
  review directory `?? validation-quarantine/` (which holds `findings.md` and `RE-REVIEW-findings.md`):
  **CONFIRMED**. Nothing was committed beyond `web/P7-01-CONTRACT-PROPOSAL.md`.
* `git show --stat c8fd597` likewise reports a single file, 312 insertions / 192 deletions.

**One provenance discrepancy, noted here rather than in §3:** the rev-3 commit message ends
"Both rev-1 and rev-2 reviews and the dry-run evidence stay in the worktree." The two reviews are indeed in
`validation-quarantine/REVIEW-p7-01-proposal/`, but **no dry-run artifact is in the worktree**
(`find validation-quarantine -type f` returns exactly the two review files; `git status
--untracked-files=all` shows exactly them). Scratch dry-run scripts exist only under `/tmp`
(`/tmp/p7-fixture-dryrun.py`, `/tmp/p7-rule-dryrun.py`, `/tmp/p7-rev3-notes.md`), i.e. ephemerally and
outside the repository. The *substantive* real-tree claims are re-verified below; the synthetic-adversarial
claims are not reproducible from anything retained (see §3 D7).

---

## 2. Closure table for the 11 mandatory changes in `RE-REVIEW-findings.md` §5

| # | Mandate (rev-2 §5) | Verdict | Anchor in rev 3 |
| --- | --- | --- | --- |
| 1 | Make the `form=original` scan at least as strong as the masker (N1) | **CLOSED** | §5 (lines 358–366) |
| 2 | Correct or delete the `sanitise_secrets` rationale (N2) | **CLOSED** | §5 (lines 352–356) |
| 3 | `mask_version` = masker revision, not `__version__` (item 3) | **CLOSED** | §5 (348–351), §11 (520, 522) |
| 4 | Reconcile the eight-chain-link mapping (N3) | **CLOSED** (mandate) — see D1 | §4.1 (261–285), §9 (493) |
| 5 | Fix the §7 error vocabulary (N7, N8) | **CLOSED** — see D2 | §7 (427–471) |
| 6 | Corrupt-row ordering and status rules (item 1) | **CLOSED** — see D5, nit | §2 (122–147) |
| 7 | Manifest provenance grammar 64 hex (N5) | **CLOSED** | §3 (244, 248–249) |
| 8 | Remove the ambiguous bare `sha256` (N4) | **CLOSED** | §5 (338–343, 371–372) |
| 9 | Define `record_sha256`; resolve dead `attribution.status` (N6) | **CLOSED** | §2 (166–167), §3 (251–255) |
| 10 | Specify root-present-but-unreadable (item 11) | **CLOSED** — see §3 D6 | §2 (169–174), §7 rows 3–4, §8 |
| 11 | Surface the `synthetic` label or scope it out (N9) | **CLOSED** | §4.2 (317–320), §2 (163), §5 (332) |

All eleven rev-2 mandatory items are addressed. Detail and independent re-verification follow; the new
defects the closure work introduced or left unaddressed are in §3.

### Mandatory 1 — original-form scan at least as strong as the masker — **CLOSED**

Rev-3 text (§5, lines 358–366):

> "**`form=original` is served only when the masker is a no-op on the content**, which makes the gate at
> least as strong as the masker by construction. All four must hold: the bytes were read once under the cap
> and hash to the declared `sha256`; the text decodes as strict UTF-8; the manifest does not declare
> `credential_shaped: true`; and **`canonical.redact_text(text) == text`**. Any failure ⇒
> `403 original_withheld`, with `details.masked_sha256` telling the caller what *is* available and
> `details.reason ∈ {credential_shaped_declared, mask_not_noop, not_utf8}`. This closes the rev-2 hole where
> an assignment-shaped secret (`db_password=…`, `{"api_key": "…"}`) was masked by `form=masked` yet served in
> cleartext by `form=original`, because `looks_like_credential` alone does not match those shapes (verified
> by dry run: `looks_like_credential: False` and `redact_text` masking both samples)."

Independent re-verification (`PYTHONPATH=<worktree>/src`, read-only import of `jasmine_core.canonical` and
`jasmine_core.validation.recorder`), the three samples from the rev-2 review:

```
--- bare password
  looks_like_credential: False
  redact_text         : 'db_password=[REDACTED] in the log line'
  redact_text==input  : False -> original WITHHELD
  sanitise_secrets    : 'db_password=hunter2hunter2 in the log line'
--- quoted json secret
  looks_like_credential: False
  redact_text         : '{"api_key": "[REDACTED]"}'
  redact_text==input  : False -> original WITHHELD
  sanitise_secrets    : '{"api_key": "abcdef1234567890"}'
--- provider token in multiline
  looks_like_credential: True
  redact_text         : 'line1\nAuthorization: Bearer [REDACTED]\nline3'
  redact_text==input  : False -> original WITHHELD
  sanitise_secrets    : '[REDACTED: withheld from the evidence bundle]'
```

The gate is now exactly right in kind: `redact_text(text) == text` is a **fixed point of the masker**, so
any content on which `form=masked` would differ from the original is withheld as `form=original`. All three
samples are withheld; the assignment-shaped ones would have leaked under rev-2's `looks_like_credential`
gate. The claim that `looks_like_credential` alone does not match the first two is correct
(`canonical.py:97-105` is a `any(p.search(text))` over `_LITERAL_CREDENTIAL_RES` only). **CLOSED.**

### Mandatory 2 — `sanitise_secrets` rationale — **CLOSED** (one wording nit)

Rev-3 text (§5, lines 352–356):

> "`sanitise_secrets` is deliberately **not** the masker: it is a *containment* search
> (`looks_like_credential` uses `p.search`) that replaces the **entire value** on any hit, so it cannot mask
> a secret inside a longer log line; the reason for choosing `redact_text` is its **broader pattern set**
> (it also masks `password=…`, `api_key: …` and other assignment shapes) and its in-place, line-preserving
> masking — not any claim about whole-string versus substring matching."

Re-verified against `canonical.py:97-105` (`return isinstance(text, str) and any(p.search(text) …)` — a
containment search) and `recorder.py:179-181` (`if isinstance(payload, str) and looks_like_credential(payload):
… return WITHHELD`, where `WITHHELD = "[REDACTED: withheld from the evidence bundle]"`). The multi-line
sample above confirms the whole value is replaced on a hit. Rev-3 no longer makes rev-2's false claim that
a multi-line log "would pass through unmasked", and it now states the two true differences (pattern set and
in-place masking). The clause "so it cannot mask a secret inside a longer log line" is loose shorthand for
"cannot mask it **in place**" — it does mask it, by discarding the whole value — but the immediately
preceding clause states the containment/whole-value fact correctly, so the mandate's substance is met.
**CLOSED**; the phrasing is an editorial nit, not a blocker.

### Mandatory 3 — `mask_version` as a masker revision — **CLOSED**

Rev-3 text (§5, lines 348–351):

> "`form=masked` (default) serves `canonical.redact_text(text)` — the **substring** masker used by the audit
> layer — with `mask_algorithm: "canonical.redact_text"` and
> `mask_version: canonical.REDACT_TEXT_VERSION` (a new module constant in §11), so `masked_sha256` is
> reproducible from `(algorithm, version, input)` and a change to the pattern table must bump the constant."

§11 (line 520): "`src/jasmine_core/canonical.py` | **additive only**: `REDACT_TEXT_VERSION = "1"` next to
`redact_text`, so `mask_version` is a masker revision rather than the package version (`__version__` is
`"0.1.0"` and does not track `canonical.py`)"; §11 (line 522): the new ADR 0015 "records … the
`REDACT_TEXT_VERSION` bump rule".

Re-verified: `grep -n "__version__" src/jasmine_core/__init__.py` → `11:__version__ = "0.1.0"`;
`hasattr(canonical, "REDACT_TEXT_VERSION")` → `False` today (it is proposed, not implemented, which is
expected for a proposal). Adding a module-level constant beside `redact_text` changes no signature and no
behaviour — it is genuinely additive and non-breaking. The contract states what must happen when the
pattern table changes (bump the constant) and commits to recording that rule in ADR 0015. **CLOSED.**
Residual (not a doc defect): the bump is process discipline with no mechanical check, and "`masked_sha256`
is computed over the served masked bytes" is implied by "reproducible from `(algorithm, version, input)`"
plus the envelope's `served_sha256`/`masked_sha256` fields, but is never stated in one sentence; see §3 D4.

### Mandatory 4 — chain-link mapping — **CLOSED** (mandate), with a new factual defect in the same block

Rev-3 text (§4.1, lines 261–285):

> "The taskbook's T06 chain is **Raw → Extraction → Resolution → Context → Agent → Tool → Evidence → State**.
> The recorder records **six** of those sources and has **no source at all** for two of them."
> `| Extraction | **none** — `recorder.SECTIONS` has no extraction field | always; `missing_reason: "the recorder records no Extraction section"` |`
> `| Resolution | **none** — `recorder.SECTIONS` has no resolution field | always; `missing_reason: "the recorder records no Resolution section"` |`
> "`case.interpretation` is a recorded `SECTIONS` member and is returned **verbatim as case data**. It is
> **not** equated with Resolution: ADR 0008 and the P2 taskbook keep Interpretation (a candidate) and
> Resolution (the resolved decision) distinct, and the recorder records only the former."
> `| Evidence | `case.evidence` (a case field, **not** one of the seven `SECTIONS`) | empty list |`

Re-verified `recorder.py:29-37`: `SECTIONS = ("user_raw", "agent_response", "tools", "state_before",
"state_after", "interpretation", "context")` — no `extraction`, no `resolution`; `interpretation` present.
Both Extraction and Resolution are now `none`; the rev-2 contradiction ("two links are not recorded" but
only one row marked `none`) is gone; §9 criterion 2 now says "no recorded source at all", not
"never-recorded"; "Seven recorded sections" is gone and `case.evidence` is correctly called a case field.
The mandate is closed.

However, the **"Verified on the real nine"** sentence in the same block is factually wrong about the real
records, and it papers over an unresolved contradiction in §1.1 — see **D1**, which I regard as
freeze-blocking.

### Mandatory 5 — error vocabulary — **CLOSED**, but the precedence table now overlaps

Rev-3 text (§7, lines 427–434, 465–471):

> "## 7. Errors and precedence (ADR 0003 shape; seven new code strings)"
> "Existing codes reused: `invalid_request` 400, `unauthenticated` 401, `forbidden_scope` 403,
> `unsupported_media_type` 415, `payload_too_large` 413, `memory_deployment_not_ready` 503. `NotFound(kind,
> id)` is used for the not-found kinds (`validation_run_not_found`, `artifact_not_found`); `NotFound` never
> emits a bare `not_found`. `schema_version_unsupported` and `database_busy` are **not** listed: they are
> raised by the migration/DB layers at Core construction or on a busy database, and no validation route can
> produce them, so the contract does not advertise them."
> "**The seven new code strings** are recorded in `docs/adr/0015-p7-validation-read-contract.md` …:
> `validation_not_configured` (503), `validation_index_too_large` (503), `validation_run_corrupt` (409),
> `validation_run_ambiguous` (409), `validation_manifest_corrupt` (409), `artifact_modified` (409),
> `original_withheld` (403), each with its trigger and the acceptance negative that exercises it."

Re-verified against `src/jasmine_core/errors.py`:

| advertised | class / code | `status` |
| --- | --- | --- |
| `400 invalid_request` | `InvalidRequest` (`errors.py:35-37`) | 400 |
| `401 unauthenticated` | `Unauthenticated` (`50-52`) | 401 |
| `403 forbidden_scope` | `ForbiddenScope` (`55-57`) | 403 |
| `413 payload_too_large` | `PayloadTooLarge` (`104-106`) | 413 |
| `415 unsupported_media_type` | `UnsupportedMediaType` (`109-111`) | 415 |
| `503 memory_deployment_not_ready` | `MemoryDeploymentNotReady` (`133-143`) | 503 |
| `404 {kind}_not_found` | `NotFound` (`70-77`): `self.code = f"{kind}_not_found"` | 404 |

`grep -oE 'code = "[a-z_]+"' src/jasmine_core/errors.py` still does **not** contain any
`validation_*` string, so the seven are genuinely new; `schema_version_unsupported` (503, `114-116`) and
`database_busy` (503, `126-130`) do exist as classes and are correctly *withheld* from the advertised
vocabulary because a read-only slice cannot raise them (`translate_lock_errors` is used by the store's
writes; the reader's `ProjectAccess.require` issues plain SELECTs). Every advertised code is reachable in
the precedence table: `validation_not_configured` rows 3–4, `validation_index_too_large` row 6,
`validation_run_corrupt`/`validation_run_ambiguous` row 10, `validation_manifest_corrupt` row 11,
`artifact_modified` row 13, `original_withheld` row 15, `payload_too_large` row 14,
`memory_deployment_not_ready` row 5. Heading and body now agree on **seven**, and the ADR forward
reference is consistent. **CLOSED** — but the table itself now has overlapping rows for `acl` mode; see
**D2**.

### Mandatory 6 — corrupt-row ordering and status — **CLOSED**

Rev-3 text (§2, lines 122–147):

> "**Statuses and their precedence.** A run-shaped file gets exactly one status:
> `corrupt` (unparseable, wrong `run_id`, or missing a required field) > `duplicate` (parseable, but another
> run-shaped file shares its stem) > `ok`. Corrupt wins because a file that cannot be parsed has no
> `run_id` to compare, so duplicate detection considers only parseable records. Duplicates are **both**
> listed, `corrupt_reason`/`duplicate_reason` names the sibling count, and detail refuses the stem with
> `409 validation_run_ambiguous`."
> "**Ordering (a strict total order).** This tuple is the single authority:
> ```
> (status_rank asc: ok=0, duplicate=1, corrupt=2,
>  created_at_key desc,
>  run_id_key desc,
>  relative_path asc)
> ```
> `created_at_key` is the recorded `created_at` for a parseable record and `""` for a corrupt one (so corrupt
> rows sort last and never interleave); `run_id_key` is the **filename stem** for every row (equal to the
> recorded `run_id` for parseable records). `relative_path` is unique by construction and is the final
> tie-break, so two files sharing a `created_at`/`run_id` can never make a page overlap or skip a row."

The rev-2 contradictions are gone: rank is now a per-status map (`ok=0, duplicate=1, corrupt=2`), there is
no "nulls last, i.e. all rank-1 rows …" non-sequitur, and the naming/ordering prose is deleted. A file that
is both corrupt and stem-duplicated is now defined (corrupt wins). The final `relative_path asc` on a
per-file-unique path makes the tuple a strict total order over any finite set — I verified the real nine are
all `ok` (`run_id == stem`, `stage`/`created_at` strings, `verdict ∈ {PASS,FAIL,BLOCKED}`, `cases` a list),
so the tuple is total over the nine real records plus a corrupt row plus a duplicate pair. **CLOSED**, with
two edge nits in §3 N-a/N-b.

### Mandatory 7 — manifest provenance grammar — **CLOSED**

Rev-3 text (§3, lines 244, 248–249):

> "`mode: real` **and** `projects` non-empty **and** `sources.recorder` is `{path, sha256: <64 hex>}` or
> `"<relative path>@<64 lowercase hex>"` → `real` / `"declared real with recorder provenance and project
> attribution"`"
> "The provenance forms are the **same digest length in both spellings** (64 hex, a SHA-256); the object form
> is preferred."

The rev-2 `"<path>@<40-hex>"` (SHA-1 length) is gone; both spellings are now 64 lowercase hex and the two
are explicitly declared the same digest. The §3 example (`"sha256": "<64 lowercase hex>"`, lines 190–194)
agrees. **CLOSED.**

### Mandatory 8 — no ambiguous bare `sha256` — **CLOSED**

Rev-3 text (§5, lines 338–343, 371–372):

> "**Artifact metadata** returns `{artifact_id, path (as recorded), bytes, declared_sha256, declared_bytes,
> integrity, media_type, forms: ["masked"] or ["masked","original"], digests: {original, masked},
> credential_shaped, content_scan}`. The two digests are **separate named fields**; there is no bare
> `sha256`."
> "The content route therefore returns a **JSON envelope**
> `{artifact_id, form, media_type, bytes, served_sha256, original_sha256, masked_sha256, mask_algorithm?,
> mask_version?, content, content_is_original}` — note **`served_sha256`**, never a bare `sha256`, so the
> digest's meaning is unambiguous"

The rev-2 envelope's bare `sha256` is replaced by `served_sha256`/`original_sha256`/`masked_sha256`, and
the metadata carries `declared_sha256` plus a `digests` map. Rev-2's N4 is closed. **CLOSED** — but
`content_is_original` (and the list row's `manifest_status`, see D4) is a newly named, never-defined field.

### Mandatory 9 — `record_sha256` and the dead `attribution.status` — **CLOSED**

Rev-3 text (§2, lines 166–167; §3, lines 251–255):

> "**`record_sha256` is the SHA-256 of the run record file's raw bytes** — exactly what `sha256sum` over the
> file returns — so the validator can recomputed it without knowing the reader's parser."
> "**Attribution.** `attribution.status ∈ {attributed, unattributed}` and `projects[]`, derived only from the
> effective manifest `projects`: non-empty ⇒ `attributed`; manifest present with `projects` empty or absent,
> no manifest, or a corrupt manifest ⇒ `unattributed` with
> `attribution.reason ∈ {declared_empty, manifest_absent, manifest_corrupt}`."

`record_sha256` is now recomputable by an independent validator (raw file bytes). The dead `unknown` value
is dropped from the enum. **CLOSED.** (Attribution is derived only from the manifest projects, consistent
with §1's refusal to derive it from nested `prj_` ids.)

### Mandatory 10 — root-present-but-unreadable — **CLOSED** (partial-tree gap: D6)

Rev-3 text (§2, lines 169–174; §7 rows 3–4):

> "**Unreadable subtrees never masquerade as emptiness.** An `OSError` while walking a subtree skips that
> subtree, increments `discovery.unreadable_entries`, and is named in `warnings[]` (≤20 samples). If
> `total == 0` **and** `unreadable_entries > 0`, the list is **not** a 200-with-empty-list: it is
> `503 validation_not_configured` with `details.reason: "root_unreadable"` and the counts, because a tree
> that could not be read must never be reported as a tree with nothing in it."
> `| 3 | root unset / missing / not a directory / symlinked / unreadable | 503 validation_not_configured (details.reason) |`
> `| 4 | root readable but nothing could be read (total == 0 with unreadable_entries > 0) | 503 validation_not_configured (details.reason: "root_unreadable") |`

There is now a defined, deterministic response for the unreadable case the rev-2 mandate named; the root
itself has a `root_unreadable` reason, subtrees are counted and warned, and the empty list is never
returned for an unreadable tree. **CLOSED** per the mandate. The residual gap is the *partially* readable
tree (`total > 0` **and** `unreadable_entries > 0`), which now returns 200 with a warning; see **D6**.

### Mandatory 11 — surface `synthetic` — **CLOSED**

Rev-3 text (§4.2, lines 317–320; list row §2 line 163; detail §5 line 332):

> "**Labelling is contract-enforced, not just process-enforced.** `synthetic` and `synthetic_note` are read
> from the record and surfaced in the list row and detail (`synthetic: false` when absent); a
> `synthetic: true` record is graded `simulated` and cannot be reported as real. Fixtures are *additionally*
> kept out of the operator's run root (§9), but the contract does not rely on that alone."

`grep -n "synthetic" web/P7-01-CONTRACT-PROPOSAL.md` shows the field in the list row (`synthetic,
synthetic_note?`), in the detail (`synthetic`, `synthetic_note?`), and in §4.2 rule 1. It is consistent with
§10's "no change to the recorder": the reader parses the record JSON generically and reads an extra key,
so no recorder change is needed to *read* it (`Run` has no `synthetic` field — `recorder.py:88-103` — and
`to_dict` uses `asdict`, so a recorder-written file cannot carry it; the fixture is therefore necessarily
hand-authored or post-edited). That is a fixture-provenance caveat, not a contract contradiction. **CLOSED**
(see nit N-c).

---

## 3. New / remaining defects

Ordered by severity. D1 and D2 are, in my judgment, freeze-blocking; D3–D6 are smaller but concrete.

### D1 (HIGH, internal contradiction + false record fact) — §1.1's `context` rule contradicts its own `present_kind` table for 30 of the 63 real cases, and §4.1's verification sentence is false

§1.1 (lines 76–90) defines:

> `| reason_only | the value is a **mapping** whose key set is a subset of `{reason, note, detail, not_applied_reason}` → **`present: false`** … |`
> `| value | anything else → `present: true` |`
> "`context` additionally requires **pack evidence**: `present: true` only when the value is a mapping
> holding at least one of `context_id, pack, sections, rendered, content, tokens, parts`, or is a non-empty
> list. A reason-only `context` is `present: false, present_kind: "reason_only"` — so the 27 real cases are
> *not* shown as having a context pack."

§4.1 (lines 283–285) then asserts:

> "Verified on the real nine: `extraction` and `resolution` missing 9/9, `context` missing 9/9 (every case is
> `reason_only` or `sentinel`), …"

My independent per-case structural analysis of the nine records (63 cases) using exactly the §1.1 rules:

```
PER-CASE context categories: {'reason_only': 27, 'other_mapping': 30, 'sentinel': 6}
total cases: 63
context mapping key-sets:
   27 ('reason',)
   9  ('db_path', 'processes', 'reason')
   9  ('reason', 'scratch_db')
   9  ('audit_entries', 'audit_rows_stored', 'reason')
   3  ('actor_id', 'core_url', 'db_path', 'host_id', 'reason', 'source_session_id')
```

* The parenthetical "(every case is `reason_only` or `sentinel`)" is **false**: 27 are reason-only, 6 are
  the sentinel, and **30 are neither**. The 30 are mappings whose key set is *not* a subset of
  `{reason, note, detail, not_applied_reason}` and which hold **none** of the pack keys
  (`{db_path, processes, reason}`, `{reason, scratch_db}`, `{audit_entries, audit_rows_stored, reason}`,
  `{actor_id, core_url, db_path, host_id, reason, source_session_id}`). This directly contradicts the record
  facts both earlier reviews verified (57 non-sentinel, 27 reason-only).
* For those 30 cases the two rules disagree about the same field: §1.1's table says `present_kind = value`
  and therefore `present: true`; the context pack rule says `present: false` (no pack key, not a list). The
  document also never says what `present_kind` is when the pack rule overrides — `value` with
  `present: false` is self-contradictory against the table's `value → present: true`.
* The contradiction propagates into §4.1's chain rule, which is the actual deliverable of acceptance
  criterion 2: `| Context | case.context (§1.1 pack rule) | present_kind != value |`. For those 30 cases the
  chain's Context link is therefore **undecidable from the text** — not-missing under the table, missing
  under the pack rule — so "missing Context shown as missing" cannot be validated on the real records.
* The *run-level* claim "`context` missing 9/9" is defensible under the pack rule (no case records any pack
  key), and §9 line 493 repeats it without the false parenthetical. The fix is nevertheless required
  because §1.1 is the normative rule the API and its tests must implement.

Concrete fix (one of): broaden `reason_only` for `context` to "a mapping with a `reason`/`note`/`detail`/
`not_applied_reason` key and no pack key", or add a fifth `present_kind` (e.g. `not_a_pack`) so `present`
and `present_kind` agree; and rewrite §4.1's parenthetical to name the three real shapes (27 reason-only,
6 sentinel, 30 non-pack mappings ⇒ no case records a pack ⇒ Context missing 9/9).

### D2 (MEDIUM-HIGH, precedence overlaps introduced by the unified `acl` 404) — rows 8/10 and 11/12 give two different answers for the same condition

§6 (lines 408–416) now says: "Any run an `acl`-mode caller may not read — a non-granted project, an
unattributed run without `validation:unattributed`, a corrupt/duplicate record (which fails closed, since it
has no readable manifest and is therefore treated as unattributed), or a run that does not exist — is
reported with the **same** status and code: `404 validation_run_not_found`." §7 rows 8–10:

> `| 8 | acl mode: the run is unreadable for any reason (not granted, unattributed without the scope, corrupt/duplicate fail-closed) | 404 validation_run_not_found |`
> `| 9 | unknown run id | 404 validation_run_not_found |`
> `| 10 | run record unparseable / wrong run_id / missing required field / duplicate stem | 409 validation_run_corrupt / 409 validation_run_ambiguous |`

In `acl` mode a corrupt/duplicate record matches **both** row 8 (404) and row 10 (409); row 10 carries no
mode qualifier, and rev-3 removed rev-2's "without `validation:unattributed`" qualifier, so the two rows
now collide even for a caller that legitimately holds `validation:unattributed`. Rows 11/12 overlap in the
same way and are not route-scoped either: "manifest breaks a rule" (row 11, 409
`validation_manifest_corrupt`) and "artifact not addressable (no/absent/**corrupt** manifest …)" (row 12,
404 `artifact_not_found`) both describe a corrupt manifest, and a corrupt run on the artifact route matches
rows 10 and 12. The table is "first matching row wins" only by implication; nothing in the document states
that rule or scopes rows by route, so a test author can implement either reading. This matters precisely
because §9 criterion 3 pairs 200/403/404 "on the same run and artifact".

Concrete fix: state "the first matching row wins", qualify row 10 as `single_user` mode, and add a
route column (or "manifest route"/"artifact route"/"run routes") so a corrupt manifest is 409 on the
manifest route and 404 `artifact_not_found` (`reason: manifest_corrupt`) on artifact routes.

### D3 (MEDIUM, §4.2) — the marker→grade mapping is not a function, and the run grade can exceed a mocked case

§4.2 (lines 289–309):

> "2. a **structural** per-case marker: `case.agent_response` is a mapping with a truthy
> `mock`/`fixture`/`simulated`/`synthetic` key ⇒ that grade, `grade_basis: "recorded in the case"`;"
> "**Section grade** = the section's own marker if the section value is a mapping with a truthy
> `mock`/`fixture`/`simulated`/`synthetic` key, otherwise the case grade."
> "**Run grade** = `simulated` when the effective bundle mode is `real` but any case grade is `mocked` or
> `simulated` …; else the effective bundle mode when it qualifies; else `unknown`."

`evidence_grade ∈ {real, component, simulated, mocked, unknown}` but the marker keys are
`{mock, fixture, simulated, synthetic}`; "⇒ that grade" is not defined for `mock` (= `mocked`?) or `fixture`
(=`mocked`? `simulated`?), and it is exactly the enum-membership mapping the grader must implement. The
same gap applies to the section rule. Separately, the "can never be upgraded" principle is only enforced for
`mode: real`: with effective mode `component` and a `mocked` case, the run grade is `component` — above the
case on the ladder — which is the inconsistency the ordering claim is meant to exclude. The per-section
claim ("a section can therefore never be graded more real than its case") does hold *provided* section
markers only ever yield `mocked`/`simulated`, which is the same undefined mapping.

Concrete fix: freeze `mock→mocked`, `fixture→mocked`, `simulated→simulated`, `synthetic→simulated` (or the
chosen mapping) for both rules, and extend the run-grade downgrade to any case grade below the effective
mode (or define run grade as the minimum of the effective mode and the worst case grade).

### D4 (MEDIUM) — two response fields are named but never defined

`manifest_status` appears exactly once — in the list row (§2 line 163–164): `evidence_grade, synthetic,
synthetic_note?, manifest_status, record_status, …` — and is never defined anywhere in the document (the
manifest response uses `status` with `{frozen, absent, corrupt}` in §3). Likewise `content_is_original`
appears once, in the content envelope (§5 line 372), with no definition. For a proposal that will be
derived into `web/contracts/p7-validation-read.v1.json` and tested against the real records, a named field
with no value set or derivation rule is not testable. Concrete fix: define both (or drop `manifest_status`
in favour of the §3 `status` values and state that `content_is_original == (form == "original")`).

### D5 (MEDIUM-LOW, §2 pagination) — the filter justification is false for duplicate rows

§2 (lines 149–153):

> "`total` is the number of discovered run-shaped records that **pass the filters**: with no filter that
> includes corrupt and duplicate rows; with a filter it counts only matching rows, because a corrupt or
> duplicate row has none of the filtered fields and therefore never matches."

Corrupt rows are unparseable and have no `verdict`/`stage`, so they cannot match — true. But **duplicate
rows are parseable by definition** (`duplicate` = "parseable, but another run-shaped file shares its stem",
line 123) and therefore *do* carry `verdict`/`stage`; a `stage=P0` or `verdict=PASS` filter can match them.
The sentence "the filtered fields … never match" therefore contradicts the status definition and leaves it
undecided whether a matching duplicate is counted in `total` and served. Concrete fix: state that a
duplicate row participates in filters on its parsed fields exactly like an `ok` row, and delete the
"therefore never matches" clause (or define duplicates as filter-exempt and say so).

### D6 (LOW-MEDIUM, §2/§7) — the partial-unreadable-tree rule is a 200 with a knowingly incomplete index

The **"unreadable ⇒ 503"** rule fires only when `total == 0 && unreadable_entries > 0` (§2 lines 170–173;
§7 row 4). When `total > 0` **and** `unreadable_entries > 0` the response is a 200 whose `runs[]`/`total`
silently exclude every record under the unreadable subtree; only `discovery.unreadable_entries` and a
≤20-sample `warnings[]` reveal it. Acceptance criterion 5 claims "no duplicates or omissions" (§9 line 496
even dry-runs "an unreadable subtree"), and criterion 6 compares served values to the records. A paginating
client that ignores `warnings[]` sees a complete-looking index. Concrete fix: either fail closed
(503/`root_unreadable` whenever `unreadable_entries > 0`) or state explicitly that `warnings[]` is a
load-bearing incompleteness signal, name the exact expected outcome for the §9 dry-run case, and add a
top-level completeness indicator.

### D7 (LOW / process) — the "dry-run" claims are not backed by any retained artifact

§2 line 99–100 ("This walk was dry-run against the real tree and against a synthetic adversarial tree"),
§3 line 209–210 ("verified by dry run: `../`, absolute, `C:`, UNC and URL all refuse the manifest with the
offending entry named"), §5 line 365–366 ("verified by dry run"), and §9 line 486–488 ("Both the
real-record rules and the adversarial rules below were **dry-run** read-only before implementation … a
synthetic tree exercises every negative") assert verification work whose evidence is not in the worktree or
the source repo. I independently reproduced the *real-tree* claims (9 records / 288 scanned entries, the
masker samples, all nine run-shaped), so those hold; the *synthetic adversarial tree* claims are not
reproducible from anything retained. Either commit the dry-run scripts/outputs under
`validation-quarantine/` (the commit message already claims they are there) or soften the claims to
"specified; exercised by the tests in §11". This is not a contract defect, but it is a review-evidence gap.

### Minor nits (non-blocking)

* **N-a (§2 ordering).** `created_at_key` is `""` for corrupt rows "so corrupt rows sort last and never
  interleave". A parseable/`ok` row with `created_at: ""` (the run-shaped rule requires a string, not a
  non-empty string) would share the `""` key and interleave with corrupt rows. The tuple is still a total
  order (the `relative_path` tie-break saves it), so the claim, not the order, is inaccurate.
* **N-b (§2 status precedence).** "duplicate detection considers only parseable records" conflicts with
  `duplicate = parseable, but another run-shaped file shares its stem`: for a stem shared by one corrupt and
  one parseable file, the parseable file has no parseable sibling, yet the stem *is* shared, so whether it is
  `ok` or `duplicate` — and whether detail returns 409 — is undefined. Also, "a file that cannot be parsed
  has no `run_id` to compare" is false for the `wrong run_id`/`missing field` corrupt sub-cases, which are
  parseable.
* **N-c (§4.2/§10).** "`synthetic` … read from the record" cannot be produced by `recorder.py`
  (`Run`/`Case` have no such field and `to_dict` is `asdict`), so the FAIL fixture must be hand-authored or
  post-edited; the document should say so to keep §9 ("every fixture record labels itself") and §10 ("no
  change to the recorder") visibly consistent.
* **N-d (§1).** The bullet "`user_raw`, `agent_response`, `interpretation` hold either a value or the
  sentinel" omits `state_before`/`state_after`, which also hold the sentinel in the real records (6 cases
  each; the §1.1 table handles them generically, so this is completeness of exposition, not an error).
* **N-e (§3/§7).** §3's manifest response defines `status: frozen|absent|corrupt` with `rule?`, but §7 row
  11 maps a corrupt manifest to `409 validation_manifest_corrupt`; the status of the 409 body is not defined
  (error envelope vs manifest-shaped body). Also `413 payload_too_large` for a server-side stored artifact
  rather than a request body is defensible but unconventional; it is at least applied consistently in §7
  row 14 and §8.

---

## 4. Residual risk register (what a validator must still exercise at runtime)

1. **The §1.1 context rule as implemented.** Until D1 is fixed, a validator cannot know whether the 30
   non-pack context mappings are `present`; once fixed, assert the exact per-case `present_kind`/`present`
   over all 63 real cases.
2. **The unified `acl` 404.** Exercise every acl refusal (non-granted project, unattributed without the
   scope, corrupt record, duplicate stem, unknown id) against one another and against the `409`s; assert
   the response bytes are indistinguishable between "absent" and "not granted" (D2).
3. **`REDACT_TEXT_VERSION` reproducibility.** `masked_sha256` must be recomputable from
   `(canonical.redact_text, REDACT_TEXT_VERSION, bytes)`; the bump rule is process-only, so a test must pin
   the value and fail if the pattern table changes without a bump.
4. **The mirrored walk vs `hash_workspace_file`.** §3 mandates a local mirror that reads once, hashes the
   buffer and serves that buffer; assert the digests agree and that a swap between stat and read cannot make
   the served bytes differ from the verified digest (`hash_workspace_file -> tuple[str, int]` confirmed).
5. **`ProjectAccess` reuse in the validation reader.** The migration `m0011_memory.py` and
   `memory_project_actors` exist and `memory/api.py` uses `self.conn = core.conn` +
   `ProjectAccess(self.conn, mode=…)`, so the reuse is feasible; assert the behaviour when the connection is
   read-only/busy and when the P4 migration is absent at runtime.
6. **The synthetic-fixture production path.** Confirm the fixture can carry `synthetic: true` without a
   recorder change (hand-authored/post-edited), and that a `synthetic: true` record is graded `simulated`
   and never reported real.
7. **Partial unreadability.** Decide and test the `total > 0 && unreadable_entries > 0` outcome (D6).
8. **The grade ladder.** The nine real records must all grade `unknown` (no manifest, no marker, no matching
   executor — I verified none of the three indicators fires); a synthetic mock/fixture record must produce
   the frozen grade from D3.
9. **ADR 0015.** Still a forward commitment (`docs/adr/` ends at `0014-p4-memory-and-durable-jobs.md`); it
   must exist, naming the seven codes and the `REDACT_TEXT_VERSION` bump rule, before the contract is frozen.
10. **The proxy handoff.** `web/server/core-paths.json` and `web/server/core-proxy.ts` exist with
    `P6_ENABLE_P3_READS`/`P6_ENABLE_P4_READS` and `p3_pending_paths`/`p4_pending_paths`; P6-03c's
    `P6_ENABLE_P7_READS`/`p7_pending_paths` is a handoff, not this slice's change.
11. **Provenance of the nine records as "real".** Unchanged from the earlier reviews: the files label
    themselves, but nothing in them proves a genuine conversation produced them.
12. **Acceptance criterion 1 remains BLOCKED.** No real P7-format FAIL run exists (0 FAIL cases, 0
    `failure_class`, 0 `failure_output`; re-verified); the unblock action is named but not executed.

---

## 5. Verdict

**APPROVE WITH NAMED MANDATORY CHANGES.**

All eleven rev-2 mandatory changes are **closed** (mandatory 1 verified empirically at runtime; 3, 5, 6, 9
verified against `canonical.py`, `errors.py`, the real records and the ordering tuple; 4, 7, 8, 10, 11
verified textually against the files), the baseline is clean, and every record fact re-verified here
(9 records, PASS 3 / BLOCKED 6, cases PASS 57 / BLOCKED 6, 0 FAIL, 0 `failure_class`/`failure_output`,
21 evidence entries / 12 absolute, 57 non-sentinel contexts / 27 reason-only, 288 scanned walk entries,
agent present only in the three PASS runs, no marker/executor firing rule 3) still holds. This is not
`REJECT`: the architecture is sound and the remaining work is targeted.

It is not `APPROVE (freeze as-is)` because rev-3 introduced or left two concrete contradictions that make
its own acceptance criteria undecidable on the real records — above all **D1**, which affects 30 of the 63
real cases and directly governs criterion 2's "missing Context shown as missing".

**Mandatory before the contract is frozen** (ordered by severity; each is concrete to implement):

1. **Fix the `context` presence contradiction and the false real-record sentence (§1.1, §4.1, §9).** Give
   the 30 non-pack mappings a defined `present_kind` that agrees with `present: false` (broaden
   `reason_only` for `context`, or add a fifth kind), and replace "(every case is `reason_only` or
   sentinel)" with the true shapes: 27 reason-only, 6 sentinel, 30 non-pack mappings, no case with a pack.
   (D1)
2. **Make the §7 precedence table single-valued and route-scoped.** State "first matching row wins";
   qualify row 10 as `single_user` mode (or state the intended acl behaviour for a caller holding
   `validation:unattributed`), and scope rows 10/11/12 by route so a corrupt manifest is 409 on the
   manifest route and 404 `artifact_not_found` on artifact routes. (D2)
3. **Freeze the marker→grade mapping and the run-grade downgrade (§4.2).** Define
   `mock/fixture/simulated/synthetic → mocked|simulated` for the case and section rules, and make the run
   grade fail below a mocked/simulated case whenever the effective mode is `component` too. (D3)
4. **Define `manifest_status` and `content_is_original`, or remove them (§2, §5).** (D4)
5. **Fix the duplicate-row filter statement (§2).** Duplicates are parseable and can match
   `verdict`/`stage` filters; say so and delete the "never matches" clause. (D5)
6. **Decide the partial-unreadable-tree semantics (§2, §7, §9).** Either fail closed whenever
   `unreadable_entries > 0`, or state that `warnings[]` is a load-bearing incompleteness signal and give
   the expected §9 dry-run outcome. (D6)

**Recommended, not freeze-blocking:** retain the dry-run evidence in `validation-quarantine/` or soften the
"dry-run" claims (D7); address the ordering/status edge nits N-a/N-b and the fixture-provenance nit N-c.

---

## Appendix A — exact commands run (verbatim key output)

Run from `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01` unless noted.

```bash
# --- baseline integrity ---
git rev-parse HEAD
# c8fd597ac6f6617072a1772595c54ec141d9c01b
git branch --show-current
# p7-01-validation-read
git status --porcelain
# ?? validation-quarantine/
git diff --cached --stat
# (empty)
git diff --stat f397213 c8fd597
#  web/P7-01-CONTRACT-PROPOSAL.md | 504 +++++++++++++++++++++++++----------------
#  1 file changed, 312 insertions(+), 192 deletions(-)
git diff --stat f397213 c8fd597 -- . ':(exclude)web/P7-01-CONTRACT-PROPOSAL.md'
# (empty)
git diff --quiet f397213 c8fd597 -- web/P7-01-CONTRACT-PROPOSAL.md; echo $?
# exit=1
git show --stat c8fd597
#  web/P7-01-CONTRACT-PROPOSAL.md | 504 +++++++++++++++++++++++++++----------------
git log -1 --format='%H %ci' c8fd597   # c8fd597… 2026-10-08 07:00:07 -0700
git log -1 --format='%H %ci' f397213   # f397213… 2026-10-08 06:54:21 -0700
find validation-quarantine -type f
# validation-quarantine/REVIEW-p7-01-proposal/findings.md
# validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-findings.md
git status --porcelain=v1 --untracked-files=all
# ?? validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-findings.md
# ?? validation-quarantine/REVIEW-p7-01-proposal/findings.md
```

```bash
# --- real records (source repo, read-only) ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
find validation-runs -name 'run-*.json' | wc -l        # 9
find validation-runs -name 'manifest.json' | wc -l     # 0
# python3 analysis:
#   stages: {'P0': 9}; run verdicts {'PASS': 3, 'BLOCKED': 6};
#   case verdicts {'PASS': 57, 'BLOCKED': 6}; 0 failure_class; 0 failure_output;
#   evidence entries 21, absolute 12;
#   PER-CASE context categories {'reason_only': 27, 'other_mapping': 30, 'sentinel': 6} (total 63);
#   context key-sets: 27 ('reason',), 9 ('db_path','processes','reason'),
#     9 ('reason','scratch_db'), 9 ('audit_entries','audit_rows_stored','reason'),
#     3 ('actor_id','core_url','db_path','host_id','reason','source_session_id');
#   agent present in exactly the 3 PASS runs (1 case each), 0 in the 6 BLOCKED;
#   runs with raw/tool/state/evidence present: 9/9;
#   all nine satisfy the §2 run-shaped rule (run_id == stem, stage, created_at, verdict, cases)
# walk simulation (skip dot/__pycache__, no symlink follow, depth <= 4):
#   scanned 288 files 217 dirs 65 links 0 run_shaped 9
# run.executor values: '/root/sol_validation_finish', 'gpt-6-sol' (x7), 'sol_p1_impl' -> rule3_match False for all
# no real case.agent_response mapping carries mock/fixture/simulated/synthetic; no record has a 'synthetic' key
```

```bash
# --- empirical masker / scan semantics (worktree src on PYTHONPATH, read-only import) ---
PYTHONPATH="$PWD/src" python3 - <<'PY'
from jasmine_core import canonical
from jasmine_core.validation import recorder
for name, s in {
 "bare password": "db_password=hunter2hunter2 in the log line",
 "quoted json secret": '{"api_key": "abcdef1234567890"}',
 "provider token in multiline": "line1\nAuthorization: Bearer sk-abcdefgh12345678\nline3",
}.items():
    print(name, canonical.looks_like_credential(s),
          repr(canonical.redact_text(s)), canonical.redact_text(s) == s,
          repr(recorder.sanitise_secrets(s)))
print("has REDACT_TEXT_VERSION:", hasattr(canonical, "REDACT_TEXT_VERSION"))
PY
# bare password           -> False | 'db_password=[REDACTED] in the log line' | False (WITHHELD) | unchanged
# quoted json secret      -> False | '{"api_key": "[REDACTED]"}'              | False (WITHHELD) | unchanged
# provider token     -> True  | 'line1\nAuthorization: Bearer [REDACTED]\nline3' | False (WITHHELD) | whole value withheld
# has REDACT_TEXT_VERSION: False
```

```bash
# --- code facts (p7-01 worktree) ---
sed -n '80,140p' src/jasmine_core/canonical.py
# looks_like_credential -> any(p.search(text) for p in _LITERAL_CREDENTIAL_RES)
# redact_text -> PEM/Bearer/Basic/JWT/provider/_SK/_QUOTED_ASSIGNMENT/_BARE_ASSIGNMENT substitutions
grep -n "class PayloadTooLarge" -A3 src/jasmine_core/errors.py     # 413 payload_too_large
grep -n "class MemoryDeploymentNotReady" -A11 src/jasmine_core/errors.py  # 503 memory_deployment_not_ready
grep -n "class UnsupportedMediaType" -A3 src/jasmine_core/errors.py # 415
sed -n '70,78p' src/jasmine_core/errors.py   # NotFound.code = f"{kind}_not_found"; status 404
sed -n '20,26p' src/jasmine_core/auth.py     # SCOPES tuple has no validation:* scopes
sed -n '75,95p' src/jasmine_core/auth.py     # existing ForbiddenActorKind guards
grep -n "validation:read\|validation:unattributed" --include=*.py src/   # (none)
grep -n "def require_mode_ready" -A18 src/jasmine_core/memory/scope.py   # raises MemoryDeploymentNotReady
sed -n '177,190p' src/jasmine_core/memory/scope.py   # ProjectAccess(conn, mode=...), require(project_id=, actor_id=)
grep -n "class Run" -A30 src/jasmine_core/validation/recorder.py   # no synthetic field; to_dict = asdict
sed -n '28,50p' src/jasmine_core/validation/recorder.py   # SECTIONS has interpretation, not extraction/resolution
sed -n '165,195p' src/jasmine_core/validation/recorder.py # sanitise_secrets: containment hit -> WITHHELD whole value
grep -n "__version__" src/jasmine_core/__init__.py        # 11:__version__ = "0.1.0"
grep -n "def hash_workspace_file" -A5 src/jasmine_core/fingerprint.py  # -> tuple[str, int]
sed -n '153,168p' src/jasmine_core/api/server.py          # _respond: fixed JSON content-type, drops handler's
ls docs/adr/ | tail -2                                     # 0013…, 0014-p4-memory-and-durable-jobs.md (no 0015)
grep -rln "memory_project_actors" src/ docs/               # m0011_memory.py, memory/api.py, scope.py, ADR 0014
grep -n "P6_ENABLE_P3_READS\|P6_ENABLE_P4_READS\|p3_pending_paths\|p4_pending_paths" web/server/core-proxy.ts
# 38: p3ReadsEnabled; 39: p4ReadsEnabled; 42-43: p3_pending_paths?/p4_pending_paths?; 47-48: spread into allowlist
grep -n "manifest_status\|content_is_original" web/P7-01-CONTRACT-PROPOSAL.md
# 164: manifest_status (list row only — never defined); 372: content_is_original (envelope only — never defined)
grep -n "9/9" web/P7-01-CONTRACT-PROPOSAL.md
# 283: "context missing 9/9 (every case is reason_only or sentinel)"; 285: "raw/tool/evidence/state present 9/9"
```

```bash
# --- rev2 vs rev3 comparison (error table and ordering) ---
git show f397213:web/P7-01-CONTRACT-PROPOSAL.md > /tmp/rev2.md
awk '/^## 7\./,/^## 8\./' /tmp/rev2.md     # rev2 rows 6/8 (acl corrupt) and 12 (cap -> 409), heading "five"
awk '/Ordering/,/Pagination/' /tmp/rev2.md # rev2 rank ok=0, corrupt=1, duplicate=1, nulls-last prose
```

## Appendix B — files written

Only this file was written:

`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-2-findings.md`

No tracked file was modified. Nothing was staged, committed or pushed; HEAD remains
`c8fd597ac6f6617072a1772595c54ec141d9c01b`. `git status --porcelain=v1 --untracked-files=all` after this
review shows exactly the three reviews in `validation-quarantine/REVIEW-p7-01-proposal/` and nothing else.
The one scratch file used (`/tmp/rev2.md`, holding the rev-2 proposal for the §2/§7 comparison) was removed;
all other reads were read-only.
