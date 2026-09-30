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
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jasmine_core.api.handlers import ROUTES  # noqa: E402

ALLOWLIST_PATH = ROOT / "web" / "server" / "core-paths.json"
#: Admin/key surfaces the browser proxy must never forward, whatever a token holds.
FORBIDDEN_PREFIXES = ("/v1/auth/", "/v1/audit")
#: The synthetic value substituted for a captured segment in a Core pattern.
SAMPLE = "sample"


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

    def core_samples(self) -> list[str]:
        samples: list[str] = []
        for route in ROUTES:
            candidate = sample_path(route.pattern)
            self.assertIsNotNone(candidate, f"cannot derive a sample for {route.pattern.pattern}")
            samples.append(candidate)
        return samples

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

    def test_p6_01_is_read_only(self) -> None:
        self.assertEqual({"GET", "OPTIONS"}, self.methods)
        for sample in self.core_samples():
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertNotIn(
                    method,
                    self.methods,
                    f"{method} {sample} would be a write from the browser",
                )

    def test_read_paths_the_dashboard_needs_are_allowed(self) -> None:
        needed = [
            "/v1/health",
            "/v1/meta/schema",
            "/v1/hosts",
            "/v1/actors",
            "/v1/projects",
            "/v1/projects/prj_x",
            "/v1/tasks",
            "/v1/tasks/tsk_x",
            "/v1/tasks/tsk_x/steps",
            "/v1/tasks/tsk_x/history",
            "/v1/tasks/tsk_x/evidence",
            "/v1/steps/stp_x",
            "/v1/steps/stp_x/history",
            "/v1/sessions",
            "/v1/sessions/ses_x",
            "/v1/events",
            "/v1/events/evt_x",
            "/v1/rules/active",
            "/v1/rules/rul_x",
            "/v1/rules/rul_x/history",
            "/v1/evidence/evd_x",
        ]
        for path in needed:
            self.assertTrue(
                any(pattern.fullmatch(path) for pattern in self.allowed),
                f"{path} is used by the dashboard but not forwarded by the proxy",
            )

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
