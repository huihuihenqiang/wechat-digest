from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wechat_digest.backfill import BackfillCollector
from wechat_digest.config import config_from_mapping
from wechat_digest.models import ChatMessage
from wechat_digest.storage import DigestStore
from wechat_digest.wechat import WxautoWeChatClient, _coerce_messages, _parse_datetime


class FakeBackfillWeChat:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, datetime]] = []

    def check_ready(self) -> str:
        return "ok"

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        raise AssertionError("backfill should use fetch_messages_between when available")

    def fetch_messages_between(
        self,
        group_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        max_scrolls: int = 0,
        scroll_pause_seconds: float = 0.2,
    ) -> tuple[list[ChatMessage], int]:
        self.calls.append((group_name, start, end))
        return [
            ChatMessage(
                group_name=group_name,
                sender="张三",
                content="昨天报名截止 https://example.com",
                msg_time=datetime(2026, 5, 8, 8, 0, tzinfo=timezone.utc),
            ),
            ChatMessage(
                group_name=group_name,
                sender="李四",
                content="",
                msg_type="image",
                msg_time=datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc),
            ),
        ], 1

    def send_message(self, recipient: str, message: str) -> None:
        raise AssertionError("backfill should not send")


class BackfillTests(unittest.TestCase):
    def test_collect_date_inserts_media_and_links(self) -> None:
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
            collector = BackfillCollector(config, store, FakeBackfillWeChat())  # type: ignore[arg-type]

            result = collector.collect_date(date(2026, 5, 8))
            messages = store.get_messages(
                datetime(2026, 5, 8, tzinfo=timezone.utc),
                datetime(2026, 5, 9, tzinfo=timezone.utc),
                ["群A"],
            )

        self.assertEqual(result.inserted, 2)
        self.assertEqual(messages[0].links, ("https://example.com",))
        self.assertEqual(messages[1].content, "[图片：未解析]")

    def test_wxauto_load_more_cache_is_supported(self) -> None:
        class FakeWx:
            def __init__(self) -> None:
                self.load_times: list[int] = []

            def LoadMoreCache(self, load_times: int = 1) -> None:
                self.load_times.append(load_times)

        fake_wx = FakeWx()
        client = WxautoWeChatClient.__new__(WxautoWeChatClient)
        client.wx = fake_wx

        loaded = client._load_older_messages()

        self.assertTrue(loaded)
        self.assertEqual(fake_wx.load_times, [10])

    def test_wxauto_time_marker_is_inherited_by_following_messages(self) -> None:
        class RawTime:
            type = "time"
            sender = "system"
            content = "time marker"
            time = "2026-05-09 18:34:00"
            id = "t1"

        class RawText:
            type = "text"
            sender = "alice"
            content = "hello"
            id = "m1"

        messages = _coerce_messages(
            "group",
            [RawTime(), RawText()],
            datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc),
            naive_tz=timezone.utc,
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, "hello")
        self.assertEqual(messages[0].msg_time, datetime(2026, 5, 9, 18, 34, tzinfo=timezone.utc))

    def test_wxauto_relative_wechat_time_text_is_parsed(self) -> None:
        parsed = _parse_datetime(
            "昨天 18:34",
            datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc),
            naive_tz=timezone.utc,
        )

        self.assertEqual(parsed, datetime(2026, 5, 9, 18, 34, tzinfo=timezone.utc))

    def test_wxauto_load_more_falls_back_to_wheel_up(self) -> None:
        class FakeMsgBox:
            def __init__(self) -> None:
                self.calls: list[tuple[int, float]] = []

            def WheelUp(self, wheelTimes: int = 1, waitTime: float = 0.5) -> None:
                self.calls.append((wheelTimes, waitTime))

        class FakeChatBox:
            def __init__(self) -> None:
                self.msgbox = FakeMsgBox()
                self.control = None

        class FakeWx:
            def __init__(self) -> None:
                self.ChatBox = FakeChatBox()

            def LoadMoreCache(self, load_times: int = 1) -> None:
                raise AttributeError("load_more_message")

        fake_wx = FakeWx()
        client = WxautoWeChatClient.__new__(WxautoWeChatClient)
        client.wx = fake_wx

        loaded = client._load_older_messages()

        self.assertTrue(loaded)
        self.assertEqual(fake_wx.ChatBox.msgbox.calls, [(40, 0.05)])
