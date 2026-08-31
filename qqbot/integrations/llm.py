from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

type Role = Literal["system", "user", "assistant"]
type UserContent = str | list[dict[str, object]]
type ThinkingMode = Literal["provider_default", "disabled"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: str | None
    person_id: str
    user_name: str
    role: Literal["user", "assistant"]
    content: str
    created_at: float

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


@dataclass(frozen=True, slots=True)
class CompletionTrace:
    finish_reason: str | None
    content_length: int
    reasoning_content: str
    usage: Mapping[str, object]


type ResponseObserver = Callable[[CompletionTrace], None]


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
        person_id: str,
        user_name: str,
        content: str,
        *,
        role: Literal["user", "assistant"] = "user",
        message_id: str | None = None,
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
                person_id=person_id,
                user_name=user_name,
                role=role,
                content=normalized,
                created_at=current,
            )
        )

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
        thinking_mode: ThinkingMode = "provider_default",
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_output_tokens = max_output_tokens
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._thinking_mode = thinking_mode

    async def complete(
        self,
        *,
        system_prompt: str,
        history: Sequence[ChatMessage],
        prompt: UserContent,
        max_output_tokens: int | None = None,
        response_observer: ResponseObserver | None = None,
    ) -> str:
        messages = [
            ChatMessage(role="system", content=system_prompt),
            *history,
            {"role": "user", "content": prompt},
        ]
        payload = {
            "model": self._model,
            "messages": [
                message.as_payload() if isinstance(message, ChatMessage) else message
                for message in messages
            ],
            "max_tokens": (
                self._max_output_tokens
                if max_output_tokens is None
                else max_output_tokens
            ),
            "stream": False,
        }
        self._apply_thinking_mode(payload)
        response_payload = await self._post(payload)
        if response_observer is not None:
            response_observer(extract_completion_trace(response_payload))
        return extract_response_text(response_payload)

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
        response_observer: ResponseObserver | None = None,
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
                "max_tokens": self._max_output_tokens,
                "stream": False,
            }
            self._apply_thinking_mode(payload)
            response_payload = await self._post(payload)
            if response_observer is not None:
                response_observer(extract_completion_trace(response_payload))
            message = extract_response_message(response_payload)
            tool_calls = extract_tool_calls(message)
            if not tool_calls:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("LLM response content is empty")
                return content.strip()

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
        raise ValueError("LLM exceeded the maximum tool-call rounds")

    def _apply_thinking_mode(self, payload: dict[str, object]) -> None:
        if self._thinking_mode == "disabled":
            payload["thinking"] = {"type": "disabled"}

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
            raise ValueError("LLM response is invalid")
        return data


def extract_response_text(payload: Mapping[str, object]) -> str:
    message = extract_response_message(payload)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response content is empty")
    return content.strip()


def extract_completion_trace(payload: Mapping[str, object]) -> CompletionTrace:
    message = extract_response_message(payload)
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    finish_reason = choice.get("finish_reason") if isinstance(choice, Mapping) else None
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    usage = payload.get("usage")
    return CompletionTrace(
        finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        content_length=len(content) if isinstance(content, str) else 0,
        reasoning_content=reasoning if isinstance(reasoning, str) else "",
        usage=usage if isinstance(usage, Mapping) else {},
    )


def extract_response_message(payload: Mapping[str, object]) -> Mapping[str, object]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response does not contain choices")

    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("LLM response choice is invalid")

    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("LLM response does not contain a message")
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
