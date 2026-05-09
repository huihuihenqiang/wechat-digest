from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from wechat_digest.digest import build_digest_prompt, split_message
from wechat_digest.models import StoredMessage


class DigestTests(unittest.TestCase):
    def test_split_message_keeps_short_text(self) -> None:
        self.assertEqual(split_message("hello", 10), ["hello"])

    def test_split_message_splits_long_text(self) -> None:
        chunks = split_message("a\nb\nc\n", 4)
        self.assertEqual(chunks, ["a\nb", "c"])

    def test_prompt_contains_message_context(self) -> None:
        message = StoredMessage(
            id=1,
            group_name="群A",
            sender="张三",
            content="明天十点开会",
            msg_type="text",
            msg_time=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
            first_seen_at=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
            content_hash="hash",
        )

        prompt = build_digest_prompt(date(2026, 5, 8), [message])

        self.assertEqual(prompt[0]["role"], "system")
        self.assertIn("张三", prompt[1]["content"])
        self.assertIn("明天十点开会", prompt[1]["content"])
