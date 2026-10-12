#!/usr/bin/env python3
"""Criterion 5 (re-validation) — pagination, corrupt != empty, unreadable subtree,
plus the fixed discovery / duplicate-resolution / bounded-read / counter behaviour.

Parts:
  1  full page walk over the real nine with a small limit
  2  fixture root: nine copies + a corrupt row + a parseable duplicate pair
  3  discovery depth: a record in a directory 4 levels down is found, 5 is pruned
     and counted `depth_cap`
  4  duplicate resolution for every combination, with list/detail agreement
     (single_user and acl + validation:unattributed)
  5  bounded reads: oversize manifest refused before being read; oversize record
     corrupt rather than read
  6  counters describe the filtered served set (F7)
  7  corrupt record is never disguised as an empty list; empty_reason values
  8  an unreadable subtree refuses the list and the by-id lookup of an unknown id
  9  the real root still reports no corrupt/duplicate rows

Run: PYTHONPATH=../../../src:. python3 re_c5_pages.py
"""

from __future__ import annotations

import os
from pathlib import Path

from re_harness import Live, Checker, emit, sha256_file, write_manifest, write_run

REAL = ("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
        "/validation-runs")
OUT = Path(__file__).resolve().parent
RUN_DUP = "run-20261008T000001Z"
RUN_CORRUPT = "run-20261008T000004Z"

S1 = "run-20261008T000101Z"   # one parseable + one corrupt share the stem
S2 = "run-20261008T000102Z"   # two parseable share the stem
S3 = "run-20261008T000103Z"   # only corrupt files share the stem
S4 = "run-20261008T000104Z"   # one parseable (control)

D3 = "run-20261008T000201Z"   # directory depth 3
D4 = "run-20261008T000202Z"   # directory depth 4  -> inside the cap
D5 = "run-20261008T000203Z"   # directory depth 5  -> pruned

MB_ID = "run-20261008T000301Z"   # manifest.json over 1 MiB
MR_ID = "run-20261008T000302Z"   # run record over 16 MiB

CORRUPT = "{not json at all"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024


def tolerant_call(live, path, token):
    """GET a path; on a transport-level disconnect, probe liveness and retry once.

    The server closes the socket without a response *and without logging a
    status*, which is a transport event, not a handler response (dispatch turns
    every handler exception into a 500). Record it, then retry so the run can
    finish; the raw transcript keeps the evidence.
    """
    try:
        return live.call("GET", path, token)
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"[transport] {path} raised {type(exc).__name__}: {exc}", flush=True)
        print(f"[transport] server thread alive: {live.thread.is_alive()}", flush=True)
        try:
            status, _, _, _ = live.call("GET", "/v1/validation/runs?limit=100", token)
            print(f"[transport] liveness probe after failure -> {status}", flush=True)
        except Exception as exc2:  # noqa: BLE001
            print(f"[transport] liveness probe also raised {type(exc2).__name__}",
                  flush=True)
        return live.call("GET", path, token)


def walk_pages(live, token, limit, base="/v1/validation/runs"):
    pages = []
    offset = 0
    guard = 0
    while True:
        guard += 1
        assert guard < 100, "page walk did not terminate"
        status, _, body, _ = live.call(
            "GET", f"{base}?limit={limit}&offset={offset}", token)
        assert status == 200, (status, body)
        pages.append(body)
        offset += body["count"]
        if not body["has_more"]:
            break
    return pages


def main() -> int:
    c = Checker("criterion 5")
    live = Live()
    try:
        granted = live.key(["validation:read"])

        # -- Part 1: page the real root with limit=2 -------------------------
        real_files = sorted(Path(REAL).rglob("run-*.json"))
        real_stems = sorted(p.stem for p in real_files)
        with live.root(REAL):
            pages = walk_pages(live, granted, 2)
            emit(OUT / "c5-01-real-root-pages.json", pages)
            seen = [row["run_id"] for page in pages for row in page["runs"]]
            c.check("real root page walk with limit=2 covers all nine",
                    sorted(seen) == real_stems, (len(seen), len(real_stems)))
            c.check("real root page walk has no repeats", len(seen) == len(set(seen)),
                    (len(seen), len(set(seen))))
            totals = {page["total"] for page in pages}
            c.check("real root total is stable at 9 across pages", totals == {9},
                    sorted(totals))
            c.check("real root corrupt_count is 0 on every page",
                    all(p["corrupt_count"] == 0 for p in pages))
            c.check("real root duplicate_count is 0 on every page",
                    all(p["duplicate_count"] == 0 for p in pages))
            for index, page in enumerate(pages):
                c.check(f"page {index} count == len(runs)",
                        page["count"] == len(page["runs"]),
                        (page["count"], len(page["runs"])))
                c.check(f"page {index} has_more arithmetic",
                        page["has_more"] == (page["offset"] + page["count"] < page["total"]),
                        (page["has_more"], page["offset"], page["count"], page["total"]))
                c.check(f"page {index} offset is n*limit", page["offset"] == index * 2,
                        page["offset"])
            c.check("the union equals the 9 files by run_id, exactly once each",
                    sorted(seen) == real_stems and len(seen) == 9)

        # -- Part 2: copies + corrupt + duplicate stem -----------------------
        fixture_root = live.base / "c5-root"
        for index, path in enumerate(real_files):
            dest = fixture_root / f"ok{index:02d}" / "leaf" / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())
        corrupt_dir = fixture_root / "corrupt" / RUN_CORRUPT
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / f"{RUN_CORRUPT}.json").write_text(CORRUPT)
        write_run(fixture_root / "pair-a" / RUN_DUP, RUN_DUP)
        write_run(fixture_root / "pair-b" / RUN_DUP, RUN_DUP, notes=["the duplicate copy"])

        with live.root(fixture_root):
            status, _, full, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            emit(OUT / "c5-02-fixture-full-list.json", full)
            c.check("fixture full list is 200", status == 200, status)
            c.check("fixture total is 12 (9 real + 1 corrupt + 2 duplicate rows)",
                    full["total"] == 12, full["total"])
            c.check("fixture count is 12", full["count"] == 12, full["count"])
            c.check("corrupt_count is 1", full["corrupt_count"] == 1,
                    full["corrupt_count"])
            c.check("duplicate_count is 2", full["duplicate_count"] == 2,
                    full["duplicate_count"])
            statuses = [row["record_status"] for row in full["runs"]]
            c.check("one corrupt row is listed", statuses.count("corrupt") == 1,
                    statuses.count("corrupt"))
            c.check("two duplicate rows are listed", statuses.count("duplicate") == 2,
                    statuses.count("duplicate"))
            corrupt_row = next(r for r in full["runs"] if r["record_status"] == "corrupt")
            c.check("corrupt row serves verdict null", corrupt_row["verdict"] is None,
                    corrupt_row["verdict"])
            c.check("corrupt row names a corrupt_reason",
                    bool(corrupt_row["corrupt_reason"]), corrupt_row["corrupt_reason"])
            c.check("corrupt row is never disguised as an empty list",
                    full["runs"] and full["total"] > 0
                    and full.get("empty_reason") is None, full.get("empty_reason"))
            dup_rows = [r for r in full["runs"] if r["record_status"] == "duplicate"]
            c.check("duplicate rows name a duplicate_reason",
                    all(r["duplicate_reason"] for r in dup_rows))
            c.check("the two duplicate rows are distinguishable by record_sha256",
                    len({r["record_sha256"] for r in dup_rows}) == 2,
                    [r["record_sha256"] for r in dup_rows])

            pages = walk_pages(live, granted, 4)
            emit(OUT / "c5-03-fixture-pages.json", pages)
            seen = [(row["run_id"], row["record_sha256"]) for page in pages
                    for row in page["runs"]]
            c.check("fixture page walk covers all 12 rows exactly once",
                    len(seen) == 12 and len(set(seen)) == 12, (len(seen), len(set(seen))))
            c.check("fixture page walk union equals the single 100-limit page",
                    seen == [(r["run_id"], r["record_sha256"]) for r in full["runs"]])
            for index, page in enumerate(pages):
                c.check(f"fixture page {index} has_more arithmetic",
                        page["has_more"] == (page["offset"] + page["count"] < page["total"]),
                        (page["has_more"], page["offset"], page["count"], page["total"]))
                c.check(f"fixture page {index} count == len(runs)",
                        page["count"] == len(page["runs"]))

        # -- Part 3: discovery depth boundary --------------------------------
        depth_root = live.base / "c5-depth"
        write_run(depth_root / "e0" / "e1" / "e2", D3)
        write_run(depth_root / "d0" / "d1" / "d2" / "d3", D4)
        write_run(depth_root / "d0" / "d1" / "d2" / "d3" / "d4", D5)
        with live.root(depth_root):
            status, _, body, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            emit(OUT / "c5-12-depth-boundary.json", body)
            ids = sorted(row["run_id"] for row in body["runs"])
            c.check("depth root list is 200", status == 200, status)
            c.check("a record 3 directory levels below the root is found",
                    D3 in ids, ids)
            c.check("a record whose directory is 4 levels below the root is found",
                    D4 in ids, ids)
            c.check("a record 5 directory levels below the root is pruned",
                    D5 not in ids, ids)
            c.check("exactly the two inside-cap records are served",
                    ids == sorted([D3, D4]), ids)
            c.check("the pruned directory is counted once as depth_cap",
                    body["ignored_entries"] == 1, body["ignored_entries"])
            samples = body.get("ignored_samples", [])
            cap_samples = [s for s in samples if s.get("reason") == "depth_cap"]
            c.check("an ignored sample names the depth_cap reason",
                    any(s.get("reason") == "depth_cap" for s in samples), samples)
            c.check("the depth_cap sample names the pruned directory (d4)",
                    any(str(s.get("relative_path", "")).endswith("d4")
                        for s in cap_samples), cap_samples)
            c.check("discovery.max_depth is 4", body["discovery"]["max_depth"] == 4,
                    body["discovery"]["max_depth"])

        # -- Part 4: duplicate resolution, every combination -----------------
        combo_root = live.base / "c5-combo"
        s1_ok = combo_root / "s1-ok" / S1
        s1_bad = combo_root / "s1-bad" / S1
        write_run(s1_ok, S1)
        s1_bad.mkdir(parents=True)
        (s1_bad / f"{S1}.json").write_text(CORRUPT)
        s1_ok_sha = sha256_file(s1_ok / f"{S1}.json")
        s1_bad_sha = sha256_file(s1_bad / f"{S1}.json")
        write_run(combo_root / "s2-a" / S2, S2)
        write_run(combo_root / "s2-b" / S2, S2, notes=["the duplicate copy"])
        for where in ("s3-a", "s3-b"):
            d = combo_root / where / S3
            d.mkdir(parents=True)
            (d / f"{S3}.json").write_text(CORRUPT)
        write_run(combo_root / "s4" / S4, S4)

        #: stem -> (expected list statuses sorted, detail status, detail error code)
        expectations = {
            S1: (["corrupt", "ok"], 200, None),
            S2: (["duplicate", "duplicate"], 409, "validation_run_ambiguous"),
            S3: (["corrupt", "corrupt"], 409, "validation_run_corrupt"),
            S4: (["ok"], 200, None),
        }

        def grouped_rows():
            status, _, listing, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            groups: dict[str, list[dict]] = {}
            for row in listing["runs"]:
                groups.setdefault(row["run_id"], []).append(row)
            return status, listing, groups

        def agreement(tag):
            status, listing, groups = grouped_rows()
            c.check(f"{tag}: combo list is 200", status == 200, status)
            c.check(f"{tag}: total == count == 7",
                    listing["total"] == 7 and listing["count"] == 7,
                    (listing["total"], listing["count"]))
            c.check(f"{tag}: the list's corrupt_count is 3",
                    listing["corrupt_count"] == 3, listing["corrupt_count"])
            c.check(f"{tag}: the list's duplicate_count is 2",
                    listing["duplicate_count"] == 2, listing["duplicate_count"])
            for stem, (want_statuses, want_status, want_code) in expectations.items():
                rows = groups.get(stem, [])
                got_statuses = sorted(row["record_status"] for row in rows)
                c.check(f"{tag} {stem}: list row statuses are {want_statuses}",
                        got_statuses == want_statuses, got_statuses)
                dstatus, _, detail, _ = live.call(
                    "GET", f"/v1/validation/runs/{stem}", granted)
                c.check(f"{tag} {stem}: detail status is {want_status}",
                        dstatus == want_status, dstatus)
                if want_code is None:
                    c.check(f"{tag} {stem}: detail serves run_id {stem}",
                            detail.get("run_id") == stem, detail.get("run_id"))
                else:
                    c.check(f"{tag} {stem}: detail code is {want_code}",
                            detail.get("error", {}).get("code") == want_code,
                            detail.get("error", {}).get("code"))
                # the list's record_status for a row must predict detail
                if any(r["record_status"] == "ok" for r in rows):
                    c.check(f"{tag} {stem}: an ok list row implies detail 200",
                            dstatus == 200, (dstatus, got_statuses))
                elif any(r["record_status"] == "duplicate" for r in rows):
                    c.check(f"{tag} {stem}: duplicate rows imply 409 ambiguous",
                            dstatus == 409 and detail.get("error", {}).get("code")
                            == "validation_run_ambiguous", (dstatus, got_statuses))
                else:
                    c.check(f"{tag} {stem}: corrupt-only rows imply 409 corrupt",
                            dstatus == 409 and detail.get("error", {}).get("code")
                            == "validation_run_corrupt", (dstatus, got_statuses))
                if stem == S1:
                    c.check(f"{tag} {stem}: an ok row carries the parseable digest",
                            any(r["record_sha256"] == s1_ok_sha for r in rows), rows)
                    c.check(f"{tag} {stem}: a corrupt row carries the corrupt digest",
                            any(r["record_sha256"] == s1_bad_sha for r in rows), rows)
            return listing

        with live.root(combo_root):
            emit(OUT / "c5-13-duplicate-combos-single-user.json", agreement("single_user"))

            # -- Part 6: counters describe the filtered served set (F7) ------
            status, _, pass_body, _ = live.call(
                "GET", "/v1/validation/runs?verdict=PASS", granted)
            emit(OUT / "c5-14-counters-verdict-pass.json", pass_body)
            c.check("verdict=PASS list is 200", status == 200, status)
            c.check("verdict=PASS serves only parseable rows",
                    {r["record_status"] for r in pass_body["runs"]} == {"ok", "duplicate"},
                    [r["record_status"] for r in pass_body["runs"]])
            c.check("verdict=PASS total/count/rows agree at 4",
                    pass_body["total"] == pass_body["count"] == len(pass_body["runs"]) == 4,
                    (pass_body["total"], pass_body["count"]))
            c.check("verdict=PASS corrupt_count is 0 on the served set",
                    pass_body["corrupt_count"] == 0, pass_body["corrupt_count"])
            c.check("verdict=PASS duplicate_count counts only served duplicates",
                    pass_body["duplicate_count"] == 2, pass_body["duplicate_count"])
            c.check("verdict=PASS corrupt_count matches the rows actually present",
                    pass_body["corrupt_count"]
                    == sum(1 for r in pass_body["runs"] if r["record_status"] == "corrupt"),
                    pass_body["corrupt_count"])
            status, _, blocked_body, _ = live.call(
                "GET", "/v1/validation/runs?verdict=BLOCKED", granted)
            emit(OUT / "c5-15-counters-filtered-empty.json", blocked_body)
            c.check("a filter matching nothing is 200 with total 0", status == 200
                    and blocked_body["total"] == 0, (status, blocked_body.get("total")))
            c.check("a filter matching nothing reports corrupt_count 0",
                    blocked_body["corrupt_count"] == 0, blocked_body["corrupt_count"])
            c.check("a filter matching nothing reports duplicate_count 0",
                    blocked_body["duplicate_count"] == 0, blocked_body["duplicate_count"])
            c.check("a filter matching nothing reports all_filtered_out",
                    blocked_body.get("empty_reason") == "all_filtered_out",
                    blocked_body.get("empty_reason"))
            status, _, stage_body, _ = live.call(
                "GET", "/v1/validation/runs?stage=P7", granted)
            c.check("stage=P7 serves four parseable rows and counts 0 corrupt",
                    stage_body["total"] == 4 and stage_body["corrupt_count"] == 0
                    and stage_body["duplicate_count"] == 2,
                    (stage_body["total"], stage_body["corrupt_count"],
                     stage_body["duplicate_count"]))

        # -- Part 4b: the same combinations in acl (F6) ----------------------
        # acl refuses to serve at all with zero grants, so plant one real grant
        # (on a project none of the combo records is attributed to).
        readiness_project = live.create_project("c5-acl-readiness")
        live.grant(readiness_project, "act_01K742SG00CBKP25A9ETPBTRMJ")
        live.set_mode("acl")
        read_only = live.key(["validation:read"], label="acl-read")
        acl_unattributed = live.key(["validation:read", "validation:unattributed"],
                                    label="acl-unattributed")
        acl_evidence = {}
        with live.root(combo_root):
            status, _, body, _ = tolerant_call(live, "/v1/validation/runs?limit=100",
                                           read_only)
            acl_evidence["read_only list"] = {"status": status, "body": body}
            c.check("acl read-only key sees no unattributed combo row",
                    status == 200 and body["total"] == 0, (status, body.get("total")))
            status, _, body, _ = tolerant_call(live, f"/v1/validation/runs/{S1}", read_only)
            acl_evidence["read_only S1 detail"] = {"status": status, "body": body}
            c.check("acl read-only key gets 404 for the S1 parseable record",
                    status == 404 and body.get("error", {}).get("code")
                    == "validation_run_not_found", (status, body))
            status, _, body, _ = tolerant_call(live, f"/v1/validation/runs/{S3}", read_only)
            c.check("acl read-only key gets 404 for the corrupt S3 record",
                    status == 404, (status, body.get("error", {}).get("code")))
            status, _, body, _ = tolerant_call(live, "/v1/validation/runs?limit=100",
                                           acl_unattributed)
            acl_evidence["unattributed list"] = {"status": status, "body": body}
            c.check("acl unattributed key sees all 7 combo rows",
                    body["total"] == 7, body.get("total"))
            c.check("acl unattributed list counts the visible corrupt rows",
                    body["corrupt_count"] == 3, body.get("corrupt_count"))
            c.check("acl unattributed list counts the visible duplicate rows",
                    body["duplicate_count"] == 2, body.get("duplicate_count"))
            status, _, body, _ = tolerant_call(live, f"/v1/validation/runs/{S1}", acl_unattributed)
            acl_evidence["unattributed S1 detail"] = {"status": status, "body": body}
            c.check("acl+unattributed S1 detail is 200 (parseable sibling wins)",
                    status == 200 and body.get("run_id") == S1, (status, body))
            status, _, body, _ = tolerant_call(live, f"/v1/validation/runs/{S2}", acl_unattributed)
            acl_evidence["unattributed S2 detail"] = {"status": status, "body": body}
            c.check("acl+unattributed S2 detail is 409 ambiguous",
                    status == 409 and body.get("error", {}).get("code")
                    == "validation_run_ambiguous", (status, body))
            status, _, body, _ = tolerant_call(live, f"/v1/validation/runs/{S3}", acl_unattributed)
            acl_evidence["unattributed S3 detail"] = {"status": status, "body": body}
            c.check("acl+unattributed S3 detail is 409 corrupt",
                    status == 409 and body.get("error", {}).get("code")
                    == "validation_run_corrupt", (status, body))
        emit(OUT / "c5-16-acl-combos.json", acl_evidence)
        live.set_mode("single_user")

        # -- Part 5: bounded reads -------------------------------------------
        big_root = live.base / "c5-big"
        mb_dir = big_root / "big-manifest" / MB_ID
        write_run(mb_dir, MB_ID)
        write_manifest(mb_dir, MB_ID, [], raw=" " * (MAX_MANIFEST_BYTES + 1))
        mr_dir = big_root / "big-record" / MR_ID
        mr_dir.mkdir(parents=True)
        (mr_dir / f"{MR_ID}.json").write_bytes(b"{" + b" " * (MAX_RECORD_BYTES + 1))
        mb_file = mb_dir / "manifest.json"
        mr_file = mr_dir / f"{MR_ID}.json"
        c.check("validator's oversize manifest is over 1 MiB on disk",
                mb_file.stat().st_size > MAX_MANIFEST_BYTES, mb_file.stat().st_size)
        c.check("validator's oversize record is over 16 MiB on disk",
                mr_file.stat().st_size > MAX_RECORD_BYTES, mr_file.stat().st_size)
        with live.root(big_root):
            status, _, listing, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            emit(OUT / "c5-17-bounded-reads-list.json", listing)
            c.check("oversize bundle root list is 200", status == 200, status)
            mb_row = next((r for r in listing["runs"] if r["run_id"] == MB_ID), None)
            mr_row = next((r for r in listing["runs"] if r["run_id"] == MR_ID), None)
            c.check("the oversize manifest is reported corrupt on the list row",
                    mb_row is not None and mb_row["manifest_status"] == "corrupt",
                    mb_row and mb_row["manifest_status"])
            c.check("the oversize record is reported corrupt on the list row",
                    mr_row is not None and mr_row["record_status"] == "corrupt",
                    mr_row and mr_row.get("record_status"))
            c.check("the oversize record reports no digest (it was not read)",
                    mr_row is not None and mr_row["record_sha256"] == "",
                    mr_row and mr_row.get("record_sha256"))
            c.check("the oversize record's reason names the size bound",
                    mr_row is not None and "exceeds" in (mr_row.get("corrupt_reason") or ""),
                    mr_row and mr_row.get("corrupt_reason"))
            c.check("the oversize record counts as corrupt",
                    listing["corrupt_count"] == 1, listing["corrupt_count"])
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{MR_ID}", granted)
            c.check("oversize record detail is 409 validation_run_corrupt",
                    status == 409 and body.get("error", {}).get("code")
                    == "validation_run_corrupt", (status, body))
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{MB_ID}/manifest", granted)
            emit(OUT / "c5-18-oversize-manifest.json", body)
            c.check("oversize manifest is refused with 409 validation_manifest_corrupt",
                    status == 409 and body.get("error", {}).get("code")
                    == "validation_manifest_corrupt", (status, body))
            c.check("oversize manifest rule names the size bound",
                    "exceeds" in (body.get("error", {}).get("details", {}).get("rule")
                                  or ""), body.get("error", {}).get("details"))
            c.check("no digest is reported for a manifest that was never read",
                    "manifest_sha256" not in body.get("error", {}).get("details", {}),
                    body.get("error", {}).get("details"))
            status, _, body, _ = tolerant_call(
                live, f"/v1/validation/runs/{MB_ID}/artifacts/art_missing", granted)
            c.check("an oversize manifest makes its artifacts unaddressable (404)",
                    status == 404 and body.get("error", {}).get("details", {}).get("reason")
                    == "manifest_corrupt", (status, body))

        # -- Part 7: corrupt-only / empty roots ------------------------------
        only_corrupt = live.base / "c5-corrupt-only"
        d = only_corrupt / "x" / RUN_CORRUPT
        d.mkdir(parents=True)
        (d / f"{RUN_CORRUPT}.json").write_text(CORRUPT)
        with live.root(only_corrupt):
            status, _, body, _ = live.call("GET", "/v1/validation/runs", granted)
            emit(OUT / "c5-05-corrupt-only-list.json", body)
            c.check("a corrupt-only root still lists the corrupt row",
                    body["total"] == 1 and body["count"] == 1
                    and body["runs"][0]["record_status"] == "corrupt",
                    (body["total"], body["count"], body["runs"][0]["record_status"]))
            c.check("a corrupt-only root is not reported as 'no_run_shaped_files'",
                    body.get("empty_reason") != "no_run_shaped_files",
                    body.get("empty_reason"))
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN_CORRUPT}", granted)
            emit(OUT / "c5-07-corrupt-detail.json", body)
            c.check("a corrupt record refuses detail with 409 validation_run_corrupt",
                    status == 409 and body.get("error", {}).get("code")
                    == "validation_run_corrupt", (status, body))
        empty_root = live.base / "c5-empty"
        empty_root.mkdir()
        with live.root(empty_root):
            status, _, body, _ = live.call("GET", "/v1/validation/runs", granted)
            c.check("a truly empty root reports no_run_shaped_files",
                    body.get("empty_reason") == "no_run_shaped_files",
                    body.get("empty_reason"))

        # -- Part 8: unreadable subtree --------------------------------------
        broken_root = live.base / "c5-broken"
        write_run(broken_root / "good" / RUN_DUP, RUN_DUP)
        locked = broken_root / "locked"
        locked.mkdir(parents=True)
        (locked / f"{RUN_CORRUPT}.json").write_text("{}")
        os.chmod(locked, 0o000)
        try:
            with live.root(broken_root):
                status, _, body, _ = live.call("GET", "/v1/validation/runs", granted)
                emit(OUT / "c5-08-unreadable-subtree.json", body)
                c.check("an unreadable subtree refuses the list (503)", status == 503,
                        status)
                c.check("the refusal code is validation_not_configured",
                        body.get("error", {}).get("code") == "validation_not_configured",
                        body.get("error", {}).get("code"))
                c.check("the refusal reason is index_incomplete",
                        body.get("error", {}).get("details", {}).get("reason")
                        == "index_incomplete", body.get("error", {}).get("details"))
                counts = body.get("error", {}).get("details", {}).get("counts", {})
                c.check("the refusal counts the unreadable entry",
                        counts.get("unreadable_entries", 0) >= 1, counts)
                samples = body.get("error", {}).get("details", {}).get("samples", [])
                c.check("the refusal names at most 20 samples", len(samples) <= 20,
                        len(samples))
                c.check("the refusal names the unreadable path",
                        any("locked" in str(s.get("path", "")) for s in samples),
                        samples)
                status, _, body, _ = live.call(
                    "GET", f"/v1/validation/runs/{RUN_DUP}", granted)
                c.check("a by-id read of a known sibling still works while a "
                        "sibling subtree is unreadable", status == 200, status)
                status, _, body, _ = live.call(
                    "GET", "/v1/validation/runs/run-20261008T999999Z", granted)
                emit(OUT / "c5-19-byid-unknown-under-unreadable.json", body)
                c.check("by-id of an id that cannot be ruled out refuses 503 "
                        "index_incomplete rather than asserting 404 (F5/ADR 5.0)",
                        status == 503 and body.get("error", {}).get("details", {})
                        .get("reason") == "index_incomplete", (status, body))
        finally:
            os.chmod(locked, 0o700)

        # -- Part 9: the real root, unchanged by all of the above ------------
        with live.root(REAL):
            status, _, real_body, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            c.check("real root still lists exactly nine",
                    real_body["total"] == 9, real_body["total"])
            c.check("real root corrupt_count is still 0", real_body["corrupt_count"] == 0,
                    real_body["corrupt_count"])
            c.check("real root duplicate_count is still 0",
                    real_body["duplicate_count"] == 0, real_body["duplicate_count"])

        emit(OUT / "c5-09-criterion5-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
