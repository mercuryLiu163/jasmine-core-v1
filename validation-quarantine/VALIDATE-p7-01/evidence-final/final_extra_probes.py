#!/usr/bin/env python3
"""Supplementary probes beyond the six criteria, kept short.

These are not part of the six acceptance criteria; they are recorded so the
report can state what else was spot-checked: the routes are GET-only, an unset
or symlinked root refuses with `validation_not_configured`, a malformed id is a
400, and an unknown query key is refused.

Run: PYTHONPATH=../../src:. python3 extra_probes.py
"""

from __future__ import annotations

import os
from pathlib import Path

from final_harness import Live, Checker, emit, write_run

OUT = Path(__file__).resolve().parent
RUN = "run-20261008T000001Z"


def main() -> int:
    c = Checker("supplementary probes")
    live = Live()
    try:
        granted = live.key(["validation:read"])
        root = live.base / "extra-root"
        write_run(root / "leaf" / RUN, RUN)

        # unset root
        saved = os.environ.pop("JASMINE_CORE_VALIDATION_RUNS", None)
        try:
            status, _, body, _ = live.call("GET", "/v1/validation/runs", granted)
            c.check("unset root is 503 validation_not_configured", status == 503, status)
            c.check("unset root reason is 'unset'",
                    body.get("error", {}).get("details", {}).get("reason") == "unset",
                    body.get("error", {}).get("details"))
        finally:
            if saved is not None:
                os.environ["JASMINE_CORE_VALIDATION_RUNS"] = saved

        # symlinked root
        link = live.base / "extra-link"
        os.symlink(root, link)
        with live.root(link):
            status, _, body, _ = live.call("GET", "/v1/validation/runs", granted)
            c.check("symlinked root is 503", status == 503, status)
            c.check("symlinked root reason is 'symlinked_root'",
                    body.get("error", {}).get("details", {}).get("reason")
                    == "symlinked_root", body.get("error", {}).get("details"))

        with live.root(root):
            status, _, body, _ = live.call(
                "GET", "/v1/validation/runs/not-a-run-id", granted)
            c.check("malformed run id is 400 invalid_request", status == 400, status)
            c.check("malformed run id code is invalid_request",
                    body.get("error", {}).get("code") == "invalid_request",
                    body.get("error", {}).get("code"))
            status, _, body, _ = live.call(
                "GET", "/v1/validation/runs?bogus=1", granted)
            c.check("unknown query key is 400 invalid_request", status == 400, status)
            status, headers, body, _ = live.call("POST", "/v1/validation/runs", granted)
            c.check("POST to the list route is 405 method_not_allowed", status == 405,
                    status)
            c.check("the allowed method list is [GET]",
                    body.get("error", {}).get("details", {}).get("allowed") == ["GET"],
                    body.get("error", {}).get("details"))
        emit(OUT / "extra-01-probes.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
