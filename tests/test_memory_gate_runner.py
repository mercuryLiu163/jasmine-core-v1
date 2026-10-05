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

from support import SRC  # noqa: F401  (path setup)

from jasmine_core import errors  # noqa: E402
from jasmine_core.memory import scope  # noqa: E402

GATE = SRC.parent / "scripts" / "p4-memory-gate.py"
CASES = ("P4-G01", "P4-G02", "P4-G03", "P4-G04", "P4-G05", "P4-G06", "P4-G07")


def load_runner():
    """Import the runner as a module, registered in `sys.modules`.

    The registration matters: the runner defines dataclasses, and `@dataclass`
    looks its own module up in `sys.modules` while decorating. Loading it
    anonymously would fail inside the standard library rather than in the runner.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("p4_memory_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    self_addCleanup = getattr(module, "__spec__", None)
    del self_addCleanup
    spec.loader.exec_module(module)
    return module


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
            self.assertTrue(case["blocked_on"], case["case_id"])
            self.assertIn("did not answer", case["reason"])
        self.assertFalse(manifest["engine"]["reachable"])
        self.assertEqual(manifest["phase"], "P4")
        self.assertIsNone(manifest["final_commit"])
        self.assertIn("not Gate evidence", " ".join(manifest["notes"]))


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
