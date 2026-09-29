"""Validation recording. P0 ships the minimal Recorder; P7 completes it."""

from __future__ import annotations

__all__ = ["Case", "Recorder", "Run"]


def __getattr__(name: str):
    if name in ("Case", "Recorder", "Run"):
        from .recorder import Case, Recorder, Run

        return {"Case": Case, "Recorder": Recorder, "Run": Run}[name]
    raise AttributeError(name)
