import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from qqbot.group_data import GroupDataStore
from qqbot.memory import MemoryStore
from qqbot.memory_extraction import MemoryExtractor, parse_extraction_payload


class MemoryExtractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.memory_store = MemoryStore(root / "memory.db", root / "people.yaml")
        self.memory_store.initialize()
        self.group_store = GroupDataStore(root / "group.db")
        self.group_store.initialize()
        self.alice = self.memory_store.ensure_person_for_account(10, "甲")
        self.bob = self.memory_store.ensure_person_for_account(11, "乙")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_self_statement_is_active_and_third_party_claim_is_candidate(
        self,
    ) -> None:
        first_id = self.group_store.record_message(
            1, 10, "甲", "我一直喜欢养猫", person_id=self.alice.person_id
        )
        second_id = self.group_store.record_message(
            1, 11, "乙", "甲天天哭", person_id=self.bob.person_id
        )
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            return_value=(
                '{"person_memories": ['
                f'{{"subject_person_id":"{self.alice.person_id}",'
                f'"kind":"preference","content":"喜欢养猫","source_message_ids":[{first_id}]}},'
                f'{{"subject_person_id":"{self.alice.person_id}",'
                f'"kind":"profile","content":"天天哭","source_message_ids":[{second_id}]}}],'
                '"group_memories": []}'
            )
        )
        extractor = MemoryExtractor(
            client,  # type: ignore[arg-type]
            self.memory_store,
            self.group_store,
            batch_size=2,
        )

        self.assertEqual(await extractor.process_available(1), 2)
        memories = self.memory_store.list_memories(self.alice.person_id, group_id=1)
        self.assertEqual([item.status for item in memories], ["active", "candidate"])
        self.assertEqual(
            [item.source_type for item in memories],
            ["self_statement", "third_party"],
        )

    async def test_bot_messages_are_not_sent_to_extractor(self) -> None:
        self.group_store.record_message(
            1,
            99,
            "BOT",
            "甲天天哭",
            person_id="bot",
            speaker_role="assistant",
        )
        self.group_store.record_message(
            1, 10, "甲", "普通发言", person_id=self.alice.person_id
        )
        messages = self.group_store.unprocessed_human_messages(1, limit=10)
        self.assertEqual([message.content for message in messages], ["普通发言"])

    def test_parser_accepts_json_code_fence(self) -> None:
        payload = parse_extraction_payload(
            '```json\n{"person_memories": [], "group_memories": []}\n```'
        )
        self.assertEqual(payload["person_memories"], [])


if __name__ == "__main__":
    unittest.main()
