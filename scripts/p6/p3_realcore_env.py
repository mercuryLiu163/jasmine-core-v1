#!/usr/bin/env python3.13
"""An **isolated, real P3 Core** with real continuity rows — the P6-03a acceptance environment.

The P6-03a slice (Checkpoint / Context Pack / Resume, read-only) has only ever been exercised
against the labelled simulated fixture `scripts/p6/p3_read_fixture.py`. This starts the *real* P3
code from the pinned commit and lets P3's own stores create the rows:

* **Real P3 Core.** `git archive <ref>` into the directory given by `--out`, its own `core.db`,
  workspace and loopback port. Truth is created through P3's `ObjectStore`/`EventStore`, and the
  continuity rows through P3's `ContinuityStore` and `ContextBuilder` — nothing is inserted behind
  the Core's back.
* **No engine.** P3's context builder reaches Memory through `MemoryProvider.from_deployment()`,
  which is the off slot when no deployment is configured; that is P3's own documented "absent"
  state, not a stand-in.
* **A labelled component stand-in for the workspace scan.** `ContinuityStore` takes a scanner; the
  real one walks a workspace and fingerprints it. This environment passes a component scan that
  returns a fixed partial fingerprint, exactly as P3's own continuity tests do, and says so in the
  descriptor: workspace snapshots in these rows are **not** a real workspace capture.
* **Isolation.** Own directory, database, workspace and port; loopback only; keys minted in-process
  by P3's own `auth.issue_key` and written only into the isolated tree at mode 600. It never reads
  the operator's database, hooks, global config or accounts.

Commands:

    python3.13 scripts/p6/p3_realcore_env.py build --out /tmp/p6-p3 --port 8792
    python3.13 scripts/p6/p3_realcore_env.py build --out /tmp/p6-p3 --seed-only
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_REF = "7016dabb8926d6bd6599f1f56b8f39cbda253ebf"  # P3 tip, the commit the contract records
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Scopes per key. `full` can do everything P3's continuity surface needs; `limited` lacks the
#: route scopes so Core refuses it; `route_scope` carries the *route table's* scope for the context
#: read but not the `checkpoint:read` its handler also requires — the distinction the frozen
#: contract records as `extra_scopes`.
CONTINUITY_SCOPES = ["objects:read", "state:read", "authority:read", "evidence:read", "events:read"]
KEY_SCOPES = {
    "full": CONTINUITY_SCOPES + ["checkpoint:read", "checkpoint:write", "context:read",
                                 "context:build", "resume:read", "resume:build"],
    "limited": CONTINUITY_SCOPES + ["checkpoint:read", "checkpoint:write", "context:build",
                                    "resume:build"],
    "route_scope": CONTINUITY_SCOPES + ["context:read"],
}


def log(message: str) -> None:
    print(f"p3-realcore: {message}", file=sys.stderr, flush=True)


def banner(ref: str, out: Path) -> None:
    print("=" * 78, file=sys.stderr)
    print("ISOLATED REAL P3 CORE — real P3 code, real continuity rows. NOT P3 phase acceptance.",
          file=sys.stderr)
    print(f"  Core from commit {ref} (git archive), extracted into {out / 'p3'}", file=sys.stderr)
    print("  Checkpoints, Context Packs and Resumes are created by P3's own ContinuityStore and",
          file=sys.stderr)
    print("  ContextBuilder; the workspace scanner is a labelled component stand-in.",
          file=sys.stderr)
    print("  Nothing here touches the operator's Core, database, hooks, config or accounts.",
          file=sys.stderr)
    print("=" * 78, file=sys.stderr)


def extract_core(ref: str, destination: Path, repo: Path | None = None) -> None:
    """Extract `ref` from `repo` (default: this worktree's repository).

    `--repo` exists so an acceptance run can be rehearsed against a **synthetic merge** that lives
    only in a throwaway clone: the Core still comes from `git archive` of a commit, just not from
    the repository the harness is checked out in.
    """
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(repo or REPO_ROOT), "archive", ref],
                             check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)
    if not (destination / "src" / "jasmine_core" / "continuity.py").is_file():
        raise SystemExit(f"{ref} has no src/jasmine_core/continuity.py — wrong commit?")


def build(args: argparse.Namespace) -> dict[str, Any]:
    out: Path = args.out
    if out.exists() and any(out.iterdir()) and not args.reuse:
        raise SystemExit(f"{out} is not empty; pass --reuse to build in place")
    out.mkdir(parents=True, exist_ok=True)
    p3 = out / "p3"
    if not (p3 / "src" / "jasmine_core").is_dir():
        extract_core(args.ref, p3, args.repo)
    workspace = out / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "source.txt").write_text("initial\n", encoding="utf-8")
    db_path = out / "core.db"

    sys.path.insert(0, str(p3 / "src"))
    os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(workspace)
    os.environ.pop("JASMINE_CORE_MEMORY_CONFIG", None)

    from jasmine_core import SCHEMA_VERSION, auth, clock, db, ids, registry
    from jasmine_core.context.builder import ContextBuilder
    from jasmine_core.context.memory_provider import MemoryProvider
    from jasmine_core.continuity import ContinuityStore
    from jasmine_core.events import EventStore
    from jasmine_core.migrations import migrate
    from jasmine_core.models import NewEvent, NewObject
    from jasmine_core.objects import ObjectStore

    conn = db.connect(db_path)
    migrate(conn, log=lambda line: log(line))

    host_id = ids.new_id("hst")
    human_id = ids.new_id("act")
    system_id = ids.new_id("act")
    agent_id = ids.new_id("act")
    with db.transaction(conn):
        reg = registry.Registry(conn)
        reg.upsert_host(host_id, display_name="p6 p3 real-core host")
        reg.upsert_actor(human_id, kind="human", display_name="p6 operator", home_host_id=host_id)
        reg.upsert_actor(system_id, kind="system", display_name="p6 worker", home_host_id=host_id)
        reg.upsert_actor(agent_id, kind="agent", display_name="p6 agent", home_host_id=host_id)

    issued: dict[str, dict[str, Any]] = {}
    keys = auth.Auth(conn)
    for name, scopes in KEY_SCOPES.items():
        record = keys.issue_key(actor_id=human_id, label=f"p6 {name}", scopes=list(scopes))
        issued[name] = {"token": record["token"],
                        "key_id": record.get("key_id") or record.get("id"),
                        "actor_id": human_id, "scopes": scopes}

    objects = ObjectStore(conn, schema_version=SCHEMA_VERSION)
    events = EventStore(conn, schema_version=SCHEMA_VERSION)

    def project(name: str) -> str:
        return objects.create(NewObject.project({"name": name, "host_id": host_id}),
                              actor_id=human_id)["object"]["project_id"]

    def task(project_id: str, title: str) -> str:
        return objects.create(NewObject.task({"project_id": project_id, "title": title,
                                              "host_id": host_id}),
                              actor_id=human_id)["object"]["task_id"]

    def prompt(project_id: str, task_id: str, text: str) -> str:
        stored, _ = events.append(NewEvent(
            event_type="user.prompt", source_system="p6-realcore", source_event_id=None,
            occurred_at=clock.now(), actor_id=human_id, actor_kind="human", host_id=host_id,
            project_id=project_id, task_id=task_id, payload={"text": text}))
        return stored["event_id"]

    # The workspace scan is a component stand-in, and the descriptor says so. P3's own continuity
    # tests use exactly this shape.
    scan_calls = {"count": 0}

    def component_scan(budget: Any, *, conn: Any) -> dict[str, Any]:
        assert not conn.in_transaction, "the scanner must not run inside a transaction"
        budget.remaining()
        scan_calls["count"] += 1
        return {"snapshot": {"algorithm": "component-stand-in", "complete": False,
                             "partial_reasons": ["component"], "root": "component",
                             "manifest_sha256": "0" * 64, "git_head": None},
                "sample_window": {"started_at_unix": 1, "finished_at_unix": 2}}

    continuity = ContinuityStore(conn, schema_version=SCHEMA_VERSION, scanner=component_scan)
    builder = ContextBuilder(conn, schema_version=SCHEMA_VERSION,
                             memory=MemoryProvider.from_deployment())

    project_id = project("p6 p3 realcore")
    task_id = task(project_id, "with continuity")
    event_id = prompt(project_id, task_id, "checkpoint this work")
    # A real note on the checkpoint, so the resume's real `note` (P3 builds it as
    # `{'value': cp['note'], 'authority': False, 'historical': True}`) is exercised rather than
    # absent: the first run had no note, and the browser record that reads it could not run.
    checkpoint = continuity.create({"task_id": task_id, "host_id": host_id,
                                    "source_event_id": event_id, "reason": "HANDOFF",
                                    "idempotency_key": "p6-realcore-checkpoint",
                                    "note": {"text": "paused here for the acceptance run",
                                             "source_event_id": event_id}},
                                   actor_id=human_id)["checkpoint"]
    resume = continuity.resume(task_id, {"host_id": host_id, "source_event_id": event_id,
                                         "idempotency_key": "p6-realcore-resume",
                                         "checkpoint_id": checkpoint["checkpoint_id"]},
                               actor_id=human_id)["resume"]
    # The Context Pack needs P3's pinned tokenizer: `tiktoken` plus a BPE asset whose SHA-256
    # `tokens.py` fixes. Neither is present here and installing one is out of scope, so the pack is
    # attempted and, if the tokenizer refuses, recorded as BLOCKED with the reason instead of being
    # fabricated. The checkpoint and resume above do not depend on it.
    context: dict[str, Any] | None = None
    context_blocked: dict[str, Any] | None = None
    try:
        context = builder.build({"task_id": task_id, "host_id": host_id,
                                 "source_event_id": event_id, "reason": "HANDOFF",
                                 "idempotency_key": "p6-realcore-context"},
                                actor_id=human_id)["context"]
    except Exception as error:  # noqa: BLE001 - the refusal is the observation
        context_blocked = {"status": "BLOCKED", "error_type": error.__class__.__name__,
                           "reason": str(error)[:200],
                           "note": "P3's ContextBuilder requires the pinned tokenizer "
                                   "(tiktoken + a fixed BPE asset SHA-256); this environment has "
                                   "neither, so no context pack exists and nothing was fabricated"}
    # A second checkpoint on the same task, so the "switching scope leaves nothing behind" record
    # has two real rows to switch between instead of the fixture's second id.
    checkpoint2 = continuity.create({"task_id": task_id, "host_id": host_id,
                                     # `MANUAL`, not `STEP_VERIFIED`: P3 checks the reason against
                                     # the source event's own nature, and a `user.prompt` is not a
                                     # verified State transition. P3's own continuity tests use MANUAL
                                     # with a prompt for exactly this reason.
                                     "source_event_id": event_id, "reason": "MANUAL",
                                     "idempotency_key": "p6-realcore-checkpoint-2",
                                     "note": {"text": "second checkpoint for the swap check",
                                              "source_event_id": event_id}},
                                    actor_id=human_id)["checkpoint"]
    empty_project = project("p6 p3 realcore empty")
    empty_task = task(empty_project, "without continuity")

    descriptor = {
        "environment": "isolated real P3 Core",
        "mode": {
            "core": "real-p3",
            "workspace_scan": "component-stand-in",
            "workspace_scan_note": "ContinuityStore's scanner returns a fixed partial fingerprint, "
                                   "as P3's own continuity tests do; workspace snapshots in these "
                                   "rows are not a real workspace capture",
        },
        "ref": args.ref,
        "schema_version": SCHEMA_VERSION,
        "out": str(out),
        "db": str(db_path),
        "core_origin": f"http://127.0.0.1:{args.port}",
        "host_id": host_id,
        "actors": {"human": human_id, "system": system_id, "agent": agent_id},
        "keys": {name: {"key_id": record["key_id"], "scopes": record["scopes"]}
                 for name, record in issued.items()},
        "seeded": {"project_id": project_id, "task_id": task_id, "source_event_id": event_id,
                   "checkpoint_id": checkpoint["checkpoint_id"],
                   "checkpoint_revision": checkpoint["task_revision"],
                   "checkpoint_digests": {
                       "projection": checkpoint["projection_digest"],
                       "context": checkpoint["full_context_digest"],
                       "workspace": checkpoint["workspace_digest"],
                   },
                   "checkpoint_note": checkpoint["note"]["text"],
                   "checkpoint_2_id": checkpoint2["checkpoint_id"],
                   "checkpoint_2_revision": checkpoint2["task_revision"],
                   "checkpoint_2_note": checkpoint2["note"]["text"],
                   "context_id": context["context_id"] if context else None,
                   "resume_id": resume["resume_id"],
                   "empty_project_id": empty_project, "empty_task_id": empty_task,
                   "unknown_checkpoint_id": ids.new_id("ckp")},
        "scan_calls": scan_calls["count"],
        "context_pack": context_blocked or {"status": "built",
                                            "context_id": context["context_id"]},
    }
    (out / "tokens.json").write_text(
        json.dumps({name: record["token"] for name, record in issued.items()}, indent=2),
        encoding="utf-8")
    os.chmod(out / "tokens.json", 0o600)
    (out / "descriptor.json").write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    os.chmod(out / "descriptor.json", 0o600)
    conn.close()
    return descriptor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--repo", type=Path, default=None,
                        help="repository to extract --ref from (default: this worktree)")
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args(argv)
    banner(args.ref, args.out)
    descriptor = build(args)
    log(f"schema {descriptor['schema_version']}; checkpoint={descriptor['seeded']['checkpoint_id']} "
        f"resume={descriptor['seeded']['resume_id']} (workspace scanner calls: "
        f"{descriptor['scan_calls']})")
    context = descriptor["context_pack"]
    log(f"context pack: {context['status']}"
        + (f" — {context.get('error_type')}: {context.get('reason')}" if context.get("reason") else ""))
    if args.seed_only:
        log(f"seed-only: descriptor at {args.out / 'descriptor.json'}")
        return 0
    from jasmine_core.api.server import serve
    log(f"serving the real P3 Core on http://127.0.0.1:{args.port}")
    try:
        serve(descriptor["db"], host="127.0.0.1", port=args.port, log=lambda line: log(line))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
