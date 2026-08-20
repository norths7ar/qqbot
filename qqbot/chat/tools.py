from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from qqbot.audit import AuditLog
from qqbot.group_data import GroupDataStore
from qqbot.identity import (
    canonical_speaker_name,
    format_group_roster,
    lookup_group_member,
    match_group_member_person_ids,
)
from qqbot.llm import ToolExecutor
from qqbot.memory import MemoryStore
from qqbot.web_tools import TavilyClient, history_today

if TYPE_CHECKING:
    from qqbot.chat.config import Config


def build_chat_tools(
    history_today_enabled: bool,
) -> tuple[list[dict[str, object]], frozenset[str]]:
    tools: list[dict[str, object]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "查询实时、近期或模型不知道的互联网信息。",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recall_memory",
                "description": (
                    "按需查询当前群的长期人物资料、群聊事件和群梗。"
                    "当回答依赖过去发生的事、某人的偏好或关系时调用；"
                    "不要仅凭BOT历史回复猜测。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "person": {
                            "type": "string",
                            "description": "可选的单个人名或称呼。",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lookup_group_member",
                "description": (
                    "按一个疑似群友称呼查询当前群身份。仅当上下文表明某个词可能指人，"
                    "且身份会影响回答时调用；普通词义不调用。可查询统一名称、别名、"
                    "当前群名片和QQ昵称。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "需要核实的单个人名或称呼，不要传整句话。",
                        }
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_group_members",
                "description": (
                    "读取当前QQ群的实时成员名单和角色，并按照管理员配置的身份映射，"
                    "合并属于同一真人的多个QQ账号。仅用于盘点全群成员；"
                    "查询单个疑似称呼时使用群友称呼查询工具。"
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_recent_group_chat",
                "description": "读取当前群最近聊天，用于用户明确要求总结群聊时。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 10,
                            "maximum": 200,
                        }
                    },
                },
            },
        },
    ]
    if history_today_enabled:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "get_history_today",
                    "description": "查询今天在历史上发生的事件。",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )
    direct_result_tools = frozenset(
        {"get_history_today"} if history_today_enabled else ()
    )
    return tools, direct_result_tools


def recent_group_transcript(
    group_data_store: GroupDataStore,
    memory_store: MemoryStore,
    group_id: int,
    limit: int,
) -> str:
    messages = group_data_store.recent_messages(
        group_id,
        limit=max(10, min(limit, 200)),
    )
    if not messages:
        return "当前还没有可总结的群聊记录。"
    lines = [
        f"{canonical_speaker_name(memory_store, message.user_id, message.user_name)}："
        f"{message.content}"
        for message in messages
    ]
    return "\n".join(lines)[:12000]


@dataclass(frozen=True, slots=True)
class ToolContext:
    bot: Bot
    event: GroupMessageEvent
    trace_id: str
    config: Config
    audit_log: AuditLog
    tavily: TavilyClient
    memory_store: MemoryStore
    recent_group_transcript: Callable[[int, int], str]


def build_tool_executor(context: ToolContext) -> ToolExecutor:
    async def run(name: str, arguments: Mapping[str, object]) -> str:
        if name == "web_search":
            return await context.tavily.search(str(arguments.get("query", "")))
        if name == "get_history_today":
            if not context.config.history_today_enabled:
                return "历史上的今天当前未启用。"
            return await history_today()
        if name == "get_group_members":
            raw_members = await context.bot.get_group_member_list(
                group_id=context.event.group_id
            )
            if not isinstance(raw_members, list):
                return "未能读取当前群成员名单。"
            members = [item for item in raw_members if isinstance(item, Mapping)]
            return format_group_roster(
                context.memory_store,
                members,
                bot_user_id=context.bot.self_id,
            )
        if name == "lookup_group_member":
            raw_members = await context.bot.get_group_member_list(
                group_id=context.event.group_id
            )
            if not isinstance(raw_members, list):
                return "未能读取当前群成员名单。"
            members = [item for item in raw_members if isinstance(item, Mapping)]
            return lookup_group_member(
                context.memory_store,
                members,
                str(arguments.get("query", "")),
                bot_user_id=context.bot.self_id,
            )
        if name == "recall_memory":
            query = str(arguments.get("query", "")).strip()
            if not query:
                return "请提供要回忆的问题。"
            person_query = str(arguments.get("person", "")).strip()
            person_ids: tuple[str, ...] = ()
            if person_query:
                raw_members = await context.bot.get_group_member_list(
                    group_id=context.event.group_id
                )
                if not isinstance(raw_members, list):
                    return "未能读取当前群成员名单，无法确认要查询的人。"
                members = [item for item in raw_members if isinstance(item, Mapping)]
                person_ids = match_group_member_person_ids(
                    context.memory_store,
                    members,
                    person_query,
                    bot_user_id=context.bot.self_id,
                )
                if not person_ids:
                    return lookup_group_member(
                        context.memory_store,
                        members,
                        person_query,
                        bot_user_id=context.bot.self_id,
                    )
            return context.memory_store.search_context(
                context.event.group_id,
                query,
                person_ids=person_ids,
            )
        if name == "get_recent_group_chat":
            try:
                limit = int(arguments.get("limit", 50))
            except (TypeError, ValueError):
                limit = 50
            return context.recent_group_transcript(context.event.group_id, limit)
        return f"未知工具：{name}"

    async def execute(name: str, arguments: Mapping[str, object]) -> str:
        started = time.perf_counter()
        context.audit_log.record(
            "tool.started",
            trace_id=context.trace_id,
            group_id=context.event.group_id,
            user_id=context.event.user_id,
            name=name,
            arguments=arguments,
        )
        try:
            result = await run(name, arguments)
        except Exception as error:
            context.audit_log.record(
                "tool.failed",
                trace_id=context.trace_id,
                group_id=context.event.group_id,
                user_id=context.event.user_id,
                name=name,
                arguments=arguments,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error_type=type(error).__name__,
                error=str(error),
            )
            raise
        context.audit_log.record(
            "tool.completed",
            trace_id=context.trace_id,
            group_id=context.event.group_id,
            user_id=context.event.user_id,
            name=name,
            arguments=arguments,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            result=result,
        )
        return result

    return execute
