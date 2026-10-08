#!/usr/bin/env python3.13
"""P6-03c failure negative: a failing check must make the harness exit non-zero.

`scripts/p6/p7_realcore_ui.py` writes a per-check PASS/FAIL table but the process exit code is
what an automated gate watches, and the original form ignored `python_checks`
(`tests/p6/test_p7_realcore_ui_exit_code.py` pins the aggregation function; this file proves the
real process obeys it).

This runs the **whole harness** — real Chrome, real proxy, real P7-01 Core — against a
**synthetic real root** built in a temporary directory, in which one record has been re-ided to a
run id the harness's own fixture root uses. That makes the python check "the fixture is not
counted toward criterion 1" fail by construction, because a fixture id then appears among the ids
the census counted.

The operator's own records tree is never touched: the synthetic root is a copy under the
temporary directory this script creates and removes.

Exit status is this script's own verdict, not the harness's:

* `0` — the harness exited non-zero **and** its `result.md` records the failing check, i.e. the
  exit code is a usable gate;
* `1` — the harness exited zero (the defect is back), or the failure is missing from `result.md`,
  or the harness could not be run at all.

The harness bundle it produces is kept, so the negative is itself evidence.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

#: One of the run ids the harness's fixture root uses (see `p7_realcore_env.py`). Re-iding a
#: record in the synthetic real root to this value is what falsifies the census check.
FIXTURE_ID = "run-20260101T000001Z"

CHECK_UNDER_TEST = "the fixture is not counted toward criterion 1"


def build_synthetic_root(destination: Path, real_runs: Path) -> tuple[Path, list[str]]:
    """Copy the real root, then re-id ONE record to a fixture id.

    Returns the synthetic root and the ids it now holds, so the caller can report exactly what
    was changed.
    """
    records = sorted(real_runs.rglob("run-*.json"))
    if not records:
        raise SystemExit(f"no run-*.json under {real_runs}; cannot build the negative")
    for source in records:
        target = destination / source.relative_to(real_runs)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # The first record, in sorted order, becomes the fixture id.
    victim = destination / records[0].relative_to(real_runs)
    data = json.loads(victim.read_text(encoding="utf-8"))
    original_id = data["run_id"]
    data["run_id"] = FIXTURE_ID
    renamed = victim.with_name(f"{FIXTURE_ID}.json")
    victim.unlink()
    renamed.write_text(json.dumps(data), encoding="utf-8")
    ids = sorted(json.loads(path.read_text(encoding="utf-8"))["run_id"]
                 for path in destination.rglob("run-*.json"))
    return destination, [f"{original_id} -> {FIXTURE_ID}", *ids]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--real-runs", type=Path,
                        default=ROOT / "validation-runs", dest="real_runs",
                        help="the operator's records tree, used read-only as the source to copy")
    parser.add_argument("--ref", default=None, help="P7-01 ref to extract (default: the driver's)")
    parser.add_argument("--repo", type=Path, default=None, help="the P7-01 worktree")
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)

    # The operator's tree for this run is the *synthetic* copy. The real tree is read once, to
    # copy from, and never written.
    source_root = args.real_runs
    if source_root.name == "validation-runs" and (source_root / "p6").is_dir():
        # The caller pointed at the dashboard's own (gitignored) tree; take the P7-01 records.
        raise SystemExit("point --real-runs at the P7-01 records tree "
                         "(jasmine-core-v1/validation-runs), not the dashboard's")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    # Deliberately NOT named like an acceptance bundle: this one is falsified on purpose, and it
    # must be impossible to mistake for a drill-down result.
    out = ROOT / "validation-runs" / "p6" / f"NEGATIVE-{stamp}-p6-03c-exit-code"
    tmp = Path(tempfile.mkdtemp(prefix="p6-03c-exit-negative-"))
    try:
        synthetic = tmp / "real-runs"
        synthetic.mkdir(parents=True)
        _, changes = build_synthetic_root(synthetic, source_root)
        print("p6-03c-negative: synthetic real root built at "
              f"{synthetic} (1 of {len(changes) - 1} records re-ided)")
        print(f"p6-03c-negative:   {changes[0]}")

        command = [sys.executable, str(HERE / "p7_realcore_ui.py"),
                   "--out", str(out), "--real-runs", str(synthetic)]
        if args.ref:
            command += ["--ref", args.ref]
        if args.repo:
            command += ["--repo", str(args.repo)]
        print(f"p6-03c-negative: running {sys.executable} {HERE / 'p7_realcore_ui.py'}")
        completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
        (out / "negative-run.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (out / "negative-run.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        print(f"p6-03c-negative: harness exit code = {completed.returncode}")
        for line in completed.stdout.splitlines():
            if "FAILING" in line or line.strip().startswith("p6-03c:   -"):
                print(f"  {line}")

        result = (out / "result.md")
        text = result.read_text(encoding="utf-8") if result.is_file() else ""
        census = out / "fixture-census.json"
        measured = json.loads(census.read_text(encoding="utf-8")) if census.is_file() else {}
        overlap = measured.get("fixture_ids_inside_census")

        verdict_lines = []
        exit_is_a_gate = completed.returncode != 0
        result_records_fail = f"| {CHECK_UNDER_TEST}" not in text or "**FAIL**" in text
        named_fail = any(CHECK_UNDER_TEST in line
                         for line in text.splitlines() if "**FAIL**" in line)
        census_shows_overlap = bool(overlap)

        verdict_lines.append(f"harness exited non-zero: {exit_is_a_gate} "
                             f"(exit {completed.returncode})")
        verdict_lines.append(f"fixture-census.json records the overlap: {census_shows_overlap} "
                             f"({overlap})")
        verdict_lines.append(f"result.md records the failing check as FAIL: {named_fail}")
        verdict = (exit_is_a_gate and census_shows_overlap and named_fail)
        (out / "NOT-AN-ACCEPTANCE-RUN.md").write_text(
            "# This bundle is a deliberate FAILURE NEGATIVE, not an acceptance run\n\n"
            f"The harness was run against a **synthetic** records root in a temporary directory in "
            f"which one record was re-ided to `{FIXTURE_ID}`, so the census check "
            f"\"{CHECK_UNDER_TEST}\" fails by construction. That is the point: it proves a failing "
            "check makes the harness exit non-zero.\n\n"
            "- the operator's own records tree was read once, to copy from, and never written;\n"
            "- none of the PASS/FAIL numbers here describe the read slice;\n"
            "- the positive run to cite is the `*-p6-03c-validation-drilldown` bundle.\n",
            encoding="utf-8")
        (out / "negative-verdict.json").write_text(json.dumps({
            "negative": "a failing python check makes the harness exit non-zero",
            "fixture_id_planted": FIXTURE_ID,
            "changes": changes,
            "harness_exit_code": completed.returncode,
            "fixture_ids_inside_census": overlap,
            "result_md_records_fail": named_fail,
            "check_under_test": CHECK_UNDER_TEST,
            "negative_passed": verdict,
            "note": "the harness bundle above is the falsified run; it is evidence for the exit "
                    "code, not for the read slice, and it must never be counted as an acceptance "
                    "run",
        }, indent=2) + "\n", encoding="utf-8")
        print("p6-03c-negative: " + "; ".join(verdict_lines))
        print(f"p6-03c-negative: bundle (falsified, not an acceptance run) {out}")
        print(f"p6-03c-negative: NEGATIVE {'PASSED' if verdict else 'FAILED'}")
        return 0 if verdict else 1
    finally:
        if args.keep_temp:
            print(f"p6-03c-negative: kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
