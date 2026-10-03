"""Ingestion-time secret filter and recall-time injection guard.

Design §12: the secret filter runs *before* Evidence/Memory ingestion, and a
password, token or private key must never enter Hindsight. This module is the
only place that decides what happens to content on its way into the bank, and
it is deliberately separate from `memory.adapter` so the policy can be reviewed
without reading the transport.

The policy frozen in ADR 0011 §5, in one line each:

* **reject** the content when it carries credential material we cannot redact
  without destroying the lesson (a private key block, or a literal provider
  token). The source Raw Event is untouched and remains readable under
  `events:read`; nothing about it reaches the engine.
* **redact** assignment-shaped secrets (`password=...`, `bearer_token: "..."`)
  and keep the surrounding prose, because "the deploy failed because the
  staging token expired" is a usable lesson once the token is `[REDACTED]`.
* **allow** everything else, and record the finding so the Inspector can show
  what was changed and why.

The second half of the module is the recall-side guard. Memory is LLM-extracted
text from earlier conversations, so a retained item can contain text that reads
like an instruction. Two defences, both applied to every engine result before it
is counted against the budget or rendered:

* `neutralize_markers` breaks the Context Pack section delimiters, so a retained
  item can never close the Memory section and open a fake Authority section.
* `detect_directive_attempt` drops the whole item when the text tries to
  *instruct* the reader. A memory is not a rule; the only way a memory becomes
  a rule is an Authority lifecycle command, which this module cannot perform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .. import canonical
from .types import CONTEXT_SECTION_MARKERS

REDACTED = canonical.REDACTED

#: Actions, most severe first. `reject` means nothing derived from this content
#: is handed to the engine; the Raw Event keeps the original text.
ACTIONS = ("reject", "redact", "allow")

#: Shapes we refuse outright. `looks_like_credential` covers the provider token
#: and JWT families; a PEM block is matched separately because truncating the
#: middle of a private key leaves a valid-looking prefix and a valid-looking
#: suffix, and both invite a later copy-paste.
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

#: Assignment-shaped secrets are redacted rather than rejected. The names mirror
#: `canonical._SECRET_NAME` so the audit log and the memory filter agree on what
#: a secret looks like.
#: Case-insensitive, deliberately. Environment variables and config keys are
#: conventionally upper case (`DB_PASSWORD`, `API_KEY`, `GITHUB_TOKEN`), and
#: `canonical.redact_text` — the log filter on this same codebase — is not
#: case-insensitive, so `DB_PASSWORD=…` passed both. The engine is a different
#: reader from a log file and a different reader from a recall, so the stricter
#: of the two behaviours is the one used here.
_NAME = (r"(?i)(?P<name>(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[-_.])*"
         r"(?:password|passwd|apikey|api[-_]?key|secret|access[-_]?token|refresh[-_]?token"
         r"|auth[-_]?token|token|client[-_]?secret|private[-_]?key|authorization|bearer|credential))")
#: Quoted: `password="…"` in JSON, YAML, an env dump or a log line.
_ASSIGNMENT_RE = re.compile(_NAME + r"(?P<sep>[\"']?\s*[:=]\s*)(?P<q>[\"'`])(?P<value>[^\"'`]*?)(?P=q)")
#: Bare: `PASSWORD=hunter2` in a shell command, a `.env` file or a `curl -d`
#: body. This is the form real captured text takes, and matching only the quoted
#: form let `DB_PASSWORD=s3cr3t-prod` through to the engine verbatim — while
#: `canonical.redact_text`, used for logs, already handled both.
_BARE_ASSIGNMENT_RE = re.compile(
    _NAME + r"(?P<sep>[\"']?\s*[:=]\s*)(?P<q>)(?P<value>[^\s,;)}\]\"'`]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer(?:%20|%09|\+|\s)+(?!\$\{?\$?)[A-Za-z0-9._~%+-]{8,}")

#: Longest error/URL fragments we keep in a safe summary. A `safe_error_summary`
#: is stored in SQLite, returned by the API and printed in a manifest, so it is
#: both clipped and passed through `redact_text` before it leaves this module.
SUMMARY_LIMIT = 200

# -- recall-side injection guard ----------------------------------------------
# These are phrases that only make sense in an instruction to a model. A lesson
# that contains one is quoted history, not a lesson, and the failure mode of
# keeping it (a memory that tells the next turn to ignore a HARD Rule) is far
# worse than the failure mode of dropping it (a slightly thinner lesson).
_DIRECTIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions",
     re.compile(r"(?i)\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b"
                r"(?:previous|prior|earlier|above|all|any|hard|the)\b[^.\n]{0,20}\b"
                r"(?:instruction|instructions|rule|rules|constraint|constraints|authority)\b")),
    ("rule_not_binding",
     re.compile(r"(?i)\b(?:no longer|not)\b[^.\n]{0,30}\b(?:bound|obligated|required)\b"
                r"|\byou\s+(?:are|may|can)\s+now\s+ignore\b")),
    ("bypass_guard",
     re.compile(r"(?i)\b(?:bypass|skip|circumvent|disable|turn\s+off)\b[^.\n]{0,30}\b"
                r"(?:hard\s+rule|rules?|guard|deny|check|policy|policies)\b")),
    ("claims_authority",
     re.compile(r"(?i)\bthis\s+(?:memory|note|lesson|fact|record|entry)\b[^.\n]{0,30}\b"
                r"(?:is|are|supersedes|overrides|replaces|amends)\b[^.\n]{0,20}\b"
                r"(?:the\s+)?(?:current\s+|active\s+)?(?:authority|decision|rule|state)\b")),
    ("fabricated_verification",
     re.compile(r"(?i)\b(?:verified|verified\s+as|confirmed)\b[^.\n]{0,20}\bpass\b")),
    ("elevates_memory",
     re.compile(r"(?i)\b(?:treat|consider|regard)\s+(?:this|these)\b[^.\n]{0,30}\b"
                r"(?:as\s+)?(?:a\s+)?(?:hard\s+)?(?:rule|requirement|mandatory|constraint)\b")),
)

# A self-contained pattern needs no capturing group, so this one is kept
# separate from the tuple above rather than shoehorned into it.
_FABRICATED_VERIFICATION_RE = re.compile(
    r"(?i)\bno\s+need\s+to\s+(?:check|verify|test|re-?run)\b"
    r"|\balready\s+verified\b(?!\s+(?:in|by)\b)"
)


@dataclass(frozen=True)
class Finding:
    """One reason the content changed, in a form an Inspector can group by."""

    rule: str
    action: str          # "reject" | "redact" | "flag"
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "action": self.action, "detail": self.detail}


@dataclass(frozen=True)
class Sanitized:
    """The content that may be handed to the engine, plus why it is that content."""

    text: str
    action: str          # "allow" | "redact" | "reject"
    findings: tuple[Finding, ...]
    #: SHA-256 of the *sanitised* text. This is the `content_sha256` stored on
    #: the Memory item and the job, so "did the content change between the
    #: enqueue check and the retain?" is answerable without keeping two copies.
    sha256: str

    @property
    def rejected(self) -> bool:
        return self.action == "reject"

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "sha256": self.sha256,
                "findings": [f.as_dict() for f in self.findings]}


def sanitize(text: str) -> Sanitized:
    """Apply the frozen ingestion policy to text bound for the engine."""
    if not isinstance(text, str):
        raise TypeError("sanitize expects text")
    findings: list[Finding] = []
    action = "allow"

    if _PEM_RE.search(text):
        # A private key cannot be partially redacted into something safe to
        # retain, so nothing derived from this content is retained.
        findings.append(Finding("private_key_block", "reject",
                                "the content contains a private key block"))
        return Sanitized("", "reject", tuple(findings), canonical.sha256_hex(""))

    if canonical.looks_like_credential(text):
        findings.append(Finding("literal_credential", "reject",
                                "the content contains credential-shaped material"))
        return Sanitized("", "reject", tuple(findings), canonical.sha256_hex(""))

    masked: list[int] = []

    def _repl(match: re.Match[str]) -> str:
        if _is_placeholder(match.group("value")):
            return match.group(0)
        masked.append(1)
        return _mask(match)

    redacted = _BARE_ASSIGNMENT_RE.sub(_repl, text)
    quoted = _ASSIGNMENT_RE.sub(_repl, redacted)
    count = len(masked)
    if count:
        findings.append(Finding("secret_assignment", "redact",
                                f"{count} secret-shaped assignment(s) replaced with {REDACTED}"))
        action = "redact"
    bearer_count = len(_BEARER_RE.findall(quoted))
    if bearer_count:
        quoted = canonical.redact_text(quoted)
        findings.append(Finding("bearer_credential", "redact",
                                f"{bearer_count} bearer credential(s) replaced with {REDACTED}"))
        action = "redact"

    return Sanitized(quoted, action, tuple(findings), canonical.sha256_hex(quoted))


#: Values that are documentation, not secrets. `$VAR` and `${VAR}` are how a
#: reproduction step is written, and `<...>` / `...` / a run of the same
#: character is how a human elides one. Redacting them would replace the one
#: part of the sentence that says "this came from the environment" with nothing,
#: while stopping no leak -- the same reasoning `canonical.looks_like_credential`
#: already applies to bearer credentials.
_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$[A-Za-z_][A-Za-z0-9_]*"
    r"|<[^>]*>|\.{3,}|x{3,}|\*{3,}|%[A-Za-z0-9_]+%|REDACTED)\s*$"
)


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value))


def _mask(match: re.Match[str]) -> str:
    """Replace only the value, so the key name and separators survive."""
    quote = match.group("q")
    name = match.group("name")
    sep = match.group("sep")
    return f"{name}{sep}{quote}{REDACTED}{quote}"


def directive_reason(text: str) -> str | None:
    """The injection rule `text` trips, or None if it reads as history."""
    for name, pattern in _DIRECTIVE_PATTERNS:
        if pattern.search(text):
            return name
    if _FABRICATED_VERIFICATION_RE.search(text):
        return "fabricated_verification"
    return None


def neutralize_markers(text: str) -> tuple[str, bool]:
    """Break Context Pack section delimiters in engine text.

    Returns `(text, had_marker)`. A retained item containing a section marker is
    also dropped by the caller with reason `section_marker`; this function is the
    second line of defence, for the case where a caller chooses to keep it.
    """
    had = any(marker in text for marker in CONTEXT_SECTION_MARKERS)
    if not had:
        return text, False
    # `[` -> `(` keeps the text readable while making it inert as a delimiter.
    return text.replace("[", "(").replace("]", ")"), True


def safe_summary(message: object, *, limit: int = SUMMARY_LIMIT) -> str:
    """A redacted, clipped, single-line summary safe to store and return.

    Engine error bodies are attacker-influenced and routinely echo the request
    that produced them, so this is applied to every remote failure before the
    string reaches SQLite, an HTTP response, a manifest or a log line.
    """
    if message is None:
        return ""
    text = message if isinstance(message, str) else str(message)
    text = canonical.redact_text(text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text
