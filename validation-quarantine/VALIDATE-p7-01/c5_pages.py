#!/usr/bin/env python3
"""Criterion 5: pagination has no duplicates or omissions; a corrupt record is
never disguised as an empty list; an unreadable subtree refuses rather than
returning a silently short page.

Part 1 pages the real root directly with a small limit and checks the union.
Part 2 builds a validator root with the nine records plus a corrupt record and a
duplicate stem, and checks the counts and the page walk. Part 3 makes a subtree
unreadable and expects 503 `index_incomplete`.

Run: PYTHONPATH=../../src:. python3 c5_pages.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from harness import Live, Checker, emit, write_run

REAL = ("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
        "/validation-runs")
OUT = Path(__file__).resolve().parent / "evidence"
RUN_DUP = "run-20261008T000001Z"
RUN_CORRUPT = "run-20261008T000004Z"


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
        corrupt_dir = fixture_root / "corrupt"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / f"{RUN_CORRUPT}.json").write_text("{not json at all")
        # duplicate stem in two distinct directories, different content
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

            # Page walk over the fixture root with limit=4.
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

            # A positive filter never matches the corrupt row but does match dupes.
            status, _, filtered, _ = live.call(
                "GET", "/v1/validation/runs?verdict=PASS", granted)
            emit(OUT / "c5-04-filtered-pass.json", filtered)
            c.check("filtered verdict=PASS excludes the corrupt row",
                    all(r["record_status"] != "corrupt" for r in filtered["runs"]))
            c.check("filtered verdict=PASS is 200 with rows (not an empty list)",
                    status == 200 and filtered["count"] > 0, filtered["count"])

            # Corrupt record alone: never an empty list.
            only_corrupt = live.base / "c5-corrupt-only"
            d = only_corrupt / "x"
            d.mkdir(parents=True)
            (d / f"{RUN_CORRUPT}.json").write_text("{not json")
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

            # Empty and all-filtered-out empty_reason.
            empty_root = live.base / "c5-empty"
            empty_root.mkdir()
            with live.root(empty_root):
                status, _, body, _ = live.call("GET", "/v1/validation/runs", granted)
                c.check("a truly empty root reports no_run_shaped_files",
                        body.get("empty_reason") == "no_run_shaped_files",
                        body.get("empty_reason"))
            with live.root(fixture_root):
                status, _, body, _ = live.call(
                    "GET", "/v1/validation/runs?stage=NOPE", granted)
                c.check("an all-filtered-out root reports all_filtered_out",
                        body.get("empty_reason") == "all_filtered_out",
                        body.get("empty_reason"))

            # Duplicate / corrupt detail refusals (single_user)
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN_DUP}", granted)
            emit(OUT / "c5-06-duplicate-detail.json", body)
            c.check("a duplicate stem refuses detail with 409", status == 409, status)
            c.check("duplicate detail code is validation_run_ambiguous",
                    body.get("error", {}).get("code") == "validation_run_ambiguous",
                    body.get("error", {}).get("code"))
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN_CORRUPT}", granted)
            emit(OUT / "c5-07-corrupt-detail.json", body)
            c.check("a corrupt record refuses detail with 409", status == 409, status)
            c.check("corrupt detail code is validation_run_corrupt",
                    body.get("error", {}).get("code") == "validation_run_corrupt",
                    body.get("error", {}).get("code"))

        # -- Part 3: unreadable subtree --------------------------------------
        broken_root = live.base / "c5-broken"
        good_dir = broken_root / "good" / RUN_DUP
        write_run(good_dir, RUN_DUP)
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
                # By-id reads still work when a sibling subtree is unreadable.
                status, _, body, _ = live.call(
                    "GET", f"/v1/validation/runs/{RUN_DUP}", granted)
                c.check("a by-id read still works while a sibling is unreadable",
                        status == 200, status)
        finally:
            os.chmod(locked, 0o700)

        emit(OUT / "c5-09-criterion5-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
