#!/usr/bin/env python3.13
"""Tests for `scripts/p6/run_t05_degraded.py`'s revision reader.

The operator's independent review found the first version of the T05 revision check passed
vacuously: it read `task_revision` / `revision` from the *top level* of the replies, where the real
fields are `payload["task"]["revision"]` and `payload["rule"]["revision"]`, so both sides of the
comparison were `None` and a changed revision still passed. These tests pin the reader that replaced
it: a wrong layer or a missing field must raise (so the check fails with a reason), and two different
revisions must compare unequal.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "p6"))

from run_t05_degraded import revision_of  # noqa: E402


class RevisionReader(unittest.TestCase):
    def test_reads_the_field_from_the_wrapped_object(self) -> None:
        self.assertEqual(revision_of({"task": {"task_id": "tsk_1", "revision": 7}}, "task"), 7)
        self.assertEqual(revision_of({"rule": {"rule_id": "rul_1", "revision": 3}}, "rule"), 3)

    def test_refuses_the_wrong_layer_instead_of_returning_none(self) -> None:
        # The exact shape that made the first version vacuous: the field at the top level.
        with self.assertRaises(ValueError):
            revision_of({"task_revision": 7}, "task")
        with self.assertRaises(ValueError):
            revision_of({"revision": 3}, "rule")
        with self.assertRaises(ValueError):
            revision_of({"task": {"task_revision": 7}}, "task")

    def test_refuses_a_missing_or_non_integer_field(self) -> None:
        for payload in ({"task": {}}, {"task": {"revision": None}}, {"task": {"revision": "7"}},
                        {"task": None}, None, []):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    revision_of(payload, "task")

    def test_a_changed_revision_compares_unequal(self) -> None:
        before = {"task": {"revision": 7}}
        after = {"task": {"revision": 8}}
        self.assertNotEqual(revision_of(before, "task"), revision_of(after, "task"))

    def test_the_real_response_shapes_read_correctly(self) -> None:
        # The layers as the real Core answers them, trimmed to the fields the reader touches.
        task = json.loads('{"task": {"task_id": "tsk_x", "revision": 4}}')
        rule = json.loads('{"rule": {"rule_id": "rul_x", "revision": 1}}')
        self.assertEqual((revision_of(task, "task"), revision_of(rule, "rule")), (4, 1))


if __name__ == "__main__":
    unittest.main()
