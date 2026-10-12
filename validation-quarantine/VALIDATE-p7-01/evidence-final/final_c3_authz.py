#!/usr/bin/env python3
"""Criterion 3: authorised success and refusal paired on the same resource.

single_user: a `validation:read` key gets 200 on the list and all four by-id
routes; a key without the scope gets 403 `forbidden_scope` naming the required
scope. `acl` mode: the granted key gets 200; a key without the scope gets 403;
a non-granted project gets 404 `validation_run_not_found`; an unattributed run
without `validation:unattributed` gets 404 and with it 200; and the list is
grant-filtered (`runs[]`, `total`, `run_shaped_files`) with `ignored_samples[]`
empty. No route emits `project_not_found`.

Run: PYTHONPATH=../../src:. python3 c3_authz.py
"""

from __future__ import annotations

from pathlib import Path

from final_harness import (ACTOR, RECORDER_SHA, Live, Checker, artifact_entry, emit,
                     write_manifest, write_run)

OUT = Path(__file__).resolve().parent
RUN_ONE = "run-20261008T000001Z"
RUN_TWO = "run-20261008T000002Z"
RUN_THREE = "run-20261008T000003Z"
RUN_FOUR = "run-20261008T000004Z"

BY_ID_SUFFIXES = ("", "/manifest")


def main() -> int:
    c = Checker("criterion 3")
    live = Live(mode="single_user")
    try:
        # -- fixture root: one attributed run, one other-project run, one unattributed
        root = live.base / "c3-root"
        granted_dir = root / "granted" / RUN_ONE
        write_run(granted_dir, RUN_ONE)
        good = artifact_entry(granted_dir, "art_good", "artifacts/good.txt", fixture="good.txt")
        write_manifest(granted_dir, RUN_ONE, [good], mode="real",
                       projects=["prj_00000000000000000000000000"],  # replaced below
                       sources={"recorder": {"path": "scripts/x.py", "sha256": RECORDER_SHA}})
        other_dir = root / "other" / RUN_TWO
        write_run(other_dir, RUN_TWO)
        unattributed_dir = root / "none" / RUN_THREE
        write_run(unattributed_dir, RUN_THREE, "no-context-run.json")
        corrupt_dir = root / "corrupt" / RUN_FOUR
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / f"{RUN_FOUR}.json").write_text("{not json")

        # Real projects + grants, created through the API while single_user.
        granted_project = live.create_project("c3-granted")
        other_project = live.create_project("c3-other")
        live.grant(granted_project, ACTOR)
        # rewrite the attributed manifest now that the project id is known
        write_manifest(granted_dir, RUN_ONE, [good], mode="real",
                       projects=[granted_project],
                       sources={"recorder": {"path": "scripts/x.py", "sha256": RECORDER_SHA}})
        write_manifest(other_dir, RUN_TWO, [], mode="real", projects=[other_project],
                       sources={"recorder": {"path": "scripts/x.py", "sha256": RECORDER_SHA}})

        granted = live.key(["validation:read"], label="granted")
        scopeless = live.key(["objects:read"], label="scopeless")
        unattributed_key = live.key(["validation:read", "validation:unattributed"],
                                    label="unattributed")
        ungranted_actor = live.new_actor()
        ungranted = live.key(["validation:read"], actor_id=ungranted_actor, label="ungranted")

        # -- single_user: granted 200 / 403 pairs on the same resources ---------
        all_paths = ["/v1/validation/runs",
                     f"/v1/validation/runs/{RUN_ONE}",
                     f"/v1/validation/runs/{RUN_ONE}/manifest",
                     f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good",
                     f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good/content"]
        with live.root(root):
            detail = {}
            for path in all_paths:
                status, _, body, _ = live.call("GET", path, granted)
                detail[path] = {"granted_status": status, "granted_body": body}
                c.check(f"single_user granted key 200 on {path}", status == 200, status)
                status, _, body, _ = live.call("GET", path, scopeless)
                detail[path]["scopeless_status"] = status
                detail[path]["scopeless_body"] = body
                c.check(f"single_user scope-less key 403 on {path}", status == 403, status)
                c.check(f"scope-less code is forbidden_scope on {path}",
                        body.get("error", {}).get("code") == "forbidden_scope",
                        body.get("error", {}).get("code"))
                c.check(f"scope-less details.required names validation:read on {path}",
                        body.get("error", {}).get("details", {}).get("required")
                        == "validation:read",
                        body.get("error", {}).get("details"))
            emit(OUT / "c3-01-single-user-pairs.json", detail)

            status, _, body, _ = live.call("GET", "/v1/validation/runs")
            emit(OUT / "c3-02-no-token.json", body)
            c.check("no token is 401 unauthenticated", status == 401, status)
            c.check("no-token code is unauthenticated",
                    body.get("error", {}).get("code") == "unauthenticated",
                    body.get("error", {}).get("code"))

        # -- acl mode ----------------------------------------------------------
        live.set_mode("acl")
        acl_evidence = {}
        with live.root(root):
            for path in all_paths:
                status, _, body, _ = live.call("GET", path, granted)
                acl_evidence[f"granted::{path}"] = {"status": status, "body": body}
                c.check(f"acl granted key 200 on {path}", status == 200, status)
                status, _, body, _ = live.call("GET", path, scopeless)
                acl_evidence[f"scopeless::{path}"] = {"status": status, "body": body}
                c.check(f"acl scope-less key 403 on {path}", status == 403, status)
                c.check(f"acl scope-less code forbidden_scope on {path}",
                        body.get("error", {}).get("code") == "forbidden_scope",
                        body.get("error", {}).get("code"))

            # Detail: non-granted project and unattributed run, same code.
            status, _, body, _ = live.call("GET", f"/v1/validation/runs/{RUN_TWO}", granted)
            acl_evidence["other-project-detail"] = {"status": status, "body": body}
            c.check("acl non-granted project detail is 404", status == 404, status)
            c.check("acl non-granted project refuses validation_run_not_found",
                    body.get("error", {}).get("code") == "validation_run_not_found",
                    body.get("error", {}).get("code"))
            c.check("acl non-granted project never emits project_not_found",
                    body.get("error", {}).get("code") != "project_not_found")
            status, _, body, _ = live.call("GET", f"/v1/validation/runs/{RUN_THREE}",
                                           granted)
            acl_evidence["unattributed-detail-without-scope"] = {"status": status, "body": body}
            c.check("acl unattributed run without validation:unattributed is 404",
                    status == 404, status)
            c.check("acl unattributed refusal is validation_run_not_found",
                    body.get("error", {}).get("code") == "validation_run_not_found",
                    body.get("error", {}).get("code"))
            status, _, body, _ = live.call("GET", f"/v1/validation/runs/{RUN_THREE}",
                                           unattributed_key)
            acl_evidence["unattributed-detail-with-scope"] = {"status": status, "body": body}
            c.check("acl unattributed run with validation:unattributed is 200",
                    status == 200, status)
            # An actor with the scope but no grant is still refused.
            status, _, body, _ = live.call("GET", f"/v1/validation/runs/{RUN_ONE}",
                                           ungranted)
            acl_evidence["ungranted-actor-detail"] = {"status": status, "body": body}
            c.check("acl ungranted actor is 404 on a granted project's run",
                    status == 404, status)
            c.check("acl ungranted actor refusal is validation_run_not_found",
                    body.get("error", {}).get("code") == "validation_run_not_found",
                    body.get("error", {}).get("code"))
            # Corrupt record fails closed to the unattributed rule.
            status, _, body, _ = live.call("GET", f"/v1/validation/runs/{RUN_FOUR}",
                                           granted)
            acl_evidence["corrupt-detail-granted"] = {"status": status, "body": body}
            c.check("acl corrupt record fails closed (404) for a granted key",
                    status == 404, status)

            # -- list is grant-filtered ---------------------------------------
            status, _, body, _ = live.call("GET", "/v1/validation/runs?limit=100", granted)
            acl_evidence["granted-list"] = {"status": status, "body": body}
            emit(OUT / "c3-03-acl-granted-list.json", body)
            c.check("acl granted list is 200", status == 200, status)
            c.check("acl list reports visibility=grant_filtered",
                    body.get("visibility") == "grant_filtered", body.get("visibility"))
            run_ids = [r["run_id"] for r in body["runs"]]
            c.check("acl granted list contains only the granted run",
                    run_ids == [RUN_ONE], run_ids)
            c.check("acl granted list total counts only what the key may read",
                    body["total"] == 1, body["total"])
            c.check("the ungranted project's run is absent from runs[]",
                    RUN_TWO not in run_ids)
            c.check("the unattributed run is absent from runs[]",
                    RUN_THREE not in run_ids)
            c.check("the corrupt run is absent from the granted key's runs[]",
                    RUN_FOUR not in run_ids)
            c.check("discovery.run_shaped_files counts visible runs",
                    body["discovery"]["run_shaped_files"] == 1,
                    body["discovery"]["run_shaped_files"])
            c.check("acl list ignored_samples[] is empty", body["ignored_samples"] == [],
                    body["ignored_samples"])
            c.check("acl granted corrupt_count excludes the invisible corrupt record",
                    body["corrupt_count"] == 0, body["corrupt_count"])

            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs?limit=100&project={other_project}", granted)
            acl_evidence["granted-list-project-filter"] = {"status": status, "body": body}
            c.check("a project filter naming the ungranted project cannot reach it",
                    body["runs"] == [] and body["total"] == 0,
                    (body.get("total"), body.get("runs")))

            status, _, body, _ = live.call("GET", "/v1/validation/runs?limit=100",
                                           unattributed_key)
            acl_evidence["unattributed-list"] = {"status": status, "body": body}
            emit(OUT / "c3-04-acl-unattributed-list.json", body)
            # Per §2/§6 a corrupt record has no readable manifest, so it fails
            # closed to unattributed and is visible only to the unattributed key.
            c.check("acl unattributed key sees the attributed, unattributed and "
                    "fail-closed corrupt runs",
                    sorted(r["run_id"] for r in body["runs"])
                    == sorted([RUN_ONE, RUN_THREE, RUN_FOUR]),
                    sorted(r["run_id"] for r in body["runs"]))
            c.check("acl unattributed list total is 3", body["total"] == 3, body["total"])
            corrupt_row = next((r for r in body["runs"] if r["run_id"] == RUN_FOUR), None)
            c.check("the fail-closed corrupt row is listed, not turned into an empty list",
                    corrupt_row is not None and corrupt_row["record_status"] == "corrupt",
                    corrupt_row and corrupt_row["record_status"])
            c.check("the corrupt row serves a null verdict and a named corrupt_reason",
                    corrupt_row and corrupt_row["verdict"] is None
                    and corrupt_row["corrupt_reason"],
                    corrupt_row and (corrupt_row["verdict"], corrupt_row["corrupt_reason"]))

            status, _, body, _ = live.call("GET", "/v1/validation/runs?limit=100",
                                           scopeless)
            acl_evidence["scopeless-list"] = {"status": status, "body": body}
            c.check("acl list with a scope-less key is 403", status == 403, status)

            # Confirm no route anywhere returned project_not_found.
            codes = {k: v["body"].get("error", {}).get("code")
                     for k, v in acl_evidence.items() if v.get("body")}
            c.check("no acl response carries project_not_found",
                    "project_not_found" not in codes.values(),
                    sorted({x for x in codes.values() if x is not None}))

        emit(OUT / "c3-05-acl-pairs.json", acl_evidence)
        emit(OUT / "c3-06-criterion3-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
