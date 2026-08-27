from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import httpx
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment

from qqbot.chat.config import Config
from qqbot.chat.memory_jobs import MemoryJobRunner
from qqbot.chat.tools import (
    ToolContext,
    build_tool_executor,
    recent_group_transcript,
)
from qqbot.integrations.llm import (
    ChatClient,
    ChatMessage,
    ConversationStore,
    Cooldown,
    UserContent,
)
from qqbot.integrations.vision import ImageContentLoader
from qqbot.integrations.web import LOCAL_TIMEZONE, TavilyClient
from qqbot.memory import MemoryStore
from qqbot.messaging.input import (
    ResolvedMessage,
    image_urls_from_message,
    message_from_onebot_api,
    resolve_onebot_message,
)
from qqbot.messaging.plain_text import to_qq_plain_text
from qqbot.runtime.audit import AuditLog
from qqbot.storage.group_data import GroupDataStore

type ChatReply = str | Message


@dataclass(frozen=True, slots=True)
class ResolvedChatTurn:
    message: ResolvedMessage
    image_urls: tuple[str, ...]
    trace_id: str


@dataclass(frozen=True, slots=True)
class PreparedModelRequest:
    system_prompt: str
    history: list[ChatMessage]
    content: UserContent
    prompt_text: str


class ChatService:
    """Coordinate group observation, model tools, media, and LLM replies."""

    def __init__(
        self,
        *,
        config: Config,
        audit_log: AuditLog,
        conversations: ConversationStore,
        cooldown: Cooldown,
        client: ChatClient,
        tavily: TavilyClient,
        image_loader: ImageContentLoader,
        memory_store: MemoryStore,
        group_data_store: GroupDataStore,
        memory_jobs: MemoryJobRunner,
        chat_tools: Sequence[Mapping[str, object]],
    ) -> None:
        self.config = config
        self.audit_log = audit_log
        self.conversations = conversations
        self.cooldown = cooldown
        self.client = client
        self.tavily = tavily
        self.image_loader = image_loader
        self.memory_store = memory_store
        self.group_data_store = group_data_store
        self.memory_jobs = memory_jobs
        self.chat_tools = chat_tools
        self.group_locks: dict[int, asyncio.Lock] = {}

    async def allowed_mention(self, event: GroupMessageEvent) -> bool:
        return (
            event.group_id in self.config.allowed_groups
            and event.to_me
            and not self._is_command(event)
        )

    async def allowed_group(self, event: GroupMessageEvent) -> bool:
        return event.group_id in self.config.allowed_groups

    @staticmethod
    def _is_command(event: GroupMessageEvent) -> bool:
        return event.get_plaintext().lstrip().startswith("/")

    def _sender_name(self, event: GroupMessageEvent) -> str:
        return event.sender.card or event.sender.nickname or str(event.user_id)

    async def observe_group(self, bot: Bot, event: GroupMessageEvent) -> None:
        if self._is_command(event):
            return
        person = self.memory_store.ensure_person_for_account(
            event.user_id,
            self._sender_name(event),
        )
        resolved = resolve_onebot_message(
            event.original_message,
            self.memory_store,
            author_user_id=event.user_id,
            author_name=person.display_name,
            bot_user_id=bot.self_id,
        )
        if not resolved.log_text:
            return
        self.conversations.append_message(
            event.group_id,
            person.person_id,
            person.display_name,
            resolved.log_text,
            message_id=str(event.message_id),
            now=float(event.time),
        )
        self.group_data_store.record_message(
            event.group_id,
            event.user_id,
            person.display_name,
            resolved.log_text[:4000],
            person_id=person.person_id,
            sent_at=datetime.fromtimestamp(event.time, tz=LOCAL_TIMEZONE),
        )
        self.memory_jobs.schedule(event.group_id)

    def recent_group_transcript(self, group_id: int, limit: int) -> str:
        return recent_group_transcript(
            self.group_data_store,
            self.memory_store,
            group_id,
            limit,
        )

    def _tool_executor(
        self,
        bot: Bot,
        event: GroupMessageEvent,
        trace_id: str,
    ):
        return build_tool_executor(
            ToolContext(
                bot=bot,
                event=event,
                trace_id=trace_id,
                audit_log=self.audit_log,
                tavily=self.tavily,
                memory_store=self.memory_store,
                recent_group_transcript=self.recent_group_transcript,
            )
        )

    async def _message_image_urls(
        self,
        bot: Bot,
        resolved_message_id: int | None,
        current_urls: tuple[str, ...],
        replied_message: Message | None = None,
    ) -> tuple[str, ...]:
        urls = list(current_urls)
        if replied_message is not None:
            urls.extend(image_urls_from_message(replied_message))
        elif resolved_message_id is not None:
            try:
                reply_data = await bot.get_msg(message_id=resolved_message_id)
                raw_message = (
                    reply_data.get("message")
                    if isinstance(reply_data, Mapping)
                    else None
                )
                reply_message = message_from_onebot_api(raw_message)
                if reply_message is not None:
                    urls.extend(image_urls_from_message(reply_message))
            except Exception:
                logger.exception(
                    "Failed to inspect replied message id={}",
                    resolved_message_id,
                )
        return tuple(dict.fromkeys(urls))[: self.config.media_max_images]

    async def _load_replied_message(
        self,
        bot: Bot,
        reply_message_id: int | None,
    ) -> tuple[Message | None, int | str | None, str]:
        """Load quote data without inventing an author when OneBot cannot resolve it."""
        if reply_message_id is None:
            return None, None, ""
        try:
            reply_data = await bot.get_msg(message_id=reply_message_id)
        except Exception:
            logger.exception(
                "Failed to resolve replied message id={}", reply_message_id
            )
            return None, None, ""
        if not isinstance(reply_data, Mapping):
            return None, None, ""
        reply_message = message_from_onebot_api(reply_data.get("message"))
        sender = reply_data.get("sender")
        if not isinstance(sender, Mapping):
            return reply_message, None, ""
        sender_id = sender.get("user_id")
        sender_name = str(sender.get("card") or sender.get("nickname") or "").strip()
        return reply_message, sender_id, sender_name

    async def _resolve_chat_turn(
        self,
        bot: Bot,
        event: GroupMessageEvent,
    ) -> ResolvedChatTurn:
        person = self.memory_store.ensure_person_for_account(
            event.user_id,
            self._sender_name(event),
        )
        replied_message = event.reply.message if event.reply is not None else None
        reply_message_id = event.reply.message_id if event.reply is not None else None
        reply_sender_user_id = (
            event.reply.sender.user_id if event.reply is not None else None
        )
        reply_sender_name = (
            event.reply.sender.card or event.reply.sender.nickname or ""
            if event.reply is not None
            else ""
        )
        resolved_message = resolve_onebot_message(
            event.original_message,
            self.memory_store,
            author_user_id=event.user_id,
            author_name=person.display_name,
            bot_user_id=bot.self_id,
            reply_message=replied_message,
            reply_id_override=reply_message_id,
            reply_sender_user_id=reply_sender_user_id,
            reply_sender_name=reply_sender_name,
        )
        reply_message_id = reply_message_id or resolved_message.reply_message_id
        if replied_message is None and reply_message_id is not None:
            (
                replied_message,
                reply_sender_user_id,
                reply_sender_name,
            ) = await self._load_replied_message(bot, reply_message_id)
            resolved_message = resolve_onebot_message(
                event.original_message,
                self.memory_store,
                author_user_id=event.user_id,
                author_name=person.display_name,
                bot_user_id=bot.self_id,
                reply_message=replied_message,
                reply_id_override=reply_message_id,
                reply_sender_user_id=reply_sender_user_id,
                reply_sender_name=reply_sender_name,
            )

        image_urls = await self._message_image_urls(
            bot,
            reply_message_id,
            resolved_message.image_urls,
            replied_message,
        )
        trace_id = f"g{event.group_id}-m{event.message_id}"
        self.audit_log.record(
            "chat.received",
            trace_id=trace_id,
            group_id=event.group_id,
            user_id=event.user_id,
            message_id=event.message_id,
            person_id=person.person_id,
            prompt=resolved_message.current_body,
            current_image_count=len(resolved_message.image_urls),
            reply_message_id=reply_message_id,
        )
        self.audit_log.record(
            "chat.input_resolved",
            trace_id=trace_id,
            image_count=len(image_urls),
            reply_message_id=reply_message_id,
        )
        return ResolvedChatTurn(
            message=resolved_message,
            image_urls=image_urls,
            trace_id=trace_id,
        )

    def _record_reply(
        self,
        event: GroupMessageEvent,
        trace_id: str,
        answer: str,
    ) -> ChatReply:
        safe_answer = to_qq_plain_text(answer)
        self.conversations.append_message(
            event.group_id,
            "bot",
            "BOT",
            safe_answer,
            role="assistant",
        )
        self.audit_log.record(
            "chat.reply_ready",
            trace_id=trace_id,
            answer=safe_answer,
        )
        return MessageSegment.reply(event.message_id) + safe_answer

    def _validate_turn(self, turn: ResolvedChatTurn) -> str | None:
        prompt = turn.message.current_body
        if not prompt and not turn.image_urls:
            self.audit_log.record(
                "chat.rejected",
                trace_id=turn.trace_id,
                reason="empty_prompt",
            )
            return "请在 @我 后面写上想聊的内容。"
        if len(prompt) > self.config.llm_max_input_chars:
            self.audit_log.record(
                "chat.rejected",
                trace_id=turn.trace_id,
                reason="input_too_long",
                input_length=len(prompt),
            )
            return (
                f"这条消息太长了，请缩短到 {self.config.llm_max_input_chars} 字以内。"
            )
        return None

    async def _prepare_model_request(
        self,
        event: GroupMessageEvent,
        turn: ResolvedChatTurn,
    ) -> PreparedModelRequest:
        prompt = turn.message.current_body
        model_prompt = turn.message.llm_prompt()
        if not prompt and turn.image_urls:
            model_prompt = f"{model_prompt}\n请描述并回应这些图片。"

        model_content: UserContent = model_prompt
        if turn.image_urls:
            media_started = time.perf_counter()
            try:
                image_parts = await self.image_loader.content_parts(turn.image_urls)
            except (httpx.HTTPError, ValueError) as error:
                self.audit_log.record(
                    "media_input.failed",
                    trace_id=turn.trace_id,
                    image_count=len(turn.image_urls),
                    duration_ms=round(
                        (time.perf_counter() - media_started) * 1000,
                        1,
                    ),
                    error_type=type(error).__name__,
                    error=str(error),
                )
                raise
            model_content = [
                *image_parts,
                {"type": "text", "text": model_prompt},
            ]
            self.audit_log.record(
                "media_input.ready",
                trace_id=turn.trace_id,
                image_count=len(image_parts),
                duration_ms=round((time.perf_counter() - media_started) * 1000, 1),
            )

        history = self.conversations.messages(
            event.group_id,
            exclude_message_id=str(event.message_id),
        )
        memory_context = self.memory_store.prompt_context(event.user_id)
        system_prompt = self.config.llm_system_prompt
        if memory_context:
            system_prompt = f"{system_prompt}\n\n{memory_context}"
        system_prompt = f"{system_prompt}\n\n{turn.message.system_context()}"
        return PreparedModelRequest(
            system_prompt=system_prompt,
            history=history,
            content=model_content,
            prompt_text=model_prompt,
        )

    async def _call_model(
        self,
        bot: Bot,
        event: GroupMessageEvent,
        turn: ResolvedChatTurn,
        request: PreparedModelRequest,
    ) -> str:
        started = time.perf_counter()
        self.audit_log.record(
            "llm.started",
            trace_id=turn.trace_id,
            model=self.config.llm_model,
            history_messages=len(request.history),
            prompt=request.prompt_text,
            available_tools=[
                tool.get("function", {}).get("name")
                for tool in self.chat_tools
                if isinstance(tool.get("function"), Mapping)
            ],
        )
        try:
            answer = await self.client.complete_with_tools(
                system_prompt=request.system_prompt,
                history=request.history,
                prompt=request.content,
                tools=self.chat_tools,
                execute_tool=self._tool_executor(bot, event, turn.trace_id),
            )
        except (httpx.HTTPError, ValueError) as error:
            fields: dict[str, object] = {
                "trace_id": turn.trace_id,
                "model": self.config.llm_model,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "error_type": type(error).__name__,
                "error": str(error),
            }
            if isinstance(error, httpx.HTTPStatusError):
                fields["http_status"] = error.response.status_code
            self.audit_log.record("llm.failed", **fields)
            raise
        self.audit_log.record(
            "llm.completed",
            trace_id=turn.trace_id,
            model=self.config.llm_model,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            answer=answer,
        )
        return answer

    async def _run_model_turn(
        self,
        bot: Bot,
        event: GroupMessageEvent,
        turn: ResolvedChatTurn,
    ) -> ChatReply:
        key = (event.group_id, event.user_id)
        lock = self.group_locks.setdefault(event.group_id, asyncio.Lock())
        async with lock:
            retry_after = self.cooldown.retry_after(*key)
            if retry_after > 0:
                self.audit_log.record(
                    "chat.rejected",
                    trace_id=turn.trace_id,
                    reason="cooldown",
                    retry_after=retry_after,
                )
                return f"说慢一点，请等待 {retry_after:.1f} 秒再问。"

            self.cooldown.mark_request(*key)
            try:
                request = await self._prepare_model_request(event, turn)
            except (httpx.HTTPError, ValueError):
                return "图片暂时没准备好，稍后再试。"

            try:
                answer = await self._call_model(bot, event, turn, request)
            except httpx.TimeoutException:
                logger.warning(
                    "LLM request timed out for group={} user={}",
                    event.group_id,
                    event.user_id,
                )
                return "模型响应超时了，请稍后再试。"
            except httpx.HTTPStatusError as error:
                logger.error(
                    "LLM returned HTTP {} for group={} user={}",
                    error.response.status_code,
                    event.group_id,
                    event.user_id,
                )
                return "模型服务暂时不可用，请稍后再试。"
            except (httpx.HTTPError, ValueError):
                logger.exception(
                    "LLM request failed for group={} user={}",
                    event.group_id,
                    event.user_id,
                )
                return "处理消息时出了点问题，请稍后再试。"
            return self._record_reply(event, turn.trace_id, answer)

    async def handle_chat(self, bot: Bot, event: GroupMessageEvent) -> ChatReply:
        turn = await self._resolve_chat_turn(bot, event)
        if rejection := self._validate_turn(turn):
            return rejection
        return await self._run_model_turn(bot, event, turn)
