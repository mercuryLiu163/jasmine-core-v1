#!/usr/bin/env python3
"""NEGATIVE CONTROL (not evidence for f0aea88).

Runs the Part D (root-symlink) and Part E (masked-over-cap) probes from
`fix_defects.py` against the SUPERSEDED candidate `0ae8041`, whose `src/` is
extracted read-only with `git archive`. Its purpose is only to show that the
probes have teeth: the defect classes they assert against are detected on the
commit that had them. Nothing here is acceptance evidence for `f0aea88`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WT = HERE.parents[3]          # evidence-fix/control-old -> p7-01 worktree
OLD_SHA = "0ae80411824739b11485c61ed388d86093f2be7c"
OLD_SRC = Path("/tmp/p7-01-control-old-src")

if OLD_SRC.exists():
    shutil.rmtree(OLD_SRC)
OLD_SRC.mkdir(parents=True)
archive = subprocess.run(["git", "archive", OLD_SHA, "src"], cwd=WT,
                         capture_output=True, check=True)
subprocess.run(["tar", "-x", "-C", str(OLD_SRC)], input=archive.stdout, check=True)
sys.path.insert(0, str(OLD_SRC / "src"))

import fix_harness  # noqa: E402
# control-old/ is one directory deeper than evidence-fix/, so repoint the fixture
# finder explicitly rather than relying on the copied module's parent count.
fix_harness.FIXTURES = WT / "tests" / "fixtures" / "p7"
fix_harness.FILES = fix_harness.FIXTURES / "files"
import fix_defects as fd  # noqa: E402
from fix_harness import Checker, Live  # noqa: E402
import jasmine_core.validation.reads as old_reads  # noqa: E402

print(f"control source: {OLD_SRC}/src  (extracted from {OLD_SHA})")
print(f"control reads module: {old_reads.__file__}")


def main() -> int:
    fd.OUT = HERE            # keep all control output inside control-old/
    c = Checker("control-old-0ae8041")
    live = Live()
    try:
        granted = live.key(["validation:read"])
        fd.part_a_manifest_swap(live, c)
        fd.part_b_fifo(live, c, granted)
        fd.part_d_root_symlink(live, c, granted)
        fd.part_e_masked_cap(live, c, granted)
        summary = c.summary()
        (HERE / "control-old-report.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"CONTROL-OLD: {summary['checks']} checks, {summary['failed']} failed")
        return 0
    finally:
        live.close()


if __name__ == "__main__":
    raise SystemExit(main())
