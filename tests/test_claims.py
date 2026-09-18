import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qqbot.memory.claims import ClaimStore, parse_operations


class ClaimStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "memory.db"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_store(self) -> ClaimStore:
        store = ClaimStore(self.database_path)
        store.initialize()
        return store

    def add_person_claim(
        self,
        store: ClaimStore,
        *,
        origin: str = "extracted",
        valid_to: str | None = None,
    ):
        return store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id="alice",
            predicate="喜欢",
            object_text="猫",
            asserted_by_person_id="alice",
            kind="preference",
            status="active",
            source_type="self_statement",
            confidence=0.9,
            importance=3,
            valid_to=valid_to,
            origin=origin,
        )

    def test_standalone_database_initializes_at_authoritative_schema(self) -> None:
        store = self.make_store()

        self.assertEqual(store.schema_version(), 1)
        self.assertEqual(store.list_claims(), [])

    def test_serving_excludes_expired_and_rejected_claims(self) -> None:
        store = self.make_store()
        extracted = self.add_person_claim(store)
        admin = self.add_person_claim(store, origin="admin")
        rejected = self.add_person_claim(store)
        store.update_claim_status(rejected.claim_id, "rejected")
        self.add_person_claim(
            store,
            valid_to=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        )

        serving_ids = {
            claim.claim_id
            for claim in store.list_serving_claims(
                subject_person_id="alice",
                group_id=1,
            )
        }

        self.assertEqual(serving_ids, {extracted.claim_id, admin.claim_id})

    def test_failed_extraction_batch_rolls_back_new_and_modified_claims(self) -> None:
        store = self.make_store()
        original = self.add_person_claim(store)
        snapshot = store.snapshot_claim(original.claim_id)
        self.assertIsNotNone(snapshot)
        batch_id = store.start_extraction_batch(1, 1, 20)
        created = store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id="alice",
            predicate="居住地",
            object_text="北京",
            asserted_by_person_id="alice",
            kind="profile",
            status="active",
            source_type="self_statement",
            confidence=0.9,
            importance=3,
            extraction_batch_id=batch_id,
        )
        store.update_claim_status(original.claim_id, "disputed")
        store.add_evidence(
            original.claim_id,
            10,
            asserted_by_person_id="alice",
            evidence_type="dispute",
            extraction_batch_id=batch_id,
        )

        store.finish_extraction_batch(
            batch_id,
            status="failed",
            operation_count=2,
            applied_count=0,
            raw_response="bad",
            error="test",
            restore_snapshots=(snapshot,),
        )

        self.assertIsNone(store.get_claim(created.claim_id))
        self.assertEqual(store.get_claim(original.claim_id).status, "active")
        self.assertEqual(store.evidence_for_claim(original.claim_id), [])

    def test_replace_claim_preserves_history(self) -> None:
        store = self.make_store()
        old = self.add_person_claim(store)
        new = store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id="alice",
            predicate="喜欢",
            object_text="狗",
            asserted_by_person_id="alice",
            kind="preference",
            status="active",
            source_type="self_statement",
            confidence=0.9,
            importance=3,
        )

        self.assertTrue(store.replace_claim(old.claim_id, new.claim_id))
        replaced = store.get_claim(old.claim_id)
        self.assertEqual(replaced.status, "superseded")
        self.assertEqual(replaced.superseded_by_claim_id, new.claim_id)

    def test_parse_operations_normalizes_common_field_key_styles(self) -> None:
        operations = parse_operations(
            """{"operations":[{
                "operation":"insert",
                "subjectpersonid":"alice",
                "objectText":"猫",
                "source-message-ids":[1]
            }]}"""
        )

        self.assertEqual(operations[0]["subject_person_id"], "alice")
        self.assertEqual(operations[0]["object_text"], "猫")
        self.assertEqual(operations[0]["source_message_ids"], [1])


if __name__ == "__main__":
    unittest.main()
