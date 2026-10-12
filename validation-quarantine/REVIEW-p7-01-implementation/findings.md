# P7-01 implementation review — candidate `1f7b0aff27198e673f264a86b7bb53d529604239`

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent, delegated
  independent reviewer). New agent context; not the author of the proposal, of any earlier review,
  or of the implementation.
* **Date of review run:** 2026-10-08 (host `Thu Oct  8 07:4x PDT 2026`).
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`, branch
  `p7-01-validation-read`.
* **Candidate under review:** `1f7b0aff27198e673f264a86b7bb53d529604239` (branch tip). Implementation
  commit `35f7e01`, plus `2eb1e5a` (fixture/gitignore fix), `087b298` (derived-contract freeze) and
  `1f7b0af` (real-record tests follow `P7_REAL_RUNS_ROOT`).
* **Authority:** the frozen, approved specification `web/P7-01-CONTRACT-PROPOSAL.md` revision 5 and
  `validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-4-findings.md` (APPROVE, advisory AD1–AD9),
  plus `docs/adr/0015-p7-validation-read-contract.md`.
* **Writes:** only `validation-quarantine/REVIEW-p7-01-implementation/findings.md`. No tracked file was
  modified, nothing staged, committed or pushed (Appendix B).

---

## 1. Baseline integrity

Verbatim, run from the worktree:

```
$ git rev-parse HEAD
1f7b0aff27198e673f264a86b7bb53d529604239

$ git branch --show-current
p7-01-validation-read

$ git status --porcelain          # at the start of the review
(empty)

$ git diff --stat 0f7b2ec 1f7b0af
 docs/adr/0015-p7-validation-read-contract.md       |  220 +++
 docs/api/core-api-v1.md                            |   89 ++
 scripts/p7/p7_contract.py                          |  286 ++++
 src/jasmine_core/api/handlers.py                   |    8 +-
 src/jasmine_core/auth.py                           |   14 +-
 src/jasmine_core/canonical.py                      |    7 +
 src/jasmine_core/errors.py                         |   60 +
 src/jasmine_core/validation/reads.py               | 1488 ++++++++++++++++++++
 tests/fixtures/p7/corrupt-run.json                 |    1 +
 tests/fixtures/p7/files/assign.txt                 |    1 +
 tests/fixtures/p7/files/binary.bin                 |  Bin 0 -> 4 bytes
 tests/fixtures/p7/files/good.txt                   |    1 +
 tests/fixtures/p7/files/plain.txt                  |    1 +
 tests/fixtures/p7/files/tampered-original.txt      |    1 +
 tests/fixtures/p7/files/tampered.txt               |    1 +
 tests/fixtures/p7/labelled-fail-run.json           |   36 +
 tests/fixtures/p7/manifest-escapes.json            |   22 +
 tests/fixtures/p7/no-context-run.json              |   62 +
 tests/fixtures/p7/ok-run.json                      |   33 +
 tests/test_validation_read.py                      | 1045 ++++++++++++++
 .../REVIEW-p7-01-proposal/RE-REVIEW-4-findings.md  |  632 +++++++++
 web/contracts/p7-validation-read.v1.json           |  133 ++
 22 files changed, 4137 insertions(+), 4 deletions(-)
```

**Assessment.**
* HEAD is `1f7b0af…` on branch `p7-01-validation-read`: **CONFIRMED**. The tracked tree was clean and
  nothing was staged at the start.
* **Concurrent writer observed (not mine):** part-way through this review an untracked directory
  `validation-quarantine/VALIDATE-p7-01/` (its own `findings.md`, `harness.py`, `c1_fail.py` …
  `c6_values.py`, `evidence/`) appeared, written by another agent working in the same worktree
  (timestamps 07:36–07:40). It is **not** mine and I did not read, run or modify it. The tracked tree
  stayed clean throughout, so baseline integrity holds; every command below was unaffected.
* `src/jasmine_core/validation/reads.py` is **byte-identical at the pinned ref and the tip**
  (`git diff 2eb1e5a 1f7b0af -- …/reads.py` empty), so the contract pin `2eb1e5a` describes the tip's
  reader (advisory F9).
* Every review command read only; scratch output went to `/tmp` and the scratch contract repo
  `/tmp/p7scratch`. `git status --porcelain` showed no tracked modification before or after.

---

## 2. Spec conformance, rule by rule

I walked §1–§8 of the proposal against `reads.py` and my own probes. The following are **conforming**
(evidence in §4 and Appendix A); the deviations are F1–F9 in §3.

| Rule (§) | Verdict |
| --- | --- |
| Root explicit config only, no default, lazy, cached by raw string, `O_DIRECTORY\|O_NOFOLLOW`, symlinked root refused, `reason ∈ {unset,missing,not_a_directory,symlinked_root,root_unreadable}` (§2) | **CONFORMS** — `reads.py:150-179`; probe: unset→`unset`, missing→`missing`, symlink→`symlinked_root`; a deleted-after-cache root fails closed |
| Walk: `onerror=...` (unreadable dirs counted), dot/`__pycache__` pruned, symlinked dir/file never a record, run-shaped grammar, corrupt kept (§2) | **CONFORMS** — `reads.py:199-259`; unreadable subtree ⇒ 503 `index_incomplete` with `{path,error}` samples |
| `corrupt > duplicate > ok`; only parseable records participate in duplicate detection; both duplicates listed; detail 409 on duplicates (§2) | **DEVIATES** in the mixed parseable+corrupt case — **F1** |
| Single ordering tuple `(status_rank, created_at desc, run_id stem desc, relative_path asc)`; unique tie-break; page walk complete (§2) | **CONFORMS** — `reads.py:1449-1464`; 12-row page walk over real records + corrupt + duplicate pair is duplicate-free and omission-free (test + my run) |
| `total` = filtered, corrupt/duplicate counted only when unfiltered; corrupt matches no positive filter; `count == len(runs)`; `has_more = offset+count < total` (§2) | **CONFORMS** for `total`/`runs[]`; `corrupt_count`/`duplicate_count` are **unfiltered** — **F7** |
| `ignored_entries` counts a pruned directory as one entry, contents not enumerated (§2) | **CONFORMS** — depth-pruned dir and dot/symlink dirs each +1 |
| Artifacts: per-component `dir_fd`+`O_NOFOLLOW`, `S_ISREG`, cap, read once, hash the buffer and serve the buffer, no re-open, symlink refused at open, `realpath` containment second (§3) | **CONFORMS** — `reads.py:689-769`; my race probe never served unhashed bytes |
| `form=original` gate = digest match **and** strict UTF-8 **and** `credential_shaped` false **and** `redact_text(text) == text`; `served_sha256`/`masked_sha256` never a bare `sha256` (§5) | **CONFORMS** — assignment-shaped secrets masked and original withheld 403 `mask_not_noop` |
| `present_kind`: `reason_only` only for mappings; empty string/list `absent`; `value` otherwise; `context_pack_present` rule; chain Context needs `present ∧ pack`; `interpretation` is not Resolution (§1.1, §4.1) | **CONFORMS** — `reads.py:345-372`, `424-484`; Resolution is always `present:false, source:null` |
| Grades: marker table; case first-match; section `= worst(case, marker)`; run `= worst(mode, cases)`; `grade_downgraded_by[]`; unmarked-mock limit documented (§4.2) | **CONFORMS** — `reads.py:375-399`, `519-521`, `1329-1344` |
| Errors: route-scoped first-matching-row-wins; `404 validation_run_not_found` for every unreadable/unknown run in `acl`; no `project_not_found`; 409 single_user-only; 503 `index_incomplete`; 413 on the artifact cap (§7) | **CONFORMS** except rows 4/6 on by-id (**F5**) and the acl+`validation:unattributed` 409 family (**F6**) |
| `acl` list grant-filtered over the visible set for `runs[]`/`count`/`total`/`corrupt_count`/`duplicate_count`/`run_shaped_files`; `visibility`; AD1 `empty_reason` over the visible set; AD2 `ignored_samples[]` empty (§2, ADR §5.1) | **CONFORMS** — acl detail/list probes: hidden/ungranted/nonexistent all identical `404 validation_run_not_found`; hidden-only root ⇒ `empty_reason: "no_run_shaped_files"`, `ignored_samples: []` |
| `ProjectAccess` + `deployment_mode()` + `require_mode_ready()` reused, not reinvented (§6) | **CONFORMS** — `reads.py:111-139` wraps the frozen P4 `ProjectAccess.may_read`/`require_mode_ready`; acl with no grants ⇒ `503 memory_deployment_not_ready`; no route emits `project_not_found` |
| Manifest grammar, symlink-at-open, integrity set, derived mode, attribution, `manifest_authentication` (§3) | **CONFORMS** except two string-rule gaps — **F3**; unbounded pre-cap read — **F4** |
| Caps `MAX_SCANNED_ENTRIES=20_000`, `MAX_RUN_SHAPED=500`, manifest 1 MiB / 128 files, artifact 8 MiB, limit 100 (§8) | **CONFORMS** except **F4** |

---

## 3. Findings

### F1 — MAJOR (mandatory): list says `record_status: "ok"` but detail refuses the same stem when a corrupt run-shaped file shares it

**File:line:** `src/jasmine_core/validation/reads.py:324-334` (`_mark_duplicates`, groups only
`status == "ok"`) versus `reads.py:1263-1273` (`_locate`, treats **any** `len(matches) > 1` as
ambiguous, including a corrupt sibling).

**Rule (proposal §2):**
> "`corrupt` (unparseable, wrong `run_id`, or missing a required field) > `duplicate` (parseable, but
> another run-shaped file shares its stem) > `ok`. Corrupt wins because a file that cannot be parsed
> has no `run_id` to compare, so **duplicate detection considers only parseable records**."

**Evidence** (my probe, one parseable `ok-run.json` and one `corrupt-run.json` sharing a stem in two
directories):

```
[ok+corrupt-same-stem] list=200 rows=[('run-20261008T000001Z', 'ok'), ('run-20261008T000001Z', 'corrupt')]
                       corrupt_count=1 duplicate_count=0 detail_http=409 code=validation_run_ambiguous
```

The list route certifies one row as `record_status: "ok"`; the detail route for that same run id
returns `409 validation_run_ambiguous`. The two routes disagree about whether the stem is ambiguous,
because they use different duplicate predicates. A validator doing exactly what criterion 6 asks
("read the same files directly and compare every served field") cannot fetch the row the list called
`ok`.

**Impact / calibration:** fail-closed (no data leak, no path escape), and the six named acceptance
scenarios use distinct stems, so this does not break a criterion outright — hence MAJOR, not
BLOCKING. But the status model is part of the frozen contract and this is a defect, not a preference.
The real nine records all have distinct stems, so real-record coverage does not exercise it.

**Smallest fix (either direction; the spec owner picks one and it must be applied to *both*
functions):**
* if "duplicate detection considers only parseable records" governs: in `_locate`, classify the
  stem's matches, apply the same parseable-only predicate, serve the unique parseable record, and
  raise `validation_run_ambiguous` only for ≥2 parseable candidates; or
* if `duplicate (parseable, but another run-shaped file shares its stem)` governs: make
  `_mark_duplicates` group every run-shaped file by stem and mark the parseable members `duplicate`
  when the group size is >1.

Add a regression test for the mixed pair (the current `test_corrupt_record_is_409_and_duplicate_stem_is_ambiguous`
uses a lone corrupt stem and a two-`ok` duplicate pair, so it pins neither behaviour).

### F2 — MINOR: depth cap is off by one — files in a directory four levels below the root are dropped

**File:line:** `reads.py:223-226`. When `len(rel_dir.parts) >= MAX_DEPTH` the loop appends a
`depth_cap` ignored entry and `continue`s, so the **current** directory's `filenames` are never
examined; only directories of depth ≤3 below the root contribute files.

**Rule (proposal §2):** "Recursive, **at most 4 directory levels below the root** (the nine need ≤3)".

**Evidence:**

```
[depth] dir_depth=3 http=200 total=1 ignored_entries=0 samples=[]
[depth] dir_depth=4 http=200 total=0 ignored_entries=1 samples=[{'relative_path': 'd0/d1/d2/d3', 'reason': 'depth_cap'}]
[depth] dir_depth=5 http=200 total=0 ignored_entries=1 samples=[{'relative_path': 'd0/d1/d2/d3', 'reason': 'depth_cap'}]
```

A run record whose directory path is four components below the root is not discovered, and the
directory is reported as `depth_cap`. Reach is *reduced*, never widened, and the nine real records
sit at depth 2–3, so no acceptance criterion fails.

**Smallest fix:** process `filenames` for the current directory and only prune the descent, e.g. move
the `if depth >= MAX_DEPTH:` block after the filename loop and make it `dirnames[:] = []` (with the
`depth_cap` entry) instead of `continue`; then a file at depth 4 is found and only depth 5 is pruned.

### F3 — MINOR: declared-path string rules miss empty/`.` components and a literal `..` inside a component

**File:line:** `reads.py:531-548` (`_classify_path`), specifically `parts = Path(path).parts`
(line 545). `pathlib` normalises `//`, `/./` and a trailing `/` away **before** the component check,
so those forms are never seen.

**Rule (proposal §3):** "`files[].path` is **relative** with **every component non-empty** and
contains no `..`, no backslash, no leading `/`, no NUL and no URL scheme".

**Evidence** (direct `_classify_path`, then through the API):

```
[path-rule] 'a//b'        -> (True, 'ok')
[path-rule] 'a/./b'       -> (True, 'ok')
[path-rule] 'a/b/'        -> (True, 'ok')
[path-rule] './a'         -> (True, 'ok')
[path-rule] '..%2Fb'      -> (True, 'ok')
[path-rule] 'a/..%2fb'    -> (True, 'ok')
[path-rule] '.'           -> (True, 'ok')
[escape] 'dot_component'  path='artifacts/./loot.txt'  manifest_status='frozen' manifest_http=200 ... artifact_http=404 reason='missing'
[escape] 'double_slash'   path='artifacts//loot.txt'   manifest_status='frozen' manifest_http=200 ... artifact_http=404 reason='missing'
[escape] 'percent'        path='..%2f..%2floot.txt'    manifest_status='frozen' manifest_http=200 ... artifact_http=404 reason='missing'
```

A manifest that should be wholly `corrupt` is served `frozen`; the artifact then 404s. **No escape
occurs** — the route capture is never percent-decoded, `pathlib` never collapses the literal `%2f`,
and `_read_artifact` opens the components literally, so `..%2f…` is just a filename that does not
exist. This is a spec-fidelity defect, not a security hole.

The five forms the suite *does* test (`../`, absolute, `C:`, UNC, URL) are all correctly refused.

**Smallest fix:** validate the raw string instead of `pathlib`: `components = path.split("/")`, then
reject `not components`, any `component in ("", ".", "..")`, and additionally `".." in path` and
`"\\" in path` (all before any `Path` use).

### F4 — MINOR: the manifest is read in full before its 1 MiB cap is applied (unbounded read)

**File:line:** `reads.py:573-580` — `raw = path.read_bytes()` (line 574) happens **before**
`if len(raw) > MAX_MANIFEST_BYTES:` (line 578). `_classify` has the same shape for run records
(`reads.py:276-282`), and the list route classifies every run-shaped file.

**Rule (proposal §8):** "Manifest: ≤ 1 MiB, `files[]` ≤ 128 entries. … **No route can be made to
read an unbounded amount of data**, and no route silently returns a partial index."

**Evidence** (40 MiB manifest, cap is 1 MiB):

```
[oversize-manifest] size=41943122 http=409 code=validation_manifest_corrupt rule='manifest exceeds 1048576 bytes' took=0.03s
[big-record] size=31457380 status=ok took=0.03s     # a 30 MiB run-shaped record is read whole
```

The refusal is correct, but the whole file was already resident. The root is operator configuration
(not request-controlled), so this is a robustness/spec-limit gap rather than a remote DoS, and the
`files[]`-list arm of the same attack **is** bounded (130 entries ⇒ `409 validation_manifest_corrupt
rule='files exceeds 128'`, measured).

**Smallest fix:** `st = os.stat(path)` and refuse `st.st_size > MAX_MANIFEST_BYTES` before
`read_bytes`; or read at most `MAX_MANIFEST_BYTES + 1` and refuse if the extra byte arrives. If a
run-record bound is also wanted, apply the same to `_classify`.

### F5 — MINOR: rows 4 and 6 (completeness and caps) are extended to the by-id routes

**File:line:** `reads.py:1253-1262` — when no stem matches, `_locate` raises
`validation_not_configured(reason="index_incomplete")` (line 1255) and
`ValidationIndexTooLarge` (line 1260) on **detail/manifest/artifact/content**, not only on list.

**Rule (proposal §7):** rows 4 and 6 are scoped `L`; row 9 (`D M A C`, unknown run id) is
`404 validation_run_not_found`. §2: "detail/manifest/artifact reads are unaffected because they
address a known run id and need no index."

**Evidence:**

```
[unknown+unreadable] /v1/validation/runs/run-20990101T000000Z                  -> 503 validation_not_configured index_incomplete
[unknown+unreadable] /v1/validation/runs/run-20990101T000000Z/manifest         -> 503 validation_not_configured index_incomplete
[unknown+unreadable] /v1/validation/runs/run-20990101T000000Z/artifacts/art_good           -> 503 validation_not_configured index_incomplete
[unknown+unreadable] /v1/validation/runs/run-20990101T000000Z/artifacts/art_good/content   -> 503 validation_not_configured index_incomplete
[cap+unknown-by-id] http=503 code=validation_index_too_large cap=MAX_SCANNED_ENTRIES
```

A **known** id still works when a sibling subtree is unreadable (the suite's
`test_by_id_reads_still_work_when_a_sibling_subtree_is_unreadable` passes, and I re-confirmed the
mechanism: the 503 only fires in the `if not matches:` branch). No existence oracle: the same 503
fires whether the id is hidden or absent. The frozen table's route scoping is simply not honoured
for unknown ids on an already-broken tree.

**Smallest fix:** either narrow the two branches to the list handler (by-id falls through to 404 for
an unresolvable id), or amend the ADR/§7 to state that by-id routes also return row 4/6 responses when
they cannot rule the id out; changing the code to 404 would be a lie about a possibly-existing
record, so I recommend the one-sentence amendment.

### F6 — MINOR: `acl` + `validation:unattributed` emits the `409` family the table scopes to `single_user`

**File:line:** `reads.py:1263-1273` — the `acl and not explicit_unattributed` guard *allows*
`ValidationRunAmbiguous`/`ValidationRunCorrupt` when the caller holds `validation:unattributed`.

**Rule (proposal §7):** rows 10/10b are "`single_user` mode only", with consequence (ii): "in `acl`
mode an unreadable record has already been answered by row 8, so the `409` family is **unreachable
there**".

**Evidence** (acl deployment, one grant present, a key with `validation:read` +
`validation:unattributed`):

```
[acl+unattributed] run-20261008T000004Z -> 409 validation_run_corrupt
[acl+unattributed] run-20261008T000001Z -> 409 validation_run_ambiguous
[acl+unattributed-list] http=200 total=3 corrupt=1 dup=2 statuses=['corrupt', 'duplicate']
```

Existence-oracle check: a caller **without** `validation:unattributed` gets one identical
`404 validation_run_not_found` for an ungranted project, an unattributed run, and a nonexistent id
(verified), so isolation holds. The divergence is that the frozen consequence (ii) is false once the
explicit unattributed grant exists; the code's 409 is the sensible answer, so the document should be
amended rather than the code.

**Smallest fix:** amend §7 consequence (ii) to "unreachable in `acl` unless the caller holds
`validation:unattributed`", or (if the frozen table must win) map the two branches to
`404 validation_run_not_found`.

### F7 — MINOR: `corrupt_count`/`duplicate_count` ignore the caller's filters while `total`/`runs[]` honour them

**File:line:** `reads.py:918-921` (`corrupt_count`/`duplicate_count` computed over `visible`) versus
`reads.py:929-931` (`total` over `filtered`).

**Rule (proposal §2):** "`total` is the number of discovered run-shaped records that **pass the
filters**"; the `acl` paragraph groups `corrupt_count`/`duplicate_count` with `total` as "computed
over the visible set" — i.e. the same set as the served rows.

**Evidence:**

```
[counters] unfiltered  total=2 corrupt_count=1 dup=0 runs=['ok','corrupt']
[counters] verdict=PASS total=1 corrupt_count=1 dup=0 runs=['ok']
[counters] stage=NOPE  total=0 corrupt_count=1 empty_reason='all_filtered_out'
```

With `verdict=PASS` the body reports `corrupt_count: 1` while no corrupt row exists in `runs[]`, and
with `stage=NOPE` it reports `total: 0` beside `corrupt_count: 1`. The two readings of "visible set"
(grant-visible vs visible∧filtered) differ, and the implementation is internally inconsistent.

**Smallest fix:** compute both counts over `filtered` (one line each), or state in §2 that they are
unfiltered root diagnostics. The suite's `test_filters_and_corrupt_rows` asserts the unfiltered value
only, so it passes under either reading — a missing negative.

### F8 — ADVISORY: `tests/fixtures/p7/manifest-escapes.json` is unused

`grep -rn "manifest-escapes" tests/ scripts/` is empty; the escape negatives are built inline in
`test_every_declared_path_escape_refuses_the_manifest`. Dead fixture (the earlier dry-run evidence
used it). Remove it or wire it into the test.

### F9 — ADVISORY: the contract pin is not the branch tip, and an equivalent short ref looks like drift

`web/contracts/p7-validation-read.v1.json:4` pins `source.ref = 2eb1e5a8cc31383b064d4055e884a8a10ffe0e18`,
not the tip `1f7b0af`. This is **correct** because `reads.py` is byte-identical at both
(`git diff 2eb1e5a 1f7b0af -- src/jasmine_core/validation/reads.py` empty), and default `--check`
passes. But `source.ref` is compared as a raw string, so
`--check --ref 2eb1e5a` (the short SHA of the same commit) exits 1 with `differs: source`. Purely a
readability/provenance wart; either is defensible.

---

## 4. Adversarial security testing (own scripts, not the suite)

Scratch harnesses `/tmp/p7_adv.py`, `/tmp/p7_adv2.py`, `/tmp/p7_adv3.py`, `/tmp/p7_adv4.py`, run as
`PYTHONPATH=$PWD/src:$PWD/tests python3.13 /tmp/p7_advN.py`. Raw key output:

**Declared paths** — every escape form below refused at load (`409 validation_manifest_corrupt`,
offending entry named) or served `frozen` but unopenable (`404`, `missing`); no file was read:

```
'../outside/loot.txt'      -> 409 rule="art_x: dot segment path '../outside/loot.txt'"
'artifacts/../../loot.txt' -> 409 rule="art_x: dot segment …"
'/tmp/p7-adv-loot.txt'     -> 409 rule="art_x: absolute/UNC prefix …"
'C:foo.txt' / 'C:\foo.txt' -> 409 rule="… drive-relative …"
'\\host\share\f.txt' / '//host/share/f.txt' -> 409 rule="… absolute/UNC prefix …"
'https://evil.example/x.txt' -> 409 rule="… URL scheme …"
'artifacts/x\x00.txt'      -> 409 rule="… NUL …"
'artifacts\loot.txt'       -> 409 rule="… backslash …"
''                         -> 409 rule="… empty …"
'artifacts//loot.txt', 'artifacts/./loot.txt', '..%2f..%2floot.txt',
'~/loot.txt', 'artifacts/loot.txt/'  -> frozen 200, then artifact 404 missing   (F3)
```

**Symlinks** — a symlinked `run-*.json` is not a record (`ignored_entries` +1, `symlinked_file`); a
symlinked final root component is refused (`503 … symlinked_root`); a symlinked **intermediate**
declared directory and a symlinked declared file both answer `404 artifact_not_found
reason=symlink_refused`, and `manifest.files[].integrity == "symlink_refused"`. A symlinked
*ancestor* of the configured root is followed (final component is real) — faithful to the frozen
`O_NOFOLLOW` final-component rule and to the root being operator configuration, not request input.

**TOCTOU** — 40 concurrent requests against a file flipped between two contents on another thread:

```
[toctou] outcomes={('409', 'artifact_modified'): 31, ('200', True): 9}
```

Every 200 served exactly the bytes whose SHA-256 equalled the envelope's `served_sha256`; every
mismatch was refused. The "read once, hash the buffer, serve the buffer, never re-open" rule holds
empirically.

**Ids / encodings** — `..%2f`, `%2e%2e%2f`, `<run_id>%2f..`, trailing `%00`, and
`artifacts/art_x%2f..` all answer `400 invalid_request` from the anchored grammar; a literal `../..`
normalised by the HTTP client becomes `404 route_not_found`. No path construction happened.

**Manifest size/limits** — 130-entry `files[]` ⇒ `409 validation_manifest_corrupt rule='files exceeds
128'`; a `bytes` of `10**18` is accepted as an integer and never allocated; a 40 MiB manifest is
refused but read whole first (**F4**); an artifact over the (patched) cap ⇒ `413 payload_too_large
limit=1024`; an existing-but-unreadable declared file ⇒ `503 validation_not_configured
reason=artifact_unreadable` (row 15).

**Assignment-shaped secrets** (`looks_like_credential` is `False` for all three; `redact_text` masks
in place):

```
[secret db_password]       looks_like=False masked_body='line\ndb_password=[REDACTED]\n'         original_http=403 reason=mask_not_noop
[secret json_api_key]      looks_like=False masked_body='{"api_key": "[REDACTED]"}\n'            original_http=403 reason=mask_not_noop
[secret underscore_token]  looks_like=False masked_body='private_token=[REDACTED]\n'           original_http=403 reason=mask_not_noop
```

**HTML/script with `download=1`** — `Content-Type: application/json; charset=utf-8`,
`X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`,
`Content-Disposition: attachment; filename="art_html.txt"`; the body is a JSON envelope, never a
renderable page.

**`acl` existence oracle** — grant-filtered list and detail; the three refusal classes are
byte-identical in status and code:

```
[acl-detail] nonexistent=(404,validation_run_not_found) ungranted=(404,validation_run_not_found) unattributed=(404,validation_run_not_found)
[acl-list] http=200 runs=['run-20261008T000001Z'] total=1 corrupt=0 dup=0 discovery={... 'run_shaped_files': 1 ...} empty_reason=None ignored_samples=[] visibility='grant_filtered'
[acl-hidden-only] http=200 total=0 empty_reason='no_run_shaped_files' discovery_run_shaped=0 ignored_samples=[]
[acl2] http=200 ignored_entries=2 ignored_samples=[]   # AD2: symlinked run-id-shaped filename not leaked
```

(AD1 and AD2 are implemented and verified; the second acl probe also contained a real run, which is
why its `total` is not 0 — the point is `ignored_samples == []` and no `leaf3/run-…` path in the
body.)

**Other** — `not_utf8` bytes: `form=original` ⇒ `403 original_withheld reason=not_utf8`; masked ⇒
`415 unsupported_media_type reason=not_utf8` (spec is silent on masked non-UTF-8; safe, and the
metadata route reports `digests.masked: null`, `forms: ["masked"]`). Malformed `limit`/`offset`/
`verdict`/`mode`/`attribution`/`project`/unknown query keys/`form`/`download` ⇒ `400 invalid_request`.
Unset/root reasons, corrupt 409, duplicate 409, corrupt-manifest 409 + unaddressable artifacts,
absent-manifest 200, declared-real-without-provenance unknown, and the synthetic-FAIL path were all
re-confirmed.

---

## 5. Shared-file edits

`git diff 0f7b2ec 1f7b0af` for the four shared files:

* **`errors.py` (+60):** seven new `CoreError` subclasses appended at end of file, correct statuses
  and codes (§7), matching docstrings. No existing class touched. **Minimal and additive.**
* **`auth.py` (+14):** `SCOPES` gains exactly `"validation:read"` and `"validation:unattributed"`
  (`auth.py:39-45`); one `ForbiddenActorKind` guard at `auth.py:101-105`, placed after the
  `memory:reflect` guard and before the system-only guard, in the same `kind == "agent"` style as its
  neighbours (`auth.py:87-100`). Adding scopes only *widens* the allowed-scope set at `issue_key`
  (`auth.py:71-75`); it cannot change the behaviour of any existing key (no test asserts the exact
  `SCOPES` tuple; full suite unaffected). No existing guard is reordered or weakened.
* **`canonical.py` (+7):** `REDACT_TEXT_VERSION = "1"` added next to `REDACTED` with a bump-rule
  comment. Pure constant; no behaviour change.
* **`api/handlers.py` (+8/-4):** `from ..validation import reads as validation_api` plus
  `+ validation_api.routes(Route)` in the `ROUTES` assembly and a two-line comment update. This is
  the approved §11 change and the P4 precedent. No import cycle: `reads.py` imports `..api.handlers`
  only under `TYPE_CHECKING` (`reads.py:45-49`), which is why `routes(route_cls)` takes the class.

All four are on the §11 approved change list; none exceeds it.

---

## 6. Contract fidelity

* **Faithful.** I diffed each route's actual `Response(200, {...})` keys against
  `web/contracts/p7-validation-read.v1.json` by hand: list 14/14, detail 23/23, manifest 13/13
  (union of frozen/absent/corrupt), artifact metadata 11/11, content 11/11 (union of masked/original).
  Patterns, handlers and the single `validation:read` scope match `reads.py:1470-1488`.
* **Genuinely derived.** `scripts/p7/p7_contract.py` parses the `route_cls(...)` table with a regex
  and the response keys with `ast` (`_response_keys`/`_dict_keys`, resolving conditional spreads),
  and refuses to drop an unresolvable spread (`raise SystemExit`). It is not hand-written.
* **`--check` behaves correctly.** Default (re-derives at the snapshot's pinned ref):
  `P7-01 contract snapshot matches 2eb1e5a8cc…`, exit 0. Absent ref:
  `… is absent, so the contract cannot be re-derived …`, **exit 2**. Drift: I copied the generator,
  `contract_provenance.py`, `reads.py` and the snapshot into `/tmp/p7scratch` with
  `source.ref = WORKTREE`, confirmed the pristine copy passes (exit 0), then edited **only the
  scratch copy** of `reads.py` four ways — route regex, an added response field, a changed scope, a
  dropped route — and each was caught with exit 1 (`differs: read_routes` / `read_scopes`, or the
  loud `route '…' is not a GET route in the P7-01 table`). Restored copy passes again. The checker
  cannot be made to lie.
* **ADVISORY F9:** the pin is not the tip (byte-identical `reads.py`), and an equivalent short ref
  reads as `differs: source`.

---

## 7. Test quality

* **38 tests; the P7 file alone is green with no skips:** `Ran 38 tests ... OK` (the real-record tree
  is present, so `TheRealNineRecords` and the two real-record pagination tests actually run).
* **Real assertions, not vacuous.** The real-record test asserts exact run ids, the exact
  PASS(3)/BLOCKED(6) split, `case_counts.total == 7`, `verdict_consistent`, `manifest_status ==
  "absent"`, `len(detail["cases"]) == 7`, and the frozen three-string Context `missing_reason` set
  (`{"recorded as N/A (P0 not implemented)", "the case records a reason, not a context pack",
  "the section records run details, not a context pack"}`). The synthetic FAIL test asserts verdict,
  `failure_class`, `failure_output`, `grade_basis` and `evidence_grade`. The contract tests run the
  real subprocess (`--check`, absent ref ⇒ 2).
* **Fixtures are clearly labelled and never evidence.** `labelled-fail-run.json` carries
  `"synthetic": true` and `synthetic_note: "NOT EVIDENCE: …"`; the test asserts the API surfaces both,
  that the run grade stays `unknown`/simulated, and that the real records hold **0** FAIL cases.
* **No test asserts something the specification contradicts.** The strongest-looking ones map to
  explicit rules (`ignored_samples == []` is AD2 in ADR §5.1; `forms == ["masked","original"]` is §5;
  `served_sha256 == masked_sha256` holds because both are the masked digest).
* **Missing negatives (each maps to a finding above):** the mixed parseable+corrupt stem (**F1**);
  the depth-4 boundary (**F2**); the path forms `a//b`, `a/./b`, `..%2f` (**F3**); `corrupt_count`
  under a caller filter (**F7**); an unknown id on by-id routes over an incomplete/capped tree
  (**F5**); `acl` + `validation:unattributed` on a corrupt/duplicate record (**F6**).
* **Weak/ambiguous test:** `test_filters_and_corrupt_rows` pins only the *unfiltered*
  `corrupt_count`, so it passes under either reading of F7.
* **Dead fixture:** `manifest-escapes.json` is referenced nowhere (**F8**).

---

## 8. Regression

```
$ PYTHON=python3.13 ./scripts/run-tests.sh
...
Ran 1404 tests in 44.675s

FAILED (failures=5, errors=4, skipped=13)
```

The failure set is **pre-existing and unrelated to this slice** — all nine are in
`tests/test_p4_native_session_start.py` and `tests/test_p4_g01_v2.py`, and every traceback bottoms
out in the same environment condition, `ValueError: canonical venv launcher parent required`
(`scripts/p4-codex-hook-install.py:81`):

```
ERROR: test_changing_the_source_and_reapplying_updates_the_installed_copy (test_p4_native_session_start…)
ERROR: test_prepare_creates_two_frozen_worktrees_and_refuses_a_second_time (test_p4_g01_v2.PairPreparation…)
ERROR: test_the_event_the_matcher_and_the_command_hook_shape (test_p4_native_session_start…)
ERROR: test_the_installed_script_is_this_repositorys_hook_source (test_p4_native_session_start…)
FAIL:  test_a_foreign_binding_in_the_existing_hooks_json_is_refused (…InstallerPlanAndApply…)
FAIL:  test_a_hook_from_another_tool_is_preserved (…InstallerPlanAndApply…)
FAIL:  test_apply_is_idempotent_and_binds_an_absolute_command (…InstallerPlanAndApply…)
FAIL:  test_apply_never_touches_the_trust_store (…InstallerPlanAndApply…)
FAIL:  test_plan_writes_nothing_and_prints_the_trust_steps (…InstallerPlanAndApply…)
```

No P7 test appears in the failure set; `tests/test_validation_read.py` runs 38/38 OK in the same
invocation. The set is identical across my three full runs.

---

## 9. Verdict

**APPROVE WITH NAMED MANDATORY CHANGES.**

The slice is substantive, careful and defensible: the reader implements the frozen §1–§8 rules
almost rule-for-rule, the isolation model is genuinely fail-closed (identical
`404 validation_run_not_found` for hidden, ungranted, corrupt and nonexistent runs; AD1/AD2
implemented), the artifact path is the strongest part (per-component `dir_fd`+`O_NOFOLLOW`, one read,
hash-the-buffer-serve-the-buffer — my TOCTOU race never served unhashed bytes), the grade `min` rules
hold, the contract is genuinely AST-derived and its `--check` cannot be made to lie, and the 38 tests
are real, run against the real records, and assert exact values. All six acceptance criteria are
demonstrated; criterion 1 remains correctly BLOCKED with the unblock action recorded.

**Mandatory before this commit is accepted as the phase's implementation (one item):**

1. **F1 — make the list and detail routes use one duplicate predicate.** A stem shared by a
   parseable and a corrupt run-shaped file is currently listed as `record_status: "ok"` while detail
   answers `409 validation_run_ambiguous`; pick the spec's reading, apply it in both
   `_mark_duplicates` (`reads.py:324-334`) and `_locate` (`reads.py:1263-1273`), and add the mixed-pair
   regression test.

**Recommended (MINOR, non-blocking; fold in or record a doc amendment):** F2 depth cap off-by-one;
F3 declared-path empty/`.`/`..`-substring components; F4 manifest read before the cap; F5 rows 4/6 on
by-id routes; F6 acl+`validation:unattributed` 409 family; F7 filtered vs unfiltered
`corrupt_count`/`duplicate_count`. **Advisory:** F8 unused fixture; F9 contract pin/short-ref
wording. Every one has a smallest fix above.

If the spec owner decides the frozen table must win on F5/F6, note that F1 alone is a code defect; the
other five are spec/doc alignment or hardening.

---

## Appendix A — exact commands and verbatim key output

Run from `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01` unless stated. Scratch
output only under `/tmp` and `/tmp/p7scratch`.

```bash
# --- baseline ---
git rev-parse HEAD                              # 1f7b0aff27198e673f264a86b7bb53d529604239
git status --porcelain                          # (empty at start; later only the other agent's VALIDATE-p7-01/)
git diff --stat 0f7b2ec 1f7b0af                 # 22 files changed, 4137 insertions(+), 4 deletions(-)
git diff --stat 2eb1e5a 1f7b0af -- src/jasmine_core/validation/reads.py   # (empty: byte-identical)
grep -c "def test_" tests/test_validation_read.py                        # 38
```

```bash
# --- P7 tests alone (real-record tests run, not skipped) ---
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 -m unittest discover -s tests -t tests -p "test_validation_read.py"
# Ran 38 tests in 1.575s
# OK
```

```bash
# --- contract ---
python3.13 scripts/p7/p7_contract.py --check
#   P7-01 contract snapshot matches 2eb1e5a8cc31383b064d4055e884a8a10ffe0e18     (exit 0)
python3.13 scripts/p7/p7_contract.py --check --ref 0000000000000000000000000000000000000000
#   P7-01 contract source '000…' is absent, … A drift guard that passes because it could not look is worse than no guard.   (exit 2)
# scratch /tmp/p7scratch (source.ref=WORKTREE): pristine exit 0; route regex / response field / scope / dropped route each exit 1
```

```bash
# --- adversarial probes (scratch) ---
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 /tmp/p7_adv.py   -v   # 15 tests, one setup error in my own harness; observations below
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 /tmp/p7_adv2.py  -v
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 /tmp/p7_adv3.py
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 /tmp/p7_adv4.py
# key lines verbatim in §3 and §4 (escapes, depth, TOCTOU, ids, caps, secrets, html, acl, acl+unattributed)
```

```bash
# --- regression (three identical runs) ---
PYTHON=python3.13 ./scripts/run-tests.sh
# Ran 1404 tests in 44.675s
# FAILED (failures=5, errors=4, skipped=13)
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 -m unittest tests.test_p4_native_session_start tests.test_p4_g01_v2
# … ValueError: canonical venv launcher parent required  (scripts/p4-codex-hook-install.py:81)
```

## Appendix B — files written

Only this file was written:

`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/REVIEW-p7-01-implementation/findings.md`

No tracked file was modified, nothing was staged, committed or pushed. Scratch files were written
only to `/tmp` (`/tmp/p7_adv.py`, `/tmp/p7_adv2.py`, `/tmp/p7_adv3.py`, `/tmp/p7_adv4.py`,
`/tmp/p7scratch/**`, `/tmp/p7_regression_full.txt`, `/tmp/p7_full_fails.txt`). The untracked
`validation-quarantine/VALIDATE-p7-01/` directory that appeared during the review was written by a
different, concurrent agent; I neither read nor modified it. HEAD remains
`1f7b0aff27198e673f264a86b7bb53d529604239` on branch `p7-01-validation-read`.
