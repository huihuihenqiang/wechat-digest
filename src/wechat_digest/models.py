from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re


_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ChatMessage:
    group_name: str
    sender: str
    content: str
    msg_type: str = "text"
    msg_time: datetime | None = None
    first_seen_at: datetime | None = None
    raw_id: str | None = None

    @property
    def normalized_content(self) -> str:
        return normalize_text(self.content)

    @property
    def content_hash(self) -> str:
        return message_hash(self)


def normalize_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def ensure_aware_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def message_hash(message: ChatMessage) -> str:
    content = normalize_text(message.content)
    raw_id = message.raw_id or ""
    if raw_id:
        key = f"{message.group_name}|{raw_id}"
    else:
        time_part = ""
        if message.msg_time is not None:
            time_part = ensure_aware_utc(message.msg_time).replace(second=0, microsecond=0).isoformat()
        key = f"{message.group_name}|{message.sender}|{message.msg_type}|{content}|{time_part}"
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


@dataclass(frozen=True)
class DigestRecord:
    digest_date: str
    generated_at: datetime
    content: str
    sent_at: datetime | None = None
