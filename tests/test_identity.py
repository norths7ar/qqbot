import tempfile
import unittest
from pathlib import Path

from qqbot.identity import (
    canonical_speaker_name,
    format_group_roster,
    lookup_group_member,
)
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

    def test_lookup_resolves_configured_alias_to_unified_identity(self) -> None:
        members = [
            {
                "user_id": 10001,
                "card": "群名片甲",
                "nickname": "QQ昵称甲",
                "role": "member",
            }
        ]

        result = lookup_group_member(self.store, members, "甲的别名")

        self.assertIn("精确且唯一", result)
        self.assertIn("身份资料：甲", result)
        self.assertIn("别名：甲的别名", result)

    def test_lookup_resolves_current_card_and_qq_nickname(self) -> None:
        members = [
            {
                "user_id": 20001,
                "card": "今日群名片",
                "nickname": "长期QQ昵称",
                "role": "member",
            }
        ]

        card_result = lookup_group_member(self.store, members, "今日群名片")
        nickname_result = lookup_group_member(self.store, members, "长期QQ昵称")

        self.assertIn("精确且唯一", card_result)
        self.assertIn("身份资料：乙", card_result)
        self.assertIn("精确且唯一", nickname_result)
        self.assertIn("身份资料：乙", nickname_result)

    def test_lookup_does_not_choose_between_duplicate_current_names(self) -> None:
        members = [
            {"user_id": 30001, "card": "同名", "nickname": "昵称甲"},
            {"user_id": 30002, "card": "同名", "nickname": "昵称乙"},
        ]

        result = lookup_group_member(self.store, members, "同名")

        self.assertIn("多个群成员使用了相同称呼", result)
        self.assertIn("不要自行选择", result)
        self.assertNotIn("精确且唯一", result)

    def test_lookup_treats_common_word_inside_sentence_as_candidate_only(self) -> None:
        members = [
            {
                "user_id": 10001,
                "card": "甲",
                "nickname": "昵称甲",
                "role": "member",
            }
        ]

        result = lookup_group_member(self.store, members, "我觉得甲的别名很好")

        self.assertIn("仅找到包含关系的候选", result)
        self.assertIn("不能据此确认身份", result)
        self.assertNotIn("精确且唯一", result)

    def test_lookup_does_not_guess_unknown_name(self) -> None:
        members = [{"user_id": 10001, "card": "甲", "nickname": "昵称甲"}]

        result = lookup_group_member(self.store, members, "不存在的人")

        self.assertIn("没有找到匹配身份", result)
        self.assertIn("不要猜测", result)


if __name__ == "__main__":
    unittest.main()
