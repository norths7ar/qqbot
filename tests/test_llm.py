import unittest
from unittest.mock import AsyncMock

from qqbot.integrations.llm import (
    ChatClient,
    ConversationStore,
    Cooldown,
    ThinkingMode,
    extract_response_text,
    extract_tool_calls,
)


class ConversationStoreTests(unittest.TestCase):
    def test_group_history_contains_multiple_speakers(self) -> None:
        store = ConversationStore(max_turns=5)
        store.append_message(1, "xiaoming", "小明", "甲的问题")
        store.append_message(1, "xiaohong", "小红", "乙的补充")

        contents = [message.content for message in store.messages(1)]

        self.assertTrue(any("小明" in item and "甲的问题" in item for item in contents))
        self.assertTrue(any("小红" in item and "乙的补充" in item for item in contents))
        self.assertEqual(store.messages(2), [])

    def test_only_configured_number_of_recent_bot_replies_are_kept(self) -> None:
        store = ConversationStore(max_turns=10, max_assistant_turns=1)
        store.append_message(1, "xiaoming", "小明", "问题1", now=100)
        store.append_message(1, "bot", "BOT", "回答1", role="assistant", now=100)
        store.append_message(1, "xiaohong", "小红", "问题2", now=150)
        store.append_message(1, "bot", "BOT", "回答2", role="assistant", now=150)

        contents = [message.content for message in store.messages(1)]

        self.assertIn("回答2", contents)
        self.assertNotIn("回答1", contents)
        self.assertTrue(any("问题1" in item for item in contents))
        self.assertTrue(any("问题2" in item for item in contents))

    def test_current_message_can_be_excluded(self) -> None:
        store = ConversationStore(max_turns=5)
        store.append_message(1, "xiaoming", "小明", "当前问题", message_id="123")
        self.assertEqual(store.messages(1, exclude_message_id="123"), [])

    def test_history_expires_after_idle_timeout(self) -> None:
        store = ConversationStore(max_turns=2, max_idle_seconds=60)
        store.append_message(1, "xiaoming", "小明", "旧话题", now=100)

        self.assertEqual(store.messages(1, now=160), [])

    def test_fresh_group_message_keeps_group_history_alive(self) -> None:
        store = ConversationStore(max_turns=3, max_idle_seconds=60)
        store.append_message(1, "xiaoming", "小明", "旧话题", now=100)
        store.append_message(1, "xiaohong", "小红", "新话题", now=150)

        contents = [message.content for message in store.messages(1, now=161)]

        self.assertTrue(any("旧话题" in item for item in contents))
        self.assertTrue(any("新话题" in item for item in contents))


class CooldownTests(unittest.TestCase):
    def test_retry_after_uses_group_and_user_key(self) -> None:
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


class ChatClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(
        self,
        *,
        thinking_mode: ThinkingMode = "provider_default",
    ) -> ChatClient:
        return ChatClient(
            api_key="test",
            base_url="https://example.com",
            model="test-model",
            timeout_seconds=5,
            max_output_tokens=100,
            max_concurrency=1,
            thinking_mode=thinking_mode,
        )

    async def test_complete_returns_raw_text_for_non_chat_consumers(self) -> None:
        client = self.make_client()
        client._post = AsyncMock(  # type: ignore[method-assign]
            return_value={"choices": [{"message": {"content": "**raw**"}}]}
        )

        answer = await client.complete(system_prompt="test", history=[], prompt="test")

        self.assertEqual(answer, "**raw**")
        payload = client._post.await_args.args[0]
        self.assertNotIn("thinking", payload)

    async def test_complete_can_disable_provider_thinking(self) -> None:
        client = self.make_client(thinking_mode="disabled")
        client._post = AsyncMock(  # type: ignore[method-assign]
            return_value={"choices": [{"message": {"content": "[]"}}]}
        )

        await client.complete(system_prompt="test", history=[], prompt="test")

        payload = client._post.await_args.args[0]
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    async def test_executes_tool_and_returns_final_answer(self) -> None:
        client = self.make_client()
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
        self.assertNotIn("thinking", client._post.await_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
