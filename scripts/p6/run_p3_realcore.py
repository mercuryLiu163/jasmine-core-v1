#!/usr/bin/env python3.13
"""P6-03a real-P3 acceptance: the continuity reads against an **isolated real P3 Core**.

Read `web/REAL-CORE-ACCEPTANCE-PLAN.md`. The P4 half of the plan is done (Core matrix 35/35, UI
pass 10/10); this closes the P6-03a half: checkpoint, Context Pack and Resume against real P3 code
instead of the labelled simulated fixture.

It starts `scripts/p6/p3_realcore_env.py` (real P3 Core at the pinned commit, its own tree, database
and port, checkpoints and resumes created by P3's own `ContinuityStore`), runs a **Core-level
matrix** of the four routes over HTTP with keys P3 minted, then builds the dashboard from a
`git archive` of `HEAD`, serves it with `P6_ENABLE_P3_READS=1`, and runs the P3 read walkthrough
(`browser_trace.mjs --p3-reads --real-core`) against it. The bundle is frozen with
`scripts/p6/freeze_bundle.py`.

**One row is BLOCKED rather than faked.** P3's `ContextBuilder` requires its pinned tokenizer
(`tiktoken` plus a BPE asset whose SHA-256 `context/tokens.py` fixes). Neither is present here and
installing one is out of scope, so **no Context Pack exists** in this Core. That row is recorded as
BLOCKED with the exact exception, in both the Core matrix and the browser pass; the other three
routes are verified for real, and the context route's shape remains covered by the frozen contract
and the simulated fixture.

    python3.13 scripts/p6/run_p3_realcore.py --out validation-runs/p6/<stamp>-p6-03a-realcore
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FREEZE = REPO_ROOT / "scripts" / "p6" / "freeze_bundle.py"
MODE = "real-core (P3)"


def log(message: str) -> None:
    print(f"p3-realcore-accept: {message}", file=sys.stderr, flush=True)


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def call(origin: str, method: str, path: str, token: str | None = None,
         body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(origin + path, method=method, data=data)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"__raw": raw[:300]}


def wait_for(url: str, *, timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.3)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ref", default=None, help="the pinned P3 commit")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--repo", type=Path, default=None,
                        help="repository for the Core ref and the dashboard archive "
                             "(default: this worktree)")
    parser.add_argument("--dashboard-ref", default="HEAD",
                        help="ref whose archive the dashboard is built from (default HEAD)")
    parser.add_argument("--no-freeze", action="store_true")
    parser.add_argument("--node-modules", type=Path, default=REPO_ROOT / "web" / "node_modules",
                        help="an existing node_modules to build the dashboard with (a fresh "
                             "worktree has none)")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    core_port, ui_port = free_port(), free_port()
    tmp = Path(tempfile.mkdtemp(prefix="p6-p3-realcore-"))
    env_dir = tmp / "env"
    web_work = tmp / "web"
    procs: list[subprocess.Popen] = []
    entries: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def record(ident: str, check: str, ok: bool | None, detail: Any = None,
               note: str = "") -> None:
        entries.append({"id": ident, "check": check, "mode": MODE, "ok": ok,
                        "detail": detail, "note": note})
        mark = "BLOCKED" if ok is None else ("ok  " if ok else "FAIL")
        log(f"{mark} {ident} {check}")

    def direct(method: str, path: str, key: str | None, token: str | None) -> tuple[int, Any]:
        status, payload = call(f"http://127.0.0.1:{core_port}", method, path, token)
        calls.append({"method": method, "path": path, "status": status, "actor_key": key,
                      "response_body": payload})
        return status, payload

    try:
        env_command = [sys.executable, str(REPO_ROOT / "scripts" / "p6" / "p3_realcore_env.py"),
                       "build", "--out", str(env_dir), "--port", str(core_port)]
        if args.ref:
            env_command += ["--ref", args.ref]
        if args.repo:
            env_command += ["--repo", str(args.repo)]
        env_log = (out / "env.stderr.txt").open("w")
        procs.append(subprocess.Popen(env_command, stdout=subprocess.DEVNULL, stderr=env_log))
        if not wait_for(f"http://127.0.0.1:{core_port}/v1/health"):
            log("the P3 Core never answered")
            return 1
        descriptor = json.loads((env_dir / "descriptor.json").read_text())
        tokens = json.loads((env_dir / "tokens.json").read_text())
        contract = json.loads((REPO_ROOT / "web" / "contracts" /
                               "p3-continuity-read.v1.json").read_text())
        # The P3 contract keeps the field sets in one top-level map, keyed by response identity;
        # `context_spread_resolved` is a nested note, not a route's key set.
        fields = {key: value for key, value in contract["response_fields"].items()
                  if isinstance(value, list)}
        seeded = descriptor["seeded"]
        origin = f"http://127.0.0.1:{core_port}"

        status, health = direct("GET", "/v1/health", None, None)
        record("C0.core-is-real-p3", "the Core reports P3's schema version",
               status == 200 and health.get("schema_version") == descriptor["schema_version"] == 10,
               {"schema_version": health.get("schema_version")})

        for ident, path, key in (
            ("C1.checkpoint", f"/v1/checkpoints/{seeded['checkpoint_id']}", "checkpoint"),
            ("C1.latest", f"/v1/tasks/{seeded['task_id']}/checkpoints/latest", "checkpoint"),
            ("C2.resume", f"/v1/resumes/{seeded['resume_id']}", "resume"),
        ):
            status, payload = direct("GET", path, "full", tokens["full"])
            got = sorted(payload.get(key, {})) if isinstance(payload.get(key), dict) else None
            record(ident, f"{path} answers real P3 with the frozen contract's keys",
                   status == 200 and got == sorted(fields[key]),
                   {"status": status, "expected": sorted(fields[key]), "got": got})

        status, payload = direct(
            "GET", f"/v1/tasks/{seeded['empty_task_id']}/checkpoints/latest", "full", tokens["full"])
        record("C3.empty", "a Task with no checkpoint answers null, not an error",
               status == 200 and payload.get("checkpoint") is None, {"status": status})

        status, payload = direct("GET", f"/v1/checkpoints/{seeded['unknown_checkpoint_id']}",
                                 "full", tokens["full"])
        code = (payload.get("error") or {}).get("code") if isinstance(payload, dict) else None
        record("C4.unknown", "an unknown checkpoint id is Core's own not_found",
               status == 404 and isinstance(code, str) and code.endswith("not_found"),
               {"status": status, "code": code})

        status, payload = direct("GET", "/v1/checkpoints/not-an-id", "full", tokens["full"])
        code = (payload.get("error") or {}).get("code") if isinstance(payload, dict) else None
        record("C5.malformed", "a malformed id is refused by Core as invalid_request",
               status == 400 and code == "invalid_request", {"status": status, "code": code})

        probe = f"/v1/context/{seeded['context_id'] or seeded['checkpoint_id']}"
        status, payload = direct("GET", probe, "limited", tokens["limited"])
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        record("C6.route_scope_missing",
               "a key without the route scope is refused, naming context:read",
               status == 403 and error.get("code") == "forbidden_scope"
               and error.get("details", {}).get("required") == "context:read",
               {"status": status, "details": error.get("details")})

        status, payload = direct("GET", probe, "route_scope", tokens["route_scope"])
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        record("C7.handler_scope_missing",
               "a key with only the route-table scope is refused, naming the checkpoint:read P3 adds",
               status == 403 and error.get("code") == "forbidden_scope"
               and error.get("details", {}).get("required") == "checkpoint:read",
               {"status": status, "details": error.get("details")})

        pack = descriptor["context_pack"]
        if pack["status"] == "BLOCKED":
            record("C8.context_pack", "a real Context Pack read", None,
                   {"error_type": pack.get("error_type"), "reason": pack.get("reason")},
                   note="BLOCKED: " + pack.get("note", "no context pack exists"))
        else:
            status, payload = direct("GET", f"/v1/context/{pack['context_id']}",
                                     "full", tokens["full"])
            got = sorted(payload.get("context", {})) if isinstance(payload.get("context"), dict) else None
            record("C8.context_pack", "a real Context Pack read answers the contract's keys",
                   status == 200 and got == sorted(fields["context"]),
                   {"status": status, "got": got})

        # --- the dashboard against the same Core -------------------------------------------------
        web_work.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(["git", "-C", str(args.repo or REPO_ROOT), "archive",
                                  args.dashboard_ref], check=True, capture_output=True)
        subprocess.run(["tar", "-x", "-C", str(web_work)], input=archive.stdout, check=True)
        os.symlink(args.node_modules, web_work / "web" / "node_modules")
        subprocess.run(["npx", "vite", "build"], cwd=web_work / "web", check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ui_env = dict(os.environ, P6_CORE_ORIGIN=origin, P6_ENABLE_P3_READS="1")
        serve_log = (out / "serve.stderr.txt").open("w")
        procs.append(subprocess.Popen(
            ["node", str(web_work / "web" / "server" / "serve.mjs"),
             "--root", str(web_work / "web" / "dist"), "--port", str(ui_port)],
            env=ui_env, stdout=subprocess.DEVNULL, stderr=serve_log))
        if not wait_for(f"http://127.0.0.1:{ui_port}/v1/health"):
            log("the dashboard host never answered")
            return 1

        trace_args = ["node", str(REPO_ROOT / "scripts" / "p6" / "browser_trace.mjs"),
                      "--ui", f"http://127.0.0.1:{ui_port}",
                      "--token-file", str(env_dir / "tokens.json"), "--token-key", "full",
                      "--out", str(out), "--p3-reads", "--real-core",
                      "--p3-checkpoint", seeded["checkpoint_id"],
                      "--p3-checkpoint-2", seeded["checkpoint_2_id"],
                      "--p3-checkpoint-revision", f"r{seeded['checkpoint_revision']}",
                      "--p3-checkpoint-2-revision", f"r{seeded['checkpoint_2_revision']}",
                      "--p3-first-note-text", seeded["checkpoint_note"],
                      "--p3-projection-digest", seeded["checkpoint_digests"]["projection"],
                      "--p3-context-digest", seeded["checkpoint_digests"]["context"],
                      "--p3-workspace-digest", seeded["checkpoint_digests"]["workspace"],
                      "--p3-resume", seeded["resume_id"],
                      "--p3-task", seeded["task_id"],
                      "--p3-task-empty", seeded["empty_task_id"],
                      "--p3-unknown", seeded["unknown_checkpoint_id"],
                      "--p3-limited-token", tokens["limited"],
                      "--p3-route-scope-token", tokens["route_scope"]]
        if pack["status"] == "BLOCKED":
            trace_args.append("--p3-no-context")
        trace_log = (out / "trace.stdout.txt").open("w")
        trace = subprocess.run(trace_args, stdout=trace_log, stderr=subprocess.STDOUT, text=True)
        trace_log.close()
        log(f"browser pass exit={trace.returncode}")

        ui_records = []
        if (out / "click-trace.jsonl").is_file():
            ui_records = [json.loads(line) for line in
                          (out / "click-trace.jsonl").read_text().splitlines() if line]
        blocked = [e for e in entries if e["ok"] is None] + \
                  [r for r in ui_records if r.get("ok") is None]
        failed = [e for e in entries if e["ok"] is False] + \
                 [r for r in ui_records if r.get("ok") is False]
        evidence = {
            "environment": descriptor["environment"],
            "ref": descriptor["ref"],
            "schema_version": descriptor["schema_version"],
            "mode": descriptor["mode"],
            "core_entries": entries,
            "ui_records": ui_records,
            "calls": calls,
            "summary": {
                "core_checks": len(entries), "ui_records": len(ui_records),
                "passed": len(entries) + len(ui_records) - len(blocked) - len(failed),
                "failed": len(failed), "blocked": len(blocked),
                "context_pack": descriptor["context_pack"]["status"],
            },
        }
        (out / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        (out / "descriptor.json").write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
        write_readme(out, evidence)
        shutil.copyfile(REPO_ROOT / "web" / "REAL-CORE-ACCEPTANCE-PLAN.md", out / "plan.md")
        assert_no_token(out, tokens)
        log(f"{evidence['summary']['passed']} passed, {evidence['summary']['failed']} failed, "
            f"{evidence['summary']['blocked']} blocked")
        if not failed and trace.returncode == 0 and not args.no_freeze:
            freeze(out, descriptor["ref"])
        return 1 if (failed or trace.returncode != 0) else 0
    finally:
        for process in procs:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            log(f"kept {tmp}")


def write_readme(out: Path, evidence: dict[str, Any]) -> None:
    summary = evidence["summary"]
    lines = [
        "# P6-03a real-P3 acceptance — the continuity reads against an isolated real P3 Core",
        "",
        f"P3 from the pinned commit `{evidence['ref']}` (schema {evidence['schema_version']}), its own",
        "tree, database and port; checkpoints and resumes created by P3's own `ContinuityStore`;",
        f"keys minted by P3's `auth.issue_key`. Mode: `{MODE}`.",
        "",
        f"Result: **{summary['passed']}** passed, {summary['failed']} failed, "
        f"{summary['blocked']} blocked ({summary['core_checks']} Core checks + "
        f"{summary['ui_records']} browser records).",
        "",
        "## What this proves, and what it does not",
        "",
        "* It proves the P6-03a views read **real P3 rows**: the checkpoint by id and `latest`, a Task",
        "  with no checkpoint answering `null`, the resume by id, Core's own `not_found` and",
        "  `invalid_request`, and the two scope refusals — a key missing the route's `context:read`, and",
        "  a key holding only the route-table scope but not the `checkpoint:read` that P3's handler",
        "  also requires (the distinction the frozen contract records as `extra_scopes`).",
        "* It proves the browser pass against a real build: no reply carries the simulated-fixture",
        "  label, and `/v1/health` reports P3's schema version.",
        "* **The Context Pack row is BLOCKED, not passed.** P3's `ContextBuilder` needs its pinned",
        "  tokenizer (`tiktoken` plus a BPE asset whose SHA-256 `context/tokens.py` fixes); neither is",
        "  present in this environment and installing one is out of scope, so no Context Pack exists",
        "  and **nothing was fabricated** in its place. Its shape remains covered by the frozen",
        "  contract and the simulated fixture.",
        "* The workspace scanner in these rows is a **component stand-in**, as P3's own continuity",
        "  tests use: workspace snapshots are not a real workspace capture.",
        "",
        "## Core checks",
        "",
        "| # | Check | Mode | Result |",
        "| --- | --- | --- | --- |",
    ]
    for index, entry in enumerate(evidence["core_entries"], start=1):
        mark = "BLOCKED" if entry["ok"] is None else ("PASS" if entry["ok"] else "**FAIL**")
        lines.append(f"| {index} | {entry['check']} | `{entry['mode']}` | {mark} |")
    lines += ["", "## Browser records", "", "| # | Record | Result |", "| --- | --- | --- |"]
    for index, record in enumerate(evidence["ui_records"], start=1):
        mark = "BLOCKED" if record.get("ok") is None else ("PASS" if record["ok"] else "**FAIL**")
        lines.append(f"| {index} | {record['step']} | {mark} |")
    blocked = [e for e in evidence["core_entries"] if e["ok"] is None] + \
              [r for r in evidence["ui_records"] if r.get("ok") is None]
    if blocked:
        lines += ["", "### Blocked, with the reason", ""]
        for entry in blocked:
            reason = entry.get("note") or json.dumps(entry.get("detail", {}))[:200]
            lines.append(f"* `{entry.get('id') or entry.get('step')}` — {reason}")
    lines += [
        "",
        "## Files",
        "",
        "* `evidence.json` — the Core checks with their request/response bodies, the browser records,",
        "  and every HTTP call (no Authorization header is recorded).",
        "* `descriptor.json` — the isolated Core's ids, mode labels and the Context Pack's status,",
        "  with the key material removed.",
        "* `env.stderr.txt`, `serve.stderr.txt`, `trace.stdout.txt`, `click-trace.jsonl`,",
        "  `browser-requests.jsonl`, `screenshots/`, `plan.md`.",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def assert_no_token(out: Path, tokens: dict[str, str]) -> None:
    for name, token in tokens.items():
        found = subprocess.run(["grep", "-rlF", "--", token, str(out)],
                               capture_output=True, text=True).stdout.split()
        if found:
            raise SystemExit(f"REFUSING TO FREEZE: token {name} appears in {found}")


def freeze(out: Path, ref: str) -> None:
    commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    dependency = json.dumps({
        "name": "P3 continuity slice", "status": "NOT MERGED", "source_ref": ref,
        "acceptance": "P3 phase acceptance NOT passed",
        "consequence": "the Core and the rows are real P3 code; the Context Pack row is BLOCKED "
                       "(no pinned tokenizer here) and the workspace scanner is a component "
                       "stand-in, so this is not a P3 Gate verdict",
    })
    subprocess.run([sys.executable, str(FREEZE), "--freeze", str(out), "--commit", commit,
                    "--kind", "real-core acceptance (isolated real P3 Core)",
                    "--runs", "one pass", "--dependency-json", dependency], check=True)
    subprocess.run([sys.executable, str(FREEZE), "--verify", str(out)], check=True)


if __name__ == "__main__":
    raise SystemExit(main())
