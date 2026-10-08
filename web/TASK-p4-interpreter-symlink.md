# TASK — P4 hook-installer tests vs. a symlinked interpreter (independent fix task)

**Registered:** 2026-10-08, from the P6-03 integration verification.
**Owner:** P4 (the installer and all nine tests are P4 code; the files are byte-identical at P4's
tip `0c4f8f0e2118711ac9aa2a7006fddc6982962248` and at the P6-03 integration commit
`f323c779bc91893b5908218d8c5c148619159180`).
**Status:** registered, **not started**. No P4 G01 work, no deployment, no product change is
authorised by this note.
**Relation to P6:** this is *not* a P6-03 task and no P6-03 acceptance round should be added for it.
It is the one thing that keeps the repository's Python suite red, so it is worth fixing once, on its
own, by its owner.

## Symptom

`PYTHONPATH=src:tests python3.13 -m unittest discover -s tests -t tests` is **not green** in every
worktree (the P6-03 integration tip, the P4 worktree, and inside the synthetic-merge clone):

```
Ran 1338 tests   FAILED (failures=5, errors=4, skipped=13)   # integration tip
Ran 1155 tests   FAILED (failures=5, errors=4, skipped=10)   # P4's own tip, same nine
```

| # | Kind | Test |
| --- | --- | --- |
| 1 | ERROR | `test_p4_g01_v2.PairPreparation.test_prepare_creates_two_frozen_worktrees_and_refuses_a_second_time` |
| 2–4 | ERROR | `test_p4_native_session_start.InstalledHooksShapeMatchesTheOfficialContract.{test_changing_the_source_and_reapplying_updates_the_installed_copy, test_the_event_the_matcher_and_the_command_hook_shape, test_the_installed_script_is_this_repositorys_hook_source}` |
| 5–9 | FAIL | `test_p4_native_session_start.InstallerPlanAndApply.{test_a_foreign_binding_in_the_existing_hooks_json_is_refused, test_a_hook_from_another_tool_is_preserved, test_apply_is_idempotent_and_binds_an_absolute_command, test_apply_never_touches_the_trust_store, test_plan_writes_nothing_and_prints_the_trust_steps}` |

Evidence: `20261008T075553Z-p6-03-failure-attribution-post-review/` (class-qualified comparison,
both raw logs) and `20261008T074622Z-p6-03-failure-attribution-p4-tip/` in this registry.

## Root cause (confirmed in code, not inferred)

`scripts/p4-codex-hook-install.py:81` (`plan()`):

```python
if not python.exists() or python.parent != python.parent.resolve(strict=True):
    raise ValueError("canonical venv launcher parent required")
```

The tests hand the installer **their own interpreter** (`PYTHON = sys.executable`,
`tests/test_p4_native_session_start.py`; `Path(sys.executable)`,
`tests/test_p4_g01_v2.py`). In this environment that is
`/opt/homebrew/opt/python@3.13/bin/python3.13`, whose parent `…/opt/python@3.13/bin` resolves
into `/opt/homebrew/Cellar/python@3.13/3.13.15/bin`. The guard therefore refuses **before the tests
reach their subject** — the four ERRORs and five FAILs are all this one precondition (the FAILs
assert on a plan/apply that never got a launcher).

It is *not* a repository-layout dependency and not a merge defect: the failing set is identical at
P4's own tip, and the four deciding files are byte-identical there and at the integration commit.

## Constraint — the identity check stays

`canonical venv launcher parent required` is an **identity/authenticity guard on the installer**: it
refuses to write a hook whose launcher resolves somewhere other than where it appears to live, which
is the class of problem a swapped or symlinked launcher parent represents (path substitution /
TOCTOU around the executable a trust decision is being bound to). **Do not delete, weaken or bypass
it to turn the suite green.** A green suite bought that way would be worse than the red one: the
tests exist to prove the installer refuses the wrong thing, and this guard is one of the refusals.

## Candidate fixes (choose one in the task; each keeps the guard)

1. **Make the tests satisfy the precondition (preferred, smallest).** Have the tests run the
   installer with an interpreter that genuinely lives in a canonical launcher parent — e.g. create a
   throwaway venv for the test (`venv.EnvBuilder` / `python -m venv`) and pass its
   `bin/python` (a real file whose parent *is* its resolved self), or a copy of the interpreter at a
   canonical path. The guard is untouched; the tests exercise the real path the guard allows.
   Cost: a per-test venv (or a session fixture that builds one once).
2. **Make the guard symlink-correct rather than symlink-hostile (principled, slightly larger).**
   Replace the literal parent comparison with the property it is actually protecting: the launcher
   must resolve into the interpreter's own installation root, and the parent directory must be
   owned/stable (e.g. compare `python.resolve(strict=True)` against the installation prefix and
   require the launcher's parent realpath to be inside it, plus a no-follow/write check). Must keep a
   negative test proving a foreign or substituted launcher is still refused.
3. **An explicit, audited test/CI escape (last resort).** A non-default flag/env (never the default,
   refused when the installer runs outside a test fixture) that records the override in its output —
   so the relaxed path can never be mistaken for the production behaviour. Only if 1 and 2 are not
   acceptable to the P4 owner.

## Acceptance criteria for this task

* The nine tests pass (or are shown to be genuinely blocked for an *unrelated*, named reason) **with
  the identity guard still present and still refusing a non-canonical launcher**; a new negative test
  proves the refusal is intact.
* The rest of the suite is unchanged: the other `1329` tests still pass, no skips added to hide the
  nine, no `@expectedFailure`, no widening of `unittest` filters.
* A short record: which fix was chosen and why, the diff, the raw before/after suite output, and the
  negative test's output. Landed on the P4 branch (owner's choice of branch/PR); if it is to be part
  of an integration candidate, it is merged and the affected Gate re-run there — **not** carried as
  an unmerged patch.
* No change to `src/` behaviour beyond the installer/test code the fix needs; no change to the live
  entry point, global hooks, credentials or the operator's Core.

## Explicitly out of scope

* Restarting the P4 G01 benefit-verification experiment.
* Deploying a real Hindsight to make this or any other row “real”.
* Any P6-03 acceptance round: this task changes P4's own test prerequisite, not the P6 dashboard.
