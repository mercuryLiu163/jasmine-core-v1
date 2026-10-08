#!/usr/bin/env python3.13
"""An **isolated real Core carrying the P7-01 Validation read slice** — the P6-03c environment.

This is the P7-01 counterpart of `scripts/p6/p4_realcore_env.py`; read that first, and read
`web/REAL-CORE-ACCEPTANCE-PLAN.md` §2 for the isolation promises this file keeps. It is not a
product feature and it is not P7 phase acceptance.

What is real here, and what is not:

* **Real Core, real slice.** Every route, scope check, id grammar, walk rule, manifest rule,
  masker and 404/409/503 decision comes from `src/jasmine_core` at the P7-01 branch tip
  (`00d604031861db6bd099a455192e5024ca0ad17f`), extracted with `git archive` from the sibling
  worktree `_worktrees/p7-01` into the directory given by `--out`. Nothing is monkey-patched.
  `src/jasmine_core/validation/reads.py` must be present or the build refuses.
* **Real records, read-only.** The Validation root for the main pass is the operator's own tree
  `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs`. It is opened
  read-only by every Core here and every real run record's mtime and digest is snapshotted
  before and after the run; a change fails the bundle. Nothing in this file writes to it. The
  tree's size is **derived**, never assumed: any non-empty tree of parseable `run-*.json` records
  builds, and the criterion-1 sentence in the banner and the descriptor is read from that tree's
  census rather than asserted.
* **Fixture negatives, clearly labelled.** The real records contain no manifest, no artifact,
  no FAIL case and no corrupt/duplicate record, so a second root inside the temp tree provides
  those. Every fixture record carries `synthetic: true` and a `synthetic_note` saying it is not
  evidence, and the API surfaces both.
* **Its own database, workspace, port and keys.** `core.db`, a workspace, loopback ports and
  keys minted in-process by the Core's own `auth.issue_key`, written only into the isolated
  tree at mode 600. It never reads the operator's database, hooks, global config or accounts.

Commands::

    # build the isolated Core, fixture root, keys and descriptor
    python3.13 scripts/p6/p7_realcore_env.py build --out /tmp/p6-p7-env

    # serve one of the four configurations the UI pass needs (separate processes)
    python3.13 scripts/p6/p7_realcore_env.py serve --out /tmp/p6-p7-env --port 8801 \
        --root real --mode single_user
    python3.13 scripts/p6/p7_realcore_env.py serve --out /tmp/p6-p7-env --port 8802 \
        --root fixture --mode single_user
    python3.13 scripts/p6/p7_realcore_env.py serve --out /tmp/p6-p7-env --port 8803 \
        --root unset --mode single_user
    python3.13 scripts/p6/p7_realcore_env.py serve --out /tmp/p6-p7-env --port 8804 \
        --root real --mode acl

    # prove the operator's real records are untouched
    python3.13 scripts/p6/p7_realcore_env.py verify-real --before before.json

The descriptor (`--out/descriptor.json`) carries the ids, the fixture expectations and the
mode labels; the tokens live only in `--out/tokens.json` (mode 600) and are printed nowhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

#: The P7-01 branch tip whose Validation read slice this environment runs. Pinned, never a branch.
DEFAULT_REF = "00d604031861db6bd099a455192e5024ca0ad17f"
REPO_ROOT = Path(__file__).resolve().parents[2]
#: The sibling worktree that carries P7-01 (this harness lives in the P6-01 worktree).
DEFAULT_P7_REPO = REPO_ROOT.parent / "p7-01"
#: The operator's real Validation records tree. **Read-only.** Never written by this harness.
DEFAULT_REAL_RUNS = REPO_ROOT.parent.parent / "jasmine-core-v1" / "validation-runs"

#: Scopes each key carries. `validation:read` is the slice's one scope; `validation:unattributed`
#: is required in `acl` mode for a run with no project attribution (every real record), and the
#: Core refuses to issue it to an agent key.
KEY_SCOPES: dict[str, list[str]] = {
    "read": ["objects:read", "events:read", "state:read", "validation:read"],
    "no_validation": ["objects:read", "events:read", "state:read"],
    "acl_unattributed": ["objects:read", "events:read", "state:read", "validation:read",
                         "validation:unattributed"],
}

#: The fixture records. Ids are the recorder's `run-YYYYmmddTHHMMSSZ` grammar; artifact ids are the
#: bundle's `art_…` grammar. They are deliberately in the far past so no fixture id can be mistaken
#: for one of the operator's real records, and every record says `synthetic: true`.
FIXTURE_RUN_BUNDLE = "run-20260101T000001Z"
FIXTURE_RUN_FAIL = "run-20260101T000002Z"
FIXTURE_RUN_CORRUPT = "run-20260101T000003Z"
FIXTURE_RUN_DUPLICATE = "run-20260101T000004Z"

FIXTURE_NOTE = ("P6-03c fixture record: synthetic, NOT evidence, and it counts toward no "
                "acceptance criterion; the real-FAIL-run path is unmet on the operator's tree")

ARTIFACTS = {
    "art_good": ("artifacts/good.txt", "text/plain",
                 "alpha\nbravo\ncharlie\n"),
    "art_secret": ("artifacts/secret.txt", "text/plain",
                   "service started\ntoken=x\ncache warm\n"),
    "art_html": ("artifacts/page.html", "text/html",
                 "<html><body><b>bold</b><script>window.__p6_xss = 1</script>"
                 "<img src=x onerror=\"window.__p6_xss = 2\"></body></html>\n"),
    "art_binary": ("artifacts/binary.bin", "application/octet-stream", None),
}
BINARY_BYTES = bytes(range(256))


def log(message: str) -> None:
    print(f"p7-realcore: {message}", file=sys.stderr, flush=True)


def banner(ref: str, out: Path, real_runs: Path, finding: dict[str, Any]) -> None:
    print("=" * 78, file=sys.stderr)
    print("ISOLATED REAL CORE + P7-01 VALIDATION READ SLICE. NOT a P7 Gate verdict, and P7-01",
          file=sys.stderr)
    # Derived from the same census the driver records, so a tree that carries a real FAIL case is
    # never described as having none.
    print(f"{criterion1_sentence(finding)}.", file=sys.stderr)
    print(f"  Core from commit {ref} (git archive), extracted into {out / 'p7'}", file=sys.stderr)
    print(f"  Real Validation root (READ-ONLY): {real_runs}", file=sys.stderr)
    print("  Fixture root (synthetic, NOT evidence) inside the same temp tree.", file=sys.stderr)
    print("  Nothing here touches the operator's Core, database, hooks, config or accounts.",
          file=sys.stderr)
    print("=" * 78, file=sys.stderr)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_real_tree(root: Path) -> dict[str, dict[str, Any]]:
    """Every real run record's mtime and digest, so a write anywhere shows up.

    Only run-shaped files are snapshotted: they are the operator's evidence, and the reader is
    asserted never to write them. Directory mtimes are deliberately not included — a read does not
    change them, but an unrelated process (Spotlight, a backup) might touch the parent, and that
    would be reported as this harness's fault.
    """
    snapshot: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("run-*.json")):
        if not path.is_file():
            continue
        stat = path.stat()
        snapshot[path.relative_to(root).as_posix()] = {
            "mtime_ns": stat.st_mtime_ns,
            "bytes": stat.st_size,
            "sha256": sha256_file(path),
        }
    return snapshot


def compare_real_tree(before: dict[str, dict[str, Any]],
                      after: dict[str, dict[str, Any]]) -> dict[str, Any]:
    changed = sorted(path for path in set(before) | set(after)
                     if before.get(path) != after.get(path))
    return {"unchanged": not changed, "changed": changed, "records": len(before)}


def read_real_records(real_runs: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every `run-*.json` under `real_runs`, parsed — or a refusal.

    The gate is about the tree being **usable**, not a fixed size: any non-empty tree of parseable
    run records builds, and the `resolve` walk downstream reads run_id-keyed data. A record that
    is not JSON, is not an object, or carries no `run_id` is refused here with the path, so a
    broken tree fails before a Core starts rather than halfway through the browser pass.
    """
    paths = sorted(path for path in real_runs.rglob("run-*.json") if path.is_file())
    if not paths:
        raise SystemExit(f"{real_runs} holds no run-*.json records; the harness needs at least "
                         f"one usable real record")
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"unusable real run record {path}: {error}") from error
        if not isinstance(data, dict) or not isinstance(data.get("run_id"), str) or not data["run_id"]:
            raise SystemExit(f"unusable real run record {path}: it carries no run_id")
        records.append((path, data))
    return records


def criterion1_finding(real_runs: Path) -> dict[str, Any]:
    """Criterion 1's census, derived from the tree — the one predicate for the whole harness.

    `p7_realcore_ui.py`'s `criterion1_census()` is this function plus the root label, so the
    banner, `descriptor.json`'s `mode.note` and the bundle's census cannot disagree about whether
    a real FAIL record exists. Every count comes from the files; nothing here assumes a size.
    """
    census: dict[str, Any] = {
        "records": 0, "run_verdicts": {"PASS": 0, "BLOCKED": 0, "FAIL": 0},
        "cases": 0, "case_verdicts": {"PASS": 0, "BLOCKED": 0, "FAIL": 0},
        "fail_cases": 0, "failure_class_non_null": 0, "failure_output_non_null": 0,
        "files": [],
    }
    for path, data in read_real_records(real_runs):
        census["records"] += 1
        run_verdict = data.get("verdict", "FAIL")
        census["run_verdicts"][run_verdict] = census["run_verdicts"].get(run_verdict, 0) + 1
        census["files"].append({"path": path.relative_to(real_runs).as_posix(),
                                "run_id": data.get("run_id"), "verdict": data.get("verdict")})
        for case in data.get("cases") or []:
            census["cases"] += 1
            verdict = case.get("verdict")
            census["case_verdicts"][verdict] = census["case_verdicts"].get(verdict, 0) + 1
            if verdict == "FAIL":
                census["fail_cases"] += 1
            if case.get("failure_class") not in (None, ""):
                census["failure_class_non_null"] += 1
            if case.get("failure_output") not in (None, ""):
                census["failure_output_non_null"] += 1
    census["criterion_1"] = (
        "BLOCKED" if census["fail_cases"] == 0 and census["failure_class_non_null"] == 0
        and census["failure_output_non_null"] == 0 else "demonstrated")
    return census


def criterion1_sentence(finding: dict[str, Any]) -> str:
    """The one derived sentence the banner and the descriptor note both print."""
    if finding["criterion_1"] == "BLOCKED":
        # The predicate keys on FAIL cases and classified failures, not only on a FAIL run
        # verdict, so the sentence says what was actually looked for.
        return ("criterion 1 (a real FAIL run) is BLOCKED: no FAIL case, failure_class or "
                "failure_output exists on this tree")
    return ("criterion 1 (a real FAIL run) is demonstrated: the census reads "
            f"{finding['fail_cases']} FAIL case(s), "
            f"{finding['failure_class_non_null']} non-null failure_class and "
            f"{finding['failure_output_non_null']} non-null failure_output on this tree")


def extract_core(ref: str, destination: Path, repo: Path) -> None:
    """Extract `ref` from `repo` (the P7-01 worktree) with `git archive`."""
    if not repo.is_dir():
        raise SystemExit(f"--repo {repo} is not a directory")
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(repo), "archive", ref],
                             check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)
    reads = destination / "src" / "jasmine_core" / "validation" / "reads.py"
    if not reads.is_file():
        raise SystemExit(f"{ref} has no src/jasmine_core/validation/reads.py — wrong commit?")
    log(f"extracted {ref} from {repo} into {destination} (reads.py present)")


def _run_record(run_id: str, *, verdict: str, created_at: str, cases: list[dict[str, Any]],
                extra: dict[str, Any] | None = None) -> str:
    data: dict[str, Any] = {
        "run_id": run_id,
        "stage": "P6-03c-fixture",
        "created_at": created_at,
        "commit": "fixture-no-commit",
        "schema_version": 1,
        "core_version": "fixture",
        "environment": {"note": FIXTURE_NOTE},
        "executor": "p6-03c-fixture",
        "reviewer": "p6-03c-fixture",
        "verifier": "p6-03c-fixture",
        "verdict": verdict,
        "synthetic": True,
        "synthetic_note": FIXTURE_NOTE,
        "notes": [FIXTURE_NOTE],
        "not_implemented": [],
        "cases": cases,
    }
    if extra:
        data.update(extra)
    return json.dumps(data, indent=2) + "\n"


def _fixture_case(case_id: str, name: str, verdict: str, **over: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "case_id": case_id,
        "name": name,
        "verdict": verdict,
        "user_raw": "fixture user line\n",
        "agent_response": "fixture agent reply\n",
        "tools": [{"name": "fixture.read", "response": {"ok": True}}],
        "state_before": {"fixture": True},
        "state_after": {"fixture": True},
        "interpretation": "N/A (P0 not implemented)",
        "context": {"reason": "the fixture records no context pack"},
        "evidence": ["P6-03c fixture only: NOT evidence"],
        "reproduction": ["python3.13 scripts/p6/p7_realcore_env.py build --out <tmp>"],
        "failure_class": None,
        "failure_output": None,
    }
    case.update(over)
    return case


def build_fixture_root(destination: Path, canonical_module: Any) -> dict[str, Any]:
    """The negatives the operator's real records cannot provide, all clearly synthetic.

    Built as a directory tree the real reader walks:
      bundle/            a frozen manifest + four artifacts (good text, assignment-shaped secret,
                         HTML, binary)
      synthetic-fail/    a run that marks itself synthetic with verdict FAIL and a failure_class
      corrupt/           a run-shaped file whose run_id does not match its stem
      dup-a/ + dup-b/    two parseable run-shaped files sharing one stem
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    # --- the manifest bundle -----------------------------------------------------------------
    bundle = destination / "bundle"
    (bundle / "artifacts").mkdir(parents=True)
    files = []
    expectations: dict[str, Any] = {"artifacts": {}, "manifest": {}}
    for artifact_id, (relative, media_type, text) in ARTIFACTS.items():
        path = bundle / relative
        if text is None:
            raw = BINARY_BYTES
        else:
            raw = text.encode("utf-8")
        path.write_bytes(raw)
        files.append({
            "artifact_id": artifact_id,
            "path": relative,
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "media_type": media_type,
            "credential_shaped": False,
        })
        entry: dict[str, Any] = {
            "media_type": media_type,
            "original_sha256": sha256_bytes(raw),
            "original_bytes": len(raw),
        }
        if text is not None:
            masked = canonical_module.redact_text(text)
            entry["redact_text_is_noop"] = masked == text
            entry["masked_text"] = masked
            entry["masked_sha256"] = sha256_bytes(masked.encode("utf-8"))
            entry["looks_like_credential"] = canonical_module.looks_like_credential(text)
        expectations["artifacts"][artifact_id] = entry
    expectations["redact_text_version"] = canonical_module.REDACT_TEXT_VERSION
    manifest = {
        "schema": "jasmine.validation-bundle.v1",
        "run_id": FIXTURE_RUN_BUNDLE,
        "frozen_at": "2026-01-01T00:00:01Z",
        "mode": "simulated",
        "projects": [],
        "sources": {"note": FIXTURE_NOTE},
        "files": files,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    expectations["manifest"]["sha256"] = sha256_file(bundle / "manifest.json")
    expectations["manifest"]["files"] = [entry["artifact_id"] for entry in files]
    (bundle / f"{FIXTURE_RUN_BUNDLE}.json").write_text(_run_record(
        FIXTURE_RUN_BUNDLE, verdict="PASS", created_at="2026-01-01T00:00:01Z",
        cases=[_fixture_case("case-bundle-1", "fixture bundle run", "PASS")]), encoding="utf-8")

    # --- the synthetic FAIL run --------------------------------------------------------------
    failure = destination / "synthetic-fail"
    failure.mkdir()
    (failure / f"{FIXTURE_RUN_FAIL}.json").write_text(_run_record(
        FIXTURE_RUN_FAIL, verdict="FAIL", created_at="2026-01-01T00:00:02Z",
        cases=[_fixture_case(
            "case-fail-1", "fixture failure path", "FAIL",
            failure_class="ASSERTION_FAILED",
            failure_output="expected 3, observed 2\n",
            evidence=[])], ), encoding="utf-8")

    # --- the corrupt record (parseable JSON, wrong run_id, still labelled) --------------------
    corrupt = destination / "corrupt"
    corrupt.mkdir()
    (corrupt / f"{FIXTURE_RUN_CORRUPT}.json").write_text(_run_record(
        "run-20260101T000099Z", verdict="BLOCKED", created_at="2026-01-01T00:00:03Z",
        cases=[_fixture_case("case-corrupt-1", "fixture corrupt record", "BLOCKED")]),
        encoding="utf-8")

    # --- the duplicate stem (two parseable files, one stem, in different directories) ---------
    for directory in ("dup-a", "dup-b"):
        target = destination / directory
        target.mkdir()
        (target / f"{FIXTURE_RUN_DUPLICATE}.json").write_text(_run_record(
            FIXTURE_RUN_DUPLICATE, verdict="PASS", created_at="2026-01-01T00:00:04Z",
            cases=[_fixture_case("case-dup-1", f"fixture duplicate in {directory}", "PASS")]),
            encoding="utf-8")

    return {
        "root": str(destination),
        "note": FIXTURE_NOTE,
        "runs": {
            "bundle": FIXTURE_RUN_BUNDLE,
            "synthetic_fail": FIXTURE_RUN_FAIL,
            "corrupt": FIXTURE_RUN_CORRUPT,
            "duplicate": FIXTURE_RUN_DUPLICATE,
        },
        "expectations": expectations,
    }


def _new_id(prefix: str) -> str:
    from jasmine_core.ids import new_id

    return new_id(prefix)


def build(args: argparse.Namespace, census_finding: dict[str, Any] | None = None) -> dict[str, Any]:
    out: Path = args.out
    if out.exists() and any(out.iterdir()) and not args.reuse:
        raise SystemExit(f"{out} is not empty; pass --reuse to build in place")
    out.mkdir(parents=True, exist_ok=True)
    p7 = out / "p7"
    if not (p7 / "src" / "jasmine_core" / "validation" / "reads.py").is_file():
        extract_core(args.ref, p7, args.repo)
    real_runs: Path = args.real_runs
    if not real_runs.is_dir():
        raise SystemExit(f"--real-runs {real_runs} is not a directory")
    snapshot_before = snapshot_real_tree(real_runs)
    # Usability gate, not a size gate: any non-empty tree of parseable run records builds. The
    # finding is the same census the driver records, so the descriptor note below cannot claim a
    # real FAIL record does not exist when one is present.
    finding = census_finding if census_finding is not None else criterion1_finding(real_runs)

    sys.path.insert(0, str(p7 / "src"))
    from jasmine_core import SCHEMA_VERSION, canonical, db, registry
    from jasmine_core.migrations import migrate
    from jasmine_core.models import NewObject
    from jasmine_core.objects import ObjectStore

    fixture = build_fixture_root(out / "fixture-runs", canonical)
    fixture_root = Path(fixture["root"])

    workspace = out / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "README.fixture.txt").write_text(
        "P6-03c isolated workspace: not the operator's workspace.\n", encoding="utf-8")
    db_path = out / "core.db"
    os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(workspace)
    os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = "single_user"

    conn = db.connect(db_path)
    migrate(conn, log=lambda line: log(line))

    host_id = _new_id("hst")
    human_id = _new_id("act")
    agent_id = _new_id("act")
    with db.transaction(conn):
        reg = registry.Registry(conn)
        reg.upsert_host(host_id, display_name="p6-03c host")
        reg.upsert_actor(human_id, kind="human", display_name="p6-03c operator",
                         home_host_id=host_id)
        reg.upsert_actor(agent_id, kind="agent", display_name="p6-03c agent",
                         home_host_id=host_id)

    # A real project and a real grant, created through the Core's own object store, so the `acl`
    # pass has a non-empty memory_project_actors table (an empty one is refused with 503 by the
    # frozen P4 mechanism the slice reuses) and `ProjectAccess.project_exists` has a row to see.
    created = ObjectStore(conn, schema_version=SCHEMA_VERSION).create(
        NewObject.project({"name": "p6-03c acl grant", "host_id": host_id}), actor_id=human_id)
    project_id = created["object"]["project_id"]
    source_event_id = conn.execute(
        "SELECT source_event_id FROM projects WHERE project_id=?", (project_id,)).fetchone()[0]
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO memory_project_actors (project_id, actor_id, granted_by_event_id,"
            " created_at) VALUES (?, ?, ?, ?)",
            (project_id, human_id, source_event_id, "2026-01-01T00:00:00Z"))

    # Keys, through the Core's own issuance rules.
    from jasmine_core import auth

    keys = auth.Auth(conn)
    issued: dict[str, dict[str, Any]] = {}
    for name, scopes in KEY_SCOPES.items():
        record = keys.issue_key(actor_id=human_id, label=f"p6-03c {name}", scopes=list(scopes))
        issued[name] = {"token": record["token"], "key_id": record["key_id"],
                        "actor_id": human_id, "scopes": record["scopes"]}
    issuance_refusals: list[dict[str, Any]] = []
    try:
        keys.issue_key(actor_id=agent_id, label="p6-03c agent unattributed",
                       scopes=["objects:read", "validation:read", "validation:unattributed"])
        issuance_refusals.append({"scope": "validation:unattributed", "actor_kind": "agent",
                                  "issued": True})
    except Exception as error:  # noqa: BLE001 - the refusal is the observation
        issuance_refusals.append({
            "scope": "validation:unattributed", "actor_kind": "agent", "issued": False,
            "code": getattr(error, "code", error.__class__.__name__),
            "status": getattr(error, "status", None)})

    snapshot_after_build = snapshot_real_tree(real_runs)
    comparison = compare_real_tree(snapshot_before, snapshot_after_build)
    if not comparison["unchanged"]:
        raise SystemExit(f"the build wrote to the operator's tree: {comparison['changed']}")

    descriptor: dict[str, Any] = {
        "environment": "isolated real P7-01 Core (Validation read slice)",
        "mode": {
            "core": "real-p7-01",
            "validation_root": "operator tree (read-only) for the main pass; a labelled synthetic "
                               "fixture root for the negatives the real records cannot provide",
            "note": "the slice is read-only; no Validation route writes anything. "
                    + criterion1_sentence(finding) + ".",
        },
        "ref": args.ref,
        "repo": str(args.repo),
        "schema_version": SCHEMA_VERSION,
        "core_dir": str(p7),
        "out": str(out),
        "db": str(db_path),
        "workspace": str(workspace),
        "real_runs_root": str(real_runs),
        "fixture_root": str(fixture_root),
        "fixture": fixture,
        "criterion_1": {"state": finding["criterion_1"], "records": finding["records"],
                        "run_verdicts": finding["run_verdicts"],
                        "fail_cases": finding["fail_cases"],
                        "failure_class_non_null": finding["failure_class_non_null"],
                        "failure_output_non_null": finding["failure_output_non_null"]},
        "host_id": host_id,
        "actors": {"human": human_id, "agent": agent_id},
        "project_id": project_id,
        "grant_event_id": source_event_id,
        "keys": {name: {"key_id": record["key_id"], "actor_id": record["actor_id"],
                        "scopes": record["scopes"]} for name, record in issued.items()},
        "issuance_refusals": issuance_refusals,
        "real_tree_before": snapshot_before,
        "real_tree_build_comparison": comparison,
    }
    (out / "tokens.json").write_text(
        json.dumps({name: record["token"] for name, record in issued.items()}, indent=2),
        encoding="utf-8")
    os.chmod(out / "tokens.json", 0o600)
    (out / "descriptor.json").write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    os.chmod(out / "descriptor.json", 0o600)
    return descriptor


def serve(args: argparse.Namespace) -> int:
    out: Path = args.out
    descriptor = json.loads((out / "descriptor.json").read_text(encoding="utf-8"))
    p7 = Path(descriptor["core_dir"])
    if not (p7 / "src" / "jasmine_core").is_dir():
        raise SystemExit(f"{p7} has no src/jasmine_core — rebuild {out}")
    sys.path.insert(0, str(p7 / "src"))
    os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = descriptor["workspace"]
    os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = args.mode
    if args.root == "real":
        os.environ["JASMINE_CORE_VALIDATION_RUNS"] = descriptor["real_runs_root"]
    elif args.root == "fixture":
        os.environ["JASMINE_CORE_VALIDATION_RUNS"] = descriptor["fixture_root"]
    else:
        os.environ.pop("JASMINE_CORE_VALIDATION_RUNS", None)
    configured = os.environ.get("JASMINE_CORE_VALIDATION_RUNS", "(unset)")
    log(f"serving Core on 127.0.0.1:{args.port} root={args.root} ({configured}) mode={args.mode}")
    from jasmine_core.api.server import serve as serve_core

    try:
        serve_core(descriptor["db"], host="127.0.0.1", port=args.port, log=lambda line: log(line))
    except KeyboardInterrupt:
        pass
    return 0


def verify_real(args: argparse.Namespace) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = snapshot_real_tree(args.real_runs)
    comparison = compare_real_tree(before, after)
    report = {"real_runs_root": str(args.real_runs), "before": len(before), "after": len(after),
              **comparison}
    print(json.dumps(report, indent=2))
    return 0 if comparison["unchanged"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="create the isolated Core, fixture root and keys")
    build_parser.add_argument("--out", type=Path, required=True)
    build_parser.add_argument("--ref", default=DEFAULT_REF, help="the pinned P7-01 branch tip")
    build_parser.add_argument("--repo", type=Path, default=DEFAULT_P7_REPO,
                              help="the P7-01 worktree to extract --ref from")
    build_parser.add_argument("--real-runs", type=Path, default=DEFAULT_REAL_RUNS,
                              help="the operator's Validation records tree (read-only)")
    build_parser.add_argument("--reuse", action="store_true",
                              help="build inside a non-empty --out")

    serve_parser = sub.add_parser("serve", help="serve one Core configuration (foreground)")
    serve_parser.add_argument("--out", type=Path, required=True)
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--root", choices=("real", "fixture", "unset"), required=True)
    serve_parser.add_argument("--mode", choices=("single_user", "acl"), default="single_user")

    verify_parser = sub.add_parser("verify-real", help="prove the operator's tree is unchanged")
    verify_parser.add_argument("--before", type=Path, required=True)
    verify_parser.add_argument("--real-runs", type=Path, default=DEFAULT_REAL_RUNS)

    args = parser.parse_args(argv)
    if args.command == "build":
        # Read the census once, for the banner and the descriptor that must agree with it; the
        # same finding is handed to `build` so the tree is not parsed twice.
        finding = criterion1_finding(args.real_runs)
        banner(args.ref, args.out, args.real_runs, finding)
        descriptor = build(args, finding)
        log(f"schema {descriptor['schema_version']}; real root {descriptor['real_runs_root']}; "
            f"fixture root {descriptor['fixture_root']}")
        log(f"keys: {', '.join(descriptor['keys'])}")
        for entry in descriptor["issuance_refusals"]:
            log(f"issuance refusal: {entry['scope']} for {entry['actor_kind']}: "
                f"issued={entry['issued']} code={entry.get('code')}")
        return 0
    if args.command == "serve":
        return serve(args)
    if args.command == "verify-real":
        return verify_real(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
