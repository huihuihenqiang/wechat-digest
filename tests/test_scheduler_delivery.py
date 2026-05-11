from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_digest.cli import _run_scheduled_delivery
from wechat_digest.config import config_from_mapping
from wechat_digest.models import ChatMessage, StoredMessage
from wechat_digest.service import DigestService
from wechat_digest.storage import DigestStore


class FakeWeChat:
    def __init__(self) -> None:
        self.backfill_groups: list[str] = []
        self.direct_sends: list[tuple[str, str]] = []

    def check_ready(self) -> str:
        return "ok"

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        raise AssertionError("scheduled delivery should backfill only when due")

    def fetch_messages_between(
        self,
        group_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        max_scrolls: int = 0,
        scroll_pause_seconds: float = 0.2,
    ) -> tuple[list[ChatMessage], int]:
        self.backfill_groups.append(group_name)
        return [
            ChatMessage(
                group_name=group_name,
                sender="alice",
                content="scheduled item",
                msg_time=datetime.now(timezone.utc),
            )
        ], 1

    def send_message(self, recipient: str, message: str) -> None:
        self.direct_sends.append((recipient, message))


class FakeGenerator:
    def generate(self, target_date, messages: list[StoredMessage]) -> str:
        return f"# digest\nmessages={len(messages)}"


class SchedulerDeliveryTests(unittest.TestCase):
    def test_scheduled_delivery_uses_active_group_and_openclaw_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_from_mapping(
                {
                    "wechat": {"groups": ["configured"], "recipient": "bot"},
                    "storage": {"path": str(Path(tmp) / "db.sqlite3")},
                    "digest": {"timezone": "UTC"},
                    "llm": {"api_key": "test"},
                    "openclaw": {"enabled": True},
                }
            )
            store = DigestStore(config.storage.path)
            store.set_state("active_group", "active")
            store.set_state("openclaw_reply_session_key", "session-1")
            store.set_schedule("00:00", enabled=True)
            wechat = FakeWeChat()
            service = DigestService(config, store, wechat, FakeGenerator())  # type: ignore[arg-type]
            delivered: list[tuple[str, str]] = []

            with patch("wechat_digest.cli._send_openclaw_reply", side_effect=lambda _config, session, text: delivered.append((session, text))):
                result = _run_scheduled_delivery(config, service, lambda _msg: None)
                second = _run_scheduled_delivery(config, service, lambda _msg: None)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["groups"], ["active"])
        self.assertEqual(second["status"], "already_ran")
        self.assertEqual(wechat.backfill_groups, ["active"])
        self.assertEqual(wechat.direct_sends, [])
        self.assertEqual(delivered[0][0], "session-1")
        self.assertIn("messages=1", delivered[0][1])

    def test_scheduled_delivery_does_not_retry_focus_work_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_from_mapping(
                {
                    "wechat": {"groups": ["configured"], "recipient": "bot"},
                    "storage": {"path": str(Path(tmp) / "db.sqlite3")},
                    "digest": {"timezone": "UTC"},
                    "llm": {"api_key": "test"},
                    "openclaw": {"enabled": True},
                }
            )
            store = DigestStore(config.storage.path)
            store.set_state("active_group", "active")
            store.set_state("openclaw_reply_session_key", "session-1")
            store.set_schedule("00:00", enabled=True)
            wechat = FakeWeChat()
            service = DigestService(config, store, wechat, FakeGenerator())  # type: ignore[arg-type]

            with patch("wechat_digest.cli._send_openclaw_reply", side_effect=RuntimeError("send failed")):
                with self.assertRaises(RuntimeError):
                    _run_scheduled_delivery(config, service, lambda _msg: None)
                second = _run_scheduled_delivery(config, service, lambda _msg: None)

            self.assertEqual(second["status"], "already_ran")
            self.assertEqual(wechat.backfill_groups, ["active"])
            self.assertEqual(store.get_state("last_scheduled_digest_error"), "send failed")
