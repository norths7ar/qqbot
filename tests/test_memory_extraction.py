import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from qqbot.memory import MemoryStore
from qqbot.memory.extraction import MemoryExtractor
from qqbot.storage.group_data import GroupDataStore


class MemoryExtractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.memory_store = MemoryStore(root / "memory.db", root / "people.yaml")
        self.memory_store.initialize()
        self.claim_store = self.memory_store.claim_store
        self.group_store = GroupDataStore(root / "group.db")
        self.group_store.initialize()
        self.alice = self.memory_store.ensure_person_for_account(10, "甲")
        self.bob = self.memory_store.ensure_person_for_account(11, "乙")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def extractor_for(self, response: object, **kwargs: object) -> MemoryExtractor:
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            side_effect=response if isinstance(response, Exception) else None,
            return_value=None if isinstance(response, Exception) else response,
        )
        return MemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
            **kwargs,  # type: ignore[arg-type]
        )

    async def test_self_statement_and_third_party_claim_have_safe_statuses(
        self,
    ) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "我喜欢猫", person_id=self.alice.person_id
        )
        second = self.group_store.record_message(
            1, 11, "乙", "甲天天哭", person_id=self.bob.person_id
        )
        response = json.dumps(
            {
                "operations": [
                    {
                        "operation": "insert",
                        "scope": "person",
                        "subject_person_id": self.alice.person_id,
                        "predicate": "喜欢",
                        "object_text": "猫",
                        "kind": "preference",
                        "source_message_ids": [first],
                    },
                    {
                        "operation": "insert",
                        "scope": "person",
                        "subject_person_id": self.alice.person_id,
                        "predicate": "经常",
                        "object_text": "哭",
                        "kind": "profile",
                        "source_message_ids": [second],
                    },
                ]
            },
            ensure_ascii=False,
        )

        result = await self.extractor_for(response).process_available(1)

        self.assertEqual(result.processed_messages, 2)
        claims = sorted(
            self.claim_store.list_claims(limit=20),
            key=lambda claim: claim.claim_id,
        )
        self.assertEqual([claim.origin for claim in claims], ["extracted", "extracted"])
        self.assertEqual([claim.status for claim in claims], ["active", "candidate"])
        self.assertEqual(
            [claim.asserted_by_person_id for claim in claims],
            [self.alice.person_id, self.bob.person_id],
        )

    async def test_invalid_subject_is_rejected_and_batch_records_reason(self) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "普通发言", person_id=self.alice.person_id
        )
        second = self.group_store.record_message(
            1, 11, "乙", "普通发言", person_id=self.bob.person_id
        )
        response = json.dumps(
            {
                "operations": [
                    {
                        "operation": "insert",
                        "scope": "person",
                        "subject_person_id": "not_in_batch",
                        "predicate": "身份",
                        "object_text": "虚构人物",
                        "kind": "profile",
                        "source_message_ids": [first, second],
                    }
                ]
            }
        )

        result = await self.extractor_for(response).process_available(1)

        self.assertEqual(result.applied_count, 0)
        self.assertEqual(
            result.rejection_reasons,
            ("operation[0]: person subject is absent from this batch",),
        )
        batch = self.claim_store.list_extraction_batches(limit=1)[0]
        self.assertEqual(batch.rejection_reasons, result.rejection_reasons)

    async def test_failed_call_does_not_advance_group_memory_cursor(self) -> None:
        self.group_store.record_message(
            1, 10, "甲", "第一条", person_id=self.alice.person_id
        )
        self.group_store.record_message(
            1, 11, "乙", "第二条", person_id=self.bob.person_id
        )

        with self.assertRaises(RuntimeError):
            await self.extractor_for(RuntimeError("boom")).process_available(1)

        self.assertEqual(
            len(self.group_store.unprocessed_human_messages(1, limit=10)), 2
        )
        self.assertEqual(self.claim_store.list_claims(), [])
        self.assertEqual(self.claim_store.list_extraction_batches()[0].status, "failed")

    async def test_extraction_uses_its_own_output_budget(self) -> None:
        self.group_store.record_message(
            1, 10, "甲", "第一条", person_id=self.alice.person_id
        )
        self.group_store.record_message(
            1, 11, "乙", "第二条", person_id=self.bob.person_id
        )
        extractor = self.extractor_for(
            '{"operations": []}',
            max_output_tokens=8192,
        )

        await extractor.process_available(1)

        self.assertEqual(
            extractor.client.complete.await_args.kwargs["max_output_tokens"],
            8192,
        )

    async def test_self_statement_can_supersede_trusted_claim(self) -> None:
        old = self.claim_store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id=self.alice.person_id,
            predicate="居住地",
            object_text="北京",
            asserted_by_person_id=self.alice.person_id,
            kind="profile",
            status="active",
            source_type="self_statement",
            confidence=0.9,
            importance=3,
            origin="admin",
        )
        first = self.group_store.record_message(
            1, 10, "甲", "我已经搬到上海了", person_id=self.alice.person_id
        )
        self.group_store.record_message(
            1, 11, "乙", "恭喜", person_id=self.bob.person_id
        )
        response = json.dumps(
            {
                "operations": [
                    {
                        "operation": "supersede",
                        "target_claim_id": old.claim_id,
                        "scope": "person",
                        "subject_person_id": self.alice.person_id,
                        "predicate": "居住地",
                        "object_text": "上海",
                        "kind": "profile",
                        "source_message_ids": [first],
                    }
                ]
            },
            ensure_ascii=False,
        )

        result = await self.extractor_for(response).process_available(1)

        self.assertEqual(result.applied_count, 1)
        replaced = self.claim_store.get_claim(old.claim_id)
        self.assertEqual(replaced.status, "superseded")
        replacement = self.claim_store.get_claim(replaced.superseded_by_claim_id or 0)
        self.assertEqual(replacement.object_text, "上海")
        self.assertEqual(replacement.origin, "extracted")

    async def test_group_episode_gets_default_expiration(self) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "周末一起看电影", person_id=self.alice.person_id
        )
        self.group_store.record_message(1, 11, "乙", "好", person_id=self.bob.person_id)
        response = json.dumps(
            {
                "operations": [
                    {
                        "operation": "insert",
                        "scope": "group",
                        "subject_person_id": None,
                        "predicate": "群活动",
                        "object_text": "周末看电影",
                        "kind": "episode",
                        "source_message_ids": [first],
                    }
                ]
            },
            ensure_ascii=False,
        )

        await self.extractor_for(response, episode_ttl_hours=24).process_available(1)

        claim = self.claim_store.list_claims(limit=1)[0]
        expires = datetime.fromisoformat(claim.valid_to or "")
        remaining_hours = (expires - datetime.now(UTC)).total_seconds() / 3600
        self.assertGreater(remaining_hours, 23.9)
        self.assertLessEqual(remaining_hours, 24)

    async def test_person_episode_gets_default_expiration(self) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "今晚在加班", person_id=self.alice.person_id
        )
        self.group_store.record_message(
            1, 11, "乙", "辛苦了", person_id=self.bob.person_id
        )
        response = json.dumps(
            {
                "operations": [
                    {
                        "operation": "insert",
                        "scope": "person",
                        "subject_person_id": self.alice.person_id,
                        "predicate": "activity",
                        "object_text": "今晚在加班",
                        "kind": "episode",
                        "source_message_ids": [first],
                    }
                ]
            }
        )

        await self.extractor_for(response).process_available(1)

        self.assertIsNotNone(self.claim_store.list_claims(limit=1)[0].valid_to)

    async def test_mixed_evidence_keeps_per_message_attribution(self) -> None:
        question = self.group_store.record_message(
            1, 11, "乙", "你在看比赛吗？", person_id=self.bob.person_id
        )
        confirmation = self.group_store.record_message(
            1, 10, "甲", "嗯", person_id=self.alice.person_id
        )
        response = json.dumps(
            {
                "operations": [
                    {
                        "operation": "insert",
                        "scope": "person",
                        "subject_person_id": self.alice.person_id,
                        "predicate": "activity",
                        "object_text": "在看比赛",
                        "kind": "episode",
                        "source_message_ids": [question, confirmation],
                    }
                ]
            }
        )

        await self.extractor_for(response).process_available(1)

        claim = self.claim_store.list_claims(limit=1)[0]
        evidence = self.claim_store.evidence_for_claim(claim.claim_id)
        self.assertEqual(claim.asserted_by_person_id, self.alice.person_id)
        self.assertEqual(
            [item.evidence_type for item in evidence],
            ["third_party", "self_statement"],
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


if __name__ == "__main__":
    unittest.main()
