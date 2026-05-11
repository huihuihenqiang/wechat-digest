from __future__ import annotations

from datetime import datetime, timezone
import unittest

from wechat_digest.commands import parse_assistant_command
from wechat_digest.wechat import WxautoWeChatClient, _parse_datetime


class SchedulerDateTests(unittest.TestCase):
    def test_parse_digest_date_form(self) -> None:
        command = parse_assistant_command("wd \u65e5\u62a5 \u4eca\u5929", ["group"])

        self.assertEqual(command.kind, "digest")
        self.assertEqual(command.date_text, "\u4eca\u5929")
        self.assertIsNone(command.scroll_count)

    def test_parse_digest_scroll_form(self) -> None:
        command = parse_assistant_command("wd \u65e5\u62a5 50", ["group"])

        self.assertEqual(command.kind, "digest")
        self.assertIsNone(command.date_text)
        self.assertEqual(command.scroll_count, 50)

    def test_parse_digest_rejects_mixed_date_and_scroll(self) -> None:
        command = parse_assistant_command("wd \u65e5\u62a5 \u4eca\u5929 50", ["group"])

        self.assertEqual(command.kind, "digest")
        self.assertIsNone(command.date_text)
        self.assertIsNone(command.scroll_count)
        self.assertEqual(command.query, "\u4eca\u5929 50")

    def test_wxauto_scrolls_to_latest_before_backfill(self) -> None:
        class FakeMsgBox:
            def __init__(self) -> None:
                self.down_calls: list[tuple[int, float]] = []

            def WheelDown(self, wheelTimes: int = 1, waitTime: float = 0.5) -> None:
                self.down_calls.append((wheelTimes, waitTime))

        class FakeChatBox:
            def __init__(self) -> None:
                self.msgbox = FakeMsgBox()
                self.control = None

        fake_wx = type("FakeWx", (), {"ChatBox": FakeChatBox()})()
        client = WxautoWeChatClient.__new__(WxautoWeChatClient)
        client.wx = fake_wx
        client._read_messages = lambda limit: [{"sender": "Alice", "content": "Hello"}]

        reached = client._ensure_at_chat_bottom()

        self.assertTrue(reached)
        self.assertGreaterEqual(len(fake_wx.ChatBox.msgbox.down_calls), 2)

    def test_wxauto_weekday_marker_is_parsed(self) -> None:
        parsed = _parse_datetime(
            "\u661f\u671f\u516d",
            datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc),
            naive_tz=timezone.utc,
        )

        self.assertEqual(parsed, datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc))
