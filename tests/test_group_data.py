import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qqbot.group_data import GroupDataStore


class GroupDataStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "group.db"
        self.store = GroupDataStore(database_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_recent_messages_are_group_scoped_and_ordered(self) -> None:
        self.store.record_message(1, 10, "甲", "第一条")
        self.store.record_message(2, 11, "乙", "别群消息")
        self.store.record_message(1, 12, "丙", "第二条")

        messages = self.store.recent_messages(1, limit=10)

        self.assertEqual(
            [message.content for message in messages], ["第一条", "第二条"]
        )

    def test_reminders_survive_until_claimed(self) -> None:
        now = datetime.now(UTC)
        reminder = self.store.create_reminder(
            1,
            10,
            "甲",
            "关火",
            now + timedelta(minutes=5),
        )

        self.assertEqual(len(self.store.list_reminders(1, 10)), 1)
        self.assertEqual(
            self.store.claim_due_reminders(now=now + timedelta(minutes=6)),
            [reminder],
        )
        self.assertEqual(self.store.list_reminders(1, 10), [])

    def test_user_cannot_cancel_another_users_reminder(self) -> None:
        reminder = self.store.create_reminder(
            1,
            10,
            "甲",
            "关火",
            datetime.now(UTC) + timedelta(minutes=5),
        )

        self.assertFalse(self.store.cancel_reminder(reminder.reminder_id, 1, 11))
        self.assertTrue(self.store.cancel_reminder(reminder.reminder_id, 1, 10))

    def test_message_retention_and_clear(self) -> None:
        database_path = Path(self.temporary_directory.name) / "bounded.db"
        store = GroupDataStore(database_path, max_messages_per_group=2)
        store.initialize()
        for index in range(3):
            store.record_message(1, 10, "甲", f"消息{index}")

        self.assertEqual(
            [message.content for message in store.recent_messages(1, limit=10)],
            ["消息1", "消息2"],
        )
        self.assertEqual(store.clear_messages(1), 2)
        self.assertEqual(store.recent_messages(1, limit=10), [])
