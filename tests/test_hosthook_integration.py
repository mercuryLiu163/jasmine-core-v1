"""Local integration: the real `jasmine-core hook` CLI process against a real Core.

Layer: local integration only. The native payloads are constructed from docs and
fed to the CLI on stdin by this test; no Codex process, hook trust prompt or model
turn is involved, so nothing here shows native effect.

Part A runs the Core HTTP server in a thread (so a fake tokenizer can supply a
Context Pack). Part B starts `jasmine-core serve` as a separate process on a
scratch database in a temp dir.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from hosthook_support import HostHookHttp
from test_api import free_port
from test_hosthook_dialect_codex import fixture

ROOT = Path(__file__).resolve().parents[1]


def cli(*args: str, stdin: str | None = None, env: dict | None = None, cwd: str | None = None):
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})}
    return subprocess.run([sys.executable, "-m", "jasmine_core.cli", *args], input=stdin, text=True,
                          capture_output=True, env=environment, cwd=cwd, timeout=60)


class CliAgainstThreadedCore(HostHookHttp):
    def setUp(self) -> None:
        super().setUp()
        self.proj = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-cli-")
        self.addCleanup(self.proj.cleanup)
        self.dir = Path(self.proj.name).resolve()
        (self.dir / "private").mkdir(mode=0o700)
        for name, token in (("system.key", self.system_token), ("human.key", self.human_hook_token)):
            path = self.dir / "private" / name
            path.write_text(token)
            path.chmod(0o600)
        (self.dir / ".jasmine").mkdir()
        from test_api import HOST
        (self.dir / ".jasmine" / "host-binding.json").write_text(json.dumps({
            "version": 1, "core_url": f"http://127.0.0.1:{self.port}", "host_id": HOST,
            "project_id": self.project, "task_id": self.task, "step_id": self.step,
            "api_key_file": str(self.dir / "private" / "system.key"),
            "human_api_key_file": str(self.dir / "private" / "human.key")}))

    def run_hook(self, name: str, **overrides):
        native = {**fixture(name), "cwd": str(self.dir), **overrides}
        return cli("hook", "--agent", "codex", stdin=json.dumps(native), cwd=str(self.dir))

    def test_five_goals_through_the_cli_process(self) -> None:
        self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})
        # 1. the user's words, verbatim
        done = self.run_hook("user_prompt_submit")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        injected = json.loads(done.stdout)["hookSpecificOutput"]
        self.assertIn("Hook goal", injected["additionalContext"])  # 4. context text produced for the agent
        prompt = self.db_rows("SELECT payload_json FROM events WHERE event_type='user.prompt' AND source_system='host-hook-v1'")
        self.assertEqual(json.loads(prompt[0]["payload_json"])["text"], fixture("user_prompt_submit")["prompt"])
        # 2. a deny decision comes back in the native deny shape
        done = self.run_hook("pre_tool_use_bash")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        # 3. tool result -> Evidence with the trust level on its source event
        done = self.run_hook("post_tool_use_bash")
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        evidence = self.db_rows("SELECT e.status, v.payload_json FROM evidence e JOIN events v ON v.event_id=e.source_event_id")
        self.assertEqual(evidence[0]["status"], "PASS")
        self.assertEqual(json.loads(evidence[0]["payload_json"])["host_hook"]["trust"], "NATIVE_HOOK")
        # 5. checkpoints
        self.assertEqual(self.run_hook("pre_compact").returncode, 0)
        done = self.run_hook("stop")
        self.assertEqual((done.returncode, done.stdout), (0, "{}"))
        self.assertEqual(sorted(r["reason"] for r in self.db_rows("SELECT reason FROM checkpoints")),
                         ["PRE_COMPACT", "SESSION_STOP"])

    def test_tool_before_latency_is_measured_not_asserted_tightly(self) -> None:
        started = time.monotonic()
        done = self.run_hook("pre_tool_use_bash", tool_input={"command": "ls /elsewhere"})
        elapsed = time.monotonic() - started
        self.assertEqual(done.returncode, 0)
        self.assertLess(elapsed, 10.0)  # sanity only; the 300 ms p95 goal is not verified here
        print(f"\n[info] hook process tool.before wall time: {elapsed * 1000:.0f} ms (single run, not a p95)")

    def test_replay_subcommand(self) -> None:
        binding = self.dir / ".jasmine" / "host-binding.json"
        live = binding.read_text()
        binding.write_text(json.dumps({**json.loads(live), "core_url": f"http://127.0.0.1:{free_port()}"}))
        done = self.run_hook("post_tool_use_bash")
        self.assertIn("spooled for replay", done.stderr)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 0)
        binding.write_text(live)
        done = cli("hook", "replay", "--binding", str(binding), cwd=str(self.dir))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["sent"], 1)
        self.assertEqual(self.db_rows("SELECT count(*) AS n FROM evidence")[0]["n"], 1)
        again = cli("hook", "replay", "--binding", str(binding), cwd=str(self.dir))
        self.assertEqual(json.loads(again.stdout)["sent"], 0)

    def test_cli_usage_errors(self) -> None:
        self.assertEqual(cli("hook", cwd=str(self.dir)).returncode, 2)
        self.assertEqual(cli("hook", "--agent", "nobody", stdin="{}", cwd=str(self.dir)).returncode, 1)


class CliAgainstSeparateCoreProcess(unittest.TestCase):
    """`jasmine-core serve` as its own process on a scratch DB; no tokenizer, so Context degrades."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="jasmine-hosthook-e2e-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.db = self.dir / "scratch" / "core.db"
        self.db.parent.mkdir()
        self.workspace = self.dir / "workspace"
        self.workspace.mkdir()
        (self.workspace / "f.txt").write_text("x")
        self.env = {"JASMINE_CORE_DB": str(self.db), "JASMINE_CORE_WORKSPACE_ROOT": str(self.workspace),
                    "JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["f.txt"]'}
        self.assertEqual(cli("migrate", env=self.env).returncode, 0)

    def bootstrap(self, kind: str, scopes: list[str], host: str | None = None) -> dict:
        args = ["bootstrap", "--actor-kind", kind, "--label", kind, "--scopes", *scopes]
        if host:
            args += ["--host-id", host]
        done = cli(*args, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_hook_cli_to_separate_core_process(self) -> None:
        human = self.bootstrap("human", ["objects:write", "objects:read", "events:read", "state:write",
                                          "hosthook:report"])
        host = human["host_id"]
        system = self.bootstrap("system", ["hosthook:report"], host=host)
        port = free_port()
        server = subprocess.Popen([sys.executable, "-m", "jasmine_core.cli", "serve", "--port", str(port)],
                                  env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **self.env},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(server.wait, 10)
        self.addCleanup(server.terminate)
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/v1/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail("scratch Core did not start")

        def api(path: str, body: dict, token: str) -> dict:
            request = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(request, timeout=10))

        project = api("/v1/projects", {"name": "e2e", "host_id": host}, human["token"])["object"]["project_id"]
        task = api("/v1/tasks", {"project_id": project, "title": "e2e task", "host_id": host}, human["token"])["object"]["task_id"]
        (self.dir / "private").mkdir(mode=0o700)
        keys = {}
        for name, token in (("system", system["token"]), ("human", human["token"])):
            keys[name] = self.dir / "private" / f"{name}.key"
            keys[name].write_text(token)
            keys[name].chmod(0o600)
        (self.dir / ".jasmine").mkdir()
        (self.dir / ".jasmine" / "host-binding.json").write_text(json.dumps({
            "version": 1, "core_url": base, "host_id": host, "project_id": project, "task_id": task,
            "api_key_file": str(keys["system"]), "human_api_key_file": str(keys["human"])}))

        def run(name: str, **overrides):
            native = {**fixture(name), "cwd": str(self.dir), **overrides}
            return cli("hook", "--agent", "codex", stdin=json.dumps(native), cwd=str(self.dir), env=self.env)

        prompt = run("user_prompt_submit")
        self.assertEqual((prompt.returncode, prompt.stdout), (0, ""), prompt.stderr)  # context degraded: no tokenizer
        self.assertEqual(run("pre_tool_use_bash", tool_input={"command": "ls /elsewhere"}).stdout, "")
        self.assertEqual(run("post_tool_use_bash").returncode, 0)
        self.assertEqual(run("session_end").returncode, 0)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            text = json.loads(conn.execute("SELECT payload_json FROM events WHERE event_type='user.prompt'").fetchone()[0])["text"]
            self.assertEqual(text, fixture("user_prompt_submit")["prompt"])
            self.assertEqual(conn.execute("SELECT status FROM evidence").fetchone()[0], "PASS")
            degraded = json.loads(conn.execute("SELECT response_json FROM host_hook_deliveries WHERE event='prompt.submit'").fetchone()[0])["degraded"]
            self.assertEqual(len(degraded), 1)
            self.assertTrue(degraded[0].startswith("context_unavailable:"), degraded)
            self.assertEqual(conn.execute("SELECT count(*) FROM events WHERE event_type='session.ended'").fetchone()[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
