from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from qqbot.sqlite_schema import ensure_column


@dataclass(frozen=True, slots=True)
class GroupMessageRecord:
    message_id: int
    group_id: int
    user_id: int
    person_id: str
    user_name: str
    content: str
    sent_at: str
    speaker_role: str


class GroupDataStore:
    def __init__(
        self,
        database_path: Path,
        *,
        max_messages_per_group: int = 5000,
    ) -> None:
        if max_messages_per_group < 1:
            raise ValueError("max_messages_per_group must be at least 1")
        self.database_path = database_path
        self.max_messages_per_group = max_messages_per_group

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS group_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_group_messages_group_time
                    ON group_messages(group_id, message_id);

                CREATE TABLE IF NOT EXISTS memory_extraction_state (
                    group_id INTEGER PRIMARY KEY,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                """
            )
            ensure_column(
                connection, "group_messages", "person_id", "TEXT NOT NULL DEFAULT ''"
            )
            ensure_column(
                connection,
                "group_messages",
                "speaker_role",
                "TEXT NOT NULL DEFAULT 'human'",
            )

    def record_message(
        self,
        group_id: int,
        user_id: int,
        user_name: str,
        content: str,
        *,
        person_id: str | None = None,
        speaker_role: str = "human",
        sent_at: datetime | None = None,
    ) -> int | None:
        normalized = " ".join(content.split())
        if not normalized:
            return None
        timestamp = (sent_at or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO group_messages(
                    group_id, user_id, person_id, user_name, content, sent_at,
                    speaker_role
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    user_id,
                    person_id or f"qq_{user_id}",
                    user_name.strip() or str(user_id),
                    normalized[:4000],
                    timestamp.isoformat(timespec="seconds"),
                    speaker_role,
                ),
            )
            connection.execute(
                """
                DELETE FROM group_messages
                WHERE group_id = ? AND message_id NOT IN (
                    SELECT message_id
                    FROM group_messages
                    WHERE group_id = ?
                    ORDER BY message_id DESC
                    LIMIT ?
                )
                """,
                (group_id, group_id, self.max_messages_per_group),
            )
            return int(cursor.lastrowid)

    def recent_messages(
        self,
        group_id: int,
        *,
        limit: int,
    ) -> list[GroupMessageRecord]:
        if limit < 1:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, group_id, user_id, person_id, user_name,
                       content, sent_at, speaker_role
                FROM (
                    SELECT message_id, group_id, user_id, person_id, user_name,
                           content, sent_at, speaker_role
                    FROM group_messages
                    WHERE group_id = ?
                    ORDER BY message_id DESC
                    LIMIT ?
                )
                ORDER BY message_id
                """,
                (group_id, limit),
            ).fetchall()
        return [
            GroupMessageRecord(
                message_id=int(row["message_id"]),
                group_id=int(row["group_id"]),
                user_id=int(row["user_id"]),
                person_id=str(row["person_id"]),
                user_name=str(row["user_name"]),
                content=str(row["content"]),
                sent_at=str(row["sent_at"]),
                speaker_role=str(row["speaker_role"]),
            )
            for row in rows
        ]

    def clear_messages(self, group_id: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM group_messages WHERE group_id = ?",
                (group_id,),
            )
        return cursor.rowcount

    def unprocessed_human_messages(
        self,
        group_id: int,
        *,
        limit: int,
    ) -> list[GroupMessageRecord]:
        with self._connect() as connection:
            state = connection.execute(
                "SELECT last_message_id FROM memory_extraction_state "
                "WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            after_id = int(state["last_message_id"]) if state else 0
            rows = connection.execute(
                """
                SELECT message_id, group_id, user_id, person_id, user_name,
                       content, sent_at, speaker_role
                FROM group_messages
                WHERE group_id = ? AND message_id > ? AND speaker_role = 'human'
                ORDER BY message_id
                LIMIT ?
                """,
                (group_id, after_id, limit),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def mark_memory_processed(self, group_id: int, message_id: int) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_extraction_state(
                    group_id, last_message_id, updated_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    last_message_id = MAX(last_message_id, excluded.last_message_id),
                    updated_at = excluded.updated_at
                """,
                (group_id, message_id, now),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> GroupMessageRecord:
        return GroupMessageRecord(
            message_id=int(row["message_id"]),
            group_id=int(row["group_id"]),
            user_id=int(row["user_id"]),
            person_id=str(row["person_id"]),
            user_name=str(row["user_name"]),
            content=str(row["content"]),
            sent_at=str(row["sent_at"]),
            speaker_role=str(row["speaker_role"]),
        )
