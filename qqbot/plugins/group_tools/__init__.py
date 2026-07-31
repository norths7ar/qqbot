from __future__ import annotations

from datetime import datetime

import httpx
from nonebot import (
    get_bots,
    get_driver,
    get_plugin_config,
    logger,
    on_command,
    on_message,
    require,
)
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from pydantic import BaseModel, Field, SecretStr

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_multi_source_daily")
require("nonebot_plugin_nmcweather")

from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from qqbot.group_data_runtime import group_data_store  # noqa: E402
from qqbot.memory_runtime import memory_store  # noqa: E402
from qqbot.message_input import resolve_onebot_message  # noqa: E402
from qqbot.reminders import LOCAL_TIMEZONE, parse_reminder  # noqa: E402
from qqbot.web_tools import (  # noqa: E402
    TavilyClient,
    fetch_bilibili_video,
    find_bilibili_reference,
    history_today,
)

__plugin_meta__ = PluginMetadata(
    name="群聊工具",
    description="提醒、历史上的今天、新闻详情、联网搜索和 B 站解析",
    usage="/菜单",
    type="application",
    homepage=None,
    supported_adapters={"~onebot.v11"},
)


class Config(BaseModel):
    tool_allowed_groups: frozenset[int] = frozenset({577015417, 482997153})
    tavily_api_key: SecretStr = SecretStr("")
    group_log_max_chars: int = Field(default=4000, ge=100, le=10000)


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

record_messages = on_message(rule=GROUP_RULE, priority=99, block=False)
bilibili = on_message(rule=Rule(has_bilibili_reference), priority=8, block=False)
remind = on_command("提醒", rule=GROUP_RULE, priority=4, block=True)
reminder_list = on_command("提醒列表", rule=GROUP_RULE, priority=4, block=True)
cancel_reminder = on_command("取消提醒", rule=GROUP_RULE, priority=4, block=True)
clear_group_log = on_command(
    "清空群聊记录",
    rule=GROUP_RULE,
    priority=4,
    block=True,
)
history = on_command("历史上的今天", rule=GROUP_RULE, priority=4, block=True)
search = on_command("搜索", rule=GROUP_RULE, priority=4, block=True)


@record_messages.handle()
async def handle_record_message(bot: Bot, event: GroupMessageEvent) -> None:
    sender_name = event.sender.card or event.sender.nickname or str(event.user_id)
    resolved_message = resolve_onebot_message(
        event.original_message,
        memory_store,
        author_user_id=event.user_id,
        author_name=sender_name,
        bot_user_id=bot.self_id,
    )
    group_data_store.record_message(
        event.group_id,
        event.user_id,
        sender_name,
        resolved_message.log_text[: plugin_config.group_log_max_chars],
        sent_at=datetime.fromtimestamp(event.time, tz=LOCAL_TIMEZONE),
    )


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


@remind.handle()
async def handle_remind(
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    try:
        due_at, content = parse_reminder(args.extract_plain_text())
        reminder = group_data_store.create_reminder(
            event.group_id,
            event.user_id,
            event.sender.card or event.sender.nickname or str(event.user_id),
            content,
            due_at,
        )
    except ValueError as error:
        await remind.finish(str(error))
    local_due = datetime.fromisoformat(reminder.due_at).astimezone(LOCAL_TIMEZONE)
    await remind.finish(
        f"提醒 {reminder.reminder_id} 已设置："
        f"{local_due:%m月%d日 %H:%M} 提醒你“{reminder.content}”。"
    )


@reminder_list.handle()
async def handle_reminder_list(event: GroupMessageEvent) -> None:
    reminders = group_data_store.list_reminders(event.group_id, event.user_id)
    if not reminders:
        await reminder_list.finish("你在本群没有待执行的提醒。")
    lines = ["你的待执行提醒："]
    for item in reminders:
        due_at = datetime.fromisoformat(item.due_at).astimezone(LOCAL_TIMEZONE)
        lines.append(f"{item.reminder_id}. {due_at:%m月%d日 %H:%M}　{item.content}")
    await reminder_list.finish("\n".join(lines))


@cancel_reminder.handle()
async def handle_cancel_reminder(
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    raw_id = args.extract_plain_text().strip()
    if not raw_id.isdigit():
        await cancel_reminder.finish("用法：/取消提醒 编号")
    deleted = group_data_store.cancel_reminder(
        int(raw_id),
        event.group_id,
        event.user_id,
    )
    await cancel_reminder.finish("已取消。" if deleted else "没有找到这条待执行提醒。")


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


@scheduler.scheduled_job("interval", seconds=15, id="qqbot_deliver_reminders")
async def deliver_reminders() -> None:
    due = group_data_store.claim_due_reminders()
    if not due:
        return
    bots = list(get_bots().values())
    if not bots:
        for item in due:
            group_data_store.restore_reminder(item.reminder_id)
        return
    bot = bots[0]
    for item in due:
        try:
            await bot.send_group_msg(
                group_id=item.group_id,
                message=MessageSegment.at(item.user_id) + f" 提醒你：{item.content}",
            )
        except Exception:
            group_data_store.restore_reminder(item.reminder_id)
            logger.exception("Failed to deliver reminder id={}", item.reminder_id)
