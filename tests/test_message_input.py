import tempfile
import unittest
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from qqbot.memory import MemoryStore
from qqbot.message_input import resolve_onebot_message


class OneBotMessageResolutionTests(unittest.TestCase):
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
    aliases: ["乙的别名"]
  person_c:
    name: 丙
    qq_ids: ["30001"]
    aliases: []
""".strip(),
            encoding="utf-8",
        )
        self.store = MemoryStore(root / "memory.db", people_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def resolve(
        self,
        message: Message,
        *,
        author_user_id: int = 10001,
    ):
        return resolve_onebot_message(
            message,
            self.store,
            author_user_id=author_user_id,
            author_name="群名片",
            bot_user_id=99999,
        )

    def test_resolves_author_and_mentioned_member_as_distinct_roles(self) -> None:
        message = Message(
            [
                MessageSegment.at(99999),
                MessageSegment.text(" 谁是"),
                MessageSegment.at(20002),
            ]
        )

        resolved = self.resolve(message)
        context = resolved.system_context()

        self.assertEqual(resolved.author.identity_key, "person:person_a")
        self.assertEqual(resolved.prompt_text, "谁是@成员1")
        self.assertEqual(resolved.log_text, "谁是@乙")
        self.assertEqual(
            [mention.identity_key for mention in resolved.mentions],
            ["person:person_b"],
        )
        self.assertIn("作者：甲", context)
        self.assertIn("@成员1：乙", context)
        self.assertIn("别名：乙的别名", context)
        self.assertIn("该真人绑定2个QQ账号", context)
        self.assertNotIn("@成员1：丙", context)

    def test_reverse_direction_does_not_swap_author_and_target(self) -> None:
        message = Message(
            [
                MessageSegment.text("请介绍"),
                MessageSegment.at(10002),
            ]
        )

        resolved = self.resolve(message, author_user_id=20001)
        context = resolved.system_context()

        self.assertIn("作者：乙", context)
        self.assertIn("@成员1：甲", context)

    def test_two_accounts_of_same_person_share_one_mention_identity(self) -> None:
        message = Message(
            [
                MessageSegment.at(20001),
                MessageSegment.text("和"),
                MessageSegment.at(20002),
            ]
        )

        resolved = self.resolve(message)

        self.assertEqual(len(resolved.mentions), 1)
        self.assertEqual(resolved.prompt_text, "@成员1和@成员1")
        self.assertEqual(resolved.log_text, "@乙和@乙")

    def test_unknown_mention_stays_unknown_instead_of_matching_another_person(
        self,
    ) -> None:
        resolved = self.resolve(Message([MessageSegment.at(40001)]))

        self.assertEqual(len(resolved.mentions), 1)
        self.assertFalse(resolved.mentions[0].configured)
        self.assertEqual(resolved.mentions[0].identity_key, "qq:40001")
        self.assertIn("尚未配置统一身份", resolved.system_context())

    def test_plain_text_that_contains_at_sign_is_not_treated_as_mention(self) -> None:
        resolved = self.resolve(Message("文字里的@乙不是消息段"))

        self.assertEqual(resolved.prompt_text, "文字里的@乙不是消息段")
        self.assertEqual(resolved.log_text, "文字里的@乙不是消息段")
        self.assertEqual(resolved.mentions, ())

    def test_preserves_non_text_segments_as_neutral_placeholders(self) -> None:
        resolved = self.resolve(
            Message(
                [
                    MessageSegment.image("https://example.com/image.jpg"),
                    MessageSegment.text("这张图"),
                ]
            )
        )

        self.assertEqual(resolved.prompt_text, "[图片]这张图")
        self.assertEqual(resolved.log_text, "[图片]这张图")


if __name__ == "__main__":
    unittest.main()
