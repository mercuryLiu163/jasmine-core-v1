# P7-01 fix review — candidate `f0aea886186a6c1e4704eceb2552c74845f333b2`

* **Reviewer model / identifier:** `deepseek-flash` (DeepSeek Harness coding agent; delegated
  independent reviewer, fresh context — not the author of the proposal, the implementation, or any
  earlier review round).
* **Date of review run:** 2026-10-08 (host time `Thu Oct  8 09:52 PDT 2026`).
* **Worktree:** `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p7-01`, branch
  `p7-01-validation-read`.
* **Candidate under review:** `f0aea886186a6c1e4704eceb2552c74845f333b2` (branch tip). Its parent
  `264163b` is the four-defect fix; `f0aea88` changes **only** `web/contracts/p7-validation-read.v1.json`
  to re-pin the derived snapshot from `46a33d7` to `264163b`.
* **Authority:** `web/P7-01-CONTRACT-PROPOSAL.md` (revision 5, frozen), `docs/adr/0015-p7-validation-read-contract.md`
  (the new invariants are §5.4), and the three earlier reviews in this same directory
  (`findings.md`, `RE-REVIEW-findings.md`, `FINAL-REVIEW-findings.md`).
* **Operator-reported defects under test:** D1 the artifact routes re-read the manifest after the
  grant check; D2 the record/manifest reads `stat`ed and then read uncapped (FIFO block / grown file
  read whole); D3 the resolved root was cached so a later symlinked root was followed; D4 the masked
  output was never measured against the cap.
* **Writes:** exactly one new file — this one. No tracked file was modified, staged, committed or
  pushed. Every harness, clone and output below lives under `/tmp/p7-review/` (Appendix B). The
  worktree status at the end shows only my one new untracked file plus untracked paths a **concurrent**
  fix-validator created (`validation-quarantine/VALIDATE-p7-01/FIX-VALIDATION-findings.md`,
  `VALIDATE-p7-01/evidence-fix/`) — not mine.

---

## 1. Baseline integrity

```
$ git rev-parse HEAD
f0aea886186a6c1e4704eceb2552c74845f333b2
$ git rev-parse --abbrev-ref HEAD
p7-01-validation-read
$ git status --porcelain            # before my write; concurrent validator paths appeared later
(empty)
$ git diff --stat 0ae8041 f0aea88 -- src/jasmine_core/validation/reads.py \
      docs/adr/0015-p7-validation-read-contract.md tests/test_validation_read.py \
      web/contracts/p7-validation-read.v1.json
 docs/adr/0015-p7-validation-read-contract.md |  38 ++++
 src/jasmine_core/validation/reads.py         | 325 ++++++++++++++++-----------
 tests/test_validation_read.py                | 115 ++++++++++
 web/contracts/p7-validation-read.v1.json     |   2 +-
 4 files changed, 348 insertions(+), 132 deletions(-)
$ git diff --stat 264163b f0aea88
 web/contracts/p7-validation-read.v1.json | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

**Assessment.** HEAD is the candidate on the right branch; the tree was clean before my write.
`264163b..f0aea88` is metadata-only (the contract re-pin), and
`git diff 264163b f0aea88 -- src/jasmine_core/validation/reads.py` is **empty**, so the snapshot at
`264163b` is exactly the code under review. Test count went 48 → 53 (`+5` new `IndependentReviewDefects`
tests). **CONFIRMED.**

**Contract re-pin.** Run in a fresh candidate clone:

```
$ python3.13 scripts/p7/p7_contract.py --check
P7-01 contract snapshot matches 264163b9406bbb0921ff0edb8692f4d9c7d6dcc8
exit=0
$ git merge-base --is-ancestor 264163b9406bbb0921ff0edb8692f4d9c7d6dcc8 HEAD; echo $?
0
```

The pinned ref is an ancestor of the candidate and the snapshot re-derives exactly. Deriving from the
working tree at HEAD yields the same `read_routes` block (only `source.ref` reads `WORKTREE`), so the
re-pin introduces no drift. **CONFIRMED.**

---

## 2. The four defects: reproduced pre-fix, absent on the candidate

Method: an independent harness (`/tmp/p7-review/p7_harness.py`) that calls the route handlers
directly with a hand-built `Request`/`Core`/`Principal` and a temporary run root. It is **not** the
committed tests. Pre-fix behaviour is measured on a throwaway clone checked out at
`0ae8041` (`/tmp/p7-review/prefix`); the worktree was never checked out or modified. Every scenario
that can block runs in its own subprocess with a 10 s hard timeout (`/tmp/p7-review/driver.py`); a
timed-out attempt is reported **HUNG** and killed.

### D1 — one manifest read per request

Counted by wrapping `_load_manifest` (the wrapper forwards the tree's own signature):

| route | pre-fix `0ae8041` | candidate `f0aea88` |
|---|---|---|
| `GET /v1/validation/runs/{id}` (detail) | **2 reads** | **1 read** |
| `GET …/manifest` | **2 reads** | **1 read** |
| `GET …/artifacts/{aid}` (metadata) | **2 reads** | **1 read** |
| `GET …/artifacts/{aid}/content` | **2 reads** | **1 read** |

Direct proof that the grant decision and the payload can come from different reads: an ACL root where
manifest **A** (`projects=[prj_a]`, artifact `artifacts/a.txt` = `AAAA…`) and manifest **B**
(`projects=[prj_b]`, artifact `artifacts/b.txt` = `BBBB…`) are swapped on disk between the two
`_load_manifest` calls; the principal is granted `prj_a` only.

```
PREFIX(0ae8041):  content reads=2  served="BBBB-from-manifest-B\n"
                  detail reads=2 status=404 validation_run_not_found
                  manifest reads=2 status=404 validation_run_not_found
CANDIDATE(f0aea88): content reads=1 served="AAAA-from-manifest-A\n"
                  detail reads=1 status=200 ; manifest reads=1 status=200
```

Pre-fix, `get_artifact`/`get_artifact_content` served the bytes declared by manifest **B**, whose
`projects` the caller was **not** granted (the second read was never re-authorised). On the candidate
the single `_locate` manifest decides both the grant and the bytes, so only A is ever served.
**FIXED.**

### D2a — a FIFO must not block a request

| scenario (injected) | pre-fix `0ae8041` | candidate `f0aea88` |
|---|---|---|
| `_classify` called on a FIFO named `run-*.json` | **HUNG @ 10.004 s** | `corrupt` / `"not a regular file"` @ 0.0001 s |
| FIFO swapped in **after the walk** admits the path, list route | **HUNG @ 10.008 s** | 200, `corrupt_count=1` @ 0.0005 s |
| FIFO at `manifest.json` from the start | 409 `validation_manifest_corrupt` @ 0.0005 s | 409 @ 0.0004 s |
| FIFO swapped in **between the pre-read check and the read** of `manifest.json` | **HUNG @ 10.006 s** | 200 frozen @ 0.0006 s (no such window) |
| FIFO at an artifact path | 503 `artifact_unreadable` @ 0.0009 s | 503 @ 0.0013 s |

The two HUNGs are the pre-fix `stat`-then-`read_bytes()` TOCTOU: the earlier regular-file check passed,
the file was replaced by a FIFO, and `read_bytes()` opened it and blocked with no writer. The two
swaps are injected exactly in the check→read window (for the record, by swapping after the real
`_walk`; for the manifest, by swapping inside `Path.read_bytes`, i.e. after `is_file()` returned True)
— they are not artificial failures of the pre-check. The candidate opens once with
`O_NONBLOCK`, `fstat`s the open descriptor, and refuses `S_ISFIFO` without a blocking open; it has no
path-check window to race. Artifact FIFOs were already refused pre-fix because the old artifact walk
also used `O_NONBLOCK`. **FIXED.**

### D2b — a file that grows past the cap must be refused, not read whole

| scenario | pre-fix `0ae8041` | candidate `f0aea88` |
|---|---|---|
| record, real 801 B, cap 200, `stat` lies 100 B | `status="ok"`, **read whole** (digest = digest of 801 B) | `corrupt` / `"record exceeds 200 bytes"`, **not read whole** |
| record, grow 187 B → 787 B between fstat/stat and read (JSON whitespace, so still parseable) | `status="ok"`, **read whole** | `corrupt` / `"record exceeds 200 bytes"`, **not read whole** |
| manifest, real 1115 B, cap 200, `stat` lies 100 B | `corrupt` but **read whole and returns its digest** (`digest_reported=true`) | `corrupt`, `digest_reported=false`, not read whole |
| record/manifest larger than cap with an honest `stat` | corrupt (never read) | corrupt (never read) |

The greedy case is the second row: the size check saw 187 B ≤ 200, the file then grew to 787 B, and
pre-fix `_classify` read all 787 B (the whitespace-append keeps it valid JSON, so it was served as a
normal `ok` record). The candidate re-`fstat`s the open descriptor and `_read_capped` requests one
byte past the cap inside the loop, so it refuses. The pre-fix manifest path also read the whole
oversized file before its *post-hoc* length test, and returned the SHA-256 of bytes it claimed not to
have read; the candidate reports `manifest_sha256: null` per ADR §5.2. **FIXED.**

### D3 — root re-validated on every use

| scenario | pre-fix `0ae8041` | candidate `f0aea88` |
|---|---|---|
| after a 200 request, root renamed and replaced by a symlink to an outside dir with a valid record — list | **200**, outside record served | **503** `validation_not_configured` `reason=symlinked_root` |
| same — detail | **200** | **503** `reason=symlinked_root` |
| root replaced by a symlink **after** `_resolve_root` (resolution patched to return it) — detail | **200** | **409** `validation_run_corrupt` (`unreadable: root_unreadable`), no bytes |
| same — content | **200**, body `"hello bundle\n"` | **409**, no body |

The first two rows are the operator's D3 (cached resolution). The third/fourth rows are stronger:
even when `_resolve_root` is forced to hand back the symlinked path, every record/manifest/artifact
read re-opens the root `O_DIRECTORY|O_NOFOLLOW`, so the candidate refuses and the pre-fix tree serves
the outside bytes. The refusal is by `O_NOFOLLOW` on macOS, which returns `ENOTDIR` here (not
`ELOOP`), hence the internal reason `root_unreadable`; the contract-visible outcome is still a
refusal. **FIXED.**

### D4 — the masked form is measured

Raw artifact `b"token=x " * 18` = 144 B, `redact_text` expands it to 306 B; cap set to 146 B (raw
under, masked over).

```
PREFIX(0ae8041):  status=200  served_bytes=306   (cap 146)
CANDIDATE(f0aea88): status=413 payload_too_large
                    details={"limit":146,"form":"masked","original_bytes":144}
```

**FIXED**, with exactly the `details.limit` / `details.original_bytes` the ADR names.

**All four operator-reported defects are real on `0ae8041` and are closed on `f0aea88`.**

---

## 3. Adversarial hunt for defects introduced by the refactor

Every item below was exercised on the candidate; where a comparison is meaningful the pre-fix result
is given too.

| property | result on candidate `f0aea88` | verdict |
|---|---|---|
| `realpath` containment still applied | present in `_read_artifact` after the descriptor walk; a symlinked-ancestor escape (`artifacts/escape/evil.txt` → outside dir) is refused `symlink_refused` → 404, identical to pre-fix | **OK** |
| error mapping `missing` / `symlink_refused` / `unreadable` / `too_large` | identical on both trees: missing→404 `missing`; final-component symlink→404 `symlink_refused`; intermediate symlink→404 `symlink_refused`; FIFO→503 `artifact_unreadable`; oversize→413 `payload_too_large` | **OK** |
| legitimate nested artifact path | record in `a/b`, artifact `artifacts/deep/x.txt` → 200 `deep-content`; manifest route reports `integrity:"match"`, `bytes_agree:true` | **OK** |
| top-level record + artifact at root | 200 `top-content` | **OK** |
| record more than one level deep / depth boundary | depth-4 record listed; depth-5 pruned with `depth_cap` (walk unchanged, same on both) | **OK** |
| empty artifact (0 B) | `bytes=0`, `content=""`, 200 | **OK** |
| empty record (0 B) | `corrupt` / `unparseable: JSONDecodeError` | **OK** |
| empty manifest (0 B) | 409 `validation_manifest_corrupt`, rule `unparseable: JSONDecodeError` | **OK** |
| directory named `manifest.json` | 409 `validation_manifest_corrupt` (candidate rule `manifest.json is not a regular readable file: not_regular_file`; pre-fix rule wording differs, same status/code/`corrupt`) | **OK** |
| manifest with `files: []` | 200 `status:"frozen"`, `files:[]` | **OK** |
| declared path whose component is a regular file, not a dir (`artifacts/good.txt/child.txt`) | 404 `artifact_not_found` `reason=missing` | **OK** |
| `record.root` set on every route-built record | `all_root_set=true`, `root_matches_walk=true`; `dir_relative`/`under_root` correct (`p7/artifacts/good.txt`) | **OK** (see A4) |
| descriptor leak | 200 iterations each of good / missing / FIFO / symlink / too-large / `..` / absolute reads: `/dev/fd` count 4 → 4, **delta 0**; no unexpected exceptions | **OK** |
| double-close of `finally: os.close(directory)` | 500 iterations of the three error paths that close the parent fd: **no `EBADF`, no exception** | **OK** |
| `_read_capped` at the edges | cap 0 + empty → `(b"", None)`; cap 0 + 1 byte → `too_large`; exactly cap → full buffer accepted; cap+1 → `too_large` | **OK** |
| `O_NONBLOCK` on regular files | harmless: all reads succeed over 200 iterations and all routes return normal bodies | **OK** |
| root re-open at read time | `_open_regular_under(<symlink root>, …)` → refusal; route-level D3b above | **OK** |

No blocking defect was found in the refactor. The following are **advisory only** (Section 6).

---

## 4. Regression

From **fresh clones** (`git clone --no-local` of the shared repo, then checkout), Python 3.13.15,
`P7_REAL_RUNS_ROOT` pointed at the operator's read-only real-run tree:

```
candidate f0aea88:  Ran 1419 tests in 46.829s  FAILED (failures=5, errors=4, skipped=13)
prefix    0ae8041:  Ran 1414 tests in 45.077s  FAILED (failures=5, errors=4, skipped=13)
$ diff <(prefix FAIL/ERROR lines | sort) <(candidate FAIL/ERROR lines | sort)
IDENTICAL
```

The `+5` tests are exactly the new `IndependentReviewDefects` class. The failure set is the
**pre-existing** one and is byte-identical to the set recorded at `0ae8041` in
`VALIDATE-p7-01/evidence-final/fullsuite-candidate.txt` (4 errors + 5 failures, all in
`test_p4_native_session_start` / `test_p4_g01_v2`, an environment/hooks issue unrelated to this slice):

```
ERROR: test_changing_the_source_and_reapplying_updates_the_installed_copy (test_p4_native_session_start…)
ERROR: test_prepare_creates_two_frozen_worktrees_and_refuses_a_second_time (test_p4_g01_v2…)
ERROR: test_the_event_the_matcher_and_the_command_hook_shape (test_p4_native_session_start…)
ERROR: test_the_installed_script_is_this_repositorys_hook_source (test_p4_native_session_start…)
FAIL:  test_a_foreign_binding_in_the_existing_hooks_json_is_refused (test_p4_native_session_start…)
FAIL:  test_a_hook_from_another_tool_is_preserved (test_p4_native_session_start…)
FAIL:  test_apply_is_idempotent_and_binds_an_absolute_command (test_p4_native_session_start…)
FAIL:  test_apply_never_touches_the_trust_store (test_p4_native_session_start…)
FAIL:  test_plan_writes_nothing_and_prints_the_trust_steps (test_p4_native_session_start…)
```

Module test on the fresh candidate clone:

```
$ PYTHONPATH=src:tests python3.13 -m unittest test_validation_read
Ran 53 tests in 2.022s
OK
```

`OK` with no skips means the real-record acceptance assertions ran against the operator's nine
records, not a skipped path. **Regression clean; no new failures on the slice.**

---

## 5. Verdict

# APPROVE (accept `f0aea88` as the phase candidate)

* All four operator-reported defects reproduce on the pre-fix commit `0ae8041` and are demonstrably
  closed on `f0aea88`: one manifest read per request on all four routes (and the ungranted-manifest
  payload bypass is gone); no FIFO can block a record or manifest read; a file that grows past the
  cap is refused rather than read whole; a root swapped to a symlink (even after resolution) is
  refused at read time; a masked body over the cap is refused `413`.
* No refactor-induced defect was found. `realpath` containment, the `missing` /
  `symlink_refused` / `unreadable` / `too_large` mapping, nested paths, empty files/manifests,
  depth handling, `record.root` population, descriptor lifetime and the `_read_capped` edges all
  behave correctly.
* The contract re-pin is metadata-only; `264163b` is an ancestor of the candidate and
  `p7_contract.py --check` passes. The implementation source at `264163b` is byte-identical to HEAD.
* The full suite fails with exactly the pre-existing failure set; the module test is `OK`.

## 6. Advisory list (non-blocking)

* **A1 — stale comment (documentation).** `src/jasmine_core/validation/reads.py:107-109` still reads
  “Root resolution is **cached** against the raw configured string … A failed resolution is not
  cached.” That was the `_ROOT_CACHE` doc comment; the cache (and the constant) is gone. It now
  contradicts the code and the function docstring directly below it. Delete or replace it.
* **A2 — undocumented return reason.** `_open_regular_under`’s docstring lists
  `invalid_path, symlink_refused, missing, not_regular_file, too_large, unreadable` but the function
  can also return `root_unreadable` (root open failed with something other than `ELOOP`). On macOS a
  symlinked root reaching this open fails `ENOTDIR`, so the reason surfaces as `root_unreadable` and
  a record corrupt-reason becomes `"unreadable: root_unreadable"`. The refusal is correct and no
  contract status changes; only the internal classification/documentation is inconsistent. Adding
  the reason to the docstring (or an `_is_symlink_at`-style pre-check on the root) would make the
  diagnostics uniform.
* **A3 — `invalid_path` mapping changed, currently unreachable.** Pre-fix, a declared path that
  failed the component check produced `integrity="unreadable"` (503); the refactor maps it to
  `missing` (404). It is unreachable through all five routes because `_classify_path` already
  rejects absolute / `..` / empty-component paths when the manifest is loaded, so no manifest entry
  can carry one. Noted so a future caller of `_read_artifact` does not silently depend on the old
  mapping.
* **A4 — `_Record.root` defaults to `None`.** Built by `_classify`, every route record has `root`
  set (verified), but the dataclass default means a hand-built record would make
  `_read_artifact(record.root, …)` raise `TypeError`. Consider making the field required (or an
  explicit guard) to keep the invariant local.
* **A5 — ADR §5.4 wording.** Item 1 says “…the manifest routes and both artifact routes use that
  object”, omitting the run-detail route, which also consumes the single `_locate` manifest (and the
  operator's defect list names four routes). Pure doc completeness.
* **A6 — optional test coverage.** The committed `test_d2_a` pins the in-loop cap on a
  pre-grown buffer, which is the load-bearing code. The distinct
  “grow between the size check and the read” case (Section 2, D2b row 2) is covered only by this
  review's harness; a committed test appending after the `fstat` would pin it too.

---

## Appendix A — exact commands and verbatim key output

Clones (the shared repo is read-only; the worktree was never used as a checkout target):

```
$ mkdir -p /tmp/p7-review
$ git clone -q --no-local "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1" /tmp/p7-review/candidate
$ git -C /tmp/p7-review/candidate checkout -q f0aea886186a6c1e4704eceb2552c74845f333b2
$ git clone -q --no-local "/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1" /tmp/p7-review/prefix
$ git -C /tmp/p7-review/prefix checkout -q 0ae80411824739b11485c61ed388d86093f2be7c
```

Harness matrix (23 scenarios × both trees, 10 s per-scenario hard timeout):

```
$ cd /tmp/p7-review && python3.13 driver.py
===== PREFIX(0ae8041) (/tmp/p7-review/prefix) =====
  d1_counts                        ok      0.071s
  d1_swap                          ok      0.067s
  d2_record_fifo_direct            HUNG   10.004s
  d2_record_fifo_swap              HUNG   10.008s
  d2_manifest_fifo_from_start      ok      0.066s
  d2_manifest_fifo_swap            HUNG   10.006s
  d2_artifact_fifo                 ok      0.075s
  d3_record_grow_lying_stat        ok      0.070s
  d3_record_grow_concurrent        ok      0.070s
  d3_record_large_real_stat        ok      0.069s
  d3_manifest_grow_lying_stat      ok      0.071s
  d3_manifest_large_real_stat      ok      0.071s
  d4_symlink_root                  ok      0.071s
  d4b_root_symlink_at_read         ok      0.070s
  d5_masked_cap                    ok      0.071s
  r_nested / r_empty / r_errors / r_fd_leak / r_capped_edge / r_record_root / r_realpath / r_hunt   ok
===== CANDIDATE(f0aea88) (/tmp/p7-review/candidate) =====
  all 23 scenarios ok, no HUNG; slowest r_fd_leak 0.170s
```

Key per-scenario JSON, verbatim (from `/tmp/p7-review/harness-results.json`):

```
d1_counts PREFIX:    detail=2, manifest=2, artifact=2, content=2 manifest reads
d1_counts CANDIDATE: detail=1, manifest=1, artifact=1, content=1 manifest reads
d1_swap   PREFIX:    content reads=2 served="BBBB-from-manifest-B\n"; detail/manifest 404
d1_swap   CANDIDATE: content reads=1 served="AAAA-from-manifest-A\n"; all 200
d2_record_fifo_direct PREFIX: HUNG @10.004s | CANDIDATE: corrupt "not a regular file" @0.0001s
d2_record_fifo_swap   PREFIX: HUNG @10.008s | CANDIDATE: 200 corrupt_count=1 @0.0005s
d2_manifest_fifo_swap PREFIX: HUNG @10.006s | CANDIDATE: 200 frozen @0.0006s
d3_record_grow_lying_stat PREFIX: ok read_whole=true | CANDIDATE: corrupt "record exceeds 200 bytes" read_whole=false
d3_record_grow_concurrent PREFIX: ok read_whole=true (187->787 B) | CANDIDATE: corrupt "record exceeds 200 bytes"
d3_manifest_grow_lying_stat PREFIX: corrupt digest_reported=true read_whole=true | CANDIDATE: corrupt digest_reported=false
d4_symlink_root PREFIX: list 200 detail 200 | CANDIDATE: 503 symlinked_root / 503 symlinked_root
d4b_root_symlink_at_read PREFIX: detail 200, content 200 "hello bundle\n" | CANDIDATE: 409, no bytes
d5_masked_cap PREFIX: 200 served_bytes=306 cap=146 | CANDIDATE: 413 details={limit:146,form:masked,original_bytes:144}
r_fd_leak PREFIX: fd 4->4 delta 0 | CANDIDATE: fd 4->4 delta 0
r_capped_edge CANDIDATE: cap0_empty=(b"",None) cap0_nonempty=too_large exactly_cap=16 one_over=too_large
r_hunt PREFIX==CANDIDATE: depth4 listed, depth5 depth_cap, manifest nested integrity=match, files:[] frozen
```

Contract check and regression (fresh clones):

```
$ cd /tmp/p7-review/candidate && python3.13 scripts/p7/p7_contract.py --check
P7-01 contract snapshot matches 264163b9406bbb0921ff0edb8692f4d9c7d6dcc8   (exit 0)
$ P7_REAL_RUNS_ROOT=… PYTHONPATH=src:tests python3.13 -m unittest test_validation_read
Ran 53 tests in 2.022s
OK
$ ( cd candidate && PYTHON=python3.13 P7_REAL_RUNS_ROOT=… ./scripts/run-tests.sh )   # candidate
Ran 1419 tests in 46.829s
FAILED (failures=5, errors=4, skipped=13)
$ ( cd prefix && PYTHON=python3.13 P7_REAL_RUNS_ROOT=… ./scripts/run-tests.sh )      # pre-fix
Ran 1414 tests in 45.077s
FAILED (failures=5, errors=4, skipped=13)
$ diff <(grep -E '^(FAIL|ERROR):' suite-prefix.txt | sort) <(grep -E '^(FAIL|ERROR):' suite-candidate.txt | sort)
IDENTICAL
```

## Appendix B — artifacts

* Harness: `/tmp/p7-review/p7_harness.py`; driver: `/tmp/p7-review/driver.py`;
  raw results: `/tmp/p7-review/harness-results.json`, `/tmp/p7-review/driver-out.txt`.
* Clones: `/tmp/p7-review/candidate` (`f0aea88`), `/tmp/p7-review/prefix` (`0ae8041`).
* Suites: `/tmp/p7-review/suite-candidate.txt`, `/tmp/p7-review/suite-prefix.txt`,
  `set-candidate.txt`, `set-prefix.txt`, `set-prior.txt`, `verbatim.txt`.

## Appendix C — write statement

I created exactly one file in the worktree: this one
(`validation-quarantine/REVIEW-p7-01-implementation/FIX-REVIEW-findings.md`). I did not modify any
tracked file, did not stage, commit or push anything, and did not fix the code. All scratch work
(two throwaway clones, the harness, the driver and all outputs) is under `/tmp/p7-review/`. The
untracked `validation-quarantine/VALIDATE-p7-01/FIX-VALIDATION-findings.md` and
`VALIDATE-p7-01/evidence-fix/` visible in `git status` were produced by a **concurrent** validator,
not by me.
