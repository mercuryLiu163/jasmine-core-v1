"""The engine boundary (ADR 0009 §2).

`MemoryTransport` is the whole of what P4 knows about Hindsight. Everything above
it — banks, provenance, jobs, lifecycle, the API — is written against this
protocol, which is what makes "the engine is replaceable" (design §2) true
rather than aspirational: a second engine needs one new module, not a rewrite.

The protocol is a real interface, not a comment. A `FakeTransport` in the test
suite satisfies it, and the recall/filter/provenance logic is exercised against
it as a *boundary* check; a fake can never stand in for the real service as
acceptance evidence, which is why the real path is also tested over a real HTTP
socket with a stub server speaking the real wire format.

Two protocol properties are load-bearing and are asserted in the tests:

* No method may be called while the caller holds a SQLite write transaction.
  `memory.worker` releases its transaction before calling `retain`, and this
  module has no database access at all, so the guarantee is structural rather
  than a convention someone has to remember.
* Failures arrive as `TransportError` with a stable `code`, never as a raw
  exception. A remote error must not become a Core 500, and must never be
  mapped onto "Truth is unavailable" (design §14: a dead Hindsight pauses
  Memory, nothing else).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .types import BankRef, EngineResult


class TransportError(Exception):
    """A remote failure, reduced to a stable code plus a safe summary.

    `code` is the branchable part. `safe_error_summary` is redacted and clipped
    by `memory.filtering.safe_summary` and is the only part allowed into a
    database row, an API response, a manifest or a log. The original exception
    stays on `__cause__` for a local traceback and is never rendered.
    """

    def __init__(self, code: str, summary: str = "", *, retryable: bool = False,
                 http_status: int | None = None) -> None:
        super().__init__(f"{code}: {summary}" if summary else code)
        self.code = code
        self.safe_error_summary = summary
        self.retryable = retryable
        self.http_status = http_status

    def as_dict(self) -> dict[str, Any]:
        return {"error_code": self.code, "safe_error_summary": self.safe_error_summary,
                "retryable": self.retryable, "http_status": self.http_status}


@runtime_checkable
class MemoryTransport(Protocol):
    """Engine operations P4 needs, and nothing else."""

    engine_id: str

    def version(self) -> dict[str, Any]:
        """Probe the engine. Must not raise; returns `{"reachable": bool, ...}`."""

    def retain(self, *, bank: BankRef, document_id: str, content: str, context: str,
               metadata: dict[str, str], tags: tuple[str, ...], memory_type: str,
               timestamp: str | None) -> dict[str, Any]:
        """Store one document. Idempotent on `document_id`.

        Returns a receipt dict (see `types.RetainReceipt.as_dict`). Raises
        `TransportError`.
        """

    def recall(self, *, bank: BankRef, query: str, max_tokens: int,
               fact_types: tuple[str, ...], tags: tuple[str, ...]) -> list[EngineResult]:
        """Search one bank. Returns ranked results. Raises `TransportError`."""

    def reflect(self, *, bank: BankRef, query: str, max_tokens: int) -> dict[str, Any]:
        """Generate a historical synthesis. Raises `TransportError`."""

    def list_document_memories(self, *, bank: BankRef, document_id: str,
                               limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        """Read back the memory units a document produced.

        Needed because a remote memory unit id is not stable across a re-send
        (see `types.RetainReceipt`), so lifecycle operations and Gate read-back
        address units by listing the document.
        """

    def invalidate_memory(self, *, bank: BankRef, memory_id: str, reason: str) -> None:
        """Soft-retire one remote memory unit: excluded from recall, kept for audit."""

    def delete_document(self, *, bank: BankRef, document_id: str) -> dict[str, Any]:
        """Physically remove the remote document and its memories."""

    def get_document(self, *, bank: BankRef, document_id: str) -> dict[str, Any] | None:
        """Read a document back, or None when the engine does not have it."""
