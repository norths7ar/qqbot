import sqlite3
import tempfile
import unittest
from pathlib import Path

from qqbot.memory import MemoryStore
from qqbot.memory.v2 import ClaimStore, parse_operations


class ClaimStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database_path = root / "memory.db"
        self.memory_store = MemoryStore(self.database_path, root / "people.yaml")
        self.memory_store.initialize()
        self.person = self.memory_store.ensure_person_for_account(10, "甲")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_store(self) -> ClaimStore:
        store = ClaimStore(self.database_path)
        store.initialize()
        return store

    def test_standalone_claim_database_initializes_without_v1_tables(self) -> None:
        database_path = Path(self.temporary_directory.name) / "standalone.db"
        store = ClaimStore(database_path)

        store.initialize()

        self.assertEqual(store.schema_version(), 2)
        self.assertEqual(store.list_claims(), [])

    def test_initialization_adds_rejection_reasons_to_existing_batch_table(
        self,
    ) -> None:
        database_path = Path(self.temporary_directory.name) / "version-one.db"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                """
                CREATE TABLE memory_shadow_batches (
                    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    first_message_id INTEGER NOT NULL,
                    last_message_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    operation_count INTEGER NOT NULL DEFAULT 0,
                    applied_count INTEGER NOT NULL DEFAULT 0,
                    raw_response TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(group_id, first_message_id, last_message_id)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        store = ClaimStore(database_path)
        store.initialize()

        connection = sqlite3.connect(database_path)
        try:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(memory_shadow_batches)"
                )
            }
        finally:
            connection.close()
        self.assertIn("rejection_reasons", columns)
        self.assertEqual(store.schema_version(), 2)

    def test_initialization_migrates_v1_rows_idempotently(self) -> None:
        memory = self.memory_store.add_memory(
            self.person.person_id,
            "喜欢养猫",
            created_by="admin",
            group_id=1,
        )
        store = self.make_store()
        store.initialize()

        claims = store.list_claims(limit=10)

        self.assertEqual(store.schema_version(), 2)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].legacy_memory_id, memory.memory_id)
        self.assertEqual(claims[0].object_text, "喜欢养猫")
        self.assertEqual(claims[0].status, "active")
        self.assertEqual(claims[0].source_type, "admin_config")
        self.assertEqual(claims[0].origin, "legacy_v1")

    def test_v1_writes_after_migration_are_mirrored(self) -> None:
        store = self.make_store()

        memory = self.memory_store.add_memory(
            self.person.person_id,
            "住在北京",
            created_by="admin",
            group_id=1,
        )

        claims = store.list_claims(limit=10)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].legacy_memory_id, memory.memory_id)
        self.memory_store.delete_memory(memory.memory_id)
        self.assertEqual(store.list_claims(limit=10), [])

    def test_reinitialization_reconciles_existing_legacy_claim(self) -> None:
        memory = self.memory_store.add_memory(
            self.person.person_id,
            "喜欢养猫",
            created_by="admin",
            group_id=1,
            source_type="admin_config",
        )
        store = self.make_store()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                UPDATE memory_claims
                SET source_type = 'legacy_v1', object_text = 'stale'
                WHERE legacy_scope = 'person' AND legacy_memory_id = ?
                """,
                (memory.memory_id,),
            )
            connection.commit()
        finally:
            connection.close()

        store.initialize()

        claim = store.list_claims(limit=10)[0]
        self.assertEqual(claim.source_type, "admin_config")
        self.assertEqual(claim.object_text, "喜欢养猫")

    def test_person_claim_requires_subject(self) -> None:
        store = self.make_store()

        with self.assertRaises(ValueError):
            store.add_claim(
                scope="person",
                group_id=1,
                subject_person_id=None,
                predicate="喜欢",
                object_text="猫",
                asserted_by_person_id=None,
                kind="preference",
                status="candidate",
                source_type="third_party",
                confidence=0.5,
                importance=2,
            )

    def test_failed_shadow_batch_cleans_claims_without_advancing(self) -> None:
        store = self.make_store()
        batch_id = store.start_shadow_batch(1, 1, 20)
        store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id=self.person.person_id,
            predicate="喜欢",
            object_text="猫",
            asserted_by_person_id=self.person.person_id,
            kind="preference",
            status="active",
            source_type="self_statement",
            confidence=0.9,
            importance=3,
            shadow_batch_id=batch_id,
        )

        store.finish_shadow_batch(
            batch_id,
            status="failed",
            operation_count=1,
            applied_count=0,
            raw_response="bad",
            error="test",
        )

        self.assertIsNone(store.shadow_cursor(1))
        self.assertEqual(
            [
                claim
                for claim in store.list_claims(limit=10)
                if claim.origin == "shadow_v2"
            ],
            [],
        )

    def test_failed_batch_restores_modified_claim_and_removes_evidence(self) -> None:
        store = self.make_store()
        original = store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id=self.person.person_id,
            predicate="喜欢",
            object_text="猫",
            asserted_by_person_id=self.person.person_id,
            kind="preference",
            status="candidate",
            source_type="third_party",
            confidence=0.5,
            importance=2,
        )
        snapshot = store.snapshot_claim(original.claim_id)
        self.assertIsNotNone(snapshot)
        batch_id = store.start_shadow_batch(1, 1, 20)
        store.update_claim_status(original.claim_id, "active")
        store.add_evidence(
            original.claim_id,
            10,
            asserted_by_person_id=self.person.person_id,
            evidence_type="confirm",
            shadow_batch_id=batch_id,
        )

        store.finish_shadow_batch(
            batch_id,
            status="failed",
            operation_count=1,
            applied_count=0,
            raw_response="bad",
            error="test",
            restore_snapshots=(snapshot,),
        )

        restored = store.get_claim(original.claim_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, "candidate")
        self.assertEqual(store.evidence_for_claim(original.claim_id), [])

    def test_replace_claim_preserves_history(self) -> None:
        store = self.make_store()
        old = store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id=self.person.person_id,
            predicate="居住地",
            object_text="北京",
            asserted_by_person_id=self.person.person_id,
            kind="profile",
            status="active",
            source_type="self_statement",
            confidence=0.9,
            importance=3,
        )
        new = store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id=self.person.person_id,
            predicate="居住地",
            object_text="上海",
            asserted_by_person_id=self.person.person_id,
            kind="profile",
            status="active",
            source_type="self_statement",
            confidence=0.9,
            importance=3,
        )

        self.assertTrue(store.replace_claim(old.claim_id, new.claim_id))
        replaced = store.get_claim(old.claim_id)
        self.assertIsNotNone(replaced)
        self.assertEqual(replaced.status, "superseded")
        self.assertEqual(replaced.superseded_by_claim_id, new.claim_id)

    def test_parse_operations_requires_array(self) -> None:
        with self.assertRaises(ValueError):
            parse_operations('{"operations": {}}')

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

    def test_parse_operations_preserves_explicit_canonical_field(self) -> None:
        operations = parse_operations(
            """{"operations":[{
                "operation":"insert",
                "object_text":"canonical",
                "objectText":"alias",
                "unrelatedField":"untouched"
            }]}"""
        )

        self.assertEqual(operations[0]["object_text"], "canonical")
        self.assertEqual(operations[0]["unrelatedField"], "untouched")


if __name__ == "__main__":
    unittest.main()
