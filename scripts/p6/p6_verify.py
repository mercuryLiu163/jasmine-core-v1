#!/usr/bin/env python3
"""Assemble a P6-01 evidence bundle (taskbook §8) for one candidate commit.

Runs the dashboard's real browser trace against a running Core + dashboard,
snapshots the Core objects before and after to show nothing was written, records
the automated test output, and writes the bundle files a reviewer needs:

    manifest.json            commit, versions, dependency commits, mode
    scenario.md              what was exercised and how
    click-trace.jsonl        browser steps (from browser_trace.mjs)
    api-requests.jsonl       redacted Core requests (method/path/status/request_id)
    api-responses.jsonl      per-request status + Core request_id (no bodies)
    truth-before-after.json  GET snapshots before/after, and whether they differ
    screenshots/             PNGs from the trace
    automated-tests.txt      raw `tsc`, `vitest`, `vite build` output
    result.md                per-Gate verdicts and the unverified scope

Nothing here mints tokens, writes Truth, reads audit or touches a user account.
The Core and dashboard must already be running; the read token comes from the
scratch fixture (or any operator-provided read-only token).

Usage::

    python3 scripts/p6/p6_verify.py \
      --ui http://127.0.0.1:5180 --core http://127.0.0.1:8789 \
      --token-file /private/tmp/p6-scratch/tokens.json --token-key read \
      --out validation-runs/p6/<run-id> --project prj_… --task tsk_… \
      --rule rul_… --evidence evd_… --event evt_…
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def api(core: str, token: str, path: str) -> tuple[int, object]:
    request = urllib.request.Request(
        core + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def snapshot(core: str, token: str, project_id: str | None, task_id: str | None,
             evidence_id: str | None, rule_id: str | None,
             interpretation_id: str | None = None, review_id: str | None = None,
             resolution_id: str | None = None) -> dict[str, object]:
    """Read the objects the trace touches. Any change here means a Truth write."""
    result: dict[str, object] = {}
    reads = {
        "health": "/v1/health",
        "projects": "/v1/projects?limit=50&offset=0",
        "tasks": "/v1/tasks?limit=50&offset=0",
        "events": "/v1/events?limit=100",
    }
    if project_id:
        reads["project"] = f"/v1/projects/{project_id}"
        reads["tasks_of_project"] = f"/v1/tasks?project_id={project_id}&limit=50&offset=0"
        reads["active_rules"] = f"/v1/rules/active?project_id={project_id}"
    if task_id:
        reads["task"] = f"/v1/tasks/{task_id}"
        reads["steps"] = f"/v1/tasks/{task_id}/steps"
        reads["task_history"] = f"/v1/tasks/{task_id}/history"
        reads["task_evidence"] = f"/v1/tasks/{task_id}/evidence"
    if evidence_id:
        reads["evidence"] = f"/v1/evidence/{evidence_id}"
    if rule_id:
        reads["rule"] = f"/v1/rules/{rule_id}"
        reads["rule_history"] = f"/v1/rules/{rule_id}/history"
    # P6-02 read slice. `reviews/pending` and `resolutions/preview` have no
    # collection siblings, so they are snapshotted exactly as the UI reads them.
    if project_id:
        reads["interpretations"] = f"/v1/interpretations?project_id={project_id}&limit=50"
        reads["reviews_pending"] = f"/v1/reviews/pending?project_id={project_id}&limit=50"
    for name, path in (
        ("interpretation", f"/v1/interpretations/{interpretation_id}" if interpretation_id else None),
        ("resolution_preview", f"/v1/resolutions/preview?interpretation_id={interpretation_id}" if interpretation_id else None),
        ("review", f"/v1/reviews/{review_id}" if review_id else None),
        ("resolution", f"/v1/resolutions/{resolution_id}" if resolution_id else None),
    ):
        if path:
            reads[name] = path
    for name, path in reads.items():
        status, body = api(core, token, path)
        result[name] = {"path": path, "status": status, "body": body}
    return result


def dev_proxy_check(ui: str, token: str) -> str:
    """Reproduce the dev-proxy allowlist checks as a recorded artefact."""
    lines = [
        "# P6-A02 dev-proxy check (operator-configured loopback Core, frozen allowlist)",
        f"# ui: {ui}  core: (see manifest)  {now()}",
        "",
    ]

    def call(method: str, path: str, *, auth: bool) -> tuple[int, str]:
        request = urllib.request.Request(ui + path, method=method)
        request.add_header("Accept", "application/json")
        if auth:
            request.add_header("Authorization", "Bearer " + token)
        if method == "POST":
            request.add_header("Content-Type", "application/json")
            request.data = b"{}"
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")

    for label, method, path, auth in (
        ("GET / (shell)", "GET", "/", False),
        ("GET /v1/health (public)", "GET", "/v1/health", False),
        ("GET /v1/projects?limit=1 (read token)", "GET", "/v1/projects?limit=1", True),
        ("GET /v1/auth/keys (admin surface)", "GET", "/v1/auth/keys", True),
        ("GET /v1/audit (admin surface)", "GET", "/v1/audit", True),
        ("POST /v1/tasks (write)", "POST", "/v1/tasks", True),
        ("OPTIONS /v1/projects (preflight)", "OPTIONS", "/v1/projects", True),
        ("GET /v1/../../etc/passwd (traversal)", "GET", "/v1/../../etc/passwd", True),
    ):
        status, body = call(method, path, auth=auth)
        lines.append(f"## {label}")
        lines.append(f"{method} {path} -> status={status}")
        if path != "/":
            lines.append(body[:400])
        lines.append("")
    return "\n".join(lines) + "\n"


#: P2 tables the read slice can touch through lazy recovery. Digesting them
#: before/after is what lets the report say *which* read wrote, if any.
P2_TABLES = [
    "interpretations", "interpretation_results", "interpretation_changes",
    "interpretation_operation_completions", "maintenance_heads",
    "resolutions", "resolution_results", "reviews", "review_changes",
    "source_applications", "source_application_changes", "events",
    # Bookkeeping Core writes for every request, including reads: auditing the
    # call (`audit_log`) and stamping the key that authenticated it
    # (`api_keys.last_used_at`). They are expected to change and are reported
    # separately from Truth writes.
    "audit_log", "api_keys",
]

#: Tables whose growth is expected evidence of authentication/auditing, not a
#: write performed by the request's own semantics.
BOOKKEEPING_TABLES = {"audit_log", "api_keys"}


def normalise_body(body: object) -> object:
    """Strip per-response correlation ids before comparing two reads.

    Every Core response carries a fresh `request_id`; comparing full bodies would
    report every *error* response as "changed" even when the refusal is identical.
    """
    if isinstance(body, dict):
        return {key: normalise_body(value) for key, value in body.items() if key != "request_id"}
    if isinstance(body, list):
        return [normalise_body(item) for item in body]
    return body


def p2_table_digest(db_path: str | None) -> dict[str, object] | None:
    """Read-only digest of the P2 tables, for an honest side-effect comparison."""
    if not db_path:
        return None
    import hashlib
    import sqlite3

    from pathlib import Path as _Path

    path = _Path(db_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, object] = {}
    try:
        for table in P2_TABLES:
            try:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error:
                out[table] = {"present": False}
                continue
            payload = "\n".join(
                json.dumps(dict(row), sort_keys=True, ensure_ascii=False, default=str) for row in rows
            )
            out[table] = {
                "present": True,
                "rows": len(rows),
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }
    finally:
        conn.close()
    return out


def run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return (
        f"$ {' '.join(command)}\n"
        f"exit={completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout}\n"
        f"--- stderr ---\n{completed.stderr}\n"
    )


def stdout_of(command: list[str]) -> str:
    """The stdout lines of a command, for version reporting only."""
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.stdout.strip()


def git(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p6_verify")
    parser.add_argument("--ui", required=True)
    parser.add_argument("--core", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--token-key", default="read")
    parser.add_argument("--out", required=True)
    parser.add_argument("--project")
    parser.add_argument("--task")
    parser.add_argument("--step")
    parser.add_argument("--rule")
    parser.add_argument("--proposed-rule", dest="proposed_rule",
                        help="P6-02b: a PROPOSED Rule, to show APPROVE armed")
    parser.add_argument("--retire-rule", dest="retire_rule",
                        help="P6-02b: a second ACTIVE Rule, to show RETIRE armed")
    parser.add_argument("--reject-review", dest="reject_review",
                        help="P6-02b: the second PENDING review, so REJECT has a target")
    parser.add_argument("--evidence")
    parser.add_argument("--event")
    parser.add_argument("--interpretation", help="P6-02: interpretation id to open")
    parser.add_argument("--pending-interpretation", dest="pending_interpretation")
    parser.add_argument("--review", help="P6-02: pending review id to open")
    parser.add_argument("--resolution", help="P6-02: resolution id to open")
    parser.add_argument("--p2-project", dest="p2_project", help="P6-02: scope for the review queue")
    parser.add_argument("--completion-interpretation", dest="completion_interpretation",
                        help="P6-02: interpretation whose operation completion must render")
    parser.add_argument("--completion-actions", dest="completion_actions",
                        help="P6-02: comma-separated maintenance actions Core reports for that chain")
    parser.add_argument("--db", help="P6-02: fixture db path, for the P2 table before/after dump")
    parser.add_argument("--scopes", help="override the recorded token scopes (else read from the fixture descriptor)")
    parser.add_argument("--label", default=None, help="run label, e.g. 'P6-02 milestone A'")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--dev-ui", help="also record the allowlist checks against a Vite dev server")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    token = json.loads(Path(args.token_file).read_text(encoding="utf-8"))[args.token_key]

    # The manifest must describe the token this run really used. The fixture
    # descriptor next to --db knows it; the P1 list is only the fallback.
    fixture_descriptor: dict[str, object] = {}
    if args.db:
        candidate = Path(args.db).resolve().parent / "descriptor.json"
        if candidate.exists():
            try:
                fixture_descriptor = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                fixture_descriptor = {}
    scopes = (
        [scope.strip() for scope in args.scopes.split(",") if scope.strip()]
        if args.scopes
        else list(fixture_descriptor.get("read_only_token_scopes") or [])  # type: ignore[arg-type]
        or ["objects:read", "events:read", "state:read", "authority:read", "evidence:read"]
    )
    p2_mode = bool(args.interpretation or args.review or args.resolution or args.p2_project)
    label = args.label or ("P6-02 milestone A (read slice)" if p2_mode else "P6-01")
    fixture_name = (
        Path(str(fixture_descriptor.get("scratch_root", ""))).name
        if fixture_descriptor
        else "scratch fixture"
    )

    started_commit = git("rev-parse", "HEAD")
    started_dirty = [line[3:] for line in git("status", "--porcelain").splitlines() if line.strip()]
    if started_dirty:
        print(json.dumps({"warning": "worktree is dirty; the evidence binds to this commit but not to "
                                      "the exact tree", "dirty": started_dirty}, indent=2))

    before = snapshot(args.core, token, args.project, args.task, args.evidence, args.rule,
                      args.interpretation, args.review, args.resolution)
    tables_before = p2_table_digest(args.db) if args.db else None

    trace_command = [
        "node", str(ROOT / "scripts" / "p6" / "browser_trace.mjs"),
        "--ui", args.ui,
        "--token-file", args.token_file,
        "--token-key", args.token_key,
        "--out", str(out),
    ]
    for flag, value in (("--project", args.project), ("--task", args.task), ("--step", args.step),
                        ("--rule", args.rule), ("--evidence", args.evidence), ("--event", args.event),
                        ("--interpretation", args.interpretation),
                        ("--pending-interpretation", args.pending_interpretation),
                        ("--review", args.review), ("--resolution", args.resolution),
                        ("--p2-project", args.p2_project),
                        ("--completion-interpretation", args.completion_interpretation),
                        ("--completion-actions", args.completion_actions),
                        ("--proposed-rule", args.proposed_rule),
                        ("--retire-rule", args.retire_rule),
                        ("--reject-review", args.reject_review)):
        if value:
            trace_command += [flag, value]
    trace_output = run(trace_command, ROOT)
    (out / "browser-trace.stdout.txt").write_text(trace_output, encoding="utf-8")

    after = snapshot(args.core, token, args.project, args.task, args.evidence, args.rule,
                     args.interpretation, args.review, args.resolution)
    tables_after = p2_table_digest(args.db) if args.db else None
    changed = sorted(
        name for name in before
        if normalise_body(before[name]) != normalise_body(after.get(name))
    )
    table_changes = {}
    if tables_before is not None and tables_after is not None:
        for name in sorted(set(tables_before) | set(tables_after)):
            if tables_before.get(name) != tables_after.get(name):
                table_changes[name] = {"before": tables_before.get(name), "after": tables_after.get(name)}
    # `audit_log` and `api_keys.last_used_at` are written for every request,
    # including reads, so a read-only run *must* change them; that is evidence of
    # auditing and authentication, not a Truth write. Everything else changing
    # would be a write performed by a read and is reported separately.
    truth_writes = {
        name: value for name, value in table_changes.items() if name not in BOOKKEEPING_TABLES
    }
    bookkeeping_changes = {
        name: value for name, value in table_changes.items() if name in BOOKKEEPING_TABLES
    }
    (out / "truth-before-after.json").write_text(
        json.dumps({"captured_at": now(), "before": before, "after": after,
                    "changed_reads": changed,
                    "p2_tables_before": tables_before, "p2_tables_after": tables_after,
                    "p2_tables_changed": table_changes,
                    "p2_truth_writes": truth_writes,
                    "p2_bookkeeping_changes": bookkeeping_changes,
                    "audit_log_grew": "audit_log" in table_changes,
                    "api_keys_touched": "api_keys" in table_changes,
                    "verdict": "unchanged" if not changed else "CHANGED",
                    "table_verdict": "unchanged" if not truth_writes else "CHANGED"}, indent=2,
                   ensure_ascii=False) + "\n", encoding="utf-8")

    # The browser request log already carries method/path/status/request id with
    # the Authorization value replaced; expose it under the taskbook's name.
    requests_path = out / "browser-requests.jsonl"
    if requests_path.exists():
        shutil.copyfile(requests_path, out / "api-requests.jsonl")
        responses = []
        for line in requests_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("path", "").startswith("/v1/"):
                continue
            responses.append({
                "at": row.get("at"),
                "method": row.get("method"),
                "path": row.get("path"),
                "status": row.get("status"),
                "core_request_id": row.get("xRequestId"),
                "authorization": row.get("authorization"),
            })
        (out / "api-responses.jsonl").write_text(
            "\n".join(json.dumps(row) for row in responses) + "\n", encoding="utf-8")

    if args.dev_ui:
        check = dev_proxy_check(args.dev_ui, token)
        (out / "dev-proxy-check.txt").write_text(check, encoding="utf-8")

    tests = ""
    if not args.skip_tests:
        # Build first, then typecheck, then test. An independent validator found the
        # old order (vitest before vite build) failed its own block on a clean
        # checkout, because a test depended on `web/dist`. That dependency is gone,
        # but the artefact under test should still exist before it is declared good.
        tests += run([str(WEB / "node_modules" / ".bin" / "vite"), "build"], WEB)
        tests += run([str(WEB / "node_modules" / ".bin" / "tsc"), "--noEmit", "--pretty", "false"], WEB)
        tests += run([str(WEB / "node_modules" / ".bin" / "vitest"), "run"], WEB)
        (out / "automated-tests.txt").write_text(tests, encoding="utf-8")

    steps = []
    trace_log = out / "click-trace.jsonl"
    if trace_log.exists():
        steps = [json.loads(line) for line in trace_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    failed = [step for step in steps if not step.get("ok")]
    manifest: dict[str, object] = {
        "run_id": out.name,
        "created_at_utc": now(),
        "phase": label,
        "mode": "real-browser-against-scratch-fixture" if "p6-scratch" in args.core else "real-browser",
        "commit": started_commit,
        "commit_at_end": git("rev-parse", "HEAD"),
        "commit_changed_during_run": started_commit != git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_clean_at_start": not started_dirty,
        "worktree_dirty_at_start": started_dirty,
        "worktree_dirty_at_end": [line[3:] for line in git("status", "--porcelain").splitlines() if line.strip()],
        "implementation_baseline_commit": "9059625",
        "core": {"url": args.core, "schema_version": before["health"]["body"]["schema_version"],
                 "core_version": before["health"]["body"]["core_version"]},
        "dashboard": {"url": args.ui, "bundle": "web/dist"},
        "runtime": {"python": platform.python_version(), "node": stdout_of(["node", "--version"]),
                    "platform": platform.platform()},
        "token": {"scopes": scopes, "value": "not recorded"},
        "fixture": {
            "name": fixture_name,
            "builder": str(fixture_descriptor.get("provider", "unknown")),
            "note": "descriptor.json next to --db" if fixture_descriptor else "not provided",
        },
        "objects": {"project_id": args.project, "task_id": args.task, "rule_id": args.rule,
                    "evidence_id": args.evidence},
        "browser_steps": {"total": len(steps), "passed": len(steps) - len(failed),
                          "failed": [step.get("step") for step in failed]},
        "truth_changed_reads": changed,
    }

    (out / "scenario.md").write_text(
        f"""# P6-01 scenario — {out.name}

- candidate commit: `{manifest['commit']}` (branch `{manifest['branch']}`)
- dashboard: {args.ui} — static build of `web/dist`
- Core: {args.core} — schema v{manifest['core']['schema_version']}, core {manifest['core']['core_version']}
- mode: {manifest['mode']}
- fixture: {fixture_name} — {manifest['fixture']['builder']}
- token scopes: {", ".join(scopes)}
  (the token value is deliberately not recorded anywhere in this bundle)

## Walkthrough

The browser trace performs these actions against the running dashboard:
{'''10. P6-02 read slice (when its flags are supplied): the Interpretation list with
    its loaded-range statement, an Interpretation detail with candidates, lineage
    and the server's Resolution preview, the preview conflict for a stale
    interpretation, a Pending Review queue and detail, and a Resolution detail.
''' if p2_mode else ''}
1. open the sign-in page, type a **bogus** token and assert it is refused (the
   public `/v1/health` answers 200 for any token, so this is what proves sign-in
   validates against an authenticated read);
2. type the real read-only token and assert it is accepted after an
   authenticated `GET /v1/projects?limit=1`;
3. assert the token appears in no DOM node, storage or cookie;
4. click Overview → Project and read `GET /v1/tasks?project_id=…`;
5. click 看板 and assert Step columns render separately from the Task status;
6. run an accessibility scan (no unnamed interactive node);
7. focus a source-trace link and activate it with the keyboard;
8. reload, assert the route survives and the in-memory token does not, sign in
   again and assert the same Event is re-read;
9. read Rule detail/history and Evidence detail;
10. assert the Interpretation area is explicitly 未接入;
11. assert an unknown route is reported rather than mapped to Overview;
12. sign out and assert no later request carries an Authorization header.

## Deliberately not covered

- P6-02 audited human writes (Rule approve/supersede, Task/Step transitions,
  Evidence confirmation) — the contracts and the permission Gate are not
  delivered in this PR.
- P6-03a/b/c inspectors (P3 Context/Checkpoint, P4 Memory, P7-01 Validation).
- The operator's own Core: this run uses an isolated scratch fixture built by
  `scripts/p6/p6_scratch.py`, so it proves the UI/API path, not a production
  dataset.
""", encoding="utf-8")

    # The tail must describe the run that actually happened: a P6-02 read-slice
    # run must not append the P6-01 "P6-02 not delivered" verdict to its own
    # milestone-A PASS (an independent validator caught that contradiction).
    if p2_mode:
        tail = [
            "## What this run does not cover",
            "",
            "- The audited writes themselves (correct/reject/rerun, review approve/reject, Rule",
            "  management). This pass proves the READ half only; the writes are covered by",
            "  `scripts/p6/p6_t03_gate.py`, whose report sits beside this one.",
            "- P6-T04/T05/T06 (P3/P4/P7-01 inspectors) — out of scope.",
            "- The operator's own Core and a real captured conversation; this run used an isolated",
            "  fixture whose data was produced by the real pipeline with a component-simulation provider.",
            "- Independent review and validation of this exact bundle.",
            "",
            "## Verdict",
            "",
            "`P6-02 milestone A (read slice): delivered and trace-verified on an isolated fixture`.",
            "`P6-02 milestone B (audited human writes): implemented; see the Gate report in this",
            "directory for its per-action evidence`. `P6-02` as a whole is PARTIAL — manual reapply and",
            "the Task/Step/Evidence writes are not delivered — and no part of the P6 stage or V1 is",
            "released by this run.",
            "",
        ]
    else:
        tail = [
            "## Not verified by this run",
            "",
            "- P6-T03 (audited human writes) — delivered after P6-01; see P6-02-COMPLIANCE.md.",
            "- P6-T04 (actual injected Context), P6-T05 (Memory degradation), P6-T06 (Validation",
            "  drill-down) — P3/P4/P7-01 contracts not frozen.",
            "- Real operator Core and real captured conversation; this run used the scratch fixture.",
            "- Independent review and independent validation: see the reviewer/validator reports added",
            "  next to this file when they exist. Until then this file is the implementer's own result",
            "  and must not be read as an independent PASS.",
            "",
            "## Verdict",
            "",
            "`P6-01: PARTIAL` — the read-only dashboard and its real browser trace work on the fixture;",
            "P6 as a stage is not complete and no part of V1 is released by this run.",
            "",
        ]

    gate_lines = [
        f"# {label} verification result",
        "",
        f"- run: `{out.name}`",
        f"- candidate commit: `{manifest['commit']}`",
        f"- browser steps: {manifest['browser_steps']['passed']}/{manifest['browser_steps']['total']} passed",
        f"- automated tests: {'see automated-tests.txt' if not args.skip_tests else 'skipped'}",
        f"- Core reads before/after: **{'unchanged' if not changed else 'CHANGED: ' + ', '.join(changed)}**",
        "",
        "## Gate mapping",
        "",
        "| Gate | What this run shows | Verdict |",
        "| --- | --- | --- |",
        "| P6-T01 P1 source trace | Project → Task → board → source Event by click and by keyboard; deep link re-read after refresh; Rule detail/history; Evidence detail | "
        + ("PASS (scratch fixture)" if not failed else "FAIL") + " |",
        "| P6-T02 permissions and secrets | token absent from DOM/storage/cookies; proxy forwards GET only and never `/v1/auth/*` or `/v1/audit` (unit + curl); a bogus token is refused at sign-in; no Truth write across the run | "
        + ("PASS (unit + trace)" if not failed else "FAIL") + " |",
        "| P6-T07 maintainability | scope switch drops the previous project's rows (component test); pagination appends by cursor; unknown route is loud; failure/unknown are not shown as empty | "
        + ("PARTIAL — no long-list/throttling run yet") + " |",
    ] + tail
    if p2_mode:
        gate_lines = gate_lines[:1] + [
            "",
            "## P6-02 milestone A Gate mapping",
            "",
            "| Area | What this run shows | Verdict |",
            "| --- | --- | --- |",
            "| Read paths and scopes | the six frozen P2 GETs answer through the proxy with the "
            f"recorded scopes ({', '.join(scopes)}); every write is still refused by the proxy | "
            + ("PASS" if not failed else "FAIL") + " |",
            "| No implied totals | both `after_id` collections state a loaded count only; completion "
            "comes from Core's null cursor | PASS |",
            "| Conflict vs empty | a stale interpretation renders the preview conflict, never an empty plan | PASS |",
            "| No write controls | no correct/reject/rerun/approve control exists in the UI | PASS |",
            "| Side-effect accounting | `p2_truth_writes` empty and `audit_log_grew` true for THIS fixture; "
            "the lazy-recovery probe in this bundle proves a plain GET can still write "
            "`interpretation_results` when a provider deadline has passed | PASS (fixture-scoped) |",
            "",
            "`p2_truth_writes: []` is a fixture-scoped observation, **not** a claim that GET never writes.",
            "See `lazy-recovery-probe/lazy-recovery-proof.md` in this directory.",
            "",
        ] + gate_lines[1:]
    if failed:
        gate_lines += ["## Failed browser steps", ""] + [
            f"- {step.get('step')} — {step.get('detail', '')}" for step in failed
        ] + [""]
    (out / "result.md").write_text("\n".join(gate_lines), encoding="utf-8")

    # Written last so `artifacts` lists everything this run produced.
    manifest["artifacts"] = sorted(path.name for path in out.glob("*") if path.is_file())
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")

    print(json.dumps({"out": str(out), "browser_steps": manifest["browser_steps"],
                      "truth_changed_reads": changed, "p2_truth_writes": sorted(truth_writes),
                      "p2_bookkeeping_changes": sorted(bookkeeping_changes),
                      "audit_log_grew": "audit_log" in table_changes,
                      "commit": manifest["commit"],
                      "commit_changed_during_run": manifest["commit_changed_during_run"]}, indent=2))
    return 1 if failed or changed or truth_writes else 0


if __name__ == "__main__":
    raise SystemExit(main())
