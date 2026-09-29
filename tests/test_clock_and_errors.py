"""Timestamp normalisation and the frozen error-code contract (ADR 0003 §1.4)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from support import SRC  # noqa: F401  (path setup)

from jasmine_core import clock, errors  # noqa: E402


class Rfc3339(unittest.TestCase):
    def test_utc_is_rendered_with_a_z_suffix(self) -> None:
        self.assertEqual(clock.to_rfc3339(datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)),
                         "2026-09-29T12:00:00.000000Z")

    def test_offsets_are_converted_to_utc(self) -> None:
        from datetime import timedelta

        self.assertEqual(clock.to_rfc3339(datetime(2026, 9, 29, 20, 0,
                                                    tzinfo=timezone(timedelta(hours=8)))),
                         "2026-09-29T12:00:00.000000Z")

    def test_naive_input_is_treated_as_utc(self) -> None:
        self.assertEqual(clock.to_rfc3339(datetime(2026, 9, 29, 12, 0)),
                         "2026-09-29T12:00:00.000000Z")

    def test_lexicographic_order_matches_chronological_order(self) -> None:
        stamps = [clock.to_rfc3339(datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc) )
                  , clock.to_rfc3339(datetime(2026, 9, 29, 9, 30, tzinfo=timezone.utc))]
        self.assertEqual(sorted(stamps), sorted(stamps, key=clock.parse_rfc3339))

    def test_round_trip(self) -> None:
        original = "2026-09-29T12:00:00.000000Z"
        self.assertEqual(clock.to_rfc3339(clock.parse_rfc3339(original)), original)

    def test_a_z_suffix_is_accepted(self) -> None:
        self.assertEqual(clock.parse_rfc3339("2026-09-29T12:00:00Z"),
                         datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc))

    def test_garbage_is_rejected(self) -> None:
        for bad in ["", "not-a-time", None, 12345]:
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                clock.parse_rfc3339(bad)  # type: ignore[arg-type]


class ErrorContract(unittest.TestCase):
    def test_each_error_class_carries_its_documented_code_and_status(self) -> None:
        expected = {
            errors.InvalidRequest: (400, "invalid_request"),
            errors.MissingExpectedRevision: (400, "missing_expected_revision"),
            errors.UnexpectedExpectedRevision: (400, "unexpected_expected_revision"),
            errors.Unauthenticated: (401, "unauthenticated"),
            errors.ForbiddenScope: (403, "forbidden_scope"),
            errors.ActorMismatch: (403, "actor_mismatch"),
            errors.EventIdConflict: (409, "event_id_conflict"),
            errors.SourceEventDuplicate: (409, "source_event_duplicate"),
            errors.RevisionConflict: (409, "revision_conflict"),
            errors.PayloadTooLarge: (413, "payload_too_large"),
            errors.UnsupportedMediaType: (415, "unsupported_media_type"),
            errors.SchemaVersionUnsupported: (503, "schema_version_unsupported"),
            errors.MigrationConflict: (503, "migration_conflict"),
            errors.DatabaseBusy: (503, "database_busy"),
        }
        for cls, (status, code) in expected.items():
            self.assertEqual((cls.status, cls.code), (status, code), cls.__name__)

    def test_not_found_generates_a_kind_specific_code(self) -> None:
        exc = errors.NotFound("task", "tsk_01K742SG00YPWF74TSXEKA3254")
        self.assertEqual(exc.status, 404)
        self.assertEqual(exc.code, "task_not_found")
        self.assertEqual(exc.details, {"kind": "task", "id": "tsk_01K742SG00YPWF74TSXEKA3254"})

    def test_error_payload_shape_is_uniform(self) -> None:
        payload = errors.ForbiddenScope("needs events:write", required="events:write").to_payload("aud_1")
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(set(payload["error"]), {"code", "message", "request_id", "details"})
        self.assertEqual(payload["error"]["code"], "forbidden_scope")
        self.assertEqual(payload["error"]["request_id"], "aud_1")
        self.assertEqual(payload["error"]["details"], {"required": "events:write"})

    def test_none_details_are_dropped_not_serialised_as_null(self) -> None:
        payload = errors.InvalidRequest("bad", field=None).to_payload("aud_1")
        self.assertEqual(payload["error"]["details"], {})

    def test_every_error_is_a_core_error(self) -> None:
        for cls in (errors.NotFound, errors.ForbiddenScope, errors.DatabaseBusy):
            self.assertTrue(issubclass(cls, errors.CoreError))


if __name__ == "__main__":
    unittest.main()
