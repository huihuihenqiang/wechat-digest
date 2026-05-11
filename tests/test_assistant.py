from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wechat_digest.assistant import AssistantRuntime
from wechat_digest.config import config_from_mapping
from wechat_digest.models import ChatMessage, StoredMessage
from wechat_digest.service import DigestService
from wechat_digest.storage import DigestStore


class FakeWeChat:
    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        return []

    def fetch_messages_between(
        self,
        group_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        max_scrolls: int = 0,
        scroll_pause_seconds: float = 0.2,
    ) -> tuple[list[ChatMessage], int]:
        return [
            ChatMessage(
                group_name=group_name,
                sender="张三",
                content="明天十点报名截止",
                msg_time=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
            )
        ], 1

    def send_message(self, recipient: str, message: str) -> None:
        raise AssertionError("assistant replies through command channel, not desktop WeChat")


class FakeGenerator:
    def generate(self, target_date: date, messages: list[StoredMessage]) -> str:
        return "\n".join(
            [
                f"# 微信群日报 {target_date.isoformat()}",
                "## 今日摘要",
                f"- 共 {len(messages)} 条消息 [m{messages[0].id}]",
            ]
        )


class AssistantTests(unittest.TestCase):
    def test_search_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_from_mapping(
                {
                    "wechat": {"groups": ["群A"]},
                    "storage": {"path": str(Path(tmp) / "db.sqlite3")},
                    "digest": {"timezone": "UTC"},
                    "llm": {"api_key": "test"},
                }
            )
            store = DigestStore(config.storage.path)
            wechat = FakeWeChat()
            service = DigestService(config, store, wechat, FakeGenerator())  # type: ignore[arg-type]
            runtime = AssistantRuntime(config, store, wechat, service)  # type: ignore[arg-type]

            digest_response = runtime.handle_text("/日报 2026-05-08")
            search_response = runtime.handle_text("/查 报名")
            context_response = runtime.handle_text("/原文 m1")

        self.assertIn("已倒查 2026-05-08", digest_response.content)
        self.assertIn("[m1]", search_response.content)
        self.assertIn("明天十点报名截止", context_response.content)
