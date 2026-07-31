import unittest

from qqbot.llm import ChatMessage, ConversationStore, Cooldown, extract_response_text


class ConversationStoreTests(unittest.TestCase):
    def test_history_is_shared_by_group_and_labels_speakers(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "小明", "问题", "回答")

        self.assertEqual(
            store.messages(1),
            [
                ChatMessage(role="user", content="小明（QQ 10）：问题"),
                ChatMessage(role="assistant", content="回答"),
            ],
        )
        self.assertEqual(store.messages(2), [])

    def test_history_keeps_only_configured_turns(self) -> None:
        store = ConversationStore(max_turns=2)
        for index in range(3):
            store.append_turn(1, 10, "小明", f"问题{index}", f"回答{index}")

        self.assertEqual(
            store.messages(1),
            [
                ChatMessage(role="user", content="小明（QQ 10）：问题1"),
                ChatMessage(role="assistant", content="回答1"),
                ChatMessage(role="user", content="小明（QQ 10）：问题2"),
                ChatMessage(role="assistant", content="回答2"),
            ],
        )

    def test_clear_session_removes_only_one_users_turns(self) -> None:
        store = ConversationStore(max_turns=3)
        store.append_turn(1, 10, "小明", "a", "b")
        store.append_turn(1, 12, "小明小号", "e", "f")
        store.append_turn(1, 11, "小红", "c", "d")

        self.assertTrue(store.clear_accounts(1, {10, 12}))
        self.assertEqual(
            store.messages(1),
            [
                ChatMessage(role="user", content="小红（QQ 11）：c"),
                ChatMessage(role="assistant", content="d"),
            ],
        )

    def test_clear_group_does_not_affect_other_groups(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "小明", "a", "b")
        store.append_turn(1, 11, "小红", "c", "d")
        store.append_turn(2, 10, "小明", "e", "f")

        self.assertEqual(store.clear_group(1), 2)
        self.assertEqual(store.messages(1), [])
        self.assertEqual(len(store.messages(2)), 2)


class CooldownTests(unittest.TestCase):
    def test_retry_after_uses_session_key(self) -> None:
        cooldown = Cooldown(seconds=3)
        cooldown.mark_request(1, 10, now=10)

        self.assertEqual(cooldown.retry_after(1, 10, now=11), 2)
        self.assertEqual(cooldown.retry_after(1, 11, now=11), 0)
        self.assertEqual(cooldown.retry_after(1, 10, now=13), 0)


class ResponseParsingTests(unittest.TestCase):
    def test_extract_response_text(self) -> None:
        payload = {"choices": [{"message": {"content": " 你好 "}}]}
        self.assertEqual(extract_response_text(payload), "你好")

    def test_extract_response_text_rejects_empty_response(self) -> None:
        with self.assertRaises(ValueError):
            extract_response_text({"choices": []})


if __name__ == "__main__":
    unittest.main()
