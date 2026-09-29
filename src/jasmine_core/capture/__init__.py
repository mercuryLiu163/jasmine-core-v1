"""Capture adapters. P0 ships exactly one: Codex UserPromptSubmit.

Each adapter is a translation from an external agent's event shape to a Core
`/v1/events` body. Adapters contain no truth logic, no interpretation and no
agent state; those are P1, P2 and P5.
"""

from __future__ import annotations

__all__ = ["codex_user_prompt_submit"]
