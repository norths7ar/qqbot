from __future__ import annotations

import asyncio

from nonebot import get_plugin_config, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from qqbot.chat.config import Config
from qqbot.chat.memory_jobs import MemoryJobRunner
from qqbot.chat.service import ChatService
from qqbot.chat.tools import build_chat_tools
from qqbot.integrations.llm import ConversationStore, Cooldown, MiMoClient
from qqbot.integrations.vision import ImageContentLoader
from qqbot.integrations.web import TavilyClient
from qqbot.memory.runtime import memory_store
from qqbot.memory.v2_runtime import claim_store
from qqbot.runtime.audit import AuditLog
from qqbot.runtime.paths import PROJECT_ROOT
from qqbot.storage.runtime import group_data_store

__plugin_meta__ = PluginMetadata(
    name="群聊 LLM",
    description="仅在指定群被 @ 时调用 MiMo 回复",
    usage="@机器人 <问题>",
    type="application",
    homepage=None,
    supported_adapters={"~onebot.v11"},
)


plugin_config = get_plugin_config(Config)
audit_log = AuditLog(
    PROJECT_ROOT / "data" / "logs" / "qqbot-audit.jsonl",
    enabled=plugin_config.audit_log_enabled,
    max_bytes=plugin_config.audit_log_max_bytes,
    backup_count=plugin_config.audit_log_backup_count,
    text_limit=plugin_config.audit_log_text_limit,
)
conversations = ConversationStore(
    plugin_config.llm_context_turns,
    plugin_config.llm_assistant_context_turns,
    max_idle_seconds=(
        plugin_config.llm_context_ttl_hours * 60 * 60
        if plugin_config.llm_context_ttl_hours > 0
        else None
    ),
)
cooldown = Cooldown(plugin_config.llm_cooldown_seconds)
COMMAND_ARGUMENT = CommandArg()
client = MiMoClient(
    api_key=plugin_config.mimo_api_key.get_secret_value(),
    base_url=plugin_config.mimo_base_url,
    model=plugin_config.mimo_model,
    timeout_seconds=plugin_config.llm_timeout_seconds,
    max_output_tokens=plugin_config.llm_max_output_tokens,
    max_concurrency=plugin_config.llm_max_concurrency,
)
tavily = TavilyClient(plugin_config.tavily_api_key.get_secret_value())
image_loader = ImageContentLoader(
    timeout_seconds=plugin_config.mimo_timeout_seconds,
    max_images=plugin_config.mimo_max_images,
    max_image_bytes=plugin_config.mimo_max_image_bytes,
)
audit_log.record(
    "runtime.ready",
    component="llm_chat",
    mimo_model=plugin_config.mimo_model,
    web_search_available=tavily.available,
    memory_v2_schema_version=claim_store.schema_version(),
    memory_v2_shadow_enabled=plugin_config.memory_v2_shadow_enabled,
    memory_v2_shadow_batch_size=plugin_config.memory_v2_shadow_batch_size,
    memory_v2_shadow_backfill_existing=plugin_config.memory_v2_shadow_backfill_existing,
)
memory_jobs = MemoryJobRunner(
    config=plugin_config,
    audit_log=audit_log,
    client=client,
    memory_store=memory_store,
    claim_store=claim_store,
    group_data_store=group_data_store,
)
memory_jobs.initialize_shadow_cursors()
memory_extraction_tasks: dict[int, asyncio.Task[None]] = memory_jobs.tasks
CHAT_TOOLS, DIRECT_RESULT_TOOLS = build_chat_tools(plugin_config)
chat_service = ChatService(
    config=plugin_config,
    audit_log=audit_log,
    conversations=conversations,
    cooldown=cooldown,
    client=client,
    tavily=tavily,
    image_loader=image_loader,
    memory_store=memory_store,
    group_data_store=group_data_store,
    memory_jobs=memory_jobs,
    chat_tools=CHAT_TOOLS,
    direct_result_tools=DIRECT_RESULT_TOOLS,
)


async def allowed_mention(event: GroupMessageEvent) -> bool:
    return await chat_service.allowed_mention(event)


async def allowed_group(event: GroupMessageEvent) -> bool:
    return await chat_service.allowed_group(event)


observe_group = on_message(rule=Rule(allowed_group), priority=1, block=False)
chat = on_message(rule=Rule(allowed_mention), priority=10, block=False)
clear_chat = on_command(
    "清空对话",
    rule=Rule(allowed_group),
    priority=5,
    block=True,
)
clear_group_chat = on_command(
    "清空本群对话",
    rule=Rule(allowed_group),
    priority=5,
    block=True,
)
summarize_chat = on_command(
    "总结",
    rule=Rule(allowed_group),
    priority=5,
    block=True,
)


@observe_group.handle()
async def handle_observe_group(bot: Bot, event: GroupMessageEvent) -> None:
    await chat_service.observe_group(bot, event)


@clear_chat.handle()
async def handle_clear_chat(event: GroupMessageEvent) -> None:
    await clear_chat.finish(await chat_service.clear_chat(event))


@clear_group_chat.handle()
async def handle_clear_group_chat(event: GroupMessageEvent) -> None:
    await clear_group_chat.finish(await chat_service.clear_group_chat(event))


@summarize_chat.handle()
async def handle_summarize_chat(
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    raw_limit = args.extract_plain_text().strip()
    await summarize_chat.finish(await chat_service.summarize_chat(event, raw_limit))


@chat.handle()
async def handle_chat(bot: Bot, event: GroupMessageEvent) -> None:
    await chat.finish(await chat_service.handle_chat(bot, event))
