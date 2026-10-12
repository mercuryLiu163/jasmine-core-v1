#!/usr/bin/env python3
"""Criterion 6: API values agree with the source records and the file digests.

The validator recomputes every compared value from the bytes on disk and checks
the served field against it, then prints the field-by-field table.

Real nine: verdict, created_at, commit, schema_version, core_version, stage,
run_id, case_counts{pass,fail,blocked,total}, record_sha256 (sha256 of the raw
file), manifest_status. Fixture bundle: manifest_sha256 (sha256 of the raw
manifest), declared_sha256 and declared bytes, integrity, masked_sha256 and
original_sha256 (recomputed via canonical.redact_text + hashlib).

Run: PYTHONPATH=../../src:. python3 c6_values.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jasmine_core import canonical

from harness import (Live, Checker, artifact_entry, emit, sha256_bytes, sha256_file,
                     write_manifest, write_run)

REAL = ("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
        "/validation-runs")
OUT = Path(__file__).resolve().parent / "evidence"
RUN = "run-20261008T000001Z"
ASSIGN = b"2026-10-08 INFO db_password=hunter2hunter2 in the log line\n"

COMPARED: list[dict] = []


def cmp_field(c: Checker, scope: str, field: str, api_value, source_value) -> None:
    ok = api_value == source_value
    COMPARED.append({"scope": scope, "field": field, "api": api_value,
                     "source": source_value, "ok": ok})
    c.check(f"{scope}: {field} matches", ok, api_value, source_value)


def main() -> int:
    c = Checker("criterion 6")
    live = Live()
    try:
        granted = live.key(["validation:read"])

        # -- real nine: field-by-field against the raw files ----------------
        with live.root(REAL):
            status, _, listing, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            c.check("real list is 200", status == 200, status)
            rows = {row["run_id"]: row for row in listing["runs"]}
            c.check("real list has the nine rows", len(rows) == 9, len(rows))
            real_evidence = {}
            for path in sorted(Path(REAL).rglob("run-*.json")):
                raw = path.read_bytes()
                data = json.loads(raw.decode("utf-8"))
                run_id = data["run_id"]
                my_sha = hashlib.sha256(raw).hexdigest()
                counts = {"pass": 0, "fail": 0, "blocked": 0}
                for case in data["cases"]:
                    counts[case["verdict"].lower()] += 1
                counts["total"] = len(data["cases"])
                expected = {
                    "run_id": data["run_id"],
                    "stage": data["stage"],
                    "created_at": data["created_at"],
                    "commit": data.get("commit"),
                    "schema_version": data.get("schema_version"),
                    "core_version": data.get("core_version"),
                    "verdict": data["verdict"],
                    "case_counts": counts,
                    "record_sha256": my_sha,
                    "synthetic": data.get("synthetic", False),
                }
                row = rows[run_id]
                for field, value in expected.items():
                    cmp_field(c, run_id, f"list.{field}", row.get(field), value)
                cmp_field(c, run_id, "list.manifest_status",
                          row.get("manifest_status"), "absent")
                c.check(f"{run_id}: no manifest.json exists on disk",
                        not (path.parent / "manifest.json").exists())

                status, _, detail, _ = live.call(
                    "GET", f"/v1/validation/runs/{run_id}", granted)
                c.check(f"{run_id}: detail is 200", status == 200, status)
                for field in ("run_id", "stage", "created_at", "commit",
                              "schema_version", "core_version", "verdict"):
                    cmp_field(c, run_id, f"detail.{field}", detail.get(field),
                              data.get(field))
                for case in detail["cases"]:
                    src = next(x for x in data["cases"] if x["case_id"] == case["case_id"])
                    cmp_field(c, f"{run_id}/{case['case_id']}", "verdict",
                              case["verdict"], src["verdict"])
                    cmp_field(c, f"{run_id}/{case['case_id']}", "failure_class",
                              case.get("failure_class"), src.get("failure_class"))
                    cmp_field(c, f"{run_id}/{case['case_id']}", "failure_output",
                              case.get("failure_output"), src.get("failure_output"))
                real_evidence[run_id] = {
                    "source_sha256": my_sha, "source_path": str(path),
                    "detail_verdict": detail["verdict"],
                    "list_record_sha256": row.get("record_sha256"),
                }
            emit(OUT / "c6-01-real-field-comparison.json", real_evidence)

            # The real runs have no manifest: `manifest_sha256` is not applicable.
            some_run_id = next(iter(rows))
            status, _, manifest, _ = live.call(
                "GET", f"/v1/validation/runs/{some_run_id}/manifest", granted)
            emit(OUT / "c6-02-real-manifest-absent.json", manifest)
            c.check("a real run's manifest status is absent",
                    manifest.get("status") == "absent", manifest.get("status"))
            c.check("an absent manifest carries no manifest_sha256",
                    manifest.get("manifest_sha256") is None,
                    manifest.get("manifest_sha256"))

        # -- fixture bundle: manifest + artifact digests --------------------
        root = live.base / "c6-root"
        run_dir = root / "bundle" / RUN
        write_run(run_dir, RUN)
        files = [
            artifact_entry(run_dir, "art_good", "artifacts/good.txt", fixture="good.txt"),
            artifact_entry(run_dir, "art_assign", "artifacts/assign.log", content=ASSIGN),
        ]
        write_manifest(run_dir, RUN, files, mode="component",
                       frozen_at="2026-10-08T00:10:00Z")
        manifest_path = run_dir / "manifest.json"
        run_path = run_dir / f"{RUN}.json"
        my_manifest_sha = sha256_file(manifest_path)
        my_run_sha = sha256_file(run_path)
        good_bytes = (run_dir / "artifacts" / "good.txt").read_bytes()
        my_good_sha = sha256_bytes(good_bytes)
        my_assign_original_sha = sha256_bytes(ASSIGN)
        my_assign_masked = canonical.redact_text(ASSIGN.decode()).encode()
        my_assign_masked_sha = sha256_bytes(my_assign_masked)

        with live.root(root):
            status, _, manifest, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/manifest", granted)
            emit(OUT / "c6-03-fixture-manifest.json", manifest)
            c.check("fixture manifest is 200", status == 200, status)
            cmp_field(c, "manifest", "manifest_sha256", manifest.get("manifest_sha256"),
                      my_manifest_sha)
            c.check("manifest_authentication is unauthenticated",
                    manifest.get("manifest_authentication") == "unauthenticated",
                    manifest.get("manifest_authentication"))

            status, _, meta_good, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_good", granted)
            emit(OUT / "c6-04-good-metadata.json", meta_good)
            cmp_field(c, "art_good", "declared_sha256", meta_good.get("declared_sha256"),
                      my_good_sha)
            cmp_field(c, "art_good", "declared_bytes", meta_good.get("declared_bytes"),
                      len(good_bytes))
            cmp_field(c, "art_good", "bytes", meta_good.get("bytes"), len(good_bytes))
            cmp_field(c, "art_good", "digests.original",
                      meta_good.get("digests", {}).get("original"), my_good_sha)
            cmp_field(c, "art_good", "integrity", meta_good.get("integrity"), "match")

            status, _, content_good, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_good/content?form=original",
                granted)
            emit(OUT / "c6-05-good-original-content.json", content_good)
            cmp_field(c, "art_good content", "original_sha256",
                      content_good.get("original_sha256"), my_good_sha)
            cmp_field(c, "art_good content", "masked_sha256",
                      content_good.get("masked_sha256"), my_good_sha)
            cmp_field(c, "art_good content", "served_sha256",
                      content_good.get("served_sha256"), my_good_sha)
            cmp_field(c, "art_good content", "content",
                      content_good.get("content"), good_bytes.decode())
            cmp_field(c, "art_good content", "bytes",
                      content_good.get("bytes"), len(good_bytes))

            status, _, meta_assign, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_assign", granted)
            emit(OUT / "c6-06-assign-metadata.json", meta_assign)
            cmp_field(c, "art_assign", "declared_sha256",
                      meta_assign.get("declared_sha256"), my_assign_original_sha)
            cmp_field(c, "art_assign", "digests.original",
                      meta_assign.get("digests", {}).get("original"),
                      my_assign_original_sha)
            cmp_field(c, "art_assign", "digests.masked",
                      meta_assign.get("digests", {}).get("masked"),
                      my_assign_masked_sha)
            cmp_field(c, "art_assign", "integrity", meta_assign.get("integrity"), "match")

            status, _, content_assign, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_assign/content", granted)
            emit(OUT / "c6-07-assign-masked-content.json", content_assign)
            cmp_field(c, "art_assign content", "original_sha256",
                      content_assign.get("original_sha256"), my_assign_original_sha)
            cmp_field(c, "art_assign content", "masked_sha256",
                      content_assign.get("masked_sha256"), my_assign_masked_sha)
            cmp_field(c, "art_assign content", "served_sha256",
                      content_assign.get("served_sha256"), my_assign_masked_sha)
            cmp_field(c, "art_assign content", "content",
                      content_assign.get("content"), my_assign_masked.decode())
            cmp_field(c, "art_assign content", "mask_algorithm",
                      content_assign.get("mask_algorithm"), "canonical.redact_text")
            cmp_field(c, "art_assign content", "mask_version",
                      content_assign.get("mask_version"),
                      canonical.REDACT_TEXT_VERSION)

            # Fixture run record sha (sha256 of the raw file).
            status, _, row_list, _ = live.call(
                "GET", "/v1/validation/runs?limit=100", granted)
            row = next(r for r in row_list["runs"] if r["run_id"] == RUN)
            cmp_field(c, "fixture run", "list.record_sha256",
                      row.get("record_sha256"), my_run_sha)

        emit(OUT / "c6-08-field-comparison-table.json", COMPARED)
        lines = [f"{'ok' if x['ok'] else 'FAIL'}  {x['scope']}.{x['field']}: "
                 f"api={x['api']!r} source={x['source']!r}" for x in COMPARED]
        (OUT / "c6-09-field-comparison-table.txt").write_text("\n".join(lines) + "\n")
        print(f"compared {len(COMPARED)} fields; "
              f"{sum(1 for x in COMPARED if not x['ok'])} mismatches")
        print("fields compared: " + ", ".join(sorted({f"{x['scope']}.{x['field']}"
                                                      for x in COMPARED})))
        emit(OUT / "c6-10-criterion6-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
