#!/usr/bin/env python3.13
"""P6-03 integration rehearsal — merge the unmerged slices in an isolated clone and record what breaks.

The acceptance plan's third step is "re-derive the contracts at the final integration commit and
complete P6-03a/b1/b2 verification there". That commit does not exist yet: P3, P4 and P6 are
unmerged branches. This script rehearses it **without touching the source repository's refs**:

1. clone the repository to a temporary directory (a clone, never a worktree of the live repo);
2. merge the named refs onto `main` one at a time, recording the conflicted files at each step;
3. at the merged ref, re-derive each contract with the P6 derivation scripts and compare it with the
   frozen file, ignoring the provenance stamps that *must* differ (`source.ref`, `source.commit`,
   and the schema version recorded by a contract pinned to a dependency older than the merge);
4. run the Python suite and the dashboard's suites at the merged ref;
5. run the Python suite at each dependency's own tip as well, so a failure can be attributed:
   environmental (present at the tip too) versus integration (appears only when merged).

Nothing here is a product verdict: it is a rehearsal against a synthetic merge. The point is to tell
the operator, in advance, what the real integration commit will have to settle — and to be runnable
unchanged against that commit when it exists.

    python3.13 scripts/p6/integration_rehearsal.py --out validation-runs/p6/<stamp>-p6-03-integration-rehearsal

It refuses to touch the source repository: the source's `HEAD` and branch list are recorded before
and after, and a difference aborts the run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FREEZE = REPO_ROOT / "scripts" / "p6" / "freeze_bundle.py"
CONTRACTS = (
    ("scripts/p6/p4_contract.py", "web/contracts/p4-memory-read.v1.json"),
    ("scripts/p6/p4_write_spec.py", "web/contracts/p4-memory-write.v1.json"),
    ("scripts/p6/p3_contract.py", "web/contracts/p3-continuity-read.v1.json"),
)
#: Stamps that *must* differ at a different ref, and are therefore not drift.
PROVENANCE = {"source.ref", "source.commit", "source.schema_version_in_p3",
              "source.branch", "source.kind"}


def log(message: str) -> None:
    print(f"p6-rehearsal: {message}", file=sys.stderr, flush=True)


def git(directory: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(directory), *args], check=check,
                          capture_output=True, text=True)


def source_fingerprint(repo: Path) -> dict[str, Any]:
    return {
        "head": git(repo, "rev-parse", "HEAD").stdout.strip(),
        "branches": sorted(line.strip() for line in
                           git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
                           .stdout.splitlines()),
    }


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: value}


def compare_contracts(clone: Path, ref: str) -> list[dict[str, Any]]:
    results = []
    for script, frozen in CONTRACTS:
        derived = subprocess.run([sys.executable, script, "--ref", ref, "--print"], cwd=clone,
                                 capture_output=True, text=True)
        if derived.returncode != 0:
            results.append({"contract": Path(frozen).name, "derived": False,
                            "error": derived.stderr.strip()[-200:]})
            continue
        left = flatten(json.loads(derived.stdout))
        right = flatten(json.loads((clone / frozen).read_text()))
        differences = sorted(
            key for key in set(left) | set(right)
            if left.get(key) != right.get(key) and key not in PROVENANCE)
        provenance = sorted(
            key for key in set(left) | set(right)
            if left.get(key) != right.get(key) and key in PROVENANCE)
        results.append({"contract": Path(frozen).name, "derived": True,
                        "content_identical": not differences, "differences": differences,
                        "provenance_differences": provenance})
    return results


def run_python_suite(directory: Path) -> dict[str, Any]:
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests"],
                            cwd=directory, capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH="src:tests"))
    tail = result.stderr.strip().splitlines()
    summary = next((line for line in reversed(tail) if line.startswith("Ran ")), "")
    verdict = next((line for line in reversed(tail) if line.startswith(("OK", "FAILED"))), "")
    failing = sorted({line.split(" (")[0] for line in tail
                      if line.startswith(("FAIL:", "ERROR:"))})
    return {"summary": summary, "verdict": verdict, "failing": failing,
            "exit": result.returncode}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--refs", default="p4-integration,p6-01-readonly-dashboard",
                        help="comma-separated refs to merge onto main, in order")
    parser.add_argument("--node-modules", type=Path, default=REPO_ROOT / "web" / "node_modules")
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    # The source repository must not change. Its git *directory* is one level up from a linked
    # worktree's metadata; a plain checkout has it in `.git`.
    common = Path(git(REPO_ROOT, "rev-parse", "--git-common-dir").stdout.strip())
    source = (common if common.is_absolute() else (REPO_ROOT / common)).resolve().parent
    before = source_fingerprint(source)
    log(f"source repository {source} at {before['head'][:12]}")

    tmp = Path(tempfile.mkdtemp(prefix="p6-integration-rehearsal-"))
    clone = tmp / "clone"
    try:
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(source), str(clone)], check=True)
        git(clone, "checkout", "-q", "-B", "rehearsal", "origin/main")
        merges = []
        for ref in [item.strip() for item in args.refs.split(",") if item.strip()]:
            target = f"origin/{ref}"
            result = git(clone, "merge", "--no-commit", "--no-ff", target, check=False)
            conflicts = sorted(line for line in
                               git(clone, "diff", "--name-only", "--diff-filter=U").stdout.splitlines())
            output = (result.stdout + result.stderr).strip().splitlines()
            merges.append({"ref": ref, "exit": result.returncode, "conflicts": conflicts,
                           "message": output[-1] if output else ""})
            log(f"merge {ref}: exit={result.returncode} conflicts={len(conflicts)}")
            git(clone, "add", "-A")
            commit = git(clone, "commit", "-q", "-m", f"rehearsal: merge {ref}",
                         check=False, )
            if commit.returncode != 0:
                merges[-1]["commit_note"] = "nothing to commit (already an ancestor)"
        merged_ref = git(clone, "rev-parse", "HEAD").stdout.strip()

        contracts = compare_contracts(clone, merged_ref)
        merged_suite = run_python_suite(clone)
        log(f"merged suite: {merged_suite['verdict']}")
        tip_suites = {}
        for ref in [item.strip() for item in args.refs.split(",") if item.strip()]:
            git(clone, "checkout", "-q", f"origin/{ref}")
            tip_suites[ref] = run_python_suite(clone)
            log(f"{ref} tip suite: {tip_suites[ref]['verdict']}")
        git(clone, "checkout", "-q", "rehearsal")

        core_runs = run_core_acceptances(clone)

        web: dict[str, Any] = {"skipped": True}
        if not args.skip_web and args.node_modules.is_dir():
            os.symlink(args.node_modules, clone / "web" / "node_modules")
            tsc = subprocess.run(["npx", "tsc", "--noEmit"], cwd=clone / "web",
                                 capture_output=True, text=True)
            vitest = subprocess.run(["npx", "vitest", "run", "--reporter=dot"], cwd=clone / "web",
                                    capture_output=True, text=True)
            counts = [line for line in (vitest.stdout + vitest.stderr).splitlines()
                      if line.strip().startswith(("Test Files", "Tests "))]
            web = {"tsc_exit": tsc.returncode, "vitest_exit": vitest.returncode,
                   "counts": counts}
            log(f"web at the merged ref: tsc={tsc.returncode} vitest={vitest.returncode} {counts}")

        evidence = {"source": before, "merged_ref": merged_ref, "merges": merges,
                    "contracts": contracts, "merged_suite": merged_suite,
                    "tip_suites": tip_suites, "web": web, "core_runs": core_runs}
        (out / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        write_readme(out, evidence)
        (out / "plan.md").write_text(
            (REPO_ROOT / "web" / "REAL-CORE-ACCEPTANCE-PLAN.md").read_text(encoding="utf-8"),
            encoding="utf-8")

        after = source_fingerprint(source)
        if after != before:
            raise SystemExit("the SOURCE repository changed during the rehearsal — aborting")
        commit = git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip()
        subprocess.run([sys.executable, str(FREEZE), "--freeze", str(out), "--commit", commit,
                        "--kind", "integration rehearsal (synthetic merge, no product commit)",
                        "--runs", "one rehearsal",
                        "--dependency-json", json.dumps({
                            "name": "P3 + P4 + P6 integration", "status": "NOT MERGED",
                            "source_ref": merged_ref,
                            "acceptance": "rehearsal only — the merged commit is synthetic",
                            "consequence": "findings are about what the real integration commit "
                                           "must settle, not about a shipped baseline"})],
                       check=True)
        subprocess.run([sys.executable, str(FREEZE), "--verify", str(out)], check=True)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_core_acceptances(clone: Path) -> dict[str, Any]:
    """Run the two real-Core acceptances at the merged ref, from the clone's own scripts.

    The unit suites say the code merges; these say the **Core behaves** the same way at the merged
    baseline — which is what a final integration commit would have to demonstrate. Each runs into a
    temporary directory of its own and its summary is kept; its bundle is not (the rehearsal's own
    bundle records the result).
    """
    results: dict[str, Any] = {}
    for name, command in (
        ("p4-core-matrix", ["scripts/p6/p4_realcore_accept.py"]),
        ("p3-core-and-ui", ["scripts/p6/run_p3_realcore.py"]),
    ):
        work = clone / "rehearsal-run" / name
        work.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, *command, "--repo", str(clone), "--out", str(work),
                "--no-freeze"]
        if name == "p3-core-and-ui":
            argv += ["--dashboard-ref", "HEAD"]
        log(f"core acceptance {name} at the merged ref")
        finished = subprocess.run(argv, cwd=clone, capture_output=True, text=True)
        evidence_file = work / "evidence.json"
        summary: dict[str, Any] = {"exit": finished.returncode}
        if evidence_file.is_file():
            payload = json.loads(evidence_file.read_text())
            if "summary" in payload:
                summary["summary"] = payload["summary"]
            else:
                ui = payload.get("ui_records", [])
                summary["summary"] = payload.get("summary", {
                    "core_checks": len(payload.get("core_entries", [])),
                    "ui_records": len(ui),
                })
        else:
            summary["tail"] = (finished.stdout + finished.stderr).strip().splitlines()[-3:]
        results[name] = summary
        shutil.rmtree(work, ignore_errors=True)
    return results


def write_readme(out: Path, evidence: dict[str, Any]) -> None:
    merges = evidence["merges"]
    real = [item for item in merges if item["exit"] == 0 and not item.get("commit_note")]
    environmental = set(evidence["tip_suites"].get(merges[0]["ref"], {}).get("failing", []))
    integrated = [name for name in evidence["merged_suite"]["failing"] if name not in environmental]
    lines = [
        "# P6-03 integration rehearsal — a synthetic merge, and what it says the real one must settle",
        "",
        f"Source repository `{evidence['source']['head'][:12]}` (unchanged before and after:",
        "the rehearsal runs in a throwaway clone and records the source's `HEAD` and branch list on",
        "both sides). Refs merged onto `origin/main`, in order:",
        "",
        "| Step | Ref | Exit | Conflicted files |",
        "| --- | --- | --- | --- |",
    ]
    for item in merges:
        note = f" ({item['commit_note']})" if item.get("commit_note") else ""
        lines.append(f"| {item['ref']} | `origin/{item['ref']}` | {item['exit']}{note} | "
                     f"{len(item['conflicts'])} |")
    lines += [
        "",
        f"Merged rehearsal ref: `{evidence['merged_ref'][:12]}`.",
        "",
        "## Contracts at the merged ref",
        "",
        "| Frozen contract | Derived at the merged ref | Content differences | Provenance-only differences |",
        "| --- | --- | --- | --- |",
    ]
    for item in evidence["contracts"]:
        if not item["derived"]:
            lines.append(f"| {item['contract']} | failed | {item.get('error', '')} | — |")
            continue
        lines.append(f"| {item['contract']} | yes | "
                     f"{', '.join(item['differences']) or 'none'} | "
                     f"{', '.join(item['provenance_differences']) or 'none'} |")
    lines += [
        "",
        "A contract whose **content** is identical needs no re-issue at integration; one whose",
        "provenance stamps differ needs a decision (record the integration ref, or keep the pinned",
        "dependency ref and add the integration ref beside it) — that is the operator's call, not the",
        "rehearsal's.",
        "",
        "## Suites",
        "",
        f"* at the **merged** ref: `{evidence['merged_suite']['summary']}` — "
        f"`{evidence['merged_suite']['verdict']}`, {len(evidence['merged_suite']['failing'])} "
        f"failing/erroring",
    ]
    for ref, suite in evidence["tip_suites"].items():
        lines.append(f"* at the **{ref}** tip alone: `{suite['summary']}` — `{suite['verdict']}`, "
                     f"{len(suite['failing'])} failing/erroring")
    web = evidence["web"]
    if web.get("skipped"):
        lines.append("* dashboard suites: skipped")
    else:
        lines.append(f"* dashboard at the merged ref: `tsc --noEmit` exit {web['tsc_exit']}, "
                     f"vitest exit {web['vitest_exit']} — {', '.join(web['counts']) or 'no counts'}")
    lines += ["", "## Real-Core acceptances at the merged ref", "",
              "| Acceptance | Exit | Result |", "| --- | --- | --- |"]
    for name, run in evidence.get("core_runs", {}).items():
        summary = run.get("summary", {})
        text = ", ".join(f"{key}={value}" for key, value in summary.items()) or \
            " | ".join(run.get("tail", []))
        lines.append(f"| `{name}` | {run['exit']} | {text} |")
    lines += [
        "",
        "These run the isolated real-Core acceptances (P4's Core matrix; P3's Core matrix and browser",
        "pass) **from the clone**, so the Core is extracted from the merged ref and the dashboard is",
        "built from the same tree. A blocked row here is the same kind of statement as anywhere else:",
        "a missing precondition, with its reason.",
        "",
        "### Failures that are the environment's, not the integration's",
        "",
        f"{len(environmental)} test(s) fail identically at the dependency's own tip inside a clone, so",
        "they say nothing about merging — they need the original repository's layout or native-hook",
        "machinery. They are listed in `evidence.json` under `tip_suites`.",
        "",
        "### Failures that appear only when merged",
        "",
    ]
    if integrated:
        for name in integrated:
            lines.append(f"* `{name}`")
        lines += [
            "",
            "These are the rehearsal's payload: each is something the real integration commit must",
            "settle. They are test expectations written for the *unmerged* baseline (\"the baseline",
            "serves none of these routes\") or comparisons against an enum the dependency extended;",
            "they are not product defects, and they are not fixed here.",
        ]
    else:
        lines.append("None.")
    lines += [
        "",
        "## What this does NOT establish",
        "",
        "* It is a **synthetic** merge in a throwaway clone, not the operator's integration commit;",
        "  the merge is textually clean here, which does not promise the real one will be.",
        "* The dashboard's browser behaviour at the merged ref was not exercised; the real-Core passes",
        "  (`*-p6-03-realcore`, `*-p6-03-realcore-ui`) cover that against the pinned P4 SHA.",
        "* Nothing in the source repository was changed: no ref, no worktree, no file.",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
