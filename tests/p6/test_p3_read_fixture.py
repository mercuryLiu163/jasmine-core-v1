"""The simulated P3 read fixture: shapes, labels, and the negatives it must produce.

The fixture exists because P6-03a's dependency is missing, so its honesty is the whole
point. Two properties matter and are checked here rather than asserted in prose:

1. every payload matches the **frozen contract** exactly — a drifted fixture would make
   every screenshot evidence for a shape nobody agreed to, so the builders assert the
   field set at construction time;
2. every reply is **labelled** as simulated, and the negative paths a browser run relies
   on (unknown id, malformed id, missing scope, a Task with no checkpoint) really produce
   the statuses the views branch on.

The HTTP cases start the fixture on an ephemeral port, so the tests exercise the same
handler the click-through Gate talks to.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_P3_SOURCE_CACHE: dict[tuple[str, str], str] = {}


def _p3_source(ref: str, path: str) -> str:
    """P3's file at `ref`, or skip the test. Cached: several tests read the same file.

    These guards are only as good as the checkout: with the P3 branch absent there is
    nothing to compare against, and a skip says so rather than passing on a hardcoded copy.
    """
    if (ref, path) in _P3_SOURCE_CACHE:
        return _P3_SOURCE_CACHE[(ref, path)]
    try:
        source = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        raise unittest.SkipTest(
            f"P3 ref {ref!r} is not in this checkout, so {path} cannot be compared "
            f"({error.__class__.__name__})") from error
    _P3_SOURCE_CACHE[(ref, path)] = source
    return source


def _effective_dict_keys(source: str, func_name: str, var_name: str) -> frozenset[str]:
    """Every key `var_name` ends up with in `func_name`, including post-hoc assignment.

    P3 builds the comparison `diff` as a literal and *then* adds `workspace_paths` by
    subscript assignment (`diff['workspace_paths'] = {...}`). A literal-only reading would
    report a key set that the returned document does not have — the first version of this
    guard did exactly that and failed against a correct fixture.
    """
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == func_name):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Assign)
                    and isinstance(inner.targets[0], ast.Name)
                    and inner.targets[0].id == var_name
                    and isinstance(inner.value, ast.Dict)):
                keys |= {k.value for k in inner.value.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if (isinstance(inner, ast.Assign)
                    and isinstance(inner.targets[0], ast.Subscript)
                    and isinstance(inner.targets[0].value, ast.Name)
                    and inner.targets[0].value.id == var_name):
                slice_node = inner.targets[0].slice
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    keys.add(slice_node.value)
        return frozenset(keys)
    raise AssertionError(f"{func_name} not found in the P3 source")


def _dict_key_sets(source: str, func_name: str) -> set[frozenset[str]]:
    """Every string-keyed dict literal inside `func_name`, as key sets.

    A rename or removal in P3 changes one of these frozensets, so a fixture that still
    serves the old key stops matching.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            out: set[frozenset[str]] = set()
            for literal in ast.walk(node):
                if not isinstance(literal, ast.Dict):
                    continue
                keys = [k.value for k in literal.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if keys:
                    out.add(frozenset(keys))
            return out
    raise AssertionError(f"{func_name} not found in the P3 source")

_spec = importlib.util.spec_from_file_location(
    "p3_read_fixture", ROOT / "scripts" / "p6" / "p3_read_fixture.py")
fixture = importlib.util.module_from_spec(_spec)
sys.modules["p3_read_fixture"] = fixture
_spec.loader.exec_module(fixture)

CONTRACT = json.loads((ROOT / "web" / "contracts" / "p3-continuity-read.v1.json")
                      .read_text(encoding="utf-8"))


def call(port: int, path: str, token: str | None = "p3fixture-token") -> tuple[int, dict, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode()), dict(error.headers)


class PayloadShapes(unittest.TestCase):
    def test_every_payload_matches_the_frozen_field_set(self) -> None:
        # The builders assert internally too; this states the expectation where a reader
        # of the tests will see it, and fails loudly if that assertion is ever removed.
        checkpoint = fixture.checkpoint_payload(
            CONTRACT, ident=fixture.CHECKPOINT, revision=1, reason="MANUAL",
            source=fixture.SOURCE_EVENT, command=fixture.COMMAND_EVENT, note=None)
        self.assertEqual(set(checkpoint), set(CONTRACT["response_fields"]["checkpoint"]))
        self.assertEqual(set(fixture.resume_payload(CONTRACT)),
                         set(CONTRACT["response_fields"]["resume"]))
        self.assertEqual(set(fixture.context_payload(CONTRACT)),
                         set(CONTRACT["response_fields"]["context"]))

    def test_a_checkpoint_without_a_note_carries_null_not_a_fabricated_one(self) -> None:
        without = fixture.checkpoint_payload(
            CONTRACT, ident=fixture.CHECKPOINT, revision=1, reason="MANUAL",
            source=fixture.SOURCE_EVENT, command=fixture.COMMAND_EVENT, note=None)
        self.assertIsNone(without["note"])

    def test_the_nested_shapes_mirrored_from_p3_carry_its_keys(self) -> None:
        """The fixture has no Truth store, so it cannot serve P3's *values*.

        It can still serve P3's *shapes*, and for the nested documents the UI renders it
        must: the first cut sent `next_actions` as a list of strings where P3 returns a list
        of objects, which exercised a branch real P3 never reaches.

        This reads the key sets out of **P3's own source**, so a rename there fails here
        rather than going unnoticed. The ref is the one the contract pins; where it is
        absent the test skips loudly instead of passing on a hardcoded copy.
        """
        ref = CONTRACT["source"]["ref"]
        source = _p3_source(ref, "src/jasmine_core/continuity.py")
        renderer = _p3_source(ref, "src/jasmine_core/context/renderer.py")

        resume = fixture.resume_payload(CONTRACT)
        # continuity.next_actions emits `{'kind': ...}` on the reconciliation branch and
        # `{'kind', 'step_id', 'status'}` otherwise. Never a string.
        self.assertEqual(resume["next_actions"], [{"kind": "RECONCILE_CURRENT_TRUTH"}])
        self.assertIn(frozenset({"kind"}), _dict_key_sets(source, "next_actions"))
        self.assertIn(frozenset({"kind", "step_id", "status"}), _dict_key_sets(source, "next_actions"))

        # continuity._compare_checkpoint returns the comparison document, and builds `diff` and its
        # `workspace_paths` as dict literals in the same function.
        comparison_sets = _dict_key_sets(source, "_compare_checkpoint")
        self.assertIn(frozenset(resume["comparison"]), comparison_sets)
        # `diff` gains `workspace_paths` by assignment after its literal, so the effective
        # key set is the literal's keys plus the assigned one.
        self.assertEqual(frozenset(resume["comparison"]["diff"]),
                         _effective_dict_keys(source, "_compare_checkpoint", "diff"))
        self.assertIn(frozenset(resume["comparison"]["diff"]["workspace_paths"]), comparison_sets)

        # context.renderer.render builds each section metric and each omission as a literal.
        pack = fixture.context_payload(CONTRACT)
        renderer_sets = _dict_key_sets(renderer, "render")
        for metric in pack["section_metrics"]:
            self.assertIn(frozenset(metric), renderer_sets)
        for omission in pack["omissions"]:
            self.assertIn(frozenset(omission), renderer_sets)

    def test_a_rename_in_p3_would_break_the_guard(self) -> None:
        """Falsify the guard by mutating P3's source, not by mutating the expectation.

        If the derivation ignored the source — or read the wrong function — a renamed key
        would leave it matching, and this test would pass for the wrong reason. So the
        source text itself is changed and the derived sets must change with it.
        """
        ref = CONTRACT["source"]["ref"]
        continuity = _p3_source(ref, "src/jasmine_core/continuity.py")
        renderer = _p3_source(ref, "src/jasmine_core/context/renderer.py")

        renamed_comparison = continuity.replace("'checkpoint_freshness'", "'checkpoint_freshness_v2'")
        self.assertNotEqual(renamed_comparison, continuity, "P3 no longer uses that key")
        self.assertNotIn(
            frozenset({"checkpoint_freshness", "workspace", "warnings",
                       "reconciliation_required", "diff"}),
            _dict_key_sets(renamed_comparison, "_compare_checkpoint"),
            "renaming the key in P3 must stop the fixture's set from matching")

        renamed_metric = renderer.replace("'prefix_delta_tokens'", "'prefix_delta_tokens_v2'")
        self.assertNotEqual(renamed_metric, renderer, "P3 no longer uses that key")
        self.assertNotIn(
            frozenset({"section", "utf8_start", "utf8_end", "sha256", "standalone_tokens",
                       "cumulative_prefix_tokens", "prefix_delta_tokens"}),
            _dict_key_sets(renamed_metric, "render"),
            "renaming a metric key in P3 must stop the fixture's set from matching")

    def test_the_resume_note_denies_its_own_authority(self) -> None:
        note = fixture.resume_payload(CONTRACT)["note"]
        self.assertIs(note["authority"], False)
        self.assertIs(note["historical"], True)


class OverHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = CONTRACT
        fixture.Handler.contract = CONTRACT
        fixture.Handler.tokens = {
            "full": set(fixture.ALL_SCOPES),
            "limited": {"objects:read", "checkpoint:read"},
            # Carries the route-table scope for a context pack but NOT the `checkpoint:read`
            # that P3's `_context_read` helper also requires. A contract that recorded only
            # the route scope would call this token sufficient; real P3 refuses it, so the
            # fixture must too.
            "route-scope-only": {"context:read", "objects:read", "state:read",
                                 "authority:read", "evidence:read", "events:read"},
            # Carries ONLY the route scope, so it is missing both `checkpoint:read` and the
            # five `_continuity_read` scopes. P3 checks the five first and names
            # `objects:read`; this pins the order, not just the set.
            "context-only": {"context:read"},
        }
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_the_four_reads_answer_200(self) -> None:
        for path in (f"/v1/checkpoints/{fixture.CHECKPOINT}",
                     f"/v1/context/{fixture.PACK}",
                     f"/v1/resumes/{fixture.RESUME}",
                     f"/v1/tasks/{fixture.TASK}/checkpoints/latest"):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path, "full")
                self.assertEqual(status, 200)
                key = ("resume" if "/resumes/" in path else
                       "context" if "/context/" in path else "checkpoint")
                self.assertIn(key, body)

    def test_every_reply_is_labelled_simulated(self) -> None:
        # The label is what stops a screenshot from this fixture being read as evidence
        # about a real Core.
        status, _, headers = call(self.port, f"/v1/checkpoints/{fixture.CHECKPOINT}", "full")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-P6-Fixture"), fixture.FIXTURE_HEADER)

    def test_a_task_with_no_checkpoint_is_200_and_null(self) -> None:
        status, body, _ = call(
            self.port, f"/v1/tasks/{fixture.TASK_EMPTY}/checkpoints/latest", "full")
        self.assertEqual(status, 200)
        self.assertIsNone(body["checkpoint"])

    def test_unknown_ids_are_404_and_malformed_ones_are_400(self) -> None:
        unknown = f"ckp_{'01M4B6VGC29ESRDRQR62DX8AK7'}"
        for path, expected, code in (
            (f"/v1/checkpoints/{unknown}", 404, "not_found"),
            (f"/v1/context/ctx_{'01M4B6VGC29ESRDRQR62DX8AK7'}", 404, "not_found"),
            (f"/v1/resumes/rms_{'01M4B6VGC29ESRDRQR62DX8AK7'}", 404, "not_found"),
            ("/v1/checkpoints/ckp_short", 400, "invalid_request"),
            ("/v1/context/ctx_short", 400, "invalid_request"),
            ("/v1/resumes/rms_short", 400, "invalid_request"),
        ):
            with self.subTest(path=path):
                status, body, _ = call(self.port, path, "full")
                self.assertEqual((status, body["error"]["code"]), (expected, code))

    #: A sample path per contract route, so a scope test can call the real route.
    SAMPLE_PATHS = {
        r"^/v1/context/([^/]+)$": "/v1/context/" + fixture.PACK,
        r"^/v1/tasks/([^/]+)/checkpoints/latest$":
            "/v1/tasks/" + fixture.TASK + "/checkpoints/latest",
        r"^/v1/checkpoints/([^/]+)$": "/v1/checkpoints/" + fixture.CHECKPOINT,
        r"^/v1/resumes/([^/]+)$": "/v1/resumes/" + fixture.RESUME,
    }

    def test_a_scope_the_route_table_does_not_mention_is_still_required(self) -> None:
        """The route-table scope is not the whole requirement.

        `GET /v1/context/{id}` also runs P3's `_context_read`, which requires
        `checkpoint:read`. A token with `context:read` but without it must be refused —
        otherwise the fixture would accept a credential real P3 rejects, and the frozen
        contract would under-state the scope set an operator has to grant.
        """
        status, body, _ = call(self.port, self.SAMPLE_PATHS[r"^/v1/context/([^/]+)$"],
                               "route-scope-only")
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertEqual(body["error"]["details"]["required"], "checkpoint:read")

    def test_the_refusal_names_the_scope_core_would_name_first(self) -> None:
        """Order, not just membership: P3 checks the shared scopes before `checkpoint:read`.

        A token with only `context:read` is missing six scopes. Real P3 runs
        `_context_read` -> `_continuity_read` first, so it refuses with `objects:read`; a
        fixture that named `checkpoint:read` would be reporting a different requirement
        than Core, and an operator following the message would grant the wrong scope first.
        """
        status, body, _ = call(self.port, self.SAMPLE_PATHS[r"^/v1/context/([^/]+)$"],
                               "context-only")
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertEqual(body["error"]["details"]["required"], "objects:read")

    def test_every_route_enforces_the_full_contract_scope_set(self) -> None:
        """Drop each required scope in turn; the route must refuse, naming that scope.

        This is the guard against a contract that records only the route-table scope: it
        fails the moment a scope P3 requires is missing from `extra_scopes`, because the
        fixture would then answer 200 for a token that lacks it.
        """
        for route in CONTRACT["read_routes"]:
            path = self.SAMPLE_PATHS[route["path_pattern"]]
            full = [route["scope"], *route["extra_scopes"]]
            for drop in full:
                token = "minus-" + drop
                fixture.Handler.tokens[token] = set(fixture.ALL_SCOPES) - {drop}
                with self.subTest(pattern=route["path_pattern"], missing=drop):
                    status, body, _ = call(self.port, path, token)
                    self.assertEqual(status, 403, body)
                    self.assertEqual(body["error"]["details"]["required"], drop)
                    self.assertEqual(body["error"]["details"]["granted"], sorted(
                        set(fixture.ALL_SCOPES) - {drop}))

    def test_each_route_also_answers_with_every_scope_granted(self) -> None:
        """The negative direction needs its positive control, or the test above would
        pass even if the fixture refused everything."""
        for route in CONTRACT["read_routes"]:
            with self.subTest(pattern=route["path_pattern"]):
                status, _, _ = call(self.port, self.SAMPLE_PATHS[route["path_pattern"]], "full")
                self.assertEqual(status, 200)

    def test_a_missing_scope_is_403_and_names_itself(self) -> None:
        status, body, _ = call(self.port, f"/v1/context/{fixture.PACK}", "limited")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden_scope")
        self.assertEqual(body["error"]["details"]["required"], "context:read")
        # A resume needs two scopes, and the one it lacks is the one it must name.
        status, body, _ = call(self.port, f"/v1/resumes/{fixture.RESUME}", "limited")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["details"]["required"], "resume:read")

    def test_no_token_is_401(self) -> None:
        status, body, _ = call(self.port, f"/v1/checkpoints/{fixture.CHECKPOINT}", None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthenticated")

    def test_the_build_shapes_are_not_operations_in_this_slice(self) -> None:
        """`GET` on a build shape must not look like a served read.

        The two cases differ, and both match what real P3 would do, which is why they are
        asserted separately rather than as a blanket 404:

        * `GET /v1/context/build` matches P3's `GET /v1/context/{id}` route with the id
          `"build"`, and `ContextStore.get` rejects a non-`ctx_` id — `400 invalid_request`;
        * `GET /v1/checkpoints` matches no route at all — `404 not_found`.
        """
        status, body, _ = call(self.port, "/v1/context/build", "full")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_request"))
        status, body, _ = call(self.port, "/v1/checkpoints", "full")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))


if __name__ == "__main__":
    unittest.main()
