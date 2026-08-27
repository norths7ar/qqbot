from __future__ import annotations

from nonebot import get_driver, get_plugin_config, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from qqbot.chat.config import Config
from qqbot.memory import MemoryEntry, Person
from qqbot.memory.runtime import get_memory_store

__plugin_meta__ = PluginMetadata(
    name="统一命令",
    description="为群聊功能提供简单、稳定的斜杠命令和记忆管理",
    usage="/记住 @群友 内容",
    type="application",
    homepage=None,
    supported_adapters={"~onebot.v11"},
)


plugin_config = get_plugin_config(Config)
memory_store = get_memory_store()


async def allowed_group(event: GroupMessageEvent) -> bool:
    return event.group_id in plugin_config.allowed_groups


GROUP_RULE = Rule(allowed_group)
COMMAND_ARGUMENT = CommandArg()

remember = on_command("记住", rule=GROUP_RULE, priority=4, block=True)
view_memories = on_command("查看记忆", rule=GROUP_RULE, priority=4, block=True)
delete_memory = on_command("删除记忆", rule=GROUP_RULE, priority=4, block=True)


def _is_superuser(user_id: int) -> bool:
    return str(user_id) in get_driver().config.superusers


def _target_qq(args: Message) -> int | None:
    for segment in args:
        if segment.type != "at":
            continue
        qq = str(segment.data.get("qq", ""))
        if qq.isdigit():
            return int(qq)
    return None


def _format_memories(person: Person, memories: list[MemoryEntry]) -> str:
    lines = [f"{person.display_name}的记忆"]
    if not memories:
        lines.append("目前没有已保存的长期记忆。")
        return "\n".join(lines)
    for memory in memories:
        scope = "通用" if memory.group_id is None else "本群"
        lines.append(f"{memory.memory_id}. [{scope}] {memory.content}")
    return "\n".join(lines)


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
