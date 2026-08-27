import unittest
from unittest.mock import AsyncMock

from qqbot.integrations.llm import (
    ConversationStore,
    Cooldown,
    ChatClient,
    extract_response_text,
    extract_tool_calls,
)


class ConversationStoreTests(unittest.TestCase):
    def test_group_history_contains_multiple_speakers(self) -> None:
        store = ConversationStore(max_turns=5)
        store.append_message(1, 10, "xiaoming", "小明", "甲的问题")
        store.append_message(1, 11, "xiaohong", "小红", "乙的补充")

        contents = [message.content for message in store.messages(1)]

        self.assertTrue(
            any("小明" in content and "甲的问题" in content for content in contents)
        )
        self.assertTrue(
            any("小红" in content and "乙的补充" in content for content in contents)
        )
        self.assertEqual(store.messages(2), [])

    def test_only_recent_bot_replies_are_kept(self) -> None:
        store = ConversationStore(max_turns=10, max_assistant_turns=1)
        store.append_turn(1, 10, "xiaoming", "小明", "问题1", "回答1")
        store.append_turn(1, 11, "xiaohong", "小红", "问题2", "回答2")

        contents = [message.content for message in store.messages(1)]

        self.assertIn("回答2", contents)
        self.assertNotIn("回答1", contents)
        self.assertTrue(any("问题1" in content for content in contents))
        self.assertTrue(any("问题2" in content for content in contents))

    def test_current_message_can_be_excluded(self) -> None:
        store = ConversationStore(max_turns=5)
        store.append_message(1, 10, "xiaoming", "小明", "当前问题", message_id="123")
        self.assertEqual(store.messages(1, exclude_message_id="123"), [])

    def test_current_message_can_be_enriched_with_temporary_observation(self) -> None:
        store = ConversationStore(max_turns=5)
        store.append_message(
            1,
            10,
            "xiaoming",
            "小明",
            "[图片]这是什么",
            message_id="123",
        )

        changed = store.enrich_message(1, "123", "[临时图片观察]一只猫")
        contents = [message.content for message in store.messages(1)]

        self.assertTrue(changed)
        self.assertTrue(any("一只猫" in content for content in contents))
        self.assertFalse(store.enrich_message(1, "missing", "不应写入"))

    def test_history_expires_after_idle_timeout(self) -> None:
        store = ConversationStore(max_turns=2, max_idle_seconds=12 * 60 * 60)
        store.append_turn(
            1,
            10,
            "xiaoming",
            "小明",
            "旧话题",
            "旧回答",
            now=100,
        )

        self.assertEqual(
            store.messages(1, now=100 + 12 * 60 * 60),
            [],
        )

    def test_history_survives_date_change_within_idle_timeout(self) -> None:
        store = ConversationStore(max_turns=2, max_idle_seconds=12 * 60 * 60)
        store.append_turn(
            1,
            10,
            "xiaoming",
            "小明",
            "23点59分的话题",
            "回答",
            now=100,
        )

        contents = [message.content for message in store.messages(1, now=100 + 2 * 60)]

        self.assertTrue(any("23点59分的话题" in content for content in contents))

    def test_fresh_group_message_keeps_group_history_alive(self) -> None:
        store = ConversationStore(max_turns=6, max_idle_seconds=60)
        store.append_turn(1, 10, "xiaoming", "小明", "旧话题", "回答1", now=100)
        store.append_turn(1, 11, "xiaohong", "小红", "新话题", "回答2", now=150)

        contents = [message.content for message in store.messages(1, now=161)]
        self.assertTrue(any("旧话题" in content for content in contents))
        self.assertTrue(any("新话题" in content for content in contents))

    def test_clear_session_removes_only_one_users_turns(self) -> None:
        store = ConversationStore(max_turns=3)
        store.append_turn(1, 10, "xiaoming", "小明", "a", "b")
        store.append_turn(1, 12, "xiaoming", "小明", "e", "f")
        store.append_turn(1, 11, "xiaohong", "小红", "c", "d")

        self.assertTrue(store.clear_accounts(1, {10, 12}))
        contents = [message.content for message in store.messages(1)]
        self.assertFalse(
            any(
                content.endswith("：a") or content.endswith("：e")
                for content in contents
            )
        )
        self.assertTrue(any(content.endswith("：c") for content in contents))

    def test_clear_group_does_not_affect_other_groups(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "xiaoming", "小明", "a", "b")
        store.append_turn(1, 11, "xiaohong", "小红", "c", "d")
        store.append_turn(2, 10, "xiaoming", "小明", "e", "f")

        self.assertEqual(store.clear_group(1), 1)
        self.assertEqual(store.messages(1), [])
        self.assertEqual(len(store.messages(2)), 2)

    def test_clear_group_counts_one_person_with_multiple_accounts_once(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "xiaoming", "小明", "a", "b")
        store.append_turn(1, 12, "xiaoming", "小明", "c", "d")

        self.assertEqual(store.clear_group(1), 1)


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

    def test_extract_tool_calls(self) -> None:
        calls = extract_tool_calls(
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query":"北京"}',
                        },
                    }
                ]
            }
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "web_search")
        self.assertEqual(calls[0].arguments, {"query": "北京"})


class ToolCallingTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_tool_and_returns_final_answer(self) -> None:
        client = ChatClient(
            api_key="test",
            base_url="https://example.com",
            model="test-model",
            timeout_seconds=5,
            max_output_tokens=100,
            max_concurrency=1,
        )
        client._post = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query":"北京"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {"choices": [{"message": {"content": "北京今天晴。"}}]},
            ]
        )
        executed: list[tuple[str, object]] = []

        async def execute(name: str, arguments: object) -> str:
            executed.append((name, arguments))
            return "晴，30℃"

        answer = await client.complete_with_tools(
            system_prompt="test",
            history=[],
            prompt="搜索北京",
            tools=[],
            execute_tool=execute,  # type: ignore[arg-type]
        )

        self.assertEqual(answer, "北京今天晴。")
        self.assertEqual(executed, [("web_search", {"query": "北京"})])

if __name__ == "__main__":
    unittest.main()
