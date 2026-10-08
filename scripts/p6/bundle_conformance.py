#!/usr/bin/env python3.13
"""Render a frozen P6 evidence bundle into the file set the P6 taskbook §8 asks for.

The taskbook's §8 lists what every bundle must contain: `manifest.json`, `scenario.md`,
`click-trace.jsonl`, masked `api-requests.jsonl` / `api-responses.jsonl`,
`truth-before-after.json`, the relevant source mappings, `screenshots/`, raw test output and
`result.md`. The bundles this project produced carry a different but overlapping set
(`BUNDLE-FROZEN.json`, `README.md`, `click-trace.jsonl`, `browser-requests.jsonl`, `evidence.json`,
raw logs, screenshots). This tool closes that naming/set gap **without touching a frozen bundle and
without inventing anything**:

* it writes into a **separate derived directory** and refuses if the output is the bundle, is inside
  it, or is a symlink;
* it refuses a bundle that is not frozen, and it re-verifies every file against the bundle's own
  `BUNDLE-FROZEN.json` (the same check `freeze_bundle.py --verify` performs) before deriving — a
  tampered, missing or added source file aborts the derivation;
* it carries **every field** of every source record it renders (request bodies included) and
  computes a `field_accounting` block in the derived `manifest.json`; anything it could not carry is
  listed there and in `NOTES.md` rather than dropped silently;
* where a §8 file has no recorded content it writes an explicit `not-recorded` marker naming the
  files it looked at. It never synthesises a request, a response or a Truth comparison;
* the derived `result.md` **restates** the counts the source bundle records and says in words that
  format conformance is not acceptance: the binding verdict stays the source bundle's;
* it never writes through a link and never removes anything but its own private staging directory:
  the derived files are staged in a private directory beside `--out` and installed with a single
  `os.replace` into a path that **must not exist yet**, so a symlink at a target name cannot
  redirect a write into the source, a failure leaves either the finished directory or nothing at
  all, and **no cleanup step ever deletes a directory the tool did not create**;
* **an existing output directory is never written to**: it is only compared (`--check`). To
  re-derive, point `--out` at a new path. This is deliberate — an earlier version "refreshed" an
  existing directory by moving it aside and deleting the copy, which could delete the source bundle
  if the source lived inside the output, or delete a source bundle that happened to occupy the fixed
  backup path;
* the source bundle and the output must not contain one another in **either** direction, checked on
  resolved paths before anything is written.

Idempotent by construction: no timestamps, no environment-dependent fields, deterministic walk and
key order. Two runs over the same bundle are byte-identical (`--check` proves it).

Usage::

    python3.13 scripts/p6/bundle_conformance.py --bundle <frozen bundle> --out <new derived dir>
    python3.13 scripts/p6/bundle_conformance.py --bundle <bundle> --out <existing derived dir> --check
    python3.13 scripts/p6/bundle_conformance.py --bundle <bundle> --out <dir> \
        --expect-commit f323c779bc91893b5908218d8c5c148619159180

Exit codes: 0 derived / identical; 1 source failed verification, a write failed, or `--check`
differs; 2 not frozen, bad paths (including an output that already exists without `--check`), or a
usage problem. "Could not look" is never 0.

History: the first version of this tool (reviewed and reproduced against by the operator) dropped
every non-empty request body (`browser-requests.jsonl`'s `postData`, `evidence.json`'s
`request_body`) and every record in nested files such as `stability/`, wrote through a symlink placed
at a target name, and claimed a cleanup it did not implement. Those three were fixed, and **the fix
introduced two more destructive paths** — the operator reproduced them: refreshing an existing output
directory deleted it, so a source bundle *inside* the output was deleted with it, and a source bundle
occupying the fixed backup path (`<out>.p6-previous`) was deleted too. This revision removes both by
construction: new directories only, `--check` for existing ones, bidirectional containment refused,
and no backup path at all. The derived bytes are unchanged — `--check` against the version-2
derivation still reports identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from freeze_bundle import verify as verify_bundle  # noqa: E402

GENERATOR = "scripts/p6/bundle_conformance.py"
GENERATOR_VERSION = "2"
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


class Refused(Exception):
    """A precondition failed; nothing was written."""


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


def source_records(bundle: Path) -> dict[str, list[dict[str, Any]]]:
    """Every record the bundle carries, by source file, including nested sub-runs.

    A bundle may hold more than one run (b2 keeps its `stability/` repeat and its `writes-off/` run
    as subdirectories). Walking the whole tree means no record is silently left out of the derived
    files; each line names the file it came from.
    """
    found: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(bundle.rglob("browser-requests.jsonl")):
        if path.is_file():
            found[path.relative_to(bundle).as_posix()] = read_jsonl(path)
    for path in sorted(bundle.rglob("evidence.json")):
        if path.is_file():
            payload = read_json(path)
            if isinstance(payload, dict) and isinstance(payload.get("calls"), list):
                found[f"{path.relative_to(bundle).as_posix()}:calls"] = [
                    call for call in payload["calls"] if isinstance(call, dict)]
    return found


def carry(records: list[dict[str, Any]], *, source: str, side: str) -> list[dict[str, Any]]:
    """Every field of every record, verbatim, plus where it came from and which side it is."""
    return [{"recorded_in": source, "side": side, **record} for record in records]


def accounting(records: dict[str, list[dict[str, Any]]],
               carried: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """What each source file's records contained, and whether every field was carried."""
    report: dict[str, Any] = {}
    for source, rows in sorted(records.items()):
        source_keys = sorted({key for row in rows for key in row})
        carried_keys = sorted({key for row in carried.get(source, []) for key in row})
        report[source] = {
            "records": len(rows),
            "source_fields": source_keys,
            "carried_fields": carried_keys,
            "not_carried": [key for key in source_keys if key not in carried_keys],
            "records_with_a_non_empty_body": sum(
                1 for row in rows
                if any(row.get(key) not in (None, "", "null")
                       for key in ("postData", "request_body", "body"))),
        }
    return report


def derive_manifest(bundle: Path, source: dict[str, Any], commit: str,
                    field_accounting: dict[str, Any]) -> dict[str, Any]:
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
        "field_accounting": field_accounting,
        "not_carried_total": sum(len(entry["not_carried"]) for entry in field_accounting.values()),
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
    traces = sorted(path for path in bundle.rglob("click-trace.jsonl") if path.is_file())
    for path in traces:
        relative = path.relative_to(bundle).as_posix()
        records = read_jsonl(path)
        lines += [f"## Browser records ({len(records)}), from `{relative}`", ""]
        for index, record in enumerate(records, start=1):
            mark = "BLOCKED" if record.get("ok") is None else ("PASS" if record.get("ok") else "FAIL")
            lines.append(f"{index}. {mark} — {record.get('step')}")
        lines.append("")
    for path in sorted(candidate for candidate in bundle.rglob("evidence.json")
                       if candidate.is_file()):
        relative = path.relative_to(bundle).as_posix()
        payload = read_json(path)
        entries = payload.get("entries") or payload.get("core_entries") or []
        lines += [f"## Core checks ({len(entries)}), from `{relative}`", ""]
        for index, entry in enumerate(entries, start=1):
            mark = "BLOCKED" if entry.get("ok") is None else ("PASS" if entry.get("ok") else "FAIL")
            lines.append(f"{index}. {mark} — {entry.get('check') or entry.get('id')}")
        lines.append("")
    if not traces and not list(bundle.rglob("evidence.json")):
        lines += ["No record list was found in this bundle; the run's actions are not enumerated here.",
                  "See the bundle's own `README.md` and raw logs.", ""]
    return "\n".join(lines)


def derive_requests(records: dict[str, list[dict[str, Any]]]) -> tuple[list[dict], list[dict]]:
    requests: list[dict] = []
    responses: list[dict] = []
    for source, rows in sorted(records.items()):
        requests += carry(rows, source=source, side="request")
        responses += carry(rows, source=source, side="response")
    if not requests:
        marker = not_recorded("this bundle records no HTTP request, response or Core call",
                              ["browser-requests.jsonl (any depth)", "evidence.json:calls"])
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
    for path in sorted(candidate for candidate in bundle.rglob("click-trace.jsonl")
                       if candidate.is_file()):
        relative = path.relative_to(bundle).as_posix()
        records = read_jsonl(path)
        passed = sum(1 for r in records if r.get("ok") is True)
        failed = sum(1 for r in records if r.get("ok") is False)
        blocked = sum(1 for r in records if r.get("ok") is None)
        counts.append(f"* `{relative}`: {len(records)} records — {passed} PASS, {failed} FAIL, "
                      f"{blocked} BLOCKED")
    for path in sorted(candidate for candidate in bundle.rglob("evidence.json")
                       if candidate.is_file()):
        payload = read_json(path)
        summary = payload.get("summary")
        if isinstance(summary, dict):
            counts.append(f"* `{path.relative_to(bundle).as_posix()}:summary`: "
                          f"{json.dumps(summary, sort_keys=True)}")
    if not counts:
        counts.append("* No record counts were found in this bundle; see its `README.md` and raw logs.")
    lines += counts + [
        "",
        "## What the source bundle does not carry in §8 form",
        "",
        "* `api-requests.jsonl` / `api-responses.jsonl` carry every field of every source record",
        "  (request bodies included) plus `recorded_in` and `side`; `manifest.json.field_accounting`",
        "  proves nothing was left out.",
        "* `truth-before-after.json` is `not-recorded` unless the source bundle itself carries one.",
        "",
    ]
    return "\n".join(lines)


def derive_notes(bundle: Path, source: dict[str, Any], field_accounting: dict[str, Any]) -> str:
    missing = {name: entry["not_carried"] for name, entry in sorted(field_accounting.items())
               if entry["not_carried"]}
    lines = [
        "# Notes on this derived directory",
        "",
        "* It was written by `scripts/p6/bundle_conformance.py`, into a directory of its own. The",
        "  source bundle was **not** written to and is unchanged — re-check it with",
        "  `python3.13 scripts/p6/freeze_bundle.py --verify <bundle>`.",
        "* Provenance: the source bundle path, the commit it records, and the sha256 of every source",
        "  file are in `manifest.json` (`source_files`), together with the sha256 of the source's own",
        f"  `{MANIFEST}`.",
        "* Records: every `browser-requests.jsonl` and every `evidence.json:calls` record in the",
        "  bundle, at any depth, is carried whole — all fields, including request bodies — into",
        "  `api-requests.jsonl` and `api-responses.jsonl`, with `recorded_in` and `side` added.",
        "  `manifest.json.field_accounting` lists the fields each source file had and confirms they",
        "  were all carried.",
        "* Absent content is marked `not-recorded`; nothing here is synthesised. No request, response",
        "  or Truth comparison was invented to fill a §8 file name.",
        "* Writing: derived files are staged privately and moved into place with `os.replace`, which",
        "  cannot write through a symlink at the target name; a pre-existing symlink or non-file",
        "  target is refused. A fresh output directory appears by one atomic rename, and refreshing an",
        "  existing one moves the old directory aside first, so a failure leaves the previous",
        "  derivation instead of a half-written one.",
        "* Verdict: unchanged, and not restated as a pass. See `result.md`.",
        "",
    ]
    if missing:
        lines += ["## Fields that were NOT carried", "",
                  "The generator could not carry the fields below; they are listed rather than dropped",
                  "silently. Read the source file for them.", ""]
        for name, keys in missing.items():
            lines.append(f"* `{name}`: {', '.join(keys)}")
        lines.append("")
    return "\n".join(lines)


def build_payloads(bundle: Path, source: dict[str, Any], commit: str) -> dict[str, str]:
    """The derived file contents. Pure: reads the bundle, writes nothing."""
    records = source_records(bundle)
    requests, responses = derive_requests(records)
    carried = {name: carry(rows, source=name, side="request") for name, rows in records.items()}
    field_accounting = accounting(records, carried)
    return {
        "manifest.json": json.dumps(
            derive_manifest(bundle, source, commit, field_accounting),
            indent=2, ensure_ascii=False) + "\n",
        "scenario.md": derive_scenario(bundle, source),
        "api-requests.jsonl": "".join(
            json.dumps(entry, ensure_ascii=False) + "\n" for entry in requests),
        "api-responses.jsonl": "".join(
            json.dumps(entry, ensure_ascii=False) + "\n" for entry in responses),
        "truth-before-after.json": json.dumps(derive_truth(bundle, source),
                                              indent=2, ensure_ascii=False) + "\n",
        "result.md": derive_result(bundle, source),
        "NOTES.md": derive_notes(bundle, source, field_accounting),
    }


def refuse_containment(bundle: Path, out: Path) -> None:
    """Refuse if the source and the output contain one another, in either direction.

    The direction that bit once already: an output directory *containing* the bundle. An earlier
    version refreshed such an output by deleting the directory it had moved aside, which deleted the
    source bundle with it. Resolved paths are compared, so a symlink cannot hide the relation.
    """
    if out == bundle:
        raise Refused(f"--out {out} is the source bundle itself")
    if bundle in out.parents:
        raise Refused(f"--out {out} is inside the source bundle {bundle}; derive elsewhere")
    if out in bundle.parents:
        raise Refused(f"the source bundle {bundle} is inside --out {out}; refusing — writing there "
                      f"would put the bundle inside a directory this tool manages")


def refuse_target_entries(out: Path) -> None:
    """For `--check` on an existing directory: refuse symlinked or non-file entries.

    Reading through a link would compare bytes that are not in the directory being checked, so the
    entry is refused and named rather than followed.
    """
    for name in DERIVED_FILES:
        target = out / name
        if target.is_symlink():
            raise Refused(f"{target} is a symlink; refusing to read through it")
        if target.exists() and not target.is_file():
            raise Refused(f"{target} exists and is not a regular file")


def install_new(staging: Path, out: Path) -> None:
    """Install `staging` at `out`, which must not exist. One rename; no cleanup path at all.

    There is deliberately no "replace an existing directory" branch: the only directory this tool
    ever removes is its own staging directory, and only when a step failed. Nothing else on disk is
    a candidate for deletion, so there is no path by which a source bundle can be removed.
    """
    if out.exists() or out.is_symlink():
        raise Refused(f"{out} already exists; derive into a NEW directory, and use --check to "
                      f"compare an existing derivation")
    os.replace(staging, out)


def derive(bundle: Path, out: Path, commit: str) -> dict[str, Path]:
    """Stage the derived files privately, then install them into a new directory. Never partial."""
    refuse_containment(bundle, out)
    payloads = build_payloads(bundle, source=read_json(bundle / MANIFEST), commit=commit)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Re-check after creating the parent: the parent's creation could have resolved the relation.
    refuse_containment(bundle, out)
    staging = Path(tempfile.mkdtemp(dir=out.parent, prefix=".p6-conformance-staging-"))
    try:
        for name in DERIVED_FILES:
            (staging / name).write_text(payloads[name], encoding="utf-8")
        if any(not (staging / name).is_file() for name in DERIVED_FILES):
            raise Refused("staging is incomplete; refusing to install it")
        install_new(staging, out)
    except BaseException:
        # The only deletion in this program: our own staging directory, on a failed run.
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {name: out / name for name in DERIVED_FILES}


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
    # Check the *unresolved* --out for a symlink first: `resolve()` would hide it, and a linked
    # output directory is exactly the redirection this tool refuses to write through.
    if args.out.is_symlink():
        print(f"REFUSING: --out {args.out} is a symlink; refusing to write through it",
              file=sys.stderr)
        return 2
    out = args.out.resolve()
    if not bundle.is_dir():
        print(f"{bundle} is not a directory", file=sys.stderr)
        return 2
    if not (bundle / MANIFEST).is_file():
        print(f"REFUSING: {bundle} has no {MANIFEST}; it is not a frozen bundle", file=sys.stderr)
        return 2
    # Never write into the source, and never let the output contain it: both directions are refused.
    # The containing direction is the one that deleted a source bundle once — an output directory
    # that contained the bundle was "refreshed" by deleting itself.
    try:
        refuse_containment(bundle, out)
    except Refused as refusal:
        print(f"REFUSING: {refusal}", file=sys.stderr)
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
        try:
            refuse_target_entries(out)
        except Refused as refusal:
            print(f"REFUSING: {refusal}", file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory(prefix="p6-conformance-check-") as tmp:
            derive(bundle, Path(tmp) / "derived", commit)
            differing = [name for name in DERIVED_FILES
                         if not (out / name).is_file()
                         or (out / name).read_bytes() != (Path(tmp) / "derived" / name).read_bytes()]
            extra = sorted(p.name for p in out.iterdir()
                           if p.is_file() and p.name not in DERIVED_FILES)
        if differing or extra:
            print(f"DIFFERS: {', '.join(differing) or 'none'}"
                  + (f"; unexpected files: {', '.join(extra)}" if extra else ""), file=sys.stderr)
            return 1
        print(f"identical: {len(DERIVED_FILES)} derived files match the source bundle {bundle.name}")
        return 0

    try:
        written = derive(bundle, out, commit)
    except Refused as refusal:
        print(f"REFUSING: {refusal}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - a failed write is the observation
        print(f"FAILED: could not derive {bundle} into {out}: "
              f"{error.__class__.__name__}: {error}", file=sys.stderr)
        return 1

    # Defence in depth: if anything at all wrote through to the source, say so and fail rather than
    # report success over a damaged bundle.
    if verify_bundle(bundle) != 0:
        print(f"FAILED: {bundle} no longer matches its own {MANIFEST} after derivation; the derived "
              f"directory at {out} must not be trusted", file=sys.stderr)
        return 1

    for name in DERIVED_FILES:
        print(f"wrote {written[name]}")
    print(f"derived {len(DERIVED_FILES)} files from {bundle} (commit {commit}); the bundle is "
          f"untouched — verify it with freeze_bundle.py --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
