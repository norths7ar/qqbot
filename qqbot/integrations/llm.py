from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from qqbot.messaging.plain_text import to_qq_plain_text

type Role = Literal["system", "user", "assistant"]
type UserContent = str | list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: str | None
    user_id: int
    person_id: str
    user_name: str
    role: Literal["user", "assistant"]
    content: str
    created_at: float
    response_to_user_id: int | None = None

    def as_chat_message(self) -> ChatMessage:
        if self.role == "assistant":
            return ChatMessage(role="assistant", content=self.content)
        speaker = f"{self.user_name}（统一身份：{self.person_id}）"
        return ChatMessage(role="user", content=f"{speaker}：{self.content}")


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]
    raw_arguments: str


type ToolExecutor = Callable[[str, Mapping[str, object]], Awaitable[str]]


class ConversationStore:
    """Keep a bounded, speaker-labelled working context for each group."""

    def __init__(
        self,
        max_turns: int,
        max_assistant_turns: int = 1,
        max_idle_seconds: float | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        if max_assistant_turns < 0:
            raise ValueError("max_assistant_turns cannot be negative")
        if max_idle_seconds is not None and max_idle_seconds <= 0:
            raise ValueError("max_idle_seconds must be positive when provided")
        self._max_turns = max_turns
        self._max_assistant_turns = max_assistant_turns
        self._max_idle_seconds = max_idle_seconds
        self._histories: defaultdict[int, deque[ConversationMessage]] = defaultdict(
            lambda: deque(maxlen=self._max_turns)
        )

    def messages(
        self,
        group_id: int,
        *,
        now: float | None = None,
        exclude_message_id: str | None = None,
    ) -> list[ChatMessage]:
        self._expire_if_stale(group_id, time.time() if now is None else now)
        entries = [
            entry
            for entry in self._histories.get(group_id, ())
            if exclude_message_id is None or entry.message_id != exclude_message_id
        ]
        assistant_indexes = [
            index for index, entry in enumerate(entries) if entry.role == "assistant"
        ]
        kept_assistant_indexes = set(
            assistant_indexes[-self._max_assistant_turns :]
            if self._max_assistant_turns
            else ()
        )
        return [
            entry.as_chat_message()
            for index, entry in enumerate(entries)
            if entry.role == "user" or index in kept_assistant_indexes
        ]

    def append_message(
        self,
        group_id: int,
        user_id: int,
        person_id: str,
        user_name: str,
        content: str,
        *,
        role: Literal["user", "assistant"] = "user",
        message_id: str | None = None,
        response_to_user_id: int | None = None,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else now
        self._expire_if_stale(group_id, current)
        normalized = content.strip()
        if not normalized:
            return
        self._histories[group_id].append(
            ConversationMessage(
                message_id=message_id,
                user_id=user_id,
                person_id=person_id,
                user_name=user_name,
                role=role,
                content=normalized,
                created_at=current,
                response_to_user_id=response_to_user_id,
            )
        )

    def append_turn(
        self,
        group_id: int,
        user_id: int,
        person_id: str,
        user_name: str,
        user_message: str,
        assistant_message: str,
        *,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else now
        self.append_message(
            group_id,
            user_id,
            person_id,
            user_name,
            user_message,
            now=current,
        )
        self.append_message(
            group_id,
            0,
            "bot",
            "BOT",
            assistant_message,
            role="assistant",
            response_to_user_id=user_id,
            now=current,
        )

    def enrich_message(self, group_id: int, message_id: str, addition: str) -> bool:
        normalized = addition.strip()
        if not normalized:
            return False
        history = self._histories.get(group_id)
        if not history:
            return False
        for index, message in enumerate(history):
            if message.message_id != message_id:
                continue
            history[index] = ConversationMessage(
                message_id=message.message_id,
                user_id=message.user_id,
                person_id=message.person_id,
                user_name=message.user_name,
                role=message.role,
                content=f"{message.content}\n{normalized}",
                created_at=message.created_at,
                response_to_user_id=message.response_to_user_id,
            )
            return True
        return False

    def _expire_if_stale(
        self,
        group_id: int,
        now: float,
    ) -> None:
        if self._max_idle_seconds is None:
            return
        history = self._histories.get(group_id)
        if history and now - history[-1].created_at >= self._max_idle_seconds:
            del self._histories[group_id]

    def clear_session(self, group_id: int, user_id: int) -> bool:
        return self.clear_accounts(group_id, {user_id})

    def clear_accounts(self, group_id: int, user_ids: Collection[int]) -> bool:
        changed = False
        history = self._histories.get(group_id)
        if not history:
            return False
        remaining = [
            message
            for message in history
            if message.user_id not in user_ids
            and message.response_to_user_id not in user_ids
        ]
        changed = len(remaining) != len(history)
        if changed and remaining:
            history.clear()
            history.extend(remaining)
        elif changed:
            del self._histories[group_id]
        return changed

    def clear_group(self, group_id: int) -> int:
        if group_id not in self._histories:
            return 0
        del self._histories[group_id]
        return 1


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


class ChatClient:
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
        prompt: UserContent,
    ) -> str:
        messages = [
            ChatMessage(role="system", content=system_prompt),
            *history,
            {"role": "user", "content": prompt},
        ]
        payload = {
            "model": self._model,
            "messages": [
                message.as_payload()
                if isinstance(message, ChatMessage)
                else message
                for message in messages
            ],
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
        prompt: UserContent,
        tools: Sequence[Mapping[str, object]],
        execute_tool: ToolExecutor,
        max_rounds: int = 3,
        tool_choice: str | Mapping[str, object] = "auto",
    ) -> str:
        messages: list[dict[str, object]] = [
            ChatMessage(role="system", content=system_prompt).as_payload(),
            *(message.as_payload() for message in history),
            {"role": "user", "content": prompt},
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
                    raise ValueError("MiMo response content is empty")
                return to_qq_plain_text(content)

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": message.get("tool_calls"),
                }
            )
            for tool_call in tool_calls:
                try:
                    result = await execute_tool(tool_call.name, tool_call.arguments)
                except Exception as error:
                    result = f"工具执行失败：{type(error).__name__}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "content": result[:10000],
                    }
                )
            tool_choice = "auto"
        raise ValueError("DeepSeek exceeded the maximum tool-call rounds")

    async def _post(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        headers = {
            "api-key": self._api_key,
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
