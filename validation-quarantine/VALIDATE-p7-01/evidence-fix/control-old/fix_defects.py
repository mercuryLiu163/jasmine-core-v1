#!/usr/bin/env python3
"""FIX-REVALIDATION — the four defect classes the operator found in `0ae8041`,
re-tested end to end against `f0aea88` while the filesystem changes under the
running server.

Parts (all real loopback HTTP against the real Application on a real migrated DB):

  A  a run's `manifest.json` is replaced, after the grant check has been
     exercised, with a manifest that names a different project and a different
     artifact; the served payload and the permission decision must always come
     from ONE manifest read (sequential swap AND a rapid swap race in `acl`).
  B  a FIFO at a record path, at a `manifest.json` path and at an artifact path
     must each be answered, not block the request; plus a rapid regular<->FIFO
     swap race at a record path.
  C  a record and a manifest that grow past their caps must be refused, never
     read whole; plus the in-loop `_read_capped` primitive that makes the
     grow-after-fstat window safe, plus a targeted grow-during-read race.
  D  the run root replaced by a symlink to an outside directory holding a valid
     record must be refused (list, detail, manifest, artifact) and serve nothing
     from outside; restoring the root must restore reads.
  E  an artifact whose raw bytes are under `MAX_ARTIFACT_BYTES` but whose masked
     form is over it must be refused `413`, never returned.
  F  legitimate reads at depth survive: a record one or more directories below
     the root and an artifact at `artifacts/sub/name.txt`.

Run: PYTHONPATH=../../../src:. python3 fix_defects.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

import jasmine_core.validation.reads as reads
from jasmine_core import canonical

from fix_harness import (ACTOR, BUNDLE_SCHEMA, Live, Checker, emit, sha256_bytes,
                         write_run)

OUT = Path(__file__).resolve().parent

MAX_ARTIFACT_BYTES = reads.MAX_ARTIFACT_BYTES
MAX_MANIFEST_BYTES = reads.MAX_MANIFEST_BYTES
MAX_RECORD_BYTES = reads.MAX_RECORD_BYTES

CLIENT_TIMEOUT = 5.0    # any request slower than this fails the probe
HUNG = 3.0              # a response slower than this is treated as a hang
RACE_REQUESTS = 60      # content/manifest requests in the manifest-swap race
FIFO_RACE_REQUESTS = 15  # list requests in the regular<->FIFO record race
GROW_RACE_ITERATIONS = 12


def timed_call(live: Live, method: str, path: str, token: str | None,
               timeout: float = CLIENT_TIMEOUT) -> dict:
    """A real HTTP call; a client-side timeout is returned as the finding."""
    started = time.monotonic()
    try:
        status, headers, body, raw = live.call(method, path, token, timeout=timeout)
        return {"status": status, "headers": headers, "body": body, "raw": raw,
                "seconds": round(time.monotonic() - started, 4), "error": None}
    except Exception as exc:  # noqa: BLE001 - a hang is a finding, not a crash
        return {"status": None, "headers": {}, "body": None, "raw": b"",
                "seconds": round(time.monotonic() - started, 4),
                "error": f"{type(exc).__name__}: {exc}"}


def no_hang(c: Checker, tag: str, response: dict) -> bool:
    return c.check(
        f"{tag}: answered inside {HUNG}s with an HTTP status (no hang)",
        response["error"] is None and response["status"] is not None
        and response["seconds"] < HUNG,
        (response["error"], response["status"], response["seconds"]))


def atomic(path: Path, data: bytes, tmpdir: Path) -> None:
    """Replace `path` atomically with `data` (same filesystem as `tmpdir`)."""
    tmp = tmpdir / (path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def manifest_bytes(run_id: str, projects: list[str], files: list[dict],
                   mode: str = "component") -> bytes:
    return json.dumps({
        "schema": BUNDLE_SCHEMA,
        "run_id": run_id,
        "mode": mode,
        "projects": list(projects),
        "sources": {"recorder": {"path": "scripts/p0-acceptance.py",
                                 "sha256": "a" * 64}},
        "files": files,
    }).encode("utf-8")


def decl(artifact_id: str, rel_path: str, content: bytes, *,
         media_type: str = "text/plain", credential_shaped: bool = False) -> dict:
    return {"artifact_id": artifact_id, "path": rel_path, "bytes": len(content),
            "sha256": sha256_bytes(content), "media_type": media_type,
            "credential_shaped": credential_shaped}


def error_code(body) -> str | None:
    if not isinstance(body, dict):
        return None
    return (body.get("error") or {}).get("code")


def error_details(body) -> dict:
    if not isinstance(body, dict):
        return {}
    return (body.get("error") or {}).get("details") or {}


def oversized_valid_record(run_id: str, target_bytes: int) -> bytes:
    """A valid JSON run record padded to exactly `target_bytes` (trailing pad)."""
    base = {"run_id": run_id, "stage": "P7", "created_at": "2026-10-08T00:00:00.000000Z",
            "commit": "0" * 40, "schema_version": 11, "core_version": "0.1.0",
            "verdict": "PASS", "cases": [], "pad": ""}
    head = json.dumps(base)
    # `{"pad":""}` -> put the pad inside the object so the JSON stays valid.
    prefix = head[:head.rindex('""') + 1]
    suffix = head[head.rindex('""') + 1:]
    pad_len = target_bytes - len(prefix) - len(suffix)
    if pad_len < 0:
        raise ValueError("target too small")
    return (prefix + "x" * pad_len + suffix).encode("utf-8")


def oversized_valid_manifest(run_id: str, target_bytes: int) -> bytes:
    import json as _json
    base = {"schema": BUNDLE_SCHEMA, "run_id": run_id, "mode": "component",
            "projects": [], "sources": {}, "files": [], "pad": ""}
    head = _json.dumps(base)
    prefix = head[:head.rindex('""') + 1]
    suffix = head[head.rindex('""') + 1:]
    pad_len = target_bytes - len(prefix) - len(suffix)
    if pad_len < 0:
        raise ValueError("target too small")
    return (prefix + " " * pad_len + suffix).encode("utf-8")


# -- Part A: one manifest read per request -----------------------------------


def part_a_manifest_swap(live: Live, c: Checker) -> None:
    print("\n########## PART A — manifest swapped after the grant check (acl)", flush=True)
    granted_project = live.create_project("fixA-granted")
    ungranted_project = live.create_project("fixA-ungranted")
    live.grant(granted_project, ACTOR)
    live.set_mode("acl")
    acl = live.key(["validation:read"], label="fixA-acl")

    run_id = "run-20261008T000501Z"
    root = live.base / "A-swap"
    run_dir = root / "swap"
    art_dir = run_dir / "artifacts"
    art_dir.mkdir(parents=True)
    write_run(run_dir, run_id)
    a_text = b"AAA-CONTENT-SERVED-FROM-MANIFEST-A"
    b_text = b"BBB-CONTENT-SERVED-FROM-MANIFEST-B"
    (art_dir / "a.txt").write_bytes(a_text)
    (art_dir / "b.txt").write_bytes(b_text)
    entry_a = decl("art_good", "artifacts/a.txt", a_text)
    entry_b = decl("art_good", "artifacts/b.txt", b_text)
    # A is attributed to the granted project; B to a project the actor has no
    # grant on. The two manifests name different artifacts with different bytes.
    man_a = manifest_bytes(run_id, [granted_project], [entry_a])
    man_b = manifest_bytes(run_id, [ungranted_project], [entry_b])
    sha_a, sha_b = sha256_bytes(man_a), sha256_bytes(man_b)
    tmpdir = live.base / "A-tmp"
    tmpdir.mkdir()
    target = run_dir / "manifest.json"
    atomic(target, man_a, tmpdir)

    content_path = f"/v1/validation/runs/{run_id}/artifacts/art_good/content"
    manifest_path = f"/v1/validation/runs/{run_id}/manifest"
    a_text_s = a_text.decode()
    a_served_sha = hashlib.sha256(a_text).hexdigest()

    evidence: dict = {"granted_project": granted_project,
                      "ungranted_project": ungranted_project,
                      "manifest_a_sha256": sha_a, "manifest_b_sha256": sha_b,
                      "a_text": a_text_s, "b_text": b_text.decode()}
    with live.root(root):
        # 1. exercise the grant check with manifest A on disk
        r1 = timed_call(live, "GET", content_path, acl)
        no_hang(c, "A granted content (manifest A)", r1)
        c.check("A (manifest A): granted content is 200 and serves A's bytes",
                r1["status"] == 200 and (r1["body"] or {}).get("content") == a_text_s,
                (r1["status"], r1["body"]))
        c.check("A (manifest A): served digest is A's, header agrees",
                r1["headers"].get("X-Validation-Sha256") == a_served_sha,
                r1["headers"].get("X-Validation-Sha256"))
        r2 = timed_call(live, "GET", manifest_path, acl)
        no_hang(c, "A granted manifest (manifest A)", r2)
        c.check("A (manifest A): manifest route serves A (sha + path agree)",
                r2["status"] == 200
                and (r2["body"] or {}).get("manifest_sha256") == sha_a
                and (r2["body"] or {}).get("files")[0]["path"] == "artifacts/a.txt",
                (r2["status"], (r2["body"] or {}).get("manifest_sha256"),
                 (r2["body"] or {}).get("files")))
        evidence["grant_check_exercised"] = {
            "content": {"status": r1["status"], "seconds": r1["seconds"],
                        "content": (r1["body"] or {}).get("content")},
            "manifest": {"status": r2["status"], "seconds": r2["seconds"],
                         "manifest_sha256": (r2["body"] or {}).get("manifest_sha256")}}

        # 2. swap to the ungranted manifest B: permission must be re-decided
        atomic(target, man_b, tmpdir)
        r3 = timed_call(live, "GET", content_path, acl)
        no_hang(c, "A content after swap to B", r3)
        c.check("A after swap to B: content is refused 404 validation_run_not_found",
                r3["status"] == 404
                and error_code(r3["body"]) == "validation_run_not_found",
                (r3["status"], error_code(r3["body"])))
        c.check("A after swap to B: the refusal serves nothing from manifest B",
                b_text not in r3["raw"] and b"artifacts/b.txt" not in r3["raw"],
                r3["raw"][:200])
        r4 = timed_call(live, "GET", manifest_path, acl)
        c.check("A after swap to B: manifest route is refused 404 too",
                r4["status"] == 404, (r4["status"], error_code(r4["body"])))
        evidence["after_swap_to_b"] = {
            "content": {"status": r3["status"], "code": error_code(r3["body"]),
                        "seconds": r3["seconds"]},
            "manifest": {"status": r4["status"], "code": error_code(r4["body"])}}

        # 3. swap back to A: reads work again
        atomic(target, man_a, tmpdir)
        r5 = timed_call(live, "GET", content_path, acl)
        c.check("A after swapping back to A: granted content is 200 with A's bytes",
                r5["status"] == 200 and (r5["body"] or {}).get("content") == a_text_s,
                (r5["status"], (r5["body"] or {}).get("content")))
        evidence["after_swap_back_to_a"] = {"status": r5["status"],
                                            "seconds": r5["seconds"]}

        # 4. rapid swap race: the payload must never come from B while the
        #    permission decision came from A (and vice versa).
        stop = threading.Event()
        flip = {"n": 0}

        def swapper() -> None:
            while not stop.is_set():
                payload = man_b if flip["n"] % 2 else man_a
                atomic(target, payload, tmpdir)
                flip["n"] += 1

        thread = threading.Thread(target=swapper, daemon=True)
        thread.start()
        mixed: list = []
        content_200 = content_404 = manifest_200 = manifest_404 = 0
        race_timings: list = []
        try:
            for _ in range(RACE_REQUESTS):
                rc = timed_call(live, "GET", content_path, acl)
                race_timings.append(rc["seconds"])
                if rc["error"] is not None or rc["seconds"] >= HUNG:
                    mixed.append(("content-hang", rc["error"], rc["seconds"]))
                elif rc["status"] == 200:
                    content_200 += 1
                    body = rc["body"] or {}
                    if (body.get("content") != a_text_s
                            or rc["headers"].get("X-Validation-Sha256") != a_served_sha):
                        mixed.append(("content-200-mixed", body.get("content"),
                                      rc["headers"].get("X-Validation-Sha256")))
                elif rc["status"] == 404:
                    content_404 += 1
                    if b_text in rc["raw"] or b"artifacts/b.txt" in rc["raw"]:
                        mixed.append(("content-404-with-B", rc["raw"][:200]))
                else:
                    mixed.append(("content-unexpected", rc["status"], error_code(rc["body"])))

                rm = timed_call(live, "GET", manifest_path, acl)
                race_timings.append(rm["seconds"])
                if rm["error"] is not None or rm["seconds"] >= HUNG:
                    mixed.append(("manifest-hang", rm["error"], rm["seconds"]))
                elif rm["status"] == 200:
                    manifest_200 += 1
                    body = rm["body"] or {}
                    files = body.get("files") or []
                    if (body.get("manifest_sha256") != sha_a or not files
                            or files[0].get("path") != "artifacts/a.txt"):
                        mixed.append(("manifest-200-mixed", body.get("manifest_sha256"),
                                      files))
                elif rm["status"] == 404:
                    manifest_404 += 1
                    if b"artifacts/b.txt" in rm["raw"]:
                        mixed.append(("manifest-404-with-B", rm["raw"][:200]))
                else:
                    mixed.append(("manifest-unexpected", rm["status"],
                                  error_code(rm["body"])))
        finally:
            stop.set()
            thread.join(timeout=5)
            atomic(target, man_a, tmpdir)
        c.check("A rapid swap race: no response ever mixes A's permission with B's "
                "payload (or B's with A's)", not mixed, mixed[:5])
        c.check("A rapid swap race: both the granted (200) and refused (404) "
                "branches were exercised",
                content_200 > 0 and content_404 > 0 and manifest_200 > 0
                and manifest_404 > 0,
                (content_200, content_404, manifest_200, manifest_404))
        c.check("A rapid swap race: every request answered well inside the timeout",
                all(s < HUNG for s in race_timings),
                (round(max(race_timings), 4) if race_timings else None,
                 round(sum(race_timings), 3)))
        r6 = timed_call(live, "GET", content_path, acl)
        c.check("A after the race: manifest A restored and content is 200 with A",
                r6["status"] == 200 and (r6["body"] or {}).get("content") == a_text_s,
                (r6["status"], (r6["body"] or {}).get("content")))
        evidence["race"] = {"content_200": content_200, "content_404": content_404,
                            "manifest_200": manifest_200, "manifest_404": manifest_404,
                            "swaps": flip["n"], "mixed": mixed[:5],
                            "max_seconds": round(max(race_timings), 4) if race_timings else None,
                            "requests": len(race_timings)}
    emit(OUT / "fix-defects-01-manifest-swap.json", evidence)
    live.set_mode("single_user")


# -- Part B: FIFO at a record / manifest / artifact path ----------------------


def part_b_fifo(live: Live, c: Checker, granted: str) -> None:
    print("\n########## PART B — FIFO at a record path, manifest.json, artifact", flush=True)
    root = live.base / "B-fifo"
    ok_id = "run-20261008T000401Z"
    fifo_id = "run-20261008T000402Z"
    man_id = "run-20261008T000403Z"
    art_id = "run-20261008T000404Z"
    write_run(root / "ok", ok_id)

    fifo_rec = root / "fifo-rec" / f"{fifo_id}.json"
    fifo_rec.parent.mkdir(parents=True)
    os.mkfifo(fifo_rec)

    man_dir = root / "man"
    write_run(man_dir, man_id)
    man_fifo = man_dir / "manifest.json"
    os.mkfifo(man_fifo)

    art_dir = root / "art"
    write_run(art_dir, art_id)
    (art_dir / "artifacts").mkdir()
    art_fifo = art_dir / "artifacts" / "pipe.txt"
    os.mkfifo(art_fifo)
    art_decl = decl("art_fifo", "artifacts/pipe.txt", b"")  # declared arbitrarily
    art_decl["sha256"] = "0" * 64
    art_decl["bytes"] = 0
    (art_dir / "manifest.json").write_bytes(
        manifest_bytes(art_id, [], [art_decl]))

    c.check("B: the record-path entry is a real FIFO on disk",
            os.stat(fifo_rec).st_mode & 0o170000 == 0o010000,
            oct(os.stat(fifo_rec).st_mode))
    c.check("B: the manifest.json entry is a real FIFO on disk",
            os.stat(man_fifo).st_mode & 0o170000 == 0o010000,
            oct(os.stat(man_fifo).st_mode))
    c.check("B: the artifact entry is a real FIFO on disk",
            os.stat(art_fifo).st_mode & 0o170000 == 0o010000,
            oct(os.stat(art_fifo).st_mode))

    evidence: dict = {"fifo_record": str(fifo_rec), "fifo_manifest": str(man_fifo),
                      "fifo_artifact": str(art_fifo),
                      "client_timeout_seconds": CLIENT_TIMEOUT}
    with live.root(root):
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        no_hang(c, "B list over a root with a FIFO record", r)
        body = r["body"] or {}
        ids = [row.get("run_id") for row in body.get("runs", [])]
        evidence["list"] = {"status": r["status"], "seconds": r["seconds"],
                            "total": body.get("total"), "run_ids": ids,
                            "ignored_entries": body.get("ignored_entries"),
                            "ignored_samples": body.get("ignored_samples")}
        c.check("B list: the FIFO record is not served as a run row",
                fifo_id not in ids, ids)
        c.check("B list: the three regular records are still served",
                body.get("total") == 3, body.get("total"))
        nrf = [s for s in body.get("ignored_samples", [])
               if s.get("reason") == "not_regular_file"]
        c.check("B list: the FIFO record is counted not_regular_file",
                any(str(s.get("relative_path", "")).endswith(f"{fifo_id}.json")
                    for s in nrf), body.get("ignored_samples"))

        r = timed_call(live, "GET", f"/v1/validation/runs/{fifo_id}", granted)
        no_hang(c, "B detail of the FIFO record stem", r)
        c.check("B detail of the FIFO record stem: 404 validation_run_not_found",
                r["status"] == 404 and error_code(r["body"]) == "validation_run_not_found",
                (r["status"], error_code(r["body"])))
        evidence["fifo_record_detail"] = {"status": r["status"], "seconds": r["seconds"],
                                          "code": error_code(r["body"])}

        r = timed_call(live, "GET", f"/v1/validation/runs/{man_id}/manifest", granted)
        no_hang(c, "B manifest route with a FIFO manifest.json", r)
        rule = error_details(r["body"]).get("rule") or ""
        c.check("B manifest route with a FIFO manifest.json: 409 manifest_corrupt "
                "naming not_regular_file",
                r["status"] == 409
                and error_code(r["body"]) == "validation_manifest_corrupt"
                and "not_regular_file" in rule,
                (r["status"], error_code(r["body"]), rule))
        evidence["fifo_manifest_route"] = {"status": r["status"], "seconds": r["seconds"],
                                           "code": error_code(r["body"]), "rule": rule}
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        row = next((x for x in (r["body"] or {}).get("runs", [])
                    if x.get("run_id") == man_id), None)
        c.check("B list: the FIFO manifest is reported manifest_status corrupt",
                row is not None and row.get("manifest_status") == "corrupt",
                row and row.get("manifest_status"))

        for tag, path in (("metadata", f"/v1/validation/runs/{art_id}/artifacts/art_fifo"),
                          ("content", f"/v1/validation/runs/{art_id}/artifacts/art_fifo/content")):
            r = timed_call(live, "GET", path, granted)
            no_hang(c, f"B artifact {tag} with a FIFO artifact path", r)
            c.check(f"B artifact {tag} with a FIFO artifact path: refused 503 "
                    "artifact_unreadable (never blocked)",
                    r["status"] == 503
                    and error_code(r["body"]) == "validation_not_configured"
                    and error_details(r["body"]).get("reason") == "artifact_unreadable",
                    (r["status"], error_code(r["body"]), error_details(r["body"])))
            evidence[f"fifo_artifact_{tag}"] = {
                "status": r["status"], "seconds": r["seconds"],
                "code": error_code(r["body"]), "details": error_details(r["body"])}
        r = timed_call(live, "GET", f"/v1/validation/runs/{art_id}/manifest", granted)
        no_hang(c, "B manifest route listing a FIFO artifact", r)
        files = (r["body"] or {}).get("files") or []
        c.check("B manifest route: the FIFO artifact is listed integrity unreadable",
                r["status"] == 200 and files
                and files[0].get("integrity") == "unreadable",
                (r["status"], files))

    # rapid regular <-> FIFO swap race at a record path
    race_root = live.base / "B-race"
    race_root.mkdir()
    race_id = "run-20261008T000405Z"
    race_target = race_root / f"{race_id}.json"
    race_tmp = live.base / "B-race-tmp"
    race_tmp.mkdir()
    record_bytes = json.dumps({
        "run_id": race_id, "stage": "P7",
        "created_at": "2026-10-08T00:00:05.000000Z", "commit": "0" * 40,
        "schema_version": 11, "core_version": "0.1.0", "verdict": "PASS",
        "cases": []}).encode()
    stop = threading.Event()
    swaps = {"n": 0}

    def fifo_swapper() -> None:
        tmp_fifo = race_tmp / "f"
        tmp_reg = race_tmp / "r"
        while not stop.is_set():
            try:
                os.unlink(tmp_fifo)
            except FileNotFoundError:
                pass
            os.mkfifo(tmp_fifo)
            os.replace(tmp_fifo, race_target)          # target -> FIFO
            swaps["n"] += 1
            tmp_reg.write_bytes(record_bytes)
            os.replace(tmp_reg, race_target)           # target -> regular

    thread = threading.Thread(target=fifo_swapper, daemon=True)
    thread.start()
    outcomes: list = []
    race_seconds: list = []
    race_timed_out = False
    try:
        with live.root(race_root):
            for _ in range(FIFO_RACE_REQUESTS):
                r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
                race_seconds.append(r["seconds"])
                if r["error"] is not None or r["status"] is None:
                    race_timed_out = True
                    outcomes.append({"status": None, "error": r["error"],
                                     "seconds": r["seconds"]})
                    break
                row = next((x for x in (r["body"] or {}).get("runs", [])
                            if x.get("run_id") == race_id), None)
                outcomes.append({"status": r["status"], "seconds": r["seconds"],
                                 "row_status": row and row.get("record_status"),
                                 "row_reason": row and row.get("corrupt_reason")})
    finally:
        stop.set()
        thread.join(timeout=5)
        race_target.write_bytes(record_bytes)
    c.check("B regular<->FIFO record swap race: no request blocked (all answered)",
            not race_timed_out and all(o["status"] == 200 for o in outcomes),
            outcomes[:3])
    c.check("B regular<->FIFO record swap race: every response was inside the "
            "hang threshold",
            all(s < HUNG for s in race_seconds),
            (round(max(race_seconds), 4) if race_seconds else None))
    reasons = [o.get("row_reason") for o in outcomes]
    c.check("B swap race: a swapped-in FIFO is refused as not a regular file, "
            "never read",
            all(reason is None or "regular file" in reason for reason in reasons),
            reasons)
    evidence["record_fifo_swap_race"] = {
        "requests": len(outcomes), "swaps": swaps["n"], "timed_out": race_timed_out,
        "outcomes": outcomes, "max_seconds": round(max(race_seconds), 4)
        if race_seconds else None}
    emit(OUT / "fix-defects-02-fifo.json", evidence)


# -- Part C: bounded reads and post-check growth ------------------------------


def part_c_caps(live: Live, c: Checker, granted: str) -> None:
    print("\n########## PART C — record/manifest growth past the caps", flush=True)
    evidence: dict = {"max_record_bytes": MAX_RECORD_BYTES,
                      "max_manifest_bytes": MAX_MANIFEST_BYTES}
    root = live.base / "C-grow"
    rec_id = "run-20261008T000601Z"
    man_id = "run-20261008T000602Z"
    rec_dir = root / "rec"
    write_run(rec_dir, rec_id)
    rec_file = rec_dir / f"{rec_id}.json"
    man_dir = root / "man"
    write_run(man_dir, man_id)
    man_file = man_dir / "manifest.json"
    man_file.write_bytes(manifest_bytes(man_id, [], []))

    with live.root(root):
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        row = next((x for x in (r["body"] or {}).get("runs", [])
                    if x.get("run_id") == rec_id), None)
        c.check("C before growth: the record is a normal ok row",
                r["status"] == 200 and row is not None
                and row.get("record_status") == "ok", row)

        # grow the record past MAX_RECORD_BYTES, keeping it valid JSON so an
        # implementation that reads it whole would still parse and serve it
        rec_file.write_bytes(oversized_valid_record(rec_id, MAX_RECORD_BYTES + 4096))
        c.check("C the record on disk is now over MAX_RECORD_BYTES",
                rec_file.stat().st_size > MAX_RECORD_BYTES, rec_file.stat().st_size)
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        row = next((x for x in (r["body"] or {}).get("runs", [])
                    if x.get("run_id") == rec_id), None)
        evidence["grown_record_list_row"] = row
        c.check("C grown record: listed corrupt, not served",
                row is not None and row.get("record_status") == "corrupt", row)
        c.check("C grown record: reports no digest because it was never read whole",
                row is not None and row.get("record_sha256") == "",
                row and row.get("record_sha256"))
        c.check("C grown record: the reason names the size bound",
                row is not None and "exceeds" in (row.get("corrupt_reason") or ""),
                row and row.get("corrupt_reason"))
        r = timed_call(live, "GET", f"/v1/validation/runs/{rec_id}", granted)
        no_hang(c, "C grown record detail", r)
        c.check("C grown record: detail is 409 validation_run_corrupt",
                r["status"] == 409 and error_code(r["body"]) == "validation_run_corrupt",
                (r["status"], error_code(r["body"])))
        evidence["grown_record_detail"] = {"status": r["status"], "seconds": r["seconds"],
                                           "code": error_code(r["body"])}

        # grow the manifest past MAX_MANIFEST_BYTES
        r = timed_call(live, "GET", f"/v1/validation/runs/{man_id}/manifest", granted)
        c.check("C before growth: the manifest is frozen and served",
                r["status"] == 200 and (r["body"] or {}).get("status") == "frozen",
                (r["status"], (r["body"] or {}).get("status")))
        man_file.write_bytes(oversized_valid_manifest(man_id, MAX_MANIFEST_BYTES + 4096))
        c.check("C the manifest on disk is now over MAX_MANIFEST_BYTES",
                man_file.stat().st_size > MAX_MANIFEST_BYTES, man_file.stat().st_size)
        r = timed_call(live, "GET", f"/v1/validation/runs/{man_id}/manifest", granted)
        no_hang(c, "C grown manifest route", r)
        rule = error_details(r["body"]).get("rule") or ""
        c.check("C grown manifest: 409 validation_manifest_corrupt naming the bound",
                r["status"] == 409
                and error_code(r["body"]) == "validation_manifest_corrupt"
                and "exceeds" in rule, (r["status"], error_code(r["body"]), rule))
        c.check("C grown manifest: no digest is reported (never read whole)",
                "manifest_sha256" not in error_details(r["body"]),
                error_details(r["body"]))
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        row = next((x for x in (r["body"] or {}).get("runs", [])
                    if x.get("run_id") == man_id), None)
        c.check("C grown manifest: the list row is manifest_status corrupt",
                row is not None and row.get("manifest_status") == "corrupt",
                row and row.get("manifest_status"))
        evidence["grown_manifest"] = {"status": r["status"], "seconds": r["seconds"],
                                      "rule": rule, "code": error_code(r["body"])}

        # restore both and show reads work again
        write_run(rec_dir, rec_id)
        man_file.write_bytes(manifest_bytes(man_id, [], []))
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        row = next((x for x in (r["body"] or {}).get("runs", [])
                    if x.get("run_id") == rec_id), None)
        c.check("C after restoring: the record is a normal ok row again",
                row is not None and row.get("record_status") == "ok", row)
        r = timed_call(live, "GET", f"/v1/validation/runs/{man_id}/manifest", granted)
        c.check("C after restoring: the manifest is frozen again",
                r["status"] == 200 and (r["body"] or {}).get("status") == "frozen",
                (r["status"], (r["body"] or {}).get("status")))

    # C2: the in-loop cap primitive that makes grow-after-fstat safe
    cap = 64
    prim = live.base / "prim.bin"
    prim.write_bytes(b"x" * cap)
    fd = os.open(prim, os.O_RDONLY)
    with open(prim, "ab") as handle:
        handle.write(b"y")                    # grows after the open/fstat
    buffer, error = reads._read_capped(fd, cap)
    c.check("C in-loop primitive: a descriptor grown past the cap is refused "
            "too_large, not read whole", buffer is None and error == "too_large",
            (buffer, error))
    prim2 = live.base / "prim2.bin"
    prim2.write_bytes(b"x" * cap)
    fd2 = os.open(prim2, os.O_RDONLY)
    buffer2, error2 = reads._read_capped(fd2, cap)
    c.check("C in-loop primitive: a file exactly at the cap is still read normally",
            buffer2 == b"x" * cap and error2 is None, (len(buffer2 or b""), error2))
    evidence["in_loop_cap"] = {"grown_descriptor": {"error": error},
                               "at_cap": {"error": error2, "bytes": len(buffer2 or b"")}}

    # C3: targeted grow-during-read race over HTTP
    race_root = live.base / "C-race"
    race_root.mkdir()
    race_id = "run-20261008T000603Z"
    target = race_root / f"{race_id}.json"
    tmpdir = live.base / "C-race-tmp"
    tmpdir.mkdir()
    cap_bytes = oversized_valid_record(race_id, MAX_RECORD_BYTES)
    sha_cap = sha256_bytes(cap_bytes)
    violations: list = []
    distributions: list = []
    race_seconds: list = []
    for index in range(GROW_RACE_ITERATIONS):
        atomic(target, cap_bytes, tmpdir)
        delay = (index % 5) * 0.0004

        def appender(wait: float = delay) -> None:
            time.sleep(wait)
            with open(target, "ab") as handle:
                handle.write(b" ")

        thread = threading.Thread(target=appender, daemon=True)
        thread.start()
        try:
            with live.root(race_root):
                r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        finally:
            thread.join(timeout=2)
        race_seconds.append(r["seconds"])
        row = next((x for x in (r["body"] or {}).get("runs", [])
                    if x.get("run_id") == race_id), None)
        if row is None:
            distributions.append("absent")
        elif row.get("record_status") == "ok":
            distributions.append("ok")
            if row.get("record_sha256") != sha_cap:
                violations.append({"index": index, "outcome": "ok-with-grown-digest",
                                   "record_sha256": row.get("record_sha256")})
        else:
            distributions.append(f"corrupt:{row.get('corrupt_reason')}")
            if row.get("record_sha256") != "" or "exceeds" not in (
                    row.get("corrupt_reason") or ""):
                violations.append({"index": index, "outcome": "corrupt-not-cap",
                                   "sha": row.get("record_sha256"),
                                   "reason": row.get("corrupt_reason")})
    c.check("C grow-during-read race: no request ever served a grown record "
            "(an ok row always carries the pre-growth digest; an over-cap row "
            "carries no digest and names the bound)",
            not violations, violations[:3])
    c.check("C grow-during-read race: every request answered inside the hang "
            "threshold", all(s < HUNG for s in race_seconds),
            round(max(race_seconds), 4) if race_seconds else None)
    evidence["grow_during_read_race"] = {
        "iterations": GROW_RACE_ITERATIONS, "distributions": distributions,
        "violations": violations, "max_seconds": round(max(race_seconds), 4)
        if race_seconds else None}
    emit(OUT / "fix-defects-03-cap-growth.json", evidence)


# -- Part D: root replaced by a symlink to an outside directory ---------------


def part_d_root_symlink(live: Live, c: Checker, granted: str) -> None:
    print("\n########## PART D — root replaced by a symlink to an outside directory",
          flush=True)
    root = live.base / "D-root"
    inside_id = "run-20261008T000701Z"
    outside_id = "run-20261008T000702Z"
    inside_dir = root / "inner"
    write_run(inside_dir, inside_id)
    inside_text = b"INSIDE-ROOT-CONTENT"
    (inside_dir / "artifacts").mkdir()
    (inside_dir / "artifacts" / "note.txt").write_bytes(inside_text)
    (inside_dir / "manifest.json").write_bytes(
        manifest_bytes(inside_id, [], [decl("art_note", "artifacts/note.txt", inside_text)]))

    outside = live.base / "D-outside"
    outside_dir = outside / "o"
    write_run(outside_dir, outside_id)
    outside_text = b"OUTSIDE-SECRET-CONTENT"
    (outside_dir / "artifacts").mkdir()
    (outside_dir / "artifacts" / "loot.txt").write_bytes(outside_text)
    (outside_dir / "manifest.json").write_bytes(
        manifest_bytes(outside_id, [], [decl("art_loot", "artifacts/loot.txt", outside_text)]))

    evidence: dict = {"inside_run": inside_id, "outside_run": outside_id,
                      "outside_text": outside_text.decode()}
    with live.root(root):
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        ids = [x.get("run_id") for x in (r["body"] or {}).get("runs", [])]
        c.check("D before swap: the inside run is served from the real root",
                r["status"] == 200 and inside_id in ids and outside_id not in ids,
                (r["status"], ids))
        r = timed_call(live, "GET", f"/v1/validation/runs/{inside_id}", granted)
        c.check("D before swap: inside detail is 200 (grant check exercised)",
                r["status"] == 200, r["status"])
        evidence["before_swap"] = {"list_status": 200, "run_ids": ids}

    backup = live.base / "D-root-backup"
    os.rename(root, backup)
    os.symlink(str(outside), str(root))
    # NB: compare realpaths, because on macOS `/var` is itself a symlink to
    # `/private/var`; the point is that the root now resolves to the outside dir.
    c.check("D the configured root is now a symlink to the outside directory",
            os.path.islink(root)
            and os.path.realpath(root) == os.path.realpath(outside),
            (os.path.islink(root), os.path.realpath(root),
             os.path.realpath(outside)))
    refusals: dict = {}
    with live.root(root):
        for tag, path in (
                ("list", "/v1/validation/runs?limit=100"),
                ("inside detail", f"/v1/validation/runs/{inside_id}"),
                ("outside detail", f"/v1/validation/runs/{outside_id}"),
                ("outside manifest", f"/v1/validation/runs/{outside_id}/manifest"),
                ("outside artifact", f"/v1/validation/runs/{outside_id}/artifacts/art_loot/content")):
            r = timed_call(live, "GET", path, granted)
            no_hang(c, f"D symlinked root, {tag}", r)
            served_outside = (outside_text in r["raw"]
                              or outside_id.encode() in r["raw"]
                              or b"OUTSIDE-SECRET" in r["raw"])
            c.check(f"D symlinked root, {tag}: refused 503 symlinked_root and "
                    "serves nothing from outside",
                    r["status"] == 503
                    and error_code(r["body"]) == "validation_not_configured"
                    and error_details(r["body"]).get("reason") == "symlinked_root"
                    and not served_outside,
                    (r["status"], error_code(r["body"]),
                     error_details(r["body"]).get("reason"), served_outside))
            refusals[tag] = {"status": r["status"], "seconds": r["seconds"],
                             "code": error_code(r["body"]),
                             "reason": error_details(r["body"]).get("reason"),
                             "served_outside": served_outside}
    evidence["while_symlinked"] = refusals

    os.remove(root)
    os.rename(backup, root)
    c.check("D the root is a real directory again", not os.path.islink(root)
            and root.is_dir())
    with live.root(root):
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        ids = [x.get("run_id") for x in (r["body"] or {}).get("runs", [])]
        c.check("D after restore: the list serves the inside run again and nothing "
                "from outside",
                r["status"] == 200 and inside_id in ids and outside_id not in ids
                and outside_text not in r["raw"], (r["status"], ids))
        r = timed_call(live, "GET", f"/v1/validation/runs/{inside_id}", granted)
        c.check("D after restore: inside detail is 200 with the inside record",
                r["status"] == 200 and (r["body"] or {}).get("run_id") == inside_id,
                (r["status"], (r["body"] or {}).get("run_id")))
        r = timed_call(live, "GET",
                       f"/v1/validation/runs/{inside_id}/artifacts/art_note/content",
                       granted)
        c.check("D after restore: the inside artifact content is 200 and correct",
                r["status"] == 200
                and (r["body"] or {}).get("content") == inside_text.decode(),
                (r["status"], (r["body"] or {}).get("content")))
        evidence["after_restore"] = {"list_status": r["status"], "run_ids": ids}
    emit(OUT / "fix-defects-04-root-symlink.json", evidence)


# -- Part E: masked form measured against the cap -----------------------------


def part_e_masked_cap(live: Live, c: Checker, granted: str) -> None:
    print("\n########## PART E — masked output measured against MAX_ARTIFACT_BYTES",
          flush=True)
    # `token=x ` (8 bytes) becomes `token=[REDACTED] ` (17 bytes): the masker
    # EXPANDS. Choose n so the raw file is under the cap and the masked text is
    # over it.
    unit_raw = b"token=x "
    n = 600_000
    raw = unit_raw * n
    masked = canonical.redact_text(raw.decode("utf-8")).encode("utf-8")
    c.check("E the validator's raw artifact is under MAX_ARTIFACT_BYTES",
            len(raw) < MAX_ARTIFACT_BYTES, len(raw))
    c.check("E the validator's masked artifact is over MAX_ARTIFACT_BYTES",
            len(masked) > MAX_ARTIFACT_BYTES, len(masked))

    root = live.base / "E-mask"
    run_id = "run-20261008T000801Z"
    run_dir = root / "m"
    write_run(run_dir, run_id)
    (run_dir / "artifacts").mkdir()
    (run_dir / "artifacts" / "mask.txt").write_bytes(raw)
    small = b"token=x " * 10
    (run_dir / "artifacts" / "small.txt").write_bytes(small)
    (run_dir / "manifest.json").write_bytes(manifest_bytes(
        run_id, [], [decl("art_mask", "artifacts/mask.txt", raw),
                     decl("art_small", "artifacts/small.txt", small)]))

    evidence: dict = {"raw_bytes": len(raw), "masked_bytes": len(masked),
                      "max_artifact_bytes": MAX_ARTIFACT_BYTES,
                      "masked_sha256": sha256_bytes(masked)}
    with live.root(root):
        r = timed_call(live, "GET",
                       f"/v1/validation/runs/{run_id}/artifacts/art_mask", granted,
                       timeout=20.0)
        no_hang(c, "E artifact metadata for the expanding artifact", r)
        body = r["body"] or {}
        c.check("E artifact metadata: 200 integrity match, declared bytes agree",
                r["status"] == 200 and body.get("integrity") == "match"
                and body.get("bytes") == len(raw),
                (r["status"], body.get("integrity"), body.get("bytes")))
        c.check("E artifact metadata: digests.masked is our recomputed masked digest",
                (body.get("digests") or {}).get("masked") == sha256_bytes(masked),
                (body.get("digests") or {}).get("masked"))

        r = timed_call(live, "GET",
                       f"/v1/validation/runs/{run_id}/artifacts/art_mask/content",
                       granted, timeout=20.0)
        no_hang(c, "E content route for the expanding artifact", r)
        details = error_details(r["body"])
        c.check("E masked content: refused 413 payload_too_large",
                r["status"] == 413 and error_code(r["body"]) == "payload_too_large",
                (r["status"], error_code(r["body"])))
        c.check("E masked content: details name the masked form, the limit and the "
                "original size",
                details.get("limit") == MAX_ARTIFACT_BYTES
                and details.get("form") == "masked"
                and details.get("original_bytes") == len(raw), details)
        c.check("E masked content: the over-cap masked text was NOT returned",
                r["body"] is not None and "content" not in (r["body"] or {})
                and len(r["raw"]) < 1_000_000,
                (len(r["raw"]), list((r["body"] or {}).keys())))
        evidence["masked_content_refusal"] = {
            "status": r["status"], "seconds": r["seconds"],
            "code": error_code(r["body"]), "details": details,
            "response_bytes": len(r["raw"])}

        r = timed_call(live, "GET",
                       f"/v1/validation/runs/{run_id}/artifacts/art_mask/content?form=original",
                       granted, timeout=20.0)
        no_hang(c, "E original form for the expanding artifact", r)
        c.check("E original form: withheld 403 original_withheld (mask not a no-op)",
                r["status"] == 403 and error_code(r["body"]) == "original_withheld",
                (r["status"], error_code(r["body"])))

        # control: an expanding artifact that stays under the cap is served
        r = timed_call(live, "GET",
                       f"/v1/validation/runs/{run_id}/artifacts/art_small/content",
                       granted)
        no_hang(c, "E control small expanding artifact", r)
        c.check("E control: a masked artifact under the cap is still served 200",
                r["status"] == 200 and (r["body"] or {}).get("form") == "masked",
                (r["status"], (r["body"] or {}).get("form")))
        c.check("E control: the served content is the masked text and its digest "
                "agrees",
                (r["body"] or {}).get("content")
                == canonical.redact_text(small.decode("utf-8"))
                and (r["body"] or {}).get("served_sha256")
                == hashlib.sha256(canonical.redact_text(
                    small.decode("utf-8")).encode()).hexdigest(),
                (r["body"] or {}).get("content", "")[:40])
    emit(OUT / "fix-defects-05-masked-cap.json", evidence)


# -- Part F: legitimate deep reads survive ------------------------------------


def part_f_deep(live: Live, c: Checker, granted: str) -> None:
    print("\n########## PART F — legitimate deep record and artifact paths", flush=True)
    root = live.base / "F-deep"
    deep_id = "run-20261008T000901Z"
    shallow_id = "run-20261008T000902Z"
    deep_text = b"DEEP-ARTIFACT-SUB-NAME-CONTENT"
    deep_dir = root / "a" / "b" / "c"
    write_run(deep_dir, deep_id)
    (deep_dir / "artifacts" / "sub").mkdir(parents=True)
    (deep_dir / "artifacts" / "sub" / "name.txt").write_bytes(deep_text)
    (deep_dir / "manifest.json").write_bytes(manifest_bytes(
        deep_id, [], [decl("art_deep", "artifacts/sub/name.txt", deep_text)]))
    shallow_dir = root / "one"
    write_run(shallow_dir, shallow_id)
    (shallow_dir / "artifacts" / "sub").mkdir(parents=True)
    (shallow_dir / "artifacts" / "sub" / "name.txt").write_bytes(deep_text)
    (shallow_dir / "manifest.json").write_bytes(manifest_bytes(
        shallow_id, [], [decl("art_deep", "artifacts/sub/name.txt", deep_text)]))

    evidence: dict = {}
    with live.root(root):
        r = timed_call(live, "GET", "/v1/validation/runs?limit=100", granted)
        ids = [x.get("run_id") for x in (r["body"] or {}).get("runs", [])]
        c.check("F a record three directory levels below the root is served",
                r["status"] == 200 and deep_id in ids, (r["status"], ids))
        c.check("F a record one directory level below the root is served",
                shallow_id in ids, ids)
        for tag, run_id in (("deep", deep_id), ("shallow", shallow_id)):
            r = timed_call(live, "GET", f"/v1/validation/runs/{run_id}", granted)
            c.check(f"F {tag} record detail is 200", r["status"] == 200, r["status"])
            r = timed_call(live, "GET", f"/v1/validation/runs/{run_id}/manifest", granted)
            files = (r["body"] or {}).get("files") or []
            c.check(f"F {tag} manifest serves the nested declared path",
                    r["status"] == 200 and files
                    and files[0].get("path") == "artifacts/sub/name.txt"
                    and files[0].get("integrity") == "match",
                    (r["status"], files))
            r = timed_call(
                live, "GET",
                f"/v1/validation/runs/{run_id}/artifacts/art_deep/content", granted)
            no_hang(c, f"F {tag} nested artifact content", r)
            c.check(f"F {tag} nested artifact content is 200 with the exact bytes",
                    r["status"] == 200
                    and (r["body"] or {}).get("content") == deep_text.decode()
                    and (r["body"] or {}).get("served_sha256")
                    == sha256_bytes(deep_text),
                    (r["status"], (r["body"] or {}).get("content")))
            evidence[tag] = {
                "detail_status": 200,
                "manifest_path": files[0]["path"] if files else None,
                "content_status": r["status"], "seconds": r["seconds"]}
    c.check("F the nested artifact path is genuinely two directories deep on disk",
            (deep_dir / "artifacts" / "sub" / "name.txt").is_file()
            and (deep_dir / "artifacts" / "sub" / "name.txt").read_bytes() == deep_text)
    emit(OUT / "fix-defects-06-legit-deep.json", evidence)


def main() -> int:
    c = Checker("fix-defects (four defect classes)")
    live = Live()
    try:
        granted = live.key(["validation:read"])
        part_a_manifest_swap(live, c)
        part_b_fifo(live, c, granted)
        part_c_caps(live, c, granted)
        part_d_root_symlink(live, c, granted)
        part_e_masked_cap(live, c, granted)
        part_f_deep(live, c, granted)

        emit(OUT / "fix-defects-07-request-timings.json", live.timings)
        summary = live.emit_timing_summary()
        emit(OUT / "fix-defects-08-timing-summary.json", summary)
        c.check("all defect probes: every request completed inside its client "
                "timeout (no request hung)",
                not summary["over_10s"] and not summary["requests_without_status"],
                summary)
        c.check("all defect probes: no request exceeded 10 s",
                summary["max_seconds"] < 10.0, summary["max_seconds"])
        c.check("all defect probes: no request exceeded 5 s",
                summary["max_seconds"] < 5.0, summary["max_seconds"])
        c.check("all defect probes: no transport-level retry was needed",
                summary["transport_retries"] == 0, summary["transport_retries"])
        emit(OUT / "fix-defects-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
