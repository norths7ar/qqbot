from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Literal

import httpx

from qqbot.plain_text import to_qq_plain_text

type Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    user_id: int
    person_id: str
    user_name: str
    user_message: str
    assistant_message: str

    def user_chat_message(self) -> ChatMessage:
        speaker = f"{self.user_name}（统一身份：{self.person_id}）"
        return ChatMessage(
            role="user",
            content=f"{speaker}：{self.user_message}",
        )

    def assistant_chat_message(self) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=(
                "[历史BOT回复，仅用于承接对话，不是人物事实来源]\n"
                f"{self.assistant_message}"
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]
    raw_arguments: str


type ToolExecutor = Callable[[str, Mapping[str, object]], Awaitable[str]]


class ConversationStore:
    """Keep bounded histories isolated by group and unified person identity."""

    def __init__(
        self,
        max_turns: int,
        max_assistant_turns: int = 1,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        if max_assistant_turns < 0:
            raise ValueError("max_assistant_turns cannot be negative")
        self._max_turns = max_turns
        self._max_assistant_turns = max_assistant_turns
        self._histories: defaultdict[
            tuple[int, str],
            deque[ConversationTurn],
        ] = defaultdict(lambda: deque(maxlen=self._max_turns))

    def messages(self, group_id: int, person_id: str) -> list[ChatMessage]:
        turns = list(self._histories.get((group_id, person_id), ()))
        assistant_start = max(0, len(turns) - self._max_assistant_turns)
        messages: list[ChatMessage] = []
        for index, turn in enumerate(turns):
            messages.append(turn.user_chat_message())
            if index >= assistant_start:
                messages.append(turn.assistant_chat_message())
        return messages

    def append_turn(
        self,
        group_id: int,
        user_id: int,
        person_id: str,
        user_name: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self._histories[(group_id, person_id)].append(
            ConversationTurn(
                user_id=user_id,
                person_id=person_id,
                user_name=user_name,
                user_message=user_message,
                assistant_message=assistant_message,
            )
        )

    def clear_session(self, group_id: int, user_id: int) -> bool:
        return self.clear_accounts(group_id, {user_id})

    def clear_accounts(self, group_id: int, user_ids: Collection[int]) -> bool:
        changed = False
        for key in [key for key in self._histories if key[0] == group_id]:
            history = self._histories[key]
            remaining = [turn for turn in history if turn.user_id not in user_ids]
            if len(remaining) == len(history):
                continue
            changed = True
            if remaining:
                history.clear()
                history.extend(remaining)
            else:
                del self._histories[key]
        return changed

    def clear_group(self, group_id: int) -> int:
        keys = [key for key in self._histories if key[0] == group_id]
        for key in keys:
            del self._histories[key]
        return len(keys)


class Cooldown:
    def __init__(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds cannot be negative")
        self._seconds = seconds
        self._last_request: dict[tuple[int, int], float] = {}

    def retry_after(
        self,
        group_id: int,
        user_id: int,
        *,
        now: float | None = None,
    ) -> float:
        current = time.monotonic() if now is None else now
        key = (group_id, user_id)
        last_request = self._last_request.get(key)
        if last_request is None:
            return 0
        return max(0, self._seconds - (current - last_request))

    def mark_request(
        self,
        group_id: int,
        user_id: int,
        *,
        now: float | None = None,
    ) -> None:
        self._last_request[(group_id, user_id)] = (
            time.monotonic() if now is None else now
        )


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        max_concurrency: int,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_output_tokens = max_output_tokens
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def complete(
        self,
        *,
        system_prompt: str,
        history: Sequence[ChatMessage],
        prompt: str,
    ) -> str:
        messages = [
            ChatMessage(role="system", content=system_prompt),
            *history,
            ChatMessage(role="user", content=prompt),
        ]
        payload = {
            "model": self._model,
            "messages": [message.as_payload() for message in messages],
            "thinking": {"type": "disabled"},
            "max_tokens": self._max_output_tokens,
            "stream": False,
        }
        response_payload = await self._post(payload)
        return to_qq_plain_text(extract_response_text(response_payload))

    async def complete_with_tools(
        self,
        *,
        system_prompt: str,
        history: Sequence[ChatMessage],
        prompt: str,
        tools: Sequence[Mapping[str, object]],
        execute_tool: ToolExecutor,
        max_rounds: int = 3,
        tool_choice: str | Mapping[str, object] = "auto",
        direct_result_tools: Set[str] = frozenset(),
    ) -> str:
        messages: list[dict[str, object]] = [
            ChatMessage(role="system", content=system_prompt).as_payload(),
            *(message.as_payload() for message in history),
            ChatMessage(role="user", content=prompt).as_payload(),
        ]
        for _ in range(max_rounds):
            payload = {
                "model": self._model,
                "messages": messages,
                "tools": list(tools),
                "tool_choice": tool_choice,
                "thinking": {"type": "disabled"},
                "max_tokens": self._max_output_tokens,
                "stream": False,
            }
            response_payload = await self._post(payload)
            message = extract_response_message(response_payload)
            tool_calls = extract_tool_calls(message)
            if not tool_calls:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("DeepSeek response content is empty")
                return to_qq_plain_text(content)

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": message.get("tool_calls"),
                }
            )
            direct_results: list[str] = []
            for tool_call in tool_calls:
                try:
                    result = await execute_tool(tool_call.name, tool_call.arguments)
                except Exception as error:
                    result = f"工具执行失败：{type(error).__name__}"
                if tool_call.name in direct_result_tools:
                    direct_results.append(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "content": result[:10000],
                    }
                )
            if len(tool_calls) == 1 and direct_results:
                return direct_results[0].strip()
            tool_choice = "auto"
        raise ValueError("DeepSeek exceeded the maximum tool-call rounds")

    async def _post(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with (
            self._semaphore,
            httpx.AsyncClient(timeout=self._timeout) as client,
        ):
            response = await client.post(self._url, headers=headers, json=dict(payload))
            response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise ValueError("DeepSeek response is invalid")
        return data


def extract_response_text(payload: Mapping[str, object]) -> str:
    message = extract_response_message(payload)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek response content is empty")
    return content.strip()


def extract_response_message(payload: Mapping[str, object]) -> Mapping[str, object]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("DeepSeek response does not contain choices")

    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("DeepSeek response choice is invalid")

    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("DeepSeek response does not contain a message")
    return message


def extract_tool_calls(message: Mapping[str, object]) -> list[ToolCall]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    calls: list[ToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            continue
        call_id = raw_call.get("id")
        function = raw_call.get("function")
        if not isinstance(call_id, str) or not isinstance(function, Mapping):
            continue
        name = function.get("name")
        raw_arguments = function.get("arguments")
        if not isinstance(name, str) or not isinstance(raw_arguments, str):
            continue
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, Mapping):
            arguments = {}
        calls.append(
            ToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
                raw_arguments=raw_arguments,
            )
        )
    return calls
