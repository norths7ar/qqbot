import tempfile
import unittest
from pathlib import Path

from qqbot.identity import canonical_speaker_name, format_group_roster
from qqbot.memory import MemoryStore


class IdentityFormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        people_path = root / "people.yaml"
        people_path.write_text(
            """
people:
  person_a:
    name: 甲
    qq_ids: ["10001", "10002"]
    aliases: ["甲的别名"]
  person_b:
    name: 乙
    qq_ids: ["20001", "20002"]
    aliases: []
""".strip(),
            encoding="utf-8",
        )
        self.store = MemoryStore(root / "memory.db", people_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_historical_card_is_replaced_with_canonical_name(self) -> None:
        self.assertEqual(
            canonical_speaker_name(self.store, 10002, "很长的旧群名片"),
            "甲",
        )
        self.assertEqual(
            canonical_speaker_name(self.store, 30001, "未配置的人"),
            "未配置的人",
        )

    def test_roster_merges_multiple_accounts_for_one_person(self) -> None:
        members = [
            {
                "user_id": 10001,
                "card": "甲",
                "nickname": "昵称甲",
                "role": "owner",
            },
            {
                "user_id": 10002,
                "card": "长群名片",
                "nickname": "昵称乙",
                "role": "member",
            },
            {
                "user_id": 20001,
                "card": "乙",
                "nickname": "昵称丙",
                "role": "admin",
            },
            {
                "user_id": 20002,
                "card": "乙的另一个群名片",
                "nickname": "昵称丁",
                "role": "member",
            },
        ]

        result = format_group_roster(self.store, members)

        self.assertEqual(result.count("\n1. 甲"), 1)
        self.assertEqual(result.count("\n2. 乙"), 1)
        self.assertIn("别名：甲的别名", result)
        self.assertIn("当前群名片：长群名片", result)
        self.assertIn("当前群名片：乙的另一个群名片", result)
        self.assertEqual(result.count("已合并 2 个群内 QQ 账号"), 2)
        self.assertIn("群角色：群主", result)
        self.assertIn("群角色：管理员", result)


if __name__ == "__main__":
    unittest.main()
