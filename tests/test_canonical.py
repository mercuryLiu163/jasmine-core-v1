"""Canonical hashing (idempotency) and redaction (logs/audit) behaviour."""

from __future__ import annotations

import unittest

from support import SRC  # noqa: F401  (path setup)

from jasmine_core import canonical  # noqa: E402


class CanonicalJson(unittest.TestCase):
    def test_key_order_and_whitespace_do_not_change_the_hash(self) -> None:
        left = {"event_type": "user.prompt", "payload": {"text": "hi", "n": 1}}
        right = {"payload": {"n": 1, "text": "hi"}, "event_type": "user.prompt"}
        self.assertEqual(canonical.body_hash(left), canonical.body_hash(right))

    def test_value_changes_change_the_hash(self) -> None:
        self.assertNotEqual(
            canonical.body_hash({"payload": {"text": "hi"}}),
            canonical.body_hash({"payload": {"text": "hi "}}),
        )

    def test_non_ascii_is_preserved_not_escaped(self) -> None:
        self.assertIn("测试", canonical.canonical_json({"text": "测试"}))

    def test_sha256_hex_of_a_string_matches_hashlib(self) -> None:
        import hashlib

        self.assertEqual(canonical.sha256_hex("abc"), hashlib.sha256(b"abc").hexdigest())


class Redaction(unittest.TestCase):
    def test_secret_looking_keys_are_masked_entirely(self) -> None:
        out = canonical.redact({"api_key": "abc123", "nested": {"authorization": "Bearer xyz"},
                                "prompt": "hello"})
        self.assertEqual(out["api_key"], canonical.REDACTED)
        self.assertEqual(out["nested"]["authorization"], canonical.REDACTED)
        self.assertEqual(out["prompt"], "hello")

    # Shapes the first implementation of redact_text let through verbatim.
    # Each of these leaked a credential; the P0-01 review reproduced all of them.
    LEAKY_SHAPES = [
        '{"password": "hunter2"}',
        'bearer_token=eyJhbGciOiJIUzI1NiJ9abcdefghijkl',
        'client_secret=shhhhh',
        'token: "abc def"',
        'Authorization: Basic dXNlcjpiYXNz',
        '-----BEGIN RSA PRIVATE KEY----- MIIE',
        'AKIAIOSFODNN7EXAMPLE',
        'ghp_1234567890abcdefghijklmnopqrstuvwxyz',
        'sk-abc',
        'x-api-key: abc123def456',
        'AIzaSyA1234567890abcdef',
    ]

    def test_credential_shapes_are_masked(self) -> None:
        for text in self.LEAKY_SHAPES:
            masked = canonical.redact_text(text)
            self.assertIn(canonical.REDACTED, masked, text)

    def test_ordinary_text_is_untouched(self) -> None:
        for text in ["请把 task 状态改成 done", "session_id=abc123", "revision=7",
                     "project prj_01K742SG000Z61XPMPFJBYH7RY updated"]:
            self.assertEqual(canonical.redact_text(text), text)

    def test_redacting_a_json_line_leaves_it_parseable(self) -> None:
        import json

        line = canonical.redact_text('{"password": "hunter2", "prompt": "hello"}')
        self.assertEqual(json.loads(line)["password"], canonical.REDACTED)
        self.assertEqual(json.loads(line)["prompt"], "hello")

    def test_redaction_never_drops_structure(self) -> None:
        out = canonical.redact({"events": [{"prompt": "hello", "token": "x"}]})
        self.assertEqual(out["events"][0]["prompt"], "hello")
        self.assertEqual(out["events"][0]["token"], canonical.REDACTED)

    def test_private_key_material_is_removed(self) -> None:
        pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
               "MIIEowIBAAKCAQEAx1234567890abcdefGHIJKLMNOP\n"
               "-----END RSA PRIVATE KEY-----")
        masked = canonical.redact_text(f"found in log: {pem}")
        self.assertNotIn("MIIEowIBAAKCAQEAx1234567890abcdef", masked)
        self.assertIn(canonical.REDACTED, masked)


if __name__ == "__main__":
    unittest.main()
