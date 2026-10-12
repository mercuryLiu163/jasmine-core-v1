#!/usr/bin/env python3
"""Independent, read-only scan of the real run records.

Validator-authored. Reads (never writes) the operator's real run tree and
reports, per record and in aggregate, exactly the facts criterion 1 and
criterion 2 turn on: verdicts, failure_class/failure_output, stage, case
counts, and for every case the presence/kind of `context` and `agent_response`.

Usage:
    python3 analysis_real_records.py [ROOT]
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = ("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1"
                "/validation-runs")
SENTINEL = "N/A (P0 not implemented)"
REASON_KEYS = {"reason", "note", "detail", "not_applied_reason"}
PACK_KEYS = {"context_id", "pack", "sections", "rendered", "content", "tokens", "parts"}


def context_kind(value, present):
    if not present:
        return "absent"
    if value == SENTINEL:
        return "sentinel"
    if isinstance(value, dict) and set(value.keys()) <= REASON_KEYS:
        return "reason_only"
    if isinstance(value, dict):
        return "run_details(no pack)"
    if value in ("", [], None):
        return "empty"
    return "value(other)"


def pack_present(value):
    if isinstance(value, dict):
        return bool(set(value.keys()) & PACK_KEYS)
    if isinstance(value, list):
        return len(value) > 0
    return False


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT)
    paths = sorted(root.rglob("run-*.json"))
    print(f"ROOT: {root}")
    print(f"RUN-SHAPED FILES: {len(paths)}")
    aggregate = Counter()
    for path in paths:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        cases = data.get("cases", [])
        case_verdicts = Counter(c.get("verdict") for c in cases)
        rec = {
            "relative_path": str(path.relative_to(root)),
            "record_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "run_id": data.get("run_id"),
            "stem": path.stem,
            "stage": data.get("stage"),
            "status_field": data.get("status"),
            "verdict": data.get("verdict"),
            "created_at": data.get("created_at"),
            "core_version": data.get("core_version"),
            "schema_version": data.get("schema_version"),
            "commit": data.get("commit"),
            "synthetic": data.get("synthetic", False),
            "top_level_keys": sorted(data.keys()),
            "case_count": len(cases),
            "case_verdicts": dict(case_verdicts),
            "fail_cases": case_verdicts.get("FAIL", 0),
            "failure_class_present": sum(
                1 for c in cases if c.get("failure_class") not in (None, "")),
            "failure_output_present": sum(
                1 for c in cases if c.get("failure_output") not in (None, "")),
            "context_kinds": dict(Counter(
                context_kind(c.get("context"), "context" in c) for c in cases)),
            "context_pack_present": sum(
                1 for c in cases if pack_present(c.get("context"))),
            "agent_response_kinds": dict(Counter(
                ("absent" if "agent_response" not in c else
                 "sentinel" if c.get("agent_response") == SENTINEL else
                 "empty" if c.get("agent_response") in ("", []) else "value")
                for c in cases)),
            "evidence_entries": sum(len(c.get("evidence") or []) for c in cases),
        }
        print(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        aggregate["records"] += 1
        aggregate["cases"] += len(cases)
        aggregate[f"verdict_{data.get('verdict')}"] += 1
        aggregate["fail_cases"] += rec["fail_cases"]
        aggregate["failure_class_present"] += rec["failure_class_present"]
        aggregate["failure_output_present"] += rec["failure_output_present"]
        for kind, n in rec["context_kinds"].items():
            aggregate[f"context_{kind}"] += n
        for kind, n in rec["agent_response_kinds"].items():
            aggregate[f"agent_{kind}"] += n
    print("AGGREGATE: " + json.dumps(dict(sorted(aggregate.items())), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
