#!/usr/bin/env python3.13
"""The P6-03c **Validation Inspector drill-down**: a real browser, through the real dashboard
proxy, against a real Core running the P7-01 Validation read slice over the operator's real
records (read-only) plus a clearly-labelled synthetic fixture root for the negatives the real
records cannot provide.

Read `scripts/p6/p7_realcore_env.py`, `scripts/p6/p7_realcore_ui_trace.mjs` and
`web/REAL-CORE-ACCEPTANCE-PLAN.md` §2 first. What this runner starts, all loopback and isolated:

1. the isolated real Core, built by `p7_realcore_env.py` from the P7-01 branch tip
   (`00d6040…`, extracted with `git archive` from `_worktrees/p7-01`);
2. **four** real Core processes over that one database, because the read root and the deployment
   mode are process configuration: `real` (the operator's tree, read-only, `single_user`),
   `fixture` (the labelled synthetic root, `single_user`), `unset` (no root, `single_user`) and
   `acl` (the operator's tree, `acl`);
3. a **harness switch proxy** in front of them, so one browser session can see all four. It is a
   router, not a second allowlist: the dashboard's own `core-paths.json` still decides what the
   browser may reach, and every forwarded request goes to a real Core;
4. the dashboard, built with `npm run build` in `web/` **from the working tree** (the P6-03c
   views are uncommitted, so a `git archive` of `HEAD` would not contain them), served by
   `web/server/serve.mjs` with `P6_ENABLE_P7_READS=1` and `P6_CORE_ORIGIN` pointed at the switch
   proxy.

Then `p7_realcore_ui_trace.mjs` drives Chrome, this runner verifies the Python-side facts (the
nine real records' digests, the criterion-1 census, and the disk-vs-API-vs-DOM verdict
comparison), and the bundle is written. Everything is torn down in a `finally`.

    python3.13 scripts/p6/p7_realcore_ui.py --out validation-runs/p6/<stamp>-p6-03c-validation-drilldown \
        [--ref SHA] [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from p7_realcore_env import (  # noqa: E402 - path setup must precede the import
    DEFAULT_P7_REPO,
    DEFAULT_REF,
    DEFAULT_REAL_RUNS,
    compare_real_tree,
    snapshot_real_tree,
)

ENV_SCRIPT = HERE / "p7_realcore_env.py"
TRACE_SCRIPT = HERE / "p7_realcore_ui_trace.mjs"

#: name -> (validation root, deployment mode). The root string is what `p7_realcore_env.py serve`
#: maps onto the operator's tree / the fixture tree / an unset variable.
CORE_PASSES: dict[str, tuple[str, str]] = {
    "real": ("real", "single_user"),
    "fixture": ("fixture", "single_user"),
    "unset": ("unset", "single_user"),
    "acl": ("real", "acl"),
}

FORWARDED = ("authorization", "content-type", "content-length", "accept", "x-request-id")

_LOG: list[str] = []


def log(message: str) -> None:
    line = f"p6-03c: {message}"
    print(line, file=sys.stderr, flush=True)
    _LOG.append(line)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reserve_ports(count: int) -> list[int]:
    """`count` distinct free loopback ports, bound simultaneously so none can collide."""
    sockets = []
    try:
        for _ in range(count):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            sockets.append(probe)
        return [int(probe.getsockname()[1]) for probe in sockets]
    finally:
        for probe in sockets:
            probe.close()


def wait_for(url: str, *, timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.25)
    return False


class SwitchProxy:
    """Route every request to whichever real Core the trace selected.

    Deliberately dumb: no allowlist (the dashboard's proxy still enforces one), no rewriting, no
    caching. Its one extra behaviour is the `/__p6/select` control route, which is a harness
    control plane on its own loopback port — never a Core route, and never reachable through the
    dashboard proxy because the dashboard only forwards `/v1/*`.
    """

    def __init__(self, origins: dict[str, str], port: int) -> None:
        self.upstreams = {name: urllib.parse.urlsplit(origin) for name, origin in origins.items()}
        self.current = "real"
        self.lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: D102 - keep the runner quiet
                return

            def _send(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method: str) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == "/__p6/select":
                    selected = urllib.parse.parse_qs(parsed.query).get("core", [None])[0]
                    with proxy.lock:
                        if selected not in proxy.upstreams:
                            self._send({"error": f"unknown core {selected!r}",
                                        "known": sorted(proxy.upstreams)}, 400)
                            return
                        proxy.current = selected
                        origin = proxy.upstreams[selected]
                    proxy.log_selection(selected)
                    self._send({"core": selected, "origin": f"http://{origin.netloc}"})
                    return
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {name: self.headers[name] for name in FORWARDED
                           if self.headers.get(name)}
                with proxy.lock:
                    upstream = proxy.upstreams[proxy.current]
                connection = http.client.HTTPConnection(upstream.hostname, upstream.port, timeout=30)
                try:
                    connection.request(method, self.path, body=body, headers=headers)
                    reply = connection.getresponse()
                    payload = reply.read()
                    status = reply.status
                    reply_headers = [(name, value) for name, value in reply.getheaders()
                                     if name.lower() not in ("transfer-encoding", "connection",
                                                             "content-length")]
                finally:
                    connection.close()
                self.send_response(status)
                for name, value in reply_headers:
                    self.send_header(name, value)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self.port = port
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.selections: list[dict[str, Any]] = []

    def log_selection(self, core: str) -> None:
        self.selections.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "core": core})

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# -- Python-side checks ---------------------------------------------------------------------------


def criterion1_census(real_runs: Path) -> dict[str, Any]:
    """Criterion 1 ("a real FAIL Run, classified, raw output traceable") — the census.

    Read straight from the operator's nine files, independent of the API and the UI.
    """
    census: dict[str, Any] = {
        "real_runs_root": str(real_runs),
        "records": 0, "run_verdicts": {"PASS": 0, "BLOCKED": 0, "FAIL": 0},
        "cases": 0, "case_verdicts": {"PASS": 0, "BLOCKED": 0, "FAIL": 0},
        "fail_cases": 0, "failure_class_non_null": 0, "failure_output_non_null": 0,
        "files": [],
    }
    for path in sorted(real_runs.rglob("run-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        census["records"] += 1
        census["run_verdicts"][data.get("verdict", "FAIL")] = \
            census["run_verdicts"].get(data.get("verdict"), 0) + 1
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


def verdict_comparison(real_runs: Path, observed_path: Path, responses_path: Path) -> dict[str, Any]:
    """The verdict in each file on disk vs the API's list/detail and vs the page's own text.

    The API side comes from `api-responses.jsonl` (Chrome's own view of every response) and the
    DOM side from `verdicts-observed.json`; neither is trusted alone.
    """
    disk: dict[str, str] = {}
    for path in sorted(real_runs.rglob("run-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        disk[data["run_id"]] = data["verdict"]
    observed = read_json(observed_path, {}) or {}
    dom_list = {row["runId"]: row["verdict"] for row in observed.get("listRows", [])}
    dom_details = observed.get("details", {})
    paginated = {run_id: verdict for run_id, verdict in observed.get("paginated", [])}
    api_list: dict[str, str] = {}
    api_details: dict[str, str] = {}
    api_manifest: dict[str, Any] = {}
    for entry in read_jsonl(responses_path):
        body = entry.get("body")
        if entry.get("status") != 200 or not isinstance(body, dict):
            continue
        path = entry.get("path", "")
        if path == "/v1/validation/runs" and isinstance(body.get("runs"), list):
            # The fixture pass lists the synthetic runs through the same route. Only a page whose
            # rows are all real records is a page of *this* comparison; mixing the two would make
            # the union check meaningless.
            if body["runs"] and not all(row.get("run_id") in disk for row in body["runs"]):
                continue
            for row in body["runs"]:
                api_list[row["run_id"]] = row["verdict"]
        elif path.startswith("/v1/validation/runs/") and path.count("/") == 4:
            if body.get("run_id") in disk:
                api_details[body["run_id"]] = body.get("verdict")
        elif path.endswith("/manifest") and body.get("run_id") in disk:
            api_manifest[body["run_id"]] = body
    rows = []
    for run_id in sorted(disk):
        row = {
            "run_id": run_id,
            "disk": disk[run_id],
            "api_list": api_list.get(run_id),
            "api_detail": api_details.get(run_id),
            "dom_list": dom_list.get(run_id),
            "dom_detail": dom_details.get(run_id),
            "paginated": paginated.get(run_id),
        }
        row["all_match"] = (all(row[key] is not None for key in (
            "disk", "api_list", "api_detail", "dom_list", "dom_detail", "paginated"))
            and len(set(row[key] for key in (
                "disk", "api_list", "api_detail", "dom_list", "dom_detail", "paginated"))) == 1)
        rows.append(row)
    nine = set(disk)
    union_ok = (set(dom_list) == nine and set(paginated) == nine and set(api_list) == nine)
    no_upgrade = all(not (row["disk"] == "BLOCKED"
                          and "PASS" in {row["api_list"], row["api_detail"], row["dom_list"],
                                         row["dom_detail"], row["paginated"]})
                     for row in rows)
    return {
        "rows": rows,
        "disk_run_ids": sorted(disk),
        "dom_list_run_ids": sorted(dom_list),
        "api_list_run_ids": sorted(api_list),
        "paginated_run_ids": sorted(paginated),
        "pagination_union_is_nine": union_ok,
        "no_verdict_upgraded": no_upgrade,
        "manifest_absent_authentication": {
            run_id: {"status": body.get("status"),
                     "manifest_authentication": body.get("manifest_authentication")}
            for run_id, body in api_manifest.items()},
    }


def assert_no_token(out: Path, tokens: dict[str, str]) -> list[str]:
    found: list[str] = []
    for name, token in tokens.items():
        for path in out.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if token in text:
                found.append(f"{name}:{path.relative_to(out).as_posix()}")
    return found


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Tolerant reader: a trace that crashed before writing a file must not crash the runner."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def digests_of(out: Path, exclude: set[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted((p for p in out.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        relative = path.relative_to(out).as_posix()
        if relative in exclude:
            continue
        entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return entries


# -- reporting ------------------------------------------------------------------------------------


def check_exit_code(*, trace_exit: int, trace_records: list[dict[str, Any]],
                    python_checks: list[dict[str, Any]], real_tree_unchanged: bool,
                    leaked: list[str], no_verdict_upgraded: bool) -> tuple[int, list[str]]:
    """The process exit code, derived only from recorded checks.

    Every check this run records must decide the exit code, or the code is not a gate. The
    original form consulted `trace_exit`, the tree comparison, the token scan and
    `no_verdict_upgraded` but **not** `python_checks`, so a run whose python checks failed
    still exited 0 (`result.md` and `manifest.json` recorded the FAIL, but a caller watching
    only the exit code saw success).

    Every check is one of three states, and they are not interchangeable:

    * `ok is True` — it passed;
    * `ok is False` — it **failed**, and the run must exit non-zero;
    * `ok is None` — it is **BLOCKED**, i.e. it could not be decided or the data itself is the
      finding (P7-01 criterion 1: there is no real FAIL run to read). A block the check declares
      in advance with `blocked_is_expected: True` is the documented outcome, so it does not fail
      the run — but it is reported, never silently counted as a pass. A block that was *not*
      declared is an unexpected one and does fail the run.

    Returns `(code, failures)`: `0` only when every browser record and every python check is a
    pass or a declared block and the preconditions hold; `1` otherwise, with one line per
    failure so the caller can print them.
    """
    def unresolved(entry: dict[str, Any]) -> str | None:
        """`None` if the entry is a pass or a declared block, else the failure description."""
        state = entry.get("ok")
        if state is True:
            return None
        name = entry.get("step", "<unnamed>")
        if state is None:
            if entry.get("blocked_is_expected") is True:
                return None
            return f"BLOCKED (not declared in advance): {name}"
        return f"FAIL: {name}"

    failures: list[str] = []
    if trace_exit != 0:
        failures.append(f"the browser trace process exited {trace_exit}")
    for record in trace_records:
        problem = unresolved(record)
        if problem is not None:
            failures.append(f"browser check {problem}")
    if not trace_records:
        failures.append("the browser trace recorded no checks at all")
    for check in python_checks:
        problem = unresolved(check)
        if problem is not None:
            failures.append(f"python check {problem}")
    if not python_checks:
        failures.append("no python checks were recorded")
    if not real_tree_unchanged:
        failures.append("the operator's real run tree changed during the run")
    if leaked:
        failures.append(f"a bearer token reached the bundle: {leaked}")
    if not no_verdict_upgraded:
        failures.append("a recorded verdict was upgraded")
    return (0 if not failures else 1), failures


def render_result(out: Path, *, trace_records: list[dict[str, Any]],
                  python_checks: list[dict[str, Any]], census: dict[str, Any],
                  verdicts: dict[str, Any], real_tree: dict[str, Any],
                  trace_exit: int, responses: list[dict[str, Any]],
                  artifact_observations: dict[str, Any]) -> str:
    passed = sum(1 for record in trace_records if record.get("ok") is True)
    failed = sum(1 for record in trace_records if record.get("ok") is False)
    blocked = sum(1 for record in trace_records if record.get("ok") is None)
    lines = [
        "# P6-03c — the Validation Inspector drill-down (`P6-T06` for the P7-01 read slice)",
        "",
        f"Browser trace: **{passed} PASS / {failed} FAIL / {blocked} BLOCKED** "
        f"(browser trace exit {trace_exit}).",
        f"Python-side checks: **{sum(1 for c in python_checks if c['ok'] is True)} PASS / "
        f"{sum(1 for c in python_checks if c['ok'] is False)} FAIL / "
        f"{sum(1 for c in python_checks if c['ok'] is None)} BLOCKED**.",
        "",
        "**This drill-down does not make P6 or P7 PASS.** P7-01's acceptance criterion 1 "
        "(read an existing real FAIL Run, classified, raw output traceable) is BLOCKED on the "
        "operator's own records: the census below finds 0 FAIL cases, 0 `failure_class` and 0 "
        "`failure_output`. The FAIL code path is exercised only by the clearly-labelled synthetic "
        "fixture, which is not evidence and counts toward no criterion.",
        "",
        "## Per-check outcomes",
        "",
        "| Check | Result | Evidence |",
        "| --- | --- | --- |",
    ]
    for check in python_checks:
        mark = "BLOCKED" if check["ok"] is None else ("PASS" if check["ok"] else "**FAIL**")
        lines.append(f"| python: {check['step']} | {mark} | {check.get('evidence', '')} |")
    for record in trace_records:
        mark = ("BLOCKED" if record.get("ok") is None
                else ("PASS" if record.get("ok") else "**FAIL**"))
        detail = str(record.get("detail") or "").replace("|", "\\|")[:300]
        lines.append(f"| {record['step']} | {mark} | `click-trace.jsonl` — {detail} |")
    lines += [
        "",
        "## Criterion 1 — independently confirmed BLOCKED",
        "",
        f"Nine real records under the operator's tree ({census['real_runs_root']}); run verdicts "
        f"{census['run_verdicts']}; {census['cases']} cases with verdicts "
        f"{census['case_verdicts']}; **{census['fail_cases']} FAIL cases, "
        f"{census['failure_class_non_null']} non-null `failure_class`, "
        f"{census['failure_output_non_null']} non-null `failure_output`**. The real-FAIL-run path "
        "is **unmet**; it is exercised only by the fixture run `run-20260101T000002Z`, which "
        "carries `synthetic: true` and a note saying it is not evidence. This drill-down is "
        "`P6-T06` evidence for the read slice and does **not** close criterion 1 and does **not** "
        "make the P6 or P7 phase PASS.",
        "",
        "## Verdict integrity (disk vs Core vs page)",
        "",
        "| run_id | file | Core list | Core detail | DOM list | DOM detail | paged fetch | match |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in verdicts["rows"]:
        lines.append(
            f"| `{row['run_id']}` | {row['disk']} | {row['api_list']} | {row['api_detail']} | "
            f"{row['dom_list']} | {row['dom_detail']} | {row['paginated']} | "
            f"{'yes' if row['all_match'] else '**NO**'} |")
    lines += [
        "",
        f"* pagination union (limit=3, offsets 0/3/6/9) is exactly the nine: "
        f"**{verdicts['pagination_union_is_nine']}**",
        f"* no verdict upgraded (no PASS anywhere the file says BLOCKED): "
        f"**{verdicts['no_verdict_upgraded']}**",
        f"* real records' mtimes/digests unchanged before and after: "
        f"**{real_tree['unchanged']}** (`real-tree-before.json`, `real-tree-after.json`)",
        "",
        "## Error classification",
        "",
        "| code | status | occurrences | classification |",
        "| --- | --- | --- | --- |",
    ]
    counts: dict[tuple[Any, Any], int] = {}
    for entry in responses:
        code = entry.get("error_code")
        if code:
            counts[(code, entry.get("status"))] = counts.get((code, entry.get("status")), 0) + 1
    classifications = {
        "forbidden_scope": "expected refusal — a key without `validation:read` (the refusal pair)",
        "validation_run_not_found": "expected refusal — acl: ungranted run and nonexistent run "
                                    "share one code; also the fixture manifest-absent artifact path",
        "validation_not_configured": "expected refusal — Pass C, the run root is unset",
        "original_withheld": "expected refusal — the assignment-shaped secret's original form",
        "unsupported_media_type": "expected refusal — the binary fixture has no text form",
        "artifact_not_found": "expected refusal — acl/absent-manifest artifact addressing",
    }
    for (code, status), count in sorted(counts.items(), key=lambda item: str(item[0])):
        lines.append(f"| `{code}` | {status} | {count} | "
                     f"{classifications.get(code, '**UNCLASSIFIED — investigate**')} |")
    if not counts:
        lines.append("| (none) | — | 0 | no error responses were observed |")
    lines += [
        "",
        "## Unverified scope, stated rather than implied",
        "",
        "* **P7 criterion 1 is BLOCKED** (above). No real FAIL run, `failure_class` or "
        "`failure_output` exists on the operator's tree; the fixture FAIL run is labelled and "
        "counted nowhere.",
        "* **No project-attributed real run exists.** All nine real records are `unattributed`, so "
        "the `acl` grant-filtered list can only show *all nine* (with `validation:unattributed`) or "
        "*zero* (without it); a partial, project-scoped subset is not demonstrable on real data. "
        "The single grant row the `acl` process needs is seeded by the harness on a project the "
        "real records do not reference.",
        "* **The withheld-original reason is only available by probing.** Core's artifact *metadata* "
        "route reports `forms` and the digests but carries no `original_withheld_reason`; only the "
        "content route with `form=original` answers `403 original_withheld` with `details.reason` "
        "(`mask_not_noop` / `credential_shaped_declared` / `not_utf8`). The Inspector therefore "
        "renders the not-offered state with an explicit probe — "
        "`请求 original(预期被拒绝,以显示原因)` — whose only effect is one documented GET that "
        "Core answers 403; the panel then shows the real `details.reason` and "
        "`details.masked_sha256`, with the masked body still on screen. **The probe is a UI "
        "affordance, not a contract guarantee**: `web/contracts/p7-validation-read.v1.json` does "
        "not require the metadata route to expose the reason, and a future contract that did "
        "(for example an `original_withheld_reason` field on the artifact metadata response) would "
        "let the UI state the reason without a request. This is a recorded contract request, not a "
        "validated contract property.",
        f"  Observed in this run: `{json.dumps(artifact_observations.get('withheldUi'))[:400]}`",
        "* **The dashboard is built from a dirty working tree.** The P6-03c views "
        "(`web/src/views/ValidationViews.tsx`, `ValidationDependency.tsx` and the touched "
        "`App.tsx`/router/endpoint files) are uncommitted, so `npm run build` from `web/` — not a "
        "`git archive` of `HEAD` — is what produced `web/dist`. The manifest records the exact "
        "`git status --porcelain` and the dist digests.",
        "* **There is no engine, no Hindsight and no write path here.** The slice is read-only; "
        "nothing in this run can speak to a live deployment, to P7 phase acceptance, or to the "
        "recorder changes the ADR excludes (the unmarked-mocked-`agent_response` residual).",
        "* **Chrome runs headless**; screenshots are of the real page but no human read them as "
        "part of this run.",
        "",
        "## Files",
        "",
        "* `manifest.json` — refs, versions, times, mode, real-tree digests and the bundle's own "
        "file digests.",
        "* `scenario.md` — what was run, and the honesty boundary.",
        "* `click-trace.jsonl` — every browser check with its detail.",
        "* `api-requests.jsonl` / `api-responses.jsonl` — de-identified traffic "
        "(`request_id`, method, path, status, error code; the `Authorization` value is never "
        "recorded, only `present(redacted)`/`absent`).",
        "* `screenshots/*.png` — the pages the checks looked at.",
        "* `criterion1-census.json`, `verdict-comparison.json`, `real-tree-before.json`, "
        "`real-tree-after.json`, `pagination-observed.json`, `verdicts-observed.json`, "
        "`artifact-observations.json`, `acl-observations.json`, `fixture-observations.json`.",
        "* `descriptor.json` (no keys), `env-build.stderr.txt`, `dashboard-build.stdout.txt`, "
        "`core-*.stderr.txt`, `serve.stderr.txt`, `trace.stdout.txt`, `run.stdout.txt`, "
        "`chrome-version.json`, `isolation.json`.",
        "",
    ]
    return "\n".join(lines)


def render_scenario(out: Path, descriptor: dict[str, Any], real_runs: Path,
                    p7_repo: Path) -> str:
    return "\n".join([
        "# Scenario — P6-03c Validation Inspector drill-down",
        "",
        "## What was driven",
        "",
        "* A real Chrome (headless, CDP) loaded the dashboard served by `web/server/serve.mjs` "
        "with `P6_ENABLE_P7_READS=1`.",
        "* The browser's requests went through the dashboard's own proxy allowlist "
        "(`web/server/core-paths.json`, `p7_pending_paths`) to a harness switch proxy and then to "
        "a real Core built from the P7-01 branch tip "
        f"`{descriptor['ref']}` (worktree `{p7_repo}`), extracted with `git archive`.",
        "* Four real Core processes answered, one per configuration: `real` (the operator's tree, "
        "`single_user`), `fixture` (the labelled synthetic root, `single_user`), `unset` (no run "
        "root, `single_user`) and `acl` (the operator's tree, `acl`).",
        "",
        "## The three passes",
        "",
        "**Pass A — the operator's real records (read-only).** The list is walked with `limit=3` "
        "by the page's own fetch (the Inspector's page size is fixed at 20), the union is checked "
        "against the nine file names, each row's verdict is compared with the file on disk, all "
        "nine detail views are opened for the eight T06 links and the three Context forms, the "
        "manifest view is opened for the `absent`/`unauthenticated` state, the `acl` grant filter "
        "is exercised with and without `validation:unattributed`, and a key without "
        "`validation:read` is refused `403 forbidden_scope`.",
        "",
        "**Pass B — the labelled fixture root.** The fixture tree is inside the harness's temp "
        "directory and every record carries `synthetic: true` plus a note saying it is not "
        "evidence. It provides the four negatives the real records cannot: a frozen manifest with "
        "a good text artifact, an assignment-shaped secret (`token=x`: `looks_like_credential` is "
        "False and `canonical.redact_text` masks it), an HTML artifact and a binary; a corrupt "
        "record; a duplicate stem; and a synthetic `FAIL` run with a `failure_class`.",
        "",
        "**Pass C — misconfiguration.** With `JASMINE_CORE_VALIDATION_RUNS` unset, the Inspector "
        "must show `503 validation_not_configured` (`details.reason: unset`) rather than an empty "
        "list.",
        "",
        "## The honesty boundary",
        "",
        "* The real records are **read-only**: the nine files' mtimes and SHA-256 digests are "
        "snapshotted before the build, after the build and after the browser pass; any change "
        "fails the bundle.",
        "* The switch proxy is a **router, not a second allowlist**: the dashboard's proxy still "
        "decides what the browser may reach, and the switch proxy only chooses which real Core "
        "answers.",
        "* The fixture root is **not evidence** and is not counted toward criterion 1. Criterion 1 "
        "(""a real FAIL Run"") stays BLOCKED.",
        "* No bearer token is written anywhere in this bundle; the two API-level probes the trace "
        "makes from the page hold the token in a page global for one call and delete it "
        "immediately.",
        "",
        f"Real run root: `{real_runs}`",
        f"Fixture root (temp, removed at teardown): `{descriptor['fixture_root']}`",
        "",
    ])


# -- runner ---------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--repo", type=Path, default=DEFAULT_P7_REPO)
    parser.add_argument("--real-runs", type=Path, default=DEFAULT_REAL_RUNS)
    parser.add_argument("--keep", action="store_true", help="keep the temporary trees")
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse web/dist instead of running `npm run build` (debugging only)")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "screenshots").mkdir(exist_ok=True)
    started = time.time()
    port_ui, port_proxy, port_real, port_fixture, port_unset, port_acl = reserve_ports(6)
    tmp = Path(tempfile.mkdtemp(prefix="p6-03c-drilldown-"))
    env_dir = tmp / "env"
    procs: list[subprocess.Popen] = []
    proxy: SwitchProxy | None = None
    trace_exit = 1
    try:
        log(f"building the isolated P7-01 environment (ref {args.ref})")
        with (out / "env-build.stderr.txt").open("w") as env_log:
            built = subprocess.run(
                [sys.executable, str(ENV_SCRIPT), "build", "--out", str(env_dir),
                 "--ref", args.ref, "--repo", str(args.repo), "--real-runs", str(args.real_runs)],
                stdout=env_log, stderr=subprocess.STDOUT, text=True)
        if built.returncode != 0:
            log("the environment build failed")
            (out / "run.stdout.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")
            return 1
        descriptor = json.loads((env_dir / "descriptor.json").read_text(encoding="utf-8"))
        tokens = json.loads((env_dir / "tokens.json").read_text(encoding="utf-8"))
        before = descriptor["real_tree_before"]
        (out / "real-tree-before.json").write_text(json.dumps(before, indent=2) + "\n",
                                                   encoding="utf-8")

        origins = {
            "real": f"http://127.0.0.1:{port_real}",
            "fixture": f"http://127.0.0.1:{port_fixture}",
            "unset": f"http://127.0.0.1:{port_unset}",
            "acl": f"http://127.0.0.1:{port_acl}",
        }
        cores = {"real": port_real, "fixture": port_fixture, "unset": port_unset, "acl": port_acl}
        for name, port in cores.items():
            root, mode = CORE_PASSES[name]
            core_log = (out / f"core-{name}.stderr.txt").open("w")
            procs.append(subprocess.Popen(
                [sys.executable, str(ENV_SCRIPT), "serve", "--out", str(env_dir),
                 "--port", str(port), "--root", root, "--mode", mode],
                stdout=subprocess.DEVNULL, stderr=core_log))
            if not wait_for(f"http://127.0.0.1:{port}/v1/health", timeout=90):
                log(f"the {name} Core never answered on {port}")
                (out / "run.stdout.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")
                return 1
            log(f"core {name} up on {port} (root={root}, mode={mode})")

        proxy = SwitchProxy(origins, port_proxy)
        log(f"switch proxy on {proxy.origin} -> real/fixture/unset/acl Cores")

        if args.skip_build:
            dist = REPO_ROOT / "web" / "dist"
        else:
            log("building the dashboard with `npm run build` in web/ (working tree)")
            with (out / "dashboard-build.stdout.txt").open("w") as build_log:
                build = subprocess.run(["npm", "run", "build"], cwd=REPO_ROOT / "web",
                                       stdout=build_log, stderr=subprocess.STDOUT, text=True)
            if build.returncode != 0:
                log("the dashboard build failed")
                (out / "run.stdout.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")
                return 1
            dist = REPO_ROOT / "web" / "dist"

        ui_env = dict(os.environ, P6_CORE_ORIGIN=proxy.origin, P6_ENABLE_P7_READS="1")
        serve_log = (out / "serve.stderr.txt").open("w")
        procs.append(subprocess.Popen(
            ["node", str(REPO_ROOT / "web" / "server" / "serve.mjs"), "--root", str(dist),
             "--port", str(port_ui)],
            env=ui_env, stdout=subprocess.DEVNULL, stderr=serve_log))
        if not wait_for(f"http://127.0.0.1:{port_ui}/v1/health"):
            log("the dashboard host never answered")
            (out / "run.stdout.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")
            return 1
        log(f"dashboard on http://127.0.0.1:{port_ui} (bundle {dist})")

        trace_log = (out / "trace.stdout.txt").open("w")
        trace = subprocess.run(
            ["node", str(TRACE_SCRIPT),
             "--ui", f"http://127.0.0.1:{port_ui}", "--proxy", proxy.origin,
             "--tokens", str(env_dir / "tokens.json"),
             "--descriptor", str(env_dir / "descriptor.json"), "--out", str(out)],
            stdout=trace_log, stderr=subprocess.STDOUT, text=True)
        trace_log.close()
        trace_exit = trace.returncode
        log(f"browser pass exit={trace_exit}")
        (out / "switch-selections.json").write_text(json.dumps(proxy.selections, indent=2) + "\n",
                                                    encoding="utf-8")

        # --- Python-side facts ---
        after = snapshot_real_tree(args.real_runs)
        (out / "real-tree-after.json").write_text(json.dumps(after, indent=2) + "\n",
                                                  encoding="utf-8")
        real_tree = compare_real_tree(before, after)
        (out / "real-tree-comparison.json").write_text(json.dumps(real_tree, indent=2) + "\n",
                                                       encoding="utf-8")
        census = criterion1_census(args.real_runs)
        (out / "criterion1-census.json").write_text(json.dumps(census, indent=2) + "\n",
                                                    encoding="utf-8")
        verdicts = verdict_comparison(args.real_runs, out / "verdicts-observed.json",
                                      out / "api-responses.jsonl")
        (out / "verdict-comparison.json").write_text(json.dumps(verdicts, indent=2) + "\n",
                                                     encoding="utf-8")
        # F6: this used to be a hard-coded `False`. It is now the measurement it claimed to be:
        # the fixture's run ids are compared against the ids the criterion-1 census actually
        # counted, and a non-empty intersection would make the flag true and fail the check.
        fixture_ids = sorted(set(descriptor["fixture"]["runs"].values()))
        real_ids = sorted({path.name[:-5] for path in args.real_runs.rglob("run-*.json")})
        overlap = sorted(set(fixture_ids) & set(real_ids))
        fixture_census_note = {
            "fixture_root": descriptor["fixture_root"],
            "fixture_run_ids": fixture_ids,
            "note": descriptor["fixture"]["note"],
            "census_run_ids": real_ids,
            "fixture_ids_inside_census": overlap,
            "counted_toward_criterion_1": bool(overlap),
            "measurement": "the shipped fixture ids are disjoint from the ids the census read from "
                           "--real-runs, so no fixture record can contribute to criterion 1",
        }
        (out / "fixture-census.json").write_text(json.dumps(fixture_census_note, indent=2) + "\n",
                                                 encoding="utf-8")
        (out / "isolation.json").write_text(json.dumps({
            "real_runs_root": descriptor["real_runs_root"],
            "real_tree_before": before,
            "real_tree_after": after,
            "real_tree_unchanged": real_tree["unchanged"],
            "core_processes": {name: {"root": CORE_PASSES[name][0], "mode": CORE_PASSES[name][1],
                                      "origin": origin} for name, origin in origins.items()},
            "switch_proxy": proxy.origin,
            "dashboard_origin": f"http://127.0.0.1:{port_ui}",
            "temp_root_removed": not args.keep,
        }, indent=2) + "\n", encoding="utf-8")

        public_descriptor = {key: value for key, value in descriptor.items() if key != "keys"}
        (out / "descriptor.json").write_text(json.dumps(public_descriptor, indent=2) + "\n",
                                             encoding="utf-8")

        trace_records = read_jsonl(out / "click-trace.jsonl")
        responses = read_jsonl(out / "api-responses.jsonl")
        artifact_observations = read_json(out / "artifact-observations.json", {})

        (out / "scenario.md").write_text(
            render_scenario(out, descriptor, args.real_runs, args.repo), encoding="utf-8")
        log(f"bundle written to {out}")
        (out / "run.stdout.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")

        # F7: everything the bundle will contain now exists except manifest.json, so the token
        # scan covers what it claims to cover. It is written here, immediately before the
        # manifest that binds it, and nothing else is written afterwards.
        leaked = assert_no_token(out, tokens)
        (out / "token-scan.json").write_text(json.dumps(
            {"tokens_in_bundle": leaked, "clean": not leaked,
             "scanned_files": sorted(path.relative_to(out).as_posix()
                                     for path in out.rglob("*") if path.is_file()
                                     and path.name != "manifest.json"),
             "not_scanned": ["manifest.json", "result.md", "token-scan.json"],
             "note": "no bearer token may appear in any bundle file. Everything written before "
                     "this point was scanned; `result.md` cannot be scanned by the scan it "
                     "reports (it is written immediately afterwards and is generated only from "
                     "the already-scanned observations), and `manifest.json` is the binder "
                     "written last."},
            indent=2) + "\n", encoding="utf-8")

        python_checks = [
            {"step": "the browser trace wrote its records (click-trace.jsonl exists)",
             "ok": (out / "click-trace.jsonl").is_file() and len(trace_records) > 0,
             "evidence": "click-trace.jsonl"},
            {"step": "the operator's nine records are byte-identical (mtime + sha256) before and "
                     "after the whole run",
             "ok": real_tree["unchanged"],
             "evidence": "real-tree-before.json, real-tree-after.json, real-tree-comparison.json"},
            {"step": "criterion 1 census: 0 FAIL cases, 0 failure_class, 0 failure_output on the "
                     "real tree",
             # BLOCKED is the documented finding (there is no real FAIL run to read), so it is
             # declared here as the expected outcome. It still prints as BLOCKED in result.md and
             # is never counted as a pass; the exit code stays 0 for a run whose only non-pass is
             # this declared block.
             "ok": None if census["criterion_1"] == "BLOCKED" else False,
             "blocked_is_expected": census["criterion_1"] == "BLOCKED",
             "evidence": "criterion1-census.json (BLOCKED is the finding, not a pass)"},
            {"step": "every verdict matches disk, and no PASS appears where the file says BLOCKED",
             "ok": verdicts["pagination_union_is_nine"] and verdicts["no_verdict_upgraded"]
                   and all(row["all_match"] for row in verdicts["rows"]),
             "evidence": "verdict-comparison.json"},
            {"step": "no bearer token appears in any bundle file",
             "ok": not leaked,
             "evidence": "token-scan.json"},
            {"step": "the fixture is not counted toward criterion 1",
             # F6: this reads the measurement rather than asserting it. `counted_toward_criterion_1`
             # is true exactly when a fixture run id appears among the ids the census counted, so
             # this check fails if that ever happens.
             "ok": not fixture_census_note["counted_toward_criterion_1"],
             "detail": f"fixture ids inside the census: "
                       f"{fixture_census_note['fixture_ids_inside_census']}",
             "evidence": "fixture-census.json"},
        ]
        (out / "result.md").write_text(
            render_result(out, trace_records=trace_records, python_checks=python_checks,
                          census=census, verdicts=verdicts, real_tree=real_tree,
                          trace_exit=trace_exit, responses=responses,
                          artifact_observations=artifact_observations),
            encoding="utf-8")
        _LOG.append(f"p6-03c: bundle written to {out}")
        git_head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
        git_status = [line for line in subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True).stdout.splitlines() if line]
        p7_head = subprocess.run(["git", "-C", str(args.repo), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
        versions = {
            "python": sys.version.split()[0],
            "node": subprocess.run(["node", "--version"], capture_output=True,
                                   text=True).stdout.strip(),
            "chrome": json.loads((out / "chrome-version.json").read_text(encoding="utf-8"))
            if (out / "chrome-version.json").is_file() else None,
        }
        dist_files = {}
        for path in sorted(dist.rglob("*")) if dist.is_dir() else []:
            if path.is_file():
                dist_files[path.relative_to(dist).as_posix()] = {
                    "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        manifest = {
            "bundle": "p6-03c-validation-drilldown",
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": round(time.time() - started, 1),
            "mode": {
                "core": "real-p7-01 (extracted with git archive from the P7-01 worktree)",
                "engine": "none — the Validation slice is read-only",
                "validation_root": "operator tree (read-only) for Pass A and the acl pass; a "
                                   "labelled synthetic fixture root for the Pass B negatives",
                "phase_acceptance": "NOT claimed: P7-01 criterion 1 stays BLOCKED (no real FAIL "
                                    "run exists on the operator's tree), so this drill-down does "
                                    "not make the P6 or P7 phase PASS",
            },
            "core": {"ref": descriptor["ref"], "branch_tip": descriptor["ref"],
                     "repo": descriptor["repo"], "schema_version": descriptor["schema_version"]},
            "dashboard": {
                "worktree": str(REPO_ROOT),
                "git_head": git_head,
                "git_status_porcelain": git_status,
                "dirty_note": "built with `npm run build` in web/ from the working tree, because "
                              "the P6-03c views are uncommitted; a git archive of HEAD would not "
                              "contain them",
                "dist_files": dist_files,
            },
            "p7_worktree_head": p7_head,
            "versions": versions,
            "times": {"started_epoch": int(started),
                      "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                      "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            "inputs": {"real_runs_root": descriptor["real_runs_root"],
                       "fixture_root": descriptor["fixture_root"],
                       "fixture_note": descriptor["fixture"]["note"],
                       "core_origins": origins, "ui_origin": f"http://127.0.0.1:{port_ui}"},
            "real_tree": {"records": len(before), "unchanged": real_tree["unchanged"],
                          "changed": real_tree["changed"]},
            "criterion_1": census["criterion_1"],
            "trace": {"exit": trace_exit,
                      "steps": {"pass": sum(1 for r in trace_records if r.get("ok") is True),
                                "fail": sum(1 for r in trace_records if r.get("ok") is False),
                                "blocked": sum(1 for r in trace_records if r.get("ok") is None)}},
            "python_checks": [{"step": c["step"],
                               "result": "BLOCKED" if c["ok"] is None
                               else ("PASS" if c["ok"] else "FAIL")} for c in python_checks],
        }
        manifest["files"] = digests_of(out, exclude={"manifest.json"})
        manifest["integrity_note"] = (
            "`files[]` carries bytes + sha256 for every bundle file except `manifest.json` itself "
            "(a file cannot hash itself). Recompute and compare; no file is written to this bundle "
            "after this manifest.")
        manifest_path = out / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # Last line of the run goes to the console only: nothing may be written to the bundle after
        # the manifest that binds it.
        print(f"p6-03c: bundle {out} — {len(manifest['files'])} files bound by manifest.json, "
              f"trace exit {trace_exit}, criterion 1 {census['criterion_1']}", flush=True)
        code, failures = check_exit_code(
            trace_exit=trace_exit, trace_records=trace_records, python_checks=python_checks,
            real_tree_unchanged=real_tree["unchanged"], leaked=leaked,
            no_verdict_upgraded=verdicts["no_verdict_upgraded"])
        if failures:
            # Console only, like the line above: nothing may be written to the bundle after the
            # manifest that binds it. The same failures are in result.md.
            print(f"p6-03c: FAILING — {len(failures)} check(s) did not pass:", flush=True)
            for failure in failures:
                print(f"p6-03c:   - {failure}", flush=True)
        else:
            print("p6-03c: every recorded check passed", flush=True)
        return code
    finally:
        if proxy is not None:
            proxy.stop()
        for process in procs:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if not args.keep:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            log(f"kept {tmp}")
        if not (out / "run.stdout.txt").exists():
            (out / "run.stdout.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
