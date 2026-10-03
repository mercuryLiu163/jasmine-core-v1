"""Jasmine Core V1 baseline.

Public entry points live in submodules; this file intentionally exposes only
version metadata so that importing the package never opens a database.
"""

from __future__ import annotations

__all__ = ["__version__", "SCHEMA_VERSION"]

__version__ = "0.1.0"

#: Mirrors the highest shipped migration. Kept here so the version check does
#: not require importing every migration module.

#: P4 adds `m0009_memory` (ADR 0014 §3), renumbered from the `m0007` this branch
#: was originally cut with and then from `m0008`, because P2 claimed
#: `m0007_interpretations` and `m0008_resolutions` first.
#: `migrate()` refuses to run when the shipped migration count and this constant
#: disagree, so a forgotten renumber is a startup failure rather than a silent
#: schema divergence.
SCHEMA_VERSION = 9
