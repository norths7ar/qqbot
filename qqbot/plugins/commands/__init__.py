from __future__ import annotations

from nonebot import get_driver, logger, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from qqbot.memory import MemoryEntry, Person
from qqbot.memory_runtime import memory_store
from qqbot.news import strip_unavailable_detail_hint

require("nonebot_plugin_jrrp3")
require("nonebot_plugin_multi_source_daily")

from nonebot_plugin_jrrp3.command import (  # noqa: E402
    alljrrp_handle_func,
    jrrp_handle_func,
    monthjrrp_handle_func,
    weekjrrp_handle_func,
)
from nonebot_plugin_multi_source_daily.api import get_news_source  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="统一命令",
    description="为群聊功能提供简单、稳定的斜杠命令和记忆管理",
    usage="/菜单",
    type="application",
    homepage=None,
    supported_adapters={"~onebot.v11"},
)

ALLOWED_GROUPS = {577015417, 482997153}


async def allowed_group(event: GroupMessageEvent) -> bool:
    return event.group_id in ALLOWED_GROUPS


GROUP_RULE = Rule(allowed_group)
COMMAND_ARGUMENT = CommandArg()

menu = on_command("菜单", aliases={"帮助"}, rule=GROUP_RULE, priority=4, block=True)
fortune = on_command("运势", rule=GROUP_RULE, priority=4, block=True)
news = on_command("新闻", rule=GROUP_RULE, priority=4, block=True)
my_memories = on_command("我的记忆", rule=GROUP_RULE, priority=4, block=True)
forget_me = on_command("忘记我", rule=GROUP_RULE, priority=4, block=True)
remember = on_command("记住", rule=GROUP_RULE, priority=4, block=True)
view_memories = on_command("查看记忆", rule=GROUP_RULE, priority=4, block=True)
delete_memory = on_command("删除记忆", rule=GROUP_RULE, priority=4, block=True)


def _is_superuser(user_id: int) -> bool:
    return str(user_id) in get_driver().config.superusers


def _sender_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


def _format_memories(person: Person, memories: list[MemoryEntry]) -> str:
    lines = [f"{person.display_name}的记忆"]
    if person.aliases:
        lines.append(f"别名：{'、'.join(person.aliases)}")
    if not memories:
        lines.append("目前没有已保存的长期记忆。")
        return "\n".join(lines)

    for memory in memories:
        scope = "通用" if memory.group_id is None else "本群"
        lines.append(f"{memory.memory_id}. [{scope}] {memory.content}")
    return "\n".join(lines)


def _target_qq(args: Message) -> int | None:
    for segment in args:
        if segment.type != "at":
            continue
        qq = str(segment.data.get("qq", ""))
        if qq.isdigit():
            return int(qq)
    return None


async def _target_person(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message,
) -> Person | None:
    target_qq = _target_qq(args)
    if target_qq is None:
        return None
    try:
        member = await bot.get_group_member_info(
            group_id=event.group_id,
            user_id=target_qq,
            no_cache=True,
        )
        display_name = member.get("card") or member.get("nickname") or str(target_qq)
    except Exception:
        logger.warning(
            "Failed to query member info for group={} user={}",
            event.group_id,
            target_qq,
        )
        display_name = str(target_qq)
    return memory_store.ensure_person_for_account(target_qq, str(display_name))


@menu.handle()
async def handle_menu(event: GroupMessageEvent) -> None:
    lines = [
        "可用功能",
        "",
        "/天气 城市",
        "/运势 [今日|本周|本月|平均]",
        "/新闻 [60秒|知乎|微博]",
        "/清空对话",
        "/我的记忆",
        "/忘记我 确认",
        "",
        "其他内容请直接 @我。",
    ]
    if _is_superuser(event.user_id):
        lines.extend(
            [
                "",
                "管理员记忆命令",
                "/记住 @群友 内容",
                "/查看记忆 @群友",
                "/删除记忆 编号",
                "/清空本群对话",
            ]
        )
    await menu.finish("\n".join(lines))


@fortune.handle()
async def handle_fortune(
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    period = args.extract_plain_text().strip()
    handlers = {
        "": jrrp_handle_func,
        "今日": jrrp_handle_func,
        "今天": jrrp_handle_func,
        "本周": weekjrrp_handle_func,
        "周": weekjrrp_handle_func,
        "本月": monthjrrp_handle_func,
        "月": monthjrrp_handle_func,
        "平均": alljrrp_handle_func,
        "历史": alljrrp_handle_func,
    }
    handler = handlers.get(period)
    if handler is None:
        await fortune.finish("用法：/运势 [今日|本周|本月|平均]")
    await fortune.finish(handler(event).strip())


@news.handle()
async def handle_news(args: Message = COMMAND_ARGUMENT) -> None:
    requested = args.extract_plain_text().strip()
    news_types = {
        "": "60秒",
        "60秒": "60秒",
        "60s": "60秒",
        "知乎": "知乎热榜",
        "知乎热榜": "知乎热榜",
        "微博": "微博热搜",
        "微博热搜": "微博热搜",
    }
    news_type = news_types.get(requested)
    if news_type is None:
        await news.finish("用法：/新闻 [60秒|知乎|微博]")

    await news.send(f"正在获取{news_type}，稍等一下。")
    try:
        result = await get_news_source(news_type).fetch(
            format_type="text",
            force_refresh=False,
        )
    except Exception:
        logger.exception("Failed to fetch news type={}", news_type)
        await news.finish("新闻源暂时不可用，稍后再试。")
    result_text = (
        result.extract_plain_text() if isinstance(result, Message) else str(result)
    )
    await news.finish(strip_unavailable_detail_hint(result_text))


@my_memories.handle()
async def handle_my_memories(event: GroupMessageEvent) -> None:
    person = memory_store.ensure_person_for_account(event.user_id, _sender_name(event))
    memories = memory_store.list_memories(
        person.person_id,
        group_id=event.group_id,
    )
    await my_memories.finish(_format_memories(person, memories))


@forget_me.handle()
async def handle_forget_me(
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    if args.extract_plain_text().strip() != "确认":
        await forget_me.finish(
            "这会删除你的全部长期记忆。如需继续，请发送：/忘记我 确认"
        )
    person = memory_store.ensure_person_for_account(event.user_id, _sender_name(event))
    deleted = memory_store.clear_person_memories(person.person_id)
    await forget_me.finish(f"已删除你的 {deleted} 条长期记忆，身份绑定仍然保留。")


@remember.handle()
async def handle_remember(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    if not _is_superuser(event.user_id):
        await remember.finish("这个命令只允许机器人管理员使用。")
    person = await _target_person(bot, event, args)
    if person is None:
        await remember.finish("用法：/记住 @群友 内容")
    content = args.extract_plain_text().strip()
    if not content:
        await remember.finish("请在 @群友 后面写上要记住的内容。")
    try:
        entry = memory_store.add_memory(
            person.person_id,
            content,
            created_by=str(event.user_id),
            group_id=event.group_id,
        )
    except ValueError as error:
        await remember.finish(str(error))
    await remember.finish(f"已为{person.display_name}保存记忆 {entry.memory_id}。")


@view_memories.handle()
async def handle_view_memories(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    if not _is_superuser(event.user_id):
        await view_memories.finish("这个命令只允许机器人管理员使用。")
    person = await _target_person(bot, event, args)
    if person is None:
        await view_memories.finish("用法：/查看记忆 @群友")
    memories = memory_store.list_memories(
        person.person_id,
        group_id=event.group_id,
    )
    await view_memories.finish(_format_memories(person, memories))


@delete_memory.handle()
async def handle_delete_memory(
    event: GroupMessageEvent,
    args: Message = COMMAND_ARGUMENT,
) -> None:
    if not _is_superuser(event.user_id):
        await delete_memory.finish("这个命令只允许机器人管理员使用。")
    raw_id = args.extract_plain_text().strip()
    if not raw_id.isdigit():
        await delete_memory.finish("用法：/删除记忆 编号")
    if not memory_store.delete_memory(int(raw_id)):
        await delete_memory.finish("没有找到这条记忆。")
    await delete_memory.finish(f"已删除记忆 {raw_id}。")
