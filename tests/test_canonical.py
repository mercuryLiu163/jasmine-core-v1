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

    def test_bearer_tokens_and_api_keys_in_free_text_are_masked(self) -> None:
        cases = [
            "Authorization: Bearer sk_live_abcdefghijklmnop",
            "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz",
            "password: hunter2 please",
            "my token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijkl",
        ]
        for text in cases:
            masked = canonical.redact_text(text)
            self.assertIn(canonical.REDACTED, masked, text)
            for leak in ("sk_live_abcdefghijklmnop", "abcdefghijklmnopqrstuvwxyz", "hunter2",
                         "eyJhbGciOiJIUzI1NiJ9"):
                self.assertNotIn(leak, masked, text)

    def test_ordinary_text_is_untouched(self) -> None:
        text = "请把 task 状态改成 done"
        self.assertEqual(canonical.redact_text(text), text)

    def test_redaction_never_drops_structure(self) -> None:
        out = canonical.redact({"events": [{"prompt": "hello", "token": "x"}]})
        self.assertEqual(out["events"][0]["prompt"], "hello")
        self.assertEqual(out["events"][0]["token"], canonical.REDACTED)


if __name__ == "__main__":
    unittest.main()
