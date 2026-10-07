#!/usr/bin/env python3
"""Build a throwaway Core with real P2 data for the dashboard read slice.

The Core pipeline is real: interpretations are produced by
`InterpretationStore.process`, resolutions by `ResolverStore.resolve`, reviews by
`ReviewStore` — the same code paths the API calls. The model *provider* is a
component simulation (`Simulation`, the shape P2's own tests use), so every row
this script creates is real Core data produced by a simulated extractor. Nothing
here is a mock of the API or of the storage layer, and nothing touches the
operator's Core.

It also deliberately builds the states the dashboard must show honestly:

* an APPLIED resolution, a PENDING_REVIEW resolution with its review row,
* a `correct`ed child interpretation (lineage), and a `reject`ed one,
* enough interpretations to page (limit is small in the UI contract).

Usage::

    python3 scripts/p6/p6_p2_fixture.py build --out /private/tmp/p6-p2 --force
    python3 scripts/p6/p6_p2_fixture.py serve --out /private/tmp/p6-p2 --port 8795
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "p6"))

from p6_scratch import Scratch  # noqa: E402  (P1 scenario builder)

from jasmine_core import SCHEMA_VERSION, auth, db, ids, registry  # noqa: E402
from jasmine_core.events import EventStore  # noqa: E402
from jasmine_core.interpretations import InterpretationStore  # noqa: E402
from jasmine_core.interpreter_provider import _base_config  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402
from jasmine_core.models import NewEvent  # noqa: E402
from jasmine_core.objects import ObjectStore  # noqa: E402
from jasmine_core.models import NewObject  # noqa: E402
from jasmine_core.resolver import ResolverStore  # noqa: E402
from jasmine_core.reviews import ReviewStore  # noqa: E402

TEXT = "你好，帮我测试一下这个程序，使用 Playwright skill。"

READ_SCOPES = [
    "objects:read", "events:read", "state:read", "authority:read", "evidence:read",
    "interpretations:read", "reviews:read", "resolutions:read",
]
#: The P2 *write* slice: a real human actor with the scopes Core requires for the
#: milestone-B actions, on top of the read slice. The browser is never given this
#: token; the T03 Gate uses it deliberately, as the operator would.
P2_WRITE_SCOPES = READ_SCOPES + [
    "interpretations:process", "interpretations:manage",
    "reviews:manage", "resolutions:process", "resolutions:read",
    "authority:propose", "authority:manage",
    "objects:write", "events:write", "state:write", "state:accept",
    "evidence:confirm", "guard:check", "fingerprint:read",
]

WRITE_SCOPES = READ_SCOPES + [
    "objects:write", "events:write", "state:write", "state:accept",
    "authority:propose", "authority:manage", "evidence:confirm",
    "interpretations:process", "interpretations:manage",
    "reviews:manage", "resolutions:process", "guard:check", "fingerprint:read",
]


class Simulation:
    """Component-simulation provider, same shape as P2's own tests use."""

    def __init__(self, action=None):
        self.action = action
        self.calls = 0

    def configuration(self):
        value = _base_config()
        value.update(provider="component_simulation", version="p6-fixture.v1")
        return value

    def extract(self, source):
        self.calls += 1
        return self.action(source) if self.action else json.dumps(output(source), ensure_ascii=False)


def output(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": source["event_id"],
        "confidence": 0.95,
        "candidates": [{
            "kind": "REQUIRED_CAPABILITY",
            "content": "playwright",
            "scope": {"kind": "TASK", "project_id": source["project_id"],
                      "task_id": source["task_id"], "path": None, "tool": None},
            "impact": "LOW", "certainty": "EXPLICIT", "confidence": 0.95,
            "rationale": "用户明确要求能力",
            "source_span": {"start": 0, "end": len(source["text"]), "quote": source["text"]},
        }],
    }


def two_candidate_output(source: dict[str, Any]) -> str:
    """A plan with a TASK candidate plus a project-scoped Rule that needs review."""
    result = output(source)
    capability = result["candidates"][0]
    rule = copy.deepcopy(capability)
    rule.update(kind="RULE", content="Always run the device smoke test before release.",
                impact="MEDIUM")
    rule["scope"] = {"kind": "PROJECT", "project_id": source["project_id"],
                     "task_id": None, "path": None, "tool": None}
    result["candidates"] = [capability, rule]
    return json.dumps(result, ensure_ascii=False)


def _sha_command(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Fixture:
    """A P1 scenario (projects/tasks/steps/evidence/rules) plus real P2 rows.

    Built on `p6_scratch.Scratch` so the dashboard sees one coherent world: the
    Task board has Steps, and the interpretations hang off real `user.prompt`
    Events of that same project and task.
    """

    def __init__(self, out: Path) -> None:
        self.out = out
        self.base = Scratch(out)

    def build(self) -> dict[str, Any]:
        base = self.base.build()  # P1 scenario, workspace, actors, tokens, descriptor
        conn = db.connect(self.base.db_path)
        try:
            seeded = base["seeded"]
            p2 = self._seed(
                conn,
                project=seeded["projects"][0],
                task=seeded["tasks"][0],
                host=self.base.host,
                human=self.base.human,
                system=self.base.system,
            )
            issuer = auth.Auth(conn)
            read_token = issuer.issue_key(
                actor_id=self.base.reader, label="p6-ui-readonly-p2",
                scopes=READ_SCOPES)["token"]
            # A real human actor with the write scopes, for the T03 Gate only.
            human_token = issuer.issue_key(
                actor_id=self.base.human, label="p6-p2-human-write",
                scopes=P2_WRITE_SCOPES)["token"]
        finally:
            conn.close()

        descriptor = {
            **base,
            "provider": "component_simulation (p6-fixture.v1) — NOT a real model run",
            "read_only_token_scopes": READ_SCOPES,
            "p2": p2,
        }
        (self.out / "descriptor.json").write_text(
            json.dumps(descriptor, indent=2, ensure_ascii=False), encoding="utf-8")
        tokens = json.loads((self.out / "tokens.json").read_text(encoding="utf-8"))
        tokens["p2_read"] = read_token
        tokens["p2_human"] = human_token
        (self.out / "tokens.json").write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        os.chmod(self.out / "tokens.json", 0o600)
        return descriptor

    def _seed(self, conn, *, project: str, task: str, host: str, human: str,
              system: str) -> dict[str, Any]:
        self.host = host
        self.human = human
        self.system = system
        events = EventStore(conn, schema_version=SCHEMA_VERSION)

        def prompt(text: str) -> str:
            event, _ = events.append(NewEvent(
                event_type="user.prompt", source_system="p6-p2-fixture", actor_id=self.human,
                actor_kind="human", host_id=self.host, project_id=project, task_id=task,
                payload={"text": text}, occurred_at=None))
            return event["event_id"]

        simulation = Simulation()
        interpretations = InterpretationStore(conn, schema_version=SCHEMA_VERSION,
                                              provider=simulation)
        resolver = ResolverStore(interpretations, execution_actor_id=self.system,
                                 execution_host_id=self.host)
        reviews = ReviewStore(resolver)

        def interpret(event_id: str, key: str, action=None) -> str:
            interpretations.provider = Simulation(action) if action else simulation
            return interpretations.process({"event_id": event_id, "idempotency_key": key},
                                           actor_id=self.human)["interpretation"]["interpretation_id"]

        def resolve_body(ident: str, key: str) -> dict[str, Any]:
            preview = resolver.preview(ident)
            return {"idempotency_key": key, "host_id": self.host,
                    "policy_version": preview["policy_version"],
                    "expected_revisions": preview["expected_revisions"],
                    "expected_context_digest": preview["expected_context_digest"]}

        # 1. applied: a task-scoped capability only.
        applied_event = prompt(TEXT + " (applied case)")
        applied = interpret(applied_event, "fixture-applied")
        applied_resolution = resolver.resolve(applied, resolve_body(applied, "resolve-applied"),
                                             actor_id=self.human)["resolution"]["resolution_id"]

        # 2. pending review: a project-scoped Rule candidate needs a human.
        review_event = prompt(TEXT + " (pending review case)")
        pending = interpret(review_event, "fixture-pending",
                            action=two_candidate_output)
        pending_resolution = resolver.resolve(pending, resolve_body(pending, "resolve-pending"),
                                              actor_id=self.human)["resolution"]
        review_items = reviews.list(limit=50)["items"]
        review_id = next((item["review_id"] for item in review_items
                          if item["resolution_id"] == pending_resolution["resolution_id"]), None)

        # 3. lineage: correct the applied interpretation, resolve the child.
        child = resolver.maintenance.change(applied, "correct", {
            "idempotency_key": "fixture-correct", "host_id": self.host,
            "reason": "fixture correction for the read slice", "expected_revision": 1,
            "result": {"event_id": applied_event, "confidence": 0.9, "candidates": output(
                {"event_id": applied_event, "project_id": project, "task_id": task, "text": TEXT}
            )["candidates"]},
        }, actor_id=self.human)["result"]["child_interpretation_id"]

        # 3b. rerun the corrected child so the fixture contains a REAL
        # `interpretation_operation_completions` row (the dashboard renders that
        # row, and a schema-only guess about its columns is not acceptable).
        # `change(rerun)` reserves the operation, runs the provider through
        # `process`, then finishes it, which is what writes the completion.
        head_after_correct = resolver.maintenance.head(child)["revision"]
        rerun = resolver.maintenance.change(child, "rerun", {
            "idempotency_key": "fixture-rerun", "host_id": self.host,
            "reason": "fixture rerun for the operation-completion row",
            "expected_revision": head_after_correct,
        }, actor_id=self.human)

        # 4. rejected interpretation, to show a non-current state.
        rejected_event = prompt(TEXT + " (rejected case)")
        rejected = interpret(rejected_event, "fixture-rejected")
        resolver.maintenance.change(rejected, "reject", {
            "idempotency_key": "fixture-reject", "host_id": self.host,
            "reason": "fixture: extraction looked wrong", "expected_revision": 1,
        }, actor_id=self.human)

        # 5. extra interpretations so the list pages at a small limit.
        extra = [interpret(prompt(f"{TEXT} (page case {index})"), f"fixture-page-{index}")
                 for index in range(4)]

        return {
            "project_id": project, "task_id": task,
            "applied_interpretation_id": applied, "applied_resolution_id": applied_resolution,
            "pending_interpretation_id": pending,
            "pending_resolution_id": pending_resolution["resolution_id"],
            "pending_review_id": review_id,
            "child_interpretation_id": child,
            "rerun_child_interpretation_id": rerun["result"]["child_interpretation_id"],
            "rerun_completion": (resolver.maintenance.get(child).get("operation_completions") or [None])[0],
            "rejected_interpretation_id": rejected,
            "extra_interpretation_ids": extra,
            "event_ids": {"applied": applied_event, "pending": review_event,
                          "rejected": rejected_event},
            "provider_calls": simulation.calls,
        }

    def serve(self, port: int) -> None:
        from jasmine_core.api.server import serve

        os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = str(self.base.workspace)
        os.environ["JASMINE_CORE_EVIDENCE_PRODUCERS"] = str(self.base.producer_file)
        serve(self.base.db_path, host="127.0.0.1", port=port,
              log=lambda line: print(f"jasmine-core: {line}", file=sys.stderr, flush=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p6_p2_fixture")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--out", required=True)
    build.add_argument("--force", action="store_true")
    serve = sub.add_parser("serve")
    serve.add_argument("--out", required=True)
    serve.add_argument("--port", type=int, default=8795)
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser().resolve()
    if args.command == "build":
        if args.force and out.exists():
            import shutil

            shutil.rmtree(out)
        print(json.dumps(Fixture(out).build(), indent=2, ensure_ascii=False))
        return 0
    fixture = Fixture(out)
    if not fixture.base.db_path.exists():
        print(f"no fixture at {fixture.base.db_path}; run build first", file=sys.stderr)
        return 2
    fixture.serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
