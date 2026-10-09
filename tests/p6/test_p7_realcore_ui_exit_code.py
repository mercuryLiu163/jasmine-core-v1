"""P6-03c harness exit code: a recorded FAIL must be a non-zero process exit.

`scripts/p6/p7_realcore_ui.py` writes `result.md` and `manifest.json` with a per-check
PASS/FAIL/BLOCKED table, but the process exit code is what an automated gate watches. The
original final expression consulted the browser trace's own exit code, the operator-tree
comparison, the token scan and the verdict-upgrade flag — and **not** `python_checks`, so a
run whose python checks failed still exited 0. That made the exit code unusable as a gate.

These tests pin the aggregation function directly. It is pure: no Chrome, no Core, no network.
The end-to-end negative — the real harness process exiting non-zero when a check fails — is
`scripts/p6/p7_realcore_ui_exit_negative.py`, because it needs the real stack.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "p6"))

from p7_realcore_ui import check_exit_code  # noqa: E402


def aggregate(*, trace_exit: int = 0, trace_records=None, python_checks=None,
              real_tree_unchanged: bool = True, leaked=None,
              no_verdict_upgraded: bool = True):
    return check_exit_code(
        trace_exit=trace_exit,
        trace_records=list(trace_records if trace_records is not None
                           else [{"step": "A/list", "ok": True}]),
        python_checks=list(python_checks if python_checks is not None
                           else [{"step": "python: census", "ok": True}]),
        real_tree_unchanged=real_tree_unchanged,
        leaked=list(leaked or []),
        no_verdict_upgraded=no_verdict_upgraded,
    )


class TheExitCodeIsDrivenByTheRecordedChecks(unittest.TestCase):
    def test_everything_passing_exits_zero(self) -> None:
        code, failures = aggregate()
        self.assertEqual(code, 0)
        self.assertEqual(failures, [])

    def test_a_failing_python_check_exits_non_zero(self) -> None:
        # The defect: this case used to exit 0 because `python_checks` was not consulted.
        code, failures = aggregate(python_checks=[
            {"step": "python: census", "ok": True},
            {"step": "python: the fixture is not counted toward criterion 1", "ok": False},
        ])
        self.assertEqual(code, 1)
        self.assertTrue(any("the fixture is not counted" in line for line in failures), failures)

    def test_a_failing_browser_record_exits_non_zero(self) -> None:
        code, failures = aggregate(trace_records=[
            {"step": "A/list", "ok": True},
            {"step": "A/chain", "ok": False},
        ], trace_exit=1)
        self.assertEqual(code, 1)
        self.assertTrue(any("A/chain" in line for line in failures), failures)

    def test_an_undeclared_block_is_a_failure(self) -> None:
        # The trace records `None` for a check it could not decide. That is not success, and
        # nothing declared it in advance, so it must fail the run.
        code, failures = aggregate(trace_records=[{"step": "A/acl", "ok": None}])
        self.assertEqual(code, 1)
        self.assertTrue(any("BLOCKED" in line and "A/acl" in line for line in failures), failures)

    def test_a_declared_block_does_not_fail_the_run(self) -> None:
        # P7-01 criterion 1 is BLOCKED because no real FAIL run exists: it is the documented
        # finding, declared with `blocked_is_expected`. It must not fail the run, and it must not
        # be counted as a pass either.
        code, failures = aggregate(python_checks=[
            {"step": "python: criterion 1 census", "ok": None, "blocked_is_expected": True},
        ])
        self.assertEqual(code, 0)
        self.assertEqual(failures, [])

    def test_an_undeclared_python_block_fails_the_run(self) -> None:
        code, failures = aggregate(python_checks=[{"step": "python: something", "ok": None}])
        self.assertEqual(code, 1)
        self.assertTrue(any("not declared in advance" in line for line in failures), failures)

    def test_a_non_zero_trace_process_exits_non_zero(self) -> None:
        code, failures = aggregate(trace_exit=2)
        self.assertEqual(code, 1)
        self.assertTrue(any("exited 2" in line for line in failures), failures)

    def test_an_empty_check_list_is_not_a_pass(self) -> None:
        # A run that recorded nothing must not look like a run that recorded success.
        code, failures = aggregate(trace_records=[], python_checks=[])
        self.assertEqual(code, 1)
        self.assertTrue(any("no checks at all" in line for line in failures), failures)
        self.assertTrue(any("no python checks" in line for line in failures), failures)

    def test_the_preconditions_still_fail_the_run(self) -> None:
        self.assertEqual(aggregate(real_tree_unchanged=False)[0], 1)
        self.assertEqual(aggregate(leaked=["api-responses.jsonl"])[0], 1)
        self.assertEqual(aggregate(no_verdict_upgraded=False)[0], 1)
        self.assertTrue(any("upgraded" in line
                            for line in aggregate(no_verdict_upgraded=False)[1]))

    def test_it_reports_every_failure_not_just_the_first(self) -> None:
        code, failures = aggregate(
            trace_exit=1,
            trace_records=[{"step": "A/one", "ok": False}, {"step": "A/two", "ok": None}],
            python_checks=[{"step": "python: one", "ok": False}],
            real_tree_unchanged=False,
            leaked=["x"],
            no_verdict_upgraded=False,
        )
        self.assertEqual(code, 1)
        self.assertGreaterEqual(len(failures), 7, failures)


if __name__ == "__main__":
    unittest.main()
