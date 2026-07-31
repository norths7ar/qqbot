from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

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
    user_name: str
    user_message: str
    assistant_message: str

    def as_messages(self) -> tuple[ChatMessage, ChatMessage]:
        speaker = f"{self.user_name}（QQ {self.user_id}）"
        return (
            ChatMessage(role="user", content=f"{speaker}：{self.user_message}"),
            ChatMessage(role="assistant", content=self.assistant_message),
        )


class ConversationStore:
    """Keep bounded, in-memory conversation histories shared within each group."""

    def __init__(self, max_turns: int) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        self._histories: defaultdict[int, deque[ConversationTurn]] = defaultdict(
            lambda: deque(maxlen=max_turns)
        )

    def messages(self, group_id: int) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for turn in self._histories[group_id]:
            messages.extend(turn.as_messages())
        return messages

    def append_turn(
        self,
        group_id: int,
        user_id: int,
        user_name: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self._histories[group_id].append(
            ConversationTurn(
                user_id=user_id,
                user_name=user_name,
                user_message=user_message,
                assistant_message=assistant_message,
            )
        )

    def clear_session(self, group_id: int, user_id: int) -> bool:
        return self.clear_accounts(group_id, {user_id})

    def clear_accounts(self, group_id: int, user_ids: Collection[int]) -> bool:
        history = self._histories.get(group_id)
        if not history:
            return False

        remaining = [turn for turn in history if turn.user_id not in user_ids]
        changed = len(remaining) != len(history)
        if not changed:
            return False
        if remaining:
            history.clear()
            history.extend(remaining)
        else:
            del self._histories[group_id]
        return True

    def clear_group(self, group_id: int) -> int:
        history = self._histories.pop(group_id, None)
        if history is None:
            return 0
        return len({turn.user_id for turn in history})


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
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with (
            self._semaphore,
            httpx.AsyncClient(timeout=self._timeout) as client,
        ):
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()

        return extract_response_text(response.json())


def extract_response_text(payload: Mapping[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("DeepSeek response does not contain choices")

    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("DeepSeek response choice is invalid")

    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("DeepSeek response does not contain a message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek response content is empty")
    return content.strip()
