# Fourth-pass delta re-review — `web/P7-01-CONTRACT-PROPOSAL.md` revision 4

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent, delegated independent
  reviewer). This is a new agent context; it is not the author of the proposal or of any earlier review.
* **Date of review run:** 2026-10-08 (host `Thu Oct  8 07:10:16 PDT 2026`; UTC `2026-10-08T14:10:16Z`).
* **Commits reviewed** (all in the p7-01 worktree, branch `p7-01-validation-read`):
  * rev 1: `3ebe46ccb3e260c4ec4e8a47f8f812498bc6c8c4`
  * rev 2: `f3972137aeb996e0c3f0f6da0884c3353cd18f58`
  * rev 3 (the delta baseline): `c8fd597ac6f6617072a1772595c54ec141d9c01b`
  * rev 4 (the artifact under review / worktree HEAD): `1b9791a125617deb85b84a565ccb61e4d95fe145` —
    `P7-01 phase 1 rev 4: close the third review's D1-D6 and retain the dry-run evidence`
  * proposal baseline: `integration/p6-03-realcore` @ `039f5a3e7800704e5f0478d01992f00eeb2ca3f1`
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`.
* **Authority / checklist:** `validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-2-findings.md` (731
  lines, read in full — its §3 D1–D7 and its §5 mandatory list are the checklist); the two earlier reviews
  were read for context. Real records were read read-only from the shared source repo
  `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1` (HEAD
  `7016dabb8926d6bd6599f1f56b8f39cbda253ebf`, matching the retained evidence README).
* **Writes:** only this file (see Appendix B). No tracked file was modified, nothing was staged, committed or
  pushed; the worktree was clean before and after every command.

---

## 1. Baseline integrity

Verbatim, run from the p7-01 worktree:

```
$ git rev-parse HEAD
1b9791a125617deb85b84a565ccb61e4d95fe145

$ git branch --show-current
p7-01-validation-read

$ git status --porcelain
(empty — clean; no untracked leftovers)

$ git diff --cached --stat
(empty)

$ git diff --stat c8fd597 1b9791a
 validation-quarantine/DRYRUN-p7-01/README.md       |  52 ++
 .../DRYRUN-p7-01/p7-fixture-dryrun.py              | 236 +++++++
 .../DRYRUN-p7-01/p7-fixture-dryrun.txt             |  34 +
 .../DRYRUN-p7-01/p7-real-records-dryrun.py         | 130 ++++
 .../DRYRUN-p7-01/p7-real-records-dryrun.txt        |  22 +
 .../REVIEW-p7-01-proposal/RE-REVIEW-2-findings.md  | 731 +++++++++++++++++++++
 .../REVIEW-p7-01-proposal/RE-REVIEW-findings.md    | 658 +++++++++++++++++++++
 .../REVIEW-p7-01-proposal/findings.md              | 493 ++++++++++++++
 web/P7-01-CONTRACT-PROPOSAL.md                     | 166 +++--
 9 files changed, 2464 insertions(+), 58 deletions(-)
```

**Assessment.**
* HEAD is `1b9791a125617deb85b84a565ccb61e4d95fe145` on branch `p7-01-validation-read`: **CONFIRMED**.
* `git status --porcelain` is empty and nothing is staged: **CONFIRMED** (nothing untracked left over).
* The changed set is `web/P7-01-CONTRACT-PROPOSAL.md` (**M**, 166 lines changed) plus two newly tracked
  directories: `validation-quarantine/DRYRUN-p7-01/` (README + the two scripts + the two captured `.txt`
  outputs — exactly the five files D7 asked to retain) and `validation-quarantine/REVIEW-p7-01-proposal/`
  (the three earlier review files, which were untracked at rev 3 and are now committed).
* **Deviation from the stated expectation:** the task said the tracked change "should be
  `web/P7-01-CONTRACT-PROPOSAL.md` plus the newly tracked `validation-quarantine/DRYRUN-p7-01/` evidence
  directory". The commit additionally tracks the three review documents (`findings.md`,
  `RE-REVIEW-findings.md`, `RE-REVIEW-2-findings.md`). This is not wrong — committing the review record is
  consistent with the revision-history table — but it is an extra tracked change the baseline summary
  should name. `git status --porcelain=v1 --untracked-files=all` is empty, so nothing remains untracked.

---

## 2. Closure table for D1–D7

| # | Defect (rev-3 §3) | Verdict | Evidence in rev 4 |
| --- | --- | --- | --- |
| D1 | `context` presence contradiction + false record sentence | **CLOSED** | §1.1 (89–97), §4.1 (285, 298–301), §9 criterion 2 (539); recomputed 6/27/30/0 |
| D2 | precedence overlap (rows 8 vs 10, 11 vs 12) | **CLOSED** (as stated) — but the new route-scoping leaves route `L` undefined in `acl` mode (new defect N1) | §7 (471–504) |
| D3 | marker→grade not a function; run grade can exceed a mocked case | **PARTIALLY CLOSED** — marker table and run-grade min are fixed, but the section-grade invariant is now false (`component` is a section marker) | §4.2 (305–342) |
| D4 | `manifest_status` and `content_is_original` named but undefined | **PARTIALLY CLOSED** — `content_is_original` is defined; `manifest_status` is still undefined | §5 (405) defined; §2 (171) still undefined |
| D5 | duplicate-row filter justification false | **CLOSED** for duplicates; the replacement sentence is still false for parseable-corrupt rows | §2 (156–160) |
| D6 | partial-unreadable tree returned a 200 with a knowingly incomplete index | **CLOSED** (`unreadable_entries > 0` ⇒ 503 `index_incomplete`); `complete` is asserted in prose but missing from the response shape | §2 (177–186), §7 row 4 (484), §9 criterion 5 (542) |
| D7 | dry-run claims backed by no retained artifact | **CLOSED** — the evidence is retained and re-runs verbatim; several fidelity/consistency caveats remain (advisory) | `validation-quarantine/DRYRUN-p7-01/` |

### D1 (the important one) — **CLOSED**

Rev-4 §1.1 (lines 89–97) now separates the two questions:

> "**`present_kind` describes whether the section was *recorded*; the chain link is stricter.** For
> `context` only, the section additionally carries `context_pack_present: bool`, true iff the value is a
> mapping holding at least one of `context_id, pack, sections, rendered, content, tokens, parts`, or is a
> non-empty list. **The chain link `context` (§4.1) requires `present ∧ context_pack_present`**; a section
> can be recorded without being a pack. … of the 63 cases, 6 `context` values are the sentinel, 27 are
> `{"reason": …}`-only, and **30 are recorded run details** (`{"db_path": …, "processes": […]}`) —
> genuinely recorded, but containing no context pack. No real case has a pack, so the Context link is
> missing for three distinct, nameable reasons rather than one, and the 30 are never counted as a pack."

I recomputed the breakdown from the nine real records with the rev-4 rules as written (63 cases):

```
total cases: 63
context categories: {'reason_only': 27, 'recorded_no_pack': 30, 'sentinel': 6}
context key-sets:
   27 ('reason',)
   9  ('db_path', 'processes', 'reason')
   9  ('reason', 'scratch_db')
   9  ('audit_entries', 'audit_rows_stored', 'reason')
   3  ('actor_id', 'core_url', 'db_path', 'host_id', 'reason', 'source_session_id')
cases where chain context present (present and pack): 0
```

* The three-form claim is **exactly right**: 6 sentinel, 27 `{"reason": …}`-only, 30 non-pack mappings
  (key sets listed above), **0 with a pack**. The 30 split into four sub-shapes, which the document's single
  `{"db_path": …, "processes": […]}` example does not enumerate; that is exposition, not an error.
* `present_kind = value` with `present = true` and `context_pack_present = false` is now internally
  consistent: `present` answers "was it recorded", and the chain link answers "is it a pack". The rev-3
  complaint ("`value` with `present:false` is self-contradictory") no longer exists.
* §4.1 (line 285) now uses the pack rule for the chain: `| Context | case.context — recorded **and**
  `context_pack_present` (§1.1) | not a context pack (three named reasons) |`; and its "Verified on the real
  nine" sentence (298–301) says **"`context` missing 9/9 — every one of the 63 cases lacks a context
  pack", in three distinct forms (6 sentinel, 27 `{"reason": …}`-only, 30 recorded run details)**. §9
  criterion 2 (539) repeats the same three forms. §4.1 and §9 are therefore consistent with §1.1 and with
  the data.
* The retained real-records dry-run agrees with my computation: its output sums to `reason_only` 3×9 = 27,
  `sentinel` 1×6 = 6 and `recorded_no_pack` 30, and it prints "context link missing for all N cases" for
  every run. **The retained output is correct.**
* **Caveat on the retained *script* (not on the contract).** `p7-real-records-dryrun.py` does **not** fully
  implement the rev-4 rule: `chain()` line 67 computes `"resolution": sec["interpretation"]["present"]`,
  i.e. it maps Resolution from `case.interpretation`, whereas rev 4 says Resolution has **no recorded source
  at all** and is always missing. It produces the right answer on this data only because `interpretation` is
  the sentinel in all 63 cases (verified: `interpretation forms across 63 cases: {'sentinel': 63}`). The
  script also treats an **empty list** as `present: true` / `present_kind: value`, while §1.1 says an empty
  list is `absent` ("recorded as empty"). Both are evidence-fidelity gaps, listed as advisory A3 below.
  D1 itself (the normative rule) is closed. The chain-link `missing_reason` string for the 30 recorded
  detail mappings is still not named anywhere (advisory A2).

**Verdict: D1 CLOSED.**

### D2 — **CLOSED as stated**, with a new route gap (N1)

Rev-4 §7 (471–477) states the rule rev 3 lacked:

> "**Precedence is evaluated in three stages, and within the stage that applies to a route the *first
> matching row wins*.** The table is therefore route-scoped … Stages: **A** authentication and scope
> (rows 1–2); **B** deployment and request validity (rows 3–7); **C** resource resolution and content
> (rows 8–16)."

and lines 498–504:

> "rows 10–11 are `single_user`-only because in `acl` mode a record that cannot be read has already been
> answered by row 8 — the `409` family is never reachable in `acl` mode … Rows 12–16 are the only rows
> that can fire on `A`/`C`, so a corrupt manifest yields `409 validation_manifest_corrupt` from `M`
> (row 11) and `404 artifact_not_found` from `A`/`C` (row 12, with `reason: "manifest_corrupt"`) — the two
> routes answer differently on purpose."

* Three stages, route column, "first matching row wins": **present and unambiguous**. Rows 10 and 11 are
  both marked "`single_user` mode only": **CONFIRMED** (lines 490–491).
* The rev-3 collisions are gone. Row 8 is `acl`-only and row 10 is `single_user`-only, so in `acl` mode a
  corrupt/duplicate record is answered by row 8 (`404`) and the `409`s are unreachable; in `single_user`
  mode row 10 answers (`409`). Rows 11 (M) and 12 (A/C) are separated by route. No two rows in the same
  stage and on the same route now give two *different* answers for the same condition: where rows can both
  match (e.g. row 13 `409 artifact_modified` and row 14 `413 payload_too_large` for a file that is both
  oversized and byte-flipped), "first matching row wins" makes the answer deterministic.
* **Residual / new defect N1 (see §3):** the route column scopes rows 8–10 to `D M A C` and never gives the
  list route `L` an `acl` behaviour. §6 does not say whether list rows are grant-filtered. That is a
  security-relevant undefined answer, not an overlap.
* Minor: row 10's Response cell still names **two** codes (`validation_run_corrupt / validation_run_ambiguous`)
  for two sub-situations, so the row is not single-valued in the strictest reading, although the
  sub-situation determines the code; and the sentence "Rows 12–16 are the only rows that can fire on `A`/`C`"
  is literally false (rows 8, 9 and 10 also carry route `A C`), even though its conclusion is correct.

**Verdict: D2 CLOSED (rows no longer collide with different answers); new list-route defect N1 raised.**

### D3 — **PARTIALLY CLOSED** (section-grade invariant is now false)

Rev-4 §4.2 fixes the two rev-3 complaints and introduces a third:

* The enum is now `{real, component, simulated, unknown}` (line 305), ordered `real > component > simulated
  > unknown` (307) — the invented `mocked` is gone and the set matches the four modes the rev-1 review
  grounded in the taskbook (real/component/simulated/unknown). The P7 taskbook itself is not in this
  checkout; the P6 taskbook §7 negative only requires "证据等级明确" (`docs/Jasmine-Core-V1-P6-阶段任务书.md:141`).
* The marker table is a **total function** (309–318): `synthetic/simulated/fixture/mock → simulated`,
  `component → component`, "any other key is not a marker". This closes the rev-3 "`⇒ that grade`" gap.
* The run-grade rule (338–342) is now a **min**: "the least trustworthy of the effective bundle mode and
  every case grade … so a `component` bundle mode with one `simulated` case yields `simulated`, not
  `component`". Checked against the trust order `real > component > simulated > unknown`: min of
  {`component`, `simulated`} = `simulated` — **yes**, `component` + a `simulated` case really yields
  `simulated`. Also a mode-`unknown` bundle cannot be improved by a case grade. **Correct.**
* **The section rule is now wrong.** Line 334–336:

  > "**Section grade** = the section's own marker's grade if that section's value is a mapping carrying a
  > marker, otherwise the case grade. Because a marker maps into the same four-value enum, a section grade
  > can never exceed its case grade."

  The marker table includes `component → component`, and the section rule applies "a marker" generically.
  Therefore a section whose mapping carries a truthy `component` key is graded `component`, while its case
  grade can be `simulated` (e.g. `run.synthetic == true`, case rule 1) or `unknown` (no manifest, no
  `agent_response` marker, no executor match) — and `component` is **more** trustworthy than both. The
  "because they map into the same enum" argument is a non-sequitur: sharing an enum does not bound the
  value above. So a section grade **can** exceed its case grade, exactly the invariant the text claims can
  never fail. Real records do not trigger it (no real section carries any marker key), but the rule is
  normative. Fix: either restrict the section-marker set to `{mock, fixture, simulated, synthetic}` (drop
  `component`), or define `section grade = the least trustworthy of the case grade and the section's marker
  grade` (the same min used for the run grade).

**Verdict: D3 PARTIALLY CLOSED.** The enum, the marker table and the run grade are fixed; the
section-grade invariant is false as written.

### D4 — **PARTIALLY CLOSED** (`manifest_status` is still undefined)

* `content_is_original` **is now defined** (§5 line 405): "`content_is_original` is `form == "original"`,
  `true` only when the original was actually served (so it is `false` for every masked response)". Closed.
* `manifest_status` **is still undefined.** `grep -n "manifest_status"` returns exactly one hit — the list
  row at §2 line 171 (`…, evidence_grade, synthetic, synthetic_note?, manifest_status, record_status, …`).
  The manifest route (§3) defines `status ∈ {frozen, absent, corrupt}` but never equates it with
  `manifest_status`, and no other section mentions the name. The rev-3 mandate ("Define `manifest_status`
  and `content_is_original`, or remove them") is therefore half unmet, and the rev-4 commit message's claim
  "D4: `manifest_status` and `content_is_original` are defined" is **not true of the document**.
  This matters because §9 criterion 6 promises that tests "compare every served field": a served field with
  no value set or derivation rule cannot be asserted. Fix: either define
  `manifest_status = ` the §3 manifest `status` (`frozen|absent|corrupt`) or drop the field.

**Verdict: D4 PARTIALLY CLOSED.**

### D5 — **CLOSED** for duplicates; the replacement justification is still false for corrupt rows

Rev-4 §2 (156–160):

> "`total` is the number of discovered run-shaped records that **pass the filters**: with no filter that
> includes corrupt and duplicate rows; with a filter it counts only matching rows. A **corrupt** row has
> none of the filtered fields and therefore never matches a positive filter (it appears only in an
> unfiltered listing), while a **duplicate** row is parseable and matches filters normally."

* The duplicate half of D5 is fixed: a duplicate is parseable by definition and now "matches filters
  normally". **CLOSED.**
* The replacement sentence about corrupt rows is still factually false. `corrupt` = "unparseable, **wrong
  `run_id`, or missing a required field**" (§2 line 130–131): the last two sub-cases are *parseable* and can
  carry `verdict`/`stage`, so a `stage=…` or `verdict=…` filter could match them. This is exactly rev-3's
  nit N-b, and rev-4 has hardened it into the normative sentence rather than removing the false
  justification. The *behaviour* is nevertheless defined (corrupt rows never match a positive filter), so
  an implementer can follow it; the justification is what is wrong. Advisory A4.

**Verdict: D5 CLOSED (behaviour defined; false justification remains, advisory).**

### D6 — **CLOSED** (fail-closed), with `complete` outside the response shape

Rev-4 §2 (177–186):

> "A traversal that is knowingly incomplete must not be paged: **if `unreadable_entries > 0`, the list
> route returns `503 validation_not_configured` with `details.reason: "index_incomplete"` and the
> counts** — whether or not records were found — so no page walk can silently omit rows. `complete: bool`
> is returned on every successful list (always `true`, since a `false` index never returns 200) …"

§7 row 4 (484) matches: `| 4 | L | the walk was knowingly incomplete (unreadable_entries > 0, whether or
not records were found) | 503 validation_not_configured (details.reason: "index_incomplete") |`. §9
criterion 5 (542) matches. **The D6 defect is fixed: the partial tree now fails closed.**

Consistency check the task asked for:
* `discovery.unreadable_entries` can only be `0` on a 200 (any positive value is a 503), and
  `discovery.capped` can only be `false` (caps exceeded is row 6). Both fields are therefore inert but not
  contradictory.
* **`complete` is not in the response shape.** §2's Response list (166–168) is `{runs[], count, total,
  limit, offset, has_more, corrupt_count, duplicate_count, ignored_entries, discovery, empty_reason?,
  warnings[]}` — no `complete`. The prose says it "is returned on every successful list". This is a
  prose-vs-schema mismatch: the field that D6's fix added is described but never declared, so it will not be
  derived into `web/contracts/p7-validation-read.v1.json` unless the shape is corrected. Mandatory item M3.
* Since `unreadable_entries > 0` is now a 503, the §2 claim that unreadable subtrees are "named in
  `warnings[]` (≤20 samples)" describes samples that can never appear on a 200. `warnings[]` is also the
  channel §4.2 uses for grade-downgrade notices, but the detail response (§5, 365–369) has no `warnings[]`
  field at all. Advisory A6.

**Verdict: D6 CLOSED (semantics fixed); `complete` schema omission raised as M3.**

### D7 — **CLOSED** (evidence retained and re-runnable), with fidelity caveats

* The directory `validation-quarantine/DRYRUN-p7-01/` exists and is tracked: `README.md`,
  `p7-fixture-dryrun.py`, `p7-fixture-dryrun.txt`, `p7-real-records-dryrun.py`,
  `p7-real-records-dryrun.txt` — exactly what D7 asked to retain.
* I re-ran both scripts read-only:
  * `python3 validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.py` exited 0 and its stdout is
    **byte-identical** to `p7-real-records-dryrun.txt` (`diff` exit 0): `discovered run-shaped: 9
    ignored: 179`, all nine runs `consistent=True`, context forms summing to 27/30/6.
  * `PYTHONPATH=src python3 validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py` exited 0 and its
    stdout is **byte-identical** to `p7-fixture-dryrun.txt` (`diff` exit 0): `discovered 5 ignored 12`,
    duplicate stem 409, `A manifest_status=frozen` with match/mismatch/missing, `E manifest_status=corrupt
    rule=art_dotdot: dot segment`, all five escape forms refused, the symlinked file/dir passing the string
    grammar, and the provider-shaped secret withheld as original.
  * The fixture script recreates `/tmp/p7-fixroot` / `/tmp/p7-outside`; `git status --porcelain` was empty
    immediately after both runs. **Re-runnable as documented, and it touches nothing in the repo.**
* The README's per-input SHAs check out: P7 HEAD `c8fd597…` and baseline `039f5a3…` are the committed
  revisions; source-repo HEAD is `7016dabb8926d6bd6599f1f56b8f39cbda253ebf`, which matches the README.
  The nine `run-*.json` records, `verdict_consistent` 9/9, PASS 3 / BLOCKED 6, cases PASS 57 / BLOCKED 6, 0
  FAIL, 0 `failure_class`/`failure_output`, 21 evidence entries / 12 absolute, and the 6/27/30 context
  split all reproduce.
* **Caveats (advisory, see A1/A3/A5):** (i) the README's aggregate "sha256 over the sorted sha256 list of
  the nine run records" (`1c9deec3…`) is not reproducible under any canonicalisation I tried — the
  individual records and both scripts *are*; (ii) the contract still says "288 scanned entries" in §2
  (line 107) and §9 (line 533), while the retained script reports 9 records + 179 ignored = 188 considered
  entries, and my enumeration of the whole tree gives 503 regular files / 206 dirs / 1 symlink; (iii) the
  real-records script hard-codes `run_grade=unknown(...)` rather than computing §4.2, and maps Resolution
  from `interpretation`; (iv) the fixture tree contains no unreadable subtree and no assignment-shaped
  secret, so it does not exercise D6's new negative nor criterion 4's `db_password=…`/`{"api_key": …}`
  negative, despite §9's "a synthetic tree exercises every negative".

**Verdict: D7 CLOSED** — the evidence exists, is tracked, is internally coherent at the record level, and
re-runs verbatim; the aggregate SHA and the "288" figure do not reproduce, and the scripts are not faithful
implementations of every rev-4 rule.

---

## 3. New defects introduced by rev 4

Ordered by severity. N1 is, in my judgment, freeze-blocking; N2–N5 are smaller but concrete.

### N1 (HIGH, security-relevant undefined behaviour) — the route-scoped precedence table never defines the list route's `acl` visibility

Rows 8–10 are scoped `D M A C`; the list route `L` appears only in rows 4 and 6. §6 says:

> "**`acl` mode is indistinguishable from "no such run".** Any run an `acl`-mode caller may not read — a
> non-granted project, an unattributed run without `validation:unattributed`, a corrupt/duplicate record
> (which fails closed …), or a run that does not exist — is reported with the **same** status and code:
> `404 validation_run_not_found`." (§6, 443–446)

That is a per-run statement; §6's bullets ("`acl`: every project in the effective `projects` list is
checked (all, never just the first)"; "Unattributed runs … additionally require
`validation:unattributed`") are also per-run. **Nowhere does the contract say whether the list route in
`acl` mode is grant-filtered, shows every discovered run, or refuses.** §2's filters (`verdict`, `stage`,
`mode`, `attribution`, `project`) are caller-chosen, not grants.

Consequences:
* An implementer reading row 8's scope literally concludes the `acl` refusal does not apply to `L`, and may
  ship a list that enumerates run ids, projects, attribution, grades and verdicts from projects the caller
  is not granted. That directly contradicts §6's isolation intent and the P4 `not_found` convention the
  document is built on.
* Nothing in the six acceptance criteria forces the safe reading: criterion 3 pairs 200/refusal "on the
  same run and artifact" (per-run routes), and criterion 5 is about pagination. So the tests cannot catch
  the unsafe choice either — it is untestable against the criteria as written.

This is a defect *introduced by rev 4's own fix*: rev 3 had no route column, so the list route was at least
not explicitly excluded from the `acl` refusal. Fix: add a row or a §2/§6 sentence that fixes list-route
`acl` visibility — e.g. "in `acl` mode the list returns only runs the caller may read (grant-filtered per
run exactly as §6), and `total`/`corrupt_count`/`duplicate_count` are computed over the visible set" — and
add the paired acceptance case. At minimum state explicitly if the list is *not* filtered, and why.

### N2 (MEDIUM, normative contradiction) — the section-grade rule can exceed the case grade

Detailed under D3 above. `component` is a marker for sections (marker table line 318 applies to "a
mapping carrying a marker"), so a section mapping `{"component": …}` grades `component` over a case graded
`simulated` or `unknown`, contradicting line 336 ("a section grade can never exceed its case grade"). This
is a second, independent instance of the rev-3 D3 class that the fix did not cover: the rev-3 text itself
warned the per-section claim "does hold *provided* section markers only ever yield `mocked`/`simulated`",
and rev 4 added `component` to that table.

### N3 (MEDIUM, schema/prose mismatch) — `complete` is described but not declared

Detailed under D6. The Response shape at §2 (166–168) omits `complete`, which the prose at 182 declares
"returned on every successful list". Whichever is intended, the derived contract will not carry the field.

### N4 (LOW-MEDIUM, undefined served field) — `manifest_status` remains undefined

Detailed under D4. It is the only list-row field with no definition and no derivation rule, which blocks
criterion 6's "compare every served field".

### N5 (LOW, undefined response for a listed integrity value) — `integrity: unreadable` has no A/C answer

§3 (237–240) defines `integrity ∈ {match, mismatch, missing, symlink_refused, unreadable}` and gives
responses only for `mismatch` ("never served as content") and `symlink_refused` ("`404 artifact_not_found`").
Row 12 covers "declared file missing or symlinked", row 13 covers `mismatch`, row 14 the size cap — but a
declared file that exists and cannot be read (permission denied, `EIO`, non-regular) is `unreadable` and has
no defined A/C status. The manifest route can still report it in `files[]`; the artifact routes cannot.
Criterion 4 does not name this case, so it is unlikely to be caught by tests. Fix: map `unreadable` to a
response (e.g. `404 artifact_not_found`, `reason: "unreadable"`, or `503`).

### N6 (LOW, wording) — false statements in the new §7 rationale

* "Rows 12–16 are the only rows that can fire on `A`/`C`" (504) is false: rows 8, 9 and 10 also carry route
  `A C`. The conclusion (corrupt manifest ⇒ 409 on `M`, 404 on `A`/`C`) is still correct.
* "a malformed id never causes a filesystem read" (477) does not follow from the stated stage ordering:
  row 3 (root open, a filesystem operation) is in stage B and precedes row 7 (malformed id, also stage B),
  so a malformed `run_id` with an unset/unreadable root is answered by row 3 (`503`), not row 7 (`400`),
  and the root is opened first. The behaviour is deterministic ("first matching row wins"); the rationale is
  the part that is wrong.
* Row 11's stated rationale — "in `acl` mode a record that cannot be read has already been answered by
  row 8" — does not hold for a **readable** run whose **manifest** is corrupt: a caller holding
  `validation:unattributed` may read the (now unattributed) run, row 8 does not fire, and row 11 is
  `single_user`-only, so `M` falls through to §3's 200-with-`status:corrupt` while `single_user` returns
  `409`. The asymmetry may be intended, but the justification as written is false.

### Things I checked that are *not* defects

* `context_pack_present` is defined (§1.1 90–92), used in the chain (§4.1 285), and consistent with the
  section rule; `content_is_original` is defined and consistent with `form`.
* The `form=original` gate (`redact_text(text) == text` plus declared-flag/UTF-8/hash checks) is sound: I
  re-ran the three masker samples (bare assignment, quoted JSON assignment, provider bearer) and
  `redact_text` masks all three while `looks_like_credential` is `False` for the two assignment shapes —
  so the assignment-shaped secret genuinely cannot leak as original.
* The id grammars (`^run-…Z$`, `^art_[a-z0-9][a-z0-9_.-]{0,63}$`) and the "validate before any path
  construction" rule are consistent with the route captures and the manifest path rules.
* Rev 4 did not touch the record facts or the six acceptance criteria except to strengthen criterion 5; the
  real-record facts it repeats (9 records, 3/6, 57/6, 0 FAIL, 21/12 evidence, 27 reason-only, all nine
  `verdict_consistent`, no marker/executor firing) all reproduce.

---

## 4. Residual risk register (what a validator must still exercise at runtime)

1. **List-route `acl` visibility (N1).** Until fixed, a validator cannot write the isolation assertion for
   `GET /v1/validation/runs` in `acl` mode. Once fixed, exercise a caller with `validation:read` but no
   grant for a run's project and assert the run is absent from the page (and from `total`), alongside the
   per-run 404.
2. **Section grade vs case grade (N2/N3).** Construct a case whose `agent_response` carries no marker (case
   grade `simulated` or `unknown`) but whose `state_before`/`context` mapping carries a truthy `component`
   key; assert the served section grade does not exceed the case grade.
3. **`manifest_status` (N4) and `complete` (N3).** Freeze the two fields in the derived contract and assert
   them on the list route for a frozen/absent/corrupt manifest and a complete walk.
4. **`integrity: unreadable` on A/C (N5).** Make a declared file unreadable (mode `000` / non-regular) and
   assert the chosen status is stable, not a 500.
5. **The fail-closed incomplete index (D6).** With `unreadable_entries > 0` on a tree that also holds valid
   records, assert `503 validation_not_configured` / `index_incomplete` (not a short page), and assert that
   by-id detail/manifest/content still work.
6. **The §1.1 presence matrix.** Assert the exact `present`/`present_kind`/`context_pack_present`/
   `missing_reason` over all 63 real cases (27 reason-only, 6 sentinel, 30 recorded-no-pack, 0 pack) and
   that the Context chain link is missing 9/9 for three distinct reasons.
7. **The grade ladder end-to-end.** Real records must grade `unknown`; a `synthetic: true` fixture must
   grade `simulated`; a `component` manifest plus a simulated case must grade `simulated`; a `component`
   section marker must be clamped (see 2).
8. **`REDACT_TEXT_VERSION` reproducibility.** Pin the constant and recompute `masked_sha256` from
   `(canonical.redact_text, version, bytes)`; fail if the pattern table changes without a bump. The constant
   does not exist yet (`hasattr(canonical, "REDACT_TEXT_VERSION") == False`) — expected for a proposal.
9. **The mirrored descriptor walk vs `hash_workspace_file`.** Assert equal digests and that the served bytes
   are the verified buffer (no stat/read race), including a symlinked intermediate directory.
10. **`ProjectAccess` reuse.** Assert behaviour with a read-only/busy connection and with the P4 migration
    absent at runtime.
11. **ADR 0015.** Still a forward commitment; it must exist and name the seven codes and the bump rule
    before the contract is frozen.
12. **Acceptance criterion 1 remains BLOCKED.** No real FAIL run exists (0 FAIL cases, 0 `failure_class`, 0
    `failure_output`; re-verified). The unblock action is named but not executed.

---

## 5. Verdict

**APPROVE WITH NAMED MANDATORY CHANGES.**

The architecture is sound and rev 4 is a real improvement: D1 is fully and correctly closed (the 6/27/30
split, the `context_pack_present` separation, and §4.1/§9 are all consistent with the data); D2's row
collisions are gone and the precedence table is now deterministic; D5's duplicate half is fixed; D6 now
fails closed; D7's evidence is retained, tracked and re-runs byte-for-byte. I re-verified every record fact
these rest on and they all hold.

I cannot return `APPROVE (freeze as-is)` because the rev-4 fix left **one security-relevant undefined
answer (N1)** and **two rev-3 mandatory items only partially closed (D3, D4)**, and one of those (N4)
directly blocks acceptance criterion 6's "compare every served field". These are not "the implementation's
tests will catch it": the contract is silent, so the tests would merely encode whatever the implementer
guessed. Each fix is small and targeted; none requires redesign.

**Mandatory before the contract is frozen** (ordered by severity; each is concrete):

1. **Fix the list route's `acl` visibility (N1).** State in §2/§6 (or add a row) whether an `acl`-mode
   `GET /v1/validation/runs` is grant-filtered; if yes, say that `total`/`corrupt_count`/`duplicate_count`
   are computed over the visible set and add the paired acceptance case; if no, state that explicitly and
   justify it against §6's isolation rule.
2. **Define `manifest_status` or delete it (D4/N4).** Say `manifest_status = ` the §3 manifest `status`
   (`frozen|absent|corrupt`) and where it is derived, or remove it from the list row.
3. **Declare `complete` in the list Response shape (D6/N3)** (or remove it from the prose). The field D6
   added is currently described but not part of the response type.
4. **Repair the section-grade rule (D3/N2).** Either drop `component` from the section-marker set, or define
   `section grade = min(case grade, section marker grade)`; the current text promises an invariant the
   marker table violates.

**Advisory (non-blocking observations):**

* **A1 — "288 scanned entries" is unreproducible.** §2 line 107 and §9 line 533 still say "9 run-shaped
  records out of 288 scanned entries", but the retained dry run reports 9 records + 179 ignored = 188
  considered entries (`p7-real-records-dryrun.txt:1`), and my own enumeration of `validation-runs/` gives
  503 regular files / 206 dirs / 1 symlink. The figure appears to be a stale rev-3 number not carried over
  to the retained evidence; delete it or re-derive it.
* **A2 — the three Context `missing_reason` strings are named but never quoted.** §4.1 says "not a context
  pack (three named reasons)" and §9 criterion 2 says each form has "its own `missing_reason`", but the
  actual strings (for sentinel / reason-only / recorded-no-pack) are nowhere in the document. Quote them or
  say the reason is free text; criterion 2 can assert only that the link is missing.
* **A3 — the retained real-records script is not a faithful implementation of rev 4.** `chain()` maps
  Resolution from `case.interpretation` (rev 4: Resolution has no source at all), treats an empty list as
  `present: true` (rev 4: `absent`), and prints a hard-coded `run_grade=unknown(...)` instead of computing
  §4.2. It agrees with the contract on the current data; it would disagree on other data.
* **A4 — the D5 replacement sentence is still false for parseable-corrupt rows.** "A corrupt row has none
  of the filtered fields" is untrue for the `wrong run_id` / `missing required field` sub-cases; the
  behaviour (corrupt never matches a positive filter) is fine, the justification is not. Same as rev-3
  N-b, second half.
* **A5 — the retained fixture does not exercise every negative it is said to.** No unreadable subtree (so
  D6's new 503 is untested by the retained dry run), no assignment-shaped secret (the fixture log is
  provider-shaped, `Authorization: Bearer ghp_…`, whereas §5/§9 criterion 4 name `db_password=…` /
  `{"api_key": …}`), and no grade computation. §9 line 534's "a synthetic tree exercises every negative"
  overstates the retained artifact.
* **A6 — `warnings[]` is under-specified.** It is a list-route field, but §4.2 uses it for the per-run
  grade-downgrade notice and the detail response has no `warnings[]` at all; and after D6 the
  unreadable-subtree samples it is described as carrying can never appear on a 200. State the field's
  routes and contents, or route the downgrade notice through `grade_basis`.
* **A7 — README aggregate SHA not reproducible.** The README's "sha256 over the sorted sha256 list of the
  nine run records" (`1c9deec38ccf0015269f61fd3f3517aa7eb93327b86f75ffc78c3b1ee6032464`) did not match any
  canonicalisation I tried (sorted hex newline/concat/comma/json, path order, page order, raw-byte concat,
  whole-tree `sha256sum`). The per-record content and both scripts reproduce, so this is cosmetic, but the
  README should either state the exact command or drop the figure.
* **A8 — `ignored_entries` count vs "20-sample cap".** §2 defines `ignored_entries` as a count; §8 (526–527)
  speaks of a "20-sample cap on the `ignored_entries`/`warnings` **samples**". Reconcile (count field vs
  sample list).
* **A9 — header stale.** The `Status:` line still reads "**proposal, revision 3, awaiting delta
  re-review**" although the revision-history table now names rev 4 "this document". Cosmetic, but a frozen
  contract must be self-identifying.
* **A10 — row 10 names two codes / minor wording.** See D2 and N6: row 10's Response cell holds
  `validation_run_corrupt / validation_run_ambiguous`; "Rows 12–16 are the only rows that can fire on
  `A`/`C`" and "a malformed id never causes a filesystem read" are false as written.

---

## Appendix A — exact commands run (verbatim key output)

Run from `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01` unless a `cd` says otherwise.
All reads were read-only; scratch stdout was captured under `/tmp` only (no repo writes).

```bash
# --- baseline integrity ---
git rev-parse HEAD                                   # 1b9791a125617deb85b84a565ccb61e4d95fe145
git branch --show-current                            # p7-01-validation-read
git status --porcelain                               # (empty)
git diff --cached --stat                             # (empty)
git diff --stat c8fd597 1b9791a
#  validation-quarantine/DRYRUN-p7-01/README.md       |  52 ++
#  .../DRYRUN-p7-01/p7-fixture-dryrun.py              | 236 +++++++
#  .../DRYRUN-p7-01/p7-fixture-dryrun.txt             |  34 +
#  .../DRYRUN-p7-01/p7-real-records-dryrun.py         | 130 ++++
#  .../DRYRUN-p7-01/p7-real-records-dryrun.txt        |  22 +
#  .../REVIEW-p7-01-proposal/RE-REVIEW-2-findings.md  | 731 +++++++++++++++++++++
#  .../REVIEW-p7-01-proposal/RE-REVIEW-findings.md    | 658 +++++++++++++++++++++
#  .../REVIEW-p7-01-proposal/findings.md              | 493 ++++++++++++++
#  web/P7-01-CONTRACT-PROPOSAL.md                     | 166 +++--
#  9 files changed, 2464 insertions(+), 58 deletions(-)
git diff --name-status c8fd597 1b9791a             # 5 A in DRYRUN-p7-01, 3 A in REVIEW-..., 1 M web/...
git log -1 --format='%H %ci' 1b9791a               # … 2026-10-08 07:06:24 -0700
git log -1 --format='%H %ci' c8fd597               # … 2026-10-08 07:00:07 -0700
```

```bash
# --- D1: recompute the context breakdown from the real records with the rev-4 rules ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
python3 - <<'PY'
import json, glob, collections
SENT="N/A (P0 not implemented)"
REASON_ONLY={"reason","note","detail","not_applied_reason"}
PACK={"context_id","pack","sections","rendered","content","tokens","parts"}
def presence(v, has=True, context=False):
    if not has: return ("absent",False,False)
    if v==SENT: return ("sentinel",False,False)
    if isinstance(v,dict) and set(v)<=REASON_ONLY: return ("reason_only",False,False)
    if isinstance(v,str) and v.strip()=="" : return ("absent",False,False)
    if isinstance(v,list) and len(v)==0: return ("absent",False,False)
    pack=False
    if context:
        pack = (isinstance(v,dict) and bool(set(v)&PACK)) or (isinstance(v,list) and len(v)>0)
    return ("value",True,pack)
files=sorted(glob.glob("validation-runs/**/run-*.json",recursive=True))
cat=collections.Counter(); keysets=collections.Counter(); total=0; packcases=0
for f in files:
    d=json.load(open(f))
    for c in d["cases"]:
        total+=1; v=c.get("context"); kind,present,pack=presence(v,"context" in c,context=True)
        if kind=="value" and present and not pack: cat["recorded_no_pack"]+=1
        elif kind=="value" and pack: cat["pack"]+=1; packcases+=1
        else: cat[kind]+=1
        if isinstance(v,dict): keysets[tuple(sorted(v))]+=1
print("total cases:",total)
print("context categories:",dict(cat))
for k,n in keysets.most_common(): print("  ",n,k)
print("cases where chain context present (present and pack):",packcases)
PY
# total cases: 63
# context categories: {'reason_only': 27, 'recorded_no_pack': 30, 'sentinel': 6}
# context key-sets:
#    27 ('reason',)
#    9  ('db_path', 'processes', 'reason')
#    9  ('reason', 'scratch_db')
#    9  ('audit_entries', 'audit_rows_stored', 'reason')
#    3  ('actor_id', 'core_url', 'db_path', 'host_id', 'reason', 'source_session_id')
# cases where chain context present (present and pack): 0
```

```bash
# --- D1 supporting: interpretation is sentinel in all 63 cases (why the retained script's
#     resolution-as-interpretation mapping happens to agree) ---
# (same script family)  interpretation forms across 63 cases: {'sentinel': 63}
```

```bash
# --- real-record facts re-verified ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
find validation-runs -name 'run-*.json' | wc -l        # 9
find validation-runs -name 'manifest.json' | wc -l      # 0
# python3: runs 9; run verdicts {'PASS': 3, 'BLOCKED': 6}; case verdicts {'PASS': 57, 'BLOCKED': 6};
#   failure_class nonzero 0; failure_output nonzero 0; evidence entries 21 absolute 12;
#   verdict_consistent 9/9; records with 'synthetic' key 0; marker hits (none);
#   executors {'gpt-6-sol': 7, '/root/sol_validation_finish': 1, 'sol_p1_impl': 1};
#   rule-3 executor regex matches 0
git rev-parse HEAD                                       # 7016dabb8926d6bd6599f1f56b8f39cbda253ebf
```

```bash
# --- D7: re-run the retained evidence and diff against the captured output ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01"
python3 validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.py > /tmp/rr3-real.out 2>/tmp/rr3-real.err; echo $?   # 0
diff /tmp/rr3-real.out validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.txt; echo "diff_exit=$?"               # diff_exit=0
# first lines of the re-run: "discovered run-shaped: 9   ignored: 179" /
#   "ignored reasons: ['depth_cap', 'excluded_dir', 'not_run_shaped']"
PYTHONPATH=src python3 validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py > /tmp/rr3-fix.out 2>/tmp/rr3-fix.err; echo $?   # 0
diff /tmp/rr3-fix.out validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.txt; echo "diff_exit=$?"                       # diff_exit=0
git status --porcelain                                   # (empty) — before and after
```

```bash
# --- walk-figure reconciliation ("288 scanned entries" vs the retained script) ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
# os.walk emulating the retained dry-run: runs 9, ignored 179, sum 188; os.walk iterations 55
# whole tree: regular files 503, real dirs 206, symlinks 1; files+dirs 709
# variants tried for 288: files maxdepth=None/3/4/5 prune-dot -> 231/140/171/217 (none = 288)
```

```bash
# --- field definitions ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01"
grep -n "manifest_status" web/P7-01-CONTRACT-PROPOSAL.md    # 171 only (list row; never defined)
grep -n "complete\b\|complete:" web/P7-01-CONTRACT-PROPOSAL.md  # 177–189 prose; not in the §2 Response list
grep -n "content_is_original" web/P7-01-CONTRACT-PROPOSAL.md    # 405 (defined)
grep -n "288\|179\|188" web/P7-01-CONTRACT-PROPOSAL.md validation-quarantine/DRYRUN-p7-01/README.md \
     validation-quarantine/DRYRUN-p7-01/*.txt
# web/P7-01-CONTRACT-PROPOSAL.md:107: … 9 run-shaped records out of 288 scanned entries.
# web/P7-01-CONTRACT-PROPOSAL.md:533: … 9 records / 288 scanned entries …
# validation-quarantine/DRYRUN-p7-01/README.md:41: … 179 ignored walk entries …
# validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.txt:1: discovered run-shaped: 9   ignored: 179
grep -n "Status:" web/P7-01-CONTRACT-PROPOSAL.md           # 3: "proposal, revision 3, awaiting delta re-review."
```

```bash
# --- masker samples re-verified (read-only import) ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01"
PYTHONPATH=src python3 - <<'PY'
from jasmine_core import canonical
from jasmine_core.validation import recorder
for name, s in {
 "bare assignment": "db_password=hunter2hunter2 in the log line",
 "quoted json assignment": '{"api_key": "abcdef1234567890"}',
 "provider bearer": "line1\nAuthorization: Bearer sk-abcdefgh12345678\nline3",
}.items():
    print(f"{name}: looks_like_credential={canonical.looks_like_credential(s)} "
          f"redact_noop={canonical.redact_text(s)==s} masked={canonical.redact_text(s)!r}")
print("has REDACT_TEXT_VERSION:", hasattr(canonical,"REDACT_TEXT_VERSION"))
PY
# bare assignment:          looks_like_credential=False redact_noop=False masked='db_password=[REDACTED] in the log line'
# quoted json assignment:   looks_like_credential=False redact_noop=False masked='{"api_key": "[REDACTED]"}'
# provider bearer:          looks_like_credential=True  redact_noop=False masked='line1\nAuthorization: Bearer [REDACTED]\nline3'
# has REDACT_TEXT_VERSION: False
grep -n "Bearer\|db_password\|api_key\|secret" validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py
# 54: "…\nAuthorization: Bearer ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"  (provider-shaped only)
```

```bash
# --- README aggregate SHA not reproduced (D7/A7) ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
python3 - <<'PY'
import hashlib, glob, json
files=sorted(glob.glob("validation-runs/**/run-*.json",recursive=True))
per=[hashlib.sha256(open(f,'rb').read()).hexdigest() for f in files]; s=sorted(per)
h=lambda b: hashlib.sha256(b).hexdigest()
print("nl-join+trailing   ", h(("\n".join(s)+"\n").encode()))
print("comma-join         ", h(",".join(s).encode()))
print("json-dumps         ", h(json.dumps(s).encode()))
print("hexconcat(sorted)  ", h("".join(s).encode()))
print("rawdigestconcat    ", h(b"".join(bytes.fromhex(x) for x in s)))
print("TARGET              1c9deec38ccf0015269f61fd3f3517aa7eb93327b86f75ffc78c3b1ee6032464")
PY
# nl-join+trailing    0809a2562255672f4876327d4a394cab170f4d1a1743cf47d81ef3f82fc3378f
# comma-join          1c1476a252879e41393038ebd7eded7572c4f97a44da21aab98a431a18952ca5
# json-dumps          86a9b7f13a4224391255012641a3cbb9f795cfafba3a36b4ae34afb01317dbc6
# hexconcat(sorted)   7b57b65df23a497d2f2b89647ec0d4f47a12fa845954b597bffeadf0fa98e3e3
# rawdigestconcat     aad8852525ba09e8f2eb7e925c8df7855d31b90f6d0d1f29adc5ef466302dce8
# TARGET              1c9deec38ccf0015269f61fd3f3517aa7eb93327b86f75ffc78c3b1ee6032464  (no match)
```

```bash
# --- the rev-3→rev-4 proposal diff was read in full ---
git diff c8fd597 1b9791a -- web/P7-01-CONTRACT-PROPOSAL.md
# touched: header/rev-history, §1.1 context_pack_present, §2 pagination/corrupt-duplicate,
#   §2 incomplete-index/complete, §3 manifest status, §4.1 Context row + verified sentence,
#   §4.2 marker table + section/run grade, §5 content_is_original, §7 route-scoped table,
#   §9 criteria 2/5 + retained-evidence paragraph.
# NOT touched: line 3 Status, line 107/533 ("288"), line 171 manifest_status, the §2 Response list.
```

## Appendix B — files written

Only this file was written:

`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-3-findings.md`

No tracked file was modified, nothing was staged, committed or pushed; HEAD remains
`1b9791a125617deb85b84a565ccb61e4d95fe145` on branch `p7-01-validation-read`. `git status --porcelain`
was empty immediately after every command, including after re-running both retained dry-run scripts (the
fixture script wrote only `/tmp/p7-fixroot` and `/tmp/p7-outside`). The only other paths I wrote were
scratch stdout captures under `/tmp` (`/tmp/rr3-real.out`, `/tmp/rr3-fix.out` and their `.err`), which are
outside the repository.
