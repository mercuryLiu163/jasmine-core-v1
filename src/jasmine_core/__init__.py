"""Jasmine Core V1 baseline.

Public entry points live in submodules; this file intentionally exposes only
version metadata so that importing the package never opens a database.
"""

from __future__ import annotations

__all__ = ["__version__", "SCHEMA_VERSION"]

__version__ = "0.1.0"

#: Mirrors the highest shipped migration. Kept here so the version check does
#: not require importing every migration module.
SCHEMA_VERSION = 10
