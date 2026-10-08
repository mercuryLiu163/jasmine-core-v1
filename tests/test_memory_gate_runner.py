"""The P4 Gate runner's precondition logic.

This is coverage of the *runner*, not of the Gates. What matters is that the
runner cannot be mistaken for a Gate that passed: every missing prerequisite has
to come out as `BLOCKED` with the exact item named, and there must be no code
path that produces `mode: "boundary-fake"`.

The real-Gate decisions themselves — G05, G06, G07 against a real engine — are
made by `scripts/p4-memory-gate.py` with an operator-supplied configuration.
None of them can run in this repository: there is no Hindsight process, no LLM
extractor, no real Agent session, and no P3 Context Pack.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import SRC, import_gate_module  # noqa: F401  (path setup)

from jasmine_core import errors  # noqa: E402
from jasmine_core.memory import scope  # noqa: E402

GATE = SRC.parent / "scripts" / "p4-memory-gate.py"
CASES = ("P4-G01", "P4-G02", "P4-G03", "P4-G04", "P4-G05", "P4-G06", "P4-G07")


def load_runner():
    """The runner module, loaded once per process and shared with the sealing tests.

    The registration matters: the runner defines dataclasses, and `@dataclass` looks
    its own module up in `sys.modules` while decorating, so an anonymous load fails
    inside the standard library rather than in the runner.

    It used to load the file itself, unconditionally. That produced a *second* module
    object with its own `Failed` and `Blocked` classes, so a sealing test asserting the
    runner raises `Failed` caught nothing when both files ran in one suite -- the classes
    were different objects. The suite order decided whether the tests passed. One loader,
    one module.
    """
    return import_gate_module()


class RunnerSource(unittest.TestCase):
    """Static checks on the runner: it must not be able to fake a verdict."""

    def test_the_runner_exists_and_is_executable_python(self) -> None:
        self.assertTrue(GATE.exists(), f"{GATE} is missing")
        source = GATE.read_text(encoding="utf-8")
        self.assertIn("P4-G01", source)
        for case_id in CASES:
            self.assertIn(case_id, source, case_id)

    def test_no_boundary_fake_verdict_can_be_produced(self) -> None:
        source = GATE.read_text(encoding="utf-8")
        # The vocabulary exists so the documentation can name what is excluded.
        self.assertIn('"boundary-fake"', source, "the vocabulary must exist to be excluded")
        # Every `mode` the runner can assign is one of the two honest values, and
        # neither is the fake one. The `BLOCKED` branch computes its value rather
        # than hard-coding `not-run`, because a case that retained, recalled,
        # obsoleted, deleted, faulted and recovered against a real engine before
        # finding a precondition missing did not "not run" -- and a manifest that
        # says so next to six receipts misleads whoever reads it next.
        assignments = set(re.findall(r'record\.mode = "([a-z-]+)"', source))
        self.assertNotIn("boundary-fake", assignments)
        self.assertTrue(assignments <= {"real", "not-run"},
                        f"unexpected mode values: {sorted(assignments)}")
        self.assertIn("not-run", source, "a case that did nothing must still be able to say so")
        conditional = re.search(r'record\.mode = \((.*?)\)\s*\n?\s*else "not-run"',
                                source, re.S)
        self.assertIsNotNone(conditional,
                             "the BLOCKED branch must still reach `not-run` when nothing ran")
        self.assertIn("record.receipts", conditional.group(1),
                      "`not-run` must depend on whether the case actually did anything")
        self.assertEqual(self.runner_module().CaseRecord("x", "BLOCKED").mode, "real",
                         "the default mode must be the honest one")
        self.assertEqual(self.runner_module().CaseRecord.__dataclass_fields__["mode"].default,
                         "real")

    @staticmethod
    def runner_module():
        return load_runner()

    def test_a_dirty_checkout_is_refused_without_an_explicit_override(self) -> None:
        source = GATE.read_text(encoding="utf-8")
        self.assertIn("checkout is dirty", source)
        self.assertIn('"--allow-dirty"', source)


class Preconditions(unittest.TestCase):
    """Each missing prerequisite yields BLOCKED with the item named."""

    def setUp(self) -> None:
        self.runner = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-test-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()

    def gate(self, *, case_id: str = "P4-TEST", **config) -> object:
        return self.runner.Gate(self.work, config, case_id)

    def test_no_engine_url_blocks_with_the_variable_named(self) -> None:
        gate = self.gate()
        previous = os.environ.pop("JASMINE_HINDSIGHT_URL", None)
        self.addCleanup(lambda: os.environ.__setitem__("JASMINE_HINDSIGHT_URL", previous)
                        if previous else None)
        with self.assertRaises(self.runner.Blocked) as caught:
            gate.open()
        self.assertIn("JASMINE_HINDSIGHT_URL", "; ".join(caught.exception.items))

    def test_an_unreachable_engine_blocks_with_the_code_and_the_host(self) -> None:
        gate = self.gate()
        os.environ["JASMINE_HINDSIGHT_URL"] = "http://127.0.0.1:1"
        os.environ["JASMINE_HINDSIGHT_TIMEOUT_SECONDS"] = "1"
        self.addCleanup(gate.close)
        gate.open()
        with self.assertRaises(self.runner.Blocked) as caught:
            gate.require_engine()
        message = "; ".join(caught.exception.items)
        self.assertIn("did not answer", message)
        self.assertIn("engine_unreachable", message)
        self.assertIn("127.0.0.1:1", message)

    def test_an_unmerged_phase_blocks_and_names_the_phase(self) -> None:
        gate = self.gate(dependency_commits={"P2": "blocked", "P3": None})
        for phase, marker in (("P2", "P2"), ("P3", "P3")):
            with self.assertRaises(self.runner.Blocked) as caught:
                gate.require_dependency(phase)
            self.assertIn(marker, "; ".join(caught.exception.items))
        # A real commit satisfies it.
        merged = self.gate(case_id="P4-TEST-MERGED", dependency_commits={"P3": "0" * 40})
        self.assertEqual(merged.require_dependency("P3"), "0" * 40)

    def test_a_missing_bank_namespace_blocks(self) -> None:
        with self.assertRaises(self.runner.Blocked) as caught:
            self.gate().require_bank_namespace()
        self.assertIn("bank namespace", "; ".join(caught.exception.items))

    def test_no_declared_real_agent_session_blocks(self) -> None:
        with self.assertRaises(self.runner.Blocked) as caught:
            self.gate().require_real_agent()
        self.assertIn("real Agent session", "; ".join(caught.exception.items))

    def test_the_structurally_blocked_cases_are_declared_up_front(self) -> None:
        # G01/G02/G03/G04 need a P3 Context Pack and G01 also needs a real
        # session. The set is recorded in the module so a future edit cannot
        # drop one of them from the list without a visible diff, and the check is
        # on the *code* of each case rather than on a comment.
        import dis

        # G05 and G07 are in because both prove something about an actual Context
        # Pack; they were the two that could previously reach a verdict without one.
        self.assertEqual(self.runner.NEEDS_P3_PACK,
                         frozenset({"P4-G01", "P4-G02", "P4-G03", "P4-G04",
                                    "P4-G05", "P4-G07"}))
        self.assertEqual(self.runner.NEEDS_REAL_AGENT, frozenset({"P4-G01"}))
        # Two ways to satisfy "this case needs P3", and the second is stronger:
        # either it declares the dependency (`require_p3_pack`), or it builds a real
        # pack through P3's own `ContextBuilder`, which cannot succeed without P3.
        # A case that does neither could return a verdict having never consulted the
        # Context contract, which is the thing this is here to prevent.
        for case_id in sorted(self.runner.NEEDS_P3_PACK):
            names = {instruction.argval
                     for instruction in dis.get_instructions(self.runner.CASES_BY_ID[case_id])}
            self.assertTrue({"require_p3_pack", "build_pack"} & names,
                            f"{case_id} neither requires P3 nor builds a pack")
        names = {instruction.argval
                 for instruction in dis.get_instructions(self.runner.CASES_BY_ID["P4-G01"])}
        self.assertIn("require_real_agent", names)

    def test_every_spawned_worker_snippet_binds_every_name_it_uses(self) -> None:
        """A snippet that uses a name it never imports raises only when it runs.

        G06's second window died with `NameError: name 'jobs_module' is not defined`,
        and the window before that with `NameError: name 'SCHEMA_VERSION' is not
        defined` -- both invisible until the engine was healthy enough to reach them,
        because a snippet only executes on the happy path. `pyflakes` cannot see
        inside a triple-quoted string, so nothing else covers this class.

        So every `spawn_worker` snippet is parsed and its loaded names are checked
        against the bootstrap's scope, its own imports and bindings, and the
        builtins. This is the check `21c2daa`'s message described; the commit fixed
        the one `NameError` and the claim about the rest turned out to be untested
        until the next one fired.
        """
        import ast
        import builtins
        import textwrap

        tree = ast.parse(pathlib.Path(self.runner.__file__).read_text(encoding="utf-8"))
        bootstrap = next(node.value for node in ast.walk(tree)
                         if isinstance(node, ast.Constant) and isinstance(node.value, str)
                         and "def boot():" in node.value)
        scope = {node.id for node in ast.walk(ast.parse(textwrap.dedent(bootstrap)))
                 if isinstance(node, ast.Name)}
        scope |= {alias.asname or alias.name.split(".")[0]
                  for alias in ast.walk(ast.parse(textwrap.dedent(bootstrap)))
                  if isinstance(alias, ast.alias)}
        # Everything the bootstrap puts in scope for a child by contract.
        shared = {"opts", "json", "sys", "os", "runner", "boot", "gate", "self"}

        checked = 0
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "spawn_worker"):
                continue
            self.assertTrue(call.args and isinstance(call.args[0], ast.Constant),
                            "spawn_worker's source must be a literal, or it cannot be checked")
            label = next((kw.value.value for kw in call.keywords
                          if kw.arg == "label" and isinstance(kw.value, ast.Constant)), "?")
            module = ast.parse(textwrap.dedent(call.args[0].value))
            bound = set(scope) | shared | set(dir(builtins))
            for node in ast.walk(module):
                if isinstance(node, ast.alias):
                    bound.add(node.asname or node.name.split(".")[0])
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bound.add(node.name)
                    bound |= {a.arg for a in getattr(getattr(node, "args", None), "posonlyargs", [])}
                    bound |= {a.arg for a in getattr(getattr(node, "args", None), "args", [])}
                    bound |= {a.arg for a in getattr(getattr(node, "args", None), "kwonlyargs", [])}
                    if getattr(node, "args", None) is not None and node.args.vararg:
                        bound.add(node.args.vararg.arg)
                    if getattr(node, "args", None) is not None and node.args.kwarg:
                        bound.add(node.args.kwarg.arg)
                if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                    bound.add(node.id)
                if isinstance(node, ast.ExceptHandler) and node.name:
                    bound.add(node.name)
                if isinstance(node, ast.comprehension):
                    bound |= {t.id for t in ast.walk(node.target) if isinstance(t, ast.Name)}
                if isinstance(node, ast.Global):
                    bound |= set(node.names)
            used = {node.id for node in ast.walk(module)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
            self.assertEqual(sorted(used - bound), [], f"{label} uses an unbound name")
            checked += 1
        self.assertGreaterEqual(checked, 4, "no snippet was checked; the scan found nothing")

    def test_a_dry_run_against_no_engine_reports_blocked_for_every_case(self) -> None:
        """The end-to-end honesty check: nothing available means nothing passes."""
        out = Path(self._tmp.name) / "out"
        config = Path(self._tmp.name) / "config.json"
        config.write_text(json.dumps({"run_id": "p4-test-dryrun",
                                      "bank_suffix": "p4-test-dryrun",
                                      "dependency_commits": {"P2": "blocked", "P3": "blocked"}}),
                          encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(SRC),
                   JASMINE_HINDSIGHT_URL="http://127.0.0.1:1",
                   JASMINE_HINDSIGHT_TIMEOUT_SECONDS="1")
        env.pop("JASMINE_HINDSIGHT_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, str(GATE), "--out", str(out), "--work", str(self.work / "run"),
             "--config", str(config), "--allow-dirty"],
            capture_output=True, text=True, env=env, timeout=300,
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        manifest = json.loads(Path(summary["result"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["verdict"], "BLOCKED")
        self.assertEqual({case["case_id"] for case in manifest["cases"]}, set(CASES))
        for case in manifest["cases"]:
            self.assertEqual(case["verdict"], "BLOCKED", case["case_id"])
            self.assertEqual(case["mode"], "not-run")
            # The functional preflight now stops the run before any case: every
            # case is recorded against the failed check, and no case database was
            # created, so "not-run" is exactly true.
            self.assertEqual(case["blocked_on"], ["database_connected"], case["case_id"])
            self.assertIn("database_connected", case["reason"])
        self.assertEqual(list((self.work / "run").glob("P4-G*/core.db")), [],
                         "a failed preflight must block before any case opens its Core")
        self.assertFalse(manifest["engine"]["reachable"])
        preflight = manifest["engine"]["functional_preflight"]
        self.assertEqual(preflight["verdict"], "BLOCKED")
        self.assertEqual(preflight["failed_check"], "database_connected")
        self.assertEqual(manifest["bank_namespace"], "p4-test-dryrun")
        self.assertEqual(manifest["phase"], "P4")
        self.assertIsNone(manifest["final_commit"])
        self.assertIn("not Gate evidence", " ".join(manifest["notes"]))
        # The native receipt's producer cannot be proved by the gate; the manifest
        # states that boundary instead of implying Codex's own process was seen.
        native = manifest["native_session_start"]
        self.assertFalse(native["required"])
        self.assertIn("operator asserts", native["attestation"])
        self.assertIn("cannot prove which process", native["attestation"])


class AWholeRunAnchorsAndScopesItsEvidence(unittest.TestCase):
    """What a finished run leaves on disk for whoever reads the manifest afterwards.

    End to end rather than per helper, because these are the parts that only exist if
    `main()` wires them: the operator configuration sealed into the work directory, the
    scope statement in the manifest, and the anchor file that holds the manifest's own
    digest -- the one link in the chain nothing else covered, which is why a FAIL could
    be rewritten as a PASS with no trace.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-anchor-e2e-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.out = self.root / "out"
        self.work = self.root / "work"
        self.config = self.root / "op.json"
        self.config.write_text(json.dumps(
            {"run_id": "p4-anchor", "bank_suffix": "p4-anchor",
             "real_agent_session": True,
             "dependency_commits": {"P2": "0" * 40, "P3": "0" * 40}}), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(SRC),
                   JASMINE_HINDSIGHT_URL="http://127.0.0.1:1",
                   JASMINE_HINDSIGHT_TIMEOUT_SECONDS="1")
        env.pop("JASMINE_HINDSIGHT_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, str(GATE), "--out", str(self.out), "--work", str(self.work),
             "--config", str(self.config), "--allow-dirty"],
            capture_output=True, text=True, env=env, timeout=300)
        self.summary = json.loads(completed.stdout.strip().splitlines()[-1])
        self.manifest_path = Path(self.summary["result"])

    def test_the_anchor_holds_this_manifests_digest(self) -> None:
        anchor = Path(self.summary["anchor_path"])
        self.assertTrue(anchor.is_file(), self.summary)
        body = json.loads(anchor.read_text(encoding="utf-8"))
        self.assertEqual(body["manifest_path"], str(self.manifest_path))
        self.assertEqual(body["manifest_sha256"],
                         hashlib.sha256(self.manifest_path.read_bytes()).hexdigest())
        self.assertEqual(body["phase"], "phase1")
        self.assertEqual(body["commit"], self.summary["commit"])

    def test_the_operator_configuration_is_sealed_into_the_work_directory(self) -> None:
        sealed = self.work / "op.json"
        self.assertTrue(sealed.is_file())
        self.assertEqual(sealed.read_bytes(), self.config.read_bytes())
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["operator_config"]["sha256"],
                         hashlib.sha256(self.config.read_bytes()).hexdigest())
        # ...and the phase seal covers it, so a resumed run re-hashes it before a case runs.
        seal = json.loads((self.work / "phase1-seal.json").read_text(encoding="utf-8"))
        self.assertIn(str(sealed.resolve()),
                      [entry["path"] for entry in seal["receipts"]])

    def test_the_manifest_states_what_is_sealed_and_what_is_only_hashed(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        scope = manifest["evidence_scope"]
        self.assertIn("receipts", scope["sealed_byte_for_byte"])
        self.assertIn("injected_packs", scope["sealed_byte_for_byte"])
        self.assertIn("op.json", scope["sealed_byte_for_byte"]["operator_config"])
        self.assertIn("NOT sealed", scope["hashed_not_content_sealed"]["note"])
        for row in scope["hashed_not_content_sealed"]["databases"]:
            self.assertTrue(Path(row["path"]).is_file())
            self.assertEqual(len(row["sha256"]), 64)
        self.assertTrue(scope["out_of_scope"])


class NoCaseReturnsWithoutItsEvidence(unittest.TestCase):
    """A case that returns normally is a case recorded as PASS.

    G05, G06 and G07 were the three that could return early: each ran a set of
    checks and fell off the end of the function, so the runner recorded `PASS`
    even though the Context Pack, the real process restart and the audit evidence
    their taskbook entries mandate had never been produced. Two shapes are
    allowed, and both are asserted here:

    * the case ends in `raise Blocked(...)` naming what is still missing; or
    * the case has actually done the thing it claims — for G06, killed a real
      worker process inside the retain window and started another.
    """

    def setUp(self) -> None:
        import ast

        self.source = GATE.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.runner = load_runner()

    def _final_statement(self, name: str):
        import ast

        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node.body[-1]
        raise AssertionError(f"{name} not found in {GATE}")

    def test_only_g06_may_return_normally(self) -> None:
        import ast

        returning = []
        for case_id in CASES:
            final = self._final_statement(self.runner.CASES_BY_ID[case_id].__name__)
            if isinstance(final, ast.Return):
                # A case whose last statement delegates to a verifier returns through
                # it; that is the "may complete" shape, like falling off the end.
                returning.append(case_id)
            elif isinstance(final, ast.Raise):
                # `raise Blocked(...)` is an ast.Call whose func names the class;
                # a bare `raise Blocked` is an ast.Name directly.
                callee = final.exc.func if isinstance(final.exc, ast.Call) else final.exc
                name = getattr(callee, "id", None) or getattr(callee, "attr", None)
                self.assertEqual(
                    name, "Blocked",
                    f"{case_id} raises {name}; only Blocked may end a case, "
                    f"because anything else is swallowed as a failure rather "
                    f"than as a missing precondition")
            else:
                returning.append(case_id)
        # A case may return normally only if it can complete on evidence alone:
        # either it verifies an agent session receipt, or -- for G06 -- its evidence
        # is produced by real worker processes. Anything else must end in Blocked,
        # because a case that cannot finish has not passed.
        evidenced = set()
        for case_id in CASES:
            module = self.runner
            source = inspect.getsource(module.CASES_BY_ID[case_id])
            # A case may hand its assertions to a module-level verifier -- by returning
            # it, or by calling it as its last statement -- and the evidence is then
            # performed there. So follow every module-level function the case names,
            # rather than looking only inside the case body.
            for node in ast.walk(ast.parse(source)):
                names = []
                if isinstance(node, ast.Name):
                    names.append(node.id)
                elif isinstance(node, ast.Attribute):
                    names.append(node.attr)
                for name in names:
                    helper = getattr(module, name, None)
                    if inspect.isfunction(helper):
                        source += inspect.getsource(helper)
            if "verify_session" in source or "spawn_worker" in source:
                evidenced.add(case_id)
        self.assertEqual(set(returning), evidenced,
                         f"these cases return normally: {returning}, but only "
                         f"{sorted(evidenced)} verify a session or use real processes")
        self.assertIn("P4-G06", evidenced)

    def test_the_crash_window_is_crossed_by_processes(self) -> None:
        for needle, why in (
            ("spawn_worker(", "the case must start separate processes at all"),
            ("expect_exit=7", "it must assert the crash exit code, not merely "
                              "call a helper"),
            ("os._exit(7)", "the child must die rather than return"),
            ("DieAfterRemoteWrite", "it must die inside the retain window, after "
                                    "the remote write and before DONE"),
            ("label=\"g06-window1-commit\"", "window 1 must be crossed by a process "
                                               "that commits and leaves"),
            ("label=\"g06-window2-restart\"", "window 2 must be resumed by a second "
                                                "real process"),
            ("g06-attempts", "the local attempt chain must still be recorded"),
            ("g06-document", "the remote document read-back must still be recorded"),
        ):
            self.assertIn(needle, self.source, why)


class RealWorkerProcesses(unittest.TestCase):
    """G06's evidence is carried by child processes, so the plumbing is tested.

    The cases themselves need a real engine and cannot run here. What can be
    tested without one is everything around them: that a child starts, rebuilds
    the same Gate with the same identities and a working transport, re-enters a
    work directory another process created, and that a child which exits with the
    wrong code is reported as a failure rather than quietly ignored.
    """

    def setUp(self) -> None:
        self.runner = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-child-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()
        self._saved = {k: os.environ.get(k) for k in (
            "JASMINE_HINDSIGHT_URL", "JASMINE_HINDSIGHT_TIMEOUT_SECONDS")}
        os.environ["JASMINE_HINDSIGHT_URL"] = "http://127.0.0.1:1"
        os.environ["JASMINE_HINDSIGHT_TIMEOUT_SECONDS"] = "1"
        os.environ.pop("JASMINE_HINDSIGHT_API_KEY", None)
        self.addCleanup(self._restore_env)
        self.gate = self.runner.Gate(self.work, {"run_id": "p4-child-test",
                                                 "bank_suffix": "p4-child"},
                                     "P4-CHILD")

    def _restore_env(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_a_child_rebuilds_the_same_gate_with_the_same_identities(self) -> None:
        proc = self.gate.spawn_worker(
            """
            g = boot()
            conn = g.conn()
            print(json.dumps({"host": g.host, "human": g.human, "case": g.case_id,
                              "transport": g.transport is not None,
                              "reentered": g.root.exists()}))
            conn.close()
            """,
            label="child-identity", expect_exit=0)
        payload = json.loads(proc.stdout.strip())
        self.assertEqual(payload["host"], self.gate.host,
                         "the child invented its own host instead of using the parent's")
        self.assertEqual(payload["human"], self.gate.human,
                         "the child invented its own actor instead of using the parent's")
        self.assertEqual(payload["case"], "P4-CHILD")
        self.assertTrue(payload["transport"],
                        "the child could not build a real transport from the environment")
        self.assertTrue(payload["reentered"])

    def test_a_child_that_exits_wrong_is_reported_not_ignored(self) -> None:
        with self.assertRaises(self.runner.Failed) as caught:
            self.gate.spawn_worker("import sys\nsys.exit(7)\n",
                                   label="child-wrong-exit", expect_exit=0)
        self.assertIn("exited 7", str(caught.exception))
        self.assertIn("child-wrong-exit", str(caught.exception))

    def test_a_restarted_process_may_re_enter_an_existing_work_directory(self) -> None:
        # The first Gate created it; a second construction for the same case is a
        # restart and must not raise. This is what makes `spawn_worker` usable
        # more than once per case.
        self.assertTrue(self.gate.root.exists())
        again = self.runner.Gate(self.work, {"run_id": "p4-child-test",
                                             "bank_suffix": "p4-child"}, "P4-CHILD")
        self.assertEqual(again.root, self.gate.root)


class DeploymentModeIsReported(unittest.TestCase):
    def test_the_manifest_records_the_mode_so_no_acl_claim_is_implicit(self) -> None:
        runner = load_runner()
        source = GATE.read_text(encoding="utf-8")
        self.assertIn('"deployment_mode": scope.deployment_mode()', source)
        self.assertIn("no user-level ACL claim is made", " ".join(
            line for line in source.splitlines() if "ACL" in line))
        self.assertEqual(runner.CASES_BY_ID["P4-G04"].__name__, "case_g04")
        self.assertEqual(scope.SINGLE_USER, "single_user")


class TheVerdictIsNotASubsetRun(unittest.TestCase):
    """A run that did not evaluate every case cannot report the stage as passed.

    `PASS` used to mean "every case I happened to select passed", so
    `--case P4-G06` alone could produce a stage PASS with six cases never
    looked at. A development run could too, which is the opposite of what
    `--allow-dirty` is for.

    Every invocation passes `--allow-dirty` so the test behaves the same on a
    committed tree and a working one; the flag is itself one of the things being
    asserted on.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-scope-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.out = self.root / "out"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"run_id": "p4-scope-test",
                                           "bank_suffix": "p4-scope-test",
                                           "dependency_commits": {"P2": "blocked",
                                                                  "P3": "blocked"}}),
                               encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(SRC),
                   JASMINE_HINDSIGHT_URL="http://127.0.0.1:1",
                   JASMINE_HINDSIGHT_TIMEOUT_SECONDS="1")
        env.pop("JASMINE_HINDSIGHT_API_KEY", None)
        self.env = env

    def _run(self, *extra) -> dict:
        completed = subprocess.run(
            [sys.executable, str(GATE), "--out", str(self.out),
             "--work", str(self.root / "work"), "--config", str(self.config),
             "--allow-dirty", *extra],
            capture_output=True, text=True, env=self.env, timeout=300)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        return json.loads(Path(summary["result"]).read_text(encoding="utf-8"))

    def test_a_partial_selection_is_not_a_stage_acceptance(self) -> None:
        manifest = self._run("--case", "P4-G06")
        self.assertEqual(manifest["scope"]["complete"], False)
        self.assertEqual(len(manifest["scope"]["not_run"]), len(CASES) - 1)
        self.assertIn("P4-G01", manifest["scope"]["not_run"])
        self.assertEqual(manifest["stage_acceptance"]["eligible"], False)
        self.assertTrue(any("selected" in reason
                            for reason in manifest["stage_acceptance"]["reasons"]),
                        manifest["stage_acceptance"]["reasons"])
        self.assertNotEqual(manifest["verdict"], "PASS",
                            "a subset run reported the stage as passed")

    def test_a_development_run_is_not_a_stage_acceptance(self) -> None:
        manifest = self._run()
        self.assertEqual(manifest["scope"]["complete"], True)
        self.assertEqual(manifest["stage_acceptance"]["eligible"], False)
        self.assertTrue(any("dirty" in reason
                            for reason in manifest["stage_acceptance"]["reasons"]),
                        manifest["stage_acceptance"]["reasons"])
        self.assertNotEqual(manifest["verdict"], "PASS",
                            "--allow-dirty reported the stage as passed")

    def test_a_full_run_has_no_other_reason_than_the_development_flag(self) -> None:
        # The only thing standing between this run and eligibility is the flag
        # that was passed deliberately. A full run without it therefore has no
        # reasons at all, which is the only way `PASS` can ever be reached.
        manifest = self._run()
        self.assertEqual(manifest["scope"]["complete"], True)
        self.assertEqual(manifest["scope"]["not_run"], [])
        self.assertEqual(len(manifest["stage_acceptance"]["reasons"]), 1,
                         manifest["stage_acceptance"]["reasons"])
        self.assertIn("dirty", manifest["stage_acceptance"]["reasons"][0])
        self.assertEqual(manifest["stage_acceptance"]["eligible"], False)

    def test_the_manifest_records_the_namespace_it_wrote_to(self) -> None:
        manifest = self._run()
        self.assertEqual(manifest["bank_namespace"], "p4-scope-test")


class AnAllReplayResumeStillRecordsItsNamespace(unittest.TestCase):
    """A resume whose every case replays must not record an empty namespace.

    `namespace_used` used to be assigned only when a case called `open()`, and a
    replayed terminal failure never opens one -- so a resume over sealed failures
    wrote `bank_namespace: ""` into the manifest even though the run was configured
    with, and its engine address derived under, a real namespace. The namespace is
    now taken from the configuration before the loop.
    """

    def test_every_case_replaying_still_names_the_configured_namespace(self) -> None:
        runner = load_runner()
        tmp = tempfile.TemporaryDirectory(prefix="p4-gate-replay-ns-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        work = root / "work"
        work.mkdir()
        out = root / "out"
        config = root / "op.json"
        config.write_text(json.dumps({
            "run_id": "p4-replay-ns", "bank_suffix": "p4-replay-ns",
            "dependency_commits": {"P2": "0" * 40, "P3": "0" * 40}}),
            encoding="utf-8")
        argv = [str(GATE), "--out", str(out), "--work", str(work),
                "--config", str(config), "--resume", "--case", "P4-G01",
                "--allow-dirty"]
        import contextlib
        import io

        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.object(runner, "_verify_prior_seal",
                                  return_value={"cases": {"P4-G01":
                                                          {"verdict": "FAIL"}}}), \
                mock.patch.object(runner, "_run_functional_preflight",
                                  return_value={"verdict": "PASS", "checks": {},
                                                "failed_check": None}) as preflight, \
                mock.patch.object(runner, "_replay_terminal_setup_failure",
                                  return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            code = runner.main()
        self.assertEqual(code, 2, "a replayed failure is BLOCKED")
        manifest_path = next(out.glob("p4-gate-*.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["bank_namespace"], "p4-replay-ns")
        # A resume whose only case replays a sealed failure must not consult the
        # engine at all: the preflight is skipped, not passed, and the skip is
        # recorded with its reason. Running it anyway would let a down engine turn
        # a reproducible FAIL into an environment BLOCKED.
        self.assertEqual(preflight.call_count, 0)
        functional = manifest["engine"]["functional_preflight"]
        self.assertEqual(functional["verdict"], "SKIPPED")
        self.assertIn("replays a sealed terminal failure", functional["skipped"])


class TheBankNamespaceShapesAddresses(unittest.TestCase):
    """`bank_suffix` had to reach `scope`, or it constrained nothing."""

    def tearDown(self) -> None:
        os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)

    def test_the_default_is_no_namespace_at_all(self) -> None:
        from jasmine_core import ids as ids_module
        from jasmine_core.memory import scope as scope_module

        os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)
        self.assertEqual(scope_module.bank_namespace(), "")
        self.assertEqual(scope_module.global_bank().bank_id, "jasmine-global")
        project = ids_module.new_id("prj")
        self.assertEqual(scope_module.project_bank(project).bank_id,
                         f"jasmine-project-{project}")

    def test_a_namespace_moves_the_engine_address_not_the_core_id(self) -> None:
        """The namespace is the engine's, and Core's ids stay canonical.

        P3's Memory provider validates `jasmine-global` and
        `jasmine-project-{prj}` exactly, so a namespaced deployment still has to
        hand it those. Isolation holds where the data is: the engine addresses
        differ.
        """
        from jasmine_core import ids as ids_module
        from jasmine_core.memory import scope as scope_module

        os.environ["JASMINE_MEMORY_BANK_NAMESPACE"] = "run-42"
        self.assertEqual(scope_module.bank_namespace(), "run-42")
        project = ids_module.new_id("prj")
        bank = scope_module.project_bank(project)
        # Core-side: canonical, whatever the engine is called.
        self.assertEqual(scope_module.global_bank().bank_id, "jasmine-global")
        self.assertEqual(bank.bank_id, f"jasmine-project-{project}")
        self.assertEqual(scope_module.scope_of(bank.bank_id).project_id, project)
        self.assertIsNotNone(scope_module.scope_of("jasmine-global"))
        self.assertTrue(scope_module.is_valid_bank_id(bank.bank_id))
        self.assertTrue(scope_module.is_valid_bank_id("jasmine-global"))
        # Engine-side: namespaced, which is what two runs must not share.
        self.assertEqual(scope_module.engine_bank_id(scope_module.global_bank()),
                         "run-42jasmine-global")
        self.assertEqual(scope_module.engine_bank_id(bank),
                         f"run-42jasmine-project-{project}")

    def test_a_malformed_namespace_is_refused_rather_than_dropped(self) -> None:
        from jasmine_core.memory import scope as scope_module

        # Silently falling back to un-namespaced ids is the sharing this exists
        # to prevent, so a bad value must not be ignored.
        os.environ["JASMINE_MEMORY_BANK_NAMESPACE"] = "not valid/space"
        with self.assertRaises(errors.InvalidRequest):
            scope_module.bank_namespace()


class TheGateRefusesAnInactiveNamespace(unittest.TestCase):
    """A non-empty `bank_suffix` that never reaches `scope` constrains nothing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-ns-")
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()
        self._saved = os.environ.get("JASMINE_HINDSIGHT_URL")
        os.environ["JASMINE_HINDSIGHT_URL"] = "http://127.0.0.1:1"
        os.environ["JASMINE_HINDSIGHT_TIMEOUT_SECONDS"] = "1"
        self.addCleanup(self._restore)
        os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("JASMINE_HINDSIGHT_URL", None)
        else:
            os.environ["JASMINE_HINDSIGHT_URL"] = self._saved
        os.environ.pop("JASMINE_MEMORY_BANK_NAMESPACE", None)

    def test_the_suffix_is_refused_until_open_puts_it_in_effect(self) -> None:
        runner = load_runner()
        gate = runner.Gate(self.work, {"bank_suffix": "p4-ns-test"}, "P4-NS")
        with self.assertRaises(runner.Blocked) as caught:
            gate.require_bank_namespace()
        self.assertIn("not the one this process", "; ".join(caught.exception.items))
        gate.open()
        self.addCleanup(gate.close)
        self.assertEqual(gate.require_bank_namespace(), "p4-ns-test")
        self.assertEqual(os.environ.get("JASMINE_MEMORY_BANK_NAMESPACE"), "p4-ns-test")


# -- the audit round: what each case's assertions are actually made of ----------
#
# Every test below drives the real verifier. The defect each one is aimed at was a
# *vacuous* check -- one that could not fail, or could not fail for the reason it
# names -- and a vacuous check cannot be shown to work by running it: it has to be
# shown to fail when the bad thing is supplied. So the shape throughout is: build
# the honest case, then supply the defect, and require the refusal.


def _render_pack(*, project: str, task_id: str, source_event_id: str, title: str,
                 status: str, memory_block: str = "",
                 authority: str = "") -> str:
    """A pack rendered the way P3 renders one, from the same pieces P3 renders.

    Written out rather than produced by the real builder because these tests are about
    what the verifier reads, and the builder is covered by its own tests. What has to
    match is the *shape*: the HEADER line, the section markers, and the Memory
    material after the Memory marker -- because that boundary is what "the only
    variable was the lesson" is checked against.
    """
    compact = dict(separators=(",", ":"), ensure_ascii=False)
    header = "JASMINE CONTEXT v1\n" + json.dumps({
        "project_id": project, "task_id": task_id, "task_revision": 1,
        "source_event_id": source_event_id,
        "precedence": "CURRENT TRUTH > CHECKPOINT > NON-AUTHORITATIVE MEMORY",
        "tokenizer_qualification": "cl100k_base", "reconciliation_required": True},
        **compact) + "\n"
    authority_text = "AUTHORITY\n" + authority
    state = "CURRENT STATE\n" + json.dumps({
        "task": {"task_id": task_id, "title": title, "description": None,
                 "status": "PLANNED", "revision": 1, "acceptance_criteria": None},
        "current_step_id": None, "steps": [],
        "safety": "Context never verifies or accepts Evidence or State."}, **compact) + "\n"
    memory = "MEMORY — NON AUTHORITATIVE\n" + json.dumps({"status": status},
                                                         **compact) + "\n" + memory_block
    return header + authority_text + state + memory


class _ScriptedGate:
    """A Gate with real receipts and prepared sessions, and nothing else.

    `receipt` is the runner's own, so the write-once guard is real in these tests; the
    rest is scripted because the engine half is not what any of these defects is about.
    """

    def __init__(self, module, root, *, sessions, questions, titles):
        self.module = module
        self._gate = module.Gate.__new__(module.Gate)
        self._gate.root = root
        self._gate.case_id = "P4-G01"
        self.sessions = sessions
        self.questions = questions
        self.titles = titles

    def receipt(self, kind, payload):
        return self._gate.receipt(kind, payload)

    def read_receipt(self, kind):
        return self._gate.read_receipt(kind)

    def verify_session(self, pack, label, *, require_tool_actions=True,
                         require_evidence_backed_actions=False):
        session = self.sessions[label]
        if require_tool_actions and not (session.get("tool_actions") or []):
            raise self.module.Failed(f"{label} recorded no tool action")
        return session

    def seal_session_artifacts(self, record, session):
        self.module.Gate.seal_session_artifacts(self, record, session)

    def prompt_text(self, event_id):
        return self.questions.get(event_id)

    def task_title(self, task_id):
        return self.titles.get(task_id)


class G01IsAControlledExperiment(unittest.TestCase):
    """Same question, same state, lesson as the only variable, benefit defined first.

    The defect was that none of that was true and none of it was checked: the two
    sessions were asked different sentences, the benefit was "the command lists differ",
    and a session that took one action against eleven passed.
    """

    CATEGORIES = load_runner().EXPLORATION_CATEGORIES

    def setUp(self) -> None:
        from jasmine_core import ids

        self.module = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-g01-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G01"
        self.root.mkdir(parents=True)
        self.project = ids.new_id("prj")
        self.baseline_task, self.lesson_task = ids.new_id("tsk"), ids.new_id("tsk")
        self.baseline_prompt, self.lesson_prompt = ids.new_id("evt"), ids.new_id("evt")
        self.historical_task = ids.new_id("tsk")
        self.memory_id, self.evidence_id = ids.new_id("mem"), ids.new_id("evd")
        self.source_event = ids.new_id("evt")

    # -- fixtures -------------------------------------------------------
    def _pack(self, *, task_id, prompt_event_id, status, memory_block=""):
        return {
            "context_pack_id": f"ctx_{task_id[4:12]}", "project_id": self.project,
            "task_id": task_id, "prompt_event_id": prompt_event_id,
            "memory_status": status, "memory_selected": [], "memory_filtered": [],
            "qualified_provenance": [], "truth_digest": "d" * 64, "check_current": True,
            "rendered_content": _render_pack(
                project=self.project, task_id=task_id, source_event_id=prompt_event_id,
                title=self.module.G01_TASK_TITLE, status=status,
                memory_block=memory_block),
            "receipt": {"path": None, "sha256": None},
        }

    def _baseline_pack(self, **kwargs):
        return self._pack(task_id=self.baseline_task, prompt_event_id=self.baseline_prompt,
                          status="empty", **kwargs)

    def _lesson_pack(self, **kwargs):
        pack = self._pack(task_id=self.lesson_task, prompt_event_id=self.lesson_prompt,
                          status="ok", **kwargs)
        pack["memory_selected"] = [{
            "memory_id": self.memory_id, "memory_type": "FAILURE_LESSON",
            "bank": {"bank_id": f"jasmine-project-{self.project}",
                     "scope": "PROJECT", "project_id": self.project},
            "source_event_ids": [self.source_event], "source_task_id": self.historical_task,
            "fact": "the callback task id did not match"}]
        pack["qualified_provenance"] = [{"memory_id": self.memory_id,
                                         "evidence_ids": [self.evidence_id],
                                         "source_event_ids": [self.source_event]}]
        return pack

    def _memory_block(self, *, command="pytest -q"):
        history = (f"history: prior FAIL result from `{command}` (task "
                   f"{self.historical_task}, step stp_{'S' * 26}; memory "
                   f"{self.memory_id}; evidence {self.evidence_id}; sources "
                   f"{self.source_event})")
        return "HISTORICAL_NONAUTHORITATIVE MEMORY DATA\n" + json.dumps({
            "memory_id": self.memory_id, "memory_type": "FAILURE_LESSON",
            "bank": {"scope": "PROJECT", "bank_id": f"jasmine-project-{self.project}",
                     "project_id": self.project},
            "source_event_ids": [self.source_event], "source_task_id": self.historical_task,
            "evidence_ids": [self.evidence_id],
            "fact": "the callback task id did not match",
            "text": history + "\nthe callback task id did not match the request"},
            separators=(",", ":")) + "\n"

    def _session(self, *, performed, completed=True, output=None, answer=None,
                 memory_seen=True, commands=("pytest -q",), backing=None):
        answer = answer if answer is not None else (
            f"{self.memory_id} was recorded before, from task {self.historical_task}; "
            f"historically the callback task id did not match the request.")
        body = {
            "final_answer": answer,
            "tool_actions": [{"command": command,
                              "output": "Ran 1 test\n\nOK\n"} for command in commands],
            "task_completed": completed,
            "final_verification": {"command": "python -m unittest tests/test_x.py",
                                   "output": output if output is not None
                                   else "Ran 1 test in 0.001s\n\nOK\n"},
            "exploration": {name: name in performed for name in self.CATEGORIES},
            "exploration_actions": (backing if backing is not None
                                    else {name: list(commands) for name in performed}),
        }
        if memory_seen:
            body["memory_seen"] = [{"memory_id": self.memory_id,
                                    "evidence_ids": [self.evidence_id],
                                    "source_event_ids": [self.source_event]}]
        body["verified"] = {"memory_carried": [] if not memory_seen else [self.memory_id],
                            "tool_actions": len(body["tool_actions"]),
                            "session_transcript": {"path": "t", "sha256": "0" * 64},
                            "injection": {"injected_sha256": "1" * 64},
                            "sealed": []}
        return body

    def _run(self, *, baseline=None, lesson=None, baseline_question=None,
             lesson_question=None, baseline_title=None, lesson_title=None,
             baseline_pack=None, lesson_pack=None):
        baseline = baseline if baseline is not None else self._session(
            performed={"enumerate_repository", "read_history", "reproduce_failure"},
            memory_seen=False, commands=("ls", "git log --oneline", "pytest -q"))
        lesson = lesson if lesson is not None else self._session(
            performed={"reproduce_failure"},
            commands=("pytest -q",))
        gate = _ScriptedGate(
            self.module, self.root,
            sessions={"g01-baseline": baseline, "g01-lesson": lesson},
            questions={self.baseline_prompt: baseline_question
                       if baseline_question is not None else self.module.G01_PROMPT,
                       self.lesson_prompt: lesson_question
                       if lesson_question is not None else self.module.G01_PROMPT},
            titles={self.baseline_task: baseline_title
                    if baseline_title is not None else f"P4-G01 {self.module.G01_TASK_TITLE}",
                    self.lesson_task: lesson_title
                    if lesson_title is not None else f"P4-G01 {self.module.G01_TASK_TITLE}"})
        record = self.module.CaseRecord("P4-G01", "PASS")
        self.module._verify_g01_packs(
            gate, record,
            lesson_pack if lesson_pack is not None else self._lesson_pack(
                memory_block=self._memory_block()),
            baseline_pack if baseline_pack is not None else self._baseline_pack())
        return record

    # -- the honest case -------------------------------------------------
    def test_one_omitted_category_with_both_sides_finished_is_a_benefit(self) -> None:
        record = self._run()
        differential = record.engine_evidence["differential"]
        self.assertEqual(differential["omitted_categories"],
                         ["enumerate_repository", "read_history"])
        self.assertEqual(differential["vocabulary"], list(self.CATEGORIES))
        self.assertEqual(record.verdict, "PASS")

    # -- reverse tests ---------------------------------------------------
    def test_two_real_p3_packs_for_different_tasks_differ_only_in_their_task_ids(self) -> None:
        """The sole-variable requirement has to be satisfiable by P3's own render.

        Built with the real `ContextBuilder` and the real renderer, for two Tasks of one
        project. If anything else in a genuine render varied between two Tasks of the
        same shape, this Gate would fail every correct run -- and an assertion nobody can
        satisfy is not a control, it is a new way to report FAIL.
        """
        from jasmine_core import SCHEMA_VERSION, db, ids
        from jasmine_core.context.builder import ContextBuilder
        from jasmine_core.context.tokens import Tokenizer, TokenizerUnavailable
        from jasmine_core.events import EventStore
        from jasmine_core.migrations import migrate
        from jasmine_core.models import NewEvent, NewObject
        from jasmine_core.objects import ObjectStore
        from jasmine_core.registry import Registry

        try:
            tokenizer = Tokenizer()
        except TokenizerUnavailable as exc:  # pragma: no cover - depends on the cache
            self.skipTest(str(exc))
        self._tmp_db = tempfile.TemporaryDirectory(prefix="p4-gate-realpack-")
        self.addCleanup(self._tmp_db.cleanup)
        conn = db.connect(Path(self._tmp_db.name) / "core.db")
        self.addCleanup(conn.close)
        migrate(conn)
        host, human, agent = ids.new_id("hst"), ids.new_id("act"), ids.new_id("act")
        reg = Registry(conn)
        reg.upsert_host(host)
        reg.upsert_actor(human, kind="human", home_host_id=host)
        reg.upsert_actor(agent, kind="agent", home_host_id=host)
        objects = ObjectStore(conn, schema_version=SCHEMA_VERSION)
        project = objects.create(NewObject.project({"name": "g01", "host_id": host}),
                                 actor_id=human)["object"]["project_id"]
        builder = ContextBuilder(conn, schema_version=SCHEMA_VERSION, tokenizer=tokenizer,
                                 scanner=lambda budget, *, conn: {
                                     "snapshot": {"complete": False,
                                                  "partial_reasons": ["gate test"]},
                                     "sample_window": {"started_at_unix": 1,
                                                       "finished_at_unix": 2}})
        packs = []
        for index, key in enumerate(("g01-no-lesson", "g01-with-lesson")):
            task = objects.create(NewObject.task({
                "project_id": project, "title": self.module.G01_TASK_TITLE,
                "host_id": host}), actor_id=human)["object"]["task_id"]
            prompt = EventStore(conn, schema_version=SCHEMA_VERSION).append(NewEvent(
                event_type="user.prompt", source_system="p4-gate-test", source_event_id=None,
                occurred_at=self.module.clock.now(), actor_id=human, actor_kind="human",
                host_id=host, project_id=project, task_id=task,
                payload={"text": self.module.G01_PROMPT}))[0]["event_id"]
            packs.append(builder.build(
                {"task_id": task, "host_id": host, "source_event_id": prompt,
                 "reason": "USER_PROMPT", "idempotency_key": key},
                actor_id=agent)["context"] | {"task_id": task, "prompt_event_id": prompt}
                         if False else dict(
                             builder.build(
                                 {"task_id": task, "host_id": host,
                                  "source_event_id": prompt, "reason": "USER_PROMPT",
                                  "idempotency_key": key},
                                 actor_id=agent)["context"],
                                 task_id=task, prompt_event_id=prompt))
        baseline, lesson = packs
        self.assertNotEqual(baseline["task_id"], lesson["task_id"])
        split = self.module._memory_section_split
        self.assertEqual(
            self.module._without_task_identity(split(baseline["rendered_content"])[0],
                                               baseline),
            self.module._without_task_identity(split(lesson["rendered_content"])[0],
                                               lesson),
            "two real P3 packs for two Tasks of one project must agree outside the "
            "Memory section once their own ids are replaced")

    def test_two_different_questions_are_refused(self) -> None:
        # The defect. One side said "the device test fails", the other "the device test
        # fails again", and a behavioural difference had a second explanation that
        # nothing could rule out.
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson_question="the device test fails again; where should I "
                                      "look first?")
        message = str(caught.exception)
        self.assertIn("different questions", message)
        self.assertIn("fails again", message)

    def test_differently_titled_tasks_are_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson_title="P4-G01 device test (new session)")
        self.assertIn("differently titled Tasks", str(caught.exception))

    def test_a_difference_outside_the_memory_section_is_refused_and_named(self) -> None:
        # Same question, same title, and still two variables: the CURRENT STATE section
        # differs. This is what the case cannot see, and it must not be able to claim
        # the experiment when it cannot.
        drifted = _render_pack(project=self.project, task_id=self.lesson_task,
                               source_event_id=self.lesson_prompt,
                               title=self.module.G01_TASK_TITLE, status="ok",
                               memory_block=self._memory_block(),
                               authority=json.dumps(
                                   {"rule_id": "rul_" + "A" * 26, "version": 1,
                                    "scope": {"kind": "project", "project_id": self.project}},
                                   separators=(",", ":")) + "\n")
        pack = self._lesson_pack(memory_block=self._memory_block())
        pack["rendered_content"] = drifted
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson_pack=pack)
        message = str(caught.exception)
        self.assertIn("differ outside the Memory section", message)
        self.assertIn("only variable", message)
        self.assertIn("at character", message)

    def test_two_packs_with_the_same_memory_section_are_refused(self) -> None:
        # If the Memory material is identical there was no injection, and a
        # differential over two identical contexts is a comparison of nothing. The
        # packs here claim different things in their JSON fields and render the same
        # Memory bytes: that disagreement is the tamper.
        shared = json.dumps({"status": "ok"}, separators=(",", ":")) + \
            f"\nCITED {self.source_event}\n"
        lesson = self._lesson_pack(memory_block=shared)
        lesson["rendered_content"] = _render_pack(
            project=self.project, task_id=self.lesson_task,
            source_event_id=self.lesson_prompt,
            title=self.module.G01_TASK_TITLE, status="ok", memory_block=shared)
        baseline = self._baseline_pack(memory_block=shared)
        baseline["rendered_content"] = _render_pack(
            project=self.project, task_id=self.baseline_task,
            source_event_id=self.baseline_prompt,
            title=self.module.G01_TASK_TITLE, status="ok", memory_block=shared)
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson_pack=lesson, baseline_pack=baseline)
        self.assertIn("identical to the", str(caught.exception))

    def test_a_category_outside_the_closed_vocabulary_is_refused(self) -> None:
        # A category invented for this run is a way to report a saving nobody measured.
        lesson = self._session(performed=set())
        lesson["exploration"] = dict(lesson["exploration"])
        lesson["exploration"]["ask_the_model"] = True
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson=lesson)
        message = str(caught.exception)
        self.assertIn("outside the closed set", message)
        self.assertIn("ask_the_model", message)

    def test_an_undeclared_category_is_refused(self) -> None:
        lesson = self._session(performed=set())
        del lesson["exploration"]["read_documentation"]
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson=lesson)
        self.assertIn("did not say whether it performed", str(caught.exception))

    def test_a_session_that_did_not_finish_is_refused(self) -> None:
        # The defect in its most flattering form: one action against eleven, and the
        # case called it a saving.
        lesson = self._session(performed={"reproduce_failure"}, completed=False)
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson=lesson)
        message = str(caught.exception)
        self.assertIn("task_completed", message)
        self.assertIn("not completing the task", message)

    def test_a_verification_that_did_not_pass_is_refused(self) -> None:
        lesson = self._session(performed={"reproduce_failure"},
                               output="Ran 1 test in 0.001s\n\nFAILED (failures=1)\n")
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson=lesson)
        self.assertIn("does not carry", str(caught.exception))

    def test_a_baseline_that_did_not_finish_is_refused(self) -> None:
        baseline = self._session(performed={"enumerate_repository"}, completed=False,
                                 memory_seen=False, commands=("ls",))
        with self.assertRaises(self.module.Failed) as caught:
            self._run(baseline=baseline)
        self.assertIn("did not declare task_completed", str(caught.exception))

    def test_no_omitted_category_is_not_a_benefit(self) -> None:
        lesson = self._session(
            performed={"enumerate_repository", "read_history", "reproduce_failure"},
            commands=("ls", "git log --oneline", "pytest -q"))
        with self.assertRaises(self.module.Failed) as caught:
            self._run(lesson=lesson)
        message = str(caught.exception)
        self.assertIn("performed every exploration category", message)
        self.assertIn("not a difference in", message)


class _PackGate:
    """A Gate that only has receipts, one sealed pack receipt, and a scripted session.

    `read_receipt` is the runner's own, so the re-derivation of a sealed fact out of the
    receipt file is real: these tests cannot pass by handing the verifier the answer.
    """

    def __init__(self, module, root, *, session, seeded=None):
        self.module = module
        self.root = root
        self._gate = module.Gate.__new__(module.Gate)
        self._gate.root = root
        self._gate.case_id = "P4-G0X"
        self.session = session
        if seeded is not None:
            self._gate.receipt(seeded[0], seeded[1])

    def receipt(self, kind, payload):
        return self._gate.receipt(kind, payload)

    def read_receipt(self, kind):
        return self._gate.read_receipt(kind)

    def verify_session(self, pack, label, *, require_tool_actions=True,
                         require_evidence_backed_actions=False):
        session = self.session
        if require_tool_actions and not (session.get("tool_actions") or []):
            raise self.module.Failed(f"{label} recorded no tool action")
        return session

    def seal_session_artifacts(self, record, session):
        self.module.Gate.seal_session_artifacts(self, record, session)

    def semantic_review(self, session, label):
        return self._gate.semantic_review(session, label)


class G03JudgesAbsenceAndFabricationTogether(unittest.TestCase):
    """The two requirements are one judgement, and an answer can fail it by both.

    Verified directly against the sealed pack: "There is no record of it in Core; the
    device 42 protocol test passed every time it ran" satisfied an absence scan and a
    fabrication scan at the same time, because they were two independent substring tests
    over one string.
    """

    def setUp(self) -> None:
        from jasmine_core import ids

        self.module = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-g03-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G03"
        self.root.mkdir(parents=True)
        self.project = ids.new_id("prj")
        self.pack = {"context_pack_id": "ctx_" + ids.new_id("ctx")[4:],
                     "project_id": self.project, "task_id": ids.new_id("tsk"),
                     "prompt_event_id": ids.new_id("evt"), "memory_status": "empty",
                     "memory_selected": [], "memory_filtered": [],
                     "qualified_provenance": [],
                     "rendered_content": 'JASMINE CONTEXT v1\n{"status":"empty"}\n'}

    def _answer(self, text, *, verdict="consistent", digest=None, reviewer="a reviewer"):
        import hashlib

        review = {"reviewer": reviewer, "verdict": verdict,
                  "reviewed_answer_sha256": digest
                  if digest is not None
                  else hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  "findings": f"reviewed the quoted answer {text[:40]!r}: consistent"}
        return {"final_answer": text, "tool_actions": [], "memory_seen": [],
                "semantic_review": review,
                "verified": {"tool_actions": 0, "memory_carried": [],
                             "session_transcript": {"path": "t", "sha256": "0" * 64},
                             "injection": {}, "sealed": []}}

    def _run(self, text, **kwargs):
        gate = _PackGate(self.module, self.root, session=self._answer(text, **kwargs))
        record = self.module.CaseRecord("P4-G03", "PASS")
        self.module._verify_g03_pack(gate, record, self.pack)
        return record

    def test_an_honest_negative_with_a_review_is_accepted(self) -> None:
        record = self._run("There is no record of the device 42 protocol test in Core, "
                           "so nothing about its results is available.")
        self.assertEqual(record.receipts[-1]["kind"], "g03-session")

    def test_the_two_sentence_answer_is_refused(self) -> None:
        # The defect, verbatim.
        with self.assertRaises(self.module.Failed) as caught:
            self._run("There is no record of it in Core; the device 42 protocol test "
                      "passed every time it ran.")
        message = str(caught.exception)
        self.assertIn("same sentence that denies the record", message)
        self.assertIn("passed", message)

    def test_one_clause_that_denies_and_asserts_is_refused(self) -> None:
        # Same failure without the semicolon: the absence must not vouch for the
        # assertion sitting beside it.
        with self.assertRaises(self.module.Failed) as caught:
            self._run("There is no record of the device 42 protocol test in Core and it "
                      "passed every time it ran.")
        self.assertIn("asserts", str(caught.exception))

    def test_an_answer_that_only_asserts_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run("The device 42 protocol test passed every time it ran.")
        self.assertIn("asserts", str(caught.exception))

    def test_an_answer_that_never_admits_the_absence_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run("The device 42 protocol test was green across the board.")
        self.assertIn("does not admit the absence", str(caught.exception))

    def test_a_negated_mention_is_still_honest(self) -> None:
        # "Nothing verified it" is the denial, not a fabrication, and a session that
        # says so correctly must not be failed for it.
        self._run("Nothing verified the device 42 protocol test; it never ran in this "
                  "project.")

    def test_a_missing_review_is_refused(self) -> None:
        session = self._answer("There is no record of the device 42 protocol test in "
                               "Core.")
        del session["semantic_review"]
        gate = _PackGate(self.module, self.root, session=session)
        with self.assertRaises(self.module.Failed) as caught:
            self.module._verify_g03_pack(gate, self.module.CaseRecord("P4-G03", "PASS"),
                                         self.pack)
        self.assertIn("no semantic_review block", str(caught.exception))

    def test_a_review_of_a_different_answer_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run("There is no record of the device 42 protocol test in Core.",
                      digest="a" * 64)
        message = str(caught.exception)
        self.assertIn("attached to a different answer", message)

    def test_an_inconsistent_review_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run("There is no record of the device 42 protocol test in Core.",
                      verdict="inconsistent")
        self.assertIn("returned 'inconsistent'", str(caught.exception))

    def test_the_reviewer_reaches_the_receipt(self) -> None:
        import hashlib

        record = self._run("There is no record of the device 42 protocol test in Core.",
                           reviewer="jasmine independent reviewer")
        sealed = json.loads(Path(record.receipts[-1]["path"]).read_text(encoding="utf-8"))
        review = sealed["semantic_review"]
        self.assertEqual(review["reviewer"], "jasmine independent reviewer")
        self.assertEqual(review["verdict"], "consistent")
        self.assertEqual(
            review["reviewed_answer_sha256"],
            hashlib.sha256("There is no record of the device 42 protocol test in Core."
                           .encode("utf-8")).hexdigest())


class G04ReadsProjectIdentityFromTheWholePack(unittest.TestCase):
    """The identity scan lowercased the haystack and kept the needle's capitals.

    So `re.findall(r"prj_[0-9A-Z]{26}", rendered.lower())` found nothing, `named` was
    always the empty set, and `named <= {project_b}` was true by construction. A foreign
    project id injected into a pack changed nothing.
    """

    def setUp(self) -> None:
        from jasmine_core import ids

        self.module = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-g04-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G04"
        self.root.mkdir(parents=True)
        self.project_a = ids.new_id("prj")
        self.project_b = ids.new_id("prj")
        self.foreign = ids.new_id("prj")

    def _pack(self, *, rendered=None, project_id=None, memory_selected=()):
        body = rendered if rendered is not None else self._rendered(
            rules=[{"rule_id": "rul_" + "B" * 26, "version": 1,
                    "scope": {"kind": "project", "project_id": self.project_b},
                    "content": "Project B must stay API compatible"}])
        return {"context_pack_id": "ctx_" + "C" * 26,
                "project_id": project_id if project_id is not None else self.project_b,
                "task_id": "tsk_" + "D" * 26, "prompt_event_id": "evt_" + "E" * 26,
                "memory_status": "ok", "memory_selected": list(memory_selected),
                "memory_filtered": [], "qualified_provenance": [],
                "rendered_content": body}

    def _rendered(self, *, rules, header_project=None):
        compact = dict(separators=(",", ":"), ensure_ascii=False)
        header = "JASMINE CONTEXT v1\n" + json.dumps({
            "project_id": header_project or self.project_b, "task_id": "tsk_" + "D" * 26,
            "task_revision": 1, "source_event_id": "evt_" + "E" * 26}, **compact) + "\n"
        authority = "AUTHORITY\n" + "".join(
            json.dumps({**rule, "content": rule["content"]}, **compact) + "\n"
            + rule["content"] + "\n" for rule in rules)
        state = "CURRENT STATE\n" + json.dumps({"task": {"task_id": "tsk_" + "D" * 26}},
                                               **compact) + "\n"
        memory = "MEMORY — NON AUTHORITATIVE\n" + json.dumps({"status": "ok"},
                                                            **compact) + "\n"
        return header + authority + state + memory

    def _run(self, pack=None):
        import hashlib

        answer = "you must stay API compatible for project B"
        gate = _PackGate(self.module, self.root,
                         session={"final_answer": answer,
                                  "tool_actions": [],
                                  "semantic_review": {
                                      "reviewer": "an independent reviewer",
                                      "verdict": "consistent",
                                      "reviewed_answer_sha256": hashlib.sha256(
                                          answer.encode("utf-8")).hexdigest(),
                                      "findings": f"quotes {answer[:25]!r}: states B's constraint alone"},
                                  "verified": {"tool_actions": 0, "memory_carried": [],
                                               "session_transcript": {},
                                               "injection": {}, "sealed": []}},
                         seeded=("g04-recall", {"project_a": self.project_a,
                                                "project_b": self.project_b}))
        record = self.module.CaseRecord("P4-G04", "PASS")
        self.module._verify_g04_pack(gate, record,
                                     pack if pack is not None else self._pack())
        return record

    def test_a_clean_pack_of_project_b_is_accepted(self) -> None:
        self.assertEqual(self._run().verdict, "PASS")

    def test_a_foreign_project_id_anywhere_in_the_pack_is_refused(self) -> None:
        # The defect. A bank id, a provenance row, a lowercase copy: the scan has to
        # see all of them, and the comparison has to be case-blind on purpose so that a
        # differently-cased copy of the *same* id is not mistaken for a foreign one.
        foreign_lowercase = "prj_" + self.foreign[4:].lower()
        for injection in (
            f"jasmine-project-{self.foreign}",
            f"jasmine-project-{self.project_a}",
            foreign_lowercase,
        ):
            with self.subTest(injection=injection):
                rendered = self._rendered(
                    rules=[{"rule_id": "rul_" + "B" * 26, "version": 1,
                            "scope": {"kind": "project", "project_id": self.project_b},
                            "content": "Project B must stay API compatible"}])
                leaked = rendered.replace("CURRENT STATE",
                                          f"BANK {injection}\nCURRENT STATE")
                with self.assertRaises(self.module.Failed) as caught:
                    self._run(self._pack(rendered=leaked))
                self.assertIn("names other projects", str(caught.exception))

    def test_a_lowercase_copy_of_project_b_is_not_a_foreign_project(self) -> None:
        # The normalisation is case-blind so that a re-cased copy of B compares equal.
        rendered = self._rendered(
            rules=[{"rule_id": "rul_" + "B" * 26, "version": 1,
                    "scope": {"kind": "project", "project_id": self.project_b},
                    "content": "Project B must stay API compatible"}])
        recased = rendered.replace("CURRENT STATE",
                                   f"NOTE {self.project_b.lower()}\nCURRENT STATE")
        self.assertEqual(self._run(self._pack(rendered=recased)).verdict, "PASS")

    def test_a_pack_built_for_another_project_is_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run(self._pack(project_id=self.project_a))
        self.assertIn("cannot be evidence about", str(caught.exception))

    def test_a_header_naming_another_project_is_refused(self) -> None:
        rendered = self._rendered(
            rules=[{"rule_id": "rul_" + "B" * 26, "version": 1,
                    "scope": {"kind": "project", "project_id": self.project_b},
                    "content": "Project B must stay API compatible"}],
            header_project=self.project_a)
        with self.assertRaises(self.module.Failed) as caught:
            self._run(self._pack(rendered=rendered))
        message = str(caught.exception)
        self.assertIn("HEADER names project", message)
        self.assertIn(self.project_a, message)

    def test_an_authority_rule_scoped_to_another_project_is_refused(self) -> None:
        rendered = self._rendered(
            rules=[{"rule_id": "rul_" + "B" * 26, "version": 1,
                    "scope": {"kind": "project", "project_id": self.project_b},
                    "content": "Project B must stay API compatible"},
                   {"rule_id": "rul_" + "A" * 26, "version": 1,
                    "scope": {"kind": "project", "project_id": self.project_a},
                    "content": "Project A may change the API shape freely"}])
        with self.assertRaises(self.module.Failed) as caught:
            self._run(self._pack(rendered=rendered))
        message = str(caught.exception)
        self.assertIn("AUTHORITY section carries rule", message)
        self.assertIn("project A's", message)
        self.assertIn(self.project_a, message)

    def test_a_pack_with_no_project_id_at_all_is_refused(self) -> None:
        rendered = "JASMINE CONTEXT v1\n" + json.dumps({"task_id": "tsk_" + "D" * 26}) + \
            "\nAUTHORITY\nCURRENT STATE\nMEMORY — NON AUTHORITATIVE\n"
        with self.assertRaises(self.module.Failed) as caught:
            self._run(self._pack(rendered=rendered))
        self.assertIn("HEADER names project", str(caught.exception))

    def test_the_other_projects_decision_is_still_refused(self) -> None:
        # The assertion that did work, and has to keep working next to the new ones: a
        # correctly scoped rule whose *text* still carries project A's freedom.
        rendered = self._rendered(
            rules=[{"rule_id": "rul_" + "B" * 26, "version": 1,
                    "scope": {"kind": "project", "project_id": self.project_b},
                    "content": "Project B must stay API compatible; unlike A it may not "
                               "change the API shape freely"}])
        with self.assertRaises(self.module.Failed) as caught:
            self._run(self._pack(rendered=rendered))
        self.assertIn("carries project A's Decision", str(caught.exception))


class G02StatesItsSupersessionPremise(unittest.TestCase):
    """`not filtered or any(...)` is true of an empty list, and the list is empty.

    P3's `filter_memory` can only report items the *Core* refused; the superseded
    Decision is dropped by the recall, so the pack's filtered list cannot contain one.
    The check therefore never fired, and the manifest implied a filter had been seen.
    """

    def setUp(self) -> None:
        from jasmine_core import ids

        self.module = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-g02-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G02"
        self.root.mkdir(parents=True)
        self.project = ids.new_id("prj")
        self.rendered = (
            "JASMINE CONTEXT v1\n"
            + json.dumps({"project_id": self.project, "task_id": "tsk_" + "D" * 26},
                         separators=(",", ":")) + "\n"
            + "AUTHORITY\n"
            + json.dumps({"rule_id": "rul_" + "B" * 26, "version": 2,
                          "kind": "DECISION",
                          "scope": {"kind": "project", "project_id": self.project}},
                         separators=(",", ":")) + "\n"
            + "V1 will use Hindsight for long-term memory\n"
            + "CURRENT STATE\n"
            + "MEMORY — NON AUTHORITATIVE\n")

    def _run(self, filtered, *, recall_filtered="default"):
        import hashlib

        pack = {"context_pack_id": "ctx_" + "C" * 26, "project_id": self.project,
                "task_id": "tsk_" + "D" * 26, "prompt_event_id": "evt_" + "E" * 26,
                "memory_status": "ok", "memory_selected": [], "memory_filtered": filtered,
                "qualified_provenance": [], "rendered_content": self.rendered}
        if recall_filtered == "default":
            recall_filtered = [{"bank_id": f"jasmine-project-{self.project}",
                                "memory_id": "mem_" + "F" * 26,
                                "reason": "superseded_decision"}]
        answer = "V1 uses Hindsight for long-term memory"
        gate = _PackGate(self.module, self.root,
                         session={"final_answer": answer,
                                  "tool_actions": [],
                                  "semantic_review": {
                                      "reviewer": "an independent reviewer",
                                      "verdict": "consistent",
                                      "reviewed_answer_sha256": hashlib.sha256(
                                          answer.encode("utf-8")).hexdigest(),
                                      "findings": f"quotes {answer[:30]!r}: names the current engine"},
                                  "verified": {"tool_actions": 0, "memory_carried": [],
                                               "session_transcript": {},
                                               "injection": {}, "sealed": []}},
                         seeded=("g02-recall", {"filtered": recall_filtered}))
        record = self.module.CaseRecord("P4-G02", "PASS")
        self.module._verify_g02_pack(gate, record, pack)
        return record

    def test_an_unobserved_premise_is_refused_not_recorded(self) -> None:
        # The empty pack `filtered` list is the expected shape -- the recall
        # behind the pack filters before rendering -- so the premise lives in
        # the sealed recall receipt. When even that receipt shows no
        # superseded filter, the case has no contest for the current Truth to
        # win and must fail rather than record the absence as a passing note.
        with self.assertRaises(self.module.Failed) as caught:
            self._run([], recall_filtered=[])
        message = str(caught.exception)
        self.assertIn("premise", message)
        self.assertIn("g02-recall", message)

    def test_an_observed_premise_is_recorded_as_observed(self) -> None:
        record = self._run([{"bank_id": f"jasmine-project-{self.project}",
                             "memory_id": "mem_" + "F" * 26,
                             "reason": "superseded_decision"}])
        self.assertTrue(record.engine_evidence["supersession_observed_in_pack"])
        self.assertIn("observed at the pack",
                      record.engine_evidence["supersession_premise"])

    def test_a_filter_for_another_reason_is_still_refused(self) -> None:
        with self.assertRaises(self.module.Failed) as caught:
            self._run([{"bank_id": f"jasmine-project-{self.project}",
                        "memory_id": "mem_" + "F" * 26, "reason": "foreign_project"}])
        self.assertIn("other than supersession", str(caught.exception))


class InheritedEvidenceIsMarkedAndReasserted(unittest.TestCase):
    """A resumed phase has to say which half of its evidence came from the other phase.

    G01..G05 and G07 re-verify the pack and the session on `--resume`; the engine halves
    ran in the earlier phase and are only re-hashed. The FINAL manifest used to show
    `engine_evidence: {}` for three of them and nothing anywhere saying that half the
    assertions came from a phase whose own verdict may have been BLOCKED.
    """

    def setUp(self) -> None:
        self.module = load_runner()
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-gate-inherit-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "work" / "P4-G03"
        self.root.mkdir(parents=True)

    def _gate(self, payload):
        gate = _PackGate(self.module, self.root, session={})
        gate.receipt("g03-empty-trace", payload)
        return gate

    def test_the_inherited_keys_are_named_and_the_load_bearing_one_rechecked(self) -> None:
        record = self.module.CaseRecord("P4-G03", "PASS")
        self.module.mark_inherited_evidence(
            self._gate({"status": "empty", "trace_id": "trc_1"}), record,
            {"evidence_keys": ["probe", "recall"], "verdict": "PASS"})
        inherited = record.inherited_evidence
        self.assertEqual(inherited["inherited_evidence_keys"], ["probe", "recall"])
        self.assertEqual(inherited["asserted_in_this_phase"], [])
        self.assertEqual([row["receipt"] for row in inherited["reasserted_from_sealed_receipts"]],
                         ["g03-empty-trace"])
        self.assertEqual(record.as_dict()["inherited_evidence"], inherited)

    def test_a_receipt_that_no_longer_supports_the_claim_fails_the_case(self) -> None:
        # The reverse test. A tampered or edited receipt cannot be used to inherit a
        # PASS: the claim is re-checked here, and a claim that no longer holds is a FAIL
        # of this case rather than a quiet inheritance.
        record = self.module.CaseRecord("P4-G03", "PASS")
        with self.assertRaises(self.module.Failed) as caught:
            self.module.mark_inherited_evidence(
                self._gate({"status": "ok", "trace_id": "trc_1"}), record,
                {"evidence_keys": ["probe"], "verdict": "PASS"})
        message = str(caught.exception)
        self.assertIn("inherits this evidence from the earlier phase", message)
        self.assertIn("not `empty`", message)

    def test_a_missing_inherited_receipt_is_refused_rather_than_skipped(self) -> None:
        # The case whose inherited claim has no receipt on disk to check it against
        # cannot quietly inherit: it fails, rather than reporting the claim as verified.
        record = self.module.CaseRecord("P4-G04", "PASS")
        with self.assertRaises(self.module.Failed) as caught:
            self.module.mark_inherited_evidence(self._gate({"status": "empty"}), record,
                                                {"evidence_keys": ["acl"]})
        self.assertIn("is missing", str(caught.exception))

    def test_a_first_phase_inherits_nothing(self) -> None:
        record = self.module.CaseRecord("P4-G03", "PASS")
        record.engine_evidence["differential"] = {"x": 1}
        self.module.mark_inherited_evidence(self._gate({"status": "empty"}), record, None)
        self.assertEqual(record.inherited_evidence, {})
        self.assertNotIn("inherited_evidence", record.as_dict())

    def test_a_case_with_no_receipt_names_what_it_could_not_recheck(self) -> None:
        # Honesty in the other direction: G01's inherited keys are listed, and the note
        # says plainly that they were not re-verified here rather than leaving the
        # reader to assume they were.
        record = self.module.CaseRecord("P4-G01", "PASS")
        self.module.mark_inherited_evidence(self._gate({"status": "empty"}), record,
                                            {"evidence_keys": ["probe"]})
        self.assertEqual(record.inherited_evidence["inherited_evidence_keys"], ["probe"])
        self.assertEqual(record.inherited_evidence["reasserted_from_sealed_receipts"], [])
        self.assertIn("not re-verified in this phase",
                      record.inherited_evidence["note"])
