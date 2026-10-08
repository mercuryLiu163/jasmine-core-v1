# P7-01 final review — candidate `0ae80411824739b11485c61ed388d86093f2be7c`

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent, delegated
  independent reviewer). This is the **third and final** review round for the phase: a fresh agent
  context that is not the author of the proposal, the implementation, review 1, or review 2.
* **Date of review run:** 2026-10-08 (host `Thu Oct  8 07:57:47 PDT 2026`).
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`, branch
  `p7-01-validation-read`.
* **Candidate under review:** `0ae80411824739b11485c61ed388d86093f2be7c` (branch tip). Its parent
  `46a33d7` closes review 2's N3 and N5; `0ae8041` changes **only** `web/contracts/p7-validation-read.v1.json`
  to re-pin the derived snapshot to `46a33d7`.
* **Authority:** the frozen specification `web/P7-01-CONTRACT-PROPOSAL.md` revision 5,
  `docs/adr/0015-p7-validation-read-contract.md`, review 1
  `validation-quarantine/REVIEW-p7-01-implementation/findings.md`, and review 2
  `validation-quarantine/REVIEW-p7-01-implementation/RE-REVIEW-findings.md`.
* **Writes:** exactly one new file — this one. No tracked file was modified, nothing was staged,
  committed or pushed. All scratch harnesses and both fresh clones live under `/tmp/p7-final/`
  (Appendix B).

---

## 1. Baseline integrity

Run verbatim from the worktree:

```
$ git rev-parse HEAD
0ae80411824739b11485c61ed388d86093f2be7c
$ git rev-parse --abbrev-ref HEAD
p7-01-validation-read
$ git status --porcelain
?? validation-quarantine/REVIEW-p7-01-implementation/
?? validation-quarantine/VALIDATE-p7-01/
$ git diff --stat a252097 0ae8041
 docs/adr/0015-p7-validation-read-contract.md | 17 +++++++++++++++
 src/jasmine_core/validation/reads.py         |  7 ++++++
 tests/test_validation_read.py                | 32 ++++++++++++++++++++++++++--
 web/contracts/p7-validation-read.v1.json     |  2 +-
 4 files changed, 55 insertions(+), 3 deletions(-)
$ git diff --name-only HEAD          # (empty: no tracked file modified)
$ grep -c "def test_" tests/test_validation_read.py
48
```

**Assessment.**
* HEAD is `0ae8041…` on branch `p7-01-validation-read`; the tree is clean apart from the two
  untracked review-quarantine directories. **CONFIRMED.**
* `git diff --stat a252097 0ae8041` is exactly the expected four files (`reads.py`, the test file,
  the ADR, the re-pinned contract JSON). **CONFIRMED.**
* The behavioural diff is exactly one hunk (Appendix A): a regular-file test in `_walk`.

---

## 2. Verification of the N3 fix (independent)

### 2.1 The hazard was real — reproduced pre-fix

Fresh clone checked out at the previously-approved `a252097` (pre-fix), a FIFO named `run-*.json`
in a run root, and the real API driven with a 3-second client timeout:

```
[PREFIX] GET /v1/validation/runs -> 'TimeoutError' after 3.01s (timed out)
Ran 1 test in 3.044s
```

The pre-fix list route **never answered** — `_walk` admitted the FIFO into `result.paths` and
`_classify` opened it with `read_bytes()`, which blocks forever on a FIFO with no writer. This is a
one-request denial of service (the request thread is stuck permanently), exactly as review 2's N3
stated.

### 2.2 The candidate closes it, and the mechanism is where it claims

Direct walk-level check (no HTTP), candidate clone:

```
paths admitted   : ['run-20261008T040002Z.json']
ignored          : [('run-20261008T040001Z/run-20261008T040001Z.json', 'not_regular_file')]
FIFO in paths    : False
regular in paths : True
DIRECT WALK OK: FIFO refused before _classify; regular file admitted
```

The FIFO is never appended to `walk.paths`, and `_classify` is only ever called over `walk.paths`
(`reads.py:948`, `reads.py:372`), so **no open is attempted** — the refusal is genuinely *before*
`_classify`, as the code comment claims.

API-level check against the candidate (live server, FIFO plus one ordinary record in the same root):

```
[N3] list   status=200 elapsed=0.008s rows=['run-20261008T000001Z']
[N3] detail status=404 elapsed=0.002s
[N3] not_regular_file samples=[{'relative_path': 'p7-fifo/run-20261008T010001Z/run-20261008T010001Z.json',
                               'reason': 'not_regular_file'}]
```

* the request **answers** (0.008 s list, 0.002 s detail — not 10 s timeout);
* the FIFO is counted in `ignored_samples[]` with reason `not_regular_file`;
* the ordinary record in the same root is still served (`rows == [RUN_ONE]`, `corrupt_count == 0`);
* the FIFO run id answers `404 validation_run_not_found` on detail, not a hang.

The committed regression test `ReviewRegression.test_n3_a_non_regular_run_shaped_entry_is_ignored_not_opened`
asserts exactly this shape and is a **genuine** regression test: reverting the hunk re-creates the
pre-fix hang demonstrated in 2.1.

### 2.3 No over-rejection introduced

Every adjacent shape was driven through the real API (Appendix A). Results:

| Shape named `run-*.json` | Observed classification | Sensible? |
| --- | --- | --- |
| ordinary regular record | admitted, `record_status: "ok"`, served | yes |
| **empty regular file** | admitted, `record_status: "corrupt"`, `corrupt_count: 1` | yes — not skipped |
| **symlink to a regular file** | `ignored_samples` reason `symlinked_file`, no row | yes — still refused |
| **symlink to a FIFO** | `ignored_samples` reason `symlinked_file`, no row | yes — never opened |
| **symlinked directory** named `run-*.json` | reason `symlinked_directory`, not traversed | yes |
| **directory** named `run-*.json` | not a record; its contents are walked and reported `not_run_shaped`; detail 404 | yes |
| **FIFO** | reason `not_regular_file`, no row, fast | yes (the fix) |
| **Unix socket** | reason `not_regular_file`, no row, fast | yes |
| **hardlink to a regular file** | admitted as a regular record (classified `corrupt` only because its `run_id` differs from its stem) | yes — hardlinks are regular files |

No case is over-rejected: an empty regular file is still classified `corrupt` rather than skipped,
and a hardlink is still a record.

### 2.4 Ordering of the symlink and regular-file tests

The walk tests, in order: `RUN_FILE_RE` match → `candidate.is_symlink()` → `not candidate.is_file()`
(`reads.py:261-270`). This order is **correct and necessary**:

* `Path.is_file()` **follows** symlinks, so a symlink to a regular file would return `True`. The
  symlink test must therefore run first — and it does. If the two were swapped, a
  `run-*.json` symlink pointing at an attacker-chosen regular file would be admitted as a record,
  which §2 forbids and which the pre-fix code already correctly refused.
* A symlink to a FIFO is caught by the symlink test first, so it is reported `symlinked_file` and
  never reaches the stat-then-open path.
* A symlink to a directory is classified by `os.walk` as a directory entry and refused in the
  `dirnames` loop as `symlinked_directory`.
* A broken symlink is `is_symlink() == True` → `symlinked_file` (verified reasoning; no open).

I found no ordering case that is misclassified.

---

## 3. Verification of the N5 fix (deterministic ids)

The F3 regression test now derives ids as `run-20261008T{100000 + index:06d}Z` instead of
`abs(hash(declared))`. The six derived ids are fixed and distinct:

```
[N5] PYTHONHASHSEED=0 ids=['run-20261008T100000Z', 'run-20261008T100001Z', 'run-20261008T100002Z',
                           'run-20261008T100003Z', 'run-20261008T100004Z', 'run-20261008T100005Z']
```

The committed test itself was run under two different hash seeds and passes identically:

```
$ PYTHONHASHSEED=0     ... -m unittest -v test_validation_read.ReviewRegression.test_f3_raw_declared_path_components_refuse_the_manifest
Ran 1 test in 0.060s
OK
$ PYTHONHASHSEED=12345 ... (same command)
Ran 1 test in 0.058s
OK
```

`grep -n "hash(" tests/test_validation_read.py` now matches only the explanatory comment, so no
`hash()`-derived value remains. The six ids match `^run-[0-9]{8}T[0-9]{6}Z$` and cannot collide.

---

## 4. Change isolation and fresh-clone regression

### 4.1 The only behavioural change is the regular-file rule

`git diff a252097 0ae8041 -- src/jasmine_core/validation/reads.py` is exactly the seven-line
addition in `_walk` quoted in Appendix A. `git diff --name-only 46a33d7 0ae8041` is only
`web/contracts/p7-validation-read.v1.json`, and `git diff --stat 46a33d7 HEAD -- src/jasmine_core/validation/reads.py`
is empty — so the re-pin's `source.ref` (`46a33d78…`) describes a reader that is byte-identical to
the candidate's. `python3.13 scripts/p7/p7_contract.py --check` answers
`P7-01 contract snapshot matches 46a33d78d03e4d8d9711c96ded62072de8a276be`, exit 0.

### 4.2 Fresh clone: P7 module

Fresh clone at the candidate (no local mutations):

```
$ PYTHONPATH=src:tests python3.13 -m unittest test_validation_read
Ran 48 tests in 1.903s
OK
```

48 tests, **zero skips** — the `TheRealNineRecords` cases ran against the real
`jasmine-core-v1/validation-runs` tree rather than skipping, and criterion 1's BLOCKED assertion
passed. Test count rose 47 → 48, which is exactly the new N3 test.

### 4.3 Fresh clone: full suite, and the failure set is pre-existing

Both the pre-fix commit `a252097` and the candidate were run in **separate fresh clones** with
`PYTHON=python3.13 ./scripts/run-tests.sh`:

```
pre-fix  a252097: Ran 1413 tests in 44.756s | FAILED (failures=5, errors=4, skipped=16)
candidate 0ae8041: Ran 1414 tests in 44.762s | FAILED (failures=5, errors=4, skipped=16)
```

The nine failing/erroring names were extracted with `grep -E "^(FAIL|ERROR):"` and compared with
`diff`; the sets are **byte-identical**:

```
1 ERROR: test_changing_the_source_and_reapplying_updates_the_installed_copy (test_p4_native_session_start…)
1 ERROR: test_prepare_creates_two_frozen_worktrees_and_refuses_a_second_time (test_p4_g01_v2.PairPreparation…)
1 ERROR: test_the_event_the_matcher_and_the_command_hook_shape (test_p4_native_session_start…)
1 ERROR: test_the_installed_script_is_this_repositorys_hook_source (test_p4_native_session_start…)
1 FAIL:  test_a_foreign_binding_in_the_existing_hooks_json_is_refused (test_p4_native_session_start…)
1 FAIL:  test_a_hook_from_another_tool_is_preserved (test_p4_native_session_start…)
1 FAIL:  test_apply_is_idempotent_and_binds_an_absolute_command (test_p4_native_session_start…)
1 FAIL:  test_apply_never_touches_the_trust_store (test_p4_native_session_start…)
1 FAIL:  test_plan_writes_nothing_and_prints_the_trust_steps (test_p4_native_session_start…)
```

Every failure is in P4 native-session / frozen-worktree environment tests, none touches
`src/jasmine_core/validation/` or the P7 routes, and the sets are identical before and after the
only behavioural change. The candidate introduces **no** regression; the `+1` test is the new N3
case.

### 4.4 Security boundary is unchanged

The declared-artifact reader (`_read_declared`, `reads.py:743-…`) independently rejects `.`/`..`/empty
components and requires `S_ISREG` behind a descriptor-relative `O_NOFOLLOW|O_NONBLOCK` walk. The N3
walk rule is therefore an additional fail-closed guard, not the security boundary, and the change
cannot weaken artifact containment.

---

## 5. Advisory dispositions

### N1 — literal `..`-substring rejection (ADR §5.3, bullet 1)

**Independently reproduced.** A manifest declaring the legitimate name `artifacts/note..txt` is
refused:

```
[N1] GET manifest -> 409 validation_manifest_corrupt rule=art_note: dot segment path 'artifacts/note..txt'
```

**Disposition: accurate and acceptable; I consider the literal reading defensible.** The frozen
spec's own words are that a declared path "contains no `..`". The code implements that sentence
literally (`if ".." in path`, `reads.py:580`). It fails **closed**, and — importantly — the actual
containment boundary is the descriptor-relative artifact walk at `reads.py:747`, which rejects a
`..` **component** regardless; the load-time substring test is belt-and-braces. So the only cost is
a narrow functional false negative (`artifacts/note..txt` cannot be frozen), never a security
weakness. A per-component `part == ".."` test would be a behaviour improvement and a reasonable
follow-up, but it is a specification-wording question, not a defect that makes this slice wrong,
insecure or untestable. **Not mandatory.**

### N2 — a >16 MiB record is `corrupt` with `record_sha256: ""` (ADR §5.2)

**Independently reproduced.** A record of `MAX_RECORD_BYTES + 1` bytes:

```
[N2] status=200 record_status=corrupt served_sha='' direct_sha=c283211e64a4b099… corrupt_reason=record exceeds 16777216 bytes
```

**Disposition: accurate and acceptable.** The direct file digest is non-empty while the served
digest is empty, so criterion 6's "recompute the raw digest" is not literally satisfied for such a
record. But §8 requires that no route read an unbounded amount of data, the list route classifies
every record on every request, the bound is 16 MiB against real records of a few kilobytes, and the
behaviour is documented. **Not mandatory.**

### N4 — duplicate rows carry no `mode`/`attribution`, so those filters never match them (ADR §5.3, bullet 3)

**Independently reproduced.** Two parseable records sharing a stem produce two duplicate rows with
null mode/attribution, while a `mode`/`attribution` filter excludes them:

```
[N4] all=200 dup_rows=[('run-20261008T030001Z', None, None), ('run-20261008T030001Z', None, None)]
[N4] mode=component total=1 ids=['run-20261008T000001Z']
[N4] attribution=unattributed total=1 ids=['run-20261008T000001Z']
```

**Disposition: accurate and acceptable.** A duplicate stem has no single manifest, so it has no
mode and no attribution; the served row also reports `mode: null`/`attribution: null`, so the
filter behaviour is consistent with the row it excludes. `verdict`/`stage` filters still match
duplicate rows because a duplicate is parseable — the ADR bullet claims only `mode`/`attribution`/
`project`, so it is not over-broad. **Not mandatory.**

### Additional advisories found this round (none blocking)

* **A1 — `is_file()` swallows `OSError`.** `Path.is_file()` returns `False` both for a genuine
  non-regular entry and for a `stat` failure (e.g. a permission or race error), so such an entry is
  now reported `not_regular_file` rather than the `corrupt`/`unreadable` classification the pre-fix
  code produced. It fails closed and cannot be reached without the caller already being unable to
  read the entry; a `try/except OSError` around an explicit `stat.S_ISREG(path.stat().st_mode)`
  would label it more precisely. Cosmetic.
* **A2 — the walk-time rule is not a lock.** A regular file swapped for a FIFO *between* the
  `is_file()` stat and `_classify`'s `read_bytes()` could still block that one request. This requires
  concurrent write access to the trusted operator root and is not a regression (pre-fix it was
  unconditionally reachable). `_read_declared` already opens with `O_NONBLOCK`, so the same trick in
  `_classify` would close even the race. Optional hardening.
* **A3 — ADR §5.3 heading nit.** "Two deliberate over-rejections, and the non-regular-file rule"
  introduces three bullets, and the non-regular-file rule is spec-conformant (§2: "only regular
  files are considered") rather than an over-rejection. The content of all three bullets is
  accurate; only the heading's count is loose.
* **A4 — test portability nit.** The committed N3 test calls `os.mkfifo` before its `try/except`,
  so on a platform without `os.mkfifo` it raises `AttributeError` instead of skipping. The suite
  already requires POSIX (`dir_fd`, `O_NOFOLLOW`), so this is theoretical.

---

## 6. Verdict

**APPROVE — accept `0ae80411824739b11485c61ed388d86093f2be7c` as the final phase candidate.**

Reasoning, stated plainly:

1. **N3 is genuinely fixed, verified independently, and the hazard was real.** I reproduced the
   pre-fix timeout, showed the refusal now happens in `_walk` before any open, and confirmed the
   request answers in milliseconds while a sibling record is still served. The symlink/regular test
   order is correct and necessary, and no adjacent shape (empty regular file, symlink, symlinked
   directory, directory, socket, hardlink) is over-rejected.
2. **N5 is fixed.** The ids are fixed, distinct and hash-seed independent; the test passes under two
   seeds; no `hash()`-derived value remains.
3. **The change is isolated and regression-free.** The only behavioural edit is the seven-line
   regular-file rule; the contract re-pin is correct (`--check` exit 0); a fresh-clone run gives
   `Ran 48 tests … OK` with zero skips, and the full suite's nine failures are byte-identical to the
   pre-fix commit's (all P4 environment tests).
4. **The slice remains correct, secure and testable against the six acceptance criteria.** The P7
   module exercises criteria 2–6 (real-nine records, paired authorisation, artifact negatives,
   pagination/corrupt rows, served-value equality) and asserts criterion 1's documented BLOCKED
   state with its unblock action; criterion 1's unblock is a data-recording task owned by the P7
   owner, not a code defect. The declared-artifact containment boundary is untouched.

Everything in §5 is advisory only and is collected for a possible follow-up task; none of it is a
condition of this approval.

---

## Appendix A — exact commands and verbatim key output

Unless stated, run from
`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`. Scratch and clones live under
`/tmp/p7-final/`.

```bash
# --- baseline ---
git rev-parse HEAD                              # 0ae80411824739b11485c61ed388d86093f2be7c
git rev-parse --abbrev-ref HEAD                 # p7-01-validation-read
git status --porcelain                          # only the two untracked quarantine dirs
git diff --stat a252097 0ae8041                 # 4 files, 55 insertions(+), 3 deletions(-)
git diff --name-only 46a33d7 0ae8041            # web/contracts/p7-validation-read.v1.json
git diff --stat 46a33d7 HEAD -- src/jasmine_core/validation/reads.py   # (empty: byte-identical)
git diff --name-only HEAD                       # (empty: no tracked change)
grep -c "def test_" tests/test_validation_read.py   # 48

# --- the single behavioural hunk ---
git diff a252097 0ae8041 -- src/jasmine_core/validation/reads.py
# @@ -261,6 +261,13 @@ def _walk(...)
#              if candidate.is_symlink():
#                  result.ignored.append((str(rel_dir / name), "symlinked_file"))
#                  continue
# +            # N3: §2 says "only regular files are considered". `is_file()` is a
# +            # stat, so a FIFO, socket or device named `run-*.json` is refused
# +            # here rather than handed to `_classify`, which would open it and
# +            # block forever on a FIFO with no writer.
# +            if not candidate.is_file():
# +                result.ignored.append((str(rel_dir / name), "not_regular_file"))
# +                continue
#              result.paths.append(candidate)
```

```bash
# --- fresh clones ---
git clone --quiet "/Users/.../_worktrees/p7-01" /tmp/p7-final/cand   && cd /tmp/p7-final/cand   && git checkout --quiet 0ae8041
git clone --quiet "/Users/.../_worktrees/p7-01" /tmp/p7-final/prefix && cd /tmp/p7-final/prefix && git checkout --quiet a252097

# --- pre-fix hazard (a252097), 3 s client timeout ---
cd /tmp/p7-final/prefix && PYTHONPATH=src:tests python3.13 /tmp/p7-final/prefix_hang.py
# [PREFIX] GET /v1/validation/runs -> 'TimeoutError' after 3.01s (timed out)
# Ran 1 test in 3.044s

# --- candidate N3 API probe (FIFO + regular sibling) ---
cd /tmp/p7-final/cand && PYTHONPATH=src:tests PYTHONHASHSEED=0 python3.13 /tmp/p7-final/probe_n3_n5.py
# [N3] list   status=200 elapsed=0.008s rows=['run-20261008T000001Z']
# [N3] detail status=404 elapsed=0.002s
# [N3] not_regular_file samples=[{'relative_path': 'p7-fifo/run-20261008T010001Z/run-20261008T010001Z.json', 'reason': 'not_regular_file'}]
# [N3] symlink list status=200 ignored=[… reason 'symlinked_file' …]
# [N3] empty list status=200 corrupt_count=1 statuses=[(RUN_ONE,'ok'), (RUN_EMPTY,'corrupt')]
# [N3] dir list status=200 rows=['run-20261008T000001Z'] … detail=404
# [N3] hardlink status=200 rows=[(RUN_ONE,'ok'), (RUN_HARD,'corrupt')]
# [N5] PYTHONHASHSEED=0 ids=['run-20261008T100000Z', …, 'run-20261008T100005Z']
# Ran 6 tests in 0.212s
# OK

# --- candidate extra shapes (socket, symlinked directory) ---
cd /tmp/p7-final/cand && PYTHONPATH=src:tests PYTHONHASHSEED=7 python3.13 /tmp/p7-final/probe_extra_shapes.py
# [N3] socket status=200 ignored=[{'relative_path': 'run-20261008T020001Z.json', 'reason': 'not_regular_file'}]
# [N3] dir-symlink status=200 ignored=[… 'symlinked_directory' …]
# Ran 2 tests in 0.081s
# OK

# --- direct _walk check (no HTTP) ---
cd /tmp/p7-final/cand && PYTHONPATH=src python3.13 - <<'PY'
import os, tempfile, pathlib
from jasmine_core.validation import reads
root = pathlib.Path(tempfile.mkdtemp(prefix="p7walk"))
d = root / "run-20261008T040001Z"; d.mkdir(parents=True)
os.mkfifo(d / "run-20261008T040001Z.json")
reg = root / "run-20261008T040002Z"; reg.mkdir(parents=True)
(reg / "run-20261008T040002Z.json").write_text("{}")
walk = reads._walk(root, stop_on_caps=False)
print("paths admitted   :", [p.name for p in walk.paths])
print("ignored          :", walk.ignored)
print("FIFO in paths    :", d / "run-20261008T040001Z.json" in walk.paths)
PY
# paths admitted   : ['run-20261008T040002Z.json']
# ignored          : [('run-20261008T040001Z/run-20261008T040001Z.json', 'not_regular_file')]
# FIFO in paths    : False

# --- N5 under two hash seeds ---
for seed in 0 12345; do cd /tmp/p7-final/cand && PYTHONHASHSEED=$seed PYTHONPATH=src:tests \
  python3.13 -m unittest -v test_validation_read.ReviewRegression.test_f3_raw_declared_path_components_refuse_the_manifest; done
# Ran 1 test in 0.060s / OK   (seed 0)
# Ran 1 test in 0.058s / OK   (seed 12345)
grep -n "hash(" tests/test_validation_read.py     # only the explanatory comment

# --- contract re-pin ---
cd /tmp/p7-final/cand && PYTHONPATH=src python3.13 scripts/p7/p7_contract.py --check
# P7-01 contract snapshot matches 46a33d78d03e4d8d9711c96ded62072de8a276be   (exit 0)

# --- advisories ---
cd /tmp/p7-final/cand && PYTHONPATH=src:tests PYTHONHASHSEED=99 python3.13 /tmp/p7-final/probe_advisories.py
# [N1] GET manifest -> 409 validation_manifest_corrupt rule=art_note: dot segment path 'artifacts/note..txt'
# [N2] status=200 record_status=corrupt served_sha='' direct_sha=c283211e64a4b099… corrupt_reason=record exceeds 16777216 bytes
# [N4] all=200 dup_rows=[('run-20261008T030001Z', None, None), ('run-20261008T030001Z', None, None)]
# [N4] mode=component total=1 ids=['run-20261008T000001Z']
# [N4] attribution=unattributed total=1 ids=['run-20261008T000001Z']
# Ran 3 tests in 0.134s
# OK

# --- fresh-clone P7 module ---
cd /tmp/p7-final/cand && PYTHONPATH=src:tests python3.13 -m unittest test_validation_read
# Ran 48 tests in 1.903s
# OK

# --- fresh-clone full suite, both commits ---
cd /tmp/p7-final/cand    && PYTHON=python3.13 ./scripts/run-tests.sh > /tmp/p7-final/cand-suite.log 2>&1
# Ran 1414 tests in 44.762s | FAILED (failures=5, errors=4, skipped=16)
cd /tmp/p7-final/prefix  && PYTHON=python3.13 ./scripts/run-tests.sh > /tmp/p7-final/prefix-suite.log 2>&1
# Ran 1413 tests in 44.756s | FAILED (failures=5, errors=4, skipped=16)
grep -E "^(FAIL|ERROR):" /tmp/p7-final/{cand,prefix}-suite.log | sort > /tmp/p7-final/{cand,prefix}-fails.txt
diff /tmp/p7-final/prefix-fails.txt /tmp/p7-final/cand-fails.txt   # (empty: identical sets)
```

## Appendix B — writes

* **Created:** `validation-quarantine/REVIEW-p7-01-implementation/FINAL-REVIEW-findings.md` (this file).
* **Modified:** nothing. `git diff --name-only HEAD` is empty; `git status --porcelain` before and
  after this review shows only the two pre-existing untracked quarantine directories and this new
  file. Nothing was staged, committed or pushed.
* **Outside the worktree:** two fresh clones and all harnesses/outputs under `/tmp/p7-final/`
  (`cand/`, `prefix/`, `probe_n3_n5.py`, `probe_extra_shapes.py`, `probe_advisories.py`,
  `prefix_hang.py`, `*-suite.log`, `*-fails.txt`). The shared source repo
  `jasmine-core-v1` was only read (cloned from); it was not written to.
