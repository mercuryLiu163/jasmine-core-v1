#!/usr/bin/env python3.13
"""Render a frozen P6 evidence bundle into the file set the P6 taskbook §8 asks for.

The taskbook's §8 lists what every bundle must contain: `manifest.json`, `scenario.md`,
`click-trace.jsonl`, masked `api-requests.jsonl` / `api-responses.jsonl`,
`truth-before-after.json`, the relevant source mappings, `screenshots/`, raw test output and
`result.md`. The bundles this project produced carry a different but overlapping set
(`BUNDLE-FROZEN.json`, `README.md`, `click-trace.jsonl`, `browser-requests.jsonl`,
`evidence.json`, raw logs, screenshots). This tool closes that naming/set gap **without touching a
frozen bundle and without inventing anything**:

* it writes into a **separate derived directory** and refuses to run if the output is the bundle or
  inside it;
* it refuses a bundle that is not frozen, and it re-verifies every file against the bundle's own
  `BUNDLE-FROZEN.json` (the same check `freeze_bundle.py --verify` performs) before deriving —
  **a tampered, missing or added source file aborts the derivation**;
* it records the source bundle, the acceptance commit and the sha256 of every source file in the
  derived `manifest.json`, so a reader can tie the derived files to exact bytes;
* where the §8 file has no recorded content it writes an explicit `not-recorded` marker with the
  files it looked at. It never synthesises a request, a response or a Truth comparison;
* the derived `result.md` **restates** the counts the source bundle records and says in words that
  format conformance is not acceptance: the binding verdict stays the source bundle's.

Idempotent by construction: no timestamps, no environment-dependent fields, deterministic key and
file order. Two runs over the same bundle are byte-identical (`--check` proves it).

Usage::

    python3.13 scripts/p6/bundle_conformance.py --bundle <frozen bundle> --out <derived dir>
    python3.13 scripts/p6/bundle_conformance.py --bundle <bundle> --out <derived dir> --check
    python3.13 scripts/p6/bundle_conformance.py --bundle <bundle> --out <dir> \
        --expect-commit f323c779bc91893b5908218d8c5c148619159180

Exit codes: 0 derived / identical; 1 source failed verification or `--check` differs; 2 not frozen,
bad paths, or a usage problem. "Could not look" is never 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from freeze_bundle import verify as verify_bundle  # noqa: E402

GENERATOR = "scripts/p6/bundle_conformance.py"
GENERATOR_VERSION = "1"
MANIFEST = "BUNDLE-FROZEN.json"

#: The files this tool writes, in a fixed order, so a diff between two derived directories is stable.
DERIVED_FILES = (
    "manifest.json",
    "scenario.md",
    "api-requests.jsonl",
    "api-responses.jsonl",
    "truth-before-after.json",
    "result.md",
    "NOTES.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def not_recorded(reason: str, checked: list[str]) -> dict[str, Any]:
    """The marker for a §8 file the bundle has nothing for. Never a fabricated empty result."""
    return {"status": "not-recorded", "reason": reason, "sources_that_were_checked": checked}


def derive_manifest(bundle: Path, source: dict[str, Any], commit: str) -> dict[str, Any]:
    files = {entry["path"]: entry["sha256"] for entry in source.get("files", [])}
    return {
        "generator": {"path": GENERATOR, "version": GENERATOR_VERSION,
                      "note": "renders §8 file names from the source bundle's own records; it does "
                              "not re-run, re-judge or upgrade any Gate"},
        "source_bundle": {"path": str(bundle), "manifest": MANIFEST,
                          "commit": commit,
                          "kind": source.get("kind"),
                          "runs": source.get("runs"),
                          "ran_from": source.get("ran_from"),
                          "frozen_at_utc": source.get("frozen_at_utc"),
                          "dependency": source.get("dependency"),
                          "simulation_label": source.get("simulation_label"),
                          "product_bundle": source.get("product_bundle")},
        "source_files": dict(sorted(files.items())),
        "source_manifest_sha256": sha256_file(bundle / MANIFEST),
        "derived_files": list(DERIVED_FILES),
    }


def derive_scenario(bundle: Path, source: dict[str, Any]) -> str:
    lines = [
        f"# {source.get('kind', 'evidence bundle')}",
        "",
        f"* Source bundle: `{bundle}`",
        f"* Acceptance commit recorded by the bundle: `{source.get('commit')}`",
        f"* Runs, as the bundle records them: {source.get('runs') or 'not-recorded'}",
        f"* Where the run happened, as the bundle records it: {source.get('ran_from') or 'not-recorded'}",
        "",
        "Rendered from the source bundle's own records; the ordering and the outcome marks below are",
        "the source's, not a re-judgement.",
        "",
    ]
    trace = bundle / "click-trace.jsonl"
    evidence = bundle / "evidence.json"
    if trace.is_file():
        records = read_jsonl(trace)
        lines += [f"## Browser records ({len(records)}), from `click-trace.jsonl`", ""]
        for index, record in enumerate(records, start=1):
            mark = "BLOCKED" if record.get("ok") is None else ("PASS" if record.get("ok") else "FAIL")
            lines.append(f"{index}. {mark} — {record.get('step')}")
        lines.append("")
    other_traces = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("click-trace.jsonl")
                          if p != trace)
    for relative in other_traces:
        records = read_jsonl(bundle / relative)
        lines += [f"## Records in `{relative}` ({len(records)})", ""]
        for index, record in enumerate(records, start=1):
            mark = "BLOCKED" if record.get("ok") is None else ("PASS" if record.get("ok") else "FAIL")
            lines.append(f"{index}. {mark} — {record.get('step')}")
        lines.append("")
    if evidence.is_file():
        payload = read_json(evidence)
        entries = payload.get("entries") or payload.get("core_entries") or []
        lines += [f"## Core checks ({len(entries)}), from `evidence.json`", ""]
        for index, entry in enumerate(entries, start=1):
            mark = "BLOCKED" if entry.get("ok") is None else ("PASS" if entry.get("ok") else "FAIL")
            lines.append(f"{index}. {mark} — {entry.get('check') or entry.get('id')}")
        lines.append("")
    if not trace.is_file() and not evidence.is_file() and not other_traces:
        lines += ["No record list was found in this bundle; the run's actions are not enumerated here.",
                  "See the bundle's own `README.md` and raw logs.", ""]
    return "\n".join(lines)


def derive_requests(bundle: Path, source: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    requests: list[dict] = []
    responses: list[dict] = []
    browser = bundle / "browser-requests.jsonl"
    if browser.is_file():
        for record in read_jsonl(browser):
            requests.append({
                "recorded_in": "browser-requests.jsonl",
                "at": record.get("at"),
                "method": record.get("method"),
                "path": record.get("path"),
                "query": record.get("query"),
                "authorization": record.get("authorization"),
            })
            responses.append({
                "recorded_in": "browser-requests.jsonl",
                "at": record.get("at"),
                "method": record.get("method"),
                "path": record.get("path"),
                "status": record.get("status"),
                "mimeType": record.get("mimeType"),
                "fixtureLabel": record.get("fixtureLabel"),
            })
    evidence = bundle / "evidence.json"
    if evidence.is_file():
        payload = read_json(evidence)
        for call in payload.get("calls", []):
            requests.append({
                "recorded_in": "evidence.json:calls",
                "method": call.get("method"),
                "path": call.get("path"),
                "actor_key": call.get("actor_key"),
            })
            responses.append({
                "recorded_in": "evidence.json:calls",
                "method": call.get("method"),
                "path": call.get("path"),
                "status": call.get("status"),
                "response_body": call.get("response_body"),
            })
    checked = ["browser-requests.jsonl", "evidence.json:calls"]
    if not requests:
        marker = not_recorded("this bundle records no HTTP request or response list", checked)
        requests = [marker]
        responses = [marker]
    return requests, responses


def derive_truth(bundle: Path, source: dict[str, Any]) -> dict[str, Any]:
    checked = [p.relative_to(bundle).as_posix() for p in sorted(bundle.rglob("*"))
               if p.is_file() and ("truth" in p.name or "readback" in p.name or "state" in p.name)]
    recorded = bundle / "truth-before-after.json"
    if recorded.is_file():
        return read_json(recorded)
    return not_recorded(
        "the bundle records no before/after Truth snapshot; this renderer does not reconstruct one "
        "from revisions, replies or fixture state", sorted(checked))


def derive_result(bundle: Path, source: dict[str, Any]) -> str:
    lines = [
        "# Result, as the source bundle records it",
        "",
        "**This file is a format-conformance rendering. It does not re-run, re-judge or upgrade the",
        "source bundle's verdict, and a complete file set is not acceptance.** The binding verdict is",
        "the source bundle's own `BUNDLE-FROZEN.json` and `README.md`.",
        "",
        f"* Source bundle: `{bundle}`",
        f"* Commit: `{source.get('commit')}`",
        f"* Kind: {source.get('kind')}",
        f"* Runs: {source.get('runs') or 'not-recorded'}",
        "",
    ]
    counts = []
    trace = bundle / "click-trace.jsonl"
    if trace.is_file():
        records = read_jsonl(trace)
        passed = sum(1 for r in records if r.get("ok") is True)
        failed = sum(1 for r in records if r.get("ok") is False)
        blocked = sum(1 for r in records if r.get("ok") is None)
        counts.append(f"* `click-trace.jsonl`: {len(records)} records — {passed} PASS, {failed} FAIL, "
                      f"{blocked} BLOCKED")
    evidence = bundle / "evidence.json"
    if evidence.is_file():
        payload = read_json(evidence)
        summary = payload.get("summary")
        if isinstance(summary, dict):
            counts.append(f"* `evidence.json:summary`: {json.dumps(summary, sort_keys=True)}")
    if not counts:
        counts.append("* No record counts were found in this bundle; see its `README.md` and raw logs.")
    lines += counts + [
        "",
        "## What the source bundle does not carry in §8 form",
        "",
        "* `api-requests.jsonl` / `api-responses.jsonl` here are renderings of the source's own",
        "  request and call records; entries missing from those records are absent, not filled in.",
        "* `truth-before-after.json` is `not-recorded` unless the source bundle itself carries one.",
        "",
    ]
    return "\n".join(lines)


def derive_notes(bundle: Path, source: dict[str, Any]) -> str:
    return "\n".join([
        "# Notes on this derived directory",
        "",
        "* It was written by `scripts/p6/bundle_conformance.py`, into a directory of its own. The",
        "  source bundle was **not** written to and is unchanged — re-check it with",
        "  `python3.13 scripts/p6/freeze_bundle.py --verify <bundle>`.",
        "* Provenance: the source bundle path, the commit it records, and the sha256 of every source",
        "  file are in `manifest.json` (`source_files`), together with the sha256 of the source's own",
        f"  `{MANIFEST}`.",
        "* Absent content is marked `not-recorded`; nothing here is synthesised. No request, response",
        "  or Truth comparison was invented to fill a §8 file name.",
        "* Verdict: unchanged, and not restated as a pass. See `result.md`.",
        "",
    ])


def derive(bundle: Path, out: Path, commit: str) -> dict[str, Path]:
    source = read_json(bundle / MANIFEST)
    requests, responses = derive_requests(bundle, source)
    written: dict[str, Path] = {}
    out.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, str] = {
        "manifest.json": json.dumps(derive_manifest(bundle, source, commit),
                                    indent=2, ensure_ascii=False) + "\n",
        "scenario.md": derive_scenario(bundle, source),
        "api-requests.jsonl": "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in requests),
        "api-responses.jsonl": "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in responses),
        "truth-before-after.json": json.dumps(derive_truth(bundle, source),
                                              indent=2, ensure_ascii=False) + "\n",
        "result.md": derive_result(bundle, source),
        "NOTES.md": derive_notes(bundle, source),
    }
    for name in DERIVED_FILES:
        path = out / name
        path.write_text(payloads[name], encoding="utf-8")
        written[name] = path
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, required=True, help="the frozen source bundle")
    parser.add_argument("--out", type=Path, required=True, help="the derived directory to write")
    parser.add_argument("--check", action="store_true",
                        help="re-derive into a temp directory and compare with --out; do not write")
    parser.add_argument("--expect-commit", default=None,
                        help="refuse unless the bundle records this exact commit")
    args = parser.parse_args(argv)

    bundle = args.bundle.resolve()
    out = args.out.resolve()
    if not bundle.is_dir():
        print(f"{bundle} is not a directory", file=sys.stderr)
        return 2
    if not (bundle / MANIFEST).is_file():
        print(f"REFUSING: {bundle} has no {MANIFEST}; it is not a frozen bundle", file=sys.stderr)
        return 2
    # Never write into the source: a derived file inside the bundle would both pollute it and break
    # its own freeze verification (an added file fails `--verify`).
    if out == bundle or bundle in out.parents:
        print(f"REFUSING: --out {out} is the bundle or inside it ({bundle}); derive elsewhere",
              file=sys.stderr)
        return 2

    try:
        source = read_json(bundle / MANIFEST)
    except Exception as error:  # noqa: BLE001 - a broken manifest is the observation
        print(f"REFUSING: {bundle / MANIFEST} is not readable JSON ({error})", file=sys.stderr)
        return 2
    commit = str(source.get("commit"))
    if args.expect_commit and commit != args.expect_commit:
        print(f"REFUSING: bundle records commit {commit}, expected {args.expect_commit}",
              file=sys.stderr)
        return 2

    # The source must be exactly what its own manifest says. `verify` covers changed, missing and
    # added files, so a tampered bundle cannot be rendered into a clean-looking derived directory.
    if verify_bundle(bundle) != 0:
        print(f"REFUSING: {bundle} failed its own {MANIFEST} verification; not deriving from it",
              file=sys.stderr)
        return 1

    if args.check:
        if not out.is_dir():
            print(f"REFUSING: --check needs an existing derived directory at {out}", file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory(prefix="p6-conformance-check-") as tmp:
            derive(bundle, Path(tmp), commit)
            differing = [name for name in DERIVED_FILES
                         if not (out / name).is_file()
                         or (out / name).read_bytes() != (Path(tmp) / name).read_bytes()]
            extra = sorted(p.name for p in out.iterdir()
                           if p.is_file() and p.name not in DERIVED_FILES)
        if differing or extra:
            print(f"DIFFERS: {', '.join(differing) or 'none'}"
                  + (f"; unexpected files: {', '.join(extra)}" if extra else ""), file=sys.stderr)
            return 1
        print(f"identical: {len(DERIVED_FILES)} derived files match the source bundle {bundle.name}")
        return 0

    written = derive(bundle, out, commit)
    for name in DERIVED_FILES:
        print(f"wrote {written[name]}")
    print(f"derived {len(DERIVED_FILES)} files from {bundle} (commit {commit}); the bundle is "
          f"untouched — verify it with freeze_bundle.py --verify")
    # Leave no half-written directory behind if something above raised: derive writes atomically
    # enough for this use, but a partially written out dir must not look complete.
    missing = [name for name in DERIVED_FILES if not (out / name).is_file()]
    if missing:
        print(f"INCOMPLETE: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
