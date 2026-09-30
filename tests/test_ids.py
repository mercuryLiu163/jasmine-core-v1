"""Public ID contract (ADR 0001 §2.2)."""

from __future__ import annotations

import re
import unittest

from support import SRC  # noqa: F401  (path setup)

from jasmine_core import ids  # noqa: E402


class IdFormat(unittest.TestCase):
    def test_shape_is_prefix_underscore_26_crockford_chars(self) -> None:
        value = ids.new_id("evt")
        self.assertRegex(value, re.compile(r"^evt_[0-9A-HJKMNP-TV-Z]{26}$"))
        self.assertEqual(len(value), 30)

    def test_every_public_object_prefix_is_reserved(self) -> None:
        self.assertEqual(
            ids.PREFIXES,
            {"prj", "tsk", "stp", "ses", "hst", "act", "evt", "evd", "aud", "key", "rul", "int"},
        )

    def test_prefix_appears_in_the_identifier(self) -> None:
        for prefix in sorted(ids.PREFIXES):
            self.assertTrue(ids.new_id(prefix).startswith(f"{prefix}_"))

    def test_unknown_prefix_is_rejected(self) -> None:
        with self.assertRaises(ids.IdError):
            ids.new_id("usr")

    def test_timestamped_ids_sort_in_creation_order(self) -> None:
        older = ids.new_id("tsk", now_ms=1_700_000_000_000)
        newer = ids.new_id("tsk", now_ms=1_700_000_001_000)
        self.assertLess(older, newer)

    def test_random_part_makes_repeated_ids_distinct(self) -> None:
        same_ms = 1_760_000_000_000
        generated = {ids.new_id("evt", now_ms=same_ms) for _ in range(500)}
        self.assertEqual(len(generated), 500)


class IdParsing(unittest.TestCase):
    def test_round_trip_recovers_prefix_and_timestamp(self) -> None:
        now_ms = 1_760_000_000_000
        value = ids.new_id("tsk", now_ms=now_ms)
        self.assertEqual(ids.parse_id(value), ("tsk", now_ms))

    def test_event_id_is_opaque_even_when_generated_with_a_clock(self) -> None:
        value = ids.new_id("evt", now_ms=1_760_000_000_000)
        self.assertEqual(ids.parse_id(value), ("evt", None))

    def test_event_accepts_full_130_bit_body_but_timestamped_ids_do_not(self) -> None:
        body = "Z" * ids.TOTAL_CHARS
        self.assertEqual(ids.parse_id("evt_" + body), ("evt", None))
        for prefix in ids.PREFIXES - {"evt"}:
            with self.subTest(prefix=prefix), self.assertRaises(ids.IdError):
                ids.parse_id(prefix + "_" + body)

    def test_timestamp_boundary_is_enforced_for_non_events(self) -> None:
        max_ms = (1 << 48) - 1
        suffix = "0" * ids.RANDOM_CHARS
        for prefix in ids.PREFIXES - {"evt"}:
            with self.subTest(prefix=prefix):
                maximum = prefix + "_" + ids._b32(max_ms, ids.TIMESTAMP_CHARS) + suffix
                overflow = prefix + "_" + ids._b32(max_ms + 1, ids.TIMESTAMP_CHARS) + suffix
                self.assertEqual(ids.parse_id(maximum), (prefix, max_ms))
                self.assertFalse(ids.is_id(overflow))
                with self.assertRaises(ids.IdError):
                    ids.new_id(prefix, now_ms=max_ms + 1)

    def test_parse_enforces_the_expected_prefix(self) -> None:
        value = ids.new_id("tsk")
        with self.assertRaises(ids.IdError):
            ids.parse_id(value, expect_prefix="evt")

    def test_malformed_values_are_rejected(self) -> None:
        for bad in ["", "evt", "evt_short", "evt_" + "0" * 25, "evt_" + "0" * 27,
                    "evt_0000000000IIIIIIIIIIIIIIII", "EVT_" + "0" * 26, 12345, None]:
            self.assertFalse(ids.is_id(bad), bad)

    def test_trailing_newline_is_rejected_for_event_and_timestamped_ids(self) -> None:
        for prefix in ("evt", "tsk"):
            with self.subTest(prefix=prefix):
                value = ids.new_id(prefix) + "\n"
                self.assertFalse(ids.is_id(value))
                with self.assertRaises(ids.IdError):
                    ids.parse_id(value)

    def test_is_id_accepts_generated_ids(self) -> None:
        self.assertTrue(ids.is_id(ids.new_id("prj"), "prj"))
        self.assertFalse(ids.is_id(ids.new_id("prj"), "tsk"))


if __name__ == "__main__":
    unittest.main()
