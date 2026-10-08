#!/usr/bin/env python3.13
"""P6-T05 degraded slice: Memory unavailable, Core and the P1 views still working — then recovered.

`web/P6-REMAINING-TASKS.md` names this as the next behavioural slice after P6-03. The taskbook's T05
PASS condition has a half no previous run covered — *"Memory 故障不挡 P1 视图,degraded 明确"* — and a
recovery half the operator asked for: after the engine returns, the same environment must be read
again. This run covers both against the same isolated real P4 Core the other acceptances use:

1. **Outage.** `scripts/p6/p4_realcore_env.py --engine-state unavailable` stops the wire-double engine
   after seeding and lets **P4's own probe** (`record_engine_probe`) record the refusal, so
   `/v1/memory/health` reports `unreachable` / `degraded: true` / `core_impact: memory_paused` /
   `last_error_code: engine_unreachable` while `/v1/health` still says `ok`.
2. **Recovery, in the same environment.** The engine port is pinned by a harness shim so the wire
   double can return on the **same address** — P4 builds its transport once per process, so a new port
   would not be the operator's situation — and a loopback-only control channel starts it again. P4's
   probe then records `reachable: true`, and a real `reflect` reaches the restored engine. The Core is
   **not** restarted: same process, same database, same ids, same engine URL.

Honesty carried into the evidence:

* `mode: boundary-fake` still means **real Core, simulated engine boundary**: the "outage" is a stopped
  loopback wire double, not a real Hindsight outage, so nothing here is evidence about Hindsight,
  extraction or reflection content, and it is not a P4 Gate verdict;
* `POST /v1/memory/reflect` answers **HTTP 200 with a `degraded` payload** during the outage, and the
  check records that as a degradation rather than a success; after recovery the same call reaches the
  engine (`error_code: null`, banks `ok`);
* the revision comparison reads the field from the response layer it actually lives in and **fails if
  it is missing**, so it cannot pass by comparing two `null`s (an independent review found the first
  version did exactly that);
* `status: idle` after recovery is P4's own conservative vocabulary ("reachable again, nothing
  pending, no operation has completed since"), and is recorded as such rather than rounded up to `ok`.

    python3.13 scripts/p6/run_t05_degraded.py --out validation-runs/p6/<stamp>-p6-t05-degraded \\
        --repo <worktree> --ref <commit> --node-modules <path>
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
MODE = "real-core (P4) + unavailable-then-restored wire-double engine"


def log(message: str) -> None:
    print(f"t05-degraded: {message}", file=sys.stderr, flush=True)


def revision_of(payload: Any, wrapper: str) -> int:
    """The `revision` of a wrapped object response, refusing to invent one.

    `/v1/tasks/{id}` answers `{"task": {...}}` and `/v1/rules/{id}` answers `{"rule": {...}}`, each
    with a `revision` field. Reading the wrong layer (or a missing field) used to yield `None` on both
    sides of the before/after comparison, which made the check pass without comparing anything an
    independent review reproduced that by changing a real revision. This raises instead, so a wrong
    read is a FAIL with a reason rather than a silent pass.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    body = payload.get(wrapper)
    if not isinstance(body, dict):
        raise ValueError(f"expected the reply to carry a `{wrapper}` object; keys were "
                         f"{sorted(payload)[:10]}")
    revision = body.get("revision")
    if not isinstance(revision, int):
        raise ValueError(f"`{wrapper}.revision` is not an integer: {revision!r}")
    return revision


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for(url: str, seconds: float = 40.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.3)
    return False


def call(origin: str, method: str, path: str, token: str | None,
         body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(origin + path, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        status = error.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"_raw": raw[:500]}


def health_subset(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": None}
    return {key: payload.get(key) for key in ("status", "degraded", "core_impact", "configured",
                                              "reachable", "last_error_code", "last_success_at")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="the evidence directory")
    parser.add_argument("--ref", default=None, help="the commit the Core is extracted from")
    parser.add_argument("--repo", type=Path, default=None,
                        help="repository for the Core ref and the dashboard archive")
    parser.add_argument("--dashboard-ref", default="HEAD",
                        help="ref whose archive the dashboard is built from (default HEAD)")
    parser.add_argument("--node-modules", type=Path, default=REPO_ROOT / "web" / "node_modules")
    parser.add_argument("--no-freeze", action="store_true")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    repo = args.repo or REPO_ROOT
    ref = args.ref or subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                     capture_output=True, text=True).stdout.strip()
    core_port, ui_port = free_port(), free_port()
    tmp = Path(tempfile.mkdtemp(prefix="p6-t05-degraded-"))
    env_dir = tmp / "env"
    web_work = tmp / "web"
    procs: list[subprocess.Popen] = []
    entries: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def record(ident: str, check: str, ok: bool | None, detail: Any = None, note: str = "") -> None:
        entries.append({"id": ident, "check": check, "mode": MODE, "ok": ok,
                        "detail": detail, "note": note})
        mark = "BLOCKED" if ok is None else ("ok  " if ok else "FAIL")
        log(f"{mark} {ident} {check}")

    def direct(method: str, path: str, key: str | None, token: str | None,
               body: Any = None) -> tuple[int, Any]:
        status, payload = call(origin, method, path, token, body)
        calls.append({"method": method, "path": path, "status": status, "actor_key": key,
                      "response_body": payload})
        return status, payload

    try:
        env_log = (out / "env.stderr.txt").open("w")
        procs.append(subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "scripts" / "p6" / "p4_realcore_env.py"), "build",
             "--out", str(env_dir), "--ref", ref, "--repo", str(repo), "--port", str(core_port),
             "--engine-state", "unavailable"],
            stdout=subprocess.DEVNULL, stderr=env_log))
        origin = f"http://127.0.0.1:{core_port}"
        if not wait_for(f"{origin}/v1/health"):
            log("the P4 Core never answered")
            return 1

        descriptor = json.loads((env_dir / "descriptor.json").read_text())
        tokens = json.loads((env_dir / "tokens.json").read_text())
        seeded = descriptor["seeded"]
        health_at_build = descriptor.get("health_at_build")
        control_url = descriptor.get("engine_control_url")
        token = tokens["human_manage"]

        # --- C0/C1: the environment is really in the unavailable state ---------------------------
        engine_state = str(descriptor["mode"].get("engine_state", ""))
        if health_at_build is None or not engine_state.startswith("unavailable"):
            record("C0.engine_state", "the isolated Core was built with the engine unavailable", None,
                   {"engine_state": engine_state},
                   note="BLOCKED: the environment did not reach the unavailable state, so the "
                        "slice's precondition is missing and nothing is fabricated in its place")
        else:
            record("C0.engine_state", "the isolated Core was built with the engine unavailable",
                   engine_state.startswith("unavailable") and health_at_build["status"]
                   == "unreachable", {"engine_state": engine_state,
                                      "status": health_at_build["status"]})
        status, health = direct("GET", "/v1/memory/health", "human_manage", token)
        unavailable = health_subset(health)
        record("C1.memory_health_unreachable",
               "Memory reports the engine unreachable and Core's own impact",
               status == 200 and unavailable["status"] == "unreachable"
               and unavailable["degraded"] is True
               and unavailable["core_impact"] == "memory_paused"
               and unavailable["configured"] is True
               and unavailable["reachable"] is False
               and unavailable["last_error_code"] == "engine_unreachable",
               {"http": status, **unavailable, "jobs": health.get("jobs")})

        # --- C2: Core itself is fine, and the P1 reads still answer ------------------------------
        status, core_health = direct("GET", "/v1/health", None, None)
        record("C2.core_health", "Core health stays ok while Memory is paused",
               status == 200 and core_health.get("status") == "ok",
               {"http": status, "status": core_health.get("status")})
        p1_paths = {
            "projects": "/v1/projects?limit=5",
            "task": f"/v1/tasks/{seeded['task_id']}",
            "events": f"/v1/events?task_id={seeded['task_id']}&limit=5",
            "rule": f"/v1/rules/{seeded['rule_id']}",
            "evidence": f"/v1/evidence/{seeded['evidence_id']}",
        }
        p1_rows: dict[str, int] = {}
        for name, path in p1_paths.items():
            status, payload = direct("GET", path, "human_manage", token)
            rows = 0
            if isinstance(payload, dict):
                for key in ("projects", "events", "task", "rule", "evidence"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        rows = len(value)
                    elif isinstance(value, dict):
                        rows = 1
            p1_rows[name] = rows if status == 200 else -1
        record("C3.p1_reads_unaffected",
               "every P1 read still answers while Memory is paused (HTTP 200, rows present)",
               all(value > 0 for value in p1_rows.values()), p1_rows)

        # --- C4: reflect degrades in its payload, and 200 is not read as success ------------------
        before = {
            "task_revision": revision_of(direct("GET", f"/v1/tasks/{seeded['task_id']}",
                                                "human_manage", token)[1], "task"),
            "rule_revision": revision_of(direct("GET", f"/v1/rules/{seeded['rule_id']}",
                                                "human_manage", token)[1], "rule"),
        }
        status, reflect = direct("POST", "/v1/memory/reflect", "human_manage", token,
                                 {"project_id": seeded["project_id"], "query": "t05 degraded slice",
                                  "host_id": descriptor["host_id"]})
        banks = reflect.get("banks") if isinstance(reflect, dict) else None
        reflect_state = {
            "http": status,
            "status": reflect.get("status") if isinstance(reflect, dict) else None,
            "error_code": reflect.get("error_code") if isinstance(reflect, dict) else None,
            "non_authoritative": reflect.get("non_authoritative") if isinstance(reflect, dict) else None,
            "based_on_memory_ids": reflect.get("based_on_memory_ids") if isinstance(reflect, dict) else None,
            "bank_statuses": [bank.get("status") for bank in banks] if isinstance(banks, list) else None,
        }
        record("C4.reflect_degrades_in_payload",
               "reflect answers 200 with a DEGRADED payload, recorded as a degradation not a success",
               status == 200 and reflect_state["status"] == "degraded"
               and reflect_state["error_code"] == "engine_unreachable"
               and reflect_state["non_authoritative"] is True
               and reflect_state["based_on_memory_ids"] == []
               and reflect_state["bank_statuses"] not in (None, [])
               and all(value == "failed" for value in reflect_state["bank_statuses"]),
               reflect_state)

        # --- C5/C6: recovery in the same environment, then read again -----------------------------
        if not control_url:
            for ident, check in (("C5.engine_restore", "the engine returns on the same address"),
                                 ("C6.recovery_health", "Memory reports recovery after the restore"),
                                 ("C7.recovery_reflect", "a real reflect reaches the restored engine")):
                record(ident, check, None, {"engine_control_url": control_url},
                       note="BLOCKED: this environment exposes no engine control channel, so the "
                            "recovery half could not run and nothing is fabricated in its place")
            recovery = None
        else:
            status, restore = call(control_url, "POST", "/restore", None)
            same_address = (isinstance(restore, dict) and restore.get("restored") is True
                            and restore.get("probe", {}).get("reachable") is True)
            record("C5.engine_restore", "the engine returns on the same address",
                   status == 200 and same_address,
                   {"http": status, "engine_url": descriptor["engine_url"],
                    "probe": restore.get("probe") if isinstance(restore, dict) else restore})
            status, health = direct("GET", "/v1/memory/health", "human_manage", token)
            recovered = health_subset(health)
            recovery = {"http": status, **recovered, "jobs": health.get("jobs")}
            record("C6.recovery_health",
                   "Memory reports recovery after the restore (reachable, not degraded, no impact)",
                   status == 200 and recovered["degraded"] is False
                   and recovered["core_impact"] == "none" and recovered["reachable"] is True
                   and recovered["last_error_code"] is None
                   and recovered["status"] in ("idle", "ok"),
                   recovery)
            status, reflect_ok = direct("POST", "/v1/memory/reflect", "human_manage", token,
                                        {"project_id": seeded["project_id"],
                                         "query": "recovery check", "host_id": descriptor["host_id"]})
            ok_banks = reflect_ok.get("banks") if isinstance(reflect_ok, dict) else None
            ok_state = {
                "http": status,
                "status": reflect_ok.get("status") if isinstance(reflect_ok, dict) else None,
                "error_code": reflect_ok.get("error_code") if isinstance(reflect_ok, dict) else None,
                "non_authoritative": (reflect_ok.get("non_authoritative")
                                      if isinstance(reflect_ok, dict) else None),
                "bank_statuses": ([bank.get("status") for bank in ok_banks]
                                  if isinstance(ok_banks, list) else None),
            }
            record("C7.recovery_reflect",
                   "a real reflect reaches the restored engine and reports no engine error",
                   status == 200 and ok_state["error_code"] is None
                   and ok_state["status"] != "degraded"
                   and ok_state["non_authoritative"] is True
                   and ok_state["bank_statuses"] not in (None, [])
                   and all(value == "ok" for value in ok_state["bank_statuses"]),
                   ok_state)

        # --- C8: no Task/Rule revision moved across the whole outage and recovery ----------------
        try:
            after = {
                "task_revision": revision_of(direct("GET", f"/v1/tasks/{seeded['task_id']}",
                                                    "human_manage", token)[1], "task"),
                "rule_revision": revision_of(direct("GET", f"/v1/rules/{seeded['rule_id']}",
                                                    "human_manage", token)[1], "rule"),
            }
        except ValueError as error:
            record("C8.no_truth_change", "a degraded and a recovered Memory call changed no Task "
                                         "revision or Rule", False, {"error": str(error)})
            after = None
        if after is not None:
            record("C8.no_truth_change",
                   "a degraded and a recovered Memory call changed no Task revision or Rule",
                   before == after, {"before": before, "after": after})

        # --- the browser pass -------------------------------------------------------------------
        web_work.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(["git", "-C", str(repo), "archive", args.dashboard_ref],
                                 check=True, capture_output=True)
        subprocess.run(["tar", "-x", "-C", str(web_work)], input=archive.stdout, check=True)
        os.symlink(args.node_modules, web_work / "web" / "node_modules")
        subprocess.run(["npx", "vite", "build"], cwd=web_work / "web", check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ui_env = dict(os.environ, P6_CORE_ORIGIN=origin, P6_ENABLE_P4_READS="1")
        serve_log = (out / "serve.stderr.txt").open("w")
        procs.append(subprocess.Popen(
            ["node", str(web_work / "web" / "server" / "serve.mjs"),
             "--root", str(web_work / "web" / "dist"), "--port", str(ui_port)],
            env=ui_env, stdout=subprocess.DEVNULL, stderr=serve_log))
        if not wait_for(f"http://127.0.0.1:{ui_port}/v1/health"):
            log("the dashboard host never answered")
            return 1
        events = direct("GET", f"/v1/events?task_id={seeded['task_id']}&limit=1",
                        "human_manage", token)[1]
        event_id = None
        if isinstance(events, dict) and isinstance(events.get("events"), list) and events["events"]:
            event_id = events["events"][0].get("event_id")
        expect = {"project_id": seeded["project_id"], "task_id": seeded["task_id"],
                  "rule_id": seeded["rule_id"], "evidence_id": seeded["evidence_id"],
                  "event_id": event_id}
        (out / "expect.json").write_text(json.dumps(expect, indent=2), encoding="utf-8")
        control_restore = "restored" if control_url else "not-available"
        trace = subprocess.run(
            ["node", str(REPO_ROOT / "scripts" / "p6" / "t05_degraded_trace.mjs"),
             "--ui", f"http://127.0.0.1:{ui_port}", "--tokens", str(env_dir / "tokens.json"),
             "--descriptor", str(env_dir / "descriptor.json"), "--expect", str(out / "expect.json"),
             "--control-state", control_restore, "--out", str(out)],
            stdout=(out / "trace.stdout.txt").open("w"), stderr=subprocess.STDOUT, text=True)
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
            "engine_url": descriptor["engine_url"],
            "engine_control_url": control_url,
            "engine_events": descriptor.get("engine_events"),
            "health_at_build": health_at_build,
            "core_entries": entries,
            "ui_records": ui_records,
            "calls": calls,
            "expect": expect,
            "summary": {
                "core_checks": len(entries), "ui_records": len(ui_records),
                "passed": len(entries) + len(ui_records) - len(blocked) - len(failed),
                "failed": len(failed), "blocked": len(blocked),
            },
        }
        (out / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        (out / "descriptor.json").write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
        write_readme(out, evidence)
        for token_name, token_value in tokens.items():
            found = subprocess.run(["grep", "-rlF", "--", token_value, str(out)],
                                   capture_output=True, text=True).stdout.split()
            if found:
                raise SystemExit(f"REFUSING TO FREEZE: token {token_name} appears in {found}")
        log(f"{evidence['summary']['passed']} passed, {evidence['summary']['failed']} failed, "
            f"{evidence['summary']['blocked']} blocked")
        if not failed and trace.returncode == 0 and not args.no_freeze:
            freeze(out, ref, evidence)
        return 1 if (failed or trace.returncode != 0) else 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if args.keep:
            log(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def write_readme(out: Path, evidence: dict[str, Any]) -> None:
    summary = evidence["summary"]
    health = evidence["health_at_build"] or {}
    lines = [
        "# P6-T05 degraded slice — Memory unavailable, Core and the P1 views still working, "
        "then recovered",
        "",
        f"Core from `{evidence['ref']}` (schema {evidence['schema_version']}), its own tree, database",
        "and loopback port, built with `--engine-state unavailable`: the wire-double engine is stopped",
        "and **P4's own probe** (`record_engine_probe`) recorded the refusal. The engine port is",
        "pinned by a harness shim so it can return on the **same address**, and a loopback-only",
        "control channel starts it again — the Core process is not restarted, so the recovery is read",
        "by the same Core that saw the outage.",
        "",
        f"Mode: `{MODE}`. `mode: boundary-fake` still means **real Core, simulated engine boundary**:",
        "the outage is a stopped loopback wire double, not a real Hindsight outage, so this is",
        "evidence about Core's and the dashboard's behaviour when Memory is unavailable — **not**",
        "about Hindsight, extraction or reflection, and not a P4 Gate verdict. P4 phase acceptance is",
        "not claimed and the live entry stays closed.",
        "",
        f"Result: **{summary['passed']}** passed, {summary['failed']} failed, "
        f"{summary['blocked']} blocked ({summary['core_checks']} Core checks + "
        f"{summary['ui_records']} browser records).",
        "",
        "## What this proves, and what it does not",
        "",
        "* **Outage:** `/v1/health` stays `ok` while `/v1/memory/health` reports `unreachable`",
        f"  (`degraded: {health.get('degraded')}`, `core_impact: {health.get('core_impact')}`,",
        f"  `last_error_code: {health.get('last_error_code')}`); every P1 read still answers with",
        "  rows; `POST /v1/memory/reflect` answers **HTTP 200 with a `degraded` payload**, recorded as",
        "  a degradation, never as a success.",
        "* **Recovery, same environment:** the engine returns on the same address and P4's probe",
        "  records `reachable: true`; `/v1/memory/health` then reports `degraded: false`,",
        "  `core_impact: none`, no error; a real `reflect` reaches the restored engine (`error_code:",
        "  null`, every bank `ok`). The Core process, database and ids are unchanged.",
        "* **No Truth moved:** the Task and Rule revisions are read from the layer they actually live",
        "  in (`task.revision` / `rule.revision`) and are compared before and after the whole outage",
        "  and recovery; a missing field fails the check instead of comparing two nulls.",
        "* **Browser:** the health panel states the pause **and** that Core keeps working, then states",
        "  the recovery; the Memory job and trace lists are never blanked; and Overview, Task,",
        "  Conversation, Rules and Evidence each render the object the page navigated to — a",
        "  **freshly observed** 200 on their own read, not a historical request — with no",
        "  dependency-pending panel and no dependency-failure alert.",
        "* It does **not** prove anything about a real Hindsight deployment, extraction or reflection",
        "  content, and it is not a P4 Gate verdict. The retry half of T05 is the real-Core matrix's.",
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
    lines += [
        "",
        "## Engine events, as the environment recorded them",
        "",
        "| at | action | health after | degraded | core impact |",
        "| --- | --- | --- | --- | --- |",
    ]
    for event in evidence.get("engine_events") or []:
        lines.append(f"| {event.get('at')} | {event.get('action')} | {event.get('health_status')} | "
                     f"{event.get('degraded')} | {event.get('core_impact')} |")
    lines += [
        "",
        "## Files",
        "",
        "* `evidence.json` — the Core checks with their request/response bodies, the browser records,",
        "  the engine's stop/restore event log, and every HTTP call (no Authorization header).",
        "* `descriptor.json` — the isolated Core's ids, the engine state and control URL, and",
        "  `health_at_build`.",
        "* `expect.json` — the ids the browser records require the current views to show.",
        "* `env.stderr.txt`, `serve.stderr.txt`, `trace.stdout.txt`, `click-trace.jsonl`,",
        "  `browser-requests.jsonl`, `screenshots/`.",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def freeze(out: Path, ref: str, evidence: dict[str, Any]) -> None:
    commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    health = evidence["health_at_build"] or {}
    dependency = json.dumps({
        "name": "P4 memory slice", "status": "NOT MERGED", "source_ref": ref,
        "acceptance": "P4 phase acceptance NOT passed",
        "consequence": "the Core is real P4 code; the engine is a labelled wire double that is "
                       f"stopped and then restarted on the same address, so the "
                       f"`{health.get('status')}` outage and the recovery are Core's own reactions "
                       "to that endpoint rather than a real Hindsight outage, and this is not a P4 "
                       "Gate verdict",
    })
    subprocess.run([sys.executable, str(FREEZE), "--freeze", str(out), "--commit", commit,
                    "--kind", "T05 degraded slice (real P4 Core, engine unavailable then restored)",
                    "--runs", "one pass: 9 Core checks + the browser pass, outage and recovery",
                    "--ran-from", "isolated real P4 Core in its own tree; dashboard from a git "
                                  "archive of the commit; wire double stopped, then restarted on the "
                                  "same pinned port without restarting the Core",
                    "--dependency-json", dependency], check=True)
    subprocess.run([sys.executable, str(FREEZE), "--verify", str(out)], check=True)


if __name__ == "__main__":
    raise SystemExit(main())
