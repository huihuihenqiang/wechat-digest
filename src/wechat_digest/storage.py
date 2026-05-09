from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator

from .models import ChatMessage, DigestRecord, StoredMessage, ensure_aware_utc


class DigestStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_name TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    content TEXT NOT NULL,
                    msg_type TEXT NOT NULL DEFAULT 'text',
                    msg_time TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(msg_time);
                CREATE INDEX IF NOT EXISTS idx_messages_group_time ON messages(group_name, msg_time);

                CREATE TABLE IF NOT EXISTS digests (
                    digest_date TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sent_at TEXT
                );
                """
            )

    def add_messages(self, messages: Iterable[ChatMessage]) -> int:
        inserted = 0
        with self.connect() as conn:
            for message in messages:
                content = message.normalized_content
                if not content:
                    continue
                msg_time = ensure_aware_utc(message.msg_time)
                first_seen_at = ensure_aware_utc(message.first_seen_at)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO messages
                        (group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.group_name,
                        message.sender or "未知",
                        content,
                        message.msg_type or "text",
                        _to_iso(msg_time),
                        _to_iso(first_seen_at),
                        message.content_hash,
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def get_messages(
        self,
        start: datetime,
        end: datetime,
        groups: list[str] | None = None,
        limit: int | None = None,
    ) -> list[StoredMessage]:
        start_iso = _to_iso(ensure_aware_utc(start))
        end_iso = _to_iso(ensure_aware_utc(end))
        params: list[object] = [start_iso, end_iso]
        group_clause = ""
        if groups:
            placeholders = ", ".join("?" for _ in groups)
            group_clause = f" AND group_name IN ({placeholders})"
            params.extend(groups)
        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash
                FROM messages
                WHERE msg_time >= ? AND msg_time < ?{group_clause}
                ORDER BY msg_time ASC, id ASC
                {limit_clause}
                """,
                params,
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    def count_messages(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM messages").fetchone()
        return int(row["total"])

    def save_digest(self, digest_date: str, content: str, sent_at: datetime | None = None) -> None:
        generated_at = datetime.now(timezone.utc)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO digests (digest_date, generated_at, content, sent_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(digest_date) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    content = excluded.content,
                    sent_at = COALESCE(excluded.sent_at, digests.sent_at)
                """,
                (
                    digest_date,
                    _to_iso(generated_at),
                    content,
                    _to_iso(ensure_aware_utc(sent_at)) if sent_at else None,
                ),
            )

    def get_digest(self, digest_date: str) -> DigestRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT digest_date, generated_at, content, sent_at FROM digests WHERE digest_date = ?",
                (digest_date,),
            ).fetchone()
        if row is None:
            return None
        return DigestRecord(
            digest_date=row["digest_date"],
            generated_at=_from_iso(row["generated_at"]),
            content=row["content"],
            sent_at=_from_iso(row["sent_at"]) if row["sent_at"] else None,
        )

    def mark_digest_sent(self, digest_date: str, sent_at: datetime | None = None) -> None:
        sent_at = sent_at or datetime.now(timezone.utc)
        with self.connect() as conn:
            conn.execute(
                "UPDATE digests SET sent_at = ? WHERE digest_date = ?",
                (_to_iso(ensure_aware_utc(sent_at)), digest_date),
            )

    def cleanup(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM messages WHERE msg_time < ?", (_to_iso(cutoff),))
        return cursor.rowcount


def _to_iso(value: datetime) -> str:
    return ensure_aware_utc(value).replace(microsecond=0).isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        id=int(row["id"]),
        group_name=row["group_name"],
        sender=row["sender"],
        content=row["content"],
        msg_type=row["msg_type"],
        msg_time=_from_iso(row["msg_time"]),
        first_seen_at=_from_iso(row["first_seen_at"]),
        content_hash=row["content_hash"],
    )
