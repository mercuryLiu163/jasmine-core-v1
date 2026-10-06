#!/usr/bin/env python3
"""Functional preflight for the Hindsight engine a P4 Gate run is about to use.

An API that answers and a version string are not evidence that the engine can do
what the Gate asks of it. A live 0.10.2 deployment has been observed returning
``success: true, items_count: 1`` for a document whose ``memory_unit_count``
stayed 0 -- the retain "succeeded" and nothing was ever extractable -- so a
preflight that stops at `/version` or `/health` can green-light an engine that
cannot retain anything. This script therefore runs the real chain on the real
engine and asserts the stored artifact, not the receipt:

  1. database connected            -- `/health` reports the database connected
                                      (the API answering with a dead database is
                                      the exact state that motivated this step)
  2. real retain                   -- a real document is sent and acknowledged
  3. extraction complete           -- the document comes back with >= 1 unit,
                                      polled because extraction is model-backed
  4. document/units read-back      -- the document and its unit listing agree
  5. recall                        -- the stored content is recallable

Exit codes:
  0  every check passed
  2  environment BLOCKED -- stop and fix the environment; a Gate run started now
     would fail from the environment, and repeating full runs would not tell you
     anything new
  1  this script itself failed (a defect here, not in the environment)

Nothing here prints a credential: the transport strips userinfo from the URL, and
only the origin is reported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jasmine_core import ids  # noqa: E402
from jasmine_core.memory import scope  # noqa: E402
from jasmine_core.memory.hindsight import HindsightConfig, HindsightTransport  # noqa: E402


def _health(origin: str, timeout: float) -> dict[str, object]:
    """`GET /health` from the engine root, not the versioned API prefix."""
    request = urllib.request.Request(f"{origin}/health",
                                     headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError(f"/health returned a {type(parsed).__name__}, not an object")
    return parsed


def run_preflight(url: str, namespace: str, timeout: float, keep: bool) -> int:
    report: dict[str, object] = {"engine": url, "bank_namespace": namespace,
                                 "checks": {}, "started_at": time.strftime(
                                     "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    checks: dict[str, dict[str, object]] = report["checks"]  # type: ignore[assignment]

    def done(name: str, ok: bool, **detail: object) -> None:
        checks[name] = {"passed": bool(ok), **detail}

    config = HindsightConfig(url, timeout_seconds=timeout)
    transport = HindsightTransport(config)

    # -- 1. database connected -------------------------------------------------
    try:
        health = _health(config.origin_host, timeout)
        status = str(health.get("status") or "")
        database = str(health.get("database") or "")
        # Strict: the database field must say connected. A payload without the
        # field is not evidence of a database, and judging by `status` alone is
        # how "the API answers" gets mistaken for "the engine works".
        connected = database == "connected"
        done("database_connected", connected,
             status=status or None, database=database or None,
             detail=None if connected else
             f"the engine reports database={database or 'absent'!r}; an API that "
             f"answers with no database behind it is not a usable engine")
        if not connected:
            raise SystemExit(0)
    except SystemExit:
        print(_blocked(report, "database_connected"))
        return 2
    except (urllib.error.URLError, ValueError, OSError) as exc:
        done("database_connected", False, error=f"{type(exc).__name__}: {exc}")
        print(_blocked(report, "database_connected"))
        return 2

    try:
        version = transport.version()
        report["api_version"] = version.get("api_version")
    except Exception as exc:  # noqa: BLE001 - reported, not fatal by itself
        report["api_version_error"] = f"{type(exc).__name__}: {exc}"

    # -- 2. real retain ----------------------------------------------------------
    project_id = ids.new_id("prj")
    bank = scope.project_bank(project_id)
    nonce = ids.new_id("evt")
    document_id = f"preflight-{nonce[-12:].lower()}"
    content = (f"The preflight nonce is {nonce}. This document exists only to prove "
               f"that the engine can retain, extract, read back and recall content.")
    report["document_id"] = document_id
    try:
        receipt = transport.retain(
            bank=bank, document_id=document_id, content=content,
            context="preflight drill", metadata={"preflight_nonce": nonce},
            tags=(), memory_type="FAILURE_LESSON", timestamp=None)
        done("real_retain", True, items_count=receipt.get("items_count"),
             operation_id=receipt.get("operation_id"))
    except Exception as exc:  # noqa: BLE001
        done("real_retain", False, error=f"{type(exc).__name__}: {exc}")
        print(_blocked(report, "real_retain"))
        return 2

    # -- 3. extraction complete --------------------------------------------------
    # `async=false` means the retain itself waited for extraction, but the same
    # deployment that acknowledged a document with zero units showed the receipt
    # is not the artifact. Poll the document until it carries a unit, because
    # extraction is a model call and can trail the acknowledgement.
    deadline = time.monotonic() + timeout
    document = None
    units: list[dict[str, object]] = []
    last_error: str | None = None
    while True:
        try:
            document = transport.get_document(bank=bank, document_id=document_id)
            units = list(transport.list_all_document_memories(
                bank=bank, document_id=document_id))
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            document, units = None, []
        count = int((document or {}).get("memory_unit_count") or 0)
        if document is not None and count >= 1 and units:
            break
        if time.monotonic() >= deadline:
            done("extraction_complete", False,
                 document_present=document is not None,
                 memory_unit_count=int((document or {}).get("memory_unit_count") or 0),
                 units_listed=len(units), last_error=last_error,
                 detail="no unit was extracted for the retained document within the "
                        "deadline; a document with zero units is unrecallable and "
                        "must not pass a preflight")
            print(_blocked(report, "extraction_complete"))
            return 2
        time.sleep(2.0)
    done("extraction_complete", True,
         memory_unit_count=int(document.get("memory_unit_count") or 0),
         units_listed=len(units), attempts=None)

    # -- 4. document/units read-back ---------------------------------------------
    unit_ids = [str(u.get("id")) for u in units]
    read_back_ok = (
        document.get("id") == document_id
        and int(document.get("memory_unit_count") or 0) == len(units)
        and all(unit_ids)
        and len(set(unit_ids)) == len(unit_ids)
    )
    done("document_units_read_back", read_back_ok,
         document_id=document.get("id"), memory_unit_count=document.get("memory_unit_count"),
         units=len(units), distinct_unit_ids=len(set(unit_ids)),
         detail=None if read_back_ok else
         "the document and its unit listing do not agree; the read-back cannot be "
         "trusted as coverage")
    if not read_back_ok:
        print(_blocked(report, "document_units_read_back"))
        return 2

    # -- 5. recall ----------------------------------------------------------------
    try:
        results = transport.recall(bank=bank, query=nonce, max_tokens=300,
                                   fact_types=(), tags=())
        bound = [r for r in results if getattr(r, "document_id", None) == document_id]
        textual = [r for r in results if nonce in getattr(r, "text", "")]
        recalled = bool(bound or textual)
        done("recall", recalled, results=len(results),
             bound_to_document=len(bound), mentioning_nonce=len(textual),
             detail=None if recalled else
             "the retained content is not recallable; the bank stored a document it "
             "cannot find again")
        if not recalled:
            print(_blocked(report, "recall"))
            return 2
    except Exception as exc:  # noqa: BLE001
        done("recall", False, error=f"{type(exc).__name__}: {exc}")
        print(_blocked(report, "recall"))
        return 2

    # -- cleanup (not one of the five; removal failure is a warning) --------------
    if keep:
        report["cleanup"] = {"performed": False, "reason": "--keep"}
    else:
        try:
            transport.delete_document(bank=bank, document_id=document_id)
            gone = transport.get_document(bank=bank, document_id=document_id) is None
            report["cleanup"] = {"performed": True, "document_gone": gone}
        except Exception as exc:  # noqa: BLE001
            report["cleanup"] = {"performed": False,
                                 "warning": f"{type(exc).__name__}: {exc}"}

    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["verdict"] = "PASS"
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _blocked(report: dict[str, object], failed: str) -> str:
    report["verdict"] = "BLOCKED"
    checks = report.get("checks") or {}
    detail = (checks.get(failed) or {}).get("detail") or (checks.get(failed) or {}).get("error")
    lines = [f"environment BLOCKED: {failed} failed",
             f"  engine: {report.get('engine')}" if report.get("engine") else "",
             f"  {detail}" if detail else "",
             "  fix the environment before a Gate run; repeating full runs against "
             "a blocked environment tells you nothing new",
             json.dumps(report, indent=2, ensure_ascii=False)]
    return "\n".join(line for line in lines if line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine-url", default=os.environ.get("JASMINE_HINDSIGHT_URL"),
                        help="engine base URL (default: $JASMINE_HINDSIGHT_URL)")
    parser.add_argument("--bank-namespace",
                        default=os.environ.get("JASMINE_MEMORY_BANK_NAMESPACE", ""),
                        help="bank namespace (default: $JASMINE_MEMORY_BANK_NAMESPACE)")
    parser.add_argument("--timeout", type=float,
                        default=float(os.environ.get("JASMINE_HINDSIGHT_TIMEOUT_SECONDS", 300)),
                        help="seconds allowed for each model-backed operation")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the preflight document afterwards")
    args = parser.parse_args()
    if not args.engine_url:
        print("environment BLOCKED: no engine URL (set JASMINE_HINDSIGHT_URL or "
              "--engine-url)")
        return 2
    if args.bank_namespace:
        os.environ["JASMINE_MEMORY_BANK_NAMESPACE"] = args.bank_namespace
    try:
        return run_preflight(args.engine_url, scope.bank_namespace(), args.timeout,
                             args.keep)
    except Exception as exc:  # noqa: BLE001 - a defect here, not the environment
        print(f"preflight itself failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
