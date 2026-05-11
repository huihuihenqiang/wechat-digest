from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_REF_RE = re.compile(r"\bm(?P<id>\d+)\b", re.IGNORECASE)
_ROUND_TOKEN_RE = re.compile(r"^(?P<count>\d+)$")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_PREFIXES = ("wd", "wechat-digest", "wechat_digest")
_DIGEST_COMMANDS = {"日报", "倒查", "总结", "digest", "daily", "backfill"}
_SEARCH_COMMANDS = {"查", "查找", "搜索", "找", "search", "find"}
_GROUP_COMMANDS = {"群", "群聊", "group"}
_MEMORY_COMMANDS = {"记忆", "memory"}
_SCHEDULE_COMMANDS = {"定时", "schedule"}
_STATUS_COMMANDS = {"状态", "status"}
_CONTEXT_COMMANDS = {"原文", "上下文", "context", "src"}
_HELP_COMMANDS = {"帮助", "help"}
_DATE_TOKENS = {"今天", "今日", "today", "昨天", "昨日", "yesterday"}


@dataclass(frozen=True)
class AssistantCommand:
    kind: str
    raw_text: str
    date_text: str | None = None
    group_name: str | None = None
    query: str | None = None
    message_id: int | None = None
    scroll_count: int | None = None


def parse_assistant_command(text: str, groups: list[str] | None = None) -> AssistantCommand:
    raw_text = text.strip()
    normalized = raw_text.lstrip()
    if not normalized:
        return AssistantCommand("help", raw_text)

    prefix, normalized = _strip_prefix(normalized)
    if not normalized:
        return AssistantCommand("help", raw_text)

    if normalized.startswith("/"):
        command, rest = _split_slash_command(normalized)
        return _dispatch_command(command, rest, raw_text, groups or [], unknown_as_search=False)

    command, rest = _split_word_command(normalized[1:].strip() if prefix == "#" else normalized)
    if prefix == "#":
        return _dispatch_command(command, rest, raw_text, groups or [], unknown_as_search=True)

    return _dispatch_legacy(command, rest, normalized, raw_text, groups or [])


def _dispatch_legacy(
    command: str,
    rest: str,
    normalized: str,
    raw_text: str,
    groups: list[str],
) -> AssistantCommand:
    dispatched = _dispatch_command(command, rest, raw_text, groups, unknown_as_search=False)
    if dispatched.kind != "unknown":
        return dispatched

    if "日报" in normalized:
        group_name, date_text, remaining, scroll_count = _parse_digest_rest(normalized, groups)
        return AssistantCommand(
            "digest",
            raw_text,
            date_text=date_text,
            group_name=group_name,
            query=remaining,
            scroll_count=scroll_count,
        )

    group_name, query = _parse_search_rest(normalized, groups)
    return AssistantCommand("search", raw_text, group_name=group_name, query=query)


def _dispatch_command(
    command: str,
    rest: str,
    raw_text: str,
    groups: list[str],
    unknown_as_search: bool,
) -> AssistantCommand:
    command_key = command.lower()
    if command_key in _DIGEST_COMMANDS:
        group_name, date_text, remaining, scroll_count = _parse_digest_rest(rest, groups)
        return AssistantCommand(
            "digest",
            raw_text,
            date_text=date_text,
            group_name=group_name,
            query=remaining,
            scroll_count=scroll_count,
        )
    if command_key in _SEARCH_COMMANDS:
        group_name, query = _parse_search_rest(rest, groups)
        return AssistantCommand("search", raw_text, group_name=group_name, query=query)
    if command_key in _GROUP_COMMANDS:
        return AssistantCommand("group", raw_text, group_name=rest.strip() or None)
    if command_key in _MEMORY_COMMANDS:
        return AssistantCommand("memory", raw_text, date_text=_extract_date_token(rest))
    if command_key in _SCHEDULE_COMMANDS:
        return AssistantCommand("schedule", raw_text, query=rest or None)
    if command_key in _STATUS_COMMANDS:
        return AssistantCommand("status", raw_text)
    if command_key in _CONTEXT_COMMANDS:
        return AssistantCommand("context", raw_text, message_id=_parse_message_id(rest))
    if command_key in _HELP_COMMANDS:
        return AssistantCommand("status", raw_text)
    if unknown_as_search:
        group_name, query = _parse_search_rest(f"{command} {rest}".strip(), groups)
        return AssistantCommand("search", raw_text, group_name=group_name, query=query)
    return AssistantCommand("unknown", raw_text, query=rest)


def _parse_digest_rest(rest: str, groups: list[str]) -> tuple[str | None, str | None, str | None, int | None]:
    group_name = _extract_group(rest, groups)
    remaining = rest.replace(group_name, "", 1).strip() if group_name else rest.strip()
    command, command_rest = _split_word_command(remaining)
    if command.lower() in _DIGEST_COMMANDS:
        remaining = command_rest

    date_tokens: list[str] = []
    scroll_tokens: list[int] = []
    unknown_tokens: list[str] = []
    for token in remaining.split():
        round_match = _ROUND_TOKEN_RE.fullmatch(token)
        if round_match:
            scroll_tokens.append(int(round_match.group("count")))
        elif _looks_like_date_token(token):
            date_tokens.append(token)
        else:
            unknown_tokens.append(token)

    invalid = unknown_tokens or len(date_tokens) > 1 or len(scroll_tokens) > 1 or (date_tokens and scroll_tokens)
    if invalid:
        return group_name, None, remaining or "invalid", None
    if date_tokens:
        return group_name, date_tokens[0], None, None
    if scroll_tokens:
        return group_name, None, None, scroll_tokens[0]
    return group_name, None, None, None


def _parse_search_rest(rest: str, groups: list[str]) -> tuple[str | None, str | None]:
    group_name = _extract_group(rest, groups)
    if group_name:
        idx = rest.find(group_name)
        query = (rest[:idx] + rest[idx + len(group_name):]).strip()
    else:
        query = rest.strip()
    query = _strip_invisible_chars(query).strip()
    return group_name, query or None


def _strip_invisible_chars(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Mn"))


def _strip_prefix(text: str) -> tuple[str, str]:
    stripped = text.lstrip()
    if stripped.startswith("#"):
        return "#", stripped
    lowered = stripped.lower()
    for prefix in _PREFIXES:
        if lowered == prefix:
            return prefix, ""
        if lowered.startswith(prefix + " "):
            return prefix, stripped[len(prefix):].strip()
    return "", stripped


def _split_word_command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    command = parts[0] if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""
    return command, rest


def _split_slash_command(text: str) -> tuple[str, str]:
    command, rest = _split_word_command(text[1:].strip())
    return command or "帮助", rest


def _extract_group(text: str, groups: list[str]) -> str | None:
    matches = [group for group in groups if group and group in text]
    if not matches:
        return None
    return max(matches, key=len)


def _extract_date_token(text: str) -> str | None:
    for token in text.split():
        if _looks_like_date_token(token):
            return token
    return None


def _parse_message_id(text: str) -> int | None:
    match = _REF_RE.search(text)
    return int(match.group("id")) if match else None


def _looks_like_date_token(text: str) -> bool:
    return text.lower() in _DATE_TOKENS or bool(_ISO_DATE_RE.fullmatch(text))
