from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import time as time_module

from .config import AppConfig
from .digest import DigestError, DigestGenerator, fallback_digest, split_message
from .storage import DigestStore
from .timeutils import app_timezone, day_window, should_run_digest
from .wechat import WeChatClient


@dataclass(frozen=True)
class DigestResult:
    digest_date: str
    content: str
    message_count: int
    sent: bool
    dry_run: bool


class DigestService:
    def __init__(
        self,
        config: AppConfig,
        store: DigestStore,
        wechat_client: WeChatClient,
        digest_generator: DigestGenerator,
    ) -> None:
        self.config = config
        self.store = store
        self.wechat_client = wechat_client
        self.digest_generator = digest_generator

    def collect_once(self) -> int:
        inserted = 0
        for group_name in self.config.wechat.groups:
            messages = self.wechat_client.fetch_recent_messages(
                group_name,
                limit=self.config.wechat.message_fetch_limit,
            )
            inserted += self.store.add_messages(messages)
        self.store.cleanup(self.config.digest.retention_days)
        return inserted

    def generate_daily_digest(
        self,
        target_date: date,
        dry_run: bool = False,
        allow_fallback: bool = False,
    ) -> DigestResult:
        start, end = day_window(target_date, self.config.digest.timezone)
        messages = self.store.get_messages(start, end, self.config.wechat.groups)
        try:
            content = self.digest_generator.generate(target_date, messages)
        except DigestError:
            if not allow_fallback:
                raise
            content = fallback_digest(target_date, messages)

        sent = False
        digest_date = target_date.isoformat()
        if dry_run:
            return DigestResult(digest_date, content, len(messages), sent=False, dry_run=True)

        self.store.save_digest(digest_date, content)
        for chunk in split_message(content, self.config.wechat.message_chunk_size):
            self.wechat_client.send_message(self.config.wechat.recipient, chunk)
        self.store.mark_digest_sent(digest_date, datetime.now(timezone.utc))
        sent = True
        return DigestResult(digest_date, content, len(messages), sent=sent, dry_run=False)

    def run_forever(self, once: bool = False) -> None:
        tz = app_timezone(self.config.digest.timezone)
        while True:
            inserted = self.collect_once()
            if once:
                print(f"Collected {inserted} new messages.")
                return
            now = datetime.now(tz)
            today = now.date()
            existing = self.store.get_digest(today.isoformat())
            if should_run_digest(now, self.config.digest.time) and not (existing and existing.sent_at):
                self.generate_daily_digest(today, dry_run=False)
            time_module.sleep(self.config.wechat.poll_interval_seconds)
