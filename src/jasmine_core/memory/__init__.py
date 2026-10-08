"""Long-term Memory: a replaceable, non-authoritative Hindsight adapter (P4).

Importing this package must not open a database or a socket, so the submodules
are resolved lazily by the caller (`memory.adapter`, `memory.jobs`, ...). See
`docs/adr/0008-p4-memory-and-durable-jobs.md`.

The one invariant every module here defends: Memory is history. Nothing in this
package may activate, supersede or retire a Rule, move State, mint Evidence,
overwrite a Checkpoint, or raise a Task revision. Recall is background; Authority
and Current State are truth (design §3, §7).
"""

from __future__ import annotations

__all__ = ["ENGINE_ID"]

#: The V1 default engine. Replaceable by design (design §2/§16), so nothing
#: outside `memory.hindsight` may assume it: the bank, provenance and job code
#: is written against the `memory.transport` protocol.
ENGINE_ID = "hindsight"
