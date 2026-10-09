#!/usr/bin/env python3.13
"""Item 6 isolated real Core/Chrome gate. No operator DB, hooks or services.

Run from a clean committed candidate with --out validation-runs/p6/<run>.
Credentials/DB/browser profiles stay in a private temporary runtime; the output
contains only fixture Truth, executed test output, receipts and browser traces.
Any failed check exits nonzero and retains the report. This is a P6 confirmation
gate, not native capture, P4 Memory acceptance or proof of a physical human.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_SECRET = "sk-item6-synthetic-only-no-credential-123456789"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def save(path, value):
    def derived(item):
        if isinstance(item, str): return item.replace(SYNTHETIC_SECRET, "[REDACTED_SYNTHETIC_SECRET_PATTERN]")
        if isinstance(item, dict): return {key: derived(child) for key, child in item.items()}
        if isinstance(item, list): return [derived(child) for child in item]
        return item
    path.write_text(json.dumps(derived(value), ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--development", action="store_true", help="uncommitted smoke only; never a frozen Gate PASS")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.chmod(out, 0o700)
    report = {"mode": "real-core-real-browser", "scope": "P6-02 item 6", "status": "RUNNING", "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    processes = []
    logs = []

    def check(name, condition, details=None):
        report["checks"].append({"name": name, "passed": bool(condition), "details": details})
        save(out / "gate.json", report)
        if not condition:
            raise AssertionError(name)

    try:
        clean = not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).strip()
        report["development"] = args.development
        report["tracked_clean"] = clean
        check("candidate_tracked_clean_or_explicit_development", clean or args.development)
        report["candidate_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        report["approved_inputs"] = {name: hashlib.sha256((ROOT / "docs/proposals/p6-02-item6" / name).read_bytes()).hexdigest() for name in ("adr.md", "api.md", "validation-plan.md")}
        report["dependencies"] = ["039f5a3e7800704e5f0478d01992f00eeb2ca3f1", "41a0dd6ae29dc559c9d3817fb6ea34229c68b66b", "7016dabb8926d6bd6599f1f56b8f39cbda253ebf"]
        report["environment"] = {"python": sys.version, "node": subprocess.check_output(["node", "--version"], text=True).strip(), "git": subprocess.check_output(["git", "--version"], text=True).strip()}
        # Dist is rebuilt from this exact clean candidate, not an old worktree.
        build = subprocess.run(["npm", "run", "build"], cwd=ROOT / "web", capture_output=True, text=True)
        (out / "build.txt").write_text(build.stdout + build.stderr)
        check("candidate_dashboard_build", build.returncode == 0)
        with tempfile.TemporaryDirectory(prefix="jasmine-item6-private-") as temporary:
            runtime = Path(temporary).resolve()
            os.chmod(runtime, 0o700)
            workspace = runtime / "workspace"
            workspace.mkdir()
            (workspace / ".gitignore").write_text("__pycache__/\nresults/\n")
            (workspace / "invoice.py").write_text(
                "from decimal import Decimal, ROUND_HALF_UP\n"
                "def total(price, quantity, tax):\n"
                "    if quantity < 0: raise ValueError('negative quantity')\n"
                "    return (Decimal(price)*quantity*(1+Decimal(tax))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n")
            (workspace / "test_invoice.py").write_text(
                "import unittest\nfrom decimal import Decimal\nfrom invoice import total\n"
                "class InvoiceTests(unittest.TestCase):\n"
                "    def test_tax_and_rounding(self): self.assertEqual(total('3.33',3,'0.07'),Decimal('10.69'))\n"
                "    def test_zero(self): self.assertEqual(total('3.33',0,'0.07'),Decimal('0.00'))\n"
                "    def test_negative(self):\n"
                "        with self.assertRaises(ValueError): total('3.33',-1,'0.07')\n")
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(["git", "add", "."], cwd=workspace, check=True)
            subprocess.run(["git", "-c", "user.name=Item6 Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "fixture source"], cwd=workspace, check=True)
            argv = [sys.executable, "-m", "unittest", "-v", "test_invoice"]
            command = shlex.join(argv)
            mapping = runtime / "producer.json"
            save(mapping, [{"kind": "TEST", "tool_name": "item6_unittest", "command_sha256": hashlib.sha256(command.encode()).hexdigest()}])
            mapping.chmod(0o600)
            env = {key: value for key, value in os.environ.items() if not key.startswith("JASMINE_")}
            env.update(PYTHONPATH=str(ROOT / "src"), PYTHONNOUSERSITE="1", JASMINE_CORE_WORKSPACE_ROOT=str(workspace), JASMINE_CORE_EVIDENCE_PRODUCERS=str(mapping))
            # No deployment-owned Memory endpoint is used by this gate.
            env.pop("JASMINE_HINDSIGHT_URL", None)
            sys.path.insert(0, str(ROOT / "src"))
            from jasmine_core import auth, db, ids, registry
            from jasmine_core.migrations import migrate
            conn = db.connect(runtime / "core.db")
            migrate(conn)
            host = ids.new_id("hst")
            actor_ids = {kind: ids.new_id("act") for kind in ("human", "system", "agent")}
            with db.transaction(conn):
                reg = registry.Registry(conn)
                reg.upsert_host(host, display_name="Isolated item 6 fixture")
                for kind, actor in actor_ids.items():
                    reg.upsert_actor(actor, kind=kind, home_host_id=host)
            human_scopes = ["objects:read", "objects:write", "events:read", "events:write", "authority:read", "state:read", "state:write", "state:accept", "evidence:read", "evidence:confirm"]
            issued = {"human": auth.Auth(conn).issue_key(actor_id=actor_ids["human"], label="isolated browser", scopes=human_scopes),
                      "system": auth.Auth(conn).issue_key(actor_id=actor_ids["system"], label="executed fixture tests", scopes=["evidence:write", "evidence:read", "fingerprint:scan"]),
                      "admin": auth.Auth(conn).issue_key(actor_id=actor_ids["human"], label="independent audit read", scopes=["admin"]),
                      "setup": auth.Auth(conn).issue_key(actor_id=actor_ids["human"], label="isolated Rule setup", scopes=["authority:manage", "authority:propose", "events:write"]),
                      "agent": auth.Auth(conn).issue_key(actor_id=actor_ids["agent"], label="negative agent", scopes=["objects:read", "events:read", "authority:read", "state:read", "evidence:read"])}
            conn.close()
            report["identities"] = {name: {field: value[field] for field in ("actor_id", "key_id", "scopes")} for name, value in issued.items()}
            core_origin = f"http://127.0.0.1:{port()}"
            ui_origin = f"http://127.0.0.1:{port()}"

            def start_core():
                handle = (runtime / "core.log").open("a")
                logs.append(handle)
                proc = subprocess.Popen([sys.executable, "-m", "jasmine_core.cli", "serve", "--db", str(runtime / "core.db"), "--port", core_origin.rsplit(":", 1)[1]], env=env, stdout=handle, stderr=handle)
                processes.append(proc)
                return proc

            def wait_url(url):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        urllib.request.urlopen(url, timeout=1).close()
                        return
                    except (OSError, urllib.error.HTTPError):
                        if processes and processes[-1].poll() is not None:
                            for handle in logs:
                                handle.flush()
                                log_path = Path(handle.name)
                                if log_path.exists():
                                    text = log_path.read_text()
                                    for key in issued.values(): text = text.replace(key["token"], "[REDACTED_BEARER]")
                                    (out / (log_path.name + ".txt")).write_text(text)
                            raise RuntimeError("isolated server exited; see retained sanitized server log")
                        time.sleep(.1)
                raise RuntimeError("isolated server unavailable")

            def call(method, path, body=None, identity="human", expected=None):
                request = urllib.request.Request(core_origin + path, data=None if body is None else json.dumps(body).encode(), method=method, headers={"Authorization": "Bearer " + issued[identity]["token"], "Content-Type": "application/json"})
                try:
                    response = urllib.request.urlopen(request, timeout=35)
                except urllib.error.HTTPError as error:
                    response = error
                value = json.load(response)
                status = response.status
                report.setdefault("http", []).append({"method": method, "path": path, "status": status, "request_id": response.headers.get("X-Request-Id"), "body": body, "response": value})
                if expected is not None:
                    check(f"{method} {path} expected {expected}", status == expected, {"status": status, "code": value.get("error", {}).get("code")})
                return status, value

            core = start_core()
            wait_url(core_origin + "/v1/health")
            _, health = call("GET", "/v1/health", expected=200)
            report["schema_version"] = health["schema_version"]
            criteria = {"requirements": [{"key": "tested", "kind": "TEST", "required_result": "PASS"}, {"key": "reviewed", "kind": "USER_CONFIRMATION", "required_result": "INFO"}]}
            _, value = call("POST", "/v1/projects", {"name": "item6 isolated gate", "host_id": host}, expected=201)
            project = value["object"]["project_id"]

            def make_task(title):
                _, value = call("POST", "/v1/tasks", {"project_id": project, "title": title, "host_id": host, "acceptance_criteria": criteria}, expected=201)
                task = value["object"]["task_id"]
                _, value = call("POST", f"/v1/tasks/{task}/steps", {"title": "review executed tests", "host_id": host, "expected_revision": 1, "acceptance_criteria": criteria}, expected=201)
                step = value["step"]["step_id"]
                call("POST", f"/v1/steps/{step}/transition", {"host_id": host, "status": "IN_PROGRESS", "expected_revision": 1}, expected=200)
                result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True)
                text = result.stdout + result.stderr
                (workspace / "results").mkdir(exist_ok=True)
                artifact = f"results/{step}.txt"
                (workspace / artifact).write_text(text)
                (out / f"executed-{step}.txt").write_text(text)
                check("actual_fixture_tests", result.returncode == 0, {"argv": argv, "output_sha256": hashlib.sha256(text.encode()).hexdigest()})
                call("POST", "/v1/tool-results", {"task_id": task, "step_id": step, "host_id": host, "codex_session_id": "isolated-gate", "turn_id": "executed-test", "tool_use_id": step, "tool_name": "item6_unittest", "tool_input": {"command": command}, "tool_response": {"exit_code": result.returncode, "output": text}, "kind": "TEST", "artifact_uri": "workspace:/" + artifact}, identity="system", expected=201)
                call("POST", f"/v1/steps/{step}/transition", {"host_id": host, "status": "EXECUTED", "expected_revision": 2}, expected=200)
                return task, step

            task, step = make_task("browser full confirmation and acceptance")
            _, origin = call("POST", "/v1/events", {"event_type": "assistant.message", "source_system": "isolated-item6-gate", "host_id": host, "project_id": project, "task_id": task, "payload": {"text": "Synthetic browser review material setup; not a user prompt or confirmation."}}, identity="setup", expected=201)
            rule_text = "Full escaped Rule: <script>globalThis.item6Injected=1</script> " + SYNTHETIC_SECRET
            _, rule = call("POST", "/v1/rules/proposals", {"rule_key": "browser-full-material", "kind": "RULE", "severity": "NORMAL", "enforcement": "CONTEXT", "content": rule_text, "matcher": {}, "scope": {"kind": "task", "project_id": project, "task_id": task}, "origin_event_id": origin["event"]["event_id"], "host_id": host}, identity="setup", expected=201)
            call("POST", f"/v1/rules/{rule['rule']['rule_id']}/approve", {"host_id": host, "expected_revision": 1}, identity="setup", expected=200)
            expiry_task, expiry_step = make_task("actual 600 second expiry")
            _, expiry_task_row = call("GET", f"/v1/tasks/{expiry_task}", expected=200)
            _, expiry_prepared = call("POST", "/v1/evidence/confirmations/prepare", {"event_id": ids.new_id("evt"), "task_id": expiry_task, "step_id": expiry_step, "target_status": "VERIFIED", "expected_task_revision": expiry_task_row["task"]["revision"], "expected_step_revision": 3}, expected=201)
            ui_log = (runtime / "ui.log").open("w")
            logs.append(ui_log)
            processes.append(subprocess.Popen(["node", str(ROOT / "web/server/serve.mjs"), "--port", ui_origin.rsplit(":", 1)[1]], env=dict(env, P6_CORE_ORIGIN=core_origin, P6_ENABLE_WEB_CONFIRMATIONS="1"), stdout=ui_log, stderr=ui_log))
            wait_url(ui_origin)
            descriptor = {"ui": ui_origin, "core": core_origin, "task": task, "step": step, "host": host, "humanToken": issued["human"]["token"], "systemToken": issued["system"]["token"], "testArgv": argv, "testCommand": command, "workspace": str(workspace), "out": str(out), "syntheticSecret": SYNTHETIC_SECRET, "ruleText": rule_text}
            report["redaction"] = {"kind": "derived-evidence-only", "replacement": "[REDACTED_SYNTHETIC_SECRET_PATTERN]", "locations": ["Rule.content", "submitted note/source text", "browser reports", "screenshots"], "product_raw_and_review_sha": "unaltered; compared before deriving evidence"}

            def browser(phase):
                result = subprocess.run(["node", str(ROOT / "scripts/p6/item6_realcore_ui.mjs"), phase], input=json.dumps(descriptor), text=True, capture_output=True, timeout=180)
                (out / f"browser-{phase}.txt").write_text(result.stdout + result.stderr)
                check("real_browser_" + phase, result.returncode == 0, {"exit_code": result.returncode})

            browser("loop")
            browser_report = json.loads((out / "browser-loop.json").read_text())
            descriptor["preparationId"] = browser_report["preparations"][-1]
            _, task_value = call("GET", f"/v1/tasks/{task}", expected=200)
            _, step_value = call("GET", f"/v1/steps/{step}", expected=200)
            check("actual_separate_state_acceptance", task_value["task"]["status"] == "ACCEPTED" and step_value["step"]["status"] == "ACCEPTED")
            _, audit = call("GET", "/v1/audit?limit=500", identity="admin", expected=200)
            submit_ids = {row["request_id"] for row in browser_report["network"] if row["path"].endswith("/confirmations/submit") and row.get("core_status") == 201}
            audit_rows = audit.get("audit", audit.get("entries", audit.get("rows", [])))
            check("audit_contains_no_raw_secret_pattern_material", SYNTHETIC_SECRET not in json.dumps(audit_rows))
            successful_audit = {row.get("request_id") for row in audit_rows if row.get("actor_id") == actor_ids["human"] and row.get("method") == "POST" and row.get("path") == "/v1/evidence/confirmations/submit" and row.get("scope") == "evidence:confirm" and row.get("decision") == "allow" and row.get("status_code") == 201}
            receipt_ids = {raw["command_event"]["payload"]["request_id"] for raw in browser_report["receipts"]}
            check("separate_admin_audit_submit_receipts", len(submit_ids) == 3 and submit_ids == receipt_ids and submit_ids <= successful_audit, {"submit_request_ids": sorted(submit_ids)})
            # Restart only this harness-owned Core; browser starts a fresh profile.
            core.terminate(); core.wait(timeout=10)
            core = start_core(); wait_url(core_origin + "/v1/health")
            browser("history")
            _, history = call("GET", "/v1/evidence/confirmations/" + descriptor["preparationId"], expected=200)
            check("restart_retains_immutable_receipt", history.get("consumption") is not None)
            def truth(target_task, target_step):
                connection = db.connect(runtime / "core.db")
                try:
                    snapshot = {
                        "task": dict(connection.execute("SELECT * FROM tasks WHERE task_id=?", (target_task,)).fetchone()),
                        "step": dict(connection.execute("SELECT * FROM steps WHERE step_id=?", (target_step,)).fetchone()),
                        "events": [dict(row) for row in connection.execute("SELECT event_id,event_type,body_sha256,seq FROM events WHERE task_id=? ORDER BY seq", (target_task,))],
                        "evidence": [dict(row) for row in connection.execute("SELECT * FROM evidence WHERE task_id=? ORDER BY evidence_id", (target_task,))],
                        "rules": [dict(row) for row in connection.execute("SELECT * FROM rule_versions WHERE rule_id IN (SELECT rule_id FROM rules WHERE task_id=?) ORDER BY rule_id,version", (target_task,))],
                    }
                    return snapshot
                finally: connection.close()

            def refuse_unchanged(name, target_task, target_step, method, path, body, expected, identity="human"):
                before = truth(target_task, target_step)
                status, value = call(method, path, body, identity=identity, expected=expected)
                expected_errors = {
                    "no_confirmation": ("missing_evidence", None),
                    "affirmation_false": ("invalid_request", None),
                    "stale_revision": ("revision_conflict", None),
                    "filesystem_drift": ("confirmation_context_conflict", "fingerprint"),
                    "agent_wrong_identity": ("forbidden_scope", None),
                    "state_after_prepare": ("confirmation_context_conflict", "revision"),
                    "same_actor_other_key": ("confirmation_owner_mismatch", None),
                    "rule_added_after_prepare": ("confirmation_context_conflict", "rules"),
                    "changed_body_same_id": ("event_id_conflict", None),
                    "info_cannot_satisfy_pass": ("missing_evidence", None),
                    "actual_wall_clock_expiry": ("confirmation_expired", None),
                    "raw_source_forgery": ("invalid_request", None),
                }
                expected_code, changed = expected_errors[name]
                error = value.get("error", {})
                check(name + "_exact_refusal_reason", error.get("code") == expected_code and (changed is None or changed in error.get("details", {}).get("changed", [])), error)
                after = truth(target_task, target_step)
                report.setdefault("truth_before_after", []).append({"scenario": name, "before": before, "after": after})
                check(name + "_truth_unchanged", before == after)
                return status, value

            def prepared_for(target_task, target_step, expected=None):
                _, current = call("GET", f"/v1/tasks/{target_task}", expected=200)
                _, current_step = call("GET", f"/v1/steps/{target_step}", expected=200)
                body = {"event_id": ids.new_id("evt"), "task_id": target_task, "step_id": target_step, "target_status": "VERIFIED", "expected_task_revision": current["task"]["revision"], "expected_step_revision": current_step["step"]["revision"]}
                if expected: body.update(expected)
                _, result = call("POST", "/v1/evidence/confirmations/prepare", body, expected=201)
                return body, result

            def submission(preparation):
                return {"event_id": ids.new_id("evt"), "preparation_id": preparation["preparation_id"], "review_sha256": preparation["review_sha256"], "affirmed": True, "note": "isolated negative/receipt exercise; INFO only"}

            # Counterexample: real filesystem mutation after prepare refuses before Truth.
            negative_task, negative_step = make_task("file drift refuses confirmation")
            _, nt = call("GET", f"/v1/tasks/{negative_task}", expected=200)
            prepare_body = {"event_id": ids.new_id("evt"), "task_id": negative_task, "step_id": negative_step, "target_status": "VERIFIED", "expected_task_revision": nt["task"]["revision"], "expected_step_revision": 3}
            _, prepared = call("POST", "/v1/evidence/confirmations/prepare", prepare_body, expected=201)
            note_body = {"event_id": ids.new_id("evt"), "preparation_id": prepared["preparation_id"], "review_sha256": prepared["review_sha256"], "affirmed": True, "note": "isolated drift negative"}
            refuse_unchanged("no_confirmation", negative_task, negative_step, "POST", f"/v1/steps/{negative_step}/transition", {"host_id": host, "status": "VERIFIED", "expected_revision": 3}, 422)
            refuse_unchanged("affirmation_false", negative_task, negative_step, "POST", "/v1/evidence/confirmations/submit", {**note_body, "affirmed": False}, 400)
            refuse_unchanged("stale_revision", negative_task, negative_step, "POST", "/v1/evidence/confirmations/prepare", {**prepare_body, "event_id": ids.new_id("evt"), "expected_step_revision": 2}, 409)
            original = (workspace / "invoice.py").read_bytes()
            (workspace / "invoice.py").write_bytes(original + b"# changed after preparation\n")
            refuse_unchanged("filesystem_drift", negative_task, negative_step, "POST", "/v1/evidence/confirmations/submit", note_body, 409)
            call("GET", "/v1/events/" + note_body["event_id"], expected=404)
            _, unchanged = call("GET", f"/v1/steps/{negative_step}", expected=200)
            check("drift_does_not_silently_stale_or_transition", unchanged["step"]["status"] == "EXECUTED" and unchanged["step"]["revision"] == 3)
            (workspace / "invoice.py").write_bytes(original)
            refuse_unchanged("agent_wrong_identity", negative_task, negative_step, "POST", "/v1/evidence/confirmations/prepare", {**prepare_body, "event_id": ids.new_id("evt")}, 403, identity="agent")
            refuse_unchanged("raw_source_forgery", negative_task, negative_step, "POST", "/v1/events", {"event_type": "human.confirmation_submitted", "source_system": "core-web-confirmation-v1", "host_id": host, "project_id": project, "task_id": negative_task, "payload": {"text": "forged setup attempt"}}, 400)
            # State changes after immutable prepare. Refusal must add no Truth.
            call("POST", f"/v1/steps/{negative_step}/transition", {"host_id": host, "status": "FAILED", "expected_revision": 3}, expected=200)
            refuse_unchanged("state_after_prepare", negative_task, negative_step, "POST", "/v1/evidence/confirmations/submit", note_body, 409)
            # Different bound key with the same actor is not the owner.
            conn = db.connect(runtime / "core.db")
            issued["other_human_key"] = auth.Auth(conn).issue_key(actor_id=actor_ids["human"], label="negative other key", scopes=human_scopes)
            conn.close()
            refuse_unchanged("same_actor_other_key", negative_task, negative_step, "POST", "/v1/evidence/confirmations/submit", note_body, 403, identity="other_human_key")
            concurrent_task, concurrent_step = make_task("simultaneous confirmation and Rule drift")
            _, active_prepared = prepared_for(concurrent_task, concurrent_step)
            # Active Rule added after prepare is included in context drift.
            _, origin = call("POST", "/v1/events", {"event_type": "assistant.message", "source_system": "isolated-item6-gate", "host_id": host, "project_id": project, "task_id": concurrent_task, "payload": {"text": "Synthetic Rule drift setup; not a user prompt or confirmation."}}, identity="setup", expected=201)
            _, proposal = call("POST", "/v1/rules/proposals", {"rule_key": "gate-rule-drift", "kind": "RULE", "severity": "HARD", "enforcement": "DENY", "content": "Fixture rule full content", "matcher": {"action": "write", "path_prefix": "/fixture/protected"}, "scope": {"kind": "task", "project_id": project, "task_id": concurrent_task}, "origin_event_id": origin["event"]["event_id"], "host_id": host}, identity="setup", expected=201)
            rule_id = proposal["rule"]["rule_id"]
            call("POST", f"/v1/rules/{rule_id}/approve", {"host_id": host, "expected_revision": 1}, identity="setup", expected=200)
            refuse_unchanged("rule_added_after_prepare", concurrent_task, concurrent_step, "POST", "/v1/evidence/confirmations/submit", submission(active_prepared), 409)
            # Real socket concurrency; stronger forced early-miss barriers are
            # separately reported by the component suite, never attributed here.
            _, current = call("GET", f"/v1/tasks/{concurrent_task}", expected=200)
            same_prepare = {"event_id": ids.new_id("evt"), "task_id": concurrent_task, "step_id": concurrent_step, "target_status": "VERIFIED", "expected_task_revision": current["task"]["revision"], "expected_step_revision": 3}
            def simultaneous(path, bodies):
                barrier = threading.Barrier(2)
                def invoke(body):
                    barrier.wait(timeout=10)
                    return call("POST", path, body)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    return list(executor.map(invoke, bodies))
            results = simultaneous("/v1/evidence/confirmations/prepare", [same_prepare, same_prepare])
            check("simultaneous_prepare_same_id", sorted(status for status, _ in results) == [200, 201])
            concurrent_prepared = results[0][1]
            submit = submission(concurrent_prepared)
            results = simultaneous("/v1/evidence/confirmations/submit", [submit, submit])
            check("simultaneous_submit_same_id", sorted(status for status, _ in results) == [200, 201])
            _, receipt = call("POST", "/v1/evidence/confirmations/submit", submit, expected=200)
            check("replay_does_not_raise_info_to_pass", receipt["evidence"]["status"] == "INFO")
            refuse_unchanged("changed_body_same_id", concurrent_task, concurrent_step, "POST", "/v1/evidence/confirmations/submit", {**submit, "note": "changed"}, 409)
            _, unique_prepared = prepared_for(concurrent_task, concurrent_step)
            first, second = submission(unique_prepared), submission(unique_prepared)
            before = truth(concurrent_task, concurrent_step)
            results = simultaneous("/v1/evidence/confirmations/submit", [first, second])
            check("simultaneous_distinct_id_one_consumption", sorted(status for status, _ in results) == [201, 409])
            after = truth(concurrent_task, concurrent_step)
            check("one_source_command_evidence_only", len(after["events"]) - len(before["events"]) == 2 and len(after["evidence"]) - len(before["evidence"]) == 1)
            # INFO cannot satisfy a USER_CONFIRMATION/PASS requirement.
            pass_task, pass_step = make_task("INFO is insufficient for PASS")
            _, step_row = call("GET", f"/v1/steps/{pass_step}", expected=200)
            pass_criteria = {"requirements": [{"key": "must_pass", "kind": "USER_CONFIRMATION", "required_result": "PASS"}]}
            call("POST", f"/v1/steps/{pass_step}/criteria", {"host_id": host, "expected_revision": step_row["step"]["revision"], "acceptance_criteria": pass_criteria}, expected=200)
            _, info_prepared = prepared_for(pass_task, pass_step)
            call("POST", "/v1/evidence/confirmations/submit", submission(info_prepared), expected=201)
            _, step_row = call("GET", f"/v1/steps/{pass_step}", expected=200)
            refuse_unchanged("info_cannot_satisfy_pass", pass_task, pass_step, "POST", f"/v1/steps/{pass_step}/transition", {"host_id": host, "status": "VERIFIED", "expected_revision": step_row["step"]["revision"]}, 422)
            # Both old/broad proxy writes and cross-origin new writes are refused
            # over the actual standalone host socket, before they reach Core.
            for path, body, origin_header in [("/v1/evidence/confirm", {}, None), ("/v1/events", {}, None), ("/v1/workspaces/fingerprint", {}, None), ("/v1/evidence/confirmations/prepare", same_prepare, "https://invalid.example")]:
                request = urllib.request.Request(ui_origin + path, data=json.dumps(body).encode(), method="POST", headers={"Authorization": "Bearer " + issued["human"]["token"], "Content-Type": "application/json", **({"Origin": origin_header} if origin_header else {})})
                before = truth(concurrent_task, concurrent_step)
                try: response = urllib.request.urlopen(request, timeout=5)
                except urllib.error.HTTPError as error: response = error
                check("actual_proxy_refuses_" + path, response.status in (403, 404, 405))
                check("proxy_refusal_truth_unchanged", truth(concurrent_task, concurrent_step) == before)
            # Actual server wall clock, without patching product time or SQL.
            if args.development:
                report["expiry"] = {"status": "NOT_RUN_DEVELOPMENT", "reason": "Development smoke never claims frozen Gate PASS"}
            else:
                expires = datetime.fromisoformat(expiry_prepared["expires_at"].replace("Z", "+00:00")).timestamp()
                report["expiry"] = {"status": "WAITING_REAL_CLOCK", "expires_at": expiry_prepared["expires_at"]}
                save(out / "gate.json", report)
                while time.time() < expires + .15:
                    time.sleep(min(10, max(.01, expires + .15 - time.time())))
                refuse_unchanged("actual_wall_clock_expiry", expiry_task, expiry_step, "POST", "/v1/evidence/confirmations/submit", submission(expiry_prepared), 409)
                report["expiry"]["status"] = "PASS_REAL_CLOCK"
            conn = db.connect(runtime / "core.db")
            auth.Auth(conn).revoke(issued["human"]["key_id"])
            conn.close()
            call("GET", "/v1/evidence/confirmations/" + descriptor["preparationId"], expected=401)
            # Product payloads contain only harmless fixture text; never headers/tokens.
            encoded = json.dumps(report, ensure_ascii=False)
            check("bundle_has_no_bearer_tokens", all(key["token"] not in encoded for key in issued.values()))
            required = ["actual_separate_state_acceptance", "separate_admin_audit_submit_receipts", "audit_contains_no_raw_secret_pattern_material", "restart_retains_immutable_receipt", "no_confirmation_truth_unchanged", "affirmation_false_exact_refusal_reason", "stale_revision_exact_refusal_reason", "filesystem_drift_exact_refusal_reason", "agent_wrong_identity_exact_refusal_reason", "same_actor_other_key_exact_refusal_reason", "state_after_prepare_exact_refusal_reason", "rule_added_after_prepare_exact_refusal_reason", "raw_source_forgery_exact_refusal_reason", "simultaneous_prepare_same_id", "simultaneous_submit_same_id", "simultaneous_distinct_id_one_consumption", "one_source_command_evidence_only", "info_cannot_satisfy_pass_exact_refusal_reason", "actual_proxy_refuses_/v1/evidence/confirm", "actual_proxy_refuses_/v1/events", "actual_proxy_refuses_/v1/workspaces/fingerprint", "actual_proxy_refuses_/v1/evidence/confirmations/prepare", "real_browser_loop", "real_browser_history"]
            if not args.development: required.append("actual_wall_clock_expiry_exact_refusal_reason")
            names = {item["name"] for item in report["checks"] if item["passed"]}
            check("all_required_scenarios_present_and_passed", set(required) <= names and all(item["passed"] for item in report["checks"]))
            report["limitations"] = ["Automated browser proves the browser-to-Core chain; no physical human presence proof.", "TEST producer is an isolated configured runner with actual subprocess output; no native Codex/P2 capture claim.", "P4 Memory engine, P7, deployment services and aggregate P6/V1 acceptance are outside this Gate."]
            report["status"] = "DEVELOPMENT_PASS" if args.development else "PASS"
    except Exception as error:
        report["status"] = "FAIL"
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        for proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        for handle in logs: handle.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save(out / "gate.json", report)
        hashes = {str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest() for path in out.rglob("*") if path.is_file()}
        save(out / "manifest.json", {"candidate_commit": report.get("candidate_commit"), "status": report["status"], "sha256": hashes})
    print(json.dumps({"status": report["status"], "out": str(out), "checks": len(report["checks"]), "error": report.get("error")}))
    return 0 if report["status"] in ("PASS", "DEVELOPMENT_PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
