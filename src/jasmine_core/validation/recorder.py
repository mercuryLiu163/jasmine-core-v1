"""The P0 Validation Recorder.

This is an *acceptance record*, not a product Evidence object. The full
Recorder and Scenario Runner are P7 (see ADR 0004 §1.4); P0 needs the minimum
that the taskbook demands: every case has USER RAW, AGENT RESPONSE, TOOLS,
STATE BEFORE/AFTER, Interpretation, Context and Verdict, with the P0-absent
stages marked `N/A (P0 not implemented)` and a reason, never a fabricated PASS.

A run is written to a directory that `.gitignore` excludes. It must never
contain a token, a key, a private conversation, or the database itself.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import SCHEMA_VERSION, __version__

VERDICTS = ("PASS", "FAIL", "BLOCKED")
SECTIONS = (
    "user_raw",
    "agent_response",
    "tools",
    "state_before",
    "state_after",
    "interpretation",
    "context",
)

FORBIDDEN_KEYS = ("token", "secret", "password", "api_key", "authorization", "private_key")
NOT_IMPLEMENTED = "N/A (P0 not implemented)"

#: Shapes that indicate real credential material. A shell placeholder such as
#: `$READER` or `${TOKEN}` in a reproduction step is documentation, not a leak,
#: so it is deliberately not in this list: refusing it would make an honest
#: bundle impossible to write.
_LITERAL_CREDENTIAL_RES = (
    re.compile(r"(?i)\bbearer\s+(?!\$\{?\$?[A-Za-z_])[A-Za-z0-9._-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def looks_like_credential(text: str) -> bool:
    return any(pattern.search(text) for pattern in _LITERAL_CREDENTIAL_RES)


@dataclass
class Case:
    """One acceptance case with a verdict a human can audit."""

    case_id: str
    name: str
    verdict: str = "BLOCKED"
    user_raw: Any = NOT_IMPLEMENTED
    agent_response: Any = NOT_IMPLEMENTED
    tools: list[dict[str, Any]] = field(default_factory=list)
    state_before: Any = NOT_IMPLEMENTED
    state_after: Any = NOT_IMPLEMENTED
    interpretation: Any = NOT_IMPLEMENTED
    context: Any = NOT_IMPLEMENTED
    failure_class: str | None = None
    failure_output: str | None = None
    reproduction: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    not_applied_reason: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if self.verdict == "FAIL" and not (self.failure_class and self.failure_output):
            # A FAIL without a failure class and raw output is not auditable.
            raise ValueError("a FAIL case needs failure_class and failure_output")
        if self.verdict == "BLOCKED" and not self.not_applied_reason:
            raise ValueError("a BLOCKED case must say why it did not run")


@dataclass
class Run:
    """A Validation Run: the bound commit, the environment, the cases."""

    run_id: str
    stage: str
    created_at: str
    commit: str
    schema_version: int
    core_version: str
    environment: dict[str, Any]
    executor: str
    reviewer: str
    verifier: str
    cases: list[Case] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    not_implemented: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Worst case wins, and an unrun case is never quietly a pass."""
        if any(case.verdict == "FAIL" for case in self.cases):
            return "FAIL"
        if any(case.verdict == "BLOCKED" for case in self.cases):
            return "BLOCKED"
        return "PASS"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict
        return payload


def current_commit(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def environment() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "sqlite3": __import__("sqlite3").sqlite_version,
    }


def assert_no_secrets(payload: Any, path: str = "run") -> None:
    """Refuse to write a bundle that contains real credential material.

    Two things are rejected: a field *named* like a credential, and a string that
    contains something shaped like an actual secret. Text that merely refers to
    credentials by name -- a reproduction step using `$TOKEN` -- is allowed,
    because refusing it would make a usable bundle impossible to write while
    stopping none of the real leaks.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(needle in lowered for needle in FORBIDDEN_KEYS):
                raise ValueError(
                    f"{path}.{key} is named like a credential field; refusing to write it"
                )
            assert_no_secrets(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            assert_no_secrets(item, f"{path}[{index}]")
    elif isinstance(payload, str) and looks_like_credential(payload):
        raise ValueError(f"{path} contains what looks like credential material; refusing to write it")


class Recorder:
    def __init__(self, output_dir: Path, *, stage: str = "P0", executor: str = "unassigned",
                 reviewer: str = "unassigned", verifier: str = "unassigned",
                 repo: Path | None = None) -> None:
        self.output_dir = Path(output_dir)
        self.repo = repo or Path.cwd()
        self.run = Run(
            run_id=datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ"),
            stage=stage,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            commit=current_commit(self.repo),
            schema_version=SCHEMA_VERSION,
            core_version=__version__,
            environment=environment(),
            executor=executor,
            reviewer=reviewer,
            verifier=verifier,
        )

    def add(self, case: Case) -> None:
        self.run.cases.append(case)

    def note(self, text: str) -> None:
        self.run.notes.append(text)

    def not_implemented(self, capability: str) -> None:
        self.run.not_implemented.append(capability)

    def write(self) -> Path:
        payload = self.run.to_dict()
        assert_no_secrets(payload)
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.output_dir / f"{self.run.run_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path
