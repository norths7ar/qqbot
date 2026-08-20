import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from qqbot.group_data import GroupDataStore
from qqbot.memory import MemoryStore
from qqbot.memory_shadow import ShadowMemoryExtractor
from qqbot.memory_v2 import ClaimStore


class ShadowMemoryExtractorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.memory_store = MemoryStore(root / "memory.db", root / "people.yaml")
        self.memory_store.initialize()
        self.claim_store = ClaimStore(root / "memory.db")
        self.claim_store.initialize()
        self.group_store = GroupDataStore(root / "group.db")
        self.group_store.initialize()
        self.alice = self.memory_store.ensure_person_for_account(10, "甲")
        self.bob = self.memory_store.ensure_person_for_account(11, "乙")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_self_statement_and_third_party_claim_keep_distinct_assertors(
        self,
    ) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "我喜欢猫", person_id=self.alice.person_id
        )
        second = self.group_store.record_message(
            1, 11, "乙", "甲天天哭", person_id=self.bob.person_id
        )
        response = {
            "operations": [
                {
                    "operation": "insert",
                    "scope": "person",
                    "subject_person_id": self.alice.person_id,
                    "predicate": "喜欢",
                    "object_text": "猫",
                    "kind": "preference",
                    "confidence": 0.9,
                    "importance": 3,
                    "source_message_ids": [first],
                },
                {
                    "operation": "insert",
                    "scope": "person",
                    "subject_person_id": self.alice.person_id,
                    "predicate": "经常",
                    "object_text": "哭",
                    "kind": "profile",
                    "confidence": 0.5,
                    "importance": 1,
                    "source_message_ids": [second],
                },
            ]
        }
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            return_value=json.dumps(response, ensure_ascii=False)
        )
        extractor = ShadowMemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
            backfill_existing=True,
        )

        result = await extractor.process_available(1)

        self.assertEqual(result.processed_messages, 2)
        claims = sorted(
            [
                claim
                for claim in self.claim_store.list_claims(limit=20)
                if claim.origin == "shadow_v2"
            ],
            key=lambda claim: claim.claim_id,
        )
        self.assertEqual([claim.status for claim in claims], ["active", "candidate"])
        self.assertEqual(
            [claim.asserted_by_person_id for claim in claims],
            [self.alice.person_id, self.bob.person_id],
        )

    async def test_invalid_subject_does_not_create_claim(self) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "普通发言", person_id=self.alice.person_id
        )
        second = self.group_store.record_message(
            1, 11, "乙", "普通发言", person_id=self.bob.person_id
        )
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            return_value=json.dumps(
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
        )
        extractor = ShadowMemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
            backfill_existing=True,
        )

        result = await extractor.process_available(1)

        self.assertEqual(result.applied_count, 0)
        self.assertEqual(
            result.rejection_reasons,
            ("operation[0]: person subject is absent from this batch",),
        )
        batch = self.claim_store.list_shadow_batches(limit=1)[0]
        self.assertEqual(batch.rejection_reasons, result.rejection_reasons)
        self.assertEqual(
            [
                claim
                for claim in self.claim_store.list_claims(limit=20)
                if claim.origin == "shadow_v2"
            ],
            [],
        )

    async def test_compact_model_fields_are_normalized_before_validation(self) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "我喜欢猫", person_id=self.alice.person_id
        )
        self.group_store.record_message(
            1, 11, "乙", "确实", person_id=self.bob.person_id
        )
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            return_value=json.dumps(
                {
                    "operations": [
                        {
                            "operation": "insert",
                            "scope": "person",
                            "subjectpersonid": self.alice.person_id,
                            "predicate": "喜欢",
                            "objecttext": "猫",
                            "kind": "preference",
                            "confidence": 0.9,
                            "importance": 3,
                            "sourcemessageids": [first],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        extractor = ShadowMemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
            backfill_existing=True,
        )

        result = await extractor.process_available(1)

        self.assertEqual(result.applied_count, 1)
        self.assertEqual(result.rejection_reasons, ())
        claim = next(
            claim
            for claim in self.claim_store.list_claims(limit=20)
            if claim.origin == "shadow_v2"
        )
        self.assertEqual(claim.subject_person_id, self.alice.person_id)
        self.assertEqual(claim.object_text, "猫")

    async def test_self_statement_can_supersede_existing_claim(self) -> None:
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
        )
        first = self.group_store.record_message(
            1, 10, "甲", "我已经搬到上海了", person_id=self.alice.person_id
        )
        self.group_store.record_message(
            1, 11, "乙", "恭喜", person_id=self.bob.person_id
        )
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            return_value=json.dumps(
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
                            "confidence": 0.95,
                            "importance": 3,
                            "source_message_ids": [first],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        extractor = ShadowMemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
            backfill_existing=True,
        )

        result = await extractor.process_available(1)

        self.assertEqual(result.applied_count, 1)
        replaced = self.claim_store.get_claim(old.claim_id)
        self.assertIsNotNone(replaced)
        self.assertEqual(replaced.status, "superseded")
        replacement = self.claim_store.get_claim(replaced.superseded_by_claim_id or 0)
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.object_text, "上海")

    async def test_third_party_cannot_supersede_another_person_claim(self) -> None:
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
        )
        first = self.group_store.record_message(
            1, 11, "乙", "甲搬到上海了", person_id=self.bob.person_id
        )
        self.group_store.record_message(
            1, 11, "乙", "真的", person_id=self.bob.person_id
        )
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            return_value=json.dumps(
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
        )
        extractor = ShadowMemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
            backfill_existing=True,
        )

        result = await extractor.process_available(1)

        self.assertEqual(result.applied_count, 0)
        unchanged = self.claim_store.get_claim(old.claim_id)
        self.assertIsNotNone(unchanged)
        self.assertEqual(unchanged.status, "active")
        self.assertIsNone(unchanged.superseded_by_claim_id)

    async def test_first_enable_anchors_at_latest_message_by_default(self) -> None:
        self.group_store.record_message(
            1, 10, "甲", "旧消息一", person_id=self.alice.person_id
        )
        latest = self.group_store.record_message(
            1, 11, "乙", "旧消息二", person_id=self.bob.person_id
        )
        client = unittest.mock.Mock()
        client.complete = AsyncMock(return_value='{"operations": []}')
        extractor = ShadowMemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
        )

        result = await extractor.process_available(1)

        self.assertEqual(result.processed_messages, 0)
        self.assertEqual(self.claim_store.shadow_cursor(1), latest)
        client.complete.assert_not_awaited()

    async def test_group_episode_gets_default_expiration(self) -> None:
        first = self.group_store.record_message(
            1, 10, "甲", "周末一起看电影", person_id=self.alice.person_id
        )
        self.group_store.record_message(1, 11, "乙", "好", person_id=self.bob.person_id)
        client = unittest.mock.Mock()
        client.complete = AsyncMock(
            return_value=json.dumps(
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
        )
        extractor = ShadowMemoryExtractor(
            client,  # type: ignore[arg-type]
            self.claim_store,
            self.group_store,
            batch_size=2,
            backfill_existing=True,
            episode_ttl_hours=24,
        )

        await extractor.process_available(1)

        claim = next(
            claim
            for claim in self.claim_store.list_claims(limit=20)
            if claim.origin == "shadow_v2"
        )
        self.assertIsNotNone(claim.valid_to)
        expires = datetime.fromisoformat(claim.valid_to or "")
        remaining_hours = (expires - datetime.now(UTC)).total_seconds() / 3600
        self.assertGreater(remaining_hours, 23.9)
        self.assertLessEqual(remaining_hours, 24)


if __name__ == "__main__":
    unittest.main()
