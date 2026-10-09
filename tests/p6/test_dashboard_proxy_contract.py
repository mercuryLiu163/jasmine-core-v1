"""P6-01 dashboard proxy contract (read-only, no network, no browser).

Two failure modes this guards against:

1. The browser allowlist in ``web/server/core-paths.json`` names a path that
   Core does not implement — a "plausible" endpoint that would 404 at runtime
   while looking deliberate in review.
2. Core grows a route the dashboard claims to need but the allowlist never
   forwards, or the allowlist quietly widens to an admin surface.

It derives concrete sample paths from Core's *actual* route table
(``jasmine_core.api.handlers.ROUTES``) rather than from a hand-written list, so
it cannot drift in the same direction as the file it checks.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The baseline the dashboard's event filter was written against (main, schema 8). Completeness is
#: measured against *this* commit, read from Git, so a later baseline that adds event types is an
#: improvement rather than a failure — which is what the integration rehearsal recorded.
BASELINE_REF = "850fbecac516ca55e396dc13bf722a28b1dd7837"
MODELS_PATH = "src/jasmine_core/models.py"


def baseline_event_types() -> set[str]:
    source = subprocess.run(["git", "show", f"{BASELINE_REF}:{MODELS_PATH}"],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout
    marker = "EVENT_TYPES = frozenset({"
    start = source.index(marker) + len(marker)
    end = source.index("})", start)
    return set(re.findall(r'"([^"]+)"', source[start:end]))
sys.path.insert(0, str(ROOT / "src"))

from jasmine_core.api.handlers import ROUTES  # noqa: E402

ALLOWLIST_PATH = ROOT / "web" / "server" / "core-paths.json"
#: Admin/key surfaces the browser proxy must never forward, whatever a token holds.
FORBIDDEN_PREFIXES = ("/v1/auth/", "/v1/audit")
#: The synthetic value substituted for a captured segment in a Core pattern.
#: Every captured segment in Core's route table is a public id, so this is a
#: well-formed id of Core's own shape (ADR 0001: ``<prefix>_`` + 26 Crockford
#: base32 characters). A word like ``sample`` would not be a path the dashboard
#: could ever produce, and would stop the allowlist from being checked against
#: the shape it actually uses.
SAMPLE = "abc_01M4B6VGC29ESRDRQR62DX8AK8"
#: Core's own id shape, as the allowlist spells it.
ID_RE = r"[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}"


def sample_path(pattern: re.Pattern[str]) -> str | None:
    """Turn a Core route regex into one concrete path it matches.

    Core's patterns use exactly two constructs: ``([^/]+)`` captures and
    ``(a|b|c)`` alternations, so picking the first alternative and a literal
    sample segment yields a path that pattern matches.
    """
    source = pattern.pattern
    if not source.startswith("^/") or not source.endswith("$"):
        return None
    body = source[1:-1]
    body = body.replace("([^/]+)", SAMPLE)
    # Pick the first alternative of every remaining group.
    while True:
        replaced = re.sub(r"\(([^()|]+)\|[^()]*\)", r"\1", body, count=1)
        if replaced == body:
            break
        body = replaced
    if "(" in body or ")" in body or "[" in body:
        return None
    return body


class DashboardProxyContract(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(ALLOWLIST_PATH.exists(), f"missing {ALLOWLIST_PATH}")
        self.allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        self.allowed = [re.compile(pattern) for pattern in self.allowlist["paths"]]
        self.methods = {method.upper() for method in self.allowlist["methods"]}
        self.write_paths = [re.compile(pattern) for pattern in self.allowlist.get("write_paths", [])]
        self.write_methods = {method.upper() for method in self.allowlist.get("write_methods", [])}

    def core_samples(self) -> list[str]:
        samples: list[str] = []
        for route in ROUTES:
            candidate = sample_path(route.pattern)
            self.assertIsNotNone(candidate, f"cannot derive a sample for {route.pattern.pattern}")
            samples.append(candidate)
        return samples

    def test_read_allowlist_only_names_real_get_routes(self) -> None:
        """The GET allowlist must not admit a path Core only answers for POST.

        `test_every_allowlisted_path_exists_in_core` accepts a match against *any*
        method, so it cannot see this: the loose `[A-Za-z0-9_]+` id patterns also
        matched POST-only literals, and `GET /v1/rules/proposals` was forwarded to
        Core (which answered 400 `rule_id must be a rul_ id`) instead of being
        refused by the proxy. Ids use Core's real shape, so those words cannot
        match, and every remaining GET pattern must correspond to a real GET route.
        """
        by_method = self.core_samples_by_method()
        get_samples = [p for method, p in by_method if method == "GET"]
        # A path that exists as both GET and POST is legitimately matched by a GET
        # pattern; only a path with *no* GET route is evidence of over-broadness.
        post_only = [p for method, p in by_method
                     if method != "GET" and p not in get_samples]
        for pattern in self.allowed:
            with self.subTest(pattern=pattern.pattern):
                self.assertTrue(
                    any(pattern.fullmatch(sample) for sample in get_samples),
                    f"GET pattern {pattern.pattern!r} matches no real GET route",
                )
                over_broad = [sample for sample in post_only if pattern.fullmatch(sample)]
                self.assertFalse(
                    over_broad,
                    f"GET pattern {pattern.pattern!r} also matches non-GET route(s) "
                    f"{over_broad}, so the proxy would forward them",
                )

    def test_allowlists_are_exactly_the_frozen_shapes(self) -> None:
        """Silent widening is the failure mode, so pin both sets exactly."""
        self.assertEqual(
            self.allowlist["paths"],
            [
                "^/v1/health$", "^/v1/meta/schema$", "^/v1/hosts$", "^/v1/actors$",
                "^/v1/projects$", f"^/v1/projects/{ID_RE}$",
                "^/v1/tasks$", f"^/v1/tasks/{ID_RE}$",
                f"^/v1/tasks/{ID_RE}/(steps|history|evidence)$",
                "^/v1/sessions$", f"^/v1/sessions/{ID_RE}$",
                f"^/v1/steps/{ID_RE}$", f"^/v1/steps/{ID_RE}/history$",
                "^/v1/events$", f"^/v1/events/{ID_RE}$",
                "^/v1/rules/active$",
                f"^/v1/rules/{ID_RE}$", f"^/v1/rules/{ID_RE}/history$",
                f"^/v1/evidence/{ID_RE}$",
                "^/v1/interpretations$", f"^/v1/interpretations/{ID_RE}$",
                "^/v1/reviews/pending$", f"^/v1/reviews/{ID_RE}$",
                "^/v1/resolutions/preview$", f"^/v1/resolutions/{ID_RE}$",
            ],
        )
        self.assertEqual(
            self.allowlist["write_paths"],
            [
                f"^/v1/interpretations/{ID_RE}/(correct|reject|rerun)$",
                f"^/v1/reviews/{ID_RE}/(approve|reject)$",
                "^/v1/rules/proposals$",
                f"^/v1/rules/{ID_RE}/(approve|supersede|retire)$",
                # P6-02b Task/Step state writes: always-on, because this baseline Core serves
                # all seven (`handlers.py:115-124`) and `tests/test_state.py` drives them over
                # the real HTTP surface. A new pattern here is a deliberate contract change.
                "^/v1/tasks$",
                f"^/v1/tasks/{ID_RE}/steps$",
                f"^/v1/tasks/{ID_RE}/transition$",
                f"^/v1/tasks/{ID_RE}/criteria$",
                f"^/v1/tasks/{ID_RE}/accept$",
                f"^/v1/steps/{ID_RE}/transition$",
                f"^/v1/steps/{ID_RE}/criteria$",
            ],
        )

    def core_samples_by_method(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for route in ROUTES:
            candidate = sample_path(route.pattern)
            self.assertIsNotNone(candidate, f"cannot derive a sample for {route.pattern.pattern}")
            out.append((route.method, candidate))
        return out

    def test_every_allowlisted_path_exists_in_core(self) -> None:
        samples = self.core_samples()
        for pattern in self.allowed:
            backed = [sample for sample in samples if pattern.fullmatch(sample)]
            self.assertTrue(
                backed,
                f"allowlist pattern {pattern.pattern!r} matches no real Core route",
            )

    def test_allowlist_never_covers_an_admin_surface(self) -> None:
        for pattern in self.allowed:
            for prefix in FORBIDDEN_PREFIXES:
                self.assertIsNone(
                    pattern.fullmatch(prefix + "keys"),
                    f"{pattern.pattern!r} can reach {prefix}",
                )
                self.assertIsNone(
                    pattern.fullmatch(prefix + "keys/key_1/revoke"),
                    f"{pattern.pattern!r} can revoke a key",
                )
        for sample in ("/v1/auth/keys", "/v1/auth/keys/key_1/revoke", "/v1/audit"):
            self.assertFalse(
                any(pattern.fullmatch(sample) for pattern in self.allowed),
                f"{sample} must not be reachable through the browser proxy",
            )
            self.assertFalse(
                any(pattern.fullmatch(sample) for pattern in self.write_paths),
                f"{sample} must not be reachable through the write proxy either",
            )

    def test_read_surface_is_get_only(self) -> None:
        self.assertEqual({"GET"}, self.methods)
        for sample in self.core_samples():
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                self.assertNotIn(
                    method,
                    self.methods,
                    f"{method} {sample} would be a write from the browser",
                )

    def test_write_surface_is_narrow_and_real(self) -> None:
        """The always-on POST surface is exactly the frozen routes and nothing else.

        The write section is separate from the read section on purpose: adding a
        write path must not widen what GET may reach, and every write pattern must
        be backed by a real Core route. The seven P6-02b Task/Step patterns are
        included because this baseline Core serves them; the out-of-scope writes
        stay closed.
        """
        self.assertEqual({"POST"}, self.write_methods)
        samples = self.core_samples()
        for pattern in self.write_paths:
            backed = [sample for sample in samples if pattern.fullmatch(sample)]
            self.assertTrue(backed, f"write pattern {pattern.pattern!r} matches no Core route")
        # The seven Task/Step routes must each be covered, not merely "no pattern is junk":
        # a missing pattern would show up as a browser 404 that looks like a Core refusal.
        post_samples = [p for method, p in self.core_samples_by_method() if method == "POST"]
        for route in (
            "/v1/tasks",
            f"/v1/tasks/{SAMPLE}/steps",
            f"/v1/tasks/{SAMPLE}/transition",
            f"/v1/tasks/{SAMPLE}/criteria",
            f"/v1/tasks/{SAMPLE}/accept",
            f"/v1/steps/{SAMPLE}/transition",
            f"/v1/steps/{SAMPLE}/criteria",
        ):
            self.assertIn(route, post_samples, f"{route} is not a real Core POST route")
            self.assertTrue(
                any(pattern.fullmatch(route) for pattern in self.write_paths),
                f"{route} is served by Core but not forwarded by the dashboard proxy",
            )
        # The endpoints deliberately NOT opened in this slice. The *plural*
        # `/v1/workspaces/fingerprint` is the path Core actually serves
        # (`handlers.py:113`); the singular spelling was a vacuous assertion and is kept
        # only as a typo guard.
        for path in (
            "/v1/evidence/confirm",
            "/v1/resolutions/res_01M4B6VGC29ESRDRQR62DX8AK8/manual-reapply",
            "/v1/resolve/int_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/tool-results",
            "/v1/workspaces/fingerprint",
            "/v1/workspace/fingerprint",
        ):
            self.assertFalse(
                any(pattern.fullmatch(path) for pattern in self.write_paths),
                f"{path} must not be open to the browser in this slice",
            )
        # Every one of the five out-of-scope POSTs is a *real* Core route, so the proxy
        # refusal is a deliberate boundary rather than "Core has no such path". The
        # browser-level counterpart (spec §11 negative 9) drives a real browser and is a
        # Gate obligation; this test pins the allowlist side of it.
        for route in (
            "/v1/evidence/confirm",
            f"/v1/resolutions/{SAMPLE}/manual-reapply",
            f"/v1/resolve/{SAMPLE}",
            "/v1/tool-results",
            "/v1/workspaces/fingerprint",
        ):
            self.assertIn(
                route,
                post_samples,
                f"{route} must be a real Core route, or the absence check proves nothing",
            )
        # Reads stay GET-only even though writes exist.
        self.assertEqual({"GET"}, self.methods)

    def test_read_paths_the_dashboard_needs_are_allowed(self) -> None:
        needed = [
            "/v1/health",
            "/v1/meta/schema",
            "/v1/hosts",
            "/v1/actors",
            "/v1/projects",
            "/v1/projects/prj_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/tasks",
            "/v1/tasks/tsk_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/tasks/tsk_01M4B6VGC29ESRDRQR62DX8AK8/steps",
            "/v1/tasks/tsk_01M4B6VGC29ESRDRQR62DX8AK8/history",
            "/v1/tasks/tsk_01M4B6VGC29ESRDRQR62DX8AK8/evidence",
            "/v1/steps/stp_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/steps/stp_01M4B6VGC29ESRDRQR62DX8AK8/history",
            "/v1/sessions",
            "/v1/sessions/ses_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/events",
            "/v1/events/evt_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/rules/active",
            "/v1/rules/rul_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/rules/rul_01M4B6VGC29ESRDRQR62DX8AK8/history",
            "/v1/evidence/evd_01M4B6VGC29ESRDRQR62DX8AK8",
            # P6-02 read slice: the real P2 collection/detail paths.
            "/v1/interpretations",
            "/v1/interpretations/int_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/reviews/pending",
            "/v1/reviews/rvw_01M4B6VGC29ESRDRQR62DX8AK8",
            "/v1/resolutions/preview",
            "/v1/resolutions/res_01M4B6VGC29ESRDRQR62DX8AK8",
        ]
        for path in needed:
            self.assertTrue(
                any(pattern.fullmatch(path) for pattern in self.allowed),
                f"{path} is used by the dashboard but not forwarded by the proxy",
            )

    def test_p2_collections_have_no_wildcard_collection_route(self) -> None:
        # `/v1/reviews` and `/v1/resolutions` are deliberately NOT collection
        # GETs, so the allowlist must not make them look like one.
        for path in ("/v1/reviews", "/v1/resolutions"):
            self.assertFalse(
                any(pattern.fullmatch(path) for pattern in self.allowed),
                f"{path} must not be allowlisted as a collection",
            )
            self.assertFalse(
                any(pattern.fullmatch(path) for pattern in self.allowed),
                f"{path} must not be reachable",
            )

    def test_conversation_event_filter_matches_the_core_enum(self) -> None:
        """The UI dropdown must not offer a type Core cannot emit, and must cover its baseline.

        The dashboard renders unknown event types verbatim, so a stale list is a usability bug
        rather than a truth bug - but it is exactly the kind of silent drift a contract test
        should catch.

        This asserted **equality** with the running Core's enum, which turned into a false failure
        the moment the dependency branches are merged in: the Core gains types the baseline
        dashboard never had, and the dashboard is not wrong for not knowing them yet. The two
        directions are therefore measured separately:

        * never name a type the running Core does not define (holds in every baseline);
        * be complete for the **pinned baseline's** enum, read from Git - so adding types later is
          an improvement to be adopted, not a test failure.
        """
        source = (ROOT / "web" / "src" / "views" / "ConversationView.tsx").read_text(encoding="utf-8")
        marker = "const EVENT_TYPES = ["
        start = source.index(marker) + len(marker)
        end = source.index("] as const", start)
        block = source[start:end]
        listed = set(re.findall(r"'([^']+)'", block))
        from jasmine_core.models import EVENT_TYPES

        self.assertEqual(
            sorted(listed - EVENT_TYPES), [],
            "dashboard filter lists event types Core does not define",
        )
        self.assertEqual(
            sorted(baseline_event_types() - listed), [],
            f"the dashboard filter is missing event types {BASELINE_REF[:12]} already defined",
        )
        # And the filter is still a real list, not an empty one that trivially passes.
        self.assertGreater(len(listed), 5)

    def test_traversal_and_absolute_targets_are_not_matched(self) -> None:
        for hostile in (
            "/v1/../../etc/passwd",
            "/v1/tasks/../tasks",
            "//evil.invalid/v1/health",
            "http://evil.invalid/v1/health",
            "/v1/",
            "/health",
        ):
            self.assertFalse(
                any(pattern.fullmatch(hostile) for pattern in self.allowed),
                f"{hostile} must not be forwarded",
            )


if __name__ == "__main__":
    unittest.main()
