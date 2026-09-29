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
from ..canonical import looks_like_credential

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

#: Field names that always hold credential material. Matched on the *normalised*
#: whole name, not as a substring: `api_key_rows` is a row count and `token_count`
#: is a statistic, and a substring rule would reject honest evidence while
#: stopping no real leak. The value-level rules below are what actually catch
#: material.
FORBIDDEN_FIELD_NAMES = frozenset({
    "token", "tokens", "accesstoken", "refreshtoken", "idtoken", "bearertoken",
    "apikey", "apikeys", "password", "passwd", "secret", "secrets", "clientsecret",
    "authorization", "privatekey", "credential", "credentials", "cookie", "sessionkey",
})
NOT_IMPLEMENTED = "N/A (P0 not implemented)"


def _normalise_field(name: str) -> str:
    return "".join(char for char in str(name).lower() if char.isalnum())



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


WITHHELD = "[REDACTED: withheld from the evidence bundle]"


def looks_like_credential_field(name: str) -> bool:
    return _normalise_field(name) in FORBIDDEN_FIELD_NAMES


class Redaction:
    """One thing the guard removed, named so a reviewer can go looking for it."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason, "value": WITHHELD}


def sanitise_secrets(payload: Any, path: str = "run",
                     found: list[Redaction] | None = None) -> Any:
    """Remove credential material, recording where it was found.

    This deliberately does not raise. A bundle that refuses to exist because the
    product leaked a token into a response throws away the one piece of evidence
    that matters. Instead the material is replaced, the location is recorded in
    `redactions`, and the acceptance runner treats any redaction as a failure to
    investigate -- so a leak is disclosed rather than either hidden or fatal.
    """
    found = [] if found is None else found
    if isinstance(payload, dict):
        return {
            key: (found.append(Redaction(f"{path}.{key}", "credential-shaped field name"))
                  or WITHHELD)
            if looks_like_credential_field(key) else sanitise_secrets(value, f"{path}.{key}", found)
            for key, value in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        return [sanitise_secrets(item, f"{path}[{index}]", found)
                for index, item in enumerate(payload)]
    if isinstance(payload, str) and looks_like_credential(payload):
        found.append(Redaction(path, "credential-shaped value"))
        return WITHHELD
    return payload


def assert_no_secrets(payload: Any, path: str = "run") -> None:
    """Raise if `payload` holds credential material.

    Kept as a strict check for callers that want refusal semantics; the
    recorder itself uses :func:`sanitise_secrets`.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if looks_like_credential_field(key):
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
        self.redactions: list[Redaction] = []

    def add(self, case: Case) -> None:
        self.run.cases.append(case)

    def note(self, text: str) -> None:
        self.run.notes.append(text)

    def not_implemented(self, capability: str) -> None:
        self.run.not_implemented.append(capability)

    def write(self) -> Path:
        payload = self.run.to_dict()
        clean = sanitise_secrets(payload, found=self.redactions)
        clean["redactions"] = [item.to_dict() for item in self.redactions]
        if self.redactions:
            # Say so in the summary, not only in a field: a bundle that had to
            # remove something is not a bundle to sign off without reading.
            clean["redaction_warning"] = (
                f"{len(self.redactions)} value(s) contained credential material and were "
                "withheld. This means the product put a secret into a recorded response."
            )
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.output_dir / f"{self.run.run_id}.json"
        path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path
