# P7-01 implementation re-review — candidate `a252097bbc46763e7920018649ccac5dad5c06fe`

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent, delegated
  independent reviewer). New agent context; not the author of the proposal, of the first
  implementation review, or of the implementation.
* **Date of review run:** 2026-10-08 (host `Thu Oct  8 08:0x PDT 2026`).
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`, branch
  `p7-01-validation-read`.
* **Candidate under review:** `a252097bbc46763e7920018649ccac5dad5c06fe` (branch tip). The fixes are
  `f0be738` ("close the implementation review's F1-F9"); `a252097` re-pins the derived contract to
  `f0be738` and changes **only** `web/contracts/p7-validation-read.v1.json`.
* **Authority:** the frozen specification `web/P7-01-CONTRACT-PROPOSAL.md` revision 5,
  `docs/adr/0015-p7-validation-read-contract.md`, and the first implementation review
  `validation-quarantine/REVIEW-p7-01-implementation/findings.md` (APPROVE WITH NAMED MANDATORY
  CHANGES, F1 mandatory, F2–F9 recommended/advisory).
* **Writes:** only this file. No tracked file was modified, nothing was staged, committed or pushed
  (Appendix B). All scratch harnesses live under `/tmp`.

---

## 1. Baseline integrity

Run verbatim from the worktree:

```
$ git rev-parse HEAD
a252097bbc46763e7920018649ccac5dad5c06fe
$ git branch --show-current
p7-01-validation-read
$ git status --porcelain          # at the start of the review
?? validation-quarantine/REVIEW-p7-01-implementation/
?? validation-quarantine/VALIDATE-p7-01/
$ git diff --stat 1f7b0af a252097
 docs/adr/0015-p7-validation-read-contract.md |  39 ++++++++
 scripts/p7/p7_contract.py                    |  10 ++
 src/jasmine_core/validation/reads.py         |  77 +++++++++++++---
 tests/fixtures/p7/manifest-escapes.json      |  22 -----
 tests/test_validation_read.py                | 133 +++++++++++++++++++++++++++
 web/contracts/p7-validation-read.v1.json     |   2 +-
 6 files changed, 246 insertions(+), 37 deletions(-)
$ git diff --name-only f0be738 a252097
web/contracts/p7-validation-read.v1.json
$ git diff --stat f0be738 a252097 -- src/jasmine_core/validation/reads.py
(empty)
```

**Assessment.**
* HEAD is `a252097…` on branch `p7-01-validation-read`: **CONFIRMED**. No tracked file is modified,
  staged or untracked-dirty. The two untracked directories are review quarantine
  (`REVIEW-p7-01-implementation/` is the first review; `VALIDATE-p7-01/` was written by another,
  concurrent agent — I did not read, run or modify it).
* The re-pin commit `a252097` touches only the contract snapshot; `reads.py` is **byte-identical** at
  `f0be738` and the tip, so the pin `source.ref = f0be738…` describes the tip's reader: **CONFIRMED**.
* The only writes any command made were to `/tmp`. `git status --porcelain` showed no tracked change
  before or after (Appendix B).

---

## 2. Closure table for F1–F9

Every row below is my own reproduction, not the commit message and not the added tests. Harnesses:
`/tmp/p7_rr_api.py` (live API), `/tmp/p7_rr_direct.py` (`_walk`/`_classify` directly, including the
pre-fix `_walk` body extracted from `1f7b0af`), `/tmp/p7_rr_f5f6.py` (F5/F6 doc accuracy). Exact
commands in Appendix A.

| # | Item | Verdict |
| --- | --- | --- |
| F1 | one duplicate predicate on list and detail | **CLOSED** |
| F2 | depth cap no longer drops depth-4 records | **CLOSED** |
| F3 | raw-string declared-path validation | **CLOSED** (spec-literal; one fail-closed over-rejection, advisory N1) |
| F4 | `stat` before read for manifest and record | **CLOSED** |
| F5 | rows 4/6 on by-id routes, documented | **CLOSED** (kept in code, ADR §5.0 claim independently verified) |
| F6 | `acl` + `validation:unattributed` 409 family, documented | **CLOSED** (kept in code, ADR §5.0 claim independently verified) |
| F7 | counters over `filtered` | **CLOSED** |
| F8 | unused `manifest-escapes.json` removed | **CLOSED** |
| F9 | generator normalises a resolvable `--ref` | **CLOSED** |

### F1 — CLOSED

The two functions now use one predicate. `reads.py:350-360`:

```python
def _mark_duplicates(records: list[_Record]) -> None:
    """Only parseable records take part: a corrupt file has no run_id to compare."""
    by_stem: dict[str, list[_Record]] = {}
    for record in records:
        if record.status == "ok":
            by_stem.setdefault(record.stem, []).append(record)
    for group in by_stem.values():
        if len(group) > 1:
```

`reads.py:1306-1322`:

```python
    parseable = [record for record in matches if record.status != "corrupt"]
    if len(parseable) > 1:
        ...
        raise errors.ValidationRunAmbiguous(
            f"{len(parseable)} run-shaped files share this stem", run_id=run_id)
    if not parseable:
        ...
        raise errors.ValidationRunCorrupt(
            matches[0].corrupt_reason or "the run record is unusable", run_id=run_id)
    record = parseable[0]
```

`_mark_duplicates` groups records whose `status == "ok"`; `_locate`'s `matches` come from
`_records_for_stem` → `_classify`, whose status is only `"ok"` or `"corrupt"` (`_mark_duplicates` is
never called on that path), so `status != "corrupt"` ≡ `status == "ok"`: **the same predicate**.
All five routes (`detail`, `manifest`, `artifact`, `content`) go through `_locate`, so they cannot
disagree with each other.

I reproduced all eight parseable/corrupt combinations over one stem (`o`=parseable, `c`=corrupt),
plus the route agreement. Verbatim (`/tmp/p7_rr_api.py F1Matrix`, 4 tests, all pass):

```
[F1] spec='o'   list=200 statuses=['ok']                                detail=200 None | manifest=200 None
[F1] spec='c'   list=200 statuses=['corrupt']                           detail=409 validation_run_corrupt | manifest=409 validation_run_corrupt
[F1] spec='oo'  list=200 statuses=['duplicate', 'duplicate']            detail=409 validation_run_ambiguous | manifest=409 validation_run_ambiguous
[F1] spec='oc'  list=200 statuses=['corrupt', 'ok']                     detail=200 None | manifest=200 None
[F1] spec='cc'  list=200 statuses=['corrupt', 'corrupt']                detail=409 validation_run_corrupt | manifest=409 validation_run_corrupt
[F1] spec='ooc' list=200 statuses=['corrupt', 'duplicate', 'duplicate'] detail=409 validation_run_ambiguous | manifest=409 validation_run_ambiguous
[F1] spec='occ' list=200 statuses=['corrupt', 'corrupt', 'ok']          detail=200 None | manifest=200 None
[F1] spec='ooo' list=200 statuses=['duplicate', 'duplicate', 'duplicate'] detail=409 validation_run_ambiguous | manifest=409 validation_run_ambiguous
```

Both directions of the mandatory item hold: **a corrupt sibling no longer hides the parseable
record** (`oc`, `occ` → detail 200 and the list certifies the same stem `ok`), and **a stem whose
members are all corrupt is corrupt, not ambiguous** (`c`, `cc` → `409 validation_run_corrupt`).
The served record is the parseable one: in `oc` the single `record_status: "ok"` row's
`record_sha256` equals `sha256` of the parseable file, and the four routes
(detail/manifest/artifact/content) all answer `200` on that mixed stem. No combination produces a
list-like row that the detail route then refuses, or vice versa.

### F2 — CLOSED

`reads.py:228-256` — the current directory's `filenames` are processed before the descent is pruned,
and only the subdirectories past the cap are named:

```python
        if depth >= MAX_DEPTH:
            for name in keep:
                result.ignored.append((str(rel_dir / name), "depth_cap"))
            dirnames[:] = []
        else:
            dirnames[:] = keep
        for name in filenames:
```

Reproduction (`/tmp/p7_rr_api.py F2Depth`, 2 tests pass): a record in a directory **3** levels down
and one **4** levels down are both found, and only the depth-5 directory is pruned:

```
[F2-depth] total=2 ignored=1 samples=[{'relative_path': 'd0/d1/d2/d3/d4', 'reason': 'depth_cap'}]
           rows=[('run-20261008T000001Z','ok'), ('run-20261008T000002Z','ok')]
[F2-mixed] total=1 ignored=1 samples=[{'relative_path': 'e0/e1/e2/e3/e4', 'reason': 'depth_cap'}]
```

The first review's evidence was `dir_depth=4 http=200 total=0 ignored_entries=1 sample
'd0/d1/d2/d3'`; the same tree now yields `total=1`. The F2 test in `tests/test_validation_read.py`
asserts the same boundary (and I ran it independently of the code).

### F3 — CLOSED

`reads.py:571-579` validates the raw string:

```python
    if ".." in path:
        return False, "dot segment"
    if "//" in path:
        return False, "empty path component"
    components = path.split("/")
    if any(component in ("", ".") for component in components):
        return False, "empty or dot path component"
```

Direct matrix (`/tmp/p7_rr_api.py F3Paths`, first test) — every form the first review named as missed
is now refused at load, through the real API (`409 validation_manifest_corrupt`, rule names the
offending entry), and legitimate forms still load:

```
[F3-classify] 'a//b' -> (False, 'empty path component')     'a/./b' -> (False, 'empty or dot path component')
              'a/b/' -> (False, 'empty or dot path component')  './a' -> (False, 'empty or dot path component')
              '..%2Fb' -> (False, 'dot segment')            '.' -> (False, 'empty or dot path component')
              '..' -> (False, 'dot segment')                'a/../b' -> (False, 'dot segment')
              'artifacts/x.txt' -> (True, 'ok')             'artifacts/a b.txt' -> (True, 'ok')
              'artifacts/<unicode>.txt' -> (True, 'ok')
[F3-api] control  'artifacts/x.txt'      manifest=200 frozen artifact=200
[F3-api] space    'artifacts/a b.txt'    manifest=200 frozen artifact=200
[F3-api] unicode  'artifacts/<unicode>.txt' manifest=200 frozen artifact=200
```

The three legitimate forms the first review grouped with F3 that remain accepted (`~/loot.txt`) are
**not** spec violations: §3 forbids only `..`, backslash, leading `/`, NUL, URL scheme and empty
components, and the reader never shell-expands `~`. One new fail-closed over-rejection is recorded as
advisory **N1** (`a..b.txt`).

### F4 — CLOSED

`reads.py:605-616` (manifest) and `reads.py:293-301` (record) both `stat` before reading:

```python
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return _Manifest(status="corrupt",
                             rule=f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
```

```python
        if path.stat().st_size > MAX_RECORD_BYTES:
            return _Record(stem=path.stem, relative_path=relative_path, path=path,
                           record_sha256="", status="corrupt",
                           corrupt_reason=f"record exceeds {MAX_RECORD_BYTES} bytes")
```

Reproduced: a 1 MiB+1 manifest is refused with no digest (it was never read); a record over
`MAX_RECORD_BYTES = 16 MiB` is `corrupt` with `record_sha256: ""`; a 1.5 MiB record is read
normally and served `ok`:

```
[F4-manifest] http=409 code=validation_manifest_corrupt details={'rule': 'manifest exceeds 1048576 bytes'}
[F4-record]   rows=[('T000001Z','ok','d25a8c01'), ('T000002Z','corrupt','')]
[F4-record]   under-bound size=1500957 -> ok
```

No previously-working case fails (adversarial matrix in §3(c)): empty, FIFO, directory and symlinked
manifests all still refuse quickly, and an empty record file is still `corrupt: unparseable`.

### F5 / F6 — CLOSED (kept in code, ADR §5.0 claim verified)

The code is unchanged for F5/F6 and `docs/adr/0015-p7-validation-read-contract.md:125-149` records
both divergences. I verified the **claims**, not merely their presence
(`/tmp/p7_rr_f5f6.py`, 3 tests pass):

```
[F5] list -> 503 validation_not_configured index_incomplete
[F5] unknown / /manifest /artifacts /content -> 503 validation_not_configured index_incomplete
[F5] known id with unreadable sibling -> 200
[F5-cap] unknown by-id -> 503 validation_index_too_large {'cap': 'MAX_SCANNED_ENTRIES'}
[F6] acl key WITHOUT validation:unattributed
     corrupt -> 404 validation_run_not_found   duplicate -> 404 validation_run_not_found
     unattributed-ok -> 404 validation_run_not_found   nonexistent -> 404 validation_run_not_found
[F6] acl key WITH validation:unattributed
     corrupt -> 409 validation_run_corrupt   duplicate -> 409 validation_run_ambiguous
     unattributed-ok -> 200   nonexistent -> 404 validation_run_not_found
```

So ADR §5.0's two sentences are accurate: by-id routes do return row 4/6 when they cannot rule the
id out (and a known id is still served beside an unreadable subtree), and `acl` +
`validation:unattributed` does reach the honest `409`s while the grant-less caller keeps one
indistinguishable `404`. One nuance, not a defect: because `_locate` walks with
`stop_on_caps=False`, `_walk` never notes `MAX_RUN_SHAPED` (`reads.py:266` guards that on
`stop_on_caps`), so the by-id row-6 refusal can only ever name `MAX_SCANNED_ENTRIES`. Row 6 covers
both caps, so the ADR wording remains correct; I note it so nobody reads it as "both caps fire
by-id".

### F7 — CLOSED

`reads.py:960-964` computes both counts over `filtered`. Reproduced across every filter/offset
combination; the counts now describe the served set, are page-independent, and never contradict
`empty_reason`/`has_more`:

```
[F7] unfiltered    count=2 total=2 corrupt=1 dup=0 has_more=False empty_reason=None
[F7] verdict=PASS  count=1 total=1 corrupt=0 dup=0 has_more=False empty_reason=None
[F7] stage=NOPE    count=0 total=0 corrupt=0 dup=0 has_more=False empty_reason='all_filtered_out'
[F7] limit=1       count=1 total=2 corrupt=1 dup=0 has_more=True  empty_reason=None
[F7] offset=5      count=0 total=2 corrupt=1 dup=0 has_more=False empty_reason=None
[F7] mode=component          count=1 total=1 corrupt=0 dup=0 empty_reason=None
[F7] attribution=unattributed count=1 total=1 corrupt=0 dup=0 empty_reason=None
[F7-dup-filter] verdict=PASS total=2 dup=2 statuses=['duplicate','duplicate']
[F7-acl] total=0 corrupt=0 statuses=[] visibility=grant_filtered
```

`verdict=PASS` is the case the first review named: it no longer reports a `corrupt_count` for a row
that is not in `runs[]`. `limit=1`/`offset=5` show the counts are over `filtered`, not over the page
(consistent with `total`), and `has_more == offset + count < total` in all 7 combinations.

### F8 — CLOSED

`grep -rn "manifest-escapes" tests/ scripts/ src/ web/ docs/` is empty (exit 1), and
`git ls-files tests/fixtures/p7/` no longer lists it. The escape negatives are built inline in
`test_every_declared_path_escape_refuses_the_manifest`, which still passes. No test or script needed
the fixture.

### F9 — CLOSED

`scripts/p7/p7_contract.py:223-232` resolves a non-`WORKTREE` ref and deliberately leaves an
unresolvable one alone:

```python
    if ref != WORKTREE:
        try:
            ref = resolve_commit(ref)
        except (subprocess.CalledProcessError, ValueError):
            pass
```

```
$ python3.13 scripts/p7/p7_contract.py --check
P7-01 contract snapshot matches f0be738fb4460581733231a7b4f513272ee4f52b      (exit 0)
$ python3.13 scripts/p7/p7_contract.py --check --ref f0be738                  # short ref, same commit
P7-01 contract snapshot matches f0be738fb4460581733231a7b4f513272ee4f52b      (exit 0)
$ python3.13 scripts/p7/p7_contract.py --check --ref 2eb1e5a                  # short ref, other commit
P7-01 contract snapshot DRIFTED from 2eb1e5a8cc31383b064d4055e884a8a10ffe0e18
  differs: source                                                             (exit 1)
$ python3.13 scripts/p7/p7_contract.py --check --ref 0000000000000000000000000000000000000000
P7-01 contract source '000…' is absent, so the contract cannot be re-derived: …     (exit 2)
```

The short-ref wart is gone, real drift is still caught (`exit 1`), and the absent-ref refusal is
still loud (`exit 2`). The snapshot equals `derive(f0be738)` ignoring the stamp, and it is
self-sufficient in a fresh clone (`--check` exit 0 there, §4). `--print --ref HEAD` and
`--print --ref p7-01-validation-read` resolve to the tip SHA, and `--derived-at` still works
(`derived_at_identical: True` for `f0be738 → a252097`).

---

## 3. New defects introduced by the fixes — adversarial results

**(a) `filenames` before pruning: counters and double counting.** I extracted the exact pre-fix
`_walk` body from `1f7b0af` and ran both implementations over the same tree
(`d0/d1/d2/d3/{run-001, d4/{run-002}}`, `e0/e1/e2/e3/{run-003, .hidden/…, symdir→…,
run-005→run-003}`, `root/{notes.txt, run-006}`):

```
[walk-delta] scanned_dirs 10->10, scanned_files 2->5, scanned_entries 12->17, paths 1->3, ignored 3->5
[walk-delta] ignored-old: [('d0/d1/d2/d3','depth_cap'), ('e0/e1/e2/e3','depth_cap'), ('notes.txt','not_run_shaped')]
[walk-delta] ignored-new: [('d0/d1/d2/d3/d4','depth_cap'), ('e0/e1/e2/e3/.hidden','excluded_dir'),
                           ('e0/e1/e2/e3/run-20261008T000005Z.json','symlinked_file'),
                           ('e0/e1/e2/e3/symdir','symlinked_directory'), ('notes.txt','not_run_shaped')]
[walk-double-count] ignored entries=5 unique=5 duplicates=[]
```

`scanned_dirs` is unchanged; the only path change is the two depth-4 records that F2 was supposed to
recover (`paths 1->3`); every ignored path is counted exactly once; and the previously-swallowed
`.hidden`/`symdir` entries now carry their correct reasons instead of `depth_cap`. The counts are
internally consistent and the cap is still checked after the filename loop
(`reads.py:269-271`). **No defect.**

**(b) raw-string path check over-rejection.** `artifacts/a..b.txt` is now refused (`409
validation_manifest_corrupt`, rule `art_x: dot segment path 'artifacts/a..b.txt'`) where the pre-fix
code would have served the manifest `frozen`. Paths with a space and with non-ASCII characters are
unaffected (both `frozen` + artifact `200`). This is fail-closed and the frozen spec literally says
the path "contains no `..`", so it is not a violation — but it is a false positive for a legitimate
component: recorded as advisory **N1**.

**(c) `stat`-before-read regressions.** Matrix over every case the task named, all through the real
API and all completing fast (no hang):

```
[F4-empty]   http=409 rule='unparseable: JSONDecodeError'
[F4-fifo]    http=409 rule='manifest.json is not a regular file'
[F4-dir]     http=409 rule='manifest.json is not a regular file'
[F4-symlink] http=409 rule='manifest.json is not a regular file'
[F4-record]  under-bound size=1500957 -> ok     (a 1.5 MiB record is read normally)
[F4-statfail] _classify on a vanished path -> corrupt, 'unreadable: FileNotFoundError'
```

The `is_file()`/`exists()` ordering in `_load_manifest` is unchanged, so the new `stat` never runs for
a FIFO/directory/symlink and the post-read length check (`reads.py:618-620`) still catches a
stat→read race. **No previously-working case fails.**

**(d) `MAX_RECORD_BYTES` reachable for a legitimate record.** 16 MiB is ~3000× a real record (the
nine are a few KB) and the 1.5 MiB probe reads normally, so no legitimate record is affected. An
over-bound record's row carries `record_sha256: ""`, which is not `sha256sum` of the file; that
divergence is exactly what ADR §5.2 documents, and it fails closed while honouring §8's "no route can
be made to read an unbounded amount of data". Advisory **N2** only.

**(e) filtered counters vs `empty_reason` / `has_more`.** §2's table above enumerates every
combination: when `filtered` is empty the reason is `all_filtered_out` (or `no_run_shaped_files` when
`visible` is empty too), `total == count == 0`, both counts are `0`, and `has_more` stays
`offset < total`. When `total > 0` no `empty_reason` key is emitted. `corrupt_count +
duplicate_count <= total` in every case. `discovery.run_shaped_files` is deliberately over `visible`
(unfiltered), which is the documented ADR §5.1 reading and does not contradict `total`. **No
inconsistency.**

**(f) `--ref` normalisation.** No existing invocation breaks: `--check` default, explicit full SHA,
explicit short SHA of the pinned commit (now `0`), a short SHA of a different commit (`1`,
`differs: source`), an absent SHA (`2`, loud), `--print --ref HEAD`/branch (resolves), `--derived-at`
(still derives twice and stamps correctly). The only behaviour change is that `--write --ref <short
or branch>` now records the resolved full SHA, which is the stated intent of F9.

**(g) pre-existing, not introduced by this fix set:** a **FIFO** named `run-*.json` is treated as a
record by `_walk` (`candidate.is_symlink()` is the only file-type test, `reads.py:261`) and
`_classify`'s `read_bytes()` blocks opening it — I confirmed `_classify` did not return within 6 s.
The pre-fix code also called `read_bytes()` unconditionally and also admitted the FIFO, so this is
**not** a regression; §2 does say "only regular files are considered", so a walk-level `S_ISREG`
test would close it. Advisory **N3**.

**(h) pre-existing, minor:** duplicate rows match `verdict`/`stage` filters but never
`mode`/`attribution`, because those row fields are `null` for a non-`ok` row. Defensible against the
served row values, and untouched by these fixes. Advisory **N4**.

**(i) test hygiene:** the new `test_f3_raw_declared_path_components_refuse_the_manifest` builds its
run ids from `hash(declared) % 1_000_000`, so they vary with `PYTHONHASHSEED`; two of the six could in
principle collide (~1.5e-5) and turn the expected `409 validation_manifest_corrupt` into
`409 validation_run_ambiguous`. Advisory **N5**.

**Nothing found that makes the slice wrong, insecure, or untestable against the six acceptance
criteria.**

---

## 4. Regression

Fresh clone of the branch at `a252097` (`/tmp/p7-rr-clone`, clean tree), so the committed set is
proven self-sufficient:

```
CLONE HEAD: a252097bbc46763e7920018649ccac5dad5c06fe
CLONE STATUS: 0 modified
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 -m unittest discover -s tests -t tests -p "test_validation_read.py"
Ran 47 tests in 1.841s
OK
python3.13 scripts/p7/p7_contract.py --check
P7-01 contract snapshot matches f0be738fb4460581733231a7b4f513272ee4f52b        (exit 0)
PYTHON=python3.13 ./scripts/run-tests.sh
Ran 1413 tests in 43.988s
FAILED (failures=5, errors=4, skipped=16)
```

```
ERROR: test_prepare_creates_two_frozen_worktrees_and_refuses_a_second_time (test_p4_g01_v2)
ERROR: test_changing_the_source_and_reapplying_updates_the_installed_copy (test_p4_native_session_start)
ERROR: test_the_event_the_matcher_and_the_command_hook_shape (test_p4_native_session_start)
ERROR: test_the_installed_script_is_this_repositorys_hook_source (test_p4_native_session_start)
FAIL:  test_a_foreign_binding_in_the_existing_hooks_json_is_refused (…InstallerPlanAndApply)
FAIL:  test_a_hook_from_another_tool_is_preserved (…InstallerPlanAndApply)
FAIL:  test_apply_is_idempotent_and_binds_an_absolute_command (…InstallerPlanAndApply)
FAIL:  test_apply_never_touches_the_trust_store (…InstallerPlanAndApply)
FAIL:  test_plan_writes_nothing_and_prints_the_trust_steps (…InstallerPlanAndApply)
```

The failure set is exactly the nine **pre-existing, unrelated P4 environment failures** the first
review reported (tracebacks bottom out in `ValueError: canonical venv launcher parent required`,
`scripts/p4-codex-hook-install.py:81`). `grep -c validation` over the failure lines is `0`; the P7
module is green inside the same invocation. A second full run in the worktree gives the identical
result (`Ran 1413 tests in 44.669s`, `failures=5, errors=4, skipped=13`, same nine names), so the
`+9` tests are the new `ReviewRegression` cases and the clone's `+3` skips are environmental (the
clone has no local `p3-checkpoint-context-resume` ref / pinned tokenizer assets — none is a P7 test).
Test count: `grep -c "def test_" tests/test_validation_read.py` = 47 (was 38).

---

## 5. Verdict

**APPROVE — accept `a252097bbc46763e7920018649ccac5dad5c06fe` as the phase's implementation commit.**

The single mandatory item (F1) is genuinely closed, in both required directions, at the predicate
level and through all five routes; F2, F3, F4, F7, F8 and F9 are closed with independent
reproductions; F5 and F6 are correctly kept in code with ADR §5.0 claims I verified rather than
trusted. The regression is clean: 47/47 P7 tests and only the nine pre-existing P4 failures, in a
fresh clone. I found no defect introduced by the fixes that affects correctness, security or
testability, and I am not manufacturing a further round.

**Advisory (non-blocking, no change demanded):**

* **N1** — `_classify_path`'s `".." in path` substring test refuses a legitimate component such as
  `artifacts/a..b.txt` (409 instead of frozen). Fails closed and the spec says "contains no `..`", so
  it is defensible; a component-equality test (`".."` as a whole component) would implement the
  intent without the false positive.
* **N2** — an over-16-MiB record is `corrupt` with `record_sha256: ""`, so criterion 6's
  "recompute the raw digest" would disagree for such a record. Documented in ADR §5.2; it is the
  bound that makes §8's "no unbounded read" true, and no real record is anywhere near it.
* **N3** (pre-existing, not introduced) — a FIFO named `run-*.json` is admitted by `_walk` and blocks
  `_classify` opening it; a walk-level regular-file test would close it and honour §2's "only regular
  files are considered".
* **N4** (pre-existing, minor) — duplicate rows do not match the `mode`/`attribution` filters; their
  row fields are `null`, so this is consistent with the served row.
* **N5** — the new F3 test derives run ids from `hash(...)`, making them `PYTHONHASHSEED`-dependent
  with a ~1.5e-5 collision chance between two declared-path cases; use fixed ids.

Appendix A (commands and verbatim key output) and Appendix B (writes) follow.

---

## Appendix A — exact commands and verbatim key output

Run from `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01` unless stated. Scratch
output only under `/tmp`.

```bash
# --- baseline ---
git rev-parse HEAD                              # a252097bbc46763e7920018649ccac5dad5c06fe
git branch --show-current                       # p7-01-validation-read
git status --porcelain                          # only the two untracked quarantine dirs
git diff --stat 1f7b0af a252097                 # 6 files changed, 246 insertions(+), 37 deletions(-)
git diff --name-only f0be738 a252097            # web/contracts/p7-validation-read.v1.json
git diff --stat f0be738 a252097 -- src/jasmine_core/validation/reads.py   # (empty: byte-identical)
git diff --stat f0be738 HEAD -- src/jasmine_core/validation/reads.py      # (empty)
grep -c "def test_" tests/test_validation_read.py                         # 47
grep -rn "manifest-escapes" tests/ scripts/ src/ web/ docs/               # (exit 1: no references)
git ls-files tests/fixtures/p7/                 # manifest-escapes.json absent
```

```bash
# --- contract (F9) ---
python3.13 scripts/p7/p7_contract.py --check                                    # matches f0be738…, exit 0
python3.13 scripts/p7/p7_contract.py --check --ref f0be738                      # matches f0be738…, exit 0
python3.13 scripts/p7/p7_contract.py --check --ref 2eb1e5a                      # DRIFTED, differs: source, exit 1
python3.13 scripts/p7/p7_contract.py --check --ref 0000000000000000000000000000000000000000   # exit 2
python3.13 scripts/p7/p7_contract.py --print --ref f0be738 > /tmp/p7_rr_derived_full.json
#   with contract_provenance.without_derived_at: frozen == derived, source.ref both f0be738…
python3.13 scripts/p7/p7_contract.py --print --ref f0be738 --derived-at a252097
#   {'ref': 'f0be738…', 'derived_at_ref': 'a252097…', 'derived_at_identical': True, 'derived_at_differences': []}
```

```bash
# --- independent API probes (F1/F2/F3/F4/F7) ---
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 /tmp/p7_rr_api.py -v        # Ran 17 tests ... OK
#   verbatim F1 matrix, F2 boundary, F3 matrix, F4 bounds and F7 combinations in §2
# --- independent direct probes (F2 counters, FIFO/dir records) ---
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 /tmp/p7_rr_direct.py
#   [walk-delta] scanned_dirs 10->10, scanned_files 2->5, scanned_entries 12->17, paths 1->3, ignored 3->5
#   [walk-double-count] ignored entries=5 unique=5 duplicates=[]
#   [fifo-record] _classify DID NOT RETURN within 6s (blocked opening the FIFO)
# --- independent F5/F6 doc-accuracy probes ---
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 /tmp/p7_rr_f5f6.py -v       # Ran 3 tests ... OK
#   verbatim F5/F6 output in §2
```

```bash
# --- module and regression ---
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 -m unittest discover -s tests -t tests \
    -p "test_validation_read.py"                                            # Ran 47 tests ... OK
rm -rf /tmp/p7-rr-clone && git clone -q --branch p7-01-validation-read \
    "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1" /tmp/p7-rr-clone
cd /tmp/p7-rr-clone && git rev-parse HEAD                                   # a252097…, 0 modified
PYTHONPATH="$PWD/src:$PWD/tests" python3.13 -m unittest discover -s tests -t tests \
    -p "test_validation_read.py"                                            # Ran 47 tests ... OK
python3.13 scripts/p7/p7_contract.py --check                                # matches f0be738…, exit 0
PYTHON=python3.13 ./scripts/run-tests.sh                                    # Ran 1413 tests in 43.988s
                                                                            # FAILED (failures=5, errors=4, skipped=16)
cd "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01"
PYTHON=python3.13 ./scripts/run-tests.sh                                    # Ran 1413 tests in 44.669s
                                                                            # FAILED (failures=5, errors=4, skipped=13)
```

## Appendix B — files written

Only this file was written:

`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01/validation-quarantine/REVIEW-p7-01-implementation/RE-REVIEW-findings.md`

No tracked file was modified, nothing was staged, committed or pushed. Scratch harnesses were written
only to `/tmp` (`/tmp/p7_rr_api.py`, `/tmp/p7_rr_direct.py`, `/tmp/p7_rr_f5f6.py`,
`/tmp/p7_rr_derived_full.json`, `/tmp/p7_rr_suite.txt`, `/tmp/p7_rr_suite_worktree.txt`,
`/tmp/p7-rr-clone/**`). The untracked `validation-quarantine/VALIDATE-p7-01/` directory belongs to a
different, concurrent agent; I neither read nor modified it. HEAD remains
`a252097bbc46763e7920018649ccac5dad5c06fe` on branch `p7-01-validation-read`.
