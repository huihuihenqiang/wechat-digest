from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wechat_digest.models import ChatMessage
from wechat_digest.storage import DigestStore


class StorageTests(unittest.TestCase):
    def test_add_messages_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DigestStore(Path(tmp) / "db.sqlite3")
            message = ChatMessage(
                group_name="群A",
                sender="张三",
                content="重要消息",
                msg_time=datetime(2026, 5, 8, 1, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(store.add_messages([message, message]), 1)
            self.assertEqual(store.count_messages(), 1)

    def test_get_messages_by_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DigestStore(Path(tmp) / "db.sqlite3")
            store.add_messages(
                [
                    ChatMessage(
                        group_name="群A",
                        sender="张三",
                        content="重要消息",
                        msg_time=datetime(2026, 5, 8, 1, 0, tzinfo=timezone.utc),
                    )
                ]
            )

            messages = store.get_messages(
                datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc),
                ["群A"],
            )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, "重要消息")
