from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wechat_digest.cli import _format_fetch_once_summary
from wechat_digest.config import config_from_mapping
from wechat_digest.models import StoredMessage
from wechat_digest.service import DigestService
from wechat_digest.storage import DigestStore


class FakeBackfill:
    scroll_count = 2
    fetched = 1
    inserted = 1


class FakeGenerator:
    def generate(self, target_date, messages):
        return f"AI summary for {target_date}: {len(messages)} messages"


class FakeWeChat:
    pass


class FetchOnceTests(unittest.TestCase):
    def test_fetch_once_formats_ai_summary_not_raw_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_from_mapping(
                {
                    "wechat": {"groups": ["group"], "recipient": "me"},
                    "storage": {"path": str(Path(tmp) / "db.sqlite3")},
                    "digest": {"timezone": "UTC"},
                    "llm": {"api_key": "test"},
                }
            )
            service = DigestService(config, DigestStore(config.storage.path), FakeWeChat(), FakeGenerator())  # type: ignore[arg-type]
            messages = [
                StoredMessage(
                    id=1,
                    group_name="group",
                    sender="alice",
                    content="raw message should not be dumped",
                    msg_type="text",
                    msg_time=datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc),
                    first_seen_at=datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc),
                    content_hash="h",
                )
            ]

            result = _format_fetch_once_summary(config, service, "group", date(2026, 5, 11), FakeBackfill(), messages)

        self.assertIn("AI summary for 2026-05-11: 1 messages", result)
        self.assertNotIn("[01:00] alice", result)

