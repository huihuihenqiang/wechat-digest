from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .config import AppConfig
from .models import ChatMessage, ensure_aware_utc
from .storage import DigestStore
from .timeutils import day_window
from .wechat import WeChatClient


@dataclass(frozen=True)
class BackfillResult:
    start: datetime
    end: datetime
    inserted: int
    fetched: int
    scroll_count: int = 0
    earliest_message_time: datetime | None = None


class BackfillCollector:
    def __init__(self, config: AppConfig, store: DigestStore, wechat_client: WeChatClient) -> None:
        self.config = config
        self.store = store
        self.wechat_client = wechat_client

    def collect_date(self, target_date: date, max_scrolls: int | None = None, groups: list[str] | None = None) -> BackfillResult:
        start, end = day_window(target_date, self.config.digest.timezone)
        return self.collect_window(start, end, max_scrolls=max_scrolls, groups=groups)

    def collect_window(self, start: datetime, end: datetime, max_scrolls: int | None = None, groups: list[str] | None = None) -> BackfillResult:
        fetch_limit = self.config.collection.backfill_fetch_limit
        max_scrolls_val = max_scrolls if max_scrolls is not None else self.config.collection.backfill_max_scrolls
        scroll_pause = self.config.collection.scroll_pause_seconds
        target_groups = groups if groups is not None else self.config.wechat.groups

        all_messages: list[ChatMessage] = []
        total_scrolls = 0
        earliest_time: datetime | None = None

        for group_name in target_groups:
            messages, scrolls = self._fetch_group_window(
                group_name, start, end, fetch_limit, max_scrolls_val, scroll_pause,
            )
            all_messages.extend(messages)
            total_scrolls += scrolls
            for m in messages:
                msg_time = ensure_aware_utc(m.msg_time) if m.msg_time else None
                if msg_time and (earliest_time is None or msg_time < earliest_time):
                    earliest_time = msg_time

        inserted = self.store.add_messages(all_messages)
        removed = self.store.dedup_messages()
        self.store.cleanup(self.config.digest.retention_days)
        return BackfillResult(
            start=start, end=end,
            inserted=inserted, fetched=len(all_messages),
            scroll_count=total_scrolls,
            earliest_message_time=earliest_time,
        )

    def _fetch_group_window(
        self, group_name: str, start: datetime, end: datetime,
        fetch_limit: int, max_scrolls: int, scroll_pause: float,
    ) -> tuple[list[ChatMessage], int]:
        fetch_between = getattr(self.wechat_client, "fetch_messages_between", None)
        if callable(fetch_between):
            return fetch_between(
                group_name, start, end, fetch_limit,
                max_scrolls=max_scrolls, scroll_pause_seconds=scroll_pause,
            )

        messages = self.wechat_client.fetch_recent_messages(group_name, limit=fetch_limit)
        return _filter_window(messages, start, end), 0


def _filter_window(messages: list[ChatMessage], start: datetime, end: datetime) -> list[ChatMessage]:
    start_utc = ensure_aware_utc(start)
    end_utc = ensure_aware_utc(end)
    filtered: list[ChatMessage] = []
    for message in messages:
        msg_time = ensure_aware_utc(message.msg_time)
        if start_utc <= msg_time < end_utc:
            filtered.append(message)
    return filtered
