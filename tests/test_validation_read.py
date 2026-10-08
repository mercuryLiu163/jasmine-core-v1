"""P7-01 Validation read contract: the five `GET` routes over real and fixture records.

Two evidence rules govern this file:

* the nine real run records are read **read-only** from the source repository's
  gitignored `validation-runs/` tree and compared field by field; they are never
  modified, and the acceptance test is skipped (with a clear message) in a clone
  that lacks them;
* every fixture lives under `tests/fixtures/p7/` and is copied into a temporary
  root built by the test. Nothing here is ever written into an operator root, and
  the synthetic FAIL record labels itself `synthetic: true`.

**Acceptance criterion 1 is BLOCKED.** No real P7-format FAIL run exists on disk
(the nine records hold 0 FAIL cases, 0 `failure_class`, 0 `failure_output`). The
FAIL code path is exercised on the labelled synthetic fixture below; a fixture is
never evidence, and the unblock action is for the P7 owner to record one genuine
failing scenario with `recorder.py` into the configured run root.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from test_api import ACTOR, HOST, ApiTestCase  # noqa: E402

from jasmine_core import auth, canonical, db, errors, fingerprint, ids, registry  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p7"
#: Fixture *source* files. NOT named `artifacts/`: `.gitignore` excludes any
#: directory with that name, so the files would be missing from a clone and the
#: committed tests could not run. The manifest paths inside a run stay
#: `artifacts/...`; this is only where the bytes are kept before being copied
#: into a temporary run directory.
FILES = FIXTURES / "files"
REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SCRIPT = REPO_ROOT / "scripts" / "p7" / "p7_contract.py"

#: The operator's source repository. Read-only; the tree is gitignored, so a
#: checkout without it skips the real-record test rather than failing.
REAL_ROOT = Path("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs")

RUN_ONE = "run-20261008T000001Z"
RUN_TWO = "run-20261008T000002Z"
RUN_THREE = "run-20261008T000003Z"
RUN_FOUR = "run-20261008T000004Z"
BUNDLE_SCHEMA = "jasmine.validation-bundle.v1"
RECORDER_SHA = "a" * 64
SENTINEL = "N/A (P0 not implemented)"
CONTEXT_MISSING_REASONS = {
    "recorded as N/A (P0 not implemented)",
    "the case records a reason, not a context pack",
    "the section records run details, not a context pack",
}


# -- fixture builders ---------------------------------------------------------


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ValidationApiTestCase(ApiTestCase):
    """A live server, a fresh DB, a temporary run root and two extra actors."""

    def setUp(self) -> None:
        super().setUp()
        self.stranger = ids.new_id("act")
        self.agent = ids.new_id("act")
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with db.transaction(conn):
            reg = registry.Registry(conn)
            reg.upsert_actor(self.stranger, kind="human", display_name="stranger",
                             home_host_id=HOST)
            reg.upsert_actor(self.agent, kind="agent", display_name="agent",
                             home_host_id=HOST)
        self.root = Path(tempfile.mkdtemp(prefix="jasmine-p7-root-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    # -- environment --

    def env(self, root: Path | str | None = None, *, mode: str = "single_user", **extra):
        values = {"JASMINE_CORE_VALIDATION_RUNS":
                  str(self.root if root is None else root),
                  "JASMINE_MEMORY_DEPLOYMENT_MODE": mode}
        values.update(extra)
        return patch.dict(os.environ, values)

    # -- keys --

    def key_for(self, actor_id: str, scopes: list[str], label: str = "k") -> str:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return auth.Auth(conn).issue_key(actor_id=actor_id, label=label, scopes=scopes)["token"]

    # -- records and manifests --

    def write_run(self, run_dir: Path, run_id: str, fixture: str = "ok-run.json",
                  **overrides) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        data = fixture_json(fixture)
        data["run_id"] = run_id
        data.update(overrides)
        path = run_dir / f"{run_id}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def write_corrupt(self, run_dir: Path, run_id: str) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{run_id}.json"
        path.write_bytes((FIXTURES / "corrupt-run.json").read_bytes())
        return path

    def artifact_entry(self, run_dir: Path, artifact_id: str, rel_path: str, *,
                       fixture: str | None = None, content: bytes | None = None,
                       media_type: str = "text/plain", credential_shaped: bool = False,
                       declared_path: str | None = None, declared_sha: str | None = None,
                       declared_bytes: int | None = None) -> dict:
        if fixture is not None or content is not None:
            destination = run_dir / rel_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if fixture is not None:
                shutil.copyfile(FILES / fixture, destination)
            if content is not None:
                destination.write_bytes(content)
        actual = (run_dir / rel_path).read_bytes() if os.path.exists(run_dir / rel_path) else b""
        return {
            "artifact_id": artifact_id,
            "path": rel_path if declared_path is None else declared_path,
            "bytes": len(actual) if declared_bytes is None else declared_bytes,
            "sha256": hashlib.sha256(actual).hexdigest() if declared_sha is None else declared_sha,
            "media_type": media_type,
            "credential_shaped": credential_shaped,
        }

    def write_manifest(self, run_dir: Path, run_id: str, files: list[dict], *,
                       mode: str = "component", projects: list[str] | None = None,
                       sources: dict | None = None, frozen_at: str | None = None,
                       schema: str = BUNDLE_SCHEMA, raw: str | None = None) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "manifest.json"
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
            return path
        manifest = {
            "schema": schema,
            "run_id": run_id,
            "mode": mode,
            "projects": list(projects or []),
            "sources": sources if sources is not None
            else {"recorder": {"path": "scripts/p0-acceptance.py", "sha256": RECORDER_SHA}},
            "files": files,
        }
        if frozen_at is not None:
            manifest["frozen_at"] = frozen_at
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def build_bundle(self, rel_dir: str = "p7-ok", run_id: str = RUN_ONE, *,
                     mode: str = "component", projects: list[str] | None = None,
                     sources: dict | None = None) -> Path:
        run_dir = self.root / rel_dir / run_id
        self.write_run(run_dir, run_id)
        files = [
            self.artifact_entry(run_dir, "art_good", "artifacts/good.txt", fixture="good.txt"),
            self.artifact_entry(run_dir, "art_assign", "artifacts/assign.log",
                                fixture="assign.txt"),
            self.artifact_entry(run_dir, "art_plain", "artifacts/plain.txt", fixture="plain.txt"),
            self.artifact_entry(run_dir, "art_binary", "artifacts/binary.bin",
                                fixture="binary.bin", media_type="application/octet-stream"),
            self.artifact_entry(run_dir, "art_declared_secret", "artifacts/secret.txt",
                                fixture="good.txt", credential_shaped=True),
        ]
        self.write_manifest(run_dir, run_id, files, mode=mode, projects=projects,
                            sources=sources, frozen_at="2026-10-08T00:10:00Z")
        return run_dir

    # -- grants --

    def create_project(self, name: str) -> str:
        status, body = self.call("POST", "/v1/projects",
                                 {"name": name, "host_id": HOST}, token=self.admin_token)
        self.assertEqual(status, 201, body)
        return body["object"]["project_id"]

    def grant(self, project_id: str, actor_id: str) -> None:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        event_id = conn.execute("SELECT source_event_id FROM projects WHERE project_id=?",
                                (project_id,)).fetchone()["source_event_id"]
        with db.transaction(conn):
            conn.execute(
                "INSERT INTO memory_project_actors (project_id, actor_id,"
                " granted_by_event_id, created_at) VALUES (?,?,?,?)",
                (project_id, actor_id, event_id, "2026-01-01T00:00:00.000000Z"))

    # -- raw request, so response headers are visible --

    def request_raw(self, method: str, path: str, token: str | None = None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), json.loads(
                    response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, dict(exc.headers), json.loads(raw) if raw else {}


# -- the five routes against a fixture run root -------------------------------


class TheFiveRoutes(ValidationApiTestCase):
    def test_list_over_a_fixture_root(self) -> None:
        self.build_bundle()
        self.write_run(self.root / "p7-plain" / RUN_TWO, RUN_TWO, "no-context-run.json")
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs", token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["limit"], 20)
        self.assertEqual(body["offset"], 0)
        self.assertFalse(body["has_more"])
        self.assertTrue(body["complete"])
        self.assertEqual(body["visibility"], "all")
        self.assertEqual(body["corrupt_count"], 0)
        self.assertEqual(body["duplicate_count"], 0)
        run_ids = {row["run_id"] for row in body["runs"]}
        self.assertEqual(run_ids, {RUN_ONE, RUN_TWO})

    def test_detail_manifest_artifact_and_content(self) -> None:
        run_dir = self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            status, detail = self.call("GET", f"/v1/validation/runs/{RUN_ONE}", token=token)
            self.assertEqual(status, 200, detail)
            self.assertEqual(detail["run_id"], RUN_ONE)
            self.assertEqual(detail["verdict"], "PASS")
            self.assertTrue(detail["verdict_consistent"])
            self.assertFalse(detail["synthetic"])
            self.assertEqual(detail["unknown_record_keys"], ["extra_record_key"])
            case = detail["cases"][0]
            self.assertTrue(case["chain"]["context"]["present"])
            self.assertEqual(case["chain"]["extraction"]["present"], False)
            self.assertEqual(case["chain"]["resolution"]["present"], False)
            self.assertEqual(case["chain"]["state"]["present"], True)
            self.assertEqual(case["evidence"][0]["status"], "unaddressable")
            self.assertEqual(case["evidence"][0]["recorded"], "/absolute/host/path/stdout.txt")

            status, manifest = self.call("GET", f"/v1/validation/runs/{RUN_ONE}/manifest",
                                         token=token)
            self.assertEqual(status, 200, manifest)
            self.assertEqual(manifest["status"], "frozen")
            self.assertEqual(manifest["mode"], "component")
            self.assertEqual(manifest["mode_basis"], "declared by the manifest")
            self.assertEqual(manifest["manifest_sha256"], sha256_file(run_dir / "manifest.json"))
            self.assertEqual(manifest["manifest_authentication"], "unauthenticated")
            integrity = {entry["artifact_id"]: entry["integrity"]
                         for entry in manifest["files"]}
            self.assertEqual(integrity["art_good"], "match")

            status, meta = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good", token=token)
            self.assertEqual(status, 200, meta)
            self.assertEqual(meta["integrity"], "match")
            self.assertEqual(meta["bytes"], len(b"hello bundle\n"))
            self.assertEqual(meta["declared_sha256"],
                             hashlib.sha256(b"hello bundle\n").hexdigest())
            self.assertEqual(meta["forms"], ["masked", "original"])
            self.assertEqual(meta["digests"]["original"],
                             hashlib.sha256(b"hello bundle\n").hexdigest())

            status, content = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good/content", token=token)
            self.assertEqual(status, 200, content)
            self.assertEqual(content["form"], "masked")
            self.assertFalse(content["content_is_original"])
            self.assertEqual(content["content"], "hello bundle\n")
            self.assertEqual(content["served_sha256"], content["masked_sha256"])

    def test_masked_masks_assignment_secret_and_original_is_withheld(self) -> None:
        self.build_bundle()
        # `looks_like_credential` does NOT match an assignment-shaped secret: the
        # original gate is `redact_text(text) == text`, which is stronger.
        text = (FILES / "assign.txt").read_text()
        self.assertFalse(canonical.looks_like_credential(text))
        self.assertNotEqual(canonical.redact_text(text), text)
        with self.env():
            token = self.key(["validation:read"])
            status, masked = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_assign/content", token=token)
            self.assertEqual(status, 200, masked)
            self.assertEqual(masked["form"], "masked")
            self.assertIn("[REDACTED]", masked["content"])
            self.assertNotIn("hunter2hunter2", masked["content"])
            self.assertEqual(masked["mask_algorithm"], "canonical.redact_text")
            self.assertEqual(masked["mask_version"], canonical.REDACT_TEXT_VERSION)
            self.assertNotEqual(masked["masked_sha256"], masked["original_sha256"])

            status, withheld = self.call(
                "GET",
                f"/v1/validation/runs/{RUN_ONE}/artifacts/art_assign/content?form=original",
                token=token)
            self.assertEqual(status, 403, withheld)
            self.assertEqual(withheld["error"]["code"], "original_withheld")
            self.assertEqual(withheld["error"]["details"]["reason"], "mask_not_noop")
            self.assertEqual(withheld["error"]["details"]["masked_sha256"],
                             masked["masked_sha256"])

    def test_original_is_served_when_the_masker_is_a_no_op(self) -> None:
        self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            status, content = self.call(
                "GET",
                f"/v1/validation/runs/{RUN_ONE}/artifacts/art_plain/content?form=original"
                "&download=1", token=token)
        self.assertEqual(status, 200, content)
        self.assertEqual(content["form"], "original")
        self.assertTrue(content["content_is_original"])
        self.assertEqual(content["content"], (FILES / "plain.txt").read_text())
        self.assertEqual(content["original_sha256"], content["masked_sha256"])

    def test_content_response_headers_are_not_renderable(self) -> None:
        self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            status, headers, body = self.request_raw(
                "GET",
                f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good/content?download=1",
                token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Security-Policy"], "default-src 'none'; sandbox")
        self.assertEqual(headers["X-Validation-Form"], "masked")
        self.assertEqual(headers["X-Validation-Sha256"], body["served_sha256"])
        self.assertEqual(headers["X-Validation-Original-Sha256"], body["original_sha256"])
        self.assertEqual(headers["Content-Disposition"],
                         'attachment; filename="art_good.txt"')

    def test_non_text_content_is_415(self) -> None:
        self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_binary/content",
                token=token)
        self.assertEqual(status, 415, body)
        self.assertEqual(body["error"]["code"], "unsupported_media_type")

    def test_declared_credential_shaped_withholds_the_original(self) -> None:
        self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call(
                "GET",
                f"/v1/validation/runs/{RUN_ONE}/artifacts/art_declared_secret/content"
                "?form=original", token=token)
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["details"]["reason"], "credential_shaped_declared")


# -- the real nine records ----------------------------------------------------


@unittest.skipUnless(REAL_ROOT.is_dir(),
                     f"the real run tree {REAL_ROOT} is absent (it is gitignored, so a "
                     "CI-less clone lacks it); this test must run and pass where it exists")
class TheRealNineRecords(ValidationApiTestCase):
    def test_nine_run_ids_verdicts_counts_and_chain(self) -> None:
        with self.env(REAL_ROOT):
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs?limit=100", token=token)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["total"], 9)
            self.assertEqual(body["count"], 9)
            # Recorded verdicts, unchanged, in the single ordering tuple.
            verdicts = {row["run_id"]: row["verdict"] for row in body["runs"]}
            self.assertEqual(verdicts["run-20260929T173423Z"], "BLOCKED")
            self.assertEqual(verdicts["run-20260929T162925Z"], "PASS")
            self.assertEqual(verdicts["run-20260929T162307Z"], "PASS")
            self.assertEqual(verdicts["run-20260929T161915Z"], "PASS")
            for run_id in ("run-20260929T145257Z", "run-20260929T144539Z",
                           "run-20260929T143845Z", "run-20260929T143156Z",
                           "run-20260929T142847Z"):
                self.assertEqual(verdicts[run_id], "BLOCKED")
            self.assertEqual(sorted(verdicts.values()).count("PASS"), 3)
            self.assertEqual(sorted(verdicts.values()).count("BLOCKED"), 6)
            for row in body["runs"]:
                self.assertTrue(row["verdict_consistent"], row["run_id"])
                self.assertEqual(row["case_counts"]["total"], 7)
                self.assertEqual(row["case_counts"]["fail"], 0)
                self.assertEqual(row["manifest_status"], "absent")
                self.assertEqual(row["record_status"], "ok")

            blocked = {row["run_id"] for row in body["runs"] if row["verdict"] == "BLOCKED"}
            self.assertEqual(len(blocked), 6)
            missing_reasons = set()
            for row in body["runs"]:
                status, detail = self.call("GET", f"/v1/validation/runs/{row['run_id']}",
                                           token=token)
                self.assertEqual(status, 200, detail)
                self.assertEqual(detail["verdict"], row["verdict"])
                self.assertEqual(len(detail["cases"]), 7)
                agent_present = False
                for case in detail["cases"]:
                    self.assertFalse(case["chain"]["extraction"]["present"])
                    self.assertFalse(case["chain"]["resolution"]["present"])
                    self.assertEqual(
                        case["chain"]["extraction"]["missing_reason"],
                        "the recorder records no Extraction section")
                    self.assertEqual(
                        case["chain"]["resolution"]["missing_reason"],
                        "the recorder records no Resolution section")
                    self.assertFalse(case["chain"]["context"]["present"])
                    missing_reasons.add(case["chain"]["context"]["missing_reason"])
                    agent_present = agent_present or case["chain"]["agent"]["present"]
                    self.assertEqual(case["evidence_grade"], "unknown")
                if row["run_id"] in blocked:
                    self.assertFalse(agent_present, row["run_id"])
                else:
                    self.assertTrue(agent_present, row["run_id"])
            # The three frozen Context strings are all exercised by real data.
            self.assertEqual(missing_reasons, CONTEXT_MISSING_REASONS)

    def test_acceptance_criterion_1_real_fail_run_is_blocked(self) -> None:
        # Criterion 1 (read an existing real FAIL Run) is BLOCKED: the real
        # records hold no FAIL case, no `failure_class` and no `failure_output`.
        # The FAIL code path is only exercised by the labelled synthetic fixture.
        records = sorted(REAL_ROOT.rglob("run-*.json"))
        self.assertEqual(len(records), 9)
        fail_cases = 0
        for path in records:
            data = json.loads(path.read_text(encoding="utf-8"))
            fail_cases += sum(1 for case in data.get("cases", [])
                              if case.get("verdict") == "FAIL")
        self.assertEqual(fail_cases, 0)


# -- criterion 4 negatives ----------------------------------------------------


class ArtifactNegatives(ValidationApiTestCase):
    def test_missing_and_tampered_files(self) -> None:
        run_dir = self.root / "p7-neg" / RUN_ONE
        self.write_run(run_dir, RUN_ONE)
        files = [
            self.artifact_entry(run_dir, "art_missing", "artifacts/missing.txt",
                                declared_sha="2" * 64, declared_bytes=3),
            self.artifact_entry(run_dir, "art_tampered", "artifacts/tampered.txt",
                                fixture="tampered.txt",
                                declared_sha=sha256_file(FILES / "tampered-original.txt"),
                                declared_bytes=len(b"original bytes\n")),
        ]
        self.write_manifest(run_dir, RUN_ONE, files)
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_missing", token=token)
            self.assertEqual(status, 404, body)
            self.assertEqual(body["error"]["code"], "artifact_not_found")
            self.assertEqual(body["error"]["details"]["reason"], "missing")

            status, body = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_tampered", token=token)
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "artifact_modified")

            status, manifest = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/manifest", token=token)
            self.assertEqual(status, 200, manifest)
            by_id = {entry["artifact_id"]: entry for entry in manifest["files"]}
            self.assertEqual(by_id["art_missing"]["integrity"], "missing")
            self.assertEqual(by_id["art_tampered"]["integrity"], "mismatch")

    def test_every_declared_path_escape_refuses_the_manifest(self) -> None:
        escapes = {
            "art_dotdot": "../outside/loot.txt",
            "art_absolute": "/tmp/outside/loot.txt",
            "art_drive": "C:foo.txt",
            "art_unc": "\\\\host\\share\\f.txt",
            "art_url": "https://evil.example/x.txt",
        }
        for index, (artifact_id, path) in enumerate(escapes.items()):
            with self.subTest(path=path):
                run_id = f"run-20261008T0001{index:02d}Z"
                sub_root = self.root / f"escape-{artifact_id}"
                run_dir = sub_root / "leaf" / run_id
                self.write_run(run_dir, run_id)
                files = [{"artifact_id": artifact_id, "path": path, "bytes": 1,
                          "sha256": "0" * 64, "media_type": "text/plain",
                          "credential_shaped": False}]
                self.write_manifest(run_dir, run_id, files)
                with self.env(sub_root):
                    token = self.key(["validation:read"])
                    status, body = self.call("GET", f"/v1/validation/runs/{run_id}/manifest",
                                             token=token)
                self.assertEqual(status, 409, body)
                self.assertEqual(body["error"]["code"], "validation_manifest_corrupt")
                self.assertIn(artifact_id, body["error"]["details"]["rule"])

    def test_symlinked_file_and_intermediate_directory_are_refused(self) -> None:
        run_dir = self.root / "p7-links" / RUN_ONE
        self.write_run(run_dir, RUN_ONE)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "loot.txt").write_text("secret outside the run directory\n")
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        os.symlink(outside / "loot.txt", run_dir / "artifacts" / "link.txt")
        os.symlink(outside, run_dir / "artifacts" / "sub")
        files = [
            self.artifact_entry(run_dir, "art_symfile", "artifacts/link.txt",
                                declared_sha="1" * 64, declared_bytes=1),
            self.artifact_entry(run_dir, "art_symdir", "artifacts/sub/loot.txt",
                                declared_sha="1" * 64, declared_bytes=1),
        ]
        self.write_manifest(run_dir, RUN_ONE, files)
        with self.env():
            token = self.key(["validation:read"])
            for artifact_id in ("art_symfile", "art_symdir"):
                status, body = self.call(
                    "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/{artifact_id}",
                    token=token)
                self.assertEqual(status, 404, (artifact_id, body))
                self.assertEqual(body["error"]["details"]["reason"], "symlink_refused")
            status, manifest = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/manifest", token=token)
            self.assertEqual(status, 200, manifest)
            integrity = {entry["artifact_id"]: entry["integrity"]
                         for entry in manifest["files"]}
            self.assertEqual(integrity["art_symfile"], "symlink_refused")
            self.assertEqual(integrity["art_symdir"], "symlink_refused")


# -- criterion 5: pagination and corrupt/duplicate rows -----------------------


class PaginationAndCorruptRows(ValidationApiTestCase):
    def _root_with_real_records(self) -> Path:
        target = self.root / "paged"
        for index, path in enumerate(sorted(REAL_ROOT.rglob("run-*.json"))):
            destination = target / f"d{index:02d}" / "leaf" / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
        return target

    @unittest.skipUnless(REAL_ROOT.is_dir(), f"the real run tree {REAL_ROOT} is absent")
    def test_page_walk_has_no_duplicates_or_omissions(self) -> None:
        target = self._root_with_real_records()
        self.write_corrupt(target / "corrupt", RUN_FOUR)
        # A duplicate pair: the same stem in two directories, with different
        # content so the two rows are distinguishable by `record_sha256`.
        self.write_run(target / "pair-a" / RUN_ONE, RUN_ONE)
        self.write_run(target / "pair-b" / RUN_ONE, RUN_ONE, notes=["the duplicate copy"])
        with self.env(target):
            token = self.key(["validation:read"])
            status, full = self.call("GET", "/v1/validation/runs?limit=100", token=token)
            self.assertEqual(status, 200, full)
            self.assertEqual(full["total"], 12)
            self.assertEqual(full["count"], 12)
            self.assertEqual(full["corrupt_count"], 1)
            self.assertEqual(full["duplicate_count"], 2)
            self.assertEqual([row["record_status"] for row in full["runs"]].count("corrupt"), 1)
            self.assertEqual([row["record_status"] for row in full["runs"]].count("duplicate"), 2)
            corrupt = next(row for row in full["runs"] if row["record_status"] == "corrupt")
            self.assertIsNone(corrupt["verdict"])
            self.assertIsNotNone(corrupt["corrupt_reason"])
            pages = []
            offset = 0
            while True:
                status, page = self.call(
                    "GET", f"/v1/validation/runs?limit=4&offset={offset}", token=token)
                self.assertEqual(status, 200, page)
                pages.extend(page["runs"])
                self.assertEqual(page["has_more"], offset + page["count"] < page["total"])
                offset += page["count"]
                if not page["has_more"]:
                    break
            self.assertEqual(pages, full["runs"])
            self.assertEqual(len(pages), 12)

    @unittest.skipUnless(REAL_ROOT.is_dir(), f"the real run tree {REAL_ROOT} is absent")
    def test_filters_and_corrupt_rows(self) -> None:
        target = self._root_with_real_records()
        self.write_corrupt(target / "corrupt", RUN_FOUR)
        with self.env(target):
            token = self.key(["validation:read"])
            status, unfiltered = self.call("GET", "/v1/validation/runs", token=token)
            self.assertEqual(status, 200, unfiltered)
            self.assertEqual(unfiltered["total"], 10)
            self.assertEqual(unfiltered["corrupt_count"], 1)
            status, filtered = self.call("GET", "/v1/validation/runs?verdict=PASS", token=token)
            self.assertEqual(status, 200, filtered)
            self.assertEqual(filtered["total"], 3)
            self.assertTrue(all(row["verdict"] == "PASS" for row in filtered["runs"]))
            # A corrupt row never matches a positive filter, and is still listed
            # (never disguised as an empty list) when unfiltered.
            self.assertEqual([row["record_status"] for row in filtered["runs"]].count("corrupt"), 0)
            self.assertGreaterEqual(unfiltered["count"], 1)

    def test_unreadable_subtree_yields_503_index_incomplete(self) -> None:
        self.build_bundle()
        unreadable = self.root / "unreadable"
        unreadable.mkdir()
        (unreadable / "run-20261008T000009Z.json").write_text("{}")
        os.chmod(unreadable, 0o000)
        self.addCleanup(os.chmod, unreadable, 0o700)
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs", token=token)
            self.assertEqual(status, 503, body)
            self.assertEqual(body["error"]["code"], "validation_not_configured")
            self.assertEqual(body["error"]["details"]["reason"], "index_incomplete")
            self.assertGreaterEqual(body["error"]["details"]["counts"]["unreadable_entries"], 1)
            self.assertLessEqual(len(body["error"]["details"]["samples"]), 20)

    def test_by_id_reads_still_work_when_a_sibling_subtree_is_unreadable(self) -> None:
        self.build_bundle()
        unreadable = self.root / "unreadable"
        unreadable.mkdir()
        os.chmod(unreadable, 0o000)
        self.addCleanup(os.chmod, unreadable, 0o700)
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_ONE}", token=token)
            self.assertEqual(status, 200, body)

    def test_empty_root_reports_empty_reason(self) -> None:
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs", token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["runs"], [])
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["empty_reason"], "no_run_shaped_files")

    def test_all_filtered_out_reports_empty_reason(self) -> None:
        self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs?stage=NOPE", token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["runs"], [])
        self.assertEqual(body["empty_reason"], "all_filtered_out")

    def test_walk_caps_return_503_index_too_large(self) -> None:
        from jasmine_core.validation import reads
        for name, value in (("MAX_SCANNED_ENTRIES", 1), ("MAX_RUN_SHAPED", 1)):
            with self.subTest(cap=name):
                self.build_bundle(rel_dir=f"caps-{name}")
                self.write_run(self.root / f"caps-pair-{name}" / RUN_TWO, RUN_TWO,
                               "no-context-run.json")
                with patch.object(reads, name, value), self.env():
                    token = self.key(["validation:read"])
                    status, body = self.call("GET", "/v1/validation/runs", token=token)
                self.assertEqual(status, 503, body)
                self.assertEqual(body["error"]["code"], "validation_index_too_large")
                self.assertEqual(body["error"]["details"]["cap"], name)


# -- criterion 3: paired authorisation ---------------------------------------


class PairedAuthorisation(ValidationApiTestCase):
    def test_single_user_grants_and_scope_refusal_on_the_same_resource(self) -> None:
        self.build_bundle()
        self.write_run(self.root / "p7-plain" / RUN_TWO, RUN_TWO, "no-context-run.json")
        with self.env():
            granted = self.key(["validation:read"], "granted")
            scopeless = self.key(["objects:read"], "scopeless")
            for path in ("/v1/validation/runs", f"/v1/validation/runs/{RUN_ONE}",
                         f"/v1/validation/runs/{RUN_ONE}/manifest"):
                status, body = self.call("GET", path, token=granted)
                self.assertEqual(status, 200, (path, body))
                status, body = self.call("GET", path, token=scopeless)
                self.assertEqual(status, 403, (path, body))
                self.assertEqual(body["error"]["code"], "forbidden_scope")
                self.assertEqual(body["error"]["details"]["required"], "validation:read")
            status, body = self.call("GET", "/v1/validation/runs")
            self.assertEqual(status, 401, body)

    def test_acl_grant_filtering_and_isolation(self) -> None:
        granted_project = self.create_project("granted")
        other_project = self.create_project("other")
        self.grant(granted_project, ACTOR)
        granted_dir = self.root / "acl-granted" / RUN_ONE
        self.write_run(granted_dir, RUN_ONE)
        self.write_manifest(granted_dir, RUN_ONE, [], mode="real",
                            projects=[granted_project],
                            sources={"recorder": {"path": "scripts/x.py", "sha256": RECORDER_SHA}})
        other_dir = self.root / "acl-other" / RUN_TWO
        self.write_run(other_dir, RUN_TWO)
        self.write_manifest(other_dir, RUN_TWO, [], mode="real", projects=[other_project],
                            sources={"recorder": {"path": "scripts/x.py", "sha256": RECORDER_SHA}})
        unattributed_dir = self.root / "acl-none" / RUN_THREE
        self.write_run(unattributed_dir, RUN_THREE, "no-context-run.json")

        with self.env(mode="acl"):
            granted = self.key(["validation:read"], "granted")
            scopeless = self.key(["objects:read"], "scopeless")
            unattributed = self.key(["validation:read", "validation:unattributed"],
                                    "unattributed")

            # Detail: granted 200; scope-less 403; non-granted project 404; an
            # unattributed run 404 without the explicit grant and 200 with it.
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_ONE}", token=granted)
            self.assertEqual(status, 200, body)
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_ONE}", token=scopeless)
            self.assertEqual(status, 403, body)
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_TWO}", token=granted)
            self.assertEqual(status, 404, body)
            self.assertEqual(body["error"]["code"], "validation_run_not_found")
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_THREE}", token=granted)
            self.assertEqual(status, 404, body)
            self.assertEqual(body["error"]["code"], "validation_run_not_found")
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_THREE}",
                                     token=unattributed)
            self.assertEqual(status, 200, body)

            # List: grant-filtered, and `total`/`runs[]` exclude the ungranted and
            # unattributed runs for the granted key.
            status, body = self.call("GET", "/v1/validation/runs", token=granted)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["visibility"], "grant_filtered")
            self.assertEqual([row["run_id"] for row in body["runs"]], [RUN_ONE])
            self.assertEqual(body["total"], 1)
            self.assertEqual(body["discovery"]["run_shaped_files"], 1)
            # AD2: no ignored sample paths in `acl` mode, only the count.
            self.assertEqual(body["ignored_samples"], [])
            self.assertGreaterEqual(body["ignored_entries"], 0)

            status, body = self.call("GET", "/v1/validation/runs", token=unattributed)
            self.assertEqual(status, 200, body)
            self.assertEqual({row["run_id"] for row in body["runs"]},
                             {RUN_ONE, RUN_THREE})
            self.assertEqual(body["total"], 2)

            # The ungranted-project run is absent from the granted list; a
            # `project` filter naming it cannot reach it either.
            status, body = self.call(
                "GET", f"/v1/validation/runs?project={other_project}", token=granted)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["runs"], [])

    def test_acl_with_no_grants_at_all_is_not_ready(self) -> None:
        self.build_bundle()
        with self.env(mode="acl"):
            token = self.key(["validation:read"], "no-grants")
            status, body = self.call("GET", "/v1/validation/runs", token=token)
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["code"], "memory_deployment_not_ready")

    def test_agent_keys_cannot_carry_validation_unattributed(self) -> None:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with self.assertRaises(errors.ForbiddenActorKind):
            auth.Auth(conn).issue_key(actor_id=self.agent, label="agent",
                                      scopes=["validation:read", "validation:unattributed"])
        issued = auth.Auth(conn).issue_key(actor_id=self.agent, label="agent-read",
                                           scopes=["validation:read"])
        self.assertIn("validation:read", issued["scopes"])


# -- criterion 6: served values match the source ------------------------------


class ServedValuesMatchTheSource(ValidationApiTestCase):
    def test_every_served_field_matches_the_record_and_the_files(self) -> None:
        run_dir = self.build_bundle(mode="real", projects=["prj_00000000000000000000000000"],
                                    sources={"recorder": "scripts/p0-acceptance.py@" + RECORDER_SHA})
        run_file = run_dir / f"{RUN_ONE}.json"
        manifest_file = run_dir / "manifest.json"
        record = json.loads(run_file.read_text())
        with self.env():
            token = self.key(["validation:read", "validation:unattributed"])
            status, row_body = self.call("GET", "/v1/validation/runs?limit=100", token=token)
            self.assertEqual(status, 200, row_body)
            row = next(entry for entry in row_body["runs"] if entry["run_id"] == RUN_ONE)
            for field in ("run_id", "stage", "created_at", "commit", "schema_version",
                          "core_version", "verdict"):
                self.assertEqual(row[field], record[field], field)
            self.assertEqual(row["record_sha256"], sha256_file(run_file))
            self.assertEqual(row["attribution"]["status"], "attributed")
            self.assertEqual(row["mode"], "real")

            status, detail = self.call("GET", f"/v1/validation/runs/{RUN_ONE}", token=token)
            self.assertEqual(status, 200, detail)
            served_case = detail["cases"][0]
            source_case = record["cases"][0]
            for field in ("case_id", "name", "verdict", "user_raw", "agent_response",
                          "interpretation", "reproduction", "not_applied_reason"):
                self.assertEqual(served_case[field], source_case[field], field)
            self.assertEqual(served_case["context"], source_case["context"])
            self.assertEqual(served_case["tools"], source_case["tools"])

            status, manifest = self.call("GET", f"/v1/validation/runs/{RUN_ONE}/manifest",
                                         token=token)
            self.assertEqual(status, 200, manifest)
            self.assertEqual(manifest["manifest_sha256"], sha256_file(manifest_file))
            declared = json.loads(manifest_file.read_text())
            served_files = {entry["artifact_id"]: entry for entry in manifest["files"]}
            for source_entry in declared["files"]:
                served = served_files[source_entry["artifact_id"]]
                self.assertEqual(served["sha256"], source_entry["sha256"])
                self.assertEqual(served["bytes"], source_entry["bytes"])
                self.assertEqual(served["integrity"], "match")

            status, meta = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_assign", token=token)
            self.assertEqual(status, 200, meta)
            declared_assign = next(entry for entry in declared["files"]
                                   if entry["artifact_id"] == "art_assign")
            self.assertEqual(meta["declared_sha256"], declared_assign["sha256"])
            self.assertEqual(meta["declared_bytes"], declared_assign["bytes"])
            self.assertEqual(meta["digests"]["original"], declared_assign["sha256"])
            self.assertNotEqual(meta["digests"]["masked"], meta["digests"]["original"])
            self.assertFalse(meta["content_scan"]["credential_shaped"])
            self.assertFalse(meta["content_scan"]["mask_is_noop"])

            status, meta_plain = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_plain", token=token)
            self.assertEqual(status, 200, meta_plain)
            self.assertTrue(meta_plain["content_scan"]["mask_is_noop"])
            self.assertEqual(meta_plain["digests"]["masked"], meta_plain["digests"]["original"])


# -- criterion 1: the labelled synthetic FAIL fixture -------------------------


class SyntheticFailFixture(ValidationApiTestCase):
    def test_labelled_synthetic_fail_path_end_to_end(self) -> None:
        run_dir = self.root / "p7-fail" / RUN_TWO
        self.write_run(run_dir, RUN_TWO, "labelled-fail-run.json")
        with self.env():
            token = self.key(["validation:read"])
            status, listing = self.call("GET", "/v1/validation/runs", token=token)
            self.assertEqual(status, 200, listing)
            row = listing["runs"][0]
            self.assertEqual(row["verdict"], "FAIL")
            self.assertTrue(row["synthetic"])
            self.assertEqual(row["synthetic_note"],
                             "NOT EVIDENCE: a labelled synthetic fixture; no real "
                             "P7-format FAIL run exists")
            # The run grade is the least trustworthy of the effective bundle mode
            # (unknown: there is no manifest) and every case grade, and an unknown
            # mode is never improved by a case grade. The case grade is simulated.
            self.assertEqual(row["evidence_grade"], "unknown")
            self.assertEqual(row["case_counts"]["fail"], 1)

            status, detail = self.call("GET", f"/v1/validation/runs/{RUN_TWO}", token=token)
            self.assertEqual(status, 200, detail)
            self.assertTrue(detail["synthetic"])
            self.assertEqual(detail["evidence_grade"], "unknown")
            case = detail["cases"][0]
            self.assertEqual(case["verdict"], "FAIL")
            self.assertEqual(case["failure_class"], "UI_FAILURE")
            self.assertEqual(case["failure_output"], "expected 200 got 500")
            self.assertEqual(case["evidence_grade"], "simulated")
            self.assertEqual(case["grade_basis"], "the run record marks itself synthetic")


# -- configuration, ids, caps and the mirrored walk ---------------------------


class ConfigurationAndGrammars(ValidationApiTestCase):
    def test_root_unset_is_503_validation_not_configured(self) -> None:
        with patch.dict(os.environ, {"JASMINE_MEMORY_DEPLOYMENT_MODE": "single_user"}):
            os.environ.pop("JASMINE_CORE_VALIDATION_RUNS", None)
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs", token=token)
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["code"], "validation_not_configured")
        self.assertEqual(body["error"]["details"]["reason"], "unset")

    def test_missing_and_symlinked_roots_are_refused(self) -> None:
        with self.env(self.root / "does-not-exist"):
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs", token=token)
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["details"]["reason"], "missing")

        real = self.root / "real-root"
        real.mkdir()
        link = self.root / "linked-root"
        os.symlink(real, link)
        with self.env(link):
            status, body = self.call("GET", "/v1/validation/runs", token=token)
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["details"]["reason"], "symlinked_root")

    def test_malformed_ids_and_unknown_query_keys_are_400(self) -> None:
        self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            for path in ("/v1/validation/runs/not-a-run-id",
                         f"/v1/validation/runs/{RUN_ONE}/artifacts/nope",
                         f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good/content?form=other",
                         f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good/content?download=0",
                         f"/v1/validation/runs?nonsense=1",
                         f"/v1/validation/runs?limit=abc",
                         f"/v1/validation/runs?limit=101",
                         f"/v1/validation/runs?offset=-1",
                         f"/v1/validation/runs?verdict=MAYBE",
                         f"/v1/validation/runs/{RUN_ONE}?extra=1"):
                status, body = self.call("GET", path, token=token)
                self.assertEqual(status, 400, (path, body))
                self.assertEqual(body["error"]["code"], "invalid_request", path)

    def test_unknown_run_id_is_404_validation_run_not_found(self) -> None:
        self.build_bundle()
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", "/v1/validation/runs/run-20990101T000000Z",
                                     token=token)
        self.assertEqual(status, 404, body)
        self.assertEqual(body["error"]["code"], "validation_run_not_found")

    def test_corrupt_record_is_409_and_duplicate_stem_is_ambiguous(self) -> None:
        corrupt_dir = self.root / "p7-corrupt" / RUN_FOUR
        self.write_corrupt(corrupt_dir, RUN_FOUR)
        duplicate_dir = self.root / "p7-dup" / RUN_ONE
        self.write_run(duplicate_dir, RUN_ONE)
        self.build_bundle(rel_dir="p7-ok")
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_FOUR}", token=token)
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "validation_run_corrupt")
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_ONE}", token=token)
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "validation_run_ambiguous")

    def test_corrupt_manifest_is_409_and_artifacts_are_unaddressable(self) -> None:
        run_dir = self.root / "p7-manifest" / RUN_ONE
        self.write_run(run_dir, RUN_ONE)
        self.write_manifest(run_dir, RUN_ONE, [], raw="{not json")
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_ONE}/manifest",
                                     token=token)
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"]["code"], "validation_manifest_corrupt")
            status, body = self.call(
                "GET", f"/v1/validation/runs/{RUN_ONE}/artifacts/art_good", token=token)
            self.assertEqual(status, 404, body)
            self.assertEqual(body["error"]["details"]["reason"], "manifest_corrupt")

    def test_absent_manifest_is_a_200_that_does_not_reconstruct_history(self) -> None:
        run_dir = self.root / "p7-absent" / RUN_TWO
        self.write_run(run_dir, RUN_TWO, "no-context-run.json")
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_TWO}/manifest",
                                     token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "absent")
        self.assertEqual(body["mode"], "unknown")
        self.assertEqual(body["mode_basis"], "no bundle manifest on disk")
        self.assertIn("not reconstructed", body["reason"])

    def test_declared_mode_real_without_provenance_is_unknown(self) -> None:
        run_dir = self.root / "p7-real" / RUN_ONE
        self.write_run(run_dir, RUN_ONE)
        self.write_manifest(run_dir, RUN_ONE, [], mode="real", projects=[],
                            sources={"recorder": {"path": "scripts/x.py"}})
        with self.env():
            token = self.key(["validation:read"])
            status, body = self.call("GET", f"/v1/validation/runs/{RUN_ONE}/manifest",
                                     token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["declared_mode"], "real")
        self.assertEqual(body["mode"], "unknown")
        self.assertEqual(body["mode_basis"],
                         "declared real without recorder provenance or project attribution")

    def test_mirrored_walk_agrees_with_fingerprint_hash_workspace_file(self) -> None:
        run_dir = self.build_bundle()
        from jasmine_core.validation import reads
        relative = "artifacts/good.txt"
        expected = fingerprint.hash_workspace_file(run_dir, relative)
        declared = hashlib.sha256((run_dir / relative).read_bytes()).hexdigest()
        read = reads._read_artifact(run_dir, relative, declared,
                                    (run_dir / relative).stat().st_size, reads.MAX_ARTIFACT_BYTES)
        self.assertEqual(read.integrity, "match")
        self.assertEqual((hashlib.sha256(read.buffer).hexdigest(), read.actual_bytes), expected)


# -- contract surface ---------------------------------------------------------


class ContractSurface(ValidationApiTestCase):
    def test_all_five_routes_are_registered_with_validation_read(self) -> None:
        from jasmine_core.api import handlers
        patterns = {route.pattern.pattern: route.scope for route in handlers.ROUTES
                    if "/v1/validation" in route.pattern.pattern}
        self.assertEqual(len(patterns), 5)
        self.assertEqual(set(patterns.values()), {"validation:read"})

    def test_redact_text_version_is_declared(self) -> None:
        self.assertEqual(canonical.REDACT_TEXT_VERSION, "1")
        self.assertIsInstance(canonical.REDACT_TEXT_VERSION, str)

    def test_frozen_contract_snapshot_matches_reads_py(self) -> None:
        result = subprocess.run([sys.executable, str(CONTRACT_SCRIPT), "--check"],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_contract_check_exits_two_when_the_ref_is_absent(self) -> None:
        # A drift guard that passes because it could not look is worse than no
        # guard, so an absent ref must be exit 2, never a silent 0.
        result = subprocess.run(
            [sys.executable, str(CONTRACT_SCRIPT), "--check",
             "--ref", "0000000000000000000000000000000000000000"],
            capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("absent", result.stderr)


if __name__ == "__main__":
    unittest.main()
