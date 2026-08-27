import tempfile
import unittest
from pathlib import Path

from qqbot.storage.group_data import GroupDataStore


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

    def test_message_retention_is_bounded(self) -> None:
        database_path = Path(self.temporary_directory.name) / "bounded.db"
        store = GroupDataStore(database_path, max_messages_per_group=2)
        store.initialize()
        for index in range(3):
            store.record_message(1, 10, "甲", f"消息{index}")

        self.assertEqual(
            [message.content for message in store.recent_messages(1, limit=10)],
            ["消息1", "消息2"],
        )

    def test_messages_by_ids_returns_exact_records(self) -> None:
        first = self.store.record_message(1, 10, "甲", "第一条")
        second = self.store.record_message(1, 11, "乙", "第二条")

        messages = self.store.messages_by_ids([second or 0, 999999, first or 0])

        self.assertEqual(set(messages), {first, second})
        self.assertEqual(messages[first or 0].content, "第一条")
