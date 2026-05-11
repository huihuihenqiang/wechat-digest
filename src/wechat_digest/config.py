from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any


_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")


@dataclass(frozen=True)
class WeChatConfig:
    groups: list[str]
    recipient: str = "文件传输助手"
    poll_interval_seconds: int = 60
    message_fetch_limit: int = 80
    message_chunk_size: int = 1800


@dataclass(frozen=True)
class CollectionConfig:
    mode: str = "backfill"
    backfill_until: str = "previous_day_start"
    backfill_fetch_limit: int = 1000
    backfill_max_scrolls: int = 20
    scroll_pause_seconds: float = 0.02


@dataclass(frozen=True)
class DigestConfig:
    time: str = "23:00"
    timezone: str = "Asia/Hong_Kong"
    retention_days: int = 30
    prompt_path: str = "prompts/digest.zh.md"


@dataclass(frozen=True)
class StorageConfig:
    path: str = "data/wechat_digest.sqlite3"


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 60
    max_input_messages: int = 300
    temperature: float = 0.2


@dataclass(frozen=True)
class OpenClawConfig:
    enabled: bool = False
    channel: str = "openclaw-weixin"
    transport: str = "mcp"
    command: str = "openclaw"
    args: tuple[str, ...] = ("mcp", "serve")
    poll_timeout_seconds: int = 30
    request_timeout_seconds: int = 90
    account_id: str = ""
    base_dir: str = ""
    sync_path: str = "data/openclaw-weixin-direct-sync.json"


@dataclass(frozen=True)
class AppConfig:
    wechat: WeChatConfig
    collection: CollectionConfig
    digest: DigestConfig
    storage: StorageConfig
    llm: LLMConfig
    openclaw: OpenClawConfig


class ConfigError(ValueError):
    """Raised when a config file is missing required values."""


def load_dotenv(path: str | Path = ".env") -> None:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_config(path: str | Path = "config.yaml", env_path: str | Path = ".env") -> AppConfig:
    load_dotenv(env_path)
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    data = _load_mapping(config_path)
    data = _expand_env(data)
    return config_from_mapping(data)


def config_from_mapping(data: dict[str, Any]) -> AppConfig:
    wechat_data = _mapping(data.get("wechat"), "wechat")
    collection_data = _mapping(data.get("collection", {}), "collection")
    digest_data = _mapping(data.get("digest", {}), "digest")
    storage_data = _mapping(data.get("storage", {}), "storage")
    llm_data = _mapping(data.get("llm", {}), "llm")
    openclaw_data = _mapping(data.get("openclaw", {}), "openclaw")

    groups = [str(item).strip() for item in wechat_data.get("groups", []) if str(item).strip()]
    if not groups:
        raise ConfigError("wechat.groups must contain at least one group name")

    recipient = str(wechat_data.get("recipient", "文件传输助手")).strip()
    if not recipient:
        raise ConfigError("wechat.recipient cannot be empty")

    return AppConfig(
        wechat=WeChatConfig(
            groups=groups,
            recipient=recipient,
            poll_interval_seconds=_positive_int(wechat_data.get("poll_interval_seconds", 60), "wechat.poll_interval_seconds"),
            message_fetch_limit=_positive_int(wechat_data.get("message_fetch_limit", 80), "wechat.message_fetch_limit"),
            message_chunk_size=_positive_int(wechat_data.get("message_chunk_size", 1800), "wechat.message_chunk_size"),
        ),
        collection=CollectionConfig(
            mode=str(collection_data.get("mode", "backfill")),
            backfill_until=str(collection_data.get("backfill_until", "previous_day_start")),
            backfill_fetch_limit=_positive_int(
                collection_data.get("backfill_fetch_limit", 500),
                "collection.backfill_fetch_limit",
            ),
            backfill_max_scrolls=_positive_int(
                collection_data.get("backfill_max_scrolls", 20),
                "collection.backfill_max_scrolls",
            ),
            scroll_pause_seconds=float(collection_data.get("scroll_pause_seconds", 0.02)),
        ),
        digest=DigestConfig(
            time=str(digest_data.get("time", "23:00")),
            timezone=str(digest_data.get("timezone", "Asia/Hong_Kong")),
            retention_days=_positive_int(digest_data.get("retention_days", 30), "digest.retention_days"),
            prompt_path=str(digest_data.get("prompt_path", "prompts/digest.zh.md")),
        ),
        storage=StorageConfig(path=str(storage_data.get("path", "data/wechat_digest.sqlite3"))),
        llm=LLMConfig(
            base_url=_first_nonempty(llm_data.get("base_url"), os.getenv("OPENAI_BASE_URL"), "https://api.openai.com/v1"),
            api_key=_first_nonempty(llm_data.get("api_key"), os.getenv("OPENAI_API_KEY"), ""),
            model=_first_nonempty(llm_data.get("model"), os.getenv("OPENAI_MODEL"), "gpt-4o-mini"),
            timeout_seconds=_positive_int(llm_data.get("timeout_seconds", 60), "llm.timeout_seconds"),
            max_input_messages=_positive_int(llm_data.get("max_input_messages", 300), "llm.max_input_messages"),
            temperature=float(llm_data.get("temperature", 0.2)),
        ),
        openclaw=OpenClawConfig(
            enabled=_bool(openclaw_data.get("enabled", False)),
            channel=str(openclaw_data.get("channel", "openclaw-weixin")),
            transport=str(openclaw_data.get("transport", "mcp")),
            command=str(openclaw_data.get("command", "openclaw")),
            args=_string_tuple(openclaw_data.get("args", ["mcp", "serve"])),
            poll_timeout_seconds=_positive_int(
                openclaw_data.get("poll_timeout_seconds", 30),
                "openclaw.poll_timeout_seconds",
            ),
            request_timeout_seconds=_positive_int(
                openclaw_data.get("request_timeout_seconds", 90),
                "openclaw.request_timeout_seconds",
            ),
            account_id=str(openclaw_data.get("account_id", "")),
            base_dir=str(openclaw_data.get("base_dir", "")),
            sync_path=str(openclaw_data.get("sync_path", "data/openclaw-weixin-direct-sync.json")),
        ),
    )


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_simple_yaml(text)

    loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        raise ConfigError("Config root must be a mapping")
    return loaded


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if number <= 0:
        raise ConfigError(f"{name} must be positive")
    return number


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return tuple(item for item in value.split() if item)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), match.group(2) or ""), value)
    return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    if not lines:
        return {}
    parsed, index = _parse_dict_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ConfigError("Could not parse config file. Install PyYAML for full YAML support.")
    return parsed


def _parse_dict_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ConfigError("Invalid indentation in config file")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise ConfigError(f"Invalid config line: {content}")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            result[key] = _parse_scalar(raw_value)
            continue
        if index >= len(lines) or lines[index][0] <= line_indent:
            result[key] = {}
            continue
        next_indent, next_content = lines[index]
        if next_content.startswith("- "):
            result[key], index = _parse_list_block(lines, index, next_indent)
        else:
            result[key], index = _parse_dict_block(lines, index, next_indent)
    return result, index


def _parse_list_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent or not content.startswith("- "):
            raise ConfigError("Invalid list indentation in config file")
        raw_value = content[2:].strip()
        index += 1
        if raw_value:
            result.append(_parse_scalar(raw_value))
            continue
        if index >= len(lines) or lines[index][0] <= line_indent:
            result.append(None)
            continue
        result_item, index = _parse_dict_block(lines, index, lines[index][0])
        result.append(result_item)
    return result, index


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "none"}:
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
