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

    def test_memory_is_visible_only_in_its_group(self) -> None:
        store = self.make_store()
        person = store.ensure_person_for_account(10001, "小爱")
        store.add_memory(
            person.person_id,
            "喜欢喝无糖可乐",
            created_by="admin",
            group_id=1,
        )

        self.assertIn("喜欢喝无糖可乐", store.search_context(1, "无糖可乐"))
        self.assertNotIn("喜欢喝无糖可乐", store.search_context(2, "无糖可乐"))
        self.assertNotIn("喜欢喝无糖可乐", store.prompt_context(10001, 1))

    def test_rebinding_seeded_identity_preserves_existing_memories(self) -> None:
        store = self.make_store()
        generated = store.ensure_person_for_account(10001, "小爱")
        store.add_memory(
            generated.person_id,
            "喜欢猫",
            created_by="admin",
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

    def test_clear_person_memories_keeps_identity(self) -> None:
        store = self.make_store()
        person = store.ensure_person_for_account(10001, "小爱")
        store.add_memory(
            person.person_id,
            "喜欢猫",
            created_by="admin",
            group_id=1,
        )

        self.assertEqual(store.clear_person_memories(person.person_id), 1)
        self.assertEqual(store.list_memories(person.person_id), [])
        self.assertEqual(store.get_person_by_qq(10001), person)

    def test_superseded_memory_is_not_retrieved(self) -> None:
        store = self.make_store()
        person = store.ensure_person_for_account(10001, "小爱")
        old = store.add_memory(
            person.person_id,
            "住在北京",
            created_by="auto_extractor",
            group_id=1,
            source_type="self_statement",
        )
        new = store.add_memory(
            person.person_id,
            "已经搬到上海",
            created_by="auto_extractor",
            group_id=1,
            source_type="self_statement",
        )

        self.assertTrue(
            store.supersede_memory(
                old.memory_id, new.memory_id, person_id=person.person_id
            )
        )
        self.assertNotIn("住在北京", store.search_context(1, "住在哪里"))

    def test_unrelated_active_person_memory_is_not_retrieved(self) -> None:
        store = self.make_store()
        person = store.ensure_person_for_account(10001, "小爱")
        store.add_memory(
            person.person_id,
            "喜欢喝无糖可乐",
            created_by="admin",
            group_id=1,
        )

        result = store.search_context(1, "周末看电影")

        self.assertNotIn("喜欢喝无糖可乐", result)

    def test_importance_only_reranks_relevant_group_memories(self) -> None:
        store = self.make_store()
        store.add_group_memory(1, "群里最近在讨论养猫", importance=1)
        store.add_group_memory(1, "下周末有人准备去爬山", importance=5)

        result = store.search_context(1, "养猫")

        self.assertIn("群里最近在讨论养猫", result)
        self.assertNotIn("下周末有人准备去爬山", result)

    def test_exact_chinese_phrase_ranks_above_partial_overlap(self) -> None:
        store = self.make_store()
        store.add_group_memory(1, "小爱喜欢养猫", importance=1)
        store.add_group_memory(1, "小爱讨厌养猫", importance=1)

        result = store.search_context(1, "小爱喜欢养猫")

        self.assertLess(result.index("小爱喜欢养猫"), result.index("小爱讨厌养猫"))

    def test_group_memory_upsert_returns_stored_values(self) -> None:
        store = self.make_store()
        original = store.add_group_memory(
            1,
            "群里约好周末看电影",
            status="active",
            importance=5,
        )

        updated = store.add_group_memory(
            1,
            "群里约好周末看电影",
            status="candidate",
            importance=1,
        )

        self.assertEqual(updated.memory_id, original.memory_id)
        self.assertEqual(updated.importance, 5)
        self.assertEqual(updated.status, "active")
        self.assertEqual(updated.created_at, original.created_at)


if __name__ == "__main__":
    unittest.main()
