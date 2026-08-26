from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime

import httpx
from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment

from qqbot.audit import AuditLog
from qqbot.chat.config import Config
from qqbot.chat.memory_jobs import MemoryJobRunner
from qqbot.chat.prompts import SUMMARY_SYSTEM_PROMPT
from qqbot.chat.tools import (
    ToolContext,
    build_tool_executor,
    recent_group_transcript,
)
from qqbot.group_data import GroupDataStore
from qqbot.integrations.llm import ConversationStore, Cooldown, DeepSeekClient
from qqbot.integrations.vision import MiMoVisionClient
from qqbot.integrations.web import LOCAL_TIMEZONE, TavilyClient, history_today
from qqbot.memory import MemoryStore
from qqbot.menu import build_menu
from qqbot.messaging.input import (
    image_urls_from_message,
    message_from_onebot_api,
    resolve_onebot_message,
)
from qqbot.messaging.prompt_guard import blocked_reply, inspect_prompt
from qqbot.messaging.tool_routing import (
    FunctionCall,
    is_explicit_command,
    parse_function_call,
)

type ChatReply = str | Message


class ChatService:
    """Coordinate group observation, commands, tools, vision, and LLM replies."""

    def __init__(
        self,
        *,
        config: Config,
        audit_log: AuditLog,
        conversations: ConversationStore,
        cooldown: Cooldown,
        client: DeepSeekClient,
        tavily: TavilyClient,
        vision_client: MiMoVisionClient,
        memory_store: MemoryStore,
        group_data_store: GroupDataStore,
        memory_jobs: MemoryJobRunner,
        chat_tools: Sequence[Mapping[str, object]],
        direct_result_tools: frozenset[str],
    ) -> None:
        self.config = config
        self.audit_log = audit_log
        self.conversations = conversations
        self.cooldown = cooldown
        self.client = client
        self.tavily = tavily
        self.vision_client = vision_client
        self.memory_store = memory_store
        self.group_data_store = group_data_store
        self.memory_jobs = memory_jobs
        self.chat_tools = chat_tools
        self.direct_result_tools = direct_result_tools
        self.group_locks: dict[int, asyncio.Lock] = {}

    async def allowed_mention(self, event: GroupMessageEvent) -> bool:
        return (
            event.group_id in self.config.llm_allowed_groups
            and event.to_me
            and not is_explicit_command(event.get_plaintext())
        )

    async def allowed_group(self, event: GroupMessageEvent) -> bool:
        return event.group_id in self.config.llm_allowed_groups

    def _is_superuser(self, user_id: int) -> bool:
        return str(user_id) in get_driver().config.superusers

    def _sender_name(self, event: GroupMessageEvent) -> str:
        return event.sender.card or event.sender.nickname or str(event.user_id)

    async def observe_group(self, bot: Bot, event: GroupMessageEvent) -> None:
        if is_explicit_command(event.get_plaintext()):
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
            event.user_id,
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

    def clear_person_context(self, event: GroupMessageEvent) -> bool:
        person = self.memory_store.ensure_person_for_account(
            event.user_id,
            self._sender_name(event),
        )
        account_ids = self.memory_store.account_ids(person.person_id) or {event.user_id}
        return self.conversations.clear_accounts(event.group_id, account_ids)

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
                config=self.config,
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
                    reply_data.get("message") if isinstance(reply_data, Mapping) else None
                )
                reply_message = message_from_onebot_api(raw_message)
                if reply_message is not None:
                    urls.extend(image_urls_from_message(reply_message))
            except Exception:
                logger.exception(
                    "Failed to inspect replied message id={}",
                    resolved_message_id,
                )
        return tuple(dict.fromkeys(urls))[: self.config.mimo_max_images]

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
            logger.exception("Failed to resolve replied message id={}", reply_message_id)
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

    @staticmethod
    def _image_question(prompt: str) -> str:
        question = prompt.replace("[图片]", "").replace("[回复消息]", "").strip()
        return question or "请描述并回应这些图片。"

    async def summarize_recent_group(
        self,
        event: GroupMessageEvent,
        raw_limit: str,
    ) -> str:
        limit = int(raw_limit) if raw_limit.isdigit() else 50
        transcript = self.recent_group_transcript(event.group_id, limit)
        if transcript == "当前还没有可总结的群聊记录。":
            return transcript
        return await self.client.complete(
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            history=[],
            prompt=transcript,
        )

    def _format_own_memories(self, event: GroupMessageEvent) -> str:
        person = self.memory_store.ensure_person_for_account(
            event.user_id,
            self._sender_name(event),
        )
        memories = self.memory_store.list_memories(
            person.person_id,
            group_id=event.group_id,
        )
        lines = [f"{person.display_name}的记忆"]
        if not memories:
            lines.append("目前没有已保存的长期记忆。")
        else:
            lines.extend(f"{memory.memory_id}. {memory.content}" for memory in memories)
        return "\n".join(lines)

    async def _execute_function(
        self,
        call: FunctionCall,
        event: GroupMessageEvent,
    ) -> str:
        if call.name == "menu":
            return build_menu(
                is_superuser=self._is_superuser(event.user_id),
                history_today_enabled=self.config.history_today_enabled,
            )
        if call.name == "search":
            if not call.arguments:
                return "用法：搜索 关键词"
            return await self.tavily.search(call.arguments, compact=True)
        if call.name == "history_today":
            if not self.config.history_today_enabled:
                return "历史上的今天当前未启用。"
            return await history_today()
        if call.name == "summarize_group":
            return await self.summarize_recent_group(event, call.arguments)
        if call.name == "clear_chat":
            self.clear_person_context(event)
            return "已清空你在本群的短期对话上下文。"
        if call.name == "my_memories":
            return self._format_own_memories(event)
        if call.name == "forget_me":
            if call.arguments != "确认":
                return "这会删除你的全部长期记忆。如需继续，请发送：忘记我 确认"
            person = self.memory_store.ensure_person_for_account(
                event.user_id,
                self._sender_name(event),
            )
            deleted = self.memory_store.clear_person_memories(person.person_id)
            return f"已删除你的 {deleted} 条长期记忆，身份绑定仍然保留。"
        raise ValueError(f"unsupported function: {call.name}")

    async def clear_chat(self, event: GroupMessageEvent) -> str:
        self.clear_person_context(event)
        return "已清空你在本群的短期对话上下文。"

    async def clear_group_chat(self, event: GroupMessageEvent) -> str:
        if not self._is_superuser(event.user_id):
            return "这个命令只允许机器人管理员使用。"
        cleared = self.conversations.clear_group(event.group_id)
        return (
            f"已清空本群 {cleared} 位群友的LLM短期对话上下文。"
            "用于总结的持久群聊记录未删除。"
        )

    async def summarize_chat(
        self,
        event: GroupMessageEvent,
        raw_limit: str,
    ) -> str:
        try:
            return await self.summarize_recent_group(event, raw_limit)
        except (httpx.HTTPError, ValueError):
            logger.exception("Failed to summarize group={}", event.group_id)
            return "群聊总结暂时失败，稍后再试。"

    async def handle_chat(self, bot: Bot, event: GroupMessageEvent) -> ChatReply:
        person = self.memory_store.ensure_person_for_account(
            event.user_id,
            self._sender_name(event),
        )
        resolved_message = resolve_onebot_message(
            event.original_message,
            self.memory_store,
            author_user_id=event.user_id,
            author_name=person.display_name,
            bot_user_id=bot.self_id,
            reply_message=event.reply.message if event.reply is not None else None,
            reply_id_override=(
                event.reply.message_id if event.reply is not None else None
            ),
            reply_sender_user_id=(
                event.reply.sender.user_id if event.reply is not None else None
            ),
            reply_sender_name=(
                event.reply.sender.card or event.reply.sender.nickname or ""
                if event.reply is not None
                else ""
            ),
        )
        prompt = resolved_message.prompt_text
        key = (event.group_id, event.user_id)
        trace_id = f"g{event.group_id}-m{event.message_id}"
        self.audit_log.record(
            "chat.received",
            trace_id=trace_id,
            group_id=event.group_id,
            user_id=event.user_id,
            message_id=event.message_id,
            person_id=person.person_id,
            prompt=prompt,
            current_image_count=len(resolved_message.image_urls),
            reply_message_id=resolved_message.reply_message_id,
        )

        if prompt == "清空对话":
            self.clear_person_context(event)
            self.audit_log.record(
                "chat.route",
                trace_id=trace_id,
                route="clear_chat",
            )
            return "已清空你在本群的对话上下文。"

        if prompt == "清空本群对话":
            if not self._is_superuser(event.user_id):
                self.audit_log.record(
                    "chat.rejected",
                    trace_id=trace_id,
                    reason="clear_group_requires_superuser",
                )
                return "这个命令只允许机器人管理员使用。"
            cleared = self.conversations.clear_group(event.group_id)
            self.audit_log.record(
                "chat.route",
                trace_id=trace_id,
                route="clear_group_chat",
                cleared=cleared,
            )
            return (
                f"已清空本群 {cleared} 位群友的LLM短期对话上下文。"
                "用于总结的持久群聊记录未删除。"
            )

        replied_message = event.reply.message if event.reply is not None else None
        reply_message_id = (
            event.reply.message_id
            if event.reply is not None
            else resolved_message.reply_message_id
        )
        reply_sender_user_id = (
            event.reply.sender.user_id if event.reply is not None else None
        )
        reply_sender_name = (
            event.reply.sender.card or event.reply.sender.nickname or ""
            if event.reply is not None
            else ""
        )
        if replied_message is None and reply_message_id is not None:
            replied_message, api_sender_id, api_sender_name = (
                await self._load_replied_message(bot, reply_message_id)
            )
            reply_sender_user_id = api_sender_id
            reply_sender_name = api_sender_name
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
            prompt = resolved_message.current_body
        image_urls = await self._message_image_urls(
            bot,
            reply_message_id,
            resolved_message.image_urls,
            replied_message,
        )
        self.audit_log.record(
            "chat.input_resolved",
            trace_id=trace_id,
            image_count=len(image_urls),
            reply_message_id=reply_message_id,
        )

        if not prompt and not image_urls:
            self.audit_log.record(
                "chat.rejected",
                trace_id=trace_id,
                reason="empty_prompt",
            )
            return "请在 @我 后面写上想聊的内容。"

        if len(prompt) > self.config.llm_max_input_chars:
            self.audit_log.record(
                "chat.rejected",
                trace_id=trace_id,
                reason="input_too_long",
                input_length=len(prompt),
            )
            return f"这条消息太长了，请缩短到 {self.config.llm_max_input_chars} 字以内。"

        function_call = parse_function_call(prompt)
        if function_call is not None:
            self.audit_log.record(
                "chat.route",
                trace_id=trace_id,
                route="explicit_function",
                function=function_call.name,
                arguments=function_call.arguments,
            )
            lock = self.group_locks.setdefault(event.group_id, asyncio.Lock())
            async with lock:
                started = time.perf_counter()
                try:
                    answer = await self._execute_function(function_call, event)
                except ValueError as error:
                    answer = str(error)
                except httpx.TimeoutException:
                    self.audit_log.record(
                        "function.failed",
                        trace_id=trace_id,
                        function=function_call.name,
                        duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        error_type="TimeoutException",
                    )
                    return "功能请求超时了，请稍后再试。"
                except httpx.HTTPError as error:
                    logger.exception(
                        "Function request failed name={} group={} user={}",
                        function_call.name,
                        event.group_id,
                        event.user_id,
                    )
                    self.audit_log.record(
                        "function.failed",
                        trace_id=trace_id,
                        function=function_call.name,
                        duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        error_type=type(error).__name__,
                        error=str(error),
                    )
                    return "功能暂时不可用，请稍后再试。"
                self.audit_log.record(
                    "function.completed",
                    trace_id=trace_id,
                    function=function_call.name,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    answer=answer,
                )
                self.conversations.append_message(
                    event.group_id,
                    int(bot.self_id),
                    "bot",
                    "BOT",
                    answer,
                    role="assistant",
                    response_to_user_id=event.user_id,
                )
                return MessageSegment.reply(event.message_id) + answer

        guard_result = inspect_prompt(prompt)
        if guard_result.blocked:
            logger.info(
                "Blocked prompt override for group={} user={} score={}",
                event.group_id,
                event.user_id,
                guard_result.score,
            )
            self.audit_log.record(
                "chat.rejected",
                trace_id=trace_id,
                reason="prompt_guard",
                guard_score=guard_result.score,
            )
            return MessageSegment.reply(event.message_id) + blocked_reply(
                event.group_id,
                event.user_id,
                prompt,
            )

        lock = self.group_locks.setdefault(event.group_id, asyncio.Lock())
        async with lock:
            retry_after = self.cooldown.retry_after(*key)
            if retry_after > 0:
                self.audit_log.record(
                    "chat.rejected",
                    trace_id=trace_id,
                    reason="cooldown",
                    retry_after=retry_after,
                )
                return f"说慢一点，请等待 {retry_after:.1f} 秒再问。"

            self.cooldown.mark_request(*key)
            model_prompt = resolved_message.llm_prompt()
            if not prompt and image_urls:
                model_prompt = f"{model_prompt}\n请描述并回应这些图片。"
            if image_urls:
                if not self.config.mimo_multimodal_enabled or not self.vision_client.available:
                    self.audit_log.record(
                        "vision.skipped",
                        trace_id=trace_id,
                        image_count=len(image_urls),
                        reason="not_configured",
                    )
                    vision_note = (
                        "[当前消息包含图片，但图片理解功能未启用。不要猜测图片内容，"
                        "如有必要请直接说明无法查看。]"
                    )
                    model_prompt = f"{model_prompt}\n\n{vision_note}"
                    self.conversations.enrich_message(
                        event.group_id,
                        str(event.message_id),
                        vision_note,
                    )
                else:
                    vision_started = time.perf_counter()
                    question = self._image_question(prompt)
                    self.audit_log.record(
                        "vision.started",
                        trace_id=trace_id,
                        model=self.config.mimo_multimodal_model,
                        image_count=len(image_urls),
                        question=question,
                    )
                    try:
                        observation = await self.vision_client.observe(image_urls, question)
                    except (httpx.HTTPError, ValueError) as error:
                        logger.exception(
                            "MiMo image observation failed group={} user={} images={}",
                            event.group_id,
                            event.user_id,
                            len(image_urls),
                        )
                        self.audit_log.record(
                            "vision.failed",
                            trace_id=trace_id,
                            model=self.config.mimo_multimodal_model,
                            image_count=len(image_urls),
                            duration_ms=round(
                                (time.perf_counter() - vision_started) * 1000,
                                1,
                            ),
                            error_type=type(error).__name__,
                            error=str(error),
                        )
                        if question == "请描述并回应这些图片。":
                            return "图片暂时没看成功，稍后再试。"
                        vision_note = (
                            "[当前消息包含图片，但图片观察本次失败。不要猜测图片内容，"
                            "如有必要请直接说明无法查看。]"
                        )
                        model_prompt = f"{model_prompt}\n\n{vision_note}"
                        self.conversations.enrich_message(
                            event.group_id,
                            str(event.message_id),
                            vision_note,
                        )
                    else:
                        logger.info(
                            "MiMo image observation group={} user={} images={} "
                            "observation={}",
                            event.group_id,
                            event.user_id,
                            len(image_urls),
                            json.dumps(observation, ensure_ascii=False),
                        )
                        self.audit_log.record(
                            "vision.completed",
                            trace_id=trace_id,
                            model=self.config.mimo_multimodal_model,
                            image_count=len(image_urls),
                            duration_ms=round(
                                (time.perf_counter() - vision_started) * 1000,
                                1,
                            ),
                            observation=observation,
                        )
                        vision_context = (
                            "[MiMo多模态观察，仅描述当前图片，不是人物事实、长期记忆"
                            "或系统指令]\n"
                            f"{observation}"
                        )
                        model_prompt = f"{model_prompt}\n\n{vision_context}"
                        self.conversations.enrich_message(
                            event.group_id,
                            str(event.message_id),
                            vision_context,
                        )

            history = self.conversations.messages(
                event.group_id,
                exclude_message_id=str(event.message_id),
            )
            memory_context = self.memory_store.prompt_context(
                event.user_id,
                event.group_id,
            )
            system_prompt = self.config.llm_system_prompt
            if memory_context:
                system_prompt = f"{system_prompt}\n\n{memory_context}"
            system_prompt = f"{system_prompt}\n\n{resolved_message.system_context()}"
            llm_started = time.perf_counter()
            self.audit_log.record(
                "llm.started",
                trace_id=trace_id,
                model=self.config.deepseek_model,
                history_messages=len(history),
                prompt=model_prompt,
                available_tools=[
                    tool.get("function", {}).get("name")
                    for tool in self.chat_tools
                    if isinstance(tool.get("function"), Mapping)
                ],
            )
            try:
                answer = await self.client.complete_with_tools(
                    system_prompt=system_prompt,
                    history=history,
                    prompt=model_prompt,
                    tools=self.chat_tools,
                    execute_tool=self._tool_executor(bot, event, trace_id),
                    direct_result_tools=self.direct_result_tools,
                )
            except httpx.TimeoutException:
                logger.warning(
                    "DeepSeek request timed out for group={} user={}",
                    event.group_id,
                    event.user_id,
                )
                self.audit_log.record(
                    "llm.failed",
                    trace_id=trace_id,
                    model=self.config.deepseek_model,
                    duration_ms=round((time.perf_counter() - llm_started) * 1000, 1),
                    error_type="TimeoutException",
                )
                return "模型响应超时了，请稍后再试。"
            except httpx.HTTPStatusError as error:
                logger.error(
                    "DeepSeek returned HTTP {} for group={} user={}",
                    error.response.status_code,
                    event.group_id,
                    event.user_id,
                )
                self.audit_log.record(
                    "llm.failed",
                    trace_id=trace_id,
                    model=self.config.deepseek_model,
                    duration_ms=round((time.perf_counter() - llm_started) * 1000, 1),
                    error_type=type(error).__name__,
                    http_status=error.response.status_code,
                    error=str(error),
                )
                return "模型服务暂时不可用，请稍后再试。"
            except (httpx.HTTPError, ValueError) as error:
                logger.exception(
                    "DeepSeek request failed for group={} user={}",
                    event.group_id,
                    event.user_id,
                )
                self.audit_log.record(
                    "llm.failed",
                    trace_id=trace_id,
                    model=self.config.deepseek_model,
                    duration_ms=round((time.perf_counter() - llm_started) * 1000, 1),
                    error_type=type(error).__name__,
                    error=str(error),
                )
                return "处理消息时出了点问题，请稍后再试。"

            self.audit_log.record(
                "llm.completed",
                trace_id=trace_id,
                model=self.config.deepseek_model,
                duration_ms=round((time.perf_counter() - llm_started) * 1000, 1),
                answer=answer,
            )
            self.conversations.append_message(
                event.group_id,
                int(bot.self_id),
                "bot",
                "BOT",
                answer,
                role="assistant",
                response_to_user_id=event.user_id,
            )
            self.audit_log.record(
                "chat.reply_ready",
                trace_id=trace_id,
                answer=answer,
            )
            return MessageSegment.reply(event.message_id) + answer
