from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from wechat_digest.openclaw import (
    _client_version_number,
    _commands_from_event_payload,
    _extract_weixin_text,
    _normalize_weixin_session_key,
    _resolve_weixin_target,
    _resolve_command,
)


class OpenClawTests(unittest.TestCase):
    def test_extracts_message_events(self) -> None:
        payload = {
            "cursor": 4,
            "events": [
                {
                    "type": "message",
                    "cursor": 5,
                    "channel": "openclaw-weixin",
                    "session_key": "session-1",
                    "message_id": "msg-1",
                    "text": "/查 报名",
                }
            ],
        }

        commands, cursor = _commands_from_event_payload(payload, "openclaw-weixin")

        self.assertEqual(cursor, 5)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].text, "/查 报名")
        self.assertEqual(commands[0].session_key, "session-1")

    def test_extracts_structured_wait_event(self) -> None:
        payload = {
            "event": {
                "type": "message",
                "cursor": 8,
                "sessionKey": "session-2",
                "messageId": "msg-2",
                "role": "user",
                "text": "/status",
                "conversation": {"channel": "openclaw-weixin"},
            }
        }

        commands, cursor = _commands_from_event_payload(payload, "openclaw-weixin")

        self.assertEqual(cursor, 8)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].text, "/status")
        self.assertEqual(commands[0].session_key, "session-2")
        self.assertEqual(commands[0].source_message_id, "msg-2")

    def test_ignores_non_user_messages(self) -> None:
        payload = {
            "event": {
                "type": "message",
                "cursor": 9,
                "sessionKey": "session-3",
                "role": "assistant",
                "text": "/status",
            }
        }

        commands, cursor = _commands_from_event_payload(payload, "openclaw-weixin")

        self.assertEqual(cursor, 9)
        self.assertEqual(commands, [])

    def test_extracts_weixin_text(self) -> None:
        text = _extract_weixin_text({"item_list": [{"type": 1, "text_item": {"text": " 状态 "}}]})

        self.assertEqual(text, "状态")

    def test_client_version_number(self) -> None:
        self.assertEqual(_client_version_number("2.4.3"), 132099)

    def test_normalizes_weixin_direct_session_key(self) -> None:
        self.assertEqual(
            _normalize_weixin_session_key("agent:main:openclaw-weixin:direct:user@im.wechat"),
            "user@im.wechat",
        )
        self.assertEqual(_normalize_weixin_session_key("user@im.wechat"), "user@im.wechat")

    def test_resolves_weixin_target_from_context_tokens_case_insensitively(self) -> None:
        target, token = _resolve_weixin_target(
            "agent:main:openclaw-weixin:direct:user@im.wechat",
            {"User@im.wechat": "ctx"},
        )

        self.assertEqual(target, "User@im.wechat")
        self.assertEqual(token, "ctx")

    @unittest.skipUnless(os.name == "nt", "Windows command resolution")
    def test_resolves_windows_cmd_shim(self) -> None:
        with patch("wechat_digest.openclaw.shutil.which") as which:
            which.side_effect = lambda name: f"C:/bin/{name}" if name == "openclaw.cmd" else None

            resolved = _resolve_command(["openclaw", "mcp", "serve"])

        self.assertEqual(resolved[0], "C:/bin/openclaw.cmd")
