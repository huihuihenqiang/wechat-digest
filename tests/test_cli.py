from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from wechat_digest.cli import main
from wechat_digest.models import ChatMessage
from wechat_digest.storage import DigestStore


class CliTests(unittest.TestCase):
    def _write_config(self, root: Path) -> Path:
        config_path = root / "config.yaml"
        config_path.write_text(
            f"""
wechat:
  groups:
    - "群A"
storage:
  path: "{(root / 'db.sqlite3').as_posix()}"
digest:
  timezone: "UTC"
llm:
  api_key: ""
""".strip(),
            encoding="utf-8",
        )
        return config_path

    def test_digest_dry_run_does_not_require_wechat_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "digest-now", "--date", "2026-05-08", "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertIn("今天没有采集到", output.getvalue())

    def test_digest_backfill_dry_run_does_not_require_wechat_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "digest-backfill",
                        "--date",
                        "2026-05-08",
                        "--dry-run",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("今天没有采集到", output.getvalue())

    def test_tool_status_search_context_and_handle_do_not_require_wechat_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            store = DigestStore(root / "db.sqlite3")
            store.add_messages(
                [
                    ChatMessage(
                        group_name="群A",
                        sender="张三",
                        content="明天十点报名截止 https://example.com",
                        msg_time=datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc),
                    )
                ]
            )

            status_output = StringIO()
            with redirect_stdout(status_output):
                status_code = main(["--config", str(config_path), "status"])

            search_output = StringIO()
            with redirect_stdout(search_output):
                search_code = main(["--config", str(config_path), "search", "报名"])

            context_output = StringIO()
            with redirect_stdout(context_output):
                context_code = main(["--config", str(config_path), "context", "m1"])

            handle_output = StringIO()
            with redirect_stdout(handle_output):
                handle_code = main(["--config", str(config_path), "handle", "wd", "查", "报名"])

        self.assertEqual(status_code, 0)
        self.assertIn("入库消息", status_output.getvalue())
        self.assertEqual(search_code, 0)
        self.assertIn("[m1]", search_output.getvalue())
        self.assertEqual(context_code, 0)
        self.assertIn("明天十点报名截止", context_output.getvalue())
        self.assertEqual(handle_code, 0)
        self.assertIn("明天十点", handle_output.getvalue())
