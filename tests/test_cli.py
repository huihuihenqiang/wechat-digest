from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from wechat_digest.cli import main


class CliTests(unittest.TestCase):
    def test_digest_dry_run_does_not_require_wechat_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "digest-now", "--date", "2026-05-08", "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertIn("今天没有采集到", output.getvalue())
