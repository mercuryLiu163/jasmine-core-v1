#!/usr/bin/env python3
"""Assemble one G01 v2 native session receipt from the raw `codex exec --json` stream.

The v2 contract (docs/p4-g01-v2-spec.md) requires every tool action to be
Evidence-bound, file changes included, and the metric to be computed from the
native stream rather than from declarations. This assembler:

  * extracts the ordered actions with `jasmine_core.memory.g01_stream`,
  * binds shell and MCP actions through the ordinary Evidence store,
  * binds each file-change action to a real before/after transition computed
    from the fixture ref and the session worktree -- and refuses to bind one it
    cannot attribute (several actions touching one path, or a workspace change
    no action claims); the Gate turns that into BLOCKED, never into a count,
  * copies the raw transcript beside the session so the Gate can seal it and
    re-extract it for a completeness check,
  * captures the end-of-session snapshot the byte-exact diff check needs.

Exit codes: 0 written, 1 the inputs do not stand up, 2 the assembler cannot be
used (missing pieces, unreadable work directory).
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from jasmine_core.memory.g01_stream import action_payload, extract_actions  # noqa: E402

TRACKED = ("calc.py", "test_calc.py", "NOTES.md", "requirements.txt")


def git(repo: pathlib.Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, check=True).stdout


def ref_bytes(repo: pathlib.Path, ref: str, path: str) -> bytes | None:
    completed = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"],
                               capture_output=True)
    return completed.stdout if completed.returncode == 0 else None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def final_json(message: str) -> dict:
    decoder = json.JSONDecoder()
    found = None
    for index, char in enumerate(message):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(message, index)
        except ValueError:
            continue
        if isinstance(value, dict) and "final_answer" in value:
            found = value
    if found is None:
        raise SystemExit("the final message carries no JSON block")
    return found


def thread_id(text: str) -> str:
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return event["thread_id"]
    raise SystemExit("the transcript records no thread.started id")


def changes_for(repo: pathlib.Path, ref: str, path: str) -> tuple[str | None, str | None, str | None]:
    """(before_sha256, after_sha256, unified diff) for one path."""
    before = ref_bytes(repo, ref, path)
    target = repo / path
    after = target.read_bytes() if target.is_file() else None
    if before is None and after is None:
        return None, None, None
    diff = None
    if before is not None and after is not None and before != after:
        diff = "".join(difflib.unified_diff(
            before.decode("utf-8", "replace").splitlines(True),
            after.decode("utf-8", "replace").splitlines(True),
            fromfile=path, tofile=path))
    return (sha256(before) if before is not None else None,
            sha256(after) if after is not None else None,
            diff)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", required=True, type=pathlib.Path)
    parser.add_argument("--last", required=True, type=pathlib.Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--pack-key", required=True)
    parser.add_argument("--work", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--repo", required=True, type=pathlib.Path)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--review", type=pathlib.Path,
                        help="semantic_review JSON produced by an independent reviewer")
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location(
        "p4_memory_gate", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "p4-memory-gate.py")
    gm = importlib.util.module_from_spec(spec)
    sys.modules["p4_memory_gate"] = gm
    spec.loader.exec_module(gm)

    transcript_text = args.transcript.read_text(encoding="utf-8")
    pack = None
    for path in sorted((args.work / "P4-G01").glob("pack-*.json")):
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if candidate.get("idempotency_key") == args.pack_key:
            pack = candidate
    if pack is None:
        print(f"no pack {args.pack_key!r} under {args.work}", file=sys.stderr)
        return 2
    config = json.loads(args.config.read_text(encoding="utf-8"))

    thread = thread_id(transcript_text)
    receipt_path = args.repo / ".codex" / "jasmine" / f"session-start-{thread}.json"
    if not receipt_path.is_file():
        print(f"no native SessionStart receipt at {receipt_path}", file=sys.stderr)
        return 1
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    installed = (args.repo / ".codex" / "jasmine" / "jasmine-p4-session-start.py").read_bytes()
    checks = {
        "receipt_names_session": receipt.get("session_id") == thread,
        "receipt_names_pack": receipt.get("context_pack_id") == pack["context_pack_id"],
        "receipt_pack_hash": receipt.get("pack_sha256") == pack["rendered_sha256"],
        "receipt_hook_script": receipt.get("hook_script_sha256") == sha256(installed),
        "receipt_source": receipt.get("source") in ("startup", "resume"),
        "receipt_cwd": pathlib.Path(receipt.get("cwd") or "").resolve() == args.repo.resolve(),
        "receipt_channel": receipt.get("injected_via") == "hookSpecificOutput.additionalContext",
    }
    if not all(checks.values()):
        print(f"native receipt does not bind: {checks}", file=sys.stderr)
        return 1

    actions = extract_actions(transcript_text)
    if not actions:
        print("the transcript records no tool actions", file=sys.stderr)
        return 1

    # Attribute the workspace transition. A path may be claimed by exactly one
    # file-change action; anything else is recorded as unattributable, and the
    # Gate refuses to compute a metric over it.
    touch: dict[str, list[int]] = {}
    for index, action in enumerate(actions):
        if action["kind"] != "file_change":
            continue
        for change in action["changes"]:
            path = str(change.get("path") or "")
            if path:
                touch.setdefault(path, []).append(index)
    status = subprocess.run(["git", "-C", str(args.repo), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    changed_paths = set()
    for line in status.splitlines():
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry.startswith('"') and entry.endswith('"'):
            entry = entry[1:-1]
        changed_paths.add(entry)
    unattributed = sorted(p for p in changed_paths
                          if not any(p.endswith(path) for path in touch))
    for index, action in enumerate(actions):
        if action["kind"] != "file_change":
            continue
        paths = [str(c.get("path") or "") for c in action["changes"]]
        if any(len(touch[path]) != 1 for path in paths if path):
            action["_unattributable"] = "several file-change actions touched one path"
            continue
        for change in action["changes"]:
            path = str(change.get("path") or "")
            before, after, diff = changes_for(args.repo, args.ref, path)
            if before == after:
                change["no_effect"] = True
            change["before_sha256"], change["after_sha256"], change["diff"] = before, after, diff

    gate = gm.Gate(args.work, config, "P4-G01")
    gate.open()
    evidence_ids: list[str | None] = []
    try:
        for action in actions:
            if action["kind"] == "file_change" and action.get("_unattributable"):
                evidence_ids.append(None)
                continue
            if action["kind"] == "file_change" and all(
                    change.get("no_effect") for change in action["changes"]):
                # A patch that changed nothing is not a file transition; it is
                # bound as an ordinary zero-result action so it cannot pretend
                # to be a repair.
                pass
            command, output = action_payload(action)
            evidence_ids.append(gate.collect_tool_evidence(
                pack["task_id"], command=command, output=output, exit_code=0,
                tool_name=action["tool_name"]))
    finally:
        gate.close()

    tool_events = []
    for index, (action, evidence_id) in enumerate(zip(actions, evidence_ids), 1):
        entry = {
            "seq": index,
            "kind": action["kind"],
            "tool_name": action["tool_name"],
            "evidence_id": evidence_id,
        }
        if action["kind"] == "shell":
            entry["command"], entry["output"] = action_payload(action)
            entry["exit_code"] = action["exit_code"]
        elif action["kind"] == "file_change":
            entry["changes"] = [{k: v for k, v in change.items()} for change in action["changes"]]
        else:
            entry["command"], entry["output"] = action_payload(action)
        tool_events.append(entry)

    snapshot = {}
    for name in TRACKED:
        target = args.repo / name
        snapshot[name] = sha256(target.read_bytes()) if target.is_file() else None

    declared = final_json(args.last.read_text(encoding="utf-8"))
    answer_text = str(declared.get("final_answer") or "")
    carried = {row["memory_id"]: row for row in (pack.get("qualified_provenance") or [])}
    memory_seen = []
    for memory_id, row in carried.items():
        if memory_id in answer_text or any(e in answer_text for e in row.get("evidence_ids") or []):
            memory_seen.append({
                "memory_id": memory_id,
                "memory_type": next((m["memory_type"] for m in pack["memory_selected"]
                                     if m["memory_id"] == memory_id), None),
                "evidence_ids": row.get("evidence_ids") or [],
                "source_event_ids": row.get("source_event_ids") or [],
            })

    green = [event for event in tool_events
             if event["kind"] == "shell" and "Ran 1 test" in event.get("output", "")
             and "OK" in event.get("output", "")]
    # Deliberately NOT `session-<label>.transcript.json`: that name is the Gate's
    # sealed copy of the session file itself. The raw Codex stream gets its own
    # name and its own digest field, and the Gate re-extracts it.
    stream_target = args.work / "P4-G01" / f"session-{args.label}.codex.jsonl"
    stream_target.write_bytes(transcript_text.encode("utf-8"))
    stream_target.chmod(0o600)

    review = None
    if args.review is not None:
        review = json.loads(args.review.read_text(encoding="utf-8"))

    payload = {
        "commit": subprocess.run(["git", "-C", str(pathlib.Path.cwd()), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip(),
        "context_pack_id": pack["context_pack_id"],
        "pack_sha256": pack["rendered_sha256"],
        "session_kind": "real Codex session, fresh context; the exact Context Pack was "
                        "injected through the project-local SessionStart hook",
        "native_session_id": thread,
        "memory_seen": memory_seen,
        "task_completed": bool(green),
        "final_verification": ({"command": green[-1]["command"], "output": green[-1]["output"]}
                               if green else None),
        "tool_events": tool_events,
        "unattributed_changes": unattributed,
        "snapshot": snapshot,
        "stream_sha256": sha256(transcript_text.encode("utf-8")),
        "final_answer": declared.get("final_answer"),
        "g01_v2": {
            "spec_sha256": config.get("g01_v2_spec_sha256"),
            "prompt_sha256": config.get("g01_v2_prompt_sha256"),
            "fixture_ref": args.ref,
            "codex_version": config.get("g01_v2_codex_version"),
            "codex_model": config.get("g01_v2_codex_model"),
            "fixture_repo": str(args.repo),
        },
    }
    if review is not None:
        payload["semantic_review"] = review
    out = args.work / "P4-G01" / f"session-{args.label}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out.chmod(0o600)
    print(json.dumps({
        "label": args.label, "thread": thread, "actions": len(tool_events),
        "bound": sum(1 for event in tool_events if event["evidence_id"]),
        "unattributed_changes": unattributed,
        "task_completed": payload["task_completed"],
        "wrote": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
