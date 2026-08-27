from __future__ import annotations

import httpx
from nonebot import get_plugin_config, logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from qqbot.chat.config import Config
from qqbot.integrations.web import (
    fetch_bilibili_video,
    find_bilibili_reference,
)

__plugin_meta__ = PluginMetadata(
    name="群聊工具",
    description="B 站链接自动解析",
    usage="发送 B 站链接或 BV 号",
    type="application",
    homepage=None,
    supported_adapters={"~onebot.v11"},
)


plugin_config = get_plugin_config(Config)


async def allowed_group(event: GroupMessageEvent) -> bool:
    return event.group_id in plugin_config.allowed_groups


async def has_bilibili_reference(event: GroupMessageEvent) -> bool:
    return (
        plugin_config.bilibili_auto_parse_enabled
        and await allowed_group(event)
        and find_bilibili_reference(event.get_plaintext()) is not None
    )


bilibili = on_message(rule=Rule(has_bilibili_reference), priority=8, block=False)


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
