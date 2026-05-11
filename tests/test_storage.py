from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
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

    def test_digests_are_scoped_by_group_or_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DigestStore(Path(tmp) / "db.sqlite3")

            store.save_digest("2026-05-11", "daily", scope="group-a")
            store.save_digest("2026-05-11", "once", scope="once:group-a")

            daily = store.get_digest("2026-05-11", scope="group-a")
            once = store.get_digest("2026-05-11", scope="once:group-a")

        self.assertIsNotNone(daily)
        self.assertIsNotNone(once)
        assert daily is not None
        assert once is not None
        self.assertEqual(daily.content, "daily")
        self.assertEqual(once.content, "once")
        self.assertEqual(once.scope, "once:group-a")

    def test_migrates_old_digest_table_to_scoped_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "db.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE digests (digest_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL, content TEXT NOT NULL, sent_at TEXT)"
            )
            conn.execute(
                "INSERT INTO digests (digest_date, generated_at, content, sent_at) VALUES (?, ?, ?, ?)",
                ("2026-05-11", "2026-05-11T00:00:00+00:00", "legacy", None),
            )
            conn.commit()
            conn.close()

            store = DigestStore(db_path)
            digest = store.get_digest("2026-05-11")

        self.assertIsNotNone(digest)
        assert digest is not None
        self.assertEqual(digest.content, "legacy")
        self.assertEqual(digest.scope, "")
