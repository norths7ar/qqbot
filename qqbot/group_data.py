from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GroupMessageRecord:
    group_id: int
    user_id: int
    user_name: str
    content: str
    sent_at: str


@dataclass(frozen=True, slots=True)
class Reminder:
    reminder_id: int
    group_id: int
    user_id: int
    user_name: str
    content: str
    due_at: str
    created_at: str


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

                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(delivered_at, due_at);
                """
            )

    def record_message(
        self,
        group_id: int,
        user_id: int,
        user_name: str,
        content: str,
        *,
        sent_at: datetime | None = None,
    ) -> None:
        normalized = " ".join(content.split())
        if not normalized:
            return
        timestamp = (sent_at or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO group_messages(
                    group_id, user_id, user_name, content, sent_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    user_id,
                    user_name.strip() or str(user_id),
                    normalized[:4000],
                    timestamp.isoformat(timespec="seconds"),
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
                SELECT group_id, user_id, user_name, content, sent_at
                FROM (
                    SELECT message_id, group_id, user_id, user_name, content, sent_at
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
                group_id=int(row["group_id"]),
                user_id=int(row["user_id"]),
                user_name=str(row["user_name"]),
                content=str(row["content"]),
                sent_at=str(row["sent_at"]),
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

    def create_reminder(
        self,
        group_id: int,
        user_id: int,
        user_name: str,
        content: str,
        due_at: datetime,
    ) -> Reminder:
        normalized = " ".join(content.split())
        if not normalized:
            raise ValueError("提醒内容不能为空")
        due_utc = due_at.astimezone(UTC)
        if due_utc <= datetime.now(UTC):
            raise ValueError("提醒时间必须晚于现在")
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reminders(
                    group_id, user_id, user_name, content, due_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    user_id,
                    user_name.strip() or str(user_id),
                    normalized[:500],
                    due_utc.isoformat(timespec="seconds"),
                    created_at,
                ),
            )
            reminder_id = cursor.lastrowid
        if reminder_id is None:
            raise RuntimeError("failed to create reminder")
        return Reminder(
            reminder_id=reminder_id,
            group_id=group_id,
            user_id=user_id,
            user_name=user_name,
            content=normalized[:500],
            due_at=due_utc.isoformat(timespec="seconds"),
            created_at=created_at,
        )

    def list_reminders(self, group_id: int, user_id: int) -> list[Reminder]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT reminder_id, group_id, user_id, user_name,
                       content, due_at, created_at
                FROM reminders
                WHERE group_id = ? AND user_id = ? AND delivered_at IS NULL
                ORDER BY due_at
                """,
                (group_id, user_id),
            ).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    def cancel_reminder(self, reminder_id: int, group_id: int, user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM reminders
                WHERE reminder_id = ? AND group_id = ? AND user_id = ?
                      AND delivered_at IS NULL
                """,
                (reminder_id, group_id, user_id),
            )
        return cursor.rowcount > 0

    def claim_due_reminders(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[Reminder]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        claimed_at = current.isoformat(timespec="seconds")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT reminder_id, group_id, user_id, user_name,
                       content, due_at, created_at
                FROM reminders
                WHERE delivered_at IS NULL AND due_at <= ?
                ORDER BY due_at
                LIMIT ?
                """,
                (claimed_at, limit),
            ).fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows)
                connection.execute(
                    f"""
                    UPDATE reminders
                    SET delivered_at = ?
                    WHERE reminder_id IN ({placeholders})
                    """,
                    (claimed_at, *(int(row["reminder_id"]) for row in rows)),
                )
        return [self._reminder_from_row(row) for row in rows]

    def restore_reminder(self, reminder_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE reminders SET delivered_at = NULL WHERE reminder_id = ?",
                (reminder_id,),
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
    def _reminder_from_row(row: sqlite3.Row) -> Reminder:
        return Reminder(
            reminder_id=int(row["reminder_id"]),
            group_id=int(row["group_id"]),
            user_id=int(row["user_id"]),
            user_name=str(row["user_name"]),
            content=str(row["content"]),
            due_at=str(row["due_at"]),
            created_at=str(row["created_at"]),
        )
