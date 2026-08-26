from __future__ import annotations

import httpx
from nonebot import get_driver, get_plugin_config, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from pydantic import BaseModel, SecretStr

from qqbot.group_data_runtime import group_data_store  # noqa: E402
from qqbot.integrations.web import (  # noqa: E402
    TavilyClient,
    fetch_bilibili_video,
    find_bilibili_reference,
    history_today,
)

__plugin_meta__ = PluginMetadata(
    name="群聊工具",
    description="历史上的今天、联网搜索和 B 站解析",
    usage="/菜单",
    type="application",
    homepage=None,
    supported_adapters={"~onebot.v11"},
)


class Config(BaseModel):
    tool_allowed_groups: frozenset[int] = frozenset({482997153})
    tavily_api_key: SecretStr = SecretStr("")
    history_today_enabled: bool = False


plugin_config = get_plugin_config(Config)
tavily = TavilyClient(plugin_config.tavily_api_key.get_secret_value())


async def allowed_group(event: GroupMessageEvent) -> bool:
    return event.group_id in plugin_config.tool_allowed_groups


async def has_bilibili_reference(event: GroupMessageEvent) -> bool:
    return (
        await allowed_group(event)
        and find_bilibili_reference(event.get_plaintext()) is not None
    )


GROUP_RULE = Rule(allowed_group)
COMMAND_ARGUMENT = CommandArg()

bilibili = on_message(rule=Rule(has_bilibili_reference), priority=8, block=False)
clear_group_log = on_command(
    "清空群聊记录",
    rule=GROUP_RULE,
    priority=4,
    block=True,
)
history = on_command("历史上的今天", rule=GROUP_RULE, priority=4, block=True)
search = on_command("搜索", rule=GROUP_RULE, priority=4, block=True)


@bilibili.handle()
async def handle_bilibili(event: GroupMessageEvent) -> None:
    reference = find_bilibili_reference(event.get_plaintext())
    if reference is None:
        return
    try:
        result = await fetch_bilibili_video(reference)
    except (httpx.HTTPError, ValueError):
        logger.exception(
            "Failed to resolve Bilibili reference in group={}",
            event.group_id,
        )
        return
    await bilibili.send(MessageSegment.reply(event.message_id) + result)


@clear_group_log.handle()
async def handle_clear_group_log(
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    if str(event.user_id) not in get_driver().config.superusers:
        await clear_group_log.finish("这个命令只允许机器人管理员使用。")
    if args.extract_plain_text().strip() != "确认":
        await clear_group_log.finish("如需删除，请发送：/清空群聊记录 确认")
    deleted = group_data_store.clear_messages(event.group_id)
    await clear_group_log.finish(
        f"已删除本群 {deleted} 条用于总结的持久群聊记录。"
        "LLM短期对话上下文未清除；如需清除，请使用 /清空本群对话。"
    )


@history.handle()
async def handle_history() -> None:
    if not plugin_config.history_today_enabled:
        await history.finish("历史上的今天当前未启用。")
    try:
        result = await history_today()
    except (httpx.HTTPError, ValueError):
        logger.exception("Failed to fetch history today")
        await history.finish("历史数据源暂时不可用，稍后再试。")
    await history.finish(result)


@search.handle()
async def handle_search(args: Message = COMMAND_ARGUMENT) -> None:
    query = args.extract_plain_text().strip()
    if not query:
        await search.finish("用法：/搜索 关键词")
    try:
        result = await tavily.search(query, compact=True)
    except (httpx.HTTPError, ValueError):
        logger.exception("Tavily search failed")
        await search.finish("联网搜索暂时失败，稍后再试。")
    await search.finish(result)
