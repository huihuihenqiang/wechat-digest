from __future__ import annotations

import unittest

from wechat_digest.commands import parse_assistant_command


class CommandTests(unittest.TestCase):
    def test_parse_digest_yesterday(self) -> None:
        command = parse_assistant_command("/日报 昨天", ["群A"])

        self.assertEqual(command.kind, "digest")
        self.assertEqual(command.date_text, "昨天")

    def test_parse_search_with_group(self) -> None:
        command = parse_assistant_command("/查 群A 报名", ["群A"])

        self.assertEqual(command.kind, "search")
        self.assertEqual(command.group_name, "群A")
        self.assertEqual(command.query, "报名")

    def test_parse_context_reference(self) -> None:
        command = parse_assistant_command("/原文 m123", ["群A"])

        self.assertEqual(command.kind, "context")
        self.assertEqual(command.message_id, 123)

    def test_parse_status_without_slash(self) -> None:
        command = parse_assistant_command("/状态", ["群A"])

        self.assertEqual(command.kind, "status")

    def test_parse_wd_prefix(self) -> None:
        command = parse_assistant_command("wd 查 群A 报名", ["群A"])

        self.assertEqual(command.kind, "search")
        self.assertEqual(command.group_name, "群A")
        self.assertEqual(command.query, "报名")

    def test_parse_backfill_alias(self) -> None:
        command = parse_assistant_command("wd 倒查 昨天", ["群A"])

        self.assertEqual(command.kind, "digest")
        self.assertEqual(command.date_text, "昨天")
