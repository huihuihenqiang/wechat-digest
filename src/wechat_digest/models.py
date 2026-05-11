from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re


_SPACE_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s<>()\"']+|www\.[^\s<>()\"']+")
_MEDIA_LABELS = {
    "image": "图片",
    "img": "图片",
    "photo": "图片",
    "video": "视频",
    "file": "文件",
    "attachment": "附件",
    "voice": "语音",
    "audio": "语音",
    "link": "链接",
}


@dataclass(frozen=True)
class ChatMessage:
    group_name: str
    sender: str
    content: str
    msg_type: str = "text"
    msg_time: datetime | None = None
    first_seen_at: datetime | None = None
    raw_id: str | None = None
    links: tuple[str, ...] = ()

    @property
    def normalized_content(self) -> str:
        return message_display_content(self.content, self.msg_type)

    @property
    def content_hash(self) -> str:
        return message_hash(self)

    @property
    def normalized_links(self) -> tuple[str, ...]:
        return normalize_links((*self.links, *extract_links(self.content)))


def normalize_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def normalize_links(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    links: list[str] = []
    for value in values:
        text = str(value or "").strip().rstrip("，。；,.;")
        if not text or text in seen:
            continue
        seen.add(text)
        links.append(text)
    return tuple(links)


def extract_links(value: str) -> tuple[str, ...]:
    return normalize_links([match.group(0) for match in _URL_RE.finditer(value or "")])


def media_label(msg_type: str) -> str | None:
    normalized_type = normalize_text(msg_type).lower()
    if not normalized_type or normalized_type == "text":
        return None
    return _MEDIA_LABELS.get(normalized_type, normalized_type)


def message_display_content(content: str, msg_type: str = "text") -> str:
    normalized = normalize_text(content)
    if normalized:
        return normalized
    label = media_label(msg_type)
    if label:
        return f"[{label}：未解析]"
    return ""


def ensure_aware_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def message_hash(message: ChatMessage) -> str:
    content = message.normalized_content
    key = f"{message.group_name}|{message.sender}|{message.msg_type}|{content}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredMessage:
    id: int
    group_name: str
    sender: str
    content: str
    msg_type: str
    msg_time: datetime
    first_seen_at: datetime
    content_hash: str
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class DigestRecord:
    digest_date: str
    generated_at: datetime
    content: str
    sent_at: datetime | None = None
    scope: str = ""
