#!/usr/bin/env python3
"""Criterion 2: missing Context / Agent shown as missing, nothing fabricated,
no verdict upgrade.

Real records: the Context chain link is missing in 9/9 across three forms
(sentinel, reason-only, recorded-run-details-with-no-pack); `agent_response` is
missing in every case of the six run-level BLOCKED records. The served verdict
is compared to the verdict in the file exactly. `verdict_consistent` is present
as a report; a labelled fixture with a deliberately inconsistent recorded verdict
shows the API serves the recorded verdict and merely reports the inconsistency.

Run: PYTHONPATH=../../src:. python3 c2_missing.py
"""

from __future__ import annotations

import json
from pathlib import Path

from final_harness import Live, Checker, emit

REAL = ("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
        "/validation-runs")
OUT = Path(__file__).resolve().parent
FROZEN_CONTEXT_REASONS = {
    "recorded as N/A (P0 not implemented)",
    "the case records a reason, not a context pack",
    "the section records run details, not a context pack",
}


def main() -> int:
    c = Checker("criterion 2")
    live = Live()
    try:
        granted = live.key(["validation:read"])
        files = sorted(Path(REAL).rglob("run-*.json"))
        c.check("nine real records present", len(files) == 9, len(files))

        seen_context_reasons: set[str] = set()
        case_count = 0
        packs = 0
        context_present_kinds: dict[str, int] = {}
        agent_missing_blocked = 0
        blocked_runs = 0
        pass_runs_with_agent = 0
        pass_runs = 0
        verdict_mismatches = []
        inconsistent = 0
        detail_dump = {}

        with live.root(REAL):
            for path in files:
                record = json.loads(path.read_text(encoding="utf-8"))
                run_id = record["run_id"]
                status, _, detail, _ = live.call(
                    "GET", f"/v1/validation/runs/{run_id}", granted)
                assert status == 200, (run_id, status, detail)
                detail_dump[run_id] = detail

                # verdict served == verdict recorded, exactly.
                if detail["verdict"] != record["verdict"]:
                    verdict_mismatches.append(
                        (run_id, detail["verdict"], record["verdict"]))
                if not detail["verdict_consistent"]:
                    inconsistent += 1

                file_cases = {case["case_id"]: case for case in record["cases"]}
                c.check(f"{run_id}: detail case count matches the file",
                        len(detail["cases"]) == len(record["cases"]),
                        (len(detail["cases"]), len(record["cases"])))
                for case in detail["cases"]:
                    case_count += 1
                    source = file_cases[case["case_id"]]
                    if case["verdict"] != source["verdict"]:
                        verdict_mismatches.append(
                            (run_id, case["case_id"], case["verdict"], source["verdict"]))
                    # Context section + chain link.
                    section = case["sections"]["context"]
                    kind = section["present_kind"]
                    context_present_kinds[kind] = context_present_kinds.get(kind, 0) + 1
                    if section.get("context_pack_present"):
                        packs += 1
                    chain_context = case["chain"]["context"]
                    if chain_context["present"]:
                        seen_context_reasons.add("PRESENT")
                    else:
                        seen_context_reasons.add(chain_context["missing_reason"])
                    # reason_only / sentinel / value fidelity, nothing fabricated.
                    if "context" not in source:
                        c.check(f"{run_id}/{case['case_id']}: absent context is named",
                                kind == "absent", kind)
                    elif source["context"] == "N/A (P0 not implemented)":
                        c.check(f"{run_id}/{case['case_id']}: sentinel context",
                                kind == "sentinel" and section["present"] is False, kind)
                    elif isinstance(source["context"], dict) and set(source["context"]) <= {
                            "reason", "note", "detail", "not_applied_reason"}:
                        c.check(f"{run_id}/{case['case_id']}: reason-only context",
                                kind == "reason_only" and section["present"] is False,
                                kind)
                        expected_reason = (source["context"].get("reason")
                                           or next(iter(source["context"].values())))
                        c.check(f"{run_id}/{case['case_id']}: reason served verbatim",
                                section.get("recorded_reason") == expected_reason,
                                section.get("recorded_reason"))
                    else:
                        c.check(f"{run_id}/{case['case_id']}: run-details context served "
                                "verbatim, not treated as a pack",
                                kind == "value" and section["present"] is True
                                and section["context_pack_present"] is False
                                and section["value"] == source["context"], kind)
                    # Agent chain link.
                    if record["verdict"] == "BLOCKED":
                        if not case["chain"]["agent"]["present"]:
                            agent_missing_blocked += 1

                if record["verdict"] == "BLOCKED":
                    blocked_runs += 1
                    c.check(f"{run_id}: agent missing in every case of a BLOCKED run",
                            all(not x["chain"]["agent"]["present"] for x in detail["cases"]))
                else:
                    pass_runs += 1
                    if any(x["chain"]["agent"]["present"] for x in detail["cases"]):
                        pass_runs_with_agent += 1

        emit(OUT / "c2-01-real-details.json", detail_dump)
        c.check("served run verdict == recorded run verdict for all nine",
                not verdict_mismatches, verdict_mismatches[:5])
        c.check("all nine real runs report verdict_consistent=true (a report, not a rewrite)",
                inconsistent == 0, inconsistent)
        c.check("63 real cases walked", case_count == 63, case_count)
        c.check("no real case has a context pack", packs == 0, packs)
        c.check("the three frozen Context missing_reasons are all exercised",
                seen_context_reasons == FROZEN_CONTEXT_REASONS, sorted(seen_context_reasons))
        c.check("context present_kind census is 6 sentinel / 27 reason_only / 30 value",
                context_present_kinds == {"sentinel": 6, "reason_only": 27, "value": 30},
                context_present_kinds)
        c.check("6 BLOCKED runs have agent missing in all 7 cases",
                blocked_runs == 6 and agent_missing_blocked == 42,
                (blocked_runs, agent_missing_blocked))
        c.check("3 PASS runs have agent_response present in at least one case",
                pass_runs == 3 and pass_runs_with_agent == 3,
                (pass_runs, pass_runs_with_agent))

        # -- an explicitly inconsistent fixture: verdict reported, never rewritten
        inconsistent_root = live.base / "c2-inconsistent"
        fixture = json.loads(
            (Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "p7"
             / "labelled-fail-run.json").read_text())
        # recorded verdict PASS but the only case is BLOCKED -> the recorder's
        # worst-case rule would say BLOCKED. The API must serve PASS and report
        # verdict_consistent=false.
        fixture["run_id"] = "run-20261008T000101Z"
        fixture["verdict"] = "PASS"
        fixture["cases"][0]["verdict"] = "BLOCKED"
        fixture["synthetic"] = True
        fixture["synthetic_note"] = "NOT EVIDENCE: inconsistent-verdict fixture"
        d = inconsistent_root / "inc" / fixture["run_id"]
        d.mkdir(parents=True)
        (d / f"{fixture['run_id']}.json").write_text(json.dumps(fixture))
        with live.root(inconsistent_root):
            status, _, detail, _ = live.call(
                "GET", f"/v1/validation/runs/{fixture['run_id']}", granted)
            emit(OUT / "c2-02-inconsistent-verdict-detail.json", detail)
            c.check("an inconsistent record is still served (200)", status == 200, status)
            c.check("the recorded verdict is served unchanged (PASS), not corrected",
                    detail["verdict"] == "PASS", detail["verdict"])
            c.check("verdict_consistent=false is reported for the inconsistency",
                    detail["verdict_consistent"] is False, detail["verdict_consistent"])
            c.check("the case verdict is served unchanged (BLOCKED)",
                    detail["cases"][0]["verdict"] == "BLOCKED",
                    detail["cases"][0]["verdict"])
            # The real root is unaffected by the fixture.
        # -- the fourth form: the field is absent from the record entirely ---
        absent_root = live.base / "c2-absent"
        absent_id = "run-20261008T000201Z"
        absent_dir = absent_root / "leaf" / absent_id
        absent_dir.mkdir(parents=True)
        absent_data = json.loads(
            (Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "p7"
             / "ok-run.json").read_text())
        absent_data["run_id"] = absent_id
        del absent_data["cases"][0]["context"]
        del absent_data["cases"][0]["agent_response"]
        (absent_dir / f"{absent_id}.json").write_text(json.dumps(absent_data))
        with live.root(absent_root):
            status, _, detail, _ = live.call(
                "GET", f"/v1/validation/runs/{absent_id}", granted)
            emit(OUT / "c2-07-absent-fields-detail.json", detail)
            case = detail["cases"][0]
            c.check("absent context section is named absent, fabricating no value",
                    case["sections"]["context"]["present_kind"] == "absent"
                    and case["sections"]["context"]["present"] is False
                    and case["sections"]["context"]["missing_reason"]
                    == "the record has no such field", case["sections"]["context"])
            c.check("absent context chain link is missing with a named reason",
                    case["chain"]["context"]["present"] is False
                    and case["chain"]["context"]["missing_reason"]
                    == "the record has no such field", case["chain"]["context"])
            c.check("absent agent_response is named absent",
                    case["sections"]["agent_response"]["present_kind"] == "absent",
                    case["sections"]["agent_response"])
            c.check("absent agent chain link is missing with a named reason",
                    case["chain"]["agent"]["present"] is False
                    and case["chain"]["agent"]["missing_reason"]
                    == "the record has no such field", case["chain"]["agent"])
            c.check("the absent-field record's verdict is still served as recorded",
                    detail["verdict"] == absent_data["verdict"], detail["verdict"])

        # -- the real root is unaffected by any fixture ----------------------
        with live.root(REAL):
            status, _, real_body, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            c.check("the inconsistent fixture is absent from the real root",
                    all(r["run_id"] != fixture["run_id"] for r in real_body["runs"]))
            c.check("the absent-field fixture is absent from the real root",
                    all(r["run_id"] != absent_id for r in real_body["runs"]))
            c.check("the real root still lists exactly nine",
                    real_body["total"] == 9, real_body["total"])

        emit(OUT / "c2-03-criterion2-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
