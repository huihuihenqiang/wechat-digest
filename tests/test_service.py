from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wechat_digest.config import config_from_mapping
from wechat_digest.models import ChatMessage, StoredMessage
from wechat_digest.service import DigestService
from wechat_digest.storage import DigestStore


class FakeWeChat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def check_ready(self) -> str:
        return "ok"

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        return [
            ChatMessage(
                group_name=group_name,
                sender="李四",
                content="项目今天完成验收",
                msg_time=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
            )
        ]

    def send_message(self, recipient: str, message: str) -> None:
        self.sent.append((recipient, message))


class FakeGenerator:
    def generate(self, target_date: date, messages: list[StoredMessage]) -> str:
        return f"# 日报 {target_date.isoformat()}\n消息数：{len(messages)}"


class ServiceTests(unittest.TestCase):
    def test_collect_digest_and_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_from_mapping(
                {
                    "wechat": {
                        "groups": ["群A"],
                        "recipient": "文件传输助手",
                        "message_chunk_size": 100,
                    },
                    "storage": {"path": str(Path(tmp) / "db.sqlite3")},
                    "digest": {"timezone": "UTC"},
                    "llm": {"api_key": "test"},
                }
            )
            store = DigestStore(config.storage.path)
            wechat = FakeWeChat()
            service = DigestService(config, store, wechat, FakeGenerator())  # type: ignore[arg-type]

            inserted = service.collect_once()
            result = service.generate_daily_digest(date(2026, 5, 8), dry_run=False)

        self.assertEqual(inserted, 1)
        self.assertTrue(result.sent)
        self.assertEqual(result.message_count, 1)
        self.assertEqual(wechat.sent[0][0], "文件传输助手")
        self.assertIn("消息数：1", wechat.sent[0][1])
