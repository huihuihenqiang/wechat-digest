from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from wechat_digest.config import config_from_mapping, load_config


class ConfigTests(unittest.TestCase):
    def test_config_from_mapping(self) -> None:
        config = config_from_mapping(
            {
                "wechat": {"groups": ["群A"], "recipient": "文件传输助手"},
                "llm": {"api_key": "key", "model": "model"},
            }
        )

        self.assertEqual(config.wechat.groups, ["群A"])
        self.assertEqual(config.wechat.recipient, "文件传输助手")
        self.assertEqual(config.collection.mode, "backfill")
        self.assertEqual(config.digest.time, "23:00")
        self.assertEqual(config.llm.model, "model")
        self.assertFalse(config.openclaw.enabled)
        self.assertEqual(config.openclaw.transport, "mcp")
        self.assertEqual(config.openclaw.request_timeout_seconds, 90)
        self.assertEqual(config.openclaw.sync_path, "data/openclaw-weixin-direct-sync.json")

    def test_load_config_expands_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("OPENAI_API_KEY=test-key\nOPENAI_MODEL=test-model\n", encoding="utf-8")
            (root / "config.yaml").write_text(
                """
wechat:
  groups:
    - "群A"
llm:
  api_key: "${OPENAI_API_KEY}"
  model: "${OPENAI_MODEL}"
""".strip(),
                encoding="utf-8",
            )
            old_key = os.environ.pop("OPENAI_API_KEY", None)
            old_model = os.environ.pop("OPENAI_MODEL", None)
            try:
                config = load_config(root / "config.yaml", root / ".env")
            finally:
                if old_key is not None:
                    os.environ["OPENAI_API_KEY"] = old_key
                if old_model is not None:
                    os.environ["OPENAI_MODEL"] = old_model

        self.assertEqual(config.llm.api_key, "test-key")
        self.assertEqual(config.llm.model, "test-model")
