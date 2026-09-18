import tempfile
import unittest
from pathlib import Path

from qqbot.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.database_path = root / "memory.db"
        self.people_path = root / "people.yaml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_store(self) -> MemoryStore:
        store = MemoryStore(self.database_path, self.people_path)
        store.initialize()
        return store

    def test_seed_maps_multiple_qq_ids_to_one_person(self) -> None:
        self.people_path.write_text(
            """
people:
  alice:
    name: 小爱
    qq_ids: ["10001", "10002"]
    aliases: ["爱姐"]
""".strip(),
            encoding="utf-8",
        )

        store = self.make_store()

        first = store.get_person_by_qq(10001)
        second = store.get_person_by_qq(10002)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(first.person_id, "alice")
        self.assertEqual(first.aliases, ("爱姐",))

    def test_admin_memory_is_claim_backed_and_group_scoped(self) -> None:
        store = self.make_store()
        person = store.ensure_person_for_account(10001, "小爱")

        entry = store.add_memory(
            person.person_id,
            "喜欢喝无糖可乐",
            group_id=1,
        )

        claim = store.claim_store.get_claim(entry.memory_id)
        self.assertIsNotNone(claim)
        self.assertEqual(claim.origin, "admin")
        self.assertIn("喜欢喝无糖可乐", store.search_context(1, "无糖可乐"))
        self.assertNotIn("喜欢喝无糖可乐", store.search_context(2, "无糖可乐"))
        self.assertNotIn("喜欢喝无糖可乐", store.prompt_context(10001))

    def test_rebinding_seeded_identity_reassigns_claims(self) -> None:
        store = self.make_store()
        generated = store.ensure_person_for_account(10001, "小爱")
        store.add_memory(
            generated.person_id,
            "喜欢猫",
            group_id=1,
        )
        self.people_path.write_text(
            """
people:
  alice:
    name: 小爱
    qq_ids: ["10001"]
    aliases: []
""".strip(),
            encoding="utf-8",
        )

        store.sync_people_file()

        person = store.get_person_by_qq(10001)
        self.assertIsNotNone(person)
        self.assertEqual(person.person_id, "alice")
        self.assertEqual(
            [memory.content for memory in store.list_memories("alice", group_id=1)],
            ["喜欢猫"],
        )

    def test_deleted_claim_is_not_recreated_on_initialize(self) -> None:
        store = self.make_store()
        person = store.ensure_person_for_account(10001, "小爱")
        entry = store.add_memory(
            person.person_id,
            "喜欢猫",
            group_id=1,
        )

        self.assertTrue(store.delete_memory(entry.memory_id))
        store.initialize()

        self.assertEqual(store.list_memories(person.person_id), [])
        self.assertEqual(store.get_person_by_qq(10001), person)


if __name__ == "__main__":
    unittest.main()
