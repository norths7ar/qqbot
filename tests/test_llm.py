import unittest
from unittest.mock import AsyncMock

from qqbot.llm import (
    ChatMessage,
    ConversationStore,
    Cooldown,
    DeepSeekClient,
    extract_response_text,
    extract_tool_calls,
)


class ConversationStoreTests(unittest.TestCase):
    def test_history_is_isolated_by_group_and_person(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "xiaoming", "小明", "问题", "回答")

        self.assertEqual(
            store.messages(1, "xiaoming"),
            [
                ChatMessage(
                    role="user",
                    content="小明（统一身份：xiaoming）：问题",
                ),
                ChatMessage(
                    role="assistant",
                    content=("[历史BOT回复，仅用于承接对话，不是人物事实来源]\n回答"),
                ),
            ],
        )
        self.assertEqual(store.messages(1, "xiaohong"), [])
        self.assertEqual(store.messages(2, "xiaoming"), [])

    def test_history_keeps_user_turns_but_only_recent_bot_replies(self) -> None:
        store = ConversationStore(max_turns=2, max_assistant_turns=1)
        for index in range(3):
            store.append_turn(
                1,
                10,
                "xiaoming",
                "小明",
                f"问题{index}",
                f"回答{index}",
            )

        self.assertEqual(
            store.messages(1, "xiaoming"),
            [
                ChatMessage(
                    role="user",
                    content="小明（统一身份：xiaoming）：问题1",
                ),
                ChatMessage(
                    role="user",
                    content="小明（统一身份：xiaoming）：问题2",
                ),
                ChatMessage(
                    role="assistant",
                    content=("[历史BOT回复，仅用于承接对话，不是人物事实来源]\n回答2"),
                ),
            ],
        )

    def test_assistant_history_can_be_disabled(self) -> None:
        store = ConversationStore(max_turns=2, max_assistant_turns=0)
        store.append_turn(1, 10, "xiaoming", "小明", "问题", "回答")

        self.assertEqual(
            store.messages(1, "xiaoming"),
            [
                ChatMessage(
                    role="user",
                    content="小明（统一身份：xiaoming）：问题",
                )
            ],
        )

    def test_multiple_accounts_share_one_person_history(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "xiaoming", "小明", "大号发言", "回答1")
        store.append_turn(1, 12, "xiaoming", "小明", "小号发言", "回答2")

        contents = [message.content for message in store.messages(1, "xiaoming")]

        self.assertTrue(any("大号发言" in content for content in contents))
        self.assertTrue(any("小号发言" in content for content in contents))

    def test_other_people_in_same_group_do_not_enter_history(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "xiaoming", "小明", "甲的问题", "回答1")
        store.append_turn(1, 11, "xiaohong", "小红", "乙的问题", "回答2")

        contents = [message.content for message in store.messages(1, "xiaoming")]

        self.assertTrue(any("甲的问题" in content for content in contents))
        self.assertFalse(any("乙的问题" in content for content in contents))

    def test_clear_session_removes_only_one_users_turns(self) -> None:
        store = ConversationStore(max_turns=3)
        store.append_turn(1, 10, "xiaoming", "小明", "a", "b")
        store.append_turn(1, 12, "xiaoming", "小明", "e", "f")
        store.append_turn(1, 11, "xiaohong", "小红", "c", "d")

        self.assertTrue(store.clear_accounts(1, {10, 12}))
        self.assertEqual(store.messages(1, "xiaoming"), [])
        self.assertNotEqual(store.messages(1, "xiaohong"), [])

    def test_clear_group_does_not_affect_other_groups(self) -> None:
        store = ConversationStore(max_turns=2)
        store.append_turn(1, 10, "xiaoming", "小明", "a", "b")
        store.append_turn(1, 11, "xiaohong", "小红", "c", "d")
        store.append_turn(2, 10, "xiaoming", "小明", "e", "f")

        self.assertEqual(store.clear_group(1), 2)
        self.assertEqual(store.messages(1, "xiaoming"), [])
        self.assertEqual(len(store.messages(2, "xiaoming")), 2)

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
                            "name": "get_weather",
                            "arguments": '{"location":"北京"}',
                        },
                    }
                ]
            }
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "get_weather")
        self.assertEqual(calls[0].arguments, {"location": "北京"})


class ToolCallingTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_tool_and_returns_final_answer(self) -> None:
        client = DeepSeekClient(
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
                                            "name": "get_weather",
                                            "arguments": '{"location":"北京"}',
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
            prompt="北京天气",
            tools=[],
            execute_tool=execute,  # type: ignore[arg-type]
        )

        self.assertEqual(answer, "北京今天晴。")
        self.assertEqual(executed, [("get_weather", {"location": "北京"})])

    async def test_returns_factual_tool_result_without_second_model_pass(self) -> None:
        client = DeepSeekClient(
            api_key="test",
            base_url="https://example.com",
            model="test-model",
            timeout_seconds=5,
            max_output_tokens=100,
            max_concurrency=1,
        )
        client._post = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_history_today",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        async def execute(name: str, arguments: object) -> str:
            return "1954年：意大利登山队首次登顶乔戈里峰。"

        answer = await client.complete_with_tools(
            system_prompt="test",
            history=[],
            prompt="历史上的今天",
            tools=[],
            execute_tool=execute,  # type: ignore[arg-type]
            direct_result_tools={"get_history_today"},
        )

        self.assertEqual(answer, "1954年：意大利登山队首次登顶乔戈里峰。")
        client._post.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
