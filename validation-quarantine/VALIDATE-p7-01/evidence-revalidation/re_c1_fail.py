#!/usr/bin/env python3
"""Criterion 1: an existing real FAIL Run.

(a) Independently confirm no real P7-format FAIL run exists.
(b) Confirm the FAIL code path is exercised only by a clearly labelled synthetic
    fixture, that nothing counts it as evidence, and that no verdict is upgraded.
(c) Report criterion 1 BLOCKED (see findings.md).

Run: PYTHONPATH=../../src:. python3 c1_fail.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from re_harness import FIXTURES, Live, Checker, emit, sha256_file, write_run

REAL = ("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
        "/validation-runs")
OUT = Path(__file__).resolve().parent
FIXTURE_RUN_ID = "run-20261008T000002Z"


def main() -> int:
    c = Checker("criterion 1")
    live = Live()
    fixture_root = live.base / "validator-fixture-root"
    try:
        # --- (b) the labelled synthetic FAIL fixture, in the validator's own root
        target = fixture_root / "p7-fail" / FIXTURE_RUN_ID
        write_run(target, FIXTURE_RUN_ID, "labelled-fail-run.json")
        fixture_file = target / f"{FIXTURE_RUN_ID}.json"
        fixture_data = json.loads(fixture_file.read_text())
        emit(OUT / "c1-02-labelled-fail-fixture-record.json", fixture_data)

        granted = live.key(["validation:read"])

        # --- (a) the real root: nine records, zero FAIL
        with live.root(REAL):
            status, headers, real_body, raw = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            emit(OUT / "c1-03-real-root-list.json", real_body)
            c.check("real root list is 200", status == 200, status)
            c.check("real root total is 9", real_body["total"] == 9, real_body["total"])
            c.check("no real run serves verdict FAIL",
                    all(r["verdict"] != "FAIL" for r in real_body["runs"]))
            c.check("no real case carries a FAIL verdict",
                    all(r["case_counts"]["fail"] == 0 for r in real_body["runs"]))
            blocked = [r for r in real_body["runs"] if r["verdict"] == "BLOCKED"]
            pas = [r for r in real_body["runs"] if r["verdict"] == "PASS"]
            c.check("3 PASS / 6 BLOCKED at run level",
                    (len(pas), len(blocked)) == (3, 6), (len(pas), len(blocked)))
            # The fixture id is NOT reachable through the operator's real root.
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{FIXTURE_RUN_ID}", granted)
            emit(OUT / "c1-04-fixture-id-under-real-root.json", body)
            c.check("the fixture run id 404s under the real root",
                    status == 404, status)
            c.check("that refusal is validation_run_not_found",
                    body.get("error", {}).get("code") == "validation_run_not_found",
                    body.get("error", {}).get("code"))

        # --- (b) the FAIL code path, exercised over HTTP on the fixture root
        with live.root(fixture_root):
            status, _, list_body, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            emit(OUT / "c1-05-fixture-root-list.json", list_body)
            c.check("fixture root list is 200", status == 200, status)
            row = next((r for r in list_body["runs"] if r["run_id"] == FIXTURE_RUN_ID), None)
            c.check("the fixture run is listed", row is not None)
            c.check("the fixture run serves verdict FAIL (the FAIL code path)",
                    row and row["verdict"] == "FAIL", row and row["verdict"])
            c.check("the fixture row marks itself synthetic",
                    row and row["synthetic"] is True, row and row["synthetic"])
            c.check("the fixture row is never graded real",
                    row and row["evidence_grade"] != "real",
                    row and row["evidence_grade"])
            c.check("the fixture row's run grade is the documented min with the "
                    "absent-manifest 'unknown' mode",
                    row and row["evidence_grade"] == "unknown",
                    row and row["evidence_grade"])
            c.check("the fixture row counts one FAIL case",
                    row and row["case_counts"]["fail"] == 1, row and row["case_counts"])

            status, _, detail, _ = live.call(
                "GET", f"/v1/validation/runs/{FIXTURE_RUN_ID}", granted)
            emit(OUT / "c1-06-labelled-fail-detail.json", detail)
            c.check("fixture detail is 200", status == 200, status)
            c.check("recorded run verdict served unchanged (FAIL)",
                    detail["verdict"] == fixture_data["verdict"],
                    detail["verdict"])
            c.check("synthetic=true surfaced in detail", detail["synthetic"] is True)
            c.check("synthetic_note surfaced verbatim",
                    detail["synthetic_note"] == fixture_data["synthetic_note"])
            case = detail["cases"][0]
            recorded_case = fixture_data["cases"][0]
            c.check("case verdict served unchanged (FAIL)",
                    case["verdict"] == recorded_case["verdict"], case["verdict"])
            c.check("failure_class traceable to the record",
                    case["failure_class"] == recorded_case["failure_class"],
                    case["failure_class"])
            c.check("failure_output traceable to the record",
                    case["failure_output"] == recorded_case["failure_output"],
                    case["failure_output"])
            c.check("the failing case is graded simulated",
                    case["evidence_grade"] == "simulated", case["evidence_grade"])
            c.check("grade_basis names the synthetic run",
                    "synthetic" in case["grade_basis"], case["grade_basis"])
            c.check("verdict_consistent is reported, not enforced",
                    detail["verdict_consistent"] is True, detail["verdict_consistent"])

        # --- the fixture is nowhere in the operator's real root
        real_files = {p.resolve() for p in Path(REAL).rglob("run-*.json")}
        c.check("the fixture file lives outside the operator run root",
                fixture_file.resolve() not in real_files)
        c.check("the operator run root is unchanged (9 files)",
                len(real_files) == 9, len(real_files))
        c.check("fixture record is self-labelled synthetic in the file",
                fixture_data.get("synthetic") is True)
        c.check("fixture record declares stage P7 (not P0)",
                fixture_data.get("stage") == "P7", fixture_data.get("stage"))

        emit(OUT / "c1-07-criterion1-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
