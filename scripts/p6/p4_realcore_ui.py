#!/usr/bin/env python3.13
"""The P6-03 **real-Core UI pass**: the dashboard's six writes, submitted from a browser, with a
real P4 Core behind it.

Read `web/REAL-CORE-ACCEPTANCE-PLAN.md`. The Core-level half of the matrix already passes
(`validation-runs/p6/*-p6-03-realcore/`, 35/35); this runner closes the half that was left: the
writes driven through the UI against real P4, plus the **withholding rule as the page shows it**.

What it starts, all isolated on loopback with its own directories and ports:

1. the real P4 Core, by `scripts/p6/p4_realcore_env.py` (pinned SHA, own `core.db`, own workspace,
   keys minted by P4, real jobs and items) with P4's `StubHindsightServer` as the wire-double engine;
2. a **drop proxy** in front of it. This exists for one reason: the replay case cannot be produced
   against a real Core from the browser, because a real Core answers. The proxy forwards every
   request to the real Core and, when armed, answers the browser with `502` **after** the Core has
   already applied the command — so the UI genuinely loses the reply, keeps its `event_id`, and the
   resubmit gets a **real** replay from real P4. The drop is the harness's, the replay is the
   Core's, and the bundle says which is which;
3. the dashboard, built from a `git archive` of `HEAD` with dependencies borrowed read-only, served
   by `web/server/serve.mjs` with `P6_ENABLE_P4_READS=1` and `P6_ENABLE_P4_WRITES=1` and
   `P6_CORE_ORIGIN` pointing at the drop proxy.

Then `scripts/p6/p4_realcore_ui_trace.mjs` drives the browser, and the bundle is frozen with
`scripts/p6/freeze_bundle.py` (which refuses to freeze a bundle containing a generated token).

    python3.13 scripts/p6/p4_realcore_ui.py --out validation-runs/p6/<stamp>-p6-03-realcore-ui \
        [--ref SHA] [--keep]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
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
DEFAULT_REF = "0c4f8f0e2118711ac9aa2a7006fddc6982962248"
FREEZE = REPO_ROOT / "scripts" / "p6" / "freeze_bundle.py"
FORWARDED = ("authorization", "content-type", "content-length", "accept", "x-request-id")


def log(message: str) -> None:
    print(f"p4-realcore-ui: {message}", file=sys.stderr, flush=True)


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class DropProxy:
    """Forward everything to the real Core; when armed, lose one reply on purpose.

    It is deliberately dumb: no allowlist (the dashboard's own proxy still enforces that), no
    rewriting, no caching. Its only behaviour beyond forwarding is the armed drop, and it records
    what it dropped so the bundle can show that the Core answered 200 while the browser saw 502.
    """

    def __init__(self, core_origin: str) -> None:
        parts = urllib.parse.urlsplit(core_origin)
        self.upstream = (parts.hostname or "127.0.0.1", parts.port or 80)
        self.armed_path: str | None = None
        self.dropped: list[dict[str, Any]] = []
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: D102 - keep the runner quiet
                return

            def _body(self) -> bytes:
                length = int(self.headers.get("content-length") or 0)
                return self.rfile.read(length) if length else b""

            def _handle(self, method: str) -> None:
                path = self.path
                if path.startswith("/v1/_p6_drop_next"):
                    from urllib.parse import parse_qs, urlsplit

                    armed = parse_qs(urlsplit(path).query).get("path", [None])[0]
                    proxy.armed_path = armed
                    payload = json.dumps({"armed": armed}).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                body = self._body()
                headers = {name: self.headers[name] for name in FORWARDED
                           if self.headers.get(name)}
                connection = http.client.HTTPConnection(*proxy.upstream, timeout=30)
                try:
                    connection.request(method, path, body=body, headers=headers)
                    reply = connection.getresponse()
                    reply_body = reply.read()
                    status = reply.status
                    reply_headers = list(reply.getheaders())
                finally:
                    connection.close()
                drop = (proxy.armed_path is not None and method == "POST"
                        and proxy.armed_path in path)
                if drop:
                    proxy.armed_path = None
                    proxy.dropped.append({"method": method, "path": path,
                                          "core_status": status,
                                          "core_body": _safe_json(reply_body)})
                    error = json.dumps({"error": {
                        "code": "proxy_core_unreachable",
                        "message": "the harness proxy dropped this reply on purpose"}}).encode()
                    self.send_response(502)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(error)))
                    self.end_headers()
                    self.wfile.write(error)
                    return
                self.send_response(status)
                for name, value in reply_headers:
                    if name.lower() in ("transfer-encoding", "connection", "content-length"):
                        continue
                    self.send_header(name, value)
                self.send_header("content-length", str(len(reply_body)))
                self.end_headers()
                self.wfile.write(reply_body)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self.port = free_port()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02},
                                       daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode())
    except Exception:  # noqa: BLE001 - evidence, not control flow
        return {"__unparsed": raw[:400].decode(errors="replace")}


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


def build_dashboard(work: Path, node_modules: Path) -> Path:
    """A dashboard built from `HEAD`'s own archive, never from a stale `dist`."""
    archive = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", "HEAD"],
                             check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", str(work)], input=archive.stdout, check=True)
    # `node_modules` is this function's parameter. The plumbing that made a fresh worktree
    # buildable changed the call site to pass `args.node_modules` but left the body reading the
    # enclosing `args`, which does not exist here: the whole UI pass died with a `NameError`
    # before it built anything. Assert the directory too — a symlink to nothing fails later, in
    # vite, where the cause is much harder to read.
    if not node_modules.is_dir():
        raise SystemExit(f"--node-modules {node_modules} is not a directory")
    os.symlink(node_modules, work / "web" / "node_modules")
    subprocess.run(["npx", "vite", "build"], cwd=work / "web", check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return work / "web" / "dist"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--keep", action="store_true", help="keep the temporary trees")
    parser.add_argument("--node-modules", type=Path, default=REPO_ROOT / "web" / "node_modules",
                        help="an existing node_modules to build the dashboard with (a fresh "
                             "worktree has none)")
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse the worktree's web/dist (debugging only)")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    core_port, ui_port = free_port(), free_port()
    tmp = Path(tempfile.mkdtemp(prefix="p6-realcore-ui-"))
    env_dir = tmp / "env"
    web_work = tmp / "web"
    procs: list[subprocess.Popen] = []
    proxy: DropProxy | None = None
    try:
        log(f"starting the real P4 Core (port {core_port})")
        env_log = (out / "env.stderr.txt").open("w")
        procs.append(subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "scripts" / "p6" / "p4_realcore_env.py"),
             "build", "--out", str(env_dir), "--ref", args.ref, "--port", str(core_port),
             "--max-ticks", "8"],
            stdout=subprocess.DEVNULL, stderr=env_log))
        if not wait_for(f"http://127.0.0.1:{core_port}/v1/health"):
            log("the Core never answered")
            return 1

        descriptor = json.loads((env_dir / "descriptor.json").read_text())
        tokens_path = env_dir / "tokens.json"
        tokens = json.loads(tokens_path.read_text())

        proxy = DropProxy(f"http://127.0.0.1:{core_port}")
        log(f"drop proxy on {proxy.origin} -> {core_port}")

        web_work.mkdir(parents=True, exist_ok=True)
        if args.skip_build:
            dist = REPO_ROOT / "web" / "dist"
        else:
            dist = build_dashboard(web_work, args.node_modules)
        log(f"dashboard built from HEAD's archive ({dist})")

        ui_env = dict(os.environ,
                      P6_CORE_ORIGIN=proxy.origin,
                      P6_ENABLE_P4_READS="1",
                      P6_ENABLE_P4_WRITES="1")
        serve_log = (out / "serve.stderr.txt").open("w")
        procs.append(subprocess.Popen(
            ["node", str(web_work / "web" / "server" / "serve.mjs"), "--root", str(dist),
             "--port", str(ui_port)],
            env=ui_env, stdout=subprocess.DEVNULL, stderr=serve_log))
        if not wait_for(f"http://127.0.0.1:{ui_port}/v1/health"):
            log("the dashboard host never answered")
            return 1

        (out / "descriptor.json").write_text(json.dumps(
            {k: v for k, v in descriptor.items() if k != "keys"}, indent=2), encoding="utf-8")
        trace_log = (out / "trace.stdout.txt").open("w")
        trace = subprocess.run(
            ["node", str(REPO_ROOT / "scripts" / "p6" / "p4_realcore_ui_trace.mjs"),
             "--ui", f"http://127.0.0.1:{ui_port}",
             "--core", f"http://127.0.0.1:{core_port}",
             "--proxy", proxy.origin,
             "--tokens", str(tokens_path),
             "--out", str(out)],
            stdout=trace_log, stderr=subprocess.STDOUT, text=True)
        trace_log.close()
        log(f"browser pass exit={trace.returncode}")

        (out / "proxy-drops.json").write_text(json.dumps(proxy.dropped, indent=2), encoding="utf-8")
        shutil.copyfile(REPO_ROOT / "web" / "REAL-CORE-ACCEPTANCE-PLAN.md", out / "plan.md")
        write_readme(out, descriptor, trace.returncode)
        assert_no_token(out, tokens)
        if trace.returncode == 0:
            freeze(out)
        return trace.returncode
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
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            log(f"kept {tmp}")


def write_readme(out: Path, descriptor: dict[str, Any], returncode: int) -> None:
    records: list[dict[str, Any]] = []
    trace_file = out / "click-trace.jsonl"
    if trace_file.is_file():
        records = [json.loads(line) for line in trace_file.read_text().splitlines() if line]
    passed = sum(1 for record in records if record["ok"])
    lines = [
        "# P6-03 real-Core UI pass — the six writes submitted from the browser",
        "",
        "Dashboard built from a `git archive` of `HEAD`; real P4 Core at the pinned commit",
        f"`{descriptor['ref']}` (schema {descriptor['schema_version']}) behind it; keys minted by P4",
        "itself; engine = P4's `StubHindsightServer` (wire double). `mode: real-core+wire`.",
        "",
        f"Result: **{passed}/{len(records)}** records passed (exit {returncode}).",
        "",
        "## What this proves, and what it does not",
        "",
        "* It proves the **dashboard** sends the frozen payloads to real P4 and renders real P4's",
        "  answers: a real 200 with the 7-key transition, a real `forbidden_scope` for a key without",
        "  `memory:manage`, a real `revision_conflict` with Core's current revision and no automatic",
        "  retry, a real replay after a lost reply, a real command Event read back, and `reflect`'s",
        "  eight keys with its real trace.",
        "* It proves the **withholding rule as the page shows it**: with a non-human key the engine",
        "  text is absent from the page and the withheld state is shown; with the human key it is",
        "  present. Both pages are recorded, and the responses they came from are in",
        "  `browser-write-responses.jsonl`.",
        "* It does **NOT** prove anything about a real Hindsight deployment (the engine is a wire",
        "  double), and P4 phase acceptance is not claimed.",
        "* The dropped reply in the replay case is the **harness proxy's**, not the Core's: the proxy",
        "  forwards the command, the Core applies it and answers 200, and only then does the proxy",
        "  answer the browser 502. `proxy-drops.json` records the Core's real status and body for the",
        "  dropped request, which is what makes the subsequent real replay meaningful.",
        "",
        "## Records",
        "",
        "| # | Record | Result |",
        "| --- | --- | --- |",
    ]
    for index, record in enumerate(records, start=1):
        mark = "PASS" if record["ok"] else "**FAIL**"
        lines.append(f"| {index} | {record['step']} | {mark} |")
    lines += [
        "",
        "## Files",
        "",
        "* `click-trace.jsonl` — the records with their details.",
        "* `browser-write-responses.jsonl` — every asserted write's request **and** response body,",
        "  straight from Chrome, with no Authorization header recorded.",
        "* `proxy-drops.json` — the harness drop, with the Core's real status and body for it.",
        "* `descriptor.json`, `env.stderr.txt`, `serve.stderr.txt`, `trace.stdout.txt`, `plan.md`.",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def assert_no_token(out: Path, tokens: dict[str, str]) -> None:
    for name, token in tokens.items():
        found = subprocess.run(["grep", "-rlF", "--", token, str(out)],
                               capture_output=True, text=True).stdout.split()
        if found:
            raise SystemExit(f"REFUSING TO FREEZE: token {name} appears in {found}")


def freeze(out: Path) -> None:
    commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    dependency = json.dumps({
        "name": "P4 memory slice", "status": "NOT MERGED", "source_ref": DEFAULT_REF,
        "acceptance": "P4 phase acceptance NOT passed",
        "consequence": "the Core and the routes are real P4 code; the engine is a wire double and "
                       "the dropped reply in the replay case is the harness proxy's, so this is not "
                       "a P4 Gate verdict",
    })
    subprocess.run([sys.executable, str(FREEZE), "--freeze", str(out), "--commit", commit,
                    "--kind", "real-core UI pass (isolated real P4 Core, wire-double engine)",
                    "--runs", "one browser pass", "--dependency-json", dependency], check=True)
    subprocess.run([sys.executable, str(FREEZE), "--verify", str(out)], check=True)


if __name__ == "__main__":
    raise SystemExit(main())
