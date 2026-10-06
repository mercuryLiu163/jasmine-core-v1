"""Native SessionStart receipts: the hook, the installer and the Gate binding.

The P4 taskbook asks for an actual SessionStart *injection receipt* per agent
session. The session JSON is written by an orchestrator and shows nothing about a
native hook; the native receipt binds the exact injected pack bytes, the session
id, the source and the installed script by content. It cannot prove *which
process* ran the hook -- the hook is an ordinary script -- so whether Codex or a
human ran it is an operator attestation recorded in the Gate manifest under
`native_session_start`, not a property the Gate verifies.

What is inside this file, and what is not:

  * `ComponentHook` pipes synthetic, real-shaped payloads into the hook script
    through a subprocess. These are **component tests, not SessionStart events**:
    tests/ code cannot fire a real Codex SessionStart, and no test here claims
    otherwise.
  * `InstallerPlanAndApply` drives the installer's `--plan`/`--apply` contract and
    proves it never writes trust state.
  * `NativeSessionStartVerification` drives the real `Gate.verify_session` with
    synthetic receipt fixtures, each labelled as a component fixture, and proves
    every reverse case is refused by name.
  * `InstalledHooksShapeMatchesTheOfficialContract` is a drift test: the installed
    hooks.json must keep the documented Codex shape.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import import_gate_module

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "scripts" / "jasmine-p4-session-start.py"
GATE = REPO / "scripts" / "p4-memory-gate.py"
INSTALLER = REPO / "scripts" / "p4-codex-hook-install.py"
PYTHON = sys.executable
RENDERED = "JASMINE CONTEXT v1\n{}\nMEMORY — NON AUTHORITATIVE\n内存\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=60,
                          **kwargs)


def _installer_module():
    import importlib.util

    name = "p4_codex_hook_install"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# -- the hook script, as a component -------------------------------------------

class ComponentHook(unittest.TestCase):
    """Synthetic payloads through the real hook script.

    NOT a real SessionStart: these payloads are written by this test and piped in
    by hand. They prove the script's contract -- receipt bytes, stdout JSON,
    write-once and hash-mismatch refusals -- and nothing about Codex.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p4-native-hook-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        (self.repo / ".codex" / "jasmine").mkdir(parents=True)
        self.pack_id = "ctx_" + "A" * 26
        self.task_id = "tsk_" + "B" * 26
        self.rendered = RENDERED
        self.digest = _sha256(self.rendered.encode("utf-8"))
        self._write_injection()

    def _write_injection(self, *, digest=None, rendered=None, rendered_path=None,
                         rendered_sha256=None) -> None:
        rendered = self.rendered if rendered is None else rendered
        name = f"rendered-{self.pack_id}.txt"
        (self.repo / ".codex" / "jasmine" / name).write_bytes(rendered.encode("utf-8"))
        body = {
            "context_pack_id": self.pack_id, "task_id": self.task_id,
            "rendered_path": (f".codex/jasmine/{name}" if rendered_path is None
                              else rendered_path),
            "rendered_sha256": (self.digest if digest is None else digest),
        }
        if rendered_sha256 is not None:
            body["rendered_sha256"] = rendered_sha256
        (self.repo / ".codex" / "jasmine" / "injection.json").write_text(
            json.dumps(body), encoding="utf-8")

    def _payload(self, **overrides) -> dict:
        payload = {"hook_event_name": "SessionStart", "source": "startup",
                   "session_id": "sess-0001", "transcript_path": None,
                   "cwd": str(self.repo), "model": "gpt-5-codex",
                   "permission_mode": "default"}
        payload.update(overrides)
        return payload

    def _hook(self, payload):
        return _run([PYTHON, str(HOOK)], input=json.dumps(payload))

    def _receipt_path(self, session_id="sess-0001") -> Path:
        return (self.repo / ".codex" / "jasmine"
                / f"session-start-{session_id}.json")

    def test_a_valid_payload_injects_the_exact_pack_and_writes_the_receipt(self):
        completed = self._hook(self._payload())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        stdout = json.loads(completed.stdout)
        self.assertEqual(stdout["hookSpecificOutput"]["hookEventName"], "SessionStart")
        additional = stdout["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(additional, self.rendered)
        path = self._receipt_path()
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["hook_event_name"], "SessionStart")
        self.assertEqual(receipt["source"], "startup")
        self.assertEqual(receipt["session_id"], "sess-0001")
        self.assertIsNone(receipt["transcript_path"])
        self.assertEqual(receipt["cwd"], str(self.repo))
        self.assertEqual(receipt["model"], "gpt-5-codex")
        self.assertEqual(receipt["permission_mode"], "default")
        self.assertEqual(receipt["context_pack_id"], self.pack_id)
        self.assertEqual(receipt["task_id"], self.task_id)
        self.assertEqual(receipt["rendered_path"], f".codex/jasmine/rendered-{self.pack_id}.txt")
        self.assertEqual(receipt["pack_sha256"], self.digest)
        self.assertEqual(receipt["injected_sha256"],
                         _sha256(additional.encode("utf-8")))
        self.assertEqual(receipt["injected_bytes"], len(additional.encode("utf-8")))
        self.assertEqual(receipt["injected_via"],
                         "hookSpecificOutput.additionalContext")
        self.assertEqual(receipt["hook_script_sha256"], _sha256(HOOK.read_bytes()))
        self.assertIsInstance(receipt["written_at"], str)
        # Supporting evidence, never a proof: the parent's name is recorded when
        # `ps` can answer, and may be absent.
        self.assertIn("parent_process_name", receipt)
        self.assertTrue(receipt["parent_process_name"] is None
                        or isinstance(receipt["parent_process_name"], str))

    def test_a_resume_receipt_records_its_source(self):
        completed = self._hook(self._payload(source="resume"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(self._receipt_path().read_text(encoding="utf-8"))
        self.assertEqual(receipt["source"], "resume")

    def test_a_non_session_start_payload_is_ignored(self):
        for payload in (self._payload(hook_event_name="UserPromptSubmit"),
                        self._payload(source="elsewhere")):
            with self.subTest(payload=payload):
                completed = self._hook(payload)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "")
                self.assertFalse(self._receipt_path().exists())

    def test_an_absent_injection_is_a_silent_noop(self):
        (self.repo / ".codex" / "jasmine" / "injection.json").unlink()
        completed = self._hook(self._payload())
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")
        self.assertFalse(self._receipt_path().exists())

    def test_a_mismatched_pack_is_refused_without_receipt_or_injection(self):
        self._write_injection(digest="0" * 64)
        completed = self._hook(self._payload())
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("mismatched pack", completed.stderr)
        self.assertFalse(self._receipt_path().exists())

    def test_a_second_trigger_with_the_same_pack_reinjects_without_rewriting(self):
        # Codex fires SessionStart again when a session resumes. The pack must be
        # injected again -- the resumed session needs it -- and the write-once
        # receipt must not be rewritten, because it is the record of what the
        # first trigger actually injected.
        first = self._hook(self._payload(source="startup"))
        self.assertEqual(first.returncode, 0, first.stderr)
        path = self._receipt_path()
        before = path.read_bytes()
        second = self._hook(self._payload(source="resume"))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            json.loads(second.stdout)["hookSpecificOutput"]["additionalContext"],
            self.rendered)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(path.parent.glob("session-start-*.json"))), 1)

    def test_a_second_trigger_with_a_different_pack_is_refused(self):
        first = self._hook(self._payload())
        self.assertEqual(first.returncode, 0, first.stderr)
        path = self._receipt_path()
        before = path.read_bytes()
        # The staging changes to another render under the same session: the
        # session already read the first pack, and its receipt is write-once.
        changed = self.rendered + "a different pack\n"
        self._write_injection(rendered=changed, digest=_sha256(changed.encode("utf-8")))
        second = self._hook(self._payload())
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stdout, "")
        self.assertIn("another injection", second.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_a_receipt_edited_to_another_injection_refuses_the_retrigger(self):
        first = self._hook(self._payload())
        self.assertEqual(first.returncode, 0, first.stderr)
        path = self._receipt_path()
        body = json.loads(path.read_bytes())
        body["injected_sha256"] = "0" * 64
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        edited = path.read_bytes()
        second = self._hook(self._payload())
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stdout, "")
        self.assertIn("another injection", second.stderr)
        self.assertEqual(path.read_bytes(), edited)

    def test_an_absolute_rendered_path_is_refused(self):
        # A staged `injection.json` that names `/etc/hosts` would inject a file
        # the repository does not contain and receipt it as this repo's pack.
        self._write_injection(rendered_path="/etc/hosts",
                              rendered_sha256=_sha256(b"whatever"))
        completed = self._hook(self._payload())
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("not a relative path", completed.stderr)
        self.assertFalse(self._receipt_path().exists())

    def test_a_traversal_rendered_path_is_refused(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_bytes(self.rendered.encode("utf-8"))
        self._write_injection(
            rendered_path=f"../../{outside.name}",
            rendered_sha256=_sha256(self.rendered.encode("utf-8")))
        completed = self._hook(self._payload())
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("not a relative path", completed.stderr)
        self.assertFalse(self._receipt_path().exists())

    def test_a_symlink_escape_is_refused(self):
        # `rendered_path` itself is relative and has no `..`, but the file it
        # names is a symlink to content outside the repository: the resolved
        # path is what has to stay inside.
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_bytes(self.rendered.encode("utf-8"))
        link = self.repo / ".codex" / "jasmine" / "escape.txt"
        link.symlink_to(outside)
        self._write_injection(rendered_path=".codex/jasmine/escape.txt",
                              rendered_sha256=_sha256(self.rendered.encode("utf-8")))
        completed = self._hook(self._payload())
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("outside the repository", completed.stderr)
        self.assertFalse(self._receipt_path().exists())


# -- the installer --------------------------------------------------------------

class InstallerPlanAndApply(unittest.TestCase):
    """`--plan` changes nothing; `--apply` is idempotent; neither writes trust."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p4-native-install-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo with space"
        self.repo.mkdir()
        self.home = self.base / "home"
        (self.home / ".codex").mkdir(parents=True)
        self.trust = self.home / ".codex" / "config.toml"
        self.trust.write_text("# operator trust store; must never change\n",
                              encoding="utf-8")
        self.env = dict(os.environ, HOME=str(self.home))

    def _install(self, *args):
        return _run([PYTHON, str(INSTALLER), "--repo", str(self.repo),
                     "--python", PYTHON, *args], env=self.env)

    def _hooks(self) -> Path:
        return self.repo / ".codex" / "hooks.json"

    def _script(self) -> Path:
        return self.repo / ".codex" / "jasmine" / "jasmine-p4-session-start.py"

    def _trust_bytes(self):
        return (self.trust.read_bytes(),
                sorted(p.name for p in (self.home / ".codex").iterdir()))

    def test_plan_writes_nothing_and_prints_the_trust_steps(self):
        before = self._trust_bytes()
        completed = self._install("--plan")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse((self.repo / ".codex").exists(),
                         "--plan created the project-local .codex directory")
        self.assertEqual(self._trust_bytes(), before)
        stdout = completed.stdout
        self.assertIn("result: would-write", stdout)
        self.assertIn(f"repo: {self.repo.resolve()}", stdout)
        self.assertIn("/hooks", stdout)
        self.assertIn("--dangerously-bypass-hook-trust", stdout)
        self.assertIn("never writes trust state", stdout)
        self.assertIn("--probe", stdout)
        self.assertIn("codex exec", stdout)
        self.assertIn(f"cd {self.repo.resolve()} && codex", stdout.replace("'", ""))
        self.assertIn("startup|resume|clear|compact", stdout)
        self.assertIn("additionalContextLimit", stdout)

    def test_apply_is_idempotent_and_binds_an_absolute_command(self):
        before = self._trust_bytes()
        first = self._install("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("result: written", first.stdout)
        self.assertIn("hook_script_write: created", first.stdout)
        document = json.loads(self._hooks().read_text(encoding="utf-8"))
        self.assertEqual(set(document["hooks"]), {"SessionStart"})
        entries = document["hooks"]["SessionStart"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "startup|resume|clear|compact")
        hook = entries[0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(shlex.split(hook["command"]),
                         [PYTHON, str(self.repo.resolve() / ".codex" / "jasmine"
                                       / "jasmine-p4-session-start.py")])
        self.assertEqual(hook["additionalContextLimit"], 0)
        self.assertEqual(hook["timeout"], 30)
        self.assertTrue(self._script().is_file())
        self.assertEqual(self._script().read_bytes(), HOOK.read_bytes())
        first_bytes = self._hooks().read_bytes()
        second = self._install("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("result: unchanged", second.stdout)
        self.assertIn("hook_script_write: unchanged", second.stdout)
        self.assertEqual(self._hooks().read_bytes(), first_bytes)
        self.assertEqual(self._trust_bytes(), before)

    def test_a_foreign_binding_in_the_existing_hooks_json_is_refused(self):
        foreign = self.repo / ".codex" / "jasmine" / "jasmine-p4-session-start.py"
        (self.repo / ".codex").mkdir()
        self._hooks().write_text(json.dumps({
            "description": "someone else's binding",
            "hooks": {"SessionStart": [{
                "matcher": "startup", "hooks": [
                    {"type": "command",
                     "command": f"/other/python {shlex.quote(str(foreign))}",
                     "timeout": 5}]}]}}), encoding="utf-8")
        stale = self._hooks().read_bytes()
        before = self._trust_bytes()
        completed = self._install("--apply")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("explicit operator migration required", completed.stdout)
        self.assertEqual(self._hooks().read_bytes(), stale)
        self.assertFalse(self._script().exists())
        self.assertEqual(self._trust_bytes(), before)

    def test_a_hook_from_another_tool_is_preserved(self):
        (self.repo / ".codex").mkdir()
        other = {"type": "command", "command": "echo other"}
        self._hooks().write_text(json.dumps({
            "description": "shared with another tool",
            "hooks": {"SessionStart": [
                {"matcher": "startup", "hooks": [other]}]}}), encoding="utf-8")
        completed = self._install("--apply")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entries = json.loads(self._hooks().read_text(encoding="utf-8")
                             )["hooks"]["SessionStart"]
        self.assertEqual(entries[0], {"matcher": "startup", "hooks": [other]})
        self.assertEqual(entries[1]["hooks"][0]["type"], "command")

    def test_apply_never_touches_the_trust_store(self):
        before = self._trust_bytes()
        completed = self._install("--apply")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self._trust_bytes(), before)
        self.assertNotIn("trust_changed: true", completed.stdout)


class StagingTheInjection(unittest.TestCase):
    """The Gate writes what the hook reads, and the two halves meet."""

    def setUp(self) -> None:
        self.module = import_gate_module()
        self.gate = _real_gate(self, case_id="P4-STAGE")
        self.repo = Path(self.gate.root.parent.parent) / "session-repo"
        (self.repo / ".codex" / "jasmine").mkdir(parents=True)
        self.gate.config["native_session_repo"] = str(self.repo)
        self.rendered = RENDERED
        self.pack = {"context_pack_id": "ctx_" + "S" * 26, "task_id": "tsk_" + "T" * 26,
                     "rendered_content": self.rendered,
                     "rendered_sha256": _sha256(self.rendered.encode("utf-8"))}

    def test_staging_writes_the_pointer_and_the_exact_bytes(self):
        staged = self.gate.stage_native_injection(self.pack)
        injection = json.loads(Path(staged["injection_path"]).read_text(encoding="utf-8"))
        self.assertEqual(injection["context_pack_id"], self.pack["context_pack_id"])
        self.assertEqual(injection["task_id"], self.pack["task_id"])
        self.assertEqual(injection["rendered_sha256"], self.pack["rendered_sha256"])
        self.assertEqual(injection["rendered_path"],
                         f".codex/jasmine/rendered-{self.pack['context_pack_id']}.txt")
        self.assertEqual(Path(staged["rendered_path"]).read_bytes(),
                         self.rendered.encode("utf-8"))

    def test_staged_bytes_are_write_once(self):
        self.gate.stage_native_injection(self.pack)
        self.gate.stage_native_injection(self.pack)
        changed = {**self.pack, "rendered_content": self.rendered + "changed\n",
                   "rendered_sha256": _sha256((self.rendered + "changed\n").encode("utf-8"))}
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.stage_native_injection(changed)
        self.assertIn("write-once", str(caught.exception))

    def test_a_staged_pack_runs_through_the_hook(self):
        staged = self.gate.stage_native_injection(self.pack)
        shutil.copy2(HOOK, self.repo / ".codex" / "jasmine"
                     / "jasmine-p4-session-start.py")
        completed = _run([PYTHON, str(HOOK)], input=json.dumps({
            "hook_event_name": "SessionStart", "source": "startup",
            "session_id": "sess-staged", "transcript_path": None,
            "cwd": str(self.repo), "model": None, "permission_mode": None}))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads((self.repo / ".codex" / "jasmine"
                              / "session-start-sess-staged.json").read_text(
                                  encoding="utf-8"))
        self.assertEqual(receipt["injected_sha256"], self.pack["rendered_sha256"])
        relative = (Path(".codex") / "jasmine"
                    / Path(staged["rendered_path"]).name).as_posix()
        self.assertEqual(receipt["rendered_path"], relative)
        self.assertEqual(json.loads(completed.stdout)["hookSpecificOutput"]
                         ["additionalContext"], self.rendered)

    def test_staging_without_a_repository_is_blocked(self):
        self.gate.config.pop("native_session_repo")
        self.gate.config.pop("g01_historical_fixture", None)
        with self.assertRaises(self.module.Blocked) as caught:
            self.gate.stage_native_injection(self.pack)
        self.assertIn("native_session_repo", str(caught.exception))


class StagingTheInjectionCli(unittest.TestCase):
    """`--stage-injection` is the runnable producer of the hook's pointer.

    Before this, `stage_native_injection` was called only by tests: nothing on a
    real path wrote the `injection.json` the hook reads, so the native flow could
    not run end to end. This drives the real CLI against a real work directory
    holding a real pack file.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p4-native-stage-cli-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.work = self.base / "work"
        self.case = "P4-G01"
        self.case_dir = self.work / self.case
        self.case_dir.mkdir(parents=True)
        self.pack_key = "g01-with-lesson"
        self.rendered = RENDERED
        self.pack = {
            "idempotency_key": self.pack_key,
            "context_pack_id": "ctx_" + "C" * 26,
            "task_id": "tsk_" + "D" * 26,
            "rendered_content": self.rendered,
            "rendered_sha256": _sha256(self.rendered.encode("utf-8")),
        }
        self.pack_path = self.case_dir / f"pack-{self.pack['context_pack_id']}.json"
        self.pack_path.write_text(json.dumps(self.pack, ensure_ascii=False),
                                  encoding="utf-8")
        self.repo = self.base / "session-repo"
        (self.repo / ".codex" / "jasmine").mkdir(parents=True)
        self.config = self.base / "op.json"
        self.config.write_text(json.dumps({"run_id": "p4-stage-cli",
                                           "bank_suffix": "p4-stage-cli"}),
                               encoding="utf-8")

    def _run(self, *extra):
        return _run([PYTHON, str(GATE), "--stage-injection", "--work", str(self.work),
                     "--case", self.case, "--pack-key", self.pack_key,
                     "--config", str(self.config),
                     "--native-session-repo", str(self.repo), *extra])

    def test_the_cli_writes_the_pointer_and_the_exact_bytes(self):
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["result"], "staged")
        injection = json.loads((self.repo / ".codex" / "jasmine"
                                / "injection.json").read_text(encoding="utf-8"))
        self.assertEqual(injection["context_pack_id"], self.pack["context_pack_id"])
        self.assertEqual(injection["rendered_sha256"], self.pack["rendered_sha256"])
        rendered_file = self.repo / injection["rendered_path"]
        self.assertEqual(rendered_file.read_bytes(), self.rendered.encode("utf-8"))

    def test_the_cli_refuses_a_missing_native_session_repo_as_an_input_error(self):
        # "the staging inputs cannot be used" includes a repository that does not
        # resolve to a directory: that is the operator's configuration, not a
        # missing pack, and the exit codes have to say which.
        missing = self.base / "no-such-repo"
        completed = self._run("--native-session-repo", str(missing))
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertIn("repository", completed.stdout)
        self.assertFalse((self.repo / ".codex" / "jasmine"
                          / "injection.json").exists())

    def test_the_cli_refuses_a_pack_whose_bytes_do_not_match_its_digest(self):
        body = json.loads(self.pack_path.read_text(encoding="utf-8"))
        body["rendered_content"] = body["rendered_content"] + "tampered\n"
        self.pack_path.write_text(json.dumps(body), encoding="utf-8")
        completed = self._run()
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("recorded digest", completed.stdout)
        self.assertFalse((self.repo / ".codex" / "jasmine"
                          / "injection.json").exists())


# -- a real Gate over a throwaway Core, as in test_p4_fix_round.py -------------

def _real_gate(testcase, *, case_id="P4-TST"):
    from jasmine_core import db, ids, registry
    from jasmine_core.migrations import migrate

    module = import_gate_module()
    tmp = tempfile.TemporaryDirectory(prefix="p4-native-gate-")
    testcase.addCleanup(tmp.cleanup)
    root = Path(tmp.name) / "work" / case_id
    root.mkdir(parents=True)
    workspace = Path(tmp.name) / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "source.txt").write_text("v1\n", encoding="utf-8")
    gate = module.Gate.__new__(module.Gate)
    gate.root = root
    gate.case_id = case_id
    gate.workspace = workspace
    gate.host = ids.new_id("hst")
    gate.human = ids.new_id("act")
    gate.system = ids.new_id("act")
    gate.agent = ids.new_id("act")
    gate.db_path = root / "core.db"
    gate.config = {"run_id": "p4-native", "bank_suffix": "p4-native"}
    gate.run_id = "p4-native"
    gate.bank_suffix = "p4-native"
    gate.transport = None
    gate._previous = {}
    conn = db.connect(gate.db_path)
    migrate(conn)
    with db.transaction(conn):
        reg = registry.Registry(conn)
        reg.upsert_host(gate.host, display_name="p4 native session start")
        for actor_id, kind in ((gate.human, "human"), (gate.system, "system"),
                               (gate.agent, "agent")):
            reg.upsert_actor(actor_id, kind=kind, home_host_id=gate.host)
    conn.close()
    environment = mock.patch.dict("os.environ", {
        "JASMINE_CORE_WORKSPACE_ROOT": str(workspace),
        "JASMINE_CORE_FINGERPRINT_EXTRA_PATHS": '["source.txt"]'}, clear=False)
    environment.start()
    testcase.addCleanup(environment.stop)
    return gate


def _insert_pack_row(gate, *, pack_id, project, task, rendered, truth_digest,
                     command_event, source_event):
    from jasmine_core import db

    columns = ("context_pack_id, command_event_id, source_event_id, actor_id, host_id,"
               " project_id, task_id, reason, trigger_provenance_json, projection_version,"
               " projection_json, truth_digest, resume_comparison_json, workspace_json,"
               " workspace_digest, sample_window_json, config_digest, renderer_version,"
               " rendered_content, rendered_sha256, rendered_utf8_bytes, tokenizer_json,"
               " token_count, section_metrics_json, omissions_json, memory_json,"
               " full_result_json, created_at").split(", ")
    values = (pack_id, command_event, source_event, gate.agent, gate.host, project,
              task, "USER_PROMPT", "{}", "jasmine.task-snapshot.v1", "{}",
              truth_digest, "{}", "{}", "a" * 64, "{}", "b" * 64,
              "jasmine.context.v1", rendered,
              _sha256(rendered.encode("utf-8")),
              len(rendered.encode("utf-8")), "{}", 0, "[]", "[]", "{}", "{}",
              "2026-01-01T00:00:00.000000Z")
    assert len(columns) == len(values), (len(columns), len(values))
    conn = gate.conn()
    try:
        with db.transaction(conn):
            conn.execute(
                f"INSERT INTO context_packs ({', '.join(columns)})"
                f" VALUES ({', '.join('?' * len(values))})", values)
    finally:
        conn.close()


# -- the Gate binds the native receipt to the session --------------------------

class NativeSessionStartVerification(unittest.TestCase):
    """`require_native_session_start` binds a session to Codex's own receipt.

    Every receipt written in this class is a **synthetic component fixture**:
    tests/ code cannot fire a real SessionStart. They exist so the real verifier
    can be driven, including through every reverse case the fix must refuse.
    """

    def setUp(self) -> None:
        self.module = import_gate_module()
        self.gate = _real_gate(self, case_id="P4-G01")
        self.project = self.gate.seed_project("native")
        self.task = self.gate.new_task(self.project, "native session start")
        self.prompt = self.gate.event(self.project, "user.prompt", "go",
                                      task_id=self.task)
        self.rendered = RENDERED
        self.pack_id = "ctx_" + "N" * 26
        self.repo = Path(self.gate.root.parent.parent) / "session-repo"
        self.directory = self.repo / ".codex" / "jasmine"
        self.directory.mkdir(parents=True)
        self.script = self.directory / "jasmine-p4-session-start.py"
        shutil.copy2(HOOK, self.script)
        self.gate.config["require_native_session_start"] = True
        self.gate.config["native_session_repo"] = str(self.repo)
        _insert_pack_row(self.gate, pack_id=self.pack_id, project=self.project,
                         task=self.task, rendered=self.rendered, truth_digest="d" * 64,
                         command_event=self.prompt, source_event=self.prompt)

    def _pack(self):
        return {"context_pack_id": self.pack_id, "project_id": self.project,
                "task_id": self.task, "prompt_event_id": self.prompt,
                "memory_status": "empty", "memory_selected": [],
                "memory_filtered": [], "qualified_provenance": [],
                "truth_digest": "d" * 64, "check_current": True,
                "rendered_content": self.rendered,
                "rendered_sha256": _sha256(self.rendered.encode("utf-8")),
                "sections": [], "token_count": 0,
                "receipt": {"path": None, "sha256": None}}

    def _component_receipt(self, **overrides) -> dict:
        """A synthetic native receipt fixture. NOT a real SessionStart."""
        receipt = {
            "hook_event_name": "SessionStart", "source": "startup",
            "session_id": "native-session-1", "transcript_path": None,
            "cwd": str(self.repo), "model": "gpt-5-codex",
            "permission_mode": "default", "context_pack_id": self.pack_id,
            "task_id": self.task,
            "rendered_path": f".codex/jasmine/rendered-{self.pack_id}.txt",
            "pack_sha256": _sha256(self.rendered.encode("utf-8")),
            "injected_sha256": _sha256(self.rendered.encode("utf-8")),
            "injected_bytes": len(self.rendered.encode("utf-8")),
            "injected_via": "hookSpecificOutput.additionalContext",
            "hook_script_sha256": _sha256(self.script.read_bytes()),
            "written_at": "2026-10-05T00:00:00Z",
        }
        receipt.update(overrides)
        return receipt

    def _write_receipt(self, receipt=None, session_id=None) -> Path:
        body = receipt if receipt is not None else self._component_receipt()
        if session_id is None:
            session_id = body.get("session_id") or "native-session-1"
        path = self.directory / f"session-start-{session_id}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _session(self, **overrides) -> dict:
        session = {"commit": self.gate.git_commit(),
                   "context_pack_id": self.pack_id,
                   "pack_sha256": _sha256(self.rendered.encode("utf-8")),
                   "memory_seen": [], "tool_actions": [],
                   "native_session_id": "native-session-1"}
        session.update(overrides)
        return session

    def _verify(self, session=None, receipt=None, label="t1"):
        self.gate.session_path(label).write_text(
            json.dumps(session if session is not None else self._session(),
                       ensure_ascii=False), encoding="utf-8")
        self._write_receipt(receipt)
        return self.gate.verify_session(self._pack(), label,
                                        require_tool_actions=False)

    def test_a_symlinked_receipt_directory_is_refused(self):
        # Staging refuses a symlinked .codex/jasmine; verification accepting one
        # would make "inside the repository" a claim about a name rather than about
        # where the bytes live.
        real = self.repo / ".codex" / "jasmine-real"
        self.directory.rename(real)
        self.directory.symlink_to(real, target_is_directory=True)
        with self.assertRaises(self.module.Failed) as caught:
            self._verify()
        self.assertIn("symlinked", str(caught.exception))

    def test_a_matching_native_receipt_binds_the_session(self):
        verified = self._verify()
        native = verified["verified"]["native_session_start"]
        self.assertIsNotNone(native)
        self.assertEqual(native["write"], "created")
        sealed = Path(native["path"])
        self.assertEqual(sealed.stat().st_mode & 0o777, 0o600)
        copied = json.loads(sealed.read_text(encoding="utf-8"))
        self.assertEqual(copied["label"], "t1")
        self.assertEqual(copied["receipt"]["session_id"], "native-session-1")
        self.assertEqual(copied["receipt"]["injected_sha256"],
                         _sha256(self.rendered.encode("utf-8")))
        self.assertIn(native["path"],
                      [artifact["path"] for artifact in verified["verified"]["sealed"]])

    def test_the_flag_off_keeps_the_old_contract(self):
        self.gate.config["require_native_session_start"] = False
        self.directory.joinpath("session-start-native-session-1.json").unlink(
            missing_ok=True)
        self.gate.session_path("t1").write_text(
            json.dumps(self._session(), ensure_ascii=False), encoding="utf-8")
        verified = self.gate.verify_session(self._pack(), "t1",
                                            require_tool_actions=False)
        self.assertIsNone(verified["verified"]["native_session_start"])

    def test_a_missing_native_receipt_fails_by_name(self):
        self.gate.session_path("t1").write_text(
            json.dumps(self._session(), ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.verify_session(self._pack(), "t1", require_tool_actions=False)
        message = str(caught.exception)
        self.assertIn("exactly one native SessionStart receipt", message)
        self.assertIn("found 0", message)

    def test_a_receipt_for_another_pack_fails_by_name(self):
        # Selected by session id, so it is found -- and then refused because the
        # bytes it injected are not the bytes this Gate rendered.
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=self._component_receipt(
                injected_sha256="f" * 64, pack_sha256="f" * 64))
        message = str(caught.exception)
        self.assertIn("does not bind", message)
        self.assertIn("injected bytes hashing to", message)

    def test_two_sessions_over_one_pack_each_bind_their_own_receipt(self):
        # Two controlled sessions can read the same pack; each must find its own
        # receipt by session id rather than fail because two candidates exist.
        first = self._verify(session=self._session(native_session_id="native-a"),
                             receipt=self._component_receipt(session_id="native-a"),
                             label="a")
        second = self._verify(session=self._session(native_session_id="native-b"),
                              receipt=self._component_receipt(session_id="native-b"),
                              label="b")
        self.assertEqual(
            first["verified"]["native_session_start"]["kind"], "native-session-start-a")
        self.assertEqual(
            second["verified"]["native_session_start"]["kind"], "native-session-start-b")

    def test_two_receipts_for_one_session_are_refused(self):
        self.gate.session_path("t1").write_text(
            json.dumps(self._session(), ensure_ascii=False), encoding="utf-8")
        self._write_receipt()
        self._write_receipt(self._component_receipt(), session_id="duplicate-file")
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.verify_session(self._pack(), "t1", require_tool_actions=False)
        self.assertIn("found 2", str(caught.exception))

    def test_a_receipt_without_hook_event_name_is_refused(self):
        receipt = self._component_receipt()
        del receipt["hook_event_name"]
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=receipt)
        self.assertIn("hook event", str(caught.exception))

    def test_a_receipt_without_injected_via_is_refused(self):
        receipt = self._component_receipt()
        del receipt["injected_via"]
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=receipt)
        self.assertIn("documented channel", str(caught.exception))

    def test_a_receipt_without_cwd_is_refused(self):
        receipt = self._component_receipt()
        del receipt["cwd"]
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=receipt)
        self.assertIn("no cwd string", str(caught.exception))

    def test_a_receipt_with_a_foreign_cwd_is_refused(self):
        receipt = self._component_receipt(cwd=str(self.directory))
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=receipt)
        self.assertIn("cwd resolves to", str(caught.exception))

    def test_a_receipt_with_a_mismatched_pack_sha256_is_refused(self):
        receipt = self._component_receipt(pack_sha256="f" * 64)
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=receipt)
        self.assertIn("pack_sha256", str(caught.exception))

    def test_a_receipt_with_an_invalid_parent_process_name_is_refused(self):
        receipt = self._component_receipt(parent_process_name=123)
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=receipt)
        self.assertIn("parent_process_name", str(caught.exception))

    def test_a_receipt_without_parent_process_name_still_binds(self):
        # `parent_process_name` is supporting evidence. Its absence, when `ps`
        # could not answer, is allowed; it is not a hard gate.
        receipt = self._component_receipt()
        receipt.pop("parent_process_name", None)
        verified = self._verify(receipt=receipt)
        self.assertIsNotNone(verified["verified"]["native_session_start"])

    def test_a_receipt_path_that_resolves_outside_the_repo_is_refused(self):
        outside = Path(self.gate.root.parent.parent) / "outside-receipt.json"
        outside.write_text(json.dumps(self._component_receipt()), encoding="utf-8")
        link = self.directory / "session-start-native-session-1.json"
        link.symlink_to(outside)
        self.gate.session_path("t1").write_text(
            json.dumps(self._session(), ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.verify_session(self._pack(), "t1", require_tool_actions=False)
        self.assertIn("outside the session repository", str(caught.exception))

    def test_a_missing_native_session_id_fails_by_name(self):
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(session=self._session(native_session_id=None))
        self.assertIn("no native_session_id", str(caught.exception))

    def test_a_session_id_mismatch_fails_by_name(self):
        # Selection is by session id: a receipt for another session is not a
        # candidate at all, and the failure says which session was looked for.
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(session=self._session(native_session_id="somewhere-else"))
        message = str(caught.exception)
        self.assertIn("for session 'somewhere-else'", message)
        self.assertIn("found 0", message)

    def test_a_clear_or_compact_receipt_does_not_bind_a_fresh_session(self):
        for source in ("clear", "compact"):
            with self.subTest(source=source):
                self.gate.config["require_native_session_start"] = True
                with self.assertRaises(self.module.Failed) as caught:
                    self._verify(receipt=self._component_receipt(source=source))
                self.assertIn("startup/resume", str(caught.exception))

    def test_a_hook_script_hash_mismatch_fails_by_name(self):
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=self._component_receipt(
                hook_script_sha256="0" * 64))
        message = str(caught.exception)
        self.assertIn("hook script", message)
        self.assertIn("installed script hashes to", message)

    def test_an_injected_byte_count_mismatch_fails_by_name(self):
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=self._component_receipt(injected_bytes=1))
        self.assertIn("bytes", str(caught.exception))

    def test_a_missing_installed_hook_script_fails_by_name(self):
        receipt = self._component_receipt()
        self.script.unlink()
        with self.assertRaises(self.module.Failed) as caught:
            self._verify(receipt=receipt)
        self.assertIn("installed hook script", str(caught.exception))

    def test_the_sealed_receipt_is_write_once_across_edits(self):
        first = self._verify()
        self.assertEqual(first["verified"]["native_session_start"]["write"], "created")
        path = self.directory / "session-start-native-session-1.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        body["written_at"] = "2020-01-01T00:00:00Z"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.assertRaises(self.module.Failed) as caught:
            self.gate.verify_session(self._pack(), "t1", require_tool_actions=False)
        self.assertIn("written once", str(caught.exception))

    def test_the_native_receipt_reaches_the_phase_seal(self):
        verified = self._verify()
        record = self.module.CaseRecord("P4-G01", "PASS", mode="real")
        self.gate.seal_session_artifacts(record, verified)
        self.assertIn("native-session-start-t1", record.sealed_artifacts)
        out = self.gate.root.parent / "out"
        out.mkdir()
        manifest = out / "p4-gate-native.json"
        manifest.write_text("{}", encoding="utf-8")
        self.module._write_phase_seal(
            self.gate.root.parent, "phase1", manifest, [record],
            {"commit": "abc1234", "dirty": False, "schema_version": 11})
        seal = json.loads((self.gate.root.parent / "phase1-seal.json").read_text(
            encoding="utf-8"))
        native = verified["verified"]["native_session_start"]
        entry = [r for r in seal["receipts"] if r["path"] == native["path"]]
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0]["sha256"], native["sha256"])


# -- drift against the documented Codex hook contract ---------------------------

class InstalledHooksShapeMatchesTheOfficialContract(unittest.TestCase):
    """The installed hooks.json must keep the documented shape.

    Official contract (codex-cli 0.159.3 docs): a project-local
    `<repo>/.codex/hooks.json` maps event names to entries with a `matcher` over
    the start `source`, whose `hooks` list holds command hooks with `type`,
    `command`, `timeout` and `additionalContextLimit`. Drift here means Codex
    silently stops firing the hook.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p4-native-drift-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.python = Path(PYTHON)
        self.installer = _installer_module()

    def _document(self) -> dict:
        _, _, new, _ = self.installer.plan(self.repo.resolve(), self.python)
        return json.loads(new.decode("utf-8"))

    def test_the_event_the_matcher_and_the_command_hook_shape(self):
        document = self._document()
        self.assertIn("SessionStart", document["hooks"])
        entries = document["hooks"]["SessionStart"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "startup|resume|clear|compact")
        hook = entries[0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertIn("additionalContextLimit", hook)
        self.assertEqual(hook["timeout"], 30)
        tokens = shlex.split(hook["command"])
        self.assertEqual(len(tokens), 2)
        self.assertTrue(Path(tokens[0]).is_absolute())
        self.assertTrue(Path(tokens[1]).is_absolute())
        self.assertEqual(Path(tokens[1]).name, "jasmine-p4-session-start.py")
        self.assertEqual(Path(tokens[1]),
                         self.repo.resolve() / ".codex" / "jasmine"
                         / "jasmine-p4-session-start.py")

    def test_the_installed_script_is_this_repositorys_hook_source(self):
        # The real property is about the installer's copy step, not a file compared
        # to itself: apply to a throwaway repo and the installed bytes must be the
        # repository source's bytes, computed now.
        repo = (Path(self.temp.name) / "repo-copy").resolve()
        repo.mkdir()
        _, _, _, script = self.installer.plan(repo, self.python)
        write = self.installer.install_script(script)
        self.assertEqual(write, "created")
        self.assertEqual(script.read_bytes(), HOOK.read_bytes())
        self.assertEqual(_sha256(script.read_bytes()), _sha256(HOOK.read_bytes()))
        self.assertEqual(self.installer.install_script(script), "unchanged")
        source = HOOK.read_text(encoding="utf-8")
        self.assertIn('"hookEventName": "SessionStart"', source)
        self.assertIn("additionalContext", source)

    def test_changing_the_source_and_reapplying_updates_the_installed_copy(self):
        # An update must move the installed copy, because the installed digest is
        # what the Gate recomputes against the receipt: a stale copy that still
        # passed would bind receipts to a script that no longer runs.
        repo = (Path(self.temp.name) / "repo-update").resolve()
        repo.mkdir()
        _, _, _, script = self.installer.plan(repo, self.python)
        self.installer.install_script(script)
        original_source = self.installer.HOOK_SOURCE
        replacement = Path(self.temp.name) / "hook-v2.py"
        replacement.write_text(HOOK.read_text(encoding="utf-8") + "\n# v2\n",
                               encoding="utf-8")
        self.installer.HOOK_SOURCE = replacement
        self.addCleanup(setattr, self.installer, "HOOK_SOURCE", original_source)
        self.assertEqual(self.installer.install_script(script), "updated")
        self.assertEqual(script.read_bytes(), replacement.read_bytes())
        self.assertNotEqual(script.read_bytes(), HOOK.read_bytes())


if __name__ == "__main__":
    unittest.main()
