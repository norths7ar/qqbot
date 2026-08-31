import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qqbot.memory.v2 import ClaimStore, parse_operations


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
        origin: str = "online_v2",
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

        self.assertEqual(store.schema_version(), 3)
        self.assertEqual(store.list_claims(), [])

    def test_upgrade_preserves_legacy_shadow_table_but_does_not_use_it(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE memory_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                INSERT INTO memory_schema_migrations VALUES (2, 'old', 'now');
                CREATE TABLE memory_shadow_batches (
                    batch_id INTEGER PRIMARY KEY,
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
                    rejection_reasons TEXT NOT NULL DEFAULT '[]'
                );
                INSERT INTO memory_shadow_batches(
                    batch_id, group_id, first_message_id, last_message_id,
                    status, created_at
                ) VALUES (1, 1, 1, 2, 'completed', 'now');
                """
            )
            connection.commit()
        finally:
            connection.close()

        store = self.make_store()

        connection = sqlite3.connect(self.database_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM memory_shadow_batches"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)
        self.assertEqual(store.schema_version(), 3)

    def test_v1_rows_migrate_once_and_mirror_triggers_are_absent(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE memories (
                    memory_id INTEGER PRIMARY KEY,
                    person_id TEXT NOT NULL,
                    group_id INTEGER,
                    content TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    updated_at TEXT,
                    expires_at TEXT,
                    superseded_by INTEGER
                );
                INSERT INTO memories VALUES (
                    7, 'alice', 1, '喜欢养猫', 'admin', '2026-01-01T00:00:00+00:00',
                    'preference', 'active', 'admin_config',
                    '2026-01-01T00:00:00+00:00', NULL, NULL
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

        store = self.make_store()
        store.initialize()

        claims = store.list_claims(limit=10)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].origin, "legacy_v1")
        self.assertEqual(claims[0].legacy_memory_id, 7)
        connection = sqlite3.connect(self.database_path)
        try:
            triggers = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(triggers, [])

    def test_serving_allows_trusted_origins_and_excludes_shadow_and_expired(
        self,
    ) -> None:
        store = self.make_store()
        online = self.add_person_claim(store)
        admin = self.add_person_claim(store, origin="admin_v2")
        legacy = self.add_person_claim(store, origin="legacy_v1")
        self.add_person_claim(store, origin="shadow_v2")
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

        self.assertEqual(
            serving_ids,
            {online.claim_id, admin.claim_id, legacy.claim_id},
        )

    def test_repairs_online_episode_expiration_and_evidence_attribution(self) -> None:
        store = self.make_store()
        episode = store.add_claim(
            scope="person",
            group_id=1,
            subject_person_id="alice",
            predicate="activity",
            object_text="在看比赛",
            asserted_by_person_id=None,
            kind="episode",
            status="candidate",
            source_type="self_statement",
            confidence=0.8,
            importance=1,
            origin="online_v2",
        )
        store.add_evidence(
            episode.claim_id,
            1,
            asserted_by_person_id="bob",
            evidence_type="self_statement",
        )
        store.add_evidence(
            episode.claim_id,
            2,
            asserted_by_person_id="alice",
            evidence_type="self_statement",
        )
        store.add_evidence(
            episode.claim_id,
            3,
            asserted_by_person_id="bob",
            evidence_type="dispute",
        )

        expiration_count = store.backfill_online_episode_expirations(72)
        evidence_count = store.repair_online_evidence_attribution()

        repaired = store.get_claim(episode.claim_id)
        self.assertEqual(expiration_count, 1)
        self.assertEqual(evidence_count, 2)
        self.assertIsNotNone(repaired)
        self.assertIsNotNone(repaired.valid_to)
        self.assertEqual(repaired.asserted_by_person_id, "alice")
        self.assertEqual(
            [
                item.evidence_type
                for item in store.evidence_for_claim(episode.claim_id)
            ],
            ["third_party", "self_statement", "dispute"],
        )

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
