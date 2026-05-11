from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator

from .models import ChatMessage, DigestRecord, StoredMessage, ensure_aware_utc, normalize_links


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
                    content_hash TEXT NOT NULL UNIQUE,
                    links_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(msg_time);
                CREATE INDEX IF NOT EXISTS idx_messages_group_time ON messages(group_name, msg_time);

                CREATE TABLE IF NOT EXISTS digests (
                    digest_date TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sent_at TEXT,
                    PRIMARY KEY (digest_date, scope)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_message_id TEXT,
                    session_key TEXT,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_summary TEXT,
                    error TEXT,
                    received_at TEXT NOT NULL,
                    handled_at TEXT,
                    UNIQUE(source, source_message_id)
                );

                CREATE TABLE IF NOT EXISTS daily_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_date TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_message_ids TEXT NOT NULL DEFAULT '[]',
                    generated_at TEXT NOT NULL,
                    UNIQUE(memory_date, category)
                );

                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    observations TEXT NOT NULL DEFAULT '[]',
                    first_seen_at TEXT NOT NULL,
                    last_updated_at TEXT NOT NULL,
                    UNIQUE(entity_type, entity_name)
                );

                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            _ensure_column(conn, "messages", "links_json", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_digest_scope_schema(conn)

    def add_messages(self, messages: Iterable[ChatMessage]) -> int:
        inserted = 0
        with self.connect() as conn:
            for message in messages:
                content = message.normalized_content
                if not content:
                    continue
                msg_time = ensure_aware_utc(message.msg_time)
                first_seen_at = ensure_aware_utc(message.first_seen_at)
                existing = conn.execute(
                    """
                    SELECT id FROM messages
                    WHERE group_name = ? AND sender = ? AND content = ? AND msg_type = ?
                    LIMIT 1
                    """,
                    (message.group_name, message.sender or "未知", content, message.msg_type or "text"),
                ).fetchone()
                if existing:
                    continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO messages
                        (group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash, links_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.group_name,
                        message.sender or "未知",
                        content,
                        message.msg_type or "text",
                        _to_iso(msg_time),
                        _to_iso(first_seen_at),
                        message.content_hash,
                        _links_to_json(message.normalized_links),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def get_state(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))

    def get_schedule(self) -> dict:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM state").fetchall()
        result = {"enabled": False, "time": "22:00", "updated_at": ""}
        for row in rows:
            if row["key"] == "enabled":
                result["enabled"] = row["value"] == "true"
            elif row["key"] == "time":
                result["time"] = row["value"]
            elif row["key"] == "schedule_updated_at":
                result["updated_at"] = row["value"]
        return result

    def set_schedule(self, time_str: str, enabled: bool) -> None:
        updated_at = _to_iso(datetime.now(timezone.utc))
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO state (key, value) VALUES ('time', ?)",
                (time_str or "22:00",),
            )
            conn.execute(
                "INSERT OR REPLACE INTO state (key, value) VALUES ('enabled', ?)",
                ("true" if enabled else "false",),
            )
            conn.execute(
                "INSERT OR REPLACE INTO state (key, value) VALUES ('schedule_updated_at', ?)",
                (updated_at,),
            )

    def dedup_messages(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM messages WHERE id NOT IN (
                    SELECT MIN(id) FROM messages
                    GROUP BY group_name, sender, content, msg_type
                )
                """
            )
        return cursor.rowcount

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
                SELECT id, group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash, links_json
                FROM messages
                WHERE msg_time >= ? AND msg_time < ?{group_clause}
                ORDER BY msg_time ASC, id ASC
                {limit_clause}
                """,
                params,
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    def get_message(self, message_id: int) -> StoredMessage | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash, links_json
                FROM messages
                WHERE id = ?
                """,
                (message_id,),
            ).fetchone()
        return _row_to_message(row) if row else None

    def get_context(self, message_id: int, before: int = 3, after: int = 3) -> list[StoredMessage]:
        message = self.get_message(message_id)
        if message is None:
            return []
        message_time = _to_iso(message.msg_time)
        with self.connect() as conn:
            before_rows = conn.execute(
                """
                SELECT id, group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash, links_json
                FROM messages
                WHERE group_name = ?
                  AND (msg_time < ? OR (msg_time = ? AND id < ?))
                ORDER BY msg_time DESC, id DESC
                LIMIT ?
                """,
                (message.group_name, message_time, message_time, message_id, before),
            ).fetchall()
            after_rows = conn.execute(
                """
                SELECT id, group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash, links_json
                FROM messages
                WHERE group_name = ?
                  AND (msg_time > ? OR (msg_time = ? AND id > ?))
                ORDER BY msg_time ASC, id ASC
                LIMIT ?
                """,
                (message.group_name, message_time, message_time, message_id, after),
            ).fetchall()
        return [
            *reversed([_row_to_message(row) for row in before_rows]),
            message,
            *[_row_to_message(row) for row in after_rows],
        ]

    def search_messages(
        self,
        query: str,
        groups: list[str] | None = None,
        limit: int = 10,
    ) -> list[StoredMessage]:
        normalized_query = f"%{query.strip()}%"
        if normalized_query == "%%":
            return []
        params: list[object] = [normalized_query, normalized_query, normalized_query, normalized_query]
        group_clause = ""
        if groups:
            placeholders = ", ".join("?" for _ in groups)
            group_clause = f" AND group_name IN ({placeholders})"
            params.extend(groups)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, group_name, sender, content, msg_type, msg_time, first_seen_at, content_hash, links_json
                FROM messages
                WHERE (
                    content LIKE ?
                    OR sender LIKE ?
                    OR group_name LIKE ?
                    OR links_json LIKE ?
                ){group_clause}
                ORDER BY msg_time DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    def count_messages(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM messages").fetchone()
        return int(row["total"])

    def save_digest(self, digest_date: str, content: str, sent_at: datetime | None = None, scope: str = "") -> None:
        generated_at = datetime.now(timezone.utc)
        scope = _normalize_digest_scope(scope)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO digests (digest_date, scope, generated_at, content, sent_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(digest_date, scope) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    content = excluded.content,
                    sent_at = COALESCE(excluded.sent_at, digests.sent_at)
                """,
                (
                    digest_date,
                    scope,
                    _to_iso(generated_at),
                    content,
                    _to_iso(ensure_aware_utc(sent_at)) if sent_at else None,
                ),
            )

    def get_digest(self, digest_date: str, scope: str = "") -> DigestRecord | None:
        scope = _normalize_digest_scope(scope)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT digest_date, scope, generated_at, content, sent_at
                FROM digests
                WHERE digest_date = ? AND scope = ?
                """,
                (digest_date, scope),
            ).fetchone()
            if row is None and not scope:
                row = conn.execute(
                    """
                    SELECT digest_date, scope, generated_at, content, sent_at
                    FROM digests
                    WHERE digest_date = ?
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """,
                    (digest_date,),
                ).fetchone()
        if row is None:
            return None
        return DigestRecord(
            digest_date=row["digest_date"],
            generated_at=_from_iso(row["generated_at"]),
            content=row["content"],
            sent_at=_from_iso(row["sent_at"]) if row["sent_at"] else None,
            scope=row["scope"] if "scope" in row.keys() else "",
        )

    def mark_digest_sent(self, digest_date: str, sent_at: datetime | None = None, scope: str = "") -> None:
        sent_at = sent_at or datetime.now(timezone.utc)
        scope = _normalize_digest_scope(scope)
        with self.connect() as conn:
            conn.execute(
                "UPDATE digests SET sent_at = ? WHERE digest_date = ? AND scope = ?",
                (_to_iso(ensure_aware_utc(sent_at)), digest_date, scope),
            )

    def record_command(
        self,
        source: str,
        content: str,
        source_message_id: str | None = None,
        session_key: str | None = None,
    ) -> int | None:
        received_at = datetime.now(timezone.utc)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO commands
                    (source, source_message_id, session_key, content, status, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, source_message_id, session_key, content, "received", _to_iso(received_at)),
            )
            if cursor.rowcount == 0:
                return None
            return int(cursor.lastrowid)

    def finish_command(self, command_id: int, result_summary: str | None = None, error: str | None = None) -> None:
        status = "failed" if error else "handled"
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE commands
                SET status = ?, result_summary = ?, error = ?, handled_at = ?
                WHERE id = ?
                """,
                (status, result_summary, error, _to_iso(datetime.now(timezone.utc)), command_id),
            )

    def save_daily_memory(self, memory_date: str, category: str, content: str, source_message_ids: list[int] | None = None) -> None:
        generated_at = datetime.now(timezone.utc)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_memories (memory_date, category, content, source_message_ids, generated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(memory_date, category) DO UPDATE SET
                    content = excluded.content,
                    source_message_ids = excluded.source_message_ids,
                    generated_at = excluded.generated_at
                """,
                (
                    memory_date,
                    category,
                    content,
                    json.dumps(source_message_ids or [], ensure_ascii=False),
                    _to_iso(generated_at),
                ),
            )

    def get_daily_memories(self, memory_date: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT memory_date, category, content, source_message_ids, generated_at FROM daily_memories WHERE memory_date = ? ORDER BY category",
                (memory_date,),
            ).fetchall()
        return [{"memory_date": r["memory_date"], "category": r["category"], "content": r["content"], "source_message_ids": json.loads(r["source_message_ids"]), "generated_at": r["generated_at"]} for r in rows]

    def upsert_long_term_memory(self, entity_type: str, entity_name: str, observations: list[str]) -> None:
        now = datetime.now(timezone.utc)
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT observations, first_seen_at FROM long_term_memories WHERE entity_type = ? AND entity_name = ?",
                (entity_type, entity_name),
            ).fetchone()
            if existing:
                existing_obs = json.loads(existing["observations"])
                existing_obs.extend(observations)
                conn.execute(
                    "UPDATE long_term_memories SET observations = ?, last_updated_at = ? WHERE entity_type = ? AND entity_name = ?",
                    (json.dumps(existing_obs, ensure_ascii=False), _to_iso(now), entity_type, entity_name),
                )
            else:
                conn.execute(
                    "INSERT INTO long_term_memories (entity_type, entity_name, observations, first_seen_at, last_updated_at) VALUES (?, ?, ?, ?, ?)",
                    (entity_type, entity_name, json.dumps(observations, ensure_ascii=False), _to_iso(now), _to_iso(now)),
                )

    def get_long_term_memories(self, entity_type: str | None = None) -> list[dict]:
        with self.connect() as conn:
            if entity_type:
                rows = conn.execute(
                    "SELECT entity_type, entity_name, observations, first_seen_at, last_updated_at FROM long_term_memories WHERE entity_type = ? ORDER BY entity_name",
                    (entity_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT entity_type, entity_name, observations, first_seen_at, last_updated_at FROM long_term_memories ORDER BY entity_type, entity_name",
                ).fetchall()
        return [{"entity_type": r["entity_type"], "entity_name": r["entity_name"], "observations": json.loads(r["observations"]), "first_seen_at": r["first_seen_at"], "last_updated_at": r["last_updated_at"]} for r in rows]

    def cleanup(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM messages WHERE msg_time < ?", (_to_iso(cutoff),))
        return cursor.rowcount


def _to_iso(value: datetime) -> str:
    return ensure_aware_utc(value).replace(microsecond=0).isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_digest_scope_schema(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(digests)").fetchall()
    columns = {row["name"] for row in rows}
    primary_key = [row["name"] for row in sorted(rows, key=lambda item: item["pk"]) if row["pk"]]
    if "scope" in columns and primary_key == ["digest_date", "scope"]:
        return

    conn.execute("ALTER TABLE digests RENAME TO digests_old")
    conn.execute(
        """
        CREATE TABLE digests (
            digest_date TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL,
            content TEXT NOT NULL,
            sent_at TEXT,
            PRIMARY KEY (digest_date, scope)
        )
        """
    )
    old_columns = {row["name"] for row in conn.execute("PRAGMA table_info(digests_old)").fetchall()}
    scope_expr = "COALESCE(scope, '')" if "scope" in old_columns else "''"
    conn.execute(
        f"""
        INSERT OR REPLACE INTO digests (digest_date, scope, generated_at, content, sent_at)
        SELECT digest_date, {scope_expr}, generated_at, content, sent_at
        FROM digests_old
        """
    )
    conn.execute("DROP TABLE digests_old")


def _normalize_digest_scope(scope: str | None) -> str:
    return str(scope or "").strip()


def _links_to_json(links: tuple[str, ...] | list[str]) -> str:
    return json.dumps(list(normalize_links(list(links))), ensure_ascii=False)


def _links_from_json(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(loaded, list):
        return ()
    return normalize_links([str(item) for item in loaded])


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
        links=_links_from_json(row["links_json"] if "links_json" in row.keys() else None),
    )
