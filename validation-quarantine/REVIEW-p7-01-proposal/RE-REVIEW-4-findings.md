# Fifth-pass delta re-review — `web/P7-01-CONTRACT-PROPOSAL.md` revision 5

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent, delegated independent
  reviewer). This is a new agent context; it is not the author of the proposal or of any earlier review.
* **Date of review run:** 2026-10-08 (host `Thu Oct  8 07:15:58 PDT 2026`; UTC `2026-10-08T14:15:58Z`).
* **Commits reviewed** (all in the p7-01 worktree, branch `p7-01-validation-read`):
  * rev 4 (the delta baseline): `1b9791a125617deb85b84a565ccb61e4d95fe145` —
    `P7-01 phase 1 rev 4: close the third review's D1-D6 and retain the dry-run evidence`
  * rev 5 (the artifact under review / worktree HEAD): `0f7b2ec60847308ba3a9119fdbfe7d741041b81c` —
    `P7-01 phase 1 rev 5: close the fourth review's 4 mandatory items and every advisory`
  * proposal baseline: `integration/p6-03-realcore` @ `039f5a3e7800704e5f0478d01992f00eeb2ca3f1`
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`.
* **Authority / checklist:** `validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-3-findings.md`
  (705 lines, read in full — its §5 four mandatory items and its A1–A10 list are the checklist); the three
  earlier reviews were skimmed for context. Real records were read read-only from the shared source repo
  `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1` (HEAD
  `7016dabb8926d6bd6599f1f56b8f39cbda253ebf`, matching the retained evidence README's capture note).
* **Writes:** only this file (Appendix B). No tracked file was modified, nothing was staged, committed or
  pushed; the worktree was clean before and after every command.

---

## 1. Baseline integrity

Verbatim, run from the p7-01 worktree:

```
$ git rev-parse HEAD
0f7b2ec60847308ba3a9119fdbfe7d741041b81c

$ git branch --show-current
p7-01-validation-read

$ git status --porcelain
(empty — clean; no untracked leftovers)

$ git diff --cached --stat
(empty)

$ git diff --stat 1b9791a 0f7b2ec
 validation-quarantine/DRYRUN-p7-01/README.md       |  94 ++-
 .../DRYRUN-p7-01/p7-fixture-dryrun.py              |  58 +-
 .../DRYRUN-p7-01/p7-fixture-dryrun.txt             |  12 +-
 .../DRYRUN-p7-01/p7-real-records-dryrun.py         |  43 +-
 .../REVIEW-p7-01-proposal/RE-REVIEW-3-findings.md  | 705 +++++++++++++++++++++
 web/P7-01-CONTRACT-PROPOSAL.md                     | 127 ++--
 6 files changed, 958 insertions(+), 81 deletions(-)

$ git diff --name-status 1b9791a 0f7b2ec
M	validation-quarantine/DRYRUN-p7-01/README.md
M	validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py
M	validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.txt
M	validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.py
A	validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-3-findings.md
M	web/P7-01-CONTRACT-PROPOSAL.md
```

**Assessment.**
* HEAD is `0f7b2ec60847308ba3a9119fdbfe7d741041b81c` on branch `p7-01-validation-read`: **CONFIRMED**.
* `git status --porcelain` is empty and nothing is staged: **CONFIRMED** (before and after all commands,
  including after re-running both retained dry-run scripts).
* The rev-5 change set is exactly: the proposal (`127 ++--`), the four retained-evidence files that changed
  (README, both scripts, one captured `.txt`), and the newly tracked rev-4 review document — i.e. the rev-3
  review itself is the only new file, and `validation-quarantine/DRYRUN-p7-01/` retains all five files
  (`README.md`, both scripts, both `.txt`) as tracked. This is a smaller, cleaner delta than rev 4's.

---

## 2. Closure table for the four mandatory items (rev-3 §5)

| # | Mandatory item (rev-3 §5) | Verdict | Evidence in rev 5 |
| --- | --- | --- | --- |
| M1 | Fix the list route's `acl` visibility (N1) | **CLOSED** | §2 lines 190–201 + §9 criterion 3 (589) |
| M2 | Define `manifest_status` (or delete it) | **CLOSED** | §2 lines 180–185 |
| M3 | Declare `complete` in the list response shape | **CLOSED** | §2 lines 169–174, 212–213 |
| M4 | Repair the section-grade rule (D3/N2) | **CLOSED** | §4.2 lines 371–375 (the old "can never exceed" wording is gone: `grep` exit 1) |

### M1 — **CLOSED**

Rev-5 §2 (190–201) now states the missing rule directly:

> "**`acl` mode: the list is grant-filtered, and `total` counts only what the caller may read.** The list
> route applies the **same** visibility predicate as detail (§6) — a run is visible iff the caller could read
> it — so `runs[]`, `count`, `total`, `corrupt_count` and `duplicate_count` are all computed over the visible
> set, and `discovery.run_shaped_files` counts visible runs rather than every run-shaped file in the tree.
> The other `discovery` fields (`scanned_dirs`, `scanned_files`, `ignored_entries`, `unreadable_entries`,
> `capped`) are diagnostics about the operator's configured root, which is not per-project information, and
> `visibility: "grant_filtered"` says which reading applies; in `single_user` mode every run is visible and
> `visibility: "all"`. A corrupt or duplicate record has no readable manifest and is therefore treated as
> unattributed, so in `acl` mode it is visible only to a key carrying `validation:unattributed` — exactly as
> in detail. This costs one manifest read per discovered run, bounded by `MAX_RUN_SHAPED` (§8). …"

§9 criterion 3 (589) adds the paired test: "for the list route a run the caller may not read appears in
neither `runs[]` nor `total` for the ungranted key while it does for the granted one (§2)".

Internal-consistency check the task asked for (details in §5 below):
* `runs[]`/`count`/`total`: visible set — **consistent** (`total` is defined at §2 159–164 as "pass the
  filters", then scoped to the visible set here).
* `corrupt_count`/`duplicate_count`: explicitly "over the visible set" — **consistent**.
* `discovery.run_shaped_files`: explicitly "counts visible runs" — **consistent**.
* `visibility`: `"grant_filtered"` / `"all"` — **consistent** with the rule.
* "one manifest read per discovered run, bounded by `MAX_RUN_SHAPED`" vs §8: §8 (567) sets
  `MAX_RUN_SHAPED = 500` and §2 (218–219) returns `503 validation_index_too_large` when exceeded, so at most
  500 manifests (each ≤ 1 MiB, §8) are read per list call — **consistent**.
* Remaining leak channels: none reach a *served* run the caller may not read (ids, counts and ordering all
  follow the visible set). Two unfiltered diagnostics remain (`empty_reason` derivation, and
  `ignored_samples[].relative_path` root paths); both are advisory AD1/AD2, not blocking.

**Verdict: M1 CLOSED.**

### M2 — **CLOSED**

Rev-5 §2 (180–185):

> "`record_status ∈ {ok, corrupt, duplicate}` (§2 statuses). `manifest_status` is the run's §3 manifest
> status — `frozen`, `absent` or `corrupt` — derived by looking for `<run_dir>/manifest.json` and applying
> §3's rules **without** reading artifact contents; for a row whose `record_status` is not `ok` it is `null`,
> because the record could not be parsed to confirm that the manifest's `run_id` matches (the row's
> `corrupt_reason`/`duplicate_reason` says why)."

`grep -n "manifest_status"` now returns §2's field list (177) **and** the definition (181); the value set
matches §3's manifest `status ∈ {frozen, absent, corrupt}` (272). The field is therefore assertable by
criterion 6's "compare every served field". **Verdict: M2 CLOSED** (one loose justification for duplicate
rows: AD9).

### M3 — **CLOSED**

Rev-5 §2 Response (169–174):

> "`{runs[], count, total, limit, offset, has_more, corrupt_count, duplicate_count, ignored_entries,
> ignored_samples[], discovery, empty_reason?, complete, visibility}` where `discovery: {root_present,
> max_depth, scanned_dirs, scanned_files, run_shaped_files, unreadable_entries, capped}`, `ignored_samples[]`
> is at most 20 `{relative_path, reason}` entries naming *why* something was not treated as a record, and
> `complete` is `true` on every successful list response — an incomplete index returns `503` (below), so
> `false` is unreachable."

and §2 (212–213): "`complete: bool` is returned on every successful list (always `true`, since a `false`
index never returns 200)". The field D6 added is now **in the response type** and is consistent with the
503-on-incomplete rule (row 4, 525). **Verdict: M3 CLOSED.**

### M4 — **CLOSED**

Rev-5 §4.2 (371–375) replaces the false invariant:

> "**Section grade** = `min` over the trust order of (the case grade, the section's own marker's grade), the
> second term being absent when the section value is not a mapping carrying a marker. The `min` is
> load-bearing: a section mapping `{"component": …}` inside a case graded `simulated` (bundle mode
> `simulated`) must grade `simulated`, not `component`, so a section can **never** be more trustworthy than
> its case."

I re-derived this against the marker table (349–355) and the trust order `real > component > simulated >
unknown` (344): "min over the trust order" is fixed by the worked example to mean *least trustworthy*, so
`section = worst(case, marker)`, which is ≤ the case in trust for every marker in the table (including
`component`). The run rule (377–379) is likewise `worst(effective mode, every case grade)`. **No remaining
path lets a sub-case grade exceed its case** (full argument in §7). **Verdict: M4 CLOSED.**

---

## 3. Closure table for the advisory items A1–A10

| # | Advisory (rev-3 §5) | Verdict | Evidence in rev 5 |
| --- | --- | --- | --- |
| A1 | "288 scanned entries" unreproducible | **CLOSED** | §2 105–110 no longer states 288; §9 579–583 no longer states it. `grep -n "288\|188" web/P7-01-CONTRACT-PROPOSAL.md` → no matches (exit 1) |
| A2 | Three Context `missing_reason` strings named but never quoted | **CLOSED** | §4.1 321–326 quotes all three, and §9 criterion 2 (588) asserts them |
| A3 | Real-records script not faithful to rev 4 | **CLOSED** | `p7-real-records-dryrun.py` now maps Resolution to `False` (65–72), empty list/string → `absent` (53–58), and computes `run_grade` (106–111, 158) instead of hard-coding it |
| A4 | D5 replacement sentence false for parseable-corrupt rows | **CLOSED** | §2 160–163 now says a corrupt row "has no usable `verdict`/`stage`/`mode`/`attribution`, so it cannot be shown to match one" — a definition of the filter behaviour, not a data claim |
| A5 | Retained fixture does not exercise every negative | **CLOSED** | Fixture now builds an unreadable subtree (`chmod 000`, script 104–109) and an assignment-shaped secret (57–61); output lines 1, 3–4 and 21, 35–36 show both |
| A6 | `warnings[]` under-specified | **CLOSED** | Removed from the list shape; §5 (405–407) declares detail `warnings[]` ("run-level notices, including the §4.2 grade downgrade"); §4.2 (380–382) routes the downgrade there and adds `grade_downgraded_by[]` to the row |
| A7 | README aggregate SHA not reproducible | **CLOSED** | README now states the exact command; I ran it and got the A7 value `1c9deec38ccf0015269f61fd3f3517aa7eb93327b86f75ffc78c3b1ee6032464` (Appendix A) |
| A8 | `ignored_entries` count vs "20-sample cap" | **CLOSED** | §2 172–173 introduces `ignored_samples[]` (≤20) distinct from the `ignored_entries` count; §8 572–574 says the cap is on `ignored_samples[]`/`details.samples` while `ignored_entries`/`unreadable_entries` "remain complete" |
| A9 | Header stale | **CLOSED** | §header line 3 now "proposal, revision 5, awaiting the fifth-pass re-review"; revision-history table names rev 5 |
| A10 | Row 10 names two codes / minor wording | **CLOSED** | Row 10 split into `10` (`validation_run_corrupt`) and `10b` (`validation_run_ambiguous`), 531–532; the false "Rows 12–16 are the only rows that can fire on `A`/`C`" is replaced by "Rows 3, 5 and 7 also fire on `A`/`C`", 548 |

**A10 residual (cosmetic, not re-opened):** line 518 still contains "a malformed id never causes a
filesystem read", while lines 548–550 correctly explain that the root *is* opened first and a malformed id
is answered "before any *record* is read". The document now corrects itself; only the isolated sentence is
imprecise (AD3).

All four mandatory items and all ten advisories are closed.

---

## 4. Data re-verification (real records, recomputed by me)

Ran read-only against `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1` (HEAD
`7016dabb…`) with the rev-5 §1.1/§4.1 rules. Verbatim output in Appendix A.

```
files(records): 9
total cases: 63
run verdicts: {'PASS': 3, 'BLOCKED': 6}
case verdicts: {'PASS': 57, 'BLOCKED': 6}
context categories: {'reason_only': 27, 'recorded_no_pack': 30, 'sentinel': 6}
context key-sets:
    27 ('reason',)
    9  ('db_path', 'processes', 'reason')
    9  ('reason', 'scratch_db')
    9  ('audit_entries', 'audit_rows_stored', 'reason')
    3  ('actor_id', 'core_url', 'db_path', 'host_id', 'reason', 'source_session_id')
cases with pack (chain context present): 0
FAIL cases: 0  failure_class nonzero: 0  failure_output nonzero: 0
evidence entries: 21  absolute: 12
manifest.json count: 0
run-file dir depths (below validation-runs): {2: 8, 3: 1}
```

* **§1.1 (94–98)** — "of the 63 cases, 6 `context` values are the sentinel, 27 are `{"reason": …}`-only,
  and **30 are recorded run details** … No real case has a pack": **matches exactly (6/27/30, 0 packs)**.
* **§4.1 (335–338)** — "`context` missing 9/9 — every one of the 63 cases lacks a context pack, in three
  distinct forms (6 sentinel, 27 `{"reason": …}`-only, 30 recorded run details)": **matches**.
* **§9 criterion 2 (588)** — "(6 sentinel, 27 reason-only, 30 recorded run details — each with its own
  `missing_reason`)": **matches**.
* **The three frozen `missing_reason` strings are now stated** in §4.1 (321–326):
  * sentinel → `"recorded as N/A (P0 not implemented)"`
  * reason-only → `"the case records a reason, not a context pack"`
  * recorded-but-packless → `"the section records run details, not a context pack"`
  They are consistent with `p7-real-records-dryrun.py` (49, 51–52) and with §9's assertion sentence.
* **Other record facts the document repeats all reproduce**: 9 records, 3 PASS / 6 BLOCKED, 57 PASS / 6
  BLOCKED cases, 0 FAIL / 0 `failure_class` / 0 `failure_output`, 21 evidence entries / 12 absolute, no
  `manifest.json`, `verdict_consistent` 9/9 (retained script), 0 synthetic keys / 0 marker hits, and the
  nine records at depth 2–3 below the root (8 at depth 2, 1 at depth 3 — matching §2's "depth 2–3").
* One exposition nuance (not an error, carried from rev 3/4): the 30 recorded-no-pack values split into
  **four** key-shapes (listed above), while §1.1 shows one example, `{"db_path": …, "processes": […]}`.
  Advisory AD7.

---

## 5. List-route `acl` visibility — deep consistency check

The task asked whether `total` / `corrupt_count` / `duplicate_count` / `discovery.run_shaped_files` all
follow the visible set, whether `visibility` is consistent with it, whether the "one manifest read per
discovered run, bounded by `MAX_RUN_SHAPED`" claim fits §8's caps, and whether any path still leaks an
unreadable run. Findings:

| Channel | Follows visible set? | Notes |
| --- | --- | --- |
| `runs[]` | **yes** (explicit) | only visible rows are built |
| `count` | **yes** (`count == len(runs)`, §2 163) | |
| `total` | **yes** ("all computed over the visible set") | visible ∧ caller filters |
| `corrupt_count` | **yes** (explicit) | |
| `duplicate_count` | **yes** (explicit) | |
| `discovery.run_shaped_files` | **yes** (explicit: "counts visible runs") | |
| ordering / page position | **yes** | order tuple is applied to the visible set and `total` is the visible count, so no gap or position inference |
| `visibility` | **yes** | `"grant_filtered"` in `acl`, `"all"` in `single_user`, matching the rule |
| `scanned_dirs`, `scanned_files`, `ignored_entries`, `unreadable_entries`, `capped` | no — raw root diagnostics, explicitly justified as "not per-project information" | counts only; no run identity. AD2 discusses `ignored_samples[]` paths |
| `empty_reason` | **not explicitly tied** to the visible set | advisory AD1: an unsafe reading could emit `all_filtered_out` when records exist but are all hidden, a 1-bit existence oracle |
| `ignored_samples[].relative_path` | **not explicitly tied** to the visible set | advisory AD2: raw relative paths can carry operator directory names (and, for a refused symlinked `run-*.json`, a run-id-shaped filename) |

* **Caps consistency:** §2 (218–219) defines `MAX_RUN_SHAPED = 500` and returns `503
  validation_index_too_large` when exceeded; §8 (567) repeats the cap. A list call therefore reads at most
  500 manifests (each ≤ 1 MiB), so "one manifest read per discovered run, bounded by `MAX_RUN_SHAPED`" is
  **consistent**.
* **No leak of a served run:** a run the caller may not read is absent from `runs[]`, from `count`, from
  `total`, from `corrupt_count`/`duplicate_count` and from `run_shaped_files`; its ordering position cannot
  be inferred because pagination is over the visible total. Corrupt and duplicate records — which rev 4
  showed were the hardest cases — are handled explicitly: unattributed, hence hidden without
  `validation:unattributed`.
* **Residual, advisory only:** `empty_reason` (AD1) and `ignored_samples[].relative_path` (AD2). Neither
  exposes a served run's id, verdict, grade or project; both are one sentence of tightening, not a redesign.
  Because §2 already defines `run_shaped_files` as the **visible** count and `empty_reason` "with the counts
  that produced it", the safe reading is derivable, so this does not block the freeze.

**Verdict: M1 stands CLOSED.**

---

## 6. Field definitions and response-shape completeness

The task asked to confirm `manifest_status`, `complete`, `grade_downgraded_by[]`, `ignored_samples[]` and
`content_is_original` are defined and used consistently, and that no named field is undefined.

| Field | Where defined | Consistency |
| --- | --- | --- |
| `manifest_status` | §2 181–185 (`frozen`/`absent`/`corrupt`, `null` for non-`ok`) | matches §3 `status` (272); used only in the list row |
| `complete` | §2 173–174, 212–213 (always `true`; 503 otherwise) | matches row 4 (525) |
| `grade_downgraded_by[]` | §2 185–186; produced by §4.2 380–382 | list-only; consistent with the run-grade rule |
| `ignored_samples[]` | §2 172–173 (≤20 `{relative_path, reason}`); §8 572–574 | distinct from the `ignored_entries` count — A8 resolved |
| `content_is_original` | §5 446–447 (`form == "original"`) | consistent with the `form=original` gate (432–440) |

Every other response shape was walked (list row, `discovery`, manifest, artifact metadata, content envelope,
detail, `chain`, `sections`). All fields with non-obvious semantics have a derivation rule; a handful rely
on their names alone — `corrupt_count`/`duplicate_count` (counts over the same visible/filtered set as
`total`), `case_counts{pass,fail,blocked,total}`, `digests.original`/`digests.masked`, `bytes` vs
`declared_bytes`, `sections[].value?`, `discovery.root_present`/`scanned_dirs`/`scanned_files`. None of these
is ambiguous enough to change an implementation decision; advisory AD8.

---

## 7. Section-grade `min` and the run-grade rule — no remaining escape

* **Section grade:** `section = worst(case_grade, marker_grade)` (the worked example fixes "min over the
  trust order" to mean least-trustworthy). For every marker in the frozen table (349–355) — `synthetic`,
  `simulated`, `fixture`, `mock` → `simulated`; `component` → `component`; `real` is **not** a marker —
  `worst(case, marker) ≤ case` in trust. If the section value is not a marker-carrying mapping, the second
  term is absent and `section = case`. Therefore **no section grade can exceed its case grade**, including
  the `{"component": …}` case that broke rev 4. The old false wording is gone (`grep` for "can never
  exceed"/"same four-value enum" → exit 1).
* **Run grade:** `run = worst(effective mode, every case grade)`, so `run ≤ mode` and `run ≤ every case`
  in trust; a mode-`unknown` bundle cannot be improved by a case grade. This is the same `min`-by-trust
  operator, applied once more.
* **Interaction:** a case marker `component` inside a `simulated` bundle yields that case `component` but
  the run `simulated` — the run clamp works as written, and `grade_downgraded_by[]` stays empty because the
  run grade does not fall *below* the bundle mode. No contradiction.

**No remaining way for a sub-case grade to exceed its case. M4 confirmed CLOSED.**

---

## 8. Retained dry-run evidence re-run

Both scripts were re-run read-only from the p7-01 worktree (verbatim commands in Appendix A):

* `python3 validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.py` → exit 0; stdout **byte-identical**
  to `p7-real-records-dryrun.txt` (`diff` exit 0): `discovered run-shaped: 9   ignored: 179`; all nine
  `consistent=True`; per-run context forms summing to 27 / 30 / 6.
* `PYTHONPATH=src python3 validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py` → exit 0; stdout
  **byte-identical** to `p7-fixture-dryrun.txt` (`diff` exit 0): `discovered 5  ignored 13  unreadable 1`;
  duplicate stem → `409 validation_run_ambiguous`; `A manifest_status=frozen`; `E manifest_status=corrupt
  rule=art_dotdot`; all five escape forms refused; symlinked file and symlinked intermediate directory pass
  the string grammar and are refused at open; assignment-shaped secret withheld as original.
* **Unreadable subtree:** the fixture builds `p7-unreadable` with `chmod 000` (script 104–109) and the
  captured output's lines 1, 3–4 show `unreadable 1` and the `503 … index_incomplete` rule — A5 resolved.
* **Assignment-shaped secret:** the fixture's `art_assign` holds `db_password=hunter2hunter2`; output lines
  21, 35–36 show `looks_like_credential=False` (the old scan would serve it) while
  `redact_text(text) == text` is `False`, so the §5 gate withholds it and `form=masked` serves
  `db_password=[REDACTED]`. I independently re-ran the three masker samples (Appendix A): both
  assignment shapes are `looks_like_credential=False` and masked; the provider bearer is `True`.
* **README digest command:** running the README's exact command in the source repo —
  `find validation-runs -name 'run-*.json' | sort | xargs shasum -a 256 | shasum -a 256` — printed
  `1c9deec38ccf0015269f61fd3f3517aa7eb93327b86f75ffc78c3b1ee6032464`, which is exactly the value rev 3
  recorded (A7) and which rev-4's README stated. A7 resolved.
* **No repo writes:** `git status --porcelain` was empty immediately after both runs; the fixture wrote only
  `/tmp/p7-fixroot` and `/tmp/p7-outside` and restores its `chmod 000` directory in a `finally` block.

---

## 9. New defects introduced by rev 5 (adversarial, proportionate)

This is the fifth review of one document. I hunted for regressions in the rev-5 delta (the new §2 acl
paragraph, the `manifest_status`/`complete`/`ignored_samples[]` additions, the row 10 split, the new row 15,
the section-grade `min`, the three frozen `missing_reason` strings, and the script/README edits). I found no
defect that makes an implementation **wrong, insecure or untestable** against the six acceptance criteria.
The concrete findings are below; none is blocking.

### D1 (LOW, cosmetic — stale range) — §7 still says stage C is "rows 8–16"

Line 515–516: "**C** resource resolution and content (rows 8–16)". Rev 5 added row 15 (artifact unreadable)
and renumbered the old row 15→16, 16→17, so the table now ends at row 17. The range should read "rows 8–17".
Behaviour is unaffected (row 17 is `C`-only non-text content, independent of the earlier rows), but the
stage description is stale.

### D2 (LOW, cosmetic — mislabelled field group) — `ignored_entries` is called a `discovery` field

§2 194–195 lists "The other `discovery` fields (`scanned_dirs`, `scanned_files`, `ignored_entries`,
`unreadable_entries`, `capped`)". `ignored_entries` is a **top-level** list field (169–170); the `discovery`
object (171–172) does not contain it. The rule (raw vs visible) is still clear; only the grouping label is
wrong.

### D3 (LOW, pre-existing but re-flagged) — "a malformed id never causes a filesystem read"

Line 518 states it absolutely; lines 548–550 correctly say the operator-configured root is opened first and a
malformed id is answered "before any *record* is read". The absolute sentence should be softened to "never
reads a record file", matching the correction.

### D4 (ADVISORY hardening) — `empty_reason` should be stated to follow the visible set

See §5/AD1. One sentence in §2 would remove the only residual list-side existence ambiguity.

### D5 (ADVISORY hardening) — `ignored_samples[].relative_path` is unfiltered in `acl`

See §5/AD2. The samples are root diagnostics, but their relative paths can contain operator directory names
and, for a refused symlinked `run-*.json`, a run-id-shaped filename. Consider filtering/redacting samples in
`acl`, or stating explicitly that samples are root diagnostics.

### D6 (LOW, cosmetic) — script/README provenance labels

`p7-real-records-dryrun.py:2` and `p7-fixture-dryrun.py:2` still say "rev-2 … rules"; the README labels the
HEAD row "when first captured (rev 4)" while asserting "Both scripts implement the **rev-5** rules". The code
*is* rev-5-faithful (A3 fixed); only the labels are stale.

### D7 (LOW, cosmetic) — two `missing_reason` spelling mismatches

§1.1 line 87 freezes `missing_reason: "recorded as empty"` for an empty string/list, while the retained
script emits `"recorded as an empty string"` / `"recorded as an empty list"` (53–58); and §1.1 line 82
describes the sentinel only as "(missing_reason quotes the sentinel)" while §4.1 freezes the exact string.
No real case hits the empty form; the three Context strings the criteria assert are exact and consistent.

### D8 (LOW, pre-existing) — values of unrecorded fields on a corrupt row

The list row requires `stage`, `created_at`, `commit`, `mode`, … for a `corrupt` row, but only the ordering
keys (`created_at_key = ""`, `run_id_key = stem`) and `record_status`/`corrupt_reason` are specified. An
implementer can choose `null`/`""` consistently; criterion 5 asserts the corrupt row is listed and counted,
which remains testable.

### D9 (LOW, cosmetic) — `manifest_status: null` justification

§2 183–184 says `null` is because "the record could not be parsed", but a `duplicate` row **is** parseable.
The behaviour (`null` for every non-`ok` row) is explicit and testable; the justification should say "for a
row that is not `ok`" without claiming parse failure for duplicates.

### Things I checked that are **not** defects

* The row-10 split (`10`/`10b`) removes the last multi-valued Response cell; the stage-scoped "first matching
  row wins" rule still gives exactly one answer per route/situation.
* The new row 15 (`A C`, declared file exists but unreadable → `503 validation_not_configured`,
  `reason: "artifact_unreadable"`) is disjoint from rows 12–14 and consistent with §3's `integrity: unreadable`
  and with row 3's root-open rule; it reuses an existing new code, so the "seven new code strings" invariant
  holds.
* `complete` is in the shape, `warnings[]` is out of the list shape and into detail, and
  `grade_downgraded_by[]`/`ignored_samples[]`/`content_is_original` are all defined and used.
* The section marker table's `component → component` is now clamped by the section `min`; the rev-3 D3
  invariant is restored.
* The `form=original` gate is strictly stronger than `looks_like_credential` (re-verified), so criterion 4's
  assignment-shaped-secret negative is sound.
* Criterion 3 now includes the list pairing, so the M1 fix is testable.
* The "288" figure is gone from the document, and the README digest command reproduces the value.

---

## 10. Verdict

**APPROVE (freeze as-is).**

The four rev-3 mandatory items are **CLOSED** and all ten advisories **A1–A10 are CLOSED**. Every
repository/data fact the proposal rests on was recomputed independently and holds: 9 records, 63 cases, the
three-form Context breakdown is exactly **6 sentinel / 27 reason-only / 30 recorded-no-pack / 0 packs**, and
§1.1, §4.1 and §9 all say the same thing; the three frozen `missing_reason` strings are now in the document;
the list route is grant-filtered with `total`/`corrupt_count`/`duplicate_count`/`run_shaped_files` over the
visible set and the manifest-read count bounded by `MAX_RUN_SHAPED = 500`; `manifest_status`, `complete`,
`grade_downgraded_by[]`, `ignored_samples[]` and `content_is_original` are defined and consistent; the
section-grade `min` and the run-grade rule leave no path for a sub-case grade to exceed its case; both
retained scripts re-run byte-for-byte, the fixture exercises the unreadable subtree and the
assignment-shaped secret, and the README's digest command reproduces its recorded value.

The remaining findings (D1–D9) are cosmetic wording, a stale stage range, a mislabelled field group, two
tightenings of advisory hardening (`empty_reason` derivation and `ignored_samples` paths in `acl`), and
pre-existing edge-case wording. None of them makes an implementation from this document wrong, insecure or
untestable against the six acceptance criteria; a validator can write the isolation, grading, discovery,
artifact and digest assertions today. Writing a sixth revision to fold D1–D9 in would be cheap but is not
required to freeze. **The proposal is approved for freezing as revision 5.**

### Advisory list (non-blocking; may be folded into implementation or a later doc pass)

* **AD1:** state that `empty_reason` is computed over the visible/filtered set (`no_run_shaped_files` when the
  visible run-shaped count is 0), so no `acl` caller can infer hidden-record existence from it. (§2 214–216)
* **AD2:** state whether `ignored_samples[].relative_path` is filtered in `acl`; if not, note that sample
  paths are root diagnostics that may contain operator directory names. (§2 190–195, 169–173)
* **AD3:** align line 518's "never causes a filesystem read" with lines 548–550 ("before any *record* is
  read"). (§7)
* **AD4:** change the stage-C range "rows 8–16" to "rows 8–17". (§7 515–516)
* **AD5:** drop `ignored_entries` from the "other `discovery` fields" list (it is top-level). (§2 194)
* **AD6:** update the two script docstrings ("rev-2") and the README HEAD label to rev 5. (dry-run dir)
* **AD7:** note that the 30 recorded-no-pack Context values have four key-shapes, not just
  `db_path`/`processes`. (§1.1 95–96)
* **AD8:** optionally define the name-only fields (`corrupt_count`, `duplicate_count`, `case_counts`,
  `bytes` vs `declared_bytes`, `digests.original`/`masked`, `sections[].value?`,
  `discovery.root_present`/`scanned_dirs`/`scanned_files`).
* **AD9:** reword the `manifest_status: null` justification so it does not claim a duplicate row could not be
  parsed. (§2 183–184)

---

## Appendix A — exact commands run (verbatim key output)

Run from `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01` unless a `cd` says otherwise. All
reads were read-only; scratch stdout was captured under `/tmp` only (no repo writes).

```bash
# --- baseline integrity ---
git rev-parse HEAD                       # 0f7b2ec60847308ba3a9119fdbfe7d741041b81c
git branch --show-current                # p7-01-validation-read
git status --porcelain                   # (empty)
git diff --cached --stat                 # (empty)
git diff --stat 1b9791a 0f7b2ec
#  validation-quarantine/DRYRUN-p7-01/README.md       |  94 ++-
#  .../DRYRUN-p7-01/p7-fixture-dryrun.py              |  58 +-
#  .../DRYRUN-p7-01/p7-fixture-dryrun.txt             |  12 +-
#  .../DRYRUN-p7-01/p7-real-records-dryrun.py         |  43 +-
#  .../REVIEW-p7-01-proposal/RE-REVIEW-3-findings.md  | 705 +++++++++++++++++++++
#  web/P7-01-CONTRACT-PROPOSAL.md                     | 127 ++--
#  6 files changed, 958 insertions(+), 81 deletions(-)
git diff --name-status 1b9791a 0f7b2ec
#  M README.md; M p7-fixture-dryrun.py; M p7-fixture-dryrun.txt;
#  M p7-real-records-dryrun.py; A RE-REVIEW-3-findings.md; M web/P7-01-CONTRACT-PROPOSAL.md
git log -1 --format='%H %ci %s' 0f7b2ec  # … 2026-10-08 07:13:32 -0700 P7-01 phase 1 rev 5: …
git log -1 --format='%H %ci %s' 1b9791a  # … 2026-10-08 07:06:24 -0700 P7-01 phase 1 rev 4: …
wc -l validation-quarantine/REVIEW-p7-01-proposal/*.md
#  493 findings.md; 658 RE-REVIEW-findings.md; 731 RE-REVIEW-2-findings.md; 705 RE-REVIEW-3-findings.md
```

```bash
# --- real-record facts + Context breakdown recomputed with the rev-5 rules ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
python3 - <<'PY'
import json, glob, collections, os
SENT="N/A (P0 not implemented)"
REASON_ONLY={"reason","note","detail","not_applied_reason"}
PACK={"context_id","pack","sections","rendered","content","tokens","parts"}
def presence(v, has=True, context=False):
    if not has: return ("absent",False,False)
    if v==SENT: return ("sentinel",False,False)
    if isinstance(v,dict) and set(v)<=REASON_ONLY: return ("reason_only",False,False)
    if isinstance(v,str) and v.strip()=="": return ("absent",False,False)
    if isinstance(v,list) and len(v)==0: return ("absent",False,False)
    pack=False
    if context:
        pack=(isinstance(v,dict) and bool(set(v)&PACK)) or (isinstance(v,list) and len(v)>0)
    return ("value",True,pack)
files=sorted(glob.glob("validation-runs/**/run-*.json",recursive=True))
cat=collections.Counter(); keysets=collections.Counter(); total=0; packcases=0
runverdicts=collections.Counter(); caseverdicts=collections.Counter()
failcase=0; fc=0; fo=0; ev=0; evabs=0; syn=0; markers=0
for f in files:
    d=json.load(open(f)); runverdicts[d.get("verdict")]+=1
    if "synthetic" in d: syn+=1
    for c in d["cases"]:
        total+=1; caseverdicts[c.get("verdict")]+=1
        if c.get("verdict")=="FAIL": failcase+=1
        if c.get("failure_class"): fc+=1
        if c.get("failure_output"): fo+=1
        for e in c.get("evidence",[]) or []:
            ev+=1
            if isinstance(e,str) and e.startswith("/"): evabs+=1
        v=c.get("context"); kind,present,pack=presence(v,"context" in c,context=True)
        if kind=="value" and present and not pack: cat["recorded_no_pack"]+=1
        elif kind=="value" and pack: cat["pack"]+=1; packcases+=1
        else: cat[kind]+=1
        if isinstance(v,dict):
            keysets[tuple(sorted(v))]+=1
            for k in v:
                if k in ("synthetic","simulated","fixture","mock","component") and v.get(k): markers+=1
print("files(records):",len(files)); print("total cases:",total)
print("run verdicts:",dict(runverdicts)); print("case verdicts:",dict(caseverdicts))
print("context categories:",dict(cat)); print("context key-sets:")
for k,n in keysets.most_common(): print("  ",n,k)
print("cases with pack (chain context present):",packcases)
print("FAIL cases:",failcase,"failure_class nonzero:",fc,"failure_output nonzero:",fo)
print("evidence entries:",ev,"absolute:",evabs)
print("records with synthetic key:",syn,"marker hits:",markers)
print("manifest.json count:",len(glob.glob("validation-runs/**/manifest.json",recursive=True)))
depths=collections.Counter()
for f in files:
    rel=os.path.relpath(f,"validation-runs"); depths[len(rel.split(os.sep))-1]+=1
print("run-file dir depths (below validation-runs):",dict(depths))
PY
# files(records): 9
# total cases: 63
# run verdicts: {'PASS': 3, 'BLOCKED': 6}
# case verdicts: {'PASS': 57, 'BLOCKED': 6}
# context categories: {'reason_only': 27, 'recorded_no_pack': 30, 'sentinel': 6}
# context key-sets:
#    27 ('reason',)
#    9 ('db_path', 'processes', 'reason')
#    9 ('reason', 'scratch_db')
#    9 ('audit_entries', 'audit_rows_stored', 'reason')
#    3 ('actor_id', 'core_url', 'db_path', 'host_id', 'reason', 'source_session_id')
# cases with pack (chain context present): 0
# FAIL cases: 0  failure_class nonzero: 0  failure_output nonzero: 0
# evidence entries: 21 absolute: 12
# records with synthetic key: 0 marker hits: 0
# manifest.json count: 0
# run-file dir depths (below validation-runs): {2: 8, 3: 1}
```

```bash
# --- D7/§8: re-run the retained evidence and diff against the captured output ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01"
python3 validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.py > /tmp/rr4-real.out 2>/tmp/rr4-real.err; echo $?   # 0
diff /tmp/rr4-real.out validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.txt; echo "real_diff_exit=$?"          # 0
PYTHONPATH=src python3 validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py > /tmp/rr4-fix.out 2>/tmp/rr4-fix.err; echo $?  # 0
diff /tmp/rr4-fix.out validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.txt; echo "fix_diff_exit=$?"                 # 0
# fixture output lines: "discovered 5  ignored 13  unreadable 1";
#   "[('/tmp/p7-fixroot/p7-unreadable', 'PermissionError')]";
#   "art_assign … looks_like_credential=False … 403 original_withheld";
#   "masked -> … db_password=[REDACTED] …"
git status --porcelain                                   # (empty) — before and after
```

```bash
# --- A7: the README's exact digest command reproduces its recorded value ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
find validation-runs -name 'run-*.json' | sort | xargs shasum -a 256 | shasum -a 256
# 1c9deec38ccf0015269f61fd3f3517aa7eb93327b86f75ffc78c3b1ee6032464  -
find validation-runs -name 'run-*.json' | wc -l          # 9
git rev-parse HEAD                                       # 7016dabb8926d6bd6599f1f56b8f39cbda253ebf
```

```bash
# --- field definitions / stale wording greps ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01"
grep -n "manifest_status" web/P7-01-CONTRACT-PROPOSAL.md        # 177 (row), 181 (definition)
grep -n "288\|188\|179" web/P7-01-CONTRACT-PROPOSAL.md; echo "exit=$?"   # exit=1 (no matches)
grep -n "can never exceed\|same four-value enum" web/P7-01-CONTRACT-PROPOSAL.md; echo "exit=$?"  # exit=1
grep -rn "rev-2" validation-quarantine/DRYRUN-p7-01/            # only the two script docstrings (line 2)
sed -n '515,516p' web/P7-01-CONTRACT-PROPOSAL.md                # stage C "rows 8–16" (stale; see D1)
```

```bash
# --- masker samples re-verified (read-only import) ---
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01"
PYTHONPATH=src python3 - <<'PY'
from jasmine_core import canonical
for name, s in {
 "bare assignment": "db_password=hunter2hunter2 in the log line",
 "quoted json assignment": '{"api_key": "abcdef1234567890"}',
 "provider bearer": "line1\nAuthorization: Bearer sk-abcdefgh12345678\nline3",
}.items():
    print(f"{name}: looks_like_credential={canonical.looks_like_credential(s)} "
          f"redact_noop={canonical.redact_text(s)==s}")
print("has REDACT_TEXT_VERSION:", hasattr(canonical,"REDACT_TEXT_VERSION"))
PY
# bare assignment:        looks_like_credential=False redact_noop=False
# quoted json assignment: looks_like_credential=False redact_noop=False
# provider bearer:        looks_like_credential=True  redact_noop=False
# has REDACT_TEXT_VERSION: False
```

## Appendix B — files written

Only this file was written:

`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-4-findings.md`

No tracked file was modified, nothing was staged, committed or pushed; HEAD remains
`0f7b2ec60847308ba3a9119fdbfe7d741041b81c` on branch `p7-01-validation-read`. Before this write,
`git status --porcelain` was empty after every command, including after re-running both retained dry-run
scripts (the fixture script wrote only `/tmp/p7-fixroot` and `/tmp/p7-outside` and restored its `chmod 000`
directory). After this write, `git status --porcelain` shows exactly one line —
`?? validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-4-findings.md` — and `git diff --stat` (tracked)
is still empty. The only
other paths I wrote were scratch stdout captures under `/tmp` (`/tmp/rr4-real.out`, `/tmp/rr4-fix.out` and
their `.err`), outside the repository.
