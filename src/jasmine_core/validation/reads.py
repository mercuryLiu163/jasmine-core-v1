"""The P7-01 Validation read surface (ADR 0015).

Five `GET` routes read the run records `validation.recorder` already wrote. This
module never writes, never runs a scenario, never re-signs or upgrades a verdict
and never resolves a path a request named: the root is the operator's
`JASMINE_CORE_VALIDATION_RUNS`, artifact paths come only from a bundle manifest
whose string rules have already passed, and every artifact is opened with a
per-component descriptor-relative `O_NOFOLLOW` walk mirrored from
`fingerprint.hash_workspace_file`.

The record grammar, the discovery walk, the ordering tuple, the manifest
convention, the chain links and the evidence grade are frozen by the approved
proposal (`web/P7-01-CONTRACT-PROPOSAL.md`, revision 5). Where a rule there is
route-scoped, this module evaluates the routes in the table's order so a
malformed id never reads a record and a refusal reveals nothing about a run the
caller may not read.

Two isolation invariants are implemented here on purpose, closing the approving
review's AD1 and AD2:

* `empty_reason` is derived from the **visible** run-shaped set, never the raw
  walk, so an `acl` caller cannot use it as a one-bit existence oracle;
* in `acl` mode `ignored_samples[]` is empty and only `ignored_entries` (a count)
  is reported, because a relative path can name operator directory structure.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import auth, canonical, errors, ids
from ..api.request import Request, Response
from ..memory import scope as scope_module
from .recorder import SECTIONS

if TYPE_CHECKING:  # pragma: no cover - import cycle broken at runtime
    # `api.handlers` imports this module to extend its route table, so the
    # reverse import exists only for type checking. `routes()` receives the
    # `Route` class as an argument for the same reason `memory.api.routes` does.
    from ..api.handlers import Core

Handler = Callable[..., Response]

#: The operator's configured run root. There is no default: an unset value makes
#: every Validation route refuse `503 validation_not_configured`.
ROOT_ENV_VAR = "JASMINE_CORE_VALIDATION_RUNS"

#: Walk limits (proposal §8).
MAX_DEPTH = 4
MAX_SCANNED_ENTRIES = 20_000
MAX_RUN_SHAPED = 500
MAX_MANIFEST_BYTES = 1024 * 1024
#: Robustness bound for one run record. §8 names no record size, but it does
#: require that no route can be made to read an unbounded amount of data, and
#: the list route classifies every discovered record. A real record is a few
#: kilobytes; a file over this bound is reported corrupt rather than read.
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_FILES = 128
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
LIMIT_DEFAULT = 20
LIMIT_MAX = 100
IGNORED_SAMPLE_CAP = 20
UNREADABLE_SAMPLE_CAP = 20

RUN_FILE_RE = re.compile(r"^run-[0-9]{8}T[0-9]{6}Z\.json$")
RUN_ID_RE = re.compile(r"^run-[0-9]{8}T[0-9]{6}Z$")
ARTIFACT_ID_RE = re.compile(r"^art_[a-z0-9][a-z0-9_.-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
DRIVE_RE = re.compile(r"^[A-Za-z]:")

BUNDLE_SCHEMA = "jasmine.validation-bundle.v1"
SENTINEL = "N/A (P0 not implemented)"
VERDICTS = ("PASS", "FAIL", "BLOCKED")
REQUIRED_RECORD_FIELDS = ("run_id", "stage", "created_at", "verdict", "cases")
REASON_ONLY_KEYS = frozenset({"reason", "note", "detail", "not_applied_reason"})
CONTEXT_PACK_KEYS = frozenset(
    {"context_id", "pack", "sections", "rendered", "content", "tokens", "parts"}
)
ALLOWED_TEXT_MEDIA = frozenset({
    "application/json", "application/jsonl", "application/x-ndjson",
    "application/xml", "application/yaml", "application/x-yaml",
})

#: Read from the record, not from the environment, and never a substring search.
MARKER_GRADE = {
    "synthetic": "simulated",
    "simulated": "simulated",
    "fixture": "simulated",
    "mock": "simulated",
    "component": "component",
}
TRUST = {"real": 0, "component": 1, "simulated": 2, "unknown": 3}
EXECUTOR_RE = re.compile(r"(?i)(fixture|mock|simulat|synthetic)")

_MISSING = object()

#: Root resolution is cached against the raw configured string, so a test or a
#: restarted deployment can point at a different root without a stale cache while
#: a request can never name or extend the path. A failed resolution is not cached.
_ROOT_CACHE: dict[str, Any] = {"raw": None, "path": None}


# -- services -----------------------------------------------------------------


class ValidationServices:
    """The grant model, built per `Core` and reused across one request.

    Constructed from the same live `core.conn` the reader holds and reusing the
    frozen P4 `memory.scope.ProjectAccess`, so no parallel authorisation
    mechanism is invented and no migration is needed.
    """

    def __init__(self, core: "Core") -> None:
        self.conn: sqlite3.Connection = core.conn
        self.mode = scope_module.deployment_mode()
        self.access = scope_module.ProjectAccess(self.conn, mode=self.mode)

    def require_mode_ready(self) -> None:
        # An `acl` deployment with no grants at all refuses to serve rather than
        # treating an empty table as "everyone".
        self.access.require_mode_ready()

    def may_read_projects(self, projects: tuple[str, ...] | list[str], actor_id: str) -> bool:
        return all(self.access.may_read(project_id=project, actor_id=actor_id)
                   for project in projects)


def services(core: "Core") -> ValidationServices:
    found = getattr(core, "validation_services", None)
    if found is None:
        found = ValidationServices(core)
        core.validation_services = found
    return found


# -- root ---------------------------------------------------------------------


def _validation_not_configured(reason: str, **details: Any) -> errors.ValidationNotConfigured:
    return errors.ValidationNotConfigured(
        "the Validation run root is not usable", reason=reason, **details)


def _resolve_root() -> Path:
    """The configured root, or `503 validation_not_configured`.

    The root is opened `O_DIRECTORY|O_NOFOLLOW`, so a symlinked root is refused
    rather than followed: a request must not be able to redirect the walk.
    """
    raw = os.environ.get(ROOT_ENV_VAR)
    if raw is None or not raw.strip():
        raise _validation_not_configured("unset")
    if _ROOT_CACHE["raw"] == raw and _ROOT_CACHE["path"] is not None:
        return _ROOT_CACHE["path"]
    path = Path(raw).expanduser()
    if os.path.islink(path):
        raise _validation_not_configured("symlinked_root", root=raw)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise _validation_not_configured("missing", root=raw) from exc
    except NotADirectoryError as exc:
        raise _validation_not_configured("not_a_directory", root=raw) from exc
    except PermissionError as exc:
        raise _validation_not_configured("root_unreadable", root=raw) from exc
    except OSError as exc:
        reason = "symlinked_root" if exc.errno == errno.ELOOP else "root_unreadable"
        raise _validation_not_configured(reason, root=raw) from exc
    else:
        os.close(descriptor)
    _ROOT_CACHE["raw"] = raw
    _ROOT_CACHE["path"] = path
    return path


# -- discovery ----------------------------------------------------------------


@dataclass
class _Walk:
    root: Path
    paths: list[Path] = field(default_factory=list)
    relative: dict[Path, str] = field(default_factory=dict)
    ignored: list[tuple[str, str]] = field(default_factory=list)
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    scanned_dirs: int = 0
    scanned_files: int = 0
    scanned_entries: int = 0
    capped: bool = False
    cap_name: str | None = None


def _walk(root: Path, *, stop_on_caps: bool) -> _Walk:
    """The §2 walk: depth 4, dot/`__pycache__`/symlink dirs pruned, symlinked
    `run-*.json` ignored, and every unreadable directory counted.

    `os.walk(..., onerror=...)` is deliberate: the default `onerror=None`
    swallows a directory it cannot list, which would make `index_incomplete`
    unreachable and let a page silently omit records.
    """
    result = _Walk(root=root)

    def on_error(exc: OSError) -> None:
        name = getattr(exc, "filename", None)
        result.unreadable.append((str(name) if name else "?", type(exc).__name__))

    def note_cap(name: str) -> None:
        result.capped = True
        if result.cap_name is None:
            result.cap_name = name

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts)
        result.scanned_dirs += 1
        result.scanned_entries += 1
        # F2: the CURRENT directory's files are at `depth`, and the cap is
        # "at most 4 directory levels below the root", so a file in a directory
        # four levels down is inside the cap. The filenames are therefore
        # processed first (below) and only the descent past the cap is pruned.
        keep: list[str] = []
        for name in dirnames:
            if name.startswith(".") or name == "__pycache__":
                result.ignored.append((str(rel_dir / name), "excluded_dir"))
                result.scanned_entries += 1
                continue
            if (Path(dirpath) / name).is_symlink():
                # A symlinked directory is never traversed. Its contents are not
                # enumerated, so it counts as one ignored entry.
                result.ignored.append((str(rel_dir / name), "symlinked_directory"))
                result.scanned_entries += 1
                continue
            keep.append(name)
        if depth >= MAX_DEPTH:
            # Subdirectories here would be at depth+1, past the cap: name each
            # one and do not descend. The contents are not enumerated, so a
            # pruned directory counts as one ignored entry.
            for name in keep:
                result.ignored.append((str(rel_dir / name), "depth_cap"))
            dirnames[:] = []
        else:
            dirnames[:] = keep
        for name in filenames:
            result.scanned_files += 1
            result.scanned_entries += 1
            candidate = Path(dirpath) / name
            if not RUN_FILE_RE.match(name):
                result.ignored.append((str(rel_dir / name), "not_run_shaped"))
                continue
            if candidate.is_symlink():
                result.ignored.append((str(rel_dir / name), "symlinked_file"))
                continue
            result.paths.append(candidate)
            result.relative[candidate] = str(rel_dir / name)
            if stop_on_caps and len(result.paths) > MAX_RUN_SHAPED:
                note_cap("MAX_RUN_SHAPED")
                dirnames[:] = []
        if result.scanned_entries > MAX_SCANNED_ENTRIES:
            note_cap("MAX_SCANNED_ENTRIES")
            dirnames[:] = []
    return result


@dataclass
class _Record:
    stem: str
    relative_path: str
    path: Path
    record_sha256: str
    status: str = "ok"
    data: dict[str, Any] | None = None
    corrupt_reason: str | None = None
    duplicate_reason: str | None = None


def _classify(path: Path, relative_path: str) -> _Record:
    """The §2 run-shaped grammar. Anything run-shaped is kept, never dropped."""
    # F4: `_classify` runs for every run-shaped file on the list route, so the
    # size is checked before the bytes are read. §8 says no route may read an
    # unbounded amount of data; a record over the bound is reported corrupt
    # rather than read.
    try:
        if path.stat().st_size > MAX_RECORD_BYTES:
            return _Record(stem=path.stem, relative_path=relative_path, path=path,
                           record_sha256="", status="corrupt",
                           corrupt_reason=f"record exceeds {MAX_RECORD_BYTES} bytes")
    except OSError as exc:
        return _Record(stem=path.stem, relative_path=relative_path, path=path,
                       record_sha256="", status="corrupt",
                       corrupt_reason=f"unreadable: {type(exc).__name__}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _Record(stem=path.stem, relative_path=relative_path, path=path,
                       record_sha256="", status="corrupt",
                       corrupt_reason=f"unreadable: {type(exc).__name__}")
    digest = hashlib.sha256(raw).hexdigest()
    record = _Record(stem=path.stem, relative_path=relative_path, path=path,
                     record_sha256=digest)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        record.status = "corrupt"
        record.corrupt_reason = f"unparseable: {type(exc).__name__}"
        return record
    if not isinstance(data, dict):
        record.status = "corrupt"
        record.corrupt_reason = "the record is not a JSON object"
        return record
    for name in REQUIRED_RECORD_FIELDS:
        if name not in data:
            record.status = "corrupt"
            record.corrupt_reason = f"missing required field: {name}"
            return record
    if not isinstance(data.get("run_id"), str) or data.get("run_id") != path.stem:
        record.status = "corrupt"
        record.corrupt_reason = "run_id does not match the filename stem"
        return record
    if not isinstance(data.get("stage"), str):
        record.status = "corrupt"
        record.corrupt_reason = "stage is not a string"
        return record
    if not isinstance(data.get("created_at"), str):
        record.status = "corrupt"
        record.corrupt_reason = "created_at is not a string"
        return record
    if data.get("verdict") not in VERDICTS:
        record.status = "corrupt"
        record.corrupt_reason = "verdict is not one of PASS, FAIL, BLOCKED"
        return record
    if not isinstance(data.get("cases"), list):
        record.status = "corrupt"
        record.corrupt_reason = "cases is not a list"
        return record
    record.data = data
    return record


def _mark_duplicates(records: list[_Record]) -> None:
    """Only parseable records take part: a corrupt file has no run_id to compare."""
    by_stem: dict[str, list[_Record]] = {}
    for record in records:
        if record.status == "ok":
            by_stem.setdefault(record.stem, []).append(record)
    for group in by_stem.values():
        if len(group) > 1:
            for record in group:
                record.status = "duplicate"
                record.duplicate_reason = f"{len(group)} files share this stem"


def _records_for_stem(walk: _Walk, stem: str) -> list[_Record]:
    matches = [path for path in walk.paths if path.stem == stem]
    return [_classify(path, walk.relative[path]) for path in matches]


# -- presence, chain and grade (proposal §1.1, §4.1, §4.2) --------------------


def _presence(value: Any, present: bool, *, context: bool = False) -> dict[str, Any]:
    if not present:
        section: dict[str, Any] = {
            "present": False, "present_kind": "absent",
            "missing_reason": "the record has no such field"}
    elif isinstance(value, str) and value == SENTINEL:
        section = {"present": False, "present_kind": "sentinel",
                   "missing_reason": f"recorded as {SENTINEL}"}
    elif isinstance(value, dict) and set(value) <= REASON_ONLY_KEYS:
        reason = value.get("reason")
        if reason is None and value:
            reason = next(iter(value.values()))
        section = {"present": False, "present_kind": "reason_only",
                   "recorded_reason": reason if isinstance(reason, str) else (
                       None if reason is None else str(reason))}
    elif isinstance(value, str) and value.strip() == "":
        section = {"present": False, "present_kind": "absent", "missing_reason": "recorded as empty"}
    elif isinstance(value, list) and not value:
        section = {"present": False, "present_kind": "absent", "missing_reason": "recorded as empty"}
    else:
        section = {"present": True, "present_kind": "value", "value": value}
    if context:
        # The context section always carries the stricter chain-link fact; a
        # recorded run-details object is `present` but holds no context pack.
        pack = (present and isinstance(value, dict) and bool(set(value) & CONTEXT_PACK_KEYS)) or (
            present and isinstance(value, list) and bool(value))
        section["context_pack_present"] = bool(pack)
    return section


def _marker_grade(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, grade in MARKER_GRADE.items():
            if value.get(key):
                return grade
    return None


def _worst(*grades: str) -> str:
    return max(grades, key=lambda grade: TRUST[grade])


def _case_grade(run: dict[str, Any], case: dict[str, Any], mode: str) -> tuple[str, str]:
    """Proposal §4.2: first matching rule wins, and no rule upgrades a case."""
    if run.get("synthetic") is True:
        return "simulated", "the run record marks itself synthetic"
    own = _marker_grade(case.get("agent_response"))
    if own:
        return own, "recorded in the case"
    executor = run.get("executor")
    if isinstance(executor, str) and EXECUTOR_RE.search(executor):
        return "simulated", "run.executor"
    if mode in ("real", "component", "simulated"):
        return mode, "the qualifying bundle manifest"
    return "unknown", "no qualifying bundle manifest"


def _case_counts(cases: list[Any]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "blocked": 0}
    for case in cases:
        verdict = case.get("verdict") if isinstance(case, dict) else None
        if verdict == "PASS":
            counts["pass"] += 1
        elif verdict == "FAIL":
            counts["fail"] += 1
        elif verdict == "BLOCKED":
            counts["blocked"] += 1
    counts["total"] = len(cases)
    return counts


def _verdict_of(cases: list[Any]) -> str:
    if any(isinstance(case, dict) and case.get("verdict") == "FAIL" for case in cases):
        return "FAIL"
    if any(isinstance(case, dict) and case.get("verdict") == "BLOCKED" for case in cases):
        return "BLOCKED"
    return "PASS"


def _chain(case: dict[str, Any], sections: dict[str, Any]) -> dict[str, Any]:
    raw = sections["user_raw"]
    context = sections["context"]
    agent = sections["agent_response"]
    state_before = sections["state_before"]
    state_after = sections["state_after"]

    raw_link: dict[str, Any] = {"present": raw["present"], "source": "case.user_raw"}
    if not raw["present"]:
        raw_link["missing_reason"] = (
            "the case records a reason, not a raw value"
            if raw["present_kind"] == "reason_only"
            else raw.get("missing_reason", "the section is not recorded"))

    pack_present = bool(context["present"] and context.get("context_pack_present"))
    context_link: dict[str, Any] = {"present": pack_present, "source": "case.context"}
    if not pack_present:
        if context["present_kind"] == "reason_only":
            context_link["missing_reason"] = "the case records a reason, not a context pack"
        elif context["present_kind"] == "value":
            context_link["missing_reason"] = "the section records run details, not a context pack"
        else:
            context_link["missing_reason"] = context.get(
                "missing_reason", "the section is not recorded")

    agent_link: dict[str, Any] = {"present": agent["present"], "source": "case.agent_response"}
    if not agent["present"]:
        agent_link["missing_reason"] = (
            "the case records a reason, not an agent response"
            if agent["present_kind"] == "reason_only"
            else agent.get("missing_reason", "the section is not recorded"))

    tool_present = bool(case.get("tools"))
    tool_link: dict[str, Any] = {"present": tool_present, "source": "case.tools"}
    if not tool_present:
        tool_link["missing_reason"] = "the case records no tool call"

    evidence_present = bool(case.get("evidence"))
    evidence_link: dict[str, Any] = {"present": evidence_present, "source": "case.evidence"}
    if not evidence_present:
        evidence_link["missing_reason"] = "the case records no evidence"

    state_present = bool(state_before["present"] or state_after["present"])
    state_link: dict[str, Any] = {
        "present": state_present, "source": "case.state_before + case.state_after"}
    if not state_present:
        state_link["missing_reason"] = (
            "neither state_before nor state_after records a value")

    return {
        "raw": raw_link,
        "extraction": {"present": False, "source": None,
                       "missing_reason": "the recorder records no Extraction section"},
        "resolution": {"present": False, "source": None,
                       "missing_reason": "the recorder records no Resolution section"},
        "context": context_link,
        "agent": agent_link,
        "tool": tool_link,
        "evidence": evidence_link,
        "state": state_link,
    }


def _detail_case(run: dict[str, Any], case: Any, mode: str) -> dict[str, Any]:
    case = case if isinstance(case, dict) else {"value": case}
    grade, basis = _case_grade(run, case, mode)
    sections: dict[str, Any] = {}
    for name in SECTIONS:
        section = _presence(case.get(name, _MISSING), name in case, context=(name == "context"))
        own_marker = _marker_grade(case.get(name))
        section["evidence_grade"] = _worst(grade, own_marker) if own_marker else grade
        sections[name] = section
    evidence = [
        {"recorded": entry, "status": "unaddressable",
         "reason": "recorded evidence is free-form data and is never resolved; artifacts are "
                   "addressable only through a bundle manifest"}
        for entry in (case.get("evidence") if isinstance(case.get("evidence"), list) else [])
    ]
    return {
        **case,
        "case_id": case.get("case_id"),
        "name": case.get("name"),
        "verdict": case.get("verdict"),
        "failure_class": case.get("failure_class"),
        "failure_output": case.get("failure_output"),
        "reproduction": case.get("reproduction") if isinstance(case.get("reproduction"), list) else [],
        "not_applied_reason": case.get("not_applied_reason"),
        "evidence": evidence,
        "sections": sections,
        "chain": _chain(case, sections),
        "evidence_grade": grade,
        "grade_basis": basis,
    }


def _run_grade(mode: str, grades: list[str]) -> str:
    """Least trustworthy of the effective bundle mode and every case grade."""
    return _worst(mode, *grades) if grades else mode


# -- manifest (proposal §3) ---------------------------------------------------


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _classify_path(path: Any) -> tuple[bool, str]:
    """The §3 declared-path string rules, in the dry run's order."""
    if not isinstance(path, str) or not path:
        return False, "empty"
    if "\x00" in path:
        return False, "NUL"
    if path.startswith("/") or path.startswith("\\"):
        return False, "absolute/UNC prefix"
    if DRIVE_RE.match(path):
        return False, "drive-relative"
    if "\\" in path:
        return False, "backslash"
    if URL_SCHEME_RE.match(path):
        return False, "URL scheme"
    # F3: `Path(path).parts` normalises `a//b`, `a/./b` and a trailing `/` away
    # before a component check can see them, so the raw string is validated.
    if ".." in path:
        return False, "dot segment"
    if "//" in path:
        return False, "empty path component"
    components = path.split("/")
    if any(component in ("", ".") for component in components):
        return False, "empty or dot path component"
    return True, "ok"


@dataclass
class _Manifest:
    status: str  # frozen | absent | corrupt
    sha256: str | None = None
    data: dict[str, Any] | None = None
    rule: str | None = None
    reason: str | None = None


def _load_manifest(run_dir: Path, run_id: str) -> _Manifest:
    """Classify `<run_dir>/manifest.json` without reading artifact contents.

    The list route needs only the status (and the projects/mode); reading every
    declared artifact is left to the manifest and artifact routes.
    """
    path = run_dir / "manifest.json"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return _Manifest(status="corrupt", rule="manifest.json is not a regular file")
    if not path.exists():
        return _Manifest(status="absent",
                         reason="the record predates the bundle convention and its history "
                                "is not reconstructed")
    # F4: refuse on the stat before reading, so a huge manifest is never made
    # resident. No digest is reported for a file that was not read.
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return _Manifest(status="corrupt",
                             rule=f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    except OSError as exc:
        return _Manifest(status="corrupt", rule=f"manifest.json is unreadable: {type(exc).__name__}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _Manifest(status="corrupt", rule=f"manifest.json is unreadable: {type(exc).__name__}")
    sha = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_MANIFEST_BYTES:
        return _Manifest(status="corrupt", sha256=sha,
                         rule=f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _Manifest(status="corrupt", sha256=sha, rule=f"unparseable: {type(exc).__name__}")
    if not isinstance(data, dict):
        return _Manifest(status="corrupt", sha256=sha, rule="manifest is not a JSON object")
    if data.get("schema") != BUNDLE_SCHEMA:
        return _Manifest(status="corrupt", sha256=sha, rule=f"schema is not {BUNDLE_SCHEMA}")
    if data.get("run_id") != run_id:
        return _Manifest(status="corrupt", sha256=sha, rule="run_id does not match the record")
    files = data.get("files")
    if not isinstance(files, list):
        return _Manifest(status="corrupt", sha256=sha, rule="files is not a list")
    if len(files) > MAX_MANIFEST_FILES:
        return _Manifest(status="corrupt", sha256=sha,
                         rule=f"files exceeds {MAX_MANIFEST_FILES}")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            return _Manifest(status="corrupt", sha256=sha, rule="file entry is not an object")
        artifact_id = entry.get("artifact_id")
        if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
            return _Manifest(status="corrupt", sha256=sha,
                             rule=f"artifact_id {artifact_id!r} fails the grammar")
        if artifact_id in seen:
            return _Manifest(status="corrupt", sha256=sha,
                             rule=f"duplicate artifact_id {artifact_id}")
        seen.add(artifact_id)
        ok, rule = _classify_path(entry.get("path"))
        if not ok:
            return _Manifest(status="corrupt", sha256=sha,
                             rule=f"{artifact_id}: {rule} path {entry.get('path')!r}")
        if not _is_sha256(entry.get("sha256")):
            return _Manifest(status="corrupt", sha256=sha,
                             rule=f"{artifact_id}: sha256 is not 64 lowercase hex")
        declared_bytes = entry.get("bytes")
        if not isinstance(declared_bytes, int) or isinstance(declared_bytes, bool) or declared_bytes < 0:
            return _Manifest(status="corrupt", sha256=sha,
                             rule=f"{artifact_id}: bytes is not a non-negative integer")
    return _Manifest(status="frozen", sha256=sha, data=data)


def _recorder_provenance(sources: Any) -> bool:
    if not isinstance(sources, dict):
        return False
    recorder = sources.get("recorder")
    if isinstance(recorder, dict):
        return (isinstance(recorder.get("path"), str) and bool(recorder.get("path"))
                and _is_sha256(recorder.get("sha256")))
    if isinstance(recorder, str):
        path, separator, digest = recorder.rpartition("@")
        return bool(separator) and bool(path) and not path.startswith("/") \
            and "\\" not in path and _is_sha256(digest)
    return False


def _effective_mode(manifest: _Manifest) -> tuple[str, str]:
    """Proposal §3: `mode` is derived, never trusted."""
    if manifest.status == "absent":
        return "unknown", "no bundle manifest on disk"
    if manifest.status == "corrupt":
        return "unknown", "the bundle manifest is corrupt"
    data = manifest.data or {}
    declared = data.get("mode")
    if declared is None:
        return "unknown", "the manifest declares no mode"
    if declared == "real":
        projects = data.get("projects")
        if isinstance(projects, list) and projects and _recorder_provenance(data.get("sources")):
            return "real", "declared real with recorder provenance and project attribution"
        return "unknown", "declared real without recorder provenance or project attribution"
    if declared in ("component", "simulated"):
        return declared, "declared by the manifest"
    return "unknown", "the manifest declares an unrecognised mode"


def _attribution(manifest: _Manifest) -> dict[str, Any]:
    if manifest.status == "corrupt":
        return {"status": "unattributed", "reason": "manifest_corrupt", "projects": []}
    if manifest.status == "absent":
        return {"status": "unattributed", "reason": "manifest_absent", "projects": []}
    projects = (manifest.data or {}).get("projects")
    if isinstance(projects, list) and projects:
        return {"status": "attributed", "projects": list(projects)}
    return {"status": "unattributed", "reason": "declared_empty", "projects": []}


def _manifest_projects(manifest: _Manifest) -> list[str]:
    if manifest.status != "frozen":
        return []
    projects = (manifest.data or {}).get("projects")
    if isinstance(projects, list) and projects:
        return list(projects)
    return []


# -- artifact bytes (the mirrored fingerprint walk) ---------------------------


@dataclass
class _ArtifactRead:
    integrity: str
    buffer: bytes | None = None
    actual_bytes: int | None = None
    too_large: bool = False
    error: str | None = None  # missing | symlink_refused | unreadable


def _read_artifact(run_dir: Path, relative: str, declared_sha: str,
                   declared_bytes: int, cap: int) -> _ArtifactRead:
    """Open every component with `dir_fd`+`O_NOFOLLOW`, read once, hash that buffer.

    Mirrors `fingerprint.hash_workspace_file` because that helper returns only
    `(digest, length)` and cannot hand back the verified buffer. Final-component
    `O_NOFOLLOW` plus an `lstat`/inode comparison cannot see a symlinked
    ancestor, so the whole walk is descriptor-relative.
    """
    parts = Path(relative).parts
    if not parts or any(part in (".", "..", "") for part in parts) or Path(relative).is_absolute():
        return _ArtifactRead(integrity="unreadable", error="unreadable")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        directory = os.open(run_dir, flags | os.O_DIRECTORY)
    except OSError:
        return _ArtifactRead(integrity="unreadable", error="unreadable")
    try:
        for part in parts[:-1]:
            # `lstat` is a supplementary classifier, not the check: on macOS an
            # intermediate symlinked directory fails `O_NOFOLLOW|O_DIRECTORY`
            # with ENOTDIR, which would otherwise be reported as "missing". The
            # descriptor-relative `O_NOFOLLOW` open below remains primary.
            if _is_symlink_at(directory, part):
                return _ArtifactRead(integrity="symlink_refused", error="symlink_refused")
            try:
                next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            except OSError as exc:
                return _ArtifactRead(integrity=_integrity_for_oserror(exc),
                                     error=_error_for_oserror(exc))
            os.close(directory)
            directory = next_directory
        if _is_symlink_at(directory, parts[-1]):
            return _ArtifactRead(integrity="symlink_refused", error="symlink_refused")
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=directory)
        except OSError as exc:
            return _ArtifactRead(integrity=_integrity_for_oserror(exc),
                                 error=_error_for_oserror(exc))
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                return _ArtifactRead(integrity="unreadable", error="unreadable")
            if metadata.st_size > cap:
                return _ArtifactRead(integrity="unreadable", too_large=True, error="too_large")
            chunks: list[bytes] = []
            total = 0
            try:
                while True:
                    chunk = os.read(descriptor, min(1024 * 1024, cap + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > cap:
                        return _ArtifactRead(integrity="unreadable", too_large=True,
                                             error="too_large")
            except OSError:
                # An I/O error after a successful open is row 15: the file exists
                # but cannot be read, reported as `503 artifact_unreadable`, never
                # as a 500 or as a fabricated digest.
                return _ArtifactRead(integrity="unreadable", error="unreadable")
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
    buffer = b"".join(chunks)
    # The realpath containment is a second, non-primary check: it is a
    # check-then-open and therefore races, so the descriptor walk above is what
    # actually decides.
    try:
        resolved_root = os.path.realpath(run_dir)
        resolved_file = os.path.realpath(run_dir / relative)
        if os.path.commonpath([resolved_root, resolved_file]) != resolved_root:
            return _ArtifactRead(integrity="unreadable", error="unreadable")
    except (OSError, ValueError):
        return _ArtifactRead(integrity="unreadable", error="unreadable")
    digest = hashlib.sha256(buffer).hexdigest()
    match = digest == declared_sha and len(buffer) == declared_bytes
    return _ArtifactRead(integrity="match" if match else "mismatch", buffer=buffer,
                         actual_bytes=len(buffer))


def _is_symlink_at(directory: int, name: str) -> bool:
    """Whether `name` is a symlink, resolved relative to the open directory fd.

    Descriptor-relative, so a parent-directory swap between this check and the
    `O_NOFOLLOW` open cannot change the answer.
    """
    try:
        return stat.S_ISLNK(os.lstat(name, dir_fd=directory).st_mode)
    except OSError:
        return False


def _integrity_for_oserror(exc: OSError) -> str:
    if exc.errno == errno.ELOOP:
        return "symlink_refused"
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        return "missing"
    return "unreadable"


def _error_for_oserror(exc: OSError) -> str:
    return _integrity_for_oserror(exc)


def _decode(buffer: bytes) -> str | None:
    try:
        return buffer.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _masked_digest(buffer: bytes) -> str | None:
    text = _decode(buffer)
    if text is None:
        return None
    return hashlib.sha256(canonical.redact_text(text).encode("utf-8")).hexdigest()


def _content_scan(buffer: bytes) -> dict[str, Any]:
    text = _decode(buffer)
    if text is None:
        return {"performed": True, "mask_is_noop": False, "credential_shaped": False}
    masked = canonical.redact_text(text)
    return {"performed": True, "mask_is_noop": masked == text,
            "credential_shaped": canonical.looks_like_credential(text)}


def _text_media(media_type: Any) -> bool:
    if not isinstance(media_type, str):
        return False
    return media_type.startswith("text/") or media_type in ALLOWED_TEXT_MEDIA


# -- request helpers ----------------------------------------------------------


def _require_run_id(value: str) -> None:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value):
        raise errors.InvalidRequest("run_id must match run-YYYYmmddTHHMMSSZ",
                                    field="run_id", value=value)


def _require_artifact_id(value: str) -> None:
    if not isinstance(value, str) or not ARTIFACT_ID_RE.fullmatch(value):
        raise errors.InvalidRequest(
            "artifact_id must match art_[a-z0-9][a-z0-9_.-]{0,63}", field="artifact_id", value=value)


def _int_param(request: Request, name: str, default: int) -> int:
    raw = request.param(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise errors.InvalidRequest(f"{name} must be an integer", field=name, value=raw) from exc


def _no_query(request: Request, route: str) -> None:
    if request.query:
        raise errors.InvalidRequest(f"{route} takes no query parameters")


def _run_visible(svc: ValidationServices, principal: auth.Principal, *,
                 projects: list[str]) -> bool:
    """§6/§2: in `acl` a run is visible iff every declared project is granted,
    and an unattributed run needs the explicit `validation:unattributed` grant."""
    if svc.mode == scope_module.SINGLE_USER:
        return True
    if not projects:
        return "validation:unattributed" in principal.scopes
    return svc.may_read_projects(projects, principal.actor_id)


def _counts(walk: _Walk) -> dict[str, int]:
    return {"scanned_dirs": walk.scanned_dirs, "scanned_files": walk.scanned_files,
            "run_shaped_files": len(walk.paths), "unreadable_entries": len(walk.unreadable),
            "ignored_entries": len(walk.ignored)}


def _discovery(walk: _Walk, run_shaped_files: int) -> dict[str, Any]:
    return {"root_present": True, "max_depth": MAX_DEPTH,
            "scanned_dirs": walk.scanned_dirs, "scanned_files": walk.scanned_files,
            "run_shaped_files": run_shaped_files,
            "unreadable_entries": len(walk.unreadable), "capped": walk.capped}


# -- handlers -----------------------------------------------------------------


def list_runs(request: Request, core: "Core", principal: auth.Principal,
              match: re.Match[str]) -> Response:
    """`GET /v1/validation/runs` (proposal §2)."""
    root = _resolve_root()
    walk = _walk(root, stop_on_caps=True)
    if walk.unreadable:
        # Row 4: a knowingly incomplete index is never paginated.
        raise _validation_not_configured(
            "index_incomplete", counts=_counts(walk),
            samples=[{"path": path, "error": error}
                     for path, error in walk.unreadable[:UNREADABLE_SAMPLE_CAP]])
    svc = services(core)
    svc.require_mode_ready()
    if walk.capped:
        raise errors.ValidationIndexTooLarge(
            "the run index exceeds a walk cap", cap=walk.cap_name,
            limit=MAX_SCANNED_ENTRIES if walk.cap_name == "MAX_SCANNED_ENTRIES" else MAX_RUN_SHAPED)
    params = _list_params(request)

    records = [_classify(path, walk.relative[path]) for path in walk.paths]
    _mark_duplicates(records)

    visible: list[tuple[_Record, _Manifest | None, str | None, str | None, list[str]]] = []
    for record in records:
        if record.status == "ok":
            manifest = _load_manifest(record.path.parent, record.stem)
            mode, basis = _effective_mode(manifest)
            projects = _manifest_projects(manifest)
        else:
            # A corrupt or duplicate record has no readable manifest: it is
            # treated as unattributed, so `acl` hides it without the explicit
            # `validation:unattributed` grant, and no mode filter can match it.
            manifest, mode, basis, projects = None, None, None, []
        if _run_visible(svc, principal, projects=projects):
            visible.append((record, manifest, mode, basis, projects))

    filtered = [entry for entry in visible if _matches(entry, params)]

    # F7: the counts describe the served set, exactly like `total` and
    # `runs[]`, so `verdict=PASS` cannot report a corrupt row that is not in the
    # body. Computed over `filtered`, not over `visible`.
    corrupt_count = sum(1 for entry in filtered if entry[0].status == "corrupt")
    duplicate_count = sum(1 for entry in filtered if entry[0].status == "duplicate")

    # AD1: `empty_reason` is computed over the visible set, never the raw walk,
    # so an `acl` caller cannot infer hidden-record existence from it.
    empty_reason: str | None = None
    if not filtered:
        empty_reason = "no_run_shaped_files" if not visible else "all_filtered_out"

    ordered = _order([entry[0] for entry in filtered])
    total = len(ordered)
    page = ordered[params["offset"]:params["offset"] + params["limit"]]
    by_path = {entry[0].path: entry for entry in filtered}
    rows = []
    for record in page:
        _, manifest, mode, basis, projects = by_path[record.path]
        rows.append(_row(record, manifest, mode, basis, projects))

    ignored_samples: list[dict[str, str]] = []
    if svc.mode != scope_module.ACL:
        ignored_samples = [{"relative_path": path, "reason": reason}
                           for path, reason in walk.ignored[:IGNORED_SAMPLE_CAP]]
    # AD2: in `acl` mode sample paths can name operator directory structure, so
    # only the count is reported. `discovery.run_shaped_files` follows the
    # visible set too (§2), unlike the raw root diagnostics beside it.
    return Response(200, {
        "runs": rows,
        "count": len(rows),
        "total": total,
        "limit": params["limit"],
        "offset": params["offset"],
        "has_more": params["offset"] + len(rows) < total,
        "corrupt_count": corrupt_count,
        "duplicate_count": duplicate_count,
        "ignored_entries": len(walk.ignored),
        "ignored_samples": ignored_samples,
        "discovery": _discovery(walk, len(visible)),
        "complete": True,
        "visibility": "all" if svc.mode != scope_module.ACL else "grant_filtered",
        **({"empty_reason": empty_reason} if empty_reason is not None else {}),
    })


def get_run(request: Request, core: "Core", principal: auth.Principal,
            match: re.Match[str]) -> Response:
    """`GET /v1/validation/runs/{run_id}` (proposal §5)."""
    _resolve_root()
    svc = services(core)
    svc.require_mode_ready()
    run_id = match.group(1)
    _require_run_id(run_id)
    _no_query(request, "run detail")
    record = _locate(svc, principal, run_id)

    manifest = _load_manifest(record.path.parent, record.stem)
    mode, _basis = _effective_mode(manifest)
    projects = _manifest_projects(manifest)
    if not _run_visible(svc, principal, projects=projects):
        raise errors.NotFound("validation_run", run_id)

    data = record.data or {}
    cases = data.get("cases") if isinstance(data.get("cases"), list) else []
    grades = [_detail_case(data, case, mode) for case in cases]
    case_grades = [case["evidence_grade"] for case in grades]
    run_grade = _run_grade(mode, case_grades)
    downgraded = []
    warnings = []
    if mode in ("real", "component", "simulated"):
        for case in grades:
            if TRUST[case["evidence_grade"]] > TRUST[mode]:
                downgraded.append(case["case_id"])
                warnings.append(
                    f"manifest declares {mode} but case {case['case_id']} is graded "
                    f"{case['evidence_grade']}")
    known = {"run_id", "stage", "created_at", "commit", "schema_version", "core_version",
             "environment", "executor", "reviewer", "verifier", "cases", "notes",
             "not_implemented", "redactions", "redaction_warning", "verdict",
             "synthetic", "synthetic_note"}
    unknown_keys = sorted(key for key in data if key not in known)
    return Response(200, {
        "run_id": data.get("run_id"),
        "stage": data.get("stage"),
        "created_at": data.get("created_at"),
        "commit": data.get("commit"),
        "schema_version": data.get("schema_version"),
        "core_version": data.get("core_version"),
        "environment": data.get("environment"),
        "executor": data.get("executor"),
        "reviewer": data.get("reviewer"),
        "verifier": data.get("verifier"),
        "verdict": data.get("verdict"),
        "verdict_consistent": data.get("verdict") == _verdict_of(cases),
        "notes": data.get("notes"),
        "not_implemented": data.get("not_implemented"),
        "cases": grades,
        "synthetic": data.get("synthetic") is True,
        "unknown_record_keys": unknown_keys,
        "evidence_grade": run_grade,
        "grade_downgraded_by": downgraded,
        "warnings": warnings,
        **({"synthetic_note": data["synthetic_note"]} if "synthetic_note" in data else {}),
        **({"redactions": data["redactions"]} if "redactions" in data else {}),
        **({"redaction_warning": data["redaction_warning"]}
           if "redaction_warning" in data else {}),
    })


def get_manifest(request: Request, core: "Core", principal: auth.Principal,
                 match: re.Match[str]) -> Response:
    """`GET /v1/validation/runs/{run_id}/manifest` (proposal §3)."""
    _resolve_root()
    svc = services(core)
    svc.require_mode_ready()
    run_id = match.group(1)
    _require_run_id(run_id)
    _no_query(request, "run manifest")
    record = _locate(svc, principal, run_id)
    manifest = _load_manifest(record.path.parent, record.stem)
    projects = _manifest_projects(manifest)
    if not _run_visible(svc, principal, projects=projects):
        raise errors.NotFound("validation_run", run_id)

    if manifest.status == "corrupt" and svc.mode == scope_module.SINGLE_USER:
        raise errors.ValidationManifestCorrupt(
            manifest.rule or "the bundle manifest is unusable",
            run_id=run_id, rule=manifest.rule)
    mode, basis = _effective_mode(manifest)
    if manifest.status == "absent":
        return Response(200, {
            "status": "absent",
            "reason": manifest.reason,
            "run_id": run_id,
            "mode": mode,
            "mode_basis": basis,
            "projects": [],
            "sources": {},
            "files": [],
            "manifest_sha256": manifest.sha256,
            "manifest_authentication": "unauthenticated",
        })
    if manifest.status == "corrupt":
        return Response(200, {
            "status": "corrupt",
            "rule": manifest.rule,
            "run_id": run_id,
            "mode": mode,
            "mode_basis": basis,
            "projects": [],
            "sources": {},
            "files": [],
            "manifest_sha256": manifest.sha256,
            "manifest_authentication": "unauthenticated",
        })
    data = manifest.data or {}
    files = [_manifest_file(record.path.parent, entry) for entry in data.get("files", [])]
    return Response(200, {
        "status": "frozen",
        "run_id": run_id,
        "frozen_at": data.get("frozen_at"),
        "declared_mode": data.get("mode"),
        "mode": mode,
        "mode_basis": basis,
        "projects": _manifest_projects(manifest),
        "sources": data.get("sources"),
        "files": files,
        "manifest_sha256": manifest.sha256,
        "manifest_authentication": "unauthenticated",
    })


def get_artifact(request: Request, core: "Core", principal: auth.Principal,
                 match: re.Match[str]) -> Response:
    """`GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}` (proposal §5)."""
    _resolve_root()
    svc = services(core)
    svc.require_mode_ready()
    run_id, artifact_id = match.group(1), match.group(2)
    _require_run_id(run_id)
    _require_artifact_id(artifact_id)
    _no_query(request, "artifact metadata")
    record = _locate(svc, principal, run_id)
    manifest, entry = _require_artifact(svc, principal, record, artifact_id, run_id)

    declared_sha = entry["sha256"]
    declared_bytes = entry["bytes"]
    read = _read_artifact(record.path.parent, entry["path"], declared_sha, declared_bytes,
                          MAX_ARTIFACT_BYTES)
    _raise_artifact_error(read, artifact_id)
    if read.integrity == "mismatch":
        raise errors.ArtifactModified(
            "artifact bytes disagree with the manifest", artifact_id=artifact_id,
            declared_sha256=declared_sha, declared_bytes=declared_bytes,
            actual_sha256=hashlib.sha256(read.buffer or b"").hexdigest(),
            actual_bytes=read.actual_bytes)
    buffer = read.buffer or b""
    scan = _content_scan(buffer)
    forms = ["masked"]
    original_allowed = (read.integrity == "match" and entry.get("credential_shaped") is not True
                        and _decode(buffer) is not None
                        and canonical.redact_text(_decode(buffer) or "") == _decode(buffer))
    if original_allowed:
        forms.append("original")
    return Response(200, {
        "artifact_id": artifact_id,
        "path": entry["path"],
        "bytes": read.actual_bytes,
        "declared_sha256": declared_sha,
        "declared_bytes": declared_bytes,
        "integrity": read.integrity,
        "media_type": entry.get("media_type"),
        "forms": forms,
        "digests": {"original": hashlib.sha256(buffer).hexdigest(),
                    "masked": _masked_digest(buffer)},
        "credential_shaped": entry.get("credential_shaped"),
        "content_scan": scan,
    })


def get_artifact_content(request: Request, core: "Core", principal: auth.Principal,
                         match: re.Match[str]) -> Response:
    """`GET /v1/validation/runs/{run_id}/artifacts/{artifact_id}/content` (proposal §5)."""
    _resolve_root()
    svc = services(core)
    svc.require_mode_ready()
    run_id, artifact_id = match.group(1), match.group(2)
    _require_run_id(run_id)
    _require_artifact_id(artifact_id)
    for key in request.query:
        if key not in ("form", "download"):
            raise errors.InvalidRequest("unknown query parameter", field=key)
        if len(request.query[key]) != 1:
            raise errors.InvalidRequest("duplicate query parameter", field=key)
    form = request.param("form") or "masked"
    if form not in ("masked", "original"):
        raise errors.InvalidRequest("form must be masked or original", field="form", value=form)
    download = request.param("download")
    if download is not None and download != "1":
        raise errors.InvalidRequest("download must be 1", field="download", value=download)
    record = _locate(svc, principal, run_id)
    manifest, entry = _require_artifact(svc, principal, record, artifact_id, run_id)

    declared_sha = entry["sha256"]
    declared_bytes = entry["bytes"]
    if not _text_media(entry.get("media_type")):
        raise errors.UnsupportedMediaType(
            "only text artifacts have a content form",
            media_type=entry.get("media_type"), artifact_id=artifact_id)
    read = _read_artifact(record.path.parent, entry["path"], declared_sha, declared_bytes,
                          MAX_ARTIFACT_BYTES)
    _raise_artifact_error(read, artifact_id)
    if read.integrity == "mismatch":
        raise errors.ArtifactModified(
            "artifact bytes disagree with the manifest", artifact_id=artifact_id,
            declared_sha256=declared_sha, declared_bytes=declared_bytes,
            actual_sha256=hashlib.sha256(read.buffer or b"").hexdigest(),
            actual_bytes=read.actual_bytes)
    buffer = read.buffer or b""
    original_sha = hashlib.sha256(buffer).hexdigest()
    text = _decode(buffer)
    masked_sha = _masked_digest(buffer)
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Validation-Form": form,
        "X-Validation-Sha256": "",
        "X-Validation-Original-Sha256": declared_sha,
    }
    if download == "1":
        headers["Content-Disposition"] = f'attachment; filename="{artifact_id}.txt"'
    if form == "original":
        reason = None
        if entry.get("credential_shaped") is True:
            reason = "credential_shaped_declared"
        elif text is None:
            reason = "not_utf8"
        elif canonical.redact_text(text) != text:
            reason = "mask_not_noop"
        if reason is not None:
            raise errors.OriginalWithheld(
                "the original form is withheld because the masker is not a no-op",
                reason=reason, masked_sha256=masked_sha)
        served = buffer
        headers["X-Validation-Sha256"] = original_sha
        return Response(200, {
            "artifact_id": artifact_id,
            "form": "original",
            "media_type": entry.get("media_type"),
            "bytes": len(served),
            "served_sha256": original_sha,
            "original_sha256": original_sha,
            "masked_sha256": masked_sha,
            "content": text,
            "content_is_original": True,
        }, headers)
    if text is None:
        # A text/* artifact whose bytes are not strict UTF-8 cannot be served as
        # text at all; masked output would silently corrupt it.
        raise errors.UnsupportedMediaType(
            "the artifact bytes are not strict UTF-8", artifact_id=artifact_id, reason="not_utf8")
    masked_text = canonical.redact_text(text)
    served = masked_text.encode("utf-8")
    served_sha = hashlib.sha256(served).hexdigest()
    headers["X-Validation-Sha256"] = served_sha
    return Response(200, {
        "artifact_id": artifact_id,
        "form": "masked",
        "media_type": entry.get("media_type"),
        "bytes": len(served),
        "served_sha256": served_sha,
        "original_sha256": original_sha,
        "masked_sha256": masked_sha,
        "mask_algorithm": "canonical.redact_text",
        "mask_version": canonical.REDACT_TEXT_VERSION,
        "content": masked_text,
        "content_is_original": False,
    }, headers)


# -- shared handler helpers ---------------------------------------------------


def _locate(svc: ValidationServices, principal: auth.Principal, run_id: str) -> _Record:
    """Resolve the id to exactly one visible, parseable record.

    The id grammar is validated by the caller before this is reached, so a
    malformed id never opens a record file. In `acl` mode a run the caller may
    not read is reported exactly like a run that does not exist.
    """
    acl = svc.mode == scope_module.ACL
    explicit_unattributed = "validation:unattributed" in principal.scopes
    root = _resolve_root()
    walk = _walk(root, stop_on_caps=False)
    matches = _records_for_stem(walk, run_id)
    if not matches:
        if walk.unreadable:
            raise _validation_not_configured(
                "index_incomplete", counts=_counts(walk),
                samples=[{"path": path, "error": error}
                         for path, error in walk.unreadable[:UNREADABLE_SAMPLE_CAP]])
        if walk.capped:
            raise errors.ValidationIndexTooLarge(
                "the run index exceeds a walk cap", cap=walk.cap_name)
        raise errors.NotFound("validation_run", run_id)
    # F1: exactly the same predicate `_mark_duplicates` uses. A stem is
    # ambiguous only when MORE THAN ONE parseable record claims it; a corrupt
    # sibling does not make the parseable record unreadable, and a stem with no
    # parseable member is corrupt rather than ambiguous. Without this the list
    # could certify a row as `ok` and the detail route then refuse it.
    parseable = [record for record in matches if record.status != "corrupt"]
    if len(parseable) > 1:
        if acl and not explicit_unattributed:
            raise errors.NotFound("validation_run", run_id)
        raise errors.ValidationRunAmbiguous(
            f"{len(parseable)} run-shaped files share this stem", run_id=run_id)
    if not parseable:
        if acl and not explicit_unattributed:
            raise errors.NotFound("validation_run", run_id)
        raise errors.ValidationRunCorrupt(
            matches[0].corrupt_reason or "the run record is unusable", run_id=run_id)
    record = parseable[0]
    manifest = _load_manifest(record.path.parent, record.stem)
    if not _run_visible(svc, principal, projects=_manifest_projects(manifest)):
        raise errors.NotFound("validation_run", run_id)
    return record


def _not_found(kind: str, ident: str, reason: str) -> errors.NotFound:
    """`NotFound` has no `details` argument, so the reason is attached here.

    The `404 artifact_not_found` code must stay identical across every
    not-addressable case; only `details.reason` distinguishes them.
    """
    error = errors.NotFound(kind, ident)
    error.details["reason"] = reason
    return error


def _require_artifact(svc: ValidationServices, principal: auth.Principal, record: _Record,
                      artifact_id: str, run_id: str) -> tuple[_Manifest, dict[str, Any]]:
    manifest = _load_manifest(record.path.parent, record.stem)
    if manifest.status != "frozen":
        raise _not_found("artifact", artifact_id,
                         "manifest_absent" if manifest.status == "absent" else "manifest_corrupt")
    entry = next((item for item in manifest.data.get("files", [])
                  if item.get("artifact_id") == artifact_id), None)
    if entry is None:
        raise _not_found("artifact", artifact_id, "unknown_artifact_id")
    return manifest, entry


def _raise_artifact_error(read: _ArtifactRead, artifact_id: str) -> None:
    if read.error == "too_large":
        raise errors.PayloadTooLarge(
            f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes", limit=MAX_ARTIFACT_BYTES)
    if read.error in ("symlink_refused", "missing"):
        raise _not_found("artifact", artifact_id, read.error)
    if read.error == "unreadable":
        raise _validation_not_configured("artifact_unreadable")


def _manifest_file(run_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    read = _read_artifact(run_dir, entry.get("path", ""), entry.get("sha256", ""),
                          entry.get("bytes", -1), MAX_ARTIFACT_BYTES)
    return {**entry, "integrity": read.integrity,
            "bytes_agree": read.integrity == "match"}


def _row(record: _Record, manifest: _Manifest | None, mode: str | None,
         basis: str | None, projects: list[str]) -> dict[str, Any]:
    data = record.data
    if record.status == "ok" and data is not None:
        cases = data.get("cases") if isinstance(data.get("cases"), list) else []
        grades = [_case_grade(data, case, mode)[0] if isinstance(case, dict) else "unknown"
                  for case in cases]
        downgraded = []
        if mode in ("real", "component", "simulated"):
            downgraded = [case.get("case_id") for case, grade in zip(cases, grades)
                          if TRUST[grade] > TRUST[mode]]
        row: dict[str, Any] = {
            "run_id": data.get("run_id"),
            "stage": data.get("stage"),
            "created_at": data.get("created_at"),
            "commit": data.get("commit"),
            "schema_version": data.get("schema_version"),
            "core_version": data.get("core_version"),
            "verdict": data.get("verdict"),
            "case_counts": _case_counts(cases),
            "mode": mode,
            "mode_basis": basis,
            "attribution": _attribution(manifest) if manifest is not None else None,
            "evidence_grade": _worst(mode, *grades) if grades else mode,
            "grade_downgraded_by": downgraded,
            "synthetic": data.get("synthetic") is True,
            "manifest_status": manifest.status if manifest is not None else None,
            "record_status": "ok",
            "record_sha256": record.record_sha256,
            "verdict_consistent": data.get("verdict") == _verdict_of(cases),
            **({"synthetic_note": data["synthetic_note"]} if "synthetic_note" in data else {}),
        }
        return row
    row = {
        "run_id": record.stem,
        "stage": data.get("stage") if data else None,
        "created_at": data.get("created_at") if data else None,
        "commit": data.get("commit") if data else None,
        "schema_version": data.get("schema_version") if data else None,
        "core_version": data.get("core_version") if data else None,
        "verdict": data.get("verdict") if data else None,
        "case_counts": _case_counts(data.get("cases") if data and isinstance(data.get("cases"), list)
                                    else []),
        "mode": None,
        "mode_basis": None,
        "attribution": None,
        "evidence_grade": None,
        "grade_downgraded_by": [],
        "synthetic": bool(data.get("synthetic")) if data else False,
        "manifest_status": None,
        "record_status": record.status,
        "record_sha256": record.record_sha256,
        "verdict_consistent": None,
    }
    if record.corrupt_reason is not None:
        row["corrupt_reason"] = record.corrupt_reason
    if record.duplicate_reason is not None:
        row["duplicate_reason"] = record.duplicate_reason
    if data and "synthetic_note" in data:
        row["synthetic_note"] = data["synthetic_note"]
    return row


def _list_params(request: Request) -> dict[str, Any]:
    allowed = {"limit", "offset", "verdict", "stage", "mode", "attribution", "project"}
    for key, values in request.query.items():
        if key not in allowed:
            raise errors.InvalidRequest("unknown query parameter", field=key)
        if len(values) != 1:
            raise errors.InvalidRequest("duplicate query parameter", field=key)
    limit = _int_param(request, "limit", LIMIT_DEFAULT)
    offset = _int_param(request, "offset", 0)
    if limit < 0 or limit > LIMIT_MAX:
        raise errors.InvalidRequest(f"limit must be between 0 and {LIMIT_MAX}",
                                    field="limit", value=limit)
    if offset < 0:
        raise errors.InvalidRequest("offset must be non-negative", field="offset", value=offset)
    verdict = request.param("verdict")
    if verdict is not None and verdict not in VERDICTS:
        raise errors.InvalidRequest("verdict must be one of PASS, FAIL, BLOCKED",
                                    field="verdict", value=verdict)
    stage = request.param("stage")
    if stage is not None and not stage:
        raise errors.InvalidRequest("stage must be non-empty", field="stage", value=stage)
    mode = request.param("mode")
    if mode is not None and mode not in TRUST:
        raise errors.InvalidRequest("mode must be one of real, component, simulated, unknown",
                                    field="mode", value=mode)
    attribution = request.param("attribution")
    if attribution is not None and attribution not in ("attributed", "unattributed"):
        raise errors.InvalidRequest("attribution must be attributed or unattributed",
                                    field="attribution", value=attribution)
    project = request.param("project")
    if project is not None and not ids.is_id(project, "prj"):
        raise errors.InvalidRequest("project must be a prj_ id", field="project", value=project)
    return {"limit": limit, "offset": offset, "verdict": verdict, "stage": stage,
            "mode": mode, "attribution": attribution, "project": project}


def _matches(entry: tuple[_Record, _Manifest | None, str, str | None, list[str]],
             params: dict[str, Any]) -> bool:
    record, manifest, mode, basis, projects = entry
    del manifest, basis
    if params["verdict"] is not None and record.data is not None:
        if record.data.get("verdict") != params["verdict"]:
            return False
    elif params["verdict"] is not None:
        return False
    if params["stage"] is not None:
        if record.data is None or record.data.get("stage") != params["stage"]:
            return False
    if params["mode"] is not None:
        if mode != params["mode"]:
            return False
    if params["attribution"] is not None:
        if (record.data is None or record.status != "ok"):
            return False
        if mode is None:
            return False
        status = "attributed" if projects else "unattributed"
        if status != params["attribution"]:
            return False
    if params["project"] is not None:
        if params["project"] not in projects:
            return False
    return True


def _order(records: list[_Record]) -> list[_Record]:
    """The single §2 ordering tuple, applied as stable passes.

    `(status_rank asc, created_at_key desc, run_id_key desc, relative_path asc)`
    with `created_at_key = ""` for a corrupt row, so corrupt rows sort last and
    can never interleave.
    """
    rank = {"ok": 0, "duplicate": 1, "corrupt": 2}
    ordered = sorted(records, key=lambda record: record.relative_path)
    ordered = sorted(ordered, key=lambda record: record.stem, reverse=True)
    ordered = sorted(ordered,
                     key=lambda record: (record.data.get("created_at", "")
                                         if record.data is not None else ""),
                     reverse=True)
    ordered = sorted(ordered, key=lambda record: rank[record.status])
    return ordered


# -- routes -------------------------------------------------------------------


def routes(route_cls) -> tuple:
    """The five P7-01 read routes, built from the caller's `Route` class.

    Built on demand rather than at import so this module has no import-time
    dependency on `api.handlers`; `handlers` calls this once while assembling its
    table, exactly as it does for `memory.api.routes`.
    """
    return (
        route_cls("GET", re.compile(r"^/v1/validation/runs$"), list_runs, "validation:read"),
        route_cls("GET", re.compile(r"^/v1/validation/runs/([^/]+)$"), get_run,
                  "validation:read"),
        route_cls("GET", re.compile(r"^/v1/validation/runs/([^/]+)/manifest$"), get_manifest,
                  "validation:read"),
        route_cls("GET", re.compile(r"^/v1/validation/runs/([^/]+)/artifacts/([^/]+)$"),
                  get_artifact, "validation:read"),
        route_cls("GET",
                  re.compile(r"^/v1/validation/runs/([^/]+)/artifacts/([^/]+)/content$"),
                  get_artifact_content, "validation:read"),
    )
