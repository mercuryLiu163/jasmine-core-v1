"""Shared test helpers: throwaway databases and a fixed frozen clock."""

from __future__ import annotations

import pathlib

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jasmine_core import clock, db  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402

BASE = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


class FrozenClock:
    """Deterministic timestamps that still advance, so ordering is testable."""

    def __init__(self, start: datetime = BASE) -> None:
        self._current = start
        self._step = timedelta(milliseconds=1)

    def now(self) -> datetime:
        value = self._current
        self._current = self._current + self._step
        return value

    def now_rfc3339(self) -> str:
        return clock.to_rfc3339(self.now())


class DbTestCase(unittest.TestCase):
    """A fresh migrated database per test; nothing touches the user's tree."""

    migrate_db = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-core-test-")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "core.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        if self.migrate_db:
            migrate(self.conn)
        self.clock = FrozenClock()


def import_gate_module():
    """Load `scripts/p4-memory-gate.py` as a module, once per process.

    It has to be in `sys.modules` under its own name while it executes: `@dataclass`
    looks the defining module up there, so an anonymous load fails inside the standard
    library rather than in the runner. Shared so the runner tests and the sealing tests
    exercise the same module object rather than two copies of it.
    """
    import importlib.util
    import sys

    name = "p4_memory_gate"
    if name in sys.modules:
        return sys.modules[name]
    gate = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "p4-memory-gate.py"
    spec = importlib.util.spec_from_file_location(name, gate)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
